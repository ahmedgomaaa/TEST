"""Saved conversations, the shared memory, and long conversations (1M context, summarize and continue)."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helper import agent, config, conversations, llm, memory, tools  # noqa: E402
from helper import strings as S  # noqa: E402

AR = "محمد بيحب الشاي"  # an Arabic sentence


def make_cfg(**extra):
    cfg = config._merge(config.DEFAULTS, {"anthropic": {"api_key": "sk-ant-test-1234567890"}})
    cfg.update(extra)
    config._sanitize(cfg, [])
    return cfg


class ScriptedClient(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, system, tool_defs, messages, cancel=None):
        self.calls.append({"system": system, "tools": tool_defs, "messages": [dict(m) for m in messages]})
        reply = self.replies.pop(0)
        if isinstance(reply, llm.LLMResponse):
            return reply
        return llm.LLMResponse([{"type": "text", "text": reply}], "end_turn",
                               {"input_tokens": 10, "output_tokens": 5})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = conversations.Store(os.path.join(self.tmp, "conversations"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conv(self, text="hello there"):
        c = conversations.Conversation()
        c.add_display("user", text, S.YOU)
        c.add_display("assistant", "answer", "x")
        c.messages = [{"role": "user", "content": [{"type": "text", "text": text}]}]
        return c

    def test_empty_conversation_is_not_saved(self):
        self.assertFalse(self.store.save(conversations.Conversation()))
        self.assertEqual(self.store.list(), [])

    def test_save_list_load_rename_delete(self):
        c = self.conv(AR)
        self.assertTrue(self.store.save(c))
        [(cid, title, _updated)] = self.store.list()
        self.assertEqual((cid, title), (c.id, AR))
        loaded = self.store.load(cid)
        self.assertEqual(loaded.messages, c.messages)
        self.assertEqual(loaded.display, c.display)
        self.store.rename(cid, "  new   name ")
        self.assertEqual(self.store.list()[0][1], "new name")
        self.store.delete(cid)
        self.assertEqual(self.store.list(), [])
        self.assertTrue(os.listdir(self.store.trash), "deleted conversations are kept in the trash")

    def test_long_first_message_makes_a_short_title(self):
        c = self.conv("word " * 50)
        self.store.save(c)
        self.assertTrue(c.title.endswith("..."))
        self.assertLessEqual(len(c.title), conversations.TITLE_CHARS + 3)

    def test_newest_first(self):
        a, b = self.conv("first"), self.conv("second")
        self.store.save(a)
        self.store.save(b)
        self.store.save(a)
        self.assertEqual([t for _i, t, _u in self.store.list()], ["first", "second"])

    def test_window_save_does_not_undo_memory_progress_or_names(self):
        """The window keeps its own copy of the conversation; saving it later must not overwrite what the
        memory worker (another thread) or a rename changed in between."""
        c = self.conv()
        self.store.save(c)
        self.store.update_meta(c.id, memory_upto=2, title="AI title", title_source="memory")
        c.add_display("user", "more", S.YOU)
        self.store.save(c)  # the window's copy still says memory_upto=0 and the first-words title
        meta = self.store.meta(c.id)
        self.assertEqual((meta["memory_upto"], meta["title"]), (2, "AI title"))
        self.store.rename(c.id, "Dad's name for it")
        self.store.update_meta(c.id, title="another AI title", title_source="memory")  # worker does not check
        self.store.save(c)
        self.assertEqual(self.store.list()[0][1], "another AI title")

    def test_broken_index_is_rebuilt(self):
        c = self.conv()
        self.store.save(c)
        with open(self.store.index_path, "w") as f:
            f.write("{broken")
        self.assertEqual(self.store.list()[0][0], c.id)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem = memory.Memory(os.path.join(self.tmp, "memory.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_update_remove_and_dedupe(self):
        self.assertEqual(self.mem.apply({"add": [AR, AR, "  two   words "]}), 2)
        items = self.mem.items()
        self.assertEqual([i["text"] for i in items], [AR, "two words"])
        first = items[0]["id"]
        self.mem.apply({"update": [{"id": first, "text": "changed"}], "remove": [items[1]["id"], "unknown"]})
        self.assertEqual([i["text"] for i in self.mem.items()], ["changed"])
        self.assertEqual(self.mem.prompt_text(), "- changed")
        self.mem.remove([first])
        self.assertEqual(self.mem.items(), [])

    def test_bad_answers_change_nothing(self):
        self.assertEqual(self.mem.apply(None), 0)
        self.assertEqual(self.mem.apply({"update": [{"id": "x"}], "remove": [None]}), 0)

    def test_listeners_are_told(self):
        calls = []
        self.mem.listeners.append(lambda: calls.append(1))
        self.mem.apply({"add": ["a"]})
        self.assertEqual(calls, [1])

    def test_parse_json_object(self):
        p = memory.parse_json_object
        self.assertEqual(p('{"add": ["a"]}'), {"add": ["a"]})
        self.assertEqual(p('Here you go:\n```json\n{"add": [], "title": "x"}\n```'), {"add": [], "title": "x"})
        self.assertEqual(p('text {not json} then {"remove": ["1"]}'), {"remove": ["1"]})
        self.assertIsNone(p("no json here"))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = conversations.Store(os.path.join(self.tmp, "conversations"))
        self.mem = memory.Memory(os.path.join(self.tmp, "memory.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def worker(self, client):
        return memory.MemoryWorker(make_cfg(), self.store, self.mem, make_client=lambda: client)

    def saved(self, *lines):
        c = conversations.Conversation()
        for kind, text in lines:
            c.add_display(kind, text)
        self.store.save(c)
        return c

    def test_reads_new_part_once_and_names_the_conversation(self):
        c = self.saved(("user", AR), ("activity", "Looking in a folder"), ("assistant", "ok"))
        client = ScriptedClient(['{"add": ["%s"], "title": "title from AI"}' % AR])
        w = self.worker(client)
        self.assertTrue(w.process(c.id))
        self.assertEqual([i["text"] for i in self.mem.items()], [AR])
        sent = client.calls[0]["messages"][0]["content"][0]["text"]
        self.assertIn("HIM: " + AR, sent)
        self.assertNotIn("Looking in a folder", sent, "tool activity is not sent")
        self.assertEqual(client.calls[0]["tools"], [])
        self.assertEqual(self.store.list()[0][1], "title from AI")
        self.assertFalse(w.process(c.id), "nothing new: no second call")
        self.assertEqual(len(client.calls), 1)

    def test_existing_memory_is_sent_and_user_names_are_kept(self):
        self.mem.apply({"add": ["old fact"]})
        c = self.saved(("user", "hello"), ("assistant", "hi"))
        self.store.rename(c.id, "My name")
        client = ScriptedClient(['{"remove": ["%s"], "title": "AI name"}' % self.mem.items()[0]["id"]])
        self.worker(client).process(c.id)
        self.assertIn("old fact", client.calls[0]["messages"][0]["content"][0]["text"])
        self.assertEqual(self.mem.items(), [])
        self.assertEqual(self.store.list()[0][1], "My name")

    def test_offline_is_retried_later_and_garbage_is_skipped(self):
        c = self.saved(("user", "hello"), ("assistant", "hi"))

        class Offline(object):
            def create(self, *a, **k):
                raise llm.net.NetworkError("offline")
        self.assertFalse(self.worker(Offline()).process(c.id))
        self.assertEqual(self.store.meta(c.id)["memory_upto"], 0, "kept for next time")
        self.assertFalse(self.worker(ScriptedClient(["I cannot answer in JSON"])).process(c.id))
        self.assertEqual(self.store.meta(c.id)["memory_upto"], 2, "a useless answer is not retried forever")

    def test_start_picks_up_leftovers(self):
        c = self.saved(("user", "hello"), ("assistant", "hi"))
        w = self.worker(ScriptedClient([]))
        with mock.patch.object(threading.Thread, "start"):
            w.start()
        self.assertEqual(w.queue.get_nowait(), c.id)


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        for sub in ("Desktop", "Documents"):
            os.makedirs(os.path.join(self.home, sub))
        self.env = mock.patch.dict(os.environ, {"USERPROFILE": self.home, "HOME": self.home})
        self.env.start()
        self.mem = memory.Memory(os.path.join(self.tmp, "memory.json"))
        self.mem.apply({"add": [AR]})
        self.emitted = []

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, client, **cfg_extra):
        cfg = make_cfg(**cfg_extra)
        runner = tools.ToolRunner(cfg, lambda q, d: True, undo_path=os.path.join(self.tmp, "undo.json"))
        return agent.Agent(cfg, client, runner, lambda k, v: self.emitted.append((k, v)), memory=self.mem)

    def test_limits(self):
        self.assertEqual(agent.context_limit(make_cfg()), 1000000)
        cfg = config._merge(config.DEFAULTS, {"provider": "openai_compatible", "openai_compatible": {"api_key": "sk-x"}})
        config._sanitize(cfg, [])
        self.assertEqual(agent.context_limit(cfg), 128000)
        self.assertEqual(agent.context_limit(make_cfg(max_context_tokens=50000)), 50000)

    def test_memory_goes_into_the_instructions(self):
        a = self.make(ScriptedClient(["hi"]))
        self.assertIn(AR, a.system)
        self.assertIn("WHAT YOU REMEMBER", a.system)

    def test_token_count_from_the_service_is_kept(self):
        client = ScriptedClient([llm.LLMResponse([{"type": "text", "text": "hi"}], "end_turn", {
            "input_tokens": 5, "cache_read_input_tokens": 70000, "cache_creation_input_tokens": 100,
            "output_tokens": 20})])
        a = self.make(client)
        a.handle("hello", threading.Event())
        self.assertEqual(a.conv.context_tokens, 70125)

    def test_near_the_limit_it_summarizes_and_goes_on(self):
        client = ScriptedClient(["SUMMARY OF EARLIER PART", "fresh answer"])
        a = self.make(client, max_context_tokens=10000)
        a.conv.messages = [{"role": "user", "content": [{"type": "text", "text": "old"}]},
                           {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}]
        a.conv.context_tokens = 9000  # 90% of the limit
        a.handle("new question", threading.Event())
        summary_call, answer_call = client.calls
        self.assertIn("summary", summary_call["messages"][-1]["content"][0]["text"].lower())
        self.assertEqual(summary_call["tools"], tools.TOOL_DEFS, "same tools: the cached prefix is reused")
        self.assertIn("SUMMARY OF EARLIER PART", answer_call["system"])
        self.assertEqual(len(answer_call["messages"]), 1, "the full old history is no longer sent")
        self.assertEqual(a.conv.summary, "SUMMARY OF EARLIER PART")
        self.assertIn(("notice", S.CONVERSATION_CONTINUED), self.emitted)
        self.assertEqual(self.emitted[-1], ("text", "fresh answer"))

    def test_below_the_limit_nothing_is_summarized(self):
        client = ScriptedClient(["answer"])
        a = self.make(client, max_context_tokens=10000)
        a.conv.messages = [{"role": "user", "content": [{"type": "text", "text": "old"}]},
                           {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}]
        a.conv.context_tokens = 5000
        a.handle("q", threading.Event())
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(client.calls[0]["messages"]), 3)

    def test_continuing_a_saved_conversation(self):
        saved = conversations.Conversation()
        saved.messages = [{"role": "user", "content": [{"type": "text", "text": "earlier"}]},
                          {"role": "assistant", "content": [{"type": "text", "text": "earlier answer"}]}]
        saved.summary = "older summary"
        client = ScriptedClient(["next"])
        a = self.make(client)
        a.new_conversation(saved)
        a.handle("again", threading.Event())
        self.assertEqual(len(client.calls[0]["messages"]), 3)
        self.assertIn("older summary", client.calls[0]["system"])


class CachingTests(unittest.TestCase):
    def test_whole_conversation_is_cached(self):
        client = llm.AnthropicLLM({"api_key": "k", "model": "m"})
        payload = client._payload("sys", [], [{"role": "user", "content": "x"}])
        self.assertEqual(payload["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(payload["system"][0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})

    def test_context_tokens_for_both_services(self):
        self.assertEqual(llm.LLMResponse([], "end_turn", {"prompt_tokens": 100, "completion_tokens": 7})
                         .context_tokens(), 107)
        self.assertIsNone(llm.LLMResponse([], "end_turn", {}).context_tokens())


if __name__ == "__main__":
    unittest.main()
