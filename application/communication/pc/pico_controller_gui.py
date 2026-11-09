"""PICO 4 Ultra 手柄全按键与双摇杆实时可视化控制台。

直连 PICO 4 Ultra 底层服务与 UDP 网络，零延迟呈现每一个按键、扳机深度、摇杆坐标及 6DoF 姿态。
"""
from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

BACKGROUND = "#0b132b"
PANEL_BG = "#1c2541"
TEXT_COLOR = "#f8fafc"
ACCENT_GREEN = "#10b981"
INACTIVE_BG = "#334155"
INACTIVE_FG = "#94a3b8"

BUTTON_GRID = {
    # 左手按键
    "L": [
        [("X", "X"), ("Y", "Y"), ("L3", "L3")],
        [("LT", "LT"), ("LG", "LG"), ("SELECT", "SEL")],
    ],
    # 右手按键
    "R": [
        [("A", "A"), ("B", "B"), ("R3", "R3")],
        [("RT", "RT"), ("RG", "RG"), ("START", "STA")],
    ]
}


class PicoControllerGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PICO 4 Ultra · 手柄全按键遥测控制台")
        self.root.geometry("820x620")
        self.root.minsize(780, 580)
        self.root.configure(bg=BACKGROUND)

        self.running = True
        self.badges = {}
        self.left_stick_val = [0.0, 0.0]
        self.right_stick_val = [0.0, 0.0]
        self.triggers = [0.0, 0.0]
        self.grips = [0.0, 0.0]
        self.pressed_keys = set()
        self.pose_text = "等待空间追踪…"
        self.fps_counter = 0
        self.fps_time = time.monotonic()
        self.current_fps = 0.0

        self.build_ui()
        self.start_worker_thread()
        self.root.after(40, self.update_ui)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        # 顶部标题栏
        header = tk.Frame(self.root, bg=BACKGROUND, padx=20, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="PICO 4 Ultra 物理手柄实时控制台", bg=BACKGROUND, fg="#38bdf8",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        self.status_lbl = tk.Label(header, text="正在侦测手柄连接…", bg=BACKGROUND, fg="#fbbf24",
                                   font=("Microsoft YaHei UI", 10, "bold"))
        self.status_lbl.pack(side="right")

        # 主内容区 (左右双手并排)
        content = tk.Frame(self.root, bg=BACKGROUND, padx=16, pady=4)
        content.pack(fill="both", expand=True)

        self.left_card = self.create_hand_card(content, "左手柄 (Left Controller)", "L")
        self.left_card.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.right_card = self.create_hand_card(content, "右手柄 (Right Controller)", "R")
        self.right_card.pack(side="right", fill="both", expand=True, padx=(8, 0))

        # 底部状态栏与 6DoF 空间位姿
        footer = tk.Frame(self.root, bg=PANEL_BG, padx=16, pady=10)
        footer.pack(fill="x", padx=16, pady=(10, 14))
        self.pose_lbl = tk.Label(footer, text="空间位姿: X: -- mm | Y: -- mm | Z: -- mm | 四元数: [--, --, --, --]",
                                 bg=PANEL_BG, fg="#94a3b8", font=("Consolas", 10))
        self.pose_lbl.pack(anchor="w")

    def create_hand_card(self, parent, title, side):
        card = tk.Frame(parent, bg=PANEL_BG, padx=16, pady=14, relief="ridge", bd=1)

        # 标题与电量
        hdr = tk.Frame(card, bg=PANEL_BG)
        hdr.pack(fill="x", pady=(0, 10))
        tk.Label(hdr, text=title, bg=PANEL_BG, fg="#38bdf8", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        bat = tk.Label(hdr, text="电量: 100%", bg=PANEL_BG, fg="#a3e635", font=("Consolas", 9))
        bat.pack(side="right")
        setattr(self, f"{side.lower()}_bat_lbl", bat)

        # 虚拟摇杆盘 (Canvas)
        stick_frame = tk.Frame(card, bg=PANEL_BG)
        stick_frame.pack(fill="x", pady=(0, 10))
        tk.Label(stick_frame, text="摇杆模拟量:", bg=PANEL_BG, fg=TEXT_COLOR, font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        canvas = tk.Canvas(stick_frame, width=130, height=130, bg="#0f172a", highlightthickness=1, highlightbackground="#334155")
        canvas.pack(pady=6)
        # 绘制十字刻度与外圈
        canvas.create_oval(10, 10, 120, 120, outline="#475569", width=1)
        canvas.create_line(65, 10, 65, 120, fill="#334155", dash=(2, 2))
        canvas.create_line(10, 65, 120, 65, fill="#334155", dash=(2, 2))
        dot = canvas.create_oval(60, 60, 70, 70, fill="#38bdf8", outline="#ffffff")
        setattr(self, f"{side.lower()}_canvas", canvas)
        setattr(self, f"{side.lower()}_dot", dot)

        stick_val_lbl = tk.Label(stick_frame, text="X: +0.00  Y: +0.00", bg=PANEL_BG, fg="#cbd5e1", font=("Consolas", 10))
        stick_val_lbl.pack()
        setattr(self, f"{side.lower()}_stick_lbl", stick_val_lbl)

        # 扳机与侧握连续行程进度条
        slider_frame = tk.Frame(card, bg=PANEL_BG)
        slider_frame.pack(fill="x", pady=(0, 12))

        tk.Label(slider_frame, text="扳机 Trigger:", bg=PANEL_BG, fg="#94a3b8", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tr_bar = ttk.Progressbar(slider_frame, orient="horizontal", length=180, mode="determinate")
        tr_bar.pack(fill="x", pady=(2, 6))
        setattr(self, f"{side.lower()}_tr_bar", tr_bar)

        tk.Label(slider_frame, text="握持 Grip:", bg=PANEL_BG, fg="#94a3b8", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        gp_bar = ttk.Progressbar(slider_frame, orient="horizontal", length=180, mode="determinate")
        gp_bar.pack(fill="x", pady=(2, 2))
        setattr(self, f"{side.lower()}_gp_bar", gp_bar)

        # 每一个按键徽章网格
        btn_frame = tk.Frame(card, bg=PANEL_BG)
        btn_frame.pack(fill="x", pady=(6, 0))
        tk.Label(btn_frame, text="实体按键状态:", bg=PANEL_BG, fg=TEXT_COLOR, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        for row in BUTTON_GRID[side]:
            r_frame = tk.Frame(btn_frame, bg=PANEL_BG)
            r_frame.pack(fill="x", pady=2)
            for btn_key, btn_text in row:
                b_lbl = tk.Label(r_frame, text=btn_text, bg=INACTIVE_BG, fg=INACTIVE_FG,
                                 font=("Consolas", 10, "bold"), width=7, height=2, relief="flat")
                b_lbl.pack(side="left", padx=3, expand=True, fill="x")
                self.badges[btn_key] = b_lbl

        return card

    def start_worker_thread(self):
        t = threading.Thread(target=self.poll_hardware_loop, daemon=True)
        t.start()

    def poll_hardware_loop(self):
        """直连 PICO 4 Ultra 系统底层 pxrcontrollerservice 与 pvrtracking。"""
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        while self.running:
            try:
                cmd = ["adb", "shell", "/system_ext/bin/pxrcontrollerservice_tools GetControllerKeyData 1; echo ---; /system_ext/bin/pxrcontrollerservice_tools GetControllerKeyData 2; dumpsys pvrtracking | grep 'controller slam:'"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0, startupinfo=startupinfo)
                if res.returncode == 0 and "hand:" in res.stdout:
                    self.parse_data(res.stdout)
                    self.fps_counter += 1
            except Exception:
                pass
            time.sleep(0.04)

    def parse_data(self, raw_text: str):
        now = time.monotonic()
        if now - self.fps_time >= 1.0:
            self.current_fps = self.fps_counter / (now - self.fps_time)
            self.fps_counter = 0
            self.fps_time = now

        parts = raw_text.split("---")
        left_text = parts[0] if len(parts) > 0 else ""
        right_text = parts[1] if len(parts) > 1 else ""

        # 解析按键
        keys = set()

        def parse_one(text, is_right):
            km = re.search(r"key\.app:\s*(\d+).*?key\.a_x:\s*(\d+).*?key\.b_y:\s*(\d+).*?key\.grip:\s*(\d+).*?key\.trigger:\s*(\d+).*?key\.rocker:\s*(\d+)", text)
            tm = re.search(r"trigger:\s*(\d+)\s+grip:\s*(\d+)\s+rocker_x:\s*(\d+)\s+rocker_y:\s*(\d+)\s+battery:\s*(\d+)", text)
            rx, ry, tr, gp, bat = 128, 128, 0, 0, 4

            if km:
                app, ax, by, grip_btn, tr_btn, rocker_btn = [int(km.group(i)) for i in range(1, 7)]
                if is_right:
                    if ax: keys.add("A")
                    if by: keys.add("B")
                    if rocker_btn: keys.add("R3")
                    if app: keys.add("START")
                    if tr_btn: keys.add("RT")
                    if grip_btn: keys.add("RG")
                else:
                    if ax: keys.add("X")
                    if by: keys.add("Y")
                    if rocker_btn: keys.add("L3")
                    if app: keys.add("SELECT")
                    if tr_btn: keys.add("LT")
                    if grip_btn: keys.add("LG")

            if tm:
                tr = int(tm.group(1))
                gp = int(tm.group(2))
                rx = int(tm.group(3))
                ry = int(tm.group(4))
                bat = int(tm.group(5))

            stick_x = (rx - 128) / 128.0
            stick_y = (ry - 128) / 128.0
            return stick_x, stick_y, tr / 255.0, gp / 255.0, bat

        lx, ly, lt, lg, l_bat = parse_one(left_text, is_right=False)
        rx, ry, rt, rg, r_bat = parse_one(right_text, is_right=True)

        if lt > 0.4: keys.add("LT")
        if lg > 0.4: keys.add("LG")
        if rt > 0.4: keys.add("RT")
        if rg > 0.4: keys.add("RG")

        self.left_stick_val = [lx, ly]
        self.right_stick_val = [rx, ry]
        self.triggers = [lt, rt]
        self.grips = [lg, rg]
        self.pressed_keys = keys
        self.l_bat = l_bat
        self.r_bat = r_bat

        # 解析 6DoF 空间位姿
        pm = re.search(r"right pose\s+\d+,([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)", raw_text)
        if pm:
            qw, qx, qy, qz, px, py, pz = pm.groups()
            self.pose_text = f"右手 6DoF: X:{float(px):+7.1f} Y:{float(py):+7.1f} Z:{float(pz):+7.1f} mm | Q: [{float(qw):.2f}, {float(qx):.2f}, {float(qy):.2f}, {float(qz):.2f}]"

    def update_ui(self):
        if not self.running:
            return

        self.status_lbl.config(text=f"PICO 4 Ultra 硬件在线 · {self.current_fps:.1f} Hz", fg=ACCENT_GREEN)

        # 更新左手模拟量与点位
        lx, ly = self.left_stick_val
        self.left_stick_lbl.config(text=f"X: {lx:+.2f}  Y: {ly:+.2f}")
        dot_x = 65 + lx * 45
        dot_y = 65 + ly * 45
        self.l_canvas.coords(self.l_dot, dot_x - 5, dot_y - 5, dot_x + 5, dot_y + 5)
        self.l_tr_bar["value"] = self.triggers[0] * 100
        self.l_gp_bar["value"] = self.grips[0] * 100
        if hasattr(self, "l_bat"): self.l_bat_lbl.config(text=f"电量: {getattr(self, 'l_bat') * 25}%")

        # 更新右手模拟量与点位
        rx, ry = self.right_stick_val
        self.right_stick_lbl.config(text=f"X: {rx:+.2f}  Y: {ry:+.2f}")
        dot_x_r = 65 + rx * 45
        dot_y_r = 65 + ry * 45
        self.r_canvas.coords(self.r_dot, dot_x_r - 5, dot_y_r - 5, dot_x_r + 5, dot_y_r + 5)
        self.r_tr_bar["value"] = self.triggers[1] * 100
        self.r_gp_bar["value"] = self.grips[1] * 100
        if hasattr(self, "r_bat"): self.r_bat_lbl.config(text=f"电量: {getattr(self, 'r_bat') * 25}%")

        # 每一个按键徽章更新
        for name, badge in self.badges.items():
            if name in self.pressed_keys:
                badge.config(bg=ACCENT_GREEN, fg="#ffffff")
            else:
                badge.config(bg=INACTIVE_BG, fg=INACTIVE_FG)

        self.pose_lbl.config(text=self.pose_text)
        self.root.after(40, self.update_ui)

    def on_close(self):
        self.running = False
        self.root.destroy()


def main():
    root = tk.Tk()
    PicoControllerGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
