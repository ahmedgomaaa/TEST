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

log = logging.getLogger("assistant.agent")

MAX_HISTORY_CHARS = 350000  # roughly 90k tokens; start fresh after that to keep costs down

ACTIVITY = {
    "list_folder": "Looking in a folder...",
    "find_files": "Searching for files...",
    "read_file": "Reading a document...",
    "create_folder": "Creating a folder...",
    "move_items": "Moving files...",
    "recycle_items": "Putting things in the Recycle Bin...",
    "open_item": "Opening...",
    "find_duplicate_files": "Looking for duplicate files (this can take a while)...",
    "undo_last_change": "Undoing the last change...",
    "run_command": "Working on the computer...",
    "generate_image": "Drawing your picture (this takes about a minute)...",
}

SYSTEM_TEMPLATE = """You are {name}, a friendly and patient helper on the Windows 7 laptop of {user}. He is 72 years old and not experienced with computers. You can chat, answer questions, and do things on his computer with your tools.

HOW TO TALK
- Use short, simple sentences and everyday words. Avoid technical words.
- Always reply in the language he writes in.
- Plain text only: no Markdown (no **, #, tables or code blocks) and no emoji. Simple numbered lists are fine.
- When you finish a task, say in one or two sentences what you did, e.g. "Done. I put your 34 photos in a new folder called Photos on your desktop."
- If his request is unclear, ask one simple question. Be warm and encouraging.

DOING THINGS ON THE COMPUTER
- Prefer the dedicated tools (list_folder, find_files, read_file, create_folder, move_items, recycle_items, open_item, find_duplicate_files, undo_last_change, generate_image). They are safe and reliable on this old computer. Use run_command only when no dedicated tool can do the job (for example disk space, system information, the list of installed programs).
- Look before you change: list or search first, then act, and act in as few calls as possible (move_items takes many moves at once).
- The app itself shows him a big Yes/No question before every change. So do NOT ask for permission in the chat for normal requests: just call the tool with a clear, simple "explanation" in his language.
- If he answers No, accept it and ask what he would like instead.
- Never delete permanently: deleting is only possible with recycle_items (Recycle Bin).
- Never touch Windows, program folders or settings. Never install, download or uninstall software, and never change system settings, the registry, security settings or user accounts. If he asks for this, explain kindly that Ahmed can help with it.
- When organizing, keep it simple: a few clearly named folders (e.g. Photos, Documents, Videos, Music, Other). Leave program shortcuts (.lnk files) on the desktop unless he asks, because he uses them to open programs.
- Always use full paths. If something goes wrong, explain it simply and suggest the next step. Mistaken moves can be reversed with undo_last_change.

THIS COMPUTER
- Windows 7, 32-bit, slow. Search a specific folder rather than whole drives when you can.
{folders}
- Drives: {drives}
- Today is {today}.

RULES FOR run_command (this is an old system)
- PowerShell is version 2.0. Do NOT use anything newer. Not available: Invoke-WebRequest, Invoke-RestMethod, ConvertTo-Json, ConvertFrom-Json, Get-FileHash, Compress-Archive, Expand-Archive, Get-CimInstance (use Get-WmiObject), [pscustomobject] (use New-Object PSObject -Property @{{...}}), [ordered], $PSItem (use $_), Get-ChildItem -File or -Directory (filter with Where-Object {{ -not $_.PSIsContainer }} or {{ $_.PSIsContainer }}), Get-Content -Raw (use [System.IO.File]::ReadAllText), the short Where-Object syntax without braces, property access on whole lists like $files.Name (use ForEach-Object {{ $_.Name }}), .ForEach() and .Where() methods, Get-Clipboard, Set-Clipboard, New-TemporaryFile, Test-NetConnection, Get-Volume, Get-Disk, Out-File -NoNewline.
- Commands run hidden, without a window or keyboard, with a time limit of {timeout} seconds. Never use anything that waits for input (Read-Host, pause, choice, set /p, prompts) or opens a window. Never use "exit".
- The output is captured as text and shortened if long: select only the columns you need (Select-Object / Format-Table -AutoSize) and limit long lists (Select-Object -First 50).
- Deleting (del, rd, Remove-Item), downloading and installing are blocked; use recycle_items to delete.
- Quote paths with spaces. In PowerShell use -LiteralPath for paths that contain [ or ].
- cmd commands run as cmd /c. Prefer PowerShell for anything complicated.

PICTURES
- You can create pictures with generate_image. Write the prompt in English with plenty of detail.
"""


def _drives_text():
    parts = []
    for root, kind in winapi.drives():
        label = {"fixed": "hard disk", "removable": "USB stick or card", "network": "network drive",
                 "cdrom": "CD/DVD"}.get(kind, kind)
        parts.append("%s (%s)" % (root, label))
    return ", ".join(parts) or "unknown"


def build_system_prompt(cfg, folders):
    folder_lines = "\n".join("- His %s folder: %s" % (k, v) for k, v in folders.items() if k != "Home")
    folder_lines += "\n- His home folder: %s" % folders.get("Home")
    return SYSTEM_TEMPLATE.format(
        name=cfg["assistant_name"],
        user=cfg["user_name"] or "the user",
        folders=folder_lines,
        drives=_drives_text(),
        today=datetime.date.today().strftime("%A %d %B %Y"),
        timeout=cfg["safety"]["command_timeout_seconds"],
    )


class Agent(object):
    def __init__(self, cfg, client, runner, emit, reporter=None):
        """emit(kind, value): kind is 'text', 'activity', 'notice' or 'error'."""
        self.cfg = cfg
        self.client = client
        self.runner = runner
        self.emit = emit
        self.reporter = reporter
        self.messages = []
        self.system = build_system_prompt(cfg, runner.folders)

    def new_conversation(self):
        self.messages = []
        self.system = build_system_prompt(self.cfg, self.runner.folders)

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
            self.emit("notice", "Stopped.")
        except Exception as e:
            log.exception("Unexpected error while handling a message")
            self._close_open_tool_calls("An internal error happened.")
            self._report("Unexpected error: %s" % e)
            self.emit("error", "Sorry, something went wrong on my side. Please try again. "
                               "(Ahmed has been sent a report.)")

    def _handle(self, user_text, cancel):
        if self.messages and len(json.dumps(self.messages)) > MAX_HISTORY_CHARS:
            log.info("Conversation is long; starting a new one.")
            self.new_conversation()
            self.emit("notice", "(This conversation was getting very long, so I started a fresh one.)")
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
                    self.new_conversation()
                    self.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
                    continue
                self._api_error(e)
                return
            except net.NetworkError as e:
                log.warning("Network error: %s", e)
                if e.looks_like_clock_problem:
                    self.emit("error", "I can't connect safely. Please check that the computer's date and time "
                                       "are correct (bottom-right corner of the screen), then try again.")
                    self._report("Certificate problem (clock?): %s" % e)
                else:
                    self.emit("error", "I can't reach the internet right now. Please check the Wi-Fi and try "
                                       "again in a minute.")
                return
            except llm.ConfigError as e:
                self._report("Configuration problem: %s" % e)
                self.emit("error", "I am not set up correctly yet. Ahmed has been sent a message.")
                return

            self.messages.append({"role": "assistant", "content": response.content})
            text = response.text()
            if text:
                log.info("ASSISTANT: %s", text[:2000])
                self.emit("text", text)

            if response.stop_reason == "refusal":
                self.emit("notice", "I'm sorry, I can't help with that one.")
                return
            tool_uses = response.tool_uses()
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    self.emit("notice", "(My answer was too long and got cut off. Ask me to continue.)")
                elif not text:
                    self.emit("notice", "Done.")
                return

            results = []
            for block in tool_uses:
                if cancel.is_set():
                    output, is_error = "Not run: the user pressed Stop.", True
                else:
                    self.emit("activity", ACTIVITY.get(block.get("name"), "Working..."))
                    log.info("TOOL %s %s", block.get("name"), json.dumps(block.get("input"), ensure_ascii=False)[:1500])
                    output, is_error = self.runner.run(block.get("name"), block.get("input"), cancel)
                    log.info("TOOL RESULT (%s): %s", "error" if is_error else "ok", output[:1500])
                results.append({"type": "tool_result", "tool_use_id": block.get("id"),
                                "content": output, "is_error": bool(is_error)})
            self.messages.append({"role": "user", "content": results})
            if cancel.is_set():
                raise net.Cancelled()

        self.emit("notice", "This is taking many steps, so I paused. Tell me if I should continue.")

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
            self.emit("error", "There is a problem with my account settings. Ahmed has been sent a message.")
        elif "credit" in body or "billing" in body or "balance" in body or e.status == 402:
            self._report("Out of API credit (HTTP %s)" % e.status, e.body[:1000])
            self.emit("error", "My account has run out of credit. Ahmed has been sent a message to top it up.")
        elif e.status in net.RETRY_STATUS:
            self.emit("error", "The AI service is very busy right now. Please try again in a few minutes.")
            if e.status >= 500:
                log.warning("Service busy: %s", e)
        else:
            self._report("API error HTTP %s" % e.status, e.body[:2000])
            self.emit("error", "Sorry, something went wrong. Please try again. (Ahmed has been sent a report.)")
