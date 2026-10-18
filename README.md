# Bot Leboncoin × Ollama

Bot Python qui répond automatiquement aux messages de tes annonces Leboncoin en utilisant un LLM local (Ollama / gemma3:4b). Zéro coût, tourne entièrement en local ou sur VPS.

---

## ✨ Fonctionnalités

- **LLM local 2-étapes** — classification du message (router) puis réponse spécialisée via gemma3:4b
- **Envoi réel vérifié** — le bot tape et envoie; l'envoi n'est validé que par l'apparition d'une nouvelle bulle sortante (jamais par un simple vidage de champ)
- **Anti-détection avancée** — frappe humaine au clavier (avec fautes de frappe corrigées), mouvements de souris Bézier, scrolls naturels, navigation dispersée, pause nocturne
- **Captcha DataDome** — tentative de glissement automatique « humain » du slider, avec repli sur intervention humaine
- **Pré-réponses Leboncoin** — détectées, tracées, et utilisées en repli si le LLM ne produit rien
- **Ne répond jamais à la place d'un vendeur** — les conversations où TU es l'acheteur (« À propos de ce vendeur ») sont ignorées
- **Auto-relance du navigateur** — si Chrome crashe ou est fermé, le bot redémarre le profil et reprend
- **Indépendant du répertoire courant** — profil, base et dumps sont toujours résolus depuis la racine du projet
- **Aucun message perdu** — un envoi non confirmé est retenté au scan suivant
- **Pause nocturne** — inactif entre 1h et 7h du matin (configurable)
- **Repos intelligent** — après 10 scans vides consécutifs, pause de 1h
- **Contexte conversationnel** — SQLite async stocke l'historique de chaque conversation
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
├── rules.py           # Règles déterministes AVANT le LLM (conclusion, injection de prompt)
├── human.py           # Simulation humaine (Bézier, frappe clavier, scroll, pauses)
├── tools/             # Outils de diagnostic et de test (non utilisés en production)
│   ├── dry_run.py         # Test À BLANC : lit + génère, sans rien envoyer
│   ├── selftest_llm.py    # Boucle qualité : batterie de messages → catégorie + réponse
│   ├── selftest_send.py   # Diagnostic/envoi réel contrôlé sur UNE conversation
│   ├── explore.py         # Cartographie la liste des conversations
│   ├── explore2.py        # Anatomie d'un fil (bulles, conteneur, compositeur)
│   └── explore3.py        # Messages entrants/sortants + badges non-lus
├── requirements.txt
├── .gitignore
├── chrome_profile/    # Auto-généré — profil Chrome persistant (login conservé)
├── conversations.db   # Auto-généré — historique SQLite
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
      ▼
 Appel 1 — Classifier LLM (gemma3, temp=0, max 5 tokens)
      │     → renvoie UN mot : negociation / produit / rdv /
      │       hors_sujet / manipulation / general
      ▼
 Appel 2 — Réponse LLM avec le mini-prompt spécialisé
      │     (ou réponse statique si hors_sujet / manipulation)
      ▼
 Vérification : nouvelle bulle SORTANTE contenant notre texte
      │     (sinon l'envoi est considéré comme échoué → retenté au scan suivant)
      ▼
 Historique enregistré en base (uniquement après un envoi confirmé)
```

**Fichiers impliqués :**

| Fichier | Rôle |
|---------|------|
| `config.py` | Constantes techniques (délais, modèle, chemins) + `BASE_DIR` |
| `prompts.py` | `PROMPT_ROUTER`, `BASE_PERSONA`, `PROMPTS`, `REPONSES_STATIQUES` (git‑ignored) |
| `rules.py` | Règles déterministes : conclusion, injection de prompt |
| `llm.py` | `classify_message()` (appel 1) + `generate_reply()` (appel 2) |
| `main.py` | Orchestration : règles → classification → génération → envoi |
| `leboncoin.py` | Lecture des conversations, détection du rôle, envoi vérifié |
| `human.py` | Mouvement souris Bézier, frappe clavier réaliste, scroll naturel |
| `captcha.py` | Glissement « humain » du slider DataDome |
| `conversation.py` | Historique SQLite async (contexte pour Ollama) |

---

## 🛡️ Anti-Détection

| Protection | Détail |
|-----------|--------|
| **Profil Chrome persistant** | Vrai Chrome + `chrome_profile/` → empreinte authentique et session conservée |
| **Navigation par clics** | Jamais de deep-link vers `/messages/id/...` (cible n°1 de DataDome) : on clique comme un humain |
| **Captcha DataDome** | Glissement automatique « humain » (easing, jitter, dépassement/correction, 0,75-1,4 s) puis repli sur intervention humaine |
| **Intervalles aléatoires** | 45-120s entre chaque scan (configurable) |
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
| `SCAN_MIN` | `45` | Secondes minimum entre scans |
| `SCAN_MAX` | `120` | Secondes maximum entre scans |
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

**Prix.** Le bot ne révèle jamais `prix_plancher_eur` (confidentiel). Comportement
par défaut face à une offre chiffrée :

| Offre reçue | Réponse |
|-------------|---------|
| `>= prix_plancher_eur` | acceptation annoncée |
| `< prix_plancher_eur` | refus poli, **sans** révéler le plancher |

Pour qu'il **contre-propose** automatiquement, renseigne
`prix_contre_offre_eur` (valeur entre `prix_plancher_eur` et `prix_affiche_eur`).
Laisse `None` si tu veux garder la main sur les contre-offres.

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

Affiche, pour 16 messages d'acheteurs réalistes, la catégorie choisie et la
réponse générée. Boucle : repérer un cas raté → ajuster `prompts.py` → relancer.

**2. Pipeline complet sans rien envoyer**

```bash
python -u tools/dry_run.py           # résumé
python -u tools/dry_run.py --debug   # logs détaillés
```

**3. Chaîne d'envoi (diagnostic, puis envoi réel)**

```bash
python -u tools/selftest_send.py                 # n'envoie RIEN
python -u tools/selftest_send.py --confirm       # envoie réellement
```

**4. Re-cartographier le site si les sélecteurs cassent**

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
- **Fichiers obsolètes** — `session.json` et `cookies.json` (ancienne méthode) ne sont plus utilisés.
- **CPU et LLM** — sans GPU, chaque réponse prend 10-30s. C'est acceptable car le bot simule un humain avec des délais naturels

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
