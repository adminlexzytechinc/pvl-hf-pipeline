"""
Download a Drive file by asking for byte ranges, several at a time.

WHY THIS EXISTS
---------------
A quota-blocked Drive file is not always unreadable. Measured on
1WwUqFJ2qeSVZOZzYuvlFlLMxyTagcb5c (14.5 GB), same URL, same second:

    GET ...&confirm=t                      -> "Quota exceeded" HTML   5/5
    GET ...&confirm=t  Range: bytes=0-63   -> 206, real bytes         5/5

So the quota page is a response to HOW you ask, not only to WHICH file.
It is NOT a clean switch: a ranged request is often served, not always.
Over 12 sequential 8 MB chunks, 11 landed but 7 needed a retry. The retry
loop is therefore load-bearing, not a nicety.

WHY SEVERAL AT A TIME
---------------------
The throttle is per-connection. Measured over the same 32 MB:

    1 worker  -> 0.41 MB/s
    4 workers -> 1.43 MB/s   (3.5x)
    8 workers -> 1.15 MB/s   (worse -- they fight for the same ceiling)

Four is the measured sweet spot on one home connection. It is a default,
not a law: the ceiling may be that link or a per-IP cap, and those two were
not distinguished. Re-measure on the machine that will actually run this.

WHAT IT GUARANTEES
------------------
Every chunk is written at its true offset in a preallocated file, so the
result is byte-identical to a single-stream download. Splitting a file by
byte ranges and rejoining them in order cannot corrupt it -- the only
failure mode is a MISSING chunk, which is why a chunk that exhausts its
retries raises instead of leaving a silent hole.

Progress is recorded in a sidecar so an interrupted run resumes instead of
starting over. That matters when a file needs hours.
"""

import hashlib
import json
import os
import re
import threading
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

DEFAULT_WORKERS = 4          # measured optimum; 8 was slower than 4
DEFAULT_CHUNK = 8 << 20      # 8 MiB
DEFAULT_TRIES = 6
USERCONTENT = "https://drive.usercontent.google.com/download"


class RangedFetchError(Exception):
    pass


def drive_url(file_id):
    """The plain usercontent URL. It answers ranges even when its own
    unranged GET returns the quota page."""
    return "%s?id=%s&export=download&confirm=t" % (USERCONTENT, file_id)


def signed_url(file_id, timeout=30):
    """The other route: POST /uc hands back a signed googleusercontent URL.

    The empty body is load-bearing -- a bodyless POST returns 411 Length
    Required, never a redirect.
    """
    r = requests.post(
        "https://drive.google.com/uc?export=download&id=%s&confirm=t" % file_id,
        data="", allow_redirects=False, timeout=timeout,
        headers={"User-Agent": UA})
    return r.headers.get("Location") if r.status_code in (302, 303) else None


def _is_html(resp):
    return "text/html" in (resp.headers.get("Content-Type") or "").lower()


def probe(url, timeout=60):
    """Total size and served filename, via a 64-byte range.

    Deliberately ranged: an unranged probe is the one that draws the quota
    page, which is exactly how a live file gets misreported as dead.
    """
    r = requests.get(url, headers={"User-Agent": UA, "Range": "bytes=0-63"},
                     timeout=timeout)
    if _is_html(r) or r.status_code not in (200, 206):
        raise RangedFetchError(
            "probe refused: %d %s" % (r.status_code,
                                      (r.headers.get("Content-Type") or "")[:40]))

    m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
    if m:
        size = int(m.group(1))
    elif r.headers.get("Content-Length"):
        size = int(r.headers["Content-Length"])
    else:
        raise RangedFetchError("server reported no length; cannot range")

    name = None
    disp = r.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", disp) or \
        re.search(r'filename="([^"]+)"', disp)
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(1))
    return size, name


class RangedFetcher:
    def __init__(self, url, dest, size=None, workers=DEFAULT_WORKERS,
                 chunk=DEFAULT_CHUNK, tries=DEFAULT_TRIES,
                 start=0, end=None, on_progress=None):
        self.url = url
        self.dest = dest
        self.workers = workers
        self.chunk = chunk
        self.tries = tries
        self.on_progress = on_progress

        self.size = size if size is not None else probe(url)[0]
        self.start = start
        self.end = self.size - 1 if end is None else min(end, self.size - 1)
        if self.end < self.start:
            raise RangedFetchError("empty window")
        self.span = self.end - self.start + 1

        self.state_path = dest + ".parts"
        self._lock = threading.Lock()
        self._done = set()
        self._bytes = 0
        self._requests = 0
        self._load_state()

    # ---- resume bookkeeping ---------------------------------------------

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return
        try:
            s = json.load(open(self.state_path))
        except Exception:
            return
        # A sidecar only applies to the same window of the same file.
        if (s.get("url_size") == self.size and s.get("start") == self.start
                and s.get("end") == self.end and s.get("chunk") == self.chunk):
            self._done = set(s.get("done", []))
            self._bytes = len(self._done) * self.chunk

    def _save_state(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"url_size": self.size, "start": self.start,
                       "end": self.end, "chunk": self.chunk,
                       "done": sorted(self._done)}, f)
        os.replace(tmp, self.state_path)

    # ---- the work --------------------------------------------------------

    def _chunks(self):
        out = []
        pos = self.start
        i = 0
        while pos <= self.end:
            last = min(pos + self.chunk - 1, self.end)
            out.append((i, pos, last))
            pos = last + 1
            i += 1
        return out

    def _fetch_one(self, idx, first, last):
        want = last - first + 1
        hdr = {"User-Agent": UA, "Range": "bytes=%d-%d" % (first, last)}

        for attempt in range(1, self.tries + 1):
            with self._lock:
                self._requests += 1
            try:
                r = requests.get(self.url, headers=hdr, timeout=300, stream=True)

                # The quota page. Not fatal -- back off and ask again.
                if _is_html(r):
                    r.close()
                    time.sleep(min(2 ** attempt, 30))
                    continue
                if r.status_code not in (200, 206):
                    r.close()
                    time.sleep(min(2 ** attempt, 30))
                    continue

                # A 200 for a ranged ask means the server ignored the Range
                # and is sending the WHOLE file. Writing that at this offset
                # would corrupt everything after it.
                if r.status_code == 200 and want < self.size:
                    r.close()
                    raise RangedFetchError(
                        "server ignored Range (200 for a %d-byte ask); "
                        "refusing to write a misaligned chunk" % want)

                got = 0
                with open(self.dest, "r+b") as f:
                    f.seek(first)
                    for block in r.iter_content(1 << 20):
                        if not block:
                            continue
                        f.write(block)
                        got += len(block)

                if got != want:
                    time.sleep(min(2 ** attempt, 30))
                    continue

                with self._lock:
                    self._done.add(idx)
                    self._bytes += got
                    if self.on_progress:
                        self.on_progress(self._bytes, self.span)
                return got

            except RangedFetchError:
                raise
            except Exception:
                time.sleep(min(2 ** attempt, 30))

        raise RangedFetchError(
            "chunk %d (bytes %d-%d) failed after %d attempts"
            % (idx, first, last, self.tries))

    def run(self):
        """Fetch the window. Returns (bytes_written, seconds, requests_made)."""
        from concurrent.futures import ThreadPoolExecutor

        # Preallocate so every worker can seek to its own offset.
        if not os.path.exists(self.dest) or os.path.getsize(self.dest) < self.end + 1:
            with open(self.dest, "ab") as f:
                f.truncate(self.end + 1)

        todo = [c for c in self._chunks() if c[0] not in self._done]
        if not todo:
            return 0, 0.0, 0

        t0 = time.time()
        errors = []

        def work(c):
            try:
                self._fetch_one(*c)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(work, todo))

        self._save_state()
        if errors:
            raise errors[0]

        return self._bytes, time.time() - t0, self._requests

    def cleanup(self):
        if os.path.exists(self.state_path):
            os.remove(self.state_path)


def sha256_file(path, start=0, length=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while True:
            n = 1 << 20 if left is None else min(1 << 20, left)
            if n <= 0:
                break
            b = f.read(n)
            if not b:
                break
            h.update(b)
            if left is not None:
                left -= len(b)
    return h.hexdigest()
