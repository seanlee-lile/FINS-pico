"""可嵌入 PC 控制窗口的方向输入面板，不依赖视频或网络。"""
from __future__ import annotations
import tkinter as tk
from keyboard_input import DIRECTIONS, DirectionState


class DirectionControls:
    """将键盘和鼠标来源合并为完整快照；失焦与停止时统一释放。"""

    def __init__(self, parent, root, state: DirectionState):
        self.root = root
        self.state = state
        self.enabled = False
        self.keyboardPressed = set()
        self.mousePressed = set()
        self.pendingRelease = {}
        self.frame = tk.Frame(parent, bg="#172235")
        self.label = tk.Label(self.frame, text="方向：—", bg="#172235", fg="#e8eff8",
                              font=("Microsoft YaHei UI", 16, "bold"))
        self.label.pack(pady=(12, 10))
        grid = tk.Frame(self.frame, bg="#172235")
        grid.pack(pady=(0, 10))
        self.buttons = {}
        for direction, symbol, row, column in [
                ("UP", "↑", 0, 1), ("LEFT", "←", 1, 0),
                ("DOWN", "↓", 1, 1), ("RIGHT", "→", 1, 2)]:
            button = tk.Button(grid, text=symbol, width=4, font=("Segoe UI", 19),
                               bg="#253750", fg="#e8eff8", activebackground="#3b82f6",
                               activeforeground="white", relief="flat", bd=0, state="disabled")
            button.grid(row=row, column=column, padx=4, pady=4)
            button.bind("<ButtonPress-1>", lambda event, value=direction: self.mouseDown(value))
            button.bind("<ButtonRelease-1>", lambda event, value=direction: self.mouseUp(value))
            button.bind("<Leave>", lambda event, value=direction: self.mouseUp(value))
            self.buttons[direction] = button
        tk.Label(self.frame, text="按键盘方向键，或按住上方按钮\n切换到其他窗口后自动释放",
                 bg="#172235", fg="#a7b7cc", font=("Microsoft YaHei UI", 10),
                 justify="center").pack(pady=(0, 14))
        root.bind("<KeyPress>", self.keyDown, add="+")
        root.bind("<KeyRelease>", self.keyUp, add="+")
        root.bind("<FocusOut>", self.focusOut, add="+")

    def setEnabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.clear()
        for button in self.buttons.values():
            button.config(state="normal" if enabled else "disabled")

    def publish(self):
        merged = self.keyboardPressed | self.mousePressed
        for direction in DIRECTIONS.values():
            self.state.set_pressed(direction, direction in merged)
        symbols = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}
        self.label.config(text="方向：" + (" ".join(symbols[d] for d in DIRECTIONS.values() if d in merged) or "—"))
        for direction, button in self.buttons.items():
            button.config(bg="#2563eb" if direction in merged else "#253750")

    def keyDown(self, event):
        direction = DIRECTIONS.get(event.keysym)
        if not self.enabled or not direction:
            return
        if event.widget.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox", "Text"):
            return
        pending = self.pendingRelease.pop(direction, None)
        if pending:
            self.root.after_cancel(pending)
        self.keyboardPressed.add(direction)
        self.publish()
        return "break"

    def keyUp(self, event):
        direction = DIRECTIONS.get(event.keysym)
        if not direction or not self.enabled:
            return
        pending = self.pendingRelease.pop(direction, None)
        if pending:
            self.root.after_cancel(pending)
        def release():
            self.pendingRelease.pop(direction, None)
            self.keyboardPressed.discard(direction)
            self.publish()
        self.pendingRelease[direction] = self.root.after(30, release)

    def mouseDown(self, direction):
        if self.enabled:
            self.root.focus_set()
            self.mousePressed.add(direction)
            self.publish()

    def mouseUp(self, direction):
        self.mousePressed.discard(direction)
        self.publish()

    def focusOut(self, _event):
        # 在同一窗口内切换控件时不误释放；离开应用时释放所有输入。
        self.root.after_idle(self.releaseIfInactive)

    def releaseIfInactive(self):
        # ttk 下拉列表的 popdown 是 Tcl 原生窗口，没有对应 Python Widget。
        # 直接检查 Tcl 焦点路径，避免 focus_displayof 的对象映射抛 KeyError。
        try:
            focusPath = self.root.tk.call('focus', '-displayof', self.root._w)
        except tk.TclError:
            return  # 窗口已关闭。
        if not focusPath:
            self.clear()

    def clear(self):
        for pending in self.pendingRelease.values():
            self.root.after_cancel(pending)
        self.pendingRelease.clear()
        self.keyboardPressed.clear()
        self.mousePressed.clear()
        self.state.clear()
        self.publish()
