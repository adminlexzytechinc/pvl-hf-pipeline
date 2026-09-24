"""
Dedup client for the build worker.

Asks the drive whether a source file is already indexed, BEFORE spending any
time repacking or any Hugging Face quota uploading.

Two rules shape everything here.

Hash the SOURCE, not the output. The build repacks with ZIP_STORED and writes
branding files with fresh timestamps, so the same firmware fetched from Google
Drive and from MEGA produces different final bytes and different content
hashes. Hashing the output would miss every cross-mirror duplicate while
appearing to work -- which is worse than not deduping at all, because nobody
would go looking.

Fail closed on availability, open on correctness. If the dedup API is
unreachable the build proceeds. A duplicate file is a far cheaper failure than
a stalled pipeline. The unchecked upload is reported so it can be reconciled
later rather than vanishing.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

DEDUP_API_URL = os.environ.get("DEDUP_API_URL", "").strip().rstrip("/")
DEDUP_API_TOKEN = os.environ.get("DEDUP_API_TOKEN", "").strip()  # a pasted secret can carry a stray newline
TIMEOUT = 15

# 8 MB reads: large enough that hashing a 4.5 GB file is I/O bound rather than
# syscall bound, small enough not to matter for memory on a runner.
CHUNK = 8 * 1024 * 1024


def sha256_file(path: str) -> str:
    """Stream a file through SHA-256. Never loads it into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def enabled() -> bool:
    return bool(DEDUP_API_URL and DEDUP_API_TOKEN)


def _post(action: str, payload: dict) -> tuple[int, dict]:
    """POST to the dedup API. Returns (status, body); status 0 means unreachable."""
    url = f"{DEDUP_API_URL}/v1/dedup/{action}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Index-Token", DEDUP_API_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        print(f"  Dedup API unreachable ({e}) -- proceeding without a check")
        return 0, {}


def check(source_sha256: str, **fields) -> dict:
    """
    Ask whether this source is already indexed, and reserve it if not.

    Returns one of:
      {"duplicate": True,  "file_id": ...}              stop; it already exists
      {"duplicate": False, "reserve_token": ...}        proceed; we hold the hash
      {"duplicate": False, "wait": True, ...}           another build has it
      {"duplicate": False, "unchecked": True}           API down; proceed blind
    """
    if not enabled():
        return {"duplicate": False, "unchecked": True, "reason": "dedup api not configured"}

    payload = {"source_sha256": source_sha256}
    payload.update({k: v for k, v in fields.items() if v not in (None, "")})
    status, body = _post("check", payload)

    if status == 0:
        return {"duplicate": False, "unchecked": True, "reason": "dedup api unreachable"}
    if status == 409:
        # Someone else holds a live reservation on this exact source.
        return {"duplicate": False, "wait": True,
                "retry_after": body.get("retry_after", 60),
                "reason": body.get("reason", "reserved by another build")}
    if status != 200:
        # An unexpected error is an availability problem, not a correctness
        # signal. Proceed, and say so.
        return {"duplicate": False, "unchecked": True,
                "reason": f"dedup api returned {status}"}
    return body


def confirm(source_sha256: str, reserve_token: str, file_id: str,
            content_sha256: str = "") -> dict:
    """Release the reservation and record both hashes against the file."""
    if not enabled() or not reserve_token:
        return {}
    status, body = _post("confirm", {
        "source_sha256": source_sha256,
        "reserve_token": reserve_token,
        "file_id": file_id,
        "content_sha256": content_sha256 or None,
    })
    if status != 200:
        print(f"  Dedup confirm failed ({status}): {body.get('error', '')}")
    return body


def release(source_sha256: str, reserve_token: str) -> None:
    """
    Drop the reservation after a failed build.

    Without this a crashed build blocks its hash until the TTL expires, so an
    immediate retry is told to wait for no reason.
    """
    if not enabled() or not reserve_token:
        return
    _post("release", {"source_sha256": source_sha256, "reserve_token": reserve_token})


def record_unchecked(file_id: str, source_sha256: str = "", reason: str = "") -> None:
    """Flag that this build uploaded without a dedup check."""
    if not enabled() or not file_id:
        return
    _post("unchecked", {
        "file_id": file_id,
        "source_sha256": source_sha256 or None,
        "reason": reason or "dedup api unreachable",
    })
