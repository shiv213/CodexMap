#!/usr/bin/env python3
"""Package release files and checksums."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    'VERSION', 'LICENSE', 'README.md', 'CONTRIBUTING.md', '.gitignore',
    'install.py', 'uninstall.py', 'update.py', 'verify.py',
    'app/server.py', 'app/index.html', 'app/app.js', 'app/style.css',
    'scripts/release-package.py',
    'macos/CodexMap.swift', 'macos/make-icon.swift', 'macos/build.sh', 'macos/README.md',
    '.github/workflows/ci.yml', '.github/workflows/release.yml',
)


def files():
    selected = [ROOT / name for name in FILES]
    missing = [str(path.relative_to(ROOT)) for path in selected if not path.is_file()]
    if missing:
        raise RuntimeError('Missing release files: ' + ', '.join(missing))
    for path in selected:
        if path.is_symlink():
            raise RuntimeError('Release symlink: ' + str(path.relative_to(ROOT)))
    return sorted(set(selected))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('VERSION must use MAJOR.MINOR.PATCH.')
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'CodexMap-source.zip'
    selected = files()
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in selected:
            info = zipfile.ZipInfo('CodexMap/' + path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            mode = 0o755 if path.suffix == '.sh' else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    assets = [source]
    macos = args.output / 'CodexMap-macOS.zip'
    if macos.exists():
        assets.append(macos)
    lines = [hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name for path in assets]
    (args.output / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n')
    print('Packaged CodexMap ' + version + ' (' + str(len(selected)) + ' source files).')
    for path in assets:
        print(path)


if __name__ == '__main__':
    main()
