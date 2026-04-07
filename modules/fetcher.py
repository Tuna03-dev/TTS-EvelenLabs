import requests
import re
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import BIBLE_API_BASE_URL

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
    
    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code == 429:
            # We hit the rate limit! Raising specific error to trigger tenacity retry
            raise RateLimitError(f"Rate limit hit for {book} {chapter}")
            
        response.raise_for_status()
        data = response.json()
        raw_text = data.get('text', '')
        
        if not raw_text:
            return None
            
        return clean_text(raw_text)
    except RateLimitError:
        raise
    except Exception as e:
        print(f"Error fetching {book} {chapter}: {e}")
        return None
