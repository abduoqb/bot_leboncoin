"""
selftest_instance.py — Le verrou « un seul bot par profil Chrome » fonctionne-t-il ?

Enjeu : un profil Chrome ne peut être ouvert que par UN Chrome. Un second
programme qui tente de le lancer pendant que le bot tourne n'obtient rien :
Chrome ouvre une fenêtre « about:blank » dans la session existante et
Playwright perd la connexion. Ce test verrouille le garde-fou, y compris le
cas important du processus tué brutalement (le verrou doit être libéré par le
système, sans faux « déjà en cours »).

Aucun navigateur, aucun réseau.
Usage : python -u selftest_instance.py
"""

import os
import sys
import time
import subprocess
import tempfile

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import instance  # noqa: E402

# On n'utilise JAMAIS le vrai bot.lock : le test ne doit pas gêner un bot en cours.
CHEMIN = os.path.join(tempfile.gettempdir(), "bot_lbc_selftest.lock")

OK = [0]
KO = [0]


def verifie(titre, attendu, obtenu):
    bon = attendu == obtenu
    (OK if bon else KO)[0] += 1
    print(f"  {'✅' if bon else '❌'} {titre}")
    if not bon:
        print(f"     attendu : {attendu!r}")
        print(f"     obtenu  : {obtenu!r}")


def sous_processus_detenteur(secondes: float):
    """Lance un vrai processus qui prend le verrou et le garde `secondes`."""
    code = (
        "import sys; sys.path.insert(0, r'{r}');"
        "import instance, time;"
        "ok = instance.acquerir(r'{c}');"
        "print('DETIENT' if ok else 'REFUSE', flush=True);"
        "time.sleep({s})"
    ).format(r=RACINE, c=CHEMIN, s=secondes)
    p = subprocess.Popen([sys.executable, "-c", code],
                         stdout=subprocess.PIPE, text=True,
                         creationflags=(subprocess.CREATE_NO_WINDOW
                                        if os.name == "nt" else 0))
    ligne = p.stdout.readline().strip()
    assert ligne == "DETIENT", f"sous-processus : {ligne!r}"
    return p


def main():
    if os.path.exists(CHEMIN):
        os.remove(CHEMIN)

    print("\n" + "=" * 74)
    print("  VERROU D'INSTANCE UNIQUE (un seul bot par profil Chrome)")
    print("=" * 74)

    # ── 1. Verrou libre au départ ──
    verifie("verrou libre : personne ne le détient", False, instance.occupe(CHEMIN))
    verifie("aucun détenteur annoncé", None, instance.detenteur(CHEMIN))

    # ── 2. Un autre processus le détient ──
    p = sous_processus_detenteur(30)
    try:
        time.sleep(0.3)
        verifie("verrou détenu par un autre processus : détecté", True,
                instance.occupe(CHEMIN))
        verifie("le PID du détenteur est lisible", str(p.pid), instance.detenteur(CHEMIN))
        verifie("le second processus ne peut PAS le prendre", False,
                instance.acquerir(CHEMIN))
        verifie("l'outil est bien refusé", True,
                instance.refuser_si_occupe("Test", CHEMIN))
    finally:
        # ── 3. Processus TUÉ brutalement : le système doit libérer le verrou ──
        p.kill()
        p.wait()

    time.sleep(0.4)
    verifie("processus tué (sans libération propre) : verrou libéré", False,
            instance.occupe(CHEMIN))

    # ── 4. On peut alors le prendre, et c'est nous qui le détenons ──
    verifie("on peut le prendre à notre tour", True, instance.acquerir(CHEMIN))
    verifie("détenu par nous : plus considéré comme occupé", False,
            instance.occupe(CHEMIN))
    verifie("notre PID est écrit", str(os.getpid()), instance.detenteur(CHEMIN))
    verifie("acquerir() deux fois de suite est sans effet", True,
            instance.acquerir(CHEMIN))

    # ── 5. Libération explicite ──
    instance.liberer()
    verifie("après liberer() : libre", False, instance.occupe(CHEMIN))
    verifie("aucun détenteur", None, instance.detenteur(CHEMIN))

    if os.path.exists(CHEMIN):
        try:
            os.remove(CHEMIN)
        except OSError:
            pass

    print("\n" + "=" * 74)
    print(f"  Réussis : {OK[0]}   Échecs : {KO[0]}")
    print("=" * 74)
    return 1 if KO[0] else 0


if __name__ == "__main__":
    sys.exit(main())
