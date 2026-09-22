"""
How fast can THIS machine pull a Drive file by ranges, and how reliably?

Every number in ranged_fetch.py's header was measured on one home
connection in Nigeria, in one hour, against two files Google had already
watched that IP pull repeatedly. Those numbers contradicted each other:
4 workers beat 1 worker 3.5x in one trial and lost to it in the next. So
they are not a basis for a design decision, and this exists to replace
them with numbers from the machine that will actually do the work.

It reports, and concludes nothing it did not measure:

  1. the line speed, against a CDN, so Drive's throughput can be compared
     to what the machine is actually capable of
  2. whether the file answers an unranged GET, a ranged GET, or neither
  3. throughput and failure rate at 1, 2 and 4 workers
  4. whether parallel chunks rejoin byte-for-byte (verified against the
     server, by re-fetching random samples -- not against itself)

Nothing is written outside the working directory, and the largest window
is 32 MiB per trial, so a full run moves well under half a gigabyte.
"""

import os
import random
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ranged_fetch import (RangedFetcher, RangedFetchError, drive_url,
                          signed_url, probe, UA)

WINDOW = 32 << 20
CHUNK = 8 << 20
CDN = "https://speed.cloudflare.com/__down?bytes=25000000"


def line_speed():
    """What the machine can do when nothing is throttling it."""
    from concurrent.futures import ThreadPoolExecutor

    def one(_):
        r = requests.get(CDN, timeout=120, stream=True)
        return sum(len(c) for c in r.iter_content(1 << 20))

    t0 = time.time()
    n = one(0)
    single = n / 1048576 / (time.time() - t0)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        tot = sum(ex.map(one, range(4)))
    quad = tot / 1048576 / (time.time() - t0)
    return single, quad


def reachability(file_id):
    """Does this file answer an unranged ask, a ranged ask, or neither?"""
    url = drive_url(file_id)
    out = {}

    r = requests.get(url, headers={"User-Agent": UA}, timeout=60, stream=True)
    ct = (r.headers.get("Content-Type") or "").lower()
    out["unranged"] = "quota page" if "text/html" in ct else "served (%d)" % r.status_code
    r.close()

    try:
        size, name = probe(url)
        out["ranged"] = "served"
        out["size"] = size
        out["name"] = name
    except RangedFetchError as e:
        out["ranged"] = "refused: %s" % e
        out["size"] = None
        out["name"] = None
    return out


def trial(url, size, workers, base, tag):
    dest = "bench_%s_%dw.part" % (tag, workers)
    for p in (dest, dest + ".parts"):
        if os.path.exists(p):
            os.remove(p)
    t0 = time.time()
    try:
        f = RangedFetcher(url, dest, size=size, workers=workers, chunk=CHUNK,
                          tries=10, start=base, end=base + WINDOW - 1)
        got, secs, reqs = f.run()
        n_chunks = WINDOW // CHUNK
        print("    %d worker(s): OK    %6.1fs  %5.2f MB/s  "
              "%2d requests for %d chunks"
              % (workers, secs, got / 1048576 / secs, reqs, n_chunks))
        return dest, base, got / 1048576 / secs
    except Exception as e:
        print("    %d worker(s): FAIL  %6.1fs  %s"
              % (workers, time.time() - t0, str(e)[:64]))
        for p in (dest, dest + ".parts"):
            if os.path.exists(p):
                os.remove(p)
        return None, None, None


def verify(dest, base, url, samples=6):
    """Re-fetch random spans from the server and compare to what landed."""
    random.seed(11)
    good = checked = 0
    for _ in range(samples):
        off = random.randrange(0, WINDOW - 4096)
        hdr = {"User-Agent": UA,
               "Range": "bytes=%d-%d" % (base + off, base + off + 4095)}
        body = None
        for attempt in range(6):
            r = requests.get(url, headers=hdr, timeout=120)
            if "text/html" not in (r.headers.get("Content-Type") or "").lower():
                body = r.content
                break
            time.sleep(2 * (attempt + 1))
        if body is None:
            continue
        checked += 1
        with open(dest, "rb") as fh:
            fh.seek(base + off)
            if fh.read(4096) == body:
                good += 1
    return good, checked


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python bench_ranged.py <fileId> [fileId ...]")

    print("=" * 70)
    print("LINE SPEED (CDN, nothing throttling)")
    s, q = line_speed()
    print("  1 stream : %5.2f MB/s" % s)
    print("  4 streams: %5.2f MB/s" % q)
    print("=" * 70)

    for file_id in sys.argv[1:]:
        print("\nFILE %s" % file_id)
        info = reachability(file_id)
        print("  unranged GET : %s" % info["unranged"])
        print("  ranged GET   : %s" % info["ranged"])
        if not info["size"]:
            print("  -> unreachable by range; nothing further to measure")
            continue
        print("  name         : %s" % info["name"])
        print("  size         : {:,} bytes ({:.2f} GB)".format(
            info["size"], info["size"] / 1e9))

        url = drive_url(file_id)
        print("\n  throughput (32 MiB per trial, fresh region each time)")
        best = None
        for i, w in enumerate((1, 2, 4)):
            base = (1 + i) * 1_000_000_000
            dest, b, rate = trial(url, info["size"], w, base, file_id[:6])
            if dest and (best is None or rate > best[2]):
                if best:
                    for p in (best[0], best[0] + ".parts"):
                        if os.path.exists(p):
                            os.remove(p)
                best = (dest, b, rate)
            elif dest:
                for p in (dest, dest + ".parts"):
                    if os.path.exists(p):
                        os.remove(p)

        if best:
            good, checked = verify(best[0], best[1], url)
            print("\n  integrity: %d/%d re-fetched samples matched%s"
                  % (good, checked,
                     "" if checked else " (none could be re-fetched)"))
            print("  projected full file at %.2f MB/s: %.1f h"
                  % (best[2], info["size"] / (best[2] * 1048576) / 3600))
            for p in (best[0], best[0] + ".parts"):
                if os.path.exists(p):
                    os.remove(p)
        else:
            print("\n  every worker count failed on this file")

        sg = signed_url(file_id)
        print("  signed URL   : %s" % ("minted" if sg else "not offered"))

    print("\n" + "=" * 70)
    print("Compare Drive's MB/s to the line speed above. If Drive is far")
    print("below it, the limit is Google's throttle and a faster machine")
    print("will not help. If Drive is near it, the link was the limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
