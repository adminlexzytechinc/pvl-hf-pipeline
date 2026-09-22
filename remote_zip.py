"""
Read individual files out of a remote ZIP, using HTTP range requests.

WHY
---
A firmware archive is 3-10 GB, but the facts that identify it live in a
handful of small members: the scatter file, Checksum.ini, version.csv, a
boot.img header, the first bytes of a .pac. Downloading gigabytes to read
kilobytes is the whole cost this avoids.

A ZIP is laid out so this is possible: the index sits at the END, and every
member records where its data begins. So:

    1. fetch the last ~64 KiB      -> end-of-central-directory
    2. fetch the central directory -> every member's name, size and offset
    3. fetch one member's bytes    -> inflate just that member

Reading the full index of a 5.24 GB archive costs about 75 KiB.

TWO THINGS THAT MATTER
----------------------
ZIP64. Any archive over 4 GiB, and any member over 4 GiB, stores its real
sizes and offsets in an extra field and leaves 0xFFFFFFFF in the normal slot.
A reader that skips this silently reports 4294967295 for a 5 GB member and
puts the wrong offset in every range request. Both are handled here.

STORED members can be read from the middle. Firmware archives usually store
their payload rather than compressing it -- measured: a Takeout folder zip
reports compressedSize == sizeOfContents == the source file's exact byte
count. When a member is stored, an arbitrary slice of it is an arbitrary
slice of the file, so `peek()` can read 4 bytes from offset 2116 of a 4 GB
.pac without touching the rest. A deflated member has to be read from its
start, so `peek()` refuses rather than quietly pulling gigabytes.
"""

import io
import re
import struct
import zlib

import requests

EOCD_SIG     = b"PK\x05\x06"
EOCD64_SIG   = b"PK\x06\x06"
CD_SIG       = b"PK\x01\x02"
LOCAL_SIG    = b"PK\x03\x04"
ZIP64_EXTRA  = 0x0001


class RemoteZipError(Exception):
    pass


class RemoteZip:
    def __init__(self, url, auth=None, session=None, timeout=60):
        self.url = url
        self.timeout = timeout
        self.headers = {"Authorization": auth} if auth else {}
        self.session = session or requests.Session()
        self.size = None
        self.served_name = None
        self._entries = None
        self._probe()

    # ---- transport ------------------------------------------------------

    def _get(self, start, end):
        """Fetch bytes [start, end] inclusive."""
        h = dict(self.headers)
        h["Range"] = "bytes=%d-%d" % (start, end)
        r = self.session.get(self.url, headers=h, timeout=self.timeout)
        if r.status_code not in (200, 206):
            raise RemoteZipError("range %d-%d returned %d" % (start, end, r.status_code))
        return r.content

    def _probe(self):
        """Total length, and the filename the server reports."""
        h = dict(self.headers)
        h["Range"] = "bytes=0-0"
        r = self.session.get(self.url, headers=h, timeout=self.timeout)
        if r.status_code not in (200, 206):
            raise RemoteZipError("probe returned %d" % r.status_code)

        m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
        if m:
            self.size = int(m.group(1))
        elif r.headers.get("Content-Length"):
            self.size = int(r.headers["Content-Length"])
        else:
            raise RemoteZipError("server did not report a length; "
                                 "ranged reads are not possible")

        disp = r.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*=UTF-8''([^;]+)", disp) or \
            re.search(r'filename="([^"]+)"', disp)
        if m:
            from urllib.parse import unquote
            self.served_name = unquote(m.group(1))

    # ---- index ----------------------------------------------------------

    @staticmethod
    def _zip64_extra(extra, need_usize, need_csize, need_offset):
        """Pull the fields ZIP64 moved out of the fixed header.

        Only the saturated fields are present, in this order, so they must be
        consumed positionally -- reading them by fixed offset is wrong.
        """
        out = {}
        p = 0
        while p + 4 <= len(extra):
            hid, hsize = struct.unpack("<HH", extra[p:p + 4])
            body = extra[p + 4:p + 4 + hsize]
            if hid == ZIP64_EXTRA:
                q = 0
                for want, key in ((need_usize, "usize"),
                                  (need_csize, "csize"),
                                  (need_offset, "offset")):
                    if want and q + 8 <= len(body):
                        out[key] = struct.unpack("<Q", body[q:q + 8])[0]
                        q += 8
                break
            p += 4 + hsize
        return out

    def entries(self):
        if self._entries is not None:
            return self._entries

        tail_len = min(65536 + 22, self.size)
        tail = self._get(self.size - tail_len, self.size - 1)
        base = self.size - tail_len

        i = tail.rfind(EOCD_SIG)
        if i < 0:
            raise RemoteZipError("no end-of-central-directory; "
                                 "the archive is truncated or not a ZIP")
        count, cd_size, cd_off = struct.unpack("<HII", tail[i + 10:i + 20])

        j = tail.rfind(EOCD64_SIG)
        if j >= 0 and (cd_off == 0xFFFFFFFF or count == 0xFFFF or cd_size == 0xFFFFFFFF):
            count, cd_size, cd_off = struct.unpack("<QQQ", tail[j + 32:j + 56])

        cd = (tail[cd_off - base:cd_off - base + cd_size]
              if cd_off >= base else self._get(cd_off, cd_off + cd_size - 1))

        out, p = [], 0
        while p + 46 <= len(cd) and cd[p:p + 4] == CD_SIG:
            (_sig, _ver, _need, _flag, method, _t, _d, _crc,
             csize, usize, nlen, elen, clen,
             _disk, _ia, _ea, offset) = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])

            name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
            extra = cd[p + 46 + nlen:p + 46 + nlen + elen]

            z = self._zip64_extra(extra,
                                  usize == 0xFFFFFFFF,
                                  csize == 0xFFFFFFFF,
                                  offset == 0xFFFFFFFF)
            usize = z.get("usize", usize)
            csize = z.get("csize", csize)
            offset = z.get("offset", offset)

            out.append({
                "name": name,
                "size": usize,
                "csize": csize,
                "method": method,       # 0 = stored, 8 = deflate
                "header_offset": offset,
                "is_dir": name.endswith("/"),
            })
            p += 46 + nlen + elen + clen

        self._entries = out
        return out

    def _find(self, name):
        for e in self.entries():
            if e["name"] == name or e["name"].endswith("/" + name) \
               or e["name"].split("/")[-1] == name:
                return e
        raise RemoteZipError("no such entry: " + name)

    def _data_offset(self, e):
        """Where a member's bytes actually start.

        The local header repeats the name and carries its own extra field,
        and both are variable length, so the central directory's offset points
        at the header, not the data. The header must be read to find the data.
        """
        head = self._get(e["header_offset"], e["header_offset"] + 29)
        if head[:4] != LOCAL_SIG:
            raise RemoteZipError("bad local header for " + e["name"])
        nlen, elen = struct.unpack("<HH", head[26:30])
        return e["header_offset"] + 30 + nlen + elen

    # ---- reading --------------------------------------------------------

    def read(self, name, limit=None):
        """Return a member's decompressed bytes (optionally only the first
        `limit` of them)."""
        e = self._find(name)
        start = self._data_offset(e)

        if e["method"] == 0:
            want = e["size"] if limit is None else min(limit, e["size"])
            if want == 0:
                return b""
            return self._get(start, start + want - 1)

        if e["method"] != 8:
            raise RemoteZipError("unsupported compression method %d for %s"
                                 % (e["method"], e["name"]))

        # Deflate must be read from the start. Pull only as much compressed
        # input as could possibly be needed.
        take = e["csize"] if limit is None else min(e["csize"], max(limit * 4, 4096))
        raw = self._get(start, start + take - 1)
        data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(
            raw, limit if limit else 0)
        return data

    def peek(self, name, offset, length):
        """Read `length` bytes from `offset` INSIDE a member.

        Only valid for a stored member, where a slice of the archive is a
        slice of the file. For a deflated member the bytes are not addressable
        without inflating everything before them, so this refuses rather than
        quietly downloading gigabytes.
        """
        e = self._find(name)
        if e["method"] != 0:
            raise RemoteZipError(
                "%s is deflated (method %d); use read(limit=...) instead -- "
                "an arbitrary offset would require inflating everything before it"
                % (e["name"], e["method"]))
        if offset + length > e["size"]:
            length = max(0, e["size"] - offset)
        if length == 0:
            return b""
        start = self._data_offset(e) + offset
        return self._get(start, start + length - 1)
