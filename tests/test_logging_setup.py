import logging

import pytest

import logging_setup


@pytest.fixture(autouse=True)
def racine_propre():
    racine = logging.getLogger()
    avant, niveau = racine.handlers[:], racine.level
    yield
    for h in racine.handlers[:]:
        if h not in avant:
            racine.removeHandler(h)
            h.close()
    racine.setLevel(niveau)


def test_niveau_invalide_repli_info(tmp_path):
    assert logging_setup.setup_logging("N_IMPORTE_QUOI", log_path=str(tmp_path / "l.log")) == logging.INFO


def test_niveau_valide(tmp_path):
    assert logging_setup.setup_logging("debug", log_path=str(tmp_path / "l.log")) == logging.DEBUG


def test_pas_de_handler_duplique(tmp_path):
    p = str(tmp_path / "l.log")
    logging_setup.setup_logging("INFO", log_path=p)
    n = len(logging.getLogger().handlers)
    logging_setup.setup_logging("INFO", log_path=p)
    assert len(logging.getLogger().handlers) == n
