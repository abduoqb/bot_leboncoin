"""
explore3.py — Diagnostic ciblé n°3.

1) Trouver un fil contenant un message du VENDEUR (sortant) pour établir la
   règle de distinction entrant/sortant de façon certaine.
2) Cartographier la détection des conversations NON LUES sur la page liste.

Sortie : debugs/explore3.json
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
MAX_CONVS = 12

JS_STATE = r"""() => ({
  url: location.href,
  loginForm: !!document.querySelector('input[type="password"]'),
  captcha: !!document.querySelector('iframe[src*="captcha-delivery"], #cmsg'),
})"""

# Liste : liens + badges non-lus
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

# Fil : côté de chaque bulle + classe du li porteur
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


async def main():
    async with async_playwright() as pw:
        print("=" * 70)
        print("  EXPLORATION 3 — messages sortants + non-lus")
        print("=" * 70)
        ctx = await pw.chromium.launch_persistent_context(
            USER_DATA_DIR, channel="chrome", headless=False,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            no_viewport=True, locale="fr-FR",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.leboncoin.fr/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.goto("https://www.leboncoin.fr/messages", wait_until="domcontentloaded")
        await asyncio.sleep(4)

        result = {"list": await page.evaluate(JS_LIST), "threads": []}
        print("\n— Liste des conversations —")
        for it in result["list"]["items"]:
            print(f'  [{it["i"]}] badge={it["badge"]}')
            print(f'      {it["text"][:90]!r}')
        print(f'  dots={result["list"]["dots"]}')

        links = await page.query_selector_all('a[href*="/messages/id/"]')
        n = min(len(links), MAX_CONVS)
        found_out = False
        for i in range(n):
            links = await page.query_selector_all('a[href*="/messages/id/"]')
            if i >= len(links):
                break
            href = await links[i].get_attribute("href")
            try:
                await links[i].click()
            except Exception:
                await links[i].click(force=True)
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
        print("  textarea attrs:", result["threads"][-1]["textareaAttrs"] if result["threads"] else None)
        print("  send attrs    :", result["threads"][-1]["sendAttrs"] if result["threads"] else None)

        os.makedirs(OUT_DIR, exist_ok=True)
        with open(f"{OUT_DIR}/explore3.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("\n  Dump : debugs/explore3.json")
        await asyncio.sleep(4)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
