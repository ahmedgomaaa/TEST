# Helper: an AI assistant for Windows 7 (32-bit)

A simple chat window that can also **do things on the laptop**: tidy the desktop, sort and rename files, find documents, delete into the Recycle Bin, open programs, find duplicate photos, answer questions and draw pictures. It is built for someone who is not comfortable with computers: big text, big buttons, a Yes/No question before every change, and an Undo button.

It runs on **Python 3.8 (32-bit) + Tkinter** and uses **only the Python standard library**, so nothing has to be installed with pip.

## What Dad sees

- One window with a large chat area, a text box, and a big green **Send** button. Enter sends; Shift+Enter starts a new line.
- **Stop** cancels the current task. **New conversation** clears the chat. **Undo last change** puts moved or renamed files back.
- **Ask Ahmed for help** e-mails you his note plus the log file.
- **A+ / A-** change the text size.
- Before any change, a big question appears, for example "I will put your 34 photos in a folder called Photos on the desktop", with **Yes, go ahead** and **No** buttons.

## One-time setup on the laptop (about 20 minutes)

1. **Windows updates Python needs.** The laptop needs Windows 7 **Service Pack 1** plus update **KB2533623**. If Windows says that update is "not applicable", install its replacement **KB3063858** instead. Without one of them, Python fails to start with an error about `api-ms-win-crt` or `LoadLibrary`.
2. **Install Python 3.8.10, 32-bit.** Download `python-3.8.10.exe` from python.org (the "Windows x86 executable installer"). 3.8.10 is the last 3.8 release with a Windows installer and the last official Python for Windows 7.
   - Use the default "Install Now". Keep "tcl/tk and IDLE" and the "py launcher" ticked; they are on by default.
   - If it complains about the Universal C Runtime, install Windows update **KB2999226** and try again.
3. **Copy the `dad-assistant` folder** to somewhere in his user folder, for example `C:\Users\Dad\Helper`. Don't put it in Program Files.
4. **Double-click `SETUP.bat`.** It:
   - checks Python and Tkinter,
   - creates `config.json` and opens it in Notepad (fill it in, then save and close),
   - tests the AI connection and sends a test e-mail,
   - puts a **Helper** icon on the desktop.

   You can run `SETUP.bat` again at any time; it's safe.

### What to put in `config.json`

| Setting | What it is |
|---|---|
| `user_name` | Dad's name, used in the greeting. |
| `anthropic.api_key` | A Claude API key from console.anthropic.com. **A Claude Pro/Max/Claude Code subscription cannot be used here**; the API is billed separately. Set a monthly spend limit in the console. |
| `anthropic.model` / `effort` | Defaults: `claude-opus-5-5` with `medium` effort. For lower cost, use `claude-sonnet-5-5` or `claude-haiku-5-5`, or lower the effort to `low`. |
| `anthropic.use_fallbacks` | If Claude declines a request, the API retries it on a fallback model automatically. Turned off automatically if your model doesn't support it. |
| `provider` = `openai_compatible` | Use DeepSeek, OpenRouter, etc. instead of Claude. Fill in `openai_compatible.api_key` / `base_url` / `model`. DeepSeek: `https://api.deepseek.com/v1` + `deepseek-chat`. OpenRouter: `https://openrouter.ai/api/v1` + a model name. |
| `images.api_key` | Optional OpenAI API key for "draw me a picture". Claude can't make images. Without it, the helper politely says pictures aren't set up. |
| `email_reports` | `send_to` is your address. `smtp_username` is the Gmail address that sends the reports (it can be the same one). `smtp_app_password` is a **Gmail App Password** (Google Account → Security → 2-Step Verification → App passwords), **not** the normal password. |
| `safety.confirm_changes` | Leave this `true`. |

`config.json` contains secrets. It is git-ignored; never commit or share it. Secrets are also redacted from logs and e-mails.

## E-mail reports you will get

- **Automatic error reports.** The API key is rejected, the credit runs out, a crash, an unexpected error. These are limited to at most one e-mail every 20 minutes, so a broken setting can't flood your inbox.
- **"The assistant closed unexpectedly last time"**, sent when it is restarted after a crash.
- **"Dad asked for help"**, sent when he presses the help button, with his note.
- Every report includes the last part of the log. If the laptop is offline, reports wait in `data/outbox/` and are sent automatically later.

The log records his requests and the actions taken, so you can see what happened. It is stored in `data/logs/` on the laptop.

## Safety rules (enforced in code, not just requested from the AI)

- **No permanent deleting.** Deleting only goes to the Recycle Bin. It is refused on USB sticks and network drives, which have no Recycle Bin. `del`, `rd`, `Remove-Item` and similar commands are blocked.
- **System areas are off limits.** The Windows folder, Program Files, ProgramData, AppData and the app's own folder can never be changed. Desktop, Documents, Pictures and drive roots can't be moved or deleted themselves.
- **Every change is confirmed and can be undone.** Each move, rename or delete shows a Yes/No question first. Moves and renames are recorded in `data/undo.json` so **Undo** can reverse them.
- **No downloads, installs or system changes.** Downloading, installing, registry, firewall, user-account, service, scheduled-task and shutdown commands are blocked.
- **Files are never overwritten.** If a name is taken, a number is added, for example "photo (2).jpg".

## How Windows 7 limitations are handled

| Limitation | What the app does |
|---|---|
| Modern AI tools need Windows 10+ / 64-bit / Node 18+ | Uses Python 3.8 32-bit and the standard library only. Calls the API over plain HTTPS. |
| Old root certificates, no TLS 1.2 by default in .NET / PowerShell 2.0 | Python 3.8 ships its own OpenSSL (TLS 1.2/1.3) **plus a bundled certificate list** (`helper/cacert.pem`), so the OS's TLS settings don't matter. PowerShell is never used for internet access. |
| Wrong clock (dead CMOS battery) breaks HTTPS | Detected. Dad is told to check the date and time, and you get a report. |
| PowerShell **2.0**: no `Invoke-WebRequest`, `ConvertTo-Json`, `Get-FileHash`, `-File`/`-Directory`, `-Raw`, `[pscustomobject]`, `Get-CimInstance`, simplified `Where-Object`, member enumeration... | The AI is given an explicit PowerShell 2.0 rule list. Most jobs use built-in Python tools instead of commands. The command wrapper itself only uses PowerShell 2.0 syntax. |
| Execution policy is `Restricted` (`.ps1` scripts won't run) | Commands are passed with `-EncodedCommand` and `[ScriptBlock]::Create`, which aren't subject to the policy. Syntax errors are caught. |
| Console output uses the OEM code page (garbled Arabic or accented names) | PowerShell output is written to a UTF-8 file by .NET. cmd runs with `/u` (UTF-16). The rest is decoded with the OEM code page. |
| Commands that wait for input hang forever | No stdin, `-NonInteractive`, a time limit (`command_timeout_seconds`), and the whole process tree is killed with `taskkill /T`. |
| A console window flashes up | `CREATE_NO_WINDOW`. The app runs with `pythonw.exe`. |
| Large command output can deadlock pipes | Output goes to temporary files, not pipes. |
| Tk 8.6.9 crashes on emoji (characters above U+FFFF) | All text is cleaned before display, and the AI is told not to use emoji. |
| `sys.stdout` is `None` under `pythonw.exe` | Replaced at startup so nothing crashes on a stray `print`. |
| Notepad on Windows 7 saves JSON with a BOM and needs CRLF line endings | The config is read as `utf-8-sig`. `.gitattributes` forces CRLF for `.bat`/`.json`. |
| `MAX_PATH` (260 characters) | Failures are reported per file. One long path doesn't stop the whole batch. |
| Slow laptop | Searches skip AppData and system folders, and have time limits. Nothing heavy runs in the background. |

## Reliability features

- **Never crashes in front of him.** Every tool, network call and window callback is wrapped. He sees a calm message like "Sorry, something went wrong, please try again", and you get the details by e-mail.
- **Network.** Every request has retries with backoff (2, 4, 8, 16 seconds) and friendly messages when offline.
- **Watchdog.** `Start Assistant.pyw` restarts the app if it crashes. After 3 crashes in 3 minutes it shows a friendly message and e-mails you.
- **One copy at a time.** Opening it twice just says "already open".
- **Conversation history stays valid.** It is append-only. Long conversations start fresh automatically to keep costs down, and a damaged conversation is reset and retried once.

## For developers

- Run the tests with Python 3.8 or newer: `python -m unittest discover -s tests`. They run on Linux; Windows-only parts have fallbacks.
- Code layout:

| File | What it does |
|---|---|
| `helper/ui.py` | The window. |
| `helper/agent.py` | The chat and tool loop, and the system prompt. |
| `helper/tools.py` | The tools and safety rules. |
| `helper/shell.py` | Runs PowerShell 2.0 and cmd commands. |
| `helper/llm.py` | Anthropic and OpenAI-compatible HTTP clients. |
| `helper/net.py` | HTTPS and retries. |
| `helper/report.py` | E-mail reports. |
| `helper/winapi.py` | ctypes calls: Recycle Bin, known folders, mutex. |

- Keep everything **Python 3.8 compatible** and **standard library only**.
- **Not yet tested on a real Windows 7 machine.** The logic, tests and the window were tested on Linux, but Windows-specific parts (Recycle Bin call, shortcut creation, PowerShell 2.0, Arabic text shaping in Tk on Windows) need a first run on the laptop. Check `data/logs/assistant.log` after the first session.
