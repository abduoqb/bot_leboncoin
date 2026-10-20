"""
explore.py — Outil de DIAGNOSTIC (hors production, lecture seule : aucune écriture sur Leboncoin).

Ouvre un VRAI Chrome via Playwright (profil persistant `config.PROFILE_DIR`) et
cartographie le DOM réel pour calibrer les sélecteurs de leboncoin.py.
Navigation LENTE (accueil -> messages -> conversation en CLIQUANT, jamais de deep-link).

Sous-commandes :
    dump      liste des conversations + 1re conversation (login/captcha à faire à la main)
              -> debugs/explore_messages.{json,html,png}, explore_conv.{json,html,png}
    threads   anatomie du fil de discussion sur plusieurs conversations (--max, défaut 5)
              -> debugs/explore2_conv_<i>.json, explore2_summary.json
    sides     côté entrant/sortant des bulles + détection des non-lus (--max, défaut 12)
              -> debugs/explore3.json

Usage :
    python tools/explore.py --help
    python tools/explore.py dump
    python tools/explore.py threads --max 3
    python tools/explore.py --yes sides        # --yes : pas de confirmation

ATTENTION : les fichiers écrits dans debugs/ contiennent des DONNÉES PERSONNELLES
(messages, noms, HTML complet de ta messagerie). Ne les partage pas, ne les commite pas.
Le bot ne doit pas tourner en même temps (même profil Chrome) : verrou `instance`.
"""

import argparse
import asyncio
import json
import os
import sys

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BASE_DIR, PROFILE_DIR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_DIR = os.path.join(BASE_DIR, "debugs")
LOGIN_TIMEOUT = 420  # secondes laissées à l'humain (login + captcha)

# ── JS : détection captcha / login ────────────────────────────────────────
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

# ── JS : dump liste des conversations (dump) ──────────────────────────────
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

# ── JS : dump d'une conversation ouverte (dump) ───────────────────────────
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

# ── JS : anatomie complète du fil (threads) ───────────────────────────────
JS_CHAT = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const attrs = el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]));
  const chain = (el, n) => {
    const out = [];
    let p = el.parentElement;
    for (let i = 0; i < n && p; i++) {
      const cs = getComputedStyle(p);
      out.push({
        tag: p.tagName, cls: p.className, qa: p.getAttribute('data-qa-id'),
        display: cs.display, justify: cs.justifyContent, align: cs.alignItems,
        alignSelf: cs.alignSelf, overflowY: cs.overflowY,
      });
      p = p.parentElement;
    }
    return out;
  };

  const bubbles = [...document.querySelectorAll('[data-qa-id="message-bubble"]')].map(b => {
    const r = b.getBoundingClientRect();
    return {
      text: clean(b.innerText).slice(0, 300),
      cls: b.className,
      qa: b.getAttribute('data-qa-id'),
      attrs: attrs(b),
      x: Math.round(r.x), right: Math.round(r.right), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      parents: chain(b, 5),
    };
  });

  // Conteneur scrollable du fil + ses enfants directs
  let container = null, children = [];
  const first = document.querySelector('[data-qa-id="message-bubble"]');
  if (first) {
    let p = first.parentElement;
    while (p && !/auto|scroll/.test(getComputedStyle(p).overflowY)) p = p.parentElement;
    if (p) {
      container = { tag: p.tagName, cls: p.className, qa: p.getAttribute('data-qa-id'),
                    scrollHeight: p.scrollHeight, clientHeight: p.clientHeight };
      children = [...p.children].map(c => {
        const cr = c.getBoundingClientRect();
        return {
          tag: c.tagName, cls: c.className, qa: c.getAttribute('data-qa-id'),
          text: clean(c.innerText).slice(0, 200),
          qaIds: [...c.querySelectorAll('[data-qa-id]')].map(e => e.getAttribute('data-qa-id')).slice(0, 8),
          x: Math.round(cr.x), w: Math.round(cr.width),
        };
      });
    }
  }

  const ta = document.querySelector('textarea');
  const send = document.querySelector('[data-qa-id="message-send"]');

  return {
    url: location.href,
    bubbles,
    container,
    children,
    textarea: ta ? { attrs: attrs(ta), cls: ta.className } : null,
    send: send ? { attrs: attrs(send), disabled: send.disabled } : null,
    sparkComponents: [...new Set([...document.querySelectorAll('[data-spark-component]')]
      .map(e => e.getAttribute('data-spark-component')))],
    qaIds: [...new Set([...document.querySelectorAll('[data-qa-id]')]
      .map(e => e.getAttribute('data-qa-id')))].slice(0, 100),
    unreadLike: [...document.querySelectorAll('[aria-label]')]
      .filter(e => /non lu|notification/i.test(e.getAttribute('aria-label') || ''))
      .slice(0, 15)
      .map(e => ({ tag: e.tagName, cls: e.className, aria: e.getAttribute('aria-label'),
                   spark: e.getAttribute('data-spark-component'), text: clean(e.innerText).slice(0, 20) })),
  };
}"""

# ── JS : liste + badges non-lus (sides) ───────────────────────────────────
JS_LIST = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const badgeOf = a => {
    let p = a;
    for (let i = 0; i < 6 && p; i++) {
      for (const b of p.querySelectorAll('[data-spark-component="badge"], [class*="badge" i]')) {
        const aria = b.getAttribute('aria-label') || '';
        const title = b.getAttribute('title') || '';
        const txt = clean(b.innerText);
        if (aria || title || txt) return { aria, title, txt, cls: b.className };
      }
      p = p.parentElement;
    }
    return null;
  };
  return {
    items: [...document.querySelectorAll('a[href*="/messages/id/"]')].map((a, i) => ({
      i,
      href: a.getAttribute('href'),
      text: clean(a.innerText).slice(0, 120),
      badge: badgeOf(a),
    })),
    dots: [...document.querySelectorAll('span[aria-label], div[aria-label]')]
      .filter(e => /non lu|notification/i.test(e.getAttribute('aria-label') || ''))
      .map(e => ({ aria: e.getAttribute('aria-label'), cls: e.className,
                   spark: e.getAttribute('data-spark-component') })),
  };
}"""

# ── JS : côté de chaque bulle entrant/sortant (sides) ─────────────────────
JS_THREAD = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const bubbleSide = b => {
    const cs = getComputedStyle(b);
    const cls = b.className;
    // 1) classes sémantiques
    let byClass = null;
    if (cls.includes('rounded-bl-none')) byClass = 'in';
    else if (cls.includes('rounded-br-none')) byClass = 'out';
    // 2) justify-content du parent
    let p = b.parentElement;
    let byJustify = null;
    if (p) {
      const j = getComputedStyle(p).justifyContent;
      if (j === 'flex-end') byJustify = 'out';
      else if (j === 'flex-start') byJustify = 'in';
    }
    return { byClass, byJustify, cls, alignSelf: cs.alignSelf, marginLeft: cs.marginLeft };
  };
  const bubbles = [...document.querySelectorAll('[data-qa-id="message-bubble"]')].map(b => {
    const li = b.closest('li');
    const s = bubbleSide(b);
    return { text: clean(b.innerText).slice(0, 160), side: s,
             liCls: li ? li.className : null };
  });
  // items de la liste qui portent une bulle / des qa-id
  const ol = document.querySelector('ol.overflow-y-auto, ol[class*="overflow-y-auto"]');
  const items = ol ? [...ol.children].map(c => ({
    tag: c.tagName, cls: c.className,
    qaIds: [...c.querySelectorAll('[data-qa-id]')].map(e => e.getAttribute('data-qa-id')),
    hasBubble: !!c.querySelector('[data-qa-id="message-bubble"]'),
    text: clean(c.innerText).slice(0, 90),
  })) : [];
  const ta = document.querySelector('[data-spark-component="textarea"]');
  const send = document.querySelector('[data-qa-id="message-send"]');
  return {
    url: location.href, bubbles, items,
    textareaAttrs: ta ? Object.fromEntries([...ta.attributes].map(a => [a.name, a.value])) : null,
    sendAttrs: send ? Object.fromEntries([...send.attributes].map(a => [a.name, a.value])) : null,
    sendDisabled: send ? send.disabled : null,
    sendAriaDisabled: send ? send.getAttribute('aria-disabled') : null,
  };
}"""


# ── Utilitaires communs ──────────────────────────────────────────────────────

async def _open_context(pw):
    """Vrai Chrome + profil persistant du bot (empreinte = ton Chrome)."""
    return await pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        channel="chrome",              # vrai Chrome, pas Chromium
        headless=False,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        no_viewport=True,
        locale="fr-FR",
    )


async def _goto_messages(page, wait_s: float = 4):
    """Accueil puis /messages (navigation douce)."""
    await page.goto("https://www.leboncoin.fr/", wait_until="domcontentloaded")
    await asyncio.sleep(3)
    await page.goto("https://www.leboncoin.fr/messages", wait_until="domcontentloaded")
    await asyncio.sleep(wait_s)


async def _click_conv(page, i: int):
    """Clique la i-ème conversation (liste relue à chaque fois : le DOM est recyclé)."""
    links = await page.query_selector_all('a[href*="/messages/id/"]')
    if i >= len(links):
        return None
    href = await links[i].get_attribute("href")
    try:
        await links[i].click()
    except Exception:
        await links[i].click(force=True)
    return href


def _write_json(name: str, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def _dump(page, label: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    js = JS_DUMP_CONV if label == "conv" else JS_DUMP_LIST
    try:
        data = await page.evaluate(js)
        state = await page.evaluate(JS_STATE)
    except Exception as e:
        print(f"  ⚠️ evaluate a échoué: {e}")
        data, state = {}, {}

    _write_json(f"explore_{label}.json", {"state": state, "data": data})

    try:
        html = await page.content()
        with open(os.path.join(OUT_DIR, f"explore_{label}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=os.path.join(OUT_DIR, f"explore_{label}.png"), full_page=True)
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


# ── Sous-commandes ───────────────────────────────────────────────────────────

async def cmd_dump(pw, args):
    print("=" * 70)
    print("  EXPLORATION DU DOM LEBONCOIN — profil Chrome persistant")
    print(f"  Profil : {PROFILE_DIR}")
    print("  Le navigateur va s'ouvrir. Connecte-toi si besoin.")
    print("=" * 70)
    ctx = await _open_context(pw)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    print("\n[1/4] Ouverture de l'accueil...")
    await page.goto("https://www.leboncoin.fr/", wait_until="domcontentloaded")
    await asyncio.sleep(4)
    await page.mouse.move(700, 420)
    await asyncio.sleep(2)

    print("\n[2/4] Ouverture de /messages...")
    await page.goto("https://www.leboncoin.fr/messages", wait_until="domcontentloaded")
    await _wait_human(page, "messages")
    await asyncio.sleep(3)

    print("\n[3/4] Dump de la liste des conversations...")
    state = await _dump(page, "messages")

    # Ouvrir la 1ère conversation EN CLIQUANT (jamais de deep-link)
    if await _click_conv(page, 0):
        print("\n[4/4] 1ère conversation ouverte par clic...")
        await _wait_human(page, "conversation")
        await asyncio.sleep(3)
        await _dump(page, "conv")
    else:
        print("\n[4/4] Aucune conversation trouvée (liste vide ?).")
        if state.get("captcha"):
            print("      Cause probable : CAPTCHA DataDome toujours actif.")

    print("\n  Terminé. Le navigateur va se fermer dans 5s.")
    await asyncio.sleep(5)
    await ctx.close()


async def cmd_threads(pw, args):
    print("=" * 70)
    print("  EXPLORATION — anatomie des fils de discussion")
    print("=" * 70)
    ctx = await _open_context(pw)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await _goto_messages(page)

    st = await page.evaluate(JS_STATE)
    print(f"  état: {st}")
    if st.get("captcha") or st.get("loginForm"):
        print("  ⚠️ captcha/login — connecte-toi puis relance ce script.")
        await asyncio.sleep(20)
        await ctx.close()
        return

    links = await page.query_selector_all('a[href*="/messages/id/"]')
    n = min(len(links), args.max)
    print(f"  {len(links)} conversations, j'en explore {n}.\n")

    summary = []
    for i in range(n):
        href = await _click_conv(page, i)
        if href is None:
            break
        print(f"[{i+1}/{n}] clic → {href}")
        await asyncio.sleep(4)  # laisse le fil se charger

        data = await page.evaluate(JS_CHAT)
        _write_json(f"explore2_conv_{i}.json", data)
        nb = len(data.get("bubbles", []))
        print(f"    → {nb} bulles, conteneur={bool(data.get('container'))}, "
              f"qaIds={data.get('qaIds', [])[:6]}")
        summary.append({"href": href, "bubbles": nb})
        await asyncio.sleep(2)

    _write_json("explore2_summary.json", summary)
    print("\n  Terminé. Dumps : debugs/explore2_conv_*.json")
    await asyncio.sleep(5)
    await ctx.close()


async def cmd_sides(pw, args):
    print("=" * 70)
    print("  EXPLORATION — messages sortants + non-lus")
    print("=" * 70)
    ctx = await _open_context(pw)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await _goto_messages(page)

    result = {"list": await page.evaluate(JS_LIST), "threads": []}
    print("\n— Liste des conversations —")
    for it in result["list"]["items"]:
        print(f'  [{it["i"]}] badge={it["badge"]}')
        print(f'      {it["text"][:90]!r}')
    print(f'  dots={result["list"]["dots"]}')

    links = await page.query_selector_all('a[href*="/messages/id/"]')
    n = min(len(links), args.max)
    found_out = False
    for i in range(n):
        href = await _click_conv(page, i)
        if href is None:
            break
        await asyncio.sleep(3.5)
        th = await page.evaluate(JS_THREAD)
        sides = [b["side"].get("byClass") or b["side"].get("byJustify") for b in th["bubbles"]]
        has_out = any(s == "out" for s in sides)
        found_out = found_out or has_out
        th["href"] = href
        result["threads"].append(th)
        print(f'\n[{i+1}/{n}] {href.split("/")[-1]}  bulles={len(th["bubbles"])} '
              f'sides={sides} sendDisabled={th["sendDisabled"]} '
              f'aria-disabled={th["sendAriaDisabled"]}{"   <<< SORTANT TROUVÉ" if has_out else ""}')
        for b in th["bubbles"][:6]:
            s = b["side"]
            print(f'      cls_side={s.get("byClass")} justify={s.get("byJustify")} '
                  f'li={b["liCls"]}  {b["text"][:70]!r}')
        await asyncio.sleep(1.5)

    print(f"\n  >>> Message sortant trouvé : {found_out}")
    last = result["threads"][-1] if result["threads"] else None
    print("  textarea attrs:", last["textareaAttrs"] if last else None)
    print("  send attrs    :", last["sendAttrs"] if last else None)

    _write_json("explore3.json", result)
    print("\n  Dump : debugs/explore3.json")
    await asyncio.sleep(4)
    await ctx.close()


# ── Point d'entrée ───────────────────────────────────────────────────────────

COMMANDS = {"dump": cmd_dump, "threads": cmd_threads, "sides": cmd_sides}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="explore.py",
        description="Diagnostic du DOM Leboncoin (lecture seule, Chrome réel, profil du bot). "
                    "Les fichiers écrits dans debugs/ contiennent des données personnelles.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="ne pas demander confirmation avant d'écrire dans debugs/ "
                        "(à placer AVANT la sous-commande)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dump", help="liste des conversations + 1re conversation (HTML/PNG/JSON)")
    t = sub.add_parser("threads", help="anatomie du fil de discussion sur plusieurs conversations")
    t.add_argument("--max", type=int, default=5, help="nombre de conversations (défaut 5)")
    s = sub.add_parser("sides", help="côté entrant/sortant des bulles + non-lus")
    s.add_argument("--max", type=int, default=12, help="nombre de conversations (défaut 12)")
    return p


async def main(args):
    import instance
    if instance.refuser_si_occupe("Ce diagnostic (explore)"):
        return

    print("\n" + "!" * 70)
    print(f"  ATTENTION : ce diagnostic écrit dans {OUT_DIR}")
    print("  Ces fichiers contiennent des DONNÉES PERSONNELLES (messages, noms,")
    print("  HTML de ta messagerie). Ne les partage pas et ne les commite pas.")
    print("!" * 70)
    if not args.yes:
        try:
            rep = input("  Continuer ? [o/N] ").strip().lower()
        except EOFError:
            rep = ""
        if rep not in ("o", "oui", "y", "yes"):
            print("  Annulé.")
            return

    from playwright.async_api import async_playwright  # import tardif : --help sans Playwright
    async with async_playwright() as pw:
        await COMMANDS[args.cmd](pw, args)


if __name__ == "__main__":
    asyncio.run(main(build_parser().parse_args()))
