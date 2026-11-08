#!/usr/bin/env python3
"""Check, list and pack the hit sounds.

    python3 scripts/hitsounds.py check            # validate every file in hitsounds/
    python3 scripts/hitsounds.py readme [--check] # update (or verify) the sound list in the READMEs
    python3 scripts/hitsounds.py build [--out D]  # hitsounds.zip + sha256sums.txt, ready to release
    python3 scripts/hitsounds.py notes [--base T] # release notes against an earlier tag

Standard library only, so it runs the same on a laptop and in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUNDS = ROOT / "hitsounds"
EXT = ".vsnd_c"
READMES = ["README.md", "README_ru.md", "README_zh.md"]
COLUMNS = 4
WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sound_files() -> list[Path]:
    return sorted(SOUNDS.glob(f"*{EXT}"), key=lambda p: p.name.lower())


def sound_names() -> list[str]:
    return [p.name[: -len(EXT)] for p in sound_files()]


# --------------------------------------------------------------------------- check


def check_file(path: Path) -> str | None:
    """A compiled Source 2 resource: header version 12, then a block table.

    The size in the header covers the header and its blocks; a sound's audio
    stream follows them, so the file is at least that long.
    """
    data = path.read_bytes()
    if len(data) < 16:
        return "too small to be a compiled sound"
    size, header_version, _, block_offset, block_count = struct.unpack_from("<IHHII", data)
    if header_version != 12:
        return "not a compiled Source 2 resource (open it in Workshop Tools first)"
    if size > len(data) or not block_count or 8 + block_offset + 12 * block_count > len(data):
        return f"damaged or cut short ({len(data)} bytes)"
    return None


def cmd_check(_: argparse.Namespace) -> int:
    errors: list[str] = []
    if not SOUNDS.is_dir():
        print(f"error: {SOUNDS.relative_to(ROOT)}/ is missing", file=sys.stderr)
        return 1

    seen: dict[str, str] = {}
    for path in sorted(SOUNDS.iterdir()):
        rel = path.relative_to(ROOT)
        if path.name.startswith("."):
            continue
        if path.is_dir():
            errors.append(f"{rel}: folders are not read by the game, keep sounds flat")
            continue
        if path.suffix.lower() in {".wav", ".mp3", ".ogg", ".flac"}:
            errors.append(f"{rel}: raw audio, compile it to {EXT} with Workshop Tools (see the README)")
            continue
        if not path.name.endswith(EXT):
            errors.append(f"{rel}: only {EXT} files belong here")
            continue
        name = path.name[: -len(EXT)]
        if not name.strip() or name != name.strip() or WINDOWS_BAD.search(name):
            errors.append(f"{rel}: the name is not a valid Windows file name")
        if (key := path.name.lower()) in seen:
            errors.append(f"{rel}: clashes with {seen[key]} on Windows (same name, different case)")
        seen[key] = path.name
        if problem := check_file(path):
            errors.append(f"{rel}: {problem}")

    for path in ROOT.rglob(f"*{EXT}"):
        if ".git" in path.parts or path.parent == SOUNDS:
            continue
        errors.append(f"{path.relative_to(ROOT)}: sounds go in hitsounds/")

    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 1
    print(f"ok: {len(seen)} sounds")
    return 0


# --------------------------------------------------------------------------- readme

BEGIN, END = "<!-- sounds:begin -->", "<!-- sounds:end -->"


def sound_table(names: list[str]) -> str:
    rows = []
    for i in range(0, len(names), COLUMNS):
        chunk = names[i : i + COLUMNS]
        chunk += [""] * (COLUMNS - len(chunk))
        cells = "".join(f"<td><code>{n}</code></td>" if n else "<td></td>" for n in chunk)
        rows.append(f"  <tr>{cells}</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def render_readme(text: str, names: list[str]) -> str:
    block = f"{BEGIN}\n{sound_table(names)}\n{END}"
    text, found = re.subn(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    if not found:
        raise ValueError(f"no {BEGIN} … {END} block")
    count = len(names)
    text = re.sub(r"(shieldcn\.dev/badge/sounds-)\d+", rf"\g<1>{count}", text)
    text = re.sub(r'alt="\d+ sounds"', f'alt="{count} sounds"', text)
    return text


def cmd_readme(args: argparse.Namespace) -> int:
    names = sound_names()
    stale = []
    for name in READMES:
        path = ROOT / name
        old = path.read_text(encoding="utf-8")
        new = render_readme(old, names)
        if new == old:
            continue
        stale.append(name)
        if not args.check:
            path.write_text(new, encoding="utf-8")
    if args.check and stale:
        print(f"error: the sound list is out of date in {', '.join(stale)}; run: python3 scripts/hitsounds.py readme", file=sys.stderr)
        return 1
    print(f"{'stale' if args.check else 'updated'}: {', '.join(stale)}" if stale else "ok: the READMEs are up to date")
    return 0


# --------------------------------------------------------------------------- build


def cmd_build(args: argparse.Namespace) -> int:
    if cmd_check(args):
        return 1
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "hitsounds.zip"
    # Flat, so its files go straight into the folder Neverlose opens. Fixed
    # timestamps and order: the same sounds always give the same archive.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sound_files():
            info = zipfile.ZipInfo(path.name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / "sha256sums.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"{archive.relative_to(Path.cwd()) if archive.is_relative_to(Path.cwd()) else archive}: "
          f"{len(sound_files())} sounds, {archive.stat().st_size // 1024} KiB, sha256 {digest}")
    return 0


# --------------------------------------------------------------------------- notes


def tree(ref: str | None) -> dict[str, str]:
    """name -> blob hash of every sound at a git ref (the working tree when ref is None)."""
    if ref is None:
        return {p.name[: -len(EXT)]: hashlib.sha1(b"blob %d\0" % p.stat().st_size + p.read_bytes()).hexdigest()
                for p in sound_files()}
    out = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-z", ref, "--", "hitsounds/"],
                         capture_output=True, check=True).stdout.decode()
    sounds = {}
    for entry in filter(None, out.split("\0")):
        meta, path = entry.split("\t", 1)
        name = path.rsplit("/", 1)[-1]
        if name.endswith(EXT):
            sounds[name[: -len(EXT)]] = meta.split()[2]
    return sounds


def cmd_notes(args: argparse.Namespace) -> int:
    now = tree(None)
    lines = [f"**{len(now)} sounds.** Download `hitsounds.zip`, open the folder from Neverlose "
             "(Miscellaneous › Features › Hit Sound › the folder button), copy the sounds into it "
             "and press Refresh.", ""]
    before = tree(args.base) if args.base else None
    if before is None:
        lines.append("The first release of the collection.")
    else:
        def section(title: str, names: list[str]) -> None:
            if names:
                lines.extend([f"### {title}", "", *(f"- `{n}`" for n in sorted(names, key=str.lower)), ""])

        section("Added", [n for n in now if n not in before])
        section("Updated", [n for n in now if n in before and now[n] != before[n]])
        section("Removed", [n for n in before if n not in now])
        if lines[-1] != "":
            lines.append("")
        if len(lines) == 2:
            lines.append(f"No sounds changed since {args.base}.")
    print("\n".join(lines).rstrip())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate every file in hitsounds/").set_defaults(func=cmd_check)
    p = sub.add_parser("readme", help="update the sound list in the READMEs")
    p.add_argument("--check", action="store_true", help="fail instead of writing when it is out of date")
    p.set_defaults(func=cmd_readme)
    p = sub.add_parser("build", help="pack hitsounds.zip and sha256sums.txt")
    p.add_argument("--out", default="dist")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("notes", help="release notes, compared with an earlier tag")
    p.add_argument("--base", help="the previous release tag (none for the first release)")
    p.set_defaults(func=cmd_notes)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
