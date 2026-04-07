import os
import sys
import subprocess
import platform

def run_command(command, description):
    print(f"\n[🚀] {description}...")
    try:
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Lỗi: {e}")
        return False
    return True

def setup():
    venv_dir = ".venv"
    python_exe = sys.executable
    
    print("=" * 60)
    print("  TTS-EvelenLabs Environment Setup (VideoLingo Style)")
    print("=" * 60)

    # 1. Tạo venv nếu chưa có
    if not os.path.exists(venv_dir):
        run_command(f"{python_exe} -m venv {venv_dir}", "Đang tạo môi trường ảo (.venv)")
    else:
        print("✅ Môi trường ảo (.venv) đã tồn tại.")

    # 2. Định nghĩa python path trong venv
    if platform.system() == "Windows":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")

    # 3. Nâng cấp pip và cài requirements
    run_command(f"{venv_python} -m pip install --upgrade pip", "Nâng cấp pip")
    run_command(f"{venv_python} -m pip install -r requirements.txt", "Cài đặt các thư viện từ requirements.txt")

    # 4. Tải các model spaCy
    run_command(f"{venv_python} -m spacy download en_core_web_sm", "Tải model tiếng Anh (spaCy)")
    # Model tiếng Việt có thể không cài được trực tiếp qua spacy download tùy phiên bản, 
    # nhưng chúng ta vẫn thử hoặc dùng regex fallback đã có trong code.
    print("\n[💡] Đang kiểm tra model tiếng Việt...")
    run_command(f"{venv_python} -m spacy download vi_core_news_sm", "Thử tải model tiếng Việt (Nếu lỗi cũng không sao, app đã có Regex fallback)")

    print("\n" + "=" * 60)
    print("🎉 Cài đặt hoàn tất! ")
    print("Để khởi động app, hãy chạy: OneKeyStart.bat hoặc")
    print(f"  {venv_dir}\\Scripts\\streamlit run app.py")
    print("=" * 60)

if __name__ == "__main__":
    setup()
