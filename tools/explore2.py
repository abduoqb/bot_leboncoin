"""
explore2.py — Diagnostic ciblé n°2 : anatomie du fil de discussion.

Objectif : déterminer de façon FIABLE comment distinguer
  - un message de l'ACHETEUR (entrant)
  - un message du VENDEUR (sortant / nos réponses)
  - un message SYSTÈME (offre, remise en main propre, etc.)
et récupérer tous les attributs stables (data-qa-id, aria-label, classe sémantique).

Ouvre le profil persistant (déjà connecté), passe en revue plusieurs
conversations en CLIQUANT dessus et dumpe chaque fil.

Usage : python -u explore2.py
Sortie : debugs/explore2_conv_<i>.json
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
MAX_CONVS = 5

JS_STATE = r"""() => {
  const body = (document.body && document.body.innerText) || '';
  return {
    url: location.href,
    loginForm: !!document.querySelector('input[type="password"]'),
    captcha: !!document.querySelector('iframe[src*="captcha-delivery"], #cmsg')
             || /Faites glisser|s'adresse bien . vous|un robot est sur le m/i.test(body),
  };
}"""

# Anatomie complète du fil de discussion
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


async def main():
    async with async_playwright() as pw:
        print("=" * 70)
        print("  EXPLORATION 2 — anatomie des fils de discussion")
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

        st = await page.evaluate(JS_STATE)
        print(f"  état: {st}")
        if st.get("captcha") or st.get("loginForm"):
            print("  ⚠️ captcha/login — connecte-toi puis relance ce script.")
            await asyncio.sleep(20)
            await ctx.close()
            return

        # Combien de conversations ?
        links = await page.query_selector_all('a[href*="/messages/id/"]')
        n = min(len(links), MAX_CONVS)
        print(f"  {len(links)} conversations, j'en explore {n}.\n")

        os.makedirs(OUT_DIR, exist_ok=True)
        summary = []

        for i in range(n):
            links = await page.query_selector_all('a[href*="/messages/id/"]')
            if i >= len(links):
                break
            href = await links[i].get_attribute("href")
            print(f"[{i+1}/{n}] clic → {href}")
            try:
                await links[i].click()
            except Exception:
                await links[i].click(force=True)
            await asyncio.sleep(4)  # laisse le fil se charger

            data = await page.evaluate(JS_CHAT)
            with open(f"{OUT_DIR}/explore2_conv_{i}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            temps = data.get("container")
            nb = len(data.get("bubbles", []))
            print(f"    → {nb} bulles, conteneur={bool(temps)}, "
                  f"qaIds={data.get('qaIds', [])[:6]}")
            summary.append({"href": href, "bubbles": nb})
            await asyncio.sleep(2)

        with open(f"{OUT_DIR}/explore2_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        print("\n  Terminé. Dumps : debugs/explore2_conv_*.json")
        await asyncio.sleep(5)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
