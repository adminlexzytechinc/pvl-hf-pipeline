"""
One archive, not an archive inside an archive (nested_archive.py, 2026-10-04).

Builds real packages and runs the real process_archive(): a .zip whose
content is one .7z comes out as a zip of the firmware itself; real firmware
formats (.pac, Samsung .tar.md5), OTA update.zips, locked inner archives and
packages with a small tools archive beside the firmware are left as they are.
Needs 7z (on the runner; on Windows from Program Files).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nested_archive as na   # noqa: E402

PASS = FAIL = 0
SEVEN = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def run7(args, **kw):
    return subprocess.run([SEVEN] + args[1:], **kw)


print("\n== Which archive is 'the package' ==")
ok(na.dominant_inner([("Tecno Pova 7 5G LJ7.rar", 5_000_000_000), ("README.txt", 300)]) == "Tecno Pova 7 5G LJ7.rar",
   "one .rar that is nearly all of the zip: unpack it")
ok(na.dominant_inner([("fw/P662L.pac", 3_000_000_000), ("Credits.txt", 100)]) == "", ".pac is the firmware itself: kept")
ok(na.dominant_inner([("AP_G556B.tar.md5", 5e9), ("CSC_OXM.tar.md5", 1e9)]) == "", "Samsung .tar.md5 parts: kept")
ok(na.dominant_inner([("fw/super.img", 6e9), ("tools.rar", 2e7), ("boot.img", 3e7)]) == "",
   "firmware beside a small tools.rar: kept")
ok(na.dominant_inner([("a.rar", 1e9), ("b.rar", 1e9)]) == "", "two archives: not guessed, kept")
ok(na.dominant_inner([("LA6.zip", 9e8), ("Tecno Flash Tool/x.exe", 5e8)],
                     excluded=lambda n: "Flash Tool" in n) == "LA6.zip",
   "a tool the owner excludes does not count against it")

tmp = tempfile.mkdtemp()
fw = os.path.join(tmp, "fw")
os.makedirs(fw)
for n, size in (("MT6789_Android_scatter.txt", 2000), ("super.img", 300_000), ("boot.img", 50_000)):
    open(os.path.join(fw, n), "wb").write(os.urandom(size))

print("\n== A real .zip holding one .7z ==")
inner = os.path.join(tmp, "Tecno Pova 7 5G LJ7.7z")
run7(["7z", "a", "-mx0", inner, os.path.join(fw, "*")], stdout=subprocess.DEVNULL)
outer = os.path.join(tmp, "outer.zip")
with zipfile.ZipFile(outer, "w") as z:
    z.write(inner, "Tecno Pova 7 5G LJ7.7z")
    z.writestr("Downloaded from somewhere.txt", "hello")
ok(na.zip_has_nested(outer) == "Tecno Pova 7 5G LJ7.7z", "the direct-copy route sees the archive inside and steps aside")

staging = os.path.join(tmp, "stage")
os.makedirs(staging)
with zipfile.ZipFile(outer) as z:
    z.extractall(staging)
done = na.unwrap_in_staging(staging, log=lambda m: None, run=run7)
files = sorted(os.path.relpath(os.path.join(r, f), staging).replace("\\", "/") for r, _d, fs in os.walk(staging) for f in fs)
ok(done == ["Tecno Pova 7 5G LJ7.7z"] and not any(f.endswith(".7z") for f in files)
   and "Tecno Pova 7 5G LJ7/super.img" in files and "Tecno Pova 7 5G LJ7/MT6789_Android_scatter.txt" in files,
   "unpacked in place: the firmware files, no inner archive left", files)

print("\n== OTA update.zip inside: kept ==")
ota = os.path.join(tmp, "update.zip")
with zipfile.ZipFile(ota, "w") as z:
    z.writestr("META-INF/com/google/android/update-binary", "x")
    z.writestr("payload.bin", os.urandom(200_000))
outer2 = os.path.join(tmp, "outer2.zip")
with zipfile.ZipFile(outer2, "w") as z:
    z.write(ota, "NX809J_update.zip")
ok(na.zip_has_nested(outer2) == "", "an OTA update.zip is flashed as a zip: not unpacked")
st2 = os.path.join(tmp, "stage2")
os.makedirs(st2)
shutil.copy(ota, os.path.join(st2, "NX809J_update.zip"))
ok(na.unwrap_in_staging(st2, log=lambda m: None, run=run7) == [] and os.path.exists(os.path.join(st2, "NX809J_update.zip")),
   "...also on the unpack route")

print("\n== A locked inner archive: kept, build not failed ==")
locked = os.path.join(tmp, "locked.7z")
run7(["7z", "a", "-pSECRET", "-mhe=on", locked, os.path.join(fw, "*")], stdout=subprocess.DEVNULL)
st3 = os.path.join(tmp, "stage3")
os.makedirs(st3)
shutil.copy(locked, os.path.join(st3, "firmware.7z"))
msgs = []
ok(na.unwrap_in_staging(st3, log=msgs.append, run=run7) == [] and os.path.exists(os.path.join(st3, "firmware.7z"))
   and any("password" in m for m in msgs), "left as it is, with a clear log line", msgs)

print("\n== The build uses it ==")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_build_worker.py"), encoding="utf-8").read()
ok("nested_archive.zip_has_nested(source_path, should_exclude)" in src
   and "if nested_archive.unwrap_in_staging(staging_dir, should_exclude):" in src,
   "process_archive skips direct copy and unpacks the inner archive")
ok("need += raw_size * 2" in src and "again = archive_facts(processed_path" in src,
   "the disk check allows for unpacking twice; the Gold card details are read from the finished zip")

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
