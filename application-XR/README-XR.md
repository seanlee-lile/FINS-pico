# IWIN LAN XR（原生 OpenXR）

本目录由最新 application 源码复制，未复制 .tools、.gradle、build 和 APK。原 application 与 Unity hello 均未修改。

## 当前行为

- 应用包名 com.iwin.application.xr，显示名称 IWIN LAN XR；可和原版同时安装。
- OpenXR + OpenGL ES 3 原生渲染，不依赖 Unity 引擎。视频位于约 2.5 米前、4 × 2.25 米的空间屏幕，背景黑色，双眼观看同一普通视频，保留原始宽高比。
- 屏幕固定在 VIEW 空间：视频和右上角状态栏跟随头部的位置与朝向，始终保持在视野正前方；不依赖启动时朝向，也不需要右摇杆回正。控制器遥测仍使用 LOCAL 空间。
- 这是普通视频的沉浸式影院模式，不把单目 MP4 自动变成双目 3D / 180° / 360° 视频。
- 继续使用更新后的 PC 逐帧 JPEG 传输与握手协议。只覆盖最新视频帧，不建立历史帧队列。延迟仍需实机测量；该协议目前不承载音频。
- 空间屏幕右上角实时显示左右手状态栏：A/B/X/Y、L3/R3、左手菜单、摇杆 XY、扳机和握持值。绿色表示按下，-- 表示当前输入不可用；PICO/Home 明确标记系统保留。
- HUD 使用独立纹理，只保留最新状态，最多约 30Hz 更新；不依赖视频、PC 握手或 UDP。失去焦点时清空状态，恢复后读取当前输入。
- OpenXR Actions 采集摇杆、扳机、握持键、A/B/X/Y、摇杆点击、左手菜单、右手位姿及速度，沿用 UDP 54322 快照。模拟扳机/握持的数字按下状态由 ≥0.75 阈值派生，连续值始终显示。Android KeyEvent 适配器仍保留。
- 摘下/暂停后释放 XR 会话及网络连接，恢复后按保存地址自动重连。

## 使用

1. 构建并安装本目录 app-debug.apk。
2. 运行本目录 Start-PC.vbs（和原版控制台不要同时占用 8080 / 54322）。
3. 选择电脑当前 WLAN 地址，点击连接手机（ADB），它会配置 XR 包的 XrActivity。
4. 戴上头显，选择 MP4 并开始推流。地址配置依赖 PC / ADB，沉浸空间内暂不提供文字键盘与设置面板。
5. 也能沿用原 application 的 PC 实时服务，但原控制台 ADB 按钮只配置原 App；XR 包需按下列命令单独配置。

```powershell
adb shell am start -W -n com.iwin.application.xr/com.iwin.application.XrActivity -a com.iwin.application.CONFIGURE --es iwin_server_host PC局域网IP --ei iwin_server_port 8080 --ez iwin_connect true
```

## 构建环境

JDK 17、Gradle 8.9、AGP 8.7.3、SDK 35、NDK 27.2.12479018、CMake 3.22.1、arm64-v8a；最低 Android 10 / API 29。使用 Maven Central 的 org.khronos.openxr:openxr_loader_for_android:1.1.49（官方 loader AAR，含 headers / prefab）。设备必须提供 OpenXR Runtime。

本机复用相邻 application/.tools 工具链及 Gradle 缓存，不重复复制 1GB 工具目录。local.properties 配置 sdk.dir、cmake.dir、可选 iwin.ndkPath；所有机器专属路径均不提交。换电脑时按实际安装位置重建 local.properties；有标准 SDK side-by-side NDK 时无需配置 iwin.ndkPath。

```powershell
$env:JAVA_HOME = 'F:\unityhub\6000.5.9f1\Editor\Data\PlaybackEngines\AndroidPlayer\OpenJDK'
$env:ANDROID_HOME = 'F:\lab\IWIN-FINS\pico\pico\application\.tools\android-sdk'
$env:ANDROID_USER_HOME = 'F:\lab\IWIN-FINS\pico\pico\application\.tools\android-user'
$env:GRADLE_USER_HOME = 'F:\lab\IWIN-FINS\pico\pico\application\.tools\gradle-home'
.\gradlew.bat :app:assembleDebug :app:lintDebug --console=plain --no-daemon
```

## 代码入口

- display/src/XrActivity.java：继承现有握手/输入业务，替换视频输出；管理 XR 生命周期。
- display/native/xr_renderer.cpp：OpenXR / EGL 会话、空间大屏、GLES 最新帧与独立 HUD、原生控制器输入。
- display/src/ControllerStatusHud.java：复用 Bitmap/Canvas 绘制角落状态栏，区分松开/按下/输入不可用。
- communication/src/RealtimeVideoStream.java：在原 JPEG 解码器上增加 Consumer<Bitmap> 输出适配点。
- display/src/MainActivity.java：保留原有 UI / 业务逻辑供继承复用；不再作为 XR Launcher。
- communication/pc/adb_bridge.py：仅配置 XR 包；PC 视频端点继续兼容原协议。

参考：[Khronos Android Manifest](https://github.com/KhronosGroup/OpenXR-SDK-Source/blob/main/src/tests/hello_xr/AndroidManifest.xml)、[官方 OpenXR Android 构建示例](https://github.com/KhronosGroup/OpenXR-Tutorials/blob/main/Chapter2/app/build.gradle)。
