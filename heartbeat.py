"""Heartbeat : petit JSON écrit de façon atomique après chaque cycle."""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("bot")


def ecrire_heartbeat(chemin, stats, etat):
    """Écrit {timestamp, scans, messages_traites, erreurs, etat} (tmp + replace).

    Les erreurs d'écriture sont loggées en debug et ignorées. Retourne True/False.
    """
    tmp = f"{chemin}.tmp"
    try:
        data = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "scans": stats.get("scans", 0),
            "messages_traites": stats.get("messages_traites", 0),
            "erreurs": stats.get("erreurs", 0),
            "etat": etat,
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, chemin)
        return True
    except Exception as e:
        logger.debug(f"Heartbeat non écrit : {e}")
        return False
