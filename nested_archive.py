"""
One archive, not an archive inside an archive (added 2026-10-04).

Some uploads are a .zip whose real content is ONE more archive:

    Tecno Pova 7 5G LJ7.zip
        Tecno Pova 7 5G LJ7.rar      <- the firmware, again packed

A visitor then downloads our zip, opens it, and finds another archive to
unpack -- two steps, and a .rar needs WinRAR. The build now unpacks that inner
archive and stores its contents, so one unzip gives the firmware.

What is NOT unpacked, because the file itself is what gets flashed:
  * .pac (Unisoc), .tar.md5 / .md5 (Samsung Odin), .ofp / .ops / .kdz / .img;
  * an OTA update.zip -- recovery flashes the zip as it is. Recognised by
    META-INF/com/google/android/update-binary, META-INF/com/android/metadata
    or payload.bin inside it.
And only when that one archive is nearly all of the package (80%+ of the
bytes left after exclusions): a firmware folder holding a small tools.rar
beside it is left alone.
"""

import os
import shutil
import subprocess
import zipfile

CONTAINER_EXTS = ('.zip', '.rar', '.7z', '.tar', '.tgz', '.tar.gz')
FLASHABLE_EXTS = ('.tar.md5', '.md5', '.pac', '.ofp', '.ops', '.kdz', '.img', '.bin')
OTA_MARKERS = ('meta-inf/com/google/android/update-binary', 'meta-inf/com/android/metadata', 'payload.bin')
SHARE = 0.80
MAX_DEPTH = 2


def _is_container(name):
    n = name.lower()
    return n.endswith(CONTAINER_EXTS) and not n.endswith(FLASHABLE_EXTS)


def is_ota_names(names):
    low = [n.lower().replace('\\', '/') for n in names]
    return any(n.endswith(m) for n in low for m in OTA_MARKERS)


def dominant_inner(files, excluded=lambda n: False):
    """files: [(name, size)]. The name of the one archive that is the package,
    or ''. Excluded files (drivers, tools by the owner's patterns) do not count."""
    kept = [(n, s) for n, s in files if not excluded(n)]
    total = sum(s for _n, s in kept) or 0
    boxes = [(n, s) for n, s in kept if _is_container(n)]
    if total <= 0 or len(boxes) != 1:
        return ''
    name, size = boxes[0]
    return name if size >= SHARE * total else ''


def zip_has_nested(zip_path, excluded=lambda n: False):
    """For the direct-copy route: does this zip hold one archive that should be
    unpacked? Reads the index only. An inner .zip that cannot be checked for
    being an OTA is left alone -- unpacking an OTA would break it."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            inner = dominant_inner([(i.filename, i.file_size) for i in infos], excluded)
            if not inner:
                return ''
            if inner.lower().endswith('.zip'):
                try:
                    with zf.open(inner) as fh, zipfile.ZipFile(fh) as iz:
                        if is_ota_names(iz.namelist()):
                            return ''
                except Exception:
                    return ''
            return inner
    except Exception:
        return ''


def _staging_files(staging_dir):
    out = []
    for root, _d, names in os.walk(staging_dir):
        for n in names:
            p = os.path.join(root, n)
            try:
                out.append((os.path.relpath(p, staging_dir).replace('\\', '/'), os.path.getsize(p)))
            except OSError:
                pass
    return out


def unwrap_in_staging(staging_dir, excluded=lambda n: False, log=print, run=subprocess.run):
    """Unpack the one inner archive in an extracted package, in place, up to
    MAX_DEPTH times. Returns the names unpacked. Never fails a build: an inner
    archive that will not open (password, damage) is kept as it is."""
    done = []
    for _ in range(MAX_DEPTH):
        inner = dominant_inner(_staging_files(staging_dir), excluded)
        if not inner:
            break
        path = os.path.join(staging_dir, inner)
        if inner.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(path) as iz:
                    if is_ota_names(iz.namelist()):
                        log('  Inner archive is an OTA update package; kept as it is: ' + inner)
                        break
            except Exception:
                pass
        base = os.path.basename(inner)
        stem = base
        for ext in ('.tar.gz',) + CONTAINER_EXTS:
            if stem.lower().endswith(ext):
                stem = stem[:-len(ext)]
                break
        out_dir = os.path.join(os.path.dirname(path), stem or 'firmware')
        if os.path.exists(out_dir):
            out_dir += '_unpacked'
        os.makedirs(out_dir)
        res = run(['7z', 'x', '-y', '-p', '-o' + out_dir, path],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0 or not os.listdir(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
            log('  Inner archive could not be unpacked (password or damage); kept as it is: ' + inner)
            break
        os.unlink(path)
        log('  Unpacked the archive inside the archive: ' + inner)
        done.append(inner)
        # .tar.gz unpacks to a .tar first: the loop takes it next time round.
    return done
