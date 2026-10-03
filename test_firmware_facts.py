"""
Gold card details, read from inside the firmware (2026-09-26).

Part 1 needs no files: header and property decoding on bytes built here.
Part 2 runs on the owner's real archives in Downloads/Compressed when they are
present (skipped otherwise), with the values checked by hand against the
files: Oukitel WP19 Pro (vbmeta), Tecno LB7 (old boot header), Samsung
XCover7 (Odin names).
"""

import os
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firmware_facts as ff     # noqa: E402
import payload_reader as pr     # noqa: E402
import hf_build_worker as w     # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def header(hv, a, b, c, y, m):
    osv = (((a << 14) | (b << 7) | c) << 11) | (((y - 2000) << 4) | m)
    h = bytearray(4096)
    h[:8] = b'ANDROID!'
    struct.pack_into('<I', h, 40, hv)
    struct.pack_into('<I', h, 16 if hv >= 3 else 44, osv)
    if hv < 3:
        struct.pack_into('<I', h, 36, 2048)      # page size, where v0 keeps it
    return bytes(h)


print("\n== Boot headers ==")
ok(ff.boot_header(header(0, 8, 1, 0, 2020, 8)) == {'header_version': 0, 'android': '8.1', 'patch': '2020-08'},
   'v0 header: Android 8.1, patch 2020-08')
ok(ff.boot_header(header(4, 12, 0, 0, 2023, 8))['header_version'] == 4, 'v4 header is recognised as v4')
ok(ff.boot_header(b'not a boot image' * 10) is None, 'not a boot image -> None')

print("\n== The rules ==")
def run(files, payload=None):
    return ff.collect(list(files), lambda n, lim: files[n][:lim], payload or {})

props = (b'\x00' * 40 + b'com.android.build.system.os_version\x0013\x00' +
         b'com.android.build.system.security_patch\x002023-12-05\x00')
got = run({'fw/vbmeta_system.img': props, 'fw/boot.img': header(4, 12, 0, 0, 2023, 8)})
ok(got.get('android') == '13' and got.get('security_patch') == '2023-12-05',
   'system label wins over a generic-kernel boot header (13 / 2023-12-05, not 12 / 2023-08)', got)
got = run({'fw/boot.img': header(4, 12, 0, 0, 2023, 8)})
ok('android' not in got and 'security_patch' not in got,
   'a v3+ boot header alone is NOT used (it describes the kernel)', got)
got = run({'fw/boot.img': header(0, 8, 1, 0, 2020, 8)})
ok(got.get('android') == '8.1' and got.get('security_patch') == '2020-08', 'an old (v0) boot header is used', got)
fp = b'com.android.build.product.fingerprint\x00OUKITEL/WP19/WP19:13/TP1A/2024:user/release-keys\x00'
ok(run({'vbmeta.img': fp}).get('android') == '13', 'fingerprint gives the version when no os_version is present')
both = b'<storage>HW_STORAGE_EMMC</storage><storage>HW_STORAGE_UFS</storage>'
ok('storage' not in run({'MT6789_Android_scatter.xml': both}), 'a scatter listing eMMC AND UFS -> storage left out')
ok(run({'MT6739_Android_scatter.txt': b'storage: HW_STORAGE_EMMC'}).get('storage') == 'eMMC', 'one type -> shown')
ok(ff.samsung_bootloader('G556BXXS9BYDB') == '9' and ff.samsung_bootloader('G556BXXSIEZH2') == '18'
   and ff.samsung_bootloader('S948USQU1AZCF') == '1', 'Samsung bootloader: 9, I=18, US build 1')
ok(ff.samsung_bootloader('rom file') == '', 'not a build code -> nothing')

print("\n== Your real archives ==")
D = 'C:/Users/lexzy/Downloads/Compressed'
def real(name):
    path = os.path.join(D, name)
    if not os.path.exists(path):
        print("  skip " + name + " (not on this computer)")
        return None
    z = zipfile.ZipFile(path)
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    payload = pr.read_payload([{'name': i.filename, 'size': i.file_size} for i in z.infolist() if not i.is_dir()])
    return ff.collect(names, lambda n, lim: z.open(n).read(lim), payload)

f = real('Oukitel_WP19_Pro_MT6789_EEA_V06_240117_MXML-001.zip')
if f is not None:
    ok(f.get('android') == '13' and f.get('security_patch') == '2023-12-05' and f.get('chipset') == 'MT6789',
       'Oukitel WP19 Pro: Android 13, patch 2023-12-05, MT6789', f)
    ok('storage' not in f, '  storage left out (scatter supports both eMMC and UFS)')
f = real('LB7-H393DEF-O-200726V2812B28_29 - lexzytechinc.com.zip')
if f is not None:
    ok(f.get('android') == '8.1' and f.get('security_patch') == '2020-08' and f.get('chipset') == 'MT6739'
       and f.get('model') == 'LB7' and f.get('storage') == 'eMMC',
       'Tecno Pouvoir 3 LB7: Android 8.1, patch 2020-08, MT6739, LB7, eMMC', f)
f = real('G556BXXSIEZH2_G556BOLMIEZH2_XXV_16.0 - lexzytechinc.com.zip')
if f is not None:
    ok(f.get('android') == '16' and f.get('model') == 'SM-G556B' and f.get('bootloader') == '18',
       'Samsung XCover7: Android 16, SM-G556B, bootloader 18 (I)', f)
    ok('security_patch' not in f, '  no patch claimed: it is inside a 9 GB AP tar, not read', f)

print("\n== The build sends them ==")
ok(hasattr(w, 'archive_facts'), 'the worker has archive_facts()')

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
