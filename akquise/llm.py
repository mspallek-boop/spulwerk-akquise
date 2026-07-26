"""Anbindung an Sprachmodelle (ohne SDK, nur Standardbibliothek).

Unterstützt zwei Anbieter:
  - "google"    Gemini (kostenlose API-Stufe über Google AI Studio)
  - "anthropic" Claude (kostenpflichtig, Prepaid-Guthaben)

Der Anbieter steht in config.json unter llm.anbieter. Ohne gültigen Key
fällt das Tool automatisch auf die Vorlagen in outreach.py zurück - es
bleibt also voll funktionsfähig.
"""

import json
import re
import time
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GOOGLE_BASIS = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMFehler(Exception):
    pass


class TagesbudgetErschoepft(LLMFehler):
    """Das Tageskontingent des Anbieters ist aufgebraucht.

    Groqs Gratis-Tarif begrenzt nicht nur Anfragen pro Tag (1000), sondern
    auch Tokens pro Tag (TPD, 200.000 fuer gpt-oss-120b). Ein Lead kostet mit
    E-Mail + DM rund 4.700 Tokens - nach etwa 40 Leads ist Schluss. Warten
    hilft dann nicht mehr (Reset erst am naechsten Tag), deshalb ein eigener
    Fehler: der Lauf endet sauber, statt Vorlagentexte zu erzeugen.
    """


def verfuegbar(config_dict, key):
    return bool(config_dict["llm"].get("aktiv") and key)


# ------------------------------------------------------------- Anthropic

def _frage_anthropic(key, modell, system, prompt, max_tokens, temperature):
    nutzlast = {
        "model": modell,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    anfrage = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(nutzlast).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    ergebnis = _sende(anfrage)
    teile = [b.get("text", "") for b in ergebnis.get("content", []) if b.get("type") == "text"]
    return "".join(teile).strip()


# ---------------------------------------------------------------- Google

def _frage_google(key, modell, system, prompt, max_tokens, temperature):
    url = "%s/%s:generateContent?key=%s" % (GOOGLE_BASIS, modell, key)
    nutzlast = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    anfrage = urllib.request.Request(
        url,
        data=json.dumps(nutzlast).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    ergebnis = _sende(anfrage)
    kandidaten = ergebnis.get("candidates") or []
    if not kandidaten:
        rueckmeldung = ergebnis.get("promptFeedback") or {}
        raise LLMFehler("Keine Antwort (evtl. blockiert): %s" % rueckmeldung)
    teile = kandidaten[0].get("content", {}).get("parts", [])
    return "".join(t.get("text", "") for t in teile).strip()


# ------------------------------------------------------------------ Groq
# Groq spricht das OpenAI-kompatible Chat-Format.

def _frage_groq(key, modell, system, prompt, max_tokens, temperature):
    nutzlast = {
        "model": modell,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    # gpt-oss sind Reasoning-Modelle: ohne Drosselung frisst das interne
    # "Nachdenken" das Token-Budget auf und schneidet die JSON-Antwort ab.
    # "low" lässt genug Platz für den eigentlichen Text.
    if "gpt-oss" in modell:
        nutzlast["reasoning_effort"] = "low"
    anfrage = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(nutzlast).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer %s" % key,
            # Groq sitzt hinter Cloudflare; der urllib-Standard-User-Agent
            # loest eine 403/1010-Sperre aus. Ein Browser-UA umgeht das.
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) SpulwerkAkquise/1.0",
        },
    )
    ergebnis = _sende(anfrage)
    auswahl = ergebnis.get("choices") or []
    if not auswahl:
        raise LLMFehler("Keine Antwort: %s" % ergebnis)
    return (auswahl[0].get("message", {}).get("content") or "").strip()


# ------------------------------------------------------------ gemeinsam

def _wartezeit(header, koerper):
    """Liest die empfohlene Wartezeit aus Retry-After-Header oder Fehlertext."""
    nach = header.get("Retry-After") if header else None
    if nach:
        try:
            return float(nach)
        except ValueError:
            pass
    treffer = re.search(r"try again in ([0-9.]+)\s*s", koerper, re.IGNORECASE)
    if treffer:
        return float(treffer.group(1))
    return 5.0


def _ist_tagesbudget(detail):
    """Unterscheidet das Tageslimit (Reset erst morgen) vom Minutenlimit."""
    text = detail.lower()
    return "per day" in text or "(tpd)" in text or "(rpd)" in text


def _budget_meldung(detail):
    treffer = re.search(r'"message"\s*:\s*"([^"]+)"', detail)
    return treffer.group(1) if treffer else detail[:300]


def _sende(anfrage, versuche=5):
    """Sendet die Anfrage. Bei 429 (Rate-Limit) wird die empfohlene Zeit
    abgewartet und erneut versucht - so laufen auch Gratis-Kontingente durch,
    nur eben langsamer."""
    for versuch in range(versuche):
        try:
            with urllib.request.urlopen(anfrage, timeout=120) as antwort:
                return json.loads(antwort.read().decode("utf-8"))
        except urllib.error.HTTPError as fehler:
            detail = fehler.read().decode("utf-8", errors="replace")
            if fehler.code == 429 and _ist_tagesbudget(detail):
                raise TagesbudgetErschoepft(_budget_meldung(detail))
            if fehler.code == 429 and versuch < versuche - 1:
                pause = min(_wartezeit(fehler.headers, detail) + 0.6, 30.0)
                time.sleep(pause)
                continue
            raise LLMFehler("API-Fehler %s: %s" % (fehler.code, detail[:400]))
        except Exception as fehler:
            raise LLMFehler("Verbindung zum Modell fehlgeschlagen: %s" % fehler)
    raise LLMFehler("Rate-Limit hielt trotz mehrerer Versuche an")


def frage(key, modell, system, prompt, max_tokens=1600, temperature=0.7,
          anbieter="anthropic"):
    if anbieter == "google":
        text = _frage_google(key, modell, system, prompt, max_tokens, temperature)
    elif anbieter == "groq":
        text = _frage_groq(key, modell, system, prompt, max_tokens, temperature)
    else:
        text = _frage_anthropic(key, modell, system, prompt, max_tokens, temperature)
    if not text:
        raise LLMFehler("Leere Antwort des Modells")
    return text


def groq_ratelimit(key, modell="openai/gpt-oss-120b"):
    """Fragt Groq nach dem aktuellen Tages-Anfragelimit.
    Rueckgabe: (limit, verbleibend) - beide None bei Fehler.
    Eine Mini-Anfrage (1 Token), liest die x-ratelimit-Header."""
    payload = {"model": modell, "messages": [{"role": "user", "content": "hi"}],
               "max_tokens": 1}
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json",
                 "authorization": "Bearer %s" % key,
                 "user-agent": "Mozilla/5.0 SpulwerkAkquise/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            header = resp.headers
    except urllib.error.HTTPError as fehler:
        header = fehler.headers  # 429 liefert die Header trotzdem
    except Exception:
        return None, None

    def num(wert):
        try:
            return int(wert)
        except (TypeError, ValueError):
            return None
    return (num(header.get("x-ratelimit-limit-requests")),
            num(header.get("x-ratelimit-remaining-requests")))


def frage_json(key, modell, system, prompt, max_tokens=1600, anbieter="anthropic"):
    """Wie frage(), erwartet aber ein JSON-Objekt als Antwort."""
    roh = frage(key, modell, system, prompt, max_tokens=max_tokens,
                temperature=0.9, anbieter=anbieter)
    start = roh.find("{")
    ende = roh.rfind("}")
    if start == -1 or ende == -1:
        raise LLMFehler("Antwort enthielt kein JSON: %s" % roh[:200])
    try:
        return json.loads(roh[start:ende + 1])
    except ValueError as fehler:
        raise LLMFehler("JSON nicht lesbar: %s" % fehler)
