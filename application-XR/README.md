> 本目录是独立 OpenXR 分支 **IWIN LAN XR**。当前入口、构建及使用步骤见 [README-XR.md](README-XR.md)；以下为复制时保留的基础工程说明。

# IWIN Android 局域网基础应用

第一阶段：PC 按实时速度读取 MP4，本地直接显示源画面，同时发布连续 MPEG-TS 实时流供 Android 播放；PC 方向键状态同步显示在 Android 右上角。Android 外接键盘也可直接输入。业务显示内容暂不实现。

## 目录

```text
application/
├── communication/          Android 通信库（:communication）
│   ├── pc/           Python PC 推流、预览及协议测试
│   └── src/          VideoStream / RealtimeVideoStream / HttpDirectionSource
├── input/          纯 Java 方向领域库（:input），不依赖 Android
│   ├── pc/           键盘捕捉窗口和线程安全方向状态
│   └── src/          Direction / DirectionSource / DirectionState 及测试
└── display/          Android 应用入口（:app），仅视频和方向调试界面
```

项目位于 `F:/lab/IWIN-FINS/pico/pico/application`，与 `F:/lab/IWIN-FINS/pico/pico/hello` 同级，后续以此目录为准。模块使用相对路径。

## PC 可视化控制台（推荐）

双击工程根目录的 **Start-PC.vbs**，即可打开“局域网视频控制台”，不需要输入命令。

1. 首次使用或升级：从右上角“更多 ⋯ → 安装 / 更新手机 App…”安装新版 APK；手机需授权 USB 调试及 USB 安装。
2. 在 PC 选择网卡地址与端口，点击“连接手机（ADB）”。PC 自动启动网络服务，通过 ADB 将地址、端口写入 IWIN LAN 并打开 App。
3. 等右侧显示“已握手 · 手机名称（在线）”。这代表手机已通过局域网完成协议握手；ADB 命令返回成功本身不代表网络已连接。
4. 点击“选择 MP4”并“开始推流”。PC 显示本地源画面，手机在视频就绪后自动播放；默认循环播放。
5. 在窗口按方向键或用鼠标按住方向按钮；切出窗口自动释放。点击“停止”关闭当前网络服务与视频，随后可改端口并重新连接。

支持先握手后选视频；也支持先推流再连接。无需 ADB 时，仍可在手机手动填写右侧地址并点击连接。端口是手机连接 PC 的目标端口，不修改手机系统网络配置。地址配置保存在手机 App 中；切到后台会断开，回来后可手动重连或从 PC 再次下发。

**安装无需手动复制 APK，但 APK 数据仍需传到手机。** `adb install -r` 会自动传输并安装本机 APK，窗口把这一过程合在一个操作内；没有本机 APK 或手机已有安装包时，不能凭空安装。安装本身可经 USB 完成，不依赖应用的局域网握手。相关命令依据：[Android ADB 官方说明](https://developer.android.com/tools/adb)。

控制台自动查找工程内的 FFmpeg，无需配置 ffplay。PC 从同一个 MP4 解码输入直接获得本地参考帧，以 640×360 / 30fps 无声显示；另一分支以 H.264/AAC 编码，通过连续 HTTP MPEG-TS 供手机播放音视频。PC 参考画面不经过网络。编码失败、端口被占用和安装失败会显示提示。端口或网卡地址可直接在窗口选择。

默认勾选“显示时间码”：时间码在两路分流之前叠加在同一源帧上，循环时连续递增。对照同一时刻两块屏幕，**PC 时间码 − 手机时间码 ≈ 手机相对 PC 的显示延迟**。例如 PC 显示 T 00:00:10.500，手机显示 T 00:00:07.800，差值约 2.7 秒。可以用另一台设备同时拍下两块屏幕再读数；取消勾选后恢复无时间码的画面。改变此选项需要停止后重新启动。

PC 也存在解码和界面绘制延迟；上述差值用于近似观察相对延迟，不是纯网络耗时或精密端到端测量。PC 窗口会缩放到 640×360，比较画质时也需考虑缩放影响。命令行备用入口的 ffplay 仍是 HLS 回放，只用于检查视频流，不作为源画面延迟基准。

启动器优先使用本机 `F:/py3.10/pythonw.exe`，其他电脑可通过 PATH 中的 pythonw 启动，也可用 Python 打开 `communication/pc/pc_control.pyw`。源码仍保留原有命令行入口作为备用。实时传输要求 Android 0.3.0（versionCode 3）及新版 PC 控制台同时更新；旧版 App 的握手将被拒绝。

## 1. 构建 Android APK

需要 JDK 17、Android SDK Platform 35、Android SDK Build-Tools 34.0.0，以及可下载 Google Maven / Maven Central 依赖的网络。Gradle 8.9 Wrapper 已包含在项目中，无需全局安装 Gradle。

推荐用 Android Studio 打开整个 `application`，按提示配置 SDK、同步 Gradle 后运行 `app`。也可以在 `application` 目录执行：

```powershell
$env:JAVA_HOME = 'C:\实际路径\jdk-17'
$env:ANDROID_HOME = 'C:\实际路径\Android\Sdk'
.\gradlew.bat :input:test :app:assembleDebug :app:lintDebug
```

可用项目根目录的 `local.properties` 配置 SDK（不提交此文件），例如 `sdk.dir=C:/Users/你的用户名/AppData/Local/Android/Sdk`。执行 `:app:assembleDebug` 后，APK 会复制到 `application/app-debug.apk`；Gradle 原始构建产物仍保存在 `display/build/outputs/apk/debug/app-debug.apk`。最低 Android 8.0 / API 26。

## 2. 旧 HLS 命令行入口（仅兼容诊断）

此入口仍使用秒级 HLS，不适用于实时观看，也不兼容新版 App 的实时视频地址。日常使用请运行 Start-PC.vbs。

需要 Python 3.10+（包含 tkinter）和包含 `ffmpeg`、`ffplay`、`libx264` 编码器的完整 FFmpeg。将其 bin 目录加入 PATH，或通过参数提供路径。PC 服务没有第三方 Python 包依赖。

在 `application` 目录运行：

```powershell
python '.\communication\pc\lan_server.py' 'D:\videos\demo.mp4'
```

启动后，FFmpeg 循环、按原始时间节奏转码视频；约生成首个分片后，自动打开 ffplay 播放同一 HTTP HLS 流。另有“PC 方向输入”窗口，点击该窗口后按上下左右键；支持组合键，松开和切换窗口时释放。

**方向键仅在方向输入窗口获得焦点时捕捉**，不监听整个系统。点击 ffplay 后方向窗口会失焦，需要再点击方向窗口继续输入。安卓外接键盘方向键同样可用，编辑服务器地址时保留光标操作。

```powershell
# 指定可执行文件、端口；禁用预览或只播放一次
python '.\communication\pc\lan_server.py' 'D:\videos\demo.mp4' --ffmpeg 'D:\ffmpeg\bin\ffmpeg.exe' --ffplay 'D:\ffmpeg\bin\ffplay.exe' --port 8080
python '.\communication\pc\lan_server.py' 'D:\videos\demo.mp4' --no-preview
python '.\communication\pc\lan_server.py' 'D:\videos\demo.mp4' --once
```

关闭方向窗口或 Ctrl+C 会关闭服务与子进程，并清理该次运行的临时视频分片。`--headless` 用于无人值守推流，不提供键盘捕捉和 PC 预览。

## 3. Android 连接

1. PC 和手机接入同一路由器 / 局域网，关闭 Wi-Fi 客户端隔离。
2. PC 用 `ipconfig` 查询正在使用的网卡 IPv4，例如 `192.168.1.20`。
3. Windows 防火墙如拦截，允许 Python 在专用网络接收入站连接，端口默认 TCP 8080。
4. 手机应用中输入 `http://192.168.1.20:8080`，点击“连接 / 重新连接”。不要填写 localhost 或 0.0.0.0。
5. 视频开始后，点击 PC 方向窗口并按键，观察 Android 右上角。断开网络时远端方向会清空；恢复网络后输入自动恢复。视频出错则检查 PC 服务后点击重新连接。

应用切后台时释放视频、网络轮询和按键状态；返回后点击连接恢复。地址会自动保存。旋转屏幕后同样需要重新连接。Android 模拟器访问宿主 PC 可使用 `http://10.0.2.2:8080`。

## 延迟与范围

- 0.3.0 默认使用连续 HTTP MPEG-TS，移除 HLS 分片和 2.5 秒目标直播偏移。编码不使用 B 帧，30fps 时关键帧间隔约 333ms；播放器启动缓冲 50ms、重缓冲 100ms、最大目标缓冲 300ms。目标是亚秒显示延迟，但这些参数不是端到端实测结果或上限保证。
- 慢客户端队列超过 250ms 或队列溢出时断开；手机自动连接当前直播点，避免不断播放旧帧。网络、编码和设备解码仍会影响实际延迟。
- PC 图形控制台显示本地源画面，手机显示经过实时编码、传输和缓冲的画面；两路共用输入时间轴，可通过共同时间码观察相对延迟。方向数据即时传递，不与视频帧时间绑定。
- 输入每次 HTTP 响应后间隔 100ms 再轮询。很短、发生在两次轮询之间的点击可能无法看到；未来若需逐事件无损输入，应替换为带序号的事件流。
- 默认循环 MP4；无音轨的视频也支持；音频转 AAC，视频转 H.264 / yuv420p，以兼容 Android 解码。
- 目前为可信局域网原型，HTTP 明文且无认证，默认监听全部 IPv4 网卡；不要暴露公网。可用 `--host` 指定局域网网卡地址。正式版本应增加认证、TLS 和明确的网络安全配置。

## 可插拔扩展

Android 视频接口 `VideoStream` 只提供 view/play/close；未来实现 WebRTC、RTSP 或硬件解码适配器，再替换 `MainActivity.createVideo()`。

输入以 `Direction` 枚举和完整 `Set<Direction>` 状态快照表示。实现 `DirectionSource` 可接手柄、蓝牙、串口或 WebSocket，再替换 `MainActivity.createInput()`。`DirectionState` 按来源合并本地和远端按键，清理一个来源不会覆盖另一个来源。

PC 端 `VideoPublisher` 是发布器协议，`RealtimePublisher` 封装实时 FFmpeg 子进程，`LiveStreamHub` 管理观众订阅；`HlsPublisher` 保留作旧协议诊断；输入窗口只修改线程安全的 `DirectionState`，HTTP 不依赖 tkinter。PC 键盘适配器实际位于 `input/pc/keyboard_input.py`，由通讯入口组合。详细协议见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 测试

```powershell
# 在 application 目录；Python 协议/输入测试不需要 FFmpeg
python -m unittest discover -s '.\communication\pc' -p 'test_*.py' -v
# 需要 FFmpeg，验证真实有声/无声 MP4 循环、HTTP HLS 和解码
python '.\communication\pc\smoke_test.py' --ffmpeg 'D:\ffmpeg\bin\ffmpeg.exe'
# 需要 JDK 17，验证纯 Java 方向状态合并
.\gradlew.bat :input:test
```

端到端验收：双端视频均持续播放且循环；上下左右及组合键正确；失焦释放；拔掉网络后方向释放；输入自动恢复；视频手动重连；切后台后不继续下载；关闭 PC 窗口后无残留 FFmpeg/ffplay。APK 构建与 Android lint 已通过；0.3.0 连续流已通过本机 HTTP 拉取与真实解码检查；新版手机端显示延迟尚需实测，不能以本机首字节时间代替。

技术依据：[Media3 支持格式](https://developer.android.com/media/media3/exoplayer/supported-formats)、[Media3 缓冲配置](https://developer.android.com/reference/androidx/media3/exoplayer/DefaultLoadControl.Builder)、[AGP 8.7 / Gradle 8.9 兼容性](https://developer.android.com/build/releases/agp-8-7-0-release-notes)、[FFmpeg HLS 文档](https://ffmpeg.org/ffmpeg-formats.html#hls)。

## 握手与 ADB 配置协议

Android 0.2.0 起支持 `com.iwin.application.CONFIGURE` 显式 Activity Intent。PC 使用 `adb shell am start -W -n com.iwin.application/.MainActivity -a com.iwin.application.CONFIGURE --es iwin_server_host PC地址 --ei iwin_server_port 端口 --ez iwin_connect true` 下发并保存配置，随后 App 自动握手。活动采用 singleTop，已有前台实例通过 onNewIntent 接收新配置。旧 APK 需要通过“更多”先更新。

0.3.0 使用协议版本 2。App 向 `/handshake` POST 协议版本、客户端 ID、设备名和随机 nonce。PC 返回相同 nonce、会话 ID 及 `video_transport=mpegts-http`；后续 `/input` 请求携带 `X-IWIN-Session`，同时维持心跳。超过 5 秒无心跳后 PC 显示连接中断；App 出错后释放方向状态，重新握手。握手用于协议兼容性和在线检测，当前原型不提供身份认证或加密。

## 局域网连接失败时

PC “连接手机”会先下发配置，再从手机侧检查目标 TCP 端口。若提示配置已写入但手机无法访问 PC，需检查两端地址、网络隔离和防火墙。相同 IP 网段不代表可互访；校园 / 公共 Wi-Fi 可能隔离无线终端。可让手机开启热点并将 PC 接入，或换到允许终端互访的路由器，再在 PC 选择新地址并重新连接。

现场曾出现手机能访问网关、但无法解析电脑邻居地址的情况。临时 USB 转发能握手只用于区分软件问题与无线网络问题，不等同于局域网连接成功，也不能用其测量 Wi-Fi 延迟。

## PC 预览帧率

0.2.2 将 GUI 分支改为解码后使用 `realtime` 滤镜按帧 PTS 节拍输出；不再用输入读包限速代替显示节拍。手机编码分支和 PC 原始帧分支仍共享输入和时间码。窗口使用独立的预览刷新，复用 Tk 图像对象，预览下方显示实际图像更新 FPS。

短视频循环回到开头本身会有画面跳变；例如 2.53 秒的视频约每 2.53 秒重播一次。观察连续流畅度时可以选用较长、运动连续的视频。显示 FPS 是软件图像更新次数，不是外部测量的显示器刷新率。
