// 原生 OpenXR 视频输出。网络/解码仍在 Java 通讯模块；这里仅负责 XR 会话与最新帧合成。
#include <jni.h>
#include <android/log.h>
#include <android/bitmap.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#define INFO(...) __android_log_print(ANDROID_LOG_INFO, "IWIN_XR", __VA_ARGS__)

static void check(XrResult result, const char* operation) {
    if (XR_FAILED(result)) throw std::runtime_error(std::string(operation) + " failed: " + std::to_string(result));
}

struct Renderer {
    std::atomic<bool> stop{true};
    std::mutex frameMutex;
    std::vector<unsigned char> pendingPixels;
    uint32_t pendingWidth = 0, pendingHeight = 0;
    bool framePending = false;
    std::vector<unsigned char> pendingHudPixels;
    uint32_t pendingHudWidth = 0, pendingHudHeight = 0;
    bool hudPending = false;
};

// 所有 OpenXR / GLES 资源只在同一渲染线程创建和销毁。
struct XrSessionResources {
    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrSpace localSpace = XR_NULL_HANDLE, viewSpace = XR_NULL_HANDLE;
    XrSwapchain swapchain = XR_NULL_HANDLE;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    GLuint program = 0, frameTexture = 0, framebuffer = 0, vertexArray = 0;
    uint32_t textureWidth = 0, textureHeight = 0;
    GLuint hudTexture = 0;
    uint32_t hudWidth = 0, hudHeight = 0;
    uint32_t screenWidth = 1920, screenHeight = 1080;
    std::vector<XrSwapchainImageOpenGLESKHR> images;
    bool running = false;
    bool focused = false;
    XrActionSet actionSet = XR_NULL_HANDLE;
    XrAction stick = XR_NULL_HANDLE, trigger = XR_NULL_HANDLE, grip = XR_NULL_HANDLE;
    XrAction primary = XR_NULL_HANDLE, secondary = XR_NULL_HANDLE, stickClick = XR_NULL_HANDLE;
    XrAction poseAction = XR_NULL_HANDLE, menu = XR_NULL_HANDLE;
    XrPath hands[2]{};
    XrSpace controllerSpace = XR_NULL_HANDLE;
    JNIEnv* environment = nullptr;
    jobject activity = nullptr;
    jmethodID controllerCallback = nullptr;
    std::chrono::steady_clock::time_point lastInputAt{};

    ~XrSessionResources() {
        if (context != EGL_NO_CONTEXT) {
            if (program) glDeleteProgram(program);
            if (frameTexture) glDeleteTextures(1, &frameTexture);
            if (hudTexture) glDeleteTextures(1, &hudTexture);
            if (framebuffer) glDeleteFramebuffers(1, &framebuffer);
            if (vertexArray) glDeleteVertexArrays(1, &vertexArray);
        }
        if (swapchain) xrDestroySwapchain(swapchain);
        if (controllerSpace) xrDestroySpace(controllerSpace);
        if (viewSpace) xrDestroySpace(viewSpace);
        if (localSpace) xrDestroySpace(localSpace);
        if (session) xrDestroySession(session);
        if (actionSet) xrDestroyActionSet(actionSet);
        if (instance) xrDestroyInstance(instance);
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            if (surface != EGL_NO_SURFACE) eglDestroySurface(display, surface);
            if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
            eglTerminate(display);
        }
    }
};

static GLuint compileShader(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint success; glGetShaderiv(shader, GL_COMPILE_STATUS, &success);
    if (!success) {
        char message[1024]; glGetShaderInfoLog(shader, sizeof(message), nullptr, message);
        glDeleteShader(shader); throw std::runtime_error(message);
    }
    return shader;
}

static void initializeGraphics(XrSessionResources& resources) {
    resources.display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (!eglInitialize(resources.display, nullptr, nullptr)) throw std::runtime_error("eglInitialize failed");
    const EGLint attributes[] = {EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
    EGLConfig config; EGLint count;
    if (!eglChooseConfig(resources.display, attributes, &config, 1, &count) || count < 1)
        throw std::runtime_error("No GLES3 EGL config");
    const EGLint contextAttributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    resources.context = eglCreateContext(resources.display, config, EGL_NO_CONTEXT, contextAttributes);
    const EGLint surfaceAttributes[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
    resources.surface = eglCreatePbufferSurface(resources.display, config, surfaceAttributes);
    if (!eglMakeCurrent(resources.display, resources.surface, resources.surface, resources.context))
        throw std::runtime_error("eglMakeCurrent failed");
    const char* vertexSource = R"(#version 300 es
        out vec2 textureUv;
        const vec2 corners[4] = vec2[4](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(1,1));
        void main() {
            vec2 corner = corners[gl_VertexID];
            gl_Position = vec4(corner, 0, 1);
            textureUv = vec2((corner.x + 1.0) * 0.5, (1.0 - corner.y) * 0.5);
        })";
    const char* fragmentSource = R"(#version 300 es
        precision mediump float;
        in vec2 textureUv;
        uniform sampler2D videoFrame;
        out vec4 color;
        void main() { color = vec4(texture(videoFrame, textureUv).rgb, 1.0); })";
    GLuint vertex = compileShader(GL_VERTEX_SHADER, vertexSource);
    GLuint fragment = compileShader(GL_FRAGMENT_SHADER, fragmentSource);
    resources.program = glCreateProgram();
    glAttachShader(resources.program, vertex); glAttachShader(resources.program, fragment);
    glLinkProgram(resources.program); glDeleteShader(vertex); glDeleteShader(fragment);
    GLint linked; glGetProgramiv(resources.program, GL_LINK_STATUS, &linked);
    if (!linked) throw std::runtime_error("Video shader link failed");
    glGenVertexArrays(1, &resources.vertexArray);
    glGenFramebuffers(1, &resources.framebuffer);
    glGenTextures(1, &resources.frameTexture);
    glBindTexture(GL_TEXTURE_2D, resources.frameTexture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenTextures(1, &resources.hudTexture);
    glBindTexture(GL_TEXTURE_2D, resources.hudTexture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
}

static void initializeOpenXr(JNIEnv* environment, jobject activity, XrSessionResources& resources) {
    JavaVM* virtualMachine = nullptr; environment->GetJavaVM(&virtualMachine);
    PFN_xrInitializeLoaderKHR initializeLoader = nullptr;
    check(xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
        reinterpret_cast<PFN_xrVoidFunction*>(&initializeLoader)), "xrInitializeLoaderKHR lookup");
    XrLoaderInitInfoAndroidKHR loaderInfo{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
    loaderInfo.applicationVM = virtualMachine; loaderInfo.applicationContext = activity;
    check(initializeLoader(reinterpret_cast<XrLoaderInitInfoBaseHeaderKHR*>(&loaderInfo)), "xrInitializeLoaderKHR");
    std::vector<const char*> extensions{XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME, XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME};
    uint32_t extensionCount = 0;
    check(xrEnumerateInstanceExtensionProperties(nullptr, 0, &extensionCount, nullptr), "extension count");
    std::vector<XrExtensionProperties> supportedExtensions(extensionCount, {XR_TYPE_EXTENSION_PROPERTIES});
    check(xrEnumerateInstanceExtensionProperties(nullptr, extensionCount, &extensionCount, supportedExtensions.data()), "extensions");
    for (const auto& extension : supportedExtensions)
        if (std::strcmp(extension.extensionName, "XR_BD_controller_interaction") == 0)
            extensions.push_back("XR_BD_controller_interaction");
    XrInstanceCreateInfoAndroidKHR androidInfo{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    androidInfo.applicationVM = virtualMachine; androidInfo.applicationActivity = activity;
    XrInstanceCreateInfo instanceInfo{XR_TYPE_INSTANCE_CREATE_INFO};
    instanceInfo.next = &androidInfo;
    std::strcpy(instanceInfo.applicationInfo.applicationName, "IWIN LAN XR");
    instanceInfo.applicationInfo.applicationVersion = 4;
    instanceInfo.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    instanceInfo.enabledExtensionCount = extensions.size(); instanceInfo.enabledExtensionNames = extensions.data();
    check(xrCreateInstance(&instanceInfo, &resources.instance), "xrCreateInstance");
    XrInstanceProperties properties{XR_TYPE_INSTANCE_PROPERTIES};
    check(xrGetInstanceProperties(resources.instance, &properties), "xrGetInstanceProperties");
    INFO("Runtime: %s", properties.runtimeName);
    XrSystemGetInfo systemInfo{XR_TYPE_SYSTEM_GET_INFO}; systemInfo.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId systemId; check(xrGetSystem(resources.instance, &systemInfo, &systemId), "xrGetSystem");
    PFN_xrGetOpenGLESGraphicsRequirementsKHR requirementsFunction = nullptr;
    check(xrGetInstanceProcAddr(resources.instance, "xrGetOpenGLESGraphicsRequirementsKHR",
        reinterpret_cast<PFN_xrVoidFunction*>(&requirementsFunction)), "graphics requirements lookup");
    XrGraphicsRequirementsOpenGLESKHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
    check(requirementsFunction(resources.instance, systemId, &requirements), "graphics requirements");
    initializeGraphics(resources);
    XrGraphicsBindingOpenGLESAndroidKHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
    binding.display = resources.display; binding.context = resources.context;
    // EGLConfig 必须和当前 context 对应，不能随意使用一个新的配置。
    EGLint configId; eglQueryContext(resources.display, resources.context, EGL_CONFIG_ID, &configId);
    EGLint configAttributes[] = {EGL_CONFIG_ID, configId, EGL_NONE}; EGLint configCount;
    eglChooseConfig(resources.display, configAttributes, &binding.config, 1, &configCount);
    XrSessionCreateInfo sessionInfo{XR_TYPE_SESSION_CREATE_INFO}; sessionInfo.next = &binding; sessionInfo.systemId = systemId;
    check(xrCreateSession(resources.instance, &sessionInfo, &resources.session), "xrCreateSession");
    XrReferenceSpaceCreateInfo spaceInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    spaceInfo.poseInReferenceSpace.orientation.w = 1;
    spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    check(xrCreateReferenceSpace(resources.session, &spaceInfo, &resources.localSpace), "local space");
    spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
    check(xrCreateReferenceSpace(resources.session, &spaceInfo, &resources.viewSpace), "view space");
    uint32_t formatCount; check(xrEnumerateSwapchainFormats(resources.session, 0, &formatCount, nullptr), "formats count");
    std::vector<int64_t> formats(formatCount);
    check(xrEnumerateSwapchainFormats(resources.session, formatCount, &formatCount, formats.data()), "formats");
    int64_t format = 0;
    for (auto supported : formats) if (supported == GL_RGBA8) format = supported;
    if (!format) for (auto supported : formats) if (supported == GL_SRGB8_ALPHA8) format = supported;
    if (!format) throw std::runtime_error("No RGBA swapchain format");
    XrSwapchainCreateInfo swapchainInfo{XR_TYPE_SWAPCHAIN_CREATE_INFO};
    swapchainInfo.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
    swapchainInfo.format = format; swapchainInfo.sampleCount = 1;
    swapchainInfo.width = resources.screenWidth; swapchainInfo.height = resources.screenHeight;
    swapchainInfo.faceCount = 1; swapchainInfo.arraySize = 1; swapchainInfo.mipCount = 1;
    check(xrCreateSwapchain(resources.session, &swapchainInfo, &resources.swapchain), "xrCreateSwapchain");
    uint32_t imageCount; check(xrEnumerateSwapchainImages(resources.swapchain, 0, &imageCount, nullptr), "image count");
    resources.images.resize(imageCount, {XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
    check(xrEnumerateSwapchainImages(resources.swapchain, imageCount, &imageCount,
        reinterpret_cast<XrSwapchainImageBaseHeader*>(resources.images.data())), "swapchain images");
    INFO("OpenXR initialized; quad 4.0 x 2.25m, distance 2.5m, %ux%u", resources.screenWidth, resources.screenHeight);
}

// 控制器适配器：OpenXR Actions -> 原有 104 字节 UDP 快照。
static XrPath pathFor(XrSessionResources& resources, const std::string& text) {
    XrPath path; check(xrStringToPath(resources.instance, text.c_str(), &path), "input path"); return path;
}
static void initializeInput(XrSessionResources& resources, JNIEnv* environment, jobject activity) {
    resources.environment = environment; resources.activity = activity;
    jclass activityClass = environment->GetObjectClass(activity);
    resources.controllerCallback = environment->GetMethodID(activityClass, "onXrController", "([FII)V");
    environment->DeleteLocalRef(activityClass);
    XrActionSetCreateInfo setInfo{XR_TYPE_ACTION_SET_CREATE_INFO};
    std::strcpy(setInfo.actionSetName, "iwin_control"); std::strcpy(setInfo.localizedActionSetName, "IWIN Controller");
    check(xrCreateActionSet(resources.instance, &setInfo, &resources.actionSet), "action set");
    resources.hands[0] = pathFor(resources, "/user/hand/left"); resources.hands[1] = pathFor(resources, "/user/hand/right");
    auto create = [&](const char* name, XrActionType type, XrAction& action) {
        XrActionCreateInfo info{XR_TYPE_ACTION_CREATE_INFO}; info.actionType = type;
        std::strcpy(info.actionName, name); std::strcpy(info.localizedActionName, name);
        info.countSubactionPaths = 2; info.subactionPaths = resources.hands;
        check(xrCreateAction(resources.actionSet, &info, &action), "create action");
    };
    create("stick", XR_ACTION_TYPE_VECTOR2F_INPUT, resources.stick);
    create("trigger", XR_ACTION_TYPE_FLOAT_INPUT, resources.trigger);
    create("grip", XR_ACTION_TYPE_FLOAT_INPUT, resources.grip);
    create("primary", XR_ACTION_TYPE_BOOLEAN_INPUT, resources.primary);
    create("secondary", XR_ACTION_TYPE_BOOLEAN_INPUT, resources.secondary);
    create("stick_click", XR_ACTION_TYPE_BOOLEAN_INPUT, resources.stickClick);
    create("pose", XR_ACTION_TYPE_POSE_INPUT, resources.poseAction);
    create("menu", XR_ACTION_TYPE_BOOLEAN_INPUT, resources.menu);
    std::vector<XrActionSuggestedBinding> bindings;
    for (int hand = 0; hand < 2; ++hand) {
        std::string prefix = hand == 0 ? "/user/hand/left/input/" : "/user/hand/right/input/";
        bindings.push_back({resources.stick, pathFor(resources, prefix + "thumbstick")});
        bindings.push_back({resources.trigger, pathFor(resources, prefix + "trigger/value")});
        bindings.push_back({resources.grip, pathFor(resources, prefix + "squeeze/value")});
        bindings.push_back({resources.primary, pathFor(resources, prefix + (hand == 0 ? "x/click" : "a/click"))});
        bindings.push_back({resources.secondary, pathFor(resources, prefix + (hand == 0 ? "y/click" : "b/click"))});
        bindings.push_back({resources.stickClick, pathFor(resources, prefix + "thumbstick/click")});
        bindings.push_back({resources.poseAction, pathFor(resources, prefix + "grip/pose")});
    }
    // 应用菜单只绑定左手；系统 Home 键不冒充为可读应用输入。
    bindings.push_back({resources.menu, pathFor(resources, "/user/hand/left/input/menu/click")});
    for (const char* profile : {"/interaction_profiles/bytedance/pico4_controller",
                               "/interaction_profiles/bytedance/pico_neo3_controller",
                               "/interaction_profiles/oculus/touch_controller"}) {
        XrInteractionProfileSuggestedBinding suggestion{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        suggestion.interactionProfile = pathFor(resources, profile);
        suggestion.countSuggestedBindings = bindings.size(); suggestion.suggestedBindings = bindings.data();
        XrResult result = xrSuggestInteractionProfileBindings(resources.instance, &suggestion);
        INFO("Input profile %s: %d", profile, result);
    }
    XrSessionActionSetsAttachInfo attach{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
    attach.countActionSets = 1; attach.actionSets = &resources.actionSet;
    check(xrAttachSessionActionSets(resources.session, &attach), "attach input actions");
    XrActionSpaceCreateInfo space{XR_TYPE_ACTION_SPACE_CREATE_INFO};
    space.action = resources.poseAction; space.subactionPath = resources.hands[1]; space.poseInActionSpace.orientation.w = 1;
    check(xrCreateActionSpace(resources.session, &space, &resources.controllerSpace), "controller space");
}

static void notifyController(XrSessionResources& resources, const float* values, int buttons, int available) {
    auto* environment = resources.environment;
    jfloatArray array = environment->NewFloatArray(21);
    environment->SetFloatArrayRegion(array, 0, 21, values);
    environment->CallVoidMethod(resources.activity, resources.controllerCallback, array, buttons, available);
    environment->DeleteLocalRef(array);
}

static void pollInput(XrSessionResources& resources, XrTime time) {
    const auto now = std::chrono::steady_clock::now();
    if (now - resources.lastInputAt < std::chrono::milliseconds(20)) return;
    resources.lastInputAt = now;
    float values[21]{}; values[14] = 1;
    if (!resources.focused) { notifyController(resources, values, 0, 0); return; }
    XrActiveActionSet active{resources.actionSet, XR_NULL_PATH};
    XrActionsSyncInfo sync{XR_TYPE_ACTIONS_SYNC_INFO}; sync.countActiveActionSets = 1; sync.activeActionSets = &active;
    if (xrSyncActions(resources.session, &sync) != XR_SUCCESS) return;
    int buttons = 0, available = 0;
    for (int hand = 0; hand < 2; ++hand) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO}; info.subactionPath = resources.hands[hand];
        info.action = resources.stick;
        XrActionStateVector2f stick{XR_TYPE_ACTION_STATE_VECTOR2F};
        xrGetActionStateVector2f(resources.session, &info, &stick);
        if (stick.isActive) { available |= 1 << (16 + hand); values[hand * 2] = stick.currentState.x; values[hand * 2 + 1] = stick.currentState.y; }
        for (int axis = 0; axis < 2; ++axis) {
            info.action = axis == 0 ? resources.trigger : resources.grip;
            XrActionStateFloat value{XR_TYPE_ACTION_STATE_FLOAT}; xrGetActionStateFloat(resources.session, &info, &value);
            if (value.isActive) {
                available |= 1 << (18 + axis * 2 + hand);
                values[4 + axis * 2 + hand] = value.currentState;
                // 模拟扳机和握持键没有统一物理 click，数字状态按 0.75 阈值派生。
                const int mask = axis == 0 ? (1 << (14 + hand)) : (1 << (4 + hand));
                available |= mask;
                if (value.currentState >= .75f) buttons |= mask;
            }
        }
        XrAction buttonActions[] = {resources.primary, resources.secondary, resources.stickClick};
        int masks[] = {hand == 0 ? 1 << 2 : 1 << 0, hand == 0 ? 1 << 3 : 1 << 1, hand == 0 ? 1 << 6 : 1 << 7};
        for (int button = 0; button < 3; ++button) {
            info.action = buttonActions[button]; XrActionStateBoolean state{XR_TYPE_ACTION_STATE_BOOLEAN};
            xrGetActionStateBoolean(resources.session, &info, &state);
            if (state.isActive) available |= masks[button];
            if (state.isActive && state.currentState) buttons |= masks[button];
        }
    }
    XrActionStateGetInfo menuInfo{XR_TYPE_ACTION_STATE_GET_INFO};
    menuInfo.action = resources.menu; menuInfo.subactionPath = resources.hands[0];
    XrActionStateBoolean menuState{XR_TYPE_ACTION_STATE_BOOLEAN};
    xrGetActionStateBoolean(resources.session, &menuInfo, &menuState);
    if (menuState.isActive) available |= 1 << 8;
    if (menuState.isActive && menuState.currentState) buttons |= 1 << 8;
    XrSpaceVelocity velocity{XR_TYPE_SPACE_VELOCITY}; XrSpaceLocation pose{XR_TYPE_SPACE_LOCATION}; pose.next = &velocity;
    xrLocateSpace(resources.controllerSpace, resources.localSpace, time, &pose);
    if (pose.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT) {
        values[8] = pose.pose.position.x; values[9] = pose.pose.position.y; values[10] = pose.pose.position.z;
    }
    if (pose.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT) {
        values[11] = pose.pose.orientation.x; values[12] = pose.pose.orientation.y;
        values[13] = pose.pose.orientation.z; values[14] = pose.pose.orientation.w;
    }
    if (velocity.velocityFlags & XR_SPACE_VELOCITY_LINEAR_VALID_BIT) {
        values[15] = velocity.linearVelocity.x; values[16] = velocity.linearVelocity.y; values[17] = velocity.linearVelocity.z;
    }
    if (velocity.velocityFlags & XR_SPACE_VELOCITY_ANGULAR_VALID_BIT) {
        values[18] = velocity.angularVelocity.x; values[19] = velocity.angularVelocity.y; values[20] = velocity.angularVelocity.z;
    }
    notifyController(resources, values, buttons, available);
}

static void uploadLatestFrame(Renderer& renderer, XrSessionResources& resources) {
    std::lock_guard<std::mutex> lock(renderer.frameMutex);
    auto upload = [](GLuint texture, uint32_t& width, uint32_t& height,
                     uint32_t nextWidth, uint32_t nextHeight, const std::vector<unsigned char>& pixels) {
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, texture);
        if (width != nextWidth || height != nextHeight) {
            width = nextWidth; height = nextHeight;
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
        } else glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
    };
    if (renderer.framePending) {
        upload(resources.frameTexture, resources.textureWidth, resources.textureHeight,
               renderer.pendingWidth, renderer.pendingHeight, renderer.pendingPixels);
        renderer.framePending = false;
    }
    if (renderer.hudPending) {
        upload(resources.hudTexture, resources.hudWidth, resources.hudHeight,
               renderer.pendingHudWidth, renderer.pendingHudHeight, renderer.pendingHudPixels);
        renderer.hudPending = false;
    }
}

static void drawVideo(XrSessionResources& resources, uint32_t imageIndex) {
    glBindFramebuffer(GL_FRAMEBUFFER, resources.framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, resources.images[imageIndex].image, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("Video framebuffer incomplete");
    glDisable(GL_DEPTH_TEST); glDisable(GL_BLEND); glDisable(GL_SCISSOR_TEST);
    glViewport(0, 0, resources.screenWidth, resources.screenHeight);
    glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
    if (resources.textureWidth) {
        // 原始视频保持宽高比，竖屏素材使用黑边，避免拉伸。
        float aspect = float(resources.textureWidth) / float(resources.textureHeight);
        int width = resources.screenWidth, height = resources.screenHeight;
        if (aspect > float(width) / height) height = int(width / aspect);
        else width = int(height * aspect);
        glViewport((resources.screenWidth - width) / 2, (resources.screenHeight - height) / 2, width, height);
        glUseProgram(resources.program); glBindVertexArray(resources.vertexArray);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, resources.frameTexture);
        glUniform1i(glGetUniformLocation(resources.program, "videoFrame"), 0);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    // HUD 使用独立纹理，按控制器节拍更新；视频暂停时仍能刷新手柄状态。
    if (resources.hudWidth) {
        const int width = 800, height = int(width * float(resources.hudHeight) / resources.hudWidth);
        glViewport(resources.screenWidth - width - 24, resources.screenHeight - height - 24, width, height);
        glUseProgram(resources.program); glBindVertexArray(resources.vertexArray);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, resources.hudTexture);
        glUniform1i(glGetUniformLocation(resources.program, "videoFrame"), 0);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0); glFlush();
}

static void runSession(Renderer& renderer, XrSessionResources& resources) {
    XrPosef screenPose{}; screenPose.orientation.w = 1; screenPose.position.z = -2.5f;
    uint64_t renderedFrames = 0;
    while (!renderer.stop) {
        XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
        while (xrPollEvent(resources.instance, &event) == XR_SUCCESS) {
            if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                auto* changed = reinterpret_cast<XrEventDataSessionStateChanged*>(&event);
                INFO("Session state: %d", changed->state);
                resources.focused = changed->state == XR_SESSION_STATE_FOCUSED;
                if (changed->state == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
                    begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    check(xrBeginSession(resources.session, &begin), "xrBeginSession"); resources.running = true;
                } else if (changed->state == XR_SESSION_STATE_STOPPING) {
                    if (resources.running) xrEndSession(resources.session);
                    resources.running = false;
                } else if (changed->state == XR_SESSION_STATE_EXITING || changed->state == XR_SESSION_STATE_LOSS_PENDING) {
                    renderer.stop = true;
                }
            } else if (event.type == XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING) renderer.stop = true;
            event = {XR_TYPE_EVENT_DATA_BUFFER};
        }
        if (renderer.stop) break;
        if (!resources.running) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); continue; }
        XrFrameWaitInfo wait{XR_TYPE_FRAME_WAIT_INFO}; XrFrameState frame{XR_TYPE_FRAME_STATE};
        check(xrWaitFrame(resources.session, &wait, &frame), "xrWaitFrame");
        XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO}; check(xrBeginFrame(resources.session, &begin), "xrBeginFrame");
        pollInput(resources, frame.predictedDisplayTime);
        XrCompositionLayerQuad layer{XR_TYPE_COMPOSITION_LAYER_QUAD};
        const XrCompositionLayerBaseHeader* layers[1] = {reinterpret_cast<XrCompositionLayerBaseHeader*>(&layer)};
        if (frame.shouldRender) {
            uploadLatestFrame(renderer, resources);
            uint32_t imageIndex; XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
            check(xrAcquireSwapchainImage(resources.swapchain, &acquire, &imageIndex), "acquire image");
            XrSwapchainImageWaitInfo imageWait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO}; imageWait.timeout = XR_INFINITE_DURATION;
            check(xrWaitSwapchainImage(resources.swapchain, &imageWait), "wait image");
            drawVideo(resources, imageIndex);
            XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
            check(xrReleaseSwapchainImage(resources.swapchain, &release), "release image");
            // VIEW 空间随头部移动/旋转；固定局部位姿即可保持视频在视野正前方。
            // LOCAL 空间仍用于控制器位姿，不影响原有遥测坐标。
            layer.space = resources.viewSpace; layer.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
            layer.subImage.swapchain = resources.swapchain;
            layer.subImage.imageRect.extent = {int32_t(resources.screenWidth), int32_t(resources.screenHeight)};
            layer.pose = screenPose; layer.size = {4.0f, 2.25f};
            if (++renderedFrames % 300 == 1) INFO("Submitted VIEW-locked quad, frame %llu", (unsigned long long)renderedFrames);
        }
        XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO}; end.displayTime = frame.predictedDisplayTime;
        end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        end.layerCount = frame.shouldRender ? 1 : 0; end.layers = layers;
        check(xrEndFrame(resources.session, &end), "xrEndFrame");
    }
}

extern "C" JNIEXPORT jlong JNICALL Java_com_iwin_application_XrActivity_nativeCreate(JNIEnv*, jclass) {
    return reinterpret_cast<jlong>(new Renderer());
}
extern "C" JNIEXPORT void JNICALL Java_com_iwin_application_XrActivity_nativeStart(JNIEnv*, jclass, jlong handle) {
    auto* renderer = reinterpret_cast<Renderer*>(handle); renderer->stop = false;
    std::lock_guard<std::mutex> lock(renderer->frameMutex); renderer->framePending = !renderer->pendingPixels.empty();
    renderer->hudPending = !renderer->pendingHudPixels.empty();
}
extern "C" JNIEXPORT jstring JNICALL Java_com_iwin_application_XrActivity_nativeRun(JNIEnv* environment, jobject activity, jlong handle) {
    try {
        XrSessionResources resources;
        initializeOpenXr(environment, activity, resources);
        initializeInput(resources, environment, activity);
        runSession(*reinterpret_cast<Renderer*>(handle), resources);
        return nullptr;
    } catch (const std::exception& failure) {
        INFO("OpenXR error: %s", failure.what()); return environment->NewStringUTF(failure.what());
    }
}
extern "C" JNIEXPORT void JNICALL Java_com_iwin_application_XrActivity_nativeStop(JNIEnv*, jclass, jlong handle) {
    if (handle) reinterpret_cast<Renderer*>(handle)->stop = true;
}
extern "C" JNIEXPORT void JNICALL Java_com_iwin_application_XrActivity_nativeDestroy(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<Renderer*>(handle);
}
static void submitBitmap(JNIEnv* environment, jlong handle, jobject bitmap, bool hud) {
    if (!handle) return;
    AndroidBitmapInfo info{}; void* pixels = nullptr;
    if (AndroidBitmap_getInfo(environment, bitmap, &info) != ANDROID_BITMAP_RESULT_SUCCESS
        || info.format != ANDROID_BITMAP_FORMAT_RGBA_8888 || info.width == 0 || info.height == 0) return;
    if (AndroidBitmap_lockPixels(environment, bitmap, &pixels) != ANDROID_BITMAP_RESULT_SUCCESS) return;
    auto* renderer = reinterpret_cast<Renderer*>(handle);
    {
        // 单槽最新帧：覆盖上一帧，不为网络到达速度建立历史播放队列。
        std::lock_guard<std::mutex> lock(renderer->frameMutex);
        auto& destination = hud ? renderer->pendingHudPixels : renderer->pendingPixels;
        destination.resize(size_t(info.width) * info.height * 4);
        for (uint32_t row = 0; row < info.height; ++row)
            std::memcpy(destination.data() + size_t(row) * info.width * 4,
                static_cast<unsigned char*>(pixels) + size_t(row) * info.stride, size_t(info.width) * 4);
        if (hud) {
            renderer->pendingHudWidth = info.width; renderer->pendingHudHeight = info.height; renderer->hudPending = true;
        } else {
            renderer->pendingWidth = info.width; renderer->pendingHeight = info.height; renderer->framePending = true;
        }
    }
    AndroidBitmap_unlockPixels(environment, bitmap);
}

extern "C" JNIEXPORT void JNICALL Java_com_iwin_application_XrActivity_nativeSubmitFrame(JNIEnv* environment, jclass, jlong handle, jobject bitmap) {
    submitBitmap(environment, handle, bitmap, false);
}
extern "C" JNIEXPORT void JNICALL Java_com_iwin_application_XrActivity_nativeSubmitHud(JNIEnv* environment, jclass, jlong handle, jobject bitmap) {
    submitBitmap(environment, handle, bitmap, true);
}
