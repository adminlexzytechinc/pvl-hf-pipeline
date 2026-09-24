"""
The heartbeat must never report progress AFTER a final report.

WordPress sets any progress stage back to "building". A heartbeat that lands
after "complete" turns a stored file back into a running build, and the stale
cleaner marks it failed 20 minutes later. Found in review, 2026-09-24.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

received = []


class WP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        received.append(json.loads(self.rfile.read(n) or b"{}").get("stage"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


srv = ThreadingHTTPServer(("127.0.0.1", 0), WP)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["CALLBACK_URL"] = "http://127.0.0.1:%d/cb" % srv.server_address[1]

import hf_build_worker as w  # noqa: E402

w.CALLBACK_URL = os.environ["CALLBACK_URL"]
w.HEARTBEAT_SECONDS = 0.05
PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


print("\n== Heartbeat stops at the final report ==")
w._progress_state.update(stage="uploading", percent=90, message="Uploading")
w.start_heartbeat()
time.sleep(0.4)
ok(received.count("uploading") >= 2, "the heartbeat reports while the build runs", received)

w.report_complete("https://hf/x/file.zip", 123, key_name="file.zip")
n_at_complete = len(received)
time.sleep(0.5)
after = received[n_at_complete:]
ok("complete" in received, "the complete report was sent")
ok(after == [], "nothing is sent after complete (was: the heartbeat kept going)", after)
ok(received[-1] == "complete", "complete is the LAST report WordPress sees", received[-3:])

srv.shutdown()
print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
