#!/usr/bin/env python3
"""Keep one non-italic font per family, including fonts already extracted.

Dry-run by default. With --apply, unwanted fonts are moved to a backup folder
(not destroyed). ZIP contents are handled without extracting misc/info files.
Use --delete-zips only when you are comfortable removing successfully read ZIPs.
Grouping is based on filenames; review the dry-run for unusual font families.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXTS = {'.ttf', '.otf'}
# Avoid erasing identifiers that are legitimate parts of distinctive font names.
STYLES = ('regular','italic','oblique','bold','semibold','demibold','extrabold',
          'ultrabold','medium','light','extralight','ultralight','thin','black',
          'heavy','condensed','narrow','extended','wide','book')
STYLE_RX = re.compile(r'(?i)(?:[-_ .]?)(?:' + '|'.join(sorted(STYLES, key=len, reverse=True)) + r')$')
RANDOM_SUFFIX = re.compile(r'-[a-zA-Z0-9]{4,6}$')

@dataclass
class Font:
    label: str
    name: str
    data: bytes
    path: Path | None = None
    zip_path: Path | None = None
    zip_member: str | None = None

    @property
    def digest(self):
        return hashlib.sha256(self.data).hexdigest()


def normalized_stem(filename: str) -> str:
    stem = Path(filename).stem
    # Font sites often append a short download token after a hyphen.
    # Only strip it when it looks random (mixed-case or containing numbers).
    match = RANDOM_SUFFIX.search(stem)
    if match and (any(c.isdigit() for c in match.group()[1:]) or
                  (any(c.islower() for c in match.group()[1:]) and any(c.isupper() for c in match.group()[1:]))):
        stem = stem[:match.start()]
    return stem.strip('-_ .') or 'Font'


def family_key(filename: str) -> str:
    stem = normalized_stem(filename)
    # Remove trailing style tokens repeatedly (e.g. BoldItalic).
    while True:
        revised = STYLE_RX.sub('', stem).strip('-_ .')
        if not revised or revised == stem:
            break
        stem = revised
    # If a previous run added -2 / -3 for filename collisions, group them.
    stem = re.sub(r'-\d+$', '', stem)
    return re.sub(r'[^a-z0-9]', '', stem.casefold()) or 'font'


def italic(font: Font) -> bool:
    return bool(re.search(r'(?i)italic|oblique', normalized_stem(font.name)))


def preference(font: Font):
    stem = normalized_stem(font.name).casefold()
    is_regular = bool(re.search(r'(?:^|[-_ .])regular$|regular$', stem))
    styled = bool(re.search(r'(?i)bold|light|thin|black|heavy|medium|condensed|wide|outline|hollow|poster', stem))
    return (0 if is_regular else (2 if styled else 1),
            0 if Path(font.name).suffix.lower() == '.ttf' else 1,
            0 if font.path else 1,
            len(font.name), font.name.casefold())


def unique_path(folder: Path, name: str) -> Path:
    out = folder / name
    index = 2
    while out.exists():
        out = folder / f'{Path(name).stem}-{index}{Path(name).suffix.lower()}'
        index += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('folder', nargs='?', type=Path, default=Path('.'))
    ap.add_argument('--apply', action='store_true', help='Actually clean files (extras moved to backup)')
    ap.add_argument('--delete-zips', action='store_true', help='Remove ZIPs after successful processing (requires --apply)')
    args = ap.parse_args()
    folder = args.folder.expanduser().resolve()
    if not folder.is_dir(): ap.error(f'Not a directory: {folder}')
    if args.delete_zips and not args.apply: ap.error('--delete-zips requires --apply')

    existing = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in EXTS:
            try: existing.append(Font(p.name, p.name, p.read_bytes(), path=p))
            except OSError as e: print(f'ERROR reading {p.name}: {e}')

    zipped = []
    good_zips = []
    for zp in sorted(folder.glob('*.zip')):
        try:
            with zipfile.ZipFile(zp) as zf:
                if zf.testzip() is not None: raise ValueError('ZIP integrity check failed')
                items = []
                for info in zf.infolist():
                    name = PurePosixPath(info.filename.replace('\\','/')).name
                    if not info.is_dir() and Path(name).suffix.lower() in EXTS:
                        items.append(Font(f'{zp.name}/{name}',name,zf.read(info),zip_path=zp,zip_member=info.filename))
                zipped.extend(items)
                good_zips.append(zp)
        except (OSError,ValueError,zipfile.BadZipFile,RuntimeError,EOFError) as exc:
            print(f'ERROR reading ZIP {zp.name}: {exc} (will NOT delete)')

    groups = defaultdict(list)
    for f in existing + zipped:
        groups[family_key(f.name)].append(f)

    backup = folder / '_removed_font_backups'
    kept = 0
    extras = 0
    added = 0
    for group_name, fonts in sorted(groups.items()):
        regular = [f for f in fonts if not italic(f)]
        # If only italic versions exist, retain none per user preference.
        winner = min(regular, key=preference) if regular else None
        if winner:
            print(f'KEEP  [{group_name}] {winner.label}')
            kept += 1
        else:
            print(f'NO NON-ITALIC VERSION [{group_name}]')
        for f in fonts:
            if f is winner:
                if f.path is None:
                    name = normalized_stem(f.name) + Path(f.name).suffix.lower()
                    dest = unique_path(folder, name)
                    print(f'  {"EXTRACT" if args.apply else "WOULD EXTRACT"}: {f.label} -> {dest.name}')
                    if args.apply: dest.write_bytes(f.data)
                    added += 1
            elif f.path is not None:
                print(f'  {"BACKUP" if args.apply else "WOULD BACKUP"}: {f.name}' + (' (italic)' if italic(f) else ' (extra version)'))
                if args.apply:
                    backup.mkdir(exist_ok=True)
                    shutil.move(str(f.path),str(unique_path(backup,f.name)))
                extras += 1
            else:
                print(f'  SKIP ARCHIVED: {f.label}' + (' (italic)' if italic(f) else ' (extra version)'))

    if args.delete_zips:
        for zp in good_zips:
            zp.unlink()
            print(f'DELETE ZIP: {zp.name}')
    print(f'\n{"DONE" if args.apply else "PREVIEW ONLY"}: {kept} font families kept, '
          f'{extras} existing font files to backup, {added} new fonts to extract, {len(good_zips)} readable ZIPs.')
    if args.apply and extras: print(f'Backups: {backup}')
    if not args.apply: print('Review the choices, then run with --apply to execute.')

if __name__ == '__main__':
    main()
