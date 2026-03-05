import os
from dotenv import load_dotenv
from prompts import PROMPT_ROUTER, BASE_PERSONA, PROMPTS, REPONSES_STATIQUES

load_dotenv()

# ── LLM ──────────────────────────────────────────────────────────────────────
OLLAMA_MODEL = "deepseek-v3.1:671b-cloud"
OLLAMA_URL = "http://localhost:11434/api/chat"

# ── ANTI-BOT & DELAYS ───────────────────────────────────────────────────────
SCAN_MIN = 45          # Secondes minimum entre deux scans
SCAN_MAX = 120         # Secondes maximum entre deux scans
NIGHT_START_HOUR = 1   # Heure de début de la pause nocturne (ex: 1 = 01:00)
NIGHT_END_HOUR = 6     # Heure de fin de la pause nocturne (ex: 7 = 07:00)
TYPING_SPEED_MIN = 40  # Ms minimum entre chaque frappe au clavier
TYPING_SPEED_MAX = 120 # Ms maximum entre chaque frappe au clavier

MAX_HISTORY = 8
HEADLESS = False       # False = navigateur visible
DEBUG_SCREENSHOTS = True

