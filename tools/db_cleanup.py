"""
db_cleanup.py — Entretien de conversations.db (liste par défaut, supprime sur --apply).

La base sert à deux choses : anti-doublon et trace. Elle accumule en revanche
des lignes vides (créées automatiquement à la première lecture d'une
conversation) et des restes de tests.

Par défaut l'outil ne fait QUE lister — rien n'est supprimé. Avec --apply, une
sauvegarde horodatée de la base est créée avant toute suppression.

Usage :
    python -u db_cleanup.py                      # liste tout, ne supprime rien
    python -u db_cleanup.py --vides              # ce qui serait supprimé (lignes vides)
    python -u db_cleanup.py --tests              # restes de tests (selftest*, test*…)
    python -u db_cleanup.py --avant 2026-06-01   # fils non touchés depuis cette date
    python -u db_cleanup.py --ids a1b2,c3d4      # ids précis
    python -u db_cleanup.py --vides --tests --apply   # applique (avec sauvegarde)
"""

import os
import sys
import shutil
import sqlite3
import argparse
from datetime import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRÉFIXES_TEST = ("selftest", "test", "essai", "demo")


def _resume(messages, largeur=58):
    if not messages:
        return "(vide)"
    dernier = (messages[-1].get("content") or "").replace("\n", " ").strip()
    return dernier[:largeur] + ("…" if len(dernier) > largeur else "")


def charger(db_path):
    if not os.path.exists(db_path):
        print(f"⚠️  Base introuvable : {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    lignes = []
    for r in conn.execute("SELECT id_lbc, messages, nb_envois, updated "
                          "FROM conversations ORDER BY updated DESC"):
        import json
        try:
            msgs = json.loads(r["messages"] or "[]")
        except Exception:
            msgs = []
        lignes.append({
            "id": r["id_lbc"], "messages": msgs,
            "nb_envois": r["nb_envois"], "updated": r["updated"] or "",
        })
    return conn, lignes


def afficher(lignes):
    print(f"\n{'id':38} {'msgs':>4} {'env':>4}  {'dernière activité':19}  dernier message")
    print("─" * 132)
    for l in lignes:
        print(f"{l['id'][:38]:38} {len(l['messages']):>4} {l['nb_envois']:>4}  "
              f"{str(l['updated'])[:19]:19}  {_resume(l['messages'])}")
    print(f"\n{len(lignes)} ligne(s) au total.")


def selectionner(lignes, args):
    """Renvoie la liste des lignes correspondant aux filtres demandés."""
    choisies, motifs = [], []
    if args.vides:
        choisies += [l for l in lignes if not l["messages"]]
        motifs.append("lignes vides")
    if args.tests:
        choisies += [l for l in lignes
                     if l["id"].lower().startswith(PRÉFIXES_TEST)]
        motifs.append("restes de tests")
    if args.avant:
        choisies += [l for l in lignes if str(l["updated"])[:10] < args.avant]
        motifs.append(f"non touchées depuis le {args.avant}")
    if args.ids:
        voulus = {i.strip() for i in args.ids.split(",") if i.strip()}
        choisies += [l for l in lignes if l["id"] in voulus]
        motifs.append("ids demandés")

    # dédoublonnage en conservant l'ordre
    vus, uniques = set(), []
    for l in choisies:
        if l["id"] not in vus:
            vus.add(l["id"])
            uniques.append(l)
    return uniques, motifs


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Entretien de conversations.db")
    p.add_argument("--db", default=os.path.join(_BASE, "conversations.db"))
    p.add_argument("--vides", action="store_true",
                   help="lignes sans aucun message (créées à la volée)")
    p.add_argument("--tests", action="store_true",
                   help="identifiants de tests (selftest*, test*, essai*, demo*)")
    p.add_argument("--avant", metavar="AAAA-MM-JJ",
                   help="fils dont la dernière activité est antérieure à cette date")
    p.add_argument("--ids", help="identifiants précis, séparés par des virgules")
    p.add_argument("--apply", action="store_true",
                   help="supprime réellement (sinon : simple simulation)")
    args = p.parse_args()

    conn, lignes = charger(args.db)
    afficher(lignes)

    if not (args.vides or args.tests or args.avant or args.ids):
        print("\nAucun filtre → rien à supprimer. (--vides, --tests, --avant, --ids)")
        conn.close()
        return 0

    cible, motifs = selectionner(lignes, args)
    print(f"\nFiltres : {', '.join(motifs)}")
    if not cible:
        print("Rien à supprimer. ✅")
        conn.close()
        return 0

    print(f"\n{len(cible)} ligne(s) concernée(s) :")
    for l in cible:
        print(f"  • {l['id']:38} {len(l['messages']):>3} msg  {_resume(l['messages'], 44)}")

    if not args.apply:
        print("\n🔒 Simulation : rien n'a été supprimé. Ajoute --apply pour le faire.")
        conn.close()
        return 0

    sauvegarde = f"{args.db}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(args.db, sauvegarde)
    print(f"\n💾 Sauvegarde : {sauvegarde}")

    conn.executemany("DELETE FROM conversations WHERE id_lbc=?",
                     [(l["id"],) for l in cible])
    conn.commit()
    conn.close()
    print(f"🗑️  {len(cible)} ligne(s) supprimée(s).")
    print("ℹ️  Les conversations concernées seront recréées au prochain scan où "
          "elles réapparaissent — le fil de la page fait de toute façon foi "
          "pour le contexte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
