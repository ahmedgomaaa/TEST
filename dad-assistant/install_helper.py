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
    "ai_ok": "• تمام: الاتصال بالذكاء الاصطناعي شغّال.",
    "ai_no_key": "• مشكلة: مفتاح Claude API مش مكتوب. المساعد مش هيقدر يرد لحد ما يتكتب.",
    "ai_bad_key": "• مشكلة: مفتاح Claude API غلط أو متوقف. اتأكد منه في console.anthropic.com.",
    "ai_credit": "• مشكلة: رصيد حساب Claude خلص. اشحن الرصيد من console.anthropic.com.",
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
    "api_key": ("anthropic", "api_key"),
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

def test_ai(cfg):
    from helper import llm, net
    if not cfg[cfg["provider"]].get("api_key"):
        return False, AR["ai_no_key"]
    llm.REQUEST_TIMEOUT = 60
    try:
        client = llm.make_llm(cfg)
        reply = client.create("Reply with the single word OK.", [], [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]}])
        say("AI replied: %r" % reply.text()[:80])
        return True, AR["ai_ok"]
    except llm.ConfigError as e:
        install_log("AI test: config error: %s" % e)
        return False, AR["ai_other"].format(err=e)
    except net.HttpError as e:
        install_log("AI test: HTTP %s: %s" % (e.status, e.body[:2000]))
        body = e.body.lower()
        if e.status in (401, 403):
            return False, AR["ai_bad_key"]
        if e.status == 402 or "credit" in body or "billing" in body or "balance" in body:
            return False, AR["ai_credit"]
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


COMMANDS = {
    "check-python": cmd_check_python,
    "write-config": cmd_write_config,
    "arabic-check": cmd_arabic_check,
    "test": cmd_test,
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
