"""
js.py — Sélecteurs et scripts JavaScript évalués dans la page Leboncoin.

Les sélecteurs sont définis UNE fois ici ; les scripts JS les reçoivent via
des marqueurs (__CONV_LINK__, __BUBBLE__) remplacés à l'import. Aucune logique
Python : uniquement des constantes (leboncoin.py les ré-exporte).
"""

from captcha import CAPTCHA_JS_EXPR

LBC_HOME = "https://www.leboncoin.fr/"
LBC_MESSAGES = "https://www.leboncoin.fr/messages"

CONV_LINK = 'a[href*="/messages/id/"]'
BUBBLE = '[data-qa-id="message-bubble"]'
TEXTAREA = '[data-spark-component="textarea"]'
SEND_BTN = '[data-qa-id="message-send"]'


def _sel(js: str) -> str:
    """Injecte les sélecteurs partagés dans un script JS."""
    return js.replace("__CONV_LINK__", CONV_LINK).replace("__BUBBLE__", BUBBLE)


# ── JS ───────────────────────────────────────────────────────────────────────

# État de la page : captcha DataDome ? page de connexion ?
# - `captcha` : motifs partagés avec captcha.is_captcha_present (CAPTCHA_JS_EXPR).
# - `login`   : vrai SEULEMENT sur l'URL d'authentification (auth.leboncoin.fr,
#   /login, /connexion) ou dans un VRAI formulaire de connexion (un <form> qui
#   contient un champ mot de passe ET un identifiant e-mail/utilisateur). Un
#   simple input[type=password] (modale de confirmation, etc.) ne suffit plus ;
#   la page de connexion réelle reste détectée par son URL.
JS_STATE = r"""() => {
  const formConnexion = [...document.querySelectorAll('form')].some(f =>
    f.querySelector('input[type="password"]')
    && f.querySelector('input[type="email"], input[name*="mail" i], input[name*="user" i], input[autocomplete="username"]'));
  return {
    url: location.href,
    captcha: __CAPTCHA_EXPR__,
    login: /auth\.leboncoin\.fr|\/(login|connexion)(\/|\?|$)/.test(location.href)
      || formConnexion,
  };
}""".replace("__CAPTCHA_EXPR__", CAPTCHA_JS_EXPR)

# Liste des conversations + détection du non-lu + aperçu du dernier message
JS_LIST = _sel(r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const badgeOf = a => {
    let p = a;
    for (let i = 0; i < 6 && p; i++) {
      if (p.querySelectorAll('__CONV_LINK__').length > 1) break;
      const b = p.querySelector('[data-spark-component="badge"]');
      if (b) {
        const aria = b.getAttribute('aria-label') || '';
        const title = b.getAttribute('title') || '';
        const txt = clean(b.innerText);
        if (aria || title || txt) return (aria + ' ' + title + ' ' + txt).toLowerCase();
      }
      p = p.parentElement;
    }
    return '';
  };
  return {
    url: location.href,
    items: [...document.querySelectorAll('__CONV_LINK__')].map(a => {
      const raw = clean(a.innerText);
      return {
        href: a.getAttribute('href'),
        raw: raw.slice(0, 220),
        preview: raw.replace(/\s*-\s*il y a .*$/i, '').trim(),
        unread: /non lu|notification/.test(badgeOf(a)),
      };
    }),
  };
}""")

# Zone de la liste (ancêtre scrollable du 1er lien, sinon le lien lui-même)
JS_LIST_BOX = _sel(r"""() => {
  const a = document.querySelector('__CONV_LINK__');
  if (!a) return null;
  let el = a.parentElement;
  while (el && el !== document.body) {
    const oy = getComputedStyle(el).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 4) break;
    el = el.parentElement;
  }
  const r = (el && el !== document.body ? el : a).getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}""")

# Fil de discussion : bulles + côté (entrant / sortant) + messages automatiques
JS_THREAD = _sel(r"""() => {
  const clean = s => (s || '').replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();

  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    // Seuil volontairement bas : une bulle courte fait ~20x37px
    if (r.width < 20 || r.height < 8) return false;
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };

  // Conteneur du fil = <ol> scrollable VISIBLE qui contient le plus de bulles.
  // (le SPA laisse parfois l'ancien fil dans le DOM : on ne veut que le visible)
  let container = null, best = -1;
  for (const o of document.querySelectorAll('ol')) {
    if (!visible(o) || !/auto|scroll/.test(getComputedStyle(o).overflowY)) continue;
    const n = o.querySelectorAll('__BUBBLE__').length;
    if (n >= best) { best = n; container = o; }
  }
  const root = container || document;

  const sideOf = b => {
    const cls = b.className || '';
    if (cls.includes('rounded-br-none')) return 'out';
    if (cls.includes('rounded-bl-none')) return 'in';
    const p = b.parentElement;
    if (p) {
      const j = getComputedStyle(p).justifyContent;
      if (j === 'flex-end') return 'out';
      if (j === 'flex-start') return 'in';
    }
    if (container) {
      const br = b.getBoundingClientRect(), or = container.getBoundingClientRect();
      return (br.left + br.width / 2) > (or.left + or.width / 2) ? 'out' : 'in';
    }
    return 'in';
  };

  const nodes = [...root.querySelectorAll('__BUBBLE__')].filter(visible);
  const all = nodes.map(b => ({ text: clean(b.innerText), side: sideOf(b) }));
  const last = all.length ? all[all.length - 1] : null;

  // Suite finale de messages de l'ACHETEUR (non répondus)
  const trailingIn = [];
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].side !== 'in') break;
    trailingIn.unshift(all[i].text);
  }

  // Messages AUTOMATIQUES leboncoin = éléments du fil SANS bulle de message.
  // On en distingue :
  //   • l'en-tête « À propos de cet acheteur / ce vendeur » (donne NOTRE rôle) ;
  //   • le bandeau « Envoyez une réponse rapide » (pré-réponses proposées) ;
  //   • les vrais messages système (offre reçue, disponibilité…).
  const sysTexts = [];
  const quickReplies = [];
  let counterpart = null;
  if (container) {
    for (const li of container.children) {
      if (li.querySelector('__BUBBLE__')) continue;
      const t = clean(li.innerText);
      if (!t) continue;
      if (/^À propos de cet acheteur/i.test(t)) { counterpart = 'acheteur'; continue; }
      if (/^À propos de ce vendeur/i.test(t)) { counterpart = 'vendeur'; continue; }
      if (/Envoyez une réponse rapide/i.test(t)) {
        const chips = [...li.querySelectorAll('[data-spark-component="chip"]')]
          .map(c => clean(c.innerText)).filter(Boolean);
        if (chips.length) quickReplies.push(...chips);
        else {
          const s = t.replace(/^Envoyez une réponse rapide\s*/i, '').trim();
          if (s) quickReplies.push(s);
        }
        continue;
      }
      if (t.length < 400) sysTexts.push(t);
    }
  }

  // Une offre se reconnaît UNIQUEMENT dans les messages automatiques du fil
  // (jamais dans les bulles : notre propre réponse contient le mot « offre »,
  // et la bannière cookies contient un bouton « Accepter »).
  const sysJoined = sysTexts.join('\n');
  const hasOffer = /Offre reçue|Vous avez\s+\d+\s*heures?\s+pour accepter|Accepter\s+\d+\s*€/i
    .test(sysJoined);

  return {
    url: location.href,
    count: all.length,
    scrollHeight: container ? container.scrollHeight : 0,
    lastSide: last ? last.side : null,
    lastText: last ? last.text : null,
    buyersText: (last && last.side === 'in' && trailingIn.length)
      ? trailingIn.join('\n') : null,
    // Le fil complet, dans l'ordre : c'est LUI qui fait foi pour le contexte
    // (il contient les messages envoyés à la main, que la base ignore).
    messages: all.slice(-40),
    // La page n'affiche que les derniers messages : au-delà, la base complète.
    hasMoreAbove: container
      ? (container.scrollHeight > container.clientHeight + 40) : false,
    hasOffer,
    counterpart,
    quickReplies: quickReplies.slice(0, 5),
    systemTexts: sysTexts.slice(0, 5),
  };
}""")

# Attente d'ouverture d'un fil (argument : id de la conversation). Prêt dès que
# l'URL correspond ET qu'il contient une bulle de message, soit un message
# automatique leboncoin (offre, rappel de disponibilité…) qui n'est PAS une bulle.
JS_THREAD_READY = _sel("""(id) => location.pathname.includes(id) && (
                    document.querySelector('__BUBBLE__')
                    || /Message automatique leboncoin|Offre reçue|s'intéresse à votre annonce/i
                         .test(document.body.innerText)
                )""")

