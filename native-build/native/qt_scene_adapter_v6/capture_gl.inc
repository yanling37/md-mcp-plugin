// Frozen public GL helpers copied from the reviewed paint/owned-resolve bridges.
namespace capture_gl {

using WglCurrentContextFn=HGLRC (WINAPI*)();using WglCurrentDcFn=HDC (WINAPI*)();
using GetIntegerFn=void (APIENTRY*)(GLenum,GLint*);using IsFramebufferFn=GLboolean (APIENTRY*)(GLuint);
using BindFramebufferFn=void (APIENTRY*)(GLenum,GLuint);
struct RestoreState {bool attempted=false,restore_attempted=false,qt_restored=true,wgl_restored=true,failed=false,not_required=true;};
struct PaintState {
    int make_calls=0,send_calls=0;bool sent=false,returned=false,send_return=false,accepted=false,cpp_exception=false,nonspontaneous=true;
    bool binding_failed=false,bindings_restored=true,binding_not_required=true;int binding_calls=0;
};

template<class Function> Function nativeProc(HMODULE module, const char* name) {
    FARPROC raw = GetProcAddress(module, name);
    Function result = nullptr;
    static_assert(sizeof(result) == sizeof(raw));
    std::memcpy(&result, &raw, sizeof(result));
    return result;
}
template<class Function> Function glProc(QOpenGLContext* context, const char* name) {
    const QFunctionPointer raw = context->getProcAddress(name);
    std::uintptr_t value = 0;
    static_assert(sizeof(raw) == sizeof(value));
    std::memcpy(&value, &raw, sizeof(value));
    if (value <= 3 || value == UINTPTR_MAX) return nullptr;
    Function result = nullptr;
    static_assert(sizeof(result) == sizeof(raw));
    std::memcpy(&result, &raw, sizeof(result));
    return result;
}
QObject* surfaceObject(QSurface* surface) {
    if (!surface) return nullptr;
    if (surface->surfaceClass() == QSurface::Offscreen) return dynamic_cast<QOffscreenSurface*>(surface);
    if (surface->surfaceClass() == QSurface::Window) return dynamic_cast<QWindow*>(surface);
    return nullptr;
}
bool existingSurface(QSurface* surface) {
    if (!surface || surface->surfaceType() != QSurface::OpenGLSurface) return false;
    if (auto* offscreen = dynamic_cast<QOffscreenSurface*>(surface)) return offscreen->isValid();
    if (auto* window = dynamic_cast<QWindow*>(surface)) return window->handle() != nullptr;
    return false;
}
bool desktop45(QOpenGLContext* context) {
    if (!context || !context->isValid()) return false;
    QSurfaceFormat format = context->format();
    return format.renderableType() == QSurfaceFormat::OpenGL
        && (format.majorVersion() > 4 || (format.majorVersion() == 4 && format.minorVersion() >= 5))
        && (format.profile() == QSurfaceFormat::CoreProfile || format.profile() == QSurfaceFormat::CompatibilityProfile);
}

class RestoreGuard {
public:
    RestoreGuard(QOpenGLContext* original, QSurface* surface, HGLRC rc, HDC dc,
            WglCurrentContextFn get_context, WglCurrentDcFn get_dc, RestoreState& state)
        : original_(original), surface_(surface), surface_object_(surfaceObject(surface)),
          rc_(rc), dc_(dc), get_context_(get_context), get_dc_(get_dc), state_(state) {}
    ~RestoreGuard() noexcept { restore(); }
    void beginAttempt() noexcept {
        state_.attempted = true; state_.not_required = false;
        state_.qt_restored = false; state_.wgl_restored = false;
    }
    void restore() noexcept {
        if (finished_) return;
        finished_ = true;
        try {
            if (state_.attempted) {
                state_.restore_attempted = true;
                const bool result = original_ && surface_object_ && original_->makeCurrent(surface_);
                state_.qt_restored = result && original_ && surface_object_ && QOpenGLContext::currentContext() == original_
                    && original_->surface() == surface_;
            } else {
                state_.qt_restored = original_ && surface_object_
                    && QOpenGLContext::currentContext() == original_ && original_->surface() == surface_;
            }
            state_.wgl_restored = get_context_() == rc_ && get_dc_() == dc_;
            state_.failed = !state_.qt_restored || !state_.wgl_restored;
        } catch (...) { state_.failed = true; state_.qt_restored = false; state_.wgl_restored = false; }
    }
private:
    QPointer<QOpenGLContext> original_;
    QSurface* surface_;
    QPointer<QObject> surface_object_;
    HGLRC rc_;
    HDC dc_;
    WglCurrentContextFn get_context_;
    WglCurrentDcFn get_dc_;
    RestoreState& state_;
    bool finished_ = false;
};


class BindingRestoreGuard {
public:
    BindingRestoreGuard(RestoreGuard& context_guard,RestoreState& context_state,PaintState& state,
            QOpenGLContext* original,QSurface* surface,HGLRC rc,HDC dc,WglCurrentContextFn get_rc,WglCurrentDcFn get_dc,
            GetIntegerFn integer,IsFramebufferFn is_fbo,BindFramebufferFn bind,GLuint draw,GLuint read)
        :context_guard_(context_guard),context_state_(context_state),state_(state),original_(original),surface_(surface),surface_lifetime_(surfaceObject(surface)),
          rc_(rc),dc_(dc),get_rc_(get_rc),get_dc_(get_dc),integer_(integer),is_fbo_(is_fbo),bind_(bind),draw_(draw),read_(read) {}
    ~BindingRestoreGuard() noexcept {restore();}
    void restore() noexcept {
        if (finished_) return;finished_=true;context_guard_.restore();
        try {
            if (!context_state_.attempted) return;
            state_.binding_not_required=false;state_.bindings_restored=false;
            if (context_state_.failed || !original_ || !surface_lifetime_ || QOpenGLContext::currentContext()!=original_
                    || original_->surface()!=surface_ || get_rc_()!=rc_ || get_dc_()!=dc_) {state_.binding_failed=true;return;}
            GLint draw=-1,read=-1;integer_(GL_DRAW_FRAMEBUFFER_BINDING,&draw);integer_(GL_READ_FRAMEBUFFER_BINDING,&read);
            if (draw<0 || read<0 || (draw_ && is_fbo_(draw_)!=GL_TRUE) || (read_ && is_fbo_(read_)!=GL_TRUE)) {state_.binding_failed=true;return;}
            if (static_cast<GLuint>(draw)!=draw_) {++state_.binding_calls;bind_(GL_DRAW_FRAMEBUFFER,draw_);}
            if (static_cast<GLuint>(read)!=read_) {++state_.binding_calls;bind_(GL_READ_FRAMEBUFFER,read_);}
            draw=-1;read=-1;integer_(GL_DRAW_FRAMEBUFFER_BINDING,&draw);integer_(GL_READ_FRAMEBUFFER_BINDING,&read);
            state_.bindings_restored=draw>=0 && read>=0 && static_cast<GLuint>(draw)==draw_ && static_cast<GLuint>(read)==read_
                && original_ && surface_lifetime_ && QOpenGLContext::currentContext()==original_ && original_->surface()==surface_ && get_rc_()==rc_ && get_dc_()==dc_;
            state_.binding_failed=!state_.bindings_restored;
        } catch (...) {state_.binding_failed=true;state_.bindings_restored=false;}
    }
private:
    RestoreGuard& context_guard_;RestoreState& context_state_;PaintState& state_;QPointer<QOpenGLContext> original_;QSurface* surface_;
    QPointer<QObject> surface_lifetime_;HGLRC rc_;HDC dc_;WglCurrentContextFn get_rc_;WglCurrentDcFn get_dc_;
    GetIntegerFn integer_;IsFramebufferFn is_fbo_;BindFramebufferFn bind_;GLuint draw_,read_;bool finished_=false;
};

struct OwnershipState {
    bool cleanup_verified=true, cleanup_failed=false, not_required=true, cleanup_attempted=false, names_invalid=true;
    int framebuffer_created=0, framebuffer_deleted=0, texture_created=0, texture_deleted=0, live=0;
    int create_calls=0, attachment_calls=0, buffer_calls=0, storage_calls=0, clear_calls=0, blit_calls=0;
    int read_calls=0, baseline_reads=0, copy_reads=0, delete_calls=0;
};

using GetNamedAttachmentFn = void (APIENTRY*)(GLuint, GLenum, GLenum, GLint*);
using GetTextureParameterFn = void (APIENTRY*)(GLuint, GLenum, GLint*);
using GetTextureLevelFn = void (APIENTRY*)(GLuint, GLint, GLenum, GLint*);
using GetTextureImageFn = void (APIENTRY*)(GLuint, GLint, GLenum, GLenum, GLsizei, void*);


using IsObjectFn=GLboolean (APIENTRY*)(GLuint);
using GetRenderbufferFn=void (APIENTRY*)(GLuint,GLenum,GLint*);
using CreateFramebuffersFn=void (APIENTRY*)(GLsizei,GLuint*);
using CreateTexturesFn=void (APIENTRY*)(GLenum,GLsizei,GLuint*);
using TextureStorageFn=void (APIENTRY*)(GLuint,GLsizei,GLenum,GLsizei,GLsizei);
using AttachRenderbufferFn=void (APIENTRY*)(GLuint,GLenum,GLenum,GLuint);
using AttachTextureFn=void (APIENTRY*)(GLuint,GLenum,GLuint,GLint);
using NamedBufferFn=void (APIENTRY*)(GLuint,GLenum);
using CheckFramebufferFn=GLenum (APIENTRY*)(GLuint,GLenum);
using ClearTextureFn=void (APIENTRY*)(GLuint,GLint,GLenum,GLenum,const void*);
using BlitFramebufferFn=void (APIENTRY*)(GLuint,GLuint,GLint,GLint,GLint,GLint,
    GLint,GLint,GLint,GLint,GLbitfield,GLenum);
using DeleteObjectsFn=void (APIENTRY*)(GLsizei,const GLuint*);
using GetStringiFn=const GLubyte* (APIENTRY*)(GLenum,GLuint);
using IsEnabledFn=GLboolean (APIENTRY*)(GLenum);

struct Procedures {
    GetIntegerFn integer=nullptr;
    GetNamedAttachmentFn attachment=nullptr;
    GetRenderbufferFn renderbuffer=nullptr;
    IsObjectFn is_framebuffer=nullptr,is_renderbuffer=nullptr,is_texture=nullptr;
    CreateFramebuffersFn create_framebuffers=nullptr;
    CreateTexturesFn create_textures=nullptr;
    TextureStorageFn storage=nullptr;
    AttachRenderbufferFn attach_renderbuffer=nullptr;
    AttachTextureFn attach_texture=nullptr;
    NamedBufferFn read_buffer=nullptr,draw_buffer=nullptr;
    CheckFramebufferFn check_framebuffer=nullptr;
    ClearTextureFn clear=nullptr;
    BlitFramebufferFn blit=nullptr;
    GetTextureLevelFn texture_level=nullptr;
    GetTextureImageFn image=nullptr;
    DeleteObjectsFn delete_framebuffers=nullptr,delete_textures=nullptr;
    GetStringiFn string_i=nullptr;
    IsEnabledFn enabled=nullptr;
    bool resolve(QOpenGLContext* context) {
        integer=glProc<GetIntegerFn>(context,"glGetIntegerv");
        attachment=glProc<GetNamedAttachmentFn>(context,"glGetNamedFramebufferAttachmentParameteriv");
        renderbuffer=glProc<GetRenderbufferFn>(context,"glGetNamedRenderbufferParameteriv");
        is_framebuffer=glProc<IsObjectFn>(context,"glIsFramebuffer");
        is_renderbuffer=glProc<IsObjectFn>(context,"glIsRenderbuffer");
        is_texture=glProc<IsObjectFn>(context,"glIsTexture");
        create_framebuffers=glProc<CreateFramebuffersFn>(context,"glCreateFramebuffers");
        create_textures=glProc<CreateTexturesFn>(context,"glCreateTextures");
        storage=glProc<TextureStorageFn>(context,"glTextureStorage2D");
        attach_renderbuffer=glProc<AttachRenderbufferFn>(context,"glNamedFramebufferRenderbuffer");
        attach_texture=glProc<AttachTextureFn>(context,"glNamedFramebufferTexture");
        read_buffer=glProc<NamedBufferFn>(context,"glNamedFramebufferReadBuffer");
        draw_buffer=glProc<NamedBufferFn>(context,"glNamedFramebufferDrawBuffer");
        check_framebuffer=glProc<CheckFramebufferFn>(context,"glCheckNamedFramebufferStatus");
        clear=glProc<ClearTextureFn>(context,"glClearTexImage");
        blit=glProc<BlitFramebufferFn>(context,"glBlitNamedFramebuffer");
        texture_level=glProc<GetTextureLevelFn>(context,"glGetTextureLevelParameteriv");
        image=glProc<GetTextureImageFn>(context,"glGetTextureImage");
        delete_framebuffers=glProc<DeleteObjectsFn>(context,"glDeleteFramebuffers");
        delete_textures=glProc<DeleteObjectsFn>(context,"glDeleteTextures");
        string_i=glProc<GetStringiFn>(context,"glGetStringi");
        enabled=glProc<IsEnabledFn>(context,"glIsEnabled");
        return integer && attachment && renderbuffer && is_framebuffer && is_renderbuffer && is_texture
            && create_framebuffers && create_textures && storage && attach_renderbuffer && attach_texture
            && read_buffer && draw_buffer && check_framebuffer && clear && blit && texture_level && image
            && delete_framebuffers && delete_textures && string_i && enabled;
    }
};

struct PixelStore {
    GLint pbo=-1,alignment=-1,row=-1,skip_rows=-1,skip_pixels=-1,image_height=-1,skip_images=-1,swap=-1,lsb=-1;
    void read(GetIntegerFn get,bool unpack) {
        get(unpack ? GL_PIXEL_UNPACK_BUFFER_BINDING : GL_PIXEL_PACK_BUFFER_BINDING,&pbo);
        get(unpack ? GL_UNPACK_ALIGNMENT : GL_PACK_ALIGNMENT,&alignment);
        get(unpack ? GL_UNPACK_ROW_LENGTH : GL_PACK_ROW_LENGTH,&row);
        get(unpack ? GL_UNPACK_SKIP_ROWS : GL_PACK_SKIP_ROWS,&skip_rows);
        get(unpack ? GL_UNPACK_SKIP_PIXELS : GL_PACK_SKIP_PIXELS,&skip_pixels);
        get(unpack ? GL_UNPACK_IMAGE_HEIGHT : GL_PACK_IMAGE_HEIGHT,&image_height);
        get(unpack ? GL_UNPACK_SKIP_IMAGES : GL_PACK_SKIP_IMAGES,&skip_images);
        get(unpack ? GL_UNPACK_SWAP_BYTES : GL_PACK_SWAP_BYTES,&swap);
        get(unpack ? GL_UNPACK_LSB_FIRST : GL_PACK_LSB_FIRST,&lsb);
    }
    bool safe() const { return pbo==0 && (alignment==1 || alignment==2 || alignment==4 || alignment==8)
        && row==0 && skip_rows==0 && skip_pixels==0 && image_height==0 && skip_images==0 && swap==0 && lsb==0; }
    bool same(const PixelStore& b) const { return pbo==b.pbo && alignment==b.alignment && row==b.row
        && skip_rows==b.skip_rows && skip_pixels==b.skip_pixels && image_height==b.image_height
        && skip_images==b.skip_images && swap==b.swap && lsb==b.lsb; }
    QJsonObject json() const { return {{"PBO_is_zero",pbo==0},{"alignment",alignment},{"row_length",row},
        {"skip_rows",skip_rows},{"skip_pixels",skip_pixels},{"image_height",image_height},
        {"skip_images",skip_images},{"swap_bytes",swap},{"lsb_first",lsb}}; }
};

struct SourceInfo {
    bool framebuffer_exists=false,renderbuffer_exists=false;
    GLint type=-1,name=-1,width=-1,height=-1,internal=-1,samples=-1;
    template<class Current> bool read(const Procedures& gl,GLuint fbo,Current current) {
        if (!current()) return false;
        framebuffer_exists=gl.is_framebuffer(fbo)==GL_TRUE;
        if (!current()) return false;
        if (!framebuffer_exists) return true;
        gl.attachment(fbo,GL_COLOR_ATTACHMENT0,GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE,&type);
        if (!current()) return false;
        if (type!=GL_RENDERBUFFER) return true;
        gl.attachment(fbo,GL_COLOR_ATTACHMENT0,GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME,&name);
        if (!current()) return false;
        if (name<=0) return true;
        const auto rbo=static_cast<GLuint>(name);
        renderbuffer_exists=gl.is_renderbuffer(rbo)==GL_TRUE;
        if (!current()) return false;
        if (!renderbuffer_exists) return true;
        gl.renderbuffer(rbo,GL_RENDERBUFFER_WIDTH,&width);if (!current()) return false;
        gl.renderbuffer(rbo,GL_RENDERBUFFER_HEIGHT,&height);if (!current()) return false;
        gl.renderbuffer(rbo,GL_RENDERBUFFER_INTERNAL_FORMAT,&internal);if (!current()) return false;
        gl.renderbuffer(rbo,GL_RENDERBUFFER_SAMPLES,&samples);return current();
    }
    bool sameFramebuffer(const SourceInfo& b) const {
        return framebuffer_exists==b.framebuffer_exists && type==b.type && name==b.name;
    }
    bool sameRenderbuffer(const SourceInfo& b) const {
        return renderbuffer_exists==b.renderbuffer_exists && width==b.width && height==b.height && internal==b.internal && samples==b.samples;
    }
    const char* typeName() const {
        if (type==GL_RENDERBUFFER) return "GL_RENDERBUFFER";
        if (type==GL_TEXTURE) return "GL_TEXTURE";
        if (type==GL_NONE) return "GL_NONE";
        return type==-1 ? "QUERY_SENTINEL_UNCHANGED" : "OTHER_UNSUPPORTED_ENUM";
    }
    void report(QJsonObject& object) const {
        object.insert("source_framebuffer_exists",framebuffer_exists);
        object.insert("source_attachment_type_enum",type); object.insert("source_attachment_type_name",typeName());
        object.insert("source_attachment_is_renderbuffer",type==GL_RENDERBUFFER && renderbuffer_exists);
        object.insert("source_renderbuffer_width",width); object.insert("source_renderbuffer_height",height);
        object.insert("source_renderbuffer_internal_format_enum",internal);
        object.insert("source_renderbuffer_internal_format_name",internal==GL_RGBA8 ? "GL_RGBA8" : "OTHER_OR_UNKNOWN");
        object.insert("source_renderbuffer_samples",samples);
    }
};

// Allocated later than RestoreGuard: cleanup necessarily precedes restoration.
// The only tracked names originate from DSA Create* output arguments; source RBO
// is never tracked, deleted, rebound or used as a destination.
class OwnedResources {
public:
    OwnedResources(QOpenGLContext* target,QSurface* surface,WglCurrentContextFn rc,WglCurrentDcFn dc,
            Procedures& gl,OwnershipState& state)
        : target_(target),surface_(surface),surface_object_(surfaceObject(surface)),get_rc_(rc),get_dc_(dc),gl_(gl),state_(state) {}
    ~OwnedResources() noexcept { cleanup(); }
    void armPair(HGLRC rc,HDC dc) noexcept { rc_=rc;dc_=dc; }
    bool current() const {
        if (!target_ || !surface_object_ || !rc_ || !dc_ || QOpenGLContext::currentContext()!=target_ || target_->surface()!=surface_) return false;
        auto* native=target_->nativeInterface<QNativeInterface::QWGLContext>();
        return native && native->nativeContext()==rc_ && get_rc_()==rc_ && get_dc_()==dc_;
    }
    void sync() noexcept {
        state_.framebuffer_created=(framebuffers[0]!=0)+(framebuffers[1]!=0);
        state_.texture_created=texture!=0;
        state_.live=state_.framebuffer_created+state_.texture_created-state_.framebuffer_deleted-state_.texture_deleted;
        state_.not_required=state_.framebuffer_created+state_.texture_created==0;
        if (!state_.not_required) { state_.cleanup_verified=false;state_.names_invalid=false; }
    }
    void cleanup() noexcept {
        if (finished_) return;
        finished_=true;sync();
        if (state_.not_required) {state_.cleanup_verified=true;state_.names_invalid=true;return;}
        state_.cleanup_attempted=true;
        try {
            if (!current() || !gl_.delete_framebuffers || !gl_.delete_textures || !gl_.is_framebuffer || !gl_.is_texture) {
                state_.cleanup_failed=true;return;
            }
            GLint read_binding=-1,draw_binding=-1;
            gl_.integer(GL_READ_FRAMEBUFFER_BINDING,&read_binding);
            if (!current()) {state_.cleanup_failed=true;return;}
            gl_.integer(GL_DRAW_FRAMEBUFFER_BINDING,&draw_binding);
            if (!current()) {state_.cleanup_failed=true;return;}
            for (GLuint id:framebuffers) if (id && (id==static_cast<GLuint>(read_binding) || id==static_cast<GLuint>(draw_binding))) {
                state_.cleanup_failed=true;return; // Never delete an unexpectedly bound object.
            }
            // Never bound: deletion affects only these owned object names.
            ++state_.delete_calls;gl_.delete_framebuffers(2,framebuffers);
            if (!current()) {state_.cleanup_failed=true;return;}
            for (GLuint id:framebuffers) if (id) {
                if (!current()) {state_.cleanup_failed=true;return;}
                if (gl_.is_framebuffer(id)==GL_FALSE) ++state_.framebuffer_deleted;
                state_.live=state_.framebuffer_created+state_.texture_created-state_.framebuffer_deleted-state_.texture_deleted;
            }
            if (!current()) {state_.cleanup_failed=true;return;}
            if (texture) {
                ++state_.delete_calls;gl_.delete_textures(1,&texture);
                if (!current()) {state_.cleanup_failed=true;return;}
                if (gl_.is_texture(texture)==GL_FALSE) state_.texture_deleted=1;
            }
            state_.live=state_.framebuffer_created+state_.texture_created-state_.framebuffer_deleted-state_.texture_deleted;
            state_.names_invalid=state_.live==0;
            state_.cleanup_verified=state_.names_invalid;
            state_.cleanup_failed=!state_.cleanup_verified;
        } catch (...) {state_.cleanup_failed=true;state_.cleanup_verified=false;state_.names_invalid=false;}
    }
    GLuint framebuffers[2]{},texture=0;
private:
    QPointer<QOpenGLContext> target_;
    QSurface* surface_;
    QPointer<QObject> surface_object_;
    WglCurrentContextFn get_rc_;WglCurrentDcFn get_dc_;
    Procedures& gl_;OwnershipState& state_;
    HGLRC rc_=nullptr;HDC dc_=nullptr;
    bool finished_=false;
};

void ownershipReport(QJsonObject& report,const OwnershipState& state) {
    report.insert("owned_resources_cleanup_verified",state.cleanup_verified);
    report.insert("owned_resource_cleanup_failed",state.cleanup_failed);
    report.insert("owned_cleanup_not_required",state.not_required);
    report.insert("owned_cleanup_attempted",state.cleanup_attempted);
    report.insert("owned_name_invalidation_verified",state.names_invalid);
    report.insert("owned_framebuffer_objects_created",state.framebuffer_created);
    report.insert("owned_framebuffer_objects_deleted",state.framebuffer_deleted);
    report.insert("owned_texture_objects_created",state.texture_created);
    report.insert("owned_texture_objects_deleted",state.texture_deleted);
    report.insert("owned_live_object_count",state.live);
    report.insert("GL_object_creation_calls",state.create_calls);
    report.insert("GL_owned_attachment_write_calls",state.attachment_calls);
    report.insert("GL_owned_buffer_selection_calls",state.buffer_calls);
    report.insert("GL_owned_texture_storage_calls",state.storage_calls);
    report.insert("GL_owned_texture_clear_calls",state.clear_calls);
    report.insert("GL_owned_color_blit_calls",state.blit_calls);
    report.insert("GL_rendering_command_calls",state.blit_calls);
    report.insert("DSA_read_calls",state.read_calls);
    report.insert("DSA_baseline_read_calls",state.baseline_reads);
    report.insert("DSA_copy_read_calls",state.copy_reads);
    report.insert("GL_owned_delete_calls",state.delete_calls);
}

QImage capture(const Scope& scope, const Canvas& canvas, Budget& budget,
        const Physical& physical_before, const QJsonObject& view_before,
        QJsonObject& report, bool& started, bool& recovery) {
    // One normal viewport paint, then a DSA read of its actual renderbuffer.
    // No grabFramebuffer(), private Qt flags, model access or event pump.
    require(receiverAlive(scope, canvas) && canvas.viewport->isValid() && !canvas.viewport->paintingActive(), "CAPTURE_VIEWPORT_NOT_READY");
    require(!canvas.viewport->format().stereo() && canvas.viewport->currentTargetBuffer() == QOpenGLWidget::LeftBuffer,
        "CAPTURE_NONSTEREO_LEFT_BUFFER_REQUIRED");
    HMODULE opengl = GetModuleHandleW(L"opengl32.dll"); require(opengl != nullptr, "EXISTING_OPENGL32_REQUIRED");
    const auto get_rc = nativeProc<WglCurrentContextFn>(opengl, "wglGetCurrentContext");
    const auto get_dc = nativeProc<WglCurrentDcFn>(opengl, "wglGetCurrentDC");
    require(get_rc && get_dc, "WGL_GETTERS_REQUIRED");
    QPointer<QOpenGLContext> target = canvas.viewport->context(), original = QOpenGLContext::currentContext();
    require(desktop45(target) && desktop45(original) && target->thread() == scope.app->thread()
        && original->thread() == scope.app->thread(), "EXISTING_DESKTOP_GL45_CONTEXTS_REQUIRED");
    QSurface* original_surface = original->surface(); QSurface* target_surface = target->surface();
    QPointer<QObject> original_lifetime = surfaceObject(original_surface), target_lifetime = surfaceObject(target_surface);
    require(original_lifetime && target_lifetime && existingSurface(original_surface) && existingSurface(target_surface), "EXISTING_GL_SURFACES_REQUIRED");
    const auto original_native = original->nativeInterface<QNativeInterface::QWGLContext>();
    const auto target_native = target->nativeInterface<QNativeInterface::QWGLContext>();
    const HGLRC original_rc = get_rc(), target_rc = target_native ? target_native->nativeContext() : nullptr;
    const HDC original_dc = get_dc();
    require(original_native && original_rc && original_dc && original_native->nativeContext() == original_rc && target_rc,
        "ORIGINAL_QT_WGL_PAIR_REQUIRED");
    const GLuint source_fbo = canvas.viewport->defaultFramebufferObject(); require(source_fbo != 0, "EXISTING_WIDGET_FBO_REQUIRED");
    const auto integer = glProc<GetIntegerFn>(original, "glGetIntegerv");
    const auto is_fbo = glProc<IsFramebufferFn>(original, "glIsFramebuffer");
    const auto bind = glProc<BindFramebufferFn>(original, "glBindFramebuffer");
    require(integer && is_fbo && bind, "GL_BINDING_PROCEDURES_REQUIRED");
    GLint old_draw = -1, old_read = -1; integer(GL_DRAW_FRAMEBUFFER_BINDING, &old_draw); integer(GL_READ_FRAMEBUFFER_BINDING, &old_read);
    require(old_draw >= 0 && old_read >= 0 && (!old_draw || is_fbo(GLuint(old_draw)) == GL_TRUE)
        && (!old_read || is_fbo(GLuint(old_read)) == GL_TRUE), "ORIGINAL_FBO_BINDINGS_UNKNOWN");
    RestoreState restored; PaintState paint; OwnershipState own; Procedures gl;
    RestoreGuard context_guard(original, original_surface, original_rc, original_dc, get_rc, get_dc, restored);
    BindingRestoreGuard bindings(context_guard, restored, paint, original, original_surface, original_rc, original_dc,
        get_rc, get_dc, integer, is_fbo, bind, GLuint(old_draw), GLuint(old_read));
    OwnedResources resources(target, target_surface, get_rc, get_dc, gl, own);
    bool source_observed = false, stores_observed = false, source_same = false, gl_state_same = false;
    SourceInfo source; PixelStore pack, unpack; GLint initial_read = -1, initial_draw = -1;
    const auto stable = [&](const char* stage) {
        const auto reject = [&](const char* reason, QJsonObject observed) {
            observed.insert("stage", stage);
            observed.insert("reason", reason);
            report.insert("capture_guard_failure", observed);
            throw Reject{"CAPTURE_VIEW_OR_PHYSICAL_CHANGED"};
        };
        if (!receiverAlive(scope, canvas))
            reject("RECEIVER_CHANGED", {{"receiver_valid", false}});
        if (!target || !target_lifetime || canvas.viewport->context() != target)
            reject("GL_CONTEXT_CHANGED", {{"receiver_valid", true}, {"gl_context_unchanged", false}});
        if (!samePhysical(physical_before, physical(scope)))
            reject("PHYSICAL_CHANGED", {{"receiver_valid", true}, {"gl_context_unchanged", true},
                                        {"physical_unchanged", false}});
        if (view_before.value("mapping_fingerprint") != snapshot(scope, canvas).value("mapping_fingerprint"))
            reject("MAPPING_CHANGED", {{"receiver_valid", true}, {"gl_context_unchanged", true},
                                       {"physical_unchanged", true}, {"mapping_unchanged", false}});
    };
    const auto finish = [&]() {
        resources.cleanup();
        if (source_observed && resources.current()) {
            SourceInfo after;
            source_same = after.read(gl, source_fbo, [&]() { return resources.current(); })
                && source.sameFramebuffer(after) && source.sameRenderbuffer(after);
        }
        if (stores_observed && resources.current()) {
            PixelStore pa, ua; pa.read(gl.integer, false); ua.read(gl.integer, true);
            GLint r = -1, d = -1; gl.integer(GL_READ_FRAMEBUFFER_BINDING, &r); gl.integer(GL_DRAW_FRAMEBUFFER_BINDING, &d);
            gl_state_same = pack.same(pa) && unpack.same(ua) && r == initial_read && d == initial_draw;
            report.insert("pack_after", pa.json()); report.insert("unpack_after", ua.json());
        }
        bindings.restore();
        report.insert("capture_method", "one_viewport_Paint_then_owned_DSA_renderbuffer_resolve_v2");
        report.insert("viewport_makeCurrent_calls", paint.make_calls);
        report.insert("direct_QPaintEvent_send_calls", paint.send_calls);
        report.insert("paint_event_sent", paint.sent); report.insert("paint_event_returned", paint.returned);
        report.insert("paint_send_return", paint.send_return); report.insert("paint_event_accepted", paint.accepted);
        report.insert("paint_nonspontaneous", paint.nonspontaneous);
        report.insert("original_Qt_context_surface_restored", restored.qt_restored && !restored.failed);
        report.insert("original_WGL_context_dc_restored", restored.wgl_restored && !restored.failed);
        report.insert("original_framebuffer_bindings_restored", paint.bindings_restored && !paint.binding_failed);
        report.insert("GL_original_framebuffer_binding_restore_calls", paint.binding_calls);
        report.insert("source_metadata_before_after_verified", source_same);
        report.insert("app_GL_observed_state_unchanged", gl_state_same);
        report.insert("capture_effects_started", started);
        report.insert("GL_state_entirely_unchanged_certified", false);
        report.insert("source_object_write_freedom_certified", false); // Normal MD Paint/cache effects are disclosed.
        report.insert("normal_Qt_MD_draw_cache_effects_allowed", true);
        report.insert("direct_resolve_source_object_writer_calls", 0);
        report.insert("raw_GL_names_returned", false);
        report.insert("new_context_calls", 0); report.insert("new_surface_calls", 0);
        report.insert("GL_error_state_observed", false); report.insert("read_errors_certified", false);
        report.insert("pixels_freshness_verified", false); report.insert("pixels_scene_content_verified", false);
        ownershipReport(report, own);
    };
    try {
        budget.check(); stable("before_make_current");
        started = true; context_guard.beginAttempt(); ++paint.make_calls; canvas.viewport->makeCurrent();
        stable("after_make_current"); require(resources.current() == false, "INTERNAL_RESOURCE_PAIR_NOT_ARMED_EXPECTED");
        require(target && target_lifetime && QOpenGLContext::currentContext() == target && target->surface() == target_surface
            && get_rc() == target_rc && get_dc(), "VIEWPORT_MAKECURRENT_TARGET_MISMATCH");
        const HDC target_dc = get_dc(); resources.armPair(target_rc, target_dc);
        require(gl.resolve(target), "GL45_OWNED_RESOLVE_PROCEDURES_REQUIRED");
        GLint draw = -1, read = -1; gl.integer(GL_DRAW_FRAMEBUFFER_BINDING, &draw); gl.integer(GL_READ_FRAMEBUFFER_BINDING, &read);
        require(draw >= 0 && read >= 0 && GLuint(draw) == source_fbo && GLuint(read) == source_fbo
            && gl.is_framebuffer(source_fbo) == GL_TRUE && canvas.viewport->defaultFramebufferObject() == source_fbo
            && !canvas.viewport->paintingActive(), "VIEWPORT_DEFAULT_FBO_NOT_BOUND_FOR_PAINT");
        budget.check(); stable("before_paint"); QPaintEvent event(QRect(QPoint(0, 0), canvas.viewport->size())); event.setAccepted(false);
        paint.nonspontaneous = !event.spontaneous(); require(paint.nonspontaneous, "NONSPONTANEOUS_PAINT_REQUIRED");
        paint.sent = true; ++paint.send_calls;
        try { paint.send_return = QCoreApplication::sendEvent(canvas.viewport, &event); paint.returned = true;
            paint.accepted = event.isAccepted(); paint.nonspontaneous = paint.nonspontaneous && !event.spontaneous(); }
        catch (...) { paint.cpp_exception = true; throw; }
        stable("after_paint"); require(resources.current() && paint.returned && paint.nonspontaneous && !canvas.viewport->paintingActive()
            && canvas.viewport->defaultFramebufferObject() == source_fbo, "POST_PAINT_SCOPE_CHANGED");
        require(source.read(gl, source_fbo, [&]() { return resources.current(); }), "SOURCE_QUERY_CONTEXT_CHANGED");
        source_observed = true; source.report(report);
        require(source.framebuffer_exists && source.type == GL_RENDERBUFFER && source.renderbuffer_exists,
            "SOURCE_ATTACHMENT_NOT_RENDERBUFFER");
        const GLint width = source.width, height = source.height; GLint max_samples = -1; gl.integer(GL_MAX_SAMPLES, &max_samples);
        const qreal dpr = canvas.viewport->devicePixelRatioF(); const QSize logical = canvas.viewport->size();
        require(width > 0 && height > 0 && width <= 16384 && height <= 16384 && qint64(width) * height <= 16777216
            && std::abs(width - logical.width() * dpr) <= 1 && std::abs(height - logical.height() * dpr) <= 1
            && source.internal == GL_RGBA8 && source.samples >= 0 && max_samples >= source.samples, "SOURCE_RGBA8_DIMENSIONS_SAMPLES_REJECTED");
        pack.read(gl.integer, false); unpack.read(gl.integer, true); stores_observed = true;
        gl.integer(GL_READ_FRAMEBUFFER_BINDING, &initial_read); gl.integer(GL_DRAW_FRAMEBUFFER_BINDING, &initial_draw);
        require(pack.safe() && unpack.safe(), "PACK_UNPACK_STATE_UNSUPPORTED");
        report.insert("pack_before", pack.json()); report.insert("unpack_before", unpack.json());
        const auto blit_guard = [&](const char* stage) {
            stable(stage); require(resources.current(), "RESOLVE_CONTEXT_CHANGED");
            GLint enabled = -1, box[4]{-1,-1,-1,-1}, origin = -1, srgb = -1;
            gl.integer(GL_SCISSOR_TEST, &enabled); gl.integer(GL_SCISSOR_BOX, box);
            gl.integer(GL_CLIP_ORIGIN, &origin); gl.integer(GL_FRAMEBUFFER_SRGB, &srgb);
            require((enabled == 0 || (enabled == 1 && box[0] <= 0 && box[1] <= 0
                && qint64(box[0]) + box[2] >= width && qint64(box[1]) + box[3] >= height))
                && origin == GL_LOWER_LEFT && srgb == 0, "SCISSOR_CLIP_SRGB_STATE_UNSUPPORTED");
        };
        const bool compatibility = target->format().profile() == QSurfaceFormat::CompatibilityProfile;
        if (compatibility) {
            GLint extensions = -1; gl.integer(GL_NUM_EXTENSIONS, &extensions);
            require(extensions > 0 && extensions <= 4096, "BOUNDED_GL_EXTENSION_SET_REQUIRED");
            bool imaging = false, histogram = false;
            for (GLint i = 0; i < extensions; ++i) {
                budget.check(); require(resources.current(), "EXTENSION_CONTEXT_CHANGED");
                const auto text = gl.string_i(GL_EXTENSIONS, GLuint(i)); require(text != nullptr, "NULL_EXTENSION_TOKEN");
                std::size_t n = 0; while (n < 256 && text[n]) ++n; require(n < 256, "UNBOUNDED_EXTENSION_TOKEN");
                imaging = imaging || (n == 14 && !std::memcmp(text, "GL_ARB_imaging", 14));
                histogram = histogram || (n == 16 && !std::memcmp(text, "GL_EXT_histogram", 16));
            }
            require((imaging || histogram) && gl.enabled(GL_HISTOGRAM) == GL_FALSE && gl.enabled(GL_MINMAX) == GL_FALSE,
                "COMPAT_IMAGING_STATE_UNSUPPORTED");
        }
        const std::size_t stride = (std::size_t(width) * 4 + pack.alignment - 1) / pack.alignment * pack.alignment;
        const std::size_t bytes = stride * height; require(bytes <= 67108864, "OWNED_RESOLVE_SCRATCH_LIMIT");
        std::vector<unsigned char> first(bytes, 0), work(bytes, 0);
        budget.check(); blit_guard("before_owned_allocation"); ++own.create_calls;
        gl.create_framebuffers(2, resources.framebuffers); resources.sync();
        require(resources.current(), "CONTEXT_CHANGED_AFTER_FBO_CREATE");
        ++own.create_calls; gl.create_textures(GL_TEXTURE_2D, 1, &resources.texture); resources.sync();
        require(resources.current() && resources.framebuffers[0] && resources.framebuffers[1]
            && resources.framebuffers[0] != resources.framebuffers[1] && resources.texture
            && gl.is_framebuffer(resources.framebuffers[0]) == GL_TRUE && gl.is_framebuffer(resources.framebuffers[1]) == GL_TRUE
            && gl.is_texture(resources.texture) == GL_TRUE, "OWNED_DSA_CREATION_UNVERIFIED");
        ++own.storage_calls; gl.storage(resources.texture, 1, GL_RGBA8, width, height);
        require(resources.current(), "CONTEXT_CHANGED_AFTER_TEXTURE_STORAGE");
        GLint w = -1, h = -1, format = -1; gl.texture_level(resources.texture, 0, GL_TEXTURE_WIDTH, &w);
        gl.texture_level(resources.texture, 0, GL_TEXTURE_HEIGHT, &h); gl.texture_level(resources.texture, 0, GL_TEXTURE_INTERNAL_FORMAT, &format);
        require(w == width && h == height && format == GL_RGBA8, "OWNED_TEXTURE_STORAGE_UNVERIFIED");
        ++own.attachment_calls; gl.attach_renderbuffer(resources.framebuffers[0], GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, GLuint(source.name));
        require(resources.current(), "CONTEXT_CHANGED_AFTER_OWN_READ_ATTACHMENT");
        ++own.attachment_calls; gl.attach_texture(resources.framebuffers[1], GL_COLOR_ATTACHMENT0, resources.texture, 0);
        require(resources.current(), "CONTEXT_CHANGED_AFTER_OWN_WRITE_ATTACHMENT");
        ++own.buffer_calls; gl.read_buffer(resources.framebuffers[0], GL_COLOR_ATTACHMENT0);
        ++own.buffer_calls; gl.draw_buffer(resources.framebuffers[1], GL_COLOR_ATTACHMENT0);
        require(resources.current() && gl.check_framebuffer(resources.framebuffers[0], GL_READ_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE
            && gl.check_framebuffer(resources.framebuffers[1], GL_DRAW_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE, "OWNED_FRAMEBUFFER_INCOMPLETE");
        const auto clear_owned = [&](const void* data) {
            budget.check(); stable("before_owned_clear"); require(resources.current(), "OWNED_CLEAR_CONTEXT_CHANGED");
            PixelStore current; current.read(gl.integer, true); require(unpack.same(current) && current.safe(), "UNPACK_CHANGED");
            require(!compatibility || (gl.enabled(GL_HISTOGRAM) == GL_FALSE && gl.enabled(GL_MINMAX) == GL_FALSE), "COMPAT_IMAGING_CHANGED");
            ++own.clear_calls; gl.clear(resources.texture, 0, GL_RGBA, GL_UNSIGNED_BYTE, data);
            require(resources.current(), "CONTEXT_CHANGED_AFTER_OWN_CLEAR");
        };
        const auto read_owned = [&](std::vector<unsigned char>& buffer, bool baseline) {
            budget.check(); stable("before_owned_read"); require(resources.current(), "OWNED_READ_CONTEXT_CHANGED");
            PixelStore current; current.read(gl.integer, false); require(pack.same(current) && current.safe(), "PACK_CHANGED");
            ++own.read_calls; if (baseline) ++own.baseline_reads; else ++own.copy_reads;
            gl.image(resources.texture, 0, GL_RGBA, GL_UNSIGNED_BYTE, GLsizei(bytes), buffer.data());
            current.read(gl.integer, false); require(resources.current() && pack.same(current), "CONTEXT_OR_PACK_CHANGED_AFTER_READ");
        };
        const auto blit = [&]() { budget.check(); blit_guard("before_owned_blit"); ++own.blit_calls;
            gl.blit(resources.framebuffers[0], resources.framebuffers[1], 0, 0, width, height, 0, 0, width, height, GL_COLOR_BUFFER_BIT, GL_NEAREST);
            require(resources.current(), "CONTEXT_CHANGED_AFTER_OWN_BLIT"); };
        clear_owned(nullptr); read_owned(work, true);
        for (GLint y = 0; y < height; ++y) for (GLint x = 0; x < width; ++x) {
            const auto at = std::size_t(y) * stride + std::size_t(x) * 4;
            require(!work[at] && !work[at+1] && !work[at+2] && !work[at+3], "ZERO_BASELINE_NOT_ZERO");
        }
        blit(); read_owned(first, false);
        const unsigned char sentinel[8]{37,91,173,255,0,0,0,0}; clear_owned(sentinel); read_owned(work, true);
        for (GLint y = 0; y < height; ++y) for (GLint x = 0; x < width; ++x) {
            const auto at = std::size_t(y) * stride + std::size_t(x) * 4;
            require(work[at] || work[at+1] || work[at+2] || work[at+3], "SECOND_BASELINE_NOT_DISTINCT");
        }
        blit(); read_owned(work, false);
        QImage result(width, height, QImage::Format_RGBA8888); require(!result.isNull(), "CPU_IMAGE_ALLOCATION_FAILED");
        for (GLint y = 0; y < height; ++y) {
            require(!std::memcmp(first.data() + std::size_t(y) * stride, work.data() + std::size_t(y) * stride, std::size_t(width) * 4), "DUAL_COPY_PIXEL_MISMATCH");
            std::memcpy(result.scanLine(height - 1 - y), first.data() + std::size_t(y) * stride, std::size_t(width) * 4);
        }
        report.insert("dual_copies_bit_exact_equal_every_pixel", true);
        report.insert("dual_baseline_owned_coverage_verified", true);
        report.insert("pixel_origin", "top_left_after_CPU_vertical_flip");
        finish(); stable("after_cleanup"); require(own.cleanup_verified && !own.cleanup_failed && own.live == 0
            && !restored.failed && paint.bindings_restored && !paint.binding_failed && source_same && gl_state_same,
            "CAPTURE_GL_CLEANUP_OR_RESTORATION_UNVERIFIED");
        report.insert("capture_recovery_verified", true); return result;
    } catch (...) { finish(); recovery = started; report.insert("capture_recovery_verified", false); throw; }
}
} // namespace capture_gl
