#pragma once

// Fixed action and host checks shared in design with the Rest simulation bridge.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <QtCore/QCoreApplication>
#include <QtCore/QElapsedTimer>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtGui/QAction>
#include <QtGui/QActionGroup>
#include <QtWidgets/QAbstractButton>
#include <QtWidgets/QApplication>
#include <QtWidgets/QWidget>
#include <QtWidgets/QCheckBox>
#include <QtWidgets/QLabel>
#include <QtWidgets/QToolButton>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <stdexcept>

namespace {
constexpr unsigned output_capacity = 1048576;
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
struct Sim {
    QPointer<QWidget> owner;
    QPointer<QAction> action;
    QPointer<QToolButton> button;
    HWND window = nullptr;
};
struct Tools {
    QPointer<QAction> brush, advanced, move;
    QPointer<QActionGroup> group;
    QPointer<QToolButton> moveButton;
};
bool ownedBy(const QObject* object, const QObject* owner) {
    for (int depth = 0; object && depth < 128; ++depth, object = object->parent()) {
        if (object == owner) return true;
    }
    return false;
}
QToolButton* visibleDelegateButton(QAction* action, const QWidget* owner, const char* failure) {
    const auto objects = action->associatedObjects();
    need(objects.size() <= 64, "ASSOCIATED_OBJECT_LIMIT");
    int matches = 0;
    QToolButton* result = nullptr;
    for (QObject* object : objects) {
        auto* button = qobject_cast<QToolButton*>(object);
        if (!button || !button->isVisible() || button->defaultAction() != action) continue;
        ++matches;
        result = button;
    }
    need(matches == 1 && result && className(result) == "CloUICommon::DelegateIconButton"
        && result->thread() == QThread::currentThread() && ownedBy(result, owner)
        && result->isCheckable() && !result->isDown(), failure);
    return result;
}
QAction* fixedAction(QWidget* owner, const char* name, const char* text, Budget& budget) {
    QAction* result = nullptr;
    int matches = 0;
    const auto children = owner->children();
    need(children.size() <= 5000, "MAINWINDOW_CHILD_LIMIT");
    for (QObject* child : children) {
        budget.check();
        if (child->objectName() != QLatin1StringView(name)) continue;
        ++matches;
        result = qobject_cast<QAction*>(child);
    }
    need(matches == 1 && result && result->parent() == owner && className(result) == "QAction"
        && result->thread() == QThread::currentThread() && (!text || result->text() == QLatin1StringView(text))
        && result->isCheckable(), "FIXED_ACTION_IDENTITY_DIFFERS");
    return result;
}
Sim resolveSim(std::uint32_t pid, Budget& budget) {
    Sim result;
    int owners = 0;
    const auto widgets = QApplication::allWidgets();
    need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    for (QWidget* widget : widgets) {
        budget.check();
        if (className(widget) == "MDMainWindow" && widget->objectName() == "CLO") {
            result.owner = widget;
            ++owners;
        }
    }
    need(owners == 1 && result.owner && result.owner->thread() == QThread::currentThread(), "MAINWINDOW_NOT_UNIQUE");
    result.window = GetAncestor(reinterpret_cast<HWND>(result.owner->effectiveWinId()), GA_ROOT);
    DWORD window_pid = 0;
    const auto thread = GetWindowThreadProcessId(result.window, &window_pid);
    need(result.window && IsWindow(result.window) && window_pid == pid && thread == GetCurrentThreadId()
        && IsIconic(result.window), "MINIMIZED_WINDOW_IDENTITY_DIFFERS");
    result.action = fixedAction(result.owner, "action_SimStop", nullptr, budget);
    need(result.action->shortcut().toString() == "Space", "SIMULATE_ACTION_SHORTCUT_DIFFERS");
    result.button = visibleDelegateButton(result.action, result.owner, "VISIBLE_SIMULATE_BUTTON_NOT_UNIQUE");
    return result;
}
bool uiRunning(const Sim& sim) {
    need(sim.owner && sim.action && sim.button && sim.action->parent() == sim.owner
        && sim.action->objectName() == "action_SimStop" && sim.action->isCheckable()
        && visibleDelegateButton(sim.action, sim.owner, "VISIBLE_SIMULATE_BUTTON_NOT_UNIQUE") == sim.button
        && sim.action->isChecked() == sim.button->isChecked(), "SIMULATE_ACTION_BUTTON_READBACK_DIFFERS");
    return sim.action->isChecked();
}
Tools resolveTools(const Sim& sim, Budget& budget) {
    Tools result;
    result.brush = fixedAction(sim.owner, "actionSimulationBrushPinching", "Brush Pinching", budget);
    result.advanced = fixedAction(sim.owner, "actionSoftSelection", "Advanced Pinching", budget);
    result.move = fixedAction(sim.owner, "actionMove_Pattern", "Select/Move", budget);
    result.group = result.brush->actionGroup();
    need(result.group && result.group == result.move->actionGroup() && result.group == result.advanced->actionGroup()
        && result.group->isExclusive()
        && result.group->actions().size() <= 64 && result.group->actions().contains(result.move)
        && result.group->actions().contains(result.brush), "ORIGINAL_BRUSH_MOVE_GROUP_DIFFERS");
    result.moveButton = visibleDelegateButton(result.move, sim.owner, "VISIBLE_MOVE_BUTTON_NOT_UNIQUE");
    need(result.moveButton->objectName() == "actionMove_Pattern", "VISIBLE_MOVE_BUTTON_NAME_DIFFERS");
    return result;
}
void validateToolGroup(const Sim& sim, const Tools& tools) {
    need(sim.owner && tools.brush && tools.advanced && tools.move && tools.group && tools.brush->parent() == sim.owner
        && tools.move->parent() == sim.owner && tools.move->objectName() == "actionMove_Pattern"
        && tools.brush->objectName() == "actionSimulationBrushPinching"
        && tools.advanced->parent() == sim.owner && tools.advanced->objectName() == "actionSoftSelection"
        && tools.advanced->text() == "Advanced Pinching" && tools.advanced->isCheckable()
        && tools.advanced->actionGroup() == tools.group
        && tools.move->isCheckable() && tools.brush->isCheckable() && tools.brush->actionGroup() == tools.group
        && tools.move->actionGroup() == tools.group && tools.group->isExclusive() && tools.group->actions().size() <= 64,
        "TOOL_GROUP_REFERENCE_CHANGED");
}
QToolButton* verifiedMoveButton(const Sim& sim, const Tools& tools) {
    auto* button = visibleDelegateButton(tools.move, sim.owner, "VISIBLE_MOVE_BUTTON_NOT_UNIQUE");
    need(button == tools.moveButton && button->objectName() == "actionMove_Pattern", "VISIBLE_MOVE_BUTTON_CHANGED");
    return button;
}
void requireOverrideOn(const Sim& sim, Budget& budget) {
    QPointer<QWidget> root;
    int roots = 0;
    const auto widgets = QApplication::allWidgets();
    need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    for (QWidget* widget : widgets) {
        budget.check();
        if (widget->objectName() == "PropertyEditorContents" && className(widget) == "QWidget" && widget->isVisible()) {
            root = widget;
            ++roots;
        }
    }
    need(roots == 1 && root && root->parent() && className(root->parent()) == "MVDockWidget"
        && root->parent()->objectName() == "Property Editor", "CURRENT_PROPERTY_CONTENTS_NOT_UNIQUE");
    bool owner = false;
    for (QObject* object = root; object; object = object->parent()) if (object == sim.owner) owner = true;
    need(owner, "PROPERTY_CONTENTS_NOT_CURRENT_OWNER");
    QList<QPointer<QWidget>> queue{root};
    QPointer<QLabel> label;
    int scanned = 0, titles = 0, labels = 0;
    while (!queue.isEmpty()) {
        budget.check();
        const auto widget = queue.takeFirst();
        need(widget && ++scanned <= 2048, "PROPERTY_SEMANTIC_SCAN_LIMIT");
        if (!widget->isVisible() || !widget->isVisibleTo(root)) continue;
        if (auto* text = qobject_cast<QLabel*>(widget.data())) {
            if (text->text() == "Simulation Properties") ++titles;
            if (text->text() == "Pinching Simulation Override") { label = text; ++labels; }
        }
        const auto children = widget->children();
        need(children.size() <= 1024, "PROPERTY_CHILD_LIMIT");
        for (QObject* child : children) if (auto* next = qobject_cast<QWidget*>(child)) {
            if (next->isVisible() && next->isVisibleTo(root)) queue.append(next);
        }
    }
    need(titles == 1 && labels == 1 && label && label->parent() && className(label->parent()) == "MVPropertyWidget"
        && label->parent()->objectName() == "MVCheckbox", "PINCHING_OVERRIDE_ROW_NOT_UNIQUE");
    QPointer<QCheckBox> checkbox;
    int buttons = 0;
    for (QObject* child : label->parent()->children()) if (auto* button = qobject_cast<QAbstractButton*>(child)) {
        if (!button->isVisible() || !button->isVisibleTo(root)) continue;
        ++buttons;
        checkbox = qobject_cast<QCheckBox*>(button);
    }
    need(buttons == 1 && checkbox && className(checkbox) == "CloUICommon::CheckBox" && checkbox->objectName() == "checkBox"
        && checkbox->isChecked() && checkbox->checkState() == Qt::Checked && checkbox->text() == "On",
        "PINCHING_OVERRIDE_NOT_ON");
}
}
