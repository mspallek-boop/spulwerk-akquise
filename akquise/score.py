"""Bewertung: Wie gut passt ein Betrieb als Spulwerk-Kunde?

Die Logik bildet ab, was in der Praxis zaehlt: Der beste Lead ist ein
Betrieb, der sichtbar in Marketing investiert (Website, Social), dessen
Bildsprache aber schwach ist - keine Videos, wenige oder alte Fotos.
Wer schon eine Produktionsfirma beschaeftigt, ist kein Kaltakquise-Ziel.
"""

from . import config, db

# Aufschlaege pro Signal. Summe wird auf 0-100 begrenzt.
GEWICHTE = {
    "website_vorhanden": 10,
    "erreichbar": 4,
    "instagram_vorhanden": 14,
    "kein_video": 16,
    "wenig_bilder": 9,
    "nicht_mobil": 6,
    "shop": 6,
    "direkt_erreichbar": 8,
    "persoenliche_email": 5,
    "tiktok": 4,
}

ABZUEGE = {
    "wettbewerber": -60,
    "kein_kontaktweg": -25,
    "profi_video_vorhanden": -12,
    "kette": -8,
}


def _text(lead, *felder):
    return " ".join(str(lead[f] or "") for f in felder).lower()


def bewerte_lead(lead, recherche=None):
    """Gibt (score, signale) zurueck. signale ist eine Liste lesbarer Gruende."""
    recherche = recherche or db.lade_json(lead["recherche"], {})
    punkte = 0
    signale = []

    kategorie = config.KATEGORIEN.get(lead["kategorie"] or "", {})
    branchen_gewicht = kategorie.get("gewicht", 12)
    punkte += branchen_gewicht
    signale.append("Branche %s (+%d)" % (kategorie.get("label", "unbekannt"), branchen_gewicht))

    if lead["website"]:
        punkte += GEWICHTE["website_vorhanden"]
        signale.append("Website vorhanden (+%d)" % GEWICHTE["website_vorhanden"])
    if recherche.get("erreichbar"):
        punkte += GEWICHTE["erreichbar"]

    if lead["instagram"] or recherche.get("instagram"):
        punkte += GEWICHTE["instagram_vorhanden"]
        signale.append(
            "Instagram aktiv - versteht Social, braucht Nachschub (+%d)"
            % GEWICHTE["instagram_vorhanden"]
        )
    if recherche.get("tiktok"):
        punkte += GEWICHTE["tiktok"]
        signale.append("TikTok vorhanden (+%d)" % GEWICHTE["tiktok"])

    if recherche.get("erreichbar"):
        if not recherche.get("hat_video"):
            punkte += GEWICHTE["kein_video"]
            signale.append(
                "Keine Videos auf der Website - direkter Anlass (+%d)"
                % GEWICHTE["kein_video"]
            )
        else:
            punkte += ABZUEGE["profi_video_vorhanden"]
            signale.append(
                "Video bereits eingebunden (%d)" % ABZUEGE["profi_video_vorhanden"]
            )

        bilder = recherche.get("anzahl_bilder", 0)
        if bilder < 6:
            punkte += GEWICHTE["wenig_bilder"]
            signale.append(
                "Nur %d Bilder auf der Startseite - duenner Bildbestand (+%d)"
                % (bilder, GEWICHTE["wenig_bilder"])
            )

        if not recherche.get("mobil_optimiert"):
            punkte += GEWICHTE["nicht_mobil"]
            signale.append(
                "Website nicht mobil optimiert - Auftritt insgesamt veraltet (+%d)"
                % GEWICHTE["nicht_mobil"]
            )

        if recherche.get("shop"):
            punkte += GEWICHTE["shop"]
            signale.append("Onlineshop - Produktfotos und -videos zahlen direkt ein (+%d)"
                           % GEWICHTE["shop"])

    email = lead["email"] or (recherche.get("emails") or [None])[0]
    if email or lead["telefon"]:
        punkte += GEWICHTE["direkt_erreichbar"]
    else:
        punkte += ABZUEGE["kein_kontaktweg"]
        signale.append("Kein Kontaktweg gefunden (%d)" % ABZUEGE["kein_kontaktweg"])

    if email and not str(email).lower().startswith(("info@", "office@", "kontakt@")):
        punkte += GEWICHTE["persoenliche_email"]
        signale.append("Persoenliche E-Mail-Adresse (+%d)" % GEWICHTE["persoenliche_email"])

    haystack = _text(lead, "name", "website", "email")
    if any(begriff in haystack for begriff in config.WETTBEWERBER_BEGRIFFE):
        punkte += ABZUEGE["wettbewerber"]
        signale.append("Wirkt wie Wettbewerber/Produktionsfirma (%d)" % ABZUEGE["wettbewerber"])

    if any(marke in haystack for marke in
           ("mcdonald", "starbucks", "burger king", "subway", "kfc", "h&m", "zara",
            "spar ", "billa", "hofer", "rewe", "lidl", "dm-", "bipa")):
        punkte += ABZUEGE["kette"]
        signale.append("Filiale einer Kette - Marketing laeuft zentral (%d)" % ABZUEGE["kette"])

    punkte = max(0, min(100, punkte))
    return punkte, signale


def prioritaet(score):
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def bewerte_alle(min_score_qualifiziert=55, ausgabe=print):
    """Bewertet alle Leads neu und hebt gute auf Status 'qualifiziert'.

    Geschrieben wird nur, was sich wirklich geaendert hat, und das in Bloecken:
    Ueber die Netz-Schnittstelle waere eine Anfrage je Lead stundenlang
    unterwegs und braeche unterwegs ab.
    """
    conn = db.verbinde()
    alle = db.leads(conn)
    kennzahlen = {"bewertet": 0, "A": 0, "B": 0, "C": 0, "D": 0, "qualifiziert": 0,
                  "geaendert": 0}
    aenderungen = []

    for lead in alle:
        punkte, signale = bewerte_lead(lead)
        text = "\n".join(signale)
        neuer_status = lead["status"]
        # Nur frische Leads automatisch qualifizieren - laufende Deals
        # behalten ihren Status.
        if lead["status"] in ("neu", "qualifiziert"):
            neuer_status = ("qualifiziert" if punkte >= min_score_qualifiziert else "neu")
            if neuer_status == "qualifiziert":
                kennzahlen["qualifiziert"] += 1

        if (punkte != lead["score"] or text != (lead["signale"] or "")
                or neuer_status != lead["status"]):
            aenderungen.append({"id": lead["id"], "score": punkte, "signale": text,
                                "status": neuer_status})
        kennzahlen["bewertet"] += 1
        kennzahlen[prioritaet(punkte)] += 1

    if aenderungen:
        db.aktualisiere_viele(conn, aenderungen)
        kennzahlen["geaendert"] = len(aenderungen)
    conn.close()
    return kennzahlen
