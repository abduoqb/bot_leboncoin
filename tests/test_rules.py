"""Tests des règles déterministes de rules.py (aucun appel LLM / navigateur)."""
import pytest

import rules

VENTE = {
    "prix_plancher_eur": 400,
    "prix_contre_offre_eur": 430,
    "prix_affiche_eur": 500,
}


# ── a) extraction des montants ───────────────────────────────────────────────
@pytest.mark.parametrize("texte, attendu", [
    ("1 200 €", [1200]),
    ("1 200 euros", [1200]),
    ("1.200", [1200]),
    ("1200€", [1200]),
    ("349,50", [349.5]),
    ("350", [350]),
    ("350 euros", [350]),
    # pas des montants : heures, dates, téléphone
    ("je passe à 20h", []),
    ("rdv à 20h30", []),
    ("rdv le 15/03 à 18h30", []),
    ("samedi 12.05.2024", []),
    ("appelez le 06 12 34 56 78", []),
])
def test_montants(texte, attendu):
    assert rules.montants(texte, 10, 100_000) == attendu


# ── b) choix du montant lié au verbe d'offre ─────────────────────────────────
@pytest.mark.parametrize("texte, offre", [
    ("je l'ai vue à 500, je vous propose 350", 350),
    ("je l'ai vue à 500 ailleurs, je vous en donne 350", 350),
    ("350 € ça vous va ?", 350),
    ("je vous propose 350", 350),
    ("Je vous ai propose 360 je peux monter a 370€", 370),
    ("400 € et je viens ce soir", 400),
])
def test_offre_choisie(texte, offre):
    d = rules.offre_chiffree(texte, VENTE)
    assert d is not None and d[0] == offre


@pytest.mark.parametrize("texte", [
    # plusieurs montants sans indice clair -> on laisse le LLM trancher
    "je viens avec 300 ou 350 en billets",
    "j'hésite entre 300 et 350, je viens avec des billets",
])
def test_offre_ambigue_renvoie_none(texte):
    assert rules.offre_chiffree(texte, VENTE) is None


# ── c) clôture figée ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("texte, attendu", [
    ("Merci, bonne journée", True),
    ("Merci pour votre retour rapide\nBelle journée", True),
    ("merci mais trop cher", False),
    ("merci je vous donne 300 euros", False),
    ("merci, non c'est trop pour moi", False),
    ("merci, un meilleur prix possible", False),
    ("merci, je confirme pour demain à mon avis", False),
    ("merci, vous pouvez baisser ?", False),
])
def test_is_closing(texte, attendu):
    assert rules.is_closing(texte) is attendu


# ── d) disponibilité ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("texte, attendu", [
    ("est-ce toujours dispo ?", True),
    ("Elle est toujours disponible ?", True),
    ("encore dispo ?", True),
    ("dispo", False),
    ("dispo ?", False),
    ("quand serez-vous dispo ?", False),
    ("quand êtes-vous disponible ?", False),
    ("je suis dispo samedi", False),
])
def test_is_disponibilite(texte, attendu):
    assert rules.is_disponibilite(texte) is attendu


# ── e) civilité + nom ────────────────────────────────────────────────────────
@pytest.mark.parametrize("texte, attendu", [
    ("Madame est ravie de vous répondre", "Madame est ravie de vous répondre"),
    ("Bonjour Monsieur Co, elle est disponible", "Bonjour Monsieur, elle est disponible"),
    ("Merci Madame Dupont", "Merci Madame"),
])
def test_civilite(texte, attendu):
    assert rules.sanitize_reply(texte) == attendu
