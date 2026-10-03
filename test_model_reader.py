"""
The phone's real model from its own words (model_reader.py, 2026-10-03).
Part 1: the rules, on labels copied from the stored files' audit.
Part 2: the owner's two Spark 9 Pro archives in Downloads/Compressed (KH7n
and KH7S -- both carry preloader_kh7n_h6919.bin), when present.
"""

import gzip
import os
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firmware_facts                  # noqa: E402
import model_reader as mr              # noqa: E402
import name_composer                   # noqa: E402
import payload_reader                  # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


print("\n== vbmeta labels ==")
for fp, want in (
        ("TECNO/KH7S-MXTC/TECNO-KH7S:12/SP1A/221012V288:user/release-keys", "KH7S"),
        ("TECNO/KH7n-OP/TECNO-KH7n:12/SP1A/221022V778:user/release-keys", "KH7n"),
        ("TECNO/LC7S-ZAVC/TECNO-LC7:10/QP1A/C-ZAVC-201124V117:user/release-keys", "LC7S"),
        ("TECNO/KL4h-OP/TECNO-KL4h:14/UP1A/x:user/release-keys", "KL4h"),
        ("Infinix/X6856-OP/Infinix-X6856:15/x/y:user/release-keys", "X6856"),
        ("Itel/P661N-GL/itel-P661N:14/x/y:user/release-keys", "P661N"),
        ("TECNO/LK6/TECNO-LK6:16/x/y:user/release-keys", "LK6"),
        ("TECNO/H571S/TECNO-AX8:7.0/NRD90M/B:user/release-keys", "AX8"),
        ("alps/hal_mgvi_64_nfc_armv82/mgvi_64_nfc_armv82:12/x/y:user/dev-keys", ""),
        ("TECNO/TSSI/FULL-64-ARMV82:14/x/y:user/release-keys", ""),
        ("Xiaomi/lavender/lavender:10/x/y:user/release-keys", ""),
        ("", "")):
    got = mr.from_fingerprint(fp)
    ok(got == want, "%-30s -> %s" % (fp.split(":")[0][:30], want or "(unknown)"), got)

ok(mr.real_model({}, None, {"vendor": "alps/hal_mgvi_64/mgvi_64:12/x", "dtbo": "TECNO/CLA6-OP/TECNO-CLA6:12/x",
                            "system": "TECNO/TSSI/FULL-64:14/x"})[0] == "CLA6",
   "newer Tecno: the generic vendor label is skipped, dtbo names the phone (CLA6)")

print("\n== Phone settings (recovery / build.prop) ==")
for props, want in (
        ({"ro.product.brand": "TECNO", "ro.product.model": "TECNO AX8S", "ro.product.device": "TECNO-AX8"}, "AX8S"),
        ({"ro.product.brand": "TECNO", "ro.product.vendor.model": "TECNO LC7S",
          "ro.product.vendor.device": "TECNO-LC7"}, "LC7S"),
        ({"ro.product.brand": "TECNO", "ro.product.model": "TECNO LA7 Pro"}, "LA7 Pro"),
        ({"ro.product.brand": "Infinix", "ro.product.model": "Infinix NOTE 3 Pro",
          "ro.product.device": "X601-LTE"}, "X601"),
        ({"ro.product.brand": "Infinix", "ro.product.model": "Infinix HOT 4 Pro",
          "ro.product.device": "Infinix-X5511-LTE"}, "X5511"),
        ({"ro.product.model": "Infinix X572"}, "X572"),
        ({"ro.product.brand": "Xiaomi", "ro.product.model": "Redmi Note 7", "ro.product.device": "lavender"}, ""),
        ({}, "")):
    ok(mr.from_props(props)[0] == want, "%-24s -> %s" % (props.get("ro.product.model") or
                                                        props.get("ro.product.vendor.model") or "(none)",
                                                        want or "(unknown)"), mr.from_props(props))


def cpio(files):
    out = b""
    for i, (name, body) in enumerate(list(files.items()) + [("TRAILER!!!", b"")]):
        n = name.encode() + bytes(1)
        out += (b"070701" + b"%08X" % (i + 1) + b"%08X" % 0o100644 + b"0" * 32 + b"%08X" % len(body)
                + b"0" * 32 + b"%08X" % len(n) + b"0" * 8) + n
        out += bytes(-len(out) % 4) + body
        out += bytes(-len(out) % 4)
    return out


def recovery(props, mtk=False):
    rd = gzip.compress(cpio({"prop.default": props.encode()}))
    if mtk:
        rd = b"\x88\x16\x88\x58" + bytes(508) + rd
    h = bytearray(2048)
    h[:8] = b"ANDROID!"
    struct.pack_into("<III", h, 8, 3000, 0, len(rd))
    struct.pack_into("<I", h, 36, 2048)
    return bytes(h) + os.urandom(4096) + rd


img = recovery("ro.product.brand=TECNO\nro.product.model=TECNO LB7\n", mtk=True)
m, src = mr.from_recovery({"recovery.img": "fw/recovery.img"}, lambda n, lim: img[:lim])
ok(m == "LB7" and "recovery.img" in src, "recovery.img (MediaTek ramdisk header) -> LB7", (m, src))
ok(mr.from_recovery({"recovery.img": "r"}, lambda n, lim: b"junk")[0] == "", "a broken recovery gives nothing")

print("\n== The preloader name is never the model ==")
names = ["fw/preloader_kh7n_h6919.bin", "fw/MT6768_Android_scatter.txt", "fw/boot.img"]
payload = payload_reader.read_payload([{"name": n, "size": 1000} for n in names])
facts = firmware_facts.collect(names, lambda n, lim: b"", payload)
ok(payload.get("model_code") == "KH7N" and "model" not in facts,
   "preloader says KH7N, nothing else readable: the card gets NO model (unknown, not KH7N)", facts)
meta = name_composer.naming_meta({"model": {"value": "KH7S", "source": "ai"}}, {"model_code": "KH7S"})
ok("_contradiction" not in meta, "the real model KH7S on the KH7S post: no contradiction")

print("\n== Your two Spark 9 Pro archives ==")
D = "C:/Users/lexzy/Downloads/Compressed"
for fn, want in (("[Hovatek]_Tecno_Spark_9_Pro_(KH7S-H6919F-S-MXTC-221012V288).zip", "KH7S"),
                 ("[Hovatek]_Tecno_Spark_9_Pro_(KH7n-H6919D-S-RU-221022V1024).zip", "KH7n")):
    path = os.path.join(D, fn)
    if not os.path.exists(path):
        print("  skip " + fn + " (not on this machine)")
        continue
    z = zipfile.ZipFile(path)
    nm = z.namelist()
    pl = payload_reader.read_payload([{"name": i.filename, "size": i.file_size} for i in z.infolist()])

    def rd(n, lim):
        with z.open(n) as fh:
            return fh.read(lim)
    f = firmware_facts.collect(nm, rd, pl, opener=z.open)
    ok(f.get("model") == want and "vbmeta" in f.get("model_source", ""),
       "%s: model %s from the vbmeta label (preloader file says kh7n)" % (want, want), f.get("model"))

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
