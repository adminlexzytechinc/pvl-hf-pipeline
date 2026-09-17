import os, sys, shutil, tempfile, subprocess as real_subprocess
import mega_download as md

fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got}\n        want: {want}")
        fails.append(label)

VALID_URL = "https://mega.nz/file/d6IXQCab#4ZAiyTb9kW09F-961OHJtOtt_2dbN4BYKq3kajqZ0fY"
FOLDER_URL = "https://mega.nz/folder/AbCdEfGh#XyZ12345-_abcdefghijklmnop"

work = tempfile.mkdtemp()
dest = os.path.join(work, "raw_download")

# --- binary missing ---
print("--- megatools not on PATH ---")
md.MEGA_BIN = None
ok, name = md.mega_download("mega_d6IXQCab", VALID_URL, dest)
check("fails cleanly", ok, False)
check("no filename returned", name, None)

# --- missing key fragment ---
print("\n--- SOURCE_URL missing #key ---")
md.MEGA_BIN = "/usr/bin/true"  # pretend it exists, shouldn't even be invoked
ok, name = md.mega_download("mega_d6IXQCab", "https://mega.nz/file/d6IXQCab", dest)
check("rejected before any subprocess call", ok, False)

# --- empty SOURCE_URL entirely ---
print("\n--- SOURCE_URL empty ---")
ok, name = md.mega_download("mega_d6IXQCab", "", dest)
check("rejected", ok, False)

# --- folder link ---
print("\n--- folder link rejected (matches Drive folder gap) ---")
ok, name = md.mega_download("mega_AbCdEfGh", FOLDER_URL, dest)
check("folder rejected", ok, False)

# --- mocked successful subprocess call ---
print("\n--- mocked successful download ---")
md.MEGA_BIN = "/usr/bin/true"

class FakeProc:
    returncode = 0
    stderr = ""

def fake_run(cmd, capture_output, text, timeout):
    # Simulate megatools writing the real (decrypted) filename into --path
    path_idx = cmd.index("--path") + 1
    out_dir = cmd[path_idx]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "Tecno_Pouvoir_3_LB7_MT6739_V213_190425.zip"), "wb") as f:
        f.write(b"\x00" * 1024)
    return FakeProc()

md.subprocess.run = fake_run
ok, name = md.mega_download("mega_d6IXQCab", VALID_URL, dest)
check("succeeds", ok, True)
check("real filename recovered", name, "Tecno_Pouvoir_3_LB7_MT6739_V213_190425.zip")
check("bytes landed at dest_path", os.path.exists(dest), True)
check("tmp dir cleaned up", os.path.exists(os.path.join(work, ".mega_dl_d6IXQCab")), False)

# --- mocked non-zero exit (e.g. bandwidth limit) ---
print("\n--- mocked megatools failure (bandwidth limit style) ---")
class FailProc:
    returncode = 1
    stderr = "err: bandwidth limit exceeded, wait or log in"

def fake_run_fail(cmd, capture_output, text, timeout):
    path_idx = cmd.index("--path") + 1
    os.makedirs(cmd[path_idx], exist_ok=True)
    return FailProc()

md.subprocess.run = fake_run_fail
ok, name = md.mega_download("mega_d6IXQCab", VALID_URL, dest + "2")
check("propagates failure", ok, False)
check("no filename on failure", name, None)

shutil.rmtree(work, ignore_errors=True)

print("\n" + ("All tests passed." if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
