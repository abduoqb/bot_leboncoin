import asyncio
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boucle import delai_backoff, tirer_intervalle, executer_nettoyage, BACKOFF_PLAFOND


class RngFixe:
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v

    def randint(self, a, b):
        return a


def test_backoff_zero_erreur():
    assert delai_backoff(0, 60) == 0


def test_backoff_exponentiel_sans_jitter():
    rng = RngFixe(0.0)
    assert delai_backoff(1, 60, rng) == 60
    assert delai_backoff(2, 60, rng) == 120
    assert delai_backoff(3, 60, rng) == 240


def test_backoff_plafonne_meme_avec_jitter():
    rng = RngFixe(1.0)
    assert delai_backoff(50, 60, rng) == BACKOFF_PLAFOND
    assert delai_backoff(10_000, 60, rng) <= BACKOFF_PLAFOND


def test_backoff_jitter_borne():
    for _ in range(100):
        d = delai_backoff(2, 60, random)
        assert 120 <= d <= 144


def test_intervalle_min_superieur_max_ne_leve_pas():
    for _ in range(50):
        assert 10 <= tirer_intervalle(30, 10) <= 30


def test_intervalle_normal():
    assert tirer_intervalle(5, 5) == 5


def test_nettoyage_isole_les_erreurs():
    appels = []

    async def ok_async():
        appels.append("a")

    def ok_sync():
        appels.append("b")

    async def boom():
        appels.append("boom")
        raise RuntimeError("x")

    def boom_sync():
        raise ValueError("y")

    echecs = asyncio.run(executer_nettoyage([
        ("boom", boom), ("a", ok_async), ("boom2", boom_sync), ("b", ok_sync),
    ]))
    assert appels == ["boom", "a", "b"]
    assert echecs == ["boom", "boom2"]
