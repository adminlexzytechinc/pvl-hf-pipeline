"""
afh_download.py against a local stand-in for AndroidFileHost (2026-10-04).
The page, the mirror list and the mirrors are served here, shaped like the
real ones (checked by hand on 4 Spark 9 Pro files). No network.

Mirror A drops the connection after 300 KB every time; mirror B serves
ranges properly. The download must resume, move to B, and end whole.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import afh_download as afh          # noqa: E402
import source_labels                # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


DATA = os.urandom(1_500_000)
NAME = "[Hovatek]_Tecno_Spark_9_Pro_(KH7-H6919ABC-S-OP-220826V539).zip"
FID = "4279422670115712661"
SEEN = {"ranges": [], "mirror_posts": 0}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/?fid="):
            html = ('<html><head><title>%s |  by Hovatek for Generic Device/Other</title></head><body>'
                    '<input type="hidden" name="file_size" id="file_size" value="%d" /></body></html>'
                    % (NAME, len(DATA)))
            return self._send(200, html.encode())
        if self.path.startswith("/dl/"):
            rng = self.headers.get("Range", "")
            SEEN["ranges"].append((self.path, rng))
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            body = DATA[start:]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            if rng:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(DATA) - 1, len(DATA)))
            self.end_headers()
            if self.path.startswith("/dl/A"):
                self.wfile.write(body[:300_000])        # then the connection dies
                self.close_connection = True
                return
            self.wfile.write(body)
            return
        self._send(404, b"no")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(n).decode()
        if self.path == "/libs/otf/mirrors.otf.php" and "getdownloadmirrors" in form and FID in form:
            SEEN["mirror_posts"] += 1
            port = self.server.server_address[1]
            body = json.dumps({"STATUS": "1", "MESSAGE": "success, #winning", "MIRRORS": [
                {"name": "Virginia, USA - mVA1", "url": "http://127.0.0.1:%d/dl/A/x" % port},
                {"name": "Virginia, USA - aVA2", "url": "http://127.0.0.1:%d/dl/B/x" % port}]})
            return self._send(200, body.encode(), "application/json")
        self._send(404, b"no")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
afh.BASE = "http://127.0.0.1:%d" % srv.server_address[1]
afh.time.sleep = lambda s: None
afh.TRIES_PER_MIRROR = 2

print("\n== Recognising AndroidFileHost ==")
url = "https://androidfilehost.com/?fid=" + FID
ok(afh.parse_fid("", url) == FID and afh.parse_fid("afh_" + FID) == FID
   and afh.parse_fid("", url + "#google_vignette") == FID, "the file number from a link, a #vignette link, or afh_<n>")
ok(afh.is_afh("", "", url) and afh.is_afh("afh") and afh.is_afh("", "afh_1") and not afh.is_afh("gdrive", "abc", ""),
   "an AFH link is recognised; a Drive one is not")
ok(source_labels.source_label("", "afh_" + FID, "") == "AndroidFileHost"
   and source_labels.source_label("", "", url) == "AndroidFileHost", 'shown to visitors as "AndroidFileHost"')

print("\n== Page and mirrors ==")
info = afh.file_info(FID)
ok(info == {"name": NAME, "size": len(DATA)}, "name and size read from the page", info)
ok([n for n, _ in afh.mirrors(FID)] == ["Virginia, USA - mVA1", "Virginia, USA - aVA2"], "both mirrors listed")

print("\n== Download: A keeps dropping, B is fine ==")
dest = os.path.join(tempfile.mkdtemp(), "raw")
SEEN["ranges"].clear()
got, name = afh.afh_download("afh_" + FID, url, dest)
ok(got and name == NAME, "download reported whole, with the real name", (got, name))
ok(open(dest, "rb").read() == DATA, "the file is byte-for-byte right (%d bytes)" % len(DATA),
   hashlib.md5(open(dest, "rb").read()).hexdigest())
a_hits = [r for p, r in SEEN["ranges"] if p.startswith("/dl/A")]
b_hits = [r for p, r in SEEN["ranges"] if p.startswith("/dl/B")]
start = lambda r: int(r.split("=")[1].rstrip("-")) if r else 0
ok(len(a_hits) == 2 and a_hits[0] == "" and start(a_hits[1]) >= 200_000,
   "on A: started, dropped, RESUMED where it stopped (not from zero)", a_hits)
ok(b_hits and start(b_hits[0]) > start(a_hits[1]), "then moved to B, carrying on from where A left off", b_hits)

print("\n== Nothing served ==")
dest2 = os.path.join(tempfile.mkdtemp(), "raw")
got, _ = afh.afh_download("", "https://androidfilehost.com/?fid=4279422670115799999", dest2)
ok(got is False, "a file AFH will not list fails cleanly (False), no crash")
ok(afh.afh_download("", "https://example.com/x", dest2)[0] is False, "no file number: fails cleanly")

print("\n== The build uses it ==")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_build_worker.py"), encoding="utf-8").read()
ok("afh_module.afh_download(" in src and "afh_module.file_info(" in src and "or is_afh:" in src,
   "the worker downloads from AFH, reads its name and size first, and adopts the real name")

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
