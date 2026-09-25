"""
Disk space is cleared only when a build needs it (2026-09-25).

Numbers are the runner's own, from the build logs: a 145 GB root disk with
86 GB free at the start, 108 GB after the toolchains are removed. The file
sizes include the owner's largest firmware, 32 GB.
"""

import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import disk_plan as dp  # noqa: E402

GB = dp.GB
PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


class Runner:
    """A runner disk: 86 GB free, 108 GB once cleared."""
    def __init__(self, free=86 * GB):
        self.free_now = free
        self.cleared = 0

    def free(self, path):
        return self.free_now

    def cleaner(self, log):
        self.cleared += 1
        self.free_now += 22 * GB


def plan(need, runner, stage="processing"):
    lines = []
    reason = dp.ensure_space("/tmp", need, stage, log=lines.append,
                             cleaner=runner.cleaner, free=runner.free)
    return reason, lines


def entries(total_gb, largest_gb, n=10):
    rest = (total_gb - largest_gb) / (n - 1)
    return [{"name": "super.img", "size": int(largest_gb * GB)}] + \
           [{"name": "p%d.img" % i, "size": int(rest * GB)} for i in range(n - 1)]


print("\n== Everyday firmware: no clean-up at all ==")
r = Runner()
need = dp.extra_needed(int(3.8 * GB), entries(4.2, 2.5), is_zip=True, is_archive=True)
reason, lines = plan(need, r)
ok(reason == "" and r.cleared == 0, "KL4h-size zip (3.8 GB): nothing cleared, ~2 minutes saved", lines)
ok("no clean-up needed" in lines[0], "and the log says so: " + lines[0])

print("\n== 32 GB firmware ==")
r = Runner()
need = dp.extra_needed(32 * GB, entries(40, 12), is_zip=True, is_archive=True)
reason, _ = plan(need, r)
ok(reason == "" and r.cleared == 0, "32 GB zip (40 GB inside): fits without clean-up (needs %s)" % dp.gb(need))

r = Runner()
need = dp.extra_needed(32 * GB, entries(70, 20), is_zip=False, is_archive=True)
reason, lines = plan(need, r)
ok(reason == "" and r.cleared == 1, "32 GB 7z unpacking to 70 GB: clears once, then fits (needs %s)" % dp.gb(need), lines)

r = Runner()
need = dp.extra_needed(32 * GB, entries(110, 30), is_zip=False, is_archive=True)
reason, _ = plan(need, r)
ok("even after clean-up" in reason and r.cleared == 1,
   "unpacking to 110 GB cannot fit: stops at once with the reason", reason)

print("\n== Before the download ==")
r = Runner()
reason, _ = plan(32 * GB + dp.MARGIN, r, "download")
ok(reason == "" and r.cleared == 0, "a 32 GB download fits in the 86 GB the runner starts with")
r = Runner()
reason, _ = plan(90 * GB + dp.MARGIN, r, "download")
ok(reason == "" and r.cleared == 1, "a 90 GB download clears first")

print("\n== Unknown contents are planned for generously ==")
need = dp.extra_needed(10 * GB, [], is_zip=False, is_archive=True)
ok(need == 30 * GB + dp.MARGIN, "an archive that cannot be listed is assumed to unpack to 3x", dp.gb(need))
need = dp.extra_needed(5 * GB, [], is_zip=False, is_archive=False)
ok(need == 5 * GB + dp.MARGIN, "a single .pac needs one copy of itself")

print("\n== The unpack route really keeps one copy on disk ==")
import hf_build_worker as w  # noqa: E402
tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "raw_download")
payload = {"fw/super.img": os.urandom(300000), "fw/boot.img": os.urandom(100000)}
with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
    for k, v in payload.items():
        z.writestr(k, v)
out = os.path.join(tmp, "processed.zip")
w.zip_passthrough.passthrough_repack = lambda *a, **k: (_ for _ in ()).throw(w.zip_passthrough.Decline("test"))
w.report_progress = lambda *a, **k: None
w.process_archive(src, out, "Test_V1.zip", root_folder="Test_V1", add_branding=False, consume_source=True)
ok(not os.path.exists(src), "the download is deleted once unpacked")
with zipfile.ZipFile(out) as z:
    got = {n.split("/", 1)[1]: z.read(n) for n in z.namelist() if not n.endswith("/")}
ok(any(v == payload["fw/super.img"] for v in got.values()) and any(v == payload["fw/boot.img"] for v in got.values()),
   "and the new zip still holds every file, byte for byte", list(got))

src2 = os.path.join(tmp, "raw2")
with zipfile.ZipFile(src2, "w") as z:
    z.writestr("a.img", b"x" * 1000)
w.process_archive(src2, os.path.join(tmp, "p2.zip"), "T.zip", add_branding=False)
ok(os.path.exists(src2), "without consume_source (the old call) the download is left alone")

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
