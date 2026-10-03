"""
backfill_facts.py end to end against a local stand-in for WordPress and
Hugging Face (2026-09-26). The "stored file" is a real ZIP built here with an
old-style boot.img header, a scatter file and a preloader, served with HTTP
ranges and refused without the repo's token. No network.
"""

import gzip
import io
import json
import os
import struct
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backfill_facts as bf     # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def boot_v0(a, b, y, m):
    h = bytearray(8192)
    h[:8] = b'ANDROID!'
    struct.pack_into('<I', h, 36, 2048)
    struct.pack_into('<I', h, 44, ((((a << 14) | (b << 7)) << 11) | (((y - 2000) << 4) | m)))
    return bytes(h)


def cpio(files):
    out = b''
    for i, (name, body) in enumerate(list(files.items()) + [('TRAILER!!!', b'')]):
        n = name.encode() + b'\0'
        out += (b'070701' + b'%08X' % (i + 1) + b'%08X' % 0o100644 + b'0' * 32 + b'%08X' % len(body)
                + b'0' * 32 + b'%08X' % len(n) + b'0' * 8) + n
        out += b'\0' * (-len(out) % 4) + body
        out += b'\0' * (-len(out) % 4)
    return out


def recovery_v0(props):
    """A recovery image whose ramdisk carries default.prop -- where an old
    phone keeps its own model (ro.product.model), added 2026-10-03."""
    ramdisk = gzip.compress(cpio({'default.prop': props.encode()}))
    h = bytearray(2048)
    h[:8] = b'ANDROID!'
    struct.pack_into('<IIII', h, 8, 4096, 0, len(ramdisk), 0)
    struct.pack_into('<I', h, 36, 2048)
    return bytes(h) + os.urandom(4096) + ramdisk


buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('Tecno_Pouvoir_3_LB7/firmware/boot.img', boot_v0(8, 1, 2020, 8) + os.urandom(200000))
    z.writestr('Tecno_Pouvoir_3_LB7/firmware/MT6739_Android_scatter.txt', 'storage: HW_STORAGE_EMMC\n' * 50)
    z.writestr('Tecno_Pouvoir_3_LB7/firmware/preloader_lb7_h393.bin', os.urandom(1000))
    z.writestr('Tecno_Pouvoir_3_LB7/firmware/system.img', os.urandom(300000))
    z.writestr('Tecno_Pouvoir_3_LB7/firmware/recovery.img',
               recovery_v0('ro.product.brand=TECNO\nro.product.model=TECNO LB7\nro.product.device=TECNO-LB7\n'))
STORED = buf.getvalue()

STATE = {'reports': [], 'ranges': 0, 'bytes': 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'{}')
        if data.get('secret') != 'cb-secret':
            return self._json({'success': False, 'data': 'Unauthorized'}, 401)
        if 'pvl_facts_todo' in self.path:
            base = 'http://127.0.0.1:%d' % self.server.server_address[1]
            return self._json({'success': True, 'data': {'files': [
                {'file_id': 'mf_lb7', 'file_name': 'Tecno_Pouvoir_3_LB7.zip', 'repo_slug': 'fw-1',
                 'hf_path': base + '/datasets/x/resolve/main/mf_lb7/Tecno_Pouvoir_3_LB7.zip'},
                {'file_id': 'gone', 'file_name': 'missing.zip', 'repo_slug': 'fw-1',
                 'hf_path': base + '/datasets/x/resolve/main/gone/missing.zip'},
            ]}})
        if 'pvl_build_progress' in self.path:
            STATE['reports'].append(data)
            return self._json({'success': True, 'data': 'Facts saved'})
        self._json({'success': False}, 404)

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer hf_fw1':
            self.send_response(401); self.end_headers(); return
        if 'mf_lb7' not in self.path:
            self.send_response(404); self.end_headers(); return
        a, _, b = self.headers['Range'][6:].partition('-')
        a, b = int(a), min(int(b), len(STORED) - 1)
        chunk = STORED[a:b + 1]
        STATE['ranges'] += 1
        STATE['bytes'] += len(chunk)
        self.send_response(206)
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (a, b, len(STORED)))
        self.send_header('Content-Length', str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({'SITE_URL': 'http://127.0.0.1:%d' % srv.server_address[1],
                   'BUILD_CALLBACK_SECRET': 'cb-secret', 'HF_TOKEN': 'wrong',
                   'HF_ACCOUNTS': json.dumps({'fw-1': 'hf_fw1'})})

print("\n== Dry run ==")
rc = bf.main(['--dry-run'])
ok(STATE['reports'] == [], 'a dry run sends nothing')

print("\n== Real run ==")
STATE['bytes'] = 0
rc = bf.main([])
rep = {r['file_id']: r for r in STATE['reports']}
f = rep.get('mf_lb7', {}).get('fw_facts', {})
ok(f.get('android') == '8.1' and f.get('security_patch') == '2020-08' and f.get('chipset') == 'MT6739'
   and f.get('model') == 'LB7' and f.get('storage') == 'eMMC', 'details read remotely: 8.1, 2020-08, MT6739, LB7, eMMC', f)
ok('recovery.img' in f.get('model_source', '') and 'preloader' not in f.get('model_source', ''),
   "the model is the phone's own (recovery settings), not the preloader's name", f.get('model_source'))
ok(rep.get('mf_lb7', {}).get('stage') == 'facts', 'sent as a "facts" report (writes only the details)')
ok('gone' not in rep and rc == 1, 'a file that cannot be read is reported as FAILED, not sent', rc)
ok(STATE['bytes'] < len(STORED), 'read %d of %d bytes (only the index and small parts)' % (STATE['bytes'], len(STORED)))
ok(bf.token_for('fw-1', {'fw-1': 'hf_fw1'}, 'x') == 'hf_fw1' and bf.token_for('fw-9', {}, 'x') == 'x',
   "each repo's own token, else HF_TOKEN")

srv.shutdown()
print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
