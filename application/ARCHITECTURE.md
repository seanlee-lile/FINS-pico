# 模块边界与协议 v2

## 依赖方向

```text
显示模块 (:app) ───────→ 通讯模块 (:communication) ───────→ 输入模块 (:input)
       └───────────────────────────────────────────────→ 输入模块 (:input)

PC MP4 → RealtimePublisher / FFmpeg → LiveStreamHub → HTTP MPEG-TS → RealtimeVideoStream → VideoStream.view
PC tkinter → DirectionState → HTTP /input → HttpDirectionSource → DirectionState → 方向小窗
Android KeyEvent ────────────────────────────────────────────→ DirectionState
```

输入模块为纯 Java 领域层，不引用 Android、HTTP 或播放器。通讯模块实现外部协议适配器。显示模块作为应用组合入口，持有会话、主线程调度和生命周期，暂不引入业务显示组件。为满足仅三个一级子目录，Android 应用入口暂放显示模块；以后可将入口抽至独立 app 工程。

## HTTP 端点

| 方法 / 路径 | 响应 | 语义 |
| --- | --- | --- |
| POST /handshake | application/json | 协议 v2、nonce、会话及传输类型确认 |
| GET /live.ts | video/mp2t | 连续直播，订阅当前编码输出；未就绪返回 503 |
| GET /hls/live.m3u8 | application/vnd.apple.mpegurl | 旧命令行 HLS 播放列表；GUI 不生成，返回 404 |
| GET /hls/segment_000000001.ts | video/mp2t | 已完成分片；滚动淘汰后返回 404 |
| GET /input | application/json | 当前完整按键快照，非输入事件队列 |
| GET /health | application/json | 实时编码正在产生数据时 200，否则 503；旧入口按播放列表判断 |

所有成功或错误响应携带 `Cache-Control: no-store`。不提供目录索引、上传、任意文件读取或视频控制接口。GUI 的 `/health` 要求实时发布器已连接且最近 1 秒内有编码数据，不表示客户端已实际解码成功。旧入口仍按播放列表存在性判断。

输入示例：

```json
{"directions":["LEFT","UP"],"sequence":17}
```

`directions` 取值为 UP / DOWN / LEFT / RIGHT；空数组表示释放全部方向，允许相反方向同时按下，业务层将来决定如何解释。`sequence` 仅供诊断，每次状态变化递增，进程重启后清零。Android 当前不依赖该字段。

## 并发、释放和错误

- PC GUI 主线程处理按键；HTTP 工作线程用锁读取快照。失焦清空状态，重复按键不增加序号。
- HTTP 输入源使用单个调度线程，1 秒连接超时和 1 秒读取超时；每次轮询完成 100ms 后再次运行，不产生并行轮询积压。失败发布空集合，持续自动恢复。
- Android 网络输入回调通过 UI 线程进入状态合并器。连接代数使旧会话排队回调失效；关闭时中断请求和调度器。
- 视频接口所有操作在主线程完成。Activity onStop 时释放播放器和输入源。实时视频错误以状态文本呈现，200ms 后自动重连当前直播点。
- PC 每次运行使用独立临时日志目录；GUI 不生成视频分片。退出先停止编码和预览，再关闭实时转发器及 HTTP，最后清理临时目录。旧 HLS 入口仍滚动清理分片。

## 后续接口演进

当前实时适配器保留 `VideoStream` 组合入口，后续可替换为 WebRTC / RTSP；增加视频时间戳和输入时间戳后才能讨论帧级同步。若需要逐次输入事件不丢失，单独增加事件协议与重放游标，不能把快照轮询当作事件队列。协议破坏性变更通过握手版本拒绝不兼容客户端；当前 v2 要求 `video_transport=mpegts-http`。

## PC 图形控制台

`communication/pc/pc_control.pyw` 组合连续实时发布器、转发器和 HTTP 服务，`input/pc/direction_controls.py` 提供可嵌入的键盘/鼠标方向面板。启动器位于根目录 `Start-PC.vbs`。Tk 主线程负责界面，视频帧解码、安装 APK 和资源清理在后台运行；预览队列只保留最新帧。临时日志位于 `.tools/temp/pc-session-*`，停止时校验目录边界后清理。

## 源画面延迟对照

图形控制台使用一个 MP4 输入，先以 `setpts` 建立连续媒体时间，再以 `realtime` 在解码后按帧 PTS 进行实时节拍控制，按选项叠加 PTS 时间码，再以 `split` 分为两路。手机分支执行 H.264/AAC 编码及连续 MPEG-TS 发布；PC 分支把源帧转换为 PPM，通过标准输出直接交给窗口，以 30fps 显示。本地分支不重新解码 HLS，不访问 HTTP，也不启动第二个计时独立的视频输入。共同进程由 RealtimePublisher 管理，停止时先退出进程，再结束原始帧读取线程。

时间码差描述两块屏幕可见画面的相对延迟，包含手机编码/传输/缓冲/显示与本地参考显示之间的差异，不代表纯网络耗时。PC 参考仍有图像缩放、线程交接和 Tk 刷新延迟。

## 握手、心跳与设备配置

`connection_registry.py` 持有协议 v2 会话，校验客户端 ID、nonce 和协议版本，nonce 原样回送；`/input` 会话头作为心跳，超时 5 秒移除。无会话头的旧输入读取端点仍保留兼容，但不会计为握手在线。Android 新输入适配器先完成握手、校验 nonce 和版本，再读取带会话头的输入；失败后重新握手。`video_ready` 控制播放器的惰性创建，允许网络服务先于视频启动。

`adb_bridge.py` 集中执行本机 ADB，检查单台授权设备及新版 APK，校验目标 host/port，再通过显式 CONFIGURE Intent 配置 App；ADB 成功仅表示指令已下发，连接状态以真实 HTTP 握手与心跳为准。主 Activity 使用 singleTop / onNewIntent 接收配置，存入私有 preferences 并重建连接会话。安装放在“更多”菜单，ADB 自动传输 APK 后安装。

配置 Intent 字段：`iwin_server_host`（字符串）、`iwin_server_port`（1–65535）、`iwin_connect`（布尔）。本原型入口只改 App 目标地址；不改 Android 系统网络设置。协议中的会话 token 只用于在线会话管理，不代表身份认证。

## 原始帧节拍与界面刷新

GUI 的 MP4 读取不使用 `-re`。输入层采用低延迟解码线程设置，解码后在 split 前用 realtime 限制视频帧节拍，PPM 输出开启 flush_packets。只限制输入包读取可能在解码/音视频调度后产生批量视频帧，而容量为 1 的预览队列会丢掉批次中的中间帧。

窗口状态每 200ms 更新；预览独立检查新帧，只有新帧才绘制，复用同一个 PhotoImage。关闭时先取消两个 Tk 定时回调，再销毁窗口。原有命令行入口没有源预览时仍使用 -re 限制 MP4 读取。

## 实时传输与背压

`RealtimePublisher` 复用共同时间轴图，将输出改为 MPEG-TS，经回环 TCP 交给 `LiveStreamHub`。H.264 使用 zerolatency、无 B 帧、GOP 10、关闭前瞻；TS 关闭 mux 延迟并及时刷新。HTTP `/live.ts` 每次只订阅未来数据，不回放历史缓存。

每位观众使用独立 32 项队列；溢出或取出数据已滞留超过 250ms 时结束连接。HTTP socket 发送超时 1 秒，避免慢连接长期占用线程。Android 使用显式 TS 提取器和较小缓冲，出错或断流后重连当前直播点。队列限制不是整个网络链路的延迟上限；TCP、播放器和设备仍有自己的缓冲，必须以共同时间码实测。
