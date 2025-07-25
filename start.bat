chcp 65001
@echo off
REM Проверява дали Python е инсталиран и е в PATH
"C:\Python\python.exe" --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python не е намерен. Моля, инсталирайте Python 3.10 или по-нова от python.org
    pause
    EXIT /B 1
)

REM Активира виртуалната среда
echo Активиране на виртуална среда...
IF EXIST "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) ELSE IF EXIST "venv\Scripts\Activate.ps1" (
    REM За PowerShell, трябва да се извика директно чрез python
    REM или да се промени политиката за изпълнение
    echo PowerShell виртуална среда намерена, но изисква ръчно активиране или промяна на политиката.
    echo Опитайте ръчно: .\.venv\Scripts\Activate.ps1
    echo Или: "C:\Python\python.exe" -m pip install PyQt5 opencv-python passlib onvif-zeep numpy ipaddress
) ELSE (
    echo Виртуална среда не е намерена. Създаване на нова...
    "C:\Python\python.exe" -m venv venv
    call "venv\Scripts\activate.bat"
)

REM Проверява и инсталира нужните библиотеки, ако е необходимо
echo Проверка на зависимостите...
"C:\Python\python.exe" -m pip install PyQt5 opencv-python passlib onvif-zeep numpy ipaddress
IF %ERRORLEVEL% NEQ 0 (
    echo Неуспешна инсталация на зависимости. Моля, проверете интернет връзката или ръчно инсталирайте.
    pause
    EXIT /B 1
)

REM Стартира главното приложение и записва изхода в текстов файл
echo Стартиране на TSA-Security...
start "" "C:\Python\python.exe" camera_viewer.py > app_log.txt 2>&1