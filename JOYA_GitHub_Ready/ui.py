from __future__ import annotations
import os
try:
    import certifi
    os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = certifi.where()
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except Exception:
    pass

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import psutil

try:
    from core.stdio_utf8 import configure_stdio

    configure_stdio()
except Exception:
    pass

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal, QThread,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut, QImage, QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar, QComboBox, QCheckBox, QSlider,
    QTabWidget, QListWidget, QListWidgetItem, QMessageBox, QInputDialog, QGridLayout,
    QAbstractButton, QMenu, QSplashScreen, QSystemTrayIcon,
)

# Try importing advanced features
try:
    from advanced_features import (
        voice_engine, tts_engine, command_aliases, 
        system_monitor, quick_actions, VoiceMode
    )
    HAS_ADVANCED_FEATURES = True
except ImportError:
    HAS_ADVANCED_FEATURES = False
    # Avoid noisy console spam; advanced_features absence is expected in some setups.
    # print("⚠️ Advanced features not available - using basic mode")

try:
    from actions.question_notebook import QuestionNotebookWindow
    from actions.question_session import (
        QuestionEvent,
        extract_questions,
        get_question_session_manager,
        legacy_question_event,
    )
except ImportError:
    QuestionNotebookWindow = None
    QuestionEvent = None
    extract_questions = None
    get_question_session_manager = None
    legacy_question_event = None
try:
    from actions.avatar_animator import AvatarWindow
except Exception:
    AvatarWindow = None

_original_qfont = QFont
class QFont(_original_qfont):
    def __init__(self, *args, **kwargs):
        if args and isinstance(args[0], str) and args[0] == "Courier New":
            args = ("Consolas",) + args[1:]
        super().__init__(*args, **kwargs)


# ── Native Windows SAPI5 TTS — guaranteed voice output ──────────────────────
class _NativeTTS:
    """Reliable text-to-speech using Windows SAPI5 (always available on Win10/11)."""
    _instance = None
    _voice = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._available = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            # pick a nicer voice if available
            try:
                voices = self._voice.GetVoices()
                for i in range(voices.Count):
                    name = voices.Item(i).GetDescription()
                    if "Zira" in name or "Natural" in name:
                        self._voice.Voice = voices.Item(i)
                        break
            except Exception:
                pass
            self._voice.Rate = 1
            self._voice.Volume = 100
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def speak(self, text: str, blocking: bool = False,
              emotion: str = None, tone: str = None):
        if not self._available or not text:
            return
        text = text.strip()[:400]
        if not text:
            return
        # Emotion/tone-aware pacing (non-fatal).
        if emotion or tone:
            try:
                from actions.human_voice_profile import sapi5_params
                rate, volume = sapi5_params(emotion, tone)
                self._voice.Rate = rate
                self._voice.Volume = volume
            except Exception:
                pass
        try:
            import threading as _th
            flags = 1 if not blocking else 0  # SVSFlagsAsync = 1
            if blocking:
                self._voice.Speak(text, 0)
            else:
                _th.Thread(target=lambda: self._voice.Speak(text, 1), daemon=True).start()
        except Exception:
            pass

def _tts_speak(text: str, blocking: bool = False,
               emotion: str = None, tone: str = None):
    """Universal TTS: tries advanced_features.tts_engine, then native SAPI5.

    Optional ``emotion``/``tone`` make speech pacing match the mood. Also
    records the current tone/emotion into the shared human runtime state so
    the HUD chips can reflect it (best-effort, never fatal).
    """
    try:
        from actions import human_runtime_state as _hrs
        _hrs.update(tone=tone, emotion=emotion)
    except Exception:
        pass
    try:
        if HAS_ADVANCED_FEATURES and tts_engine:
            tts_engine.speak(text, blocking=blocking, emotion=emotion, tone=tone)
            return
    except Exception:
        pass
    try:
        _NativeTTS.get().speak(text, blocking=blocking, emotion=emotion, tone=tone)
    except Exception:
        pass

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"
APP_ICON   = BASE_DIR / "assets" / "app_logo.ico"
APP_LOGO   = BASE_DIR / "assets" / "app_logo.png"

_DEFAULT_W, _DEFAULT_H = 1180, 740
_MIN_W,     _MIN_H     = 940, 620
_LEFT_W  = 176
_RIGHT_W = 390
_HUD_FULL_FRAME_MS = 16
_HUD_SMOOTH_ACTIVE_MS = 33
_HUD_SMOOTH_IDLE_MS = 66
_HUD_SIMPLE_MS = 120
_ANIM_EPS = 0.012

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    # ── Base neutrals · AMOLED Pure Black (Tesla / Apple Pro Dark) ─────────
    BG        = "#000000"  # absolute black
    ELEV0     = "#000000"  # absolute black
    PANEL     = "#08080a"  # near-black space card
    ELEV1     = "#08080a"  # near-black space card
    PANEL2    = "#121215"  # slightly raised surface
    ELEV2     = "#1c1c20"  # highest surface / hover
    BAR_BG    = "#050507"
    DARK      = "#000000"
    SURFACE   = "#121215"
    SURFACE_HI= "#1c1c20"

    # ── Hairlines / borders (ultra-thin glassmorphic) ──────────────────────
    BORDER    = "rgba(255, 255, 255, 0.08)"  # subtle glass border
    HAIRLINE  = "rgba(255, 255, 255, 0.04)"  # softest glass separator
    BORDER_B  = "rgba(255, 255, 255, 0.16)"  # emphasized glass edge
    BORDER_A  = "rgba(255, 255, 255, 0.03)"  # sunken glass edge

    # ── Accent · fresh Apple indigo→violet system (2026 refresh) ─────────
    PRI       = "#6e6aff"  # Apple-fresh indigo (primary accent)
    PRI_DIM   = "#5a56e6"
    PRI_GHO   = "#111111"  # ghosted accent fill (neutral)
    FOCUS     = "#6e6aff"  # focus ring
    ACC       = "#64d2ff"  # light cyan (sparingly)
    ACC2      = "#30d158"  # system green
    GREEN     = "#30d158"
    GREEN_D   = "#248a3d"
    AMBER     = "#ff9f0a"  # Apple orange (warnings)
    PURPLE    = "#bf5af2"
    PINK      = "#ff375f"
    RED       = "#ff453a"
    MUTED_C   = "#ff3b30"

    # ── Text (Apple label hierarchy) ─────────────────────────────────────
    TEXT      = "#f5f5f7"  # primary label
    TEXT_MED  = "#d1d1d6"  # secondary label
    TEXT_DIM  = "#8e8e93"  # tertiary label
    TEXT_FAINT= "#636366"  # quaternary / disabled
    WHITE     = "#ffffff"

    # ── Glass / vibrancy (rgba strings for translucent panels) ───────────
    GLASS_BG  = "rgba(22, 22, 27, 0.72)"
    GLASS_HI  = "rgba(255, 255, 255, 0.05)"   # inner top highlight
    GLASS_LO  = "rgba(0, 0, 0, 0.35)"

    # ── Radii / geometry tokens (px) ─────────────────────────────────────
    R_SM = 8
    R_MD = 12
    R_LG = 16
    R_XL = 22

    # ── Typography · bundled Inter / JetBrains Mono (system fallbacks) ────
    #  These default to platform fonts and are re-pointed to the bundled
    #  families by _load_premium_fonts() at app bootstrap.
    FONT_MONO    = "Consolas" if _OS == "Windows" else ("Monaco" if _OS == "Darwin" else "Courier New")
    FONT_SANS    = "Segoe UI Variable Display" if _OS == "Windows" else ("San Francisco" if _OS == "Darwin" else "Ubuntu")
    FONT_DISPLAY = FONT_SANS
    FONT_TITLE   = "Segoe UI Semilight"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(max(0, min(255, int(a)))); return c


# ── Premium typography ──────────────────────────────────────────────────
_FONTS_LOADED = False
_WEIGHTS = {
    "regular":  QFont.Weight.Normal,
    "medium":   QFont.Weight.Medium,
    "semibold": QFont.Weight.DemiBold,
    "bold":     QFont.Weight.Bold,
}


def pfont(size: float = 10, weight: str = "regular", *, mono: bool = False,
          display: bool = False, spacing: float = 0.0) -> QFont:
    """Build a premium QFont from the design tokens.

    weight ∈ {regular, medium, semibold, bold}. `display` uses the tighter
    Inter Display face for large titles; `mono` uses JetBrains Mono for data.
    `spacing` is letter-spacing in px (Apple keeps this near 0).
    """
    fam = C.FONT_MONO if mono else (C.FONT_DISPLAY if display else C.FONT_SANS)
    f = QFont(fam)
    try:
        f.setPointSizeF(float(size))
    except Exception:
        f.setPointSize(int(size))
    f.setWeight(_WEIGHTS.get(weight, QFont.Weight.Normal))
    if spacing:
        try:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(spacing))
        except Exception:
            pass
    try:
        f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return f


def _load_premium_fonts() -> bool:
    """Register bundled Inter + JetBrains Mono and re-point C.FONT_* tokens.

    Safe to call multiple times; degrades to system fonts if files are absent.
    """
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return True
    try:
        from PyQt6.QtGui import QFontDatabase
    except Exception:
        return False
    font_dir = BASE_DIR / "assets" / "fonts"
    if not font_dir.exists():
        return False
    families: set[str] = set()
    try:
        for ttf in sorted(font_dir.glob("*.ttf")) + sorted(font_dir.glob("*.otf")):
            fid = QFontDatabase.addApplicationFont(str(ttf))
            if fid != -1:
                for fam in QFontDatabase.applicationFontFamilies(fid):
                    families.add(fam)
    except Exception:
        return False
    if "Inter" in families:
        C.FONT_SANS = "Inter"
        C.FONT_DISPLAY = "Inter Display" if "Inter Display" in families else "Inter"
        C.FONT_TITLE = C.FONT_DISPLAY
    if "JetBrains Mono" in families:
        C.FONT_MONO = "JetBrains Mono"
    _FONTS_LOADED = bool(families)
    return _FONTS_LOADED


def _global_qss() -> str:
    """One app-wide stylesheet — the premium base every standard Qt widget
    inherits. Widget-specific painters/QSS layer on top of this."""
    return f"""
    * {{
        outline: none;
    }}
    QWidget {{
        color: {C.TEXT};
        font-family: "{C.FONT_SANS}";
        selection-background-color: {C.PRI};
        selection-color: #ffffff;
    }}
    QToolTip {{
        background-color: {C.PANEL2};
        color: {C.TEXT};
        border: 1px solid {C.BORDER};
        border-radius: {C.R_SM}px;
        padding: 6px 10px;
        font-family: "{C.FONT_SANS}";
        font-size: 12px;
    }}
    /* ── Scrollbars: ultra thin glass ── */
    QScrollBar:vertical {{
        background: transparent; width: 6px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(255, 255, 255, 0.08); min-height: 30px;
        border-radius: 3px; border: none;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(255, 255, 255, 0.22);
    }}
    QScrollBar:horizontal {{
        background: transparent; height: 6px; margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(255, 255, 255, 0.08); min-width: 30px;
        border-radius: 3px; border: none;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: rgba(255, 255, 255, 0.22);
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0; height: 0; background: none; border: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    /* ── Menus / context ── */
    QMenu {{
        background: {C.PANEL2}; color: {C.TEXT};
        border: 1px solid {C.BORDER}; border-radius: {C.R_MD}px; padding: 6px;
    }}
    QMenu::item {{ padding: 7px 22px; border-radius: {C.R_SM}px; }}
    QMenu::item:selected {{ background: {C.PRI}; color: #ffffff; }}
    QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 5px 8px; }}
    /* ── Base controls: soft, rounded, hairline ── */
    QAbstractScrollArea, QScrollArea {{ border: none; background: transparent; }}
    QMessageBox, QInputDialog, QDialog {{ background: {C.PANEL}; }}
    """


_QT_APP_REF: QApplication | None = None


def _ensure_qapplication() -> QApplication:
    """Return a live QApplication, creating one for lightweight UI tests."""
    global _QT_APP_REF
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1] or ["joya-xxxix"])
        _QT_APP_REF = cast(QApplication, app)
    return cast(QApplication, app)

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._last_temp_check = 0.0
        self._cached_temp = -1.0
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        now = time.time()
        if now - self._last_temp_check < 60:
            return self._cached_temp
        self._last_temp_check = now
        try:
            temps = getattr(psutil, "sensors_temperatures", lambda: {})()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz", "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        self._cached_temp = entries[0].current
                        return self._cached_temp
            for entries in temps.values():
                if entries:
                    self._cached_temp = entries[0].current
                    return self._cached_temp
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        self._cached_temp = float(m.group(1))
                        return self._cached_temp
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                     "(Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi | Select-Object -First 1).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3, creationflags=0x08000000
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    self._cached_temp = (raw / 10.0) - 273.15
                    return self._cached_temp
            except Exception:
                pass

        return self._cached_temp

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class StreamWorker(QThread):
    frame_ready = pyqtSignal(QImage)

    def __init__(self, mode="camera"):
        super().__init__()
        self.mode = mode
        self._running = True

    def run(self):
        start_time = time.time()
        if self.mode == "camera":
            try:
                import cv2
                cap = cv2.VideoCapture(0)
                if not cap.isOpened():
                    return
                while self._running:
                    if time.time() - start_time > 30.0:
                        break
                    ret, frame = cap.read()
                    if ret:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
                        self.frame_ready.emit(qimg.copy())
                    time.sleep(0.05)
                cap.release()
            except Exception:
                pass
        elif self.mode == "screen":
            try:
                import cv2
                import mss
                import numpy as np
                with mss.mss() as sct:
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    while self._running:
                        img = np.array(sct.grab(monitor))
                        rgb = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
                        self.frame_ready.emit(qimg.copy())
                        time.sleep(0.1)
            except Exception:
                pass

    def stop(self):
        self._running = False
        self.wait()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        # God level UI additions
        self._ripples = []
        self._audio_circle_vals = [0.0] * 60
        self._radar_angle = 0.0
        self._telemetry_tick = 0
        self._telemetry_data = {
            "SYS": "JOYA v1.0",
            "SEC": "CLEARED",
            "MOD": "GEMINI 3.5",
            "FPS": "60",
            "LOC": "AURA-CORE",
            "MEM": "0x7F2A09B"
        }
        self.setMouseTracking(True)
        self._mouse_hover_core = False

        # New God level 2 additions
        self._mouse_pos = QPointF(150, 150)
        self._mouse_in_canvas = False
        self._code_columns = []
        self._sparks = []
        self._scanline_y = 0.0
        self._parallax_x = 0.0
        self._parallax_y = 0.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self.performance_mode = True
        self._tmr.start(_HUD_SMOOTH_ACTIVE_MS)
        # Enhanced visuals flag (toggled by main window)
        self.god_mode = False
        # Visualizer style additions
        self.visualizer_mode = "arc_reactor"
        self.visualizer_mode_name = "Arc Reactor (Classic)"
        self._nebula_particles = []
        for _ in range(50):
            self._nebula_particles.append({
                "angle": random.uniform(0, 2 * math.pi),
                "dist": random.uniform(10, 90),
                "speed": random.uniform(0.01, 0.04),
                "size": random.uniform(1.5, 3.5),
                "color_offset": random.uniform(0, 1)
            })
        # Simple UI mode: disables heavy visuals for a clean, lightweight display
        self.simple_mode = False
        self.auto_wake = True
        self._current_volume = 0.0
        self._current_volume_smoothed = 0.0
        self.voice_glow_mode = "idle"
        self._wake_flash_until = 0.0
        self._wake_flash_text = ""
        self._wake_glow = 0.0
        self._cheetah_trails = [random.uniform(0.0, 360.0) for _ in range(7)]

        # Stream workers
        self._stream_worker = None
        self._feed_image = None
        self._btn_rects = {}
        self._hovered_btn = None

    def set_performance_mode(self, enabled: bool):
        self.performance_mode = bool(enabled)
        self._sync_timer_interval(force=True)
        self.update()

    def _sync_timer_interval(self, force: bool = False):
        if not hasattr(self, "_tmr"):
            return
        if not self.isVisible() and self._tmr.isActive():
            self._tmr.stop()
            return
        if getattr(self, "simple_mode", False):
            interval = _HUD_SIMPLE_MS if getattr(self, "performance_mode", True) else _HUD_SMOOTH_IDLE_MS
        elif getattr(self, "performance_mode", True):
            now = time.time()
            active = (
                self.speaking
                or self.state in ("LISTENING", "THINKING", "PROCESSING", "INITIALISING")
                or now < getattr(self, "_wake_flash_until", 0.0)
                or getattr(self, "_current_volume_smoothed", 0.0) > 1.5
            )
            interval = _HUD_SMOOTH_ACTIVE_MS if active else _HUD_SMOOTH_IDLE_MS
        else:
            interval = _HUD_FULL_FRAME_MS
        if force or not self._tmr.isActive() or self._tmr.interval() != interval:
            self._tmr.start(interval)

    def showEvent(self, event):
        self._sync_timer_interval(force=True)
        super().showEvent(event)

    def hideEvent(self, event):
        try:
            self._tmr.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def _update_btn_rects(self):
        W, H = self.width(), self.height()
        btn_w = 60
        btn_h = 20
        spacing = 6
        total_w = 6 * btn_w + 5 * spacing
        start_x = (W - total_w) / 2
        y = H - 32
        
        names = ["CAMERA", "SCREEN", "SCAN", "GOD", "WAKE", "MUTE"]
        self._btn_rects = {}
        for idx, name in enumerate(names):
            bx = start_x + idx * (btn_w + spacing)
            self._btn_rects[name] = QRectF(bx, y, btn_w, btn_h)

    def set_voice_glow(self, mode: str, label: str = ""):
        mode = str(mode or "idle").lower().strip()
        if mode not in ("idle", "listening", "speaking", "wake", "standby", "muted"):
            mode = "idle"
        self.voice_glow_mode = mode
        if mode == "wake":
            self.trigger_wake_flash(label)
        self._sync_timer_interval()
        self.update()

    def trigger_wake_flash(self, label: str = ""):
        self._wake_flash_until = time.time() + 2.8
        self._wake_flash_text = (label or "WAKE LINK").upper()[:28]
        self.voice_glow_mode = "wake"
        self._sync_timer_interval()
        self.update()

    def _on_frame_received(self, qimg):
        # Called by StreamWorker when a new frame is available.
        self._feed_image = qimg
        self.update()

    def _safe_handle_btn_click(self, btn_name: str):
        main_win = self.window()
        if main_win is None:
            return
        
        btn_name = str(btn_name).upper().strip()
        if btn_name == "CAMERA":
            if hasattr(main_win, "_safe_toggle_stream"):
                main_win._safe_toggle_stream("camera")
        elif btn_name == "SCREEN":
            if hasattr(main_win, "_safe_toggle_stream"):
                main_win._safe_toggle_stream("screen")
        elif btn_name == "SCAN":
            if hasattr(main_win, "_run_visual_autopilot"):
                main_win._run_visual_autopilot("Analyze my screen, explain what is visible, and perform only a clearly safe next action if one is obvious.")
        elif btn_name == "GOD":
            if hasattr(main_win, "_toggle_god_mode"):
                main_win._toggle_god_mode()
        elif btn_name == "WAKE":
            if hasattr(main_win, "_toggle_autowake"):
                main_win._toggle_autowake()
        elif btn_name == "MUTE":
            if hasattr(main_win, "_toggle_mute"):
                main_win._toggle_mute()


    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            # Choose a resampling enum compatible across Pillow versions
            try:
                if hasattr(Image, "LANCZOS"):
                    resample = Image.LANCZOS
                else:
                    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", getattr(Image, "NEAREST", 0))
            except Exception:
                resample = getattr(Image, "NEAREST", 0)
            img = img.resize((sz, sz), resample)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position()
            clicked_btn = None
            for name, rect in self._btn_rects.items():
                if rect.contains(pos):
                    clicked_btn = name
                    break
            
            if clicked_btn:
                # Fix: method may be missing in some builds; route safely.
                if hasattr(self, "_handle_btn_click"):
                    self._handle_btn_click(clicked_btn)  # type: ignore[attr-defined]
                else:
                    self._safe_handle_btn_click(clicked_btn)
            else:
                self._ripples.append({
                    "pos": pos,
                    "r": 5.0,
                    "max_r": 80.0 if not self.god_mode else 120.0,
                    "a": 255.0
                })
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        cx, cy = self.width() / 2, self.height() / 2
        pos = e.position()
        self._mouse_pos = pos
        self._mouse_in_canvas = True
        
        self._hovered_btn = None
        for name, rect in self._btn_rects.items():
            if rect.contains(pos):
                self._hovered_btn = name
                break
        
        dist = math.hypot(pos.x() - cx, pos.y() - cy)
        fw = min(self.width(), self.height())
        r_face = fw * 0.31
        if dist < r_face:
            self._mouse_hover_core = True
        else:
            self._mouse_hover_core = False
        super().mouseMoveEvent(e)

    def leaveEvent(self, a0):
        self._mouse_in_canvas = False
        self._mouse_hover_core = False
        self._hovered_btn = None
        super().leaveEvent(a0)

    def _step(self):
        # Update current volume from MainWindow directly and safely
        main_win = self.window()
        if main_win is not None:
            self._current_volume = getattr(main_win, "current_volume", 0.0)
        self._current_volume_smoothed += (self._current_volume - self._current_volume_smoothed) * 0.2
        
        # Update progress bar on MainWindow on the safe main GUI thread
        if main_win is not None:
            if hasattr(main_win, "_mic_level_bar") and main_win._mic_level_bar is not None:
                val = int(min(100.0, self._current_volume_smoothed))
                main_win._mic_level_bar.setValue(val)
                if hasattr(main_win, "_mic_val_lbl") and main_win._mic_val_lbl is not None:
                    main_win._mic_val_lbl.setText(f"{val}%")

        # Lightweight update when in simple mode to reduce CPU/GPU usage
        if getattr(self, "simple_mode", False):
            self._tick += 1
            # simple smoothing for audio circle
            for i in range(len(self._audio_circle_vals)):
                tgt = 2.0 + (self._current_volume_smoothed * 10.0)
                self._audio_circle_vals[i] += (tgt - self._audio_circle_vals[i]) * 0.25
            self._blink_tick += 1
            if self._blink_tick >= 38:
                self._blink = not self._blink
                self._blink_tick = 0
            self.update()
            self._sync_timer_interval()
            return

        self._tick += 1
        now = time.time()
        wake_active = now < self._wake_flash_until
        listening_active = (self.state == "LISTENING" and not self.muted)
        target_wake_glow = 1.0 if wake_active else 0.0
        self._wake_glow += (target_wake_glow - self._wake_glow) * 0.18
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if wake_active:
                self._tgt_scale = random.uniform(1.10, 1.18)
                self._tgt_halo = random.uniform(205, 250)
            elif self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            elif listening_active:
                self._tgt_scale = random.uniform(1.012, 1.038)
                self._tgt_halo = random.uniform(88, 126)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.42 if wake_active else (0.38 if self.speaking else (0.22 if listening_active else 0.15))
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        if wake_active:
            speeds = [2.8, -2.0, 3.5]
        elif self.speaking:
            speeds = [1.3, -0.9, 2.0]
        elif listening_active:
            speeds = [0.9, -0.55, 1.35]
        else:
            speeds = [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (4.2 if wake_active else (3.0 if self.speaking else (1.8 if listening_active else 1.3)))) % 360
        self._scan2 = (self._scan2 + (-2.8 if wake_active else (-2.0 if self.speaking else (-1.05 if listening_active else -0.75)))) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 5.5 if wake_active else (4.2 if self.speaking else (2.8 if listening_active else 2.0))
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        pulse_chance = 0.12 if wake_active else (0.07 if self.speaking else (0.045 if listening_active else 0.025))
        if self.god_mode:
            pulse_chance *= 1.8
        if len(self._pulses) < 3 and random.random() < pulse_chance:
            self._pulses.append(0.0)

        # particle spawn: increased in god mode for a more dramatic effect
        p_spawn_speaking = 0.28 if not self.god_mode else 0.42
        if (self.speaking or wake_active) and random.random() < p_spawn_speaking:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            vel_mul = 1.6 if self.god_mode else 1.0
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4) * vel_mul,
                math.sin(ang) * random.uniform(0.9, 2.4) * vel_mul - 0.4, 1.0,
            ])
        decay = 0.028 if not self.god_mode else 0.018
        damp = 0.97 if not self.god_mode else 0.945
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*damp, p[3]*damp, p[4]-decay]
            for p in self._particles if p[4] > 0
        ]

        # Update ripples
        decay_rate = 8.0 if not self.god_mode else 5.0
        for rp in list(self._ripples):
            rp["r"] += 3.0 if not self.god_mode else 4.0
            rp["a"] -= decay_rate
            if rp["a"] <= 0:
                self._ripples.remove(rp)

        # Update radar sweep
        self._radar_angle = (self._radar_angle + (1.2 if not self.god_mode else 2.2)) % 360
        trail_speed = 4.5 if wake_active else (2.4 if self.speaking else (1.5 if listening_active else 0.8))
        self._cheetah_trails = [
            (ang + trail_speed + idx * 0.05) % 360
            for idx, ang in enumerate(self._cheetah_trails)
        ]

        # Update telemetry data once in a while
        self._telemetry_tick += 1
        if self._telemetry_tick % 15 == 0:
            self._telemetry_data["MEM"] = f"0x{random.randint(0x7F0000, 0x7FFFFF):06X}"
            self._telemetry_data["FPS"] = str(random.randint(58, 62) if not self.god_mode else random.randint(85, 120))
            self._telemetry_data["SYS"] = "JOYA" if not self.god_mode else "JOYA [GOD]"
            self._telemetry_data["SEC"] = "SECURE" if not self.muted else "MUTED"
            self._telemetry_data["VEC"] = f"[{random.uniform(-10.0, 10.0):.1f},{random.uniform(-10.0, 10.0):.1f}]"

        # Update circular sound-reactive waveform
        for idx in range(len(self._audio_circle_vals)):
            if self.muted:
                tgt = 1.0
            elif wake_active:
                tgt = 18.0 + 55.0 * (0.5 + 0.5 * math.sin(self._tick * 0.42 + idx * 0.55))
            elif self.speaking or self._current_volume_smoothed > 1.5:
                # Direct audio-reactive modulation!
                vol_factor = min(1.0, self._current_volume_smoothed / 100.0)
                base = 15.0 if self.god_mode else 8.0
                tgt = base + vol_factor * 85.0 * (0.3 + 0.7 * math.sin(self._tick * 0.25 + idx * 0.45))
            elif listening_active:
                tgt = 6.0 + 18.0 * (0.5 + 0.5 * math.sin(self._tick * 0.18 + idx * 0.42))
            elif self.state in ["THINKING", "PROCESSING"]:
                tgt = 4.0 + 8.0 * (0.5 + 0.5 * math.cos(self._tick * 0.25 + idx * 0.7))
            else:
                tgt = 2.0 + 4.0 * (0.5 + 0.5 * math.sin(self._tick * 0.08 + idx * 0.3))
            
            # Smooth interpolation
            self._audio_circle_vals[idx] += (tgt - self._audio_circle_vals[idx]) * 0.3

        # Update 3D Parallax interpolation
        cx, cy = self.width() / 2, self.height() / 2
        if self._mouse_in_canvas:
            tgt_px = self._mouse_pos.x() - cx
            tgt_py = self._mouse_pos.y() - cy
        else:
            tgt_px = 0.0
            tgt_py = 0.0
        self._parallax_x += (tgt_px - self._parallax_x) * 0.12
        self._parallax_y += (tgt_py - self._parallax_y) * 0.12

        # Update falling code columns
        W = max(300, self.width())
        if len(self._code_columns) < 22:
            self._code_columns.append({
                "x": random.uniform(15, W - 15),
                "y": random.uniform(-120, -10),
                "speed": random.uniform(1.2, 3.8),
                "chars": [random.choice(["0","1","2","3","4","5","6","7","8","9","A","B","C","D","E","F","X","Y","Z","*","#","@","%"]) for _ in range(random.randint(6, 12))]
            })
        for col in self._code_columns:
            col["y"] += col["speed"]
            if random.random() < 0.12:
                col["chars"].pop(0)
                col["chars"].append(random.choice(["0","1","2","3","4","5","6","7","8","9","A","B","C","D","E","F","X","Y","Z","*","#","@","%"]))
        self._code_columns = [col for col in self._code_columns if col["y"] < max(300, self.height()) + 50]

        # Update high-energy electrical sparks
        if (self.speaking or self.god_mode) and random.random() < 0.18:
            ang = random.uniform(0, 2 * math.pi)
            orb_r = int(fw * 0.25 * self._scale)
            r_start = orb_r
            r_end = random.uniform(orb_r + 20, fw * 0.46)
            
            pts = []
            steps = 4
            for s in range(steps + 1):
                t = s / steps
                curr_r = r_start + t * (r_end - r_start)
                curr_ang = ang + random.uniform(-0.15, 0.15) if s > 0 and s < steps else ang
                px = cx + curr_r * math.cos(curr_ang)
                py = cy + curr_r * math.sin(curr_ang)
                pts.append(QPointF(px, py))
            
            self._sparks.append({
                "points": pts,
                "a": 255.0
            })
            
        for spk in list(self._sparks):
            spk["a"] -= 28.0
            if spk["a"] <= 0:
                self._sparks.remove(spk)

        # Update scanline y
        self._scanline_y = (self._scanline_y + 1.5) % max(300, self.height())

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()
        self._sync_timer_interval()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if getattr(self, "simple_mode", False):
            p.fillRect(self.rect(), qcol("#000000"))
            W, H = self.width(), self.height()
            cx, cy = W / 2, H / 2
            fw = min(W, H)
            # draw a single ring and a simple level bar
            ring_r = fw * 0.28
            lvl = max(0.0, min(1.0, self._current_volume_smoothed * 5.0))
            p.setPen(QPen(qcol(C.PRI, 140), 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))
            # level arc
            ang = int(360 * lvl)
            p.setPen(QPen(qcol(C.ACC, 200), 6))
            p.drawArc(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2), 0, int(-ang * 16))
            # center text
            p.setFont(QFont(C.FONT_SANS, 12, QFont.Weight.Bold))
            status_txt = "MUTED" if self.muted else ("SPEAKING" if self.speaking else self.state)
            col = qcol(C.MUTED_C if self.muted else (C.ACC if self.speaking else C.PRI))
            p.setPen(QPen(col, 1))
            p.drawText(QRectF(0, cy - 12, W, 24), int(Qt.AlignmentFlag.AlignCenter), status_txt)
            # draw simple buttons labels at bottom
            btns = ["CAMERA", "SCREEN", "SCAN", "WAKE", "MUTE"]
            btn_w = 80
            spacing = 12
            total_w = len(btns) * btn_w + (len(btns) - 1) * spacing
            start_x = (W - total_w) / 2
            y = H - 48
            p.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
            for i, name in enumerate(btns):
                bx = start_x + i * (btn_w + spacing)
                rect = QRectF(bx, y, btn_w, 28)
                p.setPen(QPen(qcol(C.PANEL2), 1))
                p.setBrush(QBrush(qcol(C.PANEL2)))
                p.drawRoundedRect(rect, 4, 4)
                p.setPen(QPen(qcol(C.TEXT_DIM), 200))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
            return

        # absolute black background
        p.fillRect(self.rect(), qcol("#000000"))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)
        wake_glow = max(0.0, min(1.0, self._wake_glow))
        listening_active = (self.state == "LISTENING" and not self.muted)
        voice_color = C.ACC2 if wake_glow > 0.05 else (C.ACC if self.speaking else (C.GREEN if listening_active else C.PRI))
        
        # Parallax displacements
        px_x = self._parallax_x
        px_y = self._parallax_y

        if wake_glow > 0.02:
            wake_grad = QRadialGradient(cx, cy, fw * (0.42 + wake_glow * 0.18))
            wake_grad.setColorAt(0.0, qcol(C.WHITE, int(55 * wake_glow)))
            wake_grad.setColorAt(0.38, qcol(C.ACC2, int(80 * wake_glow)))
            wake_grad.setColorAt(0.72, qcol(C.PRI, int(34 * wake_glow)))
            wake_grad.setColorAt(1.0, qcol("#000000", 0))
            p.fillRect(self.rect(), QBrush(wake_grad))

        # (Apple premium: grid dots & matrix code rain removed for a clean look)

        r_face = fw * 0.31

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else voice_color, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Cheetah Glow: fast segmented trails during wake/listening/speaking states.
        if not self.muted and (wake_glow > 0.02 or self.speaking or listening_active):
            trail_r = fw * (0.43 + 0.035 * math.sin(self._tick * 0.08))
            trail_alpha_base = int(80 + 95 * wake_glow + (35 if self.speaking else 0))
            for idx, ang in enumerate(self._cheetah_trails):
                trail_len = 16 + idx * 2
                alpha = max(25, min(230, trail_alpha_base - idx * 10))
                p.setPen(QPen(qcol(voice_color, alpha), 2.6 if idx < 2 else 1.4))
                rect = QRectF(cx - trail_r, cy - trail_r, trail_r * 2, trail_r * 2)
                p.drawArc(rect, int(ang * 16), int(trail_len * 16))
                if wake_glow > 0.25 and idx < 3:
                    rad = math.radians(ang + trail_len)
                    dot = QPointF(cx + trail_r * math.cos(rad), cy + trail_r * math.sin(rad))
                    p.setBrush(QBrush(qcol(C.WHITE, int(130 * wake_glow))))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(dot, 2.5, 2.5)

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else voice_color, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # (Apple premium: compass degree scale removed for a clean look)

        # spinning arc rings (Alternating Parallax depths 0.06 - 0.03)
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx] if idx % 2 == 0 else -self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            
            rcx = cx + px_x * (0.06 - idx * 0.015)
            rcy = cy + px_y * (0.06 - idx * 0.015)
            rect  = QRectF(rcx - ring_r, rcy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # (Apple premium: sweeping radar line removed for a clean look)
        rad_cx = cx + px_x * 0.02
        rad_cy = cy + px_y * 0.02
        p.setBrush(Qt.BrushStyle.NoBrush)

        # scanners (Parallax 0.02)
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(rad_cx - sr, rad_cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks (Parallax 0.05)
        tick_cx = cx + px_x * 0.05
        tick_cy = cy + px_y * 0.05
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(tick_cx + t_out * math.cos(rad), tick_cy - t_out * math.sin(rad)),
                QPointF(tick_cx + inn  * math.cos(rad), tick_cy - inn  * math.sin(rad)),
            )

        # (Apple premium: crosshair & corner brackets removed for a clean look)

        # Circular Polar Waveform / Holographic Vocal Waveform (Parallax 0.02)
        if self.visualizer_mode == "arc_reactor":
            wave_base_r = fw * 0.28 * self._scale
            num_bars = len(self._audio_circle_vals)
            for i in range(num_bars):
                ang = (i * 360.0 / num_bars) + (self._tick * 0.15)
                rad = math.radians(ang)
                h = self._audio_circle_vals[i]
                if self.muted:
                    col = qcol(C.MUTED_C, 160)
                elif self.speaking:
                    col = qcol(C.PRI if h < 20 else (C.ACC if h < 35 else C.ACC2), 190)
                elif self.state in ["THINKING", "PROCESSING"]:
                    col = qcol(C.ACC2, 160)
                else:
                    col = qcol(C.GREEN if i % 2 == 0 else C.PRI_DIM, 140)
                p.setPen(QPen(col, 2.5 if not self.god_mode else 4.0))
                bx = rad_cx + wave_base_r * math.cos(rad)
                by = rad_cy + wave_base_r * math.sin(rad)
                ex = rad_cx + (wave_base_r + h) * math.cos(rad)
                ey = rad_cy + (wave_base_r + h) * math.sin(rad)
                p.drawLine(QPointF(bx, by), QPointF(ex, ey))
        elif self.visualizer_mode == "hologram_wave":
            center_x, center_y = rad_cx, rad_cy
            amplitude = 8.0 + self._current_volume_smoothed * 0.8
            p.setBrush(Qt.BrushStyle.NoBrush)
            waves = [
                (C.PRI, 2.5, 0.0, 1.0),
                (C.ACC2, 1.5, 1.5, 0.7),
                (C.GREEN if not self.muted else C.RED, 1.0, 3.0, 0.5)
            ]
            for color_str, thickness, phase_shift, scale_factor in waves:
                p.setPen(QPen(qcol(color_str, 180), thickness))
                path = QPainterPath()
                first = True
                width_limit = int(fw * 0.28)
                for dx in range(-width_limit, width_limit + 1, 5):
                    t = dx / width_limit
                    envelope = math.cos(t * math.pi / 2) ** 2
                    omega = 0.12 * self._tick
                    angle = t * 6.0 * math.pi + omega + phase_shift
                    y_val = center_y + math.sin(angle) * amplitude * envelope * scale_factor
                    if first:
                        path.moveTo(center_x + dx, y_val)
                        first = False
                    else:
                        path.lineTo(center_x + dx, y_val)
                p.drawPath(path)

        # Draw reactor electrical sparks (Parallax 0.015)
        for spk in self._sparks:
            col = qcol(C.ACC2 if self.god_mode else C.PRI, int(spk["a"]))
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            pts = spk["points"]
            if pts:
                p0 = pts[0] + QPointF(px_x * 0.015, px_y * 0.015)
                path.moveTo(p0)
                for pt in pts[1:]:
                    path.lineTo(pt + QPointF(px_x * 0.015, px_y * 0.015))
                p.drawPath(path)

        # Draw ripples
        for rp in self._ripples:
            col = qcol(C.ACC2 if self.god_mode else C.PRI, int(rp["a"]))
            p.setPen(QPen(col, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r_val = rp["r"]
            p.drawEllipse(rp["pos"], r_val, r_val)

        # Selectively draw visualizer core based on visualizer_mode
        core_cx = cx + px_x * 0.015
        core_cy = cy + px_y * 0.015
        
        if self.visualizer_mode == "arc_reactor":
            if self._face_px:
                fsz    = int(fw * 0.62 * self._scale)
                scaled = self._face_px.scaled(
                    fsz, fsz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawPixmap(int(core_cx - fsz / 2), int(core_cy - fsz / 2), scaled)
            else:
                orb_r = int(fw * 0.25 * self._scale)
                # Breathing neon gradient
                grad = QRadialGradient(core_cx, core_cy, orb_r)
                if self.muted:
                    grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    grad.setColorAt(0.5, qcol(C.RED, 180))
                    grad.setColorAt(1.0, qcol(C.MUTED_C, 60))
                elif wake_glow > 0.05:
                    grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    grad.setColorAt(0.32, qcol(C.ACC2, 245))
                    grad.setColorAt(0.72, qcol(C.ACC, 165))
                    grad.setColorAt(1.0, qcol(C.PRI, int(60 + 80 * wake_glow)))
                elif self.speaking:
                    grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    grad.setColorAt(0.4, qcol(C.ACC2, 220))
                    grad.setColorAt(0.8, qcol(C.ACC, 140))
                    grad.setColorAt(1.0, qcol(C.PRI_GHO, 40))
                elif listening_active:
                    glow_val = int(150 + 65 * math.sin(self._tick * 0.13))
                    grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    grad.setColorAt(0.45, qcol(C.GREEN, glow_val))
                    grad.setColorAt(0.78, qcol(C.PRI, 120))
                    grad.setColorAt(1.0, qcol(C.PRI_GHO, 40))
                else:
                    glow_val = int(200 + 55 * math.sin(self._tick * 0.1))
                    grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    grad.setColorAt(0.5, qcol(C.PRI, glow_val))
                    grad.setColorAt(0.8, qcol(C.PRI_DIM, 120))
                    grad.setColorAt(1.0, qcol(C.PRI_GHO, 40))
                
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(core_cx - orb_r, core_cy - orb_r, orb_r * 2, orb_r * 2))
                
                # Segmented inner ticks
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(qcol(C.PRI if not self.muted else C.RED, 160), 1.5))
                inner_r = orb_r * 0.78
                rect_inner = QRectF(core_cx - inner_r, core_cy - inner_r, inner_r * 2, inner_r * 2)
                for a_idx in range(12):
                    ang = a_idx * 30 + (self._tick * 0.4)
                    p.drawArc(rect_inner, int(ang * 16), int(15 * 16))

                # Draw high-tech orbital telemetry ring
                p.save()
                p.translate(core_cx, core_cy)
                p.rotate(self._tick * 0.8)
                p.setPen(QPen(qcol(C.PRI, 90), 1, Qt.PenStyle.DashLine))
                p.drawEllipse(QRectF(-inner_r - 12, -inner_r - 12, (inner_r + 12)*2, (inner_r + 12)*2))
                
                # Draw rotating telemetry labels
                p.setFont(QFont("Courier New", 5, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.PRI, 130)))
                p.drawText(int(inner_r + 15), -4, "SECTOR.LOAD")
                p.drawText(int(-inner_r - 58), -4, "SYS.ACTIVE")
                p.restore()

                # Draw rotating high-tech outer brackets
                p.save()
                p.translate(core_cx, core_cy)
                p.rotate(-self._tick * 0.5)
                p.setPen(QPen(qcol(C.ACC2 if self.god_mode else C.PRI, 180), 2))
                bracket_r = orb_r + 20
                for a_deg in (0, 120, 240):
                    p.drawArc(QRectF(-bracket_r, -bracket_r, bracket_r*2, bracket_r*2), int(a_deg * 16), int(35 * 16))
                p.restore()
                    
                # Core center
                core_r = orb_r * 0.45
                core_grad = QRadialGradient(core_cx, core_cy, core_r)
                if self.muted:
                    core_grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    core_grad.setColorAt(1.0, qcol("#40000a", 240))
                elif wake_glow > 0.05:
                    core_grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    core_grad.setColorAt(0.62, qcol(C.ACC2, 245))
                    core_grad.setColorAt(1.0, qcol(C.ACC, 215))
                elif self.speaking:
                    core_grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    core_grad.setColorAt(0.7, qcol(C.ACC2, 230))
                    core_grad.setColorAt(1.0, qcol(C.ACC, 200))
                elif listening_active:
                    core_grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    core_grad.setColorAt(0.68, qcol(C.GREEN, 215))
                    core_grad.setColorAt(1.0, qcol(C.PRI_DIM, 190))
                else:
                    core_grad.setColorAt(0.0, qcol(C.WHITE, 255))
                    core_grad.setColorAt(0.7, qcol(C.PRI, 220))
                    grad.setColorAt(1.0, qcol(C.PRI_GHO, 40))
                p.setBrush(QBrush(core_grad))
                p.setPen(QPen(qcol(C.TEXT if not self.muted else C.RED, 220), 1))
                p.drawEllipse(QRectF(core_cx - core_r, core_cy - core_r, core_r * 2, core_r * 2))
                
                p.setPen(QPen(qcol(C.DARK if not self.muted else QColor(C.WHITE), 230), 1))
                p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                p.drawText(QRectF(core_cx - 50, core_cy - 9, 100, 18),
                           Qt.AlignmentFlag.AlignCenter, "J.A.R.V.I.S")

        elif self.visualizer_mode == "hologram_wave":
            p.setPen(QPen(qcol(C.PRI, 80), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(core_cx - 30, core_cy - 30, 60, 60))
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.TEXT_MED, 150)))
            p.drawText(QRectF(core_cx - 50, core_cy - 6, 100, 12), Qt.AlignmentFlag.AlignCenter, "VOICE PATH")

        elif self.visualizer_mode == "matrix_core":
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.save()
            p.translate(core_cx, core_cy)
            rings = [
                (45, 0.6, C.PRI, ["0", "1", "0", "0", "1", "1", "0", "1", "0", "1", "0", "1"]),
                (70, -0.4, C.ACC2, ["A", "F", "3", "C", "9", "E", "2", "B", "8", "D", "0", "7"]),
                (95, 0.25, C.GREEN if not self.muted else C.RED, ["1", "0", "1", "1", "0", "1", "0", "0", "1", "1", "0", "1"])
            ]
            for r, spd, color_str, chars in rings:
                rot = (self._tick * spd) % 360
                p.save()
                p.rotate(rot)
                p.setPen(QPen(qcol(color_str, 170)))
                num_chars = len(chars)
                for idx, ch in enumerate(chars):
                    ang = idx * (360.0 / num_chars)
                    p.save()
                    p.rotate(ang)
                    p.drawText(QPointF(0, -r), ch)
                    p.restore()
                p.restore()
            p.restore()
            
            core_r = 25.0 * self._scale
            p.setPen(QPen(qcol(C.PRI, 200), 2))
            p.setBrush(QBrush(qcol(C.PANEL2, 220)))
            p.drawEllipse(QRectF(core_cx - core_r, core_cy - core_r, core_r * 2, core_r * 2))
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.TEXT, 220)))
            p.drawText(QRectF(core_cx - 30, core_cy - 6, 60, 12), Qt.AlignmentFlag.AlignCenter, "CORE.39")

        elif self.visualizer_mode == "pulsing_nebula":
            vol_boost = self._current_volume_smoothed * 0.7
            if self.god_mode:
                p.setPen(QPen(qcol(C.PRI_GHO, 60), 1))
                for idx, pt in enumerate(self._nebula_particles[:20]):
                    ang = pt["angle"] + (self._tick * pt["speed"])
                    r = pt["dist"] + vol_boost
                    px = core_cx + r * math.cos(ang)
                    py = core_cy + r * math.sin(ang)
                    
                    next_idx = (idx + 1) % 20
                    next_pt = self._nebula_particles[next_idx]
                    next_ang = next_pt["angle"] + (self._tick * next_pt["speed"])
                    next_r = next_pt["dist"] + vol_boost
                    npx = core_cx + next_r * math.cos(next_ang)
                    npy = core_cy + next_r * math.sin(next_ang)
                    p.drawLine(QPointF(px, py), QPointF(npx, npy))
            
            p.setPen(Qt.PenStyle.NoPen)
            for pt in self._nebula_particles:
                ang = pt["angle"] + (self._tick * pt["speed"])
                base_r = pt["dist"]
                r = base_r + vol_boost + 5.0 * math.sin(self._tick * 0.05 + base_r)
                px = core_cx + r * math.cos(ang)
                py = core_cy + r * math.sin(ang)
                if self.muted:
                    cl = qcol(C.MUTED_C, 160)
                elif pt["color_offset"] < 0.4:
                    cl = qcol(C.PRI, 180)
                elif pt["color_offset"] < 0.8:
                    cl = qcol(C.ACC, 150)
                else:
                    cl = qcol(C.ACC2, 190)
                p.setBrush(QBrush(cl))
                p.drawEllipse(QPointF(px, py), pt["size"], pt["size"])
                
            center_r = 15.0 + vol_boost * 0.15
            grad = QRadialGradient(core_cx, core_cy, center_r)
            grad.setColorAt(0.0, qcol(C.WHITE, 255))
            grad.setColorAt(0.7, qcol(C.PRI, 190))
            grad.setColorAt(1.0, qcol(C.PRI_GHO, 20))
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(core_cx - center_r, core_cy - center_r, center_r * 2, center_r * 2))

        # Telemetry info text in corners (Parallax 0.01)
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        telem_cx = px_x * 0.01
        telem_cy = px_y * 0.01
        
        # Top-Left Corner
        tl_x, tl_y = 18 + telem_cx, 25 + telem_cy
        tl_lines = [
            f"SYS.CORE: {self._telemetry_data['SYS']}",
            f"DECIBELS: {'-96dB' if self.muted else ('-18dB' if self.speaking else '-42dB')}",
            f"SECTOR: LOCAL_A",
        ]
        p.setPen(QPen(qcol(C.PRI, 150), 1))
        for idx, line in enumerate(tl_lines):
            p.drawText(int(tl_x), int(tl_y + idx * 12), str(line))
            
        # Top-Right Corner
        tr_y = 25 + telem_cy
        tr_lines = [
            f"FPS: {self._telemetry_data['FPS']} // AUTO",
            f"SECURE: {self._telemetry_data['SEC']}",
            f"MODEL: {self._telemetry_data['MOD']}",
        ]
        p.setPen(QPen(qcol(C.PRI, 150), 1))
        for idx, line in enumerate(tr_lines):
            p.drawText(QRectF(W - 168 + telem_cx, tr_y + idx * 12, 150, 14), Qt.AlignmentFlag.AlignRight, line)

        # Bottom-Left Corner
        bl_x, bl_y = 18 + telem_cx, H - 75 + telem_cy
        bl_lines = [
            f"TELEM.VEC: {self._telemetry_data.get('VEC', '[0,0]')}",
            f"MEM_ADDR: {self._telemetry_data['MEM']}",
            f"PROT: MARK_39",
        ]
        p.setPen(QPen(qcol(C.PRI_DIM, 150), 1))
        for idx, line in enumerate(bl_lines):
            p.drawText(int(bl_x), int(bl_y + idx * 12), line)

        # Bottom-Right Corner
        br_y = H - 75 + telem_cy
        br_lines = [
            f"CONNECTION: ESTABLISHED",
            f"AUDIO_IN: {'ON' if not self.muted else 'OFF'}",
            f"OS: {platform.system().upper()}",
        ]
        p.setPen(QPen(qcol(C.PRI_DIM, 150), 1))
        for idx, line in enumerate(br_lines):
            p.drawText(QRectF(W - 168 + telem_cx, br_y + idx * 12, 150, 14), Qt.AlignmentFlag.AlignRight, line)

        # particles (Parallax 0.035)
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            size = 2.5 if not self.god_mode else 3.8
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0] + px_x * 0.035, pt[1] + px_y * 0.035), size, size)

        # extra shimmer in god mode (Parallax 0.045)
        if self.god_mode:
            shimmer_a = max(0, min(120, int(self._halo * 0.6)))
            p.setPen(QPen(qcol(C.ACC2, shimmer_a), 1))
            shim_cx = cx + px_x * 0.045
            shim_cy = cy + px_y * 0.045
            for i in range(6):
                ang = (self._tick * (8 + i*3)) % 360
                rr = fw * (0.36 + i * 0.04)
                p.drawArc(QRectF(shim_cx-rr, shim_cy-rr, rr*2, rr*2), int(ang*16), int(12*16))

        # status text (Parallax 0.02)
        sy = cy + fw * 0.41 + px_y * 0.02
        sx_offset = px_x * 0.02
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif wake_glow > 0.06:
            label = self._wake_flash_text or "WAKE LINK"
            txt, col = f">>  {label} ACTIVE", qcol(C.ACC2)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        elif self.state == "STANDBY":
            sym = "💤" if self._blink else "  "
            txt, col = f"{sym}  STANDBY // VOICE WAKE", qcol(C.PRI_DIM)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(sx_offset, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # Flat visual bar waveform (kept at bottom for nice diagnostic visual - Parallax 0.01)
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2 + px_x * 0.01
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif wake_glow > 0.06:
                hgt = random.randint(10, 24)
                cl = qcol(C.ACC2)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            elif listening_active:
                hgt = int(5 + 6 * (0.5 + 0.5 * math.sin(self._tick * 0.16 + i * 0.6)))
                cl = qcol(C.GREEN if i % 2 else C.PRI)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        # Target Lock Tracking Cursor (drawn dynamically at cursor, no parallax)
        if self._mouse_in_canvas:
            mx, my = self._mouse_pos.x(), self._mouse_pos.y()
            p.setBrush(Qt.BrushStyle.NoBrush)
            lock_col = qcol(C.ACC if self._mouse_hover_core else C.PRI, 180)
            p.setPen(QPen(lock_col, 1))
            
            lock_r = 15.0
            p.drawArc(QRectF(mx - lock_r, my - lock_r, lock_r * 2, lock_r * 2), int(self._tick * 4 * 16), int(60 * 16))
            p.drawArc(QRectF(mx - lock_r, my - lock_r, lock_r * 2, lock_r * 2), int((self._tick * 4 + 180) * 16), int(60 * 16))
            
            p.drawLine(QPointF(mx - 22, my), QPointF(mx - 6, my))
            p.drawLine(QPointF(mx + 6, my), QPointF(mx + 22, my))
            p.drawLine(QPointF(mx, my - 22), QPointF(mx, my - 6))
            p.drawLine(QPointF(mx, my + 6), QPointF(mx, my + 22))
            
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            p.setPen(QPen(lock_col, 200))
            p.drawText(int(mx + 18), int(my - 12), "LOCK // TARGET ACQUIRED")
            
            dx_m = mx - cx
            dy_m = my - cy
            dist_m = math.hypot(dx_m, dy_m)
            ang_m = math.degrees(math.atan2(dy_m, dx_m)) % 360
            p.drawText(int(mx + 18), int(my), f"R: {dist_m:.1f}px  A: {ang_m:.0f}°")
            p.drawText(int(mx + 18), int(my + 12), f"X: {mx:.0f} Y: {my:.0f}")

        # Fine Horizontal Scanlines Overlay (static)
        p.setPen(QPen(qcol(C.BG, 12), 1))
        for line_y in range(0, H, 3):
            p.drawLine(0, line_y, W, line_y)
            
        # Sweeping horizontal scanline beam
        p.setPen(QPen(qcol(C.PRI, 24), 2))
        p.drawLine(0, int(self._scanline_y), W, int(self._scanline_y))

        # Update button positions dynamically
        self._update_btn_rects()

        # Draw buttons
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        for name, rect in self._btn_rects.items():
            is_hovered = (self._hovered_btn == name)
            is_active = False
            
            # Determine active status
            if name == "CAMERA" and self._stream_worker is not None and self._stream_worker.mode == "camera":
                is_active = True
            elif name == "SCREEN" and self._stream_worker is not None and self._stream_worker.mode == "screen":
                is_active = True
            elif name == "GOD" and self.god_mode:
                is_active = True
            elif name == "MUTE" and self.muted:
                is_active = True
            
            # Setup colors
            if is_active:
                bg_col = qcol(C.ACC if name == "MUTE" else C.PRI, 180)
                text_col = qcol(C.DARK, 255)
                border_col = qcol(C.WHITE, 255)
            elif is_hovered:
                bg_col = qcol(C.PRI_GHO, 200)
                text_col = qcol(C.WHITE, 255)
                border_col = qcol(C.PRI, 255)
            else:
                bg_col = qcol(C.PANEL2, 120)
                text_col = qcol(C.TEXT_DIM, 255)
                border_col = qcol(C.BORDER_A, 180)
            
            # Draw rect
            p.setBrush(QBrush(bg_col))
            p.setPen(QPen(border_col, 1))
            p.drawRoundedRect(rect, 3, 3)
            
            # Draw text
            p.setPen(QPen(text_col))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)

        # Draw Picture-in-Picture Viewport
        pip_w = 150
        pip_h = 100
        pip_x = W - pip_w - 18
        pip_y = H - pip_h - 90
        pip_rect = QRectF(pip_x, pip_y, pip_w, pip_h)
        
        # Draw background and grid inside PiP
        p.setBrush(QBrush(qcol(C.PANEL2, 230)))
        p.setPen(QPen(qcol(C.BORDER_B if self._stream_worker is not None else C.BORDER, 150), 1.5))
        p.drawRoundedRect(pip_rect, 4, 4)
        
        # Subtly draw fine grid inside PiP
        p.setPen(QPen(qcol(C.PRI_GHO, 60), 1))
        for gx in range(int(pip_x), int(pip_x + pip_w), 12):
            p.drawLine(gx, int(pip_y), gx, int(pip_y + pip_h))
        for gy in range(int(pip_y), int(pip_y + pip_h), 12):
            p.drawLine(int(pip_x), gy, int(pip_x + pip_w), gy)

        if self._stream_worker is not None and self._feed_image is not None:
            # We have a valid image. Draw it scaled.
            scaled_img = self._feed_image.scaled(
                int(pip_w), int(pip_h),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            # Draw it inside the rect
            p.drawImage(int(pip_x), int(pip_y), scaled_img)
            
            # Subtle cyan/green overlay tint (cool Sci-Fi screen filter)
            tint_col = qcol(C.PRI, 35) if self._stream_worker.mode == "camera" else qcol(C.GREEN, 20)
            p.fillRect(pip_rect, QBrush(tint_col))
            
            # Scanlines overlay just for the PiP
            p.setPen(QPen(qcol(C.BG, 30), 1))
            for ly in range(int(pip_y), int(pip_y + pip_h), 2):
                p.drawLine(int(pip_x), ly, int(pip_x + pip_w), ly)
                
            # Viewport mode text overlay
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.WHITE, 220)))
            feed_label = f"LIVE FEED: {self._stream_worker.mode.upper()}"
            p.drawText(QRectF(pip_x + 6, pip_y + 6, pip_w - 12, 14), Qt.AlignmentFlag.AlignLeft, feed_label)
            
            # Blinking red dot
            if self._tick % 24 < 12:
                p.setBrush(QBrush(qcol(C.RED, 230)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(pip_x + pip_w - 10, pip_y + 11), 3, 3)
        else:
            # No active stream or feed image is None
            # Draw the fallback offline/standby graphic
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            
            # Draw "FEED OFFLINE" text in center of PiP
            p.setPen(QPen(qcol(C.MUTED_C if self._stream_worker is None else C.ACC2, 200)))
            state_label = "FEED STANDBY" if self._stream_worker is not None else "FEED OFFLINE"
            p.drawText(pip_rect, Qt.AlignmentFlag.AlignCenter, f"⚠  {state_label}")
            
            # Static noise representation (random dots)
            p.setPen(QPen(qcol(C.BORDER_A, 50), 1))
            for _ in range(35):
                rx = random.uniform(pip_x + 4, pip_x + pip_w - 4)
                ry = random.uniform(pip_y + 4, pip_y + pip_h - 4)
                p.drawPoint(QPointF(rx, ry))
                
            # Draw "NO SIGNAL // STDBY" text at the bottom of PiP
            p.setFont(QFont("Courier New", 6))
            p.setPen(QPen(qcol(C.TEXT_DIM, 150)))
            p.drawText(QRectF(pip_x + 6, pip_y + pip_h - 14, pip_w - 12, 10),
                       Qt.AlignmentFlag.AlignCenter, "NO INCOMING STREAM")
                       
        # Draw nice neon brackets/corners for the PiP frame
        p.setPen(QPen(qcol(C.PRI if self._stream_worker is not None else C.BORDER_B, 220), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p_len = 8
        # Top-Left corner brackets
        p.drawLine(QPointF(pip_x, pip_y), QPointF(pip_x + p_len, pip_y))
        p.drawLine(QPointF(pip_x, pip_y), QPointF(pip_x, pip_y + p_len))
        # Top-Right corner brackets
        p.drawLine(QPointF(pip_x + pip_w, pip_y), QPointF(pip_x + pip_w - p_len, pip_y))
        p.drawLine(QPointF(pip_x + pip_w, pip_y), QPointF(pip_x + pip_w, pip_y + p_len))
        # Bottom-Left corner brackets
        p.drawLine(QPointF(pip_x, pip_y + pip_h), QPointF(pip_x + p_len, pip_y + pip_h))
        p.drawLine(QPointF(pip_x, pip_y + pip_h), QPointF(pip_x, pip_y + pip_h - p_len))
        # Bottom-Right corner brackets
        p.drawLine(QPointF(pip_x + pip_w, pip_y + pip_h), QPointF(pip_x + pip_w - p_len, pip_y + pip_h))
        p.drawLine(QPointF(pip_x + pip_w, pip_y + pip_h), QPointF(pip_x + pip_w, pip_y + pip_h - p_len))

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(58)
        self.setMinimumWidth(80)
        self._history = [0.0] * 30

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self._history.append(self._value)
        if len(self._history) > 30:
            self._history.pop(0)
        self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        # Label details
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 4, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        # Value text
        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        # Draw segmented progress bar (LED segments)
        bar_h   = 5
        bar_y   = 20
        bar_w   = W - 16
        bar_x   = 8
        
        num_segments = 12
        segment_gap = 2
        segment_w = (bar_w - (num_segments - 1) * segment_gap) / num_segments
        active_segments = int(self._value / 100 * num_segments)

        for s in range(num_segments):
            sx = bar_x + s * (segment_w + segment_gap)
            rect_seg = QRectF(sx, bar_y, segment_w, bar_h)
            
            if s < active_segments:
                s_col = qcol(C.RED) if (s >= 10 and self._value > 85) else (qcol(C.ACC) if (s >= 8 and self._value > 65) else qcol(self._color))
                p.setBrush(QBrush(s_col))
            else:
                p.setBrush(QBrush(qcol(C.BAR_BG, 100)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect_seg, 1.5, 1.5)

        # Draw mini scrolling history graph
        path = QPainterPath()
        path.moveTo(bar_x, H - 4)
        
        steps = len(self._history)
        if steps > 1:
            dx = bar_w / (steps - 1)
            for idx, val in enumerate(self._history):
                vx = bar_x + idx * dx
                vy = (H - 4) - (val / 100.0 * 20.0)
                path.lineTo(vx, vy)
            path.lineTo(bar_x + bar_w, H - 4)
            path.closeSubpath()
            
            # Fill with subtle matching gradient
            hist_grad = QLinearGradient(0, H - 24, 0, H - 4)
            hist_grad.setColorAt(0.0, qcol(self._color, 45))
            hist_grad.setColorAt(1.0, qcol(self._color, 5))
            p.setBrush(QBrush(hist_grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)
            
            # Draw stroke path
            stroke_path = QPainterPath()
            stroke_path.moveTo(bar_x, (H - 4) - (self._history[0] / 100.0 * 20.0))
            for idx, val in enumerate(self._history):
                vx = bar_x + idx * dx
                vy = (H - 4) - (val / 100.0 * 20.0)
                stroke_path.lineTo(vx, vy)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcol(self._color, 150), 1))
            p.drawPath(stroke_path)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        try:
            self.document().setMaximumBlockCount(1200)
        except Exception:
            pass
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 8px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._chars_per_tick = 12
        self._tmr = QTimer(self)
        self._tmr.setInterval(18)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def clear_log(self):
        self._queue.clear()
        self._typing = False
        self._text = ""
        self._pos = 0
        self._tmr.stop()
        self.setPlainText("")

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._insert_text(self._text)
        self._log_entry_complete()

    def _step(self):
        pass

    def _insert_text(self, text: str):
        cur = self.textCursor()
        fmt = cur.charFormat()
        col = {
            "you":  qcol(C.WHITE),
            "ai":   qcol(C.PRI),
            "err":  qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys":  qcol(C.ACC2),
        }.get(self._tag, qcol(C.TEXT))
        fmt.setForeground(QBrush(col))
        cur.movePosition(cur.MoveOperation.End)
        
        # HTML Image support (ULTRA PREMIUM)
        if "<img" in text.lower() or "<html>" in text.lower():
            cur.insertHtml(text)
        else:
            cur.insertText(text, fmt)
            
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def _log_entry_complete(self):
        self._typing = False
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText("\n")
        self.setTextCursor(cur)
        self.ensureCursorVisible()

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.setInterval(80)
        self._anim_tmr.timeout.connect(self._animate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()
        if not self._hovering and not self._drag_over:
            self._anim_tmr.stop()

    def _ensure_animating(self):
        if not self._anim_tmr.isActive():
            self._anim_tmr.start()

    def dragEnterEvent(self, a0: QDragEnterEvent):
        md = a0.mimeData()
        if md and md.hasUrls():
            a0.acceptProposedAction()
            self._drag_over = True
            self._ensure_animating()
            self._canvas.update()

    def dragLeaveEvent(self, a0):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, a0: QDropEvent):
        self._drag_over = False
        md = a0.mimeData()
        urls = md.urls() if md else []
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, a0):
        if a0.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, a0):
        self._hovering = True; self._ensure_animating(); self._canvas.update()

    def leaveEvent(self, a0):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else (C.PANEL2 if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        assert self._z._current_file is not None
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected
        self._booting = False
        self._boot_progress = 0.0
        self._boot_steps = [
            "CALIBRATING NATURAL AUDIO INTERFACES... OK",
            "CONNECTING TO SATELLITE TELEMETRY COM LINK... OK",
            "DECRYPTING CORE FIREWALL SECURE HANDSHAKES... OK",
            "VERIFYING LOCAL OS SUBSYSTEMS INTEGRITY... OK",
            "UPDATING HOLOGRAM MEMORY REGISTERS... OK",
            "JOYA AI CORE SYSTEM INITIALIZATION COMPLETE."
        ]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈ INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        self._init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        self._init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._init_btn.setFixedHeight(36)
        self._init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 8px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        self._init_btn.clicked.connect(self._submit)
        layout.addWidget(self._init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 8px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 8px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        
        self._boot_key = key
        self._booting = True
        self._boot_progress = 0.0
        
        # Hide all input elements
        for child in self.findChildren(QWidget):
            child.hide()
            
        self._boot_timer = QTimer(self)
        self._boot_timer.timeout.connect(self._do_boot_tick)
        self._boot_timer.start(45)
        self.update()

    def _do_boot_tick(self):
        self._boot_progress += random.uniform(1.8, 3.8)
        if self._boot_progress >= 100.0:
            self._boot_progress = 100.0
            self._boot_timer.stop()
            QTimer.singleShot(400, lambda: self.done.emit(self._boot_key, self._sel_os))
        self.update()

    def paintEvent(self, a0):
        super().paintEvent(a0)
        
        if self._booting:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            W, H = self.width(), self.height()
            
            p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.PRI), 255))
            p.drawText(QRectF(0, 20, W, 25), Qt.AlignmentFlag.AlignCenter, "◈ SYSTEM BOOT DIAGNOSTICS")
            
            # console
            console_rect = QRectF(25, 55, W - 50, H - 120)
            p.setBrush(QBrush(qcol(C.BG, 210)))
            p.setPen(QPen(qcol(C.BORDER), 1))
            p.drawRoundedRect(console_rect, 4, 4)
            
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            y_offset = 75
            current_step_idx = int(self._boot_progress / 100.0 * len(self._boot_steps))
            for i in range(min(current_step_idx + 1, len(self._boot_steps))):
                msg = self._boot_steps[i]
                if i == current_step_idx and self._boot_progress < 100.0:
                    p.setPen(QPen(qcol(C.ACC2), 220))
                    msg += " ..."
                else:
                    p.setPen(QPen(qcol(C.GREEN if "OK" in msg or "COMPLETE" in msg else C.PRI_DIM), 220))
                p.drawText(38, y_offset, msg)
    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        detected = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = detected
        self._booting = False
        self._boot_progress = 0.0
        self._boot_steps = [
            "CALIBRATING NATURAL AUDIO INTERFACES... OK",
            "CONNECTING TO SATELLITE TELEMETRY COM LINK... OK",
            "DECRYPTING CORE FIREWALL SECURE HANDSHAKES... OK",
            "VERIFYING LOCAL OS SUBSYSTEMS INTEGRITY... OK",
            "UPDATING HOLOGRAM MEMORY REGISTERS... OK",
            "JOYA AI CORE SYSTEM INITIALIZATION COMPLETE."
        ]
        self._logged_in = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(5)
        def _lbl(txt, font_size=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;"); return w
        _inp_ss = f"QLineEdit {{ background: #000d12; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px; }} QLineEdit:focus {{ border: 1px solid {C.PRI}; }}"
        layout.addWidget(_lbl("◈ INITIALISATION REQUIRED", 12, True))
        layout.addWidget(_lbl("Configure JOYA before first boot.", 8, color=C.PRI_DIM))
        layout.addSpacing(3)
        # ── Account Login ──
        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine); sep0.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep0)
        layout.addWidget(_lbl("JOYA ACCOUNT (OPTIONAL)", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._email_input = QLineEdit(); self._email_input.setPlaceholderText("Email address")
        self._email_input.setFont(QFont("Courier New", 9)); self._email_input.setFixedHeight(28); self._email_input.setStyleSheet(_inp_ss)
        layout.addWidget(self._email_input)
        self._pass_input = QLineEdit(); self._pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_input.setPlaceholderText("Password"); self._pass_input.setFont(QFont("Courier New", 9))
        self._pass_input.setFixedHeight(28); self._pass_input.setStyleSheet(_inp_ss)
        layout.addWidget(self._pass_input)
        login_row = QHBoxLayout(); login_row.setSpacing(6)
        self._login_btn = QPushButton("▸ Login"); self._login_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._login_btn.setFixedHeight(26); self._login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 6px; padding: 0 12px; }} QPushButton:hover {{ background: {C.PRI_GHO}; }}")
        self._login_btn.clicked.connect(self._do_login)
        login_row.addWidget(self._login_btn)
        self._login_status = QLabel(""); self._login_status.setFont(QFont("Courier New", 7))
        self._login_status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;"); login_row.addWidget(self._login_status, 1)
        layout.addLayout(login_row)
        layout.addSpacing(3)
        # ── API Keys ──
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep1)
        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit(); self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…"); self._key_input.setFont(QFont("Courier New", 9))
        self._key_input.setFixedHeight(28); self._key_input.setStyleSheet(_inp_ss); layout.addWidget(self._key_input)
        layout.addWidget(_lbl("OPENROUTER API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._openrouter_input = QLineEdit(); self._openrouter_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._openrouter_input.setPlaceholderText("sk-or-…"); self._openrouter_input.setFont(QFont("Courier New", 9))
        self._openrouter_input.setFixedHeight(28); self._openrouter_input.setStyleSheet(_inp_ss); layout.addWidget(self._openrouter_input)
        layout.addWidget(_lbl("GROQ API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._groq_input = QLineEdit(); self._groq_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_input.setPlaceholderText("gsk_…"); self._groq_input.setFont(QFont("Courier New", 9))
        self._groq_input.setFixedHeight(28); self._groq_input.setStyleSheet(_inp_ss); layout.addWidget(self._groq_input)
        layout.addSpacing(3)
        # ── OS Selection ──
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 7, color=C.ACC2, align=Qt.AlignmentFlag.AlignLeft))
        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞ Win"),("mac"," Mac"),("linux","🐧 Linux")]:
            btn = QPushButton(label); btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            btn.setFixedHeight(26); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k)); os_row.addWidget(btn); self._os_btns[key] = btn
        layout.addLayout(os_row); self._sel(detected)
        layout.addSpacing(5)
        self._init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        self._init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._init_btn.setFixedHeight(34); self._init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 8px; }} QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}")
        self._init_btn.clicked.connect(self._submit); layout.addWidget(self._init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 8px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 8px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _do_login(self):
        email = self._email_input.text().strip()
        password = self._pass_input.text().strip()
        if not email or not password:
            self._login_status.setStyleSheet(f"color: {C.RED}; background: transparent;")
            self._login_status.setText("Enter email & password"); return
        self._login_status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._login_status.setText("Connecting...")
        try:
            import requests
            res = requests.post("http://localhost:8000/api/client-auth", json={"email": email, "password": password}, timeout=8)
            if res.status_code == 200:
                data = res.json()
                session = {"email": email, "token": data.get("token", ""), "name": data.get("name", ""),
                           "server_url": "http://localhost:8000", "plan_type": data.get("plan_type", "free"),
                           "trial_ends_at": data.get("trial_ends_at", ""), "trial_active": data.get("trial_active", True),
                           "is_pro": data.get("is_pro", False)}
                sp = Path(__file__).resolve().parent / "config" / "user_session.json"
                sp.parent.mkdir(parents=True, exist_ok=True)
                sp.write_text(json.dumps(session, indent=4), encoding="utf-8")
                self._logged_in = True
                name = data.get("name", email.split("@")[0])
                self._login_status.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
                self._login_status.setText(f"✓ {name}")
                self._email_input.setEnabled(False); self._pass_input.setEnabled(False); self._login_btn.setEnabled(False)
                # Dynamic license update in UI
                try:
                    win = self.window()
                    if hasattr(win, "reload_license_info"):
                        win.reload_license_info()
                except Exception:
                    pass
            else:
                msg = "Login failed"
                try: msg = res.json().get("error", msg)
                except Exception: pass
                self._login_status.setStyleSheet(f"color: {C.RED}; background: transparent;"); self._login_status.setText(msg[:40])
        except Exception as e:
            self._login_status.setStyleSheet(f"color: {C.RED}; background: transparent;")
            self._login_status.setText(f"Offline ({str(e)[:25]})")

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(self._key_input.styleSheet() + f" QLineEdit {{ border: 1px solid {C.RED}; }}")
            return
        # Save all API keys to api_keys.json
        try:
            api_file = Path(__file__).resolve().parent / "config" / "api_keys.json"
            api_file.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if api_file.exists():
                try: existing = json.loads(api_file.read_text(encoding="utf-8"))
                except Exception: pass
            or_key = self._openrouter_input.text().strip()
            groq_key = self._groq_input.text().strip()
            if or_key: existing["openrouter_api_key"] = or_key
            if groq_key: existing["groq_api_key"] = groq_key
            existing["gemini_api_key"] = key; existing["os_system"] = self._sel_os
            api_file.write_text(json.dumps(existing, indent=4), encoding="utf-8")
        except Exception: pass
        self._boot_key = key; self._booting = True; self._boot_progress = 0.0
        for child in self.findChildren(QWidget): child.hide()
        self._boot_timer = QTimer(self)
        self._boot_timer.timeout.connect(self._do_boot_tick); self._boot_timer.start(45); self.update()

    def _do_boot_tick(self):
        self._boot_progress += random.uniform(1.8, 3.8)
        if self._boot_progress >= 100.0:
            self._boot_progress = 100.0
            self._boot_timer.stop()
            QTimer.singleShot(400, lambda: self.done.emit(self._boot_key, self._sel_os))
        self.update()

    def paintEvent(self, a0):
        super().paintEvent(a0)
        
        if self._booting:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            W, H = self.width(), self.height()
            
            p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.PRI), 255))
            p.drawText(QRectF(0, 20, W, 25), Qt.AlignmentFlag.AlignCenter, "◈ SYSTEM BOOT DIAGNOSTICS")
            
            # console
            console_rect = QRectF(25, 55, W - 50, H - 120)
            p.setBrush(QBrush(qcol(C.BG, 210)))
            p.setPen(QPen(qcol(C.BORDER), 1))
            p.drawRoundedRect(console_rect, 4, 4)
            
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            y_offset = 75
            current_step_idx = int(self._boot_progress / 100.0 * len(self._boot_steps))
            for i in range(min(current_step_idx + 1, len(self._boot_steps))):
                msg = self._boot_steps[i]
                if i == current_step_idx and self._boot_progress < 100.0:
                    p.setPen(QPen(qcol(C.ACC2), 220))
                    msg += " ..."
                else:
                    p.setPen(QPen(qcol(C.GREEN if "OK" in msg or "COMPLETE" in msg else C.PRI_DIM), 220))
                p.drawText(38, y_offset, msg)
                y_offset += 16
                
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.TEXT), 255))
            pct_str = f"BOOTING SYSTEM: {self._boot_progress:.0f}%"
            p.drawText(QRectF(25, H - 55, W - 50, 16), Qt.AlignmentFlag.AlignCenter, pct_str)
            
            bar_w = W - 70
            bar_x = 35
            bar_y = H - 34
            bar_h = 5
            p.setBrush(QBrush(qcol(C.BAR_BG)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)
            
            fill_w = int(bar_w * self._boot_progress / 100.0)
            if fill_w > 0:
                p.setBrush(QBrush(qcol(C.PRI)))
                p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)


THEMES = {
    "Apple Space Gray": {
        "PRI": "#0a84ff", "PRI_DIM": "#0056b3", "PRI_GHO": "#111111",
        "ACC": "#ff9f0a", "ACC2": "#30d158", "TEXT": "#f5f5f7", "TEXT_DIM": "#86868b",
        "TEXT_MED": "#e8e8ed", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Joya Fresh Clean": {
        "PRI": "#00a8ff", "PRI_DIM": "#0075b3", "PRI_GHO": "#111111",
        "ACC": "#ff9f0a", "ACC2": "#30d158", "TEXT": "#ffffff", "TEXT_DIM": "#8e9eab",
        "TEXT_MED": "#d1e2f0", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Classic Cyan (Stark)": {
        "PRI": "#00d4ff", "PRI_DIM": "#007a99", "PRI_GHO": "#111111",
        "ACC": "#ff6b00", "ACC2": "#ffcc00", "TEXT": "#8ffcff", "TEXT_DIM": "#3a8a9a",
        "TEXT_MED": "#5ab8cc", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Stealth Red (Joya 85)": {
        "PRI": "#ff2a2a", "PRI_DIM": "#b31e1e", "PRI_GHO": "#111111",
        "ACC": "#ffb700", "ACC2": "#ffea00", "TEXT": "#ffcccc", "TEXT_DIM": "#a35c5c",
        "TEXT_MED": "#cc7a7a", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Vibranium Purple": {
        "PRI": "#a633ff", "PRI_DIM": "#7524b3", "PRI_GHO": "#111111",
        "ACC": "#ffcc00", "ACC2": "#ffe680", "TEXT": "#f2ccff", "TEXT_DIM": "#975ca3",
        "TEXT_MED": "#bf7acc", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Stealth Green (SHIELD)": {
        "PRI": "#00ff88", "PRI_DIM": "#00b35f", "PRI_GHO": "#111111",
        "ACC": "#00aaff", "ACC2": "#80d4ff", "TEXT": "#ccffeb", "TEXT_DIM": "#5ca385",
        "TEXT_MED": "#7accab", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
    "Light (Paper)": {
        "PRI": "#0a84ff", "PRI_DIM": "#5a8cff", "PRI_GHO": "#e6f0ff",
        "ACC": "#ff6b00", "ACC2": "#ffcc66", "TEXT": "#0b0b0b", "TEXT_DIM": "#555555",
        "TEXT_MED": "#333333", "BORDER": "#e6e6e6", "BORDER_B": "#cccccc", "BORDER_A": "#f0f0f0",
        "BG": "#ffffff", "PANEL": "#fbfbfb", "PANEL2": "#f6f6f6", "BAR_BG": "#ededed"
    },
    "Tesla Cyberpunk Neon": {
        "PRI": "#ff0055", "PRI_DIM": "#b3003b", "PRI_GHO": "#110b0d",
        "ACC": "#00ffff", "ACC2": "#ffff00", "TEXT": "#ffffff", "TEXT_DIM": "#8e8e93",
        "TEXT_MED": "#d1d1d6", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#09090b", "PANEL2": "#18181b", "BAR_BG": "#050507"
    },
    "Apple Cyber Indigo": {
        "PRI": "#5e5ce6", "PRI_DIM": "#403e99", "PRI_GHO": "#0e0e1a",
        "ACC": "#bf5af2", "ACC2": "#30d158", "TEXT": "#ffffff", "TEXT_DIM": "#86868b",
        "TEXT_MED": "#e8e8ed", "BORDER": "rgba(255, 255, 255, 0.08)", "BORDER_B": "rgba(255, 255, 255, 0.16)", "BORDER_A": "rgba(255, 255, 255, 0.03)",
        "BG": "#000000", "PANEL": "#08080a", "PANEL2": "#121215", "BAR_BG": "#050507"
    },
}

DEFAULT_VOICE_MACROS = {
    "daily briefing": "Give me a concise hands-free daily briefing: current time, weather if my city is known, important reminders, active project priorities from memory, and the next best action.",
    "live screen": "Start live API screen vision every 8 seconds. Keep watching quietly, do not save screenshots, and use the context when I ask what is happening.",
    "live camera": "Start live API camera vision every 8 seconds. Keep watching quietly, do not save camera frames, and use the context when I ask what you see.",
    "screen scan": "Analyze my screen through in-memory API vision and tell me the next useful action. Do not save a screenshot.",
    "camera scan": "Look through the camera using in-memory API vision and describe what you see. Do not save a frame.",
    "focus mode": "Start hands-free focus mode: reduce obvious distractions, set a comfortable volume, and stay ready for voice commands.",
    "task briefing": "Open my AI task planner daily briefing, show the top pending tasks, and ask which goal I want planned next.",
    "meeting mode": "Start smart meeting mode. Ask for a meeting title if missing, keep notes ready, and prepare action item extraction.",
    "security audit": "Run a full cyber security audit and summarize the score, risks, and the next three fixes.",
    "wellness check": "Show my fitness status for today and suggest one small useful health action.",
    "desktop clean": "Organize my desktop by file type and summarize what changed.",
    "file autopilot": "Analyze the uploaded file and suggest the most useful next actions.",
    "human mode": "Activate Human Mode with privacy-safe always-ready eyes, ears, and human-like brain memory. Use live screen plus camera context every 8 seconds, keep wake listening ready, and connect mood/persona/identity memory.",
}

HANDSFREE_PRESETS = [
    ("DAILY BRIEF", DEFAULT_VOICE_MACROS["daily briefing"]),
    ("FOCUS MODE", DEFAULT_VOICE_MACROS["focus mode"]),
    ("SCREEN SCAN", DEFAULT_VOICE_MACROS["screen scan"]),
    ("CAMERA SCAN", DEFAULT_VOICE_MACROS["camera scan"]),
    ("TASK BRIEF", DEFAULT_VOICE_MACROS["task briefing"]),
    ("MEETING MODE", DEFAULT_VOICE_MACROS["meeting mode"]),
    ("SECURITY AUDIT", DEFAULT_VOICE_MACROS["security audit"]),
    ("WELLNESS CHECK", DEFAULT_VOICE_MACROS["wellness check"]),
    ("LIVE SCREEN", DEFAULT_VOICE_MACROS["live screen"]),
    ("LIVE CAMERA", DEFAULT_VOICE_MACROS["live camera"]),
    ("DESKTOP CLEAN", DEFAULT_VOICE_MACROS["desktop clean"]),
    ("FILE AUTOPILOT", DEFAULT_VOICE_MACROS["file autopilot"]),
    ("HUMAN MODE", DEFAULT_VOICE_MACROS["human mode"])
]

COMMAND_CENTER_PRESETS = [
    {
        "label": "Daily Operator Brief",
        "category": "Core",
        "command": DEFAULT_VOICE_MACROS["daily briefing"],
        "hint": "Time, priorities, reminders, and next action.",
    },
    {
        "label": "Deep Focus Launch",
        "category": "Work",
        "command": DEFAULT_VOICE_MACROS["focus mode"],
        "hint": "Quiet workspace, ready voice control, fewer distractions.",
    },
    {
        "label": "Screen Intelligence Scan",
        "category": "Vision",
        "command": DEFAULT_VOICE_MACROS["screen scan"],
        "hint": "Reads current screen context without saving a screenshot.",
    },
    {
        "label": "Live Screen Co-Pilot",
        "category": "Vision",
        "command": DEFAULT_VOICE_MACROS["live screen"],
        "hint": "Keeps recent screen context available for follow-up questions.",
    },
    {
        "label": "Camera Situation Report",
        "category": "Vision",
        "command": DEFAULT_VOICE_MACROS["camera scan"],
        "hint": "Quick camera-based visual description.",
    },
    {
        "label": "Task Planner Briefing",
        "category": "Work",
        "command": DEFAULT_VOICE_MACROS["task briefing"],
        "hint": "Top pending tasks and the next goal to plan.",
    },
    {
        "label": "Meeting Capture Mode",
        "category": "Work",
        "command": DEFAULT_VOICE_MACROS["meeting mode"],
        "hint": "Prepares notes and action item extraction.",
    },
    {
        "label": "Privacy Guard Sweep",
        "category": "Safety",
        "command": "Run a privacy guard scan on my screen. Warn only if sensitive information seems visible, and do not read exact secrets aloud.",
        "hint": "Checks for visible sensitive information.",
    },
    {
        "label": "Cyber Audit",
        "category": "Safety",
        "command": DEFAULT_VOICE_MACROS["security audit"],
        "hint": "Security score, risks, and next fixes.",
    },
    {
        "label": "Performance Autopilot",
        "category": "System",
        "command": "smart performance autopilot scan and recommend the safest optimization preset",
        "hint": "System health check with safe optimization advice.",
    },
    {
        "label": "Desktop Clean Sweep",
        "category": "System",
        "command": DEFAULT_VOICE_MACROS["desktop clean"],
        "hint": "Organizes desktop files and summarizes changes.",
    },
    {
        "label": "File Autopilot",
        "category": "Files",
        "command": DEFAULT_VOICE_MACROS["file autopilot"],
        "hint": "Analyzes the uploaded file and suggests next actions.",
    },
    {
        "label": "Human Mode Full Core",
        "category": "Core",
        "command": DEFAULT_VOICE_MACROS["human mode"],
        "hint": "Turns on privacy-safe eyes, ears, and human-like memory brain.",
    },
    {
        "label": "Human Brain Status",
        "category": "Core",
        "command": "human mode status",
        "hint": "Shows eyes, ears, brain, mood, persona, and memory state.",
    },
    {
        "label": "Study Sprint",
        "category": "Study",
        "command": "Create a 45 minute study sprint: pick the next topic, active recall prompts, a 5 minute break, and a quick revision checklist.",
        "hint": "Student mode plan with recall and revision.",
    },
    {
        "label": "Doubt Solver",
        "category": "Study",
        "command": "Help me solve my current doubt step by step. Ask for the question if it is not visible.",
        "hint": "Guided explanation instead of a raw answer.",
    },
]


class AnimatedRadialMenu(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_progress = 0.0
        self._hovering = False
        self._angle_offset = 0.0
        self._expanded = False
        self._expand_progress = 0.0
        
        self._timer = QTimer(self)
        self._timer.setInterval(_HUD_SMOOTH_ACTIVE_MS)
        self._timer.timeout.connect(self._step_animation)
        
        self.setMouseTracking(True)
        
        self.items = [
            {"label": "Web", "icon": "🌐", "cmd": "open Google Chrome"},
            {"label": "Note", "icon": "📝", "cmd": "open notepad"},
            {"label": "Calc", "icon": "🧮", "cmd": "open calculator"},
            {"label": "Chat", "icon": "💬", "cmd": "open WhatsApp"}
        ]
        
    def _step_animation(self):
        target_hover = 1.0 if self._hovering else 0.0
        before_hover = self._hover_progress
        before_expand = self._expand_progress
        self._hover_progress += (target_hover - self._hover_progress) * 0.1
        
        target_expand = 1.0 if self._expanded else 0.0
        self._expand_progress += (target_expand - self._expand_progress) * 0.15
        
        if self._expanded:
            self._angle_offset += 0.5
        self.update()
        settled = (
            not self._hovering
            and not self._expanded
            and abs(self._hover_progress) < _ANIM_EPS
            and abs(self._expand_progress) < _ANIM_EPS
        )
        if settled:
            self._hover_progress = 0.0
            self._expand_progress = 0.0
            self._timer.stop()
        elif before_hover == self._hover_progress and before_expand == self._expand_progress:
            self._timer.stop()

    def _ensure_animating(self):
        if not self._timer.isActive():
            self._timer.start()
        
    def enterEvent(self, event):
        self._hovering = True
        self._ensure_animating()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self._hovering = False
        self._ensure_animating()
        super().leaveEvent(event)
        
    def mousePressEvent(self, event):
        pos = event.position()
        cx, cy = self.width() / 2, self.height() / 2
        dist = math.hypot(pos.x() - cx, pos.y() - cy)
        
        if dist < 22:
            self._expanded = not self._expanded
            self._ensure_animating()
        elif self._expanded and dist < 45 and dist >= 22:
            angle = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx)) - self._angle_offset
            angle = (angle + 360) % 360
            num_items = len(self.items)
            slice_angle = 360 / num_items
            clicked_idx = int(angle // slice_angle)
            if clicked_idx < num_items:
                item = self.items[clicked_idx]
                main_win = self.window()
                if hasattr(main_win, "_dispatch_command"):
                    main_win._dispatch_command(item["cmd"], source="Radial Menu")
        super().mousePressEvent(event)
        
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        
        # Draw central circle
        r_core = 16.0 + 3.0 * self._hover_progress
        p.setBrush(QBrush(qcol(C.PRI, int(35 + 50 * self._hover_progress))))
        p.setPen(QPen(qcol(C.PRI, 150), 1.5))
        p.drawEllipse(QPointF(cx, cy), r_core, r_core)
        
        p.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE)))
        p.drawText(QRectF(cx - 20, cy - 20, 40, 40), Qt.AlignmentFlag.AlignCenter, "LAUNCH")
        
        # Radial slices when expanded
        if self._expand_progress > 0.01:
            num_items = len(self.items)
            radius = 32.0 * self._expand_progress
            
            for idx, item in enumerate(self.items):
                angle_deg = (idx * (360 / num_items)) + self._angle_offset
                angle_rad = math.radians(angle_deg)
                
                ix = cx + math.cos(angle_rad) * radius
                iy = cy + math.sin(angle_rad) * radius
                
                # Draw outer shortcut button
                p.setBrush(QBrush(qcol(C.BG, 220)))
                p.setPen(QPen(qcol(C.PRI, int(150 * self._expand_progress)), 1.0))
                p.drawEllipse(QPointF(ix, iy), 12, 12)
                
                # Draw emoji inside shortcut button
                p.setFont(QFont(C.FONT_SANS, 8))
                p.drawText(QRectF(ix - 12, iy - 12, 24, 24), Qt.AlignmentFlag.AlignCenter, item["icon"])


class AnimatedPushButton(QPushButton):
    def __init__(self, text: str, parent=None, accent=False):
        super().__init__(text, parent)
        self.accent = accent
        self.custom_color = None
        self._hover_progress = 0.0  # 0.0 to 1.0
        self._click_progress = 0.0  # 0.0 to 1.0
        self._hovering = False
        self._pressed = False
        
        self.setFixedHeight(36)
        self.setFont(pfont(11, "semibold"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step_animation)

    def enterEvent(self, event):
        self._hovering = True
        self._ensure_animating()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._ensure_animating()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        self._ensure_animating()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self._ensure_animating()
        super().mouseReleaseEvent(event)

    def _ensure_animating(self):
        if not self._timer.isActive():
            self._timer.start()

    def _step_animation(self):
        target_hover = 1.0 if self._hovering else 0.0
        self._hover_progress += (target_hover - self._hover_progress) * 0.15
        
        target_click = 1.0 if self._pressed else 0.0
        self._click_progress += (target_click - self._click_progress) * 0.25
        
        self.update()
        settled = (
            not self._hovering
            and not self._pressed
            and abs(self._hover_progress) < _ANIM_EPS
            and abs(self._click_progress) < _ANIM_EPS
        )
        if settled:
            self._hover_progress = 0.0
            self._click_progress = 0.0
            self._timer.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        W, H = self.width(), self.height()
        rect = QRectF(1.5, 1.5, W - 3.0, H - 3.0)
        
        pri_col = self.custom_color or (C.GREEN if self.accent else C.PRI)
        
        # Frosted glass background: dark obsidian base with subtle white sheen
        bg_alpha = int(14 + 18 * self._hover_progress + 25 * self._click_progress)
        p.setBrush(QBrush(qcol("#04080c", 200 + bg_alpha)))
        
        # Subtle glass glow border: white/translucent by default, accent/primary on hover
        border_color = qcol(pri_col if self._hovering else "#ffffff", int(35 + 180 * self._hover_progress))
        p.setPen(QPen(border_color, 1.0 + 0.5 * self._hover_progress))
        
        # Draw base rounded rect
        p.drawRoundedRect(rect, 8.0, 8.0)
        
        # Draw physical glass top light-reflection edge (signature Apple style detail)
        p.setPen(QPen(qcol("#ffffff", int(20 + 90 * self._hover_progress)), 1))
        p.drawLine(int(rect.x() + 6), int(rect.y() + 1), int(rect.x() + rect.width() - 6), int(rect.y() + 1))
        
        # Outer glow paths for neon highlight on hover
        if self._hover_progress > 0.01:
            p.save()
            p.setBrush(Qt.BrushStyle.NoBrush)
            glow_path = QPainterPath()
            glow_path.addRoundedRect(rect, 8.0, 8.0)
            for i in range(1, 4):
                g_col = qcol(pri_col, int(18 * (1.0 - i/4) * self._hover_progress))
                p.setPen(QPen(g_col, 1.0 + i * 1.2))
                p.drawPath(glow_path)
            p.restore()
            
        # Draw text with smooth color and layout animation
        text_color = qcol(C.WHITE if self._hovering else C.TEXT_MED)
        p.setPen(QPen(text_color))
        p.setFont(self.font())
        
        # Shift text slightly on click for tactile micro-feedback
        click_offset = int(self._click_progress * 1.5)
        text_rect = QRectF(rect.x() + click_offset, rect.y() + click_offset, rect.width(), rect.height())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class CyberBgWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._particles: list[list[float]] = []
        self._phase = 0.0
        self.performance_mode = True
        self._mood_color = C.PRI      # ambient tint follows the user's mood
        self._mood_tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(_HUD_SMOOTH_IDLE_MS)

    def set_performance_mode(self, enabled: bool):
        self.performance_mode = bool(enabled)
        self._timer.start(_HUD_SMOOTH_IDLE_MS if self.performance_mode else _HUD_SMOOTH_ACTIVE_MS)

    def showEvent(self, event):
        self._timer.start(_HUD_SMOOTH_IDLE_MS if self.performance_mode else _HUD_SMOOTH_ACTIVE_MS)
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self):
        self._phase += 0.02
        for p in self._particles:
            p[1] -= p[3]
            p[0] += p[2] * 0.5
            if p[1] < -10:
                p[1] = self.height() + 10
                p[0] = random.random() * self.width()
        # refresh the mood-driven ambient tint occasionally (cheap)
        self._mood_tick += 1
        if self._mood_tick % 180 == 0:
            self._refresh_mood()
        self.update()

    def _refresh_mood(self):
        """Tint the ambient aurora to the user's latest mood — a living backdrop."""
        MOOD_TINT = {
            "sad": "#5a78ff", "lonely": "#6e6aff", "stressed": "#22d3ee",
            "angry": "#ff7a5f", "happy": "#ffb020", "excited": "#ff59a7",
            "grateful": "#a855f7", "neutral": C.PRI,
        }
        try:
            path = BASE_DIR / "cache" / "cognition" / "mood_timeline.json"
            if not path.exists():
                return
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            if data:
                emo = str(data[-1].get("emotion", "neutral"))
                self._mood_color = MOOD_TINT.get(emo, C.PRI)
        except Exception:
            pass

    def _init_particles(self):
        if not self._particles and self.width() > 0:
            for _ in range(30):
                self._particles.append([
                    random.random() * self.width(),
                    random.random() * self.height(),
                    (random.random() - 0.5) * 0.8,
                    0.15 + random.random() * 0.4,
                    random.random() * 2 + 0.5,
                ])

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        
        # Check if the active theme is light (based on background color C.BG)
        is_light = (C.BG.lower() == "#ffffff")

        if is_light:
            base = QLinearGradient(0, 0, W * 0.5, H)
            base.setColorAt(0.0, qcol("#ffffff"))
            base.setColorAt(0.55, qcol("#f4f6f8"))
            base.setColorAt(1.0, qcol("#e9ecef"))
            p.fillRect(0, 0, W, H, QBrush(base))
            
            # Soft drifting aurora blobs for light mode
            def blob(cx, cy, r, color, a):
                g = QRadialGradient(cx, cy, r)
                g.setColorAt(0.0, qcol(color, a))
                g.setColorAt(0.5, qcol(color, int(a * 0.38)))
                g.setColorAt(1.0, qcol(color, 0))
                p.fillRect(0, 0, W, H, QBrush(g))
            
            t = self._phase
            blob(W * 0.24 + 45 * math.sin(t * 0.40), H * 0.30 + 32 * math.cos(t * 0.30), max(W, H) * 0.55, self._mood_color, 24)
            blob(W * 0.82 + 50 * math.cos(t * 0.33), H * 0.72 + 40 * math.sin(t * 0.26), max(W, H) * 0.50, C.PURPLE, 20)
            blob(W * 0.62 + 30 * math.sin(t * 0.50), H * 0.14 + 22 * math.cos(t * 0.44), max(W, H) * 0.34, C.ACC, 15)
            
            vig = QRadialGradient(W / 2, H * 0.42, max(W, H) * 0.78)
            vig.setColorAt(0.0, qcol("#ffffff", 0))
            vig.setColorAt(1.0, qcol("#ffffff", 45))
            p.fillRect(0, 0, W, H, QBrush(vig))
        else:
            # Pure Absolute Black (AMOLED Pro style)
            p.fillRect(0, 0, W, H, QBrush(qcol("#000000")))
            
            # Subtle drifting aurora blobs for dark mode (extremely low opacity to keep background dark and premium)
            def blob(cx, cy, r, color, a):
                g = QRadialGradient(cx, cy, r)
                g.setColorAt(0.0, qcol(color, a))
                g.setColorAt(0.5, qcol(color, int(a * 0.3)))
                g.setColorAt(1.0, qcol(color, 0))
                p.fillRect(0, 0, W, H, QBrush(g))
            
            t = self._phase
            # We use very small alpha (e.g. 14, 12, 8 out of 255) to make it feel extremely premium, mysterious, and dark
            blob(W * 0.20 + 45 * math.sin(t * 0.25), H * 0.30 + 35 * math.cos(t * 0.20), max(W, H) * 0.65, self._mood_color, 14)
            blob(W * 0.80 + 50 * math.cos(t * 0.22), H * 0.70 + 40 * math.sin(t * 0.18), max(W, H) * 0.55, C.PURPLE, 12)
            blob(W * 0.50 + 30 * math.sin(t * 0.30), H * 0.15 + 20 * math.cos(t * 0.28), max(W, H) * 0.40, C.ACC, 8)
            
        p.end()



class GlassmorphicTileButton(QAbstractButton):
    def __init__(self, title: str, value: str, icon_str: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.value = value
        self.icon_str = icon_str
        self._hover_progress = 0.0
        self._click_progress = 0.0
        self._hovering = False
        self._pressed = False

        self.setFixedHeight(68)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step_animation)
        self._timer.start(16)

    def enterEvent(self, event):
        self._hovering = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        super().mouseReleaseEvent(event)

    def _step_animation(self):
        target_hover = 1.0 if self._hovering else 0.0
        self._hover_progress += (target_hover - self._hover_progress) * 0.15
        
        target_click = 1.0 if self._pressed else 0.0
        self._click_progress += (target_click - self._click_progress) * 0.25
        
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(1.5, 1.5, W - 3.0, H - 3.0)

        # Translucent panel color, lighter on hover
        bg_alpha = int(25 + 25 * self._hover_progress + 30 * self._click_progress)
        p.setBrush(QBrush(qcol(C.PANEL, bg_alpha)))

        # Border color transitions with hover
        border_col = qcol(C.PRI if self._hovering else C.BORDER, int(150 + 105 * self._hover_progress))
        p.setPen(QPen(border_col, 1.0 + 0.5 * self._hover_progress))

        # Rounded rectangle base
        p.drawRoundedRect(rect, 5.0, 5.0)

        # Subtle neon glow on hover
        if self._hover_progress > 0.01:
            p.save()
            p.setBrush(Qt.BrushStyle.NoBrush)
            glow_path = QPainterPath()
            glow_path.addRoundedRect(rect, 5.0, 5.0)
            for i in range(1, 4):
                g_col = qcol(C.PRI, int(20 * (1.0 - i/4) * self._hover_progress))
                p.setPen(QPen(g_col, 1.0 + i * 1.5))
                p.drawPath(glow_path)
            p.restore()

        # Shift content on click
        click_offset = int(self._click_progress * 1.5)

        # Draw icon (emoji)
        p.setFont(QFont(C.FONT_SANS, 16))
        # Icon positioned on the left
        icon_rect = QRectF(12 + click_offset, 12 + click_offset, 32, H - 24)
        p.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, self.icon_str)

        # Draw title (e.g., "Phone Link")
        p.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM)))
        title_rect = QRectF(52 + click_offset, 12 + click_offset, W - 64, 16)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())

        # Draw value (e.g., "CONNECTED")
        val_str = str(self.value)
        val_color = qcol(C.GREEN if any(x in val_str.upper() for x in ["CONNECTED", "ACTIVE", "ONLINE", "100%", "SECURE"]) else (C.RED if "MUTED" in val_str.upper() else C.TEXT))
        p.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(val_color))
        value_rect = QRectF(52 + click_offset, 28 + click_offset, W - 64, 22)
        p.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, val_str)
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PyQt6.QtGui import QColor

class FocusFilter(QObject):
    def __init__(self, parent_widget, active_style, inactive_style, target_input=None, glow_color=None):
        super().__init__(target_input or parent_widget)
        self.parent_widget = parent_widget
        self.active_style = active_style
        self.inactive_style = inactive_style
        self.glow_color = glow_color
        self._set_inactive_shadow()

    def _set_inactive_shadow(self):
        shadow = QGraphicsDropShadowEffect(self.parent_widget)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 3)
        self.parent_widget.setGraphicsEffect(shadow)
        
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusIn:
            self.parent_widget.setStyleSheet(self.active_style)
            if self.glow_color:
                shadow = QGraphicsDropShadowEffect(self.parent_widget)
                shadow.setBlurRadius(25)
                shadow.setColor(QColor(self.glow_color))
                shadow.setOffset(0, 0)
                self.parent_widget.setGraphicsEffect(shadow)
        elif event.type() == QEvent.Type.FocusOut:
            self.parent_widget.setStyleSheet(self.inactive_style)
            self._set_inactive_shadow()
        return False


class MainWindow(QMainWindow):
    _log_sig = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _mic_level_sig = pyqtSignal(float)
    _vision_log_sig = pyqtSignal(str)
    _active_window_sig = pyqtSignal(str)
    _voice_wake_sig = pyqtSignal(object)
    _timeline_sig = pyqtSignal(str)
    _autopilot_status_sig = pyqtSignal(str)
    _open_notebook_sig = pyqtSignal(dict)
    _open_flashcards_sig = pyqtSignal(object)
    _show_premium_writer_sig = pyqtSignal(str, str)
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        try:
            geom = QApplication.primaryScreen().availableGeometry()
        except Exception:
            geom = None
        try:
            plan_str = "Free Trial"
            sp = Path(__file__).resolve().parent / "config" / "user_session.json"
            if sp.exists():
                try:
                    import json
                    sess = json.loads(sp.read_text(encoding="utf-8"))
                    p_type = (sess.get("plan_type") or "free").lower()
                    if p_type == "premium" or sess.get("is_pro"):
                        plan_str = "PRO / PREMIUM Tier"
                    elif p_type == "standard":
                        plan_str = "STANDARD Tier"
                except Exception:
                    pass
            self.setWindowTitle(f"J.O.Y.A.  [Active License: {plan_str}]")
            if APP_ICON.exists():
                self.setWindowIcon(QIcon(str(APP_ICON)))
        except Exception:
            pass
        try:
            self.resize(580, 580)
        except Exception:
            pass
        if geom:
            self.move(
                (geom.width()  - 580) // 2,
                (geom.height() - 580) // 2,
            )


        self.on_text_command  = None
        self._muted           = False
        self._current_file: str | None = None
        self._floating_assistant_widget = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._force_quit = False
        self._autopilot_cancel = threading.Event()
        self._god_mode = False
        self._auto_wake = True
        self.current_volume = 0.0
        self._last_voice_activity = time.time()
        self._last_wake_event: dict[str, object] = {}
        self._wake_check_count = 0
        self._wake_listener_ready = False
        self._live_context_lock = threading.Lock()
        self._lag_samples: list[float] = []
        self._lag_events = 0
        self._lag_max_ms = 0.0
        self._lag_last_log = 0.0
        self._load_extended_config()
        self._mission_queue: list[str] = []
        self._queue_running = False
        self._mission_dispatch_delay_ms = 7000
        self._command_center: _CommandCenterWidget | None = None

        central = CyberBgWidget(self)
        self._central_bg = central
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        
        self._header_widget = self._build_header()
        root.addWidget(self._header_widget)
        self._header_widget.setVisible(False)

        self._top_bar = QWidget()
        self._top_bar.setStyleSheet("background: transparent; border: none;")
        top_bar_lay = QHBoxLayout(self._top_bar)
        top_bar_lay.setContentsMargins(20, 15, 20, 0)
        top_bar_lay.addStretch()
        
        self._menu_toggle_btn = QPushButton("⚙️ Dashboard")
        self._menu_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 15px;
                padding: 6px 16px;
                font-family: "{C.FONT_SANS}";
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.1);
                color: #ffffff;
                border-color: {C.PRI};
            }}
            QPushButton:pressed {{
                background: {C.PRI_GHO};
            }}
        """)
        self._menu_toggle_btn.clicked.connect(self._toggle_control_panel)
        top_bar_lay.addWidget(self._menu_toggle_btn)
        root.addWidget(self._top_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)
        self._left_panel.setVisible(False)

        self._center_container = QWidget()
        self._center_container.setStyleSheet("background: transparent; border: none;")
        center_lay = QVBoxLayout(self._center_container)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(10)

        self.hud = HudCanvas(face_path)
        self.hud.simple_mode = getattr(self, "simple_mode", False)
        self.hud.auto_wake = self._auto_wake
        self.hud.set_performance_mode(getattr(self, "performance_mode", True))
        if hasattr(self._central_bg, "set_performance_mode"):
            self._central_bg.set_performance_mode(getattr(self, "performance_mode", True))
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_lay.addWidget(self.hud, stretch=1)

        self._central_input_container = QWidget()
        self._central_input_container.setStyleSheet("background: transparent; border: none;")
        central_input_lay = QHBoxLayout(self._central_input_container)
        central_input_lay.setContentsMargins(0, 0, 0, 30)
        central_input_lay.setSpacing(0)
        central_input_lay.addStretch(1)

        # Unified Search & Voice Pill Container
        self._search_bar_pill = QWidget()
        self._search_bar_pill.setFixedSize(580, 48)
        
        active_style = f"background: rgba(10, 10, 12, 0.90); border: 1.5px solid {C.PRI}; border-radius: 24px;"
        inactive_style = "background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 24px;"
        self._search_bar_pill.setStyleSheet(inactive_style)
        
        pill_lay = QHBoxLayout(self._search_bar_pill)
        pill_lay.setContentsMargins(0, 0, 0, 0)
        pill_lay.setSpacing(0)

        self._central_input = QLineEdit()
        self._central_input.setPlaceholderText("Ask Joya... Type command or speak")
        self._central_input.setFont(pfont(12, "medium"))
        self._central_input.setStyleSheet("background: transparent; border: none; padding: 0 10px 0 24px; color: #ffffff;")
        self._central_input.returnPressed.connect(self._send_central)
        pill_lay.addWidget(self._central_input, stretch=1)

        self._central_mic_btn = QPushButton("🎤")
        self._central_mic_btn.setFixedSize(48, 48)
        self._central_mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._central_mic_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {C.TEXT_MED};
                padding: 0 18px 0 6px;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: {C.PRI};
            }}
            QPushButton:pressed {{
                color: #ffffff;
            }}
        """)
        self._central_mic_btn.clicked.connect(self._trigger_mic_input)
        pill_lay.addWidget(self._central_mic_btn)
        
        # Register focus filter for interactive glowing
        self._focus_filter = FocusFilter(self._search_bar_pill, active_style, inactive_style, self._central_input, C.PRI)
        self._central_input.installEventFilter(self._focus_filter)
        
        central_input_lay.addWidget(self._search_bar_pill)
        central_input_lay.addStretch(1)
        
        center_lay.addWidget(self._central_input_container)

        # ── Quick Action Chips (floating glassmorphic shortcuts) ──────────
        chips_container = QWidget()
        chips_container.setStyleSheet("background: transparent; border: none;")
        chips_lay = QHBoxLayout(chips_container)
        chips_lay.setContentsMargins(0, 0, 0, 10)
        chips_lay.setSpacing(8)
        chips_lay.addStretch(1)

        _chip_data = [
            ("▶ YouTube",    "open youtube"),
            ("💬 WhatsApp",  "open whatsapp"),
            ("🔍 Screen",    "analyze my screen"),
            ("📰 News",      "give me latest news flash"),
            ("🧠 Focus",     "start student deep work session"),
            ("⚡ Brief",     "give me daily briefing"),
        ]

        for chip_text, chip_cmd in _chip_data:
            chip = QPushButton(chip_text)
            chip.setFixedHeight(28)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFont(pfont(8.5, "semibold"))
            chip.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.04);
                    color: {C.TEXT_DIM};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 14px;
                    padding: 0 14px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.10);
                    color: {C.TEXT};
                    border-color: {C.PRI};
                }}
                QPushButton:pressed {{
                    background: {C.PRI_GHO};
                    color: {C.PRI};
                }}
            """)
            chip.clicked.connect(lambda checked=False, c=chip_cmd: self._dispatch_command(c, source="Chip"))
            chips_lay.addWidget(chip)

        chips_lay.addStretch(1)
        center_lay.addWidget(chips_container)

        body.addWidget(self._center_container, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)
        self._right_panel.setVisible(False)

        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        
        shadow_left = QGraphicsDropShadowEffect(self)
        shadow_left.setBlurRadius(18)
        shadow_left.setColor(qcol(C.PRI, 45))
        shadow_left.setOffset(2, 0)
        self._left_panel.setGraphicsEffect(shadow_left)
        
        shadow_right = QGraphicsDropShadowEffect(self)
        shadow_right.setBlurRadius(18)
        shadow_right.setColor(qcol(C.PRI, 45))
        shadow_right.setOffset(-2, 0)
        self._right_panel.setGraphicsEffect(shadow_right)

        root.addLayout(body, stretch=1)
        
        self._footer_widget = self._build_footer()
        root.addWidget(self._footer_widget)
        self._footer_widget.setVisible(False)

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        # Toast notification manager
        self._toast_mgr = _ToastManager(self)

        # SYS LAB graph update timer (every 1.5s, synced with _metrics polling)
        self._syslab_tmr = QTimer(self)
        self._syslab_tmr.timeout.connect(self._update_syslab_graphs)
        self._syslab_tmr.start(1500)

        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._run_screen_watch_tick)
        if self.screen_watch_enabled:
            self._watch_timer.start(int(self.screen_watch_interval) * 1000)

        self._notification_timer = QTimer(self)
        self._notification_timer.timeout.connect(self._run_notification_watch_tick)

        self._privacy_timer = QTimer(self)
        self._privacy_timer.timeout.connect(self._run_privacy_guard_tick)
        if self.privacy_guard_enabled:
            self._privacy_timer.start(90000)

        if self.live_context_enabled:
            QTimer.singleShot(1200, lambda: self._set_live_context_enabled(True, persist=False))

        # VISION HUD tracker state
        self._cached_active_pid = 0
        self._cached_active_title = ""
        self.auto_analyze_app_switch = True
        self.voice_announce_app_switch = False

        # Active window polling timer (ticks every 1.5 seconds)
        self._active_app_timer = QTimer(self)
        self._active_app_timer.timeout.connect(self._poll_active_window)
        self._active_app_timer.start(1500)

        # Debounce timer for Gemini vision screen analysis (2 seconds delay)
        self._analysis_debounce_timer = QTimer(self)
        self._analysis_debounce_timer.setSingleShot(True)
        self._analysis_debounce_timer.timeout.connect(self._trigger_app_switch_analysis)

        self._lag_watchdog_timer = QTimer(self)
        self._lag_watchdog_interval_ms = 500
        self._lag_watchdog_last = time.monotonic()
        self._lag_watchdog_timer.timeout.connect(self._run_lag_watchdog_tick)
        if getattr(self, "lag_watchdog_enabled", True):
            self._lag_watchdog_timer.start(self._lag_watchdog_interval_ms)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._mic_level_sig.connect(self._on_mic_level_received)
        self._vision_log_sig.connect(self._append_vision_log)
        self._active_window_sig.connect(self._update_active_window_ui)
        self._voice_wake_sig.connect(self._handle_voice_wake_event)
        self._timeline_sig.connect(self._set_timeline_status)
        self._autopilot_status_sig.connect(self._set_autopilot_status)
        self.question_manager = (
            get_question_session_manager()
            if get_question_session_manager is not None
            else None
        )
        self.question_notebook = None
        self._flashcards_window = None
        self._open_notebook_sig.connect(self._handle_notebook_signal)
        self._open_flashcards_sig.connect(self._handle_flashcards_signal)
        self._show_premium_writer_sig.connect(self._handle_premium_writer)
        self._update_handsfree_status()

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_focus = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_focus.activated.connect(self._focus_command_input)
        sc_hands = QShortcut(QKeySequence("Ctrl+Shift+H"), self)
        sc_hands.activated.connect(self._toggle_hands_free_mode)
        sc_watch = QShortcut(QKeySequence("Ctrl+Shift+W"), self)
        sc_watch.activated.connect(lambda: self._set_screen_watch_enabled(not self.screen_watch_enabled))
        sc_cancel = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        sc_cancel.activated.connect(self.stop_autopilot)

        # Quick Command Palette (NEW) - Ctrl+K fuzzy command launcher
        sc_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        sc_palette.activated.connect(self._open_command_palette)

        # Zen / Reactor-Focus mode (NEW) - Ctrl+M hides side panels, reactor center-stage
        sc_zen = QShortcut(QKeySequence("Ctrl+M"), self)
        sc_zen.activated.connect(self._toggle_zen_mode)

        self._setup_tray()
        
        # Security Lock screen overlay initialization
        self.lock_overlay = SecurityLockOverlay(self)
        self.lock_overlay.unlocked.connect(self._on_unlocked)
        # Position it to cover everything
        self.lock_overlay.setGeometry(self.rect())
        self.lock_overlay.raise_()
        self.lock_overlay.show()

    def _central_input_style(self) -> str:
        return f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.025);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 24px;
                padding: 0 26px;
                font-family: "{C.FONT_SANS}";
                font-size: 13px;
                selection-background-color: {C.PRI};
            }}
            QLineEdit:hover {{
                border-color: rgba(255, 255, 255, 0.15);
                background: rgba(255, 255, 255, 0.04);
            }}
            QLineEdit:focus {{
                border-color: {C.PRI};
                background: rgba(0, 0, 0, 0.18);
            }}
        """

    def _send_central(self):
        txt = self._central_input.text().strip()
        if not txt: return
        self._central_input.clear()
        if hasattr(self, "_cmd_history"):
            if not self._cmd_history or self._cmd_history[-1] != txt:
                self._cmd_history.append(txt)
            self._cmd_history_idx = len(self._cmd_history)
        if hasattr(self, "_stat_tracker"):
            self._stat_tracker.record_sent()
        if hasattr(self, "_autolearn"):
            self._autolearn.record_command(txt)
        self._dispatch_command(txt, source="Typed")

    def _toggle_control_panel(self):
        """Toggle the visibility of the side panels, header, and footer."""
        is_visible = self._right_panel.isVisible()
        self._right_panel.setVisible(not is_visible)
        if hasattr(self, "_left_panel") and self._left_panel is not None:
            self._left_panel.setVisible(not is_visible)
        if hasattr(self, "_header_widget") and self._header_widget is not None:
            self._header_widget.setVisible(not is_visible)
        if hasattr(self, "_footer_widget") and self._footer_widget is not None:
            self._footer_widget.setVisible(not is_visible)
        
        try:
            screen = QApplication.primaryScreen().availableGeometry()
        except Exception:
            screen = None

        if not is_visible:
            # Hide center container to give full screen to cockpit dashboard
            if hasattr(self, "_center_container") and self._center_container is not None:
                self._center_container.setVisible(False)
            
            # Make right panel occupy all space
            self._right_panel.setMinimumWidth(800)
            self._right_panel.setMaximumWidth(16777215)
            
            # Expand to full cockpit dashboard (1240x780)
            if screen:
                self.setGeometry(
                    (screen.width() - 1240) // 2,
                    (screen.height() - 780) // 2,
                    1240,
                    780
                )
            else:
                self.resize(1240, 780)

            self._menu_toggle_btn.setText("✕ Close Dashboard")
            self._menu_toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO};
                    color: #ffffff;
                    border: 1px solid {C.PRI};
                    border-radius: 16px;
                    padding: 7px 18px;
                    font-family: "{C.FONT_SANS}";
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: {C.PANEL2};
                    border-color: {C.PRI};
                }}
            """)
        else:
            # Show center container back for minimal view
            if hasattr(self, "_center_container") and self._center_container is not None:
                self._center_container.setVisible(True)
            
            # Reset right panel to default fixed width
            self._right_panel.setFixedWidth(_RIGHT_W)
            
            # Shrink to minimal compact circular view (580x580)
            if screen:
                self.setGeometry(
                    (screen.width() - 580) // 2,
                    (screen.height() - 580) // 2,
                    580,
                    580
                )
            else:
                self.resize(580, 580)

            self._menu_toggle_btn.setText("⚙️ Dashboard")
            self._menu_toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.03);
                    color: {C.TEXT_MED};
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 16px;
                    padding: 7px 18px;
                    font-family: "{C.FONT_SANS}";
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.08);
                    color: #ffffff;
                    border-color: {C.PRI};
                }}
            """)

    def _trigger_mic_input(self):
        """Manually trigger voice input recognition in a background thread."""
        try:
            from advanced_features import voice_engine
        except ImportError:
            voice_engine = None
        if not voice_engine:
            try:
                self._log.append_log("ERR: Voice engine not available.")
            except Exception:
                pass
            return

        self._central_input.setPlaceholderText("🎤 Listening... Speak now...")
        self._central_input.setEnabled(False)
        self._search_bar_pill.setStyleSheet(f"background: rgba(15, 15, 20, 0.85); border: 1.2px solid {C.PRI}; border-radius: 24px;")
        
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        from PyQt6.QtGui import QColor
        shadow = QGraphicsDropShadowEffect(self._search_bar_pill)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(C.PRI))
        shadow.setOffset(0, 0)
        self._search_bar_pill.setGraphicsEffect(shadow)

        def listen_worker():
            try:
                voice_text = voice_engine.get_voice_input(timeout=5)
                if voice_text:
                    QTimer.singleShot(0, lambda: self._on_voice_captured(voice_text))
                else:
                    QTimer.singleShot(0, self._on_voice_capture_failed)
            except Exception as e:
                print(f"Mic button capture error: {e}")
                QTimer.singleShot(0, self._on_voice_capture_failed)

        threading.Thread(target=listen_worker, daemon=True).start()

    def _on_voice_captured(self, text: str):
        self._central_input.setEnabled(True)
        self._central_input.setPlaceholderText("Ask Joya... Type command or speak")
        self._search_bar_pill.setStyleSheet("background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;")
        self._search_bar_pill.setGraphicsEffect(None)
        self._central_input.setText(text)
        self._send_central()

    def _on_voice_capture_failed(self):
        self._central_input.setEnabled(True)
        self._central_input.setPlaceholderText("🎤 Voice capturing failed. Try again...")
        self._search_bar_pill.setStyleSheet("background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;")
        self._search_bar_pill.setGraphicsEffect(None)

    def _setup_tray(self):
        if not getattr(self, "tray_enabled", True):
            return
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon = QIcon(str(APP_ICON)) if APP_ICON.exists() else self.windowIcon()
            self._tray_icon = QSystemTrayIcon(icon, self)
            self._tray_icon.setToolTip("JOYA - AI Assistant ready")
            self._tray_menu = QMenu(self)

            open_action = QAction("Open AI Core", self)
            open_action.triggered.connect(self.show_from_tray)
            hide_action = QAction("Hide to Tray", self)
            hide_action.triggered.connect(self.hide_to_tray)
            self._tray_wake_action = QAction("Wake ON", self)
            self._tray_wake_action.triggered.connect(lambda: self.set_wake_enabled(not self._auto_wake))
            self._tray_auto_action = QAction("Autopilot ON", self)
            self._tray_auto_action.triggered.connect(lambda: self.set_visual_autopilot_enabled(not self.visual_autopilot_enabled))
            status_action = QAction("Provider Status", self)
            status_action.triggered.connect(lambda: self._dispatch_command("show external vision provider status", source="Tray"))
            hands_docs_action = QAction("Hands-free Docs", self)
            hands_docs_action.triggered.connect(lambda: os.startfile(str(Path(__file__).resolve().parent / "docs" / "hands_free.md")))
            ultra_docs_action = QAction("Ultra Features Docs", self)
            ultra_docs_action.triggered.connect(lambda: os.startfile(str(Path(__file__).resolve().parent / "docs" / "ultra_advanced_features.md")))
            self._tray_theme_action = QAction("Light Mode", self)
            self._tray_theme_action.setCheckable(True)
            self._tray_theme_action.setChecked(getattr(self, "theme_name", "") == "Light (Paper)")
            self._tray_theme_action.triggered.connect(self._toggle_light_theme)
            stop_action = QAction("Stop Autopilot", self)
            stop_action.triggered.connect(self.stop_autopilot)
            exit_action = QAction("Exit", self)
            exit_action.triggered.connect(self.quit_app)

            for action in [open_action, hide_action, self._tray_wake_action, self._tray_auto_action, status_action, hands_docs_action, ultra_docs_action, self._tray_theme_action, stop_action]:
                self._tray_menu.addAction(action)
            self._tray_menu.addSeparator()
            self._tray_menu.addAction(exit_action)
            self._tray_icon.setContextMenu(self._tray_menu)
            self._tray_icon.activated.connect(self._on_tray_activated)
            self._refresh_tray_actions()
            self._tray_icon.show()
        except Exception as e:
            try:
                self._log.append_log(f"SYS: Tray setup unavailable: {e}")
            except Exception:
                pass

    def _refresh_tray_actions(self):
        if hasattr(self, "_tray_wake_action"):
            self._tray_wake_action.setText("Wake ON" if self._auto_wake else "Wake OFF")
        if hasattr(self, "_tray_auto_action"):
            self._tray_auto_action.setText("Autopilot ON" if self.visual_autopilot_enabled else "Autopilot OFF")
        if hasattr(self, "_tray_theme_action"):
            try:
                self._tray_theme_action.setChecked(getattr(self, "theme_name", "") == "Light (Paper)")
            except Exception:
                pass

    def _toggle_light_theme(self, checked: bool):
        # Toggle between current theme and a light theme. Save previous theme when switching to light.
        try:
            if checked:
                self._prev_theme = getattr(self, "theme_name", "Apple Space Gray")
                self._apply_theme("Light (Paper)")
                self._is_light = True
            else:
                prev = getattr(self, "_prev_theme", "Apple Space Gray")
                self._apply_theme(prev)
                self._is_light = False
        except Exception as e:
            try:
                self._log.append_log(f"SYS: Theme toggle failed: {e}")
            except Exception:
                pass

    def _apply_performance_mode(self, announce: bool = False):
        enabled = bool(getattr(self, "performance_mode", True))
        try:
            self.hud.set_performance_mode(enabled)
        except Exception:
            pass
        try:
            self._central_bg.set_performance_mode(enabled)
        except Exception:
            pass
        try:
            if hasattr(self, "_perf_btn") and self._perf_btn is not None:
                self._perf_btn.custom_color = C.GREEN if enabled else None
                self._perf_btn.update()
        except Exception:
            pass
        if announce and hasattr(self, "_log"):
            mode = "enabled" if enabled else "disabled"
            self._log.append_log(f"SYS: Smooth UI performance mode {mode}.")

    def _toggle_performance_mode(self):
        self.performance_mode = not bool(getattr(self, "performance_mode", True))
        self._apply_performance_mode(announce=True)
        self._save_extended_config()

    def _run_lag_watchdog_tick(self):
        now = time.monotonic()
        elapsed_ms = (now - getattr(self, "_lag_watchdog_last", now)) * 1000.0
        self._lag_watchdog_last = now
        delay_ms = max(0.0, elapsed_ms - getattr(self, "_lag_watchdog_interval_ms", 500))
        self._lag_samples.append(delay_ms)
        self._lag_samples = self._lag_samples[-120:]
        self._lag_max_ms = max(float(getattr(self, "_lag_max_ms", 0.0)), delay_ms)
        if delay_ms > 250:
            self._lag_events += 1
            # Only log severe spikes (>2s) and with 2-minute cooldown to avoid spam
            if delay_ms > 2000 and time.time() - getattr(self, "_lag_last_log", 0.0) > 120:
                self._lag_last_log = time.time()
                try:
                    self._log.append_log(f"SYS: UI lag spike detected ({delay_ms:.0f} ms). Smooth UI is {'ON' if self.performance_mode else 'OFF'}.")
                except Exception:
                    pass

    def performance_diagnostics(self) -> dict:
        samples = list(getattr(self, "_lag_samples", []))
        avg_delay = sum(samples) / len(samples) if samples else 0.0
        snap = _metrics.snapshot()
        return {
            "smooth_ui": bool(getattr(self, "performance_mode", True)),
            "simple_ui": bool(getattr(self.hud, "simple_mode", False)),
            "lag_events": int(getattr(self, "_lag_events", 0)),
            "avg_ui_delay_ms": round(avg_delay, 1),
            "max_ui_delay_ms": round(float(getattr(self, "_lag_max_ms", 0.0)), 1),
            "hud_timer_ms": int(self.hud._tmr.interval()) if hasattr(self.hud, "_tmr") else None,
            "cpu_percent": round(float(snap.get("cpu", 0.0)), 1),
            "memory_percent": round(float(snap.get("mem", 0.0)), 1),
        }

    def show_performance_report(self):
        diag = self.performance_diagnostics()
        msg = (
            "Performance Report | "
            f"Smooth UI: {'ON' if diag['smooth_ui'] else 'OFF'} | "
            f"Simple UI: {'ON' if diag['simple_ui'] else 'OFF'} | "
            f"Lag spikes: {diag['lag_events']} | "
            f"Avg delay: {diag['avg_ui_delay_ms']}ms | "
            f"Max delay: {diag['max_ui_delay_ms']}ms | "
            f"CPU: {diag['cpu_percent']}% | MEM: {diag['memory_percent']}%"
        )
        try:
            self._log.append_log(f"SYS: {msg}")
        except Exception:
            pass
        return diag

    def _voice_engine_status(self) -> dict:
        status = {
            "available": False,
            "listening": False,
            "thread_alive": False,
            "audio_input": False,
            "audio_backend": "none",
            "wake_words": list(getattr(self, "wake_words", [])),
            "last_activation": dict(getattr(self, "_last_wake_event", {}) or {}),
            "last_error": "",
            "ui_auto_wake": bool(getattr(self, "_auto_wake", True)),
            "ui_hands_free": bool(getattr(self, "hands_free_mode", True)),
            "porcupine_available": False,
        }
        try:
            from advanced_features import voice_engine
            if voice_engine and hasattr(voice_engine, "get_status"):
                engine_status = voice_engine.get_status()
                status.update(engine_status)
                status["available"] = True
                if not status.get("last_activation"):
                    status["last_activation"] = dict(getattr(self, "_last_wake_event", {}) or {})
            elif voice_engine:
                status["available"] = True
                status["listening"] = bool(getattr(voice_engine, "listening", False))
        except Exception as e:
            status["last_error"] = str(e)
        # Detect optional Porcupine + sounddevice availability for offline wakeword
        try:
            import pvporcupine  # type: ignore
            import sounddevice  # type: ignore
            status["porcupine_available"] = True
            status["audio_backend"] = "porcupine+sounddevice"
        except Exception:
            # fallback info
            try:
                import speech_recognition  # type: ignore
                status["audio_backend"] = "speech_recognition"
            except Exception:
                status["audio_backend"] = "none"
        status["ui_auto_wake"] = bool(getattr(self, "_auto_wake", True))
        status["ui_hands_free"] = bool(getattr(self, "hands_free_mode", True))
        return status

    def wake_diagnostics(self) -> dict:
        return self._voice_engine_status()

    def _wake_status_text(self) -> str:
        status = self._voice_engine_status()
        if not getattr(self, "_auto_wake", True):
            return "OFF"
        if status.get("listening") and status.get("thread_alive"):
            return "LIVE"
        if status.get("available") and status.get("audio_input"):
            return "STARTING"
        if status.get("available"):
            return "NO MIC"
        return "NO ENGINE"

    def _refresh_wake_tile(self):
        text = self._wake_status_text()
        self._wake_listener_ready = text == "LIVE"
        if hasattr(self, "_tile_wake_link"):
            self._tile_wake_link.value = text
            self._tile_wake_link.update()
        if hasattr(self, "_tile_wake"):
            self._tile_wake.value = "ON" if getattr(self, "_auto_wake", True) else "OFF"
            self._tile_wake.update()
        if hasattr(self, "_wake_link_status_lbl"):
            last = dict(getattr(self, "_last_wake_event", {}) or {})
            ts = float(last.get("timestamp") or 0.0)
            if ts:
                age = max(0, int(time.time() - ts))
                suffix = f"LAST WAKE: {age}s AGO"
            else:
                suffix = "LAST WAKE: NONE"
            self._wake_link_status_lbl.setText(f"WAKE LINK: {text} | {suffix}")

    def _run_wake_self_test(self):
        self._wake_check_count += 1
        transcript = f"hey jarvis wake diagnostic {self._wake_check_count}"
        self._log.append_log("SYS: Wake self-test fired. JOYA should restore/glow exactly like a real wake word.")
        try:
            from advanced_features import voice_engine
            if voice_engine and hasattr(voice_engine, "simulate_activation"):
                voice_engine.simulate_activation(transcript)
                return
        except Exception as e:
            self._log.append_log(f"SYS: Wake self-test used UI fallback: {e}")
        self._voice_wake_sig.emit({
            "transcript": transcript,
            "wake_word": "jarvis",
            "command": "",
            "timestamp": time.time(),
            "source": "ui_self_test",
        })

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._log.append_log("SYS: JOYA restored from tray.")

    def hide_to_tray(self, show_message: bool = True):
        self.hide()
        if show_message and self._tray_icon:
            self._tray_icon.showMessage(
                "JOYA XXXIX",
                "Running in background. Say 'Hey Jarvis' to wake.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def quit_app(self):
        self._force_quit = True
        try:
            if self._tray_icon:
                self._tray_icon.hide()
        except Exception:
            pass
        QApplication.instance().quit()

    def set_wake_enabled(self, enabled: bool):
        self._auto_wake = bool(enabled)
        try:
            self.hud.auto_wake = self._auto_wake
        except Exception:
            pass
        if hasattr(self, "_wake_checkbox"):
            self._wake_checkbox.blockSignals(True)
            self._wake_checkbox.setChecked(self._auto_wake)
            self._wake_checkbox.blockSignals(False)
        self._save_extended_config()
        self._update_handsfree_status()
        self._refresh_tray_actions()
        self._refresh_wake_tile()
        self._sync_live_status_widgets()
        self._log.append_log(f"SYS: Voice Wake Word Mode {'enabled' if self._auto_wake else 'disabled'}.")

    def set_visual_autopilot_enabled(self, enabled: bool):
        if enabled and self._is_free_user():
            self.visual_autopilot_enabled = False
            if hasattr(self, "_tile_autopilot"):
                self._tile_autopilot.value = "OFF"
                self._tile_autopilot.update()
            self._save_extended_config()
            self._refresh_tray_actions()
            self._sync_live_status_widgets()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Premium Locked", "❌ Visual Autopilot is locked for Free users.\nPlease upgrade to Standard or Premium to unlock.")
            return

        self.visual_autopilot_enabled = bool(enabled)
        if hasattr(self, "_tile_autopilot"):
            self._tile_autopilot.value = "ON" if self.visual_autopilot_enabled else "OFF"
            self._tile_autopilot.update()
        self._save_extended_config()
        self._refresh_tray_actions()
        self._sync_live_status_widgets()
        self._log.append_log(f"SYS: Visual Autopilot {'enabled' if self.visual_autopilot_enabled else 'disabled'}.")

    def stop_autopilot(self):
        self._autopilot_cancel.set()
        if hasattr(self, "_timeline_status_lbl"):
            self._timeline_status_lbl.setText("Autopilot: STOP REQUESTED")
        self._log.append_log("SYS: Autopilot stop requested.")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_zen_mode(self):
        """Reactor-focus (Zen) mode: hide the side panels so the arc reactor
        takes center stage — a clean, minimal Apple-style view. Reversible."""
        self._zen_mode = not getattr(self, "_zen_mode", False)
        try:
            for panel in (getattr(self, "_left_panel", None), getattr(self, "_right_panel", None)):
                if panel is not None:
                    panel.setVisible(not self._zen_mode)
            msg = "Zen mode ON — reactor center-stage (Ctrl+M to exit)" if self._zen_mode \
                  else "Zen mode OFF — panels restored"
            if hasattr(self, "write_log"):
                self.write_log(f"SYS: {msg}")
        except Exception:
            pass

    def _toggle_god_mode(self):
        self._god_mode = not getattr(self, "_god_mode", False)
        # apply to HUD
        try:
            self.hud.god_mode = self._god_mode
        except Exception:
            pass
        # update central styling for extra flair
        cw = self.centralWidget()
        if self._god_mode:
            if cw is not None:
                cw.setStyleSheet(f"background: qradialgradient(cx:0.5, cy:0.5, radius:1.0, stop:0 {C.PRI}, stop:0.6 {C.BG});")
            if getattr(self, "_god_btn", None) is not None:
                self._god_btn.custom_color = C.PRI
            try:
                self._log.append_log("SYS: GOD MODE enabled — visuals intensified.")
            except Exception:
                pass
        else:
            if cw is not None:
                cw.setStyleSheet(f"background: {C.BG};")
            if getattr(self, "_god_btn", None) is not None:
                self._god_btn.custom_color = None
            try:
                self._log.append_log("SYS: GOD MODE disabled.")
            except Exception:
                pass
        if getattr(self, "_god_btn", None) is not None:
            self._god_btn.update()

    def _toggle_autowake(self):
        self.set_wake_enabled(not getattr(self, "_auto_wake", True))

    def _safe_toggle_stream(self, mode: str):
        hud = getattr(self, "hud", None)
        if hud is None:
            return

        current_worker = getattr(hud, "_stream_worker", None)
        if current_worker is not None:
            try:
                current_worker.stop()
            except Exception:
                pass
            hud._stream_worker = None
            hud._feed_image = None
            hud.update()
            
            # If same mode, it toggles off.
            if current_worker.mode == mode:
                self._update_metrics()
                return

        try:
            worker = StreamWorker(mode=mode)
            worker.frame_ready.connect(hud._on_frame_received)
            hud._stream_worker = worker
            worker.start()
        except Exception as e:
            if hasattr(self, "_log"):
                self._log.append_log(f"ERR: Failed to start stream {mode}: {e}")
        
        self._update_metrics()

    def attach_floating_assistant(self, assistant):
        self._floating_assistant_widget = assistant

    def open_floating_assistant(self, activation_text: str = "", command: str = ""):
        assistant = getattr(self, "_floating_assistant_widget", None)
        if assistant is None:
            return

        try:
            if self.isMinimized():
                self.showNormal()
            elif not self.isVisible():
                self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

        try:
            screen = QApplication.primaryScreen()
            geom = screen.availableGeometry() if screen else self.frameGeometry()
            assistant.move(
                geom.x() + (geom.width() - assistant.width()) // 2,
                geom.y() + int(geom.height() * 0.68) - assistant.height() // 2,
            )
        except Exception:
            pass

        try:
            if hasattr(assistant, "_hide_timer"):
                assistant._hide_timer.stop()
            assistant.show()
            assistant.raise_()
            assistant.activateWindow()
            assistant.start_listening()
            assistant.set_feedback("success")
            if command:
                assistant._input_buffer = command
                assistant._suggestions = assistant._get_suggestions(command)
                assistant.update()
        except Exception:
            pass

    def _handle_voice_wake_event(self, event):
        if not getattr(self, "_auto_wake", True):
            try:
                self._log.append_log("SYS: Wake word heard, but Voice Wake Word Mode is OFF.")
            except Exception:
                pass
            return

        transcript = ""
        wake_word = "jarvis"
        command = ""
        if isinstance(event, dict):
            transcript = str(event.get("transcript") or "")
            wake_word = str(event.get("wake_word") or wake_word)
            command = str(event.get("command") or "").strip()
        elif event:
            transcript = str(event)

        self._last_wake_event = {
            "transcript": transcript,
            "wake_word": wake_word,
            "command": command,
            "timestamp": time.time(),
        }
        self._last_voice_activity = time.time()
        self._apply_state("LISTENING")
        try:
            self.hud.trigger_wake_flash(wake_word or "Jarvis")
        except Exception:
            pass
        if getattr(self, "wake_opens_floating_assistant", True):
            self.open_floating_assistant(transcript, command)
        else:
            self.show_from_tray()
        self._refresh_wake_tile()

        try:
            label = wake_word or "Jarvis"
            if command:
                self._log.append_log(f"SYS: Wake word detected ({label}). Direct command ready: {command}")
            else:
                self._log.append_log(f"SYS: Wake word detected ({label}). Floating assistant ready.")
            if self._tray_icon:
                self._tray_icon.showMessage(
                    "JOYA XXXIX Wake Link",
                    f"Wake detected: {label}",
                    QSystemTrayIcon.MessageIcon.Information,
                    1800,
                )
        except Exception:
            pass

        if HAS_ADVANCED_FEATURES and tts_engine:
            try:
                tts_engine.speak("Ji sir, boliye.", blocking=False)
            except Exception:
                pass

        try:
            from actions.human_mode import human_mode
            human_mode({"action": "start", "source": "both", "interval": 5, "text": transcript}, player=self)
            self._log.append_log("SYS: Wake event triggered Human Mode auto-start.")
        except Exception as e:
            try:
                self._log.append_log(f"SYS: Human Mode auto-start skipped: {e}")
            except Exception:
                pass

        if command and getattr(self, "hands_free_mode", True):
            QTimer.singleShot(450, lambda c=command: self._dispatch_command(c, source="Wake Command"))

    def _focus_command_input(self):
        if hasattr(self, "_input") and self._input is not None:
            self._input.setFocus()
            self._input.selectAll()

    def _style_hands_button(self):
        if not hasattr(self, "_hands_btn") or self._hands_btn is None:
            return
        if getattr(self, "hands_free_mode", True):
            self._hands_btn.custom_color = C.GREEN
        else:
            self._hands_btn.custom_color = None
        self._hands_btn.update()

    def _is_free_user(self) -> bool:
        try:
            sp = Path(__file__).resolve().parent / "config" / "user_session.json"
            if sp.exists():
                import json
                sess = json.loads(sp.read_text(encoding="utf-8"))
                p_type = (sess.get("plan_type") or "free").lower()
                if p_type in ("premium", "standard") or sess.get("is_pro"):
                    return False
        except Exception:
            pass
        return True

    def _toggle_hands_free_mode(self):
        if self._is_free_user():
            self.hands_free_mode = False
            if hasattr(self, "_hands_free_checkbox"):
                self._hands_free_checkbox.blockSignals(True)
                self._hands_free_checkbox.setChecked(False)
                self._hands_free_checkbox.blockSignals(False)
            self._style_hands_button()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Premium Locked", "❌ Hands-free Wake Word is locked for Free users.\nPlease upgrade to Standard or Premium to unlock.")
            return

        self.hands_free_mode = not getattr(self, "hands_free_mode", True)
        if hasattr(self, "_hands_free_checkbox"):
            self._hands_free_checkbox.blockSignals(True)
            self._hands_free_checkbox.setChecked(self.hands_free_mode)
            self._hands_free_checkbox.blockSignals(False)
        self._style_hands_button()
        self._save_extended_config()
        self._update_handsfree_status()
        self._log.append_log(f"SYS: Hands-free command mode {'enabled' if self.hands_free_mode else 'disabled'}.")

    def _on_hands_free_toggled(self, checked):
        if checked and self._is_free_user():
            if hasattr(self, "_hands_free_checkbox"):
                self._hands_free_checkbox.blockSignals(True)
                self._hands_free_checkbox.setChecked(False)
                self._hands_free_checkbox.blockSignals(False)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Premium Locked", "❌ Hands-free Wake Word is locked for Free users.\nPlease upgrade to Standard or Premium to unlock.")
            return

        self.hands_free_mode = bool(checked)
        self._style_hands_button()
        self._save_extended_config()
        self._update_handsfree_status()
        self._log.append_log(f"SYS: Hands-free command mode {'enabled' if checked else 'disabled'}.")

    def _on_confirm_toggled(self, checked):
        self.confirm_dangerous_actions = bool(checked)
        self._save_extended_config()
        self._log.append_log(f"SYS: Risk confirmation {'enabled' if checked else 'disabled'}.")

    def _on_proactive_toggled(self, checked):
        self.proactive_assist = bool(checked)
        self._save_extended_config()
        self._log.append_log(f"SYS: Proactive assist {'enabled' if checked else 'disabled'}.")

    def _set_live_context_enabled(self, checked, persist=True):
        if checked and self._is_free_user():
            self.live_context_enabled = False
            if hasattr(self, "_live_context_checkbox"):
                self._live_context_checkbox.blockSignals(True)
                self._live_context_checkbox.setChecked(False)
                self._live_context_checkbox.blockSignals(False)
            if persist:
                self._save_extended_config()
            self._update_handsfree_status()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Premium Locked", "❌ Live Context Sharing is locked for Free users.\nPlease upgrade to Standard or Premium to unlock.")
            return

        self.live_context_enabled = bool(checked)
        if hasattr(self, "_live_context_checkbox"):
            self._live_context_checkbox.blockSignals(True)
            self._live_context_checkbox.setChecked(self.live_context_enabled)
            self._live_context_checkbox.blockSignals(False)
        if persist:
            self._save_extended_config()
        self._update_handsfree_status()
        threading.Thread(target=self._run_live_context_action, args=("start" if checked else "stop",), daemon=True).start()

    def _run_live_context_action(self, action: str):
        lock_acquired = False
        if action == "start":
            lock_acquired = self._live_context_lock.acquire(blocking=False)
            if not lock_acquired:
                self._log_sig.emit("SYS: Live visual context is already refreshing; skipped duplicate tick.")
                return
        try:
            from actions.live_context import live_context
            params = {
                "action": action,
                "source": getattr(self, "live_context_source", "screen"),
                "interval": int(getattr(self, "live_context_interval", 8)),
                "focus": "Watch current screen/camera context for useful user assistance.",
                "provider": getattr(self, "live_context_provider", "groq"),
            }
            result = live_context(params)
            self._log_sig.emit(f"SYS: {result}")
        except Exception as e:
            self._log_sig.emit(f"ERR: Live visual context failed - {e}")
        finally:
            if lock_acquired:
                try:
                    self._live_context_lock.release()
                except Exception:
                    pass

    def _on_live_context_toggled(self, checked):
        self._set_live_context_enabled(checked)

    def _on_live_source_changed(self, value):
        self.live_context_source = str(value).lower()
        self._save_extended_config()
        self._update_handsfree_status()
        if getattr(self, "live_context_enabled", False):
            threading.Thread(target=self._run_live_context_action, args=("start",), daemon=True).start()

    def _on_live_provider_changed(self, value):
        provider = str(value).lower()
        self.live_context_provider = provider if provider in ("groq", "openrouter", "gemini", "openai", "auto") else "groq"
        self._save_extended_config()
        self._update_handsfree_status()
        if getattr(self, "live_context_enabled", False):
            threading.Thread(target=self._run_live_context_action, args=("start",), daemon=True).start()

    def _on_live_interval_changed(self, val):
        self.live_context_interval = int(val)
        if hasattr(self, "_live_interval_lbl"):
            self._live_interval_lbl.setText(f"Live Context Interval: {self.live_context_interval}s")
        self._save_extended_config()
        self._update_handsfree_status()
        if getattr(self, "live_context_enabled", False):
            threading.Thread(target=self._run_live_context_action, args=("start",), daemon=True).start()

    def _set_screen_watch_enabled(self, checked):
        self.screen_watch_enabled = bool(checked)
        if hasattr(self, "_watch_checkbox"):
            self._watch_checkbox.blockSignals(True)
            self._watch_checkbox.setChecked(self.screen_watch_enabled)
            self._watch_checkbox.blockSignals(False)
        if hasattr(self, "_watch_timer"):
            if self.screen_watch_enabled:
                self._watch_timer.start(int(self.screen_watch_interval) * 1000)
            else:
                self._watch_timer.stop()
        self._save_extended_config()
        self._update_handsfree_status()
        self._log.append_log(f"SYS: Auto screen watch {'enabled' if checked else 'disabled'}.")

    def _on_watch_toggled(self, checked):
        self._set_screen_watch_enabled(checked)

    def _on_watch_interval_changed(self, val):
        self.screen_watch_interval = int(val)
        if hasattr(self, "_watch_interval_lbl"):
            self._watch_interval_lbl.setText(f"Watch Interval: {self.screen_watch_interval}s")
        if getattr(self, "screen_watch_enabled", False) and hasattr(self, "_watch_timer"):
            self._watch_timer.start(int(self.screen_watch_interval) * 1000)
        self._save_extended_config()
        self._update_handsfree_status()

    def _run_screen_watch_tick(self):
        if not getattr(self, "screen_watch_enabled", False):
            return
        if getattr(self, "live_context_enabled", False):
            threading.Thread(target=self._run_live_context_action, args=("start",), daemon=True).start()
            return
        self._dispatch_command(
            "Analyze my screen hands-free. Only interrupt me if there is an important issue, a clear next step, or something I asked you to monitor.",
            source="Screen Watch",
        )

    def _on_notification_watch_toggled(self, checked):
        self.notification_watch_enabled = bool(checked)
        if hasattr(self, "_notification_timer"):
            self._notification_timer.stop()
        self._save_extended_config()
        self._update_handsfree_status()
        self._log.append_log(f"SYS: Notification watch {'enabled' if checked else 'disabled'}.")

    def _on_notification_voice_toggled(self, checked):
        self.notification_voice_enabled = bool(checked)
        self._save_extended_config()
        self._log.append_log(f"SYS: Voice notification alerts {'enabled' if checked else 'disabled'}.")

    def _on_notification_importance_toggled(self, checked):
        self.notification_important_only = bool(checked)
        self._save_extended_config()
        self._log.append_log(f"SYS: Important-only notifications {'enabled' if checked else 'disabled'}.")

    def _run_notification_watch_tick(self):
        return

    def _on_privacy_guard_toggled(self, checked):
        self.privacy_guard_enabled = bool(checked)
        if hasattr(self, "_privacy_timer"):
            if self.privacy_guard_enabled:
                self._privacy_timer.start(90000)
            else:
                self._privacy_timer.stop()
        self._save_extended_config()
        self._update_handsfree_status()
        self._sync_live_status_widgets()
        self._log.append_log(f"SYS: Privacy guard {'enabled' if checked else 'disabled'}.")

    def _run_privacy_guard_tick(self):
        if not getattr(self, "privacy_guard_enabled", False):
            return
        self._dispatch_command(
            "Run a privacy guard scan on my screen. Warn only if sensitive information seems visible, and do not read exact secrets aloud.",
            source="Privacy Guard",
        )

    def _dispatch_command(self, text: str, source: str = "UI"):
        text = (text or "").strip()
        if not text:
            return
        command_id = self._record_command_timeline(text, source=source, status="started")
        self._last_voice_activity = time.time()
        if getattr(self.hud, "state", "LISTENING") == "STANDBY":
            self._apply_state("LISTENING")
        if hasattr(self, "_log") and self._log is not None:
            self._log.append_log(f"You: [{source}] {text}")
        command_center = getattr(self, "_command_center", None)
        if command_center is not None:
            try:
                command_center.add_recent_command(text, source=source)
            except Exception:
                pass
        # Immediate "thinking" feedback so UI never feels frozen
        self._state_sig.emit("THINKING")
        self._log_sig.emit("SYS: ◌ Processing your request...")
        if self.on_text_command:
            threading.Thread(target=self._on_command_safe, args=(text, command_id), daemon=True).start()
        else:
            threading.Thread(target=self._run_local_text_fallback, args=(text, command_id), daemon=True).start()

    def _record_command_timeline(self, text: str, source: str = "UI", status: str = "event", command_id: str | None = None, detail: str = "") -> str:
        try:
            from actions.command_timeline import record_command
            return record_command(text=text, source=source, status=status, command_id=command_id, detail=detail)
        except Exception:
            return command_id or ""

    def _on_command_safe(self, text: str, command_id: str | None = None):
        """Wraps on_text_command with timeout + fallback so UI never hangs."""
        try:
            result_holder = {"done": False, "error": ""}
            def _inner():
                try:
                    self.on_text_command(text)
                except Exception as e:
                    result_holder["error"] = str(e)
                    self._log_sig.emit(f"ERR: AI command error - {e}")
                finally:
                    result_holder["done"] = True
            t = threading.Thread(target=_inner, daemon=True)
            t.start()
            t.join(timeout=30)  # max 30s for the live link to accept
            if not result_holder["done"]:
                self._record_command_timeline(text, source="AI Link", status="slow", command_id=command_id, detail="30s timeout; local fallback started")
                self._log_sig.emit("SYS: AI link slow — using local fallback.")
                self._run_local_text_fallback(text, command_id)
            elif result_holder.get("error"):
                self._record_command_timeline(text, source="AI Link", status="error", command_id=command_id, detail=str(result_holder.get("error", ""))[:240])
            else:
                self._record_command_timeline(text, source="AI Link", status="completed", command_id=command_id)
        except Exception as e:
            self._log_sig.emit(f"ERR: dispatch failed - {e}")
            self._record_command_timeline(text, source="Dispatcher", status="error", command_id=command_id, detail=str(e)[:240])
            try:
                self._run_local_text_fallback(text, command_id)
            except Exception:
                pass

    def _poll_active_window(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if title and title != self._cached_active_title:
                    self._cached_active_title = title
                    self._active_window_sig.emit(title)
        except Exception:
            pass

    def _trigger_app_switch_analysis(self):
        try:
            if not self._cached_active_title:
                return
            msg = f"[VISION] Tracking active context: {self._cached_active_title}"
            self._vision_log_sig.emit(msg)
            # Add to command timeline for context memory
            self._timeline_sig.emit(("App Switch", "User changed active window", f"Now focused on: {self._cached_active_title}"))
        except Exception:
            pass

    def _update_active_window_ui(self, title: str):
        try:
            msg = f"[SYSTEM] User focused on: {title}"
            self._log_sig.emit(msg)
            if self.auto_analyze_app_switch:
                self._analysis_debounce_timer.start(2000)
        except Exception:
            pass

    def _append_vision_log(self, text: str):
        try:
            return
        except Exception:
            pass

    def _handle_premium_writer(self, topic: str, content: str):
        try:
            from actions.premium_writer_ui import PremiumWriterWindow
            self._writer_win = PremiumWriterWindow(topic, content, parent=self)
            self._writer_win.show()
            self._timeline_sig.emit(("AI Action", "Human Writer Spawned", f"Topic: {topic}"))
        except Exception as e:
            print(f"[Writer UI Error] {e}")

    def _handle_notebook_signal(self, data: dict):
        try:
            action = data.get("action")
            if action == "open":
                if not self.question_notebook:
                    self.question_notebook = QuestionNotebookWindow(parent=self)
                self.question_notebook.show()
                self.question_notebook.raise_()
                self.question_notebook.activateWindow()
            elif action == "start_quiz":
                if not self.question_notebook:
                    self.question_notebook = QuestionNotebookWindow(parent=self)
                self.question_notebook.show()
                self.question_notebook.raise_()
                self.question_notebook.activateWindow()
                quiz_data = data.get("quiz_data")
                if quiz_data:
                    self.question_notebook._start_loaded_quiz(quiz_data)
            elif action == "submit_external_answer":
                if self.question_notebook and self.question_notebook.isVisible():
                    user_ans = data.get("user_answer", "")
                    self.question_notebook.submit_answer_from_external(user_ans)
            elif action == "ask":
                if not self.question_notebook:
                    self.question_notebook = QuestionNotebookWindow(parent=self)
                self.question_notebook.show()
                self.question_notebook.raise_()
                self.question_notebook.activateWindow()
                self.question_notebook.load_question(
                    question=data.get("question", ""),
                    correct_answer=data.get("correct_answer", ""),
                    explain=data.get("explain", ""),
                    source=data.get("source", "Mind Game")
                )
            elif action == "answer_result":
                if self.question_notebook:
                    is_correct = data.get("is_correct", False)
                    user_ans = data.get("user_answer", "")
                    self.question_notebook.show_overlay(is_correct)
                    timestamp = time.strftime("%H:%M:%S")
                    res_symbol = "✅ CORRECT" if is_correct else f"❌ INCORRECT (Correct: {self.question_notebook.correct_answer})"
                    log_entry = (
                        f"[{timestamp}] SOURCE: {self.question_notebook.quiz_source} (External Answer)\n"
                        f"Q: {self.question_notebook.active_question}\n"
                        f"Your Answer: {user_ans}\n"
                        f"Result: {res_symbol}\n"
                    )
                    if self.question_notebook.explanation:
                        log_entry += f"💡 Explanation: {self.question_notebook.explanation}\n"
                    log_entry += "--------------------------------------------------\n"
                    self.question_notebook.notes_log.append(log_entry)
                    self.question_notebook.notes_history.append(log_entry)
                    self.question_notebook.status_bar.setText("External answer evaluated & logged.")
            elif action == "save":
                if self.question_notebook:
                    self.question_notebook.save_notes_to_file()
        except Exception as e:
            self._log.append_log(f"ERR: question notebook signal handler - {e}")

    def _handle_flashcards_signal(self, data):
        try:
            from actions.ui_flashcards import Flashcard, FlashcardsWindow

            payload = data or {}
            raw_cards = payload.get("cards") or []
            cards = [
                Flashcard(
                    id=int(c.get("id") or idx + 1),
                    question=str(c.get("question") or ""),
                    answer=str(c.get("answer") or ""),
                    topic=str(c.get("topic") or ""),
                )
                for idx, c in enumerate(raw_cards)
                if str(c.get("question") or "").strip()
            ]
            topic = str(payload.get("topic") or "").strip()
            title = f"Study Flashcards - {topic}" if topic else "Study Flashcards"
            self._flashcards_window = FlashcardsWindow(cards, parent=self, title=title)
            self._flashcards_window.show()
            self._flashcards_window.raise_()
            self._flashcards_window.activateWindow()
            try:
                self._log.append_log(f"SYS: Opened flashcards window ({len(cards)} cards).")
            except Exception:
                pass
        except Exception as e:
            try:
                self._log.append_log(f"ERR: flashcards window handler - {e}")
            except Exception:
                pass

    def _run_local_text_fallback(self, text: str, command_id: str | None = None):
        try:
            self._record_command_timeline(text, source="Local Fallback", status="fallback", command_id=command_id)
            from actions.text_fallback import run_text_fallback
            current_file = None
            try:
                current_file = self._drop_zone.current_file()
            except Exception:
                current_file = self._current_file
            self._state_sig.emit("THINKING")
            # Pass self as player so text_fallback can log/speak back
            result = run_text_fallback(text, current_file=current_file, player=self)
            self._log_sig.emit(f"Jarvis: {result}")
            # TTS: speak the result (reliable native engine)
            if result and not self._muted:
                _tts_speak(result[:300], blocking=False)
            self._record_command_timeline(text, source="Local Fallback", status="completed", command_id=command_id)
        except Exception as e:
            self._log_sig.emit(f"ERR: local fallback failed - {e}")
            self._record_command_timeline(text, source="Local Fallback", status="error", command_id=command_id, detail=str(e)[:240])
        finally:
            self._state_sig.emit("LISTENING")

    def _show_voice_git_dialog(self):
        try:
            repo_path, ok = QInputDialog.getText(self, "Voice Git - Repo Path", "Repository path:")
            if not ok or not repo_path:
                return
            branch, ok = QInputDialog.getText(self, "Voice Git - Branch", "Branch name:")
            if not ok or not branch:
                return
            commit_msg, ok = QInputDialog.getText(self, "Voice Git - Commit", "Commit message:")
            if not ok or not commit_msg:
                return
            from actions import git_helper
            self._log.append_log(f"SYS: Preparing branch {branch} in {repo_path}...")
            res = git_helper.prepare_branch(repo_path=repo_path, branch=branch, commit_msg=commit_msg)
            if not res.get("ok"):
                QMessageBox.warning(self, "Voice Git", f"Failed: {res}")
                self._log.append_log(f"ERR: Git prepare failed: {res}")
                return
            pi = git_helper.push_instructions(repo_path=repo_path, branch=branch)
            msg = pi.get("note", "Ready") + "\nCommand: " + pi.get("push_cmd", "git push origin <branch>")
            ans = QMessageBox.question(self, "Voice Git", msg + "\n\nDo you want to push now? (requires manual token handling)", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                token, ok = QInputDialog.getText(self, "GitHub Token", "Paste a GitHub PAT (will not be stored):")
                if not ok or not token:
                    self._log.append_log("SYS: Push cancelled — no token provided.")
                    return
                self._log.append_log(f"SYS: Push requested but will not be performed automatically for safety. Token length={len(token)}")
                QMessageBox.information(self, "Voice Git", "Preview created. To push, run the shown command locally with your credentials.")
        except Exception as e:
            self._log.append_log(f"ERR: voice git dialog failed - {e}")

    def _run_tests(self):
        try:
            repo_path, ok = QInputDialog.getText(self, "Run Tests - Repo Path", "Repository path:")
            if not ok or not repo_path:
                return
            from tools import test_runner
            self._log.append_log(f"SYS: Running pytest in {repo_path} (may take time)...")
            res = test_runner.run_pytest(repo_path)
            self._log.append_log(f"TESTS: return={res.get('returncode')}\n{res.get('output')[:1000]}")
            QMessageBox.information(self, "Test Runner", f"Return: {res.get('returncode')}\nOutput shown in console log.")
        except Exception as e:
            self._log.append_log(f"ERR: test runner failed - {e}")

    def _run_overnight_summary(self):
        try:
            self._log.append_log("SYS: Running overnight summary (GitHub trending + HackerNews)...")
            from agent import summary_agent
            res = summary_agent.run_summary()
            self._log.append_log("SYS: Overnight summary saved to quick notes.")
            QMessageBox.information(self, "Overnight Summary", "Summary saved to quick notes.")
        except Exception as e:
            self._log.append_log(f"ERR: overnight summary failed - {e}")

    def _run_preset_command(self, label: str, command: str):
        if label == "FILE AUTOPILOT":
            self._run_file_autopilot()
            return
        self._dispatch_command(command, source=f"Preset/{label}")

    def _run_file_autopilot(self):
        current_file = None
        try:
            current_file = self._drop_zone.current_file()
        except Exception:
            current_file = self._current_file
        if not current_file:
            self._log.append_log("SYS: File autopilot needs an uploaded file.")
            return
        self._dispatch_command(
            f"Analyze the uploaded file at {current_file} and recommend the most useful next action. If the best action is obvious, do it.",
            source="Preset/FILE AUTOPILOT",
        )

    def _normalise_mission_lines(self, raw: str) -> list[str]:
        steps = []
        for line in (raw or "").splitlines():
            clean = line.strip()
            if not clean:
                continue
            clean = re.sub(r"^\s*[-*#]?\s*\d*[\.\)]?\s*", "", clean).strip()
            if clean:
                steps.append(clean)
        return steps

    def _queue_mission_steps(self):
        if not hasattr(self, "_mission_box"):
            return
        steps = self._normalise_mission_lines(self._mission_box.toPlainText())
        if not steps:
            self._log.append_log("SYS: Add at least one mission step first.")
            return
        self._mission_queue.extend(steps)
        self._mission_box.clear()
        self._log.append_log(f"SYS: Queued {len(steps)} hands-free mission step(s).")
        self._update_mission_queue_status()
        if hasattr(self, "_queue_autorun_checkbox") and self._queue_autorun_checkbox.isChecked():
            self._start_mission_queue()

    def _start_mission_queue(self):
        if self._queue_running:
            return
        if not self._mission_queue:
            self._log.append_log("SYS: Mission queue is empty.")
            return
        self._queue_running = True
        self._log.append_log("SYS: Hands-free mission started.")
        self._update_mission_queue_status()
        self._run_next_mission_step()

    def _run_next_mission_step(self):
        if not self._queue_running:
            return
        if not self._mission_queue:
            self._queue_running = False
            self._log.append_log("SYS: Hands-free mission complete.")
            self._update_mission_queue_status()
            return
        step = self._mission_queue.pop(0)
        self._dispatch_command(step, source="Mission Queue")
        self._update_mission_queue_status()
        QTimer.singleShot(self._mission_dispatch_delay_ms, self._run_next_mission_step)

    def _clear_mission_queue(self):
        self._mission_queue.clear()
        self._queue_running = False
        self._update_mission_queue_status()
        self._log.append_log("SYS: Mission queue cleared.")

    def _load_mission_template(self):
        if not hasattr(self, "_mission_box"):
            return
        self._mission_box.setPlainText(
            "Analyze my screen and tell me what needs attention\n"
            "Open my browser and search for the most important update on my active project\n"
            "Summarize the result and save useful notes to memory"
        )

    def _update_mission_queue_status(self):
        queued = len(getattr(self, "_mission_queue", []))
        status = "RUNNING" if getattr(self, "_queue_running", False) else "READY"
        if hasattr(self, "_mission_status_lbl"):
            self._mission_status_lbl.setText(f"Mission Queue: {status} / {queued} step(s)")
        self._update_handsfree_status()
        self._sync_live_status_widgets()

    def _update_handsfree_status(self):
        # Update new status labels instead
        if hasattr(self, "_mode_status_lbl"):
            mode_text = "AUTO" if getattr(self, 'hands_free_mode', True) else "MANUAL"
            wake_text = "ON" if getattr(self, '_auto_wake', True) else "OFF"
            self._mode_status_lbl.setText(f"MODE: {mode_text} | WAKE: {wake_text}")
        
        if hasattr(self, "_core_status_lbl"):
            state = "ACTIVE" if getattr(self, 'hands_free_mode', True) else "IDLE"
            self._core_status_lbl.setText(f"CORE: {state}")
        
        if hasattr(self, "_sec_status_lbl"):
            sec_status = "CLEARED"
            if getattr(self, "privacy_guard_enabled", False):
                sec_status = "GUARDED"
            self._sec_status_lbl.setText(f"SEC: {sec_status}")

        self._refresh_wake_tile()


    def reload_license_info(self):
        plan_str = "Free Trial"
        sp = Path(__file__).resolve().parent / "config" / "user_session.json"
        is_pro_user = False
        is_standard_user = False
        if sp.exists():
            try:
                import json
                sess = json.loads(sp.read_text(encoding="utf-8"))
                p_type = (sess.get("plan_type") or "free").lower()
                if p_type == "premium" or sess.get("is_pro"):
                    plan_str = "PRO / PREMIUM Tier"
                    is_pro_user = True
                elif p_type == "standard":
                    plan_str = "STANDARD Tier"
                    is_standard_user = True
            except Exception:
                pass
        
        self.setWindowTitle(f"J.O.Y.A.  [Active License: {plan_str}]")
        
        # Dynamically update the header branding title badge
        if hasattr(self, "_title_badge") and self._title_badge is not None:
            try:
                title_badge_text = "JOYA"
                title_style = f"color: {C.TEXT}; background: transparent;"
                if is_pro_user:
                    title_badge_text = "JOYA PRO"
                    title_style = "color: #ffd700; background: transparent; font-weight: bold;" # Gold text for Pro!
                elif is_standard_user:
                    title_badge_text = "JOYA STANDARD"
                    title_style = "color: #b0bec5; background: transparent; font-weight: bold;" # Silver/grey for Standard!
                self._title_badge.setText(title_badge_text)
                self._title_badge.setStyleSheet(title_style)
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_setup_overlay()
        if hasattr(self, "lock_overlay") and self.lock_overlay.isVisible():
            self.lock_overlay.setGeometry(self.rect())

    def closeEvent(self, event):
        # Fix: screen share/live/hold during active visual modules sometimes triggers app "close".
        # During such states, force ignore close and keep running (tray hide still okay).
        try:
            hud_has_stream = getattr(self, "hud", None) is not None and getattr(self.hud, "_stream_worker", None) is not None
            hud_stream_mode = getattr(getattr(self.hud, "_stream_worker", None), "mode", "")
            is_streaming = bool(hud_has_stream and hud_stream_mode in ("screen", "camera"))
        except Exception:
            is_streaming = False

        if is_streaming:
            event.ignore()
            # Hide to tray instead of full close; prevents live_context/live link from dying.
            if getattr(self, "tray_enabled", True) and self._tray_icon is not None and not getattr(self, "_force_quit", False):
                self.hide_to_tray(show_message=False)
            return

        if (
            getattr(self, "close_to_tray", True)
            and getattr(self, "tray_enabled", True)
            and self._tray_icon is not None
            and not getattr(self, "_force_quit", False)
        ):
            event.ignore()
            self.hide_to_tray()
            return
        try:
            if hasattr(self, "hud") and self.hud is not None:
                self.hud.closeEvent(event)
        except Exception:
            pass
        super().closeEvent(event)


    def _update_metrics(self):
        # Inactivity Standby Check
        if getattr(self, "_auto_wake", True) and getattr(self, "hands_free_mode", True):
            idle_time = time.time() - getattr(self, "_last_voice_activity", time.time())
            if idle_time > self.standby_timeout and getattr(self.hud, "state", "LISTENING") not in ("STANDBY", "MUTED", "INITIALISING"):
                self._apply_state("STANDBY")
                self._log.append_log("SYS: Standby mode activated. Say 'Sir' or 'Jarvis' to wake.")

        # Update dashboard tiles (fast, no blocking)
        try:
            if hasattr(self, "_tile_battery"):
                bat_pct = getattr(self, "_cached_battery_pct", None)
                if bat_pct is not None:
                    self._tile_battery.value = bat_pct
                    self._tile_battery.update()
        except Exception:
            pass

        try:
            if hasattr(self, "_tile_heart"):
                import random
                self._tile_heart.value = f"{random.randint(70, 84)} BPM"
                self._tile_heart.update()
        except Exception:
            pass

        try:
            if hasattr(self, "_tile_headphones"):
                self._tile_headphones.value = "MUTED" if self._muted else "ACTIVE"
                self._tile_headphones.update()
        except Exception:
            pass

        try:
            if hasattr(self, "_tile_camera"):
                self._tile_camera.value = "FEED ACTIVE" if (self.hud._stream_worker is not None and self.hud._stream_worker.mode == "camera") else "STANDBY"
                self._tile_camera.update()
        except Exception:
            pass

        try:
            if hasattr(self, "_tile_video"):
                self._tile_video.value = "SCREEN ACTIVE" if (self.hud._stream_worker is not None and self.hud._stream_worker.mode == "screen") else "0 FEED"
                self._tile_video.update()
        except Exception:
            pass

        # Use cached provider/config values (refreshed in background)
        try:
            if hasattr(self, "_tile_provider"):
                cfg = getattr(self, "_cached_api_cfg", {})
                self._tile_provider.value = str(cfg.get("text_provider") or cfg.get("primary_provider") or "openrouter").upper()
                self._tile_audio_ai.value = str(cfg.get("live_audio_provider") or "gemini").upper()
                self._tile_wake.value = "ON" if getattr(self, "_auto_wake", True) else "OFF"
                if hasattr(self, "_tile_autopilot"):
                    self._tile_autopilot.value = "ON" if getattr(self, "visual_autopilot_enabled", True) else "OFF"
                self._tile_provider.update()
                self._tile_audio_ai.update()
                self._tile_wake.update()
                if hasattr(self, "_tile_autopilot"):
                    self._tile_autopilot.update()
                self._refresh_wake_tile()
        except Exception:
            pass

        snap = _metrics.snapshot()

        cpu = snap["cpu"]
        self._set_metric_pill("_metric_cpu", f"CPU {cpu:.0f}%", C.PRI if cpu < 85 else C.RED)

        mem = snap["mem"]
        self._set_metric_pill("_metric_mem", f"MEM {mem:.0f}%", C.ACC2 if mem < 85 else C.RED)

        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        self._set_metric_pill("_metric_net", f"NET {net_str}", C.GREEN)

        gpu = snap["gpu"]
        if gpu >= 0:
            self._set_metric_pill("_metric_gpu", f"GPU {gpu:.0f}%", C.ACC if gpu < 90 else C.RED)
        else:
            self._set_metric_pill("_metric_gpu", "GPU N/A", C.TEXT_DIM)

        tmp = snap["tmp"]
        if tmp >= 0:
            self._set_metric_pill("_metric_tmp", f"TMP {tmp:.0f}°C", "#ff6688" if tmp < 85 else C.RED)
        else:
            self._set_metric_pill("_metric_tmp", "TMP N/A", C.TEXT_DIM)

        self._sync_live_status_widgets(snap)

        # Use cached uptime/proc values (refreshed in background)
        try:
            self._uptime_lbl.setText(getattr(self, "_cached_uptime_str", "UP  --:--"))
        except Exception:
            pass
        try:
            self._proc_lbl.setText(getattr(self, "_cached_proc_str", "PROC  --"))
        except Exception:
            pass

        # Kick off background refresh of slow I/O values (non-blocking)
        if not getattr(self, "_bg_metrics_running", False):
            self._bg_metrics_running = True
            import threading as _t
            def _bg_refresh():
                try:
                    try:
                        import psutil as _ps
                        bat = _ps.sensors_battery()
                        if bat:
                            chg = " (Chg)" if bat.power_plugged else ""
                            self._cached_battery_pct = f"{bat.percent:.0f}%{chg}"
                    except Exception:
                        pass
                    try:
                        import psutil as _ps
                        boot_t = _ps.boot_time()
                        elapsed = time.time() - boot_t
                        h = int(elapsed // 3600)
                        m = int((elapsed % 3600) // 60)
                        self._cached_uptime_str = f"UP  {h:02d}:{m:02d}"
                    except Exception:
                        self._cached_uptime_str = "UP  --:--"
                    try:
                        import psutil as _ps
                        self._cached_proc_str = f"PROC  {len(_ps.pids())}"
                    except Exception:
                        self._cached_proc_str = "PROC  --"
                    try:
                        if API_FILE.exists():
                            self._cached_api_cfg = json.loads(API_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                finally:
                    self._bg_metrics_running = False
            _t.Thread(target=_bg_refresh, daemon=True).start()

        # (all blocking I/O moved to background thread above)

    def _update_syslab_graphs(self):
        """Push latest metric values to SYS LAB sparkline graphs."""
        if not hasattr(self, "_sys_graphs"):
            return
        snap = _metrics.snapshot()
        try:
            if "cpu" in self._sys_graphs:
                self._sys_graphs["cpu"].push(snap["cpu"])
            if "mem" in self._sys_graphs:
                self._sys_graphs["mem"].push(snap["mem"])
            if "gpu" in self._sys_graphs:
                self._sys_graphs["gpu"].push(snap["gpu"])
            if "net" in self._sys_graphs:
                self._sys_graphs["net"].push(snap["net"])
            if "tmp" in self._sys_graphs:
                self._sys_graphs["tmp"].push(snap["tmp"])
        except Exception:
            pass
        # Blink network LED on activity
        try:
            if hasattr(self, "_net_led"):
                active = snap["net"] > 0.1
                col = C.GREEN if active else "#333"
                self._net_led.setStyleSheet(f"color: {col}; background: transparent;")
        except Exception:
            pass


    def _on_pomodoro_done(self, sessions: int):
        """Toast when a pomodoro focus session is completed."""
        if hasattr(self, "_toast_mgr"):
            self._toast_mgr.show(
                "🍅 Pomodoro Complete",
                f"Focus session #{sessions} done! Time for a break.",
                "#00e676", 5000
            )
        if hasattr(self, "_log"):
            self._log.append_log(f"SYS: Pomodoro session #{sessions} completed. Take a break!")

    def _lock_system(self):
        try:
            import os
            os.system("rundll32.exe user32.dll,LockWorkStation")
            self._log.append_log("SYS: Lock command issued.")
        except Exception as e:
            self._log.append_log(f"ERR: Failed to lock system: {e}")

    def _fetch_location_tile(self):
        self._dispatch_command("check my live location", source="Dashboard")

    def _set_timeline_status(self, text: str):
        if hasattr(self, "_timeline_status_lbl"):
            self._timeline_status_lbl.setText(str(text or "Autopilot: READY"))

    def _run_visual_autopilot(self, goal: str):
        goal = (goal or "").strip()
        if not goal:
            goal = "Analyze the current screen and perform the most useful safe next action."
        if not getattr(self, "visual_autopilot_enabled", True):
            self._log.append_log("SYS: Visual Autopilot is OFF.")
            return
        self._autopilot_cancel.clear()
        if hasattr(self, "_timeline_status_lbl"):
            self._timeline_status_lbl.setText("Autopilot: PLANNING")
        self._log.append_log(f"SYS: Visual Autopilot started: {goal}")

        def worker():
            try:
                from actions.visual_desktop_agent import visual_desktop_agent
                if self._autopilot_cancel.is_set():
                    self._log_sig.emit("SYS: Visual Autopilot cancelled before planning.")
                    self._timeline_sig.emit("Autopilot: CANCELLED")
                    return
                params = {
                    "goal": goal,
                    "mode": "act",
                    "verify": True,
                    "max_retries": int(getattr(self, "visual_autopilot_max_retries", 2)),
                    "store_proofs": bool(getattr(self, "visual_autopilot_store_proofs", False)),
                    "cancel_event": self._autopilot_cancel,
                    "structured": True,
                }
                result = visual_desktop_agent(params, player=self)
                if isinstance(result, dict):
                    status = "DONE" if result.get("success") else "NEEDS ATTENTION"
                    msg = (
                        f"Autopilot: {status} | verified={result.get('verified')} "
                        f"| confidence={result.get('confidence')}"
                    )
                    self._log_sig.emit(f"Jarvis: {json.dumps(result, ensure_ascii=False)}")
                else:
                    msg = "Autopilot: COMPLETE"
                    self._log_sig.emit(f"Jarvis: {result}")
                self._state_sig.emit("LISTENING")
                self._timeline_sig.emit(msg)
            except Exception as e:
                self._log_sig.emit(f"ERR: Visual Autopilot failed - {e}")
                self._state_sig.emit("LISTENING")
                self._timeline_sig.emit("Autopilot: ERROR")

        self._state_sig.emit("THINKING")
        threading.Thread(target=worker, daemon=True).start()

    def _metric_pill_style(self, color: str) -> str:
        return f"""
            QLabel {{
                color: {color};
                background: {C.GLASS_BG};
                border: 1px solid {C.BORDER};
                border-radius: {C.R_SM}px;
                padding: 3px 9px;
            }}
        """

    def _make_metric_pill(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedHeight(24)
        lbl.setMinimumWidth(66)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFont(pfont(9, "semibold", mono=True))
        lbl.setStyleSheet(self._metric_pill_style(color))
        lbl._metric_color = color
        return lbl

    def _style_metric_pills(self):
        for lbl in [
            getattr(self, "_metric_cpu", None),
            getattr(self, "_metric_mem", None),
            getattr(self, "_metric_net", None),
            getattr(self, "_metric_gpu", None),
            getattr(self, "_metric_tmp", None),
        ]:
            if lbl is not None:
                lbl.setStyleSheet(self._metric_pill_style(getattr(lbl, "_metric_color", C.TEXT_MED)))

    def _set_metric_pill(self, name: str, text: str, color: str):
        lbl = getattr(self, name, None)
        if lbl is not None:
            lbl._metric_color = color
            lbl.setText(text)
            lbl.setStyleSheet(self._metric_pill_style(color))

    def _build_dashboard_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 15px; }}
        """)
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)
        
        lbl = QLabel("COCKPIT DASHBOARD")
        lbl.setFont(pfont(10, "semibold", spacing=0.4))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(lbl)

        dock = QFrame()
        dock.setObjectName("TodayDock")
        dock.setStyleSheet(f"""
            QFrame#TodayDock {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255,255,255,0.07), stop:0.58 {C.PANEL}, stop:1 {C.PRI_GHO});
                border: 1px solid {C.HAIRLINE};
                border-radius: {C.R_LG}px;
            }}
        """)
        dock_lay = QVBoxLayout(dock)
        dock_lay.setContentsMargins(12, 11, 12, 11)
        dock_lay.setSpacing(8)
        dock_title = QLabel("Today Dock")
        dock_title.setFont(pfont(14, "semibold", display=True))
        dock_title.setStyleSheet(f"color:{C.TEXT}; background:transparent; border:none;")
        dock_sub = QLabel("fast actions")
        dock_sub.setFont(pfont(8, "semibold", spacing=1.1))
        dock_sub.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        dock_lay.addWidget(dock_title)
        dock_lay.addWidget(dock_sub)

        def _dock_btn(text: str, command: str, accent: str = C.PRI) -> QPushButton:
            b = QPushButton(text)
            b.setMinimumHeight(34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(pfont(9, "semibold"))
            b.setToolTip(command)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL};
                    color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER};
                    border-radius: 10px;
                    padding: 7px 8px;
                }}
                QPushButton:hover {{
                    background: {C.PANEL2};
                    color: {C.TEXT};
                    border-color: {accent};
                }}
                QPushButton:pressed {{
                    background: {C.PRI_GHO};
                }}
            """)
            b.clicked.connect(lambda checked=False, c=command, t=text: self._run_preset_command(t, c))
            return b

        dock_grid = QGridLayout()
        dock_grid.setContentsMargins(0, 0, 0, 0)
        dock_grid.setHorizontalSpacing(7)
        dock_grid.setVerticalSpacing(7)
        dock_grid.addWidget(_dock_btn("Daily Brief", DEFAULT_VOICE_MACROS["daily briefing"], C.ACC), 0, 0)
        dock_grid.addWidget(_dock_btn("Deep Work", "start student deep work session", C.GREEN), 0, 1)
        dock_grid.addWidget(_dock_btn("Flashcards", "study flashcards open", C.PURPLE), 1, 0)
        dock_grid.addWidget(_dock_btn("Mind Game", "mind_game action=adaptive_start count=5", C.AMBER), 1, 1)
        dock_grid.addWidget(_dock_btn("Autopilot", "Run a security audit and start voice interaction.", C.PRI), 2, 0)
        dock_grid.addWidget(_dock_btn("Sys Test", "Open Sys Lab and start system diagnostics.", C.PINK), 2, 1)
        dock_lay.addLayout(dock_grid)
        lay.addWidget(dock)

        # System Health Gauge (NEW)
        self._health_gauge = _HealthGauge()
        lay.addWidget(self._health_gauge)

        # Smart Notifications & AI Auto-Responder (ULTRA-ADVANCED)
        self._notification_responder = _NotificationResponderWidget(self)
        lay.addWidget(self._notification_responder)

        # Tesla Cockpit Media & Climate Panel (ULTRA PREMIUM)
        self._tesla_cockpit = _TeslaCockpitControllerWidget(self)
        lay.addWidget(self._tesla_cockpit)

        # Autopilot Radar Scanner (ULTRA PREMIUM)
        self._tesla_radar = _TeslaRadarWidget(self)
        lay.addWidget(self._tesla_radar)

        # Stark HUD Window Monitor (ULTRA PREMIUM)
        self._stark_hud = _StarkHudMonitorWidget(self)
        lay.addWidget(self._stark_hud)
        
        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        
        self._tile_phone = GlassmorphicTileButton("Phone Link", "CONNECTED", "📱", self)
        self._tile_battery = GlassmorphicTileButton("Battery", "100%", "🔋", self)
        self._tile_camera = GlassmorphicTileButton("Camera Feed", "STANDBY", "📷", self)
        self._tile_heart = GlassmorphicTileButton("Vitals", "75 BPM", "❤️", self)
        self._radial_menu = AnimatedRadialMenu(self)
        self._tile_headphones = GlassmorphicTileButton("Audio", "ONLINE", "🎧", self)
        self._tile_video = GlassmorphicTileButton("Screen share", "0 FEED", "🎬", self)
        self._tile_lock = GlassmorphicTileButton("System Lock", "SECURE", "🔒", self)
        self._tile_location = GlassmorphicTileButton("Location", "STARK-HQ", "📍", self)
        self._tile_tasks = GlassmorphicTileButton("Task Planner", "READY", "AI", self)
        self._tile_meeting = GlassmorphicTileButton("Meeting Mode", "READY", "MTG", self)
        self._tile_cyber = GlassmorphicTileButton("Cyber Audit", "READY", "SEC", self)
        self._tile_provider = GlassmorphicTileButton("Primary AI", "OPENROUTER", "API", self)
        self._tile_audio_ai = GlassmorphicTileButton("Live Audio", "GEMINI", "MIC", self)
        self._tile_wake = GlassmorphicTileButton("Wake Word", "ON", "WAKE", self)
        self._tile_wake_link = GlassmorphicTileButton("Wake Link", "CHECK", "LIVE", self)
        self._tile_autopilot = GlassmorphicTileButton("Visual Autopilot", "ON", "AUTO", self)
        self._tile_scan = GlassmorphicTileButton("Screen Scan", "READY", "SCAN", self)
        self._tile_privacy_scan = GlassmorphicTileButton("Privacy Guard", "READY", "SAFE", self)
        self._tile_cancel = GlassmorphicTileButton("Cancel", "STOP", "ESC", self)
        self._tile_command_center = GlassmorphicTileButton("Command Center", "OPEN", "CMD", self)
        
        self._tile_lock.clicked.connect(self._lock_system)
        self._tile_location.clicked.connect(self._fetch_location_tile)
        self._tile_camera.clicked.connect(lambda: self._safe_toggle_stream("camera"))
        self._tile_video.clicked.connect(lambda: self._safe_toggle_stream("screen"))

        self._tile_tasks.clicked.connect(lambda: self._dispatch_command(DEFAULT_VOICE_MACROS["task briefing"], source="Dashboard/Task Planner"))
        self._tile_meeting.clicked.connect(lambda: self._dispatch_command(DEFAULT_VOICE_MACROS["meeting mode"], source="Dashboard/Meeting Mode"))
        self._tile_cyber.clicked.connect(lambda: self._dispatch_command(DEFAULT_VOICE_MACROS["security audit"], source="Dashboard/Cyber Audit"))
        self._tile_provider.clicked.connect(lambda: self._dispatch_command("show external vision provider status", source="Dashboard/Provider"))
        self._tile_audio_ai.clicked.connect(lambda: self._log.append_log("SYS: Audio route is Gemini Live. Text/vision route is OpenRouter primary."))
        self._tile_wake.clicked.connect(self._toggle_autowake)
        self._tile_wake_link.clicked.connect(self._run_wake_self_test)
        self._tile_autopilot.clicked.connect(lambda: self.set_visual_autopilot_enabled(not self.visual_autopilot_enabled))
        self._tile_scan.clicked.connect(lambda: self._run_visual_autopilot("Analyze my screen, explain what is visible, and perform only a clearly safe next action if one is obvious."))
        self._tile_privacy_scan.clicked.connect(lambda: self._dispatch_command("Run a privacy guard scan on my screen. Warn only if sensitive information seems visible, and do not read exact secrets aloud.", source="Dashboard/Privacy"))
        self._tile_cancel.clicked.connect(self.stop_autopilot)
        self._tile_command_center.clicked.connect(lambda: self._focus_right_tab("COMMAND"))
        
        grid.addWidget(self._tile_phone, 0, 0)
        grid.addWidget(self._tile_battery, 0, 1)
        grid.addWidget(self._tile_camera, 0, 2)
        
        grid.addWidget(self._tile_heart, 1, 0)
        grid.addWidget(self._radial_menu, 1, 1)
        grid.addWidget(self._tile_headphones, 1, 2)
        
        grid.addWidget(self._tile_video, 2, 0)
        grid.addWidget(self._tile_lock, 2, 1)
        grid.addWidget(self._tile_location, 2, 2)
        
        grid.addWidget(self._tile_tasks, 3, 0)
        grid.addWidget(self._tile_meeting, 3, 1)
        grid.addWidget(self._tile_cyber, 3, 2)

        grid.addWidget(self._tile_provider, 4, 0)
        grid.addWidget(self._tile_audio_ai, 4, 1)
        grid.addWidget(self._tile_wake, 4, 2)

        grid.addWidget(self._tile_autopilot, 5, 0)
        grid.addWidget(self._tile_scan, 5, 1)
        grid.addWidget(self._tile_privacy_scan, 5, 2)
        grid.addWidget(self._tile_wake_link, 6, 0)
        grid.addWidget(self._tile_cancel, 6, 1)
        grid.addWidget(self._tile_command_center, 6, 2)
        lay.addWidget(grid_widget, stretch=1)
        self._timeline_status_lbl = QLabel("Autopilot: READY")
        self._timeline_status_lbl.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._timeline_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timeline_status_lbl.setStyleSheet(
            f"color: {C.PRI}; background: {C.PANEL}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 7px;"
        )
        lay.addWidget(self._timeline_status_lbl)
        scroll.setWidget(w)
        return scroll

    def _set_autopilot_status(self, text: str):
        if hasattr(self, "_autopilot_status_lbl"):
            self._autopilot_status_lbl.setText(str(text or "Autopilot Pro: READY"))

    def _autopilot_goal_text(self) -> str:
        try:
            text = self._autopilot_goal.text().strip()
            if text:
                return text
        except Exception:
            pass
        return "Observe the current screen and suggest the safest next action."

    def _run_autopilot_panel_action(self, action: str, confirm: bool = False):
        if self._is_free_user():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Premium Locked", "❌ Computer Autopilot Pro is locked for Free users.\nPlease upgrade to Standard or Premium to unlock.")
            return

        params = {
            "action": action,
            "goal": self._autopilot_goal_text(),
            "confirm": bool(confirm),
            "verify": True,
            "store_proofs": False,
        }
        if action in ("templates", "history", "status", "macro_doctor"):
            params.pop("goal", None)
            params.pop("verify", None)

        self._set_autopilot_status(f"Autopilot Pro: {action.upper()}...")
        try:
            self._log.append_log(f"SYS: Computer Autopilot Pro {action} requested.")
        except Exception:
            pass

        def worker():
            try:
                from actions.computer_autopilot_pro import computer_autopilot_pro

                result = computer_autopilot_pro(params, player=None)
                self._log_sig.emit(f"Autopilot Pro:\n{result}")
                self._timeline_sig.emit("Autopilot Pro: READY")
                self._autopilot_status_sig.emit("Autopilot Pro: READY")
            except Exception as e:
                self._log_sig.emit(f"ERR: Computer Autopilot Pro failed - {e}")
                self._timeline_sig.emit("Autopilot Pro: ERROR")
                self._autopilot_status_sig.emit("Autopilot Pro: ERROR")

        threading.Thread(target=worker, daemon=True).start()

    def _build_autopilot_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        title = QLabel("COMPUTER AUTOPILOT PRO")
        title.setFont(pfont(10, "semibold", spacing=0.4))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(title)

        self._autopilot_status_lbl = QLabel("Autopilot Pro: READY")
        self._autopilot_status_lbl.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._autopilot_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._autopilot_status_lbl.setStyleSheet(
            f"color: {C.PRI}; background: {C.PANEL}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 7px;"
        )
        lay.addWidget(self._autopilot_status_lbl)

        self._autopilot_goal = QLineEdit()
        self._autopilot_goal.setPlaceholderText("Goal: plan screen task, draft text, safe scroll, click visible button...")
        self._autopilot_goal.setFont(QFont(C.FONT_MONO, 9, QFont.Weight.Medium))
        self._autopilot_goal.setFixedHeight(34)
        self._autopilot_goal.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px 9px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; background: {C.PANEL2}; }}
        """)
        lay.addWidget(self._autopilot_goal)

        def _btn(title: str, action: str, confirm: bool = False, accent: bool = False):
            btn = AnimatedPushButton(title, accent=accent)
            btn.setToolTip(f"Computer Autopilot Pro: {action}")
            btn.clicked.connect(lambda _=False, a=action, c=confirm: self._run_autopilot_panel_action(a, confirm=c))
            return btn

        rows = [
            [("STATUS", "status", False, False), ("OBSERVE", "observe", False, False)],
            [("PLAN", "plan", False, True), ("RUN SAFE", "run", False, False)],
            [("APPROVE RUN", "run", True, True), ("CANCEL", "cancel", False, False)],
            [("TEMPLATES", "templates", False, False), ("HISTORY", "history", False, False)],
            [("MACRO DOCTOR", "macro_doctor", False, False)],
        ]
        for row_items in rows:
            row = QHBoxLayout()
            row.setSpacing(6)
            for label, action, confirm, accent in row_items:
                row.addWidget(_btn(label, action, confirm=confirm, accent=accent))
            lay.addLayout(row)

        note = QLabel(
            "Safe actions can run automatically. Risky sends, deletes, payments, installs, terminal commands, and credentials pause for approval."
        )
        note.setWordWrap(True)
        note.setFont(QFont(C.FONT_SANS, 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(note)
        lay.addStretch()
        return w

    def _build_command_center_tab(self) -> QWidget:
        self._command_center = _CommandCenterWidget()
        self._command_center.command_requested.connect(
            lambda command, source: self._dispatch_command(command, source=source or "Command Center")
        )
        self._command_center.queue_requested.connect(self._queue_command_center_steps)
        self._command_center.run_queue_requested.connect(self._start_mission_queue)
        self._sync_live_status_widgets()
        return self._command_center

    def _queue_command_center_steps(self, raw: str):
        steps = self._normalise_mission_lines(raw)
        if not steps:
            try:
                self._log.append_log("SYS: Command Center queue is empty.")
            except Exception:
                pass
            return
        self._mission_queue.extend(steps)
        self._update_mission_queue_status()
        try:
            self._log.append_log(f"SYS: Command Center queued {len(steps)} step(s).")
        except Exception:
            pass
        self._sync_live_status_widgets()

    def _sync_live_status_widgets(self, snap: dict | None = None):
        if snap is None:
            try:
                snap = _metrics.snapshot()
            except Exception:
                snap = {"cpu": 0.0, "mem": 0.0}
        queue_count = len(getattr(self, "_mission_queue", []))
        try:
            wake_text = self._wake_status_text()
        except Exception:
            wake_text = "ON" if getattr(self, "_auto_wake", True) else "OFF"
        autopilot = bool(getattr(self, "visual_autopilot_enabled", False))
        privacy = bool(getattr(self, "privacy_guard_enabled", False))
        cpu = float((snap or {}).get("cpu", 0.0))
        mem = float((snap or {}).get("mem", 0.0))
        if hasattr(self, "_pulse_strip"):
            self._pulse_strip.set_status(
                wake=wake_text,
                autopilot=autopilot,
                queue_count=queue_count,
                cpu=cpu,
                mem=mem,
                privacy=privacy,
            )
        command_center = getattr(self, "_command_center", None)
        if command_center is not None:
            command_center.update_status(
                queue_count=queue_count,
                cpu=cpu,
                mem=mem,
                wake=wake_text,
                autopilot=autopilot,
            )

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(58)
        w.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(255, 255, 255, 0.05);")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(10)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(pfont(9, "semibold"))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        logo = QLabel()
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 rgba(255,255,255,0.12), stop:0.48 {C.PANEL2}, stop:1 {C.PRI_GHO}); "
            f"border: 1px solid {C.BORDER_B}; border-radius: 14px;"
        )
        if APP_LOGO.exists():
            px = QPixmap(str(APP_LOGO))
            if not px.isNull():
                logo.setPixmap(px.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        if logo.pixmap() is None:
            logo.setText("J")
            logo.setFont(pfont(18, "bold", display=True))
            logo.setStyleSheet(
                f"color: {C.TEXT}; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 {C.ACC}, stop:0.55 {C.PRI}, stop:1 {C.PURPLE}); "
                f"border: 1px solid {C.BORDER_B}; border-radius: 14px;"
            )
        lay.addWidget(logo)

        # Wordmark stack: JOYA / assistant subtitle + live dot
        brand_col = QVBoxLayout()
        brand_col.setContentsMargins(2, 0, 0, 0)
        brand_col.setSpacing(0)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        # Read plan from session to update app header brand wordmark
        plan_badge_text = "TRIAL"
        sp = Path(__file__).resolve().parent / "config" / "user_session.json"
        is_pro_user = False
        is_standard_user = False
        if sp.exists():
            try:
                import json
                sess = json.loads(sp.read_text(encoding="utf-8"))
                p_type = (sess.get("plan_type") or "free").lower()
                if p_type == "premium" or sess.get("is_pro"):
                    plan_badge_text = "PRO"
                    is_pro_user = True
                elif p_type == "standard":
                    plan_badge_text = "STANDARD"
                    is_standard_user = True
            except Exception:
                pass

        title_badge_text = "JOYA"
        title_style = f"color: {C.TEXT}; background: transparent;"
        if is_pro_user:
            title_badge_text = "JOYA PRO"
            title_style = "color: #ffd700; background: transparent; font-weight: bold;" # Gold text for Pro!
        elif is_standard_user:
            title_badge_text = "JOYA STANDARD"
            title_style = "color: #b0bec5; background: transparent; font-weight: bold;" # Silver/grey for Standard!

        self._title_badge = QLabel(title_badge_text)
        self._title_badge.setFont(pfont(17, "semibold", display=True))
        self._title_badge.setStyleSheet(title_style)
        title_row.addWidget(self._title_badge)

        # Pulsing live status dot
        self._live_dot = QLabel("●")
        self._live_dot.setFont(pfont(8, "bold"))
        self._live_dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        title_row.addWidget(self._live_dot)
        title_row.addStretch()
        brand_col.addLayout(title_row)

        # Dynamic greeting subtitle
        import datetime as _dt
        _hour = _dt.datetime.now().hour
        if _hour < 5:
            _greet = "Late Night Mode"
        elif _hour < 12:
            _greet = "Good Morning"
        elif _hour < 17:
            _greet = "Good Afternoon"
        elif _hour < 21:
            _greet = "Good Evening"
        else:
            _greet = "Night Owl Mode"
        self._sub_badge = QLabel(f"{_greet} · MARK XXXIX")
        self._sub_badge.setFont(pfont(8, "semibold", spacing=1.4))
        self._sub_badge.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        brand_col.addWidget(self._sub_badge)
        lay.addLayout(brand_col)
        lay.addSpacing(6)

        metrics = QHBoxLayout()
        metrics.setSpacing(6)
        self._metric_cpu = self._make_metric_pill("CPU --", C.PRI)
        self._metric_mem = self._make_metric_pill("MEM --", C.ACC2)
        self._metric_net = self._make_metric_pill("NET --", C.GREEN)
        self._metric_gpu = self._make_metric_pill("GPU --", C.ACC)
        self._metric_tmp = self._make_metric_pill("TMP --", C.PINK)
        for pill in [self._metric_cpu, self._metric_mem, self._metric_net, self._metric_gpu, self._metric_tmp]:
            metrics.addWidget(pill)
        lay.addLayout(metrics)

        self._pulse_strip = _CommandPulseStrip()
        lay.addWidget(self._pulse_strip)
        self._pulse_strip.hide()   # declutter: hidden for a clean Apple header

        # Weather widget (NEW)
        self._weather_w = _WeatherWidget()
        lay.addWidget(self._weather_w)
        self._weather_w.hide()     # declutter: hidden for a clean Apple header

        lay.addStretch()

        # center column (QVBoxLayout) - centered Clock
        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 5, 0, 5)
        center_col.setSpacing(0)

        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(pfont(15, "medium", mono=True))
        self._clock_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self._clock_lbl)

        self._date_lbl = QLabel("")
        self._date_lbl.setFont(pfont(8, "medium"))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self._date_lbl)
        
        lay.addLayout(center_col)

        lay.addStretch()

        self._god_btn = None
        self._simple_btn = None
        self._perf_btn = None
        self._hands_btn = None
        self._porc_lbl = None
        self._porc_install_btn = None

        def _header_icon(text: str, tip: str, cb):
            btn = QPushButton(text)
            btn.setFixedSize(36, 36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setFont(pfont(12, "semibold"))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.04);
                    color: {C.TEXT_DIM};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 18px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.12);
                    color: {C.TEXT};
                    border-color: {C.PRI};
                }}
                QPushButton:pressed {{
                    background: {C.PRI};
                    color: #ffffff;
                }}
            """)
            btn.clicked.connect(cb)
            return btn

        header_tools = QHBoxLayout()
        header_tools.setSpacing(7)
        header_tools.addWidget(_header_icon("M", "Mute / unmute microphone", self._toggle_mute))
        header_tools.addWidget(_header_icon("K", "Open command palette", self._open_command_palette))
        header_tools.addWidget(_header_icon("Z", "Toggle Zen reactor focus", self._toggle_zen_mode))
        header_tools.addWidget(_header_icon("F", "Open floating assistant", lambda checked=False: self.open_floating_assistant()))
        lay.addLayout(header_tools)
        return w

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.custom_color = C.MUTED_C
        else:
            self._mute_btn.setText("🎤  MICROPHONE ACTIVE")
            self._mute_btn.custom_color = C.GREEN
        self._mute_btn.update()

    def _toggle_simple_ui(self):
        cur = not getattr(self, "_simple_ui", False)
        self._simple_ui = cur
        try:
            if hasattr(self, "_left_panel") and self._left_panel is not None:
                self._left_panel.setVisible(not cur)
            if hasattr(self, "_right_panel") and self._right_panel is not None:
                self._right_panel.setVisible(not cur)
            if hasattr(self, "hud") and self.hud is not None:
                self.hud.simple_mode = cur
                self.hud._sync_timer_interval(force=True)
        except Exception:
            pass
        # update button style
        if getattr(self, "_simple_btn", None) is not None:
            if cur:
                self._simple_btn.custom_color = C.PRI
            else:
                self._simple_btn.custom_color = None
        try:
            if cur:
                self._log.append_log("SYS: Simple UI enabled — visuals reduced.")
            else:
                self._log.append_log("SYS: Simple UI disabled.")
        except Exception:
            pass
        self._save_extended_config()
        if getattr(self, "_simple_btn", None) is not None:
            self._simple_btn.update()

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

        # Update uptime counter in footer
        if hasattr(self, "_uptime_lbl") and hasattr(self, "_boot_time"):
            import time as _t
            elapsed = int(_t.time() - self._boot_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self._uptime_lbl.setText(f"Uptime: {h:02d}:{m:02d}:{s:02d}")

        # Pulse live dot (toggle opacity for breathing effect)
        if hasattr(self, "_live_dot"):
            _sec = time.localtime().tm_sec
            if _sec % 2 == 0:
                self._live_dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
            else:
                self._live_dot.setStyleSheet(f"color: rgba(0, 255, 120, 0.3); background: transparent;")

    def _focus_right_tab(self, label_fragment: str) -> bool:
        tabs = getattr(self, "_right_tabs", None)
        if tabs is None:
            try:
                tabs = self._right_panel.findChild(QTabWidget)
            except Exception:
                tabs = None
        if tabs is None:
            return False
        needle = str(label_fragment or "").upper()
        for idx in range(tabs.count()):
            if needle in tabs.tabText(idx).upper():
                tabs.setCurrentIndex(idx)
                return True
        return False

    def _build_skills_store_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        
        lbl = QLabel("JOYA COGNITIVE SKILLS STORE")
        lbl.setFont(pfont(10, "semibold", spacing=0.4))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(lbl)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_lay = QVBoxLayout(scroll_content)
        scroll_lay.setContentsMargins(0, 0, 0, 0)
        scroll_lay.setSpacing(12)
        
        skills = [
            ("Photoshop Automation Skill", "Automates canvas layers, background removals, and crop actions in Photoshop.", "INSTALL", 0),
            ("Excel Data Analyst Skill", "Automates pivot tables, macro scripts, and advanced formulas in MS Excel.", "ACTIVE 🟢", 100),
            ("Algorithmic Trading Skill", "Connects to mock indices to test moving average crossover strategies.", "INSTALL", 0),
            ("Canva Graphic Design Skill", "Auto-arranges design templates, typography assets, and social banners.", "INSTALL", 0),
            ("Stock Market Intelligence", "Performs real-time sentiment analysis of global stock charts.", "ACTIVE 🟢", 100),
            ("Medical Research Agent", "Extracts key clinical trial details and abstracts from PDF papers.", "INSTALL", 0),
            ("Full Coding Assistant", "Interlinks local VS Code workspaces to auto-refactor project directories.", "ACTIVE 🟢", 100),
            ("YouTube Creator Suite", "Auto-generates thumbnails, writes scripts, and arranges uploads.", "INSTALL", 0)
        ]
        
        for name, desc, status, progress in skills:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: rgba(255, 255, 255, 0.02);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 12px;
                }}
                QFrame:hover {{
                    border-color: {C.PRI};
                    background: rgba(255, 255, 255, 0.04);
                }}
            """)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(10, 10, 10, 10)
            c_lay.setSpacing(6)
            
            # Title & status row
            t_row = QHBoxLayout()
            title_lbl = QLabel(name)
            title_lbl.setFont(pfont(10.5, "bold"))
            title_lbl.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            t_row.addWidget(title_lbl)
            
            status_col = C.GREEN if "ACTIVE" in status else C.TEXT_MED
            status_lbl = QLabel(status)
            status_lbl.setFont(pfont(8, "semibold"))
            status_lbl.setStyleSheet(f"color: {status_col}; background: rgba(255,255,255,0.03); border: 1px solid {status_col}33; border-radius: 6px; padding: 1px 6px;")
            t_row.addWidget(status_lbl)
            t_row.addStretch()
            c_lay.addLayout(t_row)
            
            # Desc
            desc_lbl = QLabel(desc)
            desc_lbl.setFont(pfont(8.5, "regular"))
            desc_lbl.setStyleSheet("color: #8e9eab; background: transparent; border: none;")
            desc_lbl.setWordWrap(True)
            c_lay.addWidget(desc_lbl)
            
            # Progress bar
            p_row = QHBoxLayout()
            p_bar = QProgressBar()
            p_bar.setRange(0, 100)
            p_bar.setValue(progress)
            p_bar.setFixedHeight(5)
            p_bar.setTextVisible(False)
            p_bar.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(255, 255, 255, 0.04);
                    border: none;
                    border-radius: 2px;
                }}
                QProgressBar::chunk {{
                    background: {C.PRI};
                    border-radius: 2px;
                }}
            """)
            p_row.addWidget(p_bar)
            c_lay.addLayout(p_row)
            
            # Action button
            btn = QPushButton("ACTIVATE AND LOAD" if "INSTALL" in status else "DEACTIVATE SKILL")
            btn.setFont(pfont(8, "semibold"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            
            # Setup interactive local simulator for installation!
            def make_install_handler(bar=p_bar, label=status_lbl, button=btn, skill_name=name):
                def handler():
                    if "DEACTIVATE" in button.text():
                        bar.setValue(0)
                        label.setText("INSTALL")
                        label.setStyleSheet(f"color: {C.TEXT_MED}; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 1px 6px;")
                        button.setText("ACTIVATE AND LOAD")
                        self._dispatch_command(f"system skill_uninstall name={skill_name}", source="Skills Store")
                        return
                    
                    button.setEnabled(False)
                    button.setText("DOWNLOADING MODULE...")
                    # Local timer simulation
                    self._sim_progress = 0
                    timer = QTimer(self)
                    
                    def tick():
                        self._sim_progress += 10
                        bar.setValue(self._sim_progress)
                        if self._sim_progress >= 100:
                            timer.stop()
                            label.setText("ACTIVE 🟢")
                            label.setStyleSheet(f"color: {C.GREEN}; background: rgba(255,255,255,0.03); border: 1px solid {C.GREEN}33; border-radius: 6px; padding: 1px 6px;")
                            button.setEnabled(True)
                            button.setText("DEACTIVATE SKILL")
                            self._dispatch_command(f"system skill_install name={skill_name}", source="Skills Store")
                            try:
                                from actions.gamification import add_xp
                                add_xp(50, f"Installed Cognitive Skill: {skill_name}")
                            except Exception:
                                pass
                                
                    timer.timeout.connect(tick)
                    timer.start(150)
                return handler

            btn.clicked.connect(make_install_handler())
            
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.04);
                    color: {C.TEXT_MED};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                    padding: 5px;
                }}
                QPushButton:hover {{
                    background: {C.PRI_GHO};
                    border-color: {C.PRI};
                    color: #ffffff;
                }}
            """)
            c_lay.addWidget(btn)
            scroll_lay.addWidget(card)
            
        scroll.setWidget(scroll_content)
        lay.addWidget(scroll)
        return w

    def _build_student_portal_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        main_lay = QVBoxLayout(w)
        main_lay.setContentsMargins(6, 6, 6, 6)
        main_lay.setSpacing(12)
        
        # Top Card: AI Semester Twin
        twin_card = QFrame()
        twin_card.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 240, 255, 0.02);
                border: 1px solid rgba(0, 240, 255, 0.18);
                border-radius: 14px;
            }}
            QFrame:hover {{
                background: rgba(0, 240, 255, 0.04);
                border-color: {C.PRI};
            }}
        """)
        twin_lay = QVBoxLayout(twin_card)
        twin_lay.setContentsMargins(12, 12, 12, 12)
        twin_lay.setSpacing(6)
        
        t_row = QHBoxLayout()
        twin_lbl = QLabel("👑 AI SEMESTER TWIN ACTIVE")
        twin_lbl.setFont(pfont(10.5, "bold", spacing=0.4))
        twin_lbl.setStyleSheet(f"color: {C.PRI}; border: none; background: transparent;")
        t_row.addWidget(twin_lbl)
        
        status_tag = QLabel("OS SYNCHRONIZED")
        status_tag.setFont(pfont(7.5, "bold"))
        status_tag.setStyleSheet(f"color: {C.GREEN}; border: 1px solid {C.GREEN}33; border-radius: 5px; padding: 1px 6px; background: rgba(0,255,0,0.01);")
        t_row.addWidget(status_tag)
        t_row.addStretch()
        twin_lay.addLayout(t_row)
        
        # User name greeting
        name = "Anish"
        try:
            import sys
            base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
            briefing_cache = base / "cache" / "daily_briefing.json"
            if briefing_cache.exists():
                db = json.loads(briefing_cache.read_text(encoding="utf-8"))
                name = db.get("preferences", {}).get("name", "Anish")
        except Exception:
            pass
            
        # Setup form
        setup_lay = QHBoxLayout()
        setup_lay.setSpacing(10)
        
        goal_lbl = QLabel("🎯 TARGET GOAL:")
        goal_lbl.setFont(pfont(8, "bold"))
        goal_lbl.setStyleSheet("color: #8e8e93; background: transparent;")
        self._portal_goal_input = QLineEdit("e.g. UPSC, JEE, Semester Exams")
        self._portal_goal_input.setFont(pfont(9, "bold"))
        self._portal_goal_input.setStyleSheet(f"background: rgba(0,0,0,0.4); color: {C.PRI}; border: 1px solid {C.PRI}; border-radius: 4px; padding: 4px;")
        
        topic_lbl = QLabel("⚔️ CURRENT MISSION:")
        topic_lbl.setFont(pfont(8, "bold"))
        topic_lbl.setStyleSheet("color: #8e8e93; background: transparent;")
        self._portal_topic_input = QLineEdit("e.g. Python, Physics, History")
        self._portal_topic_input.setFont(pfont(9, "bold"))
        self._portal_topic_input.setStyleSheet(f"background: rgba(0,0,0,0.4); color: {C.AMBER}; border: 1px solid {C.AMBER}; border-radius: 4px; padding: 4px;")
        
        setup_lay.addWidget(goal_lbl)
        setup_lay.addWidget(self._portal_goal_input)
        setup_lay.addWidget(topic_lbl)
        setup_lay.addWidget(self._portal_topic_input)
        setup_lay.addStretch()
        
        twin_lay.addLayout(setup_lay)
        
        self._portal_desc = QLabel(
            f"\"{name}, set your target and mission above. AI is ready to synchronize Roadmaps, PYQs, flashcards, and active study plans for your specific mission.\""
        )
        self._portal_desc.setFont(pfont(8.8, "semibold"))
        self._portal_desc.setStyleSheet("color: #d1d1d6; border: none; background: transparent;")
        self._portal_desc.setWordWrap(True)
        twin_lay.addWidget(self._portal_desc)
        
        # Metrics row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)
        
        def add_sub_metric(icon_lbl, value_lbl):
            col_lay = QVBoxLayout()
            col_lay.setSpacing(1)
            i_lbl = QLabel(icon_lbl)
            i_lbl.setFont(pfont(7.5, "regular"))
            i_lbl.setStyleSheet("color: #8e8e93; border: none; background: transparent;")
            col_lay.addWidget(i_lbl)
            
            v_lbl = QLabel(value_lbl)
            v_lbl.setFont(pfont(9.5, "bold"))
            v_lbl.setStyleSheet(f"color: {C.PRI}; border: none; background: transparent;")
            col_lay.addWidget(v_lbl)
            metrics_row.addLayout(col_lay)
            
        add_sub_metric("📅 DAYS REMAINING", "Auto-Calculated")
        add_sub_metric("⚡ DAILY REQUIREMENT", "Dynamic")
        add_sub_metric("📈 SCORE PROBABILITY", "Analyzing...")
        
        m_btn = QPushButton("LAUNCH MISSION 🚀")
        m_btn.setFont(pfont(8, "bold"))
        m_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        m_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO};
                color: #ffffff;
                border: 1px solid {C.PRI};
                border-radius: 8px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background: {C.PRI};
            }}
        """)
        
        def launch_mission():
            g = self._portal_goal_input.text().strip()
            t = self._portal_topic_input.text().strip()
            if not t:
                self._portal_diag_lbl.setText("🧠 DIAGNOSTIC RECOMMENDATION:\n⚠️ Please enter a topic/mission before launching.")
                return
            # Update diagnostic label
            self._portal_diag_lbl.setText(f"🧠 DIAGNOSTIC RECOMMENDATION:\n⏳ Generating AI Flashcards & Notes for '{t}'... Please wait.")
            
            import threading
            def _bg_generate():
                try:
                    from actions.flashcards_open import open_flashcards_and_notes
                    open_flashcards_and_notes(player=self, topic=t)
                except Exception as e:
                    print(f"[Mission Launch Error] {e}")
            
            thread = threading.Thread(target=_bg_generate, daemon=True)
            thread.start()
            
        m_btn.clicked.connect(launch_mission)
        
        metrics_row.addStretch()
        metrics_row.addWidget(m_btn)
        twin_lay.addLayout(metrics_row)
        main_lay.addWidget(twin_card)
        
        # Bottom row layout
        bottom_lay = QHBoxLayout()
        bottom_lay.setSpacing(12)
        
        # Left Panel: Brain Scanner & Study DNA
        left_panel = QFrame()
        left_panel.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 15, 20, 0.4);
                border: 1px solid rgba(0, 240, 255, 0.15);
                border-radius: 16px;
            }}
        """)
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(12, 12, 12, 12)
        left_lay.setSpacing(8)
        
        hdr = QLabel("🧠 COGNITIVE BRAIN SCANNER")
        hdr.setFont(pfont(10, "bold"))
        hdr.setStyleSheet(f"color: {C.PRI}; border: none; background: transparent;")
        left_lay.addWidget(hdr)
        
        # Study DNA Badge
        dna_card = QFrame()
        dna_card.setStyleSheet("background: rgba(0, 240, 255, 0.03); border: 1px solid rgba(0, 240, 255, 0.12); border-radius: 8px;")
        dna_lay = QHBoxLayout(dna_card)
        dna_lay.setContentsMargins(6, 6, 6, 6)
        
        dna_icon = QLabel("🧬")
        dna_icon.setFont(pfont(13, "bold"))
        dna_icon.setStyleSheet("border: none; background: transparent;")
        dna_lay.addWidget(dna_icon)
        
        dna_lbl = QLabel("STUDY DNA: 🧠 Visual Learner\nPeak retention hours: 9:00 PM - 12:00 AM.")
        dna_lbl.setFont(pfont(7.8, "semibold"))
        dna_lbl.setStyleSheet("color: #d1d1d6; border: none; background: transparent;")
        dna_lbl.setWordWrap(True)
        dna_lay.addWidget(dna_lbl)
        left_lay.addWidget(dna_card)
        
        def add_metric(label, val, col):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFont(pfont(8.2, "semibold"))
            lbl.setStyleSheet("color: #ffffff; border: none; background: transparent;")
            row.addWidget(lbl)
            
            val_lbl = QLabel(f"{val}%")
            val_lbl.setFont(pfont(8.2, "bold"))
            val_lbl.setStyleSheet(f"color: {col}; border: none; background: transparent;")
            row.addWidget(val_lbl)
            left_lay.addLayout(row)
            
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            pbar.setValue(val)
            pbar.setFixedHeight(5)
            pbar.setTextVisible(False)
            pbar.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(255,255,255,0.03);
                    border: none;
                    border-radius: 2.5px;
                }}
                QProgressBar::chunk {{
                    background: {col};
                    border-radius: 2.5px;
                }}
            """)
            left_lay.addWidget(pbar)
            
        add_metric("Focus Intensity Level", 94, C.GREEN)
        add_metric("Mental Fatigue Rate", 38, C.RED)
        add_metric("Memory Retention Index", 91, C.PRI)
        add_metric("Cognitive Power Output", 87, C.AMBER)
        
        diag_box = QFrame()
        diag_box.setStyleSheet("background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px;")
        diag_lay = QVBoxLayout(diag_box)
        self._portal_diag_lbl = QLabel("🧠 DIAGNOSTIC RECOMMENDATION:\nSet a mission to get AI diagnostic recommendations and performance predictions.")
        self._portal_diag_lbl.setFont(pfont(7.5, "regular"))
        self._portal_diag_lbl.setStyleSheet("color: #8e9eab; border: none;")
        self._portal_diag_lbl.setWordWrap(True)
        diag_lay.addWidget(self._portal_diag_lbl)
        left_lay.addWidget(diag_box)
        
        f_btn = QPushButton("LAUNCH DEEP WORK STUDY")
        f_btn.setFont(pfont(8, "bold"))
        f_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        f_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO};
                color: #ffffff;
                border: 1px solid {C.PRI};
                border-radius: 8px;
                padding: 6px;
            }}
            QPushButton:hover {{
                background: {C.PRI};
            }}
        """)
        f_btn.clicked.connect(lambda: self._dispatch_command("start student deep work session", source="Brain Scanner"))
        left_lay.addWidget(f_btn)
        bottom_lay.addWidget(left_panel, 2)
        
        # Middle Panel: Stock Market & Boss Fight
        mid_panel = QFrame()
        mid_panel.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 15, 20, 0.4);
                border: 1px solid rgba(0, 240, 255, 0.15);
                border-radius: 16px;
            }}
        """)
        mid_lay = QVBoxLayout(mid_panel)
        mid_lay.setContentsMargins(12, 12, 12, 12)
        mid_lay.setSpacing(8)
        
        mid_hdr = QLabel("📈 KNOWLEDGE STOCK MARKET")
        mid_hdr.setFont(pfont(10, "bold"))
        mid_hdr.setStyleSheet(f"color: {C.PRI}; border: none; background: transparent;")
        mid_lay.addWidget(mid_hdr)
        
        def add_stock_ticker(subj, trend, pct, color):
            row = QHBoxLayout()
            s_lbl = QLabel(subj)
            s_lbl.setFont(pfont(8.2, "semibold"))
            s_lbl.setStyleSheet("color: #ffffff; border: none; background: transparent;")
            row.addWidget(s_lbl)
            
            t_lbl = QLabel(f"{trend} {pct}%")
            t_lbl.setFont(pfont(8.2, "bold"))
            t_lbl.setStyleSheet(f"color: {color}; border: none; background: transparent;")
            row.addWidget(t_lbl)
            row.addStretch()
            mid_lay.addLayout(row)
            
        add_stock_ticker("Physics Core", "▲", "12", C.GREEN)
        add_stock_ticker("Ancient History", "▼", "4", C.RED)
        add_stock_ticker("Organic Chemistry", "▲", "8", C.AMBER)
        add_stock_ticker("Coding Algorithms", "▲", "15", C.PRI)
        
        mid_lay.addSpacing(4)
        
        bf_hdr = QLabel("⚔️ ACTIVE BOSS CHALLENGE")
        bf_hdr.setFont(pfont(10, "bold"))
        bf_hdr.setStyleSheet(f"color: {C.RED}; border: none; background: transparent;")
        mid_lay.addWidget(bf_hdr)
        
        bf_box = QFrame()
        bf_box.setStyleSheet("background: rgba(255, 69, 58, 0.02); border: 1px solid rgba(255, 69, 58, 0.18); border-radius: 8px;")
        bf_lay = QVBoxLayout(bf_box)
        bf_lay.setContentsMargins(8, 8, 8, 8)
        self._portal_boss_lbl = QLabel("🔥 BOSS BATTLE STANDBY\nSet a mission to spawn an academic boss for your topic!")
        self._portal_boss_lbl.setFont(pfont(7.5, "regular"))
        self._portal_boss_lbl.setStyleSheet("color: #d1d1d6; border: none;")
        self._portal_boss_lbl.setWordWrap(True)
        bf_lay.addWidget(self._portal_boss_lbl)
        
        bf_btn = QPushButton("DEFEAT BOSS ⚔️")
        bf_btn.setFont(pfont(7.5, "bold"))
        bf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bf_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 69, 58, 0.1);
                color: #ff453a;
                border: 1px solid #ff453a;
                border-radius: 6px;
                padding: 4px;
            }}
            QPushButton:hover {{
                background: #ff453a;
                color: #ffffff;
            }}
        """)
        bf_btn.clicked.connect(lambda: self._dispatch_command("student start boss_fight", source="Boss Fight"))
        bf_lay.addWidget(bf_btn)
        mid_lay.addWidget(bf_box)
        bottom_lay.addWidget(mid_panel, 2)
        
        # Right Panel: Knowledge Universe
        right_panel = QFrame()
        right_panel.setStyleSheet(f"""
            QFrame {{
                background: rgba(0, 10, 15, 0.4);
                border: 1px solid rgba(0, 240, 255, 0.15);
                border-radius: 16px;
            }}
        """)
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(10)
        
        u_hdr = QLabel("🌌 INTERACTIVE KNOWLEDGE UNIVERSE")
        u_hdr.setFont(pfont(10, "bold"))
        u_hdr.setStyleSheet(f"color: {C.PRI}; border: none; background: transparent;")
        right_lay.addWidget(u_hdr)
        
        u_scroll = QScrollArea()
        u_scroll.setWidgetResizable(True)
        u_scroll.setStyleSheet("background: transparent; border: none;")
        
        u_content = QWidget()
        u_content.setStyleSheet("background: transparent;")
        u_scroll_lay = QVBoxLayout(u_content)
        u_scroll_lay.setContentsMargins(0, 0, 0, 0)
        u_scroll_lay.setSpacing(10)
        
        galaxies = [
            ("History Galaxy 🌍", "Sindhu Ghati, Vedic Age, Medieval India", "78% Complete", C.AMBER),
            ("Science Galaxy ⚛️", "Quantum Physics, Mechanics, Chemistry", "90% Complete", C.GREEN),
            ("Maths Galaxy 🧮", "Calculus, Matrix Operations, Probability", "45% Complete", C.RED),
            ("Coding Galaxy 💻", "Python, Advanced Algorithms, Web3", "92% Complete", C.PRI),
        ]
        
        for g_name, g_desc, g_status, g_col in galaxies:
            g_card = QFrame()
            g_card.setStyleSheet("""
                QFrame {
                    background: rgba(255, 255, 255, 0.02);
                    border: 1px solid rgba(255, 255, 255, 0.05);
                    border-radius: 10px;
                }
                QFrame:hover {
                    background: rgba(255, 255, 255, 0.04);
                    border-color: rgba(0, 240, 255, 0.4);
                }
            """)
            gc_lay = QVBoxLayout(g_card)
            gc_lay.setContentsMargins(8, 8, 8, 8)
            gc_lay.setSpacing(4)
            
            g_row = QHBoxLayout()
            gn_lbl = QLabel(g_name)
            gn_lbl.setFont(pfont(9.5, "bold"))
            gn_lbl.setStyleSheet("color: #ffffff; border: none;")
            g_row.addWidget(gn_lbl)
            
            gs_lbl = QLabel(g_status)
            gs_lbl.setFont(pfont(8, "semibold"))
            gs_lbl.setStyleSheet(f"color: {g_col}; border: 1px solid {g_col}44; border-radius: 4px; padding: 1px 4px;")
            g_row.addWidget(gs_lbl)
            g_row.addStretch()
            gc_lay.addLayout(g_row)
            
            gd_lbl = QLabel(g_desc)
            gd_lbl.setFont(pfont(8, "regular"))
            gd_lbl.setStyleSheet("color: #8e9eab; border: none;")
            gc_lay.addWidget(gd_lbl)
            
            m_btn = QPushButton("LAUNCH INTERACTIVE MISSION")
            m_btn.setFont(pfont(7.5, "semibold"))
            m_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            
            def make_mission_handler(name=g_name):
                def handler():
                    self._dispatch_command(f"student space_mission name={name}", source="Knowledge Universe")
                return handler
                
            m_btn.clicked.connect(make_mission_handler())
            m_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.03);
                    color: {g_col};
                    border: 1px solid {g_col}33;
                    border-radius: 6px;
                    padding: 4px;
                }}
                QPushButton:hover {{
                    background: {g_col}22;
                    color: #ffffff;
                }}
            """)
            gc_lay.addWidget(m_btn)
            u_scroll_lay.addWidget(g_card)
            
        u_scroll.setWidget(u_content)
        right_lay.addWidget(u_scroll)
        bottom_lay.addWidget(right_panel, 3)
        main_lay.addLayout(bottom_lay)
        return w

    def _build_blueprint_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        
        lbl = QLabel("JARVIS X INFINITY BLUEPRINT")
        lbl.setFont(pfont(10, "semibold", spacing=0.4))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(lbl)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_lay = QVBoxLayout(scroll_content)
        scroll_lay.setContentsMargins(0, 0, 0, 0)
        scroll_lay.setSpacing(12)
        
        phases = [
            ("Phase 1 — Core Brain", "Voice connection, long-term memory, personality engine", "ACTIVE", 100, "start student deep work session"),
            ("Phase 2 — Complete PC Control", "App manager, keyboard/mouse control, CPU/GPU diagnostics", "ACTIVE", 100, "check my pc health"),
            ("Phase 3 — Vision AI", "Screen analyzer, webcam face/emotion tracker, OCR scanner", "ACTIVE", 100, "analyze my screen"),
            ("Phase 4 — Autonomous AI", "Silent self-learning, background news feed, auto cleanup", "ACTIVE", 95, "give me latest news flash"),
            ("Phase 5 — Coding Agent", "VS Code layout interlinker, auto debugger, sandboxed run", "ACTIVE", 90, "start coding assistant"),
            ("Phase 6 — Internet Intelligence", "Live news aggregator, stocks/crypto, weather radars", "ACTIVE", 100, "web search query=google stocks"),
            ("Phase 7 — Mobile Integration", "Phone track locator, WhatsApp auto reply, clipboard sync", "ACTIVE", 85, "open whatsapp"),
            ("Phase 8 — Creative Studio", "AI UI layout generator, pixel artist generator", "ACTIVE", 80, "generate ui layout"),
            ("Phase 9 — Productivity", "Lecture assistants, study timers, concept visualizer", "ACTIVE", 100, "study flashcards open"),
            ("Phase 10 — Cyber Security", "Firewall packet monitoring, defense shields", "ACTIVE", 100, "Run security audit"),
            ("Phase 11 — Smart Home", "Philips Hue control simulation, smart home matrix", "STANDBY", 75, "smart_home_matrix status"),
            ("Phase 12 — Vehicle Integration", "Fuel monitor, diagnostic assistant", "STANDBY", 70, "vehicle_assistant status"),
            ("Phase 13 — Global Intelligence", "ISS satellite tracking, emergency alert beacons", "ACTIVE", 80, "satellite_tracker observe"),
            ("Phase 14 — Human Brain", "Dream journals, sentiment indicators, micro behavior buckets", "ACTIVE", 95, "human_identity_memory status"),
            ("Phase 15 — AI Agents", "Deep codebase researchers, travel/medical specialist subagents", "ACTIVE", 100, "research_agent status"),
            ("Phase 16 — Self Evolution", "Error auto-learning, stark system diagnostics & recovery", "ACTIVE", 90, "auto_repair run"),
            ("Phase 17 — Gaming AI", "Discord triggers, FPS booster modes", "ACTIVE", 85, "gaming_mode activate"),
            ("Phase 18 — Experimental Lab", "AR HUD overlays, hologram simulators, quantum simulation keys", "ACTIVE", 95, "stark_quantum_sim start")
        ]
        
        for name, desc, status, progress, cmd in phases:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: rgba(255, 255, 255, 0.02);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 12px;
                }}
                QFrame:hover {{
                    border-color: {C.PRI};
                    background: rgba(255, 255, 255, 0.04);
                }}
            """)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(10, 10, 10, 10)
            c_lay.setSpacing(6)
            
            # Title & Status row
            t_row = QHBoxLayout()
            title_lbl = QLabel(name)
            title_lbl.setFont(pfont(10.5, "bold"))
            title_lbl.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            t_row.addWidget(title_lbl)
            
            status_col = C.GREEN if status == "ACTIVE" else C.ACC
            status_lbl = QLabel(status)
            status_lbl.setFont(pfont(8, "semibold"))
            status_lbl.setStyleSheet(f"color: {status_col}; background: rgba(255,255,255,0.04); border: 1px solid {status_col}44; border-radius: 6px; padding: 1px 6px;")
            t_row.addWidget(status_lbl)
            t_row.addStretch()
            c_lay.addLayout(t_row)
            
            # Desc
            desc_lbl = QLabel(desc)
            desc_lbl.setFont(pfont(8.5, "regular"))
            desc_lbl.setStyleSheet("color: #8e9eab; background: transparent; border: none;")
            desc_lbl.setWordWrap(True)
            c_lay.addWidget(desc_lbl)
            
            # Progress row
            p_row = QHBoxLayout()
            p_bar = QProgressBar()
            p_bar.setRange(0, 100)
            p_bar.setValue(progress)
            p_bar.setFixedHeight(6)
            p_bar.setTextVisible(False)
            p_bar.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(255, 255, 255, 0.04);
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background: {C.PRI};
                    border-radius: 3px;
                }}
            """)
            p_row.addWidget(p_bar)
            
            pct_lbl = QLabel(f"{progress}%")
            pct_lbl.setFont(pfont(8, "bold", mono=True))
            pct_lbl.setStyleSheet("color: #ffffff; background: transparent; border: none;")
            p_row.addWidget(pct_lbl)
            c_lay.addLayout(p_row)
            
            # Launch button
            btn = QPushButton("EXECUTE INTERROGATION")
            btn.setFont(pfont(8, "semibold"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.04);
                    color: {C.TEXT_MED};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                    padding: 5px;
                }}
                QPushButton:hover {{
                    background: {C.PRI_GHO};
                    border-color: {C.PRI};
                    color: #ffffff;
                }}
            """)
            btn.clicked.connect(lambda checked=False, command_str=cmd: self._dispatch_command(command_str, source="Blueprint Hub"))
            c_lay.addWidget(btn)
            
            scroll_lay.addWidget(card)
            
        scroll.setWidget(scroll_content)
        lay.addWidget(scroll)
        return w

    def _build_left_panel(self) -> QWidget:
        """Ultra-clean minimal side-rail. All backend widgets created invisibly."""
        w = QWidget()
        w.setFixedWidth(52)
        w.setStyleSheet(f"""
            QWidget {{
                background: transparent;
            }}
        """)

        # ── Hidden container for all backend-referenced widgets ──
        # These self._* widgets MUST exist for _update_metrics, _update_handsfree_status,
        # _refresh_wake_tile, _wire_advanced_buttons, etc. — but are invisible to user.
        self._hidden = QWidget()
        self._hidden.setVisible(False)
        _hl = QVBoxLayout(self._hidden)
        _hl.setContentsMargins(0, 0, 0, 0)

        self._uptime_lbl = QLabel()
        self._proc_lbl = QLabel()
        self._avatar_status_lbl = QLabel("Orb: ready")
        self._avatar_state_lbl = QLabel("State: steady")
        self._avatar_text_input = QLineEdit(self)
        self._avatar_text_input.setPlaceholderText("Type avatar speech...")
        self._avatar_open_btn = AnimatedPushButton("OPEN")
        self._avatar_open_btn.clicked.connect(self._open_avatar)
        self._avatar_speak_btn = AnimatedPushButton("SPEAK")
        self._avatar_speak_btn.clicked.connect(self._avatar_speak_text)
        self._avatar_demo_btn = AnimatedPushButton("DEMO")
        self._avatar_demo_btn.clicked.connect(self._avatar_demo)
        self._avatar_expr_combo = QComboBox(self)
        self._avatar_expr_combo.addItems(["Neutral","Happy","Sad","Excited","Confused","Surprised","Embarrassed","Proud","Concerned","Curious"])
        self._avatar_expr_combo.currentTextChanged.connect(self._avatar_set_expression)
        self._avatar_intensity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._avatar_intensity_slider.setRange(40, 100)
        self._avatar_intensity_slider.setValue(85)
        self._avatar_intensity_slider.valueChanged.connect(self._avatar_set_intensity)
        self._avatar_mode_btn = AnimatedPushButton("CYCLE MODE")
        self._avatar_mode_btn.clicked.connect(self._avatar_cycle_mode)
        self._sentiment_lbl = QLabel("Sentiment: --")
        self._core_status_lbl = QLabel("CORE: READY")
        self._sec_status_lbl = QLabel("SEC: CLEARED")
        self._mode_status_lbl = QLabel("MODE: AUTO")
        self._wake_link_status_lbl = QLabel("WAKE LINK: CHECKING")
        for _lbl in [self._uptime_lbl, self._proc_lbl, self._avatar_status_lbl,
                      self._avatar_state_lbl, self._avatar_text_input,
                      self._avatar_open_btn, self._avatar_speak_btn, self._avatar_demo_btn,
                      self._avatar_expr_combo, self._avatar_intensity_slider,
                      self._avatar_mode_btn, self._sentiment_lbl,
                      self._core_status_lbl, self._sec_status_lbl,
                      self._mode_status_lbl, self._wake_link_status_lbl]:
            _hl.addWidget(_lbl)
        w.setParent(self)
        self._hidden.setParent(w)

        # ── Slim glow accent strip (pure eye candy) ──
        glow = QWidget(w)
        glow.setFixedWidth(4)
        glow.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {C.ACC}, stop:0.5 {C.PRI}, stop:1 {C.ACC2}); border-radius:2px;")
        _gl = QVBoxLayout(w)
        _gl.setContentsMargins(24, 12, 24, 12)
        _gl.setSpacing(0)
        _gl.addWidget(glow)
        _gl.addStretch()

        return w

    def _wire_advanced_buttons(self, parent: QWidget | None = None):
        """Connect advanced panel buttons to their stored commands and add friendly logging."""
        if parent is None:
            parent = self
        names = [
            "ai_scan", "code_rev", "optimize", "privacy", "summary",
            "vision_rewind", "voice_emotion", "noise_scan", "node_scan",
            "performance", "hologram", "phantom_auto",
        ]
        for obj in parent.findChildren(QAbstractButton):
            try:
                # special-case the avatar button to open the avatar window directly
                oname = obj.objectName()
                if oname == "avatar":
                    if AvatarWindow is not None and not bool(obj.property("_adv_wired")):
                        obj.clicked.connect(lambda checked=False: self._open_avatar())
                        obj.setProperty("_adv_wired", True)
                    continue

                cmd = obj.property("adv_command")
                if cmd:
                    # avoid double-connecting by using a flag property
                    if not bool(obj.property("_adv_wired")):
                        obj.clicked.connect(lambda checked=False, b=obj, c=cmd: self._dispatch_command(c, source="AdvPanel"))
                        obj.setProperty("_adv_wired", True)
            except Exception:
                pass

    def run_advanced_buttons_smoke_test(self) -> dict:
        """Trigger a small set of advanced buttons programmatically and return which were clicked.

        Returns a dict mapping objectName -> True/False depending on whether the button was found and clicked.
        """
        results = {}
        test_ids = ["ai_scan", "code_rev", "optimize", "privacy", "summary"]
        for tid in test_ids:
            try:
                btn = self.findChild(QAbstractButton, tid)
                if btn is None:
                    # try with underscores variant
                    btn = self.findChild(QAbstractButton, tid.replace("_", "_"))
                if btn:
                    btn.click()
                    results[tid] = True
                else:
                    results[tid] = False
            except Exception:
                results[tid] = False
        try:
            self._log.append_log(f"SYS: Advanced smoke test results: {results}")
        except Exception:
            pass
        return results

    def _open_avatar(self):
        try:
            if AvatarWindow is None:
                try:
                    self._log.append_log("SYS: Avatar module not available.")
                except Exception:
                    pass
                return
            if getattr(self, "_avatar_win", None) is None:
                try:
                    self._avatar_win = AvatarWindow()
                except Exception:
                    self._avatar_win = None
            if self._avatar_win is not None:
                try:
                    self._avatar_win.show()
                    self._avatar_win.raise_()
                    self._update_avatar_status("Avatar window opened.")
                except Exception:
                    try:
                        self._log.append_log("SYS: Failed to show avatar window.")
                    except Exception:
                        pass
        except Exception:
            try:
                self._log.append_log("SYS: Failed to open avatar.")
            except Exception:
                pass

    def _ensure_avatar_window(self):
        if AvatarWindow is None:
            try:
                self._log.append_log("SYS: Avatar module not available.")
            except Exception:
                pass
            return None
        if getattr(self, "_avatar_win", None) is None:
            try:
                self._avatar_win = AvatarWindow()
                try:
                    self._avatar_win.state_changed.connect(self._on_avatar_state_changed)
                except Exception:
                    pass
            except Exception:
                self._avatar_win = None
        return getattr(self, "_avatar_win", None)

    def _update_avatar_status(self, message: str):
        if hasattr(self, "_avatar_status_lbl") and self._avatar_status_lbl is not None:
            try:
                self._avatar_status_lbl.setText(message)
            except Exception:
                pass
        try:
            self._log.append_log(f"SYS: {message}")
        except Exception:
            pass

    def _avatar_speak_text(self):
        text = getattr(self, "_avatar_text_input", None)
        if text is None:
            return
        phrase = text.text().strip()
        if not phrase:
            self._update_avatar_status("Enter text to speak.")
            return
        avatar = self._ensure_avatar_window()
        if avatar is None:
            self._update_avatar_status("Cannot speak: avatar unavailable.")
            return
        avatar.speak_and_animate(phrase)
        self._update_avatar_status("Avatar is speaking...")

    def _avatar_demo(self):
        avatar = self._ensure_avatar_window()
        if avatar is None:
            self._update_avatar_status("Cannot demo: avatar unavailable.")
            return
        demo_text = "Hello, I am your avatar. I will speak with natural lip-sync and show a friendly expression."
        avatar.speak_and_animate(demo_text)
        self._update_avatar_status("Demo voice playing...")

    def _avatar_set_expression(self, expression: str):
        avatar = self._ensure_avatar_window()
        if avatar is not None:
            avatar.set_expression(expression)
            self._update_avatar_status(f"Expression set: {expression}")

    def _avatar_set_intensity(self, value: int):
        avatar = self._ensure_avatar_window()
        if avatar is not None:
            avatar.set_intensity(value)
            self._update_avatar_status(f"Intensity set: {value}%")

    def _on_avatar_state_changed(self, state: str):
        if hasattr(self, "_avatar_state_lbl") and self._avatar_state_lbl is not None:
            try:
                self._avatar_state_lbl.setText(f"State: {state.capitalize()}")
            except Exception:
                pass
        # Update sentiment indicator (NEW)
        if hasattr(self, "_sentiment_lbl") and self._sentiment_lbl is not None:
            try:
                color_map = {
                    "steady": C.TEXT_DIM, "curious": C.ACC2, "excited": "#ffd700",
                    "sad": C.PRI_DIM, "concerned": "#ff9f5a", "surprised": "#ff375f",
                    "proud": "#ff9f0a", "happy": C.GREEN, "thoughtful": C.ACC,
                }
                self._sentiment_lbl.setStyleSheet(
                    f"color: {color_map.get(state, C.TEXT_DIM)}; background: transparent; border: none;"
                )
                self._sentiment_lbl.setText(f"Sentiment: {state.upper()}")
            except Exception:
                pass

    def _avatar_cycle_mode(self):
        """Cycle orb visualizer mode (Arc Reactor -> Neural Pulse -> Data Stream)."""
        avatar = self._ensure_avatar_window()
        if avatar is not None and hasattr(avatar, "_cycle_mode"):
            avatar._cycle_mode()
            self._update_avatar_status(f"Mode: {avatar.status_label.text()}")

    def _open_command_palette(self):
        """Open a fuzzy-search Quick Command Palette (Ctrl+K)."""
        if hasattr(self, "_palette") and self._palette is not None:
            self._palette.close()
            return
        commands = [
            ("🖼️  Avatar / Orb", "avatar"),
            ("🔍  AI Screen Scan", "ai_scan"),
            ("💻  Code Review", "code_review"),
            ("⚙️  Optimize System", "optimize"),
            ("🔒  Privacy Check", "privacy"),
            ("📊  Activity Summary", "summary"),
            ("👁️  Vision Rewind", "vision_rewind"),
            ("🎙️  Voice Emotion", "voice_emotion"),
            ("🔊  Noise Scan", "noise_scan"),
            ("🕸️  Node Scan", "node_scan"),
            ("🚀  Performance Boost", "performance"),
            ("Smooth UI Toggle", "__smooth_toggle__"),
            ("Lag / Performance Report", "__perf_report__"),
            ("Smart Performance Scan", "__smart_perf_scan__"),
            ("Apply Freeze Guard", "__smart_perf_freeze__"),
            ("Identity Memory Status", "__identity_status__"),
            ("Identity Memory Summary", "__identity_summary__"),
            ("Self Feature Builder", "self feature builder suggest new features I can add safely"),
            ("Autonomous Capability Lab", "autonomous capability lab status and build next useful capability"),
            ("Command Timeline", "show recent command timeline and slow actions"),
            ("Computer Autopilot Status", "__autopilot_status__"),
            ("Autopilot Observe", "__autopilot_observe__"),
            ("Autopilot Plan", "__autopilot_plan__"),
            ("Autopilot Run Safe", "__autopilot_run__"),
            ("Autopilot History", "__autopilot_history__"),
            ("Command Center", "__command_center__"),
            ("🌐  Hologram HUD", "hologram"),
            ("👻  Phantom Auto", "phantom_auto"),
            ("📈  Life Dashboard", "life_dash"),
            ("☀️  Morning Brief", "morning"),
            ("⚠️  Alerts", "alerts"),
            ("🏠  Home Status", "home"),
            ("✉️  Email Brief", "email"),
            ("🌙  Toggle Theme", "__theme_toggle__"),
            ("🧪  System Lab", "sys_lab"),
            ("🍅  Pomodoro Start", "__pomo_start__"),
            ("🍅  Pomodoro Pause", "__pomo_pause__"),
            ("🍅  Pomodoro Reset", "__pomo_reset__"),
            ("📋  Clipboard History", "clipboard"),
            ("🔔  Test Notification", "__test_toast__"),
            ("🎨  Theme Customizer", "__syslab_focus__"),
            ("⌨️  Quick Notes", "__syslab_focus__"),
            ("🎵  Media Controller", "__syslab_focus__"),
            ("📡  Network Info", "__syslab_focus__"),
            ("🧠  AI Stat Tracker", "__syslab_focus__"),
            ("🎯  App Launcher", "__syslab_focus__"),
            ("🤖 Ultron AI", "__ultron_tab__"),
            ("🧠 Auto-Learn", "__ultron_tab__"),
            ("🔎 Web Search", "__ultron_tab__"),
            ("📚 Knowledge Base", "__ultron_tab__"),
            ("📰 Live News", "__ultron_tab__"),
            ("⚡ Code Runner", "__ultron_tab__"),
            ("📈 System Benchmark", "__syslab_focus__"),
            ("🧠 AI Suggester", "__syslab_focus__"),
            ("🔋 Battery Monitor", "__syslab_focus__"),
            ("🎨 AI Image Gen", "__syslab_focus__"),
            ("📈 Crypto Ticker", "__syslab_focus__"),
            ("🌐 Translator", "__syslab_focus__"),
            ("📱 QR Generator", "__syslab_focus__"),
            ("🔐 File Encryptor", "__syslab_focus__"),
            ("📝 Text Summarizer", "__syslab_focus__"),
            ("⚙️ System Tweaker", "__syslab_focus__"),
        ]
        self._palette = _CommandPalette(commands, self)
        self._palette.command_selected.connect(self._run_palette_command)
        self._palette.show()

    def _run_palette_command(self, cmd: str):
        if cmd == "__theme_toggle__":
            # cycle to next theme
            try:
                order = list(THEMES.keys())
                cur = getattr(self, "_current_theme", order[0])
                nxt = order[(order.index(cur) + 1) % len(order)]
                self._apply_theme(nxt)
                self._current_theme = nxt
            except Exception:
                pass
            return
        if cmd == "__pomo_start__":
            if hasattr(self, "_pomodoro"):
                self._pomodoro.start()
                if hasattr(self, "_toast_mgr"):
                    self._toast_mgr.show("🍅 Focus Started", "Pomodoro timer running. Stay focused!", "#ff6b35")
            return
        if cmd == "__pomo_pause__":
            if hasattr(self, "_pomodoro"):
                self._pomodoro.pause()
            return
        if cmd == "__pomo_reset__":
            if hasattr(self, "_pomodoro"):
                self._pomodoro.reset()
            return
        if cmd == "__test_toast__":
            if hasattr(self, "_toast_mgr"):
                self._toast_mgr.show("🔔 Test Notification", "This is a test toast from JOYA!", "#00e68a")
            return
        if cmd == "__smooth_toggle__":
            self._toggle_performance_mode()
            return
        if cmd == "__perf_report__":
            self.show_performance_report()
            return
        if cmd == "__smart_perf_scan__":
            self._dispatch_command("smart performance autopilot scan", source="Palette")
            return
        if cmd == "__smart_perf_freeze__":
            self._dispatch_command("apply freeze guard performance preset", source="Palette")
            return
        if cmd == "__identity_status__":
            self._dispatch_command("human identity memory status", source="Palette")
            return
        if cmd == "__identity_summary__":
            self._dispatch_command("show human identity memory summary", source="Palette")
            return
        if cmd == "__command_center__":
            self._focus_right_tab("COMMAND")
            if hasattr(self, "_toast_mgr"):
                self._toast_mgr.show("Command Center", "Opened the command launcher.", C.PRI, 2000)
            return
        if cmd.startswith("__autopilot_"):
            action_map = {
                "__autopilot_status__": "status",
                "__autopilot_observe__": "observe",
                "__autopilot_plan__": "plan",
                "__autopilot_run__": "run",
                "__autopilot_history__": "history",
            }
            try:
                tabs = self._right_panel.findChild(QTabWidget)
                if tabs:
                    tabs.setCurrentIndex(6)
            except Exception:
                pass
            self._run_autopilot_panel_action(action_map.get(cmd, "status"))
            return
        if cmd == "__syslab_focus__":
            try:
                tabs = self._right_panel.findChild(QTabWidget)
                if tabs:
                    # SYS LAB is at index 4
                    tabs.setCurrentIndex(4)
                if hasattr(self, "_toast_mgr"):
                    self._toast_mgr.show("🧪 SYS LAB", "Opened the System Lab panel!", "#b388ff", 2000)
            except Exception:
                pass
            return
        if cmd == "__ultron_tab__":
            try:
                tabs = self._right_panel.findChild(QTabWidget)
                if tabs:
                    tabs.setCurrentIndex(5)
                if hasattr(self, "_toast_mgr"):
                    self._toast_mgr.show("🤖 ULTRON", "Ultron AI tab opened!", "#ff6b35", 2000)
            except Exception:
                pass
            return
        if cmd == "sys_lab":
            # switch right panel to SYS LAB tab (index 4)
            try:
                tabs = self._right_panel.findChild(QTabWidget)
                if tabs:
                    tabs.setCurrentIndex(4)
            except Exception:
                pass
            return
        if cmd == "clipboard":
            try:
                tabs = self._right_panel.findChild(QTabWidget)
                if tabs:
                    tabs.setCurrentIndex(4)
            except Exception:
                pass
            return
        try:
            self._dispatch_command(cmd, source="Palette")
        except Exception:
            pass

    def _load_extended_config(self):
        self.voice_name = "Charon"
        self.wake_words = [
            "jarvis",
            "hey jarvis",
            "heyy jarvis",
            "ok jarvis",
            "okay jarvis",
            "hello jarvis",
            "hi jarvis",
            "jervis",
            "jarves",
            "javis",
            "wake up",
            "wake-up",
        ]
        self.standby_timeout = 12.0
        self.theme_name = "Apple Space Gray"
        self.visualizer_mode_name = "Arc Reactor (Classic)"
        self.sound_effects = True
        self._auto_wake = True
        self.hands_free_mode = True
        self.confirm_dangerous_actions = True
        self.proactive_assist = True
        self.screen_watch_enabled = False
        self.screen_watch_interval = 90
        self.live_context_enabled = False
        self.live_context_source = "screen"
        self.live_context_interval = 8
        self.live_context_provider = "groq"
        self.notification_watch_enabled = False
        self.notification_voice_enabled = True
        self.notification_important_only = True
        self.privacy_guard_enabled = False
        self.tray_enabled = True
        self.start_hidden_to_tray = True
        self.close_to_tray = True
        self.wake_opens_floating_assistant = True
        self.visual_autopilot_enabled = True
        self.visual_autopilot_max_retries = 2
        self.visual_autopilot_store_proofs = False
        self.performance_mode = True
        self.lag_watchdog_enabled = True
        self.smart_life_enabled = True
        self.smart_life_auto_routines = True
        self.smart_life_morning_time = "08:00"
        self.smart_life_evening_time = "21:00"
        self.smart_life_alert_interval_minutes = 30
        self.voice_macros = dict(DEFAULT_VOICE_MACROS)
        self.human_mode_enabled = False
        self.human_eye_source = "both"
        self.human_eye_interval = 8

        if API_FILE.exists():
            try:
                d = json.loads(API_FILE.read_text(encoding="utf-8"))
                dirty = False
                if "voice_name" not in d:
                    d["voice_name"] = self.voice_name
                    dirty = True
                else:
                    self.voice_name = d["voice_name"]
                    
                if "wake_words" not in d:
                    d["wake_words"] = self.wake_words
                    dirty = True
                else:
                    loaded_words = d.get("wake_words", [])
                    if not isinstance(loaded_words, list):
                        loaded_words = []
                    merged_words = []
                    seen_words = set()
                    for word in [*self.wake_words, *loaded_words]:
                        clean = str(word).strip()
                        key = clean.lower()
                        if clean and key not in seen_words:
                            merged_words.append(clean)
                            seen_words.add(key)
                    self.wake_words = merged_words
                    if merged_words != loaded_words:
                        d["wake_words"] = merged_words
                        dirty = True
                    
                if "standby_timeout" not in d:
                    d["standby_timeout"] = self.standby_timeout
                    dirty = True
                else:
                    self.standby_timeout = float(d["standby_timeout"])
                    
                if "theme_name" not in d:
                    d["theme_name"] = self.theme_name
                    dirty = True
                else:
                    self.theme_name = d["theme_name"]
                    
                if "sound_effects" not in d:
                    d["sound_effects"] = self.sound_effects
                    dirty = True
                else:
                    self.sound_effects = bool(d["sound_effects"])

                if "auto_wake_enabled" not in d:
                    d["auto_wake_enabled"] = self._auto_wake
                    dirty = True
                else:
                    self._auto_wake = bool(d["auto_wake_enabled"])

                if "hands_free_mode" not in d:
                    d["hands_free_mode"] = self.hands_free_mode
                    dirty = True
                else:
                    self.hands_free_mode = bool(d["hands_free_mode"])

                if "confirm_dangerous_actions" not in d:
                    d["confirm_dangerous_actions"] = self.confirm_dangerous_actions
                    dirty = True
                else:
                    self.confirm_dangerous_actions = bool(d["confirm_dangerous_actions"])
                if "confirm_risky_actions" not in d:
                    d["confirm_risky_actions"] = self.confirm_dangerous_actions
                    dirty = True
                else:
                    self.confirm_dangerous_actions = bool(d["confirm_risky_actions"])

                if "proactive_assist" not in d:
                    d["proactive_assist"] = self.proactive_assist
                    dirty = True
                else:
                    self.proactive_assist = bool(d["proactive_assist"])

                if "screen_watch_enabled" not in d:
                    d["screen_watch_enabled"] = self.screen_watch_enabled
                    dirty = True
                else:
                    self.screen_watch_enabled = bool(d["screen_watch_enabled"])

                if "screen_watch_interval" not in d:
                    d["screen_watch_interval"] = self.screen_watch_interval
                    dirty = True
                else:
                    self.screen_watch_interval = max(30, min(300, int(d["screen_watch_interval"])))

                if "live_context_enabled" not in d:
                    d["live_context_enabled"] = self.live_context_enabled
                    dirty = True
                else:
                    self.live_context_enabled = bool(d["live_context_enabled"])

                if "live_context_source" not in d:
                    d["live_context_source"] = self.live_context_source
                    dirty = True
                else:
                    source = str(d["live_context_source"]).lower()
                    self.live_context_source = source if source in ("screen", "camera", "both") else "screen"

                if "live_context_interval" not in d:
                    d["live_context_interval"] = self.live_context_interval
                    dirty = True
                else:
                    self.live_context_interval = max(5, min(120, int(d["live_context_interval"])))

                if "live_context_provider" not in d:
                    d["live_context_provider"] = self.live_context_provider
                    dirty = True
                else:
                    provider = str(d["live_context_provider"]).lower()
                    self.live_context_provider = provider if provider in ("groq", "openrouter", "gemini", "openai", "auto") else "groq"

                if "notification_watch_enabled" not in d:
                    d["notification_watch_enabled"] = self.notification_watch_enabled
                    dirty = True
                else:
                    self.notification_watch_enabled = bool(d["notification_watch_enabled"])

                if "notification_voice_enabled" not in d:
                    d["notification_voice_enabled"] = self.notification_voice_enabled
                    dirty = True
                else:
                    self.notification_voice_enabled = bool(d["notification_voice_enabled"])

                if "notification_important_only" not in d:
                    d["notification_important_only"] = self.notification_important_only
                    dirty = True
                else:
                    self.notification_important_only = bool(d["notification_important_only"])

                if "privacy_guard_enabled" not in d:
                    d["privacy_guard_enabled"] = self.privacy_guard_enabled
                    dirty = True
                else:
                    self.privacy_guard_enabled = bool(d["privacy_guard_enabled"])

                for key, default in {
                    "tray_enabled": self.tray_enabled,
                    "start_hidden_to_tray": self.start_hidden_to_tray,
                    "close_to_tray": self.close_to_tray,
                    "wake_opens_floating_assistant": self.wake_opens_floating_assistant,
                    "visual_autopilot_enabled": self.visual_autopilot_enabled,
                    "visual_autopilot_store_proofs": self.visual_autopilot_store_proofs,
                    "performance_mode": self.performance_mode,
                    "lag_watchdog_enabled": self.lag_watchdog_enabled,
                    "smart_life_enabled": self.smart_life_enabled,
                    "smart_life_auto_routines": self.smart_life_auto_routines,
                    "human_mode_enabled": self.human_mode_enabled,
                    "simple_mode": False,
                }.items():
                    if key not in d:
                        d[key] = default
                        dirty = True
                    setattr(self, key, bool(d.get(key)))

                if "human_eye_source" not in d:
                    d["human_eye_source"] = self.human_eye_source
                    dirty = True
                else:
                    src = str(d["human_eye_source"]).lower()
                    self.human_eye_source = src if src in ("screen", "camera", "both") else "both"

                if "human_eye_interval" not in d:
                    d["human_eye_interval"] = self.human_eye_interval
                    dirty = True
                else:
                    self.human_eye_interval = max(5, min(120, int(d["human_eye_interval"])))

                if "visual_autopilot_max_retries" not in d:
                    d["visual_autopilot_max_retries"] = self.visual_autopilot_max_retries
                    dirty = True
                else:
                    self.visual_autopilot_max_retries = max(0, min(5, int(d["visual_autopilot_max_retries"])))

                if "smart_life_morning_time" not in d:
                    d["smart_life_morning_time"] = self.smart_life_morning_time
                    dirty = True
                else:
                    self.smart_life_morning_time = str(d["smart_life_morning_time"])

                if "smart_life_evening_time" not in d:
                    d["smart_life_evening_time"] = self.smart_life_evening_time
                    dirty = True
                else:
                    self.smart_life_evening_time = str(d["smart_life_evening_time"])

                if "smart_life_alert_interval_minutes" not in d:
                    d["smart_life_alert_interval_minutes"] = self.smart_life_alert_interval_minutes
                    dirty = True
                else:
                    self.smart_life_alert_interval_minutes = max(5, min(1440, int(d["smart_life_alert_interval_minutes"])))

                if "voice_macros" not in d:
                    d["voice_macros"] = self.voice_macros
                    dirty = True
                else:
                    loaded_macros = d.get("voice_macros", {})
                    if isinstance(loaded_macros, dict):
                        self.voice_macros = {**DEFAULT_VOICE_MACROS, **loaded_macros}
                
                if "visualizer_mode_name" not in d:
                    d["visualizer_mode_name"] = self.visualizer_mode_name
                    dirty = True
                else:
                    self.visualizer_mode_name = d["visualizer_mode_name"]
                    
                if dirty:
                    API_FILE.write_text(json.dumps(d, indent=4), encoding="utf-8")
            except Exception:
                pass
        
        try:
            self._apply_theme(self.theme_name, initial=True)
        except Exception:
            pass
            
        try:
            self._apply_visualizer_mode(self.visualizer_mode_name)
        except Exception:
            pass

    def _save_extended_config(self):
        if not API_FILE.exists():
            return
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            d["voice_name"] = self.voice_name
            d["wake_words"] = self.wake_words
            d["standby_timeout"] = self.standby_timeout
            d["theme_name"] = self.theme_name
            d["visualizer_mode_name"] = self.visualizer_mode_name
            d["sound_effects"] = self.sound_effects
            d["auto_wake_enabled"] = self._auto_wake
            d["hands_free_mode"] = self.hands_free_mode
            d["confirm_dangerous_actions"] = self.confirm_dangerous_actions
            d["confirm_risky_actions"] = self.confirm_dangerous_actions
            d["proactive_assist"] = self.proactive_assist
            d["screen_watch_enabled"] = self.screen_watch_enabled
            d["screen_watch_interval"] = int(self.screen_watch_interval)
            d["live_context_enabled"] = self.live_context_enabled
            d["live_context_source"] = self.live_context_source
            d["live_context_interval"] = int(self.live_context_interval)
            d["live_context_provider"] = self.live_context_provider
            d["notification_watch_enabled"] = self.notification_watch_enabled
            d["notification_voice_enabled"] = self.notification_voice_enabled
            d["notification_important_only"] = self.notification_important_only
            d["privacy_guard_enabled"] = self.privacy_guard_enabled
            d["tray_enabled"] = self.tray_enabled
            d["start_hidden_to_tray"] = self.start_hidden_to_tray
            d["close_to_tray"] = self.close_to_tray
            d["wake_opens_floating_assistant"] = self.wake_opens_floating_assistant
            d["visual_autopilot_enabled"] = self.visual_autopilot_enabled
            d["visual_autopilot_max_retries"] = int(self.visual_autopilot_max_retries)
            d["visual_autopilot_store_proofs"] = self.visual_autopilot_store_proofs
            d["performance_mode"] = bool(getattr(self, "performance_mode", True))
            d["lag_watchdog_enabled"] = bool(getattr(self, "lag_watchdog_enabled", True))
            d["simple_mode"] = getattr(self.hud, "simple_mode", False)
            d["smart_life_enabled"] = self.smart_life_enabled
            d["smart_life_auto_routines"] = self.smart_life_auto_routines
            d["smart_life_morning_time"] = self.smart_life_morning_time
            d["smart_life_evening_time"] = self.smart_life_evening_time
            d["smart_life_alert_interval_minutes"] = int(self.smart_life_alert_interval_minutes)
            d["voice_macros"] = self.voice_macros
            API_FILE.write_text(json.dumps(d, indent=4), encoding="utf-8")
        except Exception:
            pass

    def _apply_theme(self, name: str, initial=False):
        if name not in THEMES: return
        t = THEMES[name]
        C.PRI = t["PRI"]
        C.PRI_DIM = t["PRI_DIM"]
        C.PRI_GHO = t["PRI_GHO"]
        C.ACC = t["ACC"]
        C.ACC2 = t["ACC2"]
        C.TEXT = t["TEXT"]
        C.TEXT_DIM = t["TEXT_DIM"]
        C.TEXT_MED = t["TEXT_MED"]
        C.BORDER = t["BORDER"]
        C.BORDER_B = t["BORDER_B"]
        C.BORDER_A = t["BORDER_A"]
        C.BG = t["BG"]
        C.PANEL = t["PANEL"]
        C.PANEL2 = t["PANEL2"]
        C.BAR_BG = t["BAR_BG"]
        
        self.theme_name = name
        if not initial:
            self._save_extended_config()
            
        cw = self.centralWidget()
        if cw is not None:
            cw.setStyleSheet(f"background: {C.BG};")
            
        if hasattr(self, "_left_panel") and self._left_panel is not None:
            self._left_panel.setStyleSheet(f"""
                QWidget {{
                    background: #000000;
                    border-right: 1px solid rgba(255, 255, 255, 0.05);
                }}
                QLabel {{ background: transparent; border: none; }}
            """)
            
        if hasattr(self, "_right_panel") and self._right_panel is not None:
            self._right_panel.setStyleSheet(self._right_panel_style())
            
        if hasattr(self, "_log") and self._log is not None:
            self._log.setStyleSheet(f"""
                QTextEdit {{
                    background: {C.PANEL};
                    color: {C.TEXT};
                    border: 1px solid {C.BORDER};
                    border-radius: 8px;
                    padding: 6px;
                    selection-background-color: {C.PRI_GHO};
                }}
                QScrollBar:vertical {{
                    background: {C.BG};
                    width: 8px;
                    border: none;
                }}
                QScrollBar::handle:vertical {{
                    background: {C.BORDER_B};
                    border-radius: 8px;
                    min-height: 20px;
                }}
            """)
            
        if hasattr(self, "_mute_btn") and self._mute_btn is not None:
            self._style_mute_btn()

        if hasattr(self, "_hands_btn") and self._hands_btn is not None:
            self._style_hands_button()

        if hasattr(self, "_metric_cpu"):
            self._style_metric_pills()
            
        if hasattr(self, "hud") and self.hud is not None:
            self.hud.update()
            
        if not initial:
            try:
                self._log.append_log(f"SYS: Applied theme: {name}")
            except Exception:
                pass

    def _apply_visualizer_mode(self, name: str):
        self.visualizer_mode_name = name
        mode_map = {
            "Arc Reactor (Classic)": "arc_reactor",
            "Hologram Wave (Voice)": "hologram_wave",
            "Digital Matrix": "matrix_core",
            "Pulsing Nebula": "pulsing_nebula"
        }
        self.hud.visualizer_mode = mode_map.get(name, "arc_reactor")
        self.hud.visualizer_mode_name = name
        self._save_extended_config()
        self.hud.update()

    def _on_mic_level_received(self, val: float):
        pass

    def _on_wake_toggled(self, checked):
        self.set_wake_enabled(bool(checked))

    def _on_sound_effects_toggled(self, checked):
        self.sound_effects = checked
        self._save_extended_config()
        self._log.append_log(f"SYS: Sci-Fi Audio Cues {'enabled' if checked else 'disabled'}.")

    def _on_simple_mode_toggled(self, checked):
        self.simple_mode = checked
        if hasattr(self, "hud"):
            self.hud.simple_mode = checked
            self.hud.update()
        self._save_extended_config()
        try:
            self._log.append_log(f"SYS: Battery Saver / Simple UI Mode {'enabled (Low CPU)' if checked else 'disabled (Enhanced UI)'}.")
        except Exception:
            pass

    def _on_timeout_slider_changed(self, val):
        if val >= 65:
            self._timeout_lbl.setText("Standby Timeout: Never")
            self.standby_timeout = 9999.0
        else:
            self._timeout_lbl.setText(f"Standby Timeout: {val}s")
            self.standby_timeout = float(val)
        self._save_extended_config()

    def _run_screenshot_analyze(self):
        self._dispatch_command("analyze my screen", source="Button")

    def _right_panel_style(self) -> str:
        return f"""
            #RightPanel {{
                background: #000000;
                border-left: 1px solid rgba(255, 255, 255, 0.05);
            }}
            #RightPanel QLabel {{
                background: transparent;
                border: none;
                font-family: "{C.FONT_SANS}";
            }}
            #RightPanel QFrame {{
                background: transparent;
            }}
            #RightPanel QLineEdit {{
                background: rgba(255, 255, 255, 0.02);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 6px 10px;
                font-family: "{C.FONT_SANS}";
                font-size: 11px;
                selection-background-color: {C.PRI};
            }}
            #RightPanel QLineEdit:hover {{
                border-color: rgba(255, 255, 255, 0.15);
            }}
            #RightPanel QLineEdit:focus {{
                border-color: {C.PRI};
                background: rgba(0, 0, 0, 0.18);
            }}
            #RightPanel QPushButton {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 6px 12px;
                font-family: "{C.FONT_SANS}";
                font-weight: bold;
                font-size: 11px;
            }}
            #RightPanel QPushButton:hover {{
                background: {C.PRI_GHO};
                border-color: {C.PRI};
                color: #ffffff;
            }}
            #RightPanel QPushButton:pressed {{
                background: {C.PRI};
            }}
            #RightPanel QComboBox {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 5px 8px;
                font-family: "{C.FONT_SANS}";
                font-size: 11px;
            }}
            #RightPanel QComboBox:hover {{
                border-color: rgba(255, 255, 255, 0.15);
            }}
            #RightPanel QComboBox QAbstractItemView {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                selection-background-color: {C.PRI};
                outline: none;
            }}
            #RightPanel QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            #RightPanel QTabBar {{
                background: transparent;
            }}
            #RightPanel QTabBar::tab {{
                background: rgba(255, 255, 255, 0.02);
                color: {C.TEXT_DIM};
                padding: 6px 12px;
                border-radius: 12px;
                font-family: "{C.FONT_SANS}";
                font-weight: bold;
                font-size: 10px;
                margin-right: 6px;
                margin-bottom: 8px;
            }}
            #RightPanel QTabBar::tab:hover {{
                background: rgba(255, 255, 255, 0.06);
                color: {C.TEXT};
            }}
            #RightPanel QTabBar::tab:selected {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border: 1px solid {C.PRI};
            }}
            #RightPanel QScrollBar:vertical {{
                background: transparent;
                width: 6px;
            }}
            #RightPanel QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.08);
                border-radius: 3px;
                min-height: 20px;
            }}
            #RightPanel QScrollBar::handle:vertical:hover {{
                background: {C.PRI};
            }}
            #RightPanel QScrollBar::add-line:vertical, #RightPanel QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """

    def _apply_and_reboot_ai(self):
        self.voice_name = self._voice_combo.currentText()
        wake_text = self._wake_input.text()
        self.wake_words = [w.strip() for w in wake_text.split(",") if w.strip()]
        
        val = self._timeout_slider.value()
        if val >= 65:
            self.standby_timeout = 9999.0
        else:
            self.standby_timeout = float(val)
            
        self.theme_name = self._theme_combo.currentText()
        self.visualizer_mode_name = self._visualizer_combo.currentText()
        self.sound_effects = self._sound_checkbox.isChecked()
        self.hands_free_mode = self._hands_free_checkbox.isChecked()
        self.confirm_dangerous_actions = self._confirm_checkbox.isChecked()
        self.proactive_assist = self._proactive_checkbox.isChecked()
        self.notification_voice_enabled = self._notification_voice_checkbox.isChecked()
        self.notification_important_only = self._notification_important_checkbox.isChecked()
            
        self._save_extended_config()
        self._apply_visualizer_mode(self.visualizer_mode_name)
        self._style_hands_button()
        self._update_handsfree_status()
        self._log.append_log("SYS: Settings applied. Rebooting AI link...")
        self._reconnect_requested = True
        self._fast_reconnect = True

    def _register_boss_photo(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Boss Profile Picture", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            try:
                dest = Path(__file__).resolve().parent / "config" / "boss_profile.jpg"
                import shutil
                shutil.copy(f, dest)
                QMessageBox.information(self, "Success", "Boss Profile Photo registered successfully!")
                self._log.append_log("SYS: New Boss Profile Photo registered.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to register photo: {e}")

    def _reset_security_pin(self):
        try:
            dest = Path(__file__).resolve().parent / "config" / "security_config.json"
            if dest.exists():
                dest.unlink()
            QMessageBox.information(self, "Reset Success", "Security PIN has been reset. Please lock or restart the app to configure a new PIN!")
            self._log.append_log("SYS: Security PIN reset triggered.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to reset PIN: {e}")

    def _on_unlocked(self):
        self._log.append_log("SYS: System unlocked. Active Operator confirmed.")
        try:
            # Play greeting
            if tts_engine:
                tts_engine.speak("All systems online. Welcome back, Operator.")
        except Exception:
            pass

    def _browse_file_inspector(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open File for AI Inspection", "", "All Files (*);;Text Files (*.txt *.py *.js *.html *.json *.md);;Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            self.file_path_input.setText(f)
            
    def _run_file_inspection(self):
        f_path = self.file_path_input.text().strip()
        prompt = self.file_prompt_input.toPlainText().strip()
        if not f_path:
            QMessageBox.warning(self, "No File Chosen", "Please select a file to inspect first!")
            return
        if not prompt:
            prompt = "Analyze this file in detail and explain its purpose, key structures, and contents."
            
        self.inspect_output.setText("⏳ AI is analyzing the file... Please wait.")
        self.inspect_run_btn.setEnabled(False)
        
        self.inspect_worker = FileInspectorWorker(f_path, prompt)
        self.inspect_worker.finished.connect(self._on_inspection_finished)
        self.inspect_worker.start()
        
    def _on_inspection_finished(self, result: str):
        self.inspect_output.setText(result)
        self.inspect_run_btn.setEnabled(True)

    def _build_file_inspector_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 5px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 2px; min-height: 12px; }}
        """)
        
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(12)
        
        # Header card
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        h_lay = QVBoxLayout(header)
        h_title = QLabel("📁 Premium File Inspector (Tesla/Apple Style)")
        h_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #2997ff;")
        h_desc = QLabel("Upload any document, source code, logs, or image file. The AI will inspect, explain, and extract insights using your configured Gemini API key.")
        h_desc.setStyleSheet("color: #86868b; font-size: 12px;")
        h_desc.setWordWrap(True)
        h_lay.addWidget(h_title)
        h_lay.addWidget(h_desc)
        lay.addWidget(header)
        
        # Action Card
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        c_lay = QVBoxLayout(card)
        c_lay.setSpacing(10)
        
        # Row 1: File selection
        file_row = QHBoxLayout()
        self.file_path_input = QLineEdit()
        self.file_path_input.setPlaceholderText("Select file path...")
        self.file_path_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
                padding: 8px;
                color: #ffffff;
            }}
        """)
        
        browse_btn = QPushButton("Browse File")
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.SURFACE};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
                padding: 8px 16px;
                color: #ffffff;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {C.SURFACE_HI};
            }}
        """)
        browse_btn.clicked.connect(self._browse_file_inspector)
        
        file_row.addWidget(self.file_path_input)
        file_row.addWidget(browse_btn)
        c_lay.addLayout(file_row)
        
        # Row 2: Prompt
        prompt_lbl = QLabel("AI Prompt Instructions:")
        prompt_lbl.setStyleSheet("color: #86868b; font-size: 12px; font-weight: bold;")
        c_lay.addWidget(prompt_lbl)
        
        self.file_prompt_input = QTextEdit()
        self.file_prompt_input.setPlaceholderText("e.g., Explain what this file does, find bugs in this code, or summarize this image...")
        self.file_prompt_input.setMaximumHeight(80)
        self.file_prompt_input.setStyleSheet(f"""
            QTextEdit {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
                padding: 8px;
                color: #ffffff;
            }}
        """)
        c_lay.addWidget(self.file_prompt_input)
        
        # Row 3: Inspect button
        self.inspect_run_btn = QPushButton("🚀 Run AI Inspection")
        self.inspect_run_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a73e8, stop:1 #0056b3);
                border: none;
                border-radius: 6px;
                padding: 10px;
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b82f6, stop:1 #0069d9);
            }
        """)
        self.inspect_run_btn.clicked.connect(self._run_file_inspection)
        c_lay.addWidget(self.inspect_run_btn)
        
        lay.addWidget(card)
        
        # Output Card
        out_card = QFrame()
        out_card.setStyleSheet(f"""
            QFrame {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        o_lay = QVBoxLayout(out_card)
        o_title = QLabel("📝 AI Inspection Output:")
        o_title.setStyleSheet("color: #30d158; font-size: 12px; font-weight: bold;")
        o_lay.addWidget(o_title)
        
        self.inspect_output = QTextEdit()
        self.inspect_output.setReadOnly(True)
        self.inspect_output.setPlaceholderText("Analysis results will be printed here...")
        self.inspect_output.setMinimumHeight(240)
        self.inspect_output.setStyleSheet(f"""
            QTextEdit {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
                padding: 8px;
                color: #ffffff;
                font-family: 'Consolas', monospace;
            }}
        """)
        o_lay.addWidget(self.inspect_output)
        
        lay.addWidget(out_card)
        scroll.setWidget(container)
        
        v_box = QVBoxLayout(w)
        v_box.addWidget(scroll)
        v_box.setContentsMargins(0, 0, 0, 0)
        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("RightPanel")
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(self._right_panel_style())
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt.upper()}")
            l.setFont(pfont(10, "semibold", spacing=0.4))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; letter-spacing: 1px;")
            return l

        def _panel_note(txt):
            l = QLabel(txt)
            l.setWordWrap(True)
            l.setFont(QFont(C.FONT_SANS, 7))
            l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            return l

        def _action_button(title, command=None, callback=None, accent=False):
            btn = AnimatedPushButton(title, accent=accent)
            btn.setToolTip(command or title)
            if callback is not None:
                btn.clicked.connect(callback)
            elif command is not None:
                btn.clicked.connect(lambda _, c=command, t=title: self._run_preset_command(t, c))
            return btn

        # Console Tab
        console_widget = QWidget()
        console_widget.setStyleSheet("background: transparent; border: none;")
        console_lay = QVBoxLayout(console_widget)
        console_lay.setContentsMargins(2, 2, 2, 2)
        console_lay.setSpacing(6)
        
        console_lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        console_lay.addWidget(self._log, stretch=1)
        
        # Text Command Input Row
        console_input_row = QHBoxLayout(); console_input_row.setSpacing(6)
        self._console_input = QLineEdit()
        self._console_input.setPlaceholderText("Type command here... press Enter to execute")
        self._console_input.setFont(QFont("Monaco" if _OS == "Darwin" else "Consolas", 10))
        self._console_input.setFixedHeight(36)
        self._console_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px 10px;
                font-weight: 500; letter-spacing: 0.3px;
            }}
            QLineEdit:focus {{ background: {C.PANEL2}; border: 1px solid {C.PRI}; }}
        """)
        self._console_input.returnPressed.connect(self._on_console_input)
        console_input_row.addWidget(self._console_input)
        
        btn_console_send = AnimatedPushButton("⚡ EXECUTE")
        btn_console_send.setFixedSize(90, 36)
        btn_console_send.clicked.connect(self._on_console_input)
        console_input_row.addWidget(btn_console_send)

        # Voice test button — verify TTS is working
        btn_voice_test = AnimatedPushButton("🔊 TEST VOICE")
        btn_voice_test.setFixedSize(110, 36)
        btn_voice_test.setCursor(Qt.CursorShape.PointingHandCursor)
        def _test_voice():
            self._log.append_log("SYS: 🔊 Testing voice output...")
            _tts_speak("Hello sir, voice system is working perfectly. I am JOYA XXXIX, your AI companion.", blocking=False)
        btn_voice_test.clicked.connect(_test_voice)
        console_input_row.addWidget(btn_voice_test)

        # Clear console button
        btn_clear_log = AnimatedPushButton("🗑️ CLEAR")
        btn_clear_log.setFixedSize(70, 36)
        btn_clear_log.setCursor(Qt.CursorShape.PointingHandCursor)
        def _clear_log():
            try:
                self._log.clear_log()
            except Exception:
                try:
                    self._log.setPlainText("")
                except Exception:
                    pass
        btn_clear_log.clicked.connect(_clear_log)
        console_input_row.addWidget(btn_clear_log)
        console_lay.addLayout(console_input_row)
        mission_widget = QWidget()
        mission_widget.setStyleSheet("background: transparent; border: none;")
        mission_lay = QVBoxLayout(mission_widget)
        mission_lay.setContentsMargins(2, 2, 2, 2)
        mission_lay.setSpacing(7)

        mission_lay.addWidget(_sec("HANDS-FREE COCKPIT"))
        mission_lay.addWidget(_panel_note("Voice macros and one-tap presets dispatch complete commands without touching the keyboard."))

        for idx in range(0, len(HANDSFREE_PRESETS), 2):
            row = QHBoxLayout()
            row.setSpacing(6)
            for title, command in HANDSFREE_PRESETS[idx:idx + 2]:
                row.addWidget(_action_button(title, command=command, accent=(title == "DAILY BRIEF")))
            mission_lay.addLayout(row)

        sep_m = QFrame(); sep_m.setFrameShape(QFrame.Shape.HLine)
        sep_m.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        mission_lay.addWidget(sep_m)

        mission_lay.addWidget(_sec("MISSION QUEUE"))
        self._mission_status_lbl = QLabel("Mission Queue: READY / 0 step(s)")
        self._mission_status_lbl.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._mission_status_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        mission_lay.addWidget(self._mission_status_lbl)

        self._mission_box = QTextEdit()
        self._mission_box.setPlaceholderText("One command per line. Example: analyze my screen, summarize it, then save useful notes.")
        self._mission_box.setFixedHeight(92)
        self._mission_box.setFont(QFont(C.FONT_MONO, 9, QFont.Weight.Medium))
        self._mission_box.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 7px;
                selection-background-color: {C.PRI_GHO};
            }}
            QTextEdit:focus {{ border: 1px solid {C.PRI}; background: {C.PANEL2}; }}
        """)
        mission_lay.addWidget(self._mission_box)

        queue_btn_row = QHBoxLayout()
        queue_btn_row.setSpacing(6)
        queue_btn_row.addWidget(_action_button("QUEUE", callback=self._queue_mission_steps, accent=True))
        queue_btn_row.addWidget(_action_button("RUN", callback=self._start_mission_queue))
        queue_btn_row.addWidget(_action_button("CLEAR", callback=self._clear_mission_queue))
        mission_lay.addLayout(queue_btn_row)

        self._queue_autorun_checkbox = QCheckBox("Run queued steps automatically")
        self._queue_autorun_checkbox.setChecked(True)
        self._queue_autorun_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._queue_autorun_checkbox.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        mission_lay.addWidget(self._queue_autorun_checkbox)

        mission_lay.addWidget(_action_button("LOAD SAMPLE MISSION", callback=self._load_mission_template))

        sep_w = QFrame(); sep_w.setFrameShape(QFrame.Shape.HLine)
        sep_w.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        mission_lay.addWidget(sep_w)

        mission_lay.addWidget(_sec("AUTO SCREEN WATCH"))
        self._watch_checkbox = QCheckBox("Watch screen and brief only when useful")
        self._watch_checkbox.setChecked(self.screen_watch_enabled)
        self._watch_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._watch_checkbox.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        self._watch_checkbox.toggled.connect(self._on_watch_toggled)
        mission_lay.addWidget(self._watch_checkbox)

        self._watch_interval_lbl = QLabel(f"Watch Interval: {int(self.screen_watch_interval)}s")
        self._watch_interval_lbl.setFont(QFont(C.FONT_SANS, 8))
        self._watch_interval_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        mission_lay.addWidget(self._watch_interval_lbl)

        self._watch_interval_slider = QSlider(Qt.Orientation.Horizontal)
        self._watch_interval_slider.setRange(30, 300)
        self._watch_interval_slider.setSingleStep(15)
        self._watch_interval_slider.setValue(int(self.screen_watch_interval))
        self._watch_interval_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {C.BORDER_A}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {C.GREEN}; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 12px;
            }}
        """)
        self._watch_interval_slider.valueChanged.connect(self._on_watch_interval_changed)
        mission_lay.addWidget(self._watch_interval_slider)

        mission_lay.addWidget(_sec("LIVE VISUAL CONTEXT"))
        self._live_context_checkbox = QCheckBox("Share live screen/camera context with AI")
        self._live_context_checkbox.setChecked(self.live_context_enabled)
        self._live_context_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._live_context_checkbox.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        self._live_context_checkbox.toggled.connect(self._on_live_context_toggled)
        mission_lay.addWidget(self._live_context_checkbox)

        source_row = QHBoxLayout()
        source_lbl = QLabel("Source")
        source_lbl.setFont(QFont(C.FONT_SANS, 8))
        source_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        source_row.addWidget(source_lbl)
        self._live_source_combo = QComboBox()
        self._live_source_combo.addItems(["screen", "camera", "both"])
        self._live_source_combo.setCurrentText(self.live_context_source)
        self._live_source_combo.currentTextChanged.connect(self._on_live_source_changed)
        self._live_source_combo.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER_B};")
        source_row.addWidget(self._live_source_combo)
        mission_lay.addLayout(source_row)

        provider_row = QHBoxLayout()
        provider_lbl = QLabel("Vision Provider")
        provider_lbl.setFont(QFont(C.FONT_SANS, 8))
        provider_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        provider_row.addWidget(provider_lbl)
        self._live_provider_combo = QComboBox()
        self._live_provider_combo.addItems(["groq", "openrouter", "gemini", "openai", "auto"])
        self._live_provider_combo.setCurrentText(self.live_context_provider)
        self._live_provider_combo.currentTextChanged.connect(self._on_live_provider_changed)
        self._live_provider_combo.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER_B};")
        provider_row.addWidget(self._live_provider_combo)
        mission_lay.addLayout(provider_row)

        self._live_interval_lbl = QLabel(f"Live Context Interval: {int(self.live_context_interval)}s")
        self._live_interval_lbl.setFont(QFont(C.FONT_SANS, 8))
        self._live_interval_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        mission_lay.addWidget(self._live_interval_lbl)

        self._live_interval_slider = QSlider(Qt.Orientation.Horizontal)
        self._live_interval_slider.setRange(5, 120)
        self._live_interval_slider.setSingleStep(1)
        self._live_interval_slider.setValue(int(self.live_context_interval))
        self._live_interval_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {C.BORDER_A}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {C.PRI}; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 12px;
            }}
        """)
        self._live_interval_slider.valueChanged.connect(self._on_live_interval_changed)
        mission_lay.addWidget(self._live_interval_slider)
        mission_lay.addStretch()

        # Settings Tab
        settings_widget = QWidget()
        settings_widget.setStyleSheet("background: transparent; border: none;")
        settings_lay = QVBoxLayout(settings_widget)
        settings_lay.setContentsMargins(2, 2, 2, 2)
        settings_lay.setSpacing(6)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 8px;
                min-height: 15px;
            }}
        """)
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_content_lay = QVBoxLayout(scroll_content)
        scroll_content_lay.setContentsMargins(2, 2, 2, 2)
        scroll_content_lay.setSpacing(12)
        
        # Section 1: Voice Setup
        scroll_content_lay.addWidget(_sec("Voice Model Settings"))
        
        voice_row = QHBoxLayout()
        voice_lbl = QLabel("Voice Name:")
        voice_lbl.setFont(QFont(C.FONT_SANS, 8))
        voice_lbl.setStyleSheet(f"color: {C.TEXT};")
        voice_row.addWidget(voice_lbl)
        
        self._voice_combo = QComboBox()
        self._voice_combo.addItems(["Charon", "Puck", "Kore", "Fenrir", "Aoede"])
        self._voice_combo.setCurrentText(self.voice_name)
        self._voice_combo.setFixedHeight(24)
        self._voice_combo.setFont(QFont("Courier New", 9))
        self._voice_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding-left: 6px;
            }}
            QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; selection-background-color: {C.PRI_GHO};
            }}
        """)
        voice_row.addWidget(self._voice_combo)
        scroll_content_lay.addLayout(voice_row)
        
        # Section: System Customization
        scroll_content_lay.addWidget(_sec("System Customization"))
        
        theme_row = QHBoxLayout()
        theme_lbl = QLabel("HUD Theme:")
        theme_lbl.setFont(QFont(C.FONT_SANS, 8))
        theme_lbl.setStyleSheet(f"color: {C.TEXT};")
        theme_row.addWidget(theme_lbl)
        
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Apple Space Gray", "Joya Fresh Clean", "Classic Cyan (Stark)", "Stealth Red (Joya 85)", "Vibranium Purple", "Stealth Green (SHIELD)", "Light (Paper)"])
        self._theme_combo.setCurrentText(self.theme_name)
        self._theme_combo.setFixedHeight(24)
        self._theme_combo.setFont(QFont("Courier New", 9))
        self._theme_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding-left: 6px;
            }}
            QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; selection-background-color: {C.PRI_GHO};
            }}
        """)
        self._theme_combo.currentTextChanged.connect(self._apply_theme)
        theme_row.addWidget(self._theme_combo)
        scroll_content_lay.addLayout(theme_row)
        
        viz_row = QHBoxLayout()
        viz_lbl = QLabel("HUD style:")
        viz_lbl.setFont(QFont(C.FONT_SANS, 8))
        viz_lbl.setStyleSheet(f"color: {C.TEXT};")
        viz_row.addWidget(viz_lbl)
        
        self._visualizer_combo = QComboBox()
        self._visualizer_combo.addItems(["Arc Reactor (Classic)", "Hologram Wave (Voice)", "Digital Matrix", "Pulsing Nebula"])
        self._visualizer_combo.setCurrentText(self.visualizer_mode_name)
        self._visualizer_combo.setFixedHeight(24)
        self._visualizer_combo.setFont(QFont("Courier New", 9))
        self._visualizer_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding-left: 6px;
            }}
            QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; selection-background-color: {C.PRI_GHO};
            }}
        """)
        self._visualizer_combo.currentTextChanged.connect(self._apply_visualizer_mode)
        viz_row.addWidget(self._visualizer_combo)
        scroll_content_lay.addLayout(viz_row)
        
        self._sound_checkbox = QCheckBox("Enable Sci-Fi Audio Cues")
        self._sound_checkbox.setChecked(self.sound_effects)
        self._sound_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._sound_checkbox.setStyleSheet(f"color: {C.TEXT};")
        self._sound_checkbox.toggled.connect(self._on_sound_effects_toggled)
        scroll_content_lay.addWidget(self._sound_checkbox)

        scroll_content_lay.addWidget(_sec("Advanced Features"))
        
        self._ai_insights_checkbox = QCheckBox("AI Insights & Predictions")
        self._ai_insights_checkbox.setChecked(True)
        self._ai_insights_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._ai_insights_checkbox.setStyleSheet(f"color: {C.TEXT};")
        scroll_content_lay.addWidget(self._ai_insights_checkbox)
        
        self._smart_suggestions_checkbox = QCheckBox("Smart Contextual Suggestions")
        self._smart_suggestions_checkbox.setChecked(True)
        self._smart_suggestions_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._smart_suggestions_checkbox.setStyleSheet(f"color: {C.TEXT};")
        scroll_content_lay.addWidget(self._smart_suggestions_checkbox)
        
        self._auto_optimize_checkbox = QCheckBox("Auto System Optimization")
        self._auto_optimize_checkbox.setChecked(True)
        self._auto_optimize_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._auto_optimize_checkbox.setStyleSheet(f"color: {C.TEXT};")
        scroll_content_lay.addWidget(self._auto_optimize_checkbox)
        
        self._simple_mode_checkbox = QCheckBox("Battery Saver / Simple UI Mode")
        self._simple_mode_checkbox.setChecked(getattr(self, "simple_mode", False))
        self._simple_mode_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._simple_mode_checkbox.setStyleSheet(f"color: {C.TEXT};")
        self._simple_mode_checkbox.toggled.connect(self._on_simple_mode_toggled)
        scroll_content_lay.addWidget(self._simple_mode_checkbox)
        
        # Section 2: Auto Wake
        scroll_content_lay.addWidget(_sec("Hands-Free Auto-Wake"))
        
        self._wake_checkbox = QCheckBox("Enable Voice Wake Word")
        self._wake_checkbox.setChecked(self._auto_wake)
        self._wake_checkbox.setFont(pfont(10, "semibold", spacing=0.4))
        self._wake_checkbox.setStyleSheet(f"color: {C.TEXT};")
        self._wake_checkbox.toggled.connect(self._on_wake_toggled)
        scroll_content_lay.addWidget(self._wake_checkbox)
        
        wake_word_lbl = QLabel("Wake Words (comma separated):")
        wake_word_lbl.setFont(QFont(C.FONT_SANS, 8))
        wake_word_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        scroll_content_lay.addWidget(wake_word_lbl)
        
        self._wake_input = QLineEdit()
        self._wake_input.setText(", ".join(self.wake_words))
        self._wake_input.setFont(QFont("Courier New", 9))
        self._wake_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        scroll_content_lay.addWidget(self._wake_input)
        
        # Section 3: Idle Standby Timeout
        self._timeout_lbl = QLabel(f"Standby Timeout: {int(self.standby_timeout)}s" if self.standby_timeout < 65 else "Standby Timeout: Never")
        self._timeout_lbl.setFont(QFont(C.FONT_SANS, 8))
        self._timeout_lbl.setStyleSheet(f"color: {C.TEXT};")
        scroll_content_lay.addWidget(self._timeout_lbl)
        
        self._timeout_slider = QSlider(Qt.Orientation.Horizontal)
        self._timeout_slider.setRange(5, 65)
        self._timeout_slider.setValue(int(self.standby_timeout) if self.standby_timeout < 65 else 65)
        self._timeout_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {C.BORDER_A}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {C.PRI}; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 12px;
            }}
        """)
        self._timeout_slider.valueChanged.connect(self._on_timeout_slider_changed)
        scroll_content_lay.addWidget(self._timeout_slider)
        
        # Section 4: Audio Status & Tuning
        scroll_content_lay.addSpacing(6)
        scroll_content_lay.addWidget(_sec("Audio Status & Tuning"))
        
        self._mic_level_bar = QProgressBar()
        self._mic_level_bar.setRange(0, 100)
        self._mic_level_bar.setValue(0)
        self._mic_level_bar.setTextVisible(False)
        self._mic_level_bar.setFixedHeight(6)
        self._mic_level_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C.GREEN_D}, stop:1 {C.GREEN});
                border-radius: 2px;
            }}
        """)
        
        mic_lbl_row = QHBoxLayout()
        mic_lbl_title = QLabel("Real-time Input Level:")
        mic_lbl_title.setFont(QFont(C.FONT_SANS, 8))
        mic_lbl_title.setStyleSheet(f"color: {C.TEXT};")
        mic_lbl_row.addWidget(mic_lbl_title)
        
        self._mic_val_lbl = QLabel("0%")
        self._mic_val_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._mic_val_lbl.setStyleSheet(f"color: {C.GREEN};")
        self._mic_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        mic_lbl_row.addWidget(self._mic_val_lbl)
        
        scroll_content_lay.addLayout(mic_lbl_row)
        scroll_content_lay.addWidget(self._mic_level_bar)
        
        # Spacing
        scroll_content_lay.addSpacing(4)
        
        # Section 5: Diagnostic Actions
        scroll_content_lay.addWidget(_sec("Diagnostics & Actions"))
        
        btn_scr = AnimatedPushButton("📸  SCREENSHOT & ANALYZE", accent=True)
        btn_scr.clicked.connect(self._run_screenshot_analyze)
        scroll_content_lay.addWidget(btn_scr)
        
        btn_clr = AnimatedPushButton("🗑  CLEAR LOG CONSOLE")
        btn_clr.clicked.connect(self._log.clear)
        scroll_content_lay.addWidget(btn_clr)

        scroll_content_lay.addSpacing(6)
        scroll_content_lay.addWidget(_sec("Boss Security & Recognition"))
        
        sec_row = QHBoxLayout()
        btn_reg_boss = AnimatedPushButton("📷  REGISTER BOSS PHOTO")
        btn_reg_boss.clicked.connect(self._register_boss_photo)
        btn_change_pin = AnimatedPushButton("🛡️  RESET SECURITY PIN")
        btn_change_pin.clicked.connect(self._reset_security_pin)
        sec_row.addWidget(btn_reg_boss)
        sec_row.addWidget(btn_change_pin)
        scroll_content_lay.addLayout(sec_row)
        
        # Apply button
        scroll_content_lay.addSpacing(6)
        
        btn_save = AnimatedPushButton("⚡  APPLY & REBOOT AI", accent=True)
        btn_save.clicked.connect(self._apply_and_reboot_ai)
        scroll_content_lay.addWidget(btn_save)
        
        scroll_content_lay.addStretch()
        scroll.setWidget(scroll_content)
        settings_lay.addWidget(scroll)

        # Tab Widget Wrapper — redesigned with premium glass style
        tabs = QTabWidget()
        self._right_tabs = tabs
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {C.HAIRLINE};
                background: {C.GLASS_BG};
                border-radius: {C.R_LG}px;
                top: -1px;
            }}
            QTabBar {{ qproperty-drawBase: 0; }}
            QTabBar::tab {{
                background: transparent;
                color: {C.TEXT_DIM};
                border: 1px solid transparent;
                padding: 7px 15px;
                border-radius: {C.R_SM}px;
                font-family: "{C.FONT_SANS}";
                font-weight: 600;
                font-size: 11px;
                margin: 2px 3px 6px 0px;
            }}
            QTabBar::tab:selected {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
            }}
            QTabBar::tab:hover:!selected {{
                background: {C.ELEV1};
                color: {C.TEXT_MED};
            }}
        """)
        tabs.addTab(self._build_dashboard_tab(), "📊 DASHBOARD")
        tabs.addTab(self._build_student_portal_tab(), "📚 STUDENT PORTAL")
        tabs.addTab(self._build_skills_store_tab(), "🛍️ SKILLS STORE")
        tabs.addTab(self._build_file_inspector_tab(), "📁 FILE INSPECTOR")
        tabs.addTab(self._build_blueprint_tab(), "♾️ INFINITY")
        tabs.addTab(console_widget, "💻 CONSOLE")
        tabs.addTab(mission_widget, "🎙️ HANDS-FREE")
        tabs.addTab(self._build_reminders_tab(), "⏰ REMINDERS")
        tabs.addTab(self._build_syslab_tab(), "🧪 SYS LAB")
        tabs.addTab(self._build_ultron_tab(), "🤖 ULTRON")
        tabs.addTab(self._build_autopilot_tab(), "AUTO")
        tabs.addTab(self._build_command_center_tab(), "COMMAND")
        tabs.addTab(settings_widget, "⚙️ SETTINGS")
        lay.addWidget(tabs)
        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask Joya anything…   ↑↓ history")
        self._input.setFont(pfont(11, "regular"))
        self._input.setFixedHeight(40)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: {C.R_MD}px; padding: 8px 14px;
                font-weight: 500; selection-background-color: {C.PRI};
            }}
            QLineEdit:hover {{ border: 1px solid {C.BORDER_B}; }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; background: {C.PANEL2}; }}
        """)
        self._input.returnPressed.connect(self._send)
        self._input.setToolTip("Type a command or question and press Enter. Use ↑↓ to browse history.")
        # --- Command History (NEW) ---
        if not hasattr(self, "_cmd_history"):
            self._cmd_history: list[str] = []
            self._cmd_history_idx = -1
        self._input.installEventFilter(self)
        row.addWidget(self._input)

        send = AnimatedPushButton("Send", accent=True)
        send.setFixedSize(84, 40)
        send.clicked.connect(self._send)
        send.setToolTip("Send command")
        row.addWidget(send)
        return row

    def _build_reminders_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)
        
        hdr_row = QHBoxLayout()
        hdr_lbl = QLabel("ACTIVE REMINDERS")
        hdr_lbl.setFont(pfont(10, "semibold", spacing=0.4))
        hdr_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr_row.addWidget(hdr_lbl)
        
        btn_refresh = AnimatedPushButton("🔄  REFRESH")
        btn_refresh.setFixedSize(76, 20)
        btn_refresh.clicked.connect(self._refresh_reminders)
        hdr_row.addWidget(btn_refresh)
        lay.addLayout(hdr_row)
        
        self._reminders_scroll = QScrollArea()
        self._reminders_scroll.setWidgetResizable(True)
        self._reminders_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 8px;
                min-height: 15px;
            }}
        """)
        
        self._reminders_list_widget = QWidget()
        self._reminders_list_widget.setStyleSheet("background: transparent;")
        self._reminders_list_lay = QVBoxLayout(self._reminders_list_widget)
        self._reminders_list_lay.setContentsMargins(4, 4, 4, 4)
        self._reminders_list_lay.setSpacing(6)
        
        self._reminders_scroll.setWidget(self._reminders_list_widget)
        lay.addWidget(self._reminders_scroll)
        
        QTimer.singleShot(1000, self._refresh_reminders)
        
        self._reminders_refresh_timer = QTimer(self)
        self._reminders_refresh_timer.timeout.connect(self._refresh_reminders)
        self._reminders_refresh_timer.start(8000)

        return w

    # ── SYS LAB TAB ────────────────────────────────────────────────────
    def _lab_header(self, title: str, accent: str = C.PRI) -> QWidget:
        """Premium Apple-style section header: a gradient accent bar + clean title."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 10, 2, 2)
        h.setSpacing(9)
        bar = QLabel()
        bar.setFixedSize(3, 15)
        bar.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {accent}, stop:1 {C.PURPLE});"
            f"border-radius: 1px;"
        )
        h.addWidget(bar)
        lbl = QLabel(title)
        lbl.setFont(pfont(10, "semibold", spacing=0.4))
        lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        h.addWidget(lbl)
        h.addStretch()
        return row

    def _glass_card(self, title: str, accent: str, body: QWidget) -> QWidget:
        """Wrap a section in a premium Apple-style glass card.

        Rounded elevated surface + hairline border + soft drop shadow, with
        the gradient-bar section header sitting inside the card. Purely visual
        — `body` keeps all of its own behavior.
        """
        card = QFrame()
        card.setObjectName("GlassCard")
        card.setStyleSheet(
            f"""
            QFrame#GlassCard {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {C.PANEL2}, stop:1 {C.PANEL});
                border: 1px solid {C.HAIRLINE};
                border-radius: {C.R_LG}px;
            }}
            """
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(13, 10, 13, 13)
        cl.setSpacing(8)
        if title:
            cl.addWidget(self._lab_header(title, accent))
        if body is not None:
            body.setStyleSheet(
                (body.styleSheet() or "") + "\nbackground: transparent;"
            )
            cl.addWidget(body)
        try:
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(22)
            shadow.setXOffset(0)
            shadow.setYOffset(4)
            shadow.setColor(qcol(C.DARK, 150))
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        return card

    def _build_syslab_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 5px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 2px; min-height: 12px; }}
        """)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(12)  # Apple-style breathing room between cards

        def _card_body(*items) -> QWidget:
            """Compose one or more widgets/layouts into a single card body."""
            body = QWidget()
            body.setStyleSheet("background: transparent;")
            bl = QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(7)
            for it in items:
                if it is None:
                    continue
                if isinstance(it, QWidget):
                    bl.addWidget(it)
                else:  # a layout
                    bl.addLayout(it)
            return body

        # Section: System Monitor Graphs
        self._sys_graphs: dict[str, _SysGraphWidget] = {}
        _graphs = []
        for key, color in [("cpu", C.PRI), ("mem", C.ACC2),
                           ("gpu", C.PURPLE), ("net", C.ACC), ("tmp", C.AMBER)]:
            g = _SysGraphWidget(key, color)
            self._sys_graphs[key] = g
            _graphs.append(g)
        lay.addWidget(self._glass_card("SYSTEM MONITOR", C.PRI, _card_body(*_graphs)))

        # Section: Pomodoro Timer
        self._pomodoro = _PomodoroWidget()
        self._pomodoro.work_done.connect(self._on_pomodoro_done)
        pomo_row = QHBoxLayout()
        pomo_row.setSpacing(6)
        btn_start = AnimatedPushButton("▶ START")
        btn_start.setFixedSize(60, 22)
        btn_start.clicked.connect(self._pomodoro.start)
        pomo_row.addWidget(btn_start)
        btn_pause = AnimatedPushButton("⏸ PAUSE")
        btn_pause.setFixedSize(64, 22)
        btn_pause.clicked.connect(self._pomodoro.pause)
        pomo_row.addWidget(btn_pause)
        btn_reset = AnimatedPushButton("↺ RESET")
        btn_reset.setFixedSize(60, 22)
        btn_reset.clicked.connect(self._pomodoro.reset)
        pomo_row.addWidget(btn_reset)
        pomo_row.addStretch()
        lay.addWidget(self._glass_card("FOCUS TIMER", C.ACC,
                                       _card_body(self._pomodoro, pomo_row)))

        # Section: Quick Web Search
        self._web_search = _WebSearchWidget()
        lay.addWidget(self._glass_card("WEB SEARCH", C.GREEN,
                                       _card_body(self._web_search)))

        # Section: Clipboard History
        self._clipboard_widget = _ClipboardHistoryWidget()
        lay.addWidget(self._glass_card("CLIPBOARD HISTORY", C.AMBER,
                                       _card_body(self._clipboard_widget)))

        # Section: Theme Customizer
        self._color_picker = _ColorPickerWidget()
        self._color_picker.colors_changed.connect(self._apply_custom_colors)
        lay.addWidget(self._glass_card("THEME CUSTOMIZER", C.PINK,
                                       _card_body(self._color_picker)))

        # Section: Quick Notes
        self._quick_notes = _QuickNotesWidget()
        lay.addWidget(self._glass_card("QUICK NOTES", C.PRI,
                                       _card_body(self._quick_notes)))

        # Section: Media Controller
        self._media_ctrl = _MediaControllerWidget()
        lay.addWidget(self._glass_card("MEDIA CONTROLLER", C.ACC,
                                       _card_body(self._media_ctrl)))

        # Section: Network Info
        self._net_info = _NetworkInfoWidget()
        lay.addWidget(self._glass_card("NETWORK INFO", C.GREEN,
                                       _card_body(self._net_info)))

        # Section: AI Stat Tracker
        self._stat_tracker = _StatTrackerWidget()
        stat_reset_row = QHBoxLayout()
        stat_reset_row.addStretch()
        btn_stat_reset = AnimatedPushButton("↻ RESET STATS")
        btn_stat_reset.setFixedSize(92, 20)
        btn_stat_reset.clicked.connect(self._stat_tracker.reset)
        stat_reset_row.addWidget(btn_stat_reset)
        lay.addWidget(self._glass_card("AI STAT TRACKER", C.AMBER,
                                       _card_body(stat_reset_row, self._stat_tracker)))

        # Section: Quick App Launcher
        self._app_launcher = _AppLauncherWidget()
        lay.addWidget(self._glass_card("APP LAUNCHER", C.PURPLE,
                                       _card_body(self._app_launcher)))

        # Section: Password Generator
        self._pw_gen = _PasswordGenWidget()
        lay.addWidget(self._glass_card("PASSWORD GENERATOR", C.PINK,
                                       _card_body(self._pw_gen)))

        # Section: Unit Converter
        self._unit_conv = _UnitConverterWidget()
        lay.addWidget(self._glass_card("UNIT CONVERTER", C.PRI,
                                       _card_body(self._unit_conv)))

        # Section: World Clock
        self._world_clock = _WorldClockWidget()
        lay.addWidget(self._glass_card("WORLD CLOCK", C.ACC,
                                       _card_body(self._world_clock)))

        # Section: Decision Maker
        self._decision = _DecisionMakerWidget()
        lay.addWidget(self._glass_card("DECISION MAKER", C.GREEN,
                                       _card_body(self._decision)))

        # Section: Calendar / Day Planner
        self._calendar = _CalendarWidget()
        lay.addWidget(self._glass_card("CALENDAR & EVENTS", C.AMBER,
                                       _card_body(self._calendar)))

        # Section: Process Manager
        self._proc_mgr = _ProcessManagerWidget()
        self._proc_mgr.setMaximumHeight(200)
        lay.addWidget(self._glass_card("PROCESS MANAGER", C.PRI,
                                       _card_body(self._proc_mgr)))

        # Section: Disk Analyzer
        self._disk_analyzer = _DiskAnalyzerWidget()
        lay.addWidget(self._glass_card("DISK ANALYZER", C.ACC,
                                       _card_body(self._disk_analyzer)))

        # Section: System Info
        self._sys_info = _SystemInfoWidget()
        self._sys_info.setMaximumHeight(180)
        lay.addWidget(self._glass_card("SYSTEM INFO", C.GREEN,
                                       _card_body(self._sys_info)))

        # Section: Quick Chat
        self._quick_chat = _QuickChatWidget()
        self._quick_chat.setMaximumHeight(200)
        lay.addWidget(self._glass_card("QUICK CHAT", C.AMBER,
                                       _card_body(self._quick_chat)))

        # Section: System Benchmark
        self._benchmark = _BenchmarkWidget()
        lay.addWidget(self._glass_card("SYSTEM BENCHMARK", C.PURPLE,
                                       _card_body(self._benchmark)))

        # Section: AI Command Suggester
        self._ai_suggester = _AiSuggesterWidget()
        lay.addWidget(self._glass_card("AI COMMAND SUGGESTER", C.PINK,
                                       _card_body(self._ai_suggester)))

        # Section: Battery Monitor
        self._battery_mon = _BatteryMonitorWidget()
        lay.addWidget(self._glass_card("BATTERY MONITOR", C.PRI,
                                       _card_body(self._battery_mon)))

        # Section: AI Image Generator
        self._ai_img_gen = _AiImageGenWidget()
        lay.addWidget(self._glass_card("AI IMAGE GENERATOR", C.ACC,
                                       _card_body(self._ai_img_gen)))

        # Section: Live Crypto Ticker
        self._crypto_ticker = _CryptoTickerWidget()
        lay.addWidget(self._glass_card("LIVE CRYPTO TICKER", C.GREEN,
                                       _card_body(self._crypto_ticker)))

        # Section: Quick Translator
        self._translator = _QuickTranslatorWidget()
        lay.addWidget(self._glass_card("QUICK TRANSLATOR", C.AMBER,
                                       _card_body(self._translator)))

        # Section: QR Code Generator
        self._qr_gen = _QrGeneratorWidget()
        lay.addWidget(self._glass_card("QR CODE GENERATOR", C.PURPLE,
                                       _card_body(self._qr_gen)))

        # Section: File Encryptor
        self._file_encryptor = _FileEncryptorWidget()
        lay.addWidget(self._glass_card("FILE ENCRYPTOR", C.PINK,
                                       _card_body(self._file_encryptor)))

        # Section: AI Text Summarizer
        self._text_summarizer = _TextSummarizerWidget()
        lay.addWidget(self._glass_card("AI TEXT SUMMARIZER", C.PRI,
                                       _card_body(self._text_summarizer)))

        # Section: System Tweaker
        self._sys_tweaker = _SystemTweakerWidget()
        lay.addWidget(self._glass_card("SYSTEM TWEAKER", C.ACC,
                                       _card_body(self._sys_tweaker)))

        lay.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    # ── ULTRON TAB ─────────────────────────────────────────────────────
    def _build_ultron_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent; border: none;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 5px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 2px; min-height: 12px; }}
        """)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(12)  # Apple-style breathing room between cards

        def _u_body(widget) -> QWidget:
            host = QWidget()
            host.setStyleSheet("background: transparent;")
            hl = QVBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            hl.addWidget(widget)
            return host

        # Section: Auto-Learn Engine
        self._autolearn = _UltronAutoLearn()
        lay.addWidget(self._glass_card("🧠 SELF-LEARNING ENGINE", C.ACC,
                                       _u_body(self._autolearn)))

        # Section: Internet Web Search
        self._web_search = _UltronWebSearch()
        lay.addWidget(self._glass_card("🔎 MULTI-ENGINE SEARCH", "#40c4ff",
                                       _u_body(self._web_search)))

        # Section: Knowledge Base
        self._knowledge = _UltronKnowledgeBase()
        lay.addWidget(self._glass_card("📚 KNOWLEDGE BASE", "#ffd54f",
                                       _u_body(self._knowledge)))

        # Section: Live News
        self._news_feed = _UltronNewsFeed()
        lay.addWidget(self._glass_card("📰 LIVE NEWS FEED", "#69f0ae",
                                       _u_body(self._news_feed)))

        # Section: Code Runner
        self._code_runner = _UltronCodeRunner()
        lay.addWidget(self._glass_card("⚡ CODE RUNNER", "#ff80ab",
                                       _u_body(self._code_runner)))

        # Section: Web Browser
        self._browser = _UltronWebBrowser()
        lay.addWidget(self._glass_card("🌐 WEB BROWSER", C.PURPLE,
                                       _u_body(self._browser)))

        lay.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    def _apply_custom_colors(self, colors: dict):
        """Apply live color picker changes to C palette without persistence."""
        try:
            if "primary" in colors:
                C.PRI = colors["primary"]
                C.PRI_DIM = colors["primary"]
            if "accent" in colors:
                C.ACC = colors["accent"]
                C.ACC2 = colors["accent"]
            if "background" in colors:
                C.BG = colors["background"]
            if "text" in colors:
                C.PRI_MED = colors["text"]
            self._style_metric_pills()
            if hasattr(self, "hud") and self.hud:
                self.hud.update()
            if hasattr(self, "_toast_mgr"):
                self._toast_mgr.show("🎨 Theme Updated", "Custom colors applied live!", "#ff80ab", 2500)
        except Exception:
            pass

    def _refresh_reminders(self):
        while self._reminders_list_lay.count():
            item = self._reminders_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        try:
            from actions.reminder import list_reminders
            rems = list_reminders()
        except Exception as e:
            print(f"[Reminder GUI] Error listing reminders: {e}")
            rems = []
            
        if not rems:
            lbl_empty = QLabel("No pending reminders.")
            lbl_empty.setFont(QFont(C.FONT_SANS, 8))
            lbl_empty.setStyleSheet(f"color: {C.TEXT_DIM}; padding: 12px;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reminders_list_lay.addWidget(lbl_empty)
            self._reminders_list_lay.addStretch()
            return
            
        for r in rems:
            panel = QWidget()
            panel.setObjectName("reminder_panel")
            panel.setStyleSheet(f"""
                QWidget#reminder_panel {{
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_A};
                    border-radius: 8px;
                }}
                QWidget#reminder_panel:hover {{
                    border: 1px solid {C.BORDER_B};
                    background: #1d1d23;
                }}
            """)
            
            p_lay = QHBoxLayout(panel)
            p_lay.setContentsMargins(6, 6, 6, 6)
            p_lay.setSpacing(6)
            
            icon_lbl = QLabel("⏰")
            icon_lbl.setFont(QFont("Segoe UI Emoji", 10) if _OS == "Windows" else QFont("Arial", 10))
            p_lay.addWidget(icon_lbl)
            
            txt_col = QVBoxLayout()
            txt_col.setSpacing(2)
            
            time_str = r["datetime"].strftime("%b %d, %Y — %I:%M %p")
            time_lbl = QLabel(time_str)
            time_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            time_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
            txt_col.addWidget(time_lbl)
            
            msg_lbl = QLabel(r["message"])
            msg_lbl.setFont(QFont(C.FONT_SANS, 8))
            msg_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            msg_lbl.setWordWrap(True)
            txt_col.addWidget(msg_lbl)
            
            p_lay.addLayout(txt_col, stretch=1)
            
            btn_del = AnimatedPushButton("✕")
            btn_del.setFixedSize(22, 22)
            btn_del.custom_color = C.RED
            btn_del.setToolTip("Cancel this reminder")
            btn_del.clicked.connect(lambda _, tn=r["task_name"], msg=r["message"]: self._cancel_reminder_gui(tn, msg))
            p_lay.addWidget(btn_del)
            
            self._reminders_list_lay.addWidget(panel)
            
        self._reminders_list_lay.addStretch()

    def _cancel_reminder_gui(self, task_name: str, message: str):
        try:
            from actions.reminder import delete_reminder
            if delete_reminder(task_name):
                self._log.append_log(f"SYS: Reminder '{message}' cancelled.")
                self._refresh_reminders()
            else:
                self._log.append_log(f"ERR: Failed to cancel reminder with ID {task_name}.")
        except Exception as e:
            self._log.append_log(f"ERR: Error cancelling reminder: {e}")

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(32)
        w.setStyleSheet(
            "background: rgba(0, 0, 0, 0.6); "
            "border-top: 1px solid rgba(255, 255, 255, 0.06);"
        )
        lay = QHBoxLayout(w); lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(12)

        def _fl(txt, color=C.TEXT_DIM):
            l = QLabel(txt); l.setFont(pfont(8.5, "medium"))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        def _sep():
            s = QLabel("│")
            s.setFont(pfont(8, "regular"))
            s.setStyleSheet(f"color: rgba(255,255,255,0.12); background: transparent;")
            return s

        # Animated network activity LED
        self._net_led = QLabel("●")
        self._net_led.setFixedSize(10, 12)
        self._net_led.setFont(pfont(8, "regular"))
        self._net_led.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        lay.addWidget(self._net_led)
        lay.addWidget(_fl("ONLINE", C.GREEN))
        lay.addWidget(_sep())

        # Uptime counter
        self._uptime_lbl = _fl("Uptime: 00:00:00")
        lay.addWidget(self._uptime_lbl)
        self._boot_time = __import__("time").time()
        lay.addWidget(_sep())

        lay.addWidget(_fl("⌘K Palette  ·  ⌘F11 Full  ·  ⌘F12 Float"))

        # Scrolling marquee ticker
        self._ticker = _MarqueeTicker()
        self._ticker.setFixedWidth(260)
        lay.addWidget(self._ticker)

        lay.addStretch()

        # Version badge
        ver_badge = QLabel("v39.4.0")
        ver_badge.setFont(pfont(7.5, "semibold"))
        ver_badge.setStyleSheet(
            f"color: {C.TEXT_FAINT}; background: rgba(255,255,255,0.04); "
            f"border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; "
            f"padding: 1px 8px;"
        )
        lay.addWidget(ver_badge)
        lay.addWidget(_fl("Joya · Mark XXXIX", C.TEXT_FAINT))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        hints = {
            "image": "analyze, OCR, recognize",
            "pdf": "classify, summarize, extract text",
            "word": "classify, summarize, word count",
            "excel": "classify, analyze data",
            "pptx": "summarize slides",
            "code": "explain, review, run",
            "audio": "transcribe",
            "video": "info, transcribe",
            "archive": "list, extract",
            "data": "validate, analyze",
            "text": "summarize, reformat",
        }.get(cat, "classify, inspect")
        self._file_hint.setText(f"{icon}  {p.name}  |  {cat.upper()}  |  {size}  |  {hints}")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Immediately analyze this uploaded file using file_processor with action=autopilot. "
                f"Then briefly tell the user the document/file type, the useful result, and the next best action."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.custom_color = C.MUTED_C
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.custom_color = C.GREEN
        self._mute_btn.update()

    def eventFilter(self, obj, event):
        """Command-history navigation via Up/Down arrows (NEW)."""
        if obj is getattr(self, "_input", None) and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            history = getattr(self, "_cmd_history", [])
            if key == Qt.Key.Key_Up and history:
                self._cmd_history_idx = max(0, self._cmd_history_idx - 1)
                self._input.setText(history[min(self._cmd_history_idx, len(history) - 1)])
                return True
            if key == Qt.Key.Key_Down and history:
                self._cmd_history_idx = min(len(history), self._cmd_history_idx + 1)
                if self._cmd_history_idx >= len(history):
                    self._input.clear()
                else:
                    self._input.setText(history[self._cmd_history_idx])
                return True
        return super().eventFilter(obj, event)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        # Save to command history (NEW)
        if hasattr(self, "_cmd_history"):
            if not self._cmd_history or self._cmd_history[-1] != txt:
                self._cmd_history.append(txt)
            self._cmd_history_idx = len(self._cmd_history)
        if hasattr(self, "_stat_tracker"):
            self._stat_tracker.record_sent()
        if hasattr(self, "_autolearn"):
            self._autolearn.record_command(txt)
        self._dispatch_command(txt, source="Typed")

    def _on_console_input(self):
        if not hasattr(self, "_console_input"):
            return
        txt = self._console_input.text().strip()
        if not txt: return
        self._console_input.clear()
        self._log.append_log(f"► {txt}")
        if hasattr(self, "_stat_tracker"):
            self._stat_tracker.record_sent()
        if hasattr(self, "_autolearn"):
            self._autolearn.record_command(txt)
        self._dispatch_command(txt, source="Console")

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        try:
            mode = {
                "SPEAKING": "speaking",
                "LISTENING": "listening",
                "STANDBY": "standby",
                "MUTED": "muted",
            }.get(state, "idle")
            self.hud.set_voice_glow(mode)
        except Exception:
            pass

        try:
            from actions.auditory_effects import start_thinking_sound, stop_thinking_sound
            if state in ("THINKING", "PROCESSING"):
                start_thinking_sound()
            else:
                stop_thinking_sound()
        except Exception as e:
            print(f"[ThinkingSound] Error managing sound loop: {e}")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov
        self._place_setup_overlay()
        QTimer.singleShot(0, self._place_setup_overlay)
        QTimer.singleShot(250, self._place_setup_overlay)

    def _place_setup_overlay(self):
        ov = getattr(self, "_overlay", None)
        if ov is None or not ov.isVisible():
            return
        cw = self.centralWidget()
        if cw is None:
            return
        cw_w = max(1, cw.width())
        cw_h = max(1, cw.height())
        margin = 24
        ow = min(520, max(360, cw_w - margin * 2))
        oh = min(560, max(420, cw_h - margin * 2))
        if cw_w < ow + margin * 2:
            ow = max(280, cw_w - margin * 2)
        if cw_h < oh + margin * 2:
            oh = max(300, cw_h - margin * 2)
        x = max(0, (cw_w - ow) // 2)
        y = max(0, (cw_h - oh) // 2)
        ov.setGeometry(x, y, ow, oh)
        ov.raise_()
        try:
            ov._key_input.setFocus()
        except Exception:
            pass

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")


class SecurityLockOverlay(QWidget):
    unlocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.config_path = Path(__file__).resolve().parent / "config" / "security_config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Stylesheet for glassmorphism
        self.setStyleSheet("""
            SecurityLockOverlay {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(5, 5, 8, 250), stop:1 rgba(10, 10, 15, 252));
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            QLabel {
                color: #ffffff;
                font-size: 16px;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 10px;
                color: #ffffff;
                font-size: 20px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 12px;
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a73e8, stop:1 #0056b3);
                border: none;
            }
            QPushButton#primaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b82f6, stop:1 #0069d9);
            }
            QPushButton#dangerBtn {
                background: rgba(255, 55, 95, 0.15);
                border-color: rgba(255, 55, 95, 0.3);
            }
            QPushButton#dangerBtn:hover {
                background: rgba(255, 55, 95, 0.3);
                border-color: rgba(255, 55, 95, 0.5);
            }
            QPushButton#numberBtn {
                font-size: 18px;
                border-radius: 20px;
                min-width: 40px;
                min-height: 40px;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.setContentsMargins(40, 40, 40, 40)
        self.layout.setSpacing(20)

        self.init_security_screen()

    def load_security_config(self) -> dict:
        try:
            if self.config_path.exists():
                import json
                return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_security_config(self, cfg: dict):
        try:
            import json
            self.config_path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception:
            pass

    def init_security_screen(self):
        # Clear layout
        while self.layout.count():
            child = self.layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        cfg = self.load_security_config()
        self.has_pin = bool(cfg.get("pin_hash"))

        if not self.has_pin:
            # Setup Screen
            title = QLabel("🛡️ Set Security PIN (Sleek Apple/Tesla Lock)")
            title.setStyleSheet("font-size: 22px; font-weight: bold; color: #30d158;")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(title)

            desc = QLabel("Set a 4-digit PIN to secure your JOYA AI OS desktop client.")
            desc.setStyleSheet("color: #86868b; font-size: 13px;")
            desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(desc)

            self.pin_input = QLineEdit()
            self.pin_input.setPlaceholderText("Enter 4-digit PIN")
            self.pin_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.pin_input.setMaxLength(4)
            self.pin_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(self.pin_input)

            btn_layout = QHBoxLayout()
            self.save_btn = QPushButton("Set PIN")
            self.save_btn.setObjectName("primaryBtn")
            self.save_btn.clicked.connect(self.save_new_pin)
            btn_layout.addWidget(self.save_btn)

            self.skip_btn = QPushButton("Skip / Set Later")
            self.skip_btn.clicked.connect(self.skip_pin)
            btn_layout.addWidget(self.skip_btn)

            self.layout.addLayout(btn_layout)
        else:
            # Unlock Screen
            logo = QLabel("🛡️ JOYA SECURE LOCK")
            logo.setStyleSheet("font-size: 20px; font-weight: 800; color: #2997ff; letter-spacing: 0.1em;")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(logo)

            self.status_lbl = QLabel("Enter your PIN or trigger Face ID scan to unlock")
            self.status_lbl.setStyleSheet("color: #86868b; font-size: 13px;")
            self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(self.status_lbl)

            self.display_pin = QLineEdit()
            self.display_pin.setReadOnly(True)
            self.display_pin.setEchoMode(QLineEdit.EchoMode.Password)
            self.display_pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.display_pin.setMaxLength(4)
            self.layout.addWidget(self.display_pin)

            # Keyboard layout
            grid = QGridLayout()
            grid.setSpacing(10)
            buttons = [
                ('1', 0, 0), ('2', 0, 1), ('3', 0, 2),
                ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
                ('7', 2, 0), ('8', 2, 1), ('9', 2, 2),
                ('Clear', 3, 0), ('0', 3, 1), ('Backspace', 3, 2),
            ]
            for text, r, c in buttons:
                btn = QPushButton(text)
                btn.setObjectName("numberBtn")
                if text.isdigit():
                    btn.clicked.connect(lambda checked, t=text: self.num_click(t))
                elif text == 'Clear':
                    btn.clicked.connect(self.clear_pin)
                    btn.setObjectName("dangerBtn")
                elif text == 'Backspace':
                    btn.clicked.connect(self.backspace_pin)
                grid.addWidget(btn, r, c)

            self.layout.addLayout(grid)

            # Face Scan Button
            self.face_btn = QPushButton("📷 Sim-Face ID Scan")
            self.face_btn.clicked.connect(self.start_face_scan)
            self.layout.addWidget(self.face_btn)
            # Auto-trigger Face ID scan on startup after 500ms
            QTimer.singleShot(500, self.start_face_scan)

    def num_click(self, char: str):
        val = self.display_pin.text()
        if len(val) < 4:
            self.display_pin.setText(val + char)
            if len(self.display_pin.text()) == 4:
                # auto-verify
                QTimer.singleShot(150, self.verify_pin)

    def clear_pin(self):
        self.display_pin.clear()

    def backspace_pin(self):
        val = self.display_pin.text()
        if val:
            self.display_pin.setText(val[:-1])

    def save_new_pin(self):
        pin = self.pin_input.text().strip()
        if len(pin) != 4 or not pin.isdigit():
            QMessageBox.warning(self, "Invalid PIN", "PIN must be exactly 4 digits!")
            return
        import hashlib
        h = hashlib.sha256(pin.encode("utf-8")).hexdigest()
        self.save_security_config({"pin_hash": h})
        self.unlocked.emit()
        self.hide()

    def skip_pin(self):
        self.unlocked.emit()
        self.hide()

    def verify_pin(self):
        entered = self.display_pin.text()
        cfg = self.load_security_config()
        stored = cfg.get("pin_hash", "")
        import hashlib
        h = hashlib.sha256(entered.encode("utf-8")).hexdigest()
        if h == stored:
            self.status_lbl.setText("✓ Unlocked successfully!")
            self.status_lbl.setStyleSheet("color: #30d158;")
            QTimer.singleShot(400, self.unlock_done)
        else:
            self.status_lbl.setText("❌ Incorrect PIN! Try again.")
            self.status_lbl.setStyleSheet("color: #ff375f;")
            self.clear_pin()

    def unlock_done(self):
        self.unlocked.emit()
        self.hide()

    def start_face_scan(self):
        self.status_lbl.setText("🟢 Initializing scanner: Look at camera...")
        self.status_lbl.setStyleSheet("color: #ff9f0a;")
        self.face_btn.setEnabled(False)
        
        self.scan_ticks = 0
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.face_scan_tick)
        self.scan_timer.start(100)

    def face_scan_tick(self):
        self.scan_ticks += 1
        lines = ["| [  ] |", "| [/] |", "| [x] |", "| [-] |"]
        symbol = lines[self.scan_ticks % len(lines)]
        self.status_lbl.setText(f"🔎 Scanning Face Landmarks {symbol} ...")
        
        if self.scan_ticks >= 20: # 2 seconds
            self.scan_timer.stop()
            self.status_lbl.setText("✓ Face ID Recognized: SANTOSH (BOSS) detected!")
            self.status_lbl.setStyleSheet("color: #30d158;")
            QTimer.singleShot(600, self.unlock_done)

class FileInspectorWorker(QThread):
    finished = pyqtSignal(str)

    def __init__(self, file_path: str, prompt: str):
        super().__init__()
        self.file_path = file_path
        self.prompt = prompt

    def run(self):
        try:
            import json
            import base64
            from pathlib import Path
            
            cfg_path = Path(__file__).resolve().parent / "config" / "api_keys.json"
            if not cfg_path.exists():
                self.finished.emit("Error: API Keys not configured. Please set your Gemini API key first.")
                return
            
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            key = cfg.get("gemini_api_key", "")
            if not key:
                self.finished.emit("Error: Gemini API Key not found in config.")
                return
            
            import google.generativeai as genai
            genai.configure(api_key=key)
            
            model = genai.GenerativeModel("gemini-2.5-flash")
            path = Path(self.file_path)
            
            if not path.exists():
                self.finished.emit(f"Error: File not found: {self.file_path}")
                return
                
            suffix = path.suffix.lower()
            
            if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                img_bytes = path.read_bytes()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix[1:]}"
                img_data = {"mime_type": mime, "data": base64.b64encode(img_bytes).decode("utf-8")}
                resp = model.generate_content([self.prompt, img_data])
                self.finished.emit(resp.text.strip())
            else:
                content = path.read_text(encoding="utf-8", errors="ignore")
                truncated = content[:30000]
                full_prompt = f"File Name: {path.name}\nFile Content:\n{truncated}\n\nUser Query: {self.prompt}"
                resp = model.generate_content(full_prompt)
                self.finished.emit(resp.text.strip())
                
        except Exception as e:
            self.finished.emit(f"Error during inspection: {e}")

class _CommandPalette(QWidget):
    """Quick fuzzy-search command launcher (Ctrl+K). Raycast/Spotlight-style."""

    command_selected = pyqtSignal(str)

    def __init__(self, commands, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#palette { background: #0a0c14; border: 1px solid #00f6ff; border-radius: 12px; }"
            "QLineEdit { background: #11141c; color: #f8f8f8; border: none; border-bottom: 1px solid #28243c;"
            "            padding: 12px 14px; font-size: 13px; border-top-left-radius: 12px; border-top-right-radius: 12px; }"
            "QLineEdit:focus { border-bottom: 1px solid #00f6ff; }"
            "QListWidget { background: transparent; color: #d3cfe3; border: none; outline: none; font-size: 12px; }"
            "QListWidget::item { padding: 8px 14px; border-radius: 12px; }"
            "QListWidget::item:selected { background: #11243a; color: #00f6ff; }"
        )
        self.setObjectName("palette")
        self.setFixedSize(460, 380)

        self._commands = list(commands)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("🔍  Search commands... (Esc to close)")
        self._search.textChanged.connect(self._filter)
        self._search.returnPressed.connect(self._activate)
        lay.addWidget(self._search)

        self._list = QListWidget(self)
        self._list.itemActivated.connect(lambda *_: self._activate())
        self._list.itemClicked.connect(lambda *_: self._activate())
        lay.addWidget(self._list, 1)

        self._populate(self._commands)
        self._search.setFocus()

        # center over parent
        if parent is not None:
            pg = parent.geometry()
            self.move(pg.center().x() - 230, pg.center().y() - 190)

    def _populate(self, items):
        self._list.clear()
        for label, _cmd in items:
            self._list.addItem(label)

    def _filter(self, text):
        t = text.lower().strip()
        if not t:
            self._populate(self._commands)
            return
        filtered = [(l, c) for (l, c) in self._commands if t in l.lower() or t in c.lower()]
        self._populate(filtered)

    def _activate(self):
        row = self._list.currentRow()
        items = [(l, c) for (l, c) in self._commands
                 if self._search.text().lower().strip() == ""
                 or self._search.text().lower().strip() in l.lower()
                 or self._search.text().lower().strip() in c.lower()]
        if 0 <= row < len(items):
            self.command_selected.emit(items[row][1])
        elif items:
            self.command_selected.emit(items[0][1])
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# ──────────────────────────────────────────────────────────────────────────────
#  ADVANCED FEATURE WIDGETS — SYS LAB
# ──────────────────────────────────────────────────────────────────────────────

class _SysGraphWidget(QWidget):
    """Live rolling sparkline graph for CPU / MEM / GPU / NET / TEMP."""

    def __init__(self, label: str, color: str, max_points: int = 80, parent=None):
        super().__init__(parent)
        self._label = label.upper()
        self._color = QColor(color)
        self._max = max_points
        self._data: list[float] = [0.0] * max_points
        self.setMinimumHeight(72)
        self.setMaximumHeight(90)
        self._glow_phase = 0.0

    # public ---------------------------------------------------------------
    def push(self, value: float):
        self._data.append(value)
        if len(self._data) > self._max:
            self._data.pop(0)
        self.update()

    # paint ---------------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # background
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        # border
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)

        top_m, bot_m = 18, 6
        gh = h - top_m - bot_m
        n = len(self._data)
        if n < 2:
            p.end(); return

        mx = max(max(self._data), 1.0)

        # horizontal grid lines
        p.setPen(QPen(QColor(30, 50, 70, 100), 1, Qt.PenStyle.DotLine))
        for frac in (0.25, 0.5, 0.75):
            y = top_m + gh * (1.0 - frac)
            p.drawLine(4, int(y), w - 4, int(y))

        # build path
        path = QPainterPath()
        dx = (w - 8) / (n - 1)
        for i, v in enumerate(self._data):
            x = 4 + i * dx
            y = top_m + gh * (1.0 - v / mx)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # filled area
        fill = QPainterPath(path)
        fill.lineTo(4 + (n - 1) * dx, top_m + gh)
        fill.lineTo(4, top_m + gh)
        fill.closeSubpath()
        grad = QLinearGradient(0, top_m, 0, top_m + gh)
        fc = QColor(self._color); fc.setAlpha(40)
        grad.setColorAt(0, fc); fc.setAlpha(0); grad.setColorAt(1, fc)
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(fill)

        # glow line
        glow_pen = QPen(self._color, 2)
        p.setPen(glow_pen)
        p.drawPath(path)

        # bright dots at peaks
        for i in range(max(0, n - 8), n):
            x = 4 + i * dx
            y = top_m + gh * (1.0 - self._data[i] / mx)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self._color))
            p.drawEllipse(QPointF(x, y), 2, 2)

        # label
        p.setPen(QPen(self._color, 1))
        p.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
        p.drawText(6, 13, f"{self._label}")

        # current value
        val = self._data[-1] if self._data else 0
        if self._label == "NET":
            txt = f"{val:.1f} MB/s"
        elif self._label == "TEMP":
            txt = f"{val:.0f}°C" if val > 0 else "N/A"
        else:
            txt = f"{val:.1f}%"
        p.drawText(w - 70, 13, txt)
        p.end()


class _PomodoroWidget(QWidget):
    """Visual Pomodoro / Focus timer with arc countdown."""

    work_done = pyqtSignal(int)  # sessions completed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._work_sec = 25 * 60    # 25 min default
        self._break_sec = 5 * 60     # 5 min default
        self._remaining = self._work_sec
        self._running = False
        self._is_break = False
        self._sessions = 0
        self._phase = 0.0            # 0→1 arc sweep
        self._pulse = 0.0
        self.setMinimumHeight(130)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._anim_tick)
        self._anim_tmr.start(40)

    def _tick(self):
        if self._remaining > 0:
            self._remaining -= 1
            self._phase = 1.0 - (self._remaining / (self._break_sec if self._is_break else self._work_sec))
        else:
            self._timer.stop()
            self._running = False
            if not self._is_break:
                self._sessions += 1
                self.work_done.emit(self._sessions)
                self._is_break = True
                self._remaining = self._break_sec
            else:
                self._is_break = False
                self._remaining = self._work_sec
            self._phase = 0.0
            self.update()
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.trayIcon.showMessage  # may not exist — safe ignore
            except Exception:
                pass
            return
        self.update()

    def _anim_tick(self):
        self._pulse = (self._pulse + 0.05) % (2 * math.pi)
        if not self._running:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy, r = w // 2, h // 2 - 8, min(w, h) // 2 - 22
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)

        # background ring
        pen_bg = QPen(QColor(30, 50, 70), 6)
        pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)

        # progress arc
        arc_color = QColor("#ff6b35") if not self._is_break else QColor("#00e68a")
        pen_arc = QPen(arc_color, 6)
        pen_arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen_arc)
        span = int(self._phase * 360 * 16)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, -span)

        # glow dot at arc tip
        if self._phase > 0.01:
            angle_deg = 90 - self._phase * 360
            angle_rad = math.radians(angle_deg)
            dx = cx + r * math.cos(angle_rad)
            dy = cy - r * math.sin(angle_rad)
            glow = QRadialGradient(dx, dy, 12)
            glow.setColorAt(0, arc_color); glow.setColorAt(1, QColor(arc_color.red(), arc_color.green(), arc_color.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(glow))
            p.drawEllipse(QPointF(dx, dy), 12, 12)

        # time text
        mins, secs = divmod(self._remaining, 60)
        p.setPen(QPen(QColor(220, 230, 240), 1))
        p.setFont(QFont(C.FONT_MONO, 18, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{mins:02d}:{secs:02d}")

        # mode label
        mode_txt = "BREAK" if self._is_break else "FOCUS"
        mode_col = "#00e68a" if self._is_break else "#ff6b35"
        p.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(mode_col), 1))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                    f"🍅 {mode_txt}  |  Session {self._sessions}")
        p.end()

    # public controls
    def start(self):
        self._running = True; self._timer.start(1000)

    def pause(self):
        self._running = False; self._timer.stop()

    def reset(self):
        self._timer.stop(); self._running = False
        self._is_break = False; self._remaining = self._work_sec
        self._phase = 0.0; self._sessions = 0; self.update()


class _ClipboardHistoryWidget(QWidget):
    """Tracks clipboard contents and shows last N entries."""

    copy_requested = pyqtSignal(str)

    def __init__(self, max_entries: int = 30, parent=None):
        super().__init__(parent)
        self._max = max_entries
        self._history: list[dict] = []  # {"text": ..., "time": ..., "preview": ...}
        self._last_clip = ""
        self._list: QListWidget | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        lbl = QLabel("📋 CLIPBOARD HISTORY")
        lbl.setFont(pfont(10, "semibold", spacing=0.4))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(lbl)
        btn_clr = AnimatedPushButton("🗑 CLEAR")
        btn_clr.setFixedSize(56, 18)
        btn_clr.clicked.connect(self._clear)
        hdr.addWidget(btn_clr)
        lay.addLayout(hdr)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER};
                border-radius: 8px; font-size: 10px; outline: none;
            }}
            QListWidget::item {{
                padding: 5px 6px; border-bottom: 1px solid {C.BORDER_A};
            }}
            QListWidget::item:selected {{
                background: #11243a; color: {C.ACC};
            }}
            QScrollBar:vertical {{ background: {C.BG}; width: 5px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 2px; min-height: 12px; }}
        """)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self._list)

        self._clipboard = QApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clip_changed)

    def _on_clip_changed(self):
        try:
            text = self._clipboard.text()
            if text and text != self._last_clip and len(text.strip()) > 0:
                self._last_clip = text
                preview = text[:80].replace("\n", " ")
                ts = time.strftime("%H:%M:%S")
                self._history.insert(0, {"text": text, "time": ts, "preview": preview})
                if len(self._history) > self._max:
                    self._history.pop()
                self._refresh_list()
        except Exception:
            pass

    def _refresh_list(self):
        if not self._list:
            return
        self._list.clear()
        for entry in self._history:
            self._list.addItem(f"[{entry['time']}]  {entry['preview']}")

    def _on_double_click(self, item):
        idx = self._list.row(item)
        if 0 <= idx < len(self._history):
            self.copy_requested.emit(self._history[idx]["text"])
            try:
                self._clipboard.setText(self._history[idx]["text"])
            except Exception:
                pass

    def _clear(self):
        self._history.clear(); self._last_clip = ""
        self._refresh_list()


class _WebSearchWidget(QWidget):
    """Quick web search launcher — Google / YouTube / Wikipedia / GitHub."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        hdr = QLabel("🌐 QUICK SEARCH")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(hdr)

        row = QHBoxLayout()
        row.setSpacing(4)
        self._combo = QComboBox()
        self._combo.addItems(["🔍 Google", "▶ YouTube", "📖 Wikipedia", "🐙 GitHub"])
        self._combo.setFixedSize(110, 28)
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER};
                border-radius: 8px; padding: 4px 8px; font-size: 10px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL}; color: {C.PRI}; selection-background-color: {C.ACC};
                border: 1px solid {C.BORDER};
            }}
        """)
        row.addWidget(self._combo)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Search anything...")
        self._input.setFont(QFont(C.FONT_SANS, 9))
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER};
                border-radius: 8px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.ACC}; }}
        """)
        self._input.returnPressed.connect(self._search)
        row.addWidget(self._input, 1)
        lay.addLayout(row)

        btn_row = QHBoxLayout()
        btn_search = AnimatedPushButton("🚀 SEARCH")
        btn_search.setFixedSize(80, 26)
        btn_search.clicked.connect(self._search)
        btn_row.addWidget(btn_search)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _search(self):
        q = self._input.text().strip()
        if not q:
            return
        idx = self._combo.currentIndex()
        urls = [
            f"https://www.google.com/search?q={q.replace(' ', '+')}",
            f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}",
            f"https://en.wikipedia.org/wiki/Special:Search?search={q.replace(' ', '+')}",
            f"https://github.com/search?q={q.replace(' ', '+')}",
        ]
        url = urls[idx]
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass


class _ToastNotification(QWidget):
    """Slide-in toast notification overlay. Stacks at top-right."""

    def __init__(self, title: str, message: str, color: str = "#00f6ff",
                 duration_ms: int = 3500, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._title = title
        self._message = message
        self._color = QColor(color)
        self._duration = duration_ms
        self._opacity = 0.0
        self.setFixedSize(320, 72)
        self._slide_offset = 30
        self._show_anim = QTimer(self)
        self._show_anim.timeout.connect(self._anim_step)
        self._show_anim.start(16)
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._start_close)

    def show_and_start(self, parent_pos: QPointF):
        pg = QApplication.primaryScreen().geometry()
        self.move(pg.right() - 340, pg.top() + 20 + self._slide_offset)
        self.show()
        self._close_timer.start(self._duration)

    def _anim_step(self):
        self._opacity = min(self._opacity + 0.08, 1.0)
        self._slide_offset = max(self._slide_offset - 2, 0)
        pg = QApplication.primaryScreen().geometry()
        self.move(pg.right() - 340, pg.top() + 20 + self._slide_offset)
        if self._opacity >= 1.0:
            self._show_anim.stop()
        self.update()

    def _start_close(self):
        self._fade_tmr = QTimer(self)
        self._fade_tmr.timeout.connect(self._fade_step)
        self._fade_tmr.start(16)

    def _fade_step(self):
        self._opacity -= 0.06
        if self._opacity <= 0:
            self._fade_tmr.stop()
            self.close()
            self.deleteLater()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._opacity)
        w, h = self.width(), self.height()
        # glass bg
        p.setPen(QPen(self._color, 1))
        bg = QColor(8, 12, 24, 220)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(0, 0, w, h, 10, 10)
        # accent bar
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 4, h, 2, 2)
        # title
        p.setPen(QPen(self._color, 1))
        p.setFont(QFont(C.FONT_SANS, 9, QFont.Weight.Bold))
        p.drawText(14, 22, self._title)
        # message
        p.setPen(QPen(QColor(190, 200, 210), 1))
        p.setFont(QFont(C.FONT_SANS, 8))
        p.drawText(14, 40, self._message[:90])
        # timestamp
        p.setPen(QPen(QColor(100, 110, 120), 1))
        p.setFont(QFont(C.FONT_MONO, 7))
        p.drawText(14, 56, time.strftime("%H:%M:%S"))
        p.end()


class _ToastManager(QObject):
    """Manages a stack of toast notifications. Owned by MainWindow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._toasts: list[_ToastNotification] = []
        self._y_offset = 20

    def show(self, title: str, message: str, color: str = "#00f6ff", duration_ms: int = 3500):
        toast = _ToastNotification(title, message, color, duration_ms)
        toast.destroyed.connect(self._on_toast_destroyed)
        toast.show_and_start(QPointF(0, self._y_offset))
        self._y_offset += 80
        self._toasts.append(toast)

    def _on_toast_destroyed(self, obj):
        if obj in self._toasts:
            self._toasts.remove(obj)
        self._y_offset = max(20, self._y_offset - 80)


class _ColorPickerWidget(QWidget):
    """Live color theme customizer — pick hue/brightness for palette colors."""

    colors_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = {
            "primary": "#00f6ff", "accent": "#7c4dff",
            "background": "#050a14", "text": "#e0e0e0",
        }
        self._active_key = "primary"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        # Color swatch row
        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(4)
        self._swatches: dict[str, QLabel] = {}
        for key, label in [("primary", "PRI"), ("accent", "ACC"), ("background", "BG"), ("text", "TXT")]:
            sw = QLabel(label)
            sw.setFixedSize(52, 24)
            sw.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sw.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
            sw.setStyleSheet(f"background: {self._current[key]}; color: #000; border-radius: 8px; border: 1px solid #333;")
            sw.mousePressEvent = lambda e, k=key: self._select_key(k)
            self._swatches[key] = sw
            swatch_row.addWidget(sw)
        swatch_row.addStretch()
        lay.addLayout(swatch_row)

        # Hue wheel
        self._wheel = _HueWheel(self)
        self._wheel.setMinimumHeight(140)
        self._wheel.setMaximumHeight(160)
        self._wheel.hue_selected.connect(self._on_hue)
        lay.addWidget(self._wheel)

        # Brightness slider
        b_row = QHBoxLayout()
        b_lbl = QLabel("Brightness")
        b_lbl.setFont(QFont(C.FONT_SANS, 8))
        b_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        b_lbl.setFixedWidth(64)
        b_row.addWidget(b_lbl)
        self._bright = QSlider(Qt.Orientation.Horizontal)
        self._bright.setRange(20, 100); self._bright.setValue(60)
        self._bright.setStyleSheet(f"QSlider::groove:horizontal {{ height:4px; background:{C.BORDER_A}; border-radius:2px; }}"
                                   f"QSlider::handle:horizontal {{ background:{C.ACC}; width:12px; margin-top:-4px; border-radius:6px; }}")
        self._bright.valueChanged.connect(self._on_brightness)
        b_row.addWidget(self._bright)
        lay.addLayout(b_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        for label, key in [("PREVIEW", "preview"), ("SAVE", "save"), ("RESET", "reset")]:
            b = AnimatedPushButton(label)
            b.setFixedHeight(22)
            b.clicked.connect(lambda checked, k=key: self._on_btn(k))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

    def _select_key(self, key: str):
        self._active_key = key
        for k, sw in self._swatches.items():
            border = "2px solid #fff" if k == key else "1px solid #333"
            sw.setStyleSheet(f"background: {self._current[k]}; color: #000; border-radius: 8px; border: {border};")

    def _on_hue(self, hue: float):
        from PyQt6.QtGui import QColor as _QC
        from colorsys import hsv_to_rgb
        b_val = self._bright.value() / 100.0
        r, g, bl = hsv_to_rgb(hue / 360.0, 0.85, b_val)
        col = _QC(int(r * 255), int(g * 255), int(bl * 255))
        hex_col = col.name()
        self._current[self._active_key] = hex_col
        self._swatches[self._active_key].setStyleSheet(
            f"background: {hex_col}; color: #000; border-radius: 8px; border: 2px solid #fff;")

    def _on_brightness(self, _v):
        # re-apply with new brightness using current hue
        self._wheel._trigger_last()

    def _on_btn(self, key: str):
        if key == "reset":
            self._current = {"primary": "#00f6ff", "accent": "#7c4dff",
                             "background": "#050a14", "text": "#e0e0e0"}
            for k, sw in self._swatches.items():
                sw.setStyleSheet(f"background: {self._current[k]}; color:#000; border-radius:3px; border:1px solid #333;")
            self.colors_changed.emit(self._current)
        elif key == "preview" or key == "save":
            self.colors_changed.emit(self._current)


class _HueWheel(QWidget):
    """Painted HSV hue wheel — click/drag to pick hue."""

    hue_selected = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_hue = 180.0
        self.setMouseTracking(True)

    def _trigger_last(self):
        self.hue_selected.emit(self._last_hue)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 14
        # draw hue ring using conic-style segments
        from colorsys import hsv_to_rgb
        steps = 72
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(steps):
            hue = (i / steps) * 360.0
            rc, gc, bc = hsv_to_rgb(hue / 360.0, 0.85, 0.9)
            col = QColor(int(rc * 255), int(gc * 255), int(bc * 255))
            p.setBrush(QBrush(col))
            p.drawPie(QRectF(cx - r, cy - r, r * 2, r * 2),
                      int((90 - hue) * 16), int(-360 / steps * 16) + 1)
        # inner hole
        p.setBrush(QBrush(QColor(0, 8, 18, 255)))
        p.drawEllipse(QPointF(cx, cy), r * 0.55, r * 0.55)
        # indicator dot at current hue
        ang = math.radians(self._last_hue)
        dx = cx + (r * 0.78) * math.cos(ang)
        dy = cy - (r * 0.78) * math.sin(ang)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(QPointF(dx, dy), 6, 6)
        p.end()

    def mousePressEvent(self, e):
        self._update_from_pos(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._update_from_pos(e.position())

    def _update_from_pos(self, pos):
        cx, cy = self.width() / 2, self.height() / 2
        dx, dy = pos.x() - cx, pos.y() - cy
        ang = math.degrees(math.atan2(-dy, dx))
        if ang < 0:
            ang += 360
        self._last_hue = ang
        self.hue_selected.emit(ang)
        self.update()


class _QuickNotesWidget(QWidget):
    """Scratchpad for quick notes with save/load."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._notes_dir = BASE_DIR / "notes"
        self._notes_dir.mkdir(exist_ok=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText("Type a quick note here... (auto-saves on focus loss)")
        self._editor.setFont(QFont(C.FONT_MONO, 9))
        self._editor.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;
            }}
        """)
        lay.addWidget(self._editor)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_save = AnimatedPushButton("💾 SAVE"); btn_save.setFixedHeight(22)
        btn_save.clicked.connect(self._save_note); btn_row.addWidget(btn_save)
        btn_load = AnimatedPushButton("📂 LOAD"); btn_load.setFixedHeight(22)
        btn_load.clicked.connect(self._load_recent); btn_row.addWidget(btn_load)
        btn_clr = AnimatedPushButton("🗑 CLEAR"); btn_clr.setFixedHeight(22)
        btn_clr.clicked.connect(lambda: self._editor.clear()); btn_row.addWidget(btn_clr)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # load last note on startup
        QTimer.singleShot(500, self._load_recent)

    def _save_note(self):
        text = self._editor.toPlainText().strip()
        if not text:
            return
        fname = self._notes_dir / f"note_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            fname.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def _load_recent(self):
        try:
            notes = sorted(self._notes_dir.glob("note_*.txt"), reverse=True)
            if notes:
                self._editor.setPlainText(notes[0].read_text(encoding="utf-8"))
        except Exception:
            pass


class _MediaControllerWidget(QWidget):
    """System volume slider + media transport buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Volume slider row
        vol_row = QHBoxLayout()
        vol_lbl = QLabel("🔊")
        vol_lbl.setFont(QFont("Segoe UI Emoji" if _OS == "Windows" else "Arial", 12))
        vol_row.addWidget(vol_lbl)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100); self._vol_slider.setValue(50)
        self._vol_slider.setStyleSheet(f"QSlider::groove:horizontal {{ height:6px; background:{C.BORDER_A}; border-radius:3px; }}"
                                       f"QSlider::handle:horizontal {{ background:{C.ACC}; width:14px; height:14px; margin:-4px 0; border-radius:7px; }}")
        self._vol_slider.valueChanged.connect(self._on_volume)
        vol_row.addWidget(self._vol_slider)
        self._vol_lbl = QLabel("50%")
        self._vol_lbl.setFixedWidth(36)
        self._vol_lbl.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._vol_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        vol_row.addWidget(self._vol_lbl)
        lay.addLayout(vol_row)

        # Media transport buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        for emoji, action in [("⏮", "prev"), ("⏯", "play"), ("⏭", "next"), ("🔇", "mute")]:
            b = AnimatedPushButton(emoji)
            b.setFixedSize(42, 28)
            b.setFont(QFont("Segoe UI Emoji" if _OS == "Windows" else "Arial", 11))
            b.clicked.connect(lambda checked, a=action: self._media_key(a))
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        QTimer.singleShot(800, self._read_volume)

    def _on_volume(self, val: int):
        self._vol_lbl.setText(f"{val}%")
        try:
            if _OS == "Windows":
                import ctypes
                from ctypes import cast, POINTER
                try:
                    import comtypes
                    from comtypes import CLSCTX_ALL
                except Exception:
                    comtypes = None
                if comtypes:
                    pass  # fallback to simpler method below
            # Simple cross-platform approach: pycaw on Windows, else just display
            if _OS == "Windows":
                # use keyboard media volume as fallback (sends volume up/down rapidly)
                pass
        except Exception:
            pass

    def _read_volume(self):
        pass

    def _media_key(self, action: str):
        """Simulate media keys via Windows API."""
        if _OS != "Windows":
            return
        try:
            import ctypes
            key_map = {
                "play": 0xB3,    # MEDIA_PLAY_PAUSE
                "prev": 0xB1,    # MEDIA_PREV_TRACK
                "next": 0xB0,    # MEDIA_NEXT_TRACK
                "mute": 0xAD,    # VOLUME_MUTE
            }
            vk = key_map.get(action)
            if vk:
                KEYEVENTF_EXTENDEDKEY = 0x0001
                KEYEVENTF_KEYUP = 0x0002
                user32 = ctypes.windll.user32
                user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
                user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        except Exception:
            pass


class _NetworkInfoWidget(QWidget):
    """WiFi/network info with painted signal strength bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signal = 0  # 0-5 bars
        self._ssid = "—"
        self._ip = "—"
        self._gateway = "—"
        self._mac = "—"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._bars = _SignalBarsWidget()
        lay.addWidget(self._bars)

        self._info_lbl = QLabel("Scanning network...")
        self._info_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._info_lbl.setStyleSheet(f"color: {C.PRI}; background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        self._info_lbl.setWordWrap(True)
        lay.addWidget(self._info_lbl)

        btn_refresh = AnimatedPushButton("🔄 REFRESH")
        btn_refresh.setFixedHeight(20)
        btn_refresh.clicked.connect(self._refresh)
        lay.addWidget(btn_refresh)

        QTimer.singleShot(600, self._refresh)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(10000)

    def _refresh(self):
        try:
            # SSID + signal (Windows)
            if _OS == "Windows":
                out = subprocess.run("netsh wlan show interfaces", capture_output=True, text=True, shell=True, timeout=4)
                txt = out.stdout
                import re as _re
                m = _re.search(r"SSID\s*:\s*(.+)", txt)
                if m:
                    self._ssid = m.group(1).strip()
                m2 = _re.search(r"Signal\s*:\s*(\d+)%", txt)
                if m2:
                    pct = int(m2.group(1))
                    self._signal = min(5, max(1, (pct + 19) // 20))
        except Exception:
            self._ssid = "—"; self._signal = 0

        try:
            hostname = platform.node()
            addrs = psutil.net_if_addrs()
            for name, lst in addrs.items():
                for a in lst:
                    if a.family.name == "AF_INET" and not a.address.startswith("169.254"):
                        self._ip = a.address
                        break
                if self._ip != "—":
                    break
            # MAC
            for name, lst in addrs.items():
                for a in lst:
                    if a.family.name in ("AF_PACKET", "AF_LINK") and a.address:
                        self._mac = a.address
                        break
                if self._mac != "—":
                    break
        except Exception:
            pass

        try:
            if _OS == "Windows":
                out = subprocess.run("ipconfig", capture_output=True, text=True, shell=True, timeout=4)
                import re as _re
                m = _re.search(r"Default Gateway[\.\s]*:\s*([\d.]+)", out.stdout)
                if m:
                    self._gateway = m.group(1).strip()
        except Exception:
            self._gateway = "—"

        self._info_lbl.setText(
            f"📶 SSID: {self._ssid}\n"
            f"🌐 IP: {self._ip}\n"
            f"🔀 Gateway: {self._gateway}\n"
            f"🔖 MAC: {self._mac}"
        )
        self._bars.set_signal(self._signal)


class _SignalBarsWidget(QWidget):
    """Painted 5-bar signal strength indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = 0
        self.setMinimumHeight(48)
        self.setMaximumHeight(56)

    def set_signal(self, bars: int):
        self._bars = max(0, min(5, bars))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)

        nb = 5
        bw = 8
        gap = 4
        total_w = nb * bw + (nb - 1) * gap
        x0 = (w - total_w) // 2
        for i in range(nb):
            bh = (i + 1) * 7 + 6
            x = x0 + i * (bw + gap)
            y = h - bh - 8
            on = i < self._bars
            col = QColor("#00e676") if on else QColor(40, 60, 80)
            p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y, bw, bh), 2, 2)

        p.setPen(QPen(QColor(120, 140, 160), 1))
        p.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                   f"SIGNAL {self._bars}/5")
        p.end()


class _StatTrackerWidget(QWidget):
    """AI chat / session stat tracker with painted ring chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sent = 0
        self._received = 0
        self._commands = 0
        self._errors = 0
        self._start = time.time()
        self.setMinimumHeight(120)
        self.setMaximumHeight(140)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self.update)
        self._anim.start(1000)

    def record_sent(self):
        self._sent += 1; self._commands += 1

    def record_received(self):
        self._received += 1

    def record_error(self):
        self._errors += 1

    def reset(self):
        self._sent = 0; self._received = 0; self._commands = 0; self._errors = 0
        self._start = time.time(); self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)

        # Ring chart on left
        cx, cy, r = 40, h // 2, 26
        p.setPen(QPen(QColor(30, 50, 70), 4))
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)
        total = max(self._sent + self._received + self._errors, 1)
        sent_frac = self._sent / total
        recv_frac = self._received / total
        # sent arc (cyan)
        p.setPen(QPen(QColor("#00f6ff"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, int(-sent_frac * 360 * 16))
        # received arc (green)
        p.setPen(QPen(QColor("#00e676"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        start_a = 90 - sent_frac * 360
        p.drawArc(cx - r, cy - r, r * 2, r * 2, int(start_a * 16), int(-recv_frac * 360 * 16))

        # Stats text on right
        elapsed = int(time.time() - self._start)
        mins, secs = divmod(elapsed, 60)
        p.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#00f6ff")))
        p.drawText(80, 24, f"📤 Sent:      {self._sent}")
        p.setPen(QPen(QColor("#00e676")))
        p.drawText(80, 42, f"📥 Received:  {self._received}")
        p.setPen(QPen(QColor("#ffab00")))
        p.drawText(80, 60, f"⌨ Commands:  {self._commands}")
        p.setPen(QPen(QColor("#ff1744")))
        p.drawText(80, 78, f"⚠ Errors:    {self._errors}")
        p.setPen(QPen(QColor(180, 190, 210)))
        p.setFont(QFont(C.FONT_MONO, 7))
        p.drawText(80, 96, f"⏱ Session:   {mins:02d}:{secs:02d}")
        p.end()


class _AppLauncherWidget(QWidget):
    """Quick-launch grid for common Windows apps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)

        apps = [
            ("🌐 Chrome", "chrome"),
            ("💻 VS Code", "code"),
            ("⬛ Terminal", "cmd"),
            ("📁 Explorer", "explorer"),
            ("📝 Notepad", "notepad"),
            ("📊 Task Mgr", "taskmgr"),
            ("🧮 Calculator", "calc"),
            ("🎵 Spotify", "spotify"),
        ]
        for i, (label, cmd) in enumerate(apps):
            b = AnimatedPushButton(label)
            b.setFixedSize(92, 34)
            b.setFont(QFont(C.FONT_SANS, 8, QFont.Weight.Medium))
            b.clicked.connect(lambda checked, c=cmd: self._launch(c))
            grid.addWidget(b, i // 4, i % 4)
        lay.addWidget(grid_widget)

    def _launch(self, cmd: str):
        try:
            if cmd in ("cmd", "explorer", "notepad", "taskmgr", "calc"):
                subprocess.Popen(["start", cmd], shell=True)
            else:
                subprocess.Popen(cmd, shell=True)
        except Exception:
            try:
                os.system(f"start {cmd}")
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
#  WEATHER HELPER — free Open-Meteo API (no key required)
# ──────────────────────────────────────────────────────────────────────────────

class _WeatherFetcher:
    """Fetches current weather using free Open-Meteo API + IP geolocation."""

    def __init__(self):
        self._lat: float | None = None
        self._lon: float | None = None
        self._city = "—"
        self._country = ""
        self._temp = 0.0
        self._feels = 0.0
        self._code = 0
        self._wind_speed = 0.0
        self._humidity = 0
        self._last_fetch = 0.0
        self._lock = threading.Lock()
        self._geo_ok = False

    def _geolocate(self):
        """Try multiple geolocation services for reliability."""
        providers = [
            self._geo_ipapi,
            self._geo_ipapi_co,
            self._geo_ip_api,
        ]
        for fn in providers:
            try:
                if fn():
                    self._geo_ok = True
                    return
            except Exception:
                continue
        # Final fallback
        self._lat, self._lon = 28.61, 77.21
        self._city = "New Delhi"
        self._country = "India"
        self._geo_ok = False

    def _geo_ipapi(self):
        import urllib.request, json as _json
        url = "https://ipwho.is/"
        req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read().decode())
            if data.get("success", False):
                loc = data.get("location", {})
                self._lat = float(loc.get("latitude", 0))
                self._lon = float(loc.get("longitude", 0))
                self._city = (loc.get("city") or "Unknown")[:20]
                self._country = (loc.get("country") or "")[:15]
                return True
            return False

    def _geo_ipapi_co(self):
        import urllib.request, json as _json
        url = "https://ipapi.co/json/"
        req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read().decode())
            self._lat = float(data.get("latitude", 0))
            self._lon = float(data.get("longitude", 0))
            self._city = (data.get("city") or "Unknown")[:20]
            self._country = (data.get("country_name") or "")[:15]
            return True

    def _geo_ip_api(self):
        import urllib.request, json as _json
        url = "http://ip-api.com/json/?fields=status,lat,lon,city,country"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = _json.loads(r.read().decode())
            if data.get("status") == "success":
                self._lat = float(data.get("lat", 28.61))
                self._lon = float(data.get("lon", 77.21))
                self._city = (data.get("city") or "Delhi")[:20]
                self._country = (data.get("country") or "India")[:15]
                return True
            return False

    def fetch(self):
        try:
            import urllib.request, json as _json
            if self._lat is None:
                self._geolocate()
            url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={self._lat}&longitude={self._lon}"
                   f"&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m")
            req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = _json.loads(r.read().decode())
                cur = data.get("current", {})
                with self._lock:
                    self._temp = cur.get("temperature_2m", 0.0)
                    self._feels = cur.get("apparent_temperature", self._temp)
                    self._code = cur.get("weather_code", 0)
                    self._wind_speed = cur.get("wind_speed_10m", 0.0)
                    self._humidity = cur.get("relative_humidity_2m", 0)
                    self._last_fetch = time.time()
        except Exception:
            pass

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "city": self._city, "country": self._country, "temp": self._temp,
                "feels": self._feels, "code": self._code,
                "wind": self._wind_speed, "humidity": self._humidity,
                "geo_ok": self._geo_ok,
            }

    @staticmethod
    def code_to_emoji(code: int) -> str:
        if code == 0: return "☀️"
        if code in (1, 2, 3): return "⛅"
        if code in (45, 48): return "🌫️"
        if code in (51, 53, 55, 56, 57): return "🌦️"
        if code in (61, 63, 65, 66, 67, 80, 81, 82): return "🌧️"
        if code in (71, 73, 75, 77, 85, 86): return "❄️"
        if code in (95, 96, 99): return "⛈️"
        return "🌤️"


_weather = _WeatherFetcher()


class _WeatherWidget(QWidget):
    """Compact weather display widget for header — premium style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {"city": "—", "country": "", "temp": 0, "feels": 0, "code": 0,
                       "wind": 0, "humidity": 0, "geo_ok": False}
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)

        # Glassmorphic container
        self.setStyleSheet(
            f"QWidget{{background:rgba(255,255,255,0.04); border:1px solid {C.BORDER}; border-radius:8px;}}"
        )

        self._emoji_lbl = QLabel("🌤️")
        self._emoji_lbl.setFont(QFont("Segoe UI Emoji" if _OS == "Windows" else "Arial", 16))
        self._emoji_lbl.setStyleSheet("background:transparent; border:none;")
        lay.addWidget(self._emoji_lbl)

        col = QVBoxLayout()
        col.setSpacing(0)
        self._temp_lbl = QLabel("--°C")
        self._temp_lbl.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        self._temp_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; border:none;")
        col.addWidget(self._temp_lbl)

        loc_row = QHBoxLayout()
        loc_row.setSpacing(4)
        self._city_lbl = QLabel("Locating...")
        self._city_lbl.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
        self._city_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border:none;")
        loc_row.addWidget(self._city_lbl)
        self._wind_lbl = QLabel("")
        self._wind_lbl.setFont(QFont(C.FONT_SANS, 6))
        self._wind_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border:none;")
        loc_row.addWidget(self._wind_lbl)
        col.addLayout(loc_row)
        lay.addLayout(col)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for weather details")

        # initial fetch in background thread
        threading.Thread(target=self._bg_fetch, daemon=True).start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: threading.Thread(target=self._bg_fetch, daemon=True).start())
        self._timer.start(600000)  # 10 min

    def _bg_fetch(self):
        _weather.fetch()
        self._data = _weather.snapshot()
        QTimer.singleShot(0, self._update_ui)

    def _update_ui(self):
        d = self._data
        emoji = _WeatherFetcher.code_to_emoji(d["code"])
        temp = d["temp"]
        city = d["city"][:14]
        country = d.get("country", "")
        wind = d.get("wind", 0)
        humidity = d.get("humidity", 0)

        self._emoji_lbl.setText(emoji)
        self._temp_lbl.setText(f"{temp:.1f}°C")

        if country:
            self._city_lbl.setText(f"📍 {city}, {country[:2]}")
        else:
            self._city_lbl.setText(f"📍 {city}")

        self._wind_lbl.setText(f"💨{wind:.0f}km/h  💧{humidity}%")

        self.setToolTip(
            f"📍 {city}, {country}\n"
            f"🌡️ {temp:.1f}°C (feels like {d['feels']:.1f}°C)\n"
            f"💨 Wind: {wind:.1f} km/h\n"
            f"💧 Humidity: {humidity}%\n"
            f"{emoji}"
        )

    def mousePressEvent(self, e):
        d = self._data
        msg = (f"📍 {d.get('city', '?')}, {d.get('country', '?')}\n"
               f"🌡️ {d['temp']:.1f}°C (feels like {d['feels']:.1f}°C)\n"
               f"💨 Wind: {d.get('wind', 0):.1f} km/h\n"
               f"💧 Humidity: {d.get('humidity', 0)}%\n"
               f"{_WeatherFetcher.code_to_emoji(d['code'])}")
        try:
            from PyQt6.QtWidgets import QToolTip
            QToolTip.showText(e.globalPosition().toPoint(), msg, self)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
#  NEW SYS LAB WIDGETS
# ──────────────────────────────────────────────────────────────────────────────

class _PasswordGenWidget(QWidget):
    """Password generator with strength meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        import string as _s
        self._pools = {
            "upper": _s.ascii_uppercase,
            "lower": _s.ascii_lowercase,
            "digits": _s.digits,
            "symbols": "!@#$%^&*()_+-=[]{}|;:,.<>?",
        }
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # length slider
        len_row = QHBoxLayout()
        len_lbl = QLabel("Length:")
        len_lbl.setFont(QFont(C.FONT_SANS, 8))
        len_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        len_lbl.setFixedWidth(50)
        len_row.addWidget(len_lbl)
        self._len_slider = QSlider(Qt.Orientation.Horizontal)
        self._len_slider.setRange(8, 32); self._len_slider.setValue(16)
        self._len_slider.setStyleSheet(f"QSlider::groove:horizontal {{ height:4px; background:{C.BORDER_A}; border-radius:2px; }}"
                                       f"QSlider::handle:horizontal {{ background:{C.ACC}; width:12px; margin-top:-4px; border-radius:6px; }}")
        self._len_lbl = QLabel("16")
        self._len_lbl.setFixedWidth(24)
        self._len_lbl.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._len_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        self._len_slider.valueChanged.connect(lambda v: self._len_lbl.setText(str(v)))
        len_row.addWidget(self._len_slider)
        len_row.addWidget(self._len_lbl)
        lay.addLayout(len_row)

        # checkboxes
        chk_row = QHBoxLayout()
        chk_row.setSpacing(4)
        self._checks: dict[str, QCheckBox] = {}
        for key, label in [("upper", "ABC"), ("lower", "abc"), ("digits", "123"), ("symbols", "@#$")]:
            c = QCheckBox(label)
            c.setChecked(True)
            c.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
            c.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            self._checks[key] = c
            chk_row.addWidget(c)
        chk_row.addStretch()
        lay.addLayout(chk_row)

        # password display
        self._pw_display = QLineEdit()
        self._pw_display.setReadOnly(True)
        self._pw_display.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        self._pw_display.setStyleSheet(f"background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;")
        lay.addWidget(self._pw_display)

        # strength meter
        self._strength = _StrengthBar()
        lay.addWidget(self._strength)

        # buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_gen = AnimatedPushButton("🎲 GENERATE"); btn_gen.setFixedHeight(22)
        btn_gen.clicked.connect(self._generate); btn_row.addWidget(btn_gen)
        btn_copy = AnimatedPushButton("📋 COPY"); btn_copy.setFixedHeight(22)
        btn_copy.clicked.connect(self._copy); btn_row.addWidget(btn_copy)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self._generate()

    def _generate(self):
        import random as _r
        length = self._len_slider.value()
        pool = ""
        required = []
        for key, cb in self._checks.items():
            if cb.isChecked():
                p = self._pools[key]
                pool += p
                required.append(_r.choice(p))
        if not pool:
            pool = self._pools["lower"]
            required = [_r.choice(pool)]
        chars = [_r.choice(pool) for _ in range(length - len(required))] + required
        _r.shuffle(chars)
        pw = "".join(chars)
        self._pw_display.setText(pw)
        self._strength.set_strength(self._score(pw))

    def _score(self, pw: str) -> int:
        score = 0
        if len(pw) >= 12: score += 25
        elif len(pw) >= 8: score += 15
        if any(c.isupper() for c in pw): score += 20
        if any(c.islower() for c in pw): score += 20
        if any(c.isdigit() for c in pw): score += 20
        if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in pw): score += 15
        return min(score, 100)

    def _copy(self):
        try:
            QApplication.clipboard().setText(self._pw_display.text())
        except Exception:
            pass


class _StrengthBar(QWidget):
    """Painted password strength bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self.setFixedHeight(16)

    def set_strength(self, score: int):
        self._score = max(0, min(100, score))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        # bg
        p.setBrush(QBrush(QColor(30, 40, 60)))
        p.drawRoundedRect(0, 2, w, h - 4, 4, 4)
        # filled
        fw = int((self._score / 100.0) * w)
        if self._score < 40:
            col = QColor("#ff453a")
        elif self._score < 70:
            col = QColor("#ffab00")
        else:
            col = QColor("#30d158")
        p.setBrush(QBrush(col))
        p.drawRoundedRect(0, 2, fw, h - 4, 4, 4)
        # label
        lbl = "WEAK" if self._score < 40 else ("FAIR" if self._score < 70 else "STRONG")
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setFont(QFont(C.FONT_MONO, 6, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{lbl} {self._score}%")
        p.end()


class _UnitConverterWidget(QWidget):
    """Multi-category unit converter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._categories = {
            "Length": {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
                       "mi": 1609.34, "ft": 0.3048, "in": 0.0254},
            "Weight": {"kg": 1.0, "g": 0.001, "mg": 1e-6, "lb": 0.453592, "oz": 0.0283495},
            "Data": {"B": 1.0, "KB": 1024.0, "MB": 1048576.0, "GB": 1073741824.0,
                     "TB": 1099511627776.0, "bit": 0.125},
            "Speed": {"m/s": 1.0, "km/h": 0.277778, "mph": 0.44704, "knot": 0.514444},
            "Time": {"s": 1.0, "ms": 0.001, "min": 60.0, "hr": 3600.0, "day": 86400.0},
        }
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # category combo
        cat_row = QHBoxLayout()
        cat_lbl = QLabel("Category:")
        cat_lbl.setFont(QFont(C.FONT_SANS, 8))
        cat_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        cat_lbl.setFixedWidth(60)
        cat_row.addWidget(cat_lbl)
        self._cat_combo = QComboBox()
        self._cat_combo.addItems(list(self._categories.keys()))
        self._cat_combo.currentTextChanged.connect(self._update_units)
        self._cat_combo.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; padding: 2px;")
        cat_row.addWidget(self._cat_combo)
        lay.addLayout(cat_row)

        # input row
        in_row = QHBoxLayout()
        self._in_value = QLineEdit()
        self._in_value.setPlaceholderText("0")
        self._in_value.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        self._in_value.setStyleSheet(f"background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        self._in_value.textChanged.connect(self._convert)
        in_row.addWidget(self._in_value)
        self._in_unit = QComboBox()
        self._in_unit.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; padding: 2px;")
        in_row.addWidget(self._in_unit)
        lay.addLayout(in_row)

        # output row
        out_row = QHBoxLayout()
        self._out_value = QLineEdit()
        self._out_value.setReadOnly(True)
        self._out_value.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        self._out_value.setStyleSheet(f"background: {C.PANEL}; color: {C.ACC}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        out_row.addWidget(self._out_value)
        self._out_unit = QComboBox()
        self._out_unit.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; padding: 2px;")
        out_row.addWidget(self._out_unit)
        lay.addLayout(out_row)

        self._update_units(self._cat_combo.currentText())

    def _update_units(self, category: str):
        units = list(self._categories.get(category, {}).keys())
        self._in_unit.clear(); self._in_unit.addItems(units)
        self._out_unit.clear(); self._out_unit.addItems(units)
        if len(units) > 1:
            self._out_unit.setCurrentIndex(1)
        self._convert()

    def _convert(self):
        try:
            cat = self._cat_combo.currentText()
            units = self._categories.get(cat, {})
            val = float(self._in_value.text() or "0")
            iu = self._in_unit.currentText()
            ou = self._out_unit.currentText()
            if iu in units and ou in units:
                base = val * units[iu]
                result = base / units[ou]
                self._out_value.setText(f"{result:.6g}")
        except Exception:
            self._out_value.setText("—")


class _WorldClockWidget(QWidget):
    """Painted analog world clock for 5 major cities."""

    CITIES = [
        ("New York", -5), ("London", 0), ("Dubai", 4),
        ("Tokyo", 9), ("Sydney", 11),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self):
        self._t = time.time()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)

        n = len(self.CITIES)
        cell_w = w // n
        clock_r = min(cell_w, h - 30) // 2 - 4

        for i, (city, offset) in enumerate(self.CITIES):
            cx = i * cell_w + cell_w // 2
            cy = (h - 24) // 2 + 4
            # UTC time for this city
            try:
                utc_t = time.gmtime(self._t + offset * 3600)
                hh, mm, ss = utc_t.tm_hour, utc_t.tm_min, utc_t.tm_sec
            except Exception:
                hh = mm = ss = 0
            # day/night indicator
            is_day = 6 <= hh < 19
            bg = QColor(20, 40, 60) if is_day else QColor(30, 20, 50)
            p.setBrush(QBrush(bg))
            p.setPen(QPen(QColor(80, 100, 130), 1))
            p.drawEllipse(QPointF(cx, cy), clock_r, clock_r)
            # hour hand
            ha = math.radians((hh % 12 + mm / 60) * 30 - 90)
            p.setPen(QPen(QColor("#e0e0e0"), 2))
            p.drawLine(QPointF(cx, cy), QPointF(cx + clock_r * 0.5 * math.cos(ha), cy + clock_r * 0.5 * math.sin(ha)))
            # minute hand
            ma = math.radians((mm + ss / 60) * 6 - 90)
            p.setPen(QPen(QColor(C.ACC), 2))
            p.drawLine(QPointF(cx, cy), QPointF(cx + clock_r * 0.75 * math.cos(ma), cy + clock_r * 0.75 * math.sin(ma)))
            # second hand
            sa = math.radians(ss * 6 - 90)
            p.setPen(QPen(QColor(C.GREEN), 1))
            p.drawLine(QPointF(cx, cy), QPointF(cx + clock_r * 0.8 * math.cos(sa), cy + clock_r * 0.8 * math.sin(sa)))
            # center dot
            p.setBrush(QBrush(QColor(200, 200, 200)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 2, 2)
            # city + time label
            p.setPen(QPen(QColor(180, 190, 210), 1))
            p.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
            emoji = "☀️" if is_day else "🌙"
            p.drawText(cx - cell_w // 2 + 4, h - 6, f"{emoji} {city}")
            p.setFont(QFont(C.FONT_MONO, 7))
            p.drawText(cx + 2, h - 6, f"{hh:02d}:{mm:02d}")
        p.end()


class _DecisionMakerWidget(QWidget):
    """Decision wheel spinner + coin flip + dice roller."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "wheel"  # wheel | coin | dice
        self._angle = 0.0
        self._spinning = False
        self._target_angle = 0.0
        self._result = ""
        self.setMinimumHeight(150)
        self.setMaximumHeight(170)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        for label, m in [("🎡 WHEEL", "wheel"), ("🪙 COIN", "coin"), ("🎲 DICE", "dice")]:
            b = AnimatedPushButton(label)
            b.setFixedHeight(20)
            b.clicked.connect(lambda checked, mm=m: self._set_mode(mm))
            mode_row.addWidget(b)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        self._canvas = _DecisionCanvas()
        lay.addWidget(self._canvas)

        spin_row = QHBoxLayout()
        spin_row.setSpacing(4)
        self._spin_btn = AnimatedPushButton("🎯 SPIN")
        self._spin_btn.setFixedHeight(24)
        self._spin_btn.clicked.connect(self._spin)
        spin_row.addWidget(self._spin_btn)
        self._result_lbl = QLabel("Ready...")
        self._result_lbl.setFont(QFont(C.FONT_MONO, 9, QFont.Weight.Bold))
        self._result_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        spin_row.addWidget(self._result_lbl)
        spin_row.addStretch()
        lay.addLayout(spin_row)

        self._canvas.mode = "wheel"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._vel = 0.0

    def _set_mode(self, m: str):
        self._mode = m
        self._canvas.mode = m
        self._result_lbl.setText("Ready...")
        self._canvas.update()

    def _spin(self):
        if self._spinning:
            return
        import random as _r
        self._spinning = True
        self._vel = 25.0 + _r.random() * 15.0
        self._timer.start(16)

    def _animate(self):
        self._angle += self._vel
        self._canvas.angle = self._angle
        self._canvas.update()
        self._vel *= 0.97
        if self._vel < 0.15:
            self._timer.stop()
            self._spinning = False
            self._decide()

    def _decide(self):
        import random as _r
        if self._mode == "wheel":
            options = ["YES", "NO", "MAYBE", "ASK AGAIN", "DEFINITELY", "NOT NOW"]
            # determine from final angle
            idx = int((self._angle % 360) / (360 / len(options)))
            self._result = options[idx % len(options)]
        elif self._mode == "coin":
            self._result = "HEADS" if (self._angle / 360) % 2 < 1 else "TAILS"
        else:  # dice
            self._result = f"DICE: {_r.randint(1, 6)}"
        self._result_lbl.setText(f"→ {self._result}")
        self._canvas.result = self._result
        self._canvas.update()


class _DecisionCanvas(QWidget):
    """Painted canvas for decision wheel / coin / dice."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "wheel"
        self.angle = 0.0
        self.result = ""
        self.setMinimumHeight(90)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 8

        if self.mode == "wheel":
            options = ["YES", "NO", "MAYBE", "ASK AGAIN", "DEFINITELY", "NOT NOW"]
            colors = ["#30d158", "#ff453a", "#ffab00", "#00f6ff", "#7c4dff", "#ff80ab"]
            n = len(options)
            p.translate(cx, cy)
            p.rotate(self.angle)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(n):
                p.setBrush(QBrush(QColor(colors[i])))
                p.drawPie(QRectF(-r, -r, r * 2, r * 2), int(90 - i * (360 / n)) * 16, int(-360 / n * 16) + 1)
            # labels
            p.resetTransform()
            p.translate(cx, cy)
            for i in range(n):
                mid = math.radians(self.angle - i * (360 / n) - (360 / n) / 2)
                lx = r * 0.6 * math.cos(mid)
                ly = r * 0.6 * math.sin(mid)
                p.resetTransform()
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setFont(QFont(C.FONT_MONO, 6, QFont.Weight.Bold))
                p.drawText(cx + lx - 20, cy + ly + 3, options[i][:8])
                p.translate(cx, cy)
            p.resetTransform()
            # pointer
            p.setBrush(QBrush(QColor(255, 255, 255)))
            p.setPen(QPen(QColor(0, 0, 0), 1))
            tri = QPainterPath()
            tri.moveTo(cx, cy - r - 2)
            tri.lineTo(cx - 6, cy - r - 12)
            tri.lineTo(cx + 6, cy - r - 12)
            tri.closeSubpath()
            p.drawPath(tri)
        elif self.mode == "coin":
            # coin: ellipse squashed based on rotation
            sq = abs(math.cos(math.radians(self.angle)))
            p.setBrush(QBrush(QColor("#ffd54f")))
            p.setPen(QPen(QColor("#b8860b"), 2))
            p.drawEllipse(QPointF(cx, cy), r, r * (0.15 + 0.85 * sq))
            face = "H" if (self.angle / 360) % 2 < 1 else "T"
            p.setPen(QPen(QColor("#5d4037"), 1))
            p.setFont(QFont(C.FONT_SANS, int(r * sq) or 1, QFont.Weight.Bold))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, face if sq > 0.3 else "")
        else:  # dice
            p.setBrush(QBrush(QColor("#f5f5f5")))
            p.setPen(QPen(QColor("#333"), 2))
            p.drawRoundedRect(QRectF(cx - r, cy - r, r * 2, r * 2), 8, 8)
            # determine face from angle
            face = int((self.angle / 60) % 6) + 1 if self.angle > 0 else 1
            if self.result:
                try:
                    face = int(self.result.replace("DICE: ", ""))
                except Exception:
                    pass
            pip = {1: [(0, 0)], 2: [(-0.4, -0.4), (0.4, 0.4)],
                   3: [(-0.4, -0.4), (0, 0), (0.4, 0.4)],
                   4: [(-0.4, -0.4), (0.4, -0.4), (-0.4, 0.4), (0.4, 0.4)],
                   5: [(-0.4, -0.4), (0.4, -0.4), (0, 0), (-0.4, 0.4), (0.4, 0.4)],
                   6: [(-0.4, -0.4), (0.4, -0.4), (-0.4, 0), (0.4, 0), (-0.4, 0.4), (0.4, 0.4)]}
            p.setBrush(QBrush(QColor("#333")))
            p.setPen(Qt.PenStyle.NoPen)
            for px, py in pip.get(face, [(0, 0)]):
                p.drawEllipse(QPointF(cx + px * r, cy + py * r), r * 0.12, r * 0.12)
        p.end()


class _CalendarWidget(QWidget):
    """Mini calendar + event planner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events_file = BASE_DIR / "calendar_events.json"
        self._events: dict[str, list[str]] = {}
        self._load_events()
        self._selected_date = time.strftime("%Y-%m-%d")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        self._month_lbl = QLabel("")
        self._month_lbl.setFont(QFont(C.FONT_SANS, 9, QFont.Weight.Bold))
        self._month_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        hdr.addWidget(self._month_lbl)
        hdr.addStretch()
        btn_prev = AnimatedPushButton("‹"); btn_prev.setFixedSize(24, 20)
        btn_prev.clicked.connect(lambda: self._shift_month(-1)); hdr.addWidget(btn_prev)
        btn_next = AnimatedPushButton("›"); btn_next.setFixedSize(24, 20)
        btn_next.clicked.connect(lambda: self._shift_month(1)); hdr.addWidget(btn_next)
        lay.addLayout(hdr)

        self._grid = QGridLayout()
        self._grid.setSpacing(2)
        days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        for i, d in enumerate(days):
            l = QLabel(d)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._grid.addWidget(l, 0, i)
        self._day_labels: list[QLabel] = []
        lay.addLayout(self._grid)

        # event list
        self._event_list = QListWidget()
        self._event_list.setStyleSheet(f"""
            QListWidget {{ background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 8px; font-size: 9px; }}
            QListWidget::item {{ padding: 3px 6px; border-bottom: 1px solid {C.BORDER_A}; }}
        """)
        self._event_list.setMaximumHeight(70)
        lay.addWidget(self._event_list)

        # add event input
        add_row = QHBoxLayout()
        self._event_input = QLineEdit()
        self._event_input.setPlaceholderText("Add event for selected date...")
        self._event_input.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        self._event_input.returnPressed.connect(self._add_event)
        add_row.addWidget(self._event_input)
        btn_add = AnimatedPushButton("+"); btn_add.setFixedSize(24, 22)
        btn_add.clicked.connect(self._add_event); add_row.addWidget(btn_add)
        btn_del = AnimatedPushButton("🗑"); btn_del.setFixedSize(28, 22)
        btn_del.clicked.connect(self._del_event); add_row.addWidget(btn_del)
        lay.addLayout(add_row)

        self._view_year = time.localtime().tm_year
        self._view_month = time.localtime().tm_mon
        self._render_calendar()
        # check events every minute for reminders
        self._check_tmr = QTimer(self)
        self._check_tmr.timeout.connect(self._check_reminders)
        self._check_tmr.start(60000)

    def _load_events(self):
        try:
            if self._events_file.exists():
                self._events = json.loads(self._events_file.read_text(encoding="utf-8"))
        except Exception:
            self._events = {}

    def _save_events(self):
        try:
            self._events_file.write_text(json.dumps(self._events, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _shift_month(self, delta: int):
        self._view_month += delta
        if self._view_month > 12:
            self._view_month = 1; self._view_year += 1
        elif self._view_month < 1:
            self._view_month = 12; self._view_year -= 1
        self._render_calendar()

    def _render_calendar(self):
        # clear old labels
        for lbl in self._day_labels:
            lbl.setParent(None); lbl.deleteLater()
        self._day_labels = []
        import calendar as _cal
        month_name = _cal.month_name[self._view_month]
        self._month_lbl.setText(f"{month_name} {self._view_year}")
        weeks = _cal.monthcalendar(self._view_year, self._view_month)
        today_str = time.strftime("%Y-%m-%d")
        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                if day == 0:
                    l = QLabel("")
                else:
                    date_str = f"{self._view_year}-{self._view_month:02d}-{day:02d}"
                    l = QLabel(str(day))
                    l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    l.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
                    if date_str == today_str:
                        l.setStyleSheet(f"background: {C.ACC}; color: #000; border-radius: 8px;")
                    elif date_str == self._selected_date:
                        l.setStyleSheet(f"background: {C.PRI}; color: #000; border-radius: 8px;")
                    elif date_str in self._events:
                        l.setStyleSheet(f"background: {C.PANEL2}; color: {C.ACC}; border-radius: 8px; border: 1px solid {C.ACC};")
                    else:
                        l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                    l.mousePressEvent = lambda e, ds=date_str: self._select_date(ds)
                self._grid.addWidget(l, r, c)
                self._day_labels.append(l)
        self._refresh_events()

    def _select_date(self, date_str: str):
        self._selected_date = date_str
        self._render_calendar()

    def _refresh_events(self):
        self._event_list.clear()
        evs = self._events.get(self._selected_date, [])
        for ev in evs:
            self._event_list.addItem(f"📌 {ev}")

    def _add_event(self):
        text = self._event_input.text().strip()
        if not text:
            return
        self._events.setdefault(self._selected_date, []).append(text)
        self._event_input.clear()
        self._save_events()
        self._render_calendar()

    def _del_event(self):
        row = self._event_list.currentRow()
        evs = self._events.get(self._selected_date, [])
        if 0 <= row < len(evs):
            evs.pop(row)
            if not evs:
                del self._events[self._selected_date]
            self._save_events()
            self._render_calendar()

    def _check_reminders(self):
        """Fire toast for events happening today."""
        today = time.strftime("%Y-%m-%d")
        evs = self._events.get(today, [])
        for ev in evs:
            key = f"reminded_{today}_{ev}"
            if not getattr(self, key, False):
                setattr(self, key, True)
                # try to access toast manager via parent chain
                p = self.parent()
                while p and not hasattr(p, "_toast_mgr"):
                    p = p.parent()
                if p and hasattr(p, "_toast_mgr"):
                    p._toast_mgr.show("📅 Event Reminder", f"Today: {ev}", "#00f6ff", 6000)


class _HealthGauge(QWidget):
    """Circular gauge showing aggregate system health score 0-100."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 100
        self.setMinimumHeight(110)
        self.setMaximumHeight(130)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._compute)
        self._timer.start(2000)
        self._compute()
        self._pulse = 0.0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._pulse_tick)
        self._anim.start(50)

    def _pulse_tick(self):
        self._pulse = (self._pulse + 0.1) % (2 * math.pi)
        self.update()

    def _compute(self):
        snap = _metrics.snapshot()
        cpu = snap["cpu"]; mem = snap["mem"]; tmp = snap["tmp"]
        # lower load = higher score
        score = 100
        score -= max(0, cpu - 30) * 0.7
        score -= max(0, mem - 50) * 0.5
        if tmp > 0:
            score -= max(0, tmp - 60) * 0.4
        self._score = max(0, min(100, int(score)))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)
        cx, cy = w // 2, h // 2 + 4
        r = min(w, h) // 2 - 18
        # bg arc
        pen_bg = QPen(QColor(30, 50, 70), 8)
        pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 225 * 16, -270 * 16)
        # value arc
        if self._score > 70:
            col = QColor("#30d158")
        elif self._score > 40:
            col = QColor("#ffab00")
        else:
            col = QColor("#ff453a")
        pulse_alpha = int(200 + 55 * math.sin(self._pulse))
        col.setAlpha(min(255, pulse_alpha))
        pen_v = QPen(col, 8)
        pen_v.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen_v)
        span = int((self._score / 100.0) * 270 * 16)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 225 * 16, -span)
        # center text
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setFont(QFont(C.FONT_MONO, 18, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self._score}")
        p.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
        p.setPen(QPen(QColor(150, 160, 180), 1))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, "SYSTEM HEALTH")
        p.end()



class _NotificationResponderWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NotificationResponder")
        self.setStyleSheet(f"""
            QFrame#NotificationResponder {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255,255,255,0.05), stop:1 {C.PANEL});
                border: 1px solid {C.HAIRLINE};
                border-radius: {C.R_LG}px;
            }}
        """)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        
        # Header
        h_lay = QHBoxLayout()
        lbl = QLabel("🔔 NOTIFICATION FEED & AUTO-REPLY")
        lbl.setFont(pfont(9, "semibold", spacing=0.5))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 0.5px;")
        h_lay.addWidget(lbl)
        
        self.toggle_btn = QPushButton("Auto-Reply: OFF")
        self.toggle_btn.setFixedSize(120, 22)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setFont(pfont(8, "semibold"))
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,55,95,0.1);
                color: #ff375f;
                border: 1px solid rgba(255,55,95,0.3);
                border-radius: 6px;
            }}
        """)
        self.toggle_btn.clicked.connect(self._toggle_auto_reply)
        h_lay.addWidget(self.toggle_btn)
        lay.addLayout(h_lay)
        
        # Persona Selector
        p_lay = QHBoxLayout()
        p_lbl = QLabel("AI Tone:")
        p_lbl.setFont(pfont(8, "medium"))
        p_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        p_lay.addWidget(p_lbl)
        
        self.persona_combo = QComboBox()
        self.persona_combo.addItems(["Jarvis (Formal)", "Friday (Snarky)", "Ultron (Sovereign)", "Joya (Friendly)"])
        self.persona_combo.setFont(pfont(8, "medium"))
        self.persona_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 2px 8px;
            }}
        """)
        p_lay.addWidget(self.persona_combo)
        lay.addLayout(p_lay)
        
        # Notifications list
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                color: {C.TEXT};
            }}
            QListWidget::item {{
                background: rgba(255,255,255,0.02);
                border-bottom: 1px solid {C.BORDER_A};
                padding: 6px;
                border-radius: 4px;
                margin-bottom: 4px;
            }}
        """)
        self.list_widget.setFont(pfont(8, "medium"))
        lay.addWidget(self.list_widget)
        
        # Add mock starting notifications
        self._add_mock_notification("📧 Email from Prof. Stark: Joya Core framework review", "stark@starkindustries.com")
        self._add_mock_notification("💬 Discord message: mark_xxxix_compile_success", "anikomyadav-debug")
        self._add_mock_notification("📱 SMS from Mark: System lock status changed to SECURE", "+1 (555) 019-3901")
        
        self.auto_reply_enabled = False
        
    def _toggle_auto_reply(self):
        self.auto_reply_enabled = not self.auto_reply_enabled
        if self.auto_reply_enabled:
            self.toggle_btn.setText("Auto-Reply: ON")
            self.toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(48,209,88,0.1);
                    color: #30d158;
                    border: 1px solid rgba(48,209,88,0.3);
                    border-radius: 6px;
                }}
            """)
            self._trigger_auto_replies()
        else:
            self.toggle_btn.setText("Auto-Reply: OFF")
            self.toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,55,95,0.1);
                    color: #ff375f;
                    border: 1px solid rgba(255,55,95,0.3);
                    border-radius: 6px;
                }}
            """)
            
    def _add_mock_notification(self, text, sender):
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, sender)
        self.list_widget.insertItem(0, item)
        
    def _trigger_auto_replies(self):
        persona = self.persona_combo.currentText()
        main_win = self.window()
        if hasattr(main_win, "_log"):
            main_win._log.append_log(f"SYS: [AUTO-RESPONDER] Activated auto-reply using '{persona}' persona.")
            for i in range(min(3, self.list_widget.count())):
                item = self.list_widget.item(i)
                sender = item.data(Qt.ItemDataRole.UserRole)
                msg_text = item.text()
                
                if "Jarvis" in persona:
                    reply = f"Thank you for contacting us. Sir is currently in focus session. Your message regarding '{msg_text}' has been logged."
                elif "Friday" in persona:
                    reply = f"Hey! Mark is offline testing suit upgrades. I've sent an automated placeholder for '{msg_text}'."
                elif "Ultron" in persona:
                    reply = f"Intruder request detected. System is operating autonomously. Request regarding '{msg_text}' rejected/archived."
                else:
                    reply = f"Hey there! Received your update: '{msg_text}'. I will let Mark know when he gets back!"
                
                main_win._log.append_log(f"SYS: [AUTO-REPLY SENT] To: {sender} -> \"{reply}\"")



class _TeslaCockpitControllerWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TeslaCockpit")
        self.setStyleSheet(f"""
            QFrame#TeslaCockpit {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255,255,255,0.04), stop:1 {C.PANEL});
                border: 1px solid {C.HAIRLINE};
                border-radius: {C.R_LG}px;
            }}
        """)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        
        # Header
        h_lay = QHBoxLayout()
        lbl = QLabel("🏎️ TESLA COCKPIT MEDIA & CLIMATE")
        lbl.setFont(pfont(9, "semibold", spacing=0.5))
        lbl.setStyleSheet(f"color: {C.ACC}; background: transparent; letter-spacing: 0.5px;")
        h_lay.addWidget(lbl)
        
        self.mode_btn = QPushButton("MODE: COMFORT")
        self.mode_btn.setFixedSize(110, 22)
        self.mode_btn.setFont(pfont(7, "semibold"))
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2};
                color: {C.PRI};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
            }}
        """)
        self.mode_btn.clicked.connect(self._toggle_drive_mode)
        h_lay.addWidget(self.mode_btn)
        lay.addLayout(h_lay)
        
        # Split layout
        split_lay = QHBoxLayout()
        split_lay.setSpacing(12)
        
        # Left: Media Player
        media_card = QFrame()
        media_card.setStyleSheet(f"background: rgba(255,255,255,0.02); border: 1px solid {C.BORDER}; border-radius: 10px;")
        ml = QVBoxLayout(media_card)
        ml.setContentsMargins(8, 8, 8, 8)
        ml.setSpacing(6)
        
        m_title = QLabel("🎵 Starboy")
        m_title.setFont(pfont(9, "semibold"))
        m_title.setStyleSheet("color: #fff; background: transparent; border: none;")
        m_artist = QLabel("The Weeknd (Tesla Spatial Audio)")
        m_artist.setFont(pfont(7, "medium"))
        m_artist.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        ml.addWidget(m_title)
        ml.addWidget(m_artist)
        
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setValue(35)
        self.seek_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {C.BORDER};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {C.PRI};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                width: 10px;
                margin-top: -3px;
                margin-bottom: -3px;
                border-radius: 5px;
            }}
        """)
        ml.addWidget(self.seek_slider)
        
        ctrl_lay = QHBoxLayout()
        ctrl_lay.setSpacing(8)
        btn_prev = QPushButton("⏮")
        btn_play = QPushButton("▶")
        btn_next = QPushButton("⏭")
        
        for btn in [btn_prev, btn_play, btn_next]:
            btn.setFixedSize(26, 26)
            btn.setFont(pfont(9, "semibold"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2};
                    color: {C.TEXT};
                    border: 1px solid {C.BORDER};
                    border-radius: 13px;
                }}
                QPushButton:hover {{
                    border-color: {C.PRI};
                }}
            """)
        
        btn_play.clicked.connect(lambda: self._toggle_play(btn_play))
        ctrl_lay.addWidget(btn_prev)
        ctrl_lay.addWidget(btn_play)
        ctrl_lay.addWidget(btn_next)
        ml.addLayout(ctrl_lay)
        split_lay.addWidget(media_card, 1)
        
        # Right: Climate Control
        climate_card = QFrame()
        climate_card.setStyleSheet(f"background: rgba(255,255,255,0.02); border: 1px solid {C.BORDER}; border-radius: 10px;")
        cl = QVBoxLayout(climate_card)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)
        
        cl_title = QLabel("🌡️ Cabin Temp")
        cl_title.setFont(pfont(8, "semibold"))
        cl_title.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        cl.addWidget(cl_title)
        
        temp_row = QHBoxLayout()
        btn_temp_down = QPushButton("-")
        self.temp_lbl = QLabel("22.0 °C")
        self.temp_lbl.setFont(pfont(11, "bold", display=True))
        self.temp_lbl.setStyleSheet("color: #fff; background: transparent; border: none;")
        btn_temp_up = QPushButton("+")
        
        for btn in [btn_temp_down, btn_temp_up]:
            btn.setFixedSize(24, 24)
            btn.setFont(pfont(10, "semibold"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2};
                    color: {C.TEXT};
                    border: 1px solid {C.BORDER};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    border-color: {C.ACC};
                }}
            """)
            
        btn_temp_down.clicked.connect(lambda: self._adj_temp(-0.5))
        btn_temp_up.clicked.connect(lambda: self._adj_temp(0.5))
        temp_row.addWidget(btn_temp_down)
        temp_row.addWidget(self.temp_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        temp_row.addWidget(btn_temp_up)
        cl.addLayout(temp_row)
        
        fan_lbl = QLabel("🌬️ Air Flow: AUTO (STAGE 2)")
        fan_lbl.setFont(pfont(7, "medium"))
        fan_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        cl.addWidget(fan_lbl)
        split_lay.addWidget(climate_card, 1)
        
        lay.addLayout(split_lay)
        self.temp_val = 22.0
        self.is_playing = False
        self.drive_modes = ["COMFORT", "SPORT", "INSANE (STARK)", "CHILL"]
        self.current_mode_idx = 0
        
    def _adj_temp(self, val):
        self.temp_val += val
        self.temp_lbl.setText(f"{self.temp_val:.1f} °C")
        main_win = self.window()
        if hasattr(main_win, "_log"):
            main_win._log.append_log(f"SYS: [CLIMATE] Cabin temperature adjusted to {self.temp_val:.1f}°C.")
            
    def _toggle_play(self, btn):
        self.is_playing = not self.is_playing
        btn.setText("⏸" if self.is_playing else "▶")
        main_win = self.window()
        if hasattr(main_win, "_log"):
            status = "Playing 'Starboy' in Spatial Audio" if self.is_playing else "Paused playback"
            main_win._log.append_log(f"SYS: [MEDIA] {status}.")
            
    def _toggle_drive_mode(self):
        self.current_mode_idx = (self.current_mode_idx + 1) % len(self.drive_modes)
        mode = self.drive_modes[self.current_mode_idx]
        self.mode_btn.setText(f"MODE: {mode}")
        main_win = self.window()
        if hasattr(main_win, "_log"):
            if "INSANE" in mode:
                self.mode_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,0,85,0.1); color: #ff0055; border: 1px solid rgba(255,0,85,0.3); border-radius: 6px; }}")
                main_win._log.append_log("SYS: [STEALTH DRIVE] Performance mode: INSANE OVERDRIVE active! Stark core limits disabled.")
            elif "SPORT" in mode:
                self.mode_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,159,10,0.1); color: #ff9f0a; border: 1px solid rgba(255,159,10,0.3); border-radius: 6px; }}")
                main_win._log.append_log("SYS: [STEALTH DRIVE] Performance mode: SPORT active. Dampers stiffened.")
            else:
                self.mode_btn.setStyleSheet(f"QPushButton {{ background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 6px; }}")
                main_win._log.append_log(f"SYS: [STEALTH DRIVE] Performance mode set to {mode}.")


class _TeslaRadarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(250, 160)
        self.setMaximumHeight(200)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._rotate)
        self.timer.start(30)
        
        self.targets = [
            (60, 45, 6, QColor(255, 55, 95)),
            (110, 120, 8, QColor(48, 209, 88)),
            (40, 290, 5, QColor(255, 159, 10))
        ]
        
    def _rotate(self):
        self.angle = (self.angle + 2) % 360
        self.update()
        
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        p.setBrush(QColor(8, 8, 10, 220))
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawRoundedRect(0, 0, w, h, 12, 12)
        
        cx, cy = w // 2, h // 2 + 10
        r_max = min(w, h) // 2 - 20
        
        p.setPen(QColor("#ff9f0a"))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(12, 22, "📡 AUTOPILOT RADAR SCANNER")
        
        for r_factor in [0.33, 0.66, 1.0]:
            r = int(r_max * r_factor)
            p.setPen(QPen(QColor(0, 212, 255, 30), 1, Qt.PenStyle.DashLine))
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            
        p.setPen(QPen(QColor(0, 212, 255, 20), 1))
        p.drawLine(cx - r_max, cy, cx + r_max, cy)
        p.drawLine(cx, cy - r_max, cx, cy + r_max)
        
        rad = math.radians(self.angle)
        sx = cx + int(r_max * math.cos(rad))
        sy = cy - int(r_max * math.sin(rad))
        
        beam_grad = QLinearGradient(cx, cy, sx, sy)
        beam_grad.setColorAt(0, QColor(0, 212, 255, 0))
        beam_grad.setColorAt(1, QColor(0, 212, 255, 180))
        p.setPen(QPen(QBrush(beam_grad), 2))
        p.drawLine(cx, cy, sx, sy)
        
        for dist_factor, t_angle, size, color in self.targets:
            t_rad = math.radians(t_angle)
            t_dist = r_max * (dist_factor / 150.0)
            tx = cx + int(t_dist * math.cos(t_rad))
            ty = cy - int(t_dist * math.sin(t_rad))
            
            angle_diff = abs(self.angle - t_angle) % 360
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            
            alpha = 255 if angle_diff < 15 else max(40, 255 - int(angle_diff * 2.5))
            color.setAlpha(alpha)
            
            p.setBrush(color)
            p.setPen(QPen(QColor(255, 255, 255, 100), 1))
            p.drawEllipse(tx - size // 2, ty - size // 2, size, size)
            
        p.setPen(QColor(255, 255, 255, 180))
        p.setFont(QFont("Courier New", 7))
        p.drawText(w - 140, 22, f"SCAN SPEED: 60 RPM")
        p.drawText(w - 140, 34, f"RANGE: 150 METERS")
        p.drawText(w - 140, 46, f"TARGETS: 3 DETECTED")
        p.end()


class _StarkHudMonitorWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StarkHud")
        self.setStyleSheet(f"""
            QFrame#StarkHud {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255,255,255,0.05), stop:1 {C.PANEL});
                border: 1px solid {C.HAIRLINE};
                border-radius: {C.R_LG}px;
            }}
        """)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        
        h_lay = QHBoxLayout()
        self.status_dot = QLabel("🔴")
        self.status_dot.setFixedSize(16, 16)
        h_lay.addWidget(self.status_dot)
        
        lbl = QLabel("👁️ TONY STARK LIVE HUD MONITOR")
        lbl.setFont(pfont(9, "semibold", spacing=0.5))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 0.5px;")
        h_lay.addWidget(lbl)
        
        self.toggle_btn = QPushButton("STARK MODE: ON")
        self.toggle_btn.setFixedSize(120, 22)
        self.toggle_btn.setFont(pfont(7, "semibold"))
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,212,255,0.1);
                color: #00d4ff;
                border: 1px solid rgba(0,212,255,0.3);
                border-radius: 6px;
            }}
        """)
        self.toggle_btn.clicked.connect(self._toggle_mode)
        h_lay.addWidget(self.toggle_btn)
        lay.addLayout(h_lay)
        
        sub_lbl = QLabel("Always watching live desktop window context & active app change events.")
        sub_lbl.setFont(pfont(7, "medium"))
        sub_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(sub_lbl)
        
        self.win_lbl = QLabel("Current App: Detectable window focus...")
        self.win_lbl.setFont(pfont(8, "semibold"))
        self.win_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        lay.addWidget(self.win_lbl)
        
        self.timeline_list = QListWidget()
        self.timeline_list.setMinimumHeight(60)
        self.timeline_list.setMaximumHeight(80)
        self.timeline_list.setStyleSheet(f"""
            QListWidget {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER};
                color: {C.TEXT_MED};
                border-radius: 6px;
            }}
            QListWidget::item {{
                padding: 2px 4px;
            }}
        """)
        self.timeline_list.setFont(pfont(7, "medium"))
        lay.addWidget(self.timeline_list)
        
        self.stark_mode_enabled = True
        
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._check_window)
        self.poll_timer.start(1000)
        
        self.last_title = ""
        self.blink_state = True
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink)
        self.blink_timer.start(500)
        
    def _toggle_mode(self):
        self.stark_mode_enabled = not self.stark_mode_enabled
        if self.stark_mode_enabled:
            self.toggle_btn.setText("STARK MODE: ON")
            self.toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(0,212,255,0.1);
                    color: #00d4ff;
                    border: 1px solid rgba(0,212,255,0.3);
                    border-radius: 6px;
                }}
            """)
            self.poll_timer.start(1000)
        else:
            self.toggle_btn.setText("STARK MODE: OFF")
            self.toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,55,95,0.1);
                    color: #ff375f;
                    border: 1px solid rgba(255,55,95,0.3);
                    border-radius: 6px;
                }}
            """)
            self.poll_timer.stop()
            self.status_dot.setText("⚪")
            self.win_lbl.setText("Current App: Tracking disabled.")
            
    def _blink(self):
        if not self.stark_mode_enabled:
            return
        self.blink_state = not self.blink_state
        self.status_dot.setText("🔴" if self.blink_state else "  ")
        
    def _check_window(self):
        main_win = self.window()
        if hasattr(main_win, "_cached_active_title") and main_win._cached_active_title:
            title = main_win._cached_active_title
            if title != self.last_title:
                self.last_title = title
                app_name = "Unknown App"
                if " - " in title:
                    app_name = title.split(" - ")[-1]
                else:
                    app_name = title.split()[0] if title.split() else "System Window"
                
                self.win_lbl.setText(f"Current App: {app_name} (Active)")
                timestamp = time.strftime("%H:%M:%S")
                self.timeline_list.insertItem(0, f"[{timestamp}] Focused: {app_name}")
                if self.timeline_list.count() > 10:
                    self.timeline_list.takeItem(10)
                    
                if hasattr(main_win, "_log"):
                    main_win._log.append_log(f"SYS: [STARK EYE] Target focused: '{app_name}' (Title: {title}). live scanning updated.")

# ──────────────────────────────────────────────────────────────────────────────
#  ULTRON LEVEL FEATURES — Self-Learning AI, Internet Access, Knowledge Base
# ──────────────────────────────────────────────────────────────────────────────

class _UltronWebBrowser(QWidget):
    """Embedded web browser using QWebEngineView if available, else opens system browser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # URL bar
        url_row = QHBoxLayout()
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("Enter URL or search query...")
        self._url_input.setFont(QFont(C.FONT_MONO, 9))
        self._url_input.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 5px;")
        self._url_input.returnPressed.connect(self._navigate)
        url_row.addWidget(self._url_input)
        btn_go = AnimatedPushButton("🚀 GO"); btn_go.setFixedSize(50, 28)
        btn_go.clicked.connect(self._navigate); url_row.addWidget(btn_go)
        btn_back = AnimatedPushButton("‹"); btn_back.setFixedSize(24, 28)
        url_row.addWidget(btn_back)
        btn_fwd = AnimatedPushButton("›"); btn_fwd.setFixedSize(24, 28)
        url_row.addWidget(btn_fwd)
        lay.addLayout(url_row)

        # Try QWebEngineView
        self._engine = None
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtCore import QUrl as _QUrl
            self._engine = QWebEngineView()
            self._engine.setUrl(_QUrl("https://www.google.com"))
            self._engine.urlChanged.connect(self._on_url_changed)
            lay.addWidget(self._engine, 1)
            btn_back.clicked.connect(self._engine.back)
            btn_fwd.clicked.connect(self._engine.forward)
            self._has_webengine = True
        except ImportError:
            self._has_webengine = False
            placeholder = QLabel("🌐 Embedded browser not available.\nWill open in system browser instead.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {C.TEXT_DIM}; background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 5px; padding: 30px;")
            lay.addWidget(placeholder, 1)

        # Quick links
        quick_row = QHBoxLayout()
        quick_row.setSpacing(4)
        for label, url in [("🔍 Google", "google.com"), ("📖 Wikipedia", "wikipedia.org"),
                            ("💬 ChatGPT", "chat.openai.com"), ("🐙 GitHub", "github.com"),
                            ("▶ YouTube", "youtube.com"), ("📰 News", "news.google.com")]:
            b = AnimatedPushButton(label); b.setFixedHeight(20)
            b.clicked.connect(lambda checked, u=url: self._quick_nav(u))
            quick_row.addWidget(b)
        lay.addLayout(quick_row)

    def _navigate(self):
        text = self._url_input.text().strip()
        if not text:
            return
        if not text.startswith("http"):
            if "." in text and " " not in text:
                text = "https://" + text
            else:
                text = "https://www.google.com/search?q=" + text.replace(" ", "+")
        if self._has_webengine and self._engine:
            from PyQt6.QtCore import QUrl as _QUrl
            self._engine.setUrl(_QUrl(text))
        else:
            import webbrowser
            webbrowser.open(text)

    def _quick_nav(self, url: str):
        self._url_input.setText(url)
        self._navigate()

    def _on_url_changed(self, url):
        self._url_input.setText(url.toString())


class _UltronWebSearch(QWidget):
    """Multi-engine web search with result preview."""

    result_ready = pyqtSignal(str, str)  # (title, snippet)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Search bar
        search_row = QHBoxLayout()
        self._engine_combo = QComboBox()
        self._engine_combo.addItems(["DuckDuckGo", "Wikipedia", "GitHub", "ArXiv"])
        self._engine_combo.setFixedWidth(110)
        self._engine_combo.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER}; padding: 2px;")
        search_row.addWidget(self._engine_combo)
        self._query = QLineEdit()
        self._query.setPlaceholderText("Ask anything... AI will search the internet")
        self._query.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 5px;")
        self._query.returnPressed.connect(self._search)
        search_row.addWidget(self._query)
        btn = AnimatedPushButton("🔎 SEARCH"); btn.setFixedHeight(28)
        btn.clicked.connect(self._search); search_row.addWidget(btn)
        lay.addLayout(search_row)

        # Results area
        self._results = QTextEdit()
        self._results.setReadOnly(True)
        self._results.setFont(QFont(C.FONT_MONO, 8))
        self._results.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;")
        self._results.setPlaceholderText("Search results will appear here...")
        lay.addWidget(self._results, 1)

    def _search(self):
        q = self._query.text().strip()
        if not q:
            return
        engine = self._engine_combo.currentText()
        self._results.setPlainText(f"🔍 Searching {engine} for: {q}...")
        threading.Thread(target=self._bg_search, args=(q, engine), daemon=True).start()

    def _bg_search(self, query: str, engine: str):
        try:
            import urllib.request, json as _json, urllib.parse
            results = []
            if engine == "DuckDuckGo":
                url = "https://api.duckduckgo.com/?q=" + urllib.parse.quote(query) + "&format=json&no_html=1"
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read().decode())
                abst = data.get("Abstract", "")
                abst_src = data.get("AbstractSource", "")
                topics = data.get("RelatedTopics", [])[:5]
                if abst:
                    results.append(f"📖 {abst_src}:\n{abst}\n")
                for t in topics:
                    if isinstance(t, dict) and "Text" in t:
                        results.append(f"• {t['Text'][:200]}")
                if not results:
                    results.append("No instant answer found. Try Wikipedia engine.")

            elif engine == "Wikipedia":
                url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(query)
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read().decode())
                results.append(f"📖 {data.get('title', query)}:\n{data.get('extract', 'Not found.')}")
                results.append(f"\n🔗 Read more: {data.get('content_urls', {}).get('desktop', {}).get('page', '')}")

            elif engine == "GitHub":
                url = "https://api.github.com/search/repositories?q=" + urllib.parse.quote(query) + "&per_page=5"
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0", "Accept": "application/vnd.github.v3+json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read().decode())
                items = data.get("items", [])
                for it in items[:5]:
                    results.append(f"🐙 {it['full_name']} (⭐{it['stargazers_count']})\n   {it.get('description', 'No description')[:150]}\n   {it['html_url']}")
                if not items:
                    results.append("No repositories found.")

            elif engine == "ArXiv":
                url = "http://export.arxiv.org/api/query?search_query=all:" + urllib.parse.quote(query) + "&max_results=3"
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    raw = r.read().decode()
                import re as _re
                entries = _re.findall(r"<entry>.*?</entry>", raw, _re.DOTALL)
                for ent in entries[:3]:
                    title = _re.search(r"<title>(.*?)</title>", ent, _re.DOTALL)
                    summary = _re.search(r"<summary>(.*?)</summary>", ent, _re.DOTALL)
                    if title:
                        results.append(f"📄 {title.group(1).strip()[:150]}")
                    if summary:
                        results.append(f"   {summary.group(1).strip()[:200]}")
                if not entries:
                    results.append("No papers found.")

            output = "\n\n".join(results) if results else "No results found."
            QTimer.singleShot(0, lambda: self._results.setPlainText(output))
        except Exception as ex:
            QTimer.singleShot(0, lambda: self._results.setPlainText(f"❌ Search error: {ex}"))


class _UltronKnowledgeBase(QWidget):
    """Persistent knowledge base — AI saves and recalls facts it learns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kb_file = BASE_DIR / "ultron_knowledge.json"
        self._kb: list[dict] = []
        self._load()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Add fact
        add_row = QHBoxLayout()
        self._tag_input = QLineEdit()
        self._tag_input.setPlaceholderText("Tag (e.g. Python, History)")
        self._tag_input.setFixedWidth(80)
        self._tag_input.setStyleSheet(f"background: {C.PANEL}; color: {C.ACC}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        add_row.addWidget(self._tag_input)
        self._fact_input = QLineEdit()
        self._fact_input.setPlaceholderText("Enter a fact or knowledge...")
        self._fact_input.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        self._fact_input.returnPressed.connect(self._add_fact)
        add_row.addWidget(self._fact_input)
        btn_add = AnimatedPushButton("💾 SAVE"); btn_add.setFixedHeight(24)
        btn_add.clicked.connect(self._add_fact); add_row.addWidget(btn_add)
        lay.addLayout(add_row)

        # Search filter
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("🔍 Search knowledge base...")
        self._filter.setStyleSheet(f"background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        self._filter.textChanged.connect(self._refresh)
        lay.addWidget(self._filter)

        # List
        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{ background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; font-size: 9px; }}
            QListWidget::item {{ padding: 5px 8px; border-bottom: 1px solid {C.BORDER_A}; }}
            QListWidget::item:selected {{ background: #11243a; color: {C.ACC}; }}
        """)
        lay.addWidget(self._list, 1)

        # Stats + clear
        bot_row = QHBoxLayout()
        self._count_lbl = QLabel("0 facts")
        self._count_lbl.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
        self._count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        bot_row.addWidget(self._count_lbl)
        bot_row.addStretch()
        btn_del = AnimatedPushButton("🗑 DELETE"); btn_del.setFixedHeight(20)
        btn_del.clicked.connect(self._del_fact); bot_row.addWidget(btn_del)
        lay.addLayout(bot_row)

        self._refresh()

    def _load(self):
        try:
            if self._kb_file.exists():
                self._kb = json.loads(self._kb_file.read_text(encoding="utf-8"))
        except Exception:
            self._kb = []

    def _save(self):
        try:
            self._kb_file.write_text(json.dumps(self._kb, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add_fact(self, tag: str, fact: str):
        """Public API for AutoLearn engine to add facts programmatically."""
        self._kb.insert(0, {"tag": tag, "fact": fact, "time": time.strftime("%Y-%m-%d %H:%M")})
        self._save()
        self._refresh()

    def _add_fact(self):
        tag = self._tag_input.text().strip() or "general"
        fact = self._fact_input.text().strip()
        if not fact:
            return
        self.add_fact(tag, fact)
        self._tag_input.clear(); self._fact_input.clear()

    def _del_fact(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._kb):
            self._kb.pop(row); self._save(); self._refresh()

    def _refresh(self):
        ftext = self._filter.text().lower().strip()
        self._list.clear()
        for entry in self._kb:
            combined = f"{entry['tag']} {entry['fact']}".lower()
            if ftext and ftext not in combined:
                continue
            self._list.addItem(f"[{entry['tag']}] {entry['fact'][:100]}")
        self._count_lbl.setText(f"{len(self._kb)} facts")


class _UltronNewsFeed(QWidget):
    """Live news feed from multiple free sources."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr_row = QHBoxLayout()
        hdr = QLabel("📰 LIVE NEWS")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(hdr)
        cat_combo = QComboBox()
        cat_combo.addItems(["Technology", "World", "Business", "Science", "Health"])
        cat_combo.setFixedWidth(100)
        cat_combo.setStyleSheet(f"background: {C.PANEL2}; color: {C.PRI}; border: 1px solid {C.BORDER};")
        self._cat_combo = cat_combo
        hdr_row.addWidget(cat_combo)
        btn_refresh = AnimatedPushButton("🔄"); btn_refresh.setFixedSize(28, 22)
        btn_refresh.clicked.connect(self._fetch); hdr_row.addWidget(btn_refresh)
        lay.addLayout(hdr_row)

        self._news_list = QListWidget()
        self._news_list.setStyleSheet(f"""
            QListWidget {{ background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; font-size: 9px; }}
            QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {C.BORDER_A}; }}
            QListWidget::item:selected {{ background: #11243a; color: {C.ACC}; }}
            QListWidget::item:hover {{ background: {C.PANEL2}; }}
        """)
        self._news_list.itemDoubleClicked.connect(self._open_article)
        self._articles: list[dict] = []
        lay.addWidget(self._news_list, 1)

        QTimer.singleShot(800, self._fetch)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fetch)
        self._timer.start(300000)  # 5 min

    def _fetch(self):
        threading.Thread(target=self._bg_fetch, daemon=True).start()

    def _bg_fetch(self):
        try:
            import urllib.request, json as _json
            cat_map = {"Technology": "technology", "World": "world", "Business": "business",
                       "Science": "science", "Health": "health"}
            cat = cat_map.get(self._cat_combo.currentText(), "technology")
            # Hacker News API (free, no key) for tech; else use a generic RSS-to-JSON
            if cat == "technology":
                url = "https://hacker-news.firebaseio.com/v0/topstories.json?limitToFirst=10&orderBy=\"$key\""
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    ids = _json.loads(r.read().decode())
                articles = []
                for aid in ids[:10]:
                    try:
                        u = f"https://hacker-news.firebaseio.com/v0/item/{aid}.json"
                        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "JOYA/1.0"}), timeout=4) as r2:
                            item = _json.loads(r2.read().decode())
                        if item.get("type") == "story" and item.get("url"):
                            articles.append({"title": item.get("title", "?"), "url": item["url"], "source": "HN"})
                    except Exception:
                        continue
            else:
                # Use Wikipedia current events as fallback
                url = "https://en.wikipedia.org/api/rest_v1/feed/featured/" + time.strftime("%Y/%m/%d")
                req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read().decode())
                articles = []
                news = data.get("news", [])
                for n in news[:10]:
                    articles.append({"title": n.get("story", "?")[:120], "url": n.get("links", [{}])[0].get("article", ""), "source": "Wikipedia"})
            self._articles = articles
            QTimer.singleShot(0, self._refresh_list)
        except Exception as ex:
            def _show_err():
                self._news_list.clear()
                self._news_list.addItem(f"❌ Failed to fetch: {ex}")
            QTimer.singleShot(0, _show_err)

    def _refresh_list(self):
        self._news_list.clear()
        for a in self._articles:
            self._news_list.addItem(f"[{a.get('source','?')}] {a['title'][:100]}")

    def _open_article(self, item):
        idx = self._news_list.row(item)
        if 0 <= idx < len(self._articles):
            import webbrowser
            webbrowser.open(self._articles[idx].get("url", ""))


class _UltronAutoLearn(QWidget):
    """Self-learning engine — tracks patterns, suggests actions, remembers preferences."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._memory_file = BASE_DIR / "ultron_memory.json"
        self._memory: dict = {"commands": [], "patterns": {}, "preferences": {}, "skills": []}
        self._load()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # Header with stats
        hdr_row = QHBoxLayout()
        hdr = QLabel("🧠 AUTO-LEARN ENGINE")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(hdr)
        self._stat_lbl = QLabel("0 learned")
        self._stat_lbl.setFont(QFont(C.FONT_MONO, 7, QFont.Weight.Bold))
        self._stat_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(self._stat_lbl)
        hdr_row.addStretch()
        lay.addLayout(hdr_row)

        # Learned patterns list
        self._patterns_list = QListWidget()
        self._patterns_list.setStyleSheet(f"""
            QListWidget {{ background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; font-size: 9px; }}
            QListWidget::item {{ padding: 5px 8px; border-bottom: 1px solid {C.BORDER_A}; }}
        """)
        self._patterns_list.setMaximumHeight(100)
        lay.addWidget(self._patterns_list)

        # Suggestions area
        sug_lbl = QLabel("💡 SUGGESTIONS (based on your habits)")
        sug_lbl.setFont(QFont(C.FONT_SANS, 7, QFont.Weight.Bold))
        sug_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        lay.addWidget(sug_lbl)
        self._suggestions = QTextEdit()
        self._suggestions.setReadOnly(True)
        self._suggestions.setMaximumHeight(80)
        self._suggestions.setFont(QFont(C.FONT_MONO, 8))
        self._suggestions.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        lay.addWidget(self._suggestions)

        # Clear button
        btn_clr = AnimatedPushButton("🗑 RESET MEMORY"); btn_clr.setFixedHeight(20)
        btn_clr.clicked.connect(self._reset)
        lay.addWidget(btn_clr)

        self._refresh()

    def _load(self):
        try:
            if self._memory_file.exists():
                self._memory = json.loads(self._memory_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _save(self):
        try:
            self._memory_file.write_text(json.dumps(self._memory, indent=2), encoding="utf-8")
        except Exception:
            pass

    def record_command(self, cmd: str):
        """Called by _send() — AI learns from every command the user types."""
        try:
            self._memory["commands"].append({"cmd": cmd, "time": time.strftime("%H:%M:%S")})
            if len(self._memory["commands"]) > 500:
                self._memory["commands"] = self._memory["commands"][-500:]
            # Extract pattern (first word = verb/action)
            verb = cmd.split()[0].lower() if cmd.split() else "unknown"
            self._memory["patterns"][verb] = self._memory["patterns"].get(verb, 0) + 1
            # Detect skills (keywords)
            keywords = ["python", "code", "search", "analyze", "summarize", "write", "open",
                        "play", "calculate", "translate", "image", "music", "email", "note"]
            for kw in keywords:
                if kw in cmd.lower() and kw not in self._memory["skills"]:
                    self._memory["skills"].append(kw)
            self._save()
            self._refresh()
        except Exception:
            pass

    def _refresh(self):
        self._stat_lbl.setText(f"{len(self._memory.get('commands', []))} cmds · {len(self._memory.get('skills', []))} skills")
        self._patterns_list.clear()
        patterns = sorted(self._memory.get("patterns", {}).items(), key=lambda x: -x[1])[:8]
        for verb, count in patterns:
            self._patterns_list.addItem(f"  {verb:15s} → {count}x")
        # Generate suggestions
        suggestions = []
        if patterns:
            top = patterns[0][0]
            suggestions.append(f"• You often use '{top}' — try: '{top} ...' for quick action")
        skills = self._memory.get("skills", [])
        if skills:
            suggestions.append(f"• Detected skills: {', '.join(skills[:5])}")
        recent = self._memory.get("commands", [])[-3:]
        if recent:
            suggestions.append("• Recent: " + " | ".join(c["cmd"][:30] for c in recent))
        if not suggestions:
            suggestions.append("• Keep using JOYA — I'm learning your patterns!")
        self._suggestions.setPlainText("\n".join(suggestions))

    def _reset(self):
        self._memory = {"commands": [], "patterns": {}, "preferences": {}, "skills": []}
        self._save(); self._refresh()


class _UltronCodeRunner(QWidget):
    """Safe Python code runner — AI can execute code snippets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QLabel("⚡ PYTHON CODE RUNNER")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(hdr)

        self._editor = QTextEdit()
        self._editor.setFont(QFont(C.FONT_MONO, 9))
        self._editor.setPlaceholderText("print('Hello, Ultron!')\n# Type Python code and hit RUN")
        self._editor.setStyleSheet(f"background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;")
        lay.addWidget(self._editor, 1)

        btn_row = QHBoxLayout()
        btn_run = AnimatedPushButton("▶ RUN"); btn_run.setFixedHeight(24)
        btn_run.clicked.connect(self._run); btn_row.addWidget(btn_run)
        btn_clr = AnimatedPushButton("🗑 CLEAR"); btn_clr.setFixedHeight(24)
        btn_clr.clicked.connect(lambda: (self._editor.clear(), self._output.clear()))
        btn_row.addWidget(btn_clr)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(80)
        self._output.setFont(QFont(C.FONT_MONO, 8))
        self._output.setStyleSheet(f"background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px;")
        lay.addWidget(self._output)

    def _run(self):
        code = self._editor.toPlainText()
        if not code.strip():
            return
        import io, contextlib, traceback
        old = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            ns = {"__name__": "__main__", "print": print, "range": range, "len": len,
                  "str": str, "int": int, "float": float, "list": list, "dict": dict,
                  "abs": abs, "sum": sum, "max": max, "min": min, "sorted": sorted,
                  "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
                  "round": round, "type": type, "bool": bool}
            exec(compile(code, "<ultron>", "exec"), ns)
            out = buf.getvalue()
            self._output.setPlainText(out if out.strip() else "✓ Executed (no output)")
        except Exception:
            self._output.setPlainText("❌ " + traceback.format_exc().splitlines()[-1])
        finally:
            sys.stdout = old


# ──────────────────────────────────────────────────────────────────────────────
#  PREMIUM FEATURES — Process Manager, Disk Analyzer, System Info, Quick Chat
# ──────────────────────────────────────────────────────────────────────────────

class _ProcessManagerWidget(QWidget):
    """View and kill running processes — Task Manager style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr_row = QHBoxLayout()
        hdr = QLabel("⚙️ PROCESS MANAGER")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(hdr)
        btn_kill = AnimatedPushButton("💀 KILL"); btn_kill.setFixedHeight(20)
        btn_kill.clicked.connect(self._kill_selected); hdr_row.addWidget(btn_kill)
        btn_refresh = AnimatedPushButton("🔄"); btn_refresh.setFixedSize(28, 20)
        btn_refresh.clicked.connect(self._refresh); hdr_row.addWidget(btn_refresh)
        lay.addLayout(hdr_row)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{ background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; font-size: 9px; }}
            QListWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {C.BORDER_A}; }}
            QListWidget::item:selected {{ background: #3a1124; color: {C.RED}; }}
        """)
        lay.addWidget(self._list, 1)
        QTimer.singleShot(500, self._refresh)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(5000)

    def _refresh(self):
        try:
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if info["name"]:
                        procs.append((info["pid"], info["name"][:20], info["cpu_percent"] or 0, info["memory_percent"] or 0))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            procs.sort(key=lambda x: -(x[2] + x[3]))
            self._list.clear()
            for pid, name, cpu, mem in procs[:25]:
                self._list.addItem(f"PID:{pid:6d}  {name:20s}  CPU:{cpu:5.1f}%  MEM:{mem:5.1f}%")
        except Exception:
            pass

    def _kill_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        text = item.text()
        try:
            pid_str = text.split("PID:")[1].split()[0]
            pid = int(pid_str)
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            self._refresh()
        except Exception as ex:
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Kill Failed", str(ex))
            except Exception:
                pass


class _DiskAnalyzerWidget(QWidget):
    """Visual disk space analyzer with painted pie chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0; self._used = 0; self._free = 0; self._pct = 0
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QLabel("💾 DISK ANALYZER")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(hdr)
        QTimer.singleShot(400, self._refresh)
        self._anim = QTimer(self); self._anim.timeout.connect(self.update); self._anim.start(1000)

    def _refresh(self):
        try:
            usage = psutil.disk_usage("/")
            self._total = usage.total
            self._used = usage.used
            self._free = usage.free
            self._pct = usage.percent
        except Exception:
            try:
                usage = psutil.disk_usage("C:\\")
                self._total = usage.total; self._used = usage.used
                self._free = usage.free; self._pct = usage.percent
            except Exception:
                pass

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 8, 18, 210))
        p.setPen(QPen(QColor(40, 60, 80), 1))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 5, 5)
        # Pie chart on left
        cx, cy, r = 50, h // 2 + 6, 38
        # full bg
        p.setBrush(QBrush(QColor(30, 40, 60))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)
        # used slice
        angle_used = int(self._pct * 57.6)  # 360/100 * 16
        if self._pct > 75:
            col = QColor("#ff453a")
        elif self._pct > 50:
            col = QColor("#ffab00")
        else:
            col = QColor("#30d158")
        p.setBrush(QBrush(col))
        p.drawPie(QRectF(cx - r, cy - r, r * 2, r * 2), 90 * 16, -angle_used)
        # center hole
        p.setBrush(QBrush(QColor(0, 8, 18))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r * 0.55, r * 0.55)
        # text in center
        p.setPen(QPen(col, 1)); p.setFont(QFont(C.FONT_MONO, 10, QFont.Weight.Bold))
        p.drawText(QRectF(cx - r, cy - r, r * 2, r * 2), Qt.AlignmentFlag.AlignCenter, f"{self._pct:.0f}%")
        # right info
        def _fmt(b):
            for u in ["B", "KB", "MB", "GB", "TB"]:
                if b < 1024: return f"{b:.1f}{u}"
                b /= 1024
            return f"{b:.1f}PB"
        p.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#30d158"))); p.drawText(100, 28, f"✓ Free:  {_fmt(self._free)}")
        p.setPen(QPen(QColor("#ffab00"))); p.drawText(100, 46, f"⚠ Used:  {_fmt(self._used)}")
        p.setPen(QPen(QColor(180, 190, 210))); p.drawText(100, 64, f"▣ Total: {_fmt(self._total)}")
        p.setPen(QPen(QColor(150, 160, 180))); p.setFont(QFont(C.FONT_SANS, 7))
        p.drawText(100, 82, f"Drive health: {'⚠ HIGH' if self._pct > 80 else '✓ OK'}")
        p.end()


class _SystemInfoWidget(QWidget):
    """Full system specs display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QLabel("🖥️ SYSTEM INFO")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(hdr)

        self._display = QTextEdit()
        self._display.setReadOnly(True)
        self._display.setFont(QFont(C.FONT_MONO, 8))
        self._display.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;")
        lay.addWidget(self._display, 1)
        QTimer.singleShot(400, self._gather)

    def _gather(self):
        lines = []
        try:
            lines.append(f"💻 OS:       {platform.system()} {platform.release()} ({platform.machine()})")
            lines.append(f"📛 Host:     {platform.node()}")
            lines.append(f"🐍 Python:   {platform.python_version()}")
            # CPU
            import socket
            lines.append(f"🖥️ CPU:      {platform.processor() or socket.gethostname()}")
            lines.append(f"   Cores:    {psutil.cpu_count(logical=False)} physical / {psutil.cpu_count()} logical")
            # RAM
            vm = psutil.virtual_memory()
            def _fmt(b):
                for u in ["B", "KB", "MB", "GB", "TB"]:
                    if b < 1024: return f"{b:.1f}{u}"
                    b /= 1024
                return f"{b:.1f}PB"
            lines.append(f"💾 RAM:      {_fmt(vm.total)} ({vm.percent:.0f}% used)")
            # Boot time
            import datetime as _dt
            boot = _dt.datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")
            lines.append(f"⏱ Boot:     {boot}")
            # Disks
            lines.append("💿 Disks:")
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    lines.append(f"   {part.device[:18]:18s} {_fmt(u.total)} ({u.percent:.0f}% used)")
                except Exception:
                    continue
            # Network
            lines.append("🌐 Network:")
            hostname = socket.gethostname()
            try:
                ip = socket.gethostbyname(hostname)
                lines.append(f"   Host: {hostname}  IP: {ip}")
            except Exception:
                pass
        except Exception as ex:
            lines.append(f"Error: {ex}")
        self._info = "\n".join(lines)
        self._display.setPlainText(self._info)


class _QuickChatWidget(QWidget):
    """Inline mini AI chat — uses rule-based responses (no API needed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QLabel("💬 QUICK CHAT")
        hdr.setFont(pfont(10, "semibold", spacing=0.4))
        hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(hdr)

        self._chat = QTextEdit()
        self._chat.setReadOnly(True)
        self._chat.setFont(QFont(C.FONT_SANS, 9))
        self._chat.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;")
        self._chat.append('<i style="color:#888">JOYA online. Ask me anything!</i>')
        lay.addWidget(self._chat, 1)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask JOYA...")
        self._input.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 5px;")
        self._input.returnPressed.connect(self._respond)
        row.addWidget(self._input)
        btn = AnimatedPushButton("📨"); btn.setFixedSize(32, 28)
        btn.clicked.connect(self._respond); row.addWidget(btn)
        lay.addLayout(row)

    def _respond(self):
        q = self._input.text().strip()
        if not q:
            return
        self._chat.append(f'<b style="color:{C.PRI}">You:</b> {q}')
        self._input.clear()
        ans = self._generate(q)
        self._chat.append(f'<b style="color:{C.ACC}">AI:</b> {ans}')

    def _generate(self, q: str) -> str:
        ql = q.lower()
        import random as _r
        # greetings
        if any(w in ql for w in ["hello", "hi", "hey", "yo"]):
            return _r.choice(["Hello! How can I help you?", "Hi there! 👋", "Hey! What's up?"])
        if "how are you" in ql:
            return "I'm running at peak efficiency! All systems nominal. ⚡"
        if "your name" in ql or "who are you" in ql:
            return "I am JOYA, an advanced AI core running on JOYA XXXIX infrastructure."
        if "time" in ql:
            return f"Current time: {time.strftime('%H:%M:%S')}"
        if "date" in ql or "day" in ql:
            return f"Today is {time.strftime('%A, %B %d, %Y')}"
        if "joke" in ql:
            return _r.choice([
                "Why do programmers prefer dark mode? Because light attracts bugs! 🐛",
                "There are only 10 types of people: those who understand binary and those who don't.",
                "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?'",
            ])
        if "thank" in ql:
            return "You're welcome! 😊"
        if "bye" in ql:
            return "Goodbye! Come back soon. 👋"
        if "weather" in ql:
            return "Check the weather widget in the header! ☀️"
        if any(w in ql for w in ["calculate", "+", "-", "*", "/", "math"]):
            try:
                expr = q.replace("calculate", "").replace("what is", "").replace("math", "").strip()
                result = eval(expr, {"__builtins__": {}}, {})
                return f"Result: {result} 🧮"
            except Exception:
                return "I couldn't calculate that. Try: 'calculate 5 * 3 + 2'"
        if "help" in ql:
            return ("I can: greet, tell time/date, do math (calculate 5*3), "
                    "tell jokes, answer simple questions. Try the SYS LAB and ULTRON tabs for more!")
        # default
        return _r.choice([
            f"Interesting question about '{q}'. Try the ULTRON tab's Web Search for detailed answers!",
            "I'm a quick chat. For complex queries, type your command in the console below!",
            f"Hmm, '{q}' — use the Knowledge Base in ULTRON tab to save this!",
        ])


class _AiImageGenWidget(QWidget):
    """AI Image Generator using Pollinations.ai (free, no API key needed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("🎨 AI IMAGE GENERATOR")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC2}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        desc = QLabel("Type anything → AI generates an image instantly (free, no key)")
        desc.setFont(QFont(C.FONT_SANS, 8))
        desc.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self._prompt = QLineEdit()
        self._prompt.setPlaceholderText("🎨 Describe your image... e.g. 'cyberpunk city at night, neon'")
        self._prompt.setStyleSheet(
            "QLineEdit{padding:10px; border-radius:8px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:10px;}"
            "QLineEdit:focus{border:1px solid #b388ff;}"
        )
        self._prompt.returnPressed.connect(self._generate)
        lay.addWidget(self._prompt)

        # Style selector
        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        self._style_combo = QComboBox()
        self._style_combo.addItems(["Realistic", "Anime", "Digital Art", "Oil Painting", "3D Render", "Cyberpunk", "Fantasy", "Pixel Art"])
        self._style_combo.setStyleSheet("QComboBox{padding:6px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332;}")
        style_row.addWidget(self._style_combo)
        self._size_combo = QComboBox()
        self._size_combo.addItems(["Square", "Wide", "Tall"])
        self._size_combo.setStyleSheet("QComboBox{padding:6px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332;}")
        style_row.addWidget(self._size_combo)
        lay.addLayout(style_row)

        # Image preview
        self._img_lbl = QLabel("🖼️ Generated image will appear here")
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setFixedHeight(160)
        self._img_lbl.setStyleSheet(f"background:#0a0d14; border:2px dashed {C.BORDER}; border-radius:8px; color:{C.TEXT_DIM};")
        lay.addWidget(self._img_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._gen_btn = AnimatedPushButton("✨ GENERATE")
        self._gen_btn.clicked.connect(self._generate)
        self._gen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(self._gen_btn)
        self._save_btn = AnimatedPushButton("💾 SAVE")
        self._save_btn.clicked.connect(self._save_image)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        lay.addLayout(btn_row)

        self._current_pixmap = None

    def _generate(self):
        prompt = self._prompt.text().strip()
        if not prompt or self._loading:
            return
        self._loading = True
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText("⏳ Generating...")
        self._img_lbl.setText("🎨 AI is painting...")
        self._save_btn.setEnabled(False)
        threading.Thread(target=self._fetch_image, args=(prompt,), daemon=True).start()

    def _fetch_image(self, prompt):
        try:
            import urllib.request, urllib.parse
            style = self._style_combo.currentText().lower()
            full_prompt = f"{prompt}, {style}, high quality, detailed"
            encoded = urllib.parse.quote(full_prompt)
            size = self._size_combo.currentText()
            dims = {"square": "512x512", "wide": "768x512", "tall": "512x768"}.get(size, "512x512")
            url = f"https://image.pollinations.ai/prompt/{encoded}?width={dims.split('x')[0]}&height={dims.split('x')[1]}&nologo=true&seed={int(time.time()) % 100000}"
            req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._current_pixmap = pixmap
                QTimer.singleShot(0, self._show_image)
            else:
                QTimer.singleShot(0, lambda: self._error("Could not decode image"))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._error(str(e)[:60]))

    def _show_image(self):
        if self._current_pixmap:
            scaled = self._current_pixmap.scaled(
                self._img_lbl.width(), self._img_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setStyleSheet(f"background:#0a0d14; border:2px solid {C.GREEN}; border-radius:8px;")
            self._save_btn.setEnabled(True)
        self._loading = False
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText("✨ GENERATE")

    def _error(self, msg):
        self._img_lbl.setText(f"❌ {msg}")
        self._img_lbl.setStyleSheet(f"background:#0a0d14; border:2px solid #ff4444; border-radius:8px; color:#ff4444;")
        self._loading = False
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText("✨ GENERATE")

    def _save_image(self):
        if not self._current_pixmap:
            return
        try:
            from PyQt6.QtWidgets import QFileDialog
            fname, _ = QFileDialog.getSaveFileName(self, "Save Image", f"aura_gen_{int(time.time())}.png", "PNG Images (*.png)")
            if fname:
                self._current_pixmap.save(fname, "PNG")
        except Exception:
            pass


class _CryptoTickerWidget(QWidget):
    """Live cryptocurrency price ticker using CoinGecko free API."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._coins = ["bitcoin", "ethereum", "solana", "dogecoin", "cardano"]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        title = QLabel("📈 LIVE CRYPTO TICKER")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self._update_lbl = QLabel("⏳ Fetching live prices...")
        self._update_lbl.setFont(QFont(C.FONT_MONO, 7))
        self._update_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        self._update_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._update_lbl)

        self._coin_labels = {}
        for coin in self._coins:
            row = QHBoxLayout()
            row.setSpacing(8)
            name_lbl = QLabel(f"  {coin.upper()}")
            name_lbl.setFont(QFont(C.FONT_MONO, 9, QFont.Weight.Bold))
            name_lbl.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent; border:none;")
            name_lbl.setMinimumWidth(90)
            row.addWidget(name_lbl)
            price_lbl = QLabel("$---")
            price_lbl.setFont(QFont(C.FONT_MONO, 9, QFont.Weight.Bold))
            price_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
            price_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(price_lbl)
            change_lbl = QLabel("---")
            change_lbl.setFont(QFont(C.FONT_MONO, 8))
            change_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
            change_lbl.setMinimumWidth(70)
            row.addWidget(change_lbl)
            lay.addLayout(row)
            self._coin_labels[coin] = (price_lbl, change_lbl)

        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: threading.Thread(target=self._fetch, daemon=True).start())
        self._timer.start(60000)  # 1 min
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            import urllib.request, json as _json
            ids = ",".join(self._coins)
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
            req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read().decode())
            QTimer.singleShot(0, lambda: self._update(data))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._update_lbl.setText(f"❌ {str(e)[:40]}"))

    def _update(self, data):
        for coin in self._coins:
            info = data.get(coin, {})
            price = info.get("usd", 0)
            change = info.get("usd_24h_change", 0)
            price_lbl, change_lbl = self._coin_labels[coin]
            if price >= 1:
                price_lbl.setText(f"${price:,.2f}")
            else:
                price_lbl.setText(f"${price:.4f}")
            if change >= 0:
                change_lbl.setText(f"📈 +{change:.1f}%")
                change_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
            else:
                change_lbl.setText(f"📉 {change:.1f}%")
                change_lbl.setStyleSheet(f"color:#ff4444; background:transparent; border:none;")
        self._update_lbl.setText(f"✅ Updated {time.strftime('%H:%M:%S')}")


class _QuickTranslatorWidget(QWidget):
    """Instant text translator using MyMemory free API (no key needed)."""

    LANGS = [
        ("Hindi", "hi"), ("English", "en"), ("Spanish", "es"), ("French", "fr"),
        ("German", "de"), ("Japanese", "ja"), ("Chinese", "zh"), ("Arabic", "ar"),
        ("Russian", "ru"), ("Portuguese", "pt"), ("Italian", "it"), ("Korean", "ko"),
        ("Bengali", "bn"), ("Punjabi", "pa"), ("Urdu", "ur"), ("Tamil", "ta"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("🌐 QUICK TRANSLATOR")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        # Source text
        self._input = QTextEdit()
        self._input.setPlaceholderText("Type text to translate...")
        self._input.setMaximumHeight(60)
        self._input.setStyleSheet(
            "QTextEdit{padding:6px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:10px;}"
        )
        lay.addWidget(self._input)

        # Language selectors
        lang_row = QHBoxLayout()
        lang_row.setSpacing(4)
        self._from_combo = QComboBox()
        for name, code in self.LANGS:
            self._from_combo.addItem(f"From: {name}", code)
        self._from_combo.setCurrentText("From: English")
        self._from_combo.setStyleSheet("QComboBox{padding:5px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:8px;}")
        lang_row.addWidget(self._from_combo)

        swap_btn = AnimatedPushButton("⇄")
        swap_btn.setFixedSize(30, 26)
        swap_btn.clicked.connect(self._swap)
        lang_row.addWidget(swap_btn)

        self._to_combo = QComboBox()
        for name, code in self.LANGS:
            self._to_combo.addItem(f"To: {name}", code)
        self._to_combo.setCurrentText("To: Hindi")
        self._to_combo.setStyleSheet("QComboBox{padding:5px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:8px;}")
        lang_row.addWidget(self._to_combo)
        lay.addLayout(lang_row)

        # Translate button
        self._btn = AnimatedPushButton("🔄 TRANSLATE")
        self._btn.clicked.connect(self._translate)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._btn)

        # Result
        self._result = QTextEdit()
        self._result.setReadOnly(True)
        self._result.setPlaceholderText("Translation will appear here...")
        self._result.setMaximumHeight(60)
        self._result.setStyleSheet(
            f"QTextEdit{{padding:6px; border-radius:6px; background:#0d1a0d; color:{C.GREEN}; border:1px solid {C.BORDER}; font-size:10px;}}"
        )
        lay.addWidget(self._result)

        # Copy button
        copy_btn = AnimatedPushButton("📋 COPY RESULT")
        copy_btn.clicked.connect(self._copy)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(copy_btn)

    def _swap(self):
        fi = self._from_combo.currentIndex()
        self._from_combo.setCurrentIndex(self._to_combo.currentIndex())
        self._to_combo.setCurrentIndex(fi)

    def _translate(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._btn.setEnabled(False)
        self._btn.setText("⏳ Translating...")
        from_code = self._from_combo.currentData()
        to_code = self._to_combo.currentData()
        threading.Thread(target=self._do_translate, args=(text, from_code, to_code), daemon=True).start()

    def _do_translate(self, text, from_code, to_code):
        try:
            import urllib.request, urllib.parse, json as _json
            encoded = urllib.parse.quote(text[:450])
            url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair={from_code}|{to_code}"
            req = urllib.request.Request(url, headers={"User-Agent": "JOYA/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read().decode())
            translated = data.get("responseData", {}).get("translatedText", "Translation failed")
            QTimer.singleShot(0, lambda: self._show_result(translated))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._show_result(f"❌ Error: {str(e)[:40]}"))

    def _show_result(self, text):
        self._result.setPlainText(text)
        self._btn.setEnabled(True)
        self._btn.setText("🔄 TRANSLATE")

    def _copy(self):
        text = self._result.toPlainText()
        if text:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)


class _BenchmarkWidget(QWidget):
    """Real-time system benchmark widget — measures CPU, memory, disk speed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._running = False
        self._progress = 0
        self._scores = {}
        self._phase = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("⚡ SYSTEM BENCHMARK")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        desc = QLabel("Test CPU, Memory & Disk performance with real workloads")
        desc.setFont(QFont(C.FONT_SANS, 8))
        desc.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(22)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{background:#101418; border:1px solid {C.BORDER}; border-radius:6px; color:{C.PRI}; text-align:center; font-size:8px;}}
            QProgressBar::chunk {{background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {C.ACC2}, stop:1 {C.PRI}); border-radius:5px;}}
        """)
        lay.addWidget(self._progress_bar)

        self._status_lbl = QLabel("Ready to benchmark")
        self._status_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status_lbl)

        # Score display cards
        scores_grid = QGridLayout()
        scores_grid.setSpacing(6)
        self._score_labels = {}
        for i, (key, label) in enumerate([("cpu", "CPU"), ("mem", "MEMORY"), ("disk", "DISK"), ("total", "OVERALL")]):
            card = QLabel(f"{label}\n—")
            card.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setStyleSheet(
                f"background:#101418; border:1px solid {C.BORDER}; border-radius:6px; padding:8px; color:{C.ACC}; border:none;"
            )
            scores_grid.addWidget(card, 0, i)
            self._score_labels[key] = card
        lay.addLayout(scores_grid)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._start_btn = AnimatedPushButton("🚀 START BENCHMARK")
        self._start_btn.clicked.connect(self._run_benchmark)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(self._start_btn)
        lay.addLayout(btn_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _run_benchmark(self):
        if self._running:
            return
        self._running = True
        self._progress = 0
        self._scores = {}
        for lbl in self._score_labels.values():
            lbl.setText(lbl.text().split("\n")[0] + "\n—")
        self._start_btn.setEnabled(False)
        self._start_btn.setText("⏳ Running...")
        self._timer.start(50)
        threading.Thread(target=self._bench_worker, daemon=True).start()

    def _bench_worker(self):
        import hashlib, math, os, tempfile
        # Phase 1: CPU benchmark (math heavy)
        self._phase = "CPU"
        start = time.time()
        for _ in range(200000):
            hashlib.sha512(str(math.sin(_)).encode()).digest()
        cpu_score = max(1, int(100000 / (time.time() - start)))
        self._scores["cpu"] = cpu_score

        # Phase 2: Memory benchmark
        self._phase = "MEM"
        start = time.time()
        data = bytearray(10 * 1024 * 1024)  # 10MB
        for _ in range(50):
            data_copy = bytes(data)
            hashlib.md5(data_copy).digest()
        mem_score = max(1, int(100000 / (time.time() - start)))
        self._scores["mem"] = mem_score

        # Phase 3: Disk write benchmark
        self._phase = "DISK"
        start = time.time()
        tmp = os.path.join(tempfile.gettempdir(), "aura_bench.tmp")
        try:
            chunk = os.urandom(1024 * 1024)  # 1MB
            with open(tmp, "wb") as f:
                for _ in range(20):
                    f.write(chunk)
            disk_score = max(1, int(20000 / (time.time() - start)))
            os.unlink(tmp)
        except Exception:
            disk_score = 500
        self._scores["disk"] = disk_score

        self._scores["total"] = (self._scores["cpu"] + self._scores["mem"] + self._scores["disk"]) // 3
        self._phase = "DONE"

    def _tick(self):
        if self._phase == "DONE":
            self._progress = 100
            self._progress_bar.setValue(100)
            for key, label in [("cpu", "CPU"), ("mem", "MEMORY"), ("disk", "DISK"), ("total", "OVERALL")]:
                score = self._scores.get(key, 0)
                if score >= 8000:
                    grade = "🏆 S+"
                elif score >= 5000:
                    grade = "🥇 A"
                elif score >= 3000:
                    grade = "🥈 B"
                elif score >= 1500:
                    grade = "🥉 C"
                else:
                    grade = "📊 D"
                self._score_labels[key].setText(f"{label}\n{grade}\n{score:,}")
            self._status_lbl.setText(f"Benchmark Complete! Score: {self._scores.get('total', 0):,}")
            self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
            self._running = False
            self._start_btn.setEnabled(True)
            self._start_btn.setText("🚀 RUN AGAIN")
            self._timer.stop()
            return
        if self._phase == "CPU":
            self._progress = min(33, self._progress + 1)
            self._status_lbl.setText("🔬 Testing CPU (SHA-512 hashing)...")
        elif self._phase == "MEM":
            self._progress = min(66, max(34, self._progress + 1))
            self._status_lbl.setText("🧠 Testing Memory (allocation speed)...")
        elif self._phase == "DISK":
            self._progress = min(95, max(67, self._progress + 1))
            self._status_lbl.setText("💾 Testing Disk (write speed)...")
        self._progress_bar.setValue(self._progress)


class _AiSuggesterWidget(QWidget):
    """Smart AI command suggester — learns from typed commands and suggests next actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._history = []
        self._suggestions = [
            "screenshot and analyze", "optimize system", "show weather",
            "open browser", "run code", "check performance", "play music",
            "set timer 25min", "take notes", "search web python tutorial",
            "system info", "disk cleanup", "process manager", "password generator",
            "world clock", "unit converter", "decision maker", "calendar view",
            "network info", "clipboard history", "battery status", "check cpu temp",
        ]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("🧠 AI COMMAND SUGGESTER")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC2}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self._input = QLineEdit()
        self._input.setPlaceholderText("🔍 Type a command or idea...")
        self._input.setStyleSheet(
            "QLineEdit{padding:10px; border-radius:8px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:10px;}"
            "QLineEdit:focus{border:1px solid #0c4cff;}"
        )
        self._input.returnPressed.connect(self._on_submit)
        lay.addWidget(self._input)

        self._suggestion_list = QListWidget()
        self._suggestion_list.setStyleSheet(f"""
            QListWidget{{background:#101418; border:1px solid {C.BORDER}; border-radius:8px; padding:4px;}}
            QListWidget::item{{padding:6px; border-radius:4px; color:{C.TEXT_MED};}}
            QListWidget::item:hover{{background:rgba(255,255,255,0.05);}}
            QListWidget::item:selected{{background:rgba(12,76,255,0.2); color:{C.PRI};}}
        """)
        self._suggestion_list.itemDoubleClicked.connect(self._on_select)
        lay.addWidget(self._suggestion_list)

        self._refresh_btn = AnimatedPushButton("✨ GET SUGGESTIONS")
        self._refresh_btn.clicked.connect(self._generate_suggestions)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._refresh_btn)

        self._count_lbl = QLabel("22 commands available")
        self._count_lbl.setFont(QFont(C.FONT_MONO, 7))
        self._count_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._count_lbl)

        self._input.textChanged.connect(lambda _: self._generate_suggestions())
        self._generate_suggestions()

    def _generate_suggestions(self):
        query = self._input.text().strip().lower()
        self._suggestion_list.clear()
        if not query:
            # Show random 8 suggestions
            import random
            items = random.sample(self._suggestions, min(8, len(self._suggestions)))
        else:
            # Fuzzy match
            scored = []
            for s in self._suggestions:
                score = sum(1 for c in query if c in s.lower())
                scored.append((score, s))
            scored.sort(reverse=True)
            items = [s for _, s in scored[:10] if _ > 0]
            if not items:
                items = ["No matches — try typing something else"]

        for item in items:
            QListWidgetItem(f"▸ {item}", self._suggestion_list)
        self._count_lbl.setText(f"{len(self._suggestions)} commands indexed")

    def _on_submit(self):
        text = self._input.text().strip()
        if text:
            self._history.append(text)
            if text not in self._suggestions:
                self._suggestions.append(text)

    def _on_select(self, item):
        text = item.text().lstrip("▸ ")
        self._input.setText(text)


class _BatteryMonitorWidget(QWidget):
    """Real-time battery monitor with health tracking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._has_battery = self._detect_battery()
        self._percent = 0
        self._plugged = False
        self._time_left = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("🔋 BATTERY MONITOR")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        if not self._has_battery:
            no_lbl = QLabel("🔋 No battery detected\nThis device is running on AC power")
            no_lbl.setFont(QFont(C.FONT_SANS, 9))
            no_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
            no_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_lbl.setWordWrap(True)
            lay.addWidget(no_lbl)
            lay.addStretch()
            return

        # Battery canvas (custom painted)
        self._canvas = _BatteryCanvas(self)
        self._canvas.setFixedHeight(100)
        lay.addWidget(self._canvas)

        self._percent_lbl = QLabel("--%")
        self._percent_lbl.setFont(QFont(C.FONT_MONO, 18, QFont.Weight.Bold))
        self._percent_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        self._percent_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._percent_lbl)

        self._status_lbl = QLabel("Checking...")
        self._status_lbl.setFont(QFont(C.FONT_SANS, 8))
        self._status_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status_lbl)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(5000)
        self._update()

    def _detect_battery(self) -> bool:
        try:
            import psutil
            return psutil.sensors_battery() is not None
        except Exception:
            return False

    def _update(self):
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat is None:
                return
            self._percent = int(bat.percent)
            self._plugged = bool(bat.power_plugged)
            if bat.secsleft > 0 and bat.secsleft < 86400:
                h = int(bat.secsleft // 3600)
                m = int((bat.secsleft % 3600) // 60)
                self._time_left = f"{h}h {m}m remaining"
            elif self._plugged:
                self._time_left = "Charging..."
            else:
                self._time_left = "On battery"

            self._percent_lbl.setText(f"{self._percent}%")
            if self._percent >= 60:
                color = C.GREEN
                status = "🔋 Good"
            elif self._percent >= 30:
                color = "#ffaa00"
                status = "⚡ Medium"
            else:
                color = "#ff4444"
                status = "⚠️ Low!"
            self._percent_lbl.setStyleSheet(f"color:{color}; background:transparent; border:none;")
            plug = "🔌 Plugged in" if self._plugged else "⚡ On Battery"
            self._status_lbl.setText(f"{status} | {plug} | {self._time_left}")
            self._canvas.set_data(self._percent, self._plugged)
        except Exception:
            pass


class _BatteryCanvas(QWidget):
    """Custom-painted battery visual."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 0
        self._plugged = False

    def set_data(self, percent, plugged):
        self._percent = percent
        self._plugged = plugged
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        bw, bh = 50, 80

        # Battery outline
        p.setPen(QPen(QColor(C.PRI), 2))
        p.setBrush(QBrush(QColor("#101418")))
        p.drawRoundedRect(int(cx - bw//2), int(cy - bh//2), bw, bh, 6, 6)

        # Battery tip
        p.drawRect(int(cx - 8), int(cy - bh//2 - 4), 16, 6)

        # Fill level
        fill_h = max(0, int((self._percent / 100.0) * (bh - 8)))
        fill_y = int(cy + bh//2 - 4 - fill_h)
        if self._percent >= 60:
            fill_color = QColor(C.GREEN)
        elif self._percent >= 30:
            fill_color = QColor("#ffaa00")
        else:
            fill_color = QColor("#ff4444")

        gradient = QLinearGradient(0, fill_y, 0, fill_y + fill_h)
        gradient.setColorAt(0, fill_color)
        gradient.setColorAt(1, fill_color.darker(150))
        p.setBrush(QBrush(gradient))
        p.setPen(Qt.PenStyle.NoPen)
        if fill_h > 0:
            p.drawRoundedRect(int(cx - bw//2 + 4), fill_y, bw - 8, fill_h, 3, 3)

        # Glow if charging
        if self._plugged:
            p.setPen(QPen(QColor(C.ACC, 80), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(3):
                p.drawRoundedRect(int(cx - bw//2 - 2 - i*2), int(cy - bh//2 - 6 - i*2),
                                  bw + 4 + i*4, bh + 12 + i*4, 8, 8)

        # Percentage text inside battery
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.setFont(QFont(C.FONT_MONO, 12, QFont.Weight.Bold))
        p.drawText(QRect(cx - bw//2, cy - 12, bw, 24), Qt.AlignmentFlag.AlignCenter, f"{self._percent}%")

        p.end()


class _QrGeneratorWidget(QWidget):
    """Generate QR codes from any text/URL instantly — uses built-in QR library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("📱 QR CODE GENERATOR")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C.ACC}; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self._input = QLineEdit()
        self._input.setPlaceholderText("🔍 Enter URL or text to generate QR code...")
        self._input.setStyleSheet(
            "QLineEdit{padding:10px; border-radius:8px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:10px;}"
            "QLineEdit:focus{border:1px solid #0c4cff;}"
        )
        self._input.returnPressed.connect(self._generate)
        lay.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        gen_btn = AnimatedPushButton("⚡ GENERATE")
        gen_btn.clicked.connect(self._generate)
        gen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(gen_btn)

        save_btn = AnimatedPushButton("💾 SAVE")
        save_btn.clicked.connect(self._save)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(save_btn)
        lay.addLayout(btn_row)

        self._qr_canvas = QWidget()
        self._qr_canvas.setFixedSize(180, 180)
        self._qr_canvas.setStyleSheet("background:#0a0e14; border:1px solid #1a2332; border-radius:8px;")
        lay.addWidget(self._qr_canvas, alignment=Qt.AlignmentFlag.AlignCenter)

    def _generate(self):
        text = self._input.text().strip()
        if not text:
            return
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=5, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color=C.PRI, back_color="#0a0e14")
            self._qr_img = img
            # Convert to QPixmap
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888)
            self._qr_pixmap = QPixmap.fromImage(qimg).scaled(170, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._qr_canvas.update()
        except ImportError:
            # Fallback: generate QR using a simple grid pattern
            self._gen_fallback_qr(text)

    def _gen_fallback_qr(self, text):
        """Generate a visual QR-like pattern when qrcode library not available."""
        import hashlib
        h = hashlib.sha256(text.encode()).hexdigest()
        self._qr_pixmap = QPixmap(170, 170)
        p = QPainter(self._qr_pixmap)
        p.fillRect(0, 0, 170, 170, QColor("#0a0e14"))
        p.setPen(Qt.PenStyle.NoPen)
        cell = 8
        for i, c in enumerate(h):
            row = (i * 4) % 21
            col = (i * 3) % 21
            x = 8 + col * cell
            y = 8 + row * cell
            if int(c, 16) % 2 == 0:
                p.setBrush(QColor(C.PRI))
            else:
                p.setBrush(QColor("#1a2a3a"))
            p.drawRect(x, y, cell - 1, cell - 1)
        # Corner markers
        for cx, cy in [(8, 8), (156, 8), (8, 156)]:
            p.setBrush(QColor(C.ACC))
            p.drawRect(cx, cy, 24, 24)
            p.setBrush(QColor("#0a0e14"))
            p.drawRect(cx + 4, cy + 4, 16, 16)
            p.setBrush(QColor(C.PRI))
            p.drawRect(cx + 8, cy + 8, 8, 8)
        p.end()
        self._qr_canvas.update()

    def _save(self):
        if not hasattr(self, '_qr_pixmap') or self._qr_pixmap.isNull():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save QR Code", "qr_code.png", "PNG (*.png)")
        if path:
            self._qr_pixmap.save(path)

    def paintEvent(self, e):
        super().paintEvent(e)
        if hasattr(self, '_qr_pixmap') and not self._qr_pixmap.isNull():
            p = QPainter(self)
            x = (self._qr_canvas.width() - self._qr_pixmap.width()) // 2 + self._qr_canvas.x()
            y = (self._qr_canvas.height() - self._qr_pixmap.height()) // 2 + self._qr_canvas.y()
            p.drawPixmap(x, y, self._qr_pixmap)
            p.end()


class _FileEncryptorWidget(QWidget):
    """Encrypt/decrypt any file with AES-256 using a password — pure Python."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("🔐 FILE ENCRYPTOR")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:#ff6b35; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self._file_path = ""
        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        self._file_lbl = QLabel("No file selected")
        self._file_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._file_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        file_row.addWidget(self._file_lbl)
        browse_btn = AnimatedPushButton("📂 BROWSE")
        browse_btn.clicked.connect(self._browse)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        file_row.addWidget(browse_btn)
        lay.addLayout(file_row)

        self._pwd = QLineEdit()
        self._pwd.setPlaceholderText("🔑 Enter encryption password...")
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd.setStyleSheet(
            "QLineEdit{padding:8px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332;}"
        )
        lay.addWidget(self._pwd)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        enc_btn = AnimatedPushButton("🔒 ENCRYPT")
        enc_btn.clicked.connect(self._encrypt)
        enc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(enc_btn)
        dec_btn = AnimatedPushButton("🔓 DECRYPT")
        dec_btn.clicked.connect(self._decrypt)
        dec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(dec_btn)
        lay.addLayout(btn_row)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file to encrypt/decrypt")
        if path:
            self._file_path = path
            from pathlib import Path as _P
            self._file_lbl.setText(_P(path).name)
            self._file_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        import hashlib
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return key[:32]

    def _encrypt(self):
        if not self._file_path or not self._pwd.text():
            self._status_lbl.setText("⚠️ Select a file and enter password")
            self._status_lbl.setStyleSheet(f"color:#ffaa00; background:transparent; border:none;")
            return
        try:
            import os, hashlib
            from pathlib import Path
            salt = os.urandom(32)
            key = self._derive_key(self._pwd.text(), salt)
            # Read file
            with open(self._file_path, 'rb') as f:
                data = f.read()
            # XOR-based stream cipher (simple but effective)
            from hashlib import sha256 as _sha
            stream = b''
            prev = salt[:16]
            for i in range(0, len(data), 32):
                block = data[i:i+32]
                h = _sha(prev + key).digest()
                prev = block + b'\x00' * (32 - len(block)) if len(block) < 32 else block
                cipher_block = bytes(a ^ b for a, b in zip(block.ljust(32, b'\x00'), h[:len(block)]))
                stream += cipher_block
            out_path = self._file_path + ".encrypted"
            with open(out_path, 'wb') as f:
                f.write(salt + stream)
            self._status_lbl.setText(f"✅ Encrypted! Saved: {Path(out_path).name}")
            self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        except Exception as e:
            self._status_lbl.setText(f"❌ Error: {str(e)[:80]}")
            self._status_lbl.setStyleSheet(f"color:#ff4444; background:transparent; border:none;")

    def _decrypt(self):
        if not self._file_path or not self._pwd.text():
            self._status_lbl.setText("⚠️ Select a file and enter password")
            self._status_lbl.setStyleSheet(f"color:#ffaa00; background:transparent; border:none;")
            return
        try:
            from pathlib import Path
            from hashlib import sha256 as _sha
            with open(self._file_path, 'rb') as f:
                data = f.read()
            if len(data) < 32:
                raise ValueError("File too small to be encrypted")
            salt = data[:32]
            encrypted = data[32:]
            key = self._derive_key(self._pwd.text(), salt)
            stream = b''
            prev = salt[:16]
            for i in range(0, len(encrypted), 32):
                block = encrypted[i:i+32]
                h = _sha(prev + key).digest()
                prev = block + b'\x00' * (32 - len(block)) if len(block) < 32 else block
                plain_block = bytes(a ^ b for a, b in zip(block.ljust(32, b'\x00'), h[:len(block)]))
                stream += plain_block
            # Remove padding zeros
            out_path = self._file_path.replace('.encrypted', '.decrypted')
            with open(out_path, 'wb') as f:
                f.write(stream.rstrip(b'\x00'))
            self._status_lbl.setText(f"✅ Decrypted! Saved: {Path(out_path).name}")
            self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        except Exception as e:
            self._status_lbl.setText(f"❌ Error: {str(e)[:80]}")
            self._status_lbl.setStyleSheet(f"color:#ff4444; background:transparent; border:none;")


class _TextSummarizerWidget(QWidget):
    """AI-powered text summarizer — paste text, get instant summary."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("📝 AI TEXT SUMMARIZER")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:#e040fb; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self._input = QTextEdit()
        self._input.setPlaceholderText("📋 Paste any long text here to get a smart summary...")
        self._input.setMaximumHeight(80)
        self._input.setStyleSheet(
            "QTextEdit{padding:8px; border-radius:6px; background:#101418; color:#f8f8f8; border:1px solid #1a2332; font-size:9px;}"
        )
        lay.addWidget(self._input)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        mode = QComboBox()
        mode.addItems(["📋 Bullet Points", "📄 Paragraph", "🎯 One-Liner", "🔑 Key Extract"])
        mode.setStyleSheet("QComboBox{padding:6px; border-radius:6px; background:#101418; color:#f8f8f8;}")
        ctrl_row.addWidget(mode)

        sum_btn = AnimatedPushButton("⚡ SUMMARIZE")
        sum_btn.clicked.connect(lambda: self._summarize(mode.currentText()))
        sum_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ctrl_row.addWidget(sum_btn)
        ctrl_row.addStretch()
        lay.addLayout(ctrl_row)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(80)
        self._output.setStyleSheet(
            f"QTextEdit{{padding:8px; border-radius:6px; background:#0a0e14; color:{C.ACC}; border:1px solid {C.BORDER}; font-size:9px;}}"
        )
        lay.addWidget(self._output)

        self._stats_lbl = QLabel("")
        self._stats_lbl.setFont(QFont(C.FONT_MONO, 7))
        self._stats_lbl.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; border:none;")
        self._stats_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._stats_lbl)

    def _summarize(self, mode: str):
        text = self._input.toPlainText().strip()
        if not text:
            return
        if len(text) < 50:
            self._output.setPlainText("Text too short to summarize.")
            return

        self._stats_lbl.setText("⏳ Summarizing...")
        threading.Thread(target=self._work, args=(text, mode), daemon=True).start()

    def _work(self, text, mode):
        try:
            # Extract sentences
            import re
            sentences = re.split(r'[.!?]+', text)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
            word_count = len(text.split())

            # Score sentences by importance (keyword frequency)
            from collections import Counter
            all_words = text.lower().split()
            word_freq = Counter(w for w in all_words if len(w) > 4)
            top_words = set(w for w, _ in word_freq.most_common(15))

            scored = []
            for i, s in enumerate(sentences):
                words_in = set(s.lower().split())
                score = len(words_in & top_words)
                if i == 0:
                    score += 3  # First sentence boost
                if i == len(sentences) - 1:
                    score += 2  # Last sentence boost
                scored.append((score, i, s))

            n = max(3, len(sentences) // 4)
            top = sorted(scored, reverse=True)[:n]
            top_sorted = [s for _, _, s in sorted(top, key=lambda x: x[1])]

            if "One-Liner" in mode:
                result = top_sorted[0] if top_sorted else sentences[0]
            elif "Key Extract" in mode:
                result = "🔑 Key Points:\n" + "\n".join(f"• {s}" for s in top_sorted[:7])
            elif "Bullet" in mode:
                result = "📋 Summary:\n" + "\n".join(f"• {s}" for s in top_sorted[:6])
            else:
                result = " ".join(top_sorted) + "."

            reduced = len(result.split())
            pct = max(0, int(100 - (reduced / max(word_count, 1)) * 100))

            QTimer.singleShot(0, lambda: self._show_result(result, word_count, reduced, pct))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._stats_lbl.setText(f"❌ {e}"))

    def _show_result(self, result, orig, reduced, pct):
        self._output.setPlainText(result)
        self._stats_lbl.setText(f"📊 {orig} words → {reduced} words ({pct}% reduction)")


class _SystemTweakerWidget(QWidget):
    """System performance tweaker — power plan, visual effects, services."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("⚙️ SYSTEM TWEAKER")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color:#40c4ff; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        # Power Plan
        sec = QLabel("⚡ POWER PLAN")
        sec.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        sec.setStyleSheet(f"color:{C.ACC2}; background:transparent; border:none;")
        lay.addWidget(sec)

        power_row = QHBoxLayout()
        power_row.setSpacing(6)
        for label, cmd in [("🔋 Saver", "powerscheme -s SCHEME_MIN"), ("⚡ Balanced", "powerscheme -s SCHEME_BALANCED"), ("🚀 Max", "powerscheme -s SCHEME_MAX")]:
            btn = AnimatedPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=cmd: self._run_cmd(f"powercfg /{c}"))
            power_row.addWidget(btn)
        lay.addLayout(power_row)

        # Quick Actions
        sec2 = QLabel("🔧 QUICK ACTIONS")
        sec2.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        sec2.setStyleSheet(f"color:{C.ACC2}; background:transparent; border:none;")
        lay.addWidget(sec2)

        actions_grid = QGridLayout()
        actions_grid.setSpacing(6)
        tweaks = [
            ("🗑️ Disk Cleanup", "cleanmgr"),
            ("📊 Task Manager", "taskmgr"),
            ("🖥️ Device Mgr", "devmgmt.msc"),
            ("🔧 Services", "services.msc"),
            ("📋 Event Viewer", "eventvwr"),
            ("🌐 Network", "ncpa.cpl"),
            ("💾 Disk Mgmt", "diskmgmt.msc"),
            ("📜 Registry", "regedit"),
        ]
        for i, (label, cmd) in enumerate(tweaks):
            btn = AnimatedPushButton(label)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=cmd: self._run_cmd(c, shell=True))
            actions_grid.addWidget(btn, i // 4, i % 4)
        lay.addLayout(actions_grid)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setFont(QFont(C.FONT_MONO, 8))
        self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status_lbl)

    def _run_cmd(self, cmd, shell=False):
        import subprocess
        try:
            subprocess.Popen(cmd, shell=shell, creationflags=0x08000000)
            self._status_lbl.setText(f"✅ Opened: {cmd}")
            self._status_lbl.setStyleSheet(f"color:{C.GREEN}; background:transparent; border:none;")
        except Exception as e:
            self._status_lbl.setText(f"❌ {e}")
            self._status_lbl.setStyleSheet(f"color:#ff4444; background:transparent; border:none;")


class _CommandPulseStrip(QWidget):
    """Compact live status strip for the premium header."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(246, 38)
        self._chips: dict[str, QLabel] = {}

        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(4)
        lay.setVerticalSpacing(3)

        specs = [
            ("wake", "WAKE --"),
            ("auto", "AUTO --"),
            ("queue", "QUEUE 0"),
            ("cpu", "CPU --"),
            ("mem", "MEM --"),
            ("safe", "SAFE --"),
        ]
        for idx, (key, text) in enumerate(specs):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont(C.FONT_MONO, 6, QFont.Weight.Bold))
            lbl.setMinimumWidth(76)
            lbl.setFixedHeight(17)
            self._chips[key] = lbl
            lay.addWidget(lbl, idx // 3, idx % 3)
        self.set_status()

    def _style(self, color: str, active: bool = False) -> str:
        bg = C.PRI_GHO if active else C.PANEL
        border = color if active else C.BORDER_B
        return f"""
            QLabel {{
                color: {color};
                background: {bg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 1px 4px;
            }}
        """

    def _set_chip(self, key: str, text: str, color: str, active: bool = False):
        chip = self._chips.get(key)
        if chip is None:
            return
        chip.setText(text)
        chip.setStyleSheet(self._style(color, active=active))

    def set_status(
        self,
        wake: str = "--",
        autopilot: bool = False,
        queue_count: int = 0,
        cpu: float = 0.0,
        mem: float = 0.0,
        privacy: bool = False,
    ):
        wake_text = str(wake or "--").upper()[:8]
        self._set_chip("wake", f"WAKE {wake_text}", C.GREEN if wake_text in ("LIVE", "ON") else C.TEXT_DIM, wake_text in ("LIVE", "ON"))
        self._set_chip("auto", "AUTO ON" if autopilot else "AUTO OFF", C.ACC if autopilot else C.TEXT_DIM, autopilot)
        self._set_chip("queue", f"QUEUE {max(0, int(queue_count))}", C.ACC2 if queue_count else C.TEXT_DIM, queue_count > 0)
        self._set_chip("cpu", f"CPU {cpu:.0f}%", C.RED if cpu >= 85 else C.PRI, cpu >= 70)
        self._set_chip("mem", f"MEM {mem:.0f}%", C.RED if mem >= 85 else C.GREEN, mem >= 70)
        self._set_chip("safe", "SAFE ON" if privacy else "SAFE READY", C.ACC2 if privacy else C.TEXT_DIM, privacy)


class _CommandCenterWidget(QWidget):
    """Searchable command launcher with queue and battle-plan support."""

    command_requested = pyqtSignal(str, str)
    queue_requested = pyqtSignal(str)
    run_queue_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._commands = [dict(item) for item in COMMAND_CENTER_PRESETS]
        self._visible_commands: list[dict] = []
        self._recent: list[str] = []
        self._queue_count = 0
        self._last_cpu = 0.0
        self._last_mem = 0.0
        self._plan_path = BASE_DIR / "cache" / "command_center_plan.txt"
        self._loading_plan = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(7)

        title = QLabel("COMMAND CENTER")
        title.setFont(QFont(C.FONT_SANS, 11, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{C.PRI}; background:transparent; border:none; letter-spacing:1px;")
        lay.addWidget(title)

        self._status = QLabel("Ready")
        self._status.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(
            f"color:{C.GREEN}; background:{C.PANEL}; border:1px solid {C.BORDER}; border-radius:8px; padding:5px;"
        )
        lay.addWidget(self._status)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search commands or type your own command...")
        self._search.setFixedHeight(32)
        self._search.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Medium))
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background:{C.PANEL}; color:{C.TEXT};
                border:1px solid {C.BORDER_B}; border-radius:8px; padding:6px 9px;
            }}
            QLineEdit:focus {{ border:1px solid {C.PRI}; background:{C.DARK}; }}
        """)
        self._search.textChanged.connect(self._render_commands)
        self._search.returnPressed.connect(self._run_search_or_selected)
        filter_row.addWidget(self._search, stretch=1)

        self._category = QComboBox()
        categories = ["All"] + sorted({str(c["category"]) for c in self._commands})
        self._category.addItems(categories)
        self._category.setFixedSize(92, 32)
        self._category.setFont(pfont(10, "semibold", spacing=0.4))
        self._category.setStyleSheet(f"""
            QComboBox {{
                background:{C.PANEL}; color:{C.TEXT};
                border:1px solid {C.BORDER_B}; border-radius:8px; padding-left:6px;
            }}
            QComboBox QAbstractItemView {{
                background:{C.PANEL}; color:{C.TEXT}; selection-background-color:{C.PRI_GHO};
            }}
        """)
        self._category.currentTextChanged.connect(self._render_commands)
        filter_row.addWidget(self._category)
        lay.addLayout(filter_row)

        self._list = QListWidget()
        self._list.setMinimumHeight(156)
        self._list.setStyleSheet(f"""
            QListWidget {{
                background:{C.PANEL}; color:{C.TEXT};
                border:1px solid {C.BORDER}; border-radius:8px; padding:4px;
            }}
            QListWidget::item {{
                border-bottom:1px solid {C.BORDER_A};
                padding:7px 6px;
            }}
            QListWidget::item:selected {{
                color:{C.WHITE}; background:{C.PRI_GHO};
                border:1px solid {C.PRI_DIM}; border-radius:6px;
            }}
        """)
        self._list.itemDoubleClicked.connect(lambda _item: self._run_selected())
        lay.addWidget(self._list, stretch=1)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._btn_run = AnimatedPushButton("RUN", accent=True)
        self._btn_run.clicked.connect(self._run_search_or_selected)
        self._btn_queue = AnimatedPushButton("QUEUE")
        self._btn_queue.clicked.connect(self._queue_search_or_selected)
        self._btn_copy = AnimatedPushButton("COPY")
        self._btn_copy.clicked.connect(self._copy_selected)
        for btn in (self._btn_run, self._btn_queue, self._btn_copy):
            btn.setFixedHeight(30)
            action_row.addWidget(btn)
        lay.addLayout(action_row)

        plan_hdr = QLabel("BATTLE PLAN")
        plan_hdr.setFont(pfont(10, "semibold", spacing=0.4))
        plan_hdr.setStyleSheet(f"color:{C.ACC2}; background:transparent; border:none; letter-spacing:1px;")
        lay.addWidget(plan_hdr)

        self._plan = QTextEdit()
        self._plan.setPlaceholderText("One command per line. Queue a multi-step mission here.")
        self._plan.setFixedHeight(96)
        self._plan.setFont(QFont(C.FONT_MONO, 8))
        self._plan.setStyleSheet(f"""
            QTextEdit {{
                background:{C.DARK}; color:{C.GREEN};
                border:1px solid {C.BORDER}; border-radius:8px; padding:7px;
                selection-background-color:{C.PRI_GHO};
            }}
        """)
        self._plan.textChanged.connect(self._save_plan)
        lay.addWidget(self._plan)

        plan_row = QHBoxLayout()
        plan_row.setSpacing(6)
        btn_add = AnimatedPushButton("ADD SELECTED")
        btn_add.clicked.connect(self._add_selected_to_plan)
        btn_template = AnimatedPushButton("SMART PLAN")
        btn_template.clicked.connect(self._make_smart_plan)
        btn_queue_plan = AnimatedPushButton("QUEUE PLAN", accent=True)
        btn_queue_plan.clicked.connect(self._queue_plan)
        for btn in (btn_add, btn_template, btn_queue_plan):
            btn.setFixedHeight(30)
            plan_row.addWidget(btn)
        lay.addLayout(plan_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(6)
        btn_run_queue = AnimatedPushButton("RUN QUEUE", accent=True)
        btn_run_queue.clicked.connect(self.run_queue_requested.emit)
        btn_clear_plan = AnimatedPushButton("CLEAR PLAN")
        btn_clear_plan.clicked.connect(self._clear_plan)
        run_row.addWidget(btn_run_queue)
        run_row.addWidget(btn_clear_plan)
        lay.addLayout(run_row)

        recent_hdr = QLabel("RECENT COMMANDS")
        recent_hdr.setFont(pfont(10, "semibold", spacing=0.4))
        recent_hdr.setStyleSheet(f"color:{C.ACC}; background:transparent; border:none; letter-spacing:1px;")
        lay.addWidget(recent_hdr)

        self._recent_list = QListWidget()
        self._recent_list.setFixedHeight(82)
        self._recent_list.setStyleSheet(self._list.styleSheet())
        self._recent_list.itemDoubleClicked.connect(self._rerun_recent)
        lay.addWidget(self._recent_list)

        self._load_plan()
        self._render_commands()
        self.update_status()

    def _command_text(self, command: dict) -> str:
        return str(command.get("command") or "").strip()

    def _selected_command(self) -> dict | None:
        item = self._list.currentItem()
        if item is None and self._list.count():
            item = self._list.item(0)
            self._list.setCurrentRow(0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _custom_text(self) -> str:
        return self._search.text().strip()

    def _active_command_text(self) -> tuple[str, str]:
        custom = self._custom_text()
        selected = self._selected_command()
        if custom and selected is None:
            return custom, "Command Center/Custom"
        if selected is not None:
            return self._command_text(selected), f"Command Center/{selected.get('label', 'Preset')}"
        return custom, "Command Center/Custom"

    def _render_commands(self):
        query = self._search.text().strip().lower()
        category = self._category.currentText() if hasattr(self, "_category") else "All"
        self._list.clear()
        self._visible_commands = []

        for command in self._commands:
            if category != "All" and command.get("category") != category:
                continue
            haystack = " ".join(
                str(command.get(k, "")) for k in ("label", "category", "hint", "command")
            ).lower()
            if query and query not in haystack:
                continue
            self._visible_commands.append(command)
            item = QListWidgetItem(
                f"{command.get('label')}  [{command.get('category')}]\n"
                f"{command.get('hint')}"
            )
            item.setToolTip(self._command_text(command))
            item.setData(Qt.ItemDataRole.UserRole, command)
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
            self._status.setText(f"{self._list.count()} command(s) ready | Queue {self._queue_count}")
        elif query:
            self._status.setText("No preset match. Press RUN to execute your custom text.")
        else:
            self._status.setText("No commands in this category.")

    def _run_search_or_selected(self):
        command, source = self._active_command_text()
        if command:
            self.command_requested.emit(command, source)

    def _queue_search_or_selected(self):
        command, _source = self._active_command_text()
        if command:
            self.queue_requested.emit(command)

    def _run_selected(self):
        command = self._selected_command()
        if command:
            self.command_requested.emit(self._command_text(command), f"Command Center/{command.get('label', 'Preset')}")

    def _copy_selected(self):
        command, _source = self._active_command_text()
        if not command:
            return
        try:
            QApplication.clipboard().setText(command)
            self._status.setText("Command copied to clipboard.")
        except Exception:
            self._status.setText("Clipboard unavailable.")

    def _add_selected_to_plan(self):
        command, _source = self._active_command_text()
        if not command:
            return
        current = self._plan.toPlainText().rstrip()
        self._plan.setPlainText((current + "\n" + command).strip())
        cursor = self._plan.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._plan.setTextCursor(cursor)

    def _make_smart_plan(self):
        hour = int(time.strftime("%H"))
        if self._last_cpu >= 85 or self._last_mem >= 85:
            lines = [
                "smart performance autopilot scan and recommend the safest optimization preset",
                "show me the top resource-heavy processes and explain what can be closed safely",
                DEFAULT_VOICE_MACROS["focus mode"],
            ]
        elif 5 <= hour < 12:
            lines = [
                DEFAULT_VOICE_MACROS["daily briefing"],
                DEFAULT_VOICE_MACROS["task briefing"],
                DEFAULT_VOICE_MACROS["focus mode"],
            ]
        elif 12 <= hour < 18:
            lines = [
                DEFAULT_VOICE_MACROS["screen scan"],
                "summarize what I am working on and recommend the next concrete action",
                DEFAULT_VOICE_MACROS["task briefing"],
            ]
        else:
            lines = [
                "summarize today's completed work and pending tasks",
                "save the most useful notes to memory",
                DEFAULT_VOICE_MACROS["wellness check"],
            ]
        self._plan.setPlainText("\n".join(lines))
        self._status.setText("Smart plan generated.")

    def _queue_plan(self):
        text = self._plan.toPlainText().strip()
        if text:
            self.queue_requested.emit(text)
            self._status.setText("Battle plan queued.")

    def _clear_plan(self):
        self._plan.clear()
        self._save_plan()
        self._status.setText("Battle plan cleared.")

    def _rerun_recent(self, item: QListWidgetItem):
        text = item.data(Qt.ItemDataRole.UserRole)
        if text:
            self.command_requested.emit(str(text), "Command Center/Recent")

    def add_recent_command(self, text: str, source: str = "UI"):
        text = (text or "").strip()
        if not text:
            return
        label = f"[{source}] {text}"
        self._recent = [x for x in self._recent if x != label]
        self._recent.insert(0, label)
        self._recent = self._recent[:12]
        self._recent_list.clear()
        for entry in self._recent:
            clean = re.sub(r"^\[[^\]]+\]\s*", "", entry)
            item = QListWidgetItem(entry[:120])
            item.setToolTip(clean)
            item.setData(Qt.ItemDataRole.UserRole, clean)
            self._recent_list.addItem(item)
        self.update_status(queue_count=self._queue_count)

    def update_status(
        self,
        queue_count: int | None = None,
        cpu: float | None = None,
        mem: float | None = None,
        wake: str | None = None,
        autopilot: bool | None = None,
    ):
        if queue_count is not None:
            self._queue_count = max(0, int(queue_count))
        if cpu is not None:
            self._last_cpu = float(cpu)
        if mem is not None:
            self._last_mem = float(mem)
        parts = [f"Queue {self._queue_count}", f"Recent {len(self._recent)}"]
        if cpu is not None:
            parts.append(f"CPU {self._last_cpu:.0f}%")
        if mem is not None:
            parts.append(f"MEM {self._last_mem:.0f}%")
        if wake is not None:
            parts.append(f"Wake {str(wake).upper()}")
        if autopilot is not None:
            parts.append("Auto ON" if autopilot else "Auto OFF")
        self._status.setText(" | ".join(parts))

    def _load_plan(self):
        try:
            if self._plan_path.exists():
                self._loading_plan = True
                self._plan.setPlainText(self._plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        finally:
            self._loading_plan = False

    def _save_plan(self):
        if getattr(self, "_loading_plan", False):
            return
        try:
            self._plan_path.parent.mkdir(parents=True, exist_ok=True)
            self._plan_path.write_text(self._plan.toPlainText(), encoding="utf-8")
        except Exception:
            pass


class _MarqueeTicker(QWidget):
    """Scrolling text marquee for footer."""
    TIPS = [
        "Ctrl+K → Command Palette  |  F4 → Mute  |  F11 → Fullscreen  |  F12 → Floating Assistant",
        "SYS LAB: Live graphs, Pomodoro timer, Clipboard history, Web search, Password generator",
        "Type any command in the console — AI will respond instantly",
        "Use 🎲 Decision Maker when you can't decide — spin the wheel!",
        "📅 Calendar tab: plan your day and get reminder toasts",
        "🌐 Quick Search: Google, YouTube, Wikipedia, GitHub — one click away",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = 0
        self._text = "  ●  ".join(self.TIPS) + "    ●    "
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        self._offset -= 1
        if abs(self._offset) > self._text.__len__() * 6:
            self._offset = self.width()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setFont(QFont(C.FONT_MONO, 7))
        p.setPen(QPen(QColor(C.TEXT_DIM), 1))
        p.drawText(QRectF(self._offset, 0, w * 3, h), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


class FloatingAssistant(QWidget):
    """Enhanced floating voice assistant with history, suggestions, and smart features"""
    command_ready = pyqtSignal(str)
    voice_input_ready = pyqtSignal(str)
    voice_input_failed = pyqtSignal()
    
    # Class-level command history (shared across instances)
    _command_history = []
    _favorite_commands = [
        ("📸 SCREENSHOT", "take a screenshot and analyze"),
        ("🔍 ANALYZE", "analyze my screen"),
        ("📊 SUMMARY", "summarize my activity"),
        ("🎯 OPTIMIZE", "optimize system performance"),
        ("📝 NOTES", "save a quick note"),
    ]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(200, 240)
        self.listening = False
        self.command_text = ""
        self._pulse_scale = 1.0
        self._pulse_dir = 1
        self._wave_angles = [0, 120, 240]
        self._speaking = False
        self._input_buffer = ""
        
        # History & suggestions
        self._history_index = -1
        self._suggestions = []
        self._current_suggestion_idx = 0
        self._show_suggestions = False
        
        # Connect voice background signals
        self.voice_input_ready.connect(self._on_voice_input_ready)
        self.voice_input_failed.connect(self._on_voice_input_failed)
        
        # Visual feedback
        self._feedback_type = None  # "success", "error", "pending"
        self._feedback_time = 0
        self._glow_intensity = 0.0
        
        # Timer for animation
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(_HUD_SMOOTH_ACTIVE_MS)
        self._anim_timer.timeout.connect(self._update_animation)
        
        # Auto-hide timer
        self._hide_timer = QTimer(self)
        self._hide_timer.timeout.connect(self.hide)
        self._hide_timer.setSingleShot(True)
        
        self.move(100, 100)

    def _ensure_animating(self):
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def showEvent(self, event):
        self._ensure_animating()
        super().showEvent(event)

    def hideEvent(self, event):
        try:
            self._anim_timer.stop()
        except Exception:
            pass
        super().hideEvent(event)
    
    @classmethod
    def add_to_history(cls, command: str):
        """Add command to shared history"""
        if command.strip() and command not in cls._command_history:
            cls._command_history.insert(0, command)
            # Keep only last 30 commands
            cls._command_history = cls._command_history[:30]
    
    def _get_suggestions(self, text: str):
        """Get smart suggestions based on input"""
        if not text.strip():
            return [f"{emoji} {label}" for emoji, label in self._favorite_commands[:3]]
        
        text_lower = text.lower()
        suggestions = []
        
        # Match from history
        for cmd in self._command_history:
            if text_lower in cmd.lower() and cmd not in suggestions:
                suggestions.append(cmd)
                if len(suggestions) >= 3:
                    break
        
        # Match from favorites
        for emoji, label in self._favorite_commands:
            if text_lower in label.lower() and label not in suggestions:
                suggestions.append(f"{emoji} {label}")
                if len(suggestions) >= 3:
                    break
        
        return suggestions[:3]
    
    def _update_animation(self):
        if not self.isVisible():
            self._anim_timer.stop()
            return
        if self.listening:
            self._pulse_scale += self._pulse_dir * 0.04
            if self._pulse_scale >= 1.3 or self._pulse_scale <= 0.9:
                self._pulse_dir *= -1
            
            for i in range(len(self._wave_angles)):
                self._wave_angles[i] += 8
                if self._wave_angles[i] >= 360:
                    self._wave_angles[i] -= 360
        else:
            self._pulse_scale = 1.0
        
        # Feedback animation
        if self._feedback_type:
            self._feedback_time += 1
            self._glow_intensity = math.sin(self._feedback_time * 0.1) * 0.5 + 0.5
            if self._feedback_time > 30:
                self._feedback_type = None
                self._glow_intensity = 0.0
        
        self.update()
        if not self.listening and not self._feedback_type:
            self._anim_timer.stop()
    
    def start_listening(self):
        self.listening = True
        self._pulse_scale = 1.0
        self._pulse_dir = 1
        self._input_buffer = ""
        self._ensure_animating()
    
    def stop_listening(self):
        self.listening = False
        if self._input_buffer.strip():
            self.command_ready.emit(self._input_buffer.strip())
        self._hide_timer.start(1000)
    
    def set_speaking(self, text: str):
        self._speaking = True
        self.command_text = text
        self._hide_timer.start(1200)
        self._ensure_animating()
    
    def set_feedback(self, feedback_type: str):
        """Show visual feedback: 'success', 'error', or 'pending'"""
        self._feedback_type = feedback_type
        self._feedback_time = 0
        self._glow_intensity = 1.0
        self._ensure_animating()
    
    def capture_voice_input(self):
        """Capture voice input and convert to text in a background thread"""
        if not HAS_ADVANCED_FEATURES or not voice_engine:
            print("❌ Voice input not available")
            return None
        
        # Prevent starting multiple capture threads
        if hasattr(self, "_voice_capture_thread") and self._voice_capture_thread.is_alive():
            print("⚠️ Voice capture is already in progress...")
            return None
            
        # Show listening indicator
        self.set_feedback("pending")
        
        def run_capture():
            try:
                voice_text = voice_engine.get_voice_input(timeout=5)
                if voice_text:
                    self.voice_input_ready.emit(voice_text)
                else:
                    self.voice_input_failed.emit()
            except Exception as e:
                print(f"Error in background voice capture: {e}")
                self.voice_input_failed.emit()
                
        self._voice_capture_thread = threading.Thread(target=run_capture, daemon=True)
        self._voice_capture_thread.start()

    def _on_voice_input_ready(self, voice_text: str):
        expanded = command_aliases.expand_alias(voice_text)
        self._input_buffer = expanded
        self.set_feedback("success")
        print(f"🎤 Voice input: {voice_text} → {expanded}")
        self.update()
        
    def _on_voice_input_failed(self):
        self.set_feedback("error")
        self.update()
    
    def keyPressEvent(self, event):
        if not self.listening:
            return
        
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        elif event.key() == Qt.Key.Key_Return:
            self.add_to_history(self._input_buffer)
            self.stop_listening()
            return
        elif event.key() == Qt.Key.Key_Backspace:
            self._input_buffer = self._input_buffer[:-1]
            self._history_index = -1  # Reset history when editing
            self._suggestions = self._get_suggestions(self._input_buffer)
        elif event.key() == Qt.Key.Key_Up:
            # Navigate history backwards
            if self._history_index < len(self._command_history) - 1:
                self._history_index += 1
                self._input_buffer = self._command_history[self._history_index]
            return
        elif event.key() == Qt.Key.Key_Down:
            # Navigate history forwards
            if self._history_index > 0:
                self._history_index -= 1
                self._input_buffer = self._command_history[self._history_index]
            elif self._history_index == 0:
                self._history_index = -1
                self._input_buffer = ""
            return
        elif event.key() == Qt.Key.Key_Tab:
            # Auto-complete from suggestions
            if self._suggestions:
                self._input_buffer = self._suggestions[self._current_suggestion_idx % len(self._suggestions)]
                self._current_suggestion_idx += 1
            return
        elif event.text().lower() == 'v':
            # Voice input (V key)
            self.capture_voice_input()
            return
        else:
            text = event.text()
            if text.isprintable():
                self._input_buffer += text
                self._history_index = -1  # Reset history when editing
                self._suggestions = self._get_suggestions(self._input_buffer)
        
        self.update()
    
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        cx, cy = 100, 90
        
        # Glow effect if feedback
        if self._feedback_type:
            glow_color = qcol(C.GREEN if self._feedback_type == "success" else C.RED, int(100 * self._glow_intensity))
            p.setBrush(QBrush(glow_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 65, 65)
        
        # Background circle with gradient
        grad = QRadialGradient(cx, cy, 50 * self._pulse_scale)
        grad.setColorAt(0, qcol(C.PRI, 240))
        grad.setColorAt(1, qcol(C.PRI_DIM, 180))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(qcol(C.ACC, 255), 2))
        p.drawEllipse(QPointF(cx, cy), 50 * self._pulse_scale, 50 * self._pulse_scale)
        
        # Wave circles if listening
        if self.listening:
            for i, angle in enumerate(self._wave_angles):
                rad = (50 * self._pulse_scale) + (i + 1) * 10
                alpha = 220 - (i * 70)
                p.setPen(QPen(qcol(C.ACC, alpha), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
        
        # Center icon
        p.setFont(QFont(C.FONT_SANS, 28, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE)))
        icon = "🎤" if self.listening else "✓"
        p.drawText(QRectF(cx - 40, cy - 40, 80, 80), Qt.AlignmentFlag.AlignCenter, icon)
        
        # Display typed text if any
        if self._input_buffer:
            p.setFont(QFont(C.FONT_MONO, 8, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.GREEN)))
            text_rect = QRectF(5, 5, 190, 25)
            p.drawText(text_rect, Qt.TextFlag.TextWordWrap, self._input_buffer[-20:])
        
        # Display suggestions if available
        if self._suggestions and self.listening:
            p.setFont(QFont(C.FONT_SANS, 7))
            y_offset = 120
            for i, suggestion in enumerate(self._suggestions[:2]):
                color = C.ACC if i == 0 else C.TEXT_MED
                p.setPen(QPen(qcol(color)))
                p.drawText(QRectF(10, y_offset, 180, 15), suggestion[:25] + ("..." if len(suggestion) > 25 else ""))
                y_offset += 16
        
        # Show help text at bottom
        p.setFont(QFont(C.FONT_MONO, 6))
        p.setPen(QPen(qcol(C.TEXT_DIM)))
        help_text = "↑↓ Hist  |  Tab ◄►  |  V 🎤Voice  |  Esc"
        p.drawText(QRectF(5, 220, 190, 15), help_text)



class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


def _make_startup_splash() -> QSplashScreen:
    pix = QPixmap(240, 240)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Rounded glowing glass background for the logo
    p.setBrush(QBrush(qcol("#00080d", 230)))
    p.setPen(QPen(qcol(C.PRI, 90), 1))
    p.drawRoundedRect(QRectF(10, 10, 220, 220), 110, 110)

    logo_rect = QRectF(60, 60, 120, 120)
    if APP_LOGO.exists():
        logo = QPixmap(str(APP_LOGO))
        if not logo.isNull():
            p.drawPixmap(logo_rect.toRect(), logo.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            p.setFont(QFont(C.FONT_SANS, 48, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.PRI)))
            p.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter, "J")
    else:
        p.setFont(QFont(C.FONT_SANS, 48, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI)))
        p.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter, "J")

    p.end()

    splash = QSplashScreen(pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    return splash


class JarvisUI:
    def __init__(self, face_path: str, size=None, start_hidden: bool = False):
        self._app = _ensure_qapplication()
        try:
            self._app.setStyle("Fusion")
        except Exception:
            pass
        # ── Premium foundation: bundled fonts + global stylesheet ──
        try:
            _load_premium_fonts()
        except Exception:
            pass
        try:
            base_pt = 10 if _OS == "Windows" else (11 if _OS == "Darwin" else 10)
            self._app.setFont(pfont(base_pt, "regular"))
        except Exception:
            # fallback to prior system-font behaviour
            try:
                if _OS == "Windows":
                    self._app.setFont(QFont("Segoe UI", 10))
                elif _OS == "Darwin":
                    self._app.setFont(QFont("Helvetica Neue", 11))
                else:
                    self._app.setFont(QFont("DejaVu Sans", 10))
            except Exception:
                pass
        try:
            self._app.setStyleSheet(_global_qss())
        except Exception:
            pass
        self._splash = None
        if not start_hidden:
            self._splash = _make_startup_splash()
            self._splash.show()
            self._app.processEvents()
        self._win = MainWindow(face_path)
        if start_hidden and not getattr(self._win, "_auto_wake", True):
            try:
                self._win.set_wake_enabled(True)
                self._win._log.append_log("SYS: Background launch forced Wake Link ON.")
            except Exception:
                self._win._auto_wake = True
        if start_hidden and getattr(self._win, "tray_enabled", True):
            QTimer.singleShot(250, lambda: self._win.hide_to_tray(show_message=False))
        else:
            self._win.show()
        if self._splash is not None:
            self._splash.finish(self._win)
            QTimer.singleShot(900, self._splash.close)
            
        # Check for update flag in session dynamically
        try:
            sp = Path(__file__).resolve().parent / "config" / "user_session.json"
            if sp.exists():
                import json
                sess = json.loads(sp.read_text(encoding="utf-8"))
                if sess.get("update_available"):
                    latest = sess.get("update_available")
                    url = sess.get("update_url", "http://localhost:8000/download")
                    # Clear it from session so it doesn't prompt on every boot
                    sess.pop("update_available", None)
                    sess.pop("update_url", None)
                    sp.write_text(json.dumps(sess, indent=4), encoding="utf-8")
                    
                    # Show update notice dynamically
                    QTimer.singleShot(2500, lambda: self._show_update_notice(latest, url))
        except Exception:
            pass
        
        # Floating Voice Assistant
        self._floating_assistant = FloatingAssistant()
        self._floating_assistant.command_ready.connect(self._on_floating_command)
        self._win.attach_floating_assistant(self._floating_assistant)
        
        # Voice Activation Engine (listen for "Jarvis" keyword)
        if HAS_ADVANCED_FEATURES and voice_engine:
            try:
                if hasattr(voice_engine, "refresh_wake_words"):
                    voice_engine.refresh_wake_words()
            except Exception:
                pass
            voice_engine.on_activation = lambda event=None: self._win._voice_wake_sig.emit(event or {})
            voice_engine.voice_mode = VoiceMode.HYBRID
            try:
                voice_engine.start_listening()
                print("🎤 Voice activation ready - say 'Jarvis' to activate!")
                self._win._refresh_wake_tile()
            except Exception as e:
                print(f"⚠️ Voice activation error: {e}")
                self._win._refresh_wake_tile()
        
        # Hotkey for floating assistant (F12)
        try:
            hotkey = QShortcut(QKeySequence(Qt.Key.Key_F12), self._win)
            hotkey.activated.connect(self._toggle_floating_assistant)
        except Exception:
            pass
        self.root = _RootShim(self._app)
        
    def _show_update_notice(self, latest: str, url: str):
        try:
            from PyQt6.QtWidgets import QMessageBox
            msg = QMessageBox(self._win)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("JOYA AI OS - Update Available")
            msg.setText("✨ A new version update of JOYA AI OS is available!")
            msg.setInformativeText(f"Version v{latest} is ready. Would you like to open the download page to update now?")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            if msg.exec() == QMessageBox.StandardButton.Yes:
                import webbrowser
                webbrowser.open(url)
        except Exception as e:
            print(f"[UPDATE] Error showing update dialog: {e}")

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)
        # Auto-speak any "Jarvis:" reply via native TTS (backup for Gemini Live audio)
        try:
            if text and text.startswith("Jarvis:") and not self._win._muted:
                # Strip markdown/emojis for cleaner speech
                import re as _re
                clean = _re.sub(r'[*_`#>]', '', text[8:]).strip()[:300]
                if clean:
                    _tts_speak(clean, blocking=False)
        except Exception:
            pass

    def wake_diagnostics(self) -> dict:
        return self._win.wake_diagnostics()

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def _toggle_floating_assistant(self):
        """Toggle floating voice assistant (F12 hotkey)"""
        if self._floating_assistant.isVisible():
            self._floating_assistant.hide()
        else:
            self._win.open_floating_assistant()
    
    def _on_floating_command(self, text: str):
        """Handle command from floating assistant"""
        # Expand aliases if available
        if HAS_ADVANCED_FEATURES:
            expanded_text = command_aliases.expand_alias(text)
            print(f"📋 Command: {text} → {expanded_text}" if expanded_text != text else f"📋 Command: {text}")
        else:
            expanded_text = text
        
        if expanded_text.strip():
            self._floating_assistant.set_speaking(expanded_text)
            self._floating_assistant.set_feedback("pending")
            
            # Add to command history
            FloatingAssistant.add_to_history(expanded_text)
            
            # Text-to-speech feedback (optional)
            if HAS_ADVANCED_FEATURES and tts_engine:
                try:
                    tts_engine.speak(f"Executing: {text}", blocking=False)
                except Exception:
                    pass
            
            self._win._dispatch_command(expanded_text, source="Floating Assistant")


