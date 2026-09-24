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

WHY SEVERAL AT A TIME -- AND WHY THE COUNT ADAPTS
------------------------------------------------
Measured on GitHub runners, three runs, three IPs, 4 workers, same files:

    Fold5  24.73 GB   15.29   16.15   16.59 MB/s   (never throttled)
    S741N  14.57 GB   22.24    3.65    0.33 MB/s   (pulled hard all night)

No worker count was best every time. Concurrency is close to free on a file
Google is not throttling and a gamble on one it is -- in one run 2 workers
took 14 requests to land 4 chunks. So the width is not fixed: it starts
narrow, doubles while every chunk lands on its first request, and halves when
chunks need retries. The signal is requests-per-chunk, which bench_ranged.py
reports for the same reason.

A chunk that will not come now often comes a minute later. So a stubborn chunk
is not retried until it gives up: after a few quick attempts it goes to the
back of the queue and the rest of the file carries on. Only a chunk that has
failed on every revisit ends the download.

Laptop measurements were NOT representative and must not be used to tune
this: from one home IP, 3 of 5 trials failed outright; on runners, 0 of 9.

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

DEFAULT_WORKERS = 4          # the ceiling; 8 was slower than 4 on every host measured
DEFAULT_START_WORKERS = 2    # adaptive width starts here and earns the rest
DEFAULT_CHUNK = 8 << 20      # 8 MiB
DEFAULT_TRIES = 3            # quick attempts per visit before a chunk goes to the back
DEFAULT_ROUNDS = 8           # visits before a chunk is declared unobtainable
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
    """
    Pull [start, end] of `url` into `dest` as byte ranges, several at a time.

    workers        the most connections ever open at once
    start_workers  where the adaptive width begins (adaptive=True)
    tries          quick attempts per visit to a chunk
    rounds         visits before a chunk is declared unobtainable
    deadline       optional wall-clock limit, seconds from run()
    on_progress    called as on_progress(bytes_done, bytes_total)
    """

    def __init__(self, url, dest, size=None, workers=DEFAULT_WORKERS,
                 chunk=DEFAULT_CHUNK, tries=DEFAULT_TRIES, start=0, end=None,
                 on_progress=None, start_workers=DEFAULT_START_WORKERS,
                 adaptive=True, rounds=DEFAULT_ROUNDS, deadline=None,
                 backoff=None, cooldown=None):
        self.url = url
        self.dest = dest
        self.workers = max(1, workers)
        self.start_workers = max(1, min(start_workers, self.workers))
        self.adaptive = adaptive
        self.chunk = chunk
        self.tries = max(1, tries)
        self.rounds = max(1, rounds)
        self.deadline = deadline
        self.on_progress = on_progress
        # Seconds to wait before attempt n of a visit. Injectable so tests do
        # not sleep through real backoff.
        self.backoff = backoff or (lambda attempt: min(2 ** attempt, 30))
        self.cooldown = cooldown or (lambda visit: min(15 * visit, 120))

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
        self.stats = {"requests": 0, "revisits": 0, "widths": []}
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
            sizes = {i: last - first + 1 for i, first, last in self._chunks()}
            self._bytes = sum(sizes.get(i, 0) for i in self._done)

    def _save_state(self):
        tmp = self.state_path + ".tmp"
        with self._lock:
            done = sorted(self._done)
        with open(tmp, "w") as f:
            json.dump({"url_size": self.size, "start": self.start,
                       "end": self.end, "chunk": self.chunk, "done": done}, f)
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
        """
        One visit to one chunk: up to `tries` quick attempts.
        Returns the bytes written, or None if this visit did not land it --
        the caller decides whether to come back. Raises only on a condition no
        retry can fix.
        """
        want = last - first + 1
        hdr = {"User-Agent": UA, "Range": "bytes=%d-%d" % (first, last)}

        for attempt in range(1, self.tries + 1):
            with self._lock:
                self._requests += 1
            try:
                r = requests.get(self.url, headers=hdr, timeout=300, stream=True)

                # The quota page, or any refusal. Not fatal -- back off and ask again.
                if _is_html(r) or r.status_code not in (200, 206):
                    r.close()
                    if attempt < self.tries:
                        time.sleep(self.backoff(attempt))
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
                        # Never write past this chunk, whatever the server sends.
                        block = block[:want - got]
                        f.write(block)
                        got += len(block)
                        if got >= want:
                            break
                r.close()

                if got != want:
                    if attempt < self.tries:
                        time.sleep(self.backoff(attempt))
                    continue

                with self._lock:
                    self._done.add(idx)
                    self._bytes += got
                    done_bytes = self._bytes
                if self.on_progress:
                    try:
                        self.on_progress(done_bytes, self.span)
                    except Exception:
                        pass
                return got

            except RangedFetchError:
                raise
            except Exception:
                if attempt < self.tries:
                    time.sleep(self.backoff(attempt))
        return None

    def run(self):
        """Fetch the window. Returns (bytes_written, seconds, requests_made)."""
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor

        # Preallocate so every worker can seek to its own offset.
        if not os.path.exists(self.dest) or os.path.getsize(self.dest) < self.end + 1:
            with open(self.dest, "ab") as f:
                f.truncate(self.end + 1)

        pending = deque(c for c in self._chunks() if c[0] not in self._done)
        if not pending:
            return 0, 0.0, 0

        t0 = time.time()
        visits = {}
        width = self.start_workers if self.adaptive else self.workers

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            while pending:
                if self.deadline and time.time() - t0 > self.deadline:
                    self._save_state()
                    raise RangedFetchError(
                        "deadline of %ds reached with %d chunk(s) left"
                        % (self.deadline, len(pending)))

                batch = [pending.popleft() for _ in range(min(width, len(pending)))]
                before = self._requests
                results = list(ex.map(lambda c: self._fetch_one(*c), batch))
                used = self._requests - before
                failed = [c for c, got in zip(batch, results) if got is None]

                for c in failed:
                    visits[c[0]] = visits.get(c[0], 0) + 1
                    self.stats["revisits"] += 1
                    if visits[c[0]] >= self.rounds:
                        self._save_state()
                        raise RangedFetchError(
                            "chunk %d (bytes %d-%d) failed on %d visits of %d attempts"
                            % (c[0], c[1], c[2], self.rounds, self.tries))
                    # To the BACK: the rest of the file carries on, and this
                    # one is asked again later, when the throttle may have eased.
                    pending.append(c)

                # A whole batch refused means every connection is being turned
                # away. Asking again at once only feeds the throttle.
                if failed and len(failed) == len(batch):
                    time.sleep(self.cooldown(max(visits[c[0]] for c in failed)))

                if self.adaptive:
                    if not failed and used <= len(batch):
                        width = min(width * 2, self.workers)
                    elif failed or used > 1.5 * len(batch):
                        width = max(1, width // 2)
                self.stats["widths"].append(width)
                self._save_state()

        self.stats["requests"] = self._requests
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
