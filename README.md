# Mnemo

Incremental Android photo/media backup tool using ADB. Transfers only new or changed files, skipping what's already on your PC.

## Features

- GUI and CLI versions
- Incremental backup — skips files already copied (matched by size)
- Preserves original folder structure from the phone
- Skips hidden files, thumbnails, and `.nomedia` files
- Optional skip of WhatsApp Sent media
- Browse phone folders directly from the GUI
- Progress bar with file count, percentage, and ETA
- Stop button to halt mid-backup cleanly after the current file
- System tray icon — minimize to tray, restore with a click
- Desktop notification when backup completes
- Shows connected device name (e.g. Samsung Galaxy S23)
- DPI-aware — sharp rendering on high-resolution displays

## Requirements

- Windows 10/11
- [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) — `adb` must be on your PATH
- USB Debugging enabled on the phone (Settings → Developer Options → USB Debugging)
- Python dependencies:

```bat
pip install pillow pystray
```

## Usage

### GUI

```bat
python mnemo_backup.py
```

1. Select a destination folder on your PC
2. Check the phone folders you want to back up
3. Optionally browse and add custom phone folders
4. Click **▶ Start Backup**

### CLI

```bat
python mnemo_backup_cli.py
```

Follow the prompts to choose a destination and select folders.

## Default folders

| Label | Phone path |
|---|---|
| DCIM (Camera) | `/sdcard/DCIM` |
| Pictures | `/sdcard/Pictures` |
| Movies | `/sdcard/Movies` |
| Music | `/sdcard/Music` |
| Documents | `/sdcard/Documents` |
| Download | `/sdcard/Download` |
| WhatsApp | `/sdcard/Android/media/com.whatsapp/WhatsApp` |

## Filters

The following are skipped by default (all configurable in the GUI):

- Hidden files and folders (names starting with `.`)
- Thumbnail folders (`.thumbnails`, `thumbnails`, `.thumb`)
- `.db` and `.nomedia` files
- WhatsApp Sent media (`Sent/` subfolders)

## First-time setup

1. Install [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) and add the folder to your system PATH
2. Connect your phone via USB
3. On the phone: accept the **Allow USB Debugging** prompt
4. Verify with `adb devices` — your device should appear

## Building an EXE

```bat
pip install pyinstaller pillow pystray
python generate_icon.py
pyinstaller --onefile --windowed --icon mnemo.ico --name Mnemo mnemo_backup.py
```

The exe will be in the `dist/` folder.

## Releasing

Update `VERSION` and push to `main`:

```bat
echo 1.1.0 > VERSION
git add VERSION
git commit -m "chore: release v1.1.0"
git push
```

GitHub Actions will automatically create the tag, build `Mnemo.exe` and `MnemoCLI.exe`, and publish a GitHub Release.
