# Envoi automatique des emails — Recap Stalling hebdo

Script déterministe qui remplace la tâche programmée Claude : chaque lundi
matin, il récupère les deals ouverts des stages **Spotted Hot** (17),
**Stalling tier 1** (37) et **Stalling tier 2** (38) du pipeline Pipedrive,
construit le mail HTML de préparation du point hebdo, et l'envoie depuis
Outlook (samuel@frst.vc) via un webhook Zapier.

## Architecture

```
GitHub Actions (cron lundi 05:00 UTC)
   └─ weekly_recap.py
        ├─ API Pipedrive (lecture seule : GET uniquement)
        │    ├─ /api/v2/deals?stage_id=…&status=open   (pagination par curseur)
        │    ├─ /v1/deals/{id}/participants  →  /api/v2/persons/{id}
        │    └─ /v1/notes?deal_id=…&sort=add_time DESC
        ├─ Construction du HTML (nettoyage des notes, tri par ancienneté,
        │    mise en vert des "AI findings", version texte)
        └─ POST {to, subject, html, text} → webhook Zapier
                                              └─ Zap : Catch Hook → Outlook "Send Email"
```

## Secrets GitHub requis (Settings → Secrets and variables → Actions)

| Secret | Rôle |
|---|---|
| `PIPEDRIVE_API_TOKEN` | Token API Pipedrive (Paramètres → Préférences personnelles → API) |
| `ZAPIER_HOOK_URL` | URL du Catch Hook du Zap qui envoie le mail via Outlook |

## Lancer à la main

Onglet **Actions → Recap Stalling hebdo → Run workflow** :

- `mode` : `dry-run` (génère sans envoyer, HTML récupérable dans l'artefact
  `recap`) ou `send` (envoie réellement via Zapier) ;
- `recipients` : destinataires du mail (pratique pour tester en ne l'envoyant
  qu'à soi) ;
- `push_output` : pousse le HTML généré sur la branche `ci-test-output`
  (debug uniquement, branche à supprimer après usage).

En local : `PIPEDRIVE_API_TOKEN=… python weekly_recap.py --dry-run` puis
ouvrir `out/recap.html`.

## Garde-fous

- Lecture seule sur Pipedrive par construction : le script n'émet que des GET.
- En cas d'erreur (API en panne, token invalide…), le script envoie quand même
  un mail « Aucun deal récupéré — vérifier la connexion Pipedrive » avec la
  trace, et le run GitHub Actions passe au rouge (notification GitHub).
- Le cron `schedule` ne s'exécute que depuis la branche par défaut (`main`) :
  penser à merger pour activer l'envoi hebdomadaire.
