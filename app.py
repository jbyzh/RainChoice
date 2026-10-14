from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import qrcode
from PIL import ImageTk

from core import DEFAULT_COURSE, RainClient, api_endpoint, demo, solve, Problem

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG = ROOT / "settings.json"
BG, WHITE, INK, MUTED, BLUE = "#f3f5f9", "#ffffff", "#18263c", "#6b7a90", "#2764e7"
MODE_LABELS = {"只提醒": "remind", "AI 建议，手动提交": "suggest", "AI 自动答题并提交": "auto"}


class App:
    def __init__(self, root):
        self.root = root
        root.title("雨课堂 · 选择题助手 v0.2")
        root.configure(bg=BG)
        width, height = min(1220, root.winfo_screenwidth()-80), min(880, root.winfo_screenheight()-100)
        root.geometry(f"{width}x{height}+40+40")
        root.minsize(1050, 840)
        self.events = queue.Queue()
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.client = RainClient(self.emit)
        self.stop_event = threading.Event()
        self.future = None
        self.rows = {}
        self.qr_window = None
        self.is_demo = False
        self.live_rows = {}
        self.live_stats = None
        self.controls = []
        self.worker_kind = ""
        self.status = tk.StringVar(value="尚未启动 · 可以先试试演示模式")
        self.login_text = tk.StringVar(value="未登录雨课堂")
        self.course_text = tk.StringVar(value="目标课程 / " + DEFAULT_COURSE)
        self.badge = tk.StringVar(value="待开始")
        self.stats = [tk.StringVar(value="0") for _ in range(3)]
        self.build()
        self.load_config()
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def emit(self, kind, value):
        self.events.put((kind, value))

    def label(self, parent, text=None, size=10, color=INK, bold=False, **kw):
        opts = dict(bg=parent.cget("bg"), fg=color, font=("Microsoft YaHei UI", size, "bold" if bold else "normal"))
        opts.update(kw)
        if text is not None:
            opts["text"] = text
        return tk.Label(parent, **opts)

    def button(self, parent, text, command, primary=False):
        b = tk.Button(parent, text=text, command=command, bg=BLUE if primary else "#edf2fc",
                      fg="white" if primary else "#30528a", activebackground="#dfe8fa",
                      relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                      font=("Microsoft YaHei UI", 10, "bold"))
        return b

    def build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TEntry", padding=7, fieldbackground=WHITE, bordercolor="#d9e1ed")
        style.configure("TCombobox", padding=6, fieldbackground=WHITE)
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=36,
                        fieldbackground=WHITE, background=WHITE, foreground=INK, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10), background="#edf2f8", padding=8)
        style.map("Treeview", background=[("selected", "#e0ebff")], foreground=[("selected", INK)])
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=26, pady=(24, 18))
        self.label(header, "雨课堂 · 选择题助手", 23, bold=True).pack(side="left")
        tk.Label(header, textvariable=self.badge, bg="#e5ecfa", fg="#3a5c9b",
                 padx=16, pady=7, font=("Microsoft YaHei UI", 10)).pack(side="right")
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=26)
        side = tk.Frame(body, bg=WHITE, padx=20, pady=18, width=335)
        side.pack(side="left", fill="y", padx=(0, 18))
        side.pack_propagate(False)
        self.label(side, "课堂设置", 14, bold=True).pack(anchor="w")
        self.label(side, "只处理单选、多选题", 10, MUTED).pack(anchor="w", pady=(3, 13))
        self.label(side, "课程编号", 10, MUTED).pack(anchor="w")
        self.course = tk.StringVar(value=DEFAULT_COURSE)
        entry = ttk.Entry(side, textvariable=self.course)
        entry.pack(fill="x", pady=(4, 8)); self.controls.append(entry)
        self.label(side, textvariable=self.login_text, color=MUTED, size=10).pack(anchor="w")
        self.login_btn = self.button(side, "微信扫码登录", self.login)
        self.login_btn.pack(fill="x", pady=(6, 14))
        self.label(side, "运行方式", 10, MUTED).pack(anchor="w")
        self.mode = tk.StringVar(value="只提醒")
        self.mode_box = ttk.Combobox(side, textvariable=self.mode, values=list(MODE_LABELS), state="readonly")
        self.mode_box.pack(fill="x", pady=(4, 8)); self.controls.append(self.mode_box)
        self.label(side, "开始监看会进入目标课堂，并可能登记签到。", 9, MUTED,
                   wraplength=285, justify="left").pack(anchor="w", pady=(0, 12))
        self.label(side, "AI 配置", 13, bold=True).pack(anchor="w")
        self.label(side, "稍后填写也可以；演示和提醒无需 Key。", 9, MUTED).pack(anchor="w", pady=(2, 8))
        self.base = tk.StringVar()
        self.model = tk.StringVar()
        self.key = tk.StringVar()
        for caption, var, show in [("API 基础地址", self.base, ""), ("模型名称", self.model, ""), ("API Key", self.key, "•")]:
            self.label(side, caption, 9, MUTED).pack(anchor="w")
            e = ttk.Entry(side, textvariable=var, show=show)
            e.pack(fill="x", pady=(3, 6)); self.controls.append(e)
        self.vision = tk.BooleanVar(value=False)
        self.vision_check = tk.Checkbutton(side, text="发送题目图片（需视觉模型）", variable=self.vision,
                                         bg=WHITE, activebackground=WHITE, fg=INK, font=("Microsoft YaHei UI", 9))
        self.vision_check.pack(anchor="w"); self.controls.append(self.vision_check)
        self.label(side, "Key 和登录状态仅保留到关闭程序。", 9, MUTED).pack(anchor="w", pady=(2, 7))
        config_buttons = tk.Frame(side, bg=WHITE); config_buttons.pack(fill="x")
        self.save_btn = self.button(config_buttons, "保存配置", self.save_config)
        self.save_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.test_btn = self.button(config_buttons, "测试 AI", self.test_ai)
        self.test_btn.pack(side="left", expand=True, fill="x")
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        hero = tk.Frame(right, bg="#e8efff", padx=20, pady=15)
        hero.pack(fill="x")
        self.label(hero, "题目出现时，让提醒更及时", 16, bold=True).pack(anchor="w")
        self.label(hero, "先看演示，再连接你的课堂。所有真实操作都有记录。", 10, "#516a93").pack(anchor="w", pady=(5, 0))
        actions = tk.Frame(right, bg=BG); actions.pack(fill="x", pady=15)
        self.start_btn = self.button(actions, "开始监看", self.start, True)
        self.start_btn.pack(side="left")
        self.demo_btn = self.button(actions, "试运行演示", self.start_demo)
        self.demo_btn.pack(side="left", padx=10)
        self.stop_btn = self.button(actions, "停止", self.stop)
        self.stop_btn.pack(side="left"); self.stop_btn.config(state="disabled")
        delay_row = tk.Frame(actions, bg=BG)
        delay_row.pack(side="right")
        self.label(delay_row, "提交前等待", 10, MUTED).pack(side="left", padx=(0, 6))
        self.delay = tk.StringVar(value="5")
        delay_box = ttk.Spinbox(delay_row, from_=0, to=30, width=4, textvariable=self.delay)
        delay_box.pack(side="left"); self.controls.append(delay_box)
        self.label(delay_row, "秒", 10, MUTED).pack(side="left", padx=(6, 0))
        cards = tk.Frame(right, bg=BG); cards.pack(fill="x", pady=(0, 15))
        for i, title in enumerate(["发现题目", "确认提交", "跳过 / 取消"]):
            c = tk.Frame(cards, bg=WHITE, padx=16, pady=12)
            c.pack(side="left", fill="x", expand=True, padx=(0 if i==0 else 8, 0))
            self.label(c, title, 10, MUTED).pack(anchor="w")
            self.label(c, textvariable=self.stats[i], size=24, bold=True).pack(anchor="w")
        table_card = tk.Frame(right, bg=WHITE, padx=16, pady=12)
        table_card.pack(fill="both", expand=True)
        self.label(table_card, textvariable=self.course_text, size=11, bold=True).pack(anchor="w", pady=(0, 10))
        tabs = ttk.Notebook(table_card); tabs.pack(fill="both", expand=True)
        tab = tk.Frame(tabs, bg=WHITE); logs = tk.Frame(tabs, bg=WHITE)
        tabs.add(tab, text="  题目记录  "); tabs.add(logs, text="  运行日志  ")
        table_wrap = tk.Frame(tab, bg=WHITE); table_wrap.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_wrap, columns=("time", "type", "title", "answer", "status"), show="headings", height=4)
        for name, text, width in [("time", "时间", 76), ("type", "题型", 65), ("title", "题目", 235), ("answer", "答案", 60), ("status", "状态", 110)]:
            self.table.heading(name, text=text)
            self.table.column(name, width=width, minwidth=45, stretch=name=="title")
        bar = ttk.Scrollbar(table_wrap, command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y"); self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_detail)
        self.detail = tk.Text(tab, height=4, bg="#f7f9fc", fg=INK, relief="flat", wrap="word",
                              font=("Microsoft YaHei UI", 10), padx=10, pady=8)
        self.detail.pack(fill="x", pady=(10, 0))
        self.set_detail("等待新题。点击“试运行演示”，可查看单选、多选及自动跳过的效果。")
        self.log_box = tk.Text(logs, bg=WHITE, fg=MUTED, relief="flat", wrap="word",
                               font=("Microsoft YaHei UI", 10), padx=10, pady=8, state="disabled")
        self.log_box.pack(fill="both", expand=True)
        footer = tk.Frame(self.root, bg=BG, padx=26, pady=14)
        footer.pack(side="bottom", fill="x", before=body)
        self.footer_label = self.label(footer, textvariable=self.status, size=10, color=MUTED, anchor="w")
        self.footer_label.pack(fill="x")

    def settings(self, require_ai=False):
        course = self.course.get().strip()
        if not course.isdigit():
            raise ValueError("课程编号应只包含数字")
        delay = self.delay_value()
        data = {"course_id": course, "mode": MODE_LABELS[self.mode.get()], "base_url": self.base.get().strip(),
                "model": self.model.get().strip(), "api_key": self.key.get().strip(), "vision": self.vision.get(),
                "submit_delay": delay}
        if require_ai:
            api_endpoint(data["base_url"])
            if not data["api_key"] or not data["model"]:
                raise ValueError("请先填写 API Key 和模型名称。现在也可以先使用演示或只提醒模式。")
        return data

    def delay_value(self):
        try:
            delay = int(self.delay.get())
            if not 0 <= delay <= 30:
                raise ValueError()
            return delay
        except ValueError:
            raise ValueError("提交延迟请填写 0–30 之间的整数秒数") from None

    def save_config(self):
        try:
            data = self.settings(); data.pop("api_key")
            # Start each launch in reminder mode, regardless of the last run.
            data.pop("mode")
            CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set("配置已保存；API Key 未写入文件")
        except (ValueError, OSError) as e:
            messagebox.showerror("无法保存", str(e), parent=self.root)

    def load_config(self):
        if CONFIG.exists():
            try:
                d = json.loads(CONFIG.read_text(encoding="utf-8"))
                self.course.set(d.get("course_id", DEFAULT_COURSE))
                self.base.set(d.get("base_url", "")); self.model.set(d.get("model", ""))
                self.vision.set(bool(d.get("vision", False)))
                self.delay.set(str(d.get("submit_delay", 5)))
            except (ValueError, OSError):
                self.status.set("配置读取失败，已使用默认设置")

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in (self.start_btn, self.demo_btn, self.login_btn, self.test_btn, self.save_btn):
            b.config(state=state)
        for c in self.controls:
            c.config(state=state)
        if not busy:
            self.mode_box.config(state="readonly")
        self.stop_btn.config(state="normal" if busy else "disabled")

    def dispatch(self, coroutine, kind):
        if self.future and not self.future.done():
            coroutine.close(); return
        self.worker_kind = kind
        self.set_busy(True)
        self.future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        def finished(f):
            try:
                f.result()
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                pass
            except Exception as exc:
                # Never show raw HTTP exception strings containing credentials or signed URLs.
                self.emit("error", str(exc) if isinstance(exc, ValueError) else "连接失败，请检查网络；接口也可能已变化")
            self.emit("done", kind)
        self.future.add_done_callback(finished)

    def login(self):
        self.is_demo = False; self.stop_event = threading.Event()
        self.badge.set("登录中")
        self.dispatch(self.client.login(self.stop_event), "login")

    def start(self):
        try:
            settings = self.settings(require_ai=self.mode.get() != "只提醒")
            if not self.client.logged_in:
                raise ValueError("请先点击微信扫码登录")
        except ValueError as e:
            messagebox.showinfo("还差一步", str(e), parent=self.root); return
        self.is_demo = False
        self.clear_rows()
        self.stop_event = threading.Event()
        self.badge.set("监看中")
        self.dispatch(self.client.monitor(settings, self.emit, self.stop_event, ROOT / "data" / "history.jsonl"), "monitor")

    def start_demo(self):
        try:
            delay = self.delay_value()
        except ValueError as e:
            messagebox.showinfo("检查延迟", str(e), parent=self.root); return
        self.is_demo = True
        self.clear_rows()
        self.course_text.set("演示课堂 / 内置示例 · 无真实提交")
        self.badge.set("演示模式")
        self.stop_event = threading.Event()
        self.dispatch(demo(self.emit, self.stop_event, delay), "demo")

    def test_ai(self):
        try:
            settings = self.settings(require_ai=True)
        except ValueError as e:
            messagebox.showinfo("配置未完成", str(e), parent=self.root); return
        self.is_demo = False; self.stop_event = threading.Event()
        async def test():
            self.emit("status", "正在测试 AI（发送一道简单文字题，会产生一次 API 调用）")
            problem = Problem("test", "1", 1, "1 + 1 = ?", [{"key": "A", "value": "2"}, {"key": "B", "value": "3"}])
            answer, _ = await asyncio.to_thread(solve, problem, settings)
            self.emit("status", "AI 文字接口测试成功 · 返回 " + ", ".join(answer) + "；图片能力尚未测试")
        self.dispatch(test(), "test")

    def stop(self):
        self.stop_event.set()
        self.status.set("正在停止；已发出的请求无法撤回")
        if self.qr_window and self.qr_window.winfo_exists():
            self.qr_window.destroy()
        # Don't cancel a synchronous submission midway and falsely claim it never happened.
        # Worker guards block any new submission and its loop exits within bounded time.

    def clear_rows(self):
        self.rows = {}
        self.table.delete(*self.table.get_children())
        for var in self.stats:
            var.set("0")
        self.set_detail("等待题目…")

    def set_detail(self, text):
        self.detail.config(state="normal"); self.detail.delete("1.0", "end")
        self.detail.insert("end", text); self.detail.config(state="disabled")

    def show_detail(self, event=None):
        selected = self.table.selection()
        if not selected:
            return
        d = self.rows[selected[0]]; p = d["problem"]
        text = p["body"] or "题目内容见课堂图片"
        text += "\n" + "  ·  ".join(o["key"] + ": " + o["value"] for o in p["options"])
        text += "\n" + d["detail"]
        self.set_detail(text)

    def log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S") + "  " + text + "\n")
        self.log_box.see("end"); self.log_box.config(state="disabled")

    def show_qr(self, link):
        if self.qr_window and self.qr_window.winfo_exists():
            self.qr_window.destroy()
        self.qr_window = tk.Toplevel(self.root)
        self.qr_window.title("微信扫码登录雨课堂")
        self.qr_window.configure(bg=WHITE)
        self.qr_window.transient(self.root)
        self.qr_window.protocol("WM_DELETE_WINDOW", self.stop)
        self.label(self.qr_window, "微信扫一扫", 17, bold=True).pack(padx=30, pady=(20, 8))
        img = qrcode.make(link).convert("RGB").resize((290, 290))
        self.qr_image = ImageTk.PhotoImage(img)
        tk.Label(self.qr_window, image=self.qr_image, bg=WHITE).pack(padx=25)
        self.label(self.qr_window, "请在手机确认登录；不会自动开始监看。", 10, MUTED).pack(padx=20, pady=20)

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.set(value); self.log(value)
            elif kind == "log":
                self.log(value)
            elif kind == "error":
                self.status.set(value); self.log(value); self.badge.set("需要检查")
            elif kind == "qr":
                self.show_qr(value)
            elif kind == "login":
                self.login_text.set("已登录雨课堂")
                self.badge.set("已登录")
                if self.qr_window and self.qr_window.winfo_exists():
                    self.qr_window.destroy()
            elif kind == "course":
                self.course_text.set(value + " / " + self.course.get())
            elif kind == "alert":
                if not self.is_demo:
                    self.root.bell()
                    try:
                        import ctypes
                        ctypes.windll.user32.FlashWindow(self.root.winfo_id(), True)
                    except Exception:
                        pass
            elif kind == "countdown":
                key = value["key"]
                if key in self.rows:
                    self.table.set(key, "status", f"等待 {value['seconds']} 秒")
            elif kind == "problem":
                p = value["problem"]; key = value["key"]
                state = value["status"]
                if self.is_demo and state == "已提交":
                    state = "模拟提交"
                values = (value["time"], {1:"单选", 2:"多选"}.get(p["type"], "其他"),
                          p["body"][:65], ",".join(value["answer"]), state)
                if key in self.rows:
                    self.table.item(key, values=values)
                else:
                    self.table.insert("", "end", iid=key, values=values)
                self.rows[key] = value
                self.stats[0].set(str(len(self.rows)))
                self.stats[1].set(str(sum(r["status"]=="已提交" for r in self.rows.values())))
                self.stats[2].set(str(sum(r["status"] in ("已跳过", "已取消") for r in self.rows.values())))
                self.table.selection_set(key); self.table.see(key); self.show_detail()
            elif kind == "done":
                self.set_busy(False)
                if self.stop_event.is_set():
                    self.status.set("已停止 · 不会继续处理新题")
                    self.badge.set("已停止")
                elif value == "demo":
                    self.badge.set("演示完成")
                elif value == "monitor":
                    self.badge.set("监看已停止")
                if value == "login" and self.qr_window and self.qr_window.winfo_exists():
                    self.qr_window.destroy()
        self.root.after(100, self.poll)

    def close(self):
        self.stop_event.set()
        self.root.destroy()
        self.loop.call_soon_threadsafe(self.loop.stop)


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    if "--demo" in sys.argv:
        root.after(500, app.start_demo)
    root.mainloop()
