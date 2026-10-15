# CodexMap

Your Codex chats on a small, live map. Drag them between **Active / Passive**
and **High / Low priority**, glance at their status, and open a chat from its node.

The map refreshes every four seconds. Finished chats pulse as squares. Mark a
chat done to turn it gray with a check; after ten minutes it moves to the Archived
list. Unmark it to return it to its saved position. Positions, marks, and timers
stay on the Mac and sync to every browser that opens the dashboard.

## Install on macOS

Requires macOS 13 or later and the Codex desktop app with at least one chat.

1. Download [CodexMap-macOS.zip from the latest release](https://github.com/shiv213/CodexMap/releases/latest/download/CodexMap-macOS.zip).
2. Unzip it and open **CodexMap.app**.
3. Click **Install CodexMap**. Leave automatic updates enabled if you want future
   stable releases installed for you.
4. Open the map using the link shown when setup finishes.

The installer supports Apple Silicon and Intel Macs. It uses Python 3.9 or later.
If Python is missing, it offers the official Python installer, then checks again.

Current releases are unsigned and are not notarized. If macOS blocks the app,
try Control-click → Open, or use **System Settings → Privacy & Security → Open
Anyway** after attempting to open it. Signing and notarization require an Apple
Developer certificate.

## Access from your phone

Select **Tailscale private access** during setup. The installer can download the
official Tailscale package if needed. Approve its VPN access and sign in when
Tailscale asks, then finish setup. CodexMap shows a private HTTPS link.

Install Tailscale on your phone and sign in to the same network to open that link.
The map fits on a phone screen; tap a node for its title, message preview, and
done control. **Open on Mac** opens the chat on the host Mac. Opening an exact
Codex Remote chat in the ChatGPT iOS app is not supported by this project yet.

Tailscale setup uses HTTPS port 443 and a TCP route on the dashboard's port. If
either listener is already in use, setup asks you to resolve it first. Access follows
your Tailscale network's permissions. CodexMap does not enable public Funnel
access.

## Use the map

| Action | Desktop | Phone |
| --- | --- | --- |
| Preview messages | Hover a node | Tap a node |
| Move a chat | Drag a node | Drag a node |
| Open a chat on the Mac | Click a node | Open on Mac in the preview |
| Mark or unmark done | Right-click a node | Done control in the preview |
| See old done chats | Archived tab | Archived tab |

Keyboard users can focus nodes and move them with arrow keys. The page respects
reduced-motion preferences.

## Updates and storage

With automatic updates enabled, a login service checks the latest stable
[GitHub release](https://github.com/shiv213/CodexMap/releases/latest) at login and
every six hours. It checks the source ZIP against the release's SHA-256 checksum,
updates the dashboard, and restarts the service. Saved state and configuration
remain in place. The updater does not install prereleases or downgrade versions.

CodexMap runs as a user login service, so it keeps running after you close Codex
or Terminal and restarts if its process exits. The Mac must be on and logged in
for remote access. By default it stays awake on AC power; closing the lid,
logging out, or sleeping on battery can make the map unavailable.

Application files and state live in
`~/Library/Application Support/CodexMap/`. Logs live in
`~/Library/Logs/CodexMap/`. The dashboard reads local Codex data and serves
titles and message previews to browsers that can reach it. Keep the URL private
and share only with people allowed to read those chats. Release packages contain
source code and the installer; they contain no user chats or saved state.

CodexMap is an independent project. It reads Codex's local SQLite and rollout
files; changes to Codex's internal format may require an update.

## Install from source

With Python 3.9 or later installed:

```sh
git clone https://github.com/shiv213/CodexMap.git
cd CodexMap
python3 install.py
```

Use `python3 install.py --tailscale` to configure an already installed, signed-in
Tailscale. Add `--no-auto-update` to disable automatic updates or `--allow-sleep`
to allow sleep while on AC power. Run `python3 install.py --help` for a different
port or Codex data folder.

To check for and install an update manually:

```sh
python3 "$HOME/Library/Application Support/CodexMap/current/update.py"
```

To remove the login services:

```sh
python3 "$HOME/Library/Application Support/CodexMap/current/uninstall.py"
```

This keeps saved data and logs. If CodexMap owns your Tailscale listeners, remove
them separately (replace `4399` if you chose a different dashboard port):

```sh
tailscale serve --https=443 off
tailscale serve --tcp=4399 off
```

## Troubleshooting and development

If no chats appear, open Codex and start a chat. Confirm the installer points to
the correct Codex data folder. If installation reports a busy port, choose a
different one with `--port 4400`. Service errors appear in `error.log` in the logs
folder above. For phone access, check that both devices are online in Tailscale
and that MagicDNS and HTTPS certificates are enabled for your network.

Run `python3 verify.py` and `node --check app/app.js` to check the source with fake
chats. Build the native installer on a Mac with Xcode Command Line Tools using
`./macos/build.sh`, then create the release payload with
`python3 scripts/release-package.py`. See [CONTRIBUTING.md](CONTRIBUTING.md) for
pull requests.

MIT licensed. Copyright © 2026 Shiv Trivedi.
