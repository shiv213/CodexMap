# Contributing

Keep the dashboard small. Changes should preserve saved positions, done marks,
and the ability to fit the map on a phone screen.

Run the checks before opening a pull request:

```sh
python3 verify.py
node --check app/app.js
python3 scripts/release-package.py
```

On macOS, also run `./macos/build.sh` after installer changes.
The verifier uses fake chats and temporary storage. Keep real transcripts,
database files, configuration, and credentials out of commits and bug reports.

Describe what changed and how you checked it. Screenshots help with UI changes;
use fake chat names or hide personal information.
