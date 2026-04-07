import os
from dotenv import load_dotenv

load_dotenv()

# API Configuration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
BIBLE_API_BASE_URL = "https://bible-api.com/"

# Video Pack Configuration
# Target: 3 hours, 33 minutes, 33 seconds
# Total seconds: (3 * 3600) + (33 * 60) + 33 = 10800 + 1980 + 33 = 12813
TARGET_DURATION_SECONDS = 12813 

# Audio Estimation
# 150 words per minute is a standard calm reading speed
WPM_ESTIMATE = 150 
WORDS_PER_SECOND = WPM_ESTIMATE / 60.0

# Output Directories
BASE_OUTPUT_DIR = "output"
TEXT_SUBDIR = "text"
AUDIO_SUBDIR = "audio"
FINAL_SUBDIR = "final"

# Bible Data Path
BIBLE_DATA_PATH = os.path.join("data", "bible_structure.json")
LOCAL_BIBLE_PATH = os.path.join("data", "kjv.json")

# Performance
MAX_WORKERS = 10  # For 10x speed boost in processing
