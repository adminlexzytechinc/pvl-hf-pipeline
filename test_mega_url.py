from mega_url import is_mega_url, parse_mega_url, mega_file_id

fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got}\n        want: {want}")
        fails.append(label)

# The exact link from the conversation
url = "https://mega.nz/file/d6IXQCab#4ZAiyTb9kW09F-961OHJtOtt_2dbN4BYKq3kajqZ0fY"
check("is_mega_url on the pasted link", is_mega_url(url), True)
check("parse: handle", parse_mega_url(url)['handle'], "d6IXQCab")
check("parse: key", parse_mega_url(url)['key'], "4ZAiyTb9kW09F-961OHJtOtt_2dbN4BYKq3kajqZ0fY")
check("parse: kind", parse_mega_url(url)['kind'], "file")
check("file_id", mega_file_id(url), "mega_d6IXQCab")
check("file_id length under VARCHAR(64)", len(mega_file_id(url)) <= 64, True)

# Folder link, new format
folder_url = "https://mega.nz/folder/AbCdEfGh#XyZ12345-_abcdefghijklmnop"
check("folder: is_mega_url", is_mega_url(folder_url), True)
check("folder: kind", parse_mega_url(folder_url)['kind'], "folder")
check("folder: handle", parse_mega_url(folder_url)['handle'], "AbCdEfGh")

# Legacy hash-style links
legacy_file = "https://mega.nz/#!aBcD1234!someKeyWithUnderscore_and-dash"
check("legacy file: kind", parse_mega_url(legacy_file)['kind'], "file")
check("legacy file: handle", parse_mega_url(legacy_file)['handle'], "aBcD1234")

legacy_folder = "https://mega.nz/#F!wXyZ9876!anotherKey123"
check("legacy folder: kind", parse_mega_url(legacy_folder)['kind'], "folder")

# mega.io alias
io_url = "https://mega.io/file/qRsTuVwX#keyGoesHereABC123"
check("mega.io host accepted", is_mega_url(io_url), True)

# Non-mega URLs must not match
check("gdrive url rejected", parse_mega_url("https://drive.google.com/file/d/abc123/view"), None)
check("mediafire url rejected", parse_mega_url("https://www.mediafire.com/file/xyz/f.zip"), None)
check("empty string", parse_mega_url(""), None)
check("None-ish", parse_mega_url(None), None)

# Link with no key fragment -- must not silently "succeed"
no_key = "https://mega.nz/file/d6IXQCab"
check("no-key link rejected", parse_mega_url(no_key), None)

# Trailing junk (utm params etc. after the key are rare on mega but let's not choke)
url_trailing = url + "?utm_source=telegram"
check("trailing query string tolerated", parse_mega_url(url_trailing)['handle'], "d6IXQCab")

print("\n" + ("All tests passed." if not fails else f"{len(fails)} FAILURES: {fails}"))
