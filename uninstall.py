#!/usr/bin/env python3
"""Uninstall CodexMap; keep saved data."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform != 'darwin':
        sys.exit('This uninstaller requires macOS.')
    for label in ('com.codex.chat-map.updater', 'com.codex.chat-map.dashboard'):
        target = f'gui/{os.getuid()}/{label}'
        result = subprocess.run(['launchctl', 'bootout', target], capture_output=True)
        if result.returncode and subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0:
            sys.exit('Could not stop the service: ' + result.stderr.decode().strip())
        (Path.home() / 'Library/LaunchAgents' / f'{label}.plist').unlink(missing_ok=True)
    print('Uninstalled. Saved data and logs kept.')
    print('Tailscale routes kept. Removal commands: README.md.')


if __name__ == '__main__':
    main()
