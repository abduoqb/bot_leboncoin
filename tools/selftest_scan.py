"""
selftest_scan.py — Vérifie qu'on ne rouvre JAMAIS un fil déjà réglé.

Ouvrir une conversation recharge une page et la marque « lu » côté serveur.
Si le bot tournait en boucle sur les conversations déjà traitées, c'était
autant de signaux anti-bot inutiles. Ce test verrouille le comportement
attendu, sans navigateur ni réseau.

Usage : python -u selftest_scan.py
"""

import os
import sys
import tempfile

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import leboncoin as L  # noqa: E402


def nouveau_client(tag="a", garder=False):
    """Client isolé : chaque test a son propre fichier d'état persistant."""
    p = os.path.join(tempfile.gettempdir(), f"bot_lbc_selftest_{tag}.json")
    if not garder and os.path.exists(p):
        os.remove(p)
    L._STATE_FILE = p
    return L.LeboncoinClient()


import logging  # noqa: E402
logging.basicConfig(level=logging.ERROR, format="%(message)s")


def liste(n, apercus=None, non_lus=()):
    """Liste de conversations telle que la renvoie JS_LIST."""
    return [
        {
            "href": f"/messages/id/conv{i}",
            "preview": (apercus or {}).get(i, f"apercu {i}"),
            "unread": i in non_lus,
        }
        for i in range(n)
    ]


def ids(candidats):
    return sorted(c["href"].rsplit("/", 1)[-1] for c in candidats)


OK = [0]
KO = [0]


def verifie(titre, attendu, obtenu):
    bon = attendu == obtenu
    (OK if bon else KO)[0] += 1
    print(f"  {'✅' if bon else '❌'} {titre}")
    if not bon:
        print(f"     attendu : {attendu}")
        print(f"     obtenu  : {obtenu}")


def main():
    lbc = nouveau_client("a")

    print("\n" + "=" * 74)
    print("  FILTRAGE DES FILS À OUVRIR (règle anti-boucle)")
    print("=" * 74)

    # ── 1. Premier scan : on ne connaît rien, donc tout est à découvrir ──
    it = liste(5)
    verifie("1er scan : les 5 fils sont inconnus → tous candidats",
            ["conv0", "conv1", "conv2", "conv3", "conv4"], ids(lbc._candidats(it)))

    # ── 2. On simule l'inspection : « dernier message = nous » → réglés ──
    for i in range(5):
        lbc._settled.add(f"conv{i}")
    verifie("2e scan à l'identique : plus rien à ouvrir (fin de la boucle)",
            [], ids(lbc._candidats(liste(5))))

    # ── 3. Un acheteur répond → seul son fil est candidat ──
    verifie("aperçu modifié : seul le fil concerné est ouvert",
            ["conv2"], ids(lbc._candidats(liste(5, apercus={2: "c'est trop cher"}))))

    # ── 4. Nouveau message non lu jamais inspecté ──
    lbc2 = nouveau_client("b")
    verifie("1er scan : découverte des 3 fils",
            ["conv0", "conv1", "conv2"], ids(lbc2._candidats(liste(3, non_lus={1}))))
    # Le bot n'a eu le temps d'inspecter que conv0 et conv2 (quota par scan)
    lbc2._settled.update({"conv0", "conv2"})
    verifie("non lu jamais inspecté : reste candidat au scan suivant",
            ["conv1"], ids(lbc2._candidats(liste(3, non_lus={1}))))
    lbc2._settled.add("conv1")
    verifie("une fois inspecté : plus ouvert (même si le badge persiste)",
            [], ids(lbc2._candidats(liste(3, non_lus={1}))))
    # …sauf si un message arrive (l'aperçu change)
    verifie("non lu + aperçu changé : réouvert",
            ["conv1"], ids(lbc2._candidats(
                liste(3, apercus={1: "toujours dispo ?"}, non_lus={1}))))

    # ── 5. Nous sommes l'acheteur (« À propos de ce vendeur ») ──
    lbc3 = nouveau_client("c")
    lbc3._ignorees.add("conv0")
    verifie("fil où nous sommes l'acheteur : jamais rouvert",
            ["conv1", "conv2"], ids(lbc3._candidats(liste(3, non_lus={0, 1}))))
    verifie("…même quand son aperçu change",
            [], ids(lbc3._candidats(
                liste(3, apercus={0: "nouveau message"}, non_lus={0}))))

    # ── 6. Envoi raté : on retente, mais un nombre de fois borné ──
    lbc4 = nouveau_client("d")
    lbc4._last_previews["/messages/id/conv0"] = "apercu 0"
    lbc4._pending["conv0"] = 0
    tentatives = 0
    for _ in range(10):
        if ids(lbc4._candidats(liste(1))) == ["conv0"]:
            tentatives += 1
    verifie(f"envoi raté : {L.MAX_TENTATIVES} tentatives puis abandon",
            L.MAX_TENTATIVES, tentatives)
    verifie("…et le fil n'est plus jamais rouvert",
            [], ids(lbc4._candidats(liste(1))))

    # ── 7. Le succès d'envoi libère le fil ──
    lbc5 = nouveau_client("e")
    lbc5._last_previews["/messages/id/conv0"] = "apercu 0"
    lbc5._pending["conv0"] = 0
    lbc5._candidats(liste(1))          # tentative 1
    lbc5._pending.pop("conv0", None)   # ce que fait send_message après succès
    verifie("envoi confirmé : l'aperçu n'a pas changé → pas de réouverture",
            [], ids(lbc5._candidats(liste(1))))

    # ── 8. La mémoire survit au redémarrage ──
    # Le client « a » a mémorisé « c'est trop cher » pour conv2 (test 3).
    # Après redémarrage, seul cet aperçu diffère → un seul fil à rouvrir,
    # au lieu des 5 d'un parcours complet.
    lbc6 = nouveau_client("a", garder=True)   # même fichier d'état que « a »
    verifie("état persistant rechargé : pas de parcours complet au redémarrage",
            ["conv2"], ids(lbc6._candidats(liste(5))))

    print("\n" + "=" * 74)
    print(f"  Réussis : {OK[0]}   Échecs : {KO[0]}")
    print("=" * 74)
    return 1 if KO[0] else 0


if __name__ == "__main__":
    sys.exit(main())
