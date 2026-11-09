"""PC 到 Android 的 ADB 适配器；命令参数经过校验，不使用本机 shell 拼接。"""
from pathlib import Path
import os
import re
import shutil
import subprocess


class AdbBridge:
    def __init__(self, projectRoot):
        candidate = Path(projectRoot).parent / 'application/.tools/android-sdk/platform-tools/adb.exe'
        self.executable = str(candidate) if candidate.is_file() else shutil.which('adb')
        if not self.executable:
            raise RuntimeError('未找到 ADB，请先配置 Android Platform Tools。')

    def run(self, arguments, timeout=20):
        result = subprocess.run([self.executable, *arguments], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            if 'INSTALL_FAILED_ABORTED' in detail or 'User rejected permissions' in detail:
                raise RuntimeError('手机拒绝安装。请解锁手机并允许 USB 安装 / 安装确认后重试。')
            raise RuntimeError(detail)
        return result.stdout

    def device(self):
        devices = [line.split()[0] for line in self.run(['devices']).splitlines()
                   if len(line.split()) == 2 and line.split()[1] == 'device']
        if len(devices) != 1:
            raise RuntimeError('请只连接一台已授权的手机；解锁后允许 USB 调试。')
        return devices[0]

    def install(self, apkPath):
        if not Path(apkPath).is_file():
            raise RuntimeError('未找到 APK，请先完成 Android 构建。')
        self.run(['-s', self.device(), 'install', '-r', str(apkPath)], timeout=120)

    def configure(self, host, port):
        if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', host) or not 1 <= port <= 65535:
            raise ValueError('PC 地址或端口无效。')
        serial = self.device()
        package = self.run(['-s', serial, 'shell', 'dumpsys', 'package', 'com.iwin.application.xr'])
        version = re.search(r'versionCode=(\d+)', package)
        if version is None or int(version.group(1)) < 4:
            raise RuntimeError('请先从右上角“更多 → 安装 / 更新手机 App”安装新版 IWIN LAN。')
        result = self.run(['-s', serial, 'shell', 'am', 'start', '-W',
                          '-n', 'com.iwin.application.xr/com.iwin.application.XrActivity',
                          '-a', 'com.iwin.application.CONFIGURE',
                          '--es', 'iwin_server_host', host,
                          '--ei', 'iwin_server_port', str(port),
                          '--ez', 'iwin_connect', 'true'])
        if re.search(r'(?im)^\s*(Error|Exception)', result):
            raise RuntimeError(result.strip())
        # ADB 指令成功不代表 Wi-Fi 可达；从手机侧探测真实 TCP 链路。
        try:
            self.run(['-s', serial, 'shell', 'toybox', 'nc', '-z', '-w', '3', host, str(port)], timeout=8)
        except RuntimeError as error:
            detail = str(error)
            if any(value in detail.lower() for value in ('not found', 'unknown option', 'unknown command')):
                return f'配置已发送：{host}:{port}；设备不支持 TCP 探测，请等待实际握手。'
            else:
                raise RuntimeError(f'地址和端口已写入 App，但手机无法访问 {host}:{port}。'
                                   '请使用可互访的 Wi-Fi / 手机热点，并检查 PC 防火墙。'
                                   f'\n设备诊断：{detail}')
        return f'配置已发送：{host}:{port}，手机 TCP 可达。请解锁手机，等待握手。' 
