// Sealed-flat diagnostic. Bind the existing stopped/hidden Selected controls and click one fixed button.
#define MDMCP_REST_HOST_CONTROLS
#include "bridge.cpp"

namespace {
constexpr auto hostClass = "Marvelous::DialogOverrideSettings";
constexpr auto hostName = "DialogOverrideSettings";
constexpr auto frameClass = "CloUICommon::Frame";
constexpr auto textButtonClass = "CloUICommon::TextButton";
constexpr auto radioButtonClass = "CloUICommon::RadioButton";
constexpr const char* buttonTexts[] = {"Update Rest Shape", "Reset Rest Shape", "Selected Patterns", "All Patterns"};

bool exact(QObject* object, const char* name, const char* kind) {
    return object && object->objectName() == QLatin1StringView(name) && className(object) == QLatin1StringView(kind);
}

bool restPath(QAbstractButton* button, QWidget* host, const char* kind) {
    if (!exact(button, "", kind)) return false;
    auto* row = button->parent();
    if (!exact(row, "", frameClass)) return false;
    auto* rest = row->parent();
    if (!exact(rest, "subFrameRestShape", frameClass)) return false;
    auto* group = rest->parent();
    return exact(group, "MVGroupFrame", "MVGroupFrame") && group->parent() == host;
}

struct SelectedControls {
    QPointer<QWidget> owner, host;
    QPointer<QAction> simulation;
    QPointer<QAbstractButton> buttons[4];
    HWND window = nullptr;
};

SelectedControls currentControls(QApplication* app, DWORD pid, Budget& budget) {
    SelectedControls result;
    int owners = 0;
    const auto widgets = QApplication::allWidgets();
    need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    for (auto* widget : widgets) {
        budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
        if (exact(widget, "CLO", "MDMainWindow")) { result.owner = widget; ++owners; }
    }
    need(owners == 1 && result.owner, "CURRENT_MAINWINDOW_NOT_UNIQUE");
    int hosts = 0, simulationActions = 0;
    for (auto* child : result.owner->children()) {
        budget.check();
        if (exact(child, hostName, hostClass)) { result.host = qobject_cast<QWidget*>(child); ++hosts; }
        if (child->objectName() == "action_SimStop") { result.simulation = qobject_cast<QAction*>(child); ++simulationActions; }
    }
    need(hosts == 1 && result.host && result.host->isEnabled() && result.host->isHidden()
        && !result.host->isVisible(), "STOPPED_REST_HOST_NOT_UNIQUE_HIDDEN_ENABLED");
    need(simulationActions == 1 && exact(result.simulation, "action_SimStop", "QAction") && result.simulation->isEnabled()
        && result.simulation->text() == "Simulate" && result.simulation->isCheckable()
        && !result.simulation->isChecked(), "STOPPED_SIMULATION_ACTION_NOT_VERIFIED");

    QList<QPointer<QObject>> queue{result.host.data()};
    int counts[4] = {}, scanned = 0;
    while (!queue.isEmpty()) {
        budget.check(); auto child = queue.takeFirst();
        need(child && child->thread() == app->thread() && ++scanned <= 2048, "REST_HOST_SCOPE_LIMIT_OR_CHANGED");
        if (auto* button = qobject_cast<QAbstractButton*>(child.data())) {
            for (int index = 0; index < 4; ++index) {
                if (button->text() == QLatin1StringView(buttonTexts[index])
                    && restPath(button, result.host, index < 2 ? textButtonClass : radioButtonClass)) {
                    result.buttons[index] = button;
                    ++counts[index];
                }
            }
        }
        for (auto* next : child->children()) queue.append(next);
    }
    QPointer<QObject> restRoot;
    for (int index = 0; index < 4; ++index) {
        const auto button = result.buttons[index];
        need(counts[index] == 1 && button && button->isEnabled() && !button->isVisible()
            && button->isCheckable() == (index >= 2) && button->isChecked() == (index == 2)
            && !button->isDown(), "EXACT_REST_BUTTON_OR_SELECTED_SCOPE_DIFFERS");
        auto* frame = button->parent()->parent();
        if (index == 0) restRoot = frame;
        need(frame == restRoot, "REST_BUTTON_SCOPE_OWNERS_DIFFER");
    }
    result.window = GetAncestor(reinterpret_cast<HWND>(result.owner->effectiveWinId()), GA_ROOT);
    DWORD owner_pid = 0;
    const auto owner_tid = GetWindowThreadProcessId(result.window, &owner_pid);
    need(result.window && IsWindow(result.window) && IsIconic(result.window) && owner_pid == pid
        && owner_tid == GetCurrentThreadId(), "MINIMIZED_OWNER_WINDOW_DIFFERS");
    return result;
}

void background(const QJsonObject& snapshot, HWND owner, DWORD pid) {
    idle(snapshot, owner);
    DWORD foreground_pid = 0;
    const HWND foreground = GetForegroundWindow();
    need(foreground && GetWindowThreadProcessId(foreground, &foreground_pid) && foreground_pid != pid,
        "MD_FOREGROUND_OR_NO_FOREGROUND_OWNER");
}

QJsonObject controlSnapshot(const SelectedControls& scope) {
    QJsonArray controls;
    for (const auto& button : scope.buttons) controls.append(standardControl(button, scope.host));
    return {{"owner_path", ancestry(scope.host)}, {"controls", controls}, {"simulation_ui_checked", false},
            {"selected_scope_checked", true}, {"all_scope_checked", false}, {"main_window_minimized", true}};
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_selected_trial_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_selected_trial_click(std::uint32_t pid, std::uint64_t creation,
    int action, unsigned budget_ms, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!output || !written || !needed || cap != output_capacity || (action != 0 && action != 1)
        || budget_ms < 50 || budget_ms > 1500) return -1;
    FILETIME born{}, exited{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        || ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) != creation) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread() || std::strcmp(qVersion(), "6.10.3")) return -3;
    if (busy.test_and_set()) return -4;
    struct Release { ~Release() { busy.clear(); } } release;
    Budget budget(budget_ms);
    int code = 1;
    bool clicked = false;
    QJsonObject report{{"schema", "md-qt-rest-selected-trial/1"}, {"process_id", int(pid)},
        {"process_creation_filetime", QString::number(creation)}, {"action", action == 0 ? "update" : "reset"},
        {"button_click_calls", 0}, {"click_returned", false}, {"dispatch_closed", false},
        {"solver_rest_xyz_verified", false}, {"automatic_retry_allowed", false}, {"physical_endpoints_verified", false},
        {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0}, {"show_raise_activate_calls", 0},
        {"private_slot_invoke_calls", 0}, {"QAction_trigger_calls", 0}, {"property_write_calls", 0}};
    try {
        const auto before = currentControls(app, pid, budget);
        const auto beforeScope = controlSnapshot(before);
        const auto beforePhysical = physical(); background(beforePhysical, before.window, pid);
        report.insert("before", beforeScope); report.insert("physical_before", beforePhysical);
        budget.check();
        clicked = true; report.insert("button_click_calls", 1);
        before.buttons[action]->click();
        report.insert("click_returned", true);
        const auto afterPhysical = physical(); background(afterPhysical, before.window, pid);
        report.insert("physical_after", afterPhysical);
        need(beforePhysical == afterPhysical, "PHYSICAL_ENDPOINT_CHANGED");
        const auto after = currentControls(app, pid, budget);
        const auto afterScope = controlSnapshot(after);
        report.insert("after", afterScope);
        need(before.owner == after.owner && before.host == after.host && before.window == after.window
            && before.simulation == after.simulation && beforeScope == afterScope, "REST_CONTROL_SCOPE_CHANGED");
        for (int index = 0; index < 4; ++index) need(before.buttons[index] == after.buttons[index], "REST_CONTROL_REFERENCE_CHANGED");
        const auto finalPhysical = physical(); background(finalPhysical, after.window, pid);
        need(finalPhysical == beforePhysical, "PHYSICAL_SCOPE_CHANGED_DURING_READBACK");
        report.insert("dispatch_closed", true); report.insert("physical_endpoints_verified", true);
        code = 0;
    } catch (const std::exception& error) {
        report.insert("reason", error.what()); code = clicked ? 2 : 1;
    } catch (...) {
        report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); code = clicked ? 2 : 1;
    }
    report.insert("status", code == 0 ? "DISPATCH_CLOSED" : clicked ? "UNKNOWN" : "REJECTED");
    report.insert("effects_started", clicked); report.insert("requires_recovery", code == 2); report.insert("return_code", code);
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (*needed > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0;
    *written = unsigned(bytes.size()); return code;
}
