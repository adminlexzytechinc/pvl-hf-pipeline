"""
Tests for mega_download.py.

Runs fully offline. The success/failure paths mock subprocess.Popen; the
deadlock regression test spawns a REAL child process, because the bug it
guards against is an OS-level pipe-buffer interaction that a mock cannot
reproduce.
"""
import os, sys, shutil, tempfile, textwrap, threading
import subprocess as real_subprocess
import mega_download as md

_ORIGINAL_POPEN = real_subprocess.Popen

fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got!r}\n        want: {want!r}")
        fails.append(label)

VALID_URL = "https://mega.nz/file/d6IXQCab#4ZAiyTb9kW09F-961OHJtOtt_2dbN4BYKq3kajqZ0fY"
FOLDER_URL = "https://mega.nz/folder/AbCdEfGh#XyZ12345-_abcdefghijklmnop"
REAL_NAME = "Tecno_Pouvoir_3_LB7_MT6739_V213_190425.zip"

work = tempfile.mkdtemp()
dest = os.path.join(work, "raw_download")

# ── guard clauses (no subprocess involved) ──
print("--- guard clauses ---")
md.MEGA_BIN = None
check("no megatools binary", md.mega_download("mega_x", VALID_URL, dest), (False, None))

md.MEGA_BIN = "/usr/bin/true"
check("SOURCE_URL missing #key", md.mega_download("mega_x", "https://mega.nz/file/d6IXQCab", dest), (False, None))
check("SOURCE_URL empty", md.mega_download("mega_x", "", dest), (False, None))
check("folder link refused", md.mega_download("mega_y", FOLDER_URL, dest), (False, None))

# ── mocked Popen: success ──
print("\n--- mocked success ---")

class FakePopen:
    """Stands in for megatools: emits progress on stderr, writes the file."""
    def __init__(self, cmd, stdout=None, stderr=None, stdin=None, text=None, bufsize=None):
        out_dir = cmd[cmd.index("--path") + 1]
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, REAL_NAME), "wb") as f:
            f.write(b"\x00" * 2048)
        self.returncode = 0
        self._stream = iter([
            "Downloading 10% 1.2 MiB/s\r",
            "Downloading 50% 1.4 MiB/s\r",
            "Downloading 100% 1.3 MiB/s\r",
            "",
        ])
        self.stderr = self
    def read(self, n=-1):
        return next(self._stream, "")
    def wait(self, timeout=None):
        return 0
    def poll(self):
        return 0
    def kill(self):
        pass

md.subprocess.Popen = FakePopen
reported = []
ok, name = md.mega_download("mega_d6IXQCab", VALID_URL, dest,
                            progress_callback=lambda p, l: reported.append(p))
check("succeeds", ok, True)
check("real filename recovered", name, REAL_NAME)
check("bytes landed at dest_path", os.path.exists(dest), True)
check("tmp dir cleaned up", os.path.exists(os.path.join(work, ".mega_dl_d6IXQCab")), False)
check("progress forwarded to callback", len(reported) > 0, True)

# ── mocked Popen: non-zero exit ──
print("\n--- mocked failure (bandwidth limit) ---")

class FailPopen(FakePopen):
    def __init__(self, cmd, **kw):
        out_dir = cmd[cmd.index("--path") + 1]
        os.makedirs(out_dir, exist_ok=True)   # no file written
        self.returncode = 1
        self._stream = iter(["ERROR: Bandwidth limit exceeded\n", ""])
        self.stderr = self

md.subprocess.Popen = FailPopen
check("propagates failure", md.mega_download("mega_d6IXQCab", VALID_URL, dest + "2"), (False, None))

# ── credentials only passed when both env vars present ──
print("\n--- credential handling ---")
captured = {}
class CapturePopen(FakePopen):
    def __init__(self, cmd, **kw):
        captured['cmd'] = list(cmd)
        captured['stdout'] = kw.get('stdout')
        super().__init__(cmd, **kw)

md.subprocess.Popen = CapturePopen
os.environ.pop("MEGA_USER", None); os.environ.pop("MEGA_PASS", None)
md.mega_download("mega_d6IXQCab", VALID_URL, dest + "3")
check("no creds -> no --username flag", "--username" in captured['cmd'], False)

os.environ["MEGA_USER"] = "u@example.com"; os.environ["MEGA_PASS"] = "pw"
md.mega_download("mega_d6IXQCab", VALID_URL, dest + "4")
check("creds present -> --username passed", "--username" in captured['cmd'], True)
check("password read from env, never hardcoded",
      captured['cmd'][captured['cmd'].index("--password") + 1], "pw")
os.environ.pop("MEGA_USER"); os.environ.pop("MEGA_PASS")

# ── THE REGRESSION TEST: stdout must not be an undrained pipe ──
print("\n--- deadlock regression (real subprocess) ---")
check("stdout is DEVNULL, not PIPE", captured['stdout'], real_subprocess.DEVNULL)

# md.subprocess IS the real subprocess module, so the monkeypatches above
# mutated it globally. Restore before spawning real children.
real_subprocess.Popen = _ORIGINAL_POPEN

CHILD = textwrap.dedent("""
    import sys
    for _ in range(4000):
        sys.stdout.write("x" * 100 + "\\n")
    sys.stdout.flush()
    for p in range(0, 101, 20):
        sys.stderr.write(f"{p}%\\r"); sys.stderr.flush()
""")

def drain_stderr_only(stdout_mode, timeout=5):
    """Mimics mega_download's read loop against a stdout-chatty child."""
    proc = real_subprocess.Popen(
        [sys.executable, "-c", CHILD], stdout=stdout_mode,
        stderr=real_subprocess.PIPE, stdin=real_subprocess.DEVNULL,
        text=True, bufsize=1)
    done = threading.Event()
    def reader():
        try:
            while proc.stderr.read(4096):
                pass
        except Exception:
            pass
        done.set()
    threading.Thread(target=reader, daemon=True).start()
    finished = done.wait(timeout)
    if proc.poll() is None:
        proc.kill()
    return finished

check("PIPE would deadlock (proving the bug was real)",
      drain_stderr_only(real_subprocess.PIPE), False)
check("DEVNULL completes (proving the fix works)",
      drain_stderr_only(real_subprocess.DEVNULL), True)

shutil.rmtree(work, ignore_errors=True)
print("\n" + ("All tests passed." if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
