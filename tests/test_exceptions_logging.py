"""Observabilité : aucun handler d'exception ne doit avaler une erreur en silence."""
import ast
import asyncio
import logging
from pathlib import Path

import captcha
import human

RACINE = Path(__file__).resolve().parent.parent
FICHIERS = ["leboncoin.py", "captcha.py", "human.py", "instance.py", "js.py", "state.py"]

# Handlers autorisés à ne contenir que `pass`/`continue`... : (fichier, ligne) -> raison.
# Vide : tous les cas ont ete rendus visibles par un log.
SILENCIEUX_AUTORISES: dict = {}


def _handlers():
    for nom in FICHIERS:
        p = RACINE / nom
        if not p.exists():
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ExceptHandler):
                yield nom, n


def test_pas_de_except_nu():
    assert [(f, n.lineno) for f, n in _handlers() if n.type is None] == []


def test_pas_de_except_baseexception():
    def noms(t):
        elts = t.elts if isinstance(t, ast.Tuple) else [t]
        return {getattr(e, "id", getattr(e, "attr", "")) for e in elts}
    mauvais = [(f, n.lineno) for f, n in _handlers()
               if n.type is not None and {"BaseException", "CancelledError"} & noms(n.type)]
    assert mauvais == []


def test_aucun_handler_uniquement_pass():
    mauvais = []
    for f, n in _handlers():
        if all(isinstance(s, ast.Pass) for s in n.body) and (f, n.lineno) not in SILENCIEUX_AUTORISES:
            mauvais.append((f, n.lineno))
    assert mauvais == []


def test_handlers_sans_log_sont_listes():
    """Tout handler sans logger.* doit être un simple repli de valeur (pas un pass) ;
    on vérifie surtout que la plupart des handlers loggent."""
    sans_log = [(f, n.lineno) for f, n in _handlers()
                if "logger." not in ast.unparse(n)]
    # Reste toléré : FileNotFoundError d'état absent (normal) et parse int dans state.
    assert len(sans_log) <= 3, sans_log


class _PageQuiLeve:
    async def evaluate(self, *a, **k):
        raise RuntimeError("page fermee")


def test_viewport_logge_en_debug_sans_remonter(caplog):
    with caplog.at_level(logging.DEBUG, logger="human"):
        vp = asyncio.run(human._viewport(_PageQuiLeve()))
    assert vp == human.DEFAULT_VIEWPORT
    assert any("viewport" in r.getMessage().lower() and r.levelno == logging.DEBUG
               for r in caplog.records)


def test_cookie_datadome_logge_en_debug(caplog):
    class Ctx:
        async def cookies(self):
            raise RuntimeError("contexte ferme")
    with caplog.at_level(logging.DEBUG, logger="captcha"):
        assert asyncio.run(captcha.datadome_cookie(Ctx())) is None
    assert any("datadome" in r.getMessage() for r in caplog.records)


def test_human_move_souris_qui_leve(caplog):
    class Mouse:
        async def move(self, x, y):
            raise RuntimeError("boom")

    class Page:
        mouse = Mouse()
        async def evaluate(self, *a, **k):
            raise RuntimeError("closed")

    with caplog.at_level(logging.DEBUG):
        try:
            asyncio.run(human.human_move(Page(), 100, 100))
        except Exception as e:  # le comportement existant reste inchangé
            raise AssertionError(f"ne doit rien lever : {e}")
    assert caplog.records
