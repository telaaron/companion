# Installing Companion

Companion ships as a self-contained native desktop app for macOS, Windows, and
Linux. No Python, no terminal, no dependencies to install — just download,
install, and double-click.

---

## Download

Go to the [Releases page](https://github.com/telaaron/companion/releases/latest)
and download the file for your platform:

| Platform | File |
|----------|------|
| macOS (Apple Silicon, M1/M2/M3) | `Companion-{version}-aarch64-apple-darwin.dmg` |
| Windows 10/11 (x86-64) | `Companion-{version}-x86_64-pc-windows-msvc.exe` |
| Linux (x86-64) | `Companion-{version}-x86_64-unknown-linux-gnu.AppImage` |

---

## macOS

1. Open the downloaded `.dmg` file.
2. Drag **Companion.app** into your **Applications** folder.
3. Double-click Companion in Applications (or Launchpad).

> **Gatekeeper warning (unsigned build)**
>
> Because this release is not codesigned with an Apple Developer certificate,
> macOS may show _"Companion cannot be opened because it is from an
> unidentified developer."_
>
> To bypass this **once**:
>
> 1. Right-click (or Control-click) **Companion.app** in Finder.
> 2. Choose **Open** from the context menu.
> 3. Click **Open** in the dialog that appears.
>
> After the first launch you can open it normally. Apple's Gatekeeper
> remembers your choice.
>
> If you need a fully notarized build for enterprise deployment, see
> [docs/releasing.md](releasing.md) for codesigning instructions.

4. The Companion icon appears in your menu bar. The dashboard opens
   automatically in a native window pointed at `http://127.0.0.1:8082/ui/`.

---

## Windows

1. Run the downloaded `.exe` installer.
2. Follow the installer wizard (Next → Install → Finish).
3. Companion starts automatically after installation and adds a tray icon to
   the system notification area.

> **SmartScreen warning:** Windows may show a SmartScreen prompt for unsigned
> installers. Click **More info** → **Run anyway**.

---

## Linux (AppImage)

1. Make the file executable:

   ```bash
   chmod +x Companion-{version}-x86_64-unknown-linux-gnu.AppImage
   ```

2. Run it:

   ```bash
   ./Companion-{version}-x86_64-unknown-linux-gnu.AppImage
   ```

   Or double-click it in your file manager (requires FUSE support — most
   modern desktop Linux distributions include this by default).

3. The Companion tray icon appears in your system tray. If your desktop
   environment does not show tray icons by default, install the appropriate
   tray extension (e.g. **TopIcons Plus** on GNOME).

---

## First-run setup

On first launch Companion asks for your API key. You can also set it via
environment variable before launching:

```bash
export ANTHROPIC_AUTH_TOKEN=sk-ant-...
./Companion.app   # macOS
```

Your key is stored in the app's local config directory — never sent anywhere
except the configured AI gateway.

---

## Tray menu

| Action | Effect |
|--------|--------|
| **Open Dashboard** | Bring the dashboard window to the front |
| **Start Server** | (Re)start the bundled Python server if it stopped |
| **Stop Server** | Stop the Python server (dashboard becomes unavailable) |
| **Quit** | Stop server and exit completely |

Closing the window hides it — the server keeps running in the background.
Use **Quit** from the tray to exit fully.

---

## Uninstall

- **macOS:** Drag Companion.app from Applications to Trash.
- **Windows:** Use *Add or remove programs* → Companion → Uninstall.
- **Linux:** Delete the AppImage file.

User data (sessions, config) is stored under the OS-standard XDG dirs:

| Platform | Config (.env) | Sessions / cache (SQLite) |
|----------|---------------|---------------------------|
| macOS | `~/.config/companion/` | `~/.cache/companion/` |
| Linux | `~/.config/companion/` | `~/.cache/companion/` |
| Windows | `%USERPROFILE%\.config\companion\` | `%USERPROFILE%\.cache\companion\` |

Delete those directories to fully remove all user data. The Tauri shell itself
holds no state outside these dirs.

---

## Troubleshooting

**Dashboard shows "connection refused"**

The Python server may not have started yet. Wait a few seconds and reload the
page, or use **Start Server** from the tray menu.

**Nothing in the tray on Linux**

Install a tray extension for your desktop environment or run from terminal to
see logs:

```bash
RUST_LOG=info ./Companion-*.AppImage
```

**Port 8082 already in use**

Another Companion instance is running, or a different service is using the
port. Quit all Companion instances and retry.
