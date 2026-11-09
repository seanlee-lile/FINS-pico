"""PC 方向输入适配器：领域状态 + tkinter 键盘源，无视频或网络依赖。"""
from __future__ import annotations
import threading

DIRECTIONS = {"Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT"}


class DirectionState:
    """线程安全的完整状态；键盘源可替换为手柄、串口或其他设备。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pressed: set[str] = set()
        self._sequence = 0

    def set_pressed(self, direction: str, pressed: bool):
        if direction not in DIRECTIONS.values():
            raise ValueError(f"Unknown direction: {direction}")
        with self._lock:
            before = self._pressed.copy()
            if pressed:
                self._pressed.add(direction)
            else:
                self._pressed.discard(direction)
            if before != self._pressed:
                self._sequence += 1

    def clear(self):
        with self._lock:
            if self._pressed:
                self._sequence += 1
                self._pressed.clear()

    def snapshot(self):
        with self._lock:
            return {"directions": sorted(self._pressed), "sequence": self._sequence}


def run_keyboard_window(state, video_tick):
    import tkinter as tk

    root = tk.Tk()
    root.title("IWIN · PC 方向输入")
    root.geometry("620x230")
    tk.Label(root, text="点击本窗口后，按 ↑ ↓ ← →\n可同时按多个方向；失去焦点自动释放",
             font=("Microsoft YaHei", 14)).pack(pady=15)
    label = tk.Label(root, text="方向：—", font=("Microsoft YaHei", 24))
    label.pack()
    status = tk.Label(root, text="等待视频分片…")
    status.pack()
    pending_release = {}

    def show():
        symbols = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}
        label.config(text="方向：" + (" ".join(symbols[d] for d in state.snapshot()["directions"]) or "—"))

    def press(event):
        direction = DIRECTIONS.get(event.keysym)
        if direction:
            task = pending_release.pop(direction, None)
            if task:
                root.after_cancel(task)
            state.set_pressed(direction, True)
            show()

    def release(event):
        direction = DIRECTIONS.get(event.keysym)
        if direction:
            old = pending_release.pop(direction, None)
            if old:
                root.after_cancel(old)

            def commit():
                pending_release.pop(direction, None)
                state.set_pressed(direction, False)
                show()

            # Linux 的自动重复可能生成 Release/Press 对，短暂延迟可过滤该抖动。
            pending_release[direction] = root.after(30, commit)

    def focus_lost(_event):
        for task in pending_release.values():
            root.after_cancel(task)
        pending_release.clear()
        state.clear()
        show()

    def tick():
        # 调用者注入视频状态，输入模块不引用编码器、HTTP 或播放器。
        status.config(text=video_tick())
        root.after(250, tick)

    root.bind("<KeyPress>", press)
    root.bind("<KeyRelease>", release)
    root.bind("<FocusOut>", focus_lost)
    root.after(250, tick)
    try:
        root.mainloop()
    finally:
        state.clear()


