"""Entwuerfe direkt ins Gmail-Postfach legen - ueber IMAP, ohne Zusatzpakete.

Warum IMAP und nicht die Gmail-API: die API braucht ein Google-Cloud-Projekt
mit OAuth-Zustimmung. IMAP kommt mit einem App-Passwort aus (Google-Konto ->
Sicherheit -> App-Passwoerter, setzt 2-Faktor voraus) und steckt in der
Standardbibliothek. Damit kann das Dashboard die Entwuerfe selbst anlegen,
ohne dass Claude laufen muss.

Es wird ausschliesslich in den Ordner ENTWUERFE geschrieben (Flag \\Draft).
Verschickt wird nichts - das bleibt ein bewusster Klick im Postfach.
"""

import email
import imaplib
import re
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from . import config

ZEIT_LIMIT = 40        # Sekunden je IMAP-Verbindung
MAX_NACHRICHTEN = 2000  # so viele Mails je Ordner werden ausgelesen (neueste zuerst)


class GmailFehler(Exception):
    pass


# ------------------------------------------------------------- Verbindung

def _verbinde(cfg):
    adresse, passwort = config.gmail_zugang(cfg)
    if not adresse:
        raise GmailFehler("Keine Gmail-Adresse in config.json (gmail.adresse).")
    datei = config.BASE_DIR / cfg["gmail"]["app_passwort_datei"]
    if not passwort:
        raise GmailFehler(
            "Kein App-Passwort hinterlegt. Anlegen unter "
            "https://myaccount.google.com/apppasswords und speichern in %s" % datei
        )
    # Google-App-Passwoerter haben 16 Zeichen. Alles deutlich Kuerzere ist ein
    # halb geschriebener Dateiinhalt - das gleich sagen, statt Gmail fragen zu
    # lassen und eine kryptische Anmeldefehlermeldung zu bekommen.
    if len(passwort) < 12:
        raise GmailFehler(
            "In %s stehen nur %d Zeichen. Ein Google-App-Passwort hat 16 "
            "(Leerzeichen dürfen drin sein, die werden entfernt). "
            "Datei bitte neu schreiben." % (datei, len(passwort))
        )
    server = cfg["gmail"].get("imap_server", "imap.gmail.com")
    try:
        verbindung = imaplib.IMAP4_SSL(server, 993, ssl_context=ssl.create_default_context(),
                                       timeout=ZEIT_LIMIT)
        verbindung.login(adresse, passwort)
    except imaplib.IMAP4.error as fehler:
        raise GmailFehler(
            "Anmeldung bei Gmail fehlgeschlagen (%s). Stimmt das App-Passwort? "
            "Das normale Kontopasswort funktioniert nicht." % fehler
        )
    except OSError as fehler:
        raise GmailFehler("Keine Verbindung zu %s: %s" % (server, fehler))
    return verbindung, adresse


def _ordner(verbindung, sonderrolle, ersatz):
    """Findet den Ordner mit der IMAP-Sonderrolle (\\Drafts bzw. \\Sent).
    Gmail benennt die Ordner je nach Kontosprache anders, deshalb nicht raten."""
    status, zeilen = verbindung.list()
    if status == "OK":
        for zeile in zeilen:
            text = zeile.decode("utf-8", errors="replace")
            if sonderrolle.lower() in text.lower():
                treffer = re.search(r'"([^"]*)"\s*$', text)
                if treffer:
                    return treffer.group(1)
    return ersatz


def entwurfsordner(verbindung):
    return _ordner(verbindung, r"\Drafts", "[Gmail]/Drafts")


def gesendetordner(verbindung):
    return _ordner(verbindung, r"\Sent", "[Gmail]/Sent Mail")


# ----------------------------------------------------------- Adressen lesen

def _empfaenger_im_ordner(verbindung, ordner):
    """Alle Empfaengeradressen eines Ordners als Kleinbuchstaben-Menge."""
    status, _ = verbindung.select('"%s"' % ordner, readonly=True)
    if status != "OK":
        return set()
    status, daten = verbindung.search(None, "ALL")
    if status != "OK" or not daten or not daten[0]:
        return set()
    nummern = daten[0].split()
    # Nur die neuesten Nachrichten ansehen. Ein gewachsener "Gesendet"-Ordner
    # hat sonst zehntausende Mails - fuer den Abgleich reicht die junge
    # Vergangenheit, denn Akquise-Mails sind neu.
    if len(nummern) > MAX_NACHRICHTEN:
        nummern = nummern[-MAX_NACHRICHTEN:]
    adressen = set()
    # In Bloecken holen: ein FETCH je Mail waere bei 200 Entwuerfen zaeh.
    for start in range(0, len(nummern), 200):
        block = b",".join(nummern[start:start + 200]).decode()
        status, teile = verbindung.fetch(block, "(BODY.PEEK[HEADER.FIELDS (TO)])")
        if status != "OK":
            continue
        for teil in teile:
            if not isinstance(teil, tuple) or len(teil) < 2:
                continue
            kopf = email.message_from_bytes(teil[1])
            for feld in kopf.get_all("To", []):
                for stueck in str(feld).split(","):
                    adresse = parseaddr(stueck)[1].strip().lower()
                    if adresse:
                        adressen.add(adresse)
    return adressen


def bestand(cfg):
    """Was liegt im Postfach? {'entwuerfe': set(...), 'gesendet': set(...)}"""
    verbindung, _ = _verbinde(cfg)
    try:
        return {
            "entwuerfe": _empfaenger_im_ordner(verbindung, entwurfsordner(verbindung)),
            "gesendet": _empfaenger_im_ordner(verbindung, gesendetordner(verbindung)),
        }
    finally:
        try:
            verbindung.logout()
        except Exception:
            pass


# -------------------------------------------------------- Entwuerfe anlegen

def _nachricht(absender, name, an, betreff, text):
    nachricht = EmailMessage()
    nachricht["From"] = "%s <%s>" % (name, absender) if name else absender
    nachricht["To"] = an
    nachricht["Subject"] = betreff
    nachricht["Date"] = formatdate(localtime=True)
    nachricht["Message-ID"] = make_msgid(domain=absender.split("@")[-1])
    nachricht.set_content(text)
    return nachricht


def lege_entwuerfe_an(cfg, eintraege, ueberspringe_vorhandene=True, melder=None):
    """Legt fuer jeden Eintrag einen Gmail-Entwurf an.

    eintraege: Dicts mit lead_id, name, an, betreff, text (aus gmail.warteschlange).
    Rueckgabe: {"angelegt": [lead_id...], "uebersprungen": [(lead_id, grund)...]}
    """
    melde = melder or (lambda *_: None)
    verbindung, absender = _verbinde(cfg)
    ergebnis = {"angelegt": [], "uebersprungen": []}
    try:
        ordner = entwurfsordner(verbindung)
        vorhanden = set()
        gesendet = set()
        if ueberspringe_vorhandene:
            vorhanden = _empfaenger_im_ordner(verbindung, ordner)
            gesendet = _empfaenger_im_ordner(verbindung, gesendetordner(verbindung))
            melde("Im Postfach: %d Entwürfe, %d gesendete Adressen"
                  % (len(vorhanden), len(gesendet)))

        absender_name = cfg["firma"].get("absender", "")
        for nummer, eintrag in enumerate(eintraege, 1):
            an = (eintrag["an"] or "").strip().lower()
            if ueberspringe_vorhandene and an in vorhanden:
                ergebnis["uebersprungen"].append((eintrag["lead_id"], "liegt schon im Entwurf"))
                continue
            if ueberspringe_vorhandene and an in gesendet:
                ergebnis["uebersprungen"].append((eintrag["lead_id"], "wurde schon versendet"))
                continue
            nachricht = _nachricht(absender, absender_name, eintrag["an"],
                                   eintrag["betreff"], eintrag["text"])
            status, antwort = verbindung.append(
                '"%s"' % ordner, "(\\Draft)", None, nachricht.as_bytes()
            )
            if status != "OK":
                ergebnis["uebersprungen"].append(
                    (eintrag["lead_id"], "Gmail lehnte ab: %s" % antwort)
                )
                continue
            vorhanden.add(an)
            ergebnis["angelegt"].append(eintrag["lead_id"])
            melde("%d/%d %s" % (nummer, len(eintraege), eintrag["name"][:40]))
    finally:
        try:
            verbindung.logout()
        except Exception:
            pass
    return ergebnis
