@echo off
setlocal

set "PROJECT_DIR=C:\Users\user\journal-briefing-test"
set "PYTHON_EXE=C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\run.log"

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

cd /d "%PROJECT_DIR%"

echo ============================================== >> "%LOG_FILE%"
echo [%date% %time%] test_agent1.py 시작 >> "%LOG_FILE%"
"%PYTHON_EXE%" test_agent1.py >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] test_agent1.py 실패 ^(exit code %ERRORLEVEL%^). send_briefing.py는 건너뜁니다. >> "%LOG_FILE%"
    exit /b %ERRORLEVEL%
)

echo [%date% %time%] test_agent1.py 성공. send_briefing.py 시작 >> "%LOG_FILE%"
"%PYTHON_EXE%" send_briefing.py >> "%LOG_FILE%" 2>&1
echo [%date% %time%] send_briefing.py 완료 ^(exit code %ERRORLEVEL%^) >> "%LOG_FILE%"

endlocal
