"""Configuration du logging (appelée explicitement, jamais à l'import)."""
import logging
import logging.handlers
import os
import sys

from config import BASE_DIR

FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
DATEFMT = "%H:%M:%S"
_MARQUEUR = "_bot_handler"


def niveau_valide(valeur, defaut=logging.INFO):
    """Convertit un nom de niveau en entier ; repli sur `defaut` si invalide."""
    if isinstance(valeur, str):
        n = logging.getLevelName(valeur.strip().upper())
        if isinstance(n, int):
            return n
    return defaut


def setup_logging(level=None, log_path=None):
    """Configure le logger racine (console + fichier à rotation 5 Mo x 3).

    Idempotent : un second appel ne duplique pas les handlers.
    """
    if level is None:
        from config import LOG_LEVEL
        level = LOG_LEVEL
    niveau = niveau_valide(level)

    # Windows : console en cp1252 → un emoji dans un log ferait planter l'écriture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    racine = logging.getLogger()
    racine.setLevel(niveau)
    if any(getattr(h, _MARQUEUR, False) for h in racine.handlers):
        for h in racine.handlers:
            if getattr(h, _MARQUEUR, False):
                h.setLevel(niveau)
        return niveau

    fmt = logging.Formatter(FORMAT, DATEFMT)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_path or os.path.join(BASE_DIR, "bot.log"), encoding="utf-8",
            maxBytes=5_000_000, backupCount=3),
    ]
    for h in handlers:
        h.setFormatter(fmt)
        h.setLevel(niveau)
        setattr(h, _MARQUEUR, True)
        racine.addHandler(h)
    return niveau
