"""
selftest_context.py — Le fil de la page est-il bien la source du contexte ?

Enjeu : JS_THREAD lit le fil réel, mais l'historique envoyé au LLM venait
uniquement de la base. Une base incomplète (messages envoyés à la main, avant
l'installation du bot) ou fausse (anciennes réponses fautives) donnait des
réponses hors sujet. Ce test verrouille la priorité au fil.

Aucun navigateur, aucun appel LLM : base SQLite temporaire.
Usage : python -u selftest_context.py
"""

import os
import sys
import tempfile
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging  # noqa: E402
logging.basicConfig(level=logging.ERROR, format="%(message)s")

from conversation import ConversationManager, fil_vers_historique  # noqa: E402

_SYS = "SYSTEME"

OK = [0]
KO = [0]


def verifie(titre, attendu, obtenu):
    bon = attendu == obtenu
    (OK if bon else KO)[0] += 1
    print(f"  {'✅' if bon else '❌'} {titre}")
    if not bon:
        print(f"     attendu : {attendu}")
        print(f"     obtenu  : {obtenu}")


def bulles(*pairs):
    """bulles(('in', 'Bonjour'), ('out', 'Bonjour, oui')) → fil JS_THREAD."""
    return [{"side": s, "text": t} for s, t in pairs]


def hist(ctx):
    """Historique du contexte (sans le system prompt ni le message à traiter)."""
    return [(m["role"], m["content"]) for m in ctx[1:-1]]


async def main():
    chemin = os.path.join(tempfile.gettempdir(), "bot_lbc_selftest_ctx.db")
    if os.path.exists(chemin):
        os.remove(chemin)
    db = ConversationManager(db_path=chemin)

    print("\n" + "=" * 74)
    print("  CONTEXTE : LE FIL DE LA PAGE FAIT FOI")
    print("=" * 74)

    # ── 1. Base vide, fil riche (cas vécu : conv 112baf4f) ──
    fil = fil_vers_historique(bulles(
        ("in", "Bonjour, elle est toujours dispo ?"),
        ("out", "Oui, toujours disponible."),
        ("in", "Vous feriez un prix ?"),
    ))
    ctx, nb = await db.build_context("c1", "Vous feriez un prix ?", _SYS, 8, fil=fil)
    verifie("1. base vide + fil riche → tout vient du fil",
            [("user", "Bonjour, elle est toujours dispo ?"),
             ("assistant", "Oui, toujours disponible.")], hist(ctx))
    verifie("   et nb_envois reflète le fil (plus de « Bonjour » en double)", 1, nb)

    # ── 2. Le fil prime sur une base fausse ──
    await db.save_message("c2", "user", "Bonjour Raphaël Co")
    await db.save_message("c2", "assistant", "Bonjour Monsieur Co, la console est dispo.")
    fil2 = fil_vers_historique(bulles(
        ("in", "Bonjour Raphaël Co"),
        ("out", "Bonjour, oui elle est disponible à Caen."),
        ("in", "Parfait."),
    ))
    ctx, _ = await db.build_context("c2", "Parfait.", _SYS, 8, fil=fil2)
    verifie("2. fil prioritaire : l'ancienne réponse fautive de la base disparaît",
            [("user", "Bonjour Raphaël Co"),
             ("assistant", "Bonjour, oui elle est disponible à Caen.")], hist(ctx))

    # ── 3. La base complète les messages que la page n'affiche plus ──
    await db.save_message("c3", "user", "Bonjour")
    await db.save_message("c3", "assistant", "Bonjour, comment puis-je vous aider ?")
    await db.save_message("c3", "user", "Je peux venir samedi ?")
    await db.save_message("c3", "assistant", "Oui, samedi c'est possible.")
    # la page n'affiche que la fin du fil
    fil3 = fil_vers_historique(bulles(
        ("out", "Oui, samedi c'est possible."),
        ("in", "Vers 15h ?"),
    ))
    ctx, _ = await db.build_context("c3", "Vers 15h ?", _SYS, 8, fil=fil3)
    verifie("3. page tronquée → la base complète l'historique plus ancien",
            [("user", "Bonjour"),
             ("assistant", "Bonjour, comment puis-je vous aider ?"),
             ("user", "Je peux venir samedi ?"),
             ("assistant", "Oui, samedi c'est possible.")], hist(ctx))

    # ── 4. Aucun doublon quand base et fil se recouvrent ──
    verifie("4. aucun message dupliqué au raccord",
            len([c for _, c in hist(ctx) if c == "Oui, samedi c'est possible."]), 1)

    # ── 5. Sans fil lisible, la base reste utilisée seule ──
    ctx, nb = await db.build_context("c3", "Vers 15h ?", _SYS, 8, fil=None)
    verifie("5. fil illisible → repli sur la base",
            4, len(hist(ctx)))

    # ── 6. Troncature à max_hist ──
    paires = []
    for i in range(10):
        paires += [("in", f"question {i}"), ("out", f"reponse {i}")]
    fil6 = fil_vers_historique(bulles(*paires))
    ctx, _ = await db.build_context("c6", "question finale", _SYS, 8, fil=fil6)
    verifie("6. historique borné à max_hist (ici 8)", 8, len(hist(ctx)))

    # ── 7. La suite finale de l'acheteur n'est pas comptée deux fois ──
    brut = bulles(("in", "Bonjour"), ("out", "Bonjour"),
                  ("in", "Dispo ?"), ("in", "Et vous feriez un prix ?"))
    texte = "Dispo ?\nEt vous feriez un prix ?"   # ce que renvoie buyersText
    ctx, _ = await db.build_context("c7", texte, _SYS, 8,
                                    fil=fil_vers_historique(brut))
    verifie("7. la suite finale de l'acheteur sort de l'historique",
            [("user", "Bonjour"), ("assistant", "Bonjour")], hist(ctx))
    verifie("   et arrive UNE SEULE fois, en dernier, comme message à traiter",
            (1, ("user", texte)),
            (sum(1 for m in ctx if m["content"] == texte),
             (ctx[-1]["role"], ctx[-1]["content"])))

    # ── 8. Le message à traiter est bien le dernier du contexte ──
    verifie("8. le dernier tour est le message de l'acheteur",
            "user", ctx[-1]["role"])

    await db.close()
    if os.path.exists(chemin):
        os.remove(chemin)

    print("\n" + "=" * 74)
    print(f"  Réussis : {OK[0]}   Échecs : {KO[0]}")
    print("=" * 74)
    return 1 if KO[0] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
