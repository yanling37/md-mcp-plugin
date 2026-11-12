#pragma once

#include <windows.h>

#include <QtCore/QElapsedTimer>
#include <QtCore/QEvent>
#include <QtCore/QJsonObject>
#include <QtCore/QPointer>
#include <QtCore/QString>
#include <QtCore/QTimer>
#include <QtWidgets/QApplication>
#include <QtWidgets/QWidget>

#include <functional>
#include <utility>

namespace restcold {

struct Sample {
    bool scope = false;
    bool runningKnown = false;
    bool running = false;
    bool controllerKnown = false;
    bool controllerRunning = false;
    bool refreshDirty = false;
    bool updateGate = false;
    bool viewTickEnabled = false;
    bool viewTickSeen = false;
    bool toolsKnown = false;
    bool moveSelected = false;
    bool moveButtonSelected = false;
    bool brushSelected = false;
    bool brushEnabled = false;
    bool advancedEnabled = false;
    QJsonObject vendorUiBinding;
    QString moveActionLabel;
    QString moveButtonLabel;
    int hosts = -1;
    QPointer<QWidget> host;

    bool stoppedMoveSelected() const {
        return runningKnown && !running && moveSelected && !brushSelected;
    }
    bool selectMoveToolbarRestored() const {
        return stoppedMoveSelected() && controllerKnown && !controllerRunning && toolsKnown && moveButtonSelected
            && !brushEnabled && !advancedEnabled
            && moveActionLabel == "Select/Move" && moveButtonLabel == "Select/Move";
    }
    bool nativeRefreshReady() const {
        if (!refreshDirty || !updateGate || viewTickEnabled) return false;
        // An inactive viewport may never have painted the running state.
        return selectMoveToolbarRestored()
            || (stoppedMoveSelected() && controllerKnown && !controllerRunning && toolsKnown && moveButtonSelected
                && brushEnabled && advancedEnabled
                && moveActionLabel == "Quick Pinching" && moveButtonLabel == "Quick Pinching");
    }
};

class ActivationFence final : public QObject {
public:
    using HostPredicate = std::function<bool(QWidget*)>;

    explicit ActivationFence(HostPredicate matches) : matches_(std::move(matches)) {}

    ~ActivationFence() override {
        detach();
        clearCallback();
    }

    bool arm(QApplication* app, HWND root) {
        DWORD pid = 0;
        const DWORD thread = GetWindowThreadProcessId(root, &pid);
        if (active_ || !app || thread != GetCurrentThreadId() || pid != GetCurrentProcessId() || !IsIconic(root)) {
            return false;
        }
        app_ = app;
        thread_ = thread;
        pid_ = pid;
        denied_ = 0;
        human_ = 0;
        quiet_.start();
        hook_ = SetWindowsHookExW(WH_CBT, &ActivationFence::hook, nullptr, thread);
        if (!hook_) {
            app_.clear();
            return false;
        }
        active_ = this;
        app_->installEventFilter(this);
        return true;
    }

    bool detach() noexcept {
        if (app_) app_->removeEventFilter(this);
        app_.clear();
        if (marked_) marked_->setAttribute(Qt::WA_ShowWithoutActivating, previousAttribute_);
        marked_.clear();
        if (!hook_) return true;
        if (!UnhookWindowsHookEx(hook_) && GetLastError() != ERROR_INVALID_HOOK_HANDLE) {
            // Report the failed detach. Any stranded callback only passes through.
            clearCallback();
            return false;
        }
        hook_ = nullptr;
        if (active_ == this) active_ = nullptr;
        return true;
    }

    void clearCallback() noexcept {
        if (active_ == this) active_ = nullptr;
    }

    unsigned denied() const { return denied_; }
    unsigned human() const { return human_; }
    qint64 quietMilliseconds() const { return quiet_.elapsed(); }

protected:
    bool eventFilter(QObject* object, QEvent* event) override {
        if (!marked_ && (event->type() == QEvent::Polish ||
                (event->type() == QEvent::Show && !event->spontaneous()))) {
            auto* widget = qobject_cast<QWidget*>(object);
            if (widget && widget->isWindow() && matches_(widget)) {
                marked_ = widget;
                previousAttribute_ = widget->testAttribute(Qt::WA_ShowWithoutActivating);
                widget->setAttribute(Qt::WA_ShowWithoutActivating);
            }
        }
        return false;
    }

private:
    static bool keyDown(int key) { return (GetAsyncKeyState(key) & 0x8000) != 0; }

    static LRESULT CALLBACK hook(int code, WPARAM target, LPARAM data) noexcept {
        auto* fence = active_;
        if (fence && code == HCBT_ACTIVATE) {
            DWORD pid = 0;
            const DWORD thread = GetWindowThreadProcessId(reinterpret_cast<HWND>(target), &pid);
            if (thread == fence->thread_ && pid == fence->pid_) {
                const auto* activation = reinterpret_cast<const CBTACTIVATESTRUCT*>(data);
                if ((activation && activation->fMouse) || keyDown(VK_MENU) || keyDown(VK_TAB) ||
                    keyDown(VK_LWIN) || keyDown(VK_RWIN)) {
                    ++fence->human_;
                } else {
                    ++fence->denied_;
                    fence->quiet_.restart();
                    return 1;
                }
            }
        }
        return CallNextHookEx(nullptr, code, target, data);
    }

    inline static thread_local ActivationFence* active_ = nullptr;
    HostPredicate matches_;
    QPointer<QApplication> app_;
    QPointer<QWidget> marked_;
    QElapsedTimer quiet_;
    HHOOK hook_ = nullptr;
    DWORD pid_ = 0;
    DWORD thread_ = 0;
    unsigned denied_ = 0;
    unsigned human_ = 0;
    bool previousAttribute_ = false;
};

enum class Result { Idle, Pending, Initialized, Rejected, Unknown };

struct Receipt {
    Result result = Result::Idle;
    const char* reason = "IDLE";
    unsigned startCalls = 0;
    unsigned stopCalls = 0;
    unsigned toolbarRestoreCalls = 0;
    unsigned nativeUiRefreshCalls = 0;
    unsigned activationVetoes = 0;
    unsigned humanActivations = 0;
    qint64 elapsedMilliseconds = 0;
    bool detached = true;
    bool controllerRunningSeenBeforeStop = false;
    Sample before;
    Sample beforeRestore;
    Sample last;
};

class Lifecycle final : public QObject {
public:
    struct Operations {
        std::function<Sample()> observe;
        std::function<void()> start;
        std::function<void()> stop;
        std::function<void(QWidget*)> restoreToolbar;
    };

    Lifecycle(ActivationFence::HostPredicate matches, Operations operations)
        : fence_(std::move(matches)), operations_(std::move(operations)) {
        timer_.setTimerType(Qt::PreciseTimer);
        timer_.setInterval(75);
        QObject::connect(&timer_, &QTimer::timeout, this, [this] { tick(); });
    }

    ~Lifecycle() override {
        timer_.stop();
        fence_.detach();
        fence_.clearCallback();
    }

    void begin(QApplication* app, HWND root) noexcept {
        if (receipt_.result != Result::Idle) return;
        clock_.start();
        try {
            receipt_.before = receipt_.last = operations_.observe();
            const auto& before = receipt_.before;
            if (!before.scope || !before.selectMoveToolbarRestored() || before.hosts != 0) {
                finish(Result::Rejected, "COLD_PREFLIGHT_DIFFERS");
                return;
            }
            if (!fence_.arm(app, root)) {
                finish(Result::Rejected, "ACTIVATION_FENCE_NOT_ARMED");
                return;
            }
            root_ = root;
            receipt_.result = Result::Pending;
            receipt_.detached = false;
            receipt_.reason = "START_DISPATCHED";
            timer_.start();
            Dispatch guard(dispatching_);
            ++receipt_.startCalls;
            operations_.start();
        } catch (...) {
            if (receipt_.startCalls == 0) finish(Result::Rejected, "COLD_PREFLIGHT_FAILED");
            else dispatchFailed_ = true;
        }
    }

    Receipt receipt() const {
        auto copy = receipt_;
        if (copy.result == Result::Pending) {
            copy.elapsedMilliseconds = clock_.elapsed();
            copy.activationVetoes = fence_.denied();
            copy.humanActivations = fence_.human();
        }
        return copy;
    }

private:
    struct Dispatch {
        explicit Dispatch(bool& dispatching) : dispatching_(dispatching) { dispatching_ = true; }
        ~Dispatch() { dispatching_ = false; }
        bool& dispatching_;
    };

    void finish(Result result, const char* reason) noexcept {
        timer_.stop();
        receipt_.activationVetoes = fence_.denied();
        receipt_.humanActivations = fence_.human();
        receipt_.elapsedMilliseconds = clock_.elapsed();
        receipt_.detached = fence_.detach();
        receipt_.result = receipt_.detached ? result : Result::Unknown;
        receipt_.reason = receipt_.detached ? reason : "ACTIVATION_FENCE_DETACH_FAILED";
        if (!receipt_.detached) timer_.start();
    }

    void tick() noexcept {
        if (dispatching_) return;
        if (receipt_.result != Result::Pending) {
            receipt_.detached = fence_.detach();
            if (receipt_.detached || ++detachRetries_ >= 3) timer_.stop();
            return;
        }
        if (fence_.human()) {
            finish(Result::Unknown, "USER_ACTIVATION_OBSERVED");
            return;
        }
        if (!IsIconic(root_)) {
            receipt_.last = {};
            finish(Result::Unknown, "BACKGROUND_ROOT_RESTORED_OR_GONE");
            return;
        }

        const qint64 elapsed = clock_.elapsed();
        try {
            receipt_.last = operations_.observe();
        } catch (...) {
            receipt_.last = {};
        }
        const auto& now = receipt_.last;
        if (!now.scope) {
            stableSince_ = -1;
            refreshReadySince_ = -1;
            if (elapsed >= deadlineMilliseconds) finish(Result::Unknown, "BACKGROUND_SCOPE_LOST");
            return;
        }
        if (elapsed >= deadlineMilliseconds) {
            const bool toolbarUnrestored = receipt_.stopCalls && now.stoppedMoveSelected()
                && (!now.selectMoveToolbarRestored() || now.refreshDirty);
            finish(Result::Unknown, toolbarUnrestored ? "STOPPED_TOOLBAR_NOT_RESTORED" : "COLD_WATCHDOG_EXPIRED");
            return;
        }

        if (receipt_.stopCalls == 0) {
            const bool running = now.runningKnown && now.running;
            const bool controllerRunning = now.controllerKnown && now.controllerRunning;
            if (running && controllerRunning) receipt_.controllerRunningSeenBeforeStop = true;
            const bool hostReady = running && controllerRunning && now.hosts == 1 && now.host;
            if (hostReady && now.host == settledHost_) ++hostSamples_;
            else {
                settledHost_ = hostReady ? now.host.data() : nullptr;
                hostSamples_ = hostReady ? 1 : 0;
            }
            if (running && (hostSamples_ >= 2 || dispatchFailed_ || elapsed >= stopByMilliseconds)) {
                Dispatch guard(dispatching_);
                ++receipt_.stopCalls;
                receipt_.reason = "STOP_DISPATCHED";
                try { operations_.stop(); }
                catch (...) { dispatchFailed_ = true; }
            }
            return;
        }

        const bool hiddenHost = now.hosts == 1 && now.host && now.host == settledHost_ && now.host->isHidden();
        if (hiddenHost && receipt_.controllerRunningSeenBeforeStop && now.nativeRefreshReady()) {
            if (refreshReadySince_ < 0) refreshReadySince_ = elapsed;
            if (!receipt_.toolbarRestoreCalls && elapsed - refreshReadySince_ >= refreshReadyMilliseconds) {
                Dispatch guard(dispatching_);
                ++receipt_.toolbarRestoreCalls;
                receipt_.beforeRestore = now;
                receipt_.reason = "NATIVE_UI_REFRESH_DISPATCHED";
                try { operations_.restoreToolbar(now.host); }
                catch (...) { dispatchFailed_ = true; }
            }
        } else {
            refreshReadySince_ = -1;
        }
        if (!hiddenHost || !receipt_.controllerRunningSeenBeforeStop || !now.selectMoveToolbarRestored() || now.refreshDirty) {
            stableSince_ = -1;
            return;
        }
        if (stableSince_ < 0) stableSince_ = elapsed;
        if (elapsed >= holdThroughMilliseconds && elapsed - stableSince_ >= quietMilliseconds &&
                fence_.quietMilliseconds() >= quietMilliseconds) {
            if (dispatchFailed_) finish(Result::Unknown, "ACTION_DISPATCH_FAILED");
            else finish(Result::Initialized, "HIDDEN_HOST_AND_STOPPED_UI_VERIFIED");
        }
    }

    static constexpr qint64 deadlineMilliseconds = 11000;
    static constexpr qint64 stopByMilliseconds = 3500;
    // The previous passive observer saw late GUI-thread activation attempts at
    // 7.750 s and 7.953 s. The fence has to cover that interval even if UI settles early.
    static constexpr qint64 holdThroughMilliseconds = 9000;
    static constexpr qint64 quietMilliseconds = 800;
    static constexpr qint64 refreshReadyMilliseconds = 300;

    ActivationFence fence_;
    Operations operations_;
    Receipt receipt_;
    QTimer timer_;
    QElapsedTimer clock_;
    QPointer<QWidget> settledHost_;
    HWND root_ = nullptr;
    qint64 stableSince_ = -1;
    qint64 refreshReadySince_ = -1;
    unsigned detachRetries_ = 0;
    unsigned hostSamples_ = 0;
    bool dispatching_ = false;
    bool dispatchFailed_ = false;
};

}  // namespace restcold
