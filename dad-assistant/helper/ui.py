"""The window: one big chat area, one text box, a few big buttons.

Tkinter is not thread-safe, so the agent runs in a worker thread and talks to the
window only through a queue that the window checks every 100 ms.
"""

import logging
import queue
import threading
import time
import tkinter as tk
import traceback
from tkinter import scrolledtext

from . import agent as agent_mod
from . import llm, textutil, tools, winapi

log = logging.getLogger("assistant.ui")

BG = "#F4F1EA"
CHAT_BG = "#FFFFFF"
USER_COLOR = "#1F4E8C"
ASSISTANT_COLOR = "#1E1E1E"
NOTICE_COLOR = "#6B6B6B"
ERROR_COLOR = "#A61B1B"
GREEN = "#2E7D32"
FONT = "Segoe UI"


class BigDialog(object):
    """A modal Yes/No (or OK) question with very large buttons."""

    def __init__(self, root, size, question, details=None, yes="Yes, go ahead", no="No"):
        self.result = False
        win = self.win = tk.Toplevel(root)
        win.title("Please choose")
        win.configure(bg=BG)
        win.transient(root)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._no)
        tk.Label(win, text=textutil.for_tk(question), font=(FONT, size + 3, "bold"), bg=BG, wraplength=640,
                 justify="left").pack(padx=24, pady=(22, 10), anchor="w")
        if details:
            shown = [textutil.for_tk(d) for d in details[:12]]
            if len(details) > 12:
                shown.append("... and %d more" % (len(details) - 12))
            tk.Label(win, text="\n".join(shown), font=(FONT, size - 3), bg=BG, fg="#444444", wraplength=640,
                     justify="left").pack(padx=24, pady=(0, 12), anchor="w")
        row = tk.Frame(win, bg=BG)
        row.pack(padx=24, pady=(6, 22), fill="x")
        tk.Button(row, text=yes, font=(FONT, size + 2, "bold"), bg=GREEN, fg="white", activebackground="#256628",
                  activeforeground="white", padx=26, pady=10, command=self._yes).pack(side="left")
        if no:
            tk.Button(row, text=no, font=(FONT, size + 2, "bold"), padx=26, pady=10,
                      command=self._no).pack(side="left", padx=(16, 0))
        win.update_idletasks()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_width()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - win.winfo_height()) // 3)
        win.geometry("+%d+%d" % (x, y))
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
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(top, text=self.cfg["assistant_name"], font=(FONT, s + 8, "bold"), bg=BG).pack(side="left")
        tk.Button(top, text="Text size  A-", font=(FONT, s - 2), command=lambda: self._zoom(-1)).pack(side="right")
        tk.Button(top, text="A+", font=(FONT, s - 2), command=lambda: self._zoom(1)).pack(side="right", padx=4)

        self.chat = scrolledtext.ScrolledText(self.root, wrap="word", font=(FONT, s), bg=CHAT_BG, relief="flat",
                                              padx=14, pady=10, state="disabled", cursor="arrow", height=10,
                                              width=20)
        self.chat.pack(fill="both", expand=True, padx=16, pady=6)
        self._style_tags()

        self.status = tk.Label(self.root, text="", font=(FONT, s - 1, "italic"), fg=NOTICE_COLOR, bg=BG, anchor="w")
        self.status.pack(fill="x", padx=18)

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=16, pady=(4, 6))
        # Pack the button first so the text box can never squeeze it off the screen.
        self.send_btn = tk.Button(bottom, text="Send", font=(FONT, s + 4, "bold"), bg=GREEN, fg="white",
                                  activebackground="#256628", activeforeground="white", padx=24,
                                  command=self._send)
        self.send_btn.pack(side="right", fill="y", padx=(10, 0))
        self.entry = tk.Text(bottom, height=3, width=20, wrap="word", font=(FONT, s + 1), relief="solid", bd=1,
                             undo=True)
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<KP_Enter>", self._on_enter)

        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.stop_btn = tk.Button(row, text="Stop", font=(FONT, s), padx=12, state="disabled", command=self._stop)
        self.stop_btn.pack(side="left")
        tk.Button(row, text="New conversation", font=(FONT, s), padx=12,
                  command=self._new_conversation).pack(side="left", padx=8)
        tk.Button(row, text="Undo last change", font=(FONT, s), padx=12, command=self._undo).pack(side="left")
        tk.Button(row, text="Ask Ahmed for help", font=(FONT, s), padx=12,
                  command=self._help).pack(side="right")
        self.entry.focus_set()

    def _style_tags(self):
        s = self.size
        self.chat.configure(font=(FONT, s))
        self.chat.tag_configure("who_user", font=(FONT, s, "bold"), foreground=USER_COLOR, spacing1=14)
        self.chat.tag_configure("who_assistant", font=(FONT, s, "bold"), foreground=GREEN, spacing1=14)
        self.chat.tag_configure("user", foreground=USER_COLOR, lmargin1=8, lmargin2=8, spacing3=4)
        self.chat.tag_configure("assistant", foreground=ASSISTANT_COLOR, lmargin1=8, lmargin2=8, spacing3=4)
        self.chat.tag_configure("notice", foreground=NOTICE_COLOR, font=(FONT, s - 1, "italic"), lmargin1=8)
        self.chat.tag_configure("activity", foreground=NOTICE_COLOR, font=(FONT, s - 2, "italic"), lmargin1=8)
        self.chat.tag_configure("error", foreground=ERROR_COLOR, font=(FONT, s, "bold"), lmargin1=8, spacing1=8)
        self.chat.tag_configure("rtl", justify="right", rmargin=8)

    def _zoom(self, delta):
        self.size = max(10, min(30, self.size + delta))
        self._style_tags()
        self.entry.configure(font=(FONT, self.size + 1))
        self.status.configure(font=(FONT, self.size - 1, "italic"))

    # ------------------------------------------------------------------ chat output

    def _write(self, text, tag, who=None):
        text = textutil.for_tk(text)
        self.chat.configure(state="normal")
        try:
            if who:
                self.chat.insert("end", "\n%s\n" % who, "who_" + tag)
            for paragraph in text.split("\n"):
                tags = (tag, "rtl") if textutil.is_rtl(paragraph) else (tag,)
                self.chat.insert("end", paragraph + "\n", tags)
        finally:
            self.chat.configure(state="disabled")
        self.chat.see("end")

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
        hello = "Hello%s! I'm %s, your computer helper." % (" " + name if name else "", self.cfg["assistant_name"])
        self._write(hello + "\nType what you need in the box at the bottom and press Send (or the Enter key).\n"
                    "For example: \"Tidy up my desktop\", \"Find my photos from last summer\" or \"What is "
                    "the weather like in spring in Cairo?\"", "assistant", self.cfg["assistant_name"])
        if problems or self.agent is None:
            self._write("I am not completely set up yet, so I may not be able to answer. Ahmed has been "
                        "sent a message about it.", "error")

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
            self.status.configure(text="Please wait, I'm still working on the last request (or press Stop).")
            return
        if self.agent is None:
            self._write(text, "user", "You")
            self._write("Sorry, I am not set up yet. Please use \"Ask Ahmed for help\".", "error")
            self.entry.delete("1.0", "end")
            return
        self.entry.delete("1.0", "end")
        self._write(text, "user", "You")
        self._set_busy(True)
        self.cancel = threading.Event()
        threading.Thread(target=self._worker, args=(text, self.cancel), name="agent", daemon=True).start()

    def _worker(self, text, cancel):
        try:
            winapi_init_com()
            self.agent.handle(text, cancel)
        except Exception:
            log.exception("Worker crashed")
            self._emit("error", "Sorry, something went wrong. Please try again.")
        finally:
            self.events.put(("done", None))

    def _set_busy(self, busy):
        self.busy = busy
        self.send_btn.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.status.configure(text="Thinking..." if busy else "")
        self._busy_since = time.time()

    def _stop(self):
        self.cancel.set()
        self.status.configure(text="Stopping...")

    def _new_conversation(self):
        if self.busy:
            self.status.configure(text="Please press Stop first.")
            return
        if self.agent is not None:
            self.agent.new_conversation()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._greet([])

    def _undo(self):
        if self.busy:
            self.status.configure(text="Please wait until I have finished.")
            return
        batch = self.runner.undo.peek()
        if not batch:
            BigDialog(self.root, self.size, "There is nothing to undo.", yes="OK", no=None).wait()
            return
        self.cancel = threading.Event()
        self._set_busy(True)
        self.status.configure(text="Undoing...")

        def work():
            try:
                winapi_init_com()
                result, _err = self.runner.run("undo_last_change", {})
                self._emit("notice", result)
                if self.agent is not None and not result.startswith("The user said NO"):
                    # Let the model know, so it does not get confused later.
                    self.agent.messages.append({"role": "user", "content": [
                        {"type": "text", "text": "(Note from the app: the user pressed the Undo button. %s)" % result}]})
            finally:
                self.events.put(("done", None))
        threading.Thread(target=work, name="undo", daemon=True).start()

    def _help(self):
        win = tk.Toplevel(self.root)
        win.title("Ask Ahmed for help")
        win.configure(bg=BG)
        win.transient(self.root)
        s = self.size
        tk.Label(win, text="Is something not working? Write a short note for Ahmed (you can leave it empty).",
                 font=(FONT, s + 1, "bold"), bg=BG, wraplength=560, justify="left").pack(padx=20, pady=(18, 8))
        box = tk.Text(win, height=5, width=50, wrap="word", font=(FONT, s))
        box.pack(padx=20, fill="both", expand=True)
        box.focus_set()

        def send():
            note = box.get("1.0", "end").strip()
            win.destroy()
            self._write("Sending a message to Ahmed...", "notice")
            body = "Dad pressed \"Ask Ahmed for help\".\n\nHis note:\n%s" % (note or "(no note)")
            self.reporter.send_async("Dad asked for help", body, attach_log=True,
                                     done=lambda ok: self._emit("notice", "Your message was sent to Ahmed."
                                                                if ok else "I couldn't send it right now; "
                                                                "I will send it automatically later."))
        row = tk.Frame(win, bg=BG)
        row.pack(padx=20, pady=16, fill="x")
        tk.Button(row, text="Send to Ahmed", font=(FONT, s + 2, "bold"), bg=GREEN, fg="white", padx=20,
                  command=send).pack(side="left")
        tk.Button(row, text="Cancel", font=(FONT, s + 2), padx=20, command=win.destroy).pack(side="left", padx=12)
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
        if self.busy:
            dots = "." * (1 + int(time.time() * 2) % 3)
            current = self.status.cget("text").rstrip(".")
            if current:
                self.status.configure(text=current + dots)
        self.root.after(100, self._poll)

    def _handle_event(self, kind, value):
        if kind == "text":
            self._write(textutil.strip_markdown(value), "assistant", self.cfg["assistant_name"])
        elif kind == "activity":
            self.status.configure(text=value.rstrip("."))
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
            self._write("Sorry, something went wrong. Please try again.", "error")
            if self.busy and not any(t.name in ("agent", "undo") and t.is_alive() for t in threading.enumerate()):
                self._set_busy(False)
        except Exception:
            pass

    def _close(self):
        if self.busy:
            if not BigDialog(self.root, self.size, "I am still working. Close anyway?", yes="Yes, close",
                             no="No, keep working").wait():
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
