"""API publique de leboncoin.py (ré-exports) et contenu des JS générés par js.py."""

import pytest

import js
import leboncoin
import state

NOMS_PUBLICS = [
    "LeboncoinClient", "MAX_TENTATIVES", "_STATE_FILE", "_STATE_VERSION",
    "JS_STATE", "JS_LIST", "JS_LIST_BOX", "JS_THREAD",
    "CONV_LINK", "BUBBLE", "TEXTAREA", "SEND_BTN", "LBC_HOME", "LBC_MESSAGES",
    "_norm_texte", "_fil_contient_envoi", "_profil_occupe", "id_conv_de",
    "H", "asyncio",
]

METHODES_PRIVEES = [
    "_candidats", "_noter_echec", "_load_all_conversations", "_defilement_du_scan",
    "_noter_captcha", "_load_state", "_save_state", "_read_thread", "_open_thread",
    "_lister_conversations", "_classer_fil", "get_new_messages", "send_message",
    "ensure_alive", "ensure_ready", "start", "close",
]


@pytest.mark.parametrize("nom", NOMS_PUBLICS)
def test_nom_public_present(nom):
    assert hasattr(leboncoin, nom)


@pytest.mark.parametrize("nom", METHODES_PRIVEES)
def test_methode_presente(nom):
    assert callable(getattr(leboncoin.LeboncoinClient, nom))


def test_reexports_identiques():
    assert leboncoin.JS_LIST is js.JS_LIST
    assert leboncoin._norm_texte is state._norm_texte
    assert leboncoin._fil_contient_envoi is state._fil_contient_envoi


def test_js_contiennent_les_selecteurs():
    assert js.CONV_LINK in js.JS_LIST and js.CONV_LINK in js.JS_LIST_BOX
    assert js.BUBBLE in js.JS_THREAD and js.BUBBLE in js.JS_THREAD_READY
    for script in (js.JS_STATE, js.JS_LIST, js.JS_LIST_BOX, js.JS_THREAD, js.JS_THREAD_READY):
        assert "__CONV_LINK__" not in script and "__BUBBLE__" not in script
        assert "__CAPTCHA_EXPR__" not in script


def test_id_conv_de():
    assert state.id_conv_de("/messages/id/abc") == "abc"
    assert state.id_conv_de("https://www.leboncoin.fr/messages/id/abc/") == "abc"


def test_state_file_monkeypatch(tmp_path, monkeypatch):
    """_STATE_FILE reste lu à l'appel dans leboncoin (les selftests le remplacent)."""
    f = tmp_path / "s.json"
    monkeypatch.setattr(leboncoin, "_STATE_FILE", str(f))
    c = leboncoin.LeboncoinClient()
    c._noter_echec("x")
    assert f.exists()
