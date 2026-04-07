import os
import tempfile
import requests
from pydub import AudioSegment
from pydub.effects import normalize
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class ASRRequestError(Exception):
    pass


def _normalize_openai_base_url(base_url):
    url = (base_url or "https://api.openai.com/v1").rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def _format_timestamp(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msecs = int((seconds * 1000) % 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"


def _prepare_audio_for_asr(audio):
    # Match the common ASR input format used in VideoLingo-like pipelines.
    return normalize(audio).set_frame_rate(16000).set_channels(1)


def _segments_from_elevenlabs_words(words, offset_seconds=0.0, split_gap=1.0):
    if not words:
        return []

    segments = []
    current = {
        "start": float(words[0].get("start", 0.0)) + offset_seconds,
        "end": float(words[0].get("end", 0.0)) + offset_seconds,
        "text": ""
    }

    for idx, word in enumerate(words):
        text_piece = word.get("text", "")
        start = float(word.get("start", 0.0))
        end = float(word.get("end", start))

        current["text"] += text_piece
        current["end"] = end + offset_seconds

        next_word = words[idx + 1] if idx + 1 < len(words) else None
        should_split = False
        if next_word is None:
            should_split = True
        else:
            next_start = float(next_word.get("start", end))
            if next_start - end > split_gap:
                should_split = True

        if should_split:
            clean_text = current["text"].strip()
            if clean_text:
                segments.append({
                    "start": current["start"],
                    "end": current["end"],
                    "text": clean_text
                })
            if next_word is not None:
                current = {
                    "start": float(next_word.get("start", 0.0)) + offset_seconds,
                    "end": float(next_word.get("end", 0.0)) + offset_seconds,
                    "text": ""
                }

    return segments


@retry(
    retry=retry_if_exception_type(ASRRequestError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _openai_transcribe_chunk(chunk_path, api_key, base_url, model, language=None):
    url = f"{_normalize_openai_base_url(base_url)}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {
        "model": model,
        "response_format": "verbose_json"
    }
    if language:
        data["language"] = language

    with open(chunk_path, "rb") as audio_f:
        files = {
            "file": (os.path.basename(chunk_path), audio_f, "audio/mpeg")
        }
        response = requests.post(url, headers=headers, data=data, files=files, timeout=180)

    if response.status_code >= 500 or response.status_code == 429:
        raise ASRRequestError(f"OpenAI-compatible ASR temporary failure: {response.status_code}")

    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict) and payload.get("segments"):
        return [
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": seg.get("text", "").strip()
            }
            for seg in payload["segments"]
            if seg.get("text", "").strip()
        ]

    text = payload.get("text", "").strip() if isinstance(payload, dict) else ""
    if text:
        return [{"start": 0.0, "end": 0.0, "text": text}]
    return []


@retry(
    retry=retry_if_exception_type(ASRRequestError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _elevenlabs_transcribe_chunk(chunk_path, api_key, base_url, language=None, model="scribe_v1"):
    url = f"{(base_url or 'https://api.elevenlabs.io/v1').rstrip('/')}/speech-to-text"
    headers = {"xi-api-key": api_key}
    data = {
        "model_id": model,
        "timestamps_granularity": "word",
        "diarize": "false"
    }
    if language:
        data["language_code"] = language

    with open(chunk_path, "rb") as audio_f:
        files = {
            "file": (os.path.basename(chunk_path), audio_f, "audio/mpeg")
        }
        response = requests.post(url, headers=headers, data=data, files=files, timeout=180)

    if response.status_code >= 500 or response.status_code == 429:
        raise ASRRequestError(f"ElevenLabs ASR temporary failure: {response.status_code}")

    response.raise_for_status()
    payload = response.json()
    words = payload.get("words", []) if isinstance(payload, dict) else []
    return _segments_from_elevenlabs_words(words, offset_seconds=0.0)


def _local_faster_whisper_transcribe_chunk(chunk_path, model="small", language=None, device="cpu"):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("Local ASR requires faster-whisper. Please install it first.") from e

    compute_type = "int8" if device == "cpu" else "float16"
    whisper_model = WhisperModel(model, device=device, compute_type=compute_type)
    segments, _ = whisper_model.transcribe(
        chunk_path,
        language=language or None,
        word_timestamps=False,
        vad_filter=True,
    )

    out = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": text,
        })
    return out


def transcribe_audio_to_segments(
    audio_path,
    api_key,
    base_url,
    model="whisper-1",
    language=None,
    provider="openai_compatible",
    chunk_seconds=300,
    local_device="cpu",
    progress_callback=None,
):
    if provider != "local_faster_whisper" and not api_key:
        raise ValueError("ASR API key is missing.")

    audio = AudioSegment.from_file(audio_path)
    audio = _prepare_audio_for_asr(audio)
    audio_duration_sec = len(audio) / 1000.0
    if audio_duration_sec <= 0:
        return []

    all_segments = []
    step_ms = max(1, int(chunk_seconds * 1000))

    total_chunks = (len(audio) + step_ms - 1) // step_ms
    for chunk_index, chunk_start_ms in enumerate(range(0, len(audio), step_ms), start=1):
        chunk_end_ms = min(chunk_start_ms + step_ms, len(audio))
        chunk = audio[chunk_start_ms:chunk_end_ms]

        if progress_callback:
            progress_callback(f"ASR chunk {chunk_index}/{total_chunks}...")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            chunk_path = tmp.name
        try:
            chunk.export(chunk_path, format="mp3", bitrate="128k")

            if provider == "elevenlabs":
                local_segments = _elevenlabs_transcribe_chunk(
                    chunk_path=chunk_path,
                    api_key=api_key,
                    base_url=base_url,
                    language=language,
                    model=model
                )
            elif provider == "local_faster_whisper":
                local_segments = _local_faster_whisper_transcribe_chunk(
                    chunk_path=chunk_path,
                    model=model or "small",
                    language=language,
                    device=local_device,
                )
            else:
                local_segments = _openai_transcribe_chunk(
                    chunk_path=chunk_path,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    language=language
                )

            offset = chunk_start_ms / 1000.0
            for seg in local_segments:
                start = float(seg.get("start", 0.0)) + offset
                end = float(seg.get("end", 0.0)) + offset
                text = seg.get("text", "").strip()
                if not text:
                    continue
                if end <= start:
                    end = start + 1.0
                all_segments.append({
                    "start": start,
                    "end": end,
                    "text": text
                })
        finally:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)

    return all_segments


def save_segments_to_srt(segments, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, start=1):
            f.write(f"{idx}\n")
            f.write(f"{_format_timestamp(seg['start'])} --> {_format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")
