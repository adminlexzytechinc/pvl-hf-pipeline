"""
RemoteZip, checked against Python's own zipfile on real archives.

Served over a local HTTP server that supports Range, so the test is
deterministic: no network, no Drive quota, no signed URLs that expire
mid-run. What it proves is the part that matters -- that reading an archive
through ranged GETs gives byte-identical answers to reading it from disk.

ZIP64 is the reason this needs real files rather than fixtures. Any archive
over 4 GiB, or any member over 4 GiB, leaves 0xFFFFFFFF in the normal size
and offset slots and puts the truth in an extra field. A reader that skips
that reports 4294967295 for a 5 GB member and ranges against a wrong offset.
An earlier scratch version of this reader did exactly that.
"""

import os
import sys
import threading
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_zip import RemoteZip, RemoteZipError

PASS = 0
FAIL = 0


def ok(cond, label, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   ' + label)
    else:
        FAIL += 1
        print('  FAIL ' + label + ('\n       ' + str(detail) if detail else ''))


# ---------------------------------------------------------------------------
# A range-capable static server, which is all RemoteZip requires of a host.
# ---------------------------------------------------------------------------

class RangeHandler(BaseHTTPRequestHandler):
    path_on_disk = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        total = os.path.getsize(self.path_on_disk)
        rng = self.headers.get('Range')
        with open(self.path_on_disk, 'rb') as f:
            if rng and rng.startswith('bytes='):
                a, _, b = rng[6:].partition('-')
                start = int(a)
                end = int(b) if b else total - 1
                end = min(end, total - 1)
                f.seek(start)
                body = f.read(end - start + 1)
                self.send_response(206)
                self.send_header('Content-Range',
                                 'bytes %d-%d/%d' % (start, end, total))
            else:
                body = f.read()
                self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Disposition',
                             'attachment; filename="%s"'
                             % os.path.basename(self.path_on_disk))
            self.end_headers()
            self.wfile.write(body)


def serve(path):
    RangeHandler.path_on_disk = path
    srv = HTTPServer(('127.0.0.1', 0), RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d/%s' % (srv.server_address[1],
                                            os.path.basename(path))


# ---------------------------------------------------------------------------

REAL_DIR = os.environ.get('PVL_REAL_ARCHIVES',
                          r'C:\Users\lexzy\Downloads\Compressed')

CANDIDATES = [
    ('Itel_A100C_A6611L_OP_V019_250923_SPD.zip', 'Unisoc, .pac payload'),
    ('Itel_P40_P662L_V261_230118_12_SPD-001.zip', 'Unisoc, 4.2 GB -> ZIP64'),
    ('G556BXXS9.0 - lexzytechinc.com.zip', 'Samsung Odin, 8.4 GB -> ZIP64'),
    ('LB7-H393DEF-O-200726V2812B28_29 - lexzytechinc.com.zip', 'MediaTek'),
    ('Kirin-Tool-v2.4.2.zip', 'small, deflated members'),
]

available = [(n, why) for n, why in CANDIDATES
             if os.path.exists(os.path.join(REAL_DIR, n))]

if not available:
    print('\nNo real archives on this machine (set PVL_REAL_ARCHIVES).')
    print('Skipping — this suite only means something against real files.\n')
    sys.exit(0)


for name, why in available:
    path = os.path.join(REAL_DIR, name)
    size = os.path.getsize(path)
    print('\n== %s ==' % name[:62])
    print('   %s, %s bytes' % (why, '{:,}'.format(size)))

    srv, url = serve(path)
    try:
        z = RemoteZip(url)
        local = zipfile.ZipFile(path)
        lnames = [i.filename for i in local.infolist()]
        rnames = [e['name'] for e in z.entries()]

        ok(z.size == size, 'total size matches', (z.size, size))
        ok(rnames == lnames, 'entry list matches zipfile exactly (%d entries)'
           % len(lnames),
           'first mismatch: %r vs %r' % (rnames[:2], lnames[:2]))

        # Sizes: this is where a ZIP64-blind reader reports 4294967295.
        lsz = {i.filename: i.file_size for i in local.infolist()}
        bad = [e['name'] for e in z.entries() if e['size'] != lsz[e['name']]]
        ok(not bad, 'every uncompressed size matches (ZIP64 resolved)',
           bad[:3])
        big = [e for e in z.entries() if e['size'] >= 0xFFFFFFFF]
        if big:
            ok(all(e['size'] != 0xFFFFFFFF for e in big),
               '  and a >4 GiB member is not left at the 0xFFFFFFFF marker',
               [(e['name'][-40:], e['size']) for e in big[:2]])

        # Read a small member end to end and compare bytes.
        smalls = [e for e in z.entries()
                  if not e['is_dir'] and 0 < e['size'] <= 8192]
        if smalls:
            e = smalls[0]
            got = z.read(e['name'])
            want = local.read(e['name'])
            ok(got == want,
               'read() byte-identical for %s (%d bytes, method %d)'
               % (e['name'].split('/')[-1][:34], e['size'], e['method']),
               '%d vs %d bytes' % (len(got), len(want)))

        # peek() into the middle of a STORED member without pulling it all.
        stored = [e for e in z.entries()
                  if e['method'] == 0 and not e['is_dir'] and e['size'] > 4096]
        if stored:
            e = max(stored, key=lambda x: x['size'])
            off, ln = 2116, 8
            got = z.peek(e['name'], off, ln)
            want = local.read(e['name'])[off:off + ln] if e['size'] < 50_000_000 \
                else None
            ok(len(got) == ln,
               'peek() read %d bytes from offset %d of a %s-byte stored member'
               % (ln, off, '{:,}'.format(e['size'])))
            if want is not None:
                ok(got == want, '  and they match the local bytes',
                   (got, want))

        # A deflated member must refuse peek() rather than pull everything.
        defl = [e for e in z.entries() if e['method'] == 8 and not e['is_dir']]
        if defl:
            try:
                z.peek(defl[0]['name'], 10, 4)
                ok(False, 'peek() refuses a deflated member')
            except RemoteZipError as err:
                ok('deflated' in str(err), 'peek() refuses a deflated member',
                   str(err)[:80])

        local.close()
    finally:
        srv.shutdown()

print('\n%d passed, %d failed\n' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
