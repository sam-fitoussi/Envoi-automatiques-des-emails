# Envoi automatique des emails

Deux envois hebdomadaires automatiques, construits sur le même socle
(API Pipedrive en lecture seule → HTML → webhook Zapier → Outlook
samuel@frst.vc) :

| Email | Script | Horaire (dans `horaires.json`) | Bouton manuel |
|---|---|---|---|
| **Recap Stalling** | `weekly_recap.py` | lundi 14h Paris | « 📧 Envoyer le recap Stalling » |
| **DF Frst** | `df_frst.py` | vendredi 19h Paris | « 📧 Envoyer le DF Frst » |

## Changer le jour ou l'heure d'un envoi

Les horaires vivent dans **`horaires.json`** (à la racine du repo), en
**heure de Paris** — pas de conversion UTC ni d'histoire d'heure d'été :

```json
"recap_stalling": { "jour": "lundi",    "heure": 14 },
"df_frst":        { "jour": "vendredi", "heure": 19 }
```

Pour modifier : ouvrir `horaires.json` sur GitHub → icône crayon ✏️ →
changer le jour (en minuscules : lundi … dimanche) ou l'heure (entier
0-23) → **Commit changes** directement sur `main`. C'est tout.

Mécanique : le workflow `planificateur.yml` tourne toutes les heures et
envoie chaque email quand le jour et l'heure configurés sont atteints
(l'envoi part dans les minutes qui suivent l'heure pile). Une valeur
invalide (ex. « vendredit ») fait passer le run au rouge au lieu de ne
plus jamais envoyer.

## Envoyer un email à la main (réunion décalée, renvoi…)

Onglet **Actions** → choisir « **📧 Envoyer le recap Stalling** » ou
« **📧 Envoyer le DF Frst** » dans la liste de gauche → **Run workflow**
→ **Run**. L'email part dans la minute aux 4 destinataires habituels,
avec les données Pipedrive du moment. (Marche depuis le navigateur,
mobile compris.)

## Donner accès à un collègue

Il lui faut un compte GitHub (gratuit), puis : **Settings →
Collaborators → Add people**, rôle **Write**. Ce rôle suffit pour les
boutons d'envoi et pour modifier `horaires.json`.

## DF Frst — recap du dealflow (vendredi 19h)

Objet « DF Frst ». Trois blocs : **PM** (stage 32), **Instruction** (stage 4),
**Call Done** (stage 3), avec pour chaque deal ouvert : nom en gras cliquable
vers la fiche CRM + description entre parenthèses, founder en rouge (lien
LinkedIn), co-founders en vert séparés par des points-virgules (lien LinkedIn,
sinon fiche Pipedrive), et la dernière note. Tri : arrivées les plus récentes
dans le stage en premier. `df_frst.py` réutilise les fonctions de
`weekly_recap.py`.

# Recap Stalling hebdo

Script déterministe qui remplace la tâche programmée Claude : il récupère
les deals ouverts des stages **Spotted Hot** (17),
**Stalling tier 1** (37) et **Stalling tier 2** (38) du pipeline Pipedrive,
construit le mail HTML de préparation du point hebdo, et l'envoie depuis
Outlook (samuel@frst.vc) via un webhook Zapier.

## Architecture

```
GitHub Actions (planificateur.yml, toutes les heures + horaires.json)
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

## Lancer à la main avec options (test, debug)

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
