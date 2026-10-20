import asyncio
import signal
import logging
import sys
from datetime import datetime
from config import (
    SCAN_MIN, SCAN_MAX, NIGHT_START_HOUR, NIGHT_END_HOUR, MAX_HISTORY,
    PROMPTS, REPONSES_STATIQUES, BASE_PERSONA, MAX_CONVS_PER_SCAN,
    NOMS_A_NEUTRALISER, VENTE, IDLE_SCAN_LIMIT, IDLE_REST_SECONDS, valider_config,
    HEARTBEAT_FILE,
)

from conversation import ConversationManager, fil_vers_historique
import instance
from horaire import en_pause_nocturne, prochain_reveil, valider_heures
from boucle import delai_backoff, tirer_intervalle, executer_nettoyage
from heartbeat import ecrire_heartbeat
from logging_setup import setup_logging
from llm import generate_reply, classify_message, init_session, close_session
from leboncoin import LeboncoinClient
from rules import (
    is_closing, is_manipulation, is_disponibilite, reponse_disponibilite,
    corrige_offre, ligne_decision_offre, REPONSE_CLOSING, sanitize_reply,
    valider_reponse, offre_chiffree, reponse_contre_offre,
)

logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────
# db et lbc sont instanciés dans main() (aucun effet de bord à l'import ;
# les tests les remplacent par monkeypatch).
db = None
lbc = None
stats = {"messages_traites": 0, "erreurs": 0, "scans": 0}
shutdown_event = asyncio.Event()   # Python >= 3.10 : pas de boucle liée à la création


async def process() -> bool:
    """Scan les nouveaux messages et y répond. Retourne True si au moins 1 message traité.

    Le log de stats est dans un finally : une exception ne le saute pas.
    """
    scans_avant = stats["scans"]
    try:
        return await _scan_et_repondre()
    finally:
        if stats["scans"] != scans_avant and stats["scans"] % 5 == 0:
            logger.info(
                f"📊 Stats: {stats['messages_traites']} traités, "
                f"{stats['erreurs']} erreurs, {stats['scans']} scans"
            )


def _deja_repondu(existing_msgs, texte) -> bool:
    """True si le dernier message acheteur est identique ET déjà suivi d'une réponse."""
    if not existing_msgs:
        return False
    dernier_user_db = None
    for m in reversed(existing_msgs):
        if m["role"] == "user":
            dernier_user_db = m["content"]
            break
    return dernier_user_db == texte and existing_msgs[-1]["role"] == "assistant"


async def _generer_reponse_llm(msg, id_conv, texte, categorie):
    """Génération dynamique (appel 2) + garde-fous. Retourne la réponse ou None."""
    # Récupérer le mini-prompt spécialisé
    system_prompt = PROMPTS.get(categorie, PROMPTS["general"])

    # Construire le contexte : le FIL LU DANS LA PAGE est prioritaire
    # (il contient la réalité, y compris les messages envoyés à la
    # main) ; la base ne complète que les messages plus anciens.
    hist_fil = fil_vers_historique(msg.get("messages"))
    if msg.get("fil_tronque"):
        logger.info("📜 Le fil affiché est tronqué → la base complète "
                    "l'historique plus ancien.")
    context, nb_envois = await db.build_context(
        id_conv, texte, BASE_PERSONA, MAX_HISTORY, fil=hist_fil)
    logger.info(f"🧠 Contexte : {len(context) - 2} tour(s) d'historique "
                f"({len(hist_fil)} lu(s) dans la page).")

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

    # Décision chiffrée interne (offre de l'acheteur) : c'est le
    # calcul que le modèle rate (plancher confondu avec la
    # contre-offre). On la lui donne déjà tranchée.
    if categorie == "negociation":
        decision = ligne_decision_offre(texte, VENTE)
        if decision:
            context[0]["content"] += "\n\n" + decision
            logger.info(f"🧮 {decision}")

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

    # Décision chiffrée : sur un montant, on ne laisse pas le modèle
    # se tromper (il a déjà accepté sous le plancher, et recopié
    # l'historique au lieu d'accepter une offre au prix).
    if reponse and categorie == "negociation":
        fix = corrige_offre(reponse, texte, VENTE)
        if fix:
            logger.warning(
                f"🛡️ Décision chiffrée reprise en main : {fix[:70]}…")
            reponse = fix

    # Repli : une pré-réponse proposée par leboncoin (le cas échéant)
    if not reponse:
        qr = msg.get("quick_replies") or []
        if qr:
            reponse = sanitize_reply(
                qr[0], has_history=nb_envois > 0,
                noms=NOMS_A_NEUTRALISER,
            )
            logger.info(
                f"💡 Repli sur la pré-réponse leboncoin : {reponse[:70]}…")
    return reponse


async def _choisir_reponse(msg, id_conv, texte):
    """Choisit la réponse : règles figées, puis routeur + LLM.

    Retourne (reponse, categorie). reponse vaut None/"" si le LLM n'a rien
    donné et qu'aucune pré-réponse n'existe (l'échec est géré par l'appelant).
    """
    # ── RÈGLES DÉTERMINISTES (avant tout appel LLM) ──
    # 1) Injection de prompt : enjeu de sécurité → jamais délégué au modèle.
    # 2) Formule de conclusion (« merci, bonne journée ») : le LLM y colle
    #    presque toujours une relance commerciale, on répond en dur.
    # 3) Disponibilité de l'article : donnée vérifiée, le modèle l'a déjà
    #    contredite (« Non, elle n'est plus disponible »). Réponse en dur.
    if is_manipulation(texte):
        logger.info("🛡️ Injection de prompt détectée → réponse figée (0 appel LLM).")
        return REPONSES_STATIQUES["manipulation"], None
    if is_closing(texte):
        logger.info("🧷 Conclusion détectée → réponse figée (0 appel LLM).")
        return REPONSE_CLOSING, None
    if is_disponibilite(texte):
        reponse = reponse_disponibilite(
            VENTE.get("disponibilite_confirmee"), VENTE.get("ville"))
        logger.info("📦 Question de disponibilité → réponse figée (0 appel LLM).")
        return reponse, None

    # ── ROUTER (Appel 1) ──
    categorie = await classify_message(texte)

    # ── REPONSES STATIQUES (0 tokens) ──
    if categorie in REPONSES_STATIQUES:
        logger.info(f"🛑 Catégorie statique ({categorie}), bypass LLM pass 2.")
        return REPONSES_STATIQUES[categorie], categorie

    # ── GENERATION DYNAMIQUE (Appel 2) ──
    return await _generer_reponse_llm(msg, id_conv, texte, categorie), categorie


def _valider_ou_remplacer(reponse, categorie, texte, id_conv):
    """Validation finale (toutes sources) : plancher, fragments d'instructions,
    URL/téléphone, longueur. En cas de rejet : réponse statique sûre.

    Retourne la réponse à envoyer, ou None si même le repli est invalide
    (l'échec est alors déjà compté et noté).
    """
    decision = offre_chiffree(texte, VENTE)
    acceptee = decision[0] if decision and decision[1] == decision[0] else None
    ok, raison = valider_reponse(reponse, VENTE, offre_acceptee=acceptee)
    if ok:
        return reponse
    logger.warning(f"🚫 Réponse rejetée pour conv {id_conv} ({raison}) : {reponse[:70]}…")
    if categorie == "negociation":
        sure = reponse_contre_offre(VENTE)
    else:
        sure = REPONSES_STATIQUES.get("manipulation")
    if sure and valider_reponse(sure, VENTE, offre_acceptee=acceptee)[0]:
        return sure
    stats["erreurs"] += 1
    lbc._noter_echec(id_conv)
    return None


async def _envoyer_et_sauver(id_conv, texte, reponse, conv_url) -> bool:
    """Envoie la réponse puis l'enregistre. True si envoyée (message traité)."""
    logger.info(f"💬 Réponse: {reponse[:80]}...")

    # Envoyer sur Leboncoin (on passe aussi le texte acheteur pour la reading_pause)
    success = await lbc.send_message(id_conv, reponse, conv_url=conv_url, buyer_text=texte)
    if not success:
        stats["erreurs"] += 1
        return False

    # Sauvegarder user + assistant SEULEMENT après envoi réussi
    # (si l'envoi échoue, rien n'est sauvé → le prochain scan retente)
    # Une seule transaction : pas d'état à moitié écrit si le processus s'arrête.
    if not await db.save_exchange(id_conv, texte, reponse):
        logger.error(
            f"❌ Réponse envoyée à conv {id_conv} mais NON enregistrée en base "
            f"(risque de doublon au prochain scan)."
        )
        stats["erreurs"] += 1
    stats["messages_traites"] += 1
    return True


async def _traiter_conversation(msg) -> bool:
    """Traite un message. True si une réponse a été envoyée, False sinon."""
    id_conv = msg["id_conv"]
    texte = msg["texte"]
    conv_url = msg.get("conv_url")
    logger.info(f"📩 Conv {id_conv}: {texte[:80]}...")

    # ── Vérifier si on a déjà traité ce message ──
    existing_msgs, _nb_envois = await db.get_messages(id_conv)
    # Skip SEULEMENT si : même texte ET on a déjà répondu (assistant après user)
    if _deja_repondu(existing_msgs, texte):
        logger.info(f"⏭️ Déjà répondu à ce message, skip conv {id_conv}")
        return False

    reponse, categorie = await _choisir_reponse(msg, id_conv, texte)
    if not reponse:
        logger.warning(f"⏭️ Pas de réponse LLM pour conv {id_conv}")
        stats["erreurs"] += 1
        lbc._noter_echec(id_conv)   # tentative ratée : bornée par MAX_TENTATIVES
        return False

    reponse = _valider_ou_remplacer(reponse, categorie, texte, id_conv)
    if reponse is None:
        return False

    return await _envoyer_et_sauver(id_conv, texte, reponse, conv_url)


async def _scan_et_repondre() -> bool:
    nouveaux = await lbc.get_new_messages()
    stats["scans"] += 1
    traite = False

    for i, msg in enumerate(nouveaux):
        if shutdown_event.is_set():
            logger.info("🛑 Arrêt demandé : on interrompt le traitement des conversations.")
            break
        if i >= MAX_CONVS_PER_SCAN:
            logger.info(f"⏸️ Limite de {MAX_CONVS_PER_SCAN} convs/scan atteinte, les {len(nouveaux) - i} restantes attendront le prochain scan.")
            break
        if await _traiter_conversation(msg):
            traite = True

    return traite


def _heartbeat(etat):
    """Écrit le heartbeat (erreurs d'écriture ignorées)."""
    ecrire_heartbeat(HEARTBEAT_FILE, stats, etat)


async def main():
    global db, lbc
    setup_logging()

    # Configuration cohérente avant toute ouverture de Chrome
    try:
        valider_config()
    except ValueError as e:
        logger.error(f"❌ {e}")
        return 2

    # Heures de pause nocturne valides (0-23, entières) avant toute ouverture de Chrome
    valider_heures(NIGHT_START_HOUR, NIGHT_END_HOUR)

    # ── Un seul bot à la fois sur le profil Chrome ───────────────────────────
    # Un second programme sur ce profil ne peut pas ouvrir Chrome : il
    # ouvrirait des fenêtres « about:blank » et perturberait le bot en cours.
    if not instance.acquerir():
        pid = instance.detenteur()
        logger.error(
            f"❌ Un autre bot utilise déjà le profil Chrome"
            + (f" (PID {pid})" if pid else "") + " → arrêt."
        )
        return

    try:
        db = ConversationManager()
        lbc = LeboncoinClient()
        await _run()
    finally:
        await _cleanup()


async def _cleanup():
    """Fermeture unique : chaque ressource dans son propre try/except."""
    logger.info("🧹 Nettoyage en cours...")
    await executer_nettoyage([
        ("lbc.close", lbc.close if lbc else (lambda: None)),
        ("close_session", close_session),
        ("db.close", db.close if db else (lambda: None)),
        ("instance.liberer", instance.liberer),
    ])
    logger.info(
        f"👋 Bot arrêté. Bilan: {stats['messages_traites']} messages traités, "
        f"{stats['erreurs']} erreurs, {stats['scans']} scans"
    )


async def _run():
    # Arrêt propre sur Ctrl+C / SIGTERM
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("🛑 Arrêt demandé (Ctrl+C)...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows : le handler s'exécute hors de la boucle → on y revient
            # de façon thread-safe (Event.set n'est pas thread-safe).
            signal.signal(
                sig, lambda s, f: loop.call_soon_threadsafe(_signal_handler))

    # Démarrage
    await init_session()  # Session HTTP partagée
    await lbc.start()

    # Vérifie la connexion. Si un login ou un captcha est requis, on met le bot
    # en pause en attendant l'intervention humaine (jamais de résolution auto).
    if not await lbc.ensure_ready():
        logger.error("❌ Session non connectée après attente. Arrêt du bot.")
        return

    # ── Mode contrôlé : un seul scan puis arrêt (utile pour tester) ──
    if "--once" in sys.argv:
        logger.info("▶️ Mode --once : un seul scan, puis arrêt.")
        try:
            await process()
        except Exception as e:
            logger.error(f"❌ Erreur scan unique: {e}", exc_info=True)
        logger.info("🧹 Mode --once terminé.")
        return

    logger.info(f"🚀 Bot démarré — comportement humain activé (attente aléatoire + pause nocturne {NIGHT_START_HOUR}h-{NIGHT_END_HOUR}h)")

    idle_scans = 0        # scans réellement exécutés ET vides, consécutifs
    erreurs_consecutives = 0

    while not shutdown_event.is_set():
        # ── Pause nocturne vérifiée AVANT le scan ──
        now = datetime.now()
        if en_pause_nocturne(now, NIGHT_START_HOUR, NIGHT_END_HOUR):
            reveil = prochain_reveil(now, NIGHT_END_HOUR)
            duree_sec = (reveil - now).total_seconds()
            logger.info(f"🌙 Pause nocturne activée. Le bot dort jusqu'à {NIGHT_END_HOUR}h00 ({int(duree_sec/60)} min restantes)...")
            _heartbeat("pause_nocturne")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=duree_sec)
            except asyncio.TimeoutError:
                pass
            continue  # re-check l'heure au réveil

        scan_vide = False   # True seulement si un scan a vraiment tourné et trouvé 0 message
        en_erreur = False
        try:
            if not await lbc.ensure_alive():
                logger.error("❌ Navigateur indisponible, nouvelle tentative au prochain cycle.")
                en_erreur = True
            else:
                scan_vide = not await process()
        except Exception as e:
            logger.error(f"❌ Erreur boucle principale: {e}", exc_info=True)
            stats["erreurs"] += 1
            en_erreur = True

        if en_erreur:
            erreurs_consecutives += 1
        else:
            erreurs_consecutives = 0

        # Heartbeat de fin de cycle (scan terminé ou en erreur)
        _heartbeat("erreur" if en_erreur else "ok")

        # ── Système de repos (idle) : seuls les scans vides comptent ──
        if not en_erreur:
            if scan_vide:
                idle_scans += 1
                if idle_scans >= IDLE_SCAN_LIMIT:
                    logger.info(f"💤 {IDLE_SCAN_LIMIT} scans consécutifs sans message. Pause repos de {IDLE_REST_SECONDS // 60} min...")
                    _heartbeat("repos")
                    await _attendre(IDLE_REST_SECONDS)
                    idle_scans = 0
                    continue
            else:
                idle_scans = 0

        if en_erreur:
            duree_sec = delai_backoff(erreurs_consecutives, SCAN_MAX)
            logger.warning(f"⏳ {erreurs_consecutives} erreur(s) consécutive(s) : attente de {int(duree_sec)}s.")
        else:
            duree_sec = tirer_intervalle(SCAN_MIN, SCAN_MAX)
            logger.debug(f"Attente aléatoire de {duree_sec}s avant le prochain scan")
        await _attendre(duree_sec)


async def _attendre(secondes):
    """Attend `secondes` ou jusqu'à l'arrêt demandé (interruptible)."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=secondes)
    except asyncio.TimeoutError:
        pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
