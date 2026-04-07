# 📖 TTS-EvelenLabs (VideoLingo Integration)

Hệ thống chuyển đổi văn bản sang âm thanh (TTS) cho các chương Kinh Thánh với kỹ thuật **Chunking & Precise SRT Alignment** kế thừa từ **VideoLingo**.

---

## 🌟 Tính năng nổi bật

-   **VideoLingo Semantic Chunking**: Tự động chia nhỏ văn bản thành các cụm ngữ nghĩa hợp lý, đảm bảo phụ đề không bị cắt ngang câu.
-   **Precise Alignment**: Tính toán thời gian phụ đề dựa trên độ dài thực tế của từng đoạn âm thanh sinh ra (khớp 100%).
-   **Đa dạng Engine**: Hỗ trợ **ElevenLabs** (Premium) và **Edge-TTS** (Miễn phí).
-   **Giao diện One-Click**: Khởi động dễ dàng bằng file `.bat` trên Windows.
-   **Hỗ trợ đa ngôn ngữ**: Tối ưu cho tiếng Anh (spaCy) và tiếng Việt (Regex/spaCy).

---

## 🚀 Cài đặt nhanh (Windows)

1.  Đảm bảo bạn đã cài đặt **Python 3.8+**.
2.  Chỉ cần double-click vào file **`OneKeyStart.bat`**.
    -   Hệ thống sẽ tự động tạo môi trường ảo `.venv`.
    -   Tự động cài đặt các thư viện từ `requirements.txt`.
    -   Tải các mô hình ngôn ngữ cần thiết.
    -   Khởi động giao diện web trên trình duyệt.

*(Lưu ý: Bạn nên có `ffmpeg` được cài đặt trong PATH của Windows để xử lý âm thanh tốt nhất)*

---

## 🛠 Cách sử dụng

1.  **Nhập liệu**: Thêm các file Kinh Thánh `.txt` vào thư mục cần xử lý.
2.  **Cấu hình (Sidebar)**:
    -   Chọn **Provider** (Mặc định: `edge-tts` cho miễn phí).
    -   Chọn **Subtitle Source**: "VideoLingo Mode (Tách chunk + Ghép audio)".
3.  **Chạy thử**: Nhấn **Quick Demo** để kiểm tra 2 chương đầu tiên.
4.  **Tạo Full Pack**: Nhấn **Gen Full Pack** để tạo toàn bộ bộ audio và phụ đề.

---

## 📁 Cấu trúc thư mục

-   `modules/segmenter.py`: Xử lý chia câu ngữ nghĩa (Local/NLP).
-   `modules/voice_gen.py`: Quy trình sinh âm thanh từng phần và tính timeline (VideoLingo Technique).
-   `setup_env.py`: Script cài đặt tự động.
-   `OneKeyStart.bat`: Phím khởi động nhanh.

---

## 📝 Giấy phép

Project này được phát triển dựa trên các kỹ thuật mã nguồn mở từ VideoLingo và các công cụ TTS hiện đại. 

---

*Chúc bạn có những trải nghiệm tuyệt vời với TTS-EvelenLabs!*
