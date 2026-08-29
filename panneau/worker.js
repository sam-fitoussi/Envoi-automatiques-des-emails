// Panneau de contrôle des envois automatiques Frst.
// Pilote le repo GitHub sam-fitoussi/Envoi-automatiques-des-emails :
// - envoi manuel immédiat des deux emails (workflow_dispatch)
// - modification des horaires hebdomadaires (workflow set-horaire.yml)
// Accès par URL secrète (PANEL_TOKEN) ; jeton GitHub en secret (GITHUB_TOKEN).

const REPO = "sam-fitoussi/Envoi-automatiques-des-emails";
const JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const EMAILS = {
  recap_stalling: { titre: "Recap Stalling", wfEnvoi: "envoyer-stalling.yml" },
  df_frst: { titre: "DF Frst (Dealflow)", wfEnvoi: "envoyer-df.yml" },
};

function gh(env, path, init = {}) {
  return fetch("https://api.github.com/repos/" + REPO + path, {
    ...init,
    headers: {
      "Authorization": "Bearer " + env.GITHUB_TOKEN,
      "Accept": "application/vnd.github+json",
      "User-Agent": "envois-frst-panel",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const redirect = (base, kind, msg) => new Response(null, {
  status: 303,
  headers: { "Location": base + "?" + kind + "=" + encodeURIComponent(msg) },
});

async function handlePost(request, env, base) {
  const form = await request.formData();
  const action = form.get("action");
  try {
    if (action === "envoyer") {
      const email = form.get("email");
      if (!EMAILS[email]) throw new Error("email inconnu");
      const r = await gh(env, "/actions/workflows/" + EMAILS[email].wfEnvoi + "/dispatches",
        { method: "POST", body: JSON.stringify({ ref: "main" }) });
      if (r.status !== 204) throw new Error("GitHub a répondu " + r.status);
      return redirect(base, "ok", "Envoi « " + EMAILS[email].titre +
        " » déclenché — l'email part d'ici 1 à 2 minutes.");
    }
    if (action === "horaire") {
      const email = form.get("email");
      const jour = form.get("jour");
      const heure = parseInt(form.get("heure"), 10);
      if (!EMAILS[email] || !JOURS.includes(jour) || !(heure >= 0 && heure <= 23)) {
        throw new Error("valeurs invalides");
      }
      const r = await gh(env, "/actions/workflows/set-horaire.yml/dispatches", {
        method: "POST",
        body: JSON.stringify({ ref: "main", inputs: { email, jour, heure: String(heure) } }),
      });
      if (r.status !== 204) throw new Error("GitHub a répondu " + r.status);
      return redirect(base, "ok", "Nouvel horaire « " + EMAILS[email].titre + " » : " +
        jour + " à " + heure + "h (heure de Paris) — pris en compte d'ici 1 minute.");
    }
    throw new Error("action inconnue");
  } catch (e) {
    return redirect(base, "err", "Échec : " + e.message);
  }
}

async function currentHoraires(env) {
  const r = await gh(env, "/contents/horaires.json?ref=main",
    { headers: { "Accept": "application/vnd.github.raw" } });
  if (!r.ok) throw new Error("lecture des horaires impossible (GitHub " + r.status + ")");
  return JSON.parse(await r.text());
}

function carte(base, key, horaires) {
  const info = EMAILS[key];
  const cfg = (horaires && horaires[key]) || null;
  const jourActuel = cfg ? String(cfg.jour) : null;
  const heureActuelle = cfg ? parseInt(cfg.heure, 10) : null;

  const optionsJour = JOURS.map((j) =>
    `<option value="${j}"${j === jourActuel ? " selected" : ""}>${j}</option>`).join("");
  const optionsHeure = Array.from({ length: 24 }, (_, h) =>
    `<option value="${h}"${h === heureActuelle ? " selected" : ""}>${h} h</option>`).join("");

  const actuel = cfg
    ? `Envoi automatique : <strong>${esc(jourActuel)} à ${heureActuelle} h</strong> (heure de Paris)`
    : `Horaire actuel indisponible`;

  return `
  <section class="carte">
    <h2>${info.titre}</h2>
    <p class="actuel">${actuel}</p>
    <form method="POST" action="${base}" class="ligne">
      <input type="hidden" name="action" value="horaire">
      <input type="hidden" name="email" value="${key}">
      <label>Jour <select name="jour">${optionsJour}</select></label>
      <label>Heure <select name="heure">${optionsHeure}</select></label>
      <button type="submit" class="secondaire">🕒 Changer l'horaire</button>
    </form>
    <form method="POST" action="${base}"
          onsubmit="return confirm('Envoyer « ${info.titre} » maintenant aux 4 destinataires ?')">
      <input type="hidden" name="action" value="envoyer">
      <input type="hidden" name="email" value="${key}">
      <button type="submit" class="primaire">📧 Envoyer maintenant</button>
    </form>
  </section>`;
}

function pageHtml(base, horaires, ok, err, tokenManquant) {
  const bandeaux = [];
  if (ok) bandeaux.push(`<div class="bandeau ok">✅ ${esc(ok)}</div>`);
  if (err) bandeaux.push(`<div class="bandeau err">⚠️ ${esc(err)}</div>`);
  if (tokenManquant) {
    bandeaux.push(`<div class="bandeau err">⚠️ Configuration à terminer : le jeton GitHub
      n'est pas encore installé — les boutons ne fonctionneront pas.</div>`);
  }
  return `<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Envois Frst</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
         Helvetica, Arial, sans-serif; background: #f5f4f2; color: #222;
         margin: 0; padding: 24px 16px; font-size: 16px; }
  main { max-width: 560px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sous-titre { color: #666; margin: 0 0 20px; font-size: 14px; }
  .carte { background: #fff; border: 1px solid #e3e1dd; border-radius: 12px;
           padding: 18px 20px; margin-bottom: 16px;
           box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
  .carte h2 { margin: 0 0 4px; font-size: 18px; }
  .actuel { margin: 0 0 14px; color: #444; font-size: 14px; }
  .ligne { display: flex; flex-wrap: wrap; gap: 10px; align-items: end;
           margin-bottom: 12px; }
  label { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
          color: #555; }
  select { font-size: 15px; padding: 7px 8px; border: 1px solid #ccc;
           border-radius: 8px; background: #fff; }
  button { font-size: 15px; padding: 9px 14px; border-radius: 8px;
           border: none; cursor: pointer; }
  .secondaire { background: #eceae6; color: #222; }
  .secondaire:hover { background: #e0ddd7; }
  .primaire { background: #c62828; color: #fff; width: 100%; }
  .primaire:hover { background: #a92222; }
  .bandeau { border-radius: 10px; padding: 12px 14px; margin-bottom: 16px;
             font-size: 14px; }
  .ok { background: #e6f4ea; color: #1a7f37; border: 1px solid #b7e1c2; }
  .err { background: #fdecea; color: #b3261e; border: 1px solid #f5c6c2; }
  footer { color: #999; font-size: 12px; margin-top: 20px; text-align: center; }
  footer a { color: #999; }
</style>
</head>
<body>
<main>
  <h1>Envois automatiques Frst</h1>
  <p class="sous-titre">Recap Stalling &amp; DF Frst — envoi immédiat ou changement d'horaire.
     Destinataires : samuel, bruno, pierre, lio.</p>
  ${bandeaux.join("\n")}
  ${carte(base, "recap_stalling", horaires)}
  ${carte(base, "df_frst", horaires)}
  <footer>Pilote le repo <a href="https://github.com/${REPO}">${REPO}</a> —
    page réservée à l'équipe, ne pas partager l'URL.</footer>
</main>
</body>
</html>`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const seg = url.pathname.split("/").filter(Boolean);
    if (seg[0] !== env.PANEL_TOKEN) return new Response("Introuvable", { status: 404 });
    const base = url.origin + "/" + env.PANEL_TOKEN;

    if (request.method === "POST") {
      if (!env.GITHUB_TOKEN) return redirect(base, "err", "Le jeton GitHub n'est pas encore configuré.");
      return handlePost(request, env, base);
    }

    let horaires = null;
    let err = url.searchParams.get("err");
    const ok = url.searchParams.get("ok");
    const tokenManquant = !env.GITHUB_TOKEN;
    if (!tokenManquant) {
      try { horaires = await currentHoraires(env); } catch (e) { err = err || e.message; }
    }
    return new Response(pageHtml(base, horaires, ok, err, tokenManquant), {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "X-Robots-Tag": "noindex, nofollow",
        "Cache-Control": "no-store",
      },
    });
  },
};
