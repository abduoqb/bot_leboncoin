"""
leboncoin.py — Client Playwright pour Leboncoin.

Principes (calibrés sur le DOM réel du site) :
  - Navigateur = VRAI Chrome avec profil PERSISTANT (chrome_profile/).
    → empreinte authentique (principal levier anti-DataDome)
    → session conservée : plus besoin de se reconnecter à chaque lancement.
  - On NE navigue JAMAIS directement vers /messages/id/<uuid> (deep-link
    fortement protégé par DataDome) : on ouvre la liste /messages puis on
    CLIQUE la conversation comme un humain.
  - Sélecteurs stables (attributs data-*) :
      liste   : a[href*="/messages/id/"]
      bulle   : [data-qa-id="message-bubble"]   (entrant = rounded-bl-none,
                                                 sortant = rounded-br-none)
      champ   : [data-spark-component="textarea"]
      envoyer : [data-qa-id="message-send"]
  - Si un captcha DataDome apparaît : on tente d'abord un glissement
    automatique « humain » (captcha.py) ; en cas d'échec, on met le bot
    en pause et on demande à l'humain de le glisser.
"""

import os
import sys
import json
import time
import random
import re
import logging
import asyncio

from playwright.async_api import async_playwright

from config import (
    HEADLESS,
    DEBUG_SCREENSHOTS,
    BROWSER_MODE,
    PROFILE_DIR,
    CHROME_CHANNEL,
    CDP_URL,
    CAPTCHA_WAIT_SECONDS,
    RESTART_COOLDOWN_SECONDS,
    MAX_RESTART_ESSAIS,
    CAPTCHA_FENETRE_SECONDS,
    CAPTCHA_MAX_PAR_HEURE,
    CAPTCHA_PAUSE_LONGUE_SECONDS,
    CAPTCHA_AUTO_MAX_PAR_HEURE,
    MAX_CONVS_PER_SCAN,
    LIST_SCROLL_MAX_STEPS,
    LIST_SCROLL_STABLE,
    LIST_SCROLL_EVERY_MIN,
    LIST_SCROLL_EVERY_MAX,
)
import human as H
from state import _norm_texte, _fil_contient_envoi, id_conv_de  # noqa: F401
from js import (  # noqa: F401  (ré-export : leboncoin.JS_LIST, etc.)
    LBC_HOME, LBC_MESSAGES,
    CONV_LINK, BUBBLE, TEXTAREA, SEND_BTN,
    JS_STATE, JS_LIST, JS_LIST_BOX, JS_THREAD, JS_THREAD_READY,
)

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# État persistant : ce que le bot a déjà vu dans la liste. Évite de
# re-parcourir tous les fils à chaque redémarrage.
_STATE_FILE = os.path.join(_BASE_DIR, "bot_state.json")

# Nombre de tentatives (envoi raté / fil qui ne s'ouvre pas) avant d'abandonner
# une conversation. Sans borne, un fil cassé serait rouvert à chaque scan.
MAX_TENTATIVES = 3
_STATE_VERSION = 2


def _profil_occupe(erreur: BaseException) -> bool:
    """Le lancement a-t-il échoué parce que le profil Chrome est DÉJÀ ouvert ?

    Un profil Chrome ne peut être ouvert qu'une fois. Chrome ne refuse pas :
    il ouvre une nouvelle fenêtre « about:blank » dans la session existante et
    le processus lancé se termine aussitôt → Playwright perd la connexion.
    Le message exact (« existing browser session » / sa traduction) n'apparaît
    que dans les journaux du navigateur, inclus dans l'erreur Playwright.
    """
    s = str(erreur)
    return (
        "existing browser session" in s
        or "session de navigateur existante" in s
    )


# Sélecteurs et scripts JS : définis dans js.py (ré-exportés ici, API inchangée).


class LeboncoinClient:
    def __init__(self):
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._is_cdp = False
        self._shutdown = False
        self._current_thread_url = None
        self._init_captcha_state()

        # ── Antirebond de redémarrage ────────────────────────────────────────
        # Si Chrome refuse de démarrer (profil déjà ouvert), chaque tentative
        # ouvre une fenêtre « about:blank » de plus : on espace les essais.
        self._echecs_redemarrage = 0
        self._dernier_redemarrage = 0.0

        # ── État de veille ───────────────────────────────────────────────────
        # Le bot n'ouvre un fil QUE s'il a une raison de le faire : ouvrir un
        # fil le marque « lu » côté serveur et recharge la page. Boucler sur
        # des conversations déjà lues est inutile et très repérable.
        self._last_previews = {}   # href -> aperçu déjà vu (persisté)
        self._pending = {}         # id_conv -> tentatives restantes (envoi raté)
        self._ignorees = set()     # id_conv où NOUS sommes l'acheteur
        self._settled = set()      # id_conv inspectés, plus rien à répondre
        self._scans_avant_defilement = 0   # 0 = la prochaine lecture défile la liste
        self._load_state()

    # ── État persistant ──────────────────────────────────────────────────────

    def _load_state(self):
        """Recharge la mémoire (survit aux redémarrages).

        Format v2 : {"version": 2, "previews", "pending", "ignorees", "settled"}.
        Ancien format (sans version) : {"previews"} seul → toujours lisible.
        """
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("racine non-objet")
            previews = data.get("previews") or {}
            if isinstance(previews, dict):
                self._last_previews = {str(k): str(v) for k, v in previews.items()}
            pending = data.get("pending") or {}
            if isinstance(pending, dict):
                for k, v in pending.items():
                    try:
                        self._pending[str(k)] = max(0, int(v))
                    except (TypeError, ValueError):
                        logger.warning("Etat: compteur 'pending' invalide ignore (remis a 0)", exc_info=True)
                        self._pending[str(k)] = 0
            for cle, cible in (("ignorees", self._ignorees), ("settled", self._settled)):
                val = data.get(cle) or []
                if isinstance(val, list):
                    cible.update(str(x) for x in val)
            logger.info(f"💾 Mémoire : {len(self._last_previews)} aperçu(s), "
                        f"{len(self._pending)} en attente, {len(self._ignorees)} ignorée(s), "
                        f"{len(self._settled)} réglée(s)")
        except FileNotFoundError:
            logger.debug("Etat: aucun fichier d'etat (premier lancement)")
        except Exception as e:
            logger.warning(f"Mémoire illisible ({e}) → on repart de zéro.")

    def _save_state(self):
        """Écriture atomique : fichier .tmp puis os.replace (jamais de JSON tronqué)."""
        tmp = _STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "version": _STATE_VERSION,
                    "previews": self._last_previews,
                    "pending": self._pending,
                    "ignorees": sorted(self._ignorees),
                    "settled": sorted(self._settled),
                }, f, ensure_ascii=False, indent=0)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as _err:
                    logger.debug("Etat: fsync du fichier temporaire impossible : %s", type(_err).__name__)
            os.replace(tmp, _STATE_FILE)
        except Exception as e:
            logger.warning(f"Sauvegarde de la mémoire impossible : {e}")
            try:
                os.remove(tmp)
            except OSError as _err:
                logger.debug("Etat: suppression du fichier temporaire impossible : %s", type(_err).__name__)

    def _set_preview(self, href: str, preview: str):
        """Enregistre un aperçu en ordre LRU (pop puis réinsertion en fin)."""
        self._last_previews.pop(href, None)
        self._last_previews[href] = preview

    def _noter_echec(self, id_conv: str):
        """Une tentative RÉELLE (lecture ou envoi) a échoué : on la compte."""
        self._pending[id_conv] = self._pending.get(id_conv, 0) + 1
        self._save_state()

    def _trim_state(self, garde: int = 400, garde_ensembles: int = 3000):
        """Borne la taille de la mémoire (LRU : on oublie les moins récents)."""
        if len(self._last_previews) > garde * 1.5:
            for k in list(self._last_previews)[:len(self._last_previews) - garde]:
                del self._last_previews[k]
        for ens in (self._settled, self._ignorees):
            if len(ens) > garde_ensembles * 1.5:
                for k in list(ens)[:len(ens) - garde_ensembles]:
                    ens.discard(k)

    # ── Démarrage ────────────────────────────────────────────────────────────

    async def start(self):
        """Lance le navigateur (profil persistant par défaut, CDP en option)."""
        self._pw = await async_playwright().start()

        if BROWSER_MODE == "cdp":
            if await self._start_cdp():
                return
            logger.warning("CDP indisponible → repli sur le profil persistant")

        await self._start_profile()

    async def _start_cdp(self) -> bool:
        try:
            self.browser = await self._pw.chromium.connect_over_cdp(CDP_URL)
            self._is_cdp = True
            self.context = (
                self.browser.contexts[0]
                if self.browser.contexts
                else await self.browser.new_context()
            )
            pages = self.context.pages
            self.page = pages[0] if pages else await self.context.new_page()
            logger.info("🟢 Connecté à Chrome via CDP")
            return True
        except Exception as e:
            logger.info(f"Connexion CDP indisponible : {e}")
            return False

    async def _start_profile(self):
        os.makedirs(PROFILE_DIR, exist_ok=True)
        kwargs = dict(
            user_data_dir=os.path.abspath(PROFILE_DIR),
            headless=HEADLESS,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
            locale="fr-FR",
            no_viewport=True,
        )
        if CHROME_CHANNEL:
            kwargs["channel"] = CHROME_CHANNEL
        try:
            self.context = await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            if _profil_occupe(e):
                # Le repli sur Chromium ne réglerait rien : le profil est en
                # cause, pas le navigateur. Et il ouvrirait une fenêtre
                # « about:blank » DE PLUS dans la session déjà ouverte.
                logger.error(
                    f"❌ Le profil Chrome {PROFILE_DIR} est déjà ouvert "
                    f"(ton Chrome, ou une autre instance du bot). Ferme-le : "
                    f"un profil ne peut être utilisé que par un seul Chrome."
                )
                raise
            logger.warning(f"Impossible de lancer « {CHROME_CHANNEL} » ({e}) → Chromium")
            kwargs.pop("channel", None)
            try:
                self.context = await self._pw.chromium.launch_persistent_context(**kwargs)
            except Exception as e2:
                if _profil_occupe(e2):
                    logger.error(
                        f"❌ Le profil Chrome {PROFILE_DIR} est déjà ouvert. "
                        f"Ferme la fenêtre Chrome qui l'utilise avant de relancer."
                    )
                raise

        self.page = (
            self.context.pages[0] if self.context.pages
            else await self.context.new_page()
        )
        logger.info(f"🟢 Chrome persistant démarré (profil : {PROFILE_DIR})")

    def session_exists(self) -> bool:
        """Le profil contient-il déjà des cookies (donc une connexion probable) ?"""
        cookies = os.path.join(PROFILE_DIR, "Default", "Cookies")
        return os.path.exists(cookies)

    async def ensure_alive(self) -> bool:
        """Vérifie que le navigateur répond encore ; le relance sinon.

        Rend le bot autonome face à un crash/fermeture du navigateur pendant
        une longue session (le profil persistant conserve la connexion).
        """
        try:
            if self.page and not self.page.is_closed():
                await self.page.evaluate("() => 1")
                return True
        except Exception as _err:
            logger.debug("Ping de la page echoue (page consideree comme morte) : %s", type(_err).__name__)

        # Antirebond : si les redémarrages viennent d'échouer plusieurs fois
        # d'affilée (profil Chrome déjà ouvert, navigateur récalcitrant), on
        # n'essaie plus à chaque cycle. Chaque tentative rate en ouvrant une
        # fenêtre « about:blank » supplémentaire : mieux vaut attendre.
        if (self._echecs_redemarrage >= MAX_RESTART_ESSAIS
                and time.monotonic() - self._dernier_redemarrage < RESTART_COOLDOWN_SECONDS):
            reste = int(RESTART_COOLDOWN_SECONDS
                        - (time.monotonic() - self._dernier_redemarrage))
            logger.warning(
                f"⏳ Navigateur indisponible ({self._echecs_redemarrage} échecs) → "
                f"nouvelle tentative dans {reste}s. Vérifie qu'aucune autre "
                f"fenêtre Chrome n'utilise « {PROFILE_DIR} »."
            )
            return False

        logger.warning("⚠️ Navigateur/page indisponible → redémarrage automatique.")
        self._dernier_redemarrage = time.monotonic()
        try:
            await self.close()
        except Exception as _err:
            logger.debug("Fermeture du navigateur avant redemarrage echouee : %s", type(_err).__name__)
        # Réinitialise l'état VOLATIL seulement : la mémoire des aperçus reste
        # valable (elle est persistée), inutile de tout re-parcourir.
        self._shutdown = False
        self._is_cdp = False
        self._current_thread_url = None
        # (_pending / _ignorees / _settled sont persistés : on ne les vide pas,
        # sinon une réponse en attente serait perdue ou doublée.)
        try:
            await self.start()
            self._echecs_redemarrage = 0
            return True
        except Exception as e:
            self._echecs_redemarrage += 1
            logger.error(f"❌ Redémarrage du navigateur impossible : {e}")
            # start() peut échouer APRÈS avoir démarré Playwright : sans ce
            # nettoyage, chaque cycle laisserait un process de plus derrière lui.
            try:
                await self.close()
            except Exception as _err:
                logger.debug("Fermeture du navigateur apres echec de demarrage echouee : %s", type(_err).__name__)
            return False

    # ── Compteurs captcha (fenêtre glissante, pause longue) ───────────────────

    def _init_captcha_state(self):
        self._captcha_times = []       # horodatages (monotonic) des captchas vus
        self._auto_times = []          # horodatages des glissements automatiques
        self._pause_captcha_jusqu = 0.0
        self._scan_bloque = False      # le scan courant doit s'arrêter (captcha/connexion)
        self._captcha_fil = False      # un captcha a été vu dans le fil en lecture

    @staticmethod
    def _purger(liste: list, now: float):
        limite = now - CAPTCHA_FENETRE_SECONDS
        liste[:] = [t for t in liste if t > limite]

    def _en_pause_captcha(self, now: float = None) -> bool:
        """Pause longue en cours ? À son terme, les compteurs sont remis à zéro."""
        now = time.monotonic() if now is None else now
        if not self._pause_captcha_jusqu:
            return False
        if now >= self._pause_captcha_jusqu:
            self._pause_captcha_jusqu = 0.0
            self._captcha_times.clear()
            self._auto_times.clear()
            return False
        return True

    def _noter_captcha(self, now: float = None) -> bool:
        """Compte un captcha dans la fenêtre glissante. Renvoie True si le seuil
        est dépassé (la pause longue est alors armée)."""
        now = time.monotonic() if now is None else now
        self._purger(self._captcha_times, now)
        self._captcha_times.append(now)
        n = len(self._captcha_times)
        logger.info(f"🧩 Captcha à {time.strftime('%H:%M:%S')} "
                    f"({n}/{CAPTCHA_MAX_PAR_HEURE} dans la fenêtre)")
        if n > CAPTCHA_MAX_PAR_HEURE:
            self._pause_captcha_jusqu = now + CAPTCHA_PAUSE_LONGUE_SECONDS
            logger.error(
                f"🛑 {n} captchas en {CAPTCHA_FENETRE_SECONDS // 60} min : pause longue de "
                f"{CAPTCHA_PAUSE_LONGUE_SECONDS // 60} min (aucun scan), pour ne pas "
                f"aggraver le blocage.")
            return True
        return False

    def _auto_autorise(self, now: float = None) -> bool:
        """Un glissement automatique de plus est-il permis dans la fenêtre ?"""
        now = time.monotonic() if now is None else now
        self._purger(self._auto_times, now)
        return len(self._auto_times) < CAPTCHA_AUTO_MAX_PAR_HEURE

    # ── Détection captcha / connexion ─────────────────────────────────────────

    async def _state(self) -> dict:
        """Lit l'état de la page. En cas de navigation en cours, on considère
        la page comme NON prête (évite de conclure « ok » à tort)."""
        try:
            return await self.page.evaluate(JS_STATE)
        except Exception:
            logger.warning("Lecture de l'etat de la page impossible (captcha/login suppose)", exc_info=True)
            return {"url": self.page.url, "captcha": True, "login": True}

    async def _goto(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logger.warning(f"Navigation vers {url} : {e}")
        await asyncio.sleep(random.uniform(2.0, 3.5))

    async def _wait_human(self, raison: str) -> bool:
        """Captcha / connexion.

        1) On tente d'abord un glissement automatique du slider DataDome
           (comportement humain, cf. captcha.py).
        2) En cas d'échec seulement, on met le bot en pause et on attend
           une intervention humaine.
        """
        est_captcha = "captcha" in raison.lower()
        if est_captcha:
            if self._en_pause_captcha():
                logger.warning("⏸️ Pause longue anti-captcha en cours : aucune action.")
                self._scan_bloque = True
                return False
            try:
                from captcha import is_captcha_present, solve_slider
                # On ne compte / tente le glissement QUE si un vrai captcha est
                # affiché (sinon, ex. page de connexion, on attend l'humain).
                if await is_captcha_present(self.page):
                    if self._noter_captcha():
                        self._scan_bloque = True
                        return False
                    if not self._auto_autorise():
                        logger.warning(
                            f"🧩 Déjà {CAPTCHA_AUTO_MAX_PAR_HEURE} glissements automatiques "
                            f"sur la fenêtre : attente humaine directe.")
                    else:
                        self._auto_times.append(time.monotonic())
                        if await solve_slider(self.page):
                            logger.info("🤖 Captcha franchi automatiquement.")
                            if "/messages" not in self.page.url:
                                await self._goto(LBC_MESSAGES)
                            return True
            except Exception as e:
                logger.warning(f"Résolution auto du captcha impossible : {e}")

        if HEADLESS:
            logger.error(
                f"❌ {raison} : navigateur HEADLESS, personne ne peut intervenir. "
                f"Passe HEADLESS=False dans config.py pour pouvoir résoudre à la main.")
            self._scan_bloque = True
            return False

        logger.warning(f"⏸️ {raison} — intervention humaine requise.")
        print("\n" + "=" * 62)
        print("  ⚠️  ACTION HUMAINE DEMANDÉE")
        print(f"  Raison : {raison}")
        print("  → Dans la fenêtre Chrome ouverte :")
        print("      • glisse le captcha si présent")
        print("      • connecte-toi si demandé")
        print(f"  Le bot reprend tout seul (max {CAPTCHA_WAIT_SECONDS // 60} min).")
        print("=" * 62 + "\n")

        last_log = -15
        for elapsed in range(0, CAPTCHA_WAIT_SECONDS, 3):
            if self._shutdown:
                self._scan_bloque = True
                return False
            st = await self._state()
            if not st.get("login") and not st.get("captcha"):
                if "/messages" not in st.get("url", ""):
                    await self._goto(LBC_MESSAGES)
                logger.info(f"✅ Intervention humaine détectée ({elapsed}s) : {st.get('url')}")
                return True
            if elapsed - last_log >= 15:
                logger.info(f"⏳ Toujours en attente ({elapsed}s/{CAPTCHA_WAIT_SECONDS}s)…")
                last_log = elapsed
            await asyncio.sleep(3)

        logger.error("❌ Délai dépassé : aucune intervention humaine détectée.")
        self._scan_bloque = True
        return False

    async def ensure_ready(self) -> bool:
        """Se rend sur /messages et s'assure que l'on est connecté."""
        await self._goto(LBC_HOME)
        await H.arrive_on_page(self.page)
        await self._goto(LBC_MESSAGES)

        st = await self._state()
        if st.get("captcha"):
            if not await self._wait_human("Captcha DataDome détecté"):
                return False
        elif st.get("login"):
            if not await self._wait_human("Connexion à Leboncoin requise"):
                return False

        st = await self._state()
        ok = not st.get("captcha") and not st.get("login")
        if ok:
            logger.info(f"✅ Connecté — page prête : {st.get('url')}")
        return ok

    # ── Navigation vers la liste / ouverture d'un fil ─────────────────────────

    async def _ensure_on_messages(self) -> bool:
        """S'assure d'être sur la LISTE (pas dans un fil)."""
        # Navigation « dispersée » : on passe parfois par l'accueil (réalisme anti-bot)
        if random.random() < 0.4:
            await self._goto(LBC_HOME)
        if "/messages" not in self.page.url or "/messages/id/" in self.page.url:
            await self._goto(LBC_MESSAGES)
        else:
            await self.page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 3.5))

        st = await self._state()
        if st.get("captcha"):
            if not await self._wait_human("Captcha DataDome sur la liste"):
                return False
        elif st.get("login"):
            if not await self._wait_human("Connexion à Leboncoin requise (liste)"):
                return False
        else:
            return True
        # Après résolution : on revient sur la liste et on re-vérifie
        await self._goto(LBC_MESSAGES)
        st = await self._state()
        ok = not st.get("captcha") and not st.get("login")
        if not ok:
            self._scan_bloque = True
        return ok

    async def _load_all_conversations(self, cible: str = None) -> dict:
        """Lit la liste en la faisant défiler, comme le ferait une personne.

        Leboncoin n'affiche qu'environ 7 conversations tant qu'on n'a pas
        défilé. Les liens vus à chaque geste sont CUMULÉS par href : si la page
        recycle ses éléments, ceux déjà passés ne sont pas perdus.

        On s'arrête quand plusieurs descentes de suite n'amènent rien de neuf,
        au plafond de gestes, ou dès que `cible` (href) est apparue.
        Retourne le même format que JS_LIST (url + items).
        """
        data = await self.page.evaluate(JS_LIST)
        trouves = {}

        def fusionner(items):
            avant = len(trouves)
            for it in items:
                if it.get("href"):
                    trouves.setdefault(it["href"], it)
            return len(trouves) > avant

        fusionner(data.get("items", []))
        stables = 0
        try:
            for etape in range(LIST_SCROLL_MAX_STEPS):
                if cible and cible in trouves:
                    break
                box = await self.page.evaluate(JS_LIST_BOX)
                if not box:
                    break
                sens = await H.human_list_scroll(self.page, box, allow_up=etape > 0)
                nouveau = fusionner((await self.page.evaluate(JS_LIST)).get("items", []))
                if sens == "down":
                    stables = 0 if nouveau else stables + 1
                    if stables >= LIST_SCROLL_STABLE:
                        break
        except Exception as e:
            logger.debug(f"Défilement de la liste interrompu : {e}")

        logger.debug(f"📜 Liste défilée : {len(trouves)} conversation(s) vues")
        return {"url": data.get("url"), "items": list(trouves.values())}

    def _defilement_du_scan(self) -> bool:
        """Faut-il défiler la liste à ce scan ? (un scan sur N, N aléatoire)"""
        if self._scans_avant_defilement > 0:
            self._scans_avant_defilement -= 1
            return False
        self._scans_avant_defilement = random.randint(
            LIST_SCROLL_EVERY_MIN, LIST_SCROLL_EVERY_MAX) - 1
        return True

    async def _open_thread(self, conv_url: str) -> bool:
        """Ouvre un fil en CLIQUANT le lien depuis la liste (jamais de deep-link)."""
        if (
            self._current_thread_url
            and self._current_thread_url.rstrip("/") == conv_url.rstrip("/")
            and await self.page.query_selector(BUBBLE)
        ):
            return True

        if not await self._ensure_on_messages():
            return False

        href = conv_url
        if href.startswith("http"):
            href = "/messages/id/" + id_conv_de(href)

        loc = self.page.locator(f'a[href="{href}"]').first
        try:
            # La liste vient d'être rechargée : le lien peut être au-delà des
            # premières conversations affichées → on défile jusqu'à le voir.
            if await loc.count() == 0:
                await self._load_all_conversations(cible=href)
            await loc.scroll_into_view_if_needed(timeout=8000)
            await H.pause(0.3, 0.9)
        except Exception as e:
            logger.warning(f"⏹️ Conversation introuvable dans la liste ({href}) : {e}")
            return False

        if not await H.human_click(self.page, loc):
            logger.warning(f"⏹️ Clic sur la conversation impossible ({href}) — fil ignoré.")
            return False

        id_conv = id_conv_de(href)
        try:
            # Le fil est prêt dès que l'URL correspond ET qu'il contient soit une
            # bulle de message, soit un message automatique leboncoin (offre,
            # rappel de disponibilité…) qui, lui, n'est PAS une bulle.
            await self.page.wait_for_function(
                JS_THREAD_READY,
                arg=id_conv,
                timeout=15000,
            )
        except Exception:
            if DEBUG_SCREENSHOTS:
                await self.debug_dump_page(f"thread_no_load_{id_conv}")
            logger.warning(f"⏹️ Le fil {id_conv} ne s'est pas affiché.")
            return False

        self._current_thread_url = f"https://www.leboncoin.fr/messages/id/{id_conv}"
        await H.pause(0.6, 1.4)
        return True

    # ── Lecture des messages ─────────────────────────────────────────────────

    def _candidats(self, items: list) -> list:
        """Fils qu'il est LÉGITIME d'ouvrir, d'après la liste seule.

        Ouvrir un fil recharge une page et le marque « lu » côté serveur :
        on n'ouvre donc QUE s'il y a une raison.

          • l'aperçu a changé (ou n'a jamais été vu) → nouveau message ;
          • le fil est non lu et jamais inspecté depuis le démarrage ;
          • une réponse est en attente (envoi raté) → on retente, mais un
            nombre de fois BORNÉ (sinon un fil cassé boucle à l'infini).

        Tout le reste est laissé tranquille : c'est ce qui évite de relire
        en boucle les conversations déjà traitées.
        """
        todo = []
        for it in items:
            href = it["href"]
            id_conv = id_conv_de(href)
            preview = it["preview"]

            change = self._last_previews.get(href) != preview

            if id_conv in self._ignorees:
                self._set_preview(href, preview)      # décision définitive
                continue                              # on est l'acheteur ici

            if id_conv in self._pending:
                if self._pending[id_conv] >= MAX_TENTATIVES:
                    logger.warning(
                        f"⚠️ Conv {id_conv} : {MAX_TENTATIVES} tentatives sans "
                        f"succès → abandonnée (elle reste dans la liste)."
                    )
                    del self._pending[id_conv]
                    self._settled.add(id_conv)
                    self._set_preview(href, preview)
                    continue
                # Le compteur ne bouge PAS ici : il compte les échecs réels
                # (voir _noter_echec), pas les scans ni les fils coupés par
                # MAX_CONVS_PER_SCAN.
                logger.debug(f"🔁 Conv {id_conv} : réponse en attente, nouvelle tentative")
                todo.append(it)
            elif change:
                todo.append(it)
            elif it["unread"] and id_conv not in self._settled:
                todo.append(it)
            else:
                logger.debug(f"⏭️ Conv {id_conv} : rien de nouveau, fil non ouvert")
                self._set_preview(href, preview)      # vu ET vérifié : rien à faire

            # ⚠️ Pour les fils envoyés à l'inspection, l'aperçu n'est enregistré
            # qu'APRÈS l'inspection (get_new_messages). Sinon, un fil coupé par
            # la limite de scan serait marqué « vu » sans avoir été lu, et il ne
            # serait plus jamais inspecté : un message d'acheteur se perdrait.

        self._trim_state()
        self._save_state()
        return todo

    async def _lister_conversations(self) -> list:
        """Lit la liste (avec défilement un scan sur N) et dédoublonne par href.
        Renvoie [] si la liste est illisible ou vide."""
        try:
            if self._defilement_du_scan():
                data = await self._load_all_conversations()
            else:
                data = await self.page.evaluate(JS_LIST)
        except Exception as e:
            logger.error(f"Lecture de la liste impossible : {e}")
            return []

        items, seen = [], set()
        for it in data.get("items", []):
            href = it.get("href")
            if not href or href in seen:
                continue
            seen.add(href)
            items.append(it)

        if not items:
            logger.info("✅ Aucune conversation dans la liste.")
        return items

    def _classer_fil(self, it: dict, id_conv: str, conv_url: str, thread):
        """Après lecture d'un fil : met à jour la mémoire (aperçu, ignorées,
        réglées, en attente) et renvoie la conversation à traiter, ou None."""
        # Le fil a bien été ouvert : l'aperçu est désormais « vu ». S'il n'y
        # a rien à répondre il ne sera plus rouvert ; s'il reste une réponse
        # en attente, `_pending` s'en charge (retentatives bornées).
        # ⚠️ Cet enregistrement ne doit PAS avoir lieu avant l'ouverture :
        # un fil coupé par la limite de scan doit rester candidat au scan
        # suivant, sinon le message qu'il contient est perdu.
        self._set_preview(it["href"], it["preview"])

        if thread is None:
            # Fil illisible (captcha, lien absent…) : échec RÉEL, compté ;
            # on retente au scan suivant, `_pending` borne les essais.
            self._noter_echec(id_conv)
            return None

        # Conversation où NOUS sommes l'acheteur (« À propos de ce vendeur ») :
        # le bot ne doit jamais répondre à la place du vendeur d'une AUTRE
        # annonce — et ne doit plus jamais la rouvrir.
        if thread.get("counterpart") == "vendeur":
            logger.info(f"⏭️ Conv {id_conv} : nous sommes l'acheteur → ignorée pour de bon.")
            self._ignorees.add(id_conv)
            self._pending.pop(id_conv, None)
            self._save_state()
            return None

        # Pré-réponses proposées par leboncoin (facultatives) : on les trace.
        qr = thread.get("quickReplies") or []
        if qr:
            logger.info("💡 Pré-réponse(s) leboncoin proposée(s) : "
                        + " | ".join(q[:60] for q in qr[:3]))

        # Offre reçue : message AUTOMATIQUE leboncoin → décision humaine.
        # On ne répond JAMAIS à la place de l'offre, mais un vrai message
        # d'acheteur présent dans le même fil reste traité normalement.
        if thread.get("hasOffer"):
            offre = (thread.get("systemTexts") or [""])[0][:140]
            logger.warning(
                f"💰 OFFRE reçue dans {id_conv} (message automatique) — aucune "
                f"réponse auto à l'offre, à traiter toi-même. « {offre} »"
            )

        if thread.get("lastSide") == "out":
            logger.debug(f"⏭️ Dernier message = nous, conv réglée ({id_conv})")
            self._settled.add(id_conv)
            self._pending.pop(id_conv, None)
            self._save_state()
            return None

        texte = thread.get("buyersText")
        if not texte:
            logger.debug(f"⏭️ Fil sans message d'acheteur ({id_conv})")
            self._settled.add(id_conv)
            self._pending.pop(id_conv, None)
            self._save_state()
            return None

        # Réponse à produire : elle reste « en attente » jusqu'à ce que
        # l'envoi soit confirmé (send_message la retire de _pending).
        # Compteur à 0 = aucun échec ; send_message/_noter_echec l'incrémente.
        self._pending.setdefault(id_conv, 0)
        self._save_state()

        conv = {
            "id_conv": id_conv,
            "conv_url": conv_url,
            "texte": texte,
            "unread": it["unread"],
            "quick_replies": thread.get("quickReplies") or [],
            "messages": thread.get("messages") or [],
            "fil_tronque": bool(thread.get("hasMoreAbove")),
        }
        logger.info(f"💬 Conv {id_conv}: {texte[:80]}…")
        return conv

    async def get_new_messages(self) -> list:
        """Renvoie les conversations dont le DERNIER message vient de l'acheteur."""
        conversations = []
        self._shutdown = False
        self._scan_bloque = False

        if self._en_pause_captcha():
            reste = max(0, int(self._pause_captcha_jusqu - time.monotonic()))
            logger.warning(f"⏸️ Pause anti-captcha : scan sauté (encore ~{reste // 60} min).")
            return []

        if not await self._ensure_on_messages():
            return []

        items = await self._lister_conversations()
        if not items:
            return []

        nb_non_lues = sum(1 for it in items if it["unread"])
        logger.info(f"📬 {len(items)} conversation(s), {nb_non_lues} non lue(s)")

        todo = self._candidats(items)
        if not todo:
            logger.info("😴 Rien à inspecter (aucun aperçu modifié, aucune non lue).")
            return []

        logger.info(f"🔎 {len(todo)} fil(s) à inspecter sur {len(items)}")

        max_inspect = MAX_CONVS_PER_SCAN + 3
        inspected = 0

        for it in todo:
            if self._shutdown:
                logger.info("⏹️ Scan interrompu (shutdown)")
                break
            if len(conversations) >= MAX_CONVS_PER_SCAN:
                logger.info(f"⏸️ Limite de {MAX_CONVS_PER_SCAN} réponses/scan atteinte.")
                break
            if inspected >= max_inspect:
                logger.info(f"⏸️ Limite de {max_inspect} fils inspectés/scan atteinte.")
                break

            href = it["href"]
            conv_url = (href if href.startswith("http")
                        else f"https://www.leboncoin.fr{href}")
            id_conv = id_conv_de(conv_url)

            inspected += 1
            self._captcha_fil = False
            thread = await self._read_thread(conv_url)

            # Captcha / connexion non résolu : on ARRÊTE le scan (sinon chaque
            # fil suivant redéclencherait un captcha). Ce n'est pas un échec du
            # fil : ni _noter_echec, ni aperçu « vu » → il reste candidat.
            if thread is None and self._scan_bloque:
                logger.error("🛑 Captcha/connexion non résolu : scan arrêté, "
                             "les fils restants seront relus au prochain scan.")
                break
            if thread is None and self._captcha_fil:
                logger.info(f"🧩 Captcha résolu pendant la lecture de {id_conv} : "
                            f"fil relu au prochain scan (pas un échec).")
                continue

            conv = self._classer_fil(it, id_conv, conv_url, thread)
            if conv:
                conversations.append(conv)

        self._save_state()
        return conversations

    async def _read_thread(self, conv_url: str, tries: int = 6):
        """Ouvre le fil et lit son contenu, en attendant que le DOM se stabilise
        (le SPA peut afficher brièvement l'ancien fil après le clic)."""
        if not await self._open_thread(conv_url):
            return None

        data, prev_sig, stable = None, object(), 0
        await H.pause(1.2, 2.0)  # laisse le fil se rendre complètement
        # Comportement humain : on parcourt le fil du regard (scroll) avant de lire
        if random.random() < 0.6:
            await H.human_scroll(self.page, "up", random.randint(50, 170))
            await H.pause(0.25, 0.7)
            await H.human_scroll(self.page, "down", random.randint(70, 220))
        if random.random() < 0.5:
            await H.stray_move(self.page, 1)
        for _ in range(tries):
            try:
                data = await self.page.evaluate(JS_THREAD)
            except Exception as e:
                logger.warning(f"Lecture du fil échouée : {e}")
                return None
            sig = (data.get("count"), data.get("scrollHeight"),
                   data.get("lastText"), data.get("hasOffer"))
            if sig == prev_sig:
                stable += 1
                if stable >= 2:      # 3 lectures identiques consécutives
                    break
            else:
                stable = 0
            prev_sig = sig
            await asyncio.sleep(0.8)

        st = await self._state()
        if st.get("captcha"):
            if DEBUG_SCREENSHOTS:
                await self.debug_dump_page(f"captcha_thread_{id_conv_de(conv_url)}")
            self._captcha_fil = True
            # Échec/timeout propagé via self._scan_bloque (posé par _wait_human).
            await self._wait_human("Captcha détecté dans une conversation")
            return None
        return data

    # ── Envoi ────────────────────────────────────────────────────────────────

    def _marquer_envoye(self, id_conv: str):
        self._pending.pop(id_conv, None)
        self._settled.add(id_conv)
        self._save_state()

    async def send_message(self, id_conv: str, texte: str,
                           conv_url: str = None, buyer_text: str = None) -> bool:
        """Envoie un message ; un échec est compté dans `_pending` (borné)."""
        ok = await self._send_message_inner(id_conv, texte, conv_url, buyer_text)
        if not ok:
            self._noter_echec(id_conv)
        return ok

    async def _lire_fil_courant(self):
        try:
            return await self.page.evaluate(JS_THREAD)
        except Exception:
            logger.warning("Lecture du fil de discussion impossible", exc_info=True)
            return None

    async def _send_message_inner(self, id_conv: str, texte: str,
                                  conv_url: str = None, buyer_text: str = None) -> bool:
        """Tape et envoie un message dans la conversation."""
        target = conv_url or f"https://www.leboncoin.fr/messages/id/{id_conv}"

        # Les sauts de ligne sont dangereux : « Entrée » valide l'envoi dans le chat.
        texte = " ".join(texte.split())
        if not texte:
            return False

        try:
            # Temps de « réflexion » proportionnel au message de l'acheteur
            await H.reading_pause(buyer_text or texte, wpm=150)

            if not (
                self._current_thread_url
                and self._current_thread_url.rstrip("/") == target.rstrip("/")
            ):
                if not await self._open_thread(target):
                    return False

            # Anti-doublon : une tentative précédente non confirmée est peut-être
            # passée. Si la dernière bulle est déjà notre texte, on n'envoie pas.
            if _fil_contient_envoi(await self._lire_fil_courant(), texte):
                logger.info(f"Conv {id_conv} : message déjà présent dans le fil → pas de renvoi")
                self._marquer_envoye(id_conv)
                return True

            champ = await self.page.query_selector(TEXTAREA)
            if not champ:
                logger.error(f"Conv {id_conv} : champ de saisie introuvable")
                return False

            await H.human_click(self.page, champ)
            await H.pause(0.3, 0.8)
            # Le champ peut déjà contenir quelque chose (pré-réponse leboncoin,
            # brouillon, texte précédent). On le VIDE pour ne jamais envoyer
            # un message mélangé.
            await self._clear_field(champ)
            try:
                await H.human_type(self.page, champ, texte)
            except Exception as e:
                logger.warning(f"Frappe humaine en échec, repli fill : {e}")
                await champ.fill(texte)
            await H.pause(0.5, 1.5)

            # Contrôle du contenu réellement présent dans le champ
            try:
                val = (await champ.input_value()).strip()
            except Exception as _err:
                logger.debug("Lecture du champ de saisie impossible : %s", type(_err).__name__)
                val = ""
            if val != texte.strip():
                logger.warning(
                    f"Conv {id_conv} : champ inattendu ({val[:60]!r}) → saisie directe"
                )
                await self._clear_field(champ)
                await champ.fill(texte)
                await H.pause(0.3, 0.7)

            btn = await self.page.query_selector(SEND_BTN)
            if not btn:
                logger.error(f"Conv {id_conv} : bouton d'envoi introuvable")
                return False

            # Le bouton reste désactivé tant que le champ est vide
            for _ in range(24):
                disabled = await btn.evaluate(
                    "b => b.disabled || b.getAttribute('aria-disabled') === 'true'"
                )
                if not disabled:
                    break
                await asyncio.sleep(0.25)

            state = await btn.evaluate("b => !!b.disabled || b.getAttribute('aria-disabled') === 'true'")
            if state:
                logger.error(f"Conv {id_conv} : bouton d'envoi toujours inactif → abandon")
                await self._clear_field(champ)
                return False

            # Nombre de bulles avant envoi (pour confirmer l'apparition d'une nouvelle)
            try:
                before = await self.page.evaluate(JS_THREAD)
                prev_count = (before or {}).get("count", 0)
            except Exception as _err:
                logger.debug("Lecture du compteur de messages avant envoi impossible : %s", type(_err).__name__)
                prev_count = 0

            await H.human_click(self.page, btn)
            await H.pause(0.8, 1.5)

            sent = await self._verify_sent(texte, prev_count)
            if not sent:
                # Avant de conclure à l'échec (et donc de permettre un renvoi),
                # on RELIT le fil : le message est peut-être bien parti.
                try:
                    await self._open_thread(target)
                except Exception:
                    logger.warning("Reouverture du fil apres echec d'envoi impossible", exc_info=True)
                if _fil_contient_envoi(await self._lire_fil_courant(), texte):
                    logger.info(f"Conv {id_conv} : envoi retrouvé à la relecture du fil")
                    sent = True
            if sent:
                self._marquer_envoye(id_conv)
                logger.info(f"Message envoyé ✅ conv {id_conv}")
            else:
                logger.warning(f"⚠️ Conv {id_conv} : envoi non confirmé")

            # Retour à l'accueil (navigation dispersée anti-bot)
            try:
                await self._goto(LBC_HOME)
                self._current_thread_url = None
            except Exception as _err:
                logger.debug("Retour a l'accueil apres envoi echoue : %s", type(_err).__name__)
            return sent

        except Exception as e:
            logger.error(f"Erreur envoi message conv {id_conv} : {e}")
            return False

    async def _clear_field(self, champ):
        """Vide le champ de saisie (pré-réponse, brouillon, texte précédent…)."""
        try:
            await champ.click(timeout=5000)
            mod = "Meta" if sys.platform == "darwin" else "Control"
            await self.page.keyboard.press(f"{mod}+A")
            await self.page.keyboard.press("Delete")
            await H.pause(0.1, 0.3)
        except Exception as e:
            logger.debug(f"Vidage du champ impossible : {e}")

    async def _verify_sent(self, snippet: str, prev_count: int = 0) -> bool:
        """Confirme l'envoi par l'apparition d'une NOUVELLE bulle sortante
        contenant notre texte.

        Volontairement STRICT : on ne déduit pas l'envoi du simple vidage du
        champ (le champ peut se vider alors que l'envoi a échoué, ce qui
        produirait un faux « envoyé » et perdrait le message).
        """
        if not _norm_texte(snippet):
            return False
        for _ in range(30):  # ~15 s
            try:
                data = await self.page.evaluate(JS_THREAD)
            except Exception as _err:
                logger.debug("Lecture du fil pendant la verification d'envoi echouee (nouvel essai) : %s", type(_err).__name__)
                await asyncio.sleep(0.5)
                continue
            # Texte COMPLET normalisé (typographie, insécables), pas un préfixe ;
            # et la bulle sortante doit être nouvelle (count a augmenté) ou, si le
            # compteur est indisponible (prev_count == 0), simplement identique.
            if _fil_contient_envoi(data, snippet):
                if data.get("count", 0) > prev_count or prev_count == 0:
                    return True
            await asyncio.sleep(0.5)
        return False

    # ── Debug / arrêt ────────────────────────────────────────────────────────

    async def debug_dump_page(self, label: str = "debug"):
        safe = re.sub(r"[^a-z0-9_]", "_", label.lower())
        dbg = os.path.join(_BASE_DIR, "debugs")
        os.makedirs(dbg, exist_ok=True)
        try:
            await self.page.screenshot(
                path=os.path.join(dbg, f"debug_{safe}.png"), full_page=True)
            with open(os.path.join(dbg, f"debug_{safe}.html"), "w", encoding="utf-8") as f:
                f.write(await self.page.content())
            logger.info(f"📸 Debug dump : debugs/debug_{safe}.png + .html")
        except Exception as e:
            logger.warning(f"Debug dump impossible : {e}")

    async def close(self):
        self._shutdown = True
        try:
            if self._is_cdp:
                logger.info("Mode CDP — déconnexion sans fermer Chrome")
            elif self.context:
                await self.context.close()
            if self._pw:
                await self._pw.stop()
            logger.info("Navigateur fermé proprement")
        except Exception as e:
            logger.warning(f"Erreur fermeture navigateur : {e}")
