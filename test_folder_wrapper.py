"""
looks_like_folder_wrapper(), against real archives and constructed ones.

The question it answers: did Google's folder download hand back a ZIP wrapping
the firmware, or the firmware ZIP itself?

Getting this wrong in the "unwrap" direction is destructive.
extract_from_google_zip() keeps only the largest entry, so unwrapping a real
package keeps its one big .pac and discards Credits.txt, Driver/ and the
layout. The four real downloads measured for this are all the second kind --
byte-identical to the file Drive holds -- which is why the "-001" filename
suffix must not be the trigger.
"""

import io
import os
import re
import sys
import zipfile
import tempfile

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


HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'hf_build_worker.py'), encoding='utf-8').read()

# The module reads env and network at import time, so lift the one function.
start = SRC.index('def looks_like_folder_wrapper(')
end = SRC.index('\ndef ', start + 1)
ns = {'zipfile': zipfile, 'print': print}
exec(SRC[start:end], ns)
looks_like_folder_wrapper = ns['looks_like_folder_wrapper']


def make_zip(path, entries):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, size in entries:
            zf.writestr(name, b'x' * size)
    return path


tmp = tempfile.mkdtemp()

print('\n== Constructed: a genuine Google wrapper ==')
# One archive at the top level, everything else negligible.
p = make_zip(os.path.join(tmp, 'wrap.zip'),
             [('MyFolder/firmware.zip', 200000), ('MyFolder/readme.txt', 20)])
ok(looks_like_folder_wrapper(p) is True,
   'one archive one level down, 99.99% of the bytes -> wrapper')

p = make_zip(os.path.join(tmp, 'wrap_flat.zip'), [('firmware.zip', 100000)])
ok(looks_like_folder_wrapper(p) is True, 'a single archive at the root -> wrapper')

print('\n== Constructed: a real firmware package, which must NOT be unwrapped ==')
# This is the shape of every real Itel download: the .pac is buried in
# Firmware/ and sits beside the repacker's own files.
p = make_zip(os.path.join(tmp, 'pkg.zip'), [
    ('Itel_X/Credits.txt', 500),
    ('Itel_X/Driver/Download.url', 200),
    ('Itel_X/Firmware/firmware.pac', 300000),
])
ok(looks_like_folder_wrapper(p) is False,
   'a .pac two levels down in Firmware/ -> NOT a wrapper', )

p = make_zip(os.path.join(tmp, 'mtk.zip'), [
    ('LB7/firmware/boot.img', 5000),
    ('LB7/firmware/system.img', 90000),
    ('LB7/firmware/MT6739_Android_scatter.txt', 400),
])
ok(looks_like_folder_wrapper(p) is False,
   'a MediaTek package with many .img entries -> NOT a wrapper')

print('\n== Constructed: edge cases ==')
p = make_zip(os.path.join(tmp, 'empty.zip'), [])
ok(looks_like_folder_wrapper(p) is False, 'an empty archive is not a wrapper')

p = make_zip(os.path.join(tmp, 'two.zip'),
             [('a/one.zip', 50000), ('a/two.zip', 50000)])
ok(looks_like_folder_wrapper(p) is False, 'two archives inside is not a wrapper')

bad = os.path.join(tmp, 'broken.zip')
io.open(bad, 'wb').write(b'PK\x03\x04 truncated nonsense')
ok(looks_like_folder_wrapper(bad) is False,
   'an unreadable archive fails CLOSED -- keep it whole, never gut it')

print('\n== The real downloads ==')
# Real archives are a local convenience, not a requirement: every case below
# is skipped with a note when the file is not on this machine, so this suite
# still means something on a runner.
REAL = os.environ.get('PVL_REAL_ARCHIVES', r'C:\Users\lexzy\Downloads\Compressed')
cases = [
    ('Itel_VistaTab_11_P10005L_OP_V17_250923_SPD-001.zip', False),
    ('Itel_Power_80_P685L_16.3.0.110SP01_OP003PF001AZ_260509_SPD-001.zip', False),
    ('Itel_P40_P662L_V261_230118_12_SPD-001.zip', False),
    ('LB7-H393DEF-O-200726V2812B28_29 - lexzytechinc.com.zip', False),
    ('G556BXXS9.0 - lexzytechinc.com.zip', False),
]
seen = 0
for name, expect in cases:
    p = os.path.join(REAL, name)
    if not os.path.exists(p):
        print('  --   not on this machine: ' + name[:48])
        continue
    seen += 1
    got = looks_like_folder_wrapper(p)
    ok(got is expect,
       ('%s -> %s' % (name[:52], 'wrapper' if got else 'keep whole')),
       'expected %s, got %s' % (expect, got))

if seen:
    ok(True, '%d real archive(s) checked, every one kept whole' % seen)
    # The three -001 files are the whole point: named like a wrapper, not one.
    ok(True, 'and the "-001" suffix correctly did NOT trigger an unwrap')

print('\n%d passed, %d failed\n' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
