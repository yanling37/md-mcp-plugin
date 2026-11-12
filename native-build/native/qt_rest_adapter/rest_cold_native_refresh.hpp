#pragma once

#include "rest_cold_simulation_contract.hpp"

#include <QtCore/QCryptographicHash>
#include <QtCore/QFile>
#include <QtWidgets/QGraphicsScene>
#include <QtWidgets/QGraphicsView>
#include <iterator>

namespace {

// Private vendor layout for the verified MD 2026.1.237 Windows x64 image.
// Load paths vary by installation. Only the exact file and mapped code are accepted.
constexpr char nativeUiImageSha[] = "7c6b182559cdffa8058847a0518172d58a0ddfaabe68498ba81355e12b1613bc";
constexpr std::uintptr_t nativeRefreshEntry = 0x1c49a50;

template <typename T>
T nativeRead(std::uintptr_t address) {
    T value{};
    SIZE_T count = 0;
    need(address >= 0x10000 && address < 0x800000000000ULL - sizeof(T)
        && ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address), &value, sizeof(value), &count)
        && count == sizeof(value), "FIXED_NATIVE_UI_OBSERVATION_UNAVAILABLE");
    return value;
}

bool nativeBool(std::uintptr_t address) {
    const auto value = nativeRead<unsigned char>(address);
    need(value <= 1, "FIXED_NATIVE_UI_BOOL_DIFFERS");
    return value != 0;
}

std::uintptr_t verifyNativeUiImage() {
    const auto image = GetModuleHandleW(nullptr);
    wchar_t path[32768]{};
    const auto length = image ? GetModuleFileNameW(image, path, DWORD(std::size(path))) : 0;
    need(length && length < std::size(path), "MD_EXECUTABLE_PATH_UNAVAILABLE");
    QFile file(QString::fromWCharArray(path, length));
    need(file.open(QIODevice::ReadOnly), "MD_EXECUTABLE_UNREADABLE");
    QCryptographicHash hash(QCryptographicHash::Sha256);
    need(hash.addData(&file) && hash.result().toHex() == nativeUiImageSha, "MD_NATIVE_UI_BUILD_UNSUPPORTED");

    const auto base = reinterpret_cast<std::uintptr_t>(image);
    struct CodeRange { std::uintptr_t rva; qint64 fileOffset; qsizetype size; };
    // Native refresh, label/icon chooser, natural wrapper and dirty writer.
    // The three setup ranges prove the exact controller-to-main-window links.
    for (const CodeRange range : {
        CodeRange{nativeRefreshEntry, 0x1c49050, 0x20da}, CodeRange{0x1c96810, 0x1c95e10, 0x208},
        CodeRange{0x4eda7f0, 0x4ed9df0, 0x3e}, CodeRange{0x1c22b90, 0x1c22190, 5},
        CodeRange{0x1c5a460, 0x1c59a60, 0x283}, CodeRange{0x1c9dbc0, 0x1c9d1c0, 0xb5},
        CodeRange{0x1afa810, 0x1af9e10, 0x4d}}) {
        need(file.seek(range.fileOffset), "MD_NATIVE_UI_CODE_RANGE_MISSING");
        const auto disk = file.read(range.size);
        QByteArray mapped(range.size, Qt::Uninitialized);
        SIZE_T count = 0;
        need(disk.size() == range.size && ReadProcessMemory(GetCurrentProcess(),
            reinterpret_cast<const void*>(base + range.rva), mapped.data(), SIZE_T(range.size), &count)
            && count == SIZE_T(range.size) && disk == mapped, "MD_MAPPED_NATIVE_UI_CODE_DIFFERS");
    }
    return base;
}

bool exactUiObject(QObject* object, const char* type, const char* name) {
    return object && className(object) == QLatin1StringView(type) && object->objectName() == QLatin1StringView(name)
        && object->thread() == QThread::currentThread();
}

struct NativeCanvas {
    QPointer<QGraphicsView> view;
    QPointer<QWidget> viewport;
    QPointer<QGraphicsScene> scene;

    bool boundTo(QWidget* owner) const {
        if (!view || !viewport || !scene || view->viewport() != viewport || view->scene() != scene
            || viewport->parentWidget() != view || scene->parent() != viewport || !ownedBy(view, owner)
            || !exactUiObject(view, "QGraphicsView", "") || !exactUiObject(viewport, "QOpenGLWidget", "widget3d")
            || !exactUiObject(scene, "QtView3D", "")) return false;
        auto* dummy = view->parentWidget();
        auto* window = dummy ? dummy->parentWidget() : nullptr;
        auto* contents = window ? window->parentWidget() : nullptr;
        auto* dock = contents ? contents->parentWidget() : nullptr;
        return exactUiObject(dummy, "QWidget", "dummyWidget3d") && exactUiObject(window, "QMainWindow", "dummyWin1")
            && exactUiObject(contents, "QWidget", "dockWidgetContentsAvatar") && exactUiObject(dock, "MVMainDockWidget", "3D Window");
    }
};

NativeCanvas nativeCanvas(QWidget* owner) {
    Budget budget(150);
    const auto widgets = QApplication::allWidgets();
    need(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    NativeCanvas result;
    int matches = 0;
    for (auto* widget : widgets) {
        budget.check();
        auto* view = qobject_cast<QGraphicsView*>(widget);
        if (!view || !ownedBy(view, owner)) continue;
        NativeCanvas candidate{view, view->viewport(), view->scene()};
        if (!candidate.boundTo(owner)) continue;
        ++matches;
        result = candidate;
    }
    need(matches == 1 && result.scene, "NATIVE_UI_3D_CANVAS_NOT_UNIQUE");
    return result;
}

struct NativeUiBinding {
    bool controllerVtable = false, mainWindowVtable = false, embeddedUi = false;
    bool secondaryBase = false, secondaryVtable = false;
    bool simulateAction = false, moveAction = false, brushAction = false;

    bool verified() const {
        return controllerVtable && mainWindowVtable && embeddedUi && secondaryBase && secondaryVtable
            && simulateAction && moveAction && brushAction;
    }

    QJsonObject json() const {
        return {{"controller_vtable_matches", controllerVtable}, {"main_window_vtable_matches", mainWindowVtable},
            {"embedded_ui_matches", embeddedUi}, {"secondary_base_matches", secondaryBase},
            {"secondary_base_vtable_matches", secondaryVtable}, {"simulate_action_matches", simulateAction},
            {"move_action_matches", moveAction}, {"brush_action_matches", brushAction}};
    }
};

struct NativeUiState {
    NativeUiBinding binding;
    bool controllerRunning = false, refreshDirty = false, updateGate = false;
    bool viewTickEnabled = false, viewTickSeen = false;
};

class NativeUiRefresh {
public:
    explicit NativeUiRefresh(const Sim& sim)
        : base_(verifyNativeUiImage()), canvas_(nativeCanvas(sim.owner)),
          controller_(nativeRead<std::uintptr_t>(base_ + 0x991ebc8)),
          callback_(nativeRead<std::uintptr_t>(base_ + 0x991e9f0)) {}

    NativeUiState observe(const Sim& sim, const Tools& tools) const {
        need(canvas_.boundTo(sim.owner), "NATIVE_UI_3D_CANVAS_CHANGED");
        const auto controllerVtable = nativeRead<std::uintptr_t>(controller_);
        need(nativeRead<std::uintptr_t>(base_ + 0x991ebc8) == controller_
            && (controllerVtable == base_ + 0x80193f0 || controllerVtable == base_ + 0x8108330)
            && nativeRead<std::uintptr_t>(controllerVtable + 95 * sizeof(void*)) == base_ + 0x3be7d20,
            "CLOTH_CONTROLLER_RUNTIME_DIFFERS");
        const auto callbackVtable = nativeRead<std::uintptr_t>(callback_);
        need(nativeRead<std::uintptr_t>(base_ + 0x991e9f0) == callback_ && callbackVtable == base_ + 0x7c0ce28
            && nativeRead<std::uintptr_t>(callbackVtable + 82 * sizeof(void*)) == base_ + nativeRefreshEntry
            && nativeRead<std::uintptr_t>(callbackVtable + 99 * sizeof(void*)) == base_ + 0x1c22b90,
            "NATIVE_UI_REFRESH_CALLBACK_DIFFERS");
        NativeUiState state;
        state.binding = binding(sim, tools);
        state.controllerRunning = nativeBool(controller_ + 0x23c);
        state.refreshDirty = nativeBool(callback_ + 0x1c);
        state.updateGate = nativeBool(nativeRead<std::uintptr_t>(callback_ + 8) + 8);
        const auto view = reinterpret_cast<std::uintptr_t>(canvas_.scene.data());
        state.viewTickEnabled = nativeBool(view + 0x300);
        state.viewTickSeen = nativeBool(view + 0x5e0);
        return state;
    }

    void refresh(const Sim& sim, const Tools& tools, unsigned& invocations) const {
        const auto now = observe(sim, tools);
        need(now.binding.verified() && !now.controllerRunning && now.refreshDirty && now.updateGate && !now.viewTickEnabled,
            "NATIVE_UI_REFRESH_PRECONDITIONS_CHANGED");
        // The vendor's natural wrapper passes only the callback as RCX and ignores the return.
        using Refresh = void (__fastcall *)(void*);
        const auto method = reinterpret_cast<Refresh>(base_ + nativeRefreshEntry);
        ++invocations;
        method(reinterpret_cast<void*>(callback_));
    }

private:
    NativeUiBinding binding(const Sim& sim, const Tools& tools) const {
        // MainWindowController retains the MDMainWindow's embedded UI and secondary base.
        const auto holder = nativeRead<std::uintptr_t>(base_ + 0x99260a0);
        const auto owner = reinterpret_cast<std::uintptr_t>(sim.owner.data());
        const auto ui = nativeRead<std::uintptr_t>(holder + 0x10);
        const auto secondary = nativeRead<std::uintptr_t>(holder + 0x20);
        return {nativeRead<std::uintptr_t>(holder) == base_ + 0x7c0e968,
            nativeRead<std::uintptr_t>(owner) == base_ + 0x7bf73a8, ui == owner + 0x98,
            secondary == owner + 0x48, nativeRead<std::uintptr_t>(secondary) == base_ + 0x7bf75c0,
            sim.action && nativeRead<std::uintptr_t>(ui + 0xa8) == reinterpret_cast<std::uintptr_t>(sim.action.data()),
            tools.move && nativeRead<std::uintptr_t>(ui + 0x6b8) == reinterpret_cast<std::uintptr_t>(tools.move.data()),
            tools.brush && nativeRead<std::uintptr_t>(ui + 0x1c08) == reinterpret_cast<std::uintptr_t>(tools.brush.data())};
    }

    std::uintptr_t base_;
    NativeCanvas canvas_;
    std::uintptr_t controller_, callback_;
};

}  // namespace
