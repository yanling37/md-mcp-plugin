// Independent adapter. The copied common helpers have a frozen local provenance.
#include "canvas_common.inc"
#include "capture_gl.inc"

constexpr const char* tool_names[] = {"action_TransformShape", "action_PatternEdit", "action_Edit_Curve_Point"};
constexpr const char* tool_texts[] = {"Transform Pattern", "Edit Pattern", "Edit Curve Point"};
constexpr const char* tool_keys[] = {"transform_pattern", "pattern_edit", "edit_curve_point"};
constexpr const char* fit_all_action_name = "actionShow_All_Patterns_in_Window";

struct Tools {
    QPointer<QAction> actions[3];
    QPointer<QActionGroup> group;
    int current = -1;
    bool verified = false;
    QJsonArray rows;
};
Tools tools(const Scope& scope, Budget& budget) {
    Tools t; int matches[3]{};
    require(scopeAlive(scope) && scope.owner->children().size() <= 5000, "TOOL_OWNER_INVALID");
    for (QObject* o : scope.owner->children()) {
        budget.check(); require(o && o->thread() == scope.app->thread(), "TOOL_OWNER_CHANGED");
        for (int i = 0; i < 3; ++i) if (o->objectName() == QLatin1StringView(tool_names[i])) {
            ++matches[i]; t.actions[i] = qobject_cast<QAction*>(o);
        }
    }
    bool valid = true;
    for (int i = 0; i < 3; ++i) {
        const auto a = t.actions[i];
        const bool ok = matches[i] == 1 && a && a->parent() == scope.owner && a->thread() == scope.app->thread()
            && exact(a, "QAction", tool_names[i]) && a->text() == QLatin1StringView(tool_texts[i])
            && a->isEnabled() && a->isCheckable();
        valid = valid && ok;
        t.rows.append(QJsonObject{{"tool", tool_keys[i]}, {"object_name", tool_names[i]}, {"match_count", matches[i]},
            {"contract_verified", ok}, {"enabled", a ? a->isEnabled() : false},
            {"checkable", a ? a->isCheckable() : false}, {"checked", a ? a->isChecked() : false}});
    }
    if (!valid) return t;
    t.group = t.actions[0]->actionGroup();
    if (!t.group || t.group->thread() != scope.app->thread() || t.group->parent() != scope.owner
        || !exact(t.group, "QActionGroup", "") || !t.group->isEnabled() || !t.group->isExclusive()
        || t.group->exclusionPolicy() != QActionGroup::ExclusionPolicy::Exclusive
        || t.actions[1]->actionGroup() != t.group || t.actions[2]->actionGroup() != t.group) return t;
    const auto members = t.group->actions();
    if (members.size() > 5000) return t;
    int membership[3]{}, checked = 0;
    for (QAction* a : members) {
        budget.check(); if (!a || a->thread() != scope.app->thread()) return t;
        for (int i = 0; i < 3; ++i) if (a == t.actions[i]) { ++membership[i]; if (a->isChecked()) t.current = i; }
        if (a->isChecked()) ++checked;
    }
    t.verified = membership[0] == 1 && membership[1] == 1 && membership[2] == 1 && checked == 1
        && t.current >= 0 && t.group->checkedAction() == t.actions[t.current];
    return t;
}
QJsonObject toolJson(const Tools& t) {
    return {{"verified", t.verified}, {"current_mode", t.current}, {"actions", t.rows},
        {"current_tool", t.current >= 0 ? QJsonValue(tool_keys[t.current]) : QJsonValue()},
        {"restoration_claimed", false}};
}
bool sameToolObjects(const Tools& a, const Tools& b) {
    if (!a.verified || !b.verified || !a.group || a.group != b.group) return false;
    for (int i = 0; i < 3; ++i) if (!a.actions[i] || a.actions[i] != b.actions[i]) return false;
    return true;
}
Canvas current2d(const Scope& scope, Budget& budget) {
    Canvas result; int count = 0;
    for (const auto& c : canvases(scope, budget)) if (c.kind == "2d") { result = c; ++count; }
    require(count == 1 && receiverAlive(scope, result), "CURRENT_2D_VIEW_NOT_UNIQUE");
    return result;
}
bool candidateName(const QString& s) {
    const auto value = s.toLower();
    for (const char* key : {"zoom", "fit", "frame", "project", "selection", "select", "map", "millimet", "coordinate", "transform"})
        if (value.contains(QLatin1StringView(key))) return true;
    return false;
}
QJsonObject metaCatalog(QObject* o, const char* role, Budget& budget, int& reads) {
    require(o && o->thread() == QThread::currentThread(), "META_OBJECT_LOST");
    const auto* meta = o->metaObject();
    require(meta && meta->propertyCount() <= 256 && meta->methodCount() <= 1024, "META_COUNT_LIMIT");
    QJsonArray properties, methods;
    for (int i = 0; i < meta->propertyCount(); ++i) {
        budget.check(); const auto p = meta->property(i);
        const QString name = QString::fromLatin1(p.name()), type = QString::fromLatin1(p.typeName() ? p.typeName() : "");
        require(name.size() <= 256 && type.size() <= 256, "META_TEXT_LIMIT");
        QJsonObject row{{"index", i}, {"name", name}, {"type_name", type}, {"readable", p.isReadable()},
            {"writable", p.isWritable()}, {"constant", p.isConstant()}, {"has_notify", p.hasNotifySignal()},
            {"candidate_name", candidateName(name)}, {"value_read", false}};
        // No arbitrary MD getter. Only scalar properties declared by standard Qt
        // classes and a fixed small getter list are read; all others are descriptors.
        const auto* declaring = p.enclosingMetaObject();
        const QString declared = declaring ? QString::fromLatin1(declaring->className()) : QString();
        const bool standard = declared == "QObject" || declared == "QWidget" || declared == "QGraphicsView" || declared == "QGraphicsScene";
        const bool fixed = name == "objectName" || name == "enabled" || name == "visible" || name == "interactive" || name == "dragMode";
        const int id = p.metaType().id();
        if (standard && fixed && p.isReadable() && (id == QMetaType::Bool || id == QMetaType::Int || id == QMetaType::QString)) {
            const QVariant v = p.read(o); ++reads;
            require(v.isValid(), "PUBLIC_META_GETTER_FAILED");
            if (id == QMetaType::QString) require(v.toString().size() <= 256, "META_TEXT_LIMIT");
            row.insert("value", QJsonValue::fromVariant(v)); row.insert("value_read", true);
        } else row.insert("value_not_read", "UNREVIEWED_OR_NONSCALAR_META_GETTER");
        row.insert("declared_by", declared); properties.append(row);
    }
    int omitted = 0;
    for (int i = 0; i < meta->methodCount(); ++i) {
        budget.check(); const auto m = meta->method(i);
        if (m.access() != QMetaMethod::Public) { ++omitted; continue; }
        require(methods.size() < 512, "PUBLIC_METHOD_LIMIT");
        const QString signature = QString::fromLatin1(m.methodSignature());
        const QString type = QString::fromLatin1(m.typeName() ? m.typeName() : "");
        require(signature.size() <= 512 && type.size() <= 256, "META_TEXT_LIMIT");
        methods.append(QJsonObject{{"index", i}, {"signature", signature}, {"return_type", type},
            {"method_type", int(m.methodType())}, {"access", int(m.access())},
            {"candidate_name", candidateName(signature)}, {"invoked", false}});
    }
    return {{"role", role}, {"object", description(o)}, {"properties", properties}, {"public_methods", methods},
        {"nonpublic_method_count_omitted", omitted}, {"complete_within_declared_scope", true},
        {"public_declaration_is_callable_proof", false}};
}
QJsonArray zoomActions(const Scope& s, Budget& budget) {
    QJsonArray result;
    require(s.owner->children().size() <= 5000, "ACTION_LIMIT");
    for (QObject* o : s.owner->children()) {
        budget.check(); auto* a = qobject_cast<QAction*>(o); if (!a) continue;
        require(a->thread() == s.app->thread() && a->parent() == s.owner, "ACTION_REFERENCE_CHANGED");
        const QString name = a->objectName(), text = a->text(), lower = (name + " " + text).toLower();
        if (!(lower.contains("zoom") || lower.contains("fit") || lower.contains("frame"))) continue;
        const QString shortcut = a->shortcut().toString(QKeySequence::PortableText);
        require(result.size() < 128 && name.size() <= 256 && text.size() <= 256 && shortcut.size() <= 256, "ACTION_METADATA_LIMIT");
        result.append(QJsonObject{{"object_name", name}, {"text", text}, {"enabled", a->isEnabled()},
            {"checkable", a->isCheckable()}, {"checked", a->isChecked()}, {"shortcut", shortcut}, {"triggered", false}});
    }
    return result;
}
QJsonObject selection(const Canvas& c) {
    const auto items = c.scene->selectedItems(); require(items.size() <= 64, "PUBLIC_SELECTION_LIMIT");
    // Public Qt selectedItems only. Opaque digest; no pointer/role/model data is
    // returned, and no old item pointer is dereferenced after a handler runs.
    std::vector<QString> values;
    for (QGraphicsItem* item : items) { require(item != nullptr, "PUBLIC_SELECTION_CHANGED"); values.push_back(QString::number(reinterpret_cast<quintptr>(item), 16)); }
    std::sort(values.begin(), values.end()); QJsonArray ids;
    for (const auto& value : values) ids.append(value);
    return {{"selected_items_count", items.size()}, {"selection_fingerprint", hash(QJsonObject{{"view", c.token}, {"opaque", ids}})},
        {"native_line_identity_verified", false}, {"item_roles_read", false}, {"raw_pointers_returned", false}};
}
bool sameMapping(const QJsonObject& a, const QJsonObject& b) { return a.value("mapping_fingerprint") == b.value("mapping_fingerprint"); }

QPointer<QAction> fitAllAction(const Scope& scope, Budget& budget) {
    require(scopeAlive(scope) && scope.owner->children().size() <= 5000, "FIT_ALL_OWNER_INVALID");
    QPointer<QAction> action;
    int matches = 0;
    for (QObject* child : scope.owner->children()) {
        budget.check();
        require(child && child->thread() == scope.app->thread(), "FIT_ALL_OWNER_CHANGED");
        if (child->objectName() == QLatin1StringView(fit_all_action_name)) {
            ++matches;
            action = qobject_cast<QAction*>(child);
        }
    }
    require(matches == 1 && action && action->parent() == scope.owner && action->isEnabled()
        && !action->isCheckable(), "FIT_ALL_ACTION_UNAVAILABLE");
    return action;
}

constexpr const char* snap_names[] = {"action_ActivateSnapping", "action_Smart_Guide"};
constexpr const char* snap_texts[] = {"Snap to Pattern", "Smart Guide"};
struct SnapRefs {
    QPointer<QAction> action[2]; QPointer<QActionGroup> group[2]; bool had_group[2]{};
};
SnapRefs snapRefs(const Scope& s, const Tools& mode, bool restoring = false) {
    require(scopeAlive(s) && s.owner->children().size() <= 5000, "SNAP_OWNER_INVALID");
    SnapRefs result; int matches[2]{};
    for (QObject* o : s.owner->children()) for (int i = 0; i < 2; ++i)
        if (o && o->objectName() == QLatin1StringView(snap_names[i])) { ++matches[i]; result.action[i] = qobject_cast<QAction*>(o); }
    for (int i = 0; i < 2; ++i) {
        const auto a = result.action[i];
        require(matches[i] == 1 && a && exact(a, "QAction", snap_names[i]) && a->parent() == s.owner
            && a->thread() == s.app->thread() && a->text() == QLatin1StringView(snap_texts[i])
            && (restoring || a->isEnabled()) && a->isCheckable(), "FIXED_SNAP_ACTION_NOT_READY");
        result.group[i] = a->actionGroup(); result.had_group[i] = !result.group[i].isNull();
        if (const auto g = result.group[i]) {
            require(g != mode.group && g->thread() == s.app->thread() && g->parent() == s.owner
                && !std::strcmp(g->metaObject()->className(), "QActionGroup") && (restoring || g->isEnabled())
                && !g->isExclusive() && g->exclusionPolicy() == QActionGroup::ExclusionPolicy::None,
                "SNAP_GROUP_NOT_INDEPENDENT_NONEXCLUSIVE");
            const auto members = g->actions(); require(members.size() > 0 && members.size() <= 64, "SNAP_GROUP_LIMIT");
            int membership = 0;
            for (auto* member : members) {
                require(member && member->thread() == s.app->thread() && member->actionGroup() == g, "SNAP_GROUP_REFERENCE_CHANGED");
                if (member == a) ++membership;
            }
            require(membership == 1, "SNAP_GROUP_MEMBERSHIP_INVALID");
        }
    }
    require(result.action[0] != result.action[1], "SNAP_ACTIONS_NOT_DISTINCT"); return result;
}
bool sameSnapRefs(const SnapRefs& a, const SnapRefs& b) {
    for (int i = 0; i < 2; ++i) if (!a.action[i] || a.action[i] != b.action[i]
        || a.had_group[i] != b.had_group[i] || a.group[i] != b.group[i]
        || (a.had_group[i] && !a.group[i])) return false;
    return true;
}
QJsonObject withoutSnapActions(QJsonObject mapping) {
    const auto all = mapping.value("checked_actions").toArray(); QJsonArray filtered;
    for (const auto value : all) {
        const auto name = value.toObject().value("object_name").toString();
        if (name != QLatin1StringView(snap_names[0]) && name != QLatin1StringView(snap_names[1])) filtered.append(value);
    }
    mapping.insert("checked_actions", filtered); return mapping;
}
class SnapRestoreGuard {
public:
    SnapRestoreGuard(const Scope& scope, const Tools& mode, int& triggers, QJsonObject& report)
        : scope_(scope), mode_(mode), refs_(snapRefs(scope, mode)), triggers_(triggers), report_(report) {
        for (int i = 0; i < 2; ++i) before_[i] = expected_[i] = refs_.action[i]->isChecked();
    }
    ~SnapRestoreGuard() noexcept { restore(); }
    void disable(Budget& budget, const Canvas& canvas, const Physical& physical_before, const QJsonObject& original) {
        for (int i = 0; i < 2; ++i) {
            budget.check(); checkExpected();
            require(receiverAlive(scope_, canvas) && samePhysical(physical_before, physical(scope_))
                && withoutSnapActions(snapshot(scope_, canvas).value("mapping").toObject())
                    == withoutSnapActions(original.value("mapping").toObject()), "SNAP_PRETRIGGER_SCOPE_CHANGED");
            if (before_[i]) {
                ++triggers_; ++disabled_calls_[i]; refs_.action[i]->trigger(); expected_[i] = false; checkExpected();
            }
        }
        off_ = !refs_.action[0]->isChecked() && !refs_.action[1]->isChecked();
        require(off_, "SNAP_DISABLE_NOT_VERIFIED"); publish();
    }
    void restore() noexcept {
        if (finished_) return; finished_ = true; restored_ = false;
        try {
            attempted_ = true;
            require(sameSnapRefs(refs_, snapRefs(scope_, mode_, true)), "SNAP_RESTORE_REFERENCES_CHANGED");
            // Cleanup, like paired Release, is allowed on the same live GUI
            // owner even if a physical observation became unsafe meanwhile.
            // It never sets OS focus/input; the final physical guard still fails.
            for (int i = 1; i >= 0; --i) {
                try { if (refs_.action[i] && refs_.action[i]->isChecked() != before_[i]) {
                    require(sameSnapRefs(refs_, snapRefs(scope_, mode_, true)) && refs_.action[i]->isEnabled(), "SNAP_RESTORE_ACTION_NOT_READY");
                    ++triggers_; ++restore_calls_[i]; refs_.action[i]->trigger();
                } } catch (...) { /* Still attempt the other original fixed action. */ }
            }
            restored_ = sameSnapRefs(refs_, snapRefs(scope_, mode_, true));
            for (int i = 0; i < 2; ++i) restored_ = restored_ && refs_.action[i] && refs_.action[i]->isChecked() == before_[i];
        } catch (...) { restored_ = false; }
        try { publish(); } catch (...) { restored_ = false; }
    }
    bool restored() const { return restored_; }
private:
    void checkExpected() {
        require(sameSnapRefs(refs_, snapRefs(scope_, mode_)), "SNAP_REFERENCE_CHANGED");
        for (int i = 0; i < 2; ++i) require(refs_.action[i]->isChecked() == expected_[i], "SNAP_ACTION_COUPLED_OR_UNEXPECTED_STATE");
    }
    void publish() {
        QJsonArray actions;
        for (int i = 0; i < 2; ++i) {
            QJsonValue group;
            if (refs_.group[i]) group = QJsonObject{{"class_name", "QActionGroup"}, {"object_name", refs_.group[i]->objectName()},
                {"exclusive", refs_.group[i]->isExclusive()}, {"current_owner_parent", refs_.group[i]->parent() == scope_.owner}};
            actions.append(QJsonObject{{"object_name", snap_names[i]}, {"text", snap_texts[i]},
                {"before_checked", before_[i]}, {"off_checked", off_ ? QJsonValue(false) : QJsonValue()},
                {"after_checked", finished_ && refs_.action[i] ? QJsonValue(refs_.action[i]->isChecked()) : QJsonValue()},
                {"disable_trigger_calls", disabled_calls_[i]}, {"restore_trigger_calls", restore_calls_[i]}, {"group", group}});
        }
        report_.insert("snap_settings", QJsonObject{{"schema", "md-fixed-bezier-unsnapped-actions-v1"}, {"actions", actions},
            {"disabled_before_gesture", off_}, {"restoration_attempted", attempted_}, {"original_states_restored", restored_},
            {"direct_trigger_calls", disabled_calls_[0] + disabled_calls_[1] + restore_calls_[0] + restore_calls_[1]},
            {"setChecked_calls", 0}, {"setEnabled_calls", 0}});
    }
    const Scope& scope_; const Tools& mode_; SnapRefs refs_; int& triggers_; QJsonObject& report_;
    bool before_[2]{}, expected_[2]{}, off_ = false, finished_ = false, attempted_ = false, restored_ = false;
    int disabled_calls_[2]{}, restore_calls_[2]{};
};
struct CutActions { QPointer<QAction> cut, move; QPointer<QActionGroup> cut_group, move_group; };
CutActions selectedCut(const Scope& s, Budget& budget, bool after = false) {
    QPointer<QAction> cut, move; int nc = 0, nm = 0;
    for (QObject* o : s.owner->children()) {
        budget.check(); require(o && o->thread() == s.app->thread(), "CUT_ACTION_THREAD_CHANGED");
        if (o->objectName() == "actionCutPattern2D") { ++nc; cut = qobject_cast<QAction*>(o); }
        if (o->objectName() == "actionMove_Pattern") { ++nm; move = qobject_cast<QAction*>(o); }
    }
    require(nc == 1 && nm == 1 && cut && move && exact(cut, "QAction", "actionCutPattern2D")
        && cut->parent() == s.owner && (after || cut->isEnabled()) && !cut->isCheckable() && !cut->isChecked()
        && cut->text() == "Cut" && cut->toolTip() == "Cut" && cut->shortcutContext() == Qt::WindowShortcut
        && exact(move, "QAction", "actionMove_Pattern") && move->parent() == s.owner && move->isEnabled()
        && move->text() == "Select/Move" && move->isCheckable() && move->isChecked(), "SELECTED_CUT_ACTION_NOT_READY");
    CutActions result{cut, move, cut->actionGroup(), move->actionGroup()};
    for (const auto& g : {result.cut_group, result.move_group})
        require(!g || (g->thread() == s.app->thread() && g->isEnabled()), "CUT_GROUP_CHANGED");
    return result;
}
} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_scene_adapter_abi_version() noexcept { return 6; }
extern "C" __declspec(dllexport) unsigned __cdecl mdqt_scene_adapter_recovery_epoch() noexcept { return 1; }
extern "C" __declspec(dllexport)
int __cdecl mdqt_scene_adapter_json(std::uint32_t pid, std::uint64_t creation, const char* input, unsigned length,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0; if (needed) *needed = 0;
    if (!input || !length || length > 16384 || !output || !written || !needed || cap < output_capacity || cap > 1048576) return -1;
    if (!processMatches(pid, creation)) return -2;
    auto* app = QCoreApplication::instance(); if (!app || QThread::currentThread() != app->thread()) return -3;
    if (running) return -4;
    running = true; struct Busy { ~Busy() { running = false; } } busy;
    Run run; int triggers = 0, property_reads = 0, captures = 0, encodes = 0, wheels = 0, zoom_hover = 0;
    QJsonArray wheel_events; bool wheel_nonspontaneous = true;
    bool capture_started = false, capture_recovery = false;
    QJsonObject report{{"status", "REJECTED"}, {"verified", false}, {"effects_started", false}, {"requires_recovery", false},
        {"event_calls", 0}, {"semantic_geometry_verified", false}, {"native_mm_mapping_verified", false},
        {"native_handle_hit_verified", false}, {"native_cut_partition_verified", false},
        {"hard_callback_timeout", false}, {"native_faults_contained", false}, {"live_validation", "pending"}};
    try {
        QJsonParseError error{}; const auto doc = QJsonDocument::fromJson(QByteArray(input, int(length)), &error);
        require(error.error == QJsonParseError::NoError && doc.isObject(), "INVALID_JSON");
        const auto request = doc.object(); const QString op = request.value("operation").toString(); report.insert("operation", op);
        require(op == "inspect" || op == "capture" || op == "bezier_drag" || op == "bezier_drag_unsnapped" || op == "bezier_drag_precise_unsnapped" || op == "set_tool" || op == "fit_selected" || op == "fit_all" || op == "zoom_in_at" || op == "zoom_out_at" || op == "cut_selected", "UNKNOWN_OPERATION");
        Budget budget(budgetValue(request)); report.insert("modules", modules());
        const Scope scope = currentScope(budget, pid); const Canvas canvas = current2d(scope, budget);
        const auto before = snapshot(scope, canvas); const auto physical_before = physical(scope);
        require(backgroundSafe(physical_before), "BACKGROUND_INPUT_GUARD");
        report.insert("before", before); report.insert("physical_before", physicalJson(physical_before));
        const Tools original_tools = tools(scope, budget); report.insert("tools_before", toolJson(original_tools));
        const auto selected = selection(canvas); report.insert("selection_before", selected);
        if (op == "inspect") {
            require(keysOnly(request, {"operation", "budget_ms"}), "UNKNOWN_PARAMETERS");
            QJsonArray metadata;
            metadata.append(metaCatalog(scope.owner, "current_owner", budget, property_reads));
            metadata.append(metaCatalog(canvas.view, "current_2d_view", budget, property_reads));
            metadata.append(metaCatalog(canvas.viewport, "current_2d_viewport", budget, property_reads));
            metadata.append(metaCatalog(canvas.scene, "current_2d_scene", budget, property_reads));
            report.insert("metadata", metadata); report.insert("zoom_fit_frame_actions", zoomActions(scope, budget));
            report.insert("metadata_catalog_complete", true);
            report.insert("method_invocations", 0); report.insert("model_to_scene_method_callability_verified", false);
        } else {
            require(request.value("native_view_ref").toString() == canvas.token && request.value("view_fingerprint").toString() == before.value("view_fingerprint").toString(), "STALE_VIEW_REFERENCE");
            require(!recovery_required, "BRIDGE_RECOVERY_REQUIRED");
            if (op == "capture") {
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "png_path"}), "UNKNOWN_PARAMETERS");
                const QString path = request.value("png_path").toString(); const QFileInfo info(path);
                require(info.isAbsolute() && info.suffix() == "png" && !info.exists() && !info.isSymLink()
                    && info.dir().exists() && !QFileInfo(info.absolutePath()).isSymLink(), "INVALID_CORE_ARTIFACT_PATH");
                ++captures;
                const QImage image = capture_gl::capture(scope, canvas, budget, physical_before, before, report, capture_started, capture_recovery);
                require(!image.isNull() && image.width() > 0 && image.height() > 0 && image.width() <= 16384 && image.height() <= 16384
                    && qint64(image.width()) * image.height() <= 67108864, "FRAMEBUFFER_EMPTY_OR_OVERSIZED");
                require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope)) && sameMapping(before, snapshot(scope, canvas)), "FRAMEBUFFER_OBSERVATION_CHANGED");
                QFile file(path); require(file.open(QIODevice::WriteOnly | QIODevice::NewOnly), "ARTIFACT_ALREADY_EXISTS_OR_UNWRITABLE");
                ++encodes; const bool saved = image.save(&file, "PNG"); const bool flushed = file.flush(); file.close();
                require(saved && flushed && file.size() > 0 && file.size() <= 67108864, "PNG_WRITE_FAILED");
                require(file.open(QIODevice::ReadOnly), "PNG_REREAD_FAILED"); const auto bytes = file.readAll(); file.close();
                report.insert("image", QJsonObject{{"path", path}, {"sha256", QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex())},
                    {"byte_count", bytes.size()}, {"width", image.width()}, {"height", image.height()}, {"format", "PNG"},
                    {"viewport_size", before.value("mapping").toObject().value("viewport_size")},
                    {"device_pixel_ratio", before.value("mapping").toObject().value("device_pixel_ratio")},
                    {"view_fingerprint", before.value("view_fingerprint")}, {"native_view_ref", canvas.token}});
            } else if (op == "bezier_drag" || op == "bezier_drag_unsnapped" || op == "bezier_drag_precise_unsnapped") {
                const bool precise = op == "bezier_drag_precise_unsnapped";
                const bool unsnapped = op == "bezier_drag_unsnapped" || precise;
                const char* profile = precise ? "bezier_handle_precise_alt_move_unsnapped_v1"
                    : unsnapped ? "bezier_handle_alt_move_unsnapped_v1" : "bezier_handle_alt_move_v1";
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "start", "end", "profile"})
                    && request.value("profile").toString() == QLatin1StringView(profile), "INVALID_FIXED_PROFILE");
                require(original_tools.verified && original_tools.current == 2, "EDIT_CURVE_POINT_TOOL_REQUIRED");
                const auto parsePoint = [&](const char* key) { const auto a = request.value(key).toArray();
                    require(a.size() == 2 && a[0].isDouble() && a[1].isDouble(), "INVALID_POINTS"); return mapped(canvas, QPointF(a[0].toDouble(), a[1].toDouble())); };
                const Point start = parsePoint("start"), end = parsePoint("end"); require(start.logical != end.logical, "ZERO_LENGTH_DRAG");
                Point activation;
                if (precise) {
                    const int threshold = QApplication::startDragDistance();
                    require(threshold >= 0 && threshold <= 65535, "START_DRAG_DISTANCE_UNBOUNDED");
                    const int distance = std::clamp(threshold + 2, 12, 32);
                    const QPointF delta = end.logical - start.logical;
                    const double direction_length = std::hypot(delta.x(), delta.y());
                    require(std::isfinite(direction_length) && direction_length > 0, "INVALID_PRECISE_DIRECTION");
                    const QPointF point = start.logical + delta * (double(distance) / direction_length);
                    require(std::isfinite(point.x()) && std::isfinite(point.y()) && point.x() >= 0 && point.y() >= 0
                        && point.x() < canvas.viewport->width() && point.y() < canvas.viewport->height(), "ACTIVATION_POINT_OUTSIDE_VIEWPORT");
                    activation = mapped(canvas, point); // All points qualified before snap triggers/input.
                    report.insert("activation_point", xy(activation.logical)); report.insert("activation_distance_logical", distance);
                    report.insert("start_drag_distance_before", threshold);
                    report.insert("activation_rule", "max12_min32_startDragDistance_plus2_along_start_to_end");
                    report.insert("activation_point_rule_verified", true); report.insert("pointer_drag_activation_verified", false);
                }
                require(before.value("scene_item_grabber_absent").toBool(), "SCENE_ALREADY_GRABBED");
                // The default profile creates no snap guard and remains unchanged.
                std::unique_ptr<SnapRestoreGuard> snap;
                if (unsnapped) snap = std::make_unique<SnapRestoreGuard>(scope, original_tools, triggers, report);
                if (snap) snap->disable(budget, canvas, physical_before, before);
                const auto gesture_before = snap ? snapshot(scope, canvas) : before;
                if (snap) report.insert("gesture_observation_before", gesture_before);
                const auto observe = [&]() { require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope)), "RECEIVER_OR_PHYSICAL_STATE_CHANGED");
                    const Tools current = tools(scope, budget); require(sameToolObjects(original_tools, current) && current.current == 2 && sameMapping(gesture_before, snapshot(scope, canvas)), "VIEW_OR_TOOL_CHANGED"); };
                run.down = run.last = start; ReleaseGuard release(scope, canvas, run, Qt::AltModifier);
                try {
                    budget.check(); observe(); send(canvas, run, "hover", QEvent::GraphicsSceneMouseMove, start, Qt::NoModifier);
                    budget.check(); observe(); require(!canvas.scene->mouseGrabberItem(), "HOVER_CREATED_GRAB");
                    send(canvas, run, "press", QEvent::GraphicsSceneMousePress, start, Qt::NoModifier);
                    if (precise) {
                        budget.check(); observe(); send(canvas, run, "activation_move", QEvent::GraphicsSceneMouseMove, activation, Qt::AltModifier);
                        budget.check(); observe(); send(canvas, run, "final_move", QEvent::GraphicsSceneMouseMove, end, Qt::AltModifier);
                    } else {
                        budget.check(); observe(); send(canvas, run, "move", QEvent::GraphicsSceneMouseMove, end, Qt::AltModifier);
                    }
                    observe(); send(canvas, run, "release", QEvent::GraphicsSceneMouseRelease, end, Qt::AltModifier);
                    release.finish(); observe();
                    require(run.calls == (precise ? 5 : 4) && run.release_returned && !run.pending && run.cleanup_scope && !run.exception && run.nonspontaneous
                        && !canvas.scene->mouseGrabberItem(), "GESTURE_CLEANUP_FAILED");
                    if (snap) { snap->restore(); require(snap->restored() && sameMapping(before, snapshot(scope, canvas)), "SNAP_OR_ORIGINAL_VIEW_RESTORATION_FAILED"); }
                    report.insert("profile", profile); report.insert("dispatch_completed", true);
                } catch (...) { release.finish(); throw; }
            } else if (op == "zoom_in_at" || op == "zoom_out_at") {
                const bool zoom_out = op == "zoom_out_at";
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "point"}), "UNKNOWN_PARAMETERS");
                const auto xy_point = request.value("point").toArray();
                require(xy_point.size() == 2 && xy_point[0].isDouble() && xy_point[1].isDouble(), "INVALID_ZOOM_POINT");
                const Point point = mapped(canvas, QPointF(xy_point[0].toDouble(), xy_point[1].toDouble()));
                require(original_tools.verified && original_tools.current == 2, "EDIT_CURVE_POINT_TOOL_REQUIRED");
                require(canvas.scene && !canvas.scene->mouseGrabberItem(), "SCENE_ALREADY_GRABBED");
                QPointer<QScrollBar> horizontal = canvas.view->horizontalScrollBar(), vertical = canvas.view->verticalScrollBar();
                require(horizontal && vertical && horizontal != vertical && horizontal->thread() == scope.app->thread()
                    && vertical->thread() == scope.app->thread() && horizontal->orientation() == Qt::Horizontal
                    && vertical->orientation() == Qt::Vertical, "SCROLLBAR_REFS_INVALID");
                const auto prewheel = [&]() {
                    require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope))
                        && !canvas.scene->mouseGrabberItem(), "ZOOM_RECEIVER_OR_PHYSICAL_CHANGED");
                    const Tools fresh = tools(scope, budget);
                    require(sameToolObjects(original_tools, fresh) && fresh.current == 2
                        && sameMapping(before, snapshot(scope, canvas)) && mapped(canvas, point.logical).screen == point.screen,
                        "PREWHEEL_VIEW_OR_TOOL_CHANGED");
                };
                QGraphicsSceneMouseEvent hover(QEvent::GraphicsSceneMouseMove);
                hover.setWidget(canvas.viewport); hover.setScenePos(point.scene); hover.setLastScenePos(point.scene);
                hover.setScreenPos(point.screen); hover.setLastScreenPos(point.screen); hover.setButton(Qt::NoButton);
                hover.setButtons(Qt::NoButton); hover.setModifiers(Qt::NoModifier);
                hover.setSource(Qt::MouseEventSynthesizedByApplication); hover.setFlags(Qt::MouseEventFlags()); hover.setAccepted(false);
                QWheelEvent wheel(point.logical, QPointF(point.screen), QPoint(0, 0), QPoint(0, zoom_out ? 120 : -120),
                    Qt::NoButton, Qt::NoModifier, Qt::NoScrollPhase, false, Qt::MouseEventSynthesizedByApplication);
                wheel.setAccepted(false);
                require(!hover.spontaneous() && !wheel.spontaneous(), "NONSPONTANEOUS_ZOOM_EVENTS_REQUIRED");
                budget.check(); prewheel();
                QJsonObject hover_row{{"stage", "zoom_hover"}, {"event_type", "GraphicsSceneMouseMove"},
                    {"receiver", "QGraphicsScene/QtView2D"}, {"viewport_logical", xy(point.logical)},
                    {"scene", xy(point.scene)}, {"last_scene", xy(point.scene)}, {"screen", xy(point.screen)},
                    {"button", int(hover.button())}, {"buttons", int(hover.buttons())}, {"modifiers", int(hover.modifiers())},
                    {"source", int(hover.source())}, {"sent", true}, {"returned", false}, {"nonspontaneous", !hover.spontaneous()},
                    {"elapsed_before_ms", qint64(GetTickCount64() - budget.began)}};
                ++zoom_hover;
                try { const bool sent = QCoreApplication::sendEvent(canvas.scene, &hover);
                    hover_row.insert("returned", true); hover_row.insert("send_return", sent); hover_row.insert("accepted", hover.isAccepted()); }
                catch (...) { hover_row.insert("elapsed_after_ms", qint64(GetTickCount64() - budget.began)); wheel_events.append(hover_row); throw; }
                wheel_nonspontaneous = wheel_nonspontaneous && !hover.spontaneous();
                hover_row.insert("nonspontaneous_after", !hover.spontaneous());
                hover_row.insert("elapsed_after_ms", qint64(GetTickCount64() - budget.began)); wheel_events.append(hover_row);
                budget.check(); prewheel();
                report.insert("hover_stage_scope_verified", true);
                QJsonObject row{{"stage", "zoom_wheel"}, {"event_type", "Wheel"}, {"receiver", "QOpenGLWidget/widget2d"},
                    {"viewport_logical", xy(point.logical)}, {"screen", xy(point.screen)},
                    {"buttons", int(wheel.buttons())}, {"modifiers", int(wheel.modifiers())},
                    {"angle_delta", xy(wheel.angleDelta())}, {"pixel_delta", xy(wheel.pixelDelta())}, {"phase", int(wheel.phase())},
                    {"inverted", wheel.inverted()}, {"source", int(wheel.source())}, {"sent", true}, {"returned", false},
                    {"nonspontaneous", !wheel.spontaneous()}, {"elapsed_before_ms", qint64(GetTickCount64() - budget.began)}};
                ++wheels;
                try { const bool sent = QCoreApplication::sendEvent(canvas.viewport, &wheel);
                    row.insert("returned", true); row.insert("send_return", sent); row.insert("accepted", wheel.isAccepted()); }
                catch (...) { row.insert("elapsed_after_ms", qint64(GetTickCount64() - budget.began)); wheel_events.append(row); throw; }
                wheel_nonspontaneous = wheel_nonspontaneous && !wheel.spontaneous();
                row.insert("nonspontaneous_after", !wheel.spontaneous());
                row.insert("elapsed_after_ms", qint64(GetTickCount64() - budget.began)); wheel_events.append(row);
                require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope))
                    && !canvas.scene->mouseGrabberItem(), "POSTWHEEL_RECEIVER_OR_PHYSICAL_CHANGED");
                const Canvas current = current2d(scope, budget); const Tools after_tool = tools(scope, budget);
                require(current.view == canvas.view && current.viewport == canvas.viewport && current.scene == canvas.scene
                    && current.token == canvas.token && sameToolObjects(original_tools, after_tool) && after_tool.current == 2,
                    "POSTWHEEL_VIEW_OR_TOOL_CHANGED");
                require(horizontal && vertical && canvas.view->horizontalScrollBar() == horizontal
                    && canvas.view->verticalScrollBar() == vertical && horizontal->thread() == scope.app->thread()
                    && vertical->thread() == scope.app->thread(), "POSTWHEEL_SCROLLBAR_REFS_CHANGED");
                auto map_before = before.value("mapping").toObject();
                auto map_after = snapshot(scope, canvas).value("mapping").toObject();
                for (const char* key : {"view_transform", "viewport_transform", "scroll_values"}) {
                    map_before.remove(QLatin1StringView(key)); map_after.remove(QLatin1StringView(key));
                }
                require(map_before == map_after && mapped(canvas, point.logical).screen == point.screen
                    && wheel_nonspontaneous, "POSTWHEEL_FIXED_VIEW_SCOPE_CHANGED");
                report.insert("wheel_profile", zoom_out ? "zoom_out_at_hover_then_viewport_vertical_plus120_v1" : "zoom_in_at_hover_then_viewport_vertical_minus120_v1");
                report.insert("dispatch_completed", true); report.insert("wheel_scope_verified", true);
                report.insert("local_technical_cleanup_verified", true); report.insert("current_2d_refs_verified", true);
                report.insert("scrollbar_ref_identities_preserved", true); report.insert("view_size_DPR_origin_mode_preserved", true);
                report.insert("intentional_view_and_scroll_value_changes_allowed", true);
                report.insert("zoom_effect_verified", false); report.insert("zoom_direction_verified", false);
                report.insert("zoom_magnification_verified", false); report.insert("zoom_pivot_verified", false);
                report.insert("geometry_preserved_verified_by_bridge", false);
                report.insert("visible_view_restoration_verified", false); report.insert("automatic_inverse_or_retry_allowed", false);
            } else if (op == "set_tool") {
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "tool"}), "UNKNOWN_PARAMETERS");
                int target = -1; for (int i = 0; i < 3; ++i) if (request.value("tool").toString() == QLatin1StringView(tool_keys[i])) target = i;
                require(target >= 0 && original_tools.verified, "FIXED_TOOL_NOT_READY");
                if (original_tools.current != target) {
                    budget.check(); const Tools fresh = tools(scope, budget);
                    require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope))
                        && sameToolObjects(original_tools, fresh) && fresh.current == original_tools.current
                        && sameMapping(before, snapshot(scope, canvas)), "PRETRIGGER_SCOPE_CHANGED");
                    ++triggers; fresh.actions[target]->trigger();
                }
                const Tools after = tools(scope, budget); require(sameToolObjects(original_tools, after) && after.current == target, "TOOL_CHANGE_NOT_VERIFIED");
                report.insert("requested_tool", tool_keys[target]); report.insert("already_current", triggers == 0); report.insert("command_outcome_verified", true);
            } else if (op == "fit_selected") {
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint"}), "UNKNOWN_PARAMETERS");
                QPointer<QAction> fit; int matches = 0;
                for (QObject* object : scope.owner->children()) {
                    budget.check(); auto* action = qobject_cast<QAction*>(object);
                    if (action && action->objectName() == QLatin1StringView("actionShow_Selected_Patterns_in_Window")) { fit = action; ++matches; }
                }
                require(matches == 1 && fit && fit->isEnabled() && !fit->isCheckable(), "FIT_SELECTED_ACTION_UNAVAILABLE");
                require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope))
                    && sameMapping(before, snapshot(scope, canvas)), "PRETRIGGER_SCOPE_CHANGED");
                ++triggers; fit->trigger();
                require(fit && fit->isEnabled() && receiverAlive(scope, canvas), "FIT_SELECTED_ACTION_CHANGED");
                const Tools after = tools(scope, budget);
                require(sameToolObjects(original_tools, after) && after.current == original_tools.current, "FIT_SELECTED_TOOL_CHANGED");
                report.insert("fit_selected_action_returned", true);
                report.insert("fit_action_name", "actionShow_Selected_Patterns_in_Window");
            } else if (op == "fit_all") {
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint"}), "UNKNOWN_PARAMETERS");
                const Tools fresh = tools(scope, budget);
                const auto pretrigger = snapshot(scope, canvas);
                require(samePhysical(physical_before, physical(scope)) && sameToolObjects(original_tools, fresh)
                    && fresh.current == original_tools.current && pretrigger == before && selection(canvas) == selected
                    && pretrigger.value("scene_item_grabber_absent").toBool(), "PRETRIGGER_SCOPE_CHANGED");
                const auto fit = fitAllAction(scope, budget);
                budget.check();
                ++triggers;
                fit->trigger();
                report.insert("fit_all_action_returned", true);
                report.insert("fit_action_name", fit_all_action_name);

                require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope)), "POST_OPERATION_SCOPE_CHANGED");
                const Canvas current = current2d(scope, budget);
                const auto current_action = fitAllAction(scope, budget);
                require(current.token == canvas.token && fit && fit == current_action, "FIT_ALL_ACTION_OR_VIEW_CHANGED");
                const Tools after = tools(scope, budget);
                require(sameToolObjects(original_tools, after) && after.current == original_tools.current, "FIT_ALL_TOOL_CHANGED");
            } else {
                require(keysOnly(request, {"operation", "budget_ms", "native_view_ref", "view_fingerprint", "selection_fingerprint"}), "UNKNOWN_PARAMETERS");
                require(original_tools.verified && original_tools.current == 1, "EDIT_PATTERN_TOOL_REQUIRED");
                require(selected.value("selected_items_count").toInt() == 1 && request.value("selection_fingerprint") == selected.value("selection_fingerprint"), "SINGLE_PUBLIC_SELECTION_NOT_BOUND");
                const auto action = selectedCut(scope, budget); budget.check(); const Tools fresh = tools(scope, budget);
                const auto fresh_action = selectedCut(scope, budget);
                require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope)) && selection(canvas) == selected
                    && sameToolObjects(original_tools, fresh) && fresh.current == 1 && sameMapping(before, snapshot(scope, canvas))
                    && action.cut == fresh_action.cut && action.move == fresh_action.move && action.cut_group == fresh_action.cut_group
                    && action.move_group == fresh_action.move_group, "PRETRIGGER_SCOPE_CHANGED");
                ++triggers; action.cut->trigger();
                const Tools after = tools(scope, budget); const auto after_action = selectedCut(scope, budget, true);
                require(sameToolObjects(original_tools, after) && (after.current == 0 || after.current == 1)
                    && action.cut == after_action.cut && action.move == after_action.move && action.cut_group == after_action.cut_group
                    && action.move_group == after_action.move_group, "POST_CUT_TOOL_OR_ACTION_UNKNOWN");
                report.insert("selected_cut_action_returned", true);
            }
        }
        require(receiverAlive(scope, canvas) && samePhysical(physical_before, physical(scope)), "POST_OPERATION_SCOPE_CHANGED");
        const auto after = snapshot(scope, canvas); require(before.value("mapping").toObject().value("viewport_size") == after.value("mapping").toObject().value("viewport_size")
            && before.value("mapping").toObject().value("device_pixel_ratio") == after.value("mapping").toObject().value("device_pixel_ratio"), "VIEW_SIZE_OR_DPR_CHANGED");
        if (op == "inspect" || op == "capture") require(sameMapping(before, after), "READ_VIEW_MAPPING_CHANGED");
        report.insert("after", after); report.insert("tools_after", toolJson(tools(scope, budget)));
        const auto selection_after = selection(canvas);
        report.insert("selection_after", selection_after);
        if (op == "fit_all") {
            auto before_mapping = before.value("mapping").toObject();
            auto after_mapping = after.value("mapping").toObject();
            // Fit can change the view transform and scroll values within the same viewport.
            for (const char* key : {"view_transform", "viewport_transform", "scroll_values"}) {
                before_mapping.remove(QLatin1StringView(key));
                after_mapping.remove(QLatin1StringView(key));
            }
            require(before_mapping == after_mapping && after.value("scene_item_grabber_absent").toBool(), "POST_FIT_ALL_VIEW_SCOPE_CHANGED");
            require(selection_after == selected, "POST_FIT_ALL_SELECTION_CHANGED");
            report.insert("fit_all_scope_verified", true);
            report.insert("fit_all_extents_verified", false);
        }
        report.insert("physical_after", physicalJson(physical(scope))); report.insert("physical_state_unchanged", true);
        report.insert("elapsed_ms", qint64(GetTickCount64() - budget.began)); report.insert("verified", true); report.insert("status", "SUCCEEDED");
    } catch (const Reject& e) { report.insert("error", QJsonObject{{"code", e.code}, {"message", e.code}}); }
      catch (...) { run.exception = true; report.insert("error", QJsonObject{{"code", "CPP_EXCEPTION"}, {"message", "Native faults and handlers are not contained"}}); }
    const bool effects = run.calls > 0 || zoom_hover > 0 || wheels > 0 || triggers > 0, succeeded = report.value("status") == "SUCCEEDED";
    auto counts = counters(); counts.insert("QAction_trigger_calls", triggers); counts.insert("meta_property_getter_calls", property_reads);
    counts.insert("framebuffer_capture_calls", 0); counts.insert("paint_resolve_capture_calls", captures); counts.insert("PNG_encode_calls", encodes);
    counts.insert("zoom_hover_sendEvent_calls", zoom_hover); counts.insert("viewport_wheel_sendEvent_calls", wheels);
    counts.insert("bezier_scene_event_calls", run.calls); counts.insert("scene_wheel_sendEvent_calls", 0);
    report.insert("controller_calls", counts); report.insert("event_calls", run.calls + zoom_hover + wheels); report.insert("events", zoom_hover ? wheel_events : run.events);
    report.insert("zoom_hover_sendEvent_calls", zoom_hover); report.insert("viewport_wheel_sendEvent_calls", wheels);
    report.insert("bezier_scene_event_calls", run.calls); report.insert("scene_wheel_sendEvent_calls", 0);
    report.insert("QAction_trigger_calls", triggers); report.insert("effects_started", effects);
    report.insert("press_sent", run.press); report.insert("release_sent", run.release); report.insert("release_returned", run.release_returned);
    report.insert("press_outstanding", run.pending); report.insert("paired_release_verified", run.press && run.release && run.release_returned && !run.pending && run.cleanup_scope);
    report.insert("scene_ungrab_calls", run.ungrab ? 1 : 0); report.insert("all_events_nonspontaneous", run.nonspontaneous && wheel_nonspontaneous);
    report.insert("cursor_positions_observational", true); report.insert("direct_call_counters_only", true);
    if ((effects || capture_recovery) && !succeeded) { recovery_required = true; report.insert("status", "PARTIAL"); report.insert("requires_recovery", true); }
    try { return write(report, succeeded ? 0 : (effects || capture_recovery) ? -10 : -5, output, cap, written, needed); }
    catch (...) {
        if (effects || capture_recovery) recovery_required = true;
        const int n = std::snprintf(output, cap, "{\"schema\":\"md-qt-scene-adapter/6\",\"return_code\":-8,\"status\":\"UNKNOWN\",\"effects_started\":%s,\"requires_recovery\":%s,\"verified\":false}", effects ? "true" : "false", (effects || capture_recovery) ? "true" : "false");
        if (n > 0 && unsigned(n) < cap) { *written = unsigned(n); *needed = unsigned(n + 1); } return -8;
    }
}
