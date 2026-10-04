"""
After Google answers the signed URL with 429, the GAS copy goes before
slices (2026-10-04, from the Spark 9 Pro run of 2026-10-03: slices got 51%
then stalled 10 minutes; the GAS copy took about a minute).

Runs the real download_file() with every route stubbed. No network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hf_build_worker as w     # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


calls = []
dest = os.path.join(tempfile.mkdtemp(), "raw")


def route(name, status=0, works=False):
    def f(fid, d):
        calls.append(name)
        w.LAST_HTTP_STATUS = status
        if works:
            open(d, "wb").write(b"x")
        return works
    return f


def setup(signed_status, copy_works, ranged_works=False):
    calls.clear()
    w.RANGED_REFUSED = False
    w.signed_url_download = route("signed", signed_status)
    w.ranged_download = route("ranged", 0, ranged_works)
    w.gas_try_copy = route("copy", 200 if copy_works else 0, copy_works)
    w.gas_try_folder_download = route("folder")
    w.web_download = route("web")
    w.api_download = route("api")


w.time.sleep = lambda s: None
w.report_progress = lambda *a, **k: None
w.verify_archive_integrity = lambda p, n: (True, "test")
w.SOURCE_TYPE = "gdrive"
w.SOURCE_URL = ""

print("\n== 429 on the signed URL ==")
setup(429, copy_works=True)
ok(w.download_file("f", dest) and calls == ["signed", "copy"],
   "the GAS copy runs straight after, before slices -- and it fetches the file", calls)

setup(429, copy_works=False, ranged_works=True)
ok(w.download_file("f", dest) and calls == ["signed", "copy", "ranged"],
   "if the GAS copy fails, slices still run next", calls)

setup(429, copy_works=False)
w.download_file("f", dest)
ok(calls.count("copy") == 2 and calls.count("ranged") == 2 and calls[:3] == ["signed", "copy", "ranged"]
   and calls[6:9] == ["signed", "copy", "ranged"],
   "nothing is skipped: every route still runs, on both passes", calls)

print("\n== Any other answer: the order is unchanged ==")
for status in (0, 403, 500):
    setup(status, copy_works=True)
    w.download_file("f", dest)
    ok(calls == ["signed", "ranged", "copy"], "status %s: signed URL, slices, then the GAS copy" % (status or "none"), calls)

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
