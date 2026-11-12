// Fixed Grid Properties transaction, built against the same Qt scope guards as the 3D adapter.
#include <QtWidgets/QDoubleSpinBox>
#include <QtWidgets/QLabel>
#include <QtWidgets/QStackedLayout>
#include <array>
#include "../qt_world_adapter/canvas_common.inc"

constexpr auto gridSchema = "md-qt-world-grid-step/1";
constexpr auto gridActionName = "actionPattern_Editor_Property";
constexpr auto threeLabel = "3D Arrow Key Movement (mm)";
constexpr auto twoLabel = "2D Arrow Key Movement (mm)";
constexpr const char* paneClasses[]{"QWidget", "QMainWindow", "MVDockWidget", "QWidget"};
constexpr const char* paneNames[]{"centralWg", "DummyDockingWindow", "Property Editor", "PropertyEditorContents"};

using Pane = std::array<QPointer<QObject>, 4>;

Pane propertyPane(const Scope& scope, Budget& budget) {
    Pane pane;
    QObject* parent = scope.owner;
    for (std::size_t index = 0; index < pane.size(); ++index) {
        parent = uniqueChild(parent, paneClasses[index], paneNames[index], budget, scope.app->thread());
        pane[index] = parent;
    }
    return pane;
}

bool paneAlive(const Scope& scope, const Pane& pane) {
    if (!scopeAlive(scope)) return false;
    QObject* parent = scope.owner;
    for (std::size_t index = 0; index < pane.size(); ++index) {
        QObject* child = pane[index];
        if (!exact(child, paneClasses[index], paneNames[index]) || child->parent() != parent
            || child->thread() != scope.app->thread()) return false;
        parent = child;
    }
    return true;
}

bool actionAlive(const Scope& scope, QAction* action) {
    return exact(action, "QAction", gridActionName)
        && action->parent() == scope.owner && action->thread() == scope.app->thread()
        && action->text() == "Grid Properties" && action->toolTip() == "Grid Properties ( F12 )"
        && action->isEnabled() && !action->isCheckable();
}

QPointer<QAction> gridAction(const Scope& scope, Budget& budget) {
    const auto& children = scope.owner->children();
    require(children.size() <= 5000, "GRID_ACTION_SCOPE_LIMIT");
    QPointer<QAction> action;
    int matches = 0;
    for (QObject* child : children) {
        budget.check();
        require(child && child->thread() == scope.app->thread(), "GRID_ACTION_THREAD_DIFFERS");
        if (child->objectName() == QLatin1StringView(gridActionName)) { ++matches; action = qobject_cast<QAction*>(child); }
    }
    require(matches == 1 && actionAlive(scope, action), "GRID_ACTION_IDENTITY_DIFFERS");
    return action;
}

struct Page {
    QPointer<QWidget> widget;
    bool visible, hidden, relative;
};

struct Stack {
    QPointer<QWidget> contents, dock, current, grid;
    QPointer<QStackedLayout> layout;
    QList<QPointer<QWidget>> order;
    QList<Page> children;
    int index = -1;
    bool contentsVisible = false, contentsHidden = false, dockVisible = false, dockHidden = false;
};

Stack stackState(const Scope& scope, const Pane& pane, Budget& budget, QJsonObject* observation = nullptr) {
    require(paneAlive(scope, pane), "PROPERTY_PANE_LOST");
    Stack stack;
    stack.contents = qobject_cast<QWidget*>(pane[3].data());
    stack.dock = qobject_cast<QWidget*>(pane[2].data());
    require(stack.contents && stack.dock && !stack.contents->isWindow() && !stack.dock->isWindow(), "PROPERTY_PANE_NOT_DOCKED");
    stack.contentsVisible = stack.contents->isVisible(); stack.contentsHidden = stack.contents->isHidden();
    stack.dockVisible = stack.dock->isVisible(); stack.dockHidden = stack.dock->isHidden();
    auto* installed = stack.contents->layout();
    if (observation) {
        *observation = {{"contents_visible", stack.contentsVisible}, {"contents_hidden", stack.contentsHidden},
            {"dock_visible", stack.dockVisible}, {"dock_hidden", stack.dockHidden}, {"layout_present", installed != nullptr}};
        if (installed) {
            observation->insert("layout_class", QString::fromLatin1(installed->metaObject()->className()).left(96));
            observation->insert("layout_object_name", installed->objectName().left(96));
            observation->insert("layout_parent_matches", installed->parent() == stack.contents);
            observation->insert("layout_gui_thread_matches", installed->thread() == scope.app->thread());
        }
    }
    require(installed, "PROPERTY_STACK_LAYOUT_ABSENT");
    stack.layout = qobject_cast<QStackedLayout*>(installed);
    require(stack.layout && exact(stack.layout, "QStackedLayout", ""), "PROPERTY_STACK_LAYOUT_TYPE_OR_NAME_DIFFERS");
    require(stack.layout->parent() == stack.contents && stack.layout->thread() == scope.app->thread(), "PROPERTY_STACK_LAYOUT_OWNER_DIFFERS");
    const int count = stack.layout->count();
    const auto mode = stack.layout->stackingMode();
    stack.index = stack.layout->currentIndex();
    if (observation) {
        observation->insert("layout_count", count);
        observation->insert("stacking_mode", int(mode));
        observation->insert("current_index", stack.index);
    }
    require(mode == QStackedLayout::StackOne, "PROPERTY_STACK_MODE_DIFFERS");
    require(count > 0 && count <= 16, "PROPERTY_STACK_COUNT_OUTSIDE_LIMIT");
    require(stack.index >= 0 && stack.index < count, "PROPERTY_STACK_INDEX_INVALID");

    const auto& children = stack.contents->children();
    require(children.size() <= 256, "PROPERTY_STACK_CHILD_LIMIT");
    int layouts = 0, shown = 0, grids = 0;
    QPointer<QWidget> shownWidget;
    for (QObject* child : children) {
        budget.check();
        require(child && child->thread() == scope.app->thread(), "PROPERTY_STACK_THREAD_DIFFERS");
        if (qobject_cast<QStackedLayout*>(child)) ++layouts;
        auto* widget = qobject_cast<QWidget*>(child);
        if (!widget) continue;
        const bool relative = widget->isVisibleTo(stack.contents);
        const bool hidden = widget->isHidden();
        stack.children.append(Page{widget, widget->isVisible(), hidden, relative});
        if (!widget->isWindow() && relative && !hidden) { ++shown; shownWidget = widget; }
    }
    if (observation) {
        observation->insert("direct_layout_count", layouts);
        observation->insert("direct_widget_count", stack.children.size());
        observation->insert("relative_visible_widget_count", shown);
    }
    require(layouts == 1, "PROPERTY_STACK_DIRECT_LAYOUT_AMBIGUOUS");
    QJsonArray pages;
    for (int index = 0; index < count; ++index) {
        budget.check();
        auto* widget = stack.layout->widget(index);
        require(widget && widget->thread() == scope.app->thread() && widget->parentWidget() == stack.contents
            && !widget->isWindow() && !stack.order.contains(widget), "PROPERTY_STACK_MEMBER_DIFFERS");
        stack.order.append(widget);
        if (exact(widget, "PropertyEditorWidget", "PropertyEditorWidget")) { ++grids; stack.grid = widget; }
        if (observation) pages.append(QJsonObject{{"class_name", QString::fromLatin1(widget->metaObject()->className()).left(96)},
            {"object_name", widget->objectName().left(96)}, {"visible", widget->isVisible()}, {"hidden", widget->isHidden()},
            {"relative_visible", widget->isVisibleTo(stack.contents)}});
    }
    if (observation) { observation->insert("pages", pages); observation->insert("grid_page_count", grids); }
    require(shown == 1, "PROPERTY_STACK_ACTIVE_PAGE_AMBIGUOUS");
    require(grids == 1, "GRID_PAGE_NOT_UNIQUE");
    stack.current = stack.order.at(stack.index);
    require(stack.current && stack.current == shownWidget && stack.layout->currentWidget() == stack.current
        && stack.layout->count() == count && stack.layout->currentIndex() == stack.index, "PROPERTY_STACK_ACTIVE_REFERENCE_CHANGED");
    return stack;
}

bool sameStack(const Stack& original, const Stack& current, bool restoreFlags) {
    if (!original.layout || original.layout != current.layout || !original.contents || original.contents != current.contents
        || !original.dock || original.dock != current.dock || !original.grid || original.grid != current.grid
        || original.order != current.order || original.children.size() != current.children.size()
        || original.contentsVisible != current.contentsVisible || original.contentsHidden != current.contentsHidden
        || original.dockVisible != current.dockVisible || original.dockHidden != current.dockHidden) return false;
    if (restoreFlags && (original.index != current.index || !original.current || original.current != current.current)) return false;
    for (const auto& page : original.children) {
        const auto match = std::find_if(current.children.cbegin(), current.children.cend(),
            [&](const Page& other) { return page.widget && page.widget == other.widget; });
        if (match == current.children.cend()) return false;
        if (restoreFlags && (page.visible != match->visible || page.hidden != match->hidden || page.relative != match->relative)) return false;
    }
    return true;
}

struct Field {
    QPointer<QLabel> label;
    QPointer<QWidget> row;
    QPointer<QDoubleSpinBox> spin;
};

Field bindField(QLabel* label, QWidget* grid, QThread* gui) {
    Field result;
    auto* row = label->parentWidget();
    require(exact(label, "CloUICommon::Label", "label") && label->thread() == gui && label->isEnabled()
        && label->isVisibleTo(grid) && !label->isHidden() && exact(row, "CloUICommon::Frame", "frame")
        && row->thread() == gui && !row->isWindow() && row->children().size() <= 256, "GRID_FIELD_LABEL_OR_ROW_DIFFERS");
    int matches = 0;
    for (QObject* child : row->children()) {
        if (auto* spin = qobject_cast<QDoubleSpinBox*>(child)) { ++matches; result.spin = spin; }
    }
    const auto spin = result.spin;
    require(matches == 1 && exact(spin, "MVCustomDoubleSpinBox", "doubleSpinBox") && spin->thread() == gui
        && spin->parentWidget() == row && !spin->isWindow() && spin->isVisibleTo(grid) && !spin->isHidden() && spin->isEnabled()
        && spin->decimals() == 1 && spin->minimum() == 0.1 && spin->maximum() == 100000.0,
        "GRID_SPIN_IDENTITY_DIFFERS");
    result.label = label; result.row = row;
    return result;
}

struct Fields { Field three, two; double step = 0, step2d = 0; };

Fields fieldsFor(const Stack& stack, QThread* gui, Budget& budget, bool allowInactive = false) {
    require(stack.grid && (allowInactive || stack.current == stack.grid), "GRID_PAGE_NOT_CURRENT");
    QList<QPair<QWidget*, int>> widgets{{stack.grid.data(), 0}};
    Fields result;
    int three = 0, two = 0;
    for (qsizetype index = 0; index < widgets.size(); ++index) {
        budget.check();
        const auto [widget, depth] = widgets.at(index);
        require(widget && widget->thread() == gui && widget->children().size() <= 5000, "GRID_FIELD_TREE_LIMIT_OR_THREAD");
        if (auto* label = qobject_cast<QLabel*>(widget)) {
            if (label->text() == QLatin1StringView(threeLabel)) { ++three; result.three = bindField(label, stack.grid, gui); }
            if (label->text() == QLatin1StringView(twoLabel)) { ++two; result.two = bindField(label, stack.grid, gui); }
        }
        for (QObject* child : widget->children()) {
            if (auto* next = qobject_cast<QWidget*>(child); next && !next->isWindow()) {
                require(widgets.size() < 1500 && depth < 24, "GRID_FIELD_TREE_LIMIT_OR_THREAD");
                widgets.append({next, depth + 1});
            }
        }
    }
    require(three == 1 && two == 1 && result.three.row != result.two.row && result.three.spin != result.two.spin,
        "GRID_FIELDS_NOT_UNIQUE");
    result.step = result.three.spin->value(); result.step2d = result.two.spin->value();
    require(std::isfinite(result.step) && std::isfinite(result.step2d), "NONFINITE_GRID_VALUES");
    return result;
}

bool sameField(const Field& a, const Field& b) {
    return a.label && a.row && a.spin && a.label == b.label && a.row == b.row && a.spin == b.spin;
}

bool sameFields(const Fields& original, const Fields& current) {
    return sameField(original.three, current.three) && sameField(original.two, current.two) && original.step2d == current.step2d;
}

struct Lease {
    QString token;
    Scope scope;
    Pane pane;
    Stack stack;
    Fields original;
    double temporary = 0;
    bool committed = false;
    bool restoreSetterAttempted = false;
    Physical physicalBefore{};
};
Lease lease;

bool matchesRestoreField(const Lease& saved, const Fields& current) {
    const bool mayAlreadyBeOriginal = !saved.committed || saved.restoreSetterAttempted;
    return sameFields(saved.original, current)
        && (current.step == saved.temporary || (mayAlreadyBeOriginal && current.step == saved.original.step));
}

bool matchesProbeFields(const Lease& saved, const Fields& current) {
    return saved.committed && !saved.restoreSetterAttempted && sameFields(saved.original, current)
        && current.step == saved.temporary;
}

struct GridRun {
    int actionCalls = 0, setters = 0, pressCalls = 0, releaseCalls = 0, pageRestores = 0;
    bool opened = false, pageRestored = false, pairClosed = true, commit = false;
    bool leaseProbed = false;
    bool pressAccepted = false, releaseAccepted = false;
    QString token;
    QJsonValue step, original, twoD;
    QJsonObject originalStack, openedStack;
    const char* reason = nullptr;

    bool effects() const { return actionCalls || setters || pressCalls || releaseCalls || pageRestores; }
    bool numericEffects() const { return setters || pressCalls || releaseCalls; }
};

void physicalStable(const Scope& scope, const Physical& before) {
    require(samePhysical(before, physical(scope)), "GRID_PHYSICAL_SCOPE_CHANGED");
}

void requireLeaseStack(const Scope& scope, const Pane& pane, const Stack& page, const QString& token) {
    require(!lease.token.isEmpty() && token == lease.token && lease.scope.root && lease.scope.root == scope.root
        && lease.scope.owner == scope.owner && lease.pane == pane && sameStack(lease.stack, page, false),
        "GRID_RESTORE_TOKEN_OR_STACK_DIFFERS");
    require(sameStack(lease.stack, page, true), "GRID_RESTORE_ORIGINAL_PAGE_DIFFERS");
}

void probeLease(const Scope& scope, const QString& token, Budget& budget, GridRun& run) {
    const auto before = physical(scope);
    require(backgroundSafe(before) && before.minimized && before.iconic, "MINIMIZED_BACKGROUND_GRID_SCOPE_REQUIRED");
    const auto pane = propertyPane(scope, budget);
    const auto current = stackState(scope, pane, budget, &run.originalStack);
    requireLeaseStack(scope, pane, current, token);
    require(samePhysical(lease.physicalBefore, before), "GRID_LEASE_PHYSICAL_SCOPE_CHANGED");
    const auto fields = fieldsFor(current, scope.app->thread(), budget, true);
    require(matchesProbeFields(lease, fields), "GRID_PROBE_COMMITTED_FIELDS_DIFFER");
    requireLeaseStack(scope, pane, stackState(scope, pane, budget), token);
    physicalStable(scope, before);
    run.token = token;
    run.step = fields.step;
    run.original = lease.original.step;
    run.twoD = fields.step2d;
    run.pageRestored = true;
    run.leaseProbed = true;
}

void restorePage(const Scope& scope, const Pane& pane, const Physical& physicalBefore, const Stack& original,
                 GridRun& run) noexcept {
    try {
        Budget cleanup(150);
        physicalStable(scope, physicalBefore);
        const auto current = stackState(scope, pane, cleanup);
        require(sameStack(original, current, false) && original.current && original.layout, "ORIGINAL_GRID_STACK_CHANGED");
        if (current.current != original.current) {
            require(current.current == current.grid, "UNEXPECTED_PAGE_DURING_RESTORE");
            ++run.pageRestores;
            original.layout->setCurrentWidget(original.current);
        }
        physicalStable(scope, physicalBefore);
        require(sameStack(original, stackState(scope, pane, cleanup), true), "ORIGINAL_GRID_PAGE_NOT_RESTORED");
        run.pageRestored = true;
    } catch (...) {
        run.reason = "ORIGINAL_GRID_PAGE_RESTORATION_UNVERIFIED";
    }
}

bool spinAlive(const Scope& scope, const Pane& pane, const Stack& stack, const Field& field) {
    return paneAlive(scope, pane) && stack.layout && stack.grid && field.row && field.spin && field.label
        && stack.grid->parentWidget() == stack.contents && stack.contents->layout() == stack.layout
        && field.spin->parentWidget() == field.row && field.label->parentWidget() == field.row
        && field.spin->thread() == scope.app->thread() && stack.grid->isAncestorOf(field.spin)
        && exact(field.spin, "MVCustomDoubleSpinBox", "doubleSpinBox");
}

void finishReturn(const Scope& scope, const Pane& pane, const Stack& stack, const Field& field,
                  const Physical& before, GridRun& run) noexcept {
    if (!run.pressCalls || run.releaseCalls) return;
    try {
        physicalStable(scope, before);
        require(spinAlive(scope, pane, stack, field), "RETURN_RECEIVER_LOST");
        QKeyEvent release(QEvent::KeyRelease, Qt::Key_Return, Qt::NoModifier, QStringLiteral("\r"), false, 1);
        require(!release.spontaneous(), "SPONTANEOUS_RETURN_FORBIDDEN");
        ++run.releaseCalls;
        const bool sent = QCoreApplication::sendEvent(field.spin, &release);
        run.releaseAccepted = sent && release.isAccepted();
        run.pairClosed = true;
    } catch (...) {
        run.reason = "GRID_RETURN_PAIR_UNVERIFIED";
    }
}

void gridTransaction(const Scope& scope, const QString& operation, double target, const QString& token,
                     Budget& budget, GridRun& run) {
    const bool writing = operation != "inspect", restoring = operation == "restore";
    const auto before = physical(scope);
    require(backgroundSafe(before) && before.minimized && before.iconic, "MINIMIZED_BACKGROUND_GRID_SCOPE_REQUIRED");
    const auto pane = propertyPane(scope, budget);
    const auto originalPage = stackState(scope, pane, budget, &run.originalStack);
    const auto action = gridAction(scope, budget);
    if (restoring) {
        requireLeaseStack(scope, pane, originalPage, token);
        run.token = token;
        target = lease.original.step;
    }

    Stack grid;
    Fields initial;
    try {
        physicalStable(scope, before);
        require(actionAlive(scope, action), "GRID_ACTION_IDENTITY_DIFFERS");
        if (originalPage.current != originalPage.grid) {
            budget.check();
            ++run.actionCalls;
            action->trigger();
        }
        physicalStable(scope, before);
        grid = stackState(scope, pane, budget, &run.openedStack);
        require(sameStack(originalPage, grid, false) && grid.current == grid.grid && actionAlive(scope, action), "GRID_PAGE_OPEN_UNVERIFIED");
        run.opened = true;
        initial = fieldsFor(grid, scope.app->thread(), budget);
        run.step = initial.step;
        run.original = restoring ? lease.original.step : initial.step;
        run.twoD = initial.step2d;
        if (restoring) {
            require(matchesRestoreField(lease, initial), "GRID_RESTORE_FIELD_OR_VALUE_DIFFERS");
        }
        if (writing) {
            require(!initial.three.spin->isReadOnly() && !initial.three.spin->signalsBlocked(), "GRID_FIELD_WRITE_UNAVAILABLE");
            budget.check(); physicalStable(scope, before);
            require(spinAlive(scope, pane, grid, initial.three) && actionAlive(scope, action), "GRID_FIELD_REFERENCE_CHANGED");
            if (!restoring) {
                lease = {QUuid::createUuid().toString(QUuid::WithoutBraces), scope, pane, originalPage,
                    initial, target, false, false, before};
                run.token = lease.token;
            }
            if (target != initial.step) {
                ++run.setters;
                if (restoring) lease.restoreSetterAttempted = true;
                initial.three.spin->setValue(target);
            }
            physicalStable(scope, before);
            const auto staged = stackState(scope, pane, budget);
            require(sameStack(originalPage, staged, false) && staged.current == grid.grid, "GRID_STACK_CHANGED_AFTER_SETTER");
            const auto stagedFields = fieldsFor(staged, scope.app->thread(), budget);
            require(sameFields(initial, stagedFields) && stagedFields.step == target && !initial.three.spin->signalsBlocked(),
                "GRID_STAGED_VALUE_UNVERIFIED");
            budget.check(); physicalStable(scope, before);
            QKeyEvent press(QEvent::KeyPress, Qt::Key_Return, Qt::NoModifier, QStringLiteral("\r"), false, 1);
            require(!press.spontaneous(), "SPONTANEOUS_RETURN_FORBIDDEN");
            ++run.pressCalls; run.pairClosed = false;
            const bool sent = QCoreApplication::sendEvent(initial.three.spin, &press);
            run.pressAccepted = sent && press.isAccepted();
            finishReturn(scope, pane, grid, initial.three, before, run);
            require(run.pairClosed && run.pressAccepted && run.releaseAccepted, "GRID_RETURN_NOT_ACCEPTED");
            physicalStable(scope, before);
            const auto committed = stackState(scope, pane, budget);
            require(sameStack(originalPage, committed, false) && committed.current == grid.grid, "GRID_STACK_CHANGED_AFTER_RETURN");
            const auto afterFields = fieldsFor(committed, scope.app->thread(), budget);
            require(sameFields(initial, afterFields) && afterFields.step == target, "GRID_COMMITTED_WIDGET_VALUE_UNVERIFIED");
            run.step = afterFields.step;
            run.commit = true;
        }
    } catch (const Reject& error) {
        if (!run.reason) run.reason = error.code;
    } catch (...) {
        run.reason = "UNEXPECTED_GRID_TRANSACTION_EXCEPTION";
    }
    finishReturn(scope, pane, grid, initial.three, before, run);
    restorePage(scope, pane, before, originalPage, run);
    if (run.effects() && (!run.pageRestored || !run.pairClosed)) recovery_required = true;
    if (run.reason && !restoring && !run.numericEffects() && !run.token.isEmpty()) {
        lease = Lease{};
        run.token.clear();
    }
    if (!run.reason && run.commit) {
        if (restoring) lease = Lease{};
        else lease.committed = true;
    }
}

bool validStep(double value) {
    return std::isfinite(value) && value >= 0.1 && value <= 100000.0 && value == std::round(value * 10.0) / 10.0;
}
} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_world_grid_step_abi_version() noexcept { return 1; }

extern "C" __declspec(dllexport)
int __cdecl mdqt_world_grid_step_json(std::uint32_t pid, std::uint64_t creation, const char* input, unsigned length,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!input || !length || length > 4096 || !output || !written || !needed || cap < output_capacity || cap > 1048576) return -1;
    try {
        GridRun run;
        QString operation;
        bool guiThread = false;
        try {
            QJsonParseError error{};
            const auto document = QJsonDocument::fromJson(QByteArray(input, int(length)), &error);
            require(error.error == QJsonParseError::NoError && document.isObject(), "INVALID_JSON");
            auto request = document.object();
            operation = request.value("operation").toString();
            const bool probing = operation == "probe_lease";
            require(operation == "inspect" || operation == "set_temporary" || operation == "restore" || probing,
                "UNKNOWN_GRID_OPERATION");
            require(processMatches(pid, creation), "PROCESS_IDENTITY_MISMATCH");
            modules();
            auto* app = qobject_cast<QApplication*>(QCoreApplication::instance());
            guiThread = app && !QCoreApplication::closingDown() && app->thread() == QThread::currentThread();
            require(guiThread, "GUI_THREAD_REQUIRED");
            require(!running && !recovery_required, "GRID_BRIDGE_BUSY_OR_UI_RECOVERY_REQUIRED");
            QStringList allowed{"operation", "budget_ms"};
            if (operation == "set_temporary") allowed << "step_mm";
            if (operation == "restore" || probing) allowed << "restore_token";
            require(keysOnly(request, allowed), "UNEXPECTED_GRID_PARAMETER");
            const double target = request.value("step_mm").toDouble();
            if (operation == "set_temporary") {
                require(request.value("step_mm").isDouble() && validStep(target), "GRID_STEP_OUTSIDE_REPRESENTABLE_RANGE");
            }
            const auto token = request.value("restore_token").toString();
            if (operation == "restore" || probing) require(!lease.token.isEmpty() && token == lease.token, "GRID_RESTORE_TOKEN_DIFFERS");
            else require(lease.token.isEmpty(), "GRID_STEP_ALREADY_TEMPORARY");
            if (!request.contains("budget_ms")) request.insert("budget_ms", 750);
            Budget budget(budgetValue(request));
            running = true;
            struct Release { ~Release() { running = false; } } release;
            const auto scope = currentScope(budget, pid);
            if (probing) probeLease(scope, token, budget, run);
            else gridTransaction(scope, operation, target, token, budget, run);
        } catch (const Reject& error) {
            run.reason = error.code;
        } catch (...) {
            run.reason = "UNEXPECTED_GRID_BRIDGE_EXCEPTION";
        }

        const bool verified = !run.reason && run.pageRestored && run.pairClosed
            && (run.leaseProbed || (run.opened && (operation == "inspect" || run.commit)));
        const bool restoreRequired = guiThread && !lease.token.isEmpty();
        const bool uncertain = !verified && run.effects() && (!run.pageRestored || !run.pairClosed || run.numericEffects() || restoreRequired);
        const int code = verified ? 0 : uncertain ? 3 : run.effects() ? 2 : 1;
        const QJsonObject report{{"schema", gridSchema}, {"operation", operation}, {"return_code", code},
            {"status", code == 0 ? "SUCCEEDED" : code == 1 ? "REJECTED" : code == 2 ? "PARTIAL" : "UNKNOWN"},
            {"reason", run.reason ? QJsonValue(run.reason) : QJsonValue()}, {"verified", verified},
            {"effects_started", run.effects()}, {"requires_recovery", uncertain}, {"restore_required", restoreRequired},
            {"process_id", qint64(pid)}, {"process_creation_filetime", QString::number(creation)},
            {"original_page_restored", run.pageRestored}, {"step_mm", run.step}, {"original_step_mm", run.original},
            {"original_stack", run.originalStack}, {"opened_stack", run.openedStack},
            {"step_2d_mm", run.twoD}, {"restore_token", run.token.isEmpty() ? QJsonValue() : QJsonValue(run.token)},
            {"grid_widget_commit_verified", verified && run.commit}, {"effective_step_verified", false},
            {"action_trigger_calls", run.actionCalls}, {"step_set_value_calls", run.setters},
            {"return_press_calls", run.pressCalls}, {"return_release_calls", run.releaseCalls},
            {"return_pair_closed", run.pairClosed}, {"page_restore_calls", run.pageRestores},
            {"OS_input_calls", 0}, {"focus_calls", 0}, {"event_pump_calls", 0}};
        const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
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
