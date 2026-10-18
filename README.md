# Bot Leboncoin × Ollama

Bot Python qui répond automatiquement aux messages de tes annonces Leboncoin en utilisant un LLM local (Ollama / gemma3:4b). Zéro coût, tourne entièrement en local ou sur VPS.

---

## ✨ Fonctionnalités

- **Réponses naturelles et fiables** — le bot parle comme le vendeur (première personne), n'écrit jamais de nom de personne, ne salue qu'une fois par conversation, ne contredit jamais une donnée vérifiée et n'ouvre un fil que s'il y a une raison de le faire
- **LLM local 2-étapes** — classification du message (router) puis réponse spécialisée via gemma3:4b
- **Prix décidé en dur** — accepter une offre ou contre-proposer n'est jamais laissé au modèle : le budgétage (plancher / contre-offre) est calculé dans `rules.py`, le modèle ne fait que le formuler. Le prix plancher n'est jamais révélé
- **Envoi réel vérifié** — le bot tape et envoie; l'envoi n'est validé que par l'apparition d'une nouvelle bulle sortante (jamais par un simple vidage de champ)
- **Anti-détection avancée** — frappe humaine au clavier (avec fautes de frappe corrigées), mouvements de souris Bézier, scrolls naturels, navigation dispersée, pause nocturne, **aucune relecture inutile des conversations**
- **Captcha DataDome** — tentative de glissement automatique « humain » du slider, avec repli sur intervention humaine
- **Pré-réponses Leboncoin** — détectées, tracées, et utilisées en repli si le LLM ne produit rien
- **Ne répond jamais à la place d'un vendeur** — les conversations où TU es l'acheteur (« À propos de ce vendeur ») sont ignorées
- **Offres officielles Leboncoin** — le bot ne répond jamais à ce message automatique : c'est à toi de trancher (il te le signale dans les logs)
- **Auto-relance du navigateur** — si Chrome crashe ou est fermé, le bot redémarre le profil et reprend
- **Indépendant du répertoire courant** — profil, base et dumps sont toujours résolus depuis la racine du projet
- **Aucun message perdu** — un envoi non confirmé est retenté au scan suivant
- **Pause nocturne** — inactif entre 1h et 7h du matin (configurable)
- **Repos intelligent** — après 10 scans vides consécutifs, pause de 1h
- **Contexte fidèle** — l'historique envoyé au modèle vient du **fil réellement lu dans la page**, pas de la base : les messages que tu as écrits à la main sont pris en compte, et aucune ancienne réponse fautive ne peut être réinjectée
- **Contexte conversationnel** — SQLite async garde la trace et l'anti-doublon de chaque conversation
- **Retry automatique** — 3 tentatives sur erreur réseau Ollama
- **Logging complet** — console (UTF-8 forcé, sinon les emojis font planter les logs Windows) + fichier `bot.log` rotatif (5 Mo × 3 fichiers)
- **Graceful shutdown** — Ctrl+C ferme proprement navigateur, session HTTP et base SQLite

---

## 📁 Structure

```
bot_lbc/
├── main.py            # Boucle principale, orchestration, pause nocturne, idle rest
├── config.py          # Constantes (LLM, anti-bot, navigateur) + BASE_DIR
├── prompts.py         # Prompts (git-ignored) : routeur, persona, réponses statiques
├── conversation.py    # ConversationManager async (SQLite via asyncio.to_thread)
├── llm.py             # Client Ollama async (classify + generate, session partagée)
├── leboncoin.py       # LeboncoinClient (Playwright, profil Chrome persistant)
├── captcha.py         # Glissement « humain » du slider DataDome
├── rules.py           # Règles déterministes AVANT le LLM (conclusion, injection,
│                      #   disponibilité) + nettoyage des réponses générées
├── human.py           # Simulation humaine (Bézier, frappe clavier, scroll, pauses)
├── tools/             # Outils de diagnostic et de test (non utilisés en production)
│   ├── dry_run.py         # Test À BLANC : lit + génère, sans rien envoyer
│   ├── selftest_llm.py    # Boucle qualité : batterie de messages → catégorie + réponse
│   ├── selftest_scan.py   # Vérifie qu'aucun fil déjà réglé n'est rouvert (0 navigateur)
│   ├── selftest_context.py # Vérifie que le fil de la page fait foi (0 navigateur)
│   ├── db_cleanup.py      # Entretien de conversations.db (liste, puis purge sur --apply)
│   ├── selftest_send.py   # Diagnostic/envoi réel contrôlé sur UNE conversation
│   ├── explore.py         # Cartographie la liste des conversations
│   ├── explore2.py        # Anatomie d'un fil (bulles, conteneur, compositeur)
│   └── explore3.py        # Messages entrants/sortants + badges non-lus
├── requirements.txt
├── .gitignore
├── chrome_profile/    # Auto-généré — profil Chrome persistant (login conservé)
├── conversations.db   # Auto-généré — historique SQLite
├── bot_state.json     # Auto-généré — mémoire des aperçus déjà vus (anti-boucle)
└── debugs/            # Auto-généré — captures/DOM d'un échec de chargement
```

---

## ⚙️ Installation (Local)

### 1. Prérequis

- Python 3.11+
- [Ollama](https://ollama.com) installé et lancé (`ollama serve`)

### 2. Cloner et installer

```bash
git clone <url-du-repo>
cd bot_lbc
pip install -r requirements.txt
playwright install chromium
```

### 3. Télécharger le modèle

```bash
ollama pull gemma3:4b
```

### 4. Se connecter une seule fois (profil persistant)

Le bot utilise un **vrai Chrome avec un profil persistant** (`chrome_profile/`).
C'est ce qui rend l'empreinte navigateur authentique (et évite DataDome), et ça
conserve ta connexion entre les redémarrages.

```bash
python main.py
```

Au premier lancement, une fenêtre Chrome s'ouvre :
1. **Connecte-toi** à ton compte Leboncoin (email + mot de passe).
2. Si un **captcha DataDome** apparaît, le bot **tente d'abord de le glisser
   seul** (trajectoire humaine). S'il échoue, il te laisse 5 minutes pour le faire.
3. C'est tout : la session est conservée dans `chrome_profile/`, tu n'auras plus
   à te reconnecter (sauf expiration au bout de plusieurs semaines).

> Le glissement automatique du captcha ne fonctionne pas toujours : DataDome
> note aussi la dynamique de la souris et l'empreinte de l'appareil. En cas
> d'échec, tu es prévenu et le bot attend ton intervention (`CAPTCHA_WAIT_SECONDS`).
>
> La connexion automatique par identifiants n'est pas implémentée : si tu veux
> changer de compte, supprime le dossier `chrome_profile/` et relance.

#### Alternative : Chrome piloté en CDP

Si tu préfères contrôler ton propre Chrome, mets `BROWSER_MODE = "cdp"` dans
`config.py` puis lance :

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\tmp\chrome_bot"
```

Lance ensuite `python main.py`.

### 5. Lancer le bot

```bash
python main.py
```

### 6. Vérifier sans rien envoyer

```bash
python tools/dry_run.py
```

Lit les conversations, classifie et génère les réponses **sans rien poster**.

### 7. Lancer un seul scan (test réel contrôlé)

```bash
python main.py --once
```

Un seul cycle lecture → génération → **envoi**, puis arrêt.

---

## 🏗️ Architecture (règles → routeur → génération)

```
Message acheteur
      │
      ▼
 Règles déterministes (rules.py)   ← avant tout appel LLM
      │   • injection de prompt  → réponse figée (sécurité)
      │   • formule de conclusion → réponse figée
      │   • question de disponibilité → réponse figée (donnée vérifiée)
      ▼
 Appel 1 — Classifier LLM (gemma3, temp=0, max 5 tokens)
      │     → renvoie UN mot : negociation / produit / rdv /
      │       hors_sujet / manipulation / general
      ▼
 Si négociation avec un montant clair : décision chiffrée (rules.py)
      │     accepter / contre-offrir, transmise au modèle déjà tranchée
      ▼
 Appel 2 — Réponse LLM avec le mini-prompt spécialisé
      │     (ou réponse statique si hors_sujet / manipulation)
      ▼
 Nettoyage déterministe (rules.py::sanitize_reply)
      │     • aucun nom de personne, « Monsieur Co » → « Monsieur »
      │     • une seule salutation par conversation
      │     • typographie française
      ▼
 Correction d'offre (rules.py::corrige_offre)
      │     le bon montant est-il annoncé ? sinon réponse déterministe
      ▼
 Vérification : nouvelle bulle SORTANTE contenant notre texte
      │     (sinon l'envoi est considéré comme échoué → retenté au scan suivant)
      ▼
 Historique enregistré en base (uniquement après un envoi confirmé)
```

### Politique de prix (offres chiffrées)

Comparer deux nombres et ne pas se tromper de référence est ce qu'un modèle 4B
rate (il a répondu « à 400 € ça me va » à une offre de 370 €, soit le prix
plancher au lieu de la contre-offre). La décision est donc prise **en dur**
dans `rules.py`, le modèle ne fait que la formuler, et `corrige_offre()`
vérifie derrière que le bon montant a bien été écrit.

| Offre de l'acheteur | Réponse |
|---------------------|---------|
| ≥ `prix_plancher_eur` | acceptation : « Merci, à 400 € ça me va. » (prix seulement : ni réservation, ni paiement, ni rendez-vous) |
| < `prix_plancher_eur`, `prix_contre_offre_eur` renseigné | contre-offre : « Merci pour votre offre. À 430 € je vous la laisse. » |
| < `prix_plancher_eur`, aucune contre-offre | refus poli, sans aucun chiffre de ton côté |
| Aucun montant identifiable | le modèle décide ; `accepte_sous_le_plancher()` bloque toute acceptation sous le plancher |

`prix_plancher_eur` n'est **jamais** annoncé comme tel.

### Quelle conversation le bot ouvre-t-il ?

Ouvrir un fil recharge une page **et le marque « lu » côté serveur**. Le bot
n'ouvre donc un fil que s'il a une raison de le faire (`leboncoin.py` →
`_candidats`), sinon il laisse la liste tranquille :

| Situation | Le fil est-il ouvert ? |
|-----------|------------------------|
| L'aperçu a changé (nouveau message) | ✅ oui |
| L'aperçu n'a jamais été vu (démarrage, nouvelle annonce) | ✅ oui, une fois |
| Non lu et jamais inspecté depuis le démarrage | ✅ oui, une fois |
| Réponse en attente (envoi raté) | ✅ oui, **max 3 tentatives** puis abandon |
| Déjà inspecté, rien à répondre | ❌ non |
| Nous sommes l'acheteur (« À propos de ce vendeur ») | ❌ jamais |
| Fil illisible (captcha, lien absent) | ⚠️ 3 tentatives puis ignoré |

La mémoire des aperçus déjà vus est persistée dans `bot_state.json` : un
redémarrage ne provoque donc **pas** un nouveau parcours complet de toutes les
conversations.

### D'où vient le contexte envoyé au LLM ?

**Le fil lu dans la page fait foi** (`JS_THREAD` → `messages`). C'est lui qui
contient la réalité : les messages que *tu* as écrits à la main, ceux des
conversations qui existaient avant l'installation du bot, tout ce que la base
n'a jamais vu. La base ne sert plus qu'à deux choses :

| Rôle de la base | Détail |
|-----------------|--------|
| **Compléter** | La page ne rend que les derniers messages : `build_context()` aligne la base sur le premier message du fil et ne reprend que ceux qui précèdent. Rien n'est compté deux fois. |
| **Anti-doublon & trace** | Après un envoi confirmé, `user` + `assistant` sont enregistrés. Si un envoi échoue, rien n'est enregistré et le scan suivant retente. |

Conséquence : une base incomplète, ou qui contient d'anciennes réponses fautives
(« Bonjour Monsieur Co… »), ne peut plus polluer la réponse. Le nombre de
messages déjà envoyés est lui aussi déduit du fil, donc plus de « Bonjour »
répété au démarrage parce que la base est vide.

L'outil d'entretien liste et purge les lignes inutiles (simulation par défaut,
sauvegarde horodatée avec `--apply`) :

```bash
python -u tools/db_cleanup.py                        # liste tout
python -u tools/db_cleanup.py --vides --tests        # ce qui serait supprimé
python -u tools/db_cleanup.py --vides --tests --apply
python -u tools/db_cleanup.py --avant 2026-06-01     # vieux fils
python -u tools/db_cleanup.py --ids a1b2,c3d4
```

**Fichiers impliqués :**

| Fichier | Rôle |
|---------|------|
| `config.py` | Constantes techniques (délais, modèle, chemins) + `BASE_DIR` |
| `prompts.py` | `PROMPT_ROUTER`, `BASE_PERSONA`, `PROMPTS`, `REPONSES_STATIQUES` (git‑ignored) |
| `rules.py` | Règles déterministes : conclusion, injection, disponibilité, décision de prix (`corrige_offre`) + `sanitize_reply()` |
| `llm.py` | `classify_message()` (appel 1) + `generate_reply()` (appel 2) |
| `main.py` | Orchestration : règles → classification → génération → envoi |
| `leboncoin.py` | Lecture des conversations et du fil (`JS_THREAD`), détection du rôle, envoi vérifié |
| `human.py` | Mouvement souris Bézier, frappe clavier réaliste, scroll naturel |
| `captcha.py` | Glissement « humain » du slider DataDome |
| `conversation.py` | Historique SQLite async ; `fil_vers_historique()` + `build_context()` (fil prioritaire) |

---

## 🛡️ Anti-Détection

| Protection | Détail |
|-----------|--------|
| **Profil Chrome persistant** | Vrai Chrome + `chrome_profile/` → empreinte authentique et session conservée |
| **Navigation par clics** | Jamais de deep-link vers `/messages/id/...` (cible n°1 de DataDome) : on clique comme un humain |
| **Captcha DataDome** | Glissement automatique « humain » (easing, jitter, dépassement/correction, 0,75-1,4 s) puis repli sur intervention humaine |
| **Intervalles aléatoires** | 75-180 s entre chaque scan (configurable) |
| **Aucune relecture inutile** | Un fil n'est ouvert que si son aperçu change, s'il est non lu et jamais vu, ou si une réponse est en attente. Ouvrir un fil le marque « lu » : boucler dessus était le principal signal |
| **Mémoire persistante** | `bot_state.json` : un redémarrage ne relit pas toutes les conversations |
| **Pause nocturne** | Inactif entre 1h et 7h, aucune requête |
| **Repos intelligent** | Après 10 scans vides → pause 1h |
| **Navigation dispersée** | Le bot passe par l'accueil avant chaque consultation de messages |
| **Frappe clavier réaliste** | `page.keyboard.type()` caractère par caractère (40-120ms/touche), avec fautes de frappe corrigées (~4 %) |
| **Mouvements souris** | Trajectoires courbe de Bézier avec position trackée + mouvements parasites |
| **Scrolls naturels** | Petits coups progressifs avec pauses "lecture" (y compris dans le fil avant de répondre) |
| **Pause de réflexion** | Délai proportionnel au message de l'acheteur avant de répondre |
| **Champ nettoyé avant écriture** | Une pré-réponse Leboncoin pré-remplie ne peut pas se mélanger à notre message |
| **Limite par scan** | Maximum 3 conversations traitées par scan |
| **Auto-relance navigateur** | Page/Chrome mort → redémarrage automatique du profil persistant |

---

## 🔧 Configuration

Toutes les constantes sont dans `config.py` :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OLLAMA_MODEL` | `gemma3:4b` | Modèle Ollama (local, gratuit) |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Endpoint Ollama |
| `SCAN_MIN` | `75` | Secondes minimum entre scans |
| `SCAN_MAX` | `180` | Secondes maximum entre scans |
| `NIGHT_START_HOUR` | `1` | Début pause nocturne (1h) |
| `NIGHT_END_HOUR` | `6` | Fin pause nocturne |
| `TYPING_SPEED_MIN` | `40` | Ms minimum entre frappes |
| `TYPING_SPEED_MAX` | `120` | Ms maximum entre frappes |
| `MAX_HISTORY` | `8` | Messages max dans le contexte LLM |
| `MAX_CONVS_PER_SCAN` | `3` | Conversations inspectées/traitées max par scan |
| `BROWSER_MODE` | `profile` | `profile` (recommandé) ou `cdp` |
| `PROFILE_DIR` | `<racine>/chrome_profile` | Dossier du profil Chrome persistant (absolu) |
| `CHROME_CHANNEL` | `chrome` | `chrome` = vrai Google Chrome ; `None` = Chromium |
| `CDP_URL` | `http://localhost:9222` | URL CDP si `BROWSER_MODE = "cdp"` |
| `CAPTCHA_WAIT_SECONDS` | `300` | Temps laissé à l'humain après échec du glissement auto |
| `DEBUG_SCREENSHOTS` | `True` | Dump PNG+HTML dans `debugs/` quand un fil ne charge pas |

Dans `main.py` :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `IDLE_SCAN_LIMIT` | `10` | Scans vides avant pause repos |
| `IDLE_REST_SECONDS` | `3600` | Durée du repos (1h) |

### Modifier les prompts vendeur

Éditez `prompts.py` :
- `VENTE` / `PRODUIT` — les données factuelles de ta vente
- `BASE_PERSONA` — style, fiabilité, confidentialité
- `PROMPTS["negociation"]` — stratégie de prix
- `PROMPTS["produit"]` — infos techniques
- `PROMPTS["rdv"]` — dispo et localisation
- `PROMPTS["general"]` — salutations, disponibilité, messages vagues
- `REPONSES_STATIQUES` — réponses figées (hors_sujet, manipulation)

**Prix.** Le bot ne révèle jamais `prix_plancher_eur` (confidentiel). La décision
est prise en dur dans `rules.py` (cf. « Politique de prix » plus haut) :

| Offre reçue | Réponse |
|-------------|---------|
| `>= prix_plancher_eur` | acceptation : « Merci, à 400 € ça me va. » |
| `< prix_plancher_eur` + `prix_contre_offre_eur` | contre-offre : « À 430 € je vous la laisse. » |
| `< prix_plancher_eur`, sans contre-offre | refus poli, **sans** révéler le plancher |

Renseigne `prix_contre_offre_eur` (valeur entre `prix_plancher_eur` et
`prix_affiche_eur`, ici `430`) pour qu'il contre-propose ; laisse `None` pour
qu'il refuse sans avancer de chiffre. Pour interdire toute acceptation
automatique, il suffit de retirer `prix_plancher_eur` : le bot refuse alors
toujours et te laisse trancher.

Après chaque modification, valide avec `python -u tools/selftest_llm.py`.

---

## 🖥️ Déploiement VPS

### Prérequis VPS

- **RAM** : 4 Go minimum (8 Go recommandé pour Ollama)
- **CPU** : 2 vCPU minimum
- **Disque** : 20 Go
- **OS** : Ubuntu 22.04 / 24.04

### Installation

```bash
# Système
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git wget

# Ollama
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve &
ollama pull gemma3:4b

# Chrome (pour le mode CDP)
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
apt update && apt install -y google-chrome-stable

# Projet
git clone <url-du-repo>
cd bot_lbc
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps
```

### Login via VNC (une seule fois)

```bash
apt install -y xvfb x11vnc
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
google-chrome --remote-debugging-port=9222 --no-first-run --disable-gpu &
x11vnc -display :99 -forever -nopw &
```

Connectez-vous en VNC sur `VOTRE_IP:5900`, loguez-vous sur Leboncoin, puis lancez le bot.

### Service systemd (tourne H24)

```bash
sudo nano /etc/systemd/system/bot-lbc.service
```

```ini
[Unit]
Description=Bot Leboncoin
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/bot_lbc
ExecStart=/root/bot_lbc/venv/bin/python main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable bot-lbc
sudo systemctl start bot-lbc

# Logs en direct :
journalctl -u bot-lbc -f
```

---

## 🧪 Vérification et boucle d'amélioration

Tous les outils sont dans `tools/` et se lancent depuis la racine du projet.

**1. Qualité des réponses (aucun navigateur requis — le plus rapide à itérer)**

```bash
python -u tools/selftest_llm.py          # batterie complète
python -u tools/selftest_llm.py --case 5 # un seul cas
```

Affiche, pour 24 messages d'acheteurs réalistes (+ 4 conversations déjà
engagées), la catégorie choisie et la réponse générée, ainsi que les contrôles
déterministes (noms de personnes, salutations, disponibilité). Boucle : repérer
un cas raté → ajuster `prompts.py` → relancer.

**2. Boucle de scan (aucun navigateur requis)**

```bash
python -u tools/selftest_scan.py
```

Vérifie qu'un fil déjà réglé n'est **jamais** rouvert : c'est cette logique qui
évitait au bot de relire en boucle les conversations lues.

**3. Contexte (aucun navigateur requis)**

```bash
python -u tools/selftest_context.py
```

Vérifie que le contexte vient bien du fil de la page, que la base ne sert qu'à
compléter, et qu'aucun message n'est compté deux fois au raccord.

**4. Pipeline complet sans rien envoyer**

```bash
python -u tools/dry_run.py           # résumé
python -u tools/dry_run.py --debug   # logs détaillés
```

**5. Chaîne d'envoi (diagnostic, puis envoi réel)**

```bash
python -u tools/selftest_send.py                 # n'envoie RIEN
python -u tools/selftest_send.py --confirm       # envoie réellement
```

**6. Entretien de la base**

```bash
python -u tools/db_cleanup.py                    # liste (ne supprime rien)
python -u tools/db_cleanup.py --vides --tests --apply
```

**7. Re-cartographier le site si les sélecteurs cassent**

```bash
python -u tools/explore.py && python -u tools/explore3.py
```

> Les anciens tests unitaires synchrones (`tests/`) ont été supprimés :
> ils ciblaient l'ancienne API synchrone et `requests`, remplacés depuis
> par `ConversationManager` async et un client Ollama `aiohttp`.

---

## ⚠️ Limitations

- **DOM Leboncoin** — Leboncoin modifie régulièrement son HTML. Les sélecteurs reposent sur des attributs stables (`data-qa-id="message-bubble"`, `data-spark-component="textarea"`, `data-qa-id="message-send"`). En cas de changement, lance `python tools/explore.py` puis `python tools/explore3.py` pour re-cartographier le site.
- **Session** — le profil `chrome_profile/` conserve la connexion. Si elle expire, le bot ouvre Chrome et attend que tu te reconnectes.
- **Captcha** — le glissement automatique n'aboutit pas toujours (DataDome note aussi la dynamique souris et l'empreinte). Dans ce cas il faut intervenir à la main.
- **Rôle acheteur/vendeur** — le bot ne répond qu'aux conversations où tu es le VENDEUR (« À propos de cet acheteur »). Les conversations où tu achètes sont ignorées.
- **Offres chiffrées** — une offre automatique Leboncoin (« Accepter 400 € ») est signalée dans les logs mais **jamais** acceptée ni refusée automatiquement : décision humaine.
- **Base de données** — elle n'est plus la source du contexte (le fil de la page l'est) : elle sert d'anti-doublon, de trace et de complément pour les messages que la page n'affiche plus. Tu peux donc la purger sans risque pour la pertinence des réponses, en gardant une sauvegarde (`conversations.db.bak-*`).
- **Historique très long** — la page ne rend que les derniers messages et le bot ne remonte pas indéfiniment : au-delà de `MAX_HISTORY` (8 messages) l'ancienneté est résumée par son absence. Suffisant pour répondre juste au message en cours.
- **Fichiers obsolètes** — `session.json` et `cookies.json` (ancienne méthode) ne sont plus utilisés.
- **CPU et LLM** — sans GPU, chaque réponse prend 10-30s. C'est acceptable car le bot simule un humain avec des délais naturels

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
