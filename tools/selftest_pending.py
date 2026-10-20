"""
selftest_pending.py — Réponse ni perdue ni doublée : compteur d'échecs,
persistance, écriture atomique, échange SQLite transactionnel.

Aucun navigateur, aucun réseau. Usage : python -u selftest_pending.py
"""

import json
import logging
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import leboncoin as L  # noqa: E402
import conversation as C  # noqa: E402

logging.basicConfig(level=logging.CRITICAL, format="%(message)s")

TMP = tempfile.mkdtemp(prefix="bot_lbc_selftest_pending_")
OK, KO = [0], [0]


def verifie(titre, attendu, obtenu):
    bon = attendu == obtenu
    (OK if bon else KO)[0] += 1
    print(f"  {'✅' if bon else '❌'} {titre}")
    if not bon:
        print(f"     attendu : {attendu}")
        print(f"     obtenu  : {obtenu}")


def client(nom, garder=False):
    p = os.path.join(TMP, f"{nom}.json")
    if not garder and os.path.exists(p):
        os.remove(p)
    L._STATE_FILE = p
    return L.LeboncoinClient(), p


def liste(n):
    return [{"href": f"/messages/id/conv{i}", "preview": f"p{i}", "unread": False}
            for i in range(n)]


def main():
    print("\n" + "=" * 74)
    print("  PENDING / PERSISTANCE / ATOMICITÉ")
    print("=" * 74)

    # 1. pending non incrémenté sans tentative
    c, _ = client("a")
    c._last_previews["/messages/id/conv0"] = "p0"
    c._pending["conv0"] = 0
    for _ in range(10):
        c._candidats(liste(1))
    verifie("10 scans sans tentative : pending reste à 0", 0, c._pending.get("conv0"))
    c._noter_echec("conv0")
    verifie("un échec réel incrémente", 1, c._pending["conv0"])

    # 2. aller-retour de persistance
    c, p = client("b")
    c._last_previews["/messages/id/x"] = "aperçu é"
    c._pending["x"] = 2
    c._ignorees.add("y")
    c._settled.update({"z", "w"})
    c._save_state()
    d, _ = client("b", garder=True)
    verifie("previews rechargés", {"/messages/id/x": "aperçu é"}, d._last_previews)
    verifie("pending rechargé", {"x": 2}, d._pending)
    verifie("ignorées rechargées", {"y"}, d._ignorees)
    verifie("settled rechargés", {"z", "w"}, d._settled)
    with open(p, encoding="utf-8") as f:
        verifie("champ version présent", L._STATE_VERSION, json.load(f).get("version"))

    # 3. rétro-compat ancien format
    p = os.path.join(TMP, "old.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"previews": {"/messages/id/a": "ancien"}}, f)
    L._STATE_FILE = p
    o = L.LeboncoinClient()
    verifie("ancien format : previews lus", {"/messages/id/a": "ancien"}, o._last_previews)
    verifie("ancien format : le reste est vide",
            ({}, set(), set()), (o._pending, o._ignorees, o._settled))

    # 4. fichier corrompu toléré
    p = os.path.join(TMP, "bad.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"previews": {"a"')
    L._STATE_FILE = p
    b = L.LeboncoinClient()
    verifie("état corrompu : démarrage à vide", {}, b._last_previews)

    # 5. écriture atomique : pas de .tmp résiduel ; échec de remplacement = ancien fichier intact
    c, p = client("c")
    c._pending["k"] = 1
    c._save_state()
    verifie("pas de .tmp résiduel", False, os.path.exists(p + ".tmp"))
    avant = open(p, encoding="utf-8").read()
    c._pending["k"] = 9
    vrai_replace = os.replace
    os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("simulé"))
    try:
        c._save_state()          # ne doit pas lever
    finally:
        os.replace = vrai_replace
    verifie("échec de remplacement : ancien fichier intact", avant,
            open(p, encoding="utf-8").read())
    verifie("…et .tmp nettoyé", False, os.path.exists(p + ".tmp"))

    # 6. LRU : une mise à jour rend l'entrée la plus récente
    c, _ = client("d")
    c._set_preview("a", "1")
    c._set_preview("b", "1")
    c._set_preview("a", "2")
    verifie("LRU : 'a' réinséré en dernier", ["b", "a"], list(c._last_previews))

    # 7. anti-doublon : comparaison normalisée
    out = {"messages": [{"side": "in", "text": "salut"},
                        {"side": "out", "text": "Oui, c’est dispo  demain !"}]}
    verifie("texte déjà présent (typographie/insécables) → envoi reconnu", True,
            L._fil_contient_envoi(out, "Oui, c'est dispo demain !"))
    verifie("autre texte → non", False, L._fil_contient_envoi(out, "Non, vendu."))
    verifie("dernière bulle entrante → non", False, L._fil_contient_envoi(
        {"messages": out["messages"] + [{"side": "in", "text": "ok"}]},
        "Oui, c'est dispo demain !"))

    # 8. SQLite
    db = os.path.join(TMP, "conv.db")
    m = C._SyncConversationManager(db)
    verifie("get_messages n'insère pas", (([], 0), 0),
            (m.get_messages("n1"),
             m.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]))
    verifie("save_exchange OK", True, m.save_exchange("c1", "bonjour", "salut"))
    msgs, nb = m.get_messages("c1")
    verifie("deux messages écrits, nb_envois=1",
            ([{"role": "user", "content": "bonjour"},
              {"role": "assistant", "content": "salut"}], 1), (msgs, nb))
    m.conn.execute("""CREATE TRIGGER boom BEFORE UPDATE ON conversations
                      WHEN NEW.messages LIKE '%BOOM%'
                      BEGIN SELECT RAISE(ABORT, 'boom'); END""")
    m.conn.commit()
    verifie("échec en cours de transaction → False", False,
            m.save_exchange("c2", "u", "BOOM"))
    verifie("…rien d'écrit (ni ligne ni message)",
            (([], 0), 0),
            (m.get_messages("c2"),
             m.conn.execute("SELECT COUNT(*) FROM conversations WHERE id_lbc='c2'"
                            ).fetchone()[0]))
    m.conn.execute("INSERT INTO conversations (id_lbc, messages) VALUES ('j1','{pas du json')")
    m.conn.execute("INSERT INTO conversations (id_lbc, messages) VALUES ('j2','{\"a\": 1}')")
    m.conn.commit()
    verifie("JSON corrompu → []", ([], 0), m.get_messages("j1"))
    verifie("JSON non-liste → []", ([], 0), m.get_messages("j2"))
    verifie("save_message ancien API fonctionne sur JSON corrompu", True,
            (m.save_message("j1", "user", "x"), m.get_messages("j1")[0])[1]
            == [{"role": "user", "content": "x"}])
    verifie("WAL actif", "wal",
            m.conn.execute("PRAGMA journal_mode").fetchone()[0].lower())
    m.close()

    print("\n" + "=" * 74)
    print(f"  Réussis : {OK[0]}   Échecs : {KO[0]}")
    print("=" * 74)
    return 1 if KO[0] else 0


if __name__ == "__main__":
    sys.exit(main())
