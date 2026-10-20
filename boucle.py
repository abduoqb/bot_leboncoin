"""Fonctions pures de la boucle principale (sans effet de bord a l'import).

Aucun import de config : tout est passe en parametres.
"""
import inspect
import logging
import random as _random

logger = logging.getLogger("bot")

BACKOFF_PLAFOND = 15 * 60   # attente maximale apres erreurs consecutives (15 min)


def delai_backoff(n_erreurs, scan_max, rng=None, plafond=BACKOFF_PLAFOND):
    """Delai (secondes) avant de retenter apres `n_erreurs` erreurs consecutives.

    0 erreur -> 0 (pas de backoff). Sinon min(scan_max * 2**(n-1), plafond),
    auquel on ajoute un jitter de 0 a 20 % (le resultat reste <= plafond).
    """
    if n_erreurs <= 0:
        return 0.0
    rng = rng or _random
    base = max(1, scan_max) * (2 ** min(n_erreurs - 1, 30))
    base = min(base, plafond)
    return min(plafond, base * (1 + 0.2 * rng.random()))


def tirer_intervalle(scan_min, scan_max, rng=None):
    """Intervalle aleatoire entre deux scans ; tolere scan_min > scan_max."""
    rng = rng or _random
    bas, haut = min(scan_min, scan_max), max(scan_min, scan_max)
    return rng.randint(int(bas), int(haut))


async def executer_nettoyage(etapes):
    """Execute chaque etape (nom, callable) isolement ; une erreur n'arrete pas les autres.

    Un callable peut etre synchrone ou renvoyer une coroutine. Retourne la liste
    des noms d'etapes en echec.
    """
    echecs = []
    for nom, fonction in etapes:
        try:
            res = fonction()
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            echecs.append(nom)
            logger.debug(f"Nettoyage '{nom}' en echec : {e}", exc_info=True)
    return echecs
