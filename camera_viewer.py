import sys
import os
import cv2
import numpy as np
import json
import time
import threading
import subprocess
from datetime import datetime
import socket
from pathlib import Path
from ipaddress import ip_network, ip_address, IPv4Network  # Използваме ipaddress за по-добра валидация

# За хеширане на пароли
try:
    from passlib.hash import pbkdf2_sha256
except ImportError:
    print("passlib библиотеката не е инсталирана. Моля, инсталирайте я с: pip install passlib")


    # Авариен механизъм, ако passlib липсва - НЕ СЕ ИЗПОЛЗВА В ПРОДУКЦИОННА СРЕДА
    class MockHasher:
        def hash(self, password):
            return password  # Връща паролата без хеширане (НЕБЕЗОПАСНО!)

        def verify(self, password, hashed_password):
            return password == hashed_password  # Проверява паролата директно (НЕБЕЗОПАСНО!)


    pbkdf2_sha256 = MockHasher()

# За ONVIF свързаност (PTZ контрол)
try:
    from onvif import ONVIFCamera

    ONVIF_ENABLED = True
except ImportError:
    ONVIF_ENABLED = False
    print("ONVIF библиотеката (onvif-zeep) не е инсталирана. PTZ функциите няма да работят.")

# PyQt5 Imports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QGridLayout, QFrame,
    QInputDialog, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QSlider, QAbstractItemView, QProgressDialog, QSizePolicy,
    QFileSystemModel, QTreeView, QSplitter, QMessageBox, QCheckBox,
    QStackedWidget, QMenu, QComboBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QDir, QRect, QPoint, QSize
from PyQt5.QtGui import QFont, QImage, QPixmap, QPainter, QPen, QIcon, QIntValidator

# --- Глобални настройки ---
PRIMARY_COLOR = "#333333"
SECONDARY_COLOR = "#444444"
ACCENT_COLOR = "#FF8C00"
TEXT_COLOR = "#F0F0F0"
BORDER_COLOR = "#555555"
HOVER_COLOR = "#555555"
PANEL_BG_COLOR = "#3A3A3A"
FIELD_BG_COLOR = "#3A3A3A"
BUTTON_HOVER_COLOR = "#E67E00"
BUTTON_PRESSED_COLOR = "#CC7000"
BG_COLOR = "#2C2C2C"

# Икони (пътища)
ICON_PATH_CAMERAS = "icons/camera.png"
ICON_PATH_RECORDS = "icons/records.png"
ICON_PATH_ALARMS = "icons/alarm.png"
ICON_PATH_SETTINGS = "icons/settings.png"
ICON_PATH_LIVE_VIEW = "icons/live_view.png"
ICON_PATH_USER = "icons/user.png"
ICON_PATH_BELL = "icons/bell.png"
ICON_PATH_SNAPSHOT = "icons/snapshot.png"
ICON_PATH_RECORD = "icons/record.png"  # Добавена икона за Record бутона
ICON_PATH_ARROW_DOWN = "icons/arrow_down.png"

# Проверка и създаване на папка за записи
RECORDINGS_DIR = "recordings"
if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)


# --- Вградени класове от User Management системата ---

class User:
    def __init__(self, username, password_hash, is_admin=False):
        self.username = username
        self.password_hash = password_hash
        self.is_admin = is_admin

    def to_dict(self):
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "is_admin": self.is_admin
        }

    @classmethod
    def from_dict(cls, data):
        return cls(data["username"], data["password_hash"], data.get("is_admin", False))


class UserManager:
    def __init__(self, users_file="users.json"):
        self.users_file = Path(users_file)
        self.users = self._load_users()
        # Създаване на default admin, ако няма нито един админ
        if not any(user.is_admin for user in self.users.values()):
            if "admin" not in self.users:
                self.add_user("admin", "adminpass", is_admin=True)
                print("Default admin user 'admin' with password 'adminpass' created.")
            else:  # Ако потребител 'admin' съществува, но не е админ, го правим админ
                user = self.users["admin"]
                if not user.is_admin:
                    user.is_admin = True
                    self._save_users()
                    print("User 'admin' updated to be an admin.")

    def _load_users(self):
        if self.users_file.exists():
            with open(self.users_file, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    return {username: User.from_dict(user_data) for username, user_data in data.items()}
                except json.JSONDecodeError:
                    return {}
        return {}

    def _save_users(self):
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump({username: user.to_dict() for username, user in self.users.items()}, f, indent=4,
                      ensure_ascii=False)

    def add_user(self, username, password, is_admin=False):
        if username in self.users:
            return False, "Потребителското име вече съществува."

        password_hash = pbkdf2_sha256.hash(password)
        new_user = User(username, password_hash, is_admin)
        self.users[username] = new_user
        self._save_users()
        return True, "Потребителят е добавен успешно."

    def verify_password(self, username, password):
        user = self.users.get(username)
        if user:
            return pbkdf2_sha256.verify(password, user.password_hash)
        return False

    def get_user(self, username):
        return self.users.get(username)

    def get_all_users(self):
        return list(self.users.values())

    def update_user(self, username, new_password=None, new_is_admin=None):
        user = self.users.get(username)
        if not user:
            return False, "Потребителят не е намерен."

        if new_password:
            user.password_hash = pbkdf2_sha256.hash(new_password)
        if new_is_admin is not None:
            user.is_admin = new_is_admin

        self._save_users()
        return True, "Потребителят е актуализиран успешно."

    def delete_user(self, username):
        if username not in self.users:
            return False, "Потребителят не е намерен."

        del self.users[username]
        self._save_users()
        return True, "Потребителят е изтрит успешно."


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SecureView - Вход в акаунта")
        self.setFixedSize(600, 400)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_COLOR};
                color: {TEXT_COLOR};
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {TEXT_COLOR};
                font-size: 16px;
            }}
            QLineEdit {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
                padding: 10px;
                color: {TEXT_COLOR};
                font-size: 16px;
            }}
            QLineEdit:focus {{
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 25px;
                font-size: 18px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #E67E00;
            }}
            QPushButton:pressed {{
                background-color: #CC7000;
            }}
            #forgotPasswordLabel {{
                color: {ACCENT_COLOR};
                text-decoration: underline;
                font-size: 14px;
            }}
            #forgotPasswordLabel:hover {{
                color: #E67E00;
            }}
        """)

        self.user_manager = UserManager()
        self.username = None
        self.is_admin = False

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(50, 50, 50, 50)
        main_layout.setSpacing(20)
        main_layout.setAlignment(Qt.AlignCenter)

        title_label = QLabel("Вход в акаунта")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 20, 0, 20)
        form_layout.setHorizontalSpacing(20)
        form_layout.setVerticalSpacing(15)
        form_layout.setLabelAlignment(Qt.AlignLeft)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Въведете потребителско име")
        self.username_input.setFixedHeight(45)
        form_layout.addRow("Потребителско име", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Въведете парола")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setFixedHeight(45)
        form_layout.addRow("Парола", self.password_input)

        main_layout.addLayout(form_layout)

        login_button = QPushButton("Вход")
        login_button.setCursor(Qt.PointingHandCursor)
        login_button.clicked.connect(self.attempt_login)
        main_layout.addWidget(login_button)

        forgot_password_label = QLabel("<a href='#' id='forgotPasswordLabel'>Забравили сте паролата?</a>")
        forgot_password_label.setAlignment(Qt.AlignCenter)
        forgot_password_label.setOpenExternalLinks(False)
        forgot_password_label.linkActivated.connect(self.show_forgot_password_message)
        main_layout.addWidget(forgot_password_label)

        self.setLayout(main_layout)

    def attempt_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "Грешка при вход", "Моля, въведете потребителско име и парола.")
            return

        if self.user_manager.verify_password(username, password):
            user = self.user_manager.get_user(username)
            self.username = username
            self.is_admin = user.is_admin if user else False
            self.accept()
        else:
            QMessageBox.critical(self, "Грешка при вход", "Невалидно потребителско име или парола.")
            self.password_input.clear()

    def show_forgot_password_message(self):
        QMessageBox.information(self, "Забравена парола",
                                "Моля, свържете се с администратора на системата, за да възстановите паролата си.")


class UserManagementDialog(QDialog):
    user_updated = pyqtSignal()

    def __init__(self, current_username, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управление на потребители")
        self.setFixedSize(700, 600)
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
            QLineEdit, QComboBox {{
                background-color: {FIELD_BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 5px;
                padding: 8px;
                color: {TEXT_COLOR};
                font-size: 14px;
            }}
            QLineEdit:focus, QComboBox:focus {{
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

        self.user_manager = UserManager()
        self.current_username = current_username
        self.selected_user = None

        self.init_ui()
        self.load_users()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        title_label = QLabel("Управление на потребители")
        title_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        self.user_list_widget = QListWidget()
        self.user_list_widget.itemClicked.connect(self.display_user_details)
        main_layout.addWidget(self.user_list_widget)

        form_group_box = QWidget()
        form_layout = QFormLayout(form_group_box)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(10)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Потребителско име")
        form_layout.addRow("Потребителско име:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Парола (оставете празно за запазване на съществуващата)")
        self.password_input.setEchoMode(QLineEdit.Password)
        form_layout.addRow("Парола:", self.password_input)

        self.is_admin_combo = QComboBox()
        self.is_admin_combo.addItems(["Не", "Да"])
        form_layout.addRow("Администратор:", self.is_admin_combo)

        main_layout.addWidget(form_group_box)

        button_layout = QHBoxLayout()
        self.add_button = QPushButton("Добави")
        self.add_button.clicked.connect(self.add_user)
        button_layout.addWidget(self.add_button)

        self.update_button = QPushButton("Актуализирай")
        self.update_button.clicked.connect(self.update_user)
        self.update_button.setEnabled(False)
        button_layout.addWidget(self.update_button)

        self.delete_button = QPushButton("Изтрий")
        self.delete_button.clicked.connect(self.delete_user)
        self.delete_button.setEnabled(False)
        button_layout.addWidget(self.delete_button)

        self.clear_button = QPushButton("Изчисти форма")
        self.clear_button.clicked.connect(self.clear_form)
        button_layout.addWidget(self.clear_button)

        main_layout.addLayout(button_layout)

        close_button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        close_button_box.accepted.connect(self.accept)
        main_layout.addWidget(close_button_box)

    def load_users(self):
        self.user_list_widget.clear()
        for user in self.user_manager.get_all_users():
            item_text = f"{user.username} ({'Админ' if user.is_admin else 'Потребител'})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, user.username)
            self.user_list_widget.addItem(item)
        self.clear_form()

    def display_user_details(self, item):
        username = item.data(Qt.UserRole)
        self.selected_user = self.user_manager.get_user(username)
        if self.selected_user:
            self.username_input.setText(self.selected_user.username)
            self.password_input.clear()  # Изчистваме паролата за сигурност
            self.is_admin_combo.setCurrentIndex(1 if self.selected_user.is_admin else 0)
            self.username_input.setEnabled(False)  # Не позволяваме промяна на потребителското име

            self.add_button.setEnabled(False)
            self.update_button.setEnabled(True)
            self.delete_button.setEnabled(True)
        else:
            self.clear_form()

    def add_user(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        is_admin = self.is_admin_combo.currentIndex() == 1

        if not username or not password:
            QMessageBox.warning(self, "Грешка", "Моля, въведете потребителско име и парола.")
            return

        success, message = self.user_manager.add_user(username, password, is_admin)
        if success:
            QMessageBox.information(self, "Успех", message)
            self.load_users()
            self.user_updated.emit()  # Излъчва сигнал за актуализация
        else:
            QMessageBox.critical(self, "Грешка", message)

    def update_user(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Грешка", "Моля, изберете потребител за актуализация.")
            return

        username = self.selected_user.username
        new_password = self.password_input.text().strip()
        new_is_admin = self.is_admin_combo.currentIndex() == 1

        if username == self.current_username and not new_is_admin:
            QMessageBox.critical(self, "Грешка",
                                 "Не можете да премахнете администраторските си права, докато сте влезли.")
            self.is_admin_combo.setCurrentIndex(1)  # Връщаме обратно админ правата в комбобокса
            return

        success, message = self.user_manager.update_user(username, new_password if new_password else None, new_is_admin)
        if success:
            QMessageBox.information(self, "Успех", message)
            self.load_users()
            self.user_updated.emit()  # Излъчва сигнал за актуализация
        else:
            QMessageBox.critical(self, "Грешка", message)

    def delete_user(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Грешка", "Моля, изберете потребител за изтриване.")
            return

        if self.selected_user.username == self.current_username:
            QMessageBox.critical(self, "Грешка", "Не можете да изтриете собствения си акаунт, докато сте влезли.")
            return

        all_users = self.user_manager.get_all_users()
        admin_users = [u for u in all_users if u.is_admin]

        if self.selected_user.is_admin and len(admin_users) == 1:
            QMessageBox.critical(self, "Грешка", "Не можете да изтриете единствения администраторски акаунт.")
            return

        reply = QMessageBox.question(self, "Потвърждение за изтриване",
                                     f"Сигурни ли сте, че искате да изтриете потребител '{self.selected_user.username}'?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            success, message = self.user_manager.delete_user(self.selected_user.username)
            if success:
                QMessageBox.information(self, "Успех", message)
                self.load_users()
                self.user_updated.emit()  # Излъчва сигнал за актуализация
            else:
                QMessageBox.critical(self, "Грешка", message)

    def clear_form(self):
        self.username_input.clear()
        self.password_input.clear()
        self.is_admin_combo.setCurrentIndex(0)
        self.username_input.setEnabled(True)
        self.selected_user = None

        self.add_button.setEnabled(True)
        self.update_button.setEnabled(False)
        self.delete_button.setEnabled(False)


# Клас Camera
class Camera:
    def __init__(self, name, ip_address, port, status="Неактивна", rtsp_url=""):
        self.name = name
        self.ip_address = ip_address
        self.port = port
        self.status = status
        self.rtsp_url = rtsp_url
        self.is_recording = False
        self.motion_detection_enabled = False
        self.motion_sensitivity = "Средна"
        self.detection_zones = []

        self._cap = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._stream_thread = None
        self._is_streaming = False
        self._onvif_cam = None
        self._ptz_service = None
        self._media_profile = None

        self._video_writer = None
        self._is_manual_recording = False
        self._is_motion_recording = False
        self._last_motion_time = 0
        self._prev_frame_gray = None

    def to_dict(self):
        return {
            "name": self.name,
            "ip_address": self.ip_address,
            "port": self.port,
            "status": self.status,
            "rtsp_url": self.rtsp_url,
            "motion_detection_enabled": self.motion_detection_enabled,
            "motion_sensitivity": self.motion_sensitivity,
            # Съхраняваме QRect като списък от [x, y, width, height]
            "detection_zones": [[zone.x(), zone.y(), zone.width(), zone.height()] for zone in self.detection_zones]
        }

    @classmethod
    def from_dict(cls, data):
        camera = cls(data["name"], data["ip_address"], data["port"],
                     data.get("status", "Неактивна"), data.get("rtsp_url", ""))
        camera.motion_detection_enabled = data.get("motion_detection_enabled", False)
        camera.motion_sensitivity = data.get("motion_sensitivity", "Средна")
        # Възстановяваме QRect обекти от списъка
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
            # Изчакваме нишката да приключи
            self._stream_thread.join(timeout=2)
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._cap = None
        self._latest_frame = None
        self._prev_frame_gray = None  # Reset previous frame for motion detection
        self.stop_recording()  # Ensure recording is stopped

    def _run_stream(self):
        # Използваме rtsp_url, ако е даден, иначе конструираме стандартен
        stream_url = self.rtsp_url if self.rtsp_url else f"rtsp://{self.ip_address}:{self.port}/"
        print(f"[{self.name}] [Stream Thread] Attempting to open stream from URL: {stream_url}")

        self._cap = cv2.VideoCapture(stream_url)
        # Увеличен буфер за по-плавна работа
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)

        if self._cap.isOpened():
            self._is_streaming = True
            print(f"[{self.name}] [Stream Thread] Stream successfully opened. _is_streaming set to True.")
            # ONVIF инициализацията може да е бавна, по-добре да се прави асинхронно или веднъж при избор на камера
            # self.initialize_onvif() # Разумно е това да се извика само веднъж при първоначален избор, не в цикъла.
            # print(f"[{self.name}] [Stream Thread] ONVIF initialized (if applicable). Entering frame reading loop.")

            # "Загряване" на потока - четене на няколко кадъра, за да се стабилизира
            print(f"[{self.name}] [Stream Thread] Warming up stream, reading first few frames...")
            ret = False
            for _ in range(5):  # Опит за четене на 5 кадъра
                ret, frame = self._cap.read()
                if ret:
                    print(f"[{self.name}] [Stream Thread] Warm-up frame read successfully.")
                    break
                time.sleep(0.1)  # Кратка пауза между опитите

            if not ret:
                print(f"[{self.name}] [Stream Thread] FAILED to read warm-up frames. Stopping stream.")
                self._is_streaming = False
                if self._cap.isOpened(): self._cap.release()
                return

        else:
            self._is_streaming = False
            print(f"[{self.name}] [Stream Thread] FAILED to open stream from {stream_url}. _is_streaming set to False.")
            # В Production среда, може да добавите логика за автоматично повторно свързване.
            return  # Излизаме от нишката, ако не може да се отвори стрийм

        while self._is_streaming and self._cap.isOpened():
            ret, frame = self._cap.read()

            if not ret:
                print(f"[{self.name}] [Stream Thread] Failed to read frame (ret=False). Attempting reconnect...")
                self._cap.release()  # Освобождаваме ресурсите
                time.sleep(2)  # Кратка пауза преди опит за реконект
                self._cap = cv2.VideoCapture(stream_url)  # Нов опит за отваряне
                if not self._cap.isOpened():
                    print(f"[{self.name}] [Stream Thread] FAILED to reconnect after frame read error. Stopping stream.")
                    self._is_streaming = False  # Спираме, ако реконектът е неуспешен
                continue  # Продължаваме цикъла, за да опитаме отново или да излезем

            self._handle_motion_detection(frame)  # Обработка на детекция на движение

            with self._frame_lock:  # Защита на _latest_frame при достъп от множество нишки
                self._latest_frame = frame
                if (self._is_manual_recording or self._is_motion_recording) and self._video_writer:
                    try:
                        self._video_writer.write(frame)
                    except Exception as e:
                        print(f"[{self.name}] [Stream Thread] Error writing frame to video writer: {e}")
                        self.stop_recording()  # Спиране на записа при грешка

            time.sleep(0.01)  # Дава време на другите нишки и намалява CPU натоварването

        print(f"[{self.name}] [Stream Thread] Exiting main loop. Releasing resources.")
        if self._cap and self._cap.isOpened():
            self._cap.release()  # Освобождаване на VideoCapture
            print(f"[{self.name}] [Stream Thread] VideoCapture released.")
        self.stop_recording()  # Уверете се, че записът е спрян
        self._is_streaming = False
        print(f"[{self.name}] Stream thread finished for camera {self.name}.")

    def get_frame(self):
        with self._frame_lock:
            return self._latest_frame

    def initialize_onvif(self):
        if not ONVIF_ENABLED or not self.ip_address:
            print(f"[{self.name}] ONVIF disabled or no IP address. PTZ will not work.")
            return
        try:
            # ONVIF Камерите обикновено използват порт 80 или 8080 за HTTP
            # и имат специфични потребителско име и парола. Моля, коригирайте ги!
            self._onvif_cam = ONVIFCamera(self.ip_address, 80, "admin", "admin")
            # Може да се наложи да се зададе и WSDL директория, ако не се намира автоматично:
            # self._onvif_cam = ONVIFCamera(self.ip_address, 80, "admin", "admin", '/path/to/onvif/wsdl')

            self._onvif_cam.devicemgmt.GetSystemDateAndTime()  # Проверка за връзка
            print(f"[{self.name}] ONVIF connected to device. Creating PTZ service...")

            self._ptz_service = self._onvif_cam.create_ptz_service()

            media_service = self._onvif_cam.create_media_service()
            profiles = media_service.GetProfiles()

            if not profiles:
                print(f"[{self.name}] No media profiles found. PTZ will not work.")
                self._ptz_service = None
                return
            self._media_profile = profiles[0]

            if not hasattr(self._media_profile, 'PTZConfiguration') or not self._media_profile.PTZConfiguration:
                print(f"[{self.name}] No PTZ configuration found in media profile. PTZ not supported.")
                self._ptz_service = None
                return

            print(f"[{self.name}] ONVIF Initialized Successfully for PTZ.")
        except Exception as e:
            print(f"[{self.name}] ONVIF Initialization Failed: {e}")
            self._onvif_cam = None
            self._ptz_service = None

    def ptz_move(self, pan=0.0, tilt=0.0, zoom=0.0):
        if not self._ptz_service or not self._media_profile:
            print(f"[{self.name}] PTZ service not initialized.")
            return
        try:
            # pan и tilt обикновено са в диапазона [-1, 1], където 1 е максимална скорост.
            # zoom също е в същия диапазон.
            request = self._ptz_service.create_type('ContinuousMove')
            request.ProfileToken = self._media_profile.token
            # Scale pan/tilt/zoom to appropriate values if they are not already in ONVIF expected range
            request.Velocity = {'PanTilt': {'x': float(pan), 'y': float(tilt)}, 'Zoom': {'x': float(zoom)}}
            self._ptz_service.ContinuousMove(request)
            print(f"[{self.name}] PTZ move request: Pan={pan}, Tilt={tilt}, Zoom={zoom}")
        except Exception as e:
            print(f"[{self.name}] PTZ Move Error: {e}")

    def ptz_stop(self):
        if not self._ptz_service or not self._media_profile:
            return
        try:
            self._ptz_service.Stop({'ProfileToken': self._media_profile.token})
            print(f"[{self.name}] PTZ stop request.")
        except Exception as e:
            print(f"[{self.name}] PTZ Stop Error: {e}")

    def start_recording(self, is_motion=False):
        if self._video_writer or self._latest_frame is None:
            print(f"[{self.name}] [Recording] Cannot start recording. Writer exists or no frame.")
            return False

        camera_recordings_dir = Path(RECORDINGS_DIR) / self.name
        try:
            camera_recordings_dir.mkdir(parents=True, exist_ok=True)
            print(f"[{self.name}] [Recording] Recording directory ensured: {camera_recordings_dir}")
        except Exception as e:
            print(f"[{self.name}] [Recording] Error creating recording directory: {e}")
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "motion" if is_motion else "manual"
        filename = camera_recordings_dir / f"{prefix}_{timestamp}.mp4"
        print(f"[{self.name}] [Recording] Attempting to open video writer for: {filename}")

        try:
            height, width, _ = self._latest_frame.shape
            # Използвайте 'mp4v' за MP4. Уверете се, че са инсталирани нужните кодеци (например FFMPEG с OpenCV)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')

            # Опитайте да вземете FPS от камерата, иначе задайте default
            fps = self._cap.get(cv2.CAP_PROP_FPS) if self._cap and self._cap.isOpened() else 25.0
            if fps <= 0: fps = 25.0  # Fallback за FPS ако е 0 или отрицателно

            print(f"[{self.name}] [Recording] Detected stream resolution: {width}x{height}, FPS: {fps}")

            self._video_writer = cv2.VideoWriter(str(filename), fourcc, fps, (width, height))
            if not self._video_writer.isOpened():
                raise IOError(
                    f"OpenCV VideoWriter failed to open. Path: {filename}, FourCC: {fourcc}, FPS: {fps}, Size: {width}x{height}. Check codecs, permissions, and FFMPEG installation.")

            if is_motion:
                self._is_motion_recording = True
            else:
                self._is_manual_recording = True

            print(f"[{self.name}] Recording started: {filename}")
            return True
        except Exception as e:
            print(f"[{self.name}] FAILED to start recording: {e}")
            if self._video_writer:
                self._video_writer.release()
            self._video_writer = None
            return False

    def stop_recording(self):
        if self._video_writer:
            try:
                print(f"[{self.name}] Recording stopped. Releasing VideoWriter.")
                self._video_writer.release()
            except Exception as e:
                print(f"[{self.name}] [Recording] Error releasing VideoWriter: {e}")
            finally:
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
            # Мащабиране на зоните спрямо текущия размер на кадъра (ако е променен)
            h_frame, w_frame = frame.shape[:2]
            for zone_rect_orig_coords in self.detection_zones:
                # zone_rect_orig_coords вече са QRect с оригинални координати
                x1 = max(0, zone_rect_orig_coords.x())
                y1 = max(0, zone_rect_orig_coords.y())
                x2 = min(w_frame, zone_rect_orig_coords.x() + zone_rect_orig_coords.width())
                y2 = min(h_frame, zone_rect_orig_coords.y() + zone_rect_orig_coords.height())

                w = x2 - x1
                h = y2 - y1

                if w > 0 and h > 0:
                    roi_mask = thresh[y1:y2, x1:x2]
                    motion_pixels += cv2.countNonZero(roi_mask)
        else:
            # Ако няма дефинирани зони, проверява целия кадър
            motion_pixels = cv2.countNonZero(thresh)

        sensitivity_threshold = 0
        if self.motion_sensitivity == "Ниска":
            sensitivity_threshold = 5000
        elif self.motion_sensitivity == "Средна":
            sensitivity_threshold = 2000
        elif self.motion_sensitivity == "Висока":
            sensitivity_threshold = 500

        if motion_pixels > sensitivity_threshold:
            self._last_motion_time = time.time()
            if not self._is_motion_recording:
                print(f"[{self.name}] Motion detected. Starting motion recording.")
                self.start_recording(is_motion=True)
        else:
            if self._is_motion_recording and (
                    time.time() - self._last_motion_time > 5):  # Записва още 5 секунди след спиране на движението
                self._is_motion_recording = False
                if not self._is_manual_recording:  # Спира само ако не е ръчен запис
                    self.stop_recording()
                print(f"[{self.name}] Motion stopped.")

        self._prev_frame_gray = gray


# Клас NetworkScanner
class NetworkScanner(QObject):
    camera_found = pyqtSignal(str)
    scan_progress = pyqtSignal(int)
    scan_finished = pyqtSignal(str)

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
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)  # Кратък таймаут
                    if sock.connect_ex((ip_str, 554)) == 0:  # Проверка за отворен RTSP порт
                        self.camera_found.emit(ip_str)
                        print(f"Camera found at: {ip_str}")
            except Exception as e:
                # print(f"Error checking {ip_str}: {e}") # Премахнато за по-чист изход при нормална работа
                pass

            progress = int(((i + 1) / total_hosts) * 100)
            self.scan_progress.emit(progress)

        self.scan_finished.emit("Мрежовото сканиране приключи.")
        print("Network scan finished.")

    def cancel(self):
        self.is_cancelled = True


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
                    return {}  # Връща празен речник при невалиден JSON
        return {}  # Връща празен речник, ако файлът не съществува

    def _save_cameras(self):
        with open(self.cameras_file, 'w', encoding='utf-8') as f:
            json.dump([camera.to_dict() for camera in self.cameras.values()], f, indent=4, ensure_ascii=False)

    def add_camera(self, camera):
        if camera.name in self.cameras:
            return False, "Камера с това име вече съществува."
        # Проверка за уникалност на IP:Port комбинация
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

        # Проверка за дублиране на IP:Port при актуализация
        for existing_cam_name, existing_cam_obj in self.cameras.items():
            if existing_cam_name != camera.name and \
                    existing_cam_obj.ip_address == camera.ip_address and \
                    existing_cam_obj.port == camera.port:
                return False, f"Камера с IP адрес {camera.ip_address}:{camera.port} вече съществува под името '{existing_cam_name}'."

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
            self._save_cameras()  # Запазваме промяната в статуса
            return True
        return False


# Клас за странично меню
class SideMenu(QWidget):
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
                border-radius: 0px;
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

        logo_label = QLabel("Видеонаблюдение")
        logo_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        logo_label.setStyleSheet(f"color: {ACCENT_COLOR}; padding: 20px;")
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        self.buttons = {}
        button_data = {
            "Камери": ICON_PATH_CAMERAS,
            "Записи": ICON_PATH_RECORDS,
            "Аларми": ICON_PATH_ALARMS,
            "Настройки": ICON_PATH_SETTINGS,
            "Изглед на живо": ICON_PATH_LIVE_VIEW,
        }

        for text, icon_path in button_data.items():
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            if os.path.exists(icon_path):
                button.setIcon(QIcon(icon_path))
                button.setIconSize(QSize(24, 24))
            button.clicked.connect(lambda checked, t=text: self.page_changed.emit(t))
            self.buttons[text] = button
            layout.addWidget(button)

        layout.addStretch(1)
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
            .status-active {{
                color: #4CAF50;
                font-weight: bold;
            }}
            .status-inactive {{
                color: #F44336;
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
                image: url(icons/arrow_down.png);
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

        title_label = QLabel("Камери")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        main_layout.addWidget(title_label)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Търсене на камери")
        self.search_input.setFixedHeight(40)
        self.search_input.textChanged.connect(self.filter_cameras)
        search_layout.addWidget(self.search_input)
        main_layout.addLayout(search_layout)

        main_layout.addWidget(QLabel("Списък с камери"))
        self.camera_table = QListWidget()
        self.camera_table.setFixedHeight(250)
        self.camera_table.itemClicked.connect(self.on_camera_selected)
        main_layout.addWidget(self.camera_table)

        camera_buttons_layout = QHBoxLayout()
        add_camera_button = QPushButton("Добави камера")
        add_camera_button.clicked.connect(self.add_camera_requested.emit)
        camera_buttons_layout.addWidget(add_camera_button)

        self.edit_camera_button = QPushButton("Редактирай")
        self.edit_camera_button.clicked.connect(self.open_edit_camera_dialog)
        self.edit_camera_button.setEnabled(False)
        camera_buttons_layout.addWidget(self.edit_camera_button)

        self.delete_camera_button = QPushButton("Изтрий")
        self.delete_camera_button.clicked.connect(self.delete_selected_camera)
        self.delete_camera_button.setEnabled(False)
        camera_buttons_layout.addWidget(self.delete_camera_button)

        scan_network_button = QPushButton("Сканирай мрежата")
        scan_network_button.clicked.connect(self.scan_network_requested.emit)
        camera_buttons_layout.addWidget(scan_network_button)
        camera_buttons_layout.addStretch(1)
        main_layout.addLayout(camera_buttons_layout)

        main_layout.addWidget(QLabel("Настройки"))

        motion_detection_layout = QHBoxLayout()
        motion_detection_layout.addWidget(QLabel("Детекция на движение"))
        self.motion_detection_toggle = QCheckBox("Включи/Изключи")
        self.motion_detection_toggle.stateChanged.connect(self.update_camera_settings)
        motion_detection_layout.addWidget(self.motion_detection_toggle)
        motion_detection_layout.addStretch(1)
        main_layout.addLayout(motion_detection_layout)

        sensitivity_layout = QHBoxLayout()
        sensitivity_layout.addWidget(QLabel("Чувствителност на движение"))
        self.sensitivity_combo = QComboBox()
        self.sensitivity_combo.addItems(["Ниска", "Средна", "Висока"])
        self.sensitivity_combo.currentIndexChanged.connect(self.update_camera_settings)
        sensitivity_layout.addWidget(self.sensitivity_combo)
        sensitivity_layout.addStretch(1)
        main_layout.addLayout(sensitivity_layout)

        zones_layout = QHBoxLayout()
        zones_layout.addWidget(QLabel("Зони за детекция"))
        self.edit_zones_button = QPushButton("Редактирай зони")
        self.edit_zones_button.clicked.connect(self.on_edit_zones_clicked)
        zones_layout.addWidget(self.edit_zones_button)
        zones_layout.addStretch(1)
        main_layout.addLayout(zones_layout)

        main_layout.addStretch(1)

        self.selected_camera = None
        self.update_settings_ui_state(False)

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
            list_item.setData(Qt.UserRole, camera.name)

    def filter_cameras(self, text):
        search_text = text.lower()
        for i in range(self.camera_table.count()):
            item = self.camera_table.item(i)
            widget = self.camera_table.itemWidget(item)
            if widget:
                labels = widget.findChildren(QLabel)
                full_item_text = " ".join([label.text().lower() for label in labels])
                if search_text in full_item_text:
                    item.setHidden(False)
                else:
                    item.setHidden(True)
            else:
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
            self.edit_camera_button.setEnabled(True)
            self.delete_camera_button.setEnabled(True)
        else:
            self.update_settings_ui_state(False)
            self.edit_camera_button.setEnabled(False)
            self.delete_camera_button.setEnabled(False)

    def update_settings_ui_state(self, enabled):
        self.motion_detection_toggle.setEnabled(enabled)
        self.sensitivity_combo.setEnabled(enabled)
        self.edit_zones_button.setEnabled(enabled)

    def update_camera_settings(self):
        if self.selected_camera:
            self.selected_camera.motion_detection_enabled = self.motion_detection_toggle.isChecked()
            self.selected_camera.motion_sensitivity = self.sensitivity_combo.currentText()
            self.camera_manager.update_camera(self.selected_camera)

    def on_edit_zones_clicked(self):
        if self.selected_camera:
            self.edit_zones_requested.emit(self.selected_camera)

    def open_edit_camera_dialog(self):
        if not self.selected_camera:
            QMessageBox.warning(self, "Редакция на камера", "Моля, изберете камера за редакция.")
            return

        dialog = CameraDialog(camera=self.selected_camera, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            updated_camera = dialog.get_camera_data()
            if updated_camera:
                success, message = self.camera_manager.update_camera(updated_camera)
                if success:
                    QMessageBox.information(self, "Успех", message)
                    self.load_cameras_to_table()
                else:
                    QMessageBox.critical(self, "Грешка", message)

    def delete_selected_camera(self):
        if not self.selected_camera:
            QMessageBox.warning(self, "Изтриване на камера", "Моля, изберете камера за изтриване.")
            return

        reply = QMessageBox.question(self, "Потвърждение за изтриване",
                                     f"Сигурни ли сте, че искате да изтриете '{self.selected_camera.name}'?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            success, message = self.camera_manager.delete_camera(self.selected_camera.name)
            if success:
                QMessageBox.information(self, "Успех", message)
                self.load_cameras_to_table()
                self.selected_camera = None
                self.update_settings_ui_state(False)
                self.edit_camera_button.setEnabled(False)
                self.delete_camera_button.setEnabled(False)
            else:
                QMessageBox.critical(self, "Грешка", message)


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

        self.model = QFileSystemModel()
        self.model.setRootPath(RECORDINGS_DIR)
        self.model.setFilter(QDir.Files | QDir.NoDotAndDotDot | QDir.Dirs)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(RECORDINGS_DIR))
        self.tree.setColumnHidden(1, True)
        self.tree.setColumnHidden(2, True)
        self.tree.setColumnHidden(3, True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.doubleClicked.connect(self.open_selected_file)
        layout.addWidget(self.tree)

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

        try:
            os.startfile(file_path)
        except AttributeError:
            if sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
        except Exception as e:
            QMessageBox.critical(self, "Грешка при отваряне", f"Неуспешно отваряне на файла: {e}")

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
                    import shutil
                    shutil.rmtree(file_path)
                    QMessageBox.information(self, "Изтриване", "Папката и съдържанието й са изтрити успешно.")
                self.model.layoutChanged.emit()
            except Exception as e:
                QMessageBox.critical(self, "Грешка при изтриване", f"Неуспешно изтриване: {e}")

    def open_selected_file(self, index):
        file_path = self.model.filePath(index)
        if os.path.isfile(file_path):
            self.play_selected_record()


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

        filter_layout = QHBoxLayout()
        self.alarm_filter_combo = QComboBox()
        self.alarm_filter_combo.addItems(["Всички аларми", "Движение", "Звук", "Изгубена връзка"])
        filter_layout.addWidget(self.alarm_filter_combo)
        filter_layout.addStretch(1)
        layout.addLayout(filter_layout)

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

        self.load_sample_alarms()

        layout.addStretch(1)

    def load_sample_alarms(self):
        self.alarm_list_widget.clear()
        self.alarm_list_widget.addItem("Няма аларми за показване.")


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

        layout.addWidget(QLabel("Записи", objectName="setting-label"))
        record_duration_layout = QHBoxLayout()
        record_duration_layout.addWidget(QLabel("Продължителност на записите"))
        self.record_duration_combo = QComboBox()
        self.record_duration_combo.addItems(["15 минути", "30 минути", "1 час", "2 часа", "Неограничено"])
        self.record_duration_combo.setCurrentText("1 час")
        record_duration_layout.addWidget(self.record_duration_combo)
        record_duration_layout.addStretch(1)
        layout.addLayout(record_duration_layout)

        record_quality_layout = QHBoxLayout()
        record_quality_layout.addWidget(QLabel("Качество на записите"))
        self.record_quality_combo = QComboBox()
        self.record_quality_combo.addItems(["Ниско", "Средно", "Високо", "Full HD"])
        self.record_quality_combo.setCurrentText("Високо")
        record_quality_layout.addWidget(self.record_quality_combo)
        record_quality_layout.addStretch(1)
        layout.addLayout(record_quality_layout)

        layout.addWidget(QLabel("Потребители", objectName="setting-label"))
        user_management_layout = QHBoxLayout()
        user_management_layout.addWidget(QLabel("Управление на потребители"))
        self.manage_users_button = QPushButton("Управлявай >")
        self.manage_users_button.clicked.connect(self.open_user_management)
        user_management_layout.addWidget(self.manage_users_button)
        user_management_layout.addStretch(1)
        layout.addLayout(user_management_layout)

        if not self.is_admin:
            self.manage_users_button.setEnabled(False)
            self.manage_users_button.setToolTip("Само администратори могат да управляват потребители.")

        layout.addWidget(QLabel("Известия", objectName="setting-label"))
        motion_notification_layout = QHBoxLayout()
        motion_notification_layout.addWidget(QLabel("Известия при движение"))
        self.motion_notification_toggle = QCheckBox("Включи известия")
        self.motion_notification_toggle.setChecked(True)
        motion_notification_layout.addWidget(self.motion_notification_toggle)
        motion_notification_layout.addStretch(1)
        layout.addLayout(motion_notification_layout)

        layout.addWidget(QLabel("Други", objectName="setting-label"))
        storage_layout = QHBoxLayout()
        storage_layout.addWidget(QLabel("Съхранение на записи"))
        self.storage_location_label = QLabel("Локално")
        storage_layout.addWidget(self.storage_location_label)
        storage_layout.addStretch(1)
        layout.addLayout(storage_layout)

        about_layout = QHBoxLayout()
        about_layout.addWidget(QLabel("Информация за софтуера"))
        self.version_label = QLabel("v1.2.3")
        about_layout.addWidget(self.version_label)
        about_layout.addStretch(1)
        layout.addLayout(about_layout)

        layout.addStretch(1)

    def open_user_management(self):
        dialog = UserManagementDialog(current_username=self.current_username, parent=self)
        dialog.user_updated.connect(self.on_user_manager_update)
        dialog.exec_()

    def on_user_manager_update(self):
        print("Потребителските данни са актуализирани.")
        self.update_manage_users_button_state()

    def update_manage_users_button_state(self):
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
    def __init__(self, camera_manager, parent=None):
        super().__init__(parent)
        self.camera_manager = camera_manager
        self.current_camera = None
        self.active_camera_streams = {}

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
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_video_frames)
        self.timer.start(30)  # ~33 FPS

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        title_label = QLabel("Изглед на живо")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        layout.addWidget(title_label)

        camera_selection_layout = QHBoxLayout()
        camera_selection_layout.addWidget(QLabel("Камера:"))
        self.camera_combo = QComboBox()
        self.camera_combo.setPlaceholderText("Изберете камера")
        self.camera_combo.currentIndexChanged.connect(self.on_camera_combo_selected)
        camera_selection_layout.addWidget(self.camera_combo)
        camera_selection_layout.addStretch(1)
        layout.addLayout(camera_selection_layout)

        view_mode_layout = QHBoxLayout()
        view_mode_layout.addWidget(QLabel("Режим на изглед:"))
        self.single_view_button = QPushButton("Единичен изглед")
        self.single_view_button.setCheckable(True)
        self.single_view_button.setChecked(True)
        self.single_view_button.setStyleSheet("QPushButton.control-button")
        self.single_view_button.clicked.connect(lambda: self.set_view_mode("single"))

        self.multi_view_button = QPushButton("Мулти изглед")
        self.multi_view_button.setCheckable(True)
        self.multi_view_button.setStyleSheet("QPushButton.control-button")
        self.multi_view_button.clicked.connect(lambda: self.set_view_mode("multi"))

        view_mode_group = QWidget()
        view_mode_group_layout = QHBoxLayout(view_mode_group)
        view_mode_group_layout.setContentsMargins(0, 0, 0, 0)
        view_mode_group_layout.addWidget(self.single_view_button)
        view_mode_group_layout.addWidget(self.multi_view_button)
        view_mode_group_layout.addStretch(1)

        view_mode_layout.addWidget(view_mode_group)
        view_mode_layout.addStretch(1)
        layout.addLayout(view_mode_layout)

        # ==========================================================
        # Stacked Widget за видео дисплей (Single/Multi View)
        # Добавяме разтягащ елемент преди и след stacked widget
        layout.addStretch(1)  # Добавен stretch фактор преди видео дисплея

        self.video_display_stack = QStackedWidget()
        self.video_display_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.video_display_stack, 1)  # Разтягане на stacked widget

        # ---- Single View Display ----
        self.single_video_widget = QWidget()
        self.single_video_layout = QVBoxLayout(self.single_video_widget)
        self.single_video_layout.setContentsMargins(0, 0, 0, 0)
        self.single_video_layout.setSpacing(0)

        self.single_video_label = QLabel("Изберете камера за преглед")
        self.single_video_label.setAlignment(Qt.AlignCenter)
        self.single_video_label.setStyleSheet(f"background-color: #000000; border: 1px solid {BORDER_COLOR};")
        self.single_video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.single_video_label.setMinimumSize(320, 240)
        self.single_video_label.setScaledContents(True)
        self.single_video_layout.addWidget(self.single_video_label, 1)
        self.video_display_stack.addWidget(self.single_video_widget)

        # ---- Multi View Display ----
        self.multi_video_widget = QWidget()
        self.multi_video_grid_layout = QGridLayout(self.multi_video_widget)
        self.multi_video_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.multi_video_grid_layout.setSpacing(5)
        self.multi_video_labels = []
        for i in range(4):
            label = QLabel(f"Слот {i + 1}")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"background-color: {FIELD_BG_COLOR}; border: 1px dashed {BORDER_COLOR}; color: {TEXT_COLOR};")
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            label.setMinimumSize(200, 150)
            label.setScaledContents(True)
            row = i // 2
            col = i % 2
            self.multi_video_grid_layout.addWidget(label, row, col)
            self.multi_video_grid_layout.setRowStretch(row, 1)
            self.multi_video_grid_layout.setColumnStretch(col, 1)
            self.multi_video_labels.append(label)
        self.video_display_stack.addWidget(self.multi_video_widget)
        # ==========================================================

        layout.addStretch(1)  # Добавен stretch фактор след видео дисплея

        video_controls_layout = QHBoxLayout()
        video_controls_layout.addStretch(1)

        self.snapshot_button = QPushButton("Snapshot")
        if os.path.exists(ICON_PATH_SNAPSHOT):
            self.snapshot_button.setIcon(QIcon(ICON_PATH_SNAPSHOT))
            self.snapshot_button.setIconSize(QSize(24, 24))
        self.snapshot_button.clicked.connect(self.take_snapshot)
        video_controls_layout.addWidget(self.snapshot_button)

        self.record_button = QPushButton("Record")
        if os.path.exists(ICON_PATH_RECORD):  # Използваме ICON_PATH_RECORD
            self.record_button.setIcon(QIcon(ICON_PATH_RECORD))
            self.record_button.setIconSize(QSize(24, 24))
        self.record_button.clicked.connect(self.toggle_recording)
        video_controls_layout.addWidget(self.record_button)

        layout.addLayout(video_controls_layout)
        # Премахваме последния stretch factor, ако има такъв.
        # layout.addStretch(1) # Закоментирано, тъй като добавихме два преди и след stacked widget

    def set_view_mode(self, mode):
        self.stop_all_streams()

        if mode == "single":
            self.single_view_button.setChecked(True)
            self.multi_view_button.setChecked(False)
            self.video_display_stack.setCurrentIndex(0)
            self.camera_combo.setEnabled(True)
            if self.camera_combo.currentIndex() > 0:
                self.on_camera_combo_selected(self.camera_combo.currentIndex())
            else:
                self.single_video_label.setText("Изберете камера за преглед")
        elif mode == "multi":
            self.single_view_button.setChecked(False)
            self.multi_view_button.setChecked(True)
            self.video_display_stack.setCurrentIndex(1)
            self.camera_combo.setEnabled(False)
            self.start_multi_view_streams()

    def load_cameras_to_combo(self):
        self.camera_combo.clear()
        self.camera_combo.addItem("Изберете камера")
        for camera in self.camera_manager.get_all_cameras():
            self.camera_combo.addItem(camera.name)

    def on_camera_combo_selected(self, index):
        if not self.single_view_button.isChecked():
            return

        if index == 0:
            self.stop_current_stream()
            self.single_video_label.setText("Изберете камера за преглед")
            self.record_button.setText("Record")
            self.record_button.setStyleSheet("")
            return

        camera_name = self.camera_combo.currentText()
        selected_camera_obj = self.camera_manager.get_camera(camera_name)

        if selected_camera_obj:
            self.stop_current_stream()
            self.current_camera = selected_camera_obj
            self.current_camera.start_stream()
            print(f"[{self.current_camera.name}] Attempted to start stream for Single View.")
            self.single_video_label.setText(f"Свързване към {self.current_camera.name}...")
            if self.current_camera._is_manual_recording:
                self.record_button.setText("Stop Recording")
                self.record_button.setStyleSheet(f"background-color: red;")
            else:
                self.record_button.setText("Record")
                self.record_button.setStyleSheet("")
            # Инициализация на ONVIF PTZ, след като стриймът е стартиран успешно
            # Това е добро място за еднократна инициализация на ONVIF
            # self.current_camera.initialize_onvif()
        else:
            self.single_video_label.setText("Камерата не е намерена.")
            self.stop_current_stream()

    def stop_current_stream(self):
        if self.current_camera:
            self.current_camera.stop_recording()
            self.current_camera.stop_stream()
            print(f"[{self.current_camera.name}] Stream stopped by UI (Single View).")
            self.current_camera = None
        self.single_video_label.clear()
        self.single_video_label.setText("Изберете камера за преглед")

    def start_multi_view_streams(self):
        self.stop_all_streams()
        all_cameras = self.camera_manager.get_all_cameras()

        cameras_to_display = all_cameras[:4]

        for i, camera_obj in enumerate(cameras_to_display):
            self.active_camera_streams[camera_obj.name] = camera_obj
            camera_obj.start_stream()
            print(f"[{camera_obj.name}] Attempted to start stream for Multi View slot {i}.")
            self.multi_video_labels[i].setText(f"Свързване към {camera_obj.name}...")
            self.multi_video_labels[i].setStyleSheet(
                f"background-color: {FIELD_BG_COLOR}; border: 1px solid {BORDER_COLOR}; color: {TEXT_COLOR};")

        for i in range(len(cameras_to_display), 4):
            self.multi_video_labels[i].clear()
            self.multi_video_labels[i].setText(f"Слот {i + 1} (Празен)")
            self.multi_video_labels[i].setStyleSheet(
                f"background-color: {FIELD_BG_COLOR}; border: 1px dashed {BORDER_COLOR}; color: {TEXT_COLOR};")

    def stop_all_streams(self):
        if self.current_camera:
            self.current_camera.stop_recording()
            self.current_camera.stop_stream()
            self.current_camera = None
            self.single_video_label.clear()
            self.single_video_label.setText("Изберете камера за преглед")
            self.record_button.setText("Record")
            self.record_button.setStyleSheet("")

        for camera_name, camera_obj in list(self.active_camera_streams.items()):
            camera_obj.stop_recording()
            camera_obj.stop_stream()
            print(f"[{camera_obj.name}] Stream stopped by UI (Multi View).")
            del self.active_camera_streams[camera_name]

        for i in range(4):
            self.multi_video_labels[i].clear()
            self.multi_video_labels[i].setText(f"Слот {i + 1} (Празен)")
            self.multi_video_labels[i].setStyleSheet(
                f"background-color: {FIELD_BG_COLOR}; border: 1px dashed {BORDER_COLOR}; color: {TEXT_COLOR};")

    def update_video_frames(self):
        if self.single_view_button.isChecked() and self.current_camera and self.current_camera._is_streaming:
            frame = self.current_camera.get_frame()
            if frame is not None:
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)

                # DEBUGGING PRINTS
                # print(f"Single View: QLabel size: {self.single_video_label.size().width()}x{self.single_video_label.size().height()}")
                # print(f"Single View: Original frame size: {w}x{h}")

                scaled_pixmap = QPixmap.fromImage(qt_image).scaled(
                    self.single_video_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.single_video_label.setPixmap(scaled_pixmap)
                # print(f"Single View: Scaled Pixmap size: {scaled_pixmap.size().width()}x{scaled_pixmap.size().height()}")

                if self.current_camera.status == "Неактивна":
                    self.current_camera.status = "Активна"
                    self.camera_manager.update_camera_status(self.current_camera.name, "Активна")
            else:
                self.single_video_label.setText(f"Няма сигнал от {self.current_camera.name}")
                if self.current_camera.status == "Активна":
                    self.current_camera.status = "Неактивна"
                    self.camera_manager.update_camera_status(self.current_camera.name, "Неактивна")
                    print(f"[{self.current_camera.name}] Status updated to Inactive due to no frame.")
        elif self.single_view_button.isChecked() and self.current_camera is None:
            self.single_video_label.clear()
            self.single_video_label.setText("Изберете камера за преглед")
            self.record_button.setText("Record")
            self.record_button.setStyleSheet("")

        if self.multi_view_button.isChecked():
            for i, camera_name in enumerate(list(self.active_camera_streams.keys())):
                if i >= len(self.multi_video_labels):
                    break

                camera_obj = self.active_camera_streams.get(camera_name)
                if camera_obj and camera_obj._is_streaming:
                    frame = camera_obj.get_frame()
                    if frame is not None:
                        h, w, ch = frame.shape
                        bytes_per_line = ch * w
                        qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format_BGR888)

                        # DEBUGGING PRINTS
                        # print(f"Multi View Slot {i+1}: QLabel size: {self.multi_video_labels[i].size().width()}x{self.multi_video_labels[i].size().height()}")
                        # print(f"Multi View Slot {i+1}: Original frame size: {w}x{h}")

                        scaled_pixmap = QPixmap.fromImage(qt_image).scaled(
                            self.multi_video_labels[i].size(),
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation
                        )
                        self.multi_video_labels[i].setPixmap(scaled_pixmap)
                        # print(f"Multi View Slot {i+1}: Scaled Pixmap size: {scaled_pixmap.size().width()}x{scaled_pixmap.size().height()}")

                        if camera_obj.status == "Неактивна":
                            camera_obj.status = "Активна"
                            self.camera_manager.update_camera_status(camera_obj.name, "Активна")
                    else:
                        self.multi_video_labels[i].setText(f"Няма сигнал от {camera_obj.name}")
                        if camera_obj.status == "Активна":
                            camera_obj.status = "Неактивна"
                            self.camera_manager.update_camera_status(camera_obj.name, "Неактивна")
                            print(f"[{camera_obj.name}] Status updated to Inactive (Multi View).")
                elif camera_obj and not camera_obj._is_streaming:
                    self.multi_video_labels[i].setText(f"Свързване към {camera_obj.name}...")
                else:
                    self.multi_video_labels[i].setText(f"Слот {i + 1} (Неактивен)")

    def toggle_recording(self):
        if self.single_view_button.isChecked() and self.current_camera:
            if not self.current_camera._is_manual_recording:
                try:
                    if self.current_camera.start_recording(is_motion=False):
                        print(f"[{self.current_camera.name}] Manual recording started.")
                        self.record_button.setText("Stop Recording")
                        self.record_button.setStyleSheet(f"background-color: red;")
                    else:
                        QMessageBox.warning(self, "Запис", "Неуспешно стартиране на записа (неизвестна грешка).")
                except Exception as e:
                    QMessageBox.critical(self, "Грешка при запис", f"Неочакван срив при стартиране на записа: {e}")
                    self.record_button.setText("Record")
                    self.record_button.setStyleSheet("")
            else:
                try:
                    self.current_camera.stop_recording()
                    print(f"[{self.current_camera.name}] Manual recording stopped.")
                    self.record_button.setText("Record")
                    self.record_button.setStyleSheet("")
                except Exception as e:
                    QMessageBox.critical(self, "Грешка при запис", f"Неочакван срив при спиране на записа: {e}")
                    self.record_button.setText("Record")
                    self.record_button.setStyleSheet("")
        else:
            QMessageBox.warning(self, "Запис", "Моля, изберете камера в 'Single View' за ръчен запис.")

    def take_snapshot(self):
        if self.single_view_button.isChecked() and self.current_camera and self.current_camera._latest_frame is not None:
            camera_recordings_dir = Path(RECORDINGS_DIR) / self.current_camera.name
            try:
                camera_recordings_dir.mkdir(parents=True, exist_ok=True)
                print(f"[{self.current_camera.name}] Snapshot directory ensured: {camera_recordings_dir}")
            except Exception as e:
                QMessageBox.critical(self, "Грешка при снимка",
                                     f"Неуспешно създаване на директория за моментна снимка: {e}")
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = camera_recordings_dir / f"snapshot_{timestamp}.jpg"

            try:
                if self.current_camera._latest_frame is not None:
                    print(f"[{self.current_camera.name}] Attempting to save snapshot to: {filename}")
                    success = cv2.imwrite(str(filename), self.current_camera._latest_frame)
                    if success:
                        QMessageBox.information(self, "Моментна снимка", f"Моментна снимка е запазена: {filename.name}")
                        print(f"[{self.current_camera.name}] Snapshot saved successfully.")
                    else:
                        QMessageBox.critical(self, "Грешка при снимка",
                                             "Неуспешно запазване на моментна снимка: imwrite върна false.")
                        print(f"[{self.current_camera.name}] cv2.imwrite returned False. Check file path permissions.")
                else:
                    QMessageBox.warning(self, "Моментна снимка", "Няма валиден кадър за моментна снимка.")
            except Exception as e:
                QMessageBox.critical(self, "Грешка при снимка", f"Неочакван срив при запазване на моментна снимка: {e}")
                print(f"[{self.current_camera.name}] Exception during snapshot saving: {e}")
        else:
            QMessageBox.warning(self, "Моментна снимка", "Моля, изберете камера в 'Single View' за моментна снимка.")


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
        self.port_input.setValidator(QIntValidator(1, 65535, self))
        form_layout.addRow("Порт:", self.port_input)

        self.rtsp_url_input = QLineEdit()
        self.rtsp_url_input.setPlaceholderText("RTSP URL (напр. rtsp://192.168.1.100:554/stream1) - Опционално")
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
        self.rtsp_url_input.setText(self.camera.rtsp_url)
        self.name_input.setEnabled(False)

    def get_camera_data(self):
        name = self.name_input.text().strip()
        ip_address_str = self.ip_input.text().strip()
        port_str = self.port_input.text().strip()
        rtsp_url = self.rtsp_url_input.text().strip()

        if not name or not ip_address_str or not port_str:
            QMessageBox.warning(self, "Грешка", "Моля, попълнете всички задължителни полета (Име, IP, Порт).")
            return None

        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Грешка", "Невалиден номер на порт. Моля, въведете число между 1 и 65535.")
            return None

        try:
            ip_address(ip_address_str)  # Валидация на IP адрес
        except ValueError:
            QMessageBox.warning(self, "Грешка", "Невалиден IP адрес.")
            return None

        if rtsp_url and not (
                rtsp_url.startswith("rtsp://") or rtsp_url.startswith("http://") or rtsp_url.startswith("https://")):
            QMessageBox.warning(self, "Грешка", "RTSP URL трябва да започва с 'rtsp://', 'http://' или 'https://'.")
            return None

        if self.camera:
            self.camera.ip_address = ip_address_str
            self.camera.port = port
            self.camera.rtsp_url = rtsp_url
            return self.camera
        else:
            return Camera(name, ip_address_str, port, rtsp_url=rtsp_url)


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
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setScaledContents(True)

        latest_frame = self.camera.get_frame()  # Взима последния кадър от камерата
        if latest_frame is not None:
            h, w, ch = latest_frame.shape
            bytes_per_line = ch * w
            qt_image = QImage(latest_frame.data, w, h, bytes_per_line, QImage.Format_BGR888)
            self.original_pixmap = QPixmap.fromImage(qt_image)
            print(f"DetectionZoneDialog: Loaded frame from camera. Size: {w}x{h}")
        elif os.path.exists("icons/camera_placeholder.png"):  # Fallback към placeholder, ако има
            self.original_pixmap = QPixmap("icons/camera_placeholder.png")
            print("DetectionZoneDialog: Loaded placeholder image.")
        else:  # Ако няма нито кадър, нито placeholder, създаваме черен образ
            img = QImage(640, 480, QImage.Format_RGB32)
            img.fill(Qt.black)
            self.original_pixmap = QPixmap.fromImage(img)
            print("DetectionZoneDialog: Created black placeholder image.")

        self.current_pixmap = self.original_pixmap.copy()  # Копие за рисуване
        self.image_label.setPixmap(self.current_pixmap)

        self.drawing = False
        self.start_point = QPoint()
        self.end_point = QPoint()

        # Свързване на събитията на мишката
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
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Първоначално рисуване на съществуващите зони
        self.draw_existing_zones()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Прерисувай зоните при промяна на размера на диалога
        self.draw_existing_zones()

    def draw_existing_zones(self):
        label_size = self.image_label.size()
        if label_size.isEmpty() or self.original_pixmap.isNull():
            print("draw_existing_zones: QLabel size is empty or original pixmap is null.")
            return

        # Мащабираме original_pixmap до текущия размер на image_label, запазвайки съотношението
        scaled_original_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.current_pixmap = scaled_original_pixmap.copy()

        painter = QPainter(self.current_pixmap)
        painter.setPen(QPen(Qt.red, 2, Qt.SolidLine))  # Цвят за съществуващи зони
        painter.setBrush(Qt.NoBrush)

        # Изчисляваме отместването, ако мащабираната картина не запълва целия лейбъл
        image_rect = scaled_original_pixmap.rect()
        label_rect = self.image_label.rect()
        offset_x = (label_rect.width() - image_rect.width()) / 2
        offset_y = (label_rect.height() - image_rect.height()) / 2

        orig_w, orig_h = self.original_pixmap.width(), self.original_pixmap.height()
        scaled_w, scaled_h = scaled_original_pixmap.width(), scaled_original_pixmap.height()

        # Изчисляваме скалиращи фактори
        scale_factor_x = scaled_w / orig_w if orig_w > 0 else 1
        scale_factor_y = scaled_h / orig_h if orig_h > 0 else 1

        for zone in self.camera.detection_zones:
            # Мащабираме и отместваме координатите на зоните за рисуване
            scaled_x = int(zone.x() * scale_factor_x + offset_x)
            scaled_y = int(zone.y() * scale_factor_y + offset_y)
            scaled_width = int(zone.width() * scale_factor_x)
            scaled_height = int(zone.height() * scale_factor_y)

            painter.drawRect(scaled_x, scaled_y, scaled_width, scaled_height)

        painter.end()
        self.image_label.setPixmap(self.current_pixmap)
        print(f"draw_existing_zones: Drew {len(self.camera.detection_zones)} zones.")

    def mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drawing = True
            # Коригираме позицията на мишката спрямо отместването на изображението в QLabel
            label_size = self.image_label.size()
            scaled_original_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio,
                                                                 Qt.SmoothTransformation)
            image_rect = scaled_original_pixmap.rect()
            label_rect = self.image_label.rect()
            offset_x = (label_rect.width() - image_rect.width()) / 2
            offset_y = (label_rect.height() - image_rect.height()) / 2

            self.start_point = event.pos() - QPoint(offset_x, offset_y)
            self.end_point = event.pos() - QPoint(offset_x, offset_y)
            # print(f"Mouse Press: Raw {event.pos()}, Corrected Start {self.start_point}")

    def mouse_move(self, event):
        if self.drawing:
            label_size = self.image_label.size()
            scaled_original_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio,
                                                                 Qt.SmoothTransformation)
            image_rect = scaled_original_pixmap.rect()
            label_rect = self.image_label.rect()
            offset_x = (label_rect.width() - image_rect.width()) / 2
            offset_y = (label_rect.height() - image_rect.height()) / 2

            self.end_point = event.pos() - QPoint(offset_x, offset_y)
            self.draw_current_rectangle()
            # print(f"Mouse Move: Corrected End {self.end_point}")

    def mouse_release(self, event):
        if event.button() == Qt.LeftButton and self.drawing:
            self.drawing = False
            label_size = self.image_label.size()
            scaled_original_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio,
                                                                 Qt.SmoothTransformation)

            image_rect = scaled_original_pixmap.rect()
            label_rect = self.image_label.rect()
            offset_x = (label_rect.width() - image_rect.width()) / 2
            offset_y = (label_rect.height() - image_rect.height()) / 2

            final_end_point = event.pos() - QPoint(offset_x, offset_y)

            # Мащабираме обратно координатите до оригиналния размер на кадъра
            orig_w, orig_h = self.original_pixmap.width(), self.original_pixmap.height()
            scaled_w, scaled_h = scaled_original_pixmap.width(), scaled_original_pixmap.height()

            scale_factor_x = orig_w / scaled_w if scaled_w > 0 else 1
            scale_factor_y = orig_h / scaled_h if scaled_h > 0 else 1

            x1_scaled = self.start_point.x()
            y1_scaled = self.start_point.y()
            x2_scaled = final_end_point.x()
            y2_scaled = final_end_point.y()

            x1_orig = int(x1_scaled * scale_factor_x)
            y1_orig = int(y1_scaled * scale_factor_y)
            x2_orig = int(x2_scaled * scale_factor_x)
            y2_orig = int(y2_scaled * scale_factor_y)

            rect_orig = QRect(QPoint(x1_orig, y1_orig), QPoint(x2_orig, y2_orig)).normalized()

            # print(f"Mouse Release: Original Rect: {rect_orig}")

            if rect_orig.width() > 0 and rect_orig.height() > 0:
                self.camera.detection_zones.append(rect_orig)
                self.draw_existing_zones()
            else:
                print("Mouse Release: Drawn rectangle has zero width or height, not adding zone.")

    def draw_current_rectangle(self):
        label_size = self.image_label.size()
        if label_size.isEmpty() or self.original_pixmap.isNull():
            return

        temp_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation).copy()
        painter = QPainter(temp_pixmap)

        scaled_original_pixmap = self.original_pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image_rect = scaled_original_pixmap.rect()
        label_rect = self.image_label.rect()
        offset_x = (label_rect.width() - image_rect.width()) / 2
        offset_y = (label_rect.height() - image_rect.height()) / 2

        # Рисуваме съществуващите зони (мащабирани и отместени)
        painter.setPen(QPen(Qt.red, 2, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)

        orig_w, orig_h = self.original_pixmap.width(), self.original_pixmap.height()
        scaled_w, scaled_h = scaled_original_pixmap.width(), scaled_original_pixmap.height()

        scale_factor_x = scaled_w / orig_w if orig_w > 0 else 1
        scale_factor_y = scaled_h / orig_h if orig_h > 0 else 1

        for zone in self.camera.detection_zones:
            scaled_x = int(zone.x() * scale_factor_x + offset_x)
            scaled_y = int(zone.y() * scale_factor_y + offset_y)
            scaled_width = int(zone.width() * scale_factor_x)
            scaled_height = int(zone.height() * scale_factor_y)
            painter.drawRect(scaled_x, scaled_y, scaled_width, scaled_height)

        # Рисуваме текущата зона, която се чертае (с отместване)
        painter.setPen(QPen(Qt.blue, 2, Qt.DotLine))
        rect = QRect(self.start_point + QPoint(offset_x, offset_y),
                     self.end_point + QPoint(offset_x, offset_y)).normalized()
        painter.drawRect(rect)

        painter.end()
        self.image_label.setPixmap(temp_pixmap)

    def add_zone(self):
        QMessageBox.information(self, "Добавяне на зона", "Моля, маркирайте зоната с мишката върху изображението.")

    def clear_all_zones(self):
        reply = QMessageBox.question(self, "Изчисти зони",
                                     "Сигурни ли сте, че искате да изчистите всички зони за детекция?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.camera.detection_zones.clear()
            self.draw_existing_zones()


# Основен прозорец на приложението
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SecureView - Система за видеонаблюдение")
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
                border-radius: 18px;
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
            sys.exit(0)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.side_menu = SideMenu()
        self.side_menu.page_changed.connect(self.change_page)
        main_layout.addWidget(self.side_menu, 1)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        header_widget = QWidget()
        header_widget.setObjectName("headerWidget")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(15)

        self.header_label = QLabel("Начало")
        self.header_label.setObjectName("headerLabel")
        header_layout.addWidget(self.header_label)
        header_layout.addStretch(1)

        self.user_button = QPushButton()
        self.user_button.setObjectName("userButton")
        if os.path.exists(ICON_PATH_USER):
            self.user_button.setIcon(QIcon(ICON_PATH_USER))
            self.user_button.setIconSize(QSize(24, 24))
        self.user_button.clicked.connect(self.show_user_menu)
        header_layout.addWidget(self.user_button)

        self.notification_button = QPushButton()
        self.notification_button.setObjectName("notificationButton")
        if os.path.exists(ICON_PATH_BELL):
            self.notification_button.setIcon(QIcon(ICON_PATH_BELL))
            self.notification_button.setIconSize(QSize(24, 24))
        header_layout.addWidget(self.notification_button)

        content_layout.addWidget(header_widget)

        self.pages_widget = QStackedWidget()
        content_layout.addWidget(self.pages_widget, 1)

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

        self.actions_page = QWidget()
        self.actions_page_layout = QVBoxLayout(self.actions_page)
        self.actions_page_layout.addWidget(QLabel("Страница за действия"))
        self.pages_widget.addWidget(self.actions_page)

        self.settings_page = SettingsPage(self.current_username, self.is_admin)
        self.pages_widget.addWidget(self.settings_page)

        self.live_view_page = LiveViewPage(self.camera_manager)
        self.pages_widget.addWidget(self.live_view_page)

        main_layout.addLayout(content_layout, 4)

        self.change_page("Камери")
        self.settings_page.update_manage_users_button_state()

    def change_page(self, page_name):
        self.live_view_page.stop_all_streams()

        self.header_label.setText(page_name)
        if page_name == "Камери":
            self.pages_widget.setCurrentWidget(self.cameras_page)
            self.cameras_page.load_cameras_to_table()
        elif page_name == "Записи":
            self.pages_widget.setCurrentWidget(self.records_page)
            self.records_page.model.setRootPath(RECORDINGS_DIR)
            self.records_page.tree.setRootIndex(self.records_page.model.index(RECORDINGS_DIR))
        elif page_name == "Аларми":
            self.pages_widget.setCurrentWidget(self.alarms_page)
        elif page_name == "Настройки":
            self.pages_widget.setCurrentWidget(self.settings_page)
            self.settings_page.update_manage_users_button_state()
        elif page_name == "Изглед на живо":
            self.pages_widget.setCurrentWidget(self.live_view_page)
            self.live_view_page.load_cameras_to_combo()
            self.live_view_page.single_view_button.setChecked(True)
            self.live_view_page.set_view_mode("single")

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
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            subnet = IPv4Network(f"{local_ip}/24", strict=False)
        except Exception as e:
            QMessageBox.critical(self, "Грешка при сканиране", f"Не може да се определи локалната подмрежа: {e}")
            return

        self.progress_dialog = QProgressDialog("Сканиране на вашата мрежа за камери...", "Отмяна", 0, 100, self)
        self.progress_dialog.setWindowTitle("Сканиране на мрежата")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(True)

        self.scanner_thread = QThread()
        self.scanner = NetworkScanner(subnet)
        self.scanner.moveToThread(self.scanner_thread)

        self.progress_dialog.canceled.connect(self.scanner.cancel)
        self.scanner.scan_progress.connect(self.progress_dialog.setValue)
        self.scanner.camera_found.connect(self.add_scanned_camera)
        self.scanner.scan_finished.connect(self.on_scan_finished)

        self.scanner_thread.started.connect(self.scanner.run)

        self.progress_dialog.show()
        self.scanner_thread.start()

    def add_scanned_camera(self, ip_address):
        existing_camera = next((c for c in self.camera_manager.get_all_cameras() if c.ip_address == ip_address), None)
        if existing_camera:
            print(f"Камера с IP {ip_address} вече съществува. Пропускане.")
            return

        camera_name = f"Камера_{ip_address.replace('.', '_')}"
        rtsp_url = f"rtsp://{ip_address}:554/stream1"
        new_camera = Camera(name=camera_name, ip_address=ip_address, port=554, status="Неактивна", rtsp_url=rtsp_url)

        success, message = self.camera_manager.add_camera(new_camera)
        if success:
            print(f"Намерена нова камера: {camera_name} ({ip_address}). Добавена е към списъка.")
            self.cameras_page.load_cameras_to_table()

    def on_scan_finished(self, message):
        self.progress_dialog.close()
        QMessageBox.information(self, "Сканиране на мрежата", message)

        if self.scanner_thread:
            self.scanner_thread.quit()
            self.scanner_thread.wait()
        self.scanner_thread = None
        self.scanner = None

    def open_detection_zone_dialog(self, camera):
        dialog = DetectionZoneDialog(camera, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            self.camera_manager.update_camera(camera)
            QMessageBox.information(self, "Зони за детекция", f"Зоните за {camera.name} са запазени.")

    def show_live_view_for_camera(self, camera):
        self.side_menu.buttons["Изглед на живо"].setChecked(True)
        self.change_page("Изглед на живо")

        self.live_view_page.load_cameras_to_combo()
        index = self.live_view_page.camera_combo.findText(camera.name)
        if index != -1:
            self.live_view_page.camera_combo.setCurrentIndex(index)
        else:
            QMessageBox.warning(self, "Изглед на живо", f"Камера '{camera.name}' не е намерена в списъка за преглед.")
            self.live_view_page.single_video_label.setText("Избраната камера не е достъпна за преглед.")

    def show_user_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self.styleSheet())

        user_info_action = menu.addAction(f"Влязъл като: {self.current_username}")
        user_info_action.setEnabled(False)

        menu.addSeparator()

        change_password_action = menu.addAction("Смяна на парола")
        change_password_action.triggered.connect(self.open_change_password_dialog)

        logout_action = menu.addAction("Изход")
        logout_action.triggered.connect(self.logout)

        menu.exec_(self.user_button.mapToGlobal(QPoint(self.user_button.width(), 0)))

    def open_change_password_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Смяна на парола")
        dialog.setFixedSize(300, 150)
        dialog_layout = QVBoxLayout(dialog)

        form_layout = QFormLayout()
        new_password_input = QLineEdit()
        new_password_input.setEchoMode(QLineEdit.Password)
        new_password_input.setPlaceholderText("Въведете нова парола")
        form_layout.addRow("Нова парола:", new_password_input)

        confirm_password_input = QLineEdit()
        confirm_password_input.setEchoMode(QLineEdit.Password)
        confirm_password_input.setPlaceholderText("Потвърдете нова парола")
        form_layout.addRow("Потвърдете:", confirm_password_input)

        dialog_layout.addLayout(form_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)

        if dialog.exec_() == QDialog.Accepted:
            new_pass = new_password_input.text().strip()
            confirm_pass = confirm_password_input.text().strip()

            if not new_pass:
                QMessageBox.warning(self, "Смяна на парола", "Паролата не може да бъде празна.")
                return
            if new_pass != confirm_pass:
                QMessageBox.warning(self, "Смяна на парола", "Паролите не съвпадат.")
                return

            success, message = self.user_manager.update_user(self.current_username, new_password=new_pass)
            if success:
                QMessageBox.information(self, "Смяна на парола", "Паролата е сменена успешно.")
            else:
                QMessageBox.critical(self, "Грешка", f"Неуспешна смяна на парола: {message}")
        else:
            QMessageBox.information(self, "Смяна на парола", "Смяната на паролата е отменена.")

    def logout(self):
        reply = QMessageBox.question(self, "Изход", "Сигурни ли сте, че искате да излезете?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.live_view_page.stop_all_streams()
            self.close()
            os.execv(sys.executable, ['python'] + sys.argv)


if __name__ == '__main__':
    # Създаване на папка 'icons' и placeholder икони, ако липсват
    if not os.path.exists("icons"):
        os.makedirs("icons")
        print("Създадена е папка 'icons'. Моля, поставете иконите вътре.")
        try:
            from PIL import Image

            icon_names = ["camera.png", "user.png", "bell.png", "live_view.png",
                          "records.png", "alarm.png", "settings.png", "snapshot.png",
                          "record.png", "arrow_down.png", "camera_placeholder.png"]
            for name in icon_names:
                size = (24, 24) if name not in ["arrow_down.png", "camera_placeholder.png"] else (
                    (16, 16) if name == "arrow_down.png" else (640, 480))
                Image.new('RGB', size, color=(0, 0, 0)).save(f"icons/{name}")
            print("Създадени са placeholder икони.")
        except ImportError:
            print("За да създадете placeholder икони, моля инсталирайте Pillow: pip install Pillow")

    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())