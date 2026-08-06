#!/usr/bin/env python3
"""Android backup tool using ADB — incremental export of selected phone folders to local PC."""

import subprocess
import sys
import os


COMMON_FOLDERS = [
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Music",
    "/sdcard/Documents",
    "/sdcard/Download",
    "/sdcard/Android/media/com.whatsapp/WhatsApp",
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")


def check_adb() -> bool:
    try:
        return run(["adb", "version"]).returncode == 0
    except FileNotFoundError:
        return False


def get_connected_device() -> str | None:
    result = run(["adb", "devices"])
    lines = result.stdout.strip().splitlines()
    devices = [l for l in lines[1:] if l.strip() and "device" in l and "offline" not in l]
    return devices[0].split()[0] if devices else None


def prompt_destination() -> str:
    print("\nEnter the local destination folder (e.g. D:\\AndroidBackup):")
    while True:
        dest = input("  Destination: ").strip().strip('"')
        if not dest:
            print("  Destination cannot be empty.")
            continue
        if not os.path.exists(dest):
            create = input(f"  Folder '{dest}' does not exist. Create it? [Y/n]: ").strip().lower()
            if create in ("", "y", "yes"):
                os.makedirs(dest, exist_ok=True)
                print(f"  Created: {dest}")
            else:
                continue
        return dest


def prompt_folders() -> list[str]:
    print("\nCommon Android folders:")
    for i, folder in enumerate(COMMON_FOLDERS, 1):
        print(f"  {i}. {folder}")
    print(f"  {len(COMMON_FOLDERS) + 1}. Enter custom folder path(s)")
    print(f"  {len(COMMON_FOLDERS) + 2}. Export ALL common folders")

    print("\nSelect folders to export (comma-separated numbers, e.g. 1,3,5):")
    while True:
        choice = input("  Choice: ").strip()
        if not choice:
            print("  Please enter at least one choice.")
            continue

        all_option = len(COMMON_FOLDERS) + 2
        custom_option = len(COMMON_FOLDERS) + 1

        try:
            nums = [int(x.strip()) for x in choice.split(",")]
        except ValueError:
            print("  Invalid input. Enter numbers separated by commas.")
            continue

        if all_option in nums:
            return COMMON_FOLDERS[:]

        selected = []
        for num in nums:
            if 1 <= num <= len(COMMON_FOLDERS):
                selected.append(COMMON_FOLDERS[num - 1])
            elif num == custom_option:
                print("  Enter custom folder paths (comma-separated, e.g. /sdcard/Telegram,/sdcard/Signal):")
                custom = input("  Custom paths: ").strip()
                for path in custom.split(","):
                    path = path.strip()
                    if path:
                        selected.append(path)
            else:
                print(f"  Skipping invalid option: {num}")

        if not selected:
            print("  No valid folders selected. Try again.")
            continue

        return selected


SKIP_DIRS = {".thumbnails", "thumbnails", ".thumb"}
SKIP_EXTENSIONS = {".db", ".nomedia"}
WHATSAPP_SENT_DIRS = {"sent"}


def get_remote_files(src: str, skip_whatsapp_sent: bool = False) -> list[tuple[str, int]]:
    """List all files under src on device with their sizes. Returns [(path, size_bytes)]."""
    result = run(["adb", "shell", f"find '{src}' -type f -printf '%s %p\\n' 2>/dev/null"])
    if not result.stdout.strip():
        return []

    files = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        size_str, _, path = line.partition(" ")
        try:
            size = int(size_str)
        except ValueError:
            continue

        parts = path.replace("\\", "/").split("/")
        if any(p.lower() in SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        if skip_whatsapp_sent and any(p.lower() in WHATSAPP_SENT_DIRS for p in parts):
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in SKIP_EXTENSIONS:
            continue

        files.append((path, size))
    return files


def incremental_pull(src: str, dest: str, skip_whatsapp_sent: bool = False) -> tuple[int, int, int]:
    """Pull only new or changed files. Returns (pulled, skipped, failed)."""
    folder_name = src.rstrip("/").split("/")[-1]
    local_base = os.path.join(dest, folder_name)

    print(f"\n  Scanning {src}...")
    remote_files = get_remote_files(src, skip_whatsapp_sent=skip_whatsapp_sent)

    if not remote_files:
        print(f"  No files found in {src}")
        return 0, 0, 0

    to_pull = []
    skipped = 0
    for remote_path, remote_size in remote_files:
        rel = remote_path[len(src):].lstrip("/")
        local_path = os.path.join(local_base, rel.replace("/", os.sep))
        if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
            skipped += 1
        else:
            to_pull.append((remote_path, rel, local_path))

    total = len(remote_files)
    width = len(str(len(to_pull)))
    print(f"  {total} files found — {skipped} already backed up, {len(to_pull)} to pull")

    if not to_pull:
        return 0, skipped, 0

    pulled = failed = 0
    for i, (remote_path, rel, local_path) in enumerate(to_pull, 1):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        result = subprocess.run(
            ["adb", "pull", remote_path, local_path],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            pulled += 1
            print(f"  [{i:>{width}}/{len(to_pull)}] {rel}")
        else:
            failed += 1
            print(f"  [{i:>{width}}/{len(to_pull)}] FAILED: {rel}")

    return pulled, skipped, failed


def main():
    print("=" * 50)
    print("  Android Backup Tool (ADB) — Incremental")
    print("=" * 50)

    if not check_adb():
        print("\nERROR: 'adb' not found. Install Android Platform Tools and add to PATH.")
        print("Download: https://developer.android.com/tools/releases/platform-tools")
        sys.exit(1)

    print("\nChecking for connected Android device...")
    device = get_connected_device()
    if not device:
        print("\nERROR: No device connected.")
        print("  1. Connect your phone via USB")
        print("  2. Enable USB Debugging (Settings > Developer Options)")
        print("  3. Accept the RSA key prompt on your phone")
        sys.exit(1)

    print(f"  Device found: {device}")

    dest = prompt_destination()
    folders = prompt_folders()

    skip_sent = False
    if any("whatsapp" in f.lower() for f in folders):
        ans = input("\nSkip WhatsApp Sent media? [Y/n]: ").strip().lower()
        skip_sent = ans in ("", "y", "yes")

    print(f"\nExporting {len(folders)} folder(s) to: {dest}")
    print("-" * 50)

    total_pulled = total_skipped = total_failed = 0
    for folder in folders:
        pulled, skipped, failed = incremental_pull(folder, dest, skip_whatsapp_sent=skip_sent)
        total_pulled += pulled
        total_skipped += skipped
        total_failed += failed

    print("\n" + "=" * 50)
    print("  Summary")
    print("=" * 50)
    print(f"  Pulled (new):     {total_pulled}")
    print(f"  Skipped (exists): {total_skipped}")
    if total_failed:
        print(f"  Failed:           {total_failed}")
    print("\nDone.")


if __name__ == "__main__":
    main()
