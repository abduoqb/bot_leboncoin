import sqlite3
import json
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)


class _SyncConversationManager:
    """Gestionnaire synchrone SQLite (ne pas utiliser directement)."""

    def __init__(self, db_path="conversations.db"):
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

    def build_context(self, id_lbc: str, new_message: str, system_prompt: str, max_hist: int = 8) -> tuple:
        messages, nb_envois = self.get_messages(id_lbc)
        history = messages[-max_hist:]
        context = [{"role": "system", "content": system_prompt}]
        context += history
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

    def __init__(self, db_path="conversations.db"):
        self._sync = _SyncConversationManager(db_path)

    async def get_messages(self, id_lbc: str) -> tuple:
        return await asyncio.to_thread(self._sync.get_messages, id_lbc)

    async def get_nb_envois(self, id_lbc: str) -> int:
        return await asyncio.to_thread(self._sync.get_nb_envois, id_lbc)

    async def save_message(self, id_lbc: str, role: str, content: str):
        await asyncio.to_thread(self._sync.save_message, id_lbc, role, content)

    async def build_context(self, id_lbc: str, new_message: str, system_prompt: str, max_hist: int = 8) -> tuple:
        return await asyncio.to_thread(self._sync.build_context, id_lbc, new_message, system_prompt, max_hist)

    async def close(self):
        await asyncio.to_thread(self._sync.close)

