import requests
import re
import time
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import BIBLE_API_BASE_URL

logger = logging.getLogger("bible_video_automation.fetcher")

class RateLimitError(Exception):
    """Custom exception for 429 errors."""
    pass

def clean_text(text):
    """Cleans Bible text for TTS."""
    text = re.sub(r'^\s*[0-9]+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\n+', '\n', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# Retry logic: Exponential backoff (1s, 2s, 4s...) for 429 errors
@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
def fetch_chapter_text(book, chapter):
    """
    Fetches chapter text with retry logic for rate limits.
    """
    query = f"{book}+{chapter}"
    url = f"{BIBLE_API_BASE_URL}{query}?translation=kjv"
    logger.info("Fetching chapter %s %s from %s", book, chapter, url)
    print(f"[fetch] start {book} {chapter} -> {url}")
    
    try:
        response = requests.get(url, timeout=(5, 8))
        
        if response.status_code == 429:
            # We hit the rate limit! Raising specific error to trigger tenacity retry
            logger.warning("Rate limited on %s %s", book, chapter)
            print(f"[fetch] 429 rate limited {book} {chapter}")
            raise RateLimitError(f"Rate limit hit for {book} {chapter}")
            
        response.raise_for_status()
        data = response.json()
        raw_text = data.get('text', '')
        
        if not raw_text:
            logger.warning("Empty text for %s %s", book, chapter)
            print(f"[fetch] empty text {book} {chapter}")
            return None
            
        logger.info("Fetched chapter %s %s successfully", book, chapter)
        print(f"[fetch] done {book} {chapter} ({len(raw_text)} chars)")
        return clean_text(raw_text)
    except RateLimitError:
        raise
    except Exception as e:
        logger.exception("Error fetching %s %s", book, chapter)
        print(f"[fetch] error {book} {chapter}: {e}")
        return None
