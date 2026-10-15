# Native installer

`./macos/build.sh` builds `dist/CodexMap-macOS.zip` with a universal macOS app for macOS 13 or newer. It includes the dashboard and service installer. Building requires Xcode Command Line Tools.

Open CodexMap, choose private access and automatic updates, then click **Install CodexMap**. Open the Codex desktop app and start a chat before installing.

If Python 3.9 or newer is missing, **Download Python** downloads a signed package from python.org and opens Apple’s installer. Finish that installer, then click **Check again**. Python stays installed when CodexMap is removed.

Private access is optional. When Tailscale is missing, **Install Tailscale** fetches the current stable package from Tailscale’s official download server, verifies its package signature, and opens Apple’s installer. Approve the VPN connection in Tailscale, sign in, and click **Finish setup**. The final screen shows the dashboard link to bookmark or copy to another device on the same Tailscale network. Tailscale stays installed when CodexMap is removed.

The default build uses an ad hoc signature. macOS may block a downloaded copy; use the **Open Anyway** action in System Settings → Privacy & Security after attempting to open it. Release maintainers can set `CODE_SIGN_IDENTITY` to a Developer ID Application certificate before building, then notarize the result separately. An ad hoc signature does not provide Apple notarization or publisher identity.

Automatic updates apply to the installed dashboard service, not the installer window. The service checks GitHub releases every six hours when enabled. See the repository’s update instructions for checksums and recovery.
