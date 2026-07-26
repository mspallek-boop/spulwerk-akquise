"""Wählt die Datenschicht aus - lokal SQLite, in der Cloud Supabase.

Der übrige Code ruft weiterhin nur `db.…` auf und merkt vom Unterschied nichts.
Umgeschaltet wird über:

  1. Umgebungsvariable  AKQUISE_DB=supabase   (so läuft es in GitHub Actions)
  2. config.json        {"datenbank": {"backend": "supabase"}}
  3. Standard           sqlite

Warum überhaupt zwei: Auf dem Mac ist die SQLite-Datei bequem und schnell.
In der Cloud gibt es keine Platte, die zwischen zwei Läufen bestehen bleibt -
dort ist die Portal-Datenbank die einzige Wahrheit.
"""

import os

from . import config


def gewaehltes_backend():
    aus_umgebung = os.environ.get("AKQUISE_DB", "").strip().lower()
    if aus_umgebung:
        return aus_umgebung
    try:
        return (config.lade_config().get("datenbank", {}).get("backend") or "sqlite").lower()
    except Exception:
        return "sqlite"


BACKEND = "supabase" if gewaehltes_backend() in ("supabase", "postgres", "pg") else "sqlite"

if BACKEND == "supabase":
    from .db_pg import *          # noqa: F401,F403
    from .db_pg import (          # noqa: F401  - ausdrücklich, damit klar ist, was gebraucht wird
        STATUS_REIHENFOLGE, aktualisiere_lead, aktualisiere_viele, anzahl_leads, entwuerfe, entwuerfe_frisch_seit,
        faellige_wiedervorlagen, gmail_kandidaten, heute, hole_lead, in_tagen,
        initialisiere, ist_gesperrt, jetzt, kontakte, lade_json, leads,
        loesche_entwurf, markiere_gmail, protokolliere_kontakt, speichere_entwurf,
        speichere_lead, sperre, sperrliste, statistik, verbinde, gmail_zahlen,
    )
else:
    from .db_sqlite import *      # noqa: F401,F403
    from .db_sqlite import (      # noqa: F401
        STATUS_REIHENFOLGE, aktualisiere_lead, aktualisiere_viele, anzahl_leads, entwuerfe, entwuerfe_frisch_seit,
        faellige_wiedervorlagen, gmail_kandidaten, heute, hole_lead, in_tagen,
        initialisiere, ist_gesperrt, jetzt, kontakte, lade_json, leads,
        loesche_entwurf, markiere_gmail, protokolliere_kontakt, speichere_entwurf,
        speichere_lead, sperre, sperrliste, statistik, verbinde, gmail_zahlen,
    )
