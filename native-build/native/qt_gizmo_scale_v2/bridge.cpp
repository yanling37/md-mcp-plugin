// Fixed public QAction controls for up to two scoped background Gizmo Scale steps.
#include <QtWidgets/QMenu>
#include <optional>
#include "../qt_world_adapter/canvas_common.inc"

struct ScaleAction {
    QPointer<QAction> action;
    QJsonObject observed;
    bool identified = false;
};

QString menuText(QString text) { return text.remove(QChar('&')).trimmed(); }

ScaleAction readScaleAction(const Scope& s, const char* name, const char* label, Budget& budget) {
    ScaleAction result;
    int count = 0;
    require(s.owner->children().size() <= 5000, "ACTION_LIMIT");
    for (QObject* child : s.owner->children()) {
        budget.check();
        if (child && child->objectName() == QLatin1StringView(name)) {
            ++count;
            result.action = qobject_cast<QAction*>(child);
        }
    }
    result.observed = {{"object_name", name}, {"match_count", count}, {"identified", false}};
    auto action = result.action;
    if (count != 1 || !exact(action, "QAction", name) || action->parent() != s.owner
        || action->thread() != s.app->thread()) return result;

    QJsonArray menus;
    bool scoped_menu = false;
    const auto associates = action->associatedObjects();
    require(associates.size() <= 32 && action->text().size() <= 256, "ACTION_METADATA_LIMIT");
    for (QObject* object : associates) {
        budget.check();
        if (const auto* menu = qobject_cast<const QMenu*>(object)) {
            require(menu->title().size() <= 256 && menu->thread() == s.app->thread(), "MENU_METADATA_LIMIT");
            const auto title = menuText(menu->title());
            bool within_owner = false;
            unsigned depth = 0;
            for (const QObject* parent = menu; parent; parent = parent->parent()) {
                require(++depth <= 32, "MENU_ANCESTRY_LIMIT");
                if (parent == s.owner) { within_owner = true; break; }
            }
            scoped_menu = scoped_menu || (within_owner && title == "Gizmo");
            menus.append(QJsonObject{{"object", description(object)}, {"title", title}, {"within_owner", within_owner}});
        }
    }
    result.identified = scoped_menu && menuText(action->text()) == QLatin1StringView(label) && !action->isCheckable();
    result.observed.insert("identified", result.identified);
    result.observed.insert("text", action->text());
    result.observed.insert("enabled", action->isEnabled());
    result.observed.insert("checkable", action->isCheckable());
    result.observed.insert("menus", menus);
    return result;
}

struct ScalePair {
    QString ticket;
    QString view_ref;
    QString fingerprint;
    QPointer<QAction> increase, decrease;
    int increase_attempts = 0, increase_returns = 0, increase_verified = 0;
    int decrease_attempts = 0, decrease_returns = 0, decrease_verified = 0;

    bool settled() const {
        return increase_attempts == increase_returns && increase_returns == increase_verified
            && decrease_attempts == decrease_returns && decrease_returns == decrease_verified;
    }
    QJsonObject receipt() const {
        return {{"transaction_id", ticket}, {"increase_attempts", increase_attempts},
            {"increase_returns", increase_returns}, {"increase_verified", increase_verified},
            {"decrease_attempts", decrease_attempts}, {"decrease_returns", decrease_returns},
            {"decrease_verified", decrease_verified}, {"native_phases_settled", settled()}};
    }
};

std::optional<ScalePair> scale_pair;

bool isTicket(const QString& ticket) {
    if (ticket.size() != 32) return false;
    for (QChar character : ticket) {
        const auto value = character.unicode();
        if (!(value >= '0' && value <= '9') && !(value >= 'a' && value <= 'f')) return false;
    }
    return true;
}

int writeScale(QJsonObject report, int code, char* output, unsigned cap, unsigned* written, unsigned* needed) {
    report.insert("schema", "md-qt-gizmo-scale/2");
    report.insert("native_abi_version", 2);
    report.insert("return_code", code);
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Compact);
    *needed = static_cast<unsigned>(bytes.size() + 1);
    if (*needed > cap) return -7;
    std::memcpy(output, bytes.constData(), static_cast<std::size_t>(bytes.size()));
    output[bytes.size()] = 0;
    *written = static_cast<unsigned>(bytes.size());
    return code;
}

} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_gizmo_scale_v2_abi_version() noexcept { return 2; }

extern "C" __declspec(dllexport) int __cdecl mdqt_gizmo_scale_v2_json(std::uint32_t pid, std::uint64_t creation,
    const char* input, unsigned length, char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!input || !length || length > 4096 || !output || !written || !needed || cap != output_capacity) return -1;
    QJsonObject report;
    int triggers = 0;
    bool owns_running = false;
    const auto finish = [&](int code, const char* status, const char* error) {
        if (owns_running) running = false;
        auto counts = counters();
        counts.insert("QAction_trigger_calls", triggers);
        report.insert("operation_counts", counts);
        report.insert("status", status);
        report.insert("error_code", error);
        report.insert("verified", code == 0);
        report.insert("pair_pending", scale_pair.has_value());
        if (!report.contains("pair_state"))
            report.insert("pair_state", scale_pair ? QJsonValue(scale_pair->receipt()) : QJsonValue(QJsonValue::Null));
        report.insert("display_scale_restoration_verified", false); // Independent image evidence is owned by the host transaction.
        report.insert("effects_started", triggers != 0);
        report.insert("requires_recovery", code != 0 && scale_pair.has_value());
        return writeScale(report, code, output, cap, written, needed);
    };
    try {
        report.insert("process_id", double(pid));
        report.insert("process_creation_filetime", QString::number(creation));
        require(!running && processMatches(pid, creation), "BUSY_OR_PROCESS_IDENTITY_MISMATCH");
        report.insert("modules", modules());
        QJsonParseError error{};
        const auto document = QJsonDocument::fromJson(QByteArray(input, static_cast<int>(length)), &error);
        require(error.error == QJsonParseError::NoError && document.isObject(), "INVALID_JSON");
        const auto request = document.object();
        const auto operation = request.value("operation").toString();
        report.insert("operation", operation);
        require(operation == "inspect" || operation == "increase" || operation == "decrease" || operation == "close", "FIXED_OPERATION_REQUIRED");
        const bool inspect = operation == "inspect";
        QStringList keys{"operation", "budget_ms"};
        if (!inspect) keys << "transaction_id" << "native_view_ref" << "view_fingerprint";
        require(request.size() == keys.size() && keysOnly(request, keys), "FIXED_REQUEST_FIELDS_REQUIRED");
        Budget budget(budgetValue(request));
        running = owns_running = true;
        const auto scope = currentScope(budget, pid);
        Canvas view;
        int matches = 0;
        for (const auto& row : canvases(scope, budget)) if (row.kind == "3d") { view = row; ++matches; }
        require(matches == 1 && receiverAlive(scope, view), "CURRENT_3D_NOT_UNIQUE");
        const auto physical_before = physical(scope);
        const auto before = snapshot(scope, view);
        require(backgroundSafe(physical_before) && before.value("scene_item_grabber_absent").toBool(), "BACKGROUND_IDLE_VIEW_REQUIRED");
        const auto increase = readScaleAction(scope, "action_Increase_Gizmo_Scale", "Increase Gizmo Scale", budget);
        const auto decrease = readScaleAction(scope, "action_Decrease_Gizmo_Scale", "Decrease Gizmo Scale", budget);
        report.insert("increase_action", increase.observed);
        report.insert("decrease_action", decrease.observed);
        report.insert("before", before);
        report.insert("physical_before", physicalJson(physical_before));

        const auto stable = [&]() {
            require(receiverAlive(scope, view) && samePhysical(physical_before, physical(scope))
                    && snapshot(scope, view) == before, "VIEW_OR_PHYSICAL_STATE_CHANGED");
        };
        if (!inspect) {
            const auto ticket = request.value("transaction_id").toString();
            const auto ref = request.value("native_view_ref").toString();
            const auto fingerprint = request.value("view_fingerprint").toString();
            require(isTicket(ticket) && ref == before.value("native_view_ref").toString()
                    && fingerprint == before.value("view_fingerprint").toString(), "FRESH_VIEW_AND_TRANSACTION_REQUIRED");
            report.insert("transaction_id", ticket);
            require(increase.identified && decrease.identified, "GIZMO_SCALE_ACTION_SCOPE_UNVERIFIED");
            stable();
            budget.check();
            if (scale_pair) {
                require(scale_pair->ticket == ticket && scale_pair->view_ref == ref && scale_pair->fingerprint == fingerprint
                        && scale_pair->increase == increase.action && scale_pair->decrease == decrease.action,
                        "ORIGINAL_SCALE_PAIR_REQUIRED");
                require(scale_pair->settled(), "PREVIOUS_SCALE_PHASE_UNRESOLVED");
            }
            if (operation == "increase") {
                require(increase.action->isEnabled() && decrease.action->isEnabled(), "SCALE_ACTION_DISABLED");
                require(!scale_pair || (scale_pair->increase_verified < 2 && scale_pair->decrease_attempts == 0),
                        "INCREASE_LIMIT_OR_INVERSE_ALREADY_STARTED");
                if (!scale_pair) scale_pair = ScalePair{ticket, ref, fingerprint, increase.action, decrease.action};
                ++scale_pair->increase_attempts;
                ++triggers;
                increase.action->trigger();
                ++scale_pair->increase_returns;
            } else {
                require(scale_pair && scale_pair->increase_verified > 0, "ORIGINAL_SCALE_PAIR_REQUIRED");
                if (operation == "decrease") {
                    require(scale_pair->decrease_attempts < scale_pair->increase_verified && decrease.action->isEnabled(),
                            "INVERSE_LIMIT_OR_ACTION_DISABLED");
                    ++scale_pair->decrease_attempts;
                    ++triggers;
                    decrease.action->trigger();
                    ++scale_pair->decrease_returns;
                } else {
                    require(scale_pair->increase_verified == scale_pair->decrease_verified, "SCALE_PAIR_NOT_DISPATCHED");
                }
            }
        }
        stable();
        budget.check();
        report.insert("physical_after", physicalJson(physical(scope)));
        report.insert("physical_state_unchanged", true);
        report.insert("after", snapshot(scope, view));
        if (operation == "increase") ++scale_pair->increase_verified;
        if (operation == "decrease") ++scale_pair->decrease_verified;
        if (operation == "close") {
            report.insert("pair_state", scale_pair->receipt());
            scale_pair.reset();
        }
        return finish(0, "SUCCEEDED", "");
    } catch (const Reject& error) {
        return finish(scale_pair ? 2 : 1, scale_pair ? "UNKNOWN" : "REJECTED", error.code);
    } catch (...) {
        return finish(scale_pair ? 2 : 1, scale_pair ? "UNKNOWN" : "REJECTED", "NATIVE_EXCEPTION");
    }
}
