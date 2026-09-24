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

# Transfer ceilings (same values as before; module-level so a test can shorten
# them). MAX_IDLE is the one that matters: see the watchdog in mega_download().
MAX_RUNTIME_SECONDS = 3600 * 5
MAX_IDLE_SECONDS = 600

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

    # Hard ceilings for the transfer. MAX_RUNTIME caps the whole download;
    # MAX_IDLE kills a transfer that has gone silent (dead socket, throttled
    # to zero) instead of letting it hold the runner until GitHub's own limit.
    MAX_RUNTIME = MAX_RUNTIME_SECONDS
    MAX_IDLE = MAX_IDLE_SECONDS

    proc = None
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

        # stdout MUST be DEVNULL, not PIPE.
        #
        # Progress arrives on stderr, and the loop below only drains stderr.
        # With stdout=PIPE nothing ever reads that pipe: once megatools writes
        # ~64KB (one pipe buffer) to stdout it blocks in write(), therefore
        # stops writing stderr, and this process blocks forever waiting on
        # stderr that will never arrive. A proc.wait(timeout=...) placed after
        # the loop cannot save us -- the loop never exits, so the wait never
        # runs. Reproduced: stdout=PIPE hangs indefinitely with 0 lines read;
        # stdout=DEVNULL completes and captures every progress line.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        started = time.time()
        last_output_time = started
        last_report_time = 0.0
        last_pct = -1
        err_lines = []
        buf = []
        timed_out_reason = None

        # FIXED 2026-09-24: MAX_IDLE was defined above and never enforced.
        # The loop below blocks inside proc.stderr.read(); when megatools goes
        # silent (dead socket, MEGA making it wait) that read never returns,
        # so neither limit is ever checked and the build sits until GitHub
        # kills the whole job -- run 35868500541 did exactly that, for the
        # full 60 minutes, every 6 hours. A watchdog checks from outside the
        # blocking read and stops megatools, which closes stderr and lets the
        # loop end normally.
        watch = {"last": started, "reason": None}

        def _watchdog():
            import threading  # noqa: F401  (documented: runs on its own thread)
            while proc.poll() is None:
                time.sleep(5)
                now_w = time.time()
                if now_w - watch["last"] > MAX_IDLE:
                    watch["reason"] = f"no progress for {MAX_IDLE // 60} minutes (stalled)"
                elif now_w - started > MAX_RUNTIME:
                    watch["reason"] = f"exceeded {MAX_RUNTIME // 3600}h runtime limit"
                if watch["reason"]:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return

        import threading
        threading.Thread(target=_watchdog, daemon=True).start()

        # Read in chunks rather than a character at a time: an 8GB transfer
        # emits a great many progress updates and read(1) per character is
        # one syscall each for no benefit. Progress lines are \r-terminated,
        # so we can't iterate lines directly.
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break

            now = time.time()
            last_output_time = now
            watch["last"] = now
            if now - started > MAX_RUNTIME:
                timed_out_reason = f"exceeded {MAX_RUNTIME // 3600}h runtime limit"
                break

            for char in chunk:
                if char in ('\r', '\n'):
                    line = ''.join(buf).strip()
                    buf = []
                    if not line:
                        continue
                    err_lines.append(line)
                    if len(err_lines) > 50:
                        err_lines.pop(0)
                    # Stream live progress to console for GitHub Actions log
                    sys.stdout.write(f"\r  {line}   ")
                    sys.stdout.flush()

                    m = re.search(r'(\d{1,3})%', line)
                    if m and progress_callback:
                        pct = int(m.group(1))
                        if pct != last_pct and (now - last_report_time >= 5 or pct in (25, 50, 75, 100)):
                            last_pct = pct
                            last_report_time = now
                            try:
                                progress_callback(pct, line)
                            except Exception:
                                pass
                else:
                    buf.append(char)

        # The watchdog stopped megatools: report it as the timeout it is.
        if not timed_out_reason and watch["reason"]:
            timed_out_reason = watch["reason"]

        if timed_out_reason:
            proc.kill()
            proc.wait(timeout=30)
            print(f"\n  MEGA download error: {timed_out_reason}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False, None

        proc.wait(timeout=300)
        print()  # Ensure newline after progress carriage returns
    except subprocess.TimeoutExpired:
        # megatools closed stderr but did not exit within the grace period.
        print("\n  MEGA download error: process did not exit after stream closed")
        if proc is not None:
            proc.kill()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, None
    except Exception as e:
        # Never leak a half-written temp dir on an unexpected failure.
        print(f"\n  MEGA download error: {e}")
        if proc is not None and proc.poll() is None:
            proc.kill()
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
