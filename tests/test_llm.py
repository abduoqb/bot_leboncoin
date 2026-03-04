import pytest
from unittest.mock import patch, MagicMock
from llm import generate_reply


class TestGenerateReply:
    def _mock_response(self, content="Bonjour !", status_code=200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = {"message": {"content": content}}
        mock.raise_for_status.return_value = None
        return mock

    @patch("llm.requests.post")
    def test_reponse_normale(self, mock_post):
        mock_post.return_value = self._mock_response("La Clio est dispo !")
        messages = [{"role": "user", "content": "C'est encore dispo ?"}]
        result = generate_reply(messages)
        assert result == "La Clio est dispo !"
        mock_post.assert_called_once()

    @patch("llm.requests.post")
    def test_reponse_stripee(self, mock_post):
        mock_post.return_value = self._mock_response("  Avec espaces  \n")
        result = generate_reply([{"role": "user", "content": "test"}])
        assert result == "Avec espaces"

    @patch("llm.requests.post")
    @patch("llm.time.sleep")  # évite d'attendre dans les tests
    def test_timeout_retry(self, mock_sleep, mock_post):
        import requests
        mock_post.side_effect = [
            requests.exceptions.Timeout("timeout"),
            self._mock_response("Ça a marché au 2e essai"),
        ]
        result = generate_reply([{"role": "user", "content": "test"}])
        assert result == "Ça a marché au 2e essai"
        assert mock_post.call_count == 2

    @patch("llm.requests.post")
    @patch("llm.time.sleep")
    def test_3_timeouts_retourne_none(self, mock_sleep, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.Timeout("timeout")
        result = generate_reply([{"role": "user", "content": "test"}])
        assert result is None
        assert mock_post.call_count == 3

    @patch("llm.requests.post")
    def test_erreur_http_pas_de_retry(self, mock_post):
        import requests
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_post.return_value = response
        result = generate_reply([{"role": "user", "content": "test"}])
        assert result is None
        assert mock_post.call_count == 1  # pas de retry

    @patch("llm.requests.post")
    @patch("llm.time.sleep")
    def test_connection_error_retry(self, mock_sleep, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        result = generate_reply([{"role": "user", "content": "test"}])
        assert result is None
        assert mock_post.call_count == 3  # 3 tentatives

    @patch("llm.requests.post")
    def test_payload_correct(self, mock_post):
        mock_post.return_value = self._mock_response()
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "msg"}]
        generate_reply(messages)

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == "gemma3:4b"
        assert payload["messages"] == messages
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == 0.4
