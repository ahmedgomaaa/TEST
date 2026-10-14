"""First-time setup, run by SETUP.bat in a console window.  Safe to run again at any time."""

import os
import shutil
import subprocess
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


def say(text=""):
    print(text)


def step(title):
    say()
    say("=" * 60)
    say(title)
    say("=" * 60)


def ask_yes(question):
    try:
        return input(question + " [y/n] ").strip().lower().startswith("y")
    except EOFError:
        return False


def check_python():
    step("1. Checking Python")
    say("Python %s at %s" % (sys.version.split()[0], sys.executable))
    if sys.version_info < (3, 8):
        say("ERROR: Python 3.8 or newer is needed.")
        return False
    try:
        import tkinter
        say("Tkinter (the window toolkit) is available: Tk %s" % tkinter.TkVersion)
    except Exception as e:
        say("ERROR: Tkinter is missing (%s). Re-install Python and keep 'tcl/tk and IDLE' ticked." % e)
        return False
    return True


def ensure_config():
    from helper import paths
    step("2. Settings (config.json)")
    if not os.path.exists(paths.CONFIG_PATH):
        shutil.copyfile(paths.CONFIG_EXAMPLE_PATH, paths.CONFIG_PATH)
        say("Created config.json from config.example.json.")
    say("Notepad will open config.json. Fill in the API key(s) and the e-mail settings,")
    say("then SAVE and CLOSE Notepad to continue.")
    if ask_yes("Open config.json in Notepad now?"):
        notepad = "notepad.exe" if os.name == "nt" else (shutil.which("nano") or "vi")
        subprocess.call([notepad, paths.CONFIG_PATH])
    from helper import config
    cfg, problems = config.load()
    for p in problems:
        say("PROBLEM: " + p)
    return cfg, problems


def test_ai(cfg):
    from helper import llm, net
    step("3. Testing the AI connection")
    try:
        client = llm.make_llm(cfg)
        reply = client.create("Reply with the single word OK.", [], [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]}])
        say("AI replied: %r  -> connection works." % reply.text()[:80])
        return True
    except llm.ConfigError as e:
        say("PROBLEM: %s" % e)
    except net.HttpError as e:
        say("PROBLEM: the AI service answered with error %s:" % e.status)
        say(e.body[:600])
        if e.status in (401, 403):
            say("-> The API key is wrong or disabled.")
    except net.NetworkError as e:
        say("PROBLEM: cannot connect: %s" % e)
        if e.looks_like_clock_problem:
            say("-> Check the computer's date and time.")
    return False


def test_email(cfg):
    from helper import logs, report, config
    step("4. Testing e-mail reports")
    logs.set_secrets(config.secrets(cfg))
    reporter = report.Reporter(cfg)
    if not reporter.configured():
        say("E-mail reports are not configured (send_to / smtp_username / smtp_app_password). Skipping.")
        return False
    if not ask_yes("Send a test e-mail to %s now?" % cfg["email_reports"]["send_to"]):
        return False
    try:
        reporter._smtp_send(reporter.build("Test e-mail", "Setup test: e-mail reports work.", attach_log=False))
        say("Test e-mail sent. Check the inbox (and the spam folder).")
        return True
    except Exception as e:
        say("PROBLEM: could not send e-mail: %s" % e)
        say("-> For Gmail you need an App Password, not the normal password.")
        return False


def _desktop():
    from helper import winapi
    return winapi.known_folders()["Desktop"]


def make_shortcut(cfg):
    step("5. Desktop shortcut")
    if os.name != "nt":
        say("(Not Windows: skipping the shortcut.)")
        return False
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        say("PROBLEM: pythonw.exe not found next to %s" % sys.executable)
        return False
    launcher = os.path.join(APP_DIR, "Start Assistant.pyw")
    link = os.path.join(_desktop(), "%s.lnk" % cfg["assistant_name"])
    icon = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shell32.dll")

    def vbs_str(s):
        """A VBScript string expression using only ASCII (non-ASCII characters become ChrW(n)),
        so the script works no matter how cscript reads the file."""
        parts, run = [], ""
        for ch in s:
            if ord(ch) < 128:
                run += '""' if ch == '"' else ch
            else:
                if run:
                    parts.append('"%s"' % run)
                    run = ""
                parts.append("ChrW(%d)" % ord(ch))
        if run or not parts:
            parts.append('"%s"' % run)
        return " & ".join(parts)

    script = "\r\n".join([
        'Set sh = CreateObject("WScript.Shell")',
        "Set lnk = sh.CreateShortcut(%s)" % vbs_str(link),
        "lnk.TargetPath = %s" % vbs_str(pythonw),
        "lnk.Arguments = %s" % vbs_str('"%s"' % launcher),
        "lnk.WorkingDirectory = %s" % vbs_str(APP_DIR),
        "lnk.IconLocation = %s" % vbs_str(icon + ",23"),
        "lnk.Description = %s" % vbs_str("Your computer helper"),
        "lnk.Save",
    ])
    fd, vbs = tempfile.mkstemp(suffix=".vbs")
    os.close(fd)
    try:
        with open(vbs, "w", encoding="ascii", newline="\r\n") as f:
            f.write(script)
        code = subprocess.call(["cscript", "//nologo", vbs])
    except OSError as e:
        code = str(e)
    finally:
        os.remove(vbs)
    if os.path.exists(link):
        say("Shortcut created: %s" % link)
        return True
    # Fallback: a small .pyw file on the desktop also starts the helper when double-clicked.
    starter = os.path.join(_desktop(), "%s.pyw" % cfg["assistant_name"])
    with open(starter, "w", encoding="utf-8") as f:
        f.write("# Starts the helper.\nimport runpy\nrunpy.run_path(%r, run_name='__main__')\n" % launcher)
    say("Could not create a normal shortcut (cscript: %s), so I put %s on the desktop instead." % (code, starter))
    return True


def main():
    say("Helper setup")
    ok = check_python()
    if ok:
        cfg, problems = ensure_config()
        ai_ok = test_ai(cfg) if not problems else False
        test_email(cfg)
        make_shortcut(cfg)
        step("Finished")
        if ai_ok:
            say("Everything important works. Double-click the '%s' icon on the desktop." % cfg["assistant_name"])
        else:
            say("The AI connection is NOT working yet. Fix config.json and run SETUP.bat again.")
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
