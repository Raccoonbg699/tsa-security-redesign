import sys
import os
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QGridLayout, QFrame,
    QInputDialog, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QSlider, QAbstractItemView, QProgressDialog, QSizePolicy,
    QFileSystemModel, QTreeView, QSplitter, QMessageBox, QCheckBox,
    QStackedWidget, QMenu
)
from PyQt5.QtWidgets import QComboBox  # <- ДОБАВЕН ОТДЕЛНО ЗАРАДИ ПРОБЛЕМИ С IDE

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QDir, QRect, QPoint, QSize
from PyQt5.QtGui import QFont, QImage, QPixmap, QPainter, QPen, QIcon, QIntValidator

import json
from pathlib import Path
from ipaddress import ip_network, ip_address, IPv4Network  # ДОБАВЕН: За мрежово сканиране
import time  # ДОБАВЕН: За sleep в Camera стрийма

# ДОБАВЕНИ:
import threading
from datetime import datetime
import socket

# КРАЙ НА ДОБАВКИТЕ

# Опит за импортиране на ONVIF библиотека. Ако я няма, PTZ няма да работи.
try:
    from onvif import ONVIFCamera

    ONVIF_ENABLED = True
except ImportError:
    ONVIF_ENABLED = False

# Импортиране на диалозите за вход и управление на потребители
from login_dialog import LoginDialog
from user_management_dialog import UserManagementDialog
from user_manager import UserManager


# Клас Camera (вече преместен по-нагоре във файла)
class Camera:
    def __init__(self, name, ip_address, port, status="Неактивна", rtsp_url=""):
        self.name = name
        self.ip_address = ip_address
        self.port = port
        self.status = status  # Активна/Неактивна
        self.rtsp_url = rtsp_url  # Добавен RTSP URL
        self.is_recording = False
        self.motion_detection_enabled = False
        self.motion_sensitivity = "Средна"  # Ниска, Средна, Висока
        self.detection_zones = []  # Списък от QRect обекти

        # За стрийминг и PTZ
        self._cap = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._stream_thread = None
        self._is_streaming = False
        self._onvif_cam = None
        self._ptz_service = None
        self._media_profile = None

        # Запис
        self._video_writer = None
        self._is_manual_recording = False
        self._is_motion_recording = False
        self._last_motion_time = 0
        self._prev_frame_gray = None  # За детекция на движение

    def to_dict(self):
        return {
            "name": self.name,
            "ip_address": self.ip_address,
            "port": self.port,
            "status": self.status,
            "rtsp_url": self.rtsp_url,
            "motion_detection_enabled": self.motion_detection_enabled,
            "motion_sensitivity": self.motion_sensitivity,
            "detection_zones": [[zone.x(), zone.y(), zone.width(), zone.height()] for zone in self.detection_zones]
        }

    @classmethod
    def from_dict(cls, data):
        camera = cls(data["name"], data["ip_address"], data["port"],
                     data.get("status", "Неактивна"), data.get("rtsp_url", ""))
        camera.motion_detection_enabled = data.get("motion_detection_enabled", False)
        camera.motion_sensitivity = data.get("motion_sensitivity", "Средна")
        camera.detection_zones = [QRect(x, y, w, h) for x, y, w, h in data.get("detection_zones", [])]
        return camera

    def start_stream(self):
        if self._is_streaming:
            return True
        self._is_streaming = True
        self._stream_thread = threading.Thread(target=self._run_stream, daemon=True)
        self._stream_thread.start()
        return True

    def stop_stream(self):
        if not self._is_streaming:
            return
        self._is_streaming = False
        if self._stream_thread:
            self._stream_thread.join(timeout=2)
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._cap = None
        self._latest_frame = None
        self._prev_frame_gray = None
        self.stop_recording()

    def _run_stream(self):
        # Опитваме да инициализираме OpenCV стрийма.
        # Ако rtsp_url не е зададен, използваме IP и порт (което е по-лоша практика)
        stream_url = self.rtsp_url if self.rtsp_url else f"rtsp://{self.ip_address}:{self.port}/"
        print(f"[{self.name}] [Stream Thread] Attempting to open stream from URL: {stream_url}")

        self._cap = cv2.VideoCapture(stream_url)

        if self._cap.isOpened():
            self._is_streaming = True
            print(f"[{self.name}] [Stream Thread] Stream successfully opened. _is_streaming set to True.")
            self.initialize_onvif() # Инициализиране на ONVIF, ако е необходимо
            print(f"[{self.name}] [Stream Thread] ONVIF initialized (if applicable). Entering frame reading loop.")
        else:
            self._is_streaming = False
            print(f"[{self.name}] [Stream Thread] FAILED to open stream from {stream_url}. _is_streaming set to False.")

            print(f"[{self.name}] [Stream Thread] Retrying stream connection in 5 seconds...")
            time.sleep(5)
            self._cap = cv2.VideoCapture(stream_url)
            if self._cap.isOpened():
                self._is_streaming = True
                print(f"[{self.name}] [Stream Thread] Stream successfully reconnected after retry.")
                self.initialize_onvif()
                print(
                    f"[{self.name}] [Stream Thread] ONVIF re-initialized (if applicable). Entering frame reading loop.")
            else:
                print(f"[{self.name}] [Stream Thread] FAILED to reconnect after retry. Stopping stream thread.")
                return  # Излизаме от нишката, ако не успеем да се свържем

        while self._is_streaming and self._cap.isOpened():
            ret, frame = self._cap.read()

            if not ret:
                print(f"[{self.name}] [Stream Thread] Failed to read frame (ret=False). Attempting reconnect...")
                self._cap.release()
                time.sleep(2)  # Кратка пауза преди опит за повторно свързване
                self._cap = cv2.VideoCapture(stream_url)
                if not self._cap.isOpened():
                    print(f"[{self.name}] [Stream Thread] FAILED to reconnect after frame read error. Stopping stream.")
                    self._is_streaming = False
                continue  # Продължаваме към следващата итерация на цикъла

                self._handle_motion_detection(frame)

                with self._frame_lock:
                    self._latest_frame = frame
                    # Проверка дали записът е активен
                    if (self._is_manual_recording or self._is_motion_recording) and self._video_writer:
                        try:
                            self._video_writer.write(frame)
                        except Exception as e:
                            print(f"[{self.name}] [Stream Thread] Error writing frame to video writer: {e}")
                            self.stop_recording()  # Спираме записа при грешка

            # Когато цикълът приключи (т.е. _is_streaming е False или _cap не е отворен)
            print(f"[{self.name}] [Stream Thread] Exiting main loop. Releasing resources.")
            if self._cap and self._cap.isOpened():
                self._cap.release()
                print(f"[{self.name}] [Stream Thread] VideoCapture released.")
            self.stop_recording()
            self._is_streaming = False  # Уверете се, че флагът е False при изход
            print(f"[{self.name}] Stream thread finished for camera {self.name}.")

        if not self._cap.isOpened():
            print(f"[{self.name}] Failed to open stream from {stream_url}")
            self._is_streaming = False
            return

        self.initialize_onvif()  # Инициализиране на ONVIF, ако е необходимо

        while self._is_streaming and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                print(f"[{self.name}] Lost stream from {stream_url}. Attempting to reconnect...")
                self._cap.release()
                time.sleep(5)  # Изчакайте преди повторно свързване
                self._cap = cv2.VideoCapture(stream_url)
                if not self._cap.isOpened():
                    print(f"[{self.name}] Failed to reconnect to {stream_url}. Stopping stream.")
                    self._is_streaming = False
                continue

            self._handle_motion_detection(frame)

            with self._frame_lock:
                self._latest_frame = frame
                if (self._is_manual_recording or self._is_motion_recording) and self._video_writer:
                    self._video_writer.write(frame)

        if self._cap and self._cap.isOpened():
            self._cap.release()
        self.stop_recording()
        self._is_streaming = False
        print(f"[{self.name}] Stream stopped.")

    def get_frame(self):
        with self._frame_lock:
            return self._latest_frame

    def initialize_onvif(self):
        if not ONVIF_ENABLED or not self.ip_address:  # ONVIF изисква IP адрес
            print(f"[{self.name}] ONVIF disabled or no IP address. PTZ will not work.")
            return
        try:
            # ONVIF обикновено работи на HTTP/S портове (80/443) или специфични ONVIF портове (5000/8000),
            # а не RTSP порта. Може да се наложи да се конфигурира.
            # Засега, използваме порт 80 като стандартен опит.
            self._onvif_cam = ONVIFCamera(self.ip_address, 80, "username", "password")  # Placeholder
            self._ptz_service = self._onvif_cam.create_ptz_service()
            profiles = self._onvif_cam.get_profiles()
            self._media_profile = profiles[0]  # Взимаме първия профил
            print(f"[{self.name}] ONVIF Initialized Successfully.")
        except Exception as e:
            print(f"[{self.name}] ONVIF Initialization Failed: {e}")
            self._onvif_cam = None
            self._ptz_service = None

    def ptz_move(self, pan=0.0, tilt=0.0, zoom=0.0):
        if not self._ptz_service or not self._media_profile:
            return
        try:
            request = self._ptz_service.create_type('ContinuousMove')
            request.ProfileToken = self._media_profile.token
            request.Velocity = {'PanTilt': {'x': pan, 'y': tilt}, 'Zoom': {'x': zoom}}
            self._ptz_service.ContinuousMove(request)
        except Exception as e:
            print(f"[{self.name}] PTZ Move Error: {e}")

    def ptz_stop(self):
        if not self._ptz_service or not self._media_profile:
            return
        try:
            self._ptz_service.Stop({'ProfileToken': self._media_profile.token})
        except Exception as e:
            print(f"[{self.name}] PTZ Stop Error: {e}")

    def start_recording(self, is_motion=False):
        if self._video_writer or not self._latest_frame:
            return False

        # Създаване на папка за камерата, ако не съществува
        camera_recordings_dir = Path(RECORDINGS_DIR) / self.name
        camera_recordings_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "motion" if is_motion else "manual"
        filename = camera_recordings_dir / f"{prefix}_{timestamp}.mp4"

        try:
            height, width, _ = self._latest_frame.shape
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # MP4 codec
            # Опит за получаване на FPS от потока или използване на 25 като резервен
            fps = self._cap.get(cv2.CAP_PROP_FPS) if self._cap and self._cap.isOpened() else 25.0
            if fps <= 0: fps = 25.0  # Санитарна проверка

            self._video_writer = cv2.VideoWriter(str(filename), fourcc, fps, (width, height))
            if not self._video_writer.isOpened():
                raise IOError("OpenCV VideoWriter failed to open.")

            if is_motion:
                self._is_motion_recording = True
            else:
                self._is_manual_recording = True

            print(f"[{self.name}] Recording started: {filename}")
            return True
        except Exception as e:
            print(f"[{self.name}] Failed to start recording: {e}")
            if self._video_writer:
                self._video_writer.release()
            self._video_writer = None
            return False

    def stop_recording(self):
        if self._video_writer:
            print(f"[{self.name}] Recording stopped.")
            self._video_writer.release()
            self._video_writer = None
        self._is_manual_recording = False
        self._is_motion_recording = False

    def _handle_motion_detection(self, frame):
        if not self.motion_detection_enabled:
            return
        if frame is None:
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_frame_gray is None:
            self._prev_frame_gray = gray
            return

        frame_delta = cv2.absdiff(self._prev_frame_gray, gray)
        thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]

        motion_pixels = 0
        if self.detection_zones:
            # Сумиране на пикселите за движение във всички дефинирани зони
            for zone_rect in self.detection_zones:
                x, y, w, h = zone_rect.x(), zone_rect.y(), zone_rect.width(), zone_rect.height()
                # Уверете се, че зоните са в границите на кадъра
                h_frame, w_frame = frame.shape[:2]
                x = max(0, min(x, w_frame - 1))
                y = max(0, min(y, h_frame - 1))
                w = max(0, min(w, w_frame - x))
                h = max(0, min(h, h_frame - y))

                if w > 0 and h > 0:
                    roi_mask = thresh[y:y + h, x:x + w]
                    motion_pixels += cv2.countNonZero(roi_mask)
        else:
            motion_pixels = cv2.countNonZero(thresh)

        # Конвертиране на чувствителността от текст към числова стойност
        sensitivity_threshold = 0
        if self.motion_sensitivity == "Ниска":
            sensitivity_threshold = 5000  # Повече пиксели
        elif self.motion_sensitivity == "Средна":
            sensitivity_threshold = 2000
        elif self.motion_sensitivity == "Висока":
            sensitivity_threshold = 500  # По-малко пиксели

        if motion_pixels > sensitivity_threshold:
            self._last_motion_time = time.time()
            if not self._is_motion_recording:
                print(f"[{self.name}] Motion detected. Starting motion recording.")
                self.start_recording(is_motion=True)
        else:
            if self._is_motion_recording and (time.time() - self._last_motion_time > 5):  # 5 секунди post-motion запис
                self._is_motion_recording = False
                if not self._is_manual_recording:  # Спира записа само ако няма ръчен запис
                    self.stop_recording()
                print(f"[{self.name}] Motion stopped.")

        self._prev_frame_gray = gray


# Клас NetworkScanner (вече преместен по-нагоре във файла)
class NetworkScanner(QObject):
    camera_found = pyqtSignal(str)  # Излъчва IP адрес
    scan_progress = pyqtSignal(int)  # Прогрес в проценти
    scan_finished = pyqtSignal(str)  # Съобщение за край на сканирането

    def __init__(self, subnet):
        super().__init__()
        self.subnet = subnet
        self.is_cancelled = False

    def run(self):
        print(f"Starting scan for subnet: {self.subnet}")
        hosts = list(self.subnet.hosts())
        total_hosts = len(hosts)

        for i, ip in enumerate(hosts):
            if self.is_cancelled:
                self.scan_finished.emit("Сканирането е отменено.")
                print("Network scan cancelled.")
                return

            ip_str = str(ip)
            # Проверка на RTSP порт (554)
            try:
                # Използваме по-дълъг таймаут, за да избегнем "Too many open files"
                # или други мрежови грешки при много бързо сканиране.
                # За пълно сканиране, може да се наложи да се контролира скоростта.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    if sock.connect_ex((ip_str, 554)) == 0:
                        self.camera_found.emit(ip_str)
                        print(f"Camera found at: {ip_str}")
            except Exception as e:
                print(f"Error checking {ip_str}: {e}")
                pass  # Просто продължаваме към следващия IP

            progress = int(((i + 1) / total_hosts) * 100)
            self.scan_progress.emit(progress)

        self.scan_finished.emit("Мрежовото сканиране приключи.")
        print("Network scan finished.")

    def cancel(self):
        self.is_cancelled = True


# --- Глобални настройки ---
# Дефиниране на цветови константи
PRIMARY_COLOR = "#333333"  # Тъмно сиво за основни елементи
SECONDARY_COLOR = "#444444"  # По-светло сиво за вторични елементи
ACCENT_COLOR = "#FF8C00"  # Оранжев акцент
TEXT_COLOR = "#F0F0F0"  # Светъл текст
BORDER_COLOR = "#555555"  # Цвят на рамката
HOVER_COLOR = "#555555"  # Цвят при hover за бутони/елементи
PANEL_BG_COLOR = "#3A3A3A"  # Фон на панелите (по-светъл от основния)
FIELD_BG_COLOR = "#3A3A3A"  # Фон на входните полета
BUTTON_HOVER_COLOR = "#E67E00"  # По-тъмен оранжев при hover
BUTTON_PRESSED_COLOR = "#CC7000"  # Още по-тъмен оранжев при натискане
BG_COLOR = "#2C2C2C"  # ДОБАВЕН: Основен фонов цвят, използван в диалозите

# Икони (пътища, ако са локални)
# Уверете се, че тези икони съществуват в папка 'icons' във вашата директория на проекта.
# Ако ги нямате, програмата ще работи, но бутоните няма да имат икони.
ICON_PATH_DASHBOARD = "icons/dashboard.png"
ICON_PATH_CAMERAS = "icons/camera.png"
ICON_PATH_RECORDS = "icons/records.png"
ICON_PATH_ALARMS = "icons/alarm.png"
ICON_PATH_ACTIONS = "icons/actions.png"  # Този път вече не е нужен, ако бутонът "Действия" е премахнат, но може да остане
ICON_PATH_SETTINGS = "icons/settings.png"
ICON_PATH_LIVE_VIEW = "icons/live_view.png"
ICON_PATH_USER = "icons/user.png"  # Този път вече не е нужен, ако бутонът "Потребител" е премахнат
ICON_PATH_BELL = "icons/bell.png"  # Този път вече не е нужен, ако бутонът "Известия" е премахнат
ICON_PATH_SNAPSHOT = "icons/snapshot.png"
ICON_PATH_REWIND = "icons/rewind.png"
ICON_PATH_FORWARD = "icons/forward.png"
ICON_PATH_ZOOM_IN = "icons/zoom_in.png"
ICON_PATH_ZOOM_OUT = "icons/zoom_out.png"
ICON_PATH_ARROW_DOWN = "icons/arrow_down.png"  # За QComboBox стрелките

# Проверка и създаване на папка за записи
RECORDINGS_DIR = "recordings"
if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)


# Клас CameraManager
class CameraManager:
    def __init__(self, cameras_file="cameras.json"):
        self.cameras_file = Path(cameras_file)
        self.cameras = self._load_cameras()

    def _load_cameras(self):
        if self.cameras_file.exists():
            with open(self.cameras_file, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    return {camera_data["name"]: Camera.from_dict(camera_data) for camera_data in data}
                except json.JSONDecodeError:
                    return {}
        return {}

    def _save_cameras(self):
        with open(self.cameras_file, 'w', encoding='utf-8') as f:
            json.dump([camera.to_dict() for camera in self.cameras.values()], f, indent=4, ensure_ascii=False)

    def add_camera(self, camera):
        if camera.name in self.cameras:
            return False, "Камера с това име вече съществува."
        # Проверка за уникален IP адрес
        if any(c.ip_address == camera.ip_address and c.port == camera.port for c in self.cameras.values()):
            return False, f"Камера с IP адрес {camera.ip_address}:{camera.port} вече съществува."

        self.cameras[camera.name] = camera
        self._save_cameras()
        return True, "Камерата е добавена успешно."

    def get_camera(self, name):
        return self.cameras.get(name)

    def get_all_cameras(self):
        return list(self.cameras.values())

    def update_camera(self, camera):
        if camera.name not in self.cameras:
            return False, "Камерата не е намерена."
        self.cameras[camera.name] = camera
        self._save_cameras()
        return True, "Камерата е актуализирана успешно."

    def delete_camera(self, name):
        if name not in self.cameras:
            return False, "Камерата не е намерена."
        del self.cameras[name]
        self._save_cameras()
        return True, "Камерата е изтрита успешно."

    def update_camera_status(self, name, status):
        camera = self.get_camera(name)
        if camera:
            camera.status = status
            self._save_cameras()
            return True
        return False


# Клас за странично меню
class SideMenu(QWidget):
    # Дефиниране на сигнал за промяна на страницата
    page_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PRIMARY_COLOR};
                border-right: 1px solid {BORDER_COLOR};
            }}
            QPushButton {{
                background-color: {PRIMARY_COLOR};
                color: {TEXT_COLOR};
                border: none;
                padding: 15px 10px;
                text-align: left;
                font-size: 16px;
                font-weight: bold;
                border-radius: 0px; /* Премахване на закръгляне */
            }}
            QPushButton:hover {{
                background-color: {HOVER_COLOR};
            }}
            QPushButton:checked {{
                background-color: {ACCENT_COLOR};
            }}
            QPushButton::icon {{
                margin-right: 10px;
            }}
        """)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Лого (може да е изображение или текст)
        logo_label = QLabel("Видеонаблюдение")
        logo_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        logo_label.setStyleSheet(f"color: {ACCENT_COLOR}; padding: 20px;")
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        # Бутони за навигация
        self.buttons = {}
        button_data = {
            "Камери": ICON_PATH_CAMERAS,
            "Записи": ICON_PATH_RECORDS,
            "Аларми": ICON_PATH_ALARMS,
            # "Действия": ICON_PATH_ACTIONS, # Този ред е премахнат, както обсъдихме
            "Настройки": ICON_PATH_SETTINGS,
            "Изглед на живо": ICON_PATH_LIVE_VIEW,
        }

        for text, icon_path in button_data.items():
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoExclusive(True)  # Само един бутон може да е избран
            if os.path.exists(icon_path):
                button.setIcon(QIcon(icon_path))
                button.setIconSize(QSize(24, 24))
            button.clicked.connect(lambda checked, t=text: self.page_changed.emit(t))
            self.buttons[text] = button
            layout.addWidget(button)

        layout.addStretch(1)  # Разпъва останалото пространство

        # Изберете "Камери" по подразбиране
        self.buttons["Камери"].setChecked(True)


# Клас за страница "Камери"
class CamerasPage(QWidget):
    camera_selected = pyqtSignal(Camera)
    add_camera_requested = pyqtSignal()
    scan_network_requested = pyqtSignal()
    edit_zones_requested = pyqtSignal(Camera)

    def __init__(self, camera_manager, parent=None):
        super().__init__(parent)
        self.camera_manager = camera_manager
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PANEL_BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                font-size: 16px;
                color: {TEXT_COLOR};
            }}
            QLineEdit {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 8px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
            QTableWidget {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                gridline-color: {BORDER_COLOR};
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QTableWidget::item {{
                padding: 5px;
            }}
            QTableWidget::item:selected {{
                background-color: {ACCENT_COLOR};
                color: white;
            }}
            QHeaderView::section {{
                background-color: {SECONDARY_COLOR};
                color: {TEXT_COLOR};
                padding: 5px;
                border: 1px solid {BORDER_COLOR};
                font-weight: bold;
            }}
            .status-active {{
                color: #4CAF50; /* Зелено */
                font-weight: bold;
            }}
            .status-inactive {{
                color: #F44336; /* Червено */
                font-weight: bold;
            }}
            .status-button {{
                background-color: {SECONDARY_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QCheckBox {{
                color: {TEXT_COLOR};
            }}
            QSlider::groove:horizontal {{
                border: 1px solid {BORDER_COLOR};
                height: 8px;
                background: {FIELD_BG_COLOR};
                margin: 2px 0;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT_COLOR};
                border: 1px solid {ACCENT_COLOR};
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }}
            QComboBox {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QComboBox::drop-down {{
                border: 0px;
            }}
            QComboBox::down-arrow {{
                image: url(icons/arrow_down.png); /* Пример за икона на стрелка */
                width: 16px;
                height: 16px;
            }}
        """)
        self.init_ui()
        self.load_cameras_to_table()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Заглавие
        title_label = QLabel("Камери")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        main_layout.addWidget(title_label)

        # Търсене на камери
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Търсене на камери")
        self.search_input.setFixedHeight(40)
        self.search_input.textChanged.connect(self.filter_cameras)
        search_layout.addWidget(self.search_input)
        main_layout.addLayout(search_layout)

        # Списък с камери (TableWidget)
        main_layout.addWidget(QLabel("Списък с камери"))
        self.camera_table = QListWidget()  # Променено на QListWidget
        self.camera_table.setFixedHeight(250)
        self.camera_table.itemClicked.connect(self.on_camera_selected)
        main_layout.addWidget(self.camera_table)

        # Бутони за действия с камери
        camera_buttons_layout = QHBoxLayout()
        add_camera_button = QPushButton("Добави камера")
        add_camera_button.clicked.connect(self.add_camera_requested.emit)
        camera_buttons_layout.addWidget(add_camera_button)

        scan_network_button = QPushButton("Сканирай мрежата")
        scan_network_button.clicked.connect(self.scan_network_requested.emit)
        camera_buttons_layout.addWidget(scan_network_button)
        camera_buttons_layout.addStretch(1)
        main_layout.addLayout(camera_buttons_layout)

        # Настройки на камерата (движение, зони и т.н.)
        main_layout.addWidget(QLabel("Настройки"))

        # Детекция на движение
        motion_detection_layout = QHBoxLayout()
        motion_detection_layout.addWidget(QLabel("Детекция на движение"))
        motion_detection_label = QLabel("Включване/изключване на детекция на движение")
        motion_detection_layout.addWidget(motion_detection_label)
        self.motion_detection_toggle = QCheckBox()
        self.motion_detection_toggle.stateChanged.connect(self.update_camera_settings)
        motion_detection_layout.addWidget(self.motion_detection_toggle)
        motion_detection_layout.addStretch(1)
        main_layout.addLayout(motion_detection_layout)

        # Чувствителност на движение
        sensitivity_layout = QHBoxLayout()
        sensitivity_layout.addWidget(QLabel("Чувствителност на движение"))
        sensitivity_label = QLabel("Настройка на чувствителността на детекция на движение")
        sensitivity_layout.addWidget(sensitivity_label)
        self.sensitivity_combo = QComboBox()
        self.sensitivity_combo.addItems(["Ниска", "Средна", "Висока"])
        self.sensitivity_combo.currentIndexChanged.connect(self.update_camera_settings)
        sensitivity_layout.addWidget(self.sensitivity_combo)
        sensitivity_layout.addStretch(1)
        main_layout.addLayout(sensitivity_layout)

        # Зони за детекция
        zones_layout = QHBoxLayout()
        zones_layout.addWidget(QLabel("Зони за детекция"))
        zones_label = QLabel("Настройка на зони за детекция")
        zones_layout.addWidget(zones_label)
        self.edit_zones_button = QPushButton("Редактирай")
        self.edit_zones_button.clicked.connect(self.on_edit_zones_clicked)
        zones_layout.addWidget(self.edit_zones_button)
        zones_layout.addStretch(1)
        main_layout.addLayout(zones_layout)

        main_layout.addStretch(1)  # Разпъва останалото пространство

        self.selected_camera = None
        self.update_settings_ui_state(False)  # Деактивирайте настройките по подразбиране

    def load_cameras_to_table(self):
        self.camera_table.clear()
        for camera in self.camera_manager.get_all_cameras():
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 5, 5, 5)
            item_layout.setSpacing(10)

            name_label = QLabel(camera.name)
            name_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
            item_layout.addWidget(name_label)

            status_label = QLabel(camera.status)
            status_label.setStyleSheet(
                f"color: {'#4CAF50' if camera.status == 'Активна' else '#F44336'}; font-weight: bold;")
            item_layout.addWidget(status_label)

            ip_label = QLabel(camera.ip_address)
            item_layout.addWidget(ip_label)

            port_label = QLabel(str(camera.port))
            item_layout.addWidget(port_label)

            item_layout.addStretch(1)

            view_button = QPushButton("Преглед")
            view_button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {SECONDARY_COLOR};
                    border: 1px solid {BORDER_COLOR};
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-size: 12px;
                    color: {TEXT_COLOR};
                }}
                QPushButton:hover {{
                    background-color: {HOVER_COLOR};
                }}
            """)
            view_button.clicked.connect(lambda _, c=camera: self.camera_selected.emit(c))
            item_layout.addWidget(view_button)

            list_item = QListWidgetItem(self.camera_table)
            list_item.setSizeHint(item_widget.sizeHint())
            self.camera_table.addItem(list_item)
            self.camera_table.setItemWidget(list_item, item_widget)
            list_item.setData(Qt.UserRole, camera.name)  # Съхраняваме името на камерата

    def filter_cameras(self, text):
        search_text = text.lower()
        for i in range(self.camera_table.count()):
            item = self.camera_table.item(i)
            camera_name = item.data(Qt.UserRole)
            camera = self.camera_manager.get_camera(camera_name)
            if camera and search_text in camera.name.lower():
                item.setHidden(False)
            else:
                item.setHidden(True)

    def on_camera_selected(self, item):
        camera_name = item.data(Qt.UserRole)
        self.selected_camera = self.camera_manager.get_camera(camera_name)
        if self.selected_camera:
            self.update_settings_ui_state(True)
            self.motion_detection_toggle.setChecked(self.selected_camera.motion_detection_enabled)
            self.sensitivity_combo.setCurrentText(self.selected_camera.motion_sensitivity)
        else:
            self.update_settings_ui_state(False)

    def update_settings_ui_state(self, enabled):
        self.motion_detection_toggle.setEnabled(enabled)
        self.sensitivity_combo.setEnabled(enabled)
        self.edit_zones_button.setEnabled(enabled)

    def update_camera_settings(self):
        if self.selected_camera:
            self.selected_camera.motion_detection_enabled = self.motion_detection_toggle.isChecked()
            self.selected_camera.motion_sensitivity = self.sensitivity_combo.currentText()
            self.camera_manager.update_camera(self.selected_camera)
            # Може да добавите QMesageBox.information за потвърждение или да актуализирате UI по друг начин
            # print(f"Настройките за {self.selected_camera.name} са актуализирани.")

    def on_edit_zones_clicked(self):
        if self.selected_camera:
            self.edit_zones_requested.emit(self.selected_camera)


# Клас за страница "Записи"
class RecordsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PANEL_BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                font-size: 16px;
                color: {TEXT_COLOR};
            }}
            QTreeView {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QHeaderView::section {{
                background-color: {SECONDARY_COLOR};
                color: {TEXT_COLOR};
                padding: 5px;
                border: 1px solid {BORDER_COLOR};
                font-weight: bold;
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
        """)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        title_label = QLabel("Записи")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        layout.addWidget(title_label)

        # Модел за файлова система
        self.model = QFileSystemModel()
        self.model.setRootPath(RECORDINGS_DIR)
        self.model.setFilter(QDir.Files | QDir.NoDotAndDotDot | QDir.Dirs)  # Показва файлове и директории

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(RECORDINGS_DIR))
        self.tree.setColumnHidden(1, True)  # Скрива колона "Размер"
        self.tree.setColumnHidden(2, True)  # Скрива колона "Тип"
        self.tree.setColumnHidden(3, True)  # Скрива колона "Дата на модификация"
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.doubleClicked.connect(self.open_selected_file)  # Отвори файл при двоен клик
        layout.addWidget(self.tree)

        # Бутони за управление на записи
        button_layout = QHBoxLayout()
        play_button = QPushButton("Възпроизведи")
        play_button.clicked.connect(self.play_selected_record)
        button_layout.addWidget(play_button)

        delete_button = QPushButton("Изтрий")
        delete_button.clicked.connect(self.delete_selected_record)
        button_layout.addWidget(delete_button)
        button_layout.addStretch(1)
        layout.addLayout(button_layout)

        layout.addStretch(1)

    def play_selected_record(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            QMessageBox.warning(self, "Възпроизвеждане", "Моля, изберете запис за възпроизвеждане.")
            return

        file_path = self.model.filePath(index)
        if not os.path.isfile(file_path):
            QMessageBox.warning(self, "Възпроизвеждане", "Моля, изберете файл, а не папка.")
            return

        # Тук трябва да интегрирате видео плейър
        QMessageBox.information(self, "Възпроизвеждане", f"Възпроизвеждане на: {os.path.basename(file_path)}")
        # Пример: Можете да използвате cv2.VideoCapture за възпроизвеждане
        # cap = cv2.VideoCapture(file_path)
        # while cap.isOpened():
        #     ret, frame = cap.read()
        #     if not ret:
        #         break
        #     cv2.imshow('Recording Playback', frame)
        #     if cv2.waitKey(25) & 0xFF == ord('q'):
        #         break
        # cap.release()
        # cv2.destroyAllWindows()

    def delete_selected_record(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            QMessageBox.warning(self, "Изтриване", "Моля, изберете запис или папка за изтриване.")
            return

        file_path = self.model.filePath(index)

        reply = QMessageBox.question(self, "Потвърждение за изтриване",
                                     f"Сигурни ли сте, че искате да изтриете '{os.path.basename(file_path)}'?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    QMessageBox.information(self, "Изтриване", "Файлът е изтрит успешно.")
                elif os.path.isdir(file_path):
                    # Изтриване на директория и цялото й съдържание
                    import shutil
                    shutil.rmtree(file_path)
                    QMessageBox.information(self, "Изтриване", "Папката и съдържанието й са изтрити успешно.")
                self.model.layoutChanged.emit()  # Опресняване на изгледа
            except Exception as e:
                QMessageBox.critical(self, "Грешка при изтриване", f"Неуспешно изтриване: {e}")

    def open_selected_file(self, index):
        file_path = self.model.filePath(index)
        if os.path.isfile(file_path):
            self.play_selected_record()  # Извикваме функцията за възпроизвеждане


# Клас за страница "Аларми"
class AlarmsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PANEL_BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                font-size: 16px;
                color: {TEXT_COLOR};
            }}
            QComboBox {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QComboBox::drop-down {{
                border: 0px;
            }}
            QComboBox::down-arrow {{
                image: url(icons/arrow_down.png);
                width: 16px;
                height: 16px;
            }}
        """)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        title_label = QLabel("Аларми и действия")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        layout.addWidget(title_label)

        # Филтър за аларми (примерно)
        filter_layout = QHBoxLayout()
        self.alarm_filter_combo = QComboBox()
        self.alarm_filter_combo.addItems(["Всички аларми", "Движение", "Звук", "Изгубена връзка"])
        filter_layout.addWidget(self.alarm_filter_combo)
        filter_layout.addStretch(1)
        layout.addLayout(filter_layout)

        # Списък с аларми
        self.alarm_list_widget = QListWidget()
        self.alarm_list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QListWidget::item {{
                padding: 5px;
            }}
            QListWidget::item:selected {{
                background-color: {ACCENT_COLOR};
                color: white;
            }}
        """)
        layout.addWidget(self.alarm_list_widget)

        # Примерни аларми (за тестване)
        self.load_sample_alarms()

        layout.addStretch(1)

    def load_sample_alarms(self):
        self.alarm_list_widget.clear()
        # Тук бихте заредили реални аларми от база данни или файл
        sample_alarms = [
            "2023-10-26 10:30:00 - Движение засечено (Камера 1)",
            "2023-10-26 09:15:20 - Изгубена връзка (Камера 3)",
            "2023-10-25 18:05:10 - Звук засечен (Камера 2)",
            "2023-10-25 12:00:00 - Движение засечено (Камера 1)",
        ]
        if not sample_alarms:
            self.alarm_list_widget.addItem("Няма аларми за показване.")
        else:
            for alarm in sample_alarms:
                self.alarm_list_widget.addItem(alarm)


# Клас за страница "Настройки"
class SettingsPage(QWidget):
    def __init__(self, current_username, is_admin, parent=None):
        super().__init__(parent)
        self.current_username = current_username
        self.is_admin = is_admin
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PANEL_BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                font-size: 16px;
                color: {TEXT_COLOR};
            }}
            .setting-label {{
                font-size: 18px;
                font-weight: bold;
                margin-top: 15px;
                margin-bottom: 5px;
                color: {ACCENT_COLOR};
            }}
            .sub-label {{
                font-size: 14px;
                color: {TEXT_COLOR};
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
            QComboBox {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QComboBox::drop-down {{
                border: 0px;
            }}
            QComboBox::down-arrow {{
                image: url(icons/arrow_down.png);
                width: 16px;
                height: 16px;
            }}
            QCheckBox {{
                color: {TEXT_COLOR};
            }}
        """)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title_label = QLabel("Настройки")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        layout.addWidget(title_label)

        # --- Настройки за записи ---
        layout.addWidget(QLabel("Записи", objectName="setting-label"))

        # Продължителност на записите
        record_duration_layout = QHBoxLayout()
        record_duration_layout.addWidget(QLabel("Продължителност на записите"))
        record_duration_sublabel = QLabel("Задайте продължителността на записите")
        record_duration_layout.addWidget(record_duration_sublabel)
        self.record_duration_combo = QComboBox()
        self.record_duration_combo.addItems(["15 минути", "30 минути", "1 час", "2 часа", "Неограничено"])
        self.record_duration_combo.setCurrentText("1 час")  # Примерна стойност
        record_duration_layout.addWidget(self.record_duration_combo)
        record_duration_layout.addStretch(1)
        layout.addLayout(record_duration_layout)

        # Качество на записите
        record_quality_layout = QHBoxLayout()
        record_quality_layout.addWidget(QLabel("Качество на записите"))
        record_quality_sublabel = QLabel("Изберете качеството на записите")
        record_quality_layout.addWidget(record_quality_sublabel)
        self.record_quality_combo = QComboBox()
        self.record_quality_combo.addItems(["Ниско", "Средно", "Високо", "Full HD"])
        self.record_quality_combo.setCurrentText("Високо")  # Примерна стойност
        record_quality_layout.addWidget(self.record_quality_combo)
        record_quality_layout.addStretch(1)
        layout.addLayout(record_quality_layout)

        # --- Потребители ---
        layout.addWidget(QLabel("Потребители", objectName="setting-label"))
        user_management_layout = QHBoxLayout()
        user_management_layout.addWidget(QLabel("Управление на потребители"))
        user_management_sublabel = QLabel("Управление на потребителски профили")
        user_management_layout.addWidget(user_management_sublabel)
        self.manage_users_button = QPushButton(">")  # Стрелка
        self.manage_users_button.setFixedSize(30, 30)  # Малък бутон
        self.manage_users_button.clicked.connect(self.open_user_management)
        user_management_layout.addWidget(self.manage_users_button)
        user_management_layout.addStretch(1)
        layout.addLayout(user_management_layout)

        # Деактивирайте бутона, ако потребителят не е администратор
        if not self.is_admin:
            self.manage_users_button.setEnabled(False)
            self.manage_users_button.setToolTip("Само администратори могат да управляват потребители.")

        # --- Известия ---
        layout.addWidget(QLabel("Известия", objectName="setting-label"))

        # Известия при движение
        motion_notification_layout = QHBoxLayout()
        motion_notification_layout.addWidget(QLabel("Известия при движение"))
        motion_notification_sublabel = QLabel("Настройки за известия при движение")
        motion_notification_layout.addWidget(motion_notification_sublabel)
        self.motion_notification_toggle = QCheckBox()
        self.motion_notification_toggle.setChecked(True)  # Примерна стойност
        motion_notification_layout.addWidget(self.motion_notification_toggle)
        motion_notification_layout.addStretch(1)
        layout.addLayout(motion_notification_layout)

        # --- Други ---
        layout.addWidget(QLabel("Други", objectName="setting-label"))

        # Съхранение на записи
        storage_layout = QHBoxLayout()
        storage_layout.addWidget(QLabel("Съхранение на записи"))
        storage_sublabel = QLabel("Настройки за съхранение на записи")
        storage_layout.addWidget(storage_sublabel)
        self.storage_location_label = QLabel("Локално")  # Примерна стойност
        storage_layout.addWidget(self.storage_location_label)
        storage_layout.addStretch(1)
        layout.addLayout(storage_layout)

        # Информация за софтуера
        about_layout = QHBoxLayout()
        about_layout.addWidget(QLabel("Информация за софтуера"))
        about_sublabel = QLabel("Информация за версията на софтуера")
        about_layout.addWidget(about_sublabel)
        self.version_label = QLabel("v1.2.3")  # Примерна стойност
        about_layout.addWidget(self.version_label)
        about_layout.addStretch(1)
        layout.addLayout(about_layout)

        layout.addStretch(1)

    def open_user_management(self):
        dialog = UserManagementDialog(current_username=self.current_username, parent=self)
        dialog.user_updated.connect(self.on_user_manager_update)  # Свързваме сигнала
        dialog.exec_()

    def on_user_manager_update(self):
        # Тази функция може да се използва за опресняване на UI, ако е необходимо,
        # например ако текущият потребител е променил правата си
        print("Потребителските данни са актуализирани.")
        # Ако текущият потребител промени правата си, може да се наложи рестарт или презареждане
        # на главния прозорец, за да се отразят промените в достъпа до менюта.
        # Засега просто ще презаредим страницата с настройки, за да се актуализира бутона.
        self.update_manage_users_button_state()

    def update_manage_users_button_state(self):
        # Опреснява състоянието на бутона "Управление на потребители"
        user_manager = UserManager()
        current_user_obj = user_manager.get_user(self.current_username)
        if current_user_obj and current_user_obj.is_admin:
            self.manage_users_button.setEnabled(True)
            self.manage_users_button.setToolTip("")
        else:
            self.manage_users_button.setEnabled(False)
            self.manage_users_button.setToolTip("Само администратори могат да управляват потребители.")


# Клас за страница "Изглед на живо"
class LiveViewPage(QWidget):
    def __init__(self, camera_manager, parent=None):  # ДОБАВЕН camera_manager аргумент
        super().__init__(parent)
        self.camera_manager = camera_manager  # Съхраняваме camera_manager
        self.current_camera = None  # За да следим коя камера е избрана и стриймва
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {PANEL_BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                font-size: 16px;
                color: {TEXT_COLOR};
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
            .control-button {{
                background-color: {SECONDARY_COLOR};
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-size: 14px;
                color: {TEXT_COLOR};
            }}
            .control-button:hover {{
                background-color: {HOVER_COLOR};
            }}
            .control-button:checked {{
                background-color: {ACCENT_COLOR};
            }}
            QComboBox {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 5px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QComboBox::drop-down {{
                border: 0px;
            }}
            QComboBox::down-arrow {{
                image: url(icons/arrow_down.png);
                width: 16px;
                height: 16px;
            }}
        """)
        self.init_ui()
        self.timer = QTimer(self)  # Таймер за опресняване на видеото
        self.timer.timeout.connect(self.update_video_frame)
        self.timer.start(30)  # Опресняване на всеки 30 ms (около 33 FPS)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        title_label = QLabel("Live View")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        layout.addWidget(title_label)

        # Избор на камера
        camera_selection_layout = QHBoxLayout()
        self.camera_combo = QComboBox()
        self.camera_combo.setPlaceholderText("Изберете камера")
        self.camera_combo.currentIndexChanged.connect(self.on_camera_combo_selected)  # Свързваме сигнала
        camera_selection_layout.addWidget(self.camera_combo)
        camera_selection_layout.addStretch(1)
        layout.addLayout(camera_selection_layout)

        # Избор на изглед (Single/Multi)
        view_mode_layout = QHBoxLayout()
        self.single_view_button = QPushButton("Single View")
        self.single_view_button.setCheckable(True)
        self.single_view_button.setChecked(True)
        self.single_view_button.setStyleSheet("QPushButton.control-button")  # Прилагане на стил
        self.multi_view_button = QPushButton("Multi View")
        self.multi_view_button.setCheckable(True)
        self.multi_view_button.setStyleSheet("QPushButton.control-button")  # Прилагане на стил

        # Групиране на бутоните, за да може само един да е активен
        self.view_mode_group = QWidget()
        view_mode_group_layout = QHBoxLayout(self.view_mode_group)
        view_mode_group_layout.setContentsMargins(0, 0, 0, 0)
        view_mode_group_layout.addWidget(self.single_view_button)
        view_mode_group_layout.addWidget(self.multi_view_button)
        view_mode_group_layout.addStretch(1)

        view_mode_layout.addWidget(self.view_mode_group)
        view_mode_layout.addStretch(1)
        layout.addLayout(view_mode_layout)

        # Видео дисплей
        self.video_display_label = QLabel()
        self.video_display_label.setAlignment(Qt.AlignCenter)
        self.video_display_label.setStyleSheet(f"background-color: #000000; border: 1px solid {BORDER_COLOR};")
        self.video_display_label.setText("Изберете камера за преглед")  # Показва съобщение по подразбиране

        layout.addWidget(self.video_display_label)

        # Контроли за видеото (остават същите)
        video_controls_layout = QHBoxLayout()
        video_controls_layout.addStretch(1)

        rewind_button = QPushButton()
        rewind_button.setIcon(QIcon(ICON_PATH_REWIND))
        rewind_button.setIconSize(QSize(32, 32))
        rewind_button.setFixedSize(40, 40)
        rewind_button.setStyleSheet(
            "QPushButton { border: none; background-color: transparent; } QPushButton:hover { background-color: #555555; border-radius: 20px; }")
        video_controls_layout.addWidget(rewind_button)

        forward_button = QPushButton()
        forward_button.setIcon(QIcon(ICON_PATH_FORWARD))
        forward_button.setIconSize(QSize(32, 32))
        forward_button.setFixedSize(40, 40)
        forward_button.setStyleSheet(
            "QPushButton { border: none; background-color: transparent; } QPushButton:hover { background-color: #555555; border-radius: 20px; }")
        video_controls_layout.addWidget(forward_button)

        zoom_in_button = QPushButton()
        zoom_in_button.setIcon(QIcon(ICON_PATH_ZOOM_IN))
        zoom_in_button.setIconSize(QSize(32, 32))
        zoom_in_button.setFixedSize(40, 40)
        zoom_in_button.setStyleSheet(
            "QPushButton { border: none; background-color: transparent; } QPushButton:hover { background-color: #555555; border-radius: 20px; }")
        video_controls_layout.addWidget(zoom_in_button)

        zoom_out_button = QPushButton()
        zoom_out_button.setIcon(QIcon(ICON_PATH_ZOOM_OUT))
        zoom_out_button.setIconSize(QSize(32, 32))
        zoom_out_button.setFixedSize(40, 40)
        zoom_out_button.setStyleSheet(
            "QPushButton { border: none; background-color: transparent; } QPushButton:hover { background-color: #555555; border-radius: 20px; } }")
        video_controls_layout.addWidget(zoom_out_button)

        video_controls_layout.addStretch(1)

        snapshot_button = QPushButton("Snapshot")
        if os.path.exists(ICON_PATH_SNAPSHOT):
            snapshot_button.setIcon(QIcon(ICON_PATH_SNAPSHOT))
            snapshot_button.setIconSize(QSize(24, 24))
        snapshot_button.clicked.connect(self.take_snapshot)
        video_controls_layout.addWidget(snapshot_button)

        layout.addLayout(video_controls_layout)
        layout.addStretch(1)

    def load_cameras_to_combo(self):  # НОВ МЕТОД: Зарежда камери в ComboBox
        self.camera_combo.clear()
        self.camera_combo.addItem("Изберете камера")  # Първа опция
        for camera in self.camera_manager.get_all_cameras():
            self.camera_combo.addItem(camera.name)

    def on_camera_combo_selected(self, index):  # НОВ МЕТОД: Когато се избере камера от ComboBox
        if index == 0:  # Избрана е опцията "Изберете камера"
            self.stop_current_stream()
            self.video_display_label.setText("Изберете камера за преглед")
            return

        camera_name = self.camera_combo.currentText()
        selected_camera_obj = self.camera_manager.get_camera(camera_name)

        if selected_camera_obj:
            self.stop_current_stream()  # Спира текущия стрийм, ако има такъв
            self.current_camera = selected_camera_obj
            self.current_camera.start_stream()
            print(f"[{self.current_camera.name}] Attempted to start stream.")
            self.video_display_label.setText(f"Свързване към {self.current_camera.name}...")
        else:
            self.video_display_label.setText("Камерата не е намерена.")
            self.stop_current_stream()

    def stop_current_stream(self):  # НОВ МЕТОД: Спира текущия стрийм
        if self.current_camera:
            self.current_camera.stop_stream()
            print(f"[{self.current_camera.name}] Stream stopped by UI.")
            self.current_camera = None

    def update_video_frame(self):  # НОВ МЕТОД: Опреснява видео кадъра
        if self.current_camera and self.current_camera._is_streaming:
            frame = self.current_camera.get_frame()
            if frame is not None:
                # Конвертиране на OpenCV кадър към QPixmap
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)
                pixmap = QPixmap.fromImage(qt_image)

                # Мащабиране на изображението, за да пасне на QLabel
                scaled_pixmap = pixmap.scaled(self.video_display_label.size(), Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation)
                self.video_display_label.setPixmap(scaled_pixmap)

                # Ако има движение, актуализирайте статуса на камерата на "Активна"
                if self.current_camera.status == "Неактивна":
                    self.current_camera.status = "Активна"
                    self.camera_manager.update_camera(self.current_camera)
                    # Може да се наложи да излъчите сигнал, за да обновите CamerasPage
                    # self.cameras_page.load_cameras_to_table() # Това би забавило, ако се вика често
                    print(f"[{self.current_camera.name}] Status updated to Active.")
            else:
                self.video_display_label.setText(f"Няма сигнал от {self.current_camera.name}")
                self.current_camera.status = "Неактивна"  # Ако няма кадри, маркираме като неактивна
                # self.camera_manager.update_camera(self.current_camera) # Избягвайте чести записвания
        else:
            self.video_display_label.setText("Изберете камера за преглед")
            # Уверете се, че няма остатъчни пиксели от предишен кадър
            # self.video_display_label.clear() # Може да е прекалено агресивно

    def take_snapshot(self):
        if self.current_camera and self.current_camera._latest_frame is not None:
            camera_recordings_dir = Path(RECORDINGS_DIR) / self.current_camera.name
            camera_recordings_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = camera_recordings_dir / f"snapshot_{timestamp}.jpg"
            cv2.imwrite(str(filename), self.current_camera._latest_frame)
            QMessageBox.information(self, "Моментна снимка", f"Моментна снимка е запазена: {filename.name}")
        else:
            QMessageBox.warning(self, "Моментна снимка", "Няма активен видео поток за моментна снимка.")


# Клас за диалог за добавяне/редактиране на камера
class CameraDialog(QDialog):
    def __init__(self, camera=None, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.setWindowTitle("Добавяне/Редактиране на камера")
        self.setFixedSize(400, 300)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QLineEdit {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 8px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
        """)
        self.init_ui()
        if self.camera:
            self.load_camera_data()

    def init_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Име на камерата")
        form_layout.addRow("Име:", self.name_input)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP адрес (напр. 192.168.1.100)")
        form_layout.addRow("IP Адрес:", self.ip_input)

        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("Порт (напр. 8080)")
        self.port_input.setValidator(QIntValidator(1, 65535, self))  # Валидатор за порт
        form_layout.addRow("Порт:", self.port_input)

        self.rtsp_url_input = QLineEdit()  # НОВО: Поле за RTSP URL
        self.rtsp_url_input.setPlaceholderText("RTSP URL (напр. rtsp://192.168.1.100:554/stream1)")
        form_layout.addRow("RTSP URL:", self.rtsp_url_input)

        layout.addLayout(form_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def load_camera_data(self):
        self.name_input.setText(self.camera.name)
        self.ip_input.setText(self.camera.ip_address)
        self.port_input.setText(str(self.camera.port))
        self.rtsp_url_input.setText(self.camera.rtsp_url)  # НОВО: Зареждане на RTSP URL
        self.name_input.setEnabled(False)  # Не позволяваме промяна на името при редакция

    def get_camera_data(self):
        name = self.name_input.text().strip()
        ip_address = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        rtsp_url = self.rtsp_url_input.text().strip()  # НОВО: Взимане на RTSP URL

        if not name or not ip_address or not port:
            QMessageBox.warning(self, "Грешка", "Моля, попълнете всички задължителни полета (Име, IP, Порт).")
            return None

        try:
            port = int(port)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Грешка", "Невалиден номер на порт. Моля, въведете число между 1 и 65535.")
            return None

        # Проверка за валиден IP адрес (опростена)
        try:
            import ipaddress
            ipaddress.ip_address(ip_address)
        except ValueError:
            QMessageBox.warning(self, "Грешка", "Невалиден IP адрес.")
            return None

        # Опционална проверка за RTSP URL
        if rtsp_url and not (
                rtsp_url.startswith("rtsp://") or rtsp_url.startswith("http://") or rtsp_url.startswith("https://")):
            QMessageBox.warning(self, "Грешка", "RTSP URL трябва да започва с 'rtsp://', 'http://' или 'https://'.")
            return None

        if self.camera:  # Редакция
            self.camera.ip_address = ip_address
            self.camera.port = port
            self.camera.rtsp_url = rtsp_url  # НОВО: Запазване на RTSP URL
            return self.camera
        else:  # Добавяне
            return Camera(name, ip_address, port, rtsp_url=rtsp_url)  # НОВО: Подаване на RTSP URL


# Клас за диалог за редактиране на зони за детекция
class DetectionZoneDialog(QDialog):
    def __init__(self, camera, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.setWindowTitle(f"Редактиране на зони за детекция за {camera.name}")
        self.setGeometry(100, 100, 800, 600)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {TEXT_COLOR};
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_PRESSED_COLOR};
            }}
        """)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("border: 2px solid white; background-color: black;")

        # Зареждане на примерно изображение или видео кадър
        if os.path.exists("icons/camera_placeholder.png"):
            self.original_pixmap = QPixmap("icons/camera_placeholder.png")
        else:
            # Създайте празно изображение, ако няма placeholder
            img = QImage(640, 480, QImage.Format_RGB32)
            img.fill(Qt.black)
            self.original_pixmap = QPixmap.fromImage(img)

        self.current_pixmap = self.original_pixmap.copy()
        self.image_label.setPixmap(self.current_pixmap)

        self.drawing = False
        self.start_point = QPoint()
        self.end_point = QPoint()

        self.image_label.mousePressEvent = self.mouse_press
        self.image_label.mouseMoveEvent = self.mouse_move
        self.image_label.mouseReleaseEvent = self.mouse_release

        layout = QVBoxLayout(self)
        layout.addWidget(self.image_label)

        button_layout = QHBoxLayout()
        add_zone_button = QPushButton("Добави зона")
        add_zone_button.clicked.connect(self.add_zone)
        button_layout.addWidget(add_zone_button)

        clear_zones_button = QPushButton("Изчисти всички зони")
        clear_zones_button.clicked.connect(self.clear_all_zones)
        button_layout.addWidget(clear_zones_button)

        button_layout.addStretch(1)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_layout.addWidget(ok_button)

        cancel_button = QPushButton("Отказ")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        self.draw_existing_zones()

    def draw_existing_zones(self):
        self.current_pixmap = self.original_pixmap.copy()
        painter = QPainter(self.current_pixmap)
        painter.setPen(QPen(Qt.red, 2, Qt.SolidLine))  # Червена рамка
        painter.setBrush(Qt.NoBrush)

        for zone in self.camera.detection_zones:
            painter.drawRect(zone)

        painter.end()
        self.image_label.setPixmap(self.current_pixmap)

    def mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = True
            self.start_point = event.pos()
            self.end_point = event.pos()

    def mouse_move(self, event):
        if self.drawing:
            self.end_point = event.pos()
            self.draw_current_rectangle()

    def mouse_release(self, event):
        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            rect = QRect(self.start_point, self.end_point).normalized()
            if rect.width() > 0 and rect.height() > 0:
                self.camera.detection_zones.append(rect)
                self.draw_existing_zones()  # Прерисува всички зони, включително новата

    def draw_current_rectangle(self):
        temp_pixmap = self.original_pixmap.copy()
        painter = QPainter(temp_pixmap)
        painter.setPen(QPen(Qt.blue, 2, Qt.DotLine))  # Синя пунктирана линия за текущото рисуване
        painter.setBrush(Qt.NoBrush)

        # Рисуване на съществуващите зони
        painter.setPen(QPen(Qt.red, 2, Qt.SolidLine))
        for zone in self.camera.detection_zones:
            painter.drawRect(zone)

        # Рисуване на текущата зона
        painter.setPen(QPen(Qt.blue, 2, Qt.DotLine))
        rect = QRect(self.start_point, self.end_point).normalized()
        painter.drawRect(rect)

        painter.end()
        self.image_label.setPixmap(temp_pixmap)

    def add_zone(self):
        # Зоната вече е добавена при mouseRelease, този бутон може да се използва за
        # ръчно въвеждане на координати или за потвърждение, ако има сложна логика
        QMessageBox.information(self, "Добавяне на зона", "Моля, маркирайте зоната с мишката.")

    def clear_all_zones(self):
        reply = QMessageBox.question(self, "Изчисти зони",
                                     "Сигурни ли сте, че искате да изчистите всички зони за детекция?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.camera.detection_zones.clear()
            self.draw_existing_zones()  # Прерисува без зони


# Основен прозорец на приложението
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tsa-Security")
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {PRIMARY_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QMenuBar {{
                background-color: {PRIMARY_COLOR};
                color: {TEXT_COLOR};
            }}
            QMenuBar::item {{
                background-color: {PRIMARY_COLOR};
                color: {TEXT_COLOR};
                padding: 5px 10px;
            }}
            QMenuBar::item:selected {{
                background-color: {ACCENT_COLOR};
            }}
            QMenu {{
                background-color: {SECONDARY_COLOR};
                border: 1px solid {BORDER_COLOR};
            }}
            QMenu::item {{
                color: {TEXT_COLOR};
                padding: 5px 20px;
            }}
            QMenu::item:selected {{
                background-color: {ACCENT_COLOR};
            }}
            #headerWidget {{
                background-color: {SECONDARY_COLOR};
                border-bottom: 1px solid {BORDER_COLOR};
            }}
            #headerLabel {{
                font-size: 20px;
                font-weight: bold;
                color: {TEXT_COLOR};
            }}
            #userButton, #notificationButton {{
                background-color: {ACCENT_COLOR};
                border: none;
                border-radius: 18px; /* За да стане кръгъл */
                width: 36px;
                height: 36px;
            }}
            #userButton:hover, #notificationButton:hover {{
                background-color: {BUTTON_HOVER_COLOR};
            }}
        """)

        self.camera_manager = CameraManager()
        self.user_manager = UserManager()
        self.current_username = None
        self.is_admin = False

        self.check_login()

    def check_login(self):
        login_dialog = LoginDialog(self)
        if login_dialog.exec_() == QDialog.Accepted:
            self.current_username = login_dialog.username
            self.is_admin = login_dialog.is_admin
            self.init_ui()
        else:
            sys.exit(0)  # Изход от приложението, ако входът е неуспешен или отменен

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Странично меню
        self.side_menu = SideMenu()
        self.side_menu.page_changed.connect(self.change_page)
        main_layout.addWidget(self.side_menu, 1)  # 1/5 от ширината

        # Основно съдържание
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Хедър
        header_widget = QWidget()
        header_widget.setObjectName("headerWidget")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(15)

        self.header_label = QLabel("Начало")
        self.header_label.setObjectName("headerLabel")
        header_layout.addWidget(self.header_label)
        header_layout.addStretch(1)

        # Бутон за известия (премахнат, както обсъдихме)
        # notification_button = QPushButton()
        # notification_button.setObjectName("notificationButton")
        # notification_button.setIcon(QIcon(ICON_PATH_BELL))
        # notification_button.setIconSize(QSize(24, 24))
        # notification_button.setToolTip("Известия")
        # header_layout.addWidget(notification_button)

        # Бутон за потребителски профил (премахнат, както обсъдихме)
        # user_button = QPushButton()
        # user_button.setObjectName("userButton")
        # user_button.setIcon(QIcon(ICON_PATH_USER))
        # user_button.setIconSize(QSize(24, 24))
        # user_button.setToolTip(f"Влязъл като: {self.current_username} ({'Админ' if self.is_admin else 'Потребител'})")
        # user_button.clicked.connect(self.show_user_context_menu) # show_user_context_menu също може да се премахне
        # header_layout.addWidget(user_button)

        content_layout.addWidget(header_widget)

        # Stacked Widget за страниците
        self.pages_widget = QStackedWidget()
        content_layout.addWidget(self.pages_widget)

        self.cameras_page = CamerasPage(self.camera_manager)
        self.cameras_page.add_camera_requested.connect(self.open_add_camera_dialog)
        self.cameras_page.scan_network_requested.connect(self.scan_network)
        self.cameras_page.camera_selected.connect(self.show_live_view_for_camera)
        self.cameras_page.edit_zones_requested.connect(self.open_detection_zone_dialog)
        self.pages_widget.addWidget(self.cameras_page)

        self.records_page = RecordsPage()
        self.pages_widget.addWidget(self.records_page)

        self.alarms_page = AlarmsPage()
        self.pages_widget.addWidget(self.alarms_page)

        # Действия - може да е същата като аларми или отделна
        self.actions_page = QWidget()  # Примерна празна страница
        self.actions_page_layout = QVBoxLayout(self.actions_page)
        self.actions_page_layout.addWidget(QLabel("Страница за действия"))
        self.pages_widget.addWidget(self.actions_page)

        self.settings_page = SettingsPage(self.current_username, self.is_admin)
        self.pages_widget.addWidget(self.settings_page)

        self.live_view_page = LiveViewPage()
        self.pages_widget.addWidget(self.live_view_page)

        main_layout.addLayout(content_layout, 4)  # 4/5 от ширината

        self.change_page("Камери")  # Показване на начална страница

    def change_page(self, page_name):
        self.header_label.setText(page_name)
        if page_name == "Камери":
            self.pages_widget.setCurrentWidget(self.cameras_page)
            self.cameras_page.load_cameras_to_table()  # Опресняване на списъка с камери
        elif page_name == "Записи":
            self.pages_widget.setCurrentWidget(self.records_page)
        elif page_name == "Аларми":
            self.pages_widget.setCurrentWidget(self.alarms_page)
        elif page_name == "Действия":  # Въпреки че бутонът е премахнат, страницата все още съществува
            self.pages_widget.setCurrentWidget(self.actions_page)
        elif page_name == "Настройки":
            self.pages_widget.setCurrentWidget(self.settings_page)
            # Опресняване на състоянието на бутона за потребители
            self.settings_page.update_manage_users_button_state()
        elif page_name == "Изглед на живо":
            self.pages_widget.setCurrentWidget(self.live_view_page)

    def open_add_camera_dialog(self):
        dialog = CameraDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            new_camera = dialog.get_camera_data()
            if new_camera:
                success, message = self.camera_manager.add_camera(new_camera)
                if success:
                    QMessageBox.information(self, "Успех", message)
                    self.cameras_page.load_cameras_to_table()
                else:
                    QMessageBox.critical(self, "Грешка", message)

    def scan_network(self):
        # Взимане на IP адреса на текущия компютър, за да определим подмрежата
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # Свързва се с външен адрес, за да получи локалния IP
            local_ip = s.getsockname()[0]
            s.close()
            # Предполагаме /24 подмрежа (напр. 192.168.1.0/24)
            subnet = IPv4Network(f"{local_ip}/24", strict=False)
        except Exception as e:
            QMessageBox.critical(self, "Грешка при сканиране", f"Не може да се определи локалната подмрежа: {e}")
            return

        self.progress_dialog = QProgressDialog("Сканиране на вашата мрежа за камери...", "Отмяна", 0, 100, self)
        self.progress_dialog.setWindowTitle("Сканиране на мрежата")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(True)  # Автоматично затваряне при 100%

        # Създаване на нишка за скенера
        self.scanner_thread = QThread()
        self.scanner = NetworkScanner(subnet)
        self.scanner.moveToThread(self.scanner_thread)

        # Свързване на сигнали
        self.progress_dialog.canceled.connect(self.scanner.cancel)
        self.scanner.scan_progress.connect(self.progress_dialog.setValue)
        self.scanner.camera_found.connect(self.add_scanned_camera)
        self.scanner.scan_finished.connect(self.on_scan_finished)

        # Стартиране на нишката
        self.scanner_thread.started.connect(self.scanner.run)

        self.progress_dialog.show()
        self.scanner_thread.start()

    def add_scanned_camera(self, ip_address):
        # Проверява дали камера с този IP адрес вече е добавена
        existing_camera = next((c for c in self.camera_manager.get_all_cameras() if c.ip_address == ip_address), None)
        if existing_camera:
            print(f"Камера с IP {ip_address} вече съществува. Пропускане.")
            return

        # Добавяне на намерена камера
        camera_name = f"Камера_{ip_address.replace('.', '_')}"
        # RTSP URL е примерна, може да се наложи ръчна корекция от потребителя
        rtsp_url = f"rtsp://{ip_address}:554/stream1"
        new_camera = Camera(name=camera_name, ip_address=ip_address, port=554, status="Неактивна", rtsp_url=rtsp_url)

        success, message = self.camera_manager.add_camera(new_camera)
        if success:
            QMessageBox.information(self, "Камера намерена",
                                    f"Намерена нова камера: {camera_name} ({ip_address}). Добавена е към списъка.")
            self.cameras_page.load_cameras_to_table()  # Опресняване на списъка в UI
        else:
            print(f"Грешка при добавяне на сканирана камера {ip_address}: {message}")

    def on_scan_finished(self, message):
        self.progress_dialog.close()
        QMessageBox.information(self, "Сканиране на мрежата", message)

        # Почистване на нишката
        if self.scanner_thread:
            self.scanner_thread.quit()
            self.scanner_thread.wait()
        self.scanner_thread = None
        self.scanner = None

    # Методът show_user_context_menu вече не е свързан с бутон, така че може да бъде премахнат,
    # ако не се използва другаде.
    # def show_user_context_menu(self):
    #     menu = QMenu(self)

    #     user_info_action = menu.addAction(f"Влязъл като: {self.current_username}")
    #     user_info_action.setEnabled(False)
    #     menu.addSeparator()
    #     logout_action = menu.addAction("Изход")
    #     logout_action.triggered.connect(self.logout)
    #     menu.exec_(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def show_live_view_for_camera(self, camera):  # <-- ТОЗИ МЕТОД Е ДОБАВЕН/КОРИГИРАН
        # Превключване към страницата за изглед на живо и избор на камера
        self.side_menu.buttons["Изглед на живо"].setChecked(True)
        self.change_page("Изглед на живо")

        # Актуализирайте QComboBox на LiveViewPage
        # Първо изчистете старите елементи, за да не се повтарят, ако камерата е сменена
        self.live_view_page.camera_combo.clear()

        # Добавете текущата избрана камера към ComboBox-а
        self.live_view_page.camera_combo.addItem(camera.name)
        self.live_view_page.camera_combo.setCurrentText(camera.name)  # Избираме я

        # Тук трябва да инициирате стрийма на камерата в LiveViewPage
        # Засега, просто съобщение:
        QMessageBox.information(self, "Изглед на живо", f"Зареждане на изглед от {camera.name}...")

    def logout(self):
        reply = QMessageBox.question(self, "Изход", "Сигурни ли сте, че искате да излезете?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.close()  # Затваряне на главния прозорец
            os.execl(sys.executable, sys.executable, *sys.argv)  # Рестартира текущия скрипт

    def open_detection_zone_dialog(self, camera):  # <-- ДОБАВЕТЕ ТОЗИ МЕТОД
        dialog = DetectionZoneDialog(camera, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            # Зонирането е запазено директно в обекта на камерата в диалога
            self.camera_manager.update_camera(camera)  # Запазваме промените във файла
            QMessageBox.information(self, "Зони за детекция", f"Зоните за {camera.name} са запазени.")

    def logout(self):
        reply = QMessageBox.question(self, "Изход", "Сигурни ли сте, че искате да излезете?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.close()  # Затваряне на главния прозорец
            os.execl(sys.executable, sys.executable, *sys.argv)  # Рестартира текущия скрипт


if __name__ == '__main__':
    app = QApplication(sys.argv)

    # Уверете се, че папка 'icons' съществува и съдържа иконите
    # Ако ги нямате, можете да ги изтеглите или да ги премахнете от кода
    # Примерни икони: camera.png, user.png, bell.png, dashboard.png, records.png, alarm.png, actions.png, settings.png, live_view.png
    # arrow_down.png, camera_placeholder.png, snapshot.png, rewind.png, forward.png, zoom_in.png, zoom_out.png
    # Можете да използвате FontAwesome или други библиотеки за икони вместо файлове.

    # Създайте папка 'icons', ако не съществува
    if not os.path.exists("icons"):
        os.makedirs("icons")
        print("Създадена е папка 'icons'. Моля, поставете иконите вътре.")

    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())