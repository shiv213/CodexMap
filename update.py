#!/usr/bin/env python3
"""Check GitHub releases and update CodexMap without changing saved data."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'shiv213/CodexMap'
ASSET = 'CodexMap-source.zip'
FILES = ('app/server.py', 'app/app.js', 'app/style.css', 'app/index.html', 'install.py', 'uninstall.py', 'update.py', 'VERSION')
LABEL = 'com.codex.chat-map.dashboard'
MAX_DOWNLOAD = 40 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value.strip())
    if not match:
        raise ValueError('Release version must be a stable semantic version.')
    return tuple(int(part) for part in match.groups())


def download(url, limit=MAX_DOWNLOAD):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('api.github.com', 'github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com'):
        raise ValueError('Release URL must use GitHub HTTPS.')
    req = urllib.request.Request(url, headers={'User-Agent': 'CodexMap-Updater', 'Accept': 'application/vnd.github+json' if parsed.hostname == 'api.github.com' else 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Release asset exceeds the size limit.')
    return data


def verified_source(release, fetch=download):
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Only published stable releases can be installed.')
    version = release['tag_name'].removeprefix('v')
    version_tuple(version)
    assets = {asset['name']: asset['browser_download_url'] for asset in release.get('assets', [])}
    if ASSET not in assets or 'SHA256SUMS.txt' not in assets:
        raise ValueError('Release is missing its source ZIP or checksum asset.')
    checksum_text = fetch(assets['SHA256SUMS.txt'], 65536).decode('utf-8')
    hashes = {}
    for line in checksum_text.splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?([^\s]+)', line)
        if match:
            hashes[match[2]] = match[1].lower()
    data = fetch(assets[ASSET], MAX_DOWNLOAD)
    if hashes.get(ASSET) != hashlib.sha256(data).hexdigest():
        raise ValueError('The release ZIP checksum does not match.')
    return version, data


def extract_source(data, destination):
    import io
    found = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                raise ValueError('Release ZIP contains an unsafe path.')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Release ZIP contains a symbolic link.')
            name = str(path)
            if name.startswith('CodexMap/'):
                name = name[len('CodexMap/'):]
            if entry.is_dir() or name not in FILES:
                continue
            if name in found:
                raise ValueError('Release ZIP contains a duplicate source file.')
            total += entry.file_size
            if total > MAX_DOWNLOAD or entry.file_size > 10 * 1024 * 1024:
                raise ValueError('Expanded release exceeds the size limit.')
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            found.add(name)
    if found != set(FILES):
        raise ValueError('Release ZIP is missing required source files.')
    for name in ('app/server.py', 'install.py', 'uninstall.py', 'update.py'):
        compile((destination / name).read_text(), name, 'exec')


def replace_current(base, release):
    temporary = base / '.current-next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release.relative_to(base), target_is_directory=True)
    os.replace(temporary, base / 'current')


def wait_healthy(base, expected_version, timeout=12):
    config = json.loads((base / 'config.json').read_text())
    url = f'http://127.0.0.1:{config.get("port", 4399)}/health'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                health = json.load(response)
            if health.get('service') == 'codex-chat-map' and health.get('version') == expected_version:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError('Updated service did not become healthy.')


def restart_service():
    subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{LABEL}'], check=True, capture_output=True)


def apply_release(base, version, data, restart=restart_service, healthy=wait_healthy):
    base = base.resolve()
    releases = base / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    current = base / 'current'
    previous = current.resolve(strict=True)
    previous_version = (previous / 'VERSION').read_text().strip()
    target = releases / version
    if target.exists():
        # A failed earlier attempt can leave a staged release behind.
        if target.resolve() == previous:
            raise ValueError('Release is already installed.')
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=releases) as temporary:
        stage = Path(temporary) / 'source'
        stage.mkdir()
        extract_source(data, stage)
        if (stage / 'VERSION').read_text().strip() != version:
            raise ValueError('ZIP version differs from the GitHub release tag.')
        stage.rename(target)
    try:
        replace_current(base, target)
        restart()
        healthy(base, version)
    except Exception as error:
        replace_current(base, previous)
        try:
            restart()
            healthy(base, previous_version)
        except Exception as rollback_error:
            raise RuntimeError(f'Update failed: {error}. Old files restored, but service restart failed: {rollback_error}') from error
        raise RuntimeError(f'Update failed; previous version restored: {error}') from error
    # Keep the prior release so rollback is possible and bound disk usage.
    for release in releases.iterdir():
        if release.is_dir() and release not in (target, previous) and not release.name.startswith('.'):
            shutil.rmtree(release)


@contextlib.contextmanager
def exclusive_lock(base):
    base.mkdir(parents=True, exist_ok=True)
    with (base / '.update.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another installation or update is already running.')
        yield


def check(base, check_only=False, fetch=download):
    current = (base / 'current' / 'VERSION').read_text().strip()
    release = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', 1024 * 1024))
    latest = release['tag_name'].removeprefix('v')
    if release.get('prerelease') or release.get('draft'):
        raise ValueError('Latest release is not a published stable release.')
    available = version_tuple(latest) > version_tuple(current)
    result = {'currentVersion': current, 'latestVersion': latest, 'updateAvailable': available, 'updated': False}
    if available and not check_only:
        version, data = verified_source(release, fetch)
        apply_release(base, version, data)
        result['updated'] = True
        result['currentVersion'] = version
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-directory', type=Path, default=Path.home() / 'Library/Application Support/CodexMap')
    parser.add_argument('--check', action='store_true', help='Check without installing')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    base = args.install_directory.expanduser().resolve()
    with exclusive_lock(base):
        result = check(base, args.check)
        (base / 'update-status.json').write_text(json.dumps({**result, 'checkedAt': time.time()}) + '\n')
    print(json.dumps(result) if args.json else ('Updated to ' + result['currentVersion'] if result['updated'] else 'Update available: ' + result['latestVersion'] if result['updateAvailable'] else 'CodexMap is up to date.'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Update failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
