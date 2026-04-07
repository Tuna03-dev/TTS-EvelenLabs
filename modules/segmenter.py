import re
from typing import List
import logging

# Lazy-loaded NLP models
_nlp_models = {}

def get_sentence_splitter_regex():
    """A robust regex for sentence splitting in many languages."""
    # Split by '.', '!', '?' followed by a space or end of string.
    # Handles Vietnamese punctuation as well.
    return re.compile(r'(?<=[.!?])(?:\s+|\n|$)')

def split_sentences_regex(text: str) -> List[str]:
    """Fallback splitting using regex."""
    text = text.replace("\n", " ").strip()
    regex = get_sentence_splitter_regex()
    raw_sents = regex.split(text)
    return [s.strip() for s in raw_sents if s.strip()]

def load_nlp(lang_code="en"):
    """Loads a spaCy model based on the language. (Lazy load)"""
    try:
        import spacy
    except ImportError:
        return None
        
    if lang_code in _nlp_models:
        return _nlp_models[lang_code]
    
    # Map logic
    model_map = {
        "en": "en_core_web_sm",
        "vi": "vi_core_news_sm"
    }
    target_model = model_map.get(lang_code, "en_core_web_sm")
    
    try:
        nlp = spacy.load(target_model)
        _nlp_models[lang_code] = nlp
        return nlp
    except Exception:
        # Try to load English as a bare minimum
        try:
            nlp = spacy.load("en_core_web_sm")
            _nlp_models[lang_code] = nlp
            return nlp
        except:
            return None

def split_sentences(text: str, lang: str = "en") -> List[str]:
    """Splits text into sentences using spaCy, with regex fallback."""
    text = text.strip()
    if not text:
        return []
    
    nlp = load_nlp(lang)
    if not nlp:
        return split_sentences_regex(text)
        
    try:
        doc = nlp(text)
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
    except Exception as e:
        logging.warning(f"spaCy split failed: {e}. Falling back to regex.")
        return split_sentences_regex(text)

def chunk_sentences(sentences: List[str], max_words: int = 30) -> List[str]:
    """Groups sentences into chunks that don't exceed max_words."""
    chunks = []
    current_chunk = []
    current_word_count = 0
    
    for sent in sentences:
        words = sent.split()
        if not words: continue
        
        # If adding this sentence exceeds the limit and we already have some sentences...
        if current_word_count + len(words) > max_words and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sent]
            current_word_count = len(words)
        else:
            current_chunk.append(sent)
            current_word_count += len(words)
            
    if current_chunk:
        chunks.append(" ".join(current_chunk))
        
    return chunks

def segment_text(text: str, lang: str = "en", max_words: int = 30) -> List[str]:
    """Orchestrates the splitting and chunking."""
    sents = split_sentences(text, lang)
    return chunk_sentences(sents, max_words)
