import logging
import asyncio
import re
import aiohttp
from config import OLLAMA_URL, OLLAMA_MODEL, PROMPT_ROUTER

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre les tentatives

# Catégories du routeur : définies UNE fois ici (importées ailleurs).
CATEGORIES = ("negociation", "produit", "rdv", "manipulation", "hors_sujet", "general")

# Le texte acheteur est une donnée non fiable : tronqué et encadré par des balises.
MAX_TEXTE_ACHETEUR = 500
_BALISE_RE = re.compile(r"<\s*/?\s*message_acheteur\s*>", re.IGNORECASE)


def encadrer_message_acheteur(texte: str) -> str:
    """Tronque le texte acheteur, neutralise ses balises, l'encadre une seule fois."""
    t = _BALISE_RE.sub(" ", texte or "").strip()
    if len(t) > MAX_TEXTE_ACHETEUR:
        t = t[:MAX_TEXTE_ACHETEUR].rstrip() + "…"
    return f"<message_acheteur>{t}</message_acheteur>"


def _normaliser_categorie(brut: str) -> str:
    """Premier mot du modèle == catégorie exacte ; sous-chaîne en repli (loguée)."""
    mots = re.findall(r"[a-z_]+", (brut or "").lower())
    if mots and mots[0] in CATEGORIES:
        return mots[0]
    for valid in CATEGORIES:
        if valid in (brut or "").lower():
            logger.warning(f"Classification approximative '{brut[:40]}' -> [{valid}] (repli sous-chaîne)")
            return valid
    logger.warning(f"Classification inconnue '{(brut or '')[:40]}', fallback sur 'general'")
    return "general"


# ── Session HTTP partagée (Bug 4) ────────────────────────
_session: aiohttp.ClientSession | None = None


async def init_session():
    """Crée la session HTTP partagée (à appeler au démarrage)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        logger.debug("Session aiohttp créée")


async def close_session():
    """Ferme la session HTTP proprement (à appeler au shutdown)."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
        logger.debug("Session aiohttp fermée")


async def _get_session() -> aiohttp.ClientSession:
    """Retourne la session, la crée si nécessaire."""
    if _session is None or _session.closed:
        await init_session()
    return _session


async def classify_message(texte: str) -> str:
    """Demande à Ollama de catégoriser le message (negociation, produit, rdv, manipulation, hors_sujet, general)."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_ROUTER},
            {"role": "user", "content": encadrer_message_acheteur(texte)}
        ],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 16
        }
    }
    
    try:
        logger.info("Appel Ollama (Classification)...")
        session = await _get_session()
        async with session.post(OLLAMA_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            data = await r.json()
            cat = _normaliser_categorie(data["message"]["content"])
            logger.info(f"Catégorie détectée: [{cat}]")
            return cat
    except Exception as e:
        logger.error(f"Erreur classification LLM: {e}")
        return "general"



async def generate_reply(messages: list) -> str | None:
    """Envoie les messages à Ollama et retourne la réponse. Retry 3x en cas d'erreur."""
    # Les messages acheteur (role user) sont tronqués et balisés ; copie, sans muter l'appelant.
    messages = [
        {**m, "content": encadrer_message_acheteur(m.get("content", ""))}
        if m.get("role") == "user" else m
        for m in messages
    ]
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 150
        }
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Appel Ollama (tentative {attempt}/{MAX_RETRIES})...")
            session = await _get_session()
            async with session.post(OLLAMA_URL, json=payload) as r:
                r.raise_for_status()
                data = await r.json()
                reply = data["message"]["content"].strip()
                logger.info(f"Réponse Ollama reçue ({len(reply)} chars)")
                return reply
        except asyncio.TimeoutError:
            logger.warning(f"Timeout Ollama (tentative {attempt}/{MAX_RETRIES})")
        except aiohttp.ClientConnectorError:
            logger.error(f"Ollama injoignable (tentative {attempt}/{MAX_RETRIES}) — est-il lancé ?")
        except aiohttp.ClientResponseError as e:
            logger.error(f"Erreur HTTP Ollama: {e}")
            return None  # pas de retry sur erreur HTTP
        except (KeyError, ValueError) as e:
            logger.error(f"Réponse Ollama malformée: {e}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue Ollama: {e}")
            return None

        if attempt < MAX_RETRIES:
            logger.info(f"Retry dans {RETRY_DELAY}s...")
            await asyncio.sleep(RETRY_DELAY)

    logger.error("Échec Ollama après toutes les tentatives")
    return None

