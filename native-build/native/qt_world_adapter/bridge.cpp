// Current 3D typed primitives only. No world-mm/angle/hit contract is inferred.
#include "canvas_common.inc"

struct MoveTool {
    QPointer<QAction> action;
    QPointer<QActionGroup> group;
};
MoveTool moveTool(const Scope& s, Budget& budget) {
    MoveTool result; int matches = 0;
    require(s.owner->children().size() <= 5000, "ACTION_LIMIT");
    for (QObject* o : s.owner->children()) {
        budget.check();
        if (o && o->objectName() == "actionMove_Pattern") { ++matches; result.action = qobject_cast<QAction*>(o); }
    }
    auto a = result.action;
    require(matches == 1 && a && a->parent() == s.owner && a->thread() == s.app->thread()
        && exact(a, "QAction", "actionMove_Pattern") && a->text() == "Select/Move"
        && a->isEnabled() && a->isCheckable() && a->isChecked(), "SELECT_MOVE_3D_NOT_CURRENT");
    result.group = a->actionGroup();
    require(!result.group || (result.group->thread() == s.app->thread() && result.group->isEnabled()), "MOVE_GROUP_INVALID");
    return result;
}
bool sameTool(const MoveTool& a, const MoveTool& b) {
    return a.action && b.action && a.action == b.action && a.group == b.group;
}
QJsonArray gizmos(const Scope& s, Budget& budget) {
    QJsonArray result;
    for (const char* name : {"action_World_Coordinate_Gizmo", "action_Local_Coordinate_Gizmo", "action_Screen_Coordinate_Gizmo"}) {
        QPointer<QAction> action; int count = 0;
        for (QObject* o : s.owner->children()) { budget.check(); if (o && o->objectName() == QLatin1StringView(name)) { ++count; action = qobject_cast<QAction*>(o); } }
        QJsonObject row{{"object_name", name}, {"match_count", count}, {"triggered", false}};
        if (count == 1 && action && action->parent() == s.owner && action->thread() == s.app->thread()) {
            require(action->text().size() <= 256, "ACTION_TEXT_LIMIT");
            row.insert("text", action->text()); row.insert("enabled", action->isEnabled());
            row.insert("checkable", action->isCheckable()); row.insert("checked", action->isChecked());
        }
        result.append(row);
    }
    return result;
}
QJsonObject gridPropertiesAction(const Scope& scope, Budget& budget) {
    constexpr auto name = "actionPattern_Editor_Property";
    const auto& children = scope.owner->children();
    require(children.size() <= 5000, "ACTION_LIMIT");
    QPointer<QAction> action;
    int matches = 0;
    for (QObject* child : children) {
        budget.check();
        if (child && child->objectName() == QLatin1StringView(name)) {
            ++matches;
            action = qobject_cast<QAction*>(child);
        }
    }
    QJsonObject result{{"object_name", name}, {"match_count", matches}, {"identity_verified", false}, {"triggered", false}};
    if (matches != 1 || !action || action->parent() != scope.owner || action->thread() != scope.app->thread()
        || !exact(action, "QAction", name)) return result;
    const auto text = action->text(), tooltip = action->toolTip();
    const auto shortcut = action->shortcut().toString(QKeySequence::PortableText);
    require(text.size() <= 256 && tooltip.size() <= 256 && shortcut.size() <= 256, "ACTION_TEXT_LIMIT");
    result.insert("text", text);
    result.insert("tooltip", tooltip);
    result.insert("shortcut", shortcut);
    result.insert("enabled", action->isEnabled());
    result.insert("checked", action->isChecked());
    result.insert("identity_verified", text == "Grid Properties" && tooltip == "Grid Properties ( F12 )" && !action->isCheckable());
    return result;
}
Canvas current3d(const Scope& s, Budget& budget) {
    Canvas c; int count = 0;
    for (const auto& item : canvases(s, budget)) if (item.kind == "3d") { c = item; ++count; }
    require(count == 1 && receiverAlive(s, c), "CURRENT_3D_NOT_UNIQUE");
    return c;
}
QPointF point(const QJsonValue& value) {
    require(value.isArray() && value.toArray().size() == 2, "INVALID_LOGICAL_POINT");
    const auto a = value.toArray();
    require(a[0].isDouble() && a[1].isDouble() && std::isfinite(a[0].toDouble()) && std::isfinite(a[1].toDouble()), "INVALID_LOGICAL_POINT");
    return {a[0].toDouble(), a[1].toDouble()};
}
double distance(QPointF a, QPointF b) { return std::hypot(a.x() - b.x(), a.y() - b.y()); }
struct KeyRun {
    int calls = 0, key = 0;
    bool press = false, release = false, returned = false, pending = false, exception = false;
    QJsonArray events;
};
void keyEvent(const Canvas& c, KeyRun& run, bool release) {
    QKeyEvent e(release ? QEvent::KeyRelease : QEvent::KeyPress, run.key, Qt::NoModifier, QString(), false, 1);
    QJsonObject row{{"stage", release ? "release" : "press"}, {"key", run.key},
        {"modifiers", 0}, {"auto_repeat", false}, {"count", 1}, {"sent", true}, {"returned", false}};
    ++run.calls;
    if (release) run.release = true; else { run.press = true; run.pending = true; }
    try {
        require(!e.spontaneous(), "SPONTANEOUS_EVENT_FORBIDDEN");
        const bool sent = QCoreApplication::sendEvent(c.view, &e);
        row.insert("returned", true); row.insert("send_return", sent); row.insert("accepted", e.isAccepted());
        row.insert("nonspontaneous", !e.spontaneous());
        if (release) { run.pending = false; run.returned = true; }
    } catch (...) { run.exception = true; run.events.append(row); throw; }
    run.events.append(row);
}
QJsonArray candidateMethods(QObject* o, Budget& budget) {
    QJsonArray result; const auto* meta = o->metaObject();
    require(meta && meta->methodCount() <= 2048, "METHOD_LIMIT");
    for (int i = 0; i < meta->methodCount(); ++i) {
        budget.check(); const auto method = meta->method(i);
        const QString signature = QString::fromLatin1(method.methodSignature()), name = signature.toLower();
        if (method.access() != QMetaMethod::Public || !(name.contains("world") || name.contains("project") || name.contains("rotate") || name.contains("translate") || name.contains("gizmo"))) continue;
        require(result.size() < 128 && signature.size() <= 512, "METHOD_LIMIT");
        result.append(QJsonObject{{"signature", signature}, {"method_type", int(method.methodType())}, {"invoked", false}});
    }
    return result;
}
} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_world_adapter_abi_version() noexcept { return 1; }
extern "C" __declspec(dllexport)
int __cdecl mdqt_world_adapter_json(std::uint32_t pid, std::uint64_t creation, const char* input, unsigned length,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!input || !length || length > 16384 || !output || !written || !needed || cap < output_capacity || cap > 1048576) return -1;
    QJsonObject report{{"verified", false}, {"effects_started", false}, {"requires_recovery", false},
        {"semantic_geometry_verified", false}, {"world_axis_mapping_verified", false}, {"world_transform_verified", false},
        {"rotation_angle_verified", false}, {"native_target_hit_verified", false}, {"counter_scope", "direct bridge calls only; nested MD handlers not enumerated"},
        {"zero_operation_counts", counters()}};
    Run mouse; KeyRun keys;
    Scope scope; Canvas canvas; Physical baseline; MoveTool tool;
    bool owns_running = false;
    auto counts = [&]() {
        const int events = mouse.calls + keys.calls;
        report.insert("event_calls", events); report.insert("scene_mouse_calls", mouse.calls); report.insert("key_event_calls", keys.calls);
        report.insert("effects_started", events > 0); report.insert("mouse_events", mouse.events); report.insert("key_events", keys.events);
        report.insert("paired_release_verified", keys.press ? keys.returned && !keys.pending : !mouse.press || (mouse.release_returned && !mouse.pending));
        report.insert("press_outstanding", mouse.pending || keys.pending);
        report.insert("local_scene_ungrab_calls", mouse.ungrab ? 1 : 0);
        report.insert("all_mouse_events_nonspontaneous", mouse.nonspontaneous);
    };
    try {
        require(!running && !recovery_required, "ADAPTER_BUSY_OR_RECOVERY_REQUIRED");
        require(processMatches(pid, creation), "PROCESS_IDENTITY_MISMATCH");
        report.insert("modules", modules());
        QJsonParseError error{}; const auto document = QJsonDocument::fromJson(QByteArray(input, static_cast<int>(length)), &error);
        require(error.error == QJsonParseError::NoError && document.isObject(), "INVALID_JSON");
        const auto request = document.object(); const QString operation = request.value("operation").toString();
        report.insert("operation", operation);
        require(operation == "inspect" || operation == "arrow_step" || operation == "gizmo_drag", "UNKNOWN_OPERATION");
        QStringList allowed{"operation", "budget_ms"};
        if (operation != "inspect") allowed << "native_view_ref" << "view_fingerprint";
        if (operation == "arrow_step") allowed << "direction";
        if (operation == "gizmo_drag") allowed << "start" << "activation" << "end" << "purpose";
        require(keysOnly(request, allowed), "UNEXPECTED_PARAMETER");
        Budget budget(budgetValue(request)); running = true; owns_running = true;
        scope = currentScope(budget, pid); canvas = current3d(scope, budget);
        baseline = physical(scope); require(backgroundSafe(baseline), "BACKGROUND_IDLE_SCOPE_REQUIRED");
        const auto before = snapshot(scope, canvas); report.insert("before", before);
        report.insert("physical_before", physicalJson(baseline)); report.insert("gizmo_context", gizmos(scope, budget));
        auto stable = [&]() {
            budget.check(); require(receiverAlive(scope, canvas) && samePhysical(baseline, physical(scope)), "PHYSICAL_OR_RECEIVER_SCOPE_CHANGED");
            if (operation != "inspect") require(sameTool(tool, moveTool(scope, budget)), "SELECT_MOVE_REFERENCE_CHANGED");
            require(snapshot(scope, canvas).value("mapping_fingerprint") == before.value("mapping_fingerprint"), "PUBLIC_VIEW_MAPPING_CHANGED");
        };
        if (operation == "inspect") {
            report.insert("grid_properties_action", gridPropertiesAction(scope, budget));
            report.insert("public_candidate_methods", candidateMethods(canvas.scene, budget));
            stable(); report.insert("after", snapshot(scope, canvas));
        } else {
            require(request.value("native_view_ref") == before.value("native_view_ref")
                && request.value("view_fingerprint") == before.value("view_fingerprint"), "STALE_3D_VIEW_REFERENCE");
            require(before.value("scene_item_grabber_absent").toBool(), "PREEXISTING_SCENE_GRAB");
            tool = moveTool(scope, budget);
            if (operation == "arrow_step") {
                const QString direction = request.value("direction").toString();
                if (direction == "left") keys.key = Qt::Key_Left; else if (direction == "right") keys.key = Qt::Key_Right;
                else if (direction == "up") keys.key = Qt::Key_Up; else if (direction == "down") keys.key = Qt::Key_Down;
                else require(false, "INVALID_DIRECTION");
                try { stable(); keyEvent(canvas, keys, false); stable(); keyEvent(canvas, keys, true); }
                catch (...) {
                    if (keys.pending && !keys.release && receiverAlive(scope, canvas) && samePhysical(baseline, physical(scope))) {
                        try { keyEvent(canvas, keys, true); } catch (...) { keys.exception = true; }
                    }
                    throw;
                }
                require(keys.calls == 2 && keys.returned && !keys.pending && !keys.exception, "KEY_PAIR_NOT_CLOSED");
            } else {
                const QString purpose = request.value("purpose").toString();
                require(purpose == "translation_gizmo" || purpose == "rotation_ring", "INVALID_PURPOSE");
                const auto start = point(request.value("start")), activation = point(request.value("activation")), end = point(request.value("end"));
                require(distance(start, activation) > 0 && distance(activation, end) > 0
                    && distance(start, activation) + distance(activation, end) <= 128.0, "BOUNDED_DRAG_PATH_REQUIRED");
                const auto a = mapped(canvas, start), b = mapped(canvas, activation), c = mapped(canvas, end);
                mouse.down = a; mouse.last = a;
                ReleaseGuard cleanup(scope, canvas, mouse, Qt::NoModifier, baseline);
                stable(); send(canvas, mouse, "hover", QEvent::GraphicsSceneMouseMove, a, Qt::NoModifier);
                stable(); send(canvas, mouse, "press", QEvent::GraphicsSceneMousePress, a, Qt::NoModifier);
                stable(); send(canvas, mouse, "activation", QEvent::GraphicsSceneMouseMove, b, Qt::NoModifier);
                stable(); send(canvas, mouse, "move", QEvent::GraphicsSceneMouseMove, c, Qt::NoModifier);
                stable(); send(canvas, mouse, "release", QEvent::GraphicsSceneMouseRelease, c, Qt::NoModifier);
                cleanup.finish();
                require(mouse.calls == 5 && mouse.release_returned && !mouse.pending && !mouse.exception && mouse.nonspontaneous && mouse.cleanup_scope, "MOUSE_CHAIN_NOT_CLOSED");
                report.insert("purpose", purpose); report.insert("profile", "NoMod_hover_press_activation_move_release_v1");
            }
            stable(); require(canvas.scene->mouseGrabberItem() == nullptr, "SCENE_GRAB_NOT_CLEARED");
            report.insert("after", snapshot(scope, canvas));
        }
        report.insert("physical_after", physicalJson(physical(scope)));
        report.insert("physical_state_unchanged", true); report.insert("dispatch_completed", true);
        report.insert("status", "SUCCEEDED"); report.insert("verified", true);
        counts(); running = false;
        return write(report, 0, output, cap, written, needed);
    } catch (const Reject& e) {
        counts(); const bool changed = mouse.calls || keys.calls;
        report.insert("error", e.code); report.insert("status", changed ? "PARTIAL" : "REJECTED");
        report.insert("requires_recovery", changed); if (changed) recovery_required = true;
        if (owns_running) running = false;
        return write(report, changed ? 2 : 1, output, cap, written, needed);
    } catch (...) {
        counts(); const bool changed = mouse.calls || keys.calls;
        report.insert("error", "CPP_EXCEPTION"); report.insert("status", changed ? "UNKNOWN" : "REJECTED");
        report.insert("requires_recovery", changed); if (changed) recovery_required = true;
        if (owns_running) running = false;
        return write(report, changed ? -2 : 1, output, cap, written, needed);
    }
}
