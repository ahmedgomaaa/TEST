"""Shared memory across conversations, written by the helper itself.

After a conversation has been quiet for a while (and when another conversation is opened, and at the
next start for anything left over), a background worker sends the AI the part of the conversation it
has not read yet together with everything already remembered, and asks what is worth keeping for the
long run: facts about Dad, his family, his computer and files, his preferences, things he asked to
remember.  The answer adds, corrects or removes short items in data/memory.json.  Every new
conversation starts with these items in the AI's instructions.

The items are short Arabic sentences, so Dad (and Ahmed) can read them in the memory window and delete
any of them.  Nothing here ever blocks the chat: it runs in its own thread and failures are only logged.
"""

import io
import json
import logging
import os
import queue
import re
import threading
import time
import uuid

from . import llm, net, paths

log = logging.getLogger("assistant.memory")

MAX_ITEMS = 200
MAX_ITEM_CHARS = 300
MAX_TRANSCRIPT_CHARS = 60000
IDLE_SECONDS = 90

EXTRACT_SYSTEM = """You keep the long-term memory of a computer helper used by an elderly Egyptian man (and set up by his son Ahmed). You read a new part of one conversation and decide what is worth remembering in future conversations.

Remember durable, useful facts: his name and family, his interests and routines, his preferences (language, how he likes things explained or organized), important places, people and things he mentions, where his important files and folders are, how his computer is set up, problems he had and how they were solved, and anything he explicitly asks you to remember.
Do NOT remember passing details, one-time requests, small talk, things already in the memory, or any password, PIN, card or ID number.

Answer with ONLY a JSON object, no other text:
{"add": ["new item", ...], "update": [{"id": "existing id", "text": "corrected item"}], "remove": ["id of an item that is now wrong"], "title": "a short title for this conversation"}
Each item is one short, self-contained sentence in simple Egyptian Arabic. The title is 2 to 6 Arabic words. Use empty lists when there is nothing to change."""


class Memory(object):
    """data/memory.json: [{"id", "text", "created", "source"}]."""

    def __init__(self, path=None):
        self.path = path or os.path.join(paths.DATA_DIR, "memory.json")
        self._lock = threading.RLock()
        self.listeners = []  # called (from any thread) after every change

    def items(self):
        with self._lock:
            try:
                with io.open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [i for i in data if isinstance(i, dict) and i.get("text")] if isinstance(data, list) else []
            except (OSError, ValueError):
                return []

    def _save(self, items):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(items[-MAX_ITEMS:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                log.exception("Memory listener failed")

    def prompt_text(self):
        """The memory as lines for the AI's instructions ("" if empty)."""
        return "\n".join("- " + i["text"] for i in self.items())

    def remove(self, ids):
        ids = set(ids)
        with self._lock:
            self._save([i for i in self.items() if i.get("id") not in ids])

    def clear(self):
        with self._lock:
            self._save([])

    def apply(self, changes, source=None):
        """Apply {"add": [...], "update": [...], "remove": [...]} from the AI.  Returns how many changed."""
        if not isinstance(changes, dict):
            return 0
        with self._lock:
            items = self.items()
            by_id = {i.get("id"): i for i in items}
            changed = 0
            for rid in changes.get("remove") or []:
                if rid in by_id:
                    items.remove(by_id.pop(rid))
                    changed += 1
            for upd in changes.get("update") or []:
                if isinstance(upd, dict) and upd.get("id") in by_id and _clean(upd.get("text")):
                    by_id[upd["id"]]["text"] = _clean(upd["text"])
                    changed += 1
            known = set(i["text"] for i in items)
            for text in changes.get("add") or []:
                text = _clean(text)
                if text and text not in known:
                    items.append({"id": uuid.uuid4().hex[:8], "text": text, "created": time.time(),
                                  "source": source})
                    known.add(text)
                    changed += 1
            if changed:
                self._save(items)
            return changed


def _clean(text):
    text = " ".join(str(text or "").split())
    return text[:MAX_ITEM_CHARS]


def transcript(conv, start):
    """The readable part of a conversation from display entry `start` on (what was said, not tool noise)."""
    lines = []
    for kind, text, _who in conv.display[start:]:
        if kind == "user":
            lines.append("HIM: " + text)
        elif kind == "assistant":
            lines.append("HELPER: " + text)
    text = "\n".join(lines)
    return text[-MAX_TRANSCRIPT_CHARS:]


def parse_json_object(text):
    """The first {...} JSON object in the AI's answer, or None."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


class MemoryWorker(object):
    """Reads finished conversation parts in the background and updates the memory."""

    def __init__(self, cfg, store, memory, make_client=None, on_title=None):
        self.cfg = cfg
        self.store = store
        self.memory = memory
        self.make_client = make_client or (lambda: llm.make_llm(cfg))
        self.on_title = on_title  # called (conv_id, title) when a conversation gets a better title
        self.queue = queue.Queue()
        self._timers = {}
        self._lock = threading.Lock()
        self.enabled = bool((cfg.get("memory") or {}).get("enabled", True))

    def start(self):
        if not self.enabled:
            return
        threading.Thread(target=self._loop, name="memory", daemon=True).start()
        # Anything left over from last time (the app was closed before the memory read it).
        for conv_id, _title, _updated in self.store.list():
            conv = self.store.load(conv_id)
            if conv is not None and conv.memory_upto < len(conv.display):
                self.queue.put(conv_id)

    def soon(self, conv_id, delay=IDLE_SECONDS):
        """Read this conversation once it has been quiet for `delay` seconds."""
        if not self.enabled:
            return
        with self._lock:
            old = self._timers.pop(conv_id, None)
            if old:
                old.cancel()
            timer = threading.Timer(delay, lambda: self.queue.put(conv_id))
            timer.daemon = True
            self._timers[conv_id] = timer
            timer.start()

    def now(self, conv_id):
        self.soon(conv_id, delay=0)

    def _loop(self):
        while True:
            conv_id = self.queue.get()
            try:
                self.process(conv_id)
            except Exception:
                log.exception("Memory update failed for %s", conv_id)

    def process(self, conv_id):
        conv = self.store.load(conv_id)
        if conv is None or conv.memory_upto >= len(conv.display):
            return False
        end = len(conv.display)
        text = transcript(conv, conv.memory_upto)
        if "HIM:" not in text:
            self._mark_read(conv_id, end)
            return False
        existing = self.memory.items()
        known = "\n".join("%s: %s" % (i["id"], i["text"]) for i in existing) or "(nothing yet)"
        prompt = ("WHAT IS REMEMBERED NOW (id: item):\n%s\n\nCONVERSATION TITLE NOW: %s\n\n"
                  "NEW PART OF THE CONVERSATION:\n%s" % (known, conv.title or "(none)", text))
        client = self.make_client()
        if hasattr(client, "effort"):
            client.effort = (self.cfg.get("memory") or {}).get("effort") or "low"
        try:
            reply = client.create(EXTRACT_SYSTEM, [], [{"role": "user", "content": [{"type": "text", "text": prompt}]}])
        except (net.NetworkError, net.HttpError, llm.ConfigError) as e:
            log.info("Memory update postponed (%s)", e)
            return False  # tried again at the next start
        changes = parse_json_object(reply.text())
        if changes is None:
            log.warning("Memory answer was not JSON: %r", reply.text()[:300])
            self._mark_read(conv_id, end)
            return False
        n = self.memory.apply(changes, source=conv_id)
        title = _clean(changes.get("title"))[:80]
        self._mark_read(conv_id, end, title)
        log.info("Memory updated from %s: %d change(s)", conv_id, n)
        return True

    def _mark_read(self, conv_id, end, title=None):
        better = bool(title) and self.store.meta(conv_id).get("title_source") != "user"
        self.store.update_meta(conv_id, memory_upto=end, title=title if better else None, title_source="memory")
        if better and self.on_title:
            self.on_title(conv_id, title)
