"""The things the assistant can do on the laptop.

Safety rules enforced here (not just asked of the model):
- System folders, program folders, AppData and this app's own folder can never be
  changed; drive roots and the main user folders (Desktop, Documents...) can never
  be moved or deleted themselves.
- Nothing is deleted permanently: only the Recycle Bin is used, and only on fixed
  drives (USB sticks and network drives have no Recycle Bin).
- Every change is confirmed with a Yes/No question and recorded so it can be undone.
"""

import base64
import datetime
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import time
import zipfile

from . import net, paths, shell, textutil, winapi
from . import strings as S

log = logging.getLogger("assistant.tools")

_EXPLANATION = {
    "type": "string",
    "description": "One short, friendly sentence for the user, in simple Egyptian Arabic, saying what will happen. "
                   "It is shown to him in a big Yes/No question.",
}

TOOL_DEFS = [
    {
        "name": "list_folder",
        "description": "List what is inside a folder (names, sizes, dates), with a summary of file types.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the folder."},
                "show_hidden": {"type": "boolean", "description": "Also show hidden/system files. Default false."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_files",
        "description": "Search for files by name, type, date and/or text inside documents. Searches the user's "
                       "home folder by default. Slow laptop: search a specific folder when you can.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Where to search (full path). Default: the user's home folder."},
                "name_contains": {"type": "string", "description": "Part of the file name (case-insensitive). Wildcards * and ? are allowed."},
                "extensions": {"type": "array", "items": {"type": "string"}, "description": "File types like [\".jpg\", \".png\"]."},
                "modified_after": {"type": "string", "description": "Only files changed on/after this date, YYYY-MM-DD."},
                "modified_before": {"type": "string", "description": "Only files changed before this date, YYYY-MM-DD."},
                "text_contains": {"type": "string", "description": "Text that must appear inside the file (.txt, .docx and other text files only)."},
                "include_folders": {"type": "boolean", "description": "Also return folders whose names match. Default false."},
                "max_results": {"type": "integer", "description": "Default 100, maximum 500."},
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read the text of a document (.txt, .docx, .csv, .html, .rtf and similar).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path of the file."}},
            "required": ["path"],
        },
    },
    {
        "name": "create_folder",
        "description": "Create a new folder (and any missing parent folders).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path of the new folder."}},
            "required": ["path"],
        },
    },
    {
        "name": "move_items",
        "description": "Move and/or rename files and folders, many at once. Use this for organizing, sorting and "
                       "renaming. To move into a folder, give the folder path in 'to' (it is created if missing). "
                       "To rename, give the full new path including the file extension. Missing folders are created. Existing files are "
                       "never overwritten (a number is added to the name). Can be undone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "moves": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "description": "Full path of the file or folder now."},
                            "to": {"type": "string", "description": "Destination folder, or the full new path."},
                        },
                        "required": ["from", "to"],
                    },
                },
                "explanation": _EXPLANATION,
            },
            "required": ["moves", "explanation"],
        },
    },
    {
        "name": "recycle_items",
        "description": "Delete files or folders by sending them to the Recycle Bin (they can be restored from there). "
                       "This is the only way to delete things.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Full paths to delete."},
                "explanation": _EXPLANATION,
            },
            "required": ["paths", "explanation"],
        },
    },
    {
        "name": "open_item",
        "description": "Open a file, folder, web address or program for the user, the same as double-clicking it "
                       "(e.g. a photo, a document, the Desktop folder, 'notepad', 'calc', 'mspaint', or "
                       "'http://...').",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path, program name or web address."}},
            "required": ["path"],
        },
    },
    {
        "name": "find_duplicate_files",
        "description": "Find files that are exact copies of each other (same content) in a folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Full path of the folder to check."},
                "include_subfolders": {"type": "boolean", "description": "Default true."},
            },
            "required": ["folder"],
        },
    },
    {
        "name": "undo_last_change",
        "description": "Undo the most recent change made with move_items or create_folder (put things back). "
                       "Deleted items cannot be undone here; they are in the Recycle Bin.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_command",
        "description": "Run a Windows command, only when no other tool can do the job. Remember: PowerShell 2.0 "
                       "and cmd on Windows 7; hidden window, no keyboard input; time limited; deleting, "
                       "downloading, installing and system changes are blocked.",
        "input_schema": {
            "type": "object",
            "properties": {
                "shell": {"type": "string", "enum": ["powershell", "cmd"]},
                "command": {"type": "string", "description": "The command or PowerShell 2.0 script."},
                "changes_things": {"type": "boolean", "description": "true if it changes anything on the computer; false if it only looks."},
                "explanation": _EXPLANATION,
            },
            "required": ["shell", "command", "changes_things", "explanation"],
        },
    },
    {
        "name": "generate_image",
        "description": "Create a picture from a description. It is saved in his Pictures folder and shown in the chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed description of the picture, in English."},
                "file_name": {"type": "string", "description": "Short file name without extension, e.g. 'birthday card'."},
            },
            "required": ["prompt"],
        },
    },
]

TOOL_NAMES = set(t["name"] for t in TOOL_DEFS)

TEXT_EXTENSIONS = {".txt", ".csv", ".log", ".md", ".html", ".htm", ".xml", ".json", ".ini", ".rtf", ".bat",
                   ".cmd", ".ps1", ".py", ".js", ".css", ".srt", ".nfo", ".tsv", ".eml", ".vcf"}
RISKY_TO_OPEN = {".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".ps1", ".msi", ".msp", ".scr", ".reg",
                 ".hta", ".jar", ".com", ".pif", ".cpl", ".inf"}
SKIP_NAMES = {"desktop.ini", "thumbs.db", "ntuser.dat"}
MAX_TEXT_CHARS = 20000


class ToolError(Exception):
    """A problem the model should be told about in plain words."""


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_hidden(entry_path, name):
    if name.lower() in SKIP_NAMES or name.startswith("~$"):
        return True
    if name.startswith("."):
        return True
    if winapi.IS_WINDOWS:
        try:
            return bool(os.stat(entry_path).st_file_attributes & (0x2 | 0x4))  # HIDDEN | SYSTEM
        except (OSError, AttributeError):
            return False
    return False


def _date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _parse_date(value, name):
    if not value:
        return None
    try:
        return time.mktime(datetime.datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").timetuple())
    except ValueError:
        raise ToolError("%s must look like 2024-05-31." % name)


def _unique_path(path):
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    if os.path.isdir(path):
        base, ext = path, ""
    n = 2
    while os.path.lexists("%s (%d)%s" % (base, n, ext)):
        n += 1
    return "%s (%d)%s" % (base, n, ext)


def _decode_text(data):
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("mbcs" if winapi.IS_WINDOWS else "latin-1", "replace")


def _docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(a, b)
    return text


def _rtf_text(text):
    text = re.sub(r"\\par[d]?", "\n", text)
    text = re.sub(r"\{\\\*[^{}]*\}", "", text)
    text = re.sub(r"\\[a-z]+-?\d* ?", "", text)
    return text.replace("{", "").replace("}", "")


def read_text_of(path, limit=MAX_TEXT_CHARS * 5):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _docx_text(path)
    if ext in TEXT_EXTENSIONS:
        with open(path, "rb") as f:
            text = _decode_text(f.read(limit * 4))
        return _rtf_text(text) if ext == ".rtf" else text
    return None


class UndoJournal(object):
    def __init__(self, path):
        self.path = path

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, batches):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(batches[-30:], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Could not save undo journal: %s", e)

    def add(self, description, ops):
        if not ops:
            return
        batches = self._load()
        batches.append({"time": _date(time.time()), "description": description, "ops": ops})
        self._save(batches)

    def peek(self):
        batches = self._load()
        return batches[-1] if batches else None

    def pop(self):
        batches = self._load()
        if not batches:
            return None
        last = batches.pop()
        self._save(batches)
        return last


class ToolRunner(object):
    def __init__(self, cfg, confirm, notify=None, undo_path=None):
        """confirm(question, details) -> bool;  notify(kind, value) shows things in the chat."""
        self.cfg = cfg
        self.confirm_func = confirm
        self.notify = notify or (lambda kind, value: None)
        self.cancel = None
        self.folders = winapi.known_folders()
        self.home = self.folders["Home"]
        self.protected = [_norm(p) for p in winapi.system_dirs()] + [_norm(paths.APP_DIR), _norm(paths.DATA_DIR)]
        # The main folders as Windows reports them AND under the profile folder, in case they differ
        # (folder redirection, roaming profiles).
        standard = ["Desktop", "Documents", "My Documents", "Pictures", "Music", "Videos", "Downloads",
                    "Favorites", "Contacts", "Links", "Saved Games", "Searches", "OneDrive"]
        self.untouchable = set(_norm(p) for p in self.folders.values())
        self.untouchable.update(_norm(os.path.join(self.home, name)) for name in standard)
        self.undo = UndoJournal(undo_path or paths.UNDO_PATH)
        self.last_undo = None  # (items restored, problems) after undo_last_change, for the Undo button

    # ------------------------------------------------------------------ plumbing

    def run(self, name, args, cancel=None):
        """Run one tool.  Never raises.  Returns (text_for_model, is_error)."""
        self.cancel = cancel
        if name not in TOOL_NAMES:
            return "There is no tool called %r." % name, True
        if not isinstance(args, dict) or "_invalid_arguments" in args:
            return "The tool arguments were not valid JSON. Please try again.", True
        try:
            return getattr(self, "tool_" + name)(args), False
        except ToolError as e:
            return str(e), True
        except Exception as e:
            log.exception("Tool %s failed", name)
            return "The tool failed unexpectedly (%s: %s)." % (type(e).__name__, e), True

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    def _confirm(self, question, details):
        if not self.cfg["safety"].get("confirm_changes", True):
            return True
        return bool(self.confirm_func(question, details))

    def friendly(self, path):
        """Path without the long C:\\Users\\Name\\ prefix, which means nothing to him."""
        n = os.path.normpath(os.path.abspath(path))
        home = os.path.normpath(os.path.abspath(self.home))
        if _norm(n).startswith(_norm(home) + os.sep):
            return n[len(home) + 1:]
        return n

    def resolve(self, raw, what="path"):
        if not raw or not isinstance(raw, str) or not raw.strip():
            raise ToolError("A %s is needed." % what)
        path = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
        if not os.path.isabs(path):
            path = os.path.join(self.home, path)
        return os.path.normpath(path)

    def is_protected(self, path):
        n = _norm(path)
        for p in self.protected:
            if n == p or n.startswith(p.rstrip("\\/") + os.sep):
                return True
        return False

    def check_changeable(self, path):
        n = _norm(path)
        if self.is_protected(path):
            raise ToolError("%s is a system or program folder. I am not allowed to change anything there." % path)
        if os.path.dirname(n) == n:
            raise ToolError("%s is a whole drive. I am not allowed to move or delete it." % path)
        if n in self.untouchable:
            raise ToolError("%s is one of the main folders (like Desktop or Documents). It must stay where it is."
                            % path)

    # ------------------------------------------------------------------ looking

    def tool_list_folder(self, a):
        path = self.resolve(a.get("path"), "folder")
        if not os.path.isdir(path):
            raise ToolError("The folder %s does not exist." % path)
        try:
            names = os.listdir(path)
        except OSError as e:
            raise ToolError("I could not open %s (%s)." % (path, e.strerror or e))
        show_hidden = bool(a.get("show_hidden"))
        folders, files, types = [], [], {}
        for name in sorted(names, key=lambda s: s.lower()):
            full = os.path.join(path, name)
            if not show_hidden and _is_hidden(full, name):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if os.path.isdir(full):
                folders.append("[folder] %s" % name)
            else:
                ext = os.path.splitext(name)[1].lower() or "(no type)"
                types[ext] = types.get(ext, 0) + 1
                files.append("%s  |  %s  |  %s" % (name, textutil.human_size(st.st_size), _date(st.st_mtime)))
        lines = ["Folder: %s" % path, "%d folders, %d files." % (len(folders), len(files))]
        if types:
            common = sorted(types.items(), key=lambda kv: -kv[1])[:15]
            lines.append("File types: " + ", ".join("%s x%d" % kv for kv in common))
        listing = folders + files
        lines.extend(listing[:400])
        if len(listing) > 400:
            lines.append("... and %d more (use find_files to narrow down)." % (len(listing) - 400))
        return "\n".join(lines)

    def _walk(self, top, time_limit):
        """Yield (dirpath, dirnames, filenames) skipping system, hidden and huge folders."""
        deadline = time.time() + time_limit
        for dirpath, dirnames, filenames in os.walk(top, onerror=lambda e: None):
            if self._cancelled() or time.time() > deadline:
                raise _Timeout()
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith((".", "$")) and d.lower() not in ("appdata", "node_modules", "__pycache__")
                and not self.is_protected(os.path.join(dirpath, d))
            ]
            yield dirpath, dirnames, filenames

    def tool_find_files(self, a):
        top = self.resolve(a.get("folder") or self.home, "folder")
        if not os.path.isdir(top):
            raise ToolError("The folder %s does not exist." % top)
        pattern = (a.get("name_contains") or "").strip().lower()
        exts = set()
        for e in a.get("extensions") or []:
            e = str(e).strip().lower()
            if e:
                exts.add(e if e.startswith(".") else "." + e)
        after = _parse_date(a.get("modified_after"), "modified_after")
        before = _parse_date(a.get("modified_before"), "modified_before")
        needle = (a.get("text_contains") or "").strip().lower()
        include_folders = bool(a.get("include_folders"))
        try:
            limit = max(1, min(500, int(a.get("max_results") or 100)))
        except (TypeError, ValueError):
            limit = 100

        def name_ok(name):
            if not pattern:
                return True
            low = name.lower()
            if "*" in pattern or "?" in pattern:
                return fnmatch.fnmatch(low, pattern)
            return all(word in low for word in pattern.split())

        results, scanned, stopped = [], 0, False
        try:
            for dirpath, dirnames, filenames in self._walk(top, 60):
                candidates = list(filenames)
                if include_folders:
                    candidates += [d for d in dirnames]
                for name in candidates:
                    scanned += 1
                    if name.lower() in SKIP_NAMES or not name_ok(name):
                        continue
                    full = os.path.join(dirpath, name)
                    is_dir = os.path.isdir(full)
                    if exts and (is_dir or os.path.splitext(name)[1].lower() not in exts):
                        continue
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if after and st.st_mtime < after or before and st.st_mtime >= before:
                        continue
                    if needle:
                        if is_dir or st.st_size > 5 * 1024 * 1024:
                            continue
                        try:
                            text = read_text_of(full)
                        except Exception:
                            text = None
                        if not text or needle not in text.lower():
                            continue
                    results.append((st.st_mtime, full, st.st_size, is_dir))
                    if len(results) >= limit:
                        raise _Timeout()
        except _Timeout:
            stopped = True
        results.sort(reverse=True)
        lines = ["Searched %s (%d items checked)." % (top, scanned)]
        if stopped and len(results) < limit:
            lines.append("The search stopped early (time limit or Stop pressed); results may be incomplete.")
        elif len(results) >= limit:
            lines.append("Showing the first %d matches; there may be more." % limit)
        if not results:
            lines.append("No matching files found.")
        for mtime, full, size, is_dir in results:
            kind = "[folder] " if is_dir else ""
            lines.append("%s%s  |  %s  |  %s" % (kind, full, "" if is_dir else textutil.human_size(size), _date(mtime)))
        return "\n".join(lines)

    def tool_read_file(self, a):
        path = self.resolve(a.get("path"), "file")
        if not os.path.isfile(path):
            raise ToolError("The file %s does not exist." % path)
        ext = os.path.splitext(path)[1].lower()
        try:
            text = read_text_of(path)
        except (OSError, zipfile.BadZipFile, KeyError) as e:
            raise ToolError("I could not read %s (%s)." % (path, e))
        if text is None:
            raise ToolError("Files of type %s cannot be read as text. You can open it for him with open_item."
                            % (ext or "(no type)"))
        text = text.strip()
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n\n[... the rest of the file was cut ...]"
        return "Text of %s:\n\n%s" % (path, text or "(the file is empty)")

    def tool_find_duplicate_files(self, a):
        top = self.resolve(a.get("folder"), "folder")
        if not os.path.isdir(top):
            raise ToolError("The folder %s does not exist." % top)
        recursive = a.get("include_subfolders", True) is not False
        by_size = {}
        stopped = False
        try:
            for dirpath, dirnames, filenames in self._walk(top, 90):
                for name in filenames:
                    if name.lower() in SKIP_NAMES:
                        continue
                    full = os.path.join(dirpath, name)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    if size > 0:
                        by_size.setdefault(size, []).append(full)
                if not recursive:
                    dirnames[:] = []
        except _Timeout:
            stopped = True
        groups = []
        deadline = time.time() + 120
        for size, files in sorted(by_size.items(), key=lambda kv: -kv[0]):
            if len(files) < 2:
                continue
            if time.time() > deadline or self._cancelled():
                stopped = True
                break
            by_hash = {}
            for f in files:
                digest = _file_hash(f)
                if digest:
                    by_hash.setdefault(digest, []).append(f)
            for same in by_hash.values():
                if len(same) > 1:
                    groups.append((size, sorted(same, key=lambda p: os.path.getmtime(p))))
        if not groups:
            return "No duplicate files found in %s.%s" % (top, " (The check stopped early.)" if stopped else "")
        wasted = sum(size * (len(same) - 1) for size, same in groups)
        lines = ["Found %d groups of identical files in %s. Removing the extra copies would free %s."
                 % (len(groups), top, textutil.human_size(wasted))]
        if stopped:
            lines.append("The check stopped early, so there may be more.")
        lines.append("In each group the first file is the oldest.")
        for i, (size, same) in enumerate(groups[:60], 1):
            lines.append("Group %d (%s each):" % (i, textutil.human_size(size)))
            lines.extend("  " + p for p in same[:20])
        if len(groups) > 60:
            lines.append("... and %d more groups." % (len(groups) - 60))
        return "\n".join(lines)

    # ------------------------------------------------------------------ changing

    def tool_create_folder(self, a):
        path = self.resolve(a.get("path"), "folder path")
        self.check_changeable(path)
        if os.path.isdir(path):
            return "The folder %s already exists." % path
        created = []
        self._makedirs(path, created)
        self.undo.add("Created folder %s" % self.friendly(path), [{"type": "mkdir", "path": p} for p in created])
        return "Created the folder %s." % path

    def _makedirs(self, path, created):
        """Create missing folders, remembering each new one for undo."""
        missing = []
        p = path
        while p and not os.path.isdir(p):
            missing.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        for folder in reversed(missing):
            try:
                os.mkdir(folder)
                created.append(folder)
            except OSError as e:
                if not os.path.isdir(folder):
                    raise ToolError("I could not create the folder %s (%s)." % (folder, e.strerror or e))

    def tool_move_items(self, a):
        moves = a.get("moves")
        if not isinstance(moves, list) or not moves:
            raise ToolError("No items to move were given.")
        if len(moves) > 3000:
            raise ToolError("That is too many items at once (maximum 3000). Please do it in smaller groups.")
        plan, problems, planned_dests = [], [], set()
        target_counts = {}
        for m in moves:
            if isinstance(m, dict) and isinstance(m.get("to"), str):
                key = _norm(m["to"].strip().strip('"').rstrip("\\/") or m["to"])
                target_counts[key] = target_counts.get(key, 0) + 1
        for m in moves:
            if not isinstance(m, dict):
                continue
            try:
                src = self.resolve(m.get("from"), "source path")
                raw_to = str(m.get("to") or "").strip().strip('"')
                dst = self.resolve(raw_to, "destination")
                if not os.path.lexists(src):
                    raise ToolError("%s does not exist." % src)
                self.check_changeable(src)
                if _norm(dst) != _norm(src) and self._is_folder_target(src, dst, raw_to, target_counts):
                    dst = os.path.join(dst, os.path.basename(src))
                self.check_changeable(dst)
                if _norm(dst) == _norm(src):
                    continue
                if _norm(dst).startswith(_norm(src) + os.sep):
                    raise ToolError("%s cannot be moved inside itself." % src)
                if os.path.lexists(dst) or _norm(dst) in planned_dests:
                    base, ext = os.path.splitext(dst)
                    n = 2
                    while os.path.lexists("%s (%d)%s" % (base, n, ext)) or _norm("%s (%d)%s" % (base, n, ext)) in planned_dests:
                        n += 1
                    dst = "%s (%d)%s" % (base, n, ext)
                planned_dests.add(_norm(dst))
                plan.append((src, dst))
            except ToolError as e:
                problems.append(str(e))
        if not plan:
            raise ToolError("Nothing can be moved. " + " ".join(problems[:10]))
        details = ["%s   ->   %s" % (self.friendly(s), self.friendly(d)) for s, d in plan]
        question = a.get("explanation") or S.Q_MOVE.format(n=len(plan))
        if not self._confirm(question, details):
            return "The user said NO. Nothing was moved. Ask him what he would like instead."
        ops, done, failed = [], 0, []
        try:
            for src, dst in plan:
                if self._cancelled():
                    failed.append("Stopped by the user before the rest were moved.")
                    break
                try:
                    created = []
                    self._makedirs(os.path.dirname(dst), created)
                    ops.extend({"type": "mkdir", "path": p} for p in created)
                    shutil.move(src, dst)
                    ops.append({"type": "move", "from": src, "to": dst})
                    done += 1
                except (OSError, shutil.Error, ToolError) as e:
                    failed.append("%s: %s" % (self.friendly(src), getattr(e, "strerror", None) or e))
        finally:
            self.undo.add(question, ops)
        result = ["Moved/renamed %d of %d items." % (done, len(plan))]
        if problems:
            result.append("Skipped before starting: " + " | ".join(problems[:20]))
        if failed:
            result.append("Problems: " + " | ".join(failed[:20]))
        result.append("This can be undone with undo_last_change.")
        return "\n".join(result)

    @staticmethod
    def _is_folder_target(src, dst, raw_to, target_counts):
        """Is 'to' a folder to move into (rather than a new name)?  Errs on the side of "folder"."""
        if os.path.isdir(dst):
            return True
        if os.path.lexists(dst):
            return False
        if raw_to.endswith(("\\", "/")):
            return True
        if target_counts.get(_norm(dst), 0) > 1:
            return True  # several items going to the same place: it must be a folder
        src_ext = os.path.splitext(src)[1]
        return bool(src_ext) and os.path.isfile(src) and not os.path.splitext(dst)[1]

    def tool_recycle_items(self, a):
        raw = a.get("paths")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ToolError("No items to delete were given.")
        targets, problems = [], []
        for item in raw:
            try:
                path = self.resolve(item)
                if not os.path.lexists(path):
                    raise ToolError("%s does not exist." % path)
                self.check_changeable(path)
                kind = winapi.drive_kind(path)
                if kind not in ("fixed", "ramdisk"):
                    raise ToolError("%s is on a USB stick, CD or network drive, which has no Recycle Bin, so deleting "
                                    "would be permanent. I will not delete it. He can move it to the computer first."
                                    % path)
                targets.append(path)
            except ToolError as e:
                problems.append(str(e))
        if not targets:
            raise ToolError(" ".join(problems[:10]) or "Nothing can be deleted.")
        question = a.get("explanation") or S.Q_RECYCLE.format(n=len(targets))
        if not self._confirm(question, [self.friendly(p) for p in targets]):
            return "The user said NO. Nothing was deleted."
        done, failed = 0, []
        for path in targets:
            if self._cancelled():
                failed.append("Stopped by the user before the rest were deleted.")
                break
            err = winapi.recycle(path)
            if err:
                failed.append("%s: %s" % (self.friendly(path), err))
            else:
                done += 1
        result = ["Put %d of %d items in the Recycle Bin. They can be restored from the Recycle Bin on the desktop."
                  % (done, len(targets))]
        if problems:
            result.append("Skipped: " + " | ".join(problems[:20]))
        if failed:
            result.append("Problems: " + " | ".join(failed[:20]))
        return "\n".join(result)

    def tool_open_item(self, a):
        raw = (a.get("path") or "").strip().strip('"')
        if not raw:
            raise ToolError("Nothing to open was given.")
        if re.match(r"^(https?://|www\.)", raw, re.IGNORECASE):
            target = raw if raw.lower().startswith("http") else "http://" + raw
        else:
            target = self.resolve(raw)
            if not os.path.exists(target):
                if re.match(r"^[\w\-. ]+$", raw):
                    target = raw  # a program name like "notepad" or "calc"
                else:
                    raise ToolError("%s does not exist." % target)
            else:
                ext = os.path.splitext(target)[1].lower()
                if ext in RISKY_TO_OPEN or (ext == ".exe" and not self.is_protected(target)):
                    if not self._confirm(S.Q_RUN_PROGRAM.format(name=os.path.basename(target)),
                                         [S.D_RUN_PROGRAM, self.friendly(target)]):
                        return "The user said NO, so it was not opened."
        try:
            winapi.open_with_default_app(target)
        except OSError as e:
            raise ToolError("Windows could not open %s (%s)." % (target, e.strerror or e))
        return "Opened %s." % target

    def tool_undo_last_change(self, a=None):
        batch = self.undo.peek()
        if not batch:
            return "There is nothing to undo."
        if not self._confirm(S.Q_UNDO,
                             [str(batch.get("description") or "").rstrip(". "),
                              S.D_UNDO_WHEN.format(time=batch.get("time"))]):
            return "The user said NO. Nothing was undone."
        self.undo.pop()
        restored, problems = 0, []
        for op in reversed(batch.get("ops") or []):
            try:
                if op.get("type") == "move":
                    src, dst = op["to"], op["from"]
                    if not os.path.lexists(src):
                        problems.append("%s is no longer there." % self.friendly(src))
                        continue
                    dst = _unique_path(dst)
                    parent = os.path.dirname(dst)
                    if parent and not os.path.isdir(parent):
                        os.makedirs(parent)
                    shutil.move(src, dst)
                    restored += 1
                elif op.get("type") == "mkdir":
                    try:
                        os.rmdir(op["path"])  # only removes empty folders
                    except OSError:
                        pass
            except (OSError, shutil.Error, KeyError) as e:
                problems.append(str(e))
        self.last_undo = (restored, problems)
        text = "I undid the last change: %d items were put back where they were." % restored
        if problems:
            text += " Problems: " + " | ".join(problems[:20])
        return text

    def tool_run_command(self, a):
        command = a.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("No command was given.")
        if len(command) > shell.MAX_COMMAND_CHARS:
            raise ToolError("The command is too long. Please split it into smaller steps.")
        kind = "cmd" if str(a.get("shell") or "").lower() == "cmd" else "powershell"
        why = shell.blocked_reason(command)
        if why:
            raise ToolError("Blocked for safety: commands are not allowed to do %s. Use the dedicated tools "
                            "(for deleting: recycle_items) or tell the user this cannot be done here." % why)
        if a.get("changes_things") or shell.looks_like_change(command):
            question = a.get("explanation") or S.Q_CHANGE
            if not self._confirm(question, [S.D_TECHNICAL, command[:400]]):
                return "The user said NO. The command was not run."
        timeout = self.cfg["safety"]["command_timeout_seconds"]
        result = shell.run(kind, command, timeout=timeout, cancel=self.cancel)
        return result.describe()

    def tool_generate_image(self, a):
        settings = self.cfg["images"]
        if not settings.get("api_key"):
            raise ToolError("Making pictures is not set up yet (Ahmed needs to add an images API key). "
                            "Tell the user kindly.")
        prompt = (a.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("Please describe the picture.")
        model = settings.get("model") or "gpt-image-1"
        url = (settings.get("base_url") or "https://api.openai.com/v1").rstrip("/") + "/images/generations"
        payload = {"model": model, "prompt": prompt[:3900], "n": 1, "size": settings.get("size") or "1024x1024"}
        if model.startswith("dall-e"):
            payload["response_format"] = "b64_json"
        try:
            data = net.post_json(url, payload, {"Authorization": "Bearer " + settings["api_key"]},
                                 timeout=240, cancel=self.cancel, delays=(3, 6))
        except net.HttpError as e:
            log.error("Image API error: %s", e)
            if e.status in (400,) and "safety" in e.body.lower():
                raise ToolError("The picture service refused this description. Try describing it differently.")
            raise ToolError("The picture service had a problem (error %s). Try again later." % e.status)
        except net.NetworkError as e:
            raise ToolError("Could not reach the picture service (%s)." % e)
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            image = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            image = net.get_bytes(item["url"], cancel=self.cancel)
        else:
            raise ToolError("The picture service did not send a picture.")
        ext = ".jpg" if image[:3] == b"\xff\xd8\xff" else ".png"
        name = re.sub(r"[^\w\- ]+", "", a.get("file_name") or "picture", flags=re.UNICODE).strip()[:50] or "picture"
        folder = os.path.join(self.folders["Pictures"], "Assistant Pictures")
        os.makedirs(folder, exist_ok=True)
        path = _unique_path(os.path.join(folder, "%s %s%s" % (name, datetime.date.today().isoformat(), ext)))
        with open(path, "wb") as f:
            f.write(image)
        self.notify("image", path)
        return "The picture was saved as %s and is shown in the chat." % path


class _Timeout(Exception):
    pass


def _file_hash(path):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
