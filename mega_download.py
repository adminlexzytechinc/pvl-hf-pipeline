"""
MEGA download step for hf_build_worker.py.

Delegates the actual fetch+decrypt to `megatools dl` (github.com/megous/megatools,
GPLv2, packaged as `megatools` on Debian/Ubuntu). We never touch the AES-CTR
key material ourselves -- see mega_url.py's module docstring for why.

Drop-in shape matches mediafire_download(file_id, dest_path) -> bool so it
slots into download_from_source() the same way.
"""

import os
import re
import shutil
import subprocess

from mega_url import is_mega_url, parse_mega_url

MEGA_BIN = shutil.which("megatools")


def mega_download(file_id: str, source_url: str, dest_path: str):
    """
    Download a MEGA file link to dest_path.

    Unlike the Drive/MediaFire paths, file_id alone is NOT enough here --
    it deliberately excludes the decryption key (see mega_file_id() in
    mega_url.py), so source_url is required and must still carry the
    original #key fragment. Called from download_from_source() with the
    SOURCE_URL the workflow was dispatched with; if that's missing this
    fails loudly rather than silently downloading nothing.

    Returns (success: bool, real_filename: str | None). The caller -- not
    this module -- owns hf_build_worker.py's FILE_NAME global, so the real
    name megatools recovers from MEGA's decrypted attributes is handed back
    rather than set here; a cross-module `global FILE_NAME` inside this
    function would silently rebind a variable in *this* module, not
    hf_build_worker's, which is an easy mistake to ship unnoticed since
    nothing raises when it happens.
    """
    if MEGA_BIN is None:
        print("  MEGA download error: 'megatools' binary not found on PATH. "
              "Install it in the workflow (e.g. `apt-get install -y megatools` "
              "on Ubuntu runners) before this step runs.")
        return False, None

    parsed = parse_mega_url(source_url)
    if not parsed:
        print(f"  MEGA download error: SOURCE_URL is missing or has no #key "
              f"fragment ('{source_url}'). The key never reaches our server "
              f"any other way -- without it in SOURCE_URL this file cannot "
              f"be decrypted.")
        return False, None

    if parsed['kind'] == 'folder':
        # Folder links need the same "which file inside?" resolution as the
        # Drive folder case -- out of scope here, matching that decision.
        print("  MEGA folder links are not yet supported by this pipeline "
              "(same gap as Drive folder links). Pass a direct /file/ link.")
        return False, None

    work_dir = os.path.dirname(dest_path) or "."
    tmp_dir = os.path.join(work_dir, f".mega_dl_{parsed['handle']}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # --path is a directory: megatools names the file itself from the
        # link's own (decrypted) metadata, so we don't have to guess it.
        cmd = [MEGA_BIN, "dl", "--path", tmp_dir, "--no-progress"]
        mega_user = os.environ.get("MEGA_USER")
        mega_pass = os.environ.get("MEGA_PASS")
        if mega_user and mega_pass:
            cmd.extend(["--username", mega_user, "--password", mega_pass])
        cmd.append(source_url)

        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=3600 * 6,
        )
    except subprocess.TimeoutExpired:
        print("  MEGA download error: timed out after 6 hours")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, None

    if proc.returncode != 0:
        # megatools' own stderr already says the useful thing (bandwidth
        # limit exceeded, link expired/removed, etc.) -- surface it verbatim
        # rather than paraphrasing, since the exact wording is what you'd
        # grep the retry-queue error column for.
        print(f"  MEGA download failed (exit {proc.returncode}): {proc.stderr.strip()}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, None

    downloaded = os.listdir(tmp_dir)
    if len(downloaded) != 1:
        print(f"  MEGA download error: expected exactly 1 file in output dir, "
              f"found {len(downloaded)}: {downloaded}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, None

    real_name = downloaded[0]
    shutil.move(os.path.join(tmp_dir, real_name), dest_path)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"  MEGA download complete: {real_name} ({os.path.getsize(dest_path) // (1024*1024)} MB)")
    return True, real_name
