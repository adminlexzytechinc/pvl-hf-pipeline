"""
MEGA download step for hf_build_worker.py.

Delegates the actual fetch+decrypt to `megatools dl` (github.com/megous/megatools,
GPLv2, packaged as `megatools` on Debian/Ubuntu). We never touch the AES-CTR
key material ourselves -- see mega_url.py's module docstring for why.

Drop-in shape matches mediafire_download(file_id, dest_path) -> bool so it
slots into download_from_source() the same way.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import time

from mega_url import is_mega_url, parse_mega_url

MEGA_BIN = shutil.which("megatools")


def mega_download(file_id: str, source_url: str, dest_path: str, progress_callback=None):
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
        # We do NOT pass --no-progress: megatools streams progress to stderr,
        # which we display live in the GitHub Actions runner log and forward
        # to the WordPress callback so the build does not appear stuck.
        cmd = [MEGA_BIN, "dl", "--path", tmp_dir]
        mega_user = os.environ.get("MEGA_USER")
        mega_pass = os.environ.get("MEGA_PASS")
        if mega_user and mega_pass:
            cmd.extend(["--username", mega_user, "--password", mega_pass])
        cmd.append(source_url)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        last_report_time = 0
        last_pct = -1
        err_lines = []
        buf = []

        while True:
            char = proc.stderr.read(1)
            if not char:
                break
            if char in ('\r', '\n'):
                line = ''.join(buf).strip()
                buf = []
                if line:
                    err_lines.append(line)
                    if len(err_lines) > 50:
                        err_lines.pop(0)
                    # Stream live progress to console for GitHub Actions log
                    sys.stdout.write(f"\r  {line}   ")
                    sys.stdout.flush()

                    m = re.search(r'(\d{1,3})%', line)
                    if m and progress_callback:
                        pct = int(m.group(1))
                        now = time.time()
                        if pct != last_pct and (now - last_report_time >= 5 or pct in (25, 50, 75, 100)):
                            last_pct = pct
                            last_report_time = now
                            try:
                                progress_callback(pct, line)
                            except Exception:
                                pass
            else:
                buf.append(char)

        proc.wait(timeout=3600 * 6)
        print()  # Ensure newline after progress carriage returns
    except subprocess.TimeoutExpired:
        print("  MEGA download error: timed out after 6 hours")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, None

    if proc.returncode != 0:
        err_msg = "\n".join(err_lines[-5:]) if err_lines else f"exit {proc.returncode}"
        print(f"  MEGA download failed (exit {proc.returncode}): {err_msg}")
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
