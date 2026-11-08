"""App settings, in one place.

Values come from environment variables or the .env file, with defaults here.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Which Claude model answers questions. Haiku is cheapest for building and testing.
# To switch, add a line like DOCINTEL_MODEL=claude-opus-5-5 to your .env file.
MODEL = os.getenv("DOCINTEL_MODEL", "claude-haiku-5-5")

# How many chunks to send to Claude with each question.
CHUNKS_PER_QUESTION = 5
