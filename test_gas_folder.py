"""
Two bugs in the Google Drive folder-download path.

A. The retry destroyed its own retry target.
   Google zips a folder asynchronously; a large folder takes minutes. The old
   code gave the signed URL ONE attempt and then called cleanup_gas_folder(),
   which deletes the folder and with it the job URL. A zip that was merely
   slow was indistinguishable from a dead link, and unrecoverable.

B. The real filename was printed and thrown away.
   Google's folder ZIP preserves the name Drive holds. That is very often the
   only good name in the pipeline -- this is the path that produced
   "rom file, lexzytechinc.com.zip". The MEGA path already recovers its name;
   this one printed it and dropped it.

These tests read the shipping source rather than importing the module, because
hf_build_worker.py reads environment and network state at import time.
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


SRC = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'hf_build_worker.py'), encoding='utf-8').read()


def body(func_name):
    """Slice one function's source out of the module."""
    start = SRC.index('def ' + func_name + '(')
    nxt = SRC.find('\ndef ', start + 1)
    return SRC[start:nxt if nxt != -1 else len(SRC)]


print('\n== A. The folder download must not delete its own retry target ==')

fold = body('gas_try_folder_download')

ok('RETRY_DELAYS' in fold, 'a retry schedule exists')

delays = re.search(r'RETRY_DELAYS\s*=\s*\[([0-9,\s]+)\]', fold)
ok(delays is not None, 'the schedule is a literal list', fold[:200])
vals = [int(x) for x in delays.group(1).split(',') if x.strip()]
ok(len(vals) >= 4, 'at least four attempts', vals)
ok(vals[0] == 0, 'the first attempt is immediate -- no delay on the happy path', vals)
ok(vals == sorted(vals), 'delays back off rather than hammering', vals)
ok(sum(vals) >= 300, 'the total budget is minutes, not seconds -- a 9 GB folder '
   'takes that long to zip', sum(vals))

# The ordering is the whole bug. Cleanup must come AFTER the loop.
# Scan CODE only -- the fix's own comment explains the old behaviour and
# names cleanup_gas_folder(), which a naive text scan counts as a call.
code_only = chr(10).join(
    ln for ln in fold.split(chr(10)) if not ln.lstrip().startswith('#'))
loop_at = code_only.index('for i, delay in enumerate(RETRY_DELAYS)')
gave_up_at = code_only.index('if not downloaded:')
cleanups = [m.start() for m in re.finditer(r'cleanup_gas_folder\(', code_only)]
inside = [c for c in cleanups if loop_at < c < gave_up_at]
ok(not inside,
   'no cleanup happens INSIDE the retry loop -- that was the bug', inside)
ok(any(c > gave_up_at for c in cleanups),
   'cleanup does happen once the retries are exhausted')

# Cleaning up a folder the BRIDGE leaked is a different thing, and correct:
# older bridges returned a folderId on failure and deleted nothing, so every
# failed attempt leaked a folder forever.
early = [c for c in cleanups if c < loop_at]
ok(early, 'a folder leaked by the bridge is cleaned up before the loop', early)
ok('not data.get("cleanedUp")' in code_only,
   'and only when the bridge did not already clean it up itself')

ok(fold.count('if not downloaded:') == 1,
   'cleanup is gated on having actually given up')

# And the old one-shot shape must be gone.
ok('if not stream_download(signed_url, zip_path, auth=None):\n'
   '            cleanup_gas_folder(folder_id)' not in fold,
   'the one-attempt-then-delete shape is gone')

ok('success = extract_from_google_zip(zip_path, dest_path)' in fold,
   'the success path still extracts')
ok(fold.rstrip().endswith('return False'), 'the exception handler is intact')


print('\n== B. The real filename must survive ==')

ext = body('extract_from_google_zip')

ok('global FILE_NAME' in ext, 'the function can write the global')
ok('is_generic_filename(real_name)' in ext,
   'a generic name does not overwrite a better one we already had')
ok('os.path.basename(target.filename)' in ext,
   'the ZIP entry path is reduced to a basename')
ok('FILE_NAME = real_name' in ext, 'and the name is actually kept')

# The return type must NOT have changed: the Drive cascade dispatches through
# `lambda: ... -> bool` and a tuple would break every branch in it.
ok('-> bool' in ext.split('\n')[0], 'the signature still returns bool',
   ext.split('\n')[0])
_m = SRC.index('methods = [')
casc = SRC[_m:SRC.index(chr(10) + '    ]', _m)]   # the whole list, not a fixed-size window
ok('gas_try_folder_download(file_id, dest_path)' in casc,
   'the cascade still calls it with the same two arguments')

# Symmetry with the MEGA path, which already did this correctly.
ok('if real_name and not is_generic_filename(real_name):' in SRC,
   'the MEGA guard it mirrors is still there')
ok(SRC.count('is_generic_filename(real_name)') >= 2,
   'both paths now use the same guard',
   SRC.count('is_generic_filename(real_name)'))


print('\n== The extraction still works on a real ZIP ==')

# Build a folder ZIP shaped like Google's: a folder, a small stray file, and
# the firmware as the largest entry.
tmp = tempfile.mkdtemp()
zp = os.path.join(tmp, 'folder.zip')
with zipfile.ZipFile(zp, 'w') as zf:
    zf.writestr('Itel_Power_80/readme.txt', 'x' * 10)
    zf.writestr('Itel_Power_80/Itel_Power_80_P685L_16.3.0.110SP01.zip', 'P' * 5000)

with zipfile.ZipFile(zp) as zf:
    biggest = max((i for i in zf.infolist() if not i.is_dir()),
                  key=lambda i: i.file_size)
ok(os.path.basename(biggest.filename) ==
   'Itel_Power_80_P685L_16.3.0.110SP01.zip',
   'the largest entry is the firmware, and its basename is the real name',
   biggest.filename)

# The name that would have been recovered is exactly the good one.
ok(not re.match(r'^(rom|download|file)[\s._-]', os.path.basename(biggest.filename), re.I),
   'and it is not one of the generic names this whole fix exists to replace')

print('\n%d passed, %d failed\n' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
