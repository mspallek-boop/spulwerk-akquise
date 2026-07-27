#!/usr/bin/env python3
"""Spulwerk Akquise - Kommandozeilenwerkzeug.

Ablauf einer Kampagne:

  1. ./spulwerk.py init                      Datenbank + Konfiguration anlegen
  2. ./spulwerk.py suche --stadt wien --kategorien gastro,hotel
  3. ./spulwerk.py anreichern                Websites analysieren
  4. ./spulwerk.py bewerten                  Leads priorisieren
  5. ./spulwerk.py texten --limit 20         Entwuerfe erzeugen
  6. ./spulwerk.py liste --min-score 60      Ergebnisse ansehen
  7. ./spulwerk.py zeige 42                  Dossier eines Leads
  8. ./spulwerk.py export --eml              Entwuerfe fuer den Versand ablegen

Oder in einem Rutsch:  ./spulwerk.py pipeline --stadt wien --kategorien gastro,hotel

Es wird nichts automatisch versendet. Siehe RECHTLICHES.md.
"""

import argparse
import datetime
import os
import subprocess
import sys
import time

from akquise import config, db, discover, enrich, score, outreach, report


LOCK_PFAD = config.DATEN_DIR / "lauf.lock"


def _prozess_laeuft(pid):
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


class Einzellauf:
    """Laesst texten/sweep nur einmal gleichzeitig laufen.

    Zwei parallele Laeufe reissen sonst Groqs Minutenlimit (Rate-Limit ->
    Vorlagen-Fallback, also schlechte Texte) und schreiben sich gegenseitig
    die Entwuerfe um. Eine tote PID im Lockfile wird ignoriert.
    """

    def __init__(self, name):
        self.name = name
        self.gehoert_uns = False

    def __enter__(self):
        config.stelle_verzeichnisse_bereit()
        if LOCK_PFAD.exists():
            try:
                inhalt = LOCK_PFAD.read_text(encoding="utf-8").strip().split("|")
                pid, wer, seit = int(inhalt[0]), inhalt[1], inhalt[2]
            except (ValueError, IndexError, OSError):
                pid, wer, seit = 0, "?", "?"
            if pid and _prozess_laeuft(pid):
                raise SystemExit(
                    "Abbruch: '%s' läuft bereits (PID %d, seit %s).\n"
                    "Zwei Läufe gleichzeitig sprengen das Minutenlimit von Groq "
                    "und liefern Vorlagentexte statt KI-Texten.\n"
                    "Warten, bis der Lauf fertig ist - oder %s löschen, falls "
                    "der Prozess abgestürzt ist." % (wer, pid, seit, LOCK_PFAD)
                )
        LOCK_PFAD.write_text("%d|%s|%s" % (os.getpid(), self.name, db.jetzt()),
                             encoding="utf-8")
        self.gehoert_uns = True
        return self

    def __exit__(self, *fehler):
        if self.gehoert_uns and LOCK_PFAD.exists():
            try:
                pid = int(LOCK_PFAD.read_text(encoding="utf-8").split("|")[0])
                if pid == os.getpid():
                    LOCK_PFAD.unlink()
            except (ValueError, IndexError, OSError):
                pass
        return False


def _kategorien_liste(wert):
    if not wert:
        return None
    return [k.strip() for k in wert.split(",") if k.strip()]


def cmd_init(args):
    db.initialisiere()
    cfg = config.lade_config()
    if not config.CONFIG_PATH.exists():
        config.schreibe_config(cfg)
        print("Konfiguration angelegt: %s" % config.CONFIG_PATH)
    print("Datenbank bereit: %s" % config.DB_PATH)
    print("\nNächster Schritt:")
    print("  ./spulwerk.py suche --stadt wien --kategorien gastro,hotel,immobilien")
    print("\nVerfügbare Kategorien:")
    for schluessel, daten in config.KATEGORIEN.items():
        print("  %-12s %s" % (schluessel, daten["label"]))
    print("\nVerfügbare Städte: %s" % ", ".join(sorted(config.STAEDTE)))


def cmd_suche(args):
    cfg = config.lade_config()
    kategorien = _kategorien_liste(args.kategorien) or cfg["akquise"]["kategorien"]
    stadt = args.stadt or cfg["akquise"]["stadt"]
    print("Suche Betriebe in %s: %s" % (stadt, ", ".join(kategorien)))
    kennzahlen = discover.finde(
        kategorien, stadt=stadt, eigene_bbox=args.bbox, limit=args.limit,
        nur_mit_website=args.nur_website,
    )
    print("\nFertig.")
    print("  Objekte geprüft:  %d" % kennzahlen["gefunden"])
    print("  Neue Leads:       %d" % kennzahlen["neu"])
    print("  Schon bekannt:    %d" % kennzahlen["bekannt"])
    print("  Übersprungen:     %d (Wettbewerber/ohne Website)" % kennzahlen["uebersprungen"])
    print("\nNächster Schritt: ./spulwerk.py anreichern")


def cmd_anreichern(args):
    cfg = config.lade_config()
    print("Analysiere Websites (das dauert je Lead ein paar Sekunden) ...")
    kennzahlen = enrich.reichere_an(
        limit=args.limit, pause=args.pause,
        robots_beachten=not args.ignoriere_robots, alle=args.alle,
    )
    print("\nFertig.")
    print("  Geprüft:      %d" % kennzahlen["geprueft"])
    print("  Erreichbar:   %d" % kennzahlen["erreichbar"])
    print("  E-Mails neu:  %d" % kennzahlen["emails"])
    print("  Instagram:    %d" % kennzahlen["instagram"])
    print("\nNächster Schritt: ./spulwerk.py bewerten")


def cmd_bewerten(args):
    cfg = config.lade_config()
    schwelle = args.min_score if args.min_score is not None else cfg["akquise"]["min_score"]
    kennzahlen = score.bewerte_alle(min_score_qualifiziert=schwelle)
    print("Bewertet: %d Leads" % kennzahlen["bewertet"])
    print("  Prio A (>=70): %d" % kennzahlen["A"])
    print("  Prio B (>=55): %d" % kennzahlen["B"])
    print("  Prio C (>=40): %d" % kennzahlen["C"])
    print("  Prio D (<40):  %d" % kennzahlen["D"])
    print("  Qualifiziert:  %d" % kennzahlen["qualifiziert"])
    print("\nNächster Schritt: ./spulwerk.py texten --limit 20")


def cmd_texten(args):
    cfg = config.lade_config()
    key = config.api_key(cfg)
    kanaele = _kategorien_liste(args.kanaele) or list(outreach.KANAELE)
    if key:
        print("Erzeuge Entwürfe mit Claude (%s) ..." % cfg["llm"]["modell"])
    else:
        print("Kein API-Key gesetzt (%s) - nutze Vorlagen." % cfg["llm"]["api_key_env"])
    with Einzellauf("texten"):
        kennzahlen = outreach.erzeuge(
            min_score=args.min_score, limit=args.limit, kanaele=kanaele,
            nur_neue=not args.neu_erzeugen, lead_id=args.lead,
            frisch_seit=args.frisch_seit,
        )
    print("\nFertig.")
    print("  Leads betextet: %d" % kennzahlen["leads"])
    print("  Entwürfe:       %d" % kennzahlen["entwuerfe"])
    print("  davon KI:       %d" % kennzahlen["per_claude"])
    if kennzahlen.get("budget_ende"):
        print("  Abbruch wegen Tagesbudget: %s" % kennzahlen["budget_ende"])
    print("\nAnsehen: ./spulwerk.py zeige <ID>   Exportieren: ./spulwerk.py export --eml")


def cmd_liste(args):
    conn = db.verbinde()
    zeilen = db.leads(
        conn, status=args.status, kategorie=args.kategorie,
        min_score=args.min_score, ort=args.ort, limit=args.limit,
        sortierung=args.sortierung,
    )
    print(report.tabelle(zeilen))
    print("\n%d Leads angezeigt." % len(zeilen))
    conn.close()


def cmd_zeige(args):
    conn = db.verbinde()
    lead = db.hole_lead(conn, args.lead)
    if lead is None:
        print("Lead %s nicht gefunden." % args.lead)
    else:
        print(report.dossier(conn, lead))
    conn.close()


def cmd_status(args):
    conn = db.verbinde()
    lead = db.hole_lead(conn, args.lead)
    if lead is None:
        print("Lead %s nicht gefunden." % args.lead)
        conn.close()
        return
    if args.neuer_status not in db.STATUS_REIHENFOLGE:
        print("Unbekannter Status. Erlaubt: %s" % ", ".join(db.STATUS_REIHENFOLGE))
        conn.close()
        return

    felder = {"status": args.neuer_status}
    if args.notiz:
        bisher = (lead["notizen"] + "\n") if lead["notizen"] else ""
        felder["notizen"] = "%s[%s] %s" % (bisher, db.heute(), args.notiz)
    if args.wiedervorlage is not None:
        felder["wiedervorlage"] = db.in_tagen(args.wiedervorlage)

    kontakt_status = ("kontaktiert", "nachgefasst")
    if args.neuer_status in kontakt_status and args.kanal in ("dm", "instagram"):
        felder["instagram_am"] = db.jetzt()
    if args.neuer_status in kontakt_status and args.kanal == "email" and not lead["gmail_gesendet_am"]:
        felder["gmail_gesendet_am"] = db.jetzt()

    with conn:
        if args.neuer_status in kontakt_status:
            felder["kontaktversuche"] = (lead["kontaktversuche"] or 0) + 1
            db.protokolliere_kontakt(
                conn, lead["id"], kanal=args.kanal or "unbekannt",
                ergebnis=args.notiz or args.neuer_status,
            )
        db.aktualisiere_lead(conn, lead["id"], **felder)
    print("Lead %d -> %s" % (lead["id"], args.neuer_status))
    if felder.get("wiedervorlage"):
        print("Wiedervorlage: %s" % felder["wiedervorlage"])
    conn.close()


def cmd_sperren(args):
    conn = db.verbinde()
    with conn:
        db.sperre(conn, args.muster, args.grund)
    print("Gesperrt: '%s'. Passende Leads wurden auf 'gesperrt' gesetzt." % args.muster)
    conn.close()


def cmd_export(args):
    cfg = config.lade_config()
    conn = db.verbinde()
    zeilen = db.leads(
        conn, status=args.status, min_score=args.min_score, limit=args.limit,
    )
    if args.eml:
        pfade = report.exportiere_eml(
            conn, zeilen, cfg["firma"]["email"], cfg["firma"]["absender"],
        )
        print("%d .eml-Entwürfe geschrieben nach %s" % (len(pfade), config.ENTWURF_DIR))
        print("Zum Versenden per Doppelklick im Mailprogramm öffnen und prüfen.")
    if args.csv:
        pfad = report.exportiere_csv(zeilen)
        print("CSV: %s" % pfad)
    if args.arbeitsliste:
        pfad = report.exportiere_arbeitsliste(conn, zeilen)
        print("Arbeitsliste: %s" % pfad)
    if args.instagram:
        pfad, anzahl = report.exportiere_instagram(conn, zeilen)
        print("Instagram-DM-Liste (%d Profile): %s" % (anzahl, pfad))
    if not (args.eml or args.csv or args.arbeitsliste or args.instagram):
        print("Nichts gewählt. Optionen: --eml  --csv  --arbeitsliste  --instagram")
    conn.close()


def cmd_uebersicht(args):
    conn = db.verbinde()
    print(report.uebersicht(conn))
    conn.close()


def cmd_pipeline(args):
    print(">>> 1/4 Suche")
    cmd_suche(args)
    print("\n>>> 2/4 Anreichern")
    cmd_anreichern(args)
    print("\n>>> 3/4 Bewerten")
    cmd_bewerten(args)
    print("\n>>> 4/4 Texten")
    cmd_texten(args)
    print("\nPipeline fertig. Übersicht:")
    cmd_uebersicht(args)


def cmd_pruefe(args):
    from akquise import llm
    cfg = config.lade_config()
    anbieter = config.anbieter(cfg)
    key = config.api_key(cfg)
    meta = config.ANBIETER.get(anbieter, config.ANBIETER["anthropic"])
    if not key:
        print("Kein API-Key für Anbieter '%s' gefunden." % anbieter)
        print("Lege ihn ab in einer der Quellen (in dieser Reihenfolge geprüft):")
        print("  1. export %s=\"...\"" % meta["env"])
        print("  2. Datei %s (nur der Key, eine Zeile)" % config.key_datei(cfg))
        print("  3. Feld llm.api_key in config.json")
        if anbieter == "google":
            print("\nGratis-Key holen: https://aistudio.google.com/apikey")
        return
    print("Anbieter '%s', Key gefunden (endet auf ...%s). Teste %s ..."
          % (anbieter, key[-4:], cfg["llm"]["modell"]))
    try:
        antwort = llm.frage(
            key, cfg["llm"]["modell"],
            "Antworte mit genau einem Wort.",
            "Sag 'bereit', wenn du mich verstehst.",
            max_tokens=200, temperature=0, anbieter=anbieter,
        )
        print("Verbindung ok. Antwort des Modells: %s" % antwort.strip())
    except llm.LLMFehler as fehler:
        print("Fehlgeschlagen: %s" % fehler)


def cmd_sweep(args):
    """Ein vollständiger Durchlauf über alle Branchen einer Stadt.
    Idempotent: findet bei jedem Lauf nur neue Betriebe (Dubletten werden
    automatisch übersprungen). Für den täglichen Hintergrundlauf gedacht."""
    from akquise import lauf
    cfg = config.lade_config()
    lauf_id = None
    try:
        lauf.raeume_verwaiste_auf(cfg)
        lauf_id = lauf.melde_start(cfg, quelle=getattr(args, "quelle", "mac"),
                                   lauf_id=getattr(args, "lauf_id", None))
    except Exception as fehler:
        print("Hinweis: Laufprotokoll nicht erreichbar (%s)" % fehler)

    try:
        with Einzellauf("sweep"):
            kennzahlen = _sweep_lauf(args, lauf_id=lauf_id)
        lauf.melde_ende(cfg, lauf_id, "fertig", kennzahlen=kennzahlen)
    except SystemExit as fehler:      # Lauf-Sperre: ein anderer Lauf ist aktiv
        lauf.melde_ende(cfg, lauf_id, "abgebrochen", meldung=str(fehler)[:300])
        raise
    except BaseException as fehler:
        lauf.melde_ende(cfg, lauf_id, "fehler", meldung=str(fehler)[:300])
        raise


class Fortschritt:
    """Meldet dem Portal, wo der Lauf gerade steht.

    Die Prozentwerte sind grobe Wegmarken - genau genug für einen Balken, und
    innerhalb des Textens zählt sie zusätzlich die einzelnen Leads mit.
    """

    SCHRITTE = [
        ("Texten (Rückstand)", 5, 30),
        ("Gmail-Entwürfe", 30, 38),
        ("Suche", 38, 60),
        ("Anreichern", 60, 78),
        ("Bewerten", 78, 84),
        ("Portal-Sync", 84, 90),
        ("Nachschlag", 90, 100),
    ]

    # Beim Anreichern kommt alle zwei Sekunden eine Website - so oft muss das
    # Portal nichts erfahren, es sieht ohnehin nur alle fünf Sekunden nach.
    MELDEPAUSE = 4.0

    def __init__(self, cfg, lauf_id):
        from akquise import lauf as lauf_modul
        self.cfg, self.lauf_id, self.lauf = cfg, lauf_id, lauf_modul
        self.erledigt = []
        self.aktuell = None
        self.zuletzt = 0.0

    def schritt(self, name):
        if self.aktuell and self.aktuell not in self.erledigt:
            self.erledigt.append(self.aktuell)
        self.aktuell = name
        start = dict((s[0], s[1]) for s in self.SCHRITTE).get(name, 0)
        self.zuletzt = time.monotonic()
        self.lauf.melde_schritt(self.cfg, self.lauf_id, name, start, self.erledigt)

    def zwischenstand(self, text, anteil=0.0):
        """anteil 0..1 innerhalb des laufenden Schritts."""
        if anteil < 1.0 and time.monotonic() - self.zuletzt < self.MELDEPAUSE:
            return
        self.zuletzt = time.monotonic()
        bereich = dict((s[0], (s[1], s[2])) for s in self.SCHRITTE).get(self.aktuell)
        prozent = None
        if bereich:
            prozent = bereich[0] + (bereich[1] - bereich[0]) * max(0.0, min(1.0, anteil))
        self.lauf.melde_schritt(self.cfg, self.lauf_id,
                                "%s — %s" % (self.aktuell, text), prozent, self.erledigt)


# Erfahrungswert aus den bisherigen Läufen: gut die Hälfte der analysierten
# Websites führt zu einem Lead ab Score 55.
QUALIFIZIERUNGSQUOTE = 0.5


# Groqs Gratis-Tarif deckelt nicht die Anfragen, sondern die Tokens pro Tag
# (200.000). Ein Lead kostet mit E-Mail und DM rund 4.700 - mehr als das geht
# an keinem Tag, egal was die Anfragen-Kopfzeilen sagen.
LEADS_PRO_TAG_MAX = 42


def _textbudget(cfg, args):
    """Wie viele Leads lassen sich heute Nacht überhaupt betexten?"""
    if args.ohne_texten:
        return 0
    budget = args.text_limit or LEADS_PRO_TAG_MAX
    if getattr(args, "budget", None) and config.anbieter(cfg) == "groq":
        from akquise import llm
        limit, rest = llm.groq_ratelimit(config.api_key(cfg), cfg["llm"]["modell"])
        if limit and rest is not None:
            offen = max(0, int(limit * args.budget) - (limit - rest))
            budget = min(budget, max(0, offen // 2))
    return min(budget, LEADS_PRO_TAG_MAX)


def _bedarf(cfg, args, min_score):
    """Der Lauf soll nicht mehr suchen und analysieren, als er danach auch
    betexten kann - sonst wächst nur ein Berg unbearbeiteter Leads.

    Rückgabe: (wie viele Websites analysieren, soll gesucht werden, Begründung)
    """
    budget = _textbudget(cfg, args)
    conn = db.verbinde()
    try:
        # Nur die wirklich Qualifizierten zählen (Prio A/B) - die C-Leads
        # sind Beifang und sollen die Suche nicht dauerhaft blockieren.
        rueckstand = len([l for l in db.leads(conn, min_score=min_score)
                          if not db.entwuerfe(conn, l["id"], "email")])
        vorrat = len([l for l in db.leads(conn)
                      if l["website"] and not l["angereichert_am"]])
    finally:
        conn.close()

    if budget <= 0:
        return 0, False, "Tagesbudget des Modells ist aufgebraucht"
    if rueckstand >= budget:
        return 0, False, ("%d qualifizierte Leads warten schon auf Text, "
                          "das füllt das heutige Budget von %d" % (rueckstand, budget))

    fehlend = budget - rueckstand
    noetig = int(fehlend / QUALIFIZIERUNGSQUOTE) + 1
    grenze = min(noetig, args.enrich_limit or noetig)
    suchen = vorrat < noetig
    grund = ("%d Leads im Rückstand, Budget %d -> %d neue Qualifizierte nötig, "
             "dafür ~%d Websites analysieren (Vorrat: %d)"
             % (rueckstand, budget, fehlend, grenze, vorrat))
    return grenze, suchen, grund


def _texten_schritt(cfg, args, min_score, melder=None):
    """Textet offene Leads, so weit das Tagesbudget des Modells reicht."""
    if args.ohne_texten:
        print("  Übersprungen (--ohne-texten)")
        return
    text_limit = args.text_limit
    # Budget-Modus: so viele Leads texten, bis X% des Groq-Tageslimits genutzt sind.
    if getattr(args, "budget", None) and config.anbieter(cfg) == "groq":
        from akquise import llm
        limit, rest = llm.groq_ratelimit(config.api_key(cfg), cfg["llm"]["modell"])
        if limit and rest is not None:
            genutzt = limit - rest
            ziel = int(limit * args.budget)
            offen = max(0, ziel - genutzt)
            text_limit = max(0, offen // 2)  # ~2 Anfragen je Lead (E-Mail + DM)
            print("  Groq-Budget: %d/%d Anfragen genutzt heute, Ziel %d%% (%d) "
                  "→ bis zu %d Leads texten"
                  % (genutzt, limit, int(args.budget * 100), ziel, text_limit))
    if text_limit <= 0:
        print("  Übersprungen — Tagesbudget des Modells erschöpft (morgen weiter).")
        return
    text_min = cfg["akquise"].get("text_min_score", min_score)
    print("  Ab Score %d (C-Leads nur mit analysierter Website) ..." % text_min)

    gezaehlt = {"n": 0}

    def ausgabe(zeile):
        print(zeile)
        # Jede Lead-Zeile beginnt mit "  [" - daran zählen wir den Fortschritt.
        if melder and zeile.startswith("  ["):
            gezaehlt["n"] += 1
            melder.zwischenstand("%d von max. %d Leads" % (gezaehlt["n"], text_limit),
                                 gezaehlt["n"] / max(text_limit, 1))

    outreach.erzeuge(min_score=text_min, limit=text_limit, nur_neue=True, ausgabe=ausgabe)


def _gmail_schritt(cfg, min_score):
    """Warteschlange schreiben und - wenn ein App-Passwort da ist - die
    Entwürfe direkt ins Postfach legen."""
    from akquise import gmail
    conn = db.verbinde()
    try:
        offen, verworfen, _ = gmail.warteschlange(conn, min_score=min_score)
        gmail.schreibe(offen)
        if verworfen:
            print("  %d mangelhafte Entwürfe verworfen (nächster Lauf textet neu)."
                  % len(verworfen))
        _, passwort = config.gmail_zugang(cfg)
        if not offen:
            print("  Nichts offen.")
            return
        if not passwort:
            print("  %d Entwürfe warten (kein App-Passwort → der Claude-Task legt sie an)."
                  % len(offen))
            return
        from akquise import gmail_imap
        print("  Lege %d Entwürfe in %s an ..." % (len(offen), cfg["gmail"]["adresse"]))
        ergebnis = gmail_imap.lege_entwuerfe_an(cfg, offen)
        gmail.markiere(conn, ergebnis["angelegt"])
        gmail.markiere(conn, [i for i, g in ergebnis["uebersprungen"] if "schon" in g])
        print("  %d angelegt, %d übersprungen."
              % (len(ergebnis["angelegt"]), len(ergebnis["uebersprungen"])))
        k = gmail.abgleich(conn, cfg)
        print("  Abgleich: %d im Postfach, %d bereits versendet."
              % (k["im_postfach"], k["gesendet_gesamt"]))
    except Exception as fehler:
        print("  Gmail-Schritt übersprungen: %s" % fehler)
    finally:
        conn.close()


def _rotation(kategorien, wie_viele):
    """Waehlt die Branchen der heutigen Nacht.

    Alle zwoelf Branchen in einer Nacht zu durchsuchen dauert Stunden - der
    Mac muesste die ganze Zeit wach bleiben. Stattdessen bekommt jede Nacht
    ein Stueck, ueber die Woche ist alles einmal dran. Der Startpunkt haengt
    am Tag im Jahr, ist also ohne gespeicherten Zaehler reproduzierbar.
    """
    if not wie_viele or wie_viele >= len(kategorien):
        return kategorien
    tag = datetime.date.today().timetuple().tm_yday
    start = (tag * wie_viele) % len(kategorien)
    doppelt = kategorien + kategorien
    return doppelt[start:start + wie_viele]


def _schlafen_legen():
    """Legt den Mac schlafen (braucht kein sudo)."""
    print("\nLauf beendet - Mac geht wieder schlafen.")
    sys.stdout.flush()
    subprocess.run(["pmset", "sleepnow"], capture_output=True)


def _sweep_lauf(args, lauf_id=None):
    from akquise import lauf as lauf_modul
    cfg = config.lade_config()
    melder = Fortschritt(cfg, lauf_id)
    stadt = args.stadt or cfg["akquise"]["stadt"]
    alle_kategorien = _kategorien_liste(args.kategorien) or list(config.KATEGORIEN)
    kategorien = _rotation(alle_kategorien, getattr(args, "rotation", 0))
    min_score = cfg["akquise"]["min_score"]

    # Harte Obergrenze für die Laufzeit: danach werden nur noch die kurzen
    # Schritte erledigt, damit der Rechner wieder schlafen kann.
    max_minuten = getattr(args, "max_minuten", 0) or 0
    schluss = (time.monotonic() + max_minuten * 60) if max_minuten else None

    def zeit_um():
        return schluss is not None and time.monotonic() > schluss

    print("=" * 60)
    print("SWEEP  %s  |  Stadt: %s  |  %d Branchen" % (db.jetzt(), stadt, len(kategorien)))
    print("=" * 60)

    conn = db.verbinde()
    vorher = db.anzahl_leads(conn)
    conn.close()

    # Zuerst der Rückstand: texten und ins Postfach legen. Das ist der Teil mit
    # dem unmittelbaren Nutzen - und er ist in Minuten erledigt. Die Suche
    # danach läuft über Stunden; bricht der Lauf ab (Ruhezustand, Netz), sind
    # die Entwürfe trotzdem schon da.
    print("\n[1/6] Texten (Rückstand aus den Vornächten) ...")
    melder.schritt("Texten (Rückstand)")
    _texten_schritt(cfg, args, min_score, melder)

    print("\n[2/6] Gmail-Entwürfe ...")
    melder.schritt("Gmail-Entwürfe")
    _gmail_schritt(cfg, min_score)

    enrich_grenze, suchen_noetig, grund = _bedarf(cfg, args, min_score)
    print("\n  Bedarf: %s" % grund)

    print("\n[3/6] Suche (%s) ..." % ", ".join(kategorien))
    melder.schritt("Suche")
    if not suchen_noetig:
        print("  Übersprungen - es liegen genug unbearbeitete Leads bereit.")
        kategorien = []
    neu = 0
    for nummer, kategorie in enumerate(kategorien, 1):
        if zeit_um():
            print("  Zeitgrenze erreicht - restliche Branchen kommen morgen dran.")
            break
        melder.zwischenstand("Branche %d von %d: %s" % (nummer, len(kategorien), kategorie),
                             (nummer - 1) / len(kategorien))
        k = discover.finde([kategorie], stadt=stadt, nur_mit_website=args.nur_website)
        neu += k["neu"]
        melder.zwischenstand("%d von %d Branchen, %d neue Leads"
                             % (nummer, len(kategorien), neu), nummer / len(kategorien))
    print("  Neue Leads: %d" % neu)

    print("\n[4/6] Anreichern (neue Websites) ...")
    melder.schritt("Anreichern")
    if zeit_um():
        print("  Zeitgrenze erreicht - übersprungen.")
    elif enrich_grenze <= 0:
        print("  Übersprungen - %s." % grund)
    else:
        print("  Höchstens %d Websites (mehr bringt heute nichts)." % enrich_grenze)
        # Jede geprüfte Website meldet sich - so bewegt sich die Live-Ansicht
        # auch in dem Schritt, der am längsten dauert.
        gezaehlt = {"n": 0}

        def ausgabe(zeile):
            print(zeile)
            if zeile.startswith("  ["):
                gezaehlt["n"] += 1
                melder.zwischenstand("%d von %d Websites" % (gezaehlt["n"], enrich_grenze),
                                     gezaehlt["n"] / max(enrich_grenze, 1))

        enrich.reichere_an(limit=enrich_grenze, pause=cfg["akquise"]["pause_sekunden"],
                           ausgabe=ausgabe)

    print("\n[5/6] Bewerten ...")
    melder.schritt("Bewerten")
    score.bewerte_alle(min_score_qualifiziert=min_score)

    conn = db.verbinde()
    zeilen = db.leads(conn, min_score=min_score)
    report.exportiere_instagram(conn, zeilen)
    report.exportiere_csv(zeilen)
    nachher = db.anzahl_leads(conn)
    conn.close()

    print("\n[6/6] Sync nach Supabase (Portal) ...")
    melder.schritt("Portal-Sync")
    try:
        from akquise import sync
        s = sync.synchronisiere()
        print("  %d Leads ins Portal übertragen." % s["gesendet"])
    except Exception as fehler:
        print("  Sync übersprungen: %s" % fehler)

    # Zweite Runde: die heute neu gefundenen Leads texten und ablegen, falls
    # vom Tagesbudget noch etwas übrig ist.
    if zeit_um():
        print("\n[Nachschlag] Zeitgrenze erreicht - übersprungen.")
    else:
        melder.schritt("Nachschlag")
        print("\n[Nachschlag] Neue Leads texten ...")
        _texten_schritt(cfg, args, min_score, melder)
        print("\n[Nachschlag] Gmail-Entwürfe ...")
        _gmail_schritt(cfg, min_score)

    print("\n" + "=" * 60)
    print("SWEEP fertig. %d neue Leads in diesem Lauf (Gesamt: %d)."
          % (nachher - vorher, nachher))
    print("Exporte aktualisiert in export/. Ansehen: ./spulwerk.py web")
    print("=" * 60)

    if getattr(args, "dann_schlafen", False):
        _schlafen_legen()

    return {"neue_leads": nachher - vorher, "leads_gesamt": nachher,
            "branchen": kategorien, "zeitgrenze_erreicht": zeit_um()}


# --- macOS-Zeitplan (launchd) für den täglichen Sweep -----------------

PLIST_LABEL = "com.spulwerk.akquise.sweep"


def _plist_pfad():
    return os.path.expanduser("~/Library/LaunchAgents/%s.plist" % PLIST_LABEL)


def _plist_inhalt(stunde, minute, stadt, enrich_limit, budget,
                  rotation=4, max_minuten=40, schlafen=True):
    """Baut den launchd-Auftrag.

    caffeinate haelt den Mac nur waehrend des Laufs wach; danach legt der
    Sweep ihn mit `pmset sleepnow` selbst wieder schlafen. Der Rechner ist
    also nur die Minuten wach, die er zum Arbeiten braucht.
    """
    skript = str((config.BASE_DIR / "spulwerk.py").resolve())
    log = str((config.DATEN_DIR / "sweep.log").resolve())
    argumente = [
        "/usr/bin/caffeinate", "-dims", sys.executable, skript, "sweep",
        "--stadt", stadt,
        "--enrich-limit", str(enrich_limit),
        "--budget", str(budget),
        "--rotation", str(rotation),
        "--max-minuten", str(max_minuten),
    ]
    if schlafen:
        argumente.append("--dann-schlafen")
    zeilen = "\n".join("    <string>%s</string>" % a for a in argumente)
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array>
%s
  </array>
  <key>WorkingDirectory</key><string>%s</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
""" % (PLIST_LABEL, zeilen, str(config.BASE_DIR.resolve()), stunde, minute, log, log)


WACHE_LABEL = "com.spulwerk.akquise.wache"


def _wache_plist_pfad():
    return os.path.expanduser("~/Library/LaunchAgents/%s.plist" % WACHE_LABEL)


def _wache_plist_inhalt(takt_sekunden=300):
    """Kleiner Dienst, der alle paar Minuten nachsieht, ob im Portal ein Lauf
    angefordert wurde. Kostet fast nichts: eine Abfrage an Supabase."""
    skript = str((config.BASE_DIR / "spulwerk.py").resolve())
    log = str((config.DATEN_DIR / "wache.log").resolve())
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>%s</string>
    <string>wache</string>
  </array>
  <key>WorkingDirectory</key><string>%s</string>
  <key>StartInterval</key><integer>%d</integer>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
""" % (WACHE_LABEL, sys.executable, skript, str(config.BASE_DIR.resolve()),
       takt_sekunden, log, log)


def _wache_einrichten(takt_sekunden=300):
    pfad = _wache_plist_pfad()
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as datei:
        datei.write(_wache_plist_inhalt(takt_sekunden))
    subprocess.run(["launchctl", "unload", pfad], capture_output=True)
    ergebnis = subprocess.run(["launchctl", "load", pfad], capture_output=True, text=True)
    return ergebnis.returncode == 0, (ergebnis.stderr or ergebnis.stdout).strip()


def cmd_zeitplan(args):
    pfad = _plist_pfad()
    if args.aktion == "status":
        vorhanden = os.path.exists(pfad)
        print("Plist: %s" % ("vorhanden" if vorhanden else "nicht installiert"))
        if vorhanden:
            print("Datei: %s" % pfad)
            geladen = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            aktiv = PLIST_LABEL in geladen.stdout
            print("Bei launchd geladen: %s" % ("ja" if aktiv else "nein"))
            print("Log: %s" % (config.DATEN_DIR / "sweep.log"))
        wache_da = os.path.exists(_wache_plist_pfad())
        geladen = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        print("Abholdienst (Portal-Knopf): %s"
              % ("läuft alle 5 Minuten" if wache_da and WACHE_LABEL in geladen.stdout
                 else ("installiert, nicht geladen" if wache_da else "nicht installiert")))
        return

    if args.aktion == "entfernen":
        if os.path.exists(pfad):
            subprocess.run(["launchctl", "unload", pfad], capture_output=True)
            os.remove(pfad)
            print("Zeitplan entfernt.")
        else:
            print("Kein Zeitplan installiert.")
        wache = _wache_plist_pfad()
        if os.path.exists(wache):
            subprocess.run(["launchctl", "unload", wache], capture_output=True)
            os.remove(wache)
            print("Abholdienst entfernt.")
        return

    # aktion == "einrichten"
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as datei:
        datei.write(_plist_inhalt(args.stunde, args.minute, args.stadt,
                                  args.enrich_limit, args.budget,
                                  rotation=args.rotation,
                                  max_minuten=args.max_minuten,
                                  schlafen=not args.wach_bleiben))
    subprocess.run(["launchctl", "unload", pfad], capture_output=True)
    ergebnis = subprocess.run(["launchctl", "load", pfad], capture_output=True, text=True)
    if ergebnis.returncode == 0:
        print("Zeitplan eingerichtet: täglich %02d:%02d Uhr, Stadt '%s'."
              % (args.stunde, args.minute, args.stadt))
        print("  Branchen je Nacht: %s" % (args.rotation or "alle"))
        print("  Laufzeitgrenze:    %d Minuten" % args.max_minuten)
        print("  Danach:            %s"
              % ("Mac geht wieder schlafen" if not args.wach_bleiben else "Mac bleibt wach"))
        print("Plist: %s" % pfad)
        print("Log:   %s" % (config.DATEN_DIR / "sweep.log"))
        # Fünf Minuten vor dem Start wecken - der Rechner braucht einen Moment,
        # bis Netzwerk und Dienste wieder stehen.
        weck = (datetime.datetime(2000, 1, 2, args.stunde, args.minute)
                - datetime.timedelta(minutes=5))
        print("Damit der Mac zur Uhrzeit aufwacht, einmalig im Terminal:")
        print("  sudo pmset repeat wakeorpoweron MTWRFSU %s" % weck.strftime("%H:%M:%S"))
        if not args.ohne_abholdienst:
            ok, meldung = _wache_einrichten()
            print("  Abholdienst:       %s"
                  % ("alle 5 Minuten (für den Knopf im Portal)"
                     if ok else "nicht eingerichtet (%s)" % meldung))
        print("Status prüfen: ./spulwerk.py zeitplan status")
    else:
        print("launchctl-Fehler: %s" % (ergebnis.stderr or ergebnis.stdout))


def cmd_wache(args):
    """Schaut nach, ob im Portal ein Lauf angefordert wurde - und startet ihn.

    Läuft alle paar Minuten als kleiner launchd-Job. So kann der Knopf auf der
    Akquise-Seite einen Lauf auslösen, obwohl die Seite den Mac nicht direkt
    erreichen kann: sie legt den Auftrag in Supabase ab, der Mac holt ihn.
    """
    from akquise import lauf
    cfg = config.lade_config()
    try:
        lauf.raeume_verwaiste_auf(cfg)
        auftrag = lauf.offene_anforderung(cfg)
    except Exception as fehler:
        print("Laufprotokoll nicht erreichbar: %s" % fehler)
        return 1

    if not auftrag:
        if args.ausfuehrlich:
            print("Kein Auftrag offen.")
        return 0

    # Läuft schon einer, bleibt der Auftrag liegen - sonst würde die Lauf-Sperre
    # ihn sofort als "abgebrochen" quittieren und er wäre verbraucht.
    if LOCK_PFAD.exists():
        try:
            pid = int(LOCK_PFAD.read_text(encoding="utf-8").split("|")[0])
        except (ValueError, IndexError, OSError):
            pid = 0
        if pid and _prozess_laeuft(pid):
            print("Auftrag %s wartet - es läuft bereits ein Durchgang (PID %d)."
                  % (auftrag["id"], pid))
            return 0

    print("Auftrag %s (angefordert %s von %s) - starte Sweep."
          % (auftrag["id"], auftrag.get("angefordert_am"), auftrag.get("angefordert_von")))
    sweep_args = baue_parser().parse_args([
        "sweep",
        "--rotation", str(args.rotation),
        "--max-minuten", str(args.max_minuten),
        "--enrich-limit", str(args.enrich_limit),
        "--budget", str(args.budget),
    ])
    sweep_args.lauf_id = auftrag["id"]
    sweep_args.quelle = "mac"
    return cmd_sweep(sweep_args)


def cmd_instagram(args):
    """Hakt verschickte Instagram-DMs ab (die gehen ja per Hand raus)."""
    conn = db.verbinde()
    if not args.ids:
        offen = [l for l in db.leads(conn, min_score=args.min_score)
                 if l["instagram"] and not l["instagram_am"]]
        print("%d qualifizierte Leads mit Instagram-Profil, noch nicht angeschrieben:"
              % len(offen))
        for lead in offen[:15]:
            print("  [%s] %-34s %s" % (lead["id"], lead["name"][:34], lead["instagram"]))
        if len(offen) > 15:
            print("  ... und %d weitere" % (len(offen) - 15))
        print("\nAbhaken: ./spulwerk.py instagram <ID> <ID> ...")
    else:
        db.markiere_gmail(conn, args.ids, feld="instagram_am")
        for lead_id in args.ids:
            lead = db.hole_lead(conn, lead_id)
            if lead and lead["status"] in ("neu", "qualifiziert"):
                db.aktualisiere_lead(conn, lead_id, status="kontaktiert",
                                     kontaktversuche=(lead["kontaktversuche"] or 0) + 1)
                db.protokolliere_kontakt(conn, lead_id, kanal="dm",
                                         ergebnis="Instagram-DM verschickt")
        print("%d Leads als per Instagram kontaktiert vermerkt." % len(args.ids))
    conn.close()


def cmd_saeubern(args):
    """Zieht das Sicherheitsnetz nachträglich über alle gespeicherten Entwürfe:
    Jargon raus, erfundene Domains auf die echte Website korrigiert."""
    cfg = config.lade_config()
    web = cfg["firma"]["website"]
    conn = db.verbinde()
    zeilen = db.entwuerfe(conn)

    aenderungen = 0
    for eintrag in zeilen:
        neu_b = outreach._saeubere(eintrag["betreff"], web)
        neu_t = outreach._saeubere(eintrag["text"], web)
        if neu_b != eintrag["betreff"] or neu_t != eintrag["text"]:
            db.speichere_entwurf(conn, eintrag["lead_id"], eintrag["kanal"],
                                 neu_b, neu_t, eintrag.get("quelle", "vorlage"))
            aenderungen += 1
    conn.close()
    print("Entwürfe geprüft: %d  |  korrigiert: %d" % (len(zeilen), aenderungen))
    if not aenderungen:
        return
    print("Danach nicht vergessen: ./spulwerk.py sync --voll")


def cmd_gmail(args):
    """Bereitet E-Mail-Entwuerfe fuer den Gmail-Connector auf bzw. hakt sie ab."""
    from akquise import gmail
    conn = db.verbinde()

    if args.aktion == "status":
        s = gmail.stand(conn, min_score=args.min_score)
        print("Leads ab Score %d mit E-Mail-Adresse: %d" % (args.min_score, s["kandidaten"]))
        print("  schon als Gmail-Entwurf angelegt: %d" % s["in_gmail"])
        print("  versandfertig (warten auf Gmail): %d" % s["offen"])
        print("  mangelhaft (werden neu getextet): %d" % s["mangelhaft"])
        print("  ohne Text (erst texten):          %d" % s["ohne_text"])
        if s["unbrauchbar"]:
            print("  unbrauchbare Adresse:             %d" % len(s["unbrauchbar"]))
            for u in s["unbrauchbar"][:5]:
                print("     [%s] %s -> %s" % (u["lead_id"], u["name"][:34], u["an"]))
        print("\nDatei: %s" % gmail.STANDARD_DATEI)

    elif args.aktion == "warteschlange":
        eintraege, verworfen, unbrauchbar = gmail.warteschlange(
            conn, min_score=args.min_score, prio=args.prio,
            limit=args.limit, erneut=args.erneut,
        )
        pfad = gmail.schreibe(eintraege, args.datei)
        print("%d Entwürfe in der Warteschlange: %s" % (len(eintraege), pfad))
        if verworfen:
            print("%d mangelhafte Entwürfe verworfen (werden neu getextet):"
                  % len(verworfen))
            for v in verworfen:
                print("  [%s] %s - %s"
                      % (v["lead_id"], v["name"][:38], ", ".join(v["gruende"])))
        for eintrag in eintraege[:5]:
            print("  [%s] %s (Prio %s) -> %s"
                  % (eintrag["lead_id"], eintrag["name"][:38],
                     eintrag["prio"], eintrag["an"]))
        if len(eintraege) > 5:
            print("  ... und %d weitere" % (len(eintraege) - 5))

    elif args.aktion == "push":
        from akquise import gmail_imap
        cfg = config.lade_config()
        eintraege, verworfen, unbrauchbar = gmail.warteschlange(
            conn, min_score=args.min_score, prio=args.prio, limit=args.limit,
        )
        if verworfen:
            print("%d mangelhafte Entwürfe verworfen (werden neu getextet)." % len(verworfen))
        if not eintraege:
            print("Nichts zu tun - keine versandfertigen Entwürfe.")
        else:
            print("Lege %d Entwürfe in %s an ..." % (len(eintraege), cfg["gmail"]["adresse"]))
            try:
                ergebnis = gmail_imap.lege_entwuerfe_an(cfg, eintraege, melder=print)
            except gmail_imap.GmailFehler as fehler:
                print("\nGmail-Fehler: %s" % fehler)
                conn.close()
                return 1
            gmail.markiere(conn, ergebnis["angelegt"])
            print("\nAngelegt: %d" % len(ergebnis["angelegt"]))
            for lead_id, grund in ergebnis["uebersprungen"][:10]:
                print("  übersprungen [%s]: %s" % (lead_id, grund))
            if ergebnis["uebersprungen"]:
                # Auch Übersprungene abhaken, wenn sie schon im Postfach liegen.
                schon_da = [i for i, g in ergebnis["uebersprungen"] if "schon" in g]
                gmail.markiere(conn, schon_da)
                print("  (%d davon lagen bereits im Postfach)" % len(schon_da))

    elif args.aktion == "abgleich":
        print("Frage Gmail-Postfach ab ...")
        try:
            k = gmail.abgleich(conn)
        except Exception as fehler:
            print("Gmail-Fehler: %s" % fehler)
            conn.close()
            return 1
        print("  Entwürfe im Postfach:     %d" % k["im_postfach"])
        print("  Gesendete Adressen:       %d" % k["gesendet_gesamt"])
        print("  neu als Entwurf erkannt:  %d" % k["neu_als_entwurf"])
        print("  neu als versendet erkannt:%d (Status -> kontaktiert)" % k["neu_als_gesendet"])

    elif args.aktion == "fertig":
        if not args.ids:
            print("Keine Lead-IDs angegeben: ./spulwerk.py gmail fertig 12 34 56")
        else:
            anzahl = gmail.markiere(conn, args.ids)
            print("%d Leads als 'in Gmail' markiert." % anzahl)

    conn.close()


def cmd_sync(args):
    from akquise import sync
    print("Synchronisiere Leads nach Supabase (Portal) ...")
    try:
        k = sync.synchronisiere(min_score=args.min_score, voll=args.voll)
        print("Fertig: %d Leads übertragen." % k["gesendet"])
    except sync.SyncFehler as fehler:
        print("Sync-Fehler: %s" % fehler, file=sys.stderr)
        return 1


def cmd_web(args):
    from akquise import web
    web.starte(host=args.host, port=args.port)


def baue_parser():
    parser = argparse.ArgumentParser(
        prog="spulwerk.py",
        description="Kundensuche und Kaltakquise-Vorbereitung für Spulwerk.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    unter = parser.add_subparsers(dest="befehl", required=True)

    p = unter.add_parser("init", help="Datenbank und Konfiguration anlegen")
    p.set_defaults(funktion=cmd_init)

    p = unter.add_parser("suche", help="Betriebe über OpenStreetMap finden")
    p.add_argument("--stadt", help="Stadtname (z. B. wien, graz, muenchen)")
    p.add_argument("--kategorien", help="Kommagetrennt, z. B. gastro,hotel,mode")
    p.add_argument("--bbox", help="Eigener Bereich: sued,west,nord,ost")
    p.add_argument("--limit", type=int, help="Max. neue Leads je Kategorie")
    p.add_argument("--nur-website", action="store_true",
                   help="Nur Betriebe mit hinterlegter Website übernehmen")
    p.set_defaults(funktion=cmd_suche)

    p = unter.add_parser("anreichern", help="Websites analysieren, Kontakt/Signale ziehen")
    p.add_argument("--limit", type=int, help="Max. Anzahl Leads")
    p.add_argument("--pause", type=float, default=1.5, help="Sekunden zwischen Seiten")
    p.add_argument("--alle", action="store_true", help="Auch bereits geprüfte erneut")
    p.add_argument("--ignoriere-robots", action="store_true",
                   help="robots.txt ignorieren (nicht empfohlen)")
    p.set_defaults(funktion=cmd_anreichern)

    p = unter.add_parser("bewerten", help="Leads nach Bedarf priorisieren")
    p.add_argument("--min-score", type=int, help="Schwelle für 'qualifiziert'")
    p.set_defaults(funktion=cmd_bewerten)

    p = unter.add_parser("texten", help="Erstansprachen als Entwürfe erzeugen")
    p.add_argument("--min-score", type=int, help="Nur Leads ab diesem Score")
    p.add_argument("--limit", type=int, default=20, help="Max. Anzahl Leads")
    p.add_argument("--kanaele", help="Kommagetrennt: email,dm,telefon")
    p.add_argument("--lead", type=int, help="Nur für diese Lead-ID")
    p.add_argument("--neu-erzeugen", action="store_true",
                   help="Auch Leads betexten, die schon Entwürfe haben")
    p.add_argument("--frisch-seit", metavar="ZEITPUNKT",
                   help="Leads überspringen, die seit diesem Zeitpunkt schon "
                        "getextet wurden (z. B. \"2026-07-25 17:30\") - zum "
                        "Fortsetzen eines abgebrochenen Laufs")
    p.set_defaults(funktion=cmd_texten)

    p = unter.add_parser("liste", help="Leads als Tabelle anzeigen")
    p.add_argument("--status")
    p.add_argument("--kategorie")
    p.add_argument("--ort")
    p.add_argument("--min-score", type=int)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--sortierung", choices=["score", "name", "neu"], default="score")
    p.set_defaults(funktion=cmd_liste)

    p = unter.add_parser("zeige", help="Vollständiges Dossier eines Leads")
    p.add_argument("lead", help="Lead-ID (lokal eine Zahl, in der Cloud eine UUID)")
    p.set_defaults(funktion=cmd_zeige)

    p = unter.add_parser("status", help="Status/Notiz/Wiedervorlage setzen")
    p.add_argument("lead")
    p.add_argument("neuer_status", help="z. B. kontaktiert, antwort, termin, kunde")
    p.add_argument("--notiz")
    p.add_argument("--kanal", help="email, telefon, dm")
    p.add_argument("--wiedervorlage", type=int, metavar="TAGE",
                   help="In X Tagen erneut vorlegen")
    p.set_defaults(funktion=cmd_status)

    p = unter.add_parser("sperren", help="Betrieb/Domain dauerhaft ausschließen")
    p.add_argument("muster", help="Name, Domain oder Teilstring")
    p.add_argument("--grund")
    p.set_defaults(funktion=cmd_sperren)

    p = unter.add_parser("export", help="Entwürfe/Leads exportieren")
    p.add_argument("--eml", action="store_true", help="E-Mail-Entwürfe als .eml")
    p.add_argument("--csv", action="store_true", help="Leads als CSV")
    p.add_argument("--arbeitsliste", action="store_true", help="Markdown-Abhakliste")
    p.add_argument("--instagram", action="store_true",
                   help="Instagram-DM-Liste (Profile + DM-Entwürfe)")
    p.add_argument("--status")
    p.add_argument("--min-score", type=int)
    p.add_argument("--limit", type=int)
    p.set_defaults(funktion=cmd_export)

    p = unter.add_parser("uebersicht", help="Pipeline und Wiedervorlagen")
    p.set_defaults(funktion=cmd_uebersicht)

    p = unter.add_parser("pipeline", help="suche + anreichern + bewerten + texten")
    p.add_argument("--stadt")
    p.add_argument("--kategorien")
    p.add_argument("--bbox")
    p.add_argument("--limit", type=int)
    p.add_argument("--nur-website", action="store_true")
    p.add_argument("--pause", type=float, default=1.5)
    p.add_argument("--alle", action="store_true")
    p.add_argument("--ignoriere-robots", action="store_true")
    p.add_argument("--min-score", type=int)
    p.add_argument("--kanaele")
    p.add_argument("--lead", type=int)
    p.add_argument("--neu-erzeugen", action="store_true")
    p.set_defaults(funktion=cmd_pipeline)

    p = unter.add_parser("sweep", help="Kompletter Durchlauf über alle Branchen (idempotent)")
    p.add_argument("--stadt", help="Standard: wien")
    p.add_argument("--kategorien", help="Kommagetrennt; Standard: alle 12 Branchen")
    p.add_argument("--enrich-limit", type=int, help="Max. Websites pro Lauf anreichern")
    p.add_argument("--text-limit", type=int, default=40, help="Max. Leads pro Lauf texten")
    p.add_argument("--budget", type=float, metavar="ANTEIL",
                   help="Groq-Tageslimit bis zu diesem Anteil ausschöpfen (z. B. 0.85). "
                        "Überschreibt --text-limit.")
    p.add_argument("--nur-website", action="store_true")
    p.add_argument("--ohne-texten", action="store_true", help="Nur suchen/bewerten, nicht texten")
    p.add_argument("--rotation", type=int, default=0, metavar="ANZAHL",
                   help="Nur so viele Branchen je Nacht durchsuchen (rotiert "
                        "über die Woche). 0 = alle auf einmal.")
    p.add_argument("--max-minuten", type=int, default=0, metavar="MINUTEN",
                   help="Laufzeitgrenze; danach nur noch die kurzen Schritte")
    p.add_argument("--dann-schlafen", action="store_true",
                   help="Den Mac am Ende wieder schlafen legen (pmset sleepnow)")
    p.add_argument("--quelle", default="mac",
                   help="Wer läuft hier - steht so im Laufprotokoll (mac/github)")
    p.set_defaults(funktion=cmd_sweep)

    p = unter.add_parser("zeitplan", help="Täglichen Sweep automatisch einrichten (macOS)")
    p.add_argument("aktion", choices=["einrichten", "entfernen", "status"])
    p.add_argument("--stadt", default="wien")
    p.add_argument("--stunde", type=int, default=7, help="Startstunde 0-23 (Standard 7)")
    p.add_argument("--minute", type=int, default=0)
    p.add_argument("--enrich-limit", type=int, default=200,
                   help="Max. Websites pro Nachschub-Lauf (Standard 200 ≈ 7 Minuten)")
    p.add_argument("--budget", type=float, default=0.85,
                   help="Groq-Tageslimit-Anteil pro Nachschub-Lauf (Standard 0.85 = 85%%)")
    p.add_argument("--rotation", type=int, default=4, metavar="ANZAHL",
                   help="Branchen je Nacht (Standard 4, rotiert über die Woche)")
    p.add_argument("--max-minuten", type=int, default=40,
                   help="Laufzeitgrenze je Nacht (Standard 40 Minuten)")
    p.add_argument("--wach-bleiben", action="store_true",
                   help="Mac nach dem Lauf NICHT wieder schlafen legen")
    p.add_argument("--ohne-abholdienst", action="store_true",
                   help="Den 5-Minuten-Dienst für den Portal-Knopf nicht einrichten")
    p.set_defaults(funktion=cmd_zeitplan)

    p = unter.add_parser("wache",
                         help="Im Portal angeforderten Lauf abholen und starten")
    p.add_argument("--ausfuehrlich", action="store_true", help="Auch melden, wenn nichts anliegt")
    p.add_argument("--rotation", type=int, default=4)
    p.add_argument("--max-minuten", type=int, default=40)
    p.add_argument("--enrich-limit", type=int, default=200)
    p.add_argument("--budget", type=float, default=0.85)
    p.set_defaults(funktion=cmd_wache)

    p = unter.add_parser("instagram",
                         help="Verschickte Instagram-DMs abhaken (ohne IDs: offene anzeigen)")
    p.add_argument("ids", nargs="*", help="Lead-IDs, deren DM raus ist")
    p.add_argument("--min-score", type=int, default=55)
    p.set_defaults(funktion=cmd_instagram)

    p = unter.add_parser("saeubern",
                         help="Alle Entwürfe nachträglich prüfen (Jargon, falsche Domains)")
    p.set_defaults(funktion=cmd_saeubern)

    p = unter.add_parser(
        "gmail",
        help="E-Mail-Entwürfe ins Gmail-Postfach legen (push) bzw. abgleichen",
        description="warteschlange = Datei für den Claude-Task schreiben; "
                    "push = Entwürfe selbst per IMAP anlegen; "
                    "abgleich = Postfach lesen (was liegt im Entwurf, was ist raus); "
                    "fertig = Lead-IDs abhaken; status = Zahlen anzeigen")
    p.add_argument("aktion",
                   choices=["warteschlange", "push", "abgleich", "fertig", "status"])
    p.add_argument("ids", nargs="*", help="Lead-IDs für 'fertig'")
    p.add_argument("--min-score", type=int, default=55,
                   help="Ab diesem Score (Standard 55 = Prio A und B)")
    p.add_argument("--prio", choices=["A", "B"], help="Nur A- oder nur B-Leads")
    p.add_argument("--limit", type=int, help="Höchstens so viele Entwürfe je Lauf")
    p.add_argument("--datei", help="Zieldatei für die Warteschlange")
    p.add_argument("--erneut", action="store_true",
                   help="Auch Leads aufnehmen, die schon einen Gmail-Entwurf haben")
    p.set_defaults(funktion=cmd_gmail)

    p = unter.add_parser("pruefe", help="API-Key finden und Verbindung zu Claude testen")
    p.set_defaults(funktion=cmd_pruefe)

    p = unter.add_parser("sync", help="Leads nach Supabase (Portal) übertragen")
    p.add_argument("--min-score", type=int, help="Nur Leads ab diesem Score (Standard 40)")
    p.add_argument("--voll", action="store_true", help="Alles neu übertragen (ignoriert letzten Sync)")
    p.set_defaults(funktion=cmd_sync)

    p = unter.add_parser("web", help="Lokales Dashboard im Browser starten")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8733)
    p.set_defaults(funktion=cmd_web)

    return parser


def main(argv=None):
    parser = baue_parser()
    args = parser.parse_args(argv)
    try:
        args.funktion(args)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 1
    except Exception as fehler:
        print("Fehler: %s" % fehler, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
