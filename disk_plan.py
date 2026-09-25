"""
How much disk a build needs, and freeing more only when it does (2026-09-25).

The workflow used to delete ~22 GB of preinstalled toolchains on EVERY build.
It took 85-125 seconds -- longer than the whole KL4h build it preceded (83 s)
-- and a runner starts with 86 GB free, enough for nearly every firmware.
Now the worker works out what this file needs and clears only if that is
more than what is free.

What a build holds on disk at its peak, after the download is on disk:

  zip, copied directly     the new zip, about the size of the download
  anything unpacked        the unpacked files, plus one file of overlap while
                           the new zip is written (the download is deleted
                           once unpacked, and each unpacked file once it is
                           in the new zip -- see hf_build_worker.py)
  a single file (.pac)     one copy of it

A zip that the direct copy declines is unpacked instead, so a zip is planned
for whichever of the two is larger.
"""

import os
import shutil
import subprocess
import time

GB = 1024 ** 3

# Room for the Hugging Face upload's working files, logs and rounding.
MARGIN = 8 * GB

# When an archive cannot be listed, assume its contents unpack to this many
# times its size. Firmware images rarely compress better than this.
UNKNOWN_RATIO = 3

# Preinstalled on GitHub's Ubuntu runners and never used by a build. Removing
# them frees about 22 GB of the root disk (145 GB; 86 GB free -> 108 GB).
CLEANUP_PATHS = [
    "/usr/share/dotnet",
    "/usr/local/lib/android",
    "/opt/ghc",
    "/opt/hostedtoolcache/CodeQL",
    "/usr/local/share/boost",
    "/usr/local/share/powershell",
]


def gb(n):
    return "%.1f GB" % (n / GB)


def extra_needed(raw_size, entries, is_zip, is_archive):
    """Bytes a build needs beyond the download already on disk."""
    if not is_archive:
        return raw_size + MARGIN
    sizes = [int(e.get("size") or 0) for e in (entries or [])]
    if sizes:
        unpacked = sum(sizes) + max(sizes)
    else:
        unpacked = raw_size * UNKNOWN_RATIO
    need = max(unpacked, raw_size) if is_zip else unpacked
    return need + MARGIN


def free_bytes(path):
    return shutil.disk_usage(path).free


def free_up_space(log=print):
    """Delete the unused toolchains. Returns seconds taken."""
    t0 = time.time()
    for p in CLEANUP_PATHS:
        subprocess.run(["sudo", "rm", "-rf", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "docker", "image", "prune", "--all", "--force"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    secs = time.time() - t0
    log("  Disk: clean-up done in %.0fs" % secs)
    return secs


def ensure_space(path, need, stage, log=print, cleaner=free_up_space, free=free_bytes):
    """
    Make sure `need` bytes are free at `path`, clearing only if they are not.
    Returns '' when there is room, or a reason the build cannot continue.
    """
    have = free(path)
    if have >= need:
        log("  Disk (%s): need %s, have %s -- no clean-up needed" % (stage, gb(need), gb(have)))
        return ""
    log("  Disk (%s): need %s, have %s -- clearing space" % (stage, gb(need), gb(have)))
    cleaner(log)
    have = free(path)
    if have >= need:
        log("  Disk (%s): now %s free" % (stage, gb(have)))
        return ""
    return ("This firmware needs %s of working space (%s), but the build machine "
            "has only %s free even after clean-up." % (gb(need), stage, gb(have)))
