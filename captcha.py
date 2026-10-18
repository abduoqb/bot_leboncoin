"""
captcha.py — Résolution « humaine » du slider DataDome de Leboncoin.

Leboncoin (protégé par DataDome) affiche parfois un captcha du type
« Faites glisser vers la droite » : une barre à faire glisser vers la droite
dans une iframe `captcha-delivery.com`.

Ce module tente de résoudre ce slider de façon HUMAINE :
  - trajectoire à easing (accélération puis décélération),
  - jitter vertical et léger tremblement,
  - léger dépassement puis correction avant relâchement,
  - hésitation après l'appui, maintien avant le relâchement,
  - durée totale ~0,75-1,4 s (cible constructeur DataDome : 0,6-1,4 s).

Succès détecté par (dans l'ordre de fiabilité) :
  1. le cookie `datadome` a CHANGÉ de valeur,
  2. l'iframe du captcha a disparu / la page a rechargé,
  3. plus aucune invite « glissez vers la droite ».

Si la résolution échoue, l'appelant DOIT retomber sur l'attente d'une
intervention humaine (jamais de boucle infinie).

Note : on agit ici sur le navigateur de l'utilisateur, pour SON propre compte
Leboncoin. Aucune tentative de contournement vis-à-vis d'un tiers.
"""

import asyncio
import logging
import random

logger = logging.getLogger(__name__)

# ── Sélecteurs (vérifiés sur DataDome) ───────────────────────────────────────
IFRAME_SEL = (
    'iframe[src*="captcha-delivery.com"]:not([src*="/interstitial"]), '
    'iframe[title="DataDome CAPTCHA"], iframe[title*="CAPTCHA" i]'
)
FRAME_URL_HINT = "captcha-delivery.com/captcha"

# Poignée à glisser : `#captcha-container .slider` est le seul sélecteur public
# vérifié. On garde des replis au cas où DataDome change son markup.
HANDLE_SELECTORS = [
    "#captcha-container .slider",
    "#captcha-container [class*='slider']",
    ".slider",
    "[class*='slider']",
    "[role='slider']",
    "[class*='handle']",
]


async def datadome_cookie(context) -> str | None:
    try:
        for c in await context.cookies():
            if c.get("name") == "datadome":
                return c.get("value")
    except Exception:
        pass
    return None


async def _banned(page) -> bool:
    """`dd.t == 'bv'` ⇒ IP bloquée : résoudre le captcha ne servira à rien."""
    try:
        return bool(await page.evaluate(
            "() => !!(window.dd && String(window.dd.t) === 'bv')"
        ))
    except Exception:
        return False


async def is_captcha_present(page) -> bool:
    """Détection légère du captcha (iframe, invite texte ou variable `dd`)."""
    try:
        if await page.query_selector(IFRAME_SEL):
            return True
    except Exception:
        pass
    try:
        return await page.evaluate(
            """() => {
                if (window.dd && String(window.dd.t) === 'c') return true;
                const b = document.body ? document.body.innerText : '';
                return /Faites glisser vers la droite|glissez vers la droite|un robot est sur ce site/i.test(b);
            }"""
        )
    except Exception:
        return False


async def _find_slider_frame(page, timeout_s: float = 12.0):
    """Attend et renvoie la frame du captcha portant une poignée visible."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        frames = list(page.frames)
        # 1) par URL (le plus déterministe)
        for fr in frames:
            if FRAME_URL_HINT in (fr.url or ""):
                if await _has_handle(fr):
                    logger.info(f"🌐 Frame captcha (url) : {fr.url[:80]}")
                    return fr
        # 2) par iframe
        try:
            el = await page.query_selector(IFRAME_SEL)
            if el:
                fr = await el.content_frame()
                if fr and await _has_handle(fr):
                    logger.info(f"🌐 Frame captcha (iframe) : {(fr.url or '')[:80]}")
                    return fr
        except Exception:
            pass
        # 3) n'importe quelle frame qui contient une poignée
        for fr in frames:
            if "captcha" in (fr.url or "").lower() and await _has_handle(fr):
                return fr
        await asyncio.sleep(0.4)
    return None


async def _has_handle(frame) -> bool:
    for sel in HANDLE_SELECTORS:
        try:
            loc = frame.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


async def _find_handle(frame):
    """Renvoie (locator, selector) de la poignée, ou (None, None)."""
    for sel in HANDLE_SELECTORS:
        try:
            loc = frame.locator(sel)
            n = await loc.count()
            for k in range(min(n, 6)):
                el = loc.nth(k)
                if not await el.is_visible():
                    continue
                box = await el.bounding_box()
                if box and box["width"] >= 4 and box["height"] >= 4:
                    return el, sel
        except Exception:
            continue
    return None, None


async def _track_box(frame, handle):
    """Boîte de la piste = parent de la poignée (`slider.parentElement`)."""
    for expr in ("xpath=..", "xpath=..", "xpath=.."):
        try:
            parent = handle.locator(expr)
            box = await parent.bounding_box()
            if box and box["width"] > 30:
                return box
        except Exception:
            break
    # repli : boîte du #captcha-container
    for sel in ("#captcha-container", "body"):
        try:
            box = await frame.locator(sel).first.bounding_box()
            if box and box["width"] > 30:
                return box
        except Exception:
            continue
    return None


async def _human_drag(page, start, end, total_ms: int):
    """Glissement humain : easing + jitter + dépassement + correction."""
    x0, y0 = start
    x1, y1 = end

    # entrée dans la zone (survol) puis appui
    await page.mouse.move(x0 + random.uniform(-3, 3), y0 + random.uniform(-3, 3))
    await asyncio.sleep(random.uniform(0.05, 0.15))
    await page.mouse.move(x0, y0)
    await asyncio.sleep(random.uniform(0.10, 0.30))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.10, 0.30))   # hésitation après appui

    n = random.randint(45, 90)
    overshoot = random.uniform(6, 16)
    span = (x1 - x0) + overshoot
    x = x0
    for i in range(1, n + 1):
        t = i / n
        # smoothstep : accélération puis décélération naturelles
        ease = t * t * (3 - 2 * t)
        x = x0 + span * ease
        y = y0 + (y1 - y0) * ease + random.uniform(-2.0, 2.0)
        await page.mouse.move(x, y)
        await asyncio.sleep((total_ms / n) / 1000.0 * random.uniform(0.6, 1.4))

    # correction : on revient sur la cible finale
    back = overshoot
    for k in range(1, 6):
        await page.mouse.move(
            x - back * (1 - k / 5), y1 + random.uniform(-1, 1)
        )
        await asyncio.sleep(random.uniform(0.012, 0.04))

    await asyncio.sleep(random.uniform(0.10, 0.28))    # maintien avant relâchement
    await page.mouse.up()


async def solve_slider(page, attempts: int = 2, timeout_s: float = 30.0) -> bool:
    """
    Tente de résoudre le slider (1-2 tentatives). Renvoie True si le captcha
    disparaît / le cookie `datadome` change.
    """
    if not await is_captcha_present(page):
        return True

    if await _banned(page):
        logger.error("🚫 Captcha : DataDome signale un blocage IP (dd.t='bv') — "
                     "le glissement ne servira à rien.")
        return False

    context = page.context
    before_cookie = await datadome_cookie(context)

    for attempt in range(1, attempts + 1):
        logger.info(f"🧩 Captcha détecté → glissement automatique (tentative {attempt}/{attempts})…")

        frame = await _find_slider_frame(page, timeout_s=min(timeout_s / 2, 12))
        if frame is None:
            logger.warning("🧩 Frame/poignée du captcha introuvable.")
            return False

        handle, sel = await _find_handle(frame)
        if handle is None:
            logger.warning("🧩 Poignée du slider introuvable.")
            return False

        # ⚠️ bounding_box() d'un locator de frame est déjà en coordonnées
        #    du frame principal : NE PAS ajouter l'offset de l'iframe.
        hb = await handle.bounding_box()
        tb = await _track_box(frame, handle)
        if not hb:
            logger.warning("🧩 Poignée sans bounding box.")
            return False

        start = (hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        if tb:
            end_x = tb["x"] + tb["width"] - random.uniform(3, 9)
        else:
            end_x = hb["x"] + hb["width"] / 2 + 280
        end = (end_x, start[1] + random.uniform(-1, 1))

        logger.info(
            f"🧩 poignée={sel} start=({start[0]:.0f},{start[1]:.0f}) "
            f"end_x={end[0]:.0f} distance={end[0] - start[0]:.0f}px"
        )

        try:
            await _human_drag(page, start, end, total_ms=random.randint(750, 1400))
        except Exception as e:
            logger.warning(f"🧩 Glissement en échec : {e}")
            try:
                await page.mouse.up()
            except Exception:
                pass
            return False

        # ── Confirmation du succès (~10 s max) ───────────────────────────────
        for _ in range(40):
            await asyncio.sleep(0.25)
            now_cookie = await datadome_cookie(context)
            if now_cookie and now_cookie != before_cookie:
                logger.info("🧩✅ Captcha résolu (cookie datadome renouvelé).")
                return True
            if not await is_captcha_present(page):
                logger.info("🧩✅ Captcha résolu (challenge disparu).")
                return True

        logger.warning(f"🧩❌ Captcha toujours présent après la tentative {attempt}.")

    return False
