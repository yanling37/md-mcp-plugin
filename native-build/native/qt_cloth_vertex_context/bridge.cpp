// Read-only observation of the reviewed MD MeshBox/World action and canvas scope.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QMetaMethod>
#include <QtCore/QMetaProperty>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtWidgets/QApplication>
#include <QtWidgets/QGraphicsItem>
#include <QtWidgets/QGraphicsScene>
#include <QtWidgets/QGraphicsView>
#include <QtWidgets/QToolButton>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <vector>

namespace {
constexpr auto schema = "md-qt-cloth-vertex-context/1";
constexpr auto qt_version = "6.10.3";
constexpr std::uint32_t max_report_bytes = 1024 * 1024;

struct Selector {
    const char* type;
    const char* name;
};

struct ActionSpec {
    const char* name;
    const char* text;
};

constexpr std::array<ActionSpec, 6> actions{{
    {"actionBox_Selecting_Tool", "Select Mesh (Box)"}, {"actionMove_Pattern", "Select/Move"},
    {"action_World_Coordinate_Gizmo", "World Coordinate"}, {"action_Local_Coordinate_Gizmo", "Local Coordinate"},
    {"action_Screen_Coordinate_Gizmo", "Screen Coordinate"}, {"actionScale_Gizmo", "Scale Gizmo"},
}};

constexpr std::array<Selector, 3> owner_path{{
    {"CloUICommon::Frame", ""}, {"CloUICommon::Frame", ""}, {"MDMainWindow", "CLO"},
}};

constexpr std::array<Selector, 4> dock_path{{
    {"QWidget", "centralWg"}, {"QMainWindow", "DummyDockingWindow"},
    {"QWidget", "DummyDockingWidget"}, {"QMainWindow", "DummyWindow"},
}};

constexpr std::array<Selector, 7> canvas_2d_path{{
    {"MVMainDockWidget", "2D Pattern Window"}, {"QWidget", "dockWidgetContentsPattern"},
    {"QMainWindow", "dummyWin2"}, {"QWidget", "dummyWidget2d"},
    {"QGraphicsView", ""}, {"QOpenGLWidget", "widget2d"}, {"QtView2D", ""},
}};

constexpr std::array<Selector, 7> canvas_3d_path{{
    {"MVMainDockWidget", "3D Window"}, {"QWidget", "dockWidgetContentsAvatar"},
    {"QMainWindow", "dummyWin1"}, {"QWidget", "dummyWidget3d"},
    {"QGraphicsView", ""}, {"QOpenGLWidget", "widget3d"}, {"QtView3D", ""},
}};

void require(bool condition, const char* reason) {
    if (!condition) throw std::runtime_error(reason);
}

class Budget {
public:
    explicit Budget(std::uint32_t milliseconds) : deadline_(GetTickCount64() + milliseconds) {}
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
    const DWORD thread = GetWindowThreadProcessId(window, &owner);
    return IsWindow(window) && GetAncestor(window, GA_ROOT) == window && owner == pid && thread == GetCurrentThreadId();
}

struct Physical {
    HWND foreground = GetForegroundWindow();
    HWND focus = GetFocus();
    HWND capture = GetCapture();
    QPointer<QWidget> qt_focus = QApplication::focusWidget();
    QPointer<QWidget> modal = QApplication::activeModalWidget();
    QPointer<QWidget> popup = QApplication::activePopupWidget();
    bool minimized;

    explicit Physical(HWND window) : minimized(IsIconic(window) != FALSE) {}

    bool same_as(const Physical& other) const {
        return foreground == other.foreground && focus == other.focus && capture == other.capture
            && qt_focus == other.qt_focus && modal == other.modal && popup == other.popup && minimized == other.minimized;
    }

    QJsonObject report(HWND window) const {
        return {{"target_foreground", foreground == window}, {"thread_focus_present", focus != nullptr},
                {"thread_capture_present", capture != nullptr}, {"qt_focus_present", !qt_focus.isNull()},
                {"modal_present", !modal.isNull()}, {"popup_present", !popup.isNull()}, {"minimized", minimized}};
    }
};

QString bounded(const QString& value) { return value.left(256); }

bool has_class(const QObject* object, const char* type) {
    return object && std::strcmp(object->metaObject()->className(), type) == 0;
}

QJsonObject describe(QObject* object) {
    QJsonObject value{{"class_name", QString::fromLatin1(object->metaObject()->className())},
                      {"object_name", bounded(object->objectName())}};
    if (auto* widget = qobject_cast<QWidget*>(object)) {
        value.insert("visible", widget->isVisible());
        value.insert("enabled", widget->isEnabled());
    }
    return value;
}

using References = std::vector<QPointer<QObject>>;

QObject* unique_child(QObject* parent, Selector selector, QThread* thread, const Budget& budget, References& refs) {
    const auto& children = parent->children();
    require(children.size() <= 5000, "CHILD_COUNT_LIMIT");
    QObject* match = nullptr;
    for (QObject* child : children) {
        budget.check();
        if (!has_class(child, selector.type) || child->objectName() != QLatin1StringView(selector.name)) continue;
        require(!match && child->parent() == parent && child->thread() == thread, "CHILD_IDENTITY_AMBIGUOUS");
        match = child;
    }
    require(match != nullptr, "FIXED_CHILD_NOT_FOUND");
    refs.emplace_back(match);
    return match;
}

QJsonObject action_state(const QAction* action) {
    return {{"object_name", bounded(action->objectName())}, {"text", bounded(action->text())},
            {"enabled", action->isEnabled()}, {"visible", action->isVisible()},
            {"checkable", action->isCheckable()}, {"checked", action->isChecked()}};
}

struct Group {
    QAction* checked;
    QList<QAction*> members;
    QJsonObject report;
};

Group observe_group(QActionGroup* group, QObject* owner, QThread* thread, const Budget& budget, References& refs) {
    require(group && group->thread() == thread && group->isExclusive(), "ACTION_GROUP_NOT_EXCLUSIVE_OR_NOT_CURRENT");
    refs.emplace_back(group);
    const auto members = group->actions();
    require(!members.isEmpty() && members.size() <= 128, "ACTION_GROUP_COUNT_LIMIT");
    QAction* checked = nullptr;
    QJsonArray states;
    for (QAction* member : members) {
        budget.check();
        require(has_class(member, "QAction") && member->parent() == owner && member->thread() == thread
                && member->actionGroup() == group && members.count(member) == 1, "ACTION_GROUP_MEMBER_IDENTITY_DIFFERS");
        refs.emplace_back(member);
        states.append(action_state(member));
        if (member->isChecked()) {
            require(!checked && member->isCheckable(), "ACTION_GROUP_CHECKED_MEMBER_AMBIGUOUS");
            checked = member;
        }
    }
    require(checked && group->checkedAction() == checked, "ACTION_GROUP_CHECKED_MEMBER_UNAVAILABLE");
    return {checked, members, {{"exclusive", true}, {"checked_action", bounded(checked->objectName())},
                               {"member_count", static_cast<int>(members.size())}, {"members", states}}};
}

QJsonArray associated_objects(QAction* action, QThread* thread, const Budget& budget, References& refs) {
    const auto objects = action->associatedObjects();
    require(objects.size() <= 64, "ACTION_ASSOCIATION_COUNT_LIMIT");
    QJsonArray observations;
    for (QObject* object : objects) {
        budget.check();
        require(object && object->thread() == thread && objects.count(object) == 1, "ACTION_ASSOCIATION_DIFFERS");
        refs.emplace_back(object);
        QJsonObject value = describe(object);
        if (auto* button = qobject_cast<QToolButton*>(object)) {
            value.insert("standard_control", "QToolButton");
            value.insert("default_action_matches", button->defaultAction() == action);
            value.insert("checkable", button->isCheckable());
            value.insert("checked", button->isChecked());
            value.insert("down", button->isDown());
        }
        observations.append(value);
    }
    return observations;
}

bool vertex_term(const QString& name) {
    const QString value = name.toLower();
    return value.contains("vertex") || value.contains("vertices") || value.contains("mesh")
        || value.contains("point") || value.contains("select") || value.contains("particle");
}

QJsonObject own_declarations(QObject* object, const Budget& budget) {
    const QMetaObject* meta = object->metaObject();
    require(meta && meta->methodCount() <= 1024 && meta->propertyCount() <= 256, "METAOBJECT_COUNT_LIMIT");
    QJsonArray methods, properties;
    for (int i = meta->methodOffset(); i < meta->methodCount(); ++i) {
        budget.check();
        const auto method = meta->method(i);
        if (!vertex_term(QString::fromLatin1(method.name()))) continue;
        require(methods.size() < 32, "VERTEX_METHOD_COUNT_LIMIT");
        methods.append(QJsonObject{{"signature", QString::fromLatin1(method.methodSignature()).left(512)},
            {"access", static_cast<int>(method.access())}, {"method_type", static_cast<int>(method.methodType())},
            {"parameter_count", method.parameterCount()}, {"invoked", false}});
    }
    for (int i = meta->propertyOffset(); i < meta->propertyCount(); ++i) {
        budget.check();
        const auto property = meta->property(i);
        if (!vertex_term(QString::fromLatin1(property.name()))) continue;
        require(properties.size() < 32, "VERTEX_PROPERTY_COUNT_LIMIT");
        properties.append(QJsonObject{{"name", QString::fromLatin1(property.name()).left(256)},
            {"type_name", QString::fromLatin1(property.typeName()).left(256)},
            {"readable", property.isReadable()}, {"value_read", false}});
    }
    return {{"class_name", QString::fromLatin1(meta->className())}, {"own_methods", methods}, {"own_properties", properties}};
}

struct Canvas {
    QList<QGraphicsItem*> selected;
    QJsonObject report;
};

Canvas observe_canvas(QObject* dock, bool is_3d, QThread* thread, const Budget& budget, References& refs) {
    QObject* current = dock;
    const auto& path = is_3d ? canvas_3d_path : canvas_2d_path;
    QJsonArray found;
    QGraphicsView* view = nullptr;
    QWidget* viewport = nullptr;
    for (std::size_t i = 0; i < path.size(); ++i) {
        current = unique_child(current, path[i], thread, budget, refs);
        found.append(describe(current));
        if (i == 4) view = qobject_cast<QGraphicsView*>(current);
        if (i == 5) viewport = qobject_cast<QWidget*>(current);
    }
    auto* scene = qobject_cast<QGraphicsScene*>(current);
    require(view && viewport && scene && view->viewport() == viewport && view->scene() == scene,
            "CANVAS_VIEW_VIEWPORT_SCENE_BINDING_DIFFERS");
    auto selected = scene->selectedItems();
    require(selected.size() <= 64, "QT_SELECTION_COUNT_LIMIT");
    // Qt does not specify list order. Pointer ordering stays internal to the comparison.
    std::sort(selected.begin(), selected.end(), std::less<QGraphicsItem*>());
    require(std::adjacent_find(selected.cbegin(), selected.cend()) == selected.cend(), "QT_SELECTION_DUPLICATE_ITEM");
    QJsonArray observations;
    for (QGraphicsItem* item : selected) {
        budget.check();
        require(item && item->scene() == scene && item->isSelected(), "QT_SELECTION_ITEM_CHANGED");
        auto* object = item->toGraphicsObject();
        QJsonObject observation{{"qt_item_type", item->type()}, {"selectable", item->flags().testFlag(QGraphicsItem::ItemIsSelectable)},
                                {"qt_selected", true}, {"has_qgraphics_object", object != nullptr}};
        if (object) {
            require(object->thread() == thread, "QT_SELECTED_OBJECT_THREAD_DIFFERS");
            refs.emplace_back(object);
            observation.insert("object", describe(object));
            observation.insert("metadata", own_declarations(object, budget));
        }
        observations.append(observation);
    }
    return {selected, {{"path", found}, {"view_viewport_scene_bound", true},
                       {"scene_metadata", own_declarations(scene, budget)},
                       {"qt_selected_item_count", static_cast<int>(selected.size())}, {"qt_selected_items", observations}}};
}

struct Observation {
    References refs;
    Canvas two_d, three_d;
    QJsonObject context;
    bool mesh_box = false;
};

Observation observe(QWidget* root, QThread* thread, const Budget& budget) {
    require(has_class(root, "TitleFrame") && root->objectName() == "TitleFrame" && root->thread() == thread,
            "ROOT_TITLEFRAME_IDENTITY_DIFFERS");
    Observation observed;
    auto& refs = observed.refs;
    refs.emplace_back(root);
    QObject* owner = root;
    QJsonArray main_path{describe(root)};
    for (auto selector : owner_path) {
        owner = unique_child(owner, selector, thread, budget, refs);
        main_path.append(describe(owner));
    }

    std::array<QAction*, actions.size()> fixed{};
    for (std::size_t i = 0; i < actions.size(); ++i) {
        const auto spec = actions[i];
        auto* action = qobject_cast<QAction*>(unique_child(owner, {"QAction", spec.name}, thread, budget, refs));
        require(action && action->text() == QLatin1StringView(spec.text) && action->isCheckable(), "FIXED_ACTION_CONTRACT_DIFFERS");
        fixed[i] = action;
    }
    auto* mode_group = fixed[0]->actionGroup();
    auto* coordinates_group = fixed[2]->actionGroup();
    require(mode_group && mode_group == fixed[1]->actionGroup() && coordinates_group != mode_group,
            "FIXED_ACTION_GROUP_BINDING_DIFFERS");
    const Group mode = observe_group(mode_group, owner, thread, budget, refs);
    const Group coordinates = observe_group(coordinates_group, owner, thread, budget, refs);
    require(mode.members.contains(fixed[0]) && mode.members.contains(fixed[1]) && coordinates.members.size() == 4
            && std::all_of(fixed.begin() + 2, fixed.end(), [&](QAction* action) { return coordinates.members.contains(action); }),
            "FIXED_ACTION_GROUP_MEMBERSHIP_DIFFERS");
    observed.mesh_box = mode.checked == fixed[0];
    const bool move = mode.checked == fixed[1];
    const bool world = coordinates.checked == fixed[2];
    QJsonArray action_controls;
    for (auto* action : fixed) {
        auto row = action_state(action);
        row.insert("associated_objects", associated_objects(action, thread, budget, refs));
        action_controls.append(row);
    }

    QObject* dock = owner;
    QJsonArray common_dock_path;
    for (auto selector : dock_path) {
        dock = unique_child(dock, selector, thread, budget, refs);
        common_dock_path.append(describe(dock));
    }
    observed.two_d = observe_canvas(dock, false, thread, budget, refs);
    observed.three_d = observe_canvas(dock, true, thread, budget, refs);
    observed.context = {{"main_path", main_path}, {"common_dock_path", common_dock_path},
        {"mesh_mode", observed.mesh_box ? "MeshBox" : move ? "MovePattern" : "Other"},
        {"world_coordinate_checked", world}, {"n237_action_state_observed", observed.mesh_box && world},
        {"mode_group", mode.report}, {"coordinate_group", coordinates.report}, {"fixed_action_controls", action_controls},
        {"canvas_2d", observed.two_d.report}, {"canvas_3d", observed.three_d.report}};
    return observed;
}

QJsonObject vertex_unavailable(const char* reason) {
    return {{"status", "UNAVAILABLE"}, {"reason", QString::fromLatin1(reason)},
            {"pattern_uuid", QJsonValue::Null}, {"topology_fingerprint", QJsonValue::Null},
            {"native_vertex_index", QJsonValue::Null}};
}
} // namespace

extern "C" __declspec(dllexport) std::uint32_t __cdecl mdqt_cloth_vertex_context_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport) int __cdecl mdqt_cloth_vertex_context_inspect(
    std::uint32_t pid, std::uint64_t creation, std::size_t root_window, std::uint32_t budget_ms,
    char* output, std::uint32_t capacity, std::uint32_t* written, std::uint32_t* needed) noexcept {
    if (!written || !needed) return -1;
    *written = *needed = 0;
    if (!output || !root_window || !pid || !creation || !capacity || capacity > max_report_bytes
        || budget_ms < 50 || budget_ms > 1500) return -1;
    const auto window = reinterpret_cast<HWND>(root_window);
    if (!process_matches(pid, creation) || !window_matches(window, pid)) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QThread::currentThread() != app->thread() || std::strcmp(qVersion(), qt_version)) return -3;

    try {
        Budget budget(budget_ms);
        const Physical physical_before(window);
        auto* root = QWidget::find(static_cast<WId>(root_window));
        QJsonObject report{{"schema", schema}, {"status", "OBSERVATION_UNAVAILABLE"},
            {"process_id", int(pid)}, {"creation_filetime", QString::number(static_cast<qulonglong>(creation))},
            {"root_window", QString::number(static_cast<qulonglong>(root_window))}, {"qt_version", qt_version},
            {"context_verified", false}, {"native_vertex_identity", vertex_unavailable("CONTEXT_NOT_VERIFIED")},
            {"sdk_vertex_getter_calls", 0}, {"model_mutation_calls", 0}, {"event_dispatch_calls", 0},
            {"action_trigger_calls", 0}, {"focus_change_calls", 0}, {"global_input_calls", 0},
            {"raw_qt_pointers_returned", false}, {"native_faults_contained", false}};
        int code = 1;
        try {
            const auto before = observe(root, app->thread(), budget);
            report.insert("context", before.context);
            const auto after = observe(QWidget::find(static_cast<WId>(root_window)), app->thread(), budget);
            require(before.refs == after.refs && std::none_of(before.refs.begin(), before.refs.end(),
                    [](const auto& ref) { return ref.isNull(); }) && before.context == after.context
                    && before.two_d.selected == after.two_d.selected && before.three_d.selected == after.three_d.selected,
                    "MODE_CANVAS_OR_QT_SELECTION_CHANGED_DURING_READ");
            const Physical physical_after(window);
            report.insert("physical_after", physical_after.report(window));
            require(physical_before.same_as(physical_after), "FOREGROUND_FOCUS_OR_WINDOW_STATE_CHANGED");
            require(process_matches(pid, creation) && window_matches(window, pid)
                    && QCoreApplication::instance() == app && QThread::currentThread() == app->thread(), "LIFETIME_CHANGED_DURING_READ");
            budget.check();
            const bool qt_items = !after.two_d.selected.isEmpty() || !after.three_d.selected.isEmpty();
            report.insert("native_vertex_identity", vertex_unavailable(!after.mesh_box ? "MESH_BOX_NOT_ACTIVE"
                : qt_items ? "QT_SELECTED_ITEMS_HAVE_NO_PROVEN_NATIVE_VERTEX_BINDING" : "NO_QT_SELECTED_ITEMS_EXPOSED"));
            report.insert("status", "CONTEXT_OBSERVED_NATIVE_VERTEX_UNAVAILABLE");
            report.insert("context_verified", true);
            report.insert("physical_state_unchanged", true);
            code = 0;
        } catch (const std::exception& error) {
            report.insert("reason", QString::fromUtf8(error.what()).left(256));
        }
        report.insert("physical_before", physical_before.report(window));
        report.insert("return_code", code);
        const QByteArray json = QJsonDocument(report).toJson(QJsonDocument::Compact);
        if (json.size() >= max_report_bytes) return -5;
        *needed = static_cast<std::uint32_t>(json.size()) + 1;
        if (*needed > capacity) return -4;
        std::memcpy(output, json.constData(), static_cast<std::size_t>(json.size()));
        output[json.size()] = '\0';
        *written = static_cast<std::uint32_t>(json.size());
        return code;
    } catch (...) {
        return -5;
    }
}
