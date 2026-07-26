# Rechtliches zur Kaltakquise

**Kurz:** Dieses Tool recherchiert, bewertet und schreibt Entwürfe. Es
versendet **nichts** automatisch. Ob, wie und an wen du einen Entwurf
tatsächlich schickst, entscheidest und verantwortest du. Der folgende
Überblick ersetzt keine Rechtsberatung.

## Österreich (Sitz von Spulwerk)

**E-Mail & Telefon – § 174 TKG 2021.** Unaufgeforderte Werbung per
E-Mail und Telefonanruf ist **auch im B2B** nur mit **vorheriger
Einwilligung** des Empfängers zulässig. Das gilt für Kaltakquise ohne
bestehende Geschäftsbeziehung. Verstöße können mit Geldstrafen **bis zu
37.000 €** geahndet werden.

Praktische Konsequenz für die Nutzung:

- **Kaltanrufe** bei Betrieben ohne vorherige Einwilligung: rechtlich
  heikel. Der Telefonleitfaden dieses Tools ist als Gesprächsvorbereitung
  gedacht – setze ihn nur ein, wo eine Einwilligung oder ein tragfähiger
  Anlass besteht (z. B. der Betrieb hat aktiv Kontaktaufnahme erbeten).
- **Kalte Werbe-E-Mails** an `office@`/`info@`: ebenfalls von § 174
  erfasst. Am tragfähigsten sind Wege, bei denen der Empfänger den Kontakt
  eröffnet – etwa ein Kontaktformular des Betriebs, ein
  Ausschreibungs-/Anfrageportal oder ein persönliches Netzwerk-Intro.
- **Social-DMs (Instagram):** unterliegen zusätzlich den Nutzungsbedingungen
  der Plattform. Weniger klar von § 174 erfasst als E-Mail, aber kein
  Freibrief – Maß halten, kein Massenversand.

**DSGVO.** Namen und Kontaktdaten von Ansprechpartnern sind
personenbezogene Daten. Rechtsgrundlage für die Recherche kann das
berechtigte Interesse (Art. 6 Abs. 1 lit. f) sein; das erfordert eine
Abwägung, Transparenz und die Wahrung von Betroffenenrechten
(Auskunft, Löschung, Widerspruch). Wer widerspricht, gehört auf die
Sperrliste (`./spulwerk.py sperren`).

## Deutschland (falls du dort akquirierst)

**§ 7 UWG.** E-Mail-Werbung ohne Einwilligung ist unzulässig. Für B2B gilt
eine enge Ausnahme (§ 7 Abs. 3) nur bei bestehender Kundenbeziehung und
eigenen, ähnlichen Produkten – für Kaltakquise also praktisch nicht
einschlägig. Telefonwerbung gegenüber Unternehmen ist bei **mutmaßlicher
Einwilligung** zulässig (enge Auslegung, sachlicher Bezug zum Geschäft
des Angerufenen nötig).

## So ist das Tool darauf ausgelegt

- Kein Auto-Versand. Entwürfe landen in der Datenbank und als Dateien.
- `.eml`-Export öffnet als **Entwurf** im Mailprogramm (`X-Unsent`) – du
  liest gegen, bevor etwas rausgeht.
- Wettbewerber und Ketten werden bei der Suche aussortiert.
- `robots.txt` wird bei der Website-Analyse respektiert.
- Sperrliste für Betriebe, die nicht kontaktiert werden wollen.
- Fairer Umgang mit der offenen Overpass-API (Pausen, ein Nutzer-Agent
  mit Kontaktangabe).

## Empfohlene, tragfähige Wege für den Erstkontakt

1. **Kontaktformular des Betriebs** nutzen – der Betrieb bietet den Kanal
   aktiv an.
2. **Persönliche Vorstellung** über gemeinsames Netzwerk / Empfehlung.
3. **Einwilligung einholen**, bevor du in die E-Mail-/Telefon-Ansprache
   gehst (z. B. über Social-Interaktion, Event, Messe).
4. Wenn E-Mail: an eine im Impressum **für Anfragen ausgewiesene** Adresse,
   klar als Erstkontakt erkennbar, mit einfacher Abbestellmöglichkeit.

Im Zweifel: kurze Rücksprache mit einer Rechtsberatung oder der WKO.
