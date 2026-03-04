import asyncio
import signal
import logging
import sys
from config import SYSTEM_PROMPT, SCAN_INTERVAL, MAX_HISTORY
from conversation import ConversationManager
from llm import generate_reply
from leboncoin import LeboncoinClient

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────
db = ConversationManager()
lbc = LeboncoinClient()
stats = {"messages_traites": 0, "erreurs": 0, "scans": 0}
shutdown_event = asyncio.Event()


async def process():
    """Scan les nouveaux messages et y répond."""
    nouveaux = await lbc.get_new_messages()
    stats["scans"] += 1

    for msg in nouveaux:
        id_conv = msg["id_conv"]
        texte = msg["texte"]
        logger.info(f"📩 Conv {id_conv}: {texte[:80]}...")

        # Construire le contexte avec nb_envois intégré au prompt
        context, nb_envois = db.build_context(id_conv, texte, SYSTEM_PROMPT, MAX_HISTORY)

        # Injecter le compteur d'échanges dans le system prompt
        info_envois = f"\n\n[INFO INTERNE: Tu as déjà échangé {nb_envois} message(s) avec cet acheteur.]"
        context[0]["content"] += info_envois

        # Générer la réponse via Ollama
        reponse = generate_reply(context)
        if not reponse:
            logger.warning(f"⏭️ Pas de réponse LLM pour conv {id_conv}")
            stats["erreurs"] += 1
            continue

        logger.info(f"💬 Réponse: {reponse[:80]}...")

        # Envoyer sur Leboncoin
        success = await lbc.send_message(id_conv, reponse)
        if not success:
            stats["erreurs"] += 1
            continue

        # Sauvegarder dans la DB
        db.save_message(id_conv, "user", texte)
        db.save_message(id_conv, "assistant", reponse)
        stats["messages_traites"] += 1

    if stats["scans"] % 5 == 0:
        logger.info(
            f"📊 Stats: {stats['messages_traites']} traités, "
            f"{stats['erreurs']} erreurs, {stats['scans']} scans"
        )


async def main():
    # Graceful shutdown
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("🛑 Arrêt demandé (Ctrl+C)...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows ne supporte pas add_signal_handler, fallback
            signal.signal(sig, lambda s, f: _signal_handler())

    # Démarrage
    await lbc.start()

    # Login email/password seulement si aucun cookies.json ni session.json
    if not lbc.session_exists():
        await lbc.login()

    logger.info(f"🚀 Bot démarré — scan toutes les {SCAN_INTERVAL}s")

    while not shutdown_event.is_set():
        try:
            await process()
        except Exception as e:
            logger.error(f"❌ Erreur boucle principale: {e}", exc_info=True)
            stats["erreurs"] += 1

        # Attend SCAN_INTERVAL tout en restant réactif au shutdown
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=SCAN_INTERVAL)
        except asyncio.TimeoutError:
            pass  # timeout normal, on reboucle

    # Cleanup
    logger.info("🧹 Nettoyage en cours...")
    await lbc.close()
    db.close()
    logger.info(
        f"👋 Bot arrêté. Bilan: {stats['messages_traites']} messages traités, "
        f"{stats['erreurs']} erreurs, {stats['scans']} scans"
    )


if __name__ == "__main__":
    asyncio.run(main())
