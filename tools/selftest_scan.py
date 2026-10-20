"""
selftest_scan.py — Vérifie qu'on n'ouvre un fil QUE s'il y a une raison.

Ouvrir une conversation recharge une page et la marque « lu » côté serveur :
si le bot tournait en boucle sur les conversations déjà traitées, c'était
autant de signaux anti-bot inutiles. À l'inverse, un fil jamais ouvert ne doit
pas être oublié — c'est l'équilibre que ce test verrouille.

Aucun navigateur, aucun réseau.
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


def liste(n, apercus=None, non_lus=(), ids=None):
    """Liste de conversations telle que la renvoie JS_LIST."""
    ids = ids or [f"conv{i}" for i in range(n)]
    return [
        {
            "href": f"/messages/id/{ids[i]}",
            "preview": (apercus or {}).get(ids[i], f"apercu {ids[i]}"),
            "unread": ids[i] in non_lus,
        }
        for i in range(n)
    ]


def ouvre(lbc, items, combien=None):
    """Simule l'ouverture des fils : enregistre l'aperçu de ceux réellement lus.

    C'est exactement ce que fait get_new_messages après chaque lecture — et
    c'est ce qui distingue « fil vérifié » (à ne plus rouvrir) de « fil coupé
    par la limite de scan » (à revoir au scan suivant).
    """
    for it in items[:combien] if combien is not None else items:
        lbc._last_previews[it["href"]] = it["preview"]


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
    print("\n" + "=" * 74)
    print("  FILTRAGE DES FILS À OUVRIR (règle anti-boucle)")
    print("=" * 74)

    # ── 1. Premier scan : on ne connaît rien, donc tout est à découvrir ──
    lbc = nouveau_client("a")
    it5 = liste(5)
    verifie("1er scan : les 5 fils sont inconnus → tous candidats",
            [f"conv{i}" for i in range(5)], ids(lbc._candidats(it5)))

    # ── 2. On ouvre les fils, ils n'ont rien à répondre → réglés ──
    ouvre(lbc, it5)
    for i in range(5):
        lbc._settled.add(f"conv{i}")
    verifie("2e scan à l'identique : plus rien à ouvrir (fin de la boucle)",
            [], ids(lbc._candidats(liste(5))))

    # ── 3. Un acheteur répond → seul son fil est candidat ──
    verifie("aperçu modifié : seul le fil concerné est ouvert",
            ["conv2"], ids(lbc._candidats(
                liste(5, apercus={"conv2": "c'est trop cher"}))))

    # ── 4. ⚠️ Non-régression : un fil coupé par la limite de scan doit
    #       rester candidat, sinon le message qu'il contient est perdu. ──
    lbc_l = nouveau_client("limite")
    it7 = liste(7)
    premier = lbc_l._candidats(it7)
    verifie("7 fils inconnus → les 7 sont candidats", 7, len(premier))
    # get_new_messages n'inspecte que 6 fils puis s'arrête : on simule
    ouvre(lbc_l, it7, combien=6)
    for i in range(6):
        lbc_l._settled.add(f"conv{i}")
    verifie("fil non inspecté (limite de scan) : reste candidat au scan suivant",
            ["conv6"], ids(lbc_l._candidats(it7)))
    # au scan suivant il est enfin lu
    ouvre(lbc_l, it7)
    lbc_l._settled.add("conv6")
    verifie("…et une fois lu, il ne l'est plus",
            [], ids(lbc_l._candidats(it7)))
    verifie("…puis plus jamais", [], ids(lbc_l._candidats(it7)))

    # ── 5. Non lu jamais inspecté ──
    lbc2 = nouveau_client("b")
    it3 = liste(3, non_lus=("conv1",))
    lbc2._candidats(it3)                       # découverte
    ouvre(lbc2, it3)
    lbc2._settled.update({"conv0", "conv2"})   # non lus, mais pas conv1
    verifie("non lu jamais inspecté : reste candidat",
            ["conv1"], ids(lbc2._candidats(it3)))
    lbc2._settled.add("conv1")
    verifie("une fois inspecté : plus ouvert (même si le badge persiste)",
            [], ids(lbc2._candidats(it3)))
    verifie("non lu + aperçu changé : réouvert",
            ["conv1"], ids(lbc2._candidats(
                liste(3, apercus={"conv1": "toujours dispo ?"},
                      non_lus=("conv1",)))))

    # ── 6. Nous sommes l'acheteur (« À propos de ce vendeur ») ──
    lbc3 = nouveau_client("c")
    it3b = liste(3, non_lus=("conv0", "conv1"))
    lbc3._ignorees.add("conv0")
    verifie("fil où nous sommes l'acheteur : jamais ouvert",
            ["conv1", "conv2"], ids(lbc3._candidats(it3b)))
    ouvre(lbc3, it3b)
    verifie("…même quand son aperçu change",
            [], ids(lbc3._candidats(
                liste(3, apercus={"conv0": "nouveau message"},
                      non_lus=("conv0",)))))

    # ── 7. Envoi raté : on retente, mais un nombre de fois borné ──
    lbc4 = nouveau_client("d")
    lbc4._last_previews["/messages/id/conv0"] = "apercu conv0"
    lbc4._pending["conv0"] = 0
    tentatives = 0
    for _ in range(10):
        if ids(lbc4._candidats(liste(1))) == ["conv0"]:
            tentatives += 1
            lbc4._noter_echec("conv0")   # l'envoi/la lecture échoue vraiment
    verifie(f"envoi raté : {L.MAX_TENTATIVES} tentatives puis abandon",
            L.MAX_TENTATIVES, tentatives)
    verifie("…et le fil n'est plus jamais rouvert",
            [], ids(lbc4._candidats(liste(1))))

    # ── 8. Le succès d'envoi libère le fil ──
    lbc5 = nouveau_client("e")
    lbc5._last_previews["/messages/id/conv0"] = "apercu conv0"
    lbc5._pending["conv0"] = 0
    lbc5._candidats(liste(1))          # tentative 1
    lbc5._pending.pop("conv0", None)   # ce que fait send_message après succès
    verifie("envoi confirmé : l'aperçu n'a pas changé → pas de réouverture",
            [], ids(lbc5._candidats(liste(1))))

    # ── 9. La mémoire survit au redémarrage ──
    # Le client « a » a ouvert les 5 fils puis vu conv2 changer d'aperçu.
    lbc6 = nouveau_client("a", garder=True)   # même fichier d'état que « a »
    verifie("état persistant rechargé : pas de parcours complet au redémarrage",
            [], ids(lbc6._candidats(liste(5))))

    print("\n" + "=" * 74)
    print(f"  Réussis : {OK[0]}   Échecs : {KO[0]}")
    print("=" * 74)
    return 1 if KO[0] else 0


if __name__ == "__main__":
    sys.exit(main())
