"""
Gold card details for files stored BEFORE the Gold card step (2026-09-26).

New builds read the details while building (firmware_facts.py). Files already
on Hugging Face are read here instead, remotely, without downloading them:
remote_zip fetches the archive's index (~75 KB) and a few small members
(vbmeta*.img, the scatter file, the first 4 KB of boot.img). A few hundred KB
per file, whatever the file's size.

    1. Ask WordPress which stored files have no details yet
       (admin-ajax.php?action=pvl_facts_todo, signed with the build secret).
    2. Read each one remotely and collect its details.
    3. Send them back as a "facts" report -- the same callback the builds
       use; it writes only the details, nothing else about the file.

A file where nothing could be read is still reported, as {"checked": date},
so it is not read again on every run.

Run from GitHub Actions ("Backfill Gold card details"), or locally:
    SITE_URL=https://lexzytechinc.com BUILD_CALLBACK_SECRET=... \\
    HF_TOKEN=... [HF_ACCOUNTS='{"fw-1": "hf_..."}'] python backfill_facts.py [--limit 50] [--dry-run]
"""

import argparse
import datetime
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firmware_facts                          # noqa: E402
import payload_reader                          # noqa: E402
from remote_zip import RemoteZip, RemoteZipError   # noqa: E402

FIELDS = ("android", "security_patch", "chipset", "model", "storage", "bootloader")


def ajax(site):
    return site.rstrip("/") + "/wp-admin/admin-ajax.php"


def todo(site, secret, limit):
    r = requests.post(ajax(site) + "?action=pvl_facts_todo",
                      json={"secret": secret, "limit": limit}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise RuntimeError("WordPress refused the list: %s" % body.get("data"))
    return body["data"].get("files", [])


def token_for(slug, accounts, fallback):
    return (accounts or {}).get(slug) or fallback


def read_facts(url, token):
    rz = RemoteZip(url, auth=("Bearer " + token) if token else None)
    entries = rz.entries()
    names = [e["name"] for e in entries if not e["name"].endswith("/")]
    payload = payload_reader.read_payload([{"name": e["name"], "size": e["size"]} for e in entries])
    return firmware_facts.collect(names, lambda n, lim: rz.read(n, limit=lim), payload)


def report(site, secret, file_id, facts):
    r = requests.post(ajax(site) + "?action=pvl_build_progress", json={
        "build_id": "backfill", "file_id": file_id, "secret": secret,
        "stage": "facts", "percent": 100, "message": "Gold card details", "fw_facts": facts,
    }, timeout=30)
    return r.ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true", help="read and print, send nothing")
    args = ap.parse_args(argv)

    site = os.environ.get("SITE_URL", "https://lexzytechinc.com")
    secret = os.environ.get("BUILD_CALLBACK_SECRET", "").strip()
    fallback = os.environ.get("HF_TOKEN", "").strip()
    try:
        accounts = json.loads(os.environ.get("HF_ACCOUNTS") or "{}")
    except ValueError:
        accounts = {}
    if not secret:
        sys.exit("BUILD_CALLBACK_SECRET is not set")

    files = todo(site, secret, args.limit)
    print("%d stored file(s) without Gold card details" % len(files))
    done = empty = failed = 0
    for f in files:
        fid, url = f.get("file_id"), f.get("hf_path")
        label = f.get("file_name") or fid
        try:
            facts = read_facts(url, token_for(f.get("repo_slug"), accounts, fallback))
        except (RemoteZipError, requests.RequestException, ValueError) as e:
            failed += 1
            print("  FAILED  %s: %s" % (label, e))
            continue
        shown = ", ".join("%s %s" % (k, facts[k]) for k in FIELDS if facts.get(k))
        if not shown:
            empty += 1
            facts = {"checked": datetime.date.today().isoformat()}
            print("  none    %s" % label)
        else:
            done += 1
            print("  read    %s: %s" % (label, shown))
        if not args.dry_run and not report(site, secret, fid, facts):
            print("          (WordPress did not accept the report)")
    print("\n%d with details, %d with nothing readable, %d failed%s"
          % (done, empty, failed, " -- DRY RUN, nothing sent" if args.dry_run else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
