# -*- coding: utf-8 -*-
import os
import sys
import re
import time
import json
import struct
import shutil
import sqlite3
import tempfile
import binascii
import threading
import datetime
import subprocess
import urllib.parse
from collections import Counter
from typing import Optional
from pathlib import Path

from PySide6.QtCore import Qt, QCoreApplication, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QFont, QIcon, QPixmap, QPainter, QColor, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QTextEdit, QMessageBox, QVBoxLayout, QHBoxLayout, QProgressBar, QDialog
)

# Colorama fallback
try:
    from colorama import Fore, Style
except ImportError:
    class DummyColor:
        def __getattr__(self, _): return ''
    Fore = Style = DummyColor()


# ——— Utility ———
def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)
    if getattr(sys, "frozen", False):
        macos_dir = Path(sys.executable).resolve().parent
        for base in [macos_dir, macos_dir.parent / "Resources"]:
            p = base / relative_path
            if p.exists():
                return str(p)
        raise FileNotFoundError(relative_path)
    return str(Path(__file__).resolve().parent / relative_path)


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SuccessDialog(QDialog):
    def __init__(self, parent=None, device_name="", ios_version=""):
        super().__init__(parent)
        self.setWindowTitle("MobiDoc A12+")
        self.setFixedSize(400, 150)
        self.setStyleSheet("""
            QDialog { background-color: #000000; border-radius: 12px; }
            QLabel  { color: white; border: none; background: transparent; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(64, 64)
        logo_path = resource_path("img/logo.icns")  # icône de l'application
        if os.path.exists(logo_path):
            src = QPixmap(logo_path).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix = QPixmap(64, 64)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, 64, 64, 14, 14)
            p.setClipPath(path)
            p.drawPixmap(0, 0, src)
            p.end()
        else:
            pix = QPixmap(64, 64)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, 64, 64)
            p.setClipPath(path)
            p.fillRect(0, 0, 64, 64, QColor("#2196F3"))
            p.setPen(QColor("white"))
            p.setFont(QFont("Arial", 18, QFont.Bold))
            p.drawText(pix.rect(), Qt.AlignCenter, "M")
            p.end()
        icon_lbl.setPixmap(pix)
        layout.addWidget(icon_lbl)

        right = QVBoxLayout()
        right.setSpacing(6)

        title = QLabel("MobiDoc A12+")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: white;")

        msg = QLabel(f"Your Device {device_name} iOS {ios_version}\nhas been Activated Successfully! 🎉")
        msg.setStyleSheet("font-size: 12px; color: #cccccc;")
        msg.setWordWrap(True)

        ok_btn = QPushButton("Ok")
        ok_btn.setFixedWidth(70)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton:hover   { background-color: #1976D2; }
            QPushButton:pressed { background-color: #0D47A1; }
        """)
        ok_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)

        right.addWidget(title)
        right.addWidget(msg)
        right.addLayout(btn_row)
        layout.addLayout(right)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MobiDoc A12+")
        self.setFixedSize(500, 280)

        # Charger police
        font_path = resource_path("fonts/FuturaCyrillicBold.ttf")
        if os.path.exists(font_path):
            QFontDatabase.addApplicationFont(font_path)

        # Icône de la fenêtre (et du dock via app)
        icon_path = resource_path("img/logo.icns")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Variables d'état (inchangées)
        self.api_url = "https://api.mobidocserver.com/mac/get2.php"
        self.timeouts = {
            'asset_wait': 300,
            'asset_delete_delay': 15,
            'reboot_wait': 300,
            'syslog_collect': 180,
            'log_show_timeout': 60,
        }
        self.device_info = {}
        self.guid = None
        self.attempt_count = 0
        self.max_attempts = 5
        self.global_GUID = ""
        self.BLDB_FILENAME = "BLDatabaseManager.sqlite"
        self.GUID_REGEX = re.compile(r'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', re.IGNORECASE)
        self.temp_dir = tempfile.gettempdir()

        # UI - Nouvelle interface type Mobidoc
        self.centralwidget = QWidget()
        self.setCentralWidget(self.centralwidget)
        layout = QVBoxLayout(self.centralwidget)
        layout.setSpacing(8)

        self.lbl_uuid = QLabel("")
        self.lbl_device = QLabel("")
        self.lbl_ecid = QLabel("")
        self.lbl_imei_sn = ClickableLabel("")
        self.lbl_imei_sn.clicked.connect(self._copy_sn)
        self.lbl_imei_sn.setToolTip("Click to copy Serial Number")

        for lbl in (self.lbl_uuid, self.lbl_device, self.lbl_ecid, self.lbl_imei_sn):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: white; font-size: 11px;")

        # Barre de progression (style original avec QFrame)
        self.pbFrame = QFrame()
        self.pbFrame.setFixedHeight(12)
        self.pbFrame.setStyleSheet("background-color: rgb(2, 33, 51); border-radius: 5px;")
        self.pb = QFrame(self.pbFrame)
        self.pb.setGeometry(0, 0, 0, 12)
        self.pb.setStyleSheet("background-color: rgb(19, 159, 255); border-radius: 5px;")
        self.pbFrame.raise_()
        self.pb.raise_()

        self.status_label = QLabel("No device connected")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: white; font-size: 11px;")

        self.activateButton = QPushButton("Activate Device")
        self.activateButton.setEnabled(False)
        self.activateButton.setCursor(Qt.PointingHandCursor)
        self.activateButton.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:hover   { background-color: #1976D2; }
            QPushButton:pressed { background-color: #0D47A1; }
        """)

        layout.addWidget(self.lbl_uuid)
        layout.addWidget(self.lbl_device)
        layout.addWidget(self.lbl_ecid)
        layout.addWidget(self.lbl_imei_sn)
        layout.addSpacing(8)
        layout.addWidget(self.pbFrame)
        layout.addWidget(self.status_label)
        layout.addWidget(self.activateButton)

        self.pbFrame.hide()  # cachée tant que l'activation n'a pas commencé

        # Connexions
        self.activateButton.clicked.connect(self.StartThread)

        # Timer de détection device (affichage)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_device)
        self.poll_timer.start(1000)

        self._current_sn = ""
        self._current_udid = ""

        # Démarrer le watcher en arrière-plan (SearchingDevices original)
        threading.Thread(target=self.SearchingDevices, daemon=True).start()

        # Console (non utilisée dans cette UI, mais conservée pour compatibilité)
        self.console = None

    def _copy_sn(self):
        if self._current_sn:
            QApplication.clipboard().setText(self._current_sn)
            self.lbl_imei_sn.setStyleSheet("color: #2196F3; font-size: 11px;")
            QTimer.singleShot(1000, lambda: self.lbl_imei_sn.setStyleSheet("color: white; font-size: 11px;"))

    def poll_device(self):
        """Met à jour l'affichage depuis self.device_info (rempli par SearchingDevices)"""
        info = self.device_info
        if not info:
            self.lbl_uuid.setText("")
            self.lbl_device.setText("")
            self.lbl_ecid.setText("")
            self.lbl_imei_sn.setText("")
            self.status_label.setText("No device connected")
            self.status_label.setVisible(True)
            self.activateButton.setEnabled(False)
            return

        product = info.get("ProductType", "")
        ios = info.get("iOSVersion", "")
        udid = info.get("UDID", "")
        sn = info.get("SerialNumber", "")
        imei = info.get("IMEI", "")
        ecid = info.get("UniqueChipID", udid)

        self._current_sn = sn
        self._current_udid = udid

        self.lbl_uuid.setText(f"APP_UUID: {udid}")
        self.lbl_device.setText(f"Connected Device: {product}  iOS {ios}")
        self.lbl_ecid.setText(f"ECID: {ecid}")
        self.lbl_imei_sn.setText(f"IMEI {imei}  SN: {sn} 📋")
        self.status_label.setVisible(False)
        self.activateButton.setEnabled(True)

    # ---------- Toutes les méthodes métier ORIGINALES ----------
    def _run_cmd(self, cmd, timeout=None):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except subprocess.TimeoutExpired:
            return 124, "", "Timeout"
        except Exception as e:
            return 1, "", str(e)

    def _curl_download(self, url, filename):
        full_path = os.path.join(self.temp_dir, filename)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except:
                pass
        curl_cmd = ["curl", "-L", "-k", "-f", "-o", full_path, url]
        self.log(f"Starting download: {' '.join(curl_cmd)}", "info")
        code, out, err = self._run_cmd(curl_cmd)
        self.log(f"cURL exit code: {code}", "info")
        if code != 0:
            if err:
                self.log(f"cURL error: {err.strip()}", "error")
            self.log("Download failed", "error")
            return False
        if os.path.exists(full_path) and os.path.getsize(full_path) > 100:
            size_mb = os.path.getsize(full_path) / (1024 * 1024)
            self.log(f"Successfully downloaded {filename}: ~{size_mb:.2f} MB", "success")
            return full_path
        else:
            self.log("Downloaded file is empty or missing", "error")
            return False

    def reboot_device(self):
        self.log("Rebooting device...", "info")
        code, _, err = self._run_cmd(["pymobiledevice3", "restart"])
        if code != 0:
            code, _, err = self._run_cmd(["idevicediagnostics", "restart"])
            if code != 0:
                self.log(f"Soft reboot failed: {err}", "warn")
                self.log("Please reboot device manually and press Enter to continue...", "warn")
                input()
                return True
        self.log("Reboot command sent. Waiting for device to reconnect...", "info")
        for i in range(60):
            time.sleep(5)
            code, _, _ = self._run_cmd(["ideviceinfo"])
            if code == 0:
                self.log(f"Device reconnected after {i * 5} seconds", "success")
                time.sleep(10)
                return True
            if i % 6 == 0:
                self.log(f"Still waiting... ({i * 5} seconds)", "info")
        self.log("Device did not reconnect in time", "error")
        return False

    def _wait_for_device(self, timeout_sec: int) -> bool:
        start = time.time()
        while time.time() - start < timeout_sec:
            code, _, _ = self._run_cmd(["ideviceinfo"], timeout=5)
            if code == 0:
                self.log(f"Device reconnected after {int(time.time() - start)}s", "success")
                time.sleep(5)
                return True
            time.sleep(2)
        self.log(f"Timed out waiting for device ({timeout_sec}s)", "error")
        return False

    def verify_dependencies(self):
        self.log("Verifying system dependencies...", "info")
        self.afc_mode = "pymobiledevice3"
        self.log(f"AFC Transfer Mode: {self.afc_mode}", "info")

    def _cleanup(self):
        pass

    def detect_device(self):
        self.log("Detecting device...", "info")
        code, out, err = self._run_cmd(["ideviceinfo"])
        if code != 0:
            self.log(f"Device not found. Error: {err or 'Unknown'}", "error")
            sys.exit(1)
        info = {}
        for line in out.splitlines():
            if ": " in line:
                key, val = line.split(": ", 1)
                info[key.strip()] = val.strip()
        self.device_info = info
        udid = info.get('UniqueDeviceID', '?')
        self.log(f"UDID: {udid}", "info")
        if info.get('ActivationState') == 'Activated':
            self.log("⚠ Warning: Device is already activated", "warn")

    def collect_syslog_archive(self, archive_path: str, timeout: int = 200) -> bool:
        self.log(f"[+] Collecting syslog archive → {os.path.basename(archive_path)} (timeout {timeout}s)", "info")
        cmd = ["pymobiledevice3", "syslog", "collect", archive_path]
        code, _, err = self._run_cmd(cmd, timeout=timeout + 30)
        if not os.path.isdir(archive_path):
            self.log("[-] Archive directory not created", "error")
            return False
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, f))
            for dirpath, _, filenames in os.walk(archive_path)
            for f in filenames
            if os.path.isfile(os.path.join(dirpath, f))
        )
        size_mb = total_size // (1024 * 1024)
        if total_size < 10_000_000:
            self.log(f"[-] Archive too small ({size_mb} MB)", "error")
            return False
        self.log(f"[✓] Archive collected: ~{size_mb} MB", "success")
        return True

    def extract_guid_from_archive(self, archive_path: str) -> Optional[str]:
        self.log("[+] Searching for GUID in archive using 'log show'...", "info")
        if not shutil.which("/usr/bin/log"):
            self.log("[-] '/usr/bin/log' not found — skipping log-show method", "warn")
            return None
        cmd = [
            "/usr/bin/log", "show",
            "--archive", archive_path,
            "--info", "--debug",
            "--style", "syslog",
            "--predicate", f'process == "bookassetd" AND eventMessage CONTAINS "{self.BLDB_FILENAME}"'
        ]
        code, stdout, stderr = self._run_cmd(cmd, timeout=self.timeouts['log_show_timeout'])
        if code != 0:
            self.log(f"[-] log show failed (code {code}): {stderr}", "error")
            return None
        for line in stdout.splitlines():
            if self.BLDB_FILENAME in line:
                self.log("[+] Found relevant line", "info")
                self.log(f" {line.strip()}", "info")
                match = self.GUID_REGEX.search(line)
                if match:
                    guid = match.group(0).upper()
                    self.log(f"[✓] GUID extracted: {guid}", "success")
                    return guid
        self.log("[-] GUID not found in archive", "error")
        return None

    def get_guid_auto_new(self, max_attempts: int = 5) -> Optional[str]:
        for attempt in range(1, max_attempts + 1):
            self.log(f"\n=== GUID Extraction (Attempt {attempt}/{max_attempts}) ===\n", "attempt")
            if not self.reboot_device():
                if attempt == max_attempts:
                    self.log("[-] Final reboot failed — aborting", "error")
                    return None
                self.log("[-] Reboot failed — retrying...", "warn")
                continue
            if not self._wait_for_device(180):
                if attempt == max_attempts:
                    self.log("[-] Device never reconnected — aborting", "error")
                    return None
                self.log("[-] Device not found — retrying...", "warn")
                continue
            with tempfile.TemporaryDirectory() as tmpdir:
                archive_path = os.path.join(tmpdir, "ios_logs.logarchive")
                if not self.collect_syslog_archive(archive_path, timeout=200):
                    self.log("[-] Failed to collect syslog archive", "error")
                    if attempt == max_attempts:
                        return None
                    continue
                guid = self.extract_guid_from_archive(archive_path)
                if guid and self.validate_guid_structure(guid):
                    self.global_GUID = guid
                    return guid
        self.log("[-] All attempts exhausted: GUID detection failed", "error")
        return None

    def get_guid_auto(self):
        self.log("Trying NEW method (log show + archive parsing)...", "info")
        guid = self.get_guid_auto_new(max_attempts=3)
        if guid:
            return guid
        self.log("⚠ NEW method failed — falling back to legacy tracev3 parsing...", "warn")
        return self.get_guid_auto_with_retry()

    def get_guid_manual(self):
        print(f"\n⚠ GUID Input Required")
        print(" Format: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
        print(" Example: 2A22A82B-C342-444D-972F-5270FB5080DF")
        UUID_PATTERN = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$', re.IGNORECASE)
        while True:
            guid_input = input("\n➤ Enter SystemGroup GUID: ").strip()
            if UUID_PATTERN.match(guid_input):
                return guid_input.upper()
            print("❌ Invalid format. Must be 8-4-4-4-12 hex chars (e.g. 2A22A82B-C342-444D-972F-5270FB5080DF).")

    def parse_tracev3_structure(self, data):
        signatures = []
        db_patterns = [
            b'BLDatabaseManager',
            b'BLDatabase',
            b'BLDatabaseManager.sqlite',
            b'bookassetd [Database]: Store is at file:///private/var/containers/Shared/SystemGroup',
        ]
        for pattern in db_patterns:
            pos = 0
            while True:
                pos = data.find(pattern, pos)
                if pos == -1:
                    break
                signatures.append(('string', pattern, pos))
                pos += len(pattern)
        return signatures

    def extract_guid_candidates(self, data, context_pos, window_size=512):
        candidates = []
        guid_pattern = re.compile(
            rb'([0-9A-F]{8}[-][0-9A-F]{4}[-][0-9A-F]{4}[-][0-9A-F]{4}[-][0-9A-F]{12})',
            re.IGNORECASE
        )
        start = max(0, context_pos - window_size)
        end = min(len(data), context_pos + window_size)
        context_data = data[start:end]
        for match in guid_pattern.finditer(context_data):
            guid = match.group(1).decode('ascii').upper()
            relative_pos = match.start() + start - context_pos
            if self.validate_guid_structure(guid):
                candidates.append({
                    'guid': guid,
                    'position': relative_pos,
                    'context': self.get_context_string(context_data, match.start(), match.end())
                })
        return candidates

    def validate_guid_structure(self, guid):
        try:
            parts = guid.split('-')
            if len(parts) != 5:
                return False
            if not (len(parts[0]) == 8 and len(parts[1]) == len(parts[2]) == len(parts[3]) == 4 and len(parts[4]) == 12):
                return False
            hex_chars = set('0123456789ABCDEF')
            clean = guid.replace('-', '')
            if not all(c in hex_chars for c in clean):
                return False
            if parts[2][0] != '4':
                return False
            if parts[3][0] not in '89AB':
                return False
            return True
        except Exception:
            return False

    def get_context_string(self, data, start, end, context_size=50):
        context_start = max(0, start - context_size)
        context_end = min(len(data), end + context_size)
        context = data[context_start:context_end]
        try:
            return context.decode('utf-8', errors='replace')
        except:
            return binascii.hexlify(context).decode('ascii')

    def analyze_guid_confidence(self, guid_candidates):
        if not guid_candidates:
            return None
        guid_counts = Counter(candidate['guid'] for candidate in guid_candidates)
        scored_guids = []
        for guid, count in guid_counts.items():
            score = count * 10
            positions = [c['position'] for c in guid_candidates if c['guid'] == guid]
            close_positions = [p for p in positions if abs(p) < 100]
            if close_positions:
                score += len(close_positions) * 5
            before_positions = [p for p in positions if p < 0]
            if before_positions:
                score += len(before_positions) * 3
            scored_guids.append((guid, score, count))
        scored_guids.sort(key=lambda x: x[1], reverse=True)
        return scored_guids

    def confirm_guid_manual(self, guid):
        self.log(f"GUID successfully parsed! {guid}", type="success")
        self.global_GUID = guid
        return True

    def get_guid_enhanced(self):
        self.attempt_count += 1
        self.log(f"GUID search attempt {self.attempt_count}/{self.max_attempts}", "attempt")
        udid = self._current_udid
        log_path = f"{udid}.logarchive"
        try:
            self.activateButton.setText(f"⏳ Searching GUID (Attempt {self.attempt_count} / {self.max_attempts}) ...")
            code, _, err = self._run_cmd(["pymobiledevice3", "syslog", "collect", log_path], timeout=120)
            if code != 0:
                self.log(f"Log collection failed: {err}", "error")
                return None
            trace_file = os.path.join(log_path, "logdata.LiveData.tracev3")
            if not os.path.exists(trace_file):
                self.log("tracev3 file not found", "error")
                return None
            with open(trace_file, 'rb') as f:
                data = f.read()
            size_mb = len(data) / (1024 * 1024)
            self.log(f"Analyzing tracev3 ({size_mb:.1f} MB)...", "info")
            signatures = self.parse_tracev3_structure(data)
            self.log(f"Found {len(signatures)} relevant signatures", "info")
            all_candidates = []
            for sig_type, pattern, pos in signatures:
                if pattern == b'BLDatabaseManager':
                    candidates = self.extract_guid_candidates(data, pos)
                    all_candidates.extend(candidates)
                    if candidates:
                        self.log(f"Found {len(candidates)} GUID candidates near BLDatabaseManager at 0x{pos:x}", "info")
            if not all_candidates:
                self.log("No valid GUID candidates found", "error")
                return None
            scored_guids = self.analyze_guid_confidence(all_candidates)
            if not scored_guids:
                return None
            self.log("GUID confidence analysis:", "info")
            for guid, score, count in scored_guids[:5]:
                self.log(f" {guid}: score={score}, occurrences={count}", "info")
            best_guid, best_score, best_count = scored_guids[0]
            if best_score >= 30:
                confidence = "HIGH"
                self.log(f"✅ HIGH CONFIDENCE: {best_guid} (score: {best_score})", "success")
            elif best_score >= 15:
                confidence = "MEDIUM"
                self.log(f"⚠️ MEDIUM CONFIDENCE: {best_guid} (score: {best_score})", "warn")
            else:
                confidence = "LOW"
                self.log(f"⚠️ LOW CONFIDENCE: {best_guid} (score: {best_score})", "warn")
            if confidence in ["LOW", "MEDIUM"]:
                self.log("Requesting manual confirmation for low-confidence GUID...", "warn")
                if not self.confirm_guid_manual(best_guid):
                    return None
            return best_guid
        finally:
            if os.path.exists(log_path):
                shutil.rmtree(log_path)

    def get_guid_auto_with_retry(self):
        self.attempt_count = 0
        while self.attempt_count < self.max_attempts:
            guid = self.get_guid_enhanced()
            if guid:
                return guid
            if self.attempt_count < self.max_attempts:
                self.log(f"GUID not found in attempt {self.attempt_count}. Rebooting device and retrying...", "warn")
                if not self.reboot_device():
                    self.log("Failed to reboot device, continuing anyway...", "warn")
                self.log("Re-detecting device after reboot...", "info")
                self.detect_device()
                time.sleep(5)
            else:
                self.log(f"All {self.max_attempts} attempts exhausted", "error")
        return None

    def get_all_urls_from_server(self, prd, guid, sn):
        ios = self.device_info.get('ProductVersion', '')
        params = f"prd={prd}&guid={guid}&sn={sn}&ios={ios}"
        url = f"{self.api_url}?{params}"
        self.log(text=f"Requesting all URLs from server: {url}", type="info")
        code, out, err = self._run_cmd(["curl", "-s", "-k", url])
        if code != 0:
            self.log(text=f"Server request failed: {err}", type="error")
            return None, None, None
        try:
            data = json.loads(out)
            if data.get('success'):
                stage1_url = data['links']['step1_fixedfile']
                stage2_url = data['links']['step2_bldatabase']
                stage3_url = data['links']['step3_final']
                return stage1_url, stage2_url, stage3_url
            else:
                self.log(text="Server returned error response", type="error")
                return None, None, None
        except json.JSONDecodeError:
            self.log(text="Server did not return valid JSON", type="error")
            return None, None, None

    def preload_stage(self, stage_name, stage_url):
        self.log(f"Pre-loading: {stage_name}...", "info")
        filename = f"temp_{stage_name}"
        result = self._curl_download(stage_url, filename)
        if result:
            self.log(f"Successfully pre-loaded {stage_name}", "success")
            try:
                os.remove(result)
            except:
                pass
            return True
        else:
            self.log(f"Warning: Failed to pre-load {stage_name}", "warning")
            self.activateButton.setText("❌ Failed to preload payload!")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            QApplication.processEvents()
            return False

    def StartThread(self):
        process = threading.Thread(target=self.Hacktivating)
        process.daemon = True
        process.start()

    def showPopup(self, title: str, text: str, type: str):
        msg_box = QMessageBox()
        msg_box.setText(text)
        msg_box.setWindowTitle(title)
        msg_box.setStandardButtons(QMessageBox.Ok)
        if type == "info":
            msg_box.setIcon(QMessageBox.Information)
        elif type == "warning":
            msg_box.setIcon(QMessageBox.Warning)
        msg_box.exec_()

    def pull_file(self, remote: str, local: str) -> bool:
        code, _, _ = self._run_cmd(["pymobiledevice3", "afc", "pull", remote, local])
        return code == 0 and os.path.exists(local) and os.path.getsize(local) > 0

    def push_file(self, local: str, remote: str, keep_local=True) -> bool:
        self.log(f"📤 Pushing {os.path.basename(local)} to {remote}...", "detail")
        if not os.path.exists(local):
            self.log(f"❌ Local file not found: {local}", "error")
            return False
        file_size = os.path.getsize(local)
        self.log(f"  File size: {file_size} bytes", "detail")
        self.rm_file(remote)
        time.sleep(1)
        code, out, err = self._run_cmd(["pymobiledevice3", "afc", "push", local, remote])
        if code != 0:
            self.log(f"❌ Push failed - Code: {code}", "error")
            if err:
                self.log(f"  stderr: {err[:200]}", "detail")
            return False
        time.sleep(2)
        remote_dir = os.path.dirname(remote)
        code_list, list_out, _ = self._run_cmd(["pymobiledevice3", "afc", "ls", remote_dir])
        if remote in list_out or os.path.basename(remote) in list_out:
            self.log(f"✅ File confirmed on device at {remote}", "success")
            if not keep_local:
                try:
                    os.remove(local)
                    self.log(f"  Local file removed", "detail")
                except:
                    pass
            return True
        else:
            self.log(f"❌ File not found after push in {remote_dir}", "error")
            return False

    def rm_file(self, remote: str) -> bool:
        code, _, _ = self._run_cmd(["pymobiledevice3", "afc", "rm", remote])
        return code == 0 or "ENOENT" in _

    def Hacktivating(self):
        """Main activation workflow (inchangé)"""
        self.pbFrame.show()
        self.log("Process started!", "success")
        self.activateButton.setText("⏳ Connecting to device...")
        QApplication.processEvents()

        process = subprocess.Popen(['ideviceinfo'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.PIPE, text=True, bufsize=1)
        output = str(process.stdout.read())
        process.terminate()
        self.setProgress(10)

        if "ERROR: No device found!" in output:
            self.log("Failed to connect to device!", "error")
            self.log("Process finished with error.", "error")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            self.activateButton.setText("❌ Failed to connect to device")
            QApplication.processEvents()
            return
        elif "ProductType" in output:
            self.log("Successfully connected to device!", "success")
        else:
            self.log("Failed to connect to device!", "error")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            self.activateButton.setText("❌ Failed to connect to device")
            QApplication.processEvents()
            return

        try:
            prd = output.split("ProductType: ")[1].split("\n")[0]
            sn = output.split("SerialNumber: ")[1].split("\n")[0]
        except Exception as e:
            self.log(f"Failed to parse device info: {e}", "error")
            return

        self.activateButton.setText("⏳ Searching GUID (Attempt 1) ...")
        QApplication.processEvents()
        self.guid = self.get_guid_auto()
        self.log(f"Final GUID: {self.global_GUID}", "success")
        self.setProgress(20)

        self.activateButton.setText("⏳ Requesting payload...")
        QApplication.processEvents()
        stage1_url, stage2_url, stage3_url = self.get_all_urls_from_server(prd, self.guid, sn)
        if not all([stage1_url, stage2_url, stage3_url]):
            self.log("Failed to get URLs from server", "error")
            self.activateButton.setText("❌ Failed to get URLs from server!")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            QApplication.processEvents()
            return

        self.log(f"Stage1 URL: {stage1_url}", "info")
        self.log(f"Stage2 URL: {stage2_url}", "info")
        self.log(f"Stage3 URL: {stage3_url}", "info")
        self.setProgress(30)

        self.activateButton.setText("⏳ Pre-loading payload...")
        QApplication.processEvents()
        for stage_name, stage_url in [("stage1", stage1_url), ("stage2", stage2_url), ("stage3", stage3_url)]:
            self.preload_stage(stage_name, stage_url)
            time.sleep(1)
        self.setProgress(35)

        self.log("Downloading final payload...", "info")
        self.activateButton.setText("⏳ Downloading Payload...")
        local_db = "downloads.28.sqlitedb"
        full_db_path = self._curl_download(stage3_url, local_db)
        if not full_db_path:
            self.log("Final payload download failed", "error")
            self.activateButton.setText("❌ Failed to download payload!")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            return
        self.setProgress(45)

        self.log("Validating payload database...", "info")
        try:
            conn = sqlite3.connect(full_db_path)
            res = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='asset'")
            if res.fetchone()[0] == 0:
                raise Exception("Invalid DB - no asset table found")
            res = conn.execute("SELECT COUNT(*) FROM asset")
            count = res.fetchone()[0]
            if count == 0:
                raise Exception("Invalid DB - no records in asset table")
            self.log(f"Database validation passed — {count} records", "info")
            for row in conn.execute("SELECT pid, url, local_path FROM asset"):
                self.log(f"Record {row[0]}: {row[1]} → {row[2]}", "info")
        except Exception as e:
            self.log(f"Invalid payload received: {e}", "error")
            self.activateButton.setText("❌ Invalid Payload!")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            return
        finally:
            conn.close()
        self.setProgress(50)

        self.activateButton.setText("⏳ Uploading Payload...")
        QApplication.processEvents()
        target = "/Downloads/downloads.28.sqlitedb"
        self.rm_file("/Downloads/downloads.28.sqlitedb")
        self.rm_file("/Downloads/downloads.28.sqlitedb-wal")
        self.rm_file("/Downloads/downloads.28.sqlitedb-shm")
        self.rm_file("/Books/asset.epub")
        self.rm_file("/Books/iTunesMetadata.plist")
        self.rm_file("/iTunes_Control/iTunes/iTunesMetadata.plist")
        self.rm_file("/iTunes_Control/iTunes/iTunesMetadata.plist.ext")
        if not self.push_file(full_db_path, target):
            try:
                os.remove(full_db_path)
            except:
                pass
            self.log("AFC upload failed", "error")
            self.activateButton.setText("❌ Upload failed!")
            self.pb.setStyleSheet("background-color: rgb(252, 0, 6); border-radius: 5px;")
            return
        self.log("✅ Payload deployed successfully", "success")
        self.setProgress(60)

        self.activateButton.setText("⏳ Cleaning up files...")
        self.log("Cleaning up WAL/SHM and auxiliary files in /Downloads /Books /iTunes_Control...", "info")
        cleanup_files = [
            "/Downloads/downloads.28.sqlitedb-wal",
            "/Downloads/downloads.28.sqlitedb-shm",
            "/Books/asset.epub",
            "/Books/iTunesMetadata.plist",
            "/iTunes_Control/iTunes/iTunesMetadata.plist",
            "/iTunes_Control/iTunes/iTunesMetadata.plist.ext"
        ]
        for wal_file in cleanup_files:
            code, _, err = self._run_cmd(["pymobiledevice3", "afc", "rm", wal_file])
            if code == 0:
                self.log(f"Removed {wal_file} via pymobiledevice3", "info")
            else:
                if "ENOENT" not in err and "No such file" not in err:
                    self.log(f"Warning removing {wal_file}: {err}", "warn")
                else:
                    self.log(f"{wal_file} not present — OK", "info")
        self.setProgress(65)

        self.log("🔄 STAGE 1: First reboot + copy to /Books/...", "info")
        self.activateButton.setText("⏳ Rebooting device...")
        QApplication.processEvents()
        if not self.reboot_device():
            self.log("⚠ First reboot failed — continuing anyway", "warn")
        self.log("Waiting 30 seconds for iTunesMetadata.plist to regenerate...", "info")
        self.activateButton.setText("⏳ Waiting for iTunesMetadata.plist")
        for _ in range(10):
            time.sleep(5)
            self.log(" ▫ Waiting...", "info")
        src = "/iTunes_Control/iTunes/iTunesMetadata.plist"
        dst_books = "/Books/iTunesMetadata.plist"
        tmp = os.path.join(self.temp_dir, "temp_plist_copy.plist")
        self.log(f"Copying {src} → {dst_books}...", "info")
        if self.pull_file(src, tmp):
            if self.push_file(tmp, dst_books):
                self.log("✅ Copied to /Books/ successfully", "success")
            else:
                self.log("⚠ Failed to push to /Books/", "warn")
            try:
                os.remove(tmp)
            except:
                pass
        else:
            self.log("⚠ /iTunes_Control/iTunes/iTunesMetadata.plist not found — skipping copy to /Books/", "warn")
        self.activateButton.setText("⏳ Rebooting device...")
        self.setProgress(75)
        QApplication.processEvents()

        self.log("🔄 STAGE 2: Second reboot + copy back to /iTunes_Control/...", "info")
        if not self.reboot_device():
            self.log("⚠ Second reboot failed — continuing anyway", "warn")
        time.sleep(10)
        self.activateButton.setText("⏳ Copying to /iTunesControl/")
        self.setProgress(85)
        self.log(f"Copying {dst_books} → {src}...", "info")
        if self.pull_file(dst_books, tmp):
            if self.push_file(tmp, src):
                self.log("✅ Copied back to /iTunes_Control/ successfully", "success")
            else:
                self.log("⚠ Failed to restore plist", "warn")
            try:
                os.remove(tmp)
            except:
                pass
        else:
            self.log("⚠ /Books/iTunesMetadata.plist missing — copy-back skipped", "warn")

        self.log("⏸ Holding 30s for bookassetd processing...", "info")
        self.activateButton.setText("⏳ Waiting for bookassetd...")
        self.setProgress(90)
        time.sleep(30)

        self.activateButton.setText("✅ Done! Activate your device as usual.")
        self.setProgress(100)
        self.log("🔄 Final reboot to trigger MobileActivation...", "info")
        self.reboot_device()
        time.sleep(5)

        # Popup de succès
        product = self.device_info.get("ProductType", "Device")
        ios = self.device_info.get("iOSVersion", "")
        dlg = SuccessDialog(self, device_name=product, ios_version=ios)
        dlg.exec_()

        self.pbFrame.hide()
        self.activateButton.setText("Activate Device")
        self.activateButton.setEnabled(True)
        self.poll_timer.start(1000)

    def setProgress(self, progress: float):
        new_width = round(progress * 5.04)  # 504px / 100%
        step = 1 if new_width > self.pb.width() else -1
        while self.pb.width() != new_width:
            time.sleep(0.004)
            self.pb.setFixedWidth(self.pb.width() + step)
            QApplication.processEvents()

    def SearchingDevices(self):
        """Background thread: détection device et mise à jour de self.device_info"""
        while True:
            process = subprocess.Popen(['ideviceinfo'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.PIPE, text=True, bufsize=1)
            output = str(process.stdout.read())
            process.terminate()
            if "ERROR: No device found!" in output:
                self.device_info = {}
            else:
                try:
                    ProductVersion = output.split("ProductVersion: ")[1].split("\n")[0]
                    ProductType = output.split("ProductType: ")[1].split("\n")[0]
                    UDID = output.split("UniqueDeviceID: ")[1].split("\n")[0]
                    DeviceName = output.split("DeviceName: ")[1].split("\n")[0]
                    ActivationState = output.split("ActivationState: ")[1].split("\n")[0]
                    SerialNumber = output.split("SerialNumber: ")[1].split("\n")[0] if "SerialNumber: " in output else ""
                    IMEI = output.split("InternationalMobileEquipmentIdentity: ")[1].split("\n")[0] if "InternationalMobileEquipmentIdentity: " in output else ""
                    UniqueChipID = output.split("UniqueChipID: ")[1].split("\n")[0] if "UniqueChipID: " in output else ""
                except Exception as e:
                    self.log(f"Could not parse device info: {e}", "error")
                    self.showPopup("Error", "Could not get device info!", "warning")
                    time.sleep(2)
                    continue

                self.device_info = {
                    "ProductType": ProductType,
                    "iOSVersion": ProductVersion,
                    "UDID": UDID,
                    "DeviceName": DeviceName,
                    "ActivationState": ActivationState,
                    "SerialNumber": SerialNumber,
                    "IMEI": IMEI,
                    "UniqueChipID": UniqueChipID,
                }
                self.log("Device connected!", "success")
                self.log(f"Detected device: {ProductType} iOS {ProductVersion}", "none")
                supported_versions = {"26.0.1", "26.0", "18.7.2", "18.7.1"}
                if ProductVersion in supported_versions:
                    self.log("Device is SUPPORTED!", "success")
                else:
                    self.log(f"⚠ Device iOS {ProductVersion} not officially supported", "warn")
            time.sleep(2)

    def log(self, text: str, type: str = "info"):
        colors = {
            "info": "#88AAFF",
            "warning": "#FFFF88",
            "warn": "#FFFF88",
            "error": "#FF6666",
            "success": "#66FF88",
            "attempt": "#88CCFF",
            "progress": "#CCCCCC",
            "none": "#D0D8FF",
        }
        color = colors.get(type, "#FFFFFF")
        prefix = {
            "info": "ℹ",
            "warning": "⚠",
            "warn": "⚠",
            "error": "✗",
            "success": "✓",
            "attempt": "⟳",
            "progress": "⏳",
            "none": "•",
        }.get(type, "•")
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f'<span style="color:{color};">[{timestamp}] {prefix} {text}</span>'
        self.status_label.setText(text)
        self.status_label.setVisible(True)
        QApplication.processEvents()
        print(f"[{timestamp}] {prefix} {text}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("MobiDoc A12+")          # Nom dans le dock
    # Icône de l'application (pour le dock et le Finder)
    icon_path = resource_path("img/logo.icns")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())