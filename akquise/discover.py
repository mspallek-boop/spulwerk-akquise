"""Lead-Recherche ueber die Overpass-API von OpenStreetMap.

OpenStreetMap ist offen lizenziert (ODbL), kostenlos und ohne API-Key nutzbar.
Wir holen dort oeffentlich hinterlegte Betriebsdaten: Name, Adresse, Website,
Telefon. Das ersetzt das manuelle Durchklicken von Branchenverzeichnissen.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db

OVERPASS_SERVER = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def _bbox(stadt, eigene_bbox=None):
    if eigene_bbox:
        teile = [float(t.strip()) for t in eigene_bbox.split(",")]
        if len(teile) != 4:
            raise ValueError("bbox braucht 4 Werte: sued,west,nord,ost")
        return tuple(teile)
    schluessel = stadt.lower().strip()
    if schluessel not in config.STAEDTE:
        raise ValueError(
            "Unbekannte Stadt '%s'. Bekannt: %s (oder --bbox nutzen)"
            % (stadt, ", ".join(sorted(config.STAEDTE)))
        )
    return config.STAEDTE[schluessel]


def baue_query(kategorien, bbox, timeout=120):
    sued, west, nord, ost = bbox
    bereich = "(%s,%s,%s,%s)" % (sued, west, nord, ost)
    zeilen = []
    for kategorie in kategorien:
        if kategorie not in config.KATEGORIEN:
            raise ValueError(
                "Unbekannte Kategorie '%s'. Bekannt: %s"
                % (kategorie, ", ".join(sorted(config.KATEGORIEN)))
            )
        for schluessel, wert in config.KATEGORIEN[kategorie]["tags"]:
            for typ in ("node", "way"):
                zeilen.append('  %s["%s"="%s"]%s;' % (typ, schluessel, wert, bereich))
    return "[out:json][timeout:%d];\n(\n%s\n);\nout center tags;" % (
        timeout, "\n".join(zeilen),
    )


def frage_overpass(query, versuche=2):
    letzter_fehler = None
    for server in OVERPASS_SERVER:
        for versuch in range(versuche):
            try:
                daten = urllib.parse.urlencode({"data": query}).encode("utf-8")
                anfrage = urllib.request.Request(
                    server, data=daten, headers={"User-Agent": config.USER_AGENT}
                )
                with urllib.request.urlopen(anfrage, timeout=180) as antwort:
                    return json.loads(antwort.read().decode("utf-8"))
            except Exception as fehler:  # Netz, Rate-Limit, Serverwartung
                letzter_fehler = fehler
                time.sleep(3 * (versuch + 1))
    raise RuntimeError("Overpass nicht erreichbar: %s" % letzter_fehler)


def _tag(tags, *namen):
    for name in namen:
        wert = tags.get(name)
        if wert:
            return wert.strip()
    return None


def _normalisiere_website(wert):
    if not wert:
        return None
    wert = wert.strip().split()[0]
    if wert.startswith("www."):
        wert = "http://" + wert
    if not wert.startswith(("http://", "https://")):
        if "." not in wert:
            return None
        wert = "http://" + wert
    return wert.rstrip("/")


def _instagram(tags):
    for schluessel in ("contact:instagram", "instagram", "brand:instagram"):
        wert = tags.get(schluessel)
        if wert:
            wert = wert.strip()
            if wert.startswith("http"):
                return wert
            return "https://instagram.com/" + wert.lstrip("@/")
    return None


def _branche(tags):
    for schluessel in ("amenity", "shop", "tourism", "office", "leisure", "craft",
                       "healthcare"):
        if tags.get(schluessel):
            return "%s=%s" % (schluessel, tags[schluessel])
    return None


def element_zu_lead(element, kategorie):
    tags = element.get("tags") or {}
    name = _tag(tags, "name", "operator", "brand")
    if not name:
        return None

    strasse = _tag(tags, "addr:street")
    hausnummer = _tag(tags, "addr:housenumber")
    if strasse and hausnummer:
        strasse = "%s %s" % (strasse, hausnummer)

    mittelpunkt = element.get("center") or {}
    return {
        "quelle": "osm",
        "quelle_id": "%s/%s" % (element.get("type"), element.get("id")),
        "name": name,
        "kategorie": kategorie,
        "branche": _branche(tags),
        "strasse": strasse,
        "plz": _tag(tags, "addr:postcode"),
        "ort": _tag(tags, "addr:city"),
        "lat": element.get("lat") or mittelpunkt.get("lat"),
        "lon": element.get("lon") or mittelpunkt.get("lon"),
        "website": _normalisiere_website(
            _tag(tags, "website", "contact:website", "url")
        ),
        "telefon": _tag(tags, "phone", "contact:phone", "contact:mobile"),
        "email": _tag(tags, "email", "contact:email"),
        "instagram": _instagram(tags),
        "facebook": _tag(tags, "contact:facebook", "facebook"),
    }


def _ist_wettbewerber(name):
    klein = (name or "").lower()
    return any(begriff in klein for begriff in config.WETTBEWERBER_BEGRIFFE)


def finde(kategorien, stadt="wien", eigene_bbox=None, limit=None,
          nur_mit_website=False, ausgabe=print):
    """Sucht Betriebe und legt sie als Leads an. Rueckgabe: Kennzahlen-Dict."""
    bbox = _bbox(stadt, eigene_bbox)
    conn = db.verbinde()
    kennzahlen = {"gefunden": 0, "neu": 0, "bekannt": 0, "uebersprungen": 0}

    for kategorie in kategorien:
        ausgabe("  Suche Kategorie '%s' in %s ..." % (kategorie, stadt))
        query = baue_query([kategorie], bbox)
        antwort = frage_overpass(query)
        elemente = antwort.get("elements", [])
        ausgabe("    %d Objekte von OpenStreetMap erhalten" % len(elemente))

        gesehen = set()
        anzahl_kategorie = 0
        with conn:
            for element in elemente:
                lead = element_zu_lead(element, kategorie)
                if not lead:
                    continue
                kennzahlen["gefunden"] += 1

                schluessel = (lead["name"].lower(), lead.get("strasse") or "")
                if schluessel in gesehen:
                    continue
                gesehen.add(schluessel)

                if _ist_wettbewerber(lead["name"]):
                    kennzahlen["uebersprungen"] += 1
                    continue
                if nur_mit_website and not lead["website"]:
                    kennzahlen["uebersprungen"] += 1
                    continue

                lead_id, war_neu = db.speichere_lead(conn, lead)
                if war_neu:
                    kennzahlen["neu"] += 1
                    anzahl_kategorie += 1
                else:
                    kennzahlen["bekannt"] += 1

                if limit and anzahl_kategorie >= limit:
                    break
        time.sleep(1.0)  # fair gegenueber der oeffentlichen Overpass-Instanz

    conn.close()
    return kennzahlen
