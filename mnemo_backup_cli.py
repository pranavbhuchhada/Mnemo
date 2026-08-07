#!/usr/bin/env python3
"""Mnemo — Android backup tool using ADB, incremental CLI version."""

import subprocess
import sys
import os

from ppadb.client import Client as AdbClient

COMMON_FOLDERS = [
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Music",
    "/sdcard/Documents",
    "/sdcard/Download",
    "/sdcard/Android/media/com.whatsapp/WhatsApp",
]

SKIP_DIRS       = {".thumbnails", "thumbnails", ".thumb"}
SKIP_EXTENSIONS = {".db", ".nomedia"}
WHATSAPP_SENT_DIRS = {"sent"}


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _start_adb_server():
    subprocess.run(["adb", "start-server"], capture_output=True, creationflags=_NO_WINDOW)


def check_adb() -> bool:
    try:
        _start_adb_server()
        AdbClient(host="127.0.0.1", port=5037).version()
        return True
    except Exception:
        return False


def get_connected_device():
    try:
        _start_adb_server()
        devices = AdbClient(host="127.0.0.1", port=5037).devices()
        return devices[0] if devices else None
    except Exception:
        return None


def get_device_name(device) -> str | None:
    try:
        manufacturer = device.shell("getprop ro.product.manufacturer").strip()
        model        = device.shell("getprop ro.product.model").strip()
        if manufacturer and model:
            if model.lower().startswith(manufacturer.lower()):
                return model
            return f"{manufacturer.capitalize()} {model}"
        return model or manufacturer or None
    except Exception:
        return None


def prompt_destination() -> str:
    print("\nEnter the local destination folder (e.g. D:\\Backup):")
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

        all_option    = len(COMMON_FOLDERS) + 2
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
                print("  Enter custom folder paths (comma-separated):")
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


def get_remote_files(device, src: str, skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True) -> list[tuple[str, int]]:
    try:
        output = device.shell(f"find '{src}' -type f -printf '%s %p\\n' 2>/dev/null")
    except Exception:
        return []

    files = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        size_str, _, path = line.partition(" ")
        try:
            size = int(size_str)
        except ValueError:
            continue

        parts = path.replace("\\", "/").split("/")
        if any(p.lower() in SKIP_DIRS for p in parts):
            continue
        if skip_hidden and any(p.startswith(".") for p in parts):
            continue
        if skip_whatsapp_sent and any(p.lower() in WHATSAPP_SENT_DIRS for p in parts):
            continue
        if os.path.splitext(path)[1].lower() in SKIP_EXTENSIONS:
            continue

        files.append((path, size))
    return files


def incremental_pull(device, src: str, dest: str, skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True) -> tuple[int, int, int]:
    folder_name = src.rstrip("/").split("/")[-1]
    local_base  = os.path.join(dest, folder_name)

    print(f"\n  Scanning {src}...")
    remote_files = get_remote_files(device, src,
                                    skip_whatsapp_sent=skip_whatsapp_sent,
                                    skip_hidden=skip_hidden)

    if not remote_files:
        print(f"  No files found in {src}")
        return 0, 0, 0

    to_pull = []
    skipped = 0
    for remote_path, remote_size in remote_files:
        rel        = remote_path[len(src):].lstrip("/")
        local_path = os.path.join(local_base, rel.replace("/", os.sep))
        if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
            skipped += 1
        else:
            to_pull.append((remote_path, rel, local_path))

    total = len(remote_files)
    width = len(str(len(to_pull)))
    print(f"  {total} files — {skipped} already backed up, {len(to_pull)} to pull")

    if not to_pull:
        return 0, skipped, 0

    pulled = failed = 0
    for i, (remote_path, rel, local_path) in enumerate(to_pull, 1):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            device.pull(remote_path, local_path)
            pulled += 1
            print(f"  [{i:>{width}}/{len(to_pull)}] {rel}")
        except Exception as e:
            failed += 1
            print(f"  [{i:>{width}}/{len(to_pull)}] FAILED: {rel} ({e})")

    return pulled, skipped, failed


def main():
    print("=" * 50)
    print("  Mnemo — Incremental Android Backup")
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

    name = get_device_name(device) or str(device)
    print(f"  Device found: {name}")

    dest    = prompt_destination()
    folders = prompt_folders()

    skip_sent = False
    if any("whatsapp" in f.lower() for f in folders):
        ans = input("\nSkip WhatsApp Sent media? [Y/n]: ").strip().lower()
        skip_sent = ans in ("", "y", "yes")

    ans = input("Skip hidden files and folders? [Y/n]: ").strip().lower()
    skip_hidden = ans in ("", "y", "yes")

    print(f"\nExporting {len(folders)} folder(s) to: {dest}")
    print("-" * 50)

    total_pulled = total_skipped = total_failed = 0
    for folder in folders:
        pulled, skipped, failed = incremental_pull(
            device, folder, dest,
            skip_whatsapp_sent=skip_sent,
            skip_hidden=skip_hidden,
        )
        total_pulled  += pulled
        total_skipped += skipped
        total_failed  += failed

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
