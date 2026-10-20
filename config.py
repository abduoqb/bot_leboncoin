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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
# Timeouts (s) par appel : un chargement à froid du modèle peut dépasser 60 s.
OLLAMA_TIMEOUT_CLASSIFY = 30
OLLAMA_TIMEOUT_REPLY = 90
# Reprises (erreur réseau, 5xx, timeout) : tentatives TOTALES, bornées.
OLLAMA_MAX_ATTEMPTS_REPLY = 3
OLLAMA_MAX_ATTEMPTS_CLASSIFY = 2
OLLAMA_BACKOFF_BASE = 1.0   # s ; délai = base * 2^(essai-1) + jitter
OLLAMA_BACKOFF_MAX = 8.0    # s ; plafond d'un délai
# Garde le modèle en mémoire entre deux messages (évite les chargements à froid).
OLLAMA_KEEP_ALIVE = "30m"

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

# Leboncoin n'affiche qu'environ 7 conversations tant qu'on n'a pas défilé la
# liste. Le bot la fait donc défiler comme le ferait une personne : un scan sur
# N (N tiré au hasard dans cette plage), par gestes de longueur variable.
LIST_SCROLL_EVERY_MIN = 2     # Défilement de la liste au moins tous les N scans...
LIST_SCROLL_EVERY_MAX = 4     # ...et au plus tous les N scans (tirage aléatoire)
LIST_SCROLL_MAX_STEPS = 12    # Plafond de gestes de défilement par passage
LIST_SCROLL_STABLE = 3        # On s'arrête après N descentes sans nouvelle conversation

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

# Espacement minimal entre deux redémarrages AUTOMATIQUES du navigateur.
# Sans ce délai, un profil Chrome déjà occupé (ton propre Chrome, ou une autre
# instance) ferait relancer Chrome à chaque cycle : chaque échec ouvre une
# fenêtre « about:blank » de plus.
RESTART_COOLDOWN_SECONDS = 180
MAX_RESTART_ESSAIS = 3         # au-delà, on attend le cooldown

# Captchas : fenêtre glissante + pauses (évite de s'acharner et d'aggraver le blocage)
CAPTCHA_FENETRE_SECONDS = 3600     # fenêtre glissante de comptage (1 h)
CAPTCHA_MAX_PAR_HEURE = 4          # au-delà : pause longue (plus aucun scan)
CAPTCHA_PAUSE_LONGUE_SECONDS = 5400  # durée de la pause longue (1 h 30)
CAPTCHA_AUTO_MAX_PAR_HEURE = 2     # glissements AUTOMATIQUES max / fenêtre, puis attente humaine

MAX_HISTORY = 8
HEADLESS = False       # False = navigateur visible
DEBUG_SCREENSHOTS = True


