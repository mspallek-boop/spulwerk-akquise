"""Laufprotokoll in Supabase: Wer lief wann, wie weit kam er, wurde einer angefordert?

Die Portal-Seite kann den Mac nicht direkt fragen - der schläft ja meistens.
Deshalb schreibt jeder Sweep seinen Zustand in die Tabelle `akquise_lauf`,
und ein Knopf im Portal legt dort umgekehrt einen Auftrag ab, den der Mac beim
nächsten Wachwerden abholt (`spulwerk.py wache`).

Dieselbe Tabelle funktioniert später unverändert, wenn der Lauf in der Cloud
stattfindet - dann steht in `quelle` eben "github" statt "mac".
"""

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config


def _jetzt():
    """Zeitstempel mit Zeitzone. PostgREST nimmt in JSON-Werten keine
    SQL-Ausdrücke wie now() entgegen, deshalb ISO-Format."""
    return datetime.now(timezone.utc).isoformat()

TABELLE = "akquise_lauf"

# Ein Lauf, der ewig "laeuft", ist in Wahrheit abgestürzt (Ruhezustand, Absturz).
VERWAIST_NACH_MINUTEN = 120


class LaufFehler(Exception):
    pass


def _basis(cfg):
    url = cfg["supabase"]["url"].rstrip("/")
    key = config.supabase_service_key(cfg)
    if not key:
        raise LaufFehler("Kein Supabase-Service-Key gefunden.")
    return url, key


def _anfrage(cfg, pfad, methode="GET", koerper=None, extra=None):
    url, key = _basis(cfg)
    daten = json.dumps(koerper).encode("utf-8") if koerper is not None else None
    kopf = {
        "apikey": key,
        "Authorization": "Bearer %s" % key,
        "Content-Type": "application/json",
    }
    kopf.update(extra or {})
    anfrage = urllib.request.Request(
        "%s/rest/v1/%s" % (url, pfad), data=daten, headers=kopf, method=methode
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=30) as antwort:
            roh = antwort.read().decode("utf-8")
            return json.loads(roh) if roh.strip() else None
    except urllib.error.HTTPError as fehler:
        raise LaufFehler("Supabase %s: %s"
                         % (fehler.code, fehler.read().decode("utf-8", "replace")[:200]))
    except Exception as fehler:
        raise LaufFehler("Verbindung zu Supabase fehlgeschlagen: %s" % fehler)


# ------------------------------------------------------------ schreiben

def melde_start(cfg=None, quelle="mac", lauf_id=None):
    """Neuen Lauf eintragen (oder einen angeforderten übernehmen). Gibt die ID."""
    cfg = cfg or config.lade_config()
    if lauf_id:
        _anfrage(cfg, "%s?id=eq.%d" % (TABELLE, lauf_id), "PATCH",
                 {"zustand": "laeuft", "quelle": quelle, "gestartet_am": _jetzt()},
                 {"Prefer": "return=minimal"})
        return lauf_id
    zeilen = _anfrage(cfg, TABELLE, "POST",
                      [{"quelle": quelle, "zustand": "laeuft", "schritt": "Start"}],
                      {"Prefer": "return=representation"})
    return zeilen[0]["id"] if zeilen else None


def melde_schritt(cfg, lauf_id, schritt, fortschritt=None, erledigt=None):
    """Aktueller Schritt, Fortschritt in Prozent und was schon fertig ist.

    Damit kann das Portal einen Balken zeigen, statt nur "läuft". Fehler beim
    Melden werden geschluckt - ein Lauf darf nicht an der Protokollierung
    scheitern.
    """
    if not lauf_id:
        return
    daten = {"schritt": schritt}
    if fortschritt is not None:
        daten["fortschritt"] = max(0, min(100, int(fortschritt)))
    if erledigt is not None:
        daten["erledigt"] = erledigt
    try:
        _anfrage(cfg, "%s?id=eq.%d" % (TABELLE, lauf_id), "PATCH",
                 daten, {"Prefer": "return=minimal"})
    except LaufFehler:
        pass


def melde_ende(cfg, lauf_id, zustand="fertig", meldung=None, kennzahlen=None):
    if not lauf_id:
        return
    try:
        _anfrage(cfg, "%s?id=eq.%d" % (TABELLE, lauf_id), "PATCH",
                 {"zustand": zustand, "beendet_am": _jetzt(), "schritt": None,
                  "fortschritt": 100 if zustand == "fertig" else None,
                  "meldung": meldung, "kennzahlen": kennzahlen},
                 {"Prefer": "return=minimal"})
    except LaufFehler:
        pass


def fordere_an(cfg=None, von="cli"):
    """Legt einen Auftrag ab, den der nächste `wache`-Durchgang abholt."""
    cfg = cfg or config.lade_config()
    zeilen = _anfrage(cfg, TABELLE, "POST",
                      [{"zustand": "angefordert", "angefordert_am": _jetzt(),
                        "angefordert_von": von, "schritt": "wartet auf den Mac"}],
                      {"Prefer": "return=representation"})
    return zeilen[0]["id"] if zeilen else None


# --------------------------------------------------------------- lesen

def offene_anforderung(cfg=None):
    """Ältester unerledigter Auftrag - oder None."""
    cfg = cfg or config.lade_config()
    zeilen = _anfrage(
        cfg,
        "%s?zustand=eq.angefordert&order=angefordert_am.asc&limit=1" % TABELLE,
    )
    return zeilen[0] if zeilen else None


def letzte(cfg=None, anzahl=5):
    cfg = cfg or config.lade_config()
    return _anfrage(cfg, "%s?order=gestartet_am.desc&limit=%d" % (TABELLE, anzahl)) or []


def raeume_verwaiste_auf(cfg=None):
    """Läufe, die seit Stunden 'laeuft' melden, sind abgestürzt (z. B. weil der
    Mac eingeschlafen ist). Die werden ehrlich als abgebrochen markiert."""
    cfg = cfg or config.lade_config()
    grenze = (datetime.now(timezone.utc)
              - timedelta(minutes=VERWAIST_NACH_MINUTEN)).isoformat()
    pfad = "%s?zustand=eq.laeuft&gestartet_am=lt.%s" % (TABELLE, grenze)
    try:
        _anfrage(cfg, pfad, "PATCH",
                 {"zustand": "abgebrochen",
                  "meldung": "Kein Lebenszeichen mehr - vermutlich Ruhezustand oder Absturz.",
                  "beendet_am": _jetzt()},
                 {"Prefer": "return=minimal"})
    except LaufFehler:
        pass
