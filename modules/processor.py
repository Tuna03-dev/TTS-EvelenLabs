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

def estimate_duration(text):
    """Simple estimation of text duration (seconds)."""
    words = len(text.split())
    return words / WORDS_PER_SECOND

def generate_video_pack(output_base_dir=BASE_OUTPUT_DIR, progress_callback=None):
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
    total_duration = 0
    
    if progress_callback:
        progress_callback(f"🚀 Speed Boost Enabled: Fetching with {MAX_WORKERS} workers...")
    
    # Using ThreadPoolExecutor for 10x Speed
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_chapter = {}
        
        # We start by submitting a batch of chapters
        batch_size = MAX_WORKERS * 2
        for i in range(batch_size):
            if i < len(chapters):
                book, chapter = chapters[i]
                future = executor.submit(fetch_chapter_text, book, chapter)
                future_to_chapter[future] = (book, chapter)
        
        chapter_idx = batch_size
        
        # Parallel Execution Loop
        while future_to_chapter:
            if total_duration >= TARGET_DURATION_SECONDS:
                # Cancel remaining
                for f in future_to_chapter:
                    f.cancel()
                break
                
            for future in as_completed(future_to_chapter):
                book, chapter = future_to_chapter.pop(future)
                
                try:
                    clean_text = future.result()
                    if clean_text:
                        duration = estimate_duration(clean_text)
                        
                        # Stop if we hit duration
                        if total_duration >= TARGET_DURATION_SECONDS:
                            continue
                            
                        total_duration += duration
                        
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
                            progress_callback(f"Added {book} {chapter} | {int(total_duration // 60)}m / {int(TARGET_DURATION_SECONDS // 60)}m")
                            
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"⚠️ Error with {book} {chapter}: {e}")
                
                # Submit more if needed
                if total_duration < TARGET_DURATION_SECONDS and chapter_idx < len(chapters):
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
