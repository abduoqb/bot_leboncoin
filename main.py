import asyncio
import signal
import logging
import logging.handlers
import sys
from config import SYSTEM_PROMPT, SCAN_INTERVAL, MAX_HISTORY
from conversation import ConversationManager
from llm import generate_reply
from leboncoin import LeboncoinClient

# ── Logging (Bug 16: rotation 5Mo x 3 fichiers) ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            "bot.log", encoding="utf-8", maxBytes=5_000_000, backupCount=3
        ),
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
        conv_url = msg.get("conv_url")
        logger.info(f"📩 Conv {id_conv}: {texte[:80]}...")

        # ── Vérifier si on a déjà traité ce message ──
        existing_msgs, nb_envois = db.get_messages(id_conv)

        if existing_msgs:
            # Trouver le dernier message user en DB
            dernier_user_db = None
            for m in reversed(existing_msgs):
                if m["role"] == "user":
                    dernier_user_db = m["content"]
                    break

            # Skip SEULEMENT si : même texte ET on a déjà répondu (assistant après user)
            if dernier_user_db == texte and existing_msgs[-1]["role"] == "assistant":
                logger.info(f"⏭️ Déjà répondu à ce message, skip conv {id_conv}")
                continue

        # ── Construire le contexte AVANT de sauvegarder (évite le doublon) ──
        context, nb_envois = db.build_context(id_conv, texte, SYSTEM_PROMPT, MAX_HISTORY)
        info_envois = f"\n\n[INFO INTERNE: Tu as déjà échangé {nb_envois} message(s) avec cet acheteur.]"
        context[0]["content"] += info_envois

        # Générer la réponse via Ollama
        reponse = await generate_reply(context)
        if not reponse:
            logger.warning(f"⏭️ Pas de réponse LLM pour conv {id_conv}")
            stats["erreurs"] += 1
            continue

        logger.info(f"💬 Réponse: {reponse[:80]}...")

        # Envoyer sur Leboncoin
        success = await lbc.send_message(id_conv, reponse, conv_url=conv_url)
        if not success:
            stats["erreurs"] += 1
            continue

        # Sauvegarder user + assistant SEULEMENT après envoi réussi
        # (si l'envoi échoue, rien n'est sauvé → le prochain scan retente)
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
            signal.signal(sig, lambda s, f: _signal_handler())

    # Démarrage
    await lbc.start()

    # En mode CDP, le Chrome réel gère l'auth.
    # En mode Chromium, login manuel seulement si aucune session sauvegardée.
    if not lbc._is_cdp and not lbc.session_exists():
        await lbc.login()

    logger.info(f"🚀 Bot démarré — scan toutes les {SCAN_INTERVAL}s")

    while not shutdown_event.is_set():
        try:
            await process()
        except Exception as e:
            logger.error(f"❌ Erreur boucle principale: {e}", exc_info=True)
            stats["erreurs"] += 1

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=SCAN_INTERVAL)
        except asyncio.TimeoutError:
            pass

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
