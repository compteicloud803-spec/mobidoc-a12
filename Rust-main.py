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

# PySide6 imports (ONLY PySide6 — NO PyQt5)
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


# ────────────────────────────────────────────────────────────────
#  CORRECTION POUR LE PATH DANS L’APPLICATION COMPILÉE
# ────────────────────────────────────────────────────────────────
def configure_path():
    """Ajoute les chemins Homebrew au PATH pour que les outils soient trouvés."""
    extra_paths = [
        "/opt/homebrew/bin",   # Apple Silicon (M1/M2/M3)
        "/usr/local/bin",      # Intel Mac
        "/usr/local/sbin",
        "/opt/homebrew/sbin"
    ]
    current_path = os.environ.get("PATH", "")
    for p in extra_paths:
        if p not in current_path and os.path.exists(p):
            os.environ["PATH"] = f"{p}:{current_path}"
    # Optionnel : afficher le PATH dans la console pour déboguer
    # print(f"[DEBUG] PATH = {os.environ['PATH']}")

# Exécuter immédiatement la configuration du PATH
configure_path()


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
        logo_path = resource_path("img/logo.icns")
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

        self.pbFrame.hide()

        self.activateButton.clicked.connect(self.StartThread)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_device)
        self.poll_timer.start(1000)

        self._current_sn = ""
        self._current_udid = ""

        threading.Thread(target=self.SearchingDevices, daemon=True).start()
        self.console = None

    def _copy_sn(self):
        if self._current_sn:
            QApplication.clipboard().setText(self._current_sn)
            self.lbl_imei_sn.setStyleSheet("color: #2196F3; font-size: 11px;")
            QTimer.singleShot(1000, lambda: self.lbl_imei_sn.setStyleSheet("color: white; font-size: 11px;"))

    def poll_device(self):
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

    # ────────────────────────────────────────────────────────────────
    # MÉTHODES MÉTIER (inchangées sauf _run_cmd pour utiliser which)
    # ────────────────────────────────────────────────────────────────
    def _run_cmd(self, cmd, timeout=None):
        """Run a subprocess command, return (returncode, stdout, stderr)
           with automatic resolution of command path."""
        if not cmd:
            return 1, "", "Empty command"
        # Cherche le chemin absolu de la commande
        cmd_path = shutil.which(cmd[0])
        if not cmd_path:
            self.log(f"Command not found: {cmd[0]}", "error")
            return 1, "", f"Command not found: {cmd[0]}"
        # Remplacer le nom de la commande par son chemin absolu
        full_cmd = [cmd_path] + cmd[1:]
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
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
               ):
                return False return False
           
            hex_ch hex_chars =ars = set('012 set('34567890123456789ABCDEFABCDEF')
           ')
            clean = clean = guid.replace('-', guid.replace('-', ' '')
            if')
            if not all not all(c in hex_ch(c in hex_chars forars for c in c in clean clean):
                return False):
                return False
            if parts[2
            if parts[2][0] != '][0] != '44':
                return':
                return False False
            if parts
            if parts[3[3][0]][0] not in not in '89AB '89AB':
                return':
                return False False
            return
            return True True
        except
        except Exception Exception:
            return:
            return False False

    def

    def get_context_string(self, data get_context_string(self, data, start, end, start, context, end, context_size=_size=5050):
        context):
        context_start =_start = max(0, max(0, start - start - context_size context_size)
       )
        context_end context_end = min = min(len(data(len(data), end), end + context + context_size_size)
        context)
        context = data = data[context[context_start:_start:context_endcontext_end]
       ]
        try:
            return context.decode('utf try:
            return context.decode('utf--8',8 errors='replace', errors='replace')
       ')
        except except:
            return:
            return binasci binascii.i.hexlhexlify(contextify(context).decode).decode('asci('asciii')

    def analyze_')

    defguid_ analyze_guid_confidence(self, guidconfidence(self_candidates, guid):
       _candidates):
        if not guid_c if not guid_candidatesandidates:
            return:
            return None None
        guid
        guid_counts =_counts = Counter(c Counter(candidate['andidate['guid']guid'] for candidate for candidate in guid in guid_candidates_candidates)
        scored_)
        scored_guidsguids = = []
 []
        for        for guid, guid, count in guid_counts count in guid_counts.items.items():
            score():
            score = count *  = count * 1010
            positions =
            positions = [c[' [c['position'] for c in guid_candidates if cposition'] for c in guid_candidates if c['guid['guid'] =='] == guid guid]
            close]
            close_positions = [p for p in positions if abs(p)_positions = [p for p in positions if abs(p) < 100 < 100]
           ]
            if close if close_positions_positions:
                score +=:
                score += len(close_positions) * 5
            len(close_positions) * 5
            before before_positions_positions = = [p for [p for p in p in positions if p < 0 positions if p < 0]
           ]
            if before if before_positions_positions:
                score +=:
                score += len(b len(before_posefore_positions)itions) * 3 * 3
            scored_gu
            scored_guids.appendids.append((guid, score, count((guid, score, count))
       ))
        scored_ scored_guidsguids.sort(key=lambda x.sort(key=lambda x: x: x[1], reverse[1], reverse=True)
        return=True)
        return scored_ scored_guidsguids

   

    def confirm_guid def confirm_guid_manual(self,_manual(self, guid):
        self.log(f"G guid):
        self.log(f"GUID successfully parsed!UID successfully parsed! {guid {guid}", type}", type="success="success")
       ")
        self.gl self.global_GUID =obal_GUID = guid guid
        return
        return True

    def True

    def get_guid_enhanced get_guid_enhanced(self(self):
        self):
        self.attempt_count +=.attempt_count += 1 1
       
        self.log(f" self.log(f"GUIDGUID search attempt {self search attempt.attempt {self_count}/{.attemptself.max_count}/{self.max_attempt_attempts}",s}", "attempt "attempt")
       ")
        udid udid = self._current = self._current_ud_udidid
        log
        log_path =_path = f"{ f"{udidudid}.log}.logarchive"
        tryarchive"
        try:
           :
            self.activateButton.setText(f self.activateButton.setText(f""⏳⏳ Searching GUID Searching GUID (Attempt (Attempt {self.attempt_count} {self.attempt_count} / { / {self.maxself.max_attempts}) ..._attempts}) ...")
            code")
            code, _, err =, _, self._ err = self._run_crun_cmd(["md(["pympymobiledeviceobiledevice3",3", "syslog", "syslog", "collect "collect", log_path],", log_path], timeout= timeout=120120)
            if)
            if code != code != 0 0:
               :
                self.log(f" self.log(f"Log collectionLog collection failed: failed: {err}", "error {err}", "error")
                return")
                return None None
            trace
            trace_file =_file = os.path.join(log_path, os.path.join(log_path, "logdata.LiveData.tracev3")
 "logdata.LiveData.tracev3            if not os.path")
            if not os.path.exists(t.exists(trace_filerace_file):
                self):
               .log self.log("trace("tracev3v3 file not file not found", found", "error "error")
               ")
                return None return None
           
            with open with open(trace(trace_file,_file, ' 'rbrb') as') as f f:
                data:
                data = f = f.read.read()
            size()
            size_mb_mb = len = len(data)(data) / ( / (10241024 *  * 10241024)
            self.log)
            self.log(f"(f"AnalyzingAnalyzing tracev tracev33 ({size ({size_mb:.1f} MB)...", "_mb:.1f} MB)...", "info")
            signatures = self.parse_tracev3_stinfo")
            signatures = selfructure.parse_tracev3_structure(data)
            self.log(f"Found {len(data)
            self.log(f"Found {len(sign(signatures)} relevant signatures", "atures)} relevant signatures", "infoinfo")
            all")
            all_candidates_candidates = []
            for = []
            for sig_type sig_type, pattern, pattern, pos in signatures, pos in signatures:
               :
                if pattern == b if pattern == b'BLDatabaseManager'BLDatabaseManager':
                    candidates =':
                    candidates = self.ext self.extract_guid_cract_guid_candidates(dataandidates(data, pos, pos)
                   )
                    all_c all_candidates.extend(candidates)
                   andidates.extend(candidates)
                    if candidates if candidates:
                       :
                        self.log self.log(f"(f"Found {Found {len(clen(candidates)}andidates)} GUID candidates GUID candidates near BLDatabaseManager at  near BLDatabaseManager at 0x0x{pos:x}",{pos:x}", "info")
            if not "info")
            if not all_c all_candidatesandidates:
                self:
                self.log(".log("No valid GUID candidatesNo valid GUID candidates found", "error found", "error")
                return None
            scored_")
                return None
            scored_guidsguids = self = self.analy.analyze_ze_guid_guid_confidence(all_candidatesconfidence(all_candidates)
           )
            if not if not scored_ scored_guidsguids:
               :
                return None return None
           
            self.log self.log("G("GUID confidenceUID confidence analysis:", "info analysis:", "info")
           ")
            for guid for guid, score, score, count, count in scored in scored_gu_guids[:ids[:55]:
                self]:
                self.log(f" {guid}:.log(f" {guid}: score={ score={score}, occurrences={score}, occurrences={count}",count}", "info "info")
           ")
            best_guid, best_score, best_count = scored_ best_guid, best_score, best_count = scored_guidsguids[0]
           [0]
            if best if best_score >=_score >= 30 30:
               :
                confidence = "HIGH confidence = ""
               HIGH"
                self.log self.log(f"(f"✅ HIGH✅ HIGH CONFID CONFIDENCE: {bestENCE:_guid {best} (_guid} (score:score: {best {best_score})_score})", "", "success")
            elif best_scoresuccess")
            elif best_score >=  >= 1515:
                confidence = ":
                confidence = "MEDIUM"
               MEDIUM"
                self.log self.log(f"(f"⚠️⚠️ MEDIUM MEDIUM CONFIDENCE: {best CONFIDENCE: {best_guid_guid} (} (score:score: {best {best_score})_score})", "", "warnwarn")
            else:
                confidence = "LOW")
            else:
                confidence = "LOW"
               "
                self.log self.log(f"(f"⚠️⚠️ LOW CONFIDENCE LOW CONFIDENCE: {: {best_best_guid}guid} (score (score: {best_score: {best_score})})",", "w "warnarn")
            if")
            if confidence in confidence in ["L ["LOW",OW", "MED "MEDIUM"]:
               IUM"]:
                self.log("Request self.log("Requesting manualing manual confirmation for confirmation for low-confidence low-confidence GUID... GUID...", "", "warnwarn")
               ")
                if not if not self.conf self.confirm_irm_guid_manual(best_guid_manual(best_guidguid):
                    return):
                    return None
 None
                       return best_ return best_guidguid
        finally
        finally:
           :
            if os if os.path.exists(log_path):
               .path.exists(log_path):
                shutil shutil.rmtree.rmtree(log_path(log_path)

   )

    def get def get_guid_guid_auto_auto_with__with_retryretry(self(self):
        self.attempt):
        self.attempt_count =_count = 0 0
       
        while self while self.attempt.attempt_count_count < self.max < self.max_attempt_attemptss:
           :
            guid = self guid = self.get_.get_guid_enhancedguid_enhanced()
           ()
            if guid if guid:
               :
                return guid return guid
           
            if self if self.attempt.attempt_count_count < self.max < self.max_attempts:
                self_attempts:
                self.log(f.log(f"G"GUID notUID not found in found in attempt { attempt {self.self.attempt_countattempt_count}. Reb}. Rebooting deviceooting device and ret and retrying...", "rying...", "warnwarn")
               ")
                if not if not self.re self.reboot_deviceboot_device():
                   ():
                    self.log("Failed self.log("Failed to reboot to reboot device, device, continuing anyway continuing anyway...",...", "w "warn")
                selfarn")
                self.log(".log("Re-detRe-detecting device after rebootecting device after reboot...",...", "info "info")
               ")
                self.detect_device()
                self.detect_device()
                time.sleep time.sleep(5(5)
           )
            else else:
                self:
                self.log(f.log(f"All"All {self {self.max_.max_attempts} attemptsattempts} attempts exhausted", exhausted", "error "error")
       ")
        return None

    return None

    def get def get_all_url_all_urls_froms_from_server(self, pr_server(self, prd,d, guid, sn guid, sn):
        params):
        params = f"pr = f"prd={d={prdprd}&guid}&guid={guid={guid}&sn}&sn={sn={sn}"
       }"
        url = f"{ url = f"{self.apiself.api_url}_url}?{?{paramsparams}"
        self}"
        self.log(text.log(text=f"=f"RequestingRequesting all URLs all URLs from server from server: {: {url}", type="url}", type="infoinfo")
        code")
        code, out, out, err, err = self = self._run._run_cmd_cmd(["curl(["curl", "-", "-s",s", "-k", url "-k", url])
       ])
        if code if code !=  != 00:
            self:
            self.log(text.log(text=f"=f"Server requestServer request failed: failed: {err {err}", type="error}", type="error")
           ")
            return None return None, None, None, None
       , None try
       :
            data try:
            data = = json json.loads.loads(out(out)
            if)
            if data.get('success'):
                stage1 data.get('success'):
                stage1_url =_url = data[' data['links']['links']['step1step1_f_fixedixedfilefile']
                stage']
                stage2_url = data2_url = data['links['links']['step']['step2_b2_bldatabaseldatabase']
                stage3']
                stage3_url =_url = data[' data['links']['links']['step3_finalstep3']
                return stage_final']
                return stage1_url1_url, stage2_url, stage2_url, stage, stage3_url3_url
           
            else:
                self else:
.log(text                self.log(text="Server="Server returned error returned error response", type=" response", type="errorerror")
                return")
                return None, None, None, None, None None
        except
        except json.JSON json.JSONDecodeDecodeErrorError:
            self.log(text="Server:
            self.log(text="Server did not did not return valid JSON", return valid type="error JSON", type="")
            returnerror")
            return None, None, None, None, None None

    def

    def preload preload_stage_stage(self,(self, stage_name stage_name, stage, stage_url_url):
        self):
        self.log(f.log(f"Pre"Pre-loading-loading: {: {stage_namestage_name}...}...", "info", "info")
        filename")
        filename = f = f"temp"temp_{stage_{stage_name_name}"
        result}"
        result = self = self._curl._curl_download_download(stage(stage_url, filename_url, filename)
        if)
        if result result:
            self:
            self.log(f.log(f"Success"Successfully pre-loaded {stage_name}", "success")
            try:
                os.removefully pre-loaded {stage_name}", "success")
            try:
                os.remove(result(result)
            except)
            except:
:
                               pass pass
            return
            return True True
        else
        else:
           :
            self.log self.log(f"(f"Warning: Failed toWarning: Failed to pre-load pre-load {stage {stage_name}", "warning_name}", "warning")
           ")
            self. self.activateButton.setText("activateButton.setText("❌❌ Failed to Failed to preload preload payload!")
            self.pb payload!")
            self.setStyle.pb.setStyleSheet("Sheet("background-colorbackground-color: rgb: rgb(252(252, 0,, 0, 6); border 6); border-radius:-radius: 5 5px;")
           px;")
            QApplication QApplication.processEvents.processEvents()
           ()
            return False return False

   

    def Start def StartThread(selfThread(self):
       ):
        process = threading.Thread process = threading.Thread(target=self(target=self.Hack.Hacktivatingtivating)
       )
        process.d process.daemonaemon = True = True
       
        process.start process.start()

   ()

    def showPopup(self def showPopup(self, title, title: str, text: str: str, text: str, type, type: str: str):
       ):
        msg msg_box_box = Q = QMessageBoxMessageBox()
        msg_box()
        msg_box.setText(text.setText(text)
       )
        msg_box msg_box.setWindow.setWindowTitle(titleTitle(title)
       )
        msg_box.setStandard msg_box.setStandardButtons(QButtons(QMessageBoxMessageBox.Ok)
        if type.Ok)
        if type == " == "infoinfo":
            msg":
            msg_box.set_box.setIcon(QMessageBoxIcon(QMessageBox.Information)
       .Information)
        elif type elif type == " == "warning":
            msgwarning":
            msg_box.setIcon(QMessageBox.W_box.setIcon(QMessageBox.Warning)
       arning)
        msg_box msg_box.exec_.exec_()

    def pull()

   _file(self def pull_file(self, remote: str, remote: str, local, local: str: str) ->) -> bool bool:
        code:
        code, _,, _, _ = _ = self._ self._run_cmd(["run_cmd(["pympymobiledevice3", "afobiledevice3", "afc",c", "pull "pull", remote", remote, local, local])
        return code])
        return code == 0 and == 0 and os.path.exists(l os.path.exists(local)ocal) and os and os.path.get.path.getsize(lsize(local)ocal) >  > 00

    def

    def push_file push_file(self,(self, local: str, local: str, remote: remote: str, str, keep_local keep_local=True)=True) -> bool -> bool:
       :
        self.log self.log(f"(f"📤📤 Pushing Pushing {os.path.b {os.path.basenameasename(local(local)} to)} to {remote}... {remote}...", "", "detaildetail")
        if")
        if not os not os.path.exists.path.exists(local):
           (local):
            self.log(f"❌ self.log(f"❌ Local file Local file not found not found: {: {local}",local}", "error "error")
           ")
            return False return False
       
        file_size = os file_size = os.path.get.path.getsize(lsize(localocal)
)
        self        self.log(f.log(f" "  File size File size: {: {filefile_size_size} bytes} bytes", "", "detail")
        selfdetail")
        self.rm_file.rm_file(remote(remote)
       )
        time.sleep time.sleep(1(1)
       )
        code, code, out, out, err = err = self._ self._run_cmd(["run_cmd(["pymobiledevice3", "afpymobiledevice3", "afc",c", "push "push", local", local, remote, remote])
       ])
        if if code != 0 code != 0:
            self:
            self.log(f.log(f""❌ Push❌ Push failed - failed - Code: Code: {code {code}", "}", "errorerror")
            if")
            if err err:
                self:
                self.log(f.log(f" "  stderr stderr: {: {err[:err[:200]}200]}", "", "detaildetail")
            return")
            return False False
        time
        time.sleep(.sleep(22)
        remote)
        remote_dir =_dir = os.path os.path.dirname.dirname(remote(remote)
       )
        code_list code_list, list, list_out, _ = self.__out, _ = self._run_crun_cmd(["md(["pympymobiledobiledeviceevice3",3", "af "afc",c", "ls "ls", remote", remote_dir])
        if_dir])
        if remote in remote in list_out list_out or os or os.path.b.path.basenameasename(remote(remote)) in in list_out list_out:
           :
            self.log self.log(f"✅ File(f"✅ File confirmed on confirmed on device at { device atremote {remote}", "success}", "success")
            if")
            if not keep_local not keep_local:
                try:
                try:
:
                                       os.remove os.remove(local(local)
                   )
                    self.log(f" self.log(f"  Local  Local file removed file removed", "", "detail")
               detail")
                except except:
                   :
                    pass pass
            return True
            return True
        else:
           
        else:
            self.log(f"❌ File not found after self.log(f"❌ File not found after push in push in {remote {remote_dir}",_dir}", "error")
 "error")
            return False

               return False

    def rm def rm_file(self_file(self, remote: str, remote: str) ->) -> bool bool:
        code:
        code, _, _ =, _, _ = self._ self._run_cmd(["run_cmd(["pymobiledevice3", "afc",pymobiledevice3", "afc", "rm", remote "rm", remote])
       ])
        return code return code == 0 or "EN == 0 or "ENOENTOENT" in" in _

    _

    def Hacktivating def Hacktivating(self(self):
        """):
        """Main activationMain activation workflow (inchangé)"""
        workflow (inchangé)"""
        self.p self.pbFramebFrame.show.show()
        self()
        self.log(".log("Process started!", "Process started!", "successsuccess")
        self.activateButton.setText("⏳")
        self.activateButton.setText("⏳ Connecting to Connecting to device... device...")
       ")
        QApplication QApplication.processEvents()

       .processEvents process =()

        process = subprocess.Popen subprocess.Popen(['idevice(['ideviceinfo'],info'], stdout= stdout=subprocesssubprocess.PIPE.PIPE, st, stderr=derr=subprocesssubprocess.STDOUT.STDOUT,
                                   stdin=,
                                   stdin=subprocesssubprocess.PIPE.PIPE, text, text=True,=True, bufs bufsize=ize=11)
        output)
        output = str(process = str.stdout.read(process.stdout.read())
        process.())
        process.terminateterminate()
       ()
        self.setProgress( self.setProgress(1010)

        if)

        if "ERROR: No "ERROR: No device found device found!" in!" in output output:
            self.log(":
            self.log("Failed to connect toFailed to connect to device!", device!", "error "error")
           ")
            self.log self.log("Process("Process finished with finished with error.", error.", "error "error")
            self.pb.set")
            self.pb.setStyleSheetStyleSheet("background("background-color:-color rgb(252,: rgb(252, 0 0, , 6);6); border-radius:  border-radius: 5px;5px;")
            self")
            self.activate.activateButton.setText("Button.setText("❌ Failed❌ Failed to connect to connect to device to device")
")
                       QApplication.processEvents()
            return QApplication.processEvents()
            return
        elif
        elif "Product "ProductType"Type" in output in output:
           :
            self.log self.log("Success("Successfully connectedfully connected to device to device!", "!", "successsuccess")
        else:
           ")
        else:
            self.log self.log("Failed("Failed to connect to device to connect to device!", "!", "errorerror")
            self")
            self.pb.setStyle.pb.setStyleSheet("Sheet("background-color: rgbbackground-color: rgb(252(252, , 0,0, 6 6); border); border-radius:-radius: 5 5px;")
           px;")
            self. self.activateButton.setText("activateButton.setText("❌❌ Failed to Failed to connect to connect to device device")
            Q")
            QApplication.processApplication.processEventsEvents()
            return()
            return

       

        try try:
            prd = output.split:
            prd = output.split("Product("ProductType:Type: ")[1]. ")[1].split("\split("\n")n")[0[0]
           ]
            sn = sn = output.split output.split("SerialNumber:("SerialNumber: " ")[1].)[1].split("\split("\n")n")[0[0]
       ]
        except Exception except Exception as e as e:
           :
            self.log self.log(f"(f"Failed to parse deviceFailed to parse device info: info: {e}", " {eerror}", "error")
            return")
            return

       

        self. self.activateButtonactivateButton.setText(".setText("⏳ Searching GUID (Attempt 1)⏳ Searching GUID (Attempt 1) ... ...")
        QApplication.process")
        QApplication.processEventsEvents()
       ()
        self self.guid.guid = self = self.get.get__guid_autoguid_auto()
        self()
        self.log(f.log(f"Final"Final GUID: GUID: {self {self.global.global_GUID_GUID}", "success}", "success")
        self")
        self.setProgress(20.setProgress(20)

       )

        self. self.activateButtonactivateButton.setText(".setText("⏳ Request⏳ Requesting payloading payload......")
        Q")
        QApplication.processEvents()
        stage1_urlApplication.processEvents()
        stage1_url, stage, stage2_url2_url, stage, stage3_url3_url = self.get_all = self.get_all_urls_urls_from_server_from_server(pr(prd,d, self.guid, self.guid, sn sn)
        if)
        if not all not all([stage([stage1_url1_url, stage, stage2_url, stage2_url3_url, stage3_url]):
           ]):
            self.log("Failed self.log("Failed to to get URLs from get URLs from server", server", "error")
            "error")
            self.activate self.activateButtonButton.setText(".setText("❌❌ Failed to Failed to get URLs from server!")
            get URLs from server!")
            self.p self.pb.setb.setStyleSheet("background-color:StyleSheet("background rgb(-color:252, rgb( 0252,,  0, 6);6); border-radius:  border-radius: 5px5px;")
            QApplication;")
            Q.processEventsApplication.process()
            returnEvents()
            return

       

        self.log self.log(f"(f"Stage1Stage1 URL: URL: {stage {stage1_url}", "1_url}", "infoinfo")
        self.log(f")
        self.log(f"Stage"Stage2 URL2 URL: {stage2: {stage2_url}",_url}", "info "info")
        self.log")
        self.log(f"(f"Stage3Stage3 URL: URL: {stage {stage3_url3_url}",}", "info "info")
        self")
        self.setProgress.setProgress(30)

        self.(30)

        self.activateButtonactivateButton.setText(".setText("⏳ Pre⏳ Pre-loading-loading payload... payload...")
       ")
        QApplication.processEvents()
        for stage_name, stage QApplication.processEvents()
        for stage_name, stage_url in [("stage_url in [("stage1", stage1_url), ("stage2",1", stage1_url), ("stage2", stage2_url), stage2_url), ("stage ("stage3", stage33", stage3_url)]_url)]:
           :
            self.pre self.preload_stload_stage(stage(stage_nameage_name,, stage_url stage_url)
            time)
            time.sleep(.sleep(11)
        self.setProgress)
        self.setProgress(35(35)

       )

        self.log self.log("Download("Downloading finaling final payload... payload...", "", "infoinfo")
        self")
        self.activateButton.setText(".activateButton.setText("⏳⏳ Downloading Downloading Payload Payload...")
        local_db = "download...")
        local_db = "downloads.s.2828.sqlitedb.sqlitedb"
        full_db"
        full_db_path =_path = self._curl_d self._curl_download(stownload(stage3age3_url,_url, local_db local_db)
        if not full_db)
        if not full_db_path:
            self_path:
            self.log(".log("Final payloadFinal payload download failed download failed", "error", "error")
            self")
            self.activate.activateButton.setTextButton.setText("("❌ Failed❌ Failed to download payload to download payload!")
            self!")
            self.pb.pb.setStyle.setStyleSheet("background-colorSheet("background-color: rgb: rgb(252(252, , 0,0, 6 6); border); border-radius:-radius: 5 5px;px;")
           ")
            return return
        self
        self.setProgress.setProgress(45(45)

        self.log)

        self.log("Valid("Validating payload database...ating payload database...", "info", "info")
        try")
        try:
            conn =:
            conn = sqlite sqlite3.connect3.connect(full(full_db_path_db_path)
           )
            res = conn.execute res = conn.execute("SELECT("SELECT count(*) count(*) FROM sql FROM sqlite_mite_master WHEREaster WHERE type=' type='table'table' AND name='asset AND name='asset''")
            if")
            if res.fetchone()[0] res.fetchone()[0] ==  == 00:
                raise:
                raise Exception("Invalid DB Exception("Invalid DB - no - no asset table asset table found found")
            res")
            res = conn = conn.execute(".execute("SELECT COUNT(*) FROMSELECT COUNT asset(*) FROM")
            count asset")
            = res.fetchone count = res()[0.fetchone]
           ()[0]
            if count if count ==  == 00:
                raise:
                raise Exception("Invalid DB Exception("Invalid DB - no - no records in records in asset table asset table")
            self.log")
            self.log(f"(f"Database validationDatabase validation passed — passed — {count} records {count} records", "info", "info")
            for")
            for row in conn.execute row in conn.execute("SELECT("SELECT pid, pid, url, url, local_path FROM asset local_path FROM asset"):
               "):
                self.log self.log(f"Record {row(f"Record {row[0][0]}: {}: {rowrow[1]} → {[1]} →row {row[2]}[2]}", "", "infoinfo")
        except Exception as e")
        except Exception as e:
            self:
            self.log(f"Invalid.log(f"Invalid payload received payload received: {: {e}",e}", "error")
            "error")
            self. self.activateButtonactivateButton.setText("❌.setText("❌ Invalid Payload Invalid Payload!")
            self!")
            self.pb.pb.setStyle.setStyleSheet("Sheet("background-colorbackground-color: rgb: rgb(252(252, , 0,0, 6); border-radius: 6); border-radius: 5px; 5px;")
           ")
            return return
        finally
        finally:
           :
            conn.close()
        conn.close()
        self.set self.setProgress(50Progress(50)

)

        self        self.activate.activateButton.setText("Button.setText⏳(" Uploading⏳ Uploading Payload... Payload...")
        Q")
        QApplication.processEventsApplication.processEvents()
        target()
        target = "/ = "/Downloads/downloadDownloads/downloads.28.sqls.itedb28.sqlitedb"
       "
        self.rm_file("/ self.rm_file("/Downloads/downloads.Downloads/downloads.28.sql28.sqlitedbitedb")
       ")
        self.rm self.rm_file("/Downloads/download_file("/Downloads/downloads.s.28.sql28.sqliteditedb-walb-wal")
        self.rm_file("/Downloads/download")
        self.rm_file("/Downloads/downloads.28s.28.sqlitedb.sqlitedb-shm-shm")
       ")
        self.rm_file("/ self.rm_file("/Books/Books/asset.asset.epub")
        self.rmepub")
        self.rm_file("/Books/i_file("/Books/iTunesMetadataTunesMetadata.plist.plist")
       ")
        self.rm self.rm_file("/_file("/iTunes_ControliTunes_Control/iTunes/iTunes/iTunes/iTunesMetadata.plMetadata.plist")
        selfist")
        self.rm_file.rm_file("/i("/iTunes_Tunes_Control/iControl/iTunes/iTunesMetadataTunes/iTunesMetadata.plist.ext.plist.ext")
        if")
        if not self not self.push_file(full_db_path.push_file(full_db_path, target):
            try, target):
            try:
                os:
                os.remove(f.remove(full_dbull_db_path_path)
            except:
                pass
            self)
            except:
                pass
            self.log(".log("AFC upload failedAFC", " upload failederror", "error")
            self")
            self.activateButton.setText.activateButton.setText("("❌ Upload❌ Upload failed failed!")
            self.pb!")
            self.pb.setStyle.setStyleSheet("Sheet("background-colorbackground-color: rgb: rgb(252, (252, 0,0, 6 6); border); border-radius:-radius: 5 5px;")
           px;")
            return return
        self
        self.log(".log("✅ Pay✅ Payload deployedload deployed successfully", successfully", "success")
        "success")
        self.set self.setProgress(Progress(60)

        self60)

        self.activate.activateButton.setText("Button.setText("⏳⏳ Cleaning up files... Cleaning up")
        files...")
        self.log self.log("Cle("Cleaning upaning up WAL/SHM and auxiliary WAL/SHM and auxiliary files in files in /Downloads /Downloads /Books /i /Books /iTunes_Tunes_Control...Control...", "", "info")
        cleanupinfo")
        cleanup_files =_files = [
            "/Downloads [
            "/Downloads/downloads/downloads.28.28.sqlitedb-w.sqlitedb-walal",
            "/",
            "/Downloads/downloadDownloads/downloads.28.sqls.itedb28.sqlitedb-shm",
           -shm",
            "/Books "/Books/asset/asset..epepubub",
            "/Books/iTunesMetadata",
            "/Books/iTunesMetadata.plist.plist",
           ",
            "/i "/iTunes_Control/iTunes_Control/iTunes/iTunes/iTunesMetadataTunesMetadata.plist.plist",
           ",
            "/i "/iTunes_Control/iTunes_Control/iTunes/iTunes/iTunesMetadata.plist.ext"
        ]
        forTunesMetadata.plist.ext"
        ]
        for wal_file wal_file in cleanup in cleanup_files_files:
            code, _,:
            code, err = _, err = self._ self._run_crun_cmd(["md(["pympymobiledeviceobiledevice3",3", "af "afc", "rm", walc", "rm", wal_file_file])
            if])
            if code == code == 0:
                0:
                self.log(f" self.log(f"Removed {walRemoved_file} {wal_file} via pymob via pymobiledeviledevice3ice3",", "info "info")
            else")
            else:
               :
                if "ENO if "ENOENT"ENT" not in not in err and err and "No "No such file" not such file" not in err in err:
                    self.log:
                    self.log(f"(f"Warning removingWarning removing {wal {wal_file}:_file}: {err {err}", "}", "warnwarn")
               ")
                else else:
                    self.log(f:
                    self.log(f"{wal"{wal_file}_file} not present not present — OK", " — OK", "infoinfo")
")
        self        self.setProgress.setProgress(65(65)

       )

        self.log self.log("🔄 ST("🔄 STAGE AGE 1:1: First reboot + copy First reboot + copy to / to /Books/...",Books/...", "info "info")
        self.activateButton.setText("")
        self.activateButton.setText("⏳ Reb⏳ Rebooting device...ooting device...")
        Q")
        QApplication.processApplication.processEventsEvents()
        if()
        if not self not self.reboot_device.reboot():
            self_device():
            self.log(".log("⚠ First⚠ First reboot failed — continuing anyway", reboot failed — continuing anyway", "w "warnarn")
        self")
        self.log(".log("Waiting Waiting 30 seconds30 seconds for iTunes for iTunesMetadata.plMetadata.plist toist to regenerate... regenerate...", "", "infoinfo")
        self.activateButton.setText")
        self.activateButton.setText("("⏳⏳ Waiting for iTunes Waiting forMetadata.plist iTunesMetadata.plist")
       ")
        for _ for _ in range(10):
 in range(10):
                       time.sleep time.sleep(5(5)
           )
            self.log(" self.log(" ▫ Waiting ▫ Waiting...",...", "info "info")
        src = "/iTunes_")
        src = "/iTunes_Control/iControl/iTunesTunes/i/iTunesMetadataTunesMetadata.plist.plist"
       "
        dst_books = dst_books = "/Books "/Books/iTunes/iTunesMetadata.plMetadata.plistist"
        tmp = os"
        tmp = os.path.join.path.join(self.t(self.temp_diremp_dir, "temp_, "temp_plistplist_copy.pl_copy.plistist")
        self")
        self.log(f.log(f"Copy"Copying {src}ing {src} → { → {dst_dst_books}books}...",...", "info "info")
       ")
        if self if self.pull.pull_file(src_file(src,, tmp):
            tmp):
            if self.push_file if self.push_file(tmp, dst_books(tmp, dst_):
                selfbooks.log("):
                self.log("✅ Cop✅ Copied to /Booksied to /Books/ successfully/ successfully", "success", "success")
            else:
                self.log")
            else:
                self.log("⚠("⚠ Failed to Failed to push to push to /Books/", /Books/", "w "warn")
            try:
               arn")
            try:
                os.remove(tmp os.remove(tmp)
            except:
               )
            except:
                pass
        else pass
       :
            else:
            self.log self.log("⚠("⚠ /iTunes_ /iTunes_Control/iControl/iTunes/iTunes/iTunesMetadata.plistTunesMetadata.plist not found not found — skipping — skipping copy to copy to /Books/", /Books "w/", "warnarn")
        self")
        self.activateButton.setText.activateButton.setText("("⏳⏳ Rebooting Rebooting device... device...")
       ")
        self.set self.setProgress(Progress(7575)
        Q)
        QApplication.processApplication.processEvents()

        selfEvents()

        self.log(".log("🔄🔄 ST STAGEAGE 2 2: Second reboot +: Second copy back reboot + copy back to / to /iTunesiTunes_Control_Control/.../...", "", "infoinfo")
        if")
        if not self not self.reboot_device.reboot_device():
            self():
            self.log(".log("⚠ Second reboot failed — continuing⚠ Second reboot failed — continuing anyway", anyway", "warn "warn")
        time")
        time.sleep(.sleep(10)
10)
        self.activate        self.activateButton.setTextButton.setText("("⏳ Copying⏳ Copying to / to /iTunesControl/iTunesControl/")
        self.set")
        self.setProgress(Progress(8585)
        self)
        self.log(f.log(f"Copy"Copying {ing {dst_dst_books}books} → {src} → {src}......",", "info "info")
       ")
        if self if self.pull.pull_file(d_file(dst_st_books,books, tmp):
            if tmp):
            if self.push_file(tmp, src self.push_file(tmp, src):
               ):
                self.log self.log("✅("✅ Copied back to Copied back to /i /iTunes_Tunes_Control/Control/ successfully", "success successfully", "success")
            else")
            else:
                self:
                self.log("⚠ Failed.log("⚠ Failed to restore to restore plist", " plist", "warn")
           warn")
            try try:
                os:
                os.remove(tmp)
           .remove(tmp)
            except except:
                pass:
                pass
       
        else else:
            self:
            self.log("⚠ /.log("⚠ /Books/iBooks/iTunesMetadataTunesMetadata.plist.plist missing — copy-back skipped", missing — copy-back skipped", "w "warnarn")

        self")

        self.log(".log("⏸ Holding⏸ Holding 30 30s fors for bookasset bookassetd processingd processing...", "info...", "info")
       ")
        self. self.activateButton.setText("activateButton.setText("⏳ Waiting⏳ Waiting for book for bookassetdassetd...")
        self...")
        self.setProgress(90)
       .setProgress(90)
        time.sleep time.sleep(30(30)

       )

        self. self.activateButtonactivateButton.setText(".setText("✅ Done✅ Done! Act! Activate yourivate your device as device as usual.")
        self usual.")
        self.setProgress.setProgress(100(100)
       )
        self.log self.log("🔄("🔄 Final reboot to Final reboot to trigger Mobile trigger MobileActivationActivation...",...", "info")
        "info")
        self.re self.reboot_deviceboot_device()
       ()
        time.sleep(5 time.sleep(5)

       )

        # Pop # Popup de succèsup de
        succès
        product = product = self.dev self.device_infoice_info.get("ProductType.get("ProductType", "", "DeviceDevice")
        ios =")
        ios self.device = self.device_info.get_info.get("iOS("iOSVersion",Version", "")
        dlg = Success "")
        dlg = SuccessDialog(self, deviceDialog(self, device_name=_name=productproduct,, ios_version ios_version=ios=ios)
       )
        dlg dlg.exec_.exec_()

       ()

        self.p self.pbFramebFrame.hide.hide()
        self.()
        self.activateButtonactivateButton.setText(".setText("ActActivateivate Device Device")
        self.activate")
        self.activateButton.setButton.setEnabled(TrueEnabled(True)
        self)
        self.poll_t.poll_timer.startimer.start(100(10000)

    def)

    def setProgress setProgress(self,(self, progress: float progress: float):
        new):
        new_width =_width = round( round(progress * 5progress * 5.04.04) )  #  # 504px504px / 100 / 100%
        step%
        step = 1 if new_width = 1 if new_width > self.pb.width() > self.pb.width() else - else -11
        while
        while self.p self.pb.widthb.width() != new_width() !=:
            time.sleep new_width:
            time.sleep(0(0.004)
           .004)
            self.p self.pb.setb.setFixedWidthFixedWidth(self.p(self.pb.width() +b.width() + step step)
            QApplication.process)
            QApplication.processEventsEvents()

   ()

    def SearchingDevices(self def SearchingDev):
       ices(self """Background):
        """Background thread: détection thread: détection device et device et mise à mise à jour de self.dev jour de self.device_infoice_info"""
       """
        while True:
            while True # Util:
           iser # Utiliser _run _run_cmd_cmd pour pour une meilleure une meilleure robustesse robustesse
           
            code, output, code, _ = output, _ = self._ self._run_crun_cmd(["md(["idevideviceinfoiceinfo"])
            if code"])
            if code !=  != 0:
                self0.device:
                self.device_info =_info = {}
                {}
                time.sleep time.sleep(2(2)
                continue)
               

            try continue

            try:
               :
                ProductVersion ProductVersion = output = output.split("ProductVersion.split("ProductVersion: ")[1: ")[1].split].split("\n")[0]
                ProductType = output.split("ProductType: "("\n")[0]
                ProductType = output.split("ProductType: ")[1].split("\n")[0]
                UDID =)[1].split("\n")[0]
                UDID = output output.split.split("Unique("UniqueDeviceIDDeviceID: ")[1: ")[1].split].split("\n("\n")")[0]
                Device[0]
                DeviceName =Name = output.split output.split("Device("DeviceName: "Name: ")[1].split("\)[1].split("\n")n")[0[0]
                ActivationState]
                = output ActivationState = output.split(".split("ActivationActivationState:State: " ")[1].)[1].split("\split("\n")[0n")]
               [0]
                SerialNumber SerialNumber = output.split(" = output.split("SerialNumberSerialNumber: ": ")[1)[1].split("\n].split("\n")")[0] if "SerialNumber[0] if "SerialNumber: ": " in output in output else else ""
                I ""
                IMEMEI = outputI = output.split(".split("InternationalMobileInternationalMobileEquipmentIdentityEquipmentIdentity: ")[1].split: ")[1].split("\n("\n")")[0][0] if " if "InternationalMobileEquipmentIdentityInternationalMobileEquipmentIdentity: ": " in output in output else ""
                UniqueChipID = else ""
                UniqueChipID = output.split output.split("Unique("UniqueChipChipID: "ID:)[1]. "split("\)[1].split("\n")[0n")[0] if] if "UniqueChip "UniqueChipID:ID: " in " in output else output else ""
            ""
            except Exception except Exception as e as e:
                self.log:
               (f" self.log(f"Could notCould not parse device info: parse device info: {e {e}", "}", "error")
                selferror.showPopup")
                self.showPopup("Error("Error", "", "Could notCould not get device get device info!", info!", "warning "warning")
                time.sleep")
                time.sleep(2(2)
)
                continue                continue

            self.device

            self.device_info =_info = {
                "ProductType": {
                "ProductType": ProductType ProductType,
                "iOSVersion":,
                "iOSVersion": ProductVersion ProductVersion,
               ,
                "UD "UDID": UDIDID": UDID,
                ",
                "DeviceNameDeviceName": Device": DeviceName,
                "ActivationName,
                "ActivationState":State": ActivationState ActivationState,
               ,
                "SerialNumber "SerialNumber":": SerialNumber SerialNumber,
               ,
                "IME "IMEI": IMEI": IMEI,
                "I,
                "UniqueCUniqueChipIDhipID": UniqueChip": UniqueIDChipID,
           ,
            }
            self }
            self.log(".log("Device connectedDevice connected!", "!", "success")
            selfsuccess")
            self.log(f.log(f"Det"Detected deviceected device: {ProductType: {ProductType} iOS} iOS {Product {ProductVersion}",Version}", "none")
            "none")
            supported_ supported_versions =versions = {"26 {"26.0.0.1.1", "", "26.026.", "180", "18.7.7.2.2", "", "18.18.7.7.11"}
            if"}
            if ProductVersion ProductVersion in supported in supported_versions:
               _versions:
                self.log self.log("Device("Device is SU is SUPPORTED!", "PPORTED!", "successsuccess")
            else")
            else:
               :
                self.log self.log(f"(f"⚠ Device⚠ Device iOS { iOS {ProductVersion} not officiallyProductVersion} not officially supported supported", "", "warnwarn")
           ")
            time.sleep(2 time.sleep(2)

    def log)

    def log(self,(self, text: text: str, type: str, type: str = str = "info "info"):
       "):
        colors = colors = {
            {
            "info": "# "info": "#88AA88AAFF",
            "FF",
            "warning":warning": "#FFFF "#FFFF88",
            "88warn",
            "warn": "#": "#FFFF88FFFF88",
            "error":",
            "error": "# "#FF666FF66666",
            "",
            "success":success": "#66 "#66FF88FF88",
           ",
            "attempt": "# "attempt": "#88CCFF88CCFF",
            "",
            "progress":progress": "#CCCCCC "#CCCCCC",
            "none",
            "none": "#": "#D0D8D0D8FFFF",
       ",
        }
        color }
        color = colors = colors.get(type.get(type, "#, "#FFFFFF")
        prefixFFFFFF")
        prefix = = {
            " {
            "info":info": " "ℹ",
            "ℹ",
            "warning": "⚠warning": "⚠",
           ",
            "warn": "warn": "⚠ "⚠",
            "error",
            "error": "": "✗✗",
           ",
            "success "success": "✓": "✓",
            "attempt":",
            "attempt": "⟳ "⟳",
            "",
            "progress": "⏳",
            "none": "•",
        }.progress": "⏳",
            "none": "•",
        }.get(typeget(type, ", "•")
        timestamp•")
        timestamp = datetime = datetime.datetime.now.datetime.now().str().strftime("%ftime("%H:%M:%S")
        lineH:%M:%S")
        line = f = f''<span style<span style="color:{color="color:{color};">};">[{timestamp[{timestamp}] {prefix}}] {prefix} {text {text}</span}</span>'
        self.status_label>'
        self.status_label.setText(text.setText(text)
       )
        self.status self.status_label.setVisible(_label.setVisible(TrueTrue)
        Q)
        QApplication.processApplication.processEvents()
        print(fEvents()
        print(f"[{timestamp}] {"[{timestamp}] {prefix} {textprefix} {text}"}")


if __)


if __name__name__ == "__main == "__main__":
    #__":
    # Configurer le Configurer le PATH avant tout
    PATH avant tout
    configure_path()
    configure_path()
    app = app = QApplication QApplication(sys.argv(sys.argv)
   )
    app.set app.setApplicationNameApplicationName("MobiDoc("MobiDoc A12 A12++")
    icon")
    icon_path =_path = resource_path resource_path("img("img/logo/logo.ic.icns")
    ifns")
    if os.path os.path.exists(icon_path.exists(icon_path):
       ):
        app.set app.setWindowIconWindowIcon(QIcon(QIcon(icon(icon_path_path))
    window))
    window = Main = MainWindow()
    windowWindow()
    window.show.show()
    sys.exit(app()
    sys.exit(app.exec.exec())
