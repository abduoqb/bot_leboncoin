# Bot Leboncoin × Ollama

Bot Python qui répond automatiquement aux messages de tes annonces Leboncoin en utilisant un LLM local (Ollama / gemma3:4b). Zéro coût, tourne entièrement en local ou sur VPS.

---

## ✨ Fonctionnalités

- **LLM local 2-étapes** — classification du message (router) puis réponse spécialisée via gemma3:4b
- **Anti-détection avancée** — frappe humaine au clavier, mouvements de souris Bézier, scrolls naturels, navigation dispersée, pause nocturne
- **Pause nocturne** — inactif entre 1h et 7h du matin (configurable)
- **Repos intelligent** — après 10 scans vides consécutifs, pause de 1h
- **Contexte conversationnel** — SQLite async stocke l'historique de chaque conversation
- **Retry automatique** — 3 tentatives sur erreur réseau Ollama
- **Logging complet** — console + fichier `bot.log` rotatif (5 Mo × 3 fichiers)
- **Graceful shutdown** — Ctrl+C ferme proprement navigateur, session HTTP et base SQLite

---

## 📁 Structure

```
bot_lbc/
├── main.py            # Boucle principale, orchestration, pause nocturne, idle rest
├── config.py          # Constantes (LLM, anti-bot, navigateur)
├── prompts.py         # Prompts (git-ignored) : routeur, persona, réponses statiques
├── conversation.py    # ConversationManager async (SQLite via asyncio.to_thread)
├── llm.py             # Client Ollama async (classify + generate, session partagée)
├── leboncoin.py       # LeboncoinClient (Playwright, profil Chrome persistant)
├── human.py           # Simulation humaine (Bézier, frappe clavier, scroll, pauses)
├── dry_run.py         # Test À BLANC : lit + génère les réponses, sans rien envoyer
├── explore.py         # Diagnostic : cartographie la liste des conversations
├── explore2.py        # Diagnostic : anatomie d'un fil (bulles, conteneur)
├── explore3.py        # Diagnostic : messages entrants/sortants + badges non-lus
├── requirements.txt
├── .gitignore
├── chrome_profile/    # Auto-généré — profil Chrome persistant (login conservé)
└── conversations.db   # Auto-généré — historique SQLite
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
2. Si un **captcha DataDome** apparaît, **glisse-le** toi-même.
3. C'est tout : la session est conservée dans `chrome_profile/`, tu n'auras plus
   à te reconnecter (sauf expiration au bout de plusieurs semaines).

> Le bot **ne résout jamais un captcha automatiquement**. S'il en rencontre un,
> il met la pause et attend que tu le glisses (`CAPTCHA_WAIT_SECONDS` dans `config.py`).
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
python dry_run.py
```

Lit les conversations, classifie et génère les réponses **sans rien poster**.

---

## 🏗️ Architecture (2-Step Router)

```
Message acheteur
      │
      ▼
 Appel 1 — Classifier LLM (gemma3, temp=0, max 5 tokens)
      │     → renvoie UN mot : negociation / produit / rdv /
      │       hors_sujet / manipulation / general
      ▼
 Appel 2 — Réponse LLM avec le mini-prompt spécialisé
      │     (ou réponse statique si hors_sujet / manipulation)
      ▼
  Envoi sur Leboncoin (frappe humaine)
```

**Fichiers impliqués :**

| Fichier | Rôle |
|---------|------|
| `config.py` | Contient les constantes techniques (délais, modèle, etc.) |
| `prompts.py` | Contient `PROMPT_ROUTER`, `BASE_PERSONA`, `PROMPTS` et `REPONSES_STATIQUES` (git‑ignored) |
| `llm.py` | `classify_message()` (appel 1) + `generate_reply()` (appel 2) |
| `main.py` | Orchestration : classification → sélection prompt → génération → envoi |
| `leboncoin.py` | Scan des messages non lus + envoi avec frappe humaine |

| `human.py` | Mouvement souris Bézier, frappe clavier réaliste, scroll naturel |
| `conversation.py` | Historique SQLite async (contexte pour Ollama) |

---

## 🛡️ Anti-Détection

| Protection | Détail |
|-----------|--------|
| **Profil Chrome persistant** | Vrai Chrome + `chrome_profile/` → empreinte authentique et session conservée |
| **Navigation par clics** | Jamais de deep-link vers `/messages/id/...` (cible n°1 de DataDome) : on clique comme un humain |
| **Détection captcha** | DataDome détecté → pause + demande d'intervention humaine (aucune résolution automatique) |
| **Intervalles aléatoires** | 45-120s entre chaque scan (configurable) |
| **Pause nocturne** | Inactif entre 1h et 7h, aucune requête |
| **Repos intelligent** | Après 10 scans vides → pause 1h |
| **Navigation dispersée** | Le bot passe par l'accueil avant chaque consultation de messages |
| **Frappe clavier réaliste** | `page.keyboard.type()` caractère par caractère (40-120ms/touche) |
| **Mouvements souris** | Trajectoires courbe de Bézier avec position trackée |
| **Scrolls naturels** | Petits coups progressifs avec pauses "lecture" |
| **Pause de réflexion** | Délai proportionnel au message de l'acheteur avant de répondre |
| **Limite par scan** | Maximum 3 conversations traitées par scan |

---

## 🔧 Configuration

Toutes les constantes sont dans `config.py` :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OLLAMA_MODEL` | `deepseek-v3.1:671b-cloud` | Modèle Ollama de votre choix |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Endpoint Ollama |
| `SCAN_MIN` | `45` | Secondes minimum entre scans |
| `SCAN_MAX` | `120` | Secondes maximum entre scans |
| `NIGHT_START_HOUR` | `1` | Début pause nocturne (1h) |
| `NIGHT_END_HOUR` | `6` | Fin pause nocturne |
| `TYPING_SPEED_MIN` | `40` | Ms minimum entre frappes |
| `TYPING_SPEED_MAX` | `120` | Ms maximum entre frappes |
| `MAX_HISTORY` | `8` | Messages max dans le contexte LLM |
| `BROWSER_MODE` | `profile` | `profile` (recommandé) ou `cdp` |
| `PROFILE_DIR` | `chrome_profile` | Dossier du profil Chrome persistant |
| `CAPTCHA_WAIT_SECONDS` | `300` | Temps laissé à l'humain pour un captcha |

Dans `main.py` :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MAX_CONVS_PER_SCAN` | `3` | Conversations inspectées/traitées max par scan (dans `config.py`) |
| `IDLE_SCAN_LIMIT` | `10` | Scans vides avant pause repos |
| `IDLE_REST_SECONDS` | `3600` | Durée du repos (1h) |

### Modifier les prompts vendeur

Éditez `config.py` :
- `BASE_PERSONA` — caractéristiques du produit et style de réponse
- `PROMPTS["negociation"]` — stratégie de prix
- `PROMPTS["produit"]` — infos techniques
- `PROMPTS["rdv"]` — dispo et localisation
- `PROMPTS["general"]` — salutations

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

## 🧪 Vérification

Le test à blanc valide tout le pipeline (lecture, classification, génération)
**sans jamais envoyer de message** :

```bash
python -u dry_run.py          # résumé
python -u dry_run.py --debug  # logs détaillés
```

> Les anciens tests unitaires synchrones (`tests/`) ont été supprimés :
> ils ciblaient l'ancienne API synchrone et `requests`, remplacés depuis
> par `ConversationManager` async et un client Ollama `aiohttp`.

---

## ⚠️ Limitations

- **DOM Leboncoin** — Leboncoin modifie régulièrement son HTML. Les sélecteurs reposent sur des attributs stables (`data-qa-id="message-bubble"`, `data-spark-component="textarea"`, `data-qa-id="message-send"`). En cas de changement, lance `python explore.py` puis `python explore3.py` pour re-cartographier le site.
- **Session** — le profil `chrome_profile/` conserve la connexion. Si elle expire, le bot ouvre Chrome et attend que tu te reconnectes.
- **Fichiers obsolètes** — `session.json` et `cookies.json` (ancienne méthode) ne sont plus utilisés. Tu peux les supprimer.
- **CPU et LLM** — sans GPU, chaque réponse prend 10-30s. C'est acceptable car le bot simule un humain avec des délais naturels

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
