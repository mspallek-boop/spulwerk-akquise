"""Sync: lokale Leads -> Supabase (Portal-Datenbank akquise_leads).

Schiebt die relevanten Leads (Score >= Schwelle) samt Entwuerfen per
Upsert in die Portal-Datenbank. Nutzt den Service-Role-Key (nur lokal,
liegt im Portal-Ordner .secrets). Läuft am Ende des nächtlichen Sweeps.
"""

import json
import urllib.error
import urllib.request

from . import config, db

LETZTER_SYNC = config.DATEN_DIR / "last_sync.txt"
BATCH = 400


class SyncFehler(Exception):
    pass


def _lese_letzten_sync():
    if LETZTER_SYNC.exists():
        return LETZTER_SYNC.read_text(encoding="utf-8").strip() or None
    return None


def _schreibe_letzten_sync(wert):
    config.stelle_verzeichnisse_bereit()
    LETZTER_SYNC.write_text(wert, encoding="utf-8")


def _entwuerfe_map(conn, lead_ids):
    """lead_id -> {'email': {betreff,text,quelle}, 'dm': {...}, 'telefon': {...}}

    `quelle` wandert mit, damit das Portal Vorlagentexte von KI-Texten
    unterscheiden kann (Vorlagen sollen nicht ins Postfach).
    """
    if not lead_ids:
        return {}
    platz = ",".join("?" for _ in lead_ids)
    zeilen = conn.execute(
        "SELECT lead_id, kanal, betreff, text, quelle FROM entwuerfe "
        "WHERE lead_id IN (%s)" % platz,
        list(lead_ids),
    ).fetchall()
    ergebnis = {}
    for z in zeilen:
        ergebnis.setdefault(z["lead_id"], {})[z["kanal"]] = {
            "betreff": z["betreff"], "text": z["text"], "quelle": z["quelle"],
        }
    return ergebnis


def _row(lead, entwuerfe):
    return {
        "quelle_id": lead["quelle_id"],
        "quelle": lead["quelle"],
        "name": lead["name"],
        "kategorie": lead["kategorie"],
        "branche": lead["branche"],
        "strasse": lead["strasse"],
        "plz": lead["plz"],
        "ort": lead["ort"],
        "lat": lead["lat"],
        "lon": lead["lon"],
        "website": lead["website"],
        "telefon": lead["telefon"],
        "email": lead["email"],
        "instagram": lead["instagram"],
        "facebook": lead["facebook"],
        "ansprechpartner": lead["ansprechpartner"],
        "status": lead["status"],
        "score": lead["score"],
        "signale": lead["signale"],
        "recherche": db.lade_json(lead["recherche"], None),
        "entwuerfe": entwuerfe or None,
        "notizen": lead["notizen"],
        "gmail_am": lead["gmail_am"] or None,
        "gmail_gesendet_am": lead["gmail_gesendet_am"] or None,
        "wiedervorlage": lead["wiedervorlage"] or None,
        "kontaktversuche": lead["kontaktversuche"],
        "erstellt_am": lead["erstellt_am"],
        "aktualisiert_am": lead["aktualisiert_am"],
        "angereichert_am": lead["angereichert_am"],
        "instagram_am": lead["instagram_am"],
    }


def _upsert(url, key, rows):
    ziel = "%s/rest/v1/akquise_leads?on_conflict=quelle_id" % url.rstrip("/")
    anfrage = urllib.request.Request(
        ziel,
        data=json.dumps(rows).encode("utf-8"),
        headers={
            "apikey": key,
            "Authorization": "Bearer %s" % key,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=90) as antwort:
            return antwort.status
    except urllib.error.HTTPError as fehler:
        detail = fehler.read().decode("utf-8", errors="replace")[:400]
        raise SyncFehler("Supabase %s: %s" % (fehler.code, detail))
    except Exception as fehler:
        raise SyncFehler("Verbindung zu Supabase fehlgeschlagen: %s" % fehler)


def synchronisiere(min_score=None, voll=False, ausgabe=print):
    """Upsert aller relevanten Leads nach Supabase.
    voll=True ignoriert den letzten Sync-Zeitpunkt (alles neu schreiben)."""
    if getattr(db, "BACKEND", "sqlite") == "supabase":
        # Läuft das Werkzeug ohnehin auf der Portal-Datenbank, gibt es nichts
        # zu übertragen - die Daten stehen schon dort.
        ausgabe("  Übersprungen: Datenbank ist bereits das Portal.")
        return {"gesendet": 0}
    cfg = config.lade_config()
    url = cfg["supabase"]["url"]
    key = config.supabase_service_key(cfg)
    if not key:
        raise SyncFehler(
            "Kein Supabase-Service-Key gefunden (%s)" % config.SUPABASE_SERVICE_KEY_DATEI
        )
    if min_score is None:
        min_score = cfg["supabase"].get("min_score_sync", 40)

    conn = db.verbinde()
    kandidaten = [l for l in db.leads(conn, min_score=min_score) if l["quelle_id"]]

    seit = None if voll else _lese_letzten_sync()
    if seit:
        kandidaten = [l for l in kandidaten if (l["aktualisiert_am"] or "") > seit]

    if not kandidaten:
        conn.close()
        ausgabe("  Nichts zu synchronisieren (keine Änderungen seit %s)." % (seit or "Beginn"))
        return {"gesendet": 0}

    ent_map = _entwuerfe_map(conn, [l["id"] for l in kandidaten])
    rows = [_row(l, ent_map.get(l["id"])) for l in kandidaten]
    conn.close()

    gesendet = 0
    for i in range(0, len(rows), BATCH):
        teil = rows[i:i + BATCH]
        _upsert(url, key, teil)
        gesendet += len(teil)
        ausgabe("  %d / %d übertragen ..." % (gesendet, len(rows)))

    _schreibe_letzten_sync(db.jetzt())
    return {"gesendet": gesendet}
