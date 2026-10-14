"""Text clean-up for display in Tkinter on Windows 7."""

import re

# Tk 8.6.9 (bundled with Python 3.8) cannot display characters outside the Basic
# Multilingual Plane: inserting an emoji raises TclError.  Strip them everywhere.
_NON_BMP = re.compile(" ?[\U00010000-\U0010FFFF]+")
_ARABIC = re.compile("[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]")
_LATIN = re.compile("[A-Za-z]")


def for_tk(text):
    return _NON_BMP.sub("", str(text or "")).replace("\r\n", "\n").replace("\x00", "")


def strip_markdown(text):
    text = re.sub(r"```[a-zA-Z0-9]*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "- ", text)
    return text


def is_rtl(text):
    return len(_ARABIC.findall(text)) > len(_LATIN.findall(text))


def human_size(num):
    num = float(num or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return ("%d %s" % (num, unit)) if unit == "bytes" else ("%.1f %s" % (num, unit))
        num /= 1024.0
    return "%.1f GB" % num


def rtl_visual_lines(paragraph, max_width, measure):
    """Lay out a right-to-left paragraph for Tk.

    Tk 8.6 draws the words of a line from left to right, so Arabic sentences come out in reverse word
    order (each word itself is shaped correctly).  We wrap the paragraph ourselves to `max_width`
    pixels (using `measure(text) -> pixels`) and reverse the word order of every line.
    """
    words = paragraph.split()
    if not words:
        return [paragraph]
    lines, current = [], []
    for word in words:
        candidate = current + [word]
        if current and measure(" ".join(candidate)) > max_width:
            lines.append(current)
            current = [word]
        else:
            current = candidate
    lines.append(current)
    return [" ".join(reversed(line)) for line in lines]
