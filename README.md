# Spulwerk Akquise

Ein Werkzeug, das für **Spulwerk** (Video- & Fotostudio, Wien) passende
Betriebe findet, sie nach Bedarf an Video/Fotografie **bewertet**,
öffentliche Kontaktdaten **recherchiert** und personalisierte
**Erstansprachen als Entwürfe** erzeugt – für E-Mail, Instagram-DM und
Telefon.

> **Es versendet nichts automatisch.** Alle Texte sind Entwürfe, die du
> prüfst und selbst verschickst. Warum das so ist – und was du bei
> Kaltakquise in Österreich beachten musst – steht in
> [RECHTLICHES.md](RECHTLICHES.md). Bitte vorher lesen.

## Voraussetzungen

- Python 3.9+ (nur Standardbibliothek – **keine Installation nötig**)
- Internet (für die Betriebssuche über OpenStreetMap)
- Optional: ein API-Key für individuell formulierte Ansprachen durch ein
  Sprachmodell. Unterstützt **Google Gemini** (kostenlose API-Stufe) oder
  **Anthropic Claude** (kostenpflichtig). Ohne Key nutzt das Tool eingebaute
  Vorlagen und funktioniert vollständig.

## Schnellstart

```bash
cd spulwerk-akquise
python3 spulwerk.py init
python3 spulwerk.py pipeline --stadt wien --kategorien gastro,hotel,immobilien --limit 40
python3 spulwerk.py web        # Dashboard im Browser: http://127.0.0.1:8733
```

`pipeline` führt in einem Rutsch aus: **suche → anreichern → bewerten →
texten**. Danach im Dashboard durchsehen oder per CLI exportieren.

## Die Schritte einzeln

| Befehl | Was er tut |
|--------|------------|
| `init` | Datenbank + `config.json` anlegen |
| `suche --stadt wien --kategorien gastro,hotel` | Betriebe über OpenStreetMap finden |
| `anreichern` | Websites analysieren: Kontakt, Social, Videos, Zustand |
| `bewerten` | Leads nach Bedarf priorisieren (Score 0–100, Prio A–D) |
| `texten --limit 20` | Erstansprachen als Entwürfe erzeugen |
| `liste --min-score 60` | Leads als Tabelle |
| `zeige 42` | Vollständiges Dossier eines Leads inkl. Entwürfe |
| `status 42 kontaktiert --kanal telefon --wiedervorlage 7 --notiz "..."` | Verlauf pflegen |
| `sperren "domain.at" --grund "Widerspruch"` | Betrieb dauerhaft ausschließen |
| `export --eml --csv --arbeitsliste` | Entwürfe/Leads ausgeben |
| `uebersicht` | Pipeline + fällige Wiedervorlagen |
| `web` | Lokales Dashboard starten |
| `sweep` | Kompletter Durchlauf über alle Branchen (idempotent, für nachts) |
| `zeitplan einrichten --stunde 20` | Täglichen Sweep als macOS-Job (launchd) |
| `sync` | Leads + Entwürfe ins Portal (Supabase) übertragen |
| `gmail push` | Fällige Entwürfe selbst ins Gmail-Postfach legen (IMAP) |
| `gmail abgleich` | Postfach lesen: was liegt im Entwurf, was ist versendet |
| `gmail status` | Zahlen fürs Postfach |
| `gmail warteschlange --limit 40` | Nur die Datei schreiben (für den Claude-Task) |
| `gmail fertig 12 34` | Angelegte Entwürfe abhaken |
| `wache` | Im Portal angeforderten Lauf abholen und starten |
| `saeubern` | Alle Entwürfe nachträglich prüfen (Jargon, falsche Domains) |

Jeder Befehl kennt `--help`.

## Wo die Daten liegen

Zwei austauschbare Datenschichten mit gleicher Schnittstelle:

| | lokal | Cloud |
|---|---|---|
| Backend | SQLite (`daten/akquise.db`) | Supabase/Postgres (Portal-Datenbank) |
| Umschalten | Standard | `AKQUISE_DB=supabase` oder `config.json` → `datenbank.backend` |
| Lead-ID | fortlaufende Zahl | UUID |
| Entwürfe | eigene Tabelle | JSON-Spalte am Lead |

Der Nachschub-Lauf in GitHub Actions (`.github/workflows/nachschub.yml`) arbeitet
auf Supabase, weil ein Cloud-Läufer keine Platte behält. Nötige Secrets:
`GROQ_API_KEY`, `GMAIL_APP_PASSWORT`, `SUPABASE_SERVICE_KEY`.

## Postfach

Das Werkzeug legt Entwürfe direkt per IMAP im Gmail-Konto
**spulwerk.com@gmail.com** ab (App-Passwort in `.secrets/gmail-app-passwort`
oder als Umgebungsvariable `GMAIL_APP_PASSWORT`). Was schon im Entwurfsordner
liegt oder bereits versendet wurde, wird übersprungen — dieselbe Adresse
bekommt nie zwei Mails. `gmail abgleich` liest den Ist-Zustand zurück und setzt
versendete Leads auf „kontaktiert".

`texten` und `sweep` sperren sich gegenseitig über `daten/lauf.lock` – zwei
gleichzeitige Läufe würden das Minutenlimit von Groq sprengen und statt
KI-Texten Vorlagen liefern. Stürzt ein Lauf ab, kann die Lock-Datei gelöscht
werden.

## Nachschub und Gmail-Entwürfe

Der eingerichtete Zeitplan startet **täglich um 20:00 Uhr** einen `sweep`
(`caffeinate -i` hält den Mac dabei wach). Am Ende schreibt der Sweep die
fälligen E-Mail-Entwürfe nach `export/gmail-warteschlange.json`.

Gmail-Entwürfe kann das Skript nicht selbst anlegen – das macht der geplante
Claude-Task **„spulwerk-gmail-entwuerfe"** (täglich ~23:09, Gmail-Connector).
Er liest die Warteschlange, legt pro Eintrag einen Entwurf im Postfach an und
hakt die Leads mit `gmail fertig` ab, damit keine Dubletten entstehen. Es
werden ausschließlich **Entwürfe** angelegt, nie etwas versendet.

Sortierung: nach Score, also A-Leads zuerst, danach B. Pro Nacht standardmäßig
40 Stück.

## Wie bewertet wird

Der beste Lead ist ein Betrieb, der **sichtbar in Marketing investiert**
(Website, aktives Instagram), dessen **Bildsprache aber schwach** ist –
kein Video, wenige oder alte Fotos, nicht mobil optimiert. Genau da hat
Spulwerk einen konkreten, benennbaren Anlass. Wer bereits mit einer
Produktionsfirma arbeitet (Video eingebunden) oder eine Kettenfiliale ist,
wird abgewertet oder aussortiert.

Die Signale und Gewichte stehen offen in
[`akquise/score.py`](akquise/score.py) und lassen sich anpassen.

## Dashboard

`python3 spulwerk.py web` startet ein lokales Dashboard (nur `127.0.0.1`):
Pipeline-Kennzahlen, filterbare Lead-Liste, pro Lead ein Detailfenster mit
den Entwürfen (bearbeit- und kopierbar) sowie Status, Notiz und
Wiedervorlage. Auch dieses Dashboard versendet nichts – es kopiert dir den
fertigen Text, den Versand machst du bewusst selbst.

## Konfiguration

`config.json` (nach `init` vorhanden): Absender, Positionierung,
Leistungen, Standard-Stadt/-Kategorien, Score-Schwelle, LLM-Modell.
Trag deine echte Kontaktadresse und ggf. Referenzen ein – die Texte werden
dadurch konkreter.

KI-Texte aktivieren. Anbieter und Modell stehen in `config.json` unter
`llm`. Der Key wird je Anbieter gesucht: Umgebungsvariable → lokale
Key-Datei → `config.json` (die Key-Datei ist per `.gitignore` geschützt).

**Google Gemini (kostenlos, empfohlen).** Gratis-Key ohne Kreditkarte
unter <https://aistudio.google.com/apikey>:

```bash
echo "DEIN-GEMINI-KEY" > .gemini_key
python3 spulwerk.py pruefe        # findet den Key und testet die Verbindung
python3 spulwerk.py texten --limit 20 --neu-erzeugen
```

`config.json` steht bereits auf `"anbieter": "google"`,
`"modell": "gemini-2.0-flash"`.

**Anthropic Claude (kostenpflichtig)** als Alternative: in `config.json`
`"anbieter": "anthropic"` und `"modell": "claude-sonnet-5"` setzen, dann:

```bash
echo "sk-ant-DEIN-KEY" > .anthropic_key
python3 spulwerk.py pruefe
```

`pruefe` zeigt den Key nie im Klartext, nur die letzten vier Zeichen.

## Datenquelle

Betriebsdaten stammen aus **OpenStreetMap** (offene ODbL-Lizenz) über die
öffentliche Overpass-API – Name, Adresse, Website, Telefon, soweit dort
hinterlegt. Kein Scraping von Google, kein Kauf von Adresslisten. Die
Website-Analyse liest nur öffentlich erreichbare Seiten und respektiert
`robots.txt`.

## Projektaufbau

```
spulwerk-akquise/
  spulwerk.py         CLI-Einstieg
  akquise/
    config.py         Stammdaten, Zielbranchen, Städte
    db.py             SQLite-Datenhaltung
    discover.py       Betriebssuche (OpenStreetMap/Overpass)
    enrich.py         Website-Analyse
    score.py          Bewertung / Priorisierung
    llm.py            LLM-Anbindung (Groq/Google/Anthropic)
    outreach.py       Entwürfe (E-Mail, DM, Telefon)
    report.py         Listen, Dossiers, CSV-/.eml-Export
    sync.py           Übertragung ins Portal (Supabase)
    gmail.py          Warteschlange für Gmail-Entwürfe
    web.py            Lokales Dashboard
  daten/akquise.db    deine Leads (wird angelegt)
  daten/lauf.lock     verhindert parallele texten-/sweep-Läufe
  entwuerfe/          .eml-Entwürfe
  export/             CSV, Arbeitslisten
  config.json         deine Konfiguration
  RECHTLICHES.md      bitte lesen
```
