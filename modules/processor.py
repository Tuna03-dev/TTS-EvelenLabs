import json
import random
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    BIBLE_DATA_PATH, 
    TARGET_DURATION_SECONDS, 
    WORDS_PER_SECOND, 
    BASE_OUTPUT_DIR, 
    TEXT_SUBDIR,
    MAX_WORKERS
)
from modules.fetcher import fetch_chapter_text

def load_bible_structure():
    """Loads Bible books and chapter counts."""
    with open(BIBLE_DATA_PATH, 'r') as f:
        return json.load(f)

def get_interleaved_chapters():
    """Creates a randomized but interleaved list of chapters (OT, NT, OT...)."""
    data = load_bible_structure()
    ot_list = []
    for book in data['old_testament']:
        for ch in range(1, book['chapters'] + 1):
            ot_list.append((book['name'], ch))
            
    nt_list = []
    for book in data['new_testament']:
        for ch in range(1, book['chapters'] + 1):
            nt_list.append((book['name'], ch))
            
    random.shuffle(ot_list)
    random.shuffle(nt_list)
    
    interleaved = []
    i, j = 0, 0
    while i < len(ot_list) or j < len(nt_list):
        if i < len(ot_list):
            interleaved.append(ot_list[i])
            i += 1
        if j < len(nt_list):
            interleaved.append(nt_list[j])
            j += 1
            
    return interleaved

def estimate_duration(text, words_per_second=WORDS_PER_SECOND):
    """Simple estimation of text duration (seconds)."""
    words = len(text.split())
    return words / max(0.1, float(words_per_second))

def generate_video_pack(
    output_base_dir=BASE_OUTPUT_DIR,
    progress_callback=None,
    target_seconds=TARGET_DURATION_SECONDS,
    words_per_second=WORDS_PER_SECOND,
    fetch_workers=MAX_WORKERS,
):
    """
    Orchestrates the generation using Parallel Processing.
    Support for custom output directory.
    """
    pack_id = int(time.time())
    pack_dir = os.path.join(output_base_dir, f"video_pack_{pack_id}")
    text_dir = os.path.join(pack_dir, TEXT_SUBDIR)
    os.makedirs(text_dir, exist_ok=True)
    
    chapters = get_interleaved_chapters()
    selected_chapters = []
    total_duration = 0.0
    target_seconds = max(60.0, float(target_seconds))
    safe_budget_seconds = max(60.0, target_seconds - 200.0)
    # Accept being near the safe budget without forcing bad chapter choices.
    near_target_sec = max(90.0, target_seconds * 0.0075)
    
    if progress_callback:
        progress_callback(f"🚀 Speed Boost Enabled: Fetching with {fetch_workers} workers...")
    
    # Using ThreadPoolExecutor for 10x Speed
    fetch_workers = max(1, int(fetch_workers or 1))
    with ThreadPoolExecutor(max_workers=fetch_workers) as executor:
        future_to_chapter = {}
        
        # We start by submitting a batch of chapters
        batch_size = fetch_workers * 2
        if progress_callback:
            progress_callback(f"📦 Submitting initial batch of {batch_size} fetch tasks...")
        for i in range(batch_size):
            if i < len(chapters):
                book, chapter = chapters[i]
                future = executor.submit(fetch_chapter_text, book, chapter)
                future_to_chapter[future] = (book, chapter)
        
        chapter_idx = batch_size
        
        # Parallel Execution Loop
        while future_to_chapter:
            if progress_callback:
                progress_callback(f"⏳ Waiting on {len(future_to_chapter)} fetch task(s)...")
            if total_duration >= safe_budget_seconds - near_target_sec:
                # Cancel remaining
                for f in future_to_chapter:
                    f.cancel()
                break
                
            for future in as_completed(future_to_chapter):
                book, chapter = future_to_chapter.pop(future)
                
                try:
                    clean_text = future.result()
                    if clean_text:
                        duration = estimate_duration(clean_text, words_per_second=words_per_second)
                        current_gap = abs(safe_budget_seconds - total_duration)
                        candidate_total = total_duration + duration
                        candidate_gap = abs(safe_budget_seconds - candidate_total)

                        if total_duration >= safe_budget_seconds:
                            continue

                        # Do not cut text; accept full chapter only when it improves closeness or still under target.
                        should_add = False
                        if candidate_total <= safe_budget_seconds:
                            should_add = True
                        elif candidate_gap < current_gap and candidate_gap <= max(near_target_sec, duration * 0.5):
                            should_add = True

                        if not should_add:
                            continue

                        total_duration = candidate_total
                        
                        # Prepend Chapter Header (e.g., "James 3.")
                        chapter_header = f"{book} {chapter}. "
                        full_content = chapter_header + clean_text
                        
                        # Save text file
                        order_num = len(selected_chapters) + 1
                        safe_book_name = book.replace(" ", "_").lower()
                        filename = f"{order_num:02d}_{safe_book_name}_{chapter}.txt"
                        file_path = os.path.join(text_dir, filename)
                        
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(full_content)
                        
                        selected_chapters.append({
                            "book": book, "chapter": chapter, "duration": duration, "cumulative": total_duration, "file": filename
                        })
                        
                        if progress_callback:
                            progress_callback(
                                f"✅ Fetched {book} {chapter} | est {duration:.1f}s | total {int(total_duration // 60)}m / {int(target_seconds // 60)}m"
                            )
                            
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"⚠️ Error with {book} {chapter}: {e}")
                
                # Submit more if needed
                if total_duration < safe_budget_seconds and chapter_idx < len(chapters):
                    next_book, next_ch = chapters[chapter_idx]
                    chapter_idx += 1
                    new_future = executor.submit(fetch_chapter_text, next_book, next_ch)
                    future_to_chapter[new_future] = (next_book, next_ch)
                
                # Breaking early from inner loop to re-check total_duration
                break
    
    metadata = {
        "pack_id": pack_id,
        "final_duration": total_duration,
        "chapters_count": len(selected_chapters),
        "chapters": selected_chapters
    }
    
    with open(os.path.join(pack_dir, "metadata.json"), 'w') as f:
        json.dump(metadata, f, indent=2)
        
    return metadata, pack_dir
