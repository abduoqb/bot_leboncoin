"""
instance.py — Un seul programme à la fois sur un profil Chrome donné.

Pourquoi c'est indispensable
----------------------------
Le bot pilote un **profil Chrome persistant** (`chrome_profile/`). Un profil
Chrome ne peut être ouvert que par **un seul** Chrome à la fois. Si un second
programme (un outil de diagnostic, ou une seconde instance du bot) tente de
lancer Chrome sur ce même profil pendant que le premier tourne, Chrome ne
démarre pas : il ouvre une **nouvelle fenêtre `about:blank` dans la session
existante**, puis Playwright perd la connexion. Résultat : des fenêtres vides
s'empilent, et l'envoi en cours peut être perturbé.

Ce module pose donc un verrou au niveau du système d'exploitation :
  • `acquerir()`  : le prend (renvoie False si quelqu'un l'a déjà) ;
  • `occupe()`    : test non destructif, pour prévenir avant de se lancer ;
  • `liberer()`   : le relâche.

Le verrou est rendu par **le système** à la mort du processus, même en cas de
crash ou de `kill` : il n'y a jamais de faux « déjà en cours » après un arrêt
brutal.
"""

import os
import sys
import logging

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CHEMIN = os.path.join(_BASE_DIR, "bot.lock")

# Le PID du détenteur est écrit HORS de la zone verrouillée : Windows bloque
# aussi la LECTURE d'un octet verrouillé, donc le mettre à l'offset 0 rendrait
# le PID illisible pour les autres processus.
_OFFSET_PID = 64

_fd = None           # descripteur du verrou détenu par CE processus

if sys.platform == "win32":
    import msvcrt
    _fcntl = None
else:
    import fcntl as _fcntl
    msvcrt = None


def _verrouille(fd) -> bool:
    """Tente de poser un verrou exclusif NON bloquant. True si on l'obtient."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _deverrouille(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    except Exception:
        pass


def acquerir(chemin: str = None) -> bool:
    """Prend le verrou. False si un autre programme le détient déjà."""
    global _fd
    if _fd is not None:
        return True
    chemin = chemin or _CHEMIN
    try:
        fd = os.open(chemin, os.O_CREAT | os.O_RDWR)
    except OSError as e:
        logger.warning(f"Verrou indisponible ({e}) → on continue sans.")
        return True
    if not _verrouille(fd):
        os.close(fd)
        return False
    _fd = fd
    try:
        os.lseek(fd, _OFFSET_PID, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
    except Exception:
        pass
    return True


def occupe(chemin: str = None) -> bool:
    """Vrai si un AUTRE processus détient le verrou (aucun effet de bord)."""
    if _fd is not None:
        return False                     # c'est nous
    chemin = chemin or _CHEMIN
    if not os.path.exists(chemin):
        return False
    try:
        fd = os.open(chemin, os.O_CREAT | os.O_RDWR)
    except OSError:
        return False
    try:
        if _verrouille(fd):
            _deverrouille(fd)
            return False
        return True
    finally:
        os.close(fd)


def detenteur(chemin: str = None):
    """PID du détenteur, pour l'AFFICHER seulement (None si illisible).

    ⚠️ À n'utiliser qu'après `occupe()` ou `acquerir()` : après un processus tué
    brutalement, ce PID peut être périmé. L'autorité, c'est `occupe()`.
    """
    chemin = chemin or _CHEMIN
    try:
        with open(chemin, "rb") as f:
            f.seek(_OFFSET_PID)
            v = f.read(32).decode(errors="replace").strip()
        return v or None
    except Exception:
        return None


def liberer():
    """Relâche le verrou (le système le fait aussi à la mort du processus)."""
    global _fd
    if _fd is None:
        return
    # On efface le PID AVANT de déverrouiller : sinon, deux processus pourraient
    # acquérir le verrou entre-temps et l'ancien effacerait le PID du nouveau.
    try:
        os.lseek(_fd, _OFFSET_PID, os.SEEK_SET)
        os.write(_fd, b" " * 24)
    except Exception:
        pass
    _deverrouille(_fd)
    try:
        os.close(_fd)
    except Exception:
        pass
    _fd = None


def refuser_si_occupe(quoi: str = "Ce programme", chemin: str = None) -> bool:
    """Garde-fou destiné aux outils : True si un bot tourne déjà (à refuser)."""
    if not occupe(chemin):
        return False
    pid = detenteur(chemin)
    print("\n" + "=" * 68)
    print(f"  ⛔ {quoi} ne peut pas démarrer : le bot tourne déjà"
          + (f" (PID {pid})." if pid else "."))
    print("=" * 68)
    print("  Le bot pilote le profil Chrome « chrome_profile ». Un second")
    print("  programme sur ce même profil ne peut pas ouvrir Chrome : il")
    print("  ouvrirait des fenêtres « about:blank » sans jamais fonctionner,")
    print("  et perturberait le bot en cours.")
    print("\n  → Arrête d'abord le bot (Ctrl+C dans sa fenêtre), puis relance.")
    print("=" * 68 + "\n")
    return True
