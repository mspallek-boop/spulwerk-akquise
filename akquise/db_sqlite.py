"""SQLite-Datenhaltung: Leads, Kontaktverlauf, Entwuerfe, Sperrliste.

Das lokale Backend. Die Cloud-Variante steht in db_pg.py; ausgewaehlt wird in
db.py. Beide bieten dieselben Funktionen."""

import json
import sqlite3
from datetime import datetime, timedelta

from . import config

STATUS_REIHENFOLGE = [
    "neu",
    "qualifiziert",
    "kontaktiert",
    "nachgefasst",
    "antwort",
    "termin",
    "kunde",
    "kein_interesse",
    "gesperrt",
]


def jetzt():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def heute():
    return datetime.now().strftime("%Y-%m-%d")


def in_tagen(tage):
    return (datetime.now() + timedelta(days=tage)).strftime("%Y-%m-%d")


def verbinde():
    config.stelle_verzeichnisse_bereit()
    # timeout: wartet, statt sofort "database is locked" zu melden - waehrend
    # eines laufenden texten-/sweep-Laufs schreibt ein zweiter Prozess sonst
    # ins Leere.
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migriere(conn)
    return conn


# Nachtraeglich ergaenzte Spalten. Aeltere Datenbanken bekommen sie beim
# ersten Verbinden automatisch - so bleibt ein Update ein reines Dateikopieren.
NACHRUESTUNG = {
    "leads": {"gmail_am": "TEXT", "gmail_gesendet_am": "TEXT",
              "instagram_am": "TEXT"},
}


def _migriere(conn):
    for tabelle, spalten in NACHRUESTUNG.items():
        vorhanden = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % tabelle)}
        if not vorhanden:
            continue  # Tabelle gibt es noch nicht - initialisiere() legt sie an.
        for spalte, typ in spalten.items():
            if spalte not in vorhanden:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabelle, spalte, typ))
                conn.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quelle TEXT NOT NULL DEFAULT 'osm',
    quelle_id TEXT UNIQUE,
    name TEXT NOT NULL,
    kategorie TEXT,
    branche TEXT,
    strasse TEXT,
    plz TEXT,
    ort TEXT,
    lat REAL,
    lon REAL,
    website TEXT,
    telefon TEXT,
    email TEXT,
    instagram TEXT,
    facebook TEXT,
    ansprechpartner TEXT,
    status TEXT NOT NULL DEFAULT 'neu',
    score INTEGER DEFAULT 0,
    signale TEXT,
    recherche TEXT,
    notizen TEXT,
    wiedervorlage TEXT,
    kontaktversuche INTEGER NOT NULL DEFAULT 0,
    erstellt_am TEXT NOT NULL,
    aktualisiert_am TEXT NOT NULL,
    angereichert_am TEXT,
    gmail_am TEXT,
    gmail_gesendet_am TEXT,
    instagram_am TEXT
);

CREATE TABLE IF NOT EXISTS kontakte (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    datum TEXT NOT NULL,
    kanal TEXT NOT NULL,
    richtung TEXT NOT NULL DEFAULT 'raus',
    betreff TEXT,
    inhalt TEXT,
    ergebnis TEXT
);

CREATE TABLE IF NOT EXISTS entwuerfe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    kanal TEXT NOT NULL,
    betreff TEXT,
    text TEXT NOT NULL,
    erstellt_am TEXT NOT NULL,
    quelle TEXT NOT NULL DEFAULT 'vorlage',
    verwendet INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sperrliste (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    muster TEXT NOT NULL UNIQUE,
    grund TEXT,
    erstellt_am TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score);
CREATE INDEX IF NOT EXISTS idx_kontakte_lead ON kontakte(lead_id);
CREATE INDEX IF NOT EXISTS idx_entwuerfe_lead ON entwuerfe(lead_id);
"""


def initialisiere():
    conn = verbinde()
    with conn:
        conn.executescript(SCHEMA)
    conn.close()


def speichere_lead(conn, daten):
    """Legt einen Lead an oder aktualisiert leere Felder eines bestehenden.

    Rueckgabe: (lead_id, war_neu)
    """
    quelle_id = daten.get("quelle_id")
    vorhanden = None
    if quelle_id:
        vorhanden = conn.execute(
            "SELECT * FROM leads WHERE quelle_id = ?", (quelle_id,)
        ).fetchone()
    if vorhanden is None and daten.get("website"):
        vorhanden = conn.execute(
            "SELECT * FROM leads WHERE website = ? AND name = ?",
            (daten["website"], daten.get("name")),
        ).fetchone()

    if vorhanden is not None:
        # Nur fehlende Felder auffuellen, manuelle Pflege nicht ueberschreiben.
        aenderungen = {}
        for feld in (
            "kategorie", "branche", "strasse", "plz", "ort", "lat", "lon",
            "website", "telefon", "email", "instagram", "facebook",
        ):
            neu = daten.get(feld)
            if neu and not vorhanden[feld]:
                aenderungen[feld] = neu
        if aenderungen:
            aenderungen["aktualisiert_am"] = jetzt()
            satz = ", ".join("%s = ?" % f for f in aenderungen)
            conn.execute(
                "UPDATE leads SET %s WHERE id = ?" % satz,
                list(aenderungen.values()) + [vorhanden["id"]],
            )
        return vorhanden["id"], False

    felder = {
        "quelle": daten.get("quelle", "osm"),
        "quelle_id": quelle_id,
        "name": daten.get("name") or "(ohne Namen)",
        "kategorie": daten.get("kategorie"),
        "branche": daten.get("branche"),
        "strasse": daten.get("strasse"),
        "plz": daten.get("plz"),
        "ort": daten.get("ort"),
        "lat": daten.get("lat"),
        "lon": daten.get("lon"),
        "website": daten.get("website"),
        "telefon": daten.get("telefon"),
        "email": daten.get("email"),
        "instagram": daten.get("instagram"),
        "facebook": daten.get("facebook"),
        "status": "neu",
        "erstellt_am": jetzt(),
        "aktualisiert_am": jetzt(),
    }
    spalten = ", ".join(felder)
    platzhalter = ", ".join("?" for _ in felder)
    cursor = conn.execute(
        "INSERT INTO leads (%s) VALUES (%s)" % (spalten, platzhalter),
        list(felder.values()),
    )
    return cursor.lastrowid, True


def aktualisiere_lead(conn, lead_id, **felder):
    if not felder:
        return
    felder["aktualisiert_am"] = jetzt()
    satz = ", ".join("%s = ?" % f for f in felder)
    conn.execute(
        "UPDATE leads SET %s WHERE id = ?" % satz,
        list(felder.values()) + [_id(lead_id)],
    )


def _id(wert):
    """Lead-IDs sind hier Zahlen. Kommt eine Zeichenkette herein (z. B. von der
    Kommandozeile oder aus der Cloud-Variante), wird sie umgewandelt."""
    try:
        return int(wert)
    except (TypeError, ValueError):
        return wert


def hole_lead(conn, lead_id):
    return conn.execute("SELECT * FROM leads WHERE id = ?", (_id(lead_id),)).fetchone()


def leads(conn, status=None, kategorie=None, min_score=None, ort=None,
          limit=None, nur_unangereichert=False, nur_ohne_entwurf=False,
          sortierung="score"):
    bedingungen = ["status != 'gesperrt'"]
    werte = []
    if status:
        bedingungen.append("status = ?")
        werte.append(status)
    if kategorie:
        bedingungen.append("kategorie = ?")
        werte.append(kategorie)
    if min_score is not None:
        bedingungen.append("score >= ?")
        werte.append(min_score)
    if ort:
        bedingungen.append("LOWER(ort) LIKE ?")
        werte.append("%%%s%%" % ort.lower())
    if nur_unangereichert:
        bedingungen.append("angereichert_am IS NULL")
    if nur_ohne_entwurf:
        bedingungen.append(
            "id NOT IN (SELECT lead_id FROM entwuerfe)"
        )
    sortier_sql = {
        "score": "score DESC, name ASC",
        "name": "name ASC",
        "neu": "erstellt_am DESC",
    }.get(sortierung, "score DESC, name ASC")
    sql = "SELECT * FROM leads WHERE %s ORDER BY %s" % (
        " AND ".join(bedingungen), sortier_sql,
    )
    if limit:
        sql += " LIMIT %d" % int(limit)
    return conn.execute(sql, werte).fetchall()


def faellige_wiedervorlagen(conn):
    return conn.execute(
        """SELECT * FROM leads
           WHERE wiedervorlage IS NOT NULL
             AND wiedervorlage <= ?
             AND status NOT IN ('kunde', 'kein_interesse', 'gesperrt')
           ORDER BY wiedervorlage ASC""",
        (heute(),),
    ).fetchall()


def protokolliere_kontakt(conn, lead_id, kanal, betreff=None, inhalt=None,
                          ergebnis=None, richtung="raus", datum=None):
    conn.execute(
        """INSERT INTO kontakte (lead_id, datum, kanal, richtung, betreff, inhalt, ergebnis)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, datum or jetzt(), kanal, richtung, betreff, inhalt, ergebnis),
    )


def kontakte(conn, lead_id):
    return conn.execute(
        "SELECT * FROM kontakte WHERE lead_id = ? ORDER BY datum ASC", (lead_id,)
    ).fetchall()


def speichere_entwurf(conn, lead_id, kanal, betreff, text, quelle="vorlage"):
    conn.execute("DELETE FROM entwuerfe WHERE lead_id = ? AND kanal = ? AND verwendet = 0",
                 (lead_id, kanal))
    cursor = conn.execute(
        """INSERT INTO entwuerfe (lead_id, kanal, betreff, text, erstellt_am, quelle)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lead_id, kanal, betreff, text, jetzt(), quelle),
    )
    # Lead mitstempeln: der Sync nach Supabase geht nach leads.aktualisiert_am.
    # Ohne diese Zeile bleiben neu getextete Entwuerfe im Portal unsichtbar.
    conn.execute("UPDATE leads SET aktualisiert_am = ? WHERE id = ?", (jetzt(), lead_id))
    return cursor.lastrowid


def entwuerfe(conn, lead_id=None, kanal=None):
    bedingungen = []
    werte = []
    if lead_id:
        bedingungen.append("lead_id = ?")
        werte.append(lead_id)
    if kanal:
        bedingungen.append("kanal = ?")
        werte.append(kanal)
    sql = "SELECT * FROM entwuerfe"
    if bedingungen:
        sql += " WHERE " + " AND ".join(bedingungen)
    sql += " ORDER BY erstellt_am DESC"
    return conn.execute(sql, werte).fetchall()


def sperre(conn, muster, grund=None):
    conn.execute(
        "INSERT OR IGNORE INTO sperrliste (muster, grund, erstellt_am) VALUES (?, ?, ?)",
        (muster.lower().strip(), grund, jetzt()),
    )
    conn.execute(
        """UPDATE leads SET status = 'gesperrt', aktualisiert_am = ?
           WHERE LOWER(COALESCE(website, '')) LIKE ?
              OR LOWER(COALESCE(email, '')) LIKE ?
              OR LOWER(name) LIKE ?""",
        (jetzt(), "%%%s%%" % muster.lower(), "%%%s%%" % muster.lower(),
         "%%%s%%" % muster.lower()),
    )


def ist_gesperrt(conn, lead):
    muster = [r["muster"] for r in conn.execute("SELECT muster FROM sperrliste")]
    haystack = " ".join(
        str(lead[f] or "") for f in ("name", "website", "email")
    ).lower()
    return any(m in haystack for m in muster)


def sperrliste(conn):
    return conn.execute("SELECT * FROM sperrliste ORDER BY erstellt_am DESC").fetchall()


def statistik(conn):
    zeilen = conn.execute(
        "SELECT status, COUNT(*) AS anzahl FROM leads GROUP BY status"
    ).fetchall()
    verteilung = {z["status"]: z["anzahl"] for z in zeilen}
    gesamt = sum(verteilung.values())
    schnitt = conn.execute(
        "SELECT AVG(score) AS s FROM leads WHERE score > 0"
    ).fetchone()["s"]
    return {
        "gesamt": gesamt,
        "verteilung": verteilung,
        "durchschnitt_score": round(schnitt or 0, 1),
        "kontakte": conn.execute("SELECT COUNT(*) AS c FROM kontakte").fetchone()["c"],
        "entwuerfe": conn.execute("SELECT COUNT(*) AS c FROM entwuerfe").fetchone()["c"],
    }


def lade_json(wert, standard=None):
    if not wert:
        return standard if standard is not None else {}
    try:
        return json.loads(wert)
    except (ValueError, TypeError):
        return standard if standard is not None else {}


def loesche_entwurf(conn, lead_id, kanal):
    """Entfernt den (noch unbenutzten) Entwurf eines Kanals.

    Das `with conn:` ist hier wesentlich: ohne Transaktion verwirft sqlite3 die
    Loeschung beim Schliessen der Verbindung wieder - mangelhafte Entwuerfe
    blieben dann stehen und wanderten doch ins Postfach.
    """
    with conn:
        conn.execute(
            "DELETE FROM entwuerfe WHERE lead_id = ? AND kanal = ? AND verwendet = 0",
            (_id(lead_id), kanal),
        )


def entwuerfe_frisch_seit(conn, kanaele, seit):
    """lead_ids, deren Entwuerfe fuer ALLE genannten Kanaele neuer als `seit` sind."""
    platz = ", ".join("?" for _ in kanaele)
    zeilen = conn.execute(
        """SELECT lead_id, COUNT(DISTINCT kanal) AS fertig
             FROM entwuerfe
            WHERE kanal IN (%s) AND erstellt_am >= ?
            GROUP BY lead_id""" % platz,
        list(kanaele) + [seit],
    ).fetchall()
    return {z["lead_id"] for z in zeilen if z["fertig"] >= len(kanaele)}


def gmail_kandidaten(conn, min_score, erledigt_status, nur_offene=True):
    """Leads mit E-Mail-Adresse und E-Mail-Entwurf, aufbereitet fuer den Versand."""
    bedingungen = [
        "l.score >= ?",
        "l.status NOT IN (%s)" % ", ".join("?" for _ in erledigt_status),
        "l.email IS NOT NULL AND TRIM(l.email) != ''",
    ]
    werte = [min_score] + list(erledigt_status)
    if nur_offene:
        bedingungen.append("l.gmail_am IS NULL")
    sql = """
        SELECT l.id, l.name, l.email, l.score, l.kategorie, l.ort, l.website,
               e.id AS entwurf_id, e.betreff, e.text, e.quelle, e.erstellt_am
          FROM leads l
          JOIN entwuerfe e ON e.lead_id = l.id AND e.kanal = 'email'
         WHERE %s
           AND e.id = (SELECT id FROM entwuerfe
                        WHERE lead_id = l.id AND kanal = 'email'
                        ORDER BY erstellt_am DESC, id DESC LIMIT 1)
         ORDER BY l.score DESC, l.name ASC
    """ % " AND ".join(bedingungen)
    return [dict(z) for z in conn.execute(sql, werte)]


def markiere_gmail(conn, lead_ids, feld="gmail_am", zeit=None):
    zeit = zeit or jetzt()
    with conn:
        for lead_id in lead_ids:
            conn.execute(
                "UPDATE leads SET %s = ?, aktualisiert_am = ? WHERE id = ?" % feld,
                (zeit, zeit, int(lead_id)),
            )


def gmail_zahlen(conn, min_score, erledigt_status):
    """Kennzahlen fuers Postfach-Panel."""
    def zaehle(zusatz=""):
        sql = """SELECT COUNT(*) c FROM leads l
                  WHERE l.score >= ? AND l.status NOT IN (%s)
                    AND l.email IS NOT NULL AND TRIM(l.email) != '' %s""" % (
            ", ".join("?" for _ in erledigt_status), zusatz)
        return conn.execute(sql, [min_score] + list(erledigt_status)).fetchone()["c"]

    return {
        "kandidaten": zaehle(),
        "gesendet": zaehle("AND l.gmail_gesendet_am IS NOT NULL"),
        "im_entwurf": zaehle("AND l.gmail_am IS NOT NULL AND l.gmail_gesendet_am IS NULL"),
        "ohne_text": zaehle("AND l.gmail_am IS NULL AND l.id NOT IN "
                            "(SELECT lead_id FROM entwuerfe WHERE kanal = 'email')"),
    }


def anzahl_leads(conn):
    return conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]


def aktualisiere_viele(conn, zeilen, blockgroesse=500):
    """Schreibt viele Leads in einer Transaktion zurueck."""
    zeilen = [dict(z) for z in zeilen if z.get("id")]
    if not zeilen:
        return 0
    jetzt_zeit = jetzt()
    with conn:
        for z in zeilen:
            felder = {k: v for k, v in z.items() if k != "id"}
            felder["aktualisiert_am"] = jetzt_zeit
            satz = ", ".join("%s = ?" % f for f in felder)
            conn.execute("UPDATE leads SET %s WHERE id = ?" % satz,
                         list(felder.values()) + [_id(z["id"])])
    return len(zeilen)
