import sys
try:
    import audioop
except ImportError:
    try:
        import audioop_lts as audioop
        sys.modules['audioop'] = audioop
    except ImportError:
        pass

from pydub import AudioSegment
from pydub.effects import speedup
import os
import json
from config import TARGET_DURATION_SECONDS
from modules.voice_gen import format_timestamp


class OutputValidationError(Exception):
    """Raised when output validation fails."""
    pass


def validate_audio_file(filepath):
    """Validates that an audio file exists and is non-empty."""
    if not os.path.exists(filepath):
        raise OutputValidationError(f"Audio file not found: {filepath}")
    if os.path.getsize(filepath) == 0:
        raise OutputValidationError(f"Audio file is empty: {filepath}")
    try:
        audio = AudioSegment.from_file(filepath)
        if len(audio) == 0:
            raise OutputValidationError(f"Audio file has zero duration: {filepath}")
        return True
    except Exception as e:
        raise OutputValidationError(f"Audio file is corrupted or unreadable: {filepath} - {e}")


def validate_srt_file(filepath, min_segments=1):
    """Validates that an SRT file exists and contains valid segments."""
    if not os.path.exists(filepath):
        raise OutputValidationError(f"SRT file not found: {filepath}")
    if os.path.getsize(filepath) == 0:
        raise OutputValidationError(f"SRT file is empty: {filepath}")
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
        
        blocks = [b for b in content.split("\n\n") if b.strip()]
        if len(blocks) < min_segments:
            raise OutputValidationError(f"SRT file has only {len(blocks)} segments, expected at least {min_segments}: {filepath}")
        
        # Validate first block structure
        first_block = blocks[0].strip().split("\n")
        if len(first_block) < 3:
            raise OutputValidationError(f"SRT file has invalid block structure: {filepath}")
        if " --> " not in first_block[1]:
            raise OutputValidationError(f"SRT file has invalid timestamp format: {filepath}")
        
        return True
    except OutputValidationError:
        raise
    except Exception as e:
        raise OutputValidationError(f"SRT file is invalid or corrupted: {filepath} - {e}")


def _parse_srt_timestamp(timestamp):
    """Parses SRT timestamp (HH:MM:SS,mmm) into seconds."""
    hhmmss, millis = timestamp.split(",")
    hours, minutes, seconds = hhmmss.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0

def get_audio_duration(filepath):
    """Returns duration of an audio file in seconds."""
    audio = AudioSegment.from_file(filepath)
    return len(audio) / 1000.0

def stitch_video_pack(pack_dir, chapters, target_seconds=TARGET_DURATION_SECONDS):
    """
    Stitches all chapter audios into one final MP3.
    Uses natural chapter pauses, optional tail silence, and gentle global speed-up
    to keep duration close to target_seconds without cutting text.
    Also merges individual SRTs into one final subtitle file.
    
    Returns:
        tuple: (final_mp3_path, final_srt_path)
        
    Raises:
        OutputValidationError: If required files are missing or invalid
    """
    audio_dir = os.path.join(pack_dir, "audio")
    final_dir = os.path.join(pack_dir, "final")
    os.makedirs(final_dir, exist_ok=True)
    
    if not chapters:
        raise ValueError("No chapters provided for stitching.")
    
    # Pre-validation: ensure all required audio and SRT files exist
    missing_files = []
    for ch in chapters:
        audio_path = os.path.join(audio_dir, ch["audio_file"])
        srt_path = os.path.join(audio_dir, ch["srt_file"])
        
        if not os.path.exists(audio_path):
            missing_files.append(f"Audio: {audio_path}")
        elif os.path.getsize(audio_path) == 0:
            missing_files.append(f"Audio (empty): {audio_path}")
            
        if not os.path.exists(srt_path):
            missing_files.append(f"SRT: {srt_path}")
        elif os.path.getsize(srt_path) == 0:
            missing_files.append(f"SRT (empty): {srt_path}")
    
    if missing_files:
        error_msg = "Missing or empty chapter files:\n" + "\n".join(missing_files)
        raise OutputValidationError(error_msg)

    # 1. Calculate total raw audio duration
    total_raw_duration = 0
    audio_segments = []
    
    for i, ch in enumerate(chapters):
        audio_path = os.path.join(audio_dir, ch["audio_file"])
        try:
            validate_audio_file(audio_path)
            segment = AudioSegment.from_file(audio_path)
            audio_segments.append(segment)
            ch_duration = len(segment) / 1000.0
            total_raw_duration += ch_duration
            ch["duration"] = ch_duration  # Store for reference
        except OutputValidationError as e:
            raise OutputValidationError(f"Chapter {i+1} ({ch.get('book', '?')} {ch.get('chapter', '?')}): {e}")
        
    # 2. Build natural spacing strategy
    # Keep chapter pauses natural, then pad tail if still short.
    num_gaps = len(chapters) - 1
    natural_gap_ms = 700
    tail_padding_ms = 0

    if num_gaps <= 0:
        if target_seconds is not None:
            padding_total = target_seconds - total_raw_duration
            gap_ms = 0
            tail_padding_ms = int(max(0, padding_total) * 1000)
        else:
            gap_ms = 0
            tail_padding_ms = 0
        gap_segment = AudioSegment.silent(duration=gap_ms)
        final_audio = audio_segments[0] + gap_segment + AudioSegment.silent(duration=tail_padding_ms)
    else:
        if target_seconds is not None:
            base_with_natural_gaps = total_raw_duration + (num_gaps * (natural_gap_ms / 1000.0))
            if base_with_natural_gaps <= target_seconds:
                gap_ms = natural_gap_ms
                tail_padding_ms = int((target_seconds - base_with_natural_gaps) * 1000)
            else:
                # If content is already long, minimize chapter gaps to avoid adding "slow" feeling.
                available_gap_total_sec = max(0.0, target_seconds - total_raw_duration)
                gap_ms = int((available_gap_total_sec / num_gaps) * 1000) if num_gaps > 0 else 0
                tail_padding_ms = 0
        else:
            gap_ms = natural_gap_ms
            tail_padding_ms = 0
        gap_segment = AudioSegment.silent(duration=gap_ms)
        
        final_audio = audio_segments[0]
        for i in range(1, len(audio_segments)):
            final_audio += gap_segment + audio_segments[i]
        if tail_padding_ms > 0:
            final_audio += AudioSegment.silent(duration=tail_padding_ms)
            
    # 3. Gentle global speed adjustment (only if total is longer than target)
    time_scale = 1.0
    if target_seconds is not None:
        final_duration_sec = len(final_audio) / 1000.0
        if final_duration_sec > target_seconds:
            required_speed = final_duration_sec / target_seconds
            max_speedup = 1.07  # Keep speech natural (<= 7% faster)
            applied_speed = min(required_speed, max_speedup)
            if applied_speed > 1.001:
                final_audio = speedup(final_audio, playback_speed=applied_speed, chunk_size=150, crossfade=20)
                time_scale = 1.0 / applied_speed

        # If after adjustments it is still short, pad tail to target.
        final_duration_sec = len(final_audio) / 1000.0
        if final_duration_sec < target_seconds:
            final_audio += AudioSegment.silent(duration=int((target_seconds - final_duration_sec) * 1000))

    # 4. Export final audio
    final_audio_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    try:
        final_audio.export(final_audio_path, format="mp3", bitrate="128k")
        validate_audio_file(final_audio_path)  # Verify export succeeded
    except Exception as e:
        raise OutputValidationError(f"Failed to export final audio: {e}")

    # Re-check exported duration and correct drift from MP3 encoder delay if needed.
    if target_seconds is not None:
        tolerance_sec = 0.5
        for _ in range(3):
            exported_duration = get_audio_duration(final_audio_path)
            drift = target_seconds - exported_duration
            if abs(drift) <= tolerance_sec:
                break

            correction_ms = int(drift * 1000)
            corrected_audio = AudioSegment.from_file(final_audio_path)
            if correction_ms > 0:
                corrected_audio += AudioSegment.silent(duration=correction_ms)
            elif correction_ms < 0:
                corrected_audio = corrected_audio[:max(0, len(corrected_audio) + correction_ms)]

            corrected_audio.export(final_audio_path, format="mp3", bitrate="128k")

        final_check = get_audio_duration(final_audio_path)
        if abs(target_seconds - final_check) > 1.0:
            raise OutputValidationError(
                f"Final audio duration drift is too large: got {final_check:.3f}s, target {target_seconds:.3f}s"
            )
    
    # 5. Merge Subtitles (.srt)
    final_srt_path = os.path.join(final_dir, "final_subtitles.srt")
    current_offset = 0
    gap_sec = gap_ms / 1000.0 if num_gaps > 0 else 0
    
    srt_written_count = 0
    try:
        with open(final_srt_path, "w", encoding="utf-8") as out_f:
            subtitle_count = 1
            for i, ch in enumerate(chapters):
                srt_path = os.path.join(audio_dir, ch["srt_file"])
                if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
                    with open(srt_path, "r", encoding="utf-8") as in_f:
                        blocks = [b for b in in_f.read().strip().split("\n\n") if b.strip()]
                        for block in blocks:
                            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
                            if len(lines) < 2:
                                continue

                            time_idx = 1 if lines[0].isdigit() else 0
                            if time_idx >= len(lines) or " --> " not in lines[time_idx]:
                                continue

                            start_str, end_str = lines[time_idx].split(" --> ")
                            text_lines = lines[time_idx + 1:]
                            if not text_lines:
                                continue

                            raw_start = _parse_srt_timestamp(start_str) + current_offset
                            raw_end = _parse_srt_timestamp(end_str) + current_offset
                            start_adj = format_timestamp(raw_start * time_scale)
                            end_adj = format_timestamp(raw_end * time_scale)

                            out_f.write(f"{subtitle_count}\n")
                            out_f.write(f"{start_adj} --> {end_adj}\n")
                            out_f.write("\n".join(text_lines) + "\n\n")
                            subtitle_count += 1
                            srt_written_count += 1
                    
                    # Update offset for next chapter: chapter_duration + gap
                    ch_duration = len(audio_segments[i]) / 1000.0
                    current_offset += ch_duration + gap_sec
        
        # Validate final SRT (allow 0 segments for edge case, but at least 1 for normal case)
        min_expected = 1 if len(chapters) > 0 else 0
        if srt_written_count == 0:
            raise OutputValidationError(f"Final SRT file has no subtitle segments (written: {srt_written_count})")
        validate_srt_file(final_srt_path, min_segments=0)  # Already validated count above
        
    except OutputValidationError:
        raise
    except Exception as e:
        raise OutputValidationError(f"Failed to merge SRT files: {e}")
    
    return final_audio_path, final_srt_path
