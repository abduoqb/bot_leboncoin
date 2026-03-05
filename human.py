"""
human.py — Simulation de comportements humains pour Playwright.

Fonctionnalités :
- Déplacement de souris avec trajectoire courbe de Bézier
- Frappe caractère par caractère avec pauses variables
- Scrolls naturels progressifs
- Pauses de "lecture" proportionnelles à la longueur du texte
- Micro-hésitations avant les clics
"""

import asyncio
import random
import math
import logging

logger = logging.getLogger(__name__)


# ── Délais ────────────────────────────────────────────────────────────────────

async def pause(min_s: float = 0.8, max_s: float = 2.5):
    """Pause aléatoire simple."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def wait_page_load(page, extra_pause: float = None):
    """
    Attend que la page finisse de charger (version légère pour les scans).
    Pas de scroll ni de mouvement de souris — juste attendre le réseau.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass  # pas bloquant
    delay = extra_pause if extra_pause else random.uniform(1.5, 3.0)
    await asyncio.sleep(delay)


async def reading_pause(text: str, wpm: float = 180):
    """
    Pause proportionnelle au temps de lecture humain.
    Un humain lit environ 200-250 mots/min ; on simule 150-200 wpm.
    """
    words = len(text.split())
    base = (words / wpm) * 60  # secondes théoriques
    jitter = random.uniform(0.85, 1.25)
    delay = max(0.8, base * jitter)
    delay = min(delay, 8.0)  # plafonné à 8s
    logger.debug(f"⏳ Lecture simulée : {delay:.1f}s pour {words} mots")
    await asyncio.sleep(delay)


# ── Souris ────────────────────────────────────────────────────────────────────

def _bezier_points(x0: float, y0: float, x1: float, y1: float, steps: int = 25) -> list:
    """
    Génère des points le long d'une courbe de Bézier quadratique entre (x0,y0) et (x1,y1).
    Le point de contrôle est déplacé aléatoirement pour simuler un mouvement naturel.
    """
    # Point de contrôle décalé perpendiculairement à la droite
    mid_x = (x0 + x1) / 2 + random.uniform(-80, 80)
    mid_y = (y0 + y1) / 2 + random.uniform(-60, 60)

    points = []
    for i in range(steps + 1):
        t = i / steps
        # Formule Bézier quadratique
        px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * mid_x + t ** 2 * x1
        py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * mid_y + t ** 2 * y1
        points.append((px, py))
    return points


async def human_move(page, x: float, y: float):
    """
    Déplace la souris vers (x, y) en suivant une trajectoire courbe.
    Vitesse variable : accélération + décélération naturelle.
    """
    try:
        # Position actuelle (approximée au centre si inconnue)
        vp = page.viewport_size or {"width": 1920, "height": 1080}
        curr_x = random.uniform(200, vp["width"] - 200)
        curr_y = random.uniform(150, vp["height"] - 150)

        # Petite variation sur la destination (on ne clique jamais exactement au pixel)
        dest_x = x + random.uniform(-3, 3)
        dest_y = y + random.uniform(-3, 3)

        points = _bezier_points(curr_x, curr_y, dest_x, dest_y, steps=random.randint(20, 35))

        # Durée totale du déplacement : 0.6 à 1.4s selon la distance
        dist = math.hypot(dest_x - curr_x, dest_y - curr_y)
        total_ms = random.uniform(600, 1400) + dist * 0.2
        step_delay = total_ms / len(points) / 1000  # en secondes

        for px, py in points:
            await page.mouse.move(px, py)
            # Variation de vitesse : ralentir vers la fin
            await asyncio.sleep(step_delay * random.uniform(0.7, 1.3))

    except Exception as e:
        logger.debug(f"human_move erreur (non bloquant): {e}")


async def human_click(page, element):
    """
    Déplace la souris vers l'élément, hésite légèrement, puis clique.
    """
    try:
        box = await element.bounding_box()
        if box:
            cx = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
            cy = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
            await human_move(page, cx, cy)
            # Micro-hésitation avant le clic (réflexe humain)
            await asyncio.sleep(random.uniform(0.08, 0.25))
            await element.click()
        else:
            await element.click()
    except Exception as e:
        logger.debug(f"human_click fallback: {e}")
        await element.click()


# ── Clavier ───────────────────────────────────────────────────────────────────

async def human_type(element, text: str):
    """
    Tape le texte caractère par caractère avec des délais variables.
    - Vitesse de base : 60-100 mots/min (frappe normale)
    - Occasionnellement plus lent (hésitation)
    - Petites pauses après les espaces et la ponctuation
    """
    # Délai moyen par caractère en secondes (60 mots/min ≈ 5 chars/mot → ~200ms/char)
    for i, char in enumerate(text):
        await element.type(char, delay=0)  # on gère le délai manuellement

        if char == " ":
            # Petite pause après un mot
            delay = random.uniform(0.07, 0.18)
        elif char in ".,!?;:":
            # Pause après ponctuation (on "réfléchit")
            delay = random.uniform(0.15, 0.45)
        elif char.isupper():
            # Majuscule = un poil plus lent (Shift)
            delay = random.uniform(0.10, 0.22)
        else:
            delay = random.uniform(0.05, 0.17)

        # Hésitation occasionnelle (1 fois sur 20 environ)
        if random.random() < 0.05:
            delay += random.uniform(0.3, 0.8)

        await asyncio.sleep(delay)


# ── Scroll ────────────────────────────────────────────────────────────────────

async def human_scroll(page, direction: str = "down", amount: int = None):
    """
    Scroll progressif et naturel, par petits coups successifs.
    direction : 'down' | 'up'
    amount : pixels totaux (None = aléatoire 200-600px)
    """
    if amount is None:
        amount = random.randint(200, 600)

    delta = amount if direction == "down" else -amount
    steps = random.randint(4, 8)
    per_step = delta // steps

    for _ in range(steps):
        # Légère variation sur chaque coup de scroll
        scroll_this = per_step + random.randint(-20, 20)
        await page.mouse.wheel(0, scroll_this)
        await asyncio.sleep(random.uniform(0.08, 0.2))

    # Pause après le scroll (on "regarde" le contenu)
    await asyncio.sleep(random.uniform(0.5, 1.5))


# ── Séquences composées ───────────────────────────────────────────────────────

async def arrive_on_page(page):
    """
    Simule l'arrivée humaine sur une nouvelle page :
    - Pause initiale (la page charge)
    - Scroll léger pour "regarder"
    - Déplacement de souris aléatoire
    """
    await asyncio.sleep(random.uniform(1.5, 3.5))

    # Parfois scroll un peu pour simuler la lecture
    if random.random() < 0.6:
        await human_scroll(page, "down", random.randint(80, 250))
        await asyncio.sleep(random.uniform(0.5, 1.2))
        if random.random() < 0.3:
            await human_scroll(page, "up", random.randint(50, 150))

    # Mouvement de souris aléatoire ("on cherche quelque chose")
    vp = page.viewport_size or {"width": 1920, "height": 1080}
    rx = random.uniform(200, vp["width"] - 200)
    ry = random.uniform(150, vp["height"] - 400)
    await human_move(page, rx, ry)
    await asyncio.sleep(random.uniform(0.3, 0.8))
