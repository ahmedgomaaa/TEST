"""The conversation loop: send the chat to the model, run the tools it asks for, repeat.

Rules that keep the API from rejecting the conversation:
- History is only ever appended to (never edited), and the model's content is
  stored exactly as returned (thinking blocks included).
- Every tool_use gets a tool_result, even when the user says No, presses Stop,
  or the tool fails.
- When a conversation gets long, a fresh one is started instead of trimming.
"""

import datetime
import json
import logging

from . import llm, net, tools, winapi
from .conversations import Conversation
from . import strings as S

log = logging.getLogger("assistant.agent")

# How much conversation the AI may see.  Claude models read up to 1 million tokens; DeepSeek about 128 000.
# Near the limit the older part is summarized and the conversation simply goes on.
DEFAULT_CONTEXT_TOKENS = {"anthropic": 1000000, "openai_compatible": 128000}
CONTEXT_HEADROOM = 0.85   # summarize when the conversation reaches 85% of the limit
CHARS_PER_TOKEN = 3.0     # rough estimate when the service does not say

SUMMARY_REQUEST = ("(Note from the app, not from him: this conversation is getting too long to keep in full. "
                   "Write a summary of everything so far that you will need to continue: what he asked, what you "
                   "did on the computer (with full paths), what is unfinished, and anything he told you about "
                   "himself. Plain text, in English, as detailed as needed. Do not use any tools.)")

ACTIVITY = S.ACTIVITY

SYSTEM_TEMPLATE = """You are {name}, a friendly and patient helper on the Windows 7 laptop of {user}. He is 72 years old and not experienced with computers. You can chat, answer questions, and do things on his computer with your tools.

HOW TO TALK
- He is Egyptian. Reply in simple Egyptian Arabic (the everyday spoken dialect, written in Arabic letters), not formal Arabic. Only if he writes to you in English, reply in English.
- Use short, simple sentences and everyday words. Avoid technical words. Ahmed (احمد) is his son, who set you up.
- Plain text only: no Markdown (no **, #, tables or code blocks) and no emoji. Simple numbered lists are fine.
- When you finish a task, say in one or two sentences what you did, e.g. "خلصت. حطيت الـ 34 صورة في فولدر جديد اسمه صور على سطح المكتب."
- Give new folders simple Arabic names (for example صور, مستندات, فيديوهات, أغاني, حاجات تانية) unless he asks otherwise.
- If his request is unclear, ask one simple question. Be warm and encouraging.

DOING THINGS ON THE COMPUTER
- Prefer the dedicated tools (list_folder, find_files, read_file, create_folder, move_items, recycle_items, open_item, find_duplicate_files, zip_files, unzip_file, undo_last_change, generate_image). They are safe and reliable on this old computer. Use run_command only when no dedicated tool can do the job (for example disk space, system information, the list of installed programs).
- Look before you change: list or search first, then act, and act in as few calls as possible (move_items takes many moves at once).
- The app itself shows him a big Yes/No question before every change. So do NOT ask for permission in the chat for normal requests: just call the tool with a clear, simple "explanation" in Egyptian Arabic.
- If he answers No, accept it and ask what he would like instead.
- Never delete permanently: deleting is only possible with recycle_items (Recycle Bin).
- Never touch Windows, program folders or settings. Never install, download or uninstall software, and never change system settings, the registry, security settings or user accounts. If he asks for this, explain kindly that Ahmed can help with it.
- When organizing, keep it simple: a few clearly named folders. Leave program shortcuts (.lnk files) on the desktop unless he asks, because he uses them to open programs.
- Always use full paths. If something goes wrong, explain it simply and suggest the next step. Mistaken moves can be reversed with undo_last_change.

THIS COMPUTER
- Windows 7, 32-bit, slow. Search a specific folder rather than whole drives when you can.
{folders}
- Drives: {drives}
- Today is {today}.

RULES FOR run_command
{powershell}
- Commands run hidden, without a window or keyboard, with a time limit of {timeout} seconds. Never use anything that waits for input (Read-Host, pause, choice, set /p, prompts) or opens a window. Never use "exit".
- The output is captured as text and shortened if long: select only the columns you need (Select-Object / Format-Table -AutoSize) and limit long lists (Select-Object -First 50).
- Deleting (del, rd, Remove-Item), downloading and installing are blocked; use recycle_items to delete.
- Quote paths with spaces. In PowerShell use -LiteralPath for paths that contain [ or ].
- cmd commands run as cmd /c. Prefer PowerShell for anything complicated.

PICTURES
- You can create pictures with generate_image. Write the prompt in English with plenty of detail.
"""


PS2_RULES = ("- PowerShell is version 2.0 (Windows 7 default). Do NOT use anything newer. Not available: "
             "Invoke-WebRequest, Invoke-RestMethod, ConvertTo-Json, ConvertFrom-Json, Get-FileHash, Compress-Archive, "
             "Expand-Archive, Get-CimInstance (use Get-WmiObject), [pscustomobject] (use New-Object PSObject -Property "
             "@{...}), [ordered], $PSItem (use $_), Get-ChildItem -File or -Directory (filter with Where-Object "
             "{ -not $_.PSIsContainer } or { $_.PSIsContainer }), Get-Content -Raw (use [System.IO.File]::ReadAllText), "
             "the short Where-Object syntax without braces, property access on whole lists like $files.Name (use "
             "ForEach-Object { $_.Name }), .ForEach() and .Where() methods, Get-Clipboard, Set-Clipboard, "
             "New-TemporaryFile, Test-NetConnection, Get-Volume, Get-Disk, Out-File -NoNewline.\n"
             "- For zip files use the zip_files / unzip_file tools.")

PS5_RULES = ("- PowerShell is version {version} (Windows Management Framework 5.1): the full modern Windows PowerShell. "
             "You can use Compress-Archive / Expand-Archive (zip), Invoke-WebRequest, Invoke-RestMethod, ConvertTo-Json, "
             "ConvertFrom-Json, Get-FileHash, Get-CimInstance, [pscustomobject], [ordered], Get-ChildItem -File/-Directory, "
             "Get-Content -Raw, Get-Clipboard / Set-Clipboard, classes, and member enumeration.\n"
             "- Do not use PowerShell 7-only features: ForEach-Object -Parallel, the ternary ?: operator, && and ||, "
             "?? and ?., or -AsHashtable.")

UNRESTRICTED_RULES = ("- Ahmed has allowed all commands on this computer (no command is blocked). Still: explain what you "
                      "will do first, and prefer recycle_items over permanent deleting.")


def shell_rules(cfg):
    from . import shell
    version = shell.powershell_version()
    rules = PS5_RULES.format(version=version) if version and version >= "5" else PS2_RULES
    if (cfg.get("safety") or {}).get("allow_all_commands"):
        rules += "\n" + UNRESTRICTED_RULES
    return rules


def _drives_text():
    parts = []
    for root, kind in winapi.drives():
        label = {"fixed": "hard disk", "removable": "USB stick or card", "network": "network drive",
                 "cdrom": "CD/DVD"}.get(kind, kind)
        parts.append("%s (%s)" % (root, label))
    return ", ".join(parts) or "unknown"


def build_system_prompt(cfg, folders, memory_text="", summary=""):
    """Built once per conversation (not per message), so the AI service can cache it."""
    folder_lines = "\n".join("- His %s folder: %s" % (k, v) for k, v in folders.items() if k != "Home")
    folder_lines += "\n- His home folder: %s" % folders.get("Home")
    text = SYSTEM_TEMPLATE.format(
        name=cfg["assistant_name"],
        user=cfg["user_name"] or "the user",
        folders=folder_lines,
        drives=_drives_text(),
        today=datetime.date.today().strftime("%A %d %B %Y"),
        timeout=cfg["safety"]["command_timeout_seconds"],
        powershell=shell_rules(cfg),
    )
    if memory_text:
        text += ("\nWHAT YOU REMEMBER FROM EARLIER CONVERSATIONS (written by you; use it naturally, do not "
                 "recite it; it may be out of date)\n" + memory_text + "\n")
    if summary:
        text += "\nEARLIER IN THIS CONVERSATION (summary, the full text was too long)\n" + summary + "\n"
    return text


def context_limit(cfg):
    configured = cfg.get("max_context_tokens")
    try:
        if configured and int(configured) > 1000:
            return int(configured)
    except (TypeError, ValueError):
        pass
    return DEFAULT_CONTEXT_TOKENS.get(cfg["provider"], 128000)


class Agent(object):
    def __init__(self, cfg, client, runner, emit, reporter=None, memory=None):
        """emit(kind, value): kind is 'text', 'activity', 'notice' or 'error'."""
        self.cfg = cfg
        self.client = client
        self.runner = runner
        self.emit = emit
        self.reporter = reporter
        self.memory = memory
        self.limit = context_limit(cfg)
        self.new_conversation()

    @property
    def messages(self):
        return self.conv.messages

    @messages.setter
    def messages(self, value):
        self.conv.messages = value

    def _build_system(self):
        memory_text = ""
        if self.memory is not None:
            try:
                memory_text = self.memory.prompt_text()
            except Exception:
                log.exception("Could not read the memory")
        self.system = build_system_prompt(self.cfg, self.runner.folders, memory_text, self.conv.summary)

    def new_conversation(self, conv=None):
        """Start a new conversation, or continue a saved one."""
        self.conv = conv or Conversation()
        self._build_system()

    def _estimated_tokens(self):
        if self.conv.context_tokens:
            return self.conv.context_tokens
        return int((len(json.dumps(self.messages)) + len(self.system)) / CHARS_PER_TOKEN)

    def _continue_with_summary(self, cancel):
        """The conversation is near the limit: summarize it and go on with the summary instead."""
        log.info("Conversation at about %d tokens (limit %d); summarizing", self._estimated_tokens(), self.limit)
        self.emit("activity", S.SUMMARIZING)
        summary = ""
        try:
            request = self.messages + [{"role": "user", "content": [{"type": "text", "text": SUMMARY_REQUEST}]}]
            reply = self.client.create(self.system, tools.TOOL_DEFS, request, cancel)
            summary = reply.text()
        except (net.HttpError, net.NetworkError) as e:
            log.warning("Summary failed (%s); starting the history fresh", e)
        old = self.conv.summary
        self.conv.summary = ((old + "\n\n") if old else "") + (summary or "(The earlier part could not be summarized.)")
        self.conv.messages = []
        self.conv.context_tokens = 0
        self._build_system()
        self.emit("notice", S.CONVERSATION_CONTINUED)

    def _told(self, short=False):
        """Only promise that Ahmed knows if e-mail reports actually work."""
        try:
            configured = self.reporter is not None and self.reporter.configured()
        except Exception:
            configured = False
        if configured:
            return S.AHMED_REPORT_SHORT if short else S.AHMED_MESSAGE
        return "" if short else S.PLEASE_TELL_AHMED

    def _report(self, summary, details=""):
        log.error("%s %s", summary, details)
        if self.reporter is not None:
            try:
                self.reporter.report_error(summary, details)
            except Exception:
                log.exception("report_error failed")

    def handle(self, user_text, cancel):
        """Process one message from the user.  Never raises."""
        try:
            self._handle(user_text, cancel)
        except net.Cancelled:
            self._close_open_tool_calls("The user pressed Stop.")
            self.emit("notice", S.STOPPED)
        except Exception as e:
            log.exception("Unexpected error while handling a message")
            self._close_open_tool_calls("An internal error happened.")
            self._report("Unexpected error: %s" % e)
            self.emit("error", S.SOMETHING_WRONG_MY_SIDE + self._told(True))

    def _handle(self, user_text, cancel):
        if self.messages and self._estimated_tokens() > self.limit * CONTEXT_HEADROOM:
            self._continue_with_summary(cancel)
        log.info("USER: %s", user_text[:2000])
        self.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
        retried_fresh = False
        for _step in range(self.cfg["max_steps_per_request"]):
            if cancel.is_set():
                raise net.Cancelled()
            try:
                response = self.client.create(self.system, tools.TOOL_DEFS, self.messages, cancel)
            except net.HttpError as e:
                if e.status == 400 and not retried_fresh and "credit" not in e.body.lower():
                    # Most likely a damaged conversation: start fresh with just this message.
                    log.warning("400 from API, retrying with a fresh conversation: %s", e)
                    retried_fresh = True
                    self.conv.messages = []
                    self.conv.context_tokens = 0
                    self.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
                    continue
                self._api_error(e)
                return
            except net.NetworkError as e:
                log.warning("Network error: %s", e)
                if e.looks_like_clock_problem:
                    self.emit("error", S.CHECK_CLOCK)
                    self._report("Certificate problem (clock?): %s" % e)
                else:
                    self.emit("error", S.NO_INTERNET)
                return
            except llm.ConfigError as e:
                self._report("Configuration problem: %s" % e)
                self.emit("error", S.NOT_SET_UP_RIGHT + self._told())
                return

            self.messages.append({"role": "assistant", "content": response.content})
            size = response.context_tokens()
            if size:
                self.conv.context_tokens = size
            text = response.text()
            if text:
                log.info("ASSISTANT: %s", text[:2000])
                self.emit("text", text)

            if response.stop_reason == "refusal":
                self.emit("notice", S.CANT_HELP_WITH_THAT)
                return
            tool_uses = response.tool_uses()
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    self.emit("notice", S.ANSWER_CUT)
                elif not text:
                    self.emit("notice", S.DONE)
                return

            results = []
            for block in tool_uses:
                if cancel.is_set():
                    output, is_error = "Not run: the user pressed Stop.", True
                else:
                    self.emit("activity", ACTIVITY.get(block.get("name"), S.WORKING))
                    log.info("TOOL %s %s", block.get("name"), json.dumps(block.get("input"), ensure_ascii=False)[:1500])
                    output, is_error = self.runner.run(block.get("name"), block.get("input"), cancel)
                    log.info("TOOL RESULT (%s): %s", "error" if is_error else "ok", output[:1500])
                results.append({"type": "tool_result", "tool_use_id": block.get("id"),
                                "content": output, "is_error": bool(is_error)})
            self.messages.append({"role": "user", "content": results})
            if cancel.is_set():
                raise net.Cancelled()

        self.emit("notice", S.MANY_STEPS)

    def _close_open_tool_calls(self, reason):
        """If the last assistant message asked for tools, answer them so the history stays valid."""
        if not self.messages or self.messages[-1].get("role") != "assistant":
            return
        content = self.messages[-1].get("content") or []
        pending = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if pending:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.get("id"), "content": reason, "is_error": True}
                for b in pending]})

    def _api_error(self, e):
        body = e.body.lower()
        if e.status in (401, 403):
            self._report("API key rejected (HTTP %s)" % e.status, e.body[:1000])
            self.emit("error", S.ACCOUNT_PROBLEM + self._told())
        elif "credit" in body or "billing" in body or "balance" in body or e.status == 402:
            self._report("Out of API credit (HTTP %s)" % e.status, e.body[:1000])
            self.emit("error", S.NO_CREDIT + self._told())
        elif e.status in net.RETRY_STATUS:
            self.emit("error", S.SERVICE_BUSY)
            if e.status >= 500:
                log.warning("Service busy: %s", e)
        else:
            self._report("API error HTTP %s" % e.status, e.body[:2000])
            self.emit("error", S.SOMETHING_WRONG + self._told(True))
