"""Haalt vóór elke pijplijn-run zelf de nieuwste code binnen, zodat leveringen niet van handmatig git-werk afhangen.

Veilig by design: alleen stdlib; elke fout laat de pijplijn gewoon met de huidige code draaien; bij een conflict
wordt de merge afgebroken (niets verandert). Uitzetten: AUTO_UPDATE=off. Log: automation/self_update.log.
"""
import os
import subprocess
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH = os.environ.get("AUTO_UPDATE_BRANCH", "main-b0gds1")
LOG_FILE = os.path.join(BASE_DIR, "automation", "self_update.log")


def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
    except OSError:
        pass


def _git(args, repo=None, timeout=90):
    ident = []
    if not subprocess.run(["git", "config", "user.email"], cwd=repo or BASE_DIR, capture_output=True,
                          text=True).stdout.strip():
        ident = ["-c", "user.name=Nognize pipeline", "-c", "user.email=pipeline@localhost"]
    return subprocess.run(["git"] + ident + args, cwd=repo or BASE_DIR, capture_output=True, text=True,
                          timeout=timeout)


def update(repo=None, branch=None):
    """-> 'updated' | 'current' | 'skipped' | 'conflict'. Gooit nooit."""
    branch = branch or BRANCH
    try:
        if _git(["fetch", "origin", branch], repo).returncode != 0:
            _log("fetch mislukt, ga door met huidige code")
            return "skipped"
        ref = "origin/" + branch
        if _git(["merge-base", "--is-ancestor", ref, "HEAD"], repo).returncode == 0:
            return "current"
        if _git(["diff", "--cached", "--quiet"], repo).returncode != 0:
            _git(["commit", "-m", "Leveringen vastgelegd (self_update)"], repo)
        r = _git(["merge", "--no-edit", ref], repo)
        if r.returncode != 0:
            _git(["merge", "--abort"], repo)
            _log("CONFLICT/geweigerd, merge afgebroken: " + (r.stdout + r.stderr).strip()[:300])
            return "conflict"
        _log("bijgewerkt naar " + _git(["rev-parse", "--short", "HEAD"], repo).stdout.strip())
        return "updated"
    except Exception as e:  # noqa: BLE001 - de pijplijn mag hier nooit op stuklopen
        _log("fout (genegeerd): %r" % (e,))
        return "skipped"


def update_and_reexec():
    if os.environ.get("AUTO_UPDATE", "on").strip().lower() == "off" or os.environ.get("AUTO_UPDATE_DONE"):
        return
    os.environ["AUTO_UPDATE_DONE"] = "1"
    if update() == "updated":
        os.execv(sys.executable, [sys.executable] + sys.argv)
