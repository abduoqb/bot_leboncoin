import sqlite3
import json
import logging
import asyncio
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def fil_vers_historique(bulles, retire_fin_acheteur: bool = True) -> list:
    """Convertit les bulles d'un fil (JS_THREAD) en historique de conversation.

    `side` vaut « in » (acheteur) ou « out » (nous). On retire la suite finale
    de messages de l'acheteur : c'est le message auquel on va répondre, il est
    déjà passé au LLM séparément (sinon il compterait deux fois).
    """
    bulles = list(bulles or [])
    if retire_fin_acheteur:
        while bulles and bulles[-1].get("side") == "in":
            bulles.pop()
    return [
        {"role": "assistant" if b.get("side") == "out" else "user",
         "content": (b.get("text") or "").strip()}
        for b in bulles
        if (b.get("text") or "").strip()
    ]


def _meme_message(a: dict, b: dict) -> bool:
    return (a.get("role") == b.get("role")
            and (a.get("content") or "").strip() == (b.get("content") or "").strip())



class _SyncConversationManager:
    """Gestionnaire synchrone SQLite (ne pas utiliser directement)."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(_BASE_DIR, "conversations.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id_lbc    TEXT PRIMARY KEY,
                messages  TEXT DEFAULT '[]',
                nb_envois INTEGER DEFAULT 0,
                created   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def get_messages(self, id_lbc: str) -> tuple:
        try:
            row = self.conn.execute(
                "SELECT messages, nb_envois FROM conversations WHERE id_lbc=?", (id_lbc,)
            ).fetchone()
            if not row:
                self.conn.execute(
                    "INSERT INTO conversations (id_lbc) VALUES (?)", (id_lbc,)
                )
                self.conn.commit()
                return [], 0
            return json.loads(row[0]), row[1]
        except sqlite3.Error as e:
            logger.error(f"Erreur SQLite get_messages({id_lbc}): {e}")
            return [], 0

    def get_nb_envois(self, id_lbc: str) -> int:
        try:
            row = self.conn.execute(
                "SELECT nb_envois FROM conversations WHERE id_lbc=?", (id_lbc,)
            ).fetchone()
            return row[0] if row else 0
        except sqlite3.Error as e:
            logger.error(f"Erreur SQLite get_nb_envois({id_lbc}): {e}")
            return 0

    def save_message(self, id_lbc: str, role: str, content: str):
        try:
            messages, nb = self.get_messages(id_lbc)
            messages.append({"role": role, "content": content})
            increment = 1 if role == "assistant" else 0
            self.conn.execute(
                "UPDATE conversations SET messages=?, nb_envois=?, updated=? WHERE id_lbc=?",
                (json.dumps(messages, ensure_ascii=False), nb + increment, datetime.now(), id_lbc)
            )
            self.conn.commit()
            logger.debug(f"Message sauvegardé ({role}) pour conv {id_lbc}")
        except sqlite3.Error as e:
            logger.error(f"Erreur SQLite save_message({id_lbc}): {e}")

    def build_context(self, id_lbc: str, new_message: str, system_prompt: str,
                      max_hist: int = 8, fil: list = None) -> tuple:
        """Construit le contexte envoyé au LLM.

        LE FIL LU DANS LA PAGE FAIT FOI (`fil`, issu de JS_THREAD) : il contient
        la réalité — messages envoyés à la main, avant l'installation du bot,
        ou conversations que la base n'a jamais vues. La base ne sert qu'à
        deux choses :
          • compléter les messages plus anciens que la page n'affiche plus
            (la page ne rend que les derniers) ;
          • garder la trace qui permet l'anti-doublon.

        Elle reste donc utile, mais elle n'est plus la source de vérité du
        contexte : une base incomplète ou contenant d'anciennes réponses
        fautives ne peut plus polluer la réponse.
        """
        messages, nb_envois_db = self.get_messages(id_lbc)
        fil = fil or []

        if not fil:
            complet = messages              # pas de fil lisible → base seule
        else:
            # Recoller les messages plus anciens : on aligne la base sur le
            # premier message du fil et on ne garde que ce qui précède.
            prefixe = []
            if messages:
                idx = next((i for i in range(len(messages))
                            if _meme_message(messages[i], fil[0])), None)
                if idx is not None:
                    prefixe = messages[:idx]
            complet = prefixe + fil

        nb_envois = max(nb_envois_db,
                        sum(1 for m in complet if m.get("role") == "assistant"))

        context = [{"role": "system", "content": system_prompt}]
        context += complet[-max_hist:]
        context.append({"role": "user", "content": new_message})
        return context, nb_envois

    def close(self):
        try:
            self.conn.close()
            logger.info("Base SQLite fermée proprement")
        except sqlite3.Error as e:
            logger.error(f"Erreur fermeture SQLite: {e}")


class ConversationManager:
    """Wrapper async pour ne pas bloquer l'event loop (Weakness 1)."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(_BASE_DIR, "conversations.db")
        self._sync = _SyncConversationManager(db_path)

    async def get_messages(self, id_lbc: str) -> tuple:
        return await asyncio.to_thread(self._sync.get_messages, id_lbc)

    async def get_nb_envois(self, id_lbc: str) -> int:
        return await asyncio.to_thread(self._sync.get_nb_envois, id_lbc)

    async def save_message(self, id_lbc: str, role: str, content: str):
        await asyncio.to_thread(self._sync.save_message, id_lbc, role, content)

    async def build_context(self, id_lbc: str, new_message: str, system_prompt: str,
                            max_hist: int = 8, fil: list = None) -> tuple:
        return await asyncio.to_thread(self._sync.build_context, id_lbc, new_message,
                                       system_prompt, max_hist, fil)

    async def close(self):
        await asyncio.to_thread(self._sync.close)

