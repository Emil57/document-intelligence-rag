"""App settings, in one place.

Values come from environment variables or the .env file, with defaults here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Which Claude model answers questions. Haiku is cheapest for building and testing.
# To switch, add a line like DOCINTEL_MODEL=claude-opus-5-5 to your .env file.
MODEL = os.getenv("DOCINTEL_MODEL", "claude-haiku-5-5")

# How many chunks to send to Claude with each question.
CHUNKS_PER_QUESTION = 5

# Where the web app keeps its data. This folder is in .gitignore,
# because it holds private files and accounts.
DATA_DIR = Path(os.getenv("DOCINTEL_DATA_DIR", "data"))
FILES_DIR = DATA_DIR / "files"  # copies of uploaded files, one folder per library
DB_PATH = DATA_DIR / "app.db"   # database: users, libraries, documents

# If set, people need this code to create an account. Use it whenever the app
# can be reached by others, so strangers can't sign up and spend your credits.
SIGNUP_CODE = os.getenv("DOCINTEL_SIGNUP_CODE") or None
