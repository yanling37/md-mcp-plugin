// Independent R2 DLL. R1 source and any loaded R1 module stay unchanged.
#include "bridge.cpp"
#include <QtCore/QAbstractItemModel>
#include <QtWidgets/QAbstractItemView>
#include <QtWidgets/QCheckBox>
#include <QtWidgets/QDoubleSpinBox>
#include <QtWidgets/QLabel>
#include <QtWidgets/QLineEdit>
#include <QtWidgets/QSpinBox>
#include <QtWidgets/QStackedWidget>
#include <cmath>
#include <vector>

namespace {
// Property Editor controls can be nested below the fixed Contents root more
// deeply than the generic whole-object ancestry limit permits. Keep the wire
// path absolute, while bounding the host prefix and Contents subtree separately.
constexpr int contents_owner_path_limit = 64;

QJsonArray propertyOwnerPath(QObject* object, QObject* contents, const QJsonArray& root_path, const Budget& budget) {
    QJsonArray descendants;
    for (int depth = 1; object && object != contents && depth < contents_owner_path_limit;
         ++depth, object = object->parent()) {
        budget.check();
        descendants.prepend(descriptor(object));
    }
    need(object == contents, "PROPERTY_OWNER_CONTENTS_ROOT_NOT_REACHED");
    QJsonArray path = root_path;
    for (const auto row : descendants) path.append(row);
    return path;
}

QJsonObject controlDescriptor(QObject* object) {
    QJsonObject row = descriptor(object);
    if (auto* label = qobject_cast<QLabel*>(object)) {
        row.insert("standard_control", "QLabel"); row.insert("text", bounded(label->text(), 1024));
    } else if (auto* button = qobject_cast<QAbstractButton*>(object)) {
        row.insert("standard_control", "QAbstractButton"); row.insert("text", bounded(button->text()));
        row.insert("checked", button->isChecked()); row.insert("checkable", button->isCheckable());
    } else if (auto* combo = qobject_cast<QComboBox*>(object)) {
        row.insert("standard_control", "QComboBox"); row.insert("count", combo->count());
        row.insert("current_index", combo->currentIndex());
    }
    return row;
}

QJsonObject scalar(const QVariant& value) {
    QJsonObject result{{"valid", value.isValid()}, {"type_id", value.metaType().id()},
        {"type_name", QString::fromLatin1(value.typeName() ? value.typeName() : "")}, {"scalar", false}};
    if (!value.isValid()) return result;
    const int type = value.metaType().id();
    if (type == QMetaType::Bool) { result.insert("scalar", true); result.insert("value", value.toBool()); }
    else if (type == QMetaType::Int || type == QMetaType::UInt || type == QMetaType::LongLong || type == QMetaType::ULongLong) {
        const double number = value.toDouble();
        if (std::isfinite(number) && std::abs(number) <= 9007199254740991.0) {
            result.insert("scalar", true); result.insert("value", QJsonValue::fromVariant(value));
        }
    } else if (type == QMetaType::Double || type == QMetaType::Float) {
        const double number = value.toDouble();
        if (std::isfinite(number)) { result.insert("scalar", true); result.insert("value", number); }
    } else if (type == QMetaType::QString && value.toString().size() <= 1024) {
        result.insert("scalar", true); result.insert("value", value.toString());
    }
    return result;
}
bool activeNumeric(QWidget* widget, QWidget* editor) {
    return widget && editor && className(editor) == "PropertyEditorWidget"
        && editor->isVisible() && widget->isVisible() && widget->isVisibleTo(editor);
}
QJsonArray labelsFor(QWidget* widget, QObject* root) {
    QJsonArray labels;
    QObject* ancestor = widget->parent();
    for (int level = 0; ancestor && ancestor != root && level < 3; ++level, ancestor = ancestor->parent()) {
        const auto children = ancestor->children(); need(children.size() <= 256, "NUMERIC_LABEL_ROW_LIMIT");
        for (QObject* child : children) if (auto* label = qobject_cast<QLabel*>(child)) {
            auto* editor = qobject_cast<QWidget*>(root);
            if (!editor || !label->isVisible() || !label->isVisibleTo(editor)) continue;
            if (labels.size() >= 8) return labels;
            labels.append(QJsonObject{{"text", bounded(label->text(), 1024)}, {"object", descriptor(label)}, {"ancestor_level", level}});
        }
    }
    return labels;
}
QJsonObject numericControl(QWidget* widget, QWidget* root, const QJsonArray& owner_path) {
    QJsonObject row{{"object", descriptor(widget)}, {"owner_path", owner_path},
        {"parent", widget->parent() ? descriptor(widget->parent()) : QJsonObject{}}, {"labels", labelsFor(widget, root)},
        {"active_visible_page", true}, {"numeric_owner_binding_verified", false}, {"write_authorized", false}};
    if (auto* spin = qobject_cast<QDoubleSpinBox*>(widget)) {
        const double value = spin->value(), minimum = spin->minimum(), maximum = spin->maximum(), step = spin->singleStep();
        need(std::isfinite(value) && std::isfinite(minimum) && std::isfinite(maximum) && std::isfinite(step), "NONFINITE_NUMERIC_CONTROL");
        row.insert("standard_control", "QDoubleSpinBox"); row.insert("value", value);
        row.insert("minimum", minimum); row.insert("maximum", maximum); row.insert("single_step", step);
        row.insert("decimals", spin->decimals()); row.insert("read_only", spin->isReadOnly());
        row.insert("prefix", bounded(spin->prefix())); row.insert("suffix", bounded(spin->suffix()));
    } else if (auto* integerSpin = qobject_cast<QSpinBox*>(widget)) {
        row.insert("standard_control", "QSpinBox"); row.insert("value", integerSpin->value());
        row.insert("minimum", integerSpin->minimum()); row.insert("maximum", integerSpin->maximum()); row.insert("single_step", integerSpin->singleStep());
        row.insert("decimals", 0); row.insert("read_only", integerSpin->isReadOnly());
        row.insert("prefix", bounded(integerSpin->prefix())); row.insert("suffix", bounded(integerSpin->suffix()));
    } else if (auto* line = qobject_cast<QLineEdit*>(widget)) {
        row.insert("standard_control", "QLineEdit"); row.insert("read_only", line->isReadOnly());
        row.insert("echo_mode", int(line->echoMode()));
        const bool normal = line->echoMode() == QLineEdit::Normal;
        row.insert("text_read", normal); if (normal) row.insert("text", bounded(line->text(), 1024));
        row.insert("acceptable_input", line->hasAcceptableInput());
    }
    return row;
}
QJsonObject modelRows(QAbstractItemView* view, Budget& budget, bool numeric, int& rowsRemaining,
                      const QJsonArray& owner_path) {
    auto* model = view->model();
    QJsonObject result{{"view", descriptor(view)}, {"value_roles", QJsonArray{"DisplayRole", "CheckStateRole"}},
        {"setData_calls", 0}, {"matching_rows", QJsonArray{}}, {"scope", "exact Pinching Simulation Override label only"},
        {"numeric_active_scope", numeric}};
    if (numeric) {
        result.insert("value_roles", QJsonArray{"DisplayRole", "EditRole", "CheckStateRole"});
        result.insert("scope", "current active-visible PropertyEditor page; basic scalar roles only");
        result.insert("row_limit", 128); result.insert("column_limit", 3);
        result.insert("row_owner_path", owner_path);
    }
    if (!model) { result.insert("model_present", false); return result; }
    need(model->thread() == QThread::currentThread(), "PROPERTY_MODEL_THREAD_CHANGED");
    result.insert("model_present", true); result.insert("model", descriptor(model));
    struct Item { QModelIndex parent; QJsonArray path; int depth; };
    QList<Item> pending{{view->rootIndex(), {}, 0}};
    QJsonArray matches, numeric_rows; int visited = 0; bool truncated = false;
    while (!pending.isEmpty()) {
        const auto item = pending.takeFirst(); budget.check();
        need(item.depth <= 8, "PROPERTY_MODEL_DEPTH_LIMIT");
        const int rows = model->rowCount(item.parent), columns = model->columnCount(item.parent);
        need(rows >= 0 && rows <= 4096 && columns >= 0 && columns <= 32, "PROPERTY_MODEL_SHAPE_LIMIT");
        for (int r = 0; r < rows; ++r) {
            if (numeric && rowsRemaining <= 0) { truncated = true; break; }
            budget.check(); need(++visited <= 4096, "PROPERTY_MODEL_ROW_LIMIT");
            const auto index = model->index(r, 0, item.parent);
            need(index.isValid(), "PROPERTY_MODEL_INDEX_INVALID");
            const QString label = bounded(model->data(index, Qt::DisplayRole).toString(), 1024);
            QJsonArray path = item.path; path.append(r);
            if (numeric) {
                --rowsRemaining; QJsonArray cells;
                for (int c = 0; c < qMin(columns, 3); ++c) {
                    budget.check(); const auto cell = model->index(r, c, item.parent);
                    need(cell.isValid(), "NUMERIC_MODEL_INDEX_INVALID");
                    cells.append(QJsonObject{{"column", c}, {"display", scalar(model->data(cell, Qt::DisplayRole))},
                        {"edit", scalar(model->data(cell, Qt::EditRole))}, {"checkstate", scalar(model->data(cell, Qt::CheckStateRole))},
                        {"flags", int(model->flags(cell))}});
                }
                numeric_rows.append(QJsonObject{{"row_path", path}, {"label", label}, {"cells", cells},
                    {"active_visible_page", true}, {"numeric_owner_binding_verified", false}, {"write_authorized", false}});
            }
            if (label == "Pinching Simulation Override" || label == "bPinchingSimulationOverride") {
                QJsonArray values;
                for (int c = 0; c < qMin(columns, 2); ++c) {
                    const auto cell = model->index(r, c, item.parent);
                    const auto checked = model->data(cell, Qt::CheckStateRole);
                    values.append(QJsonObject{{"column", c}, {"display", bounded(model->data(cell, Qt::DisplayRole).toString(), 1024)},
                        {"checkstate_present", checked.isValid()},
                        {"checkstate_int", checked.isValid() ? QJsonValue(checked.toInt()) : QJsonValue()},
                        {"flags", int(model->flags(cell))}});
                }
                matches.append(QJsonObject{{"row_path", path}, {"label", label}, {"cells", values},
                    {"native_property_identity_verified", false}, {"write_authorized", false}});
            }
            if (model->hasChildren(index)) pending.append(Item{index, path, item.depth + 1});
        }
        if (truncated) break;
    }
    result.insert("matching_rows", matches); result.insert("visited_rows", visited);
    result.insert("scan_complete", !truncated); result.insert("truncated", truncated);
    if (numeric) result.insert("numeric_rows", numeric_rows);
    return result;
}
QJsonObject propertyPageActive(QObject* root, Budget& budget, int& numericRowsRemaining,
        int& controlsRemaining, QJsonObject& progress) {
    QPointer<QObject> live(root);
    auto* editor = qobject_cast<QWidget*>(root);
    const auto root_path = ancestry(root);
    QJsonObject result{{"root", descriptor(root)}, {"owner_path", root_path},
        {"write_authorized", false}, {"page_restore_authorized", false},
        {"traversal_scope", "active_visible_widgets_only"}};
    QJsonArray controls, stacks, models, numeric_controls;
    int nodes = 0, hidden = 0, nonwidget = 0, inactive_stack = 0;
    QJsonObject last;
    auto snapshot = [&](bool complete) {
        result.insert("controls", controls); result.insert("stacks", stacks);
        result.insert("item_models", models); result.insert("numeric_controls", numeric_controls);
        result.insert("descendants_scanned", nodes); result.insert("pruned_hidden_widgets", hidden);
        result.insert("pruned_nonwidget_children", nonwidget);
        result.insert("pruned_inactive_stack_pages", inactive_stack);
        result.insert("emitted_standard_controls", controls.size()); result.insert("last_object", last);
        result.insert("scan_complete", complete); result.insert("truncated", !complete);
        progress = result;
    };
    snapshot(false);
    try {
        need(editor && editor->objectName() == "PropertyEditorContents" && className(editor) == "QWidget" && editor->isVisible(), "PROPERTY_EDITOR_NOT_ACTIVE_VISIBLE");
        result.insert("metadata", metadata(root, budget));
        QList<QPointer<QWidget>> queue{editor};
        while (!queue.isEmpty()) {
            budget.check(); auto widget = queue.takeFirst();
            need(widget && widget->thread() == QThread::currentThread(), "ACTIVE_PROPERTY_WIDGET_CHANGED");
            if (!widget->isVisible() || !widget->isVisibleTo(editor)) { ++hidden; continue; }
            last = descriptor(widget); need(++nodes <= 1024, "ACTIVE_PROPERTY_NODE_LIMIT");
            if (auto* stack = qobject_cast<QStackedWidget*>(widget.data())) {
                need(stack->count() <= 64, "PROPERTY_STACK_LIMIT"); QJsonArray pages;
                for (int i = 0; i < stack->count(); ++i) {
                    auto* page = stack->widget(i); need(page, "PROPERTY_PAGE_LOST");
                    pages.append(QJsonObject{{"index", i}, {"object", descriptor(page)},
                        {"current", i == stack->currentIndex()}, {"preexisting_page", true}});
                }
                stacks.append(QJsonObject{{"object", descriptor(stack)},
                    {"owner_path", propertyOwnerPath(stack, editor, root_path, budget)},
                    {"current_index", stack->currentIndex()}, {"pages", pages}, {"page_set_calls", 0}});
            }
            if (auto* view = qobject_cast<QAbstractItemView*>(widget.data())) {
                models.append(modelRows(view, budget, true, numericRowsRemaining,
                                        propertyOwnerPath(view, editor, root_path, budget)));
            }
            const bool spin = qobject_cast<QDoubleSpinBox*>(widget.data()) || qobject_cast<QSpinBox*>(widget.data());
            const bool standalone_line = qobject_cast<QLineEdit*>(widget.data())
                && !qobject_cast<QAbstractSpinBox*>(widget->parentWidget());
            if (spin || standalone_line) {
                need(controlsRemaining > 0, "NUMERIC_CONTROL_LIMIT"); --controlsRemaining;
                numeric_controls.append(numericControl(widget, editor,
                                                       propertyOwnerPath(widget, editor, root_path, budget)));
            }
            if (qobject_cast<QLabel*>(widget.data()) || qobject_cast<QAbstractButton*>(widget.data())
                    || qobject_cast<QComboBox*>(widget.data())) {
                need(controls.size() < 256, "ACTIVE_PROPERTY_CONTROL_LIMIT");
                auto row = controlDescriptor(widget);
                row.insert("parent", widget->parent() ? descriptor(widget->parent()) : QJsonObject{});
                controls.append(row);
            }
            // Prune before enqueue. Models/layouts and all other non-widget
            // QObjects are never traversed; view.model() is read only via the
            // bounded public roles above. Hidden/inactive pages cannot consume caps.
            for (QObject* child : widget->children()) {
                budget.check(); auto* child_widget = qobject_cast<QWidget*>(child);
                if (!child_widget) { ++nonwidget; continue; }
                if (auto* stack = qobject_cast<QStackedWidget*>(widget.data())) {
                    if (stack->indexOf(child_widget) >= 0 && stack->currentWidget() != child_widget) {
                        ++inactive_stack; continue;
                    }
                }
                if (!child_widget->isVisible() || !child_widget->isVisibleTo(editor)) { ++hidden; continue; }
                queue.append(child_widget);
            }
        }
        need(live && editor->isVisible(), "PROPERTY_ROOT_LOST_OR_HIDDEN");
        snapshot(true); return result;
    } catch (const std::exception& error) {
        result.insert("read_failure_reason", error.what()); snapshot(false); throw;
    } catch (...) {
        result.insert("read_failure_reason", "UNEXPECTED_PROPERTY_READ_EXCEPTION"); snapshot(false); throw;
    }
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_visible_contents_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_visible_contents_inspect(std::uint32_t pid, std::uint64_t creation,
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
    Budget budget(budget_ms); int code = 1;
    QJsonObject report{{"schema", "md-qt-rest-visible-contents/1"}, {"diagnostic_profile", "visible_property_editor_contents_pruned_v1"},
        {"verified", false}, {"catalog_complete", false},
        {"traversal_scope", "visible_PropertyEditorContents_active_widgets_only"}, {"partial_diagnostics_available", true},
        {"effects_started", false}, {"requires_recovery", false}, {"action_entry_authorized", false},
        {"original_page_restoration_contract_verified", false}, {"RestShape_write_contract_verified", false},
        {"solver_rest_value_read", false}, {"model_data_preservation_verified", false},
        {"event_calls", 0}, {"QAction_trigger_calls", 0}, {"method_invoke_calls", 0}, {"QMetaProperty_read_calls", 0},
        {"property_write_calls", 0}, {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0},
        {"widget_create_show_calls", 0}, {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)}};
    try {
        const auto widgets = QApplication::allWidgets(); need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
        QPointer<QWidget> owner; int owner_count = 0; QList<QPointer<QObject>> roots;
        for (QWidget* widget : widgets) {
            budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
            if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") { owner = widget; ++owner_count; }
            if (widget->objectName() == "PropertyEditorContents" && className(widget) == "QWidget" && widget->isVisible()) {
                need(roots.size() < 8, "VISIBLE_PROPERTY_ROOT_LIMIT"); roots.append(widget);
            }
        }
        need(owner_count == 1 && owner, "CURRENT_MAINWINDOW_NOT_UNIQUE");
        need(roots.size() == 1, "VISIBLE_PROPERTY_CONTENTS_NOT_UNIQUE");
        need(roots[0]->parent() && className(roots[0]->parent()) == "MVDockWidget"
            && roots[0]->parent()->objectName() == "Property Editor", "PROPERTY_CONTENTS_DOCK_BINDING_DIFFERS");
        bool owner_in_chain = false;
        for (QObject* ancestor = roots[0]; ancestor; ancestor = ancestor->parent()) if (ancestor == owner) owner_in_chain = true;
        need(owner_in_chain, "PROPERTY_CONTENTS_NOT_CURRENT_OWNER");
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(owner->effectiveWinId()), GA_ROOT);
        DWORD window_pid = 0; const auto thread = GetWindowThreadProcessId(hwnd, &window_pid);
        need(hwnd && IsWindow(hwnd) && window_pid == pid && thread == GetCurrentThreadId(), "OWNER_WINDOW_IDENTITY_DIFFERS");
        const auto before = physical(); idle(before, hwnd);
        const auto first_actions = actions(owner, budget); QJsonArray pages;
        int rowsRemaining = 128, controlsRemaining = 128;
        for (auto root : roots) {
            need(root, "PROPERTY_ROOT_LOST"); QJsonObject partial;
            try { pages.append(propertyPageActive(root, budget, rowsRemaining, controlsRemaining, partial)); }
            catch (...) {
                if (!partial.isEmpty()) pages.append(partial);
                report.insert("property_roots", pages); report.insert("property_root_count", pages.size());
                report.insert("numeric_model_rows_read", 128 - rowsRemaining);
                report.insert("numeric_standard_controls_read", 128 - controlsRemaining); throw;
            }
            report.insert("property_roots", pages); report.insert("property_root_count", pages.size());
        }
        need(owner && actions(owner, budget) == first_actions, "ACTION_VALUES_CHANGED_DURING_READ");
        const auto after = physical(); idle(after, hwnd); need(before == after, "PHYSICAL_STATE_CHANGED_DURING_READ");
        report.insert("actions", first_actions); report.insert("property_roots", pages);
        report.insert("numeric_model_rows_read", 128 - rowsRemaining);
        report.insert("numeric_standard_controls_read", 128 - controlsRemaining);
        report.insert("property_root_count", pages.size()); report.insert("physical_before", before); report.insert("physical_after", after);
        report.insert("physical_state_unchanged", true); report.insert("catalog_complete", true);
        report.insert("verified", true); report.insert("status", "READ_COMPLETE"); code = 0;
    } catch (const std::exception& error) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", error.what()); }
      catch (...) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); }
    report.insert("return_code", code); report.insert("elapsed_ms", int(budget.timer.elapsed()));
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}

// Separate fixed command export, no caller-controlled action/slot. Opening this
// property page is the intended persistent UI effect; no old-page restoration.
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_visible_contents_open_simulation_properties(
    std::uint32_t pid, std::uint64_t creation, unsigned budget_ms, char* output, unsigned cap,
    unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!output || !written || !needed || cap != 2 * output_capacity || budget_ms < 50 || budget_ms > 1500) return -1;
    QJsonObject result{{"schema", "md-qt-rest-open-properties/1"}, {"verified", false},
        {"action_object_name", "actionSet_Simulation_Property"},
        {"effects_started", false}, {"requires_recovery", false}, {"QAction_trigger_calls", 0},
        {"event_calls", 0}, {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0},
        {"show_raise_activate_calls", 0}, {"property_write_calls", 0}, {"action_returned", false},
        {"RestShape_write_contract_verified", false}, {"model_data_preservation_verified", false},
        {"simulation_state_independently_verified", false}, {"native_faults_contained", false},
        {"original_property_page_restore_required", false}, {"automatic_retry_allowed", false},
        {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)}};
    int code = 1; bool dispatched = false;
    try {
        // Full process/GUI/preflight and initial visible Contents read happen in
        // the same native batch. No SDK/Python call occurs between these phases.
        std::vector<char> buffer(output_capacity); unsigned first_written = 0, first_needed = 0;
        const int initial_code = mdqt_rest_visible_contents_inspect(pid, creation, budget_ms,
            buffer.data(), output_capacity, &first_written, &first_needed);
        if (initial_code < 0 && !first_written) return initial_code;
        need(first_written && first_needed == first_written + 1, "BEFORE_CONTENTS_RECEIPT_MISSING");
        const auto before = QJsonDocument::fromJson(QByteArray(buffer.data(), int(first_written))).object();
        result.insert("before_read", before);
        need(initial_code == 0 && before.value("verified").toBool(), "BEFORE_CONTENTS_NOT_QUALIFIED");
        if (busy.test_and_set()) return -4;
        struct ReleaseBusy { ~ReleaseBusy() { busy.clear(); } } release_busy;
        Budget budget(budget_ms);
        auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
        need(app && app->thread() == QThread::currentThread(), "GUI_THREAD_CHANGED");
        QPointer<QWidget> owner, contents; int owners = 0, content_count = 0;
        for (QWidget* widget : QApplication::allWidgets()) {
            budget.check();
            if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") { owner = widget; ++owners; }
            if (className(widget) == "QWidget" && widget->objectName() == "PropertyEditorContents" && widget->isVisible()) {
                contents = widget; ++content_count;
            }
        }
        need(owners == 1 && owner && content_count == 1 && contents, "COMMAND_OWNER_CONTENTS_NOT_UNIQUE");
        bool owns_contents = false;
        for (QObject* object = contents; object; object = object->parent()) if (object == owner) owns_contents = true;
        need(owns_contents, "COMMAND_CONTENTS_OWNER_CHANGED");
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(owner->effectiveWinId()), GA_ROOT);
        need(hwnd && IsWindow(hwnd) && IsIconic(hwnd), "MAIN_WINDOW_MUST_REMAIN_MINIMIZED");
        result.insert("main_window_minimized_before", true);
        const auto physical_before = physical(); idle(physical_before, hwnd);
        result.insert("physical_before", physical_before);
        QPointer<QAction> action; int matches = 0;
        need(owner->children().size() <= 5000, "COMMAND_OWNER_CHILD_LIMIT");
        for (QObject* object : owner->children()) {
            budget.check();
            if (object->objectName() == "actionSet_Simulation_Property") { ++matches; action = qobject_cast<QAction*>(object); }
        }
        need(matches == 1 && action && action->parent() == owner && action->thread() == app->thread()
            && className(action) == "QAction" && action->text() == "Simulation Properties"
            && action->isEnabled() && !action->isCheckable() && !action->isChecked(), "FIXED_SIMULATION_PROPERTIES_ACTION_NOT_READY");
        result.insert("action_owner_reference_verified", true);
        result.insert("receiver_connection_introspection_performed", false);
        result.insert("elapsed_before_trigger_ms", int(budget.timer.elapsed())); budget.check();
        result.insert("effects_started", true); result.insert("requires_recovery", true);
        result.insert("QAction_trigger_calls", 1); dispatched = true;
        action->trigger();  // Exactly this one public, already-observed action.
        result.insert("action_returned", true); result.insert("elapsed_after_trigger_ms", int(budget.timer.elapsed()));
        need(owner && contents && action && action->parent() == owner && action->objectName() == "actionSet_Simulation_Property"
            && contents->objectName() == "PropertyEditorContents", "COMMAND_REFS_CHANGED");
        need(IsWindow(hwnd) && IsIconic(hwnd), "MAIN_WINDOW_MINIMIZED_STATE_CHANGED");
        result.insert("main_window_minimized_after", true);
        const auto physical_after = physical(); idle(physical_after, hwnd);
        result.insert("physical_after", physical_after); result.insert("physical_state_unchanged", physical_before == physical_after);
        need(physical_before == physical_after, "COMMAND_PHYSICAL_STATE_CHANGED");
        result.insert("current_owner_and_contents_references_preserved", true);
        // Release the re-entry guard only to call the existing read entry; still
        // synchronous in this C ABI with no event pump, SDK or RPC in between.
        busy.clear();
        unsigned after_written = 0, after_needed = 0;
        const int after_code = mdqt_rest_visible_contents_inspect(pid, creation, budget_ms,
            buffer.data(), output_capacity, &after_written, &after_needed);
        if (busy.test_and_set()) throw std::runtime_error("COMMAND_READ_GUARD_CHANGED");
        need(after_written && after_needed == after_written + 1, "AFTER_CONTENTS_RECEIPT_MISSING");
        const auto after = QJsonDocument::fromJson(QByteArray(buffer.data(), int(after_written))).object();
        result.insert("after_read", after);
        need(after_code == 0 && after.value("verified").toBool(), "AFTER_CONTENTS_NOT_QUALIFIED");
        const auto page_observation = [](const QJsonValue& value) {
            QJsonArray rows;
            for (const auto item : value.toArray()) {
                auto row = item.toObject();
                for (const char* key : {"descendants_scanned", "pruned_hidden_widgets", "pruned_nonwidget_children",
                        "pruned_inactive_stack_pages", "emitted_standard_controls", "last_object", "read_failure_reason"})
                    row.remove(QLatin1StringView(key));
                rows.append(row);
            }
            return rows;
        };
        const bool same = page_observation(before.value("property_roots")) == page_observation(after.value("property_roots"));
        result.insert("property_page_observation_changed", !same); result.insert("no_effect_observed", same);
        result.insert("verified", true); result.insert("requires_recovery", false); result.insert("status", "COMMAND_RETURNED"); code = 0;
    } catch (const std::exception& error) {
        result.insert("status", dispatched ? "UNKNOWN" : "REJECTED"); result.insert("reason", error.what());
        result.insert("effects_started", dispatched); result.insert("requires_recovery", dispatched); code = dispatched ? 2 : 1;
    } catch (...) {
        result.insert("status", dispatched ? "UNKNOWN" : "REJECTED"); result.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION");
        result.insert("effects_started", dispatched); result.insert("requires_recovery", dispatched); code = dispatched ? 2 : 1;
    }
    result.insert("return_code", code);
    const auto bytes = QJsonDocument(result).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}
