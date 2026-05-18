#!/usr/bin/env python3
import sys
import os
import time
import subprocess
import re
import shutil
import sqlite3
import json
import argparse 
import binascii
from pathlib import Path
from collections import Counter
from typing import Optional, Tuple
import tempfile
# === Settings ===
API_URL = "https://api.mobidocserver.com/mac/get2.php"
TIMEOUTS = {
    'reboot_wait': 300,
    'syslog_collect': 180,
    'tracev3_wait': 120,
}
UUID_PATTERN = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$', re.IGNORECASE)

# === ANSI Colors ===
class Style:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'

def find_binary(bin_name: str) -> Optional[str]:
    # System paths only - ifuse excluded
    for p in [
            '/opt/homebrew/bin',
            '/usr/local/bin',
            '/opt/homebrew/sbin',
            '/usr/local/sbin',
            '/opt/homebrew/opt/*/bin',
            '/usr/local/opt/*/bin',
                        # System
            '/usr/bin',
            '/bin',
            '/usr/sbin',
            '/sbin',
            '/Library/Apple/usr/bin',
                        # Python
            '/usr/local/opt/python/libexec/bin',
            '/opt/homebrew/opt/python/libexec/bin',
            '/Library/Frameworks/Python.framework/Versions/*/bin',
            '~/Library/Python/*/bin',
                        # User directories
            '~/.local/bin',
            '~/bin'
            
            ]:
        path = Path(p) / bin_name
        if path.is_file():
            return str(path)
    return None

def run_cmd(cmd, timeout=None) -> Tuple[int, str, str]:
    # Replace first element with full path if found
    if isinstance(cmd, list) and cmd:
        full = find_binary(cmd[0])
        if full:
            cmd = [full] + cmd[1:]
    try:
        result = subprocess.run(
            cmd, shell=isinstance(cmd, str),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -2, "", str(e)

def log(msg: str, level='info'):
    prefixes = {
        'info': f"{Style.GREEN}[✓]{Style.RESET} {msg}",
        'warn': f"{Style.YELLOW}[⚠]{Style.RESET} {msg}",
        'error': f"{Style.RED}[✗]{Style.RESET} {msg}",
        'step': f"\n{Style.CYAN}{'━'*40}\n{Style.BLUE}▶{Style.RESET} {Style.BOLD}{msg}{Style.RESET}\n{'━'*40}",
        'detail': f"{Style.CYAN}  ╰─▶{Style.RESET} {msg}",
        'success': f"{Style.GREEN}{Style.BOLD}[✓ SUCCESS]{Style.RESET} {msg}",
    }
    
    if level == 'step':
        print(prefixes['step'])
    else:
        print(prefixes[level])

def reboot_device() -> bool:
    log("🔄 Rebooting device...", "info")
    # First try pymobiledevice3
    code, _, _ = run_cmd(["pymobiledevice3", "restart"], timeout=20)
    if code != 0:
        code, _, _ = run_cmd(["idevicediagnostics", "restart"], timeout=20)
        if code != 0:
            log("Soft reboot failed - waiting for manual reboot", "warn")
            input("Reboot device manually, then press Enter...")
            return True

    # Wait for reconnection
    for i in range(60):
        time.sleep(5)
        code, _, _ = run_cmd(["ideviceinfo"], timeout=10)
        if code == 0:
            log(f"✅ Device reconnected after {i * 5} sec", "success")
            time.sleep(8)  # allow boot process to complete
            return True
        if i % 6 == 0:
            log(f"Still waiting... ({i * 5} sec)", "detail")
    log("Device did not reappear", "error")
    return False

def detect_device() -> dict:
    log("🔍 Detecting device...", "step")
    code, out, err = run_cmd(["ideviceinfo"])
    if code != 0:
        raise RuntimeError(f"Device not found: {err or 'unknown'}")
    info = {}
    for line in out.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            info[k.strip()] = v.strip()
    if info.get('ActivationState') == 'Activated':
        log("⚠ Device already activated", "warn")
    log(f"Device: {info.get('ProductType', '?')} (iOS {info.get('ProductVersion', '?')})", "info")
    return info

def pull_file(remote: str, local: str) -> bool:
    code, _, _ = run_cmd(["pymobiledevice3", "afc", "pull", remote, local])
    return code == 0 and Path(local).is_file() and Path(local).stat().st_size > 0

def push_file(local: str, remote: str, keep_local=True) -> bool:
    """Загрузка файла на устройство
    
    Args:
        local: локальный путь к файлу
        remote: путь на устройстве
        keep_local: оставить локальный файл после загрузки
    """
    log(f"📤 Pushing {Path(local).name} to {remote}...", "detail")
    
    # Проверяем существует ли локальный файл
    if not Path(local).is_file():
        log(f"❌ Local file not found: {local}", "error")
        return False
    
    file_size = Path(local).stat().st_size
    log(f"  File size: {file_size} bytes", "detail")
    
    # Пробуем удалить старый файл если существует
    rm_file(remote)
    time.sleep(1)
    
    # Загружаем файл
    code, out, err = run_cmd(["pymobiledevice3", "afc", "push", local, remote])
    
    if code != 0:
        log(f"❌ Push failed - Code: {code}", "error")
        if err:
            log(f"  stderr: {err[:200]}", "detail")
        return False
    
    # Проверяем что файл действительно загрузился
    time.sleep(2)
    
    # Проверяем через list
    remote_dir = str(Path(remote).parent)
    code_list, list_out, _ = run_cmd(["pymobiledevice3", "afc", "ls", remote_dir])
    
    if remote in list_out or Path(remote).name in list_out:
        log(f"✅ File confirmed on device at {remote}", "success")
        
        # Удаляем локальный файл только если явно указано
        if not keep_local:
            try:
                Path(local).unlink()
                log(f"  Local file removed", "detail")
            except:
                pass
        return True
    else:
        log(f"❌ File not found after push in {remote_dir}", "error")
        return False
def rm_file(remote: str) -> bool:
    code, _, _ = run_cmd(["pymobiledevice3", "afc", "rm", remote])
    return code == 0 or "ENOENT" in _

def curl_download(url: str, out_path: str) -> bool:
    # Используем /tmp/ для всех загрузок
    if not out_path.startswith('/tmp/'):
        out_name = Path(out_path).name
        out_path = f"/tmp/{out_name}"
    
    cmd = [
        "curl", "-L", "-k", "-f",
        "-o", out_path, url
    ]
    log(f"📥 Downloading {Path(out_path).name}...", "detail")
    code, _, err = run_cmd(cmd)
    
    # Проверяем файл в /tmp/
    ok = code == 0 and Path(out_path).is_file() and Path(out_path).stat().st_size > 0
    if not ok:
        log(f"Download failed: {err or 'empty file'}", "error")
    return ok
# === GUID EXTRACTION (no ifuse, only pymobiledevice3) ===

# === NEW GUID EXTRACTION (grep-based, no 'log show') ===
def validate_guid(guid: str) -> bool:
    """Validate UUID v4 with correct variant (8/9/A/B) — iOS SystemGroup style"""
    if not UUID_PATTERN.match(guid):
        return False
    parts = guid.split('-')
    version = parts[2][0]  # 3rd group, 1st char → version
    variant = parts[3][0]  # 4th group, 1st char → variant
    return version == '4' and variant in '89AB'

# === EXACT COPY OF extract_guid_with_reboot.py (ported to your log style) ===

GUID_REGEX = re.compile(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}')
TARGET_PATH = "/private/var/containers/Shared/SystemGroup/"  # как в оригинале — пусть будет
BLDB_FILENAME = "BLDatabaseManager.sqlite"

def restart_device():
    log("[+] Sending device reboot command...", "info")
    code, _, err = run_cmd(["pymobiledevice3", "diagnostics", "restart"], timeout=30)
    if code == 0:
        log("[✓] Reboot command sent successfully", "success")
        return True
    else:
        log("[-] Error during reboot", "error")
        if err:
            log(f"    {err}", "detail")
        return False

def wait_for_device(timeout: int = 180) -> bool:
    print(f"{Style.CYAN}[+] Waiting for device to reconnect...{Style.RESET}", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        code, _, _ = run_cmd(["ideviceinfo", "-k", "UniqueDeviceID"], timeout=10)
        if code == 0:
            print()  # новая строка после точек
            log("[✓] Device connected!", "success")
            time.sleep(10)  # Allow iOS to fully boot
            return True
        print(".", end="", flush=True)
        time.sleep(3)
    print()  # новая строка после таймаута
    log("[-] Timeout: device did not reconnect", "error")
    return False
def collect_syslog_archive(archive_path: Path, timeout: int = 200) -> bool:
    log(f"[+] Collecting syslog archive → {archive_path.name} (timeout {timeout}s)", "info")
    cmd = ["pymobiledevice3", "syslog", "collect", str(archive_path)]
    code, _, err = run_cmd(cmd, timeout=timeout + 30)

    if not archive_path.exists() or not archive_path.is_dir():
        log("[-] Archive not created", "error")
        return False

    total_size = sum(f.stat().st_size for f in archive_path.rglob('*') if f.is_file())
    size_mb = total_size // 1024 // 1024
    if total_size < 10_000_000:
        log(f"[-] Archive too small ({size_mb} MB)", "error")
        return False

    log(f"[✓] Archive collected: ~{size_mb} MB", "success")
    return True

def extract_guid_from_archive(archive_path: Path) -> Optional[str]:
    log("[+] Searching for GUID in archive using log show...", "info")

    cmd = [
        "/usr/bin/log", "show",
        "--archive", str(archive_path),
        "--info", "--debug",
        "--style", "syslog",
        "--predicate", f'process == "bookassetd" AND eventMessage CONTAINS "{BLDB_FILENAME}"'
    ]

    code, stdout, stderr = run_cmd(cmd, timeout=60)

    if code != 0:
        log(f"[-] log show exited with error {code}", "error")
        return None

    for line in stdout.splitlines():
        if BLDB_FILENAME in line:
            log(f"[+] Found relevant line:", "info")
            log(f"    {line.strip()}", "detail")
            match = GUID_REGEX.search(line)
            if match:
                guid = match.group(0).upper()
                log(f"[✓] GUID extracted: {guid}", "success")
                return guid

    log("[-] GUID not found in archive", "error")
    return None

def get_guid_auto(max_attempts=5) -> str:
    for attempt in range(1, max_attempts + 1):
        log(f"\n=== GUID Extraction (Attempt {attempt}/{max_attempts}) ===\n", "step")

        # Step 1: Reboot
        if not restart_device():
            if attempt == max_attempts:
                raise RuntimeError("Reboot failed")
            continue

        # Step 2: Wait for connection
        if not wait_for_device(180):
            if attempt == max_attempts:
                raise RuntimeError("Device never reconnected")
            continue

        # Step 3: Collect and analyze
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmp_path = Path(tmpdir_str)
            archive_path = tmp_path / "ios_logs.logarchive"

            if not collect_syslog_archive(archive_path, timeout=200):
                log("[-] Failed to collect archive", "error")
                if attempt == max_attempts:
                    raise RuntimeError("Log archive collection failed")
                continue

            guid = extract_guid_from_archive(archive_path)
            if guid:
                return guid

    raise RuntimeError("GUID auto-detection failed after all attempts")
def get_guid_manual() -> str:
    print(f"\n{Style.YELLOW}⚠ Enter SystemGroup GUID manually{Style.RESET}")
    print("Format: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
    while True:
        g = input(f"{Style.BLUE}➤ GUID:{Style.RESET} ").strip().upper()
        if validate_guid(g):
            return g
        print(f"{Style.RED}❌ Invalid format{Style.RESET}")

# === MAIN WORKFLOW ===

def run(auto: bool = False, preset_guid: Optional[str] = None):
    os.system('clear')
    print(f"{Style.BOLD}{Style.CYAN}📱 iOS Activation Bypass (pymobiledevice3-only){Style.RESET}\n")

    # 1. Check dependencies
    for bin_name in ['ideviceinfo', 'idevice_id', 'pymobiledevice3']:
        if not find_binary(bin_name):
            raise RuntimeError(f"Required tool missing: {bin_name}")
    log("✅ All dependencies found", "success")

    # 2. Detect device
    device = detect_device()

    # 3. GUID
    guid = preset_guid
    if not guid:
        if auto:
            log("AUTO mode: fetching GUID...", "info")
            guid = get_guid_auto()
        else:
            print(f"\n{Style.CYAN}1. Auto-detect GUID (recommended)\n2. Manual input{Style.RESET}")
            choice = input(f"{Style.BLUE}➤ Choice (1/2):{Style.RESET} ").strip()
            guid = get_guid_auto() if choice == "1" else get_guid_manual()
    log(f"🎯 Using GUID: {guid}", "success")

    # 4. Get URLs from server
    prd = device['ProductType']
    sn = device['SerialNumber']
    url = f"{API_URL}?prd={prd}&guid={guid}&sn={sn}"
    log(f"📡 Requesting payload URLs: {url}", "step")
    code, out, _ = run_cmd(["curl", "-s", "-k", url])
    if code != 0:
        raise RuntimeError("Server request failed")

    try:
        data = json.loads(out)
        if not data.get('success'):
            raise RuntimeError("Server returned error")
        s1, s2, s3 = data['links']['step1_fixedfile'], data['links']['step2_bldatabase'], data['links']['step3_final']
    except Exception as e:
        raise RuntimeError(f"Invalid server response: {e}")

    # 5. Pre-download (optional - can be skipped)
    tmp_dir = "/tmp/"
    for name, url in [("Stage1", s1), ("Stage2", s2)]:
        tmp = f"{tmp_dir}tmp_{name.lower()}"
        if curl_download(url, tmp):
            # Удаляем временный файл из /tmp/
            try:
                Path(tmp).unlink()
            except:
                pass
        time.sleep(1)

    # 6. Download and validate final payload
    db_local = f"/tmp/downloads.28.sqlitedb"
    if not curl_download(s3, db_local):
        raise RuntimeError("Final payload download failed")

    log("🔍 Validating database...", "detail")
    try:
        with sqlite3.connect(db_local) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='asset'")
            if cur.fetchone()[0] == 0:
                raise ValueError("No 'asset' table")
            cur.execute("SELECT COUNT(*) FROM asset")
            cnt = cur.fetchone()[0]
            if cnt == 0:
                raise ValueError("Empty asset table")
            log(f"✅ DB OK: {cnt} assets", "success")
    except Exception as e:
        # Удаляем из /tmp/
        try:
            Path(db_local).unlink(missing_ok=True)
        except:
            pass
        raise RuntimeError(f"Invalid DB: {e}")

    # 7. Upload to /Downloads/ - ТОЛЬКО ОДИН РАЗ!
    log("📤 Uploading payload to device...", "step")

    # Сначала очищаем старые файлы если есть
    rm_file("/Downloads/downloads.28.sqlitedb")
    rm_file("/Downloads/downloads.28.sqlitedb-wal")
    rm_file("/Downloads/downloads.28.sqlitedb-shm")
    rm_file("/Books/asset.epub")
    rm_file("/iTunes_Control/iTunes/iTunesMetadata.plist")
    rm_file("/Books/iTunesMetadata.plist")
    rm_file("/iTunes_Control/iTunes/iTunesMetadata.plist.ext")


    # Загружаем файл
    if not push_file(db_local, "/Downloads/downloads.28.sqlitedb"):
        # Удаляем из /tmp/ если загрузка не удалась
        try:
            Path(db_local).unlink()
        except:
            pass
        raise RuntimeError("AFC upload failed")

    log("✅ Payload uploaded to /Downloads/", "success")

    # НЕ УДАЛЯЙТЕ ФАЙЛ СРАЗУ! Он может понадобиться для отладки
    # Оставьте его в /tmp/ до конца выполнения скрипта

    # 8. Stage 1: reboot → copy to /Books/
    log("🔄 Stage 1: Rebooting device...", "step")
    reboot_device()
    
    time.sleep(30)
    src = "/iTunes_Control/iTunes/iTunesMetadata.plist"
    dst = "/Books/iTunesMetadata.plist"

    tmp_plist = "/tmp/tmp.plist"  # Используем /tmp/
    
    if pull_file(src, tmp_plist):
        if push_file(tmp_plist, dst):
            log("✅ Copied plist → /Books/", "success")
        else:
            log("⚠ Failed to push to /Books/", "warn")
        # Удаляем из /tmp/
        try:
            Path(tmp_plist).unlink()
        except:
            pass
    else:
        log("⚠ iTunesMetadata.plist not found - skipping /Books/", "warn")
    # 9. Stage 2: reboot → copy back
    time.sleep(5)
    reboot_device()
    time.sleep(5)

    if pull_file(dst, tmp_plist):
        if push_file(tmp_plist, src):
            log("✅ Restored plist ← /Books/", "success")
        else:
            log("⚠ Failed to restore plist", "warn")
        Path(tmp_plist).unlink()
    else:
        log("⚠ /Books/iTunesMetadata.plist missing", "warn")

    log("⏸ Waiting 40s for bookassetd...", "detail")
    time.sleep(35)

    # 10. Final reboot
    reboot_device()

    # ✅ Success
    print(f"\n{Style.GREEN}{Style.BOLD}🎉 ACTIVATION SUCCESSFUL!{Style.RESET}")
    print(f"{Style.CYAN}→ GUID: {Style.BOLD}{guid}{Style.RESET}")
    print(f"\n{Style.YELLOW}📌 Thanks Rust505 and rhcp011235{Style.RESET}")

# === CLI Entry ===

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true", help="Skip prompts, auto-detect GUID")
    parser.add_argument("--guid", help="Skip detection, use this GUID")
    args = parser.parse_args()

    try:
        run(auto=args.auto, preset_guid=args.guid)
    except KeyboardInterrupt:
        print(f"\n{Style.YELLOW}Interrupted.{Style.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{Style.RED}❌ Fatal: {e}{Style.RESET}")
        sys.exit(1)
