from datetime import datetime

import pytest

from horaire import en_pause_nocturne, prochain_reveil, valider_heures


def a(h, m=0):
    return datetime(2026, 10, 20, h, m)


def test_plage_simple_1_6():
    assert not en_pause_nocturne(a(0, 59), 1, 6)
    assert en_pause_nocturne(a(1), 1, 6)      # borne début incluse
    assert en_pause_nocturne(a(5, 59), 1, 6)
    assert not en_pause_nocturne(a(6), 1, 6)  # borne fin exclue
    assert not en_pause_nocturne(a(12), 1, 6)


def test_plage_passant_minuit_23_6():
    assert not en_pause_nocturne(a(22, 59), 23, 6)
    assert en_pause_nocturne(a(23), 23, 6)
    assert en_pause_nocturne(a(0), 23, 6)
    assert en_pause_nocturne(a(5, 59), 23, 6)
    assert not en_pause_nocturne(a(6), 23, 6)
    assert not en_pause_nocturne(a(14), 23, 6)


def test_debut_egal_fin_jamais_en_pause():
    for h in range(24):
        assert not en_pause_nocturne(a(h), 3, 3)


def test_bornes_0_et_23():
    assert en_pause_nocturne(a(0), 0, 6)
    assert not en_pause_nocturne(a(23), 0, 6)
    assert en_pause_nocturne(a(23), 22, 0)
    assert not en_pause_nocturne(a(0), 22, 0)


def test_prochain_reveil_le_soir():
    assert prochain_reveil(a(23, 30), 6) == datetime(2026, 10, 21, 6, 0)


def test_prochain_reveil_apres_minuit():
    assert prochain_reveil(a(2, 15), 6) == datetime(2026, 10, 20, 6, 0)


def test_prochain_reveil_strictement_apres():
    assert prochain_reveil(a(6, 0), 6) == datetime(2026, 10, 21, 6, 0)


def test_validation_ok():
    valider_heures(0, 23)
    valider_heures(23, 6)
    valider_heures(3, 3)


@pytest.mark.parametrize("debut,fin", [
    (-1, 6), (23, 24), (24, 6), (1, 99),
    (1.5, 6), ("23", 6), (None, 6), (True, 6), (1, "6"),
])
def test_validation_invalide(debut, fin):
    with pytest.raises(ValueError):
        valider_heures(debut, fin)
