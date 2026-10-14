"""The window: one big chat area, one text box, a few big buttons.

Tkinter is not thread-safe, so the agent runs in a worker thread and talks to the
window only through a queue that the window checks every 100 ms.
"""

import logging
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from tkinter import scrolledtext

from . import agent as agent_mod
from . import llm, textutil, tools, winapi
from . import strings as S

log = logging.getLogger("assistant.ui")

BG = "#F4F1EA"
CHAT_BG = "#FFFFFF"
USER_COLOR = "#1F4E8C"
ASSISTANT_COLOR = "#1E1E1E"
NOTICE_COLOR = "#6B6B6B"
ERROR_COLOR = "#A61B1B"
GREEN = "#2E7D32"
FONT = "Tahoma"  # clear Arabic letters on every Windows 7 laptop


def _center(win, root):
    """Place a dialog in the middle of the main window."""
    win.update_idletasks()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_reqwidth()) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry("+%d+%d" % (x, y))


class BigDialog(object):
    """A modal Yes/No (or OK) question with very large buttons."""

    def __init__(self, root, size, question, details=None, yes=S.YES_GO_AHEAD, no=S.NO):
        self.result = False
        win = self.win = tk.Toplevel(root)
        win.title(S.PLEASE_CHOOSE)
        win.configure(bg=BG)
        win.transient(root)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._no)
        qfont = (FONT, size + 3, "bold")
        question = textutil.for_tk(question)
        if textutil.is_rtl(question):
            measure = tkfont.Font(root=root, font=qfont).measure
            tk.Label(win, text="\n".join(textutil.rtl_visual_lines(question, 620, measure)), font=qfont, bg=BG,
                     justify="right").pack(padx=24, pady=(22, 10), anchor="e")
        else:
            tk.Label(win, text=question, font=qfont, bg=BG, wraplength=640,
                     justify="left").pack(padx=24, pady=(22, 10), anchor="w")
        if details:
            shown = []
            dfont = tkfont.Font(root=root, font=(FONT, size - 3))
            for d in details[:12]:
                d = textutil.for_tk(d)
                shown.extend(textutil.rtl_visual_lines(d, 620, dfont.measure) if textutil.is_rtl(d) else [d])
            if len(details) > 12:
                shown.append(textutil.rtl(S.AND_MORE.format(n=len(details) - 12)))
            tk.Label(win, text="\n".join(shown), font=(FONT, size - 3), bg=BG, fg="#444444", wraplength=640,
                     justify="right").pack(padx=24, pady=(0, 12), anchor="e")
        # Right to left: the first choice ("Yes") is on the right.
        row = tk.Frame(win, bg=BG)
        row.pack(padx=24, pady=(6, 22), fill="x")
        tk.Button(row, text=textutil.rtl(yes), font=(FONT, size + 2, "bold"), bg=GREEN, fg="white",
                  activebackground="#256628", activeforeground="white", padx=26, pady=10,
                  command=self._yes).pack(side="right")
        if no:
            tk.Button(row, text=textutil.rtl(no), font=(FONT, size + 2, "bold"), padx=26, pady=10,
                      command=self._no).pack(side="right", padx=(0, 16))
        _center(win, root)
        win.lift()
        win.focus_force()
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def _yes(self):
        self.result = True
        self.win.destroy()

    def _no(self):
        self.result = False
        self.win.destroy()

    def wait(self):
        self.win.wait_window()
        return self.result


class App(object):
    def __init__(self, cfg, config_problems, reporter):
        self.cfg = cfg
        self.reporter = reporter
        self.size = cfg["font_size"]
        textutil.REORDER = bool(cfg.get("arabic_word_order_fix", False))
        self._status_text = ""
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.images = []  # keep PhotoImage objects alive

        self.root = tk.Tk()
        self.root.title(cfg["assistant_name"])
        self.root.configure(bg=BG)
        self.root.geometry("980x720")
        self.root.minsize(640, 480)
        self.root.report_callback_exception = self._tk_error
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._build()

        self.runner = tools.ToolRunner(cfg, self._confirm_from_worker, self._notify_from_worker)
        self.agent = None
        try:
            client = llm.make_llm(cfg)
            self.agent = agent_mod.Agent(cfg, client, self.runner, self._emit, reporter)
        except llm.ConfigError as e:
            config_problems = list(config_problems) + [str(e)]
        if config_problems:
            log.error("Configuration problems: %s", config_problems)
            reporter.report_error("Configuration problem", "\n".join(config_problems))
        self._greet(config_problems)
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ layout

    def _build(self):
        s = self.size
        # Everything is laid out right to left, like an Arabic page.
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(top, text=textutil.rtl(self.cfg["assistant_name"]), font=(FONT, s + 8, "bold"),
                 bg=BG).pack(side="right")
        tk.Button(top, text="A-", font=(FONT, s - 2), command=lambda: self._zoom(-1)).pack(side="left")
        tk.Button(top, text="A+", font=(FONT, s - 2), command=lambda: self._zoom(1)).pack(side="left", padx=4)
        tk.Label(top, text=textutil.rtl(S.TEXT_SIZE), font=(FONT, s - 2), bg=BG).pack(side="left", padx=4)

        self.chat = scrolledtext.ScrolledText(self.root, wrap="word", font=(FONT, s), bg=CHAT_BG, relief="flat",
                                              padx=14, pady=10, state="disabled", cursor="arrow", height=10,
                                              width=20)
        self.chat.pack(fill="both", expand=True, padx=16, pady=6)
        self._style_tags()

        self.status = tk.Label(self.root, text="", font=(FONT, s - 1, "italic"), fg=NOTICE_COLOR, bg=BG, anchor="e")
        self.status.pack(fill="x", padx=18)

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=16, pady=(4, 6))
        # Pack the button first so the text box can never squeeze it off the screen.
        self.send_btn = tk.Button(bottom, text=textutil.rtl(S.SEND), font=(FONT, s + 4, "bold"), bg=GREEN,
                                  fg="white", activebackground="#256628", activeforeground="white", padx=24,
                                  command=self._send)
        self.send_btn.pack(side="left", fill="y", padx=(0, 10))
        self.entry = tk.Text(bottom, height=3, width=20, wrap="word", font=(FONT, s + 1), relief="solid", bd=1,
                             undo=True)
        self.entry.tag_configure("rtl", justify="right")
        self.entry.pack(side="right", fill="both", expand=True)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<KP_Enter>", self._on_enter)
        self.entry.bind("<KeyRelease>", lambda e: self.entry.tag_add("rtl", "1.0", "end"))

        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.stop_btn = tk.Button(row, text=textutil.rtl(S.STOP), font=(FONT, s), padx=12, state="disabled",
                                  command=self._stop)
        self.stop_btn.pack(side="right")
        tk.Button(row, text=textutil.rtl(S.NEW_CONVERSATION), font=(FONT, s), padx=12,
                  command=self._new_conversation).pack(side="right", padx=8)
        tk.Button(row, text=textutil.rtl(S.UNDO), font=(FONT, s), padx=12, command=self._undo).pack(side="right")
        tk.Button(row, text=textutil.rtl(S.ASK_AHMED), font=(FONT, s), padx=12,
                  command=self._help).pack(side="left")
        self.entry.focus_set()

    def _style_tags(self):
        s = self.size
        self.chat.configure(font=(FONT, s))
        self.chat.tag_configure("who_user", font=(FONT, s, "bold"), foreground=USER_COLOR, spacing1=14)
        self.chat.tag_configure("who_assistant", font=(FONT, s, "bold"), foreground=GREEN, spacing1=14)
        self.chat.tag_configure("user", foreground=USER_COLOR, lmargin1=8, lmargin2=8, spacing3=4)
        self.chat.tag_configure("assistant", foreground=ASSISTANT_COLOR, lmargin1=8, lmargin2=8, spacing3=4)
        self.chat.tag_configure("notice", foreground=NOTICE_COLOR, font=(FONT, s - 1, "italic"), lmargin1=8,
                                lmargin2=8)
        self.chat.tag_configure("activity", foreground=NOTICE_COLOR, font=(FONT, s - 2, "italic"), lmargin1=8,
                                lmargin2=8)
        self.chat.tag_configure("error", foreground=ERROR_COLOR, font=(FONT, s, "bold"), lmargin1=8, lmargin2=8,
                                spacing1=8)
        self.chat.tag_configure("rtl", justify="right", rmargin=8)

    def _zoom(self, delta):
        self.size = max(10, min(30, self.size + delta))
        self._style_tags()
        self.entry.configure(font=(FONT, self.size + 1))
        self.status.configure(font=(FONT, self.size - 1, "italic"))
        self.entry.tag_configure("rtl", justify="right")

    # ------------------------------------------------------------------ chat output

    def _write(self, text, tag, who=None):
        text = textutil.for_tk(text)
        self.chat.configure(state="normal")
        try:
            if who:
                if textutil.is_rtl(who):
                    self.chat.insert("end", "\n%s\n" % textutil.rtl(who), ("who_" + tag, "rtl"))
                else:
                    self.chat.insert("end", "\n%s\n" % who, "who_" + tag)
            for paragraph in text.split("\n"):
                if textutil.is_rtl(paragraph):
                    for line in self._rtl_lines(paragraph, tag):
                        self.chat.insert("end", line + "\n", (tag, "rtl"))
                else:
                    self.chat.insert("end", paragraph + "\n", (tag,))
        finally:
            self.chat.configure(state="disabled")
        self.chat.see("end")

    def _rtl_lines(self, paragraph, tag):
        try:
            font = tkfont.Font(root=self.root, font=self.chat.tag_cget(tag, "font") or self.chat.cget("font"))
            width = max(200, self.chat.winfo_width() - 60)
            if width <= 200:
                width = 800  # window not drawn yet
            return textutil.rtl_visual_lines(paragraph, width, font.measure)
        except Exception:
            log.exception("RTL layout failed")
            return [paragraph]

    def _show_image(self, path):
        try:
            img = tk.PhotoImage(file=path)
            factor = max(1, int(max(img.width(), img.height()) / 380) + 1)
            if factor > 1:
                img = img.subsample(factor, factor)
            self.images.append(img)
            self.chat.configure(state="normal")
            self.chat.image_create("end", image=img, padx=10, pady=6)
            self.chat.insert("end", "\n")
            self.chat.configure(state="disabled")
            self.chat.see("end")
        except Exception as e:
            log.warning("Could not show image in chat (%s); opening it instead", e)
            try:
                winapi.open_with_default_app(path)
            except Exception:
                pass

    def _greet(self, problems):
        name = self.cfg["user_name"]
        hello = S.GREETING.format(name=S.GREETING_NAME.format(name=name) if name else "", assistant=self.cfg["assistant_name"])
        self._write(hello + "\n" + S.GREETING_HOW, "assistant", self.cfg["assistant_name"])
        if problems or self.agent is None:
            told = S.AHMED_WAS_TOLD if self.reporter.configured() else S.TELL_AHMED_TO_FINISH
            self._write(S.NOT_SET_UP + " " + told, "error")

    # ------------------------------------------------------------------ input

    def _on_enter(self, event):
        if event.state & 0x0001:  # Shift+Enter makes a new line
            return None
        self._send()
        return "break"

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        if self.busy:
            self._set_status(S.STATUS_WAIT_LAST)
            return
        if self.agent is None:
            self._write(text, "user", S.YOU)
            self._write(S.NOT_SET_UP_SHORT, "error")
            self.entry.delete("1.0", "end")
            return
        self.entry.delete("1.0", "end")
        self._write(text, "user", S.YOU)
        self._set_busy(True)
        self.cancel = threading.Event()
        threading.Thread(target=self._worker, args=(text, self.cancel), name="agent", daemon=True).start()

    def _worker(self, text, cancel):
        try:
            winapi_init_com()
            self.agent.handle(text, cancel)
        except Exception:
            log.exception("Worker crashed")
            self._emit("error", S.SOMETHING_WRONG)
        finally:
            self.events.put(("done", None))

    def _set_status(self, text):
        """Show a status line; while busy, _poll adds moving dots at its end."""
        self._status_text = (text or "").rstrip(". ")
        self.status.configure(text=textutil.rtl(self._status_text))

    def _set_busy(self, busy):
        self.busy = busy
        self.send_btn.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self._set_status(S.STATUS_THINKING if busy else "")
        self._busy_since = time.time()

    def _stop(self):
        self.cancel.set()
        self._set_status(S.STATUS_STOPPING)

    def _new_conversation(self):
        if self.busy:
            self._set_status(S.STATUS_PRESS_STOP_FIRST)
            return
        if self.agent is not None:
            self.agent.new_conversation()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._greet([])

    def _undo(self):
        if self.busy:
            self._set_status(S.STATUS_WAIT_FINISH)
            return
        batch = self.runner.undo.peek()
        if not batch:
            BigDialog(self.root, self.size, S.NOTHING_TO_UNDO, yes=S.OK, no=None).wait()
            return
        self.cancel = threading.Event()
        self._set_busy(True)
        self._set_status(S.STATUS_UNDOING)

        def work():
            try:
                winapi_init_com()
                self.runner.last_undo = None
                result, _err = self.runner.run("undo_last_change", {})
                outcome = self.runner.last_undo
                if outcome is None:
                    self._emit("notice", S.UNDO_CANCELLED if result.startswith("The user said NO")
                               else S.SOMETHING_WRONG)
                else:
                    restored, problems = outcome
                    text = S.UNDO_DONE.format(n=restored)
                    if problems:
                        text += " " + S.UNDO_SOME_PROBLEMS.format(n=len(problems))
                    self._emit("notice", text)
                if self.agent is not None and not result.startswith("The user said NO"):
                    # Let the model know, so it does not get confused later.
                    self.agent.messages.append({"role": "user", "content": [
                        {"type": "text", "text": "(Note from the app: the user pressed the Undo button. %s)" % result}]})
            finally:
                self.events.put(("done", None))
        threading.Thread(target=work, name="undo", daemon=True).start()

    def _help(self):
        win = tk.Toplevel(self.root)
        win.title(S.HELP_TITLE)
        win.configure(bg=BG)
        win.transient(self.root)
        s = self.size
        pfont = (FONT, s + 1, "bold")
        measure = tkfont.Font(root=self.root, font=pfont).measure
        tk.Label(win, text="\n".join(textutil.rtl_visual_lines(S.HELP_PROMPT, 540, measure)), font=pfont, bg=BG,
                 justify="right").pack(padx=20, pady=(18, 8), anchor="e")
        box = tk.Text(win, height=5, width=50, wrap="word", font=(FONT, s))
        box.tag_configure("rtl", justify="right")
        box.bind("<KeyRelease>", lambda e: box.tag_add("rtl", "1.0", "end"))
        box.pack(padx=20, fill="both", expand=True)

        def send():
            note = box.get("1.0", "end").strip()
            win.destroy()
            if not self.reporter.configured():
                self._write(S.HELP_NOT_CONFIGURED, "error")
                return
            self._write(S.HELP_SENDING, "notice")
            body = "Dad pressed \"Ask Ahmed for help\".\n\nHis note:\n%s" % (note or "(no note)")
            self.reporter.send_async("Dad asked for help", body, attach_log=True,
                                     done=lambda ok: self._emit("notice", S.HELP_SENT if ok else S.HELP_QUEUED))
        row = tk.Frame(win, bg=BG)
        row.pack(padx=20, pady=16, fill="x")
        tk.Button(row, text=textutil.rtl(S.HELP_SEND), font=(FONT, s + 2, "bold"), bg=GREEN, fg="white", padx=20,
                  command=send).pack(side="right")
        tk.Button(row, text=textutil.rtl(S.CANCEL), font=(FONT, s + 2), padx=20,
                  command=win.destroy).pack(side="right", padx=12)
        _center(win, self.root)
        box.focus_set()
        try:
            win.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ worker -> window

    def _emit(self, kind, value):
        self.events.put((kind, value))

    def _notify_from_worker(self, kind, value):
        self.events.put((kind, value))

    def _confirm_from_worker(self, question, details):
        """Called in the worker thread; blocks until the user answers in the window."""
        done = threading.Event()
        holder = {"answer": False}
        self.events.put(("confirm", (question, details, holder, done)))
        while not done.wait(0.5):
            if self.cancel.is_set():
                return False
        return holder["answer"]

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self._handle_event(kind, value)
        except queue.Empty:
            pass
        except Exception:
            log.exception("Error while updating the window")
        if self.busy and self._status_text:
            dots = "." * (1 + int(time.time() * 2) % 3)
            self.status.configure(text=textutil.rtl(self._status_text) + dots)
        self.root.after(100, self._poll)

    def _handle_event(self, kind, value):
        if kind == "text":
            self._write(textutil.strip_markdown(value), "assistant", self.cfg["assistant_name"])
        elif kind == "activity":
            self._set_status(value)
            self._write(value, "activity")
        elif kind == "notice":
            self._write(value, "notice")
        elif kind == "error":
            self._write(value, "error")
        elif kind == "image":
            self._show_image(value)
        elif kind == "confirm":
            question, details, holder, done = value
            try:
                holder["answer"] = BigDialog(self.root, self.size, question, details).wait()
            finally:
                done.set()
        elif kind == "done":
            self._set_busy(False)
            self.entry.focus_set()

    # ------------------------------------------------------------------ errors and closing

    def _tk_error(self, exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Window error:\n%s", details)
        self.reporter.report_error("Window error: %s" % exc, details)
        try:
            self._write(S.SOMETHING_WRONG, "error")
            if self.busy and not any(t.name in ("agent", "undo") and t.is_alive() for t in threading.enumerate()):
                self._set_busy(False)
        except Exception:
            pass

    def _close(self):
        if self.busy:
            if not BigDialog(self.root, self.size, S.STILL_WORKING_CLOSE, yes=S.YES_CLOSE,
                             no=S.NO_KEEP_WORKING).wait():
                return
            self.cancel.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def winapi_init_com():
    """ShellExecute and the Recycle Bin API want COM initialised on the calling thread."""
    if winapi.IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2 | 0x4)  # APARTMENTTHREADED | DISABLE_OLE1DDE
        except Exception:
            pass
