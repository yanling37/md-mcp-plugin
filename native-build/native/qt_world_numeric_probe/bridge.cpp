// Public Qt getters and metadata for an existing Translate 3D dialog.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <QtCore/QCoreApplication>
#include <QtCore/QDir>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QMetaMethod>
#include <QtCore/QPointer>
#include <QtCore/QThread>
#include <QtCore/QUuid>
#include <QtGui/QValidator>
#include <QtWidgets/QAbstractButton>
#include <QtWidgets/QApplication>
#include <QtWidgets/QDialog>
#include <QtWidgets/QDialogButtonBox>
#include <QtWidgets/QDoubleSpinBox>
#include <QtWidgets/QLabel>
#include <QtWidgets/QLineEdit>
#include <QtWidgets/QPushButton>
#include <QtWidgets/QSpinBox>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>

#if QT_VERSION != QT_VERSION_CHECK(6,10,3) || !defined(_WIN64) || !defined(_MSC_VER) || defined(QT_DEBUG)
#error Release Qt 6.10.3, MSVC and x64 are required.
#endif

namespace {
constexpr unsigned outputCapacity = 1048576;
constexpr const char* schema = "md-qt-world-numeric-probe/1";

struct Reject { const char* reason; };

void require(bool condition, const char* reason) {
    if (!condition) throw Reject{reason};
}

struct Budget {
    ULONGLONG start = GetTickCount64();
    unsigned milliseconds;

    void check() const {
        require(GetTickCount64() - start < milliseconds, "METADATA_BUDGET_EXCEEDED");
    }
};

QString bounded(const QString& value, int limit = 256) {
    require(value.size() <= limit, "METADATA_TEXT_LIMIT");
    return value;
}

bool processMatches(std::uint32_t pid, std::uint64_t creation) {
    FILETIME born{}, exited{}, kernel{}, user{};
    return pid == GetCurrentProcessId()
        && GetProcessTimes(GetCurrentProcess(), &born, &exited, &kernel, &user)
        && ((std::uint64_t(born.dwHighDateTime) << 32) | born.dwLowDateTime) == creation;
}

QString moduleDirectory(HMODULE module) {
    wchar_t buffer[32768];
    const DWORD count = GetModuleFileNameW(module, buffer, 32768);
    require(count && count < 32768, "MODULE_PATH_UNAVAILABLE");
    return QFileInfo(QDir::cleanPath(QString::fromWCharArray(buffer, int(count)))).absolutePath();
}

void verifyQtModules() {
    const QString directory = moduleDirectory(nullptr);
    for (const wchar_t* name : {L"Qt6Core.dll", L"Qt6Gui.dll", L"Qt6Widgets.dll"}) {
        HMODULE module = GetModuleHandleW(name);
        require(module && moduleDirectory(module).compare(directory, Qt::CaseInsensitive) == 0,
                "QT_MODULE_DIRECTORY_MISMATCH");
    }
}

bool isTranslateDialog(const QWidget* widget) {
    const char* name = widget->metaObject()->className();
    return !std::strcmp(name, "DialogTranslate3D") || !std::strcmp(name, "Marvelous::DialogTranslate3D");
}

QJsonObject describe(const QWidget* widget) {
    return {{"class_name", bounded(QString::fromLatin1(widget->metaObject()->className()))},
        {"object_name", bounded(widget->objectName())}, {"enabled", widget->isEnabled()},
        {"visible", widget->isVisible()}, {"hidden", widget->isHidden()}};
}

QJsonArray widgetPath(const QWidget* widget, const QWidget* dialog) {
    QJsonArray path;
    while (widget != dialog) {
        const auto* parent = widget->parentWidget();
        require(parent && path.size() < 16, "DIALOG_ANCESTRY_LIMIT_OR_CHANGED");
        const auto& siblings = parent->children();
        require(siblings.size() <= 512, "DIALOG_CHILDREN_LIMIT_OR_CHANGED");
        const auto position = std::find(siblings.cbegin(), siblings.cend(), widget);
        require(position != siblings.cend(), "DIALOG_CHILDREN_LIMIT_OR_CHANGED");
        path.prepend(int(position - siblings.cbegin()));
        widget = parent;
    }
    return path;
}

QString dialogReference(QWidget* dialog) {
    struct ObservedDialog { QPointer<QWidget> widget; QString reference; };
    static QList<ObservedDialog> observed;
    observed.removeIf([](const ObservedDialog& row) { return row.widget.isNull(); });
    for (const auto& row : observed) {
        if (row.widget.data() == dialog) return row.reference;
    }
    require(observed.size() < 4, "DIALOG_REFERENCE_LIMIT");
    const QString reference = QUuid::createUuid().toString(QUuid::WithoutBraces);
    observed.append(ObservedDialog{QPointer<QWidget>(dialog), reference});
    return reference;
}

QJsonObject methodMetadata(const QMetaObject* meta, const Budget& budget) {
    require(meta->methodCount() <= 512, "META_METHOD_LIMIT");
    QJsonArray publicMethods, knownSlots;
    for (int index = meta->methodOffset(); index < meta->methodCount(); ++index) {
        budget.check();
        const QMetaMethod method = meta->method(index);
        const QByteArray signature = method.methodSignature();
        const bool isPublic = method.access() == QMetaMethod::Public;
        const bool isKnown = signature == "OnAccept()" || signature == "OnChangeInputDistanceValue(double)";
        if (!isPublic && !isKnown) continue;
        const QJsonObject row{{"index", index}, {"signature", bounded(QString::fromLatin1(signature), 512)},
            {"return_type", bounded(QString::fromLatin1(method.typeName() ? method.typeName() : ""))},
            {"access", int(method.access())}, {"method_type", int(method.methodType())},
            {"parameter_count", method.parameterCount()}};
        if (isPublic) publicMethods.append(row);
        if (isKnown) knownSlots.append(row);
    }
    return {{"own_public_methods", publicMethods}, {"known_declarations", knownSlots}};
}

QJsonValue finiteNumber(double value) {
    return std::isfinite(value) ? QJsonValue(value) : QJsonValue(QJsonValue::Null);
}

template <typename SpinBox>
QJsonObject spinState(const SpinBox* spin) {
    return {{"value", finiteNumber(spin->value())}, {"minimum", finiteNumber(spin->minimum())},
        {"maximum", finiteNumber(spin->maximum())}, {"single_step", finiteNumber(spin->singleStep())},
        {"read_only", spin->isReadOnly()}, {"prefix", bounded(spin->prefix())},
        {"suffix", bounded(spin->suffix())}, {"locale", bounded(spin->locale().name())}};
}

QJsonObject validatorState(const QValidator* validator) {
    if (!validator) return {};
    require(validator->thread() == QThread::currentThread(), "VALIDATOR_THREAD_MISMATCH");
    QJsonObject result{{"class_name", bounded(QString::fromLatin1(validator->metaObject()->className()))}};
    if (const auto* decimal = qobject_cast<const QDoubleValidator*>(validator)) {
        result.insert("standard_validator", "QDoubleValidator");
        result.insert("minimum", finiteNumber(decimal->bottom()));
        result.insert("maximum", finiteNumber(decimal->top()));
        result.insert("decimals", decimal->decimals());
        result.insert("notation", int(decimal->notation()));
        result.insert("locale", bounded(decimal->locale().name()));
    } else if (const auto* integer = qobject_cast<const QIntValidator*>(validator)) {
        result.insert("standard_validator", "QIntValidator");
        result.insert("minimum", integer->bottom());
        result.insert("maximum", integer->top());
        result.insert("locale", bounded(integer->locale().name()));
    }
    return result;
}

QJsonObject controlState(const QWidget* widget, const QWidget* dialog) {
    if (const auto* spin = qobject_cast<const QDoubleSpinBox*>(widget)) {
        auto row = spinState(spin);
        row.insert("standard_control", "QDoubleSpinBox");
        row.insert("decimals", spin->decimals());
        return row;
    }
    if (const auto* spin = qobject_cast<const QSpinBox*>(widget)) {
        auto row = spinState(spin);
        row.insert("standard_control", "QSpinBox");
        row.insert("decimals", 0);
        return row;
    }
    if (const auto* editor = qobject_cast<const QLineEdit*>(widget)) {
        const QWidget* parent = editor->parentWidget();
        if (qobject_cast<const QDoubleSpinBox*>(parent) || qobject_cast<const QSpinBox*>(parent)) return {};
        QJsonObject row{{"standard_control", "QLineEdit"}, {"read_only", editor->isReadOnly()},
            {"echo_mode", int(editor->echoMode())}, {"validator", validatorState(editor->validator())}};
        if (editor->echoMode() == QLineEdit::Normal) row.insert("text", bounded(editor->text(), 512));
        return row;
    }
    if (const auto* label = qobject_cast<const QLabel*>(widget)) {
        QJsonObject row{{"standard_control", "QLabel"}, {"text", bounded(label->text(), 512)}};
        if (const QWidget* buddy = label->buddy(); buddy && dialog->isAncestorOf(buddy)) {
            row.insert("buddy_path", widgetPath(buddy, dialog));
        }
        return row;
    }
    if (const auto* button = qobject_cast<const QAbstractButton*>(widget)) {
        return {{"standard_control", "QAbstractButton"}, {"text", bounded(button->text(), 512)},
            {"checkable", button->isCheckable()}, {"checked", button->isChecked()}};
    }
    return {};
}

QJsonObject inspectDialog(QWidget* dialog, QThread* guiThread, const Budget& budget) {
    require(dialog->thread() == guiThread, "DIALOG_THREAD_MISMATCH");
    auto result = describe(dialog);
    result.insert("dialog_ref", dialogReference(dialog));
    result.insert("is_window", dialog->isWindow());
    if (const QWidget* parent = dialog->parentWidget()) result.insert("parent", describe(parent));
    result.insert("metadata", methodMetadata(dialog->metaObject(), budget));

    const auto* host = qobject_cast<const QDialog*>(dialog);
    QJsonObject hostState{{"is_qdialog", host != nullptr},
        {"active_modal_is_self", QApplication::activeModalWidget() == dialog},
        {"is_modal", dialog->isModal()}, {"window_modality", int(dialog->windowModality())},
        {"delete_on_close", dialog->testAttribute(Qt::WA_DeleteOnClose)}};
    if (host) {
        hostState.insert("result", host->result());
        hostState.insert("result_is_accepted", host->result() == QDialog::Accepted);
    }

    QList<const QWidget*> queue{dialog};
    QJsonArray controls, standardOkButtons;
    for (qsizetype index = 0; index < queue.size(); ++index) {
        budget.check();
        const QWidget* widget = queue.at(index);
        require(widget->thread() == guiThread, "CONTROL_THREAD_MISMATCH");
        if (widget != dialog) {
            if (const auto* box = qobject_cast<const QDialogButtonBox*>(widget)) {
                if (auto* button = box->button(QDialogButtonBox::Ok)) {
                    require(standardOkButtons.size() < 8 && dialog->isAncestorOf(button)
                        && button->thread() == guiThread, "DIALOG_OK_BUTTON_SCOPE_UNVERIFIED");
                    standardOkButtons.append(QJsonObject{{"path", widgetPath(button, dialog)},
                        {"widget", describe(button)}, {"visible_to_dialog", button->isVisibleTo(dialog)},
                        {"accept_role", box->buttonRole(button) == QDialogButtonBox::AcceptRole}});
                }
            }
            auto row = controlState(widget, dialog);
            if (!row.isEmpty()) {
                require(controls.size() < 128, "DIALOG_CONTROL_LIMIT");
                row.insert("widget", describe(widget));
                row.insert("path", widgetPath(widget, dialog));
                row.insert("visible_to_dialog", widget->isVisibleTo(dialog));
                controls.append(row);
            }
        }
        const auto& children = widget->children();
        require(children.size() <= 512, "DIALOG_CHILDREN_LIMIT_OR_CHANGED");
        for (QObject* child : children) {
            if (auto* childWidget = qobject_cast<QWidget*>(child)) {
                require(queue.size() < 512, "DIALOG_SUBTREE_LIMIT");
                queue.append(childWidget);
            }
        }
    }
    result.insert("controls", controls);
    hostState.insert("standard_ok_buttons", standardOkButtons);
    result.insert("host", hostState);
    return result;
}

QJsonArray inspectInstances(QThread* guiThread, const Budget& budget) {
    const auto widgets = QApplication::allWidgets();
    require(widgets.size() <= 25000, "WIDGET_SNAPSHOT_LIMIT");
    QJsonArray instances;
    for (QWidget* widget : widgets) {
        budget.check();
        if (!isTranslateDialog(widget)) continue;
        require(instances.size() < 4, "DIALOG_INSTANCE_LIMIT");
        instances.append(inspectDialog(widget, guiThread, budget));
    }
    return instances;
}
}

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_world_numeric_probe_abi_version() noexcept {
    return 1;
}

extern "C" __declspec(dllexport) int __cdecl mdqt_world_numeric_probe_inspect(std::uint32_t pid, std::uint64_t creation,
    unsigned budget_ms, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!output || !written || !needed || cap != outputCapacity || budget_ms < 50 || budget_ms > 1500) return -1;
    if (!processMatches(pid, creation)) return -2;
    auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
    if (!app || QCoreApplication::closingDown() || app->thread() != QThread::currentThread()
        || std::strcmp(qVersion(), "6.10.3")) return -3;
    try {
        const Budget budget{GetTickCount64(), budget_ms};
        int code = 1;
        QJsonObject report{{"schema", schema}, {"process_id", double(pid)},
            {"process_creation_filetime", QString::number(creation)}, {"qt_version", qVersion()},
            {"verified", false}, {"read_only", true}, {"write_contract_verified", false}, {"effects_started", false}};
        try {
            verifyQtModules();
            const auto instances = inspectInstances(app->thread(), budget);
            budget.check();
            report.insert("instances", instances);
            report.insert("instance_count", instances.size());
            report.insert("verified", true);
            report.insert("status", "READ_COMPLETE");
            code = 0;
        } catch (const Reject& error) {
            report.insert("status", "READ_NOT_VERIFIED");
            report.insert("reason", error.reason);
        }
        report.insert("return_code", code);
        report.insert("elapsed_ms", double(GetTickCount64() - budget.start));
        const QByteArray bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
        require(bytes.size() < UINT_MAX, "RESPONSE_SIZE_LIMIT");
        *needed = unsigned(bytes.size()) + 1;
        if (*needed > cap) return -5;
        std::memcpy(output, bytes.constData(), size_t(bytes.size()));
        output[bytes.size()] = 0;
        *written = unsigned(bytes.size());
        return code;
    } catch (...) {
        return -6;
    }
}
