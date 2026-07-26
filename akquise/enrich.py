"""Anreicherung: oeffentliche Website eines Leads analysieren.

Gesucht wird, was fuer die Einschaetzung des Bedarfs relevant ist:
Kontaktadresse, Social-Profile, vorhandene Videos, Bildmenge,
technischer Zustand der Seite. Es wird nur die oeffentliche Startseite
plus Impressum/Kontakt gelesen, robots.txt wird respektiert.
"""

import gzip
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

from . import config, db

EMAIL_MUSTER = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
TELEFON_MUSTER = re.compile(r"(\+43|\+49|0)[\d\s/().-]{7,}\d")

# Adressen, die typischerweise nicht persoenlich gelesen werden
GENERISCHE_POSTFAECHER = ("noreply", "no-reply", "postmaster", "abuse", "webmaster")

# Platzhalter-Adressen aus Themes/Vorlagen - nie echte Kontakte.
PLATZHALTER_ADRESSEN = (
    "@example.", "@xyz.", "@domain.", "@test.", "@email.", "@yourdomain",
    "@sentry.", "@wixpress.", "@company.", "@mydomain", "@firma.",
    "abc@", "test@", "your@", "email@", "name@", "mail@example",
    "mustermann", "max.mustermann", "vorname.nachname", "user@",
)

VIDEO_SIGNALE = (
    "youtube.com/embed", "player.vimeo.com", "<video", "wistia", "youtu.be",
    "vimeo.com/", "mp4", "showreel", "imagefilm",
)
UNTERSEITEN = ("/impressum", "/kontakt", "/contact", "/ueber-uns", "/about")


class SeitenParser(HTMLParser):
    """Zieht Links, Bilder, Videos und Meta-Angaben aus einer HTML-Seite."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.links = []
        self.bilder = 0
        self.videos = 0
        self.iframes = []
        self.titel = ""
        self.beschreibung = ""
        self.hat_viewport = False
        self._in_titel = False

    def handle_starttag(self, tag, attrs):
        attribute = dict(attrs)
        if tag == "a" and attribute.get("href"):
            self.links.append(attribute["href"])
        elif tag in ("img", "source"):
            self.bilder += 1
        elif tag == "video":
            self.videos += 1
        elif tag == "iframe" and attribute.get("src"):
            self.iframes.append(attribute["src"])
        elif tag == "title":
            self._in_titel = True
        elif tag == "meta":
            name = (attribute.get("name") or attribute.get("property") or "").lower()
            if name == "viewport":
                self.hat_viewport = True
            elif name in ("description", "og:description"):
                self.beschreibung = (attribute.get("content") or "")[:400]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_titel = False

    def handle_data(self, daten):
        if self._in_titel:
            self.titel += daten.strip()


def _hole(url, timeout=15):
    """Laedt eine URL und gibt (text, finale_url) zurueck; None bei Fehler."""
    kontext = ssl.create_default_context()
    kontext.check_hostname = False
    kontext.verify_mode = ssl.CERT_NONE  # viele KMU-Seiten haben schlechte Zertifikate
    anfrage = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "de-AT,de;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout, context=kontext) as antwort:
            roh = antwort.read(900_000)
            if antwort.headers.get("Content-Encoding") == "gzip":
                try:
                    roh = gzip.decompress(roh)
                except OSError:
                    pass
            zeichensatz = antwort.headers.get_content_charset() or "utf-8"
            return roh.decode(zeichensatz, errors="replace"), antwort.geturl()
    except Exception:
        return None, None


def _robots_erlaubt(basis_url, beachten=True):
    if not beachten:
        return True
    teile = urllib.parse.urlparse(basis_url)
    robots_url = "%s://%s/robots.txt" % (teile.scheme, teile.netloc)
    parser = urllib.robotparser.RobotFileParser()
    try:
        parser.set_url(robots_url)
        parser.read()
        return parser.can_fetch(config.USER_AGENT, basis_url)
    except Exception:
        return True  # kein robots.txt erreichbar -> Standardverhalten: erlaubt


def _social(links, muster):
    for link in links:
        klein = link.lower()
        if muster in klein and "sharer" not in klein and "intent" not in klein:
            if link.startswith("//"):
                return "https:" + link
            if link.startswith("http"):
                return link.split("?")[0]
    return None


def _emails(text, links):
    treffer = set()
    for link in links:
        if link.lower().startswith("mailto:"):
            treffer.add(link[7:].split("?")[0].strip())
    for adresse in EMAIL_MUSTER.findall(text):
        treffer.add(adresse)
    sauber = [
        a for a in treffer
        if not any(p in a.lower() for p in GENERISCHE_POSTFAECHER)
        and not any(p in a.lower() for p in PLATZHALTER_ADRESSEN)
        and not a.lower().endswith((".png", ".jpg", ".gif", ".webp"))
    ]
    # Persoenliche Adressen zuerst, dann office@/info@
    sauber.sort(key=lambda a: (a.lower().startswith(("info@", "office@", "kontakt@")), len(a)))
    return sauber


def analysiere_website(url, robots_beachten=True):
    """Liest Startseite + Impressum/Kontakt und gibt ein Befund-Dict zurueck."""
    befund = {
        "erreichbar": False,
        "titel": None,
        "beschreibung": None,
        "emails": [],
        "telefon": None,
        "instagram": None,
        "facebook": None,
        "tiktok": None,
        "linkedin": None,
        "youtube": None,
        "anzahl_bilder": 0,
        "hat_video": False,
        "video_quellen": [],
        "mobil_optimiert": False,
        "shop": False,
        "geprueft_am": db.jetzt(),
    }

    if not _robots_erlaubt(url, robots_beachten):
        befund["hinweis"] = "robots.txt verbietet das Auslesen - uebersprungen"
        return befund

    text, finale_url = _hole(url)
    if not text:
        return befund

    befund["erreichbar"] = True
    parser = SeitenParser()
    try:
        parser.feed(text)
    except Exception:
        pass

    klein = text.lower()
    befund["titel"] = (parser.titel or "").strip()[:200] or None
    befund["beschreibung"] = (parser.beschreibung or "").strip() or None
    befund["anzahl_bilder"] = parser.bilder
    befund["mobil_optimiert"] = parser.hat_viewport
    # Nur echte Kaufsignale zaehlen. "shopify"/"woocommerce" allein steckt in
    # vielen Themes, ohne dass es einen Shop gibt - das hat Architekturbueros
    # faelschlich einen Onlineshop angedichtet.
    befund["shop"] = any(
        s in klein for s in ("warenkorb", "in den warenkorb", "add to cart",
                             "zum shop", "/cart", "/checkout", "jetzt kaufen")
    )

    quellen = " ".join(parser.iframes).lower()
    gefundene_signale = [s for s in VIDEO_SIGNALE if s in klein or s in quellen]
    befund["hat_video"] = bool(parser.videos or gefundene_signale)
    befund["video_quellen"] = gefundene_signale[:5]

    absolute_links = []
    for link in parser.links:
        if link.startswith(("http", "//", "mailto:")):
            absolute_links.append(link)
        elif finale_url:
            absolute_links.append(urllib.parse.urljoin(finale_url, link))

    befund["instagram"] = _social(absolute_links, "instagram.com")
    befund["facebook"] = _social(absolute_links, "facebook.com")
    befund["tiktok"] = _social(absolute_links, "tiktok.com")
    befund["linkedin"] = _social(absolute_links, "linkedin.com")
    befund["youtube"] = _social(absolute_links, "youtube.com/@") or _social(
        absolute_links, "youtube.com/channel"
    )

    befund["emails"] = _emails(text, absolute_links)
    treffer = TELEFON_MUSTER.search(re.sub(r"<[^>]+>", " ", text))
    if treffer:
        befund["telefon"] = treffer.group(0).strip()[:40]

    # Impressum/Kontakt nachladen, falls dort erst die Adresse steht
    if not befund["emails"] and finale_url:
        for pfad in UNTERSEITEN:
            unterseite = urllib.parse.urljoin(finale_url, pfad)
            unter_text, _ = _hole(unterseite, timeout=10)
            if not unter_text:
                continue
            unter_parser = SeitenParser()
            try:
                unter_parser.feed(unter_text)
            except Exception:
                pass
            befund["emails"] = _emails(unter_text, unter_parser.links)
            if befund["emails"]:
                befund["quelle_email"] = unterseite
                break
            time.sleep(0.4)

    return befund


def reichere_an(limit=None, pause=1.5, robots_beachten=True, alle=False,
                ausgabe=print):
    """Analysiert Websites aller (noch nicht angereicherten) Leads."""
    conn = db.verbinde()
    kandidaten = db.leads(
        conn, nur_unangereichert=not alle, limit=limit, sortierung="neu"
    )
    kandidaten = [k for k in kandidaten if k["website"]]

    kennzahlen = {"geprueft": 0, "erreichbar": 0, "emails": 0, "instagram": 0}
    for lead in kandidaten:
        ausgabe("  [%d] %s -> %s" % (lead["id"], lead["name"][:38], lead["website"]))
        befund = analysiere_website(lead["website"], robots_beachten)
        kennzahlen["geprueft"] += 1

        aktualisierung = {
            "recherche": json.dumps(befund, ensure_ascii=False),
            "angereichert_am": db.jetzt(),
        }
        if befund["erreichbar"]:
            kennzahlen["erreichbar"] += 1
        if befund["emails"] and not lead["email"]:
            aktualisierung["email"] = befund["emails"][0]
            kennzahlen["emails"] += 1
        if befund["instagram"] and not lead["instagram"]:
            aktualisierung["instagram"] = befund["instagram"]
            kennzahlen["instagram"] += 1
        if befund["facebook"] and not lead["facebook"]:
            aktualisierung["facebook"] = befund["facebook"]
        if befund["telefon"] and not lead["telefon"]:
            aktualisierung["telefon"] = befund["telefon"]

        with conn:
            db.aktualisiere_lead(conn, lead["id"], **aktualisierung)
        time.sleep(pause)

    conn.close()
    return kennzahlen
