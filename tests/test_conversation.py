import pytest
import json
from conversation import ConversationManager


@pytest.fixture
def db():
    """Crée un ConversationManager en mémoire pour chaque test."""
    manager = ConversationManager(db_path=":memory:")
    yield manager
    manager.close()


class TestGetMessages:
    def test_nouvelle_conversation(self, db):
        messages, nb = db.get_messages("conv_123")
        assert messages == []
        assert nb == 0

    def test_conversation_existante(self, db):
        db.save_message("conv_1", "user", "Bonjour")
        messages, nb = db.get_messages("conv_1")
        assert len(messages) == 1
        assert messages[0]["content"] == "Bonjour"
        assert nb == 0


class TestSaveMessage:
    def test_save_user_message(self, db):
        db.save_message("conv_1", "user", "Salut")
        messages, nb = db.get_messages("conv_1")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "Salut"}
        assert nb == 0  # user ne compte pas dans nb_envois

    def test_save_assistant_incremente_nb_envois(self, db):
        db.save_message("conv_1", "user", "Salut")
        db.save_message("conv_1", "assistant", "Bonjour !")
        messages, nb = db.get_messages("conv_1")
        assert len(messages) == 2
        assert nb == 1

    def test_multiple_assistant_messages(self, db):
        db.save_message("conv_1", "user", "msg1")
        db.save_message("conv_1", "assistant", "rep1")
        db.save_message("conv_1", "user", "msg2")
        db.save_message("conv_1", "assistant", "rep2")
        _, nb = db.get_messages("conv_1")
        assert nb == 2

    def test_caracteres_speciaux(self, db):
        db.save_message("conv_1", "user", "Prix de la Clio ? 800€ ça va ?")
        messages, _ = db.get_messages("conv_1")
        assert "800€" in messages[0]["content"]


class TestGetNbEnvois:
    def test_conversation_inexistante(self, db):
        assert db.get_nb_envois("conv_999") == 0

    def test_apres_envois(self, db):
        db.save_message("conv_1", "user", "msg")
        db.save_message("conv_1", "assistant", "rep")
        assert db.get_nb_envois("conv_1") == 1


class TestBuildContext:
    def test_contexte_vide(self, db):
        context, nb = db.build_context("conv_new", "Bonjour", "Tu es un vendeur")
        assert len(context) == 2  # system + user
        assert context[0]["role"] == "system"
        assert context[1]["role"] == "user"
        assert context[1]["content"] == "Bonjour"
        assert nb == 0

    def test_contexte_avec_historique(self, db):
        db.save_message("conv_1", "user", "Salut")
        db.save_message("conv_1", "assistant", "Bonjour !")
        context, nb = db.build_context("conv_1", "Le prix ?", "System prompt")
        assert len(context) == 4  # system + 2 historique + nouveau user
        assert context[-1]["content"] == "Le prix ?"
        assert nb == 1

    def test_max_history_limite(self, db):
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            db.save_message("conv_1", role, f"msg_{i}")

        context, _ = db.build_context("conv_1", "nouveau", "System", max_hist=4)
        # system(1) + 4 historique + nouveau user(1) = 6
        assert len(context) == 6
