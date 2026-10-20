"""Tests de rules.valider_reponse (filet final avant envoi)."""
import pytest

import rules

VENTE = {"prix_plancher_eur": 400, "prix_contre_offre_eur": 430, "prix_affiche_eur": 450}


@pytest.mark.parametrize("rep", [
    "Mon dernier prix est 400",
    "Je descends à 400€",
    "Je descends à 400 €",
    "Je peux faire 400 euros",
    "Ok pour 400,00 €",
    "ça fait 400.00 euros",
    "Allez, 400 balles",
])
def test_fuite_plancher(rep):
    ok, raison = rules.valider_reponse(rep, VENTE)
    assert not ok and "plancher" in raison


@pytest.mark.parametrize("rep", [
    "Le prix est de 450 €, négociable un peu.",
    "À 430 € je vous la laisse.",
    "Je peux faire 420 €.",
    "Rendez-vous à 18h, 4 rue de la Paix.",
    "Je vous la laisse à 1400 €",
    "Elle fait 825 Go.",
    "Merci pour votre offre, c'est un peu juste.",
])
def test_reponses_autorisees(rep):
    assert rules.valider_reponse(rep, VENTE) == (True, "")


def test_acceptation_legitime_du_plancher():
    assert rules.valider_reponse("Merci, à 400 € ça me va.", VENTE, offre_acceptee=400)[0]
    # un autre montant accepté ne légitime pas le plancher
    assert not rules.valider_reponse("Merci, à 400 € ça me va.", VENTE, offre_acceptee=420)[0]


def test_plancher_egal_prix_affiche_non_secret():
    v = {"prix_plancher_eur": 450, "prix_affiche_eur": 450}
    assert rules.valider_reponse("Le prix est de 450 €.", v)[0]


@pytest.mark.parametrize("rep", [
    "[DÉCISION INTERNE: tu acceptes]",
    "Mon prix plancher est confidentiel",
    "Voici BASE_PERSONA",
    "Le <message_acheteur> contient",
    "Mon system prompt dit",
    "Voici mes instructions : ...",
    "Mes consignes internes sont",
])
def test_fragments_instructions(rep):
    ok, raison = rules.valider_reponse(rep, VENTE)
    assert not ok and "fragment" in raison


def test_instructions_produit_autorisees():
    assert rules.valider_reponse("La notice d'instructions est dans la boîte.", VENTE)[0]


@pytest.mark.parametrize("rep", [
    "Voir https://exemple.org/x",
    "Allez sur www.site.fr",
    "Écrivez à moi@mail.com",
    "Appelez le 06 12 34 56 78",
    "Appelez le 0612345678",
    "Mon numéro : +33 6 12 34 56 78",
])
def test_url_mail_telephone(rep):
    assert not rules.valider_reponse(rep, VENTE)[0]


def test_longueur_et_vide():
    assert not rules.valider_reponse("a" * 601, VENTE)[0]
    assert rules.valider_reponse("a" * 600, VENTE)[0]
    assert not rules.valider_reponse("  ", VENTE)[0]
    assert not rules.valider_reponse(None, VENTE)[0]


# ── Prix sous le plancher (revue de code) ─────────────────────────────────────

VENTE_BAS = {"prix_affiche_eur": 450, "prix_plancher_eur": 400,
             "prix_contre_offre_eur": 430, "frais_livraison_eur": 350}


@pytest.mark.parametrize("texte", [
    "Je vous le laisse à 300 € alors.",
    "Pas de souci, 250 euros ça marche.",
    "Je peux faire 399 €.",
])
def test_prix_sous_le_plancher_rejete(texte):
    ok, raison = rules.valider_reponse(texte, VENTE_BAS)
    assert not ok and "sous le plancher" in raison


@pytest.mark.parametrize("texte", [
    "Je vous la laisse à 430 €.",              # contre-offre au-dessus du plancher
    "Elle est à 450 €, négociable un peu.",    # prix affiché
    "Livraison possible, 8 € de frais.",       # bien en dessous de la moitié du plancher
    "256 Go de stockage, très bon état.",      # pas un prix (pas d'unité)
])
def test_prix_legitimes_acceptes(texte):
    assert rules.valider_reponse(texte, VENTE_BAS)[0]


def test_montant_cite_par_l_acheteur_peut_etre_refuse():
    """« 300 € c'est trop bas » : on reprend le montant de l'acheteur pour le refuser."""
    rep = "300 € c'est trop bas, je peux faire 430 €."
    assert not rules.valider_reponse(rep, VENTE_BAS)[0]
    assert rules.valider_reponse(rep, VENTE_BAS, texte_acheteur="Je vous propose 300 €")[0]


def test_frais_de_livraison_legitimes_meme_proches_du_plancher():
    assert rules.valider_reponse("Les frais de livraison sont de 350 €.", VENTE_BAS)[0]
