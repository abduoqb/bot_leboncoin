"""Tests de la gestion des captchas (sans navigateur : fausses pages écrites à la main)."""

import asyncio

import pytest

import captcha
import leboncoin
from leboncoin import LeboncoinClient


def run(coro):
    return asyncio.run(coro)


# ── Faux objets ───────────────────────────────────────────────────────────────

class FakePage:
    def __init__(self, captcha_visible=True, url="https://www.leboncoin.fr/messages"):
        self.captcha_visible = captcha_visible
        self.url = url
        self.js_list = {"items": []}
        self.context = None

    async def query_selector(self, sel):
        return None

    async def evaluate(self, script, *a):
        if script is leboncoin.JS_LIST:
            return self.js_list
        # is_captcha_present / _state
        if "url" in script and "login" in script:
            return {"url": self.url, "captcha": self.captcha_visible, "login": False}
        return self.captcha_visible


def make_client(page=None):
    c = LeboncoinClient.__new__(LeboncoinClient)   # sans __init__ (pas de fichier d'état)
    c.page = page or FakePage()
    c._shutdown = False
    c._pending = {}
    c._ignorees = set()
    c._settled = set()
    c._last_previews = {}
    c._current_thread_url = None
    c._init_captcha_state()
    c._save_state = lambda: None
    return c


@pytest.fixture(autouse=True)
def seuils(monkeypatch):
    monkeypatch.setattr(leboncoin, "CAPTCHA_FENETRE_SECONDS", 3600)
    monkeypatch.setattr(leboncoin, "CAPTCHA_MAX_PAR_HEURE", 3)
    monkeypatch.setattr(leboncoin, "CAPTCHA_PAUSE_LONGUE_SECONDS", 1000)
    monkeypatch.setattr(leboncoin, "CAPTCHA_AUTO_MAX_PAR_HEURE", 2)
    monkeypatch.setattr(leboncoin, "HEADLESS", False)


# ── Fenêtre glissante / pause longue ──────────────────────────────────────────

def test_fenetre_glissante_oublie_les_anciens():
    c = make_client()
    assert not c._noter_captcha(now=0)
    assert not c._noter_captcha(now=10)
    assert not c._noter_captcha(now=20)
    # 4e captcha mais tous les précédents sont sortis de la fenêtre d'1 h : pas de dépassement
    assert not c._noter_captcha(now=3650)
    assert len(c._captcha_times) == 1


def test_depassement_arme_la_pause_longue_puis_elle_expire():
    c = make_client()
    for t in (0, 1, 2):
        assert not c._noter_captcha(now=t)
    assert c._noter_captcha(now=3)             # 4 > seuil 3
    assert c._en_pause_captcha(now=500)
    assert not c._en_pause_captcha(now=1003)   # fin de pause : compteurs remis à zéro
    assert c._captcha_times == []


def test_get_new_messages_en_pause_retourne_vide_sans_naviguer():
    c = make_client()
    c._pause_captcha_jusqu = 10**12

    async def boom():
        raise AssertionError("ne doit pas naviguer pendant la pause")
    c._ensure_on_messages = boom
    assert run(c.get_new_messages()) == []


def test_limite_des_resolutions_automatiques(monkeypatch):
    monkeypatch.setattr(leboncoin, "CAPTCHA_MAX_PAR_HEURE", 99)
    monkeypatch.setattr(leboncoin, "HEADLESS", True)   # attente humaine => retour immédiat
    appels = []

    async def faux_solve(page, *a, **k):
        appels.append(1)
        return False
    monkeypatch.setattr(captcha, "solve_slider", faux_solve)

    c = make_client(FakePage(captcha_visible=True))
    for _ in range(4):
        assert run(c._wait_human("Captcha DataDome détecté")) is False
    assert len(appels) == 2     # CAPTCHA_AUTO_MAX_PAR_HEURE


# ── Headless ──────────────────────────────────────────────────────────────────

def test_headless_ne_attend_pas_un_humain(monkeypatch):
    monkeypatch.setattr(leboncoin, "HEADLESS", True)
    monkeypatch.setattr(leboncoin, "CAPTCHA_WAIT_SECONDS", 10**6)

    async def pas_de_sommeil(*a, **k):
        raise AssertionError("aucune attente en headless")
    monkeypatch.setattr(leboncoin.asyncio, "sleep", pas_de_sommeil)

    c = make_client(FakePage(captcha_visible=False))
    assert run(c._wait_human("Connexion à Leboncoin requise")) is False
    assert c._scan_bloque is True


# ── Propagation vers get_new_messages ─────────────────────────────────────────

def _client_scan(nb_fils=3):
    c = make_client()
    items = [{"href": f"/messages/id/c{i}", "preview": f"p{i}", "unread": True}
             for i in range(nb_fils)]
    c.page.js_list = {"items": items}

    async def ok():
        return True
    c._ensure_on_messages = ok
    c._defilement_du_scan = lambda: False
    c._candidats = lambda its: list(its)
    c._set_preview = lambda href, prev: c._last_previews.__setitem__(href, prev)
    return c


def test_captcha_non_resolu_arrete_le_scan_sans_compter_d_echec():
    c = _client_scan(3)
    lus = []

    async def lire(url):
        lus.append(url)
        c._scan_bloque = True          # ce que fait _wait_human en cas d'échec
        return None
    c._read_thread = lire

    assert run(c.get_new_messages()) == []
    assert len(lus) == 1               # les fils suivants ne sont pas ouverts
    assert c._pending == {}            # pas un échec du fil
    assert c._last_previews == {}      # l'aperçu n'est pas marqué « vu »


def test_captcha_resolu_dans_un_fil_n_est_pas_un_echec():
    c = _client_scan(2)
    lus = []

    async def lire(url):
        lus.append(url)
        c._captcha_fil = True
        return None
    c._read_thread = lire

    assert run(c.get_new_messages()) == []
    assert len(lus) == 2
    assert c._pending == {}


def test_echec_reel_d_un_fil_reste_compte():
    c = _client_scan(1)

    async def lire(url):
        return None
    c._read_thread = lire
    run(c.get_new_messages())
    assert c._pending == {"c0": 1}


def test_read_thread_propage_l_echec_de_wait_human(monkeypatch):
    c = make_client()

    async def open_ok(url):
        return True
    c._open_thread = open_ok

    async def etat():
        return {"url": "x", "captcha": True, "login": False}
    c._state = etat

    async def attente(raison):
        c._scan_bloque = True
        return False
    c._wait_human = attente

    async def rien(*a, **k):
        return None
    monkeypatch.setattr(leboncoin.H, "pause", rien)
    monkeypatch.setattr(leboncoin.H, "human_scroll", rien)
    monkeypatch.setattr(leboncoin.H, "stray_move", rien)
    monkeypatch.setattr(leboncoin, "DEBUG_SCREENSHOTS", False)
    monkeypatch.setattr(leboncoin.asyncio, "sleep", rien)

    class P(FakePage):
        async def evaluate(self, script, *a):
            return {"count": 1, "scrollHeight": 1, "lastText": "x", "hasOffer": False}
    c.page = P()
    assert run(c._read_thread("https://www.leboncoin.fr/messages/id/c0")) is None
    assert c._scan_bloque and c._captcha_fil


# ── _track_box ────────────────────────────────────────────────────────────────

class FakeLoc:
    """Locator qui note les remontées xpath=.. et renvoie une boîte par niveau."""
    def __init__(self, boxes, niveau=0, journal=None):
        self.boxes, self.niveau = boxes, niveau
        self.journal = journal if journal is not None else []

    def locator(self, expr):
        self.journal.append((self.niveau, expr))
        return FakeLoc(self.boxes, self.niveau + 1, self.journal)

    async def bounding_box(self):
        return self.boxes.get(self.niveau)

    @property
    def first(self):
        return self


def test_track_box_remonte_d_un_niveau_a_chaque_tour():
    boxes = {1: {"x": 0, "y": 0, "width": 10, "height": 10},    # trop étroit
             2: {"x": 0, "y": 0, "width": 300, "height": 40}}
    handle = FakeLoc(boxes)
    box = run(captcha._track_box(None, handle))
    assert box["width"] == 300
    assert [n for n, _ in handle.journal] == [0, 1]


def test_track_box_repli_sur_le_conteneur():
    class Frame:
        def locator(self, sel):
            return FakeLoc({0: {"x": 0, "y": 0, "width": 200, "height": 50}})
    handle = FakeLoc({})
    box = run(captcha._track_box(Frame(), handle))
    assert box["width"] == 200


def test_selecteurs_generiques_limites_a_la_frame_captcha():
    class F:
        url = "https://example.com/autre"
    class FC:
        url = "https://geo.captcha-delivery.com/captcha/?x"
    assert ".slider" not in captcha._handle_selectors(F())
    assert "[class*='handle']" not in captcha._handle_selectors(FC())
    assert ".slider" in captcha._handle_selectors(FC())


def test_motifs_captcha_partages():
    assert captcha.CAPTCHA_TEXT_PATTERN in leboncoin.JS_STATE.replace("\\", "") or \
        "glissez vers la droite" in leboncoin.JS_STATE
    assert "captcha-delivery.com" in leboncoin.JS_STATE
    assert "input[type=\"password\"]')\n  " not in leboncoin.JS_STATE.split("login:")[1]
