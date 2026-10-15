# Mac installer

Requires Xcode Command Line Tools:

```sh
./macos/build.sh
```

Produces `dist/CodexMap-macOS.zip` for Intel and Apple Silicon, macOS 13+.

Builds use ad hoc signing. Set `CODE_SIGN_IDENTITY` to a Developer ID certificate for distribution signing; notarize separately.

Python and Tailscale use their official signed installers. Uninstalling CodexMap leaves both installed. Automatic updates update the dashboard service.
