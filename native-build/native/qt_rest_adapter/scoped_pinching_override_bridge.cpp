// Temporarily enter the exact Simulation Properties page, toggle one fixed flag and restore the page.
#define MDMCP_PROPERTY_STACK_ONLY
#include "../qt_world_grid_step/bridge.cpp"
#include <QtWidgets/QCheckBox>

namespace {
QPointer<QAction> namedAction(const Scope& scope, const char* name, Budget& budget) {
    return qobject_cast<QAction*>(uniqueChild(scope.owner, "QAction", name, budget, scope.app->thread()));
}

void requireStopped(const Scope& scope, Budget& budget) {
    const auto action = namedAction(scope, "action_SimStop", budget);
    require(action && action->isCheckable() && !action->isChecked(), "SIMULATE_MUST_BE_STOPPED");
}

QPointer<QWidget> simulationPage(const Stack& stack) {
    QPointer<QWidget> page;
    for (const auto& widget : stack.order) {
        if (!exact(widget, "SimulationPropertyWidget", "SimulationProperty")) continue;
        require(!page, "SIMULATION_PROPERTY_PAGE_NOT_UNIQUE");
        page = widget;
    }
    require(page, "SIMULATION_PROPERTY_PAGE_MISSING");
    return page;
}

QPointer<QCheckBox> pinchingFlag(QWidget* page, QThread* gui, Budget& budget) {
    QList<QPointer<QWidget>> queue{page};
    QPointer<QLabel> label;
    for (qsizetype i = 0; i < queue.size(); ++i) {
        budget.check();
        const auto widget = queue.at(i);
        require(widget && widget->thread() == gui && widget->children().size() <= 1024, "INVALID_SIMULATION_PAGE_TREE");
        if (auto* text = qobject_cast<QLabel*>(widget.data()); text && text->text() == "Pinching Simulation Override") {
            require(!label && exact(text, "CloUICommon::Label", "label"), "PINCHING_OVERRIDE_LABEL_NOT_UNIQUE");
            label = text;
        }
        for (QObject* child : widget->children()) {
            if (auto* next = qobject_cast<QWidget*>(child); next && !next->isWindow() && !next->isHidden()) {
                require(queue.size() < 2048, "SIMULATION_PAGE_TREE_LIMIT");
                queue.append(next);
            }
        }
    }
    require(label && label->isVisibleTo(page) && exact(label->parent(), "MVPropertyWidget", "MVCheckbox"),
            "PINCHING_OVERRIDE_ROW_MISSING");
    auto* field = qobject_cast<QCheckBox*>(uniqueChild(label->parent(), "CloUICommon::CheckBox", "checkBox", budget, gui));
    require(field && field->isVisible() && field->isVisibleTo(page) && field->isEnabled() && field->isCheckable(),
            "PINCHING_OVERRIDE_CHECKBOX_NOT_READY");
    return field;
}

bool checkboxState(const QPointer<QCheckBox>& field) {
    require(field && field->isCheckable() && field->isEnabled(), "PINCHING_OVERRIDE_CHECKBOX_CHANGED");
    const bool checked = field->isChecked();
    require(field->checkState() == (checked ? Qt::Checked : Qt::Unchecked)
            && field->text() == (checked ? "On" : "Off"), "PINCHING_OVERRIDE_STATE_NOT_BINARY");
    return checked;
}

void scopedFlag(const Scope& scope, bool enabled, Budget& budget, QJsonObject& report, bool& actionCalled, bool& clicked) {
    const auto before = physical(scope);
    require(backgroundSafe(before) && before.minimized && before.iconic, "MINIMIZED_BACKGROUND_SCOPE_REQUIRED");
    requireStopped(scope, budget);
    const auto pane = propertyPane(scope, budget);
    QJsonObject beforeStack, afterStack;
    const auto original = stackState(scope, pane, budget, &beforeStack);
    report.insert("original_stack", beforeStack);
    const auto page = simulationPage(original);
    QPointer<QCheckBox> field;
    bool changed = false;
    try {
        if (original.current != page) {
            const auto action = namedAction(scope, "actionSet_Simulation_Property", budget);
            require(action && action->isEnabled() && !action->isCheckable() && !action->isChecked()
                    && action->text() == "Simulation Properties", "FIXED_SIMULATION_PROPERTIES_ACTION_NOT_READY");
            physicalStable(scope, before);
            actionCalled = true;
            action->trigger();
        }
        physicalStable(scope, before);
        const auto opened = stackState(scope, pane, budget);
        require(sameStack(original, opened, false) && opened.current == page, "SIMULATION_PROPERTY_PAGE_NOT_OPEN");
        requireStopped(scope, budget);
        field = pinchingFlag(page, scope.app->thread(), budget);
        const bool prior = checkboxState(field);
        report.insert("before_checked", prior);
        if (prior != enabled) {
            physicalStable(scope, before);
            clicked = true;
            field->click();
            require(field == pinchingFlag(page, scope.app->thread(), budget) && checkboxState(field) == enabled,
                    "PINCHING_OVERRIDE_CHANGE_NOT_READ_BACK");
            changed = true;
        }
        report.insert("after_checked", checkboxState(field));
    } catch (const Reject& error) {
        report.insert("setting_error", error.code);
    } catch (...) {
        report.insert("setting_error", "UNEXPECTED_SETTING_EXCEPTION");
    }
    Budget cleanup(500);
    physicalStable(scope, before);
    const auto current = stackState(scope, pane, cleanup);
    require(sameStack(original, current, false), "ORIGINAL_PROPERTY_STACK_CHANGED");
    if (current.current != original.current) {
        require(current.current == page && original.current && original.layout, "UNEXPECTED_PAGE_DURING_RESTORE");
        report.insert("page_restore_calls", 1);
        original.layout->setCurrentWidget(original.current);
    }
    physicalStable(scope, before);
    require(sameStack(original, stackState(scope, pane, cleanup, &afterStack), true), "ORIGINAL_PROPERTY_PAGE_NOT_RESTORED");
    report.insert("restored_stack", afterStack);
    requireStopped(scope, cleanup);
    report.insert("original_page_restored", true);
    report.insert("physical_endpoints_verified", true);
    if (field && report.contains("after_checked")) {
        require(page && page->isAncestorOf(field) && checkboxState(field) == enabled, "FLAG_CHANGED_AFTER_PAGE_RESTORE");
        report.insert("checked_after_page_restore", enabled);
    }
    report.insert("flag_transition_verified", changed);
}
} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_scoped_pinching_override_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport)
int __cdecl mdqt_scoped_pinching_override(std::uint32_t pid, std::uint64_t creation, int enabled, unsigned budget_ms,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!output || !written || !needed || cap < output_capacity || cap > 1048576
        || (enabled != 0 && enabled != 1) || budget_ms < 50 || budget_ms > 1500) return -1;
    bool actionCalled = false, clicked = false;
    QJsonObject report{{"schema", "md-qt-scoped-pinching-override/1"}, {"requested_enabled", bool(enabled)},
        {"process_id", qint64(pid)}, {"process_creation_filetime", QString::number(creation)},
        {"original_page_restored", false}, {"physical_endpoints_verified", false}, {"flag_transition_verified", false},
        {"page_restore_calls", 0}, {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0},
        {"rest_calls", 0}, {"event_pump_calls", 0}};
    try {
        require(processMatches(pid, creation), "PROCESS_IDENTITY_MISMATCH");
        modules();
        require(!running && !recovery_required, "SCOPED_PINCHING_BRIDGE_BUSY_OR_UNRESOLVED");
        running = true;
        struct Release { ~Release() { running = false; } } release;
        Budget budget(budget_ms);
        scopedFlag(currentScope(budget, pid), bool(enabled), budget, report, actionCalled, clicked);
    } catch (const Reject& error) {
        report.insert("reason", error.code);
    } catch (...) {
        report.insert("reason", "UNEXPECTED_SCOPED_PINCHING_EXCEPTION");
    }
    const bool closed = report.value("original_page_restored").toBool() && report.value("physical_endpoints_verified").toBool();
    const bool effect = actionCalled || clicked || report.value("page_restore_calls").toInt() != 0;
    const bool uncertain = (effect && !closed) || (clicked && !report.value("flag_transition_verified").toBool());
    if (uncertain) recovery_required = true;
    const int code = uncertain ? 2 : closed ? 0 : 1;
    report.insert("return_code", code);
    report.insert("status", code == 0 ? "CLOSED" : code == 1 ? "REJECTED" : "UNKNOWN");
    report.insert("effects_started", effect);
    report.insert("requires_recovery", uncertain);
    report.insert("action_trigger_calls", int(actionCalled));
    report.insert("button_click_calls", int(clicked));
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = unsigned(bytes.size()) + 1;
    if (*needed > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size()));
    output[bytes.size()] = 0;
    *written = unsigned(bytes.size());
    return code;
}
