# 🤖 Bot Leboncoin × Ollama

Bot Python qui répond automatiquement aux messages de tes annonces Leboncoin en utilisant un LLM local (Ollama / gemma3:4b). Zéro coût, tourne entièrement en local.

---

## ✨ Fonctionnalités

- **Scraping Playwright** — scan automatique des messages non lus toutes les 60s
- **LLM local (Ollama)** — génération des réponses avec gemma3:4b (0€, vie privée préservée)
- **Contexte conversationnel** — SQLite stocke l'historique de chaque conversation
- **Anti-détection** — user-agent réaliste, délais aléatoires, désactivation des flags automation
- **Retry automatique** — 3 tentatives sur erreur réseau Ollama
- **Logging complet** — console + fichier `bot.log` avec timestamps
- **Graceful shutdown** — Ctrl+C ferme proprement le navigateur et la base SQLite
- **Stats en temps réel** — messages traités, erreurs, scans

---

## 📁 Structure

```
bot_lbc/
├── main.py            # Boucle principale, orchestration, logging, shutdown
├── config.py          # SYSTEM_PROMPT, constantes, chargement .env
├── conversation.py    # ConversationManager (SQLite)
├── llm.py             # Client Ollama (POST /api/chat avec retry)
├── leboncoin.py       # LeboncoinClient (Playwright async)
├── tests/
│   ├── test_conversation.py
│   └── test_llm.py
├── requirements.txt
├── .gitignore
├── .env               # ⚠️ Non commité — tes identifiants
├── session.json       # Auto-généré — session Leboncoin
└── conversations.db   # Auto-généré — historique SQLite
```

---

## ⚙️ Installation

### 1. Prérequis

- Python 3.11+
- [Ollama](https://ollama.com) installé

### 2. Cloner et installer les dépendances

```bash
git clone <url-du-repo>
cd bot_lbc
pip install -r requirements.txt
playwright install chromium
```

### 3. Télécharger le modèle Ollama

```bash
ollama pull gemma3:4b
```

### 4. Configurer le fichier `.env`

Crée un fichier `.env` à la racine :

```env
LBC_EMAIL=ton@email.com
LBC_PASSWORD=ton_mot_de_passe
```

> ⚠️ Ne commite **jamais** ce fichier. Il est déjà dans `.gitignore`.

--- 

## 🚀 Utilisation

### Lancer le bot

```bash
python main.py
```

Le bot va :
1. Démarrer Chromium (headless)
2. Se connecter à Leboncoin (ou réutiliser la session `session.json`)
3. Scanner les messages non lus toutes les 60 secondes
4. Générer et envoyer une réponse via Ollama pour chaque nouveau message
5. Sauvegarder le contexte en SQLite

### Arrêter le bot

```
Ctrl+C
```

Le bot affiche un bilan final et ferme proprement toutes les ressources.

---

## 🔧 Configuration

Toutes les constantes sont dans [`config.py`](config.py) :

| Variable | Valeur par défaut | Description |
|----------|-------------------|-------------|
| `OLLAMA_MODEL` | `gemma3:4b` | Modèle Ollama utilisé |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Endpoint Ollama |
| `SCAN_INTERVAL` | `60` | Secondes entre chaque scan |
| `MAX_HISTORY` | `8` | Nombre max de messages dans le contexte |

### Modifier le prompt vendeur

Édite `SYSTEM_PROMPT` dans `config.py` pour adapter le comportement du bot à ton annonce.

---

## 🧪 Tests

```bash
python -m pytest tests/ -v
```

18 tests unitaires couvrant `ConversationManager` et le client LLM (avec mock HTTP).

---

## 🏗️ Architecture

```
main.py
  │
  ├── leboncoin.py  ── Playwright ──► leboncoin.fr
  │       └── get_new_messages() / send_message()
  │
  ├── conversation.py ── SQLite ──► conversations.db
  │       └── build_context() / save_message()
  │
  └── llm.py ── HTTP ──► localhost:11434 (Ollama)
          └── generate_reply()
```

**Flux pour chaque message :**

1. `LeboncoinClient.get_new_messages()` retourne les convs non lues
2. `ConversationManager.build_context()` construit le contexte Ollama (historique + nouveau msg)
3. `generate_reply()` envoie à Ollama et retourne la réponse texte
4. `LeboncoinClient.send_message()` poste la réponse sur le site
5. `ConversationManager.save_message()` sauvegarde les deux messages (user + assistant)

---

## ⚠️ Limitations connues

- **Sélecteurs CSS** : Leboncoin modifie régulièrement son DOM. Si le bot ne détecte pas de messages, inspecte le site (F12) et ajuste les sélecteurs dans `leboncoin.py`
- **Headless** : certaines interactions CAPTCHA peuvent bloquer la connexion en mode headless. Passe `headless=False` temporairement si besoin
- **Session** : la session `session.json` expire (généralement après quelques jours). Supprime-la pour forcer un nouveau login

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE)
