"""
The payload reader, against every real archive listing this project has.

All four are real: three came from the user's own files, the fourth is the
mislabelled archive found on the live X6896 post. Nothing here is synthetic
except the last section, which pins behaviour the real four cannot reach
(Unisoc, Qualcomm, Samsung, and the not-firmware case).
"""

import sys
from payload_reader import read_payload, contradicts

PASS = 0
FAIL = 0


def ok(cond, label, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   ' + label)
    else:
        FAIL += 1
        print('  FAIL ' + label + ('\n       ' + str(detail) if detail else ''))


def split(block):
    return [l for l in block.strip().split('\n') if l.strip()]


# ---------------------------------------------------------------------------
# Real listing 1 -- Tecno Pouvoir 3 LB7, MT6739
# Keeps its vendor package folder name, which survives any rename of the zip.
# ---------------------------------------------------------------------------
TECNO = split(r"""
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\APDB_MT6739_S01_alps-trunk-o1.bsp_W18.36
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\boot.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\boot-verified.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\cache.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\Checksum.ini
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\efuse_MT6739.xml
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\lk.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\logo.bin
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\md1img.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\MDDB_InfoCustomAppSrcP_MT6739_S00_MOLY_LR12A_R2_MP_V56_1_P1_1_ulwtg_n.EDB
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\MT6739_Android_scatter.txt
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\preloader.bin
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\preloader_lb7_h393.bin
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\recovery.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\system.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\tranfs.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\userdata.img
Tecno_Pouvoir_3_LB7_MT6739_V213_190425\Firmware\vendor.img
""")

# ---------------------------------------------------------------------------
# Real listing 2 -- Infinix S4 X626, MT6765
# ---------------------------------------------------------------------------
INFINIX = split(r"""
...\firmware\APDB_MT6765_S01__W1904
...\firmware\boot.img
...\firmware\cache.img
...\firmware\dtbo.img
...\firmware\efuse_MT6765.xml
...\firmware\lk.img
...\firmware\MDDB_InfoCustomAppSrcP_MT6765_S00_MOLY_LR12A_R3_MP_V45_P5_1_ulwctg_n.EDB
...\firmware\MT6765_Android_scatter.txt
...\firmware\preloader.bin
...\firmware\preloader_x626_h623.bin
...\firmware\recovery.img
...\firmware\system.img
...\firmware\userdata.img
...\firmware\vbmeta.img
""")

# ---------------------------------------------------------------------------
# Real listing 3 -- Oukitel WP19 Pro, MT6789
# Scatter is .xml here, and the preloader names the PLATFORM, not the device.
# ---------------------------------------------------------------------------
OUKITEL = split(r"""
...\Firmware\boot.img
...\Firmware\download_agent
...\Firmware\dtbo.img
...\Firmware\MT6789_Android_scatter.xml
...\Firmware\preloader_k6789v1_64.bin
...\Firmware\super.img
...\Firmware\userdata.img
...\Firmware\vbmeta.img
...\Firmware\vendor_boot.img
""")

# ---------------------------------------------------------------------------
# Real listing 4 -- the mislabelled archive from the live X6896 post.
# It is actually X6856 / MT6897. Its root folder is a repacker's watermark.
# ---------------------------------------------------------------------------
ROMFILE = split(r"""
rom file - lexzytechinc.com\firmware\download_agent
rom file - lexzytechinc.com\firmware\APDB_MT6897___W2552
rom file - lexzytechinc.com\firmware\boot.img
rom file - lexzytechinc.com\firmware\init_boot.img
rom file - lexzytechinc.com\firmware\MDDB_InfoCustomAppSrcP_MT6897_S00_MOLY_NR17_R1_MP5_RC_MP_V51_2_P10_1_unlwctg_n.EDB
rom file - lexzytechinc.com\firmware\mcf_ota_TK_MD_NLWCG_MT6197_6897__R16MP5.img
rom file - lexzytechinc.com\firmware\MT6897_Android_scatter.txt
rom file - lexzytechinc.com\firmware\MT6897_Android_scatter.xml
rom file - lexzytechinc.com\firmware\preloader_x6856_h971.bin
rom file - lexzytechinc.com\firmware\preloader_ufs.img
rom file - lexzytechinc.com\firmware\super.img
rom file - lexzytechinc.com\firmware\system.map
rom file - lexzytechinc.com\firmware\tr_carrier.map
rom file - lexzytechinc.com\firmware\tr_company.map
rom file - lexzytechinc.com\firmware\tr_region.map
rom file - lexzytechinc.com\firmware\userdata.img
rom file - lexzytechinc.com\firmware\vbmeta.img
rom file - lexzytechinc.com\firmware\vendor_boot.img
rom file - lexzytechinc.com\firmware\version.csv
""")


print('\n== Real listing: Tecno Pouvoir 3 LB7 ==')
f = read_payload(TECNO)
ok(f['platform'] == 'mediatek', 'platform is mediatek', f.get('platform'))
ok(f['soc'] == 'MT6739', 'SoC MT6739 from the scatter', f.get('soc'))
ok(len(f['soc_evidence']) >= 2, 'corroborated by efuse/APDB/MDDB too',
   f.get('soc_evidence'))
ok(f['model_code'] == 'LB7', 'model LB7 from the preloader', f.get('model_code'))
ok(f['board_code'] == 'H393', 'board H393', f.get('board_code'))
ok(f['android_branch'].startswith('Android 8.1'), 'Android 8.1 from alps branch',
   f.get('android_branch'))
ok(f['build_week'] == '2018 week 36', 'build week 2018-36', f.get('build_week'))
ok(f['band_code'] == 'ulwtg', 'band letters ulwtg', f.get('band_code'))
ok(f['full_flash'] is True, 'full flash (userdata + cache present)')
ok(f['package_type'] == 'full flash (wipes user data)', 'package type',
   f.get('package_type'))
ok('vendor_group' not in f,
   'no Transsion maps in this listing, so no vendor group is claimed',
   f.get('vendor_group'))
ok(f['archive_root_name'] == 'Tecno_Pouvoir_3_LB7_MT6739_V213_190425',
   'vendor package name recovered from INSIDE the archive',
   f.get('archive_root_name'))
ok(f['checksum_present'] is True, 'Checksum.ini noted')
ok('boot.img' in [t['file'] for t in f['tier1_targets']],
   'boot.img queued as a Tier 1 target')
ok('Checksum.ini' in [t['file'] for t in f['tier1_targets']],
   'Checksum.ini queued as a Tier 1 target')

print('\n== Real listing: Infinix S4 X626 ==')
f = read_payload(INFINIX)
ok(f['soc'] == 'MT6765', 'SoC MT6765', f.get('soc'))
ok(f['model_code'] == 'X626', 'model X626', f.get('model_code'))
ok(f['board_code'] == 'H623', 'board H623', f.get('board_code'))
ok(f['build_week'] == '2019 week 04', 'build week 2019-04', f.get('build_week'))
ok(f.get('android_branch') is None,
   'APDB has no alps branch here, so nothing is claimed', f.get('android_branch'))
ok(f['android_floor'] == 8, 'structure floor Android 8 (vbmeta + dtbo)',
   f.get('android_floor'))
ok(f['avb_present'] is True, 'AVB present')
# A preloader is NOT a download agent: the DA runs in RAM and is never
# written, the preloader is a partition that IS written. This suite originally
# asserted they were the same thing, which would have told a user the flashing
# tool was covered when nothing in the box covers it.
ok(f['loader_present'] is False,
   'no download agent bundled here -- a preloader is not a DA',
   f.get('loader_files'))
ok(f['preloader_present'] is True, 'the preloader is reported separately')

print('\n== Real listing: Oukitel WP19 Pro ==')
f = read_payload(OUKITEL)
ok(f['soc'] == 'MT6789', 'SoC MT6789 from a .xml scatter', f.get('soc'))
ok(f.get('model_code') is None,
   'preloader_k6789v1_64 is a PLATFORM name, so no model is claimed',
   f.get('model_code'))
ok(f.get('platform_board') == 'K6789V1_64', 'it is recorded as a platform board',
   f.get('platform_board'))
ok(f['dynamic_partitions'] is True, 'super.img -> dynamic partitions')
ok(f['android_floor'] == 11, 'structure floor Android 11 (vendor_boot)',
   f.get('android_floor'))
ok(f['loader_present'] is True, 'download_agent bundled')
ok('download_agent' in f['loader_files'], 'and it is named', f.get('loader_files'))
ok(f.get('archive_root_name') is None,
   'a root segment with no letters or digits is junk, not a package name',
   f.get('archive_root_name'))

print('\n== Real listing: the mislabelled X6896 download ==')
f = read_payload(ROMFILE)
ok(f['soc'] == 'MT6897', 'SoC MT6897 from the scatter', f.get('soc'))
ok(f['companion_chips'] == ['MT6197'],
   'MT6197 is recorded as the MODEM, not a conflict', f.get('companion_chips'))
ok(f.get('soc_conflict') is None,
   'and it is therefore not a conflict', f.get('soc_conflict'))
ok(f['model_code'] == 'X6856', 'model X6856 -- NOT the X6896 on the post',
   f.get('model_code'))
ok(f['board_code'] == 'H971', 'board H971', f.get('board_code'))
ok(f['storage_type'] == 'UFS', 'UFS storage from preloader_ufs.img',
   f.get('storage_type'))
ok(f['android_floor'] == 13, 'structure floor Android 13 (init_boot)',
   f.get('android_floor'))
ok(f['vendor_group'] == 'Transsion', 'Transsion, from its own tr_*.map partitions',
   f.get('vendor_group'))
ok(f['brand_hint'] == 'Infinix', 'X#### -> Infinix within Transsion',
   f.get('brand_hint'))
ok(f['build_week'] == '2025 week 52', 'build week 2025-52', f.get('build_week'))
ok(f['band_code'] == 'unlwctg', 'bands include NR (5G)', f.get('band_code'))
ok('NR (5G)' in f['bands'], 'and that is spelled out', f.get('bands'))
ok(f.get('archive_root_name') == 'rom file - lexzytechinc.com',
   'the root folder is a repacker watermark, reported raw for the stripper',
   f.get('archive_root_name'))
ok('version.csv' in [t['file'] for t in f['tier1_targets']],
   "version.csv queued -- it holds the vendor's own version string")

print('\n== The guard: does the archive contradict the post? ==')
problems = contradicts(f, claimed_model='X6896', claimed_soc='MT6858')
ok(len(problems) == 2, 'both the model and the chipset disagree', problems)
ok(any('X6856' in p for p in problems), 'the model mismatch is named')
ok(any('MT6897' in p for p in problems), 'the chipset mismatch is named')
ok(contradicts(f, claimed_model='X6856', claimed_soc='MT6897') == [],
   'and the CORRECT claim raises nothing')

print('\n== Central-directory metadata is free, so use it ==')
sized = [{'name': 'x/firmware/MT6768_Android_scatter.txt', 'size': 4096, 'date': '2023-04-01'},
         {'name': 'x/firmware/super.img', 'size': 5 * 1024 ** 3, 'date': '2023-04-02'},
         {'name': 'x/firmware/boot.img', 'size': 33554432, 'date': '2023-04-02'}]
f = read_payload(sized)
ok(f['uncompressed_bytes'] == 4096 + 5 * 1024 ** 3 + 33554432,
   'total uncompressed size computed', f.get('uncompressed_bytes'))
ok(f['largest_entry']['name'] == 'super.img', 'largest entry identified',
   f.get('largest_entry'))
ok(f['entry_date_range'] == ['2023-04-01', '2023-04-02'], 'date range',
   f.get('entry_date_range'))

print('\n== Other platforms ==')
f = read_payload(['SC9863A_xyz/firmware.pac', 'SC9863A_xyz/nvitem.bin'])
ok(f['platform'] == 'unisoc', 'a .pac is Unisoc', f.get('platform'))
ok(f['soc'] == 'SC9863A', 'and the part number is read', f.get('soc'))
ok('SPD Research' in f['flash_tool'],
   'the tool is SPD Research, NOT SP Flash Tool', f.get('flash_tool'))

f = read_payload(['rom/rawprogram0.xml', 'rom/patch0.xml',
                  'rom/prog_firehose_ddr.elf', 'rom/boot.img', 'rom/SDM660_x.bin'])
ok(f['platform'] == 'qualcomm', 'rawprogram + firehose is Qualcomm',
   f.get('platform'))
ok(f['flash_tool'] == 'QFIL / QPST', 'the tool is QFIL', f.get('flash_tool'))
ok(f['soc'] == 'SDM660', 'part number read', f.get('soc'))

f = read_payload(['AP_G556BXXS2AXD2.tar.md5', 'BL_G556BXXS2AXD2.tar.md5',
                  'CP_G556BXXS2AXD2.tar.md5', 'CSC_OLM_G556BOLM2AXD2.tar.md5'])
ok(f['container'] == 'samsung_odin', 'Samsung four-file set recognised',
   f.get('container'))
ok(f['region_code'] == 'OLM', 'region OLM from the CSC name', f.get('region_code'))
ok(f['model_code'] == 'G556B', 'model G556B from the part names',
   f.get('model_code'))
ok(f['odin_slots'] == ['AP', 'BL', 'CP', 'CSC'], 'all four slots present',
   f.get('odin_slots'))

print('\n== Things that must NOT be called firmware ==')
f = read_payload(['Kirin-Tool-v2.4.2/Kirin-Tool.exe',
                  'Kirin-Tool-v2.4.2/readme.txt'])
ok(f['is_firmware'] is False, 'a flash TOOL is not firmware', f)
ok(contradicts(f) != [], 'and the guard says so', contradicts(f))

print('\n== Conflicts are flagged, never averaged ==')
f = read_payload(['x/efuse_MT6765.xml', 'x/APDB_MT6789_S01__W1904', 'x/boot.img'])
ok(f.get('soc') is None, 'two part numbers and no scatter -> claim nothing',
   f.get('soc'))
ok(f.get('soc_conflict') == ['MT6765', 'MT6789'], 'the conflict is reported',
   f.get('soc_conflict'))

print('\n== Regressions this project has already paid for ==')
f = read_payload(['x/efuse_MT6765.xml', 'x/MT6765_Android_scatter.txt', 'x/boot.img'])
ok('efuse_MT6765.xml' in f['soc_evidence'],
   'underscore-separated corroboration is seen (\\b would miss it)',
   f.get('soc_evidence'))
f = read_payload(['x/preloader_x6896_p586a.bin', 'x/MT6858_Android_scatter.txt'])
ok(f['board_code'] == 'P586A',
   'a non-H board code is read positionally (/H\\d+/ would have failed)',
   f.get('board_code'))

print('\n%d passed, %d failed\n' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
