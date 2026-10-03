"""
The Gold card's details, read from inside the firmware (added 2026-09-26).

Shown on the download card: Android version, security patch, chipset, model
code, storage, and for Samsung the bootloader (binary) level. Every value
comes from the archive itself and carries where it came from. Nothing is
guessed; a field that cannot be read is left out, and the card simply does
not show it.

WHERE EACH VALUE COMES FROM, BEST FIRST
--------------------------------------
Android and security patch
  1. vbmeta*.img properties -- com.android.build.system.os_version and
     .security_patch. The system's own label, a few KB. On the Oukitel WP19
     Pro: Android 13, patch 2023-12-05.
  2. The build fingerprint in the same properties ("...:13/TP1A...").
  3. The boot.img header -- but ONLY header version 0-2. From version 3 on
     (Android 11+ generic kernels) the header carries the KERNEL's own
     version: the same Oukitel boot.img says Android 12, patch 2023-08,
     which is wrong for the phone. Older phones are right: Tecno LB7
     (header v0) says 8.1.0, 2020-08.
  4. Samsung: "_OS16" in the AP file's name.
Chipset, model: payload_reader (scatter, preloader, APDB, Odin part names).
Storage: the scatter's HW_STORAGE_* -- shown only when it names ONE type.
  Newer MediaTek scatters list both eMMC and UFS (one firmware for both
  kinds of phone); then there is no single answer and the field is left out.
Bootloader (Samsung): the character after the update-type letter in the
  build code. G556BXXS9BYDB -> 9; G556BXXSIEZH2 -> I -> 18.

`read(name, limit)` is supplied by the caller: the build reads the local
archive, the backfill reads the stored file over HTTP ranges (remote_zip).
"""

import re
import struct

PROP = re.compile(
    rb'com\.android\.build\.([a-z_]+)\.(security_patch|os_version|fingerprint)\x00([\x20-\x7e]{1,160})\x00')
FP_ANDROID = re.compile(r':(\d{1,2}(?:\.\d)?)/')
PATCH_OK = re.compile(r'^(20\d\d)-(0[1-9]|1[0-2])(?:-(\d\d))?$')
SAMSUNG_OS = re.compile(r'_OS(\d{1,2})(?:\.|_|$)', re.I)
SAMSUNG_AP = re.compile(r'^AP_([A-Z0-9]+?)_', re.I)
STORAGE = re.compile(r'HW_STORAGE_([A-Z0-9]+)')
STORAGE_NAMES = {'EMMC': 'eMMC', 'UFS': 'UFS', 'NAND': 'NAND', 'NVME': 'NVMe'}

# Where the system's own version lives, most authoritative first.
PROP_PARTS = ('system', 'product', 'system_ext', 'vendor', 'odm')


def _leaf(name):
    return name.replace('\\', '/').split('/')[-1]


def boot_header(head):
    """Android version and patch level from a boot image header, or None.
    Returns header_version too, because v3+ values describe the kernel."""
    if len(head) < 48 or head[:8] != b'ANDROID!':
        return None
    hv = struct.unpack('<I', head[40:44])[0]
    if hv > 16:                                  # v0 keeps a page size near here
        hv = 0
    raw = struct.unpack('<I', head[16:20] if hv >= 3 else head[44:48])[0]
    out = {'header_version': hv}
    if raw:
        ver, lvl = raw >> 11, raw & 0x7FF
        a, b, c = (ver >> 14) & 0x7F, (ver >> 7) & 0x7F, ver & 0x7F
        y, m = (lvl >> 4) + 2000, lvl & 0xF
        if a:
            out['android'] = ('%d.%d' % (a, b)) if b else str(a)
        if 1 <= m <= 12 and 2008 <= y <= 2099:
            out['patch'] = '%d-%02d' % (y, m)
    return out


def avb_properties(blob):
    """{(partition, key): value} from a vbmeta image's property descriptors."""
    out = {}
    for part, key, val in PROP.findall(blob or b''):
        out.setdefault((part.decode(), key.decode()), val.decode())
    return out


def _android_from_props(props):
    for part in PROP_PARTS:
        v = props.get((part, 'os_version'))
        if v and re.match(r'^\d{1,2}(\.\d)?$', v):
            return v, 'vbmeta %s.os_version' % part
    for part in PROP_PARTS:
        fp = props.get((part, 'fingerprint'))
        m = FP_ANDROID.search(fp or '')
        if m:
            return m.group(1), 'vbmeta %s fingerprint' % part
    return None, None


def _patch_from_props(props):
    for part in PROP_PARTS:
        v = props.get((part, 'security_patch'))
        if v and PATCH_OK.match(v):
            return v, 'vbmeta %s.security_patch' % part
    return None, None


def samsung_bootloader(build_code):
    """Bootloader (binary) level from a Samsung build code, or ''.
    G556BXXS9BYDB: model G556B, region XX, type S, bootloader 9."""
    m = re.match(r'^([A-Z]\d{3,4}[A-Z0-9]{0,2}?)([A-Z]{2})([SUQ])([0-9A-Z])[A-Z][A-Z][A-L0-9]', build_code or '')
    if not m:
        return ''
    c = m.group(4)
    return c if c.isdigit() else str(ord(c) - ord('A') + 10)


def collect(names, read, payload=None):
    """
    names    every member path in the archive
    read     read(name, limit) -> bytes or None
    payload  payload_reader.read_payload() output, when the caller has it

    Returns the card's facts, each with a matching '<field>_source'.
    """
    facts = {}
    payload = payload or {}
    leaves = {_leaf(n).lower(): n for n in names}

    if payload.get('soc'):
        facts['chipset'], facts['chipset_source'] = payload['soc'], 'archive contents'
    if payload.get('model_code'):
        facts['model'] = payload['model_code']
        facts['model_source'] = payload.get('model_code_source', 'archive contents')

    # --- Android and patch: vbmeta first --------------------------------
    props = {}
    for leaf in sorted(leaves):
        if leaf.startswith('vbmeta') and leaf.endswith('.img'):
            try:
                props.update({k: v for k, v in avb_properties(read(leaves[leaf], 65536)).items() if k not in props})
            except Exception:
                pass
    android, a_src = _android_from_props(props)
    patch, p_src = _patch_from_props(props)

    # --- boot header, only where it describes the phone -----------------
    if not android or not patch:
        for leaf in ('boot.img', 'recovery.img'):
            if leaf not in leaves:
                continue
            try:
                h = boot_header(read(leaves[leaf], 4096))
            except Exception:
                h = None
            if h and h['header_version'] <= 2:
                if not android and h.get('android'):
                    android, a_src = h['android'], '%s header' % leaf
                if not patch and h.get('patch'):
                    patch, p_src = h['patch'], '%s header' % leaf
            break

    # --- Samsung --------------------------------------------------------
    if payload.get('container') == 'samsung_odin':
        for n in names:
            leaf = _leaf(n)
            if leaf.upper().startswith('AP_'):
                m = SAMSUNG_OS.search(leaf)
                if m and not android:
                    android, a_src = m.group(1), 'Samsung AP file name'
                ap = SAMSUNG_AP.match(leaf)
                if ap:
                    bl = samsung_bootloader(ap.group(1).upper())
                    if bl:
                        facts['bootloader'], facts['bootloader_source'] = bl, 'Samsung build code'
                break
        if facts.get('model') and not str(facts['model']).upper().startswith('SM-'):
            facts['model'] = 'SM-' + str(facts['model']).upper()

    if android:
        facts['android'], facts['android_source'] = android, a_src
    if patch:
        facts['security_patch'], facts['security_patch_source'] = patch, p_src

    # --- storage: only when the scatter names exactly one type ----------
    for leaf, full in leaves.items():
        if 'scatter' in leaf and leaf.endswith(('.txt', '.xml')):
            try:
                text = (read(full, 400000) or b'').decode('latin-1')
            except Exception:
                text = ''
            kinds = sorted(set(STORAGE.findall(text)))
            if len(kinds) == 1:
                facts['storage'] = STORAGE_NAMES.get(kinds[0], kinds[0])
                facts['storage_source'] = _leaf(full)
            break

    return facts
