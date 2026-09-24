"""
Repack a ZIP without decompressing it.

WHY
---
The build used to extract every archive and write the files back out with
ZIP_STORED. Firmware images compress well, so the stored copy came out 25-27%
BIGGER than the download on every Itel/Unisoc package tested (Itel P40: 4.19 GB
in, 5.30 GB out). Every visitor then downloaded the extra gigabyte, and the
runner needed disk for the archive, the extracted tree and the output at once.

Recompressing fixes the size but not the time: deflate on the runner's four
cores took 162 s for that one file, to save about 20 s of upload.

The compressed bytes are already correct -- `verify_archive_integrity()` tested
every CRC before we get here -- so they can be copied across untouched. Only
the entry NAMES change. Same Itel P40 file: 4 s, and the output is the size of
the download.

WHAT IT MUST NOT CHANGE
-----------------------
The files inside, and where they sit. `plan_layout()` reproduces, over the
central directory, exactly what process_archive() does on disk:

    flatten_staging_dir()                 hoist a single top-level folder, repeatedly
    filter_and_clean_staging()            drop excluded files and folders
    restructure_staging_for_packaging()   everything under firmware/
    repack_staging_to_zip(root_folder)    everything under the root folder

Whenever the listing holds something that rule set was never tested on --
encryption, an unusual compression method, a name that would collide or
escape the folder -- this module declines and the caller uses the old path.
Declining costs time; guessing could cost a file.
"""

import fnmatch
import os
import posixpath
import struct
import zipfile
import zlib

# Methods every unzip tool a visitor might use can open. Anything else
# (deflate64, bzip2, lzma, zstd) stays on the extract-and-store path, which
# turns it into STORED as it always has.
PASSTHROUGH_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)

COPY_CHUNK = 16 * 1024 * 1024
U32 = 0xFFFFFFFF
U16 = 0xFFFF


class Decline(Exception):
    """The listing is outside what the passthrough is known to handle."""


def _excluded(patterns, rel_path, name):
    """Same test as should_exclude(), applied to both forms the walk used."""
    for p in patterns:
        for candidate in (rel_path, name):
            if fnmatch.fnmatch(posixpath.basename(candidate), p) or fnmatch.fnmatch(candidate, p):
                return True
    return False


def _clean_path(name):
    path = name.replace("\\", "/")
    parts = [s for s in path.split("/") if s not in ("", ".")]
    if not parts or ".." in parts or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise Decline("unsafe entry name %r" % name)
    return parts


def plan_layout(infos, exclude_patterns, root_folder):
    """
    infos: ZipInfo list from the source archive.
    Returns (plan, stats): plan is [(ZipInfo, output_name)], in archive order.
    Raises Decline for anything the on-disk path might treat differently.
    """
    files = []          # (info, parts)
    dirs = set()        # tuple(parts) of every folder, explicit or implied
    for info in infos:
        parts = _clean_path(info.filename)
        if info.is_dir():
            dirs.add(tuple(parts))
            continue
        if info.flag_bits & 0x1:
            raise Decline("encrypted entry %s" % info.filename)
        if info.compress_type not in PASSTHROUGH_METHODS:
            raise Decline("compression method %d" % info.compress_type)
        files.append((info, parts))
        for i in range(1, len(parts)):
            dirs.add(tuple(parts[:i]))

    seen = set()
    for _, parts in files:
        key = "/".join(parts)
        if key in seen or tuple(parts) in dirs:
            raise Decline("two entries share the path %s" % key)
        seen.add(key)

    # 1. flatten_staging_dir: while the root holds exactly one item and it is a folder.
    while True:
        top = {p[0] for _, p in files} | {d[0] for d in dirs}
        if len(top) != 1:
            break
        only = next(iter(top))
        if (only,) not in dirs:
            break                                   # the single item is a file
        files = [(i, p[1:]) for i, p in files]
        dirs = {d[1:] for d in dirs if len(d) > 1}

    # 2. filter_and_clean_staging: a matching folder takes its whole subtree.
    dead_dirs = {d for d in dirs if _excluded(exclude_patterns, "/".join(d), d[-1])}
    stats = {"kept": 0, "excluded": 0, "total_size": 0}
    kept = []
    for info, parts in files:
        if any(tuple(parts[:i]) in dead_dirs for i in range(1, len(parts))):
            continue
        if _excluded(exclude_patterns, "/".join(parts), parts[-1]):
            stats["excluded"] += 1
            continue
        kept.append((info, parts))
        stats["kept"] += 1
        stats["total_size"] += info.file_size
    stats["excluded"] += len({d for d in dead_dirs
                              if not any(d[:i] in dead_dirs for i in range(1, len(d)))})
    dirs = {d for d in dirs if not any(d[:i] in dead_dirs for i in range(1, len(d) + 1))}

    # 3. restructure_staging_for_packaging: reuse a top-level firmware folder
    # (any case) or create one, and move everything else into it.
    top_dirs = sorted(d[0] for d in dirs if len(d) == 1)
    firmware_like = [d for d in top_dirs if d.lower() == "firmware"]
    if len(firmware_like) > 1:
        raise Decline("more than one firmware folder at the top")
    fw = firmware_like[0] if firmware_like else None

    if fw is None and any(len(p) == 1 and p[0].lower() == "firmware" for _, p in kept):
        raise Decline("a top-level FILE is named firmware")
    # Moving a root item into an existing firmware/ that already holds one of
    # the same name would merge or overwrite on disk.
    inside = ({p[1] for _, p in kept if p[0] == fw and len(p) > 1}
              | {d[1] for d in dirs if d[0] == fw and len(d) > 1}) if fw is not None else set()

    plan = []
    out_seen = set()
    for info, parts in kept:
        if fw is not None and parts[0] == fw:
            rel = ["firmware"] + parts[1:]
        else:
            if parts[0] in inside:
                raise Decline("%s exists both at the top and inside firmware/" % parts[0])
            rel = ["firmware"] + parts
        name = "/".join(([root_folder] if root_folder else []) + rel)
        if name in out_seen:
            raise Decline("output collision at %s" % name)
        out_seen.add(name)
        plan.append((info, name))
    return plan, stats


# --------------------------------------------------------------------------
# Raw writer
# --------------------------------------------------------------------------

def _dos(date_time):
    y, mo, d, h, mi, s = date_time
    y = max(1980, y)
    return ((h << 11) | (mi << 5) | (s // 2)), (((y - 1980) << 9) | (mo << 5) | d)


def _data_offset(src, info):
    """Where an entry's compressed bytes start: after ITS OWN local header,
    whose name and extra lengths can differ from the central directory's."""
    src.seek(info.header_offset)
    head = src.read(30)
    if len(head) != 30 or head[:4] != b"PK\x03\x04":
        raise Decline("bad local header for %s" % info.filename)
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    return info.header_offset + 30 + name_len + extra_len


class _RawZipWriter:
    def __init__(self, fh):
        self.fh = fh
        self.central = []

    def _local(self, name_b, flags, method, dt, crc, csize, usize):
        zip64 = csize >= U32 or usize >= U32
        extra = struct.pack("<HHQQ", 1, 16, usize, csize) if zip64 else b""
        t, d = _dos(dt)
        offset = self.fh.tell()
        self.fh.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 45 if zip64 else 20, flags, method,
                                  t, d, crc, U32 if zip64 else csize, U32 if zip64 else usize,
                                  len(name_b), len(extra)))
        self.fh.write(name_b)
        self.fh.write(extra)
        return offset

    def _record(self, name_b, flags, method, dt, crc, csize, usize, offset, ext_attr):
        self.central.append((name_b, flags, method, dt, crc, csize, usize, offset, ext_attr))

    @staticmethod
    def _name(name):
        try:
            return name.encode("ascii"), 0
        except UnicodeEncodeError:
            return name.encode("utf-8"), 0x800

    def copy(self, src, info, name):
        name_b, utf8 = self._name(name)
        # Bit 3 (sizes in a trailing descriptor) is cleared: this header
        # carries the real sizes and the descriptor is not copied.
        flags = (info.flag_bits & ~0x0808) | utf8
        start = _data_offset(src, info)
        offset = self._local(name_b, flags, info.compress_type, info.date_time,
                             info.CRC, info.compress_size, info.file_size)
        src.seek(start)
        left = info.compress_size
        while left:
            chunk = src.read(min(COPY_CHUNK, left))
            if not chunk:
                raise Decline("source ended inside %s" % info.filename)
            self.fh.write(chunk)
            left -= len(chunk)
        # The old path wrote each file from disk, so every entry carried a Unix
        # mode. A Windows-made source carries DOS attributes instead, which a
        # Linux unzip would read as mode 000.
        attr = info.external_attr if (info.create_system == 3 and info.external_attr >> 16)             else (0o100644 << 16)
        self._record(name_b, flags, info.compress_type, info.date_time, info.CRC,
                     info.compress_size, info.file_size, offset, attr)

    def add_stored(self, name, data, date_time):
        name_b, utf8 = self._name(name)
        crc = zlib.crc32(data) & U32
        offset = self._local(name_b, utf8, zipfile.ZIP_STORED, date_time, crc, len(data), len(data))
        self.fh.write(data)
        self._record(name_b, utf8, zipfile.ZIP_STORED, date_time, crc, len(data), len(data),
                     offset, 0o100644 << 16)

    def close(self):
        cd_start = self.fh.tell()
        for name_b, flags, method, dt, crc, csize, usize, offset, ext_attr in self.central:
            vals = []
            if usize >= U32:
                vals.append(usize)
            if csize >= U32:
                vals.append(csize)
            if offset >= U32:
                vals.append(offset)
            extra = struct.pack("<HH", 1, 8 * len(vals)) + b"".join(struct.pack("<Q", v) for v in vals) if vals else b""
            t, d = _dos(dt)
            self.fh.write(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, (3 << 8) | 45,
                                      45 if vals else 20, flags, method, t, d, crc,
                                      U32 if csize >= U32 else csize, U32 if usize >= U32 else usize,
                                      len(name_b), len(extra), 0, 0, 0, ext_attr & U32,
                                      U32 if offset >= U32 else offset))
            self.fh.write(name_b)
            self.fh.write(extra)
        cd_end = self.fh.tell()
        count, cd_size = len(self.central), cd_end - cd_start
        if count >= U16 or cd_size >= U32 or cd_start >= U32:
            self.fh.write(struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0,
                                      count, count, cd_size, cd_start))
            self.fh.write(struct.pack("<IIQI", 0x07064B50, 0, cd_end, 1))
        self.fh.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, min(count, U16), min(count, U16),
                                  min(cd_size, U32), min(cd_start, U32), 0))


def passthrough_repack(source_zip, output_zip, exclude_patterns, root_folder="", extra_files=None):
    """
    Build output_zip from source_zip's compressed bytes. extra_files is an
    optional {name_under_root: bytes} for the branding files (stored).
    Returns the same stats dict process_archive() returns. Raises Decline, and
    removes any partial output, when the passthrough does not apply.
    """
    try:
        with zipfile.ZipFile(source_zip) as zf, open(source_zip, "rb") as src, \
                open(output_zip, "wb") as out:
            plan, stats = plan_layout(zf.infolist(), exclude_patterns, root_folder)
            if not plan:
                raise Decline("nothing left after exclusions")
            writer = _RawZipWriter(out)
            for info, name in plan:
                writer.copy(src, info, name)
            if extra_files:
                import time
                now = time.localtime()[:6]
                prefix = (root_folder + "/") if root_folder else ""
                for name, data in extra_files.items():
                    writer.add_stored(prefix + name, data, now)
            writer.close()
        return stats
    except Decline:
        try:
            os.remove(output_zip)
        except OSError:
            pass
        raise
    except (zipfile.BadZipFile, OSError, struct.error) as e:
        try:
            os.remove(output_zip)
        except OSError:
            pass
        raise Decline("could not read the source as a zip: %s" % e)
