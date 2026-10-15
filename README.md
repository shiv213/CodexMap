# CodexMap

A live map of your Codex chats.

## Install

[Download the Mac installer](https://github.com/shiv213/CodexMap/releases/latest/download/CodexMap-macOS.zip), unzip it, and open CodexMap.app.

Choose automatic updates and optional Tailscale access, then click Install. Setup shows your dashboard link and offers Python or Tailscale if missing.

Requires macOS 13+ and an existing Codex desktop chat. Supports Intel and Apple Silicon. The app isn't notarized; if blocked, use System Settings → Privacy & Security → Open Anyway.

## Use

- Drag chats between Active / Passive and High / Low priority. Arrow keys also move nodes.
- Hover for messages; tap on mobile. Click to open the chat on the Mac.
- Right-click to mark or unmark done. On mobile, use the preview's done button.
- Done chats turn gray and move to Archived after 10 minutes. Unmark to restore.

The map refreshes every four seconds. Messages appear oldest to newest; previews open at the bottom. Positions and marks sync across browsers.

For phone access, sign in to Tailscale on both devices. The Mac must stay awake and logged in. CodexMap opens chats on the Mac, not in ChatGPT's iPhone app.

## Updates and data

Starts at login and checks GitHub releases every six hours. Updates verify checksums and preserve saved state.

Data: `~/Library/Application Support/CodexMap/`. Logs: `~/Library/Logs/CodexMap/`.

Anyone allowed to reach the dashboard can read its chat previews. Tailscale access stays private to your network. The Mac stays awake on AC power; sleep, lid closure, or logout can disconnect it.

## Source

```sh
git clone https://github.com/shiv213/CodexMap.git
cd CodexMap
python3 install.py
```

Requires Python 3.9+. Options: `--tailscale`, `--no-auto-update`, `--allow-sleep`, `--port 4400`. See `--help` for all options.

```sh
# Update
python3 "$HOME/Library/Application Support/CodexMap/current/update.py"
# Uninstall; keeps saved data
python3 "$HOME/Library/Application Support/CodexMap/current/uninstall.py"
```

If you configured Tailscale routes, remove them separately:

```sh
tailscale serve --https=443 off
tailscale serve --tcp=4399 off
```

Use your chosen port. Existing routes must be free before setup.

Independent project using Codex's internal database format. App updates may require compatibility fixes. [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE).
