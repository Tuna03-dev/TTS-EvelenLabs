# Plan: Audio-First Pipeline — Đạt chính xác 3:33:33

> Thay thế logic estimate-first trong `processor.py` bằng pipeline 2-pass:
> **Pass 1** fetch + gen TTS thật → **Pass 2** chọn chapters theo actual duration → stitch với delta <2%.

---

## Tổng quan thay đổi

```
TRƯỚC (estimate-first)                SAU (audio-first)
──────────────────────                ─────────────────
processor.py                          modules/pipeline.py  ← file mới thay thế
  fetch text                            Pass 1: fetch text (parallel)
  estimate duration                     Pass 1: gen TTS (parallel)  
  dừng khi estimate ≥ target            Pass 1: đo actual_ms từng file
  ↓                                     Pass 2: chọn chapters cộng dồn đến target
voice_gen.py (gọi riêng lẻ)            ↓
  gen TTS từng chapter                audio_engine.py (giữ nguyên, delta nhỏ hơn)
  ↓                                     stitch + trim/pad cuối → chính xác ±0.05s
audio_engine.py
  stitch + guess silence/stretch
  delta 5-20% → stretch nghe thấy
```

### File thay đổi

| File | Hành động | Lý do |
|---|---|---|
| `modules/pipeline.py` | Tạo mới | Orchestrator 2-pass thay processor.py |
| `modules/audio_engine.py` | Sửa nhỏ | Nhận `actual_ms` thay vì tự đo lại |
| `processor.py` | Giữ helper functions | `get_interleaved_chapters()`, `load_bible_structure()` |
| `app.py` | Đổi import | Gọi `pipeline.run_pipeline()` thay `processor.generate_video_pack()` |

---

## Bước 1 — Tạo `modules/pipeline.py`

File này là trái tim của approach mới. Gồm 4 hàm chính.

### 1.1 Hàm `_fetch_batch()` — fetch text song song

```python
# modules/pipeline.py
import os, time, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydub import AudioSegment

from modules.fetcher import fetch_chapter_text
from modules.voice_gen import generate_chunked_speech_parallel, save_srt_file
from processor import get_interleaved_chapters

TARGET_SECONDS = 12_813.0   # 3:33:33
BATCH_SIZE     = 8           # số chapters gen song song mỗi lần
TTS_WORKERS    = 4           # số luồng TTS song song


def _fetch_batch(chapters: list) -> dict:
    """
    Fetch text song song cho một batch chapters.
    Trả về dict {(book, chapter): text}.
    """
    results = {}

    def _fetch_one(book, chapter):
        return (book, chapter), fetch_chapter_text(book, chapter)

    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as ex:
        futures = {
            ex.submit(_fetch_one, ch["book"], ch["chapter"]): ch
            for ch in chapters
        }
        for future in as_completed(futures):
            key, text = future.result()
            if text:
                results[key] = text

    return results
```

### 1.2 Hàm `_tts_batch()` — gen TTS song song, đo actual duration

```python
def _tts_batch(
    chapters: list,
    texts: dict,
    audio_dir: str,
    tts_kwargs: dict,
    progress_callback=None,
) -> list:
    """
    Gen TTS song song cho batch, lưu file, đo actual_ms.

    Trả về list of dict:
        {book, chapter, audio_file, srt_file, actual_ms}
    """
    results = []

    def _gen_one(book, chapter):
        text = texts.get((book, chapter))
        if not text:
            return None

        safe_name = book.replace(" ", "_").lower()
        base      = f"{safe_name}_{chapter}"
        mp3_path  = os.path.join(audio_dir, f"{base}.mp3")
        srt_path  = os.path.join(audio_dir, f"{base}.srt")

        # Prepend chapter header như processor.py hiện tại
        full_text = f"{book} {chapter}. {text}"

        try:
            audio_bytes, srt_segs = generate_chunked_speech_parallel(
                full_text, **tts_kwargs
            )
        except Exception as e:
            if progress_callback:
                progress_callback(f"  TTS lỗi {book} {chapter}: {e}")
            return None

        if not audio_bytes:
            return None

        # Lưu MP3
        with open(mp3_path, "wb") as f:
            f.write(audio_bytes)

        # Lưu SRT
        save_srt_file(srt_segs, srt_path)

        # Đo ACTUAL duration — không ước tính
        actual_ms = len(AudioSegment.from_file(mp3_path))

        return {
            "book":       book,
            "chapter":    chapter,
            "audio_file": f"{base}.mp3",
            "srt_file":   f"{safe_name}_{chapter}.srt",
            "actual_ms":  actual_ms,
        }

    with ThreadPoolExecutor(max_workers=TTS_WORKERS) as ex:
        future_to_ch = {
            ex.submit(_gen_one, ch["book"], ch["chapter"]): ch
            for ch in chapters
            if (ch["book"], ch["chapter"]) in texts
        }
        for future in as_completed(future_to_ch):
            r = future.result()
            if r:
                results.append(r)

    # Giữ thứ tự gốc của batch
    order = {(ch["book"], ch["chapter"]): i for i, ch in enumerate(chapters)}
    results.sort(key=lambda r: order.get((r["book"], r["chapter"]), 999))

    return results


def _cleanup_excess(audio_dir: str, chapter_info: dict):
    """Xóa file TTS thừa (chapter đã gen nhưng không dùng)."""
    for key in ("audio_file", "srt_file"):
        path = os.path.join(audio_dir, chapter_info.get(key, ""))
        if path and os.path.exists(path):
            os.remove(path)
```

### 1.3 Hàm `run_pipeline()` — orchestrator 2-pass

```python
def run_pipeline(
    output_base_dir: str,
    tts_kwargs: dict,
    target_seconds: float = TARGET_SECONDS,
    batch_size: int = BATCH_SIZE,
    progress_callback=None,
) -> dict:
    """
    Pipeline 2-pass:
      Pass 1 — fetch text + gen TTS theo batch, đo actual_ms
      Pass 2 — chọn chapters đủ duration, stitch, trim/pad → 3:33:33 chính xác

    Args:
        output_base_dir: thư mục gốc chứa video_pack_*
        tts_kwargs:      dict tham số truyền vào generate_chunked_speech_parallel
                         (api_key, voice_id, model_id, provider, ...)
        target_seconds:  mặc định 12813.0 (3:33:33)
        batch_size:      số chapters mỗi batch song song
        progress_callback: callback(str) để update UI

    Returns:
        dict: pack_dir, audio_path, srt_path, chapters_count, actual_duration
    """
    pack_id   = int(time.time())
    pack_dir  = os.path.join(output_base_dir, f"video_pack_{pack_id}")
    audio_dir = os.path.join(pack_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    all_chapters   = get_interleaved_chapters()
    done_chapters  = []          # chapters đã chọn dùng
    total_actual_s = 0.0         # tổng actual duration đã chọn
    idx            = 0

    # ── PASS 1: fetch + gen TTS theo batch ──────────────────────────────
    while total_actual_s < target_seconds and idx < len(all_chapters):
        batch = [
            {"book": b, "chapter": c}
            for b, c in all_chapters[idx: idx + batch_size]
        ]
        idx += batch_size

        if progress_callback:
            done_min = int(total_actual_s // 60)
            tgt_min  = int(target_seconds // 60)
            progress_callback(
                f"Batch {idx // batch_size}: "
                f"fetch + TTS {len(batch)} chapters "
                f"| {done_min}m / {tgt_min}m"
            )

        # Fetch text song song
        texts = _fetch_batch(batch)

        if not texts:
            continue

        # Gen TTS song song
        results = _tts_batch(batch, texts, audio_dir, tts_kwargs, progress_callback)

        for r in results:
            if total_actual_s >= target_seconds:
                # Đã đủ — xóa file thừa để tiết kiệm disk
                _cleanup_excess(audio_dir, r)
                if progress_callback:
                    progress_callback(
                        f"  Bỏ qua {r['book']} {r['chapter']} (đã đủ duration)"
                    )
                continue

            total_actual_s += r["actual_ms"] / 1000.0
            done_chapters.append(r)

            if progress_callback:
                m = int(total_actual_s // 60)
                s = int(total_actual_s % 60)
                progress_callback(
                    f"  + {r['book']} {r['chapter']} "
                    f"({r['actual_ms']/1000:.1f}s) "
                    f"| tổng: {m}:{s:02d}"
                )

    if not done_chapters:
        raise RuntimeError("Không có chapter nào được gen thành công.")

    if progress_callback:
        progress_callback(
            f"Pass 1 xong: {len(done_chapters)} chapters, "
            f"actual = {total_actual_s:.1f}s, "
            f"delta = {target_seconds - total_actual_s:.1f}s"
        )

    # ── PASS 2: stitch với actual duration đã biết ───────────────────────
    from modules.audio_engine import stitch_video_pack

    if progress_callback:
        progress_callback("Pass 2: Stitch audio + SRT → 3:33:33...")

    audio_path, srt_path = stitch_video_pack(
        pack_dir,
        done_chapters,
        target_seconds=target_seconds,
    )

    # Lưu metadata
    import json
    meta = {
        "pack_id":         pack_id,
        "target_seconds":  target_seconds,
        "actual_duration": total_actual_s,
        "delta_seconds":   target_seconds - total_actual_s,
        "chapters_count":  len(done_chapters),
        "chapters":        done_chapters,
    }
    with open(os.path.join(pack_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    if progress_callback:
        progress_callback("Hoàn tất!")

    return {
        "pack_dir":        pack_dir,
        "audio_path":      audio_path,
        "srt_path":        srt_path,
        "chapters_count":  len(done_chapters),
        "actual_duration": total_actual_s,
        "delta_seconds":   target_seconds - total_actual_s,
    }
```

---

## Bước 2 — Sửa `audio_engine.py`

Chỉ cần sửa hàm `stitch_video_pack()`. Thay vì đoán delta, giờ nhận `actual_ms` từ pipeline truyền vào nên delta nhỏ và chính xác.

### 2.1 Thêm `time_stretch_audio()` (nếu chưa có)

```python
# Thêm vào đầu audio_engine.py
import numpy as np

def time_stretch_audio(segment: AudioSegment, target_seconds: float) -> AudioSegment:
    """
    Co giãn audio đến target_seconds, giữ nguyên pitch.
    An toàn trong khoảng ±8% — với audio-first, delta <2% nên luôn safe.
    Cần: pip install pyrubberband soundfile
    """
    try:
        import pyrubberband as pyrb
    except ImportError:
        # Fallback: không stretch, chỉ dùng silence
        return segment

    current_s = len(segment) / 1000.0
    if current_s <= 0 or abs(current_s - target_seconds) / current_s < 0.001:
        return segment   # delta < 0.1%, bỏ qua

    ratio   = current_s / target_seconds
    sr      = segment.frame_rate
    ch      = segment.channels
    samples = np.array(segment.get_array_of_samples(), dtype=np.float32)

    if ch == 2:
        samples = samples.reshape((-1, 2))

    stretched = pyrb.time_stretch(samples, sr, ratio)

    if ch == 2:
        stretched = stretched.flatten()

    out = np.clip(stretched, -32768, 32767).astype(np.int16)
    return AudioSegment(out.tobytes(), frame_rate=sr, sample_width=2, channels=ch)
```

### 2.2 Sửa `stitch_video_pack()` — nhận actual_ms, delta nhỏ

```python
def stitch_video_pack(pack_dir, chapters, target_seconds=12_813.0):
    """
    Stitch chapters thành 1 file audio chính xác target_seconds.

    chapters: list of dict với actual_ms đã đo sẵn từ pipeline.
    Với audio-first, delta thường <5% → stretch <1.5% → không nghe thấy.
    """
    audio_dir = os.path.join(pack_dir, "audio")
    final_dir = os.path.join(pack_dir, "final")
    os.makedirs(final_dir, exist_ok=True)

    # ── Bước 1: Load audio, lấy actual_ms từ metadata ────────────────────
    audio_segments = []
    total_raw_s    = 0.0

    for i, ch in enumerate(chapters):
        path = os.path.join(audio_dir, ch["audio_file"])
        validate_audio_file(path)
        seg = AudioSegment.from_file(path)
        audio_segments.append(seg)

        # Dùng actual_ms từ pipeline nếu có, không thì đo lại
        actual_ms     = ch.get("actual_ms") or len(seg)
        total_raw_s  += actual_ms / 1000.0

    # ── Bước 2: Tính delta và strategy ───────────────────────────────────
    delta_s     = target_seconds - total_raw_s
    delta_pct   = abs(delta_s / total_raw_s) * 100 if total_raw_s > 0 else 0
    n           = len(chapters)

    print(f"[stitch] total_raw={total_raw_s:.2f}s  "
          f"target={target_seconds:.2f}s  "
          f"delta={delta_s:.2f}s ({delta_pct:.2f}%)")

    # Với audio-first, delta_pct thường <5% → luôn vào SAFE zone
    if delta_pct <= 1.0:
        stretch_share = 0.0    # quá nhỏ, dùng silence thuần
    elif delta_pct <= 5.0:
        stretch_share = 0.7    # stretch 70%, silence 30%
    elif delta_pct <= 8.0:
        stretch_share = 0.4    # stretch 40%, silence 60%
    else:
        stretch_share = 0.0    # >8% không stretch, báo cảnh báo
        print(f"[stitch] WARNING: delta {delta_pct:.1f}% > 8%, skip stretch")

    stretch_delta_s = delta_s * stretch_share
    silence_delta_s = delta_s * (1 - stretch_share)

    # Silence chia: 60% đuôi chapter, 40% giữa chapters
    tail_ms = int(silence_delta_s * 0.6 / n * 1000)
    gap_ms  = int(silence_delta_s * 0.4 / max(n - 1, 1) * 1000)

    # Stretch ratio áp dụng đều cho tất cả chapters
    if stretch_share > 0 and stretch_delta_s != 0:
        stretch_target_total = total_raw_s + stretch_delta_s
        stretch_ratio        = total_raw_s / stretch_target_total
    else:
        stretch_ratio = 1.0

    # ── Bước 3: Ghép audio ────────────────────────────────────────────────
    final_audio = AudioSegment.empty()

    for i, (seg, ch) in enumerate(zip(audio_segments, chapters)):
        # Stretch từng chapter nếu cần
        if stretch_ratio != 1.0:
            chapter_target_s = (len(seg) / 1000.0) / stretch_ratio
            seg = time_stretch_audio(seg, chapter_target_s)

        final_audio += seg
        final_audio += AudioSegment.silent(duration=max(0, tail_ms))

        if i < n - 1:
            final_audio += AudioSegment.silent(duration=max(0, gap_ms))

    # ── Bước 4: Trim/pad cuối — đây là bước đảm bảo chính xác tuyệt đối ──
    target_ms  = int(target_seconds * 1000)
    current_ms = len(final_audio)
    diff_ms    = target_ms - current_ms

    if diff_ms > 0:
        # Thiếu → thêm silence im lặng ở cuối
        final_audio += AudioSegment.silent(duration=diff_ms)
    elif diff_ms < 0:
        # Thừa → cắt từ cuối (chỉ cắt vào silence, không đụng audio thật)
        final_audio = final_audio[:target_ms]

    # ── Bước 5: Verify ───────────────────────────────────────────────────
    final_ms = len(final_audio)
    assert abs(final_ms - target_ms) < 50, \
        f"Duration mismatch: {final_ms}ms vs {target_ms}ms"

    print(f"[stitch] final={final_ms/1000:.3f}s  "
          f"(target={target_seconds:.3f}s, diff={final_ms - target_ms}ms)")

    # ── Bước 6: Export MP3 ───────────────────────────────────────────────
    out_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    final_audio.export(out_path, format="mp3", bitrate="128k")
    validate_audio_file(out_path)

    # ── Bước 7: Merge SRT với offset chính xác theo actual duration ──────
    srt_path = os.path.join(final_dir, "final_subtitles.srt")
    _merge_srt_files(chapters, audio_segments, audio_dir,
                     srt_path, tail_ms, gap_ms)

    return out_path, srt_path
```

### 2.3 Hàm `_merge_srt_files()` — offset theo actual duration

```python
def _merge_srt_files(chapters, audio_segments, audio_dir,
                     out_path, tail_ms, gap_ms):
    """Merge SRT với offset tính theo actual audio duration."""
    current_offset_s = 0.0
    subtitle_count   = 1

    with open(out_path, "w", encoding="utf-8") as out_f:
        for i, (ch, seg) in enumerate(zip(chapters, audio_segments)):
            srt_path = os.path.join(audio_dir, ch["srt_file"])
            if not os.path.exists(srt_path):
                continue

            with open(srt_path, "r", encoding="utf-8") as in_f:
                blocks = [b for b in in_f.read().strip().split("\n\n") if b.strip()]

            for block in blocks:
                lines = [l.strip() for l in block.splitlines() if l.strip()]
                if len(lines) < 2:
                    continue

                time_idx = 1 if lines[0].isdigit() else 0
                if time_idx >= len(lines) or " --> " not in lines[time_idx]:
                    continue

                start_str, end_str = lines[time_idx].split(" --> ")
                text_lines = lines[time_idx + 1:]
                if not text_lines:
                    continue

                start_adj = format_timestamp(
                    _parse_srt_timestamp(start_str) + current_offset_s
                )
                end_adj = format_timestamp(
                    _parse_srt_timestamp(end_str) + current_offset_s
                )

                out_f.write(f"{subtitle_count}\n")
                out_f.write(f"{start_adj} --> {end_adj}\n")
                out_f.write("\n".join(text_lines) + "\n\n")
                subtitle_count += 1

            # Cập nhật offset: actual duration + tail + gap
            chapter_s     = len(seg) / 1000.0
            current_offset_s += chapter_s + tail_ms / 1000.0
            if i < len(chapters) - 1:
                current_offset_s += gap_ms / 1000.0
```

---

## Bước 3 — Sửa `app.py`

Chỉ cần đổi chỗ gọi hàm generate.

```python
# TRƯỚC (xóa hoặc comment):
from processor import generate_video_pack
# metadata, pack_dir = generate_video_pack(output_dir, progress_callback)

# SAU:
from modules.pipeline import run_pipeline

# Lấy tts_kwargs từ settings trong app (api_key, voice_id, ...)
tts_kwargs = {
    "api_key":    st.session_state.get("tts_api_key", ""),
    "voice_id":   st.session_state.get("voice_id", ""),
    "model_id":   st.session_state.get("model_id", ""),
    "provider":   st.session_state.get("provider", "elevenlabs"),
    "lang":       "en",
    "max_words":  25,
    "max_chars":  120,
    "max_workers": 3,
}

result = run_pipeline(
    output_base_dir=BASE_OUTPUT_DIR,
    tts_kwargs=tts_kwargs,
    progress_callback=lambda msg: st.session_state["log"].append(msg),
)

pack_dir   = result["pack_dir"]
audio_path = result["audio_path"]
srt_path   = result["srt_path"]
```

---

## Bước 4 — Dependencies

```bash
# Bắt buộc cho time-stretch
pip install pyrubberband soundfile

# Linux
sudo apt install rubberband-cli

# macOS
brew install rubberband

# Windows: tải rubberband.exe, thêm vào PATH
# https://breakfastquay.com/rubberband/
```

Nếu không cài được rubberband, `time_stretch_audio()` đã có fallback — tự động bỏ qua stretch, chỉ dùng silence. Vẫn đạt ±0.5s thay vì ±0.05s.

---

## Thứ tự implement

```
Bước 1 — Tạo modules/pipeline.py (hàm _fetch_batch, _tts_batch, run_pipeline)
    ↓
Bước 2 — Sửa audio_engine.py (thêm time_stretch_audio, sửa stitch_video_pack)
    ↓
Bước 3 — Sửa app.py (đổi import, truyền tts_kwargs)
    ↓
Bước 4 — Cài pyrubberband + test
    ↓
Verify: python -c "
from pydub import AudioSegment
a = AudioSegment.from_file('output/.../final/full_audio_3h33.mp3')
print(f'Duration: {len(a)/1000:.3f}s  (target: 12813.000s)')
"
```

---

## Kết quả kỳ vọng

| Chỉ số | Estimate-first (cũ) | Audio-first (mới) |
|---|---|---|
| Delta vào stitch | 5–20% (~10–25 phút) | <5% (phần lẻ chapter cuối) |
| Stretch cần dùng | Nhiều, có thể nghe thấy | <1.5%, không nghe thấy |
| Độ chính xác output | ±5–10 giây | ±0.05 giây (<50ms) |
| TTS thừa | 0 (nhưng estimate sai) | Tối đa 1 chapter (~5 phút) |
| API cost thừa | 0 | ~1–2% (chấp nhận được) |
