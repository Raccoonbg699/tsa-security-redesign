from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QDialogButtonBox, QListWidget, QListWidgetItem,
    QLabel, QMessageBox, QComboBox, QWidget  # Добавено QWidget
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QFont

# Import UserManager and User from the user_manager.py file
from user_manager import UserManager, User

# Дефиниране на цветове
BG_COLOR = "#2C2C2C"  # Тъмно сиво за фон
FIELD_BG_COLOR = "#3A3A3A"  # По-светло сиво за полета
TEXT_COLOR = "#F0F0F0"  # Светъл текст
ACCENT_COLOR = "#FF8C00"  # Оранжев акцент
BORDER_COLOR = "#505050"  # Цвят на рамката
BUTTON_HOVER_COLOR = "#E67E00"
BUTTON_PRESSED_COLOR = "#CC7000"


class UserManagementDialog(QDialog):
    user_updated = pyqtSignal()  # Сигнал, който се излъчва при промяна на потребител

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
        self.current_username = current_username  # Потребителят, който е влязъл в момента
        self.selected_user = None

        self.init_ui()
        self.load_users()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Заглавие
        title_label = QLabel("Управление на потребители")
        title_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # Списък с потребители
        self.user_list_widget = QListWidget()
        self.user_list_widget.itemClicked.connect(self.display_user_details)
        main_layout.addWidget(self.user_list_widget)

        # Форма за добавяне/редактиране на потребители
        form_group_box = QWidget()
        form_layout = QFormLayout(form_group_box)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(10)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Потребителско име")
        form_layout.addRow("Потребителско име:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Парола (оставете празно за запазване)")
        self.password_input.setEchoMode(QLineEdit.Password)
        form_layout.addRow("Парола:", self.password_input)

        self.is_admin_combo = QComboBox()
        self.is_admin_combo.addItems(["Не", "Да"])  # 0 за Не, 1 за Да
        form_layout.addRow("Администратор:", self.is_admin_combo)

        main_layout.addWidget(form_group_box)

        # Бутони за действия
        button_layout = QHBoxLayout()
        self.add_button = QPushButton("Добави")
        self.add_button.clicked.connect(self.add_user)
        button_layout.addWidget(self.add_button)

        self.update_button = QPushButton("Актуализирай")
        self.update_button.clicked.connect(self.update_user)
        self.update_button.setEnabled(False)  # Деактивиран по подразбиране
        button_layout.addWidget(self.update_button)

        self.delete_button = QPushButton("Изтрий")
        self.delete_button.clicked.connect(self.delete_user)
        self.delete_button.setEnabled(False)  # Деактивиран по подразбиране
        button_layout.addWidget(self.delete_button)

        self.clear_button = QPushButton("Изчисти форма")
        self.clear_button.clicked.connect(self.clear_form)
        button_layout.addWidget(self.clear_button)

        main_layout.addLayout(button_layout)

        # Бутон за затваряне
        close_button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        close_button_box.accepted.connect(self.accept)
        main_layout.addWidget(close_button_box)

    def load_users(self):
        self.user_list_widget.clear()
        for user in self.user_manager.get_all_users():
            item_text = f"{user.username} ({'Админ' if user.is_admin else 'Потребител'})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, user.username)  # Съхраняваме потребителското име в UserRole
            self.user_list_widget.addItem(item)
        self.clear_form()

    def display_user_details(self, item):
        username = item.data(Qt.UserRole)
        self.selected_user = self.user_manager.get_user(username)
        if self.selected_user:
            self.username_input.setText(self.selected_user.username)
            self.password_input.clear()  # Не показваме паролата
            self.is_admin_combo.setCurrentIndex(1 if self.selected_user.is_admin else 0)
            self.username_input.setEnabled(False)  # Не позволяваме промяна на потребителско име при редакция

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
            self.user_updated.emit()  # Излъчваме сигнал за актуализация
        else:
            QMessageBox.critical(self, "Грешка", message)

    def update_user(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Грешка", "Моля, изберете потребител за актуализация.")
            return

        username = self.selected_user.username  # Използваме старото потребителско име
        new_password = self.password_input.text().strip()
        new_is_admin = self.is_admin_combo.currentIndex() == 1

        # Не позволяваме на текущо влезлия админ да премахне администраторските си права
        if username == self.current_username and not new_is_admin:
            QMessageBox.critical(self, "Грешка",
                                 "Не можете да премахнете администраторските си права, докато сте влезли.")
            self.is_admin_combo.setCurrentIndex(1)  # Връщаме избора на "Да"
            return

        success, message = self.user_manager.update_user(username, new_password if new_password else None, new_is_admin)
        if success:
            QMessageBox.information(self, "Успех", message)
            self.load_users()
            self.user_updated.emit()  # Излъчваме сигнал за актуализация
        else:
            QMessageBox.critical(self, "Грешка", message)

    def delete_user(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Грешка", "Моля, изберете потребител за изтриване.")
            return

        if self.selected_user.username == self.current_username:
            QMessageBox.critical(self, "Грешка", "Не можете да изтриете собствения си акаунт, докато сте влезли.")
            return

        # Проверка дали опитваме да изтрием единствения админ
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
                self.user_updated.emit()  # Излъчваме сигнал за актуализация
            else:
                QMessageBox.critical(self, "Грешка", message)

    def clear_form(self):
        self.username_input.clear()
        self.password_input.clear()
        self.is_admin_combo.setCurrentIndex(0)  # По подразбиране "Не"
        self.username_input.setEnabled(True)  # Активираме полето за потребителско име
        self.selected_user = None

        self.add_button.setEnabled(True)
        self.update_button.setEnabled(False)
        self.delete_button.setEnabled(False)

# Пример за изпълнение (само за тестване на диалога самостоятелно)
if __name__ == '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    # За тестване, приемете, че "admin" е текущо влезлият потребител
    dialog = UserManagementDialog(current_username="admin")
    dialog.exec_()
    sys.exit(app.exec_())