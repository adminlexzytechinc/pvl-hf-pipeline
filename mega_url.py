"""
MEGA URL parsing.

Deliberately does NOT touch MEGA's AES-CTR decryption or MAC verification.
That crypto is finicky — a wrong byte in the key-unpacking step produces a
file that *looks* complete (right size) but is silently corrupt, which is
exactly the kind of bug verify_archive_integrity() won't catch if the
corruption is inside a compressed stream. Rather than re-implement it here,
this hands the full URL to `megatools` (github.com/megous/megatools), a
mature, widely packaged open-source client that's been doing this specific
job for over a decade. Our own code only ever needs to know the file's
*handle*, for building a short, stable, dedup-friendly file_id -- never
the key.

Both link generations are handled:
  New: https://mega.nz/file/<handle>#<key>
       https://mega.nz/folder/<handle>#<key>[/folder/<sub>#<subkey>]/file/<fh>
  Old: https://mega.nz/#!<handle>!<key>
       https://mega.nz/#F!<handle>!<key>
"""

import re

MEGA_HOST_RE = re.compile(r'mega\.(nz|io|co\.nz)', re.IGNORECASE)

# (pattern, kind) -- checked in order, first match wins.
_PATTERNS = [
    (re.compile(r'mega\.\w+(?:\.\w+)?/folder/([\w-]+)#([\w-]+)', re.IGNORECASE), 'folder'),
    (re.compile(r'mega\.\w+(?:\.\w+)?/file/([\w-]+)#([\w-]+)', re.IGNORECASE), 'file'),
    (re.compile(r'mega\.\w+(?:\.\w+)?/#F!([\w-]+)!([\w-]+)', re.IGNORECASE), 'folder'),
    (re.compile(r'mega\.\w+(?:\.\w+)?/#!([\w-]+)!([\w-]+)', re.IGNORECASE), 'file'),
]


def is_mega_url(url: str) -> bool:
    return bool(url) and bool(MEGA_HOST_RE.search(url))


def parse_mega_url(url: str):
    """
    Return {'kind': 'file'|'folder', 'handle': str, 'key': str} or None.

    'key' is returned only because callers may want to sanity-check that one
    is present (a link missing its key fragment can't be decrypted by
    anyone, us included, and should be rejected before it reaches the
    download cascade) -- it should not otherwise be inspected or stored
    separately from the URL it came from.
    """
    if not url:
        return None
    for pattern, kind in _PATTERNS:
        m = pattern.search(url)
        if m:
            handle, key = m.group(1), m.group(2)
            if not handle or not key:
                return None
            return {'kind': kind, 'handle': handle, 'key': key}
    return None


def mega_file_id(url: str):
    """
    'mega_' + public handle, mirroring the existing 'mf_' MediaFire
    convention. Deliberately excludes the key: file_id is a cache/dedup
    identity, and two links to the same node always share the same handle
    even if regenerated, so this still dedups correctly. The key travels
    separately in source_url, where the download step reads it from.
    """
    parsed = parse_mega_url(url)
    if not parsed:
        return None
    return 'mega_' + parsed['handle']
