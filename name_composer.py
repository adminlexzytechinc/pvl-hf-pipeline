"""
The file-naming rule, in Python, for the build worker.

A port of pvl_hf_canonical_name() and its helpers from pvl-engine/pvl-match.php.
The worker needs it because the platform tag and chipset come from the archive's
payload, and the payload is only read here. It MUST agree with the PHP byte for
byte -- tests/test_composer_parity.py runs both on the same real filenames --
because WordPress and the worker would otherwise name one file two ways.

THE RULE: enrich, never rename. The vendor string survives byte for byte.
Brand, series and model may be prepended; chipset and platform appended; each
only when genuinely absent and only from a source at or above the trust floor.
Nothing is reordered, shortened, re-cased or re-spelled. Three Camon 30s Pro
regional builds differ only inside the vendor string, and composing a name from
metadata would collapse them into one file.

Keep this file mechanical. A change here is a change to the PHP first, then a
port, then a green parity run.
"""

import re

MATCH_EXTENSIONS = ('zip|rar|7z|pac|tar|gz|tgz|bz2|iso|bin|img|md5|ozip|kdz|dz|'
                    'sin|ftf|nb0|app')

NAME_TRUST = {
    'payload':     5,
    'filename':    4,
    'ai_verified': 3,
    'gsmarena':    2,
    'ai':          1,
}
NAME_MIN_TRUST = 2

NAME_SOC = re.compile(r'\b(MT\d{4}[A-Za-z]*|SC\d{4}[A-Za-z]*|S[DM]M?\d{3,4}[A-Za-z]*|T\d{3}[A-Za-z]*)\b')

PLATFORM_ALIASES = {
    'MTK':     ['mtk', 'mediatek'],
    'UNISOC':  ['unisoc'],
    'QC':      ['qc', 'qualcomm', 'snapdragon'],
    'SAMSUNG': ['samsung'],
    'HUAWEI':  ['huawei'],
    'LG':      ['lg'],
    'SONY':    ['sony'],
}

_EXT = re.compile(r'^(.*?)(\.(?:' + MATCH_EXTENSIONS + r'))$', re.I | re.S)


def name_tokens(text):
    t = re.sub(r'[^a-z0-9]+', ' ', str(text or ''), flags=re.I).lower()
    return [x for x in t.split(' ') if x != '']


def name_has(haystack_tokens, needle):
    want = name_tokens(needle)
    if not want:
        return True
    i = 0
    for tok in haystack_tokens:
        if tok == want[i]:
            i += 1
            if i == len(want):
                return True
    squashed = ''.join(want)
    if len(squashed) < 4:
        return False
    if not re.search(r'[a-z]', squashed):
        return False
    return squashed in ''.join(haystack_tokens)


def name_soc_conflict(haystack_tokens, value):
    want = re.sub(r'[^A-Za-z0-9]', '', str(value or '')).upper()
    if want == '':
        return ''
    for tok in haystack_tokens:
        m = NAME_SOC.search(tok.upper())
        if not m:
            continue
        found = m.group(1)
        if found == want:
            return 'SAME'
        if found.startswith(want) or want.startswith(found):
            return 'SAME'
        return found
    return ''


def name_platform_said(haystack_tokens, tag):
    aliases = PLATFORM_ALIASES.get(str(tag or '').upper(), [str(tag or '').lower()])
    for alias in aliases:
        if alias in haystack_tokens:
            return 'the name already says ' + alias
    for tok in haystack_tokens:
        m = NAME_SOC.search(tok.upper())
        if m:
            return 'the name already names chipset ' + m.group(1)
    return ''


def canonical_name(vendor_name, meta=None):
    """
    vendor_name: host-reported filename, watermarks already stripped.
    meta:        {field: {'value': str, 'source': key of NAME_TRUST}} for any of
                 brand, series, model, chipset, platform.
    Returns {'name': str, 'decisions': {field: {'status', 'why'}}}.
    """
    meta = meta or {}
    vendor_name = str(vendor_name or '').strip()

    ext = ''
    m = _EXT.match(vendor_name)
    if m:
        vendor_name, ext = m.group(1), m.group(2)

    tokens = name_tokens(vendor_name)
    prefix, suffix, decisions = [], [], {}

    for field in ('brand', 'series', 'model', 'chipset', 'platform'):
        entry = meta.get(field) or {}
        value = str(entry.get('value') or '').strip()
        source = entry.get('source') or 'ai'

        if value == '':
            decisions[field] = {'status': 'omitted', 'why': 'no value available'}
            continue

        if name_has(tokens, value):
            decisions[field] = {'status': 'present', 'why': 'already in the vendor name'}
            continue

        if field == 'platform':
            said = name_platform_said(tokens, value)
            if said:
                decisions[field] = {'status': 'present', 'why': said}
                continue

        if field == 'chipset':
            clash = name_soc_conflict(tokens, value)
            if clash == 'SAME':
                decisions[field] = {'status': 'present',
                                    'why': 'the vendor name already states this chipset'}
                continue
            if clash:
                decisions[field] = {'status': 'conflict',
                                    'why': 'the filename says ' + clash + ', metadata says '
                                           + value + ' -- not added, needs review'}
                continue

        if NAME_TRUST.get(source, 0) < NAME_MIN_TRUST:
            decisions[field] = {'status': 'omitted',
                                'why': 'only source is ' + source + ', uncorroborated'}
            continue

        if field in ('chipset', 'platform'):
            suffix.append(value.replace(' ', '_'))
        else:
            prefix.append(value.replace(' ', '_'))
        decisions[field] = {'status': 'added', 'why': 'from ' + source}
        tokens = tokens + name_tokens(value)

    name = vendor_name
    if prefix:
        name = '_'.join(prefix) + '_' + name
    if suffix:
        name = name + '_' + '_'.join(suffix)
    return {'name': name + ext, 'decisions': decisions}


# --- payload -> naming metadata ---------------------------------------------

def platform_tag(facts):
    """The family tag the payload proves, or '' when it proves none.

    Brand containers first, exactly as the reader resolves them: a Samsung
    Odin package is SAMSUNG whatever silicon it holds. Encrypted wrappers
    (.ofp/.ops) are not platforms and get no tag.
    """
    facts = facts or {}
    cont = facts.get('container')
    if cont in ('samsung_odin', 'samsung_pit'):
        return 'SAMSUNG'
    if cont == 'huawei':
        return 'HUAWEI'
    if cont == 'lg':
        return 'LG'
    if cont == 'sony':
        return 'SONY'
    if cont == 'unisoc_pac':
        return 'UNISOC'
    return {'mediatek': 'MTK', 'unisoc': 'UNISOC', 'qualcomm': 'QC',
            'exynos': 'SAMSUNG'}.get(facts.get('platform') or '', '')


def naming_meta(context_meta, facts, expected_model=''):
    """
    Merge what WordPress sent with what the archive proves.

    context_meta: {'brand': {...}, 'series': {...}, 'model': {...}} from the
                  build dispatch; brand and series arrive as 'gsmarena'.
    facts:        read_payload() output, or None when the archive was not read.

    The model is the careful one. NAMING-PLAN decision 2: a model the AI gave
    without corroboration never enters a filename. The one corroboration
    available here is the archive itself -- if it proves the same model the
    post claims, the model is added at payload trust. Anything else keeps the
    WordPress source, which for an unverified model is 'ai' and is omitted.
    """
    meta = {}
    for f in ('brand', 'series', 'model'):
        e = (context_meta or {}).get(f) or {}
        if e.get('value'):
            meta[f] = {'value': str(e['value']), 'source': e.get('source') or 'ai'}

    facts = facts or {}
    proved = facts.get('model_code')
    if proved:
        want = (meta.get('model') or {}).get('value') or expected_model or ''
        norm = lambda s: re.sub(r'[^A-Z0-9]', '', str(s).upper())
        if not want or norm(want).endswith(norm(proved)):
            # Use the post's spelling when it matches (SM-S741N, not S741N).
            meta['model'] = {'value': want or proved, 'source': 'payload'}
        else:
            # ADDED 2026-09-24. The archive proves a DIFFERENT phone from the
            # one the post names -- the live "rom file" case: an X6856 package
            # on the X6896 post. The name describes the file, so the proven
            # model goes in, and the post's series goes out: it belongs to the
            # other phone.
            meta['model'] = {'value': proved, 'source': 'payload'}
            meta.pop('series', None)
            meta['_contradiction'] = 'archive is model %s, the post says %s' % (proved, want)
    # Only a hint that names ONE brand. For Tecno/itel model codes the reader
    # says "Tecno or itel", which is a shrug, not a brand (P661N, 2026-09-24).
    hint = str(facts.get('brand_hint') or '').strip()
    if not meta.get('brand') and hint and ' or ' not in hint.lower():
        meta['brand'] = {'value': hint, 'source': 'payload'}

    if facts.get('soc') and not facts.get('soc_conflict'):
        meta['chipset'] = {'value': facts['soc'], 'source': 'payload'}
    tag = platform_tag(facts)
    if tag:
        meta['platform'] = {'value': tag, 'source': 'payload'}
    return meta


# --- the stored name, end to end (ADDED 2026-09-24) ---------------------------

# Same list as PVL_MATCH_NOISE in pvl-engine/pvl-match.php.
MATCH_NOISE = re.compile(
    r'\b(?:firmwares?|stock|roms?|flash(?:ing)?|files?|official|download|full|complete|'
    r'tested|free|new|latest|update|repair|fix|dead|boot|scatter|unbrick|frp|version|'
    r'tool|pack|original|factory|signed|untouched|working|ok|good|rmm)\b')

# How Google names a folder download: "<folder>-001" or
# "<folder>-20260917T155630Z-1-001". Not part of any vendor's name.
GOOGLE_FOLDER_SUFFIX = re.compile(r'(?:-\d{8}T\d{6}Z-\d+)?-\d{3}$')


def carries_identity(base):
    """False for names like "rom file", "Firmware", "Stock ROM": once the
    download words are gone, no token with a digit is left, so the name says
    nothing about which phone or which build it is. pvl_match_keys() gives
    such a name EMPTY identity keys."""
    n = MATCH_NOISE.sub(' ', re.sub(r'[^a-z0-9]+', ' ', str(base or '').lower()))
    return any(re.search(r'\d', t) for t in n.split())


# Samsung Odin part names: AP_<build>_..., CSC_<region>_<build>_...,
# HOME_CSC_<region>_<build>_... The build codes are Samsung's own, e.g.
# G556BXXS9BYDB (firmware) and G556BOLM9BYD9 (region package).
_ODIN_AP = re.compile(r'^AP_([A-Z0-9]{8,20})_', re.I)
_ODIN_CSC = re.compile(r'^CSC_[A-Z0-9]{3}_([A-Z0-9]{8,20})_', re.I)


def samsung_build_code(file_names):
    """
    Samsung's build code, read from the Odin files inside the archive:
    "G556BXXS9BYDB_G556BOLM9BYD9" (AP build, then CSC build). '' when the
    archive holds no AP file. ADDED 2026-09-24: uploaders shorten Samsung
    names ("G556BXXS9.0"), which drops the build and makes different builds
    look identical; the files inside always carry the full code.
    """
    ap = csc = ''
    for name in file_names or []:
        leaf = str(name).replace('\\', '/').rsplit('/', 1)[-1]
        m = _ODIN_AP.match(leaf)
        if m and not ap:
            ap = m.group(1).upper()
        m = _ODIN_CSC.match(leaf)
        if m and not csc:
            csc = m.group(1).upper()
    if not ap:
        return ''
    return ap + ('_' + csc if csc and csc != ap else '')


def vendor_base(outer_base, root_base='', samsung_code=''):
    """
    The vendor's own name for the build, from the outer archive name (already
    watermark-stripped, no extension) and the archive's top folder (same).

    Two repairs, both confirmed by the archive itself, never guessed:
      * "Itel_P40_P662L_V261_230118_12_SPD-001" -> "..._SPD" when the zip's
        own top folder is "..._SPD". The "-001" is Google's folder-download
        suffix (STORAGE-AND-BUILD-PLAN 9b), not the vendor's.
      * "rom file" -> the top folder's name, when that one carries identity.
      * "G556BXXS9.0" -> "G556BXXS9BYDB_G556BOLM9BYD9", the Samsung build
        code read from the AP and CSC files inside (samsung_build_code()).
    Returns '' when neither carries any identity.
    """
    outer_base = (outer_base or '').strip()
    root_base = (root_base or '').strip()
    # A Samsung package whose outer name lacks the full AP build code is named
    # by the code itself; a name that already carries it is left alone.
    if samsung_code:
        ap = samsung_code.split('_', 1)[0]
        if ap.lower() not in outer_base.lower():
            return samsung_code
    stripped = GOOGLE_FOLDER_SUFFIX.sub('', outer_base)
    if stripped != outer_base and root_base and stripped.lower() == root_base.lower():
        outer_base = stripped
    if carries_identity(outer_base):
        return outer_base
    if carries_identity(root_base):
        return root_base
    return ''


def parse_device_info(raw):
    """The build dispatch's device_info input: JSON {brand, series, model}
    from the file registry. Brand and series are GSMArena's (tier 2). The
    model is sent as 'ai' on purpose: the registry cannot say whether a human
    or a filename ever confirmed it, and NAMING-PLAN decision 2 says an
    unconfirmed model never enters a filename -- unless the archive proves it."""
    try:
        import json
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for f, src in (('brand', 'gsmarena'), ('series', 'gsmarena'), ('model', 'ai')):
        v = str(d.get(f) or '').strip()
        if v and v.upper() != 'N/A':
            out[f] = {'value': v, 'source': src}
    return out


def stored_base_name(vendor, device_info, facts):
    """
    The stored name, without extension, and the decisions behind it.
    vendor: vendor_base() output. device_info: parse_device_info() output.
    facts: payload_reader.read_payload() output, or None.
    Returns (name or '', decisions dict).
    """
    meta = naming_meta(device_info, facts)
    note = meta.pop('_contradiction', '')
    if vendor:
        out = canonical_name(vendor, meta)
    else:
        # Nothing to enrich: the name is built from the archive's own facts.
        # The board code (H971) stands in for the vendor string -- it is read
        # from the preloader's filename, so it is still the payload talking.
        board = (facts or {}).get('board_code') or ''
        out = canonical_name(board, meta)
        if not board:
            out['name'] = out['name'].strip('_')
    if note:
        out['decisions']['contradiction'] = {'status': 'flag', 'why': note}
    name = out['name'].strip('_ ')
    return name, out['decisions']
