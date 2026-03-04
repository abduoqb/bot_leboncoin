import os
import json
import random
import logging
import asyncio
from playwright.async_api import async_playwright
from config import LBC_EMAIL, LBC_PASSWORD, HEADLESS, DEBUG_SCREENSHOTS

logger = logging.getLogger(__name__)

# User-agent réaliste pour anti-détection
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
COOKIES_FILE = "cookies.json"
SESSION_FILE = "session.json"


def _parse_netscape_cookies(path: str) -> list:
    """
    Parse un fichier de cookies au format Netscape (exporté par EditThisCookie, etc.)
    et retourne une liste de dicts compatibles Playwright.
    """
    cookies = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, path_c, secure, expires, name, value = parts[:7]
                cookie = {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path_c,
                    "secure": secure.upper() == "TRUE",
                    "httpOnly": False,
                }
                try:
                    exp = int(expires)
                    if exp > 0:
                        cookie["expires"] = exp
                except ValueError:
                    pass
                cookies.append(cookie)
        logger.info(f"{len(cookies)} cookies chargés depuis {path}")
    except Exception as e:
        logger.error(f"Erreur lecture cookies Netscape: {e}")
    return cookies


def _is_json_cookies(path: str) -> bool:
    """Détermine si le fichier est en JSON (format Playwright) ou Netscape."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_char = f.read(1)
            return first_char == "["
    except Exception:
        return False


async def _random_delay(min_s: float = 1.5, max_s: float = 3.5):
    """Délai aléatoire pour paraître humain."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


class LeboncoinClient:
    def __init__(self):
        self._pw = None
        self.browser = None
        self.page = None

    async def start(self):
        """Lance le navigateur Playwright avec anti-détection."""
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )

        # Priorité : cookies.json > session.json > pas de session
        storage = None
        if os.path.exists(SESSION_FILE):
            storage = SESSION_FILE

        context = await self.browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            storage_state=storage,
        )
        self.page = await context.new_page()

        # Injecter les cookies du navigateur réel si cookies.json présent
        if os.path.exists(COOKIES_FILE):
            if _is_json_cookies(COOKIES_FILE):
                # Format JSON brut (liste de cookies)
                with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                await context.add_cookies(raw)
                logger.info(f"{len(raw)} cookies JSON injectés depuis {COOKIES_FILE}")
            else:
                # Format Netscape (EditThisCookie, etc.)
                cookies = _parse_netscape_cookies(COOKIES_FILE)
                if cookies:
                    await context.add_cookies(cookies)
            logger.info("✅ Session chargée depuis cookies.json — aucun login requis")

        logger.info("Navigateur Playwright démarré")

    def session_exists(self) -> bool:
        """Vérifie si une session sauvegardée (session.json ou cookies.json) existe."""
        return os.path.exists(SESSION_FILE) or os.path.exists(COOKIES_FILE)

    async def save_session(self):
        """Sauvegarde la session courante dans session.json (pour les prochains lancements)."""
        try:
            await self.page.context.storage_state(path=SESSION_FILE)
            logger.info(f"Session sauvegardée dans {SESSION_FILE}")
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder la session: {e}")

    async def login(self):
        """Connexion email/password (fallback si pas de cookies.json)."""
        try:
            logger.info("Connexion email/password à Leboncoin...")
            await self.page.goto("https://www.leboncoin.fr/account/login")
            await _random_delay(2, 4)

            await self.page.fill('input[name="email"]', LBC_EMAIL)
            await _random_delay(0.5, 1.5)
            await self.page.fill('input[name="password"]', LBC_PASSWORD)
            await _random_delay(0.5, 1.5)

            await self.page.click('button[type="submit"]')
            await self.page.wait_for_timeout(4000)

            await self.save_session()
            logger.info("Session sauvegardée ✅")
        except Exception as e:
            logger.error(f"Erreur login: {e}")
            raise

    async def debug_dump_page(self, label: str = "debug"):
        """Sauvegarde screenshot + HTML brut pour identifier les vrais sélecteurs."""
        import re
        safe = re.sub(r'[^a-z0-9_]', '_', label.lower())
        try:
            await self.page.screenshot(path=f"debug_{safe}.png", full_page=True)
            html = await self.page.content()
            with open(f"debug_{safe}.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"📸 Debug dump sauvegardé : debug_{safe}.png + debug_{safe}.html")
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder le debug dump: {e}")

    async def get_new_messages(self) -> list:
        """
        Retourne la liste des nouveaux messages non lus.
        Chaque élément : {"id_conv": str, "texte": str, "url": str}
        """
        conversations = []

        try:
            await self.page.goto("https://www.leboncoin.fr/mes-messages")
            await _random_delay(2, 4)

            # Vérifier qu'on est bien connecté (redirection vers login = échec)
            current_url = self.page.url
            if "login" in current_url or "account" in current_url:
                logger.error("❌ Redirigé vers la page de login — cookies invalides ou expirés")
                await self.debug_dump_page("login_redirect")
                return []

            logger.info(f"📄 Page chargée : {current_url}")

            # --- Étape 1 : collecter les URLs des conversations non lues ---
            # Sélecteurs possibles (Leboncoin change son DOM régulièrement)
            SELECTORS_ITEM = [
                '[data-testid="conversation-item"]',
                'a[href*="/mes-messages/"]',
                '[class*="conversation"] a',
                'li[class*="conversation"]',
                'article[class*="conversation"]',
            ]
            SELECTORS_UNREAD = [
                '[data-testid="unread-badge"]',
                '[class*="unread"]',
                '[class*="badge"]',
                '[aria-label*="non lu"]',
                '[class*="dot"]',
            ]
            SELECTORS_BUBBLE = [
                '[data-testid="message-bubble"]',
                '[class*="message"] p',
                '[class*="bubble"]',
                '[class*="MessageContent"]',
                '[class*="messageText"]',
            ]

            # Trouver le bon sélecteur pour les items de conversation
            items = []
            used_item_selector = None
            for sel in SELECTORS_ITEM:
                items = await self.page.query_selector_all(sel)
                if items:
                    used_item_selector = sel
                    logger.info(f"✅ Sélecteur items : '{sel}' → {len(items)} élément(s)")
                    break

            if not items:
                logger.warning("⚠️ Aucun sélecteur de conversation ne fonctionne")
                if DEBUG_SCREENSHOTS:
                    await self.debug_dump_page("no_items_found")
                    logger.info("Ouvre debug_no_items_found.png pour voir la page réelle")
                return []

            # Collecter les URLs non lues
            urls_non_lues = []
            for item in items:
                # Chercher un badge non-lu
                badge = None
                for sel_badge in SELECTORS_UNREAD:
                    badge = await item.query_selector(sel_badge)
                    if badge:
                        break

                if badge:
                    # L'item lui-même peut être un <a> ou contenir un <a>
                    href = await item.get_attribute("href")
                    if not href:
                        a_tag = await item.query_selector("a[href]")
                        if a_tag:
                            href = await a_tag.get_attribute("href")
                    if href and "/mes-messages/" in href:
                        urls_non_lues.append(href)

            if not urls_non_lues:
                logger.info(f"✅ {len(items)} conversation(s) trouvée(s), aucune non lue")
                return []

            logger.info(f"📬 {len(urls_non_lues)} conversation(s) non lue(s) détectée(s)")

            # --- Étape 2 : naviguer vers chaque conversation ---
            for url in urls_non_lues:
                try:
                    full_url = url if url.startswith("http") else f"https://www.leboncoin.fr{url}"
                    await self.page.goto(full_url)
                    await _random_delay(1.5, 3)

                    # Trouver le bon sélecteur pour les bulles de messages
                    msgs = []
                    for sel_bubble in SELECTORS_BUBBLE:
                        msgs = await self.page.query_selector_all(sel_bubble)
                        if msgs:
                            logger.debug(f"Sélecteur bulles : '{sel_bubble}'")
                            break

                    if msgs:
                        dernier = await msgs[-1].inner_text()
                        id_conv = url.rstrip("/").split("/")[-1]
                        conversations.append({
                            "id_conv": id_conv,
                            "texte": dernier.strip(),
                            "url": url,
                        })
                        logger.info(f"💬 Conv {id_conv}: {dernier[:80]}...")
                    else:
                        logger.warning(f"⚠️ Aucune bulle trouvée pour {url}")
                        if DEBUG_SCREENSHOTS:
                            await self.debug_dump_page(f"no_bubble_{url.split('/')[-1]}")

                except Exception as e:
                    logger.warning(f"Erreur lecture conversation {url}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Erreur scan messages: {e}")
            if DEBUG_SCREENSHOTS:
                await self.debug_dump_page("scan_error")

        return conversations

    async def send_message(self, id_conv: str, texte: str) -> bool:
        """Envoie un message dans une conversation. Retourne True si succès."""
        try:
            await self.page.goto(f"https://www.leboncoin.fr/mes-messages/{id_conv}")
            await _random_delay(1.5, 3)

            champ = await self.page.query_selector('textarea[placeholder]')
            if not champ:
                logger.error(f"Conv {id_conv}: champ texte non trouvé")
                return False

            await champ.fill(texte)
            await _random_delay(0.5, 1.5)

            btn = await self.page.query_selector('button[type="submit"]')
            if not btn:
                logger.error(f"Conv {id_conv}: bouton envoi non trouvé")
                return False

            await btn.click()
            await _random_delay(1, 2)
            logger.info(f"Message envoyé ✅ conv {id_conv}")
            return True

        except Exception as e:
            logger.error(f"Erreur envoi message conv {id_conv}: {e}")
            return False

    async def close(self):
        """Ferme proprement le navigateur."""
        try:
            if self.browser:
                await self.browser.close()
            if self._pw:
                await self._pw.stop()
            logger.info("Navigateur fermé proprement")
        except Exception as e:
            logger.warning(f"Erreur fermeture navigateur: {e}")
