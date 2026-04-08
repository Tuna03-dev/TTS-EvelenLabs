import base64
import requests
import asyncio
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

import numpy as np

try:
    import edge_tts  # type: ignore[import-not-found]
except ImportError:
    edge_tts = None

try:
    from kokoro import KPipeline  # type: ignore[import-not-found]
except ImportError:
    KPipeline = None

# Default Voice ID (Josh - Professional/Free-compatible)
DEFAULT_VOICE_ID = "TxGEqnSAs9dnLURhk9Wb" 
# Multilingual v2 model
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
EDGE_MALE_VOICE_PRESETS = [
    {"label": "Male warm - Ryan", "voice": "en-GB-RyanNeural", "rate": "-10%", "pitch": "+0Hz"},
    {"label": "Male calm - Guy", "voice": "en-US-GuyNeural", "rate": "-5%", "pitch": "-2Hz"},
    {"label": "Male deep - Connor", "voice": "en-US-ConnorNeural", "rate": "-8%", "pitch": "-3Hz"},
    {"label": "Male sleepy - Andrew", "voice": "en-US-AndrewNeural", "rate": "-25%", "pitch": "-6Hz"},
    {"label": "Male energetic - Brandon", "voice": "en-US-BrandonNeural", "rate": "-3%", "pitch": "+1Hz"},
]
KOKORO_SAMPLE_RATE = 24000
KOKORO_BIBLE_VOICE_PRESETS = [
    {"label": "Bible warm gentle - Sarah", "voice": "af_sarah", "rate": "-18%", "pitch": "0Hz"},
    {"label": "Bible soft peaceful - Heart", "voice": "af_heart", "rate": "-20%", "pitch": "0Hz"},
    {"label": "Bible calm male - Adam", "voice": "am_adam", "rate": "-16%", "pitch": "0Hz"},
    {"label": "Bible deep male - Michael", "voice": "am_michael", "rate": "-14%", "pitch": "0Hz"},
]
_KOKORO_PIPELINE_CACHE = {}


def _normalize_base_url(base_url):
    return (base_url or "https://api.elevenlabs.io/v1").rstrip("/")


def _build_headers(api_key):
    return {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("edge-tts async call cannot run inside an active event loop here.")

def get_voices(api_key, base_url="https://api.elevenlabs.io/v1"):
    """Fetches available voices from ElevenLabs."""
    if not api_key:
        return []
    try:
        url = f"{_normalize_base_url(base_url)}/voices"
        resp = requests.get(url, headers=_build_headers(api_key), timeout=20)
        resp.raise_for_status()
        data = resp.json().get("voices", [])
        return [{"name": v.get("name", "Unknown"), "id": v.get("voice_id", "")} for v in data if v.get("voice_id")]
    except Exception as e:
        print(f"Error fetching voices: {e}")
        return []

def get_models(api_key, base_url="https://api.elevenlabs.io/v1"):
    """Fetches available models from ElevenLabs."""
    if not api_key:
        return []
    try:
        url = f"{_normalize_base_url(base_url)}/models"
        resp = requests.get(url, headers=_build_headers(api_key), timeout=20)
        resp.raise_for_status()
        models = resp.json()
        result = []
        for model in models:
            if model.get("can_do_text_to_speech"):
                result.append({"name": model.get("name", "Unknown"), "id": model.get("model_id", "")})
        return [m for m in result if m["id"]]
    except Exception as e:
        print(f"Error fetching models: {e}")
        return []


def get_edge_voices(locale_prefix=None):
    if edge_tts is None:
        return []
    try:
        voices = _run_async(edge_tts.list_voices())
        result = []
        for voice in voices:
            short_name = voice.get("ShortName", "")
            if locale_prefix and not short_name.lower().startswith(locale_prefix.lower()):
                continue
            result.append({
                "name": f"{voice.get('FriendlyName', short_name)} ({voice.get('Locale', '')})",
                "id": short_name,
            })
        return result
    except Exception as e:
        print(f"Error fetching edge voices: {e}")
        return []


def get_edge_male_presets():
    return EDGE_MALE_VOICE_PRESETS


def get_kokoro_voice_presets():
    return KOKORO_BIBLE_VOICE_PRESETS


def _parse_tts_rate_to_speed(tts_rate):
    match = re.match(r"^([+-]?)\s*(\d+(?:\.\d+)?)%$", str(tts_rate or "0%").strip())
    if not match:
        return 1.0
    sign = -1.0 if match.group(1) == "-" else 1.0
    percent = float(match.group(2)) * sign
    return max(0.70, min(1.30, 1.0 + (percent / 100.0)))


def _apply_depth_shift(audio_bytes, semitones_down=0.0):
    """Lower perceived pitch while preserving duration."""
    depth = float(semitones_down or 0.0)
    if depth <= 0.0:
        return audio_bytes

    try:
        src = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        pitch_factor = 2 ** (-depth / 12.0)
        shifted = src._spawn(src.raw_data, overrides={"frame_rate": int(src.frame_rate * pitch_factor)})
        shifted = shifted.set_frame_rate(src.frame_rate)
        out_buf = io.BytesIO()
        shifted.export(out_buf, format="mp3", bitrate="128k")
        return out_buf.getvalue()
    except Exception:
        return audio_bytes


def _apply_soft_tone(audio_bytes, softness=0.0):
    """Reduce harshness: low-pass + gentle gain trim."""
    soft = max(0.0, min(1.0, float(softness or 0.0)))
    if soft <= 0.0:
        return audio_bytes
    try:
        src = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        cutoff_hz = int(5200 - (soft * 2800))  # 0.0=>5200, 1.0=>2400
        out = src.low_pass_filter(cutoff_hz)
        out = out.apply_gain(-(1.2 * soft))
        out_buf = io.BytesIO()
        out.export(out_buf, format="mp3", bitrate="128k")
        return out_buf.getvalue()
    except Exception:
        return audio_bytes


def _get_kokoro_pipeline(lang_code="a"):
    key = lang_code or "a"
    if key in _KOKORO_PIPELINE_CACHE:
        return _KOKORO_PIPELINE_CACHE[key]
    if KPipeline is None:
        raise ValueError("Kokoro is not installed. Run: pip install kokoro")
    pipeline = KPipeline(lang_code=key)
    _KOKORO_PIPELINE_CACHE[key] = pipeline
    return pipeline

def generate_speech_with_timestamps(
    text,
    api_key,
    voice_id=DEFAULT_VOICE_ID,
    model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1",
    provider="elevenlabs",
    tts_rate="-10%",
    tts_pitch="0Hz",
    tts_depth_semitones=0.0,
    tts_softness=0.0,
):
    """
    Calls ElevenLabs API using the official SDK with timestamps support.
    Returns (audio_bytes, alignment_dict).
    """
    if provider == "kokoro":
        speed = _parse_tts_rate_to_speed(tts_rate)
        selected_voice = voice_id or "af_sarah"
        try:
            pipeline = _get_kokoro_pipeline(lang_code="a")
            generator = pipeline(
                text,
                voice=selected_voice,
                speed=speed,
                split_pattern=r"\n+",
            )
            chunks = []
            for _, _, audio in generator:
                arr = np.asarray(audio, dtype=np.float32)
                if arr.size:
                    chunks.append(arr)
            if not chunks:
                raise ValueError("Kokoro returned empty audio.")
            merged = np.concatenate(chunks)
            pcm16 = np.clip(merged, -1.0, 1.0)
            pcm16 = (pcm16 * 32767.0).astype(np.int16)
            segment = AudioSegment(
                data=pcm16.tobytes(),
                sample_width=2,
                frame_rate=KOKORO_SAMPLE_RATE,
                channels=1,
            )
            out_buf = io.BytesIO()
            segment.export(out_buf, format="mp3", bitrate="128k")
            audio_bytes = _apply_depth_shift(out_buf.getvalue(), tts_depth_semitones)
            audio_bytes = _apply_soft_tone(audio_bytes, tts_softness)
            return audio_bytes, {"words": []}
        except Exception as e:
            raise ValueError(
                "Kokoro TTS failed. On Windows, ensure espeak-ng is installed and available in PATH. "
                f"Original error: {e}"
            ) from e

    if provider == "edge-tts":
        if edge_tts is None:
            raise ValueError("edge-tts is not installed. Please install it first.")

        async def _generate_edge_tts(selected_voice):
            communicate = edge_tts.Communicate(
                text=text,
                voice=selected_voice or DEFAULT_EDGE_VOICE,
                rate=tts_rate,
                pitch=tts_pitch,
            )
            audio_chunks = []
            word_boundaries = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start_sec = chunk.get("offset", 0) / 10_000_000
                    duration_sec = max(0.08, len(chunk.get("text", "").strip()) * 0.035)
                    word_boundaries.append({
                        "text": chunk.get("text", "").strip(),
                        "start": start_sec,
                        "end": start_sec + duration_sec,
                    })
            return b"".join(audio_chunks), word_boundaries

        voice_candidates = []
        if voice_id:
            voice_candidates.append(voice_id)

        # If the selected voice is the deep preset, try safer fallback voices after it.
        if (voice_id or "").lower() == "en-us-connorneural":
            voice_candidates.extend([
                "en-US-GuyNeural",
                DEFAULT_EDGE_VOICE,
                "en-US-AriaNeural",
            ])
        else:
            voice_candidates.extend([
                DEFAULT_EDGE_VOICE,
                "en-US-GuyNeural",
                "en-US-AriaNeural",
            ])

        last_error = None
        for selected_voice in dict.fromkeys(voice_candidates):
            try:
                audio_bytes, boundaries = _run_async(_generate_edge_tts(selected_voice))
                if audio_bytes:
                    alignment = {
                        "words": boundaries,
                    }
                    audio_bytes = _apply_depth_shift(audio_bytes, tts_depth_semitones)
                    audio_bytes = _apply_soft_tone(audio_bytes, tts_softness)
                    return audio_bytes, alignment
                last_error = ValueError(f"edge-tts returned empty audio for voice={selected_voice}")
            except Exception as e:
                last_error = e

        raise ValueError(f"edge-tts failed for all fallback voices: {last_error}")

    if not api_key:
        raise ValueError("ElevenLabs API Key is missing. Please add it to Settings.")

    url = f"{_normalize_base_url(base_url)}/text-to-speech/{voice_id}/with-timestamps"
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.75,
            "similarity_boost": 0.85,
            "style": 0.0,
            "use_speaker_boost": True,
        }
    }
    response = requests.post(url, headers=_build_headers(api_key), json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()

    audio_bytes = base64.b64decode(data["audio_base64"])
    alignment = data["alignment"]
    
    audio_bytes = _apply_depth_shift(audio_bytes, tts_depth_semitones)
    audio_bytes = _apply_soft_tone(audio_bytes, tts_softness)
    return audio_bytes, alignment

from pydub import AudioSegment
from modules.segmenter import segment_text

def format_timestamp(seconds):
    """Helper to format seconds into SRT timestamp (HH:MM:SS,mmm)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msecs = int((seconds * 1000) % 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"


def _get_natural_pause_ms(text: str) -> int:
    """Returns pause length based on punctuation at the end of chunk text."""
    text = (text or "").strip()
    if not text:
        return 0
    last = text[-1]
    if last in ".!?":
        return 380
    if last in ",;:":
        return 140
    return 60


def _tts_call_with_retry(
    text,
    api_key,
    voice_id,
    model_id,
    base_url,
    provider,
    tts_rate,
    tts_pitch,
    tts_depth_semitones=0.0,
    tts_softness=0.0,
    max_retries=3,
):
    last_error = None
    for attempt in range(1, max(1, int(max_retries)) + 1):
        try:
            audio_bytes, _ = generate_speech_with_timestamps(
                text,
                api_key=api_key,
                voice_id=voice_id,
                model_id=model_id,
                base_url=base_url,
                provider=provider,
                tts_rate=tts_rate,
                tts_pitch=tts_pitch,
                tts_depth_semitones=tts_depth_semitones,
                tts_softness=tts_softness,
            )
            if audio_bytes:
                return audio_bytes, None
            raise ValueError("Empty audio returned")
        except Exception as e:
            last_error = e
            if attempt < max(1, int(max_retries)):
                time.sleep(2 ** (attempt - 1))
    return None, last_error


def _merge_short_audio_items(audio_items, min_segment_seconds=1.0, min_text_chars=18):
    """Merges tiny segments with the next one to reduce choppy cadence."""
    if not audio_items:
        return []

    merged = []
    i = 0
    while i < len(audio_items):
        current = audio_items[i]
        cur_dur = len(current["audio"]) / 1000.0

        if (
            cur_dur < min_segment_seconds
            and len(current["text"].strip()) <= min_text_chars
            and i + 1 < len(audio_items)
        ):
            nxt = audio_items[i + 1]
            merged.append({
                "text": f"{current['text'].strip()} {nxt['text'].strip()}".strip(),
                "audio": current["audio"] + nxt["audio"],
            })
            i += 2
            continue

        merged.append(current)
        i += 1

    return merged


def _build_paced_audio_and_srt(
    audio_items,
    pause_weak_ms=80,
    pause_strong_ms=160,
    max_cps=17.0,
    min_segment_seconds=1.0,
):
    """Builds final audio and SRT timeline with punctuation-aware pauses and rate balancing."""
    items = _merge_short_audio_items(
        audio_items,
        min_segment_seconds=min_segment_seconds,
    )

    combined_audio = AudioSegment.empty()
    srt_segments = []
    current_offset = 0.0

    for idx, item in enumerate(items):
        text = item["text"].strip()
        audio = item["audio"]
        if not text or len(audio) <= 0:
            continue

        duration_sec = max(0.01, len(audio) / 1000.0)
        cps = len(text) / duration_sec if duration_sec > 0 else 999.0

        combined_audio += audio
        srt_segments.append({
            "text": text,
            "start": current_offset,
            "end": current_offset + duration_sec,
        })
        current_offset += duration_sec

        extra_pause_ms = 0
        if cps > max_cps:
            extra_pause_ms += min(220, int((cps - max_cps) * 22))

        if idx < len(items) - 1:
            punctuation_pause = _get_natural_pause_ms(text)
            if punctuation_pause >= 380:
                extra_pause_ms += max(pause_strong_ms, punctuation_pause)
            elif punctuation_pause >= 140:
                extra_pause_ms += max(pause_weak_ms, punctuation_pause)
            else:
                extra_pause_ms += punctuation_pause

        if extra_pause_ms > 0:
            silence = AudioSegment.silent(duration=extra_pause_ms)
            combined_audio += silence
            current_offset += extra_pause_ms / 1000.0

    return combined_audio, srt_segments


def generate_chunked_speech_parallel(
    text,
    api_key,
    voice_id=DEFAULT_VOICE_ID,
    model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1",
    provider="elevenlabs",
    tts_rate="-10%",
    tts_pitch="0Hz",
    tts_depth_semitones=0.0,
    tts_softness=0.0,
    lang="en",
    max_words=25,
    max_chars=120,
    max_workers=3,
    max_retries=3,
    progress_callback=None,
):
    """Parallel chunked TTS with ordered merge and retry."""
    chunks = segment_text(
        text,
        lang=lang,
        max_words=max_words,
        max_chars=max_chars,
        sentence_mode=True,
    )
    if not chunks:
        if progress_callback:
            progress_callback("❌ Error: No chunks generated from text.")
        return b"", []

    if progress_callback:
        progress_callback(f"📦 Split into {len(chunks)} chunks. Running TTS with {max_workers} workers...")

    max_workers = max(1, int(max_workers or 1))
    if provider == "kokoro" and max_workers > 1:
        # Kokoro is local/CPU-heavy; concurrent chunk calls cause repeated model/voice checks and slows down.
        max_workers = 1
        if progress_callback:
            progress_callback("ℹ️ Kokoro optimized mode: forcing sequential chunk generation (workers=1).")
    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                _tts_call_with_retry,
                chunk_text,
                api_key,
                voice_id,
                model_id,
                base_url,
                provider,
                tts_rate,
                tts_pitch,
                tts_depth_semitones,
                tts_softness,
                max_retries,
            ): i
            for i, chunk_text in enumerate(chunks)
        }

        completed = 0
        total = len(chunks)
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            audio_bytes, err = future.result()
            completed += 1

            if err:
                errors[idx] = err
                if progress_callback:
                    progress_callback(f"⚠️ Chunk {idx+1} error: {err}")
            else:
                results[idx] = audio_bytes
                if progress_callback:
                    progress_callback(f"✅ {completed}/{total} chunks done")

    audio_items = []

    for i, chunk_text in enumerate(chunks):
        audio_bytes = results.get(i)

        if not audio_bytes:
            # Fallback: split smaller and try sequentially for this chunk only.
            words = chunk_text.split()
            if len(words) > 1:
                midpoint = max(1, len(words) // 2)
                fallback_parts = [
                    " ".join(words[:midpoint]).strip(),
                    " ".join(words[midpoint:]).strip(),
                ]
                for part_text in [p for p in fallback_parts if p]:
                    part_audio, part_err = _tts_call_with_retry(
                        part_text,
                        api_key,
                        voice_id,
                        model_id,
                        base_url,
                        provider,
                        tts_rate,
                        tts_pitch,
                        tts_depth_semitones,
                        tts_softness,
                        max_retries,
                    )
                    if part_err or not part_audio:
                        if progress_callback:
                            progress_callback(f"⚠️ Bỏ qua chunk {i+1} fallback lỗi")
                        continue

                    segment_audio = AudioSegment.from_file(io.BytesIO(part_audio), format="mp3")
                    audio_items.append({
                        "text": part_text.strip(),
                        "audio": segment_audio,
                    })
            continue

        segment_audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        audio_items.append({
            "text": chunk_text.strip(),
            "audio": segment_audio,
        })

    combined_audio, srt_segments = _build_paced_audio_and_srt(
        audio_items,
        pause_weak_ms=80,
        pause_strong_ms=160,
        max_cps=17.0,
        min_segment_seconds=1.0,
    )

    if not srt_segments:
        return b"", []

    out_buf = io.BytesIO()
    combined_audio.export(out_buf, format="mp3", bitrate="128k")
    return out_buf.getvalue(), srt_segments

def generate_chunked_speech(
    text,
    api_key,
    voice_id=DEFAULT_VOICE_ID,
    model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1",
    provider="elevenlabs",
    tts_rate="-10%",
    tts_pitch="0Hz",
    tts_depth_semitones=0.0,
    tts_softness=0.0,
    lang="en",
    max_words=25,
    max_chars=120,
    progress_callback=None
):
    """
    Implements VideoLingo technique: 
    Strictly measures each segment duration to build a precise timeline.
    """
    chunks = segment_text(
        text,
        lang=lang,
        max_words=max_words,
        max_chars=max_chars,
        sentence_mode=True,
    )
    if not chunks:
        if progress_callback: progress_callback("❌ Error: No chunks generated from text.")
        return b"", []

    if progress_callback: progress_callback(f"📦 Split into {len(chunks)} chunks.")

    combined_audio = AudioSegment.empty()
    srt_segments = []
    current_offset = 0.0

    def _split_chunk_smaller(chunk_text):
        words = chunk_text.split()
        if len(words) <= 1:
            return [chunk_text]
        midpoint = max(1, len(words) // 2)
        left = " ".join(words[:midpoint]).strip()
        right = " ".join(words[midpoint:]).strip()
        return [part for part in (left, right) if part]

    for i, chunk_text in enumerate(chunks):
        if progress_callback: 
            progress_callback(f"🔊 Processing {i+1}/{len(chunks)}...")

        chunk_parts = [chunk_text]
        chunk_index = 0
        while chunk_index < len(chunk_parts):
            part_text = chunk_parts[chunk_index]
            chunk_index += 1

            try:
                audio_bytes_chunk, _ = generate_speech_with_timestamps(
                    part_text,
                    api_key=api_key,
                    voice_id=voice_id,
                    model_id=model_id,
                    base_url=base_url,
                    provider=provider,
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                    tts_depth_semitones=tts_depth_semitones,
                    tts_softness=tts_softness,
                )

                if not audio_bytes_chunk:
                    raise ValueError("No audio was received. Please verify that your parameters are correct.")

                segment_audio = AudioSegment.from_file(io.BytesIO(audio_bytes_chunk), format="mp3")
                duration_sec = len(segment_audio) / 1000.0

                srt_segments.append({
                    "text": part_text.strip(),
                    "start": current_offset,
                    "end": current_offset + duration_sec
                })

                combined_audio += segment_audio
                current_offset += duration_sec

            except Exception as e:
                if len(part_text.split()) > 1:
                    smaller_parts = _split_chunk_smaller(part_text)
                    if len(smaller_parts) > 1:
                        if progress_callback:
                            progress_callback(f"↩️ Chunk {i+1} failed, retry smaller parts...")
                        chunk_parts[chunk_index:chunk_index] = smaller_parts
                        continue

                if progress_callback:
                    progress_callback(f"⚠️ Skip chunk {i+1} due to audio error: {e}")

    if not srt_segments:
        return b"", []

    out_buf = io.BytesIO()
    combined_audio.export(out_buf, format="mp3", bitrate="128k")
    return out_buf.getvalue(), srt_segments

def create_srt_from_alignment(alignment, chars_per_segment=40):
    """
    Converts ElevenLabs alignment data into a list of SRT segments.
    Groups characters into readable chunks.
    """
    # ===== FORMAT 1: Dict with "words" key (edge-tts) =====
    if isinstance(alignment, dict) and alignment.get("words"):
        words = alignment.get("words", [])
        if not words:
            return []

        segments = []
        current_text = ""
        current_start = words[0].get("start", 0.0)
        current_end = words[0].get("end", current_start)

        for idx, word in enumerate(words):
            word_text = word.get("text", "").strip()
            if not word_text:
                continue
            
            current_text = f"{current_text} {word_text}".strip()
            current_end = float(word.get("end", current_end))

            # Determine if we should split here
            is_last_word = (idx == len(words) - 1)
            ends_with_punctuation = word_text.endswith((".", "!", "?"))
            text_long_enough = len(current_text) >= chars_per_segment
            
            should_split = text_long_enough or ends_with_punctuation or is_last_word
            
            if should_split:
                segments.append({
                    "text": current_text,
                    "start": float(current_start),
                    "end": float(current_end),
                })
                # Reset for next segment (if not last word)
                if not is_last_word:
                    current_text = ""
                    next_word = words[idx + 1]
                    current_start = float(next_word.get("start", current_end))
                    current_end = float(next_word.get("end", current_start))
        
        return segments

    # ===== FORMAT 2: ElevenLabs object with .words attribute =====
    if hasattr(alignment, "words"):
        words = alignment.words
        if not words:
            return []

        segments = []
        current_text = ""
        current_start = words[0].get("start", 0.0)
        current_end = words[0].get("end", current_start)

        for idx, word in enumerate(words):
            word_text = word.get("text", "").strip()
            if not word_text:
                continue
            
            current_text = f"{current_text} {word_text}".strip()
            current_end = float(word.get("end", current_end))

            is_last_word = (idx == len(words) - 1)
            ends_with_punctuation = word_text.endswith((".", "!", "?"))
            text_long_enough = len(current_text) >= chars_per_segment
            
            should_split = text_long_enough or ends_with_punctuation or is_last_word
            
            if should_split:
                segments.append({
                    "text": current_text,
                    "start": float(current_start),
                    "end": float(current_end),
                })
                if not is_last_word:
                    current_text = ""
                    next_word = words[idx + 1]
                    current_start = float(next_word.get("start", current_end))
                    current_end = float(next_word.get("end", current_start))
        
        return segments
    
    # ===== FORMAT 3+: ElevenLabs character-level formats =====
    if hasattr(alignment, "characters"):
        characters = alignment.characters
        start_times = alignment.character_start_times_seconds
        end_times = alignment.character_end_times_seconds
    elif isinstance(alignment, list):
        characters = [c for item in alignment for c in item.get("characters", [])]
        start_times = [t for item in alignment for t in item.get("character_start_times_seconds", [])]
        end_times = [t for item in alignment for t in item.get("character_end_times_seconds", [])]
    else:
        characters = alignment.get("characters", []) if isinstance(alignment, dict) else []
        start_times = alignment.get("character_start_times_seconds", []) if isinstance(alignment, dict) else []
        end_times = alignment.get("character_end_times_seconds", []) if isinstance(alignment, dict) else []

    if not characters or not start_times or not end_times:
        return []
    
    segments = []
    current_chars = ""
    current_start = start_times[0]
    
    for i in range(len(characters)):
        current_chars += characters[i]
        
        # Split logic: roughly 40 chars or end of sentence/punctuation
        if len(current_chars) >= chars_per_segment or characters[i] in [".", "!", "?"]:
            segments.append({
                "text": current_chars.strip(),
                "start": current_start,
                "end": end_times[i]
            })
            if i + 1 < len(start_times):
                current_start = start_times[i+1]
                current_chars = ""
    
    # Add leftovers
    if current_chars.strip():
        segments.append({
            "text": current_chars.strip(),
            "start": current_start,
            "end": end_times[-1]
        })
        
    return segments

def save_srt_file(segments, filepath, offset_seconds=0):
    """Writes segments to a .srt file with time offset."""
    with open(filepath, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments):
            start = format_timestamp(seg["start"] + offset_seconds)
            end = format_timestamp(seg["end"] + offset_seconds)
            f.write(f"{i+1}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{seg['text']}\n\n")
