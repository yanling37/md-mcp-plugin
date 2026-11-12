// One fixed Qt tool transition and its matching restoration, on the MD GUI thread.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtGui/QWindow>
#include <QtWidgets/QApplication>
#include <QtWidgets/QToolButton>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <optional>
#include <stdexcept>
#include <vector>

namespace {
constexpr auto schema = "md-qt-cloth-meshbox-entry/1";
constexpr std::uint32_t report_capacity = 1024 * 1024;
enum class Command { Enter, Restore };
enum class Mode { Move, Box, Other };

struct Selector { const char* type; const char* name; };
struct ActionSpec { const char* name; const char* text; };
constexpr std::array<Selector, 3> owner_path{{
    {"CloUICommon::Frame", ""}, {"CloUICommon::Frame", ""}, {"MDMainWindow", "CLO"},
}};
constexpr std::array<ActionSpec, 6> actions{{
    {"actionBox_Selecting_Tool", "Select Mesh (Box)"}, {"actionMove_Pattern", "Select/Move"},
    {"action_World_Coordinate_Gizmo", "World Coordinate"}, {"action_Local_Coordinate_Gizmo", "Local Coordinate"},
    {"action_Screen_Coordinate_Gizmo", "Screen Coordinate"}, {"actionScale_Gizmo", "Scale Gizmo"},
}};

void require(bool condition, const char* reason) {
    if (!condition) throw std::runtime_error(reason);
}

class Budget {
public:
    explicit Budget(std::uint32_t ms) : deadline_(GetTickCount64() + ms) {}
    void check() const { require(GetTickCount64() < deadline_, "OBSERVATION_BUDGET_EXCEEDED"); }
private:
    ULONGLONG deadline_;
};

bool process_matches(std::uint32_t pid, std::uint64_t creation) {
    FILETIME start{}, exit{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &start, &exit, &kernel, &user)) return false;
    return ((std::uint64_t{start.dwHighDateTime} << 32) | start.dwLowDateTime) == creation;
}

bool window_matches(HWND window, std::uint32_t pid) {
    DWORD owner = 0;
    const auto thread = GetWindowThreadProcessId(window, &owner);
    return IsWindow(window) && GetAncestor(window, GA_ROOT) == window && owner == pid && thread == GetCurrentThreadId();
}

bool exact_class(const QObject* object, const char* name) {
    return object && std::strcmp(object->metaObject()->className(), name) == 0;
}

using References = std::vector<QPointer<QObject>>;

QObject* unique_child(QObject* owner, Selector selector, QThread* thread, const Budget& budget, References& refs) {
    const auto& children = owner->children();
    require(children.size() <= 5000, "OWNER_CHILD_COUNT_LIMIT");
    QObject* result = nullptr;
    for (QObject* child : children) {
        budget.check();
        if (!exact_class(child, selector.type) || child->objectName() != QLatin1StringView(selector.name)) continue;
        require(!result && child->parent() == owner && child->thread() == thread, "FIXED_OBJECT_NOT_UNIQUE");
        result = child;
    }
    require(result != nullptr, "FIXED_OBJECT_NOT_FOUND");
    refs.emplace_back(result);
    return result;
}

QJsonObject describe(QObject* object) {
    return {{"class_name", QString::fromLatin1(object->metaObject()->className())}, {"object_name", object->objectName().left(256)}};
}

struct Group {
    QAction* checked;
    QList<QAction*> members;
};

Group observe_group(QActionGroup* group, QObject* owner, QThread* thread, const Budget& budget, References& refs) {
    require(group && group->thread() == thread && group->isExclusive(), "ORIGINAL_ACTION_GROUP_NOT_EXCLUSIVE_OR_NOT_CURRENT");
    auto members = group->actions();
    require(!members.isEmpty() && members.size() <= 128, "ACTION_GROUP_COUNT_LIMIT");
    std::sort(members.begin(), members.end(), std::less<QAction*>());
    require(std::adjacent_find(members.cbegin(), members.cend()) == members.cend(), "ACTION_GROUP_MEMBER_DUPLICATE");
    refs.emplace_back(group);
    QAction* checked = nullptr;
    for (QAction* member : members) {
        budget.check();
        require(exact_class(member, "QAction") && member->parent() == owner && member->thread() == thread
                && member->actionGroup() == group, "ACTION_GROUP_OWNER_OR_MEMBER_DIFFERS");
        refs.emplace_back(member);
        if (member->isChecked()) {
            require(!checked && member->isCheckable(), "ACTION_GROUP_CHECKED_MEMBER_AMBIGUOUS");
            checked = member;
        }
    }
    require(checked && group->checkedAction() == checked, "ACTION_GROUP_CHECKED_MEMBER_UNAVAILABLE");
    return {checked, members};
}

QJsonObject action_observation(QAction* action, QThread* thread, const Budget& budget) {
    QJsonArray buttons;
    const auto objects = action->associatedObjects();
    require(objects.size() <= 64, "ACTION_ASSOCIATION_COUNT_LIMIT");
    for (QObject* object : objects) {
        budget.check();
        require(object && object->thread() == thread, "ACTION_ASSOCIATION_THREAD_DIFFERS");
        auto* button = qobject_cast<QToolButton*>(object);
        if (!button) continue;
        auto row = describe(button);
        row.insert("visible", button->isVisible());
        row.insert("checked", button->isChecked());
        row.insert("default_action_matches", button->defaultAction() == action);
        buttons.append(row);
    }
    auto result = describe(action);
    result.insert("text", action->text());
    result.insert("checkable", action->isCheckable());
    result.insert("checked", action->isChecked());
    result.insert("enabled", action->isEnabled());
    result.insert("tool_buttons", buttons);
    return result;
}

struct Scope {
    QPointer<QWidget> root, owner;
    QPointer<QAction> box, move;
    References refs;
    Mode mode = Mode::Other;
    QJsonObject report;

    bool same_objects(const Scope& other) const {
        return refs == other.refs && std::none_of(refs.begin(), refs.end(), [](const auto& ref) { return ref.isNull(); });
    }
};

Scope observe_scope(HWND window, QThread* thread, const Budget& budget) {
    Scope scope;
    scope.root = QWidget::find(reinterpret_cast<WId>(window));
    require(exact_class(scope.root, "TitleFrame") && scope.root->objectName() == "TitleFrame" && scope.root->thread() == thread,
            "TITLEFRAME_IDENTITY_DIFFERS");
    scope.refs.emplace_back(scope.root.data());
    QJsonArray path{describe(scope.root)};
    QObject* owner = scope.root;
    for (auto selector : owner_path) {
        owner = unique_child(owner, selector, thread, budget, scope.refs);
        path.append(describe(owner));
    }
    scope.owner = qobject_cast<QWidget*>(owner);
    require(scope.owner, "MD_MAIN_WINDOW_TYPE_DIFFERS");

    std::array<QAction*, actions.size()> fixed{};
    for (std::size_t i = 0; i < actions.size(); ++i) {
        const auto spec = actions[i];
        auto* action = qobject_cast<QAction*>(unique_child(owner, {"QAction", spec.name}, thread, budget, scope.refs));
        require(action && action->text() == QLatin1StringView(spec.text) && action->isCheckable(), "FIXED_ACTION_CONTRACT_DIFFERS");
        fixed[i] = action;
    }
    scope.box = fixed[0];
    scope.move = fixed[1];
    auto* mode_group = scope.box->actionGroup();
    auto* world_group = fixed[2]->actionGroup();
    require(mode_group && mode_group == scope.move->actionGroup() && mode_group != world_group, "FIXED_ACTION_GROUP_BINDING_DIFFERS");
    const auto mode = observe_group(mode_group, owner, thread, budget, scope.refs);
    const auto world = observe_group(world_group, owner, thread, budget, scope.refs);
    require(mode.members.contains(scope.box) && mode.members.contains(scope.move) && world.members.size() == 4
            && std::all_of(fixed.begin() + 2, fixed.end(), [&](QAction* action) { return world.members.contains(action); }),
            "FIXED_ACTION_GROUP_MEMBERSHIP_DIFFERS");
    require(world.checked == fixed[2] && fixed[2]->isEnabled(), "WORLD_COORDINATE_MODE_NOT_CURRENT");
    scope.mode = mode.checked == scope.move ? Mode::Move : mode.checked == scope.box ? Mode::Box : Mode::Other;
    const bool box_checked = scope.mode == Mode::Box;
    const bool move_checked = scope.mode == Mode::Move;
    scope.report = {{"owner_path", path}, {"mode", box_checked ? "MeshBox" : move_checked ? "MovePattern" : "Other"},
                    {"mode_group_checked_action", mode.checked->objectName().left(256)},
                    {"mode_group_member_count", static_cast<int>(mode.members.size())},
                    {"mode_group_exclusive", true}, {"world_coordinate_checked", true},
                    {"box_action", action_observation(scope.box, thread, budget)},
                    {"move_action", action_observation(scope.move, thread, budget)}};
    return scope;
}

struct Physical {
    HWND foreground = GetForegroundWindow(), focus = GetFocus(), capture = GetCapture();
    QPointer<QWidget> modal = QApplication::activeModalWidget(), popup = QApplication::activePopupWidget();
    QPointer<QWidget> mouse = QWidget::mouseGrabber(), keyboard = QWidget::keyboardGrabber();
    QPointer<QWindow> gui_modal = QGuiApplication::modalWindow();
    QPointer<QWidget> logical_focus = QApplication::focusWidget();
    QRect root_geometry, owner_geometry;
    DWORD foreground_pid = 0;
    bool qt_minimized, os_minimized, inactive;

    Physical(const Scope& scope, HWND window)
        : root_geometry(scope.root->geometry()), owner_geometry(scope.owner->geometry()),
          qt_minimized(scope.root->isMinimized()), os_minimized(IsIconic(window) != FALSE),
          inactive(!scope.root->isActiveWindow()) {
        if (foreground) GetWindowThreadProcessId(foreground, &foreground_pid);
    }

    bool safe(std::uint32_t pid) const {
        return foreground_pid && foreground_pid != pid && qt_minimized && os_minimized && inactive
            && !focus && !capture && !modal && !popup && !mouse && !keyboard && !gui_modal;
    }

    bool same(const Physical& other) const {
        return foreground == other.foreground && foreground_pid == other.foreground_pid
            && root_geometry == other.root_geometry && owner_geometry == other.owner_geometry
            && logical_focus == other.logical_focus
            && qt_minimized == other.qt_minimized && os_minimized == other.os_minimized && inactive == other.inactive;
    }

    QJsonObject report(std::uint32_t pid) const {
        return {{"verified", safe(pid)}, {"background_inactive_minimized", qt_minimized && os_minimized && inactive},
                {"foreign_foreground", foreground_pid && foreground_pid != pid},
                {"thread_focus_absent", focus == nullptr}, {"thread_capture_absent", capture == nullptr},
                {"qt_focus_absent", logical_focus.isNull()}, {"qt_grabbers_absent", !mouse && !keyboard},
                {"modal_and_popup_absent", !modal && !popup && !gui_modal}};
    }
};

struct Lease {
    std::uint32_t pid;
    std::uint64_t creation, ticket;
    HWND window;
    Scope original;
    bool restore_attempted = false;
    bool uncertain = true;
};

std::optional<Lease> active;
std::uint64_t last_closed_ticket = 0;
std::atomic_flag busy = ATOMIC_FLAG_INIT;

int transition(Command command, std::uint32_t pid, std::uint64_t creation, std::size_t root_window,
               std::uint64_t ticket, std::uint32_t budget_ms, char* output, std::uint32_t capacity,
               std::uint32_t* written, std::uint32_t* needed) noexcept {
    if (!written || !needed) return -1;
    *written = *needed = 0;
    if (!output || capacity != report_capacity || !pid || !creation || !root_window || !ticket
        || budget_ms < 50 || budget_ms > 1500) return -1;
    const auto window = reinterpret_cast<HWND>(root_window);
    if (!process_matches(pid, creation) || !window_matches(window, pid)) return -2;
    QPointer<QApplication> app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QThread::currentThread() != app->thread() || std::strcmp(qVersion(), "6.10.3")) return -3;
    if (busy.test_and_set(std::memory_order_acquire)) return -4;
    struct Release { ~Release() { busy.clear(std::memory_order_release); } } release;

    bool dispatched = false, returned = false;
    const bool entering = command == Command::Enter;
    try {
        Budget budget(budget_ms);
        QJsonObject report{{"schema", schema}, {"command", entering ? "enter" : "restore"},
            {"process_id", static_cast<qint64>(pid)}, {"creation_filetime", QString::number(static_cast<qulonglong>(creation))},
            {"root_window", QString::number(static_cast<qulonglong>(root_window))},
            {"ticket", QString::number(static_cast<qulonglong>(ticket))}, {"qt_version", "6.10.3"},
            {"mode_readback_verified", false}, {"original_scope_preserved", false}, {"physical_state_unchanged", false},
            {"global_input_calls", 0}, {"event_dispatch_calls", 0}, {"point_selection_calls", 0}, {"geometry_setter_calls", 0},
            {"focus_change_calls", 0}, {"geometry_preservation_verified", false}, {"native_faults_contained", false}};
        const char* status = "REJECTED";
        int code = 1;
        try {
            if (entering) {
                require(!active, "TRANSACTION_ALREADY_HELD");
                require(ticket != last_closed_ticket, "TICKET_ALREADY_CLOSED");
            } else {
                require(active && active->ticket == ticket && active->pid == pid && active->creation == creation
                        && active->window == window, "RESTORE_TICKET_OR_LIFETIME_DIFFERS");
            }
            const Scope before = observe_scope(window, app->thread(), budget);
            report.insert("scope_before", before.report);
            const Physical physical_before(before, window);
            report.insert("physical_before", physical_before.report(pid));
            require(physical_before.safe(pid), "BACKGROUND_MINIMIZED_SCOPE_UNAVAILABLE");
            if (entering) {
                require(before.mode == Mode::Move && before.box->isEnabled() && before.move->isEnabled(),
                        "ENTRY_REQUIRES_ENABLED_MOVE_PATTERN_AND_BOX_ACTION");
            } else {
                require(active->original.same_objects(before), "ORIGINAL_ACTION_OWNER_OR_GROUP_CHANGED");
                require(before.mode == Mode::Box || before.mode == Mode::Move, "RESTORE_REQUIRES_KNOWN_BOX_OR_MOVE_MODE");
                if (before.mode == Mode::Box) {
                    require(!active->restore_attempted, "RESTORE_ALREADY_DISPATCHED_RECONCILIATION_REQUIRED");
                    require(before.move->isEnabled(), "ORIGINAL_MOVE_ACTION_DISABLED");
                }
            }
            budget.check();
            const Physical immediately_before(before, window);
            require(immediately_before.safe(pid) && physical_before.same(immediately_before), "BACKGROUND_CHANGED_BEFORE_ACTION");

            if (entering || before.mode == Mode::Box) {
                if (entering) active.emplace(Lease{pid, creation, ticket, window, before});
                else active->restore_attempted = true;
                active->uncertain = true;
                dispatched = true;
                QAction* target = entering ? before.box.data() : before.move.data();
                target->trigger();
                returned = true;
            }

            require(app && QCoreApplication::instance() == app && QThread::currentThread() == app->thread()
                    && process_matches(pid, creation) && window_matches(window, pid), "LIFETIME_CHANGED_AFTER_ACTION");
            const auto after = observe_scope(window, app->thread(), budget);
            report.insert("scope_after", after.report);
            const Physical physical_after(after, window);
            report.insert("physical_after", physical_after.report(pid));
            require(physical_after.safe(pid) && physical_before.same(physical_after), "BACKGROUND_CHANGED_AFTER_ACTION");
            require(before.same_objects(after) && active->original.same_objects(after), "ORIGINAL_ACTION_OWNER_OR_GROUP_CHANGED");
            require(after.mode == (entering ? Mode::Box : Mode::Move), "REQUESTED_MODE_NOT_READ_BACK");
            budget.check();
            report.insert("mode_readback_verified", true);
            report.insert("original_scope_preserved", true);
            report.insert("physical_state_unchanged", true);
            active->uncertain = false;
            if (entering) {
                status = "MESHBOX_VERIFIED";
            } else {
                status = dispatched ? "MOVE_RESTORED" : "MOVE_ALREADY_OBSERVED";
                last_closed_ticket = ticket;
                active.reset();
            }
            code = 0;
        } catch (const std::exception& error) {
            report.insert("reason", QString::fromUtf8(error.what()).left(256));
            if (dispatched) {
                status = "UNKNOWN";
                code = 2;
            }
        }
        report.insert("status", status);
        report.insert("return_code", code);
        report.insert("QAction_trigger_calls", dispatched ? 1 : 0);
        report.insert("trigger_returned", returned);
        report.insert("transaction_held", active.has_value());
        report.insert("restore_required", active.has_value());
        report.insert("requires_recovery", active && active->uncertain);
        const QByteArray payload = QJsonDocument(report).toJson(QJsonDocument::Compact);
        if (payload.size() >= report_capacity) return -5;
        *written = static_cast<std::uint32_t>(payload.size());
        *needed = *written + 1;
        std::memcpy(output, payload.constData(), *written);
        output[*written] = '\0';
        return code;
    } catch (...) {
        return -5;
    }
}
} // namespace

extern "C" __declspec(dllexport) std::uint32_t __cdecl mdqt_cloth_meshbox_entry_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport) int __cdecl mdqt_cloth_meshbox_enter(
    std::uint32_t pid, std::uint64_t creation, std::size_t root_window, std::uint64_t ticket,
    std::uint32_t budget_ms, char* output, std::uint32_t capacity, std::uint32_t* written, std::uint32_t* needed) noexcept {
    return transition(Command::Enter, pid, creation, root_window, ticket, budget_ms, output, capacity, written, needed);
}

extern "C" __declspec(dllexport) int __cdecl mdqt_cloth_meshbox_restore(
    std::uint32_t pid, std::uint64_t creation, std::size_t root_window, std::uint64_t ticket,
    std::uint32_t budget_ms, char* output, std::uint32_t capacity, std::uint32_t* written, std::uint32_t* needed) noexcept {
    return transition(Command::Restore, pid, creation, root_window, ticket, budget_ms, output, capacity, written, needed);
}
