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
├── config.py          # Router prompt, mini-prompts vendeur, constantes anti-bot
├── conversation.py    # ConversationManager async (SQLite via asyncio.to_thread)
├── llm.py             # Client Ollama async (classify + generate, session partagée)
├── leboncoin.py       # LeboncoinClient (Playwright async, CDP prioritaire)
├── human.py           # Simulation humaine (Bézier, frappe clavier, scroll, pauses)
├── tests/
│   ├── test_conversation.py
│   └── test_llm.py
├── requirements.txt
├── .gitignore
├── session.json       # Auto-généré — session Leboncoin
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

### 4. Lancer Chrome en mode CDP (recommandé)

Le mode CDP utilise votre vrai Chrome → empreinte navigateur authentique, zéro détection.

**Windows (PowerShell) :**
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\tmp\chrome_bot"
```

**Linux :**
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome_bot &
```

Connectez-vous à votre compte Leboncoin dans cette fenêtre Chrome.

### 5. Lancer le bot

```bash
python main.py
```

---

## 🏗️ Architecture (2-Step Router)

```
Message acheteur
      │
      ▼
 Appel 1 — Classifier LLM (gemma3, temp=0, max 5 tokens)
      │     → renvoie UN mot : negociation / voiture / rdv /
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
| **Mode CDP** | Connexion au vrai Chrome → empreinte navigateur authentique |
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
| `SCAN_MIN` | `4` | Secondes minimum entre scans |
| `SCAN_MAX` | `12` | Secondes maximum entre scans |
| `NIGHT_START_HOUR` | `1` | Début pause nocturne (1h) |
| `NIGHT_END_HOUR` | `5` | Fin pause nocturne |
| `TYPING_SPEED_MIN` | `4` | Ms minimum entre frappes |
| `TYPING_SPEED_MAX` | `12` | Ms maximum entre frappes |
| `MAX_HISTORY` | `8` | Messages max dans le contexte LLM |

Dans `main.py` :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MAX_CONVS_PER_SCAN` | `3` | Conversations traitées max par scan |
| `IDLE_SCAN_LIMIT` | `10` | Scans vides avant pause repos |
| `IDLE_REST_SECONDS` | `3600` | Durée du repos (1h) |

### Modifier les prompts vendeur

Éditez `config.py` :
- `BASE_PERSONA` — caractéristiques de la voiture et style de réponse
- `PROMPTS["negociation"]` — stratégie de prix
- `PROMPTS["voiture"]` — infos techniques
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

## 🧪 Tests

```bash
python -m pytest tests/ -v
```

---

## ⚠️ Limitations

- **DOM Leboncoin** — Leboncoin modifie régulièrement son HTML. Si le bot ne détecte pas de messages, inspectez le site (F12) et ajustez les sélecteurs dans `leboncoin.py`
- **Session** — la session `session.json` expire après quelques jours. Supprimez-la pour forcer un nouveau login
- **CPU et LLM** — sans GPU, chaque réponse prend 10-30s. C'est acceptable car le bot simule un humain avec des délais naturels

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
