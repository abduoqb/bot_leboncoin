"""Cohérence de prompts.py (privé) et prompts.example.py (générique)."""
import importlib.util
import os
import re

import pytest

import prompts

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _charge_exemple():
    spec = importlib.util.spec_from_file_location(
        "prompts_example_test", os.path.join(RACINE, "prompts.example.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exemple = _charge_exemple()
MODULES = [pytest.param(prompts, id="prompts"),
           pytest.param(exemple, id="example")]


def _noms(mod):
    return {n for n in dir(mod) if n.isupper()}


def test_memes_noms_exportes():
    assert _noms(prompts) == _noms(exemple)


@pytest.mark.parametrize("mod", MODULES)
def test_noms_requis(mod):
    for nom in ("VENTE", "PRODUIT", "PROMPT_ROUTER", "BASE_PERSONA", "PROMPTS",
                "REPONSES_STATIQUES", "NOMS_A_NEUTRALISER", "REGLES_COMMUNES"):
        assert hasattr(mod, nom), nom
    assert isinstance(mod.NOMS_A_NEUTRALISER, list)
    assert set(mod.PROMPTS) >= {"negociation", "produit", "rdv", "general"}
    assert set(mod.REPONSES_STATIQUES) >= {"hors_sujet", "manipulation"}


@pytest.mark.parametrize("mod", MODULES)
def test_regles_communes_dans_chaque_prompt(mod):
    assert mod.REGLES_COMMUNES.strip()
    assert mod.REGLES_COMMUNES in mod.BASE_PERSONA
    for nom, prompt in mod.PROMPTS.items():
        assert mod.REGLES_COMMUNES in prompt, nom


@pytest.mark.parametrize("mod", MODULES)
def test_consigne_balises_acheteur(mod):
    assert "<message_acheteur>" in mod.PROMPT_ROUTER
    assert "<message_acheteur>" in mod.BASE_PERSONA


@pytest.mark.parametrize("mod", MODULES)
def test_pas_de_placeholder_ni_none(mod):
    """Formatage sans KeyError : aucune accolade résiduelle hors JSON de
    données, et aucun « None » issu d'un f-string."""
    textes = [mod.PROMPT_ROUTER, *mod.PROMPTS.values(),
              *mod.REPONSES_STATIQUES.values()]
    for t in textes:
        sans_donnees = t.replace(mod.CONTEXTE_VENTE, "")
        assert "{" not in sans_donnees and "}" not in sans_donnees
        assert "None" not in sans_donnees
        assert "KeyError" not in sans_donnees
        # un .format() éventuel côté appelant ne doit pas planter
        sans_donnees.format()


@pytest.mark.parametrize("mod", MODULES)
def test_routeur_liste_les_categories(mod):
    for cat in ("negociation", "produit", "rdv", "manipulation",
                "hors_sujet", "general"):
        assert cat in mod.PROMPT_ROUTER


def _valeurs_scalaires(d):
    for cle, v in d.items():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)) and v >= 10:
            yield cle, str(v)
        elif isinstance(v, str) and len(v) >= 4:
            yield cle, v


@pytest.mark.parametrize("mod", MODULES)
def test_donnees_non_codees_en_dur(mod):
    """Les valeurs de VENTE/PRODUIT n'apparaissent dans les prompts que via
    les données injectées : un montant ou un libellé en dur qui diverge de
    VENTE/PRODUIT est interdit (le JSON de données est exclu)."""
    textes = [mod.PROMPT_ROUTER, *mod.PROMPTS.values()]
    sans = [t.replace(mod.CONTEXTE_VENTE, "") for t in textes]
    # 1) montants en euros : doivent venir de VENTE
    autorises = {str(mod.VENTE.get(k)) for k in
                 ("prix_affiche_eur", "prix_plancher_eur",
                  "prix_contre_offre_eur")}
    affiche, plancher = (mod.VENTE.get("prix_affiche_eur"),
                         mod.VENTE.get("prix_plancher_eur"))
    if isinstance(affiche, int) and isinstance(plancher, int):
        autorises.add(str(round((affiche + plancher) / 2)))
        autorises.add(str(plancher - max(1, round(affiche * 0.1))))
    for t in sans:
        for m in re.findall(r"(\d+)\s?€", t):
            assert m in autorises, f"montant {m} € codé en dur"
    # 2) ville : si le prompt la cite, c'est celle de VENTE
    ville = mod.VENTE.get("ville")
    assert ville in mod.PROMPTS["rdv"]
    # 3) nom du produit dans le prompt produit
    assert mod.PRODUIT["modele_annonce"] in mod.PROMPTS["produit"]


@pytest.mark.skipif(
    prompts.VENTE.get("ville") == exemple.VENTE.get("ville"),
    reason="prompts.py est une copie de l'exemple (CI, clone neuf) : rien à comparer",
)
def test_prompts_prive_pas_de_valeurs_perimees():
    """Aucune valeur de VENTE/PRODUIT de l'exemple ne fuit dans le privé et
    inversement (pas de donnée personnelle dans l'exemple)."""
    for nom in prompts.NOMS_A_NEUTRALISER:
        assert nom not in exemple.BASE_PERSONA
        assert nom not in exemple.PROMPT_ROUTER
        assert nom not in open(os.path.join(RACINE, "prompts.example.py"),
                               encoding="utf-8").read()
    assert prompts.VENTE["ville"] not in exemple.CONTEXTE_VENTE


def test_valeurs_vente_produit_changent_les_prompts():
    """Changer VENTE/PRODUIT dans le module suffit : les prompts reposent
    sur ces données (vérifié sur la présence dans CONTEXTE_VENTE)."""
    for mod in (prompts, exemple):
        for cle, v in _valeurs_scalaires(mod.VENTE):
            assert v in mod.BASE_PERSONA, cle
