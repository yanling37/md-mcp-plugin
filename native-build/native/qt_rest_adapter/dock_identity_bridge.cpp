// Independent fixed dock identity probe: no hidden numeric/property traversal.
#include "bridge.cpp"
#include <QtWidgets/QDockWidget>
#include <QtWidgets/QStackedWidget>

namespace {
bool fixedDockCandidate(QObject* object) {
    const QString name = className(object);
    return object->objectName() == "PropertyEditorContents" || name == "PropertyEditorWidget"
        || name == "Marvelous::PropertyEditorWidget" || name == "SimulationPropertyWidget"
        || name == "Marvelous::SimulationPropertyWidget" || name == "SimulationProperties"
        || name == "Marvelous::SimulationProperties";
}
QJsonObject widgetIdentity(QObject* object, QWidget* owner) {
    QJsonObject result = descriptor(object);
    if (auto* widget = qobject_cast<QWidget*>(object)) {
        result.insert("is_hidden", widget->isHidden()); result.insert("is_visible", widget->isVisible());
        result.insert("is_visible_to_mainwindow", widget->isVisibleTo(owner));
        result.insert("is_window", widget->isWindow()); result.insert("is_minimized", widget->isMinimized());
        result.insert("parent_widget_present", widget->parentWidget() != nullptr);
    }
    result.insert("owner_path", ancestry(object)); return result;
}
QJsonObject actionIdentity(QAction* action, QWidget* owner, QObject* dock, Budget& budget) {
    need(action && action->thread() == QThread::currentThread(), "DOCK_ACTION_LOST_OR_WRONG_THREAD");
    QJsonObject result = descriptor(action);
    result.insert("text", bounded(action->text())); result.insert("enabled", action->isEnabled());
    result.insert("checkable", action->isCheckable()); result.insert("checked", action->isChecked());
    result.insert("triggered", false);
    const auto objects = action->associatedObjects(); need(objects.size() <= 64, "ASSOCIATED_OBJECT_LIMIT");
    QJsonArray associated; int same_dock = 0;
    for (QObject* object : objects) {
        budget.check(); need(object && object->thread() == QThread::currentThread(), "ASSOCIATED_OBJECT_CHANGED");
        auto item = widgetIdentity(object, owner);
        item.insert("is_same_ancestor_dock", dock && object == dock);
        if (dock && object == dock) ++same_dock;
        associated.append(item);
    }
    result.insert("associated_objects", associated); result.insert("same_ancestor_dock_association_count", same_dock);
    return result;
}
QJsonObject dockCandidate(QObject* root, QWidget* owner, Budget& budget) {
    QPointer<QObject> live(root);
    QJsonObject result = widgetIdentity(root, owner);
    QJsonArray ancestors, stacks;
    QPointer<QDockWidget> nearest;
    QObject* object = root;
    for (int depth = 0; object && depth < 24; ++depth, object = object->parent()) {
        budget.check(); need(object->thread() == QThread::currentThread(), "ANCESTOR_THREAD_CHANGED");
        ancestors.append(widgetIdentity(object, owner));
        if (!nearest) nearest = qobject_cast<QDockWidget*>(object);
        if (auto* stack = qobject_cast<QStackedWidget*>(object)) {
            need(stack->count() <= 64, "ANCESTOR_STACK_LIMIT");
            QJsonArray pages;
            for (int i = 0; i < stack->count(); ++i) {
                auto* page = stack->widget(i); need(page, "STACK_PAGE_LOST");
                pages.append(QJsonObject{{"index", i}, {"identity", widgetIdentity(page, owner)},
                    {"current", stack->currentWidget() == page}});
            }
            stacks.append(QJsonObject{{"identity", widgetIdentity(stack, owner)},
                {"current_index", stack->currentIndex()}, {"pages", pages}, {"page_set_calls", 0}});
        }
    }
    need(!object, "ANCESTOR_LIMIT");
    result.insert("ancestors", ancestors); result.insert("ancestor_stacks", stacks);
    result.insert("ancestor_dock_present", nearest != nullptr);
    if (nearest) {
        auto dock = widgetIdentity(nearest, owner);
        dock.insert("floating", nearest->isFloating()); dock.insert("features", int(nearest->features()));
        auto* toggle = nearest->toggleViewAction();
        dock.insert("toggle_action", actionIdentity(toggle, owner, nearest, budget));
        dock.insert("toggle_action_checked_matches_not_hidden", toggle->isCheckable() && toggle->isChecked() == !nearest->isHidden());
        result.insert("ancestor_dock", dock);
    }
    const auto children = root->children();
    QJsonArray direct;
    int emitted = 0;
    for (QObject* child : children) {
        budget.check();
        if (!qobject_cast<QWidget*>(child)) continue;
        if (emitted == 32) break;
        direct.append(widgetIdentity(child, owner)); ++emitted;
    }
    result.insert("direct_widget_children", direct);
    int widget_count = 0;
    for (QObject* child : children) if (qobject_cast<QWidget*>(child)) ++widget_count;
    result.insert("direct_widget_child_count", widget_count);
    result.insert("direct_widget_children_truncated", widget_count > emitted);
    result.insert("hidden_numeric_read", false); result.insert("toggle_restore_authorized", false);
    need(live && fixedDockCandidate(live), "FIXED_DOCK_CANDIDATE_LOST"); return result;
}
QJsonObject simulationPropertyAction(QWidget* owner, Budget& budget) {
    const auto children = owner->children(); need(children.size() <= 5000, "ACTION_OWNER_CHILD_LIMIT");
    QPointer<QAction> action; int matches = 0;
    for (QObject* child : children) {
        budget.check(); need(child && child->thread() == QThread::currentThread(), "ACTION_CHILD_CHANGED");
        if (child->objectName() == "actionSet_Simulation_Property") { ++matches; action = qobject_cast<QAction*>(child); }
    }
    QJsonObject result{{"object_name", "actionSet_Simulation_Property"}, {"match_count", matches}, {"triggered", false}};
    if (matches == 1 && action && action->parent() == owner) {
        result = actionIdentity(action, owner, nullptr, budget); result.insert("match_count", matches);
        result.insert("reference_unique", true);
    } else result.insert("reference_unique", false);
    return result;
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_dock_identity_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_dock_identity_inspect(std::uint32_t pid, std::uint64_t creation,
    unsigned budget_ms, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!output || !written || !needed || cap != output_capacity || budget_ms < 50 || budget_ms > 1500) return -1;
    FILETIME born{}, exited{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        || ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) != creation) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread() || std::strcmp(qVersion(), "6.10.3")) return -3;
    if (busy.test_and_set()) return -4;
    struct Release { ~Release() { busy.clear(); } } release;
    Budget budget(budget_ms); int code = 1; QJsonArray candidates;
    QJsonObject report{{"schema", "md-qt-rest-dock-identity/1"}, {"verified", false}, {"catalog_complete", false},
        {"effects_started", false}, {"requires_recovery", false}, {"RestShape_write_contract_verified", false},
        {"toggle_restore_authorized", false}, {"hidden_numeric_read", false}, {"event_calls", 0},
        {"QAction_trigger_calls", 0}, {"method_invoke_calls", 0}, {"property_write_calls", 0}, {"simulation_calls", 0},
        {"OS_input_calls", 0}, {"focus_calls", 0}, {"widget_create_show_calls", 0}, {"message_handler_changes", 0},
        {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)}};
    try {
        const auto widgets = QApplication::allWidgets(); need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
        QPointer<QWidget> owner; int owner_count = 0; QList<QPointer<QWidget>> roots;
        for (QWidget* widget : widgets) {
            budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
            if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") { owner = widget; ++owner_count; }
            if (fixedDockCandidate(widget)) { need(roots.size() < 16, "FIXED_CANDIDATE_LIMIT"); roots.append(widget); }
        }
        need(owner_count == 1 && owner, "CURRENT_MAINWINDOW_NOT_UNIQUE");
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(owner->effectiveWinId()), GA_ROOT);
        DWORD window_pid = 0; const auto tid = GetWindowThreadProcessId(hwnd, &window_pid);
        need(hwnd && IsWindow(hwnd) && window_pid == pid && tid == GetCurrentThreadId(), "OWNER_WINDOW_IDENTITY_DIFFERS");
        const auto before = physical(); idle(before, hwnd);
        report.insert("physical_before", before);
        const auto action = simulationPropertyAction(owner, budget); report.insert("simulation_property_action", action);
        for (auto root : roots) {
            need(root, "FIXED_CANDIDATE_LOST");
            const auto first = dockCandidate(root, owner, budget);
            need(root && dockCandidate(root, owner, budget) == first, "DOCK_IDENTITY_CHANGED_DURING_READ");
            candidates.append(first); report.insert("candidates", candidates);
        }
        need(owner && simulationPropertyAction(owner, budget) == action, "SIMULATION_ACTION_CHANGED_DURING_READ");
        const auto after = physical(); idle(after, hwnd);
        report.insert("physical_after", after); report.insert("physical_state_unchanged", before == after);
        need(before == after, "PHYSICAL_STATE_CHANGED_DURING_READ");
        report.insert("catalog_complete", true); report.insert("verified", true); report.insert("status", "READ_COMPLETE"); code = 0;
    } catch (const std::exception& error) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", error.what()); }
      catch (...) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); }
    report.insert("candidates", candidates); report.insert("candidate_count", candidates.size());
    report.insert("return_code", code); report.insert("elapsed_ms", int(budget.timer.elapsed()));
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}
