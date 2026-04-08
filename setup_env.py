import os
import sys
import subprocess
import platform
import shutil

REQUIRED_PYTHON = (3, 12)

def run_command(command, description, allow_failure=False):
    print(f"\n[INFO] {description}...")
    try:
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        if allow_failure:
            print(f"[WARN] Optional step failed: {e}")
            return False
        print(f"[ERROR] {e}")
        return False
    return True


def get_python_version(python_exe):
    try:
        result = subprocess.run(
            [python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            check=True,
            capture_output=True,
            text=True,
        )
        major, minor = result.stdout.strip().split(".")
        return int(major), int(minor)
    except Exception:
        return None


def get_python_312_executable():
    current = (sys.version_info.major, sys.version_info.minor)
    if current == REQUIRED_PYTHON:
        return sys.executable

    if platform.system() == "Windows":
        candidates = ["py -3.12", "python3.12"]
    else:
        candidates = ["python3.12", "python3"]

    for candidate in candidates:
        version_cmd = f'{candidate} -c "import sys; print(f\'{sys.version_info.major}.{sys.version_info.minor}\')"'
        try:
            result = subprocess.run(version_cmd, shell=True, check=True, capture_output=True, text=True)
            if result.stdout.strip() == "3.12":
                return candidate
        except Exception:
            continue
    return None

def setup():
    venv_dir = ".venv"
    python_exe = get_python_312_executable()

    print("=" * 60)
    print("  TTS-EvelenLabs Environment Setup (VideoLingo Style)")
    print("=" * 60)

    if python_exe is None:
        print("[ERROR] Python 3.12 not found.")
        print("Please install Python 3.12 and make sure 'py -3.12' or 'python3.12' is available.")
        return

    # 1. Kiểm tra venv hiện tại, nếu không phải 3.12 thì xóa để tạo lại
    if platform.system() == "Windows":
        existing_venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        existing_venv_python = os.path.join(venv_dir, "bin", "python")

    if os.path.exists(existing_venv_python):
        current_venv_version = get_python_version(existing_venv_python)
        if current_venv_version != REQUIRED_PYTHON:
            print(f"[WARN] .venv uses Python {current_venv_version}, expected 3.12. Recreating environment.")
            shutil.rmtree(venv_dir, ignore_errors=True)
    elif os.path.exists(venv_dir):
        shutil.rmtree(venv_dir, ignore_errors=True)

    if not os.path.exists(venv_dir):
        run_command(f"{python_exe} -m venv {venv_dir}", "Creating .venv with Python 3.12")
    else:
        print("[OK] .venv is ready with Python 3.12.")

    # 2. Định nghĩa python path trong venv
    if platform.system() == "Windows":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")

    # 3. Nâng cấp pip và cài requirements
    run_command(f"{venv_python} -m pip install --upgrade pip", "Upgrading pip")
    run_command(f"{venv_python} -m pip install -r requirements.txt", "Installing requirements.txt")

    # 4. Tải các model spaCy
    run_command(f"{venv_python} -m spacy download en_core_web_sm", "Downloading spaCy English model")
    # Model tiếng Việt có thể không cài được trực tiếp qua spacy download tùy phiên bản, 
    # nhưng chúng ta vẫn thử hoặc dùng regex fallback đã có trong code.
    print("\n[INFO] Checking Vietnamese spaCy model...")
    run_command(
        f"{venv_python} -m spacy download vi_core_news_sm",
        "Trying to download Vietnamese spaCy model (optional)",
        allow_failure=True,
    )

    print("\n" + "=" * 60)
    print("[DONE] Setup completed.")
    print("To start the app, run: OneKeyStart.bat or")
    print(f"  {venv_dir}\\Scripts\\streamlit run app.py")
    print("=" * 60)

if __name__ == "__main__":
    setup()
