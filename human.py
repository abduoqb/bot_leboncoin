"""
human.py — Simulation de comportements humains pour Playwright.

Fonctionnalités :
- Déplacement de souris avec trajectoire courbe de Bézier (easing + vitesse en cloche)
- Frappe caractère par caractère avec pauses variables et fautes de frappe réalistes
- Scrolls naturels progressifs
- Pauses de "lecture" proportionnelles à la longueur du texte
- Micro-hésitations avant les clics

Pour des tests reproductibles : `seed(n)`.
"""

import asyncio
import logging
import math
import random
import weakref

from config import TYPING_SPEED_MIN, TYPING_SPEED_MAX

logger = logging.getLogger(__name__)

# ── Constantes de réglage ─────────────────────────────────────────────────────
DEFAULT_VIEWPORT = (1600, 900)   # (largeur, hauteur) si la page ne répond pas

TYPO_RATE = 0.04                 # proba de faute de frappe par lettre (hors 1re lettre)
HESITATION_RATE = 0.05           # proba d'une hésitation après un caractère
HESITATION_MIN_S = 0.3
HESITATION_MAX_S = 0.8
TYPO_NOTICE_S = (0.08, 0.25)     # délai avant de remarquer la faute
TYPO_FIX_S = (0.05, 0.15)        # délai après l'effacement

OVERSHOOT_RATE = 0.12            # proba de dépasser un peu la cible
OVERSHOOT_MIN_DIST = 200         # pas de dépassement sur un trajet plus court (px)
CURVE_BEND = 0.25                # écart max du point de contrôle, en fraction de la distance

# ── Position souris, par page ─────────────────────────────────────────────────
_mouse_pos = weakref.WeakKeyDictionary()   # page -> (x, y)
_fallback_pos = {}                         # pages non « weakref-ables »


def seed(n=None):
    """Fixe la graine aléatoire (tests reproductibles)."""
    random.seed(n)


def reset_mouse(page=None):
    """Oublie la position de la souris : d'une page, ou de toutes si page=None."""
    if page is None:
        _mouse_pos.clear()
        _fallback_pos.clear()
        return
    try:
        _mouse_pos.pop(page, None)
    except TypeError as _err:
        logger.debug("Position souris: page non hachable (reset ignore) : %s", type(_err).__name__)
    _fallback_pos.pop(id(page), None)


def _get_pos(page):
    try:
        pos = _mouse_pos.get(page)
    except TypeError as _err:
        logger.debug("Position souris: page non hachable (lecture) : %s", type(_err).__name__)
        pos = None
    return pos if pos is not None else _fallback_pos.get(id(page))


def _set_pos(page, x, y):
    try:
        _mouse_pos[page] = (x, y)
    except TypeError as _err:
        logger.debug("Position souris: page non hachable (repli par id) : %s", type(_err).__name__)
        _fallback_pos[id(page)] = (x, y)


async def _viewport(page) -> tuple:
    """Taille (largeur, hauteur) de la fenêtre ; DEFAULT_VIEWPORT en cas d'échec."""
    try:
        vp = await page.evaluate("() => ({ w: innerWidth, h: innerHeight })")
        w, h = float(vp["w"]), float(vp["h"])
        if w > 0 and h > 0:
            return w, h
    except Exception as _err:
        logger.debug("Taille du viewport illisible (valeur par defaut) : %s", type(_err).__name__)
    return DEFAULT_VIEWPORT


def _clamp(x: float, y: float, vp: tuple) -> tuple:
    """Borne un point au viewport (les coordonnées souris hors fenêtre sont invalides)."""
    return (min(max(x, 0.0), max(vp[0] - 1, 0.0)),
            min(max(y, 0.0), max(vp[1] - 1, 0.0)))


async def _mouse_move(page, x: float, y: float, vp: tuple):
    """mouse.move borné au viewport, avec mise à jour de la position à CHAQUE appel."""
    x, y = _clamp(x, y, vp)
    await page.mouse.move(x, y)
    _set_pos(page, x, y)


# ── Délais ────────────────────────────────────────────────────────────────────

async def pause(min_s: float = 0.8, max_s: float = 2.5):
    """Pause aléatoire simple."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def wait_page_load(page, extra_pause: float = None):
    """
    Attend que la page finisse de charger (version légère pour les scans).
    Pas de scroll ni de mouvement de souris — juste attendre le réseau.
    `extra_pause=0` est respecté (pas de pause) ; None = pause aléatoire 1.5-3 s.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception as _err:
        logger.debug("wait_for_load_state(domcontentloaded) a echoue : %s", type(_err).__name__)
    delay = extra_pause if extra_pause is not None else random.uniform(1.5, 3.0)
    await asyncio.sleep(delay)


async def reading_pause(text: str, wpm: float = 180, extra_pause: float = None):
    """
    Pause proportionnelle au temps de lecture humain.
    Un humain lit environ 200-250 mots/min ; on simule 150-200 wpm.
    `extra_pause` (si fourni, 0 compris) remplace le calcul : durée fixe en secondes.
    """
    words = len(text.split())
    if extra_pause is not None:
        delay = extra_pause
    else:
        base = (words / wpm) * 60  # secondes théoriques
        jitter = random.uniform(0.85, 1.25)
        delay = min(max(0.8, base * jitter), 8.0)  # plafonné à 8s
    logger.debug(f"Lecture simulée : {delay:.1f}s pour {words} mots")
    await asyncio.sleep(delay)


# ── Souris ────────────────────────────────────────────────────────────────────

def _ease(t: float) -> float:
    """Easing « smoothstep » : t' = 3t² − 2t³ (monotone sur [0, 1], lent aux extrémités)."""
    t = min(max(t, 0.0), 1.0)
    return 3 * t * t - 2 * t ** 3


def _bezier_points(x0: float, y0: float, x1: float, y1: float, steps: int = 25) -> list:
    """
    Points le long d'une Bézier quadratique entre (x0,y0) et (x1,y1).
    Le point de contrôle est décalé PERPENDICULAIREMENT au segment, d'une quantité
    proportionnelle à la distance. Les points sont espacés selon l'easing :
    serrés au départ et à l'arrivée, espacés au milieu.
    """
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist > 0:
        nx, ny = -dy / dist, dx / dist            # normale unitaire
        offset = random.uniform(-CURVE_BEND, CURVE_BEND) * dist
    else:
        nx = ny = offset = 0.0
    mid_x = (x0 + x1) / 2 + nx * offset
    mid_y = (y0 + y1) / 2 + ny * offset

    points = []
    for i in range(steps + 1):
        t = _ease(i / steps)
        px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * mid_x + t ** 2 * x1
        py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * mid_y + t ** 2 * y1
        points.append((px, py))
    return points


def _delay_weights(n: int) -> list:
    """Poids de délai en cloche : plus lent (poids fort) au départ et à l'arrivée."""
    if n <= 1:
        return [1.0] * n
    return [1.0 + 1.2 * (1 - 4 * (i / (n - 1)) * (1 - i / (n - 1))) for i in range(n)]


async def _glide(page, points: list, total_s: float, vp: tuple):
    """Parcourt `points` en `total_s` secondes avec un profil de vitesse en cloche."""
    weights = _delay_weights(len(points))
    unit = total_s / sum(weights)
    for (px, py), w in zip(points, weights):
        await _mouse_move(page, px, py, vp)
        await asyncio.sleep(unit * w * random.uniform(0.8, 1.2))


async def human_move(page, x: float, y: float):
    """
    Déplace la souris vers (x, y) en suivant une trajectoire courbe.
    Easing + vitesse en cloche, léger dépassement occasionnel de la cible.
    La destination est bornée au viewport.
    """
    try:
        vp = await _viewport(page)
        pos = _get_pos(page)
        curr_x, curr_y = pos if pos is not None else (vp[0] / 2, vp[1] / 2)

        # Petite variation sur la destination (on ne clique jamais exactement au pixel)
        dest_x, dest_y = _clamp(x + random.uniform(-3, 3), y + random.uniform(-3, 3), vp)

        dist = math.hypot(dest_x - curr_x, dest_y - curr_y)
        total_s = (random.uniform(600, 1400) + dist * 0.2) / 1000

        if dist > OVERSHOOT_MIN_DIST and random.random() < OVERSHOOT_RATE:
            # On dépasse un peu la cible, puis on revient dessus
            over = random.uniform(6, 18)
            ox, oy = _clamp(dest_x + (dest_x - curr_x) / dist * over,
                            dest_y + (dest_y - curr_y) / dist * over, vp)
            await _glide(page, _bezier_points(curr_x, curr_y, ox, oy, random.randint(20, 35)),
                         total_s * 0.9, vp)
            await asyncio.sleep(random.uniform(0.04, 0.12))
            await _glide(page, _bezier_points(ox, oy, dest_x, dest_y, random.randint(5, 9)),
                         total_s * 0.2, vp)
        else:
            await _glide(page, _bezier_points(curr_x, curr_y, dest_x, dest_y, random.randint(20, 35)),
                         total_s, vp)

    except Exception as e:
        logger.debug(f"human_move erreur (non bloquant): {e}")


async def human_click(page, element) -> bool:
    """
    Déplace la souris vers l'élément, hésite légèrement, puis clique.
    Retourne True si le clic a bien été effectué.
    """
    try:
        box = await element.bounding_box()
    except Exception as _err:
        logger.debug("bounding_box illisible : %s", type(_err).__name__)
        box = None

    if box:
        try:
            cx = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
            cy = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
            await human_move(page, cx, cy)
            # Micro-hésitation avant le clic (réflexe humain)
            await asyncio.sleep(random.uniform(0.08, 0.25))
        except Exception as e:
            logger.debug(f"human_click (mouvement) : {e}")

    try:
        await element.click(timeout=10000)
        return True
    except Exception as e:
        logger.warning(f"human_click : clic impossible ({e})")
        return False


# ── Clavier ───────────────────────────────────────────────────────────────────

def _build_azerty_neighbors() -> dict:
    """Touches voisines (même rangée, ou rangées adjacentes décalées) sur un clavier AZERTY."""
    rows = [("azertyuiop", 0.0), ("qsdfghjklm", 0.25), ("wxcvbn", 0.75)]
    pos = {}
    for r, (letters, offset) in enumerate(rows):
        for i, c in enumerate(letters):
            pos[c] = (r, i + offset)
    out = {}
    for c, (r, x) in pos.items():
        out[c] = sorted(
            o for o, (r2, x2) in pos.items()
            if o != c and ((r2 == r and abs(x2 - x) == 1) or (abs(r2 - r) == 1 and abs(x2 - x) <= 0.8))
        )
    return out


AZERTY_NEIGHBORS = _build_azerty_neighbors()


async def human_type(page, element, text: str):
    """
    Tape le texte caractère par caractère via page.keyboard.
    Utilise TYPING_SPEED_MIN/MAX (config). Fautes de frappe sur une touche
    voisine AZERTY, corrigées par Backspace.

    Les retours à la ligne (\\n, \\r) sont remplacés par un espace : dans le champ
    de message, Entrée ENVOIE le message, on ne doit jamais l'émettre par accident.
    """
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    # Cliquer sur l'élément pour le focus
    try:
        await element.click(timeout=10000)
    except Exception as e:
        logger.debug(f"human_type : focus impossible ({e})")

    vmin, vmax = TYPING_SPEED_MIN / 1000, TYPING_SPEED_MAX / 1000

    for i, char in enumerate(text):
        # Faute de frappe occasionnelle puis correction : signal humain fort.
        neighbors = AZERTY_NEIGHBORS.get(char.lower())
        if neighbors and i and random.random() < TYPO_RATE:
            wrong = random.choice(neighbors)
            if char.isupper():
                wrong = wrong.upper()
            await page.keyboard.type(wrong, delay=0)
            await asyncio.sleep(random.uniform(*TYPO_NOTICE_S))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(*TYPO_FIX_S))

        await page.keyboard.type(char, delay=0)  # on gère le délai manuellement

        if char == " ":
            delay = random.uniform(vmin, vmax * 1.5)
        elif char in ".,!?;:":
            delay = random.uniform(vmax, vmax * 3.5)
        elif char.isupper():
            delay = random.uniform(vmin * 1.5, vmax * 1.8)
        else:
            delay = random.uniform(vmin, vmax)

        if random.random() < HESITATION_RATE:
            delay += random.uniform(HESITATION_MIN_S, HESITATION_MAX_S)

        await asyncio.sleep(delay)


# ── Mouvements parasites ──────────────────────────────────────────────────────

async def stray_move(page, n: int = 1):
    """Petits mouvements de souris « parasites » (la main qui traîne)."""
    vp = await _viewport(page)
    for _ in range(n):
        try:
            await human_move(
                page,
                random.uniform(100, max(200, vp[0] - 100)),
                random.uniform(100, max(200, vp[1] - 200)),
            )
        except Exception as _err:
            logger.debug("Deplacement de souris de l'agitation echoue : %s", type(_err).__name__)
        await asyncio.sleep(random.uniform(0.15, 0.5))


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


async def human_list_scroll(page, box: dict, allow_up: bool = True) -> str:
    """
    Un geste de défilement sur une liste, d'une longueur variable.

    La souris se place d'abord dans `box` (x, y, w, h : zone de la liste), car
    la molette agit là où se trouve le curseur. Le plus souvent on descend, plus
    ou moins loin ; parfois on remonte un peu, parfois on s'arrête « lire ».
    Retourne la direction réellement prise : 'down' ou 'up'.
    """
    try:
        await human_move(
            page,
            box["x"] + box["w"] * random.uniform(0.25, 0.75),
            box["y"] + box["h"] * random.uniform(0.25, 0.75),
        )
    except Exception as e:
        logger.debug(f"human_list_scroll : placement de la souris impossible ({e})")

    if allow_up and random.random() < 0.2:
        await human_scroll(page, "up", random.randint(60, 260))
        return "up"

    long_geste = random.random() < 0.15
    await human_scroll(page, "down", random.randint(600, 1100) if long_geste else random.randint(180, 720))
    if random.random() < 0.25:
        await asyncio.sleep(random.uniform(1.0, 3.0))   # on lit ce qui vient d'apparaître
    return "down"


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
    vp = await _viewport(page)
    rx = random.uniform(200, max(300, vp[0] - 200))
    ry = random.uniform(150, max(250, vp[1] - 400))
    await human_move(page, rx, ry)
    await asyncio.sleep(random.uniform(0.3, 0.8))
