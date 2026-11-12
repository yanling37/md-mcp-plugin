// Public Qt ABI only. Loaded by trusted embedded Python in MD, never injected.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <QtCore/QCoreApplication>
#include <QtCore/QCryptographicHash>
#include <QtCore/QDir>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtCore/QUuid>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtGui/QGuiApplication>
#include <QtGui/QWindow>
#include <QtWidgets/QApplication>
#include <QtWidgets/QGraphicsItem>
#include <QtWidgets/QGraphicsScene>
#include <QtWidgets/QGraphicsSceneMouseEvent>
#include <QtWidgets/QGraphicsView>
#include <QtWidgets/QScrollBar>
#include <QtWidgets/QWidget>
#include <QtOpenGLWidgets/QOpenGLWidget>

#if QT_VERSION != QT_VERSION_CHECK(6,10,3) || !defined(_WIN64) || !defined(_MSC_VER) || defined(QT_DEBUG)
#error Release Qt 6.10.3, MSVC and x64 are required.
#endif

#include <algorithm>
#include <QtCore/QFile>
#include <QtCore/QMetaMethod>
#include <QtCore/QMetaProperty>
#include <QtCore/QVariant>
#include <QtGui/QImage>
#include <QtGui/QOpenGLContext>

#include <QtGui/QKeyEvent>

namespace {
constexpr unsigned output_capacity = 262144, max_points = 32, max_objects = 32768;
constexpr const char* schema = "md-qt-world-adapter/1";
bool running = false, recovery_required = false; // GUI thread only, no outstanding gesture state.
struct Reject { const char* code; };
void require(bool condition, const char* code) { if (!condition) throw Reject{code}; }
QString hash(const QJsonObject& value) {
    return QString::fromLatin1(QCryptographicHash::hash(QJsonDocument(value).toJson(QJsonDocument::Compact),
        QCryptographicHash::Sha256).toHex());
}
QJsonArray xy(QPointF p) { return {p.x(), p.y()}; }
QJsonArray xy(QPoint p) { return {p.x(), p.y()}; }
QJsonArray rect(QRect r) { return {r.x(), r.y(), r.width(), r.height()}; }
QJsonArray rect(QRectF r) { return {r.x(), r.y(), r.width(), r.height()}; }
QJsonArray matrix(const QTransform& t) {
    return {t.m11(),t.m12(),t.m13(),t.m21(),t.m22(),t.m23(),t.m31(),t.m32(),t.m33()};
}
bool finite(const QTransform& t) {
    for (const auto& v : matrix(t)) if (!std::isfinite(v.toDouble())) return false;
    return true;
}
bool processMatches(std::uint32_t pid, std::uint64_t creation) {
    FILETIME born{}, ended{}, kernel{}, user{};
    return pid == GetCurrentProcessId() && GetProcessTimes(GetCurrentProcess(), &born, &ended, &kernel, &user)
        && ((static_cast<std::uint64_t>(born.dwHighDateTime) << 32) | born.dwLowDateTime) == creation;
}
QString modulePath(HMODULE module) {
    wchar_t buffer[32768]{};
    const DWORD count = GetModuleFileNameW(module, buffer, 32768);
    require(count && count < 32768, "MODULE_PATH_UNAVAILABLE");
    return QDir::cleanPath(QString::fromWCharArray(buffer, static_cast<int>(count)));
}
QJsonObject modules() {
    const QString executable = modulePath(nullptr);
    const QString directory = QFileInfo(executable).absolutePath();
    QJsonObject result{{"executable", executable}, {"qt_version", qVersion()}};
    for (const wchar_t* name : {L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Widgets.dll",L"Qt6OpenGL.dll",L"Qt6OpenGLWidgets.dll"}) {
        HMODULE module = GetModuleHandleW(name);
        require(module != nullptr, "QT_MODULE_NOT_LOADED");
        const QString path = modulePath(module);
        require(QFileInfo(path).absolutePath().compare(directory, Qt::CaseInsensitive) == 0, "QT_MODULE_DIRECTORY_MISMATCH");
        result.insert(QString::fromWCharArray(name), path);
    }
    require(std::strcmp(qVersion(), "6.10.3") == 0, "QT_VERSION_MISMATCH");
    return result;
}
bool exact(QObject* o, const char* type, const char* name) {
    return o && !std::strcmp(o->metaObject()->className(), type) && o->objectName() == QLatin1StringView(name);
}
QJsonObject description(QObject* o) {
    require(o && o->objectName().size() <= 256, "SELECTOR_TEXT_TOO_LONG");
    const QString type = QString::fromLatin1(o->metaObject()->className());
    require(type.size() <= 256, "SELECTOR_TEXT_TOO_LONG");
    return {{"class_name", type}, {"object_name", o->objectName()}};
}
struct Budget {
    ULONGLONG began = GetTickCount64();
    unsigned milliseconds;
    explicit Budget(unsigned value) : milliseconds(value) {}
    bool available() const { return GetTickCount64() - began < milliseconds; }
    void check() const { require(available(), "COOPERATIVE_BUDGET_EXCEEDED"); }
};
QObject* uniqueChild(QObject* parent, const char* type, const char* name, Budget& budget, QThread* thread) {
    require(parent && parent->thread() == thread && parent->children().size() <= 5000, "OWNER_SCOPE_INVALID");
    QObject* match = nullptr;
    int count = 0;
    for (QObject* child : parent->children()) {
        budget.check();
        require(child && child->thread() == thread, "OWNER_THREAD_MISMATCH");
        if (exact(child, type, name)) { match = child; ++count; }
    }
    require(count == 1, "OWNER_SCOPE_AMBIGUOUS");
    return match;
}
struct Scope {
    QPointer<QApplication> app;
    QPointer<QWidget> root, owner;
    std::vector<QPointer<QObject>> chain;
    HWND hwnd = nullptr;
};
Scope currentScope(Budget& budget, std::uint32_t pid) {
    Scope s;
    s.app = qobject_cast<QApplication*>(QCoreApplication::instance());
    require(s.app && QThread::currentThread() == s.app->thread(), "GUI_THREAD_REQUIRED");
    const auto roots = QApplication::topLevelWidgets();
    require(roots.size() <= 1024, "TOP_LEVEL_LIMIT");
    int matches = 0;
    for (QWidget* root : roots) {
        budget.check();
        if (exact(root, "TitleFrame", "TitleFrame")) { ++matches; s.root = root; }
    }
    require(matches == 1 && s.root && s.root->thread() == s.app->thread(), "MD_WINDOW_AMBIGUOUS");
    s.hwnd = reinterpret_cast<HWND>(s.root->effectiveWinId()); // Does not create a native window.
    DWORD owner_pid = 0;
    require(s.hwnd && IsWindow(s.hwnd) && GetWindowThreadProcessId(s.hwnd, &owner_pid) && owner_pid == pid
        && QWidget::find(reinterpret_cast<WId>(s.hwnd)) == s.root, "MD_WINDOW_OWNER_MISMATCH");
    QObject* current = s.root;
    s.chain.push_back(current);
    for (int i = 0; i < 3; ++i) {
        current = uniqueChild(current, i == 2 ? "MDMainWindow" : "CloUICommon::Frame", i == 2 ? "CLO" : "", budget, s.app->thread());
        s.chain.push_back(current);
    }
    s.owner = qobject_cast<QWidget*>(current);
    require(s.owner, "MD_OWNER_NOT_WIDGET");
    return s;
}
bool scopeAlive(const Scope& s) {
    if (!s.app || !s.root || !s.owner || QCoreApplication::instance() != s.app
        || QThread::currentThread() != s.app->thread() || s.chain.size() != 4 || !IsWindow(s.hwnd)
        || QWidget::find(reinterpret_cast<WId>(s.hwnd)) != s.root) return false;
    DWORD pid = 0;
    if (!GetWindowThreadProcessId(s.hwnd, &pid) || pid != GetCurrentProcessId()) return false;
    for (std::size_t i = 0; i < s.chain.size(); ++i) {
        QObject* o = s.chain[i];
        if (!o || o->thread() != s.app->thread() || (i && o->parent() != s.chain[i - 1])) return false;
        if (!exact(o, i == 0 ? "TitleFrame" : i == 3 ? "MDMainWindow" : "CloUICommon::Frame",
            i == 0 ? "TitleFrame" : i == 3 ? "CLO" : "")) return false;
    }
    return true;
}
struct Canvas {
    QString kind, token;
    QPointer<QWidget> root, owner;
    QPointer<QGraphicsView> view;
    QPointer<QOpenGLWidget> viewport;
    QPointer<QGraphicsScene> scene;
    std::vector<QPointer<QObject>> chain; // owner -> view, independent of dock positions/layout depth.
};
std::vector<Canvas> references;
bool receiverAlive(const Scope& s, const Canvas& c) {
    if (!scopeAlive(s) || c.root != s.root || c.owner != s.owner || !c.view || !c.viewport || !c.scene
        || c.view->viewport() != c.viewport || c.view->scene() != c.scene || c.viewport->parentWidget() != c.view
        || c.scene->parent() != c.viewport || c.chain.empty() || c.chain.front() != s.owner || c.chain.back() != c.view
        || !exact(c.viewport, "QOpenGLWidget", c.kind == "2d" ? "widget2d" : "widget3d")
        || !exact(c.scene, c.kind == "2d" ? "QtView2D" : "QtView3D", "")) return false;
    for (std::size_t i = 0; i < c.chain.size(); ++i)
        if (!c.chain[i] || c.chain[i]->thread() != s.app->thread() || (i && c.chain[i]->parent() != c.chain[i - 1])) return false;
    return c.scene->thread() == s.app->thread() && c.viewport->thread() == s.app->thread();
}
std::vector<Canvas> canvases(const Scope& s, Budget& budget) {
    struct Entry { QPointer<QObject> object; unsigned depth; };
    std::vector<Entry> queue{{s.owner.data(), 0}};
    std::vector<Canvas> result;
    for (std::size_t i = 0; i < queue.size(); ++i) {
        budget.check();
        QObject* o = queue[i].object;
        require(o && o->thread() == s.app->thread(), "CANVAS_TREE_CHANGED");
        if (auto* view = qobject_cast<QGraphicsView*>(o)) {
            auto* viewport = qobject_cast<QOpenGLWidget*>(view->viewport());
            auto* scene = view->scene();
            QString kind;
            if (exact(scene, "QtView2D", "") && exact(viewport, "QOpenGLWidget", "widget2d")) kind = "2d";
            if (exact(scene, "QtView3D", "") && exact(viewport, "QOpenGLWidget", "widget3d")) kind = "3d";
            if (!kind.isEmpty()) {
                Canvas c; c.kind = kind; c.root = s.root; c.owner = s.owner; c.view = view; c.viewport = viewport; c.scene = scene;
                for (QObject* p = view; p && p != s.owner; p = p->parent()) c.chain.insert(c.chain.begin(), p);
                c.chain.insert(c.chain.begin(), s.owner.data());
                require(receiverAlive(s, c), "CANVAS_RECEIVER_INVALID");
                for (const Canvas& old : references)
                    if (old.view && old.view == c.view && old.scene == c.scene && old.viewport == c.viewport
                        && old.root == c.root && old.owner == c.owner) c.token = old.token;
                if (c.token.isEmpty()) c.token = QUuid::createUuid().toString(QUuid::WithoutBraces);
                result.push_back(c);
                require(result.size() <= 8, "CANVAS_COUNT_LIMIT");
            }
        }
        // A QGraphicsView has a QWidget parent chain. Models/actions/layouts
        // cannot contain a view, and need not consume this bounded view search.
        std::vector<QObject*> children;
        for (QObject* child : o->children())
            if (qobject_cast<QWidget*>(child)) children.push_back(child);
        require(queue.size() + children.size() <= max_objects, "CANVAS_TREE_LIMIT");
        require(queue[i].depth < 32 || children.empty(), "CANVAS_DEPTH_LIMIT");
        for (QObject* child : children) queue.push_back({child, queue[i].depth + 1});
    }
    references = result; // Only QObject lifetimes; never an input grab or in-flight press.
    return result;
}
QJsonArray checkedActions(const Scope& s) {
    QJsonArray result;
    require(s.owner && s.owner->children().size() <= 5000, "ACTION_LIMIT");
    for (QObject* o : s.owner->children()) if (auto* action = qobject_cast<QAction*>(o)) {
        if (action->isCheckable() && action->isChecked()) {
            require(result.size() < 256 && action->text().size() <= 256, "ACTION_LIMIT");
            auto value = description(action);
            value.insert("text", action->text()); value.insert("enabled", action->isEnabled());
            result.append(value);
        }
    }
    return result;
}
QJsonObject snapshot(const Scope& s, const Canvas& c) {
    require(receiverAlive(s, c), "STALE_VIEW_REFERENCE");
    const auto t = c.view->transform(), vt = c.view->viewportTransform();
    bool invertible = false; const QTransform inverse = vt.inverted(&invertible);
    require(finite(t) && finite(vt) && finite(inverse) && invertible, "VIEW_MAPPING_INVALID");
    const QSize size = c.viewport->size(); const qreal dpr = c.viewport->devicePixelRatioF();
    require(size.width() > 0 && size.height() > 0 && std::isfinite(dpr) && dpr > 0, "VIEW_SIZE_INVALID");
    require(c.view->horizontalScrollBar() && c.view->verticalScrollBar(), "VIEW_SCROLLBARS_MISSING");
    QJsonArray chain;
    for (const auto& o : c.chain) chain.append(description(o));
    QJsonObject mapping{{"viewport_size", QJsonArray{size.width(), size.height()}}, {"device_pixel_ratio", dpr},
        {"view_geometry", rect(c.view->geometry())}, {"viewport_geometry", rect(c.viewport->geometry())},
        {"view_transform", matrix(t)}, {"viewport_transform", matrix(vt)},
        {"scroll_values", QJsonArray{c.view->horizontalScrollBar()->value(), c.view->verticalScrollBar()->value()}},
        {"global_origin", xy(c.viewport->mapToGlobal(QPoint(0, 0)))}, {"checked_actions", checkedActions(s)},
        {"relative_visible", c.view->isVisibleTo(s.owner) && c.viewport->isVisibleTo(c.view)},
        {"enabled", c.view->isEnabled() && c.viewport->isEnabled() && c.view->isInteractive()},
        {"drag_mode", static_cast<int>(c.view->dragMode())}, {"start_drag_distance", QApplication::startDragDistance()}};
    QJsonObject value{{"view", c.kind}, {"native_view_ref", c.token}, {"mapping", mapping},
        {"selector_chain", chain}, {"viewport", description(c.viewport)}, {"scene", description(c.scene)},
        {"scene_rect", rect(c.scene->sceneRect())}, {"scene_item_grabber_absent", c.scene->mouseGrabberItem() == nullptr},
        {"coordinate_space", "viewport_logical_pixels"}, {"scene_coordinate_units", "Qt scene units; neither mm nor world XYZ"}};
    value.insert("mapping_fingerprint", hash(mapping));
    value.insert("view_fingerprint", hash(value));
    return value;
}
struct Physical {
    HWND foreground = nullptr, focus = nullptr, capture = nullptr, foreground_focus = nullptr, foreground_capture = nullptr;
    DWORD foreground_pid = 0;
    bool foreground_known = false, rect_known = false, foreground_info_known = false;
    RECT os_rect{}; QPoint cursor; bool cursor_known = false;
    bool iconic = false, minimized = false, active = false, visible = false, hidden = false;
    QRect root_rect, owner_rect;
    QPointer<QWidget> mouse, keyboard, logical_focus, modal, popup;
    QWidget* logical_focus_identity = nullptr; // Compare lifetime changes without dereferencing an old pointer.
    QPointer<QWindow> gui_modal;
    Qt::KeyboardModifiers modifiers, queried;
    Qt::MouseButtons buttons;
    bool physical_keys_idle = false;
};
Physical physical(const Scope& s) {
    require(scopeAlive(s), "MD_SCOPE_LOST");
    Physical p;
    p.foreground = GetForegroundWindow(); p.focus = GetFocus(); p.capture = GetCapture();
    const DWORD thread = p.foreground ? GetWindowThreadProcessId(p.foreground, &p.foreground_pid) : 0;
    p.foreground_known = thread && p.foreground_pid;
    GUITHREADINFO info{}; info.cbSize = sizeof(info);
    p.foreground_info_known = thread && GetGUIThreadInfo(thread, &info);
    if (p.foreground_info_known) { p.foreground_focus = info.hwndFocus; p.foreground_capture = info.hwndCapture; }
    p.rect_known = GetWindowRect(s.hwnd, &p.os_rect) != FALSE;
    POINT cursor{}; p.cursor_known = GetCursorPos(&cursor) != FALSE; p.cursor = QPoint(cursor.x, cursor.y);
    p.iconic = IsIconic(s.hwnd) != FALSE; p.minimized = s.root->isMinimized(); p.active = s.root->isActiveWindow();
    p.visible = s.root->isVisible(); p.hidden = s.root->isHidden(); p.root_rect = s.root->geometry(); p.owner_rect = s.owner->geometry();
    p.mouse = QWidget::mouseGrabber(); p.keyboard = QWidget::keyboardGrabber(); p.logical_focus = QApplication::focusWidget();
    p.logical_focus_identity = p.logical_focus.data();
    p.modal = QApplication::activeModalWidget(); p.popup = QApplication::activePopupWidget(); p.gui_modal = QGuiApplication::modalWindow();
    p.modifiers = QGuiApplication::keyboardModifiers(); p.queried = QGuiApplication::queryKeyboardModifiers(); p.buttons = QGuiApplication::mouseButtons();
    p.physical_keys_idle = true;
    for (int key : {VK_LBUTTON,VK_RBUTTON,VK_MBUTTON,VK_XBUTTON1,VK_XBUTTON2,VK_SHIFT,VK_CONTROL,VK_MENU,VK_LEFT,VK_UP,VK_RIGHT,VK_DOWN})
        if (GetAsyncKeyState(key) & 0x8000) p.physical_keys_idle = false;
    return p;
}
bool backgroundSafe(const Physical& p) {
    return p.foreground_known && p.foreground_pid != GetCurrentProcessId() && p.foreground_info_known && !p.active
        && p.rect_known && p.cursor_known && !p.focus && !p.capture && !p.foreground_capture && !p.mouse && !p.keyboard
        && !p.modal && !p.popup && !p.gui_modal && p.modifiers == Qt::NoModifier && p.queried == Qt::NoModifier
        && p.buttons == Qt::NoButton && p.physical_keys_idle;
}
bool samePhysical(const Physical& a, const Physical& b) {
    return backgroundSafe(b) && a.foreground == b.foreground && a.focus == b.focus && a.capture == b.capture
        && a.foreground_focus == b.foreground_focus && a.foreground_capture == b.foreground_capture
        && a.mouse == b.mouse && a.keyboard == b.keyboard && a.logical_focus_identity == b.logical_focus_identity && a.modal == b.modal
        && a.popup == b.popup && a.gui_modal == b.gui_modal && a.iconic == b.iconic && a.minimized == b.minimized
        && a.active == b.active && a.visible == b.visible && a.hidden == b.hidden
        && a.root_rect == b.root_rect && a.owner_rect == b.owner_rect && a.rect_known && b.rect_known
        && EqualRect(&a.os_rect, &b.os_rect);
}
QJsonObject physicalJson(const Physical& p) {
    return {{"background_safe", backgroundSafe(p)}, {"foreground_is_other_process", p.foreground_known && p.foreground_pid != GetCurrentProcessId()},
        {"cursor_query_known", p.cursor_known}, {"cursor_position", xy(p.cursor)}, {"cursor_position_is_observational", true},
        {"foreground_focus_capture_query_known", p.foreground_info_known}, {"foreground_capture_absent", !p.foreground_capture},
        {"thread_focus_absent", !p.focus}, {"thread_capture_absent", !p.capture}, {"Qt_mouse_grabber_absent", p.mouse.isNull()},
        {"Qt_keyboard_grabber_absent", p.keyboard.isNull()}, {"no_modal_or_popup", !p.modal && !p.popup && !p.gui_modal},
        {"Qt_minimized", p.minimized}, {"OS_minimized", p.iconic}, {"Qt_active", p.active}, {"physical_keys_idle", p.physical_keys_idle}};
}
struct Point { QPointF logical, scene; QPoint screen; };
Point mapped(const Canvas& c, QPointF logical) {
    const QSize size = c.viewport->size();
    require(std::isfinite(logical.x()) && std::isfinite(logical.y()) && logical.x() >= 0 && logical.y() >= 0
        && logical.x() < size.width() && logical.y() < size.height(), "POINT_OUTSIDE_VIEWPORT");
    const QPoint integral(qRound(logical.x()), qRound(logical.y()));
    require(c.viewport->rect().contains(integral), "ROUNDED_POINT_OUTSIDE_VIEWPORT");
    bool ok = false; const QTransform vt = c.view->viewportTransform(), inv = vt.inverted(&ok);
    require(ok && finite(inv), "VIEW_MAPPING_INVALID");
    Point p{logical, inv.map(logical), c.viewport->mapToGlobal(integral)};
    const QPointF roundtrip = vt.map(p.scene), sample = c.view->mapToScene(integral), public_sample = inv.map(QPointF(integral));
    require(std::isfinite(p.scene.x()) && std::isfinite(p.scene.y()) && std::abs(roundtrip.x() - logical.x()) <= 1e-6
        && std::abs(roundtrip.y() - logical.y()) <= 1e-6 && std::abs(sample.x() - public_sample.x()) <= 1e-6
        && std::abs(sample.y() - public_sample.y()) <= 1e-6 && c.viewport->mapFromGlobal(p.screen) == integral, "VIEW_ROUNDTRIP_FAILED");
    return p;
}
struct Run {
    int calls = 0, moves = 0;
    bool hover = false, press = false, release = false, release_returned = false, pending = false;
    bool cleanup_scope = true, exception = false, nonspontaneous = true, ungrab = false;
    Point down{}, last{};
    QJsonArray events;
};
void send(const Canvas& c, Run& r, const char* stage, QEvent::Type type, const Point& p, Qt::KeyboardModifiers mods) {
    QGraphicsSceneMouseEvent e(type);
    e.setWidget(c.viewport); e.setScenePos(p.scene); e.setScreenPos(p.screen);
    e.setLastScenePos(r.last.scene); e.setLastScreenPos(r.last.screen);
    e.setButtonDownScenePos(Qt::LeftButton, r.down.scene); e.setButtonDownScreenPos(Qt::LeftButton, r.down.screen);
    const bool is_press = type == QEvent::GraphicsSceneMousePress, is_release = type == QEvent::GraphicsSceneMouseRelease;
    e.setButton(is_press || is_release ? Qt::LeftButton : Qt::NoButton);
    e.setButtons(is_press || (r.pending && !is_release) ? Qt::LeftButton : Qt::NoButton);
    e.setModifiers(mods); e.setSource(Qt::MouseEventSynthesizedByApplication); e.setFlags(Qt::MouseEventFlags()); e.setAccepted(false);
    QJsonObject row{{"stage", stage}, {"viewport_logical", xy(p.logical)}, {"scene", xy(p.scene)}, {"screen", xy(p.screen)},
        {"last_scene", xy(r.last.scene)}, {"button_down_scene", xy(r.down.scene)}, {"button", static_cast<int>(e.button())},
        {"buttons", static_cast<int>(e.buttons())}, {"modifiers", static_cast<int>(mods)}, {"sent", true}, {"returned", false}};
    if (is_press) { r.press = true; r.pending = true; }
    if (is_release) r.release = true;
    if (!std::strcmp(stage, "hover")) r.hover = true;
    if (!std::strcmp(stage, "move")) ++r.moves;
    ++r.calls;
    r.last = p; // Last attempted position also determines exception cleanup release.
    try {
        r.nonspontaneous = r.nonspontaneous && !e.spontaneous();
        const bool returned = QCoreApplication::sendEvent(c.scene, &e);
        row.insert("returned", true); row.insert("send_return", returned); row.insert("accepted", e.isAccepted());
        r.nonspontaneous = r.nonspontaneous && !e.spontaneous();
        if (is_release) { r.pending = false; r.release_returned = true; }
    } catch (...) { r.exception = true; r.events.append(row); throw; }
    r.events.append(row);
}
class ReleaseGuard {
public:
    ReleaseGuard(const Scope& s, const Canvas& c, Run& r, Qt::KeyboardModifiers mods, const Physical& physical_before) : s_(s), c_(c), r_(r), mods_(mods), physical_before_(physical_before) {}
    ~ReleaseGuard() noexcept { finish(); }
    void finish() noexcept {
        if (finished_) return; finished_ = true;
        if (!r_.press) return;
        try {
            r_.cleanup_scope = receiverAlive(s_, c_) && samePhysical(physical_before_, physical(s_));
            if (!r_.cleanup_scope) return;
            if (r_.pending && !r_.release) send(c_, r_, "cleanup_release", QEvent::GraphicsSceneMouseRelease, r_.last, mods_);
            // Local scene grab created by this batch only (preflight required none).
            if (receiverAlive(s_, c_)) if (QGraphicsItem* item = c_.scene->mouseGrabberItem()) { r_.ungrab = true; item->ungrabMouse(); }
        } catch (...) { r_.exception = true; r_.cleanup_scope = false; }
    }
private:
    const Scope& s_; const Canvas& c_; Run& r_; Qt::KeyboardModifiers mods_; const Physical& physical_before_; bool finished_ = false;
};
QJsonObject counters() {
    return {{"OS_input_calls", 0}, {"OS_cursor_setter_calls", 0}, {"activation_calls", 0}, {"focus_setter_calls", 0},
        {"window_restore_calls", 0}, {"global_capture_setter_calls", 0}, {"global_modifier_setter_calls", 0},
        {"event_pump_calls", 0}, {"QAction_trigger_calls", 0}, {"model_memory_access_calls", 0},
        {"project_open_import_save_calls", 0}, {"simulation_calls", 0}};
}
bool keysOnly(const QJsonObject& obj, const QStringList& allowed) {
    for (const QString& key : obj.keys()) if (!allowed.contains(key)) return false;
    return true;
}
unsigned budgetValue(const QJsonObject& request) {
    const auto value = request.value("budget_ms");
    require(value.isDouble() && value.toDouble() == value.toInt() && value.toInt() >= 50 && value.toInt() <= 1500, "INVALID_BUDGET");
    return static_cast<unsigned>(value.toInt());
}
int write(QJsonObject report, int code, char* output, unsigned cap, unsigned* written, unsigned* needed) {
    report.insert("schema", schema); report.insert("return_code", code);
    const QByteArray bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = static_cast<unsigned>(bytes.size() + 1);
    if (*needed > cap) return -7; // No re-execution/buffer-sizing retry is permitted.
    std::memcpy(output, bytes.constData(), static_cast<std::size_t>(bytes.size())); output[bytes.size()] = 0;
    *written = static_cast<unsigned>(bytes.size()); return code;
}
