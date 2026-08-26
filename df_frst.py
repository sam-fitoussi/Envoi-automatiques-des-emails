#!/usr/bin/env python3
"""DF Frst — recap hebdomadaire du dealflow (Frst).

Récupère les deals OUVERTS des stages "3 - PM", "2 - Instruction" et
"1 - Call done" du pipeline Pipedrive, construit le mail HTML "DF Frst"
(un bloc par stage : deal en gras → founder en rouge → co-founders en vert →
dernière note en noir), puis l'envoie via le webhook Zapier → Outlook.

Réutilise les briques de weekly_recap.py. Lecture seule sur Pipedrive.

Usage :
    python df_frst.py --dry-run --out-dir out          # génère sans envoyer
    python df_frst.py --send --out-dir out             # génère et envoie

Variables d'environnement :
    PIPEDRIVE_API_TOKEN   (obligatoire)
    ZAPIER_HOOK_URL       (obligatoire avec --send)
"""

import argparse
import html
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from weekly_recap import (
    FIELD_ONELINER,
    GREEN,
    RED,
    clean_note_html,
    custom_field,
    ensure_url,
    fetch_deal_extras,
    fetch_stage_deals,
    fr_date,
    html_to_text,
    parse_dt,
    send_via_zapier,
    step_summary,
    write_outputs,
)

# (nom de section, stage_id) — pipeline 1
STAGES = [
    ("PM", 32),
    ("Instruction", 4),
    ("Call Done", 3),
]

DEFAULT_RECIPIENTS = "samuel@frst.vc,bruno@frst.vc,pierre@frst.vc,lio@frst.vc"
SUBJECT = "DF Frst"


# ---------------------------------------------------------------- rendu

def founder_html(p):
    """Founder en rouge ; lien LinkedIn si dispo, sinon texte simple."""
    name = html.escape(p["name"])
    url = ensure_url(p.get("linkedin"))
    if url:
        return f'<a href="{html.escape(url, quote=True)}" style="color:{RED}; text-decoration:none;">{name}</a>'
    return name


def cofounder_html(p):
    """Co-founder en vert ; lien LinkedIn, sinon repli sur sa fiche Pipedrive."""
    name = html.escape(p["name"])
    url = ensure_url(p.get("linkedin"))
    if not url and p.get("id"):
        url = f"https://app.pipedrive.com/person/{p['id']}"
    if url:
        return f'<a href="{html.escape(url, quote=True)}" style="color:{GREEN}; text-decoration:none;">{name}</a>'
    return name


def person_text(p, pipedrive_fallback=True):
    url = ensure_url(p.get("linkedin"))
    if not url and pipedrive_fallback and p.get("id"):
        url = f"https://app.pipedrive.com/person/{p['id']}"
    return f"{p['name']} ({url})" if url else p["name"]


def render_deal_html(deal, extras):
    deal_id = deal["id"]
    name = html.escape(deal.get("title") or f"Deal {deal_id}")
    oneliner = custom_field(deal, FIELD_ONELINER)

    head = (f'<a href="https://app.pipedrive.com/deal/{deal_id}" '
            f'style="color:#222; text-decoration:none;"><strong>{name}</strong></a>')
    if oneliner:
        head += f" ({html.escape(oneliner)})"
    lines = [f'<p style="margin:0 0 3px; font-size:16px;">{head}</p>']

    founder = extras.get("primary")
    if founder:
        lines.append(f'<p style="margin:0 0 3px; color:{RED};">{founder_html(founder)}</p>')

    cofounders = extras.get("cofounders") or []
    if cofounders:
        lines.append(f'<p style="margin:0 0 3px; color:{GREEN};">'
                     + " ; ".join(cofounder_html(p) for p in cofounders) + "</p>")

    notes = extras.get("notes") or []
    if notes:
        date = fr_date(parse_dt(notes[0].get("add_time")))
        body = clean_note_html(notes[0].get("content")).replace("\n", "<br>")
        lines.append(f'<p style="margin:6px 0 0; color:#222;">Note du {date} : {body}</p>')

    return ('<div style="padding:12px 0; border-top:1px solid #e5e5e5;">'
            + "".join(lines) + "</div>")


def render_deal_text(deal, extras):
    deal_id = deal["id"]
    oneliner = custom_field(deal, FIELD_ONELINER)
    out = [f"* {deal.get('title') or f'Deal {deal_id}'}"
           + (f" ({oneliner})" if oneliner else "")]
    out.append(f"  https://app.pipedrive.com/deal/{deal_id}")
    founder = extras.get("primary")
    if founder:
        out.append(f"  Founder : {person_text(founder, pipedrive_fallback=False)}")
    cofounders = extras.get("cofounders") or []
    if cofounders:
        out.append("  Co-founders : " + " ; ".join(person_text(p) for p in cofounders))
    notes = extras.get("notes") or []
    if notes:
        date = fr_date(parse_dt(notes[0].get("add_time")))
        body = html_to_text(clean_note_html(notes[0].get("content")))
        body = "\n".join("  " + l for l in body.splitlines())
        out.append(f"  Note du {date} :\n{body}")
    return "\n".join(out)


def build_email(sections, warning=None):
    body, text = [], []
    if warning:
        body.append(f'<p style="color:{RED}; font-weight:bold;">{html.escape(warning)}</p>')
        text += [warning.upper(), ""]

    for name, items in sections:
        body.append(f'<h2 style="color:#444; font-size:20px; margin:26px 0 6px; '
                    f'border-bottom:2px solid #ddd; padding-bottom:4px;">{name}</h2>')
        text += ["", f"=== {name} ===", ""]
        if not items:
            body.append('<p style="margin:0;">Aucun deal.</p>')
            text.append("Aucun deal.")
        for deal, extras in items:
            body.append(render_deal_html(deal, extras))
            text.append(render_deal_text(deal, extras))
            text.append("")

    html_doc = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        'Roboto,Helvetica,Arial,sans-serif; font-size:15px; line-height:1.5; '
        'color:#222; max-width:680px; margin:0 auto; padding:12px;">'
        + "".join(body) + "</div>")
    return SUBJECT, html_doc, "\n".join(text)


def build_error_email(err):
    msg = "Aucun deal récupéré — vérifier la connexion Pipedrive."
    html_doc = (
        '<div style="font-family:sans-serif; font-size:15px;">'
        f'<p style="color:{RED}; font-weight:bold;">{msg}</p>'
        f"<pre>{html.escape(err)}</pre></div>")
    return SUBJECT, html_doc, f"{msg}\n\n{err}"


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="envoyer via ZAPIER_HOOK_URL (sinon dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="générer les fichiers sans envoyer (défaut)")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--to", default=DEFAULT_RECIPIENTS,
                    help="destinataires, séparés par des virgules")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    try:
        if not os.environ.get("PIPEDRIVE_API_TOKEN"):
            raise RuntimeError("PIPEDRIVE_API_TOKEN manquant dans l'environnement")

        sections = []
        for name, stage_id in STAGES:
            deals = fetch_stage_deals(stage_id)
            # arrivées les plus récentes dans le stage en premier
            deals.sort(key=lambda d: d.get("stage_change_time") or d.get("add_time") or "",
                       reverse=True)
            with ThreadPoolExecutor(max_workers=6) as pool:
                extras = list(pool.map(fetch_deal_extras, deals))
            sections.append((name, list(zip(deals, extras))))
            print(f"{name} (stage {stage_id}) : {len(deals)} deals")

        total = sum(len(items) for _, items in sections)
        warning = ("Aucun deal récupéré — vérifier la connexion Pipedrive."
                   if total == 0 else None)
        subject, html_doc, text = build_email(sections, warning)
        meta = {"date": fr_date(now), "total": total,
                "counts": {name: len(items) for name, items in sections}}
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        subject, html_doc, text = build_error_email(err)
        write_outputs(args.out_dir, subject, html_doc, text, {"error": True})
        if args.send and os.environ.get("ZAPIER_HOOK_URL"):
            send_via_zapier(subject, html_doc, text, args.to)
        step_summary("**DF Frst : ÉCHEC** — voir les logs.")
        sys.exit(1)

    write_outputs(args.out_dir, subject, html_doc, text, meta)
    print(f"Généré : {meta}")
    step_summary(f"**DF Frst** — {meta['total']} deals : "
                 + ", ".join(f"{v} {k}" for k, v in meta["counts"].items()))

    if args.send:
        send_via_zapier(subject, html_doc, text, args.to)
    else:
        print("Dry-run : aucun envoi.")


if __name__ == "__main__":
    main()
