#!/usr/bin/env python3
"""Filet de sécurité des envois automatiques.

L'horloge principale est le Worker Cloudflare (panneau/worker.js), qui
déclenche chaque envoi à l'heure configurée dans horaires.json. GitHub exécute
ses tâches planifiées avec retard et en saute beaucoup : ce script ne sert donc
qu'à vérifier, quand il tourne, que chaque créneau récent a bien donné lieu à
un envoi — et à le rattraper sinon.

Pour chaque email :
  - créneau = dernier (jour, heure) configuré déjà passé (heure de Paris) ;
  - moins de 30 min après le créneau : on laisse l'horloge Cloudflare agir ;
  - une exécution du workflow d'envoi existe depuis le créneau : rien à faire
    (un clic manuel compte aussi) ;
  - sinon, jusqu'à 4 h après : on déclenche l'envoi (rattrapage) ;
  - sinon, jusqu'à 24 h après : créneau manqué -> le run passe au rouge.

Codes de sortie : 0 ok / 1 créneau manqué / 2 configuration invalide.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO = os.environ.get("GITHUB_REPOSITORY", "sam-fitoussi/Envoi-automatiques-des-emails")
WORKFLOWS = {"recap_stalling": "envoyer-stalling.yml", "df_frst": "envoyer-df.yml"}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
PARIS = ZoneInfo("Europe/Paris")

DELAI_GRACE = timedelta(minutes=30)
FENETRE_RATTRAPAGE = timedelta(hours=4)
FENETRE_ALARME = timedelta(hours=24)


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "envois-frst-watchdog",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def dernier_creneau(jour, heure, now):
    """Dernier instant (heure pleine, Paris) correspondant à (jour, heure) et <= now."""
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        if JOURS[t.weekday()] == jour and t.hour == heure:
            return t
        t -= timedelta(hours=1)
    return None


def envois_depuis(wf, depuis_utc):
    runs = api(f"/actions/workflows/{wf}/runs?per_page=10").get("workflow_runs") or []
    return [r for r in runs
            if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= depuis_utc]


def resume(ligne):
    print(ligne)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(ligne + "\n\n")


def main():
    with open("horaires.json") as f:
        cfg = json.load(f)
    now = datetime.now(PARIS)
    manques, invalides = [], []

    for key, wf in WORKFLOWS.items():
        c = cfg.get(key) or {}
        jour = str(c.get("jour", "")).strip().lower()
        try:
            heure = int(c.get("heure"))
        except (TypeError, ValueError):
            heure = -1
        if jour not in JOURS or not 0 <= heure <= 23:
            invalides.append(key)
            resume(f"❌ {key} : horaires.json invalide (jour={jour!r}, heure={c.get('heure')!r})")
            continue

        creneau = dernier_creneau(jour, heure, now)
        age = now - creneau
        libelle = f"{key} (créneau {jour} {heure}h, il y a {int(age.total_seconds() // 60)} min)"

        if age < DELAI_GRACE:
            print(f"{libelle} : créneau en cours, l'horloge Cloudflare s'en charge.")
            continue
        deja = envois_depuis(wf, creneau.astimezone(timezone.utc))
        if deja:
            print(f"{libelle} : déjà envoyé ({len(deja)} exécution(s) de {wf}).")
            continue
        if age <= FENETRE_RATTRAPAGE:
            api(f"/actions/workflows/{wf}/dispatches", "POST", {"ref": "main"})
            resume(f"🛟 RATTRAPAGE {libelle} : aucun envoi trouvé, envoi déclenché maintenant.")
        elif age <= FENETRE_ALARME:
            manques.append(key)
            resume(f"🚨 CRÉNEAU MANQUÉ {libelle} : aucun envoi, et trop tard pour rattraper "
                   f"(fenêtre de {int(FENETRE_RATTRAPAGE.total_seconds() // 3600)} h dépassée).")
        else:
            print(f"{libelle} : créneau ancien sans envoi, hors fenêtre de surveillance.")

    if invalides:
        sys.exit(2)
    if manques:
        sys.exit(1)


if __name__ == "__main__":
    main()
