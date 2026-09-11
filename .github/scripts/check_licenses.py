#!/usr/bin/env python3
"""Fail CI if a bundled Python dependency is not permissively licensed.

Allowed: MIT / BSD / Apache-2.0 / ISC / MPL-2.0 / PSF / 0BSD / Zlib / CC0 / Unlicense.
Reviewed exception: LGPL-3.0 for `psycopg`/`psycopg-binary`. The Postgres driver is
dynamically imported and user-replaceable, so its weak copyleft does not propagate to
GaleQEA; it also lives only in the optional `postgres` extra. Forbidden outright:
AGPL / SSPL / BSL / GPL-2 / GPL-3.
"""
import csv
import sys

ALLOWED_SUBSTRINGS = [
    "mit", "bsd", "apache", "isc", "mozilla public license 2", "mpl 2", "mpl-2",
    "psf", "python software foundation", "0bsd", "zlib", "cc0", "unlicense",
    "public domain", "blue oak", "historical permission", "zope",
]
EXCEPTIONS = {  # package name -> reason (must be a weak/dynamic-link licence)
    "psycopg": "LGPL-3.0, dynamically imported Postgres driver (optional extra)",
    "psycopg-binary": "LGPL-3.0, dynamically imported Postgres driver (optional extra)",
}
FORBIDDEN = ["agpl", "sspl", "server side public", "business source",
             "gpl-2", "gplv2", "gpl-3", "gplv3", "gnu general public"]


def permissive(lic: str) -> bool:
    low = lic.lower()
    if any(f in low for f in FORBIDDEN) and "lesser" not in low and "lgpl" not in low:
        return False
    return any(a in low for a in ALLOWED_SUBSTRINGS)


def main(path: str) -> int:
    bad = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            name, lic = row.get("Name", ""), row.get("License", "")
            if name in EXCEPTIONS:
                print(f"  exception: {name} ({lic}). {EXCEPTIONS[name]}")
                continue
            if not permissive(lic):
                bad.append((name, lic))
    if bad:
        print("\nNON-PERMISSIVE dependencies (not on the allowlist):", file=sys.stderr)
        for name, lic in bad:
            print(f"  {name}: {lic}", file=sys.stderr)
        return 1
    print("\nAll bundled Python dependencies are permissively licensed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "licenses.csv"))
