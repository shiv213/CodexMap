#!/usr/bin/env python3
"""Remove CodexMap login services. Keep saved positions, marks, and config."""
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
    print('Removed CodexMap services. Saved data and logs remain in Library.')
    print('Tailscale routes remain. See README.md to remove the dashboard listeners.')


if __name__ == '__main__':
    main()
