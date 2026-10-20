"""Tests de config.valider_config() et du message d'erreur si prompts.py manque."""
import copy
import subprocess
import sys
import os

import pytest

import config

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def cfg(monkeypatch):
    """Config valide, avec une COPIE de VENTE (l'état global n'est pas modifié)."""
    monkeypatch.setattr(config, "SCAN_MIN", 75)
    monkeypatch.setattr(config, "SCAN_MAX", 180)
    monkeypatch.setattr(config, "MAX_CONVS_PER_SCAN", 3)
    monkeypatch.setattr(config, "NIGHT_START_HOUR", 1)
    monkeypatch.setattr(config, "NIGHT_END_HOUR", 6)
    monkeypatch.setattr(config, "VENTE", {
        "prix_affiche_eur": 100, "prix_plancher_eur": 80,
        "prix_contre_offre_eur": 90, "disponibilite_confirmee": True,
        "ville": "Paris",
    })
    return config


def test_config_valide(cfg):
    cfg.valider_config()


def test_non_appelee_a_l_import():
    # Importer config ne doit pas valider (la valeur de test locale peut être valide ou non).
    assert callable(config.valider_config)


@pytest.mark.parametrize("nom,valeur", [
    ("SCAN_MIN", 0), ("SCAN_MIN", -5), ("SCAN_MIN", 1.5), ("SCAN_MIN", True),
    ("SCAN_MIN", 200),            # > SCAN_MAX
    ("SCAN_MAX", 0),
    ("MAX_CONVS_PER_SCAN", 0),
    ("NIGHT_START_HOUR", 24), ("NIGHT_END_HOUR", -1), ("NIGHT_END_HOUR", 6.0),
    ("OLLAMA_TIMEOUT_REPLY", 0), ("OLLAMA_BACKOFF_BASE", -1),
    ("CAPTCHA_WAIT_SECONDS", 0), ("CAPTCHA_MAX_PAR_HEURE", 0),
    ("IDLE_SCAN_LIMIT", 0),
])
def test_valeur_invalide(cfg, monkeypatch, nom, valeur):
    monkeypatch.setattr(config, nom, valeur)
    with pytest.raises(ValueError) as e:
        cfg.valider_config()
    assert "Configuration invalide" in str(e.value)


def test_vente_cle_manquante(cfg, monkeypatch):
    v = copy.deepcopy(cfg.VENTE)
    del v["prix_plancher_eur"]
    monkeypatch.setattr(config, "VENTE", v)
    with pytest.raises(ValueError, match="prix_plancher_eur"):
        cfg.valider_config()


def test_vente_incoherente(cfg, monkeypatch):
    v = dict(cfg.VENTE, prix_plancher_eur=95)   # plancher > contre-offre
    monkeypatch.setattr(config, "VENTE", v)
    with pytest.raises(ValueError, match="prix_plancher_eur <="):
        cfg.valider_config()


def test_vente_prix_none_autorise(cfg, monkeypatch):
    v = dict(cfg.VENTE, prix_plancher_eur=None, prix_contre_offre_eur=None)
    monkeypatch.setattr(config, "VENTE", v)
    cfg.valider_config()


def test_tous_les_problemes_listes(cfg, monkeypatch):
    monkeypatch.setattr(config, "SCAN_MIN", 0)
    monkeypatch.setattr(config, "MAX_CONVS_PER_SCAN", 0)
    monkeypatch.setattr(config, "CAPTCHA_WAIT_SECONDS", -1)
    with pytest.raises(ValueError) as e:
        cfg.valider_config()
    msg = str(e.value)
    assert "SCAN_MIN" in msg and "MAX_CONVS_PER_SCAN" in msg and "CAPTCHA_WAIT_SECONDS" in msg


def test_prompts_absent_message_clair(tmp_path):
    """Copie config.py seul dans un dossier vide : pas de prompts.py -> SystemExit clair."""
    (tmp_path / "config.py").write_text(
        open(os.path.join(RACINE, "config.py"), encoding="utf-8").read(), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import config"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode != 0
    assert "prompts.example.py" in r.stderr and "prompts.py" in r.stderr
