"""
A stalled MEGA transfer must stop itself, not hold the runner.

mega_download() reads megatools' stderr with a blocking read. MAX_IDLE (10 min)
was defined to stop a transfer that goes silent -- but it was only ever checked
AFTER a read returned, and a silent megatools never lets a read return. So a
stall ran until GitHub killed the whole job: run 35868500541 sat for its full
60 minutes on mega_k94RWTKR, and the retry timer re-sent it every 6 hours.

This runs a REAL child process that prints one progress line and then goes
silent while holding the pipe open -- the actual stall, not a mock of it --
with the idle limit shortened to a few seconds.

Evidence used: elapsed time. A crash returns at once; a stall with the old code
never returns; a watchdog stop returns just after the idle limit.
"""

import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mega_download as md  # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, "fake_megatools_stall.py")
with open(fake, "w") as f:
    f.write("import sys, time\n"
            "sys.stderr.write('  1% of 1.7 GB (18 MB)\\r')\n"
            "sys.stderr.flush()\n"
            "time.sleep(3600)\n")

real_popen = subprocess.Popen
started_child = {}


def popen(cmd, **kw):
    p = real_popen([sys.executable, fake], **kw)
    started_child["p"] = p
    return p


md.subprocess.Popen = popen
md.MEGA_BIN = sys.executable
IDLE = 6
md.MAX_IDLE_SECONDS = IDLE

print("\n== A stalled transfer ==")
url = "https://mega.nz/file/d6IXQCab#4ZAiyTb9kW09F-961OHJtOtt_2dbN4BYKq3kajqZ0fY"
t0 = time.time()
res = md.mega_download("mega_d6IXQCab", url, os.path.join(tmp, "out.zip"))
dt = time.time() - t0
p = started_child.get("p")

ok(res == (False, None), "reports failure (so the cascade can move on)", res)
ok(dt >= IDLE, "waited out the idle limit first -- it did not just crash (%.0fs)" % dt)
ok(dt < IDLE + 20, "then stopped by itself, within seconds of the limit (%.0fs)" % dt)
ok(p is not None and p.poll() is not None, "the stalled megatools process was actually stopped")

subprocess.Popen = real_popen
print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
