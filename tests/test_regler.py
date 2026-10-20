"""Un fil réglé (message déjà traité) ne doit plus être rouvert à chaque scan."""
import leboncoin as L


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_STATE_FILE", str(tmp_path / "state.json"))
    return L.LeboncoinClient()


def liste(preview="Bonjour", unread=False):
    return [{"href": "/messages/id/c1", "preview": preview, "unread": unread}]


def test_fil_en_attente_est_rouvert_tant_qu_il_n_est_pas_regle(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    c._pending["c1"] = 0
    assert len(c._candidats(liste())) == 1
    assert len(c._candidats(liste())) == 1   # toujours candidat : c'était le défaut


def test_regler_retire_le_fil_des_candidats(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    c._pending["c1"] = 0
    c._regler("c1")
    assert "c1" not in c._pending
    assert "c1" in c._settled
    c._set_preview("/messages/id/c1", "Bonjour")
    assert c._candidats(liste()) == []


def test_regler_est_persiste(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    c._pending["c1"] = 0
    c._regler("c1")
    c2 = L.LeboncoinClient()
    assert "c1" in c2._settled and "c1" not in c2._pending
