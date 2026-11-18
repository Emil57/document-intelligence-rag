"""Settings, read from environment variables or the .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("DOCINTEL_MODEL", "claude-haiku-5-5")

CHUNKS_PER_QUESTION = 5

# In .gitignore: it holds private files and accounts.
DATA_DIR = Path(os.getenv("DOCINTEL_DATA_DIR", "data"))
FILES_DIR = DATA_DIR / "files"  # one folder per library
DB_PATH = DATA_DIR / "app.db"

# Daily spending limit per user, in dollars. Days start at midnight UTC.
USER_DAILY_LIMIT_USD = float(os.getenv("DOCINTEL_USER_DAILY_LIMIT_USD", "0.50"))

# Required to sign up, if set, so strangers can't create accounts and spend API credits.
SIGNUP_CODE = os.getenv("DOCINTEL_SIGNUP_CODE") or None
