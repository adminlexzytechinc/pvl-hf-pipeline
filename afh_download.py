"""
AndroidFileHost download step for hf_build_worker.py (added 2026-10-04).

Tested by hand first, from the owner's PC, on four Spark 9 Pro files:
no login, no captcha, no wait time. One POST returns direct mirror links
(Virginia, USA), valid about a day, and every mirror serves byte ranges
(HTTP 206) -- so a dropped download resumes where it stopped instead of
starting again, and the Gold card details can be read before downloading.

    1. GET  androidfilehost.com/?fid=N      -> name, size (and a session cookie)
    2. POST /libs/otf/mirrors.otf.php        -> {"MIRRORS": [{"name", "url"}, ...]}
    3. GET  the mirror, resuming with Range until the file is whole;
       on a mirror that keeps failing, the next one.

Drop-in shape matches mega_download(): returns (ok, real_name).
"""

import os
import re
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BASE = "https://androidfilehost.com"
CHUNK = 64 * 1024           # small: a dropped connection loses at most this much
TRIES_PER_MIRROR = 4        # resumes on one mirror before trying the next
STALL_SECONDS = 120         # no bytes for this long: drop and resume

_FID = re.compile(r'(?:[?&]fid=|^afh_|^)(\d{10,25})(?:$|[&#])')


def parse_fid(file_id="", url=""):
    """The AFH file number from 'afh_4279…', a bare number, or a page URL."""
    for s in (url or "", file_id or ""):
        m = _FID.search(s.strip())
        if m:
            return m.group(1)
    return ""


def is_afh(source_type="", file_id="", url=""):
    return ((source_type or "").lower() == "afh" or (file_id or "").startswith("afh_")
            or "androidfilehost.com" in (url or "").lower())


def file_info(fid, session=None):
    """{'name', 'size'} from the file page; name/size may be ''/0."""
    s = session or requests.Session()
    r = s.get(BASE + "/?fid=" + fid, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    html = r.text
    name = ""
    m = re.search(r'<title>\s*([^|<]+?)\s*\|', html)
    if m:
        name = m.group(1).strip()
    m = re.search(r'id="file_size"\s+value="(\d+)"', html)
    size = int(m.group(1)) if m else 0
    return {"name": name, "size": size}


def mirrors(fid, session=None):
    """[(mirror name, url), ...] -- empty when AFH refuses."""
    s = session or requests.Session()
    r = s.post(BASE + "/libs/otf/mirrors.otf.php",
               data={"submit": "submit", "action": "getdownloadmirrors", "fid": fid},
               headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                        "Referer": BASE + "/?fid=" + fid}, timeout=30)
    r.raise_for_status()
    try:
        d = r.json()
    except ValueError:
        return []
    return [(m.get("name", "?"), m["url"]) for m in (d.get("MIRRORS") or []) if m.get("url")]


def _fetch(url, dest, total, progress):
    """Download/resume url into dest until it holds `total` bytes.
    Returns True when whole."""
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    if total and have > total:
        os.unlink(dest)
        have = 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = "bytes=%d-" % have
    with requests.get(url, headers=headers, stream=True, timeout=(30, STALL_SECONDS)) as r:
        if have and r.status_code == 200:
            have = 0                    # mirror ignored the Range: start over
        elif r.status_code not in (200, 206):
            print("  AndroidFileHost mirror answered HTTP %d" % r.status_code)
            return False
        if not total:
            cr = r.headers.get("Content-Range", "")
            total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else int(r.headers.get("Content-Length", 0)) + have
        last = time.time()
        with open(dest, "ab" if have else "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK):
                f.write(chunk)
                have += len(chunk)
                if progress and time.time() - last > 10:
                    progress(have, total)
                    last = time.time()
    return bool(total) and os.path.getsize(dest) == total


def afh_download(file_id, source_url, dest_path, progress=None):
    """Returns (ok, real_name). progress(done_bytes, total_bytes)."""
    fid = parse_fid(file_id, source_url)
    if not fid:
        print("  No AndroidFileHost file number in %r / %r" % (file_id, source_url))
        return False, None
    s = requests.Session()
    try:
        info = file_info(fid, s)
    except Exception as e:
        print("  AndroidFileHost page failed: %s" % e)
        info = {"name": "", "size": 0}
    total = info["size"]
    print("  AndroidFileHost file %s: %s (%d MB)" % (fid, info["name"] or "?", total // (1024 * 1024)))

    for round_ in (1, 2):
        try:
            mlist = mirrors(fid, s)
        except Exception as e:
            print("  AndroidFileHost mirror list failed: %s" % e)
            mlist = []
        if not mlist:
            print("  AndroidFileHost gave no mirrors (round %d)" % round_)
            time.sleep(5)
            continue
        for name, url in mlist:
            print("  AndroidFileHost mirror: %s" % name)
            for attempt in range(1, TRIES_PER_MIRROR + 1):
                try:
                    if _fetch(url, dest_path, total, progress):
                        print("  AndroidFileHost download complete: %d bytes" % os.path.getsize(dest_path))
                        return True, info["name"] or None
                except Exception as e:
                    print("  AndroidFileHost transfer dropped (%s); resuming (%d/%d)"
                          % (str(e)[:120], attempt, TRIES_PER_MIRROR))
                time.sleep(3)
    return False, info["name"] or None
