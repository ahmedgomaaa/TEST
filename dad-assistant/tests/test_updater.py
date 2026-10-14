"""Automatic updates (helper/updater.py) against a local HTTPS server standing in for GitHub releases.

Each test builds a fake installed app (a copy of the real app files with build 1) in a temp folder,
publishes a package, and runs the same entry point the launcher uses.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, HERE)

import fakes  # noqa: E402
from helper import net, paths, updater  # noqa: E402

PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy")


def app_files():
    """{archive name: bytes} for the real app's managed files."""
    out = {}
    for name in updater.MANAGED:
        src = os.path.join(APP_DIR, "installer", "helper.ico") if name == "helper.ico" else os.path.join(APP_DIR, name)
        if os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if not f.endswith(".pyc") and f != "build_info.json":
                        full = os.path.join(root, f)
                        with open(full, "rb") as fh:
                            out[os.path.relpath(full, APP_DIR).replace(os.sep, "/")] = fh.read()
        else:
            with open(src, "rb") as fh:
                out[name] = fh.read()
    return out


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="updater-test-")
        self.app = os.path.join(self.tmp, "Helper app")
        self.data = os.path.join(self.app, "data")
        files = app_files()
        files["helper/build_info.json"] = json.dumps({"build": 1}).encode()
        for name, data in files.items():
            dest = os.path.join(self.app, *name.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
        with open(os.path.join(self.app, "config.json"), "w") as f:
            f.write('{"user_name": "keep me"}')
        os.makedirs(os.path.join(self.data, "logs"))
        with open(os.path.join(self.data, "logs", "assistant.log"), "w") as f:
            f.write("old log")

        bundle = os.path.join(self.tmp, "bundle.pem")
        with open(bundle, "wb") as out:
            for name in (paths.CA_BUNDLE, fakes.TEST_CA):
                with open(name, "rb") as f:
                    out.write(f.read() + b"\n")
        update_dir = os.path.join(self.data, "update")
        self.patches = [
            mock.patch.dict(os.environ, dict({n: "" for n in PROXY_VARS}, NO_PROXY="*", no_proxy="*")),
            mock.patch.object(paths, "CA_BUNDLE", bundle),
            mock.patch.object(paths, "APP_DIR", self.app),
            mock.patch.multiple(updater, BUILD_INFO=os.path.join(self.app, "helper", "build_info.json"),
                                UPDATE_DIR=update_dir, STAGING_DIR=os.path.join(update_dir, "staging"),
                                BACKUP_DIR=os.path.join(update_dir, "backup"),
                                PENDING_FILE=os.path.join(update_dir, "pending.json"),
                                NOTICE_FILE=os.path.join(update_dir, "notice.json"),
                                BAD_BUILDS_FILE=os.path.join(update_dir, "bad_builds.json")),
        ]
        for p in self.patches:
            p.start()
        net._context = None
        self.server = fakes.FakeFiles()

    def tearDown(self):
        self.server.close()
        for p in self.patches:
            p.stop()
        net._context = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def publish(self, build, files=None, tamper=None, manifest_extra=None):
        files = dict(files if files is not None else app_files())
        files["helper/build_info.json"] = json.dumps({"build": build}).encode()
        data = make_zip(files)
        manifest = {"build": build, "zip": "helper-app.zip", "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "commit": "abc123", "notes": "fix"}
        manifest.update(manifest_extra or {})
        self.server.files["/rel/helper-app.zip"] = tamper(data) if tamper else data
        self.server.files["/rel/update.json"] = json.dumps(manifest).encode()

    def cfg(self, enabled=True):
        return {"updates": {"enabled": enabled, "manifest_url": self.server.url + "/rel/update.json"}}

    def run_update(self):
        return updater.run_at_start(self.cfg(), show_splash=False)

    def build_now(self):
        return updater.current_build()

    def assert_untouched_settings(self):
        with open(os.path.join(self.app, "config.json")) as f:
            self.assertEqual(f.read(), '{"user_name": "keep me"}')
        with open(os.path.join(self.data, "logs", "assistant.log")) as f:
            self.assertEqual(f.read(), "old log")

    def test_installs_newer_build_and_keeps_settings(self):
        files = app_files()
        files["README.md"] = b"new readme"
        self.publish(2, files)
        self.assertEqual(self.run_update(), "updated to build 2")
        self.assertEqual(self.build_now(), 2)
        with open(os.path.join(self.app, "README.md"), "rb") as f:
            self.assertEqual(f.read(), b"new readme")
        self.assert_untouched_settings()
        self.assertEqual(updater.pending()["to"], 2)
        notice = updater.take_notice()
        self.assertEqual((notice["kind"], notice["from"], notice["to"]), ("updated", 1, 2))
        self.assertIsNone(updater.take_notice(), "the notice is shown only once")
        self.assertFalse(os.path.exists(updater.STAGING_DIR))
        self.assertTrue(os.path.isfile(os.path.join(updater.BACKUP_DIR, "helper", "__init__.py")))

    def test_same_build_does_nothing(self):
        self.publish(1)
        self.assertEqual(self.run_update(), "up to date (build 1)")
        self.assertEqual(self.server.requests, ["/rel/update.json"], "the package is not downloaded")

    def test_disabled_and_offline(self):
        self.publish(2)
        self.assertEqual(updater.run_at_start(self.cfg(enabled=False), show_splash=False), "no update information")
        cfg = {"updates": {"enabled": True, "manifest_url": "https://localhost:1/update.json"}}
        started = time.time()
        self.assertEqual(updater.run_at_start(cfg, show_splash=False), "no update information")
        self.assertLess(time.time() - started, 15)
        self.assertEqual(self.build_now(), 1)

    def test_corrupted_download_is_refused(self):
        self.publish(2, tamper=lambda data: data[:-10] + b"0123456789")
        self.assertIn("update failed", self.run_update())
        self.assertEqual(self.build_now(), 1)
        self.assert_untouched_settings()

    def test_code_that_does_not_compile_is_refused(self):
        files = app_files()
        files["helper/ui.py"] = b"def broken(:\n"
        self.publish(2, files)
        result = self.run_update()
        self.assertIn("does not compile", result)
        self.assertEqual(self.build_now(), 1)
        with open(os.path.join(self.app, "helper", "ui.py"), "rb") as f:
            self.assertNotIn(b"broken", f.read())

    def test_paths_outside_the_app_are_refused(self):
        for bad in ("../evil.py", "config.json", "data/logs/x.txt", "C:/Windows/evil.dll"):
            files = app_files()
            files[bad] = b"x"
            self.publish(2, files)
            self.assertIn("update failed", self.run_update(), bad)
            self.assertEqual(self.build_now(), 1, bad)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.py")))
        self.assert_untouched_settings()

    def test_incomplete_package_is_refused(self):
        files = app_files()
        del files["helper/__main__.py"]
        self.publish(2, files)
        self.assertIn("incomplete", self.run_update())
        self.assertEqual(self.build_now(), 1)

    def test_rollback_restores_and_never_reinstalls_that_build(self):
        self.publish(2)
        self.run_update()
        self.assertEqual(self.build_now(), 2)
        self.assertEqual(updater.rollback(), 1)
        self.assertEqual(self.build_now(), 1)
        self.assertIsNone(updater.pending())
        self.assert_untouched_settings()
        self.assertEqual(updater.take_notice()["kind"], "rolled_back")
        self.assertEqual(self.run_update(), "up to date (build 1)")  # build 2 is marked bad
        self.publish(3)  # a newer fix is installed again
        self.assertEqual(self.run_update(), "updated to build 3")

    def test_started_version_is_not_rolled_back(self):
        self.publish(2)
        self.run_update()
        updater.confirm_started()
        self.assertIsNone(updater.pending())

    def test_failed_swap_puts_the_old_version_back(self):
        self.publish(2)
        real_move = shutil.move
        calls = {"n": 0}

        def flaky_move(src, dst):
            calls["n"] += 1
            if calls["n"] == len(updater.MANAGED) + 3:  # fail while moving the new files in
                raise OSError("disk full")
            return real_move(src, dst)
        with mock.patch.object(updater.shutil, "move", side_effect=flaky_move):
            self.assertIn("could not replace", self.run_update())
        self.assertEqual(self.build_now(), 1)
        for name in ("helper/__init__.py", "helper/ui.py", "Start Assistant.pyw", "README.md"):
            self.assertTrue(os.path.exists(os.path.join(self.app, *name.split("/"))), name)
        self.assert_untouched_settings()


if __name__ == "__main__":
    unittest.main()
