"""Logique horaire de la pause nocturne (module pur, sans effet de bord).

Aucun import de config : les heures sont passées en paramètres.
"""
from datetime import datetime, timedelta


def valider_heures(debut, fin):
    """Vérifie que debut et fin sont des entiers entre 0 et 23.

    Lève ValueError sinon (les booléens et les flottants sont refusés).
    """
    for nom, valeur in (("debut", debut), ("fin", fin)):
        if isinstance(valeur, bool) or not isinstance(valeur, int):
            raise ValueError(f"Heure de {nom} invalide (entier attendu) : {valeur!r}")
        if not 0 <= valeur <= 23:
            raise ValueError(f"Heure de {nom} hors de 0-23 : {valeur}")


def en_pause_nocturne(now, debut, fin):
    """True si `now` est dans la plage de pause [debut, fin).

    Gère les plages passant minuit (ex. 23 -> 6).
    Si debut == fin, il n'y a jamais de pause.
    """
    if debut == fin:
        return False
    h = now.hour
    if debut < fin:
        return debut <= h < fin
    return h >= debut or h < fin


def prochain_reveil(now, fin):
    """Prochaine occurrence de `fin`h00 strictement après `now`."""
    reveil = now.replace(hour=fin, minute=0, second=0, microsecond=0)
    if reveil <= now:
        reveil += timedelta(days=1)
    return reveil
