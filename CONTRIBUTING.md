# Contributing

Keep the map small and preserve saved state and mobile layout.

```sh
python3 verify.py
node --check app/app.js
python3 scripts/release-package.py
```

For installer changes, also run `./macos/build.sh` on a Mac.

Explain the change and checks in your PR. Use fake chats in screenshots. Keep transcripts, databases, config, and credentials out of commits.
