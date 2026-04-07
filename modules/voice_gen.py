from elevenlabs.client import ElevenLabs
import json
import base64
import os

# Default Voice ID (Josh - Professional/Free-compatible)
DEFAULT_VOICE_ID = "TxGEqnSAs9dnLURhk9Wb" 
# Multilingual v2 model
DEFAULT_MODEL_ID = "eleven_flash_v2_5"

def get_voices(api_key):
    """Fetches available voices from ElevenLabs."""
    if not api_key: return []
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        v_list = client.voices.get_all()
        return [{"name": v.name, "id": v.voice_id} for v in v_list.voices]
    except Exception as e:
        print(f"Error fetching voices: {e}")
        return []

def get_models(api_key):
    """Fetches available models from ElevenLabs."""
    if not api_key: return []
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        m_list = client.models.get_all()
        # Filter for models that support TTS
        return [{"name": m.name, "id": m.model_id} for m in m_list if m.can_do_text_to_speech]
    except Exception as e:
        print(f"Error fetching models: {e}")
        return []

def generate_speech_with_timestamps(text, api_key, voice_id=DEFAULT_VOICE_ID, model_id=DEFAULT_MODEL_ID):
    """
    Calls ElevenLabs API using the official SDK with timestamps support.
    Returns (audio_bytes, alignment_dict).
    """
    if not api_key:
        raise ValueError("ElevenLabs API Key is missing. Please add it to Settings.")

    client = ElevenLabs(api_key=api_key)
    
    # Use the official SDK method for timestamps
    response = client.text_to_speech.convert_with_timestamps(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        voice_settings={
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    )
    
    # The SDK returns an object with audio_base64 and alignment
    # Note: alignment contains 'characters', 'character_start_times_seconds', 'character_end_times_seconds'
    audio_bytes = base64.b64decode(response.audio_base_64)
    alignment = response.alignment
    
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
    characters = alignment.characters
    start_times = alignment.character_start_times_seconds
    end_times = alignment.character_end_times_seconds
    
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
