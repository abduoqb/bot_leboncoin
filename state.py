"""
state.py — Fonctions pures (sans navigateur ni état) utilisées par leboncoin.py.

Normalisation de texte, détection d'un message déjà envoyé, id de conversation.
leboncoin.py les ré-exporte (leboncoin._norm_texte, etc.).
"""

import difflib
import re
import unicodedata


def id_conv_de(href_ou_url: str) -> str:
    """Id de conversation = dernier segment du lien (« /messages/id/<id> » ou URL
    complète), sans slash final."""
    return str(href_ou_url).rstrip("/").split("/")[-1]


def _norm_texte(s) -> str:
    """Normalise un texte pour comparer ce qu'on a envoyé à ce que la page affiche :
    NFKC, espaces insécables/fines → espace, typographie (’ “ ” – …) aplanie,
    espaces repliés, minuscules."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("«", '"'), ("»", '"'), ("–", "-"), ("—", "-"),
                 ("…", "...")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def _fil_contient_envoi(data, texte: str, seuil: float = 0.92) -> bool:
    """La DERNIÈRE bulle du fil est-elle une bulle sortante portant déjà `texte` ?

    Comparaison normalisée du texte complet (contenance, ou quasi-égalité pour
    absorber de menus écarts de rendu).
    """
    if not data or not _norm_texte(texte):
        return False
    cible = _norm_texte(texte)
    bulles = data.get("messages") or []
    if bulles:
        derniere = bulles[-1]
        if derniere.get("side") != "out":
            return False
        vu = _norm_texte(derniere.get("text"))
    else:
        if data.get("lastSide") != "out":
            return False
        vu = _norm_texte(data.get("lastText"))
    if not vu:
        return False
    if cible in vu:
        return True
    return difflib.SequenceMatcher(None, cible, vu).ratio() >= seuil
