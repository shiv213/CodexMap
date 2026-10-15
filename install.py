#!/usr/bin/env python3
"""Install CodexMap."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

LABEL = 'com.codex.chat-map.dashboard'
SOURCE_ROOT = Path(__file__).resolve().parent
SOURCE = SOURCE_ROOT / 'app'
UPDATE_LABEL = 'com.codex.chat-map.updater'
from update import FILES, exclusive_lock, replace_current, version_tuple


def tailscale_settings(status, port):
    if status.get('BackendState') != 'Running':
        raise RuntimeError('Sign in to Tailscale, then retry.')
    device = status['Self']
    dns = device.get('DNSName', '').rstrip('.')
    hostname = device.get('HostName', '')
    origins = []
    for name in (dns, hostname):
        if name:
            origins.append(f'http://{name}:{port}')
    for ip in device.get('TailscaleIPs', []):
        host = f'[{ip}]' if ':' in ip else ip
        origins.append(f'http://{host}:{port}')
    if not dns:
        raise RuntimeError('Enable Tailscale MagicDNS, then retry.')
    origins.append(f'https://{dns}')
    return sorted(set(origins)), f'https://{dns}/'


def service_plist(install_dir, logs, python, keep_awake):
    command = [python, '-u', str(install_dir / 'current' / 'app' / 'server.py')]
    if keep_awake:
        command = ['/usr/bin/caffeinate', '-s', *command]
    return {
        'Label': LABEL, 'ProgramArguments': command,
        'WorkingDirectory': str(install_dir / 'current' / 'app'),
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
        'ProcessType': 'Background',
        'EnvironmentVariables': {'CODEXMAP_DATA_DIR': str(install_dir), 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'StandardOutPath': str(logs / 'service.log'),
        'StandardErrorPath': str(logs / 'error.log'),
    }


def find_tailscale():
    options = [shutil.which('tailscale'), '/opt/homebrew/bin/tailscale', '/usr/local/bin/tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale']
    for binary in options:
        if binary and Path(binary).is_file():
            return binary
    raise RuntimeError('Install Tailscale (brew install tailscale) or omit --tailscale.')


def updater_plist(install_dir, logs, python):
    return {
        'Label': UPDATE_LABEL,
        'ProgramArguments': [python, str(install_dir / 'current' / 'update.py')],
        'RunAtLoad': True, 'StartInterval': 21600, 'ProcessType': 'Background',
        'StandardOutPath': str(logs / 'updates.log'),
        'StandardErrorPath': str(logs / 'updates-error.log'),
    }


def ensure_routes_available(binary, port, local_url):
    result = subprocess.run([binary, 'serve', 'status', '--json'], check=True, capture_output=True, text=True)
    status = json.loads(result.stdout or '{}')
    for listener, value in status.get('TCP', {}).items():
        if str(listener) not in ('443', str(port)):
            continue
        if str(listener) == str(port) and value.get('TCPForward') == f'127.0.0.1:{port}':
            continue
        if str(listener) == '443':
            handlers = [handler for website in status.get('Web', {}).values() for handler in website.get('Handlers', {}).values()]
            if value.get('HTTPS') and handlers and all(handler.get('Proxy', '').rstrip('/') == local_url.rstrip('/') for handler in handlers):
                continue
        raise RuntimeError(f'Tailscale Serve already uses port {listener}. Free that port or use local setup.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tailscale', action='store_true', help='Enable Tailscale access')
    parser.add_argument('--json', action='store_true', help='Print JSON')
    parser.add_argument('--no-auto-update', action='store_true', help='Disable updates')
    parser.add_argument('--python', type=Path, help='Python interpreter')
    parser.add_argument('--port', type=int, default=4399)
    parser.add_argument('--codex-home', type=Path, default=Path.home() / '.codex')
    parser.add_argument('--allow-sleep', action='store_true', help='Allow sleep on AC')
    parser.add_argument('--dry-run', action='store_true', help='Preview setup')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise RuntimeError('This installer requires macOS.')
    if sys.version_info < (3, 9):
        raise RuntimeError('Requires Python 3.9+: brew install python.')
    if not 1024 <= args.port <= 65535:
        raise RuntimeError('Choose a port between 1024 and 65535.')
    if not SOURCE.is_dir():
        raise RuntimeError('Keep install.py beside the app folder from the ZIP.')
    codex_home = args.codex_home.expanduser().resolve()
    if not list(codex_home.glob('state_*.sqlite')):
        raise RuntimeError('Start a Codex chat first. For custom data paths, use --codex-home.')
    home = Path.home()
    install_dir = home / 'Library/Application Support/CodexMap'
    logs = home / 'Library/Logs/CodexMap'
    plist_path = home / 'Library/LaunchAgents' / f'{LABEL}.plist'
    target = f'gui/{os.getuid()}/{LABEL}'
    local_url = f'http://127.0.0.1:{args.port}/'
    config = {'port': args.port, 'codexHome': str(codex_home), 'allowedOrigins': []}
    config_path = install_dir / 'config.json'
    if config_path.exists():
        existing_config = json.loads(config_path.read_text())
        config['allowedOrigins'] = existing_config.get('allowedOrigins', [])
        config['remoteUrl'] = existing_config.get('remoteUrl')
    remote_url = config.get('remoteUrl')
    binary = None
    if args.tailscale:
        binary = find_tailscale()
        result = subprocess.run([binary, 'status', '--json'], check=True, capture_output=True, text=True)
        config['allowedOrigins'], remote_url = tailscale_settings(json.loads(result.stdout), args.port)
        ensure_routes_available(binary, args.port, local_url)
        config['remoteUrl'] = remote_url
    python = str((args.python or Path(sys.executable)).absolute())
    if not Path(python).is_file():
        raise RuntimeError('Python interpreter was not found.')
    version = (SOURCE_ROOT / 'VERSION').read_text().strip()
    version_tuple(version)
    for filename in FILES:
        if not (SOURCE_ROOT / filename).is_file():
            raise RuntimeError(f'Missing {filename}. Download the source ZIP again.')
    install_source = True
    installed_version = install_dir / 'current' / 'VERSION'
    if installed_version.exists():
        existing_version = installed_version.read_text().strip()
        if version_tuple(existing_version) > version_tuple(version):
            version = existing_version
            install_source = False
    config['autoUpdates'] = not args.no_auto_update
    plist = service_plist(install_dir, logs, python, not args.allow_sleep)
    if args.dry_run:
        print(json.dumps({'installDirectory': str(install_dir), 'plistPath': str(plist_path), 'config': config, 'service': plist}, indent=2))
        return
    running = subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0
    if not running:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', args.port))
            except OSError:
                raise RuntimeError(f'Port {args.port} is in use. Try --port 4400.')
    install_dir.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(install_dir):
        if running:
            subprocess.run(['launchctl', 'bootout', target], check=True)
        release = install_dir / 'releases' / version
        release.mkdir(parents=True, exist_ok=True)
        for filename in FILES if install_source else ():
            destination = release / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (SOURCE_ROOT / filename).resolve() != destination.resolve():
                shutil.copy2(SOURCE_ROOT / filename, destination)
        replace_current(install_dir, release)
        config_path.write_text(json.dumps(config, indent=2) + '\n')
        config_path.chmod(0o600)
        plist_path.write_bytes(plistlib.dumps(plist))
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist_path)], check=True, capture_output=True)
        for attempt in range(50):
            try:
                with urllib.request.urlopen(local_url + 'health', timeout=2) as response:
                    health = json.load(response)
                if health.get('service') == 'codex-chat-map' and health.get('version') == version:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        else:
            raise RuntimeError(f'Service did not start. Check {logs / "error.log"}.')
        if binary:
            subprocess.run([binary, 'serve', '--bg', '--https=443', local_url.rstrip('/')], check=True, capture_output=True)
            subprocess.run([binary, 'serve', '--bg', f'--tcp={args.port}', f'tcp://127.0.0.1:{args.port}'], check=True, capture_output=True)
    update_target = f'gui/{os.getuid()}/{UPDATE_LABEL}'
    update_path = plist_path.parent / f'{UPDATE_LABEL}.plist'
    subprocess.run(['launchctl', 'bootout', update_target], capture_output=True)
    if args.no_auto_update:
        update_path.unlink(missing_ok=True)
    else:
        update_path.write_bytes(plistlib.dumps(updater_plist(install_dir, logs, python)))
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(update_path)], check=True, capture_output=True)
    result = {'installed': True, 'localUrl': local_url, 'remoteUrl': remote_url,
              'installDirectory': str(install_dir), 'version': version, 'autoUpdates': not args.no_auto_update}
    if args.json:
        print(json.dumps(result))
    else:
        print(f'Installed CodexMap {version}. Open {remote_url or local_url}')
        print('Starts at login.')
        print('Updates check GitHub every six hours.' if not args.no_auto_update else 'Updates disabled.')



if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        if isinstance(detail, bytes):
            detail = detail.decode(errors='replace')
        print(f'Setup failed: {detail}', file=sys.stderr)
        sys.exit(1)
