@echo off
set VENV_DIR=.venv

IF NOT EXIST %VENV_DIR% (
    echo [🚀] Đang cài đặt môi trường cho lần đầu chạy...
    python setup_env.py
) ELSE (
    echo [✅] Môi trường ảo đã sẵn sàng.
)

echo [🎮] Đang khởi động TTS-EvelenLabs...
%VENV_DIR%\Scripts\streamlit run app.py

pause
