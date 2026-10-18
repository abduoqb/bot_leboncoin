import asyncio
import signal
import logging
import logging.handlers
import os
import sys
import random
from datetime import datetime, timedelta
from config import (
    SCAN_MIN, SCAN_MAX, NIGHT_START_HOUR, NIGHT_END_HOUR, MAX_HISTORY,
    PROMPTS, REPONSES_STATIQUES, BASE_PERSONA, MAX_CONVS_PER_SCAN, BASE_DIR,
    NOMS_A_NEUTRALISER, VENTE,
)

from conversation import ConversationManager
from llm import generate_reply, classify_message, init_session, close_session
from leboncoin import LeboncoinClient
from rules import (
    is_closing, is_manipulation, is_disponibilite, reponse_disponibilite,
    REPONSE_CLOSING, sanitize_reply,
)

# ── Logging (Bug 16: rotation 5Mo x 3 fichiers) ─────────
# Windows : la console est en cp1252 et ferait planter tout log contenant un
# emoji (UnicodeEncodeError → message perdu). On force UTF-8 sur les flux.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(BASE_DIR, "bot.log"), encoding="utf-8",
            maxBytes=5_000_000, backupCount=3
        ),
    ],
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────
db = ConversationManager()
lbc = LeboncoinClient()
stats = {"messages_traites": 0, "erreurs": 0, "scans": 0}
shutdown_event = asyncio.Event()
idle_scans = 0            # Compteur de scans consécutifs sans nouveaux messages
IDLE_SCAN_LIMIT = 10      # Nombre de scans vides avant pause repos
IDLE_REST_SECONDS = 3600  # Durée de la pause repos (1h)


async def process() -> bool:
    """Scan les nouveaux messages et y répond. Retourne True si au moins 1 message traité."""
    nouveaux = await lbc.get_new_messages()
    stats["scans"] += 1
    traite = False

    for i, msg in enumerate(nouveaux):
        if i >= MAX_CONVS_PER_SCAN:
            logger.info(f"⏸️ Limite de {MAX_CONVS_PER_SCAN} convs/scan atteinte, les {len(nouveaux) - i} restantes attendront le prochain scan.")
            break

        id_conv = msg["id_conv"]
        texte = msg["texte"]
        conv_url = msg.get("conv_url")
        logger.info(f"📩 Conv {id_conv}: {texte[:80]}...")

        # ── Vérifier si on a déjà traité ce message ──
        existing_msgs, nb_envois = await db.get_messages(id_conv)

        if existing_msgs:
            dernier_user_db = None
            for m in reversed(existing_msgs):
                if m["role"] == "user":
                    dernier_user_db = m["content"]
                    break

            # Skip SEULEMENT si : même texte ET on a déjà répondu (assistant après user)
            if dernier_user_db == texte and existing_msgs[-1]["role"] == "assistant":
                logger.info(f"⏭️ Déjà répondu à ce message, skip conv {id_conv}")
                continue

        # ── RÈGLES DÉTERMINISTES (avant tout appel LLM) ──
        # 1) Injection de prompt : enjeu de sécurité → jamais délégué au modèle.
        # 2) Formule de conclusion (« merci, bonne journée ») : le LLM y colle
        #    presque toujours une relance commerciale, on répond en dur.
        # 3) Disponibilité de l'article : donnée vérifiée, le modèle l'a déjà
        #    contredite (« Non, elle n'est plus disponible »). Réponse en dur.
        if is_manipulation(texte):
            reponse = REPONSES_STATIQUES["manipulation"]
            logger.info("🛡️ Injection de prompt détectée → réponse figée (0 appel LLM).")
        elif is_closing(texte):
            reponse = REPONSE_CLOSING
            logger.info("🧷 Conclusion détectée → réponse figée (0 appel LLM).")
        elif is_disponibilite(texte):
            reponse = reponse_disponibilite(
                VENTE.get("disponibilite_confirmee"), VENTE.get("ville"))
            logger.info("📦 Question de disponibilité → réponse figée (0 appel LLM).")
        else:
            # ── ROUTER (Appel 1) ──
            categorie = await classify_message(texte)

            # ── REPONSES STATIQUES (0 tokens) ──
            if categorie in REPONSES_STATIQUES:
                reponse = REPONSES_STATIQUES[categorie]
                logger.info(f"🛑 Catégorie statique ({categorie}), bypass LLM pass 2.")
            else:
                # ── GENERATION DYNAMIQUE (Appel 2) ──
                # Récupérer le mini-prompt spécialisé
                system_prompt = PROMPTS.get(categorie, PROMPTS["general"])

                # Construire le contexte avec BASE_PERSONA (Bug 5 : cohérence historique)
                # L'historique est stocké avec un persona neutre, on remplace ensuite
                context, nb_envois = await db.build_context(
                    id_conv, texte, BASE_PERSONA, MAX_HISTORY)

                # Assainir l'historique réinjecté : d'anciennes réponses fautives
                # (« Bonjour Monsieur Co, ... ») sont en base et le modèle les
                # recopierait. On ne modifie pas la base, on nettoie à la lecture.
                for m in context[1:-1]:
                    if m.get("role") == "assistant":
                        m["content"] = sanitize_reply(
                            m["content"], has_history=True,
                            noms=NOMS_A_NEUTRALISER,
                        )

                # Remplacer le system prompt par le mini-prompt spécialisé du tour actuel
                context[0]["content"] = system_prompt
                info_envois = (
                    f"\n\n[INFO INTERNE: Tu as déjà échangé {nb_envois} message(s) "
                    f"avec cet acheteur. Réponds au dernier message.]"
                )
                context[0]["content"] += info_envois

                # Génération LLM
                reponse = await generate_reply(context)

                # Garde-fou déterministe : aucun nom de personne, une seule
                # salutation par conversation (un modèle 4B ne tient pas la
                # consigne tout seul).
                if reponse:
                    propre = sanitize_reply(
                        reponse, has_history=nb_envois > 0,
                        noms=NOMS_A_NEUTRALISER,
                    )
                    if propre != reponse:
                        logger.info(f"🧽 Réponse nettoyée (nom/salutation) : {propre[:80]}…")
                    reponse = propre

                # Repli : une pré-réponse proposée par leboncoin (le cas échéant)
                if not reponse:
                    qr = msg.get("quick_replies") or []
                    if qr:
                        reponse = qr[0]
                        logger.info(
                            f"💡 Repli sur la pré-réponse leboncoin : {reponse[:70]}…")

                if not reponse:
                    logger.warning(f"⏭️ Pas de réponse LLM pour conv {id_conv}")
                    stats["erreurs"] += 1
                    continue

        logger.info(f"💬 Réponse: {reponse[:80]}...")

        # Envoyer sur Leboncoin (on passe aussi le texte acheteur pour la reading_pause)
        success = await lbc.send_message(id_conv, reponse, conv_url=conv_url, buyer_text=texte)
        if not success:
            stats["erreurs"] += 1
            continue

        # Sauvegarder user + assistant SEULEMENT après envoi réussi
        # (si l'envoi échoue, rien n'est sauvé → le prochain scan retente)
        await db.save_message(id_conv, "user", texte)
        await db.save_message(id_conv, "assistant", reponse)
        stats["messages_traites"] += 1
        traite = True

    if stats["scans"] % 5 == 0:
        logger.info(
            f"📊 Stats: {stats['messages_traites']} traités, "
            f"{stats['erreurs']} erreurs, {stats['scans']} scans"
        )

    return traite


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
    await init_session()  # Session HTTP partagée (Bug 4)
    await lbc.start()

    # Vérifie la connexion. Si un login ou un captcha est requis, on met le bot
    # en pause en attendant l'intervention humaine (jamais de résolution auto).
    if not await lbc.ensure_ready():
        logger.error("❌ Session non connectée après attente. Arrêt du bot.")
        await lbc.close()
        await close_session()
        await db.close()
        return

    # ── Mode contrôlé : un seul scan puis arrêt (utile pour tester) ──
    if "--once" in sys.argv:
        logger.info("▶️ Mode --once : un seul scan, puis arrêt.")
        try:
            await process()
        except Exception as e:
            logger.error(f"❌ Erreur scan unique: {e}", exc_info=True)
        await lbc.close()
        await close_session()
        await db.close()
        logger.info("🧹 Mode --once terminé.")
        return

    logger.info(f"🚀 Bot démarré — comportement humain activé (attente aléatoire + pause nocturne {NIGHT_START_HOUR}h-{NIGHT_END_HOUR}h)")

    while not shutdown_event.is_set():
        # ── Check nocturne AVANT le scan (Bug 1) ──
        now = datetime.now()
        if NIGHT_START_HOUR <= now.hour < NIGHT_END_HOUR:
            reveil = now.replace(hour=NIGHT_END_HOUR, minute=0, second=0, microsecond=0)
            if reveil < now:
                reveil += timedelta(days=1)
            duree_sec = (reveil - now).total_seconds()
            logger.info(f"🌙 Pause nocturne activée. Le bot dort jusqu'à {NIGHT_END_HOUR}h00 ({int(duree_sec/60)} min restantes)...")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=duree_sec)
            except asyncio.TimeoutError:
                pass
            continue  # re-check l'heure au réveil

        try:
            if not await lbc.ensure_alive():
                logger.error("❌ Navigateur indisponible, nouvelle tentative au prochain cycle.")
                had_messages = False
            else:
                had_messages = await process()
        except Exception as e:
            logger.error(f"❌ Erreur boucle principale: {e}", exc_info=True)
            stats["erreurs"] += 1
            had_messages = False

        # ── Système de repos (idle) ──
        global idle_scans
        if had_messages:
            idle_scans = 0
        else:
            idle_scans += 1
            if idle_scans >= IDLE_SCAN_LIMIT:
                logger.info(f"💤 {IDLE_SCAN_LIMIT} scans consécutifs sans message. Pause repos de {IDLE_REST_SECONDS // 60} min...")
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=IDLE_REST_SECONDS)
                except asyncio.TimeoutError:
                    pass
                idle_scans = 0
                continue

        try:
            duree_sec = random.randint(SCAN_MIN, SCAN_MAX)
            logger.debug(f"Attente aléatoire de {duree_sec}s avant le prochain scan")
            await asyncio.wait_for(shutdown_event.wait(), timeout=duree_sec)
        except asyncio.TimeoutError:
            pass

    # Cleanup
    logger.info("🧹 Nettoyage en cours...")
    await lbc.close()
    await close_session()  # Fermer la session HTTP (Bug 4)
    await db.close()
    logger.info(
        f"👋 Bot arrêté. Bilan: {stats['messages_traites']} messages traités, "
        f"{stats['erreurs']} erreurs, {stats['scans']} scans"
    )


if __name__ == "__main__":
    asyncio.run(main())
