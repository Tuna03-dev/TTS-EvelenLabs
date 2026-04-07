import base64
import requests
import asyncio
import tempfile
import os

try:
    import edge_tts
except ImportError:
    edge_tts = None

# Default Voice ID (Josh - Professional/Free-compatible)
DEFAULT_VOICE_ID = "TxGEqnSAs9dnLURhk9Wb" 
# Multilingual v2 model
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
EDGE_MALE_VOICE_PRESETS = [
    {"label": "Male warm - Ryan", "voice": "en-GB-RyanNeural", "rate": "+0%", "pitch": "+0Hz"},
    {"label": "Male calm - Guy", "voice": "en-US-GuyNeural", "rate": "-5%", "pitch": "-2Hz"},
    {"label": "Male deep - Connor", "voice": "en-US-ConnorNeural", "rate": "-3%", "pitch": "-3Hz"},
    {"label": "Male energetic - Brandon", "voice": "en-US-BrandonNeural", "rate": "+3%", "pitch": "+1Hz"},
]


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

def generate_speech_with_timestamps(
    text,
    api_key,
    voice_id=DEFAULT_VOICE_ID,
    model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1",
    provider="elevenlabs",
    tts_rate="0%",
    tts_pitch="0Hz",
):
    """
    Calls ElevenLabs API using the official SDK with timestamps support.
    Returns (audio_bytes, alignment_dict).
    """
    if provider == "edge-tts":
        if edge_tts is None:
            raise ValueError("edge-tts is not installed. Please install it first.")

        async def _generate_edge_tts():
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice_id or DEFAULT_EDGE_VOICE,
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

        audio_bytes, boundaries = _run_async(_generate_edge_tts())
        alignment = {
            "words": boundaries,
        }
        return audio_bytes, alignment

    if not api_key:
        raise ValueError("ElevenLabs API Key is missing. Please add it to Settings.")

    url = f"{_normalize_base_url(base_url)}/text-to-speech/{voice_id}/with-timestamps"
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    response = requests.post(url, headers=_build_headers(api_key), json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()

    audio_bytes = base64.b64decode(data["audio_base64"])
    alignment = data["alignment"]
    
    return audio_bytes, alignment

from pydub import AudioSegment
import io
from modules.segmenter import segment_text

def format_timestamp(seconds):
    """Helper to format seconds into SRT timestamp (HH:MM:SS,mmm)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msecs = int((seconds * 1000) % 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"

def generate_chunked_speech(
    text,
    api_key,
    voice_id=DEFAULT_VOICE_ID,
    model_id=DEFAULT_MODEL_ID,
    base_url="https://api.elevenlabs.io/v1",
    provider="elevenlabs",
    tts_rate="0%",
    tts_pitch="0Hz",
    lang="en",
    max_words=25,
    progress_callback=None
):
    """
    Implements VideoLingo technique: 
    Strictly measures each segment duration to build a precise timeline.
    """
    chunks = segment_text(text, lang=lang, max_words=max_words)
    if not chunks:
        if progress_callback: progress_callback("❌ Error: No chunks generated from text.")
        return b"", []

    if progress_callback: progress_callback(f"📦 Split into {len(chunks)} chunks.")

    combined_audio = AudioSegment.empty()
    srt_segments = []
    current_offset = 0.0

    for i, chunk_text in enumerate(chunks):
        if progress_callback: 
            progress_callback(f"🔊 Processing {i+1}/{len(chunks)}...")
            
        audio_bytes_chunk, _ = generate_speech_with_timestamps(
            chunk_text,
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            base_url=base_url,
            provider=provider,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
        )

        if not audio_bytes_chunk:
            continue

        try:
            segment_audio = AudioSegment.from_file(io.BytesIO(audio_bytes_chunk), format="mp3")
            
            duration_sec = len(segment_audio) / 1000.0
            srt_segments.append({
                "text": chunk_text.strip(),
                "start": current_offset,
                "end": current_offset + duration_sec
            })
            
            combined_audio += segment_audio
            current_offset += duration_sec
            
        except Exception as e:
            if progress_callback: progress_callback(f"⚠️ Skip chunk {i+1} due to audio error: {e}")

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
