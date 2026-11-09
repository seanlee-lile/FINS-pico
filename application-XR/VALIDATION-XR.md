# XR 验证记录（2026-11-09）

- 已从最新 application 复制 43 个源码/配置文件，排除工具链、构建缓存和 APK。所有后续源码修改仅在 application-XR。
- 构建：:app:assembleDebug :app:lintDebug 成功；最终一次 88 项任务，17 项执行、71 项 up-to-date。没有新增或修改测试文件。
- 独立包 com.iwin.application.xr，versionCode 4、versionName 0.4.0-xr，arm64-v8a。
- PICO A9210 / Android API 34，ADB 安装返回 Success。实际使用系统 com.pico.xr.openxr_runtime，运行时名称 Pico XRRuntime()。
- 日志显示 OpenXR Session 进入 READY / SYNCHRONIZED / VISIBLE / FOCUSED，持续 xrEndFrame 提交 Quad 层；300 帧约 3.33 秒，XR 提交节拍约 90Hz，不代表源视频为 90fps。
- PICO4、Neo3、Touch 控制器绑定建议均返回 XR_SUCCESS。PC /input 收到真实原生控制器位姿和速度，UDP 约 47.5Hz。
- PC 通过热点以外当前 WLAN 地址 192.168.8.39:8080 推送已有 MP4；/health 返回 video_ready=true、connected_clients=1；USB 仅用于配置和日志，没有 adb reverse 视频转发。
- 实机截图 .tools/temp/xr-device.png 确认左右眼均显示实际视频及共同时间码，黑色环境，没有二维浮窗边框。当前为竖屏素材，保持宽高比。
- 未测量光学端到端延迟；没有声称亚 50ms。手柄各物理按键和右摇杆重定位仍需逐项人工体验，代码已接入。
- 当前是普通视频空间大屏；尚未实现球幕/180°/360°、双目视频、空间设置键盘或画面内控制面板。

## 0.4.1-xr 手柄状态栏

- versionCode=5；assembleDebug 和 lintDebug 通过（88 项任务，24 执行、64 up-to-date）。未新增或修改测试文件。
- PICO A9210 安装返回 Success；原生输入 profile 绑定返回 XR_SUCCESS，Session 进入 FOCUSED 并持续提交画面。
- 实机截图 .tools/temp/xr-controller-hud.png 确认双眼右上角显示完整状态栏；无 PC 视频时仍更新。A/B/X/Y、L3/R3、左菜单和两侧模拟轴均显示有效状态。
- 系统 Home 不宣称能读取；不可用值显示 --，不作为松开处理。模拟键数字按下阈值为 0.75。
- 按下/释放的物理交互结果等待用户现场确认。

## 0.4.2-xr 视野固定

- video Quad 的参考空间由 LOCAL 改为 VIEW，局部位姿固定为 (0,0,-2.5)，朝向为单位四元数；视频与同层 HUD 跟随头部，保持在视野正前方。LOCAL 仍用于控制器遥测。
- 移除初始头部定位与右摇杆回正逻辑；R3 保留为普通手柄输入。HUD 提示和 README-XR 同步更新。
- assembleDebug / lintDebug 通过，88 项任务（24 执行、64 up-to-date）；ADB 安装 Success，versionCode=6 / versionName=0.4.2-xr。
- PICO 日志确认 FOCUSED，持续 Submitted VIEW-locked quad；截图 .tools/temp/xr-view-locked.png 用于检查正前方画面和右上角状态栏。未加入或修改测试文件。
