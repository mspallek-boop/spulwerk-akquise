"""Lokales Web-Dashboard (nur Standardbibliothek, http.server).

Laeuft ausschliesslich lokal (127.0.0.1). Zeigt die Pipeline, laesst Leads
durchsehen, Entwuerfe lesen/bearbeiten und Status setzen. Es versendet nichts.
Die Recherche-Kommandos (suche/anreichern) laufen bewusst weiter ueber die CLI,
damit langlaufende Netzabfragen nicht im Browser haengen.
"""

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, db, gmail, score

# Zustand des laufenden Gmail-Uebertrags. Der Push laeuft im Hintergrund-
# Thread, damit der Browser nicht minutenlang auf die Antwort wartet.
PUSH = {
    "laeuft": False, "meldung": "", "angelegt": 0, "uebersprungen": 0,
    "gesamt": 0, "fehler": None, "fertig_am": None,
}
PUSH_SPERRE = threading.Lock()


def _push_lauf(min_score, prio, limit):
    from . import gmail_imap
    cfg = config.lade_config()
    conn = db.verbinde()
    try:
        eintraege, verworfen, _ = gmail.warteschlange(
            conn, min_score=min_score, prio=prio, limit=limit
        )
        PUSH["gesamt"] = len(eintraege)
        if not eintraege:
            PUSH["meldung"] = "Nichts zu tun — keine versandfertigen Entwürfe."
            return
        PUSH["meldung"] = "Verbinde mit Gmail ..."

        def melde(text):
            PUSH["meldung"] = text

        ergebnis = gmail_imap.lege_entwuerfe_an(cfg, eintraege, melder=melde)
        gmail.markiere(conn, ergebnis["angelegt"])
        schon_da = [i for i, grund in ergebnis["uebersprungen"] if "schon" in grund]
        gmail.markiere(conn, schon_da)
        PUSH["angelegt"] = len(ergebnis["angelegt"])
        PUSH["uebersprungen"] = len(ergebnis["uebersprungen"])
        PUSH["meldung"] = "Fertig: %d angelegt, %d übersprungen%s" % (
            PUSH["angelegt"], PUSH["uebersprungen"],
            (", %d mangelhafte verworfen" % len(verworfen)) if verworfen else "",
        )
    except Exception as fehler:
        PUSH["fehler"] = str(fehler)
        PUSH["meldung"] = "Abgebrochen: %s" % fehler
    finally:
        conn.close()
        PUSH["laeuft"] = False
        PUSH["fertig_am"] = db.jetzt()

SEITE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spulwerk Akquise</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#fbfbf8; --paper:#ffffff; --ink:#16211a; --blau:#1e3cff; --sand:#e5dcbf;
    --muted:rgba(22,33,26,.62); --muted2:rgba(22,33,26,.42);
    --line:rgba(22,33,26,.16); --line2:rgba(22,33,26,.28);
    --sandbg:rgba(229,220,191,.32);
    --serif:"Cormorant Garamond",Georgia,serif;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
    --titel:"Cinzel",Georgia,serif;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:400 17px/1.55 var(--serif); background:var(--bg); color:var(--ink);
         -webkit-font-smoothing:antialiased; }
  .mono { font-family:var(--mono); }
  header { display:flex; align-items:center; gap:16px; padding:20px 30px;
           border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5;
           background:rgba(251,251,248,.9); backdrop-filter:blur(6px); }
  header .logo { font-family:var(--titel); font-weight:500; letter-spacing:6px;
                 font-size:19px; color:var(--ink); }
  header .logo b { font-weight:600; color:var(--blau); }
  header .punkt { width:9px; height:9px; border-radius:50%; background:var(--blau); display:inline-block; }
  header .hinweis { margin-left:auto; color:var(--muted); font-size:12px; font-family:var(--mono);
                    text-transform:uppercase; letter-spacing:1px; }
  .wrap { padding:30px; max-width:1220px; margin:0 auto; }
  .eyebrow { font-family:var(--mono); font-size:11px; letter-spacing:2px; text-transform:uppercase;
             color:var(--blau); margin:0 0 14px; }
  .karten { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:0;
            border:1px solid var(--line); border-radius:2px; overflow:hidden; margin-bottom:30px; background:var(--paper); }
  .karte { padding:18px 20px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
  .karte .zahl { font-family:var(--titel); font-weight:500; font-size:34px; line-height:1; color:var(--ink); }
  .karte .label { color:var(--muted); font-size:10.5px; font-family:var(--mono); text-transform:uppercase;
                  letter-spacing:1.5px; margin-top:8px; }
  .filter { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; align-items:center; }
  select, input, button, textarea { font-family:var(--mono); font-size:13px; color:var(--ink);
           background:var(--paper); border:1px solid var(--line2); border-radius:2px; padding:9px 11px; }
  select:focus, input:focus, textarea:focus { outline:none; border-color:var(--blau); }
  button { cursor:pointer; text-transform:uppercase; letter-spacing:1px; font-size:11px; }
  button.primary { background:var(--blau); border-color:var(--blau); color:#fff; font-weight:500; }
  button.primary:hover { background:#16211a; border-color:#16211a; }
  .anzahl { margin-left:auto; color:var(--muted); font-family:var(--mono); font-size:12px;
            text-transform:uppercase; letter-spacing:1px; }
  table { width:100%; border-collapse:collapse; background:var(--paper); border:1px solid var(--line); }
  th, td { text-align:left; padding:12px 14px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:400; font-size:10.5px; font-family:var(--mono);
       text-transform:uppercase; letter-spacing:1.5px; }
  td { font-size:16px; }
  td.name { font-weight:500; font-size:18px; }
  td .kmono { font-family:var(--mono); font-size:12.5px; color:var(--muted); }
  tr:hover td { background:var(--sandbg); cursor:pointer; }
  .prio { display:inline-block; width:26px; height:26px; line-height:26px; text-align:center;
          border-radius:2px; font-family:var(--mono); font-weight:500; font-size:12px; }
  .prio.A { background:var(--blau); color:#fff; }
  .prio.B { background:var(--ink); color:var(--bg); }
  .prio.C { background:var(--sand); color:var(--ink); }
  .prio.D { background:transparent; color:var(--muted2); border:1px solid var(--line2); }
  .score { font-family:var(--titel); font-weight:500; font-size:19px; }
  .pill { font-family:var(--mono); font-size:10.5px; padding:3px 9px; border-radius:2px;
          background:var(--sandbg); border:1px solid var(--line); color:var(--muted);
          text-transform:uppercase; letter-spacing:.8px; }
  .overlay { position:fixed; inset:0; background:rgba(22,33,26,.5); display:none; z-index:20;
             backdrop-filter:blur(2px); }
  .overlay.auf { display:flex; }
  .modal { background:var(--bg); border:1px solid var(--line2); border-radius:2px; margin:auto;
           width:min(780px,94vw); max-height:90vh; overflow:auto; padding:0;
           box-shadow:0 30px 80px rgba(22,33,26,.25); }
  .modal .kopf { padding:24px 28px 20px; border-bottom:1px solid var(--line); position:sticky; top:0;
                 background:var(--bg); z-index:2; }
  .modal .kopf .eyebrow { margin-bottom:10px; }
  .modal .koerper { padding:22px 28px 28px; }
  .modal h2 { margin:0; font-family:var(--titel); font-weight:500; font-size:28px; letter-spacing:.5px; }
  .zeile { display:flex; gap:10px; margin:7px 0; font-size:16px; }
  .zeile .k { color:var(--muted); min-width:130px; font-family:var(--mono); font-size:11px;
              text-transform:uppercase; letter-spacing:1px; padding-top:3px; }
  .signale { background:var(--sandbg); border-left:2px solid var(--blau); padding:12px 16px;
             margin:16px 0; font-size:15px; color:var(--ink); font-style:italic; }
  .tabs { display:flex; gap:0; margin:18px 0 0; border-bottom:1px solid var(--line); }
  .tab { padding:9px 16px; background:transparent; border:none; border-bottom:2px solid transparent;
         cursor:pointer; font-family:var(--mono); font-size:11px; text-transform:uppercase;
         letter-spacing:1px; color:var(--muted); }
  .tab.aktiv { color:var(--blau); border-bottom-color:var(--blau); }
  .entwurf { display:none; padding-top:16px; }
  .entwurf.auf { display:block; }
  .entwurf textarea { width:100%; min-height:240px; font-family:var(--mono); font-size:12.5px;
                      line-height:1.6; resize:vertical; background:var(--paper); }
  .betreff { width:100%; margin-bottom:8px; }
  .aktionen { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; align-items:center; }
  .aktionen label { font-family:var(--mono); font-size:10.5px; text-transform:uppercase;
                    letter-spacing:1px; color:var(--muted); }
  .schliessen { float:right; cursor:pointer; color:var(--muted); font-size:26px; line-height:1;
                font-family:var(--serif); }
  .leer { color:var(--muted); text-align:center; padding:50px; font-style:italic; font-size:17px; }
  .toast { position:fixed; bottom:26px; left:50%; transform:translateX(-50%); background:var(--ink);
           color:var(--bg); padding:11px 20px; border-radius:2px; font-family:var(--mono); font-size:12px;
           text-transform:uppercase; letter-spacing:1px; display:none; z-index:40; }
  a { color:var(--blau); text-decoration:none; border-bottom:1px solid var(--line2); }
  a:hover { border-bottom-color:var(--blau); }
  .postfach { border:1px solid var(--line); background:var(--paper); border-radius:2px;
              padding:20px 22px; margin-bottom:30px; }
  .postfach .kopfzeile { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  .postfach h3 { margin:0; font-family:var(--titel); font-weight:500; font-size:20px; letter-spacing:.5px; }
  .postfach .adresse { font-family:var(--mono); font-size:11.5px; color:var(--muted); }
  .balken { display:flex; height:8px; border-radius:2px; overflow:hidden; background:var(--sandbg);
            border:1px solid var(--line); margin:4px 0 14px; }
  .balken span { display:block; height:100%; }
  .balken .b_gesendet { background:var(--blau); }
  .balken .b_entwurf { background:var(--ink); }
  .balken .b_fertig { background:var(--sand); }
  .zahlen { display:flex; flex-wrap:wrap; gap:26px; margin-bottom:16px; }
  .zahlen div { min-width:96px; }
  .zahlen .z { font-family:var(--titel); font-size:27px; font-weight:500; line-height:1; }
  .zahlen .t { font-family:var(--mono); font-size:10px; text-transform:uppercase;
               letter-spacing:1.3px; color:var(--muted); margin-top:6px; }
  .zahlen .punkt { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; }
  .meldung { font-family:var(--mono); font-size:11.5px; color:var(--muted); margin-top:12px;
             min-height:16px; }
  .warnung { background:var(--sandbg); border-left:2px solid var(--blau); padding:12px 16px;
             font-size:14.5px; margin-top:12px; }
  .warnung code { font-family:var(--mono); font-size:12px; }
  button:disabled { opacity:.4; cursor:not-allowed; }
</style>
</head>
<body>
<header>
  <span class="punkt"></span>
  <span class="logo">SPUL<b>WERK</b></span>
  <span class="mono" style="font-size:12px;letter-spacing:2px;color:var(--muted);text-transform:uppercase">Akquise</span>
  <span class="hinweis">Lokal · es wird nichts automatisch versendet</span>
</header>
<div class="wrap">
  <p class="eyebrow">Szene 01 — Postfach</p>
  <div class="postfach" id="postfach">
    <div class="kopfzeile">
      <h3>Gmail-Entwürfe</h3>
      <span class="adresse" id="gm_adresse"></span>
    </div>
    <div class="balken" id="gm_balken"></div>
    <div class="zahlen" id="gm_zahlen"></div>
    <div class="aktionen">
      <button class="primary" id="gm_push" onclick="gmailPush()">In Gmail-Entwürfe legen</button>
      <button id="gm_abgleich" onclick="gmailAbgleich()">Postfach abgleichen</button>
      <span class="mono" style="font-size:10.5px;letter-spacing:1px;color:var(--muted2);
            text-transform:uppercase">legt nur Entwürfe an — versendet nichts</span>
    </div>
    <div class="meldung" id="gm_meldung"></div>
    <div id="gm_hinweis"></div>
  </div>

  <p class="eyebrow">Szene 02 — Pipeline / Übersicht</p>
  <div class="karten" id="karten"></div>
  <div class="filter">
    <select id="f_status"><option value="">Alle Status</option></select>
    <select id="f_kat"><option value="">Alle Kategorien</option></select>
    <input id="f_score" type="number" placeholder="Min. Score" style="width:120px">
    <input id="f_ort" placeholder="Ort" style="width:120px">
    <button class="primary" onclick="laden()">Filtern</button>
    <span style="margin-left:auto;color:var(--dim)" id="anzahl"></span>
  </div>
  <table>
    <thead><tr><th>Prio</th><th>Name</th><th>Kategorie</th><th>Ort</th><th>Kontakt</th><th>Status</th><th>Score</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div class="overlay" id="overlay" onclick="if(event.target===this)zu()">
  <div class="modal" id="modal"></div>
</div>
<div class="toast" id="toast"></div>

<script>
const STATUS = ["neu","qualifiziert","kontaktiert","nachgefasst","antwort","termin","kunde","kein_interesse","gesperrt"];
let KATEGORIEN = {};

async function j(url, opts) {
  const r = await fetch(url, opts);
  return r.json();
}
function toast(t) {
  const el = document.getElementById('toast');
  el.textContent = t; el.style.display='block';
  setTimeout(()=>el.style.display='none', 2200);
}
function esc(s){ return (s||'').toString().replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function initFilter() {
  const meta = await j('/api/meta');
  KATEGORIEN = meta.kategorien;
  const fs = document.getElementById('f_status');
  STATUS.forEach(s=>fs.innerHTML += `<option value="${s}">${s}</option>`);
  const fk = document.getElementById('f_kat');
  Object.entries(meta.kategorien).forEach(([k,v])=>fk.innerHTML += `<option value="${k}">${v}</option>`);
}

async function ladenKarten() {
  const s = await j('/api/stats');
  const k = document.getElementById('karten');
  const felder = [
    ['Leads', s.gesamt], ['Ø Score', s.durchschnitt_score],
    ['Qualifiziert', s.verteilung.qualifiziert||0], ['Kontaktiert', s.verteilung.kontaktiert||0],
    ['Termine', s.verteilung.termin||0], ['Kunden', s.verteilung.kunde||0],
    ['Wiedervorlagen', s.wiedervorlagen]
  ];
  k.innerHTML = felder.map(([l,v])=>`<div class="karte"><div class="zahl">${v}</div><div class="label">${l}</div></div>`).join('');
}

async function laden() {
  const p = new URLSearchParams();
  const st = document.getElementById('f_status').value; if(st) p.set('status',st);
  const ka = document.getElementById('f_kat').value; if(ka) p.set('kategorie',ka);
  const sc = document.getElementById('f_score').value; if(sc) p.set('min_score',sc);
  const or = document.getElementById('f_ort').value; if(or) p.set('ort',or);
  const d = await j('/api/leads?'+p.toString());
  const tb = document.getElementById('tbody');
  document.getElementById('anzahl').textContent = d.leads.length + ' Leads';
  if(!d.leads.length){ tb.innerHTML = '<tr><td colspan="7" class="leer">Noch keine Leads. Erst per CLI suchen: <code>./spulwerk.py suche</code></td></tr>'; return; }
  tb.innerHTML = d.leads.map(l=>`<tr onclick="oeffne(${l.id})">
    <td><span class="prio ${l.prio}">${l.prio}</span></td>
    <td class="name">${esc(l.name)}</td>
    <td><span class="pill">${esc(KATEGORIEN[l.kategorie]||l.kategorie||'-')}</span></td>
    <td class="kmono">${esc(l.ort||'-')}</td>
    <td class="kmono">${esc(l.kontakt||'-')}</td>
    <td class="kmono">${esc(l.status)}</td>
    <td><span class="score">${l.score}</span></td></tr>`).join('');
}

async function oeffne(id) {
  const d = await j('/api/lead/'+id);
  const l = d.lead;
  const tabs = d.entwuerfe.map((e,i)=>`<div class="tab ${i===0?'aktiv':''}" onclick="tab(${i})">${e.kanal.toUpperCase()}</div>`).join('');
  const panels = d.entwuerfe.map((e,i)=>`<div class="entwurf ${i===0?'auf':''}" data-i="${i}" data-kanal="${e.kanal}">
      ${e.kanal==='email'?`<input class="betreff" id="betreff_${i}" value="${esc(e.betreff)}">`:''}
      <textarea id="text_${i}">${esc(e.text)}</textarea>
      <div class="aktionen">
        ${e.kanal==='dm' && l.instagram ? `<button class="primary" onclick="igDm(${i},${JSON.stringify(l.instagram)})">↗ Profil öffnen + DM kopieren</button>` : ''}
        <button ${e.kanal==='dm' && l.instagram ? '' : 'class="primary"'} onclick="speichern(${id},${i})">Entwurf speichern</button>
        <button onclick="kopieren(${i})">In Zwischenablage</button>
        <span class="pill">erzeugt von ${e.quelle}</span>
      </div></div>`).join('');
  const statusSel = STATUS.map(s=>`<option ${s===l.status?'selected':''}>${s}</option>`).join('');
  document.getElementById('modal').innerHTML = `
    <div class="kopf">
      <span class="schliessen" onclick="zu()">&times;</span>
      <p class="eyebrow">${esc(KATEGORIEN[l.kategorie]||l.kategorie||'Lead')}</p>
      <h2>${esc(l.name)}</h2>
      <div style="margin-top:10px;display:flex;align-items:center;gap:10px">
        <span class="prio ${l.prio}">${l.prio}</span>
        <span class="mono" style="font-size:12px;letter-spacing:1px;color:var(--muted)">SCORE ${l.score} / 100</span>
      </div>
    </div>
    <div class="koerper">
      <div class="zeile"><span class="k">Adresse</span><span>${esc([l.strasse,l.plz,l.ort].filter(Boolean).join(', ')||'-')}</span></div>
      <div class="zeile"><span class="k">Website</span><span>${l.website?`<a href="${esc(l.website)}" target="_blank" rel="noopener">${esc(l.website)}</a>`:'-'}</span></div>
      <div class="zeile"><span class="k">E-Mail</span><span>${esc(l.email||'-')}</span></div>
      <div class="zeile"><span class="k">Telefon</span><span>${esc(l.telefon||'-')}</span></div>
      <div class="zeile"><span class="k">Instagram</span><span>${l.instagram?`<a href="${esc(l.instagram)}" target="_blank" rel="noopener">${esc(l.instagram)}</a>`:'-'}</span></div>
      ${l.signale?`<div class="signale">${esc(l.signale).replace(/\\n/g,'<br>')}</div>`:''}
      <div class="tabs">${tabs||'<span style="color:var(--dim)">Noch keine Entwürfe – per CLI: ./spulwerk.py texten --lead '+id+'</span>'}</div>
      ${panels}
      <div class="aktionen" style="border-top:1px solid var(--line);padding-top:14px;margin-top:16px">
        <label>Status</label>
        <select id="statusSel">${statusSel}</select>
        <label>Notiz</label>
        <input id="notizFeld" placeholder="z. B. Termin Do 14:00" style="flex:1;min-width:160px">
        <label>Wiedervorlage (Tage)</label>
        <input id="wvFeld" type="number" style="width:80px">
        <button class="primary" onclick="setzeStatus(${id})">Übernehmen</button>
      </div>
    </div>`;
  document.getElementById('overlay').classList.add('auf');
}
function tab(i){
  document.querySelectorAll('.tab').forEach((t,x)=>t.classList.toggle('aktiv',x===i));
  document.querySelectorAll('.entwurf').forEach(p=>p.classList.toggle('auf', +p.dataset.i===i));
}
function zu(){ document.getElementById('overlay').classList.remove('auf'); }
function kopieren(i){ navigator.clipboard.writeText(document.getElementById('text_'+i).value); toast('Kopiert'); }
async function igDm(i, url){
  try { await navigator.clipboard.writeText(document.getElementById('text_'+i).value); } catch(e){}
  window.open(url, '_blank', 'noopener');
  toast('DM kopiert — im Profil nur noch einfügen');
}

async function speichern(id,i){
  const panel = document.querySelector(`.entwurf[data-i="${i}"]`);
  const betreffEl = document.getElementById('betreff_'+i);
  await j('/api/entwurf', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({lead_id:id, kanal:panel.dataset.kanal,
      betreff: betreffEl?betreffEl.value:null, text: document.getElementById('text_'+i).value})});
  toast('Entwurf gespeichert');
}
async function setzeStatus(id){
  await j('/api/status', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({lead_id:id, status:document.getElementById('statusSel').value,
      notiz:document.getElementById('notizFeld').value,
      wiedervorlage:document.getElementById('wvFeld').value||null})});
  toast('Status gesetzt'); zu(); ladenKarten(); laden();
}

let GM_TIMER = null;

async function ladenPostfach() {
  const g = await j('/api/gmail');
  document.getElementById('gm_adresse').textContent = g.postfach || 'kein Postfach konfiguriert';

  const gesamt = Math.max(g.kandidaten, 1);
  const teil = (n) => (100 * n / gesamt).toFixed(1) + '%';
  document.getElementById('gm_balken').innerHTML =
    `<span class="b_gesendet" style="width:${teil(g.gesendet)}"></span>
     <span class="b_entwurf" style="width:${teil(g.im_entwurf)}"></span>
     <span class="b_fertig" style="width:${teil(g.versandfertig)}"></span>`;

  const felder = [
    ['Nachrichten gesamt', g.kandidaten, ''],
    ['davon versendet', g.gesendet, 'var(--blau)'],
    ['im Entwurf', g.im_entwurf, 'var(--ink)'],
    ['versandfertig', g.versandfertig, 'var(--sand)'],
    ['noch ohne Text', g.ohne_text, ''],
  ];
  document.getElementById('gm_zahlen').innerHTML = felder.map(([t,v,farbe])=>
    `<div><div class="z">${v}</div><div class="t">${farbe?`<span class="punkt" style="background:${farbe}"></span>`:''}${t}</div></div>`
  ).join('');

  const knopf = document.getElementById('gm_push');
  knopf.disabled = !g.bereit || g.versandfertig === 0 || g.push.laeuft;
  knopf.textContent = g.push.laeuft
    ? 'läuft ...'
    : (g.versandfertig ? `${g.versandfertig} in Gmail-Entwürfe legen` : 'Nichts versandfertig');
  document.getElementById('gm_abgleich').disabled = !g.bereit;

  document.getElementById('gm_meldung').textContent = g.push.meldung || '';
  document.getElementById('gm_hinweis').innerHTML = g.bereit ? '' :
    `<div class="warnung"><b>App-Passwort fehlt.</b> Damit das Dashboard die Entwürfe selbst
      anlegen kann, einmalig ein Google-App-Passwort erstellen
      (<a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">myaccount.google.com/apppasswords</a>,
      setzt Zwei-Faktor voraus) und in diese Datei speichern:<br>
      <code>${esc(g.passwort_datei)}</code></div>`;

  if (g.push.laeuft && !GM_TIMER) GM_TIMER = setInterval(ladenPostfach, 1500);
  if (!g.push.laeuft && GM_TIMER) { clearInterval(GM_TIMER); GM_TIMER = null; ladenKarten(); laden(); }
}

async function gmailPush() {
  const knopf = document.getElementById('gm_push');
  knopf.disabled = true; knopf.textContent = 'startet ...';
  const r = await j('/api/gmail/push', {method:'POST', headers:{'Content-Type':'application/json'},
                                        body: JSON.stringify({})});
  if (!r.ok) toast(r.meldung || 'Läuft bereits');
  ladenPostfach();
}

async function gmailAbgleich() {
  const knopf = document.getElementById('gm_abgleich');
  knopf.disabled = true; knopf.textContent = 'lese Postfach ...';
  document.getElementById('gm_meldung').textContent = 'Frage Gmail ab ...';
  const r = await j('/api/gmail/abgleich', {method:'POST', headers:{'Content-Type':'application/json'},
                                            body: JSON.stringify({})});
  knopf.textContent = 'Postfach abgleichen';
  document.getElementById('gm_meldung').textContent = r.fehler
    ? 'Fehler: ' + r.fehler
    : `Postfach: ${r.im_postfach} Entwürfe, ${r.gesendet_gesamt} gesendete Adressen — ` +
      `${r.neu_als_entwurf} neu als Entwurf, ${r.neu_als_gesendet} neu als versendet erkannt.`;
  ladenPostfach(); ladenKarten(); laden();
}

initFilter().then(ladenKarten).then(laden).then(ladenPostfach);
</script>
</body>
</html>
"""


def _prio_kontakt(lead):
    kontakt = lead["email"] or lead["telefon"]
    if not kontakt and lead["instagram"]:
        kontakt = "IG"
    return score.prioritaet(lead["score"]), kontakt


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keine Zugriffs-Logs in der Konsole

    def _sende(self, koerper, typ="application/json", code=200):
        if isinstance(koerper, (dict, list)):
            koerper = json.dumps(koerper, ensure_ascii=False)
        daten = koerper.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "%s; charset=utf-8" % typ)
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def _koerper_json(self):
        laenge = int(self.headers.get("Content-Length", 0))
        if not laenge:
            return {}
        return json.loads(self.rfile.read(laenge).decode("utf-8"))

    def do_GET(self):
        pfad = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        conn = db.verbinde()
        try:
            if pfad == "/" or pfad == "/index.html":
                self._sende(SEITE, "text/html")
            elif pfad == "/api/meta":
                self._sende({
                    "kategorien": {k: v["label"] for k, v in config.KATEGORIEN.items()},
                })
            elif pfad == "/api/stats":
                stat = db.statistik(conn)
                stat["wiedervorlagen"] = len(db.faellige_wiedervorlagen(conn))
                self._sende(stat)
            elif pfad == "/api/leads":
                self._sende({"leads": self._leads(conn, query)})
            elif pfad.startswith("/api/lead/"):
                self._sende(self._lead(conn, int(pfad.rsplit("/", 1)[1])))
            elif pfad == "/api/gmail":
                cfg = config.lade_config()
                adresse, passwort = config.gmail_zugang(cfg)
                zahlen = gmail.kennzahlen(conn, min_score=cfg["akquise"]["min_score"])
                zahlen.update({
                    "postfach": adresse,
                    "bereit": bool(adresse and passwort),
                    "passwort_datei": str(
                        config.BASE_DIR / cfg["gmail"]["app_passwort_datei"]
                    ),
                    "push": dict(PUSH),
                })
                self._sende(zahlen)
            else:
                self._sende({"fehler": "unbekannt"}, code=404)
        except Exception as fehler:
            self._sende({"fehler": str(fehler)}, code=500)
        finally:
            conn.close()

    def do_POST(self):
        pfad = urlparse(self.path).path
        conn = db.verbinde()
        try:
            daten = self._koerper_json()
            if pfad == "/api/entwurf":
                with conn:
                    db.speichere_entwurf(
                        conn, int(daten["lead_id"]), daten["kanal"],
                        daten.get("betreff"), daten.get("text", ""), quelle="manuell",
                    )
                self._sende({"ok": True})
            elif pfad == "/api/status":
                self._status(conn, daten)
                self._sende({"ok": True})
            elif pfad == "/api/gmail/push":
                with PUSH_SPERRE:
                    if PUSH["laeuft"]:
                        self._sende({"ok": False, "meldung": "Läuft bereits."})
                        return
                    PUSH.update({"laeuft": True, "meldung": "Startet ...", "angelegt": 0,
                                 "uebersprungen": 0, "gesamt": 0, "fehler": None,
                                 "fertig_am": None})
                cfg = config.lade_config()
                threading.Thread(
                    target=_push_lauf,
                    args=(int(daten.get("min_score") or cfg["akquise"]["min_score"]),
                          daten.get("prio") or None,
                          int(daten["limit"]) if daten.get("limit") else None),
                    daemon=True,
                ).start()
                self._sende({"ok": True})
            elif pfad == "/api/gmail/abgleich":
                ergebnis = gmail.abgleich(conn)
                self._sende({"ok": True, **ergebnis})
            else:
                self._sende({"fehler": "unbekannt"}, code=404)
        except Exception as fehler:
            self._sende({"fehler": str(fehler)}, code=500)
        finally:
            conn.close()

    def _leads(self, conn, query):
        def erster(name):
            werte = query.get(name)
            return werte[0] if werte else None
        min_score = erster("min_score")
        zeilen = db.leads(
            conn,
            status=erster("status"),
            kategorie=erster("kategorie"),
            min_score=int(min_score) if min_score else None,
            ort=erster("ort"),
            limit=300,
        )
        ergebnis = []
        for lead in zeilen:
            prio, kontakt = _prio_kontakt(lead)
            ergebnis.append({
                "id": lead["id"], "name": lead["name"], "kategorie": lead["kategorie"],
                "ort": lead["ort"], "status": lead["status"], "score": lead["score"],
                "prio": prio, "kontakt": kontakt,
            })
        return ergebnis

    def _lead(self, conn, lead_id):
        lead = db.hole_lead(conn, lead_id)
        if lead is None:
            return {"fehler": "nicht gefunden"}
        prio, _ = _prio_kontakt(lead)
        entwuerfe = [
            {"kanal": e["kanal"], "betreff": e["betreff"] or "", "text": e["text"],
             "quelle": e["quelle"]}
            for e in db.entwuerfe(conn, lead_id)
        ]
        reihenfolge = {"email": 0, "dm": 1, "telefon": 2}
        entwuerfe.sort(key=lambda e: reihenfolge.get(e["kanal"], 9))
        return {
            "lead": {
                "id": lead["id"], "name": lead["name"], "kategorie": lead["kategorie"],
                "strasse": lead["strasse"], "plz": lead["plz"], "ort": lead["ort"],
                "website": lead["website"], "email": lead["email"],
                "telefon": lead["telefon"], "instagram": lead["instagram"],
                "status": lead["status"], "score": lead["score"], "prio": prio,
                "signale": lead["signale"],
            },
            "entwuerfe": entwuerfe,
        }

    def _status(self, conn, daten):
        lead = db.hole_lead(conn, int(daten["lead_id"]))
        if lead is None:
            return
        felder = {"status": daten["status"]}
        if daten.get("notiz"):
            bisher = (lead["notizen"] + "\n") if lead["notizen"] else ""
            felder["notizen"] = "%s[%s] %s" % (bisher, db.heute(), daten["notiz"])
        if daten.get("wiedervorlage"):
            felder["wiedervorlage"] = db.in_tagen(int(daten["wiedervorlage"]))
        with conn:
            if daten["status"] in ("kontaktiert", "nachgefasst"):
                felder["kontaktversuche"] = (lead["kontaktversuche"] or 0) + 1
                db.protokolliere_kontakt(
                    conn, lead["id"], kanal="dashboard",
                    ergebnis=daten.get("notiz") or daten["status"],
                )
            db.aktualisiere_lead(conn, lead["id"], **felder)


def starte(host="127.0.0.1", port=8733):
    db.initialisiere()
    server = ThreadingHTTPServer((host, port), Handler)
    print("Spulwerk Dashboard läuft: http://%s:%d" % (host, port))
    print("Beenden mit Strg+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard beendet.")
        server.shutdown()
