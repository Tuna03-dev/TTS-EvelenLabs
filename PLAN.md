# Bible Audio/Video Pipeline — Project Plan

> Mục tiêu: Tạo video Bible audio chính xác **3:33:33**, subtitle tùy chỉnh, ảnh động MP4 loop, export tự động từ Streamlit.

---

## Tổng quan kiến trúc

```
processor.py        → fetch & chọn chapters (word budget)
voice_gen.py        → TTS từng chapter → audio + SRT
audio_engine.py     → stitch audio + subtitle → đúng 3:33:33
subtitle_config.py  → style config (font/size/màu/vị trí)
srt_to_ass.py       → convert SRT → ASS với style inject
video_builder.py    → FFmpeg: MP4 loop + ASS subtitle → output.mp4
app.py (Streamlit)  → UI: drag video, subtitle editor, export button
```

---

## Phase 1 — Sửa audio ngắt nghỉ tự nhiên

**Vấn đề:** TTS nhận từng chunk nhỏ (~25 từ) → mỗi chunk xử lý độc lập → intonation sai, giọng bị ngắt cứng giữa câu.

### 1.1 Sửa `segmenter.py` — chunk theo câu hoàn chỉnh

**File:** `modules/segmenter.py`

**Thêm hàm mới:**

```python
def chunk_by_sentences(sentences: List[str], max_chars: int = 300) -> List[str]:
    """
    Nhóm câu hoàn chỉnh vào chunks, KHÔNG cắt giữa câu.
    max_chars lớn hơn → TTS có nhiều context → intonation tự nhiên hơn.
    """
    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        if current and current_len + len(sent) + 1 > max_chars:
            chunks.append(" ".join(current))
            current = [sent]
            current_len = len(sent)
        else:
            current.append(sent)
            current_len += len(sent) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks
```

**Sửa `segment_text()`:**

```python
def segment_text(text, lang="en", max_words=30, max_chars=140, sentence_mode=False):
    sents = split_sentences(text, lang)
    if sentence_mode:
        return chunk_by_sentences(sents, max_chars=300)  # mode mới
    return chunk_sentences(sents, max_words=max_words, max_chars=max_chars)
```

**Sửa cuối mỗi chunk — thêm dấu phẩy nếu chưa có dấu câu:**

```python
# Trong chunk_sentences(), trước khi append:
text = " ".join(current_chunk).strip()
if text and text[-1] not in ".!?,;:":
    text += ","   # TTS pause nhẹ, không xuống giọng hẳn
chunks.append(text)
```

---

### 1.2 Sửa `voice_gen.py` — micro-silence thông minh khi ghép

**File:** `modules/voice_gen.py`

**Thêm helper:**

```python
def _get_natural_pause_ms(text: str) -> int:
    """Tính pause dựa theo dấu câu cuối chunk."""
    text = text.strip()
    if not text:
        return 0
    last = text[-1]
    if last in ".!?":
        return 380    # kết thúc câu
    elif last in ",;:":
        return 140    # dấu phẩy / mệnh đề con
    else:
        return 60     # không dấu — nối mềm
```

**Sửa merge loop trong `generate_chunked_speech_parallel()`:**

```python
# Thay:
combined_audio += segment_audio
current_offset += duration_sec

# Thành:
pause_ms = _get_natural_pause_ms(chunk_text)
combined_audio += segment_audio
if pause_ms > 0:
    combined_audio += AudioSegment.silent(duration=pause_ms)

current_offset += duration_sec + pause_ms / 1000.0

# Cập nhật SRT end time bao gồm pause
srt_segments[-1]["end"] = current_offset
```

**Tăng stability ElevenLabs:**

```python
"voice_settings": {
    "stability": 0.75,          # tăng từ 0.5
    "similarity_boost": 0.75,
    "style": 0.35,
    "use_speaker_boost": True
}
```

**edge-tts — đọc chậm hơn 5% mặc định:**

```python
DEFAULT_EDGE_RATE = "-5%"   # thay "+0%"
```

---

### 1.3 Sử dụng `sentence_mode=True` trong `voice_gen.py`

**Sửa tất cả chỗ gọi `segment_text()`:**

```python
chunks = segment_text(
    text,
    lang=lang,
    max_words=max_words,
    max_chars=max_chars,
    sentence_mode=True      # thêm dòng này
)
```

---

## Phase 2 — Đạt đúng 3:33:33 với stretch mềm

**Target:** `12,813 giây` (3×3600 + 33×60 + 33)

**Nguyên tắc:**
- Không stretch >8% — tai người bắt đầu nghe lạ
- Kết hợp stretch nhẹ + silence tự nhiên
- Không bao giờ cắt vào audio thật

### 2.1 Thêm dependency

```bash
pip install pyrubberband soundfile
# Linux cần: apt install rubberband-cli
```

### 2.2 Hàm `time_stretch_audio()`

**File:** `modules/audio_engine.py` — thêm vào đầu file

```python
import pyrubberband as pyrb
import numpy as np

def time_stretch_audio(audio_segment: AudioSegment, target_seconds: float) -> AudioSegment:
    """
    Co giãn audio đến target_seconds.
    Giữ nguyên pitch (WSOLA algorithm qua rubberband).
    Chỉ an toàn trong khoảng ±8%.
    """
    current_seconds = len(audio_segment) / 1000.0
    if current_seconds <= 0:
        return audio_segment

    ratio = current_seconds / target_seconds   # >1=nén, <1=kéo dài
    stretch_pct = abs(1 - ratio) * 100

    if stretch_pct < 0.1:
        return audio_segment   # quá nhỏ, bỏ qua

    if stretch_pct > 8.0:
        raise ValueError(f"Stretch {stretch_pct:.1f}% vượt ngưỡng an toàn 8%")

    sr = audio_segment.frame_rate
    channels = audio_segment.channels
    samples = np.array(audio_segment.get_array_of_samples(), dtype=np.float32)

    if channels == 2:
        samples = samples.reshape((-1, 2))

    stretched = pyrb.time_stretch(samples, sr, ratio)

    if channels == 2:
        stretched = stretched.flatten()

    stretched_int = np.clip(stretched, -32768, 32767).astype(np.int16)
    return AudioSegment(
        stretched_int.tobytes(),
        frame_rate=sr,
        sample_width=2,
        channels=channels
    )
```

### 2.3 Hàm `detect_stretch_zone()` — log trước khi stitch

```python
def detect_stretch_zone(total_raw: float, target: float) -> dict:
    """Phân tích delta và quyết định strategy."""
    delta = target - total_raw
    pct = abs(delta / total_raw) * 100

    if pct < 1.0:
        zone = "SILENCE_ONLY"
        stretch_share = 0.0
    elif pct <= 5.0:
        zone = "SAFE"
        stretch_share = 0.7
    elif pct <= 8.0:
        zone = "ACCEPTABLE"
        stretch_share = 0.4
    else:
        zone = "DANGER"
        stretch_share = 0.0

    return {
        "delta_seconds": delta,
        "delta_pct": pct,
        "zone": zone,
        "stretch_share": stretch_share,
        "silence_share": 1.0 - stretch_share,
        "action": "stretch + silence" if stretch_share > 0 else
                  "silence only" if zone != "DANGER" else
                  "ERROR: add/remove chapters"
    }
```

### 2.4 Sửa `stitch_video_pack()` — logic chính

```python
TARGET_SECONDS = 12_813.0   # 3:33:33

def stitch_video_pack(pack_dir, chapters, target_seconds=TARGET_SECONDS):
    audio_dir = os.path.join(pack_dir, "audio")
    final_dir = os.path.join(pack_dir, "final")
    os.makedirs(final_dir, exist_ok=True)

    # --- Bước 1: Load audio, đo actual duration ---
    audio_segments = []
    total_raw = 0.0
    for i, ch in enumerate(chapters):
        path = os.path.join(audio_dir, ch["audio_file"])
        validate_audio_file(path)
        seg = AudioSegment.from_file(path)
        audio_segments.append(seg)
        total_raw += len(seg) / 1000.0

    # --- Bước 2: Phân tích delta ---
    info = detect_stretch_zone(total_raw, target_seconds)
    print(f"[stitch] Delta: {info['delta_seconds']:.1f}s "
          f"({info['delta_pct']:.2f}%) — Zone: {info['zone']}")

    if info["zone"] == "DANGER":
        raise ValueError(
            f"Delta {info['delta_pct']:.1f}% quá lớn để xử lý tự động.\n"
            f"Hãy thêm/bớt chapters trong processor.py (SAFE_BUDGET)."
        )

    delta = info["delta_seconds"]
    n = len(chapters)

    # --- Bước 3: Tính stretch ratio và silence ---
    stretch_delta  = delta * info["stretch_share"]
    silence_delta  = delta * info["silence_share"]

    # Stretch ratio áp dụng đều cho tất cả chapters
    stretch_target_total = total_raw + stretch_delta
    stretch_ratio = total_raw / stretch_target_total  # rubberband ratio

    # Silence chia: 60% tail cuối chapter, 40% gap giữa chapters
    tail_ms = int((silence_delta * 0.6 / n) * 1000)
    gap_ms  = int((silence_delta * 0.4 / max(n - 1, 1)) * 1000)

    # --- Bước 4: Ghép ---
    final_audio = AudioSegment.empty()
    for i, seg in enumerate(audio_segments):
        # Stretch từng chapter riêng
        if info["stretch_share"] > 0:
            chapter_target = len(seg) / 1000.0 / stretch_ratio
            try:
                seg = time_stretch_audio(seg, chapter_target)
            except ValueError as e:
                print(f"[stitch] Warning chapter {i+1}: {e} — skip stretch")

        final_audio += seg
        final_audio += AudioSegment.silent(duration=tail_ms)
        if i < n - 1:
            final_audio += AudioSegment.silent(duration=gap_ms)

    # --- Bước 5: Trim/pad để chính xác tuyệt đối ---
    target_ms  = int(target_seconds * 1000)
    current_ms = len(final_audio)
    diff_ms    = target_ms - current_ms

    if diff_ms > 0:
        final_audio += AudioSegment.silent(duration=diff_ms)
    elif diff_ms < 0:
        # Chỉ cắt vào silence ở cuối — không bao giờ chạm audio thật
        final_audio = final_audio[:target_ms]

    # Verify
    actual = len(final_audio) / 1000.0
    assert abs(actual - target_seconds) < 0.05, \
        f"Duration mismatch: {actual:.3f}s vs {target_seconds}s"

    # --- Bước 6: Export ---
    out_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    final_audio.export(out_path, format="mp3", bitrate="128k")
    validate_audio_file(out_path)

    print(f"[stitch] Done. Final duration: {actual:.3f}s")
    return out_path
```

### 2.5 Sửa `processor.py` — để lại buffer an toàn

```python
# Thay TARGET_DURATION_SECONDS trực tiếp bằng SAFE_BUDGET
SAFE_BUDGET = TARGET_DURATION_SECONDS - 200   # buffer 200s cho silence + stretch

# Trong generate_video_pack(), đổi điều kiện dừng:
if total_duration >= SAFE_BUDGET:
    break
```

---

## Phase 3 — Subtitle customizable (ASS format)

### 3.1 Tạo `modules/subtitle_config.py`

```python
from dataclasses import dataclass, field

def hex_to_ass(hex_color: str, alpha: int = 0) -> str:
    """Convert #RRGGBB → &HAABBGGRR (ASS format)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}"

def position_to_alignment(pos: str) -> int:
    return {"Bottom center": 2, "Top center": 8, "Middle center": 5}.get(pos, 2)

@dataclass
class SubtitleStyle:
    font_family: str   = "Arial"
    font_size: int     = 20
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    bold: bool         = False
    outline: float     = 2.0
    shadow: float      = 1.0
    has_box: bool      = True        # nền mờ sau chữ
    box_alpha: int     = 128         # 0=trong suốt, 255=đục
    alignment: int     = 2           # 2=bottom, 8=top, 5=middle
    margin_v: int      = 30

    def to_ass_style_line(self) -> str:
        bold_val    = -1 if self.bold else 0
        back_color  = f"&H{self.box_alpha:02X}000000" if self.has_box else "&H00000000"
        border_style = 3 if self.has_box else 1
        return (
            f"Style: Default,"
            f"{self.font_family},{self.font_size},"
            f"{hex_to_ass(self.primary_color)},"
            f"{hex_to_ass(self.outline_color)},"
            f"{back_color},"
            f"{bold_val},0,0,0,"
            f"100,100,0,0,"
            f"{border_style},"
            f"{self.outline},{self.shadow},"
            f"{self.alignment},"
            f"10,10,{self.margin_v},0"
        )
```

### 3.2 Tạo `modules/srt_to_ass.py`

```python
import re
from modules.subtitle_config import SubtitleStyle

def _srt_time_to_ass(ts: str) -> str:
    """00:01:23,456 → 0:01:23.46"""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":", 2)
    s, ms = rest.split(".")
    cs = int(ms[:2]) if len(ms) >= 2 else 0
    return f"{int(h)}:{m}:{s}.{cs:02d}"

def srt_to_ass(srt_path: str, style: SubtitleStyle) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{style.to_ass_style_line()}

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    events = []
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        time_line = next((l for l in lines if "-->" in l), None)
        if not time_line:
            continue
        start_str, end_str = time_line.split("-->")
        text_lines = lines[lines.index(time_line) + 1:]
        text = "\\N".join(l.strip() for l in text_lines if l.strip())
        start = _srt_time_to_ass(start_str.strip())
        end   = _srt_time_to_ass(end_str.strip())
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"
```

---

## Phase 4 — Video export với FFmpeg

### 4.1 Tạo `modules/video_builder.py`

```python
import subprocess
import os
import tempfile
from modules.subtitle_config import SubtitleStyle
from modules.srt_to_ass import srt_to_ass
from modules.audio_engine import get_audio_duration

def build_video(
    pack_dir: str,
    video_clip_path: str,
    style: SubtitleStyle,
    output_name: str = "output_video.mp4",
    resolution: str = "1920:1080",
    video_crf: int = 23,
    preset: str = "fast",
    burn_subtitles: bool = True,
) -> str:
    final_dir  = os.path.join(pack_dir, "final")
    audio_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    srt_path   = os.path.join(final_dir, "final_subtitles.srt")
    output     = os.path.join(final_dir, output_name)

    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")
    if not os.path.exists(srt_path):
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    duration = get_audio_duration(audio_path)

    # Convert SRT → ASS với style
    ass_path = srt_path.replace(".srt", "_styled.ass")
    ass_content = srt_to_ass(srt_path, style)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    # Escape path cho FFmpeg filter (Windows & Linux)
    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

    # Build video filter
    scale_filter = f"scale={resolution}:force_original_aspect_ratio=decrease,pad={resolution}:(ow-iw)/2:(oh-ih)/2"
    if burn_subtitles:
        vf = f"{scale_filter},ass='{ass_escaped}'"
    else:
        vf = scale_filter

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",      # loop video clip
        "-i", video_clip_path,
        "-i", audio_path,
        "-t", str(duration),       # đúng bằng audio duration
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(video_crf),
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",  # streaming-friendly
        output,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-2000:]}")

    return output
```

---

## Phase 5 — Streamlit UI

### 5.1 Thêm tab Export Video vào `app.py`

```python
# Trong app.py, thêm tab mới:
from modules.subtitle_config import SubtitleStyle, hex_to_ass, position_to_alignment
from modules.video_builder import build_video

def render_export_tab(pack_dir: str, chapters: list):
    st.header("Export video")

    # --- Upload video nền ---
    st.subheader("1. Video nền (MP4 loop)")
    use_single = st.checkbox("Dùng 1 video cho toàn bộ", value=True)

    video_file = None
    if use_single:
        video_file = st.file_uploader(
            "Kéo video MP4 vào đây", type=["mp4", "mov", "webm"],
            help="Video sẽ được loop tự động theo đúng độ dài audio"
        )
    else:
        st.info("Multi-video mode: gán riêng từng chapter (coming soon)")

    # --- Subtitle style editor ---
    st.subheader("2. Subtitle style")

    col1, col2, col3 = st.columns(3)
    with col1:
        font    = st.selectbox("Font", ["Arial", "Georgia", "Times New Roman",
                                        "Roboto", "Impact", "Verdana"])
        size    = st.slider("Size", 12, 56, 22)
        bold    = st.checkbox("Bold", False)
    with col2:
        color         = st.color_picker("Màu chữ", "#FFFFFF")
        outline_color = st.color_picker("Màu outline", "#000000")
        outline_size  = st.slider("Outline", 0.0, 5.0, 2.0, 0.5)
    with col3:
        position  = st.selectbox("Vị trí", ["Bottom center", "Top center", "Middle center"])
        margin    = st.slider("Khoảng cách mép (px)", 10, 120, 35)
        has_box   = st.checkbox("Nền mờ sau chữ", True)
        box_alpha = st.slider("Độ đục nền", 0, 255, 140) if has_box else 0

    # Preview
    st.markdown(
        f"<div style='padding:8px 12px;background:#222;display:inline-block;border-radius:6px'>"
        f"<span style='font-family:{font};font-size:{size * 0.6:.0f}px;color:{color};"
        f"font-weight:{\"bold\" if bold else \"normal\"};"
        f"text-shadow:1px 1px 3px {outline_color}'>"
        f"In the beginning God created the heaven and the earth.</span></div>",
        unsafe_allow_html=True
    )

    # --- Export ---
    st.subheader("3. Export")
    preset = st.selectbox("FFmpeg preset", ["fast", "medium", "slow"],
                          help="slow = chất lượng cao hơn, encode lâu hơn")
    crf    = st.slider("Chất lượng video (CRF)", 18, 35, 23,
                       help="Thấp hơn = chất lượng cao hơn, file lớn hơn")

    if st.button("Export MP4", type="primary", disabled=(video_file is None)):
        style = SubtitleStyle(
            font_family   = font,
            font_size     = size,
            primary_color = color,
            outline_color = outline_color,
            bold          = bold,
            outline       = outline_size,
            has_box       = has_box,
            box_alpha     = box_alpha,
            alignment     = position_to_alignment(position),
            margin_v      = margin,
        )

        # Lưu video upload ra file tạm
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(video_file.read())
            clip_path = tmp.name

        with st.spinner("Đang render video... (có thể mất vài phút)"):
            try:
                out = build_video(pack_dir, clip_path, style,
                                  preset=preset, video_crf=crf)
                st.success(f"Done! {out}")
                st.video(out)
            except Exception as e:
                st.error(f"Lỗi: {e}")
            finally:
                os.unlink(clip_path)
```

---

## Danh sách file thay đổi

| File | Hành động | Phase |
|---|---|---|
| `modules/segmenter.py` | Thêm `chunk_by_sentences()`, sửa `chunk_sentences()` thêm dấu phẩy | 1.1 |
| `modules/voice_gen.py` | Thêm `_get_natural_pause_ms()`, sửa merge loop, tăng stability | 1.2 & 1.3 |
| `modules/audio_engine.py` | Thêm `time_stretch_audio()`, `detect_stretch_zone()`, sửa `stitch_video_pack()` | 2.2–2.4 |
| `processor.py` | Đổi `TARGET` → `SAFE_BUDGET = TARGET - 200` | 2.5 |
| `modules/subtitle_config.py` | Tạo mới — `SubtitleStyle` dataclass | 3.1 |
| `modules/srt_to_ass.py` | Tạo mới — convert SRT → ASS | 3.2 |
| `modules/video_builder.py` | Tạo mới — FFmpeg orchestrator | 4.1 |
| `app.py` | Thêm tab Export Video | 5.1 |

---

## Dependencies cần thêm

```bash
pip install pyrubberband soundfile

# Linux
sudo apt install rubberband-cli

# macOS
brew install rubberband
```

---

## Thứ tự implement

```
Phase 1 (ngắt nghỉ tự nhiên)   → test nghe thử → Phase 2 (duration)
                                                  ↓
Phase 3 (subtitle ASS)  →  Phase 4 (FFmpeg)  →  Phase 5 (UI)
```

Không cần làm song song — mỗi phase độc lập và có thể test riêng trước khi sang phase tiếp theo.

---

## Kiểm tra / test nhanh

```python
# Test Phase 1 — nghe thử output chunk
python -c "
from modules.segmenter import segment_text
text = open('sample_chapter.txt').read()
chunks = segment_text(text, sentence_mode=True)
print(f'{len(chunks)} chunks')
for c in chunks[:5]: print(repr(c))
"

# Test Phase 2 — verify duration
python -c "
from modules.audio_engine import get_audio_duration
d = get_audio_duration('output/video_pack_xxx/final/full_audio_3h33.mp3')
print(f'Duration: {d:.3f}s (target: 12813.000s, delta: {d-12813:.3f}s)')
"

# Test Phase 3 — xem ASS output
python -c "
from modules.subtitle_config import SubtitleStyle
from modules.srt_to_ass import srt_to_ass
style = SubtitleStyle(font_size=24, primary_color='#FFFF00')
print(srt_to_ass('test.srt', style)[:500])
"
```
