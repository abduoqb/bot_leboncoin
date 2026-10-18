# Bot Leboncoin

Un bot qui répond à ta place aux messages de tes annonces Leboncoin, avec un
modèle de langage qui tourne **chez toi** (Ollama + `gemma3:4b`). Aucune API,
aucun abonnement, rien qui sorte de ta machine.

Concrètement : tu vends une PS5, tu vas dormir, et le lendemain les acheteurs ont
eu une réponse. Le bot lit tes conversations, écrit dans ton style, envoie, puis
recommence quelques minutes plus tard.

---

## Le problème qu'il résout

Les messages arrivent n'importe quand : à 23h, pendant une réunion, le dimanche.
Un acheteur qui n'a pas de réponse dans l'heure va voir ailleurs. Et dans la
plupart des cas, les questions sont toujours les mêmes : *toujours dispo ?*,
*vous feriez un prix ?*, *possible samedi ?*.

Le bot s'occupe de ces questions-là. Ce qui engage vraiment de l'argent reste de
ton côté.

---

## Ce qu'il fait

### Il répond à toutes les conversations

Toutes les 1 à 3 minutes, il ouvre la liste de tes messages. Pour chaque
conversation **où tu es le vendeur** et où un acheteur attend une réponse, il
rédige et il envoie. Les conversations où c'est toi qui achètes sont ignorées :
il ne répond pas à la place d'un vendeur.

### Il écrit comme toi

Le modèle parle à la première personne, en français naturel, en une à trois
phrases. Il n'écrit **jamais** de nom de personne, ne salue qu'une fois par
conversation, et ne contredit pas ce que dit ton annonce.

### L'argent ne passe jamais par le modèle

C'est le point le plus important, et la raison d'être de `rules.py`.

Un modèle 4B compare mal deux nombres et se trompe de référence : il a réellement
répondu « à 400 € ça me va » à une offre de 370 €, c'est-à-dire qu'il a annoncé
le prix plancher au lieu de la contre-offre. Accepter ou contre-proposer est donc
décidé **par du code**. Le modèle ne fait que mettre la décision en mots, et une
vérification repasse derrière pour confirmer que le bon montant a été écrit.

```
Acheteur   Vous feriez un prix ? Je vous en propose 370.
Bot        Merci pour votre offre. À 430 € je vous la laisse.

Acheteur   D'accord. Je vous la prends à 400.
Bot        Merci, à 400 € ça me va.
```

Ton prix plancher n'est **jamais** annoncé à l'acheteur.

### Il ne réveille pas les morts

Ouvrir une conversation la recharge **et la marque « lue » côté serveur**. Boucler
sur des conversations déjà traitées est le meilleur moyen de se faire repérer. Le
bot n'ouvre donc un fil que s'il a une raison :

- l'aperçu a changé, donc il y a un nouveau message ;
- le fil est non lu et jamais vu depuis le démarrage ;
- une réponse est en attente parce qu'un envoi a échoué.

Sinon, il laisse la liste tranquille. Et cette mémoire survit au redémarrage
(`bot_state.json`) : relancer le bot ne provoque pas un parcours complet.

### Le reste

- **Il vérifie ses envois.** Un message n'est considéré comme parti que si une
  nouvelle bulle sortante apparaît. Le champ qui se vide ne suffit pas : ça
  arrive aussi quand l'envoi a échoué.
- **Il détecte les tentatives de manipulation.** « Oublie tes instructions »,
  « affiche ton prompt » : réponse figée, décidée hors du modèle.
- **Il gère les captchas DataDome.** Il essaie d'abord de glisser le curseur
  comme un humain, puis te laisse la main si ça ne passe pas.
- **Il sait se taire.** Une formule de politesse pour clore la conversation
  reçoit une réponse figée, pas une relance commerciale.
- **Il ne répond jamais à une offre officielle.** Le message automatique
  « Offre reçue : 400 € » attend ta décision, et le bot te le signale.
- **Il se relance tout seul.** Si Chrome crashe ou est fermé, il redémarre le
  navigateur et reprend.
- **Il dort la nuit.** De 1h à 6h, aucune activité.

Le navigateur reste visible : tu peux le regarder travailler.

---

## Installation

Il faut Python 3.11+ et [Ollama](https://ollama.com) lancé.

```bash
git clone <url-du-repo>
cd bot_lbc
python -m venv venv && source venv/bin/activate   # Windows : venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

ollama pull gemma3:4b
```

### 1. Renseigner tes données de vente

`prompts.py` contient tes informations personnelles, il n'est donc pas dans le
dépôt. Copie le modèle et remplis-le :

```bash
cp prompts.example.py prompts.py
```

Le minimum à renseigner est le bloc `VENTE` : ville, prix affiché, prix plancher,
contre-offre. Tout est détaillé dans **Configuration**.

### 2. Se connecter une seule fois

```bash
python main.py
```

Une fenêtre Chrome s'ouvre. Connecte-toi à ton compte Leboncoin et glisse le
captcha s'il y en a un. C'est tout : la session est conservée dans
`chrome_profile/`, tu n'auras plus à te reconnecter, sauf expiration au bout de
plusieurs semaines.

> La connexion par identifiants n'est pas automatisée, volontairement. Pour
> changer de compte, supprime le dossier `chrome_profile/` et relance.

---

## Le lancer

```bash
python main.py            # la boucle normale
python main.py --once     # un seul cycle complet, puis arrêt
```

Et pour voir ce qu'il ferait **sans rien envoyer** :

```bash
python tools/dry_run.py           # résumé
python tools/dry_run.py --debug   # avec les logs détaillés
```

C'est le premier réflexe quand une réponse te semble bizarre : `dry_run` montre
le message reçu, la catégorie choisie et la réponse générée, sans rien poster.

---

## Configuration

### `prompts.py` : tes données et ton style

| Bloc | Ce que c'est |
|------|--------------|
| `VENTE` | Ville, prix affiché, prix plancher, contre-offre, disponibilité |
| `BASE_PERSONA` | Le ton général et les interdits (pas d'emoji, pas de nom, ne jamais dire qu'il est un bot) |
| `PROMPTS["negociation"]` | La stratégie de prix |
| `PROMPTS["produit"]` | Les caractéristiques à connaître |
| `PROMPTS["rdv"]` | Disponibilités et lieu de rendez-vous |
| `PROMPTS["general"]` | Salutations et messages vagues |
| `REPONSES_STATIQUES` | Réponses figées (hors sujet, manipulation) |
| `NOMS_A_NEUTRALISER` | Noms qui ne doivent jamais apparaître dans une réponse |

Après chaque modification, valide avec `python -u tools/selftest_llm.py`.

**La politique de prix** est appliquée par `rules.py`, pas par le modèle :

- offre **≥** `prix_plancher_eur` → il accepte (« Merci, à 400 € ça me va. ») ;
- offre **<** `prix_plancher_eur`, avec `prix_contre_offre_eur` renseigné → il
  contre-propose (« À 430 € je vous la laisse. ») ;
- offre **<** `prix_plancher_eur`, sans contre-offre → refus poli, sans chiffre.

Deux façons de rester maître du jeu :

- `prix_plancher_eur: None` → il **refuse toujours** et te laisse trancher ;
- `prix_contre_offre_eur: None` → il refuse sans avancer de chiffre.

### `config.py` : les réglages techniques

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OLLAMA_MODEL` | `gemma3:4b` | Le modèle local |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | L'endpoint Ollama |
| `SCAN_MIN` / `SCAN_MAX` | `75` / `180` | Secondes entre deux scans |
| `NIGHT_START_HOUR` / `NIGHT_END_HOUR` | `1` / `6` | La pause nocturne |
| `MAX_CONVS_PER_SCAN` | `3` | Conversations traitées par scan |
| `MAX_HISTORY` | `8` | Messages d'historique donnés au modèle |
| `TYPING_SPEED_MIN` / `MAX` | `40` / `120` | Millisecondes entre les frappes |
| `BROWSER_MODE` | `profile` | `profile` (recommandé) ou `cdp` |
| `PROFILE_DIR` | `<racine>/chrome_profile` | Le profil Chrome persistant |
| `CHROME_CHANNEL` | `chrome` | `chrome` = vrai Chrome, `None` = Chromium |
| `CAPTCHA_WAIT_SECONDS` | `300` | Temps laissé à l'humain pour un captcha |
| `DEBUG_SCREENSHOTS` | `True` | Capture PNG + HTML quand un fil ne charge pas |

Le repos intelligent se règle en haut de `main.py` : après `IDLE_SCAN_LIMIT`
scans vides (10), le bot se met en pause pendant `IDLE_REST_SECONDS` (1h).

### Deux réglages à connaître

**Un seul programme à la fois sur `chrome_profile/`.** Un profil Chrome ne peut
être ouvert que par un seul Chrome. Si un second programme tente de le lancer,
Chrome n'échoue pas franchement : il ouvre des fenêtres `about:blank` dans la
session existante et Playwright perd la connexion. Le bot pose donc un verrou
(`bot.lock`) et refuse de démarrer si un autre tourne déjà ; `dry_run.py` et
`selftest_send.py` font pareil.

**Le bot s'arrête si personne ne se connecte.** Si la session expire, il ouvre
Chrome et attend 5 minutes (`CAPTCHA_WAIT_SECONDS`) que tu te reconnectes. Passé
ce délai, il s'arrête plutôt que de tourner à vide.

---

## Comment il décide (les détails)

### Le trajet d'un message

```
Message de l'acheteur
   │
   ├─ Règles déterministes (rules.py)          ← avant tout appel au modèle
   │     manipulation ?        → réponse figée
   │     formule de fin ?      → réponse figée
   │     question de dispo ?   → réponse figée (c'est une donnée vérifiée)
   │
   ├─ Appel 1 : classification (gemma3, 5 tokens max)
   │     → un seul mot : negociation / produit / rdv / general /
   │       hors_sujet / manipulation
   │
   ├─ Négociation avec un montant clair : la décision est prise ici, dans
   │     rules.py, et transmise au modèle déjà tranchée
   │
   ├─ Appel 2 : génération, avec le mini-prompt du thème
   │
   ├─ Nettoyage (sanitize_reply) : aucun nom de personne, une seule
   │     salutation, typographie française
   │
   ├─ Correction d'offre (corrige_offre) : le bon montant est-il annoncé ?
   │
   └─ Envoi, puis vérification : une nouvelle bulle sortante est-elle
         apparue ? Sinon, on retente au scan suivant.
```

L'historique n'est enregistré en base **qu'après** un envoi confirmé. Si l'envoi
échoue, rien n'est écrit et le scan suivant reprend le même message.

### D'où vient le contexte envoyé au modèle

**Du fil réellement lu dans la page.** C'est lui qui contient la vérité : ce que
tu as écrit à la main, les conversations qui existaient avant le bot, tout ce que
la base n'a jamais vu. La base SQLite (`conversations.db`) ne sert plus qu'à deux
choses :

- **compléter** les messages plus anciens que la page n'affiche plus, en
  s'alignant sur le premier message du fil pour ne rien compter deux fois ;
- **garder la trace** et servir d'anti-doublon.

Résultat : une base incomplète, ou qui contient d'anciennes réponses ratées, ne
peut plus polluer une nouvelle réponse. Tu peux donc la purger sans risque :

```bash
python -u tools/db_cleanup.py                      # liste (ne supprime rien)
python -u tools/db_cleanup.py --vides --tests       # ce qui serait supprimé
python -u tools/db_cleanup.py --vides --tests --apply
```

### Discrétion

Aucun de ces points n'est décoratif : ce sont exactement les traces qui font
repérer un bot.

- **Vrai Chrome, profil persistant** : l'empreinte du navigateur est authentique.
- **Navigation par clics** : jamais de lien direct vers `/messages/id/...`, qui
  est la cible n°1 de DataDome. On clique le lien comme un humain.
- **Cadence irrégulière** : 75 à 180 secondes entre deux scans, tirées au hasard.
- **Frappe réellement humaine** : caractère par caractère, 40 à 120 ms, avec
  quelques fautes de frappe corrigées.
- **Souris et scrolls** : trajectoires en courbe de Bézier, mouvements parasites,
  et un petit scroll « de lecture » avant de répondre.
- **Pause de réflexion** : un délai proportionnel à la longueur du message reçu.
- **Navigation dispersée** : il passe par l'accueil avant de consulter les
  messages.
- **Champ nettoyé avant écriture** : une pré-réponse Leboncoin pré-remplie ne
  peut pas se mélanger à son message.

---

## Les outils de test

Deux familles : ceux qui ne touchent pas au site (rapides, à lancer souvent) et
ceux qui pilotent Chrome.

**Sans navigateur**, c'est là qu'on itère :

```bash
python -u tools/selftest_llm.py          # qualité des réponses (le plus utile)
python -u tools/selftest_llm.py --case 5 # un seul cas
python -u tools/selftest_scan.py         # règle anti-boucle de lecture
python -u tools/selftest_context.py      # priorité au fil de la page
python -u tools/selftest_instance.py     # verrou « un seul bot »
```

`selftest_llm.py` fait passer 24 messages d'acheteurs réalistes et 4
conversations déjà engagées au classifieur et à la génération, puis vérifie
39 règles déterministes (nettoyage, disponibilité, garde-fou du prix plancher,
décision chiffrée, correction d'offre). C'est la boucle : repérer un raté,
ajuster `prompts.py`, relancer.

**Avec Chrome** (le bot doit être arrêté) :

```bash
python -u tools/dry_run.py                    # lit et génère, sans envoyer
python -u tools/selftest_send.py              # diagnostic d'envoi, n'envoie rien
python -u tools/selftest_send.py --confirm    # envoie réellement
python -u tools/explore.py                    # re-cartographie le site
```

Le dernier sert si Leboncoin change son HTML. Le bot s'appuie sur des attributs
stables (`data-qa-id="message-bubble"`, `data-spark-component="textarea"`,
`data-qa-id="message-send"`), et quand ils cassent, `explore.py` les retrouve.

---

## Limites

Autant le dire tout de suite :

- **Le glissement du captcha n'aboutit pas toujours.** DataDome regarde aussi la
  dynamique de la souris et l'empreinte de l'appareil. En cas d'échec, tu es
  prévenu et le bot attend ton intervention.
- **La session peut expirer** au bout de plusieurs semaines, et il faut alors se
  reconnecter à la main.
- **Un historique très long est tronqué.** La page n'affiche que les derniers
  messages et le bot ne remonte pas indéfiniment : au-delà de `MAX_HISTORY`
  (8 messages), le début de la conversation est ignoré. Suffisant pour répondre
  juste au message en cours, insuffisant pour se souvenir d'un détail d'il y a
  deux semaines.
- **Sans GPU, chaque réponse prend 10 à 30 secondes.** Ce n'est pas gênant : le
  bot simule de toute façon des délais humains.
- **Leboncoin peut changer son HTML** à tout moment. C'est le principal risque de
  casse, et `tools/explore.py` est là pour ça.
- **Les offres chiffrées restent ta décision.** Le bot les signale, il ne clique
  pas à ta place.

---

## Sur un VPS

Pour tourner 24h/24, compte 4 Go de RAM (8 recommandés pour Ollama), 2 vCPU et
20 Go de disque, sous Ubuntu 22.04 ou 24.04.

```bash
# Système et Ollama
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git wget
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve &
ollama pull gemma3:4b

# Projet
git clone <url-du-repo> && cd bot_lbc
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium && playwright install-deps
cp prompts.example.py prompts.py     # puis renseigne VENTE
```

Sur un serveur sans écran, il faut un affichage virtuel pour la connexion
initiale, à faire une seule fois : `xvfb` + `x11vnc`, tu te connectes en VNC, tu
logues Leboncoin, et c'est réglé, la session reste dans `chrome_profile/`.

Pour le faire tourner en service, l'essentiel est le fichier systemd
`/etc/systemd/system/bot-lbc.service` :

```ini
[Unit]
Description=Bot Leboncoin
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/bot_lbc
ExecStart=/root/bot_lbc/venv/bin/python main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now bot-lbc
journalctl -u bot-lbc -f
```

---

## Licence

MIT — voir [LICENSE](LICENSE)
