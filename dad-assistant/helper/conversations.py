"""Saved conversations (the list on the side of the window).

Each conversation is one JSON file in data/conversations/: the messages exactly as sent to the AI
(so it can continue later), what the chat window showed (so it can be shown again), its title, a
summary of older parts (when it got too long), and how much of it the memory has already read.
index.json keeps the titles and dates so the list opens fast.  Deleted conversations go to
data/conversations/trash/, never straight away.
"""

import io
import json
import logging
import os
import shutil
import threading
import time
import uuid

from . import paths

log = logging.getLogger("assistant.conversations")

TITLE_CHARS = 60


def _now():
    return time.time()


class Conversation(object):
    def __init__(self, data=None):
        data = data or {}
        self.id = data.get("id") or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.title = data.get("title") or ""
        self.title_source = data.get("title_source") or "auto"  # auto (first words), memory (AI), user
        self.created = float(data.get("created") or _now())
        self.updated = float(data.get("updated") or self.created)
        self.messages = data.get("messages") or []          # API format, append-only
        self.display = data.get("display") or []            # [kind, text, who] as shown in the chat
        self.summary = data.get("summary") or ""            # earlier parts, when the history got too long
        self.context_tokens = int(data.get("context_tokens") or 0)
        self.memory_upto = int(data.get("memory_upto") or 0)  # display entries the memory has read

    def to_dict(self):
        return {"id": self.id, "title": self.title, "title_source": self.title_source,
                "created": self.created, "updated": self.updated,
                "messages": self.messages, "display": self.display, "summary": self.summary,
                "context_tokens": self.context_tokens, "memory_upto": self.memory_upto}

    def add_display(self, kind, text, who=None):
        self.display.append([kind, text, who])

    def has_user_messages(self):
        return any(entry[0] == "user" for entry in self.display)

    def auto_title(self):
        if self.title:
            return
        for kind, text, _who in self.display:
            if kind == "user" and text.strip():
                title = " ".join(text.split())
                self.title = title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS].rsplit(" ", 1)[0] + "..."
                self.title_source = "auto"
                return


class Store(object):
    def __init__(self, folder=None):
        self.folder = folder or os.path.join(paths.DATA_DIR, "conversations")
        self.trash = os.path.join(self.folder, "trash")
        self.index_path = os.path.join(self.folder, "index.json")
        self._lock = threading.RLock()
        os.makedirs(self.trash, exist_ok=True)

    # ------------------------------------------------------------------ files

    def _path(self, conv_id):
        safe = "".join(ch for ch in conv_id if ch.isalnum() or ch in "-_")
        return os.path.join(self.folder, safe + ".json")

    @staticmethod
    def _write_json(path, data):
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _read_index(self):
        try:
            with io.open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return self._rebuild_index()

    def _rebuild_index(self):
        index = {}
        for name in os.listdir(self.folder):
            if name.endswith(".json") and name != "index.json":
                conv = self.load(name[:-5])
                if conv is not None:
                    index[conv.id] = self._meta(conv)
        try:
            self._write_json(self.index_path, index)
        except OSError:
            pass
        return index

    # ------------------------------------------------------------------ public

    def list(self):
        """[(id, title, updated)] newest first."""
        with self._lock:
            index = self._read_index()
        items = [(cid, info.get("title") or "", float(info.get("updated") or 0)) for cid, info in index.items()
                 if isinstance(info, dict)
                 and os.path.exists(self._path(cid))]
        return sorted(items, key=lambda item: item[2], reverse=True)

    @staticmethod
    def _meta(conv):
        return {"title": conv.title, "title_source": conv.title_source, "updated": conv.updated,
                "memory_upto": conv.memory_upto}

    def _merge_meta(self, conv, meta):
        """The index is the authority for what the memory worker and renaming changed."""
        if not meta:
            return
        conv.memory_upto = max(conv.memory_upto, int(meta.get("memory_upto") or 0))
        if meta.get("title_source") in ("memory", "user"):  # only first-words titles come from the window
            conv.title, conv.title_source = meta.get("title") or conv.title, meta["title_source"]

    def load(self, conv_id):
        try:
            with io.open(self._path(conv_id), "r", encoding="utf-8") as f:
                conv = Conversation(json.load(f))
        except (OSError, ValueError) as e:
            log.warning("Could not load conversation %s: %s", conv_id, e)
            return None
        with self._lock:
            try:
                with io.open(self.index_path, "r", encoding="utf-8") as f:
                    self._merge_meta(conv, json.load(f).get(conv.id))
            except (OSError, ValueError, AttributeError):
                pass
        return conv

    def save(self, conv):
        """Save if it has anything in it.  Never raises."""
        if not conv.has_user_messages():
            return False
        try:
            with self._lock:
                index = self._read_index()
                self._merge_meta(conv, index.get(conv.id))
                conv.auto_title()
                conv.updated = _now()
                self._write_json(self._path(conv.id), conv.to_dict())
                index[conv.id] = self._meta(conv)
                self._write_json(self.index_path, index)
            return True
        except OSError as e:
            log.error("Could not save conversation %s: %s", conv.id, e)
            return False

    def update_meta(self, conv_id, memory_upto=None, title=None, title_source=None):
        """Change title / memory progress without touching the messages (used from other threads)."""
        with self._lock:
            index = self._read_index()
            meta = index.get(conv_id)
            if meta is None:
                return False
            if memory_upto is not None:
                meta["memory_upto"] = max(int(meta.get("memory_upto") or 0), int(memory_upto))
            if title:
                meta["title"] = " ".join(str(title).split())[:120]
                meta["title_source"] = title_source or "user"
            self._write_json(self.index_path, index)
            return True

    def meta(self, conv_id):
        with self._lock:
            return dict(self._read_index().get(conv_id) or {})

    def rename(self, conv_id, title):
        return self.update_meta(conv_id, title=title, title_source="user")

    def delete(self, conv_id):
        with self._lock:
            src = self._path(conv_id)
            if os.path.exists(src):
                shutil.move(src, os.path.join(self.trash, os.path.basename(src)))
            index = self._read_index()
            index.pop(conv_id, None)
            self._write_json(self.index_path, index)
