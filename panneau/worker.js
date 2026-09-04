// Panneau de contrôle et horloge des envois automatiques Frst.
// Pilote le repo GitHub sam-fitoussi/Envoi-automatiques-des-emails :
// - page web (URL secrète PANEL_TOKEN) : envoi manuel immédiat des deux emails,
//   modification des horaires (workflow set-horaire.yml) ;
// - déclencheur cron (toutes les 10 min) : lance l'envoi de chaque email à
//   l'heure configurée dans horaires.json, sans doublon (vérifie l'historique
//   des exécutions GitHub) ;
// - alertes par email via le webhook Zapier (ZAPIER_HOOK_URL) si un envoi
//   automatique ne peut pas être déclenché, ou si le jeton GitHub expire bientôt.
// Secrets/variables du Worker : PANEL_TOKEN, GITHUB_TOKEN, ZAPIER_HOOK_URL ;
// stockage KV ETAT (témoin de vie de l'horloge : heure du dernier passage).

const REPO = "sam-fitoussi/Envoi-automatiques-des-emails";
const JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const EMAILS = {
  recap_stalling: { titre: "Recap Stalling", wfEnvoi: "envoyer-stalling.yml" },
  df_frst: { titre: "DF Frst (Dealflow)", wfEnvoi: "envoyer-df.yml" },
};
const ALERTE_TO = "samuel@frst.vc";
const JOURS_AVANT_EXPIRATION = 14;
const SILENCE_HORLOGE_MAX_MIN = 30;   // au-delà, le panneau signale l'horloge muette

// ---------------------------------------------------------------- témoin de vie

async function lireDernierTick(env) {
  if (!env.ETAT) return null;
  try { return JSON.parse((await env.ETAT.get("dernier_tick")) || "null"); }
  catch (e) { return null; }
}

async function noterTick(env, rapport, cron) {
  if (!env.ETAT) return;
  await env.ETAT.put("dernier_tick", JSON.stringify({ at: new Date().toISOString(), cron, rapport }));
}

function etatHorloge(dernier) {
  if (!dernier) return { ok: false, texte: "Horloge : aucun passage enregistré pour l'instant." };
  const min = Math.round((Date.now() - new Date(dernier.at)) / 60000);
  const h = new Date(dernier.at).toLocaleTimeString("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" });
  return { ok: min <= SILENCE_HORLOGE_MAX_MIN, minutes: min,
           texte: `Horloge : dernier passage à ${h} (heure de Paris), il y a ${min} min.` };
}

// ---------------------------------------------------------------- GitHub

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

// Date d'expiration du jeton fine-grained, renvoyée par GitHub dans un en-tête.
function expirationJeton(resp) {
  const h = resp && resp.headers.get("github-authentication-token-expiration");
  if (!h) return null;
  const d = new Date(h.trim().replace(" UTC", "").replace(" ", "T") + "Z");
  return isNaN(d) ? null : d;
}

async function lireHoraires(env) {
  const r = await gh(env, "/contents/horaires.json?ref=main",
    { headers: { "Accept": "application/vnd.github.raw" } });
  if (!r.ok) throw new Error("lecture des horaires impossible (GitHub " + r.status + ")");
  return { horaires: JSON.parse(await r.text()), expiration: expirationJeton(r) };
}

async function declencher(env, wf, inputs) {
  const body = inputs ? { ref: "main", inputs } : { ref: "main" };
  const r = await gh(env, "/actions/workflows/" + wf + "/dispatches",
    { method: "POST", body: JSON.stringify(body) });
  if (r.status !== 204) throw new Error("GitHub a répondu " + r.status);
}

// Nombre d'exécutions du workflow d'envoi créées depuis un instant donné.
async function envoisDepuis(env, wf, depuis) {
  const r = await gh(env, "/actions/workflows/" + wf + "/runs?per_page=10");
  if (!r.ok) throw new Error("lecture de l'historique impossible (GitHub " + r.status + ")");
  const runs = (await r.json()).workflow_runs || [];
  return runs.filter((x) => new Date(x.created_at) >= depuis).length;
}

// ---------------------------------------------------------------- temps (Paris)

function maintenantParis(now = new Date()) {
  const p = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Paris" }));
  return { jour: JOURS[(p.getDay() + 6) % 7], heure: p.getHours(), minute: p.getMinutes(), local: p };
}

function configValide(cfg) {
  if (!cfg) return null;
  const jour = String(cfg.jour || "").trim().toLowerCase();
  const heure = parseInt(cfg.heure, 10);
  if (!JOURS.includes(jour) || !(heure >= 0 && heure <= 23)) return null;
  return { jour, heure };
}

// Prochain créneau (pour l'affichage) : "vendredi 11/09 à 19 h".
function prochainCreneau(cfg, now = new Date()) {
  const { local } = maintenantParis(now);
  for (let k = 1; k <= 24 * 8; k++) {
    const d = new Date(local.getTime() + k * 3600000);
    if (JOURS[(d.getDay() + 6) % 7] === cfg.jour && d.getHours() === cfg.heure) {
      const dd = String(d.getDate()).padStart(2, "0");
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      return `${cfg.jour} ${dd}/${mm} à ${cfg.heure} h`;
    }
  }
  return null;
}

// ---------------------------------------------------------------- alertes

async function alerter(env, sujet, lignes) {
  if (!env.ZAPIER_HOOK_URL) return false;
  const texte = lignes.join("\n\n");
  const html = '<div style="font-family:sans-serif;font-size:15px;line-height:1.5">'
    + lignes.map((l) => "<p>" + esc(l) + "</p>").join("") + "</div>";
  const r = await fetch(env.ZAPIER_HOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: ALERTE_TO, subject: sujet, html, text: texte }),
  });
  return r.ok;
}

const AIDE_JETON = "Pour rétablir le panneau : GitHub → Settings → Developer settings → "
  + "Fine-grained tokens → nouveau jeton limité au repo Envoi-automatiques-des-emails "
  + "(Actions : Read and write ; Contents : Read-only), puis le réinstaller comme secret "
  + "GITHUB_TOKEN du Worker Cloudflare envois-frst (ou le donner à Claude, qui le fera).";

// ---------------------------------------------------------------- horloge

// Un « tick » : appelé toutes les 10 minutes par le cron, ou à la main via
// /tick (mode simulation par défaut). Renvoie un compte rendu.
async function tick(env, { dry = false, now = new Date() } = {}) {
  const t = maintenantParis(now);
  const rapport = { heure_paris: `${t.jour} ${t.heure}h${String(t.minute).padStart(2, "0")}`,
                    simulation: dry, decisions: [] };
  let lu;
  try {
    lu = await lireHoraires(env);
  } catch (e) {
    rapport.erreur = e.message;
    if (!dry && t.heure === 9 && t.minute >= 50) {
      await alerter(env, "⚠️ Envois automatiques Frst : GitHub inaccessible", [
        "Le planificateur Cloudflare n'arrive plus à lire horaires.json sur GitHub (" + e.message + ").",
        "Les envois automatiques ne seront pas déclenchés par le panneau tant que ce n'est pas réglé ; "
        + "le filet de sécurité GitHub prendra le relais avec retard.",
        AIDE_JETON,
      ]);
      rapport.alerte = "envoyée";
    }
    return rapport;
  }

  // Le créneau en cours a commencé à l'heure pile (Paris et UTC ont des heures pleines).
  const debutCreneau = new Date(Math.floor(now.getTime() / 3600000) * 3600000);

  for (const [key, info] of Object.entries(EMAILS)) {
    const cfg = configValide(lu.horaires[key]);
    const d = { email: key, configure: cfg ? `${cfg.jour} ${cfg.heure}h` : "invalide" };
    rapport.decisions.push(d);
    if (!cfg) { d.action = "config invalide, rien fait"; continue; }
    if (cfg.jour !== t.jour || cfg.heure !== t.heure) { d.action = "pas le créneau"; continue; }
    try {
      const deja = await envoisDepuis(env, info.wfEnvoi, debutCreneau);
      if (deja > 0) { d.action = `déjà envoyé (${deja} exécution(s) depuis ${debutCreneau.toISOString()})`; continue; }
      if (dry) { d.action = "SIMULATION : déclencherait l'envoi maintenant"; continue; }
      await declencher(env, info.wfEnvoi);
      d.action = "envoi déclenché";
    } catch (e) {
      d.action = "échec : " + e.message;
      if (!dry && t.minute >= 50) {
        await alerter(env, `⚠️ Envoi automatique « ${info.titre} » non déclenché`, [
          `Le planificateur Cloudflare n'a pas réussi à déclencher l'envoi de « ${info.titre} » `
          + `prévu ${cfg.jour} à ${cfg.heure}h (${e.message}).`,
          "Cause la plus probable : le jeton GitHub du panneau a expiré ou a été révoqué. "
          + "Le filet de sécurité GitHub tentera un rattrapage dans les heures qui suivent ; "
          + "en attendant, l'envoi manuel reste possible depuis l'onglet Actions du repo.",
          AIDE_JETON,
        ]);
        d.alerte = "envoyée";
      }
    }
  }

  // Rappel avant expiration du jeton : une fois par jour, à 9h.
  if (lu.expiration) {
    const jours = Math.floor((lu.expiration - now) / 86400000);
    rapport.jeton_expire_dans_jours = jours;
    if (!dry && jours <= JOURS_AVANT_EXPIRATION && t.heure === 9 && t.minute < 10) {
      await alerter(env, `⏳ Le jeton GitHub du panneau des envois expire dans ${jours} jour(s)`, [
        `Le jeton GitHub utilisé par le panneau et l'horloge des envois automatiques expire le `
        + `${lu.expiration.toLocaleDateString("fr-FR")}. Passé cette date, les envois automatiques `
        + "ne seront plus déclenchés à l'heure (seulement rattrapés avec retard par GitHub) et "
        + "les boutons du panneau ne fonctionneront plus.",
        AIDE_JETON,
      ]);
      rapport.rappel_jeton = "envoyé";
    }
  }
  return rapport;
}

// ---------------------------------------------------------------- page web

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
      await declencher(env, EMAILS[email].wfEnvoi);
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
      await declencher(env, "set-horaire.yml", { email, jour, heure: String(heure) });
      return redirect(base, "ok", "Nouvel horaire « " + EMAILS[email].titre + " » : " +
        jour + " à " + heure + "h (heure de Paris) — pris en compte d'ici 1 minute.");
    }
    throw new Error("action inconnue");
  } catch (e) {
    return redirect(base, "err", "Échec : " + e.message);
  }
}

function carte(base, key, horaires) {
  const info = EMAILS[key];
  const cfg = configValide(horaires && horaires[key]);
  const optionsJour = JOURS.map((j) =>
    `<option value="${j}"${cfg && j === cfg.jour ? " selected" : ""}>${j}</option>`).join("");
  const optionsHeure = Array.from({ length: 24 }, (_, h) =>
    `<option value="${h}"${cfg && h === cfg.heure ? " selected" : ""}>${h} h</option>`).join("");
  const prochain = cfg ? prochainCreneau(cfg) : null;
  const actuel = cfg
    ? `Envoi automatique : <strong>${esc(cfg.jour)} à ${cfg.heure} h</strong> (heure de Paris)`
      + (prochain ? ` — prochain : ${esc(prochain)}` : "")
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

function pageHtml(base, lu, ok, err, tokenManquant, horloge) {
  const bandeaux = [];
  if (horloge && !horloge.ok) {
    bandeaux.push(`<div class="bandeau err">⚠️ ${esc(horloge.texte)} Les envois automatiques
      reposent sur ce passage régulier : si le silence dure, vérifier le Worker Cloudflare.</div>`);
  }
  if (ok) bandeaux.push(`<div class="bandeau ok">✅ ${esc(ok)}</div>`);
  if (err) bandeaux.push(`<div class="bandeau err">⚠️ ${esc(err)}</div>`);
  if (tokenManquant) {
    bandeaux.push(`<div class="bandeau err">⚠️ Configuration à terminer : le jeton GitHub
      n'est pas encore installé — les boutons ne fonctionneront pas.</div>`);
  }
  let jeton = "";
  if (lu && lu.expiration) {
    const jours = Math.floor((lu.expiration - Date.now()) / 86400000);
    const date = lu.expiration.toLocaleDateString("fr-FR");
    jeton = `Jeton GitHub valable jusqu'au ${date} (${jours} jours).`;
    if (jours <= JOURS_AVANT_EXPIRATION) {
      bandeaux.push(`<div class="bandeau err">⏳ Le jeton GitHub du panneau expire le ${date}
        (dans ${jours} jours) : à renouveler, sinon les envois automatiques et ces boutons
        s'arrêteront.</div>`);
    }
  }
  const horaires = lu ? lu.horaires : null;
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
  footer { color: #999; font-size: 12px; margin-top: 20px; text-align: center;
           line-height: 1.6; }
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
    page réservée à l'équipe, ne pas partager l'URL.<br>${horloge ? esc(horloge.texte) + " " : ""}${esc(jeton)}</footer>
</main>
</body>
</html>`;
}

// ---------------------------------------------------------------- points d'entrée

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

    // /tick : exécute l'horloge à la main. Simulation sauf ?dry=0.
    if (seg[1] === "tick") {
      const dry = url.searchParams.get("dry") !== "0";
      const rapport = await tick(env, { dry });
      rapport.dernier_passage_cron = await lireDernierTick(env);
      return new Response(JSON.stringify(rapport, null, 2),
        { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
    }
    // /etat : témoin de vie de l'horloge (dernier passage du cron).
    if (seg[1] === "etat") {
      return new Response(JSON.stringify(await lireDernierTick(env), null, 2),
        { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
    }

    let lu = null;
    let err = url.searchParams.get("err");
    const ok = url.searchParams.get("ok");
    const tokenManquant = !env.GITHUB_TOKEN;
    if (!tokenManquant) {
      try { lu = await lireHoraires(env); } catch (e) { err = err || e.message; }
    }
    const horloge = etatHorloge(await lireDernierTick(env));
    return new Response(pageHtml(base, lu, ok, err, tokenManquant, horloge), {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "X-Robots-Tag": "noindex, nofollow",
        "Cache-Control": "no-store",
      },
    });
  },

  // Déclencheur cron Cloudflare (toutes les 10 minutes).
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      let rapport;
      try { rapport = await tick(env, { dry: false }); }
      catch (e) { rapport = { erreur: "tick en échec : " + e.message }; }
      console.log(JSON.stringify(rapport));
      await noterTick(env, rapport, event.cron);
    })());
  },
};
