# Envoi automatique des emails

Deux envois hebdomadaires automatiques, construits sur le même socle
(API Pipedrive en lecture seule → HTML → webhook Zapier → Outlook
samuel@frst.vc) :

| Email | Script | Horaire (dans `horaires.json`) | Bouton manuel |
|---|---|---|---|
| **Recap Stalling** | `weekly_recap.py` | lundi 14h Paris | « 📧 Envoyer le recap Stalling » |
| **DF Frst** | `df_frst.py` | vendredi 19h Paris | « 📧 Envoyer le DF Frst » |

## Panneau de contrôle web (le plus simple pour l'équipe)

**https://envois-frst.samuelfitoussi.workers.dev/1c94923b74819f227e62b68364e1a8bb**

Page réservée à l'équipe (URL secrète, à ne pas partager hors Frst) qui
permet, sans compte GitHub :

- d'**envoyer immédiatement** le Recap Stalling ou le DF Frst (bouton rouge,
  avec confirmation) ;
- de **changer le jour et l'heure** de chaque envoi hebdomadaire (menus
  déroulants, heure de Paris).

Sous le capot : un Cloudflare Worker (code dans `panneau/worker.js`, déployé
sur le compte Cloudflare de Samuel sous le nom `envois-frst`) qui déclenche
les workflows GitHub via un jeton à portée minimale (secret `GITHUB_TOKEN`
du Worker : Actions en écriture + lecture du contenu, sur ce seul repo).
Ce même Worker est aussi **l'horloge des envois automatiques** (voir
ci-dessous) et affiche la date d'expiration du jeton.

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

### Mécanique : qui déclenche l'envoi à l'heure dite ?

**L'horloge, c'est le Worker Cloudflare** (`panneau/worker.js`), via une
**alarme Durable Object** (`HORLOGE`) qui se réarme elle-même toutes les
10 minutes (alarme persistante, relancée par Cloudflare en cas d'échec ;
elle est armée automatiquement à la première visite du panneau). À chaque
passage, pendant l'heure configurée, elle lance le workflow d'envoi — sauf
si une exécution de ce workflow existe déjà depuis le début de l'heure (un
clic manuel compte, donc jamais de doublon). L'email part dans les minutes
qui suivent l'heure pile. Un déclencheur cron Cloudflare (`*/10 * * * *`)
appelle la même logique en second rideau. Pourquoi pas GitHub ? Ses tâches
planifiées (`schedule`) sont exécutées en retard et **souvent sautées** sur
un compte gratuit — le premier planificateur, basé dessus, a manqué ses
deux premiers créneaux réels (lundi 31/08 et vendredi 04/09/2026). Et
pourquoi pas seulement le cron Cloudflare ? Configuré le 04/09, il n'a
produit aucune exécution en plus d'une heure (aucune trace dans les
analytics ni dans le témoin de vie), d'où l'alarme comme horloge principale.

**GitHub reste un filet de sécurité** : `rattrapage.yml` (script
`watchdog.py`) tourne quand GitHub veut bien et vérifie que chaque créneau
récent a donné lieu à un envoi ; sinon il le déclenche lui-même (jusqu'à
4 h après l'heure prévue), et au-delà passe au rouge pour prévenir. Il
utilise le jeton natif de GitHub, donc il fonctionne même si le jeton du
panneau a expiré.

**Alertes** : si le Worker ne parvient pas à déclencher un envoi (jeton
expiré, GitHub injoignable), il envoie un email d'alerte à Samuel via le
Zap ; il envoie aussi un rappel quotidien pendant les 14 jours qui
précèdent l'expiration du jeton GitHub. Une valeur invalide dans
`horaires.json` (ex. « vendredit ») est signalée par le filet (run rouge)
au lieu d'être ignorée en silence.

Pour tester l'horloge sans rien envoyer : ouvrir
`<URL du panneau>/tick` — la page renvoie ce qu'elle ferait maintenant
(simulation ; `?dry=0` pour exécuter réellement). Pour vérifier qu'elle
tourne : `<URL du panneau>/etat` affiche le dernier passage enregistré et
l'heure de la prochaine alarme (et réarme l'alarme si elle avait disparu).

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
Worker Cloudflare envois-frst (alarme toutes les 10 min, lit horaires.json)
   └─ déclenche GitHub Actions (envoyer-stalling.yml / envoyer-df.yml)
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

## Secrets du Worker Cloudflare `envois-frst`

| Secret | Rôle |
|---|---|
| `GITHUB_TOKEN` | Jeton GitHub fine-grained limité à ce repo (Actions : Read and write ; Contents : Read-only). Il expire : la date est affichée en bas du panneau, un rappel part 14 jours avant. Pour le renouveler : recréer le même jeton, puis `PUT …/workers/scripts/envois-frst/secrets` (ou demander à Claude). |
| `ZAPIER_HOOK_URL` | Même URL que le secret GitHub ; sert aux emails d'alerte. |
| `PANEL_TOKEN` (variable) | Segment secret de l'URL du panneau. |
| `ETAT` (espace KV `envois-frst-etat`) | Témoin de vie de l'horloge : heure et compte rendu du dernier passage (alarme ou cron). Visible en bas du panneau (bandeau rouge si silence > 30 min) et sur `<URL du panneau>/etat`. |
| `HORLOGE` (Durable Object, classe `Horloge`, migration `v1` / `new_sqlite_classes`) | L'horloge elle-même : une alarme auto-réarmée toutes les 10 minutes. Si elle disparaissait, une simple visite du panneau (ou de `/etat`) la réarme. |

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
- Les envois automatiques sont déclenchés par le Worker Cloudflare (précis à
  la minute) et vérifiés/rattrapés par `rattrapage.yml` ; l'horaire de
  référence est toujours `horaires.json` sur `main`.
- Aucun échec silencieux : envoi impossible → email d'alerte à Samuel ;
  créneau manqué malgré tout → run « Filet de sécurité » rouge (notification
  GitHub).
