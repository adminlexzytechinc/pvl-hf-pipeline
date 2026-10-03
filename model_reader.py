"""
The phone's REAL model, read from the phone's own words inside the firmware
(added 2026-10-03). Replaces the preloader filename, which is the PROJECT
name, not the phone: preloader_kh7n_h6919.bin sits inside the genuine KH7S
firmware, KL4HA32 is the KL4h, K69V1 is a board.

Tested read-only on the 244 stored files first (audit_real_model.py,
audit_old_model.py, audit_super_model.py): about 92% give a model this way.

WHERE, BEST FIRST
-----------------
1. vbmeta label (Android 11+). The build fingerprint per partition:
       TECNO/LC7S-ZAVC/TECNO-LC7     brand / product-region / device
   The PRODUCT part names the phone; the device part can be the base model
   (the LC7S's device is TECNO-LC7). MediaTek's generic vendor (mgvi_*) and
   Transsion's shared system (TSSI/FULL-64) say nothing and are skipped.
2. recovery.img settings (Android 6-9): ro.product.model in the ramdisk's
   default.prop / prop.default. "TECNO AX8S" -- whose device is TECNO-AX8.
3. build.prop in super.img / vendor.img / system.img (Android 10, whose
   recovery carries no model): ro.product.vendor.model. Needs random access
   (`opener`); skipped when the caller cannot give it.
Samsung is handled by the caller (the AP file name).

When nothing is found the answer is '' -- unknown. Never a guess.
"""

import re
import struct
import zlib

CODE = re.compile(r'^[A-Z]{1,4}\d{1,5}[A-Z0-9]{0,4}$', re.I)
GENERIC = re.compile(r'^(full|generic|tssi|aosp|qssi|mssi|sys_|system|gsi|mgvi|hal_|alps)', re.I)
FP_PARTS = ("dtbo", "vendor", "odm", "product", "system_ext", "boot", "vendor_boot", "system")
MODEL_KEYS = ("ro.product.model", "ro.product.vendor.model", "ro.product.system.model",
              "ro.product.product.model", "ro.product.odm.model")
DEVICE_KEYS = ("ro.product.device", "ro.product.vendor.device", "ro.product.system.device")
FP_KEYS = ("ro.build.fingerprint", "ro.vendor.build.fingerprint", "ro.system.build.fingerprint")
RAMDISK_MAX = 64 * 1024 * 1024


# --- turning the phone's words into a model code --------------------------

def _strip_brand(value, brand):
    v = (value or '').strip()
    for b in filter(None, {brand or '', 'tecno', 'infinix', 'itel'}):
        if v.lower().startswith(b.lower()) and len(v) > len(b) and v[len(b)] in ' -_':
            return v[len(b) + 1:].strip()
    return v


def from_fingerprint(fp):
    """'TECNO/LC7S-ZAVC/TECNO-LC7:10/...' -> 'LC7S'; '' when it names no phone."""
    bits = (fp or '').split(':')[0].split('/')
    if len(bits) < 3:
        return ''
    brand, product, device = bits[0], bits[1], bits[2]
    if GENERIC.match(product) or GENERIC.match(device):
        return ''
    dev = _strip_brand(device, brand)
    if dev == device:                  # device not "BRAND-CODE": a codename, not a model
        dev = ''
    dev = dev.split('-')[0]
    prod = product.split('-')[0]
    if CODE.match(prod) and dev and prod.upper().startswith(dev.upper()):
        return prod                    # LC7S over LC7, KH7S over KH7S
    return dev if CODE.match(dev or '') else ''


def from_props(props):
    """{key: value} from a .prop file -> (model, which key) or ('', '')."""
    brand = props.get('ro.product.brand') or props.get('ro.product.vendor.brand') or ''
    for k in MODEL_KEYS:
        m = _strip_brand(props.get(k), brand)
        first = m.split(' ')[0] if m else ''
        if CODE.match(first):
            return m, k                # "AX8S", "LC7S", "LA7 Pro"
    for k in FP_KEYS:
        code = from_fingerprint(props.get(k))
        if code:
            return code, k
    for k in DEVICE_KEYS:
        d = _strip_brand(props.get(k), brand).split('-')[0]
        if CODE.match(d):
            return d, k                # "Infinix NOTE 3 Pro" -> device X601-LTE -> X601
    return '', ''


def parse_props(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            out.setdefault(k.strip(), v.strip())
    return out


# --- 2. recovery ramdisk ----------------------------------------------------

def _cpio_props(data):
    text, i = '', 0
    while i + 110 <= len(data) and data[i:i + 6] in (b'070701', b'070702'):
        fsize = int(data[i + 54:i + 62], 16)
        nsize = int(data[i + 94:i + 102], 16)
        name = data[i + 110:i + 110 + nsize - 1].decode('latin-1')
        j = (i + 110 + nsize + 3) & ~3
        if name == 'TRAILER!!!':
            break
        if name.rsplit('/', 1)[-1] in ('default.prop', 'prop.default', 'build.prop'):
            text += data[j:j + fsize].decode('latin-1') + '\n'
        i = (j + fsize + 3) & ~3
    return text


def _ramdisk_props(blob):
    if blob[:4] == b'\x88\x16\x88\x58':          # MediaTek 512-byte header
        blob = blob[512:]
    if blob[:2] == b'\x1f\x8b':
        try:
            blob = zlib.decompressobj(31).decompress(blob, 256 * 1024 * 1024)
        except zlib.error:
            return {}
    if blob[:6] not in (b'070701', b'070702'):
        return {}                                 # lz4 or unknown: not read
    return parse_props(_cpio_props(blob))


def from_recovery(leaves, read):
    """leaves: {lowercase leaf: member name}. read(name, limit) -> bytes."""
    if 'ramdisk-recovery.img' in leaves:
        p = _ramdisk_props(read(leaves['ramdisk-recovery.img'], RAMDISK_MAX) or b'')
        m, k = from_props(p)
        if m:
            return m, 'ramdisk-recovery.img ' + k
    for leaf in ('recovery.img', 'recovery-verified.img'):
        if leaf not in leaves:
            continue
        img = read(leaves[leaf], RAMDISK_MAX) or b''
        if img[:8] != b'ANDROID!' or len(img) < 48:
            continue
        ksz, _ka, rsz = struct.unpack('<III', img[8:20])
        hv = struct.unpack('<I', img[40:44])[0]
        if hv > 2:
            continue                              # v3+ recovery: no settings copy worth trusting here
        page = struct.unpack('<I', img[36:40])[0] or 2048
        off = page + ((ksz + page - 1) // page) * page
        m, k = from_props(_ramdisk_props(img[off:off + rsz]))
        if m:
            return m, leaf + ' ' + k
    return '', ''


# --- 3. build.prop inside super / vendor / system images ---------------------

class _Img:
    """Random reads from a raw or Android-sparse image. f: seekable file."""

    def __init__(self, f):
        self.f = f
        f.seek(0)
        h = f.read(28)
        self.sparse = len(h) == 28 and struct.unpack('<I', h[:4])[0] == 0xED26FF3A
        if self.sparse:
            (_m, _a, _b, self.fhs, self.chs, self.bs, _tb, self.total, _c) = struct.unpack('<IHHHHIIII', h)
            self.map, self.pos, self.out, self.done = [], self.fhs, 0, 0

    def _extend(self, upto):
        while self.out < upto and self.done < self.total:
            self.f.seek(self.pos)
            t, _r, blocks, total = struct.unpack('<HHII', self.f.read(12))
            n, body = blocks * self.bs, self.pos + self.chs
            if t == 0xCAC1:
                self.map.append((self.out, n, 'raw', body))
            elif t == 0xCAC2:
                self.f.seek(body)
                self.map.append((self.out, n, 'fill', self.f.read(4)))
            elif t == 0xCAC3:
                self.map.append((self.out, n, 'zero', None))
            if t != 0xCAC4:
                self.out += n
            self.pos += total
            self.done += 1

    def read(self, off, n):
        if not self.sparse:
            self.f.seek(off)
            return self.f.read(n)
        self._extend(off + n)
        out = bytearray()
        for start, ln, kind, data in self.map:
            if start + ln <= off or start >= off + n:
                continue
            a, b = max(off, start), min(off + n, start + ln)
            if kind == 'raw':
                self.f.seek(data + a - start)
                out += self.f.read(b - a)
            elif kind == 'fill':
                out += (data * ((b - a) // 4 + 1))[:b - a]
            else:
                out += b'\0' * (b - a)
        return bytes(out)


def _lp_partitions(img):
    hdr = img.read(4096 * 3, 256)
    magic, _maj, _min, hsize = struct.unpack('<IHHI', hdr[:12])
    if magic != 0x414C5030:
        return {}
    tables_size = struct.unpack('<I', hdr[44:48])[0]
    p_off, p_num, p_sz, e_off, e_num, e_sz = struct.unpack('<6I', hdr[80:104])
    tabs = img.read(4096 * 3 + hsize, tables_size)
    exts = [struct.unpack('<QIQI', tabs[e_off + i * e_sz:e_off + i * e_sz + 24]) for i in range(e_num)]
    out = {}
    for i in range(p_num):
        ent = tabs[p_off + i * p_sz:p_off + (i + 1) * p_sz]
        name = ent[:36].split(b'\0')[0].decode('latin-1')
        first, num = struct.unpack('<II', ent[40:48])
        out[name] = [(exts[j][2] * 512, exts[j][0] * 512) for j in range(first, first + num) if exts[j][1] == 0]
    return out


class _Ext4:
    def __init__(self, img, extents):
        self.img, self.ext = img, extents
        sb = self.r(1024, 1024)
        if len(sb) < 256 or struct.unpack('<H', sb[56:58])[0] != 0xEF53:
            raise ValueError('not ext4')
        self.bs = 1024 << struct.unpack('<I', sb[24:28])[0]
        self.ipg = struct.unpack('<I', sb[40:44])[0]
        self.isz = struct.unpack('<H', sb[88:90])[0]
        self.dsz = struct.unpack('<H', sb[254:256])[0] if struct.unpack('<I', sb[96:100])[0] & 0x80 else 32
        self.gdt = (1 if self.bs > 1024 else 2) * self.bs

    def r(self, off, n):
        out, pos = b'', 0
        for po, pl in self.ext:
            if off < pos + pl and n > 0:
                a = off - pos
                take = min(n, pl - a)
                out += self.img.read(po + a, take)
                off, n = off + take, n - take
            pos += pl
        return out

    def inode(self, num):
        g, idx = divmod(num - 1, self.ipg)
        d = self.r(self.gdt + g * self.dsz, self.dsz)
        tbl = struct.unpack('<I', d[8:12])[0] | ((struct.unpack('<I', d[40:44])[0] << 32) if self.dsz >= 64 else 0)
        return self.r(tbl * self.bs + idx * self.isz, self.isz)

    def read_file(self, ino, cap=4 * 1024 * 1024):
        size = struct.unpack('<I', ino[4:8])[0] | (struct.unpack('<I', ino[108:112])[0] << 32)
        if size > cap:
            raise ValueError('too big')
        ext = []

        def walk(node):
            magic, entries, _mx, depth = struct.unpack('<HHHH', node[:8])
            if magic != 0xF30A:
                raise ValueError('no extents')
            for i in range(entries):
                e = node[12 + i * 12:24 + i * 12]
                if depth == 0:
                    lblk, ln, hi, lo = struct.unpack('<IHHI', e)
                    ext.append((lblk, (hi << 32) | lo, ln if ln <= 32768 else ln - 32768))
                else:
                    _lb, lo, hi = struct.unpack('<IIH', e[:10])
                    walk(self.r(((hi << 32) | lo) * self.bs, self.bs))
        walk(ino[40:100])
        return b''.join(self.r(p * self.bs, n * self.bs) for _l, p, n in sorted(ext))[:size]

    def lookup(self, path):
        num = 2
        for part in [p for p in path.split('/') if p]:
            data, i, found = self.read_file(self.inode(num), 64 * 1024 * 1024), 0, None
            while i + 8 <= len(data):
                inum, rec, nl = struct.unpack('<IHB', data[i:i + 7])
                if rec < 8:
                    break
                if inum and data[i + 8:i + 8 + nl].decode('latin-1') == part:
                    found = inum
                    break
                i += rec
            if not found:
                return None
            num = found
        return self.inode(num)


def _build_prop(img, extents, paths):
    try:
        fs = _Ext4(img, extents)
    except Exception:
        return {}
    for p in paths:
        try:
            ino = fs.lookup(p)
            if ino is not None:
                return parse_props(fs.read_file(ino).decode('latin-1'))
        except Exception:
            continue
    return {}


def from_images(leaves, opener):
    """opener(member name) -> seekable binary file, or None."""
    if not opener:
        return '', ''
    if 'super.img' in leaves:
        f = opener(leaves['super.img'])
        if f:
            img = _Img(f)
            parts = _lp_partitions(img)
            for pname, paths in (('vendor', ('/build.prop',)),
                                 ('system', ('/system/build.prop', '/build.prop')),
                                 ('product', ('/build.prop',))):
                name = next((n for n in (pname, pname + '_a') if parts.get(n)), None)
                if name:
                    m, k = from_props(_build_prop(img, parts[name], paths))
                    if m:
                        return m, 'super.img %s %s' % (pname, k)
    for leaf, paths in (('vendor.img', ('/build.prop',)), ('system.img', ('/system/build.prop', '/build.prop'))):
        if leaf in leaves:
            f = opener(leaves[leaf])
            if f:
                img = _Img(f)
                m, k = from_props(_build_prop(img, [(0, 1 << 62)], paths))
                if m:
                    return m, '%s %s' % (leaf, k)
    return '', ''


# --- all of it ----------------------------------------------------------------

def real_model(leaves, read, fingerprints, opener=None):
    """
    leaves        {lowercase leaf: member name}
    read          read(name, limit) -> bytes
    fingerprints  {partition: fingerprint} from the vbmeta images
    opener        optional; random access for step 3

    Returns (model, source) or ('', '').
    """
    for part in list(FP_PARTS) + sorted(set(fingerprints) - set(FP_PARTS)):
        code = from_fingerprint(fingerprints.get(part))
        if code:
            return code, 'vbmeta %s label' % part
    for step in (lambda: from_recovery(leaves, read), lambda: from_images(leaves, opener)):
        try:
            m, src = step()
        except Exception:
            m, src = '', ''
        if m:
            return m, src
    return '', ''
