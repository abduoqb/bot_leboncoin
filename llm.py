import logging
import asyncio
import random
import re
import aiohttp
from config import (
    OLLAMA_URL, OLLAMA_MODEL, PROMPT_ROUTER,
    OLLAMA_TIMEOUT_CLASSIFY, OLLAMA_TIMEOUT_REPLY,
    OLLAMA_MAX_ATTEMPTS_REPLY, OLLAMA_MAX_ATTEMPTS_CLASSIFY,
    OLLAMA_BACKOFF_BASE, OLLAMA_BACKOFF_MAX, OLLAMA_KEEP_ALIVE,
)

logger = logging.getLogger(__name__)

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
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_REPLY))
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


_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OUVERT_RE = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
_FIN_PHRASE_RE = re.compile(r"[.!?…]['\")»\]]*(?=\s|$)")


def _nettoyer_sortie(texte: str) -> str:
    """Retire les blocs <think>…</think> (même non refermé) et les espaces autour."""
    t = _THINK_RE.sub("", texte or "")
    t = _THINK_OUVERT_RE.sub("", t)
    return t.strip()


def _couper_derniere_phrase(texte: str) -> str:
    """Sortie tronquée (done_reason=length) : garde jusqu'à la dernière phrase complète.
    Sans ponctuation finale exploitable, renvoie le texte tel quel."""
    if _FIN_PHRASE_RE.search(texte) is None:
        return texte
    if re.search(r"[.!?…]['\")»\]]*$", texte):
        return texte  # déjà terminé proprement
    fins = list(_FIN_PHRASE_RE.finditer(texte))
    return texte[:fins[-1].end()].strip()


def _delai_backoff(essai: int) -> float:
    """Backoff exponentiel plafonné + jitter (court)."""
    base = min(OLLAMA_BACKOFF_BASE * (2 ** (essai - 1)), OLLAMA_BACKOFF_MAX)
    return base + random.uniform(0, base / 2)


async def _post_chat(payload: dict, timeout: float, max_attempts: int = 1,
                     couper_si_length: bool = False) -> str | None:
    """POST unique vers Ollama avec reprises bornées. Retourne le texte nettoyé ou None.

    Reprise : timeout, aiohttp.ClientError, HTTP >= 500, sortie vide.
    Pas de reprise : HTTP 4xx, JSON/structure invalide.
    """
    for essai in range(1, max_attempts + 1):
        raison = ""
        try:
            session = await _get_session()
            async with session.post(OLLAMA_URL, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                statut = getattr(r, "status", 200)
                if statut >= 500:
                    raison = f"HTTP {statut}"
                elif statut >= 400:
                    logger.error(f"Erreur HTTP Ollama {statut} (non retentée)")
                    return None
                else:
                    data = await r.json()
                    contenu = data["message"]["content"]
                    if not isinstance(contenu, str):
                        raise TypeError("content n'est pas une chaîne")
                    texte = _nettoyer_sortie(contenu)
                    if not texte:
                        raison = "réponse vide"
                    else:
                        if couper_si_length and data.get("done_reason") == "length":
                            avant = len(texte)
                            texte = _couper_derniere_phrase(texte)
                            logger.warning(f"Réponse tronquée (length) : {avant} -> {len(texte)} chars")
                        return texte
        except asyncio.TimeoutError:
            raison = f"timeout ({timeout}s)"
        except aiohttp.ContentTypeError as e:
            logger.error(f"Réponse Ollama non JSON: {e}")
            return None
        except aiohttp.ClientResponseError as e:
            if e.status < 500:
                logger.error(f"Erreur HTTP Ollama {e.status} (non retentée)")
                return None
            raison = f"HTTP {e.status}"
        except aiohttp.ClientError as e:
            raison = f"erreur réseau ({type(e).__name__}) — Ollama est-il lancé ?"
        except (KeyError, TypeError, ValueError, IndexError):
            logger.exception("Réponse Ollama malformée")
            return None
        except Exception:
            logger.exception("Erreur inattendue Ollama")
            return None

        if essai < max_attempts:
            delai = _delai_backoff(essai)
            logger.warning(f"Ollama : {raison} (tentative {essai}/{max_attempts}), retry dans {delai:.1f}s")
            await asyncio.sleep(delai)
        else:
            logger.error(f"Ollama : {raison} (tentative {essai}/{max_attempts}) — abandon")
    return None


async def classify_message(texte: str) -> str:
    """Demande à Ollama de catégoriser le message (negociation, produit, rdv, manipulation, hors_sujet, general)."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_ROUTER},
            {"role": "user", "content": encadrer_message_acheteur(texte)}
        ],
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.0,
            "num_predict": 16
        }
    }
    try:
        logger.info("Appel Ollama (Classification)...")
        brut = await _post_chat(payload, OLLAMA_TIMEOUT_CLASSIFY, OLLAMA_MAX_ATTEMPTS_CLASSIFY)
        if brut is None:
            return "general"
        cat = _normaliser_categorie(brut)
        logger.info(f"Catégorie détectée: [{cat}]")
        return cat
    except Exception:
        logger.exception("Erreur classification LLM, repli sur 'general'")
        return "general"


async def generate_reply(messages: list) -> str | None:
    """Envoie les messages à Ollama et retourne la réponse (None si échec)."""
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
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.4,
            "num_predict": 150
        }
    }
    logger.info("Appel Ollama (réponse)...")
    reply = await _post_chat(payload, OLLAMA_TIMEOUT_REPLY, OLLAMA_MAX_ATTEMPTS_REPLY,
                             couper_si_length=True)
    if reply is None:
        logger.error("Échec Ollama : aucune réponse exploitable")
    else:
        logger.info(f"Réponse Ollama reçue ({len(reply)} chars)")
    return reply
