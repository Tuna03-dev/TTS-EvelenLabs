# Plan: Tăng tốc TTS Generation — Parallel Audio + SRT Đồng Bộ

**Dự án:** Bible Video Automation  
**Phạm vi:** `voice_gen.py`, `app.py`  
**Mục tiêu:** Giảm thời gian gen audio/SRT từ N×latency xuống ~max(latency), giữ nguyên đồng bộ SRT và nhất quán giọng đọc.

---

## 1. Chẩn đoán vấn đề hiện tại

### 1.1 Bottleneck chính — `generate_chunked_speech` (voice_gen.py)

```
chunk 1 → TTS call → chờ → nhận audio → chunk 2 → TTS call → chờ → ...
```

Hàm hiện tại xử lý **tuần tự từng chunk**. Với một chapter điển hình có 20–40 chunks và latency trung bình 1.5–3s/call:

| Số chunks | Latency/call | Tổng thời gian hiện tại | Sau cải tiến |
|-----------|-------------|--------------------------|--------------|
| 20        | 2s          | ~40s                     | ~8–10s       |
| 40        | 2s          | ~80s                     | ~10–12s      |
| 20        | 3s          | ~60s                     | ~10–14s      |

### 1.2 Hai rủi ro nếu làm parallel ngây thơ

**Rủi ro 1 — SRT lệch timestamp:**  
`as_completed()` trả về future theo thứ tự *hoàn thành*, không theo thứ tự *index*. Nếu merge audio theo thứ tự hoàn thành → offset SRT sai.

**Rủi ro 2 — Giọng không nhất quán:**  
ElevenLabs mặc định `stability=0.5` có thể biến động giữa các call. Cần tăng stability và pin các tham số.

---

## 2. Giải pháp: 2-Phase Parallel Pipeline

### Tổng quan

```
Phase 1: Gọi TTS song song → lưu results[index] = audio_bytes
Phase 2: Merge theo thứ tự index 0→N → cộng offset từ duration thực tế
```

Phase 2 luôn chạy tuần tự theo index nên SRT không bao giờ lệch, bất kể thứ tự future hoàn thành ở Phase 1.

---

## 3. Các thay đổi cụ thể

### 3.1 `voice_gen.py` — Thêm hàm mới

**Thêm 2 thứ:**

**a. Helper `_tts_call_with_retry`** — gọi TTS cho 1 chunk với exponential backoff:

```python
def _tts_call_with_retry(
    idx, text, api_key, voice_id, model_id, base_url, provider, tts_rate, tts_pitch, max_retries=3
):
    """Gọi TTS cho 1 chunk, retry nếu lỗi. Trả về (idx, audio_bytes, error)."""
    for attempt in range(max_retries):
        try:
            audio_bytes, _ = generate_speech_with_timestamps(
                text, api_key=api_key, voice_id=voice_id, model_id=model_id,
                base_url=base_url, provider=provider, tts_rate=tts_rate, tts_pitch=tts_pitch,
            )
            if audio_bytes:
                return idx, audio_bytes, None
            raise ValueError("Empty audio returned")
        except Exception as e:
            if attempt == max_retries - 1:
                return idx, None, e
            time.sleep(2 ** attempt)
    return idx, None, Exception("Max retries exceeded")
```

**b. Hàm chính `generate_chunked_speech_parallel`:**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def generate_chunked_speech_parallel(
    text, api_key, voice_id=DEFAULT_VOICE_ID, model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1", provider="elevenlabs",
    tts_rate="0%", tts_pitch="0Hz", lang="en",
    max_workers=5,
    progress_callback=None,
):
    from modules.segmenter import segment_text

    chunks = segment_text(text, lang=lang)
    if not chunks:
        if progress_callback:
            progress_callback("❌ Không tạo được chunk từ text.")
        return b"", []

    if progress_callback:
        progress_callback(f"📦 {len(chunks)} chunks → TTS song song ({max_workers} workers)...")

    # ── PHASE 1: Gọi TTS song song ──────────────────────────────────────────
    results = {}  # index → audio_bytes
    errors  = {}  # index → Exception

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                _tts_call_with_retry,
                i, chunk, api_key, voice_id, model_id,
                base_url, provider, tts_rate, tts_pitch
            ): i
            for i, chunk in enumerate(chunks)
        }
        completed = 0
        for future in as_completed(future_to_idx):
            idx, audio_bytes, err = future.result()
            completed += 1
            if err:
                errors[idx] = err
                if progress_callback:
                    progress_callback(f"⚠️ Chunk {idx+1} lỗi: {err}")
            else:
                results[idx] = audio_bytes
                if progress_callback:
                    progress_callback(f"✅ {completed}/{len(chunks)} chunks xong")

    # ── PHASE 2: Merge THEO THỨ TỰ INDEX (đảm bảo SRT không lệch) ──────────
    combined_audio = AudioSegment.empty()
    srt_segments   = []
    current_offset = 0.0

    for i, chunk_text in enumerate(chunks):
        if i in errors:
            # Fallback: chia nhỏ và gọi lại tuần tự
            words = chunk_text.split()
            if len(words) > 1:
                mid = len(words) // 2
                for part in [" ".join(words[:mid]), " ".join(words[mid:])]:
                    try:
                        audio_bytes, _ = generate_speech_with_timestamps(
                            part, api_key=api_key, voice_id=voice_id,
                            model_id=model_id, base_url=base_url,
                            provider=provider, tts_rate=tts_rate, tts_pitch=tts_pitch,
                        )
                        seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
                        dur = len(seg) / 1000.0
                        srt_segments.append({"text": part.strip(), "start": current_offset, "end": current_offset + dur})
                        combined_audio += seg
                        current_offset += dur
                    except Exception:
                        if progress_callback:
                            progress_callback(f"⚠️ Bỏ qua chunk {i+1} (fallback cũng lỗi)")
            continue

        seg = AudioSegment.from_file(io.BytesIO(results[i]), format="mp3")
        dur = len(seg) / 1000.0
        srt_segments.append({
            "text": chunk_text.strip(),
            "start": current_offset,
            "end":   current_offset + dur,
        })
        combined_audio += seg
        current_offset += dur

    if not srt_segments:
        return b"", []

    out_buf = io.BytesIO()
    combined_audio.export(out_buf, format="mp3", bitrate="128k")
    return out_buf.getvalue(), srt_segments
```

**Không xóa** `generate_chunked_speech` cũ — giữ lại làm fallback.

---

### 3.2 `voice_gen.py` — Tăng stability cho ElevenLabs

Trong `generate_speech_with_timestamps`, sửa `voice_settings`:

```python
# Trước
"voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.75
}

# Sau
"voice_settings": {
    "stability": 0.75,        # tăng → giọng ít biến động hơn giữa các chunk
    "similarity_boost": 0.85, # bám sát voice profile
    "style": 0.0,             # tắt style exaggeration
    "use_speaker_boost": True
}
```

---

### 3.3 `app.py` — Tích hợp hàm mới + thêm UI control

**a. Import thêm:**

```python
from modules.voice_gen import (
    generate_speech_with_timestamps,
    generate_chunked_speech,
    generate_chunked_speech_parallel,   # <-- thêm
    create_srt_from_alignment,
    save_srt_file,
    get_edge_voices,
    get_edge_male_presets
)
```

**b. Thêm slider trong sidebar settings:**

```python
# Trong phần TTS settings của sidebar
tts_max_workers = st.slider(
    "TTS parallel workers",
    min_value=1, max_value=10, value=3,
    help="ElevenLabs free tier: 2–3 | Paid: 5–10. Quá cao → bị rate limit 429."
)
```

**c. Thay chỗ gọi `generate_chunked_speech`:**

```python
# Trước
audio_bytes, srt_segments = generate_chunked_speech(
    text=chapter_text,
    api_key=tts_api_key,
    ...
    progress_callback=progress_callback,
)

# Sau
audio_bytes, srt_segments = generate_chunked_speech_parallel(
    text=chapter_text,
    api_key=tts_api_key,
    ...
    max_workers=tts_max_workers,
    progress_callback=progress_callback,
)
```

---

## 4. Giới hạn `max_workers` theo tier

| ElevenLabs Tier | Concurrent requests | `max_workers` khuyến nghị |
|-----------------|--------------------|-----------------------------|
| Free            | 2                  | 2                           |
| Starter         | 3                  | 3                           |
| Creator+        | 5–10               | 5                           |
| edge-tts        | Không giới hạn     | 8–10                        |

Nếu gặp lỗi 429, `_tts_call_with_retry` sẽ retry với backoff 1s → 2s → 4s trước khi báo lỗi.

---

## 5. Tại sao SRT không bị lệch

Đây là điểm quan trọng nhất của thiết kế:

```
Phase 1 hoàn thành theo bất kỳ thứ tự nào:
  future chunk 3 → done
  future chunk 1 → done
  future chunk 2 → done

Phase 2 luôn merge theo index:
  i=0 → audio[0], offset += duration[0]
  i=1 → audio[1], offset += duration[1]
  i=2 → audio[2], offset += duration[2]
  ...

→ offset của mỗi SRT segment = tổng duration các audio trước đó
→ luôn khớp với vị trí thực trong file MP3 cuối
```

Khác với `stitch_video_pack` trong `audio_engine.py` (tính offset từ duration ước lượng), hàm mới tính từ **duration thực tế** của `AudioSegment` sau khi decode — chính xác hơn.

---

## 6. Thứ tự thực hiện

| Bước | File           | Việc làm                                     | Ưu tiên |
|------|----------------|----------------------------------------------|---------|
| 1    | `voice_gen.py` | Thêm `_tts_call_with_retry`                  | Cao     |
| 2    | `voice_gen.py` | Thêm `generate_chunked_speech_parallel`      | Cao     |
| 3    | `voice_gen.py` | Sửa `voice_settings` stability               | Trung bình |
| 4    | `app.py`       | Import hàm mới                               | Cao     |
| 5    | `app.py`       | Thêm slider `tts_max_workers` trong sidebar  | Trung bình |
| 6    | `app.py`       | Thay gọi `generate_chunked_speech` → parallel| Cao     |

---

## 7. Kiểm tra sau khi triển khai

1. **Test chapter ngắn (~5 chunks):** So sánh SRT timestamp với waveform trong Audacity.
2. **Test chapter dài (~30 chunks):** Đảm bảo chunk cuối có timestamp đúng.
3. **Test khi 1 chunk lỗi:** Fallback chia nhỏ phải giữ offset đúng cho các chunk sau.
4. **Test với `max_workers=1`:** Phải cho kết quả giống hệt hàm cũ.

---

## 8. Không thay đổi

- `audio_engine.py` — không cần sửa, `stitch_video_pack` hoạt động ở cấp chapter.
- `processor.py` — không liên quan.
- `fetcher.py`, `segmenter.py`, `transcriber.py` — không liên quan.
- Logic SRT offset trong `stitch_video_pack` — đã đúng, giữ nguyên.
