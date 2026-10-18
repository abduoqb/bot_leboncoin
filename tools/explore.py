"""
explore.py — Outil de DIAGNOSTIC (hors production, ne fait rien d'autre que lire).

But : ouvrir un VRAI Chrome via Playwright (profil persistant), te laisser te
connecter manuellement / glisser un éventuel captcha DataDome, puis cartographier
le DOM réel de la page /messages et d'une conversation pour calibrer les
sélecteurs de leboncoin.py.

Différences clés vs le bot :
  - `channel="chrome"` + profil persistant → empreinte = ton vrai Chrome
    (c'est le principal levier anti-DataDome).
  - navigation LENTE (accueil → messages → conversation), jamais de deep-link brut.
  - AUCUNE action d'écriture : on lit et on dumpe.

Usage :
    python explore.py

Sorties (dans debugs/) :
    explore_messages.html / .png / .json
    explore_conv.html / .png / .json
"""

import os
import sys

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

USER_DATA_DIR = os.path.abspath("chrome_profile")
import os
import sys

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BASE_DIR  # noqa: E402

OUT_DIR = os.path.join(BASE_DIR, "debugs")
LOGIN_TIMEOUT = 420  # secondes laissées à l'humain (login + captcha)

# ── JS : détection captcha / login ───────────────────────────────────────────
JS_STATE = r"""() => {
  const body = (document.body && document.body.innerText) || '';
  const captcha = !!document.querySelector(
      'iframe[src*="captcha-delivery"], iframe[title*="CAPTCHA"], #cmsg');
  const captchaText = /Faites glisser|s'adresse bien . vous|un robot est sur le m/i.test(body);
  const loginForm = !!document.querySelector(
      'input[type="password"], form[action*="login"], input[name="email"]');
  return {
    url: location.href,
    title: document.title,
    captcha: captcha || captchaText,
    loginForm,
    convCount: document.querySelectorAll('a[href*="/messages/id/"]').length,
  };
}"""

# ── JS : dump de la liste des conversations ──────────────────────────────────
JS_DUMP_LIST = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const linkOf = el => {
    let p = el;
    for (let i = 0; i < 8 && p; i++) {
      const l = p.querySelector && p.querySelector('a[href*="/messages/id/"]');
      if (l) return l.getAttribute('href');
      p = p.parentElement;
    }
    return null;
  };
  return {
    url: location.href,
    title: document.title,
    convLinks: [...document.querySelectorAll('a[href*="/messages/id/"]')].map(a => ({
      href: a.getAttribute('href'),
      text: clean(a.innerText).slice(0, 200),
      aria: a.getAttribute('aria-label'),
      cls: a.className,
      testid: a.getAttribute('data-test-id'),
      qa: a.getAttribute('data-qa-id'),
      spark: a.getAttribute('data-spark-component'),
    })),
    badges: [...document.querySelectorAll('[data-spark-component="badge"], [class*="badge" i], [class*="unread" i]')]
      .slice(0, 40)
      .map(b => ({
        tag: b.tagName,
        cls: b.className,
        title: b.getAttribute('title'),
        aria: b.getAttribute('aria-label'),
        text: clean(b.innerText).slice(0, 60),
        parentHref: linkOf(b),
      })),
  };
}"""

# ── JS : dump d'une conversation ouverte ─────────────────────────────────────
JS_DUMP_CONV = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const dump = el => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName,
      cls: el.className,
      testid: el.getAttribute('data-test-id'),
      qa: el.getAttribute('data-qa-id'),
      spark: el.getAttribute('data-spark-component'),
      role: el.getAttribute('role'),
      aria: el.getAttribute('aria-label'),
      text: clean(el.innerText).slice(0, 220),
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
    };
  };

  const sels = [
    '[data-test-id*="message" i]', '[data-qa-id*="message" i]',
    '[class*="message" i]', '[class*="bubble" i]',
  ];
  const seen = new Set();
  const messageLike = [];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      if (seen.has(el)) continue;
      seen.add(el);
      if (messageLike.length < 120) messageLike.push(dump(el));
    }
  }

  return {
    url: location.href,
    title: document.title,
    vpWidth: window.innerWidth,
    messageLike,
    inputs: [...document.querySelectorAll('textarea, [contenteditable="true"]')].map(dump),
    buttons: [...document.querySelectorAll('button')].map(dump).slice(0, 60),
    paragraphs: [...document.querySelectorAll('p')].slice(0, 60).map(dump),
  };
}"""


async def _dump(page, label: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    js = JS_DUMP_CONV if label == "conv" else JS_DUMP_LIST
    try:
        data = await page.evaluate(js)
        state = await page.evaluate(JS_STATE)
    except Exception as e:
        print(f"  ⚠️ evaluate a échoué: {e}")
        data, state = {}, {}

    with open(f"{OUT_DIR}/explore_{label}.json", "w", encoding="utf-8") as f:
        json.dump({"state": state, "data": data}, f, ensure_ascii=False, indent=2)

    try:
        html = await page.content()
        with open(f"{OUT_DIR}/explore_{label}.html", "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=f"{OUT_DIR}/explore_{label}.png", full_page=True)
    except Exception as e:
        print(f"  ⚠️ html/png: {e}")

    print(f"  📸 dump écrit : {OUT_DIR}/explore_{label}.{{json,html,png}}")
    print(f"     captcha={state.get('captcha')} login={state.get('loginForm')} "
          f"convs={state.get('convCount')} url={state.get('url')}")
    return state


async def _read_state(page) -> dict:
    """Lit l'état de la page. En cas d'erreur (navigation en cours), on considère
    la page comme NON prête — sinon on conclut « prêt » à tort pendant un redirect."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass
    try:
        return await page.evaluate(JS_STATE)
    except Exception as e:
        # "Execution context was destroyed" = la page navigue encore → pas prête
        return {"url": page.url, "captcha": True, "loginForm": True,
                "convCount": 0, "_err": str(e)[:80]}


def _is_ready(st: dict) -> bool:
    url = st.get("url", "") or ""
    return (
        not st.get("captcha")
        and not st.get("loginForm")
        and "auth.leboncoin.fr" not in url
        and "/messages" in url
    )


async def _wait_human(page, label: str):
    """Attend que l'humain se connecte / glisse le captcha.
    Exige 3 lectures CONSÉCUTIVES « prêtes » avant de valider (anti faux positif)."""
    good_streak = 0
    last_log = -15

    for elapsed in range(0, LOGIN_TIMEOUT, 3):
        st = await _read_state(page)

        if _is_ready(st):
            good_streak += 1
            if good_streak >= 3:
                print(f"  ✅ {label} prêt ({elapsed}s) : {st.get('url')} "
                      f"({st.get('convCount')} convs)")
                return st
        else:
            good_streak = 0
            if elapsed - last_log >= 15:
                url = st.get("url", "")
                if st.get("captcha"):
                    reason = "CAPTCHA DataDome 👉 glisse le slider"
                elif st.get("loginForm") or "auth.leboncoin.fr" in url:
                    reason = "page de CONNEXION 👉 connecte-toi"
                else:
                    reason = f"chargement… ({url[:70]})"
                print(f"  ⏳ [{label}] {reason} — ({elapsed}s/{LOGIN_TIMEOUT}s)")
                last_log = elapsed

        await asyncio.sleep(3)

    print(f"  ⏱️ Timeout sur {label}, on dumpe ce qu'on a.")
    return await _read_state(page)


async def main():
    async with async_playwright() as pw:
        print("=" * 70)
        print("  EXPLORATION DU DOM LEBONCOIN — profil Chrome persistant")
        print(f"  Profil : {USER_DATA_DIR}")
        print("  Le navigateur va s'ouvrir. Connecte-toi si besoin.")
        print("=" * 70)

        ctx = await pw.chromium.launch_persistent_context(
            USER_DATA_DIR,
            channel="chrome",              # vrai Chrome, pas Chromium
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            no_viewport=True,
            locale="fr-FR",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 1) Accueil (navigation douce)
        print("\n[1/4] Ouverture de l'accueil...")
        await page.goto("https://www.leboncoin.fr/", wait_until="domcontentloaded")
        await asyncio.sleep(4)
        await page.mouse.move(700, 420)
        await asyncio.sleep(2)

        # 2) Page messages + attente login/captcha
        print("\n[2/4] Ouverture de /messages...")
        await page.goto("https://www.leboncoin.fr/messages", wait_until="domcontentloaded")
        await _wait_human(page, "messages")
        await asyncio.sleep(3)

        # 3) Dump de la liste
        print("\n[3/4] Dump de la liste des conversations...")
        state = await _dump(page, "messages")

        # 4) Ouvrir la 1ère conversation EN CLIQUANT (jamais de deep-link)
        links = await page.query_selector_all('a[href*="/messages/id/"]')
        if links:
            print(f"\n[4/4] Ouverture de la 1ère conversation par clic ({len(links)} dispo)...")
            try:
                await links[0].click()
            except Exception:
                await links[0].click(force=True)
            await _wait_human(page, "conversation")
            await asyncio.sleep(3)
            await _dump(page, "conv")
        else:
            print("\n[4/4] Aucune conversation trouvée (liste vide ?).")
            if state.get("captcha"):
                print("      Cause probable : CAPTCHA DataDome toujours actif.")

        print("\n" + "=" * 70)
        print("  Terminé. Le navigateur va se fermer dans 5s.")
        print("=" * 70)
        await asyncio.sleep(5)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
