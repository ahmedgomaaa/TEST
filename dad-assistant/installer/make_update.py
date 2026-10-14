"""Make the automatic-update files (run by build.sh before the installer is built).

    python make_update.py --build N [--commit SHA] [--notes TEXT]

Writes:
- helper/build_info.json   the build number, inside the app (and so inside HelperSetup.exe too)
- dist/helper-app.zip      the app's own files (no config.json, no data/), about 200 KB
- dist/update.json         build number, SHA-256 and size of the zip

The installed helper downloads update.json at every start and installs helper-app.zip when its build
number is higher than its own (see helper/updater.py).  N must grow with every published build; the
GitHub workflow uses its run number.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from helper.updater import MANAGED, REQUIRED  # noqa: E402

SKIP_DIRS = {"__pycache__"}
SKIP_EXT = (".pyc", ".pyo")


def files_to_pack():
    for name in MANAGED:
        src = os.path.join(APP, "installer", "helper.ico") if name == "helper.ico" else os.path.join(APP, name)
        if os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for f in sorted(files):
                    if not f.endswith(SKIP_EXT):
                        full = os.path.join(root, f)
                        yield full, os.path.relpath(full, APP).replace(os.sep, "/")
        elif os.path.isfile(src):
            yield src, name
        else:
            raise SystemExit("missing app file: %s" % src)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=int, required=True)
    ap.add_argument("--commit", default="local")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(APP, "helper", "build_info.json"), "w", encoding="utf-8") as f:
        json.dump({"build": args.build, "commit": args.commit, "date": now}, f, indent=1)

    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)
    zip_path = os.path.join(dist, "helper-app.zip")
    names = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, arc in files_to_pack():
            z.write(full, arc)
            names.append(arc)
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        raise SystemExit("package is missing %s" % missing)
    with open(zip_path, "rb") as f:
        data = f.read()
    manifest = {
        "build": args.build,
        "commit": args.commit,
        "date": now,
        "zip": "helper-app.zip",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "notes": args.notes,
    }
    with open(os.path.join(dist, "update.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("build %d: %d files, %d bytes, sha256 %s" % (args.build, len(names), len(data), manifest["sha256"]))


if __name__ == "__main__":
    main()
