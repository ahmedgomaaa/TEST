"""Text clean-up for display in Tkinter on Windows 7."""

import re
import unicodedata

# Tk 8.6.9 (bundled with Python 3.8) cannot display characters outside the Basic
# Multilingual Plane: inserting an emoji raises TclError.  Strip them everywhere.
_NON_BMP = re.compile(" ?[\U00010000-\U0010FFFF]+")
_ARABIC = re.compile("[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]")
_LATIN = re.compile("[A-Za-z]")

# Tk 8.6 draws the words of a line from left to right, so Arabic comes out in reverse word order.
# When True we reverse the words ourselves.  Set from config "arabic_word_order_fix" at startup (the
# installer asks which version reads correctly on the laptop).
REORDER = False


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
    """Arabic if it has more Arabic than Latin letters, or if its first letter is Arabic."""
    arabic = len(_ARABIC.findall(text))
    if not arabic:
        return False
    if arabic > len(_LATIN.findall(text)):
        return True
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


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
    if not REORDER:
        return [rtl_display(" ".join(line)) for line in lines]
    return [" ".join(reversed(line)) for line in lines]


# Characters that decide direction (letters, digits) or belong to one (combining marks such as tanween).
_STRONG = {"L", "R", "AL", "EN", "AN", "NSM"}
_MIRROR = str.maketrans("()[]{}<>\u00ab\u00bb", ")(][}{><\u00bb\u00ab")


def _is_strong(ch):
    return unicodedata.bidirectional(ch) in _STRONG


def rtl_display(line):
    """Arrange one Arabic line so that it reads right to left when Windows draws it as a left-to-right line.

    Tk cannot ask Windows for a right-to-left base direction, so Windows treats each line as left-to-right
    text that contains Arabic.  The Arabic itself comes out fine, but punctuation at the ends lands on the
    wrong side (".hello" instead of "hello.") and English words split the sentence in the wrong order.  So:
    - punctuation and spaces at the start move to the (visual) right end and those at the end to the left,
      with brackets mirrored;
    - the words are grouped into Arabic and English runs, and the runs are put in right-to-left order.
    Windows lays out each Arabic run (with its numbers and inner punctuation) correctly by itself.
    """
    if REORDER or not is_rtl(line):
        return line
    strong = [i for i, ch in enumerate(line) if _is_strong(ch)]
    if not strong:
        return line
    lead, core, trail = line[:strong[0]], line[strong[0]:strong[-1] + 1], line[strong[-1] + 1:]
    segments = []  # [is_latin, [words]]
    for word in core.split():
        latin = any(unicodedata.bidirectional(ch) == "L" for ch in word)
        if segments and segments[-1][0] == latin:
            segments[-1][1].append(word)
        else:
            segments.append([latin, [word]])
    visual = " ".join(" ".join(words) for _latin, words in reversed(segments))
    return trail[::-1].translate(_MIRROR) + visual + lead[::-1].translate(_MIRROR)


def rtl(text):
    """Short text for a button or label: fix the word order of each Arabic line (no wrapping)."""
    text = for_tk(text)
    if not REORDER:
        return "\n".join(rtl_display(line) for line in text.split("\n"))
    return "\n".join(" ".join(reversed(line.split(" "))) if is_rtl(line) else line for line in text.split("\n"))
