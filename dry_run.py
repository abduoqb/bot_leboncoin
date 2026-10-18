"""
dry_run.py — Test À BLANC du pipeline complet.

Lit les conversations, classifie et génère les réponses… mais N'ENVOIE RIEN.
Aucun message n'est posté sur Leboncoin : c'est un test sûr.

Note : pour lire le fil, le script ouvre les conversations comme le ferait
un humain (cela les marque « lus »). Aucune autre action n'est effectuée.

Usage : python -u dry_run.py
"""

import asyncio
import logging
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
if "--debug" in sys.argv:
    logging.getLogger().setLevel(logging.DEBUG)

from config import PROMPTS, BASE_PERSONA, REPONSES_STATIQUES  # noqa: E402
from conversation import ConversationManager  # noqa: E402
from llm import classify_message, generate_reply, init_session, close_session  # noqa: E402
from leboncoin import LeboncoinClient  # noqa: E402


async def main():
    db = ConversationManager()
    lbc = LeboncoinClient()
    await init_session()
    await lbc.start()
    try:
        if not await lbc.ensure_ready():
            print("\n❌ Pas de session connectée — test interrompu.")
            return

        msgs = await lbc.get_new_messages()
        print(f"\n{'=' * 70}\n  {len(msgs)} conversation(s) avec un message acheteur en attente\n{'=' * 70}")

        for m in msgs:
            print(f"\n--- conv {m['id_conv']}  (non_lu={m['unread']}) ---")
            print(f"  ACHETEUR  : {m['texte'][:300]}")
            cat = await classify_message(m['texte'])
            print(f"  CATÉGORIE : {cat}")
            if cat in REPONSES_STATIQUES:
                print(f"  RÉPONSE (statique) : {REPONSES_STATIQUES[cat]}")
            else:
                ctx, nb = await db.build_context(m["id_conv"], m["texte"], BASE_PERSONA, 8)
                ctx[0]["content"] = PROMPTS.get(cat, PROMPTS["general"])
                rep = await generate_reply(ctx)
                print(f"  RÉPONSE (LLM) : {rep}")

        print(f"\n{'=' * 70}\n  Test à blanc terminé — AUCUN message n'a été envoyé.\n{'=' * 70}")
    finally:
        await lbc.close()
        await close_session()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
