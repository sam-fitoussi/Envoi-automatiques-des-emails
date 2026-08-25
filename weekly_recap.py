#!/usr/bin/env python3
"""Recap Stalling hebdomadaire (Frst).

Récupère les deals OUVERTS des stages "0.1 - Spotted Hot", "0.2 Stalling tier 1"
et "0.3 Stalling tier 2" dans Pipedrive, construit le mail HTML de préparation
du point hebdo, puis l'envoie via un webhook Zapier qui relaie vers Outlook
(samuel@frst.vc).

Lecture seule sur Pipedrive : ce script n'émet que des requêtes GET.

Usage :
    python weekly_recap.py --dry-run --out-dir out          # génère sans envoyer
    python weekly_recap.py --send --out-dir out             # génère et envoie

Variables d'environnement :
    PIPEDRIVE_API_TOKEN   (obligatoire)
    ZAPIER_HOOK_URL       (obligatoire avec --send)
"""

import argparse
import html
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

PARIS = ZoneInfo("Europe/Paris")
API = "https://api.pipedrive.com"

# (nom affiché, stage_id)
STAGES = [
    ("Spotted Hot", 17),
    ("Stalling tier 1", 37),
    ("Stalling tier 2", 38),
]

# Clés des custom fields Pipedrive
FIELD_WEBSITE = "5cd560795da57ba665aa695adf942f214b0b7664"    # deal : site web
FIELD_ONELINER = "9b328b3928bf0d2f93f72f072d7c011fcb1661f3"   # deal : description 1 ligne
FIELD_LINKEDIN = "7e2d3b70a1bf20feb78cfd2f311a209842c7edb4"   # person : URL LinkedIn

DEFAULT_RECIPIENTS = "samuel@frst.vc,bruno@frst.vc,pierre@frst.vc,lio@frst.vc"

BROWN = "#8d5524"   # titres de section
RED = "#c62828"     # titres de deal
GREEN = "#1a7f37"   # partie "AI findings" des notes


# ---------------------------------------------------------------- Pipedrive

def api_get(path, **params):
    token = os.environ["PIPEDRIVE_API_TOKEN"]
    last = None
    for attempt in range(5):
        r = requests.get(f"{API}{path}", params=params,
                         headers={"x-api-token": token}, timeout=30)
        if r.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            last = r
            continue
        r.raise_for_status()
        payload = r.json()
        if not payload.get("success", True):
            raise RuntimeError(f"Pipedrive success=false sur {path}: {str(payload)[:500]}")
        return payload
    raise RuntimeError(f"Rate limit persistant sur {path} (HTTP {last.status_code if last else '?'})")


def fetch_stage_deals(stage_id):
    deals, cursor = [], None
    while True:
        params = {"stage_id": stage_id, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        payload = api_get("/api/v2/deals", **params)
        deals += payload.get("data") or []
        cursor = (payload.get("additional_data") or {}).get("next_cursor")
        if not cursor:
            break
    return [d for d in deals
            if not d.get("is_archived") and not d.get("is_deleted")]


def person_ref(value):
    """person_id peut être un int (v2) ou un objet (v1)."""
    if isinstance(value, dict):
        return value.get("value") or value.get("id")
    return value


def custom_field(obj, key):
    v = (obj.get("custom_fields") or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value") or v.get("label") or v.get("name")
    if isinstance(v, str):
        v = v.strip()
    return v or None


def fetch_person(pid):
    data = api_get(f"/api/v2/persons/{pid}").get("data") or {}
    name = data.get("name") or " ".join(
        filter(None, [data.get("first_name"), data.get("last_name")])).strip()
    return {"name": name or "—", "linkedin": custom_field(data, FIELD_LINKEDIN)}


def fetch_deal_extras(deal):
    """Participants (contact principal + co-fondateurs) et 3 dernières notes."""
    deal_id = deal["id"]
    primary_id = person_ref(deal.get("person_id"))

    ids = []
    part = api_get(f"/v1/deals/{deal_id}/participants", limit=100)
    for item in part.get("data") or []:
        pid = person_ref(item.get("person_id")) or (item.get("person") or {}).get("id")
        if pid and pid not in ids:
            ids.append(pid)
    if primary_id and primary_id not in ids:
        ids.insert(0, primary_id)

    persons = {pid: fetch_person(pid) for pid in ids}

    # getNotes renvoie data: null quand il n'y a aucune note
    notes_payload = api_get("/v1/notes", deal_id=deal_id, limit=5, sort="add_time DESC")
    notes = (notes_payload.get("data") or [])[:3]

    return {
        "primary": persons.get(primary_id),
        "cofounders": [persons[i] for i in ids if i != primary_id],
        "notes": notes,
    }


# ---------------------------------------------------------------- dates

def parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("T", " ").replace("Z", "")
    s = re.sub(r"[+-]\d{2}:\d{2}$", "", s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def fr_date(dt):
    return dt.astimezone(PARIS).strftime("%d/%m/%Y") if dt else "—"


def days_since(dt, now):
    if not dt:
        return None
    return (now.astimezone(PARIS).date() - dt.astimezone(PARIS).date()).days


# ---------------------------------------------------------------- nettoyage HTML des notes

BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
              "ul", "ol", "tr", "table", "blockquote", "pre"}


def repair_broken_tags(raw):
    """Répare les attributs dont le guillemet n'est jamais refermé avant '>'
    (ex. <p style="mso-fareast;broken>), qui feraient avaler le texte suivant
    par le parseur. L'attribut cassé est simplement retiré."""
    return re.sub(r'<([a-zA-Z][a-zA-Z0-9]*)((?:[^>"]*"[^"]*")*[^>"]*?)\s*="[^">]*>',
                  r"<\1\2>", raw or "")


def clean_note_html(raw):
    """HTML sale de Pipedrive -> texte avec \n et liens <a> propres (échappé)."""
    raw = repair_broken_tags(raw)
    soup = BeautifulSoup(raw, "html.parser")
    parts = []

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(("text", str(child)))
            elif isinstance(child, Tag):
                name = (child.name or "").lower()
                if name == "br":
                    parts.append(("nl", "\n"))
                elif name == "a":
                    href = (child.get("href") or "").strip()
                    text = child.get_text(" ", strip=True) or href
                    if href.lower().startswith("mailto:"):
                        parts.append(("text", text if "@" in text else href[7:]))
                    elif href:
                        parts.append(("link", (href, text)))
                    else:
                        parts.append(("text", text))
                elif name == "li":
                    parts.append(("nl", "\n"))
                    parts.append(("text", "• "))
                    walk(child)
                    parts.append(("nl", "\n"))
                elif name in BLOCK_TAGS:
                    parts.append(("nl", "\n"))
                    walk(child)
                    parts.append(("nl", "\n"))
                elif name in ("style", "script", "head"):
                    continue
                else:  # span, b, strong, em, font… -> contenu seulement
                    walk(child)

    walk(soup)

    out = []
    for kind, val in parts:
        if kind == "text":
            out.append(html.escape(val.replace("\xa0", " ")))
        elif kind == "nl":
            out.append("\n")
        else:
            href, text = val
            out.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(text)}</a>')
    s = "".join(out)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    result = linkify(s)

    # Un attribut avec guillemet non fermé fait avaler le texte suivant par le
    # parseur ; si le strip naïf des balises retient nettement plus de contenu,
    # c'est qu'on est dans ce cas -> on lui fait confiance.
    fallback = naive_strip(raw)
    if visible_len(fallback) > visible_len(result) * 1.1 + 5:
        return fallback
    return result


def naive_strip(raw):
    s = raw or ""
    s = re.sub(r"(?is)<(style|script|head)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|ul|ol|blockquote)>", "\n", s)
    s = re.sub(r"<[^>]*>", " ", s)
    s = html.escape(html.unescape(s).replace("\xa0", " "))
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return linkify(s)


def visible_len(cleaned):
    return len(re.sub(r"\s", "", html_to_text(cleaned)))


def linkify(s):
    """Rend cliquables les URLs nues (hors liens déjà posés)."""
    def repl(m):
        url, trail = m.group(1), ""
        while url and url[-1] in ".,;:)]}":
            trail = url[-1] + trail
            url = url[:-1]
        return f'<a href="{url}">{url}</a>{trail}'
    return re.sub(r'(?<!["\'>=])(https?://[^\s<]+)', repl, s)


def colorize_ai_findings(note_html):
    """Met en vert tout ce qui suit "AI findings" jusqu'à la fin de la note."""
    m = re.search(r"AI ?findings", note_html, re.IGNORECASE)
    if not m:
        return note_html
    i = m.start()
    return (note_html[:i]
            + f'<span style="color:{GREEN}">' + note_html[i:] + "</span>")


def html_to_text(s):
    s = re.sub(r'<a href="([^"]*)">([^<]*)</a>',
               lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(2)} ({m.group(1)})",
               s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


# ---------------------------------------------------------------- rendu

def ensure_url(u):
    if not u:
        return None
    return u if re.match(r"^https?://", u, re.IGNORECASE) else "https://" + u


def person_link(p):
    name = html.escape(p["name"])
    url = ensure_url(p.get("linkedin"))
    if url:
        return f'<a href="{html.escape(url, quote=True)}" style="color:#1a56a0;">{name}</a>'
    return name


def person_text(p):
    url = ensure_url(p.get("linkedin"))
    return f"{p['name']} ({url})" if url else p["name"]


def render_deal_html(deal, extras, now):
    deal_id = deal["id"]
    title = html.escape(deal.get("title") or f"Deal {deal_id}")
    added = parse_dt(deal.get("add_time"))
    days = days_since(added, now)
    oneliner = custom_field(deal, FIELD_ONELINER)
    website = ensure_url(custom_field(deal, FIELD_WEBSITE))

    lines = []
    age = f" — dans le CRM depuis {days} jours" if days is not None else ""
    lines.append(
        f'<p style="margin:0 0 4px;">'
        f'<a href="https://app.pipedrive.com/deal/{deal_id}" '
        f'style="color:{RED}; font-weight:bold; font-size:16px; text-decoration:none;">{title}</a>'
        f'{age}</p>')

    l2 = []
    if oneliner:
        l2.append(html.escape(oneliner))
    if website:
        l2.append(f'<a href="{html.escape(website, quote=True)}">{html.escape(website)}</a>')
    if l2:
        lines.append(f'<p style="margin:0 0 4px;">{" — ".join(l2)}</p>')

    founder = extras.get("primary")
    lines.append(f'<p style="margin:0 0 4px;">Founder : {person_link(founder) if founder else "—"}</p>')

    cofounders = extras.get("cofounders") or []
    if cofounders:
        label = "Co-founders" if len(cofounders) > 1 else "Co-founder"
        lines.append(f'<p style="margin:0 0 4px;">{label} : '
                     + ", ".join(person_link(p) for p in cofounders) + "</p>")

    for note in extras.get("notes") or []:
        date = fr_date(parse_dt(note.get("add_time")))
        body = colorize_ai_findings(clean_note_html(note.get("content"))).replace("\n", "<br>")
        lines.append(f'<p style="margin:8px 0 4px;"><strong>Note du {date}</strong> :<br>{body}</p>')
    if not (extras.get("notes") or []):
        lines.append('<p style="margin:8px 0 4px;">Aucune note.</p>')

    return ('<div style="padding:14px 0; border-top:1px solid #ddd;">'
            + "".join(lines) + "</div>")


def render_deal_text(deal, extras, now):
    deal_id = deal["id"]
    added = parse_dt(deal.get("add_time"))
    days = days_since(added, now)
    oneliner = custom_field(deal, FIELD_ONELINER)
    website = ensure_url(custom_field(deal, FIELD_WEBSITE))

    out = [f"* {deal.get('title') or f'Deal {deal_id}'}"
           + (f" — dans le CRM depuis {days} jours" if days is not None else "")]
    out.append(f"  https://app.pipedrive.com/deal/{deal_id}")
    if oneliner or website:
        out.append("  " + " — ".join(filter(None, [oneliner, website])))
    founder = extras.get("primary")
    out.append(f"  Founder : {person_text(founder) if founder else '—'}")
    cofounders = extras.get("cofounders") or []
    if cofounders:
        out.append("  Co-founder(s) : " + ", ".join(person_text(p) for p in cofounders))
    for note in extras.get("notes") or []:
        date = fr_date(parse_dt(note.get("add_time")))
        body = html_to_text(clean_note_html(note.get("content")))
        body = "\n".join("  " + l for l in body.splitlines())
        out.append(f"  Note du {date} :\n{body}")
    if not (extras.get("notes") or []):
        out.append("  Aucune note.")
    return "\n".join(out)


def build_email(sections, now, warning=None):
    """sections : liste de (nom, [(deal, extras), ...]) triés."""
    date = now.astimezone(PARIS).strftime("%d/%m/%Y")
    subject = f"Recap Stalling — {date}"

    counters_html = "<br>".join(
        f"<strong>{len(items)}</strong> {name}" for name, items in sections)
    counters_text = "\n".join(f"{len(items)} {name}" for name, items in sections)

    body = [f'<p style="margin:0 0 16px;">{counters_html}</p>']
    text = [f"RECAP STALLING — {date}", "", counters_text, ""]

    if warning:
        body.append(f'<p style="color:{RED}; font-weight:bold;">{html.escape(warning)}</p>')
        text += [warning.upper(), ""]

    for name, items in sections:
        body.append(f'<h2 style="color:{BROWN}; font-size:19px; margin:24px 0 6px;">{name}</h2>')
        text += ["", f"=== {name} ===", ""]
        if not items:
            body.append('<p style="margin:0;">Aucun deal.</p>')
            text.append("Aucun deal.")
        for deal, extras in items:
            body.append(render_deal_html(deal, extras, now))
            text.append(render_deal_text(deal, extras, now))
            text.append("")

    html_doc = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        'Roboto,Helvetica,Arial,sans-serif; font-size:15px; line-height:1.5; '
        'color:#222; max-width:680px; margin:0 auto; padding:12px;">'
        + "".join(body) + "</div>")
    return subject, html_doc, "\n".join(text)


def build_error_email(now, err):
    date = now.astimezone(PARIS).strftime("%d/%m/%Y")
    subject = f"Recap Stalling — {date}"
    msg = "Aucun deal récupéré — vérifier la connexion Pipedrive."
    html_doc = (
        '<div style="font-family:sans-serif; font-size:15px;">'
        f'<p style="color:{RED}; font-weight:bold;">{msg}</p>'
        f"<pre>{html.escape(err)}</pre></div>")
    return subject, html_doc, f"{msg}\n\n{err}"


# ---------------------------------------------------------------- envoi / sortie

def send_via_zapier(subject, html_doc, text, recipients):
    hook = os.environ["ZAPIER_HOOK_URL"]
    r = requests.post(hook, json={
        "to": recipients,
        "subject": subject,
        "html": html_doc,
        "text": text,
    }, timeout=30)
    r.raise_for_status()
    print(f"Envoyé via Zapier ({r.status_code}) à : {recipients}")


def write_outputs(out_dir, subject, html_doc, text, meta):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "recap.html"), "w") as f:
        f.write(html_doc)
    with open(os.path.join(out_dir, "recap.txt"), "w") as f:
        f.write(f"Objet : {subject}\n\n{text}")
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def step_summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


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
            deals.sort(key=lambda d: d.get("add_time") or "9999")
            with ThreadPoolExecutor(max_workers=6) as pool:
                extras = list(pool.map(fetch_deal_extras, deals))
            sections.append((name, list(zip(deals, extras))))
            print(f"{name} (stage {stage_id}) : {len(deals)} deals")

        total = sum(len(items) for _, items in sections)
        warning = ("Aucun deal récupéré — vérifier la connexion Pipedrive."
                   if total == 0 else None)
        subject, html_doc, text = build_email(sections, now, warning)
        meta = {"date": fr_date(now), "total": total,
                "counts": {name: len(items) for name, items in sections}}
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        subject, html_doc, text = build_error_email(now, err)
        write_outputs(args.out_dir, subject, html_doc, text, {"error": True})
        if args.send and os.environ.get("ZAPIER_HOOK_URL"):
            send_via_zapier(subject, html_doc, text, args.to)
        step_summary("**Recap Stalling : ÉCHEC** — voir les logs.")
        sys.exit(1)

    write_outputs(args.out_dir, subject, html_doc, text, meta)
    print(f"Généré : {meta}")
    step_summary(f"**Recap Stalling** — {meta['total']} deals : "
                 + ", ".join(f"{v} {k}" for k, v in meta["counts"].items()))

    if args.send:
        send_via_zapier(subject, html_doc, text, args.to)
    else:
        print("Dry-run : aucun envoi.")


if __name__ == "__main__":
    main()
