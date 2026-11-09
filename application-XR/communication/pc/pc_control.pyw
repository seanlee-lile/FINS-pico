"""IWIN PC 可视化控制台。双击工程根目录的 Start-PC.vbs 启动。

组合连续实时发布器和输入领域状态；Tk 主线程只处理界面。
一个 MP4 输入同时输出本地参考帧和手机连续 MPEG-TS；本地预览不读取网络流。
"""
from __future__ import annotations
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from lan_server import DirectionState, LanServer, handler_factory
from realtime_stream import LiveStreamHub, RealtimePublisher
from direction_controls import DirectionControls
from connection_registry import ConnectionRegistry
from adb_bridge import AdbBridge
from pico_receiver import PicoControllerReceiver

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_ROOT = PROJECT_ROOT.parent / "application"
BACKGROUND = "#0c1422"
PANEL = "#172235"
TEXT = "#e8eff8"
MUTED = "#a7b7cc"


def findEncoder():
    """优先复用工程内工具，再查 PATH；更换电脑后也能手动选择。"""
    candidates = sorted((TOOL_ROOT / '.tools/python/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
    candidates += [TOOL_ROOT / '.tools/ffmpeg/bin/ffmpeg.exe']
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which('ffmpeg') or ''


def localAddresses():
    virtual_ips = set()
    if os.name == 'nt':
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output('ipconfig /all', text=True, errors='ignore', startupinfo=startupinfo)
            sections = re.split(r'\r?\n(?=\S)', out)
            for sec in sections:
                header = sec.splitlines()[0] if sec.splitlines() else ''
                if any(k in header.lower() for k in ['vethernet', 'hyper-v', 'wsl', 'vmware', 'virtualbox']):
                    for ip in re.findall(r'(?:IPv4 Address|IPv4 地址)[ .]*:[ ]*([0-9.]+)', sec):
                        virtual_ips.add(ip)
        except Exception:
            pass
    else:
        # Linux 环境：过滤 docker、veth、br 等虚拟网桥接口
        try:
            out = subprocess.check_output(['ip', '-4', 'addr', 'show'], text=True, errors='ignore')
            for line in out.splitlines():
                if any(v in line for v in ['docker', 'veth', 'virbr', 'br-']):
                    for ip in re.findall(r'inet\s+([0-9.]+)', line):
                        virtual_ips.add(ip)
        except Exception:
            pass

    # 兜底过滤已知的本机虚拟交换机/容器常用私有网段（如 WSL2/Hyper-V 的 172.17~31.*）
    def is_virtual(ip_str):
        if ip_str in virtual_ips:
            return True
        parts = ip_str.split('.')
        if len(parts) == 4 and parts[0] == '172':
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
            except ValueError:
                pass
        return False

    addresses = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(('8.8.8.8', 80))  # 只查询路由选择，不发送数据。
            candidate = connection.getsockname()[0]
            if not is_virtual(candidate):
                addresses.append(candidate)
    except OSError:
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if not address.startswith(('127.', '169.254.')) and address not in addresses and not is_virtual(address):
                addresses.append(address)
    except OSError:
        pass
    return addresses or ['127.0.0.1']


class SourcePreview:
    """接收共享 MP4 解码器的原始帧；保留最新帧，避免参考窗口积压延迟。"""

    def __init__(self, sourceProcess):
        self.frames = queue.Queue(maxsize=1)
        self.frameCount = 0
        self.closed = False
        self.process = sourceProcess  # 生命周期由 RealtimePublisher 持有。
        self.thread = threading.Thread(target=self.readFrames, daemon=True)
        self.thread.start()

    def readFrames(self):
        stream = self.process.stdout
        try:
            while not self.closed:
                if stream.readline() != b'P6\n':
                    break
                dimensions = stream.readline()
                width, height = map(int, dimensions.split())
                maximum = stream.readline()
                if maximum.strip() != b'255' or not (0 < width <= 1920 and 0 < height <= 1080):
                    break
                remaining = width * height * 3
                chunks = []
                while remaining:
                    chunk = stream.read(remaining)
                    if not chunk:
                        return
                    chunks.append(chunk)
                    remaining -= len(chunk)
                frame = b'P6\n' + dimensions + maximum + b''.join(chunks)
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(frame)
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def close(self):
        self.closed = True
        # 调用方先停止共享解码器，使阻塞中的管道读取退出。
        self.thread.join(timeout=2)


class PcController:
    def __init__(self, root):
        self.root = root
        self.root.title('IWIN · XR 实时视频控制台 0.4')
        self.root.geometry('1100x820')
        self.root.minsize(1000, 760)
        self.root.configure(bg=BACKGROUND)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.videoPath = tk.StringVar()
        self.encoderPath = findEncoder()
        self.port = tk.StringVar(value='8080')
        self.loop = tk.BooleanVar(value=True)
        self.timecode = tk.BooleanVar(value=True)
        self.address = tk.StringVar()
        self.channelMode = tk.StringVar(value='mp4')  # 'mp4' (本地MP4) 或 'rov' (水下ROV直接转运)
        self.rovPort = tk.StringVar(value='5600')      # 依据远端下位机实际配置：5600 (V5.0/GStreamer RTP) 或 5004 (stream.sdp)
        self.status = tk.StringVar(value='待启动 · 请选择一个 MP4 视频')
        self.detail = tk.StringVar(value='选择视频后点击“开始推流”，再用手机连接右侧地址。')
        self.server = self.publisher = self.preview = self.serverThread = self.logOutput = None
        self.sessionDirectory = None
        self.stopping = False
        self.closePending = False
        self.closed = False
        self.installing = False
        self.configuring = False
        self.connections = None
        self.streamHub = None
        self.everConnected = False
        self.handshakeDeadline = None
        self.connectionStatus = tk.StringVar(value="手机：未握手")
        self.previewImage = None
        self.previewInfo = tk.StringVar(value='本地源画面 · 无声 / 30fps · 时间码差 ≈ 相对延迟')
        self.previewSampleAt = time.monotonic()
        self.previewSampleCount = 0
        self.events = queue.Queue()
        self.state = DirectionState()
        self.picoReceiver = PicoControllerReceiver(port=54322)
        self.picoReceiver.start()
        self.addresses = localAddresses()
        self.buildWindow()
        self.updateAddress()
        self.statusTimer = self.root.after(200, self.tick)
        self.previewTimer = self.root.after(0, self.renderPreview)

    def buildWindow(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TCheckbutton', background=BACKGROUND, foreground=TEXT)
        style.map('TCheckbutton', background=[('active', BACKGROUND)])
        header = tk.Frame(self.root, bg=BACKGROUND)
        header.pack(fill='x', padx=28, pady=(22, 18))
        moreButton = ttk.Menubutton(header, text='更多 ⋯')
        self.moreMenu = tk.Menu(moreButton, tearoff=False)
        self.moreMenu.add_command(label='安装 / 更新手机 App…', command=self.installApk)
        moreButton.config(menu=self.moreMenu)
        moreButton.place(relx=1, y=8, anchor='ne')
        tk.Label(header, text='IWIN  /  PC 控制台', bg=BACKGROUND, fg=TEXT,
                 font=('Microsoft YaHei UI', 23, 'bold')).pack(anchor='w')
        tk.Label(header, text='选择信道模式；手机与电脑连接同一局域网。',
                 bg=BACKGROUND, fg=MUTED, font=('Microsoft YaHei UI', 11)).pack(anchor='w', pady=(6, 0))

        # --- 信道切换栏（信道 1：本地 MP4 推流 / 信道 2：水下 ROV 实时转运） ---
        channelBar = tk.Frame(self.root, bg=BACKGROUND)
        channelBar.pack(fill='x', padx=28, pady=(0, 10))
        tk.Label(channelBar, text='输入信道：', bg=BACKGROUND, fg=TEXT,
                 font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left', padx=(0, 10))
        self.channelRadioMp4 = tk.Radiobutton(
            channelBar, text='信道 1 · 本地 MP4 视频推流', variable=self.channelMode, value='mp4',
            bg=BACKGROUND, fg=TEXT, activebackground=BACKGROUND, activeforeground='#70d9c4',
            selectcolor=PANEL, font=('Microsoft YaHei UI', 10), command=self.onChannelChange)
        self.channelRadioMp4.pack(side='left', padx=(0, 16))
        self.channelRadioRov = tk.Radiobutton(
            channelBar, text='信道 2 · 水下 ROV 实时转运 (UDP/RTP)', variable=self.channelMode, value='rov',
            bg=BACKGROUND, fg=TEXT, activebackground=BACKGROUND, activeforeground='#70d9c4',
            selectcolor=PANEL, font=('Microsoft YaHei UI', 10), command=self.onChannelChange)
        self.channelRadioRov.pack(side='left')

        # --- 信道 1：MP4 文件选择面板 ---
        self.mp4Panel = tk.Frame(self.root, bg=PANEL, padx=16, pady=14)
        self.mp4Panel.pack(fill='x', padx=28)
        tk.Label(self.mp4Panel, text='视频文件 (MP4)', bg=PANEL, fg=MUTED,
                 font=('Microsoft YaHei UI', 10)).pack(anchor='w')
        row = tk.Frame(self.mp4Panel, bg=PANEL)
        row.pack(fill='x', pady=(7, 0))
        self.pathEntry = ttk.Entry(row, textvariable=self.videoPath, state='readonly')
        self.pathEntry.pack(side='left', fill='x', expand=True, ipady=7)
        self.chooseButton = self.button(row, '选择 MP4', self.chooseVideo)
        self.chooseButton.pack(side='left', padx=(12, 0))

        # --- 信道 2：水下 ROV UDP 端口与协议面板 ---
        self.rovPanel = tk.Frame(self.root, bg=PANEL, padx=16, pady=14)
        # 初始默认显示 mp4Panel，隐藏 rovPanel
        tk.Label(self.rovPanel, text='水下 ROV 数据输入 (基于下位机 RTP/UDP 实际协议)', bg=PANEL, fg='#70d9c4',
                 font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w')
        rovRow = tk.Frame(self.rovPanel, bg=PANEL)
        rovRow.pack(fill='x', pady=(7, 0))
        tk.Label(rovRow, text='ROV 下位机发送端口：UDP', bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 10)).pack(side='left', padx=(0, 6))
        self.rovPortChoice = ttk.Combobox(
            rovRow, textvariable=self.rovPort, values=['5600', '5004'], width=8)
        self.rovPortChoice.pack(side='left', ipady=3)
        tk.Label(rovRow, text='(5600: V5.0/GStreamer H.264 RTP 默认 | 5004: stream.sdp)',
                 bg=PANEL, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(10, 0))
        controls = tk.Frame(self.root, bg=BACKGROUND)
        controls.pack(fill='x', padx=28, pady=16)
        self.startButton = self.button(controls, '开始推流', self.start, '#16866d')
        self.startButton.pack(side='left')
        self.stopButton = self.button(controls, '停止', self.stop)
        self.stopButton.config(state='disabled')
        self.stopButton.pack(side='left', padx=10)
        self.loopCheck = ttk.Checkbutton(controls, text='循环播放', variable=self.loop)
        self.loopCheck.pack(side='left', padx=10)
        self.timecodeCheck = ttk.Checkbutton(controls, text='显示时间码', variable=self.timecode)
        self.timecodeCheck.pack(side='left', padx=(0, 8))
        tk.Label(controls, text='端口', bg=BACKGROUND, fg=MUTED).pack(side='left', padx=(14, 6))
        self.portEntry = ttk.Entry(controls, textvariable=self.port, width=7)
        self.portEntry.pack(side='left', ipady=5)
        self.port.trace_add('write', lambda *_: self.updateAddress())
        self.phoneButton = self.button(controls, '连接手机（ADB）', self.configurePhone)
        self.phoneButton.pack(side='right')
        body = tk.Frame(self.root, bg=BACKGROUND)
        body.pack(fill='both', expand=True, padx=28)
        left = tk.Frame(body, bg=PANEL, padx=10, pady=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 16))
        tk.Label(left, text='PC 本地源画面 · 延迟对照', bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', padx=4, pady=(2, 10))
        self.previewLabel = tk.Label(left, text='尚未启动\n\n选择视频后即可查看本地源画面',
                                     bg='#080e18', fg=MUTED, font=('Microsoft YaHei UI', 13))
        self.previewLabel.pack(fill='both', expand=True)
        self.previewLabel.bind('<Button-1>', lambda _: self.root.focus_set())
        tk.Label(left, textvariable=self.previewInfo,
                 bg=PANEL, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(10, 0))
        right = tk.Frame(body, bg=BACKGROUND, width=300)
        right.pack(side='right', fill='y')
        connection = tk.Frame(right, bg=PANEL, padx=16, pady=14)
        connection.pack(fill='x', pady=(0, 12))
        tk.Label(connection, text='手机连接', bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        tk.Label(connection, text='点击“连接手机”自动下发地址与端口',
                 bg=PANEL, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(6, 10))
        self.addressChoice = ttk.Combobox(connection, values=self.addresses, state='readonly', width=28)
        self.addressChoice.set(self.addresses[0])
        self.addressChoice.pack(fill='x')
        self.addressChoice.bind('<<ComboboxSelected>>', self.selectAddress)
        tk.Label(connection, textvariable=self.address, bg=PANEL, fg='#70d9c4',
                 font=('Consolas', 12), wraplength=280).pack(anchor='w', pady=12)
        self.button(connection, '复制连接地址', self.copyAddress).pack(fill='x')
        tk.Label(connection, textvariable=self.connectionStatus, bg=PANEL, fg='#70d9c4',
                 font=('Microsoft YaHei UI', 10), wraplength=280, justify='left').pack(anchor='w', pady=(10, 0))
        self.directionControls = DirectionControls(right, self.root, self.state)
        self.directionControls.frame.pack(fill='x', pady=(0, 12))

        # --- PICO 4 Ultra 手柄遥测与每一个按键状态面板 ---
        picoPanel = tk.Frame(right, bg=PANEL, padx=14, pady=10)
        picoPanel.pack(fill='x')

        picoHeader = tk.Frame(picoPanel, bg=PANEL)
        picoHeader.pack(fill='x')
        tk.Label(picoHeader, text='PICO 手柄遥测', bg=PANEL, fg=TEXT,
                 font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        self.picoStatusLabel = tk.Label(picoHeader, text='未连接 (UDP/ADB)',
                                        bg=PANEL, fg=MUTED, font=('Microsoft YaHei UI', 9))
        self.picoStatusLabel.pack(side='right')

        # 模拟量显示行：双摇杆与扳机
        self.picoSticksLabel = tk.Label(picoPanel, text='左摇杆: (0.00, 0.00)  右: (0.00, 0.00)',
                                        bg=PANEL, fg=TEXT, font=('Consolas', 9), justify='left')
        self.picoSticksLabel.pack(anchor='w', pady=(4, 2))
        self.picoTriggersLabel = tk.Label(picoPanel, text='LT: 0% LG: 0% | RT: 0% RG: 0%',
                                          bg=PANEL, fg=MUTED, font=('Consolas', 8), justify='left')
        self.picoTriggersLabel.pack(anchor='w', pady=(0, 6))

        # 按键徽章矩阵网格（显示每一个手柄按键的状态）
        badgeGrid = tk.Frame(picoPanel, bg=PANEL)
        badgeGrid.pack(fill='x', pady=(2, 4))

        self.picoBadges = {}
        # 左手柄按键列 (Left Hand)
        leftCol = tk.Frame(badgeGrid, bg=PANEL)
        leftCol.pack(side='left', fill='x', expand=True, padx=(0, 4))
        tk.Label(leftCol, text='左手 (L)', bg=PANEL, fg='#70d9c4', font=('Microsoft YaHei UI', 8, 'bold')).pack(anchor='w')

        rowL1 = tk.Frame(leftCol, bg=PANEL)
        rowL1.pack(fill='x', pady=1)
        for name in ['X', 'Y', 'L3']:
            lbl = tk.Label(rowL1, text=name, bg='#1e293b', fg='#64748b', font=('Consolas', 9, 'bold'), width=4, relief='flat')
            lbl.pack(side='left', padx=1)
            self.picoBadges[name] = lbl

        rowL2 = tk.Frame(leftCol, bg=PANEL)
        rowL2.pack(fill='x', pady=1)
        for name in ['LT', 'LG', 'SELECT']:
            w = 5 if name == 'SELECT' else 4
            lbl = tk.Label(rowL2, text=name, bg='#1e293b', fg='#64748b', font=('Consolas', 8, 'bold'), width=w, relief='flat')
            lbl.pack(side='left', padx=1)
            self.picoBadges[name] = lbl

        # 右手柄按键列 (Right Hand)
        rightCol = tk.Frame(badgeGrid, bg=PANEL)
        rightCol.pack(side='right', fill='x', expand=True, padx=(4, 0))
        tk.Label(rightCol, text='右手 (R)', bg=PANEL, fg='#70d9c4', font=('Microsoft YaHei UI', 8, 'bold')).pack(anchor='w')

        rowR1 = tk.Frame(rightCol, bg=PANEL)
        rowR1.pack(fill='x', pady=1)
        for name in ['A', 'B', 'R3']:
            lbl = tk.Label(rowR1, text=name, bg='#1e293b', fg='#64748b', font=('Consolas', 9, 'bold'), width=4, relief='flat')
            lbl.pack(side='left', padx=1)
            self.picoBadges[name] = lbl

        rowR2 = tk.Frame(rightCol, bg=PANEL)
        rowR2.pack(fill='x', pady=1)
        for name in ['RT', 'RG', 'START']:
            w = 5 if name == 'START' else 4
            lbl = tk.Label(rowR2, text=name, bg='#1e293b', fg='#64748b', font=('Consolas', 8, 'bold'), width=w, relief='flat')
            lbl.pack(side='left', padx=1)
            self.picoBadges[name] = lbl
        footer = tk.Frame(self.root, bg=BACKGROUND)
        footer.pack(fill='x', padx=28, pady=(16, 22))
        tk.Label(footer, textvariable=self.status, bg=BACKGROUND, fg='#70d9c4',
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        tk.Label(footer, textvariable=self.detail, bg=BACKGROUND, fg=MUTED,
                 font=('Microsoft YaHei UI', 10), wraplength=1020, justify='left').pack(anchor='w', pady=(5, 0))

    def button(self, parent, text, command, color='#283b56'):
        return tk.Button(parent, text=text, command=command, bg=color, fg=TEXT,
                         activebackground='#36567c', activeforeground='white', relief='flat', bd=0,
                         padx=18, pady=9, cursor='hand2', font=('Microsoft YaHei UI', 10))

    def updateAddress(self):
        if hasattr(self, 'addressChoice'):
            self.address.set(f'http://{self.addressChoice.get()}:{self.port.get()}')

    def selectAddress(self, _event):
        self.updateAddress()
        self.root.focus_set()

    def copyAddress(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.address.get())
        self.root.focus_set()
        self.detail.set('连接地址已复制。请在手机 App 顶部填写这个地址，然后点击连接。')

    def onChannelChange(self):
        mode = self.channelMode.get()
        if mode == 'mp4':
            self.rovPanel.pack_forget()
            self.mp4Panel.pack(fill='x', padx=28, after=self.channelRadioRov.master)
            self.loopCheck.config(state='normal')
            self.startButton.config(text='开始推流 (MP4)')
            if not self.publisher:
                self.status.set('信道 1：本地 MP4 推流 · 请选择视频')
                self.detail.set('选择视频后点击“开始推流”，再用手机连接右侧地址。')
        else:
            self.mp4Panel.pack_forget()
            self.rovPanel.pack(fill='x', padx=28, after=self.channelRadioRov.master)
            self.loopCheck.config(state='disabled')
            self.startButton.config(text='开始转运 (ROV)')
            if not self.publisher:
                port = self.rovPort.get()
                self.status.set(f'信道 2：水下 ROV 直接转运 · 准备接收 UDP {port}')
                self.detail.set(f'下位机通过 RTP 发送视频到 PC 的 UDP {port} 端口；点击“开始转运”后自动解码同流并转送手机。')

    def chooseVideo(self):
        path = filedialog.askopenfilename(parent=self.root, title='选择要推流的 MP4',
                                         filetypes=[('MP4 视频', '*.mp4'), ('所有文件', '*.*')])
        if path:
            self.videoPath.set(path)
            self.status.set('视频已选择 · 点击开始推流')

    def refreshDeviceControls(self):
        busy = self.installing or self.configuring
        self.phoneButton.config(state='disabled' if busy or self.stopping else 'normal')
        self.moreMenu.entryconfigure(0, state='disabled' if busy else 'normal')

    def installApk(self):
        """次级菜单执行安装；adb install 自动传输 APK，无需手动复制。"""
        if self.installing or self.configuring:
            return
        self.installing = True
        self.refreshDeviceControls()
        self.detail.set('正在传输并安装 APK，请保持 USB 连接并允许手机上的调试 / 安装提示。')
        def install():
            try:
                AdbBridge(PROJECT_ROOT).install(PROJECT_ROOT / 'display/build/outputs/apk/debug/app-debug.apk')
                self.events.put(('installed', '安装完成。点击“连接手机”下发地址与端口并握手。'))
            except Exception as error:
                self.events.put(('installFailed', str(error)))
        threading.Thread(target=install, daemon=True).start()

    def configurePhone(self):
        if self.installing or self.configuring or self.stopping:
            return
        try:
            self.ensureServer()
        except Exception as error:
            self.detail.set(str(error))
            self.stop('服务启动失败，请检查端口')
            messagebox.showerror('连接手机', str(error), parent=self.root)
            return
        host = self.addressChoice.get()
        port = self.server.server_port
        self.configuring = True
        self.refreshDeviceControls()
        self.detail.set('正在通过 ADB 配置手机；USB 只负责配置，握手通过局域网完成。')
        def configure():
            try:
                message = AdbBridge(PROJECT_ROOT).configure(host, port)
                self.events.put(('configured', message))
            except Exception as error:
                self.events.put(('configureFailed', str(error)))
        threading.Thread(target=configure, daemon=True).start()

    def ensureServer(self):
        """网络服务可先于视频启动，手机能够先握手再等待视频就绪。"""
        try:
            port = int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            raise ValueError('端口应为 1 到 65535 之间的整数。')
        if self.server:
            if port != self.server.server_port:
                raise ValueError('请先停止当前服务，再更改端口。')
            return
        tempRoot = TOOL_ROOT / '.tools/temp'
        tempRoot.mkdir(parents=True, exist_ok=True)
        import tempfile
        self.sessionDirectory = Path(tempfile.mkdtemp(prefix='pc-session-', dir=tempRoot))
        self.logOutput = (self.sessionDirectory / 'ffmpeg.log').open('wb')
        self.connections = ConnectionRegistry()
        self.streamHub = LiveStreamHub()
        self.everConnected = False
        self.server = LanServer(('0.0.0.0', port),
                                handler_factory(self.sessionDirectory, self.state, self.connections, self.streamHub, self.picoReceiver))
        self.serverThread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serverThread.start()
        self.portEntry.config(state='disabled')
        self.stopButton.config(state='normal')
        self.directionControls.setEnabled(True)
        self.status.set('服务就绪 · 等待手机握手 / 选择视频')

    def setRunning(self, running):
        self.startButton.config(state='disabled' if running else 'normal')
        self.chooseButton.config(state='disabled' if running else 'normal')
        self.channelRadioMp4.config(state='disabled' if running else 'normal')
        self.channelRadioRov.config(state='disabled' if running else 'normal')
        self.rovPortChoice.config(state='disabled' if running else 'normal')
        self.portEntry.config(state='disabled' if self.server else 'normal')
        self.loopCheck.config(state='disabled' if (running or self.channelMode.get() == 'rov') else 'normal')
        self.timecodeCheck.config(state='disabled' if running else 'normal')
        self.stopButton.config(state='normal' if self.server else 'disabled')
        self.directionControls.setEnabled(bool(self.server) and not self.stopping)
        self.refreshDeviceControls()

    def start(self):
        if self.publisher or self.stopping:
            return
        isRov = (self.channelMode.get() == 'rov')
        if isRov:
            try:
                rovPort = int(self.rovPort.get())
                if not 1 <= rovPort <= 65535:
                    raise ValueError()
            except ValueError:
                messagebox.showerror('ROV 端口无效', '水下 ROV UDP 端口应为 1 到 65535 之间的整数。', parent=self.root)
                return
            videoSource = None  # 稍后在 sessionDirectory 中生成 rov.sdp
        else:
            video = Path(self.videoPath.get())
            if not self.videoPath.get() or not video.is_file():
                messagebox.showinfo('请选择视频', '先点击“选择 MP4”，选择一个本机视频文件。', parent=self.root)
                return
            videoSource = video.resolve()

        try:
            port = int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            messagebox.showerror('端口无效', '端口应为 1 到 65535 之间的整数。', parent=self.root)
            return
        if not self.encoderPath or not Path(self.encoderPath).is_file():
            filterTypes = [('FFmpeg', 'ffmpeg*.exe')] if os.name == 'nt' else [('FFmpeg', 'ffmpeg*'), ('所有文件', '*')]
            self.encoderPath = filedialog.askopenfilename(parent=self.root, title='选择 ffmpeg 可执行文件',
                                                         filetypes=filterTypes)
            if not self.encoderPath:
                return
        timecodeFont = None
        if self.timecode.get():
            fontCandidates = [
                Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/consola.ttf',
                Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),
                Path('/usr/share/fonts/truetype/freefont/FreeMono.ttf'),
                Path('/usr/share/fonts/TTF/DejaVuSansMono.ttf'),
                Path('/usr/share/fonts/dejavu/DejaVuSansMono.ttf')
            ]
            for candidate in fontCandidates:
                if candidate.is_file():
                    timecodeFont = candidate
                    break
            if not timecodeFont:
                messagebox.showerror('时间码字体不可用', '未找到等宽字体，请取消“显示时间码”后启动。', parent=self.root)
                return
        try:
            self.ensureServer()
            if isRov:
                # 依据远端下位机实际 H.264 RTP 格式动态生成 SDP 文件
                sdpFile = self.sessionDirectory / f'rov_{rovPort}.sdp'
                sdpContent = (
                    "v=0\r\n"
                    "o=- 0 0 IN IP4 127.0.0.1\r\n"
                    "s=ROV Stream\r\n"
                    "c=IN IP4 0.0.0.0\r\n"
                    "t=0 0\r\n"
                    f"m=video {rovPort} RTP/AVP 96\r\n"
                    "a=rtpmap:96 H264/90000\r\n"
                    "a=fmtp:96 packetization-mode=1\r\n"
                )
                sdpFile.write_text(sdpContent, encoding='ascii')
                videoSource = sdpFile

            self.publisher = RealtimePublisher(self.encoderPath, videoSource, self.sessionDirectory,
                                          self.loop.get() if not isRov else False, logOutput=self.logOutput,
                                          sourcePreview=True, timecodeFont=timecodeFont, streamHub=self.streamHub,
                                          isRov=isRov)
            self.publisher.start()
            self.preview = SourcePreview(self.publisher.process)
        except Exception as error:
            self.detail.set(str(error))
            self.stop('启动失败，请检查端口或视频源配置')
            messagebox.showerror('启动失败', str(error), parent=self.root)
            return
        self.previewImage = None
        self.previewSampleAt = time.monotonic()
        self.previewSampleCount = 0
        self.startedAt = time.monotonic()
        self.endedAt = None
        self.setRunning(True)
        self.root.focus_set()
        if isRov:
            self.status.set(f'水下 ROV 转运中 · 正在监听 UDP {rovPort}…')
            self.detail.set(f'PC 正在监听 UDP {rovPort} 接收 ROV 视频；同时以逐帧极速流转发至手机。')
            self.previewLabel.config(image='', text=f'正在等待水下 ROV 视频信号 (UDP {rovPort})…\n\n信号接入后自动同流预览')
        else:
            self.status.set('启动中 · 正在发送实时视频…')
            self.detail.set('PC 显示本地源画面；手机连接右侧地址。对照同一时刻两块屏幕上的 T 时间码。')
            self.previewLabel.config(image='', text='正在读取 MP4 源画面…')

    def stop(self, reason='已停止 · 可以选择另一个视频重新开始'):
        if self.stopping:
            return
        if not any((self.publisher, self.server, self.sessionDirectory)):
            if self.closePending and not (self.installing or self.configuring):
                self.finishClose()
            return
        self.stopping = True
        self.directionControls.setEnabled(False)
        self.startButton.config(state='disabled')
        self.stopButton.config(state='disabled')
        self.status.set('正在停止…')
        self.refreshDeviceControls()
        resources = (self.preview, self.publisher, self.server, self.serverThread,
                     self.logOutput, self.sessionDirectory, self.streamHub)
        self.preview = self.publisher = self.server = self.serverThread = self.logOutput = None
        self.sessionDirectory = None
        self.streamHub = None
        self.connections = None
        self.everConnected = False
        self.handshakeDeadline = None
        def cleanup():
            preview, publisher, server, serverThread, logOutput, directory, streamHub = resources
            try:
                if publisher:
                    publisher.close()
                if preview:
                    preview.close()
                if streamHub:
                    streamHub.close()
                if server:
                    server.shutdown()
                    server.server_close()
                if serverThread:
                    serverThread.join(timeout=3)
                if logOutput:
                    logOutput.close()
                if directory:
                    resolvedDirectory = directory.resolve()
                    if resolvedDirectory.parent != (TOOL_ROOT / '.tools/temp').resolve() or not directory.name.startswith('pc-session-'):
                        raise RuntimeError('临时目录不在本工程范围内，已保留目录')
                    shutil.rmtree(resolvedDirectory)
                self.events.put(('stopped', reason))
            except Exception as error:
                self.events.put(('stopped', f'已停止；清理提示：{error}'))
        threading.Thread(target=cleanup, daemon=True).start()

    def tick(self):
        if self.closed:
            return
        try:
            event, message = self.events.get_nowait()
            if event == 'stopped':
                self.stopping = False
                self.setRunning(False)
                self.status.set(message)
                self.previewLabel.config(image='', text='推流已停止')
                self.previewLabel.image = None
                self.previewImage = None
                self.previewInfo.set("本地源画面 · 无声 / 30fps · 时间码差 ≈ 相对延迟")
                if self.closePending and not (self.installing or self.configuring):
                    self.finishClose()
                    return
            elif event in ('installed', 'installFailed'):
                self.installing = False
                self.refreshDeviceControls()
                self.detail.set(message)
                if not self.closePending:
                    if event == 'installed':
                        messagebox.showinfo('手机 App', message, parent=self.root)
                    else:
                        messagebox.showerror('安装失败', message, parent=self.root)
                elif not self.publisher and not self.stopping:
                    self.finishClose()
                    return
            elif event in ('configured', 'configureFailed'):
                if event == 'configured' and self.server:
                    self.handshakeDeadline = time.monotonic() + 10
                self.configuring = False
                self.refreshDeviceControls()
                self.detail.set(message)
                if self.closePending and not self.publisher and not self.stopping:
                    self.finishClose()
                    return
                if event == 'configureFailed' and not self.closePending:
                    messagebox.showerror('手机连接未完成', message, parent=self.root)
        except queue.Empty:
            pass
        if self.connections:
            clients = self.connections.connectedClients()
            if clients:
                self.handshakeDeadline = None
                self.everConnected = True
                names = ', '.join(client['name'] for client in clients)
                self.connectionStatus.set(f'已握手 · {names}（在线）')
            elif self.everConnected:
                self.connectionStatus.set('手机连接已中断 · 等待重新握手')
            else:
                self.connectionStatus.set('服务就绪 · 等待手机握手')
            if self.handshakeDeadline is not None and time.monotonic() > self.handshakeDeadline:
                self.detail.set('ADB 配置已下发，但尚未握手。请确认手机 App 在前台、Wi-Fi 可互访，并放行当前端口。')
                self.handshakeDeadline = None
        else:
            self.connectionStatus.set('手机：未握手')
        if self.publisher and not self.stopping:
            code = self.publisher.process.poll()
            if code is not None and code != 0:
                try:
                    errorText = (self.sessionDirectory / 'ffmpeg.log').read_text(encoding='utf-8', errors='replace')[-1000:]
                    self.detail.set(errorText.strip() or '视频编码失败，请选择可播放的 MP4 文件。')
                except OSError:
                    pass
                self.stop('视频编码失败，请检查视频文件')
            elif self.streamHub and self.streamHub.isReady():
                self.status.set('实时推流中 · 连续视频 / 无分片')
            elif time.monotonic() - self.startedAt > 20:
                self.status.set('视频准备时间较长 · 请确认文件可正常播放')
            if code == 0 and not self.stopping:
                # 文件播完后保留服务，让已缓冲的尾部视频正常播放完毕。
                if self.endedAt is None:
                    self.endedAt = time.monotonic()
                self.status.set('视频源已结束 · 等待缓冲播放完成…')
                if time.monotonic() - self.endedAt >= 1:
                    self.stop('视频播放完成 · 可以重新开始')

        # --- PICO 手柄实时遥测与按键高亮刷新 ---
        picoSnap = self.picoReceiver.snapshot()
        if picoSnap["connected"]:
            src = picoSnap.get("source", "active").upper()
            fps = picoSnap.get("fps", 0)
            self.picoStatusLabel.config(text=f"已连接 · {src} {fps}Hz", fg="#10b981")
            lx, ly = picoSnap["left_stick"]
            rx, ry = picoSnap["right_stick"]
            lt, rt = picoSnap["triggers"]
            lg, rg = picoSnap.get("grips", [0.0, 0.0])
            self.picoSticksLabel.config(text=f"左摇杆: ({lx:+.2f}, {ly:+.2f})  右: ({rx:+.2f}, {ry:+.2f})")
            self.picoTriggersLabel.config(text=f"LT:{int(lt*100):2d}% LG:{int(lg*100):2d}% | RT:{int(rt*100):2d}% RG:{int(rg*100):2d}%")

            pressedSet = set(picoSnap["pressed_buttons"])
            if (picoSnap["buttons"] & (1 << 6)): pressedSet.add("L3")
            if (picoSnap["buttons"] & (1 << 7)): pressedSet.add("R3")

            for name, badge in self.picoBadges.items():
                if name in pressedSet:
                    badge.config(bg="#10b981", fg="#ffffff")
                else:
                    badge.config(bg="#1e293b", fg="#64748b")

            # 摇杆联动 PC 方向状态指示
            self.state.set_pressed("UP", ly < -0.3)
            self.state.set_pressed("DOWN", ly > 0.3)
            self.state.set_pressed("LEFT", lx < -0.3)
            self.state.set_pressed("RIGHT", lx > 0.3)
        else:
            self.picoStatusLabel.config(text="未连接 (UDP/ADB)", fg=MUTED)
            self.picoSticksLabel.config(text="左摇杆: (0.00, 0.00)  右: (0.00, 0.00)")
            self.picoTriggersLabel.config(text="LT: 0% LG: 0% | RT: 0% RG: 0%")
            for badge in self.picoBadges.values():
                badge.config(bg="#1e293b", fg="#64748b")

        self.statusTimer = self.root.after(100, self.tick)

    def renderPreview(self):
        """独立帧时钟刷新，复用同一个 Tk 图像，避免反复创建和删除图像对象。"""
        if self.closed:
            return
        preview = self.preview
        if preview and not self.stopping:
            try:
                frame = preview.frames.get_nowait()
                if self.previewImage is None:
                    self.previewImage = tk.PhotoImage(data=frame, format='PPM')
                    self.previewLabel.config(image=self.previewImage, text='')
                    self.previewLabel.image = self.previewImage
                else:
                    self.previewImage.configure(data=frame, format='PPM')
                preview.frameCount += 1
            except queue.Empty:
                pass
            now = time.monotonic()
            elapsed = now - self.previewSampleAt
            if elapsed >= 1:
                rate = (preview.frameCount - self.previewSampleCount) / elapsed
                self.previewInfo.set(f'PC 预览 {rate:.1f} fps · 本地源画面 · 无声 · 时间码差 ≈ 相对延迟')
                self.previewSampleAt = now
                self.previewSampleCount = preview.frameCount
            # 源帧已按 30fps 节拍输出；更快检查队列，有新帧才绘制。
            # 不再额外等待一个 33ms 周期，避免两个时钟相位抖动导致丢帧。
            delay = 8
        else:
            delay = 100
        self.previewTimer = self.root.after(delay, self.renderPreview)

    def close(self):
        self.closePending = True
        self.stop()

    def finishClose(self):
        self.closed = True
        for timer in (self.statusTimer, self.previewTimer):
            self.root.after_cancel(timer)
        self.directionControls.clear()
        self.picoReceiver.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    PcController(root)
    root.mainloop()


if __name__ == '__main__':
    main()
