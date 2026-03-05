import os
import json
import random
import logging
import asyncio
from playwright.async_api import async_playwright
from config import HEADLESS, DEBUG_SCREENSHOTS
import human as H

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
COOKIES_FILE = "cookies.json"
SESSION_FILE = "session.json"
CDP_URL = "http://localhost:9222"


def _parse_netscape_cookies(path: str) -> list:
    """Parse un fichier de cookies au format Netscape → liste de dicts Playwright."""
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
    """Détermine si le fichier est en JSON ou Netscape."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_char = f.read(1)
            return first_char in ("[", "{")
    except Exception:
        return False


class LeboncoinClient:
    def __init__(self):
        self._pw = None
        self.browser = None
        self.page = None
        self._is_cdp = False
        self._shutdown = False  # flag pour arrêter le scan en cours

    async def start(self):
        """Lance Playwright (CDP prioritaire, sinon Chromium contrôlé)."""
        self._pw = await async_playwright().start()

        try:
            self.browser = await self._pw.chromium.connect_over_cdp(CDP_URL)
            self._is_cdp = True
            logger.info("🟢 Connecté au vrai Chrome via CDP (fingerprint réel)")
            contexts = self.browser.contexts
            if contexts:
                context = contexts[0]
                pages = context.pages
                self.page = pages[0] if pages else await context.new_page()
            else:
                context = await self.browser.new_context()
                self.page = await context.new_page()
            return
        except Exception:
            logger.info("⚪ Chrome CDP non disponible — lancement Chromium contrôlé")

        self.browser = await self._pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )
        storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
        context = await self.browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            storage_state=storage,
        )
        self.page = await context.new_page()

        if os.path.exists(COOKIES_FILE):
            if _is_json_cookies(COOKIES_FILE):
                with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                await context.add_cookies(raw)
                logger.info(f"{len(raw)} cookies JSON injectés")
            else:
                cookies = _parse_netscape_cookies(COOKIES_FILE)
                if cookies:
                    await context.add_cookies(cookies)
            logger.info("✅ Session chargée depuis cookies.json")

        logger.info("Navigateur Playwright démarré")

    def session_exists(self) -> bool:
        return os.path.exists(SESSION_FILE) or os.path.exists(COOKIES_FILE)

    async def save_session(self):
        try:
            await self.page.context.storage_state(path=SESSION_FILE)
            logger.info(f"Session sauvegardée dans {SESSION_FILE}")
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder la session: {e}")

    async def login(self):
        """Login MANUEL : attend que l'utilisateur se connecte (max 3 minutes)."""
        logger.info("🔐 Connexion manuelle requise — navigateur ouvert...")
        await self.page.goto("https://www.leboncoin.fr/messages")

        print("\n" + "=" * 55)
        print("  👤 CONNEXION MANUELLE REQUISE")
        print("  1. Glissez le CAPTCHA si demandé")
        print("  2. Entrez votre email → cliquez Continuer")
        print("  3. Entrez votre mot de passe → validez")
        print("  Le bot reprend automatiquement une fois connecté.")
        print("=" * 55 + "\n")

        MAX_WAIT = 180
        POLL = 2
        for elapsed in range(0, MAX_WAIT, POLL):
            url = self.page.url
            if (
                "/messages" in url
                and "login" not in url
                and "account" not in url
                and "connexion" not in url
            ):
                logger.info(f"✅ Connecté ! ({elapsed}s) URL : {url}")
                await self.save_session()
                print("\n✅ Connexion détectée — session sauvegardée, le bot démarre !\n")
                return
            await asyncio.sleep(POLL)

        raise RuntimeError("⏱️ Timeout : connexion non effectuée en 3 minutes")

    async def debug_dump_page(self, label: str = "debug"):
        """Sauvegarde screenshot + HTML brut dans le dossier debugs/."""
        import re as _re
        safe = _re.sub(r'[^a-z0-9_]', '_', label.lower())
        os.makedirs("debugs", exist_ok=True)
        try:
            await self.page.screenshot(path=f"debugs/debug_{safe}.png", full_page=True)
            html = await self.page.content()
            with open(f"debugs/debug_{safe}.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"📸 Debug dump : debugs/debug_{safe}.png + .html")
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder le debug dump: {e}")

    async def get_new_messages(self) -> list:
        """
        Détecte les conversations avec des messages non lus et en extrait le dernier message.
        Utilise le badge Spark de Leboncoin pour l'unread, et JavaScript pour lire les bulles.
        """
        conversations = []
        self._shutdown = False

        try:
            await self.page.goto("https://www.leboncoin.fr/messages")
            await H.wait_page_load(self.page)

            # Vérifier connexion
            current_url = self.page.url
            if "login" in current_url or "account" in current_url or "connexion" in current_url:
                logger.warning("⚠️ Session expirée — en attente de connexion manuelle...")
                await self.login()
                return []

            logger.info(f"📄 Page chargée : {current_url}")

            # ── Trouver les conversations NON LUES via le badge Spark ──
            # Le badge est : <span data-spark-component="badge" title="N message(s) non lu(s)">
            # Il est DANS un parent qui contient aussi le <a href="/messages/id/UUID">
            urls_non_lues = await self.page.evaluate("""() => {
                const badges = document.querySelectorAll('span[data-spark-component="badge"]');
                const urls = [];
                for (const badge of badges) {
                    const title = badge.getAttribute('title') || '';
                    const label = badge.getAttribute('aria-label') || '';
                    if (!title.includes('non lu') && !label.includes('non lu')) continue;
                    
                    // Remonter dans le DOM pour trouver le lien de conversation
                    let parent = badge.parentElement;
                    for (let i = 0; i < 10 && parent; i++) {
                        const link = parent.querySelector('a[href*="/messages/id/"]');
                        if (link) {
                            urls.push(link.getAttribute('href'));
                            break;
                        }
                        parent = parent.parentElement;
                    }
                }
                return urls;
            }""")

            if not urls_non_lues:
                # Fallback : compter les conversations pour le log
                total = await self.page.evaluate("""() => {
                    return document.querySelectorAll('a[href*="/messages/id/"]').length;
                }""")
                logger.info(f"✅ {total} conversation(s), aucune non lue")
                return []

            logger.info(f"📬 {len(urls_non_lues)} conversation(s) non lue(s)")

            # ── Visiter chaque conversation non lue ──
            for url in urls_non_lues:
                if self._shutdown:
                    logger.info("⏹️ Scan interrompu (shutdown)")
                    break
                try:
                    full_url = url if url.startswith("http") else f"https://www.leboncoin.fr{url}"
                    await self.page.goto(full_url)
                    await H.wait_page_load(self.page)

                    # Extraire le dernier message de L'ACHETEUR via JavaScript
                    # En mode conversation : PAS de sidebar, plein écran
                    # Messages acheteur = à gauche, nos messages = à droite (centrés)
                    dernier = await self.page.evaluate("""() => {
                        const allElements = document.querySelectorAll('p, span, div');
                        const candidates = [];
                        const vpWidth = window.innerWidth;
                        
                        for (const el of allElements) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width < 30 || rect.height < 10) continue;
                            // Zone des messages : entre le header (~100px) et le footer (~150px du bas)
                            if (rect.top < 100 || rect.top > window.innerHeight - 150) continue;
                            
                            const text = el.innerText?.trim();
                            if (!text || text.length < 2 || text.length > 1000) continue;
                            
                            const role = el.getAttribute('role') || '';
                            if (role === 'button' || role === 'navigation') continue;
                            
                            // Exclure les timestamps ("il y a X min")
                            if (text.match(/^il y a \d+/i)) continue;
                            
                            // Élément feuille uniquement
                            const childText = Array.from(el.children)
                                .map(c => c.innerText?.trim() || '')
                                .join('');
                            if (childText.length > 0 && childText === text) continue;
                            
                            // Position : centre horizontal de l'élément
                            const centerX = rect.left + rect.width / 2;
                            
                            // Acheteur = aligné à gauche (centre < 40% du viewport)
                            // Vendeur = aligné à droite (centre > 60% du viewport)
                            const isBuyer = centerX < vpWidth * 0.4;
                            
                            if (isBuyer) {
                                candidates.push({
                                    text: text,
                                    top: rect.top
                                });
                            }
                        }
                        
                        if (candidates.length === 0) return null;
                        
                        // Le plus bas = dernier message de l'acheteur
                        candidates.sort((a, b) => b.top - a.top);
                        
                        for (const c of candidates) {
                            if (c.text.length >= 2 && c.text.length <= 500) {
                                return c.text;
                            }
                        }
                        return candidates[0]?.text || null;
                    }""")




                    if dernier:
                        conv_url = self.page.url
                        id_conv = conv_url.rstrip("/").split("/")[-1]
                        conversations.append({
                            "id_conv": id_conv,
                            "conv_url": conv_url,
                            "texte": dernier.strip(),
                            "url": url,
                        })
                        logger.info(f"💬 Conv {id_conv}: {dernier[:80]}...")
                    else:
                        logger.warning(f"⚠️ Aucun message trouvé pour {url}")
                        if DEBUG_SCREENSHOTS:
                            await self.debug_dump_page(f"no_msg_{url.split('/')[-1]}")

                except Exception as e:
                    logger.warning(f"Erreur lecture conversation {url}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Erreur scan messages: {e}")
            if DEBUG_SCREENSHOTS:
                try:
                    await self.debug_dump_page("scan_error")
                except Exception:
                    pass

        return conversations

    async def send_message(self, id_conv: str, texte: str, conv_url: str = None) -> bool:
        """Envoie un message dans une conversation."""
        try:
            target = conv_url if conv_url else f"https://www.leboncoin.fr/messages/id/{id_conv}"
            await self.page.goto(target)
            await H.arrive_on_page(self.page)

            champ = await self.page.query_selector('textarea[placeholder]')
            if not champ:
                logger.error(f"Conv {id_conv}: champ texte non trouvé")
                return False

            await H.human_click(self.page, champ)
            await H.pause(0.3, 0.8)

            try:
                await H.human_type(champ, texte)
            except Exception as e:
                logger.warning(f"Erreur frappe humaine, fallback fill: {e}")
                await champ.fill(texte)

            await H.pause(0.5, 1.5)

            btn = (
                await self.page.query_selector('[data-qa-id="message-send"]')
                or await self.page.query_selector('button[aria-label="Envoyer mon message"]')
                or await self.page.query_selector('button[title="Envoyer mon message"]')
                or await self.page.query_selector('button[type="submit"]')
            )
            if not btn:
                logger.error(f"Conv {id_conv}: bouton envoi non trouvé")
                return False

            await H.human_click(self.page, btn)
            await H.pause(1.0, 2.5)
            logger.info(f"Message envoyé ✅ conv {id_conv}")
            # Revenir à la liste des messages
            await self.page.goto("https://www.leboncoin.fr/messages")
            return True

        except Exception as e:
            logger.error(f"Erreur envoi message conv {id_conv}: {e}")
            return False

    async def close(self):
        """Ferme proprement le navigateur."""
        self._shutdown = True  # arrêter le scan en cours
        try:
            if self._is_cdp:
                logger.info("Mode CDP — déconnexion sans fermer Chrome")
            elif self.browser:
                await self.browser.close()
            if self._pw:
                await self._pw.stop()
            logger.info("Navigateur fermé proprement")
        except Exception as e:
            logger.warning(f"Erreur fermeture navigateur: {e}")
