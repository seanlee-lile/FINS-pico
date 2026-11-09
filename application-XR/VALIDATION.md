# 验证记录

日期：2026-11-06。

## 已通过

- PC 图形控制台：真实 MP4 在 Tk 窗口渲染预览帧；HTTP 视频/方向接口可用；连续两轮启动、停止、清理和重启通过。单次播放也验证了尾部缓冲与自动停止。既有 6 项 Python 测试仍通过；未新增或修改测试文件。

- Android 构建：`gradlew.bat :app:assembleDebug :app:lintDebug --console=plain --no-daemon` 成功，83 项任务执行完成；lint 无错误。
- APK：`display/build/outputs/apk/debug/app-debug.apk`，4,786,150 字节；`apksigner verify --verbose` 校验通过（v2 签名）。

- Python 标准库 unittest：6 项。覆盖方向组合、重复按下、释放、状态快照隔离、FFmpeg 实时/循环参数、HTTP 输入、健康检查、视频 MIME 类型、禁止目录/任意文件/临时分片访问。
- 真实 FFmpeg 冒烟测试：有声音轨及无声音轨两种 MP4。生成 3 秒测试片源，按实时速度循环发布，确认播放列表跨越源视频循环后继续推进；从 HTTP 获取非空 TS 分片，再从同一 HTTP HLS 地址实际拉流解码两秒，均成功退出。
- Gradle 8.9 官方 Wrapper JAR SHA-256 校验通过；发行包 SHA-256 已固定在 wrapper 配置中。

FFmpeg 测试使用 `imageio-ffmpeg==0.6.0` 携带的 Windows FFmpeg 二进制，放在被忽略的 `.tools/` 中，仅用于当前工作区验证；正式 PC 运行推荐安装同时包含 ffplay 的完整 FFmpeg。测试视频和 HLS 分片位于临时目录，测试结束自动清理。

## 尚未验证

- Java 方向单元测试尚未执行。本次复用 Unity 自带 JDK 17，并在工程 `.tools/android-sdk` 补齐 API 35 / Build-Tools 34.0.0，已解除此前缺少构建环境的限制。
- Android 实机 / 模拟器播放、双端播放延迟及 PC ffplay 图形预览：已通过 ADB 确认 Android 15 手机在线；尚未安装 APK 或验证播放，当前也未找到 ffplay。
- tkinter 实际物理键盘和 Android 外接键盘交互：代码已实现，仍需按 README 的验收步骤人工联调。状态领域测试不能替代物理键盘验证。

构建及联调命令见 README；`smoke_test.py` 可在具有 FFmpeg 的环境重复验证真实视频链路。

## PC 图形控制台使用范围

双击 `Start-PC.vbs` 启动。内置预览直接接收同一 MP4 输入的源帧，以 640×360 / 30fps 无声显示，无需 ffplay。手机音视频链路保持现有协议。安装手机 App 按钮已实现，尚未实际点击安装；物理键盘和 Wi-Fi 实机播放仍待用户联调。

## 源画面与传输画面对照验证

已将图形控制台从 HLS 回放改为同一 FFmpeg MP4 输入的本地原始帧分支，手机仍读取 HLS。共同时间码在 split 之前绘制，两路共享连续媒体时间；可在界面关闭时间码。一次真实有声 MP4 运行中，本地首次显示约 0.172 秒，HLS 播放列表约 1.125 秒后就绪，确认本地画面没有等待 HLS 分片或网络回放。带共同时间码的视频流经 HTTP 实际拉取解码成功；原有 6 项回归检查通过。

这些时间只记录本次启动检查，不是手机端延迟测量。手机与 PC 的实际显示差值仍需并排观察共同时间码。

## 0.2.0 握手及 ADB 配置

- 新 APK versionCode=2、versionName=0.2.0，大小 4,789,398 字节。assembleDebug、lintDebug 通过，v2 签名校验通过。
- PC 网络服务可先于视频启动。已实际请求握手，校验 nonce 回传、会话 ID、错误协议拒绝、无效会话拒绝、5 秒心跳到期及重新握手；在同一服务上开始源画面与 HLS 视频成功。既有 6 项 Python 回归检查通过；没有新增或修改测试文件。
- 主界面提供“连接手机（ADB）”，安装迁到“更多”。ADB 配置只接受经过校验的 host 和 port；旧 APK 会提示先更新。网络状态以实际握手和心跳为准，而不是 ADB 进程返回值。
- 已尝试向授权在线手机更新本项目 APK，但设备返回 INSTALL_FAILED_ABORTED: User rejected permissions。因此本次未完成新版安装及实机 ADB 下发端口联调；需用户在手机允许 USB 安装 / 安装确认后继续。未尝试绕过拒绝或卸载旧 App。

## 现场连接诊断与 PC 0.2.1 修复

- 已确认 vivo V2352A 安装 Android App 0.2.0，私有配置为 http://10.181.183.234:8080，App 位于前台，PC 8080 服务可从本机及自身 WLAN 地址访问。
- 手机 WLAN 地址 10.181.211.85，能 ping 通网关 10.180.0.1；访问 PC 10.181.183.234 报 Destination Host Unreachable，对应邻居项 INCOMPLETE，未完成对电脑的邻居解析。故当前 Wi-Fi 的终端互访路径不可用；客户端隔离 / 网络策略是可能原因，尚未确认具体网络设备配置。
- 临时设置 adb reverse 18081 并下发 127.0.0.1:18081，实机成功握手，设备名 vivo V2352A，超过 5 秒仍有持续心跳。验证后已恢复原 WLAN 地址并移除临时 USB 转发。该结果不代表 Wi-Fi 视频延迟。
- 修复 ttk Combobox 原生 popdown 无 Python Widget 映射导致的 focus_displayof KeyError，改为直接读取 Tcl 焦点路径；PC 配置下发后新增手机侧 TCP 连接探测，区分“ADB 已配置”和“局域网已通”。原有 6 项回归检查通过。

## 热点实机连接与 PC 预览性能修复

- 切换手机热点后，PC 地址 192.168.131.58，手机热点地址 192.168.131.111。已通过 ADB 下发新地址，手机侧 TCP 8080 探测成功，手机直接读取 /input 得到 HTTP 200；App 进程日志显示 H.264 与 AAC 解码器已启动，证明经应用握手后进入视频解码。没有使用 USB 视频转发。
- 当前源视频为旋转标记的 1920×1080 H.264 / AAC、约 30fps、时长 2.53 秒。旧共享管道平均约 30fps 但帧成批到达，最大间隔约 1.469 秒；旧 Tk 窗口图像更新仅约 3.5fps。
- 对比了输出 flush、解码线程、滤镜线程等设置。采用解码后 realtime PTS 节拍控制、取消 GUI 输入 -re 并开启 PPM flush 后，源输出约 30.02fps，帧间隔中位数约 31ms、最大约 47ms，8 秒样本中无超过 150ms 的间隔。
- 再将界面状态刷新与预览分开并复用图像，同一视频的窗口图像更新复测约 27.18fps。软件计数测得的图像更新率不等于显示器光学刷新率；循环片源回到开头仍会有内容跳变。
- 原有 6 项 Python 回归检查通过；窗口可正常关闭，无残留的 renderPreview 定时回调报错。Android 源码和 APK 未因本次 PC 性能优化更改。

## 0.3.0 连续实时流（取代 GUI HLS）

- Android versionCode=3 / versionName=0.3.0，APK 4,798,381 字节；assembleDebug 与 lintDebug 成功。
- GUI 改为连续 HTTP MPEG-TS；协议 v2 要求传输类型 mpegts-http。旧 HLS 代码仅保留兼容诊断，不再作为 GUI 默认发布器。
- 使用当前真实 MP4，通过本机 HTTP 取到 18,800 字节约 31ms；FFmpeg 从实际 HTTP 地址解码 1 秒成功，同时 PC 源预览正常更新，没有生成 HLS 分片。31ms 是本机读取首批数据耗时，不能作为手机显示延迟。
- 原有 6 项 Python 检查通过，未新增或修改测试文件。关闭和资源清理通过。
- 新版尚未安装到手机；ADB 服务重启后曾枚举 10AE4L08QN000SL 为 offline，重连后仍待恢复。0.3.0 的手机播放与端到端延迟尚未验证。旧版 3 秒延迟不能作为新版结果。

### 0.3.0 手机现场更新成功

- USB 恢复为 device 后，向 vivo V2352A 成功执行 adb install -r，返回 Success。
- PC 控制台已从旧 HLS 进程切换至新版，保留当前 MP4、循环、共同时间码及 8080 端口。
- ADB 下发 http://192.168.131.58:8080，手机 TCP 探测成功；/health 返回 video_ready=true、connected_clients=1。
- 实机截图确认“实时视频播放中”“握手成功 · 方向输入已连接”，显示实际视频和 T 时间码。视频经热点 LAN 传输，未使用 USB 转发。
- 本次没有同步拍摄 PC 和手机，尚不能给出新版相对显示延迟数值；ADB 截屏耗时不能作为视频延迟。
