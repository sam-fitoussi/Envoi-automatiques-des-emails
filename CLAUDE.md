# Repères pour les agents qui interviennent sur ce repo

Lis d'abord `README.md` (mode d'emploi complet). Ce fichier ne contient que
ce qu'il faut savoir avant de toucher au code.

## Ce que fait le projet

Deux emails hebdomadaires (Recap Stalling, DF Frst) générés depuis Pipedrive
(lecture seule : GET uniquement) et envoyés depuis Outlook samuel@frst.vc via
un webhook Zapier. Destinataires : samuel, bruno, pierre, lio @frst.vc.
Langue du projet : français (code, commits, docs, panneau).

## Où tourne quoi

| Brique | Où | Rôle |
|---|---|---|
| `weekly_recap.py`, `df_frst.py` | GitHub Actions | Fabrication et envoi des emails. `df_frst.py` réutilise les fonctions de `weekly_recap.py`. |
| `horaires.json` | repo (`main`) | Jour/heure de chaque envoi, **heure de Paris**. Seule config lue par l'horloge. |
| `watchdog.py` + `rattrapage.yml` | GitHub Actions (cron, peu fiable) | Filet de sécurité : rattrape un créneau manqué jusqu'à 4 h après. |
| `panneau/worker.js` | Cloudflare Worker `envois-frst` (compte de Samuel) | Panneau web (URL secrète) **et** horloge des envois. |

## L'horloge : ce qu'il ne faut pas casser

- L'horloge est une **alarme Durable Object** (`HORLOGE`, classe `Horloge`)
  qui se réarme toutes les 10 min et est ré-armée à chaque visite du panneau.
- **Pas de cron Cloudflare** : celui essayé le 04/09/2026 ne s'est jamais
  déclenché, et un second déclencheur simultané risquerait un envoi en double.
  Ne pas en rajouter. Ne pas non plus remettre de `schedule` GitHub comme
  horloge principale (créneaux sautés sur compte gratuit).
- Un passage ne déclenche l'envoi que si aucune exécution du workflow d'envoi
  n'existe depuis le début de l'heure : c'est ce qui évite les doublons avec
  un clic manuel. Garder cette vérification.
- Vérifier que l'horloge vit : `<URL du panneau>/etat` (dernier passage,
  prochaine alarme). Simuler un passage : `<URL du panneau>/tick`.

## Déployer le Worker

Le fichier `panneau/worker.js` du repo est la copie de référence ; la version
qui tourne est déployée à la main sur Cloudflare (API `PUT
/accounts/{id}/workers/scripts/envois-frst`, multipart, `main_module:
worker.js`, `compatibility_date: 2025-01-01`). Bindings à redéclarer à chaque
déploiement : `kv_namespace ETAT` (id `3c99a83ec6644358869d6f1d569470c6`),
`plain_text PANEL_TOKEN`, `durable_object_namespace HORLOGE` (classe
`Horloge`), plus `keep_bindings: ["secret_text"]` pour conserver
`GITHUB_TOKEN` et `ZAPIER_HOOK_URL`. La migration `v1` (`new_sqlite_classes:
["Horloge"]`) est déjà appliquée : ne pas la renvoyer. Après déploiement,
ouvrir `/etat` pour confirmer que l'alarme est armée.

## Conventions de travail

- Toute évolution passe par une PR mergée dans `main` (les workflows sont
  déclenchés sur `main`, `horaires.json` est lu sur `main`).
- Tester avec le workflow « Recap Stalling hebdo » / « DF Frst hebdo » en
  `dry-run`, puis en `send` vers soi seul (`recipients`), avant l'équipe.
- Les secrets ne sont jamais dans le repo : `PIPEDRIVE_API_TOKEN` et
  `ZAPIER_HOOK_URL` côté GitHub ; `GITHUB_TOKEN` (jeton fine-grained limité à
  ce repo), `ZAPIER_HOOK_URL` et `PANEL_TOKEN` côté Worker.
- Identifiants Pipedrive figés dans le code : stages 17/37/38 (Stalling),
  32/4/3 (PM/Instruction/Call done) ; clés des champs personnalisés en tête
  des scripts.
- Ne jamais écrire dans Pipedrive depuis ce projet.
