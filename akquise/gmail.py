"""Gmail-Entwuerfe: Warteschlange fuer den Gmail-Connector in Claude.

Der naechtliche Sweep laeuft als reines Python-Skript und hat keinen Zugang
zu Gmail. Deshalb legt dieses Modul die faelligen E-Mail-Entwuerfe als JSON
ab (`export/gmail-warteschlange.json`). Ein geplanter Claude-Task liest die
Datei, legt fuer jeden Eintrag einen Gmail-Entwurf an und meldet die
erledigten Lead-IDs mit `spulwerk.py gmail fertig <IDs>` zurueck.

Es wird weiterhin NICHTS versendet - der Entwurf landet nur im Postfach,
das Abschicken bleibt eine bewusste Handlung eines Menschen (RECHTLICHES.md).
"""

import json
import random
import re
import socket
import struct

from . import config, db, llm, score

# Adressen von Baukasten-/Dienstleister-Domains: die stehen zwar im Impressum
# oder im Seitenquelltext, gehoeren aber nicht dem Betrieb (z. B. der
# Datenschutzkontakt von WordPress). Nie anschreiben.
FREMDE_DOMAINS = (
    "wordpress.org", "wordpress.com", "automattic.com", "wix.com",
    "squarespace.com", "jimdo.com", "shopify.com", "godaddy.com",
    "sentry.io", "example.com", "domain.at", "sensor.at",
    "webador.de", "webador.com", "ionos.de", "strato.de", "hostinger.com",
    "one.com", "weebly.com", "webnode.com", "site123.com",
)

STANDARD_DATEI = config.EXPORT_DIR / "gmail-warteschlange.json"

# Leads in diesen Status sind durch - fuer die braucht es keine Erstansprache mehr.
ERLEDIGT_STATUS = (
    "kontaktiert", "nachgefasst", "antwort", "termin", "kunde",
    "kein_interesse", "gesperrt",
)

EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# Textstellen, die nie in einer echten Mail stehen duerfen. Das Modell hat
# sowas vereinzelt geliefert: "Guten Tag Frau/Herr [Name]," oder eine frei
# erfundene Domain. Solche Entwuerfe werden verworfen und in der naechsten
# Nacht neu getextet, statt sie ins Postfach zu legen.
MAENGEL = (
    (re.compile(r"\[[^\]\n]{2,40}\]"), "Platzhalter in eckigen Klammern"),
    (re.compile(r"<[A-Za-zÄÖÜäöüß ]{2,25}>"), "Platzhalter in spitzen Klammern"),
    (re.compile(r"(Frau|Herr)\s*/\s*(sehr\s+geehrter?\s+)?(Frau|Herr)", re.IGNORECASE),
     "unklare Anrede 'Frau/Herr'"),
    (re.compile(r"ewegtbild", re.IGNORECASE), "verbotenes Wort 'Bewegtbild'"),
    (re.compile(r"spulwerk\.(?!com)[a-z]{2,4}", re.IGNORECASE), "falsche Domain"),
    (re.compile(r"^\s*$"), "leerer Text"),
)


DNS_ZEITLIMIT = 3.0


def _namensserver():
    """Erster Eintrag aus /etc/resolv.conf, sonst ein oeffentlicher Auflöser."""
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as datei:
            for zeile in datei:
                if zeile.startswith("nameserver"):
                    teile = zeile.split()
                    if len(teile) > 1 and ":" not in teile[1]:   # kein IPv6
                        return teile[1]
    except OSError:
        pass
    return "1.1.1.1"


def _dns_frage(domain, typ):
    """Minimale DNS-Anfrage über UDP. Rückgabe: Anzahl der Antworteinträge.

    Kein externes Paket - das Werkzeug kommt bewusst mit der Standardbibliothek
    aus, und `socket` reicht dafür.
    """
    kennung = random.randint(0, 0xFFFF)
    kopf = struct.pack(">HHHHHH", kennung, 0x0100, 1, 0, 0, 0)
    frage = b"".join(bytes([len(t)]) + t.encode("idna")
                     for t in domain.split(".") if t) + b"\x00"
    paket = kopf + frage + struct.pack(">HH", typ, 1)

    verbindung = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    verbindung.settimeout(DNS_ZEITLIMIT)
    try:
        verbindung.sendto(paket, (_namensserver(), 53))
        antwort, _ = verbindung.recvfrom(2048)
    finally:
        verbindung.close()
    if len(antwort) < 12 or antwort[:2] != paket[:2]:
        raise OSError("unpassende DNS-Antwort")
    rcode = antwort[3] & 0x0F
    if rcode == 3:                      # NXDOMAIN: Domain gibt es nicht
        return 0
    if rcode != 0:
        raise OSError("DNS-Fehler %d" % rcode)
    return struct.unpack(">H", antwort[6:8])[0]     # ANCOUNT


_MAILSERVER_CACHE = {}


def hat_mailserver(domain):
    """Nimmt die Domain überhaupt Post an? (MX, ersatzweise A - so wie ein
    Mailserver es auch versucht.)

    Bei Netzproblemen wird im Zweifel `True` geliefert: eine wacklige Leitung
    darf keine gültigen Adressen aussortieren.
    """
    domain = domain.lower().strip(".")
    if domain in _MAILSERVER_CACHE:
        return _MAILSERVER_CACHE[domain]
    ergebnis = True
    try:
        ergebnis = _dns_frage(domain, 15) > 0 or _dns_frage(domain, 1) > 0
    except Exception:
        ergebnis = True                 # im Zweifel durchlassen
    _MAILSERVER_CACHE[domain] = ergebnis
    return ergebnis


# Bewusst KEINE Liste erlaubter Endungen: ein erster Versuch damit warf
# "office@danzon.club" weg - fuer einen Wiener Club ist .club voellig richtig.
# Neue Endungen (.bar, .cafe, .studio, .wien) sind hier eher Regel als Ausnahme.
# Was Muell ausfiltert, sind die beiden Pruefungen darunter: Buchstabensalat
# und die Frage, ob die Domain ueberhaupt Post annimmt.

# Zeichenketten ohne einen einzigen Vokal sind keine Woerter. "t7aacbfjx" faellt
# darunter, "spulwerk" nicht.
_VOKALE = set("aeiouäöüy")


def _wirkt_zufaellig(teil):
    """Erkennt Buchstabensalat wie 'wybx1qeqcjqcm' - ohne echte Namen zu treffen."""
    nur_buchstaben = "".join(c for c in teil.lower() if c.isalpha())
    if len(nur_buchstaben) < 6:
        return False                      # zu kurz, um sicher zu urteilen
    anteil = sum(1 for c in nur_buchstaben if c in _VOKALE) / len(nur_buchstaben)
    return anteil < 0.2                   # unter 20 % Vokale: kein Wort


def _brauchbare_email(wert):
    if not wert:
        return False
    # Unsichtbare Zeichen entfernen: eine Adresse kam mit einem vorangestellten
    # Zero-Width-Space aus dem Seitenquelltext und passte danach zu nichts mehr.
    adresse = "".join(c for c in str(wert) if c.isprintable() and not c.isspace())
    if not EMAIL_MUSTER.match(adresse):
        return False
    lokal, _, domain = adresse.lower().rpartition("@")
    if any(domain == f or domain.endswith("." + f) for f in FREMDE_DOMAINS):
        return False
    if _wirkt_zufaellig(domain.rsplit(".", 1)[0]) or _wirkt_zufaellig(lokal):
        return False
    return hat_mailserver(domain)


def maengel(betreff, text):
    """Liste der Beanstandungen - leer heisst: Entwurf ist versandfertig."""
    zusammen = "%s\n%s" % (betreff or "", text or "")
    return [grund for muster, grund in MAENGEL if muster.search(zusammen)]


def warteschlange(conn, min_score=55, prio=None, limit=None, erneut=False,
                  verwerfen=True):
    """Fällige E-Mail-Entwürfe als Liste von Dicts.

    prio="A" beschränkt auf A-Leads, "B" auf B-Leads, None nimmt alles ab
    min_score. Ohne `erneut` bleiben Leads außen vor, für die schon einmal ein
    Gmail-Entwurf angelegt wurde - so entstehen keine Dubletten im Postfach.

    Rückgabe: (versandfertig, verworfen, unbrauchbar)
    """
    cfg = config.lade_config()
    ki_verfuegbar = llm.verfuegbar(cfg, config.api_key(cfg))
    zeilen = db.gmail_kandidaten(conn, min_score, ERLEDIGT_STATUS, nur_offene=not erneut)

    eintraege, verworfen, unbrauchbar = [], [], []
    for zeile in zeilen:
        wert = zeile.get("score") or 0
        if prio == "A" and wert < 70:
            continue
        if prio == "B" and not (55 <= wert < 70):
            continue
        if not _brauchbare_email(zeile.get("email")):
            unbrauchbar.append({"lead_id": zeile["id"], "name": zeile["name"],
                                "an": zeile.get("email")})
            continue
        if db.ist_gesperrt(conn, zeile):
            continue

        beanstandet = maengel(zeile.get("betreff"), zeile.get("text"))
        # Vorlagentexte sind bewusst nicht versandfertig: alle klingen gleich.
        # Sie werden verworfen und neu getextet - aber nur, wenn überhaupt ein
        # Sprachmodell eingerichtet ist, sonst gäbe es eine Endlosschleife.
        if zeile.get("quelle") != "claude" and ki_verfuegbar:
            beanstandet = beanstandet + ["Vorlagentext statt KI-Text"]
        if beanstandet:
            verworfen.append({"lead_id": zeile["id"], "name": zeile["name"],
                              "gruende": beanstandet})
            continue

        eintraege.append({
            "lead_id": zeile["id"],
            "name": zeile["name"],
            "prio": score.prioritaet(wert),
            "score": wert,
            "an": zeile["email"].strip(),
            "betreff": (zeile.get("betreff") or "Kurze Idee für %s" % zeile["name"]).strip(),
            "text": (zeile.get("text") or "").strip(),
            "quelle": zeile.get("quelle"),
            "erstellt_am": zeile.get("erstellt_am"),
        })
        if limit and len(eintraege) >= limit:
            break

    # Mangelhafte Entwürfe löschen: der Lead gilt damit wieder als "noch nicht
    # getextet" und wird im nächsten Lauf neu geschrieben. Versendet wurde davon nie etwas.
    if verworfen and verwerfen:
        for eintrag in verworfen:
            db.loesche_entwurf(conn, eintrag["lead_id"], "email")
    return eintraege, verworfen, unbrauchbar


def schreibe(eintraege, pfad=None):
    pfad = pfad or STANDARD_DATEI
    config.stelle_verzeichnisse_bereit()
    inhalt = {
        "erzeugt_am": db.jetzt(),
        "anzahl": len(eintraege),
        "hinweis": "Nur Entwuerfe anlegen, NICHT versenden.",
        "eintraege": eintraege,
    }
    with open(str(pfad), "w", encoding="utf-8") as datei:
        json.dump(inhalt, datei, ensure_ascii=False, indent=2)
    return pfad


def markiere(conn, lead_ids, feld="gmail_am"):
    """Hält fest, dass für diese Leads ein Gmail-Entwurf existiert."""
    lead_ids = list(lead_ids)
    db.markiere_gmail(conn, lead_ids, feld=feld)
    return len(lead_ids)


def abgleich(conn, cfg=None):
    """Holt den Ist-Zustand aus dem Postfach und schreibt ihn in die Datenbank.

    Was im Gmail-Entwurfsordner liegt, bekommt gmail_am; was im Ordner
    "Gesendet" auftaucht, bekommt gmail_gesendet_am und wandert - sofern der
    Lead noch unberührt war - auf Status "kontaktiert". So sieht man im
    Dashboard und im Portal, was tatsächlich rausgegangen ist.
    """
    from . import gmail_imap
    cfg = cfg or config.lade_config()
    daten = gmail_imap.bestand(cfg)
    entwurf_adressen = daten["entwuerfe"]
    gesendet_adressen = daten["gesendet"]

    neu_entwurf, neu_gesendet = [], []
    for zeile in db.gmail_abgleich_kandidaten(conn):
        adresse = (zeile["email"] or "").strip().lower()
        if not adresse:
            continue
        if adresse in gesendet_adressen and not zeile["gmail_gesendet_am"]:
            neu_gesendet.append(zeile)
        elif adresse in entwurf_adressen and not zeile["gmail_am"]:
            neu_entwurf.append(zeile)

    if neu_entwurf:
        db.markiere_gmail(conn, [z["id"] for z in neu_entwurf], "gmail_am")
    if neu_gesendet:
        db.markiere_gmail(conn, [z["id"] for z in neu_gesendet], "gmail_gesendet_am")
        db.markiere_gmail(conn, [z["id"] for z in neu_gesendet if not z["gmail_am"]],
                          "gmail_am")
        for zeile in neu_gesendet:
            if zeile["status"] in ("neu", "qualifiziert"):
                db.aktualisiere_lead(
                    conn, zeile["id"], status="kontaktiert",
                    kontaktversuche=(zeile["kontaktversuche"] or 0) + 1,
                )
                db.protokolliere_kontakt(
                    conn, zeile["id"], kanal="email",
                    ergebnis="per Gmail versendet (automatisch erkannt)",
                )

    return {
        "im_postfach": len(entwurf_adressen),
        "gesendet_gesamt": len(gesendet_adressen),
        "neu_als_entwurf": len(neu_entwurf),
        "neu_als_gesendet": len(neu_gesendet),
    }


def kennzahlen(conn, min_score=55):
    """Zahlen für das Postfach-Panel (lokal wie im Portal)."""
    zahlen = db.gmail_zahlen(conn, min_score, ERLEDIGT_STATUS)
    versandfertig, mangelhaft, unbrauchbar = warteschlange(
        conn, min_score=min_score, verwerfen=False
    )
    zahlen.update({
        "min_score": min_score,
        "versandfertig": len(versandfertig),
        "mangelhaft": len(mangelhaft),
        "unbrauchbar": len(unbrauchbar),
    })
    return zahlen


def stand(conn, min_score=55):
    """Wie kennzahlen(), zusätzlich mit der Liste der unbrauchbaren Adressen."""
    zahlen = db.gmail_zahlen(conn, min_score, ERLEDIGT_STATUS)
    versandfertig, mangelhaft, unbrauchbar = warteschlange(
        conn, min_score=min_score, verwerfen=False
    )
    zahlen.update({
        "in_gmail": zahlen["im_entwurf"] + zahlen["gesendet"],
        "offen": len(versandfertig),
        "mangelhaft": len(mangelhaft),
        "unbrauchbar": unbrauchbar,
    })
    return zahlen
