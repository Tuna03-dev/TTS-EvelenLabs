import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydub import AudioSegment

from config import BASE_OUTPUT_DIR, TARGET_DURATION_SECONDS, TEXT_SUBDIR, WORDS_PER_SECOND
from modules.fetcher import fetch_chapter_text
from modules.processor import estimate_duration, get_interleaved_chapters
from modules.voice_gen import generate_chunked_speech_parallel, save_srt_file


def _safe_book_name(book: str) -> str:
    return (book or "chapter").strip().replace(" ", "_").lower()


def _normalize_chapter(entry, order_number):
    if isinstance(entry, dict):
        chapter = dict(entry)
        chapter.setdefault("book", chapter.get("book"))
        chapter.setdefault("chapter", chapter.get("chapter"))
        chapter["order"] = int(chapter.get("order", order_number))
        return chapter

    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return {
            "book": entry[0],
            "chapter": entry[1],
            "order": int(order_number),
        }

    raise TypeError(f"Unsupported chapter entry: {entry!r}")


def _parse_rate_factor(tts_rate):
    value = str(tts_rate or "0%").strip()
    match = re.match(r"^([+-]?)\s*(\d+(?:\.\d+)?)%$", value)
    if not match:
        return 1.0

    sign = -1.0 if match.group(1) == "-" else 1.0
    percentage = float(match.group(2)) * sign
    return max(0.5, 1.0 + (percentage / 100.0))


def _estimate_chapter_seconds(text, tts_rate="0%", calibration_factor=1.0):
    rate_factor = _parse_rate_factor(tts_rate)
    effective_wps = max(0.5, float(WORDS_PER_SECOND) * rate_factor)
    base_estimate = estimate_duration(text, words_per_second=effective_wps)
    # Conservative by design: estimate a bit longer than the raw text math.
    return max(0.1, base_estimate * max(1.0, float(calibration_factor)))


def _fetch_batch(chapters, fetch_workers=None, progress_callback=None):
    results = {}
    worker_count = max(1, int(fetch_workers or min(4, len(chapters) or 1)))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_chapter = {
            executor.submit(fetch_chapter_text, chapter["book"], chapter["chapter"]): chapter
            for chapter in chapters
        }

        for future in as_completed(future_to_chapter):
            chapter = future_to_chapter[future]
            key = (chapter["book"], chapter["chapter"])
            try:
                text = future.result()
            except Exception as exc:
                if progress_callback:
                    progress_callback(f"⚠️ Fetch failed for {chapter['book']} {chapter['chapter']}: {exc}")
                continue

            if text and text.strip():
                results[key] = text.strip()
            elif progress_callback:
                progress_callback(f"⚠️ Empty text for {chapter['book']} {chapter['chapter']}")

    return results


def _tts_batch(chapters, texts, audio_dir, tts_kwargs, tts_workers=None, progress_callback=None):
    results = []
    worker_count = max(1, int(tts_workers or min(4, len(chapters) or 1)))
    tts_kwargs = dict(tts_kwargs or {})

    def _generate_one(chapter):
        text = texts.get((chapter["book"], chapter["chapter"]))
        if not text:
            return None

        book = chapter["book"]
        chapter_number = chapter["chapter"]
        order_number = int(chapter["order"])
        safe_book = _safe_book_name(book)
        base_name = f"{order_number:02d}_{safe_book}_{chapter_number}"
        mp3_path = os.path.join(audio_dir, f"{base_name}.mp3")
        srt_path = os.path.join(audio_dir, f"{base_name}.srt")

        full_text = chapter.get("full_text") or f"{book} {chapter_number}. {text}"
        audio_bytes, srt_segments = generate_chunked_speech_parallel(
            full_text,
            **tts_kwargs,
        )

        if not audio_bytes:
            return None

        with open(mp3_path, "wb") as audio_file:
            audio_file.write(audio_bytes)

        if srt_segments:
            save_srt_file(srt_segments, srt_path)
        else:
            with open(srt_path, "w", encoding="utf-8") as srt_file:
                srt_file.write("")

        actual_ms = len(AudioSegment.from_file(mp3_path))

        return {
            "book": book,
            "chapter": chapter_number,
            "order": order_number,
            "file": chapter.get("file") or f"{base_name}.txt",
            "text_file": chapter.get("text_file") or f"{base_name}.txt",
            "audio_file": f"{base_name}.mp3",
            "srt_file": f"{base_name}.srt",
            "actual_ms": actual_ms,
            "audio_duration_sec": actual_ms / 1000.0,
            "estimated_seconds": chapter.get("estimated_seconds", 0.0),
        }

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_chapter = {
            executor.submit(_generate_one, chapter): chapter
            for chapter in chapters
        }

        completed = 0
        total = len(chapters)
        for future in as_completed(future_to_chapter):
            completed += 1
            chapter = future_to_chapter[future]
            try:
                result = future.result()
            except Exception as exc:
                if progress_callback:
                    progress_callback(f"⚠️ TTS failed for {chapter['book']} {chapter['chapter']}: {exc}")
                continue

            if result:
                results.append(result)
                if progress_callback:
                    progress_callback(f"✅ {completed}/{total} chapters generated")
            elif progress_callback:
                progress_callback(f"⚠️ Empty TTS result for {chapter['book']} {chapter['chapter']}")

    order_map = {
        (chapter["book"], chapter["chapter"]): chapter["order"]
        for chapter in chapters
    }
    results.sort(key=lambda item: order_map.get((item["book"], item["chapter"]), item["order"]))
    return results


def _cleanup_excess(pack_dir, chapter_info):
    text_dir = os.path.join(pack_dir, TEXT_SUBDIR)
    audio_dir = os.path.join(pack_dir, "audio")
    for key, base_dir in (("text_file", text_dir), ("audio_file", audio_dir), ("srt_file", audio_dir)):
        path = chapter_info.get(key)
        if not path:
            continue
        full_path = os.path.join(base_dir, path)
        if os.path.exists(full_path):
            os.remove(full_path)


def run_pipeline(
    output_base_dir=BASE_OUTPUT_DIR,
    tts_kwargs=None,
    target_seconds=TARGET_DURATION_SECONDS,
    batch_size=8,
    fetch_workers=None,
    tts_workers=4,
    progress_callback=None,
):
    """Audio-first pipeline with batch fetch, batch TTS, and actual-duration stitching."""
    pack_id = int(time.time())
    pack_dir = os.path.join(output_base_dir, f"video_pack_{pack_id}")
    text_dir = os.path.join(pack_dir, TEXT_SUBDIR)
    audio_dir = os.path.join(pack_dir, "audio")
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)

    target_seconds = max(60.0, float(target_seconds or TARGET_DURATION_SECONDS))
    batch_size = max(1, int(batch_size or 1))
    target_chapters = [
        _normalize_chapter(entry, index + 1)
        for index, entry in enumerate(get_interleaved_chapters())
    ]

    selected_chapters = []
    selected_estimated_s = 0.0
    total_actual_s = 0.0
    current_index = 0
    calibration_factor = 1.12
    reserve_seconds = max(20.0, target_seconds * 0.06)

    while total_actual_s < target_seconds and current_index < len(target_chapters):
        raw_batch = target_chapters[current_index : current_index + batch_size]
        current_index += batch_size

        if progress_callback:
            progress_callback(
                f"Batch {((current_index - 1) // batch_size) + 1}: fetch + TTS {len(raw_batch)} chapters"
            )

        fetched_texts = _fetch_batch(raw_batch, fetch_workers=fetch_workers, progress_callback=progress_callback)
        if not fetched_texts:
            continue

        prepared_batch = []
        for chapter in raw_batch:
            key = (chapter["book"], chapter["chapter"])
            text = fetched_texts.get(key)
            if not text:
                continue

            order_number = len(selected_chapters) + len(prepared_batch) + 1
            safe_book = _safe_book_name(chapter["book"])
            base_name = f"{order_number:02d}_{safe_book}_{chapter['chapter']}"
            text_filename = f"{base_name}.txt"
            text_path = os.path.join(text_dir, text_filename)
            full_text = f"{chapter['book']} {chapter['chapter']}. {text}"
            estimated_seconds = _estimate_chapter_seconds(
                full_text,
                tts_rate=tts_kwargs.get("tts_rate") if isinstance(tts_kwargs, dict) else "0%",
                calibration_factor=calibration_factor,
            )

            projected_total = selected_estimated_s + estimated_seconds
            if selected_chapters and projected_total > max(0.0, target_seconds - reserve_seconds):
                if progress_callback:
                    progress_callback(
                        f"  Skipping {chapter['book']} {chapter['chapter']} by estimate ({estimated_seconds:.1f}s would overshoot budget)"
                    )
                continue

            if not selected_chapters and estimated_seconds > target_seconds:
                if progress_callback:
                    progress_callback(
                        f"  Keeping first chapter {chapter['book']} {chapter['chapter']} even though estimate is longer than target"
                    )

            with open(text_path, "w", encoding="utf-8") as text_file:
                text_file.write(full_text)

            prepared_batch.append(
                {
                    "book": chapter["book"],
                    "chapter": chapter["chapter"],
                    "order": order_number,
                    "file": text_filename,
                    "text_file": text_filename,
                    "text_path": text_path,
                    "full_text": full_text,
                    "estimated_seconds": estimated_seconds,
                }
            )
            selected_estimated_s += estimated_seconds

        if not prepared_batch:
            continue

        tts_results = _tts_batch(
            prepared_batch,
            fetched_texts,
            audio_dir,
            tts_kwargs,
            tts_workers=tts_workers,
            progress_callback=progress_callback,
        )

        for result in tts_results:
            total_actual_s += result["actual_ms"] / 1000.0
            result["cumulative"] = total_actual_s
            selected_chapters.append(result)

            estimated_seconds = result.get("estimated_seconds") or 0.0
            if estimated_seconds > 0:
                observed_factor = max(0.5, min(2.0, (result["actual_ms"] / 1000.0) / estimated_seconds))
                calibration_factor = (calibration_factor * 0.7) + (observed_factor * 0.3)

            if progress_callback:
                minutes = int(total_actual_s // 60)
                seconds = int(total_actual_s % 60)
                progress_callback(
                    f"  + {result['book']} {result['chapter']} ({result['audio_duration_sec']:.1f}s, est {estimated_seconds:.1f}s) | total {minutes}:{seconds:02d}"
                )

    if not selected_chapters:
        raise RuntimeError("Không có chapter nào được tạo thành công.")

    if progress_callback:
        progress_callback(
            f"Pass 1 xong: {len(selected_chapters)} chapters | actual={total_actual_s:.1f}s | delta={target_seconds - total_actual_s:.1f}s"
        )

    from modules.audio_engine import stitch_video_pack

    if progress_callback:
        progress_callback("Pass 2: Stitch audio + SRT...")

    audio_path, srt_path = stitch_video_pack(
        pack_dir,
        selected_chapters,
        target_seconds=target_seconds,
        progress_callback=progress_callback,
    )

    from modules.audio_engine import get_audio_duration

    final_audio_duration = get_audio_duration(audio_path)

    metadata = {
        "pack_id": pack_id,
        "pack_dir": pack_dir,
        "target_seconds": target_seconds,
        "actual_duration": total_actual_s,
        "final_duration": final_audio_duration,
        "raw_duration": total_actual_s,
        "estimated_duration": selected_estimated_s,
        "calibration_factor": calibration_factor,
        "delta_seconds": target_seconds - total_actual_s,
        "chapters_count": len(selected_chapters),
        "chapters": selected_chapters,
        "text_dir": text_dir,
        "audio_dir": audio_dir,
        "audio_path": audio_path,
        "srt_path": srt_path,
        "pipeline_mode": "audio-first",
    }

    with open(os.path.join(pack_dir, "metadata.json"), "w", encoding="utf-8") as meta_file:
        json.dump(metadata, meta_file, indent=2, ensure_ascii=False)

    if progress_callback:
        progress_callback("Hoàn tất audio-first pipeline.")

    return metadata