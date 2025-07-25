from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QLabel, QMessageBox, QHBoxLayout, QFrame, QWidget # Добавено QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap, QImage
from user_manager import UserManager
from pathlib import Path
import os

# Дефиниране на цветове
BG_COLOR = "#2C2C2C" # Тъмно сиво за фон
FIELD_BG_COLOR = "#3A3A3A" # По-светло сиво за полета
TEXT_COLOR = "#F0F0F0" # Светъл текст
ACCENT_COLOR = "#FF8C00" # Оранжев акцент
BORDER_COLOR = "#505050" # Цвят на рамката

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tsa-Security - Вход в системата")
        self.setFixedSize(600, 400) # Фиксиран размер на прозореца за вход
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
                background-color: #E67E00; /* По-тъмен оранжев при hover */
            }}
            QPushButton:pressed {{
                background-color: #CC7000; /* Още по-тъмен оранжев при натискане */
            }}
            #forgotPasswordLabel {{ /* ID за линка "Забравена парола" */
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

        # Заглавие
        title_label = QLabel("Вход в акаунта")
        title_label.setFont(QFont("Segoe UI", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # Форма за вход
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

        # Бутон за вход
        login_button = QPushButton("Вход")
        login_button.setCursor(Qt.PointingHandCursor)
        login_button.clicked.connect(self.attempt_login)
        main_layout.addWidget(login_button)

        # Линк "Забравили сте паролата?"
        forgot_password_label = QLabel("<a href='#' id='forgotPasswordLabel'>Забравили сте паролата?</a>")
        forgot_password_label.setAlignment(Qt.AlignCenter)
        forgot_password_label.setOpenExternalLinks(False) # Важно: да не отваря външни линкове
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
            self.is_admin = user.is_admin if user else False # Уверете се, че user не е None
            self.accept() # Затваря диалога успешно
        else:
            QMessageBox.critical(self, "Грешка при вход", "Невалидно потребителско име или парола.")
            self.password_input.clear() # Изчиства паролата при грешен опит

    def show_forgot_password_message(self):
        QMessageBox.information(self, "Забравена парола",
                                "Моля, свържете се с администратора на системата, за да възстановите паролата си.")

# Пример за изпълнение (само за тестване на диалога самостоятелно)
if __name__ == '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication

    # Уверете се, че user_manager.py е в същата директория
    # или добавете пътя до него
    # import os
    # sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    app = QApplication(sys.argv)
    login_dialog = LoginDialog()
    if login_dialog.exec_() == QDialog.Accepted:
        print(f"Вход успешен за потребител: {login_dialog.username}")
        print(f"Потребителят е администратор: {login_dialog.is_admin}")
    else:
        print("Входът е отменен.")
    sys.exit(app.exec_())