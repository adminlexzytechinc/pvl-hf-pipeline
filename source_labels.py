"""
One place that decides what a download source is CALLED.

Replaces the hardcoded brand names scattered through hf_build_worker.py
("Connecting to MEGA source...", "Downloading from MediaFire (...)"), which
caused two separate problems:

 1. WRONG BRAND. Each branch spelled its own label by hand, so a Google
    Drive build could surface a MEGA string whenever a branch was
    copy-pasted or a fallthrough hit the wrong arm. Deriving the label from
    SOURCE_TYPE makes that class of bug structurally impossible.

 2. INTERNAL JARGON LEAKING TO USERS. "Trying GAS copy...", "Trying GAS
    folder download...", "Trying Web download URL...", "Trying API
    download..." are names for our own quota-workaround strategies. To
    someone waiting on a firmware file they are meaningless, and they
    advertise how the workaround is built. Users should see one steady
    "Fetching from Google Drive..." no matter which internal strategy is
    mid-flight; the strategy name still goes to the build log via print(),
    which is where it is useful.
"""

SOURCE_LABELS = {
    "gdrive":    "Google Drive",
    "mediafire": "MediaFire",
    "mega":      "MEGA",
}

DEFAULT_LABEL = "the source"


def source_label(source_type: str = "", file_id: str = "", source_url: str = "") -> str:
    """
    Display name for whatever we are currently downloading from.

    Resolves through the same signals download_from_source() branches on, in
    the same order, so the label can never disagree with the code path that
    actually runs: explicit SOURCE_TYPE, then file_id prefix, then URL host.

    Returns a generic phrase rather than guessing when nothing matches --
    "Fetching from the source..." is always better than confidently naming
    the wrong company.
    """
    st = (source_type or "").strip().lower()
    if st in SOURCE_LABELS:
        return SOURCE_LABELS[st]

    fid = file_id or ""
    if fid.startswith("mf_"):
        return SOURCE_LABELS["mediafire"]
    if fid.startswith("mega_"):
        return SOURCE_LABELS["mega"]

    url = (source_url or "").lower()
    if "mediafire.com" in url:
        return SOURCE_LABELS["mediafire"]
    if any(h in url for h in ("mega.nz", "mega.io", "mega.co.nz")):
        return SOURCE_LABELS["mega"]
    if any(h in url for h in ("drive.google.com", "docs.google.com",
                              "drive.usercontent.google.com")):
        return SOURCE_LABELS["gdrive"]

    return DEFAULT_LABEL
