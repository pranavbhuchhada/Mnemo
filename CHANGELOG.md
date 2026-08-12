## [1.1.1] - 2026-08-12
- feat: auto-detect device brand and show brand-specific folders
- chore: updated changelog

# Changelog

## [1.1.0] - 2026-08-07
- fix: workflow re-run issues
- fix: removed workflow icon generation
- feat: switch to ppadb for persistent ADB connection, auto changelog on release
- fix: readme.md

## [1.0.0] - 2026-08-07
- Initial release
- Incremental backup via ADB — skips files already on PC (matched by size)
- GUI with dark theme, two-column layout, color-coded log
- CLI version
- Browse phone folders directly from the GUI
- Progress bar with file count, percentage, and ETA
- System tray icon — minimize to tray, restore with a click
- Desktop notification on backup complete
- Stop button — halts cleanly after current file
- Skip hidden files and folders
- Skip WhatsApp Sent media
- Shows connected device name
- Remembers last destination folder
- DPI-aware rendering on high-resolution displays
