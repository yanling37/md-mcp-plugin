// One existing 3D viewport Paint and an owned framebuffer resolve.
#include "canvas_common.inc"
#include "capture_gl.inc"

Canvas unique3d(const Scope& scope, Budget& budget) {
    const auto views = canvases(scope, budget);
    Canvas result;
    unsigned count = 0;
    for (const auto& view : views) {
        if (view.kind == "3d") {
            result = view;
            ++count;
        }
    }
    require(count == 1 && receiverAlive(scope, result), "UNIQUE_3D_VIEW_REQUIRED");
    return result;
}

bool sameReceiver(const Canvas& before, const Canvas& after) {
    return before.root == after.root && before.owner == after.owner && before.view == after.view
        && before.viewport == after.viewport && before.scene == after.scene && before.chain == after.chain;
}

bool fingerprintValid(const QString& value) {
    if (value.size() != 64) return false;
    for (const auto character : value) {
        const auto code = character.unicode();
        if (!(code >= '0' && code <= '9') && !(code >= 'a' && code <= 'f')) return false;
    }
    return true;
}

} // namespace

extern "C" __declspec(dllexport) unsigned __cdecl mdqt_world_frame_probe_abi_version() noexcept {
    return 1;
}

extern "C" __declspec(dllexport)
int __cdecl mdqt_world_frame_probe_json(std::uint32_t pid, std::uint64_t creation, const char* input, unsigned length,
    char* output, unsigned cap, unsigned* written, unsigned* needed) noexcept {
    if (written) *written = 0;
    if (needed) *needed = 0;
    if (!input || length == 0 || length > 4096 || !output || !written || !needed || cap != output_capacity) return -1;

    QJsonObject report;
    bool owns_running = false, capture_started = false, capture_recovery = false;
    bool endpoints_verified = false, succeeded = false, unexpected = false;
    bool previous_recovery_pending = false;
    const char* failure = "";
    try {
        report = QJsonObject{{"operation", "capture"}, {"status", "REJECTED"}, {"verified", false},
            {"effects_started", false}, {"requires_recovery", false}, {"capture_effects_started", false},
            {"process_id", double(pid)}, {"process_creation_filetime", QString::number(creation)},
            {"OS_input_calls", 0}, {"focus_calls", 0}, {"activation_calls", 0}, {"camera_switch_calls", 0},
            {"scene_input_calls", 0}, {"model_setter_calls", 0}, {"PNG_encode_calls", 0},
            {"zero_operation_counts", counters()}, {"scope", "current_unique_3d_Qt_viewport_framebuffer"},
            {"mapping_schema", "world_adapter_canvas_common_mapping"}, {"opaque_view_reference_used", false},
            {"camera_intrinsics_verified", false}, {"world_axis_mapping_verified", false},
            {"native_selected_vertex_verified", false}, {"persistent_UI_write_calls", 0},
            {"pixels_freshness_verified", false}, {"pixels_scene_content_verified", false},
            {"gl_state_restored", false}, {"background_endpoints_verified", false}};
        require(processMatches(pid, creation), "PROCESS_IDENTITY_MISMATCH");
        report.insert("modules", modules());
        QJsonParseError error{};
        const auto document = QJsonDocument::fromJson(QByteArray(input, static_cast<int>(length)), &error);
        require(error.error == QJsonParseError::NoError && document.isObject(), "INVALID_JSON");
        const auto request = document.object();
        require(request.size() == 3 && keysOnly(request, {"operation", "mapping_fingerprint", "budget_ms"})
            && request.value("operation") == "capture", "INVALID_FIXED_CAPTURE_REQUEST");
        const QString expected = request.value("mapping_fingerprint").toString();
        require(fingerprintValid(expected), "INVALID_MAPPING_FINGERPRINT");

        Budget budget(budgetValue(request));
        const Scope scope = currentScope(budget, pid);
        previous_recovery_pending = recovery_required;
        require(!previous_recovery_pending, "FRAME_PROBE_RECOVERY_REQUIRED");
        require(!running, "FRAME_PROBE_BUSY");
        running = true;
        owns_running = true;
        const Canvas canvas = unique3d(scope, budget);
        const Physical physical_before = physical(scope);
        require(backgroundSafe(physical_before), "BACKGROUND_SCOPE_REQUIRED");
        const QJsonObject before = snapshot(scope, canvas);
        report.insert("before", before);
        report.insert("physical_before", physicalJson(physical_before));
        report.insert("mapping_fingerprint", before.value("mapping_fingerprint"));
        require(before.value("mapping_fingerprint") == expected && before.value("scene_item_grabber_absent").toBool(),
                "STALE_MAPPING_OR_SCENE_GRABBER");
        const QSize logical = canvas.viewport->size();
        const qreal dpr = canvas.viewport->devicePixelRatioF();
        const double width_bound = std::ceil(logical.width()*dpr)+1.;
        const double height_bound = std::ceil(logical.height()*dpr)+1.;
        require(width_bound <= 4096 && height_bound <= 4096 && width_bound*height_bound <= 4000000,
                "FRAME_PIXEL_BUDGET");

        const QImage frame = capture_gl::capture(scope, canvas, budget, physical_before, before,
                                               report, capture_started, capture_recovery);
        const bool gl_restored = report.value("capture_recovery_verified").toBool()
            && report.value("original_Qt_context_surface_restored").toBool()
            && report.value("original_WGL_context_dc_restored").toBool()
            && report.value("original_framebuffer_bindings_restored").toBool()
            && report.value("owned_resources_cleanup_verified").toBool();
        report.insert("gl_state_restored", gl_restored);
        require(gl_restored, "FRAME_CAPTURE_RESTORATION_UNVERIFIED");

        budget.check();
        QByteArray png;
        QBuffer device(&png);
        require(device.open(QIODevice::WriteOnly), "PNG_BUFFER_OPEN_FAILED");
        report.insert("PNG_encode_calls", 1);
        require(frame.save(&device, "PNG"), "PNG_ENCODE_FAILED");
        device.close();
        require(png.size() > 24 && png.size() <= 5*1024*1024, "PNG_BYTE_BUDGET");
        const QString png_sha = QString::fromLatin1(QCryptographicHash::hash(png, QCryptographicHash::Sha256).toHex());
        const QString base64 = QString::fromLatin1(png.toBase64());

        budget.check();
        require(processMatches(pid, creation) && scopeAlive(scope), "END_PROCESS_OR_OWNER_SCOPE_CHANGED");
        const Canvas current = unique3d(scope, budget);
        const Physical physical_after = physical(scope);
        const QJsonObject after = snapshot(scope, current);
        report.insert("after", after);
        report.insert("physical_after", physicalJson(physical_after));
        endpoints_verified = sameReceiver(canvas, current) && before == after && samePhysical(physical_before, physical_after)
            && after.value("scene_item_grabber_absent").toBool();
        report.insert("background_endpoints_verified", endpoints_verified);
        require(endpoints_verified, "FRAME_CAPTURE_ENDPOINT_CHANGED");
        report.insert("image", QJsonObject{{"format", "PNG"}, {"width", frame.width()}, {"height", frame.height()},
            {"viewport_size", QJsonArray{logical.width(), logical.height()}}, {"device_pixel_ratio", dpr},
            {"byte_count", double(png.size())}, {"sha256", png_sha}, {"mapping_fingerprint", expected},
            {"pixel_origin", "top_left_after_CPU_vertical_flip"}});
        report.insert("png_base64", base64);
        succeeded = true;
    } catch (const Reject& error) {
        failure = error.code;
    } catch (...) {
        unexpected = true;
        failure = "FRAME_PROBE_EXCEPTION";
    }

    const bool uncertain = !succeeded && (capture_recovery || (capture_started && !endpoints_verified));
    if (owns_running) {
        recovery_required = recovery_required || uncertain;
        running = false;
    }
    try {
        report.insert("capture_effects_started", capture_started);
        report.insert("requires_recovery", uncertain || previous_recovery_pending);
        report.insert("previous_recovery_pending", previous_recovery_pending);
        report.insert("verified", succeeded);
        report.insert("status", succeeded ? "SUCCEEDED" : uncertain ? (unexpected ? "UNKNOWN" : "PARTIAL") : "REJECTED");
        report.insert("error_code", succeeded ? QJsonValue::Null : QJsonValue(QString::fromLatin1(failure)));
        if (!succeeded) {
            report.remove("image");
            report.remove("png_base64");
        }
        const int code = write(report, succeeded ? 0 : uncertain ? (unexpected ? -8 : -10) : -5,
                               output, cap, written, needed);
        if (code == -7 && capture_started) recovery_required = true;
        return code;
    } catch (...) {
        if (capture_started) recovery_required = true;
        return -8;
    }
}
