# Helper (المساعد): an Arabic AI assistant for Windows 7

A simple chat window, **in Egyptian Arabic**, that can also **do things on the laptop**: tidy the desktop, sort and rename files, find documents, delete into the Recycle Bin, open programs, find duplicate photos, answer questions and draw pictures. It is built for someone who is not comfortable with computers: big text, big buttons, a Yes/No question before every change, and an Undo button.

It runs on **Python 3.8 (32-bit) + Tkinter** and uses **only the Python standard library**. **`HelperSetup.exe` installs everything**, including Python.

## What Dad sees

Everything is in Arabic and laid out right to left. The AI answers in simple Egyptian Arabic, or in English if he writes in English.

- One window with a large chat area, a text box, and a big green **إرسال** (Send) button. Enter sends; Shift+Enter starts a new line.
- **إيقاف** (Stop) cancels the current task. **محادثة جديدة** (New conversation) clears the chat. **تراجع عن آخر تغيير** (Undo last change) puts moved or renamed files back.
- **اطلب مساعدة أحمد** (Ask Ahmed for help) e-mails you his note plus the log file.
- **A+ / A-** change the text size.
- Before any change, a big question appears, for example "هحط الـ 34 صورة في فولدر جديد اسمه صور", with **أيوه، كمّل** (Yes, go ahead) and **لأ** (No) buttons.
- The typing box is a real Windows text box set to right-to-left, the same one Notepad uses. Arabic typing, the caret, selection, Backspace and mouse clicks behave as he expects. Tk's own box drew the caret on the wrong side, so letters landed in the wrong place. Ctrl+A selects everything.
- Chat lines are arranged for right-to-left reading. Punctuation at the ends and English words (for example "Google Chrome") are placed where an Arabic reader expects them, because Tk can only draw left-to-right lines.
- Messages for you (e-mail reports, logs) stay in English. All of Dad's texts are in `helper/strings.py`, so wording can be changed in one place.

## Installing: one file, `HelperSetup.exe`

Copy `HelperSetup.exe` to the laptop and double-click it. It asks for administrator rights once, then:

1. **Checks Windows.** Windows 7 **Service Pack 1** or newer is needed. On Windows XP/Vista, or Windows 7 without SP1, it explains this in Arabic and changes nothing.
2. **Installs the Windows update Python needs, if it's missing.** Python 3.8 needs KB2533623 on Windows 7. Microsoft no longer offers that update, so setup bundles **KB2758857**, which replaces it (Microsoft-signed, x86 and x64). After it is installed the laptop must restart. Setup offers to restart and then continues by itself through a "highest privileges" log-on task. A **كمّل تثبيت المساعد** (continue installing) icon on the desktop is the fallback.
3. **Installs Python 3.8.10 32-bit** for this user, silently, if no working Python 3.8 is found. A broken one is repaired first. Python is bundled (official installer, signature checked when building), so **no internet is needed for installing**.
4. **Settings page:** Dad's name, the AI key (**Claude or DeepSeek**: one box, and the type is detected from the key: `sk-ant-...` is Claude, any other key is DeepSeek), your e-mail for reports, the Gmail address that sends (empty means your own address) and its App Password. On a reinstall, leave a box empty to keep the old value. Your e-mail is checked for an `@`.
5. **Arabic check:** shows the same Arabic sentence twice, numbered 1 and 2, in the same font and kind of text box as the chat. You click the number of the one that reads correctly. This sets `arabic_word_order_fix` (see below). It runs only on the first install.
6. **Shortcuts:** an **المساعد** icon on the desktop and in the Start menu. Both start the helper with `pythonw.exe` directly, without relying on file associations. There is also an uninstaller in "Add or remove programs".
7. **Tests** the AI connection (Claude or DeepSeek) and sends a test e-mail, then shows the result in Arabic: for example a wrong key, no credit, no internet, a wrong clock or a rejected Gmail password.

Every step is logged to `%TEMP%\HelperSetup.log`, which is copied to `data\logs\setup.log` next to the app. Every failure shows a short Arabic message and where the log is. The app goes to `%LOCALAPPDATA%\Programs\Helper`.

**Running it again is safe.** It keeps `config.json` and `data\`, reuses Python, and asks you to close the helper first if it is open. That's also how you change the API key or e-mail later. **Uninstalling** asks whether to keep the settings and logs (default: keep) and leaves Python installed.

## Automatic updates (fix things remotely)

Every time the helper starts, the launcher checks `update.json` in the GitHub release `helper-installer-latest` (8-second limit; offline means it just starts). If the build number there is higher than its own, it:

1. shows a small "بحدّث المساعد..." (updating) window,
2. downloads `helper-app.zip` (about 230 KB: the app's own files only),
3. checks its size and SHA-256, refuses any file outside the app's own files, and compiles every `.py` file,
4. swaps the files, keeping the old version in `data\update\backup`. If the swap fails half-way, the old version is put back at once.

`config.json` and `data\` are never touched. The chat then says "(المساعد اتحدّث لنسخة جديدة رقم N.)" (updated to build N), and you get an e-mail "Updated to build N".

**If the new version crashes twice right after updating**, the launcher automatically puts the previous version back and remembers that build as bad, so it isn't downloaded again. Dad sees a calm note, and you get an e-mail "Update to build N failed, rolled back". To fix it, push a newer build.

**How to send Dad a fix:** commit and push to `ccr-2106592e-sihkos` (or the default branch). The GitHub workflow runs the tests, builds `HelperSetup.exe`, `helper-app.zip` and `update.json` with build number = the workflow's run number, and publishes them. The next time Dad opens the helper, he has the fix. Pushes to other branches build and test but don't publish.

What an update can't change: Python itself and Windows updates. Those still need `HelperSetup.exe`, which is safe to run again. To turn updates off, set `"updates": {"enabled": false}` in `config.json`.

**Trust:** updates come only from this repository's release, over HTTPS checked against the bundled certificates. Anyone who can push to the repository can therefore run code on Dad's laptop, so protect the GitHub account with two-factor authentication.

### Building `HelperSetup.exe`

Run `installer/build.sh` on Linux (`apt install nsis`) or with NSIS 3 on Windows. It:

- downloads Python 3.8.10 and the two KB2758857 packages into `installer/payload/` (git-ignored),
- **refuses to build** if a SHA-256 hash doesn't match,
- writes `installer/dist/HelperSetup.exe` (about 43 MB).

For a silent test install, use `HelperSetup.exe /S /NAME=... /APIKEY=... /EMAIL=... /SENDER=... /APPPASS=... [/NOTEST]`.

### Manual install (alternative)

The old way still works:

1. Install the Windows updates and Python 3.8.10 32-bit yourself.
2. Copy this folder to the laptop.
3. Run `SETUP.bat`. It checks Python, creates `config.json`, tests the connection and makes a shortcut.

### What to put in `config.json`

| Setting | What it is |
|---|---|
| `user_name` | Dad's name, used in the greeting ("أهلاً يا ..."). |
| `assistant_name` | Empty means **المساعد**. |
| `arabic_word_order_fix` | Set by the installer's Arabic check. Tk draws Arabic in reverse word order when the font has no Arabic letters (each word is drawn separately). The app uses **Tahoma**, which has Arabic on every Windows 7, so this is normally `false`. Set it to `true` only if Arabic words show in reverse order. |
| `anthropic.api_key` | A Claude API key from console.anthropic.com. **A Claude Pro/Max/Claude Code subscription cannot be used here**; the API is billed separately. Set a monthly spend limit in the console. |
| `anthropic.model` / `effort` | Defaults: `claude-opus-5-5` with `medium` effort. For lower cost, use `claude-sonnet-5-5` or `claude-haiku-5-5`, or lower the effort to `low`. |
| `anthropic.use_fallbacks` | If Claude declines a request, the API retries it on a fallback model automatically. Turned off automatically if your model doesn't support it. |
| Automatic provider | You don't have to set `provider` by hand. A DeepSeek key put in the Claude slot (`anthropic.api_key`) is moved to `openai_compatible` and used with DeepSeek. If only one provider has a key, that one is used. |
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

## Testing

How to run the tests:

- **Any OS:** `python -m unittest discover -s tests` with Python 3.8 or newer.
- **PowerShell wrapper outside Windows:** set `PWSH=/path/to/pwsh` to run it against PowerShell 7.
- **On Windows:** the same command also runs the Windows-only tests (`test_windows.py`, `test_launcher.py`).

### Arabic version and installer (verified on Wine 9.0, Windows 7 SP1 32-bit mode)

- **Tests:** all 85 pass on the official Windows Python 3.8.10 32-bit (8 skipped because they need a real `powershell.exe`) and on Linux Python 3.13.
- **Full install on a brand-new Windows 7 profile with no Python**, driven through the real installer window: welcome page, settings page (Arabic name typed in, a bad e-mail rejected with an Arabic message), silent Python 3.8.10 install, Python check (OpenSSL 1.1.1k, Tk 8.6), `config.json` written, Arabic check window, shortcuts with Arabic names, uninstall entry, test step (the real Claude API was reached and a fake key was reported as wrong, in Arabic), finish page, and the app opening in Arabic.
- **Reinstall over an existing install:** settings kept byte-for-byte, Python reused, no Arabic check.
- **Windows update path** (test build that simulates wusa's "restart needed", because Wine cannot install Microsoft packages): the Arabic explanation and restart question, and the resume copy and desktop icon. Running it after the "restart" with `/AFTERREBOOT` finished the install and removed the icon. The real Wine `wusa` failure was reported with its error code, and setup stopped without changing anything.
- **Uninstall:** app files, shortcuts and the registry entry removed; settings, logs and Python kept.
- **App:** the main window, the Yes/No question (with Arabic file names) and error messages all read right to left. Sending a message with a fake key showed "في مشكلة في إعدادات الحساب بتاعي" and logged the API's 401.

### What was verified on Windows 7 SP1 32-bit (Wine 9.0, Windows 7 mode, official Python 3.8.10 32-bit)

All tests passed at the time: 76 on Windows Python, 8 skipped because they need a real `powershell.exe`. On top of that, the real app was driven through its window with simulated mouse and keyboard.

**The app itself**
- **Launch:** `Start Assistant.pyw` under `pythonw.exe`, installed in a folder with an Arabic name and spaces, and a `config.json` saved with a Notepad-style BOM.
- **Tidy-up conversation:** a full conversation against a local fake Claude API. Request → AI lists the desktop → big Yes/No question → files moved → reply shown. Emoji are stripped safely and Markdown is cleaned.
- **Stop:** works immediately while the AI is still thinking, and the late answer is discarded.
- **Undo button:** puts files back and removes folders it created.
- **"Ask Ahmed for help":** delivered over TLS to a fake Gmail SMTP server. The log is attached as UTF-8, and the API key and app password are redacted.

**Crashes and restarts**
- **Crash recovery:** a native access-violation crash makes the launcher restart the app.
- **Repeated failures:** after 3 failures it shows a friendly message and emails "cannot start".
- **Double start:** a second launch says "already open".
- **Clean close:** Alt+F4 closes normally and nothing is left running.
- **Crash email on restart:** the next start emails "closed unexpectedly".

**Windows parts**
- **Recycle Bin:** the `SHFileOperationW` structure has the correct 32-bit layout (30 bytes), and files and folders with Arabic names are recycled.
- **cmd.exe:** Arabic file names come back intact, quoting and spaces work, and exit codes are kept.
- **Large output:** 20,000 lines produce no deadlock, and both the start and the end are kept.
- **Hung commands:** they are killed, `set /p` doesn't hang, and Stop works.
- **pythonw:** runs fine with no console.
- **SETUP.bat:** runs under `cmd.exe`, finds Python through the `py` launcher, and creates the config. It creates the desktop shortcut, or a `Helper.pyw` starter if shortcut creation fails; that starter works by double-click.

**HTTPS and email**
- Python 3.8.10's own OpenSSL 1.1.1k handles TLS.
- An untrusted certificate is refused with a "check the date and time" message.
- Retries on 429 errors, plus clear messages for a bad key, no credit, and no internet.

### Bugs these tests found, all fixed

- Moving files "to" a folder that didn't exist yet renamed the first file to the folder's name.
- Command output over 400 KB lost its end, which is where results and errors usually are.
- cmd output that was mostly Arabic came out garbled.
- Stop didn't work while waiting for the AI, and the late answer still appeared.
- PowerShell output printed before an error was lost.
- A native crash could show Windows' "stopped working" box, and Wine reported exit code 0, so the app wasn't restarted.
- Arabic was garbled in the emailed log.
- Arabic sentences showed in reverse word order. Tk draws each word separately when the font has no Arabic letters, so the app can reverse the words itself (`arabic_word_order_fix`). The Arabic version uses Tahoma, which has Arabic letters. With it, Wine showed every widget in the correct order without the fix, so the default is now off and the installer's check confirms it on the laptop.
- Main folders weren't protected when the profile path and Windows' reported folder differ.

### Not verified, so check on the real laptop

- **PowerShell 2.0:** Wine has no real PowerShell. The wrapper was checked against PowerShell 7 and uses only PowerShell 2.0 constructs.
- **Arabic word order on real Windows:** the installer's check sets it. If Arabic looks reversed later, flip `arabic_word_order_fix` in `config.json`.
- **Typing Arabic with the keyboard:** Tk accepted Arabic characters sent as Windows keyboard messages. The test tool's simulated Arabic keystrokes reached Tk as "?" under Wine (they did reach the installer's native boxes), so try typing a few Arabic words on the laptop.
- **Gmail:** e-mail could not leave the test environment. The installer's test step sends a real test e-mail on the laptop.
- **The real KB2758857 install, the restart and the log-on task:** Wine cannot install Microsoft packages or create log-on tasks. If the task does not start setup after the restart, double-click "كمّل تثبيت المساعد" on the desktop.

## For developers

- Code layout:

| File | What it does |
|---|---|
| `helper/ui.py` | The window. |
| `helper/agent.py` | The chat and tool loop, and the system prompt. |
| `helper/tools.py` | The tools and safety rules. |
| `helper/shell.py` | Runs PowerShell 2.0 and cmd commands. |
| `helper/llm.py` | Anthropic and OpenAI-compatible HTTP clients. |
| `helper/net.py` | HTTPS, retries and Stop. |
| `helper/report.py` | E-mail reports. |
| `helper/winapi.py` | ctypes calls: Recycle Bin, known folders, mutex, crash dialogs. |
| `helper/strings.py` | Every text Dad sees, in Egyptian Arabic. |
| `install_helper.py` | The steps the installer runs with the new Python: settings, Arabic check, tests. |
| `installer/HelperSetup.nsi`, `installer/build.sh` | The one-file installer. |

- Keep everything **Python 3.8 compatible** and **standard library only**. Source files are ASCII, except the Arabic texts (`helper/strings.py`, `install_helper.py`, the examples in the system prompt and the installer script). All files are UTF-8.
