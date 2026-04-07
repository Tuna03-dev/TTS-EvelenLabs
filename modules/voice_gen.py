import requests
import json
import base64
import os
from config import ELEVENLABS_API_KEY

# Default Voice ID for James/Daniel
DEFAULT_VOICE_ID = "onwK4R9RrjmqSoxS88ve" 
# Multilingual v2 model
DEFAULT_MODEL_ID = "eleven_multilingual_v2"

def generate_speech_with_timestamps(text, voice_id=DEFAULT_VOICE_ID, model_id=DEFAULT_MODEL_ID):
    """
    Calls ElevenLabs API with timestamps support.
    Returns (audio_bytes, alignment_dict).
    """
    if not ELEVENLABS_API_KEY:
        raise ValueError("ElevenLabs API Key is missing. Please add it to Settings.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    
    headers = {
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    data = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"ElevenLabs API Error: {response.text}")
        
    res_data = response.json()
    
    # Audio is base64 encoded
    audio_bytes = base64.b64decode(res_data["audio_base64"])
    alignment = res_data["alignment"]
    
    return audio_bytes, alignment

def format_timestamp(seconds):
    """Helper to format seconds into SRT timestamp (HH:MM:SS,mmm)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msecs = int((seconds * 1000) % 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"

def create_srt_from_alignment(alignment, chars_per_segment=40):
    """
    Converts ElevenLabs alignment data into a list of SRT segments.
    Groups characters into readable chunks.
    """
    characters = alignment["characters"]
    start_times = alignment["character_start_times_seconds"]
    end_times = alignment["character_end_times_seconds"]
    
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
