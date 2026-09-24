"""
zip_passthrough: the repack that copies compressed bytes must give the SAME
files in the SAME places as the extract-and-store path it replaces, and must
step aside whenever it cannot promise that.

Every layout case is checked against the real process_archive() old path, not
against an expected list written by hand.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SEVEN = r"C:\Program Files\7-Zip"
if os.path.isdir(SEVEN):
    os.environ["PATH"] = SEVEN + os.pathsep + os.environ.get("PATH", "")
os.environ["EXCLUDE_PATTERNS"] = "*Driver*\n*SN Write Tool*\n*SP Flash Tool*\n*Credits.txt\n*.url"
sys.path.insert(0, HERE)

import hf_build_worker as w      # noqa: E402
import zip_passthrough as zp     # noqa: E402

w.report_progress = lambda *a, **k: None
PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def make(path, members, method=zipfile.ZIP_DEFLATED):
    """members: {name: bytes or None for a folder entry}"""
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in members.items():
            if data is None:
                z.writestr(zipfile.ZipInfo(name.rstrip("/") + "/"), b"")
            else:
                z.writestr(name, data)


def listing(path):
    with zipfile.ZipFile(path) as z:
        return sorted((i.filename, i.file_size, i.CRC) for i in z.infolist() if not i.is_dir())


def both(members, root="ROOT", method=zipfile.ZIP_DEFLATED):
    d = tempfile.mkdtemp()
    tempfile.tempdir = d
    try:
        src = os.path.join(d, "src.zip")
        make(src, members, method)
        # Force the OLD path by hiding the zip from the passthrough.
        real = zipfile.is_zipfile
        w.zipfile.is_zipfile = lambda p: False if p == src else real(p)
        try:
            w.process_archive(src, os.path.join(d, "old.zip"), "src.zip", root_folder=root, add_branding=False)
        finally:
            w.zipfile.is_zipfile = real
        try:
            zp.passthrough_repack(src, os.path.join(d, "new.zip"), w.EXCLUDE_PATTERNS, root)
            new = listing(os.path.join(d, "new.zip"))
        except zp.Decline as e:
            new = "DECLINED: %s" % e
        return listing(os.path.join(d, "old.zip")), new
    finally:
        tempfile.tempdir = None
        shutil.rmtree(d, ignore_errors=True)


BIG = os.urandom(300_000)
print("\n== Same files, same places as the old path ==")
cases = {
    "vendor folder wrapping Firmware/ and junk (the Itel shape)": {
        "Itel_P40_SPD/Credits.txt": b"c", "Itel_P40_SPD/Driver/": None,
        "Itel_P40_SPD/Driver/Download.url": b"u", "Itel_P40_SPD/Firmware/": None,
        "Itel_P40_SPD/Firmware/P662L.pac": BIG, "Itel_P40_SPD/Firmware/boot.img": b"b" * 5000,
        "Itel_P40_SPD/How to Flash.url": b"u", "Itel_P40_SPD/SPD Upgrade Tool/Download.url": b"u"},
    "two nested single folders are both hoisted": {
        "a/b/firmware/system.img": BIG, "a/b/firmware/scatter.txt": b"s", "a/b/readme.txt": b"r"},
    "no firmware folder: one is created": {"system.img": BIG, "boot.img": b"b" * 999, "Credits.txt": b"c"},
    "Firmware (capital F) is reused and renamed": {"Firmware/super.img": BIG, "notes.txt": b"n"},
    "an excluded folder takes its whole subtree": {
        "pkg/SP Flash Tool/flash_tool.exe": b"x" * 100, "pkg/SP Flash Tool/sub/a.dll": b"d",
        "pkg/firmware/boot.img": BIG, "pkg/Driver_Auto_Installer/setup.exe": b"e"},
    "empty folder entries do not change the layout": {
        "pkg/": None, "pkg/empty/": None, "pkg/firmware/": None, "pkg/firmware/x.img": BIG},
    "a lone file at the top is not hoisted": {"only.pac": BIG},
    "a folder whose files were all excluded still counts as firmware/": {
        "p/Firmware/Credits.txt": b"c", "p/boot.img": BIG},
    "non-ASCII names survive": {"Téléphone/firmware/système.img": BIG, "Téléphone/说明.txt": b"z"},
}
for label, members in cases.items():
    old, new = both(members)
    ok(old == new, label, "old=%s\nnew=%s" % (old, new))
old, new = both(cases["vendor folder wrapping Firmware/ and junk (the Itel shape)"], method=zipfile.ZIP_STORED)
ok(old == new, "a STORED source gives the same result too")
old, new = both({"x/firmware/a.img": BIG}, root="")
ok(old == new, "with no root folder")

print("\n== Steps aside instead of guessing ==")
for label, members, method in [
    ("LZMA-compressed entries (visitors' unzip may not open them)", {"a.img": BIG}, zipfile.ZIP_LZMA),
    ("an item at the top AND inside firmware/ with the same name",
     {"firmware/boot.img": BIG, "boot.img": b"other"}, zipfile.ZIP_DEFLATED),
    ("a top-level FILE named firmware", {"firmware": b"f", "a.img": BIG}, zipfile.ZIP_DEFLATED),
    ("a path that climbs out of the folder", {"../evil.img": BIG}, zipfile.ZIP_DEFLATED),
]:
    d = tempfile.mkdtemp()
    try:
        src = os.path.join(d, "s.zip")
        make(src, members, method)
        try:
            zp.passthrough_repack(src, os.path.join(d, "o.zip"), w.EXCLUDE_PATTERNS, "R")
            ok(False, label + " -> declined", "it copied")
        except zp.Decline:
            ok(not os.path.exists(os.path.join(d, "o.zip")), label + " -> declined, no partial file left")
    finally:
        shutil.rmtree(d, ignore_errors=True)

if shutil.which("7z"):
    d = tempfile.mkdtemp()
    try:
        srcdir = os.path.join(d, "in")
        os.makedirs(srcdir)
        open(os.path.join(srcdir, "a.img"), "wb").write(BIG)
        src = os.path.join(d, "enc.zip")
        subprocess.run(["7z", "a", "-tzip", "-pX", src, "."], cwd=srcdir, stdout=subprocess.DEVNULL, check=True)
        try:
            zp.passthrough_repack(src, os.path.join(d, "o.zip"), [], "R")
            ok(False, "encrypted entries -> declined")
        except zp.Decline:
            ok(True, "encrypted entries -> declined")
    finally:
        shutil.rmtree(d, ignore_errors=True)

print("\n== The output is a sound archive ==")
d = tempfile.mkdtemp()
try:
    src = os.path.join(d, "s.zip")
    make(src, {"pkg/firmware/a.img": BIG, "pkg/firmware/b.txt": b"hello"})
    out = os.path.join(d, "o.zip")
    zp.passthrough_repack(src, out, [], "Tecno_X", extra_files={"README - Download Info.txt": b"readme"})
    with zipfile.ZipFile(out) as z:
        ok(z.testzip() is None, "every CRC checks out (Python zipfile)")
        ok(z.read("Tecno_X/firmware/a.img") == BIG, "the payload bytes are identical")
        ok(z.read("Tecno_X/README - Download Info.txt") == b"readme", "branding files are added under the root")
        ok(all((i.external_attr >> 16) & 0o777 == 0o644 for i in z.infolist()),
           "every entry gets a normal file mode, as the old path gave")
    if shutil.which("7z"):
        r = subprocess.run(["7z", "t", out], capture_output=True, text=True)
        ok(r.returncode == 0, "7-Zip tests it clean")
    ok(os.path.getsize(out) < len(BIG) + 5000, "no growth: compressed bytes were copied, not stored",
       os.path.getsize(out))
finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n== The worker uses it, and falls back ==")
d = tempfile.mkdtemp()
tempfile.tempdir = d
try:
    src = os.path.join(d, "s.zip")
    make(src, cases["vendor folder wrapping Firmware/ and junk (the Itel shape)"])
    buf = io.StringIO()
    real_stdout, sys.stdout = sys.stdout, buf
    try:
        w.process_archive(src, os.path.join(d, "o.zip"), "s.zip", root_folder="R", add_branding=False)
    finally:
        sys.stdout = real_stdout
    ok("Direct copy:" in buf.getvalue(), "process_archive() takes the direct copy for a normal zip")
    lz = os.path.join(d, "lz.zip")
    make(lz, {"a/firmware/x.img": BIG}, zipfile.ZIP_LZMA)
    buf = io.StringIO()
    sys.stdout = buf
    try:
        w.process_archive(lz, os.path.join(d, "o2.zip"), "lz.zip", root_folder="R", add_branding=False)
    finally:
        sys.stdout = real_stdout
    ok("Direct copy not used" in buf.getvalue() and listing(os.path.join(d, "o2.zip"))[0][0] == "R/firmware/x.img",
       "an LZMA zip falls back to the old path and still builds")
finally:
    tempfile.tempdir = None
    shutil.rmtree(d, ignore_errors=True)

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
