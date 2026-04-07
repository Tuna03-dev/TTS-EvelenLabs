import streamlit as st
import sys
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
        sys.modules['audioop'] = audioop
    except ImportError:
        pass
import os
import json
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
)
from modules.processor import generate_video_pack
from modules.voice_gen import generate_speech_with_timestamps, generate_chunked_speech, create_srt_from_alignment, save_srt_file, get_edge_voices, get_edge_male_presets
from modules.transcriber import transcribe_audio_to_segments, save_segments_to_srt
from modules.audio_engine import stitch_video_pack, validate_audio_file, validate_srt_file, OutputValidationError

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Bible Video Automation",
    page_icon="📜",
    layout="wide",
)

def is_tts_api_required(provider_name):
    return provider_name != "edge-tts"

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

    /* Glassmorphism Card */
    .stApp > div:first-child {
        background: transparent;
    }
    
    .stApp {
        background: linear-gradient(135deg, #0c001e 0%, #1a0a33 100%);
    }

    /* Sidebar Glassmorphism */
    section[data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.05) !important;
        backdrop-filter: blur(10px);
        border-right: 1px solid rgba(255, 255, 255, 0.1);
    }

    /* Progress and Status Cards */
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

    /* Scrollbar */
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
        tts_provider = st.selectbox("Provider", ["edge-tts", "elevenlabs"], index=0 if TTS_PROVIDER == "edge-tts" else 1)
        if tts_provider == "edge-tts":
            st.success("✅ Sử dụng Edge-TTS miễn phí, không cần API Key.")
        else:
            tts_api_key = st.text_input("ElevenLabs API Key", type="password", value=TTS_API_KEY).strip()
        
        tts_style = st.selectbox("Giọng đọc", ["Default", "Male warm", "Male calm", "Male deep", "Male energetic"], index=1 if tts_provider == "edge-tts" else 0)

    # Simplified ASR section (Hiding advanced API settings)
    with st.expander("2. Subtitle Configuration", expanded=True):
        subtitle_source = st.selectbox(
            "Phương pháp tạo Sub",
            ["VideoLingo Mode (Tách chunk + Ghép audio) 🔥"],
            index=0
        )
        st.info("Kỹ thuật VideoLingo: Chia văn bản thành các câu nhỏ -> tạo tiếng -> ghép lại. Đảm bảo sub khớp 100% không cần AI tách tiếng.")

    with st.expander("3. Output Settings", expanded=True):
        output_dir = st.text_input("Thư mục lưu kết quả", value=st.session_state.get('output_dir', BASE_OUTPUT_DIR))
        target_len = st.number_input("Tổng thời lượng (Giây)", value=TARGET_DURATION_SECONDS)
        wpm = st.number_input("Tốc độ nói (WPM)", value=WPM_ESTIMATE)

    # Default values for hidden/removed settings
    tts_api_key = tts_api_key if "tts_api_key" in locals() else TTS_API_KEY
    tts_base_url = TTS_BASE_URL
    asr_language = ASR_LANGUAGE
    asr_api_key = ASR_API_KEY
    asr_base_url = ASR_BASE_URL
    asr_model = ASR_MODEL
    asr_chunk_seconds = ASR_CHUNK_SECONDS
    asr_local_device = ASR_LOCAL_DEVICE
    asr_provider = ASR_PROVIDER
    
    api_key = tts_api_key if tts_provider == "elevenlabs" else ""

    voices = []
    models = []
    tts_rate = "0%"
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
            elif tts_style == "Male energetic":
                voices = [p for p in male_presets if p["label"] == "Male energetic - Brandon"]
            else:
                voices = get_edge_voices()
    elif api_key:
        with st.spinner("Syncing your ElevenLabs account..."):
            from modules.voice_gen import get_voices, get_models
            voices = get_voices(api_key, tts_base_url)
            models = get_models(api_key, tts_base_url)

    st.divider()
    st.info("💡 Target mặc định: 3:33:33 = 12,813s")

    if voices:
        if tts_provider == "edge-tts" and "label" in voices[0]:
            voice_labels = [f"🗣️ {v['label']}" for v in voices]
        else:
            voice_labels = [f"🗣️ {v['name'] if 'name' in v else v['id']}" for v in voices]
        selected_voice_label = st.selectbox("Voice", voice_labels, index=0)
        selected_voice = voices[voice_labels.index(selected_voice_label)]
        voice_id = selected_voice.get("id") or selected_voice.get("voice")
        tts_rate = selected_voice.get("rate", "0%")
        tts_pitch = selected_voice.get("pitch", "0Hz")
    else:
        default_voice = "en-US-AriaNeural" if tts_provider == "edge-tts" else "TxGEqnSAs9dnLURhk9Wb"
        voice_id = st.text_input("Voice / ShortName", value=default_voice)

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

col_gen1, col_gen2 = st.columns(2)
with col_gen1:
    gen_full = st.button("🎬 Generate FULL Pack")
with col_gen2:
    gen_demo = st.button("🧪 Quick Demo (2 Chapters)")

if gen_full or gen_demo:
    # Set demo target or full target
    effective_target = 600 if gen_demo else target_len # 10 mins for demo approx
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    status_container = st.empty()
    progress_bar = st.progress(0)
    
    def update_status(msg):
        status_container.write(f"💬 {msg}")
        
    start_info = st.info("Starting generation process...")
    
    # 1. Fetch Text Chapters
    metadata, pack_dir = generate_video_pack(output_base_dir=output_dir, progress_callback=update_status)
    
    # 2. Audio Generation (Phase 3)
    if is_tts_api_required(tts_provider) and not api_key:
        st.warning("Chọn edge-tts nếu muốn chạy miễn phí không cần API key.")
        st.stop()

    if api_key or tts_provider == "edge-tts":
        if subtitle_source.startswith("ASR") and asr_provider != "local_faster_whisper" and not asr_api_key:
            st.error("ASR subtitle mode is selected but ASR API Key is missing.")
            st.stop()

        st.subheader("🎙️ Generating Audio & Subtitles")
        if tts_provider == "edge-tts":
            st.caption("edge-tts: free, no local server, requires internet only.")
        else:
            st.caption("ElevenLabs: requires API key and network access.")
        audio_dir = os.path.join(pack_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        
        chapters_to_process = metadata["chapters"][:2] if gen_demo else metadata["chapters"]
        
        # Track each chapter's status
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
                
                # Load text
                if not os.path.exists(text_path):
                    raise FileNotFoundError(f"Text file not found: {text_path}")
                    
                with open(text_path, "r", encoding="utf-8") as tf:
                    txt = tf.read()
                    
                if not txt.strip():
                    raise ValueError(f"Text file is empty: {text_path}")
                
                # --- GENERATION BRANCH ---
                if "VideoLingo" in subtitle_source:
                    target_lang = asr_language if asr_language else ("vi" if "vi" in voice_id.lower() or "viet" in voice_id.lower() else "en")
                    update_status(f"🚀 [VideoLingo] Processing {ch_id}...")
                    
                    audio_bytes, segments = generate_chunked_speech(
                        txt,
                        api_key=api_key,
                        voice_id=voice_id,
                        model_id=model_id,
                        base_url=tts_base_url,
                        provider=tts_provider,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        lang=target_lang,
                        max_words=25,
                        progress_callback=update_status
                    )
                    alignment = None
                else:
                    # Original single-shot generation
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
                    )
                    segments = None
                
                if not audio_bytes or len(audio_bytes) == 0:
                    raise ValueError(f"TTS returned empty audio for {ch_id}")
                
                ch_result["audio_bytes_size"] = len(audio_bytes)
                
                # Save MP3
                audio_filename = ch["file"].replace(".txt", ".mp3")
                audio_path = os.path.join(audio_dir, audio_filename)
                
                with open(audio_path, "wb") as af:
                    af.write(audio_bytes)
                
                # Validate MP3
                try:
                    validate_audio_file(audio_path)
                    from pydub import AudioSegment
                    audio_obj = AudioSegment.from_file(audio_path)
                    ch_result["audio_duration_sec"] = len(audio_obj) / 1000.0
                except Exception as e:
                    raise OutputValidationError(f"MP3 validation failed: {e}")
                
                ch_result["audio_file"] = audio_filename
                
                # Create/Save SRT
                update_status(f"📝 Finalizing subtitles for {ch_id}...")
                srt_filename = ch["file"].replace(".txt", ".srt")
                srt_path = os.path.join(audio_dir, srt_filename)
                
                if "VideoLingo" in subtitle_source:
                    if not segments:
                        update_status(f"❌ {ch_id}: No segments returned!")
                        raise ValueError(f"No SRT segments were generated for {ch_id}.")
                    
                    update_status(f"💾 Saving {len(segments)} segments to {srt_filename}...")
                    save_srt_file(segments, srt_path)
                    
                    # Final disk check
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
                
                # Validate SRT
                try:
                    validate_srt_file(srt_path, min_segments=0)  # Allow empty edge case
                except OutputValidationError as srt_err:
                    # Log but don't fail if SRT has issues, as alignment-based might be sparse
                    update_status(f"⚠️ SRT validation warning for {ch_id}: {str(srt_err)[:100]}")
                
                ch_result["srt_file"] = srt_filename
                ch_result["success"] = True
                
                # Update metadata
                ch["audio_file"] = audio_filename
                ch["srt_file"] = srt_filename
                
                update_status(f"✅ {ch_id} completed (audio: {ch_result['audio_duration_sec']:.1f}s, srt: {ch_result['srt_segments']} segments)")
                
            except Exception as e:
                import traceback
                full_error = traceback.format_exc()
                ch_result["error"] = full_error
                update_status(f"❌ {ch_id} FAILED: {str(e)[:150]}")
                # Also log to a hidden expander for the user to copy if needed
                with st.sidebar.expander(f"Debug Error: {ch_id}"):
                    st.code(full_error)
            
            chapter_results.append(ch_result)
            progress_bar.progress(int((i + 1) / len(chapters_to_process) * 100))
        
        # Check if all chapters succeeded
        successful_chapters = [r for r in chapter_results if r["success"]]
        failed_chapters = [r for r in chapter_results if not r["success"]]
        
        if not successful_chapters:
            st.error("❌ No chapters were generated successfully. Cannot proceed to stitching.")
            st.subheader("⚠️ Chapter Generation Summary")
            for result in chapter_results:
                with st.expander(f"❌ {result['id']}"):
                    st.error(result["error"])
            st.stop()
        
        if failed_chapters:
            st.warning(f"⚠️ {len(failed_chapters)} chapter(s) failed, but proceeding with {len(successful_chapters)} successful chapters.")
            with st.expander("📋 Failed Chapters"):
                for result in failed_chapters:
                    st.write(f"**{result['id']}**: {result['error']}")
        
        # 3. Stitch Everything (only with successful chapters)
        update_status(f"Finalizing: Stitching {len(successful_chapters)} audio files and merging subtitles...")
        
        try:
            # Rebuild chapters_to_stitch to only include successful ones
            chapters_to_stitch = [ch for ch in chapters_to_process if any(r["success"] and r["id"] == f"{ch['book']} {ch['chapter']}" for r in chapter_results)]
            
            final_mp3, final_srt = stitch_video_pack(pack_dir, chapters_to_stitch, target_seconds=effective_target)
            
            # Validate final outputs
            try:
                validate_audio_file(final_mp3)
            except OutputValidationError as e:
                st.error(f"❌ Final audio validation failed: {e}")
                st.stop()
            
            st.success(f"✅ Demo Pack ready at `{pack_dir}`")
            st.write(f"final audio: `{final_mp3}`")
            st.write(f"Final SRT: `{final_srt}`")
            
        except OutputValidationError as ve:
            st.error(f"❌ Stitching failed: {ve}")
            st.stop()
        except Exception as e:
            st.error(f"❌ Unexpected error during stitching: {e}")
            st.stop()
        
        # Show generation summary
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
    else:
        st.warning("No API Key provided. Audio generation skipped.")
    
    with st.expander("📊 Pack Details"):
        st.json(metadata)
        
    # Show recently generated file list
    st.subheader("📁 Generated Files")
    text_dir = os.path.join(pack_dir, "text")
    files = os.listdir(text_dir)
    st.write(f"Folders created: {len(files)} text files.")
    for f in sorted(files)[:10]: # Show first 10
        st.code(f)
    if len(files) > 10:
        st.write(f"... and {len(files)-10} more.")

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
                        st.write(f"Total Duration (Estimated): {int(meta['final_duration'] // 3600)}h {int((meta['final_duration'] % 3600) // 60)}m {int(meta['final_duration'] % 60)}s")
                        st.write(f"Chapters: {meta['chapters_count']}")
                        
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
                                            a_bytes, segs = generate_chunked_speech(
                                                txt,
                                                api_key=api_key,
                                                voice_id=voice_id,
                                                model_id=model_id,
                                                base_url=tts_base_url,
                                                provider=tts_provider,
                                                tts_rate=tts_rate,
                                                tts_pitch=tts_pitch,
                                                lang=target_lang,
                                                max_words=25,
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

