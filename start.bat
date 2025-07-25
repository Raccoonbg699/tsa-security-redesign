chcp 65001
@echo off

REM Проверява дали Python е наличен и работи
REM Опитва 'python' и 'py' като алтернативи
SET "PYTHON_EXE="
python --version >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    SET "PYTHON_EXE=python"
) ELSE (
    py --version >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        SET "PYTHON_EXE=py"
    )
)

IF NOT DEFINED PYTHON_EXE (
    echo Python не е намерен в системния PATH.
    echo Моля, инсталирайте Python 3.10 или по-нова от python.org
    echo или се уверете, че "python" или "py" са добавени към PATH.
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
    echo Моля, стартирайте програмата от PowerShell с: .\.venv\Scripts\Activate.ps1
    echo и след това: python camera_viewer.py
    pause
    EXIT /B 1
) ELSE (
    echo Виртуална среда не е намерена. Създаване на нова...
    %PYTHON_EXE% -m venv venv
    call "venv\Scripts\activate.bat"
)

REM Проверява и инсталира нужните библиотеки, ако е необходимо
echo Проверка на зависимостите...
%PYTHON_EXE% -m pip install PyQt5 opencv-python passlib onvif-zeep numpy ipaddress
IF %ERRORLEVEL% NEQ 0 (
    echo Неуспешна инсталация на зависимости. Моля, проверете интернет връзката или ръчно инсталирайте.
    pause
    EXIT /B 1
)

REM Стартира главното приложение без конзолен прозорец
echo Стартиране на TSA-Security...
start "" %PYTHON_EXE% camera_viewer.py > app_log.txt 2>&1

REM Ако искате конзолата да остане отворена за дебъгване, закоментирайте горния ред и разкоментирайте долния:
REM %PYTHON_EXE% camera_viewer.py