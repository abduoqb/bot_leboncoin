import sqlite3
import json
import logging
import asyncio
import os
from datetime import datetime

from config import BASE_DIR

logger = logging.getLogger(__name__)


def fil_vers_historique(bulles: list | None, retire_fin_acheteur: bool = True) -> list[dict]:
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

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.path.join(BASE_DIR, "conversations.db")
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.Error as e:
            logger.warning(f"PRAGMA SQLite impossible : {e}")
        self._init_db()

    def _init_db(self) -> None:
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

    @staticmethod
    def _parse_messages(raw: str | None) -> list:
        """JSON corrompu ou valeur non-liste → [] (jamais d'exception)."""
        try:
            val = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Historique JSON corrompu → ignoré")
            return []
        return val if isinstance(val, list) else []

    def get_messages(self, id_lbc: str) -> tuple[list, int]:
        """Lecture seule : aucune écriture (l'INSERT se fait dans les sauvegardes)."""
        try:
            row = self.conn.execute(
                "SELECT messages, nb_envois FROM conversations WHERE id_lbc=?", (id_lbc,)
            ).fetchone()
            if not row:
                return [], 0
            return self._parse_messages(row[0]), (row[1] or 0)
        except sqlite3.Error as e:
            logger.error(f"Erreur SQLite get_messages({id_lbc}): {e}")
            return [], 0

    def save_message(self, id_lbc: str, role: str, content: str) -> None:
        self._ecrire(id_lbc, [(role, content)])

    def save_exchange(self, id_lbc: str, user: str, assistant: str) -> bool:
        """Écrit le message acheteur ET notre réponse dans UNE transaction.

        Tout ou rien. Renvoie True si l'échange est enregistré."""
        return self._ecrire(id_lbc, [("user", user), ("assistant", assistant)])

    def _ecrire(self, id_lbc: str, paires: list[tuple[str, str]]) -> bool:
        try:
            with self.conn:    # transaction : commit si OK, rollback sinon
                self.conn.execute(
                    "INSERT OR IGNORE INTO conversations (id_lbc) VALUES (?)", (id_lbc,))
                row = self.conn.execute(
                    "SELECT messages, nb_envois FROM conversations WHERE id_lbc=?",
                    (id_lbc,)).fetchone()
                messages = self._parse_messages(row[0]) if row else []
                nb = (row[1] or 0) if row else 0
                for role, content in paires:
                    messages.append({"role": role, "content": content})
                    if role == "assistant":
                        nb += 1
                self.conn.execute(
                    "UPDATE conversations SET messages=?, nb_envois=?, updated=? WHERE id_lbc=?",
                    (json.dumps(messages, ensure_ascii=False), nb,
                     datetime.now().isoformat(), id_lbc))
            logger.debug(f"{len(paires)} message(s) sauvegardé(s) pour conv {id_lbc}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Erreur SQLite écriture({id_lbc}): {e}")
            return False

    def build_context(self, id_lbc: str, new_message: str, system_prompt: str,
                      max_hist: int = 8, fil: list | None = None) -> tuple[list, int]:
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

    def close(self) -> None:
        try:
            self.conn.close()
            logger.info("Base SQLite fermée proprement")
        except sqlite3.Error as e:
            logger.error(f"Erreur fermeture SQLite: {e}")


class ConversationManager:
    """Wrapper async pour ne pas bloquer l'event loop (Weakness 1)."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.path.join(BASE_DIR, "conversations.db")
        self._sync = _SyncConversationManager(db_path)

    async def get_messages(self, id_lbc: str) -> tuple[list, int]:
        return await asyncio.to_thread(self._sync.get_messages, id_lbc)

    async def save_message(self, id_lbc: str, role: str, content: str) -> None:
        await asyncio.to_thread(self._sync.save_message, id_lbc, role, content)

    async def save_exchange(self, id_lbc: str, user: str, assistant: str) -> bool:
        return await asyncio.to_thread(self._sync.save_exchange, id_lbc, user, assistant)

    async def build_context(self, id_lbc: str, new_message: str, system_prompt: str,
                            max_hist: int = 8, fil: list | None = None) -> tuple[list, int]:
        return await asyncio.to_thread(self._sync.build_context, id_lbc, new_message,
                                       system_prompt, max_hist, fil)

    async def close(self) -> None:
        await asyncio.to_thread(self._sync.close)

