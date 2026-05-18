#!/usr/bin/env python3
# extract_guid_with_reboot.py

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GUID_REGEX = re.compile(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}')
TARGET_PATH = "/private/var/containers/Shared/SystemGroup/"
BLDB_FILENAME = "BLDatabaseManager.sqlite"


def run_command(cmd, timeout=None):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Timeout expired"


def restart_device():
    print("[+] Sending device reboot command...")
    code, out, err = run_command(["pymobiledevice3", "diagnostics", "restart"], timeout=30)
    if code == 0:
        print("[✓] Reboot command sent successfully")
        return True
    else:
        print("[-] Error during reboot")
        if err: print(f"    {err}")
        return False


def wait_for_device(timeout: int = 180):
    print("[+] Waiting for device to reconnect...", end="")
    start = time.time()
    while time.time() - start < timeout:
        code, _, _ = run_command(["ideviceinfo", "-k", "UniqueDeviceID"], timeout=10)
        if code == 0:
            print("\n[✓] Device connected!")
            time.sleep(10)  # Allow iOS to fully boot
            return True
        print(".", end="", flush=True)
        time.sleep(3)
    print("\n[-] Timeout: device did not reconnect")
    return False


def collect_syslog_archive(archive_path: Path, timeout: int = 200):
    print(f"[+] Collecting syslog archive → {archive_path.name} (timeout {timeout}s)")
    cmd = ["pymobiledevice3", "syslog", "collect", str(archive_path)]
    code, stdout, stderr = run_command(cmd, timeout=timeout + 30)

    if not archive_path.exists() or not archive_path.is_dir():
        print("[-] Archive not created")
        return False

    total_size = sum(f.stat().st_size for f in archive_path.rglob('*') if f.is_file())
    size_mb = total_size // 1024 // 1024
    if total_size < 10_000_000:
        print(f"[-] Archive too small ({size_mb} MB)")
        return False

    print(f"[✓] Archive collected: ~{size_mb} MB")
    return True


def extract_guid_from_archive(archive_path: Path) -> str | None:
    print("[+] Searching for GUID in archive using log show...")

    # Improved filter — searching specifically for bookassetd and BLDatabaseManager
    cmd = [
        "/usr/bin/log", "show",
        "--archive", str(archive_path),
        "--info", "--debug",
        "--style", "syslog",
        "--predicate", f'process == "bookassetd" AND eventMessage CONTAINS "{BLDB_FILENAME}"'
    ]

    code, stdout, stderr = run_command(cmd, timeout=60)

    if code != 0:
        print(f"[-] log show exited with error {code}")
        return None

    for line in stdout.splitlines():
        if BLDB_FILENAME in line:
            print(f"[+] Found relevant line:")
            print(f"    {line.strip()}")
            match = GUID_REGEX.search(line)
            if match:
                guid = match.group(0).upper()
                print(f"[✓] GUID extracted: {guid}")
                return guid

    print("[-] GUID not found in archive")
    return None


def main():
    print("\n=== GUID Extraction (with reboot, as in original script) ===\n")

    # Step 1: Reboot
    if not restart_device():
        sys.exit(1)

    # Step 2: Wait for connection
    if not wait_for_device(180):
        sys.exit(1)

    # Step 3: Collect and analyze logs
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmp_path = Path(tmpdir_str)
        archive_path = tmp_path / "ios_logs.logarchive"

        if not collect_syslog_archive(archive_path, timeout=200):
            print("[-] Failed to collect archive")
            sys.exit(1)

        guid = extract_guid_from_archive(archive_path)
        if guid:
            print(f"\n[✓] SUCCESS! GUID = {guid}")
            sys.exit(0)

    print("\n[-] GUID not found")
    sys.exit(1)


if __name__ == "__main__":
    main()