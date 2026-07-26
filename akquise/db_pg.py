"""Datenschicht auf Supabase/Postgres - API-gleich zu db.py (SQLite).

Warum es beides gibt: lokal auf dem Mac ist die SQLite-Datei bequem und
schnell. Für den Lauf in der Cloud (GitHub Actions) gibt es aber keine Platte,
die zwischen zwei Läufen bestehen bleibt - dort ist die Portal-Datenbank die
einzige Wahrheit. Beide Backends bieten dieselben Funktionen, umgeschaltet
wird über config.json (`datenbank.backend`) oder die Umgebungsvariable
AKQUISE_DB=supabase.

Unterschiede, die man kennen muss:
  * Die Lead-ID ist hier eine UUID (Zeichenkette), keine fortlaufende Zahl.
  * Entwürfe liegen als JSON am Lead (Spalte `entwuerfe`), nicht in einer
    eigenen Tabelle. Ein "Entwurf" hat deshalb die zusammengesetzte ID
    "<lead-id>:<kanal>".
  * PostgREST kennt keine Transaktionen über mehrere Aufrufe; `with conn:`
    ist hier ein No-Op. Jeder Schreibvorgang steht für sich.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config

STATUS_REIHENFOLGE = [
    "neu", "qualifiziert", "kontaktiert", "nachgefasst", "antwort",
    "termin", "kunde", "kein_interesse", "gesperrt",
]

LEADS = "akquise_leads"
KONTAKTE = "akquise_kontakte"
SPERRLISTE = "akquise_sperrliste"

# Spalten, die es in der Portal-Tabelle gibt. Alles andere wird beim Schreiben
# stillschweigend verworfen (z. B. das lokale Feld "quelle").
FELDER = (
    "id", "quelle", "quelle_id", "name", "kategorie", "branche", "strasse",
    "plz", "ort", "lat", "lon", "website", "telefon", "email", "instagram",
    "facebook", "ansprechpartner", "status", "score", "signale", "recherche",
    "entwuerfe", "notizen", "wiedervorlage", "kontaktversuche", "erstellt_am",
    "aktualisiert_am", "angereichert_am", "gmail_am", "gmail_gesendet_am",
    "instagram_am",
)


def jetzt():
    return datetime.now(timezone.utc).isoformat()


def heute():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def in_tagen(tage):
    return (datetime.now(timezone.utc) + timedelta(days=tage)).strftime("%Y-%m-%d")


def lade_json(wert, standard=None):
    """Wie db.lade_json, verträgt aber auch schon geparste Werte (jsonb)."""
    if wert is None or wert == "":
        return standard if standard is not None else {}
    if isinstance(wert, (dict, list)):
        return wert
    try:
        return json.loads(wert)
    except (ValueError, TypeError):
        return standard if standard is not None else {}


class DbFehler(Exception):
    pass


class Verbindung:
    """Dünne Hülle um die PostgREST-Schnittstelle, damit der übrige Code
    weiterhin `conn` herumreichen kann."""

    def __init__(self, cfg=None):
        self.cfg = cfg or config.lade_config()
        self.url = self.cfg["supabase"]["url"].rstrip("/")
        self.key = config.supabase_service_key(self.cfg)
        if not self.key:
            raise DbFehler("Kein Supabase-Service-Key gefunden.")

    # `with conn:` gibt es nur, damit der Code identisch bleibt.
    def __enter__(self):
        return self

    def __exit__(self, *fehler):
        return False

    def close(self):
        pass

    def alle_seiten(self, pfad, seite=1000, hoechstens=None):
        """Holt alles - PostgREST gibt pro Anfrage höchstens 1000 Zeilen zurück.
        Ohne dieses Blättern sähe die Cloud-Version nur die ersten 1000 Leads."""
        gesammelt = []
        versatz = 0
        while True:
            grenze = seite if hoechstens is None else min(seite, hoechstens - len(gesammelt))
            if grenze <= 0:
                break
            teil = self.anfrage("%s&limit=%d&offset=%d" % (pfad, grenze, versatz)) or []
            gesammelt.extend(teil)
            if len(teil) < grenze:
                break
            versatz += grenze
        return gesammelt

    def zaehle(self, pfad):
        """Zeilenzahl ohne die Daten zu holen (PostgREST liefert sie im
        Content-Range-Kopf, wenn man count=exact anfordert)."""
        anfrage = urllib.request.Request(
            "%s/rest/v1/%s" % (self.url, pfad), method="HEAD",
            headers={"apikey": self.key, "Authorization": "Bearer %s" % self.key,
                     "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
        )
        try:
            with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                bereich = antwort.headers.get("Content-Range", "")
        except Exception as fehler:
            raise DbFehler("Zählen fehlgeschlagen: %s" % fehler)
        return int(bereich.rsplit("/", 1)[-1]) if "/" in bereich else 0

    def anfrage(self, pfad, methode="GET", koerper=None, extra=None):
        daten = json.dumps(koerper).encode("utf-8") if koerper is not None else None
        kopf = {
            "apikey": self.key,
            "Authorization": "Bearer %s" % self.key,
            "Content-Type": "application/json",
        }
        kopf.update(extra or {})
        anfrage = urllib.request.Request(
            "%s/rest/v1/%s" % (self.url, pfad), data=daten, headers=kopf, method=methode
        )
        try:
            with urllib.request.urlopen(anfrage, timeout=60) as antwort:
                roh = antwort.read().decode("utf-8")
                return json.loads(roh) if roh.strip() else None
        except urllib.error.HTTPError as fehler:
            text = fehler.read().decode("utf-8", "replace")[:300]
            raise DbFehler("Supabase %s bei %s: %s" % (fehler.code, pfad, text))
        except Exception as fehler:
            raise DbFehler("Verbindung zu Supabase fehlgeschlagen: %s" % fehler)


def verbinde():
    return Verbindung()


def initialisiere():
    """Die Tabellen liegen in Supabase und werden dort per Migration angelegt -
    hier ist nichts zu tun."""
    Verbindung().anfrage("%s?select=id&limit=1" % LEADS)


# ------------------------------------------------------------------- Leads

def _saeubere_felder(daten):
    return {k: v for k, v in daten.items() if k in FELDER}


# Felder, die beim Wiederfinden eines Leads verglichen werden.
ABGLEICH_FELDER = ("kategorie", "branche", "strasse", "plz", "ort", "lat", "lon",
                   "website", "telefon", "email", "instagram", "facebook")


def _bekannte(conn):
    """quelle_id -> vorhandener Lead (nur die Felder, die verglichen werden).

    Die Suche prüft für jedes gefundene Objekt, ob es den Lead schon gibt. Über
    die Netz-Schnittstelle wäre das eine Anfrage pro Objekt - bei 2000 Objekten
    je Branche dauert das acht Minuten. Einmal alles holen kostet Sekunden.
    """
    if getattr(conn, "_bekannt", None) is None:
        felder = "id,quelle_id," + ",".join(ABGLEICH_FELDER)
        zeilen = conn.alle_seiten(
            "%s?select=%s&quelle_id=not.is.null" % (LEADS, felder))
        conn._bekannt = {str(z["quelle_id"]): z for z in zeilen}
    return conn._bekannt


def speichere_lead(conn, daten):
    """Legt einen Lead an oder füllt leere Felder eines bestehenden. (id, war_neu)"""
    quelle_id = daten.get("quelle_id")
    vorhanden = None
    if quelle_id:
        vorhanden = _bekannte(conn).get(str(quelle_id))

    if vorhanden is not None:
        aenderungen = {}
        for feld in ("kategorie", "branche", "strasse", "plz", "ort", "lat", "lon",
                     "website", "telefon", "email", "instagram", "facebook"):
            neu = daten.get(feld)
            if neu and not vorhanden.get(feld):
                aenderungen[feld] = neu
        if aenderungen:
            aenderungen["aktualisiert_am"] = jetzt()
            conn.anfrage("%s?id=eq.%s" % (LEADS, vorhanden["id"]), "PATCH",
                         aenderungen, {"Prefer": "return=minimal"})
            vorhanden.update(aenderungen)
        return vorhanden["id"], False

    satz = _saeubere_felder(dict(daten))
    satz.setdefault("status", "neu")
    satz.setdefault("quelle", "osm")
    satz["erstellt_am"] = jetzt()
    satz["aktualisiert_am"] = jetzt()
    zeilen = conn.anfrage(LEADS, "POST", [satz], {"Prefer": "return=representation"})
    neue_id = zeilen[0]["id"] if zeilen else None
    if quelle_id and getattr(conn, "_bekannt", None) is not None:
        merker = {"id": neue_id, "quelle_id": quelle_id}
        merker.update({f: satz.get(f) for f in ABGLEICH_FELDER})
        conn._bekannt[str(quelle_id)] = merker
    return neue_id, True


def aktualisiere_lead(conn, lead_id, **felder):
    if not felder:
        return
    felder = _saeubere_felder(felder)
    felder["aktualisiert_am"] = jetzt()
    conn.anfrage("%s?id=eq.%s" % (LEADS, lead_id), "PATCH", felder,
                 {"Prefer": "return=minimal"})


def hole_lead(conn, lead_id):
    zeilen = conn.anfrage("%s?id=eq.%s&select=*" % (LEADS, lead_id))
    return zeilen[0] if zeilen else None


def leads(conn, status=None, kategorie=None, min_score=None, ort=None,
          limit=None, nur_unangereichert=False, nur_ohne_entwurf=False,
          sortierung="score"):
    teile = ["select=*", "status=neq.gesperrt"]
    if status:
        teile.append("status=eq.%s" % status)
    if kategorie:
        teile.append("kategorie=eq.%s" % kategorie)
    if min_score is not None:
        teile.append("score=gte.%d" % min_score)
    if ort:
        teile.append("ort=ilike.*%s*" % urllib.parse.quote(ort))
    if nur_unangereichert:
        teile.append("angereichert_am=is.null")
    if nur_ohne_entwurf:
        teile.append("entwuerfe=is.null")
    teile.append({
        "score": "order=score.desc,name.asc",
        "name": "order=name.asc",
        "neu": "order=erstellt_am.desc",
    }.get(sortierung, "order=score.desc,name.asc"))
    return conn.alle_seiten("%s?%s" % (LEADS, "&".join(teile)), hoechstens=limit)


def faellige_wiedervorlagen(conn):
    pfad = ("%s?select=*&wiedervorlage=lte.%s&status=not.in.(kunde,kein_interesse,gesperrt)"
            "&order=wiedervorlage.asc" % (LEADS, heute()))
    return conn.anfrage(pfad) or []


# --------------------------------------------------------------- Entwuerfe

def speichere_entwurf(conn, lead_id, kanal, betreff, text, quelle="vorlage"):
    lead = hole_lead(conn, lead_id)
    if lead is None:
        raise DbFehler("Lead %s nicht gefunden" % lead_id)
    bestand = lade_json(lead.get("entwuerfe"), {}) or {}
    if bestand.get(kanal, {}).get("verwendet"):
        return "%s:%s" % (lead_id, kanal)      # benutzte Entwürfe bleiben stehen
    bestand[kanal] = {
        "betreff": betreff, "text": text, "quelle": quelle,
        "erstellt_am": jetzt(), "verwendet": 0,
    }
    conn.anfrage("%s?id=eq.%s" % (LEADS, lead_id), "PATCH",
                 {"entwuerfe": bestand, "aktualisiert_am": jetzt()},
                 {"Prefer": "return=minimal"})
    return "%s:%s" % (lead_id, kanal)


def entwuerfe(conn, lead_id=None, kanal=None):
    if lead_id:
        lead = hole_lead(conn, lead_id)
        quellen = [(lead_id, lade_json(lead.get("entwuerfe"), {}))] if lead else []
    else:
        zeilen = conn.alle_seiten("%s?select=id,entwuerfe&entwuerfe=not.is.null" % LEADS)
        quellen = [(z["id"], lade_json(z.get("entwuerfe"), {})) for z in zeilen]

    ergebnis = []
    for lid, bestand in quellen:
        for name, eintrag in (bestand or {}).items():
            if kanal and name != kanal:
                continue
            ergebnis.append({
                "id": "%s:%s" % (lid, name),
                "lead_id": lid,
                "kanal": name,
                "betreff": eintrag.get("betreff"),
                "text": eintrag.get("text", ""),
                "quelle": eintrag.get("quelle", "vorlage"),
                "erstellt_am": eintrag.get("erstellt_am"),
                "verwendet": eintrag.get("verwendet", 0),
            })
    ergebnis.sort(key=lambda e: e.get("erstellt_am") or "", reverse=True)
    return ergebnis


def loesche_entwurf(conn, lead_id, kanal):
    lead = hole_lead(conn, lead_id)
    if lead is None:
        return
    bestand = lade_json(lead.get("entwuerfe"), {}) or {}
    if bestand.pop(kanal, None) is None:
        return
    conn.anfrage("%s?id=eq.%s" % (LEADS, lead_id), "PATCH",
                 {"entwuerfe": bestand or None, "aktualisiert_am": jetzt()},
                 {"Prefer": "return=minimal"})


def entwuerfe_frisch_seit(conn, kanaele, seit):
    """lead_ids, deren Entwürfe für ALLE genannten Kanäle neuer als `seit` sind."""
    zeilen = conn.alle_seiten("%s?select=id,entwuerfe&entwuerfe=not.is.null" % LEADS)
    fertig = set()
    for zeile in zeilen:
        bestand = lade_json(zeile.get("entwuerfe"), {}) or {}
        if all((bestand.get(k) or {}).get("erstellt_am", "") >= seit for k in kanaele):
            fertig.add(zeile["id"])
    return fertig


# ---------------------------------------------------------------- Kontakte

def protokolliere_kontakt(conn, lead_id, kanal, betreff=None, inhalt=None,
                          ergebnis=None, richtung="raus", datum=None):
    conn.anfrage(KONTAKTE, "POST", [{
        "lead_id": lead_id, "datum": datum or jetzt(), "kanal": kanal,
        "richtung": richtung, "betreff": betreff, "inhalt": inhalt,
        "ergebnis": ergebnis,
    }], {"Prefer": "return=minimal"})


def kontakte(conn, lead_id):
    return conn.anfrage("%s?lead_id=eq.%s&order=datum.asc" % (KONTAKTE, lead_id)) or []


# -------------------------------------------------------------- Sperrliste

def sperre(conn, muster, grund=None):
    muster = muster.lower().strip()
    conn.anfrage(SPERRLISTE, "POST", [{"muster": muster, "grund": grund}],
                 {"Prefer": "resolution=ignore-duplicates,return=minimal"})
    for feld in ("website", "email", "name"):
        conn.anfrage("%s?%s=ilike.*%s*" % (LEADS, feld, urllib.parse.quote(muster)),
                     "PATCH", {"status": "gesperrt", "aktualisiert_am": jetzt()},
                     {"Prefer": "return=minimal"})


def sperrliste(conn):
    return conn.anfrage("%s?order=erstellt_am.desc" % SPERRLISTE) or []


def ist_gesperrt(conn, lead):
    if not hasattr(conn, "_sperrmuster"):
        conn._sperrmuster = [z["muster"] for z in sperrliste(conn)]
    haystack = " ".join(
        str(lead.get(f) or "") for f in ("name", "website", "email")
    ).lower()
    return any(m in haystack for m in conn._sperrmuster)


# --------------------------------------------------------------- Kennzahlen

def statistik(conn):
    alle = conn.alle_seiten("%s?select=status,score,entwuerfe" % LEADS)
    verteilung = {}
    for zeile in alle:
        verteilung[zeile["status"]] = verteilung.get(zeile["status"], 0) + 1
    mit_score = [z["score"] for z in alle if (z.get("score") or 0) > 0]
    anzahl_entwuerfe = sum(len(lade_json(z.get("entwuerfe"), {}) or {}) for z in alle)
    return {
        "gesamt": len(alle),
        "verteilung": verteilung,
        "durchschnitt_score": round(sum(mit_score) / len(mit_score), 1) if mit_score else 0,
        "kontakte": conn.zaehle("%s?select=id" % KONTAKTE),
        "entwuerfe": anzahl_entwuerfe,
    }


# ------------------------------------------------------- Gmail-Unterstützung

def gmail_kandidaten(conn, min_score, erledigt_status, nur_offene=True):
    """Leads mit E-Mail-Adresse und E-Mail-Entwurf, aufbereitet für den Versand."""
    teile = [
        "select=id,name,email,score,kategorie,ort,website,entwuerfe",
        "score=gte.%d" % min_score,
        "status=not.in.(%s)" % ",".join(erledigt_status),
        "email=not.is.null",
        "entwuerfe=not.is.null",
        "order=score.desc,name.asc",
    ]
    if nur_offene:
        teile.append("gmail_am=is.null")
    zeilen = conn.alle_seiten("%s?%s" % (LEADS, "&".join(teile)))

    ergebnis = []
    for zeile in zeilen:
        entwurf = (lade_json(zeile.get("entwuerfe"), {}) or {}).get("email") or {}
        if not entwurf.get("text"):
            continue
        ergebnis.append({
            "id": zeile["id"], "name": zeile["name"], "email": zeile["email"],
            "score": zeile["score"], "kategorie": zeile.get("kategorie"),
            "ort": zeile.get("ort"), "website": zeile.get("website"),
            "entwurf_id": "%s:email" % zeile["id"],
            "betreff": entwurf.get("betreff"), "text": entwurf.get("text"),
            "quelle": entwurf.get("quelle", "vorlage"),
            "erstellt_am": entwurf.get("erstellt_am"),
        })
    return ergebnis


def markiere_gmail(conn, lead_ids, feld="gmail_am", zeit=None):
    lead_ids = [str(i) for i in lead_ids]
    if not lead_ids:
        return
    zeit = zeit or jetzt()
    liste = ",".join('"%s"' % i for i in lead_ids)
    conn.anfrage("%s?id=in.(%s)" % (LEADS, liste), "PATCH",
                 {feld: zeit, "aktualisiert_am": zeit}, {"Prefer": "return=minimal"})


def gmail_zahlen(conn, min_score, erledigt_status):
    """Kennzahlen fürs Postfach-Panel - eine Abfrage, dann zählen."""
    pfad = ("%s?select=id,email,entwuerfe,gmail_am,gmail_gesendet_am"
            "&score=gte.%d&status=not.in.(%s)&email=not.is.null"
            % (LEADS, min_score, ",".join(erledigt_status)))
    zeilen = conn.alle_seiten(pfad)
    gesendet = sum(1 for z in zeilen if z.get("gmail_gesendet_am"))
    im_entwurf = sum(1 for z in zeilen if z.get("gmail_am") and not z.get("gmail_gesendet_am"))
    ohne_text = sum(1 for z in zeilen
                    if not z.get("gmail_am")
                    and not (lade_json(z.get("entwuerfe"), {}) or {}).get("email"))
    return {"kandidaten": len(zeilen), "gesendet": gesendet,
            "im_entwurf": im_entwurf, "ohne_text": ohne_text}


def anzahl_leads(conn):
    return conn.zaehle("%s?select=id" % LEADS)
