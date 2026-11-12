// Fixed Rest cold-start transaction for the MD GUI thread.
#include "rest_cold_simulation_contract.hpp"
#include "rest_cold_lifecycle.hpp"
#include "rest_cold_native_refresh.hpp"

namespace {

void coldScope(const Sim& sim) {
    DWORD owner = 0, foreground = 0;
    const HWND foregroundWindow = GetForegroundWindow();
    const auto guiThread = GetWindowThreadProcessId(sim.window, &owner);
    if (foregroundWindow) GetWindowThreadProcessId(foregroundWindow, &foreground);
    need(sim.owner && className(sim.owner) == "MDMainWindow" && sim.owner->objectName() == "CLO"
        && GetAncestor(reinterpret_cast<HWND>(sim.owner->effectiveWinId()), GA_ROOT) == sim.window
        && guiThread == GetCurrentThreadId() && owner == GetCurrentProcessId()
        && foreground != owner && IsIconic(sim.window), "BACKGROUND_MAINWINDOW_CHANGED");
    idle(physical(), sim.window);
}

class ColdOperation final : public QObject {
public:
    ColdOperation(QApplication* app, Sim sim, Tools tools)
        : QObject(app), sim_(std::move(sim)), tools_(std::move(tools)), nativeUi_(sim_),
          transaction_([this](QWidget* widget) { return matches(widget); },
            {[this] { return observe(); }, [this] { start(); }, [this] { stop(); },
             [this](QWidget* host) { restoreToolbar(host); }}) {}

    void begin(QApplication* app) { transaction_.begin(app, sim_.window); }
    restcold::Receipt receipt() const {
        auto result = transaction_.receipt();
        result.nativeUiRefreshCalls = nativeUiRefreshCalls_;
        return result;
    }

private:
    bool matches(QWidget* widget) const {
        return sim_.owner && overrideClass(widget) && ownedBy(widget, sim_.owner);
    }

    restcold::Sample observe() const {
        restcold::Sample sample;
        coldScope(sim_);
        sample.scope = true;
        try {
            sample.running = uiRunning(sim_);
            sample.runningKnown = true;
        } catch (const std::exception&) {
            // A transitioning button may disagree with its action for one turn.
        }
        try {
            validateToolGroup(sim_, tools_);
            sample.brushSelected = tools_.brush->isChecked();
            sample.brushEnabled = tools_.brush->isEnabled();
            sample.advancedEnabled = tools_.advanced->isEnabled();
            sample.moveSelected = tools_.group->checkedAction() == tools_.move && tools_.move->isChecked();
            sample.moveActionLabel = bounded(tools_.move->text());
            const auto* button = verifiedMoveButton(sim_, tools_);
            sample.moveButtonLabel = bounded(button->text());
            sample.moveButtonSelected = button->isChecked();
            sample.toolsKnown = true;
        } catch (const std::exception&) {
            // An unknown tool group cannot meet stopped readback.
        }
        try {
            const auto state = nativeUi_.observe(sim_, tools_);
            sample.vendorUiBinding = state.binding.json();
            sample.controllerKnown = state.binding.verified();
            sample.controllerRunning = state.controllerRunning;
            sample.refreshDirty = state.refreshDirty;
            sample.updateGate = state.updateGate;
            sample.viewTickEnabled = state.viewTickEnabled;
            sample.viewTickSeen = state.viewTickSeen;
        } catch (const std::exception&) {
            // A changed native identity cannot authorize a refresh.
        }

        Budget budget(120);
        const auto widgets = QApplication::allWidgets();
        need(widgets.size() <= 25000, "DIALOG_WIDGET_SNAPSHOT_LIMIT");
        sample.hosts = 0;
        for (QWidget* widget : widgets) {
            budget.check();
            if (!overrideClass(widget)) continue;
            ++sample.hosts;
            if (widget->thread() == QThread::currentThread() && matches(widget)) {
                sample.host = widget;
            }
        }
        if (sample.hosts != 1) sample.host.clear();
        return sample;
    }

    void verifyToggle(bool wasRunning) const {
        coldScope(sim_);
        need(uiRunning(sim_) == wasRunning && sim_.action->isEnabled() && sim_.button->isEnabled(),
            "FIXED_SIMULATION_TOGGLE_CHANGED");
    }

    void start() const {
        verifyToggle(false);
        Budget budget(500);
        need(sim_.action->text() == "Simulate", "START_SIMULATE_LABEL_DIFFERS");
        requireOverrideOn(sim_, budget);
        sim_.action->trigger();
    }

    void stop() const {
        verifyToggle(true);
        // The button dispatches its QAction and runs the toolbar's own click handlers.
        sim_.button->click();
    }

    void restoreToolbar(QWidget* host) {
        const auto state = observe();
        need(state.nativeRefreshReady() && state.hosts == 1 && state.host && state.host == host && state.host->isHidden(),
            "STOPPED_NATIVE_UI_REFRESH_SCOPE_CHANGED");
        nativeUi_.refresh(sim_, tools_, nativeUiRefreshCalls_);
    }

    Sim sim_;
    Tools tools_;
    NativeUiRefresh nativeUi_;
    restcold::Lifecycle transaction_;
    unsigned nativeUiRefreshCalls_ = 0;
};

QPointer<ColdOperation> coldOperation;
int coldModuleAnchor = 0;

QApplication* coldContext(std::uint32_t pid, std::uint64_t creation) {
    FILETIME born{}, exited{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        || ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) != creation) return nullptr;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread()
        || std::strcmp(qVersion(), "6.10.3")) return nullptr;
    return app;
}

const char* coldStatus(restcold::Result status) {
    switch (status) {
        case restcold::Result::Idle: return "IDLE";
        case restcold::Result::Pending: return "PENDING";
        case restcold::Result::Initialized: return "INITIALIZED";
        case restcold::Result::Rejected: return "REJECTED";
        case restcold::Result::Unknown: return "UNKNOWN";
    }
    return "UNKNOWN";
}

int coldReport(std::uint32_t pid, std::uint64_t creation, const restcold::Receipt& receipt,
    char* output, unsigned cap, unsigned* written, unsigned* needed) {
    const auto& last = receipt.last;
    const auto& before = receipt.before;
    const auto& beforeRestore = receipt.beforeRestore;
    const bool initialized = receipt.result == restcold::Result::Initialized;
    const auto observed = [](bool known, bool value) {
        return known ? QJsonValue(value) : QJsonValue(QJsonValue::Null);
    };
    const QJsonObject report{
        {"schema", "md-qt-rest-cold/1"}, {"status", coldStatus(receipt.result)}, {"reason", receipt.reason},
        {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)},
        {"start_calls", int(receipt.startCalls)}, {"stop_calls", int(receipt.stopCalls)},
        {"toolbar_restore_calls", int(receipt.toolbarRestoreCalls)},
        {"native_ui_refresh_calls", int(receipt.nativeUiRefreshCalls)},
        {"md_executable_sha256", before.controllerKnown ? QString::fromLatin1(nativeUiImageSha) : QString()},
        {"controller_simulation_mode_before", observed(before.controllerKnown, before.controllerRunning)},
        {"controller_running_seen_before_stop", receipt.controllerRunningSeenBeforeStop},
        {"controller_simulation_mode_after", observed(last.controllerKnown, last.controllerRunning)},
        {"ui_refresh_dirty_before_restore", observed(beforeRestore.controllerKnown, beforeRestore.refreshDirty)},
        {"ui_refresh_dirty_after", observed(last.controllerKnown, last.refreshDirty)},
        {"ui_update_gate_before_restore", observed(beforeRestore.controllerKnown, beforeRestore.updateGate)},
        {"view_tick_enabled_before_restore", observed(beforeRestore.controllerKnown, beforeRestore.viewTickEnabled)},
        {"view_tick_seen_before_restore", observed(beforeRestore.controllerKnown, beforeRestore.viewTickSeen)},
        {"ui_update_gate_after", observed(last.controllerKnown, last.updateGate)},
        {"view_tick_enabled_after", observed(last.controllerKnown, last.viewTickEnabled)},
        {"view_tick_seen_after", observed(last.controllerKnown, last.viewTickSeen)},
        {"brush_disabled_after", observed(last.toolsKnown, !last.brushEnabled)},
        {"advanced_pinching_disabled_after", observed(last.toolsKnown, !last.advancedEnabled)},
        {"vendor_ui_binding", last.vendorUiBinding},
        {"host_count_before", before.hosts}, {"host_count_after", last.hosts},
        {"host_hidden_after", last.host && last.host->isHidden()},
        {"stopped_move_action_after", last.stoppedMoveSelected()},
        {"move_action_label_after", last.moveActionLabel}, {"move_button_label_after", last.moveButtonLabel},
        {"stopped_select_move_after", last.selectMoveToolbarRestored()},
        {"main_window_minimized_after", last.scope},
        {"activation_fence_detached", receipt.detached}, {"guard_elapsed_ms", receipt.elapsedMilliseconds},
        {"programmatic_activation_vetoes", int(receipt.activationVetoes)},
        {"user_activations_observed", int(receipt.humanActivations)},
        {"OS_input_calls", 0}, {"focus_calls", 0}, {"event_pump_calls", 0},
        {"effects_started", receipt.startCalls != 0},
        {"requires_recovery", receipt.result == restcold::Result::Unknown && receipt.startCalls != 0},
        {"verified", initialized}
    };
    const auto json = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = unsigned(json.size());
    if (*needed >= cap) return -3;
    std::memcpy(output, json.constData(), *needed);
    output[*needed] = '\0';
    *written = *needed;
    return 0;
}

int coldCommand(bool begin, std::uint32_t pid, std::uint64_t creation,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!output || !written || !needed || cap != output_capacity) return -1;
    auto* app = coldContext(pid, creation);
    if (!app || busy.test_and_set()) return -2;
    struct Release { ~Release() { busy.clear(); } } release;
    restcold::Receipt receipt;
    try {
        if (begin) {
            if (coldOperation) {
                const auto previous = coldOperation->receipt();
                if (previous.startCalls || !previous.detached) {
                    receipt.result = restcold::Result::Rejected;
                    receipt.reason = "COLD_START_ALREADY_ATTEMPTED_IN_PROCESS";
                    return coldReport(pid, creation, receipt, output, cap, written, needed);
                }
                delete coldOperation.data();
            }
            Budget budget(750);
            const Sim sim = resolveSim(pid, budget);
            coldScope(sim);
            need(!uiRunning(sim), "COLD_START_REQUIRES_STOPPED_UI");
            const Tools tools = resolveTools(sim, budget);
            requireOverrideOn(sim, budget);
            HMODULE module = nullptr;
            need(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                reinterpret_cast<LPCWSTR>(&coldModuleAnchor), &module), "COLD_MODULE_NOT_PINNED");
            coldOperation = new ColdOperation(app, sim, tools);
            coldOperation->begin(app);
        }
        if (coldOperation) receipt = coldOperation->receipt();
        return coldReport(pid, creation, receipt, output, cap, written, needed);
    } catch (const std::exception& error) {
        try {
            if (coldOperation) receipt = coldOperation->receipt();
            receipt.result = receipt.startCalls ? restcold::Result::Unknown : restcold::Result::Rejected;
            receipt.reason = error.what();
            return coldReport(pid, creation, receipt, output, cap, written, needed);
        } catch (...) { return -4; }
    } catch (...) { return -4; }
}

}  // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_cold_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport) int __cdecl mdqt_rest_cold_begin(std::uint32_t pid, std::uint64_t creation,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    return coldCommand(true, pid, creation, output, cap, written, needed);
}

extern "C" __declspec(dllexport) int __cdecl mdqt_rest_cold_poll(std::uint32_t pid, std::uint64_t creation,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    return coldCommand(false, pid, creation, output, cap, written, needed);
}
