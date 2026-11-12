// Independent fixed UI-setting command. No simulation/brush/Rest action entry.
#include "contents_local_bridge.cpp"

namespace {
struct PinchingRow {
    QPointer<QWidget> owner, contents, row;
    QPointer<QLabel> title, label;
    QPointer<QCheckBox> checkbox;
};
PinchingRow pinchingRow(QApplication* app, Budget& budget) {
    PinchingRow result; int owners = 0, roots = 0;
    const auto all = QApplication::allWidgets(); need(all.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    for (QWidget* widget : all) {
        budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
        if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") { result.owner = widget; ++owners; }
        if (className(widget) == "QWidget" && widget->objectName() == "PropertyEditorContents" && widget->isVisible()) {
            result.contents = widget; ++roots;
        }
    }
    need(owners == 1 && result.owner && roots == 1 && result.contents, "CURRENT_CONTENTS_OWNER_NOT_UNIQUE");
    need(result.contents->parent() && className(result.contents->parent()) == "MVDockWidget"
        && result.contents->parent()->objectName() == "Property Editor", "CONTENTS_DOCK_DIFFERS");
    bool owns = false;
    for (QObject* parent = result.contents; parent; parent = parent->parent()) if (parent == result.owner) owns = true;
    need(owns, "CONTENTS_NOT_CURRENT_MAINWINDOW");
    QList<QPointer<QWidget>> queue{result.contents}; int scanned = 0, titles = 0, labels = 0;
    while (!queue.isEmpty()) {
        budget.check(); auto widget = queue.takeFirst(); need(widget, "VISIBLE_ROW_REFERENCE_LOST");
        need(++scanned <= 2048, "VISIBLE_SEMANTIC_ROW_LIMIT");
        if (!widget->isVisible() || !widget->isVisibleTo(result.contents)) continue;
        if (auto* label = qobject_cast<QLabel*>(widget.data())) {
            if (label->text() == "Simulation Properties") { result.title = label; ++titles; }
            if (label->text() == "Pinching Simulation Override") { result.label = label; ++labels; }
        }
        const auto children = widget->children(); need(children.size() <= 1024, "VISIBLE_ROW_CHILD_LIMIT");
        for (QObject* child : children) if (auto* next = qobject_cast<QWidget*>(child)) {
            if (next->isVisible() && next->isVisibleTo(result.contents)) queue.append(next);
        }
    }
    need(titles == 1 && result.title && labels == 1 && result.label, "SIMULATION_OVERRIDE_LABEL_NOT_UNIQUE");
    result.row = qobject_cast<QWidget*>(result.label->parent());
    need(result.row && className(result.row) == "MVPropertyWidget" && result.row->objectName() == "MVCheckbox",
        "OVERRIDE_SEMANTIC_ROW_DIFFERS");
    int buttons = 0;
    for (QObject* child : result.row->children()) if (auto* button = qobject_cast<QAbstractButton*>(child)) {
        if (button->isVisible() && button->isVisibleTo(result.contents)) {
            ++buttons; result.checkbox = qobject_cast<QCheckBox*>(button);
        }
    }
    need(buttons == 1 && result.checkbox && className(result.checkbox) == "CloUICommon::CheckBox"
        && result.checkbox->objectName() == "checkBox" && result.checkbox->isEnabled()
        && result.checkbox->isCheckable() && result.checkbox->thread() == app->thread(), "OVERRIDE_CHECKBOX_NOT_UNIQUE_ENABLED");
    return result;
}
QJsonObject checkboxValue(const PinchingRow& row) {
    need(row.owner && row.contents && row.row && row.title && row.label && row.checkbox,
        "SEMANTIC_ROW_REFERENCE_LOST");
    need(row.label->parent() == row.row && row.checkbox->parent() == row.row
        && row.title->text() == "Simulation Properties" && row.label->text() == "Pinching Simulation Override"
        && row.checkbox->objectName() == "checkBox" && row.checkbox->isVisible()
        && row.checkbox->isVisibleTo(row.contents) && row.checkbox->isEnabled() && row.checkbox->isCheckable(),
        "SEMANTIC_ROW_CHANGED");
    const bool checked = row.checkbox->isChecked(); const auto state = row.checkbox->checkState();
    need(state == (checked ? Qt::Checked : Qt::Unchecked)
        && row.checkbox->text() == (checked ? "On" : "Off"), "CHECKBOX_STATE_TEXT_NOT_BINARY");
    return QJsonObject{{"checked", checked}, {"text", row.checkbox->text()}, {"check_state", int(state)},
        {"enabled", true}, {"visible", true}, {"class_name", className(row.checkbox)},
        {"object_name", row.checkbox->objectName()}, {"row_owner_path", contentsPath(row.row, row.contents)},
        {"label_owner_path", contentsPath(row.label, row.contents)}, {"checkbox_owner_path", contentsPath(row.checkbox, row.contents)}};
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_pinching_override_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl mdqt_set_pinching_override(std::uint32_t pid, std::uint64_t creation,
    int enabled, unsigned budget_ms, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!output || !written || !needed || cap != output_capacity || (enabled != 0 && enabled != 1)
        || budget_ms < 50 || budget_ms > 1500) return -1;
    FILETIME born{}, exited{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        || ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) != creation) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread() || std::strcmp(qVersion(), "6.10.3")) return -3;
    if (busy.test_and_set()) return -4;
    struct Release { ~Release() { busy.clear(); } } release;
    Budget budget(budget_ms); int code = 1; bool clicked = false;
    QJsonObject report{{"schema", "md-qt-set-pinching-override/1"}, {"verified", false}, {"status", "REJECTED"},
        {"requested_enabled", bool(enabled)}, {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)},
        {"effects_started", false}, {"requires_recovery", false}, {"button_click_calls", 0}, {"click_returned", false},
        {"QAction_trigger_calls", 0}, {"event_calls", 0}, {"OS_input_calls", 0}, {"focus_calls", 0},
        {"simulation_calls", 0}, {"Brush_action_calls", 0}, {"UpdateRest_calls", 0}, {"ResetRest_calls", 0},
        {"show_raise_activate_calls", 0}, {"arbitrary_property_set_calls", 0}, {"automatic_retry_allowed", false},
        {"solver_rest_write_verified", false}, {"native_faults_contained", false}, {"direct_UI_setting_only", true}};
    try {
        const auto row = pinchingRow(app, budget);
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(row.owner->effectiveWinId()), GA_ROOT);
        DWORD owner_pid = 0; const auto owner_thread = GetWindowThreadProcessId(hwnd, &owner_pid);
        need(hwnd && IsWindow(hwnd) && owner_pid == pid && owner_thread == GetCurrentThreadId() && IsIconic(hwnd), "MINIMIZED_OWNER_WINDOW_DIFFERS");
        report.insert("main_window_minimized_before", true);
        const auto before_physical = physical(); idle(before_physical, hwnd); report.insert("physical_before", before_physical);
        const auto before = checkboxValue(row); report.insert("before", before); report.insert("original_ui_value", before.value("checked"));
        const bool change = before.value("checked").toBool() != bool(enabled); report.insert("change_required", change);
        budget.check(); report.insert("elapsed_before_click_ms", int(budget.timer.elapsed()));
        if (change) {
            clicked = true; report.insert("button_click_calls", 1); report.insert("effects_started", true); report.insert("requires_recovery", true);
            row.checkbox->click();  // Public QAbstractButton operation, this fixed semantic checkbox only.
            report.insert("click_returned", true);
        }
        report.insert("elapsed_after_click_ms", int(budget.timer.elapsed()));
        const auto after = checkboxValue(row); report.insert("after", after);
        need(after.value("checked").toBool() == bool(enabled), "REQUESTED_BOOLEAN_NOT_READ_BACK");
        need(before.value("row_owner_path") == after.value("row_owner_path")
            && before.value("label_owner_path") == after.value("label_owner_path")
            && before.value("checkbox_owner_path") == after.value("checkbox_owner_path"), "SEMANTIC_ROW_PATH_CHANGED");
        need(IsWindow(hwnd) && IsIconic(hwnd), "MAIN_WINDOW_MINIMIZED_STATE_CHANGED"); report.insert("main_window_minimized_after", true);
        const auto after_physical = physical(); idle(after_physical, hwnd); report.insert("physical_after", after_physical);
        report.insert("physical_state_unchanged", before_physical == after_physical); need(before_physical == after_physical, "SETTING_PHYSICAL_STATE_CHANGED");
        report.insert("semantic_row_binding_verified", true); report.insert("no_effect_observed", !change);
        report.insert("verified", true); report.insert("status", "UI_SETTING_READ_BACK"); report.insert("requires_recovery", false); code = 0;
    } catch (const std::exception& error) {
        report.insert("status", clicked ? "UNKNOWN" : "REJECTED"); report.insert("reason", error.what()); code = clicked ? 2 : 1;
    } catch (...) {
        report.insert("status", clicked ? "UNKNOWN" : "REJECTED"); report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); code = clicked ? 2 : 1;
    }
    report.insert("effects_started", clicked); report.insert("requires_recovery", code == 2); report.insert("return_code", code);
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}
