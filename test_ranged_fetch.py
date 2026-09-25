"""
RangedFetcher against a local server that misbehaves the way Drive does.

Drive was measured doing four things to a ranged downloader, and each is a
case here:

  1. serving cleanly                    -> width should grow, bytes identical
  2. refusing one chunk for a while     -> the rest must finish first, then it
                                           must land on a later visit
  3. refusing one chunk for good        -> must raise, never leave a hole
  4. ignoring Range and sending it all  -> must raise, never write misaligned

plus the resume sidecar and the deadline. Deterministic: no network, and the
backoff and cool-down are injected so nothing sleeps for real.
"""

import hashlib
import os
import random
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ranged_fetch import RangedFetcher, RangedFetchError

PASS = FAIL = 0


def ok(cond, label, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   ' + label)
    else:
        FAIL += 1
        print('  FAIL ' + label + ('\n       ' + str(detail) if detail else ''))


CHUNK = 64 * 1024
random.seed(3)
DATA = bytes(random.getrandbits(8) for _ in range(CHUNK * 10 + 1234))  # 11 chunks
QUOTA = b'<!DOCTYPE html><html><head><title>Google Drive - Quota exceeded</title></head></html>'


class Server:
    """Behaviour is swapped per test by setting attributes."""
    refuse = {}          # chunk index -> how many more times to refuse (-1 = forever)
    ignore_range = False
    concurrent = 0
    peak = 0
    order = []           # chunk indexes in the order they were served
    lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get('Range', '')
        with Server.lock:
            Server.concurrent += 1
            Server.peak = max(Server.peak, Server.concurrent)
        try:
            if Server.ignore_range or not rng.startswith('bytes='):
                self._send(200, DATA, 'application/zip')
                return
            a, _, b = rng[6:].partition('-')
            first, last = int(a), min(int(b), len(DATA) - 1)
            idx = first // CHUNK
            with Server.lock:
                left = Server.refuse.get(idx, 0)
                if left:
                    if left > 0:
                        Server.refuse[idx] = left - 1
                    refused = True
                else:
                    refused = False
                    Server.order.append(idx)
            if refused:
                self._send(200, QUOTA, 'text/html; charset=utf-8')
                return
            body = DATA[first:last + 1]
            self.send_response(206)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (first, last, len(DATA)))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with Server.lock:
                Server.concurrent -= 1

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = 'http://127.0.0.1:%d/file' % srv.server_address[1]
TMP = tempfile.mkdtemp()
NO_WAIT = dict(backoff=lambda a: 0, cooldown=lambda v: 0)


def reset(**kw):
    Server.refuse = dict(kw.get('refuse', {}))
    Server.ignore_range = kw.get('ignore_range', False)
    Server.peak = 0
    Server.order = []


def fresh(name):
    p = os.path.join(TMP, name)
    for f in (p, p + '.parts'):
        if os.path.exists(f):
            os.remove(f)
    return p


def same(path):
    return hashlib.sha256(open(path, 'rb').read()).digest() == hashlib.sha256(DATA).digest()


print('\n== 1. A clean server ==')
reset()
dest = fresh('clean.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, **NO_WAIT)
got, secs, reqs = f.run()
ok(same(dest), 'file is byte-identical to the source')
ok(got == len(DATA), 'every byte accounted for (%d)' % got)
ok(reqs == 11, 'one request per chunk when nothing is refused (%d)' % reqs)
ok(f.stats['widths'][:2] == [4, 4] and max(f.stats['widths']) == 4,
   'width starts at 2, doubles to the ceiling of 4, and stays', f.stats['widths'])
ok(Server.peak <= 4, 'never more than 4 connections open (peak %d)' % Server.peak)

print('\n== 2. One stubborn chunk ==')
reset(refuse={2: 5})            # chunk 2 refused 5 times, then served
dest = fresh('stubborn.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, tries=3, **NO_WAIT)
got, secs, reqs = f.run()
ok(same(dest), 'file is byte-identical once the chunk finally lands')
ok(f.stats['revisits'] >= 1, 'the stubborn chunk was sent to the back (%d revisits)'
   % f.stats['revisits'])
ok(Server.order[-1] == 2, 'every other chunk finished BEFORE the stubborn one',
   Server.order)
ok(any(w < 4 for w in f.stats['widths'][1:]),
   'width backed off while a chunk was being refused', f.stats['widths'])

print('\n== 3. A chunk that never comes ==')
reset(refuse={5: -1})
dest = fresh('never.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, tries=2, rounds=3, **NO_WAIT)
try:
    f.run()
    ok(False, 'raises instead of finishing with a hole')
except RangedFetchError as e:
    ok('chunk 5' in str(e), 'raises, naming the missing chunk', str(e))
ok(os.path.exists(dest + '.parts'), 'progress is saved so a rerun can resume')

print('\n== 4. Resume picks up only what is missing ==')
reset()                          # the chunk is available now
f2 = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, **NO_WAIT)
got, secs, reqs = f2.run()
ok(reqs == 1 and Server.order == [5], 'only chunk 5 is fetched on the rerun', Server.order)
ok(same(dest), 'and the resumed file is byte-identical')

print('\n== 5. A server that ignores Range ==')
reset(ignore_range=True)
dest = fresh('norange.bin')
try:
    RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, **NO_WAIT).run()
    ok(False, 'refuses to write a whole-file response at a chunk offset')
except RangedFetchError as e:
    ok('ignored Range' in str(e), 'refuses to write a whole-file response at a chunk offset')

print('\n== 6. The deadline ==')
reset(refuse={0: -1, 1: -1, 2: -1, 3: -1})
dest = fresh('deadline.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, tries=1, rounds=1000,
                  deadline=0.3, backoff=lambda a: 0, cooldown=lambda v: 0.05)
try:
    f.run()
    ok(False, 'stops at the deadline')
except RangedFetchError as e:
    ok('deadline' in str(e), 'stops at the deadline and says so', str(e))

print('\n== 6b. Every slice refused: stop on the stall limit, not the deadline ==')
reset(refuse={i: -1 for i in range(11)})
dest = fresh('stall.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, tries=1, rounds=1000,
                  deadline=60, stall=0.3, backoff=lambda a: 0, cooldown=lambda v: 0.05)
t0 = time.time()
try:
    f.run()
    ok(False, 'stops when no slice lands')
except RangedFetchError as e:
    ok('no slice served' in str(e) and time.time() - t0 < 5,
       'stops after the stall limit, long before the deadline', str(e))

print('\n== 6c. A slow file that keeps landing slices is NOT stopped ==')
reset(refuse={2: 3, 6: 3})
dest = fresh('slow.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, tries=1, stall=0.5,
                  backoff=lambda a: 0, cooldown=lambda v: 0.05)
f.run()
ok(same(dest), 'finishes intact while slices keep arriving')

print('\n== 7. Fixed width when adaptive is off ==')
reset()
dest = fresh('fixed.bin')
f = RangedFetcher(URL, dest, size=len(DATA), chunk=CHUNK, workers=3, adaptive=False, **NO_WAIT)
f.run()
ok(set(f.stats['widths']) == {3} and same(dest), 'width stays at 3, file intact',
   f.stats['widths'])

srv.shutdown()
print('\n%d passed, %d failed\n' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
