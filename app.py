import streamlit as st
import sys
import hashlib
import importlib
try:
    audioop = importlib.import_module("audioop")
except ImportError:
    try:
        audioop = importlib.import_module("audioop_lts")
        sys.modules['audioop'] = audioop
    except ImportError:
        audioop = None
import os
import json
import logging
import tkinter as tk
from tkinter import filedialog
from config import (
    TARGET_DURATION_SECONDS, 
    WPM_ESTIMATE, 
    BASE_OUTPUT_DIR,
    TTS_API_KEY,
    TTS_BASE_URL,
    TTS_PROVIDER,
    ASR_PROVIDER,
    ASR_API_KEY,
    ASR_BASE_URL,
    ASR_MODEL,
    ASR_LANGUAGE,
    ASR_CHUNK_SECONDS,
    ASR_FALLBACK_TO_ALIGNMENT,
    ASR_LOCAL_DEVICE,
    MAX_WORKERS,
)
from modules.pipeline import run_pipeline
from modules.voice_gen import generate_speech_with_timestamps, generate_chunked_speech, generate_chunked_speech_parallel, create_srt_from_alignment, save_srt_file, get_edge_voices, get_edge_male_presets, get_kokoro_voice_presets
from modules.transcriber import transcribe_audio_to_segments, save_segments_to_srt
from modules.audio_engine import stitch_video_pack, validate_audio_file, validate_srt_file, OutputValidationError
from modules.subtitle_config import SubtitleStyle, hex_to_ass, position_to_alignment, ass_alpha_from_opacity
from modules.video_builder import build_video, render_ass_preview_image

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Bible Video Automation",
    page_icon="📜",
    layout="wide",
)

def is_tts_api_required(provider_name):
    return provider_name == "elevenlabs"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bible_video_automation")


def _resolve_target_seconds(mode: str, demo_unit: str, demo_value: float, full_target: float) -> float:
    if mode == "Demo":
        if demo_unit == "Minutes":
            return max(60.0, float(demo_value) * 60.0)
        return max(60.0, float(demo_value))
    return max(60.0, float(full_target))


def _resolve_fetch_workers(mode: str) -> int:
    if mode == "Demo":
        return 3
    return min(6, int(MAX_WORKERS))


def _normalize_voice_option(option: dict) -> dict:
    """Adapter: normalize provider-specific voice payload to one schema."""
    voice_id = option.get("id") or option.get("voice") or option.get("ShortName") or ""
    display_name = (
        option.get("label")
        or option.get("name")
        or option.get("FriendlyName")
        or voice_id
        or "Unknown voice"
    )
    return {
        "id": voice_id,
        "display": display_name,
        "rate": option.get("rate", "-10%"),
        "pitch": option.get("pitch", "0Hz"),
        "raw": option,
    }


def _normalize_voice_options(options):
    return [_normalize_voice_option(v) for v in (options or [])]


def _init_generation_state():
    defaults = {
        "current_pack": None,
        "current_metadata": None,
        "current_target_seconds": None,
        "audio_ready": False,
        "video_ready": False,
        "ui_logs": [],
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _append_ui_log(message: str, level: str = "INFO"):
    line = f"[{level}] {message}"
    logs = st.session_state.setdefault("ui_logs", [])
    logs.append(line)
    st.session_state.ui_logs = logs[-300:]
    getattr(logger, level.lower(), logger.info)(message)


def _clear_ui_logs():
    st.session_state.ui_logs = []


def _render_ui_logs():
    with st.expander("Live Logs", expanded=False):
        st.text_area(
            "Console mirror",
            value="\n".join(st.session_state.get("ui_logs", [])),
            height=260,
            key="ui_logs_view",
            disabled=True,
        )


def _generate_audio_and_subtitles(
    pack_dir,
    metadata,
    chapters_to_process,
    update_status,
    tts_provider,
    api_key,
    voice_id,
    model_id,
    tts_base_url,
    tts_rate,
    tts_pitch,
    tts_depth_semitones,
    tts_softness,
    tts_max_workers,
    subtitle_source,
    asr_provider,
    asr_api_key,
    asr_base_url,
    asr_model,
    asr_language,
    asr_chunk_seconds,
    asr_local_device,
    asr_fallback_to_alignment,
    fetch_workers,
):
    audio_dir = os.path.join(pack_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    chapter_results = []
    for i, ch in enumerate(chapters_to_process):
        ch_id = f"{ch['book']} {ch['chapter']}"
        ch_result = {
            "id": ch_id,
            "success": False,
            "audio_file": None,
            "srt_file": None,
            "error": None,
            "audio_bytes_size": 0,
            "audio_duration_sec": 0,
            "srt_segments": 0,
        }

        try:
            update_status(f"📖 [{i+1}/{len(chapters_to_process)}] Generating {ch_id} via {tts_provider}...")
            text_path = os.path.join(pack_dir, "text", ch["file"])

            if not os.path.exists(text_path):
                raise FileNotFoundError(f"Text file not found: {text_path}")

            with open(text_path, "r", encoding="utf-8") as tf:
                txt = tf.read()

            if not txt.strip():
                raise ValueError(f"Text file is empty: {text_path}")

            if "VideoLingo" in subtitle_source:
                target_lang = asr_language if asr_language else ("vi" if "vi" in voice_id.lower() or "viet" in voice_id.lower() else "en")
                update_status(f"🚀 [VideoLingo] Processing {ch_id}...")
                audio_bytes, segments = generate_chunked_speech_parallel(
                    txt,
                    api_key=api_key,
                    voice_id=voice_id,
                    model_id=model_id,
                    base_url=tts_base_url,
                    provider=tts_provider,
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                    tts_depth_semitones=tts_depth_semitones,
                    tts_softness=tts_softness,
                    lang=target_lang,
                    max_words=25,
                    max_chars=120,
                    max_workers=tts_max_workers,
                    progress_callback=update_status,
                )
                alignment = None
            else:
                update_status(f"🔊 Generating single-shot audio for {ch_id}...")
                audio_bytes, alignment = generate_speech_with_timestamps(
                    txt,
                    api_key=api_key,
                    voice_id=voice_id,
                    model_id=model_id,
                    base_url=tts_base_url,
                    provider=tts_provider,
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                    tts_depth_semitones=tts_depth_semitones,
                    tts_softness=tts_softness,
                )
                segments = None

            if not audio_bytes or len(audio_bytes) == 0:
                raise ValueError(f"TTS returned empty audio for {ch_id}")

            ch_result["audio_bytes_size"] = len(audio_bytes)
            audio_filename = ch["file"].replace(".txt", ".mp3")
            audio_path = os.path.join(audio_dir, audio_filename)

            with open(audio_path, "wb") as af:
                af.write(audio_bytes)

            try:
                validate_audio_file(audio_path)
                from pydub import AudioSegment
                audio_obj = AudioSegment.from_file(audio_path)
                ch_result["audio_duration_sec"] = len(audio_obj) / 1000.0
            except Exception as e:
                raise OutputValidationError(f"MP3 validation failed: {e}")

            ch_result["audio_file"] = audio_filename
            update_status(f"📝 Finalizing subtitles for {ch_id}...")
            srt_filename = ch["file"].replace(".txt", ".srt")
            srt_path = os.path.join(audio_dir, srt_filename)

            if "VideoLingo" in subtitle_source:
                if not segments:
                    update_status(f"❌ {ch_id}: No segments returned!")
                    raise ValueError(f"No SRT segments were generated for {ch_id}.")

                update_status(f"💾 Saving {len(segments)} segments to {srt_filename}...")
                save_srt_file(segments, srt_path)

                if not os.path.exists(srt_path) or os.path.getsize(srt_path) == 0:
                    raise ValueError(f"Failed to write SRT file: {srt_path}")

                ch_result["srt_segments"] = len(segments)
            elif subtitle_source.startswith("ASR"):
                try:
                    update_status(f"🗣️ Transcribing audio for {ch_id}...")
                    segments = transcribe_audio_to_segments(
                        audio_path=audio_path,
                        api_key=asr_api_key,
                        base_url=asr_base_url,
                        model=asr_model,
                        language=asr_language or None,
                        provider=asr_provider,
                        chunk_seconds=asr_chunk_seconds,
                        local_device=asr_local_device,
                        progress_callback=update_status,
                    )
                    save_segments_to_srt(segments, srt_path)
                    ch_result["srt_segments"] = len(segments)
                except Exception as asr_err:
                    if asr_fallback_to_alignment:
                        update_status(f"⚠️ ASR failed for {ch_id}, fallback to alignment: {str(asr_err)[:100]}")
                        segments = create_srt_from_alignment(alignment)
                        save_srt_file(segments, srt_path)
                        ch_result["srt_segments"] = len(segments)
                    else:
                        raise
            else:
                segments = create_srt_from_alignment(alignment)
                save_srt_file(segments, srt_path)
                ch_result["srt_segments"] = len(segments)

            try:
                validate_srt_file(srt_path, min_segments=0)
            except OutputValidationError as srt_err:
                update_status(f"⚠️ SRT validation warning for {ch_id}: {str(srt_err)[:100]}")

            ch_result["srt_file"] = srt_filename
            ch_result["success"] = True
            ch["audio_file"] = audio_filename
            ch["srt_file"] = srt_filename
            update_status(f"✅ {ch_id} completed (audio: {ch_result['audio_duration_sec']:.1f}s, srt: {ch_result['srt_segments']} segments)")

        except Exception as e:
            import traceback
            full_error = traceback.format_exc()
            ch_result["error"] = full_error
            update_status(f"❌ {ch_id} FAILED: {str(e)[:150]}")
            with st.sidebar.expander(f"Debug Error: {ch_id}"):
                st.code(full_error)

        chapter_results.append(ch_result)

    successful_chapters = [r for r in chapter_results if r["success"]]
    failed_chapters = [r for r in chapter_results if not r["success"]]

    chapters_to_stitch = [
        ch for ch in chapters_to_process
        if any(r["success"] and r["id"] == f"{ch['book']} {ch['chapter']}" for r in chapter_results)
    ]

    final_mp3 = None
    final_srt = None
    if successful_chapters:
        update_status(f"Finalizing: Stitching {len(successful_chapters)} audio files and merging subtitles...")
        final_mp3, final_srt = stitch_video_pack(pack_dir, chapters_to_stitch, target_seconds=st.session_state.current_target_seconds)

    return chapter_results, successful_chapters, failed_chapters, chapters_to_stitch, final_mp3, final_srt


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    color = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {max(0.0, min(alpha, 1.0))})"
    
# --- CUSTOM CSS (PREMIUM GLASSMORPHISM) ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
        color: #f0f0f0;
    }

    .main {
        background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #312e81 100%);
        padding: 2rem;
    }

    .stApp > div:first-child {
        background: transparent;
    }
    
    .stApp {
        background: linear-gradient(135deg, #0c001e 0%, #1a0a33 100%);
    }

    section[data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.05) !important;
        backdrop-filter: blur(10px);
        border-right: 1px solid rgba(255, 255, 255, 0.1);
    }

    div.stButton > button {
        background: linear-gradient(90deg, #6366f1 0%, #a855f7 100%);
        color: white;
        border: none;
        padding: 0.5rem 0.75rem;
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);
        width: 100%;
        font-size: 0.9rem;
        white-space: nowrap;
    }

    div.stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(99, 102, 241, 0.6);
        color: white;
    }

    .css-1r6slb0, .stAlert {
        background: rgba(255, 255, 255, 0.07) !important;
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 15px;
        color: #e2e8f0 !important;
    }

    h1, h2, h3 {
        color: #fff;
        font-weight: 600;
        letter-spacing: -0.02em;
    }

    .stats-card {
        background: rgba(255, 255, 255, 0.05);
        padding: 1.5rem;
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        text-align: center;
        margin-bottom: 1rem;
    }

    .stats-val {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(90deg, #818cf8, #c084fc);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    ::-webkit-scrollbar {
        width: 8px;
    }
    ::-webkit-scrollbar-track {
        background: rgba(255, 255, 255, 0.05);
    }
    ::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.2);
        border-radius: 10px;
    }

    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://img.icons8.com/isometric/512/bible.png", width=96)
    st.title("Control Panel")
    st.caption("Chọn engine, chạy thử nhanh, rồi mở rộng sang full pack.")

    with st.expander("1. Text To Speech", expanded=True):
        provider_options = ["edge-tts", "kokoro", "elevenlabs"]
        default_provider_idx = provider_options.index(TTS_PROVIDER) if TTS_PROVIDER in provider_options else 0
        tts_provider = st.selectbox("Provider", provider_options, index=default_provider_idx)
        if tts_provider == "edge-tts":
            st.success("✅ Sử dụng Edge-TTS miễn phí, không cần API Key.")
        elif tts_provider == "kokoro":
            st.info("🎙️ Kokoro-TTS local: giọng tự nhiên, ấm và chậm. Không cần API Key.")
        else:
            tts_api_key = st.text_input("ElevenLabs API Key", type="password", value=TTS_API_KEY).strip()
        
        if tts_provider == "kokoro":
            tts_style = st.selectbox(
                "Giọng đọc",
                ["Bible warm gentle", "Bible soft peaceful", "Bible calm male", "Bible deep male", "Michael male"],
                index=0,
            )
        else:
            tts_style = st.selectbox("Giọng đọc", ["Default", "Male warm", "Male calm", "Male deep", "Male sleepy", "Male energetic"], index=1 if tts_provider == "edge-tts" else 0)
        tts_max_workers = st.slider(
            "TTS parallel workers",
            min_value=1,
            max_value=8,
            value=3,
            help="Tăng tốc TTS theo chunk. Nếu gặp rate limit hoặc lỗi rỗng, giảm xuống 1-2."
        )
        tts_speed_factor = st.slider(
            "Tốc độ đọc (speed factor)",
            min_value=0.6,
            max_value=1.2,
            value=0.7,
            step=0.05,
            help="0.7 = chậm hơn đáng kể, phù hợp giọng đọc Bible từ tốn."
        )
        tts_depth_semitones = st.slider(
            "Độ trầm (semitones xuống)",
            min_value=0.0,
            max_value=4.0,
            value=1.5,
            step=0.5,
            help="Tăng để giọng trầm hơn. Áp dụng cho cả Edge-TTS và Kokoro-TTS.",
        )
        tts_softness = st.slider(
            "Độ dịu giọng (anti-chói)",
            min_value=0.0,
            max_value=1.0,
            value=0.7,
            step=0.05,
            help="Tăng để giảm chói/cao, nghe nhẹ và êm hơn.",
        )

    with st.expander("2. Subtitle Configuration", expanded=True):
        subtitle_source = st.selectbox(
            "Phương pháp tạo Sub",
            ["VideoLingo Mode (Tách chunk + Ghép audio) 🔥"],
            index=0
        )
        st.info("Kỹ thuật VideoLingo: Chia văn bản thành các câu nhỏ -> tạo tiếng -> ghép lại. Đảm bảo sub khớp 100% không cần AI tách tiếng.")

    with st.expander("3. Output Settings", expanded=True):
        output_dir = st.text_input("Thư mục lưu kết quả", value=st.session_state.get('output_dir', BASE_OUTPUT_DIR))
        generation_mode = st.selectbox("Generation mode", ["Demo", "Full"], index=1)
        if generation_mode == "Full":
            full_target_len = st.number_input("Tổng thời lượng Full (Giây)", value=TARGET_DURATION_SECONDS)
            demo_target_unit = "Minutes"
            demo_target_value = 10
        else:
            demo_target_unit = st.selectbox("Demo target unit", ["Minutes", "Seconds"], index=0)
            if demo_target_unit == "Minutes":
                demo_target_value = st.number_input(
                    "Demo target value (minutes)",
                    value=10,
                    min_value=1,
                    step=1,
                )
            else:
                demo_target_value = st.number_input(
                    "Demo target value (seconds)",
                    value=600,
                    min_value=60,
                    step=1,
                )
            full_target_len = TARGET_DURATION_SECONDS
        wpm = st.number_input("Tốc độ nói (WPM)", value=WPM_ESTIMATE)

    tts_api_key = tts_api_key if "tts_api_key" in locals() else TTS_API_KEY
    tts_base_url = TTS_BASE_URL
    asr_language = ASR_LANGUAGE
    asr_api_key = ASR_API_KEY
    asr_base_url = ASR_BASE_URL
    asr_model = ASR_MODEL
    asr_chunk_seconds = ASR_CHUNK_SECONDS
    asr_fallback_to_alignment = ASR_FALLBACK_TO_ALIGNMENT
    asr_local_device = ASR_LOCAL_DEVICE
    asr_provider = ASR_PROVIDER
    
    api_key = tts_api_key if tts_provider == "elevenlabs" else ""

    voices = []
    models = []
    tts_rate = "-10%"
    tts_pitch = "0Hz"
    if tts_provider == "edge-tts":
        with st.spinner("Loading Edge TTS voices..."):
            male_presets = get_edge_male_presets()
            if tts_style == "Male warm":
                voices = [p for p in male_presets if p["label"] == "Male warm - Ryan"]
            elif tts_style == "Male calm":
                voices = [p for p in male_presets if p["label"] == "Male calm - Guy"]
            elif tts_style == "Male deep":
                voices = [p for p in male_presets if p["label"] == "Male deep - Connor"]
            elif tts_style == "Male sleepy":
                voices = [p for p in male_presets if p["label"] == "Male sleepy - Andrew"]
            elif tts_style == "Male energetic":
                voices = [p for p in male_presets if p["label"] == "Male energetic - Brandon"]
            else:
                voices = get_edge_voices()
    elif tts_provider == "kokoro":
        with st.spinner("Loading Kokoro voice presets..."):
            kokoro_presets = get_kokoro_voice_presets()
            if tts_style == "Bible soft peaceful":
                voices = [p for p in kokoro_presets if p["label"] == "Bible soft peaceful - Heart"]
            elif tts_style == "Bible calm male":
                voices = [p for p in kokoro_presets if p["label"] == "Bible calm male - Adam"]
            elif tts_style in ("Bible deep male", "Michael male"):
                voices = [p for p in kokoro_presets if p["label"] == "Bible deep male - Michael"]
            else:
                voices = [p for p in kokoro_presets if p["label"] == "Bible warm gentle - Sarah"]
    elif api_key:
        with st.spinner("Syncing your ElevenLabs account..."):
            from modules.voice_gen import get_voices, get_models
            voices = get_voices(api_key, tts_base_url)
            models = get_models(api_key, tts_base_url)

    st.divider()
    st.info("💡 Target mặc định: 3:33:33 = 12,813s")

    if voices:
        normalized_voices = _normalize_voice_options(voices)
        voice_labels = [f"🗣️ {v['display']}" for v in normalized_voices]
        selected_voice_label = st.selectbox("Voice", voice_labels, index=0)
        selected_voice = normalized_voices[voice_labels.index(selected_voice_label)]
        voice_id = selected_voice["id"]
        tts_rate = selected_voice.get("rate", "-10%")
        tts_pitch = selected_voice.get("pitch", "0Hz")
    else:
        default_voice = "en-US-AriaNeural" if tts_provider == "edge-tts" else ("af_sarah" if tts_provider == "kokoro" else "TxGEqnSAs9dnLURhk9Wb")
        voice_id = st.text_input("Voice / ShortName", value=default_voice)

    # Global override so user can custom slow/fast regardless preset.
    tts_rate = f"{(tts_speed_factor - 1.0) * 100:+.0f}%"

    if models and tts_provider == "elevenlabs":
        model_labels = [f"🤖 {m['name']}" for m in models]
        default_idx = 0
        for i, m in enumerate(models):
            if "flash" in m['id'].lower() or "turbo" in m['id'].lower():
                default_idx = i
                break
        selected_model_label = st.selectbox("Model", model_labels, index=default_idx)
        model_id = models[model_labels.index(selected_model_label)]["id"]
    else:
        model_id = ""

_init_generation_state()
effective_target = _resolve_target_seconds(generation_mode, demo_target_unit, demo_target_value, full_target_len)
st.session_state.current_target_seconds = effective_target

_render_ui_logs()

# --- MAIN DASHBOARD ---
st.title("📜 Bible Video Automation")
st.markdown("A guided pipeline for generating audio, subtitles, and final packs with a simpler control flow.")

col_intro1, col_intro2, col_intro3 = st.columns(3)
with col_intro1:
    st.markdown('<div class="stats-card"><div class="stats-val">1</div><div>Chọn TTS</div></div>', unsafe_allow_html=True)
with col_intro2:
    st.markdown('<div class="stats-card"><div class="stats-val">2</div><div>Chọn Subtitle</div></div>', unsafe_allow_html=True)
with col_intro3:
    st.markdown('<div class="stats-card"><div class="stats-val">3</div><div>Generate</div></div>', unsafe_allow_html=True)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown('<div class="stats-card"><div class="stats-val">~3.5h</div><div>Target Length</div></div>', unsafe_allow_html=True)
with col2:
    st.markdown('<div class="stats-card"><div class="stats-val">KJV</div><div>Translation</div></div>', unsafe_allow_html=True)
with col3:
    st.markdown('<div class="stats-card"><div class="stats-val">🚀 High Speed</div><div>Parallel Enabled</div></div>', unsafe_allow_html=True)

st.divider()
st.caption("Audio-first mode: step 1 now fetches text, generates TTS, and stitches the selected pack.")

fetch_col, audio_col = st.columns(2)
with fetch_col:
    fetch_text = st.button("1) Build Pack (Fetch + TTS)", type="primary")
with audio_col:
    can_generate_audio = st.session_state.current_pack is not None and not st.session_state.audio_ready
    generate_audio = st.button("2) Generate Audio", disabled=not can_generate_audio)

reset_col1, reset_col2 = st.columns(2)
with reset_col1:
    reset_workflow = st.button("Reset workflow")
with reset_col2:
    clear_logs = st.button("Clear logs")

if reset_workflow:
    st.session_state.current_pack = None
    st.session_state.current_metadata = None
    st.session_state.audio_ready = False
    st.session_state.video_ready = False
    st.session_state.current_target_seconds = effective_target
    _clear_ui_logs()
    _append_ui_log("Workflow reset requested.", "INFO")
    st.success("Workflow reset.")

if clear_logs:
    _clear_ui_logs()
    st.success("Logs cleared.")

if fetch_text:
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    status_container = st.empty()
    progress_bar = st.progress(0)

    def update_status(msg):
        status_container.write(f"💬 {msg}")
        _append_ui_log(msg, "INFO")

    _clear_ui_logs()
    try:
        update_status(f"Starting fetch text phase. mode={generation_mode}, target={st.session_state.current_target_seconds:.1f}s")
        metadata = run_pipeline(
            output_base_dir=output_dir,
            tts_kwargs={
                "api_key": api_key,
                "voice_id": voice_id,
                "model_id": model_id,
                "base_url": tts_base_url,
                "provider": tts_provider,
                "tts_rate": tts_rate,
                "tts_pitch": tts_pitch,
                "tts_depth_semitones": tts_depth_semitones,
                "tts_softness": tts_softness,
                "lang": "en",
                "max_words": 25,
                "max_chars": 120,
            },
            progress_callback=update_status,
            target_seconds=st.session_state.current_target_seconds,
            batch_size=8,
            fetch_workers=_resolve_fetch_workers(generation_mode),
            tts_workers=tts_max_workers,
        )
        pack_dir = metadata["pack_dir"]
        st.session_state.current_pack = pack_dir
        st.session_state.current_metadata = metadata
        st.session_state.audio_ready = True
        st.session_state.video_ready = False
        _append_ui_log(f"Text fetched for pack: {pack_dir}", "INFO")
        st.success(f"✅ Audio-first pack ready: {pack_dir}")
        st.write(f"Chapters selected: {metadata['chapters_count']}")
        st.write(f"Actual duration: {int(metadata.get('actual_duration', metadata.get('final_duration', 0)))}s")
    except Exception as fetch_err:
        import traceback
        full_error = traceback.format_exc()
        _append_ui_log(f"Fetch phase failed: {fetch_err}", "ERROR")
        st.error(f"❌ Fetch phase failed: {fetch_err}")
        with st.expander("Fetch Error Traceback"):
            st.code(full_error)

if generate_audio:
    if not st.session_state.current_pack or not st.session_state.current_metadata:
        st.error("Fetch text first before generating audio.")
        _append_ui_log("Generate audio blocked: no fetched pack in session.", "ERROR")
    elif is_tts_api_required(tts_provider) and not api_key:
        st.warning("Chọn edge-tts nếu muốn chạy miễn phí không cần API key.")
        _append_ui_log("Generate audio blocked: missing TTS API key.", "WARNING")
    else:
        if subtitle_source.startswith("ASR") and asr_provider != "local_faster_whisper" and not asr_api_key:
            st.error("ASR subtitle mode is selected but ASR API Key is missing.")
            _append_ui_log("Generate audio blocked: missing ASR API key.", "ERROR")
        else:
            status_container = st.empty()
            progress_bar = st.progress(0)

            def update_status(msg):
                status_container.write(f"💬 {msg}")
                _append_ui_log(msg, "INFO")

            pack_dir = st.session_state.current_pack
            metadata = st.session_state.current_metadata
            chapters_to_process = metadata["chapters"]
            _append_ui_log(f"Starting audio phase with {len(chapters_to_process)} chapters.", "INFO")
            st.subheader("🎙️ Generating Audio & Subtitles")
            if tts_provider == "edge-tts":
                st.caption("edge-tts: free, no local server, requires internet only.")
            elif tts_provider == "kokoro":
                st.caption("Kokoro-TTS: local inference, natural soft voice. Có thể cần cài espeak-ng trên Windows.")
            else:
                st.caption("ElevenLabs: requires API key and network access.")

            chapter_results, successful_chapters, failed_chapters, chapters_to_stitch, final_mp3, final_srt = _generate_audio_and_subtitles(
                pack_dir=pack_dir,
                metadata=metadata,
                chapters_to_process=chapters_to_process,
                update_status=update_status,
                tts_provider=tts_provider,
                api_key=api_key,
                voice_id=voice_id,
                model_id=model_id,
                tts_base_url=tts_base_url,
                tts_rate=tts_rate,
                tts_pitch=tts_pitch,
                tts_depth_semitones=tts_depth_semitones,
                tts_softness=tts_softness,
                tts_max_workers=tts_max_workers,
                subtitle_source=subtitle_source,
                asr_provider=asr_provider,
                asr_api_key=asr_api_key,
                asr_base_url=asr_base_url,
                asr_model=asr_model,
                asr_language=asr_language,
                asr_chunk_seconds=asr_chunk_seconds,
                asr_local_device=asr_local_device,
                asr_fallback_to_alignment=asr_fallback_to_alignment,
                fetch_workers=_resolve_fetch_workers(generation_mode),
            )

            if not successful_chapters:
                st.error("❌ No chapters were generated successfully. Cannot proceed to stitching.")
            else:
                if failed_chapters:
                    st.warning(f"⚠️ {len(failed_chapters)} chapter(s) failed, but proceeding with {len(successful_chapters)} successful chapters.")

                try:
                    final_mp3, final_srt = stitch_video_pack(pack_dir, chapters_to_stitch, target_seconds=st.session_state.current_target_seconds)
                    validate_audio_file(final_mp3)
                    _append_ui_log(f"Audio stitch complete: {final_mp3}", "INFO")
                    st.success(f"✅ Audio phase complete at `{pack_dir}`")
                    st.write(f"Final audio: `{final_mp3}`")
                    st.write(f"Final SRT: `{final_srt}`")
                    st.session_state.audio_ready = True
                    st.session_state.video_ready = False
                except OutputValidationError as ve:
                    _append_ui_log(f"Audio stitch failed: {ve}", "ERROR")
                    st.error(f"❌ Stitching failed: {ve}")
                except Exception as e:
                    _append_ui_log(f"Audio phase unexpected error: {e}", "ERROR")
                    st.error(f"❌ Unexpected error during stitching: {e}")

                st.subheader("📊 Generation Summary")
                summary_cols = st.columns(4)
                with summary_cols[0]:
                    st.metric("Total Chapters", len(chapters_to_process))
                with summary_cols[1]:
                    st.metric("✅ Successful", len(successful_chapters))
                with summary_cols[2]:
                    st.metric("❌ Failed", len(failed_chapters))
                with summary_cols[3]:
                    total_audio_dur = sum(r["audio_duration_sec"] for r in successful_chapters)
                    st.metric("Total Duration", f"{int(total_audio_dur)}s")

                with st.expander("📋 Detailed Chapter Results"):
                    for result in chapter_results:
                        status_icon = "✅" if result["success"] else "❌"
                        with st.expander(f"{status_icon} {result['id']}"):
                            if result["success"]:
                                st.success(f"Audio file: {result['audio_file']}")
                                st.write(f"  - Size: {result['audio_bytes_size']:,} bytes")
                                st.write(f"  - Duration: {result['audio_duration_sec']:.1f}s")
                                st.success(f"SRT file: {result['srt_file']}")
                                st.write(f"  - Segments: {result['srt_segments']}")
                            else:
                                st.error(f"Error: {result['error']}")

if st.session_state.current_pack:
    st.info("Phase 3: video export is available in the current pack card below. Upload a background video there and press Export MP4.")

    current_pack_dir = st.session_state.current_pack
    current_metadata = st.session_state.current_metadata

    if current_pack_dir and current_metadata:
        with st.expander("📊 Pack Details"):
            st.json(current_metadata)

        st.subheader("📁 Generated Files")
        text_dir = os.path.join(current_pack_dir, "text")
        if os.path.exists(text_dir):
            files = os.listdir(text_dir)
            st.write(f"Folders created: {len(files)} text files.")
            for f in sorted(files)[:10]:
                st.code(f)
            if len(files) > 10:
                st.write(f"... and {len(files)-10} more.")
        else:
            st.warning("Current pack text directory not found yet.")
    else:
        st.warning("No fetched pack is loaded in session yet. Use '1) Fetch Text Chapters' first.")

# --- LIST RECENT PACKS ---
st.divider()
st.subheader("📚 Recent Video Packs (Global / Persistent)")
if os.path.exists(output_dir):
    packs = [d for d in os.listdir(output_dir) if d.startswith("video_pack_")]
    if not packs:
        st.write(f"No packs found in `{output_dir}` yet.")
    else:
        for p in sorted(packs, reverse=True):
            with st.expander(f"📦 {p}"):
                p_path = os.path.join(output_dir, p, "metadata.json")
                if os.path.exists(p_path):
                    with open(p_path, 'r') as f:
                        meta = json.load(f)
                        total_duration = meta.get('actual_duration', meta.get('final_duration', 0))
                        st.write(f"Total Duration (Actual): {int(total_duration // 3600)}h {int((total_duration % 3600) // 60)}m {int(total_duration % 60)}s")
                        st.write(f"Chapters: {meta['chapters_count']}")

                        st.markdown("#### 🎞️ Export Video (Loop + ASS Subtitle)")
                        uploaded_bg = st.file_uploader(
                            "Background video (MP4/MOV/WEBM)",
                            type=["mp4", "mov", "webm"],
                            key=f"bg_{p}",
                        )

                        style_col1, style_col2, style_col3 = st.columns(3)
                        with style_col1:
                            sub_font = st.selectbox(
                                "Font",
                                [
                                    "Segoe UI",
                                    "Montserrat",
                                    "Poppins",
                                    "Playfair Display",
                                    "Cormorant Garamond",
                                    "Gabriola",
                                    "Segoe Script",
                                    "Lucida Handwriting",
                                    "Monotype Corsiva",
                                    "Palatino Linotype",
                                    "Georgia",
                                ],
                                index=10,
                                key=f"font_{p}",
                            )
                            sub_size = st.slider("Size", 14, 92, 54, key=f"size_{p}")
                            sub_bold = st.checkbox("Bold", value=False, key=f"bold_{p}")
                        with style_col2:
                            sub_color = st.color_picker("Text color", "#FFFFFF", key=f"color_{p}")
                            sub_outline_color = st.color_picker("Outline color", "#000000", key=f"outline_color_{p}")
                            sub_outline_opacity = st.slider("Outline opacity", 0, 100, 60, key=f"outline_opacity_{p}")
                            sub_has_box = st.checkbox("Background box", value=True, key=f"has_box_{p}")
                        with style_col3:
                            sub_position = st.selectbox(
                                "Position",
                                ["Bottom center", "Top center", "Middle center"],
                                index=0,
                                key=f"pos_{p}",
                            )
                            sub_outline = st.slider("Outline", 0.0, 8.0, 8.0, 0.5, key=f"outline_{p}")
                            sub_margin_v = st.slider("Vertical margin", 10, 120, 100, key=f"margin_{p}")

                        layout_col1, layout_col2, layout_col3 = st.columns(3)
                        with layout_col1:
                            aspect_label = st.selectbox(
                                "Output ratio",
                                ["16:9 (YouTube)", "9:16 (Shorts/TikTok)", "1:1 (Square)"],
                                index=0,
                                key=f"ratio_{p}",
                            )
                            aspect_map = {
                                "16:9 (YouTube)": (1920, 1080),
                                "9:16 (Shorts/TikTok)": (1080, 1920),
                                "1:1 (Square)": (1080, 1080),
                            }
                            export_width, export_height = aspect_map[aspect_label]
                        with layout_col2:
                            sub_box_padding_ass = st.slider(
                                "Box padding (export)",
                                0,
                                6,
                                2,
                                key=f"box_pad_ass_{p}",
                                help="Padding ngang cho ASS/libass (mô phỏng bằng khoảng trắng cứng).",
                            )
                        with layout_col3:
                            sub_wrap_chars = st.slider(
                                "Wrap length (chars/line)",
                                24,
                                120,
                                72,
                                key=f"wrap_chars_{p}",
                                help="Tăng giá trị để subtitle giữ được dài hơn trước khi xuống dòng.",
                            )
                            st.caption("Preview hiện dùng đúng FFmpeg/libass nên sẽ giống output export.")

                        quality_col1, quality_col2 = st.columns(2)
                        with quality_col1:
                            export_preset = st.selectbox(
                                "Encode preset",
                                ["veryfast", "faster", "fast", "medium"],
                                index=2,
                                key=f"preset_{p}",
                            )
                        with quality_col2:
                            export_crf = st.slider("Quality (CRF, lower is better)", 14, 28, 18, key=f"crf_{p}")

                        preview_text = st.text_input(
                            "Preview text",
                            value="In the beginning God created the heaven and the earth.",
                            key=f"preview_text_{p}",
                        )

                        style = SubtitleStyle(
                            font_family=sub_font,
                            font_size=sub_size,
                            primary_color=hex_to_ass(sub_color, alpha="00"),
                            outline_color=hex_to_ass(sub_outline_color, alpha=ass_alpha_from_opacity(sub_outline_opacity)),
                            back_color=hex_to_ass("#000000", alpha=ass_alpha_from_opacity(65)) if sub_has_box else "&HFF000000",
                            bold=sub_bold,
                            italic=False,
                            uppercase=False,
                            outline=sub_outline,
                            shadow=0.0,
                            alignment=position_to_alignment(sub_position),
                            margin_v=sub_margin_v,
                            has_box=sub_has_box,
                            box_padding_h=sub_box_padding_ass,
                            wrap_chars=sub_wrap_chars,
                        )

                        if uploaded_bg:
                            pack_dir = os.path.join(output_dir, p)
                            assets_dir = os.path.join(pack_dir, "video_assets")
                            os.makedirs(assets_dir, exist_ok=True)
                            bg_path = os.path.join(assets_dir, uploaded_bg.name)

                            with open(bg_path, "wb") as bgf:
                                bgf.write(uploaded_bg.getbuffer())

                            preview_img = os.path.join(pack_dir, "final", "subtitle_preview_exact.jpg")
                            try:
                                _append_ui_log(f"Rendering live preview for pack {p} at {export_width}x{export_height}.", "INFO")
                                preview_sig = hashlib.sha1(
                                    "|".join([
                                        uploaded_bg.name,
                                        str(uploaded_bg.size),
                                        preview_text,
                                        sub_font,
                                        str(sub_size),
                                        sub_color,
                                        sub_outline_color,
                                        str(sub_outline_opacity),
                                        str(sub_has_box),
                                        sub_position,
                                        str(sub_outline),
                                        str(sub_margin_v),
                                        str(sub_box_padding_ass),
                                        str(sub_wrap_chars),
                                        str(export_width),
                                        str(export_height),
                                    ]).encode("utf-8")
                                ).hexdigest()
                                cached_sig = st.session_state.get(f"preview_sig_{p}")
                                cached_path = st.session_state.get(f"preview_path_{p}")
                                if cached_sig != preview_sig or not cached_path or not os.path.exists(cached_path):
                                    with st.spinner("Rendering live preview with FFmpeg/libass..."):
                                        preview_out = render_ass_preview_image(
                                            background_video_path=bg_path,
                                            style=style,
                                            preview_text=preview_text,
                                            out_image_path=preview_img,
                                            width=export_width,
                                            height=export_height,
                                        )
                                    st.session_state[f"preview_sig_{p}"] = preview_sig
                                    st.session_state[f"preview_path_{p}"] = preview_out
                                else:
                                    preview_out = cached_path
                                st.caption(f"Live preview ({aspect_label}) from the exact export pipeline.")
                                st.image(preview_out, caption="Live exact preview", use_container_width=True)
                            except Exception as preview_err:
                                _append_ui_log(f"Preview render failed for {p}: {preview_err}", "ERROR")
                                st.error(f"Preview render failed: {preview_err}")

                        if st.button(f"Export MP4 for {p}", key=f"export_video_{p}", type="primary"):
                            if not uploaded_bg:
                                st.error("Please upload a background video first.")
                                _append_ui_log(f"Export blocked for {p}: no background video uploaded.", "ERROR")
                            else:
                                pack_dir = os.path.join(output_dir, p)
                                assets_dir = os.path.join(pack_dir, "video_assets")
                                os.makedirs(assets_dir, exist_ok=True)
                                bg_path = os.path.join(assets_dir, uploaded_bg.name)

                                with open(bg_path, "wb") as bgf:
                                    bgf.write(uploaded_bg.getbuffer())

                                try:
                                    _append_ui_log(f"Starting video export for {p} with preset={export_preset}, crf={export_crf}.", "INFO")
                                    with st.spinner("Rendering video with FFmpeg..."):
                                        out_video = build_video(
                                            pack_dir=pack_dir,
                                            background_video_path=bg_path,
                                            style=style,
                                            output_name="output_video.mp4",
                                            crf=export_crf,
                                            preset=export_preset,
                                            width=export_width,
                                            height=export_height,
                                        )
                                    _append_ui_log(f"Video export complete for {p}: {out_video}", "INFO")
                                    st.success(f"✅ Export complete: {out_video}")
                                    st.video(out_video)
                                except Exception as export_err:
                                    _append_ui_log(f"Video export failed for {p}: {export_err}", "ERROR")
                                    st.error(f"❌ Export failed: {export_err}")
                        
                        if st.button(f"🧪 Test TTS (First 2 Chapters)", key=f"demo_{p}"):
                            if is_tts_api_required(tts_provider) and not api_key:
                                st.error("Please enter your TTS API Key in the sidebar.")
                            elif subtitle_source.startswith("ASR") and asr_provider != "local_faster_whisper" and not asr_api_key:
                                st.error("Please enter your ASR API Key in the sidebar for Audio->SRT mode.")
                            else:
                                demo_status = st.empty()
                                demo_progress = st.progress(0)
                                demo_status.info(f"Processing Demo for {p}...")
                                audio_dir = os.path.join(output_dir, p, "audio")
                                os.makedirs(audio_dir, exist_ok=True)
                                # Pick first 2 chapters from metadata
                                demo_chapters = meta["chapters"][:2]
                                demo_results = []
                                
                                for i, ch in enumerate(demo_chapters):
                                    ch_id = f"{ch['book']} {ch['chapter']}"
                                    ch_result = {
                                        "id": ch_id,
                                        "success": False,
                                        "error": None,
                                    }
                                    
                                    try:
                                        demo_status.write(f"🎙️ [{i+1}/2] Generating: {ch_id}")
                                        text_path = os.path.join(output_dir, p, "text", ch["file"])
                                        
                                        if not os.path.exists(text_path):
                                            raise FileNotFoundError(f"Text file not found: {text_path}")
                                        
                                        with open(text_path, "r", encoding="utf-8") as tf:
                                            txt = tf.read()
                                        
                                        if not txt.strip():
                                            raise ValueError(f"Text file is empty: {text_path}")
                                            
                                        # --- DEMO GENERATION BRANCH ---
                                        if "VideoLingo" in subtitle_source:
                                            target_lang = asr_language if asr_language else ("vi" if "vi" in voice_id.lower() or "viet" in voice_id.lower() else "en")
                                            a_bytes, segs = generate_chunked_speech_parallel(
                                                txt,
                                                api_key=api_key,
                                                voice_id=voice_id,
                                                model_id=model_id,
                                                base_url=tts_base_url,
                                                provider=tts_provider,
                                                tts_rate=tts_rate,
                                                tts_pitch=tts_pitch,
                                                tts_depth_semitones=tts_depth_semitones,
                                                tts_softness=tts_softness,
                                                lang=target_lang,
                                                max_words=25,
                                                max_chars=120,
                                                max_workers=tts_max_workers,
                                                progress_callback=lambda msg: demo_status.write(f"  {msg}")
                                            )
                                            # Alignment not needed
                                            align = None
                                        else:
                                            a_bytes, align = generate_speech_with_timestamps(
                                                txt,
                                                api_key=api_key,
                                                voice_id=voice_id,
                                                model_id=model_id,
                                                base_url=tts_base_url,
                                                provider=tts_provider,
                                                tts_rate=tts_rate,
                                                tts_pitch=tts_pitch,
                                                tts_depth_semitones=tts_depth_semitones,
                                                tts_softness=tts_softness,
                                            )
                                            segs = None
                                        
                                        if not a_bytes or len(a_bytes) == 0:
                                            raise ValueError(f"TTS returned empty audio")
                                        
                                        a_fn = ch["file"].replace(".txt", ".mp3")
                                        a_path = os.path.join(audio_dir, a_fn)
                                        with open(a_path, "wb") as af:
                                            af.write(a_bytes)
                                        
                                        # Validate audio
                                        try:
                                            validate_audio_file(a_path)
                                        except OutputValidationError as e:
                                            raise ValueError(f"Audio validation failed: {e}")
                                        
                                        s_fn = ch["file"].replace(".txt", ".srt")
                                        s_path = os.path.join(audio_dir, s_fn)
                                        
                                        if "VideoLingo" in subtitle_source:
                                            if not segs:
                                                raise ValueError("No segments generated in VideoLingo mode.")
                                            save_srt_file(segs, s_path)
                                        elif "ASR" in subtitle_source:
                                            try:
                                                demo_status.write(f"📝 Transcribing: {ch_id}")
                                                segs = transcribe_audio_to_segments(
                                                    audio_path=a_path,
                                                    api_key=asr_api_key,
                                                    base_url=asr_base_url,
                                                    model=asr_model,
                                                    language=asr_language or None,
                                                    provider=asr_provider,
                                                    chunk_seconds=asr_chunk_seconds,
                                                    local_device=asr_local_device,
                                                    progress_callback=lambda msg: demo_status.write(f"  {msg}"),
                                                )
                                                save_segments_to_srt(segs, s_path)
                                            except Exception as asr_err:
                                                if asr_fallback_to_alignment:
                                                    demo_status.write(f"⚠️ ASR failed, fallback to alignment")
                                                    segs = create_srt_from_alignment(align)
                                                    save_srt_file(segs, s_path)
                                                else:
                                                    raise
                                        else:
                                            segs = create_srt_from_alignment(align)
                                            save_srt_file(segs, s_path)
                                        
                                        # Validate SRT
                                        try:
                                            validate_srt_file(s_path, min_segments=0)
                                        except OutputValidationError as e:
                                            demo_status.write(f"⚠️ SRT validation issue: {str(e)[:100]}")
                                        
                                        ch["audio_file"] = a_fn
                                        ch["srt_file"] = s_fn
                                        ch_result["success"] = True
                                        demo_status.write(f"✅ {ch_id} completed")
                                        
                                    except Exception as e:
                                        ch_result["error"] = str(e)
                                        demo_status.write(f"❌ {ch_id} failed: {str(e)[:100]}")
                                    
                                    demo_results.append(ch_result)
                                    demo_progress.progress((i+1) / 2)
                                
                                # Check if stitching can proceed
                                successful = [r for r in demo_results if r["success"]]
                                if not successful:
                                    st.error("❌ No chapters completed successfully, cannot stitch.")
                                else:
                                    # Temporary preview merge for successful chapters
                                    demo_status.write(f"🎬 Stitching {len(successful)} chapters (preview)...")
                                    try:
                                        demo_chapters_to_stitch = [ch for ch in demo_chapters if any(r["success"] and r["id"] == f"{ch['book']} {ch['chapter']}" for r in demo_results)]
                                        f_mp3, f_srt = stitch_video_pack(os.path.join(output_dir, p), demo_chapters_to_stitch, target_seconds=None)
                                        
                                        # Validate final outputs
                                        try:
                                            validate_audio_file(f_mp3)
                                            st.success(f"✅ Demo completed successfully!")
                                        except OutputValidationError as e:
                                            st.error(f"⚠️ Final audio has issues: {e}")
                                        
                                        st.write(f"Preview MP3: `{f_mp3}`")
                                        st.write(f"Preview SRT: `{f_srt}`")
                                    except OutputValidationError as ve:
                                        st.error(f"❌ Stitching failed: {ve}")
                                    except Exception as e:
                                        st.error(f"❌ Unexpected error: {e}")

                st.write(f"Path: `{os.path.join(output_dir, p)}`")
else:
    st.write("No output directory found.")

