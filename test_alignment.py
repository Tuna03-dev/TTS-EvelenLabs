#!/usr/bin/env python
"""Quick test for create_srt_from_alignment"""

from modules.voice_gen import create_srt_from_alignment, save_srt_file

# Test 1: Short text
print("=" * 60)
print("Test 1: Short text (should be 1 segment)")
print("=" * 60)
alignment = {
    'words': [
        {'text': 'Hello', 'start': 0.0, 'end': 0.5},
        {'text': 'world', 'start': 0.5, 'end': 1.0},
        {'text': 'this', 'start': 1.0, 'end': 1.5},
        {'text': 'is', 'start': 1.5, 'end': 2.0},
        {'text': 'a', 'start': 2.0, 'end': 2.2},
        {'text': 'test.', 'start': 2.2, 'end': 2.8},
    ]
}

segments = create_srt_from_alignment(alignment)
print(f"✅ Generated {len(segments)} segments:")
for i, seg in enumerate(segments, 1):
    print(f"  {i}. [{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")

# Test 2: Longer text with punctuation
print("\n" + "=" * 60)
print("Test 2: Longer text (should split at punctuation)")
print("=" * 60)
alignment2 = {
    'words': [
        {'text': 'The', 'start': 0.0, 'end': 0.2},
        {'text': 'quick', 'start': 0.2, 'end': 0.4},
        {'text': 'brown', 'start': 0.4, 'end': 0.6},
        {'text': 'fox', 'start': 0.6, 'end': 0.8},
        {'text': 'jumps', 'start': 0.8, 'end': 1.0},
        {'text': 'over.', 'start': 1.0, 'end': 1.3},  # Punctuation
        {'text': 'The', 'start': 1.3, 'end': 1.5},
        {'text': 'lazy', 'start': 1.5, 'end': 1.7},
        {'text': 'dog', 'start': 1.7, 'end': 1.9},
        {'text': 'sleeps', 'start': 1.9, 'end': 2.2},
        {'text': 'quietly', 'start': 2.2, 'end': 2.5},
        {'text': 'now.', 'start': 2.5, 'end': 2.8},
    ]
}

segments2 = create_srt_from_alignment(alignment2)
print(f"✅ Generated {len(segments2)} segments:")
for i, seg in enumerate(segments2, 1):
    print(f"  {i}. [{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}")

print("\n✅ All tests completed successfully!")

