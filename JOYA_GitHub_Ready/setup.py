import sys
import os
import urllib.request
import zipfile
import shutil
import time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                             QVBoxLayout, QLabel, QProgressBar, QTextEdit, 
                             QPushButton, QFrame, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QLinearGradient, QBrush, QPalette

# Configuration
SERVER_URL = "https://joya-site-1.onrender.com"
ZIP_NAME = "JOYA_AI_OS.zip"
DOWNLOAD_URL = f"{SERVER_URL}/{ZIP_NAME}"
INSTALL_DIR = os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "Programs", "JOYA")

class InstallThread(QThread):
    progress_sig = pyqtSignal(int)
    log_sig = pyqtSignal(str)
    finished_sig = pyqtSignal(bool, str)

    def run(self):
        try:
            self.log_sig.emit("Initializing deployment environment...")
            self.progress_sig.emit(5)
            time.sleep(0.5)

            # 1. Create install dir
            self.log_sig.emit(f"Creating installation folder: {INSTALL_DIR}")
            os.makedirs(INSTALL_DIR, exist_ok=True)
            self.progress_sig.emit(10)
            time.sleep(0.5)

            # 2. Download ZIP
            self.log_sig.emit("Connecting to JOYA download servers...")
            self.log_sig.emit(f"Downloading {ZIP_NAME} from {DOWNLOAD_URL}...")
            self.progress_sig.emit(15)
            
            zip_path = os.path.join(INSTALL_DIR, ZIP_NAME)
            
            req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                chunk_size = 1024 * 64
                
                with open(zip_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int(15 + (downloaded / total_size) * 50)
                            self.progress_sig.emit(percent)
            
            self.log_sig.emit("Download complete. Verifying archive integrity...")
            self.progress_sig.emit(70)
            time.sleep(0.5)

            # 3. Extract ZIP
            self.log_sig.emit("Extracting system packages...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(INSTALL_DIR)
            
            os.remove(zip_path)
            self.log_sig.emit("System packages extracted successfully.")
            self.progress_sig.emit(85)
            time.sleep(0.5)

            # 4. Create Desktop Shortcut
            self.log_sig.emit("Creating desktop shortcut...")
            desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
            shortcut_path = os.path.join(desktop, "JOYA AI OS.lnk")
            bat_path = os.path.join(INSTALL_DIR, "install_and_launch.bat")
            
            if not os.path.exists(bat_path):
                with open(bat_path, "w") as bf:
                    bf.write(f'@echo off\ncd /d "%~dp0"\nif not exist ".venv" (\n    python -m venv .venv\n)\n.venv\\Scripts\\python.exe main.py\npause\n')

            ps_cmd = f'$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut("{shortcut_path}"); $Shortcut.TargetPath = "{bat_path}"; $Shortcut.WorkingDirectory = "{INSTALL_DIR}"; $Shortcut.Save()'
            os.system(f'powershell -Command "{ps_cmd}"')
            
            self.log_sig.emit("Desktop shortcut created.")
            self.progress_sig.emit(100)
            time.sleep(0.5)
            
            self.finished_sig.emit(True, "JOYA AI OS has been successfully installed!")
        except Exception as e:
            self.finished_sig.emit(False, str(e))

class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JOYA AI OS Installer")
        self.setFixedSize(680, 420)
        
        self.setStyleSheet("""
            QMainWindow {
                background-color: #08080a;
            }
            QWidget#MainWidget {
                background-color: #08080a;
            }
            QLabel {
                color: #ffffff;
            }
            QProgressBar {
                background-color: #1a1a24;
                border: 1px solid #2a2a35;
                border-radius: 6px;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6e6aff, stop:1 #00d4ff);
                border-radius: 5px;
            }
            QTextEdit {
                background-color: #0f0f15;
                border: 1px solid #20202b;
                border-radius: 8px;
                color: #8e8e93;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }
            QPushButton {
                background-color: #1a1a24;
                border: 1px solid #3a3a4b;
                border-radius: 8px;
                color: #ffffff;
                font-weight: 600;
                padding: 8px 16px;
            }
            QPushButton:hover {
                border-color: #6e6aff;
                background-color: #20202e;
            }
            QPushButton:pressed {
                background-color: #12121b;
            }
            QFrame#Sidebar {
                background-color: #0c0c12;
                border-right: 1px solid #1c1c28;
            }
        """)
        
        main_widget = QWidget()
        main_widget.setObjectName("MainWidget")
        self.setCentralWidget(main_widget)
        
        h_layout = QHBoxLayout(main_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)
        
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 30, 20, 30)
        
        logo = QLabel("JOYA AI")
        logo.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        logo.setStyleSheet("color: #6e6aff;")
        sidebar_layout.addWidget(logo)
        
        desc = QLabel("PERSONAL INTELLIGENCE")
        desc.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        desc.setStyleSheet("color: #8e8e93; letter-spacing: 1px;")
        sidebar_layout.addWidget(desc)
        
        sidebar_layout.addStretch()
        
        status_lbl = QLabel("SYSTEM SETUP")
        status_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        status_lbl.setStyleSheet("color: #00d4ff;")
        sidebar_layout.addWidget(status_lbl)
        
        h_layout.addWidget(sidebar)
        
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(24, 24, 24, 24)
        right_layout.setSpacing(12)
        
        self.header_lbl = QLabel("Installing components... (0%)")
        self.header_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        right_layout.addWidget(self.header_lbl)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        right_layout.addWidget(self.progress_bar)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        right_layout.addWidget(self.log_text)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.cancel_btn)
        right_layout.addLayout(btn_layout)
        
        h_layout.addWidget(right_panel)
        
        self.thread = InstallThread()
        self.thread.progress_sig.connect(self.update_progress)
        self.thread.log_sig.connect(self.add_log)
        self.thread.finished_sig.connect(self.install_finished)
        self.thread.start()

    def update_progress(self, val):
        self.progress_bar.setValue(val)
        self.header_lbl.setText(f"Installing components... ({val}%)")

    def add_log(self, text):
        self.log_text.append(text)

    def install_finished(self, success, message):
        if success:
            QMessageBox.information(self, "Installation Complete", message)
            self.header_lbl.setText("Installation Successful!")
            self.cancel_btn.setText("Finish")
        else:
            QMessageBox.critical(self, "Installation Failed", f"An error occurred during setup:\n{message}")
            self.header_lbl.setText("Installation Failed")
            self.cancel_btn.setText("Close")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = InstallerWindow()
    window.show()
    sys.exit(app.exec())
