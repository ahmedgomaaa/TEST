"""The box Dad types in.

Tk's own text box treats every line as left-to-right: with Arabic the caret is drawn on the wrong side,
clicks and the arrow keys go to unexpected places, so letters end up in the wrong spot.  On Windows we
therefore put a real Windows edit box (the same one Notepad uses) inside the Tk window, set to
right-to-left.  Caret, selection, Backspace, mouse and the Arabic keyboard then work as Dad expects.

Enter sends (Shift+Enter makes a new line), Ctrl+A selects everything.  If the Windows box cannot be
created (or not on Windows), a plain Tk text box with the same methods is used instead.
"""

import ctypes
import logging
import sys
import tkinter as tk

log = logging.getLogger("assistant.textbox")

IS_WINDOWS = sys.platform == "win32"

WM_SETFONT = 0x0030
WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_KILLFOCUS = 0x0008
EM_SETSEL = 0x00B1
EM_SCROLLCARET = 0x00B7
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
GWL_WNDPROC = -4

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
WS_TABSTOP = 0x00010000
ES_MULTILINE = 0x0004
ES_RIGHT = 0x0002
ES_AUTOVSCROLL = 0x0040
ES_WANTRETURN = 0x1000
WS_EX_CLIENTEDGE = 0x00000200
WS_EX_RIGHT = 0x00001000
WS_EX_RTLREADING = 0x00002000
WS_EX_LEFTSCROLLBAR = 0x00004000


class TkBox(object):
    """Fallback: Tk's text box, right-aligned."""

    native = False

    def __init__(self, parent, font, on_enter, height=3):
        self.widget = tk.Text(parent, height=height, width=20, wrap="word", font=font, relief="solid", bd=1,
                              undo=True)
        self.widget.tag_configure("rtl", justify="right")
        self.widget.bind("<KeyRelease>", lambda e: self.widget.tag_add("rtl", "1.0", "end"))
        self._on_enter = on_enter
        if on_enter:
            self.widget.bind("<Return>", self._enter)
            self.widget.bind("<KP_Enter>", self._enter)

    def _enter(self, event):
        if event.state & 0x0001:  # Shift+Enter makes a new line
            return None
        self._on_enter()
        return "break"

    def pack(self, **kw):
        self.widget.pack(**kw)

    def get(self):
        return self.widget.get("1.0", "end").strip()

    def clear(self):
        self.widget.delete("1.0", "end")

    def set(self, text):
        self.clear()
        self.widget.insert("1.0", text, "rtl")

    def focus(self):
        self.widget.focus_set()

    def set_font(self, font):
        self.widget.configure(font=font)

    def poll(self):
        pass


class NativeBox(object):
    """A Windows EDIT control inside a Tk frame."""

    native = True

    def __init__(self, parent, font, on_enter, height=3):
        self._on_enter = on_enter
        self._enter_pressed = False
        self._font_handle = None
        u = self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        g = self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        HWND, UINT, WPARAM, LPARAM = ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        self._WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, HWND, UINT, WPARAM, LPARAM)
        u.CreateWindowExW.restype = HWND
        u.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, HWND, UINT, WPARAM, LPARAM]
        self._set_long = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW  # 32-bit has no ...Ptr
        self._set_long.restype = ctypes.c_void_p
        self._set_long.argtypes = [HWND, ctypes.c_int, ctypes.c_void_p]
        u.MoveWindow.argtypes = [HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        u.SetFocus.argtypes = [HWND]
        u.SetFocus.restype = HWND
        u.GetWindowTextLengthW.argtypes = [HWND]
        u.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
        u.SetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p]
        u.GetKeyState.restype = ctypes.c_short
        g.CreateFontW.restype = ctypes.c_void_p
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        k.GetModuleHandleW.restype = ctypes.c_void_p

        # Tk draws nothing in this frame; the Windows box covers it.
        self.widget = tk.Frame(parent, height=height * 34, width=200, bg="white", bd=0, highlightthickness=0)
        self.widget.update_idletasks()
        style = WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_TABSTOP | ES_MULTILINE | ES_RIGHT | ES_AUTOVSCROLL | \
            ES_WANTRETURN
        exstyle = WS_EX_CLIENTEDGE | WS_EX_RIGHT | WS_EX_RTLREADING | WS_EX_LEFTSCROLLBAR
        self.hwnd = u.CreateWindowExW(exstyle, "EDIT", "", style, 0, 0, 200, height * 34,
                                      self.widget.winfo_id(), None, k.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed (%d)" % ctypes.get_last_error())
        self._proc = self._WNDPROC(self._wndproc)  # kept alive for as long as the box exists
        self._old_proc = self._set_long(self.hwnd, GWL_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        if not self._old_proc:
            raise OSError("subclassing the edit box failed (%d)" % ctypes.get_last_error())
        self.set_font(font)
        self.widget.bind("<Configure>", self._resize)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            u = self._user32
            sends = self._on_enter is not None
            if sends and msg == WM_KEYDOWN and wparam == VK_RETURN and not (u.GetKeyState(VK_SHIFT) & 0x8000):
                self._enter_pressed = True  # handled from Tk's own loop (poll), never inside this callback
                return 0
            if sends and msg == WM_CHAR and wparam in (13, 10) and not (u.GetKeyState(VK_SHIFT) & 0x8000):
                return 0  # no new line and no beep for plain Enter
            if msg == WM_KEYDOWN and wparam == 0x41 and (u.GetKeyState(VK_CONTROL) & 0x8000):
                u.SendMessageW(hwnd, EM_SETSEL, 0, -1)  # Ctrl+A: Windows 7 edit boxes do not do this
                return 0
            if msg == WM_CHAR and wparam == 1:  # the Ctrl+A character itself: no beep
                return 0
        except Exception:
            log.exception("Text box message handler failed")
        return u.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def _resize(self, event):
        self._user32.MoveWindow(self.hwnd, 0, 0, max(10, event.width), max(10, event.height), 1)

    def poll(self):
        """Called by the window every 100 ms: run Enter outside the Windows message handler."""
        if self._enter_pressed:
            self._enter_pressed = False
            if self._on_enter:
                self._on_enter()

    def pack(self, **kw):
        self.widget.pack(**kw)

    def get(self):
        u = self._user32
        n = u.GetWindowTextLengthW(self.hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(self.hwnd, buf, n + 1)
        return buf.value.replace("\r\n", "\n").strip()

    def clear(self):
        self._user32.SetWindowTextW(self.hwnd, "")

    def set(self, text):
        self._user32.SetWindowTextW(self.hwnd, text.replace("\n", "\r\n"))
        self._user32.SendMessageW(self.hwnd, EM_SETSEL, 0, -1)  # selected, so typing replaces it

    def focus(self):
        self._user32.SetFocus(self.hwnd)

    def set_font(self, font):
        family, size = font[0], font[1]
        try:
            dpi = self.widget.winfo_fpixels("1i")
        except tk.TclError:
            dpi = 96.0
        handle = self._gdi32.CreateFontW(-int(round(size * dpi / 72.0)), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0,
                                         family)
        if handle:
            self._user32.SendMessageW(self.hwnd, WM_SETFONT, handle, 1)
            if self._font_handle:
                self._gdi32.DeleteObject(self._font_handle)
            self._font_handle = handle


def make(parent, font, on_enter=None, height=3):
    """The best text box for this computer."""
    if IS_WINDOWS:
        before = set(parent.winfo_children())
        try:
            return NativeBox(parent, font, on_enter, height)
        except Exception as e:
            log.warning("Windows text box not available (%s); using Tk's", e)
            for child in set(parent.winfo_children()) - before:
                child.destroy()
    return TkBox(parent, font, on_enter, height)
