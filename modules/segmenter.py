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


def _split_by_connectors(sentence: str, lang: str = "en") -> List[str]:
    """Attempts semantic-like splitting at major connectors before hard wrapping."""
    connector_patterns = {
        "en": r"\b(?:and|but|because|so|therefore|however|though|although|while|when|if|that|which|who)\b",
        "vi": r"\b(?:va|nhung|boi vi|vi vay|tuy nhien|mac du|khi|neu|rang)\b",
    }
    pattern = connector_patterns.get(lang, connector_patterns["en"])

    tokens = re.split(f"({pattern})", sentence, flags=re.IGNORECASE)
    if len(tokens) <= 1:
        return [sentence.strip()] if sentence.strip() else []

    parts = []
    current = ""
    for token in tokens:
        token = (token or "").strip()
        if not token:
            continue

        if re.fullmatch(pattern, token, flags=re.IGNORECASE) and current:
            parts.append(current.strip())
            current = token
        else:
            current = f"{current} {token}".strip() if current else token

    if current:
        parts.append(current.strip())

    cleaned = []
    for p in parts:
        text = re.sub(r"\s+", " ", p).strip()
        # Safety net: remove accidental duplicate leading connector (e.g., "Because Because ...").
        text = re.sub(r"^(\b\w+\b)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
        if text:
            cleaned.append(text)

    return cleaned


def _split_long_sentence(sentence: str, max_words: int, max_chars: int, lang: str = "en") -> List[str]:
    """Breaks a long sentence into smaller readable parts."""
    sentence = re.sub(r"\s+", " ", sentence or "").strip()
    if not sentence:
        return []

    words = sentence.split()
    if len(words) <= max_words and len(sentence) <= max_chars:
        return [sentence]

    # First pass: split by punctuation that usually maps to short pauses.
    punct_parts = [p.strip() for p in re.split(r"(?<=[,;:])\s+", sentence) if p.strip()]
    if len(punct_parts) == 1:
        punct_parts = [sentence]

    # Second pass: split by connectors to keep phrase-level meaning.
    semantic_parts = []
    for part in punct_parts:
        semantic_parts.extend(_split_by_connectors(part, lang=lang))

    if not semantic_parts:
        semantic_parts = [sentence]

    parts = []
    for semantic in semantic_parts:
        semantic_words = semantic.split()
        if not semantic_words:
            continue

        if len(semantic_words) <= max_words and len(semantic) <= max_chars:
            parts.append(semantic)
            continue

        current = []
        for word in semantic_words:
            candidate = " ".join(current + [word]).strip()
            if current and (len(current) >= max_words or len(candidate) > max_chars):
                parts.append(" ".join(current).strip())
                current = [word]
            else:
                current.append(word)

        if current:
            parts.append(" ".join(current).strip())

    return [part for part in parts if part]

def chunk_sentences(sentences: List[str], max_words: int = 30, max_chars: int = 140, lang: str = "en") -> List[str]:
    """Groups sentences into chunks that don't exceed max_words or max_chars."""
    chunks = []
    current_chunk = []
    current_word_count = 0
    current_char_count = 0
    
    for sent in sentences:
        sentence_parts = _split_long_sentence(sent, max_words=max_words, max_chars=max_chars, lang=lang)
        for part in sentence_parts:
            words = part.split()
            if not words:
                continue

            next_word_count = current_word_count + len(words)
            next_char_count = current_char_count + len(part) + (1 if current_chunk else 0)

            if current_chunk and (next_word_count > max_words or next_char_count > max_chars):
                chunks.append(" ".join(current_chunk))
                current_chunk = [part]
                current_word_count = len(words)
                current_char_count = len(part)
            else:
                current_chunk.append(part)
                current_word_count = next_word_count
                current_char_count = next_char_count
            
    if current_chunk:
        chunks.append(" ".join(current_chunk))
        
    return chunks


def chunk_by_sentences(sentences: List[str], max_chars: int = 300) -> List[str]:
    """Groups complete sentences together without splitting inside a sentence."""
    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        sent = (sent or "").strip()
        if not sent:
            continue

        if current and current_len + len(sent) + 1 > max_chars:
            chunks.append(" ".join(current).strip())
            current = [sent]
            current_len = len(sent)
        else:
            current.append(sent)
            current_len += len(sent) + 1

    if current:
        chunks.append(" ".join(current).strip())

    return [c for c in chunks if c]

def segment_text(
    text: str,
    lang: str = "en",
    max_words: int = 30,
    max_chars: int = 140,
    sentence_mode: bool = False,
) -> List[str]:
    """Orchestrates the splitting and chunking."""
    sents = split_sentences(text, lang)
    if sentence_mode:
        return chunk_by_sentences(sents, max_chars=300)

    chunks = []
    for sent in sents:
        chunks.extend(_split_long_sentence(sent, max_words=max_words, max_chars=max_chars, lang=lang))
    return chunk_sentences(chunks, max_words=max_words, max_chars=max_chars, lang=lang)
