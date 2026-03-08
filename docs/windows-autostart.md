# Auto-Starting the Workstate Dashboard on Windows Boot

The dashboard can be configured to launch automatically when you log into Windows, so it's always ready at `http://localhost:7777`.

For baseline requirements and optional integration env vars, see [platform assumptions](platform-assumptions.md).

## How It Works

Two files handle the auto-start:

1. **`tools/start-dashboard.vbs`** — A VBScript wrapper that launches the dashboard silently (no console window pops up). It uses `pythonw` so there's no visible terminal.

2. **A shortcut in Windows Startup** — Located at:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Workstate Dashboard.lnk
   ```
   Windows runs everything in this folder on login.

## Setup

If the startup shortcut isn't already installed, run:

```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\create-startup-shortcut.ps1
```

This creates the shortcut pointing to `start-dashboard.vbs`.

## Removing Auto-Start

Delete the shortcut:

```
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Workstate Dashboard.lnk"
```

Or open the Startup folder in Explorer (`shell:startup`) and delete "Workstate Dashboard".

## Troubleshooting

- **Dashboard not running after reboot?** Make sure `pythonw` is on your PATH. Open a terminal and run `pythonw --version` to verify.
- **Want to change the port?** Edit `tools/start-dashboard.vbs` and add `--port XXXX` to the command line.
- **Want to run it manually instead?** Just run `python tools/workstate-dashboard.py` from the repo root.
