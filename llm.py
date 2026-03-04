import time
import logging
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre les tentatives


def generate_reply(messages: list) -> str | None:
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
            r = requests.post(OLLAMA_URL, json=payload, timeout=60)
            r.raise_for_status()
            reply = r.json()["message"]["content"].strip()
            logger.info(f"Réponse Ollama reçue ({len(reply)} chars)")
            return reply
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout Ollama (tentative {attempt}/{MAX_RETRIES})")
        except requests.exceptions.ConnectionError:
            logger.error(f"Ollama injoignable (tentative {attempt}/{MAX_RETRIES}) — est-il lancé ?")
        except requests.exceptions.HTTPError as e:
            logger.error(f"Erreur HTTP Ollama: {e}")
            return None  # pas de retry sur erreur HTTP (400, 500, etc.)
        except (KeyError, ValueError) as e:
            logger.error(f"Réponse Ollama malformée: {e}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue Ollama: {e}")
            return None

        if attempt < MAX_RETRIES:
            logger.info(f"Retry dans {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    logger.error("Échec Ollama après toutes les tentatives")
    return None
