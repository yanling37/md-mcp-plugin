// Derived from the audited N237 2D MeshBox widget2d viewport H/P/M/R route.
// This bridge dispatches one bounded box and leaves geometric interpretation to readback.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <vector>
#include <QtCore/QCoreApplication>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtGui/QGuiApplication>
#include <QtGui/QMouseEvent>
#include <QtGui/QWindow>
#include <QtWidgets/QApplication>
#include <QtWidgets/QGraphicsScene>
#include <QtWidgets/QGraphicsView>
#include <QtWidgets/QScrollBar>
#include <QtWidgets/QWidget>
#include <QtOpenGLWidgets/QOpenGLWidget>

#if QT_VERSION != QT_VERSION_CHECK(6,10,3) || !defined(_WIN64) || !defined(_MSC_VER) || defined(QT_DEBUG)
#error Exact release Qt 6.10.3 / MSVC x64 public ABI is required.
#endif

namespace {
constexpr const char* schema = "md-qt-cloth-meshbox-select/1";
constexpr int max_box_side = 32;
constexpr auto event_modifiers = Qt::ShiftModifier | Qt::ControlModifier;
std::atomic_flag busy = ATOMIC_FLAG_INIT;
bool unresolved = false; // Accessed only under busy on the Qt GUI thread.
constexpr const wchar_t* md_directory=MDMCP_QT_INSTALL_DIR;
constexpr const char* action_names[]={"actionBox_Selecting_Tool","actionMove_Pattern","action_World_Coordinate_Gizmo",
    "action_Local_Coordinate_Gizmo","action_Screen_Coordinate_Gizmo","actionScale_Gizmo"};
constexpr const char* action_texts[]={"Select Mesh (Box)","Select/Move","World Coordinate","Local Coordinate","Screen Coordinate","Scale Gizmo"};
bool processMatches(std::uint32_t pid,std::uint64_t creation) {
    FILETIME born{},exited{},kernel{},user{};
    return pid==GetCurrentProcessId() && GetProcessTimes(GetCurrentProcess(),&born,&exited,&kernel,&user)
        && ((static_cast<std::uint64_t>(born.dwHighDateTime)<<32)|born.dwLowDateTime)==creation;
}
bool moduleMatches(const wchar_t* name) {
    HMODULE module=GetModuleHandleW(name);if (!module) return false;
    wchar_t path[32768]{},expected[32768]{};const DWORD count=GetModuleFileNameW(module,path,32768);
    return count && count<32768 && swprintf_s(expected,L"%ls%ls",md_directory,name)>=0
        && CompareStringOrdinal(path,-1,expected,-1,TRUE)==CSTR_EQUAL;
}
bool exactObject(QObject* object,QObject* parent,QThread* thread,const char* type,const char* name) {
    return object && object->parent()==parent && object->thread()==thread
        && !std::strcmp(object->metaObject()->className(),type) && object->objectName()==QLatin1StringView(name);
}
QObject* uniqueChild(QObject* parent,QThread* thread,const char* type,const char* name,ULONGLONG began,std::uint32_t budget) {
    if (!parent || parent->thread()!=thread || parent->children().size()>5000) return nullptr;
    QPointer<QObject> found;int count=0;
    for (QObject* child:parent->children()) {
        if (GetTickCount64()-began>=budget || !child || child->thread()!=thread) return nullptr;
        if (exactObject(child,parent,thread,type,name)) {found=child;++count;}
    }
    return count==1 ? found.data() : nullptr;
}
struct Group {
    QPointer<QActionGroup> pointer;std::vector<QPointer<QAction>> members;QPointer<QAction> checked;
    QJsonArray descriptions;bool valid=false;
};
Group inspectGroup(QActionGroup* group,QObject* owner,QThread* thread,ULONGLONG began,std::uint32_t budget) {
    Group g;g.pointer=group;
    if (!group || group->thread()!=thread || !group->isExclusive()) return g;
    const QList<QAction*> actions=group->actions();if (actions.empty() || actions.size()>128) return g;
    int checked=0;
    for (QAction* action:actions) {
        if (GetTickCount64()-began>=budget || !action || action->thread()!=thread || action->parent()!=owner
                || std::strcmp(action->metaObject()->className(),"QAction") || action->actionGroup()!=group
                || action->objectName().size()>256 || action->text().size()>256) return g;
        for (const auto& seen:g.members) if (seen==action) return g;
        g.members.push_back(action);
        if (action->isChecked()) {++checked;g.checked=action;}
        g.descriptions.append(QJsonObject{{"object_name",action->objectName()},{"text",action->text()},
            {"enabled",action->isEnabled()},{"checkable",action->isCheckable()},{"checked",action->isChecked()}});
    }
    g.valid=checked==1 && g.checked && group->checkedAction()==g.checked && group->actions()==actions;
    return g;
}
bool groupSame(const Group& a,const Group& b) {
    if (!a.valid || !b.valid || !a.pointer || a.pointer!=b.pointer || a.members.size()!=b.members.size()) return false;
    // QActionGroup order is observed but not a model identity contract.
    for (const auto& member:a.members) {
        if (!member) return false;int matches=0;
        for (const auto& candidate:b.members) if (candidate==member) ++matches;
        if (matches!=1) return false;
    }
    return true;
}
struct Scope {
    QPointer<QWidget> root,owner;std::vector<QPointer<QObject>> ancestors;
    QPointer<QAction> actions[6];Group mesh,world;bool valid=false,box=false,move=false,world_checked=false;
};
Scope inspectScope(WId hwnd,QThread* thread,ULONGLONG began,std::uint32_t budget) {
    Scope s;s.root=QWidget::find(hwnd);
    if (!s.root || s.root->thread()!=thread || std::strcmp(s.root->metaObject()->className(),"TitleFrame")
            || s.root->objectName()!=QLatin1StringView("TitleFrame")) return s;
    s.ancestors.push_back(s.root);QObject* current=s.root;
    for (const char* type:{"CloUICommon::Frame","CloUICommon::Frame","MDMainWindow"}) {
        current=uniqueChild(current,thread,type,!std::strcmp(type,"MDMainWindow") ? "CLO" : "",began,budget);
        if (!current) return s;s.ancestors.push_back(current);
    }
    s.owner=qobject_cast<QWidget*>(current);if (!s.owner) return s;
    for (int index=0;index<6;++index) {
        s.actions[index]=qobject_cast<QAction*>(uniqueChild(s.owner,thread,"QAction",action_names[index],began,budget));
        if (!s.actions[index] || s.actions[index]->text()!=QLatin1StringView(action_texts[index]) || !s.actions[index]->isCheckable()) return s;
    }
    if (!s.actions[0]->isEnabled() || !s.actions[1]->isEnabled() || !s.actions[2]->isEnabled()) return s;
    s.mesh=inspectGroup(s.actions[0]->actionGroup(),s.owner,thread,began,budget);
    s.world=inspectGroup(s.actions[2]->actionGroup(),s.owner,thread,began,budget);
    if (!s.mesh.valid || !s.world.valid || s.mesh.pointer==s.world.pointer || s.actions[1]->actionGroup()!=s.mesh.pointer
            || s.world.members.size()!=4) return s;
    for (int index=2;index<6;++index) {
        if (s.actions[index]->actionGroup()!=s.world.pointer) return s;
        int matches=0;for (const auto& member:s.world.members) if (member==s.actions[index]) ++matches;
        if (matches!=1) return s;
    }
    s.box=s.mesh.checked==s.actions[0];s.move=s.mesh.checked==s.actions[1];s.world_checked=s.world.checked==s.actions[2];
    s.valid=s.world_checked && (s.box || s.move);
    return s;
}
bool sameScope(const Scope& a,const Scope& b,bool same_mode=true) {
    if (!a.valid || !b.valid || !a.root || a.root!=b.root || !a.owner || a.owner!=b.owner
            || a.ancestors.size()!=4 || b.ancestors.size()!=4 || !groupSame(a.mesh,b.mesh) || !groupSame(a.world,b.world)) return false;
    for (std::size_t i=0;i<4;++i) if (!a.ancestors[i] || a.ancestors[i]!=b.ancestors[i]) return false;
    for (int i=0;i<6;++i) if (!a.actions[i] || a.actions[i]!=b.actions[i]) return false;
    return !same_mode || (a.box==b.box && a.move==b.move);
}
QJsonObject scopeValue(const Scope& s) {
    return {{"verified",s.valid},{"MeshBox_checked",s.box},{"MovePattern_checked",s.move},{"World_checked",s.world_checked},
        {"mesh_group_complete",s.mesh.valid},{"world_group_complete",s.world.valid},
        {"mesh_group_members",s.mesh.descriptions},{"world_group_members",s.world.descriptions}};
}
struct Canvas {
    QPointer<QGraphicsView> view;QPointer<QOpenGLWidget> viewport;QPointer<QGraphicsScene> scene;
    std::vector<QPointer<QObject>> chain;bool valid=false;
};
const char* canvas_classes[]={"QWidget","QMainWindow","QWidget","QMainWindow","MVMainDockWidget",
    "QWidget","QMainWindow","QWidget","QGraphicsView","QOpenGLWidget","QtView2D"};
const char* canvas_names[]={"centralWg","DummyDockingWindow","DummyDockingWidget","DummyWindow","2D Pattern Window",
    "dockWidgetContentsPattern","dummyWin2","dummyWidget2d","","widget2d",""};
Canvas inspectCanvas(const Scope& s,QThread* thread,ULONGLONG began,std::uint32_t budget) {
    Canvas c;QObject* current=s.owner;if (!current) return c;c.chain.push_back(current);
    for (int i=0;i<11;++i) {
        current=uniqueChild(current,thread,canvas_classes[i],canvas_names[i],began,budget);
        if (!current) return c;c.chain.push_back(current);
        if (i==8) c.view=qobject_cast<QGraphicsView*>(current);
        if (i==9) c.viewport=qobject_cast<QOpenGLWidget*>(current);
        if (i==10) c.scene=qobject_cast<QGraphicsScene*>(current);
    }
    c.valid=c.view && c.viewport && c.scene && c.view->viewport()==c.viewport && c.view->scene()==c.scene;return c;
}
bool receiverSafe(const Canvas& c,const Scope& s,QCoreApplication* app,WId hwnd) {
    if (!app || QCoreApplication::instance()!=app || QThread::currentThread()!=app->thread() || !s.root || !s.owner
            || QWidget::find(hwnd)!=s.root || s.ancestors.size()!=4 || !c.view || !c.viewport || !c.scene || c.chain.size()!=12
            || c.chain[0]!=s.owner || c.view->viewport()!=c.viewport || c.view->scene()!=c.scene) return false;
    for (std::size_t i=0;i<4;++i) {
        QObject* object=s.ancestors[i];if (!object || object->thread()!=app->thread()) return false;
        if (i && object->parent()!=s.ancestors[i-1]) return false;
        if (i==0 && (std::strcmp(object->metaObject()->className(),"TitleFrame") || object->objectName()!=QLatin1StringView("TitleFrame"))) return false;
        if (i==3 && (std::strcmp(object->metaObject()->className(),"MDMainWindow") || object->objectName()!=QLatin1StringView("CLO"))) return false;
        if ((i==1 || i==2) && (std::strcmp(object->metaObject()->className(),"CloUICommon::Frame") || !object->objectName().isEmpty())) return false;
    }
    for (int i=1;i<12;++i) if (!exactObject(c.chain[static_cast<std::size_t>(i)],c.chain[static_cast<std::size_t>(i-1)],app->thread(),
            canvas_classes[i-1],canvas_names[i-1])) return false;
    return true;
}
bool sameCanvas(const Canvas& a,const Canvas& b) {
    if (!a.valid || !b.valid || a.chain.size()!=b.chain.size()) return false;
    for (std::size_t i=0;i<a.chain.size();++i) if (!a.chain[i] || a.chain[i]!=b.chain[i]) return false;return true;
}

QJsonArray xy(const QPoint& point) { return {point.x(), point.y()}; }
QJsonArray rect(const QRect& value) { return {value.x(), value.y(), value.width(), value.height()}; }
QJsonArray matrix(const QTransform& value) {
    return {value.m11(), value.m12(), value.m13(), value.m21(), value.m22(), value.m23(),
            value.m31(), value.m32(), value.m33()};
}

struct View {
    QPointer<QWidget> window;
    QPointer<QScrollBar> hbar, vbar;
    QPoint start_window, end_window, start_global, end_global;
    QPointF start_scene, end_scene;
    QJsonObject mapping;
    bool valid = false;
};

View inspectView(const Canvas& canvas, const Scope& scope, const QPoint& start, const QPoint& end) {
    View v;
    if (!canvas.valid || !canvas.view || !canvas.viewport || !scope.owner) return v;
    v.window = canvas.viewport->window();
    v.hbar = canvas.view->horizontalScrollBar();
    v.vbar = canvas.view->verticalScrollBar();
    if (!v.window || !v.hbar || !v.vbar) return v;
    bool window_in_chain = false;
    for (const auto& parent : scope.ancestors) window_in_chain = window_in_chain || parent.data() == v.window.data();
    for (const auto& parent : canvas.chain) window_in_chain = window_in_chain || parent.data() == v.window.data();
    if (!window_in_chain) return v;
    const QSize size = canvas.viewport->size();
    const auto transform = canvas.view->transform();
    const auto viewport_transform = canvas.view->viewportTransform();
    const qreal dpr = canvas.viewport->devicePixelRatioF();
    const int threshold = QApplication::startDragDistance();
    const bool enabled = canvas.view->isEnabled() && canvas.viewport->isEnabled() && canvas.view->isInteractive();
    const bool visible = canvas.view->isVisibleTo(scope.owner) && canvas.viewport->isVisibleTo(canvas.view);
    const QRect inside = canvas.viewport->rect().adjusted(2, 2, -2, -2);
    const auto width = static_cast<std::int64_t>(end.x()) - start.x();
    const auto height = static_cast<std::int64_t>(end.y()) - start.y();
    if (!enabled || !visible || size.width() < 64 || size.height() < 64
            || size.width() > 16384 || size.height() > 16384 || !std::isfinite(dpr) || dpr < .5 || dpr > 8.
            || threshold < 1 || threshold >= max_box_side || width <= threshold || height <= threshold
            || width > max_box_side || height > max_box_side || !inside.contains(start) || !inside.contains(end)
            || !transform.isInvertible() || !viewport_transform.isInvertible()
            || canvas.view->dragMode() != QGraphicsView::NoDrag) return v;
    v.start_window = canvas.viewport->mapTo(v.window, start);
    v.end_window = canvas.viewport->mapTo(v.window, end);
    v.start_global = canvas.viewport->mapToGlobal(start);
    v.end_global = canvas.viewport->mapToGlobal(end);
    v.start_scene = canvas.view->mapToScene(start);
    v.end_scene = canvas.view->mapToScene(end);
    if (!std::isfinite(v.start_scene.x()) || !std::isfinite(v.start_scene.y())
            || !std::isfinite(v.end_scene.x()) || !std::isfinite(v.end_scene.y())
            || canvas.view->mapFromScene(v.start_scene) != start || canvas.view->mapFromScene(v.end_scene) != end
            || canvas.viewport->mapFromGlobal(v.start_global) != start || canvas.viewport->mapFromGlobal(v.end_global) != end
            || canvas.viewport->mapFrom(v.window, v.start_window) != start
            || canvas.viewport->mapFrom(v.window, v.end_window) != end) return v;
    v.mapping = {{"viewport_size", QJsonArray{size.width(), size.height()}}, {"device_pixel_ratio", dpr},
                 {"view_geometry", rect(canvas.view->geometry())}, {"viewport_geometry", rect(canvas.viewport->geometry())},
                 {"view_transform", matrix(transform)}, {"viewport_transform", matrix(viewport_transform)},
                 {"scroll_values", QJsonArray{v.hbar->value(), v.vbar->value()}},
                 {"global_origin", xy(canvas.viewport->mapToGlobal(QPoint(0, 0)))},
                 {"relative_visible", visible}, {"enabled", enabled},
                 {"drag_mode", static_cast<int>(canvas.view->dragMode())}, {"start_drag_distance", threshold}};
    v.valid = true;
    return v;
}

bool sameView(const View& a, const View& b) {
    return a.valid && b.valid && a.window && a.window == b.window
        && a.hbar && a.hbar == b.hbar && a.vbar && a.vbar == b.vbar && a.mapping == b.mapping
        && a.start_window == b.start_window && a.end_window == b.end_window
        && a.start_global == b.start_global && a.end_global == b.end_global
        && a.start_scene == b.start_scene && a.end_scene == b.end_scene;
}

struct Physical {
    HWND foreground = nullptr, focus = nullptr, capture = nullptr;
    DWORD foreground_pid = 0;
    QPointer<QWidget> mouse, keyboard, modal, popup;
    QPointer<QWindow> gui_modal;
    bool valid = false, minimized = false, os_minimized = false, inactive = false;
    QRect root_geometry, owner_geometry;
    Qt::KeyboardModifiers cached = Qt::NoModifier, queried = Qt::NoModifier;
    Qt::MouseButtons buttons = Qt::NoButton;
};

Physical inspectPhysical(const Scope& scope, HWND hwnd, std::uint32_t pid, std::uint64_t creation) {
    Physical p;
    DWORD owner = 0;
    p.valid = scope.root && scope.owner && processMatches(pid, creation) && IsWindow(hwnd)
        && GetWindowThreadProcessId(hwnd, &owner) && owner == pid;
    if (!p.valid) return p;
    p.foreground = GetForegroundWindow();
    if (p.foreground) GetWindowThreadProcessId(p.foreground, &p.foreground_pid);
    p.focus = GetFocus();
    p.capture = GetCapture();
    p.mouse = QWidget::mouseGrabber();
    p.keyboard = QWidget::keyboardGrabber();
    p.modal = QApplication::activeModalWidget();
    p.popup = QApplication::activePopupWidget();
    p.gui_modal = QGuiApplication::modalWindow();
    p.cached = QGuiApplication::keyboardModifiers();
    p.queried = QGuiApplication::queryKeyboardModifiers();
    p.buttons = QGuiApplication::mouseButtons();
    p.minimized = scope.root->isMinimized();
    p.os_minimized = IsIconic(hwnd) != FALSE;
    p.inactive = !scope.root->isActiveWindow();
    p.root_geometry = scope.root->geometry();
    p.owner_geometry = scope.owner->geometry();
    return p;
}

bool physicalSafe(const Physical& p, std::uint32_t pid) {
    return p.valid && p.minimized && p.os_minimized && p.inactive && p.foreground && p.foreground_pid && p.foreground_pid != pid
        && !p.focus && !p.capture && !p.mouse && !p.keyboard && !p.modal && !p.popup && !p.gui_modal
        && p.cached == Qt::NoModifier && p.queried == Qt::NoModifier && p.buttons == Qt::NoButton;
}

bool samePhysical(const Physical& a, const Physical& b, std::uint32_t pid) {
    return physicalSafe(a, pid) && physicalSafe(b, pid) && a.foreground == b.foreground
        && a.foreground_pid == b.foreground_pid && a.root_geometry == b.root_geometry && a.owner_geometry == b.owner_geometry;
}

QJsonObject physicalValue(const Physical& p, std::uint32_t pid) {
    return {{"verified", physicalSafe(p, pid)}, {"background_inactive_minimized", p.minimized && p.os_minimized && p.inactive},
            {"foreground_owner_known", p.foreground && p.foreground_pid != 0},
            {"foreground_is_not_MD", p.foreground_pid != 0 && p.foreground_pid != pid},
            {"OS_thread_focus_absent", p.focus == nullptr}, {"OS_capture_absent", p.capture == nullptr},
            {"Qt_mouse_grabber_absent", p.mouse.isNull()}, {"Qt_keyboard_grabber_absent", p.keyboard.isNull()},
            {"no_modal_or_popup", !p.modal && !p.popup && !p.gui_modal},
            {"cached_keyboard_modifiers", static_cast<int>(p.cached)}, {"queried_keyboard_modifiers", static_cast<int>(p.queried)},
            {"cached_mouse_buttons", static_cast<int>(p.buttons)}};
}

struct EventRecord {
    bool sent = false, returned = false, accepted = false, send_return = false, nonspontaneous = false;
};

bool send(QWidget* receiver, QMouseEvent& event, EventRecord& record) {
    record.nonspontaneous = !event.spontaneous();
    if (!record.nonspontaneous) return false;
    event.setAccepted(false);
    record.sent = true;
    record.send_return = QCoreApplication::sendEvent(receiver, &event);
    record.returned = true;
    record.accepted = event.isAccepted();
    record.nonspontaneous = record.nonspontaneous && !event.spontaneous();
    return record.nonspontaneous && record.send_return && record.accepted;
}

QJsonObject eventValue(const char* kind, const EventRecord& e, const QPoint& position) {
    return {{"kind", kind}, {"position", xy(position)}, {"sent", e.sent}, {"returned", e.returned},
            {"accepted", e.accepted}, {"send_return", e.send_return}, {"nonspontaneous", e.nonspontaneous}};
}

struct Events {
    EventRecord hover, press, move, release;
    bool release_scope = false, cpp_exception = false;

    int count() const { return static_cast<int>(hover.sent) + press.sent + move.sent + release.sent; }
    bool paired() const { return press.sent && press.returned && release.sent && release.returned && release_scope; }
};

class ReleaseGuard {
public:
    ReleaseGuard(const Canvas& canvas, const Scope& scope, QCoreApplication* app, HWND hwnd, std::uint32_t pid,
                 std::uint64_t creation, const Physical& physical, QMouseEvent& start, QMouseEvent& end, Events& events)
        : canvas_(canvas), scope_(scope), app_(app), hwnd_(hwnd), pid_(pid), creation_(creation),
          physical_(physical), start_(start), end_(end), events_(events) {}
    ~ReleaseGuard() noexcept { release(); }
    bool release() noexcept {
        if (attempted_ || !events_.press.sent) return false;
        attempted_ = true;
        try {
            events_.release_scope = receiverSafe(canvas_, scope_, app_, reinterpret_cast<WId>(hwnd_))
                && samePhysical(physical_, inspectPhysical(scope_, hwnd_, pid_, creation_), pid_);
            if (!events_.release_scope) return false;
            return send(canvas_.viewport, events_.move.sent ? end_ : start_, events_.release);
        } catch (...) {
            events_.cpp_exception = true;
            return false;
        }
    }
private:
    const Canvas& canvas_;
    const Scope& scope_;
    QPointer<QCoreApplication> app_;
    HWND hwnd_;
    std::uint32_t pid_;
    std::uint64_t creation_;
    const Physical& physical_;
    QMouseEvent& start_;
    QMouseEvent& end_;
    Events& events_;
    bool attempted_ = false;
};

int writeReport(QJsonObject report, int code, char* output, std::uint32_t capacity,
                std::uint32_t* written, std::uint32_t* needed) {
    const QByteArray bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = static_cast<std::uint32_t>(bytes.size() + 1);
    if (*needed > capacity) {
        if (report.value("effects_started").toBool()) unresolved = true;
        return -5;
    }
    std::memcpy(output, bytes.constData(), static_cast<std::size_t>(bytes.size()));
    output[bytes.size()] = '\0';
    *written = static_cast<std::uint32_t>(bytes.size());
    return code;
}

bool validWindow(HWND hwnd, std::uint32_t pid) {
    DWORD owner = 0;
    return hwnd && IsWindow(hwnd) && GetWindowThreadProcessId(hwnd, &owner) && owner == pid;
}

int runSelect(std::uint32_t pid, std::uint64_t creation, std::uintptr_t window, std::uint32_t budget,
              const QPoint& start, const QPoint& end, const char* mapping, std::uint32_t mapping_bytes,
              char* output, std::uint32_t capacity, std::uint32_t* written, std::uint32_t* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!output || !written || !needed || capacity < 65536 || capacity > 1024 * 1024
            || !mapping || !mapping_bytes || mapping_bytes > 8192 || budget < 50 || budget > 1500) return -1;
    const HWND hwnd = reinterpret_cast<HWND>(window);
    if (!processMatches(pid, creation) || !validWindow(hwnd, pid)) return -2;
    QPointer<QCoreApplication> app = QCoreApplication::instance();
    if (!app || std::strcmp(qVersion(), "6.10.3") || QThread::currentThread() != app->thread()) return -3;
    for (const auto* name : {L"MarvelousDesigner_Personal.exe", L"Qt6Core.dll", L"Qt6Gui.dll", L"Qt6Widgets.dll",
                            L"Qt6OpenGL.dll", L"Qt6OpenGLWidgets.dll"}) {
        if (!moduleMatches(name)) return -3;
    }
    if (busy.test_and_set(std::memory_order_acquire)) return -4;
    struct Unlock { ~Unlock() { busy.clear(std::memory_order_release); } } unlock;
    const ULONGLONG began = GetTickCount64();
    Events events;
    bool verified = false;
    const char* reason = "NATIVE_CALLBACK_UNRESOLVED";
    Scope scope_before, scope_after;
    Physical physical_before, physical_after;
    View view_before, view_after;
    QJsonObject stages;
    try {
        if (unresolved) {
            reason = "PREVIOUS_BATCH_UNRESOLVED";
        } else {
            QJsonParseError parse_error;
            const auto document = QJsonDocument::fromJson(QByteArray(mapping, static_cast<qsizetype>(mapping_bytes)), &parse_error);
            const QJsonObject expected = document.object();
            reason = "INVALID_EXPECTED_VIEW_MAPPING";
            if (parse_error.error == QJsonParseError::NoError && document.isObject() && expected.size() == 12) {
                const auto wid = static_cast<WId>(window);
                QThread* thread = app->thread();
                scope_before = inspectScope(wid, thread, began, budget);
                const Canvas canvas = inspectCanvas(scope_before, thread, began, budget);
                view_before = inspectView(canvas, scope_before, start, end);
                physical_before = inspectPhysical(scope_before, hwnd, pid, creation);
                const auto observe = [&](const char* stage, bool require_no_scene_grab) {
                    if (!app || QCoreApplication::instance() != app) return false;
                    scope_after = inspectScope(wid, thread, began, budget);
                    const Canvas current = inspectCanvas(scope_after, thread, began, budget);
                    view_after = inspectView(current, scope_after, start, end);
                    physical_after = inspectPhysical(scope_after, hwnd, pid, creation);
                    const bool scope_same = sameScope(scope_before, scope_after) && scope_after.box && scope_after.world_checked;
                    const bool receiver_same = sameCanvas(canvas, current) && receiverSafe(canvas, scope_before, app, wid);
                    const bool view_same = sameView(view_before, view_after) && view_after.mapping == expected;
                    const bool physical_same = samePhysical(physical_before, physical_after, pid);
                    const bool no_scene_grab = current.scene && current.scene->mouseGrabberItem() == nullptr;
                    const bool within_budget = GetTickCount64() - began < budget;
                    const bool stable = scope_same && receiver_same && view_same && physical_same && within_budget
                        && (!require_no_scene_grab || no_scene_grab);
                    stages.insert(QLatin1StringView(stage), QJsonObject{{"verified", stable}, {"same_scope", scope_same},
                                  {"same_receiver", receiver_same}, {"same_mapping", view_same}, {"same_physical", physical_same},
                                  {"scene_item_grabber_absent", no_scene_grab}, {"within_budget", within_budget}});
                    return stable;
                };
                reason = "MESHBOX_WORLD_OR_BACKGROUND_SCOPE_UNVERIFIED";
                if (scope_before.valid && scope_before.box && scope_before.world_checked && canvas.valid
                        && receiverSafe(canvas, scope_before, app, wid) && physicalSafe(physical_before, pid)) {
                    reason = "VIEW_MAPPING_OR_BOX_THRESHOLD_UNVERIFIED";
                    if (view_before.valid && view_before.mapping == expected && observe("before", true)) {
                        const QPointF local(start), window_start(view_before.start_window), global_start(view_before.start_global);
                        const QPointF finish(end), window_end(view_before.end_window), global_end(view_before.end_global);
                        QMouseEvent hover(QEvent::MouseMove, local, window_start, global_start, Qt::NoButton, Qt::NoButton,
                                          event_modifiers, Qt::MouseEventSynthesizedByApplication);
                        QMouseEvent press(QEvent::MouseButtonPress, local, window_start, global_start, Qt::LeftButton, Qt::LeftButton,
                                          event_modifiers, Qt::MouseEventSynthesizedByApplication);
                        QMouseEvent move(QEvent::MouseMove, finish, window_end, global_end, Qt::NoButton, Qt::LeftButton,
                                         event_modifiers, Qt::MouseEventSynthesizedByApplication);
                        QMouseEvent release_start(QEvent::MouseButtonRelease, local, window_start, global_start, Qt::LeftButton,
                                                  Qt::NoButton, event_modifiers, Qt::MouseEventSynthesizedByApplication);
                        QMouseEvent release_end(QEvent::MouseButtonRelease, finish, window_end, global_end, Qt::LeftButton,
                                                Qt::NoButton, event_modifiers, Qt::MouseEventSynthesizedByApplication);
                        ReleaseGuard cleanup(canvas, scope_before, app, hwnd, pid, creation, physical_before,
                                             release_start, release_end, events);
                        reason = "VIEWPORT_EVENT_SEQUENCE_UNVERIFIED";
                        const bool ready_to_press = send(canvas.viewport, hover, events.hover) && observe("after_hover", true);
                        const bool ready_to_move = ready_to_press && send(canvas.viewport, press, events.press)
                            && observe("after_press", false);
                        const bool move_complete = ready_to_move && send(canvas.viewport, move, events.move)
                            && observe("after_move", false);
                        const bool released = cleanup.release();
                        const bool final = observe("after_release", true);
                        verified = move_complete && released && final && events.paired() && events.count() == 4;
                        if (verified) reason = "FIXED_WIDGET2D_EVENT_SEQUENCE_COMPLETED";
                    }
                }
            }
        }
    } catch (...) {
        events.cpp_exception = true;
        reason = "CPP_EXCEPTION";
    }
    const bool started = events.count() != 0;
    const int code = verified ? 0 : started ? 2 : 1;
    if (code == 2) unresolved = true;
    try {
        const QJsonObject report{
            {"schema", schema}, {"return_code", code}, {"status", code == 0 ? "EVENTS_DISPATCHED" : code == 1 ? "REJECTED" : "UNKNOWN"},
            {"reason", reason}, {"process_id", static_cast<qint64>(pid)},
            {"creation_filetime", QString::number(static_cast<qulonglong>(creation))},
            {"root_window", QString::number(static_cast<qulonglong>(window))}, {"qt_version", "6.10.3"},
            {"profile", "widget2d_shift_control_hover_press_move_release"}, {"receiver", "QOpenGLWidget/widget2d"},
            {"box_start", xy(start)}, {"box_end", xy(end)}, {"mapping_before", view_before.mapping}, {"mapping_after", view_after.mapping},
            {"scope_before", scopeValue(scope_before)}, {"scope_after", scopeValue(scope_after)}, {"stages", stages},
            {"physical_before", physicalValue(physical_before, pid)}, {"physical_after", physicalValue(physical_after, pid)},
            {"events", QJsonArray{eventValue("hover", events.hover, start), eventValue("press", events.press, start),
                                  eventValue("move", events.move, end), eventValue("release", events.release, events.move.sent ? end : start)}},
            {"direct_synthetic_event_calls", events.count()}, {"viewport_mouse_sendEvent_calls", events.count()},
            {"scene_sendEvent_calls", 0}, {"key_sendEvent_calls", 0}, {"physical_OS_input_calls", 0},
            {"focus_calls", 0}, {"activation_calls", 0}, {"event_pump_calls", 0}, {"QAction_trigger_calls", 0},
            {"model_setter_calls", 0}, {"global_input_cache_setter_calls", 0}, {"OS_cursor_setter_calls", 0},
            {"effects_started", started}, {"requires_recovery", unresolved}, {"dispatch_sequence_completed", verified},
            {"release_receiver_scope_verified", events.release_scope}, {"paired_release_verified", events.paired()},
            {"selection_hit_verified", false}, {"single_vertex_selection_verified", false}, {"geometry_preservation_verified", false},
            {"cpp_exception", events.cpp_exception}, {"elapsed_ms", static_cast<qint64>(GetTickCount64() - began)}
        };
        return writeReport(report, code, output, capacity, written, needed);
    } catch (...) {
        if (started) unresolved = true;
        return -5;
    }
}
} // namespace

extern "C" __declspec(dllexport) std::uint32_t __cdecl mdqt_cloth_meshbox_select_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport) int __cdecl mdqt_cloth_meshbox_select(
        std::uint32_t pid, std::uint64_t creation, std::uintptr_t hwnd, std::uint32_t budget,
        std::int32_t start_x, std::int32_t start_y, std::int32_t end_x, std::int32_t end_y,
        const char* expected_mapping_utf8, std::uint32_t mapping_bytes, char* output, std::uint32_t capacity,
        std::uint32_t* written, std::uint32_t* needed) noexcept {
    return runSelect(pid, creation, hwnd, budget, QPoint(start_x, start_y), QPoint(end_x, end_y),
                     expected_mapping_utf8, mapping_bytes, output, capacity, written, needed);
}
