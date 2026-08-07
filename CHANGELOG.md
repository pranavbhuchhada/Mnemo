## [1.1.0] - 2026-08-07
- feat: switch to ppadb for persistent ADB connection, auto changelog on release
- fix: readme.md

# Changelog

## [1.0.0] - 2026-08-07

### Added
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
- Persistent ADB connection via ppadb — no cmd window flashing
