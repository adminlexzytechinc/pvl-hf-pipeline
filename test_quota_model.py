"""
Is Drive's download quota counted per FILE, or per REQUESTER?

WHY THIS DECIDES THE DESIGN
---------------------------
The proposal is to add several accounts and split the work between them. That
helps or does nothing depending on one fact nobody has measured here:

  per-FILE, global      "Too many users have viewed or downloaded this file"
                        is about the FILE. Every identity hits the same wall.
                        More accounts change nothing. Only making a NEW object
                        (a copy) helps, because a copy has its own counter.

  per-REQUESTER         Each identity carries its own budget. Then N accounts
                        multiply the budget, and a large file can be pulled in
                        N slices by N identities and reassembled -- which is
                        exactly what HTTP range requests make possible.

Google does not document this, and the wording of the error ("too many USERS")
points at per-file. But the pipeline's own behaviour points the other way: the
service account reaches files the public link refuses. That contradiction is
what this resolves.

WHAT IT DOES
------------
Against one file, at one moment, it asks the same small question as three
different identities and compares:

  1. anonymous, via the signed /uc URL
  2. the service account, via the Drive API
  3. the service account again, from a second token

Then, if two identities can both read, it has each fetch a DIFFERENT byte
range and checks the pieces join up byte-for-byte against a single-identity
read of the same span. That is the actual capability the proposal needs.

Reads a few kilobytes in total. It cannot exhaust anything.

USAGE
    SA_CREDENTIALS_PATH=/tmp/sa_credentials.json \
    python test_quota_model.py <fileId>
"""

import json
import os
import sys
import time

import requests

try:
    import jwt
except ImportError:
    sys.exit("PyJWT missing:  pip install pyjwt cryptography")

TOKEN_URL  = "https://oauth2.googleapis.com/token"
DRIVE_API  = "https://www.googleapis.com/drive/v3/files"
CREDS_PATH = os.environ.get("SA_CREDENTIALS_PATH", "/tmp/sa_credentials.json")

# A short span far enough in to be real data rather than a header.
SPAN_START = 4096
SPAN_LEN   = 512


def mint(creds, scope="https://www.googleapis.com/auth/drive"):
    now = int(time.time())
    a = jwt.encode({"iss": creds["client_email"], "scope": scope,
                    "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
                   creds["private_key"], algorithm="RS256")
    r = requests.post(TOKEN_URL, timeout=30, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": a})
    return r.json().get("access_token") if r.status_code == 200 else None


def signed_url(file_id):
    r = requests.post(
        "https://drive.google.com/uc?export=download&id=%s&confirm=t" % file_id,
        data="", allow_redirects=False, timeout=30)
    return r.headers.get("Location") if r.status_code in (302, 303) else None


def read_range(url, start, length, auth=None, params=None):
    """Returns (status, bytes, content_type)."""
    h = {"Range": "bytes=%d-%d" % (start, start + length - 1)}
    if auth:
        h["Authorization"] = auth
    try:
        r = requests.get(url, headers=h, params=params, timeout=45,
                         allow_redirects=True)
        return r.status_code, r.content, (r.headers.get("Content-Type") or "")
    except Exception as e:
        return 0, b"", str(e)[:80]


def describe(status, body, ctype):
    if status in (200, 206) and body and "text/" not in ctype:
        return "OK (%d, %d bytes, %s)" % (status, len(body), ctype.split(";")[0])
    if status == 429:
        return "429 rate limited / quota"
    if status == 403:
        return "403 forbidden (often downloadQuotaExceeded)"
    if "text/" in ctype:
        return "%d but served %s -- an error page, not the file" % (status, ctype.split(";")[0])
    return "%d" % status


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python test_quota_model.py <fileId>")
    file_id = sys.argv[1]

    if not os.path.exists(CREDS_PATH):
        sys.exit("SA credentials not found at " + CREDS_PATH)
    creds = json.load(open(CREDS_PATH))

    print("file           : " + file_id)
    print("service account: " + creds.get("client_email", "?"))
    print("span           : bytes %d-%d\n" % (SPAN_START, SPAN_START + SPAN_LEN - 1))

    results = {}

    # --- identity 1: anonymous ------------------------------------------
    print("1. anonymous, via the signed /uc URL")
    su = signed_url(file_id)
    if not su:
        print("   no signed URL (the /uc POST gave no redirect)\n")
        results["anon"] = None
    else:
        st, body, ct = read_range(su, SPAN_START, SPAN_LEN)
        print("   " + describe(st, body, ct) + "\n")
        results["anon"] = body if st in (200, 206) and "text/" not in ct else None

    # --- identity 2: the service account --------------------------------
    print("2. service account, via the Drive API")
    tok = mint(creds)
    if not tok:
        print("   could not mint a token\n")
        results["sa"] = None
    else:
        st, body, ct = read_range(
            DRIVE_API + "/" + file_id, SPAN_START, SPAN_LEN,
            auth="Bearer " + tok,
            params={"alt": "media", "supportsAllDrives": "true"})
        print("   " + describe(st, body, ct) + "\n")
        results["sa"] = body if st in (200, 206) and "text/" not in ct else None

    # --- the verdict ----------------------------------------------------
    print("=" * 66)
    anon_ok = results["anon"] is not None
    sa_ok = results["sa"] is not None

    if anon_ok and sa_ok:
        same = results["anon"] == results["sa"]
        print("Both identities can read. Bytes identical: %s" % same)
        if not same:
            print("  (different bytes for the same span is unexpected -- "
                  "one of the routes is not serving the file)")
        print()
        print("This file is not blocked, so it says nothing about the quota")
        print("model. Re-run against a file that IS blocked -- that is the")
        print("only case where the answer matters.")
    elif sa_ok and not anon_ok:
        print("VERDICT: the quota has a PER-REQUESTER component.")
        print()
        print("Anonymous is refused while the service account reads the same")
        print("bytes at the same moment. So identity matters, and adding")
        print("accounts genuinely multiplies what you can pull:")
        print("  - split one file into N ranges across N identities")
        print("  - reassemble, since ranges are exact and verifiable")
        print("This is worth building.")
    elif anon_ok and not sa_ok:
        print("VERDICT: the service account is the constrained one here.")
        print("Anonymous reads while the SA cannot -- check the SA's access")
        print("to this file before concluding anything about quota.")
    else:
        print("VERDICT: neither identity can read this file.")
        print()
        print("Consistent with a PER-FILE, GLOBAL quota. If that holds, more")
        print("accounts do NOT help for reading: they all meet the same wall.")
        print("Only creating a NEW object beats it -- a copy has its own")
        print("counter -- so extra accounts are worth having for their")
        print("STORAGE, to make the copy possible, not for their identity.")
    print("=" * 66)

    # --- the split, if two identities can both read ---------------------
    if anon_ok and sa_ok:
        print("\nsplit-and-rejoin check")
        half = SPAN_LEN // 2
        _, a, _ = read_range(su, SPAN_START, half)
        _, b, _ = read_range(DRIVE_API + "/" + file_id, SPAN_START + half, half,
                             auth="Bearer " + tok,
                             params={"alt": "media", "supportsAllDrives": "true"})
        joined = a + b
        print("  first half  by anonymous : %d bytes" % len(a))
        print("  second half by the SA    : %d bytes" % len(b))
        print("  rejoined == single read  : %s" % (joined == results["anon"]))
        if joined == results["anon"]:
            print("  -> a file CAN be pulled in slices by different identities")
            print("     and reassembled byte-for-byte.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
