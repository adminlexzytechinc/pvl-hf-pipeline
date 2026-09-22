r"""
Read everything an archive's own contents prove about the firmware inside it.

WHY THIS EXISTS
---------------
Every identity check in this project reads the *filename*, and filenames are
the one thing mirrors change. A real case from the live site:

    rom file, lexzytechinc.com.zip     published on an Infinix X6896 post

Nothing in that name carries a digit, so every name-based guard had nothing to
compare and waved it through. The archive's contents say plainly that it is a
different phone:

    preloader_x6856_h971.bin           model X6856, board H971
    MT6897_Android_scatter.txt         MT6897, not the post's MT6858

A mirror renames the zip. It does not rename the files inside it. That makes
the payload the only rename-proof evidence available, which is why it outranks
every other source in the trust ladder (payload=5, filename=4, ai_verified=3,
gsmarena=2, ai=1).

TIERS
-----
Tier 0  Filename and central-directory metadata only. FREE: the build already
        walks every entry, it simply throws the list away. Everything this
        module *reports* is Tier 0.

Tier 1  Targeted reads of small files already inside the archive (boot.img
        header, android-info.txt, updater-script, scatter body, .pac header).
        Cheap but not free. This module only NAMES them, in `tier1_targets`,
        so the caller can decide. It never opens them.

RULES THIS MODULE FOLLOWS
-------------------------
1.  Enrich, never rename. Nothing here rewrites a vendor string. Facts carry
    provenance so the caller can add only what is missing.
2.  Never average a disagreement. Two conflicting claims produce a flag, not a
    winner -- except where one source is definitionally authoritative. The
    scatter file is named after the application processor, so a modem part
    number in the same archive is companion silicon, not a competing claim:
        MT6897_Android_scatter.txt                     the SoC
        mcf_ota_TK_MD_NLWCG_MT6197_6897__R16MP5.img    MT6197, the modem
    Treating that as a conflict refuses to name a chip the archive states.
3.  Positional extraction only. preloader_<model>_<board>.bin is read by
    position. Board codes are NOT always H-prefixed -- P586A is real -- so
    matching /H\d+/ would have been wrong.
4.  Explicit lookarounds, never \b, around part numbers. Underscore is a word
    character, so \b never fires between "_" and "M", and every corroborating
    filename here is underscore-separated (efuse_MT6765.xml, APDB_MT6765_S01).
    That trap has already cost this project two bugs.
5.  Version floors are floors. A device that shipped on Android 10 and was
    updated still carries super.img, so structure proves "at least", never
    "exactly".
"""

import re

# ---------------------------------------------------------------------------
# Part numbers
# ---------------------------------------------------------------------------

# MT6765, MT6789, MT8788, MT6757T. Lookarounds, not \b -- see rule 4.
RE_MTK = re.compile(r'(?<![A-Za-z0-9])MT(\d{4})([A-Z]{0,2})(?![A-Za-z0-9])', re.I)
# Unisoc/Spreadtrum. SC9863A, SC7731E, and the T-series marketing names (T610).
RE_UNISOC = re.compile(r'(?<![A-Za-z0-9])(SC\d{4}[A-Z]?|T\d{3})(?![A-Za-z0-9])', re.I)
# Qualcomm: MSM8916, SDM660, SM6115, APQ8064.
RE_QC = re.compile(
    r'(?<![A-Za-z0-9])((?:MSM|APQ|SDM|SM|QM|QCM)\d{3,4}[A-Z]{0,2})(?![A-Za-z0-9])', re.I)
RE_EXYNOS = re.compile(r'(?<![A-Za-z0-9])Exynos[ _-]?(\d{3,4})(?![A-Za-z0-9])', re.I)

# MediaTek ships the scatter as .txt on older platforms and .xml on newer ones.
# Matching only .txt would have missed the Oukitel archive outright.
RE_SCATTER = re.compile(
    r'^(?:(MT\d{4}[A-Z]{0,2})_)?android[_-]?scatter.*\.(?:txt|xml)$', re.I)

# preloader_<model>_<board>.bin
RE_PRELOADER = re.compile(r'^preloader_([a-z0-9]+)_([a-z0-9]+)\.bin$', re.I)
# preloader_ufs.img / preloader_emmc.bin -- storage type, not a model code.
RE_PRELOADER_STORAGE = re.compile(r'^preloader_(ufs|emmc)\.(?:bin|img)$', re.I)

# Both spellings are real:
#   APDB_MT6739_S01_alps-trunk-o1.bsp_W18.36
#   APDB_MT6897___W2552              <- no section number at all
RE_APDB = re.compile(r'^APDB_MT\d{4}[A-Z]*(?:_S\d+)?[_.]*(.+)$', re.I)
RE_ALPS = re.compile(r'alps-\w+-([a-z]\d?)(?![a-z0-9])', re.I)
# W18.36 and W2552 are both in the wild -- the dot is optional.
RE_WEEK = re.compile(r'(?<![A-Za-z0-9])W(\d{2})\.?(\d{2})(?![A-Za-z0-9])')

# MDDB_..._MOLY_LR12A_R2_MP_V56_1_P1_1_ulwtg_n.EDB
RE_MOLY = re.compile(r'MOLY[_.]([A-Z0-9_]+?)_((?:[ulwctgn]){3,8})_', re.I)

ALPS = {'m': 'Android 6 (Marshmallow)', 'n': 'Android 7 (Nougat)',
        'o': 'Android 8 (Oreo)', 'o1': 'Android 8.1 (Oreo MR1)',
        'p': 'Android 9 (Pie)', 'q': 'Android 10', 'r': 'Android 11',
        's': 'Android 12', 't': 'Android 13', 'u': 'Android 14'}

# Informational only. Never used for identity, never shown as a flashing fact.
BANDS = {'u': 'UMTS', 'l': 'LTE', 'w': 'WCDMA', 'c': 'CDMA',
         't': 'TD-SCDMA', 'g': 'GSM', 'n': 'NR (5G)'}

# ---------------------------------------------------------------------------
# Structure -> Android version FLOORS (rule 5)
# ---------------------------------------------------------------------------
# Each entry is the release that INTRODUCED the partition. Presence proves the
# build is at least that new. It never proves the build is exactly that.
VERSION_FLOOR = [
    ('init_boot.img',   13, 'init_boot is a generic-kernel-image 13+ partition'),
    ('vendor_boot.img', 11, 'vendor_boot arrived with GKI 2.0 in Android 11'),
    ('super.img',       10, 'super / dynamic partitions arrived in Android 10'),
    ('vbmeta.img',       8, 'AVB 2.0 vbmeta arrived in Android 8'),
    ('dtbo.img',         8, 'DTBO is required for devices launching on Android 8'),
]

# Transsion ships its own partitions. Their presence identifies the vendor
# GROUP -- Tecno, Infinix and itel are all Transsion -- and the model code
# then picks the brand within it.
TRANSSION_MAPS = ('tr_carrier.map', 'tr_company.map', 'tr_region.map',
                  'tr_preload.map', 'tr_manifest.map', 'tr_overlayfs.map')
RE_INFINIX_MODEL = re.compile(r'^X\d{3,4}[A-Z]?$', re.I)

# The Download Agent: the small program SP Flash Tool pushes into the phone's
# RAM to talk to it. If it is bundled, the thing a user would otherwise have to
# go and find is already in the box.
#
# preloader.bin is NOT a download agent and must not be counted as one. The DA
# runs in RAM and is never written; the preloader is a partition image that IS
# written to the phone. Conflating them would tell a user the tool is covered
# when it is not. They are reported as separate facts.
DA_NAMES = ('download_agent', 'mtk_allinone_da', 'da_pl')

# ---------------------------------------------------------------------------
# Brand containers. RULE: these resolve BEFORE any partition fingerprinting,
# because a Samsung PIT lists Qualcomm partition names and would otherwise be
# fingerprinted as a Qualcomm package.
# ---------------------------------------------------------------------------
CONTAINERS = [
    ('samsung_odin', re.compile(r'^(?:AP|BL|CP|CSC|HOME_CSC)_.*\.tar(?:\.md5)?$', re.I)),
    ('samsung_pit',  re.compile(r'\.pit$', re.I)),
    ('huawei',       re.compile(r'^UPDATE\.APP$', re.I)),
    ('lg',           re.compile(r'\.(?:kdz|dz)$', re.I)),
    ('sony',         re.compile(r'\.(?:ftf|sin)$', re.I)),
    ('oppo_realme',  re.compile(r'\.(?:ofp|ops)$', re.I)),
    ('nokia',        re.compile(r'\.nb0$', re.I)),
    ('unisoc_pac',   re.compile(r'\.pac$', re.I)),
]

# Samsung encodes the region in the CSC filename and the model in the leading
# token: CSC_OLM_G556BOLM2AXD2.tar.md5 -> region OLM, model prefix G556B.
RE_SAMSUNG_CSC = re.compile(r'^(?:HOME_)?CSC_([A-Z]{3})_', re.I)
RE_SAMSUNG_PART = re.compile(r'^(?:AP|BL|CP|CSC|HOME_CSC)_([A-Z]\d{3}[A-Z]?)', re.I)

# Qualcomm packages are identified by their flashing scaffolding, not by a part
# number in a filename.
RE_QC_RAWPROGRAM = re.compile(r'^rawprogram\d*\.xml$', re.I)
RE_QC_FIREHOSE = re.compile(r'(?:prog_.*firehose|firehose).*\.(?:elf|mbn)$', re.I)

RE_XIAOMI_FLASH = re.compile(r'^flash_all(?:_except_data_storage|_lock)?\.(?:bat|sh)$', re.I)

# A/B slotted partitions: boot_a.img / boot_b.img.
RE_AB_SUFFIX = re.compile(r'_(?:a|b)\.img$', re.I)

# Tier 1 candidates: small files worth opening later, and what each would add.
TIER1 = [
    ('boot.img',          'OS version and security patch date from the AOSP header'),
    ('init_boot.img',     'OS version and security patch date (Android 13+)'),
    ('android-info.txt',  'required board name and baseband version'),
    ('version.csv',       "the vendor's own version string (Transsion)"),
    ('build.prop',        'fingerprint, device codename, build id'),
    ('payload_properties.txt', 'A/B OTA build fingerprint'),
    ('metadata',          'A/B OTA pre/post build fingerprint'),
    ('Checksum.ini',      'per-partition checksums'),
    ('misc.txt',          'Xiaomi fastboot ROM build tag'),
]


def _leaf(path):
    """Last path segment, separator-agnostic. Archives carry both kinds."""
    return path.replace('\\', '/').rstrip('/').split('/')[-1]


def _segments(path):
    return [s for s in path.replace('\\', '/').split('/') if s]


def _norm_entries(entries):
    """
    Accept bare strings, or dicts carrying central-directory metadata:
        {'name': ..., 'size': int, 'date': 'YYYY-MM-DD'}
    Size and date are FREE: the zip central directory already holds them and
    the build already reads it to walk the archive.
    """
    out = []
    for e in entries:
        if isinstance(e, dict):
            name = str(e.get('name', '')).strip().strip('"')
            rec = {'path': name, 'size': e.get('size'), 'date': e.get('date')}
        else:
            name = str(e).strip().strip('"')
            rec = {'path': name, 'size': None, 'date': None}
        rec['leaf'] = _leaf(rec['path'])
        if rec['leaf']:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Section readers
# ---------------------------------------------------------------------------

def _read_container(leaves_lower, leaves):
    """
    Brand containers first (rule: precedence over fingerprinting).
    Returns (container_name or None, extra facts dict).
    """
    extra = {}
    found = None
    for name, rx in CONTAINERS:
        if any(rx.match(l) for l in leaves):
            found = name
            break

    if found in ('samsung_odin', 'samsung_pit'):
        for l in leaves:
            m = RE_SAMSUNG_CSC.match(l)
            if m:
                extra['region_code'] = m.group(1).upper()
                break
        for l in leaves:
            m = RE_SAMSUNG_PART.match(l)
            if m:
                extra['model_code'] = m.group(1).upper()
                extra['model_code_source'] = 'samsung odin part name'
                break
        # Which Odin slots are present tells the user what this package flashes.
        slots = [s for s in ('AP', 'BL', 'CP', 'CSC', 'HOME_CSC')
                 if any(l.upper().startswith(s + '_') for l in leaves)]
        if slots:
            extra['odin_slots'] = slots
            extra['full_flash'] = 'AP' in slots and 'BL' in slots and 'CP' in slots
    return found, extra


def _read_platform(names, leaves_lower, tokens=None):
    """
    Which silicon, and how sure. The scatter file arbitrates for MediaTek; for
    the others the flashing scaffolding is the evidence.
    """
    out = {'platform': None, 'soc': None, 'soc_evidence': [],
           'soc_conflict': None, 'companion_chips': [], 'flash_tool': None}
    if tokens is None:
        tokens = names

    # --- MediaTek ---------------------------------------------------------
    scatter = None
    scatter_soc = None
    for leaf in names:
        m = RE_SCATTER.match(leaf)
        if m:
            scatter = leaf
            if m.group(1):
                scatter_soc = m.group(1).upper()
            break

    mtk_hits = {}
    for leaf in tokens:
        for m in RE_MTK.finditer(leaf):
            part = ('MT' + m.group(1) + m.group(2)).upper()
            mtk_hits.setdefault(part, []).append(leaf)

    if scatter or mtk_hits:
        out['platform'] = 'mediatek'
        out['scatter'] = scatter
        out['flash_tool'] = 'SP Flash Tool'
        if scatter_soc:
            # Authoritative. Everything else in the archive is companion
            # silicon, not a competing claim (rule 2).
            out['soc'] = scatter_soc
            out['companion_chips'] = sorted(p for p in mtk_hits if p != scatter_soc)
            out['soc_evidence'] = sorted(set(mtk_hits.get(scatter_soc, [])))
        elif len(mtk_hits) == 1:
            out['soc'] = list(mtk_hits)[0]
            out['soc_evidence'] = sorted(set(list(mtk_hits.values())[0]))
        elif len(mtk_hits) > 1:
            # No scatter to arbitrate and more than one part number. Claim
            # nothing and flag it.
            out['soc_conflict'] = sorted(mtk_hits)
        return out

    # --- Unisoc -----------------------------------------------------------
    if any(l.endswith('.pac') for l in leaves_lower):
        out['platform'] = 'unisoc'
        # The user is explicit on this: SPD, Unisoc and Spreadtrum are the same
        # vendor, and the tool is SPD Research / UpgradeDownload -- NOT SP
        # Flash Tool, which is MediaTek's.
        out['flash_tool'] = 'SPD Research Tool / UpgradeDownload'
        hits = {}
        for leaf in tokens:
            for m in RE_UNISOC.finditer(leaf):
                hits.setdefault(m.group(1).upper(), []).append(leaf)
        if len(hits) == 1:
            out['soc'] = list(hits)[0]
            out['soc_evidence'] = sorted(set(list(hits.values())[0]))
        elif len(hits) > 1:
            out['soc_conflict'] = sorted(hits)
        return out

    # --- Qualcomm ---------------------------------------------------------
    has_raw = any(RE_QC_RAWPROGRAM.match(l) for l in names)
    has_fh = any(RE_QC_FIREHOSE.search(l) for l in names)
    if has_raw or has_fh:
        out['platform'] = 'qualcomm'
        out['flash_tool'] = 'QFIL / QPST'
        out['loader_present'] = has_fh
        hits = {}
        for leaf in tokens:
            for m in RE_QC.finditer(leaf):
                hits.setdefault(m.group(1).upper(), []).append(leaf)
        if len(hits) == 1:
            out['soc'] = list(hits)[0]
            out['soc_evidence'] = sorted(set(list(hits.values())[0]))
        elif len(hits) > 1:
            out['soc_conflict'] = sorted(hits)
        return out

    # --- Exynos -----------------------------------------------------------
    for leaf in tokens:
        m = RE_EXYNOS.search(leaf)
        if m:
            out['platform'] = 'exynos'
            out['soc'] = 'Exynos ' + m.group(1)
            out['soc_evidence'] = [leaf]
            break
    return out


def _read_mtk_details(names, soc):
    """Model, board, storage, Android branch, build week, bands."""
    out = {}
    soc_digits = soc[2:6] if soc and soc.startswith('MT') else ''

    for leaf in names:
        m = RE_PRELOADER.match(leaf)
        if m:
            cand, board = m.group(1).upper(), m.group(2).upper()
            # Transsion and Infinix name the preloader after the DEVICE:
            #   preloader_lb7_h393.bin   -> model LB7,  board H393
            # Others name it after the PLATFORM, which is the SoC in disguise:
            #   preloader_k6789v1_64.bin -> MT6789 reference board, not a model
            # Claiming the latter as a model would put a board name on a phone.
            if soc_digits and soc_digits in cand:
                out['platform_board'] = cand + '_' + board
            else:
                out['model_code'] = cand
                out['board_code'] = board
                out['model_code_source'] = 'preloader filename'
            out['preloader'] = leaf
            break

    for leaf in names:
        m = RE_PRELOADER_STORAGE.match(leaf)
        if m:
            out['storage_type'] = m.group(1).upper()
            break

    for leaf in names:
        m = RE_APDB.match(leaf)
        if not m:
            continue
        tail = m.group(1)
        a = RE_ALPS.search(tail)
        if a:
            out['android_branch'] = ALPS.get(a.group(1).lower(),
                                             'alps branch ' + a.group(1))
            out['android_branch_source'] = 'APDB alps branch'
        w = RE_WEEK.search(tail)
        if w:
            out['build_week'] = '20%s week %s' % (w.group(1), w.group(2))
        out['apdb'] = leaf
        break

    for leaf in names:
        m = RE_MOLY.search(leaf)
        if m:
            letters = m.group(2).lower()
            out['modem_build'] = m.group(1).upper()
            out['band_code'] = letters
            out['bands'] = [BANDS[c] for c in letters if c in BANDS]
            break
    return out


def _read_shape(entries, names, leaves_lower, container=None):
    """
    What kind of package this is, and what it will do to the phone. This is
    the part a user actually needs before flashing.
    """
    out = {'_container': container}
    has = lambda n: n in leaves_lower

    out['full_flash'] = has('userdata.img') or has('cache.img')
    out['dynamic_partitions'] = has('super.img')
    out['avb_present'] = has('vbmeta.img') or any(
        l.endswith('-verified.img') or l.endswith('-verified.bin') for l in leaves_lower)
    out['ab_slots'] = any(RE_AB_SUFFIX.search(l) for l in names)
    out['checksum_present'] = any('checksum' in l for l in leaves_lower)
    out['loader_present'] = any(
        any(d in l for d in DA_NAMES) for l in leaves_lower)
    out['loader_files'] = sorted({l for l in names
                                  if any(d in l.lower() for d in DA_NAMES)})
    # Reported separately, on purpose -- see the note on DA_NAMES.
    out['preloader_present'] = any(
        l.startswith('preloader') for l in leaves_lower)

    # Package type, in the order a reader would recognise them.
    if has('payload.bin'):
        ptype = 'A/B OTA (payload.bin)'
    elif any(RE_XIAOMI_FLASH.match(l) for l in names):
        ptype = 'fastboot ROM'
    elif out['full_flash']:
        ptype = 'full flash (wipes user data)'
    elif has('boot.img') or has('system.img') or has('super.img'):
        ptype = 'partition images, no userdata'
    else:
        ptype = None
    out['package_type'] = ptype

    # Is this firmware at all? The dataset has a flash TOOL sitting in it
    # (Kirin-Tool-v2.4.2), and this is the check that would catch that class.
    #
    # Three shapes had to be added after running this over real archives,
    # because each one is firmware that carries NONE of the AOSP partition
    # names this originally looked for:
    #
    #   Samsung Odin   AP_/BL_/CP_/CSC_*.tar.md5 and nothing else
    #   Unisoc         a single .pac holding every partition
    #   repacked       root/Firmware/<one big .pac>, the rest being the
    #                  repacker's Credits.txt and Driver/ folder
    #
    # All three reported is_firmware=False, which would have flagged real
    # firmware as junk. The container check is what fixes it: a brand
    # container IS the evidence, exactly as the precedence rule says.
    out['is_firmware'] = bool(
        out['package_type']
        or any(RE_SCATTER.match(l) for l in names)
        or has('boot.img') or has('system.img')
        or out.get('_container')
        or any(l.endswith(('.pac', '.pit', '.kdz', '.dz', '.ofp', '.ops',
                           '.nb0', '.ftf')) for l in leaves_lower)
        or any(l.startswith(('ap_', 'bl_', 'cp_', 'csc_', 'home_csc_'))
               and '.tar' in l for l in leaves_lower))

    # Android version FLOOR from structure (rule 5).
    floor = None
    reason = None
    for part, ver, why in VERSION_FLOOR:
        if has(part):
            if floor is None or ver > floor:
                floor, reason = ver, why
    if floor:
        out['android_floor'] = floor
        out['android_floor_reason'] = reason

    # Sizes and dates are free from the central directory.
    sizes = [e['size'] for e in entries if isinstance(e.get('size'), int)]
    if sizes:
        out['uncompressed_bytes'] = sum(sizes)
        biggest = max((e for e in entries if isinstance(e.get('size'), int)),
                      key=lambda e: e['size'])
        out['largest_entry'] = {'name': biggest['leaf'], 'size': biggest['size']}
    dates = sorted({e['date'] for e in entries if e.get('date')})
    if dates:
        # The newest entry is the closest thing to a build date that survives
        # a rename. The oldest is usually a stale vendor file.
        out['entry_date_range'] = [dates[0], dates[-1]]

    out['partitions'] = sorted({l[:-4] for l in leaves_lower if l.endswith('.img')})
    return out


def _read_vendor(names, leaves_lower, model_code):
    out = {}
    if any(t in leaves_lower for t in TRANSSION_MAPS):
        out['vendor_group'] = 'Transsion'
        out['vendor_evidence'] = sorted(
            {l for l in names if l.lower() in TRANSSION_MAPS})
        if model_code:
            # Within Transsion the model code picks the brand: Infinix uses
            # X####, Tecno and itel use short letter+digit codes (LB7, AD11).
            out['brand_hint'] = ('Infinix' if RE_INFINIX_MODEL.match(model_code)
                                 else 'Tecno or itel')
    return out


def _read_root_name(entries):
    """
    The top folder INSIDE the archive. This is the vendor's own package name
    and it survives the zip being renamed:

        Tecno_Pouvoir_3_LB7_MT6739_V213_190425/Firmware/boot.img

    It is not always useful -- a repacker's watermark ends up here too
    ("rom file - lexzytechinc.com") -- so it is reported raw with a note, and
    the caller runs it through the existing watermark stripper before trusting
    it. Reported, never acted on here.
    """
    roots = set()
    for e in entries:
        segs = _segments(e['path'])
        if len(segs) > 1:
            roots.add(segs[0])
    if len(roots) != 1:
        return None
    root = roots.pop()
    # A single generic wrapper folder carries nothing.
    if root.lower() in ('firmware', 'rom', 'files', 'images', 'flash'):
        return None
    # A segment with no letters or digits carries nothing.
    if not re.search(r'[A-Za-z0-9]', root):
        return None
    return root


def _tier1_targets(leaves_lower, names):
    """Small files present in THIS archive that are worth opening later."""
    out = []
    for target, gives in TIER1:
        for l in names:
            if l.lower() == target.lower():
                out.append({'file': l, 'gives': gives})
                break
    for l in names:
        if RE_SCATTER.match(l):
            out.append({'file': l,
                        'gives': 'full partition table, offsets and storage type'})
            break
    for l in names:
        if l.lower().endswith('.pac'):
            out.append({'file': l,
                        'gives': 'device and product name from the PAC header'})
            break
    for l in names:
        if RE_QC_RAWPROGRAM.match(l):
            out.append({'file': l, 'gives': 'Qualcomm partition layout'})
            break
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def read_payload(entries):
    """
    entries: the archive's file list. Bare paths, or dicts with size/date.

    Returns a flat dict of facts. Every field is derived from the archive
    itself -- nothing is inferred from a device database, and nothing is
    guessed from the archive's own filename.
    """
    recs = _norm_entries(entries)
    names = [e['leaf'] for e in recs]
    leaves_lower = [n.lower() for n in names]

    # Part numbers are not always in a leaf. Unisoc packages routinely carry
    # the chip in the FOLDER name and nothing but "firmware.pac" beneath it:
    #     SC9863A_xyz/firmware.pac
    # So the silicon hunt sees directory segments too. Structural patterns
    # (scatter, preloader, APDB) still only ever match leaves, because those
    # are filenames by definition.
    tokens = list(names)
    for e in recs:
        for seg in _segments(e['path'])[:-1]:
            if seg not in tokens:
                tokens.append(seg)

    facts = {'entry_count': len(recs)}

    # 1. Brand containers, before any fingerprinting.
    container, extra = _read_container(leaves_lower, names)
    facts['container'] = container
    facts.update(extra)

    # 2. Silicon.
    plat = _read_platform(names, leaves_lower, tokens)
    for k, v in plat.items():
        if v not in (None, [], {}):
            facts[k] = v

    # 3. Platform-specific detail.
    if facts.get('platform') == 'mediatek':
        for k, v in _read_mtk_details(names, facts.get('soc')).items():
            facts.setdefault(k, v)

    # 4. Package shape.
    shape = _read_shape(recs, names, leaves_lower, container)
    shape.pop('_container', None)
    for k, v in shape.items():
        facts.setdefault(k, v)

    # 5. Vendor group and brand.
    facts.update(_read_vendor(names, leaves_lower, facts.get('model_code')))

    # 6. The vendor's own package name, if the archive kept it.
    root = _read_root_name(recs)
    if root:
        facts['archive_root_name'] = root
        facts['archive_root_note'] = (
            'vendor package name from inside the archive; '
            'run the watermark stripper before trusting it')

    # 7. What is worth opening next.
    facts['tier1_targets'] = _tier1_targets(leaves_lower, names)

    return facts


def contradicts(facts, claimed_model=None, claimed_soc=None):
    """
    The guard the X6896 case needed. Compares what the archive SAYS against
    what a post CLAIMS, and reports disagreement. It never picks a winner and
    it never deletes anything -- the caller holds the post for review.
    """
    problems = []
    mc = facts.get('model_code')
    if claimed_model and mc and mc.upper() != str(claimed_model).upper():
        problems.append('archive says model %s, post claims %s' % (mc, claimed_model))
    soc = facts.get('soc')
    if claimed_soc and soc and soc.upper() != str(claimed_soc).upper():
        problems.append('archive says %s, post claims %s' % (soc, claimed_soc))
    if facts.get('soc_conflict'):
        problems.append('archive names more than one chipset: '
                        + ', '.join(facts['soc_conflict']))
    if facts.get('is_firmware') is False:
        problems.append('archive does not look like firmware at all')
    return problems
