"""
A file Google refuses outright (2026-09-26, from the MegaPad 11 run of
2026-09-25): the second pass of the cascade skips the slice route, and the
build reports "google_blocked" so WordPress retries it hours later.

Runs the real download_file() with every route stubbed to fail the way that
run failed. No network, no waiting.
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


def refused_slices(fid, dest):
    calls.append("ranged")
    w.RANGED_REFUSED = True          # what ranged_download() sets on "no slice served"
    return False


def fail(name):
    def f(fid, dest):
        calls.append(name)
        return False
    return f


w.time.sleep = lambda s: None
w.report_progress = lambda *a, **k: None
w.signed_url_download = fail("signed")
w.ranged_download = refused_slices
w.gas_try_copy = fail("copy")
w.gas_try_folder_download = fail("folder")
w.web_download = fail("web")
w.api_download = fail("api")
w.SOURCE_TYPE = "gdrive"
w.SOURCE_URL = ""
w.RANGED_REFUSED = False

dest = os.path.join(tempfile.mkdtemp(), "raw")
got = w.download_file("109ECnjTgzJ4bJThgap5ylK42rXsI5tmU", dest)

print("\n== The cascade ==")
ok(got is False, "every route fails, as on 2026-09-25")
ok(calls.count("ranged") == 1, "the slice route runs once, not twice (saves ~10 minutes)", calls)
ok(calls.count("signed") == 2 and calls.count("copy") == 2 and calls.count("api") == 2,
   "the quick routes are still tried again on pass 2", calls)

print("\n== Not blocked: unchanged ==")
calls.clear()
w.RANGED_REFUSED = False
w.ranged_download = fail("ranged")
w.download_file("x", dest)
ok(calls.count("ranged") == 2, "a slice route that failed for another reason is still retried", calls)

print("\n== The report ==")
sent = []
w.requests.post = lambda url, json=None, timeout=None: sent.append(json)
w._final_sent = False
w.report_error("Google is blocking downloads of this file right now (too many downloads). It will be tried again later.",
               reason="google_blocked")
ok(sent and sent[-1].get("reason") == "google_blocked" and sent[-1].get("stage") == "error",
   'sent as an error with reason "google_blocked"', sent[-1] if sent else None)
w._final_sent = False
w.report_error("Something else")
ok("reason" not in sent[-1], "other errors carry no reason (WordPress treats them as before)")
src = open(w.__file__, encoding="utf-8").read()
ok('reason="google_blocked")' in src and "if RANGED_REFUSED:" in src, "main() reports a blocked file that way")

print("\n== MEGA ==")
import mega_download as md      # noqa: E402
ok(md._QUOTA_WORDS.search("ERROR: Can't download: Transfer quota exceeded (EOVERQUOTA)") is not None
   and md._QUOTA_WORDS.search("ERROR: File not found") is None, "MEGA's own quota message is recognised; other errors are not")
ok('mega_module.LAST_FAILURE in ("stalled", "overquota")' in src and 'reason="mega_limited")' in src,
   'a stalled or over-quota MEGA download is reported as "mega_limited"')
md_src = open(md.__file__, encoding="utf-8").read()
ok(md_src.count('LAST_FAILURE = "stalled"') == 1 and md_src.count('LAST_FAILURE = "overquota"') == 1
   and md_src.count('LAST_FAILURE = ""') == 2, "the MEGA step records why it failed, and clears it on success")

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
