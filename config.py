import os
from dotenv import load_dotenv
from prompts import PROMPT_ROUTER, BASE_PERSONA, PROMPTS, REPONSES_STATIQUES, VENTE

# Noms de personnes à ne jamais voir apparaître dans une réponse (le nom du
# compte vendeur, typiquement). Optionnel : prompts.example.py n'en définit pas.
try:
    from prompts import NOMS_A_NEUTRALISER
except ImportError:
    NOMS_A_NEUTRALISER = []

# Racine du projet : rend le bot INDÉPENDANT du répertoire courant
# (profil Chrome, base SQLite et dumps sont toujours au même endroit).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── LLM ──────────────────────────────────────────────────────────────────────
# deepseek-v3.1:671b-cloud est un modèle CLOUD retiré → HTTP 410 Gone.
# gemma3:4b est installé en local et gratuit (cf. README).
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_URL = "http://localhost:11434/api/chat"

# ── ANTI-BOT & DELAYS ───────────────────────────────────────────────────────
# Cadence volontairement lente : chaque scan recharge la liste des messages.
# 45–120 s représentait ~1000 chargements/jour pour un usage humain normal,
# ce qui est un signal en soi. Le bot ne rouvre plus les conversations déjà
# réglées (cf. leboncoin.py::_candidats), la cadence peut donc rester calme.
SCAN_MIN = 75          # Secondes minimum entre deux scans
SCAN_MAX = 180         # Secondes maximum entre deux scans
NIGHT_START_HOUR = 1   # Heure de début de la pause nocturne (ex: 1 = 01:00)
NIGHT_END_HOUR = 6     # Heure de fin de la pause nocturne (ex: 7 = 07:00)
TYPING_SPEED_MIN = 40  # Ms minimum entre chaque frappe au clavier
TYPING_SPEED_MAX = 120 # Ms maximum entre chaque frappe au clavier
MAX_CONVS_PER_SCAN = 3 # Conversations inspectées/traitées max par scan (anti-bot)

# ── NAVIGATEUR / ANTI-DÉTECTION ─────────────────────────────────────────────
# "profile" = vrai Chrome avec profil persistant (RECOMMANDÉ)
#             → empreinte navigateur authentique + session conservée entre
#               les redémarrages (plus besoin de se reconnecter).
# "cdp"     = connexion à un Chrome lancé à la main avec --remote-debugging-port.
BROWSER_MODE = "profile"
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
CHROME_CHANNEL = "chrome"      # "chrome" = vrai Google Chrome ; None = Chromium
CDP_URL = "http://localhost:9222"

# Temps laissé à l'humain pour glisser un captcha DataDome s'il apparaît
CAPTCHA_WAIT_SECONDS = 300

MAX_HISTORY = 8
HEADLESS = False       # False = navigateur visible
DEBUG_SCREENSHOTS = True


