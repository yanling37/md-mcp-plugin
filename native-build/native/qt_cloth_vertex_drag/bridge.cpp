// MeshBox/World drag from the reviewed N237 bridge (SHA-256 2fde4ebc31b104c092f13b6800760b004b36e939833835d0b2795104523666d6).
// Bind the viewport and Qt installation to the current process before sending the same five events.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <cwchar>
#include <string>
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
#include <QtWidgets/QGraphicsSceneMouseEvent>
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
struct DragCoordinates {
    double sx,sy,ax,ay,ex,ey;
    int width,height;
    double dpr;
};
bool coordinatesValid(const DragCoordinates& c) {
    for (double v:{c.sx,c.sy,c.ax,c.ay,c.ex,c.ey}) if (!std::isfinite(v)) return false;
    return c.width>=2 && c.width<=16384 && c.height>=2 && c.height<=16384 && c.dpr>=.5 && c.dpr<=4.
        && c.sx>=0 && c.sy>=0 && c.ax>=0 && c.ay>=0 && c.ex>=0 && c.ey>=0
        && c.sx<c.width && c.ax<c.width && c.ex<c.width
        && c.sy<c.height && c.ay<c.height && c.ey<c.height
        && c.ax==c.sx-12. && c.ay==c.sy && (c.ex!=c.sx || c.ey!=c.sy)
        && 12. + std::hypot(c.ex-c.ax,c.ey-c.ay)<=128.;
}
constexpr const char* action_names[]={"actionBox_Selecting_Tool","actionMove_Pattern","action_World_Coordinate_Gizmo",
    "action_Local_Coordinate_Gizmo","action_Screen_Coordinate_Gizmo","actionScale_Gizmo"};
constexpr const char* action_texts[]={"Select Mesh (Box)","Select/Move","World Coordinate","Local Coordinate","Screen Coordinate","Scale Gizmo"};
bool processMatches(std::uint32_t pid,std::uint64_t creation) {
    FILETIME born{},exited{},kernel{},user{};
    return pid==GetCurrentProcessId() && GetProcessTimes(GetCurrentProcess(),&born,&exited,&kernel,&user)
        && ((static_cast<std::uint64_t>(born.dwHighDateTime)<<32)|born.dwLowDateTime)==creation;
}
bool moduleMatches(const wchar_t* name,const std::wstring& directory) {
    HMODULE module=GetModuleHandleW(name);if (!module) return false;
    wchar_t path[32768]{};const DWORD count=GetModuleFileNameW(module,path,32768);
    return count && count<32768 && CompareStringOrdinal(path,-1,(directory+name).c_str(),-1,TRUE)==CSTR_EQUAL;
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
    std::vector<QPointer<QObject>> chain;bool is3d=false,valid=false;
};
const char* canvas_classes[]={"QWidget","QMainWindow","QWidget","QMainWindow","MVMainDockWidget",
    "QWidget","QMainWindow","QWidget","QGraphicsView","QOpenGLWidget","QtView2D"};
const char* names2d[]={"centralWg","DummyDockingWindow","DummyDockingWidget","DummyWindow","2D Pattern Window",
    "dockWidgetContentsPattern","dummyWin2","dummyWidget2d","","widget2d",""};
const char* names3d[]={"centralWg","DummyDockingWindow","DummyDockingWidget","DummyWindow","3D Window",
    "dockWidgetContentsAvatar","dummyWin1","dummyWidget3d","","widget3d",""};
Canvas inspectCanvas(const Scope& s,QThread* thread,bool is3d,ULONGLONG began,std::uint32_t budget) {
    Canvas c;c.is3d=is3d;QObject* current=s.owner;if (!current) return c;c.chain.push_back(current);
    for (int i=0;i<11;++i) {
        current=uniqueChild(current,thread,i==10 && is3d ? "QtView3D" : canvas_classes[i],is3d ? names3d[i] : names2d[i],began,budget);
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
            i==11 && c.is3d ? "QtView3D" : canvas_classes[i-1],c.is3d ? names3d[i-1] : names2d[i-1])) return false;
    return true;
}
bool sameCanvas(const Canvas& a,const Canvas& b) {
    if (!a.valid || !b.valid || a.is3d!=b.is3d || a.chain.size()!=b.chain.size()) return false;
    for (std::size_t i=0;i<a.chain.size();++i) if (!a.chain[i] || a.chain[i]!=b.chain[i]) return false;return true;
}
struct View {
    bool valid=false;QSize size;QRect geometry,view_geometry;QTransform transform,viewport_transform;qreal dpr=0;
    QPointer<QScrollBar> hbar,vbar;int horizontal=0,vertical=0;QPointF scene;QPoint screen,window_point,end_screen,end_window_point;QPointF end_scene,refine_scene;QPoint refine_screen,refine_window_point;QPointer<QWidget> window;
};
View inspectView(const Canvas& c,const DragCoordinates& config) {
    View v;if (!c.valid || !c.view || !c.viewport) return v;
    v.size=c.viewport->size();v.geometry=c.viewport->geometry();v.view_geometry=c.view->geometry();v.dpr=c.viewport->devicePixelRatioF();
    v.transform=c.view->transform();v.viewport_transform=c.view->viewportTransform();
    v.hbar=c.view->horizontalScrollBar();v.vbar=c.view->verticalScrollBar();if (!v.hbar || !v.vbar) return v;
    v.horizontal=v.hbar->value();v.vertical=v.vbar->value();
    const auto finite=[](const QTransform& m) {return std::isfinite(m.m11()) && std::isfinite(m.m12()) && std::isfinite(m.m13())
        && std::isfinite(m.m21()) && std::isfinite(m.m22()) && std::isfinite(m.m23())
        && std::isfinite(m.m31()) && std::isfinite(m.m32()) && std::isfinite(m.m33());};
    if (!c.is3d || !coordinatesValid(config) || v.size!=QSize(config.width,config.height)
            || v.dpr!=config.dpr || !c.view->isEnabled() || !c.viewport->isEnabled() || !c.view->isInteractive()
            || !finite(v.transform) || !finite(v.viewport_transform) || !v.transform.isInvertible() || !v.viewport_transform.isInvertible()) return v;
    const QPointF pixel(config.sx,config.sy),end_pixel(config.ax,config.ay),refine_pixel(config.ex,config.ey);
    if (!std::isfinite(pixel.x()) || !std::isfinite(pixel.y()) || !std::isfinite(end_pixel.x()) || !std::isfinite(end_pixel.y())
            || !std::isfinite(refine_pixel.x()) || !std::isfinite(refine_pixel.y())) return v;
    bool invertible=false;const QTransform inverse=v.viewport_transform.inverted(&invertible);
    if (!invertible || !finite(inverse)) return v;
    v.scene=inverse.map(pixel);v.end_scene=inverse.map(end_pixel);v.refine_scene=inverse.map(refine_pixel);
    if (!std::isfinite(v.scene.x()) || !std::isfinite(v.scene.y()) || !std::isfinite(v.end_scene.x()) || !std::isfinite(v.end_scene.y())
            || !std::isfinite(v.refine_scene.x()) || !std::isfinite(v.refine_scene.y())) return v;
    // Screen positions are honestly integer; the subpixel value exists only in scene-space payloads.
    const QPoint rounded=pixel.toPoint(),end_rounded=end_pixel.toPoint(),refine_rounded=refine_pixel.toPoint();
    v.screen=c.viewport->mapToGlobal(rounded);v.end_screen=c.viewport->mapToGlobal(end_rounded);v.refine_screen=c.viewport->mapToGlobal(refine_rounded);
    v.window=c.viewport->window();if (!v.window) return v;
    v.window_point=c.viewport->mapTo(v.window,rounded);v.end_window_point=c.viewport->mapTo(v.window,end_rounded);v.refine_window_point=c.viewport->mapTo(v.window,refine_rounded);
    const auto close=[](const QPointF& a,const QPointF& b) {return std::abs(a.x()-b.x())<=1e-9 && std::abs(a.y()-b.y())<=1e-9;};
    v.valid=close(v.viewport_transform.map(v.scene),pixel) && close(v.viewport_transform.map(v.end_scene),end_pixel)
        && close(v.viewport_transform.map(v.refine_scene),refine_pixel)
        && c.viewport->mapFromGlobal(v.screen)==rounded && c.viewport->mapFromGlobal(v.end_screen)==end_rounded && c.viewport->mapFromGlobal(v.refine_screen)==refine_rounded
        && c.viewport->rect().contains(rounded) && c.viewport->rect().contains(end_rounded) && c.viewport->rect().contains(refine_rounded);
    return v;
}
bool sameView(const View& a,const View& b) {
    return a.valid && b.valid && a.size==b.size && a.geometry==b.geometry && a.view_geometry==b.view_geometry && a.dpr==b.dpr
        && a.transform==b.transform && a.viewport_transform==b.viewport_transform && a.hbar && a.vbar && a.hbar==b.hbar && a.vbar==b.vbar
        && a.horizontal==b.horizontal && a.vertical==b.vertical && a.scene==b.scene && a.screen==b.screen && a.window && a.window==b.window && a.window_point==b.window_point && a.end_scene==b.end_scene && a.end_screen==b.end_screen && a.end_window_point==b.end_window_point && a.refine_scene==b.refine_scene && a.refine_screen==b.refine_screen && a.refine_window_point==b.refine_window_point;
}
QJsonArray rectValue(const QRect& r) {return {r.x(),r.y(),r.width(),r.height()};}
QJsonArray matrixValue(const QTransform& m) {return {m.m11(),m.m12(),m.m13(),m.m21(),m.m22(),m.m23(),m.m31(),m.m32(),m.m33()};}
QJsonObject viewValue(const View& v) {return {{"verified",v.valid},{"viewport_size",QJsonArray{v.size.width(),v.size.height()}},
    {"DPR",v.dpr},{"viewport_geometry",rectValue(v.geometry)},{"view_geometry",rectValue(v.view_geometry)},
    {"transform",matrixValue(v.transform)},{"viewport_transform",matrixValue(v.viewport_transform)},
    {"horizontal_scroll",v.horizontal},{"vertical_scroll",v.vertical},{"scene_point",QJsonArray{v.scene.x(),v.scene.y()}},
    {"screen_point",QJsonArray{v.screen.x(),v.screen.y()}},{"window_point",QJsonArray{v.window_point.x(),v.window_point.y()}},{"end_scene_point",QJsonArray{v.end_scene.x(),v.end_scene.y()}},{"end_screen_point",QJsonArray{v.end_screen.x(),v.end_screen.y()}},{"end_window_point",QJsonArray{v.end_window_point.x(),v.end_window_point.y()}},{"refine_scene_point",QJsonArray{v.refine_scene.x(),v.refine_scene.y()}},{"refine_screen_point",QJsonArray{v.refine_screen.x(),v.refine_screen.y()}},{"refine_window_point",QJsonArray{v.refine_window_point.x(),v.refine_window_point.y()}}};}
struct Physical {
    HWND foreground=nullptr,focus=nullptr,capture=nullptr;DWORD foreground_pid=0;bool foreground_known=false;
    QPointer<QWidget> mouse,keyboard,modal,popup;QPointer<QWindow> gui_modal;
    bool valid=false,minimized=false,os_minimized=false,inactive=false;QRect root_geometry,owner_geometry;
    Qt::KeyboardModifiers cached_modifiers=Qt::NoModifier,queried_modifiers=Qt::NoModifier;Qt::MouseButtons buttons=Qt::NoButton;
    POINT cursor{};bool cursor_valid=false;
};
Physical inspectPhysical(const Scope& s,HWND hwnd,std::uint32_t pid,std::uint64_t creation) {
    Physical p;p.foreground=GetForegroundWindow();p.focus=GetFocus();p.capture=GetCapture();
    p.foreground_known=p.foreground && GetWindowThreadProcessId(p.foreground,&p.foreground_pid) && p.foreground_pid!=0;
    p.mouse=QWidget::mouseGrabber();p.keyboard=QWidget::keyboardGrabber();p.modal=QApplication::activeModalWidget();
    p.popup=QApplication::activePopupWidget();p.gui_modal=QGuiApplication::modalWindow();
    p.cached_modifiers=QGuiApplication::keyboardModifiers();p.queried_modifiers=QGuiApplication::queryKeyboardModifiers();p.buttons=QGuiApplication::mouseButtons();
    p.cursor_valid=GetCursorPos(&p.cursor)!=FALSE;
    DWORD owner_pid=0;p.valid=processMatches(pid,creation) && IsWindow(hwnd) && GetWindowThreadProcessId(hwnd,&owner_pid) && owner_pid==pid && s.root && s.owner;
    if (p.valid) {p.minimized=s.root->isMinimized();p.os_minimized=IsIconic(hwnd)!=FALSE;p.inactive=!s.root->isActiveWindow();
        p.root_geometry=s.root->geometry();p.owner_geometry=s.owner->geometry();}
    return p;
}
bool physicalSafe(const Physical& p,std::uint32_t pid) {
    return p.valid && p.minimized && p.os_minimized && p.inactive && p.foreground_known && p.foreground_pid!=pid
        && !p.focus && !p.capture && !p.mouse && !p.keyboard && !p.modal && !p.popup && !p.gui_modal
        && p.cached_modifiers==Qt::NoModifier && p.queried_modifiers==Qt::NoModifier && p.buttons==Qt::NoButton;
}
bool samePhysical(const Physical& a,const Physical& b) {
    return a.valid && b.valid && a.foreground==b.foreground && a.focus==b.focus && a.capture==b.capture
        && a.mouse==b.mouse && a.keyboard==b.keyboard && a.root_geometry==b.root_geometry && a.owner_geometry==b.owner_geometry
        && a.minimized==b.minimized && a.os_minimized==b.os_minimized && a.inactive==b.inactive
        && a.modal==b.modal && a.popup==b.popup && a.gui_modal==b.gui_modal;
}
QJsonObject physicalValue(const Physical& p,std::uint32_t pid) {return {{"verified",physicalSafe(p,pid)},
    {"background_inactive_minimized",p.minimized && p.os_minimized && p.inactive},{"foreground_owner_known",p.foreground_known},
    {"foreground_is_not_MD",p.foreground_known && p.foreground_pid!=pid},{"OS_thread_focus_absent",p.focus==nullptr},
    {"OS_capture_absent",p.capture==nullptr},{"Qt_mouse_grabber_absent",p.mouse.isNull()},{"Qt_keyboard_grabber_absent",p.keyboard.isNull()},
    {"no_modal_or_popup",!p.modal && !p.popup && !p.gui_modal},{"cached_keyboard_modifiers",static_cast<int>(p.cached_modifiers)},
    {"queried_keyboard_modifiers",static_cast<int>(p.queried_modifiers)},{"cached_mouse_buttons",static_cast<int>(p.buttons)}};}
struct Events {
    bool hover_sent=false,hover_returned=false,press_sent=false,press_returned=false,move_sent=false,move_returned=false,release_sent=false,release_returned=false;
    bool hover_send_return=false,press_send_return=false,move_send_return=false,release_send_return=false,hover_accepted=false,press_accepted=false,move_accepted=false,release_accepted=false;
    bool refine_sent=false,refine_returned=false,refine_send_return=false,refine_accepted=false;
    bool release_scope=false,nonspontaneous=true,exception=false,sticky=false;int calls=0;
    bool viewport_sampled=false;double viewport_DPR=0;int viewport_width=0,viewport_height=0;
    bool drag_distance_sampled=false;int drag_distance=-1;bool threshold_gate=false;
};
class ReleaseGuard {
public:
    ReleaseGuard(const Canvas& c,const Scope& s,QCoreApplication* app,WId hwnd,QEvent& event_start,QEvent& event_end,QEvent& event_refine,Events& e)
        :canvas_(c),scope_(s),app_(app),hwnd_(hwnd),event_start_(event_start),event_end_(event_end),event_refine_(event_refine),events_(e) {}
    ~ReleaseGuard() noexcept {release();}
    void release() noexcept {
        if (finished_ || !events_.press_sent || events_.release_sent) return;
        finished_=true; // A skipped unsafe original receiver is not retried.
        try {
            events_.release_scope=app_ && receiverSafe(canvas_,scope_,app_,hwnd_);
            if (!events_.release_scope) {events_.sticky=true;return;}
            QEvent& event_=events_.refine_sent ? event_refine_ : events_.move_sent ? event_end_ : event_start_;
            events_.nonspontaneous=events_.nonspontaneous && !event_.spontaneous();events_.release_sent=true;++events_.calls;
            QObject* receiver=canvas_.scene.data();
            events_.release_send_return=QCoreApplication::sendEvent(receiver,&event_);events_.release_returned=true;events_.release_accepted=event_.isAccepted();
            events_.nonspontaneous=events_.nonspontaneous && !event_.spontaneous();
        } catch (...) {events_.exception=true;events_.sticky=true;}
    }
private:
    const Canvas& canvas_;const Scope& scope_;QPointer<QCoreApplication> app_;WId hwnd_;QEvent& event_start_;QEvent& event_end_;QEvent& event_refine_;Events& events_;bool finished_=false;
};
int writeReport(QJsonObject report,int code,char* output,std::uint32_t capacity,std::uint32_t* written,std::uint32_t* needed) {
    const QByteArray bytes=QJsonDocument(report).toJson(QJsonDocument::Compact);*needed=static_cast<std::uint32_t>(bytes.size()+1);
    if (bytes.size()+1>capacity) return -7;
    std::memcpy(output,bytes.constData(),static_cast<std::size_t>(bytes.size()));output[bytes.size()]='\0';*written=static_cast<std::uint32_t>(bytes.size());return code;
}
QJsonObject baseReport(const char* schema) {return {{"schema",schema},{"selection_hit_verified",false},{"single_vertex_selection_verified",false},
    {"vertex_XYZ_write_verified",false},{"native_handler_side_effects_certified",false},{"complete_private_camera_preservation_verified",false},
    {"physical_OS_input_calls",0},{"OS_cursor_setter_calls",0},{"focus_calls",0},{"activation_calls",0},{"global_input_cache_setter_calls",0},
    {"event_pump_calls",0},{"direct_model_setter_calls",0},{"preference_setter_calls",0},{"GL_function_calls",0},
    {"QObject_property_calls",0},{"QMetaObject_invoke_calls",0},{"direct_graphics_item_enumeration_calls",0},
    {"native_faults_or_hangs_contained",false},{"hard_native_callback_timeout",false},{"raw_Qt_pointers_returned",false}};}
int fallback(const char* schema,int code,const Events& e,double required_dpr,char* output,std::uint32_t cap,std::uint32_t* written,std::uint32_t* needed) noexcept {
    const auto b=[](bool value){return value ? "true" : "false";};
    const bool attempted=e.hover_sent || e.press_sent || e.release_sent;
    const int count=std::snprintf(output,cap,"{\"schema\":\"%s\",\"return_code\":%d,\"status\":\"REJECTED_OR_INCOMPLETE\","
        "\"hover_sent\":%s,\"hover_returned\":%s,\"press_sent\":%s,\"press_returned\":%s,\"release_sent\":%s,\"release_returned\":%s,"
        "\"move_sent\":%s,\"move_returned\":%s,\"refine_sent\":%s,\"refine_returned\":%s,\"direct_synthetic_event_calls\":%d,\"sticky_unsafe_observed\":%s,\"gesture_not_started\":%s,\"requires_recovery\":%s,"
        "\"viewport_DPR_sampled\":%s,\"viewport_DPR_before\":%.17g,\"required_DPR\":%.17g,\"viewport_size_before\":[%d,%d],"
        "\"public_drag_distance_sampled\":%s,\"start_drag_distance\":%d,\"public_drag_threshold_gate_verified\":%s,"
        "\"selection_hit_verified\":false,\"vertex_XYZ_write_verified\":false}",schema,code,b(e.hover_sent),b(e.hover_returned),b(e.press_sent),b(e.press_returned),
        b(e.release_sent),b(e.release_returned),b(e.move_sent),b(e.move_returned),b(e.refine_sent),b(e.refine_returned),e.calls,b(e.sticky),b(!attempted),b(attempted),
        b(e.viewport_sampled),std::isfinite(e.viewport_DPR) ? e.viewport_DPR : 0.,std::isfinite(required_dpr) ? required_dpr : 0.,e.viewport_width,e.viewport_height,b(e.drag_distance_sampled),e.drag_distance,b(e.threshold_gate));
    *written=count>0 && static_cast<std::uint32_t>(count)<cap ? static_cast<std::uint32_t>(count) : 0;
    *needed=count>0 ? static_cast<std::uint32_t>(count+1) : 0;return code;
}
bool entry(std::uint32_t pid,std::uint64_t creation,std::uintptr_t window,std::uint32_t budget,QPointer<QCoreApplication>& app) {
    DWORD owner=0;const HWND hwnd=reinterpret_cast<HWND>(window);
    if (!window || budget<50 || budget>1500 || !processMatches(pid,creation) || !IsWindow(hwnd) || !GetWindowThreadProcessId(hwnd,&owner) || owner!=pid) return false;
    wchar_t executable[32768]{};
    const DWORD count=GetModuleFileNameW(nullptr,executable,32768);
    if (!count || count>=32768) return false;
    const wchar_t* separator=std::wcsrchr(executable,L'\\');
    if (!separator) return false;
    const std::wstring directory(executable,static_cast<std::size_t>(separator-executable+1));
    for (const wchar_t* name:{L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Widgets.dll",L"Qt6OpenGL.dll",L"Qt6OpenGLWidgets.dll"}) {
        if (!moduleMatches(name,directory)) return false;
    }
    app=QCoreApplication::instance();return app && !std::strcmp(qVersion(),"6.10.3") && QThread::currentThread()==app->thread();
}
int runSceneDrag(std::uint32_t pid,std::uint64_t creation,std::uintptr_t window,const DragCoordinates& config,std::uint32_t budget,
        char* output,std::uint32_t capacity,std::uint32_t* written,std::uint32_t* needed) noexcept {
    if (written) *written=0;if (needed) *needed=0;Events events;constexpr bool is3d=true;const char* schema="md-qt-mesh-vertex-3d-bounded-scene-drag-v1";
    if (!output || capacity<4096 || capacity>1024*1024 || !written || !needed) return -1;
    const auto reject=[&](int code){return fallback(schema,code,events,config.dpr,output,capacity,written,needed);};
    if (!coordinatesValid(config)) return reject(-30); // Before Qt/process access and before every event.
    try {
        QPointer<QCoreApplication> app;if (!entry(pid,creation,window,budget,app)) return reject(-2);
        const ULONGLONG began=GetTickCount64();const WId wid=static_cast<WId>(window);const HWND hwnd=reinterpret_cast<HWND>(window);
        const Scope before=inspectScope(wid,app->thread(),began,budget);const Canvas canvas=inspectCanvas(before,app->thread(),is3d,began,budget);
        const View view=inspectView(canvas,config);const Physical physical=inspectPhysical(before,hwnd,pid,creation);
        events.viewport_sampled=canvas.valid && canvas.viewport;events.viewport_DPR=view.dpr;
        events.viewport_width=view.size.width();events.viewport_height=view.size.height();
        const int drag_distance=QApplication::startDragDistance();const bool drag_threshold=drag_distance>=0 && drag_distance<=10 && config.sx-config.ax>drag_distance;
        events.drag_distance_sampled=true;events.drag_distance=drag_distance;events.threshold_gate=drag_threshold;
        QJsonObject observations;
        const auto observe=[&](const char* stage,bool require_no_scene_grab) {
            if (!app || QCoreApplication::instance()!=app) {events.sticky=true;return false;}
            const Scope current=inspectScope(wid,app->thread(),GetTickCount64(),budget);
            const Canvas current_canvas=inspectCanvas(current,app->thread(),is3d,GetTickCount64(),budget);
            const View current_view=inspectView(current_canvas,config);const Physical current_physical=inspectPhysical(current,hwnd,pid,creation);
            const bool receiver=receiverSafe(canvas,before,app,wid) && sameCanvas(canvas,current_canvas);
            const bool scope=sameScope(before,current) && current.box && current.world_checked;
            const bool physical_safe=physicalSafe(current_physical,pid) && samePhysical(physical,current_physical);
            const bool view_same=sameView(view,current_view);
            const bool scene_absent=current_canvas.scene && current_canvas.scene->mouseGrabberItem()==nullptr;
            const bool drag_same=QApplication::startDragDistance()==drag_distance;
            const bool complete=receiver && scope && physical_safe && view_same && drag_threshold && drag_same && (!require_no_scene_grab || scene_absent);
            observations.insert(QString::fromLatin1(stage),QJsonObject{{"verified",complete},{"scope_refs_same",scope},{"receiver_refs_same",receiver},
                {"physical_safe",physical_safe},{"physical",physicalValue(current_physical,pid)},{"view_unchanged",view_same},
                {"scene_item_grabber_absent",scene_absent},{"drag_distance_unchanged",drag_same}});
            if (!complete) events.sticky=true;return complete;
        };
        const bool preflight=before.valid && before.box && before.world_checked && canvas.valid && receiverSafe(canvas,before,app,wid)
            && view.valid && drag_threshold && physicalSafe(physical,pid) && canvas.scene && canvas.scene->mouseGrabberItem()==nullptr;
        if (!preflight || !observe("before",true) || GetTickCount64()-began>=budget) return reject(-10);
        const auto prepare=[&](QGraphicsSceneMouseEvent& event,const QPointF& position,const QPoint& screen,
                const QPointF& last,const QPoint& last_screen,Qt::MouseButton button,Qt::MouseButtons buttons) {
            event.setWidget(canvas.viewport);event.setScenePos(position);event.setScreenPos(screen);
            event.setLastScenePos(last);event.setLastScreenPos(last_screen);
            event.setButtonDownScenePos(Qt::LeftButton,view.scene);event.setButtonDownScreenPos(Qt::LeftButton,view.screen);
            event.setButton(button);event.setButtons(buttons);event.setModifiers(Qt::NoModifier);
            event.setSource(Qt::MouseEventSynthesizedByApplication);event.setFlags(Qt::MouseEventFlags());event.setAccepted(false);
        };
        QGraphicsSceneMouseEvent hover(QEvent::GraphicsSceneMouseMove),mouse_press(QEvent::GraphicsSceneMousePress),
            mouse_move(QEvent::GraphicsSceneMouseMove),mouse_refine(QEvent::GraphicsSceneMouseMove),
            mouse_release_start(QEvent::GraphicsSceneMouseRelease),mouse_release_end(QEvent::GraphicsSceneMouseRelease),mouse_release_refine(QEvent::GraphicsSceneMouseRelease);
        prepare(hover,view.scene,view.screen,view.scene,view.screen,Qt::NoButton,Qt::NoButton);
        prepare(mouse_press,view.scene,view.screen,view.scene,view.screen,Qt::LeftButton,Qt::LeftButton);
        prepare(mouse_move,view.end_scene,view.end_screen,view.scene,view.screen,Qt::NoButton,Qt::LeftButton);
        prepare(mouse_refine,view.refine_scene,view.refine_screen,view.end_scene,view.end_screen,Qt::NoButton,Qt::LeftButton);
        prepare(mouse_release_start,view.scene,view.screen,view.scene,view.screen,Qt::LeftButton,Qt::NoButton);
        prepare(mouse_release_end,view.end_scene,view.end_screen,view.end_scene,view.end_screen,Qt::LeftButton,Qt::NoButton);
        prepare(mouse_release_refine,view.refine_scene,view.refine_screen,view.refine_scene,view.refine_screen,Qt::LeftButton,Qt::NoButton);
        const auto payload=[&](const QGraphicsSceneMouseEvent& event) {
            const QPointF pos=event.scenePos(),last=event.lastScenePos(),down=event.buttonDownScenePos(Qt::LeftButton);
            const QPoint screen=event.screenPos(),last_screen=event.lastScreenPos(),down_screen=event.buttonDownScreenPos(Qt::LeftButton);
            return QJsonObject{{"event_type",static_cast<int>(event.type())},{"scene_position",QJsonArray{pos.x(),pos.y()}},
                {"last_scene_position",QJsonArray{last.x(),last.y()}},{"button_down_scene_position",QJsonArray{down.x(),down.y()}},
                {"screen_position",QJsonArray{screen.x(),screen.y()}},{"last_screen_position",QJsonArray{last_screen.x(),last_screen.y()}},
                {"button_down_screen_position",QJsonArray{down_screen.x(),down_screen.y()}},{"button",static_cast<int>(event.button())},
                {"buttons",static_cast<int>(event.buttons())},{"modifiers",static_cast<int>(event.modifiers())},
                {"viewport_widget_verified",event.widget()==canvas.viewport},{"nonspontaneous",!event.spontaneous()}};
        };
        const QJsonArray prepared_payloads{payload(hover),payload(mouse_press),payload(mouse_move),payload(mouse_refine),payload(mouse_release_refine)};
        QEvent& press=mouse_press;QEvent& release=mouse_release_refine;
        ReleaseGuard cleanup(canvas,before,app,wid,mouse_release_start,mouse_release_end,mouse_release_refine,events);
        QObject* receiver=canvas.scene.data();
        {
            events.nonspontaneous=!hover.spontaneous();events.hover_sent=true;++events.calls;
            try {events.hover_send_return=QCoreApplication::sendEvent(receiver,&hover);events.hover_returned=true;events.hover_accepted=hover.isAccepted();
                events.nonspontaneous=events.nonspontaneous && !hover.spontaneous();}
            catch (...) {events.exception=true;events.sticky=true;}
            if (!observe("after_hover",true) || events.sticky || GetTickCount64()-began>=budget) {
                events.sticky=true;
            }
        }
        if (!events.sticky && observe("before_press",true) && GetTickCount64()-began<budget) {
            events.nonspontaneous=events.nonspontaneous && !press.spontaneous() && !release.spontaneous();
            if (events.nonspontaneous) {
                events.press_sent=true;++events.calls;
                try {events.press_send_return=QCoreApplication::sendEvent(receiver,&press);events.press_returned=true;events.press_accepted=press.isAccepted();
                    events.nonspontaneous=events.nonspontaneous && !press.spontaneous();}
                catch (...) {events.exception=true;events.sticky=true;}
                observe("after_press",false);
                if (!events.sticky && events.press_returned && !events.exception && GetTickCount64()-began<budget) {
                    events.move_sent=true;++events.calls;
                    try {events.move_send_return=QCoreApplication::sendEvent(receiver,&mouse_move);events.move_returned=true;events.move_accepted=mouse_move.isAccepted();
                        events.nonspontaneous=events.nonspontaneous && !mouse_move.spontaneous();}
                    catch (...) {events.exception=true;events.sticky=true;}
                    observe("after_move",false);
                    if (!events.sticky && events.move_returned && !events.exception && GetTickCount64()-began<budget) {
                        events.refine_sent=true;++events.calls;
                        try {events.refine_send_return=QCoreApplication::sendEvent(receiver,&mouse_refine);events.refine_returned=true;events.refine_accepted=mouse_refine.isAccepted();
                            events.nonspontaneous=events.nonspontaneous && !mouse_refine.spontaneous();}
                        catch (...) {events.exception=true;events.sticky=true;}
                        observe("after_refine",false);
                    }
                }
                cleanup.release(); // Always original receiver; no retry, including unsafe/budget cases.
                observe("after_release",true);
            } else events.sticky=true;
        }
        cleanup.release();
        if (!app || QCoreApplication::instance()!=app) return reject(-21);
        const bool final_observed=observe("final",true);const Scope after=inspectScope(wid,app->thread(),GetTickCount64(),budget);
        const Physical final_physical=inspectPhysical(after,hwnd,pid,creation);
        const bool paired=events.press_sent && events.press_returned && events.release_sent && events.release_returned && events.release_scope;
        const bool all_events=paired && events.calls==5 && events.hover_sent && events.hover_returned && events.move_sent && events.move_returned && events.refine_sent && events.refine_returned;
        const bool completed=all_events && events.nonspontaneous && !events.exception && !events.sticky && final_observed;
        if (!completed || GetTickCount64()-began>=budget) events.sticky=true;
        const bool cursor_unchanged=physical.cursor_valid && final_physical.cursor_valid && physical.cursor.x==final_physical.cursor.x && physical.cursor.y==final_physical.cursor.y;
        const bool success=completed && !events.sticky;
        const int code=success ? 0 : -21;QJsonObject report=baseReport(schema);
        report.insert("status",success ? "MESH_VERTEX_3D_BOUNDED_SCENE_DRAG_EVENTS_DISPATCHED_EFFECT_UNVERIFIED" : "MESH_VERTEX_3D_BOUNDED_SCENE_DRAG_EVENTS_UNSAFE_OR_INCOMPLETE");
        report.insert("return_code",code);report.insert("receiver","QGraphicsScene/QtView3D");
        report.insert("scope_before",scopeValue(before));report.insert("scope_after",scopeValue(after));report.insert("stages",observations);report.insert("view_before",viewValue(view));
        report.insert("hover_sent",events.hover_sent);report.insert("hover_returned",events.hover_returned);report.insert("hover_accepted",events.hover_accepted);report.insert("hover_send_return",events.hover_send_return);
        report.insert("press_sent",events.press_sent);report.insert("press_returned",events.press_returned);report.insert("press_accepted",events.press_accepted);report.insert("press_send_return",events.press_send_return);
        report.insert("release_sent",events.release_sent);report.insert("release_returned",events.release_returned);report.insert("release_accepted",events.release_accepted);report.insert("release_send_return",events.release_send_return);
        report.insert("release_receiver_scope_verified",events.release_scope);report.insert("paired_release_verified",paired);report.insert("dispatch_sequence_completed",success);
        report.insert("all_events_nonspontaneous",events.nonspontaneous);report.insert("cpp_exception",events.exception);report.insert("sticky_unsafe_observed",events.sticky);
        report.insert("direct_synthetic_event_calls",events.calls);report.insert("viewport_mouse_sendEvent_calls",0);report.insert("scene_sendEvent_calls",events.calls);report.insert("key_sendEvent_calls",0);
        report.insert("QAction_trigger_calls",0);report.insert("event_modifiers","NoModifier");report.insert("event_local_NoModifier_only",true);report.insert("physical_cursor_unchanged",cursor_unchanged);
        report.insert("gesture_not_started",!events.hover_sent && !events.press_sent);report.insert("gesture_cleanup_verified",success);report.insert("requires_recovery",!success);
        report.insert("parameters_validated",coordinatesValid(config));report.insert("parameters_received_count",6);
        report.insert("supplied_viewport_coordinates",QJsonArray{config.sx,config.sy,config.ax,config.ay,config.ex,config.ey});
        report.insert("native_source_image_binding_verified",false);report.insert("native_plan_binding_verified",false);
        report.insert("selected_vertex_hit_verified",false);report.insert("world_axis_or_delta_verified",false);
        report.insert("supplied_drag_start",QJsonArray{config.sx,config.sy});report.insert("supplied_activation_position",QJsonArray{config.ax,config.ay});report.insert("supplied_drag_end",QJsonArray{config.ex,config.ey});
        report.insert("viewport_DPR_sampled",events.viewport_sampled);report.insert("viewport_DPR_before",view.dpr);report.insert("required_DPR",config.dpr);
        report.insert("viewport_size_before",QJsonArray{view.size.width(),view.size.height()});
        report.insert("public_drag_distance_sampled",events.drag_distance_sampled);
        report.insert("public_drag_threshold_gate_verified",drag_threshold);
        report.insert("configured_displacement_exceeds_public_drag_threshold",12>drag_distance);
        report.insert("configured_activation_pixel_delta_x",config.ax-config.sx);
        report.insert("configured_final_pixel_delta_x",config.ex-config.sx);
        report.insert("configured_final_pixel_delta_y",config.ey-config.sy);
        
        report.insert("drag_threshold_exceeded_claimed",false);report.insert("start_drag_distance",drag_distance);report.insert("public_drag_distance_observed",drag_threshold);
        report.insert("move_sent",events.move_sent);report.insert("move_returned",events.move_returned);report.insert("move_accepted",events.move_accepted);report.insert("move_send_return",events.move_send_return);
        report.insert("refine_sent",events.refine_sent);report.insert("refine_returned",events.refine_returned);report.insert("refine_accepted",events.refine_accepted);report.insert("refine_send_return",events.refine_send_return);
        report.insert("activation_then_refine_completed",success);
        report.insert("release_position",events.refine_sent ? QJsonArray{config.ex,config.ey} : events.move_sent ? QJsonArray{config.ax,config.ay} : QJsonArray{config.sx,config.sy});
        report.insert("expected_button_down_position",QJsonArray{config.sx,config.sy});
        report.insert("expected_move_last_position",QJsonArray{config.sx,config.sy});report.insert("expected_refine_last_position",QJsonArray{config.ax,config.ay});
        report.insert("expected_release_last_position",events.refine_sent ? QJsonArray{config.ex,config.ey} : events.move_sent ? QJsonArray{config.ax,config.ay} : QJsonArray{config.sx,config.sy});
        report.insert("QMouseEvent_explicit_last_or_buttonDown_fields",false);
        report.insert("scene_coordinate_public_setters_used",true);report.insert("item_local_coordinates_left_to_Qt",true);
        report.insert("screen_positions_integer",true);report.insert("viewport_to_scene_QPointF_unrounded",true);
        report.insert("QGraphicsView_mouse_cache_setter_calls",0);report.insert("MD_scene_coordinate_consumption_verified",false);
        report.insert("world_delta_verified",false);report.insert("prepared_event_payloads",prepared_payloads);report.insert("QGraphicsView_internal_bookkeeping_certified",false);
        report.insert("event_types",QJsonArray{"GraphicsSceneMouseMove","GraphicsSceneMousePress","GraphicsSceneMouseMove","GraphicsSceneMouseMove","GraphicsSceneMouseRelease"});
        report.insert("key_enum",0);report.insert("key_auto_repeat",false);report.insert("Qt_widget_mouse_propagation_possible",false);
        report.insert("delivery","QCoreApplication::sendEvent(QGraphicsScene QtView3D, QGraphicsSceneMouseEvent)");
        report.insert("counter_scope","Only direct controller event sends; nested Qt filters/handlers and MD effects are not enumerated or certified");
        report.insert("elapsed_ms",static_cast<qint64>(GetTickCount64()-began));
        const int written_code=writeReport(report,code,output,capacity,written,needed);
        if (written_code==-7) {const auto required=*needed;const int result=reject(-7);*needed=required;return result;}return code;
    } catch (...) {events.sticky=true;events.exception=true;return reject(-8);}
}
} // namespace

extern "C" __declspec(dllexport)
int __cdecl mdqt_cloth_vertex_drag_abi_version() noexcept {return 2;}

extern "C" __declspec(dllexport)
int __cdecl mdqt_cloth_vertex_drag(std::uint32_t pid,std::uint64_t creation,std::uintptr_t hwnd,
        double start_x,double start_y,double activation_x,double activation_y,double end_x,double end_y,
        int width,int height,double dpr,std::uint32_t budget,
        char* output,std::uint32_t capacity,std::uint32_t* written,std::uint32_t* needed) noexcept {
    const DragCoordinates config{start_x,start_y,activation_x,activation_y,end_x,end_y,width,height,dpr};
    return runSceneDrag(pid,creation,hwnd,config,budget,output,capacity,written,needed);
}
