"""
selftest_send.py — Diagnostic + test réel de l'ENVOI de message.

But : isoler et prouver la chaîne d'envoi (champ → bouton → bulle sortante)
sur UNE seule conversation, sans jamais spammer.

Modes :
  python -u selftest_send.py                 # DIAGNOSTIC SEUL (n'envoie RIEN)
  python -u selftest_send.py --confirm       # envoie réellement le message

Options :
  --conv <uuid>   cible une conversation précise
  --text "..."    texte à taper (défaut : message de test neutre)

En mode diagnostic, le programme va jusqu'à vérifier que le bouton d'envoi
s'active, puis VIDE le champ et s'arrête. Aucun envoi.
"""

import os
import sys

# Modules du bot : à la racine du projet (ce fichier est dans tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("asyncio").setLevel(logging.WARNING)

from leboncoin import (  # noqa: E402
    LeboncoinClient, TEXTAREA, SEND_BTN, BUBBLE, JS_THREAD,
)
from config import BASE_DIR  # noqa: E402

_DBG = os.path.join(BASE_DIR, "debugs")
os.makedirs(_DBG, exist_ok=True)

JS_COMPOSER = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 5 && r.height > 5 && cs.display !== 'none'
      && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };
  const list = sel => [...document.querySelectorAll(sel)].map((e, i) => {
    const r = e.getBoundingClientRect();
    return {
      i, visible: vis(e), disabled: e.disabled === true,
      ariaDisabled: e.getAttribute('aria-disabled'),
      placeholder: e.getAttribute('placeholder'),
      value: (e.value !== undefined ? e.value : null),
      text: clean(e.innerText).slice(0, 80),
      box: { x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height) },
    };
  });
  // Chips (pré-réponses éventuelles de leboncoin) proches du compositeur
  const chips = [...document.querySelectorAll('[data-spark-component="chip"]')]
    .filter(vis).map(c => clean(c.innerText)).filter(Boolean);
  return {
    textareas: list('[data-spark-component="textarea"]'),
    allTextareaTags: list('textarea'),
    sendButtons: list('[data-qa-id="message-send"]'),
    anySendLike: list('[aria-label*="nvoyer"], [title*="nvoyer"]'),
    chips,
    activeTag: document.activeElement ? document.activeElement.tagName : null,
  };
}"""


def show(title, obj):
    print(f"\n  ── {title}")
    print("  " + repr(obj).replace("\\n", "\n  "))


async def main():
    import instance
    if instance.refuser_si_occupe("Ce test d'envoi (selftest_send)"):
        return

    args = sys.argv[1:]
    confirm = "--confirm" in args
    conv_id = None
    text = None
    if "--conv" in args:
        conv_id = args[args.index("--conv") + 1]
    if "--text" in args:
        text = args[args.index("--text") + 1]

    lbc = LeboncoinClient()
    await lbc.start()
    try:
        if not await lbc.ensure_ready():
            print("❌ Session non connectée.")
            return

        # ── Choisir la conversation ──────────────────────────────────────────
        if not conv_id:
            msgs = await lbc.get_new_messages()
            if msgs:
                conv_id = msgs[0]["id_conv"]
                print(f"\n▶️ Cible = 1re conversation en attente : {conv_id}")
            else:
                conv_id = "112baf4f-d6a8-4adb-917e-de146be19fb7"
                print(f"\n▶️ Aucune en attente → cible par défaut : {conv_id}")

        conv_url = f"https://www.leboncoin.fr/messages/id/{conv_id}"
        if not text:
            text = "Bonjour, merci pour votre message, je vous réponds rapidement."

        print(f"\n{'=' * 72}\n  TEST D'ENVOI — conv {conv_id}\n  mode : {'ENVOI RÉEL' if confirm else 'DIAGNOSTIC (aucun envoi)'}\n{'=' * 72}")

        if not await lbc._open_thread(conv_url):
            print("❌ Impossible d'ouvrir le fil.")
            return
        print(f"✅ Fil ouvert : {lbc.page.url}")

        before = await lbc.page.evaluate(JS_THREAD)
        show("fil AVANT (rôle, count, lastSide, lastText, offer)", {
            k: before.get(k) for k in
            ("counterpart", "count", "lastSide", "lastText", "hasOffer",
             "quickReplies", "systemTexts")
        })

        comp = await lbc.page.evaluate(JS_COMPOSER)
        show("compositeur AVANT", comp)

        # ── Vider le champ (une pré-réponse a pu le pré-remplir) ─────────────
        champ = await lbc.page.query_selector(TEXTAREA)
        if not champ:
            print("❌ Aucun textarea trouvé via TEXTAREA selector.")
            return
        await champ.click()
        await lbc.page.keyboard.press("Control+A")
        await lbc.page.keyboard.press("Delete")
        await asyncio.sleep(0.4)

        # ── Taper ────────────────────────────────────────────────────────────
        await lbc.page.keyboard.type(text, delay=25)
        await asyncio.sleep(1.0)
        comp2 = await lbc.page.evaluate(JS_COMPOSER)
        show("compositeur APRÈS frappe", comp2)

        typed_ok = any(
            (t.get("value") or "").strip() == text.strip() for t in comp2["textareas"]
        )
        print(f"\n  ► champ contient le texte attendu : {'OUI ✅' if typed_ok else 'NON ❌'}")

        btn = await lbc.page.query_selector(SEND_BTN)
        if not btn:
            print("❌ Bouton d'envoi introuvable.")
        else:
            state = await btn.evaluate(
                "b => ({disabled: !!b.disabled, aria: b.getAttribute('aria-disabled')})"
            )
            print(f"  ► bouton d'envoi : {state}")
            enabled = (not state["disabled"]) and state["aria"] != "true"
            print(f"  ► bouton ACTIF   : {'OUI ✅' if enabled else 'NON ❌'}")

        await lbc.page.screenshot(
            path=os.path.join(_DBG, "selftest_send_before.png"), full_page=True)
        print(f"  📸 {os.path.join(_DBG, 'selftest_send_before.png')}")

        if not confirm:
            # Nettoyage obligatoire : on repart sans rien laisser
            await champ.click()
            await lbc.page.keyboard.press("Control+A")
            await lbc.page.keyboard.press("Delete")
            await asyncio.sleep(0.5)
            left = await champ.input_value()
            print(f"\n  🧹 champ vidé : {left!r}")
            print("\n  ✅ DIAGNOSTIC TERMINÉ — aucun message envoyé.\n")
            return

        # ── ENVOI RÉEL ───────────────────────────────────────────────────────
        print("\n  ✉️ Clic sur Envoyer…")
        for _ in range(24):
            d = await btn.evaluate(
                "b => b.disabled || b.getAttribute('aria-disabled') === 'true'"
            )
            if not d:
                break
            await asyncio.sleep(0.25)
        await btn.click(timeout=10000)
        await asyncio.sleep(3.0)

        after = await lbc.page.evaluate(JS_THREAD)
        show("fil APRÈS", {
            k: after.get(k) for k in
            ("count", "lastSide", "lastText", "hasOffer")
        })
        wait_ok = after.get("lastSide") == "out"
        print(f"\n  ► dernier message = nous : {'OUI ✅' if wait_ok else 'NON ❌'}")

        # Attendre que le serveur confirme (la bulle peut apparaître en optimiste)
        for _ in range(12):
            await asyncio.sleep(1.5)
            cur = await lbc.page.evaluate(JS_THREAD)
            if cur.get("lastSide") == "out":
                break
        await lbc.page.screenshot(
            path=os.path.join(_DBG, "selftest_send_after.png"), full_page=True)
        print(f"  📸 {os.path.join(_DBG, 'selftest_send_after.png')}")

        print(f"\n  ► état final : count={cur.get('count')} lastSide={cur.get('lastSide')}")
        print("  " + ("✅ ENVOI CONFIRMÉ" if cur.get("lastSide") == "out" else "❌ ENVOI NON CONFIRMÉ"))
    finally:
        await lbc.close()


if __name__ == "__main__":
    asyncio.run(main())
