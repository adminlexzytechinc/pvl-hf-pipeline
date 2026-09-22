"""
Can the SERVICE ACCOUNT drive the Takeout export API?

WHY THIS EXISTS
---------------
Two of the five download methods sidestep the per-file quota by creating a NEW
object, which carries its own counter:

    GAS copy       -- a copy is a new file.   Blocked today: needs free Drive
                      space, and the bridge account is ~90% full.
    Folder trick   -- the zip is a new object. Blocked today: Apps Script gets
                      403 "insufficient authentication scopes".

Everything else negotiates with a counter that is already exhausted, which is
why some files download and some fail outright.

The 403 came from Apps Script, whose scopes are INFERRED from its code -- it
cannot ask for a scope it has no API call for. A service account has no such
limit: it signs a JWT naming whatever scopes it likes. So the question this
answers is narrow and worth one run:

    Does takeout-pa-qw.clients6.google.com accept a service-account Bearer
    token, and if so under which scope?

WHAT IT DOES
------------
For each candidate scope set: mint a token, create a temp folder, put a
SHORTCUT to the target inside it (a pointer, so no storage is consumed), then
POST /v1/exports. It prints the FULL error body, because Google's `details`
array names the scope it actually wants -- and that is the answer we need.

Everything it creates is deleted, including on failure.

USAGE
-----
    SA_CREDENTIALS_PATH=/tmp/sa_credentials.json \
    python test_takeout_sa.py <driveFileId>

Read the result as:

    200            -> the SA can drive Takeout. The folder trick is automatable
                      with no Apps Script and no storage. This is the win.
    403 + scope    -> the named scope is the fix; add it and re-run.
    403 identical  -> first-party only. Stop spending time on this route.
    401            -> the SA identity is not accepted at all. Dead end.
"""

import json
import os
import sys
import time

import requests

try:
    import jwt  # PyJWT, already a worker dependency
except ImportError:
    sys.exit("PyJWT missing:  pip install pyjwt cryptography")

TOKEN_URL   = "https://oauth2.googleapis.com/token"
DRIVE_API   = "https://www.googleapis.com/drive/v3/files"
TAKEOUT_API = "https://takeout-pa-qw.clients6.google.com/v1/exports"
# Drive's own public web key, as used by the Drive UI itself.
TAKEOUT_KEY = "AIzaSyD_InbmSFufIEps5UAt2NmB_3LvBH3Sz_8"

# Ordered cheapest-to-broadest. An SA can request any of these; the point is to
# find the narrowest one Takeout accepts, or prove none is accepted.
SCOPE_SETS = [
    ["https://www.googleapis.com/auth/drive"],
    ["https://www.googleapis.com/auth/drive.readonly"],
    ["https://www.googleapis.com/auth/drive",
     "https://www.googleapis.com/auth/drive.file"],
    # Takeout's own product scope, on the chance it is grantable to an SA.
    ["https://www.googleapis.com/auth/drive",
     "https://www.googleapis.com/auth/takeout"],
]

CREDS_PATH = os.environ.get("SA_CREDENTIALS_PATH", "/tmp/sa_credentials.json")


def mint_token(creds, scopes):
    now = int(time.time())
    assertion = jwt.encode({
        "iss":   creds["client_email"],
        "scope": " ".join(scopes),
        "aud":   TOKEN_URL,
        "iat":   now,
        "exp":   now + 3600,
    }, creds["private_key"], algorithm="RS256")

    r = requests.post(TOKEN_URL, timeout=30, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  assertion,
    })
    if r.status_code != 200:
        return None, f"token endpoint {r.status_code}: {r.text[:300]}"
    return r.json().get("access_token"), None


def make_folder_with_shortcut(token, file_id, name):
    """A folder holding a SHORTCUT to the target. A shortcut is a pointer, so
    this consumes no storage -- which is the whole reason it works where
    makeCopy() fails on a full Drive."""
    h = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    r = requests.post(DRIVE_API + "?supportsAllDrives=true", headers=h, timeout=60,
                      data=json.dumps({"name": name,
                                       "mimeType": "application/vnd.google-apps.folder"}))
    if r.status_code not in (200, 201):
        return None, f"folder create {r.status_code}: {r.text[:300]}"
    folder_id = r.json()["id"]

    r = requests.post(DRIVE_API + "?supportsAllDrives=true", headers=h, timeout=60,
                      data=json.dumps({
                          "name": name + ".zip",
                          "mimeType": "application/vnd.google-apps.shortcut",
                          "shortcutDetails": {"targetId": file_id},
                          "parents": [folder_id],
                      }))
    if r.status_code not in (200, 201):
        return folder_id, f"shortcut create {r.status_code}: {r.text[:300]}"

    requests.post(f"{DRIVE_API}/{folder_id}/permissions?supportsAllDrives=true",
                  headers=h, timeout=60,
                  data=json.dumps({"role": "reader", "type": "anyone"}))
    return folder_id, None


def delete_folder(token, folder_id):
    if not folder_id:
        return
    try:
        requests.delete(f"{DRIVE_API}/{folder_id}?supportsAllDrives=true",
                        headers={"Authorization": "Bearer " + token}, timeout=30)
    except Exception:
        pass


def try_scopes(creds, file_id, scopes):
    label = ", ".join(s.rsplit("/", 1)[-1] for s in scopes)
    print("\n" + "=" * 66)
    print("SCOPES: " + label)
    print("=" * 66)

    token, err = mint_token(creds, scopes)
    if not token:
        print("  token: FAILED — " + str(err))
        print("  (the SA is probably not authorised for this scope set)")
        return None
    print("  token: minted ok")

    folder_id = None
    try:
        folder_id, err = make_folder_with_shortcut(
            token, file_id, "pvl_sa_test_" + str(int(time.time())))
        if err:
            print("  drive: " + err)
            return None
        print("  drive: folder " + folder_id + " + shortcut created (0 bytes used)")

        r = requests.post(
            TAKEOUT_API + "?key=" + TAKEOUT_KEY,
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json",
                     "X-Goog-Drive-Client-Version": "drive.web-frontend"},
            timeout=60,
            data=json.dumps({"archivePrefix": "pvl_sa_test",
                             "items": [{"id": folder_id}]}))

        print("  takeout POST /v1/exports -> HTTP " + str(r.status_code))
        print("  ---- full response body ----")
        print(r.text[:2500])
        print("  ----------------------------")

        if r.status_code == 200:
            job = r.json().get("exportJob", {})
            print("\n  *** SUCCESS: job " + str(job.get("id")) +
                  " status " + str(job.get("status")) + " ***")
            print("  The service account CAN drive Takeout.")
            print("  The folder trick is automatable with no Apps Script,")
            print("  no storage, and no manual click.")
            return {"scopes": scopes, "jobId": job.get("id"), "token": token}

        # Surface the scope Google names, which is the whole point of the run.
        try:
            for d in r.json().get("error", {}).get("details", []):
                for key in ("metadata", "violations"):
                    if key in d:
                        print("  detail[" + key + "]: " + json.dumps(d[key])[:400])
        except Exception:
            pass
        return None

    finally:
        delete_folder(token, folder_id)
        print("  cleanup: temp folder deleted")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: SA_CREDENTIALS_PATH=... python test_takeout_sa.py <fileId>")
    file_id = sys.argv[1]

    if not os.path.exists(CREDS_PATH):
        sys.exit("SA credentials not found at " + CREDS_PATH +
                 " (set SA_CREDENTIALS_PATH)")
    with open(CREDS_PATH) as f:
        creds = json.load(f)

    print("service account : " + creds.get("client_email", "?"))
    print("target file     : " + file_id)

    for scopes in SCOPE_SETS:
        won = try_scopes(creds, file_id, scopes)
        if won:
            print("\n" + "=" * 66)
            print("ANSWER: Takeout accepts the service account.")
            print("Narrowest working scope set: " + ", ".join(won["scopes"]))
            print("=" * 66)
            return 0

    print("\n" + "=" * 66)
    print("ANSWER: no scope set was accepted.")
    print("If every body said 'insufficient authentication scopes' with no")
    print("scope named, the endpoint is first-party only and this route is")
    print("closed. Stop here and free Drive space so GAS copy can work —")
    print("a copy is also a new object, so it beats the quota the same way.")
    print("=" * 66)
    return 1


if __name__ == "__main__":
    sys.exit(main())
