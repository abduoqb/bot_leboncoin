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
import random
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
)
import human as H

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LBC_HOME = "https://www.leboncoin.fr/"
LBC_MESSAGES = "https://www.leboncoin.fr/messages"

CONV_LINK = 'a[href*="/messages/id/"]'
BUBBLE = '[data-qa-id="message-bubble"]'
TEXTAREA = '[data-spark-component="textarea"]'
SEND_BTN = '[data-qa-id="message-send"]'

# ── JS ───────────────────────────────────────────────────────────────────────

# État de la page : captcha DataDome ? page de connexion ?
JS_STATE = r"""() => {
  const body = (document.body && document.body.innerText) || '';
  return {
    url: location.href,
    captcha: !!document.querySelector(
        'iframe[src*="captcha-delivery"], iframe[title*="CAPTCHA"], #cmsg')
      || /Faites glisser vers la droite|s'adresse bien . vous|un robot est sur le m/i.test(body),
    login: !!document.querySelector('input[type="password"]')
      || /auth\.leboncoin\.fr/.test(location.href),
  };
}"""

# Liste des conversations + détection du non-lu + aperçu du dernier message
JS_LIST = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const badgeOf = a => {
    let p = a;
    for (let i = 0; i < 6 && p; i++) {
      if (p.querySelectorAll('a[href*="/messages/id/"]').length > 1) break;
      const b = p.querySelector('[data-spark-component="badge"]');
      if (b) {
        const aria = b.getAttribute('aria-label') || '';
        const title = b.getAttribute('title') || '';
        const txt = clean(b.innerText);
        if (aria || title || txt) return (aria + ' ' + title + ' ' + txt).toLowerCase();
      }
      p = p.parentElement;
    }
    return '';
  };
  return {
    url: location.href,
    items: [...document.querySelectorAll('a[href*="/messages/id/"]')].map(a => {
      const raw = clean(a.innerText);
      return {
        href: a.getAttribute('href'),
        raw: raw.slice(0, 220),
        preview: raw.replace(/\s*-\s*il y a .*$/i, '').trim(),
        unread: /non lu|notification/.test(badgeOf(a)),
      };
    }),
  };
}"""

# Fil de discussion : bulles + côté (entrant / sortant) + messages automatiques
JS_THREAD = r"""() => {
  const clean = s => (s || '').replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();

  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    // Seuil volontairement bas : une bulle courte fait ~20x37px
    if (r.width < 20 || r.height < 8) return false;
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };

  // Conteneur du fil = <ol> scrollable VISIBLE qui contient le plus de bulles.
  // (le SPA laisse parfois l'ancien fil dans le DOM : on ne veut que le visible)
  let container = null, best = -1;
  for (const o of document.querySelectorAll('ol')) {
    if (!visible(o) || !/auto|scroll/.test(getComputedStyle(o).overflowY)) continue;
    const n = o.querySelectorAll('[data-qa-id="message-bubble"]').length;
    if (n >= best) { best = n; container = o; }
  }
  const root = container || document;

  const sideOf = b => {
    const cls = b.className || '';
    if (cls.includes('rounded-br-none')) return 'out';
    if (cls.includes('rounded-bl-none')) return 'in';
    const p = b.parentElement;
    if (p) {
      const j = getComputedStyle(p).justifyContent;
      if (j === 'flex-end') return 'out';
      if (j === 'flex-start') return 'in';
    }
    if (container) {
      const br = b.getBoundingClientRect(), or = container.getBoundingClientRect();
      return (br.left + br.width / 2) > (or.left + or.width / 2) ? 'out' : 'in';
    }
    return 'in';
  };

  const nodes = [...root.querySelectorAll('[data-qa-id="message-bubble"]')].filter(visible);
  const all = nodes.map(b => ({ text: clean(b.innerText), side: sideOf(b) }));
  const last = all.length ? all[all.length - 1] : null;

  // Suite finale de messages de l'ACHETEUR (non répondus)
  const trailingIn = [];
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].side !== 'in') break;
    trailingIn.unshift(all[i].text);
  }

  // Messages AUTOMATIQUES leboncoin = éléments du fil SANS bulle de message.
  // On en distingue :
  //   • l'en-tête « À propos de cet acheteur / ce vendeur » (donne NOTRE rôle) ;
  //   • le bandeau « Envoyez une réponse rapide » (pré-réponses proposées) ;
  //   • les vrais messages système (offre reçue, disponibilité…).
  const sysTexts = [];
  const quickReplies = [];
  let counterpart = null;
  if (container) {
    for (const li of container.children) {
      if (li.querySelector('[data-qa-id="message-bubble"]')) continue;
      const t = clean(li.innerText);
      if (!t) continue;
      if (/^À propos de cet acheteur/i.test(t)) { counterpart = 'acheteur'; continue; }
      if (/^À propos de ce vendeur/i.test(t)) { counterpart = 'vendeur'; continue; }
      if (/Envoyez une réponse rapide/i.test(t)) {
        const chips = [...li.querySelectorAll('[data-spark-component="chip"]')]
          .map(c => clean(c.innerText)).filter(Boolean);
        if (chips.length) quickReplies.push(...chips);
        else {
          const s = t.replace(/^Envoyez une réponse rapide\s*/i, '').trim();
          if (s) quickReplies.push(s);
        }
        continue;
      }
      if (t.length < 400) sysTexts.push(t);
    }
  }

  // Une offre se reconnaît UNIQUEMENT dans les messages automatiques du fil
  // (jamais dans les bulles : notre propre réponse contient le mot « offre »,
  // et la bannière cookies contient un bouton « Accepter »).
  const sysJoined = sysTexts.join('\n');
  const hasOffer = /Offre reçue|Vous avez\s+\d+\s*heures?\s+pour accepter|Accepter\s+\d+\s*€/i
    .test(sysJoined);

  return {
    url: location.href,
    count: all.length,
    scrollHeight: container ? container.scrollHeight : 0,
    lastSide: last ? last.side : null,
    lastText: last ? last.text : null,
    buyersText: (last && last.side === 'in' && trailingIn.length)
      ? trailingIn.join('\n') : null,
    hasOffer,
    counterpart,
    quickReplies: quickReplies.slice(0, 5),
    systemTexts: sysTexts.slice(0, 5),
  };
}"""


class LeboncoinClient:
    def __init__(self):
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._is_cdp = False
        self._shutdown = False
        self._current_thread_url = None
        self._last_previews = {}   # href -> aperçu normalisé (détection de nouveauté)
        self._answered = set()     # id_conv déjà répondus (évite de les rouvrir)

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
            logger.warning(f"Impossible de lancer « {CHROME_CHANNEL} » ({e}) → Chromium")
            kwargs.pop("channel", None)
            self.context = await self._pw.chromium.launch_persistent_context(**kwargs)

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
        except Exception:
            pass

        logger.warning("⚠️ Navigateur/page indisponible → redémarrage automatique.")
        try:
            await self.close()
        except Exception:
            pass
        # Réinitialise l'état volatil (le serveur redevient la source de vérité)
        self._shutdown = False
        self._is_cdp = False
        self._current_thread_url = None
        self._last_previews.clear()
        self._answered.clear()
        try:
            await self.start()
            return True
        except Exception as e:
            logger.error(f"❌ Redémarrage du navigateur impossible : {e}")
            return False

    # ── Détection captcha / connexion ─────────────────────────────────────────

    async def _state(self) -> dict:
        """Lit l'état de la page. En cas de navigation en cours, on considère
        la page comme NON prête (évite de conclure « ok » à tort)."""
        try:
            return await self.page.evaluate(JS_STATE)
        except Exception:
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
        if "captcha" in raison.lower():
            try:
                from captcha import is_captcha_present, solve_slider
                # On ne tente le glissement QUE si un vrai captcha est affiché
                # (sinon, ex. page de connexion, on doit attendre l'humain).
                if await is_captcha_present(self.page):
                    if await solve_slider(self.page):
                        logger.info("🤖 Captcha franchi automatiquement.")
                        if "/messages" not in self.page.url:
                            await self._goto(LBC_MESSAGES)
                        return True
            except Exception as e:
                logger.warning(f"Résolution auto du captcha impossible : {e}")

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
        return not st.get("captcha") and not st.get("login")

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
            href = "/messages/id/" + href.rstrip("/").split("/")[-1]

        loc = self.page.locator(f'a[href="{href}"]').first
        try:
            await loc.scroll_into_view_if_needed(timeout=8000)
            await H.pause(0.3, 0.9)
        except Exception as e:
            logger.warning(f"⏹️ Conversation introuvable dans la liste ({href}) : {e}")
            return False

        if not await H.human_click(self.page, loc):
            logger.warning(f"⏹️ Clic sur la conversation impossible ({href}) — fil ignoré.")
            return False

        id_conv = href.split("/")[-1]
        try:
            # Le fil est prêt dès que l'URL correspond ET qu'il contient soit une
            # bulle de message, soit un message automatique leboncoin (offre,
            # rappel de disponibilité…) qui, lui, n'est PAS une bulle.
            await self.page.wait_for_function(
                """(id) => location.pathname.includes(id) && (
                    document.querySelector('[data-qa-id="message-bubble"]')
                    || /Message automatique leboncoin|Offre reçue|s'intéresse à votre annonce/i
                         .test(document.body.innerText)
                )""",
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

    async def get_new_messages(self) -> list:
        """Renvoie les conversations dont le DERNIER message vient de l'acheteur."""
        from config import MAX_CONVS_PER_SCAN
        conversations = []
        self._shutdown = False

        if not await self._ensure_on_messages():
            return []

        try:
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
            return []

        nb_non_lues = sum(1 for it in items if it["unread"])
        logger.info(f"📬 {len(items)} conversation(s), {nb_non_lues} non lue(s)")

        # Candidats : non-lus, aperçu modifié/jamais vu, ou fil auquel on n'a
        # pas encore répondu. `_answered` n'empêche PAS de détecter un aperçu
        # modifié (un nouveau message sans badge doit rester visible).
        todo = []
        for it in items:
            id_conv = it["href"].rstrip("/").split("/")[-1]
            if it["unread"]:
                self._answered.discard(id_conv)
            preview_changed = (
                it["href"] not in self._last_previews
                or self._last_previews[it["href"]] != it["preview"]
            )
            if it["unread"] or preview_changed or id_conv not in self._answered:
                todo.append(it)

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
            id_conv = conv_url.rstrip("/").split("/")[-1]

            inspected += 1
            thread = await self._read_thread(conv_url)

            if thread is None:
                continue

            # Conversation où NOUS sommes l'acheteur (« À propos de ce vendeur ») :
            # le bot ne doit jamais répondre à la place du vendeur d'une AUTRE annonce.
            if thread.get("counterpart") == "vendeur":
                logger.info(f"⏭️ Conv {id_conv} : nous sommes l'acheteur → ignorée.")
                self._last_previews[href] = it["preview"]
                continue

            # Pré-réponses proposées par leboncoin (facultatives) : on les trace.
            qr = thread.get("quickReplies") or []
            if qr:
                logger.info(f"💡 Pré-réponse(s) leboncoin proposée(s) : "
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
                logger.debug(f"⏭️ Dernier message = nous, conv ignorée ({id_conv})")
                self._last_previews[href] = it["preview"]
                continue

            texte = thread.get("buyersText")
            if not texte:
                logger.debug(f"⏭️ Fil sans message d'acheteur ({id_conv})")
                self._last_previews[href] = it["preview"]
                continue

            # ⚠️ On NE marque PAS ce fil comme « vu » ici : s'il est renvoyé comme
            # candidat puis que l'envoi échoue, il doit être retenté au scan suivant.

            conversations.append({
                "id_conv": id_conv,
                "conv_url": conv_url,
                "texte": texte,
                "unread": it["unread"],
                "quick_replies": thread.get("quickReplies") or [],
            })
            logger.info(f"💬 Conv {id_conv}: {texte[:80]}…")

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
                await self.debug_dump_page(f"captcha_thread_{conv_url.split('/')[-1]}")
            await self._wait_human("Captcha détecté dans une conversation")
            return None
        return data

    # ── Envoi ────────────────────────────────────────────────────────────────

    async def send_message(self, id_conv: str, texte: str,
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
            except Exception:
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
            except Exception:
                prev_count = 0

            await H.human_click(self.page, btn)
            await H.pause(0.8, 1.5)

            sent = await self._verify_sent(texte, prev_count)
            if sent:
                self._answered.add(id_conv)
                logger.info(f"Message envoyé ✅ conv {id_conv}")
            else:
                logger.warning(f"⚠️ Conv {id_conv} : envoi non confirmé")

            # Retour à l'accueil (navigation dispersée anti-bot)
            try:
                await self._goto(LBC_HOME)
                self._current_thread_url = None
            except Exception:
                pass
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
        core = snippet.strip()[:40]
        for _ in range(30):  # ~15 s
            try:
                data = await self.page.evaluate(JS_THREAD)
            except Exception:
                await asyncio.sleep(0.5)
                continue
            if data and data.get("lastSide") == "out":
                lt = data.get("lastText") or ""
                if not core or core in lt:
                    return True
                # bulle sortante mais texte différent : on accepte seulement si
                # le nombre de bulles a réellement augmenté
                if data.get("count", 0) > prev_count:
                    return True
            await asyncio.sleep(0.5)
        return False

    # ── Debug / arrêt ────────────────────────────────────────────────────────

    async def debug_dump_page(self, label: str = "debug"):
        import re as _re
        safe = _re.sub(r"[^a-z0-9_]", "_", label.lower())
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
