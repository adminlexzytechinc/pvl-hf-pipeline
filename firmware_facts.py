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
Chipset: payload_reader (scatter, preloader, APDB).
Model: the phone's own words -- model_reader.py (vbmeta label, recovery
  settings, build.prop); Samsung: the Odin part names. Never the preloader
  filename, which is the project name (2026-10-03).
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

import model_reader

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


# The phone's own software: on newer Unisoc phones "system" is a shared base
# that reports an older Android (Tecno KN3: system 13, product 15 -- the phone
# runs 15), so the newest of these wins (2026-10-04).
MAIN_PARTS = ('system', 'product', 'system_ext')


def _num(v):
    return tuple(int(x) for x in re.findall(r'\d+', v))


def _android_from_props(props):
    best = None
    for part in MAIN_PARTS:
        v = props.get((part, 'os_version'))
        if v and re.match(r'^\d{1,2}(\.\d)?$', v) and (best is None or _num(v) > _num(best[0])):
            best = (v, 'vbmeta %s.os_version' % part)
    if best:
        return best
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
    best = None
    for part in MAIN_PARTS:
        v = props.get((part, 'security_patch'))
        if v and PATCH_OK.match(v) and (best is None or v > best[0]):
            best = (v, 'vbmeta %s.security_patch' % part)
    if best:
        return best
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


def collect(names, read, payload=None, opener=None):
    """
    names    every member path in the archive
    read     read(name, limit) -> bytes or None
    payload  payload_reader.read_payload() output, when the caller has it
    opener   optional opener(name) -> seekable file, for build.prop inside
             super.img (Android 10); without it that step is skipped

    Returns the card's facts, each with a matching '<field>_source'.
    """
    facts = {}
    payload = payload or {}
    leaves = {_leaf(n).lower(): n for n in names}

    if payload.get('soc'):
        facts['chipset'], facts['chipset_source'] = payload['soc'], 'archive contents'
    # The model: Samsung's comes from the Odin part names (payload). Every
    # other phone's comes from its own words (model_reader.py, 2026-10-03).
    # The preloader filename is the PROJECT name, not the phone (KH7N inside
    # the KH7S firmware), so it is never used as the model.
    if payload.get('container') == 'samsung_odin' and payload.get('model_code'):
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
    # Unisoc .pac: its vbmeta images sit inside the .pac (2026-10-04).
    if not props and opener:
        pac = model_reader.pac_member(leaves)
        if pac:
            try:
                f = opener(pac)
                for blob in (model_reader.pac_vbmeta_blobs(f) if f else []):
                    props.update({k: v for k, v in avb_properties(blob).items() if k not in props})
            except Exception:
                pass

    if 'model' not in facts and payload.get('container') != 'samsung_odin':
        fps = {part: v for (part, key), v in props.items() if key == 'fingerprint'}
        try:
            model, m_src = model_reader.real_model(leaves, read, fps, opener)
        except Exception:
            model, m_src = '', ''
        if model:
            facts['model'], facts['model_source'] = model, m_src

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

    # --- a loose build.prop (realme ships one beside the .pac) -----------
    if not android or not patch:
        bp = model_reader.loose_build_prop(leaves, read)
        v = bp.get('ro.build.version.release') or bp.get('ro.system.build.version.release')
        if not android and v and re.match(r'^\d{1,2}(\.\d)?$', v):
            android, a_src = v, 'build.prop'
        v = bp.get('ro.build.version.security_patch')
        if not patch and v and PATCH_OK.match(v):
            patch, p_src = v, 'build.prop'

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
