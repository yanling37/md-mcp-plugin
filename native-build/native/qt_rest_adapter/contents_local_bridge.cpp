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
QJsonArray contentsPath(QObject* object, QObject* contents) {
    need(contents && contents->objectName() == "PropertyEditorContents", "FIXED_CONTENTS_ROOT_REQUIRED");
    QJsonArray path;
    for (int depth = 0; object && depth < 64; ++depth) {
        auto row = descriptor(object);
        if (object == contents) {
            row.insert("fixed_contents_root", true); path.prepend(row); return path;
        }
        QObject* parent = object->parent(); need(parent, "LOCAL_OWNER_NOT_UNDER_CONTENTS");
        const auto siblings = parent->children(); need(siblings.size() <= 4096, "LOCAL_OWNER_SIBLING_LIMIT");
        int object_index = -1, widget_index = -1, widgets = 0;
        for (int i = 0; i < siblings.size(); ++i) {
            const bool widget = qobject_cast<QWidget*>(siblings[i]) != nullptr;
            if (siblings[i] == object) { object_index = i; if (widget) widget_index = widgets; }
            if (widget) ++widgets;
        }
        need(object_index >= 0, "LOCAL_OWNER_PARENT_LINK_DIFFERS");
        row.insert("sibling_object_index", object_index); row.insert("sibling_widget_index", widget_index);
        path.prepend(row); object = parent;
    }
    throw std::runtime_error("LOCAL_OWNER_CONTENTS_ROOT_NOT_REACHED");
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
QJsonObject numericControl(QWidget* widget, QWidget* root) {
    QJsonObject row{{"object", descriptor(widget)}, {"owner_path", contentsPath(widget, root)},
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
QJsonObject modelRows(QAbstractItemView* view, Budget& budget, bool numeric, int& rowsRemaining, QObject* contentsRoot) {
    auto* model = view->model();
    QJsonObject result{{"view", descriptor(view)}, {"value_roles", QJsonArray{"DisplayRole", "CheckStateRole"}},
        {"setData_calls", 0}, {"matching_rows", QJsonArray{}}, {"scope", "exact Pinching Simulation Override label only"},
        {"numeric_active_scope", numeric}};
    if (numeric) {
        result.insert("value_roles", QJsonArray{"DisplayRole", "EditRole", "CheckStateRole"});
        result.insert("scope", "current active-visible PropertyEditor page; basic scalar roles only");
        result.insert("row_limit", 128); result.insert("column_limit", 3);
        result.insert("row_owner_path", contentsPath(view, contentsRoot));
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
    QJsonObject result{{"root", descriptor(root)}, {"owner_path", contentsPath(root, root)},
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
                stacks.append(QJsonObject{{"object", descriptor(stack)}, {"owner_path", contentsPath(stack, root)},
                    {"current_index", stack->currentIndex()}, {"pages", pages}, {"page_set_calls", 0}});
            }
            if (auto* view = qobject_cast<QAbstractItemView*>(widget.data())) {
                models.append(modelRows(view, budget, true, numericRowsRemaining, root));
            }
            const bool spin = qobject_cast<QDoubleSpinBox*>(widget.data()) || qobject_cast<QSpinBox*>(widget.data());
            const bool standalone_line = qobject_cast<QLineEdit*>(widget.data())
                && !qobject_cast<QAbstractSpinBox*>(widget->parentWidget());
            if (spin || standalone_line) {
                need(controlsRemaining > 0, "NUMERIC_CONTROL_LIMIT"); --controlsRemaining;
                numeric_controls.append(numericControl(widget, editor));
            }
            if (qobject_cast<QLabel*>(widget.data()) || qobject_cast<QAbstractButton*>(widget.data())
                    || qobject_cast<QComboBox*>(widget.data())) {
                need(controls.size() < 256, "ACTIVE_PROPERTY_CONTROL_LIMIT");
                auto row = controlDescriptor(widget);
                row.insert("parent", widget->parent() ? descriptor(widget->parent()) : QJsonObject{});
                row.insert("owner_path", contentsPath(widget, root));
                row.insert("parent_owner_path", contentsPath(widget->parent(), root));
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

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_rest_contents_local_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl mdqt_rest_contents_local_inspect(std::uint32_t pid, std::uint64_t creation,
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
    QJsonObject report{{"schema", "md-qt-rest-contents-local/1"}, {"diagnostic_profile", "visible_contents_local_owner_paths_v1"},
        {"verified", false}, {"catalog_complete", false},
        {"traversal_scope", "visible_PropertyEditorContents_active_widgets_only"}, {"partial_diagnostics_available", true},
        {"owner_path_scope", "fixed_PropertyEditorContents_root_inclusive"}, {"root_local_paths_complete", false},
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
        for (QObject* ancestor = roots[0]; ancestor; ancestor = ancestor->parent()) if (ancestor == owner) { owner_in_chain = true; break; }
        need(owner_in_chain, "PROPERTY_CONTENTS_NOT_CURRENT_OWNER");
        HWND hwnd = GetAncestor(reinterpret_cast<HWND>(owner->effectiveWinId()), GA_ROOT);
        DWORD window_pid = 0; const auto thread = GetWindowThreadProcessId(hwnd, &window_pid);
        need(hwnd && IsWindow(hwnd) && window_pid == pid && thread == GetCurrentThreadId(), "OWNER_WINDOW_IDENTITY_DIFFERS");
        const auto before = physical(); idle(before, hwnd);
        report.insert("main_window_minimized_before", IsIconic(hwnd) != FALSE);
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
        const auto after = physical(); idle(after, hwnd);
        report.insert("main_window_minimized_after", IsIconic(hwnd) != FALSE); need(before == after, "PHYSICAL_STATE_CHANGED_DURING_READ");
        report.insert("actions", first_actions); report.insert("property_roots", pages);
        report.insert("numeric_model_rows_read", 128 - rowsRemaining);
        report.insert("numeric_standard_controls_read", 128 - controlsRemaining);
        report.insert("property_root_count", pages.size()); report.insert("physical_before", before); report.insert("physical_after", after);
        report.insert("physical_state_unchanged", true); report.insert("catalog_complete", true);
        report.insert("root_local_paths_complete", true); report.insert("verified", true); report.insert("status", "READ_COMPLETE"); code = 0;
    } catch (const std::exception& error) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", error.what()); }
      catch (...) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); }
    report.insert("return_code", code); report.insert("elapsed_ms", int(budget.timer.elapsed()));
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact); *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0; *written = unsigned(bytes.size()); return code;
}

