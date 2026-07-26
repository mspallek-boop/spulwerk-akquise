"""Erstansprachen erzeugen: E-Mail, Instagram-DM, Telefonleitfaden.

Wichtig: Hier wird nichts versendet. Es entstehen ausschliesslich Entwuerfe,
die in der Datenbank landen und als .eml/.md exportiert werden koennen.
Das Versenden bleibt eine bewusste Handlung eines Menschen - siehe
RECHTLICHES.md.
"""

import re
import time

from . import config, db, llm, score

KANAELE = ("email", "dm", "telefon")

# Pause zwischen Leads, damit der Batch unter Groqs 30-Anfragen/Minute bleibt
# (gpt-oss mit reasoning=low ist schnell -> ohne Pause 429 -> Vorlagen-Fallback).
GROQ_PAUSE_SEK = 4.5


def _anrede(cfg):
    return "Sie" if cfg["firma"].get("anrede", "Sie") == "Sie" else "du"


def _kurzprofil(lead, recherche):
    """Verdichtet, was wir ueber den Betrieb wissen - Basis fuer den Aufhaenger."""
    zeilen = [
        "Name: %s" % lead["name"],
        "Branche: %s" % (config.KATEGORIEN.get(lead["kategorie"] or "", {})
                         .get("label", lead["kategorie"] or "unbekannt")),
    ]
    adresse = ", ".join(x for x in (lead["strasse"], lead["plz"], lead["ort"]) if x)
    if adresse:
        zeilen.append("Adresse: %s" % adresse)
    if lead["website"]:
        zeilen.append("Website: %s" % lead["website"])
    if recherche.get("titel"):
        zeilen.append("Seitentitel: %s" % recherche["titel"])
    if recherche.get("beschreibung"):
        zeilen.append("Selbstbeschreibung: %s" % recherche["beschreibung"])
    if lead["instagram"]:
        zeilen.append("Instagram: %s" % lead["instagram"])
    if recherche.get("erreichbar"):
        zeilen.append(
            "Videos auf der Website: %s" % ("ja" if recherche.get("hat_video") else "nein")
        )
        zeilen.append("Bilder auf der Startseite: %s" % recherche.get("anzahl_bilder", 0))
        zeilen.append(
            "Mobil optimiert: %s" % ("ja" if recherche.get("mobil_optimiert") else "nein")
        )
        if recherche.get("shop"):
            zeilen.append("Betreibt einen Onlineshop")
    if lead["signale"]:
        zeilen.append("Bewertungssignale:\n%s" % lead["signale"])
    return "\n".join(zeilen)


def _aufhaenger(lead, recherche):
    """Ein konkreter, ueberpruefbarer Anlass fuer die Ansprache."""
    if recherche.get("erreichbar") and not recherche.get("hat_video"):
        return ("auf Ihrer Website findet sich bisher kein einziges Video - "
                "dabei ist genau das der Punkt, an dem Gäste hängenbleiben")
    if recherche.get("erreichbar") and recherche.get("anzahl_bilder", 99) < 6:
        return ("Ihr Auftritt lebt aktuell von sehr wenigen Bildern - "
                "da liegt sichtbar Potenzial")
    if lead["instagram"]:
        return ("Ihr Instagram-Auftritt läuft, ihm fehlt aber regelmäßiger "
                "Video-Nachschub")
    if recherche.get("erreichbar") and not recherche.get("mobil_optimiert"):
        return ("Ihre Website wird am Handy nicht sauber dargestellt - "
                "dort schaut heute fast jeder zuerst")
    return ("wir drehen gerade viel in %s und Ihr Betrieb ist uns aufgefallen"
            % (lead["ort"] or "Wien"))


# ---------------------------------------------------------------- Vorlagen

def vorlage_email(lead, recherche, cfg):
    firma = cfg["firma"]
    sie = _anrede(cfg) == "Sie"
    ansprache = "Sehr geehrtes Team von %s," % lead["name"] if sie else "Hallo %s-Team," % lead["name"]
    hoeflich = "Sie" if sie else "du"
    ihr = "Ihr" if sie else "dein"

    betreff = "Kurze Idee für %s" % lead["name"]
    text = """{ansprache}

mein Name ist {absender}, ich mache gemeinsam mit {partner} das Videostudio
{studio} hier in {stadt}. Mir ist aufgefallen: {aufhaenger}.

Wir drehen genau dafür: Reels und kurze Clips, die {hoeflich} direkt auf
Instagram und der Website einsetzen {koennen} - gedreht an einem halben Tag,
geschnitten und geliefert innerhalb von 48 Stunden.

Wenn das interessant klingt, zeige ich {hoeflich_dat} gerne in 15 Minuten, wie so
etwas für {ihr_akk} {name} aussehen könnte - unverbindlich und ohne Verkaufsgespräch.
Passt {hoeflich_dat} diese oder nächste Woche ein kurzer Termin?

Herzliche Grüße
{absender}
{studio} · {website}{telefon_zeile}
""".format(
        ansprache=ansprache,
        absender=firma["absender"],
        partner=firma["partner"],
        studio=firma["name"],
        stadt=firma["ort"],
        aufhaenger=_aufhaenger(lead, recherche),
        hoeflich=hoeflich,
        hoeflich_dat="Ihnen" if sie else "dir",
        koennen="können" if sie else "kannst",
        ihr_akk=ihr,
        name=lead["name"],
        website=firma["website"],
        telefon_zeile=("\n" + firma["telefon"]) if firma.get("telefon") else "",
    )
    return betreff, text.strip()


def vorlage_dm(lead, recherche, cfg):
    firma = cfg["firma"]
    text = """Hallo {name}! 👋

{absender} vom Videostudio {studio} aus {stadt}. {aufhaenger_kurz}

Wir drehen Reels für Betriebe wie {euren} - halber Drehtag, fertiger Schnitt
in 48 Stunden. Soll ich zwei, drei Beispiele schicken, die zu {euch} passen?

{website}""".format(
        name=lead["name"],
        absender=firma["absender"],
        studio=firma["name"],
        stadt=firma["ort"],
        aufhaenger_kurz=(
            "Euer Profil ist uns aufgefallen - was fehlt, sind regelmäßige Videos."
            if lead["instagram"] else
            "Euer Auftritt ist uns aufgefallen."
        ),
        euren="euren",
        euch="euch",
        website=firma["website"],
    )
    return None, text.strip()


def vorlage_telefon(lead, recherche, cfg):
    firma = cfg["firma"]
    aufhaenger = _aufhaenger(lead, recherche)
    text = """LEITFADEN TELEFONAT - {name}
{trenner}

VORAB PRÜFEN
- Einwilligung vorhanden oder bestehende Geschäftsbeziehung? Wenn nein:
  nicht anrufen (§174 TKG). Siehe RECHTLICHES.md.
- Beste Zeit: {zeitfenster}

EINSTIEG (15 Sekunden)
"Guten Tag, mein Name ist {absender} von {studio}, wir sind ein Videostudio
hier in {stadt}. Ich habe eine kurze, konkrete Frage - haben Sie 30 Sekunden?"

AUFHÄNGER
"Mir ist aufgefallen: {aufhaenger}."

KERNAUSSAGE
"Wir drehen kurze Videos für Betriebe wie Ihren: ein halber Drehtag bei Ihnen
vor Ort, fertiger Schnitt innerhalb von 48 Stunden, direkt einsetzbar auf
Instagram und der Website."

FRAGEN, DIE DAS GESPRÄCH ÖFFNEN
- Wer kümmert sich bei Ihnen aktuell um Fotos und Videos?
- Was war das letzte Mal, dass professionell bei Ihnen gedreht wurde?
- Was würden Sie zeigen wollen, wenn Sie freie Hand hätten?

EINWÄNDE
- "Kein Interesse."  -> "Verstanden. Darf ich Ihnen zwei Beispiele per Mail
  schicken? Dann haben Sie es liegen, wenn es aktuell wird."
- "Zu teuer."        -> "Verständlich, deshalb starten die meisten mit einem
  einzelnen Drehtag statt einem großen Paket. Was wäre denn ein Rahmen, der
  für Sie realistisch ist?"
- "Machen wir selbst mit dem Handy." -> "Das ist ein guter Anfang - genau
  darauf setzen wir auf. Wir liefern das Material, das Sie zwischen Ihre
  eigenen Clips legen können."
- "Schicken Sie Unterlagen."  -> Zusage einholen: "Mache ich. An welche
  Adresse? Und darf ich in zwei Wochen kurz nachfragen?"

ABSCHLUSS
"Ich schlage vor: 15 Minuten, unverbindlich, bei Ihnen vor Ort oder per
Telefon. Passt Ihnen Dienstag oder Donnerstag besser?"

NACH DEM ANRUF
- Ergebnis eintragen:  ./spulwerk.py status {lead_id} <status> --notiz "..."
- Wiedervorlage setzen: ./spulwerk.py status {lead_id} kontaktiert --wiedervorlage 7

KONTAKTDATEN
Telefon: {telefon}
Website: {web}
""".format(
        name=lead["name"],
        trenner="=" * (len("LEITFADEN TELEFONAT - ") + len(lead["name"])),
        zeitfenster=(
            "Di-Do, 14:30-16:30 Uhr (ausserhalb des Mittagsgeschäfts)"
            if lead["kategorie"] in ("gastro", "hotel") else "Di-Do, 9:30-11:30 Uhr"
        ),
        absender=firma["absender"],
        studio=firma["name"],
        stadt=firma["ort"],
        aufhaenger=aufhaenger,
        lead_id=lead["id"],
        telefon=lead["telefon"] or "- nicht hinterlegt -",
        web=lead["website"] or "-",
    )
    return None, text.strip()


VORLAGEN = {
    "email": vorlage_email,
    "dm": vorlage_dm,
    "telefon": vorlage_telefon,
}


# ------------------------------------------------------------------ LLM-Weg

SYSTEM_PROMPT = """Du bist erfahren in B2B-Erstansprache für kleine Kreativstudios im DACH-Raum.
Du schreibst für {studio}, ein Video- und Fotostudio aus {stadt}.

Positionierung: {positionierung}
Leistungen:
{leistungen}

Fachlicher Hintergrund (echtes Verkaufsargument, keine Erfindung):
{hintergrund}
Baue diesen Hintergrund in den Text ein - kurz, in einem Nebensatz, nie als
eigener Absatz und nie angeberisch. Bei Architektur, Immobilien, Innenausbau,
Handwerk und Hotellerie gehört er in jede Ansprache; sonst nur, wenn er zum
Betrieb passt. Formuliere ihn jedes Mal anders ("wir kommen selbst aus der
Architektur", "als Architekturstudenten im Master schauen wir auf Raum und
Licht", …) - nicht denselben Satz wiederholen.

Regeln für jeden Text:
- Deutsch, österreichisches Geschäftsdeutsch. Die Anrede richtet sich nach dem
  Kanal (siehe Kanal-Auftrag ganz unten) - nicht selbst festlegen.
- Länge: E-Mail 110-150 Wörter (nicht kürzer!), Instagram-DM 35-55 Wörter.
- Schreibe wie ein echter Mensch, der kurz und direkt schreibt - natürliches
  Geschäftsdeutsch, nicht gestelzt.
- VERMEIDE leeres Agentur-Blabla: "Bewegtbild", "ganzheitlich", "maßgeschneidert",
  "innovativ", "Synergien". Sag statt "Bewegtbild" einfach "Videos", "kurze
  Clips" oder "Reels". (Wörter wie "Content", "hochwertig" oder "Reichweite" sind
  völlig okay.)
- Keine Superlative, keine erfundenen Prozentzahlen, keine Emojis in E-Mails
  (in DMs höchstens eines).
- KEINE Adressen erfinden. Die einzige Website ist {website}, die einzige
  E-Mail-Adresse {kontakt_email}. Keine andere Domain, keine Telefonnummer,
  kein Instagram-Handle - auch nicht "spulwerk.at" o. ä.
- Der Einstieg muss sich auf eine konkrete, überprüfbare Beobachtung über
  genau diesen Betrieb beziehen (nutze den mitgelieferten Aufhänger). Keine
  erfundenen Fakten, keine Behauptungen über Zahlen, Auszeichnungen oder
  Personen, die nicht in den Daten stehen.
- Nicht behaupten, man sei Kunde/Gast gewesen, wenn das nicht belegt ist.
- Der Betrieb darf nicht schlechtgemacht werden. Beobachtung sachlich halten.
- Kein Preis, keine Rabatte, keine Dringlichkeit erfinden.

Jede E-Mail deckt diese Punkte ab - Reihenfolge und Formulierung wählst du FREI:
- kurz, wer ihr seid ({absender_hinweis}) - aber NICHT immer mit "mein Name ist"
  anfangen, formuliere es jedes Mal anders
- die konkrete Beobachtung zu genau diesem Betrieb (nutze den Aufhänger)
- was ihr anbietet: kurze Videos / Reels, ein halber Drehtag, fertig in 48 Stunden,
  direkt für Instagram und Website - jedes Mal anders formuliert, nicht als Textbaustein
- genau eine lockere Frage nach 15 Minuten Termin, dann Gruß + Signatur.
  Die Signatur lautet exakt:
  {absender}
  {studio} · {website}

WICHTIG - ABWECHSLUNG: Diese Mail ist eine von vielen. KEINE zwei dürfen gleich
klingen. Variiere Einstieg, Satzbau und Wortwahl stark: fang mal mit der Beobachtung
an, mal mit einer Frage, mal mit dem Bezug zur Branche oder Stadt. Benutze NICHT immer
denselben Eröffnungssatz und nicht dieselbe Angebots-Formulierung. Beginne NICHT mit
"mir ist aufgefallen" oder "ich habe gesehen" - finde jedes Mal einen anderen ersten
Satz. Auch der Betreff soll variieren (nicht immer "Videos für X"). Pass den Ton an die Branche an - ein
Club/Restaurant darf lockerer klingen als eine Kanzlei oder ein Immobilienbüro.

Antworte ausschließlich mit einem JSON-Objekt (kein Text davor oder danach):
{{"betreff": "...", "text": "...", "aufhaenger": "...", "begruendung": "..."}}
Bei Kanal "dm" und "telefon" ist "betreff" null.
"""


def _system(cfg):
    firma = cfg["firma"]
    return SYSTEM_PROMPT.format(
        studio=firma["name"],
        stadt=firma["ort"],
        positionierung=firma["positionierung"],
        leistungen="\n".join("- " + l for l in firma["leistungen"]),
        anrede=firma.get("anrede", "Sie"),
        website=firma["website"],
        kontakt_email=firma.get("email", ""),
        absender=firma["absender"],
        hintergrund=firma.get("hintergrund", ""),
        absender_hinweis="%s betreibt mit %s das Videostudio %s in %s"
        % (firma["absender"], firma["partner"], firma["name"], firma["ort"]),
    )


KANAL_HINWEIS = {
    "email": ("Schreibe eine Erstansprache per E-Mail. FÖRMLICH, durchgehend "
              "Anrede 'Sie'. Betreff maximal 6 Wörter, kein Ausrufezeichen. "
              "Signatur mit Name, Studio und Website. "
              "ANREDE: Nur wenn in den Daten ein echter Personenname steht, "
              "diesen verwenden ('Sehr geehrte Frau <Nachname>'). Sonst immer "
              "'Sehr geehrte Damen und Herren' - NIEMALS den Firmennamen als "
              "Nachnamen benutzen, niemals 'Frau/Herr', niemals Platzhalter "
              "wie [Name]."),
    "dm": ("Schreibe eine Instagram-Direktnachricht. Locker und nahbar, sehr kurz, "
           "höchstens ein Emoji, endet mit einer Frage. Sprich den Betrieb als "
           "Team mit 'Ihr/euch' an. NUR wenn es klar eine Einzelperson ist "
           "(Solo-Selbstständige oder eine einzelne genannte Person), dann 'du'."),
    "telefon": ("Schreibe einen Telefonleitfaden mit den Abschnitten Einstieg, "
                "Aufhänger, Kernaussage, drei Öffnungsfragen, vier Einwände mit "
                "Antworten und Terminvorschlag. Stichpunktartig, sprechbar."),
}


def erzeuge_mit_llm(lead, recherche, cfg, key, kanal):
    prompt = """Kanal: {kanal}
{hinweis}

Absender: {absender} (gemeinsam mit {partner})

Daten über den Betrieb (nur diese Fakten verwenden):
{profil}
""".format(
        kanal=kanal,
        hinweis=KANAL_HINWEIS[kanal],
        absender=cfg["firma"]["absender"],
        partner=cfg["firma"]["partner"],
        profil=_kurzprofil(lead, recherche),
    )
    antwort = llm.frage_json(
        key, cfg["llm"]["modell"], _system(cfg), prompt,
        max_tokens=cfg["llm"].get("max_tokens", 1600),
        anbieter=config.anbieter(cfg),
    )
    text = (antwort.get("text") or "").strip()
    if not text:
        raise llm.LLMFehler("Kein Text im JSON")
    website = cfg["firma"]["website"]
    return (_saeubere(antwort.get("betreff"), website),
            _saeubere(text, website))


# Sicherheitsnetz: falls die KI trotz Verbot doch Jargon nutzt oder eine
# Adresse erfindet, hart ersetzen. Beides ist vorgekommen - "Bewegtbild" als
# Jargon und "spulwerk.at" als frei erfundene Domain in der Signatur.
# Dateiendungen ausnehmen: "./spulwerk.py" ist ein Befehl, keine Domain.
FALSCHE_DOMAIN = re.compile(
    r"(?<![\w@./])(?:https?://)?(?:www\.)?spulwerk\."
    r"(?!py\b|json\b|db\b|md\b|sh\b|txt\b|log\b)[a-z]{2,4}\b(?:/\S*)?",
    re.IGNORECASE,
)


def _saeubere(text, website=None):
    if not text:
        return text
    text = re.sub(r"[Bb]ewegtbild(ern|er|es|e|s)?", "Videos", text)
    if website:
        text = FALSCHE_DOMAIN.sub(website.rstrip("/"), text)
    return text


# --------------------------------------------------------------- Steuerung

def erzeuge_fuer_lead(conn, lead, cfg, key, kanaele=KANAELE, ausgabe=print):
    recherche = db.lade_json(lead["recherche"], {})
    ergebnisse = {}
    for kanal in kanaele:
        betreff, text, quelle = None, None, "vorlage"
        # Telefonleitfaden bewusst immer aus der Vorlage: das strukturierte
        # Skript (Einwände, Fragen, Ablauf) ist stärker als frei generierter
        # Text und spart Tokenbudget.
        if kanal != "telefon" and llm.verfuegbar(cfg, key):
            try:
                betreff, text = erzeuge_mit_llm(lead, recherche, cfg, key, kanal)
                quelle = "claude"
            except llm.TagesbudgetErschoepft:
                raise  # Lauf sauber beenden, nicht auf Vorlagen ausweichen
            except llm.LLMFehler as fehler:
                # Bewusst KEIN Vorlagentext: die generischen Texte waren genau
                # das, was der Lead-Qualität geschadet hat. Lieber gar kein
                # Entwurf - der nächste Lauf holt den Lead automatisch nach.
                ausgabe("    %s übersprungen (%s)" % (kanal, fehler))
                continue
        if not text:
            betreff, text = VORLAGEN[kanal](lead, recherche, cfg)
        db.speichere_entwurf(conn, lead["id"], kanal, betreff, text, quelle)
        ergebnisse[kanal] = (betreff, text, quelle)
    return ergebnisse


def erzeuge(min_score=None, limit=20, kanaele=KANAELE, nur_neue=True,
            lead_id=None, frisch_seit=None, ausgabe=print):
    cfg = config.lade_config()
    key = config.api_key(cfg)
    if min_score is None:
        min_score = cfg["akquise"]["min_score"]

    conn = db.verbinde()
    if lead_id:
        kandidaten = [db.hole_lead(conn, lead_id)]
        if kandidaten[0] is None:
            conn.close()
            raise ValueError("Lead %s nicht gefunden" % lead_id)
    else:
        qualifiziert_ab = cfg["akquise"]["min_score"]
        roh = db.leads(
            conn, min_score=min_score, limit=None,
            nur_ohne_entwurf=nur_neue,
        )
        # Unter der Qualifiziert-Grenze (z. B. C-Leads) nur Betriebe mit schon
        # analysierter Website texten - sonst fehlt der konkrete Aufhänger.
        kandidaten = [
            l for l in roh
            if l["score"] >= qualifiziert_ab or l["angereichert_am"]
        ]
        if frisch_seit:
            fertig = db.entwuerfe_frisch_seit(conn, kanaele, frisch_seit)
            vorher = len(kandidaten)
            kandidaten = [l for l in kandidaten if l["id"] not in fertig]
            ausgabe("  %d Leads übersprungen (seit %s schon getextet)."
                    % (vorher - len(kandidaten), frisch_seit))
        if limit is not None:
            kandidaten = kandidaten[:limit]

    kennzahlen = {"leads": 0, "entwuerfe": 0, "per_claude": 0, "budget_ende": None}
    for lead in kandidaten:
        if db.ist_gesperrt(conn, lead):
            ausgabe("  [%s] %s - auf Sperrliste, übersprungen" % (lead["id"], lead["name"]))
            continue
        ausgabe("  [%s] %s (Score %d, Prio %s)"
                % (lead["id"], lead["name"][:40], lead["score"], score.prioritaet(lead["score"])))
        try:
            with conn:
                ergebnisse = erzeuge_fuer_lead(conn, lead, cfg, key, kanaele, ausgabe)
        except llm.TagesbudgetErschoepft as fehler:
            kennzahlen["budget_ende"] = str(fehler)
            ausgabe("\n  Tagesbudget des Modells aufgebraucht - Lauf wird hier beendet.")
            ausgabe("  %s" % fehler)
            ausgabe("  Die restlichen Leads holt der nächste Lauf automatisch nach.")
            break
        kennzahlen["leads"] += 1
        kennzahlen["entwuerfe"] += len(ergebnisse)
        kennzahlen["per_claude"] += sum(
            1 for _, _, quelle in ergebnisse.values() if quelle == "claude"
        )
        # Tempo drosseln, damit Groqs Minuten-Limit nicht greift (nur bei LLM).
        if llm.verfuegbar(cfg, key) and config.anbieter(cfg) == "groq":
            time.sleep(GROQ_PAUSE_SEK)
    conn.close()
    return kennzahlen
