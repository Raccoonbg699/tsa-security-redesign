import json
from passlib.hash import pbkdf2_sha256  # Използваме pbkdf2_sha256 за хеширане на пароли
from pathlib import Path
import os


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
        # Добавяне на админ потребител, ако няма такъв
        if not any(user.is_admin for user in self.users.values()):
            if "admin" not in self.users:
                self.add_user("admin", "adminpass", is_admin=True)
                print("Default admin user 'admin' with password 'adminpass' created.")
            else:
                # Ако потребител 'admin' съществува, но не е админ, го правим админ
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
                    return {}  # Връща празен речник, ако файлът е повреден
        return {}

    def _save_users(self):
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump({username: user.to_dict() for username, user in self.users.items()}, f, indent=4,
                      ensure_ascii=False)

    def add_user(self, username, password, is_admin=False):
        if username in self.users:
            return False, "Потребителското име вече съществува."

        # Хеширане на паролата
        password_hash = pbkdf2_sha256.hash(password)
        new_user = User(username, password_hash, is_admin)
        self.users[username] = new_user
        self._save_users()
        return True, "Потребителят е добавен успешно."

    def verify_password(self, username, password):
        user = self.users.get(username)
        if user:
            # Проверка на хешираната парола
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


# Пример за употреба (може да бъде закоментиран или изтрит след тестване)
if __name__ == "__main__":
    # Уверете се, че users.json е в същата директория или укажете пътя
    # Ако users.json не съществува, ще бъде създаден
    user_manager = UserManager()

    # Добавяне на тестови потребители
    # user_manager.add_user("testuser", "password123")
    # user_manager.add_user("testadmin", "adminpass", is_admin=True)

    # Проверка на пароли
    # print(f"admin:adminpass -> {user_manager.verify_password('admin', 'adminpass')}")
    # print(f"testuser:wrongpass -> {user_manager.verify_password('testuser', 'wrongpass')}")

    # Всички потребители
    # print("\nAll users:")
    # for user in user_manager.get_all_users():
    #     print(f"  - {user.username} (Admin: {user.is_admin})")

    # Актуализиране на потребител
    # user_manager.update_user("testuser", new_password="newpassword")
    # print(f"testuser:newpassword -> {user_manager.verify_password('testuser', 'newpassword')}")

    # Изтриване на потребител
    # user_manager.delete_user("testuser")
    # print("\nUsers after deletion:")
    # for user in user_manager.get_all_users():
    #     print(f"  - {user.username} (Admin: {user.is_admin})")