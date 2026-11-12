// Fixed, read-only Rest Override instance diagnostic. No invocation or setters.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <QtCore/QCoreApplication>
#include <QtCore/QElapsedTimer>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QMetaMethod>
#include <QtCore/QMetaProperty>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtGui/QGuiApplication>
#include <QtWidgets/QAbstractButton>
#include <QtWidgets/QApplication>
#include <QtWidgets/QComboBox>
#include <QtWidgets/QWidget>
#ifdef MDMCP_REST_HOST_CONTROLS
#include <QtWidgets/QGroupBox>
#include <QtWidgets/QLabel>
#endif
#include <atomic>
#include <cstdint>
#include <cstring>
#include <stdexcept>

namespace {
#ifdef MDMCP_REST_HOST_CONTROLS
constexpr auto schema = "md-qt-rest-host-controls/1";
#define REST_ABI mdqt_rest_host_controls_abi_version
#define REST_INSPECT mdqt_rest_host_controls_inspect
#else
constexpr auto schema = "md-qt-rest-adapter/1";
#define REST_ABI mdqt_rest_adapter_abi_version
#define REST_INSPECT mdqt_rest_adapter_inspect
#endif
constexpr unsigned output_capacity = 1048576;
constexpr const char* actionNames[] = {"actionSet_Simulation_Property", "actionSoftSelection",
    "actionSimulationBrushPinching", "actionBrushPinching", "actionPinchBrush", "actionMove_Pattern"};
std::atomic_flag busy = ATOMIC_FLAG_INIT;
void need(bool value, const char* reason) { if (!value) throw std::runtime_error(reason); }
struct Budget {
    QElapsedTimer timer;
    unsigned milliseconds;
    explicit Budget(unsigned value) : milliseconds(value) { timer.start(); }
    void check() const { need(timer.elapsed() < milliseconds, "METADATA_BUDGET_EXCEEDED"); }
};
QString bounded(const QString& value, int limit = 256) {
    need(value.size() <= limit, "METADATA_TEXT_LIMIT"); return value;
}
QString className(QObject* object) { return bounded(QString::fromLatin1(object->metaObject()->className())); }
bool overrideClass(QObject* object) {
    const QString name = className(object);
    return name == "Marvelous::DialogOverrideSettings" || name == "DialogOverrideSettings";
}
QJsonObject descriptor(QObject* object) {
    need(object && object->thread() == QThread::currentThread(), "OBJECT_THREAD_CHANGED");
    QJsonObject row{{"class_name", className(object)}, {"object_name", bounded(object->objectName())}};
    if (auto* widget = qobject_cast<QWidget*>(object)) {
        row.insert("enabled", widget->isEnabled()); row.insert("visible", widget->isVisible());
    }
    return row;
}
QJsonArray ancestry(QObject* object) {
    QJsonArray rows;
    for (int depth = 0; object && depth < 16; ++depth, object = object->parent()) rows.prepend(descriptor(object));
    need(!object, "OWNER_PATH_LIMIT"); return rows;
}
#ifdef MDMCP_REST_HOST_CONTROLS
bool ownedBy(QObject* object, QObject* owner) {
    for (int depth = 0; object && depth < 16; ++depth, object = object->parent()) {
        if (object == owner) return true;
    }
    return false;
}
QJsonObject standardControl(QObject* object, QObject* host) {
    QJsonObject values;
    QString text;
    if (auto* button = qobject_cast<QAbstractButton*>(object)) {
        values = {{"standard_control", "QAbstractButton"}, {"checkable", button->isCheckable()}, {"checked", button->isChecked()}};
        text = button->text();
    } else if (auto* combo = qobject_cast<QComboBox*>(object)) {
        QJsonArray items;
        for (int index = 0; index < combo->count() && index < 16; ++index) items.append(bounded(combo->itemText(index), 1024));
        values = {{"standard_control", "QComboBox"}, {"current_index", combo->currentIndex()}, {"item_count", combo->count()}, {"items", items}};
        text = combo->currentText();
    } else if (auto* label = qobject_cast<QLabel*>(object)) {
        values = {{"standard_control", "QLabel"}};
        text = label->text();
    } else if (auto* box = qobject_cast<QGroupBox*>(object)) {
        values = {{"standard_control", "QGroupBox"}, {"checkable", box->isCheckable()}, {"checked", box->isChecked()}};
        text = box->title();
    } else if (auto* action = qobject_cast<QAction*>(object)) {
        values = {{"standard_control", "QAction"}, {"enabled", action->isEnabled()}, {"visible", action->isVisible()},
                  {"checkable", action->isCheckable()}, {"checked", action->isChecked()}};
        text = action->text();
    } else return {};
    auto row = descriptor(object);
    for (auto it = values.constBegin(); it != values.constEnd(); ++it) row.insert(it.key(), it.value());
    row.insert("text", text.left(1024));
    row.insert("text_truncated", text.size() > 1024);
    QJsonArray path;
    for (; object && object != host && path.size() < 16; object = object->parent()) {
        path.prepend(QJsonArray{className(object), bounded(object->objectName())});
    }
    need(object == host, "HOST_CONTROL_PATH_LIMIT_OR_CHANGED");
    row.insert("path_from_host", path);
    return row;
}
#endif
QJsonObject physical() {
    POINT cursor{};
    const bool valid = GetCursorPos(&cursor) != FALSE;
    unsigned keys = 0;
    for (int code : {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2,
                     VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN}) {
        if (GetAsyncKeyState(code) & 0x8000) ++keys;
    }
    return {{"foreground", QString::number(reinterpret_cast<quintptr>(GetForegroundWindow()))},
        {"focus", QString::number(reinterpret_cast<quintptr>(GetFocus()))},
        {"capture", QString::number(reinterpret_cast<quintptr>(GetCapture()))},
        {"cursor_valid", valid}, {"cursor", QJsonArray{int(cursor.x), int(cursor.y)}}, {"active_key_or_button_count", int(keys)},
        {"modal_present", QApplication::activeModalWidget() != nullptr},
        {"popup_present", QApplication::activePopupWidget() != nullptr},
        {"mouse_grabber_present", QWidget::mouseGrabber() != nullptr},
        {"keyboard_grabber_present", QWidget::keyboardGrabber() != nullptr}};
}
void idle(const QJsonObject& state, HWND window) {
    need(state.value("cursor_valid").toBool() && state.value("active_key_or_button_count").toInt() == 0
        && !state.value("modal_present").toBool() && !state.value("popup_present").toBool()
        && !state.value("mouse_grabber_present").toBool() && !state.value("keyboard_grabber_present").toBool()
        && GetForegroundWindow() != window && GetCapture() == nullptr, "PHYSICAL_READ_SCOPE_NOT_IDLE");
}
QJsonObject metadata(QObject* object, Budget& budget) {
    const auto* meta = object->metaObject();
    need(meta && meta->methodCount() <= 512 && meta->propertyCount() <= 128, "META_COUNT_LIMIT");
    QJsonArray methods, properties;
    for (int index = meta->methodOffset(); index < meta->methodCount(); ++index) {
        budget.check(); const auto method = meta->method(index);
        methods.append(QJsonObject{{"index", index}, {"signature", bounded(QString::fromLatin1(method.methodSignature()), 512)},
            {"return_type", bounded(QString::fromLatin1(method.typeName() ? method.typeName() : ""))},
            {"method_type", int(method.methodType())}, {"access", int(method.access())},
            {"parameter_count", method.parameterCount()}, {"invoked", false}});
    }
    for (int index = meta->propertyOffset(); index < meta->propertyCount(); ++index) {
        budget.check(); const auto property = meta->property(index);
        properties.append(QJsonObject{{"index", index}, {"name", bounded(QString::fromLatin1(property.name()))},
            {"type_name", bounded(QString::fromLatin1(property.typeName() ? property.typeName() : ""))},
            {"readable", property.isReadable()}, {"writable", property.isWritable()}, {"value_read", false}});
    }
    return {{"own_methods", methods}, {"own_properties", properties}, {"declarations_complete", true},
        {"public_declaration_is_callable_proof", false}};
}
QJsonObject dialog(QObject* object, Budget& budget) {
    need(overrideClass(object), "UNEXPECTED_DIALOG_CLASS");
    QPointer<QObject> root(object);
    QJsonObject row = descriptor(object);
    row.insert("owner_path", ancestry(object)); row.insert("metadata", metadata(object, budget));
    // Only the known Rest Shape frame subtree; no arbitrary dialog/property values.
    QList<QPointer<QObject>> queue{object}; QPointer<QObject> frame; int count = 0, matches = 0;
#ifdef MDMCP_REST_HOST_CONTROLS
    QJsonArray hostControls;
#endif
    while (!queue.isEmpty()) {
        budget.check(); auto current = queue.takeFirst();
        need(current && ++count <= 2048 && current->thread() == QThread::currentThread(), "DIALOG_SUBTREE_LIMIT_OR_CHANGED");
        if (current->objectName() == "frameRestShape") { frame = current; ++matches; }
#ifdef MDMCP_REST_HOST_CONTROLS
        if (current != root) {
            const auto control = standardControl(current, root);
            if (!control.isEmpty()) {
                need(hostControls.size() < 128, "HOST_STANDARD_CONTROL_LIMIT");
                hostControls.append(control);
            }
        }
#endif
        for (QObject* child : current->children()) queue.append(child);
    }
#ifdef MDMCP_REST_HOST_CONTROLS
    row.insert("current_mainwindow_in_parent_chain", true);
    row.insert("host_descendants_scanned", count - 1);
    row.insert("host_standard_controls", hostControls);
#endif
    row.insert("rest_frame_match_count", matches);
    QJsonArray controls;
    if (matches == 1 && frame) {
        queue = {frame}; int scanned = 0;
        while (!queue.isEmpty()) {
            budget.check(); auto current = queue.takeFirst();
            need(current && ++scanned <= 256 && current->thread() == QThread::currentThread(), "REST_FRAME_LIMIT_OR_CHANGED");
            QJsonObject control = descriptor(current);
            if (auto* button = qobject_cast<QAbstractButton*>(current.data())) {
                control.insert("standard_control", "QAbstractButton"); control.insert("text", bounded(button->text()));
                control.insert("checkable", button->isCheckable()); control.insert("checked", button->isChecked());
            } else if (auto* combo = qobject_cast<QComboBox*>(current.data())) {
                need(combo->count() <= 16, "SCOPE_ITEM_LIMIT");
                QJsonArray items;
                for (int i = 0; i < combo->count(); ++i) items.append(bounded(combo->itemText(i)));
                control.insert("standard_control", "QComboBox"); control.insert("current_index", combo->currentIndex());
                control.insert("items", items);
            }
            controls.append(control);
            for (QObject* child : current->children()) queue.append(child);
        }
    }
    need(root && overrideClass(root), "DIALOG_REFERENCE_CHANGED");
    row.insert("rest_frame_controls", controls); row.insert("rest_scope_semantics_verified", false);
    row.insert("solver_rest_value_read", false); row.insert("update_reset_authorized", false);
    return row;
}
QJsonArray actions(QWidget* owner, Budget& budget) {
    const auto children = owner->children(); need(children.size() <= 5000, "OWNER_CHILD_LIMIT");
    QJsonArray result;
    for (const char* name : actionNames) {
        QPointer<QAction> action; int matches = 0;
        for (QObject* child : children) {
            budget.check(); need(child && child->thread() == QThread::currentThread(), "ACTION_REFERENCE_CHANGED");
            if (child->objectName() == QLatin1StringView(name)) { ++matches; action = qobject_cast<QAction*>(child); }
        }
        QJsonObject row{{"object_name", name}, {"match_count", matches}, {"triggered", false}};
        if (matches == 1 && action && action->parent() == owner) {
            row.insert("text", bounded(action->text())); row.insert("enabled", action->isEnabled());
            row.insert("checkable", action->isCheckable()); row.insert("checked", action->isChecked());
            row.insert("reference_unique", true);
            if (auto* group = action->actionGroup()) {
                need(group->thread() == QThread::currentThread(), "ACTION_GROUP_THREAD_CHANGED");
                const auto members = group->actions(); need(members.size() <= 64, "ACTION_GROUP_LIMIT");
                QJsonArray names;
                for (QAction* member : members) { budget.check(); need(member, "GROUP_MEMBER_LOST"); names.append(bounded(member->objectName())); }
                row.insert("group_members", names);
                row.insert("group_checked_action_name", group->checkedAction() ? bounded(group->checkedAction()->objectName()) : QString());
            }
        } else row.insert("reference_unique", false);
        result.append(row);
    }
    return result;
}
}

extern "C" __declspec(dllexport) unsigned __cdecl REST_ABI() noexcept { return 1; }
extern "C" __declspec(dllexport) int __cdecl REST_INSPECT(std::uint32_t pid, std::uint64_t creation,
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
    Budget budget(budget_ms);
    QJsonObject report{{"schema", schema}, {"verified", false}, {"effects_started", false},
        {"requires_recovery", false}, {"catalog_complete", false}, {"RestShape_write_contract_verified", false},
        {"action_dispatch_authorized", false}, {"solver_rest_value_read", false},
        {"model_data_preservation_verified", false}, {"event_calls", 0}, {"QAction_trigger_calls", 0},
        {"method_invoke_calls", 0}, {"QMetaProperty_read_calls", 0}, {"property_write_calls", 0},
        {"OS_input_calls", 0}, {"focus_calls", 0}, {"simulation_calls", 0}, {"widget_create_show_calls", 0},
        {"process_id", int(pid)}, {"process_creation_filetime", QString::number(creation)}};
    int code = 1;
    try {
        const auto widgets = QApplication::allWidgets();
        need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
        QPointer<QWidget> owner; int owners = 0; QList<QPointer<QObject>> dialogs;
        for (QWidget* widget : widgets) {
            budget.check(); need(widget && widget->thread() == app->thread(), "WIDGET_THREAD_CHANGED");
            const auto name = className(widget);
            if (name == "MDMainWindow" && widget->objectName() == "CLO") { owner = widget; ++owners; }
            if (overrideClass(widget)) { need(dialogs.size() < 4, "OVERRIDE_INSTANCE_LIMIT"); dialogs.append(widget); }
        }
        need(owners == 1 && owner, "CURRENT_MAINWINDOW_NOT_UNIQUE");
        HWND hwnd = reinterpret_cast<HWND>(owner->effectiveWinId());
        DWORD window_pid = 0; const DWORD tid = GetWindowThreadProcessId(hwnd, &window_pid);
        need(hwnd && IsWindow(hwnd) && window_pid == pid && tid == GetCurrentThreadId(), "OWNER_WINDOW_IDENTITY_DIFFERS");
        HWND physical_root = GetAncestor(hwnd, GA_ROOT);
        need(physical_root && IsWindow(physical_root), "PHYSICAL_ROOT_UNAVAILABLE");
        const auto before = physical(); idle(before, physical_root);
        QJsonArray rows;
        for (auto candidate : dialogs) {
            budget.check(); need(candidate, "OVERRIDE_REFERENCE_LOST");
#ifdef MDMCP_REST_HOST_CONTROLS
            if (!ownedBy(candidate, owner)) continue;
#endif
            const auto first = dialog(candidate, budget);
            need(candidate && dialog(candidate, budget) == first, "OVERRIDE_VALUES_CHANGED_DURING_READ");
            rows.append(first);
        }
        const auto first_actions = actions(owner, budget);
        need(owner && className(owner) == "MDMainWindow" && actions(owner, budget) == first_actions, "ACTION_VALUES_CHANGED_DURING_READ");
        const auto after = physical(); idle(after, physical_root);
        report.insert("owner", descriptor(owner)); report.insert("owner_path", ancestry(owner));
        report.insert("actions", first_actions); report.insert("override_instances", rows);
        report.insert("override_instance_count", rows.size());
        report.insert("physical_before", before); report.insert("physical_after", after);
        report.insert("physical_state_unchanged", before == after);
        need(before == after, "PHYSICAL_STATE_CHANGED_DURING_READ");
        report.insert("catalog_complete", true); report.insert("verified", true);
        report.insert("status", "READ_COMPLETE"); code = 0;
    } catch (const std::exception& error) {
        report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", error.what());
    } catch (...) { report.insert("status", "READ_NOT_VERIFIED"); report.insert("reason", "UNEXPECTED_NATIVE_EXCEPTION"); }
    report.insert("return_code", code); report.insert("elapsed_ms", int(budget.timer.elapsed()));
    const QByteArray bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = unsigned(bytes.size()) + 1;
    if (unsigned(bytes.size()) + 1 > cap) return -5;
    std::memcpy(output, bytes.constData(), size_t(bytes.size())); output[bytes.size()] = 0;
    *written = unsigned(bytes.size()); return code;
}
