"""
Stored names: the vendor's string, unchanged, plus detail -- never a rename.

Cases are the real archives tested on 2026-09-24 and the live "rom file"
upload, with the file lists those archives actually contain.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import name_composer as nc      # noqa: E402
import payload_reader as pr     # noqa: E402

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("\n       " + str(detail) if detail else ""))


def name(outer, root="", device=None, entries=None):
    facts = pr.read_payload(entries) if entries else None
    vendor = nc.vendor_base(outer, root)
    got, _ = nc.stored_base_name(vendor, nc.parse_device_info(json.dumps(device or {})), facts)
    return got, vendor


MTK = ["pkg/MT6739_Android_scatter.txt", "pkg/preloader_lb7_h393.bin", "pkg/boot.img", "pkg/system.img"]
PAC = ["Itel_P40_P662L_V261_230118_12_SPD/Firmware/P662L-SE668S-12.0-GL-V261-20230118.pac",
       "Itel_P40_P662L_V261_230118_12_SPD/Credits.txt"]

print("\n== Google's -001 is removed only when the archive confirms it ==")
got, vendor = name("Itel_P40_P662L_V261_230118_12_SPD-001", "Itel_P40_P662L_V261_230118_12_SPD")
ok(vendor == "Itel_P40_P662L_V261_230118_12_SPD", "-001 dropped: the zip's own folder has the same name", vendor)
got, vendor = name("P661N_MT6833_GL_V124_240911-001", "P661N_MT6833_GL_V124_240911_other")
ok(vendor == "P661N_MT6833_GL_V124_240911-001", "kept when the folder does not confirm it", vendor)
got, vendor = name("Tecno_Pouvoir_3_LB7_MT6739_V213_190425", "")
ok(vendor == "Tecno_Pouvoir_3_LB7_MT6739_V213_190425", "a name without the suffix is untouched")

print("\n== Detail is added around the vendor string, never inside it ==")
cases = [
    ("Tecno_Pouvoir_3_LB7_MT6739_V213_190425", {"brand": "Tecno", "series": "Pouvoir 3", "model": "LB7"}, MTK,
     "Tecno_Pouvoir_3_LB7_MT6739_V213_190425"),
    ("LB7-H393DEF-O-200726V2812B28_29", {"brand": "Tecno", "series": "Pouvoir 3", "model": "LB7"}, MTK,
     "Tecno_Pouvoir_3_LB7-H393DEF-O-200726V2812B28_29_MT6739"),
    ("X626_MT6765_V239_190417", {"brand": "Infinix", "series": "S4", "model": "X626"}, None,
     "Infinix_S4_X626_MT6765_V239_190417"),
]
for vendor_in, dev, entries, want in cases:
    got, vendor = name(vendor_in, "", dev, entries)
    ok(got == want, "%s -> %s" % (vendor_in, want), got)
    ok(vendor_in in got, "  the vendor string is still in it, byte for byte")

got, _ = name("Itel_P40_P662L_V261_230118_12_SPD", "", {"brand": "Itel", "series": "P40", "model": "P662L"}, PAC)
ok(got.startswith("Itel_P40_P662L_V261_230118_12_SPD") and got.endswith("_UNISOC"),
   "a .pac package gets the _UNISOC family tag", got)

print("\n== Samsung: the build code comes from the Odin files inside ==")
S9 = ["G556BXXS9.0 - lexzytechinc.com/firmware/CSC_OLM_G556BOLM9BYD9_QB95273766_REV00_user_low_ship_MULTI_CERT.tar.md5",
      "G556BXXS9.0 - lexzytechinc.com/firmware/AP_G556BXXS9BYDB_G556BXXS9BYDB_MQB95492202_REV00_user_low_ship_MULTI_CERT_meta_OS14.tar.md5",
      "G556BXXS9.0 - lexzytechinc.com/firmware/BL_G556BXXS9BYDB_G556BXXS9BYDB_MQB95492202_REV00_user_low_ship_MULTI_CERT.tar.md5",
      "G556BXXS9.0 - lexzytechinc.com/firmware/CP_G556BXXS9BYD9_CP29897048_MQB95237634_REV00_user_low_ship_MULTI_CERT.tar.md5",
      "G556BXXS9.0 - lexzytechinc.com/firmware/HOME_CSC_OLM_G556BOLM9BYD9_QB95273766_REV00_user_low_ship_MULTI_CERT.tar.md5"]
IEZH2 = ["G556BXXSIEZH2_G556BOLMIEZH2_XXV_16.0 - lexzytechinc.com/firmware/AP_G556BXXSIEZH2_G556BXXSIEZH2_MQB113006374_REV00_user_low_ship_MULTI_CERT_meta_OS16.tar.md5",
         "G556BXXSIEZH2_G556BOLMIEZH2_XXV_16.0 - lexzytechinc.com/firmware/CSC_OLM_G556BOLMIEZH2_MQB113006374_REV00_user_low_ship_MULTI_CERT.tar.md5"]
XC7 = {"brand": "Samsung", "series": "Galaxy XCover7", "model": "SM-G556B"}
ok(nc.samsung_build_code(S9) == "G556BXXS9BYDB_G556BOLM9BYD9", "AP + CSC build codes read from the file names",
   nc.samsung_build_code(S9))
facts = pr.read_payload(S9)
vendor = nc.vendor_base("G556BXXS9.0", "G556BXXS9.0", nc.samsung_build_code(S9))
got, _ = nc.stored_base_name(vendor, nc.parse_device_info(json.dumps(XC7)), facts)
ok(got == "Samsung_Galaxy_XCover7_SM-G556B_G556BXXS9BYDB_G556BOLM9BYD9",
   'shortened "G556BXXS9.0" -> the real build, G556BXXS9BYDB_G556BOLM9BYD9', got)
facts = pr.read_payload(IEZH2)
vendor = nc.vendor_base("G556BXXSIEZH2_G556BOLMIEZH2_XXV_16.0", "", nc.samsung_build_code(IEZH2))
got, _ = nc.stored_base_name(vendor, nc.parse_device_info(json.dumps(XC7)), facts)
ok(got == "Samsung_Galaxy_XCover7_SM-G556B_G556BXXSIEZH2_G556BOLMIEZH2_XXV_16.0",
   "a name that already carries the full build code is kept as it is", got)
ok(nc.samsung_build_code(["Firmware/boot.img", "CSC_OLM_X_1.tar.md5"]) == "",
   "no AP file -> no Samsung code, the outer name is used as before")

print("\n== _SPD_UNISOC is kept (owner's decision) ==")
got, _ = name("Itel_P40_P662L_V261_230118_12_SPD-001", "Itel_P40_P662L_V261_230118_12_SPD",
              {"brand": "Itel", "series": "P40", "model": "P662L"}, PAC)
ok(got == "Itel_P40_P662L_V261_230118_12_SPD_UNISOC", "Itel P40 -> Itel_P40_P662L_V261_230118_12_SPD_UNISOC", got)

print("\n== An unconfirmed model never enters a name ==")
got, _ = name("H393DEF-O-200726V2812B28_29", "", {"brand": "Tecno", "series": "Pouvoir 3", "model": "LB8"}, None)
ok("LB8" not in got, "the AI's model with nothing proving it is left out", got)
got, _ = name("H393DEF-O-200726V2812B28_29", "", {"brand": "Tecno", "model": "LB7"}, MTK)
ok(got.startswith("Tecno_LB7_"), "the same model IS added once the archive proves it (preloader_lb7)", got)

print("\n== A name that says nothing is rebuilt from what is inside ==")
ROM = ["rom file - lexzytechinc.com/firmware/preloader_x6856_h971.bin",
       "rom file - lexzytechinc.com/firmware/MT6897_Android_scatter.txt",
       "rom file - lexzytechinc.com/firmware/tr_region.map",
       "rom file - lexzytechinc.com/firmware/super.img"]
got, vendor = name("rom file", "rom file", {"brand": "Infinix", "series": "Hot 50 Pro+", "model": "X6896"}, ROM)
ok(vendor == "" and got == "Infinix_X6856_H971_MT6897",
   '"rom file" on the X6896 post -> Infinix_X6856_H971_MT6897 (the phone it really is)', got)
ok("Hot_50" not in got, "  and the post's series is NOT put on another phone's file")
facts = pr.read_payload(ROM)
_, dec = nc.stored_base_name("", nc.parse_device_info('{"model":"X6896"}'), facts)
ok("contradiction" in dec and "X6856" in dec["contradiction"]["why"], "  the mismatch is reported for the build log")
got, vendor = name("Firmware", "", {}, None)
ok(got == "" and vendor == "", "nothing known at all -> empty, and the worker falls back to the file id")

print("\n== A brand guess that is not ONE brand is never used ==")
P661N = ["P661N_MT6833_GL_V124_240911/preloader_p661n_h6121.bin",
         "P661N_MT6833_GL_V124_240911/MT6833_Android_scatter.txt",
         "P661N_MT6833_GL_V124_240911/tr_region.map"]
got, vendor = name("P661N_MT6833_GL_V124_240911-001", "P661N_MT6833_GL_V124_240911", {}, P661N)
ok(got == "P661N_MT6833_GL_V124_240911" and "or" not in got.split("_"),
   'P661N with no post details -> P661N_MT6833_GL_V124_240911 (was: Tecno_or_itel_P661N_...)', got)
got, _ = name("rom file", "rom file", {}, ROM)
ok(got.startswith("Infinix_X6856"), "a single-brand guess (Infinix, from an X#### code) is still used", got)

print("\n== Transsion project names are the same phone (first live build, 2026-09-24) ==")
KL4 = ["KL4h-XE679C-UGo-IN-241021V1772/MT6765_Android_scatter.txt",
       "KL4h-XE679C-UGo-IN-241021V1772/preloader_kl4ha32_h6127.bin"]
got, _ = name("KL4h-XE679C-UGo-IN-241021V1772 Factory Signed Firmware", "", {}, KL4)
ok(got == "KL4h-XE679C-UGo-IN-241021V1772 Factory Signed Firmware_MT6765",
   "project KL4HA32 in a KL4h name -> nothing added in front (was: KL4HA32_KL4h-...)", got)
facts = pr.read_payload(KL4)
_, dec = nc.stored_base_name("KL4h-XE679C-UGo-IN-241021V1772", nc.parse_device_info('{"brand":"Tecno","series":"Spark 30C","model":"KL4h"}'), facts)
ok("contradiction" not in dec, "a post saying KL4h is NOT flagged as a different phone", dec.get("contradiction"))
ok(nc.same_model("KL4h", "KL4HA32") and nc.same_model("LB7", "LB7") and not nc.same_model("LC7", "LC7S")
   and not nc.same_model("X6896", "X6856") and not nc.same_model("X68", "X6856"),
   "same phone: KL4h/KL4HA32; different: LC7/LC7S, X6896/X6856")
_, dec = nc.stored_base_name("", nc.parse_device_info('{"model":"X6896"}'), pr.read_payload(ROM))
ok("contradiction" in dec, "the real wrong-phone case (X6856 on the X6896 post) is still caught")

print("\n== Bad input never breaks a build ==")
for raw in ("", "not json", "[1,2]", '{"brand": null, "model": "N/A"}'):
    ok(nc.parse_device_info(raw) == {}, "device_info %r -> ignored" % raw)

print("\n== Names identity keys can use ==")
for base, want in [("rom file", False), ("Stock ROM", False), ("Firmware", False), ("-FRP-File_RMM", False),
                   ("G556BXXS9.0", True), ("Tecno Pouvoir 1 LA6 MT6580 7.0 Dead Recovery", True)]:
    ok(nc.carries_identity(base) is want, "%r carries identity: %s" % (base, want))

print("\n%d passed, %d failed\n" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
