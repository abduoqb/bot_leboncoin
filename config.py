import os
from dotenv import load_dotenv

try:
    from prompts import PROMPT_ROUTER, BASE_PERSONA, PROMPTS, REPONSES_STATIQUES, VENTE
except ModuleNotFoundError as _e:
    if _e.name != "prompts":
        raise
    raise SystemExit(
        "prompts.py est introuvable : copiez prompts.example.py vers prompts.py "
        "(cp prompts.example.py prompts.py), puis renseignez le bloc VENTE."
    ) from None

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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud")
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

# Repos intelligent : après IDLE_SCAN_LIMIT scans vides d'affilée, pause longue.
IDLE_SCAN_LIMIT = 10      # Nombre de scans vides avant pause repos
IDLE_REST_SECONDS = 3600  # Durée de la pause repos (1h)

MAX_HISTORY = 8
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")   # DEBUG / INFO / WARNING / ERROR
HEARTBEAT_FILE = os.path.join(BASE_DIR, "heartbeat.json")   # état écrit après chaque cycle
HEADLESS = False       # False = navigateur visible
DEBUG_SCREENSHOTS = True


# ── VALIDATION ──────────────────────────────────────────────────────────────
def _est_entier(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _est_nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valider_config():
    """Vérifie la cohérence de la configuration (PAS appelée à l'import).

    Lève ValueError avec la liste de TOUS les problèmes trouvés.
    """
    from horaire import valider_heures

    g = globals()
    problemes = []

    # Cadence des scans
    smin, smax = g["SCAN_MIN"], g["SCAN_MAX"]
    for nom, v in (("SCAN_MIN", smin), ("SCAN_MAX", smax)):
        if not _est_entier(v) or v <= 0:
            problemes.append(f"{nom} doit être un entier > 0 (valeur : {v!r})")
    if _est_entier(smin) and _est_entier(smax) and smin > smax:
        problemes.append(f"SCAN_MIN ({smin}) doit être <= SCAN_MAX ({smax})")

    n = g["MAX_CONVS_PER_SCAN"]
    if not _est_entier(n) or n < 1:
        problemes.append(f"MAX_CONVS_PER_SCAN doit être un entier >= 1 (valeur : {n!r})")

    # Pause nocturne
    try:
        valider_heures(g["NIGHT_START_HOUR"], g["NIGHT_END_HOUR"])
    except ValueError as e:
        problemes.append(f"Pause nocturne : {e}")

    # Bloc VENTE
    vente = g["VENTE"]
    if not isinstance(vente, dict):
        problemes.append("VENTE doit être un dictionnaire")
    else:
        for cle in ("prix_affiche_eur", "prix_plancher_eur",
                    "prix_contre_offre_eur", "disponibilite_confirmee", "ville"):
            if cle not in vente:
                problemes.append(f"VENTE: clé manquante « {cle} »")
        prix = []
        for cle in ("prix_plancher_eur", "prix_contre_offre_eur", "prix_affiche_eur"):
            v = vente.get(cle)
            if v is None:
                continue          # None est autorisé (info inconnue)
            if not _est_nombre(v) or v < 0:
                problemes.append(f"VENTE['{cle}'] doit être un nombre >= 0 ou None (valeur : {v!r})")
            else:
                prix.append((cle, v))
        valeurs = dict(prix)
        if len(valeurs) == 3:
            if not (valeurs["prix_plancher_eur"] <= valeurs["prix_contre_offre_eur"]
                    <= valeurs["prix_affiche_eur"]):
                problemes.append(
                    "VENTE: il faut prix_plancher_eur <= prix_contre_offre_eur <= "
                    f"prix_affiche_eur ({valeurs['prix_plancher_eur']} / "
                    f"{valeurs['prix_contre_offre_eur']} / {valeurs['prix_affiche_eur']})")

    # Constantes strictement positives (OLLAMA_*, CAPTCHA_*, repos)
    positives = sorted(
        k for k, v in g.items()
        if (k.startswith(("OLLAMA_", "CAPTCHA_")) and _est_nombre(v)
            and k not in ("OLLAMA_MODEL", "OLLAMA_URL", "OLLAMA_KEEP_ALIVE"))
    ) + ["IDLE_SCAN_LIMIT", "IDLE_REST_SECONDS"]
    for k in positives:
        v = g[k]
        if not _est_nombre(v) or v <= 0:
            problemes.append(f"{k} doit être un nombre > 0 (valeur : {v!r})")
    for k in ("OLLAMA_MODEL", "OLLAMA_URL"):
        if not isinstance(g[k], str) or not g[k].strip():
            problemes.append(f"{k} ne doit pas être vide")

    if problemes:
        raise ValueError(
            "Configuration invalide :\n" + "\n".join(f"  - {p}" for p in problemes))
