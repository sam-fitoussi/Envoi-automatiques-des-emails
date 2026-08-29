#!/usr/bin/env python3
"""Décide si un email doit partir maintenant, d'après horaires.json.

Usage : python schedule_gate.py <clé>   (ex. recap_stalling, df_frst)

Codes de sortie :
    0 = c'est le jour et l'heure configurés -> envoyer
    1 = ce n'est pas le moment -> ne rien faire
    2 = configuration invalide (jour ou heure illisible) -> faire échouer le run
        pour que l'erreur soit visible, plutôt que de ne plus jamais envoyer
"""

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def main():
    key = sys.argv[1]
    try:
        with open("horaires.json") as f:
            cfg = json.load(f)[key]
        jour = str(cfg["jour"]).strip().lower()
        heure = int(cfg["heure"])
        if jour not in JOURS or not 0 <= heure <= 23:
            raise ValueError(f"jour={jour!r}, heure={heure!r}")
    except Exception as e:
        print(f"horaires.json invalide pour « {key} » : {e} — "
              f"jours attendus {JOURS}, heure entière entre 0 et 23.")
        sys.exit(2)

    now = datetime.now(ZoneInfo("Europe/Paris"))
    if JOURS[now.weekday()] == jour and now.hour == heure:
        print(f"{key} : c'est l'heure configurée ({jour} {heure}h, Paris) -> envoi.")
        sys.exit(0)
    print(f"{key} : pas maintenant (configuré : {jour} {heure}h ; "
          f"actuellement : {JOURS[now.weekday()]} {now.hour}h, heure de Paris).")
    sys.exit(1)


if __name__ == "__main__":
    main()
