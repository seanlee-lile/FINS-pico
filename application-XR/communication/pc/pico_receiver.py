"""PICO 手柄遥测接收与解析模块。

支持两种底层信道自动融合：
1. UDP 高频直推 (端口 54322)：由头显 App 发送 104 字节全量二进制数据。
2. ADB 原生服务直通：调用系统底层 /system_ext/bin/pxrcontrollerservice_tools GetControllerKeyData 1(左) 和 2(右)，
   绕过 Android 2D 悬浮窗口将手柄作为触控板的限制，100% 获得左右摇杆模拟量、实体按键与扳机。
"""
from __future__ import annotations

import re
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

PICO_PACKET_FMT = "<IIQ4f4fI7f6f"
PICO_PACKET_SIZE = struct.calcsize(PICO_PACKET_FMT)
PICO_MAGIC = 0x5049434F

BUTTON_NAMES = {
    1 << 0: "A",
    1 << 1: "B",
    1 << 2: "X",
    1 << 3: "Y",
    1 << 4: "LT",
    1 << 5: "RT",
    1 << 6: "L3",
    1 << 7: "R3",
    1 << 8: "START",
    1 << 9: "SELECT",
    1 << 10: "UP",
    1 << 11: "DOWN",
    1 << 12: "LEFT",
    1 << 13: "RIGHT",
}


class PicoControllerReceiver:
    def __init__(self, port: int = 54322, on_update: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.port = port
        self.on_update = on_update
        self._lock = threading.Lock()
        self._running = False
        self._udp_thread: Optional[threading.Thread] = None
        self._adb_thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._last_udp_time = 0.0
        self._last_packet_time = 0.0
        self._current_fps = 0.0
        self._fps_time = time.monotonic()
        self._fps_counter = 0

        self._state: Dict[str, Any] = {
            "connected": False,
            "source": "none",
            "sequence": 0,
            "timestamp_ms": 0,
            "fps": 0.0,
            "left_stick": [0.0, 0.0],
            "right_stick": [0.0, 0.0],
            "triggers": [0.0, 0.0], # LT, RT
            "grips": [0.0, 0.0],    # LG, RG
            "buttons": 0,
            "pressed_buttons": [],
            "pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "velocity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }

    def start(self):
        if self._running:
            return
        self._running = True

        # 启动 UDP 监听
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.settimeout(0.5)
            self._udp_thread = threading.Thread(target=self._listen_udp_loop, daemon=True, name="PicoUdpReceiver")
            self._udp_thread.start()
        except Exception:
            pass

        # 启动 ADB 直连后台轮询（作为零配置保底与原生驱动访问）
        self._adb_thread = threading.Thread(target=self._adb_loop, daemon=True, name="PicoAdbReceiver")
        self._adb_thread.start()

    def _listen_udp_loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(256)
                if len(data) == PICO_PACKET_SIZE:
                    unpacked = struct.unpack(PICO_PACKET_FMT, data)
                    if unpacked[0] == PICO_MAGIC:
                        self._handle_udp_packet(unpacked)
            except (socket.timeout, BlockingIOError):
                pass
            except OSError:
                break

    def _handle_udp_packet(self, unpacked):
        now = time.monotonic()
        self._last_udp_time = now
        self._fps_counter += 1
        if now - self._fps_time >= 1.0:
            self._current_fps = self._fps_counter / (now - self._fps_time)
            self._fps_counter = 0
            self._fps_time = now

        seq = unpacked[1]
        ts_ms = unpacked[2]
        lx, ly, rx, ry = unpacked[3], unpacked[4], unpacked[5], unpacked[6]
        lt, rt, lg, rg = unpacked[7], unpacked[8], unpacked[9], unpacked[10]
        btn_mask = unpacked[11]
        px, py, pz, qx, qy, qz, qw = unpacked[12:19]
        lvx, lvy, lvz, avx, avy, avz = unpacked[19:25]

        pressed = [name for mask, name in BUTTON_NAMES.items() if (btn_mask & mask) != 0]

        with self._lock:
            self._last_packet_time = now
            self._state = {
                "connected": True,
                "source": "udp",
                "sequence": seq,
                "timestamp_ms": ts_ms,
                "fps": round(self._current_fps, 1),
                "left_stick": [round(lx, 3), round(ly, 3)],
                "right_stick": [round(rx, 3), round(ry, 3)],
                "triggers": [round(lt, 3), round(rt, 3)],
                "grips": [round(lg, 3), round(rg, 3)],
                "buttons": btn_mask,
                "pressed_buttons": pressed,
                "pose": [round(px, 3), round(py, 3), round(pz, 3), round(qx, 4), round(qy, 4), round(qz, 4), round(qw, 4)],
                "velocity": [round(lvx, 3), round(lvy, 3), round(lvz, 3), round(avx, 3), round(avy, 3), round(avz, 3)],
            }
            snapshot = dict(self._state)

        if self.on_update:
            self.on_update(snapshot)

    def _adb_loop(self):
        """当 UDP 未连接时，使用 ADB 管道持续抽取底层 pxrcontrollerservice 硬件状态。"""
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        seq = 0
        while self._running:
            # 如果 UDP 最近 1.2 秒内有数据，优先让位给高频 UDP
            if (time.monotonic() - self._last_udp_time) < 1.2:
                time.sleep(0.5)
                continue

            try:
                # 一次命令同时拉取左手(1)与右手(2)
                cmd = ["adb", "shell", "/system_ext/bin/pxrcontrollerservice_tools GetControllerKeyData 1; echo ---; /system_ext/bin/pxrcontrollerservice_tools GetControllerKeyData 2"]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0, startupinfo=startupinfo)
                if proc.returncode == 0 and "hand:" in proc.stdout:
                    parts = proc.stdout.split("---")
                    left_text = parts[0] if len(parts) > 0 else ""
                    right_text = parts[1] if len(parts) > 1 else ""

                    left_data = self._parse_key_data(left_text)
                    right_data = self._parse_key_data(right_text)

                    # 归一化摇杆：原始范围 0 ~ 255，128 为中心
                    lx = (left_data.get("rx", 128) - 128) / 128.0
                    ly = (left_data.get("ry", 128) - 128) / 128.0
                    rx = (right_data.get("rx", 128) - 128) / 128.0
                    ry = (right_data.get("ry", 128) - 128) / 128.0

                    # 扳机归一化：0 ~ 255 -> 0.0 ~ 1.0
                    lt = left_data.get("trigger", 0) / 255.0
                    rt = right_data.get("trigger", 0) / 255.0
                    lg = left_data.get("grip", 0) / 255.0
                    rg = right_data.get("grip", 0) / 255.0

                    btn_mask = 0
                    pressed = []
                    # 右手: a_x -> A, b_y -> B, rocker -> R3, app -> START
                    if right_data.get("a_x"): btn_mask |= (1 << 0); pressed.append("A")
                    if right_data.get("b_y"): btn_mask |= (1 << 1); pressed.append("B")
                    if right_data.get("trigger_btn") or rt > 0.4: btn_mask |= (1 << 5); pressed.append("RT")
                    if right_data.get("grip_btn") or rg > 0.4: pressed.append("RG")
                    if right_data.get("rocker_btn"): btn_mask |= (1 << 7); pressed.append("R3")
                    if right_data.get("app"): btn_mask |= (1 << 8); pressed.append("START")

                    # 左手: a_x -> X, b_y -> Y, rocker -> L3, app -> SELECT
                    if left_data.get("a_x"): btn_mask |= (1 << 2); pressed.append("X")
                    if left_data.get("b_y"): btn_mask |= (1 << 3); pressed.append("Y")
                    if left_data.get("trigger_btn") or lt > 0.4: btn_mask |= (1 << 4); pressed.append("LT")
                    if left_data.get("grip_btn") or lg > 0.4: pressed.append("LG")
                    if left_data.get("rocker_btn"): btn_mask |= (1 << 6); pressed.append("L3")
                    if left_data.get("app"): btn_mask |= (1 << 9); pressed.append("SELECT")

                    seq += 1
                    now = time.monotonic()
                    with self._lock:
                        self._last_packet_time = now
                        self._state = {
                            "connected": True,
                            "source": "adb",
                            "sequence": seq,
                            "timestamp_ms": int(time.time() * 1000),
                            "fps": 5.0,
                            "left_stick": [round(lx, 2), round(ly, 2)],
                            "right_stick": [round(rx, 2), round(ry, 2)],
                            "triggers": [round(lt, 2), round(rt, 2)],
                            "grips": [round(lg, 2), round(rg, 2)],
                            "buttons": btn_mask,
                            "pressed_buttons": pressed,
                            "pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                            "velocity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        }
                        snapshot = dict(self._state)
                    if self.on_update:
                        self.on_update(snapshot)
            except Exception:
                pass
            time.sleep(0.08)

    def _parse_key_data(self, text: str) -> Dict[str, int]:
        res = {}
        km = re.search(r"key\.app:\s*(\d+).*?key\.a_x:\s*(\d+).*?key\.b_y:\s*(\d+).*?key\.grip:\s*(\d+).*?key\.trigger:\s*(\d+).*?key\.rocker:\s*(\d+)", text)
        if km:
            res["app"] = int(km.group(1))
            res["a_x"] = int(km.group(2))
            res["b_y"] = int(km.group(3))
            res["grip_btn"] = int(km.group(4))
            res["trigger_btn"] = int(km.group(5))
            res["rocker_btn"] = int(km.group(6))
        tm = re.search(r"trigger:\s*(\d+)\s+grip:\s*(\d+)\s+rocker_x:\s*(\d+)\s+rocker_y:\s*(\d+)", text)
        if tm:
            res["trigger"] = int(tm.group(1))
            res["grip"] = int(tm.group(2))
            res["rx"] = int(tm.group(3))
            res["ry"] = int(tm.group(4))
        return res

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            is_alive = (time.monotonic() - self._last_packet_time) < 1.0
            state = dict(self._state)
            state["connected"] = is_alive
            return state

    def close(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._udp_thread and self._udp_thread.is_alive():
            self._udp_thread.join(timeout=0.5)
        if self._adb_thread and self._adb_thread.is_alive():
            self._adb_thread.join(timeout=0.5)
