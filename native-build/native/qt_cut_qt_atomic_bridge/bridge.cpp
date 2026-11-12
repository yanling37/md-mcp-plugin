// N127-style finite composite. No SDK call or Python callback inside the batch.
#include "canvas_common.inc"
#include <optional>
#include <QtGui/QActionGroup>

constexpr const char* mode_names[] = {"action_TransformShape", "action_PatternEdit", "action_Edit_Curve_Point"};
constexpr const char* mode_texts[] = {"Transform Pattern", "Edit Pattern", "Edit Curve Point"};
struct Mode { QPointer<QAction> a[3]; QPointer<QActionGroup> group; int index = -1; };
Mode mode(const Scope& s, Budget& budget) {
    Mode result; int matches[3]{};
    require(scopeAlive(s) && s.owner->children().size() <= 5000, "MODE_OWNER_INVALID");
    for (QObject* o : s.owner->children()) {
        budget.check(); require(o && o->thread() == s.app->thread(), "MODE_THREAD_CHANGED");
        for (int i = 0; i < 3; ++i) if (o->objectName() == QLatin1StringView(mode_names[i])) { ++matches[i]; result.a[i] = qobject_cast<QAction*>(o); }
    }
    for (int i = 0; i < 3; ++i) require(matches[i] == 1 && result.a[i] && exact(result.a[i], "QAction", mode_names[i])
        && result.a[i]->parent() == s.owner && result.a[i]->isEnabled() && result.a[i]->isCheckable()
        && result.a[i]->text() == QLatin1StringView(mode_texts[i]), "FIXED_MODE_ACTION_INVALID");
    result.group = result.a[0]->actionGroup();
    require(result.group && result.group->thread() == s.app->thread() && result.group->parent() == s.owner
        && exact(result.group, "QActionGroup", "") && result.group->isEnabled() && result.group->isExclusive()
        && result.group->exclusionPolicy() == QActionGroup::ExclusionPolicy::Exclusive
        && result.a[1]->actionGroup() == result.group && result.a[2]->actionGroup() == result.group, "MODE_GROUP_INVALID");
    const auto members = result.group->actions(); require(members.size() <= 5000, "MODE_GROUP_LIMIT");
    int present[3]{}, checked = 0;
    for (auto* member : members) {
        budget.check(); require(member && member->thread() == s.app->thread(), "MODE_MEMBER_CHANGED");
        if (member->isChecked()) ++checked;
        for (int i = 0; i < 3; ++i) if (member == result.a[i]) { ++present[i]; if (member->isChecked()) result.index = i; }
    }
    require(present[0] == 1 && present[1] == 1 && present[2] == 1 && checked == 1 && result.index >= 0
        && result.group->checkedAction() == result.a[result.index], "MODE_NOT_UNIQUE"); return result;
}
bool sameModeRefs(const Mode& a, const Mode& b) {
    if (!a.group || a.group != b.group) return false;
    for (int i = 0; i < 3; ++i) if (!a.a[i] || a.a[i] != b.a[i]) return false;
    return true;
}
struct Actions {
    QPointer<QAction> cut, move; QPointer<QActionGroup> cut_group, move_group;
    bool cut_group_present = false, move_group_present = false;
};
Actions actions(const Scope& s, Budget& budget, bool allow_disabled_cut) {
    Actions result; int cuts = 0, moves = 0;
    for (QObject* o : s.owner->children()) {
        budget.check(); require(o && o->thread() == s.app->thread(), "CUT_ACTION_THREAD_CHANGED");
        if (o->objectName() == "actionCutPattern2D") { ++cuts; result.cut = qobject_cast<QAction*>(o); }
        if (o->objectName() == "actionMove_Pattern") { ++moves; result.move = qobject_cast<QAction*>(o); }
    }
    require(cuts == 1 && moves == 1 && result.cut && result.move
        && exact(result.cut, "QAction", "actionCutPattern2D") && result.cut->parent() == s.owner
        && (allow_disabled_cut || result.cut->isEnabled()) && !result.cut->isCheckable() && !result.cut->isChecked()
        && result.cut->text() == "Cut" && result.cut->toolTip() == "Cut" && result.cut->shortcutContext() == Qt::WindowShortcut
        && exact(result.move, "QAction", "actionMove_Pattern") && result.move->parent() == s.owner
        && result.move->isEnabled() && result.move->isCheckable() && result.move->isChecked()
        && result.move->text() == "Select/Move", "FIXED_CUT_ACTION_CONTRACT_REJECTED");
    result.cut_group = result.cut->actionGroup(); result.move_group = result.move->actionGroup();
    result.cut_group_present = !result.cut_group.isNull(); result.move_group_present = !result.move_group.isNull();
    for (const auto& group : {result.cut_group, result.move_group})
        require(!group || (group->thread() == s.app->thread() && group->isEnabled()), "CUT_GROUP_CHANGED");
    return result;
}
bool sameActions(const Actions& a, const Actions& b) {
    return a.cut && a.move && a.cut == b.cut && a.move == b.move
        && a.cut_group_present == b.cut_group_present && a.move_group_present == b.move_group_present
        && (!a.cut_group_present || (a.cut_group && a.cut_group == b.cut_group))
        && (!a.move_group_present || (a.move_group && a.move_group == b.move_group));
}
Canvas canvas2d(const Scope& s, Budget& budget) {
    Canvas result; int count = 0;
    for (const auto& c : canvases(s, budget)) if (c.kind == "2d") { result = c; ++count; }
    require(count == 1 && receiverAlive(s, result), "CURRENT_2D_NOT_UNIQUE"); return result;
}
QJsonObject mappingWithoutChecks(QJsonObject value) { value.remove("checked_actions"); return value; }
} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_cut_qt_atomic_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport)
int __cdecl mdqt_cut_qt_atomic_json(std::uint32_t pid, std::uint64_t creation, const char* input, unsigned length,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!input || !length || length > 16384 || !output || !written || !needed || cap < output_capacity || cap > 1048576) return -1;
    if (!processMatches(pid, creation)) return -2;
    auto* app = QCoreApplication::instance(); if (!app || QThread::currentThread() != app->thread()) return -3;
    if (running) return -4; running = true; struct Busy { ~Busy() { running = false; } } busy;
    const auto batch_began = GetTickCount64();
    Run run; int triggers = 0; bool action_returned = false;
    qint64 dispatch_elapsed_before_cut_ms = 0, cut_action_elapsed_ms = 0;
    std::optional<Budget> post_cut_budget;
    QJsonArray stages;
    QJsonObject report{{"status", "REJECTED"}, {"verified", false}, {"effects_started", false}, {"requires_recovery", false},
        {"semantic_line_selection_verified", false}, {"native_partition_verified", false}, {"native_mm_mapping_verified", false},
        {"geometry_preserved_verified", false}, {"hard_callback_timeout", false}, {"native_faults_contained", false},
        {"single_native_batch", true}, {"SDK_calls", 0}, {"SDK_reads_between_click_and_cut", 0}, {"Python_callbacks_in_batch", 0},
        {"event_pump_calls", 0}, {"automatic_retry_allowed", false}};
    try {
        QJsonParseError error{}; const auto doc = QJsonDocument::fromJson(QByteArray(input, int(length)), &error);
        require(error.error == QJsonParseError::NoError && doc.isObject(), "INVALID_JSON");
        const auto request = doc.object(); const auto op = request.value("operation").toString(); report.insert("operation", op);
        require(op == "inspect" || op == "select_and_cut", "UNKNOWN_OPERATION");
        Budget budget(budgetValue(request)); report.insert("modules", modules());
        report.insert("cooperative_budget_ms", int(budget.milliseconds));
        const Scope scope = currentScope(budget, pid); const Canvas canvas = canvas2d(scope, budget);
        const auto before = snapshot(scope, canvas); const auto p0 = physical(scope); const Mode original_mode = mode(scope, budget);
        require(backgroundSafe(p0) && original_mode.index == 1 && !canvas.scene->mouseGrabberItem(), "MODE1_IDLE_BACKGROUND_REQUIRED");
        const Actions original_actions = actions(scope, budget, true);
        report.insert("before", before); report.insert("physical_before", physicalJson(p0));
        report.insert("mode_before", original_mode.index); report.insert("Cut_enabled_before_pair", original_actions.cut->isEnabled());
        if (op == "inspect") {
            require(keysOnly(request, {"operation", "budget_ms"}), "UNKNOWN_PARAMETERS");
        } else {
            require(!recovery_required, "BRIDGE_RECOVERY_REQUIRED");
            require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "point"}), "UNKNOWN_PARAMETERS");
            require(request.value("native_view_ref").toString() == canvas.token
                && request.value("view_fingerprint") == before.value("view_fingerprint"), "STALE_VIEW_REFERENCE");
            const auto point = request.value("point").toArray();
            require(point.size() == 2 && point[0].isDouble() && point[1].isDouble(), "INVALID_POINT");
            const Point hit = mapped(canvas, QPointF(point[0].toDouble(), point[1].toDouble()));
            const auto observe_pair = [&]() {
                require(receiverAlive(scope, canvas) && samePhysical(p0, physical(scope)), "PAIR_RECEIVER_OR_PHYSICAL_CHANGED");
                const auto current_mode = mode(scope, budget);
                require(sameModeRefs(original_mode, current_mode) && current_mode.index == 1
                    && mappingWithoutChecks(before.value("mapping").toObject())
                        == mappingWithoutChecks(snapshot(scope, canvas).value("mapping").toObject()), "PAIR_MODE_OR_VIEW_CHANGED");
            };
            run.down = run.last = hit; ReleaseGuard release(scope, canvas, run, Qt::NoModifier);
            try {
                observe_pair(); budget.check();
                send(canvas, run, "line_press", QEvent::GraphicsSceneMousePress, hit, Qt::NoModifier); stages.append("line_press");
                observe_pair(); // Release is mandatory even when the cooperative deadline passed.
                send(canvas, run, "line_release", QEvent::GraphicsSceneMouseRelease, hit, Qt::NoModifier); stages.append("line_release");
                release.finish(); observe_pair();
                require(run.calls == 2 && run.release_returned && !run.pending && run.cleanup_scope && !run.exception
                    && run.nonspontaneous && !canvas.scene->mouseGrabberItem(), "PAIR_CLEANUP_UNVERIFIED");
                report.insert("line_pair_cleanup_verified", true);
                // Only public Qt guards between the pair and the fixed action.
                // No SDK/identity/export/selection read, Python callback or pump.
                budget.check(); const Actions fresh = actions(scope, budget, false); const Mode fresh_mode = mode(scope, budget);
                require(sameActions(original_actions, fresh) && sameModeRefs(original_mode, fresh_mode) && fresh_mode.index == 1
                    && receiverAlive(scope, canvas) && samePhysical(p0, physical(scope))
                    && !canvas.scene->mouseGrabberItem(), "PRE_CUT_FIXED_SCOPE_CHANGED");
                const auto cut_began = GetTickCount64();
                require(cut_began - budget.began < budget.milliseconds, "COOPERATIVE_BUDGET_EXCEEDED");
                dispatch_elapsed_before_cut_ms = qint64(cut_began - budget.began);
                ++triggers;
                try { fresh.cut->trigger(); }
                catch (...) {
                    cut_action_elapsed_ms = qint64(GetTickCount64() - cut_began);
                    throw;
                }
                action_returned = true;
                // MD handles Cut synchronously. After its return, only the
                // fixed read-only postconditions get a fresh cooperative window.
                post_cut_budget.emplace(budget.milliseconds);
                cut_action_elapsed_ms = qint64(post_cut_budget->began - cut_began);
                const auto after_mode = mode(scope, *post_cut_budget);
                const auto after_actions = actions(scope, *post_cut_budget, true);
                require(sameModeRefs(original_mode, after_mode) && (after_mode.index == 0 || after_mode.index == 1)
                    && sameActions(original_actions, after_actions), "POST_CUT_MODE_OR_ACTION_CHANGED");
                report.insert("mode_after", after_mode.index);
            } catch (...) { release.finish(); throw; }
            report.insert("dispatch_completed", true);
        }
        const auto& readback_budget = post_cut_budget ? *post_cut_budget : budget;
        readback_budget.check();
        require(receiverAlive(scope, canvas) && samePhysical(p0, physical(scope)) && !canvas.scene->mouseGrabberItem(), "POST_BATCH_SCOPE_CHANGED");
        const auto after = snapshot(scope, canvas); auto a = before.value("mapping").toObject(), b = after.value("mapping").toObject();
        a.remove("checked_actions"); b.remove("checked_actions"); a.remove("drag_mode"); b.remove("drag_mode");
        require(a == b, "POST_BATCH_VIEW_CHANGED");
        const auto p1 = physical(scope);
        require(samePhysical(p0, p1), "POST_BATCH_PHYSICAL_CHANGED");
        readback_budget.check();
        report.insert("after", after); report.insert("physical_after", physicalJson(p1));
        report.insert("physical_scope_verified", true); report.insert("view_scope_verified", true);
        report.insert("local_technical_cleanup_verified", true); report.insert("cursor_position_observational", true);
        report.insert("status", "SUCCEEDED"); report.insert("verified", true);
    } catch (const Reject& e) { report.insert("error", QJsonObject{{"code", e.code}, {"message", e.code}}); }
      catch (...) { run.exception = true; report.insert("error", QJsonObject{{"code", "CPP_EXCEPTION"}, {"message", "Native faults not contained"}}); }
    const bool effects = run.calls > 0 || triggers > 0, ok = report.value("status") == "SUCCEEDED";
    const auto batch_ended = GetTickCount64();
    report.insert("elapsed_ms", qint64(batch_ended - batch_began));
    if (triggers == 1) {
        stages.append("actionCutPattern2D");
        report.insert("dispatch_elapsed_before_cut_ms", dispatch_elapsed_before_cut_ms);
        report.insert("Cut_action_elapsed_ms", cut_action_elapsed_ms);
    }
    if (post_cut_budget) {
        report.insert("post_cut_readback_budget_ms", int(post_cut_budget->milliseconds));
        report.insert("post_cut_readback_elapsed_ms", qint64(batch_ended - post_cut_budget->began));
    }
    report.insert("event_calls", run.calls); report.insert("events", run.events); report.insert("stages", stages);
    report.insert("QAction_trigger_calls", triggers); report.insert("Cut_action_returned", action_returned);
    report.insert("press_sent", run.press); report.insert("release_sent", run.release); report.insert("release_returned", run.release_returned);
    report.insert("press_outstanding", run.pending); report.insert("paired_release_verified", run.press && run.release && run.release_returned && !run.pending && run.cleanup_scope);
    report.insert("scene_ungrab_calls", run.ungrab ? 1 : 0); report.insert("all_events_nonspontaneous", run.nonspontaneous);
    report.insert("effects_started", effects); auto counts = counters(); counts.insert("QAction_trigger_calls", triggers); report.insert("controller_calls", counts);
    if (effects && !ok) { recovery_required = true; report.insert("status", "PARTIAL"); report.insert("requires_recovery", true); }
    try { return write(report, ok ? 0 : effects ? -10 : -5, output, cap, written, needed); }
    catch (...) { if (effects) recovery_required = true; return -8; } // No output/replay; core treats invoked unknown as recovery.
}
