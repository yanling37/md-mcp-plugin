// Readonly fixed simulation-entry metadata. Never starts/stops simulation.
#include "bridge.cpp"
#include <QtWidgets/QToolButton>

namespace {
bool simulationName(const QString& name) {
    return name.contains("Simulat", Qt::CaseInsensitive) || name.contains("SimStop", Qt::CaseInsensitive)
        || name.contains("Drape", Qt::CaseInsensitive) || name.contains("Pinch", Qt::CaseInsensitive)
        || name.contains("RestShape", Qt::CaseInsensitive);
}
QJsonArray simulationMetadata(QObject* object, Budget& budget) {
    QJsonArray declarations; int levels = 0, count = 0;
    for (const QMetaObject* meta = object->metaObject(); meta; meta = meta->superClass()) {
        need(++levels <= 16 && meta->methodCount() <= 2048 && meta->propertyCount() <= 256, "SIMULATION_META_LIMIT");
        QJsonArray methods, properties;
        for (int i = meta->methodOffset(); i < meta->methodCount(); ++i) {
            budget.check(); const auto method = meta->method(i); const auto signature = QString::fromLatin1(method.methodSignature());
            if (!simulationName(signature)) continue; need(++count <= 128, "SIMULATION_META_MATCH_LIMIT");
            methods.append(QJsonObject{{"signature", bounded(signature, 512)},
                {"return_type", QString::fromLatin1(method.typeName() ? method.typeName() : "")},
                {"access", int(method.access())}, {"method_type", int(method.methodType())},
                {"parameter_count", method.parameterCount()}, {"invoked", false}});
        }
        for (int i = meta->propertyOffset(); i < meta->propertyCount(); ++i) {
            budget.check(); const auto property = meta->property(i); const auto name = QString::fromLatin1(property.name());
            if (!simulationName(name)) continue; need(++count <= 128, "SIMULATION_META_MATCH_LIMIT");
            properties.append(QJsonObject{{"name", name}, {"type_name", QString::fromLatin1(property.typeName() ? property.typeName() : "")},
                {"readable", property.isReadable()}, {"writable", property.isWritable()}, {"value_read", false}});
        }
        if (!methods.isEmpty() || !properties.isEmpty()) declarations.append(QJsonObject{
            {"declaring_class", QString::fromLatin1(meta->className())}, {"methods", methods}, {"properties", properties}});
    }
    return declarations;
}
QJsonArray fixedSimulationActions(QWidget* owner, Budget& budget) {
    const auto children = owner->children(); need(children.size() <= 5000, "OWNER_CHILD_LIMIT"); QJsonArray result;
    for (const char* name : {"action_SimStop", "action_DrapePattern", "actionSimulationBrushPinching", "actionMove_Pattern"}) {
        QPointer<QAction> action; int matches = 0;
        for (QObject* child : children) {
            budget.check(); need(child && child->thread() == QThread::currentThread(), "ACTION_THREAD_CHANGED");
            if (child->objectName() == QLatin1StringView(name)) { ++matches; action = qobject_cast<QAction*>(child); }
        }
        QJsonObject row{{"object_name", name}, {"match_count", matches}, {"reference_unique", false}, {"triggered", false}};
        if (matches == 1 && action && action->parent() == owner && className(action) == "QAction") {
            row.insert("reference_unique", true); row.insert("text", bounded(action->text())); row.insert("enabled", action->isEnabled());
            row.insert("checkable", action->isCheckable()); row.insert("checked", action->isChecked());
            row.insert("shortcut", bounded(action->shortcut().toString())); row.insert("metadata", metadata(action, budget));
            if (auto* group = action->actionGroup()) {
                need(group->thread() == QThread::currentThread() && group->actions().size() <= 64, "FIXED_GROUP_LIMIT");
                QJsonArray members;
                for (QAction* member : group->actions()) {
                    budget.check(); need(member && member->thread() == QThread::currentThread(), "GROUP_REFERENCE_CHANGED");
                    members.append(QJsonObject{{"object_name", bounded(member->objectName())}, {"text", bounded(member->text())},
                        {"enabled", member->isEnabled()}, {"checkable", member->isCheckable()}, {"checked", member->isChecked()}});
                }
                row.insert("group", QJsonObject{{"exclusive", group->isExclusive()}, {"members", members},
                    {"checked_action_name", group->checkedAction() ? bounded(group->checkedAction()->objectName()) : QString()}});
            }
            const auto associated = action->associatedObjects(); need(associated.size() <= 64, "ASSOCIATED_OBJECT_LIMIT"); QJsonArray objects;
            for (QObject* object : associated) {
                budget.check(); need(object && object->thread() == QThread::currentThread(), "ASSOCIATED_REFERENCE_CHANGED");
                QJsonObject item = descriptor(object);
                if (auto* button = qobject_cast<QToolButton*>(object)) {
                    item.insert("standard_control", "QToolButton"); item.insert("default_action_matches", button->defaultAction() == action);
                    item.insert("checkable", button->isCheckable()); item.insert("checked", button->isChecked());
                    item.insert("down", button->isDown()); item.insert("text", bounded(button->text()));
                }
                objects.append(item);
            }
            row.insert("associated_objects", objects);
        }
        result.append(row);
    }
    return result;
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_simulation_context_abi_version() noexcept { return 2; }
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_simulation_context_inspect(std::uint32_t pid, std::uint64_t creation,
    unsigned budget_ms, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!output || !written || !needed || cap != output_capacity || budget_ms < 50 || budget_ms > 1500) return -1;
    FILETIME born{}, exited{}, kernel{}, user{};
    if (pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        || ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) != creation) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread() || std::strcmp(qVersion(), "6.10.3")) return -3;
    if (busy.test_and_set()) return -4; struct Release { ~Release() { busy.clear(); } } release;
    Budget budget(budget_ms); int code = 1;
    QJsonObject report{{"schema", "md-qt-rest-simulation-context/2"}, {"verified", false}, {"catalog_complete", false},
        {"status", "READ_NOT_VERIFIED"}, {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)},
        {"effects_started", false}, {"requires_recovery", false}, {"event_calls", 0}, {"QAction_trigger_calls", 0},
        {"button_click_calls", 0}, {"method_invoke_calls", 0}, {"QMetaProperty_read_calls", 0}, {"property_write_calls", 0},
        {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0}, {"show_raise_activate_calls", 0},
        {"simulation_running_state_verified", false}, {"start_stop_dispatch_authorized", false}, {"RestShape_write_contract_verified", false}};
    try {
        QPointer<QWidget> owner; int owners = 0; QList<QPointer<QWidget>> overrides;
        const auto widgets = QApplication::allWidgets(); need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
        for (QWidget* widget : widgets) {
            budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
            if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") { owner = widget; ++owners; }
            if (overrideClass(widget)) { need(overrides.size() < 4, "OVERRIDE_INSTANCE_LIMIT"); overrides.append(widget); }
        }
        need(owners == 1 && owner, "MAINWINDOW_NOT_UNIQUE");
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(owner->effectiveWinId()), GA_ROOT);
        DWORD owner_pid = 0; const auto owner_thread = GetWindowThreadProcessId(hwnd, &owner_pid);
        need(hwnd && IsWindow(hwnd) && owner_pid == pid && owner_thread == GetCurrentThreadId() && IsIconic(hwnd), "MINIMIZED_OWNER_WINDOW_DIFFERS");
        const auto before = physical(); idle(before, hwnd); report.insert("physical_before", before); report.insert("main_window_minimized_before", true);
        const auto first_actions = fixedSimulationActions(owner, budget); report.insert("actions", first_actions);
        report.insert("main_window_simulation_declarations", simulationMetadata(owner, budget));
        QJsonArray instances;
        for (auto widget : overrides) {
            need(widget, "OVERRIDE_INSTANCE_LOST"); bool owner_in_chain = false;
            for (QObject* object = widget; object; object = object->parent()) if (object == owner) owner_in_chain = true;
            QJsonObject instance = descriptor(widget); instance.insert("isHidden", widget->isHidden());
            instance.insert("current_mainwindow_in_parent_chain", owner_in_chain); instance.insert("metadata", metadata(widget, budget));
            instances.append(instance);
        }
        report.insert("override_instances", instances); report.insert("override_instance_count", instances.size());
        need(owner && fixedSimulationActions(owner, budget) == first_actions, "FIXED_ACTIONS_CHANGED_DURING_READ");
        const auto after = physical(); idle(after, hwnd); report.insert("physical_after", after);
        auto before_without_cursor = before, after_without_cursor = after;
        before_without_cursor.remove("cursor"); after_without_cursor.remove("cursor");
        // This reader never sets the cursor. Keep all other physical checks exact.
        need(IsWindow(hwnd) && IsIconic(hwnd) && before_without_cursor == after_without_cursor, "MINIMIZED_PHYSICAL_STATE_CHANGED");
        report.insert("main_window_minimized_after", true);
        report.insert("physical_state_unchanged", before == after);
        report.insert("cursor_position_changed_during_read", before.value("cursor") != after.value("cursor"));
        report.insert("verified", true); report.insert("catalog_complete", true); report.insert("status", "READ_COMPLETE"); code = 0;
    } catch (const std::exception& error) { report.insert("reason", error.what()); }
      catch (...) { report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); }
    report.insert("return_code", code); report.insert("elapsed_ms", int(budget.timer.elapsed()));
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}
