"""Talking to the AI model over plain HTTPS.

The official Anthropic Python SDK needs a newer Python than Windows 7 can run
(and native packages with no 32-bit Windows builds), so this module calls the
Messages API directly with urllib.

Conversations are stored in the Anthropic message format.  The OpenAI-compatible
client (DeepSeek, OpenRouter, ...) converts to and from that format.
"""

import json
import logging

from . import net

log = logging.getLogger("assistant.llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 16000
REQUEST_TIMEOUT = 300


class ConfigError(Exception):
    pass


class LLMResponse(object):
    def __init__(self, content, stop_reason, usage=None):
        self.content = content or []
        self.stop_reason = stop_reason or "end_turn"
        self.usage = usage or {}

    def text(self):
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    def tool_uses(self):
        return [b for b in self.content if b.get("type") == "tool_use"]


class AnthropicLLM(object):
    def __init__(self, settings):
        self.api_key = settings.get("api_key") or ""
        self.model = settings.get("model") or "claude-opus-5-5"
        self.effort = settings.get("effort") or None
        self.use_fallbacks = bool(settings.get("use_fallbacks", True))
        if not self.api_key:
            raise ConfigError("No Anthropic API key in config.json")

    def _payload(self, system, tools, messages):
        payload = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if self.effort:
            payload["output_config"] = {"effort": self.effort}
        if self.use_fallbacks:
            payload["fallbacks"] = "default"
        return payload

    def _headers(self):
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        if self.use_fallbacks:
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        return headers

    def create(self, system, tools, messages, cancel=None):
        # Optional features are switched off one at a time if the API rejects them
        # (for example after Ahmed changes "model" to one that does not support them).
        for _ in range(3):
            try:
                data = net.post_json(
                    ANTHROPIC_URL, self._payload(system, tools, messages), self._headers(),
                    timeout=REQUEST_TIMEOUT, cancel=cancel,
                )
                break
            except net.HttpError as e:
                body = e.body.lower()
                if e.status == 400 and self.use_fallbacks and ("fallback" in body or "beta" in body):
                    log.warning("Disabling refusal fallbacks: %s", e)
                    self.use_fallbacks = False
                    continue
                if e.status == 400 and self.effort and ("effort" in body or "output_config" in body):
                    log.warning("Disabling effort setting: %s", e)
                    self.effort = None
                    continue
                raise
        else:
            raise net.HttpError(400, "Request rejected after removing optional features")
        return LLMResponse(data.get("content"), data.get("stop_reason"), data.get("usage"))


class OpenAICompatibleLLM(object):
    """DeepSeek, OpenRouter, OpenAI and other /chat/completions APIs."""

    def __init__(self, settings):
        self.api_key = settings.get("api_key") or ""
        self.model = settings.get("model") or "deepseek-chat"
        base = (settings.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        self.url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        if not self.api_key:
            raise ConfigError("No API key for the OpenAI-compatible provider in config.json")

    def create(self, system, tools, messages, cancel=None):
        payload = {
            "model": self.model,
            "max_tokens": 8000,
            "messages": to_openai_messages(system, messages),
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in tools
            ]
        headers = {"Authorization": "Bearer " + self.api_key}
        data = net.post_json(self.url, payload, headers, timeout=REQUEST_TIMEOUT, cancel=cancel)
        return from_openai_response(data)


def to_openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue
        blocks = content or []
        if msg["role"] == "assistant":
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [
                {"id": b["id"], "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                for b in blocks if b.get("type") == "tool_use"
            ]
            entry = {"role": "assistant", "content": text}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        else:
            for b in blocks:
                if b.get("type") == "tool_result":
                    result = b.get("content")
                    if isinstance(result, list):
                        result = "\n".join(x.get("text", "") for x in result if isinstance(x, dict))
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(result or "")})
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            if text:
                out.append({"role": "user", "content": text})
    return out


def from_openai_response(data):
    choices = data.get("choices") or []
    if not choices:
        raise net.NetworkError("The AI service sent an empty answer: %r" % (str(data)[:300],))
    message = choices[0].get("message") or {}
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            arguments = json.loads(fn.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments are not an object")
        except ValueError:
            arguments = {"_invalid_arguments": fn.get("arguments")}
        content.append({"type": "tool_use", "id": call.get("id") or "call", "name": fn.get("name") or "", "input": arguments})
    finish = choices[0].get("finish_reason")
    if any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    elif finish == "length":
        stop = "max_tokens"
    elif finish == "content_filter":
        stop = "refusal"
    else:
        stop = "end_turn"
    return LLMResponse(content, stop, data.get("usage"))


def make_llm(cfg):
    if cfg["provider"] == "openai_compatible":
        return OpenAICompatibleLLM(cfg["openai_compatible"])
    return AnthropicLLM(cfg["anthropic"])
