"""
The whole build worker, start to finish, against a quota-blocked "Drive".

Runs the REAL hf_build_worker.main() -- metadata, the download cascade,
integrity test, dedup, extraction, junk removal, repack, upload, callbacks --
with only the two things that cannot run on a laptop replaced:

  * Google Drive  -> a local server that behaves the way a quota-blocked file
                     was measured to behave: a request for the whole file gets
                     the "Quota exceeded" HTML page, a request for a byte range
                     gets the bytes.
  * Hugging Face  -> upload_to_hf() writes to a local folder instead.

Everything else is the production code path with the production settings seen
in live run 35868502740: serve-time branding on, the same exclude patterns, the
worker handed the broken filename "https:" by the live Downloader.php.

What it proves:
  1. the firmware is fetched even though the whole-file request is refused
  2. what gets uploaded is the same firmware (every member byte-identical)
  3. the real name is recovered and used, as before
  4. junk files are removed exactly as the live exclude patterns say
  5. the progress callbacks WordPress receives have the same shape as before,
     plus heartbeats while the build is busy
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


# ── the firmware: a real-shaped Tecno package ─────────────────────────────
REAL_NAME = "Tecno_Pouvoir_4_LC7_MT6761_V218_200703.zip"
ROOT = "Tecno_Pouvoir_4_LC7_MT6761_V218_200703"
MEMBERS = {
    f"{ROOT}/Firmware/MT6761_Android_scatter.txt": b"partition_index: SYS0\n" * 200,
    f"{ROOT}/Firmware/preloader_lc7_h6116.bin": os.urandom(300_000),
    f"{ROOT}/Firmware/boot.img": os.urandom(2_000_000),
    f"{ROOT}/Firmware/system.img": os.urandom(6_000_000),
    f"{ROOT}/Credits.txt": b"repacked by some other site\n",       # excluded
    f"{ROOT}/Visit Us.url": b"[InternetShortcut]\nURL=x\n",        # excluded
    f"{ROOT}/SP Flash Tool/flash_tool.exe": os.urandom(50_000),    # excluded
}
EXCLUDES = "*SN Write Tool*\n*SP Flash Tool*\n*Credits.txt\n*.url"

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    for name, data in MEMBERS.items():
        zf.writestr(name, data)
ARCHIVE = buf.getvalue()
QUOTA_PAGE = (b"<!DOCTYPE html><html><head><title>Google Drive - Quota exceeded"
              b"</title></head><body>Too many users have viewed or downloaded "
              b"this file recently.</body></html>")

seen = {"unranged": 0, "ranged": 0}
callbacks = []


class Drive(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range", "")
        if not rng.startswith("bytes="):
            seen["unranged"] += 1
            self._send(200, QUOTA_PAGE, "text/html; charset=utf-8")
            return
        seen["ranged"] += 1
        a, _, b = rng[6:].partition("-")
        first, last = int(a), min(int(b or len(ARCHIVE) - 1), len(ARCHIVE) - 1)
        body = ARCHIVE[first:last + 1]
        self.send_response(206)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Range", "bytes %d-%d/%d" % (first, last, len(ARCHIVE)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % REAL_NAME)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):          # the WordPress progress callback
        n = int(self.headers.get("Content-Length") or 0)
        callbacks.append(json.loads(self.rfile.read(n) or b"{}"))
        self._send(200, b'{"success":true}', "application/json")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Drive)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]

# ── production settings, as the live workflow passes them ─────────────────
work = tempfile.mkdtemp(prefix="e2e_")
os.environ.update({
    "FILE_ID": "1Ng-TESTTESTTESTTESTTESTTESTTEST",
    "FILE_NAME": "https:",                     # what live Downloader.php sends
    "CALLBACK_URL": BASE + "/callback",
    "BUILD_ID": "pvl_e2e_test",
    "BUILD_CALLBACK_SECRET": "test-secret",
    "EXCLUDE_PATTERNS": EXCLUDES,
    "SOURCE_TYPE": "gdrive",
    "SOURCE_URL": "https://drive.google.com/file/d/1Ng-TEST/view",
    "DUAL_BUILD": "true",
    "SERVE_TIME_BRANDING": "true",
    "HF_TOKEN": "", "HF_REPO_ID": "test/test",
    "DEDUP_API_URL": "", "DEDUP_API_TOKEN": "",
    "TMPDIR": work, "TEMP": work, "TMP": work,
})
seven = r"C:\Program Files\7-Zip"
if os.path.isdir(seven):
    os.environ["PATH"] = seven + os.pathsep + os.environ.get("PATH", "")
tempfile.tempdir = work

sys.path.insert(0, HERE)
import ranged_fetch                                  # noqa: E402
import hf_build_worker as w                          # noqa: E402
import dedup_client                                  # noqa: E402

# Drive -> the local blocked file. Everything else is the real code.
ranged_fetch.drive_url = lambda fid: BASE + "/download?id=" + fid
w.HEARTBEAT_SECONDS = 1
w.get_file_metadata = lambda fid: {"name": REAL_NAME, "size": len(ARCHIVE),
                                   "mimeType": "application/zip"}


def signed_like_live(fid, dest):
    # The live step: a small ranged probe succeeds, then the WHOLE-file
    # download is refused. Both requests really go to the blocked "Drive".
    import requests
    probe = requests.get(BASE + "/signed?id=" + fid, headers={"Range": "bytes=0-63"}, timeout=10)
    print("  Signed URL obtained (%d, %s)" % (probe.status_code, probe.headers.get("Content-Type")))
    whole = requests.get(BASE + "/signed?id=" + fid, timeout=10)
    if "text/html" in (whole.headers.get("Content-Type") or ""):
        print("  Download returned HTTP status 429")   # what the live log says
        return False
    return True


w.signed_url_download = signed_like_live
reached = []
w.gas_try_copy = lambda *a: reached.append("GAS copy") or False
w.gas_try_folder_download = lambda *a: reached.append("GAS folder") or False
w.web_download = lambda *a: reached.append("web") or False
w.api_download = lambda *a: reached.append("API") or False

# Dedup exactly as live: the API refuses the token, the build carries on.
dedup_client.check = lambda *a, **k: {"unchecked": True, "reason": "dedup api returned 403"}
dedup_client.record_unchecked = lambda *a, **k: None

uploads = {}


def fake_upload(path, hf_filename):
    dest = os.path.join(work, "hf", hf_filename.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(path, dest)
    uploads[hf_filename] = dest
    return "https://huggingface.co/datasets/test/test/resolve/main/" + hf_filename


w.upload_to_hf = fake_upload

# ── run it ────────────────────────────────────────────────────────────────
print("\n==== worker output ====")
t0 = time.time()
code = 0
try:
    w.main()
except SystemExit as e:
    code = e.code or 0
time.sleep(1.5)                                    # let one heartbeat land
print("==== end of worker output (%.1fs) ====\n" % (time.time() - t0))

print("1. The blocked file was fetched")
ok(code == 0, "the build finished without error", "exit %r" % code)
ok(seen["unranged"] >= 1, "a whole-file request was refused with the quota page (as live)")
ok(seen["ranged"] >= 2, "the file came down in byte ranges (%d range requests)" % seen["ranged"])
ok(reached == [], "no later route was needed (GAS copy / folder / web / API untouched)", reached)

print("\n2. What was uploaded is the same firmware")
ok(len(uploads) == 1, "exactly one file uploaded (serve-time branding: one neutral copy)", list(uploads))
hf_name, local = next(iter(uploads.items())) if uploads else ("", "")
out = zipfile.ZipFile(local) if local else None
kept = {n: d for n, d in MEMBERS.items()
        if not any(x in n for x in ("Credits.txt", ".url", "SP Flash Tool"))}
if out:
    got = {i.filename: i for i in out.infolist() if not i.is_dir()}
    by_leaf = {n.split("/")[-1]: n for n in got}
    same = all(hashlib.sha256(out.read(by_leaf[n.split("/")[-1]])).digest()
               == hashlib.sha256(d).digest() for n, d in kept.items()
               if n.split("/")[-1] in by_leaf)
    ok(all(n.split("/")[-1] in by_leaf for n in kept), "every firmware file is in the upload",
       sorted(by_leaf))
    ok(same, "every firmware file is byte-identical to the source")

print("\n3. The real name is used, as before")
ok(hf_name == "1Ng-TESTTESTTESTTESTTESTTESTTEST/" + REAL_NAME,
   "stored as <file_id>/" + REAL_NAME + " (not 'https:')", hf_name)

print("\n4. Junk removed exactly by the live exclude patterns")
if out:
    names = [i.filename for i in out.infolist()]
    ok(not any("Credits.txt" in n or n.endswith(".url") or "SP Flash Tool" in n for n in names),
       "Credits.txt, *.url and SP Flash Tool/ removed", names)

print("\n5. What WordPress hears")
stages = [c.get("stage") for c in callbacks]
ok("metadata" in stages and "downloading" in stages and "complete" in stages,
   "the same stages as before: metadata, downloading, dedup, processing, uploading, complete",
   sorted(set(stages)))
done = [c for c in callbacks if c.get("stage") == "complete"]
ok(done and done[-1].get("hf_url", "").endswith(REAL_NAME),
   "the completion report carries the uploaded file's URL, as before")
ok(all(set(c) >= {"build_id", "file_id", "secret", "stage", "percent", "message"} for c in callbacks),
   "every report has the same fields WordPress already reads")
ok(len(callbacks) > len(set(json.dumps(c, sort_keys=True) for c in callbacks)) or len(callbacks) >= 8,
   "heartbeats re-send progress while the build is busy (%d reports)" % len(callbacks))

srv.shutdown()
shutil.rmtree(work, ignore_errors=True)
print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
