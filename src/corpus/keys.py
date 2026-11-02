"""Citation keys: one spelling everywhere, and the --key/--keys-file/--all options.

Keys are NFC. macOS hands back file names in NFD (a key with an accented
letter, read from the disk, is a different string from the manifest's key) and APFS hides
the difference when opening files, Linux does not. Every key entering a
script, from argv, a keys file or a file name, goes through `nfc()`.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

# A key is one path segment: no separators, not "." or "..", no leading dot.
_BAD = re.compile(r"[/\\\x00]")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def key_from_path(path: Path) -> str:
    """The citation key a file stands for: its NFC stem."""
    return nfc(Path(path).stem)


def validate(key: str) -> str:
    """The key, NFC, if it is safe to use as a file name; else ValueError."""
    key = nfc(key.strip())
    if not key or key in (".", "..") or key.startswith(".") or _BAD.search(key):
        raise ValueError(f"not a valid citation key: {key!r}")
    return key


def read_keys_file(path: Path) -> list[str]:
    """Keys from a file, one per line; blank lines and # comments (also
    indented ones, and trailing ones after a key) are ignored."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(validate(line))
    return out


def add_key_args(ap: argparse.ArgumentParser, *, keys_file: bool = True,
                 all_: bool = False, all_help: str = "every eligible key") -> None:
    """--key (repeatable), --keys-file and optionally --all."""
    ap.add_argument("--key", action="append", default=[], type=key_arg,
                    help="citation key (repeatable)")
    if keys_file:
        ap.add_argument("--keys-file", help="file with one key per line (# comments allowed)")
    if all_:
        ap.add_argument("--all", action="store_true", help=all_help)


def resolve_keys(args: argparse.Namespace) -> list[str]:
    """--key values then --keys-file lines, NFC, validated, duplicates dropped."""
    keys = [validate(k) for k in (getattr(args, "key", None) or [])]
    if getattr(args, "keys_file", None):
        keys += read_keys_file(args.keys_file)
    return list(dict.fromkeys(keys))


def keys_in(folder: Path, suffix: str) -> list[str]:
    """Sorted NFC keys of the files `*{suffix}` in folder."""
    return sorted({key_from_path(p) for p in Path(folder).glob(f"*{suffix}")})


def key_arg(value: str) -> str:
    """argparse `type=` for citation keys: NFC and validated."""
    try:
        return validate(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
