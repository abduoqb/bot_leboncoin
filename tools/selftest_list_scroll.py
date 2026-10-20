"""
selftest_list_scroll.py — Vérifie que la liste est défilée pour voir TOUTES les conversations.

Leboncoin n'affiche qu'environ 7 conversations tant qu'on n'a pas défilé. Une
fausse page reproduit ce comportement (fenêtre glissante de 7 liens, donc DOM
recyclé) et on contrôle que le bot cumule bien ce qu'il a vu.

Aucun navigateur, aucun réseau.
Usage : python -u selftest_list_scroll.py
"""

import asyncio
import os
import sys
import tempfile

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import human as H  # noqa: E402
import leboncoin as L  # noqa: E402
import logging  # noqa: E402
logging.basicConfig(level=logging.ERROR, format="%(message)s")

VISIBLES = 7


class FausseListe:
    """Fenêtre glissante de 7 conversations parmi `total`, déplacée par le scroll."""

    def __init__(self, total):
        self.total, self.debut, self.gestes = total, 0, 0

    async def evaluate(self, js):
        if js is L.JS_LIST_BOX:
            return {"x": 0, "y": 0, "w": 400, "h": 800}
        fin = min(self.debut + VISIBLES, self.total)
        return {"url": "u", "items": [
            {"href": f"/messages/id/c{i}", "preview": "", "unread": False}
            for i in range(self.debut, fin)]}

    def descendre(self, sens):
        self.gestes += 1
        self.debut = max(0, min(self.total - VISIBLES,
                                self.debut + (5 if sens == "down" else -2)))


def client(page):
    L._STATE_FILE = os.path.join(tempfile.gettempdir(), "bot_lbc_selftest_scroll.json")
    c = L.LeboncoinClient()
    c.page = page
    return c


def brancher(page):
    async def faux_scroll(_p, _box, allow_up=True):
        sens = "up" if allow_up and page.gestes % 5 == 3 else "down"
        page.descendre(sens)
        return sens
    H.human_list_scroll = faux_scroll


ok = True


def check(nom, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("✅" if cond else "❌"), nom, detail)


async def main():
    # 17 conversations, 7 visibles : sans défilement on n'en voit que 7
    page = FausseListe(17)
    brancher(page)
    data = await client(page)._load_all_conversations()
    check("17 conversations trouvées", len(data["items"]) == 17, f"({len(data['items'])})")

    # Une liste courte ne déclenche pas une boucle sans fin
    page = FausseListe(5)
    brancher(page)
    data = await client(page)._load_all_conversations()
    check("liste courte : 5 trouvées", len(data["items"]) == 5)
    check("liste courte : arrêt rapide", page.gestes <= 4, f"({page.gestes} gestes)")

    # Plafond de gestes respecté sur une liste énorme
    from config import LIST_SCROLL_MAX_STEPS
    page = FausseListe(500)
    brancher(page)
    await client(page)._load_all_conversations()
    check("plafond de gestes", page.gestes <= LIST_SCROLL_MAX_STEPS, f"({page.gestes})")

    # Arrêt dès que la cible apparaît
    page = FausseListe(40)
    brancher(page)
    data = await client(page)._load_all_conversations(cible="/messages/id/c12")
    check("cible trouvée", any(i["href"] == "/messages/id/c12" for i in data["items"]))
    check("arrêt avant la fin", len(data["items"]) < 40, f"({len(data['items'])})")

    # Cadence : 1ère lecture défile, puis un scan sur N dans la plage configurée
    from config import LIST_SCROLL_EVERY_MIN, LIST_SCROLL_EVERY_MAX
    c = client(FausseListe(10))
    scans = [c._defilement_du_scan() for _ in range(60)]
    check("1er scan défile", scans[0])
    idx = [i for i, s in enumerate(scans) if s]
    ecarts = {b - a for a, b in zip(idx, idx[1:])}
    check("écarts dans la plage", ecarts and
          all(LIST_SCROLL_EVERY_MIN <= e <= LIST_SCROLL_EVERY_MAX for e in ecarts), f"({sorted(ecarts)})")

    # Le geste humain : placement souris + sens valide (page factice)
    class Mouse:
        async def wheel(self, x, y): pass
        async def move(self, x, y): pass

    class P:
        mouse = Mouse()
        async def evaluate(self, *a, **k): return {"w": 1600, "h": 900}

    import importlib
    importlib.reload(H)
    async def pas_de_pause(_s): pass
    H.asyncio.sleep = pas_de_pause
    sens = {await H.human_list_scroll(P(), {"x": 0, "y": 0, "w": 400, "h": 800}) for _ in range(60)}
    check("gestes down et up produits", sens == {"down", "up"}, f"({sens})")
    premier = {await H.human_list_scroll(P(), {"x": 0, "y": 0, "w": 400, "h": 800}, allow_up=False)
               for _ in range(30)}
    check("allow_up=False : jamais up", premier == {"down"})

    print("\nRÉSULTAT :", "OK" if ok else "ÉCHEC")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
