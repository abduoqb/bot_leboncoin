import logging
import asyncio
import aiohttp
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre les tentatives

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
    """Demande à Ollama de catégoriser le message (negociation, voiture, rdv, manipulation, hors_sujet, general)."""
    from config import PROMPT_ROUTER
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_ROUTER},
            {"role": "user", "content": texte}
        ],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 10
        }
    }
    
    try:
        logger.info("Appel Ollama (Classification)...")
        session = await _get_session()
        async with session.post(OLLAMA_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            data = await r.json()
            cat = data["message"]["content"].strip().lower()
            
            # Nettoyer la réponse au cas où
            for valid in ["negociation", "voiture", "rdv", "manipulation", "hors_sujet", "general"]:
                if valid in cat:
                    logger.info(f"Catégorie détectée: [{valid}]")
                    return valid
                    
            logger.warning(f"Classification inconnue '{cat}', fallback sur 'general'")
            return "general"
    except Exception as e:
        logger.error(f"Erreur classification LLM: {e}")
        return "general"



async def generate_reply(messages: list) -> str | None:
    """Envoie les messages à Ollama et retourne la réponse. Retry 3x en cas d'erreur."""
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

