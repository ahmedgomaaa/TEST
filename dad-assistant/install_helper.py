# -*- coding: utf-8 -*-
"""Steps the installer (HelperSetup.exe) runs with the Python it installed.

    python install_helper.py check-python
    python install_helper.py write-config <settings file, UTF-16>
    python install_helper.py arabic-check
    python install_helper.py test <result file>

Progress for the installer's details list is printed in plain ASCII English (for Ahmed).
Messages for the person installing are written in Arabic to the result file (UTF-16-LE), one per line,
which the installer shows in a message box.  Every command catches its own errors and exits with a code
instead of crashing.
"""

import io
import json
import os
import shutil
import smtplib
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Exit codes for "test" (added together).
AI_FAILED = 1
EMAIL_FAILED = 2
INTERNAL_ERROR = 64

AR = {
    "ai_ok": "• تمام: الاتصال بالذكاء الاصطناعي ({name}) شغّال.",
    "ai_no_key": "• مشكلة: مفتاح الذكاء الاصطناعي (Claude أو DeepSeek) مش مكتوب. المساعد مش هيقدر يرد لحد ما يتكتب.",
    "ai_bad_key": "• مشكلة: مفتاح {name} غلط أو متوقف. اتأكد منه في {site}.",
    "ai_credit": "• مشكلة: رصيد حساب {name} خلص. اشحن الرصيد من {site}.",
    "ai_offline": "• مشكلة: مقدرتش أوصل للإنترنت. اتأكد إن الكمبيوتر متوصل وجرّب تاني.",
    "ai_clock": "• مشكلة: تاريخ أو ساعة الكمبيوتر غلط، فالاتصال الآمن مش شغّال. صلّح التاريخ والساعة.",
    "ai_http": "• مشكلة: خدمة الذكاء الاصطناعي ردّت بخطأ رقم {code}.",
    "ai_other": "• مشكلة: اختبار الذكاء الاصطناعي فشل: {err}",
    "mail_ok": "• تمام: إيميل تجريبي اتبعت لـ {to}.",
    "mail_skipped": "• تقارير الإيميل مش متجهزة (اختياري)، فأحمد مش هيوصله تقارير لما تحصل مشكلة.",
    "mail_auth": "• مشكلة: مقدرتش أبعت إيميل: Gmail رفض الدخول. لازم تستخدم App Password مش الباسورد العادي.",
    "mail_offline": "• مشكلة: مقدرتش أوصل لسيرفر الإيميل (Gmail). اتأكد من الإنترنت، وإن برنامج الحماية مش مانع البرنامج.",
    "mail_other": "• مشكلة: مقدرتش أبعت الإيميل التجريبي: {err}",
    "internal": "• مشكلة: حصل خطأ داخلي أثناء الاختبار. التفاصيل في ملف install.log.",
    "ps_already": "• تمام: PowerShell {version} موجود فعلاً.",
    "ps_restart": "• تمام: PowerShell 5.1 اتثبّت، وهيشتغل بعد إعادة تشغيل الكمبيوتر.",
    "ps_download": "• مشكلة: مقدرتش أحمّل PowerShell 5.1 ({what}). اتأكد من الإنترنت وشغّل برنامج التثبيت تاني.",
    "ps_failed": "• مشكلة: تثبيت {what} فشل (رقم {code}). المساعد هيشتغل عادي بـ PowerShell 2.0.",
    "ps_unsupported": "• مشكلة: PowerShell 5.1 محتاج ويندوز 7 Service Pack 1 أو أحدث.",
}


def say(text):
    """Progress line for the installer's details list (ASCII only, it cannot show anything else)."""
    try:
        print(str(text).encode("ascii", "replace").decode("ascii"))
        sys.stdout.flush()
    except Exception:
        pass


def install_log(text):
    """Full details for Ahmed, next to the app's own logs."""
    try:
        from helper import paths
        paths.ensure_dirs()
        with io.open(os.path.join(paths.LOG_DIR, "install.log"), "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ check-python

def cmd_check_python(_args):
    import ctypes  # noqa: F401  (needs KB2533623 on Windows 7)
    import ssl
    import tkinter
    tkinter.Tcl()  # the Tcl/Tk libraries are really there
    say("Python %s, %s, Tk %s" % (sys.version.split()[0], ssl.OPENSSL_VERSION, tkinter.TkVersion))
    return 0


# ------------------------------------------------------------------ write-config

FIELDS = {
    # key in the installer's file -> path in config.json
    "user_name": ("user_name",),
    "send_to": ("email_reports", "send_to"),
    "smtp_username": ("email_reports", "smtp_username"),
    "smtp_app_password": ("email_reports", "smtp_app_password"),
}


def read_settings_file(path):
    values = {}
    with io.open(path, "r", encoding="utf-16") as f:
        for line in f:
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().lstrip("﻿")] = value.strip()
    return values


def cmd_write_config(args):
    from helper import paths
    values = read_settings_file(args[0])
    if os.path.exists(paths.CONFIG_PATH):
        with io.open(paths.CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            try:
                data = json.load(f)
            except ValueError:
                backup = paths.CONFIG_PATH + ".broken"
                say("config.json was not valid JSON; keeping a copy as config.json.broken")
                shutil.copyfile(paths.CONFIG_PATH, backup)
                data = None
    else:
        data = None
    if not isinstance(data, dict):
        with io.open(paths.CONFIG_EXAMPLE_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    changed = []
    for key, where in FIELDS.items():
        value = values.get(key, "")
        if not value:
            continue  # empty box = keep what is there
        node = data
        for part in where[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[where[-1]] = value
        changed.append(key)
    key = values.get("api_key", "")
    if key:
        # One box for any key: Claude keys start with sk-ant-, anything else is used with DeepSeek.
        from helper import config
        provider = config.detect_provider(key)
        data["provider"] = provider
        section = data.get(provider) if isinstance(data.get(provider), dict) else {}
        section["api_key"] = key
        data[provider] = section
        changed.append("api_key (%s)" % ("Claude" if provider == "anthropic" else "DeepSeek"))
    if values.get("smtp_app_password"):
        # Google shows App Passwords in groups of four ("abcd efgh ijkl mnop"); spaces are not part of it.
        data["email_reports"]["smtp_app_password"] = values["smtp_app_password"].replace(" ", "")
    if values.get("send_to") and not values.get("smtp_username") and not data["email_reports"].get("smtp_username"):
        data["email_reports"]["smtp_username"] = values["send_to"]
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = paths.CONFIG_PATH + ".new"
    # BOM + CRLF: what Notepad on Windows 7 expects, so Ahmed can still edit it by hand.
    with io.open(tmp, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text + "\n")
    os.replace(tmp, paths.CONFIG_PATH)
    say("config.json saved (updated: %s)" % (", ".join(changed) or "nothing"))
    return 0


# ------------------------------------------------------------------ arabic-check

def cmd_arabic_check(_args):
    """Show the same sentence twice (normal and with words reversed) and let the person pick the
    one that reads correctly.  Tk on some systems draws Arabic words in reverse order."""
    import tkinter as tk
    from helper import config, paths, strings, textutil

    sentence = strings.ORDER_CHECK_SENTENCE
    as_is = sentence
    reversed_words = " ".join(reversed(sentence.split()))
    choice = {"value": None}

    root = tk.Tk()
    root.title(strings.ORDER_CHECK_TITLE)
    root.configure(bg="#F4F1EA")
    root.resizable(False, False)
    # The same font and the same kind of text box as the chat window, so the check matches what Dad sees.
    from helper import ui
    font = (ui.FONT, 20)
    for number, text in (("1", as_is), ("2", reversed_words)):
        row = tk.Frame(root, bg="#F4F1EA")
        row.pack(fill="x", padx=24, pady=12)
        tk.Button(row, text=number, font=(ui.FONT, 20, "bold"), width=3, bg="#2E7D32", fg="white",
                  command=lambda n=number: (choice.__setitem__("value", n), root.destroy())).pack(side="right")
        box = tk.Text(row, height=1, width=len(text) + 4, font=font, wrap="word", relief="solid", bd=1,
                      padx=12, pady=8, cursor="arrow")
        box.tag_configure("rtl", justify="right")
        box.insert("1.0", text, "rtl")
        box.configure(state="disabled")
        box.pack(side="right", padx=(0, 12))
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry("+%d+%d" % (x, y))
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.mainloop()

    if choice["value"] is None:
        say("Arabic check: window closed without a choice; keeping the current setting")
        return 1
    # Line 2 is the reversed one: if it reads correctly, the app must reverse the words itself.
    fix = choice["value"] == "2"
    cfg_data = {}
    if os.path.exists(paths.CONFIG_PATH):
        with io.open(paths.CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg_data = json.load(f)
    cfg_data["arabic_word_order_fix"] = fix
    with io.open(paths.CONFIG_PATH + ".new", "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(json.dumps(cfg_data, ensure_ascii=False, indent=2) + "\n")
    os.replace(paths.CONFIG_PATH + ".new", paths.CONFIG_PATH)
    say("Arabic check: line %s chosen -> arabic_word_order_fix = %s" % (choice["value"], fix))
    textutil.REORDER = fix
    config.load()  # make sure the file still loads
    return 0


# ------------------------------------------------------------------ test

def provider_names(cfg):
    """(name, website) of the AI service in use, for the Arabic messages."""
    if cfg["provider"] == "anthropic":
        return "Claude", "console.anthropic.com"
    base = cfg["openai_compatible"].get("base_url") or ""
    if "deepseek" in base:
        return "DeepSeek", "platform.deepseek.com"
    return "AI", base or "?"


def test_ai(cfg):
    from helper import llm, net
    name, site = provider_names(cfg)
    if not cfg[cfg["provider"]].get("api_key"):
        return False, AR["ai_no_key"]
    llm.REQUEST_TIMEOUT = 60
    try:
        client = llm.make_llm(cfg)
        reply = client.create("Reply with the single word OK.", [], [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]}])
        say("AI replied: %r" % reply.text()[:80])
        return True, AR["ai_ok"].format(name=name)
    except llm.ConfigError as e:
        install_log("AI test: config error: %s" % e)
        return False, AR["ai_other"].format(err=e)
    except net.HttpError as e:
        install_log("AI test: HTTP %s: %s" % (e.status, e.body[:2000]))
        body = e.body.lower()
        if e.status in (401, 403):
            return False, AR["ai_bad_key"].format(name=name, site=site)
        if e.status == 402 or "credit" in body or "billing" in body or "balance" in body:
            return False, AR["ai_credit"].format(name=name, site=site)
        return False, AR["ai_http"].format(code=e.status)
    except net.NetworkError as e:
        install_log("AI test: network error: %s" % e)
        return False, AR["ai_clock"] if e.looks_like_clock_problem else AR["ai_offline"]


def test_email(cfg):
    from helper import report
    reporter = report.Reporter(cfg)
    if not reporter.configured():
        return None, AR["mail_skipped"]
    to = cfg["email_reports"]["send_to"]
    try:
        reporter._smtp_send(reporter.build(
            "Test e-mail from the installer",
            "The helper was installed on Dad's laptop and e-mail reports work.", attach_log=False))
        say("Test e-mail sent to %s" % to)
        return True, AR["mail_ok"].format(to=to)
    except Exception as e:
        install_log("E-mail test failed: %r" % e)
        text = str(e)
        if isinstance(e, (OSError, TimeoutError)) and not isinstance(e, smtplib.SMTPException):
            return False, AR["mail_offline"]
        if "535" in text or "Username and Password" in text or "authentic" in text.lower():
            return False, AR["mail_auth"]
        return False, AR["mail_other"].format(err=text[:120])


def cmd_test(args):
    from helper import config, logs
    result_path = args[0]
    lines, code = [], 0
    try:
        cfg, problems = config.load()
        logs.set_secrets(config.secrets(cfg))
        for p in problems:
            install_log("Config problem: %s" % p)
        say("Testing the AI connection...")
        ok, msg = test_ai(cfg)
        lines.append(msg)
        if not ok:
            code |= AI_FAILED
        say("Testing e-mail...")
        ok, msg = test_email(cfg)
        lines.append(msg)
        if ok is False:
            code |= EMAIL_FAILED
    except Exception:
        install_log("Test crashed:\n" + traceback.format_exc())
        lines.append(AR["internal"])
        code |= INTERNAL_ERROR
    with io.open(result_path, "w", encoding="utf-16-le", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    say("Test result code %d" % code)
    return code


# ------------------------------------------------------------------ install-powershell
# PowerShell 5.1 = Windows Management Framework 5.1 (KB3191566), which needs .NET Framework 4.5.2 or newer
# (4.8 is installed).  Downloaded from Microsoft with this Python (its own TLS works on Windows 7),
# each file checked against the SHA-256 below before it runs.
NET48 = ("ndp48-x86-x64-allos-enu.exe",
         "https://download.visualstudio.microsoft.com/download/pr/2d6bb6b2-226a-4baa-bdec-798822606ff1/"
         "8494001c276a4b96804cde7829c04d7f/ndp48-x86-x64-allos-enu.exe",
         "68c9986a8dcc0214d909aa1f31bee9fb5461bb839edca996a75b08ddffc1483f")
WMF51 = {
    "x86": ("Win7-KB3191566-x86.zip",
            "https://download.microsoft.com/download/6/F/5/6F5FF66C-6775-42B0-86C4-47D41F2DA187/Win7-KB3191566-x86.zip",
            "eb7e2c4ce2c6cb24206474a6cb8610d9f4bd3a9301f1cd8963b4ff64e529f563"),
    "x64": ("Win7AndW2K8R2-KB3191566-x64.zip",
            "https://download.microsoft.com/download/6/F/5/6F5FF66C-6775-42B0-86C4-47D41F2DA187/"
            "Win7AndW2K8R2-KB3191566-x64.zip",
            "f383c34aa65332662a17d95409a2ddedadceda74427e35d05024cd0a6a2fa647"),
}
NET48_RELEASE = 528040     # registry "Release" of .NET 4.8
NET452_RELEASE = 379893    # the minimum WMF 5.1 accepts
RESTART = (3010, 1641, 0x00240005, 0x00240006)
PS_RESTART_CODE = 10


def _reg_value(path, name):
    import winreg
    for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), 0):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            continue
    return None


def windows_arch():
    """"x64" or "x86" for Windows itself (this Python is 32-bit, so ask the environment)."""
    arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "x86"
    return "x64" if arch.upper() in ("AMD64", "IA64", "ARM64") else "x86"


def _system_exe(name):
    """A program in the real System32 (not the 32-bit SysWOW64 copy)."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    native = os.path.join(windir, "Sysnative", name)
    return native if os.path.exists(native) else os.path.join(windir, "System32", name)


def download_checked(name, url, sha256, folder):
    """Download (with progress lines) and check the SHA-256.  Returns the file path or raises."""
    from helper import net
    import hashlib
    import urllib.request
    path = os.path.join(folder, name)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == sha256:
                return path
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=net.ssl_context()))
    req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    digest = hashlib.sha256()
    last = -1
    with opener.open(req, timeout=60) as resp, open(path + ".part", "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if total and done * 10 // total != last:
                last = done * 10 // total
                say("Downloading %s: %d%% of %d MB" % (name, last * 10, total // (1024 * 1024)))
    if digest.hexdigest() != sha256:
        os.remove(path + ".part")
        raise ValueError("%s: SHA-256 %s does not match" % (name, digest.hexdigest()))
    os.replace(path + ".part", path)
    return path


def _run(args, what):
    import subprocess
    say("Running %s" % what)
    code = subprocess.call(args)
    install_log("%s exit code %s" % (what, code))
    say("%s exit code %s" % (what, code))
    return code


def cmd_install_powershell(args):
    """Install .NET 4.8 (if needed) and PowerShell 5.1.  0 = done, 10 = done after a restart, 1 = failed."""
    result_path = args[0]
    folder = args[1] if len(args) > 1 else os.path.join(os.environ.get("TEMP", "."), "HelperSetup-ps51")
    lines, code = [], 1

    def finish():
        with io.open(result_path, "w", encoding="utf-16-le", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
        return code

    version = str(_reg_value(r"SOFTWARE\Microsoft\PowerShell\3\PowerShellEngine", "PowerShellVersion") or "")
    if version >= "5.1":
        lines.append(AR["ps_already"].format(version=version))
        code = 0
        return finish()
    os.makedirs(folder, exist_ok=True)
    release = _reg_value(r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", "Release") or 0
    if int(release) < NET452_RELEASE:
        try:
            exe = download_checked(*NET48, folder=folder)
        except Exception as e:
            install_log(".NET 4.8 download failed: %r" % e)
            lines.append(AR["ps_download"].format(what=".NET 4.8"))
            return finish()
        result = _run([exe, "/q", "/norestart"], ".NET Framework 4.8")
        if result != 0 and result not in RESTART:
            lines.append(AR["ps_failed"].format(what=".NET 4.8", code=result))
            return finish()
    try:
        zpath = download_checked(*WMF51[windows_arch()], folder=folder)
        import zipfile
        with zipfile.ZipFile(zpath) as z:
            msu_name = [n for n in z.namelist() if n.lower().endswith(".msu")][0]
            z.extract(msu_name, folder)
        msu = os.path.join(folder, msu_name)
    except Exception as e:
        install_log("WMF 5.1 download failed: %r" % e)
        lines.append(AR["ps_download"].format(what="WMF 5.1"))
        return finish()
    result = _run([_system_exe("wusa.exe"), msu, "/quiet", "/norestart"], "WMF 5.1 (KB3191566)")
    if result == 0x80240017 or result == -2145124329:  # "not applicable": Windows 7 without SP1
        lines.append(AR["ps_unsupported"])
        return finish()
    if result not in (0,) + RESTART:
        lines.append(AR["ps_failed"].format(what="WMF 5.1", code=hex(result & 0xFFFFFFFF)))
        return finish()
    # WMF 5.1 replaces PowerShell itself: it only runs after a restart (wusa reports 3010).
    lines.append(AR["ps_restart"])
    code = PS_RESTART_CODE
    return finish()


COMMANDS = {
    "check-python": cmd_check_python,
    "write-config": cmd_write_config,
    "arabic-check": cmd_arabic_check,
    "test": cmd_test,
    "install-powershell": cmd_install_powershell,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        say("usage: install_helper.py %s" % "|".join(COMMANDS))
        return 2
    try:
        return COMMANDS[argv[0]](argv[1:]) or 0
    except Exception:
        details = traceback.format_exc()
        install_log("%s failed:\n%s" % (argv[0], details))
        say("ERROR in %s: %s" % (argv[0], details.strip().splitlines()[-1]))
        return INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
