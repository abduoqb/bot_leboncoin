"""Tests de human.py avec une fausse page / souris / clavier (aucun navigateur)."""
import asyncio

import pytest

import human as H


class FakeMouse:
    def __init__(self):
        self.moves = []

    async def move(self, x, y):
        self.moves.append((x, y))

    async def wheel(self, x, y):
        pass


class FakeKeyboard:
    def __init__(self):
        self.typed = []
        self.pressed = []

    async def type(self, text, delay=0):
        self.typed.append(text)

    async def press(self, key):
        self.pressed.append(key)


class FakePage:
    def __init__(self, w=800, h=600):
        self.vp = {"w": w, "h": h}
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()

    async def evaluate(self, *a, **k):
        return self.vp

    async def wait_for_load_state(self, *a, **k):
        pass


class FakeElement:
    async def click(self, **k):
        pass


@pytest.fixture(autouse=True)
def rapide(monkeypatch):
    """Pas d'attente réelle ; on enregistre les durées demandées."""
    delays = []

    async def fake_sleep(s):
        delays.append(s)

    monkeypatch.setattr(H.asyncio, "sleep", fake_sleep)
    H.reset_mouse()
    H.seed(1234)
    return delays


def run(coro):
    return asyncio.run(coro)


def test_mouvement_borne_au_viewport():
    page = FakePage(800, 600)
    run(H.human_move(page, 5000, -300))
    assert page.mouse.moves
    for x, y in page.mouse.moves:
        assert 0 <= x <= 799 and 0 <= y <= 599


def test_position_par_page():
    a, b = FakePage(), FakePage()
    run(H.human_move(a, 100, 100))
    run(H.human_move(b, 700, 500))
    assert H._get_pos(a) != H._get_pos(b)
    assert abs(H._get_pos(a)[0] - 100) < 25
    # b est parti du centre de SA fenêtre, pas de la position de a
    assert b.mouse.moves[0][1] > 250
    H.reset_mouse(a)
    assert H._get_pos(a) is None and H._get_pos(b) is not None
    H.reset_mouse()
    assert H._get_pos(b) is None


def test_position_mise_a_jour_a_chaque_move():
    page = FakePage()
    run(H.human_move(page, 400, 300))
    assert H._get_pos(page) == page.mouse.moves[-1]


def test_easing_monotone_et_bornes():
    vals = [H._ease(i / 100) for i in range(101)]
    assert vals[0] == 0 and vals[-1] == 1
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_voisins_azerty():
    n = H.AZERTY_NEIGHBORS
    assert {"z", "q"} <= set(n["a"])
    assert {"z", "r", "s", "d"} <= set(n["e"])
    assert "k" not in n["a"] and "a" not in n["a"]
    assert all(c not in n[c] for c in n)
    assert all(c in n[o] for c in n for o in n[c])  # symétrique


def test_human_type_n_envoie_jamais_entree(monkeypatch):
    monkeypatch.setattr(H, "TYPO_RATE", 1.0)  # fautes max : doit rester sans Entrée
    page = FakePage()
    run(H.human_type(page, FakeElement(), "Bonjour\nmonsieur\r\nmerci"))
    assert "Enter" not in page.keyboard.pressed
    assert all("\n" not in t and "\r" not in t for t in page.keyboard.typed)
    assert set(page.keyboard.pressed) == {"Backspace"}
    # chaque faute est suivie d'un Backspace ; les \n sont devenus des espaces
    assert len(page.keyboard.typed) == len("Bonjour monsieur merci") + len(page.keyboard.pressed)


def test_wait_page_load_pause_zero(rapide):
    run(H.wait_page_load(FakePage(), extra_pause=0))
    assert rapide == [0]


def test_reading_pause_extra_zero(rapide):
    run(H.reading_pause("un texte assez long pour durer", extra_pause=0))
    assert rapide == [0]
    rapide.clear()
    run(H.reading_pause("un texte"))
    assert 0.8 <= rapide[0] <= 8.0


def test_graine_reproductible():
    H.seed(7)
    a = [H._bezier_points(0, 0, 300, 100, 10)]
    H.seed(7)
    b = [H._bezier_points(0, 0, 300, 100, 10)]
    assert a == b
