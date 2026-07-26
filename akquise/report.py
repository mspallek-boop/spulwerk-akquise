"""Ausgabe: Konsolenlisten, Lead-Dossiers, CSV- und .eml-Export."""

import csv
import re
from email.message import EmailMessage

from . import config, db, score


def _kuerze(wert, laenge):
    text = str(wert or "")
    return text if len(text) <= laenge else text[: laenge - 1] + "…"


def tabelle(zeilen):
    """Gibt Leads als kompakte Konsolentabelle aus."""
    if not zeilen:
        return "Keine Treffer."
    kopf = "%-5s %-4s %-32s %-13s %-24s %-11s %s" % (
        "ID", "Prio", "Name", "Kategorie", "Kontakt", "Status", "Score"
    )
    ausgabe = [kopf, "-" * len(kopf)]
    for lead in zeilen:
        kontakt = lead["email"] or lead["telefon"] or ("IG " + (lead["instagram"] or "")) or "-"
        ausgabe.append(
            "%-5s %-4s %-32s %-13s %-24s %-11s %s"
            % (
                lead["id"],
                score.prioritaet(lead["score"]),
                _kuerze(lead["name"], 32),
                _kuerze(lead["kategorie"], 13),
                _kuerze(kontakt, 24),
                _kuerze(lead["status"], 11),
                lead["score"],
            )
        )
    return "\n".join(ausgabe)


def dossier(conn, lead):
    """Alles, was vor einem Anruf oder einer Mail auf einen Blick nötig ist."""
    recherche = db.lade_json(lead["recherche"], {})
    teile = []
    teile.append("=" * 70)
    teile.append("%s  [Lead %s]" % (lead["name"], lead["id"]))
    teile.append("=" * 70)
    teile.append("Priorität:    %s (Score %d)" % (score.prioritaet(lead["score"]), lead["score"]))
    teile.append("Status:       %s" % lead["status"])
    teile.append("Kategorie:    %s" % (config.KATEGORIEN.get(lead["kategorie"] or "", {})
                                       .get("label", lead["kategorie"] or "-")))
    adresse = ", ".join(x for x in (lead["strasse"], lead["plz"], lead["ort"]) if x)
    teile.append("Adresse:      %s" % (adresse or "-"))
    teile.append("Website:      %s" % (lead["website"] or "-"))
    teile.append("E-Mail:       %s" % (lead["email"] or "-"))
    teile.append("Telefon:      %s" % (lead["telefon"] or "-"))
    teile.append("Instagram:    %s" % (lead["instagram"] or "-"))
    if lead["wiedervorlage"]:
        teile.append("Wiedervorlage: %s" % lead["wiedervorlage"])
    teile.append("Kontaktversuche: %d" % (lead["kontaktversuche"] or 0))

    if recherche.get("erreichbar"):
        teile.append("")
        teile.append("WEBSITE-BEFUND")
        teile.append("  Titel:            %s" % (recherche.get("titel") or "-"))
        teile.append("  Videos:           %s" % ("ja" if recherche.get("hat_video") else "nein"))
        teile.append("  Bilder Startseite: %s" % recherche.get("anzahl_bilder", 0))
        teile.append("  Mobil optimiert:  %s" % ("ja" if recherche.get("mobil_optimiert") else "nein"))
        teile.append("  Onlineshop:       %s" % ("ja" if recherche.get("shop") else "nein"))
        if recherche.get("emails"):
            teile.append("  Gefundene Mails:  %s" % ", ".join(recherche["emails"][:4]))

    if lead["signale"]:
        teile.append("")
        teile.append("BEWERTUNGSSIGNALE")
        for zeile in lead["signale"].split("\n"):
            teile.append("  - %s" % zeile)

    verlauf = db.kontakte(conn, lead["id"])
    if verlauf:
        teile.append("")
        teile.append("KONTAKTVERLAUF")
        for eintrag in verlauf:
            teile.append("  %s  %-8s %-5s %s"
                         % (eintrag["datum"][:16], eintrag["kanal"],
                            eintrag["richtung"], eintrag["ergebnis"] or ""))

    for entwurf in db.entwuerfe(conn, lead["id"]):
        teile.append("")
        teile.append("-" * 70)
        teile.append("ENTWURF · %s · erzeugt von %s · %s"
                     % (entwurf["kanal"].upper(), entwurf["quelle"], entwurf["erstellt_am"][:16]))
        if entwurf["betreff"]:
            teile.append("Betreff: %s" % entwurf["betreff"])
        teile.append("-" * 70)
        teile.append(entwurf["text"])

    if lead["notizen"]:
        teile.append("")
        teile.append("NOTIZEN")
        teile.append(lead["notizen"])
    return "\n".join(teile)


def _dateiname(text, lead_id):
    sauber = re.sub(r"[^a-zA-Z0-9]+", "-", text or "lead").strip("-").lower()
    return "%04d-%s" % (lead_id, sauber[:40] or "lead")


def exportiere_eml(conn, leads_liste, absender_email, absender_name):
    """Schreibt E-Mail-Entwürfe als .eml - per Doppelklick im Mailprogramm zu öffnen.

    Bewusst als Entwurf: das Absenden bleibt eine manuelle Handlung.
    """
    config.stelle_verzeichnisse_bereit()
    geschrieben = []
    for lead in leads_liste:
        entwurf = None
        for kandidat in db.entwuerfe(conn, lead["id"], "email"):
            entwurf = kandidat
            break
        if entwurf is None or not lead["email"]:
            continue
        nachricht = EmailMessage()
        nachricht["To"] = lead["email"]
        nachricht["From"] = "%s <%s>" % (absender_name, absender_email)
        nachricht["Subject"] = entwurf["betreff"] or ("Kurze Idee für %s" % lead["name"])
        nachricht["X-Unsent"] = "1"  # oeffnet als Entwurf statt als empfangene Mail
        nachricht.set_content(entwurf["text"])
        pfad = config.ENTWURF_DIR / (_dateiname(lead["name"], lead["id"]) + ".eml")
        with open(pfad, "wb") as datei:
            datei.write(bytes(nachricht))
        geschrieben.append(pfad)
    return geschrieben


def exportiere_csv(leads_liste, pfad=None):
    config.stelle_verzeichnisse_bereit()
    pfad = pfad or (config.EXPORT_DIR / "leads.csv")
    spalten = [
        "id", "prioritaet", "score", "status", "name", "kategorie", "strasse",
        "plz", "ort", "website", "email", "telefon", "instagram",
        "wiedervorlage", "kontaktversuche", "erstellt_am",
    ]
    with open(pfad, "w", encoding="utf-8-sig", newline="") as datei:
        schreiber = csv.writer(datei, delimiter=";")
        schreiber.writerow(spalten)
        for lead in leads_liste:
            schreiber.writerow([
                lead["id"], score.prioritaet(lead["score"]), lead["score"],
                lead["status"], lead["name"], lead["kategorie"], lead["strasse"],
                lead["plz"], lead["ort"], lead["website"], lead["email"],
                lead["telefon"], lead["instagram"], lead["wiedervorlage"],
                lead["kontaktversuche"], lead["erstellt_am"],
            ])
    return pfad


def exportiere_arbeitsliste(conn, leads_liste, pfad=None):
    """Markdown-Arbeitsliste für einen Akquise-Block - zum Ausdrucken/Abhaken."""
    config.stelle_verzeichnisse_bereit()
    pfad = pfad or (config.EXPORT_DIR / "arbeitsliste.md")
    zeilen = ["# Akquise-Arbeitsliste · %s" % db.heute(), ""]
    for lead in leads_liste:
        zeilen.append("## [ ] %s (Lead %s, Prio %s, Score %d)"
                      % (lead["name"], lead["id"], score.prioritaet(lead["score"]), lead["score"]))
        zeilen.append("")
        zeilen.append("- Kontakt: %s | %s" % (lead["email"] or "-", lead["telefon"] or "-"))
        zeilen.append("- Web: %s" % (lead["website"] or "-"))
        if lead["signale"]:
            erstes = lead["signale"].split("\n")[1] if "\n" in lead["signale"] else lead["signale"]
            zeilen.append("- Aufhänger: %s" % erstes)
        entwuerfe = db.entwuerfe(conn, lead["id"], "email")
        if entwuerfe:
            zeilen.append("")
            zeilen.append("```")
            if entwuerfe[0]["betreff"]:
                zeilen.append("Betreff: %s" % entwuerfe[0]["betreff"])
            zeilen.append(entwuerfe[0]["text"])
            zeilen.append("```")
        zeilen.append("")
    with open(pfad, "w", encoding="utf-8") as datei:
        datei.write("\n".join(zeilen))
    return pfad


def _ig_handle(url):
    """Zieht den @handle aus einer Instagram-Profil-URL."""
    if not url:
        return None
    rest = url.rstrip("/").split("instagram.com/")[-1]
    rest = rest.split("?")[0].strip("/")
    if not rest or rest in ("p", "reel", "explore"):
        return None
    return "@" + rest.lstrip("@")


def instagram_liste(conn, leads_liste):
    """Alle Leads mit Instagram-Profil, sortiert nach Score, mit DM-Entwurf.
    Basis für die Direktansprache per Instagram-DM."""
    treffer = [l for l in leads_liste if l["instagram"]]
    treffer.sort(key=lambda l: l["score"], reverse=True)
    return treffer


def exportiere_instagram(conn, leads_liste, pfad=None):
    """Schreibt eine Instagram-DM-Arbeitsliste als Markdown (zum Abhaken)."""
    config.stelle_verzeichnisse_bereit()
    pfad = pfad or (config.EXPORT_DIR / "instagram_dm.md")
    treffer = instagram_liste(conn, leads_liste)
    zeilen = [
        "# Instagram-Direktansprache · %s" % db.heute(),
        "",
        "%d Leads mit Instagram-Profil. Profil öffnen, DM prüfen, senden, abhaken." % len(treffer),
        "",
    ]
    for lead in treffer:
        handle = _ig_handle(lead["instagram"]) or lead["instagram"]
        zeilen.append("## [ ] %s — %s (Score %d, Prio %s)"
                      % (handle, lead["name"], lead["score"], score.prioritaet(lead["score"])))
        zeilen.append("")
        zeilen.append("- Profil: %s" % lead["instagram"])
        zeilen.append("- Branche: %s" % (config.KATEGORIEN.get(lead["kategorie"] or "", {})
                                         .get("label", lead["kategorie"] or "-")))
        dm = db.entwuerfe(conn, lead["id"], "dm")
        if dm:
            zeilen.append("")
            zeilen.append("```")
            zeilen.append(dm[0]["text"])
            zeilen.append("```")
        else:
            zeilen.append("- (noch kein DM-Entwurf – erst: ./spulwerk.py texten --lead %s)" % lead["id"])
        zeilen.append("")
    with open(pfad, "w", encoding="utf-8") as datei:
        datei.write("\n".join(zeilen))
    return pfad, len(treffer)


def uebersicht(conn):
    stat = db.statistik(conn)
    zeilen = ["", "PIPELINE", "-" * 40]
    for status in db.STATUS_REIHENFOLGE:
        anzahl = stat["verteilung"].get(status, 0)
        if anzahl:
            balken = "█" * min(30, anzahl)
            zeilen.append("%-15s %4d  %s" % (status, anzahl, balken))
    zeilen.append("-" * 40)
    zeilen.append("Leads gesamt:      %d" % stat["gesamt"])
    zeilen.append("Ø Score:           %s" % stat["durchschnitt_score"])
    zeilen.append("Kontakte erfasst:  %d" % stat["kontakte"])
    zeilen.append("Entwürfe:          %d" % stat["entwuerfe"])

    faellig = db.faellige_wiedervorlagen(conn)
    if faellig:
        zeilen.append("")
        zeilen.append("FÄLLIGE WIEDERVORLAGEN: %d" % len(faellig))
        for lead in faellig[:10]:
            zeilen.append("  [%s] %s (seit %s)" % (lead["id"], lead["name"], lead["wiedervorlage"]))
    return "\n".join(zeilen)
