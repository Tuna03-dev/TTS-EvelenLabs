import os
import re
import subprocess
import tempfile
from typing import List, Optional, Tuple

from modules.subtitle_config import SubtitleStyle


def _srt_to_ass_timestamp(srt_time: str) -> str:
    hhmmss, ms = srt_time.split(",")
    hh, mm, ss = hhmmss.split(":")
    cs = int(round(int(ms) / 10.0))
    if cs >= 100:
        cs = 99
    return f"{int(hh)}:{int(mm):02d}:{int(ss):02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _wrap_subtitle_line(line: str, max_chars: int) -> List[str]:
    words = line.split()
    if not words:
        return [line]

    wrapped_lines: List[str] = []
    current_line = words[0]

    for word in words[1:]:
        if len(current_line) + 1 + len(word) <= max_chars:
            current_line = f"{current_line} {word}"
        else:
            wrapped_lines.append(current_line)
            current_line = word

    wrapped_lines.append(current_line)

    hard_wrapped: List[str] = []
    for wrapped_line in wrapped_lines:
        if len(wrapped_line) <= max_chars:
            hard_wrapped.append(wrapped_line)
            continue
        start_index = 0
        while start_index < len(wrapped_line):
            hard_wrapped.append(wrapped_line[start_index : start_index + max_chars])
            start_index += max_chars

    return hard_wrapped


def _parse_srt_to_ass_events(srt_path: str, style: Optional[SubtitleStyle] = None) -> List[str]:
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    blocks = [b for b in re.split(r"\n\s*\n", content) if b.strip()]
    events = []

    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        ts_idx = 1 if lines[0].isdigit() else 0
        if ts_idx >= len(lines) or " --> " not in lines[ts_idx]:
            continue

        start_raw, end_raw = lines[ts_idx].split(" --> ")
        text_lines = lines[ts_idx + 1 :]
        if not text_lines:
            continue

        start = _srt_to_ass_timestamp(start_raw)
        end = _srt_to_ass_timestamp(end_raw)
        pad_count = max(0, int(style.box_padding_h)) if style else 0
        pad = r"\h" * pad_count
        if style and style.uppercase:
            text_lines = [line.upper() for line in text_lines]
        max_chars = max(24, min(120, int(style.wrap_chars))) if style else 72

        wrapped_lines: List[str] = []
        for line in text_lines:
            wrapped_lines.extend(_wrap_subtitle_line(line, max_chars=max_chars))

        # When subtitle wraps to multiple lines, avoid per-line hard-space padding
        # to reduce visual stacking/edge overlap artifacts with thick outlines.
        if len(wrapped_lines) > 1:
            padded_lines = [_escape_ass_text(line) for line in wrapped_lines]
        else:
            padded_lines = [f"{pad}{_escape_ass_text(line)}{pad}" for line in wrapped_lines]
        text = r"\N".join(padded_lines)
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return events


def srt_to_ass(srt_path: str, ass_path: str, style: SubtitleStyle, play_res: Tuple[int, int] = (1920, 1080)) -> str:
    events = _parse_srt_to_ass_events(srt_path, style=style)
    play_res_x, play_res_y = play_res

    ass_header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"{style.to_ass_style()}\n"
        "\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        if events:
            f.write("\n")

    return ass_path


def _escape_filter_path(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace(":", r"\:")
    p = p.replace("'", r"\'")
    return p


def build_video(
    pack_dir: str,
    background_video_path: str,
    style: SubtitleStyle,
    output_name: str = "output_video.mp4",
    crf: int = 18,
    preset: str = "fast",
    width: int = 1920,
    height: int = 1080,
) -> str:
    final_dir = os.path.join(pack_dir, "final")
    audio_path = os.path.join(final_dir, "full_audio_3h33.mp3")
    srt_path = os.path.join(final_dir, "final_subtitles.srt")

    if not os.path.exists(background_video_path):
        raise FileNotFoundError(f"Background video not found: {background_video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Final audio not found: {audio_path}")
    if not os.path.exists(srt_path):
        raise FileNotFoundError(f"Final subtitles not found: {srt_path}")

    os.makedirs(final_dir, exist_ok=True)
    ass_path = os.path.join(final_dir, "final_subtitles.ass")
    srt_to_ass(srt_path, ass_path, style=style, play_res=(width, height))

    out_path = os.path.join(final_dir, output_name)
    ass_filter_path = _escape_filter_path(ass_path)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"ass='{ass_filter_path}'"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        background_video_path,
        "-i",
        audio_path,
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        out_path,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg is not installed or not in PATH") from e

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1000:]}")

    return out_path


def render_ass_preview_image(
    background_video_path: str,
    style: SubtitleStyle,
    preview_text: str,
    out_image_path: str,
    width: int = 1920,
    height: int = 1080,
    seek_seconds: float = 1.0,
) -> str:
    """Render a one-frame subtitle preview using the same FFmpeg + ASS pipeline as export."""
    if not os.path.exists(background_video_path):
        raise FileNotFoundError(f"Background video not found: {background_video_path}")

    os.makedirs(os.path.dirname(out_image_path), exist_ok=True)

    text = (preview_text or "In the beginning God created the heaven and the earth.").strip()
    if not text:
        text = "In the beginning God created the heaven and the earth."

    with tempfile.TemporaryDirectory() as tmp_dir:
        srt_tmp = os.path.join(tmp_dir, "preview.srt")
        ass_tmp = os.path.join(tmp_dir, "preview.ass")
        with open(srt_tmp, "w", encoding="utf-8") as f:
            f.write("1\n")
            f.write("00:00:00,000 --> 00:00:04,000\n")
            f.write(text + "\n")

        srt_to_ass(srt_tmp, ass_tmp, style=style, play_res=(width, height))
        ass_filter_path = _escape_filter_path(ass_tmp)
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"ass='{ass_filter_path}'"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(max(0.0, seek_seconds)),
            "-i",
            background_video_path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            out_image_path,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg is not installed or not in PATH") from e

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg preview failed: {proc.stderr[-1000:]}")

    return out_image_path
