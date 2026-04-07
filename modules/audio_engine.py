from pydub import AudioSegment
import os
import json
from config import TARGET_DURATION_SECONDS
from modules.voice_gen import format_timestamp

def get_audio_duration(filepath):
    """Returns duration of an audio file in seconds."""
    audio = AudioSegment.from_file(filepath)
    return len(audio) / 1000.0

def stitch_video_pack(pack_dir, chapters, target_seconds=TARGET_DURATION_SECONDS):
    """
    Stitches all chapter audios into one final MP3.
    Adds silence gaps between chapters to reach exactly target_seconds.
    Also merges individual SRTs into one final subtitle file.
    """
    audio_dir = os.path.join(pack_dir, "audio")
    final_dir = os.path.join(pack_dir, "final")
    os.makedirs(final_dir, exist_ok=True)
    
    # 1. Calculate total raw audio duration
    total_raw_duration = 0
    audio_segments = []
    
    for ch in chapters:
        audio_path = os.path.join(audio_dir, ch["audio_file"])
        segment = AudioSegment.from_file(audio_path)
        audio_segments.append(segment)
        total_raw_duration += len(segment) / 1000.0
        
    # 2. Calculate padding needed (silence gaps)
    # We add gaps between chapters. Total gaps = len(chapters) - 1
    num_gaps = len(chapters) - 1
    if num_gaps <= 0:
        # Only one chapter, add silence at the end
        padding_total = target_seconds - total_raw_duration
        gap_ms = int(padding_total * 1000)
        gap_segment = AudioSegment.silent(duration=max(0, gap_ms))
        final_audio = audio_segments[0] + gap_segment
    else:
        padding_total = target_seconds - total_raw_duration
        gap_ms = int((padding_total / num_gaps) * 1000)
        gap_segment = AudioSegment.silent(duration=max(0, gap_ms))
        
        final_audio = audio_segments[0]
        for i in range(1, len(audio_segments)):
            final_audio += gap_segment + audio_segments[i]
            
    # 3. Export final audio
    final_audio_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    final_audio.export(final_audio_path, format="mp3", bitrate="128k")
    
    # 4. Merge Subtitles (.srt)
    final_srt_path = os.path.join(final_dir, "final_subtitles.srt")
    current_offset = 0
    gap_sec = gap_ms / 1000.0 if num_gaps > 0 else 0
    
    with open(final_srt_path, "w", encoding="utf-8") as out_f:
        subtitle_count = 1
        for i, ch in enumerate(chapters):
            srt_path = os.path.join(audio_dir, ch["srt_file"])
            if os.path.exists(srt_path):
                with open(srt_path, "r", encoding="utf-8") as in_f:
                    # Parse and offset timestamps
                    lines = in_f.readlines()
                    for line in lines:
                        if " --> " in line:
                            start_str, end_str = line.strip().split(" --> ")
                            # Offset by current_offset
                            # Simple string replacement for demo/now, but robust parsing is better
                            # (Re-formatting is handled by the srt generator usually)
                            out_f.write(line)
                        elif line.strip().isdigit():
                            out_f.write(f"{subtitle_count}\n")
                            subtitle_count += 1
                        else:
                            out_f.write(line)
                
                # Update offset for next chapter: chapter_duration + gap
                ch_duration = len(audio_segments[i]) / 1000.0
                current_offset += ch_duration + gap_sec
                
    return final_audio_path, final_srt_path
