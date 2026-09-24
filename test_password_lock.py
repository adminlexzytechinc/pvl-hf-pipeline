"""
find_password_locked(): refuse firmware that is locked inside, and nothing else.

Real case behind it: "Tecno Pouvoir 1 LA6 MT6580 7.0 Dead Recovery ... Factroy
Signed Firmware" was built and published. Its outer archive is fine; inside is
LA6-H8021AC-N-180105V79.zip with 17 of 18 files password-protected, beside a
"Contact Me For Password" folder holding AnyDesk and UltraViewer.

Uses 7-Zip to make real encrypted archives, as the runner has it installed.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SEVEN = r"C:\Program Files\7-Zip"
if os.path.isdir(SEVEN):
    os.environ["PATH"] = SEVEN + os.pathsep + os.environ.get("PATH", "")
if shutil.which("7z") is None:
    print("7z not available; skipping")
    sys.exit(0)

SRC = io.open(os.path.join(HERE, "hf_build_worker.py"), encoding="utf-8").read()
start = SRC.index("NESTED_ARCHIVE_EXTS = ")
end = SRC.index("def process_archive(")
ns = {"os": os, "subprocess": subprocess}
exec(SRC[start:end], ns)
find_password_locked = ns["find_password_locked"]

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def make_file(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))


def make_zip(path, members, password=None):
    """members: {name: size}. Built with 7z, encrypted when a password is given."""
    src = tempfile.mkdtemp()
    for name, size in members.items():
        make_file(os.path.join(src, name), size)
    cmd = ["7z", "a", "-tzip", "-mx0", path] + ([f"-p{password}"] if password else []) + ["."]
    subprocess.run(cmd, cwd=src, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    shutil.rmtree(src, ignore_errors=True)


def staging(build):
    d = tempfile.mkdtemp()
    build(d)
    return d


print("\n== The scam package shape ==")
d = staging(lambda d: (
    make_zip(os.path.join(d, "firmware", "LA6-H8021AC-N-180105V79.zip"),
             {"boot.img": 900_000, "system.img": 2_000_000, "MT6580_Android_scatter.txt": 5_000},
             password="paid"),
    make_zip(os.path.join(d, "firmware", "Contact Me For Password", "AnyDesk.zip"), {"AnyDesk.exe": 60_000}),
    make_file(os.path.join(d, "firmware", "Contact Me For Password", "READ ME.txt"), 1_500)))
why = find_password_locked(d)
ok(why != "", "locked firmware inside the package is refused", why)
ok("LA6-H8021AC-N-180105V79.zip" in why, "and the reason names the locked archive", why)

print("\n== Real firmware must still build ==")
d = staging(lambda d: (
    make_file(os.path.join(d, "firmware", "system.img"), 3_000_000),
    make_file(os.path.join(d, "firmware", "boot.img"), 800_000),
    make_zip(os.path.join(d, "Tools", "SP_Flash_Tool.zip"), {"flash_tool.exe": 100_000}, password="x")))
ok(find_password_locked(d) == "", "a small LOCKED tools archive beside real firmware is allowed")

d = staging(lambda d: make_zip(os.path.join(d, "Tecno_Pouvoir_3_LB7_MT6739_V213_190425.zip"),
                               {"boot.img": 900_000, "system.img": 2_000_000}))
ok(find_password_locked(d) == "", "an UNLOCKED archive inside (a folder-download wrapper) is allowed")

d = staging(lambda d: make_file(os.path.join(d, "firmware", "firmware.pac"), 2_000_000))
ok(find_password_locked(d) == "", "a package with no inner archives is allowed")

d = staging(lambda d: None)
ok(find_password_locked(d) == "", "an empty folder is allowed (nothing to judge)")

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
