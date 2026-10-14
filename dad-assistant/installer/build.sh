#!/bin/sh
# Builds installer/dist/HelperSetup.exe (one file that installs everything).
#
# Needs: makensis (NSIS 3, "apt install nsis" on Linux or the Windows installer from nsis.sourceforge.io),
# curl and sha256sum.  The three large prerequisites are downloaded once into installer/payload/ (not in
# git) and checked against the hashes below, so a damaged or tampered download is never packaged.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PAYLOAD="$HERE/payload"
mkdir -p "$PAYLOAD" "$HERE/dist"

fetch() {  # fetch <file name> <url> <sha256>
    file="$PAYLOAD/$1"
    if [ ! -f "$file" ] || ! echo "$3  $file" | sha256sum -c --status; then
        echo "Downloading $1 ..."
        curl -fL --retry 4 -o "$file.part" "$2"
        mv "$file.part" "$file"
    fi
    if ! echo "$3  $file" | sha256sum -c --status; then
        echo "ERROR: $1 does not have the expected SHA-256; refusing to build." >&2
        rm -f "$file"
        exit 1
    fi
}

# Python 3.8.10 32-bit: the last official Python for Windows 7 (signed by the Python release manager).
fetch python-3.8.10.exe \
    https://www.python.org/ftp/python/3.8.10/python-3.8.10.exe \
    ad07633a1f0cd795f3bf9da33729f662281df196b4567fa795829f3bb38a30ac

# KB2758857 (Microsoft, signed): supersedes KB2533623, which Python 3.8 needs on Windows 7 and which Microsoft
# no longer offers.  Only installed when the laptop is missing it.
fetch kb2758857-x86.msu \
    https://catalog.s.download.windowsupdate.com/msdownload/update/software/secu/2012/10/windows6.1-kb2758857-x86_48f788c1ff97e942eb12358a21abdc40d8b24b8d.msu \
    66b91cc289c7a08da84000e049402a08e3c7d950f84747b05c119aa44541ee05
fetch kb2758857-x64.msu \
    https://catalog.s.download.windowsupdate.com/msdownload/update/software/secu/2012/10/windows6.1-kb2758857-x64_b717b4e2168ab84dda2eb17c71a4a1adee502ba6.msu \
    8c62047ecb6fc0cd0620538fb9ef50fa69aa86a8a4e34988a9929e56f3a1c1f8

makensis -V2 -INPUTCHARSET UTF8 "$HERE/HelperSetup.nsi"
echo "Built: $HERE/dist/HelperSetup.exe"
sha256sum "$HERE/dist/HelperSetup.exe"
