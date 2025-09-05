"""
overlay_wallpaper_full.py

Minimal, reliable overlay live-wallpaper for Windows (Option 1).
Features:
- Fullscreen click-through wallpaper window that plays an MP4 using VLC (hardware decode enabled).
- Separate floating Control Panel with:
    * Load MP4
    * Play / Pause
    * Playback speed selector (0.5x, 1x, 1.5x, 2x)
    * Aspect toggle (Fit -> stretch to screen / KeepAspect -> 16:9/auto)
    * Set as Default button
- System tray icon with menu (Show Controls, Load MP4, Play/Pause, Toggle Aspect, Quit).
- Global hotkey Ctrl+R toggles aspect mode (Fit <-> KeepAspect).
- Keeps wallpaper window forced to bottom periodically so apps don't go behind it.
- Saves last-used settings (last video path, default video, speed, aspect) to a JSON file in same folder.
- Lightweight: uses python-vlc for decoding; prefer 720p/480p mp4 for low CPU.

Requirements:
    pip install PySide6 python-vlc keyboard
    + Install VLC (VideoLAN) on Windows and copy libvlc.dll, libvlccore.dll and plugins next to EXE when packaging.

Usage:
    python overlay_wallpaper_full.py
"""

import sys
import os
import json
import ctypes
from pathlib import Path
from typing import Optional

from PySide6 import QtWidgets, QtCore, QtGui
import vlc
import keyboard  # global hotkey (Ctrl+R)

# -----------------------
# Constants, paths, and Win32 helpers
# -----------------------
APP_DIR = Path(r"C:\Users\USER\AppData\Local\LiveWallpaper")
APP_DIR.mkdir(parents=True, exist_ok=True)  # make sure folder exists
CONFIG_PATH = APP_DIR / "overlay_wallpaper_config.json"


user32 = ctypes.windll.user32
SetWindowPos = user32.SetWindowPos
GetWindowLongW = user32.GetWindowLongW
SetWindowLongW = user32.SetWindowLongW

HWND_BOTTOM = 1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002

WS_EX_TRANSPARENT = 0x20    # mouse clicks pass through
WS_EX_TOOLWINDOW = 0x80    # avoid appearing in alt-tab
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20

DEFAULT_CONFIG = {
    "last_video": "",
    "default_video": "",  # new key for default video
    "speed": 1.0,
    "aspect_mode": "fit"  # "fit" or "keep_aspect"
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def make_clickthrough(hwnd: int):
    """Make window ignore mouse clicks (so desktop icons stay clickable)."""
    try:
        style = GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
        SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
    except Exception:
        pass


def send_to_bottom(hwnd: int):
    """Place window at the very bottom."""
    try:
        SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                     SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
    except Exception:
        pass


# -----------------------
# VLC-backed Wallpaper Window (fullscreen click-through)
# -----------------------
class WallpaperWindow(QtWidgets.QWidget):
    def __init__(self, config: dict):
        super().__init__(None, QtCore.Qt.Window)
        # Frameless, translucent, no decorations
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setCursor(QtCore.Qt.BlankCursor)

        # VLC initialization with HW decode hints and proper looping
        vlc_args = [
            "--quiet",
            "--intf=dummy",
            "--no-video-title-show",
            "--no-osd",
            "--no-sub-autodetect-file",
            "--avcodec-hw=dxva2",
            "--drop-late-frames",
            "--skip-frames",
            "--no-xlib",
            "--input-repeat=999999",  # Very high repeat count for seamless looping
        ]

        self.vlc_instance = vlc.Instance(vlc_args)
        self.player = self.vlc_instance.media_player_new()
        self.media: Optional[vlc.Media] = None

        # state & config
        self.config = config
        self.aspect_mode = config.get("aspect_mode", "fit")  # "fit" or "keep_aspect"
        self.speed = float(config.get("speed", 1.0))
        self.current_video_path = ""
        self._was_playing = False  # Track if we were playing before
        self._manually_paused = False  # Track if user manually paused

        # Timer to periodically force bottom z-order
        self._keep_bottom_timer = QtCore.QTimer(self)
        self._keep_bottom_timer.setInterval(1000)  # every 1s
        self._keep_bottom_timer.timeout.connect(self._ensure_bottom)

    def showEvent(self, event: QtGui.QShowEvent):
        super().showEvent(event)
        # Resize to primary screen
        screen = QtWidgets.QApplication.primaryScreen()
        geom = screen.geometry()
        self.setGeometry(geom)

        hwnd = int(self.winId())
        make_clickthrough(hwnd)
        send_to_bottom(hwnd)

        # Bind VLC to our native window
        try:
            if sys.platform.startswith("win"):
                self.player.set_hwnd(hwnd)
            else:
                # For other platforms
                self.player.set_xwindow(hwnd)
        except Exception as e:
            print(f"Error setting window handle: {e}")

        # Start timer
        self._keep_bottom_timer.start()

    def _ensure_bottom(self):
        try:
            send_to_bottom(int(self.winId()))
        except Exception:
            pass

    def load(self, path: str):
        if not Path(path).exists():
            raise FileNotFoundError(path)

        self.current_video_path = path
        media = self.vlc_instance.media_new(path)

        # Set proper looping options for seamless playback
        media.add_option("input-repeat=999999")  # Very high repeat count
        media.add_option("--loop")  # Enable native looping

        # Set caching for smoother playback
        media.add_option(":network-caching=300")
        media.add_option(":file-caching=300")
        media.add_option(":live-caching=300")

        self.media = media
        self.player.set_media(media)

        # set playback rate if player supports it
        try:
            if self.speed != 1.0:
                self.player.set_rate(self.speed)
        except Exception:
            pass

        # Set aspect ratio based on current mode
        self._apply_aspect_mode()

    def _apply_aspect_mode(self):
        """Apply the current aspect mode to the player."""
        if self.aspect_mode == "fit":
            try:
                self.player.video_set_scale(1)  # Scale to fit window
                self.player.video_set_aspect_ratio(None)  # Clear aspect ratio
            except Exception:
                pass
        else:
            try:
                self.player.video_set_scale(0)  # Use default scaling
                self.player.video_set_aspect_ratio("16:9")  # Force 16:9 aspect
            except Exception:
                pass

    def play(self):
        if self.media is None:
            return
        try:
            self.player.play()
            self._was_playing = True
            self._manually_paused = False
            # apply rate again after play
            if self.speed != 1.0:
                try:
                    self.player.set_rate(self.speed)
                except Exception:
                    pass
        except Exception as e:
            print(f"Error playing video: {e}")

    def pause(self):
        try:
            self.player.pause()
            self._was_playing = False
            self._manually_paused = True
        except Exception as e:
            print(f"Error pausing video: {e}")

    def stop(self):
        try:
            self.player.stop()
            self.current_video_path = ""
            self._was_playing = False
            self._manually_paused = False
        except Exception as e:
            print(f"Error stopping video: {e}")

    def set_speed(self, speed: float):
        self.speed = float(speed)
        try:
            self.player.set_rate(self.speed)
        except Exception as e:
            print(f"Error setting playback speed: {e}")

    def toggle_aspect(self) -> str:
        """Toggle aspect mode. Return new mode string for UI feedback."""
        if self.aspect_mode == "fit":
            self.aspect_mode = "keep_aspect"
        else:
            self.aspect_mode = "fit"

        self._apply_aspect_mode()
        return self.aspect_mode

    # Helper for UI to query state
    def is_playing(self) -> bool:
        try:
            return bool(self.player.is_playing())
        except Exception:
            return False


# -----------------------
# Control Panel (floating, can be closed to tray)
# -----------------------
class ControlPanel(QtWidgets.QMainWindow):
    def __init__(self, wallpaper: WallpaperWindow, config: dict):
        super().__init__(None, QtCore.Qt.WindowStaysOnTopHint)
        self.wallpaper = wallpaper
        self.config = config

        self.setWindowTitle("Lite Live Wallpaper — Controls")
        self.setFixedSize(450, 90)  # Increased width for new button
        # main layout
        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # First row: Load + Play/Pause + Set Default + Tray minimize
        row1 = QtWidgets.QHBoxLayout()
        self.btn_load = QtWidgets.QPushButton("Load MP4")
        self.btn_load.setToolTip("Load an MP4 to use as wallpaper")
        self.btn_playpause = QtWidgets.QPushButton("Play")
        self.btn_playpause.setEnabled(False)
        self.btn_setdefault = QtWidgets.QPushButton("Set as Default")
        self.btn_setdefault.setEnabled(False)
        self.btn_setdefault.setToolTip("Set this video to play automatically on app startup")
        self.btn_minimize = QtWidgets.QPushButton("Minimize to Tray")
        row1.addWidget(self.btn_load)
        row1.addWidget(self.btn_playpause)
        row1.addWidget(self.btn_setdefault)
        row1.addWidget(self.btn_minimize)
        v.addLayout(row1)

        # Second row: Speed combobox + aspect label+button
        row2 = QtWidgets.QHBoxLayout()
        self.speed_box = QtWidgets.QComboBox()
        self.speed_box.addItems(["0.5x", "1x", "1.5x", "2x"])
        # set current index based on config
        speed = float(config.get("speed", 1.0))
        self.speed_box.setCurrentIndex({0.5: 0, 1.0: 1, 1.5: 2, 2.0: 3}.get(speed, 1))
        self.lbl_aspect = QtWidgets.QLabel("Aspect:")
        self.btn_aspect = QtWidgets.QPushButton("Fit" if config.get("aspect_mode", "fit") == "fit" else "KeepAspect")
        row2.addWidget(QtWidgets.QLabel("Speed:"))
        row2.addWidget(self.speed_box)
        row2.addStretch(1)
        row2.addWidget(self.lbl_aspect)
        row2.addWidget(self.btn_aspect)
        v.addLayout(row2)

        # Status row
        self.status = QtWidgets.QLabel("Idle")
        v.addWidget(self.status)

        self.setCentralWidget(central)

        # connect signals
        self.btn_load.clicked.connect(self.on_load)
        self.btn_playpause.clicked.connect(self.on_playpause)
        self.btn_setdefault.clicked.connect(self.on_set_default)
        self.speed_box.currentIndexChanged.connect(self.on_speed_change)
        self.btn_aspect.clicked.connect(self.on_aspect_toggle)
        self.btn_minimize.clicked.connect(self.hide_to_tray)

        # tray
        self._create_tray()

        # register global hotkey Ctrl+R
        try:
            keyboard.add_hotkey("ctrl+r", self._on_hotkey_toggle_aspect, suppress=False, trigger_on_release=False)
        except Exception:
            # keyboard might require admin on some systems; ignore if fails
            pass

        # Check if we have a default video to load
        default_video = config.get("default_video", "")
        if default_video and Path(default_video).exists():
            try:
                self.wallpaper.load(default_video)
                self.btn_playpause.setEnabled(True)
                self.btn_playpause.setText("Pause")
                self.btn_setdefault.setEnabled(True)
                self.status.setText(f"Loaded default: {Path(default_video).name}")
                self.wallpaper.play()
            except Exception as e:
                print(f"Error loading default video: {e}")
        # Fallback to last video if no default set
        elif config.get("last_video", "") and Path(config["last_video"]).exists():
            try:
                self.wallpaper.load(config["last_video"])
                self.btn_playpause.setEnabled(True)
                self.btn_playpause.setText("Pause")
                self.btn_setdefault.setEnabled(True)
                self.status.setText(f"Loaded: {Path(config['last_video']).name}")
                self.wallpaper.play()
            except Exception as e:
                print(f"Error loading last video: {e}")

    # ---------------- tray ----------------
    def _create_tray(self):
        self.tray = QtWidgets.QSystemTrayIcon(self)
        # use app icon if available, otherwise standard icon
        icon = QtGui.QIcon.fromTheme("video") if QtGui.QIcon.hasThemeIcon("video") else self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Lite Live Wallpaper")
        menu = QtWidgets.QMenu()

        act_show = menu.addAction("Show Controls")
        act_load = menu.addAction("Load MP4…")
        act_play = menu.addAction("Play / Pause")
        act_aspect = menu.addAction("Toggle Aspect (Ctrl+R)")
        menu.addSeparator()
        act_quit = menu.addAction("Quit")

        act_show.triggered.connect(self.show_panel)
        act_load.triggered.connect(self.on_load)
        act_play.triggered.connect(self.on_playpause)
        act_aspect.triggered.connect(self.on_aspect_toggle)
        act_quit.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        # single click: show controls
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            self.show_panel()

    def show_panel(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def hide_to_tray(self):
        self.hide()
        self.tray.showMessage("Lite Live Wallpaper", "App minimized to tray. Use tray menu or Ctrl+R.", QtWidgets.QSystemTrayIcon.Information, 2200)

    # ---------------- actions ----------------
    def on_load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose MP4", str(Path.home()), "Videos (*.mp4)")
        if not path:
            return
        # save config
        self.config["last_video"] = path
        save_config(self.config)
        # load to wallpaper and update UI
        try:
            self.wallpaper.load(path)
            self.btn_playpause.setEnabled(True)
            self.btn_setdefault.setEnabled(True)
            self.wallpaper.play()
            self.btn_playpause.setText("Pause")
            self.status.setText(f"Playing: {Path(path).name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to load video:\n{e}")

    def on_playpause(self):
        if self.wallpaper.is_playing():
            self.wallpaper.pause()
            self.btn_playpause.setText("Play")
            self.status.setText("Paused")
        else:
            self.wallpaper.play()
            self.btn_playpause.setText("Pause")
            self.status.setText("Playing")

    def on_set_default(self):
        """Set the currently loaded video as the default startup video."""
        if self.wallpaper.current_video_path:
            self.config["default_video"] = self.wallpaper.current_video_path
            save_config(self.config)
            self.status.setText(f"Default set: {Path(self.wallpaper.current_video_path).name}")
            self.tray.showMessage("Lite Live Wallpaper", f"Default video set: {Path(self.wallpaper.current_video_path).name}", QtWidgets.QSystemTrayIcon.Information, 2000)

    def on_speed_change(self, index: int):
        mapping = {0: 0.5, 1: 1.0, 2: 1.5, 3: 2.0}
        speed = mapping.get(index, 1.0)
        self.wallpaper.set_speed(speed)
        self.config["speed"] = speed
        save_config(self.config)
        self.status.setText(f"Speed: {speed}x")

    def on_aspect_toggle(self):
        new_mode = self.wallpaper.toggle_aspect()
        # update button text
        self.btn_aspect.setText("Fit" if new_mode == "fit" else "KeepAspect")
        self.config["aspect_mode"] = new_mode
        save_config(self.config)
        self.tray.showMessage("Lite Live Wallpaper", f"Aspect set to: {new_mode}", QtWidgets.QSystemTrayIcon.Information, 1200)

    def _on_hotkey_toggle_aspect(self):
        # called from global hotkey thread -> use Qt event to call in main thread
        QtCore.QMetaObject.invokeMethod(self, "_hotkey_toggle_aspect_gui", QtCore.Qt.QueuedConnection)

    @QtCore.Slot()
    def _hotkey_toggle_aspect_gui(self):
        self.on_aspect_toggle()

    def hideEvent(self, event):
        # when user closes/hides the control panel, keep app in tray
        super().hideEvent(event)

    def closeEvent(self, event):
        # instead of quitting, hide to tray (so wallpaper continues)
        event.ignore()
        self.hide_to_tray()

    def quit_app(self):
        # cleanup keyboard hooks
        try:
            keyboard.clear_all_hotkeys()
        except Exception:
            pass
        # stop wallpaper player
        try:
            self.wallpaper.stop()
        except Exception:
            pass
        QtWidgets.QApplication.quit()


# -----------------------
# Main entrypoint
# -----------------------
def main():
    cfg = load_config()

    if not sys.platform.startswith("win"):
        QtWidgets.QMessageBox.critical(None, "Unsupported OS", "This app currently supports Windows only.")
        return

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running when windows are closed (tray mode)

    # Instantiate wallpaper (hidden fullscreen overlay)
    wallpaper = WallpaperWindow(cfg)
    wallpaper.showFullScreen()  # shows wallpaper window (click-through)

    # Instantiate control panel
    panel = ControlPanel(wallpaper, cfg)
    panel.show()  # user sees controls; can minimize to tray

    try:
        sys.exit(app.exec())
    finally:
        # ensure keyboard hooks cleared in case of unexpected exit
        try:
            keyboard.clear_all_hotkeys()
        except Exception:
            pass


if __name__ == "__main__":
    main()