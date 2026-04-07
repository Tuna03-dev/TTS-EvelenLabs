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
    BASE_OUTPUT_DIR
)
from modules.processor import generate_video_pack
from modules.voice_gen import generate_speech_with_timestamps, create_srt_from_alignment, save_srt_file
from modules.audio_engine import stitch_video_pack

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Bible Video Automation",
    page_icon="📜",
    layout="wide",
)

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
    st.image("https://img.icons8.com/isometric/512/bible.png", width=128)
    st.title("Settings")
    
    api_key = st.text_input("ElevenLabs API Key", type="password", value=ELEVENLABS_API_KEY, help="Required for audio generation")
    voice_id = st.text_input("Voice ID (James/Daniel)", value="onwK4R9RrjmqSoxS88ve")
    model_id = st.selectbox("Model", ["eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2"], index=0)
    
    output_dir = st.text_input("Output Directory", value=st.session_state.get('output_dir', BASE_OUTPUT_DIR), help="Folder where video packs will be saved")
    target_len = st.number_input("Target Duration (Seconds)", value=TARGET_DURATION_SECONDS)
    wpm = st.number_input("Words Per Minute (WPM)", value=WPM_ESTIMATE)
    
    st.divider()
    st.info("💡 Target: 3:33:33 is 12,813s")

# --- MAIN DASHBOARD ---
st.title("📜 Bible Video Automation")
st.markdown("Automate your KJV Bible content creation with smart shuffling and precise duration estimation.")

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
    gen_full = st.button("🎬 Generate FULL 3.5h Video Pack")
with col_gen2:
    gen_demo = st.button("🧪 Demo (2 Chapters + Audio)")

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
    if api_key:
        st.subheader("🎙️ Generating Audio & Subtitles")
        audio_dir = os.path.join(pack_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        
        chapters_to_process = metadata["chapters"][:2] if gen_demo else metadata["chapters"]
        
        for i, ch in enumerate(chapters_to_process):
            update_status(f"ElevenLabs: Generating {ch['book']} {ch['chapter']}...")
            text_path = os.path.join(pack_dir, "text", ch["file"])
            with open(text_path, "r", encoding="utf-8") as tf:
                txt = tf.read()
                
            audio_bytes, alignment = generate_speech_with_timestamps(txt, voice_id=voice_id, model_id=model_id)
            
            # Save MP3
            audio_filename = ch["file"].replace(".txt", ".mp3")
            audio_path = os.path.join(audio_dir, audio_filename)
            with open(audio_path, "wb") as af:
                af.write(audio_bytes)
                
            # Create/Save SRT
            srt_filename = ch["file"].replace(".txt", ".srt")
            srt_path = os.path.join(audio_dir, srt_filename)
            segments = create_srt_from_alignment(alignment)
            save_srt_file(segments, srt_path)
            
            # Update metadata
            ch["audio_file"] = audio_filename
            ch["srt_file"] = srt_filename
            
            progress_bar.progress(int((i + 1) / len(chapters_to_process) * 100))
            
        # 3. Stitch Everything
        update_status("Finalizing: Stitching audio and merging subtitles...")
        final_mp3, final_srt = stitch_video_pack(pack_dir, chapters_to_process, target_seconds=effective_target)
        
        st.success(f"✅ Demo Pack ready at `{pack_dir}`")
        st.write(f"Final MP3: `{final_mp3}`")
        st.write(f"Final SRT: `{final_srt}`")
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
                            if not api_key:
                                st.error("Please enter your ElevenLabs API Key in the sidebar.")
                            else:
                                with st.status(f"Processing Demo for {p}..."):
                                    audio_dir = os.path.join(output_dir, p, "audio")
                                    os.makedirs(audio_dir, exist_ok=True)
                                    # Pick first 2 chapters from metadata
                                    demo_chapters = meta["chapters"][:2]
                                    
                                    for i, ch in enumerate(demo_chapters):
                                        st.write(f"🎙️ Generating: {ch['book']} {ch['chapter']}")
                                        text_path = os.path.join(output_dir, p, "text", ch["file"])
                                        with open(text_path, "r", encoding="utf-8") as tf:
                                            txt = tf.read()
                                            
                                        # Use voice_gen module
                                        a_bytes, align = generate_speech_with_timestamps(txt, voice_id=voice_id, model_id=model_id)
                                        
                                        a_fn = ch["file"].replace(".txt", ".mp3")
                                        a_path = os.path.join(audio_dir, a_fn)
                                        with open(a_path, "wb") as af:
                                            af.write(a_bytes)
                                            
                                        s_fn = ch["file"].replace(".txt", ".srt")
                                        s_path = os.path.join(audio_dir, s_fn)
                                        segs = create_srt_from_alignment(align)
                                        save_srt_file(segs, s_path)
                                        
                                        ch["audio_file"] = a_fn
                                        ch["srt_file"] = s_fn
                                    
                                    # Stitch for demo
                                    st.write("Merging files...")
                                    f_mp3, f_srt = stitch_video_pack(os.path.join(output_dir, p), demo_chapters, target_seconds=600)
                                    st.success(f"Demo for {p} Done! Files saved in `{p}/final/`")

                st.write(f"Path: `{os.path.join(output_dir, p)}`")
else:
    st.write("No output directory found.")

