
# Kölner Gremienkalender

Automatischer ICS-Kalender für die öffentlichen Gremiensitzungen der Stadt Köln.

## Projektziel

Dieses Projekt soll einen zentralen, abonnierbaren ICS-Kalender mit den zukünftigen öffentlichen Sitzungen der politischen Gremien der Stadt Köln bereitstellen.

Berücksichtigt werden sollen:

- Rat der Stadt Köln
- Sämtliche Fachausschüsse
- Sämtliche Betriebsausschüsse
- Alle neun Bezirksvertretungen:
  - Innenstadt
  - Rodenkirchen
  - Lindenthal
  - Ehrenfeld
  - Nippes
  - Chorweiler
  - Porz
  - Kalk
  - Mülheim

Die neue Ratsperiode ab November 2025 wird berücksichtigt.

## Datenquelle

Verwendet wird die öffentliche OParl-Schnittstelle von mandariOSS.

- [Gremien](https://oparl.mandari.de/oparl/v1/body/277544fa-c7eb-598a-b8fa-458b979ccbec/organizations)
- [Sitzungen](https://oparl.mandari.de/oparl/v1/body/277544fa-c7eb-598a-b8fa-458b979ccbec/meetings)
- [Vorlagen](https://oparl.mandari.de/oparl/v1/body/277544fa-c7eb-598a-b8fa-458b979ccbec/papers)

Die Daten werden ausschließlich lesend abgerufen.

## Technische Architektur

Geplante Komponenten:

- GitHub: Speicherung des Quellcodes
- Python: Kalendergenerator
- GitHub Actions: spätere automatisierte Aktualisierung
- GitHub Pages: spätere Bereitstellung der ICS-Datei
- mandariOSS: öffentliche Datenquelle

Cloudflare und eigene Server werden nicht verwendet.

## Sicherheitskonzept

Die Sicherheit und die Kontrolle des Projektinhabers haben höchste Priorität.

Grundsätze:

1. Keine autonomen KI-Coding-Agenten.
2. Keine fremden Repository-Mitwirkenden.
3. Keine unnötigen externen GitHub-Actions.
4. Vollständig offengelegter und nachvollziehbarer Programmcode.
5. Keine Passwörter oder API-Schlüssel im Repository.
6. Minimale Berechtigungen für spätere Automatisierungen.
7. Strikte Trennung zwischen Programmcode und externen Kalenderdaten.
8. Externe Daten werden niemals als ausführbarer Code behandelt.
9. Eingehende Daten und URLs werden überprüft.
10. Fehlerhafte Daten dürfen nicht zu einer fehlerhaften Veröffentlichung führen.
11. Programmänderungen erfolgen nur nach ausdrücklicher Zustimmung des Projektinhabers.

## Projektstatus

Das Projekt befindet sich in der Entwicklungs- und Prüfungsphase.

Bereits angelegt:

- `calendar.py`: erste Version des lesenden OParl-Datenabrufs
- `requirements.txt`: Dokumentation der Python-Abhängigkeiten
- `README.md`: Projektdokumentation

Noch nicht umgesetzt:

- Vollständige Prüfung der OParl-Daten
- Zuordnung sämtlicher Kölner Gremien
- Erzeugung der ICS-Datei
- Stabile Kalenderereignis-UIDs
- Behandlung von Änderungen und abgesagten Sitzungen
- GitHub Pages
- Automatisierte tägliche Aktualisierung

## Automatisierung

GitHub Actions sind derzeit deaktiviert.

Eine spätere Aktivierung erfolgt erst nach ausdrücklicher Zustimmung des Projektinhabers und nach vollständiger Sicherheitsprüfung des Programmcodes.

## Lizenz

Derzeit wurde keine Lizenz festgelegt.

## Transparenz

Der Programmcode soll vollständig öffentlich, verständlich und überprüfbar bleiben.

Automatische Änderungen am Programmcode sind nicht vorgesehen.
