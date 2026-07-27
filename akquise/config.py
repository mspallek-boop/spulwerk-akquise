"""Konfiguration, Stammdaten und Zielgruppen-Definitionen."""

import json
import re
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DATEN_DIR = BASE_DIR / "daten"
DB_PATH = DATEN_DIR / "akquise.db"
ENTWURF_DIR = BASE_DIR / "entwuerfe"
EXPORT_DIR = BASE_DIR / "export"

USER_AGENT = (
    "SpulwerkAkquise/1.0 (Recherche-Tool, Kontakt: hallo@spulwerk.com; "
    "https://spulwerk.com)"
)

# Bounding-Boxen: (sued, west, nord, ost)
STAEDTE = {
    "wien": (48.1150, 16.1820, 48.3230, 16.5770),
    "graz": (46.9950, 15.3400, 47.1400, 15.5400),
    "linz": (48.2350, 14.2200, 48.3600, 14.3800),
    "salzburg": (47.7500, 12.9800, 47.8600, 13.1200),
    "innsbruck": (47.2200, 11.3000, 47.3100, 11.4800),
    "klagenfurt": (46.5900, 14.2300, 46.6600, 14.3800),
    "st-poelten": (48.1600, 15.5600, 48.2400, 15.6800),
    "wiener-neustadt": (47.7800, 16.1800, 47.8500, 16.3000),
    "muenchen": (48.0600, 11.3600, 48.2500, 11.7200),
    "berlin": (52.3800, 13.0900, 52.6800, 13.7600),
}

# Zielbranchen mit OSM-Tags und Grundgewicht (0-25) fuer die Bewertung.
# Das Gewicht bildet ab, wie gut die Branche zu Video/Foto passt.
KATEGORIEN = {
    "gastro": {
        "label": "Gastronomie (Restaurant, Cafe, Bar)",
        "gewicht": 22,
        "tags": [
            ("amenity", "restaurant"),
            ("amenity", "cafe"),
            ("amenity", "bar"),
            ("amenity", "pub"),
            ("amenity", "ice_cream"),
        ],
    },
    "hotel": {
        "label": "Hotellerie & Beherbergung",
        "gewicht": 25,
        "tags": [
            ("tourism", "hotel"),
            ("tourism", "guest_house"),
            ("tourism", "apartment"),
            ("tourism", "hostel"),
        ],
    },
    "immobilien": {
        "label": "Immobilienmakler & Bautraeger",
        "gewicht": 25,
        "tags": [
            ("office", "estate_agent"),
            ("office", "property_management"),
        ],
    },
    "mode": {
        "label": "Mode, Schmuck & Concept Stores",
        "gewicht": 24,
        "tags": [
            ("shop", "clothes"),
            ("shop", "boutique"),
            ("shop", "shoes"),
            ("shop", "jewelry"),
            ("shop", "bag"),
            ("shop", "watches"),
        ],
    },
    "fitness": {
        "label": "Fitness, Yoga & Sportstudios",
        "gewicht": 21,
        "tags": [
            ("leisure", "fitness_centre"),
            ("leisure", "sports_centre"),
            ("shop", "sports"),
        ],
    },
    "beauty": {
        "label": "Beauty, Friseur & Kosmetik",
        "gewicht": 20,
        "tags": [
            ("shop", "hairdresser"),
            ("shop", "beauty"),
            ("shop", "cosmetics"),
            ("leisure", "spa"),
        ],
    },
    "event": {
        "label": "Event-Locations & Nachtgastronomie",
        "gewicht": 23,
        "tags": [
            ("amenity", "events_venue"),
            ("amenity", "nightclub"),
            ("amenity", "conference_centre"),
        ],
    },
    "kultur": {
        "label": "Kultur, Museen & Buehnen",
        "gewicht": 18,
        "tags": [
            ("tourism", "museum"),
            ("amenity", "theatre"),
            ("amenity", "arts_centre"),
            ("tourism", "gallery"),
        ],
    },
    "auto": {
        "label": "Autohaus & Fahrzeughandel",
        "gewicht": 22,
        "tags": [
            ("shop", "car"),
            ("shop", "motorcycle"),
        ],
    },
    "handwerk": {
        "label": "Handwerk & Manufaktur",
        "gewicht": 16,
        "tags": [
            ("shop", "interior_decoration"),
            ("shop", "furniture"),
            ("craft", "carpenter"),
            ("craft", "goldsmith"),
            ("craft", "brewery"),
        ],
    },
    "gesundheit": {
        "label": "Praxen, Kliniken & Gesundheit",
        "gewicht": 15,
        "tags": [
            ("amenity", "clinic"),
            ("amenity", "dentist"),
            ("healthcare", "physiotherapist"),
        ],
    },
    "buero": {
        "label": "Agenturen & Dienstleister",
        "gewicht": 14,
        # office=lawyer ist am 27.07.2026 rausgeflogen: ein Wiener Anwalt hat
        # auf eine Erstansprache mit dem Hinweis auf § 174 Abs 3 TKG 2021
        # geantwortet. Rechtsberufe wissen am genauesten, was unerlaubte
        # Direktwerbung ist - die gehoeren nicht in die Liste. RECHTSBERUFE
        # weiter unten faengt, was ueber office=company oder =consulting
        # trotzdem noch hereinkaeme (Kanzleien taggen sich uneinheitlich).
        "tags": [
            ("office", "company"),
            ("office", "financial"),
            ("office", "consulting"),
        ],
    },
    # Eigene Branche statt unter "buero" mitzulaufen: Marlon und Leander
    # studieren beide Architektur im Master - fachlich die naheliegendste
    # Zielgruppe, und im Anschreiben ein echtes Argument.
    "architektur": {
        "label": "Architekturbüros & Planung",
        "gewicht": 25,
        "tags": [
            ("office", "architect"),
            ("craft", "architect"),
            ("office", "engineer"),
        ],
    },
}

# Rechts-, Steuer- und Kammerberufe: gar nicht erst in die Datenbank aufnehmen.
#
# Anlass war der 27.07.2026: eine Erstansprache an eine Wiener Kanzlei kam mit
# dem Hinweis auf § 174 Abs 3 TKG 2021 zurueck (unverlangte Direktwerbung per
# E-Mail ist auch im B2B einwilligungspflichtig). Diese Berufsgruppen kennen
# die Rechtslage berufsbedingt genau und haben die Mittel, darauf zu reagieren.
#
# Geprueft wird gegen Name, E-Mail und Website - Kanzleien taggen sich in OSM
# uneinheitlich, ueber office=company rutschen sie sonst doch herein.
# Auf WORTGRENZEN geprueft, nicht als Teilstring. Ein erster Versuch mit
# schlichtem "steht drin" war unbrauchbar: "ra@" traf aurora@, nora@ und
# ancora@, "kanzlei" traf die "K. u. K. Bierkanzlei" (ein Wirtshaus) und
# "kammer" haette die Josefstaedter Kammerspiele erwischt. 17 Treffer,
# davon 17 Fehlalarme.
RECHTSBERUFE = re.compile(r"""\b(
      rechtsanw\w*            # Rechtsanwalt, Rechtsanwaelte, Rechtsanwaltskanzlei
    | anwalt | anw[äa]lt\w* | anwaelt\w* | anwaltskanzlei
    | advokat\w*
    | notar | notarin | notare | notariat\w*     # nicht Notarzt: Wortgrenze
    | steuerberat\w* | steuerkanzlei
    | wirtschaftspr[üu]f\w* | wirtschaftstreuh\w*
    | patentanw\w*
    | kanzlei                 # allein stehend; "Bierkanzlei" hat keine Grenze
    | rechtsanwaltskammer | notariatskammer | wirtschaftskammer
    | arbeiterkammer | [äa]rztekammer | apothekerkammer
    | ziviltechnikerkammer
    # Auch die OSM-Herkunft pruefen: "DDr. Ciresa" traegt kein Stichwort im
    # Namen und kam allein ueber office=lawyer herein.
    | lawyer | notary | tax_advisor
)\b""", re.IGNORECASE | re.VERBOSE)


def ist_rechtsberuf(lead):
    """Anwalt, Notar, Steuerberater oder Kammer? Prueft Name, Adresse und die
    OSM-Herkunft eines Leads."""
    heu = " ".join(str(lead.get(f) or "")
                   for f in ("name", "email", "website", "branche"))
    return bool(RECHTSBERUFE.search(heu))


# Begriffe, die auf einen Wettbewerber hindeuten -> nicht anschreiben.
WETTBEWERBER_BEGRIFFE = [
    "filmproduktion",
    "videoproduktion",
    "fotostudio",
    "photostudio",
    "werbeagentur",
    "mediaagentur",
    "media agentur",
    "filmstudio",
    "produktionsfirma",
    "content agency",
    "creative agency",
    "photographer",
    "fotograf",
    "videograf",
]

# Supabase (Portal-Datenbank) — Service-Key liegt im Portal-Ordner (.secrets).
SUPABASE_SERVICE_KEY_DATEI = (
    BASE_DIR.parent / "spulwerk.com" / "website" / "myspulwerk"
    / ".secrets" / "supabase-service-key"
)


def supabase_service_key(config):
    # In der Cloud gibt es keine Schlüsseldateien - dort kommt der Wert als
    # Umgebungsvariable (GitHub-Secret) herein.
    aus_env = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if aus_env:
        return aus_env
    pfad = config.get("supabase", {}).get("service_key_datei")
    datei = Path(pfad) if pfad else SUPABASE_SERVICE_KEY_DATEI
    if datei.exists():
        inhalt = datei.read_text(encoding="utf-8").strip()
        if inhalt:
            return inhalt
    return ""


def gmail_zugang(config):
    """(Adresse, App-Passwort) fuer das Gmail-Postfach - oder ("", "").

    Das App-Passwort steht nie in der config.json, sondern in einer eigenen
    Datei (.secrets/, per .gitignore geschuetzt). Erlaubte Formate:
      nur das Passwort            -> Adresse kommt aus config.json
      adresse:passwort            -> beides aus der Datei
    Leerzeichen im App-Passwort (Google zeigt es in Viererblocks) werden
    entfernt.
    """
    daten = config.get("gmail", {})
    adresse = (daten.get("adresse") or "").strip()
    pfad = daten.get("app_passwort_datei") or ".secrets/gmail-app-passwort"
    datei = Path(pfad)
    if not datei.is_absolute():
        datei = BASE_DIR / datei
    aus_env = os.environ.get("GMAIL_APP_PASSWORT", "").strip()
    inhalt = aus_env
    if not inhalt and datei.exists():
        inhalt = datei.read_text(encoding="utf-8").strip()
    if not inhalt:
        return adresse, ""
    if ":" in inhalt and "@" in inhalt.split(":", 1)[0]:
        adresse, inhalt = inhalt.split(":", 1)
    return adresse.strip(), inhalt.replace(" ", "").strip()


DEFAULT_CONFIG = {
    "firma": {
        "name": "Spulwerk",
        "website": "https://spulwerk.com",
        "ort": "Wien",
        "absender": "Marlon Spallek",
        "partner": "Leander Höltershinken",
        "email": "hallo@spulwerk.com",
        "telefon": "",
        "positionierung": (
            "Kreativstudio aus Wien für Video und Fotografie. "
            "Retro-Seele, digital gedreht: filmische Bildsprache, moderne 4K-Produktion."
        ),
        # Fachlicher Hintergrund - taucht in den Ansprachen auf, weil er echtes
        # Vertrauen schafft: wer Räume plant, filmt sie auch anders.
        "hintergrund": (
            "Marlon und Leander studieren beide Architektur im Master. "
            "Sie haben ein geschultes Auge für Raum, Licht, Material und "
            "Proportion - besonders hilfreich bei Architektur, Immobilien, "
            "Innenausbau, Hotellerie und Handwerk."
        ),
        "leistungen": [
            "Reels & Social Content (Lieferung in 48 Stunden)",
            "Imagefilm & Unternehmensvideo",
            "Fotoshooting (Produkt, Location, Team)",
            "Post-Produktion, Schnitt und Color Grading",
        ],
        "referenzen": [],
        "anrede": "Sie",
    },
    "akquise": {
        "stadt": "wien",
        "kategorien": ["gastro", "hotel", "immobilien", "mode"],
        "min_score": 55,
        "text_min_score": 40,
        "wiedervorlage_tage": 7,
        "max_kontaktversuche": 2,
        "pause_sekunden": 1.5,
        "robots_beachten": True,
    },
    "llm": {
        "aktiv": True,
        "anbieter": "groq",
        "modell": "openai/gpt-oss-120b",
        "max_tokens": 1600,
    },
    "supabase": {
        "url": "https://usadqelhwobcvemxvgkt.supabase.co",
        "min_score_sync": 40,
    },
    # Gmail-Postfach fuer die Entwuerfe. Das App-Passwort steht bewusst NICHT
    # hier, sondern in der Datei unter app_passwort_datei.
    "gmail": {
        "adresse": "spulwerk.com@gmail.com",
        "app_passwort_datei": ".secrets/gmail-app-passwort",
        "imap_server": "imap.gmail.com",
    },
}


def _merge(basis, custom):
    """Rekursives Zusammenfuehren von Default- und Nutzerkonfiguration."""
    ergebnis = dict(basis)
    for schluessel, wert in (custom or {}).items():
        if isinstance(wert, dict) and isinstance(ergebnis.get(schluessel), dict):
            ergebnis[schluessel] = _merge(ergebnis[schluessel], wert)
        else:
            ergebnis[schluessel] = wert
    return ergebnis


def lade_config():
    """Liest config.json und ergaenzt fehlende Werte aus den Defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as datei:
            return _merge(DEFAULT_CONFIG, json.load(datei))
    return dict(DEFAULT_CONFIG)


def schreibe_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as datei:
        json.dump(config, datei, indent=2, ensure_ascii=False)


# Pro Anbieter: passende Umgebungsvariable und lokale Key-Datei.
ANBIETER = {
    "anthropic": {"env": "ANTHROPIC_API_KEY", "datei": ".anthropic_key"},
    "google": {"env": "GEMINI_API_KEY", "datei": ".gemini_key"},
    "groq": {"env": "GROQ_API_KEY", "datei": ".groq_key"},
}


def anbieter(config):
    return (config["llm"].get("anbieter") or "anthropic").lower()


def key_datei(config):
    return BASE_DIR / ANBIETER.get(anbieter(config), ANBIETER["anthropic"])["datei"]


def api_key(config):
    """Findet den API-Key des aktiven Anbieters in dieser Reihenfolge:
    1. Umgebungsvariable (z. B. GEMINI_API_KEY / ANTHROPIC_API_KEY)
    2. lokale Datei (z. B. .gemini_key / .anthropic_key; per .gitignore geschützt)
    3. Feld llm.api_key in config.json
    Rueckgabe: der Key als String oder "".
    """
    meta = ANBIETER.get(anbieter(config), ANBIETER["anthropic"])
    env_name = config["llm"].get("api_key_env") or meta["env"]
    aus_env = os.environ.get(env_name, "")
    if aus_env.strip():
        return aus_env.strip()
    datei = BASE_DIR / meta["datei"]
    if datei.exists():
        inhalt = datei.read_text(encoding="utf-8").strip()
        if inhalt:
            return inhalt
    aus_config = (config["llm"].get("api_key") or "").strip()
    if aus_config:
        return aus_config
    return ""


def stelle_verzeichnisse_bereit():
    for pfad in (DATEN_DIR, ENTWURF_DIR, EXPORT_DIR):
        pfad.mkdir(parents=True, exist_ok=True)
