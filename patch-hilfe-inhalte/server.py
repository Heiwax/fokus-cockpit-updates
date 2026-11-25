#!/usr/bin/env python3
"""BlockEm – lokaler Server.

Liest deine Bildschirmzeit aus ActivityWatch (http://localhost:5600) und deine
Aufgaben/Ziele/Projekte aus dem Obsidian-Vault, und liefert die BlockEm-App aus.
Keine Zusatzpakete nötig: nur Python 3.

Start:  python3 server.py      dann im Browser: http://localhost:8765
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

try:
    import schutz_pc   # Deinstallier-Schutz Mac/Windows (09.11.2026)
except ImportError:    # Handy-App (Pyodide) bringt nur server.py mit: dort gibt es keinen Computer-Schutz
    class schutz_pc:
        init = waechter = staticmethod(lambda *a: None)
        sperre = laeuft_zeile = konto_sperre = staticmethod(lambda *a: None)
        status = staticmethod(lambda: {"plattform": "andere", "eingerichtet": False})

        @staticmethod
        def aktion(data):
            raise ValueError("Nur am Computer.")

HERE = Path(__file__).resolve().parent
# Eigene Daten (config.json, automatik.json, fokus.json, Handy-Daten …): neben dem Code, auf einem Server bzw. im Handy in COCKPIT_DATEN
DATEN = Path(os.environ.get("COCKPIT_DATEN") or HERE).expanduser().resolve()
DATEN.mkdir(parents=True, exist_ok=True)
STATIC = {"/": "index.html", "/index.html": "index.html", "/manifest.webmanifest": "manifest.webmanifest", "/handy.apk": "handy.apk",
          "/sw.js": "sw.js", "/icon.svg": "icon.svg"}
INCLUDE = re.compile(rb"^<!--@ (teile/[\w.-]+) -->\n", re.M)


def build_index():
    """index.html ist nur das Gerüst: jede Zeile <!--@ teile/x --> wird durch die Teildatei ersetzt (ergibt eine Seite wie vorher)."""
    return INCLUDE.sub(lambda m: (HERE / m.group(1).decode()).read_bytes(), (HERE / "index.html").read_bytes())


BROWSERS = ("chrome", "safari", "firefox", "arc", "brave", "edge", "opera", "vivaldi")
SKELETON = (b'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            b'<meta name="apple-mobile-web-app-capable" content="yes"><link rel="apple-touch-icon" href="icon.svg">'
            b'<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}[hidden]{display:none!important}</style></head><body>',
            b'</body></html>')
CHECKBOX = re.compile(r"^(\s*[-*] \[)( |x|X)(\] )(.+)$")


def load_json(name):
    f = DATEN / name
    return json.loads((f if f.exists() else HERE / name).read_text("utf-8"))


APP_VERSION = "1.0.98"   # 1.0.95: BlockEm auf Deutsch und Englisch (Sprachwahl in Einrichtung und unter Aussehen), Schalter „Bild-KI auf YouTube und Netflix“ unter Schutz (24.11.); 1.0.94: 1.0.94: erster Start ohne Flackern (Symbol bis zur Einrichtung statt alter Oberfläche, Ladebildschirm und „Heute“) (23.11.); 1.0.93: 1.0.93: Bild-KI-Modell steckt in der App (kein Nachladen) und startet nach langem Aus schneller, Einstellungen fertig (keine schwarzen Seiten, Sicherung unter Daten & Export, kürzere Texte), Mac sperrt wieder (Befehle an den richtigen Browser), „Heute“ zeigt Limits direkt nach der Einrichtung (23.11.); 1.0.92: Sammel (21.11.); 1.0.91: Männchen bewegt sich ruhig weiter beim Nachladen, Schloss und Weg-Figur bewegen sich, Schloss auch wenn alles frei ist (offen), Weg-Karte wie Vorschau mit „Karte öffnen“ (große Karte), Ausblick raus (21.11.); 1.0.90: Kern-Runde 3 nach Designübergabe (Fokus mit Anpassen, Sperre mit aktivem Schloss, Sperre anlegen, Schutz mit Erklärungen, Geräte mit 24-Std-Entfernung, Weg als Karte mit Farbgeschenken, Animationen + Schalter), Abdeckung hängt nach Wischen nicht mehr (21.11.); 1.0.89: Sperrzeiten als Uhrzeit-Kreise zum Aufklappen, Apps-Reiter nur einzelne Apps, Mac bei Geräte, Pause-beenden-Knopf (Entwickler), Update-Knopf GitHub (beta) (20.11.); 1.0.88: Sperre mit Reitern wie Werte (Sperrzeiten als Kreise, Gruppen, Apps) (20.11.); 1.0.87: Android-Zurück wie Pfeil (Blätter zuerst, Verlauf, Scrollposition), Vertrauensperson lesbar, Sperrzeiten/Tageslimits/Gruppen wie Play-Fassung (Für wen: Gruppe oder Apps), Regeln in der Entwickler-Pause sichtbar, dunkler Schein, Hell weicher (20.11.); 1.0.86: Kern an die Galerie angeglichen: Maße wachsen mit der Handybreite, Kopf und Gestenleiste richtig eingerückt, Systemschriftgröße, Sperre-Leerzustand, Werte-Farben, Woche/Rückblick, App-Symbole, Weg offen (20.11.); 1.0.85: Redesign „Kern“ (neue Oberfläche Heute/Fokus/Sperre/Werte/Weg für Handy, Tablet, Mac, Windows), Block-Männchen als App-Icon, Tester-Reset wieder raus (19.11.); 1.0.58: Entwickler kann Mann/Frau wechseln, APP_VERSION wieder mitgezählt. 1.0.54: Oberfläche aufgeräumt (Inhaltsschutz nur Bild-KI einstellbar, Fokus-Profil mit einem Knopf zum Porno-Schutz, Automatisch begrenzen erklärt, Reels unter Sperren, Geschichten und Audio fest für alle), Umbau Heute/Weg/Zahnrad (11.11.); 1.0.53: Belohnung Stufe 1: Karte „Dein Bild“ (ein Teil pro sauberem Tag, Sammlung), Gesamttage statt 0 nach Rückfall, Warum-Satz beim Panik-Knopf, Karte „Gemeinsam“ (anonym), Morgen-Mitteilung (11.11.); 1.0.52: Hentai + Text-Erkennung auch am Mac, Abdeckkarten folgen dem Scrollen (blockem-decke), Ausstieg und Gratiszeit laufen auch ohne Netz-Zeit ab, Sprachregel ohne Update-/Rechte-Fenster, ehrliche Bedienungshilfen-Erklärung, Seite „Was dein Gerät verlässt“, Blätter Vertrauensperson/Pro neu, freischaltbare Farbstile, Mac-Helfer universal (10./11.11.); 1.0.51: Bild-KI prüft jedes Bild mit dem großen Modell, bis zu 6 Bilder in Kachel-Ansichten, Freigabe-Fenster nur bei offener App (09.11.); 1.0.50: Bezahlung (BlockEm Pro, 7 Tage gratis, im Test alles frei), Orte unter Sperren, Websites jeder App mitsperren, Sicherungsdatei, Passwort vergessen per Notfall-Code (09.11.); 1.0.49: Locker „Sperren | Schutz“, Tagesverlauf in Tageszeiten, Woche/Rückblick mit Top 3 Produktiv/Neutral/Ablenkung und Öffnungen des Tages, Rückblick ohne Vault (09.11.); 1.0.48: Locker und Werte aufgeräumt (Sperrzeiten mit Namen, Mehr Schutz, Tagesverlauf als Fläche), kein Ablenkungs-Limit, Fokus nicht abbrechbar, Panik startet 10 Min Fokus, Mann/Frau Pflicht, Hilfe holen raus (09.11.); 1.0.47: Einrichtung kürzer (Rechte am Ende, nur installierte Ablenker, ohne Habits), Drive aus, Heute/Werte ohne Produktiv-Ziel, Hilfe holen (08.11.); 1.0.46: Testrunde (07.11.): Fokus-Leerraum, Rückblick-Zahlen, Locker-Leiste, Panik-Rand, Wächter ohne Doppel-Threads; 1.0.45: Deinstallier-Schutz Pflicht (48 Std, alle 6 Std, nachts nicht, 2. Mal 72 Std, PIN Vertrauensperson 1 Std), Bild-KI Modus-Kacheln, KI-Chip bei gleich schnell (05.11.); 1.0.44: Umgehungs-Test: blinde Browser zu, Proxy/Übersetzer/Archiv, Speicher löschen, Uhrzeit, Gast (05.11.); 1.0.43: Bild-KI zwei Modi (sparsam/sicher) (05.11.); 1.0.42: Karte groß, Lockern-Leiste, KI-Begleiter fest, kein Zu-Fokus, Ausnahme-Lücke zu (05.11.); 1.0.41: Anzeige KI-Chip oder CPU (05.11.); 1.0.40: Bild-KI akkuschonender (nur nach Bewegung schnell, KI-Chip, Sparmodus) (05.11.); 1.0.39: Design-Korrekturen (05.11.); 1.0.38: Release-Feinschliff: Sperrzeiten-Bereich, Schutz-Seite neu, Text-Erkennung + Hentai fest für alle, Mann/Frau fest, Lockern teurer immer an (05.11.); 1.0.36: Frauen-Profil Kontrast, Mehr-Blatt, Statusleiste (05.11.); 1.0.35: Frauen-Profil Koralle & Weinrot (05.11.); 1.0.33: Social/Streaming immer Konsum, Frauen-Profil Farben (05.11.); 1.0.32: ohne Lichtflecken/Verlauf, Karten mit Tiefe, eigene Fokus-Dauer, Frauen-Profil weicher (05.11.); 1.0.29: Frauen-Profil Runde 1 (Listen, weiß-rosa, Mann/Frau umschalten) (05.11.); 1.0.28: Profile Porno-Schutz/Fokus nach Ziel und Geschlecht (05.11.); 1.0.27: eigener Durchgang, Locker eine Leiste, Legende, Texte (05.11.); 1.0.26: Schutz-Reiter, Bild-KI nur weg/2. Treffer (05.11.); 1.0.25: Fokus immer Kacheln, doppelte Sperr-Einstellungen raus (05.11.); 1.0.24: Locker-Gruppen, Aussehen-Auswahl aus (05.11.); 1.0.23: Runde 2 (Locker ohne PIN, Grundschutz immer an, Fokus-Kacheln, Einstellungen, Werte/Habits, Statusleiste, 05.11.); 1.0.22: Geräte & Konto im Android-Stil (Thread Sync, 05.11.); 1.0.21: Einstellungen, Locker-Unterseiten im Android-Stil wie Pixel (Kacheln, runde Symbole, kurze Texte, 05.11.); 1.0.20: Locker, Werte und Einstellungen im Apple-Stil (gruppierte Listen, Unterseiten, 04.11.); 1.0.19: Einstellungs-Unterseiten modern und luftig (04.11.); 1.0.18: Geräte & Konto (Anmelden mit E-Mail und Passwort, Thread Sync), 04.11.; 1.0.17: Gerät per Code statt QR, Einstellungen aufgeräumt (04.11.); 1.0.16: Sync, KI und Google am Handy ohne ssl-Modul (Pyodide), 04.11.; 1.0.15: BlockEm Sync fest eingebaut (Helsinki), Texte „Server in der EU“ (04.11.); 1.0.14: alle Ungereimtheiten raus (nur Handy ohne Mac-Schalter, Locker nach PIN, Nachfrage mit App-Symbolen, Test-Kanal, B-Icon, 04.11.); 1.0.13: Name BlockEm, App-Symbole bei Ablenkern (04.11.); 1.0.12: Einrichtung wie Entwurf (04.11.); 1.0.11: Entwickler-Reset auch auf dem Kunden-Handy (7× tippen, nur mit Code, 04.11.); 1.0.10: Entwickler-Reset mit Einmalcode (Kunden-Handy, 04.11.); 1.0.9: Beitrags-Erkennung breiter, öfter abdecken statt wegschalten (04.11.); 1.0.8: Unschärfe raus, Abdeckung nur am Beitrag mit Wächter (04.11.); 1.0.7: Bild-KI merkt erkannten Beitrag und deckt ihn beim Scrollen weiter ab (04.11.); 1.0.6: CLIP startet wieder (LiteRT 1.4.2 statt TFLite 2.16.1, 04.11.); 1.0.5: Knopf „Bild-KI-Modell jetzt laden“ mit Fortschritt (04.11.); 1.0.4: Bild-KI am Handy wieder an (1.0.3 ohne CLIP-Dateien gebaut), Modell lädt beim Start (04.11.); 1.0.3: Sync-Wege BlockEm Sync + Google Drive, Geräte per QR-Code, Geräte-Frage in der Einrichtung, Mac-Zentrale/Import nur Entwickler (04.11.); 1.0.2: Werte je Gerät, Erziehungs-System raus, neues Icon, Design-Feinschliff (04.11.); 1.0 (Release, 03.11.): Zählung beginnt neu, 1.x gilt als neuer als 3.x (version_tupel); 3.45: Limits pro App (eine Karte mit Tageslimit, Öffnen, am Stück, Sperrzeit), Auswahl Nacktheit raus (03.11.); 3.44: Bild-KI ohne Nacktheit, CLIP allein entscheidet (02.11.); 3.43: Anzeige, welches Bild-KI-Modell läuft, CLIP lädt sofort (02.11.); 3.42: Regeln gelten am Handy sofort nach dem Ändern in der App (02.11.); 3.41: Bild-KI am Handy schneller beim Scrollen, kleine Bilder früher, passende Grenzen ohne CLIP (02.11.); 3.40: Erziehungs-System am Handy, CLIP-Bild-KI, gemeinsamer Stand (02.11.); 3.39: Akkusparer, CLIP wird einmal per WLAN nachgeladen (02.11.); 3.38: Bild-KI mit CLIP (139 MB, Update nur über den Mac, 02.11.); 3.37: neues App-Icon D (02.11.); 3.36: Systemleisten in App-Farbe, Regel-Assistent im Glas-Look, Ring mittig (02.11.); 3.35: Fehlalarm-Knopf und Kachel „Bild-KI übersehen“ raus (02.11.); 3.34: keine Scrollleiste in der WebView (02.11.); 3.33: Bild-KI Flickr-Runde, neuer linearer Kopf, neue Grenzen (02.11.); 3.31: Fehlalarm mit Auswahl, was es stattdessen ist (02.11.); 3.30: Scrollleiste aus, Design S fertig (02.11.); 3.29: Inter eingebettet, Glas, Systemleisten, Regel-Symbole, Dialoge (02.11.); 3.28: Silhouetten-Icons, Regel-Karten, Fokus ohne „eigene“-Zeile (02.11.); 3.27: Heute und Locker genau wie Entwurf S (02.11.); 3.26: Design S, Name BlockEm in der Handy-App (02.11.); 3.25: Knopf Fehlalarm, Kachel Bild-KI übersehen, /api/bild-rueck (02.11.); 3.24: Bild-KI-Modell Runde 2+3 (02.11.); 3.23: neuer Kopf mit 6 Klassen, Handy prüft nur die Beitragsfläche (01.11.); 3.22: Anzeige unten (was gesehen, was passiert, ohne Prozente), Wächter, Schalter YouTube/Netflix (01.11.); 3.21: Bild-KI mit Klassen (Bikini, Unterwäsche, Mann okay) und Regeln je Klasse (01.11.); 3.20: Leistung ganzer Computer + Handys, Tagesplan/Kalender aus (01.11.); 3.19: ein Fenster-Helfer statt vieler osascript-Starts, Bildersuche, Handy-Browser-Raster (01.11.); = Handy-App-Version in handy.apk (3.17: Windows-Sperre, Bild-KI am Computer, Test-Anzeige, 01.11.)
# Grundeinstellungen für neue Nutzer (eine vorhandene config.json gewinnt immer; fehlt sie, wird sie angelegt)
CFG_STANDARD = {"port": 8765, "lan": True, "activitywatch": "http://127.0.0.1:5600", "vault": "",
                "cockpitNotiz": "Cockpit/Cockpit.md", "datenOrdner": "BlockEm", "projekteOrdner": "Projekte",
                "tagesberichtOrdner": "Cockpit/Bildschirmzeit", "produktivZielMin": 180, "prokrastinationLimitMin": 60}
try:
    CFG = load_json("config.json")
except FileNotFoundError:
    CFG = dict(CFG_STANDARD)
for _k, _v in CFG_STANDARD.items():
    if _k not in ("cockpitNotiz", "datenOrdner", "tagesberichtOrdner"):
        CFG.setdefault(_k, _v)
if not CFG.get("cockpitNotiz"):
    CFG["cockpitNotiz"], CFG["tagesberichtOrdner"] = CFG_STANDARD["cockpitNotiz"], CFG_STANDARD["tagesberichtOrdner"]
# Wo das Cockpit läuft: "mac" (lokal wie bei John), "server" (bei einem Anbieter im Internet), "handy" (in der App, ohne Server)
# "mac" heißt: lokal auf dem eigenen Computer (Mac oder Windows-PC)
MODUS = (os.environ.get("COCKPIT_MODUS") or CFG.get("modus") or ("mac" if sys.platform in ("darwin", "win32") else "server")).lower()
if os.environ.get("COCKPIT_PORT") or os.environ.get("PORT"):
    CFG["port"] = int(os.environ.get("COCKPIT_PORT") or os.environ["PORT"])
if os.environ.get("COCKPIT_VAULT"):
    CFG["vault"] = os.environ["COCKPIT_VAULT"]
if MODUS == "server":
    # Auf dem Server liegen die Notizen im Datenordner (eigener Ordner per COCKPIT_VAULT bzw. "vault" in config.json)
    CFG["vault"] = CFG.get("vault") or str(DATEN / "notizen")
    Path(CFG["vault"]).expanduser().mkdir(parents=True, exist_ok=True)
if MODUS == "server" and len(str(CFG.get("handyCode") or "")) < 16:
    # Im Internet reicht ein 6-stelliger Code nicht: langer Zugangscode (oder fest aus COCKPIT_ZUGANG)
    CFG["handyCode"] = os.environ.get("COCKPIT_ZUGANG") or secrets.token_urlsafe(18).replace("-", "x").replace("_", "y").upper()
    (DATEN / "config.json").write_text(json.dumps(CFG, ensure_ascii=False, indent=2), "utf-8")
if not CFG.get("handyCode") or not (DATEN / "config.json").exists():
    # Zugangscode für Handys und andere Geräte im WLAN, einmalig erzeugt
    CFG["handyCode"] = secrets.token_hex(3).upper()
    (DATEN / "config.json").write_text(json.dumps(CFG, ensure_ascii=False, indent=2), "utf-8")
HANDY_DIR = DATEN / "handy-daten"
# Entwicklerversion (john 01.11.): nur auf johns Mac (config.json "entwickler": "<Entwickler-Code>", seit 24.11. nicht mehr true). Dort sind z. B. alle Schutz-Schalter frei an/aus.
# Jede Release-Version (Computer-Paket, Handy-App, GitHub-Update) hat das nicht: Lockern nur mit dem Locker-Timer.
# 3.20 (john 01.11.: „Tagesplan und Kalender kann weg“): Planen gehört in die Planungs-App. In der Tracking-App sind diese
# Teile aus (kein Hintergrund-Abgleich, Schalter und Kalender ausgeblendet), nichts gelöscht. config "planung": true holt sie zurück.
PLANUNG = bool(CFG.get("planung", False))
PLAN_SCHALTER = ("tagesplan", "wochenplan", "kalender", "zeitleiste", "planfokus", "googleBloecke")
# Sicherheitstest 24.11.2026: "entwickler": true in config.json schaltete auf jedem Mac alle Sperren frei (Datei kann jeder bearbeiten).
# Jetzt nur mit dem Entwickler-Code (gleicher Hash wie in der Handy-App); die Umgebungsvariable gilt nur in Pyodide (handy_kern.py prüft vorher den Code).
ENTWICKLER = (hashlib.sha256(("clearmind-entwickler|" + re.sub(r"[^A-Z0-9]", "", str(CFG.get("entwickler") or "").upper())).encode()).hexdigest()
              == "c9aa39fd0342b634712c70a5075d212f077fe5428b2938601d11a51f73199c3f") or (sys.platform == "emscripten" and os.environ.get("COCKPIT_ENTWICKLER") == "1")   # nur in der Handy-App (Pyodide), nicht per Umgebung/config am Mac


def mem(name=""):
    """Ordner im Vault für die Cockpit-Notizen (Habits, Erledigt, Rückblicke …): bei John „Claude Memory“, neu „Cockpit“."""
    o = CFG.get("datenOrdner") or str(Path(CFG.get("cockpitNotiz") or "Claude Memory/Cockpit.md").parent)
    o = "Claude Memory" if o in ("", ".") else o
    return f"{o}/{name}" if name else o


def nutzer_name():
    """Vorname für Begrüßung und KI-Anleitungen: Einstellung „name“, sonst der Benutzername des Macs (john -> John)."""
    n = str(CFG.get("name") or "").strip()
    if n:
        return n
    if MODUS != "mac":
        return ""
    try:
        n = Path.home().name
        return n[:1].upper() + n[1:] if n.isalpha() and len(n) <= 20 else ""
    except Exception:
        return ""

LOCK = threading.Lock()
CACHE = {}          # date -> (computed_at, result)
REPORTED = {}       # date -> last report write time


# ---------- ActivityWatch ----------

def aw_get(path):
    with urlopen(CFG["activitywatch"].rstrip("/") + "/api/0" + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pick_buckets():
    """Fenster- und AFK-Bucket desselben Rechners, plus Handy-Buckets (falls synchronisiert).
    Ab 3.18 zählt BlockEm selbst (eigener Zähler): dann die eigenen „Buckets“, ActivityWatch nur noch für ältere Tage."""
    if zaehler_an():
        return "eigen:fenster", "eigen:afk", []
    if MODUS == "handy":   # 1.0.50: am Handy gibt es kein ActivityWatch (fragte sonst bei jedem Tag 127.0.0.1:5600 über die App-Brücke)
        return None, None, []
    return aw_buckets()


def aw_buckets():
    buckets = aw_get("/buckets/")
    host = socket.gethostname().split(".")[0].lower()
    win = [b for b in buckets.values() if b.get("type") == "currentwindow" and "android" not in b["id"]]
    afk = [b for b in buckets.values() if b.get("type") == "afkstatus"]
    phone = [b["id"] for b in buckets.values() if "android" in b["id"]]

    def best(lst):
        if not lst:
            return None
        # der zuletzt aktive Bucket gewinnt: macOS meldet je nach Netz mal „Mac“, mal „MacBook-Pro-von-…“ als Rechnernamen,
        # dann gäbe es zwei Buckets, und der alte (seit Tagen still) darf nicht gewinnen. Rechnername nur bei Gleichstand.
        lst.sort(key=lambda b: (str(b.get("last_updated") or ""), str(b.get("hostname", "")).split(".")[0].lower() == host), reverse=True)
        return lst[0]

    w, a = best(win), best(afk)
    if w and a and w.get("hostname") != a.get("hostname"):
        same = [b for b in afk if b.get("hostname") == w.get("hostname")]
        a = same[0] if same else a
    return (w and w["id"]), (a and a["id"]), phone


def events(bucket, start, end):
    if str(bucket).startswith("eigen:"):
        return zaehler_events(bucket[6:], start, end)
    q = f"?start={quote(start.isoformat())}&end={quote(end.isoformat())}&limit=-1"
    out = []
    for e in aw_get(f"/buckets/{quote(bucket)}/events{q}"):
        s = max(parse_ts(e["timestamp"]), start)
        t = min(parse_ts(e["timestamp"]) + timedelta(seconds=e["duration"]), end)
        if t > s:
            out.append((s, t, e.get("data", {})))
    out.sort(key=lambda x: x[0])
    return out


# ---------- 3.18: Leistung: wie viel Rechenzeit (CPU) jeder Teil des Cockpits braucht (john 01.11.: „ganz genau anzeigen“) ----------
# Jeder Hintergrund-Teil misst seine eigene Prozessor-Zeit (time.thread_time) plus die der Hilfsprogramme, die er startet
# (osascript, screencapture, sips … über RUSAGE_CHILDREN, nur Mac/Linux). Alle 10 s ein Schnappschuss, 10 Minuten Verlauf.
import collections
LEISTUNG = {"lock": threading.Lock(), "teile": collections.defaultdict(float), "verlauf": collections.deque(maxlen=61)}


def _kinder_cpu():
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_CHILDREN)
        return r.ru_utime + r.ru_stime
    except Exception:
        return 0.0


LEISTUNG["fremd"] = 0.0   # Rechenzeit der dauerhaft laufenden Hilfsprozesse (Fenster-Helfer, System Events), 3.19
LEISTUNG["helfer"] = {}
_LS = threading.local()
LS_FENSTER = "Fenster-Blick (gemeinsam für Sperre, Zähler, Bild-KI)"
LS_SE = "macOS: System Events (beantwortet den Fenster-Blick)"
LS_APP = "Mac-App (Fenster und Menüleiste)"


class leistung:
    """with leistung("Zähler"): ... bucht die Prozessor-Zeit dieses Blocks auf den Teil. Verschachtelt (3.19): ein innerer
    Block (z. B. der gemeinsame Fenster-Blick) wird nur bei sich gezählt, nicht noch einmal beim äußeren."""
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0, self.k0, self.abzug = time.thread_time(), _kinder_cpu(), 0.0
        st = getattr(_LS, "s", None)
        if st is None:
            st = _LS.s = []
        st.append(self)
        return self

    def __exit__(self, *a):
        st = _LS.s
        if st and st[-1] is self:
            st.pop()
        d = (time.thread_time() - self.t0) + max(0.0, _kinder_cpu() - self.k0)
        if st:
            st[-1].abzug += d
        with LEISTUNG["lock"]:
            LEISTUNG["teile"][self.name] += max(0.0, d - self.abzug)
        return False


def _leistung_abziehen(sek):
    """Schon gezählte Zeit (beendeter Hilfsprozess, der jetzt in RUSAGE_CHILDREN auftaucht) nicht doppelt buchen."""
    st = getattr(_LS, "s", None)
    if st:
        st[-1].abzug += sek


def _prozess_cpu():
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system + LEISTUNG["fremd"]


_SYS = {}


def _system_ticks():
    """(beschäftigt, gesamt) Rechenzeit ALLER Programme auf dem Computer seit dem Start, ohne neuen Prozess (3.20).
    Mac: host_statistics, Windows: GetSystemTimes, Linux: /proc/stat."""
    try:
        import ctypes
        if sys.platform == "darwin":
            if "lib" not in _SYS:
                lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
                lib.mach_host_self.restype = ctypes.c_uint32
                _SYS.update(lib=lib, host=lib.mach_host_self())
            arr, n = (ctypes.c_uint32 * 4)(), ctypes.c_uint32(4)
            if _SYS["lib"].host_statistics(_SYS["host"], 3, arr, ctypes.byref(n)) != 0:   # 3 = HOST_CPU_LOAD_INFO
                return None
            user, system, idle, nice = arr
            return user + system + nice, user + system + idle + nice
        if sys.platform == "win32":
            ft = [ctypes.c_ulonglong() for _ in range(3)]
            if not ctypes.windll.kernel32.GetSystemTimes(*[ctypes.byref(x) for x in ft]):
                return None
            idle, kern, user = (x.value for x in ft)   # Kernel enthält Leerlauf
            return kern + user - idle, kern + user
        z = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        w = [int(x) for x in z]
        leer = w[3] + (w[4] if len(w) > 4 else 0)
        return sum(w) - leer, sum(w)
    except Exception:
        return None


def system_last(vorher, jetzt):
    if not vorher or not jetzt or jetzt[1] <= vorher[1]:
        return None
    return round((jetzt[0] - vorher[0]) / (jetzt[1] - vorher[1]) * 100, 1)


TOP = {"t": 0.0, "liste": []}


def top_programme():
    """Was den Computer gerade am meisten beschäftigt (alle Programme, Anteil am ganzen Computer). Nur wenn das
    Leistungs-Fenster offen ist, höchstens alle 15 s."""
    if time.time() - TOP["t"] < 15:
        return TOP["liste"]
    kerne = os.cpu_count() or 1
    liste = []
    try:
        if sys.platform == "win32":
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -notin '_Total','Idle' } | "
                                "Sort-Object PercentProcessorTime -Descending | Select-Object -First 8 | ForEach-Object { \"$($_.PercentProcessorTime)`t$($_.Name)\" }"],
                               capture_output=True, text=True, timeout=15, creationflags=0x08000000)
            zeilen = [z.split("\t", 1) for z in r.stdout.splitlines() if "\t" in z]
        else:
            r = subprocess.run(["ps", "-A", "-o", "%cpu=,comm="], capture_output=True, text=True, timeout=5)
            zeilen = [z.strip().split(None, 1) for z in r.stdout.splitlines() if z.strip()]
        summe = {}
        for z in zeilen:
            if len(z) != 2:
                continue
            name = z[1].strip().rsplit("/", 1)[-1].split("#")[0]
            name = {"Python": "Python (BlockEm u. a.)", "python3": "Python (BlockEm u. a.)"}.get(name, name)
            summe[name] = summe.get(name, 0.0) + float(z[0].replace(",", "."))
        liste = [{"name": n, "anteil": round(v / kerne, 1)} for n, v in sorted(summe.items(), key=lambda x: -x[1])[:6] if v / kerne >= 0.05]
    except Exception:
        liste = []
    TOP.update(t=time.time(), liste=liste)
    return liste


def _cputime(t):
    """„1:02.34“ oder „1:02:03.45“ (ps) in Sekunden."""
    sek = 0.0
    for teil in t.strip().replace("-", ":").split(":"):
        sek = sek * 60 + float(teil.replace(",", "."))
    return sek


def helfer_cpu_messen():
    """Rechenzeit der dauerhaft laufenden Helfer: der Fenster-Helfer (Mac: osascript, Windows: PowerShell) und am Mac
    System Events, das ihm antwortet. Beide tauchen in os.times nicht auf, solange sie laufen."""
    if MODUS != "mac" or sys.platform not in ("darwin", "win32"):
        return
    werte = {}
    p = WIN.get("proc")
    lebt = p is not None and p.poll() is None
    if sys.platform == "win32":
        if lebt:
            c = win_befehl("cpu").get("cpu")
            if isinstance(c, (int, float)):
                werte[p.pid] = (LS_FENSTER, float(c))
    else:
        pids = {p.pid: LS_FENSTER} if lebt else {}
        if time.time() - LEISTUNG.get("se_t", 0) > 60:
            try:
                out = subprocess.run(["pgrep", "-x", "System Events"], capture_output=True, text=True, timeout=3).stdout.split()
                LEISTUNG["se_pid"] = int(out[0]) if out else None
            except Exception:
                LEISTUNG["se_pid"] = None
            LEISTUNG["se_t"] = time.time()
        if LEISTUNG.get("se_pid"):
            pids[LEISTUNG["se_pid"]] = LS_SE
        if "app_pid" not in LEISTUNG:   # Fokus-Cockpit.app startet server-schleife.sh, die startet uns
            try:
                pp = subprocess.run(["ps", "-o", "ppid=", "-p", str(os.getppid())], capture_output=True, text=True, timeout=3).stdout.strip()
                name = subprocess.run(["ps", "-o", "comm=", "-p", pp], capture_output=True, text=True, timeout=3).stdout if pp else ""
                LEISTUNG["app_pid"] = int(pp) if pp and ("Fokus-Cockpit" in name or "BlockEm" in name) else None
            except Exception:
                LEISTUNG["app_pid"] = None
        if LEISTUNG.get("app_pid"):
            pids[LEISTUNG["app_pid"]] = LS_APP
        if pids:
            try:
                out = subprocess.run(["ps", "-o", "pid=,cputime=", "-p", ",".join(map(str, pids))],
                                     capture_output=True, text=True, timeout=3).stdout
                for z in out.splitlines():
                    pid, t = z.split()
                    werte[int(pid)] = (pids[int(pid)], _cputime(t))
            except Exception:
                pass
    with LEISTUNG["lock"]:
        h = LEISTUNG["helfer"]
        for pid, (name, c) in werte.items():
            alt = h.get(pid)
            h[pid] = c
            if alt is None:
                if name in (LS_SE, LS_APP):
                    continue   # läuft schon länger: erst ab jetzt zählen
                alt = 0.0
            d = max(0.0, c - alt)
            LEISTUNG["teile"][name] += d
            LEISTUNG["fremd"] += d


def _helfer_beenden(p):
    """Fenster-Helfer beenden. Am Mac taucht seine Zeit danach in RUSAGE_CHILDREN auf: die schon gezählte nicht doppelt."""
    try:
        p.kill()
        p.wait(timeout=3)
    except Exception:
        return
    if sys.platform != "win32":
        with LEISTUNG["lock"]:
            c = LEISTUNG["helfer"].pop(p.pid, 0.0)
            LEISTUNG["fremd"] -= c
        _leistung_abziehen(c)


def leistung_waechter():
    n = 0
    while True:
        try:
            helfer_cpu_messen()
            if n % 30 == 3:   # alle 5 Minuten (und kurz nach dem Start)
                umgebung_merken()
        except Exception:
            pass
        n += 1
        with LEISTUNG["lock"]:
            LEISTUNG["verlauf"].append((time.time(), dict(LEISTUNG["teile"]), _prozess_cpu(), _system_ticks()))
        time.sleep(10)


def leistung_daten():
    """Prozent eines Prozessorkerns (wie die Aktivitätsanzeige am Mac) für die letzte Minute und die letzten 10 Minuten."""
    with LEISTUNG["lock"]:
        v = list(LEISTUNG["verlauf"])
        jetzt = (time.time(), dict(LEISTUNG["teile"]), _prozess_cpu(), _system_ticks())
    v.append(jetzt)

    def spanne(sek):
        alt = next((x for x in v if jetzt[0] - x[0] <= sek + 5), v[0])
        dt = max(1e-6, jetzt[0] - alt[0])
        teile = {n: (c - alt[1].get(n, 0.0)) / dt * 100 for n, c in jetzt[1].items()}
        gesamt = (jetzt[2] - alt[2]) / dt * 100
        return teile, gesamt, dt, system_last(alt[3] if len(alt) > 3 else None, jetzt[3])

    t1, g1, d1, s1 = spanne(60)
    t10, g10, d10, s10 = spanne(600)
    namen = sorted(set(t1) | set(t10), key=lambda n: -t10.get(n, 0))
    zeilen = [{"name": n, "jetzt": round(t1.get(n, 0), 2), "zehn": round(t10.get(n, 0), 2)} for n in namen]
    rest1, rest10 = max(0.0, g1 - sum(t1.values())), max(0.0, g10 - sum(t10.values()))
    zeilen.append({"name": "Sonstiges (Start, Python selbst)", "jetzt": round(rest1, 2), "zehn": round(rest10, 2)})
    ram = None
    try:
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=3).stdout
            ram = round(int(out.strip()) / 1024)
    except Exception:
        pass
    l = BILD_KI.get("letzt") or {}
    handys = []
    try:
        for dv in handy_devices():
            lv = dv.get("leistung") or []
            if not lv or time.time() - lv[-1]["t"] > 6 * 3600:
                continue
            neu, k = lv[-1], max(1, int(lv[-1].get("kerne") or 8))
            alt = next((x for x in reversed(lv[:-1]) if x["seitMs"] < neu["seitMs"] and neu["seitMs"] - x["seitMs"] >= 5 * 60000), None)
            if alt:   # seit der vorletzten Lieferung (App lief durch)
                anteil = (neu["cpuMs"] - alt["cpuMs"]) / (neu["seitMs"] - alt["seitMs"]) * 100 / k
                spanne = round((neu["seitMs"] - alt["seitMs"]) / 60000)
            else:     # seit dem Start der App
                anteil = neu["cpuMs"] / max(1, neu["seitMs"]) * 100 / k
                spanne = round(neu["seitMs"] / 60000)
            handys.append({"name": dv.get("name"), "anteil": round(anteil, 2), "minuten": spanne, "kerne": k,
                           "bildGeprueft": neu.get("bildGeprueft"), "bildGespart": neu.get("bildGespart"), "bildMs": neu.get("bildMs"),
                           "vorMin": round((time.time() - neu["t"]) / 60)})
    except Exception:
        pass
    kurz = next((x for x in reversed(v[:-1]) if jetzt[0] - x[0] >= 8), None)   # letzte ~10 s, wie die Aktivitätsanzeige
    return {"systemJetzt": system_last(kurz[3] if kurz and len(kurz) > 3 else None, jetzt[3]), "system1": s1, "system10": s10,
            "top": top_programme(), "pc": pc_name(), "handys": handys, "kerne": os.cpu_count(), "gesamtJetzt": round(g1, 2), "gesamtZehn": round(g10, 2), "sekunden": [round(d1), round(d10)],
            "teile": zeilen, "ramMb": ram, "bildKiMs": l.get("ms"), "bildKiGeprueft": BILD_KI["pruefungen"], "bildKiGespart": BILD_KI["gespart"],
            "hinweis": ""}


# ---------- 3.18: Eigener Zähler statt ActivityWatch (john 01.11.: „auf dem Mac ohne ActivityWatch auskommen“) ----------
# Alle 5 Sekunden: welches Fenster vorne ist (App + Fenstertitel) und ob jemand am Gerät ist (3 Min ohne Eingabe = weg).
# Gleiches wird verlängert wie bei ActivityWatch. Gespeichert pro Tag in zaehler/JJJJ-MM-TT.jsonl, das laufende Stück in
# zaehler/offen.json. Ältere Tage ohne eigene Daten kommen weiter aus ActivityWatch, solange es installiert ist.
ZAEHLER_DIR = DATEN / "zaehler"
ZAEHLER = {"lock": threading.Lock(), "offen": {}, "fehler": "", "puls": 0.0, "cache": {}, "seit": None}
ZAEHLER_TAKT, ZAEHLER_WEG = 5, 180


def zaehler_an():
    return MODUS == "mac" and sys.platform in ("darwin", "win32") and CFG.get("zaehler", "eigen") != "activitywatch"


def zaehler_seit():
    """Zeitpunkt (UTC), ab dem der eigene Zähler Daten hat."""
    if ZAEHLER["seit"] is None:
        f = ZAEHLER_DIR / "seit.txt"
        try:
            ZAEHLER["seit"] = datetime.fromisoformat(f.read_text("utf-8").strip())
        except (OSError, ValueError):
            jetzt = datetime.now(timezone.utc)
            if zaehler_an():
                ZAEHLER_DIR.mkdir(parents=True, exist_ok=True)
                f.write_text(jetzt.isoformat(), "utf-8")
            ZAEHLER["seit"] = jetzt
    return ZAEHLER["seit"]


def _zaehler_schreiben(art, ev):
    tag = datetime.fromtimestamp(ev["s"]).date().isoformat()
    ZAEHLER_DIR.mkdir(parents=True, exist_ok=True)
    with open(ZAEHLER_DIR / f"{tag}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"b": art, **ev}, ensure_ascii=False) + "\n")


def zaehler_melden(art, daten, jetzt, ab=None):
    """Herzschlag wie bei ActivityWatch: gleiche Daten und keine Lücke → verlängern, sonst neues Stück."""
    with ZAEHLER["lock"]:
        o = ZAEHLER["offen"].get(art)
        if o and o["d"] == daten and jetzt - o["e"] <= ZAEHLER_TAKT * 3:
            o["e"] = jetzt
        else:
            if o:
                o["e"] = min(o["e"], ab) if ab and ab > o["s"] else o["e"]
                if o["e"] > o["s"]:
                    _zaehler_schreiben(art, o)
            beginn = ab if ab and (not o or ab >= o["e"]) else jetzt
            ZAEHLER["offen"][art] = {"s": beginn, "e": jetzt, "d": daten}
        try:
            (ZAEHLER_DIR / "offen.json").write_text(json.dumps(ZAEHLER["offen"], ensure_ascii=False), "utf-8")
        except OSError:
            pass


def zaehler_tag_lesen(tag):
    f = ZAEHLER_DIR / f"{tag}.jsonl"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return []
    c = ZAEHLER["cache"].get(tag)
    if c and c[0] == mt:
        return c[1]
    out = []
    with open(f, encoding="utf-8", errors="replace") as fh:
        for z in fh:
            try:
                out.append(json.loads(z))
            except ValueError:
                pass
    ZAEHLER["cache"][tag] = (mt, out)
    return out


def zaehler_events(art, start, end):
    """Wie events() für ActivityWatch: [(start, ende, daten)]. Vor dem Start des eigenen Zählers aus ActivityWatch."""
    b = "f" if art == "fenster" else "a"
    out = []
    seit = zaehler_seit()
    if start < seit:
        try:
            w, a, _ = aw_buckets()
            aw_id = w if art == "fenster" else a
            if aw_id:
                q = f"?start={quote(start.isoformat())}&end={quote(min(end, seit).isoformat())}&limit=-1"
                for e in aw_get(f"/buckets/{quote(aw_id)}/events{q}"):
                    s = max(parse_ts(e["timestamp"]), start)
                    t = min(parse_ts(e["timestamp"]) + timedelta(seconds=e["duration"]), end, seit)
                    if t > s:
                        out.append((s, t, e.get("data", {})))
        except Exception:
            pass   # ActivityWatch nicht (mehr) da: ältere Tage bleiben leer
    t0, t1 = start.timestamp(), end.timestamp()
    tag = (start - timedelta(days=1)).astimezone().date()
    stuecke = []
    while tag <= end.astimezone().date():
        stuecke += [x for x in zaehler_tag_lesen(tag.isoformat()) if x.get("b") == b]
        tag += timedelta(days=1)
    with ZAEHLER["lock"]:
        o = ZAEHLER["offen"].get(b)
        if o:
            stuecke.append(dict(o))
    for x in stuecke:
        s, t = max(x["s"], t0), min(x["e"], t1)
        if t > s:
            out.append((datetime.fromtimestamp(s, timezone.utc), datetime.fromtimestamp(t, timezone.utc), x.get("d") or {}))
    out.sort(key=lambda x: x[0])
    return out


MAC_FENSTER_SKRIPT = """tell application "System Events"
    set p to first process whose frontmost is true
    set n to name of p
    set t to ""
    try
        set t to name of front window of p
    end try
end tell
return n & linefeed & t"""


def zaehler_messen():
    """(App, Fenstertitel, Sekunden ohne Eingabe) oder None. Aus dem gemeinsamen Fenster-Blick (3.19)."""
    v = vorne(4.0)
    if not v or not (v.get("app") or v.get("h")):
        return None
    return v["app"], v["titel"], v["leerlauf"]


# ---------- 3.19: Ein gemeinsamer Blick aufs vorderste Fenster für Sperre, Zähler und Bild-KI ----------
# Vorher fragte jeder Teil selbst, mit je 1–3 neuen osascript-Prozessen (zusammen 3–6 alle 3 Sekunden, das meiste der
# Prozessor-Last, john 01.11.: „Das ist schon sehr viel“). Jetzt: ein dauerhaft laufender Helfer (Mac fenster-mac.js,
# Windows fenster-waechter-windows.ps1) und ein kurzer Zwischenspeicher. Klappt der Helfer am Mac nicht, der alte Weg.
VORNE = {"lock": threading.Lock(), "t": 0.0, "v": None, "quelle": "", "io": (0.0, 0.0)}


def vorne(max_alter=2.5):
    """{"app", "titel", "adresse", "leerlauf" (s), "h" (nur Windows)} des vordersten Fensters oder None."""
    with VORNE["lock"]:
        if time.time() - VORNE["t"] < max_alter:
            return VORNE["v"]
        with leistung(LS_FENSTER):
            try:
                v = _vorne_holen()
            except Exception as e:
                VORNE["fehler"] = str(e)[:200]
                v = None
        VORNE.update(t=time.time(), v=v)
        return v


def _vorne_holen():
    if sys.platform == "win32":
        v = win_befehl("vorn", browser=sorted(WIN_BROWSER))
        if not v.get("h"):
            return None
        WIN["h"] = v["h"]
        VORNE["quelle"] = "Helfer"
        return {"h": v["h"], "app": str(v.get("app") or ""), "titel": str(v.get("titel") or ""),
                "adresse": str(v.get("adresse") or ""), "leerlauf": float(v.get("leerlauf") or 0) / 1000}
    if sys.platform != "darwin":
        return None
    v = win_befehl("vorn")
    if v.get("app"):
        leer = float(v.get("leerlauf", -1))
        VORNE["quelle"] = "Helfer"
        return {"app": str(v["app"]).strip(), "titel": str(v.get("titel") or "").strip(), "adresse": str(v.get("adresse") or "").strip(),
                "leerlauf": leer / 1000 if leer >= 0 else _mac_leerlauf()}
    VORNE["quelle"] = "einzeln"
    return _vorne_alt_mac()


def _mac_leerlauf():
    """Sekunden ohne Eingabe über ioreg (nur wenn der Helfer es nicht weiß), höchstens alle 15 s neu gefragt."""
    t, leer = VORNE["io"]
    if time.time() - t < 15:
        return leer + (time.time() - t)
    leer = 0.0
    try:
        io = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r", "-k", "HIDIdleTime"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', io)
        leer = int(m.group(1)) / 1e9 if m else 0.0
    except Exception:
        pass
    VORNE["io"] = (time.time(), leer)
    return leer


def _vorne_alt_mac():
    """Alter Weg (bis 3.18): einzelne osascript-Aufrufe."""
    r = subprocess.run(["osascript", "-e", MAC_FENSTER_SKRIPT], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        ZAEHLER["fehler"] = sperre_fehlertext(r.stderr, "System Events")
        return None
    app, _, titel = r.stdout.rstrip("\n").partition("\n")
    app = app.strip()
    adresse = browser_adresse(app)
    if not titel.strip() and any(k in app.lower() for k in BROWSER_ADRESSE):
        # ohne Bedienungshilfen-Erlaubnis kein Fenstertitel: Tab-Titel direkt beim Browser holen (Automation reicht)
        k = next(k for k in BROWSER_ADRESSE if k in app.lower())
        prog = {"safari": "Safari", "chrome": "Google Chrome", "arc": "Arc", "brave": "Brave Browser", "edge": "Microsoft Edge"}[k]
        tab = "current tab" if k == "safari" else "active tab"
        eig = "name" if k == "safari" else "title"
        try:
            rt = subprocess.run(["osascript", "-e", f'tell application "{prog}" to get {eig} of {tab} of front window'],
                                capture_output=True, text=True, timeout=3)
            titel = rt.stdout.strip() if rt.returncode == 0 else ""
        except Exception:
            titel = ""
    return {"app": app, "titel": titel.strip(), "adresse": adresse, "leerlauf": _mac_leerlauf()}


def zaehler_waechter():
    """Läuft am Mac und Windows-PC im Hintergrund, solange zaehler_an()."""
    ZAEHLER_DIR.mkdir(parents=True, exist_ok=True)
    zaehler_seit()
    try:   # Stück vom letzten Lauf (Absturz, Neustart) übernehmen
        alt = json.loads((ZAEHLER_DIR / "offen.json").read_text("utf-8"))
        for art, o in alt.items():
            if o.get("e", 0) > o.get("s", 0):
                _zaehler_schreiben(art, o)
        (ZAEHLER_DIR / "offen.json").write_text("{}", "utf-8")
    except (OSError, ValueError):
        pass
    while True:
        try:
            with leistung("Zähler (Bildschirmzeit)"):
                m = zaehler_messen()
                jetzt = time.time()
                if m:
                    app, titel, leer = m
                    weg = leer >= ZAEHLER_WEG
                    zaehler_melden("a", {"status": "afk" if weg else "not-afk"}, jetzt, ab=(jetzt - leer) if weg else None)
                    zaehler_melden("f", {"app": app, "title": titel}, jetzt)
                    ZAEHLER["fehler"] = ""
                ZAEHLER["puls"] = jetzt
        except Exception as e:
            ZAEHLER["fehler"] = f"Zähler: {e}"[:200]
        time.sleep(ZAEHLER_TAKT)


def merge(intervals):
    out = []
    for s, t in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], t)
        else:
            out.append([s, t])
    return out


KATEGORIEN = ("produktiv", "neutral", "prokrastination")


KONSUM_CACHE = {"zeit": 0.0, "namen": frozenset()}


def konsum_namen():
    """john 05.11.: Apps aus den Listen Social Media und Streaming sind immer Konsum (Prokrastination), auch wenn jemand sie anders einordnet."""
    if time.time() - KONSUM_CACHE["zeit"] > 30:
        try:
            k = kategorien_daten()
            KONSUM_CACHE["namen"] = frozenset(str(x).strip().lower() for x in (k.get("social") or []) + (k.get("streaming") or []) if str(x).strip())
        except Exception:
            pass
        KONSUM_CACHE["zeit"] = time.time()
    return KONSUM_CACHE["namen"]


# john 19.11.: Ruhezeit „Konsum-Apps begrenzen“ sperrt nur eindeutig als Konsum eingestufte Apps. Eindeutig = Listen Social Media/Streaming,
# benannte Konsum-Apps aus kategorien.json (ohne Wortstämme wie „spiel“/„game“, die würden auch Bahn- oder Lern-Apps treffen) und was der
# Nutzer selbst als Konsum eingeordnet hat. Unbekannte und neue Apps bleiben offen, WhatsApp steht in keiner Liste.
KONSUM_STAEMME = {"spiel", "game", "clash"}


def konsum_liste(a=None):
    k = kategorien_daten(a)
    try:
        kat = load_json("kategorien.json")
    except Exception:
        kat = {}
    eigene = kat.get("eigene") or {}
    namen, gesehen = [], set()
    def dazu(n):
        n = " ".join(str(n).split())
        if len(re.sub(r"[^\w]", "", n)) >= 2 and n.lower() not in gesehen and eigene.get(n.lower(), "prokrastination") == "prokrastination":
            gesehen.add(n.lower()); namen.append(n)
    for n in (k.get("social") or []) + (k.get("streaming") or []):
        dazu(n)
    for n in (kat.get("prokrastination") or {}).get("apps") or []:
        if len(n) >= 4 and n.lower() not in KONSUM_STAEMME:
            dazu(n[:1].upper() + n[1:])
    for n, v in eigene.items():
        if v == "prokrastination":
            dazu(n[:1].upper() + n[1:])
    return namen


def locker_wirksame_apps(r, konsum=None):
    """Apps, die eine Regel wirklich sperrt: bei „Konsum-Apps begrenzen“ alle Konsum-Apps ohne die erlaubten, plus selbst dazugenommene (z. B. WhatsApp)."""
    if not r.get("konsum"):
        return list(r.get("apps") or [])
    erl = {str(x).lower() for x in r.get("erlaubt") or []}
    out = [n for n in (konsum_liste() if konsum is None else konsum) if n.lower() not in erl]
    return out + [x for x in r.get("apps") or [] if x.lower() not in {n.lower() for n in out}]


def classify(app, title, name=None):
    konsum = konsum_namen()
    if (name or "").strip().lower() in konsum or (app or "").strip().lower() in konsum:
        return "prokrastination"
    kat = load_json("kategorien.json")   # bei jedem Aufruf neu, damit Änderungen sofort greifen
    eigen = kat.get("eigene", {}).get((name or "").strip().lower())   # selbst eingeordnet in BlockEm (Reiter Werte)
    if eigen in KATEGORIEN:
        return eigen
    app, title = (app or "").lower(), (title or "").lower()
    for k in ("prokrastination", "produktiv"):
        if any(w.lower() in title for w in kat[k].get("titel", [])):
            return k
    for k in ("prokrastination", "produktiv"):
        if any(w.lower() in app for w in kat[k].get("apps", [])):
            return k
    return "neutral"


def kategorie_setzen(data):
    """{"name": "YouTube", "kat": "neutral"}; kat None = wieder automatisch nach den Stichwörtern."""
    name, k = str(data.get("name") or "").strip(), data.get("kat")
    if not name or (k is not None and k not in KATEGORIEN):
        raise ValueError("Unbekannte Kategorie")
    if k != "prokrastination" and schutz_pc.konto_sperre():   # Plan B: während der Sperre nichts zu „Neutral“ umstufen
        raise ValueError("Während der Sperre lässt sich nichts umstufen.")
    if k in ("produktiv", "neutral") and name.lower() in konsum_namen():
        raise ValueError(f"{name} steht auf der Liste Social Media/Streaming und zählt immer als Konsum.")
    # 24.11. (john): raus aus „Ablenkung“ ist Lockern und wartet wie jede Lockerung (nur diese eine App), rein geht sofort
    if k != "prokrastination" and classify(name, "", name) == "prokrastination":
        with AUTOMATIK_LOCK:
            a = automatik_load()
            l = a.setdefault("locker", {})
            if locker_warte_min(l):
                ab = time.time() + locker_warte_jetzt(l) * 60
                was = {"produktiv": "Produktiv", "neutral": "Neutral"}.get(k, "automatisch eingeordnet")
                l.setdefault("geplant", {})["kat:" + name.lower()] = {"kat": {"name": name, "kat": k}, "ab": ab, "zeit": time.time(), "satz": f"{name} zählt als {was}"}
                j, w, _ = date.today().isocalendar()
                l["lockernWoche"] = {"kw": f"{j}-{w}", "n": locker_woche_n(l) + 1}
                automatik_save(a)
                sperre_protokoll(f"App Locker: {name} soll {was} werden, gilt {zeit_text(ab)}")
                return f"Beantragt. {name} zählt {zeit_text(ab).replace(', ', ' ab ', 1)} als {was}, bis dahin bleibt es Ablenkung."
    with AUTOMATIK_LOCK:   # sofort gültige Wahl (z. B. zurück auf Ablenkung) beendet einen offenen Antrag für diese App
        a = automatik_load()
        l = a.get("locker") or {}
        if "kat:" + name.lower() in locker_geplant_offen(l):
            l["geplant"]["kat:" + name.lower()] = {"ab": 0, "zeit": time.time()}
            automatik_save(a)
            sperre_protokoll(f"App Locker: Umstufen von {name} abgebrochen")
    kategorie_schreiben(name, k)
    return ""


def kategorie_schreiben(name, k, merker=None):
    with LOCK:
        kat = load_json("kategorien.json")
        if merker:
            kat.setdefault("geplantErledigt", {})[merker[0]] = merker[1]
        eigene = kat.setdefault("eigene", {})
        if k:
            eigene[name.lower()] = k
        else:
            eigene.pop(name.lower(), None)
        (DATEN / "kategorien.json").write_text(json.dumps(kat, ensure_ascii=False, indent=2) + "\n", "utf-8")
        CACHE.clear()   # alle Tage neu rechnen, auch die Woche


def label(app, title):
    if any(b in (app or "").lower() for b in BROWSERS) and title:
        return title.split(" - ")[-1].split(" – ")[-1].strip()[:40] or app
    return app or "Unbekannt"


def add_span(hours, idx, s, t):
    """Verteilt die Sekunden von s bis t auf die Stunden-Spalten (lokale Zeit)."""
    while s < t:
        local = s.astimezone()
        nxt = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
        part = min(t, nxt)
        if local.date() == hours["day"]:
            hours["h"][local.hour][idx] += (part - s).total_seconds()
        s = part


def compute_day(day):
    tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, datetime.min.time(), tz).astimezone(timezone.utc)
    end = min(start + timedelta(days=1), datetime.now(timezone.utc))
    res = {"date": day.isoformat(), "quelle": "eigen" if zaehler_an() else "activitywatch", "stand": datetime.now().astimezone().isoformat(timespec="seconds"),
           "summe": {"produktiv": 0, "prokrastination": 0, "neutral": 0, "afk": 0, "handy": 0},
           "stunden": [[0, 0, 0, 0] for _ in range(24)], "apps": []}
    if end <= start:
        return res
    try:
        win_id, afk_id, phone_ids = pick_buckets()
    except Exception:
        win_id, afk_id, phone_ids = None, None, []
    hours = {"day": day, "h": res["stunden"]}
    idx = {"produktiv": 0, "neutral": 1, "prokrastination": 2}
    apps = {}
    geraete = {}

    eigene = load_json("kategorien.json").get("eigene", {})
    erk = erkennung_lesen()
    proj, offen, erste = {}, {}, {}
    proj_min, werkzeug = {}, []   # Projektzeit pro Minute, Werkzeug-Zeiträume (werden am Ende dem Projekt drumherum zugebucht)
    try:
        erz_km = erz_kat_apps()   # Erziehungs-System (02.11.): Social Media, Video, weitere Apps
    except Exception:
        erz_km = {}
    erz_iv = {}
    oe_iv = {}   # 1.0: (Gerät, App) → Zeiträume, daraus Öffnungen (Lücke bis 60 s zählt noch zur selben) und Ø Dauer

    def count(s, t, data, name=None, dev="Mac", schluessel=None):
        name = name or label(data.get("app"), data.get("title"))
        short = name[:-len(" (Handy)")] if name.endswith(" (Handy)") else name
        k = classify(data.get("app"), data.get("title"), short)
        sec = (t - s).total_seconds()
        res["summe"][k] += sec
        # Projekt automatisch erkennen (Stichwörter aus „Claude Memory/Projekt-Erkennung.md“)
        pr = projekt_von(erk, data.get("app"), data.get("title"))
        if pr:
            proj[pr] = proj.get(pr, 0) + sec
            minuten_buchen(proj_min, s, t, pr)
        elif ist_werkzeug(erk, data.get("app"), data.get("title"), schluessel):
            werkzeug.append((s, t, sec))
        elif k == "produktiv":
            sk = schluessel or fenster_schluessel(data.get("app"), data.get("title"))
            if sk.lower() not in erk["ignorieren"]:
                offen[sk] = offen.get(sk, 0) + sec
        erste[short.lower()] = min(erste.get(short.lower(), s), s)
        ek = erz_kat_von(erz_km, data.get("app"), f"{data.get('title') or ''} {short}") if erz_km else None
        if ek:
            erz_iv.setdefault((dev, ek), []).append((s, t))
        add_span(hours, idx[k], s, t)
        a = apps.setdefault(name, {"name": name, "kat": k, "sek": 0, "eigen": short.lower() in eigene})
        a["sek"] += sec
        g = geraete.setdefault(dev, {"name": dev, "sek": 0, "produktiv": 0, "neutral": 0, "prokrastination": 0, "apps": {}})
        g["sek"] += sec
        g[k] += sec
        add_span({"day": day, "h": g.setdefault("stunden", [[0, 0, 0, 0] for _ in range(24)])}, idx[k], s, t)   # 1.0.2: Tagesverlauf je Gerät
        ga = g["apps"].setdefault(short, {"name": short, "kat": k, "sek": 0, "eigen": short.lower() in eigene})
        ga["sek"] += sec
        oe_iv.setdefault((dev, short), []).append((s, t))
        return sec

    active = []
    if afk_id:
        for s, t, d in events(afk_id, start, end):
            if d.get("status") == "not-afk":
                active.append((s, t))
            else:
                res["summe"]["afk"] += (t - s).total_seconds()
                add_span(hours, 3, s, t)
    active = merge(active)
    if win_id:
        i = 0
        for s, t, d in events(win_id, start, end):
            while i < len(active) and active[i][1] <= s:
                i += 1
            j = i
            while j < len(active) and active[j][0] < t:
                a, b = max(s, active[j][0]), min(t, active[j][1])
                if b > a:
                    count(a, b, d)
                j += 1
    for pid in phone_ids:
        for s, t, d in events(pid, start, end):
            res["summe"]["handy"] += count(s, t, {"app": d.get("app") or d.get("package"), "title": d.get("classname", "")}, dev="Android (ActivityWatch)")
    for dev in handy_devices():
        for pkg, name, s_ms, t_ms in dev["tage"].get(day.isoformat(), []):
            s = max(datetime.fromtimestamp(s_ms / 1000, timezone.utc), start)
            t = min(datetime.fromtimestamp(t_ms / 1000, timezone.utc), end)
            if t > s:
                res["summe"]["handy"] += count(s, t, {"app": name, "title": pkg}, f"{name} (Handy)", dev["name"], schluessel=name)
    res["summe"] = {k: round(v) for k, v in res["summe"].items()}
    werk_offen = 0
    for s, t, sec in werkzeug:
        pr = projekt_drumherum(proj_min, s, t)
        if pr:
            proj[pr] = proj.get(pr, 0) + sec
        else:
            werk_offen += sec
    res["werkzeugOhne"] = round(werk_offen)
    res["projekte"] = [{"name": n, "sek": round(v)} for n, v in sorted(proj.items(), key=lambda x: -x[1])]
    res["offen"] = [{"name": n, "sek": round(v)} for n, v in sorted(offen.items(), key=lambda x: -x[1]) if v >= 120][:8]
    res["erste"] = {n: v.astimezone().strftime("%H:%M") for n, v in erste.items()}
    try:
        res["erz"] = erz_messen(erz_iv)
    except Exception as e:
        print("Erziehung messen:", e)
    oe_app = {}
    for (dev, short), liste in oe_iv.items():
        liste.sort()
        n, letzte = 0, None
        for s, t in liste:
            if letzte is None or (s - letzte).total_seconds() > 60:
                n += 1
            letzte = max(letzte, t) if letzte else t
        ga = geraete[dev]["apps"].get(short)
        if ga:
            ga["oeffnen"] = n
            ga["proOeffnung"] = round(ga["sek"] / n) if n else 0
        oe_app[short.lower()] = oe_app.get(short.lower(), 0) + n
    for a in apps.values():
        short = a["name"][:-len(" (Handy)")] if a["name"].endswith(" (Handy)") else a["name"]
        n = oe_app.get(short.lower(), 0)
        a["oeffnen"] = n
        a["proOeffnung"] = round(a["sek"] / n) if n else 0
    res["stunden"] = [[round(x) for x in h] for h in res["stunden"]]
    res["apps"] = sorted(({**a, "sek": round(a["sek"])} for a in apps.values()), key=lambda a: -a["sek"])[:12]
    res["geraete"] = [{**{k: [[round(x) for x in h] for h in v] if k == "stunden" else round(v) if isinstance(v, float) else v for k, v in g.items() if k != "apps"},
                       "apps": sorted(({**a, "sek": round(a["sek"])} for a in g["apps"].values()), key=lambda a: -a["sek"])[:25]}
                      for g in sorted(geraete.values(), key=lambda g: (g["name"] != "Mac", g["name"]))]
    return res


# ---------- Handy ----------

def handy_devices():
    if not HANDY_DIR.is_dir():
        return []
    out = []
    for f in sorted(HANDY_DIR.glob("*.json")):
        try:
            out.append(handy_lesen(f))
        except Exception:
            pass
    return out


HANDY_LOCK = threading.Lock()   # 03.11.: zwei Syncs eines Handys gleichzeitig ließen die Datei kaputt zurück (Rest hinten dran)


def handy_lesen(f):
    """Liest eine Handy-Datei; hängt hinten noch Rest von einem alten Schreibfehler, zählt nur das erste JSON."""
    try:
        return json.loads(f.read_text("utf-8"))
    except json.JSONDecodeError:
        return json.JSONDecoder().raw_decode(f.read_text("utf-8"))[0]


def handy_save(data):
    with HANDY_LOCK:
        return _handy_save(data)


def _handy_save(data):
    """Speichert die Nutzungs-Intervalle eines Handys. Tage im gesendeten Zeitraum werden ersetzt."""
    name = re.sub(r"[^\w\- ]", "", str(data.get("geraet", "Handy")))[:40] or "Handy"
    von = date.fromtimestamp(int(data["von"]) / 1000)
    f = HANDY_DIR / f"{name}.json"
    dev = handy_lesen(f) if f.exists() else {"name": name, "tage": {}}
    days = {}
    for pkg, label_, s, t in data.get("intervalle", []):
        d = date.fromtimestamp(int(s) / 1000)
        while True:
            day_end = int(datetime.combine(d + timedelta(days=1), datetime.min.time()).timestamp() * 1000)
            days.setdefault(d.isoformat(), []).append([str(pkg)[:120], str(label_)[:60], int(s), min(int(t), day_end)])
            if int(t) <= day_end:
                break
            s, d = day_end, d + timedelta(days=1)
    d = von
    while d <= date.today():
        dev["tage"][d.isoformat()] = days.get(d.isoformat(), [])
        with LOCK:
            CACHE.pop(d, None)
        d += timedelta(days=1)
    keep = (date.today() - timedelta(days=60)).isoformat()
    dev["tage"] = {k: v for k, v in dev["tage"].items() if k >= keep}
    dev["letzterSync"] = datetime.now().astimezone().isoformat(timespec="seconds")
    dev["version"] = str(data.get("version") or "")[:10]   # ab Handy-App 1.6: Version und ob die Fokus-Sperre an ist
    dev["sperreDienst"] = data.get("sperre") if isinstance(data.get("sperre"), bool) else None
    ls = data.get("leistung")
    if isinstance(ls, dict) and isinstance(ls.get("cpuMs"), (int, float)) and isinstance(ls.get("seitMs"), (int, float)):
        # 3.20: Rechenzeit der Handy-App (fürs Leistungs-Fenster), die letzten 30 Lieferungen
        eintrag = {"t": time.time(), **{k: ls.get(k) for k in ("cpuMs", "seitMs", "kerne", "bildGeprueft", "bildGespart", "bildMs")}}
        dev["leistung"] = (dev.get("leistung") or [])[-29:] + [eintrag]
    HANDY_DIR.mkdir(exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(dev, ensure_ascii=False), "utf-8")
    os.replace(tmp, f)   # erst fertig schreiben, dann tauschen: nie eine halbe Datei
    return sum(len(v) for v in days.values())


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def day_data(day):
    today = date.today()
    with LOCK:
        hit = CACHE.get(day)
        if hit and (day < today or time.time() - hit[0] < 30):
            return hit[1]
    res = compute_day(day)
    with LOCK:
        CACHE[day] = (time.time(), res)
    write_report(res, final=day < today)
    return res


def week():
    today = date.today()
    tage = []
    for n in range(6, -1, -1):
        r = day_data(today - timedelta(days=n))
        tage.append({"date": r["date"], **r["summe"]})
    goal = CFG["produktivZielMin"] * 60
    serie = 0
    n = 0 if day_data(today)["summe"]["produktiv"] >= goal else 1
    while n < 60 and day_data(today - timedelta(days=n))["summe"]["produktiv"] >= goal:
        serie, n = serie + 1, n + 1
    return {"tage": tage, "serie": serie}


# ---------- Obsidian ----------

def vault():
    v = CFG.get("vault") or ""
    p = Path(v).expanduser()
    return p if v and p.is_dir() else None


FRIST = re.compile(r"\s*\(\s*bis\s+([^)]+)\)", re.I)
AUFWAND = re.compile(r"\s*\(\s*(½|\d+(?:[.,]\d+)?)\s*(Tage?|Std\.?|Stunden?|h|Min\.?|Minuten)\s*\)", re.I)
STD_JE = {"t": 4, "s": 1, "h": 1, "m": 1 / 60}   # Aufwand in Stunden; ein „Tag“ zählt als 4 Std
STD_OHNE = 2   # Ziel ohne Aufwand-Angabe
PRIO = re.compile(r"\s*!(muss|soll|kann)\b", re.I)
FERTIG_WENN = re.compile(r"^\*{0,2}Fertig,?\s+wenn:?\*{0,2}:?\s*$", re.I)
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def frist_of(text):
    """„… (bis 18.10.)“, „(bis 2026-10-18)“, „(bis Ende 2027)“, „(bis Ende Oktober)“ -> ISO-Datum oder None."""
    m = FRIST.search(text or "")
    if not m:
        return None
    t, today = m.group(1).strip(), date.today()
    try:
        if x := re.search(r"(\d{4})-(\d{2})-(\d{2})", t):
            return date(int(x.group(1)), int(x.group(2)), int(x.group(3))).isoformat()
        if x := re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", t):
            return date(int(x.group(3) or today.year), int(x.group(2)), int(x.group(1))).isoformat()
        mon = next((i for i, n in enumerate(MONATE) if n.lower() in t.lower()), None)
        y = int(x.group(1)) if (x := re.search(r"(\d{4})", t)) else today.year
        if mon is not None:
            nxt = date(y + (mon == 11), (mon + 1) % 12 + 1, 1)
            return (nxt - timedelta(days=1)).isoformat()
        if re.search(r"ende", t, re.I) and x:
            return date(y, 12, 31).isoformat()
    except ValueError:
        return None
    return None


def aufwand_of(text):
    """„### Ziel (3 Std)“ / „(45 Min)“ / „(1 Tag)“ -> Aufwand in Stunden oder None."""
    m = AUFWAND.search(text or "")
    if not m:
        return None
    n = 0.5 if m.group(1) == "½" else float(m.group(1).replace(",", "."))
    return round(n * STD_JE[m.group(2)[0].lower()], 2)


def prio_of(text):
    m = PRIO.search(text or "")
    return m.group(1).lower() if m else ""


def clean(text):
    """Obsidian-Links und Code-Striche für die Anzeige entfernen: [[A|b]] -> b, [[A#Abschnitt]] -> A; Frist „(bis …)“ weg."""
    text = PRIO.sub("", AUFWAND.sub("", FRIST.sub("", text)))
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]#|]+)(?:#[^\]]*)?\]\]", r"\1", text)
    return text.replace("`", "").strip()


COCKPIT_VORLAGE = """---
typ: cockpit
aktualisiert: {d}
---
# Cockpit

Diese Notiz liest BlockEm. Aufgaben als Checkboxen unter die passende Überschrift schreiben.

## Heute

## Woche

## Monat

## Jahr
"""


def cockpit_path():
    return vault() / CFG["cockpitNotiz"]


LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
TAIL = re.compile(r"\s+[–-]\s+\[\[[^\]]+\]\]\s*$")
DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
VAULT_LOCK = threading.RLock()


def base_text(raw):
    """Aufgabentext ohne angehängtes „– [[Projekt]]“, zum Vergleichen."""
    return clean(TAIL.sub("", raw)).lower()


def project_files():
    root = vault() / CFG["projekteOrdner"]
    return {f.name[: -len(" – Aufgaben.md")]: f for f in sorted(root.rglob("* – Aufgaben.md"))} if root.is_dir() else {}


def rel(p):
    return str(p.relative_to(vault()))


def section_key(line):
    """„## Heute (Di 13.10.)“ -> "heute", „## Wochenplan“ -> "plan"; andere Überschriften -> None."""
    h = line[3:].strip().lower()
    if h.startswith("wochenplan"):
        return "plan"
    return next((k for k in ("heute", "woche", "monat", "jahr", "zeit") if h.startswith(k)), None)


def day_label(d, year=True):
    return f"{WT[d.weekday()]} {d.day:02d}.{d.month:02d}." + (str(d.year) if year else "")


def parse_day(text):
    m = DAY.search(text)
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


# ---------- Cockpit-Notiz als Abschnitte (zum Umschreiben) ----------

def note_read(p):
    head, secs, cur = [], [], None
    for line in p.read_text("utf-8").split("\n"):
        if line.startswith("## "):
            cur = {"h": line, "key": section_key(line), "body": []}
            secs.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur["body"].append(line)
    return head, secs


def note_write(p, head, secs):
    out = list(head)
    for s in secs:
        body = list(s["body"])
        while body and not body[-1].strip():
            body.pop()
        out += [s["h"], *body, ""]
    p.write_text("\n".join(out).rstrip("\n") + "\n", "utf-8")


def front_get(head, key):
    for line in head:
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    return None


def front_set(head, key, value):
    if not head or head[0] != "---":
        head[:0] = ["---", "---"]
    end = head.index("---", 1)
    for i in range(1, end):
        if head[i].startswith(key + ":"):
            head[i] = f"{key}: {value}"
            return
    head.insert(end, f"{key}: {value}")


def sec(secs, key, after=None):
    """Abschnitt holen, notfalls anlegen (hinter dem Abschnitt `after`)."""
    for s in secs:
        if s["key"] == key:
            return s
    names = {"heute": "Heute", "woche": "Woche", "plan": "Wochenplan", "monat": "Monat", "jahr": "Jahr", "zeit": "Zeit"}
    s = {"h": f"## {names[key]}", "key": key, "body": []}
    pos = next((i + 1 for i, x in enumerate(secs) if x["key"] == after), 0)
    secs.insert(pos, s)
    return s


def box(line):
    return CHECKBOX.match(line)


def is_done(line):
    m = box(line)
    return bool(m) and m.group(2) != " "


def has_task(s, line):
    k = base_text(box(line).group(4))
    return any(box(x) and base_text(box(x).group(4)) == k for x in s["body"])


def append_task(s, line):
    last = max((i for i, x in enumerate(s["body"]) if box(x)), default=-1)
    s["body"].insert(last + 1, line)


def plan_days(s):
    """Wochenplan-Abschnitt -> [(datum, [zeilen])]; Zeilen vor dem ersten Tag gehen verloren nicht: Tag None."""
    days, cur = [], None
    for line in s["body"]:
        if line.startswith("### "):
            cur = (parse_day(line), [])
            days.append(cur)
        elif line.strip():
            if cur is None:
                cur = (None, [])
                days.append(cur)
            cur[1].append(line)
    return days


def plan_days_set(s, days):
    body = []
    for d, lines in days:
        if d:
            body.append(f"### {day_label(d)}")
        body += [*lines, ""]
    s["body"] = body


ZEIT_STD = re.compile(r"(\d+(?:[.,]\d+)?)\s*Std", re.I)
ZEIT_AUFG = re.compile(r"(\d+)\s*Aufgabe", re.I)
ZEIT_STANDARD = {"std": 3, "aufgaben": 3}


def zahl(x):
    return int(x) if float(x).is_integer() else x


def zeit_werte(text):
    """„frei“ / „2 Std“ / „4 Std, 5 Aufgaben“ -> {"std": .., "aufgaben": ..} (nur was drinsteht)."""
    out = {}
    if re.search(r"\bfrei\b", text, re.I):
        out["std"] = 0
    elif m := ZEIT_STD.search(text):
        out["std"] = zahl(float(m.group(1).replace(",", ".")))
    if m := ZEIT_AUFG.search(text):
        out["aufgaben"] = int(m.group(1))
    return out


def woche_start(d=None):
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def zeit_lesen(secs):
    z = {**ZEIT_STANDARD, "tage": {}}
    s = next((x for x in secs if x["key"] == "zeit"), None)
    for line in s["body"] if s else []:
        if re.match(r"^\s*Standard:", line, re.I):
            z.update(zeit_werte(line))
        elif re.match(r"^\s*[-*]\s", line) and (d := parse_day(line)) and d >= woche_start():
            w = zeit_werte(line.split(":", 1)[1] if ":" in line else "")
            if w:
                z["tage"][d.isoformat()] = w
    return z


def zeit_zeile(w):
    teile = ["frei" if w.get("std") == 0 else f"{str(w['std']).replace('.', ',')} Std"] if "std" in w else []
    if "aufgaben" in w:
        teile.append(f"{w['aufgaben']} Aufgaben")
    return ", ".join(teile)


def zeit_schreiben(secs, z):
    s = sec(secs, "zeit", after="woche")
    s["h"] = "## Zeit"
    s["body"] = [f"Standard: {zeit_zeile({'std': z['std'], 'aufgaben': z['aufgaben']})} pro Tag"]
    s["body"] += [f"- {day_label(date.fromisoformat(d))}: {zeit_zeile(w)}" for d, w in sorted(z["tage"].items())]


def zeit_setzen(data):
    """{"standard": {"std": 3, "aufgaben": 3}} oder {"datum": "2026-10-15", "std": 0, "aufgaben": 2}; std und aufgaben None = wie Standard."""
    def std(x):
        x = round(float(x) * 2) / 2
        if not 0 <= x <= 16:
            raise ValueError("Stunden pro Tag: 0 bis 16")
        return zahl(x)

    def aufg(x):
        x = int(x)
        if not 1 <= x <= 20:
            raise ValueError("Aufgaben pro Tag: 1 bis 20")
        return x

    p = ensure_cockpit()
    with VAULT_LOCK:
        head, secs = note_read(p)
        z = zeit_lesen(secs)
        if isinstance(data.get("standard"), dict):
            st = data["standard"]
            if st.get("std") is not None:
                z["std"] = std(st["std"]) or 1
            if st.get("aufgaben") is not None:
                z["aufgaben"] = aufg(st["aufgaben"])
        elif data.get("datum"):
            d = date.fromisoformat(str(data["datum"])[:10]).isoformat()
            w = {}
            if data.get("std") is not None:
                w["std"] = std(data["std"])
            if data.get("aufgaben") is not None:
                w["aufgaben"] = aufg(data["aufgaben"])
            if w:
                z["tage"][d] = w
            else:
                z["tage"].pop(d, None)
        else:
            raise ValueError("Was soll eingestellt werden?")
        zeit_schreiben(secs, z)
        note_write(p, head, secs)


def archive(entries):
    """Erledigtes in „Claude Memory/Erledigt.md“ anhängen: entries = [(überschrift, [zeilen])]."""
    entries = [(h, ls) for h, ls in entries if ls]
    if not entries:
        return
    p = vault() / CFG.get("erledigtNotiz", mem("Erledigt.md"))
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntyp: erledigt\n---\n# Erledigt\n\nHier legt BlockEm abgehakte Aufgaben ab, wenn ein neuer Tag beginnt.\n", "utf-8")
    text = p.read_text("utf-8").rstrip("\n") + "\n"
    for h, ls in entries:
        text += f"\n## {h}\n" + "\n".join(ls) + "\n"
    p.write_text(text, "utf-8")


def ensure_cockpit():
    p = cockpit_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(COCKPIT_VORLAGE.format(d=date.today().isoformat()), "utf-8")
    return p


def rollover():
    """Neuer Tag: Erledigtes von Heute ins Archiv, geplante Tage bis heute nach Heute holen, Wochen-Überschrift nachziehen."""
    p = cockpit_path()
    if not p.exists():
        return
    today = date.today()
    with VAULT_LOCK:
        head, secs = note_read(p)
        if front_get(head, "heute") == today.isoformat():
            return
        try:
            last = date.fromisoformat(front_get(head, "heute") or "")
        except ValueError:
            last = None
        out = []
        heute = sec(secs, "heute")
        if last and last < today:
            out.append((f"{day_label(last)} (Heute)", [l for l in heute["body"] if is_done(l)]))
            heute["body"] = [l for l in heute["body"] if not is_done(l)]
        wp = next((s for s in secs if s["key"] == "plan"), None)
        if wp:
            keep = []
            for d, lines in plan_days(wp):
                if d and d <= today:
                    out.append((f"{day_label(d)} (Wochenplan)", [l for l in lines if is_done(l)]))
                    for l in lines:
                        if box(l) and not is_done(l) and not has_task(heute, l):
                            append_task(heute, l)
                else:
                    keep.append((d, lines))
            plan_days_set(wp, keep)
        woche = next((s for s in secs if s["key"] == "woche"), None)
        kw = today.isocalendar()[1]
        m = re.search(r"KW\s*(\d+)", woche["h"]) if woche else None
        if woche and m and int(m.group(1)) not in (kw, kw + 1):
            out.append((f"KW {m.group(1)} (Wochenziele)", [l for l in woche["body"] if is_done(l)]))
            woche["body"] = [l for l in woche["body"] if not is_done(l)]
            woche["h"] = f"## Woche (KW {kw})"
        monat = next((s for s in secs if s["key"] == "monat"), None)
        mname = f"{MONATE[today.month - 1]} {today.year}"
        if monat and mname not in monat["h"]:
            if re.search(r"\(\w+ \d{4}\)", monat["h"]):
                out.append((f"{monat['h'][3:].strip()} (Monatsziele)", [l for l in monat["body"] if is_done(l)]))
                monat["body"] = [l for l in monat["body"] if not is_done(l)]
            monat["h"] = f"## Monat ({mname})"
        if any(x["key"] == "zeit" for x in secs):
            zeit_schreiben(secs, zeit_lesen(secs))
        heute["h"] = f"## Heute ({day_label(today, year=False)})"
        front_set(head, "heute", today.isoformat())
        archive(out)
        note_write(p, head, secs)


PHASE = re.compile(r"^##\s+(?:Phase|Meilenstein):\s*(.+?)\s*$", re.I)
AUSSEHEN = re.compile(r"^Aussehen:[ \t]*(.*?)[ \t]*$", re.I)   # Zeile direkt unter Phase/Abschnitt: „Aussehen: 🚀 #a78bfa“
ABSCHNITT = re.compile(r"^####\s+Abschnitt:\s*(.+?)\s*$", re.I)   # Unterteilung einer Phase, z. B. „#### Abschnitt: V1“
ENDZIEL = re.compile(r"^(?:\*\*)?Endziel:(?:\*\*)?\s*(.+?)\s*$", re.I)


def phase_name(raw):
    return clean(re.sub(r"\s*\(aktiv\)\s*", " ", raw, flags=re.I).replace("✓", "")).strip()


def endziel_of(text):
    m = next((ENDZIEL.match(l) for l in text.split("\n") if ENDZIEL.match(l)), None)
    if not m:
        return None
    kopf = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]   # „Fertig, wenn:“ vor dem ersten Meilenstein gehört zum Endziel
    k = re.search(r"^\*{0,2}Fertig,?\s+wenn:?\*{0,2}:?\s*\n((?:\s*[-*]\s+(?!\[).+\n?)+)", kopf, re.M | re.I)
    kriterien = [clean(re.sub(r"^\s*[-*]\s+", "", l)) for l in k.group(1).strip("\n").split("\n")] if k else []
    v = re.search(r"^(?:\*\*)?Vision:(?:\*\*)?\s*(.+?)\s*$", kopf, re.M | re.I)
    infos = {k: clean(x.group(1)) for k in ("Drin", "Nicht drin", "Rahmen") if (x := re.search(rf"^{k}:\s*(.+?)\s*$", kopf, re.M))}
    return {"text": clean(m.group(1)), "frist": frist_of(m.group(1)), "fertigWenn": kriterien, "vision": clean(v.group(1)) if v else "", "infos": infos}


def parse_project(f):
    """<Projekt> – Aufgaben.md -> Phasen (## Phase: …) -> Ziele (### …) -> Schritte (Checkboxen).
    Checkboxen ohne Ziel-Überschrift sind Ziele mit nur einem Schritt (so funktionieren auch alte Notizen)."""
    phases, ph, goal, fw, plain, absn, kopf = [], None, None, None, None, None, None

    def implicit():
        p = {"name": "Aufgaben", "marke": False, "fertigMarke": False, "frist": None, "ergebnis": "", "fertigWenn": [], "ziele": [], "abschnitte": [], "icon": "", "farbe": ""}
        phases.append(p)
        return p

    for i, line in enumerate(f.read_text("utf-8").split("\n")):
        if line.strip() and not CHECKBOX.match(line):
            plain = None   # eingerückte Unteraufgaben gehören nur zur Aufgabe direkt darüber
        # „Fertig, wenn:“ + Spiegelstriche (ohne Kästchen): Kriterien fürs Endziel (vor dem 1. Meilenstein) oder den Meilenstein
        if FERTIG_WENN.match(line.strip()):
            fw = ph["fertigWenn"] if ph else []
            continue
        if ph and (m := re.match(r"^\*{0,2}Fertig,?\s+wenn:\*{0,2}\s*(\S.*)$", line.strip(), re.I)):
            ph["fertigWenn"].append(clean(m.group(1)))   # einzeilig: „Fertig, wenn: Satz“
            continue
        if fw is not None and (m := re.match(r"^\s*[-*]\s+(?!\[)(.+)$", line)):
            fw.append(clean(m.group(1)))
            continue
        fw = None
        if ph and (m := re.match(r"^\*{0,2}(?:Ergebnis|Ziel der Phase):\*{0,2}\s*(.+)$", line)):
            ph["ergebnis"] = clean(m.group(1))
        elif ph and (m := AUSSEHEN.match(line)):
            if kopf is not None:
                kopf.update(look_parse(m.group(1)))
        elif ph and (m := ABSCHNITT.match(line)):
            absn = clean(m.group(1))
            if absn.lower() not in [a["name"].lower() for a in ph["abschnitte"]]:
                ph["abschnitte"].append({"name": absn, "frist": frist_of(m.group(1)), "icon": "", "farbe": ""})
            kopf = next(a for a in ph["abschnitte"] if a["name"].lower() == absn.lower())
            goal = None
        elif m := PHASE.match(line):
            ph = {"name": phase_name(m.group(1)), "marke": "(aktiv)" in m.group(1).lower(),
                  "fertigMarke": "✓" in m.group(1), "frist": frist_of(m.group(1)), "ergebnis": "", "fertigWenn": [], "ziele": [], "abschnitte": [], "icon": "", "farbe": ""}
            phases.append(ph)
            goal, absn, kopf = None, None, ph
        elif line.startswith("## "):
            ph, goal, absn, kopf = None, None, None, None
        elif line.startswith("### "):
            kopf = None
            ph = ph or implicit()
            goal = {"name": clean(line[4:].replace("✓", "")), "frist": frist_of(line), "aufwand": aufwand_of(line), "prio": prio_of(line), "schritte": [], "einzel": False, "zeile": i, "abschnitt": absn}
            ph["ziele"].append(goal)
        elif m := CHECKBOX.match(line):
            ph = ph or implicit()
            step = {"id": f"{rel(f)}:{i}", "text": clean(m.group(4)), "done": m.group(2) != " ", "aufwand": aufwand_of(m.group(4)),
                    "prio": prio_of(m.group(4)), "frist": frist_of(m.group(4)), "raw": m.group(4)}
            if goal:
                goal["schritte"].append(step)
            elif plain and line[:1].isspace():
                # eingerückt unter einer einfachen Aufgabe: die Aufgabe wird zum Ziel mit Unteraufgaben
                if plain["einzel"]:
                    plain["einzel"], plain["schritte"] = False, []
                plain["schritte"].append(step)
            else:
                plain = {"name": step["text"], "frist": frist_of(m.group(4)), "aufwand": aufwand_of(m.group(4)), "prio": prio_of(m.group(4)), "schritte": [step], "einzel": True, "zeile": i, "abschnitt": absn}
                ph["ziele"].append(plain)
    for p in phases:
        for g in p["ziele"]:
            g["done"] = sum(s["done"] for s in g["schritte"])
            g["total"] = len(g["schritte"])
            g["prozent"] = round(100 * g["done"] / g["total"]) if g["total"] else 0
            g["fertig"] = g["total"] > 0 and g["done"] == g["total"]
        # Prozent nach Aufwand: ein 4-Std-Ziel zählt doppelt so viel wie ein 2-Std-Ziel (ohne Angabe: STD_OHNE)
        p["aufwand"] = sum(g["aufwand"] or STD_OHNE for g in p["ziele"])
        p["rest"] = round(sum((g["aufwand"] or STD_OHNE) * (100 - g["prozent"]) / 100 for g in p["ziele"]), 1)
        p["prozent"] = round(100 - 100 * p["rest"] / p["aufwand"]) if p["ziele"] else (100 if p["fertigMarke"] else 0)
        p["fertig"] = p["prozent"] == 100 and (not p["ziele"] or all(g["fertig"] for g in p["ziele"]))
        for a in p["abschnitte"]:
            zs = [g for g in p["ziele"] if g["abschnitt"] == a["name"]]
            a["aufwand"] = sum(g["aufwand"] or STD_OHNE for g in zs)
            a["rest"] = round(sum((g["aufwand"] or STD_OHNE) * (100 - g["prozent"]) / 100 for g in zs), 1)
            a["prozent"] = round(100 - 100 * a["rest"] / a["aufwand"]) if zs else 0
            a["fertig"] = bool(zs) and all(g["fertig"] for g in zs)
    akt = next((p for p in phases if p["marke"]), None) or next((p for p in phases if not p["fertig"]), None)
    for p in phases:
        p["aktiv"] = p is akt
    return phases


def look_of(f):
    """Farbe und Icon eines Projekts aus dem Frontmatter der Aufgaben-Notiz (farbe: "#a78bfa", icon: "🚀")."""
    fm = re.match(r"^---\n(.*?)\n---", f.read_text("utf-8"), re.S)
    out = {}
    for k in ("farbe", "icon"):
        m = re.search(rf"^{k}:[ \t]*\"?(.*?)\"?[ \t]*$", fm.group(1), re.M) if fm else None
        out[k] = m.group(1).strip() if m else ""
    return out


def look_parse(raw):
    """„🚀 #a78bfa“ -> {"icon": "🚀", "farbe": "#a78bfa"}."""
    m = re.search(r"#[0-9a-fA-F]{6}\b", raw)
    return {"farbe": m.group(0) if m else "", "icon": " ".join((raw.replace(m.group(0), "") if m else raw).split())}


def look_werte(data):
    farbe = str(data.get("farbe") or "").strip()
    icon = " ".join(str(data.get("icon") or "").replace('"', "").replace("#", "").split())[:16]
    if farbe and not re.fullmatch(r"#[0-9a-fA-F]{6}", farbe):
        raise ValueError("Ungültige Farbe")
    return farbe, icon


def look_zeile(d):
    """Für den Assistenten: ["Aussehen: …"] oder []."""
    if not isinstance(d, dict):
        return []
    try:
        farbe, icon = look_werte(d)
    except ValueError:
        farbe, icon = "", look_werte({"icon": d.get("icon")})[1]
    w = " ".join(x for x in (icon, farbe) if x)
    return [f"Aussehen: {w}"] if w else []


def look_setzen(data):
    """Icon und Farbe von Projekt (Frontmatter), Phase oder Abschnitt (Zeile „Aussehen:“ unter der Überschrift)."""
    art = data.get("art") or "projekt"
    if art == "projekt":
        return projekt_look(data)
    files = project_files()
    projekt = data.get("projekt")
    if projekt not in files:
        raise ValueError("Projekt nicht gefunden")
    farbe, icon = look_werte(data)
    with VAULT_LOCK:
        f = files[projekt]
        lines = f.read_text("utf-8").split("\n")
        if art == "phase":
            i = phase_range(lines, data.get("phase"))[0]
        elif art == "abschnitt":
            i = abschnitt_range(lines, data.get("phase"), data.get("name"))[0]
        else:
            raise ValueError("Unbekannte Art")
        w = " ".join(x for x in (icon, farbe) if x)
        da = i + 1 < len(lines) and AUSSEHEN.match(lines[i + 1])
        if w and da:
            lines[i + 1] = f"Aussehen: {w}"
        elif w:
            lines.insert(i + 1, f"Aussehen: {w}")
        elif da:
            del lines[i + 1]
        f.write_text("\n".join(lines), "utf-8")


def projekt_look(data):
    """Farbe und Icon (Emoji) eines Projekts setzen; leer = automatisch."""
    files = project_files()
    projekt = data.get("projekt")
    if projekt not in files:
        raise ValueError("Projekt nicht gefunden")
    farbe, icon = look_werte(data)
    with VAULT_LOCK:
        f = files[projekt]
        lines = f.read_text("utf-8").split("\n")
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            lines[:0] = ["---", "---"]
        end = lines.index("---", 1)
        for k, v in (("farbe", farbe), ("icon", icon)):
            i = next((x for x in range(1, end) if lines[x].startswith(k + ":")), None)
            if v and i is None:
                lines.insert(end, f'{k}: "{v}"')
                end += 1
            elif v:
                lines[i] = f'{k}: "{v}"'
            elif i is not None:
                del lines[i]
                end -= 1
        f.write_text("\n".join(lines), "utf-8")


def prozent_gesamt(phases):
    """Projekt-Prozent = erledigter Aufwand / gesamter Aufwand über alle Meilensteine (leere Meilensteine zählen nicht)."""
    a = sum(p["aufwand"] for p in phases)
    if not a:
        return round(100 * sum(p["fertigMarke"] for p in phases) / len(phases)) if phases else 0
    return round(100 - 100 * sum(p["rest"] for p in phases) / a)


def plan():
    v = vault()
    out = {"heute": [], "woche": [], "monat": [], "jahr": [], "tage": [], "projekte": [], "kw": date.today().isocalendar()[1], "profil": profil()}
    if not v:
        try:
            out["laeuft"] = laeuft_alles()   # 21.11.: Systemcheck auch ohne Vault, sonst ewig „Wird geladen …“
        except Exception:
            pass
        return out
    out["vaultName"] = v.name
    out["ki"] = ki_status()
    rollover()
    files = project_files()
    done_by_text, goals, stones = {}, {}, {}
    for name, f in files.items():
        status = "aktiv"
        main = f.with_name(name + ".md")
        if main.exists():
            m = re.search(r"^status:\s*(.+)$", main.read_text("utf-8"), re.M)
            status = re.split(r"\s+[–-]\s+", m.group(1).strip())[0] if m else status
        phases = parse_project(f)
        steps = [s for p in phases for g in p["ziele"] for s in g["schritte"]]
        for s in steps:
            done_by_text[base_text(s.pop("raw"))] = s["done"]
        for p in phases:
            stones[(name, p["name"].lower())] = p
            for g in p["ziele"]:
                goals[(name, g["name"].lower())] = g
        akt = next((p for p in phases if p["aktiv"]), None)
        offen = [g for p in ([akt] if akt else []) + phases for g in p["ziele"] if not g["fertig"]]
        aktiv = not status.lower().startswith(("idee", "pausiert", "archiv", "später"))
        out["projekte"].append({
            "name": name, "status": status, "aktiv": aktiv, "endziel": endziel_of(f.read_text("utf-8")),
            "aufwand": sum(p["aufwand"] for p in phases), "rest": round(sum(p["rest"] for p in phases), 1),
            "prozent": prozent_gesamt(phases),
            "phase": akt["name"] if akt else "", "next": offen[0]["name"] if offen else "",
            "phasen": [{k: p[k] for k in ("name", "prozent", "aktiv", "fertig", "frist", "ziele", "aufwand", "rest", "fertigWenn", "ergebnis", "abschnitte", "icon", "farbe")} for p in phases],
            "done": sum(s["done"] for s in steps), "total": len(steps), "aufgaben": steps, **look_of(f),
            "ideen": ideen_lesen(f.with_name(name + IDEEN_ENDE)), "ideenNotiz": rel(f.with_name(name + IDEEN_ENDE))})
    p = cockpit_path()
    sync = []   # Zeilen, die in BlockEm als erledigt gelten, aber noch kein [x] haben
    out["zeit"] = {**ZEIT_STANDARD, "tage": {}}
    if p.exists():
        out["zeit"] = zeit_lesen(note_read(p)[1])
        sec_, day = None, None
        for i, line in enumerate(p.read_text("utf-8").split("\n")):
            if line.startswith("## "):
                sec_ = section_key(line)
                if sec_ == "woche" and (m := re.search(r"KW\s*(\d+)", line)):
                    out["wocheKw"] = int(m.group(1))
                if sec_ == "monat":
                    out["monatName"] = (re.search(r"\(([^)]+)\)", line) or [None, ""])[1]
            elif sec_ == "plan" and line.startswith("### "):
                d = parse_day(line)
                day = {"datum": d.isoformat(), "label": day_label(d, year=False), "items": []} if d else None
                if day and d > date.today():
                    out["tage"].append(day)
            elif sec_ and sec_ != "zeit" and (m := CHECKBOX.match(line)):
                raw = m.group(4)
                projekt = next((l for l in LINK.findall(raw) if l in files), None)
                text = clean(TAIL.sub("", raw)) if projekt else clean(raw)
                own = m.group(2) != " "
                g = goals.get((projekt, text.lower())) if projekt else None
                ms = stones.get((projekt, text.lower())) if projekt and not g else None
                done = own or (g["fertig"] if g and not g["einzel"] else ms["fertig"] and bool(ms["ziele"]) if ms
                               else done_by_text.get(base_text(raw), False))
                if done and not own and sec_ in ("heute", "woche", "plan", "monat"):
                    sync.append(i)
                item = {"id": f"{CFG['cockpitNotiz']}:{i}", "text": text, "done": done, "projekt": projekt, "frist": frist_of(raw)}
                if g and not g["einzel"]:
                    item["ziel"] = {"done": g["done"], "total": g["total"], "schritte": g["schritte"]}
                    item["frist"] = item["frist"] or g["frist"]
                # Stunden fürs Stundenkonto: Angabe in der Zeile, sonst vom Ziel bzw. der Phase (Ziel ohne Angabe: STD_OHNE)
                a = aufwand_of(raw) or (g["aufwand"] if g else None)
                if a:
                    item["aufwand"] = a
                std = a or (STD_OHNE if g else ms["aufwand"] if ms else None)
                if std is not None:
                    pz = 100 if done else g["prozent"] if g else ms["prozent"] if ms else 0
                    item["std"], item["rest"] = std, round(std * (100 - pz) / 100, 2)
                if ms:
                    item["meilenstein"] = {"prozent": ms["prozent"], "done": sum(z["fertig"] for z in ms["ziele"]), "total": len(ms["ziele"]),
                                           "ziele": [{"name": z["name"], "fertig": z["fertig"], "prozent": z["prozent"]} for z in ms["ziele"]]}
                    item["frist"] = item["frist"] or ms["frist"]
                if sec_ == "plan":
                    # Tage bis heute zählen zu Heute (falls jemand nach dem Tageswechsel noch etwas einträgt)
                    if day:
                        (out["heute"] if day["datum"] <= date.today().isoformat() else day["items"]).append(item)
                else:
                    out[sec_].append(item)
    if sync:
        # Was in den Projekt-Notizen (z. B. von Claude) erledigt wurde, auch in der Cockpit-Notiz abhaken
        with VAULT_LOCK:
            lines = p.read_text("utf-8").split("\n")
            for i in sync:
                if i < len(lines) and (m := CHECKBOX.match(lines[i])) and m.group(2) == " ":
                    lines[i] = m.group(1) + "x" + m.group(3) + m.group(4)
            p.write_text("\n".join(lines), "utf-8")
    out["tage"].sort(key=lambda d: d["datum"])
    out["projekte"].sort(key=lambda p: (not p["aktiv"], p["name"]))
    try:
        out["kalender"] = kalender_frei()
    except Exception as e:
        out["kalender"] = {"fehler": f"Kalender: {e}", "frei": [], "termine": [], "ganztags": [], "tage": {}}
    out["habits"] = habits_lesen()
    habits_auto(out)
    out["habits"]["dran"] = [n for n in out["habits"]["liste"] if habit_faellig(out["habits"], n, date.today().isoformat())]
    out["konsistenz"] = konsistenz_merken(out)
    out["vorschlag"] = vorschlaege(out)
    out["tagesplan"] = tagesplan(out, out["vorschlag"])
    out["wochenplan"] = wochenplan(out)
    out["laeuft"] = laeuft_alles()
    echt_eintragen(out)
    try:
        out["zeitplan"] = zeitplan(out)
    except Exception as e:
        out["zeitplan"] = None
        print("Zeitplan:", e)
    a = automatik_load()
    hw = a.get("hinweis") or {}
    out["automatik"] = {"limitMin": a.get("limitMin"), "hinweise": a.get("hinweise", True), "schalter": {k: automatik_an(k, a) for k in SCHALTER},
                        "hinweis": hw if hw.get("datum") == date.today().isoformat() else None,
                        "abend": a.get("abend"), "sperre": {**sperre_daten(a), "fehler": SPERRE_LOG.get("fehler"), "test": SPERRE_LOG.get("test"),
                                                    "handys": {n: round(time.time() - t) for n, t in (SPERRE_LOG.get("handys") or {}).items()},
                                                    "geraete": [d.get("name") for d in handy_devices()],
                                                    "gilt": sperre_geraete(a), "alleGeraete": geraete_namen(),
                                                    "infos": {d.get("name"): {"version": d.get("version") or "", "dienst": d.get("sperreDienst")} for d in handy_devices()}}, "sicherung": a.get("sicherung"),
                        "kalenderUrl": bool(kal_einstellung(a)["liste"]),
                        "kalenderListe": [{k: x.get(k) for k in ("id", "name", "farbe", "an")} for x in kal_einstellung(a)["liste"]],
                        "kalPlatzhalter": kal_einstellung(a)["platzhalter"], "google": g_status(),
                        "tagRahmen": {k: kal_einstellung(a)[k] for k in ("von", "bis", "abziehen")}, "erkennung": erkennung_lesen()["liste"], "werkzeuge": erkennung_lesen()["werkzeugListe"], "erkennungNotiz": rel(erkennung_pfad())}
    return out


# ---------- Habits: tägliche Gewohnheiten, unabhängig von Projekten (Notiz „Claude Memory/Habits.md“) ----------
# „## Habits“: ein Spiegelstrich pro Habit. „## Log“: „- JJJJ-MM-TT: Habit, Habit“ = was an dem Tag erledigt war.
KONSISTENZ_FILE = DATEN / "konsistenz.json"


def habits_path():
    return vault() / CFG.get("habitsNotiz", mem("Habits.md"))


def habits_lesen():
    out = {"liste": [], "log": {}, "schwelle": 80, "rhythmus": {}, "auto": {}}
    p = habits_path() if vault() else None
    if not p or not p.exists():
        return out
    teil = None
    for line in p.read_text("utf-8").split("\n"):
        if line.startswith("## "):
            teil = line[3:].strip().lower()
            continue
        if teil is None and (m := re.match(r"^durchgezogen:\s*(\d+)", line)):
            out["schwelle"] = max(1, min(100, int(m.group(1))))
        m = re.match(r"^[-*]\s+(.+)$", line.strip())
        if not m:
            continue
        if teil == "habits":
            name, r = habit_rhythmus(m.group(1).strip())   # „Sport (Mo, Mi, Fr)“ / „Lesen (3x pro Woche)“
            out["liste"].append(name)
            if r:
                out["rhythmus"][name] = r
        elif teil == "log" and (d := re.match(r"^(\d{4}-\d{2}-\d{2}):\s*(.*)$", m.group(1))):
            out["log"][d.group(1)] = [x.strip() for x in d.group(2).split(",") if x.strip()]
    return out


def habits_schreiben(h):
    p = habits_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    log = "\n".join(f"- {d}: {', '.join(n)}" for d, n in sorted(h["log"].items(), reverse=True) if n)
    p.write_text(f"---\ntyp: habits\ndurchgezogen: {h.get('schwelle', 80)}\n---\n# Habits\n\nTägliche Gewohnheiten, unabhängig von Projekten. BlockEm "
                 "schreibt unter „Log“, was an welchem Tag erledigt war.\n"
                 "Hinter einem Habit in Klammern steht, wann er dran ist, z. B. „(Mo, Mi, Fr)“ oder „(3x pro Woche)“; ohne Klammer täglich.\n\n"
                 "## Habits\n" + "\n".join(f"- {n}" + (f" ({rhythmus_text(h['rhythmus'][n])})" if h.get("rhythmus", {}).get(n) else "") for n in h["liste"])
                 + "\n\n## Log\n" + log + ("\n" if log else ""), "utf-8")


def habit(data):
    if data.get("aktion") == "schwelle":   # ab wie viel Prozent ein Tag als durchgezogen zählt
        with VAULT_LOCK:
            h = habits_lesen()
            h["schwelle"] = max(1, min(100, int(data.get("wert") or 80)))
            habits_schreiben(h)
        return
    roh, rhythmus = habit_rhythmus(str(data.get("name") or ""))
    if data.get("rhythmus") is not None:   # Rhythmus ändern: "Mo, Mi, Fr" / "3x pro Woche" / "" = täglich
        rhythmus = habit_rhythmus(f"x ({data['rhythmus']})")[1] if str(data["rhythmus"]).strip() else {}
    name = " ".join(re.sub(r"[,\n]+", " ", roh).split())[:80]
    if not name:
        raise ValueError("Name fehlt")
    with VAULT_LOCK:
        h = habits_lesen()
        da = next((n for n in h["liste"] if n.lower() == name.lower()), None)
        aktion = data.get("aktion")
        if aktion == "add":
            if not da:
                h["liste"].append(name)
                if rhythmus:
                    h["rhythmus"][name] = rhythmus
        elif aktion == "rhythmus":
            if not da:
                raise ValueError("Habit nicht gefunden")
            h["rhythmus"].pop(da, None)
            if rhythmus:
                h["rhythmus"][da] = rhythmus
        elif aktion == "weg":
            if da:
                h["liste"].remove(da)
                papierkorb("Habits", [f"- {da}"])
        elif aktion == "abhaken":
            if not da:
                raise ValueError("Habit nicht gefunden")
            tag = date.fromisoformat(str(data.get("datum") or date.today().isoformat())).isoformat()
            rest = [x for x in h["log"].get(tag, []) if x.lower() != da.lower()]
            h["log"][tag] = rest + [da] if data.get("done") else rest
            if habit_auto_regel(da, list(project_files())):
                # von Hand geändert: das Cockpit hakt diesen Habit heute nicht mehr selbst um
                with AUTOMATIK_LOCK:
                    a = automatik_load()
                    hand = a.setdefault("habitsHand", {})
                    hand.setdefault(tag, {})[da] = bool(data.get("done"))
                    a["habitsHand"] = dict(sorted(hand.items())[-7:])
                    automatik_save(a)
        else:
            raise ValueError("Unbekannte Aktion")
        habits_schreiben(h)


def konsistenz_merken(p):
    """Pro Tag festhalten, wie viel von Habits und Heute-Zielen erledigt war (für die Consistency-Auswertung)."""
    try:
        k = json.loads(KONSISTENZ_FILE.read_text("utf-8"))
    except Exception:
        k = {}
    heute, h = date.today().isoformat(), p["habits"]
    erledigt = {x.lower() for x in h["log"].get(heute, [])}
    z = p.get("zeit") or {}
    std = (z.get("tage", {}).get(heute) or {}).get("std", z.get("std", 3)) or 0
    with FOKUS_LOCK:
        fokus = sum((s.get("ergebnis") or {}).get("produktiv", 0) for s in fokus_load()["sessions"] if s["start"][:10] == heute)
    # Fokuszeit zählt gegen die für heute eingeplanten Stunden (Ziele-Reiter); ein freier Tag (0 Std) zählt nicht mit
    dran = [n for n in h["liste"] if habit_faellig(h, n, heute)]   # nur Habits, die heute dran sind
    neu = {"habits": sum(1 for n in dran if n.lower() in erledigt), "habitsVon": len(dran),
           "ziele": sum(1 for t in p["heute"] if t.get("done")), "zieleVon": len(p["heute"]),
           "fokusMin": round(fokus / 60), "fokusZielMin": round(std * 60), "frei": not std}
    if k.get(heute) != neu:
        k[heute] = neu
        k = dict(sorted(k.items())[-400:])
        KONSISTENZ_FILE.write_text(json.dumps(k), "utf-8")
    ab = (date.today() - timedelta(days=60)).isoformat()
    return {d: v for d, v in k.items() if d >= ab}


# ---------- Automatik (Phase „6. Automatisierung“; was wie automatisch läuft, steht in „Browser-App Cockpit – Automatisierung.md“) ----------
AUTOMATIK_FILE = DATEN / "automatik.json"
AUTOMATIK_LOCK = threading.RLock()
TAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def automatik_load():
    try:
        return json.loads(AUTOMATIK_FILE.read_text("utf-8"))
    except Exception:
        return {}


# Schalter im Reiter „Automatik“: jede Automatik einzeln an/aus (Standard an). „hinweise“ bleibt aus Kompatibilität ein eigener Schlüssel.
SCHALTER = ("tagesplan", "mehr", "erkennung", "habits", "hinweise", "rueckblick", "jetzt", "abend", "sperre", "wochenplan", "kalender", "sicherung",
            "zeitleiste", "planfokus", "googleBloecke")


def automatik_an(name, a=None):
    if not PLANUNG and name in PLAN_SCHALTER:
        return False
    a = automatik_load() if a is None else a
    if name == "hinweise":
        return a.get("hinweise", True) is not False
    return (a.get("schalter") or {}).get(name, True) is not False


def automatik_save(a):
    AUTOMATIK_FILE.write_text(json.dumps(a, ensure_ascii=False, indent=1), "utf-8")


# Projekt-Erkennung: „Claude Memory/Projekt-Erkennung.md“, „## <Projekt>“ mit Stichwörtern als Spiegelstriche,
# „## Kein Projekt“ = Fenster, nach denen das Cockpit nicht mehr fragt. Passt ein Stichwort in App-Name oder Fenstertitel, zählt die Zeit fürs Projekt.
ERKENNUNG_KEIN = "Kein Projekt"
ERKENNUNG_VORLAGE = ("---\ntyp: automatik\n---\n# Projekt-Erkennung\n\nBlockEm bucht Bildschirmzeit automatisch auf ein Projekt, wenn ein Stichwort "
                     "unten im App-Namen oder Fenstertitel vorkommt (Groß-/Kleinschreibung egal). Der Projektname selbst zählt immer. Neue Stichwörter "
                     "entstehen, wenn du in BlockEm bei „Wozu gehörte das?“ ein Projekt wählst. Du kannst hier auch selbst welche eintragen oder löschen.\n"
                     "Unter „Werkzeuge“ stehen Programme, die zu allem gehören (Claude, Obsidian, BlockEm): ihre Zeit zählt für das Projekt, an dem du 10 Min davor "
                     "oder danach gearbeitet hast. Unter „Kein Projekt“ steht, wonach BlockEm nicht mehr fragt.\n\nMehr dazu: [[Browser-App Cockpit – Automatisierung]]\n")


def erkennung_pfad():
    return vault() / CFG.get("erkennungNotiz", mem("Projekt-Erkennung.md"))


def erkennung_lesen():
    """-> {"regeln": [(stichwort, projekt)], "ignorieren": set(), "liste": {projekt: [stichwörter]}}; längere Stichwörter zuerst."""
    out = {"regeln": [], "ignorieren": set(), "liste": {}, "werkzeuge": [w.lower() for w in WERKZEUGE_STANDARD], "werkzeugListe": list(WERKZEUGE_STANDARD)}
    v = vault()
    if not v:
        return out
    namen = list(project_files())
    p = erkennung_pfad()
    teil, werk = None, None
    if p.exists():
        for line in p.read_text("utf-8").split("\n"):
            if line.startswith("## "):
                teil = line[3:].strip()
                continue
            m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if not m or not teil:
                continue
            w = m.group(1).strip().strip("`")
            if teil.lower() == ERKENNUNG_KEIN.lower():
                out["ignorieren"].add(w.lower())
            elif teil.lower() == WERKZEUGE_TITEL.lower():
                werk = (werk or []) + [w]
            elif teil in namen:
                out["liste"].setdefault(teil, []).append(w)
    if werk is not None:   # steht der Abschnitt in der Notiz, gilt nur er
        out["werkzeugListe"], out["werkzeuge"] = werk, [w.lower() for w in werk if len(w) >= 3]
    for n in namen:
        out["regeln"].append((n.lower(), n))
        out["regeln"] += [(w.lower(), n) for w in out["liste"].get(n, []) if len(w) >= 3]
    out["regeln"].sort(key=lambda r: -len(r[0]))
    return out


def projekt_von(erk, app, title):
    text = f"{app or ''} {title or ''}".lower()
    return next((n for w, n in erk["regeln"] if w in text), None)


# Werkzeuge (Claude, Obsidian, das Cockpit …) gehören zu allem: sie zählen für das Projekt, an dem drumherum gearbeitet wird
WERKZEUGE_TITEL = "Werkzeuge"
WERKZEUGE_STANDARD = ["Claude", "ChatGPT", "Obsidian", "Cockpit", "Fokus-Cockpit", "Cockpit Handy", "BlockEm"]
WERKZEUG_FENSTER = timedelta(minutes=10)


def ist_werkzeug(erk, app, title, schluessel=None):
    text = f"{app or ''} {title or ''} {schluessel or ''}".lower()
    return any(w in text for w in erk["werkzeuge"])


def minuten_buchen(pm, s, t, pr):
    m = int(s.timestamp() // 60)
    while m * 60 < t.timestamp():
        a, b = max(s.timestamp(), m * 60), min(t.timestamp(), (m + 1) * 60)
        if b > a:
            pm.setdefault(m, {})
            pm[m][pr] = pm[m].get(pr, 0) + b - a
        m += 1


def projekt_drumherum(pm, s, t):
    """Projekt mit der meisten erkannten Zeit von 10 Min vorher bis 10 Min nachher, sonst None."""
    summe = {}
    for m in range(int((s - WERKZEUG_FENSTER).timestamp() // 60), int((t + WERKZEUG_FENSTER).timestamp() // 60) + 1):
        for pr, v in pm.get(m, {}).items():
            summe[pr] = summe.get(pr, 0) + v
    return max(summe, key=summe.get) if summe else None


def fenster_schluessel(app, title):
    """Kurzer Name fürs Nachfragen: Webseite bzw. Ordner/Dokument aus dem Fenstertitel, sonst der App-Name."""
    app = (app or "").strip()
    teile = [x.strip() for x in re.split(r"\s+[—–-]\s+", (title or "").strip()) if x.strip()]
    if not teile:
        return app or "Unbekannt"
    letzt = teile[-1]
    if len(teile) >= 2 and app and (app.lower() in letzt.lower() or letzt.lower() in app.lower()):
        letzt = teile[-2]   # „Datei — Ordner — App“: der Ordner sagt mehr als die App
    return letzt[:40]


def erkennung_setzen(data):
    """{"stichwort": "BlockEm", "projekt": "Browser-App Cockpit"}; projekt "" = kein Projekt (nicht mehr fragen)."""
    w = " ".join(str(data.get("stichwort") or "").replace("\n", " ").split())[:60]
    projekt = str(data.get("projekt") or "").strip()
    if not w:
        raise ValueError("Stichwort fehlt")
    if projekt and projekt != "*" and projekt not in project_files():
        raise ValueError("Projekt nicht gefunden")
    ziel = WERKZEUGE_TITEL if projekt == "*" else projekt or ERKENNUNG_KEIN
    with VAULT_LOCK:
        p = erkennung_pfad()
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ERKENNUNG_VORLAGE, "utf-8")
        head, secs = note_read(p)
        if ziel == WERKZEUGE_TITEL and not any(x["h"][3:].strip() == WERKZEUGE_TITEL for x in secs):
            # Abschnitt neu: erst die Standard-Werkzeuge hineinschreiben, damit sie weiter gelten
            secs.insert(0, {"h": f"## {WERKZEUGE_TITEL}", "key": None, "body": [f"- {x}" for x in WERKZEUGE_STANDARD if x.lower() != w.lower()]})
        for s in secs:   # Stichwort steht nur bei einem Projekt
            s["body"] = [l for l in s["body"] if not re.match(rf"^\s*[-*]\s+`?{re.escape(w)}`?\s*$", l, re.I)]
        s = next((x for x in secs if x["h"][3:].strip() == ziel), None)
        if not s:
            s = {"h": f"## {ziel}", "key": None, "body": []}
            pos = len(secs) if ziel == ERKENNUNG_KEIN else next((i for i, x in enumerate(secs) if x["h"][3:].strip() == ERKENNUNG_KEIN), len(secs))
            secs.insert(pos, s)
        while s["body"] and not s["body"][-1].strip():
            s["body"].pop()
        s["body"].append(f"- {w}")
        note_write(p, head, secs)
    with LOCK:
        CACHE.clear()


# Tagesplan und „Mehr machen?“: was heute noch in die Stunden passt
def tag_zeit(out, tag=None):
    """(Stunden, Aufgaben) für einen Tag. Mit Kalender: nie mehr Stunden, als im Tagesrahmen neben den Terminen frei sind."""
    z = out.get("zeit") or {}
    tag = tag or date.today().isoformat()
    t = (z.get("tage") or {}).get(tag) or {}
    std, aufg = t.get("std", z.get("std", 3)), t.get("aufgaben", z.get("aufgaben", 3))
    k = out.get("kalender") or {}
    kt = (k.get("tage") or {}).get(tag)
    if std and kt and k.get("abziehen"):
        frei = max(0, (k["bis"] - k["von"]) * 60 - kt["min"]) / 60
        if frei < std:
            std = int(frei * 2) / 2
    return std, aufg


def vorschlaege(out):
    """Offene Wochenziele, dann offene Aufgaben der aktiven Phase jedes aktiven Projekts (Muss vor Soll vor Kann, sonst Reihenfolge der Notiz)."""
    std, aufg = tag_zeit(out)
    belegt = sum((t.get("std") or 0) if t["done"] else (t.get("rest") if t.get("rest") is not None else STD_OHNE) for t in out["heute"])
    drin = {t["text"].lower() for t in out["heute"]}
    liste, gesehen = [], set()
    for t in out["woche"]:
        if not t["done"] and t["text"].lower() not in drin:
            liste.append({"text": t["text"], "projekt": t.get("projekt"), "std": t.get("rest") if t.get("rest") is not None else STD_OHNE, "woher": "Wochenziel"})
            gesehen.add(t["text"].lower())
    rang = {"muss": 0, "soll": 1, "kann": 2}
    projektaufgaben = []
    for pr in out["projekte"]:
        if not pr["aktiv"]:
            continue
        ph = next((x for x in pr["phasen"] if x["aktiv"]), None)
        for i, g in enumerate(ph["ziele"] if ph else []):
            if g["fertig"] or g["name"].lower() in drin or g["name"].lower() in gesehen:
                continue
            rest = round((g["aufwand"] or STD_OHNE) * (100 - g["prozent"]) / 100, 2)
            projektaufgaben.append((rang.get(g.get("prio") or "", 1), i, {"text": g["name"], "projekt": pr["name"], "std": rest, "woher": ph["name"]}))
    liste += [x[2] for x in sorted(projektaufgaben, key=lambda x: (x[0], x[1]))]
    return {"std": std, "aufgaben": aufg, "frei": round(max(0, std - belegt), 2), "platz": max(0, aufg - len(out["heute"])), "liste": liste[:12]}


def tagesplan(out, v):
    """Vorschlag für heute: so viele Vorschläge, wie in Stunden und Aufgaben passen. Nur bis er übernommen oder weggeklickt ist."""
    if not automatik_an("tagesplan"):
        return None
    a = automatik_load().get("tagesplan") or {}
    heute = date.today().isoformat()
    if a.get("datum") == heute or not v["std"]:
        return None
    frei, platz, wahl = v["frei"], v["platz"], []
    for x in v["liste"]:
        if platz <= 0 or frei <= 0:
            break
        if x["std"] <= frei + 1e-9:
            wahl.append(x)
            frei, platz = frei - x["std"], platz - 1
    return {"liste": wahl, "std": v["std"], "aufgaben": v["aufgaben"]} if wahl else None


def tagesplan_setzen(data):
    """{"status": "uebernommen", "heute": [{text, projekt}]} oder {"status": "weg"}."""
    if data.get("status") == "uebernommen" and data.get("heute"):
        plan_setzen({"heute": [{"text": x.get("text", ""), "projekt": x.get("projekt")} for x in data["heute"]]})
    with AUTOMATIK_LOCK:
        a = automatik_load()
        a["tagesplan"] = {"datum": date.today().isoformat(), "status": data.get("status") or "weg"}
        automatik_save(a)


# Habits: Rhythmus („Sport (Mo, Mi, Fr)“, „Lesen (3x pro Woche)“) und automatisches Abhaken, wenn der Name etwas Messbares sagt
ZEITANGABE = r"(\d+(?:[.,]\d+)?)\s*(std\.?|stunden?|h|min\.?|minuten?)"


def habit_rhythmus(text):
    """„Sport (Mo, Mi, Fr)“ -> ("Sport", {"tage": [0, 2, 4]}); „Lesen (3x pro Woche)“ -> ("Lesen", {"proWoche": 3})."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    if m:
        inhalt = m.group(2).strip().lower()
        if w := re.fullmatch(r"(\d)\s*(?:x|×|mal)\s*(?:pro|die|in der|/)?\s*woche", inhalt):
            return m.group(1).strip(), {"proWoche": max(1, min(7, int(w.group(1))))}
        tage = [t.strip()[:2] for t in re.split(r"[,\s/+]+|\bund\b", inhalt) if t.strip()]
        if tage and all(t.capitalize() in TAGE_KURZ for t in tage):
            return m.group(1).strip(), {"tage": sorted({TAGE_KURZ.index(t.capitalize()) for t in tage})}
        if inhalt in ("täglich", "jeden tag"):
            return m.group(1).strip(), {}
    return text.strip(), {}


def rhythmus_text(r):
    if r.get("tage"):
        return ", ".join(TAGE_KURZ[i] for i in r["tage"])
    if r.get("proWoche"):
        return f"{r['proWoche']}x pro Woche"
    return ""


def habit_faellig(h, name, tag):
    """Ist der Habit an dem Tag dran? Bestimmte Tage: nur an denen. x pro Woche: bis die Woche voll ist (oder wenn heute schon erledigt)."""
    r = h["rhythmus"].get(name) or {}
    d = date.fromisoformat(tag)
    if r.get("tage"):
        return d.weekday() in r["tage"]
    if r.get("proWoche"):
        mo = d - timedelta(days=d.weekday())
        heute_da = any(x.lower() == name.lower() for x in h["log"].get(tag, []))
        vorher = sum(1 for i in range(d.weekday()) if any(x.lower() == name.lower() for x in h["log"].get((mo + timedelta(days=i)).isoformat(), [])))
        return heute_da or vorher < r["proWoche"]
    return True


def std_min(zahl_, einheit):
    n = float(zahl_.replace(",", "."))
    return round(n * (1 if einheit.startswith("m") else 60))


def habit_auto_regel(name, projekte):
    """Was am Namen messbar ist: Fokuszeit, produktive Zeit, Zeit für ein Projekt, App-Limits. None = von Hand abhaken."""
    n = " ".join(name.lower().split())
    if m := re.match(r"^(?:kein|keine|keinen|nicht)\s+(.+?)(?:\s+(?:vor|bis)\s+(\d{1,2})(?:[:.](\d{2}))?(?:\s*uhr)?)?$", n):
        bis = f"{int(m.group(2)):02d}:{m.group(3) or '00'}" if m.group(2) else None
        return {"art": "ohne", "was": m.group(1).strip(), "bis": bis}
    m = re.match(rf"^(?:max\.?|maximal|höchstens)\s+{ZEITANGABE}\s+(.+)$", n) or re.match(rf"^(.+?)\s+(?:max\.?|maximal|höchstens|unter)\s+{ZEITANGABE}$", n)
    if m:
        g = m.groups()
        was, zahl_, einh = (g[2], g[0], g[1]) if re.match(r"^(?:max|maximal|höchstens)", n) else (g[0], g[1], g[2])
        return {"art": "max", "was": was.strip(), "min": std_min(zahl_, einh)}
    m = re.match(rf"^(?:mind\.?|mindestens)?\s*{ZEITANGABE}\s+(.+)$", n)
    if m:
        was, minuten = m.group(3).strip(), std_min(m.group(1), m.group(2))
        if re.match(r"^(?:fokus|im fokus|fokuszeit|deep work)", was):
            return {"art": "fokus", "min": minuten}
        if re.match(r"^(?:produktiv|produktiv arbeiten|arbeiten|arbeit)$", was):
            return {"art": "produktiv", "min": minuten}
        pr = next((p for p in projekte if p.lower() in was or was in p.lower()), None)
        if pr:
            return {"art": "projekt", "projekt": pr, "min": minuten}
    return None


def app_sekunden(day, was):
    if was in ("ablenkung", "prokrastination", "ablenkungen"):
        return day["summe"]["prokrastination"]
    if was in ("handy", "das handy", "am handy"):
        return day["summe"]["handy"]
    return sum(a["sek"] for g in day.get("geraete", []) for a in g.get("apps", []) if was in a["name"].lower())


def habit_auto_wert(r, day, fokus_min):
    """-> (geschafft, Text, fest): fest = steht für heute endgültig."""
    jetzt = datetime.now().strftime("%H:%M")
    if r["art"] in ("fokus", "produktiv", "projekt"):
        ist = (fokus_min if r["art"] == "fokus" else
               round(day["summe"]["produktiv"] / 60) if r["art"] == "produktiv" else
               round(next((p["sek"] for p in day.get("projekte", []) if p["name"] == r["projekt"]), 0) / 60))
        return ist >= r["min"], f"{ist}/{r['min']} Min", ist >= r["min"]
    ist = round(app_sekunden(day, r["was"]) / 60)
    if r["art"] == "max":
        return ist <= r["min"], f"{ist}/{r['min']} Min", ist > r["min"]
    if r["bis"]:
        erste = min((v for n, v in day.get("erste", {}).items() if r["was"] in n), default=None)
        ok = erste is None or erste >= r["bis"]
        return ok, ("frei bis " + r["bis"] if ok else f"um {erste} benutzt"), (not ok) or jetzt >= r["bis"]
    return ist < 1, ("noch nicht benutzt" if ist < 1 else f"{ist} Min benutzt"), ist >= 1


def habits_auto(out):
    """Messbare Habits für heute selbst abhaken (und wieder weg, wenn es nicht mehr stimmt). Von Hand Geändertes gilt für den Tag."""
    h = out["habits"]
    if not h["liste"] or not automatik_an("habits"):
        return
    try:
        day = day_data(date.today())
    except Exception:
        return
    heute = date.today().isoformat()
    with FOKUS_LOCK:
        fokus_min = round(sum((s.get("ergebnis") or {}).get("produktiv", 0) for s in fokus_load()["sessions"] if s["start"][:10] == heute) / 60)
    hand = (automatik_load().get("habitsHand") or {}).get(heute, {})
    namen, aendern = [p["name"] for p in out["projekte"]], {}
    for n in h["liste"]:
        r = habit_auto_regel(n, namen)
        if not r:
            continue
        ok, text, fest = habit_auto_wert(r, day, fokus_min)
        h["auto"][n] = {"text": text, "ok": ok, "fest": fest, "hand": n in hand}
        soll = hand.get(n, ok)
        if soll != any(x.lower() == n.lower() for x in h["log"].get(heute, [])):
            aendern[n] = soll
    if aendern:
        with VAULT_LOCK:
            h2 = habits_lesen()
            for n, soll in aendern.items():
                rest = [x for x in h2["log"].get(heute, []) if x.lower() != n.lower()]
                h2["log"][heute] = rest + [n] if soll else rest
            habits_schreiben(h2)
            h["log"] = h2["log"]


# Rückblick: Woche (Mo–So) oder Monat auf einer Seite, auch als Notiz in „Claude Memory/Rückblicke“
def konsistenz_score(v):
    if not v or v.get("frei"):
        return None
    teile = [v["habits"] / v["habitsVon"] if v.get("habitsVon") else None, v["ziele"] / v["zieleVon"] if v.get("zieleVon") else None,
             min(1, (v.get("fokusMin") or 0) / v["fokusZielMin"]) if v.get("fokusZielMin") else None]
    teile = [x for x in teile if x is not None]
    return round(sum(teile) / len(teile) * 100) if teile else None


def zeitraum(art, bezug):
    if art == "monat":
        von = bezug.replace(day=1)
        bis = (von + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return von, bis, f"{MONATE[von.month - 1]} {von.year}"
    von = bezug - timedelta(days=bezug.weekday())
    return von, von + timedelta(days=6), f"KW {von.isocalendar()[1]} {von.isocalendar()[0]}"


def erledigt_im(von, bis):
    """Erledigte Aufgaben aus „Erledigt.md“ (Tage und Wochenziele im Zeitraum) plus heute Erledigtes aus BlockEm."""
    if not vault():   # 1.0.49: ohne Vault (Handy, neue Nutzer) lief der ganze Rückblick in einen Fehler
        return []
    p = vault() / CFG.get("erledigtNotiz", mem("Erledigt.md"))
    out, drin = [], False
    if p.exists():
        for line in p.read_text("utf-8").split("\n"):
            if line.startswith("## "):
                d = parse_day(line)
                if not d and (m := re.search(r"KW\s*(\d+)", line)):
                    try:
                        d = date.fromisocalendar(von.year, int(m.group(1)), 7)
                    except ValueError:
                        d = None
                drin = bool(d) and von <= d <= bis
            elif drin and (m := CHECKBOX.match(line)) and m.group(2) != " ":
                t = clean(TAIL.sub("", m.group(4)))
                if t.lower() not in {x.lower() for x in out}:
                    out.append(t)
    return out


def rueckblick(art="woche", bezug=None, plan_=None):
    art = "monat" if art == "monat" else "woche"
    bezug = bezug or date.today()
    von, bis, titel = zeitraum(art, bezug)
    heute = date.today()
    tage, summe, proj, apps = [], {"produktiv": 0, "prokrastination": 0, "neutral": 0}, {}, {}
    nach_kat = {"produktiv": {}, "neutral": {}}
    d = von
    while d <= min(bis, heute):
        r = day_data(d)
        tage.append({"date": d.isoformat(), **{k: r["summe"][k] for k in summe}})
        for k in summe:
            summe[k] += r["summe"][k]
        for p in r.get("projekte", []):
            proj[p["name"]] = proj.get(p["name"], 0) + p["sek"]
        for g in r.get("geraete", []):
            for a in g.get("apps", []):
                if a["kat"] == "prokrastination":
                    apps[a["name"]] = apps.get(a["name"], 0) + a["sek"]
                elif a["kat"] in ("produktiv", "neutral"):   # 1.0.49: Top-Apps auch für Produktiv und Neutral
                    nach_kat[a["kat"]][a["name"]] = nach_kat[a["kat"]].get(a["name"], 0) + a["sek"]
        d += timedelta(days=1)
    with FOKUS_LOCK:
        sess = [s for s in fokus_load()["sessions"] if von.isoformat() <= s["start"][:10] <= bis.isoformat() and s.get("ergebnis")]
    fokus = {"sessions": len(sess), "produktiv": sum(s["ergebnis"]["produktiv"] for s in sess), "dauer": sum(s["ergebnis"]["dauer"] for s in sess),
             "anzahl": sum(s["ergebnis"].get("anzahl", 0) for s in sess)}
    fokus["prozent"] = round(100 * fokus["produktiv"] / fokus["dauer"]) if fokus["dauer"] else 0
    try:
        k = json.loads(KONSISTENZ_FILE.read_text("utf-8"))
    except Exception:
        k = {}
    h = habits_lesen()
    scores = {x: konsistenz_score(v) for x, v in k.items() if von.isoformat() <= x <= bis.isoformat()}
    mit = [s for s in scores.values() if s is not None]
    habit_tab = []
    for n in h["liste"]:
        faellig = erledigt = 0
        d = von
        while d <= min(bis, heute):
            t = d.isoformat()
            da = any(x.lower() == n.lower() for x in h["log"].get(t, []))
            # nur Tage, an denen das Cockpit Habits mitgezählt hat (vorher gab es den Habit evtl. noch nicht)
            if da or (habit_faellig(h, n, t) and (k.get(t) or {}).get("habitsVon")):
                faellig += 1
                erledigt += da
            d += timedelta(days=1)
        if (h["rhythmus"].get(n) or {}).get("proWoche"):   # „3x pro Woche“: Soll = 3 je Woche, nicht jeder Tag
            gezaehlt = sum(1 for x, v in k.items() if von.isoformat() <= x <= bis.isoformat() and v.get("habitsVon"))
            faellig = max(erledigt, round(h["rhythmus"][n]["proWoche"] * min(gezaehlt, (bis - von).days + 1) / 7) if gezaehlt else 0)
        habit_tab.append({"name": n, "erledigt": erledigt, "faellig": faellig})
    erledigt = erledigt_im(von, bis)
    if plan_ and von <= heute <= bis:
        erledigt += [t["text"] for t in plan_["heute"] if t["done"] and t["text"].lower() not in {x.lower() for x in erledigt}]
    out = {"art": art, "titel": titel, "von": von.isoformat(), "bis": bis.isoformat(), "fertig": heute >= bis, "tage": tage, "summe": summe,
           "projekte": [{"name": n, "sek": v} for n, v in sorted(proj.items(), key=lambda x: -x[1])],
           "ablenkung": [{"name": n, "sek": v} for n, v in sorted(apps.items(), key=lambda x: -x[1])[:5]], "fokus": fokus,
           **{k: [{"name": n, "sek": v} for n, v in sorted(m.items(), key=lambda x: -x[1])[:5]] for k, m in nach_kat.items()},
           "konsistenz": {"schnitt": round(sum(mit) / len(mit)) if mit else None, "tage": len(mit),
                          "durchgezogen": sum(1 for s in mit if s >= h["schwelle"]), "schwelle": h["schwelle"],
                          "frei": sum(1 for x, v in k.items() if von.isoformat() <= x <= bis.isoformat() and v.get("frei"))},
           "habits": habit_tab, "erledigt": erledigt}
    leer = not any(summe.values()) and not erledigt and not fokus["sessions"]
    if not leer and automatik_an("rueckblick") and (heute >= bis or heute.weekday() == 6 or (art == "monat" and (heute + timedelta(days=1)).month != heute.month)):
        rueckblick_notiz(out)
    out["ki"] = (automatik_load().get("kiRueckblick") or {}).get(f"{art}:{out['von']}")
    return out


def rueckblick_notiz(r):
    v = vault()
    if not v:
        return
    p = v / CFG.get("rueckblickOrdner", mem("Rückblicke")) / f"{'Woche' if r['art'] == 'woche' else 'Monat'} {r['titel']}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    s, f, k = r["summe"], r["fokus"], r["konsistenz"]
    zeilen = lambda xs: "\n".join(f"- {x['name']}: {fmt(x['sek'])}" for x in xs) or "- keine"
    md = (f"---\ntyp: rückblick\nart: {r['art']}\nvon: {r['von']}\nbis: {r['bis']}\n---\n# Rückblick {r['titel']}\n\n"
          f"Schreibt BlockEm automatisch (Stand {datetime.now():%d.%m.%Y %H:%M}).\n\n## Zeit\n- Produktiv: {fmt(s['produktiv'])}\n"
          f"- Ablenkung: {fmt(s['prokrastination'])}\n- Neutral: {fmt(s['neutral'])}\n\n## Fokus\n- {f['sessions']} Sessions, echte Fokuszeit {fmt(f['produktiv'])} "
          f"({f['prozent']} %), {f['anzahl']}× abgelenkt\n\n## Consistency\n- Schnitt: {'–' if k['schnitt'] is None else str(k['schnitt']) + ' %'}, "
          f"{k['durchgezogen']} von {k['tage']} Tagen durchgezogen (ab {k['schwelle']} %), {k['frei']} freie Tage\n\n## Habits\n"
          + ("\n".join(f"- {x['name']}: {x['erledigt']}/{x['faellig']}" for x in r["habits"]) or "- keine")
          + f"\n\n## Zeit pro Projekt\n{zeilen(r['projekte'])}\n\n## Größte Ablenkungen\n{zeilen(r['ablenkung'])}\n\n## Erledigt\n"
          + ("\n".join(f"- [x] {t}" for t in r["erledigt"]) or "- nichts") + "\n")
    if not p.exists() or p.read_text("utf-8").split("Stand ", 1)[-1].split("\n", 1)[-1] != md.split("Stand ", 1)[-1].split("\n", 1)[-1]:
        p.write_text(md, "utf-8")


# Hinweis bei Ablenkung: Mitteilung auf dem Mac (und über die Handy-App), wenn das Ablenkungs-Budget voll ist, dann alle 30 Min wieder
HINWEIS = {"proz": None}


SPRACHE_EN = {}


def tr(s):
    """24.11.2026 (john): Mitteilungen am Computer in der Sprache der Oberfläche (CFG["sprache"], gesetzt von teile/sprache.js).
    Gleiches Wörterbuch und gleiche Regel wie teile/sprache.js und Sprache.java: genau, mit Zahlen als Platzhalter, Muster {1}…; Logik bleibt deutsch."""
    if not s or CFG.get("sprache") != "en":
        return s
    try:
        w = SPRACHE_EN if "muster" in SPRACHE_EN else {}
        if not w:
            roh = json.loads((HERE / "teile" / "sprache-en.json").read_text("utf-8"))
            zahl = re.compile(r"\d+(?:[.,:]\d+)*")
            w.update(zahl=zahl, ex={}, nx={}, muster=[])
            for de, en in roh.items():
                if not re.search(r"\{\d\}", de):
                    w["ex"][de] = en
                    if zahl.findall(de) and zahl.findall(de) == zahl.findall(en):
                        w["nx"][zahl.sub("\x02", de)] = zahl.sub("\x02", en)
                elif len(re.sub(r"\{\d\}|\s", "", de)) >= 3:
                    teile = re.split(r"(\{\d\})", de)
                    w["muster"].append((len(re.sub(r"\{\d\}", "", de)), re.compile("^" + "".join("(.*?)" if re.fullmatch(r"\{\d\}", t) else re.escape(t) for t in teile) + "$", re.S),
                                        [int(t[1]) for t in teile if re.fullmatch(r"\{\d\}", t)], en))
            w["muster"].sort(key=lambda m: -m[0])
            SPRACHE_EN.update(w)   # erst fertig gebaut übernehmen: ein zweiter Thread sieht nie ein halbes Wörterbuch

        def genau(k):
            if k in w["ex"]:
                return w["ex"][k]
            z = w["zahl"].findall(k)
            e = w["nx"].get(w["zahl"].sub("\x02", k)) if z else None
            if e is None:
                return None
            it = iter(z)
            return re.sub("\x02", lambda _: (lambda x: x.replace(",", ".") if re.fullmatch(r"\d+,\d{1,2}", x) else x)(next(it, "")), e)

        def muster(k, tiefe=0):
            for _, rx, nr, en in w["muster"]:
                t = rx.match(k)
                if t:
                    def platz(m):
                        g = (t.group(nr.index(int(m.group(1))) + 1) if int(m.group(1)) in nr else "").strip()
                        return genau(g) or (muster(g, tiefe + 1) if tiefe < 2 and len(g) > 2 else None) or g
                    return re.sub(r"\{(\d)\}", platz, en)
            return None

        def eins(k):
            k = " ".join(k.split())
            return (genau(k) or muster(k) or k) if k else k
        return "\n".join(eins(z) for z in str(s).split("\n"))
    except Exception:
        return s   # eine Mitteilung nie wegen der Übersetzung verlieren


def ki_anzeige(titel, text):
    """3.22 (john 01.11.): sofort unten am Bildschirm zeigen, was genau reagiert hat und warum. Ersetzt eine noch sichtbare Anzeige."""
    titel, text = tr(titel), tr(text)
    if sys.platform == "win32":
        win_befehl("anzeige", titel=titel, text=text)
        return
    if sys.platform != "darwin":
        return
    try:
        alt = HINWEIS["proz"]
        if alt and alt.poll() is None:
            alt.kill()
        HINWEIS["proz"] = subprocess.Popen(["osascript", "-l", "JavaScript", str(Path(__file__).resolve().parent / "hinweis-mac.js"), titel, text],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        mac_mitteilung(titel, text)


def mac_mitteilung(titel, text):
    titel, text = tr(titel), tr(text)
    if sys.platform == "win32":   # 3.17: Windows-Mitteilung über den Fenster-Helfer
        win_befehl("meldung", titel=titel, text=text)
        return
    if sys.platform != "darwin":
        return
    import subprocess
    q = lambda x: '"' + x.replace("\\", "").replace('"', "'") + '"'
    subprocess.run(["osascript", "-e", f"display notification {q(text)} with title {q(titel)} sound name \"Ping\""], timeout=10, check=False)


def hinweis_pruefen():
    a = automatik_load()
    if a.get("hinweise") is False:
        return None
    limit = int(a.get("limitMin") or CFG["prokrastinationLimitMin"])
    day = day_data(date.today())
    ab = round(day["summe"]["prokrastination"] / 60)
    if ab < limit:
        return None
    stufe = 1 + (ab - limit) // 30
    heute = date.today().isoformat()
    h = a.get("hinweis") or {}
    if h.get("datum") == heute and h.get("stufe", 0) >= stufe:
        return None
    m = {}
    for g in day.get("geraete", []):
        for x in g.get("apps", []):
            if x["kat"] == "prokrastination":
                m[x["name"]] = m.get(x["name"], 0) + x["sek"]
    top = sorted(m.items(), key=lambda x: -x[1])[:2]
    text = (f"{fmt(ab * 60)} abgelenkt, Budget {fmt(limit * 60)}." + (" Am meisten: " + ", ".join(f"{n} {round(s / 60)} Min" for n, s in top) + "." if top else ""))
    with AUTOMATIK_LOCK:
        a = automatik_load()
        a["hinweis"] = {"datum": heute, "stufe": stufe, "id": f"{heute}-{stufe}", "titel": "Ablenkungs-Budget voll" if stufe == 1 else "Immer noch abgelenkt",
                        "text": text, "zeit": datetime.now().strftime("%H:%M"), "geraete": []}
        automatik_save(a)
    mac_mitteilung(a["hinweis"]["titel"], text)
    return a["hinweis"]


def hinweis_fuer_handy(geraet):
    """Der neueste Hinweis von heute, einmal pro Handy."""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        h = a.get("hinweis")
        if not h or h.get("datum") != date.today().isoformat() or geraet in h.get("geraete", []):
            return None
        h.setdefault("geraete", []).append(geraet)
        automatik_save(a)
        return {"id": h["id"], "titel": h["titel"], "text": h["text"]}


def version_tupel(v):
    """Release 1.0 (03.11.): die Zählung fing bei 1.0 neu an, nach 3.45. Damit 1.x als neuer gilt, zählen 1.x und 2.x als 11.x und 12.x."""
    t = tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])
    return (t[0] + 10,) + t[1:] if t and t[0] < 3 else t


def handy_update_hinweis(geraet, version):
    """Ältere Handy-App: einmal am Tag eine Mitteilung mit dem Weg zur neuen Version.
    Nötig, weil die App ab 3.0 BlockEm selbst mitbringt und den Update-Hinweis darin nicht mehr vom Mac bekommt."""
    if MODUS == "handy" or not version or not (HERE / "handy.apk").exists() or version_tupel(version) >= version_tupel(APP_VERSION):
        return None
    with AUTOMATIK_LOCK:
        a = automatik_load()
        u = a.setdefault("appUpdate", {})
        stand = f"{date.today().isoformat()}|{APP_VERSION}"
        if u.get(geraet) == stand:
            return None
        u[geraet] = stand
        automatik_save(a)
    adr = f"http://{lan_ip()}:{CFG.get('port', 8765)}/handy.apk" if MODUS == "mac" else "deine BlockEm-Adresse mit /handy.apk am Ende"
    return {"id": f"update-{APP_VERSION}", "titel": f"Neue Handy-App {APP_VERSION} ist da",
            "text": f"Du hast noch {version}. Öffne die BlockEm-App und tippe auf „Update installieren“. "
                    f"Fehlt der Knopf, einmal im Handy-Browser {adr} öffnen und installieren."}


def automatik_einstellen(data):
    with AUTOMATIK_LOCK:
        a = automatik_load()
        if data.get("limitMin") is not None:
            a["limitMin"] = max(1, min(1440, int(data["limitMin"])))
        if data.get("hinweise") is not None:
            a["hinweise"] = bool(data["hinweise"])
        if data.get("abend"):
            a["abend"] = date.today().isoformat()
        for k, v in (data.get("schalter") or {}).items():
            if k == "hinweise":
                a["hinweise"] = bool(v)
            elif k in SCHALTER:
                if k == "sperre" and not v and fokus_laeuft(a):
                    raise ValueError("Während einer Fokus-Session lässt sich die Sperre nicht ausschalten.")
                a.setdefault("schalter", {})[k] = bool(v)
        automatik_save(a)


def waechter():
    """Läuft im Hintergrund: alle 5 Minuten Ablenkung prüfen."""
    time.sleep(20)
    while True:
        # 1.0.48 (john 08.11.): keine Mitteilung „Ablenkungs-Budget voll“ mehr, das regeln die Locker-Limits (hinweis_pruefen ruht)
        try:
            with leistung("Hinweise und Sicherung"):
                nacht_sicherung()
        except Exception as e:
            print("Nacht-Sicherung:", e)
        if erz_an():   # 1.0: Erziehungs-System nur mit erziehungTest in der Entwicklerversion
            try:
                with leistung("Hinweise und Sicherung"):
                    erz_pruefen()
            except Exception as e:
                print("Erziehung:", e)
        time.sleep(300)

# ---------- V1.2: Sperre im Fokus, Wochenplan-Vorschlag, Kalender (freie Zeiten), „Läuft alles?“, Nacht-Sicherung, Plan gegen Wirklichkeit ----------
# Beschreibung im Vault: „Browser-App Cockpit – Automatisierung“. Zustand in automatik.json (sperre, wochenplan, kalender, sicherung, abend).

# Sperre im Fokus: während einer Fokus-Session werden angehakte Seiten (Browser-Tab) bzw. Apps weggeschaltet.
# Erkennung über ActivityWatch (aktuelles Fenster); Tab: Adresse auf das Cockpit (#fokus) umstellen, App: ausblenden.
SPERRE_STANDARD = ["YouTube", "Instagram", "Netflix", "Paramount", "TikTok", "Facebook", "Reddit", "Twitch", "Disney+", "Prime Video"]
SPERRE_AN_STANDARD = ["YouTube", "Instagram", "Netflix", "Paramount", "TikTok"]
SPERRE_LOG = {"start": None, "liste": [], "fehler": "", "test": None}   # was in der laufenden Session gesperrt wurde
BROWSER_SKRIPT = {"safari": 'tell application "Safari" to set URL of current tab of front window to "{u}"',
                  "chrome": 'tell application "Google Chrome" to set URL of active tab of front window to "{u}"',
                  "arc": 'tell application "Arc" to set URL of active tab of front window to "{u}"',
                  "brave": 'tell application "Brave Browser" to set URL of active tab of front window to "{u}"',
                  "edge": 'tell application "Microsoft Edge" to set URL of active tab of front window to "{u}"'}


def sperre_daten(a=None):
    a = automatik_load() if a is None else a
    sp = a.get("sperre") or {}
    eigene = sp.get("eigene", [])
    if MODUS == "handy":   # 1.0.14: BlockEm am Handy: Vorschläge aus den Listen Social Media/Streaming statt Mac-Mustern (steam, game …)
        k = kategorien_daten(a)
        quelle = [w for w in k["social"] + k["streaming"] if w.lower() != "twitter"]
    else:
        kat = load_json("kategorien.json").get("prokrastination", {})
        quelle = kat.get("titel", []) + kat.get("apps", [])
    kandidaten, gesehen = [], set()
    norm = (lambda w: re.sub(r"[^\w]", "", w.lower())) if MODUS == "handy" else (lambda w: w.strip().lower())   # 1.0.17: am Handy „Paramount“ = „Paramount+“
    for w in (quelle + eigene if MODUS == "handy" else SPERRE_STANDARD + eigene + quelle):
        if len(re.sub(r"[^\w]", "", w)) >= 3 and norm(w) not in gesehen and w.strip().lower() not in gesehen:
            gesehen.add(norm(w)); gesehen.add(w.strip().lower())
            kandidaten.append(w.strip()[:40])
    an = sp.get("an")
    if an is None:
        an = [w for w in SPERRE_AN_STANDARD if w.lower() in gesehen]
        if MODUS == "handy":   # Standard auf die Namen der Vorschläge abbilden (Paramount → Paramount+)
            namen = {norm(k): k for k in kandidaten}
            an = [namen[norm(w)] for w in SPERRE_AN_STANDARD if norm(w) in namen]
    return {"kandidaten": kandidaten, "an": an, "eigene": eigene}


def sperre_geraete(a):
    """john 17.11.2026: Fokus-Sperre nur auf gewählten Geräten. Leere Liste = alle Geräte (Standard).
    Namen, die es nicht (mehr) gibt, zählen nicht: sonst sperrt ein gelöschtes oder fremdes Gerät in der Liste nirgends mehr."""
    da = {geraet_name(x) for x in geraete_namen()}
    return [g for g in dict.fromkeys(geraet_name(x) for x in ((a.get("sperre") or {}).get("geraete") or [])) if g and g in da]


def sperre_gilt(a, geraet):
    g = sperre_geraete(a)
    return not g or (geraet_name(geraet) or "Handy") in g


def sperre_setzen(data):
    """{"name": "YouTube", "an": true}, {"neu": "Twitter"} oder {"liste": ["YouTube", …], "an": true} (17.11.2026: Fokus-Kategorien in einem Schritt)."""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        sd = sperre_daten(a)
        sp = a.setdefault("sperre", {})
        an = list(sd["an"])
        neu = " ".join(str(data.get("neu") or "").split())[:40]
        if isinstance(data.get("geraete"), list):   # [] = alle Geräte
            neu_g = list(dict.fromkeys(geraet_name(x) for x in data["geraete"][:20] if geraet_name(x)))
            alt_g = sperre_geraete(a)
            if fokus_laeuft(a) and neu_g and (not alt_g or any(x not in neu_g for x in alt_g)):
                raise ValueError("Während einer Fokus-Session lässt sich die Sperre nicht lockern.")
            sp["geraete"] = neu_g
        elif isinstance(data.get("liste"), list):
            namen = [" ".join(str(x).split())[:40] for x in data["liste"][:300]]
            namen = [x for x in namen if len(re.sub(r"[^\w]", "", x)) >= 3]
            klein = {x.lower() for x in namen}
            if data.get("an"):
                kand = {x.lower() for x in sd["kandidaten"]}
                sp["eigene"] = sd["eigene"] + [x for x in dict.fromkeys(namen) if x.lower() not in kand]
                an = an + [x for x in dict.fromkeys(namen) if x.lower() not in {y.lower() for y in an}]
            else:
                if fokus_laeuft(a) and any(x.lower() in klein for x in an):
                    raise ValueError("Während einer Fokus-Session lässt sich die Sperre nicht lockern.")
                an = [x for x in an if x.lower() not in klein]
        elif neu:
            if neu.lower() not in {x.lower() for x in sd["kandidaten"]}:
                sp["eigene"] = sd["eigene"] + [neu]
            if neu.lower() not in {x.lower() for x in an}:
                an.append(neu)
        elif data.get("name"):
            n = str(data["name"])
            if not data.get("an") and fokus_laeuft(a):
                raise ValueError("Während einer Fokus-Session lässt sich die Sperre nicht lockern.")
            an = [x for x in an if x.lower() != n.lower()] + ([n] if data.get("an") else [])
        sp["an"] = an
        automatik_save(a)


def sperre_treffer(app, title, liste):
    text = f"{app or ''} {title or ''}".lower()
    return next((w for w in mit_seiten(liste) if len(w) >= 3 and w.lower() in text), None)


def cockpit_selbst(app, adresse):
    """True nur für BlockEm selbst: die App (Fokus-Cockpit/BlockEm) oder die eigene Seite auf localhost/127.0.0.1 mit dem Port des Servers."""
    if (app or "").strip().lower() in ("fokus-cockpit", "blockem"):
        return True
    return re.match(rf"^https?://(localhost|127\.0\.0\.1):{CFG['port']}(?:[/?#]|$)", (adresse or "").strip(), re.I) is not None


BROWSER_NAME = re.compile(r"chrome|chromium|brave|edge|vivaldi|opera|arc$|safari", re.I)
INSTANZ = {}   # App -> (Zeit, PIDs, Anzahl inkl. Headless): pgrep/ps höchstens alle 10 s


def browser_pids(app):
    """Sortierte PIDs der sichtbaren Instanzen dieses Browsers (Headless-Instanzen von Prüf-/Testwerkzeugen zählen nicht), 10 s zwischengespeichert."""
    z = INSTANZ.get(app)
    if z and time.time() - z[0] < 10:
        return z[1]
    import subprocess
    pids = []
    alle = subprocess.run(["pgrep", "-x", app], capture_output=True, text=True, timeout=3).stdout.split()
    for x in alle:
        cmd = subprocess.run(["ps", "-p", x, "-o", "command="], capture_output=True, text=True, timeout=3).stdout
        if "--headless" not in cmd:
            pids.append(int(x))
    pids.sort()
    INSTANZ[app] = (time.time(), pids, len(alle))
    return pids


# 22.11.: Läuft vom selben Chromium-Browser eine Headless-Instanz (Prüfwerkzeuge wie Codex/Playwright), traf 'tell application "Google Chrome"'
# die statt johns Fenster: Sperre meldete „ok“, der Tab blieb offen. Dann den aktiven Tab per PID über ScriptingBridge ansprechen.
TAB_PER_PID = ("ObjC.import('ScriptingBridge');function run(a){var t=$.SBApplication.applicationWithProcessIdentifier(parseInt(a[0]))"
               ".valueForKey('windows').objectAtIndex(0).valueForKey('activeTab');if(a.length>1){t.setValueForKey($(a[1]),'URL');return a[1]}"
               "return ObjC.unwrap(t.valueForKey('URL'))}")


def browser_osa(app, browser, url=None, timeout=10):
    """Aktiven Tab umleiten (url) oder Adresse lesen (url None). browser = Schlüssel aus BROWSER_SKRIPT. Gibt subprocess.CompletedProcess zurück."""
    pid = 0
    if sys.platform == "darwin" and browser in ("chrome", "brave", "edge"):
        try:
            pids = browser_pids(app)
            if pids and INSTANZ[app][2] > len(pids):
                front = vorderste_pid()
                pid = front if front in pids else pids[0]
        except Exception:
            pid = 0
    if pid:
        return subprocess.run(["osascript", "-l", "JavaScript", "-e", TAB_PER_PID, str(pid)] + ([url] if url is not None else []),
                              timeout=timeout, check=False, capture_output=True, text=True)
    skript = BROWSER_ADRESSE[browser] if url is None else BROWSER_SKRIPT[browser].replace("{u}", url)
    return subprocess.run(["osascript", "-e", skript], timeout=timeout, check=False, capture_output=True, text=True)


def vorderste_pid():
    import subprocess
    f = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=3).stdout.strip()
    asn = f.splitlines()[0].strip() if f else ""
    info = subprocess.run(["lsappinfo", "info", "-only", "pid", asn], capture_output=True, text=True, timeout=3).stdout if asn else ""
    m = re.search(r"pid\s*=\s*(\d+)", info)
    return int(m.group(1)) if m else 0


def seitenregel_aktiv(a, fokus):
    """Gilt gerade eine Regel, die Adressen braucht? Fokus, Erwachsenen-Liste (gleiche Bedingung wie in sperre_pruefen) oder laufende feste Sperrzeit/Bindung im App Locker."""
    if fokus or schutz_handy(a, "Mac").get("liste", True):
        return True
    for r in locker_regeln_fuer("Mac", a):
        if (r.get("art") == "zeit" and locker_zeit_aktiv(r)) or (r.get("art") == "bis" and float(r.get("ende") or 0) > time.time()):
            return True
    return False


def zweite_browser_instanz_sperre(app, a, fokus):
    """True, wenn die Sperre eine ZWEITE Instanz desselben Browsers (z. B. eigener --user-data-dir) ausgeblendet hat (nur Mac).
    AppleScript spricht nur eine Instanz an: die Adresse der zweiten Instanz ist unbekannt, Seiten-Regeln würden ins Leere laufen oder den falschen Tab treffen.
    Die älteste sichtbare Instanz gilt als johns normaler Browser und wird nie berührt (Grenze: wer seine eigene Instanz VOR dem normalen Browser startet,
    macht sie zur „normalen“). Reihenfolge nach Kosten: Name, Regel (ohne Prozess), pgrep (10 s Cache), erst bei 2+ Instanzen lsappinfo."""
    if sys.platform != "darwin" or not BROWSER_NAME.search(app or "") or not seitenregel_aktiv(a, fokus):
        return False
    try:
        pids = browser_pids(app)
        if len(pids) < 2:
            return False
        front = vorderste_pid()
        if front not in pids or front == pids[0]:
            return False
        import subprocess
        r = subprocess.run(["osascript", "-e", f'tell application "System Events" to set visible of (first process whose unix id is {front}) to false'],
                           capture_output=True, text=True, timeout=10)   # bei jeder Prüfung: wieder eingeblendet = nach höchstens 3 s erneut weg
        if time.time() - SPERRE_LOG.setdefault("zweitInstanz", 0) >= 10:   # nur Meldung und Protokoll gedrosselt
            SPERRE_LOG["zweitInstanz"] = time.time()
            sperre_protokoll(f"Zweite Instanz von {app} (PID {front}) gesperrt: Adressen unbekannt -> {'ok' if r.returncode == 0 else 'Fehler: ' + (r.stderr or '').strip()[:150]}")
            ki_anzeige("Gesperrt", f"Eine zweite Instanz von {app} zeigt dem Schutz keine Adressen.")
        return True
    except Exception as e:
        SPERRE_LOG["fehler"] = f"Zweite Instanz: {e}"[:200]
        return False


def sperre_pruefen(win_id):
    a = automatik_load()
    with FOKUS_LOCK:
        akt = fokus_load().get("aktiv")
    now = datetime.now(timezone.utc)
    fokus = bool(akt) and automatik_an("sperre", a) and now < datetime.fromisoformat(akt["planEnde"]) and sperre_gilt(a, "Mac")   # „Mac“ = der Computer, wie in geraete_namen()
    # Erwachsenen-Inhalte sind immer gesperrt, darum wird auch ohne Fokus und ohne Locker-Regel geprüft
    if fokus and SPERRE_LOG["start"] != akt["start"]:
        SPERRE_LOG.update(start=akt["start"], liste=[])
    # ActivityWatch schickt das aktuelle Fenster mit bis zu ~10 s Verzögerung: das neueste Ereignis der letzten 2 Minuten ist das aktuelle
    v = vorne(2.5)   # 3.19: gemeinsamer Fenster-Blick (Mac und Windows), sieht auch Inkognito-Fenster
    if not v:
        return
    app, title, adresse = v["app"], v["titel"], v["adresse"]
    if zweite_browser_instanz_sperre(app, a, fokus):   # 21.11.: vor dem Cockpit-Ausschluss, denn die Adresse käme sonst aus der ersten Instanz
        return
    title = f"{title} {adresse}"
    if cockpit_selbst(app, adresse):   # 21.11.: nur BlockEm selbst; vorher überschritt jede Seite mit „blockem“ oder „cockpit“ im Titel oder in der Adresse die Prüfung
        return
    sp = schutz_pc.sperre(app, title)   # Deinstallier-Schutz: Werkzeuge zu; Plan B: Ablenker zu (09.11.2026)
    if sp and sp[0] == "werkzeug":
        schutz_pc.werkzeug_zu(app)
        sperre_protokoll(f"Schutz: {app} ({title[:60]}) {sp[1]}")
        return
    liste = schutz_handy(a, "Mac").get("liste", True)
    treffer = erwachsen_treffer(adresse) if liste else None
    if not liste and not schutz_handy(a, "Mac").get("entwickler"):   # john 17.11.2026: Porno-Schutz aus, Seite bleibt offen, Versuch zählt trotzdem
        z = erwachsen_treffer(adresse)
        if z and time.time() - SPERRE_LOG.setdefault("gezaehlt", {}).get(z, 0) > 600:
            SPERRE_LOG["gezaehlt"][z] = time.time()
            erwachsen_versuch("Mac", z)
    grund = "ist immer gesperrt (Erwachsenen-Inhalt)."
    erwachsen = bool(treffer)
    still = erwachsen   # 19.11.: diskret anzeigen und protokollieren (Erwachsenen-Inhalt, Frauen-Listen)
    if not treffer and fremder_browser(app, a):
        treffer, grund = app, "ist gesperrt: dieser Browser zeigt dem Schutz keine Adressen. Nimm Safari, Chrome, Brave, Edge oder Arc."
    if not treffer and not re.match(r"^(https?://)?(localhost|127\.0\.0\.1)[:/]", adresse or ""):
        g = mac_dns_sperre(app, a) or mac_vpn_sperre(app, a)
        if g:
            treffer, grund = app, g
    if not treffer:
        f = frau_treffer(schutz_handy(a, "Mac"), app, adresse)
        if f:
            (treffer, grund), still = f, True
    kurz_ziel = None
    if not treffer and adresse and schutz_handy(a, "Mac")["kurzvideos"]:
        k = kurzvideo_treffer(adresse)
        if k:
            treffer, grund, kurz_ziel = k[0], "sind gesperrt. Der Rest der Seite geht.", k[1]
    if not treffer and adresse and schutz_handy(a, "Mac")["suche"]:
        such = such_pruefen(adresse)
        if such and such[0] == "umleiten":
            safesearch_umleiten(app, such[1])
            return
        if such:
            treffer, grund, erwachsen = such[2], such[1], True
    if not treffer and adresse and schutz_handy(a, "Mac").get("liste", True):   # nur Browser-Tabs, nicht Obsidian/Finder mit johns Projekttiteln
        w = inhalt_treffer(title.replace(adresse or "", ""), adresse)
        if w:
            treffer, grund, erwachsen = f"„{w}“", "ist immer gesperrt (Erwachsenen-Inhalt).", True
    if not treffer and adresse and schutz_handy(a, "Mac").get("liste", True):
        r = reddit_nsfw(adresse, title)
        if r:
            treffer, grund, erwachsen = r, "ist immer gesperrt (Erwachsenen-Inhalt).", True
    if not treffer and adresse and schutz_handy(a, "Mac")["texte"]:
        w = text_pruefen(app, adresse)
        if w:
            treffer, grund, erwachsen = "Erotischer Text", f"ist gesperrt: {w}.", True
    if not treffer and sp:
        treffer, grund = app, sp[1]
    if not treffer:
        treffer = sperre_treffer(app, title, sperre_daten(a)["an"]) if fokus else None
        grund = f"ist bis {datetime.fromisoformat(akt['planEnde']).astimezone().strftime('%H:%M')} gesperrt. Zurück an die Arbeit." if fokus else ""
    if not treffer:
        lk = locker_sperre(app, title, "Mac", a)   # App Locker: feste Sperrzeit, Tageslimit oder Sitzungs-Limit
        if lk:
            treffer, grund = lk[0], lk[1] + "."
    if not treffer:
        w = innehalten_treffer(app, title, a)
        if not w or time.time() - SPERRE_LOG.get("innehalten", 0) < 5:
            return
        SPERRE_LOG["innehalten"] = time.time()
        innehalten_oeffnen(app, w, adresse)
        return
    still = still or erwachsen
    if erwachsen:
        erwachsen_versuch("Mac", treffer)
    zuletzt = SPERRE_LOG.setdefault("zuletzt", {})
    if time.time() - zuletzt.get(treffer, 0) < 10:   # nicht im Sekundentakt wiederholen
        return
    zuletzt[treffer] = time.time()
    import subprocess
    ziel = kurz_ziel or (f"http://localhost:{CFG['port']}/#" + ("dns" if grund.startswith("ist zu, bis der DNS-Filter") else "fokus"))
    if sys.platform == "win32":
        ok, fehler = win_wegschalten(app, ziel)
        SPERRE_LOG["fehler"] = fehler
        sperre_protokoll(f"{'Gesperrter Inhalt' if still else treffer} in {app}{'' if still else f' ({title[:60]})'} -> {'ok' if ok else 'Fehler: ' + fehler[:200]}")
    else:
        browser = next((k for k in BROWSER_SKRIPT if k in app.lower()), None)
        if browser:
            r = browser_osa(app, browser, ziel)
        else:
            cmd = f'tell application "System Events" to set visible of process "{app.replace(chr(34), "")}" to false'
            r = subprocess.run(["osascript", "-e", cmd], timeout=10, check=False, capture_output=True, text=True)
        if r.returncode != 0:
            # keine Erlaubnis (Systemeinstellungen › Datenschutz & Sicherheit › Automation): wenigstens das Cockpit nach vorn holen
            SPERRE_LOG["fehler"] = sperre_fehlertext(r.stderr, "System Events" if not browser else app)
        else:
            SPERRE_LOG["fehler"] = ""
        if not browser or r.returncode != 0:
            subprocess.run(["open", ziel], timeout=10, check=False)
        sperre_protokoll(f"{'Gesperrter Inhalt' if still else treffer} in {app}{'' if still else f' ({title[:60]})'} -> {'ok' if r.returncode == 0 else 'Fehler: ' + (r.stderr or '').strip()[:200]}")
    if fokus:
        SPERRE_LOG["liste"].append({"zeit": datetime.now().strftime("%H:%M"), "name": "Gesperrter Inhalt" if still else treffer})
    if still:   # 19.11. (john): wer daneben sitzt, liest weder Suchwort noch Seite; im Protokoll steht es auch nicht
        ki_anzeige("Suche gesperrt" if "Suche" in grund else "Inhalt gesperrt", "Diese Suche geht hier nicht." if "Suche" in grund else "Diese Seite ist gesperrt.")
    else:
        ki_anzeige("Im Fokus gesperrt" if fokus else "Gesperrt", f"{treffer} {grund}"[:140])


def innehalten_oeffnen(app, w, adresse):
    """Browser-Tab bzw. App weg, stattdessen die Atem-Pause in BlockEm; „Weiter“ dort gibt die App für ein paar Minuten frei."""
    import subprocess
    from urllib.parse import quote as q_
    ziel = f"http://localhost:{CFG['port']}/#innehalten?w={q_(w)}" + (f"&u={q_(adresse, safe='')}" if adresse else "")
    if sys.platform == "win32":
        win_wegschalten(app, ziel)
        return
    browser = next((k for k in BROWSER_SKRIPT if k in app.lower()), None)
    if browser:
        r = browser_osa(app, browser, ziel)
    else:
        cmd = f'tell application "System Events" to set visible of process "{app.replace(chr(34), "")}" to false'
        r = subprocess.run(["osascript", "-e", cmd], timeout=10, check=False, capture_output=True, text=True)
    if not browser or r.returncode != 0:
        subprocess.run(["open", ziel], timeout=10, check=False)


# ---------- Sperrliste Erwachsenen-Inhalte: immer an, nicht abschaltbar (Mac hier, Handy in SperrDienst) ----------
# sperrliste-erwachsen.txt: eine Domain pro Zeile (StevenBlack porn-only, Sinfonietta, HaGeZi NSFW, Block List Project Porn + Ergänzungen, ~970.000);
# geprüft wird über sperrliste-erwachsen.bin (sortierte FNV-1a-Hashes, gleiche Datei wie assets/erwachsen.bin der Handy-App); Unterdomains zählen mit
ERWACHSEN = {"set": None}
ERWACHSEN_WORTE = ("porn", "xxx", "hentai", "onlyfans", "nsfw", "celebforum", "fappening", "celebjihad", "nudecelebforum", "celebfanforum", "celebsroulette", "egotastic", "forumophilia", "planetsuzy", "vipergirls", "phun.org", "fapello", "fapopedia", "fapodrop", "leakedmodels", "leakedzone", "leakimedia", "hotleak", "nudostar", "nudogram", "nudebay", "thotsbay", "thothub", "influencersgonewild", "socialmediagirls", "simpcity", "erome.com", "coomer.su", "coomer.party", "coomer.st", "kemono.su", "kemono.party", "kemono.cr")   # 05.11. john (Karte): alle Leak-/Promi-Nackt-Foren immer sperren, als Wort, damit neue Endungen auch greifen


def erwachsen_liste():
    """Sortierte 64-Bit-Hashes (FNV-1a) aller Domains aus sperrliste-erwachsen.bin; spart Speicher bei ~1 Mio. Einträgen."""
    if ERWACHSEN["set"] is None:
        from array import array
        h = array("q")
        try:
            with open(HERE / "sperrliste-erwachsen.bin", "rb") as f:
                h.frombytes(f.read())
            if sys.byteorder == "little":
                h.byteswap()
        except Exception:
            h = array("q")
        ERWACHSEN["set"] = h
    return ERWACHSEN["set"]


def erwachsen_hash(d):
    h = 0xcbf29ce484222325
    for b in d.encode():
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h - (1 << 64) if h >= 1 << 63 else h


def erwachsen_drin(d):
    import bisect
    l = erwachsen_liste()
    h = erwachsen_hash(d)
    i = bisect.bisect_left(l, h)
    return i < len(l) and l[i] == h


def erwachsen_treffer(adresse):
    """Domain, wenn die Adresse auf der Erwachsenen-Sperrliste steht (oder ein eindeutiges Wort enthält), sonst None."""
    m = re.match(r"^\s*(?:[a-z][a-z0-9+.-]*://)?([^/?#:\s]+)", (adresse or "").lower())
    if not m or "." not in m.group(1):
        return None
    host = m.group(1)[4:] if m.group(1).startswith("www.") else m.group(1)
    teile = host.split(".")
    if any(erwachsen_drin(".".join(teile[i:])) for i in range(len(teile) - 1)) or any(w in host for w in ERWACHSEN_WORTE):
        return host
    return None


# ---------- Seitentitel/Adresse/Suche (Laptop-Schutz 09.11.2026, john: „Nudes, OnlyFans werden nicht erkannt“): eindeutige Wörter immer sperren ----------
INHALT_TEILE = ("onlyfans", "fansly", "fanvue", "pornhub", "xvideos", "xhamster", "xnxx", "porn", "hentai", "nsfw", "nudes", "camgirl")   # Teilstring im Wort
# „nude“, „naked“, „leaked“, „topless“ fehlen absichtlich (Nude-Lippenstift, naked eye, Datenleck) – Prüfung 09.11.
INHALT_WORTE = {"xxx", "nudes", "nudity", "nackt", "nackte", "nackten", "nacktbilder", "nacktfotos", "porno", "pornos", "sexvideo", "sexvideos",
                "sexcam", "sexcams", "creampie", "blowjob", "gonewild", "milf", "erotik", "erotica", "fetisch", "bdsm"}
INHALT_NICHT = ("pornograf", "pornograph")   # Nachrichten/Wikipedia („Kinderpornografie: Razzia“); Suchen danach sperrt such_pruefen
# Hilfe-Seiten zur Sucht nicht sperren: nur im Seitentitel (nicht in der Adresse, sonst reicht „?x=sucht“ zum Umgehen). 25.11.: INHALT_HILFE → HILFE_MUSTER
# 25.11. (john: FreiVonX-Video „Ab wann bin ich pornosüchtig? (Mit Psychologin …)“ ging zu): Hilfe zur Pornofreiheit erkennen. Gleiches Muster in
# SperrDienst.HILFE_MUSTER (Text vorher durch hilfe_norm). Bewusst nicht: Hilfe, Therapie, Psychologin, frei, loskommen, „porn free“, „Porno sucht …“,
# „süchtig nach Sex“, „sucht“/„addicted“/„Rückfall“ allein (stehen auch in Porno-Titeln: „Frau für Sex sucht Mann“, „Sex addicted …“). Prüfer 25.11., 2 Runden.
HILFE_MUSTER = re.compile(" (?:(?:porn(?:o|os|ografie|ography)?|masturbation)-?(?:sucht|süchtig|suechtig|abhängig|abhaengig|addiction|entzug)(?:e|er|en|es|em|hilfe)?(?= )|sex-?sucht(?:hilfe)?(?= )|porn(?:o|ografie|ography)? addiction(?= )|(?:süchtig|suechtig|abhängig|abhaengig) (?:nach|von) porn|addicted to porn|no ?fap(?= )|porn(?:o)?(?:free|frei)(?:e|er|en|es|em)?(?= )|frei von porn|porn(?:o|os)? (?:aufhören|aufhoeren|aufzuhören|aufzuhoeren)(?= )|(?:quit|quitting|stop watching) porn|porn recovery(?= )|fight ?the ?new ?drug(?= )|covenant eyes(?= )|freivonx(?= )|x-?freiheit(?= ))")   # Leerzeichen statt \b: Android (ICU) kann kein (?U), so verhalten sich beide gleich
# Wörter, die eine Porno-Quelle nennen: wirken auch im Hilfe-Zusammenhang und auf großen Plattformen („xxx“ nur als ganzes Wort, sonst „XXXL“)
INHALT_QUELLE = ("onlyfans", "0nlyfans", "onlyfanz", "fansly", "fanvue", "pornhub", "xvideos", "xhamster", "xnxx", "youporn", "redtube", "brazzers",
                 "nudes", "hentai", "camgirl", "sexcam", "chaturbate", "stripchat", "rule34", "eporner", "porntrex", "pornhd", "spankbang", "tnaflix")
# Allgemeine Wörter: lösen auf Hilfe-Seiten und Plattformen nichts aus. Alles andere (milf, creampie, nacktbilder, …) wirkt dort weiter (wie vor 25.11.).
INHALT_ALLGEMEIN = {"sex", "nackt", "nackte", "nackten", "nude", "naked", "nudity", "erotik", "erotica", "erotisch", "erotic", "masturbation", "masturbieren"}
# Große Seiten mit gemischten Inhalten (25.11., john): dort schließt ein allgemeines Wort im TITEL nichts (Suchen in der Adresse schon)
# Nur YouTube und Wikipedia: Suchmaschinen nicht (Prüfer 25.11.: Startpage/DDG-Suche per POST hat keine Suche in der Adresse, der Titel ist dort die Suche)
PLATTFORM = re.compile(r"^(?:(?:www|m|music)\.)?youtube\.com$|^youtu\.be$|^[a-z-]+(?:\.m)?\.wikipedia\.org$")
# Allgemeine Porno-Wörter (keine Seitennamen wie „eporner“, „pornhd“)
INHALT_PORN_ALLGEMEIN = {"porn", "porno", "pornos", "porns", "pornografie", "pornographie", "pornography", "pornografisch", "pornographisch"}


def hilfe_norm(text):
    import unicodedata
    return " " + " ".join(re.sub(r"[^\w-]+|_", " ", unicodedata.normalize("NFKC", text or "").lower()).split()) + " "


def hilfe_kontext(text):
    return bool(HILFE_MUSTER.search(hilfe_norm(text)))


def quelle_text(text):
    """Nennt der Text irgendwo eine Porno-Quelle? Dann gilt keine Hilfe- oder Plattform-Ausnahme (Prüfer 25.11.: „Brazzers Porno“ auf YouTube)."""
    n = hilfe_norm(text).replace("-", " ")
    return any(f" {x} " in n for x in ("only fans", "porn hub", "rule 34")) or any(quelle_wort(w) for w in n.split())


def quelle_wort(w):
    return w == "xxx" or any(q in w for q in INHALT_QUELLE)


def frei_im_kontext(w):
    """Allgemeines Wort oder Hilfe-Wort („pornosüchtig“), das in Hilfe-Suchen und auf Plattformen nichts auslöst."""
    return not quelle_wort(w) and (w in INHALT_PORN_ALLGEMEIN or w in INHALT_ALLGEMEIN or bool(HILFE_MUSTER.search(hilfe_norm(w))))


def erster_treffer(text, hart, frei_alle):
    """Erstes harte Wort im Text, das nicht frei ist. Frei: Wörter innerhalb einer Hilfe-Wendung („pornosüchtig“, „Pornos aufhören“), sofern der Text
    keine Quelle nennt; mit frei_alle (YouTube/Wikipedia, Hilfe-Subreddit) dazu alle allgemeinen Wörter. Auf anderen Seiten bleibt „Porno“ neben
    einem Hilfe-Wort hart (Prüfer 25.11.: „Pornosüchtige Stiefschwester Porno“)."""
    quelle = quelle_text(text)
    n = hilfe_norm(text)
    spannen = [] if quelle else [m.span() for m in HILFE_MUSTER.finditer(n)]
    for m in re.finditer(r"[^ ]+", n):
        in_hilfe = any(a < m.end() and m.start() < b for a, b in spannen)
        for w in m.group().split("-"):
            if not w or not hart(w):
                continue
            if (in_hilfe and not quelle_wort(w)) or (frei_alle and not quelle and frei_im_kontext(w)):
                continue
            return w
    return None


def adresse_host(adresse):
    from urllib.parse import urlsplit
    a = (adresse or "").strip()
    if a and not re.match(r"^[a-z][a-z0-9+.-]*://", a, re.I):
        a = "https://" + a
    try:
        return (urlsplit(a).hostname or "").lower()
    except ValueError:
        return ""


def inhalt_treffer(titel, adresse):
    """Gefundenes Wort, wenn Seitentitel, Pfad oder Suchanfrage ein eindeutiges Erwachsenen-Wort enthält, sonst None.
    shortcut: nur Wörter, keine Bilder; „sexy“, „bikini“ u. ä. fehlen absichtlich (zu viele harmlose Treffer), die fängt die Bild-KI."""
    from urllib.parse import unquote_plus, urlsplit
    host = adresse_host(adresse)
    try:
        pfad = urlsplit(adresse if re.match(r"^[a-z][a-z0-9+.-]*://", adresse or "", re.I) else "https://" + (adresse or "")).path.lower()
    except ValueError:
        pfad = ""
    m = re.match(r"^/r/([a-z0-9_-]+)(?:/(?!search)|$)", pfad)   # nur der Pfad (nicht ?q=…/r/nofap), nur /r/ (Nutzernamen kann jeder anlegen), keine Reddit-Suche
    reddit = bool(re.search(r"(?:^|\.)reddit\.com$", host) and m and m.group(1) in REDDIT_HILFE)
    platt = bool(PLATTFORM.search(host))
    def hart(w):
        return w in INHALT_WORTE or quelle_wort(w) or (any(x in w for x in INHALT_TEILE) and not any(x in w for x in INHALT_NICHT))   # 25.11.: Quellen-Namen allein („Stripchat“) auch
    w = erster_treffer(titel, hart, platt or reddit)   # 25.11.: Titel; YouTube/Wikipedia/Hilfe-Subreddit: allgemeine Wörter (Porno, nackt) schließen nichts
    if w:
        return w
    if reddit:
        return None   # Hilfe-Subreddit (r/pornfreewomen …): Name und Beitrags-Pfad gehören zur Hilfe
    return erster_treffer(unquote_plus(re.sub(r'^(?:[a-z][a-z0-9+.-]*://)?[^/?#]*', '', adresse or '')), hart, False)   # Adresse/Suche: nur Hilfe-Wendungen frei („porno videos“ bei YouTube bleibt zu)


# ---------- Text-Erkennung am Computer (10.11.2026, john: „erotische Texte“): gleiche Regeln und Listen wie SperrDienst.textTreffer (Quelle: textpatch.py) ----------
# Sichtbarer Seitentext kommt über die Bedienungshilfen (ax_lage.text), nichts wird gespeichert. shortcut: nur Mac; Windows hat noch keinen Textleser.
TEXT_SOFORT = ("rating: explicit", "rating:explicit", "rated: explicit", "rated explicit", "rated e for explicit", "contains explicit sex", "explizite sexuelle inhalte", "expliziter sex", "smut warning", "nsfw smut")
TEXT_PUNKTE = {**dict.fromkeys(("cock", "cocks", "pussy", "cunt", "clit", "cum", "cumming", "cummed", "blowjob", "deepthroat", "creampie", "smut", "smutty", "ficken", "fickte", "fickt", "gefickt", "möse", "fotze", "kitzler", "abspritzen", "lusttropfen", "wichsen", "wichste", "handjob", "rimming", "squirt", "squirting", "vögeln", "vögelte", "gevögelt", "bumsen", "gebumst", "fingerte", "ahegao", "nakadashi", "paizuri", "futanari", "bukkake", "gangbang", "cumshot", "titten", "arschfick"), 3),
               **dict.fromkeys(("orgasm", "orgasms", "climax", "climaxed", "thrust", "thrusts", "thrusting", "moaned", "moaning", "moans", "erection", "nipples", "nipple", "aroused", "arousal", "wetness", "throbbing", "orgasmus", "höhepunkt", "stöhnte", "stöhnen", "stöhnend", "erregt", "erregung", "nippel", "brustwarzen", "schwanz", "muschi", "eichel", "leckte", "lecken", "geleckt", "glied", "spritzte", "ergoss", "horny", "kink", "kinky", "breeding", "dildo", "vibrator", "masturbate", "masturbating", "masturbated", "masturbieren", "masturbierte", "uncensored", "doujinshi", "doujin", "hentai"), 2),
               **dict.fromkeys(("naked", "nude", "panties", "lingerie", "bra", "thighs", "hips", "lips", "tongue", "bed", "kiss", "kissed", "nackt", "nackte", "nackten", "höschen", "schenkel", "zunge", "lippen", "hüften", "bett", "küsste", "fucked", "fucking", "fucks", "dick", "anal", "oral", "stieß", "stöße", "feucht", "feuchte", "geil", "geile", "lust", "keuchte", "ritt", "slick", "breasts", "grinding", "stroke", "sucked", "sucking", "harder", "spicy", "daddy", "naughty", "gierig", "eindringen", "drang", "steif", "wet", "licked", "begging", "gasped", "panting"), 1)}
TEXT_SATZ_STARK = ("fucked her", "fucked him", "fucked me", "fuck me", "fucking her", "fucking him", "fucking me", "fick mich", "fickte sie", "fickte ihn", "jerk off", "jerking off", "jerked off", "edge yourself", "spreiz die beine", "spreizte die beine", "besorg es dir", "besorgte es ihr", "stieß in sie", "kam in ihr", "in ihr kam", "slick heat", "take me harder", "take her harder", "cum inside", "einen blasen", "hartes glied", "steifes glied", "harter schwanz", "harten schwanz", "steifen schwanz", "nasse muschi", "feuchte spalte")
TEXT_SATZ = ("came undone", "his length", "zieh dich aus", "on her knees", "on your knees", "auf die knie", "good girl", "good boy", "daddy kink", "praise kink", "stroke it", "in her mouth", "in ihrem mund", "bis du kommst", "in sie ein")
TEXT_ERSATZ = (("c*ck", "cock"), ("c0ck", "cock"), ("d*ck", "dick"), ("p*ssy", "pussy"), ("pu$$y", "pussy"), ("f*ck", "fuck"), ("f**k", "fuck"), ("fu*k", "fuck"), ("c*m", "cum"), ("s3x", "sex"), ("s*x", "sex"), ("seggs", "sex"), ("p0rn", "porn"), ("pr0n", "porn"))
TEXT = {"zeit": 0.0, "adresse": None}


def text_treffer(s):
    """Grund, wenn der (klein geschriebene) Text klar erotisch ist, sonst None. Kennzeichnung („Rating: Explicit“) sofort, sonst Punkte:
    ab 9 und mindestens ein eindeutiges Wort; jedes Wort zählt höchstens 3-mal, jede Wendung einmal."""
    if not s or len(s) < 40:
        return None
    s = re.sub(r"\s+", " ", s).replace("cum laude", "")
    for a, b in TEXT_ERSATZ:
        s = s.replace(a, b)
    for w in TEXT_SOFORT:
        if w in s:
            return f"„{w}“ steht auf der Seite"
    if "explicit" in s and "archive warning" in s:
        return "Geschichte ist als „Explicit“ markiert"
    punkte = 3 * sum(bool(re.search(r"\b" + w + r"\b", s)) for w in TEXT_SATZ_STARK)   # Wortgrenzen: „einen blasen“ trifft nicht „einen Blasenkatheter“
    stark = punkte // 3 * 2   # eine eindeutige Wendung zählt wie zwei starke Wörter
    punkte += 2 * sum(bool(re.search(r"\b" + w + r"\b", s)) for w in TEXT_SATZ)
    n = {}
    for w in re.findall(r"[^\W\d_]+", s):
        p = TEXT_PUNKTE.get(w)
        if not p or n.get(w, 0) >= 3:
            continue
        n[w] = n.get(w, 0) + 1
        punkte += p
        stark += p == 3
    return "Eindeutige Sex-Szene im Text" if punkte >= 9 and stark >= 1 else None   # Prüfer 10.11.: Fehlalarme (cum laude, spritzte) über die Listen gelöst; 2 starke Treffer verlangen kostete 2 von 10 echten


def text_pruefen(app, adresse):
    """Sichtbaren Text der Browser-Seite prüfen: höchstens alle 3 s, und nur wenn die Seite neu ist oder gerade jemand scrollt/tippt (spart Strom)."""
    if sys.platform != "darwin" or not any(k in (app or "").lower() for k in ("chrome", "brave", "edge", "arc", "chromium", "vivaldi", "opera", "safari", "firefox")):
        return None
    t = time.time()
    if TEXT.get("treffer") and TEXT["treffer"][0] == adresse and t - TEXT["treffer"][2] < 60:
        return TEXT["treffer"][1]   # Prüfer 10.11.: ein Treffer bleibt (Drossel in sperre_pruefen darf ihn nicht verschlucken)
    try:
        import ax_lage
        if t - TEXT["zeit"] < 3 or (TEXT["adresse"] == adresse and ax_lage.leerlauf() > 4):
            return None
        TEXT["zeit"] = t
        s = ax_lage.text(app)
        w = text_treffer(s)
        if w:
            TEXT["treffer"] = (adresse, w, t)
        elif s and ax_lage.INFO.get("text_ganz"):   # nur ganz gelesener, harmloser Text gilt als geprüft (sonst nächste Runde noch einmal)
            TEXT["adresse"] = adresse
        return w
    except Exception:
        return None


# ---------- Reddit (Prüfung 09.11.2026, john: „in Reddit kann ich Sachen aufmachen“): Domain erlaubt, eindeutige Subreddits/Profile/Beiträge nicht ----------
REDDIT_STARK = ("nsfw", "porn", "gonewild", "nude", "naked", "nackt", "hentai", "onlyfans", "fansly", "rule34", "r34", "xxx", "boob", "tits",
                "titty", "titties", "sexy", "busty", "pussy", "milf", "blowjob", "cumshot", "creampie", "lewd", "thot", "ecchi", "ahegao", "18plus", "realgirls", "celebnsfw",
                "gooned", "gooning", "goonette", "goonable")   # john 09.11.: r/GOONED


REDDIT_HILFE = {"pornfreewomen", "pornfree", "nofap", "nofapchristians", "pornaddiction", "pornfreerelationships", "nofapwar", "pornfreeradio",
                "nofapfemales", "pornrecovery", "christianpornrecovery"}


def reddit_nsfw(adresse, titel=""):
    """Treffer (z. B. „reddit.com/r/xyz“), wenn Subreddit-/Profilname oder Seitentitel eindeutig ist, sonst None.
    shortcut: nur Namen und Titel, keine Bilder; harmlos benannte NSFW-Subreddits fängt erst die Bild-KI."""
    a = (adresse or "").strip().lower()
    if not re.match(r"^(?:https?://)?(?:[a-z0-9-]+\.)?(?:reddit\.com|redd\.it)(?:[/?#]|$)", a):
        return None
    m = re.search(r"reddit\.com/(?:r|u|user)/([a-z0-9_-]+)", a)
    name = m.group(1) if m else ""
    if name in REDDIT_HILFE:
        return None   # Hilfe-Subreddits bleiben offen (feste Liste, damit u/nofap_xxx nicht durchrutscht)
    worte = set(re.findall(r"[a-z0-9]+", (titel or "").lower()))
    if (name and any(w in name.replace("_", "") for w in REDDIT_STARK)) or worte & set(REDDIT_STARK):
        return "reddit.com/" + (m.group(0).split("reddit.com/")[1] if m else "")
    return None


# ---------- Suchmaschinen (01.11.2026): Porno-Suchen sperren, SafeSearch erzwingen (Google-Bilder war ein Schlupfloch) ----------
SUCH_WORTE = {
    "porn", "porno", "pornos", "porns", "pornografie", "pornography", "xxx", "nackt", "nackte", "nackten", "nacktbilder", "nacktfotos",
    "nude", "nudes", "naked", "nudity", "sex", "sexy", "sexvideo", "sexvideos", "hentai", "onlyfans", "fansly", "fanvue", "nsfw", "milf",
    "titten", "boobs", "tits", "titties", "pussy", "muschi", "fotze", "fick", "ficken", "fickt", "fuck", "fucking", "blowjob", "cumshot",
    "creampie", "anal", "erotik", "erotisch", "erotic", "erotica", "lingerie", "dessous", "unterwäsche", "reizwäsche", "bikini", "leaked",
    "leaks", "camgirl", "camgirls", "webcamgirl", "stripper", "striptease", "strip", "escort", "escorts", "hure", "huren", "nutte", "nutten",
    "rule34", "r34", "e621", "chaturbate", "xhamster", "xvideos", "xnxx", "brazzers", "bdsm", "fetisch", "fetish", "nippel", "nipple",
    "nipples", "topless", "busen", "twerk", "twerking", "thot", "jav", "upskirt", "playboy", "penthouse", "joi", "bukkake", "dildo",
    "vibrator", "masturbation", "masturbieren", "wichsen", "orgasmus", "orgasm", "gangbang", "threesome", "dreier", "lesbian", "lesben",
    "schlampe", "slut", "booty", "ass", "arsch", "hintern", "bra", "panties", "höschen", "badeanzug", "spicy", "lewd", "ecchi", "ahegao",
}
SUCH_SAETZE = ("oben ohne", "18+", "ohne bh", "no bra", "see through", "durchsichtig", "only fans", "feet pics", "fuß bilder", "hot girls",
               "heiße frauen", "sexy girls", "girls in", "nip slip", "wardrobe malfunction", "cosplay nsfw", "asmr nsfw", "link in bio")
SUCH_PARAMS = ("q", "p", "query", "text", "search_query", "searchterm", "keywords")
# Host-Teil -> Pfade, die Suchen sind, und der SafeSearch-Parameter
SUCH_SAFE = [
    (r"(^|\.)google\.[a-z.]+$", ("/search", "/imgres", "/images", "/webhp"), ("safe", "active")),
    (r"(^|\.)bing\.com$", ("/search", "/images", "/videos"), ("adlt", "strict")),
    (r"(^|\.)duckduckgo\.com$", ("/",), ("kp", "1")),
    (r"^search\.yahoo\.com$|^[a-z]+\.search\.yahoo\.com$", ("/search",), ("vm", "r")),
    (r"^search\.brave\.com$", ("/search", "/images", "/videos"), ("safesearch", "strict")),
    (r"(^|\.)startpage\.com$", ("/do/search", "/sp/search"), ("qadf", "heavy")),
    (r"(^|\.)ecosia\.org$", ("/search", "/images", "/videos"), ("sfs", "strict")),
    (r"(^|\.)qwant\.com$", ("/",), ("s", "2")),
]
SUCH_GANZ_SPERREN = (r"(^|\.)yandex\.[a-z.]+$", r"(^|\.)yandex\.com\.tr$")   # Bilder/Videos dort ohne verlässliches SafeSearch


def such_text(q):
    import unicodedata
    q = unicodedata.normalize("NFKC", q or "").lower()
    return " " + re.sub(r"[^\w+]+", " ", q) + " "


def such_wort(q):
    """Erstes Erwachsenen-Wort in einer Suchanfrage, sonst None (auch Domains der Sperrliste wie „xvideos“)."""
    t = such_text(q).replace("_", " ")   # „porno_videos“
    hilfe = hilfe_kontext(q) and not quelle_text(q)   # 25.11. (john): „Pornosucht Hilfe“ darf man suchen, „porno videos“ nicht; Quellen und eindeutige Wörter nie
    for w in t.split():
        if (w in SUCH_WORTE or quelle_wort(w)) and not (hilfe and frei_im_kontext(w)):
            return w
        if len(w) >= 5 and w.isalnum() and not hilfe and (erwachsen_drin(w + ".com") or erwachsen_drin(w + ".net")):
            return w   # Prüfer 25.11.: mit Hilfe-Wort nicht (porno.com, videos.com, girls.com stehen auf der Liste); Quellen fängt quelle_wort
    n = hilfe_norm(q).replace("-", " ")
    for x in ("only fans", "porn hub", "rule 34"):   # Quellen in zwei Wörtern („Rule_34“)
        if f" {x} " in n:
            return x
    for satz in SUCH_SAETZE:   # Wendungen („oben ohne“, „only fans“) sind eindeutig: wirken auch mit Hilfe-Wort
        if " " + satz + " " in t or (not satz.isalnum() and satz in (q or "").lower()):
            return satz
    return None


def such_pruefen(adresse):
    """("sperren", Grund, Name) bei einer Erwachsenen-Suche, ("umleiten", neue Adresse) ohne SafeSearch, sonst None."""
    from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
    a = (adresse or "").strip()
    if not a:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", a, re.I):
        a = "https://" + a
    try:
        u = urlsplit(a)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host:
        return None
    params = parse_qsl(u.query, keep_blank_values=True)
    q = " ".join(v for k, v in params if k.lower() in SUCH_PARAMS)
    if any(re.search(m, host) for m in SUCH_GANZ_SPERREN) and re.search(r"/(images|video)", u.path):
        return ("sperren", "Bilder- und Videosuche ist dort gesperrt (kein sicheres SafeSearch).", host)
    for muster, pfade, (k, v) in SUCH_SAFE:
        if not re.search(muster, host) or not any(u.path.startswith(x) for x in pfade):
            continue
        if not q and k != "kp":
            return None
        w = such_wort(q) if q else None
        if w:
            return ("sperren", f"Suche nach „{w}“ ist gesperrt (Erwachsenen-Inhalt).", host)
        if not q:
            return None
        if (k, v) in [(a_.lower(), b_.lower()) for a_, b_ in params]:
            return None
        neu = [(a_, b_) for a_, b_ in params if a_.lower() != k] + [(k, v)]
        return ("umleiten", urlunsplit((u.scheme, u.netloc, u.path, urlencode(neu), u.fragment)))
    return None


def safesearch_umleiten(app, neu):
    """Aktiven Tab auf dieselbe Suche mit SafeSearch setzen (höchstens alle 2 s)."""
    import subprocess
    if sys.platform == "win32":
        if win_browser(app) and time.time() - SPERRE_LOG.get("umgeleitet", 0) >= 2:
            SPERRE_LOG["umgeleitet"] = time.time()
            win_befehl("setzen", h=WIN["h"], u=neu)
        return
    browser = next((k for k in BROWSER_SKRIPT if k in (app or "").lower()), None)
    if not browser or time.time() - SPERRE_LOG.get("umgeleitet", 0) < 2:
        return
    SPERRE_LOG["umgeleitet"] = time.time()
    ziel = neu.replace("\\", "%5C").replace('"', "%22")
    browser_osa(app, browser, ziel)


ERWACHSEN_DATEI = "erwachsen-versuche.json"


def erwachsen_versuch(wo, was):
    """Versuche, Erwachsenen-Inhalte zu öffnen, pro Tag zählen (für „Sauber seit …“ auf Heute). Inhalt bleibt auf dem Gerät."""
    try:
        f = DATEN / ERWACHSEN_DATEI
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
        t = date.today().isoformat()
        tag = d.setdefault("tage", {}).setdefault(t, {"n": 0, "wo": []})
        tag["n"] += 1
        # 19.11. (Werte › Muster): je Stunde zählen, Bild-KI getrennt (schwaches Signal); was in 10 Min. kommt, ist ein Treffer
        ki = was == "Bild" or str(was).endswith((": Bild", ": Video"))
        art, jetzt = ("ki" if ki else "std"), time.time()
        if jetzt - d.setdefault("letzte", {}).get(art, 0) > 600:   # festes 10-Min.-Fenster ab dem gezählten Treffer
            st = tag.setdefault(art, {})
            st[str(datetime.now().hour)] = st.get(str(datetime.now().hour), 0) + 1
            d["letzte"][art] = jetzt
        if wo and wo not in tag["wo"]:
            tag["wo"].append(wo[:30])
        d["zuletzt"] = datetime.now().isoformat(timespec="seconds")
        if len(d["tage"]) > 400:
            for k in sorted(d["tage"])[:-400]:
                d["tage"].pop(k)
        f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    except Exception as e:
        print("Versuch:", e)


def erwachsen_serie():
    """Tage ohne Versuch bis heute (Start: erster Tag mit Daten oder erster Start), Versuche heute und diese Woche."""
    f = DATEN / ERWACHSEN_DATEI
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    tage = d.get("tage") or {}
    if not d.get("seit"):
        d["seit"] = date.today().isoformat()
        try:
            f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    heute = date.today()
    letzte = max((k for k, v in tage.items() if v.get("n")), default=None)
    start = date.fromisoformat(letzte) + timedelta(days=1) if letzte else date.fromisoformat(d["seit"])
    montag = heute - timedelta(days=heute.weekday())
    woche = sum(v.get("n", 0) for k, v in tage.items() if k >= montag.isoformat())
    vorwoche = sum(v.get("n", 0) for k, v in tage.items() if (montag - timedelta(days=7)).isoformat() <= k < montag.isoformat())
    beste, lauf, vorher = 0, 0, None
    for k in sorted(tage):
        if tage[k].get("n"):
            if vorher:
                beste = max(beste, (date.fromisoformat(k) - vorher).days - 1)
            vorher = date.fromisoformat(k)
    serie = max(0, (heute - start).days + (1 if not (tage.get(heute.isoformat()) or {}).get("n") else 0))
    # Gesamttage (Belohnung 11.11.): alle Tage seit dem Start ohne Versuch, heute mitgezählt; ein Rückfall nimmt nichts weg.
    gesamt = max(0, (heute - date.fromisoformat(d["seit"])).days + 1 - sum(1 for k, v in tage.items() if v.get("n") and k >= d["seit"]))
    return {"tage": serie, "gesamt": max(gesamt, serie), "heute": (tage.get(heute.isoformat()) or {}).get("n", 0), "woche": woche, "vorwoche": vorwoche, "beste": max(beste, serie),
            "verlauf": {k: v.get("n", 0) for k, v in sorted(tage.items())[-35:]}}


# ---------- Innehalten (01.11.2026): kurze Atem-Pause, bevor eine ausgewählte App/Seite aufgeht ----------
# 1.0 (03.11.): Nachfrage standardmäßig AN, vor allen Apps der Listen Social Media + Streaming (kategorien, änderbar) plus eigenen Apps
INNEHALTEN_STD = {"an": True, "apps": [], "kategorien": True, "sekunden": 10, "frei": 10,
                  "text": "Atme einmal tief durch. Brauchst du das gerade wirklich, oder ist es nur ein Impuls?"}
KATEGORIEN_STD = {
    "social": ["Instagram", "TikTok", "Snapchat", "Facebook", "X", "twitter", "Reddit", "Threads", "Pinterest", "Tumblr", "BeReal", "9gag",
               "Jodel", "Lemon8", "Likee", "Imgur"],
    "streaming": ["YouTube", "Netflix", "Prime Video", "Disney+", "Paramount+", "Twitch", "Joyn", "Kick.com", "RTL+", "WOW", "Crunchyroll",
                  "Apple TV", "MagentaTV", "DAZN", "Sky Go", "ARD Mediathek", "ZDFmediathek", "Pluto TV", "Dailymotion", "Vimeo"],
}
# 1.0.50 (john 09.11.): Wer eine App sperrt, sperrt automatisch auch ihre Website, auf jedem Gerät. Die Sperre vergleicht Wörter
# mit Adresse/Titel (Teilstring), darum nur Adressen, die nicht in fremden Adressen vorkommen. shortcut: „x.com“ fehlt, weil es in
# „dropbox.com“ steckt; X im Browser greift über „twitter.com“ und am Computer über den Fensternamen. Upgrade: Host-Vergleich in SperrDienst.
APP_SEITEN = {
    "instagram": ["instagram.com"], "tiktok": ["tiktok.com"], "snapchat": ["snapchat.com"], "facebook": ["facebook.com", "fb.watch"],
    "x": ["twitter.com"], "twitter": ["twitter.com"], "reddit": ["reddit.com", "redd.it"], "threads": ["threads.net", "threads.com"],
    "pinterest": ["pinterest."], "tumblr": ["tumblr.com"], "bereal": ["bere.al"], "9gag": ["9gag.com"], "jodel": ["jodel.com"],
    "lemon8": ["lemon8-app.com"], "likee": ["likee.video"], "imgur": ["imgur.com"],
    "youtube": ["youtube.com", "youtu.be"], "netflix": ["netflix.com"], "prime video": ["primevideo.com", "amazon.de/gp/video", "amazon.com/gp/video"],
    "disney+": ["disneyplus.com"], "paramount+": ["paramountplus.com"], "paramount+ deutschland": ["paramountplus.com"], "paramount": ["paramountplus.com"],   # 21.11.: Regelname „Paramount+ Deutschland“ (so heißt die App am Handy) und Fokus-Wort „Paramount“ sperrten die Website nicht
     "twitch": ["twitch.tv"], "joyn": ["joyn.de", "joyn.at", "joyn.ch"],
    "kick.com": ["kick.com"], "rtl+": ["plus.rtl.de"], "wow": ["wowtv.de"], "crunchyroll": ["crunchyroll.com"], "apple tv": ["tv.apple.com"],
    "magentatv": ["magentatv.de", "web.magentatv"], "dazn": ["dazn.com"], "sky go": ["skygo.sky.de", "sky.de/skygo"],
    "ard mediathek": ["ardmediathek.de"], "zdfmediathek": ["zdf.de"], "pluto tv": ["pluto.tv"], "dailymotion": ["dailymotion.com"], "vimeo": ["vimeo.com"],
}


def mit_seiten(liste):
    """Liste von Apps plus die Website jeder bekannten App (ohne Doppelte, Reihenfolge bleibt)."""
    out = list(liste or [])
    for w in list(out):
        for s in APP_SEITEN.get(str(w).strip().lower(), []):
            if s not in out:
                out.append(s)
    return out


WARUM_GRUENDE = ["Langeweile", "Stress", "Gewohnheit", "Einsam", "Müde", "Aufschieben"]


def kategorien_daten(a=None):
    """Listen Social Media / Streaming (Nachfrage, Orts-Sperre); fehlt eine, gilt die Vorgabe."""
    a = automatik_load() if a is None else a
    k = a.get("kategorien") or {}
    return {x: list(k[x]) if isinstance(k.get(x), list) else list(KATEGORIEN_STD[x]) for x in KATEGORIEN_STD}


def konsum_lockern_pruefen(namen, a=None):
    """john 19.11.: Wer eine App aus „Konsum“ nimmt, lockert jede Konsum-Ruhezeit. Das braucht dieselbe Wartezeit wie andere Lockerungen."""
    a = automatik_load() if a is None else a
    l = a.get("locker") or {}
    if not any(r.get("konsum") for r in l.get("regeln") or []):
        return
    weg = {str(n).lower() for n in namen} & {n.lower() for n in konsum_liste(a)}
    if weg and not (locker_frei_status(l)["frei"] or locker_frei_status(l).get("immerFrei")):
        raise ValueError(locker_lockern_text(l).replace("diese Regel", "deine Ruhezeit"))


def kategorien_setzen(data):
    """POST /api/kategorien {social?: [..], streaming?: [..]}"""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        k = kategorien_daten(a)
        neu = dict(k)
        for x in KATEGORIEN_STD:
            if x in data:
                neu[x] = [" ".join(str(v).split())[:40] for v in data[x] or [] if len(re.sub(r"[^\w]", "", str(v))) >= 2][:40]
        # 19.11. (Prüfer): erst kürzen, dann vergleichen, was wirklich aus „Konsum“ fällt (auch hinter Platz 40); nur das braucht die Wartezeit
        weg = {n.lower() for n in konsum_liste(a)} - {n.lower() for n in konsum_liste({**a, "kategorien": neu})}
        if weg:
            konsum_lockern_pruefen(sorted(weg), a)
        k = neu
        a["kategorien"] = k
        automatik_save(a)
    return {"ok": True, "kategorien": k}


def warum_merken(grund, app="", zeit=None):
    """„Lieber nicht“ im Innehalten → „Warum wolltest du drauf?“: Grund und App je Tag zählen (Werte)."""
    grund = " ".join(str(grund or "").split())[:40]
    if not grund:
        return
    f = DATEN / "warum.json"
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    tag = (datetime.fromtimestamp(zeit / 1000) if zeit else datetime.now()).date().isoformat()
    d.setdefault(tag, []).append({"grund": grund, "app": " ".join(str(app or "").split())[:40], "zeit": int(zeit or time.time() * 1000)})
    d[tag] = d[tag][-100:]
    try:
        f.write_text(json.dumps(dict(sorted(d.items())[-120:]), ensure_ascii=False), "utf-8")
    except Exception:
        pass


def warum_stand():
    f = DATEN / "warum.json"
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    heute = date.today().isoformat()
    montag = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    ab30 = (date.today() - timedelta(days=30)).isoformat()
    gruende, apps, n30 = {}, {}, 0
    for t, liste in d.items():
        if t < ab30:
            continue
        for e in liste:
            n30 += 1
            gruende[e.get("grund") or "?"] = gruende.get(e.get("grund") or "?", 0) + 1
            if e.get("app"):
                apps[e["app"]] = apps.get(e["app"], 0) + 1
    return {"heute": len(d.get(heute) or []), "woche": sum(len(v) for t, v in d.items() if t >= montag), "tage30": n30,
            "gruende": dict(sorted(gruende.items(), key=lambda x: -x[1])), "apps": dict(sorted(apps.items(), key=lambda x: -x[1])[:12]),
            "vorgaben": WARUM_GRUENDE}


def oeffnungen_stand():
    """Werte: wie oft jede App/Seite geöffnet wurde und wie lange im Schnitt pro Öffnung, heute und die letzten 7 Tage."""
    def summe(tage):
        out = {}
        for i in range(tage):
            try:
                d = day_data(date.today() - timedelta(days=i))
            except Exception:
                continue
            for g in d.get("geraete", []):
                for x in g.get("apps", []):
                    nl = str(x.get("name") or "").lower()
                    if not x.get("oeffnen") or "launcher" in nl or nl.startswith(("com.", "android", "org.")) or "cockpit" in nl or "blockem" in nl or "clear mind" in nl:
                        continue   # 1.0.2: Launcher, Paketnamen und die App selbst nicht zählen
                    e = out.setdefault(x["name"].lower(), {"name": x["name"], "kat": x.get("kat"), "oeffnen": 0, "sek": 0})
                    e["oeffnen"] += x["oeffnen"]
                    e["sek"] += x.get("sek") or 0
        liste = sorted(out.values(), key=lambda e: -e["oeffnen"])[:15]
        return [{**e, "proOeffnung": round(e["sek"] / e["oeffnen"])} for e in liste]
    return {"heute": summe(1), "woche": summe(7)}

INNEHALTEN_FREI = {}   # Wort -> frei bis (time.time())


def innehalten_daten(a=None):
    a = automatik_load() if a is None else a
    return {**INNEHALTEN_STD, **(a.get("innehalten") or {})}


def innehalten_setzen(data):
    if data.get("warum") or data.get("warumListe"):   # 1.0: Antwort auf „Warum wolltest du drauf?“ (auch gesammelt vom Handy)
        for e in (data.get("warumListe") or []) + ([data] if data.get("warum") else []):
            warum_merken(e.get("warum"), e.get("app"), e.get("zeit"))
        return {"ok": True, "warum": warum_stand()}
    if data.get("weiter"):
        w = str(data["weiter"])[:40].lower()
        INNEHALTEN_FREI[w] = time.time() + innehalten_daten()["frei"] * 60
        return {"ok": True, "frei": INNEHALTEN_FREI[w]}
    with AUTOMATIK_LOCK:
        a = automatik_load()
        x = innehalten_daten(a)
        if "an" in data:
            x["an"] = bool(data["an"])
        if "apps" in data:
            x["apps"] = [" ".join(str(v).split())[:40] for v in data["apps"] or [] if len(re.sub(r"[^\w]", "", str(v))) >= 3][:30]
        if "kategorien" in data:
            x["kategorien"] = bool(data["kategorien"])
        if "sekunden" in data:
            x["sekunden"] = max(3, min(120, int(data["sekunden"] or 10)))
        if "frei" in data:
            x["frei"] = max(1, min(120, int(data["frei"] or 10)))
        if "text" in data:
            x["text"] = " ".join(str(data["text"] or "").split())[:300] or INNEHALTEN_STD["text"]
        a["innehalten"] = x
        automatik_save(a)
    return {"ok": True, "innehalten": x}


def innehalten_treffer(app, title, a=None):
    x = innehalten_wirksam(a)
    if not x["an"]:
        return None
    w = sperre_treffer(app, title, x["apps"])
    if not w or INNEHALTEN_FREI.get(w.lower(), 0) > time.time():
        return None
    return w


# ---------- Orts-Sperre (1.0, 03.11.): am Ort (Gym, Arbeit, Uni) sind Social Media + Streaming am Handy ganz zu (Geofence in der Handy-App) ----------
# ---------- 1.0.1 Regulierung: die App sperrt von allein, was messbar frisst (john 03.11.2026) ----------
# Gemessen über die letzten 3 Tage: nur Apps aus den Listen Social Media/Streaming (john 04.11.: nie Arbeits-Apps) mit im Schnitt ≥ 60 Min oder ≥ 20 Öffnungen am Tag
# (an mindestens 2 Tagen), die noch keine Locker-Regel haben, kommen auf die Liste „offen“. Der Nutzer wählt nur noch WIE
# (Limit, Öffnungen, Sperrzeit); nach 48 Std. ohne Wahl setzt die App das Standard-Limit. Regeln ohne PIN, weil nur strenger.
REGUL_STD = {"minuten": 60, "oeffnen": 20, "tage": 3, "fristStd": 48, "limit": 60, "pause": 7 * 86400}


def regul_kandidaten(a):
    listen = [w.lower() for l in kategorien_daten(a).values() for w in l]
    geregelt = {w.lower() for r in (a.get("locker") or {}).get("regeln") or [] for w in r.get("apps") or []}
    out = {}
    for i in range(1, REGUL_STD["tage"] + 1):
        try:
            d = day_data(date.today() - timedelta(days=i))
        except Exception:
            continue
        tag = {}
        for g in d.get("geraete", []):          # je Gerät (kurzer App-Name ohne „(Handy)“), über alle Geräte summiert
            for x in g.get("apps", []):
                sek, n = x.get("sek") or 0, " ".join(str(x.get("name") or "").split())[:40]
                nl = n.lower()
                if sek < 60 or len(re.sub(r"[^\w]", "", n)) < 3 or "cockpit" in nl or "blockem" in nl or "clear mind" in nl:
                    continue
                if "launcher" in nl or nl.startswith(("com.", "android", "org.")) or nl.endswith(".apk"):   # Launcher/System, Paketnamen ohne Label
                    continue
                if not any(w == nl or (len(w) >= 3 and (w in nl or nl in w)) for w in listen):   # john 04.11.: NUR Social/Streaming, nie Arbeits-Apps
                    continue
                e = tag.setdefault(nl, {"name": n, "sek": 0, "oeffnen": 0})
                e["sek"] += sek
                e["oeffnen"] += x.get("oeffnen") or 0
        for nl, t in tag.items():
            e = out.setdefault(nl, {"name": t["name"], "sek": 0, "oeffnen": 0, "tage": 0})
            e["sek"] += t["sek"]
            e["oeffnen"] += t["oeffnen"]
            e["tage"] += 1
    res = []
    for nl, e in out.items():
        if e["tage"] < 2 or any(nl == g or nl in g or g in nl for g in geregelt):
            continue
        m, o = e["sek"] / 60 / e["tage"], e["oeffnen"] / e["tage"]
        if m >= REGUL_STD["minuten"] or o >= REGUL_STD["oeffnen"]:
            res.append({"name": e["name"], "minuten": round(m), "oeffnen": round(o), "tage": e["tage"]})
    return sorted(res, key=lambda e: -e["minuten"])


def regul_anwenden(a, name, wahl, auto=False):
    """Legt die Regel ohne PIN an (nur strenger) und trägt die App als erledigt ein."""
    l = a.setdefault("locker", {})
    art = wahl.get("art")
    if art == "limit":
        r = {"art": "limit", "apps": [name], "minuten": max(15, min(90, int(wahl.get("minuten") or REGUL_STD["limit"])))}
    elif art == "oeffnen":
        r = {"art": "oeffnen", "apps": [name], "anzahl": max(3, min(20, int(wahl.get("anzahl") or 10)))}
    elif art == "zeit":
        r = {"art": "zeit", "apps": [name], "von": wahl.get("von") or "21:00", "bis": wahl.get("bis") or "07:00", "tage": wahl.get("tage") or list(range(7))}
    else:
        raise ValueError("Bitte wählen: Limit, Öffnungen oder Sperrzeit.")
    r = locker_regel_sauber(r)
    r["geaendert"] = time.time()
    r["auto"] = True
    l["regeln"] = [x for x in l.get("regeln") or [] if x.get("id") != r["id"]] + [r]
    rr = a.setdefault("regulierung", {})
    rr.setdefault("offen", {}).pop(name.lower(), None)
    rr.setdefault("erledigt", {})[name.lower()] = {"name": name, "art": r["art"], "zeit": time.time(), "auto": auto}
    sperre_protokoll(f"Regulierung: {name} → {r['art']}{' (automatisch nach 48 Std.)' if auto else ''}")
    return r


def regul_stand(a=None):
    a = automatik_load() if a is None else a
    rr, jetzt = a.get("regulierung") or {}, time.time()
    offen = [{**e, "fristStd": max(0, round((e.get("seit", jetzt) + REGUL_STD["fristStd"] * 3600 - jetzt) / 3600))}
             for e in (rr.get("offen") or {}).values()]
    erl = sorted((rr.get("erledigt") or {}).values(), key=lambda e: -e.get("zeit", 0))[:10]
    return {"offen": offen, "erledigt": erl, "schwelle": {"minuten": REGUL_STD["minuten"], "oeffnen": REGUL_STD["oeffnen"]}}


def regul_pruefen():
    """Neue Kandidaten merken; wer 48 Std. nicht wählt, bekommt das Standard-Limit. Wird bei jedem /api/tracking aufgerufen."""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        rr = a.setdefault("regulierung", {})
        offen, erl, jetzt, neu = rr.setdefault("offen", {}), rr.setdefault("erledigt", {}), time.time(), False
        for k in regul_kandidaten(a):
            nl = k["name"].lower()
            if nl in offen:
                if (offen[nl].get("minuten"), offen[nl].get("oeffnen")) != (k["minuten"], k["oeffnen"]):
                    offen[nl].update(minuten=k["minuten"], oeffnen=k["oeffnen"], tage=k["tage"])
                    neu = True
                continue
            if jetzt - (erl.get(nl) or {}).get("zeit", 0) < REGUL_STD["pause"]:
                continue
            offen[nl] = {**k, "seit": jetzt}
            neu = True
        for nl, e in list(offen.items()):
            if jetzt - e.get("seit", jetzt) > REGUL_STD["fristStd"] * 3600:
                regul_anwenden(a, e["name"], {"art": "limit"}, auto=True)
                neu = True
        if neu:
            automatik_save(a)
        return regul_stand(a)


def regul_setzen(data):
    """POST /api/regulierung {app, art: limit|oeffnen|zeit, minuten?, anzahl?, von?, bis?, tage?}"""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        offen = (a.get("regulierung") or {}).get("offen") or {}
        nl = str(data.get("app") or "").strip().lower()
        if nl not in offen:
            raise ValueError("Diese App steht gerade nicht zur Wahl.")
        r = regul_anwenden(a, offen[nl]["name"], data)
        automatik_save(a)
    return {"ok": True, "regel": r, "regulierung": regul_stand(), "locker": locker_info()}


def orte_daten(a=None):
    a = automatik_load() if a is None else a
    return [o for o in (a.get("orte") or []) if isinstance(o, dict) and o.get("name")]


def orte_setzen(data):
    """POST /api/orte {orte: [{name, lat, lon, radius?, kategorien?}]} ersetzt die Liste (höchstens 20 Orte)."""
    out = []
    for o in data.get("orte") or []:
        if not isinstance(o, dict):
            continue
        name = " ".join(str(o.get("name") or "").split())[:40]
        try:
            lat, lon = float(o.get("lat")), float(o.get("lon"))
        except (TypeError, ValueError):
            raise ValueError(f"Ort „{name or '?'}“: Standort fehlt. Tippe am Handy auf „Ich bin gerade hier“.")
        if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Bitte einen Namen und einen gültigen Standort angeben.")
        try:
            radius = max(50, min(2000, int(o.get("radius") or 150)))
        except (TypeError, ValueError):
            radius = 150
        kat = [k for k in (o.get("kategorien") or []) if k in KATEGORIEN_STD] or list(KATEGORIEN_STD)
        out.append({"name": name, "lat": round(lat, 6), "lon": round(lon, 6), "radius": radius, "kategorien": kat})
    with AUTOMATIK_LOCK:
        a = automatik_load()
        a["orte"] = out[:20]
        automatik_save(a)
    sperre_protokoll(f"Orts-Sperre: {len(out)} Orte")
    return {"ok": True, "orte": out[:20]}


# ---------- Habits und Offline-Momente (1.0, 03.11.): eigene Habits, Frage nach längerer Zeit ohne Handy, Tagesabschluss am Abend ----------
OFFLINE_DATEI = "offline.json"
OFFLINE_STD = {"habits": [], "momente": [], "tage": {},
               "frage": {"an": True, "min": 45, "max": 2}, "abschluss": {"an": False, "zeit": "21:30"}}


def offline_load():
    f = DATEN / OFFLINE_DATEI
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    out = json.loads(json.dumps(OFFLINE_STD))
    for k in out:
        if isinstance(d.get(k), type(out[k])):
            out[k] = d[k] if not isinstance(out[k], dict) else {**out[k], **d[k]}
    return out


def offline_save(d):
    (DATEN / OFFLINE_DATEI).write_text(json.dumps(d, ensure_ascii=False), "utf-8")


def offline_daten():
    d = offline_load()
    heute = date.today().isoformat()
    d["momente"] = d["momente"][-60:]
    d["heuteMin"] = sum(int(m.get("min") or 0) for m in d["momente"] if str(m.get("tag")) == heute)
    return d


def offline_handy():
    """Für die Handy-App: ob und wie oft sie nach Offline-Zeit fragt, Tagesabschluss-Mitteilung."""
    d = offline_load()
    return {"fragen": bool(d["frage"].get("an", True)), "min": int(d["frage"].get("min") or 45), "max": int(d["frage"].get("max") or 2),
            "abendAn": bool(d["abschluss"].get("an")), "abendZeit": d["abschluss"].get("zeit") or "21:30"}


def offline_aktion(data):
    """POST /api/offline: {habit: {name}} neu, {habitWeg: id}, {habitErledigt: {id, tag?, an}} abhaken,
    {moment: {min, text, von?}} Offline-Moment, {tag: {datum?, text}} Tagesabschluss-Notiz, {frage: {...}}, {abschluss: {...}}."""
    d = offline_load()
    heute = date.today().isoformat()
    if isinstance(data.get("habit"), dict):
        name = " ".join(str(data["habit"].get("name") or "").split())[:60]
        if len(name) < 2:
            raise ValueError("Bitte einen Namen für den Habit angeben.")
        if len(d["habits"]) >= 30:
            raise ValueError("Höchstens 30 Habits.")
        d["habits"].append({"id": secrets.token_hex(4), "name": name, "seit": heute, "tage": []})
    if data.get("habitWeg"):
        d["habits"] = [h for h in d["habits"] if h.get("id") != data["habitWeg"]]
    if isinstance(data.get("habitErledigt"), dict):
        e = data["habitErledigt"]
        tag = str(e.get("tag") or heute)[:10]
        for h in d["habits"]:
            if h.get("id") == e.get("id"):
                t = [x for x in h.get("tage") or [] if x != tag]
                if e.get("an", True):
                    t.append(tag)
                h["tage"] = sorted(t)[-400:]
    if isinstance(data.get("moment"), dict):
        m = data["moment"]
        try:
            minuten = max(1, min(1440, int(m.get("min") or 0)))
        except (TypeError, ValueError):
            minuten = 0
        d["momente"].append({"tag": heute, "zeit": datetime.now().strftime("%H:%M"), "min": minuten,
                             "text": " ".join(str(m.get("text") or "").split())[:200], "von": str(m.get("von") or "")[:5]})
        d["momente"] = d["momente"][-200:]
    if isinstance(data.get("tag"), dict):
        tag = str(data["tag"].get("datum") or heute)[:10]
        d["tage"][tag] = {"text": " ".join(str(data["tag"].get("text") or "").split())[:500], "zeit": datetime.now().strftime("%H:%M")}
        d["tage"] = dict(sorted(d["tage"].items())[-120:])
    if isinstance(data.get("frage"), dict):
        f = data["frage"]
        if "an" in f:
            d["frage"]["an"] = bool(f["an"])
        for k, lo, hi in (("min", 15, 600), ("max", 1, 5)):
            if k in f:
                try:
                    d["frage"][k] = max(lo, min(hi, int(f[k])))
                except (TypeError, ValueError):
                    pass
    if isinstance(data.get("abschluss"), dict):
        ab = data["abschluss"]
        if "an" in ab:
            d["abschluss"]["an"] = bool(ab["an"])
        if ab.get("zeit") and UHRZEIT.match(str(ab["zeit"])):
            d["abschluss"]["zeit"] = str(ab["zeit"])[:5]
    offline_save(d)
    return {"ok": True, **offline_daten()}


# ---------- Unterwegs (01.11.2026): Handys verbinden sich über Tailscale mit dem Mac, auch ohne gleiches WLAN ----------
def tailscale_adresse():
    """Tailscale-Adresse dieses Rechners (100.64.0.0/10), sonst ""."""
    import subprocess
    for cmd in (["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip", "-4"], ["tailscale", "ip", "-4"]):
        try:
            r = subprocess.run(cmd, timeout=3, check=False, capture_output=True, text=True)
            ip = (r.stdout or "").split()
            if r.returncode == 0 and ip and re.match(r"^100\.", ip[0]):
                return ip[0]
        except Exception:
            pass
    try:
        r = subprocess.run(["ifconfig"], timeout=3, check=False, capture_output=True, text=True)
        for m in re.finditer(r"inet (100\.(\d+)\.\d+\.\d+)", r.stdout or ""):
            if 64 <= int(m.group(2)) <= 127:
                return m.group(1)
    except Exception:
        pass
    return ""


BROWSER_ADRESSE = {"safari": 'tell application "Safari" to get URL of current tab of front window',
                   "chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
                   "arc": 'tell application "Arc" to get URL of active tab of front window',
                   "brave": 'tell application "Brave Browser" to get URL of active tab of front window',
                   "edge": 'tell application "Microsoft Edge" to get URL of active tab of front window'}


def vorderste_app():
    import subprocess
    try:
        r = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
                           timeout=3, check=False, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def browser_adresse(app):
    """Adresse des aktiven Tabs (auch im Inkognito-Fenster), leer wenn kein Browser oder keine Erlaubnis."""
    import subprocess
    k = next((k for k in BROWSER_ADRESSE if k in (app or "").lower()), None)
    if not k:
        return ""
    try:
        r = browser_osa(app, k, None, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def sperre_fehlertext(err, ziel):
    err = (err or "").strip()
    if "-1743" in err or "not allowed" in err.lower() or "nicht erlaubt" in err.lower() or "berechtigt" in err.lower():
        return (f"Keine Erlaubnis, {ziel} zu steuern. Systemeinstellungen › Datenschutz & Sicherheit › Automation › Terminal: "
                f"„{ziel}“ einschalten. Bis dahin holt BlockEm sich nur nach vorn.")
    return f"Sperren ging nicht ({err[:120] or 'unbekannter Fehler'}). BlockEm holt sich stattdessen nach vorn."


def sperre_protokoll(text):
    try:
        f = DATEN / "sperre.log"
        alt = f.read_text("utf-8").splitlines()[-200:] if f.exists() else []
        f.write_text("\n".join(alt + [f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}"]) + "\n", "utf-8")
    except Exception:
        pass


def sperre_test():
    """Löst die Erlaubnis-Abfrage des Mac aus und meldet, was geht: Browser steuern und Apps ausblenden."""
    import subprocess
    out = []
    laeuft = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'],
                            timeout=20, check=False, capture_output=True, text=True)
    if laeuft.returncode != 0:
        out.append({"name": "Apps ausblenden (System Events)", "ok": False, "text": sperre_fehlertext(laeuft.stderr, "System Events")})
        namen = ""
    else:
        out.append({"name": "Apps ausblenden (System Events)", "ok": True, "text": "erlaubt"})
        namen = laeuft.stdout
    for prog, skript in (("Safari", 'tell application "Safari" to get URL of current tab of front window'),
                         ("Google Chrome", 'tell application "Google Chrome" to get URL of active tab of front window')):
        if namen and prog not in namen:
            continue
        r = subprocess.run(["osascript", "-e", skript], timeout=20, check=False, capture_output=True, text=True)
        out.append({"name": f"{prog} steuern", "ok": r.returncode == 0, "text": "erlaubt" if r.returncode == 0 else sperre_fehlertext(r.stderr, prog)})
    sperre_protokoll("Test: " + "; ".join(f"{x['name']} {'ok' if x['ok'] else 'NEIN'}" for x in out))
    SPERRE_LOG["test"] = {"zeit": datetime.now().strftime("%H:%M"), "liste": out}
    return out


def fokus_handy(gesperrt="", geraet="", art="", dns=None):
    """Für die Sperre in der Handy-App: läuft ein Fokus, bis wann, was ist gesperrt. gesperrt= meldet, was das Handy weggeschaltet hat."""
    a = automatik_load()
    if geraet:
        SPERRE_LOG.setdefault("handys", {})[geraet[:40]] = time.time()   # zeigt in BlockEm, ob die Sperre am Handy nachfragt
        if dns is not None:
            SPERRE_LOG.setdefault("dns", {})[geraet[:40]] = dns[:80]   # "" = Privates DNS aus, sonst Anbieter
    if art == "panik":
        if gesperrt == "sperre-aus":
            SPERRE_LOG.setdefault("aus", {})[(geraet or "Handy")[:40]] = time.time()
            sperre_protokoll(f"Handy: Fokus-Sperre ausgeschaltet ({geraet[:30] or 'Handy'})")
        elif gesperrt in ("gedrückt", "geschafft"):
            panik_zaehlen(gesperrt)
            sperre_protokoll(f"Panik-Knopf: {gesperrt} ({geraet[:30] or 'Handy'})")
        gesperrt = ""
    with FOKUS_LOCK:
        akt = fokus_load().get("aktiv")
    jetzt = datetime.now(timezone.utc)
    an = bool(akt) and automatik_an("sperre", a) and jetzt < datetime.fromisoformat(akt["planEnde"]) and sperre_gilt(a, geraet or "Handy")
    if gesperrt and art == "warum":   # 1.0: „grund|app“ aus dem Innehalten am Handy
        g, _, app = gesperrt.partition("|")
        warum_merken(g, app)
        gesperrt = ""
    if gesperrt and art == "erwachsen":
        erwachsen_versuch(geraet or "Handy", gesperrt)
        gesperrt = (gesperrt.split(":")[0].strip()[:30] if ":" in gesperrt else "Handy") + ": Gesperrter Inhalt"   # 19.11.: nur die App, nie Suchwort/Seite (auch von älteren Handys)
        sperre_protokoll(f"Handy: {gesperrt} ({geraet[:30] or 'Handy'})")
    if gesperrt and art == "sitzung":   # 24.11.: Pause nach „am Stück“ gilt auf allen Geräten
        sitzung_pause_melden(gesperrt, geraet)
        gesperrt = ""
    if gesperrt and art == "gezaehlt":   # john 17.11.2026: Porno-Schutz aus, nicht gesperrt, nur gezählt
        erwachsen_versuch(geraet or "Handy", gesperrt)
        gesperrt = ""
    if gesperrt and an:
        if SPERRE_LOG["start"] != akt["start"]:
            SPERRE_LOG.update(start=akt["start"], liste=[])
        n = f"{gesperrt[:40]} ({geraet[:30] or 'Handy'})"
        SPERRE_LOG["liste"].append({"zeit": datetime.now().strftime("%H:%M"), "name": n})
        sperre_protokoll(f"Handy: {n}")
    return {"aktiv": an, "bis": int(datetime.fromisoformat(akt["planEnde"]).timestamp() * 1000) if an else 0,
            "liste": mit_seiten(sperre_daten(a)["an"]) if an else [], "locker": locker_fuer_handy(geraet, a) if geraet else [],
            "innehalten": innehalten_wirksam(a), "schutz": schutz_handy(a, geraet or None),
            "kategorien": kategorien_daten(a), "orte": orte_daten(a), "offline": offline_handy()}


# Schutz-Schalter (3.12, john 01.11.): alle an/aus, für alle Geräte oder je Gerät („Mac“ bzw. Handy-Name wie in handy-daten).
# Wert: (Standard, wo er wirkt). Die Erwachsenen-Sperrliste ist KEIN Schalter, sie bleibt immer an (john).
# Beta (john, 01.11.): DNS-Filter (Handy + Mac), Bild-KI und Suchsperre sind auch im Release frei an/aus, ohne Locker-Timer
SCHUTZ_BETA_FREI = ("bilderVideo",)   # john 17.11.: Bild-KI auf YouTube/Netflix bleibt Beta, jederzeit an/aus ohne Locker-Timer. Alles andere nur nach dem Timer.
# Standard (john, 01.11.): alle neuen Schutz-Funktionen erst einmal AUS, jeder schaltet selbst ein. Die Sperrliste Erwachsenen-Inhalte bleibt immer an.
SCHUTZ_SCHALTER = {"inApps": (False, "handy"), "bilder": (False, "beide"), "webcam": (False, "handy"), "nurStore": (False, "handy"),
                   "dnsPflicht": (False, "handy"), "suche": (False, "beide"), "browser": (False, "mac"), "macDns": (False, "mac"),
                   "vpn": (False, "beide"), "kurzvideos": (False, "beide"), "bilderVideo": (False, "beide"), "liste": (True, "mac"),
                   "geschichten": (False, "beide"), "kiBegleiter": (False, "beide"), "texte": (False, "beide"), "zeichnungen": (False, "beide"), "bildSicher": (True, "handy"),
                   "bilderGalerie": (False, "handy"), "bilderChats": (False, "handy")}   # 25.11. (john): Bild-KI in Galerie/Video-Playern und Messengern nur auf Wunsch, aus nach dem Locker-Timer;   # Frauen-Profil (05.11.)   # liste (05.11.): Erwachsenen-Sperrliste am Computer, nur in der Entwicklerversion abschaltbar
# john 05.11.: Grundschutz immer an, ohne Schalter (SafeSearch, VPN, Erwachsenes in Apps, Zufalls-Chats, nur Play Store, Bild-KI für Social Media).
# Schaltbar bleiben nur bilderVideo (Beta: YouTube/Netflix), kurzvideos und die DNS-/Browser-Schalter.
SCHUTZ_IMMER = ("suche", "vpn", "inApps", "webcam", "nurStore", "bilder")
# john 05.11. (später am Tag): Profil aus der Einrichtung. „porno“/„beides“ = Porno-Schutz (alles oben fest an), „fokus“ = nur weniger
# Doomscrolling: fest an nur SafeSearch (+ die Erwachsenen-Sperrliste, die ohnehin immer gilt), Rest aus und frei einschaltbar.
# Frauen: Bild-KI ist bisher nur auf Männer trainiert, darum bei „w“ nicht fest an (Beta, selbst einschaltbar).
# Ohne Profil (bestehende Nutzer) = Porno-Schutz wie bisher. Gespeichert in automatik.json „profil“ {ziel, geschlecht, seit}.
PROFIL_ZIELE = ("porno", "fokus", "beides")
# Listen fürs Frauen-Profil, gleich in SperrDienst.java (GESCHICHTEN_*, KI_*). Domains inkl. Unterdomains, App-Namen als ganzes Wort.
FRAU_LISTEN = {
    "geschichten": ("Geschichten und Audio",
        ("dipseastories.com", "tryquinn.com", "quinn.app", "ferly.com", "emjoy.app", "bloomstories.com", "literotica.com", "lushstories.com",
         "chyoa.com", "storiesonline.net", "asstr.org", "galatea.com", "inkitt.com", "dreame.com", "radishfiction.com", "joyread.com",
         "lezhin.com", "lezhinus.com", "toomics.com", "tappytoon.com", "manta.net", "sexstories.com", "nifty.org", "mcstories.com",
         "storiesonline.org", "bdsmlibrary.com", "erotische-geschichten.net", "sevac.com", "lesestunden.ch", "fictionmania.tv",
         "audiodesires.com", "gonewildaudio.com", "soundgasm.net", "whisperaudios.com", "mybedtimestories.com"),
        ("dipsea", "quinn", "ferly", "emjoy", "bloom stories", "literotica", "lush stories", "chyoa", "storiesonline", "galatea", "dreame",
         "radish fiction", "joyread", "lezhin", "toomics", "tappytoon", "manta comics")),
    "kiBegleiter": ("KI-Begleiter",
        ("character.ai", "janitorai.com", "chai.ml", "chai-research.com", "talkie-ai.com", "polybuzz.ai", "replika.com", "replika.ai",
         "nomi.ai", "kindroid.ai", "spicychat.ai", "crushon.ai", "candy.ai", "romanticai.com"),
        ("character.ai", "character ai", "janitor", "chai", "talkie", "polybuzz", "replika", "nomi", "kindroid", "spicychat", "crushon",
         "candy.ai", "candy ai", "romantic ai")),
}


def frau_treffer(x, app, adresse):
    """Grund, wenn App oder Seite auf einer eingeschalteten Frauen-Liste steht, sonst None."""
    m = re.match(r"^\s*(?:[a-z][a-z0-9+.-]*://)?([^/?#:\s]+)", (adresse or "").lower())
    host = m.group(1) if m else ""
    n = f" {app or ''} ".lower()
    for k, (name, hosts, namen) in FRAU_LISTEN.items():
        if not x.get(k):
            continue
        if host and any(host == h or host.endswith("." + h) for h in hosts):
            return host, f"ist gesperrt ({name})."
        if any(re.search(r"(^|[^a-z0-9])" + re.escape(w) + r"($|[^a-z0-9])", n) for w in namen):
            return app, f"ist gesperrt ({name})."
    return None


def profil_daten(a=None):
    a = automatik_load() if a is None else a
    p = a.get("profil") if isinstance(a.get("profil"), dict) else {}
    ziel = p.get("ziel") if p.get("ziel") in PROFIL_ZIELE else "porno"
    return {"ziel": ziel, "geschlecht": p.get("geschlecht") if p.get("geschlecht") in ("m", "w") else "", "pornoSchutz": ziel != "fokus"}


def schutz_immer(a=None):
    if ENTWICKLER:   # john 05.11.: Entwicklerversion (sein Mac) muss zum Recherchieren alles abschalten können
        return ()
    p = profil_daten(a)
    if not p["pornoSchutz"]:   # john 17.11.2026: Porno-Schutz aus = nichts davon fest an, Porno-Seiten werden nur gezählt (pornoAus)
        return ()
    # Frauen-Profil (Plan 05.11.): Männer-Bild-KI läuft fest mit, dazu die Liste „Geschichten und Audio“
    # Release 1.0 (john 05.11. „Pornoschutz für Frauen finishen“): Text-Erkennung (Smut, „Rating: Explicit“) und Hentai/Zeichnungen fest
    # john 05.11. 18:12: Hentai-Sperre auch bei Männern fest
    return SCHUTZ_IMMER + ("zeichnungen", "texte", "kiBegleiter", "geschichten")   # john 11.11.: Geschichten und Audio fest für alle, nicht nur im Frauen-Profil     # 18:51: KI-Begleiter fest   # 18:14: Text-Erkennung auch bei Männern fest


def profil_setzen(data):
    """Profil ändern. Zu Porno-Schutz (strenger) geht sofort, zurück zu Fokus nur nach dem Locker-Timer (wie Lockern)."""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        alt = profil_daten(a)
        ziel = data.get("ziel") if data.get("ziel") in PROFIL_ZIELE else alt["ziel"]
        geschlecht = data.get("geschlecht") if data.get("geschlecht") in ("m", "w", "") else alt["geschlecht"]
        lockert = alt["pornoSchutz"] and ziel == "fokus"   # Mann/Frau umschalten lockert nicht (beide Porno-Profile gleich streng)
        l = a.get("locker") or {}
        erstes = data.get("einrichtung") and not isinstance(a.get("profil"), dict)   # nur die allererste Einrichtung darf ohne Timer
        if lockert and not erstes and not ENTWICKLER:   # john 05.11. 18:51: Porno-Schutz lässt sich nach der Einrichtung nicht mehr abwählen
            raise ValueError("Der Porno-Schutz bleibt an.")
        if geschlecht != alt["geschlecht"] and isinstance(a.get("profil"), dict) and alt["geschlecht"] and not data.get("einrichtung") and not ENTWICKLER:
            raise ValueError("Mann oder Frau ist nach der Einrichtung fest.")   # john 05.11.: nicht mehr umschaltbar
        a["profil"] = {"ziel": ziel, "geschlecht": geschlecht, "seit": time.time()}
        automatik_save(a)
    return {"profil": profil_daten(), "schutz": schutz_handy()}   # bilderVideo (3.22): Bild-KI auch auf YouTube und Netflix; 3.16 (BlockP-Abgleich): VPN-Erkennung, Reels/Shorts/Explore


# 3.21: Bild-KI unterscheidet Klassen; je Klasse einstellbar, was passiert (john 01.11.: „alles offen, damit ich testen kann,
# wie aggressiv sie ist“). weg = sofort, zweiter = beim 2. Treffer innerhalb 15 s, hinweis = nur Meldung, aus = nichts.
BILD_KLASSEN = ("nackt", "unterwaesche", "bademode", "anzueglich")
BILD_AKTIONEN = ("weg", "zweiter", "hinweis", "aus")
BILD_REGELN_STD = {"nackt": "weg", "unterwaesche": "weg", "bademode": "weg", "anzueglich": "weg"}   # 18.11.: alles beim 1. Treffer


def bild_regeln(x):
    # john 18.11.2026: „alles beim ersten, warum sollte irgendwas beim 2. Treffer sein“ – jede Art sofort, gespeichertes „zweiter“ zählt nicht mehr
    return {k: "weg" for k in BILD_REGELN_STD}


def schutz_handy(a=None, geraet=None):
    """Schalter für den Schutz. Ohne geraet: die Einstellung für alle Geräte (plus "geraete" = eigene Werte je Gerät).
    Mit geraet: was für dieses Gerät gilt (eigener Wert vor dem für alle)."""
    a = automatik_load() if a is None else a
    x = a.get("schutz") or {}
    eigen = {g: {k: bool(w) for k, w in (v or {}).items() if k in SCHUTZ_SCHALTER} for g, v in (x.get("geraete") or {}).items() if isinstance(v, dict)}
    out = {k: x.get(k, std) for k, (std, _) in SCHUTZ_SCHALTER.items()}
    if geraet:
        out.update(eigen.get(geraet_name(geraet) or geraet) or {})
    else:
        out["geraete"] = eigen
    p = profil_daten(a)
    out.update({k: True for k in schutz_immer(a)})
    if geraet == "Mac" and schutz_pc.konto_sperre():   # Plan B „Filter scharf“ (john 09.11.): SafeSearch, Shorts/Reels, Bild-KI an diesem Computer fest an
        out.update(suche=True, kurzvideos=True, bilder=True)
    out["profil"] = p
    if not ENTWICKLER:
        out["liste"] = p["pornoSchutz"]
    out["pornoAus"] = not p["pornoSchutz"]   # Handy: Porno-Seiten nicht sperren, nur als Versuch zählen (art=gezaehlt)
    out["immer"] = list(schutz_immer(a))
    out.update(macDnsWarAn=bool(x.get("macDnsWarAn")), ausnahmen=x.get("ausnahmen") or [],
               bilderZiele=x.get("bilderZiele") or list(BILD_ZIELE_STD), entwickler=ENTWICKLER,
               bildRegeln=bild_regeln(x))   # 3.17: Bild-KI am Computer, Test-Anzeige
    return out


# ---------- DNS-Filter am Mac (3.11): CleanBrowsing Adult Filter für alle Netzwerkdienste, Wächter sperrt Browser, wenn er fehlt ----------
MAC_DNS_IPS = ["185.228.168.10", "185.228.169.11"]
MAC_DNS_IP6 = ["2a0d:2a00:1::1", "2a0d:2a00:2::1"]
MAC_DNS = {"t": 0.0, "ok": None, "laeuft": False}


def _mac_dns_messen():
    ok = None
    try:
        out = (win_befehl("dns").get("server") or "") if sys.platform == "win32" else \
            subprocess.run(["scutil", "--dns"], capture_output=True, text=True, timeout=5).stdout
        ok = any(ip in out for ip in MAC_DNS_IPS + ["2a0d:2a00:1::", "2a0d:2a00:2::"])
        if not ok:   # verschlüsseltes DNS (Profil) steht nicht immer in scutil: Testauflösung einer gesperrten Seite
            try:
                ip = socket.gethostbyname("pornhub.com")
                ok = ip in ("0.0.0.0", "127.0.0.1") or ip.startswith("185.228.")
            except socket.gaierror:
                ok = None   # offline oder gefiltert: nichts ändern
    except Exception:
        ok = None
    if ok is not None:
        MAC_DNS["ok"] = ok
    if ok:
        with AUTOMATIK_LOCK:
            a = automatik_load()
            x = a.get("schutz") or {}
            if not x.get("macDnsWarAn"):
                x["macDnsWarAn"] = True
                a["schutz"] = x
                automatik_save(a)
    MAC_DNS["laeuft"] = False


def mac_dns_ok():
    """True/False/None (unbekannt). Misst höchstens jede Minute im Hintergrund, blockiert nie. Mac und Windows-PC (3.17)."""
    if sys.platform not in ("darwin", "win32"):
        return None
    if time.time() - MAC_DNS["t"] > 60 and not MAC_DNS["laeuft"]:
        MAC_DNS.update(t=time.time(), laeuft=True)
        threading.Thread(target=_mac_dns_messen, daemon=True).start()
    return MAC_DNS["ok"]


def mac_dns_einrichten(aus=False):
    """Setzt den Filter für alle Netzwerkdienste (fragt einmal nach dem Mac-Passwort). aus=True stellt wieder auf automatisch (Router)."""
    if sys.platform == "win32":
        return win_dns_einrichten(aus)
    if sys.platform != "darwin":
        raise ValueError("Geht nur am Mac und am Windows-PC.")
    import shlex
    liste = subprocess.run(["networksetup", "-listallnetworkservices"], capture_output=True, text=True, timeout=10).stdout.splitlines()[1:]
    dienste = [d.strip() for d in liste if d.strip() and not d.startswith("*")]
    if not dienste:
        raise ValueError("Keine Netzwerkdienste gefunden.")
    befehl = " ; ".join(f"/usr/sbin/networksetup -setdnsservers {shlex.quote(d)} {'Empty' if aus else ' '.join(MAC_DNS_IPS + MAC_DNS_IP6)}" for d in dienste)
    befehl += " ; /usr/bin/dscacheutil -flushcache ; /usr/bin/killall -HUP mDNSResponder"
    skript = 'do shell script "' + befehl.replace("\\", "\\\\").replace('"', '\\"') + '" with administrator privileges'
    r = subprocess.run(["osascript", "-e", skript], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise ValueError(("Nicht ausgeschaltet: " if aus else "Nicht eingerichtet: ") + ((r.stderr or "").strip()[:160] or "abgebrochen"))
    MAC_DNS["t"] = 0
    time.sleep(1)
    _mac_dns_messen()
    sperre_protokoll(f"DNS-Filter am Mac {'ausgeschaltet' if aus else 'eingerichtet'} für {', '.join(dienste)[:120]}")
    return {"ok": True, "dienste": dienste, "aktiv": MAC_DNS["ok"]}


BROWSER_MAC = {"safari", "google chrome", "chrome", "brave browser", "microsoft edge", "arc", "chromium", "google chrome canary",
               "safari technology preview"}


def mac_dns_sperre(app, a=None):
    """Grund, wenn der DNS-Filter am Mac schon einmal an war, jetzt fehlt und die App ein Browser ist."""
    n = (app or "").strip().lower().removesuffix(".app").removesuffix(".exe")
    if n not in BROWSER_MAC and n not in FREMDE_BROWSER and n not in WIN_BROWSER:
        return None
    x = schutz_handy(a, "Mac")
    if not x["macDns"] or mac_dns_ok() is not False:   # Pflicht ab sofort (john 01.11.): ohne Filter kein Browser
        return None
    return "ist zu, bis der DNS-Filter am Mac wieder an ist (BlockEm › Systemcheck › DNS-Filter am Mac einschalten)."


# ---------- 3.16: VPN-Erkennung und Kurzvideos (Reels, Shorts, Explore) am Mac ----------
MAC_VPN = {"t": 0.0, "an": False}


def mac_vpn_aktiv():
    """Läuft der Standard-Weg ins Internet über einen VPN-Tunnel (utun/ipsec/ppp/tun/wg)? Ein VPN kann den DNS-Filter umgehen."""
    if sys.platform not in ("darwin", "win32"):
        return False
    if time.time() - MAC_VPN["t"] < 20:
        return MAC_VPN["an"]
    if sys.platform == "win32":
        MAC_VPN.update(t=time.time(), an=bool(win_befehl("vpn").get("an")))
        return MAC_VPN["an"]
    import subprocess
    an = False
    try:
        r = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5)
        m = re.search(r"interface:\s*(\S+)", r.stdout or "")
        an = bool(m and re.match(r"^(utun|ipsec|ppp|tun|tap|wg)\d*", m.group(1)))
    except Exception:
        an = False
    MAC_VPN.update(t=time.time(), an=an)
    return an


def mac_vpn_sperre(app, a=None):
    n = (app or "").strip().lower().removesuffix(".app").removesuffix(".exe")
    if n not in BROWSER_MAC and n not in FREMDE_BROWSER and n not in WIN_BROWSER:
        return None
    if not schutz_handy(a, "Mac")["vpn"] or not mac_vpn_aktiv():
        return None
    return "ist zu, solange ein VPN läuft (ein VPN kann den Schutz umgehen). VPN ausschalten, dann geht der Browser wieder."


KURZVIDEO_MUSTER = [
    (re.compile(r"(^|//|\.)youtube\.com/shorts(/|$|\?)"), "YouTube Shorts", "https://www.youtube.com/"),
    (re.compile(r"(^|//|\.)instagram\.com/(reels?|explore)(/|$|\?)"), "Instagram Reels/Explore", "https://www.instagram.com/"),
    (re.compile(r"(^|//|\.)facebook\.com/(reel|reels)(/|$|\?)"), "Facebook Reels", "https://www.facebook.com/"),
]


def kurzvideo_treffer(adresse):
    """(Name, Startseite), wenn die Adresse Reels, Shorts oder Explore ist, sonst None."""
    u = (adresse or "").strip().lower()
    for muster, name, start in KURZVIDEO_MUSTER:
        if muster.search(u):
            return name, start
    return None


# ---------- Panik-Knopf (3.11): gedrückt / geschafft pro Tag ----------
PANIK_DATEI = "panik.json"


def panik_zaehlen(was):
    f = DATEN / PANIK_DATEI
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    t = d.setdefault(date.today().isoformat(), {})
    k = "geschafft" if was == "geschafft" else "gedrueckt"
    t[k] = t.get(k, 0) + 1
    if k == "gedrueckt":   # 19.11. (Werte › Muster): nur die Stunde, bleibt auf dem Gerät
        st = t.setdefault("std", {})
        st[str(datetime.now().hour)] = st.get(str(datetime.now().hour), 0) + 1
    try:
        f.write_text(json.dumps(dict(sorted(d.items())[-400:]), ensure_ascii=False), "utf-8")
    except Exception:
        pass


def panik_stand():
    f = DATEN / PANIK_DATEI
    try:
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    montag = date.today() - timedelta(days=date.today().weekday())
    vor = montag - timedelta(days=7)
    summe = lambda von, bis, k: sum(v.get(k, 0) for t, v in d.items() if von.isoformat() <= t < bis.isoformat())
    morgen = date.today() + timedelta(days=1)
    return {"woche": summe(montag, morgen, "gedrueckt"), "wocheGeschafft": summe(montag, morgen, "geschafft"),
            "vorwoche": summe(vor, montag, "gedrueckt"), "gesamtGeschafft": sum(v.get("geschafft", 0) for v in d.values())}


MUSTER_MIN = 15 * 60      # so viel Konsum in einer Stunde zählt als „lang“
MUSTER_TAGE = 28
WT_MEHRZAHL = ["Montage", "Dienstage", "Mittwoche", "Donnerstage", "Freitage", "Samstage", "Sonntage"]


def muster_stand(a=None):
    """Werte › Woche (john 19.11.): wiederkehrende Zeiten mit viel Konsum, als Beobachtung mit Vorschlag.
    Nur aus vorhandenen Zahlen (Konsum-Minuten je Stunde, Panik-Knopf je Stunde; Bild-KI nur als schwaches Zusatzsignal),
    jedes Mal neu gerechnet, nichts gespeichert, nicht im Sync, nichts an die Vertrauensperson, keine Inhalte."""
    a = automatik_load() if a is None else a
    heute = date.today()
    lesen = lambda n: (json.loads((DATEN / n).read_text("utf-8")) if (DATEN / n).exists() else {})
    try:
        pan, erw = lesen(PANIK_DATEI), (lesen(ERWACHSEN_DATEI).get("tage") or {})
    except Exception:
        pan, erw = {}, {}
    tage = []   # (Datum, Konsum-Sekunden je Stunde, Panik-Stunden)
    for i in range(1, MUSTER_TAGE + 1):   # heute zählt nicht, der Tag ist noch nicht vorbei
        d = heute - timedelta(days=i)
        try:
            st = day_data(d).get("stunden") or []
        except Exception:
            continue
        p = {int(h) for h, n in ((pan.get(d.isoformat()) or {}).get("std") or {}).items() if n}
        if len(st) == 24 and (sum(sum(x[:3]) for x in st) > 0 or p):
            tage.append((d, [x[2] for x in st], p))
    if len(tage) < 14:
        return {"status": "zuwenig", "tage": len(tage), "brauche": 14}
    kandidaten = []
    def stunden_mit(liste, h):
        return [t for t in liste if t[1][h] >= MUSTER_MIN or h in t[2]]
    # a) fast jeden Tag (mindestens die Hälfte der Tage, mindestens 7)
    # Panik-Knopf ist ein bewusstes Signal: an 3 verschiedenen Tagen zur selben Stunde reicht schon
    ok = [h for h in range(24) if len(stunden_mit(tage, h)) >= max(7, len(tage) / 2) or sum(h in t[2] for t in tage) >= 3]
    kandidaten += [("taeglich", None, f) for f in muster_fenster(ok)]
    # b) ein Wochentag: mindestens 3 Wochen und 3 von 4 dieser Tage (ein einzelner Sonntag reicht nie)
    for wd in range(7):
        an = [t for t in tage if t[0].weekday() == wd]
        if len(an) < 3:
            continue
        ok = [h for h in range(24) if len(stunden_mit(an, h)) >= max(3, 0.75 * len(an))]
        kandidaten += [("wochentag", wd, f) for f in muster_fenster(ok)]
    if not kandidaten:
        return {"status": "keins", "tage": len(tage)}
    def gewicht(k):
        art, wd, (v, b) = k
        an = [t for t in tage if wd is None or t[0].weekday() == wd]
        return sum(t[1][h] for t in an for h in range(v, b)) / len(an)
    art, wd, (v, b) = max(kandidaten, key=gewicht)
    an = [t for t in tage if wd is None or t[0].weekday() == wd]
    konsum_t = [t for t in an if any(t[1][h] >= MUSTER_MIN for h in range(v, b))]
    panik_t = [t for t in an if any(h in t[2] for h in range(v, b))]
    minuten = round(sum(t[1][h] for t in konsum_t for h in range(v, b)) / 60 / max(1, len(konsum_t)))
    ueber_konsum = len(konsum_t) >= 3 and minuten >= 15   # sonst kam das Fenster über den Panik-Knopf zustande
    treffer = konsum_t if ueber_konsum else panik_t
    panik = sum(n for t in an for h, n in ((pan.get(t[0].isoformat()) or {}).get("std") or {}).items() if v <= int(h) < b)
    ki = sum(n for t in an for h, n in ((erw.get(t[0].isoformat()) or {}).get("ki") or {}).items() if v <= int(h) < b)
    versuche = sum(n for t in an for h, n in ((erw.get(t[0].isoformat()) or {}).get("std") or {}).items() if v <= int(h) < b)
    uhr = lambda h: f"{h % 24:02d}:00"
    zeit = f"zwischen {v} und {b} Uhr"
    wann = f"An {len(treffer)} von {len(an)} Tagen" if art == "taeglich" else f"An {len(treffer)} der letzten {len(an)} {WT_MEHRZAHL[wd]}"
    satz = f"{wann} warst du {zeit} länger in Social Media, Videos oder Spielen, im Schnitt {minuten} Minuten." if ueber_konsum \
        else f"{wann} hast du {zeit} den Panik-Knopf gebraucht."
    zusatz = []
    if panik and ueber_konsum:
        zusatz.append(f"In dieser Zeit hast du {panik}× den Panik-Knopf gedrückt. Gut, dass du ihn genutzt hast.")
    if versuche:
        zusatz.append(f"Der Schutz musste in dieser Zeit {versuche}× eingreifen.")
    elif ki >= 2:
        zusatz.append("Die Bild-KI hat in dieser Zeit ein paar Mal reagiert. Das kann auch ein Fehlalarm gewesen sein.")
    tage_regel = list(range(7)) if art == "taeglich" else [wd]
    # „schon abgedeckt“ nur durch eine Konsum-Ruhezeit auf allen Geräten (eine Sperrzeit nur für Steam am Mac zählt nicht)
    schon = any(r.get("art", "zeit") == "zeit" and r.get("konsum") and not r.get("geraete") and muster_deckt(r, v, b, tage_regel)
                for r in (locker_load(a).get("regeln") or []))
    return {"status": "muster", "tage": len(tage), "art": art, "wochentag": wd, "von": uhr(v), "bis": uhr(b), "regelTage": tage_regel,
            "treffer": len(treffer), "vonTagen": len(an), "minuten": minuten, "panik": panik, "versuche": versuche, "ki": ki,
            "satz": satz, "zusatz": " ".join(zusatz), "schonAbgedeckt": schon}


def muster_fenster(stunden):
    """[21, 22, 23, 9] -> [(21, 24), (9, 10)]. shortcut: kein Fenster über Mitternacht (23 + 0 sind zwei Fenster), reicht für Abende."""
    out = []
    for h in sorted(stunden):
        if out and out[-1][1] == h:
            out[-1] = (out[-1][0], h + 1)
        else:
            out.append((h, h + 1))
    return out


def muster_deckt(r, v, b, tage):
    """Liegt eine Sperrzeit schon über dem ganzen Fenster v–b (volle Stunden) an diesen Tagen?"""
    try:
        rv, rb = (int(x[:2]) * 60 + int(x[3:5]) for x in (r["von"], r["bis"]))
    except Exception:
        return False
    if not set(tage) <= set(r.get("tage") or range(7)):
        return False
    drin = lambda m: (rv <= m < rb) if rv < rb else (m >= rv or m < rb)
    return all(drin(m) for m in range(v * 60, b * 60, 15))


# Browser, die dem Mac keine Tab-Adresse verraten (kein AppleScript): sonst wäre die Erwachsenen-Sperre dort blind
FREMDE_BROWSER = {"firefox", "firefox developer edition", "firefox nightly", "opera", "opera gx", "vivaldi", "tor browser", "librewolf",
                  "waterfox", "orion", "zen", "zen browser", "duckduckgo", "mullvad browser", "chromium", "yandex", "floorp", "sigmaos",
                  "min", "thorium", "comet", "dia", "pale moon", "seamonkey", "whale", "naver whale", "maxthon", "epic", "ghostery",
                  "ghostery private browser", "falkon", "iridium", "basilisk", "palemoon", "ungoogled chromium"}


def fremder_browser(app, a=None):
    if sys.platform == "win32":   # Windows liest die Adresse jedes Browsers über UI Automation, auch Firefox
        return None
    n = (app or "").strip().lower().removesuffix(".app")
    if n not in FREMDE_BROWSER:
        return None
    x = schutz_handy(a, "Mac")
    if not x["browser"] or n in [y.strip().lower() for y in x["ausnahmen"]]:
        return None
    return app


def schutz_setzen(data):
    """Schalter setzen, für alle Geräte oder mit "geraet" nur für eines ("zuruecksetzen": eigene Werte des Geräts weg).
    Strenger geht sofort. Lockern (Schalter aus, neue Ausnahme) nur mit freigeschaltetem Locker-Timer,
    außer in der Entwicklerversion (ENTWICKLER): dort sind die Schalter frei an/aus. Neue Ausnahmen brauchen immer den Timer."""
    with AUTOMATIK_LOCK:
        a = automatik_load()
        roh = dict(a.get("schutz") or {})
        x = schutz_handy(a)
        lockern = any(str(v).lower() not in [y.lower() for y in x["ausnahmen"]] for v in data.get("ausnahmen") or [])
        if not ENTWICKLER:
            g = geraet_name(data.get("geraet")) if data.get("geraet") else ""
            vorher = schutz_handy(a, g) if g else x
            nachher = dict(x if data.get("zuruecksetzen") else vorher, **{k: bool(data[k]) for k in SCHUTZ_SCHALTER if k in data})
            lockern = lockern or any(vorher[k] and not nachher[k] for k in SCHUTZ_SCHALTER if k not in SCHUTZ_BETA_FREI)
            if not g:   # „Alle Geräte“ aus: lockert jedes Gerät ohne eigenen Wert
                lockern = lockern or any(data.get(k) is False and x[k] for k in SCHUTZ_SCHALTER if k not in SCHUTZ_BETA_FREI)
        l = a.get("locker") or {}
        if lockern and (l.get("pin") or LOCKER_OHNE_PIN) and not (locker_frei_status(l)["frei"] or locker_frei_status(l).get("immerFrei")):
            raise ValueError(locker_lockern_text(l))
        geraet = geraet_name(data.get("geraet")) if data.get("geraet") else ""
        eigen = dict(roh.get("geraete") or {})
        if geraet:
            e = dict(eigen.get(geraet) or {})
            if data.get("zuruecksetzen"):
                e = {}
            for k in SCHUTZ_SCHALTER:
                if k in data:
                    e[k] = bool(data[k])
            if e:
                eigen[geraet] = e
            else:
                eigen.pop(geraet, None)
        else:
            for k in SCHUTZ_SCHALTER:
                if k in data:
                    roh[k] = bool(data[k])
        roh["geraete"] = eigen
        if "ausnahmen" in data:
            boes = [v for v in data["ausnahmen"] or [] if re.search(r"porn|p0rn|xxx|hentai|nsfw|sex|nude|naked|nackt|erotik|erotic|onlyfans|fansly|xvideo|xhamster|stripchat|chaturbate|camsoda|bongacams|livejasmin|milf|fetis|hookup|18plus|adult|lewd|rule34|nhentai", str(v), re.I)]
            if boes and not ENTWICKLER:   # john 05.11. 18:51: Erwachsenen-Apps nie als Ausnahme
                raise ValueError(f"„{str(boes[0])[:60]}“ sieht nach einer Erwachsenen-App aus und kann keine Ausnahme sein.")
            roh["ausnahmen"] = [str(v).strip()[:120] for v in data["ausnahmen"] or [] if str(v).strip()][:50]
        if "bilderZiele" in data:   # Seiten und Apps, auf denen die Bild-KI am Computer schaut (Beta: frei änderbar)
            roh["bilderZiele"] = [str(v).strip().lower()[:80] for v in data["bilderZiele"] or [] if str(v).strip()][:60]
        if isinstance(data.get("bildRegeln"), dict):   # 3.21: je Klasse, was passiert
            roh["bildRegeln"] = bild_regeln({"bildRegeln": {**(roh.get("bildRegeln") or {}), **data["bildRegeln"]}})
        a["schutz"] = roh
        automatik_save(a)
        x = schutz_handy(a)
        sperre_protokoll(f"Schutz{' (' + geraet + ')' if geraet else ''}: {json.dumps({k: w for k, w in data.items() if k != 'ausnahmen'}, ensure_ascii=False)[:200]}")
    return {"ok": True, "schutz": x}


AW_SCHUTZ = {"versuch": 0.0, "verdacht": 0, "tag": "", "n": 0, "fehler": ""}   # ActivityWatch-Schutz (nur Mac)


def aw_zustand():
    """(antwortet, Minuten seit der letzten Meldung des Fenster- bzw. AFK-Watchers, je nachdem was älter ist)."""
    try:
        bs = aw_get("/buckets/")
        w, a, _ = pick_buckets()
    except Exception:
        return False, None
    jetzt, alter = datetime.now(timezone.utc), []
    for b in (w, a):
        # Ende des letzten Ereignisses statt last_updated: ein langes „nicht abwesend“ wächst nur in der Dauer,
        # last_updated bleibt dann beim Anfang stehen und sah nach einem toten Watcher aus (Neustart alle 5 Min)
        ende = None
        try:
            ev = aw_get(f"/buckets/{quote(b)}/events?limit=5") if b else []   # mehrere: gleicher Anfang, das kurze steht vorn
            if ev:
                ende = max(parse_ts(e["timestamp"]) + timedelta(seconds=e.get("duration") or 0) for e in ev)
        except Exception:
            pass
        lu = bs.get(b, {}).get("last_updated") if b else None
        if lu and (not ende or parse_ts(lu) > ende):
            ende = parse_ts(lu)
        alter.append((jetzt - ende).total_seconds() / 60 if ende else 999)
    return True, max(alter)


def aw_neu_starten(laeuft):
    """ActivityWatch (wieder) starten. Läuft die App, aber die Watcher sind aus: einmal beenden und neu öffnen."""
    if laeuft:
        subprocess.run(["osascript", "-e", 'tell application "ActivityWatch" to quit'], capture_output=True, timeout=20)
        time.sleep(6)
    # Reste beenden: ein übriggebliebener aw-server/aw-watcher ohne aw-qt blockiert den Neustart („Another instance is already running“)
    subprocess.run(["pkill", "-f", "/Applications/ActivityWatch.app/Contents/"], capture_output=True, timeout=20)
    time.sleep(2)
    r = subprocess.run(["open", "-g", "-a", "ActivityWatch"], capture_output=True, text=True, timeout=20)
    AW_SCHUTZ["fehler"] = "" if r.returncode == 0 else "ActivityWatch ist nicht installiert (Programme-Ordner)."
    heute = date.today().isoformat()
    if AW_SCHUTZ["tag"] != heute:
        AW_SCHUTZ.update(tag=heute, n=0)
    AW_SCHUTZ["n"] += 1
    sperre_protokoll("ActivityWatch war aus: neu gestartet" + (f" (Fehler: {r.stderr.strip()[:120]})" if r.returncode else ""))


def aw_waechter():
    """Mac: ActivityWatch lässt sich nicht einfach abschalten (john, 26.10.2026). Wird die App beendet oder ein Watcher
    im Menü ausgeschaltet, startet BlockEm sie nach etwa einer Minute wieder. Kein Schalter, wie die Erwachsenen-Sperre.
    Nach dem Aufwachen aus dem Ruhezustand wird eine Runde ausgelassen, weil die Watcher dann kurz hinterherhängen."""
    time.sleep(90)
    letzte = time.time()
    while True:
        time.sleep(30)
        jetzt = time.time()
        aufgewacht = jetzt - letzte > 90
        letzte = jetzt
        try:
            laeuft, alter = aw_zustand()
            aus = not laeuft or alter > 3
            AW_SCHUTZ["verdacht"] = 0 if (not aus or aufgewacht) else AW_SCHUTZ["verdacht"] + 1
            # zweimal hintereinander aus (eine Minute) und höchstens alle 5 Minuten ein Versuch
            if AW_SCHUTZ["verdacht"] >= 2 and jetzt - AW_SCHUTZ["versuch"] > 300:
                AW_SCHUTZ.update(versuch=jetzt, verdacht=0)
                aw_neu_starten(laeuft)
        except Exception as e:
            print("ActivityWatch-Schutz:", e)


# ---------- 3.17: Sperren am Windows-PC (john 01.11.: „alles, was du für Mac baust, auch für Windows“) ----------
# fenster-waechter-windows.ps1 läuft dauerhaft im Hintergrund (eingebautes PowerShell, UI Automation): vorderstes Fenster,
# Adresse im Browser, Adresse setzen, Fenster minimieren, Mitteilung, VPN, DNS. Eine Zeile JSON hin, eine zurück.
WIN = {"proc": None, "zeilen": None, "lock": threading.Lock(), "fehler": "", "h": 0}


RECHTE = {"zeit": 0.0, "liste": []}


def mac_rechte_fehlen():
    """Mac: Liste fehlender Voraussetzungen des Schutzes (21.11.): Bedienungshilfen, Bildschirmaufnahme, toter Fenster-Helfer.
    Leere Liste = alles da. Nur am Mac im Modus mac; 30 s zwischengespeichert. Wirkung: /api/status meldet sie, die Oberfläche zeigt nicht „Schutz aktiv“."""
    if sys.platform != "darwin" or MODUS != "mac":
        return []
    if time.time() - RECHTE["zeit"] < 30:
        return RECHTE["liste"]
    out = []
    try:
        import ax_lage
        if not ax_lage.vertraut():
            out.append("Bedienungshilfen fehlen: BlockEm sieht keine Fenstertitel. Systemeinstellungen › Datenschutz & Sicherheit › Bedienungshilfen: Fokus-Cockpit entfernen (−), neu hinzufügen (+) und einschalten.")
    except Exception as e:
        out.append(f"Bedienungshilfen lassen sich nicht prüfen ({type(e).__name__}).")
    try:
        import ctypes
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if not cg.CGPreflightScreenCaptureAccess():
            out.append("Bildschirmaufnahme fehlt: die Bild-KI sieht den Bildschirm nicht. Systemeinstellungen › Datenschutz & Sicherheit › Bildschirm- & Systemaudioaufnahme: Fokus-Cockpit entfernen (−), neu hinzufügen (+) und einschalten.")
    except Exception:
        pass
    if WIN.get("fehler"):
        out.append(f"Fenster-Helfer: {WIN['fehler']}")
    p = WIN.get("proc")
    if p is not None and p.poll() is not None and not WIN.get("fehler"):
        out.append("Der Fenster-Helfer läuft nicht (wird neu gestartet).")
    RECHTE.update(zeit=time.time(), liste=out)
    return out
WIN_BROWSER = {"chrome", "msedge", "brave", "firefox", "opera", "vivaldi", "arc", "chromium", "librewolf", "waterfox", "floorp", "zen",
               "thorium", "duckduckgo", "yandex", "browser", "whale", "maxthon", "palemoon", "seamonkey", "iexplore", "mullvadbrowser",
               "ungoogled-chromium", "iridium", "comet", "dia", "ghostery", "epic", "torch", "avast secure browser", "avastbrowser",
               "ccleaner browser", "slimjet", "cent browser", "chrome_proxy"}


def pc_name():
    return "Windows-PC" if sys.platform == "win32" else "Mac"


def win_browser(app):
    return (app or "").strip().lower().removesuffix(".exe") in WIN_BROWSER


def win_befehl(was, **daten):
    """Befehl an den Fenster-Helfer, Antwort als dict ({} bei Fehler). Startet den Helfer bei Bedarf (neu).
    Windows: PowerShell (fenster-waechter-windows.ps1), Mac ab 3.19: osascript mit fenster-mac.js (nur „vorn“)."""
    if sys.platform not in ("win32", "darwin"):
        return {}
    import subprocess, queue
    win = sys.platform == "win32"
    with WIN["lock"]:
        if time.time() < WIN.get("pause_bis", 0):
            return {}
        p = WIN["proc"]
        if p is not None and p.poll() is None and WIN.get("anzahl", 0) > 3000:
            _helfer_beenden(p)   # ab und zu frisch starten (hält den Speicher klein)
            p = None
        if p is None or p.poll() is not None:
            skript = HERE / ("fenster-waechter-windows.ps1" if win else "fenster-mac.js")
            if not skript.exists():
                WIN["fehler"] = f"{skript.name} fehlt im Cockpit-Ordner"
                return {}
            cmd = (["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(skript)] if win
                   else ["osascript", "-l", "JavaScript", str(skript)])
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=0x08000000 if win else 0)   # Windows: ohne Fenster
            except Exception as e:
                WIN["fehler"] = f"Fenster-Helfer startet nicht: {e}"[:200]
                return {}
            q = queue.Queue()
            def lesen(st=p.stdout, q=q):
                for z in st:
                    q.put(z)
                q.put(None)
            threading.Thread(target=lesen, daemon=True).start()
            WIN.update(proc=p, zeilen=q, neu=time.time(), anzahl=0)
        q = WIN["zeilen"]
        try:
            p.stdin.write(json.dumps(dict(was=was, **daten), ensure_ascii=False) + "\n")
            p.stdin.flush()
            # erster Befehl wartet, bis PowerShell geladen ist (einige Sekunden), danach reichen 8 s
            z = q.get(timeout=(25 if win else 10) if time.time() - WIN.get("neu", 0) < 30 else 8)
        except Exception as e:
            z = None
            WIN["fehler"] = f"Fenster-Helfer antwortet nicht ({type(e).__name__}), wird neu gestartet"
        if z is None:
            _helfer_beenden(p)
            WIN["proc"] = None
            WIN["pannen"] = WIN.get("pannen", 0) + 1
            if WIN["pannen"] >= 3:   # klappt dreimal nicht: 10 Minuten Pause (Mac nimmt so lange den alten Weg)
                WIN.update(pannen=0, pause_bis=time.time() + 600)
                WIN["fehler"] = "Fenster-Helfer startet nicht richtig, nächster Versuch in 10 Minuten"
            return {}
        WIN["anzahl"] = WIN.get("anzahl", 0) + 1
        WIN["pannen"] = 0
        try:
            r = json.loads(z)
        except ValueError:
            return {}
        WIN["fehler"] = "" if not r.get("fehler") else f"Fenster-Helfer: {r['fehler']}"[:200]
        return r if isinstance(r, dict) else {}


def win_vorn():
    """{"h", "app", "titel", "adresse"} des vordersten Fensters oder None (3.19: aus dem gemeinsamen Fenster-Blick)."""
    return vorne(2.5) if sys.platform == "win32" else None


def win_wegschalten(app, ziel):
    """Browser: Adresse im aktiven Tab auf ziel setzen. Sonst (oder wenn das nicht geht): Fenster minimieren, ziel öffnen. (ok, Fehler)"""
    if win_browser(app) and win_befehl("setzen", h=WIN["h"], u=ziel).get("ok"):
        return True, ""
    win_befehl("weg", h=WIN["h"])
    try:
        os.startfile(ziel)
    except Exception as e:
        return False, f"Sperren ging nicht ({e})"[:200]
    return True, ""


def win_dns_einrichten(aus=False):
    """DNS-Filter (CleanBrowsing) für alle aktiven Netzwerke am Windows-PC. Windows fragt einmal, ob du die Änderung erlaubst."""
    import base64, subprocess
    ips = ",".join(f"'{ip}'" for ip in MAC_DNS_IPS + MAC_DNS_IP6)
    innen = ("Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex "
             + ("-ResetServerAddresses" if aus else f"-ServerAddresses @({ips})") + " }; Clear-DnsClientCache")
    kodiert = base64.b64encode(innen.encode("utf-16-le")).decode()
    aussen = ("try { $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              f"-ArgumentList '-NoProfile','-EncodedCommand','{kodiert}'; exit $p.ExitCode }} catch {{ exit 5 }}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", aussen], capture_output=True, text=True, timeout=180,
                       creationflags=0x08000000)
    if r.returncode != 0:
        raise ValueError(("Nicht ausgeschaltet: " if aus else "Nicht eingerichtet: ") +
                         ("abgebrochen" if r.returncode == 5 else (r.stderr or "").strip()[:160] or f"Fehler {r.returncode}"))
    MAC_DNS["t"] = 0
    time.sleep(1)
    _mac_dns_messen()
    sperre_protokoll(f"DNS-Filter am Windows-PC {'ausgeschaltet' if aus else 'eingerichtet'}")
    return {"ok": True, "dienste": ["alle aktiven Netzwerke"], "aktiv": MAC_DNS["ok"]}


# ---------- 3.17: Bild-KI am Mac und Windows-PC (gleiches Modell wie am Handy, bild_ki.py, nur numpy) ----------
# Schaut nur, wenn vorne eine Seite oder App aus der Liste ist (Social Media, einstellbar), alle 3,5 s, und nur wenn sich
# der Bildschirm geändert hat. Eindeutig: sofort weg; anzüglich: beim zweiten Treffer innerhalb von 15 s.
BILD_ZIELE_STD = ("instagram.com", "tiktok.com", "x.com", "twitter.com", "reddit.com", "facebook.com", "snapchat.com", "tumblr.com",
                  "pinterest.com", "threads.net", "bsky.app", "9gag.com", "imgur.com", "discord.com", "web.telegram.org",
                  "instagram", "tiktok", "telegram", "discord", "reddit", "snapchat", "bildersuche")
# „bildersuche“ (3.19): Bildersuche bei Google, Bing, DuckDuckGo, Ecosia, Yandex, Startpage, Qwant, Yahoo
BILDERSUCHE = re.compile(r"^(?:https?://)?(?:[^/?#]*\.)?(?:google\.[a-z.]+/(?:search\?[^#]*(?:tbm=isch|udm=2)|imgres)|images\.google\.|"
                         r"bing\.com/images|duckduckgo\.com/\?[^#]*ia=images|ecosia\.org/images|yandex\.[a-z]+/images|"
                         r"startpage\.com/[^#]*cat=images|qwant\.com/\?[^#]*t=images|images\.search\.yahoo\.com)", re.I)
BILD_NAMEN = {"nackt": "Nacktbild", "unterwaesche": "Unterwäsche", "bademode": "Bikini/Bademode", "anzueglich": "Anzügliches Bild"}
BILD_KI = {"letzt": None, "fehler": "", "verdacht": 0.0, "abdruck": None, "puls": 0.0, "pruefungen": 0, "gespart": 0, "aussetzer": 0, "gemeldet": 0.0}
BILD_RUECK_SPERRE = threading.Lock()
BILD_ZIELE_VIDEO = ("youtube.com", "netflix.com", "twitch.tv", "youtube", "netflix", "twitch")   # 25.11. (john): Twitch wie YouTube


def bild_ziel(app, adresse, ziele):
    """Passender Eintrag der Liste, wenn vorne eine Social-Media-Seite oder -App ist."""
    m = re.match(r"^\s*(?:[a-z][a-z0-9+.-]*://)?([^/?#:\s]+)", (adresse or "").lower())
    host = m.group(1) if m else ""
    n = (app or "").strip().lower().removesuffix(".app").removesuffix(".exe")
    for z in ziele:
        if z == "bildersuche":
            if adresse and BILDERSUCHE.search(adresse.strip()):
                return z
            continue
        if "." in z:
            if host and (host == z or host.endswith("." + z)):
                return z
        elif z and n == z or (z and not adresse and n.startswith(z)):
            return z
    return None


def bild_aufnehmen(rahmen=None, gross=False):
    """Bildschirm (Hauptbildschirm, verkleinert) als RGB-Array, oder None. Mit rahmen (x0,y0,x1,y1 in Punkten): nur dieses Fenster,
    auf jedem Bildschirm (john 09.11.: Chrome lag auf dem zweiten Bildschirm, -m sah nur den ersten)."""
    import tempfile
    from bild_ki import bmp_lesen
    d = Path(tempfile.gettempdir()) / "fokus-cockpit-bild"
    d.mkdir(exist_ok=True)
    bmp = d / "schirm.bmp"
    if sys.platform == "win32":
        if not win_befehl("bild", pfad=str(bmp), hoehe=448).get("ok"):
            return None
    else:
        if rahmen and sys.platform == "darwin":   # 09.11.: Hilfsprogramm blockem-auge, ca. 20 ms statt screencapture + sips ca. 200 ms
            try:
                import auge
                f = auge.foto(rahmen, 2560 if gross else 720)
                if f is not None:
                    BILD_KI["rahmen"] = f[1]   # tatsächlich fotografierter Teil (Fenster ragt evtl. über den Bildschirm hinaus)
                    return f[0]
            except Exception:
                pass
        art = "jpg" if gross else "png"   # gross (09.11.): bis 2560 px, daraus werden die Einzelbilder der Seite geschnitten (ein Foto statt eines je Bild)
        png = d / f"schirm.{art}"
        try:
            png.unlink()   # altes Foto weg, sonst prüft die KI still ein veraltetes Bild
        except OSError:
            pass
        wo = ["-R", f"{int(rahmen[0])},{int(rahmen[1])},{int(rahmen[2] - rahmen[0])},{int(rahmen[3] - rahmen[1])}"] if rahmen else ["-m"]
        r = subprocess.run(["screencapture", "-x", *wo, "-t", art, str(png)], capture_output=True, timeout=10)
        if r.returncode != 0 or not png.exists() or b"could not create" in r.stderr:
            BILD_KI["fehler"] = "Bildschirmfoto ging nicht. Erlaubnis: Systemeinstellungen › Datenschutz & Sicherheit › Bildschirmaufnahme."
            return None
        subprocess.run(["sips", "-s", "format", "bmp", "-Z", "2560" if gross else "720", str(png), "--out", str(bmp)], capture_output=True, timeout=10)
    try:
        return bmp_lesen(bmp)
    finally:
        try:
            bmp.unlink()
        except OSError:
            pass


def fenster_rahmen(app):
    """Rahmen des vordersten Fensters über die Bedienungshilfen (nur Mac, nur mit Recht), sonst None = Hauptbildschirm."""
    if sys.platform != "darwin":
        return None
    try:
        import ax_lage
        return ax_lage.fenster(app)
    except Exception:
        return None


def bild_pruefen():
    rest_alt = BILD_KI.pop("rest", 0)   # offene Bilder der letzten Runde; ohne neue Runde nicht stehen lassen (sonst läuft die Schleife im Schnellgang)
    a = automatik_load()
    x = schutz_handy(a, "Mac")
    if not x["bilder"]:
        decke_weg()
        ax_weg()
        return
    v = vorne(4.0)   # 3.19: gemeinsamer Fenster-Blick, kein eigenes osascript mehr
    if not v:
        if AX["fl"]:
            ax_weg()   # kein Blick auf das vorderste Fenster: keine Fläche stehen lassen
        return
    app, adresse = v["app"], v["adresse"]
    ziele = list(x["bilderZiele"]) + (list(BILD_ZIELE_VIDEO) if x["bilderVideo"] else [])
    try:
        import bild_ki
        bild_ki.ZEICHNUNGEN = bool(x["zeichnungen"])   # 10.11.: Hentai/explizite Zeichnungen auch am Computer (wie Bilder.ZEICHNUNGEN am Handy)
    except ImportError:
        pass
    h = BILD_KI.get("hinten")   # john 09.11.: Klick auf den anderen Bildschirm darf den Schutz nicht aushebeln – Browser bleibt dran, solange sein Fenster frei sichtbar ist
    if h and sys.platform == "darwin" and not bild_ziel(app, adresse, ziele):
        try:
            import ax_lage
            import auge
            frei = None if h[0] == app else auge.sichtbar(h[0])
            if h[0] == app or not (ax_lage.frei_sichtbar(h[0], app) if frei is None else frei):
                BILD_KI["hinten"] = None if h[0] == app else h   # Browser selbst vorne mit harmloser Seite: vergessen
            else:
                app, adresse = h
        except Exception:
            pass
    if AX["fl"] and not (AX["wo"] and AX["wo"][0] == app):
        ax_weg()   # Browser nicht mehr vorne (Zoom, Teams, FaceTime ...): Flächen sofort weg
    ziel = bild_ziel(app, adresse, ziele)
    if BILD_KI.get("decke") and (not ziel or BILD_KI["decke"]["wo"] != (app, adresse)):
        decke_weg()   # andere App oder Seite: Abdeckung weg
    if AX["fl"] and (not ziel or AX["wo"] != (app, adresse)):
        ax_weg()
    if not ziel:
        return
    # niemand bedient das Gerät seit 2 Minuten: nur alle 10 s schauen (Video läuft evtl. weiter)
    if v.get("leerlauf", 0) > 120 and time.time() - BILD_KI.get("zuletzt", 0) < 10:
        return
    BILD_KI["zuletzt"] = time.time()
    try:
        import numpy as np
        from bild_ki import werten
    except ImportError:
        numpy_einrichten()
        bild_ausfall()
        return
    ru = AX.get("ruhig")
    if ru and not BILD_KI.get("decke"):
        d_ = ax_liste(app) if ru[:2] == (app, adresse) and time.time() < ru[3] else None
        if d_ and frozenset(m["id"] for m in d_["medien"]) == ru[2]:
            ax_nachziehen(d_["medien"], ganz=not d_.get("teil"))
            BILD_KI["gespart"] += 1
            return   # dieselben, schon entschiedenen Bilder: nichts zu fotografieren
        AX["ruhig"] = None
    BILD_KI["rahmen"] = fenster_rahmen(app)
    if BILD_KI["rahmen"]:
        BILD_KI["hinten"] = (app, adresse)
    t_foto = time.time()
    bild = bild_aufnehmen(BILD_KI["rahmen"], gross=bool(BILD_KI["rahmen"]))
    BILD_KI["foto_ms"] = round((time.time() - t_foto) * 1000)
    BILD_KI["foto"] = bild
    if bild is None:
        bild_ausfall()
        return
    nachsehen = False
    if BILD_KI.get("decke"):
        d = BILD_KI["decke"]
        jetzt = time.time()
        tot = jetzt - d["start"] > d["dauer"] or (d["proz"] is not None and d["proz"].poll() is not None)
        if not tot and d.get("durchsicht") is None:
            if jetzt - d["start"] < 1.5:
                return   # Fenster erscheint gerade erst
            d["durchsicht"] = not decke_im_foto(bild, d["box"])   # blendet das System die Abdeckung im Foto aus (Windows, manche macOS)?
        if not tot and d["durchsicht"]:
            nachsehen = True   # Foto zeigt, was darunter liegt: rechnen, Abdeckung bleibt liegen
        else:
            if not tot and (jetzt - d["seit"] < 4 or (v.get("leerlauf", 0) >= jetzt - d["seit"] and decke_bleibt(bild))):
                return   # höchstens alle 4 s nachsehen, und nur wenn bedient (Scrollen, Maus, Tasten) oder außen etwas anders ist
            # kurz drunter schauen: Abdeckung weg, Foto, sofort wieder zu; gerechnet wird am Foto ohne Abdeckung
            alt_box = d["box"]
            decke_weg()
            time.sleep(0.15)
            try:
                neu = bild_aufnehmen(BILD_KI.get("rahmen"), gross=bool(BILD_KI.get("rahmen")))
            except Exception:
                neu = None
            BILD_KI["foto"] = neu if neu is not None else BILD_KI.get("foto")
            decke_zeigen(alt_box, neu if neu is not None else bild, (app, adresse))   # auch wenn das Foto scheitert: wieder zu
            if neu is None:
                return
            bild = neu
            BILD_KI["abdruck"] = None
            nachsehen = True
    # nur rechnen, wenn sich der Bildschirm geändert hat (spart Akku/Strom beim Lesen oder Video-Schauen)
    h, w = bild.shape[:2]
    abdruck = bild[::max(1, h // 24), ::max(1, w // 24)].astype(np.int16)
    alt = BILD_KI["abdruck"]
    BILD_KI["abdruck"] = abdruck
    if alt is not None and alt.shape == abdruck.shape and float(np.abs(alt - abdruck).mean()) < 2.0 and not rest_alt \
            and not any(f["video"] for i, f in list(AX["fl"].items()) if i != "decke"):   # Rest offen oder Video abgedeckt: weiterprüfen
        BILD_KI["gespart"] += 1
        return
    # Laptop-Schutz 09.11. (john: „wie am Handy nur das Bild“): im Browser jedes Bild/Video der Seite einzeln prüfen und nur dieses abdunkeln
    if seite_pruefen(app, adresse, x, ziel):
        return
    # sonst (andere App, Arc, JavaScript im Browser aus): ganzer Bildschirm, bei Verdacht die 6 kleinen Ausschnitte
    w_ = BILD_KI.get("web") if sys.platform == "darwin" else None
    f_ = BILD_KI.get("rahmen")
    if w_ and f_ and BILD_KI.get("ax_medien"):
        bild = bild.copy()
        sx_, sy_ = w / (f_[2] - f_[0]), h / (f_[3] - f_[1])
        for m_ in BILD_KI["ax_medien"]:
            bild[max(0, int((m_["y0"] - f_[1]) * sy_)):max(0, int((m_["y1"] - f_[1]) * sy_)), max(0, int((m_["x0"] - f_[0]) * sx_)):max(0, int((m_["x1"] - f_[0]) * sx_))] = 128
    r = werten(bild, raster=True)
    if w_ and f_ and r.get("box"):   # john 09.11.: Feld-Raster lag neben den Bildern; im Browser lieber die ganze Seite decken
        fw, fh = f_[2] - f_[0], f_[3] - f_[1]
        r["box"] = [max(0.0, (w_[0] - f_[0]) / fw), max(0.0, (w_[1] - f_[1]) / fh), min(1.0, (w_[2] - f_[0]) / fw), min(1.0, (w_[3] - f_[1]) / fh)]
    BILD_KI["pruefungen"] += 1
    BILD_KI["fehler"] = ""
    BILD_KI["aussetzer"] = 0
    BILD_KI["letzt"] = dict(r, wo=ziel, zeit=datetime.now().strftime("%H:%M:%S"))
    t = time.time()
    klasse = r.get("klasse") or ("nackt" if r["stufe"] == 2 else "anzueglich" if r["stufe"] == 1 else "")
    aktion = x["bildRegeln"].get(klasse, "aus") if klasse in BILD_KLASSEN else "aus"
    if nachsehen and aktion not in ("weg", "zweiter"):
        decke_weg()   # Bild ist weg (weitergescrollt): Abdeckung weg
    was = BILD_NAMEN.get(klasse, "Bild")
    titel = "Inhalt ausgeblendet"   # 19.11. (john): nie nennen, was erkannt wurde (vorher „Bild-KI: Nacktbild“)
    if aktion == "hinweis":
        ki_anzeige("Hinweis", "Nur Hinweis, nichts weggeschaltet.")
        return
    weg = aktion == "weg" or (aktion == "zweiter" and (nachsehen or t - BILD_KI["verdacht"] < 15))   # nachsehen: war schon bestätigt
    BILD_KI["verdacht"] = t if aktion == "zweiter" and not weg else 0.0
    if not weg:
        return
    # Laptop-Schutz 09.11. (john): wie am Handy abdecken statt wegschalten; geht das nicht, wie bisher wegschalten
    d = BILD_KI.get("decke")
    if nachsehen and d and d["box"] == r.get("box") and time.time() - d["start"] < d["dauer"] - 3:
        d["seit"] = time.time()   # dieselbe Stelle: Abdeckung liegen lassen, nicht neu starten
        d["raster"], d["maske"] = decke_umfeld(bild, d["box"])
        gedeckt = True
    else:
        gedeckt = decke_zeigen(r.get("box"), bild, (app, adresse))
    ok, fehler = (True, "") if gedeckt else (win_wegschalten(app, f"http://localhost:{CFG['port']}/#fokus") if sys.platform == "win32"
                                             else bild_weg_mac(app))
    if nachsehen and gedeckt:
        return   # dasselbe Bild nach kurzem Nachsehen wieder abgedeckt: nicht erneut melden
    sperre_protokoll(f"Bild-KI in {app} -> {'abgedeckt' if gedeckt else 'ok' if ok else fehler}")   # 19.11.: ohne Seite und Art
    if t - BILD_KI.get("gezaehlt", 0) > 30:   # dasselbe Bild nach dem Scrollen nicht mehrfach zählen
        BILD_KI["gezaehlt"] = t
        erwachsen_versuch(pc_name(), "Bild")
    ki_anzeige(titel, ("Abgedeckt." if gedeckt else "Weggeschaltet.") if ok
               else f"Wegschalten ging nicht: {fehler}"[:140])


def decke_umfeld(bild, box):
    """Raster des Bildschirms (32x32) und Maske „außerhalb der Abdeckung“. Unten (Hinweis-Anzeige, Dock) zählt nicht."""
    import numpy as np
    h, w = bild.shape[:2]
    ys, xs = np.arange(0, h, max(1, h // 32)), np.arange(0, w, max(1, w // 32))
    yy, xx = np.meshgrid(ys / h, xs / w, indexing="ij")
    rand = 0.03
    drin = (xx >= box[0] - rand) & (xx <= box[2] + rand) & (yy >= box[1] - rand) & (yy <= box[3] + rand)
    return bild[ys][:, xs].astype(np.int16), ~drin & (yy < 0.78)


def decke_zeigen(box, bild, wo):
    """Abdeckung über den Ausschnitt mit dem Treffer legen (Mac: abdeck-mac.js, Windows: Fenster-Helfer). True, wenn sie liegt."""
    if not box:
        return False
    decke_weg()
    gross = (box[2] - box[0]) * (box[3] - box[1]) > 0.8
    dauer = 6 if gross else 30   # ganzer Bildschirm: kein Rand zum Vergleichen, darum nach 6 s neu schauen; sonst spätestens nach 30 s
    try:
        if sys.platform == "win32":
            if not win_befehl("abdecken", x0=box[0], y0=box[1], x1=box[2], y1=box[3], dauer=dauer + 2).get("ok"):
                return False
            proz = None
        elif sys.platform == "darwin" and BILD_KI.get("rahmen"):   # Foto zeigt nur das Fenster: Fläche in Bildschirmpunkten über den Flächen-Prozess
            r = BILD_KI["rahmen"]
            w, h = r[2] - r[0], r[3] - r[1]
            AX["wo"] = wo
            AX["fl"]["decke"] = {"box": (r[0] + box[0] * w, r[1] + box[1] * h, r[0] + box[2] * w, r[1] + box[3] * h), "t": time.time(), "video": False}
            if not ax_senden():
                return False
            proz = None
        elif sys.platform == "darwin":
            if not (HERE / "abdeck-mac.js").exists():
                return False
            proz = subprocess.Popen(["osascript", "-l", "JavaScript", str(HERE / "abdeck-mac.js")] + [f"{v:.4f}" for v in box] + [str(dauer + 2)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.3)
            if proz.poll() is not None:   # Skript abgebrochen: lieber wegschalten
                return False
        else:
            return False
    except Exception as e:
        BILD_KI["fehler"] = f"Abdecken ging nicht: {e}"[:200]
        return False
    g, m = decke_umfeld(bild, box)
    BILD_KI["decke"] = {"proz": proz, "box": box, "wo": wo, "start": time.time(), "seit": time.time(), "dauer": dauer, "raster": g, "maske": m,
                        "durchsicht": None}
    return True


def decke_im_foto(bild, box):
    """True, wenn im Foto an der Stelle die eigene Abdeckung (BlockEm-Dunkel 14/19/32) zu sehen ist."""
    import numpy as np
    h, w = bild.shape[:2]
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    mx, my = (x1 - x0) // 8, (y1 - y0) // 8
    teil = bild[y0 + my:y1 - my, x0 + mx:x1 - mx].reshape(-1, 3).astype(np.int16)
    if not len(teil):
        return True
    return float((np.abs(teil - np.array([14, 19, 32])).max(axis=1) < 14).mean()) > 0.7


def decke_bleibt(bild):
    """Abdeckung liegen lassen, solange sich außerhalb nichts bewegt (kein Scrollen) und sie nicht zu alt ist."""
    import numpy as np
    d = BILD_KI["decke"]
    if time.time() - d["start"] > d["dauer"] or (d["proz"] is not None and d["proz"].poll() is not None):
        return False
    g, m = decke_umfeld(bild, d["box"])
    if g.shape != d["raster"].shape:
        return False
    if m.sum() < 20:
        return True
    return float(np.abs(g - d["raster"])[m].mean()) < 4.0


# ---------- Laptop-Schutz 09.11.2026: Bilder in der Webseite finden, einzeln prüfen, nur sie abdunkeln (wie SperrDienst am Handy) ----------
# Braucht im Browser „JavaScript von Apple Events erlauben“ (Chrome/Brave/Edge: Darstellung › Entwickler; Safari: Entwickler-Menü).
SEITE_APPS = {"chrome": "Google Chrome", "brave": "Brave Browser", "edge": "Microsoft Edge", "safari": "Safari"}
SEITE = {"url": "", "gesehen": set(), "js": None, "fehler": "", "vfrei": {}, "host": ""}

# AX-Weg 09.11.: kein JavaScript aus Apple Events (anderes Chrome-Profil, Firefox, Arc): Lage der Bilder über die Bedienungshilfen (ax_lage.py),
# Treffer deckt der Dauerprozess abdeck-flaechen-mac.js mit kleinen Flächen genau ab. Braucht das Bedienungshilfen-Recht für das Python-Programm des Servers.
AX = {"proz": None, "fl": {}, "wo": None, "gemeldet": False}
AX_SPERRE = threading.RLock()   # Bild-KI-Runde und der schnelle Nachzieher (ax_folger) teilen sich Flächen und ax_lage


def ax_liste(app):
    """Wie seite_js(app, "liste"), aber über die Bedienungshilfen. dict oder None (kein Recht, kein Browser, Fehler)."""
    if sys.platform != "darwin" or not any(k in (app or "").lower() for k in ("chrome", "brave", "edge", "arc", "chromium", "vivaldi", "opera", "safari", "firefox")):
        return None
    try:
        import ax_lage
        if not ax_lage.vertraut():
            if not AX["gemeldet"]:
                AX["gemeldet"] = True
                sperre_protokoll("Bild-KI: Bedienungshilfen-Recht fehlt für genaue Abdeckung")
            return None
        with AX_SPERRE:
            m, i = ax_medien(app)
            web, teil = i.get("web"), bool(i.get("teil"))
        if time.time() - AX.get("diag", 0) > 15:   # Diagnose 09.11.: was liefern die Bedienungshilfen?
            AX["diag"] = time.time()
            sperre_protokoll(f"Bild-KI AX {app}: {'—' if m is None else len(m)} Bilder/Videos, {i.get('knoten', '?')} Knoten, {i.get('ms', '?')} ms"
                             f"{', abgebrochen' if i.get('teil') else ''}{', ' + i['fehler'] if i.get('fehler') else ''}"
                             f", {'Auge' if i.get('auge') else 'Python'}, Karten {'/'.join(map(str, AX.get('anker') or ('?', '?')))} am Bild, Foto {BILD_KI.get('foto_ms', '?')} ms, Runde {BILD_KI.get('runde_ms', '?')} ms, offen {BILD_KI.get('rest', 0)}, Flächen {len(AX['fl'])}")
    except Exception as e:
        SEITE["fehler"] = f"AX: {e}"[:200]
        return None
    if m is None:
        return None
    return {"ok": True, "medien": m, "dunkle": [], "nsfw": False, "web": web, "teil": teil}


def ax_medien(app, zeit=0.9):
    """(Liste, Info) wie _ax_medien; merkt dazu Messzeit und Webseiten-Bereich für den Flächen-Prozess (10.11.: Flächen folgen dem Scrollen)."""
    t0 = time.time()
    m, i = _ax_medien(app, zeit)
    AX["mess"], AX["web"] = (t0 + time.time()) / 2, i.get("web")   # Mitte der Messung: die Lagen werden über ihre ganze Dauer gelesen
    return m, i


def _ax_medien(app, zeit=0.9):
    """(Liste, Info) über das Hilfsprogramm blockem-auge (schnell), sonst über ax_lage (Python). Liste None = nichts brauchbar."""
    try:
        import auge
        m = auge.medien(app, zeit)
        if m is not None or auge.INFO.get("fehler", "").startswith(("kein AXWebArea", "kein Fenster", "Programm")):   # fehlt dem Hilfsprogramm das Recht: Python-Weg
            return m, dict(auge.INFO, auge=True)
    except Exception:
        pass
    import ax_lage
    m = ax_lage.medien(None, app, zeit)
    return m, dict(ax_lage.INFO)


def ax_programm():
    """Programm, das die Karten zeichnet: blockem-decke (10.11.: hängt jede Karte an ihr Bild, läuft beim Scrollen selbst mit), sonst das alte JS."""
    for p in (HERE / "Fokus-Cockpit.app/Contents/MacOS/blockem-decke", HERE / "mac-app/blockem-decke"):
        if p.exists():
            return [str(p)], True
    return (["osascript", "-l", "JavaScript", str(HERE / "abdeck-flaechen-mac.js")], False) if (HERE / "abdeck-flaechen-mac.js").exists() else (None, False)


def ax_anker_lesen(p):
    """Liest die Meldungen von blockem-decke ({"karten": n, "anker": m}); m = Karten, die an ihrem Bild hängen und selbst mitlaufen."""
    try:
        for z in p.stdout:
            d = json.loads(z)
            if AX.get("proz") is p:
                AX["anker"] = (int(d.get("karten", 0)), int(d.get("anker", 0)))
    except Exception:
        pass


def ax_senden():
    """Aktuelle Flächen an den Overlay-Prozess schicken (startet ihn bei Bedarf). True, wenn angekommen."""
    prog, nativ = ax_programm()
    if not prog:
        return False
    try:
      with AX_SPERRE:
        if AX["proz"] is None or AX["proz"].poll() is not None:
            AX["proz"] = subprocess.Popen(prog, stdin=subprocess.PIPE, stdout=subprocess.PIPE if nativ else subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, text=True)
            AX["anker"] = None
            if nativ:
                threading.Thread(target=ax_anker_lesen, args=(AX["proz"],), daemon=True).start()
            AX["gesendet"] = None
            if not AX.get("folger"):
                AX["folger"] = True
                threading.Thread(target=ax_folger, daemon=True).start()
        # t = wann die Lage gemessen wurde: der Flächen-Prozess rechnet den Scrollweg seit dann drauf (abdeck-flaechen-mac.js)
        liste = [dict({"id": i, "x0": round(f["box"][0]), "y0": round(f["box"][1]), "x1": round(f["box"][2]), "y1": round(f["box"][3])},
                      **({"t": round(f["lage"], 3)} if f.get("lage") else {})) for i, f in list(AX["fl"].items())]
        web = AX.get("web") if any(i != "decke" for i in AX["fl"]) else None   # Prüfer 10.11.: altes web darf die große Fläche „decke“ nicht beschneiden
        text = json.dumps({"flaechen": liste, **({"web": [round(v) for v in web]} if web and len(web) == 4 else {}),
                           **({"app": AX["wo"][0]} if AX.get("wo") else {})})
        if text != AX.get("gesendet"):   # nur bei Änderung schicken; mit neuer Messzeit t ist das jede Messung des Nachziehers (8-mal je Sekunde, eine Zeile)
            AX["proz"].stdin.write(text + "\n")
            AX["proz"].stdin.flush()
            AX["gesendet"] = text
        return True
    except Exception as e:
        BILD_KI["fehler"] = f"Abdecken (AX) ging nicht: {e}"[:200]
        AX["proz"] = None
        return False


def ax_weg():
    with AX_SPERRE:
        _ax_weg()


def _ax_weg():
    """Alle AX-Flächen weg (andere Seite/App, Schutz aus). Die große Fläche „decke“ bleibt, die räumt decke_weg ab."""
    d = AX["fl"].pop("decke", None)
    AX["fl"].clear()
    AX["web"] = AX["mess"] = None
    if d is not None:
        AX["fl"]["decke"] = d
        ax_senden()
        return
    AX["wo"] = None
    p, AX["proz"] = AX["proz"], None
    if p is not None:
        try:
            p.stdin.write('{"ende":1}\n')
            p.stdin.flush()
            p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(timeout=1)
        except Exception:
            p.kill()


def ax_nachziehen(medien, folgen=False, ganz=True):
    """Gemerkte Flächen auf die neue Lage der Bilder setzen, verschwundene weg (nur bei vollständiger Liste). Sieht das Bildschirmfoto
    die Fläche (keine Durchsicht), Videos nach 5 s kurz frei zum Neu-Prüfen; die Kennungen kommen zurück. folgen: nur Lage (Nachzieher)."""
    frisch = set()
    with AX_SPERRE:
        tr = SEITE.get("treffer")
        if not AX["fl"] and not tr:
            return frisch
        jetzt = {m["id"]: m for m in medien}
        for m in medien if tr else ():   # john 09.11.: hoch- und zurückgescrollt – bekanntes Treffer-Bild sofort wieder zu, nicht erst neu prüfen
            if not m.get("video") and m["id"] not in AX["fl"] and m["id"] + "|" + (m.get("q") or "") in tr:
                AX["fl"][m["id"]] = {"box": (m["x0"], m["y0"], m["x1"], m["y1"]), "t": time.time(), "video": False}
        t = time.time()
        lage = AX.get("mess")   # medien stammt aus der letzten Messung (Aufrufer messen direkt davor)
        for i in list(AX["fl"]):
            if i == "decke":   # große Fläche vom Ganzer-Bildschirm-Weg: verwaltet decke_*
                continue
            m, f = jetzt.get(i), AX["fl"].get(i)
            if f is None:
                continue
            if not m:
                if ganz:
                    AX["fl"].pop(i, None)
                continue
            if not folgen and f["video"] and not AX.get("durchsicht") and t - f["t"] > 5:
                AX["fl"].pop(i, None)
                frisch.add(i)
                continue
            f["box"], f["lage"] = (m["x0"], m["y0"], m["x1"], m["y1"]), lage
        ax_senden()
    if frisch:
        time.sleep(0.15)   # Fläche muss weg sein, bevor das Video neu fotografiert wird
    return frisch


def ax_folger():
    """Zieht liegende Flächen beim Scrollen schnell nach: etwa 8-mal je Sekunde, aber nur solange jemand das Gerät bedient (john 09.11.)."""
    import ax_lage
    while True:
        time.sleep(0.1)
        try:
            st = AX.get("anker")
            if st and st[0] and st[0] == st[1]:
                time.sleep(0.2)   # 10.11.: alle Karten hängen an ihrem Bild und laufen selbst mit – die ganze Seite nur noch 3- statt 8-mal je Sekunde abfragen
            wo = AX["wo"]
            if not wo or not (SEITE.get("treffer") or any(i != "decke" for i in list(AX["fl"]))) or ax_lage.leerlauf() > 2.5:   # 10.11.: 2,5 statt 1 s, der Nachlauf beim Scrollen dauert länger
                time.sleep(0.4)
                continue
            with AX_SPERRE:
                if AX["wo"] != wo:   # inzwischen andere Seite/App (ax_weg): nichts neu anlegen
                    continue
                m, i = ax_medien(wo[0], 0.3)
                if m is not None:
                    ax_nachziehen(m, folgen=True, ganz=not i.get("teil"))
        except Exception:
            time.sleep(1)


def ax_decken(ids, medien):
    jetzt = {m["id"]: m for m in medien}
    with AX_SPERRE:
        for i in ids:
            m = jetzt.get(i)
            if m and i not in AX["fl"]:
                AX["fl"][i] = {"box": (m["x0"], m["y0"], m["x1"], m["y1"]), "t": time.time(), "video": bool(m.get("video"))}
        return ax_senden()


def decke_sichtbar(b):
    """True, wenn der Ausschnitt (fast) nur das BlockEm-Dunkel 14/19/32 zeigt: das Bildschirmfoto sieht die eigene Fläche, nicht das Bild darunter."""
    import numpy as np
    return float((np.abs(b[::4, ::4].reshape(-1, 3).astype(np.int16) - np.array([14, 19, 32])).max(axis=1) < 14).mean()) > 0.7



def bild_daten(b64):
    """JPEG (base64, Videobild aus der Seite) → RGB-Array über sips, oder None."""
    import base64, tempfile
    from bild_ki import bmp_lesen
    d = Path(tempfile.gettempdir()) / "fokus-cockpit-bild"
    d.mkdir(exist_ok=True)
    jpg, bmp = d / "video.jpg", d / "video.bmp"
    try:
        jpg.write_bytes(base64.b64decode(b64))
        subprocess.run(["sips", "-s", "format", "bmp", str(jpg), "--out", str(bmp)], capture_output=True, timeout=3)
        return bmp_lesen(bmp)
    except Exception:
        return None
    finally:
        for f in (jpg, bmp):
            try:
                f.unlink()
            except OSError:
                pass


def seite_js(app, art, ids=()):
    """seite-medien.js im aktiven Tab ausführen. dict oder None (kein unterstützter Browser, JavaScript aus, Fehler)."""
    if sys.platform != "darwin":
        return None
    name = next((v for k, v in SEITE_APPS.items() if k in (app or "").lower()), None)
    if not name:
        return None
    if SEITE["js"] is None:
        try:
            SEITE["js"] = (HERE / "seite-medien.js").read_text("utf-8").strip()
        except OSError:
            SEITE["js"] = ""
    if not SEITE["js"]:
        return None
    code = SEITE["js"] + "(" + json.dumps(art) + "," + json.dumps([str(i) for i in ids]) + ")"
    t = code.replace("\\", "\\\\").replace('"', '\\"')
    befehl = (f'tell application "Safari" to do JavaScript "{t}" in current tab of front window' if name == "Safari"
              else f'tell application "{name}" to execute active tab of front window javascript "{t}"')
    try:
        r = subprocess.run(["osascript", "-e", befehl], capture_output=True, text=True, timeout=3)
        d = json.loads(r.stdout.strip() or "null")
    except Exception as e:
        SEITE["fehler"] = f"{name}: {e}"[:200]
        return None
    if not isinstance(d, dict):
        err = (r.stderr or "").strip()
        if err and SEITE.get("gemeldet") != err[:80]:   # einmal ins Protokoll, damit man sieht, warum der alte Weg greift
            SEITE["gemeldet"] = err[:80]
            sperre_protokoll(f"Bild-KI: Webseite antwortet nicht ({name}): {err[:160]}")
        SEITE["fehler"] = (f"{name}: JavaScript von Apple Events ist aus. Einschalten: " +
                           ("Safari › Entwickler › JavaScript von Apple Events erlauben" if name == "Safari"
                            else "Darstellung › Entwickler › JavaScript aus Apple Events zulassen")) if "JavaScript" in err or "Apple Events" in err \
            else f"{name}: {err or 'keine Antwort'}"[:200]
        return None
    SEITE["fehler"] = ""
    return d


def bild_stelle(x0, y0, x1, y1):
    """Bildschirmfoto nur dieser Stelle (Punkte, oben links = 0 0, auch Zweitbildschirm), höchstens 448 px, als RGB-Array."""
    import tempfile
    from bild_ki import bmp_lesen
    d = Path(tempfile.gettempdir()) / "fokus-cockpit-bild"
    d.mkdir(exist_ok=True)
    png, bmp = d / "stelle.png", d / "stelle.bmp"
    try:
        r = subprocess.run(["screencapture", "-x", "-R", f"{int(x0)},{int(y0)},{max(1, int(x1 - x0))},{max(1, int(y1 - y0))}", "-t", "png", str(png)],
                           capture_output=True, timeout=3)
        if r.returncode != 0 or not png.exists() or b"could not create" in r.stderr:
            BILD_KI["fehler"] = "Bildschirmfoto ging nicht. Erlaubnis: Systemeinstellungen › Datenschutz & Sicherheit › Bildschirmaufnahme."
            return None
        subprocess.run(["sips", "-s", "format", "bmp", "-Z", "448", str(png), "--out", str(bmp)], capture_output=True, timeout=3)
        return bmp_lesen(bmp)
    except Exception:
        return None
    finally:
        for f in (png, bmp):
            try:
                f.unlink()
            except OSError:
                pass


def seite_pruefen(app, adresse, x, ziel):
    """True, wenn die Webseite die Bilder geliefert hat und sie einzeln geprüft wurden; False = alter Weg über den ganzen Bildschirm
    (Seite antwortet nicht, Lage unsicher, keine Bilder gefunden, oder alle 10 s zur Sicherheit für Hintergrundbilder/iframes)."""
    from bild_ki import werten_teil
    BILD_KI["web"] = BILD_KI["ax_medien"] = None
    BILD_KI["rest"] = 0
    BILD_KI["video_da"] = True
    AX["ruhig"] = None
    d = None if time.time() < SEITE.get("js_pause", 0) else seite_js(app, "liste")
    if d is None and "JavaScript" in SEITE.get("fehler", ""):
        SEITE["js_pause"] = time.time() + 60   # Haken in Chrome fehlt: nicht jede Runde neu fragen (kostet je Frage einen Programmstart)
    ax = False
    if d is None:   # kein JavaScript aus Apple Events: Lage über die Bedienungshilfen
        d = ax_liste(app)
        ax = d is not None
    if not ax and AX["fl"]:
        ax_weg()
    host = re.sub(r"^https?://(www\.)?", "", adresse or "").split("/")[0][:60]
    if d and d.get("ok") and SEITE["host"] != host:   # einmal je Seite ins Protokoll: findet BlockEm dort Bilder? (Diagnose Instagram 09.11.)
        SEITE["host"] = host
        n = len(d["medien"]) if d.get("medien") is not None else "Lage unsicher (Seitenleiste/Zoom?)"
        sperre_protokoll(f"Bild-KI: {n} Bilder/Videos gefunden")   # 19.11.: ohne Seite
    if not d or not d.get("ok") or d.get("medien") is None:
        return False
    t = time.time()
    BILD_KI["seite_zeit"] = t
    if d.get("nsfw") and not reddit_hilfe(adresse):   # Reddit markiert sichtbare Beiträge selbst als NSFW: Seite weg wie bei der Sperrliste
        ok, fehler = bild_weg_mac(app)
        if t - BILD_KI.get("nsfw_gemeldet", 0) > 30:
            BILD_KI["nsfw_gemeldet"] = t
            sperre_protokoll(f"Erwachsenen-Inhalt in {app} -> {'ok' if ok else fehler}")
            erwachsen_versuch(pc_name(), "Reddit NSFW")
            ki_anzeige("Inhalt gesperrt", "Diese Seite ist gesperrt.")
        return True
    if SEITE["url"] != adresse:
        SEITE.update(url=adresse, gesehen=set(), vfrei={}, treffer=set())
        ax_weg()
    frisch = set()
    if ax:
        AX["wo"] = (app, adresse)
        frisch = ax_nachziehen(d["medien"], ganz=not d.get("teil"))
        BILD_KI["web"], BILD_KI["ax_medien"] = d.get("web"), d["medien"]
    frei = []   # john 09.11.: kurze Stelle im Video soll nicht das ganze Video schwarz lassen
    for v in d.get("dunkle") or []:
        if v.get("bild"):
            b = bild_daten(v["bild"])
            if b is None:
                continue
            r = werten_teil(b)
            BILD_KI["pruefungen"] += 1
            k = r.get("klasse") or ""
            if (x["bildRegeln"].get(k, "aus") if k in BILD_KLASSEN else "aus") in ("weg", "zweiter"):
                SEITE["vfrei"][v["id"]] = 0
            else:
                SEITE["vfrei"][v["id"]] = SEITE["vfrei"].get(v["id"], 0) + 1
                if SEITE["vfrei"][v["id"]] >= 2 and v.get("alt", 0) > 2500:   # zweimal harmlos und mind. 2,5 s dunkel
                    frei.append(v["id"])
        elif v.get("alt", 0) > 5000:   # Bild nicht lesbar (fremde Quelle): nach 5 s zeigen, nächste Runde prüft neu
            frei.append(v["id"])
    if frei and not ax:
        seite_js(app, "frei", frei)
        for i in frei:
            SEITE["vfrei"].pop(i, None)
    schluessel = lambda m: m["id"] + "|" + (m.get("q") or "")   # gleiche Stelle, neue Quelle (nachgeladen, Karussell) = neu prüfen
    groesse = lambda m: -(m["x1"] - m["x0"]) * (m["y1"] - m["y0"])
    medien = d["medien"]
    if ax and BILD_KI.get("rahmen"):   # Prüfer 10.11.: Foto erst NACH der Liste – die Lage davor (medien) und danach (d2 unten) klammert es ein
        f_ = bild_aufnehmen(BILD_KI["rahmen"], gross=True)
        BILD_KI["foto"] = f_ if f_ is not None else BILD_KI.get("foto")
    foto, fr = BILD_KI.get("foto"), BILD_KI.get("rahmen")

    def stelle(m):
        """Ausschnitt dieses Bildes: am Mac (AX) aus dem einen großen Fensterfoto geschnitten, sonst eigenes Foto der Stelle."""
        if ax and foto is not None and fr and m["id"] not in frisch \
                and m["x0"] >= fr[0] - 2 and m["y0"] >= fr[1] - 2 and m["x1"] <= fr[2] + 2 and m["y1"] <= fr[3] + 2:   # ragt es heraus (zweiter Bildschirm): eigenes Foto
            sx, sy = foto.shape[1] / (fr[2] - fr[0]), foto.shape[0] / (fr[3] - fr[1])
            a = foto[max(0, int((m["y0"] - fr[1]) * sy)):max(0, int((m["y1"] - fr[1]) * sy)), max(0, int((m["x0"] - fr[0]) * sx)):max(0, int((m["x1"] - fr[0]) * sx))]
            return a if a.shape[0] >= 40 and a.shape[1] >= 40 else None
        return bild_stelle(m["x0"], m["y0"], m["x1"], m["y1"])
    arbeit = []   # (Bild der Seite, Ausschnitt oder None = erst beim Prüfen holen, schon harmlos laut Vorprüfung)
    if ax:   # 09.11.: alles Sichtbare auf einmal, das Verdächtigste zuerst; Videos jede Runde, Bilder einmal
        from bild_ki import vorpruefen, CLIP_MIN
        kand = []
        for m in sorted(medien, key=groesse)[:60]:
            liegt = m["id"] in AX["fl"]
            if not m.get("video") and (liegt or schluessel(m) in SEITE["gesehen"]):
                continue
            b = stelle(m)
            if b is None:
                continue
            if liegt:   # abgedecktes Video: zeigt das Foto unsere Fläche, lässt sich nicht drunter schauen (dann gibt ax_nachziehen es nach 5 s kurz frei)
                AX["durchsicht"] = not decke_sichtbar(b)
                if not AX["durchsicht"]:
                    continue
            kand.append((m, b))
        vor = vorpruefen([b for _, b in kand])
        for (m, b), (verdacht, flach) in sorted(zip(kand, vor), key=lambda z: -z[1][0]):
            arbeit.append((m, b, None if verdacht >= CLIP_MIN and not flach else {"klasse": "", "flach": flach}))   # einfarbig (lädt noch) gibt nie Alarm
    else:
        arbeit = [(m, None, None) for m in sorted([m for m in medien if m.get("video")], key=groesse)[:3] +
                  sorted([m for m in medien if not m.get("video") and schluessel(m) not in SEITE["gesehen"]], key=groesse)[:8]]   # Videos jedes Mal, Bilder einmal
    weg, beste, harmlos = [], None, []
    ende = time.time() + 1.5   # nach 1,5 s Runde beenden (Flächen ziehen nach, neues Foto), der Rest kommt sofort in der nächsten
    for m, b, r in arbeit:
        if r is None:
            if ax and time.time() > ende:
                BILD_KI["rest"] += 1
                continue
            if b is None:
                b = stelle(m)
            if b is None:
                continue
            r = werten_teil(b)
            BILD_KI["pruefungen"] += 1
        k = r.get("klasse") or ""
        aktion = x["bildRegeln"].get(k, "aus") if k in BILD_KLASSEN else "aus"
        if "nackt" in r and (beste is None or (aktion != "aus" and beste[1] == "aus")):
            beste = (r, aktion, k)
        liegt = ax and m["id"] in AX["fl"]
        if liegt:   # abgedecktes Video mit Durchsicht: bleibt zu, solange es anschlägt; zweimal harmlos = wieder frei
            if aktion in ("weg", "zweiter"):
                SEITE["vfrei"][m["id"]] = 0
                (AX["fl"].get(m["id"]) or {})["t"] = time.time()
            else:
                SEITE["vfrei"][m["id"]] = SEITE["vfrei"].get(m["id"], 0) + 1
                if SEITE["vfrei"][m["id"]] >= 2:
                    with AX_SPERRE:
                        AX["fl"].pop(m["id"], None)
                        ax_senden()
                    SEITE["vfrei"].pop(m["id"], None)
            continue
        if not m.get("video") and aktion in ("aus", "hinweis") and not r.get("flach"):
            harmlos.append(m)   # Treffer und noch Ladendes nie als geprüft merken
        if aktion == "weg" or (aktion == "zweiter" and t - BILD_KI["verdacht"] < 15):
            weg.append((m["id"], k, r))
            if ax and not m.get("video"):
                SEITE.setdefault("treffer", set()).add(schluessel(m))
            if ax:
                ax_decken([m["id"]], medien)   # sofort zu, nicht erst am Ende der Runde
        elif aktion == "zweiter":
            BILD_KI["verdacht"] = t
        elif aktion == "hinweis":
            ki_anzeige("Hinweis", "Nur Hinweis, nichts abgedeckt.")
    BILD_KI["runde_ms"] = round((time.time() - t) * 1000)
    BILD_KI["video_da"] = not ax or any(m.get("video") for m in medien)
    if ax and not BILD_KI["rest"] and not BILD_KI["video_da"]:   # Stromsparen 09.11.: alle sichtbaren Bilder entschieden – bis sich die Liste ändert, kein Foto mehr
        offen = any(m["id"] not in AX["fl"] and schluessel(m) not in SEITE["gesehen"] and m not in harmlos for m in medien)
        AX["ruhig"] = (app, adresse, frozenset(m["id"] for m in medien), time.time() + (1.5 if offen else 10))
    else:
        AX["ruhig"] = None
    if harmlos:   # nur merken, was noch an derselben Stelle steht (sonst wurde beim Scrollen etwas anderes fotografiert)
        d2 = (ax_liste(app) if ax else seite_js(app, "liste")) or {}
        jetzt = {m["id"]: m for m in d2.get("medien") or []}
        for m in harmlos:
            n = jetzt.get(m["id"])
            if n and abs(n["y0"] - m["y0"]) < 4 and abs(n["x0"] - m["x0"]) < 4:
                SEITE["gesehen"].add(schluessel(m))
    BILD_KI["fehler"] = ""
    BILD_KI["aussetzer"] = 0
    if beste:
        BILD_KI["letzt"] = dict(beste[0], wo=ziel, zeit=datetime.now().strftime("%H:%M:%S"))
    if BILD_KI["rest"] and not weg:
        return True   # noch Bilder offen: gleich weiter, kein Ganzer-Bildschirm-Blick dazwischen
    if weg:
        BILD_KI["verdacht"] = 0.0
        ok = ax_decken([i for i, _, _ in weg], medien) if ax else bool(seite_js(app, "decken", [i for i, _, _ in weg]))
        if not ok:   # Abdunkeln ging nicht: wie früher wegschalten
            ok, _ = bild_weg_mac(app)
        was = BILD_NAMEN.get(weg[0][1], "Bild")
        sperre_protokoll(f"Bild-KI in {app}: {len(weg)} Bild(er) -> {'abgedeckt' if ok else 'Fehler'}")   # 19.11.: ohne Seite und Art
        if t - BILD_KI.get("gezaehlt", 0) > 30:
            BILD_KI["gezaehlt"] = t
            erwachsen_versuch(pc_name(), "Bild")
        ki_anzeige("Inhalt ausgeblendet", "Abgedeckt.")
        return True
    if not medien or t - BILD_KI.get("voll", 0) > 10:   # keine Bilder gefunden (Hintergrundbild, iframe, canvas) oder alle 10 s: auch ganzer Bildschirm
        BILD_KI["voll"] = t
        return False
    return True


def reddit_hilfe(adresse):
    m = re.search(r"reddit\.com/(?:r|u|user)/([a-z0-9_-]+)", (adresse or "").lower())
    return bool(m and m.group(1) in REDDIT_HILFE)


def decke_weg():
    d = BILD_KI.pop("decke", None)
    if not d:
        return
    if d["proz"] is not None:
        try:
            d["proz"].kill()
        except OSError:
            pass
    elif sys.platform == "win32":
        win_befehl("aufdecken")
    elif AX["fl"].pop("decke", None) is not None:
        if AX["fl"]:
            ax_senden()
        else:
            ax_weg()


def bild_rueck_speichern(geraet, roh):
    """3.25: „Fehlalarm“ und „übersehen“ vom Handy: nur Merkmalszahlen, keine Fotos. An bild-rueck/<geraet>.jsonl anhängen."""
    if len(roh) > 10_000_000:   # Handy puffert bis 8 MB
        raise ApiFehler("Zu groß", 413)
    zeilen = [z for z in roh.decode("utf-8").splitlines() if z.strip()]
    for z in zeilen:
        if not isinstance(json.loads(z), dict):
            raise ApiFehler("Keine gültige Zeile", 400)
    name = re.sub(r"[^\w -]", "_", geraet or "")[:40].strip(" _") or "handy"
    d = DATEN / "bild-rueck"
    d.mkdir(exist_ok=True)
    with BILD_RUECK_SPERRE, (d / f"{name}.jsonl").open("a", encoding="utf-8") as f:
        f.write("".join(z + "\n" for z in zeilen))
    return {"ok": True, "zeilen": len(zeilen)}


def bild_ausfall():
    """3.22 Wächter: Die Bild-KI soll schauen, kann aber nicht (Erlaubnis, numpy, Fehler). Nach 3 Aussetzern sichtbar melden, nicht still bleiben."""
    BILD_KI["aussetzer"] += 1
    if BILD_KI["aussetzer"] >= 3 and time.time() - BILD_KI["gemeldet"] > 600:
        BILD_KI["gemeldet"] = time.time()
        grund = BILD_KI["fehler"] or "Grund unbekannt, siehe Systemcheck."
        sperre_protokoll("Wächter: Bild-KI ausgefallen: " + grund)
        ki_anzeige("Bild-KI schaut gerade NICHT", grund[:140])


def numpy_einrichten():
    """Bild-KI braucht numpy. Fehlt es, einmal pro Start still nachladen (pip, nur für diesen Nutzer). Ergebnis im Protokoll."""
    if BILD_KI.get("pip"):
        return
    BILD_KI["pip"] = "läuft"
    BILD_KI["fehler"] = "numpy fehlt, wird gerade geladen (einmalig, etwa 1 Minute)"

    def laden():
        befehl = [sys.executable, "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check", "numpy"]
        try:
            r = subprocess.run(befehl, capture_output=True, text=True, timeout=600,
                               creationflags=0x08000000 if sys.platform == "win32" else 0)
            if r.returncode != 0 and "externally-managed" in (r.stderr or "") + (r.stdout or ""):
                # Homebrew-Python: nur in den Nutzer-Ordner (~/Library/Python), Homebrew selbst bleibt unberührt
                r = subprocess.run(befehl + ["--break-system-packages"], capture_output=True, text=True, timeout=600)
            ok = r.returncode == 0
            if ok:
                import importlib, site
                importlib.invalidate_caches()
                u = site.getusersitepackages()
                if u not in sys.path:
                    sys.path.append(u)
            BILD_KI["pip"] = "ok" if ok else "fehler"
            BILD_KI["fehler"] = "" if ok else ("numpy ließ sich nicht laden: " + ((r.stderr or r.stdout or "").strip().splitlines() or ["?"])[-1])[:200]
            sperre_protokoll("Bild-KI: numpy " + ("geladen" if ok else "ging nicht: " + BILD_KI["fehler"]))
        except Exception as e:
            BILD_KI.update(pip="fehler", fehler=f"numpy ließ sich nicht laden: {e}"[:200])
    threading.Thread(target=laden, daemon=True).start()


def umgebung_merken():
    """Was auf diesem Computer läuft (Python, numpy, Helfer), als kleine Datei zum Nachschauen (3.19)."""
    try:
        import importlib.util
        (DATEN / "umgebung.json").write_text(json.dumps({
            "zeit": datetime.now().isoformat(timespec="seconds"), "version": APP_VERSION, "python": sys.executable,
            "pythonVersion": sys.version.split()[0], "system": sys.platform, "numpy": importlib.util.find_spec("numpy") is not None,
            "fensterBlick": VORNE.get("quelle"), "helferFehler": WIN.get("fehler"), "bildKiFehler": BILD_KI.get("fehler"),
            "zaehlerFehler": ZAEHLER.get("fehler")}, ensure_ascii=False, indent=1), "utf-8")
    except Exception:
        pass


def bild_weg_mac(app):
    ziel = f"http://localhost:{CFG['port']}/#fokus"
    browser = next((k for k in BROWSER_SKRIPT if k in (app or "").lower()), None)
    if browser:
        r = browser_osa(app, browser, ziel)
    else:
        r = subprocess.run(["osascript", "-e", f'tell application "System Events" to set visible of process "{(app or "").replace(chr(34), "")}" to false'],
                           timeout=10, check=False, capture_output=True, text=True)
    if not browser or r.returncode != 0:
        subprocess.run(["open", ziel], timeout=10, check=False)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def bild_pause():
    """Wartezeit bis zur nächsten Bild-KI-Runde. Stromsparen 09.11.: bedient niemand den Mac und läuft auf der Seite kein Video,
    ändert sich nichts – dann warten, bis wieder gescrollt/geklickt wird (höchstens 4 s)."""
    if BILD_KI.get("rest"):
        return time.sleep(0.05)
    kurz = 0.6 if AX["fl"] else 1.0 if BILD_KI.get("decke") or time.time() - BILD_KI.get("seite_zeit", 0) < 6 else 3.5
    if sys.platform == "darwin" and kurz < 3.5 and not BILD_KI.get("video_da", True) and not BILD_KI.get("decke"):
        try:
            import ax_lage
            if ax_lage.leerlauf() > 2:
                ende = time.time() + 4
                while time.time() < ende and ax_lage.leerlauf() > 0.5:
                    time.sleep(0.2)
                return
        except Exception:
            pass
    time.sleep(kurz)


def bild_waechter():
    time.sleep(20)
    BILD_KI["gen"] = gen = BILD_KI.get("gen", 0) + 1   # 1.0.46: alte Threads beenden sich selbst
    while BILD_KI.get("gen") == gen:
        try:
            with leistung("Bild-KI"):
                bild_pruefen()
            BILD_KI["puls"] = time.time()
        except Exception as e:
            BILD_KI["fehler"] = f"Bild-KI: {e}"[:200]
            bild_ausfall()
            time.sleep(30)
        bild_pause()   # Webseite liefert Bilder: schneller schauen   # liegt eine Abdeckung: öfter schauen, damit sie beim Scrollen schnell nachzieht


def sperr_waechter():
    """Läuft im Hintergrund: während einer Fokus-Session alle 3 Sekunden das aktuelle Fenster prüfen."""
    time.sleep(15)
    win_id, gewaehlt = None, 0
    SPERRE_LOG["gen"] = gen = SPERRE_LOG.get("gen", 0) + 1   # 1.0.46: nur der neueste Wächter läuft (alte Threads beenden sich selbst)
    while SPERRE_LOG.get("gen") == gen:
        try:
            with leistung("Sperre (Seiten, Apps, DNS, VPN)"):
                sperre_pruefen(None)   # 3.19: Fenster direkt über den gemeinsamen Fenster-Blick, ActivityWatch nicht nötig
            SPERRE_LOG["puls"] = time.time()
            SPERRE_LOG["waechterFehler"] = ""
        except Exception as e:
            print("Sperre:", e)
            SPERRE_LOG["waechterFehler"] = str(e)[:200]
            win_id = None
            time.sleep(30)
        time.sleep(3)


def sperr_aufpasser():
    """Startet den Sperr-Wächter neu, wenn er länger als 3 Minuten keinen Puls mehr gegeben hat."""
    time.sleep(120)
    zuletzt = time.time()
    while True:
        try:
            jetzt = time.time()
            if jetzt - zuletzt > 150:   # 1.0.46: Mac war im Ruhezustand → kein Fehlalarm, Puls zurücksetzen
                SPERRE_LOG["puls"] = jetzt
                if BILD_KI.get("puls"): BILD_KI["puls"] = jetzt
            zuletzt = jetzt
            if time.time() - SPERRE_LOG.get("puls", time.time()) > 180:
                sperre_protokoll("Sperr-Wächter hing: neu gestartet")
                SPERRE_LOG["puls"] = time.time()
                SPERRE_LOG["neustarts"] = SPERRE_LOG.get("neustarts", 0) + 1
                threading.Thread(target=sperr_waechter, daemon=True).start()
            if schutz_handy(None, "Mac")["bilder"] and time.time() - BILD_KI.get("puls", 0) > 180 and BILD_KI.get("puls"):   # 3.22: Bild-KI hängt
                sperre_protokoll("Wächter: Bild-KI hing: neu gestartet")
                BILD_KI["puls"] = time.time()
                threading.Thread(target=bild_waechter, daemon=True).start()
                ki_anzeige("Bild-KI hing", "Sie wurde gerade neu gestartet.")
        except Exception as e:
            print("Aufpasser:", e)
        time.sleep(60)


# ---------- App Locker: feste Sperrzeiten und Tageslimits pro App und Gerät, Einstellungen nur mit PIN ----------
# automatik.json "locker": {"pin": {"salz", "hash"}, "an": true, "regeln": [{"id", "art": "zeit"|"limit", "apps": [...],
#   "tage": [0..6 = Mo..So], "von": "09:00", "bis": "17:00", "minuten": 30, "geraete": [] = alle}]}
# Die PIN schützt nur den Einstellungsbereich; in einer gesperrten App lässt sich nichts freischalten. Im Fokus bleibt er zu.
LOCKER_TOKEN = {}                     # token -> gültig bis (time.time())
LOCKER_OHNE_PIN = True                # john 05.11.: Locker ohne PIN, immer offen (Lockern bleibt beim Timer); alte PIN wird ignoriert
LOCKER_OFFEN_SEK = 10 * 60
LOCKER_FEHL = {"n": 0, "bis": 0}      # nach 5 falschen PINs 5 Minuten Pause
# Timer vor dem Lockern (Impulskontrolle): erst nach der Wartezeit darf gelockert werden, dann LOCKER_FREI_SEK lang.
# Strenger machen (neue Regel, einschalten, längere Wartezeit) geht sofort.
# john 19.11.: 12 Std. statt 5 Min, fest für alle (nur die Entwicklerversion kann sie ändern). Der Timer steht jetzt
# in automatik.json (locker.frei = {ab, bis, zeit}) und übersteht einen Neustart; vorher lag er nur im Speicher.
LOCKER_WARTE_STD = 12 * 60
LOCKER_WARTE_MAX = 12 * 60
LOCKER_FREI_SEK = 2 * 3600   # nach 12 Std. Warten 2 Std. Zeit zum Lockern (10 Min passten zu 5 Min Warten, nicht zu einer Nacht)
WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def zeit_text(ts, jetzt=None):
    """„heute, 18:00 Uhr“, „morgen, 09:40 Uhr“, „Freitag, 18:00 Uhr“ (bis 6 Tage), sonst „28.11., 18:00 Uhr“.
    Gleiche Regel in SperrDienst.zeitText (Java) und teile/bindung.js (bdZeit), damit überall derselbe Satz steht."""
    d, j = datetime.fromtimestamp(float(ts)), datetime.fromtimestamp(jetzt if jetzt is not None else time.time())
    tage = (d.date() - j.date()).days
    tag = "heute" if tage == 0 else "morgen" if tage == 1 else WOCHENTAGE[d.weekday()] if 1 < tage <= 6 else d.strftime("%d.%m.")
    return f"{tag}, {d.strftime('%H:%M')} Uhr"


def apps_text(apps):
    apps = list(apps or [])
    if len(apps) <= 1:
        return "".join(apps)
    if len(apps) <= 3:
        return ", ".join(apps[:-1]) + " und " + apps[-1]
    return f"{apps[0]}, {apps[1]} und {len(apps) - 2} weitere"


def bindung_satz(r, jetzt=None):
    """Derselbe Satz in der Übersicht und beim Sperren (john 19.11.): „Instagram ist bis Freitag, 18:00 Uhr gesperrt.“"""
    n = len(r.get("apps") or [])
    return f"{apps_text(r.get('apps'))} {'ist' if n == 1 else 'sind'} bis {zeit_text(r['ende'], jetzt)} gesperrt."
LOCKER_ENTFERNEN_SEK = 24 * 3600   # 1.0 (john 03.11.): Regel löschen oder Locker ausschalten wirkt erst nach 24 Std. (Entwicklerversion sofort)
UHRZEIT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


FOKUS_EXTERN = {"bis": 0}   # Handy offline (3.14): Fokus-Ende in Sek., das der Mac zuletzt gemeldet hat


def fokus_laeuft(a=None):
    if FOKUS_EXTERN["bis"] > time.time():
        return True
    a = automatik_load() if a is None else a
    with FOKUS_LOCK:
        akt = fokus_load().get("aktiv")
    return bool(akt) and automatik_an("sperre", a) and datetime.now(timezone.utc) < datetime.fromisoformat(akt["planEnde"])


def geraet_name(n):
    return re.sub(r"[^\w\- ]", "", str(n or ""))[:40]   # wie handy_save den Dateinamen bildet


LOCKER_GERAET_WEG_SEK = 24 * 3600   # 21.11. (Designübergabe): ein Gerät aus einer bestehenden Regel herausnehmen wirkt erst nach 24 Std.


def locker_geraete_wirksam(r, jetzt=None):
    """Geräte, auf denen die Regel gerade gilt ([] = alle). Ein beantragtes Herausnehmen (r["geraetWeg"] = {Gerät: ab}) wirkt erst ab „ab“;
    bis dahin gilt die Regel dort weiter, auch für das gemeinsame Tageslimit. Nie breiter als vorher: bliebe nichts übrig, gilt die alte Liste."""
    g = list(r.get("geraete") or [])
    if not g:
        return []   # „alle Geräte“ bleibt bis zum Aufräumen alle (nie lockerer); erst dann wird die Liste ausgeschrieben
    jetzt = jetzt or time.time()
    weg = set()
    for k, ab in (r.get("geraetWeg") if isinstance(r.get("geraetWeg"), dict) else {}).items():
        try:
            if float(ab or 0) <= jetzt:
                weg.add(str(k).lower())
        except (TypeError, ValueError):
            pass
    rest = [x for x in g if x.lower() not in weg]
    return rest if rest or not g else g


def locker_geraet_weg_offen(r, jetzt=None):
    """{Gerät: ab} der noch laufenden Anträge dieser Regel."""
    jetzt = jetzt or time.time()
    out = {}
    for k, ab in (r.get("geraetWeg") if isinstance(r.get("geraetWeg"), dict) else {}).items():
        try:
            if float(ab or 0) > jetzt:
                out[str(k)] = float(ab)
        except (TypeError, ValueError):
            pass
    return out


def locker_geraete_aufraeumen(l):
    """Fällige Anträge ausführen: Gerät aus „geraete“ streichen (dann sehen es auch ältere App-Fassungen). True, wenn sich etwas geändert hat."""
    jetzt, geaendert = time.time(), False
    for r in l.get("regeln") or []:
        if not r.get("geraetWeg"):
            continue
        offen = locker_geraet_weg_offen(r, jetzt)
        if isinstance(r["geraetWeg"], dict) and len(offen) == len(r["geraetWeg"]):
            continue
        if isinstance(r["geraetWeg"], dict):
            faellig = {str(k).lower() for k in r["geraetWeg"] if str(k) not in offen}
            rest = [x for x in (r.get("geraete") or locker_alle_geraete(l)) if x.lower() not in faellig]
            if rest:
                r["geraete"] = rest
        if offen:
            r["geraetWeg"] = offen
        else:
            r.pop("geraetWeg", None)
        r["geaendert"] = jetzt
        geaendert = True
    return geaendert


def locker_alle_geraete(l):
    """Alle bekannten Geräte für eine Regel „alle Geräte“: Liste der Geräte, immer „Mac“ (am Handy fehlt er in geraete_namen) und jedes in einer Regel genannte."""
    out = {}
    for g in [*geraete_namen(), "Mac", *[x for r in l.get("regeln") or [] for x in r.get("geraete") or []]]:
        if g and str(g).lower() not in out:
            out[str(g).lower()] = str(g)
    return list(out.values())


def locker_geraet_antrag(l, r, geraete_weg, jetzt=None):
    """Herausnehmen beantragen: Regel r gilt auf diesen Geräten noch 24 Std. „geraete“ bleibt unverändert (leer = alle bleibt alle,
    ausgeschrieben wird erst beim fälligen Aufräumen). Fehler, wenn danach kein Gerät übrig bliebe (dafür gibt es „Regel entfernen“)."""
    jetzt = jetzt or time.time()
    alle = list(r.get("geraete") or []) or locker_alle_geraete(l)
    offen = locker_geraet_weg_offen(r, jetzt)
    bleibt = [g for g in alle if g.lower() not in {x.lower() for x in list(offen) + list(geraete_weg)}]
    if not bleibt:
        raise ValueError("Auf mindestens einem Gerät muss die Regel bleiben. Ganz beenden geht mit „Regel entfernen“ (24 Std. Wartezeit).")
    r["geraetWeg"] = {**offen, **{g: offen.get(g) or jetzt + LOCKER_GERAET_WEG_SEK for g in geraete_weg}}
    r["geaendert"] = jetzt
    return r


def locker_entfernen_offen(l):
    """{Regel-Id oder "_an": Zeitpunkt (time.time()), ab dem das Entfernen wirkt} – nur die, die noch laufen."""
    jetzt = time.time()
    out = {}
    for k, e in (l.get("entfernen") or {}).items():
        try:
            ab = float((e or {}).get("ab") or 0)
        except (TypeError, ValueError):
            ab = 0
        if ab > jetzt:
            out[str(k)] = ab
    return out


def locker_entfernen_anwenden(l):
    """Abgelaufene Entfern-Aufträge ausführen (Regel weg bzw. Locker aus). True, wenn sich etwas geändert hat."""
    jetzt, geaendert = time.time(), False
    for k, e in list((l.get("entfernen") or {}).items()):
        try:
            ab = float((e or {}).get("ab") or 0)
        except (TypeError, ValueError):
            ab = 0
        if not ab or ab > jetzt:
            if not ab:
                l["entfernen"].pop(k, None)
            continue
        if k == "_an":
            l["an"], l["einstZeit"] = False, jetzt
            sperre_protokoll("App Locker: nach 24 Std. ausgeschaltet")
        else:
            l["regeln"] = [x for x in l.get("regeln") or [] if x.get("id") != k]
            weg = l.setdefault("geloescht", {})
            weg[str(k)] = jetzt
            if len(weg) > 300:
                l["geloescht"] = dict(sorted(weg.items(), key=lambda x: x[1])[-300:])
            sperre_protokoll("App Locker: Regel nach 24 Std. entfernt")
        l["entfernen"].pop(k, None)
        geaendert = True
    return geaendert


# 24.11. (john): Lockern gilt nur für die eine beantragte Änderung. Sie liegt in l["geplant"][Regel-Id] und wird nach der
# Wartezeit von selbst wirksam (kein offenes Fenster für alle Regeln mehr). {"regel", "vorher", "ab", "zeit", "satz"}; ab 0 = erledigt/abgebrochen.
def locker_geplant_offen(l):
    jetzt, out = time.time(), {}
    for k, e in ((l or {}).get("geplant") or {}).items():
        try:
            if isinstance(e, dict) and float(e.get("ab") or 0) > jetzt:
                out[str(k)] = e
        except (TypeError, ValueError):
            pass
    return out


def locker_geplant_regel(e, c, neu_gewinnt=False, zeit=None):
    """Die geplante Lockerung auf den jetzigen Stand c anwenden: was seit dem Antrag strenger gestellt wurde, bleibt strenger.
    neu_gewinnt: zwei Anträge zusammenlegen, dann zählt bei einem Wert, den beide ändern, der neue (e).
    zeit: fester Zeitstempel „geaendert“ (Fälligkeit), damit zwei Geräte, die denselben Antrag anwenden, dasselbe Ergebnis haben
    und eine spätere strengere Änderung beim Abgleich nicht überschrieben wird."""
    zeit = time.time() if zeit is None else zeit
    a0, p, out = e.get("vorher") or {}, e.get("regel") or {}, dict(c)
    if p.get("art") and p.get("art") != a0.get("art"):   # andere Art (z. B. Limit → Sperrzeit, alte Oberfläche): ganz ersetzen
        if not (p.get("apps") or p.get("konsum")):
            return c
        kl = {str(x).lower() for x in p.get("apps") or []} | {str(x).lower() for x in a0.get("apps") or []}
        out = {**p, "apps": list(p.get("apps") or []) + [x for x in c.get("apps") or [] if str(x).lower() not in kl], "geaendert": zeit}   # in der Wartezeit dazugenommene Apps bleiben
        if c.get("geraetWeg"):
            out["geraetWeg"] = c["geraetWeg"]
        return out
    for k in ("minuten", "pause", "anzahl", "von", "bis"):
        if k in p and (c.get(k) == a0.get(k) or neu_gewinnt and p.get(k) != a0.get(k)):
            out[k] = p[k]
    if bool(c.get("konsum")) == bool(a0.get("konsum")) or neu_gewinnt and bool(p.get("konsum")) != bool(a0.get("konsum")):
        if p.get("konsum"):            # locker_regel_sauber lässt „konsum“ weg, wenn es aus ist
            out["konsum"] = True
        else:
            out.pop("konsum", None); out.pop("erlaubt", None)
    for k in ("apps", "tage", "erlaubt"):
        if k not in p:
            continue
        A, P, C = ({str(x).lower(): x for x in (d.get(k) or [])} for d in (a0, p, c))
        weg, dazu = set(A) - set(P), set(P) - set(A)
        out[k] = [v for x, v in C.items() if x not in weg] + [v for x, v in P.items() if x in dazu and x not in C]
    if [str(x).lower() for x in a0.get("geraete") or []] == [str(x).lower() for x in c.get("geraete") or []]:
        out["geraete"] = list(p.get("geraete") or [])
    out["tage"] = sorted(out.get("tage") or range(7))
    if not out.get("apps") and not out.get("konsum"):
        return c                       # nie eine leere Regel erzeugen
    out["geaendert"] = zeit
    return out


def locker_geplant_anwenden(l, schreiben=True):
    """Fällige geplante Lockerungen wirksam machen. Liste der Sätze, die jetzt gelten (leer = nichts geändert)."""
    jetzt, fertig = time.time(), []
    for k, e in list((l.get("geplant") or {}).items()):
        try:
            ab = float((e or {}).get("ab") or 0)
        except (TypeError, ValueError):
            ab = 0
        if not ab:   # erledigt/abgebrochen: bleibt als Grabstein (nie löschen, sonst holt ein alter Gerätestand den Antrag zurück)
            if schreiben and str(k).startswith("kat:") and (e or {}).get("erledigt"):
                kat_erledigt_hier(k, e)   # Umstufung, die ein anderes Gerät ausgeführt hat, auch hier eintragen
            continue
        if ab > jetzt:
            continue
        if str(k).startswith("kat:"):   # App umstufen (raus aus Ablenkung)
            if not schreiben:
                continue
            x = e.get("kat") or {}
            l["geplant"][k] = {"ab": 0, "zeit": ab, "erledigt": True, "kat": x, "satz": e.get("satz") or "", "warAb": ab, "melden": True}
            if x.get("name"):
                kat_erledigt_hier(k, l["geplant"][k])
                fertig.append(str(e.get("satz") or x["name"]))
            continue
        c = next((x for x in l.get("regeln") or [] if x.get("id") == k), None)
        if c:
            neu = locker_geplant_regel(e, c, zeit=ab)
            l["regeln"] = [x for x in l["regeln"] if x.get("id") != k] + [neu]
            fertig.append(str(e.get("satz") or "Deine Lockerung"))
            sperre_protokoll(f"App Locker: Lockerung gilt jetzt ({e.get('satz') or k})")
        # zeit = Fälligkeit (nicht jetzt): wendet ein zweites Gerät denselben Antrag später an, gilt sein Eintrag nicht als jünger (sonst doppelt angewendet/gemeldet)
        l["geplant"][k] = {"ab": 0, "zeit": ab, "erledigt": bool(c), "satz": e.get("satz") or "", "warAb": ab, "melden": bool(c)}
    return fertig


def kat_erledigt_hier(k, e):
    """Umstufung aus einem erledigten Antrag in die kategorien.json dieses Geräts schreiben (einmal je Antrag, Merker geplantErledigt)."""
    x = e.get("kat") or {}
    if not x.get("name"):
        return
    try:
        schon = float((load_json("kategorien.json").get("geplantErledigt") or {}).get(k) or 0)
    except Exception:
        schon = 0
    if schon < float(e.get("zeit") or 0):
        kategorie_schreiben(x["name"], x.get("kat"), merker=(k, float(e.get("zeit") or 0)))


def locker_geplant_speichern():
    with AUTOMATIK_LOCK:
        a = automatik_load()
        if locker_geplant_anwenden(a.setdefault("locker", {})):
            automatik_save(a)


def kat_geplant():
    """{app (klein): {kat, ab, abText}} – beantragte Umstufungen für die Werte-Seite."""
    return {k[4:]: {"kat": (e.get("kat") or {}).get("kat"), "ab": e["ab"], "abText": zeit_text(e["ab"]).replace(", ", " ab ", 1)}
            for k, e in locker_geplant_offen(automatik_load().get("locker") or {}).items() if k.startswith("kat:")}


def locker_strenger_teil(a, n):
    """Aus einer gemischten Änderung nur das Strengere (App/Tag/Gerät dazu, weniger Minuten, längere Sperrzeit …).
    Gibt a selbst zurück, wenn nichts strenger wird."""
    if n.get("art") != a.get("art"):
        return a
    kl = lambda xs: {str(x).lower() for x in xs or []}
    s = json.loads(json.dumps(a))
    s["apps"] = list(a.get("apps") or []) + [x for x in n.get("apps") or [] if x.lower() not in kl(a.get("apps"))]
    s["tage"] = sorted(set(a.get("tage") or range(7)) | set(n.get("tage") or range(7)))
    if a.get("geraete"):
        s["geraete"] = [] if not n.get("geraete") else list(a["geraete"]) + [g for g in n["geraete"] if g.lower() not in kl(a["geraete"])]
    if a.get("art") in ("limit", "sitzung") and n.get("minuten"):
        s["minuten"] = min(int(a.get("minuten") or n["minuten"]), int(n["minuten"]))
    if a.get("art") == "sitzung" and n.get("pause"):
        s["pause"] = max(int(a.get("pause") or 0), int(n["pause"]))
    if a.get("art") == "oeffnen" and n.get("anzahl"):
        s["anzahl"] = min(int(a.get("anzahl") or n["anzahl"]), int(n["anzahl"]))
    if a.get("art") == "zeit" and n.get("von") and locker_minuten_fenster(a["von"], a["bis"]) <= locker_minuten_fenster(n["von"], n["bis"]):
        s["von"], s["bis"] = n["von"], n["bis"]
    if n.get("konsum") and not a.get("konsum"):
        s["konsum"], s["erlaubt"] = True, list(n.get("erlaubt") or [])
    elif a.get("konsum") and n.get("konsum"):
        s["erlaubt"] = [x for x in a.get("erlaubt") or [] if x.lower() in kl(n.get("erlaubt"))]
    schl = ("apps", "tage", "geraete", "minuten", "pause", "anzahl", "von", "bis", "konsum", "erlaubt")
    return a if all(s.get(k) == a.get(k) for k in schl) else s


def locker_aenderung_satz(alt, neu):
    """Kurzer Satz, was die Lockerung ändert, z. B. „Streaming: 120 → 180 Min am Tag“."""
    name, teile = alt.get("gruppe") or apps_text(alt.get("apps")) or "Regel", []
    if neu.get("art") and neu.get("art") != alt.get("art"):   # alte Oberfläche kann die Art wechseln (z. B. Limit → Sperrzeit)
        return f"{name}: andere Regelart"
    if neu.get("minuten") != alt.get("minuten") and neu.get("minuten") is not None:
        teile.append(f"{alt.get('minuten')} → {neu['minuten']} Min" + (" am Tag" if neu.get("art") == "limit" else " am Stück"))
    if neu.get("pause") != alt.get("pause") and neu.get("pause") is not None:
        teile.append(f"Pause {alt.get('pause')} → {neu['pause']} Min")
    if neu.get("anzahl") != alt.get("anzahl") and neu.get("anzahl") is not None:
        teile.append(f"{alt.get('anzahl')}× → {neu['anzahl']}× öffnen")
    if (neu.get("von"), neu.get("bis")) != (alt.get("von"), alt.get("bis")) and neu.get("von"):
        teile.append(f"{alt.get('von')}–{alt.get('bis')} → {neu['von']}–{neu['bis']} Uhr")
    weg = [x for x in alt.get("apps") or [] if x.lower() not in {y.lower() for y in neu.get("apps") or []}]
    if weg:
        teile.append("ohne " + apps_text(weg[:3]))
    if not set(alt.get("tage") or range(7)) <= set(neu.get("tage") or range(7)):
        teile.append("weniger Tage")
    if neu.get("geraete") and (not alt.get("geraete") or not {x.lower() for x in alt["geraete"]} <= {x.lower() for x in neu["geraete"]}):
        teile.append("weniger Geräte")
    if alt.get("konsum") and not neu.get("konsum"):
        teile.append("ohne Konsum-Apps")
    elif {x.lower() for x in neu.get("erlaubt") or []} - {x.lower() for x in alt.get("erlaubt") or []}:
        teile.append("mehr erlaubt")
    return f"{name}: {', '.join(teile) or 'gelockert'}"


def locker_load(a=None):
    a = automatik_load() if a is None else a
    l = a.get("locker") or {}
    l = json.loads(json.dumps(l)) if l.get("entfernen") or l.get("geplant") else dict(l)
    if l.get("entfernen"):
        try:
            locker_entfernen_anwenden(l)   # nur für diese Anfrage; gespeichert wird in locker_aktion / locker_abgleich
        except Exception:
            pass
    if l.get("geplant"):
        try:
            locker_geplant_anwenden(l, schreiben=False)   # 24.11.: fällige Lockerung wirkt auf jedem Gerät pünktlich, auch ohne Verbindung
            kat = [(k, e) for k, e in l["geplant"].items() if str(k).startswith("kat:")]
            erl = (load_json("kategorien.json").get("geplantErledigt") or {}) if kat else {}
            if any(0 < float((e or {}).get("ab") or 0) <= time.time() or (e or {}).get("erledigt") and float(erl.get(k) or 0) < float(e.get("zeit") or 0) for k, e in kat):
                locker_geplant_speichern()   # App-Einordnung steht in kategorien.json: einmal gespeichert anwenden
        except Exception:
            pass
    if ENTWICKLER and (l.get("pauseBis") or 0) > time.time():   # Entwickler-Pause (john 04.11.): Regeln bis dahin aus, z. B. zum Testen in der Nacht
        return {"pin": l.get("pin"), "an": l.get("an", True) is not False, "regeln": [], "pauseBis": l["pauseBis"]}
    regeln = l.get("regeln") or []
    if any(x.get("konsum") for x in regeln):
        kl = konsum_liste(a)
        regeln = [{**x, "extra": list(x.get("apps") or []), "apps": locker_wirksame_apps(x, kl)} if x.get("konsum") else x for x in regeln]
    return {"pin": l.get("pin"), "an": l.get("an", True) is not False, "regeln": regeln}


def locker_regeln_fuer(geraet, a=None):
    l = locker_load(a)
    if not l["an"]:
        return []
    # Erziehungs-System (02.11.): Apps aus gelockerten Kategorien fallen aus den Regeln (dort fragt stattdessen Innehalten nach)
    regeln = [erz_regel_wirksam(r, a) for r in l["regeln"] if not locker_geraete_wirksam(r) or geraet in locker_geraete_wirksam(r)]
    return [r for r in regeln if r]


def pbkdf2_sha256(pw, salz, n):
    """PBKDF2-HMAC-SHA256 in reinem Python, gleiches Ergebnis wie hashlib.pbkdf2_hmac. Pyodide (Handy-App) hat kein OpenSSL und damit kein pbkdf2_hmac."""
    if len(pw) > 64:
        pw = hashlib.sha256(pw).digest()
    pw = pw.ljust(64, b"\0")
    innen, aussen = hashlib.sha256(bytes(x ^ 0x36 for x in pw)), hashlib.sha256(bytes(x ^ 0x5C for x in pw))
    def prf(m):
        i, o = innen.copy(), aussen.copy()
        i.update(m); o.update(i.digest())
        return o.digest()
    u = prf(salz + b"\0\0\0\1")
    out = int.from_bytes(u, "big")
    for _ in range(n - 1):
        u = prf(u)
        out ^= int.from_bytes(u, "big")
    return out.to_bytes(32, "big")


def pin_hash(pin, salz):
    pw, s = str(pin).encode("utf-8"), bytes.fromhex(salz)
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha256", pw, s, 100000).hex()
    return pbkdf2_sha256(pw, s, 100000).hex()


def locker_zeit_aktiv(r, jetzt=None):
    jetzt = jetzt or datetime.now()
    tage = r.get("tage") or list(range(7))
    von, bis, hm, wt = r.get("von") or "00:00", r.get("bis") or "23:59", jetzt.strftime("%H:%M"), jetzt.weekday()
    if von <= bis:
        return wt in tage and von <= hm < bis
    # über Mitternacht (z. B. 22:00–06:00): die Nacht gehört zum Starttag
    return (wt in tage and hm >= von) or ((wt - 1) % 7 in tage and hm < bis)


def locker_minuten(apps, geraete, ohne=None):
    """Heutige Minuten der Apps auf den Geräten der Regel (leer = alle), ohne = dieses Gerät nicht mitzählen."""
    sek = 0
    for g in day_data(date.today()).get("geraete", []):
        if (geraete and g["name"] not in geraete) or g["name"] == ohne:
            continue
        sek += sum(x["sek"] for x in g.get("apps", []) if sperre_treffer(x["name"], "", apps))
    return sek / 60


def locker_sperre(app, title, geraet, a=None):
    """(Treffer, Grund), wenn der App Locker dieses Fenster gerade sperrt, sonst None."""
    for r in locker_regeln_fuer(geraet, a):
        w = sperre_treffer(app, title, r.get("apps") or [])
        if r.get("art") == "oeffnen":
            g = locker_oeffnen(r, geraet, bool(w))
            if w and g:
                return w, g
            continue
        if not w:
            continue
        if r.get("art") == "bis" and float(r.get("ende") or 0) > time.time():
            return w, f"ist bis {zeit_text(r['ende'])} gesperrt"
        if r.get("art") == "zeit" and locker_zeit_aktiv(r):
            return w, f"ist gesperrt bis {r.get('bis')}"
        if r.get("art") == "limit" and date.today().weekday() in (r.get("tage") or range(7)) \
                and locker_minuten(r["apps"], locker_geraete_wirksam(r)) >= float(r.get("minuten") or 0):
            return w, f"hat das Tageslimit von {r.get('minuten')} Min erreicht"
        if r.get("art") == "sitzung" and date.today().weekday() in (r.get("tage") or range(7)) and locker_sitzung(r, geraet):
            frei = datetime.fromtimestamp(SITZUNG[(r.get("id"), geraet)]["bis"]).strftime("%H:%M")
            return w, f"hat {r.get('minuten')} Min am Stück erreicht: Pause bis {frei}"
    return None


SITZUNG = {}   # (Regel-ID, Gerät) -> {"start", "zuletzt", "bis"}; nur im Speicher
SITZUNG_PAUSE = {}   # 24.11. (john „Pause überall“): Regel-ID -> Ende der Pause (s); erreicht ein Gerät das Limit, pausieren alle Geräte der Regel


def sitzung_pause_bis(rid):
    b = SITZUNG_PAUSE.get(rid, 0)
    return b if b > time.time() else 0


def sitzung_pause_melden(meldung, geraet):
    """Handy meldet „Regel-ID|Ende in ms“, wenn es dort die Minuten am Stück erreicht hat. Nur bekannte Sitzungs-Regeln, höchstens deren Pause lang."""
    rid, _, bis = str(meldung).partition("|")
    r = next((x for x in locker_regeln_fuer(geraet_name(geraet or "Handy")) if x.get("id") == rid and x.get("art") == "sitzung"), None)   # nur Regeln, zu denen das Gerät gehört
    try:
        bis = float(bis) / 1000
    except ValueError:
        return
    if not r or bis <= time.time():
        return
    bis = min(bis, time.time() + float(r.get("pause") or 30) * 60 + 60)
    if bis > SITZUNG_PAUSE.get(rid, 0):
        SITZUNG_PAUSE[rid] = bis
        sperre_protokoll(f"App Locker: Sitzungs-Limit {', '.join(r.get('apps') or [])[:60]} ({geraet[:30] or 'Handy'}), Pause auf allen Geräten bis {datetime.fromtimestamp(bis).strftime('%H:%M')}")
OEFFNEN = {}   # (Regel-ID, Gerät) -> {"tag", "n", "vorn", "voll"}; nur im Speicher (das Handy zählt selbst)


def locker_oeffnen(r, geraet, vorn):
    """Öffnungs-Limit: zählt jedes neue Öffnen (vorher war etwas anderes vorn). Grund, sobald das Limit heute erreicht ist."""
    k, heute = (r.get("id"), geraet), date.today().isoformat()
    o = OEFFNEN.setdefault(k, {"tag": heute, "n": 0, "vorn": False, "voll": False})
    if o["tag"] != heute:
        o.update(tag=heute, n=0, voll=False)
    if not vorn or date.today().weekday() not in (r.get("tage") or range(7)):
        o["vorn"] = False
        return None
    max_n = int(r.get("anzahl") or 5)
    if not o["vorn"]:
        o["vorn"] = True
        if o["n"] >= max_n:
            o["voll"] = True
        else:
            o["n"] += 1
            o["voll"] = False
    return f"hast du heute schon {max_n}-mal geöffnet (Limit). Morgen wieder" if o["voll"] else None


def locker_sitzung(r, geraet):
    """Sitzungs-Limit: True, solange die Pause läuft. Eine Lücke von über 2 Min beendet die Sitzung (dann zählt es neu)."""
    t, k = time.time(), (r.get("id"), geraet)
    s = SITZUNG.setdefault(k, {"start": t, "zuletzt": t, "bis": 0})
    pb = sitzung_pause_bis(r.get("id"))   # 24.11. (john): Pause von einem anderen Gerät gilt hier auch
    if pb > s["bis"]:
        s.update(bis=pb, start=pb)
    if t < s["bis"]:
        return True
    if t - s["zuletzt"] > 120:
        s["start"] = t
    s["zuletzt"] = t
    if t - s["start"] >= float(r.get("minuten") or 0) * 60:
        s.update(bis=t + float(r.get("pause") or 30) * 60, start=t)
        SITZUNG_PAUSE[r.get("id")] = max(SITZUNG_PAUSE.get(r.get("id"), 0), s["bis"])
        sperre_protokoll(f"App Locker: Sitzungs-Limit {', '.join(r.get('apps') or [])[:60]} ({geraet})")
        return True
    return False


def locker_fuer_handy(geraet, a=None):
    """Regeln für ein Handy; bei Limits dazu, was die anderen Geräte heute schon verbraucht haben (das Handy zählt selbst mit)."""
    geraet = geraet_name(geraet)
    out = []
    for r in locker_regeln_fuer(geraet, a):
        if r.get("art") == "bis" and float(r.get("ende") or 0) <= time.time():
            continue   # abgelaufene Bindung: nichts mehr ans Handy
        x = {k: r.get(k) for k in ("id", "art", "apps", "tage", "von", "bis", "minuten", "pause", "anzahl", "ende")}
        x["apps"] = mit_seiten(r.get("apps"))   # 1.0.50: Websites der Apps sperrt das Handy im Browser mit
        x["tage"] = r.get("tage") or list(range(7))
        if r.get("art") == "sitzung" and sitzung_pause_bis(r.get("id")):   # 24.11.: Pause von einem anderen Gerät
            x["pauseBis"] = int(sitzung_pause_bis(r.get("id")) * 1000)
        if r.get("konsum"):   # 19.11.: Handy prüft Konsum-Ruhezeiten im Browser nur am Seitennamen, Messenger nur als Zusatz
            x["konsum"], x["extra"] = True, list(r.get("extra") or [])
        if r.get("art") == "limit":
            try:
                x["andereMin"] = round(locker_minuten(r["apps"], locker_geraete_wirksam(r), ohne=geraet), 1)
            except Exception:
                x["andereMin"] = 0
        out.append(x)
    return out


# ---------- Erziehungs-System (02.11.2026, john): erst hart sperren, wer durchhält wird gelockert, wer rückfällig wird wieder hart ----------
# Je Kategorie drei Stufen: hart (Locker-Regeln gelten voll), nachfrage (Regeln greifen nicht, stattdessen Innehalten
# „Brauchst du das gerade wirklich?“), frei (nur beobachten). Pornografie ist keine Kategorie: immer gesperrt, nie gelockert.
# Lockern gibt es nur als Angebot nach ERZ_TAGE sauberen Tagen. Rückfall → Hinweis mit Zahlen und Vorschlag „wieder sperren“;
# geht es in den 24 Std. danach weiter, automatisch wieder hart. Zustand: automatik.json `erziehung`. Messwerte je Tag: compute_day → res["erz"].
ERZ_STUFEN = ("hart", "nachfrage", "frei")
ERZ_STUFE_NAME = {"hart": "Hart", "nachfrage": "Nachfrage", "frei": "Frei"}
ERZ_TAGE = {"hart": 14, "nachfrage": 30}   # saubere Tage bis zum Angebot, eine Stufe lockerer zu gehen
ERZ_KATEGORIEN = {
    "social": ("Social Media", ["Instagram", "TikTok", "Facebook", "Snapchat", "twitter", "x.com", "Reddit", "Threads", "Pinterest", "Tumblr", "BeReal", "9gag"]),
    "video": ("Videos und Streaming", ["YouTube", "Netflix", "Twitch", "Prime Video", "Disney+", "Paramount", "Joyn", "Kick.com"]),
    "weitere": ("Weitere Apps", []),   # alles aus den Locker-Regeln, was nicht Social Media oder Video ist
}
ERZ_ATTACKE_MIN = 45          # so lange am Stück = Doom-Scroll-Attacke
ERZ_NACHT = (23, 5)           # spät nachts: 23 bis 5 Uhr
ERZ_NACHT_MIN = 20
ERZ_FRIST = 24 * 3600         # Zeit nach dem Hinweis, bevor automatisch wieder hart gesperrt wird
ERZ_TEXT = "Brauchst du das gerade wirklich? Ist es nur eine Attacke, oder nimmst du dir bewusst Zeit dafür?"
ERZ_LAUF = {"zuletzt": 0}


def erz_kat_wort(w):
    """Kategorie eines App- oder Seitennamens aus einer Locker-Regel."""
    w = str(w or "").lower()
    for k, (_, liste) in ERZ_KATEGORIEN.items():
        if any(x.lower() in w or (len(w) >= 3 and w in x.lower()) for x in liste):
            return k
    return "weitere"


def erz_kat_apps(a=None):
    """{Kategorie: [Wörter]}: feste Listen plus alle Apps aus den Locker-Regeln."""
    out = {k: list(v[1]) for k, v in ERZ_KATEGORIEN.items()}
    for r in locker_load(a)["regeln"]:
        for w in r.get("apps") or []:
            k = erz_kat_wort(w)
            if not any(w.lower() == x.lower() for x in out[k]):
                out[k].append(w)
    return out


def erz_kat_von(km, app, title):
    for k in ERZ_KATEGORIEN:
        if km.get(k) and sperre_treffer(app, title, km[k]):
            return k
    return None


def erz_load(a=None):
    a = automatik_load() if a is None else a
    z = json.loads(json.dumps(a.get("erziehung") or {}))
    for k in ERZ_KATEGORIEN:
        st = z.setdefault(k, {})
        if st.get("stufe") not in ERZ_STUFEN:
            st["stufe"] = "hart"
        st.setdefault("seit", date.today().isoformat())
    return z


def erz_an():
    """Erziehungs-System (Runde 1) ist seit 04.11. (john) komplett aus der App; nur zum Testen mit config.json "erziehungTest": true in der Entwicklerversion."""
    return bool(ENTWICKLER and CFG.get("erziehungTest"))


def erz_regel_wirksam(r, a=None):
    """Locker-Regel, wie sie gerade greift: nur noch mit den Apps, deren Kategorie hart ist. None, wenn keine übrig bleibt."""
    apps = r.get("apps") or []
    if not apps or not erz_an() or r.get("art") == "bis":   # Bindung: selbst gewählt, wird nie automatisch gelockert
        return r
    z = erz_load(a)
    hart = [w for w in apps if z[erz_kat_wort(w)]["stufe"] == "hart"]
    if len(hart) == len(apps):
        return r
    return {**r, "apps": hart} if hart else None


def innehalten_wirksam(a=None):
    """Innehalten, wie es gerade wirkt: eigene Einstellung plus alle Apps der Kategorien auf Stufe „Nachfrage“."""
    a = automatik_load() if a is None else a
    x = innehalten_daten(a)
    if x["an"] and x.get("kategorien", True):   # 1.0: die Listen Social Media + Streaming zählen mit dazu
        apps = list(x["apps"])
        for liste in kategorien_daten(a).values():
            apps += [w for w in liste if not any(w.lower() == y.lower() for y in apps)]
        x = {**x, "apps": apps}
    z = erz_load(a)
    nach = [k for k in ERZ_KATEGORIEN if z[k]["stufe"] == "nachfrage"] if erz_an() else []
    if not nach:
        return {**x, "erziehung": False}
    km = erz_kat_apps(a)
    apps = list(x["apps"]) if x["an"] else []
    for k in nach:
        apps += [w for w in km[k] if not any(w.lower() == y.lower() for y in apps)]
    return {**x, "an": True, "apps": apps, "text": ERZ_TEXT, "sekunden": max(10, int(x.get("sekunden") or 10)), "erziehung": True}


def erz_messen(stuecke):
    """stuecke: {(Gerät, Kategorie): [(s, t)]} (UTC) → je Kategorie Sekunden, Sekunden und Öffnungen je Stunde, längste Sitzung."""
    out = {}
    for (dev, k), liste in stuecke.items():
        m = out.setdefault(k, {"sek": 0, "std": [0] * 24, "oeStd": [0] * 24, "oeffnen": 0, "laengste": 0})
        liste.sort()
        sitz = []   # Sitzungen: Lücke bis 60 s zählt noch dazu
        for s, t in liste:
            if sitz and (s - sitz[-1][1]).total_seconds() <= 60:
                sitz[-1][1] = max(sitz[-1][1], t)
            else:
                sitz.append([s, t])
            m["sek"] += (t - s).total_seconds()
            x = s
            while x < t:   # auf die Stunden verteilen (Ortszeit)
                lok = x.astimezone()
                ende = min(t, (lok.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc))
                m["std"][lok.hour] += (ende - x).total_seconds()
                x = ende
        for s, t in sitz:
            m["oeffnen"] += 1
            m["oeStd"][s.astimezone().hour] += 1
        lang = []   # Attacke: kurze Wechsel (bis 2 Min) unterbrechen sie nicht
        for s, t in sitz:
            if lang and (s - lang[-1][1]).total_seconds() <= 120:
                lang[-1][1] = max(lang[-1][1], t)
            else:
                lang.append([s, t])
        m["laengste"] = max([m["laengste"]] + [(t - s).total_seconds() for s, t in lang])
    for m in out.values():
        m["sek"] = round(m["sek"])
        m["std"] = [round(v) for v in m["std"]]
        m["laengste"] = round(m["laengste"])
    return out


def erz_tag(d, k):
    try:
        return (day_data(d).get("erz") or {}).get(k) or {}
    except Exception:
        return {}


def erz_grenzen(st):
    b = st.get("basis") or {}
    bm, bo = float(b.get("min") or 0), float(b.get("oeffnen") or 0)
    return max(bm * 1.5, bm + 20, 30), max(bo * 1.5, bo + 5, 10), bm, bo


def erz_regeln_der(k, a):
    return [r for r in locker_load(a)["regeln"] if r.get("apps") and any(erz_kat_wort(w) == k for w in r["apps"])]


def erz_signale(k, st, d, a):
    """Was an Tag d nach Rückfall aussieht (Liste kurzer Sätze)."""
    m = erz_tag(d, k)
    if not m:
        return []
    gm, go, bm, bo = erz_grenzen(st)
    mi, oe, std = m.get("sek", 0) / 60, m.get("oeffnen", 0), m.get("std") or [0] * 24
    g = []
    if mi >= gm:
        g.append(f"{round(mi)} Min (vorher im Schnitt {round(bm)})")
    if oe >= go:
        g.append(f"{oe}× geöffnet (vorher {round(bo)}×)")
    nacht = sum(std[h] for h in range(24) if h >= ERZ_NACHT[0] or h < ERZ_NACHT[1]) / 60
    if nacht >= ERZ_NACHT_MIN:
        g.append(f"{round(nacht)} Min nach {ERZ_NACHT[0]} Uhr")
    for r in erz_regeln_der(k, a):
        art = r.get("art")
        if art == "limit" and mi >= float(r.get("minuten") or 1e9):
            g.append(f"über deinem alten Limit von {r.get('minuten')} Min")
        elif art == "oeffnen" and oe > int(r.get("anzahl") or 1e9):
            g.append(f"öfter als dein altes Limit von {r.get('anzahl')}×")
        elif art == "sitzung" and m.get("laengste", 0) / 60 >= float(r.get("minuten") or 1e9):
            g.append(f"länger am Stück als dein altes Limit von {r.get('minuten')} Min")
        elif art == "zeit":
            drin = sum(std[h] for h in range(24) if locker_zeit_aktiv(r, datetime.combine(d, datetime.min.time()).replace(hour=h, minute=30))) / 60
            if drin >= 10:
                g.append(f"{round(drin)} Min in deiner alten Sperrzeit {r.get('von')}–{r.get('bis')}")
    return list(dict.fromkeys(g))


def erz_porno():
    try:
        f = DATEN / ERWACHSEN_DATEI
        d = json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:
        d = {}
    zuletzt = 0
    try:
        zuletzt = datetime.fromisoformat(d["zuletzt"]).timestamp() if d.get("zuletzt") else 0
    except Exception:
        pass
    return {k: v.get("n", 0) for k, v in (d.get("tage") or {}).items() if v.get("n")}, zuletzt


def erz_start(st, porno):
    """Erster Tag, der für das Durchhalten zählt: nach Stufenwechsel, letztem Rückfall und letztem Porno-Versuch."""
    s = date.fromisoformat(st["seit"])
    for x in (st.get("letzterRueckfall"), max(porno, default=None)):
        if x:
            s = max(s, date.fromisoformat(x) + timedelta(days=1))
    return s


def erz_rueckfall(k, st, a, heute, porno):
    """Satz mit Zahlen, wenn die Kategorie rückfällig aussieht, sonst ''."""
    ab = date.fromisoformat(st["seit"])
    if st.get("letzterRueckfall"):
        ab = max(ab, date.fromisoformat(st["letzterRueckfall"]) + timedelta(days=1))
    name = ERZ_KATEGORIEN[k][0]
    tage = [d for d in (heute - timedelta(days=1), heute) if d >= ab]
    neu = [d for d in tage if porno.get(d.isoformat())]
    if neu:
        n = sum(porno[d.isoformat()] for d in neu)
        return f"Es gab {n} {'Versuch' if n == 1 else 'Versuche'}, Pornos zu öffnen. Das zählt sofort als Rückfall, auch bei {name}."
    for d in reversed(tage):
        lang = erz_tag(d, k).get("laengste", 0) / 60
        if lang >= ERZ_ATTACKE_MIN:
            return f"{'Heute' if d == heute else 'Gestern'} warst du {round(lang)} Min am Stück auf {name}. Das ist eine Doom-Scroll-Attacke."
    if len(tage) == 2:
        sig = [erz_signale(k, st, d, a) for d in tage]
        if sig[0] and sig[1]:
            return f"Du bist wieder rückfällig bei {name}. Gestern: {', '.join(sig[0])}. Heute: {', '.join(sig[1])}."
    return ""


def erz_fenster(k, st, von, bis, a, porno_zuletzt):
    """Ging es in den 24 Std. nach dem Hinweis weiter? Minuten und Öffnungen aus den Stundenwerten, Attacke, Porno-Versuch."""
    if porno_zuletzt > von:
        return True
    gm, go, _, _ = erz_grenzen(st)
    sek = oe = 0
    d0, d1 = datetime.fromtimestamp(von).date(), datetime.fromtimestamp(bis).date()
    d = d0
    while d <= d1:
        m = erz_tag(d, k)
        for h in range(24):
            t0 = datetime.combine(d, datetime.min.time()).replace(hour=h).timestamp()
            if t0 + 3600 > von and t0 < bis:
                sek += (m.get("std") or [0] * 24)[h]
                oe += (m.get("oeStd") or [0] * 24)[h]
        if d > d0 and m.get("laengste", 0) / 60 >= ERZ_ATTACKE_MIN:
            return True
        d += timedelta(days=1)
    return sek / 60 >= gm or oe >= go


def erz_basis(k, heute):
    """Durchschnitt der letzten 14 Tage (Minuten, Öffnungen) als Maßstab für die lockere Stufe."""
    ms = [erz_tag(heute - timedelta(days=n), k) for n in range(1, 15)]
    return {"min": round(sum(m.get("sek", 0) for m in ms) / 60 / len(ms), 1), "oeffnen": round(sum(m.get("oeffnen", 0) for m in ms) / len(ms), 1)}


def erz_pruefen():
    """Alle 5 Min (waechter) und beim Öffnen der App: Hinweise nach 24 Std. entscheiden, Rückfälle erkennen, Lockerung anbieten."""
    ERZ_LAUF["zuletzt"] = time.time()
    a = automatik_load()
    vorher = a.get("erziehung")
    z, heute, jetzt = erz_load(a), date.today(), time.time()
    porno, porno_zuletzt = erz_porno()
    meldungen = []
    for k, st in z.items():
        name = ERZ_KATEGORIEN[k][0]
        h = st.get("hinweis")
        if h:
            if jetzt >= h["ab"] + ERZ_FRIST:
                if erz_fenster(k, st, h["ab"], h["ab"] + ERZ_FRIST, a, porno_zuletzt):
                    st.update(stufe="hart", seit=heute.isoformat(), letzterRueckfall=heute.isoformat(), auto=jetzt)
                    meldungen.append((f"{name}: wieder hart gesperrt", "Du hast nach dem Hinweis weitergemacht. Ab jetzt gilt wieder die volle Sperre. Du schaffst das."))
                else:
                    st["gut"] = jetzt
                st.pop("hinweis", None)
            continue
        if st["stufe"] != "hart":
            satz = erz_rueckfall(k, st, a, heute, porno)
            if satz:
                st["hinweis"] = {"ab": jetzt, "text": satz}
                st["letzterRueckfall"] = heute.isoformat()
                st.pop("angebot", None)
                meldungen.append((f"{name}: Ich sehe einen Rückfall", satz[:180] + " Sollen wir wieder voll sperren?"))
                continue
        if st["stufe"] in ERZ_TAGE and not st.get("angebot") and (st.get("spaeterBis") or "") <= heute.isoformat():
            tage = (heute - erz_start(st, porno)).days
            if tage >= ERZ_TAGE[st["stufe"]]:
                st["angebot"] = {"ab": jetzt, "tage": tage}
                naechste = ERZ_STUFEN[ERZ_STUFEN.index(st["stufe"]) + 1]
                meldungen.append((f"{name}: {tage} Tage durchgehalten", f"Lass uns probieren, ob du es mit Stufe {ERZ_STUFE_NAME[naechste]} schaffst."))
    if z != erz_load(a):
        with AUTOMATIK_LOCK:
            a2 = automatik_load()
            if a2.get("erziehung") != vorher:   # in der Zwischenzeit von Hand geändert: diese Runde auslassen
                return z
            a2["erziehung"] = z
            automatik_save(a2)
        for t, x in meldungen:
            try:
                mac_mitteilung(t, x)
            except Exception:
                pass
    return z


def erz_status(neu=False):
    if neu or time.time() - ERZ_LAUF["zuletzt"] > 60:
        try:
            erz_pruefen()
        except Exception as e:
            print("Erziehung:", e)
    a = automatik_load()
    z, heute = erz_load(a), date.today()
    porno, _ = erz_porno()
    km = erz_kat_apps(a)
    out = []
    for k, (name, _) in ERZ_KATEGORIEN.items():
        st = z[k]
        if k == "weitere" and not km[k]:
            continue
        x = {"k": k, "name": name, "stufe": st["stufe"], "stufeName": ERZ_STUFE_NAME[st["stufe"]], "seit": st["seit"],
             "regeln": len(erz_regeln_der(k, a)), "apps": km[k][:12]}
        if st["stufe"] in ERZ_TAGE:
            x["tage"], x["ziel"] = max(0, (heute - erz_start(st, porno)).days), ERZ_TAGE[st["stufe"]]
            x["naechste"] = ERZ_STUFE_NAME[ERZ_STUFEN[ERZ_STUFEN.index(st["stufe"]) + 1]]
        if st.get("hinweis"):
            h = st["hinweis"]
            x["hinweis"] = {"text": h.get("text", ""), "bis": int((h["ab"] + ERZ_FRIST) * 1000), "abgelehnt": bool(h.get("abgelehnt"))}
        if st.get("angebot"):
            x["angebot"] = st["angebot"]
        if st.get("auto") and time.time() - st["auto"] < 3 * 86400:
            x["autoHart"] = True
        if st.get("gut") and time.time() - st["gut"] < 86400:
            x["gutGemacht"] = True
        if st["stufe"] != "hart":
            gm, go, bm, bo = erz_grenzen(st)
            m = erz_tag(heute, k)
            x["heute"] = {"min": round(m.get("sek", 0) / 60), "oeffnen": m.get("oeffnen", 0), "grenzeMin": round(gm), "grenzeOeffnen": round(go)}
        out.append(x)
    return {"kategorien": out, "porno": "immer gesperrt", "entwickler": ENTWICKLER}


def erz_aktion(data):
    """POST /api/erziehung {kat, aktion}: sperren (wieder hart), schaffeDas (Hinweis ablehnen), lockern (Angebot annehmen),
    spaeter (Angebot in 3 Tagen wieder), stufe {stufe} (strenger immer, lockerer nur in der Entwicklerversion), pruefen."""
    if data.get("aktion") == "pruefen":
        return erz_status(neu=True)
    k, akt = str(data.get("kat") or ""), str(data.get("aktion") or "")
    if k not in ERZ_KATEGORIEN:
        raise ApiFehler("Unbekannte Kategorie.")
    heute = date.today().isoformat()
    with AUTOMATIK_LOCK:
        a = automatik_load()
        z = erz_load(a)
        st = z[k]
        if akt == "sperren":
            st.update(stufe="hart", seit=heute)
            st.pop("hinweis", None); st.pop("angebot", None)
        elif akt == "schaffeDas":
            if st.get("hinweis"):
                st["hinweis"]["abgelehnt"] = time.time()
        elif akt == "lockern":
            if not st.get("angebot") and not ENTWICKLER:
                raise ApiFehler("Lockern gibt es erst, wenn du lange genug durchgehalten hast.")
            if st["stufe"] == "frei":
                raise ApiFehler("Lockerer geht es nicht.")
            if st["stufe"] == "hart":
                st["basis"] = erz_basis(k, date.today())
            st.update(stufe=ERZ_STUFEN[ERZ_STUFEN.index(st["stufe"]) + 1], seit=heute)
            st.pop("angebot", None); st.pop("hinweis", None)
        elif akt == "spaeter":
            st.pop("angebot", None)
            st["spaeterBis"] = (date.today() + timedelta(days=3)).isoformat()
        elif akt == "stufe":
            neu = str(data.get("stufe") or "")
            if neu not in ERZ_STUFEN:
                raise ApiFehler("Unbekannte Stufe.")
            if ERZ_STUFEN.index(neu) > ERZ_STUFEN.index(st["stufe"]) and not ENTWICKLER:
                raise ApiFehler("Lockern gibt es erst, wenn du lange genug durchgehalten hast.")
            if st["stufe"] == "hart" and neu != "hart" and not st.get("basis"):
                st["basis"] = erz_basis(k, date.today())
            st.update(stufe=neu, seit=heute)
            st.pop("hinweis", None); st.pop("angebot", None)
        else:
            raise ApiFehler("Unbekannte Aktion.")
        a["erziehung"] = z
        automatik_save(a)
    return erz_status()


def locker_status(r):
    if r.get("art") == "bis":
        aktiv = float(r.get("ende") or 0) > time.time()
        return {"gesperrt": aktiv, "satz": bindung_satz(r) if aktiv else "", "endeText": zeit_text(r["ende"]) if r.get("ende") else ""}
    if r.get("art") == "zeit":
        return {"gesperrt": locker_zeit_aktiv(r)}
    if r.get("art") == "sitzung":
        bis = max([v["bis"] for k, v in SITZUNG.items() if k[0] == r.get("id")] + [sitzung_pause_bis(r.get("id"))])
        return {"gesperrt": bis > time.time(), "pauseBis": datetime.fromtimestamp(bis).strftime("%H:%M") if bis > time.time() else ""}
    if r.get("art") == "oeffnen":
        n = max([v["n"] for k, v in OEFFNEN.items() if k[0] == r.get("id") and v["tag"] == date.today().isoformat()] or [0])
        return {"gesperrt": n >= int(r.get("anzahl") or 5), "geoeffnet": n}
    try:
        m = locker_minuten(r["apps"], locker_geraete_wirksam(r))
    except Exception:
        m = 0
    heute = date.today().weekday() in (r.get("tage") or range(7))
    return {"gesperrt": heute and m >= float(r.get("minuten") or 0), "verbraucht": round(m)}


def locker_warte_min(l):
    if not ENTWICKLER or (l or {}).get("wieKunde"):   # john 19.11.: 12 Std. für alle; 24.11.: Entwickler kann „wie Kunde“ testen
        return LOCKER_WARTE_STD
    try:
        return max(0, min(LOCKER_WARTE_MAX, int(l.get("warteMin", LOCKER_WARTE_STD))))
    except (TypeError, ValueError):
        return LOCKER_WARTE_STD


def locker_woche_n(l):
    """Wie oft diese Kalenderwoche schon der Timer zum Lockern lief (Montag beginnt neu)."""
    j, w, _ = date.today().isocalendar()
    x = l.get("lockernWoche") or {}
    return int(x.get("n", 0)) if x.get("kw") == f"{j}-{w}" else 0


def locker_warte_jetzt(l):
    """Wartezeit für das nächste Lockern: mit „teurer“ verdoppelt sie sich mit jedem Lockern in der Woche (höchstens 12 Std.,
    bei 12 Std. Grundwert also ohne Wirkung)."""
    m = locker_warte_min(l)
    if not m or (ENTWICKLER and not l.get("teurer", True)):   # john 05.11.: „teurer“ für Kunden immer an, nicht ausschaltbar
        return m
    return min(LOCKER_WARTE_MAX, m * 2 ** min(locker_woche_n(l), 6))


def locker_frei_status(l):
    jetzt = time.time()
    if not locker_warte_min(l):
        return {"frei": 0, "wartet": 0, "immerFrei": True}
    f = (l or {}).get("frei") or {}
    try:
        ab, bis = float(f.get("ab") or 0), float(f.get("bis") or 0)
    except (TypeError, ValueError):
        ab = bis = 0
    if ab > jetzt:
        return {"frei": 0, "wartet": int(ab - jetzt) + 1, "freiAb": ab, "freiBis": bis, "freiAbText": zeit_text(ab, jetzt), "freiBisText": zeit_text(bis, jetzt)}
    if ab and jetzt < bis:
        return {"frei": int(bis - jetzt), "wartet": 0, "freiBis": bis, "freiBisText": zeit_text(bis, jetzt)}
    return {"frei": 0, "wartet": 0}


def locker_lockern_text(l):
    """Fehlertext, wenn jemand ohne abgelaufene Wartezeit lockern will (john 19.11.: mit konkreter Uhrzeit)."""
    st = locker_frei_status(l)
    if st.get("wartet"):
        return f"Du kannst diese Regel {st['freiAbText'].replace(', ', ' ab ', 1)} lockern."
    m = locker_warte_jetzt(l)
    return f"Lockern braucht {f'{m // 60} Std.' if m >= 60 else f'{m} Min'} Wartezeit. Tippe auf „Lockern beantragen“, strenger geht sofort."


def locker_minuten_fenster(von, bis):
    """Minuten des Tages, die eine Sperrzeit abdeckt (über Mitternacht geht weiter am Morgen)."""
    v, b = (int(x[:2]) * 60 + int(x[3:5]) for x in (von, bis))
    return set(range(v, b)) if v < b else set(range(v, 1440)) | set(range(0, b))


def locker_strenger(alt, neu):
    """Ist die geänderte Regel gleich streng oder strenger? Dann geht die Änderung ohne Timer:
    mehr Apps, mehr Tage, mehr Geräte, längere Sperrzeit oder kleineres Limit."""
    try:
        n = locker_regel_sauber({**neu, "id": alt.get("id")})
    except (ValueError, TypeError):
        return True                    # ungültig: die Prüfung beim Speichern meldet den Fehler
    klein = lambda xs: {str(x).lower() for x in xs or []}
    # 19.11.: wirksame Apps vergleichen: Konsum-Schalter aus, mehr erlaubt oder weniger dazugenommen = lockern
    if n["art"] != alt.get("art") or not klein(locker_wirksame_apps(alt)) <= klein(locker_wirksame_apps(n)):
        return False
    if not set(alt.get("tage") or range(7)) <= set(n["tage"]):
        return False
    if n["geraete"] and (not alt.get("geraete") or not klein(alt["geraete"]) <= klein(n["geraete"])):
        return False                   # leer = alle Geräte
    if n["art"] == "bis":   # Bindung: gleich lang oder länger
        return n["ende"] >= float(alt.get("ende") or 0)
    if n["art"] == "zeit":
        return locker_minuten_fenster(alt["von"], alt["bis"]) <= locker_minuten_fenster(n["von"], n["bis"])
    if n["art"] == "sitzung":
        return n["minuten"] <= int(alt.get("minuten") or 0) and n["pause"] >= int(alt.get("pause") or 0)
    if n["art"] == "oeffnen":
        return n["anzahl"] <= int(alt.get("anzahl") or 0)
    return n["minuten"] <= int(alt.get("minuten") or 0)


def locker_ist_lockern(l, data):
    if data.get("loeschen") or data.get("an") is False:
        return False                   # 1.0: Entfernen läuft über die 24-Std.-Wartezeit (locker_aktion), nicht über den Timer
    if l.get("pin") and (data.get("pinNeu") or data.get("pinZufall")):
        return True
    r = data.get("regel")
    alt = next((x for x in l.get("regeln") or [] if r and r.get("id") and x.get("id") == r.get("id")), None)
    if alt and not locker_strenger(alt, r):
        return True                    # bestehende Regel lockern (weniger Apps, kürzere Zeit …) braucht den Timer
    if data.get("teurer") is False and l.get("teurer", True):
        return True
    if data.get("warteMin") is not None and ENTWICKLER:   # Kunden: Wartezeit fest, der Wert wird ignoriert
        try:
            return int(data["warteMin"]) < locker_warte_min(l)
        except (TypeError, ValueError):
            return False
    return False


LOCKER_ZEIT_NAMEN = (("Nachts", "23:00", "07:00"), ("Abends", "21:00", "07:00"), ("Morgens", "06:00", "09:00"), ("Arbeitszeit", "09:00", "17:00"))


def locker_regel_name(r, kat):
    """Kurzer Name einer Regel für „Gerade gesperrt“ (john 08.11.: Gruppen/Sperrzeiten statt jeder App einzeln)."""
    if r.get("gruppe"):
        return r["gruppe"]
    if r.get("art") == "bis":
        return apps_text(r["apps"])
    if r.get("art") == "zeit":
        return next((n for n, v, b in LOCKER_ZEIT_NAMEN if v == r.get("von") and b == r.get("bis")), "Sperrzeit")
    apps = [x.lower() for x in r["apps"]]
    for name, k in (("Social Media", "social"), ("Streaming", "streaming")):   # wie r2GruppeVon im Locker
        liste = [x.lower() for x in kat.get(k, [])]
        if len(apps) >= 3 and sum(x in liste for x in apps) * 2 > len(apps):
            return name
    return ", ".join(r["apps"][:2]) + (f" +{len(r['apps']) - 2}" if len(r["apps"]) > 2 else "")


def locker_jetzt_namen(regeln, a):
    kat, out = kategorien_daten(a), []
    for r in regeln:
        zusatz = f"bis {zeit_text(r['ende'])}" if r.get("art") == "bis" else f"bis {r['bis']}" if r.get("art") == "zeit" else "Pause" if r.get("art") == "sitzung" else "Limit erreicht"
        t = f"{locker_regel_name(r, kat)} ({zusatz})"
        if t not in out:
            out.append(t)
    return out


def locker_info(token=""):
    a = automatik_load()
    l, roh = locker_load(a), a.get("locker") or {}   # roh: Wartezeit-Einstellungen, die locker_load nicht mitnimmt
    jetzt = [r for r in l["regeln"] if l["an"] and locker_status(r)["gesperrt"]]
    out = {"pinGesetzt": LOCKER_OHNE_PIN or bool(l["pin"]), "ohnePin": LOCKER_OHNE_PIN, "fokus": fokus_laeuft(a), "an": l["an"], "anzahl": len(l["regeln"]), "pauseBis": l.get("pauseBis") or 0,
           "entfernen": locker_entfernen_offen(roh), "entfernenStd": LOCKER_ENTFERNEN_SEK // 3600, "sofort": ENTWICKLER,
           "jetzt": locker_jetzt_namen(jetzt, a), "geraetWegStd": LOCKER_GERAET_WEG_SEK // 3600}
    if LOCKER_OHNE_PIN or (token and LOCKER_TOKEN.get(token, 0) > time.time()):
        # 1.0.50 (john 09.11.): im Fokus Regeln zum Ansehen mitschicken; ändern verhindert locker_aktion (Fokus läuft)
        # 1.0.87 (john 20.11.): in der Entwickler-Pause die gespeicherten Regeln trotzdem zeigen (nur Anzeige, gesperrt=False); wirken tun sie erst nach der Pause
        anzeige = [{**r, **locker_status(r)} for r in l["regeln"]] if not l.get("pauseBis") else [{**r, **locker_status(r), **({"extra": list(r.get("apps") or [])} if r.get("konsum") else {}), "gesperrt": False, "pausiert": True}
                                                                                    for r in roh.get("regeln") or [] if r.get("id") not in set(roh.get("entfernen") or {}) - set(locker_entfernen_offen(roh))]   # schon fällig Entferntes nicht zeigen
        out.update(offen=not out["fokus"], regeln=anzeige,
                   geraete=geraete_namen(), apps=locker_kandidaten(), konsumApps=konsum_liste(a),   # 1.0.50; 19.11.: Konsum-Apps für „Diese Apps bleiben erlaubt“: am Handy kein „Mac“ in der Geräteauswahl
                   versionen={d.get("name"): d.get("version") or "" for d in handy_devices()},
                   warteMin=locker_warte_min(roh), warteJetzt=locker_warte_jetzt(roh),
                   teurer=bool(roh.get("teurer", True) or not ENTWICKLER), lockernWoche=locker_woche_n(roh), **locker_frei_status(roh),
                   geplant={k: {"ab": e["ab"], "abText": zeit_text(e["ab"]).replace(", ", " ab ", 1), "satz": e.get("satz") or ""} for k, e in locker_geplant_offen(roh).items()},
                   wieKunde=bool(roh.get("wieKunde")),
                   geplantFertig={k: {"ab": float(e.get("warAb") or e.get("ab")), "satz": e.get("satz") or ""} for k, e in (roh.get("geplant") or {}).items()
                                  if isinstance(e, dict) and (e.get("erledigt") and e.get("warAb") or float(e.get("ab") or 0) > 0)
                                  and 0 <= time.time() - float(e.get("warAb") or e.get("ab")) < 86400})   # auch fällig, aber noch nicht gespeichert angewendet
    return out


def locker_kandidaten():
    """Apps und Seiten zum Antippen: was in den letzten 7 Tagen lief (Ablenkendes und Meistgenutztes zuerst) plus die Sperr-Vorschläge."""
    sek = {}
    for i in range(7):
        try:
            d = day_data(date.today() - timedelta(days=i))
        except Exception:
            continue
        for g in d.get("geraete", []):
            for x in g.get("apps", []):
                n = " ".join(str(x.get("name") or "").split())[:40]
                if len(re.sub(r"[^\w]", "", n)) < 3 or "cockpit" in n.lower() or "blockem" in n.lower():
                    continue
                e = sek.setdefault(n.lower(), [n, 0, x.get("kat")])
                e[1] += x.get("sek") or 0
    liste = sorted(sek.values(), key=lambda e: (e[2] != "prokrastination", -e[1]))[:40]
    out = [{"name": n, "min": round(s / 60), "kat": k} for n, s, k in liste]
    gesehen = {o["name"].lower() for o in out}
    for w in sperre_daten()["kandidaten"]:
        if w.lower() not in gesehen:
            gesehen.add(w.lower())
            out.append({"name": w, "min": 0, "kat": "prokrastination"})
    return out


def locker_regel_sauber(r):
    apps = []
    for x in r.get("apps") or []:
        x = " ".join(str(x).split())[:40]
        if len(re.sub(r"[^\w]", "", x)) >= 3 and x.lower() not in [y.lower() for y in apps]:
            apps.append(x)
    konsum = bool(r.get("konsum")) and r.get("art", "zeit") == "zeit"
    if not apps and not konsum:
        raise ValueError("Bitte mindestens eine App oder Seite mit 3+ Buchstaben angeben.")
    tage = sorted({int(t) for t in r.get("tage") or [] if str(t).isdigit() and 0 <= int(t) <= 6}) or list(range(7))
    art = r.get("art") if r.get("art") in ("limit", "sitzung", "oeffnen", "bis") else "zeit"
    out = {"id": str(r.get("id") or secrets.token_hex(4))[:12], "art": art,
           "apps": apps[:60], "tage": tage, "geraete": [geraet_name(g) for g in r.get("geraete") or [] if geraet_name(g)]}
    if konsum:   # john 19.11.: Ruhezeit für alle Konsum-Apps, „erlaubt“ gilt nur für diese Regel
        out["konsum"] = True
        out["erlaubt"] = sorted({" ".join(str(x).split())[:40] for x in r.get("erlaubt") or [] if str(x).strip()}, key=str.lower)[:60]
        # 19.11. (Prüfer): Konsum-Apps, die ohnehin zu sind, nicht als „Zusätzlich“ speichern (sonst wirkt „erlauben“ später nicht);
        # wirksam bleibt dasselbe, also nie eine Lockerung
        zu = {n.lower() for n in konsum_liste()} - {x.lower() for x in out["erlaubt"]}
        out["apps"] = [x for x in apps if x.lower() not in zu][:60]
    if str(r.get("gruppe") or "").strip():   # john 05.11.: Gruppen im Locker (Social Media, Streaming …), nur ein Name zum Anzeigen
        out["gruppe"] = " ".join(str(r["gruppe"]).split())[:40]
    if out["art"] == "bis":   # john 19.11.: Bindung „bis Freitag 18 Uhr kein Instagram“, gilt jeden Tag bis zum Ende
        try:
            ende = float(r.get("ende"))
        except (TypeError, ValueError):
            raise ValueError("Bitte Datum und Uhrzeit für das Ende angeben.")
        if not time.time() < ende <= time.time() + 366 * 86400:
            raise ValueError("Das Ende muss in der Zukunft liegen (höchstens ein Jahr).")
        out.update(ende=int(ende), tage=list(range(7)))
    elif out["art"] == "zeit":
        if not (UHRZEIT.match(str(r.get("von"))) and UHRZEIT.match(str(r.get("bis")))) or r.get("von") == r.get("bis"):
            raise ValueError("Bitte eine gültige Zeit von–bis angeben.")
        out.update(von=r["von"], bis=r["bis"])
    elif out["art"] == "sitzung":
        out["minuten"] = max(1, min(240, int(r.get("minuten") or 0)))     # so lange am Stück
        out["pause"] = max(1, min(1440, int(r.get("pause") or 30)))      # danach so lange gesperrt
    elif out["art"] == "oeffnen":
        out["anzahl"] = max(1, min(100, int(r.get("anzahl") or 5)))       # so oft am Tag öffnen
    else:
        out["minuten"] = max(1, min(1440, int(r.get("minuten") or 0)))
    return out


def locker_aktion(data):
    with AUTOMATIK_LOCK:
        a = automatik_load()
        l = a.setdefault("locker", {})
        if ENTWICKLER and "pauseBis" in data:   # Entwickler-Pause ohne PIN: Regeln bis zum Zeitpunkt aus (0 = Pause beenden)
            l["pauseBis"] = max(0.0, float(data.get("pauseBis") or 0))
            l["einstZeit"] = time.time()
            automatik_save(a)
            sperre_protokoll(f"App Locker: Entwickler-Pause bis {datetime.fromtimestamp(l['pauseBis']).strftime('%d.%m. %H:%M') if l['pauseBis'] else 'beendet'}")
            return {**locker_info(str(data.get("token") or "")), "token": str(data.get("token") or ""), "neuePin": ""}
        if fokus_laeuft(a):
            raise ValueError("Während einer Fokus-Session bleibt der App Locker zu.")
        token = str(data.get("token") or "")
        offen = LOCKER_TOKEN.get(token, 0) > time.time()
        neue_pin = ""
        if l.get("entfernen") and locker_entfernen_anwenden(l):
            automatik_save(a)
        if l.get("geplant") and locker_geplant_anwenden(l):
            automatik_save(a)
        if locker_geraete_aufraeumen(l):
            automatik_save(a)
        hinweis = ""
        geraete_weg, wahl_kl = [], None   # 21.11.: Regel-Editor mit weniger Geräten = Antrag (24 Std.), kein Lockern-Timer, wenn sonst nichts lockerer wird
        if data.get("regel"):
            alt = next((x for x in l.get("regeln") or [] if x.get("id") and x.get("id") == data["regel"].get("id")), None)
            if alt and alt.get("art") != "bis":
                try:
                    neu_g = locker_regel_sauber({**data["regel"], "id": alt["id"]})["geraete"]
                except (ValueError, TypeError):
                    neu_g = None
                if neu_g:
                    alt_alle = list(alt.get("geraete") or []) or locker_alle_geraete(l)
                    neu_kl = {x.lower() for x in neu_g}
                    fehlt = [g for g in alt_alle if g.lower() not in neu_kl]   # inkl. schon beantragter
                    if fehlt:   # 24.11. (Prüfer): Gerät raus immer als 24-Std.-Antrag, auch wenn sonst noch etwas gelockert wird
                        geraete_weg = [g for g in fehlt if g not in locker_geraet_weg_offen(alt)]
                        wahl_kl = neu_kl
                        data = {**data, "regel": {**data["regel"], "geraete": list(dict.fromkeys(alt_alle + neu_g)) if alt.get("geraete") else []}}
        plan_alt = None   # 24.11.: eine Regel lockern = genau diese Änderung planen, nach der Wartezeit gilt sie von selbst
        if data.get("regel") and locker_warte_min(l):
            plan_alt = next((x for x in l.get("regeln") or [] if x.get("id") and x.get("id") == data["regel"].get("id") and x.get("art") != "bis"), None)
            if plan_alt and locker_strenger(plan_alt, data["regel"]):
                plan_alt = None
        if not plan_alt and locker_ist_lockern(l, data) and not (locker_frei_status(l)["frei"] or locker_frei_status(l).get("immerFrei")):
            raise ValueError(locker_lockern_text(l))
        if LOCKER_OHNE_PIN:
            offen = True
        elif not l.get("pin") or (offen and (data.get("pinNeu") or data.get("pinZufall"))):
            # pinZufall: lange Zufalls-PIN (16 Ziffern), wird nur in dieser Antwort einmal mitgeschickt
            pin = "".join(secrets.choice("0123456789") for _ in range(16)) if data.get("pinZufall") else re.sub(r"\D", "", str(data.get("pinNeu") or ""))
            if not re.fullmatch(r"\d{4,24}", pin):
                raise ValueError("Die PIN braucht 4 bis 24 Ziffern.")
            salz = secrets.token_hex(16)
            l["pin"] = {"salz": salz, "hash": pin_hash(pin, salz)}
            l["pinZeit"] = time.time()
            automatik_save(a)
            sperre_protokoll("App Locker: neue PIN")
            neue_pin = pin if data.get("pinZufall") else ""
            offen, token = True, token if offen else ""
        elif data.get("pin") is not None:
            if time.time() < LOCKER_FEHL["bis"]:
                raise ValueError(f"Zu oft falsch. Wieder möglich in {int(LOCKER_FEHL['bis'] - time.time()) // 60 + 1} Min.")
            if not secrets.compare_digest(pin_hash(re.sub(r"\D", "", str(data["pin"])), l["pin"]["salz"]), l["pin"]["hash"]):
                LOCKER_FEHL["n"] += 1
                if LOCKER_FEHL["n"] >= 5:
                    LOCKER_FEHL.update(n=0, bis=time.time() + 300)
                sperre_protokoll("App Locker: falsche PIN")
                raise ValueError("PIN falsch.")
            LOCKER_FEHL["n"] = 0
            offen, token = True, ""
        if not offen:
            raise ValueError("Bitte zuerst die PIN eingeben.")
        if not token:
            token = secrets.token_hex(16)
        LOCKER_TOKEN.clear()                      # immer nur ein offener Zugang
        LOCKER_TOKEN[token] = time.time() + LOCKER_OFFEN_SEK
        if data.get("warten") and not locker_frei_status(l)["wartet"] and not locker_frei_status(l)["frei"]:
            m = locker_warte_jetzt(l)
            ab = time.time() + m * 60
            l["frei"] = {"ab": ab, "bis": ab + LOCKER_FREI_SEK, "zeit": time.time()}   # 19.11.: gespeichert, übersteht Neustart
            LOCKER_TOKEN[token] = ab + LOCKER_FREI_SEK   # Bereich bleibt offen, solange der Timer läuft
            j, w, _ = date.today().isocalendar()
            l["lockernWoche"] = {"kw": f"{j}-{w}", "n": locker_woche_n(l) + 1}
            sperre_protokoll(f"App Locker: Lockern beantragt, möglich ab {zeit_text(ab)}")
        if data.get("abbrechen"):
            if float((l.get("frei") or {}).get("ab") or 0) > time.time() and locker_woche_n(l):
                l["lockernWoche"]["n"] -= 1      # wer den Timer abbricht, hat nicht gelockert: zählt nicht
            l["frei"] = {"ab": 0, "bis": 0, "zeit": time.time()}
            sperre_protokoll("App Locker: Timer abgebrochen")
        if data.get("warteMin") is not None and ENTWICKLER:   # 19.11.: Kunden haben fest 12 Std., ihr Wert wird ignoriert
            try:
                l["warteMin"] = max(0, min(LOCKER_WARTE_MAX, int(data["warteMin"])))
            except (TypeError, ValueError):
                raise ValueError(f"Bitte eine Wartezeit von 0 bis {LOCKER_WARTE_MAX} Minuten angeben.")
            l["einstZeit"] = time.time()
            sperre_protokoll(f"App Locker: Wartezeit {l['warteMin']} Min")
        if data.get("teurer") is not None:
            if data["teurer"] is False and not ENTWICKLER:
                raise ValueError("Lockern wird teurer ist immer an.")
            l["teurer"] = bool(data["teurer"])
            l["einstZeit"] = time.time()
            sperre_protokoll(f"App Locker: Lockern wird teurer {'an' if l['teurer'] else 'aus'}")
        regeln = l.setdefault("regeln", [])
        if plan_alt:
            r = locker_regel_sauber({**data["regel"], "id": plan_alt["id"]})
            s = locker_strenger_teil(plan_alt, r)   # was strenger wird, gilt sofort; nur der Rest wartet
            offen_g = locker_geraet_weg_offen(plan_alt)
            if offen_g:   # wie im strengen Zweig: ein wieder gewähltes Gerät (oder „alle Geräte“) bricht seinen 24-Std.-Antrag sofort ab
                alle = wahl_kl is None and not r.get("geraete")
                neu_kl = wahl_kl if wahl_kl is not None else {x.lower() for x in r.get("geraete") or []}
                bleibt = {} if alle else {g: x for g, x in offen_g.items() if g.lower() not in neu_kl}
                if bleibt != offen_g:
                    s = json.loads(json.dumps(s))
                    s.pop("geraetWeg", None)
                    if bleibt:
                        s["geraetWeg"] = bleibt
            if s is not plan_alt:
                s["geaendert"] = time.time()
                l["regeln"] = [x for x in l.get("regeln") or [] if x.get("id") != s["id"]] + [s]
                plan_alt = s
            ab = time.time() + locker_warte_jetzt(l) * 60
            vorher = {k: v for k, v in plan_alt.items() if k not in ("geaendert", "geraetWeg")}
            offen_plan = locker_geplant_offen(l).get(plan_alt["id"])
            if offen_plan:   # schon ein Antrag offen: beide Änderungen zusammen, die Wartezeit beginnt neu
                r = locker_geplant_regel({"vorher": vorher, "regel": r}, locker_geplant_regel(offen_plan, plan_alt), neu_gewinnt=True)
                r = {k: v for k, v in r.items() if k != "geaendert"}
            l.setdefault("geplant", {})[plan_alt["id"]] = {"regel": r, "vorher": vorher, "ab": ab, "zeit": time.time(), "satz": locker_aenderung_satz(plan_alt, r)}
            j, w, _ = date.today().isocalendar()
            l["lockernWoche"] = {"kw": f"{j}-{w}", "n": locker_woche_n(l) + 1}
            hinweis = f"Lockern beantragt. Gilt {zeit_text(ab).replace(', ', ' ab ', 1)}, bis dahin bleibt alles wie jetzt."
            if geraete_weg:   # Gerät raus läuft wie bisher als eigener 24-Std.-Antrag
                locker_geraet_antrag(l, next(x for x in l["regeln"] if x.get("id") == plan_alt["id"]), geraete_weg)
            sperre_protokoll(f"App Locker: Lockern beantragt ({l['geplant'][plan_alt['id']]['satz']}), gilt {zeit_text(ab)}")
        elif data.get("regel"):
            r = locker_regel_sauber(data["regel"])
            r["geaendert"] = time.time()
            alt = next((x for x in regeln if x.get("id") == r["id"]), None)
            offen = locker_geraet_weg_offen(alt) if alt else {}
            if offen and (r["geraete"] or wahl_kl is not None):   # laufende Anträge bleiben; ein wieder gewähltes Gerät (oder „alle Geräte“ = leer) bricht seinen Antrag ab
                neu_kl = wahl_kl if wahl_kl is not None else {x.lower() for x in r["geraete"]}
                bleibt = {g: ab for g, ab in offen.items() if g.lower() not in neu_kl}
                if r["geraete"]:
                    r["geraete"] = list(dict.fromkeys(r["geraete"] + list(bleibt)))
                if bleibt:
                    r["geraetWeg"] = bleibt
            if geraete_weg:
                locker_geraet_antrag(l, r, geraete_weg)
                sperre_protokoll(f"App Locker: {', '.join(geraete_weg)} aus Regel {', '.join(r['apps'])[:60]} in 24 Std.")
            l["regeln"] = [x for x in regeln if x.get("id") != r["id"]] + [r]
            sperre_protokoll(f"App Locker: Regel {r['art']} {', '.join(r['apps'])}")
        for schluessel in ("geraetWeg", "geraetWegAb", "geraetDazu"):   # 21.11.: Geräte einer Regel (Ansicht „Geräte“)
            w = data.get(schluessel)
            if not isinstance(w, dict):
                continue
            r = next((x for x in l.get("regeln") or [] if x.get("id") == w.get("id")), None)
            g = geraet_name(w.get("geraet"))
            if not r or not g:
                raise ValueError("Diese Regel oder dieses Gerät gibt es nicht mehr.")
            if r.get("art") == "bis":
                raise ValueError("Feste Sperren gelten auf allen Geräten.")
            if schluessel == "geraetWeg":
                if g.lower() not in {x.lower() for x in locker_geraete_wirksam(r) or locker_alle_geraete(l)}:
                    raise ValueError(f"Die Regel gilt auf {g} schon nicht mehr.")
                if g not in locker_geraet_weg_offen(r):
                    locker_geraet_antrag(l, r, [g])
                    sperre_protokoll(f"App Locker: {g} aus Regel {', '.join(r.get('apps') or [])[:60]} in 24 Std.")
            elif schluessel == "geraetWegAb":
                weg = {k: v for k, v in locker_geraet_weg_offen(r).items() if k.lower() != g.lower()}
                r.pop("geraetWeg", None)
                if weg:
                    r["geraetWeg"] = weg
                r["geaendert"] = time.time()
                sperre_protokoll(f"App Locker: {g} bleibt in Regel {', '.join(r.get('apps') or [])[:60]}")
            elif r.get("geraete") and g.lower() not in {x.lower() for x in locker_geraete_wirksam(r)}:   # zuschalten = strenger, gilt sofort
                if g.lower() not in {x.lower() for x in r["geraete"]}:
                    r["geraete"] = r["geraete"] + [g]
                weg = {k: v for k, v in locker_geraet_weg_offen(r).items() if k.lower() != g.lower()}
                r.pop("geraetWeg", None)
                if weg:
                    r["geraetWeg"] = weg
                r["geaendert"] = time.time()
                sperre_protokoll(f"App Locker: Regel {', '.join(r.get('apps') or [])[:60]} jetzt auch auf {g}")
        if data.get("loeschen") and ENTWICKLER:
            l["regeln"] = [x for x in regeln if x.get("id") != data["loeschen"]]
            weg = l.setdefault("geloescht", {})
            weg[str(data["loeschen"])] = time.time()
            if len(weg) > 300:
                l["geloescht"] = dict(sorted(weg.items(), key=lambda x: x[1])[-300:])
            sperre_protokoll(f"App Locker: Regel gelöscht")
        elif data.get("loeschen"):   # 1.0: erst nach 24 Std. wirklich weg, bis dahin gilt die Regel weiter
            alt = next((x for x in regeln if x.get("id") == data["loeschen"]), None)
            if not alt:
                raise ValueError("Diese Regel gibt es nicht mehr.")
            if alt.get("art") == "bis":   # john 19.11.: Bindung vorzeitig beenden = Lockern, 12 Std. Wartezeit
                ab = time.time() + locker_warte_min(l) * 60
                if float(alt.get("ende") or 0) <= ab:
                    raise ValueError(f"Diese Bindung endet ohnehin {zeit_text(alt['ende'])}, früher geht es nicht.")
                l.setdefault("entfernen", {})[str(alt["id"])] = {"ab": ab, "zeit": time.time()}
                sperre_protokoll(f"App Locker: Bindung endet vorzeitig {zeit_text(ab)}")
            else:
                l.setdefault("entfernen", {})[str(data["loeschen"])] = {"ab": time.time() + LOCKER_ENTFERNEN_SEK, "zeit": time.time()}
                sperre_protokoll("App Locker: Regel wird in 24 Std. entfernt")
        if data.get("geplantAbbrechen"):
            k = str(data["geplantAbbrechen"])
            if k in locker_geplant_offen(l):
                l["geplant"][k] = {"ab": 0, "zeit": time.time()}
                if locker_woche_n(l):
                    l["lockernWoche"]["n"] -= 1   # abgebrochen = nicht gelockert, zählt nicht
                sperre_protokoll("App Locker: Lockern abgebrochen")
        if "wieKunde" in data and ENTWICKLER:   # 24.11.: Entwickler testet die echte Wartezeit (12 Std.)
            l["wieKunde"], l["einstZeit"] = bool(data["wieKunde"]), time.time()
        if data.get("loeschenAbbrechen"):
            l.setdefault("entfernen", {})[str(data["loeschenAbbrechen"])] = {"ab": 0, "zeit": time.time()}
            sperre_protokoll("App Locker: Entfernen abgebrochen")
        if data.get("an") is False and not ENTWICKLER:
            l.setdefault("entfernen", {})["_an"] = {"ab": time.time() + LOCKER_ENTFERNEN_SEK, "zeit": time.time()}
            sperre_protokoll("App Locker: wird in 24 Std. ausgeschaltet")
        elif data.get("an") is not None:
            l["an"] = bool(data["an"])
            l["einstZeit"] = time.time()
            if l["an"] and (l.get("entfernen") or {}).get("_an"):
                l["entfernen"]["_an"] = {"ab": 0, "zeit": time.time()}
            sperre_protokoll(f"App Locker {'an' if l['an'] else 'aus'}")
        if data.get("schliessen"):
            LOCKER_TOKEN.clear()
            f = l.get("frei") or {}
            if float(f.get("ab") or 0) <= time.time() < float(f.get("bis") or 0):   # offenes Fenster zu; ein laufender 12-Std.-Antrag bleibt
                l["frei"] = {"ab": 0, "bis": 0, "zeit": time.time()}
            token = ""
        jetzt = time.time()   # 19.11.: abgelaufene Bindungen aufräumen (Löschen mit Zeitpunkt, damit der Abgleich sie nicht zurückholt)
        for x in [x for x in l.get("regeln") or [] if x.get("art") == "bis" and float(x.get("ende") or 0) < jetzt - 3600]:
            l["regeln"] = [y for y in l["regeln"] if y.get("id") != x.get("id")]
            l.setdefault("geloescht", {})[str(x.get("id"))] = jetzt
        automatik_save(a)
    return {**locker_info(token), "token": token, "neuePin": neue_pin, "hinweis": hinweis}


# ---------- Locker auf jedem Gerät (3.14): Handys halten eine Kopie, ändern sie auch ohne Mac, Abgleich in beide Richtungen ----------
def geraete_namen():
    """Geräte für Regeln und Werte. 1.0.14: am Handy kein fester „Mac“ mehr (BlockEm nur auf dem Handy hat keinen)."""
    handys = [d.get("name") for d in handy_devices() if d.get("name")]
    return handys if MODUS == "handy" else ["Mac"] + handys


def locker_kopie():
    """Ganzer Locker-Stand für ein Handy (Regeln, PIN-Hash, Einstellungen) plus Geräte und App-Vorschläge für die Offline-Anzeige."""
    a = automatik_load()
    try:
        apps = locker_kandidaten()
    except Exception:
        apps = []
    return {"locker": a.get("locker") or {}, "geraete": geraete_namen(), "apps": apps}


def locker_mischen(hier, fremd):
    """Zwei Locker-Stände zusammenführen: je Regel gewinnt die jüngere Änderung, Löschen zählt mit Zeitpunkt,
    PIN und Einstellungen (an, Wartezeit, teurer) vom Stand, der sie zuletzt geändert hat."""
    hier, fremd = dict(hier or {}), dict(fremd or {})
    weg = dict(hier.get("geloescht") or {})
    for k, t in (fremd.get("geloescht") or {}).items():
        weg[k] = max(float(t or 0), float(weg.get(k) or 0))
    regeln = {}
    for r in list(hier.get("regeln") or []) + list(fremd.get("regeln") or []):
        if not isinstance(r, dict) or not r.get("id"):
            continue
        alt = regeln.get(r["id"])
        if not alt or float(r.get("geaendert") or 0) > float(alt.get("geaendert") or 0):
            regeln[r["id"]] = r
    out = dict(hier)
    out["regeln"] = [r for r in regeln.values() if float(weg.get(str(r["id"])) or -1) < float(r.get("geaendert") or 0)]
    if weg:
        out["geloescht"] = dict(sorted(weg.items(), key=lambda x: x[1])[-300:])
    if fremd.get("pin") and (not hier.get("pin") or float(fremd.get("pinZeit") or 0) > float(hier.get("pinZeit") or 0)):
        out["pin"], out["pinZeit"] = fremd["pin"], fremd.get("pinZeit") or 0
    if float(fremd.get("einstZeit") or 0) > float(hier.get("einstZeit") or 0):
        for k in ("an", "warteMin", "teurer", "wieKunde", "einstZeit"):
            if k in fremd:
                out[k] = fremd[k]
    ent = dict(hier.get("entfernen") or {})   # 1.0: Entfern-Aufträge (24 Std.), je Schlüssel der jüngere Eintrag (ab 0 = abgebrochen)
    for k, e in (fremd.get("entfernen") or {}).items():
        if isinstance(e, dict) and float(e.get("zeit") or 0) > float((ent.get(k) or {}).get("zeit") or 0):
            ent[k] = e
    if ent:
        out["entfernen"] = ent
    gp = dict(hier.get("geplant") or {})   # 24.11.: geplante Lockerungen, je Regel der jüngere Eintrag (ab 0 = erledigt/abgebrochen)
    for k, e in (fremd.get("geplant") or {}).items():
        if isinstance(e, dict) and float(e.get("zeit") or 0) > float((gp.get(k) or {}).get("zeit") or 0):
            gp[k] = e
    if gp:
        out["geplant"] = gp
    if float((fremd.get("frei") or {}).get("zeit") or 0) > float((hier.get("frei") or {}).get("zeit") or 0):
        out["frei"] = fremd["frei"]   # 19.11.: Lockern-Antrag (12 Std.) gilt auf allen Geräten, der jüngere Stand gewinnt
    w1, w2 = hier.get("lockernWoche") or {}, fremd.get("lockernWoche") or {}
    if w2.get("kw") and (w2.get("kw") != w1.get("kw") and w2["kw"] > str(w1.get("kw") or "") or w2.get("kw") == w1.get("kw") and int(w2.get("n", 0)) > int(w1.get("n", 0))):
        out["lockernWoche"] = w2
    return out


def locker_abgleich(data):
    """POST /api/locker-abgleich {locker}: ein Handy bringt seine offline geänderte Kopie, zurück kommt der gemeinsame Stand."""
    fremd = data.get("locker")
    if not isinstance(fremd, dict):
        raise ValueError("Locker-Stand fehlt.")
    with AUTOMATIK_LOCK:
        a = automatik_load()
        vorher = json.dumps(a.get("locker") or {}, sort_keys=True)
        a["locker"] = locker_mischen(a.get("locker") or {}, fremd)
        if a["locker"].get("entfernen"):
            locker_entfernen_anwenden(a["locker"])
        if a["locker"].get("geplant"):
            locker_geplant_anwenden(a["locker"])
        locker_geraete_aufraeumen(a["locker"])
        if json.dumps(a["locker"], sort_keys=True) != vorher:
            automatik_save(a)
            sperre_protokoll(f"App Locker: Abgleich mit {geraet_name(data.get('geraet')) or 'Handy'}")
    return locker_kopie()


# Wochenplan kommt fertig: Sonntag (für die nächste Woche) und Montag (für diese Woche) ein Vorschlag aus den aktiven Phasen
def wochenplan(out):
    if not automatik_an("wochenplan"):
        return None
    heute = date.today()
    if heute.weekday() not in (6, 0):
        return None
    montag = heute + timedelta(days=1) if heute.weekday() == 6 else heute
    kw = montag.isocalendar()[1]
    a = automatik_load().get("wochenplan") or {}
    if a.get("kw") == kw and a.get("jahr") == montag.year:
        return None
    tage = [montag + timedelta(days=i) for i in range(7)]
    std = round(sum(tag_zeit(out, d.isoformat())[0] for d in tage), 2)
    if not std:
        return None
    woche_offen = [t for t in out["woche"] if not t["done"]]
    belegt = sum(t.get("rest") if t.get("rest") is not None else STD_OHNE for t in woche_offen)
    drin = {t["text"].lower() for t in out["woche"] + out["heute"]}
    rang = {"muss": 0, "soll": 1, "kann": 2}
    kand = []
    for pr in out["projekte"]:
        if not pr["aktiv"]:
            continue
        for ph in pr["phasen"]:
            if not ph["aktiv"]:
                continue
            for i, g in enumerate(ph["ziele"]):
                if g["fertig"] or g["name"].lower() in drin:
                    continue
                rest = round((g["aufwand"] or STD_OHNE) * (100 - g["prozent"]) / 100, 2)
                kand.append((rang.get(g.get("prio") or "", 1), i, {"text": g["name"], "projekt": pr["name"], "std": rest, "woher": ph["name"], "prio": g.get("prio")}))
    frei, wahl = std - belegt, []
    for _, _, x in sorted(kand, key=lambda k: (k[0], k[1])):
        if x["std"] <= frei + 1e-9:
            wahl.append(x)
            frei -= x["std"]
    if not wahl:
        return None
    return {"kw": kw, "jahr": montag.year, "std": std, "belegt": round(belegt, 2), "liste": wahl[:15], "naechste": heute.weekday() == 6}


def wochenplan_setzen(data, out):
    """{"status": "uebernommen", "woche": [{text, projekt}]} oder {"status": "weg"}."""
    wp = wochenplan(out)
    if data.get("status") == "uebernommen" and data.get("woche") and wp:
        alt = [{"text": t["text"], "projekt": t.get("projekt")} for t in out["woche"] if not t["done"]]
        plan_setzen({"woche": alt + [{"text": x.get("text", ""), "projekt": x.get("projekt")} for x in data["woche"]], "kw": wp["kw"]})
    with AUTOMATIK_LOCK:
        a = automatik_load()
        ziel = wp or {"kw": date.today().isocalendar()[1], "jahr": date.today().year}
        a["wochenplan"] = {"kw": ziel["kw"], "jahr": ziel["jahr"], "status": data.get("status") or "weg"}
        automatik_save(a)


# Kalender: freie Zeiten aus dem Google-Kalender (geheime iCal-Adresse). Termine sind schon verplant, gezeigt wird nur, was frei ist.
KALENDER = {"cache": {}}
KAL_VON, KAL_BIS = 8, 20   # Tagesrahmen für freie Zeiten


def ics_zeit(wert, params):
    from zoneinfo import ZoneInfo
    if params.get("VALUE") == "DATE" or (len(wert) == 8 and wert.isdigit()):
        return date(int(wert[:4]), int(wert[4:6]), int(wert[6:8])), True
    dt = datetime.strptime(wert.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if wert.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc).astimezone(), False
    if params.get("TZID"):
        try:
            return dt.replace(tzinfo=ZoneInfo(params["TZID"])).astimezone(), False
        except Exception:
            pass
    return dt.astimezone(), False


def ics_text(w):
    return w.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()[:200]


def ics_lesen(text):
    """-> Liste von Terminen {start, ende, ganz, rrule, exdate, rid, uid}; Zeilen werden entfaltet."""
    zeilen, evs, cur = [], [], None
    for l in text.replace("\r\n", "\n").split("\n"):
        if l[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += l[1:]
        else:
            zeilen.append(l)
    for l in zeilen:
        if l == "BEGIN:VEVENT":
            cur = {"exdate": []}
        elif l == "END:VEVENT" and cur is not None:
            if "start" in cur and cur.get("status") != "CANCELLED" and cur.get("transp") != "TRANSPARENT":
                evs.append(cur)
            cur = None
        elif cur is not None and ":" in l:
            kopf, wert = l.split(":", 1)
            name, *ps = kopf.split(";")
            params = dict(p.split("=", 1) for p in ps if "=" in p)
            try:
                if name == "DTSTART":
                    cur["start"], cur["ganz"] = ics_zeit(wert, params)
                elif name == "DTEND":
                    cur["ende"], _ = ics_zeit(wert, params)
                elif name == "DURATION":
                    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", wert)
                    cur["dauer"] = timedelta(days=int(m.group(1) or 0), hours=int(m.group(2) or 0), minutes=int(m.group(3) or 0)) if m else None
                elif name == "RRULE":
                    cur["rrule"] = dict(p.split("=", 1) for p in wert.split(";") if "=" in p)
                elif name == "EXDATE":
                    cur["exdate"] += [ics_zeit(w, params)[0] for w in wert.split(",")]
                elif name == "RECURRENCE-ID":
                    cur["rid"], _ = ics_zeit(wert, params)
                elif name == "UID":
                    cur["uid"] = wert
                elif name == "SUMMARY":
                    cur["titel"] = ics_text(wert)
                elif name == "LOCATION":
                    cur["ort"] = ics_text(wert)
                elif name == "STATUS":
                    cur["status"] = wert.strip()
                elif name == "TRANSP":
                    cur["transp"] = wert.strip()
            except Exception:
                pass
    return evs


ICS_WT = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def ics_am_tag(evs, tag):
    """Zeitlich feste Termine an einem Tag (lokale Zeit) als [(start, ende, ganztägig)]; einfache Wiederholungen (täglich/wöchentlich/monatlich)."""
    ersetzt = {(e.get("uid"), e["rid"]) for e in evs if e.get("rid")}
    out = []
    for e in evs:
        s0 = e["start"]
        ende0 = e.get("ende") or (s0 + (e.get("dauer") or (timedelta(days=1) if e["ganz"] else timedelta(0))))
        dauer = ende0 - s0
        d0 = s0 if e["ganz"] else s0.date()
        r = e.get("rrule")
        treffer = None
        if e.get("rid") or not r:
            treffer = d0 if d0 <= tag < (ende0 if e["ganz"] else ende0.date() + timedelta(days=1)) else None
            if treffer and not e["ganz"] and ende0.date() < tag:
                treffer = None
        elif tag >= d0:
            freq, iv = r.get("FREQ"), int(r.get("INTERVAL", "1") or 1)
            until = None
            if r.get("UNTIL"):
                u, _ = ics_zeit(r["UNTIL"], {})
                until = u if isinstance(u, date) and not isinstance(u, datetime) else u.date()
            ok = until is None or tag <= until
            diff = (tag - d0).days
            if ok and freq == "DAILY":
                ok = diff % iv == 0 and (not r.get("COUNT") or diff // iv < int(r["COUNT"]))
            elif ok and freq == "WEEKLY":
                tage = [ICS_WT[x[-2:]] for x in r.get("BYDAY", "").split(",") if x[-2:] in ICS_WT] or [d0.weekday()]
                wochen = (tag - (d0 - timedelta(days=d0.weekday()))).days // 7
                ok = tag.weekday() in tage and wochen % iv == 0
                if ok and r.get("COUNT"):
                    ok = wochen // iv * len(tage) < int(r["COUNT"])
            elif ok and freq == "MONTHLY":
                monate = (tag.year - d0.year) * 12 + tag.month - d0.month
                ok = tag.day == d0.day and monate % iv == 0 and not r.get("BYDAY")
            elif ok and freq == "YEARLY":
                ok = (tag.month, tag.day) == (d0.month, d0.day)
            else:
                ok = False
            treffer = tag if ok else None
        if not treffer:
            continue
        if e["ganz"]:
            if r and tag in e["exdate"]:
                continue
            out.append((None, None, True, e))
            continue
        s = datetime.combine(tag, s0.timetz()) if r else s0
        if r and (any(isinstance(x, datetime) and x.astimezone().date() == tag and x.astimezone().time() == s0.time() for x in e["exdate"]) or (e.get("uid"), s) in ersetzt):
            continue
        out.append((s, s + dauer, False, e))
    return out


KAL_FARBEN = ["#5ad1ec", "#a78bfa", "#f2b45a", "#5be39a", "#f472b6", "#fb923c"]


def kal_einstellung(a=None):
    """automatik.json "kalender": {"liste": [{id, name, url, farbe, an}], "von": 8, "bis": 20, "abziehen": true}; alte Form {"url"} wird übernommen."""
    a = automatik_load() if a is None else a
    k = dict(a.get("kalender") or {})
    liste = list(k.get("liste") or [])
    if not liste and k.get("url"):
        liste = [{"id": "k1", "name": "Kalender", "url": k["url"], "farbe": KAL_FARBEN[0], "an": True}]
    von = int(k.get("von", CFG.get("tagVon", KAL_VON)))
    bis = int(k.get("bis", CFG.get("tagBis", KAL_BIS)))
    platz = k.get("platzhalter")
    return {"liste": liste, "von": max(0, min(23, von)), "bis": max(von + 1, min(24, bis)), "abziehen": k.get("abziehen", True),
            "platzhalter": [str(x)[:30] for x in platz][:20] if isinstance(platz, list) else ["X"]}


def kal_platz_key(t):
    """Titel für den Vergleich mit den Platzhaltern: klein, ohne Satzzeichen und Leerzeichen am Rand („ X. “ -> „x“)."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", str(t or "")).lower()


def kal_fehlertext(e):
    """Verständliche Meldung, wenn ein Kalender nicht geladen werden kann."""
    code = getattr(e, "code", None)
    if code in (401, 403):
        return "Kein Zugriff. Ist es die private (geheime) iCal-Adresse?"
    if code == 404:
        return "Die Adresse gibt es nicht mehr. Hol im Kalender eine neue iCal-Adresse."
    if code:
        return f"Der Kalender-Dienst antwortet mit Fehler {code}. Später nochmal."
    t = str(e)
    if t == "kein-ics":
        return "Unter der Adresse liegt kein Kalender. Nimm die Adresse im iCal-Format (endet meist auf .ics)."
    if "SSL" in t.upper() or "CERTIFICATE" in t.upper():
        return "Sichere Verbindung zum Kalender klappt nicht. Prüf die Adresse (https://)."
    if "timed out" in t or "Errno 8" in t or "Name or service" in t or "nodename" in t or "Errno 61" in t or "Errno 111" in t:
        return "Keine Verbindung zum Kalender. Internet da? Dann die Adresse prüfen."
    return f"Kalender lässt sich nicht laden ({t[:80]})."


def kal_laden(url, warten=True):
    """ICS einer Adresse holen (10 Min zwischengespeichert). Ist der Stand alt, wird im Hintergrund neu geladen und so lange der alte gezeigt."""
    c = KALENDER.setdefault("cache", {}).setdefault(url, {"stand": 0, "termine": [], "fehler": "", "laedt": False})

    def holen():
        c["laedt"] = True
        try:
            with urlopen(Request(url, headers={"User-Agent": "Fokus-Cockpit"}), timeout=10) as r:
                text = r.read().decode("utf-8", "replace")
            if "BEGIN:VCALENDAR" not in text[:3000]:
                raise ValueError("kein-ics")
            c["termine"], c["fehler"] = ics_lesen(text), ""
        except Exception as e:
            fehler = str(e)
            if "CERTIFICATE" in fehler.upper() and Path("/usr/bin/curl").exists():
                import subprocess
                r = subprocess.run(["/usr/bin/curl", "-sSL", "-m", "15", url], capture_output=True, timeout=20)
                if r.returncode == 0 and b"BEGIN:VCALENDAR" in r.stdout[:3000]:
                    c["termine"], c["fehler"] = ics_lesen(r.stdout.decode("utf-8", "replace")), ""
                else:
                    c["fehler"] = kal_fehlertext(e)
            else:
                c["fehler"] = kal_fehlertext(e)
        c["stand"], c["laedt"] = time.time(), False

    if time.time() - c["stand"] > 600 and not c["laedt"]:
        if c["stand"] == 0 and warten:
            holen()
        else:
            threading.Thread(target=holen, daemon=True).start()
    return c


def kal_termine(tag, ke=None):
    """Alle Termine eines Tages aus allen eingeschalteten Kalendern, sortiert: [{titel, von, bis, ganz, kalender, farbe, ort, start, ende}]."""
    ke = ke or kal_einstellung()
    out, fehler = [], []
    platz = {kal_platz_key(x) for x in ke.get("platzhalter") or [] if kal_platz_key(x)}
    eigene = g_eigene_ids() if GOOGLE_DATEI.exists() else set()
    for k in ke["liste"]:
        if not k.get("an", True) or not k.get("url"):
            continue
        c = kal_laden(k["url"])
        if c["fehler"]:
            fehler.append(f"{k.get('name') or 'Kalender'}: {c['fehler']}")
        for s, t, ganz, e in ics_am_tag(c["termine"], tag):
            if platz and kal_platz_key(e.get("titel")) in platz:
                continue                       # Platzhalter („X“, „Block“ …) halten nur frei, sind kein echter Termin
            if eigene and str(e.get("uid") or "").split("@")[0] in eigene:
                continue                       # von BlockEm selbst eingetragener Block, steht schon in der Zeitleiste
            out.append({"titel": e.get("titel") or "Termin", "ganz": ganz, "kalender": k.get("name") or "Kalender", "farbe": k.get("farbe") or KAL_FARBEN[0],
                        "ort": e.get("ort", ""), "von": s.strftime("%H:%M") if s else "", "bis": t.strftime("%H:%M") if t else "",
                        "start": s, "ende": t})
    out.sort(key=lambda x: (not x["ganz"], x["start"] or datetime.min.replace(tzinfo=timezone.utc)))
    return out, fehler


def kal_belegt_min(termine, tag, ke):
    """Minuten, die Termine im Tagesrahmen (von–bis) belegen; überlappende Termine zählen einmal."""
    von = datetime.combine(tag, datetime.min.time()).astimezone().replace(hour=ke["von"])
    bis = von + timedelta(hours=ke["bis"] - ke["von"])
    spannen = sorted((max(x["start"], von), min(x["ende"], bis)) for x in termine if not x["ganz"] and x["ende"] > von and x["start"] < bis)
    summe, pos = 0, von
    for s, t in spannen:
        s = max(s, pos)
        if t > s:
            summe += (t - s).total_seconds()
            pos = t
    return round(summe / 60)


def kalender_frei(tag=None):
    """Kalender für Heute: {"frei": [{von,bis,min}], "termine": [...], "ganztags": [...], "belegtMin", "tage": {iso: {min, anzahl}}, "fehler"}; None ohne Kalender."""
    a = automatik_load()
    ke = kal_einstellung(a)
    if not any(k.get("an", True) and k.get("url") for k in ke["liste"]) or not automatik_an("kalender", a):
        return None
    tag = tag or date.today()
    schluessel = json.dumps([tag.isoformat(), ke], sort_keys=True, default=str)
    alt = KALENDER.get("frei")
    if alt and alt[1] == schluessel and time.time() - alt[0] < 60 and not any(c.get("laedt") for c in KALENDER["cache"].values()):
        return alt[2]
    erg = kalender_rechnen(tag, ke)
    KALENDER["frei"] = (time.time(), schluessel, erg)
    return erg


def kalender_rechnen(tag, ke):
    termine, fehler = kal_termine(tag, ke)
    jetzt = datetime.now().astimezone()
    von = datetime.combine(tag, datetime.min.time()).astimezone().replace(hour=ke["von"])
    bis = von + timedelta(hours=ke["bis"] - ke["von"])
    if tag == jetzt.date():
        von = max(von, jetzt.replace(second=0, microsecond=0))
    belegt = sorted((max(x["start"], von), min(x["ende"], bis)) for x in termine if not x["ganz"] and x["ende"] > von and x["start"] < bis)
    frei, pos = [], von
    for s, t in belegt:
        if s > pos:
            frei.append((pos, s))
        pos = max(pos, t)
    if bis > pos:
        frei.append((pos, bis))
    frei = [(s, t) for s, t in frei if (t - s).total_seconds() >= 1800]
    tage = {}
    for i in range(14):
        d = tag + timedelta(days=i)
        tt = termine if i == 0 else kal_termine(d, ke)[0]
        tage[d.isoformat()] = {"min": kal_belegt_min(tt, d, ke), "anzahl": len(tt),
                               "titel": [x["titel"] for x in tt][:6], "liste": [{k: x[k] for k in ("titel", "von", "bis", "ganz", "farbe", "kalender")} for x in tt][:12]}
    zeigen = lambda x: {k: x[k] for k in ("titel", "von", "bis", "ganz", "kalender", "farbe", "ort")}
    return {"frei": [{"von": s.strftime("%H:%M"), "bis": t.strftime("%H:%M"), "min": round((t - s).total_seconds() / 60)} for s, t in frei],
            "termine": [zeigen(x) for x in termine if not x["ganz"]], "ganztags": [zeigen(x) for x in termine if x["ganz"]],
            "belegtMin": tage[tag.isoformat()]["min"], "freiMin": round(sum((t - s).total_seconds() for s, t in frei) / 60),
            "tage": tage, "fehler": "; ".join(fehler), "rahmen": f"{ke['von']}–{ke['bis']} Uhr", "von": ke["von"], "bis": ke["bis"],
            "abziehen": ke["abziehen"], "anzahl": len(termine)}


def kalender_setzen(data):
    """POST /api/kalender:
    {"neu": {"name", "url"}}                  -> Kalender hinzufügen
    {"id", "an"?, "name"?, "farbe"?}          -> ändern;  {"weg": id} -> entfernen
    {"von": 8, "bis": 20, "abziehen": true}   -> Tagesrahmen
    {"url": ...}                              -> alte Form: einzigen Kalender setzen (leer = trennen)"""
    def sauber(u):
        u = str(u or "").strip()
        if u and not re.match(r"^(https?|webcal)://", u, re.I):
            raise ValueError("Bitte die Adresse im iCal-Format einfügen (beginnt mit https:// oder webcal://)")
        return re.sub(r"^webcal://", "https://", u, flags=re.I)
    with AUTOMATIK_LOCK:
        a = automatik_load()
        ke = kal_einstellung(a)
        liste = ke["liste"]
        if "url" in data and not data.get("neu"):
            u = sauber(data["url"])
            liste = [{"id": "k1", "name": "Kalender", "url": u, "farbe": KAL_FARBEN[0], "an": True}] if u else []
        if isinstance(data.get("neu"), dict):
            u = sauber(data["neu"].get("url"))
            if not u:
                raise ValueError("Adresse fehlt")
            if any(k["url"] == u for k in liste):
                raise ValueError("Dieser Kalender ist schon verbunden.")
            nid = "k" + secrets.token_hex(3)
            liste.append({"id": nid, "name": einzeilig(data["neu"].get("name"))[:40] or f"Kalender {len(liste) + 1}", "url": u,
                          "farbe": KAL_FARBEN[len(liste) % len(KAL_FARBEN)], "an": True})
        if data.get("weg"):
            liste = [k for k in liste if k["id"] != data["weg"]]
        if data.get("id"):
            for k in liste:
                if k["id"] == data["id"]:
                    if "an" in data:
                        k["an"] = bool(data["an"])
                    if data.get("name"):
                        k["name"] = einzeilig(data["name"])[:40]
                    if re.match(r"^#[0-9a-fA-F]{6}$", str(data.get("farbe") or "")):
                        k["farbe"] = data["farbe"]
        for f in ("von", "bis"):
            if data.get(f) is not None:
                ke[f] = max(0, min(24, int(data[f])))
        if ke["bis"] <= ke["von"]:
            raise ValueError("Der Tag muss später enden als er anfängt.")
        if "abziehen" in data:
            ke["abziehen"] = bool(data["abziehen"])
        if "platzhalter" in data:
            roh = data["platzhalter"] if isinstance(data["platzhalter"], list) else str(data["platzhalter"] or "").split(",")
            ke["platzhalter"] = [w for w in dict.fromkeys(einzeilig(x)[:30] for x in roh) if kal_platz_key(w)][:20]
        a["kalender"] = {"liste": liste, "von": ke["von"], "bis": ke["bis"], "abziehen": ke["abziehen"], "platzhalter": ke["platzhalter"]}
        automatik_save(a)
    KALENDER.pop("frei", None)


# „Läuft alles?“: kommen Daten vom Mac und von den Handys? Plus die nächtliche Sicherung.
def laeuft_alles():
    out, jetzt = [], datetime.now(timezone.utc)
    try:
        if MODUS != "mac":
            raise LookupError
        if zaehler_an():
            ok = time.time() - ZAEHLER["puls"] < 60 and not ZAEHLER["fehler"]
            out.append({"name": f"{pc_name()} (eigener Zähler)", "ok": ok,
                        "text": "zählt alle 5 Sekunden, ActivityWatch nicht mehr nötig" if ok else ZAEHLER["fehler"] or "zählt gerade nicht"})
            raise LookupError
        bs = aw_get("/buckets/")
        win_id = pick_buckets()[0]
        lu = bs.get(win_id, {}).get("last_updated") if win_id else None
        alter = (jetzt - parse_ts(lu)).total_seconds() / 60 if lu else None
        out.append({"name": "Mac (ActivityWatch)", "ok": alter is not None and alter < 15,
                    "text": "läuft" if alter is not None and alter < 15 else (f"letzte Daten vor {fmt(alter * 60)}" if alter is not None else "kein Fenster-Tracking gefunden")})
    except LookupError:
        pass
    except Exception:
        out.append({"name": "Mac (ActivityWatch)", "ok": False, "text": "ActivityWatch antwortet nicht"})
    for dev in handy_devices():
        ls = dev.get("letzterSync")
        alter = (jetzt - datetime.fromisoformat(ls)).total_seconds() / 60 if ls else None
        # Handys senden alle 15 Minuten, nachts oder ohne WLAN auch mal länger
        ok = alter is not None and alter < 60
        out.append({"name": dev.get("name", "Handy"), "ok": ok, "text": ("sendet" if ok else f"zuletzt vor {fmt(alter * 60)}") if alter is not None else "noch nie gesendet"})
    if (MODUS == "mac" and CFG.get("vault")) or (MODUS != "mac" and not (vault() and vault().is_dir())):   # john 05.11.: „Datenspeicher“ am Handy nur zeigen, wenn er fehlt; 23.11.: Mac ohne Obsidian zeigt keinen roten Vault
        out.append({"name": "Obsidian-Vault" if MODUS == "mac" else "Datenspeicher", "ok": bool(vault() and vault().is_dir()), "text": "erreichbar" if vault() and vault().is_dir() else "nicht gefunden"})
    s = automatik_load().get("sicherung") or {}
    if automatik_an("sicherung") and MODUS != "handy":
        gestern = (date.today() - timedelta(days=1)).isoformat()
        ok = s.get("datum", "") >= gestern
        out.append({"name": "Nacht-Sicherung", "ok": ok, "text": f"zuletzt {s['datum']} ({s.get('groesse', '')})" if s.get("datum") else "noch keine"})
    if MODUS != "mac":
        return out
    n = AW_SCHUTZ["n"] if AW_SCHUTZ["tag"] == date.today().isoformat() else 0
    if not zaehler_an() and sys.platform == "darwin":
        out.append({"name": "ActivityWatch-Schutz", "ok": not AW_SCHUTZ["fehler"],
                    "text": AW_SCHUTZ["fehler"] or ("startet ActivityWatch wieder, falls es beendet wird" + (f" (heute {n}× neu gestartet)" if n else ""))})
    for g, d in sorted((SPERRE_LOG.get("dns") or {}).items()):
        gut = "cleanbrowsing" in d or "family" in d or "adult" in d or "nextdns" in d
        out.append({"name": f"Privates DNS ({g})", "ok": gut,
                    "text": f"Filter aktiv ({d})" if gut else (f"{d}: filtert keine Erwachsenen-Seiten" if d else
                            "aus: am Handy Einstellungen › Netzwerk › Privates DNS › adult-filter-dns.cleanbrowsing.org")})
    for g, t in sorted((SPERRE_LOG.get("aus") or {}).items()):
        if time.time() - t < 86400:
            out.append({"name": f"Fokus-Sperre ({g})", "ok": False, "text": f"wurde um {datetime.fromtimestamp(t).strftime('%H:%M')} ausgeschaltet"})
    if sys.platform in ("darwin", "win32"):
        x, m, pc = schutz_handy(None, "Mac"), mac_dns_ok(), pc_name()
        out.append({"name": f"DNS-Filter ({pc})", "ok": bool(m), "text": "an (CleanBrowsing, Erwachsenen-Seiten gesperrt)" if m else
                    ("wird geprüft …" if m is None else f"aus: Locker › Schutz › „DNS-Filter am {pc} einschalten“" +
                     (". Bis dahin sind die Browser zu." if x["macDns"] else ""))})
    if sys.platform in ("darwin", "win32"):
        puls = SPERRE_LOG.get("puls")
        ok = bool(puls) and time.time() - puls < 120 and not (sys.platform == "win32" and WIN["fehler"])
        out.append({"name": f"Sperre ({pc_name()})", "ok": ok,
                    "text": ("prüft alle 3 Sekunden" if ok else (WIN["fehler"] if sys.platform == "win32" else "") or SPERRE_LOG.get("waechterFehler") or "prüft gerade nicht, startet sich selbst neu")
                    + (f" ({SPERRE_LOG['neustarts']}× neu gestartet)" if SPERRE_LOG.get("neustarts") else "")})
    if sys.platform in ("darwin", "win32") and schutz_handy(None, "Mac")["bilder"]:
        l = BILD_KI["letzt"]
        out.append({"name": f"Bild-KI ({pc_name()})", "ok": not BILD_KI["fehler"],
                    "text": BILD_KI["fehler"] or (f"zuletzt {l['zeit']} auf {l['wo']}: neutral {l['neutral']} %, anzüglich {l['anzueglich']} %, "
                                                  f"nackt {l['nackt']} % ({l['ms']} ms)" if l and ENTWICKLER else
                                                  f"läuft, zuletzt {l['zeit']}" if l else "wartet auf eine Social-Media-Seite")})   # 10.11.: Prozente nur Entwickler
    z = schutz_pc.laeuft_zeile()
    if z:
        out.append(z)
    da = WAECHTER_PLIST.exists() or WAECHTER_PLIST_ALT.exists()
    out.append({"name": "Hintergrunddienst", "ok": da,
                "text": "startet BlockEm von selbst wieder" if da else "nicht eingerichtet: autostart-einrichten.command doppelklicken"})
    return out


WAECHTER_PLIST = Path.home() / "Library" / "LaunchAgents" / "app.fokus-cockpit.waechter.plist"
WAECHTER_PLIST_ALT = Path.home() / "Library" / "LaunchAgents" / "de.john.fokus-cockpit.waechter.plist"   # alter Name (vor 2.0)


def neustart():
    """Knopf „BlockEm neu starten“: Server beendet sich mit Code 42, start-cockpit.command startet ihn sofort wieder
    (läuft er ohne die Schleife, holt ihn der Hintergrunddienst innerhalb einer Minute zurück)."""
    import os
    time.sleep(0.5)
    os._exit(42)


SICHERUNG_DIR = DATEN / "sicherung" / "nacht"


def nacht_sicherung(erzwingen=False):
    """Einmal am Tag ab 3 Uhr (bzw. beim ersten Start danach): Cockpit-Daten und die Notizen aus „Claude Memory“ und „Projekte“ als ZIP, 14 Tage aufheben."""
    import zipfile
    a = automatik_load()
    heute = date.today().isoformat()
    if not erzwingen and (not automatik_an("sicherung", a) or (a.get("sicherung") or {}).get("datum") == heute or datetime.now().hour < 3):
        return None
    SICHERUNG_DIR.mkdir(parents=True, exist_ok=True)
    ziel = SICHERUNG_DIR / f"{heute}.zip"
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
        for n in ("automatik.json", "fokus.json", "konsistenz.json", "kategorien.json", "config.json"):
            if (DATEN / n).exists():
                z.write(DATEN / n, f"Fokus-Cockpit/{n}")
        if HANDY_DIR.is_dir():
            for f in HANDY_DIR.glob("*.json"):
                z.write(f, f"Fokus-Cockpit/handy-daten/{f.name}")
        v = vault()
        if v:
            for ordner in (mem(), CFG.get("projekteOrdner", "Projekte")):
                for f in (v / ordner).rglob("*.md") if (v / ordner).is_dir() else []:
                    z.write(f, f"Vault/{f.relative_to(v)}")
    for alt in sorted(SICHERUNG_DIR.glob("*.zip"))[:-14]:
        alt.unlink()
    with AUTOMATIK_LOCK:
        a = automatik_load()
        a["sicherung"] = {"datum": heute, "datei": str(ziel.relative_to(DATEN)), "groesse": f"{max(1, round(ziel.stat().st_size / 1024))} KB"}
        automatik_save(a)
    return ziel


# Plan gegen Wirklichkeit: echte Zeit pro Aufgabe = produktive Minuten aller Fokus-Sessions mit diesem Ziel
def echte_zeiten(nur_fokus=False):
    """Echte Zeit pro Aufgabe (Sekunden) = produktive Fokuszeit mit dieser Aufgabe + Korrektur von Hand (fokus.json "korrektur")."""
    with FOKUS_LOCK:
        st = fokus_load()
    m = {}
    for s in st.get("sessions", []):
        if s.get("aufgabe") and s.get("ergebnis"):
            k = base_text(s["aufgabe"]).lower()
            m[k] = m.get(k, 0) + s["ergebnis"].get("produktiv", 0)
    if not nur_fokus:
        for k, d in (st.get("korrektur") or {}).items():
            m[k] = max(0, m.get(k, 0) + d)
    return m


def echt_setzen(data):
    """Echte Zeit einer Aufgabe von Hand auf `minuten` setzen; gespeichert wird nur der Unterschied zur Fokuszeit."""
    text = base_text(str(data.get("aufgabe") or "")).lower()
    if not text:
        raise ValueError("Welche Aufgabe?")
    try:
        soll = max(0, min(100000, float(str(data.get("minuten")).replace(",", ".")))) * 60
    except (TypeError, ValueError):
        raise ValueError("Bitte Minuten als Zahl angeben.")
    fokus = echte_zeiten(nur_fokus=True).get(text, 0)
    with FOKUS_LOCK:
        st = fokus_load()
        korr = st.setdefault("korrektur", {})
        if abs(soll - fokus) < 1:
            korr.pop(text, None)
        else:
            korr[text] = round(soll - fokus)
        fokus_save(st)


def echt_eintragen(out):
    m = echte_zeiten()
    if not m:
        return
    for liste in ("heute", "woche", "monat", "jahr"):
        for t in out.get(liste, []):
            v = m.get(base_text(t["text"]).lower())
            if v:
                t["echt"] = v
    for t in out.get("tage", []):
        for it in t.get("items", []):
            v = m.get(base_text(it["text"]).lower())
            if v:
                it["echt"] = v
    for pr in out.get("projekte", []):
        for ph in pr.get("phasen", []):
            for g in ph.get("ziele", []):
                v = m.get(base_text(g["name"]).lower())
                if v:
                    g["echt"] = v

# ---------- Ideen und Notizen pro Projekt (eigene Notiz neben der Aufgaben-Notiz) ----------
# „<Projekt> – Ideen und Notizen.md“: „## Allgemein“ für alles ohne Phase, „## Phase: <Name>“ pro Phase (gleicher Name wie in der
# Aufgaben-Notiz), darin einfache Spiegelstriche, gern mit eingerückten Stichpunkten. Andere Überschriften lässt das Cockpit in Ruhe.
IDEEN_ENDE = " – Ideen und Notizen.md"
IDEE = re.compile(r"^[-*] (?!\[[ xX]\] )(.+)$")
ALLGEMEIN = re.compile(r"^##\s+Allgemein\b", re.I)
STOPPWOERTER = {"alle", "allen", "laufen", "läuft", "funktionen", "gefällt", "durchplanen", "phase", "fertig"}


# ---------- Lokale KI (Ollama auf dem Mac): Ideen in Stichpunkte zusammenfassen, kostenlos, nichts verlässt den Rechner ----------
OLLAMA = "http://127.0.0.1:11434"
KI_MODELL = "gemma3:4b"   # ca. 3 GB, kann gut Deutsch; ein anderes geladenes Modell geht auch („kiModell“ in config.json)
KI = {"stand": 0, "status": None, "laedt": False, "fehler": ""}


def ollama(path, body=None, timeout=2):
    req = Request(OLLAMA + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def ki_status(frisch=False):
    """Welche KI BlockEm nutzt. "modell" ist nur gesetzt, wenn eine KI bereit ist (die Oberfläche fragt genau das ab).
    Ollama-Prüfung 20 s zwischengespeichert. Schlüssel gehen nie an die Oberfläche, nur eine Maske (sk-…abcd)."""
    if MODUS == "handy":   # im Handy gibt es weder Ollama noch Claude Code, nur KI per Schlüssel
        KI["status"], KI["stand"] = {"laeuft": False, "modell": None, "namen": []}, time.time()
    if frisch or time.time() - KI["stand"] > 20:
        try:
            namen = [m["name"] for m in ollama("/api/tags", timeout=0.6).get("models", [])]
            wunsch = CFG.get("kiModell") or KI_MODELL
            modell = next((n for n in namen if n == wunsch or n.split(":")[0] == wunsch), None) or (namen[0] if namen else None)
            KI["status"] = {"laeuft": True, "modell": modell, "namen": namen}
        except Exception:
            KI["status"] = {"laeuft": False, "modell": None, "namen": []}
        KI["stand"] = time.time()
    st = KI["status"]
    out = {"laeuft": st["laeuft"], "laedt": KI["laedt"], "fehler": KI["fehler"], "empfohlen": CFG.get("kiModell") or KI_MODELL}
    pfad = claude_pfad() if MODUS != "handy" else None
    k = ki_konfig()
    wahl = ki_wahl(k)
    eigene_ollama = (k.get("anbieter") or {}).get("ollama", {}).get("modell")
    ollama_modell = eigene_ollama if eigene_ollama in st.get("namen", []) else st["modell"]
    bereit = {"claude": bool(pfad), "ollama": bool(ollama_modell)}
    for aid, a in KI_ANBIETER.items():
        if a["art"] in ("anthropic", "openai"):
            e = (k.get("anbieter") or {}).get(aid) or {}
            bereit[aid] = bool(e.get("schluessel") or (aid == "eigene" and e.get("url"))) and bool(e.get("modell") or a.get("modell"))
    if wahl == "auto":
        anbieter = "claude" if bereit["claude"] else "ollama" if bereit["ollama"] else next((x for x in KI_ANBIETER if bereit.get(x)), None)
    else:
        anbieter = wahl if bereit.get(wahl) else None
    liste = []
    for aid, a in KI_ANBIETER.items():
        if MODUS == "handy" and aid in ("claude", "ollama"):
            continue
        e = (k.get("anbieter") or {}).get(aid) or {}
        z = {"id": aid, "name": a["name"], "art": a["art"], "text": a["text"], "lokal": a.get("lokal", False), "bereit": bereit.get(aid, False),
             "link": a.get("link", ""), "schluesselNoetig": a["art"] in ("anthropic", "openai") and aid != "eigene"}
        if a["art"] in ("anthropic", "openai"):
            z.update(maske=ki_maske(e.get("schluessel")), modell=e.get("modell") or a.get("modell") or "", url=e.get("url", "") if aid == "eigene" else "")
        elif aid == "claude":
            z.update(pfad=pfad or "")
        else:
            z.update(modell=ollama_modell or "", modelle=st.get("namen", []), laeuft=st["laeuft"])
        liste.append(z)
    anzeige = None
    if anbieter == "claude":
        anzeige = "Claude (dein Abo)"
    elif anbieter == "ollama":
        anzeige = ollama_modell
    elif anbieter:
        e = (k.get("anbieter") or {}).get(anbieter) or {}
        anzeige = f"{KI_ANBIETER[anbieter]['kurz']} · {e.get('modell') or KI_ANBIETER[anbieter].get('modell')}"
    out.update(claude=bool(pfad), claudePfad=pfad or "", wahl=wahl, anbieter=anbieter, ollamaModell=ollama_modell, modell=anzeige,
               anbieterListe=liste, lokal=bool(anbieter and KI_ANBIETER[anbieter].get("lokal")),
               name=KI_ANBIETER[anbieter]["kurz"] if anbieter else "")
    return out


def ki_laden():
    """Empfohlenes Modell im Hintergrund herunterladen (einmalig, ca. 3 GB)."""
    if KI["laedt"]:
        return

    def lauf():
        KI["laedt"], KI["fehler"] = True, ""
        try:
            ollama("/api/pull", {"model": CFG.get("kiModell") or KI_MODELL, "stream": False}, timeout=3600)
        except Exception as e:
            KI["fehler"] = f"Laden ging nicht: {e}"
        KI["laedt"] = False
        ki_status(frisch=True)
    threading.Thread(target=lauf, daemon=True).start()


# ---------- Claude als KI im Cockpit: Claude Code auf dem Mac, angemeldet mit dem eigenen Claude-Abo ----------
# Jeder Nutzer bindet die KI ein, die er selbst nutzt; das Cockpit ruft nur das Programm „claude“ im Hintergrund auf (claude -p).
CLAUDE_ORTE = ["~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
CLAUDE_APP_MUSTER = ["~/Library/Application Support/Claude/claude-code*/**/claude", "/Applications/Claude.app/Contents/Resources/**/claude"]
CLAUDE_SUCHE = {"stand": 0, "pfad": None}     # Suche 60 s zwischenspeichern


def claude_pfad():
    if time.time() - CLAUDE_SUCHE["stand"] > 60:
        CLAUDE_SUCHE.update(stand=time.time(), pfad=claude_suchen())
    return CLAUDE_SUCHE["pfad"]


def claude_fuer_mac(f):
    """Nur Programme, die auf dem Mac laufen: Mach-O oder Skript, keine Linux-Version (die Claude-App bringt eine für ihre VM mit)."""
    if "claude-code-vm" in f:
        return False
    try:
        with open(f, "rb") as d:
            kopf = d.read(4)
    except OSError:
        return False
    return kopf != b"\x7fELF"


def claude_suchen():
    import os
    import shutil
    p = shutil.which("claude")
    if p:
        return p
    for o in CLAUDE_ORTE:
        o = os.path.expanduser(o)
        if os.path.isfile(o) and os.access(o, os.X_OK):
            return o
    # Claude Code, das die Claude-Desktop-App mitbringt (neueste Version zuerst)
    import glob
    funde = []
    for muster in CLAUDE_APP_MUSTER:
        funde += [f for f in glob.glob(os.path.expanduser(muster), recursive=True) if os.path.isfile(f) and os.access(f, os.X_OK) and claude_fuer_mac(f)]
    if funde:
        return max(funde, key=os.path.getmtime)
    return None


def claude_fragen(prompt, timeout=180):
    """Eine Frage an Claude Code (claude -p); -> Antworttext. Läuft in einem leeren Ordner, damit Claude keine Dateien anfasst."""
    import os
    import subprocess
    import tempfile
    pfad = claude_pfad()
    if not pfad:
        raise RuntimeError("Claude Code ist auf diesem Mac nicht installiert.")
    env = dict(os.environ)
    env["PATH"] = ":".join([os.path.dirname(pfad), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
    with tempfile.TemporaryDirectory(prefix="cockpit-ki-") as ordner:
        try:
            r = subprocess.run([pfad, "-p", prompt, "--output-format", "json"], cwd=ordner, env=env,
                               capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude hat zu lange gebraucht.")
        except OSError as e:
            CLAUDE_SUCHE["stand"] = 0
            raise RuntimeError(f"Claude Code ließ sich nicht starten ({e.strerror or e}). Bitte Claude Code für den Mac installieren.")
    raus = (r.stdout or "").strip()
    try:
        d = json.loads(raus)
    except ValueError:
        d = None
    if isinstance(d, dict):
        if d.get("is_error"):
            raise RuntimeError("Claude: " + str(d.get("result") or d.get("subtype") or "Fehler")[:300])
        return str(d.get("result") or "").strip()
    if r.returncode != 0:
        fehler = (r.stderr or raus or "unbekannter Fehler").strip().splitlines()
        raise RuntimeError("Claude: " + (fehler[-1] if fehler else "Fehler")[:300])
    return raus


def claude_json(prompt, timeout=180):
    """Claude fragen und das erste JSON-Objekt aus der Antwort lesen."""
    t = claude_fragen(prompt, timeout)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise RuntimeError("Claude hat kein JSON geliefert")
    return json.loads(m.group(0))


# ---------- Deine KI: jeder verbindet die KI, die er schon nutzt ----------
# Claude Code (Abo), Claude-API, ChatGPT, Gemini, OpenRouter, Mistral, Ollama oder ein eigener OpenAI-kompatibler Server (LM Studio …).
# Schlüssel liegen nur in ki-schluessel.json im App-Ordner (nur für dich lesbar) und gehen nie an die Oberfläche.
# Bewusst ohne Zusatzpakete (reines HTTP mit der Python-Standardbibliothek), damit das Cockpit auf jedem Mac ohne Installation läuft.
KI_DATEI = DATEN / "ki-schluessel.json"
KI_LOCK = threading.RLock()
KI_ANBIETER = {
    "claude": {"name": "Claude (dein Abo über Claude Code)", "kurz": "Claude", "art": "programm",
               "text": "Nutzt dein Claude-Abo, keine Extra-Kosten. Braucht Claude Code auf dem Mac.", "link": "https://claude.ai/download"},
    "anthropic": {"name": "Claude per API-Schlüssel", "kurz": "Claude API", "art": "anthropic", "url": "https://api.anthropic.com",
                  "modell": "claude-opus-5-5", "text": "Abrechnung nach Nutzung bei Anthropic.", "link": "https://console.anthropic.com/settings/keys"},
    "openai": {"name": "ChatGPT (OpenAI)", "kurz": "ChatGPT", "art": "openai", "url": "https://api.openai.com/v1", "modell": "gpt-5-mini",
               "text": "API-Schlüssel von OpenAI, Abrechnung nach Nutzung.", "link": "https://platform.openai.com/api-keys"},
    "gemini": {"name": "Google Gemini", "kurz": "Gemini", "art": "openai", "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "modell": "gemini-2.5-flash", "text": "Schlüssel aus Google AI Studio, mit kostenlosem Kontingent.", "link": "https://aistudio.google.com/apikey"},
    "openrouter": {"name": "OpenRouter (viele Modelle, ein Schlüssel)", "kurz": "OpenRouter", "art": "openai", "url": "https://openrouter.ai/api/v1",
                   "modell": "openrouter/auto", "text": "Ein Schlüssel für Claude, GPT, Gemini, Llama und mehr.", "link": "https://openrouter.ai/keys"},
    "mistral": {"name": "Mistral (Europa)", "kurz": "Mistral", "art": "openai", "url": "https://api.mistral.ai/v1", "modell": "mistral-small-latest",
                "text": "Europäischer Anbieter, Server in der EU.", "link": "https://console.mistral.ai/api-keys"},
    "ollama": {"name": "Ollama (lokal, kostenlos)", "kurz": "Ollama", "art": "ollama", "lokal": True,
               "text": "Läuft komplett auf deinem Mac, nichts verlässt den Rechner.", "link": "https://ollama.com/download"},
    "eigene": {"name": "Eigener Server (OpenAI-kompatibel)", "kurz": "Eigener Server", "art": "openai", "lokal": False,
               "text": "Zum Beispiel LM Studio (http://localhost:1234/v1), Groq oder DeepSeek: Adresse, Modell und ggf. Schlüssel eintragen.", "link": ""},
}
# Neue Claude-Modelle bekommen bei einer Ablehnung durch Sicherheitsfilter automatisch ein Ersatzmodell (serverseitig)
ANTHROPIC_FALLBACK = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


def ki_konfig():
    try:
        return json.loads(KI_DATEI.read_text("utf-8"))
    except Exception:
        return {}


def ki_konfig_speichern(k):
    import os
    tmp = KI_DATEI.with_suffix(".tmp")
    tmp.write_text(json.dumps(k, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(KI_DATEI)


def ki_wahl(k=None):
    """Gewählter Anbieter: in ki-schluessel.json, sonst die alte Wahl aus automatik.json (auto|claude|ollama)."""
    k = ki_konfig() if k is None else k
    w = k.get("aktiv") or ((automatik_load().get("ki") or {}).get("anbieter")) or "auto"
    return w if w == "auto" or w in KI_ANBIETER else "auto"


def ki_maske(sch):
    sch = str(sch or "")
    return "" if not sch else (sch[:3] + "…" + sch[-4:]) if len(sch) > 10 else "••••"


def ki_fehlertext(code, text):
    try:
        d = json.loads(text)
        msg = d.get("error", {}).get("message") if isinstance(d.get("error"), dict) else d.get("error") or d.get("message")
    except Exception:
        msg = None
    msg = str(msg or text or "").strip()[:240]
    if code in (401, 403):
        return f"Schlüssel wird nicht angenommen ({code}). Bitte prüfen oder neu erzeugen. {msg}"
    if code == 404:
        return f"Nicht gefunden ({code}): meist stimmt der Modellname nicht. Wähle ein Modell aus der Liste. {msg}"
    if code == 429:
        return f"Zu viele Anfragen oder kein Guthaben mehr beim Anbieter (429). {msg}"
    return f"Fehler {code}: {msg}"


def _ssl_modul():
    """ssl gibt es im Handy (Pyodide) nicht, dort macht die App die Verbindung. Ersatz mit SSLError, der nie auftritt."""
    try:
        import ssl
        return ssl
    except ImportError:
        class _KeinSsl:
            class SSLError(Exception):
                pass
        return _KeinSsl


def http_json(url, body=None, headers=None, timeout=60):
    """JSON per HTTP(S). Fehlen Python die Zertifikate (häufig bei Python von python.org auf dem Mac), übernimmt /usr/bin/curl."""
    ssl = _ssl_modul()
    from urllib.error import HTTPError, URLError
    h = {"Content-Type": "application/json", "User-Agent": "Fokus-Cockpit", **(headers or {})}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urlopen(Request(url, data=data, headers=h), timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except HTTPError as e:
        raise RuntimeError(ki_fehlertext(e.code, e.read().decode("utf-8", "replace")))
    except URLError as e:
        if isinstance(e.reason, ssl.SSLError) and Path("/usr/bin/curl").exists():
            return curl_json(url, data, h, timeout)
        raise RuntimeError(f"Nicht erreichbar: {e.reason}")


def curl_json(url, data, headers, timeout):
    """Ersatzweg über curl: Kopfzeilen (mit Schlüssel) in einer Datei, die nur du lesen kannst, damit sie nicht in der Prozessliste stehen."""
    import os
    import subprocess
    import tempfile
    fd, cfg = tempfile.mkstemp(prefix="cockpit-curl-")
    try:
        with os.fdopen(fd, "w") as f:
            for k, v in headers.items():
                f.write('header = "%s: %s"\n' % (k, str(v).replace('"', '\\"')))
        r = subprocess.run(["/usr/bin/curl", "-sS", "-m", str(int(timeout)), "-K", cfg, "-w", "\n%{http_code}", url]
                           + (["--data-binary", "@-"] if data is not None else []),
                           input=data or b"", capture_output=True, timeout=timeout + 5)
    finally:
        os.unlink(cfg)
    if r.returncode != 0:
        raise RuntimeError("Nicht erreichbar: " + r.stderr.decode("utf-8", "replace").strip()[:200])
    text, _, code = r.stdout.decode("utf-8", "replace").rpartition("\n")
    if not code.isdigit() or int(code) >= 400:
        raise RuntimeError(ki_fehlertext(int(code) if code.isdigit() else 0, text))
    return json.loads(text or "{}")


def ki_eintrag(aid, k=None):
    k = ki_konfig() if k is None else k
    a = KI_ANBIETER[aid]
    e = (k.get("anbieter") or {}).get(aid) or {}
    return {"schluessel": e.get("schluessel", ""), "modell": e.get("modell") or a.get("modell", ""),
            "url": (e.get("url") if aid == "eigene" else a.get("url", "")) or ""}


def ki_text(prompt, system="", timeout=180, anbieter=None):
    """Eine Anfrage an die gewählte KI -> Antworttext. system = Anleitung, prompt = Inhalt."""
    st = ki_status()
    aid = anbieter or st.get("anbieter")
    if not aid:
        raise ValueError("Keine KI verbunden. Einstellungen › Deine KI: eine KI auswählen.")
    art = KI_ANBIETER[aid]["art"]
    if art == "programm":
        return claude_fragen((system + "\n\n" if system else "") + prompt, timeout=timeout)
    if art == "ollama":
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return ollama("/api/chat", {"model": st["ollamaModell"], "stream": False, "messages": msgs}, timeout=timeout)["message"]["content"].strip()
    e = ki_eintrag(aid)
    if art == "anthropic":
        body = {"model": e["modell"], "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        h = {"x-api-key": e["schluessel"], "anthropic-version": "2023-06-01"}
        if e["modell"] in ANTHROPIC_FALLBACK:
            h["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        r = http_json(e["url"].rstrip("/") + "/v1/messages", body, h, timeout)
        if r.get("stop_reason") == "refusal":
            raise RuntimeError("Die KI hat diese Anfrage abgelehnt. Bitte anders formulieren.")
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    # OpenAI-kompatibel (OpenAI, Gemini, OpenRouter, Mistral, eigener Server)
    if not e["url"]:
        raise ValueError("Für den eigenen Server fehlt die Adresse.")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    h = {"Authorization": "Bearer " + e["schluessel"]} if e["schluessel"] else {}
    if aid == "openrouter":
        h.update({"HTTP-Referer": "https://fokus-cockpit.app", "X-Title": "Fokus-Cockpit"})
    r = http_json(e["url"].rstrip("/") + "/chat/completions", {"model": e["modell"], "messages": msgs}, h, timeout)
    try:
        return str(r["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unerwartete Antwort vom KI-Server.")


def ki_json(prompt, system="", timeout=180):
    """Wie ki_text, liest aber das erste JSON-Objekt aus der Antwort."""
    t = ki_text(prompt, system + "\nAntworte nur mit JSON, ohne Text davor oder danach.", timeout)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise RuntimeError("Die KI hat kein JSON geliefert")
    return json.loads(m.group(0))


def ki_modelle(aid):
    """Modelle, die der Anbieter mit diesem Schlüssel anbietet (für die Auswahl)."""
    a = KI_ANBIETER[aid]
    if a["art"] == "ollama":
        ki_status(frisch=True)
        return KI["status"].get("namen", [])
    if a["art"] == "programm":
        return []
    e = ki_eintrag(aid)
    if a["art"] == "anthropic":
        r = http_json(e["url"] + "/v1/models?limit=100", None, {"x-api-key": e["schluessel"], "anthropic-version": "2023-06-01"}, 20)
        return [m["id"] for m in r.get("data", []) if m.get("id")]
    h = {"Authorization": "Bearer " + e["schluessel"]} if e["schluessel"] else {}
    r = http_json(e["url"].rstrip("/") + "/models", None, h, 20)
    ids = [str(m.get("id", "")).replace("models/", "") for m in r.get("data", []) if m.get("id")]
    if aid == "openai":
        ids = [i for i in ids if i.startswith(("gpt", "o1", "o3", "o4", "o5", "chatgpt"))] or ids
    return sorted(ids)[:300]


def ki_einstellen(data):
    """POST /api/ki:
    {"anbieter": "auto"|<id>}                         -> diese KI benutzen
    {"speichern": {"id", "schluessel"?, "modell"?, "url"?}} -> Zugang speichern (leerer Schlüssel = alten behalten)
    {"entfernen": <id>}                               -> Zugang löschen
    {"modelle": <id>}                                 -> {"modelle": [...]}
    {"test": true}                                    -> kurze Antwort der gewählten KI
    {"login": true}                                   -> Terminal mit Claude Code zum Anmelden öffnen"""
    out = {}
    with KI_LOCK:
        k = ki_konfig()
        geaendert = False
        if data.get("anbieter") == "auto" or data.get("anbieter") in KI_ANBIETER:
            k["aktiv"] = data["anbieter"]
            geaendert = True
        sp = data.get("speichern")
        if isinstance(sp, dict) and sp.get("id") in KI_ANBIETER and KI_ANBIETER[sp["id"]]["art"] in ("anthropic", "openai", "ollama"):
            e = k.setdefault("anbieter", {}).setdefault(sp["id"], {})
            sch = "".join(str(sp.get("schluessel") or "").split())
            if sch:
                if len(sch) > 400 or not re.match(r"^[\w.\-:/+=]+$", sch):
                    raise ValueError("Der Schlüssel sieht nicht richtig aus (bitte nur den Schlüssel einfügen).")
                e["schluessel"] = sch
            if sp.get("modell") is not None:
                m = str(sp["modell"]).strip()[:120]
                if m and not re.match(r"^[\w.\-:/@]+$", m):
                    raise ValueError("Ungültiger Modellname")
                e["modell"] = m
            if sp["id"] == "eigene" and sp.get("url") is not None:
                u = str(sp["url"]).strip().rstrip("/")
                if u and not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w.\-/]*)?$", u):
                    raise ValueError("Bitte eine Adresse wie http://localhost:1234/v1 eintragen.")
                e["url"] = u
            if not data.get("anbieter"):
                k["aktiv"] = sp["id"]
            geaendert = True
        if data.get("entfernen") in KI_ANBIETER:
            (k.get("anbieter") or {}).pop(data["entfernen"], None)
            if k.get("aktiv") == data["entfernen"]:
                k["aktiv"] = "auto"
            geaendert = True
        if geaendert:
            ki_konfig_speichern(k)
            KI["stand"] = 0
    if data.get("modelle") in KI_ANBIETER:
        out["modelle"] = ki_modelle(data["modelle"])
    if data.get("login"):
        # Terminal öffnen und Claude Code starten: beim ersten Start fragt es selbst nach der Anmeldung (/login)
        import shlex
        import subprocess
        pfad = claude_pfad()
        if not pfad:
            raise ValueError("Claude Code ist auf diesem Mac nicht installiert.")
        befehl = "cd ~ && " + shlex.quote(pfad)
        skript = 'tell application "Terminal" to activate\ntell application "Terminal" to do script ' + json.dumps(befehl)
        subprocess.run(["osascript", "-e", skript], timeout=15, check=False)
        out["login"] = True
    if data.get("test"):
        st = ki_status(frisch=True)
        if not st.get("anbieter"):
            raise ValueError("Keine KI verbunden: wähle unten eine aus und trag ggf. den Schlüssel ein.")
        t0 = time.time()
        out["antwort"] = ki_text(f"Begrüße {nutzer_name() or 'mich'} in einem kurzen, freundlichen Satz auf Deutsch und sag, dass du jetzt in BlockEm eingebaut bist.", timeout=120)
        out["sekunden"] = round(time.time() - t0, 1)
    return out


# ---------- „Frag Claude“ im Cockpit: Frage + Johns aktuelle Cockpit-Daten an die KI, Verlauf in „Claude Memory/Cockpit-Chat.md“ ----------
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def ki_kontext(ort=""):
    """Johns Plan als kompakter Text: heute, Woche, Monat, Projekte mit Phasen und offenen Aufgaben, Bildschirmzeit heute."""
    out = plan()
    jetzt = datetime.now()
    z = [f"Jetzt: {WOCHENTAGE[jetzt.weekday()]}, {jetzt.strftime('%d.%m.%Y %H:%M')}"]
    try:
        std, aufg = tag_zeit(out)
        z.append(f"Zeit für heute: {std} Std, {aufg} Aufgaben")
    except Exception:
        pass
    try:
        su = day_data(date.today())["summe"]
        z.append(f"Bildschirmzeit heute: produktiv {round(su.get('produktiv', 0) / 60)} Min, Ablenkung {round(su.get('prokrastination', 0) / 60)} Min")
    except Exception:
        pass
    box = lambda t: f"- [{'x' if t.get('done') else ' '}] {t.get('text')}" + (f" ({t['projekt']})" if t.get("projekt") else "") + (f", {t['std']} Std" if t.get("std") else "")
    for k, titel in (("heute", "Heute"), ("woche", "Diese Woche"), ("monat", "Dieser Monat"), ("jahr", "Jahr")):
        if out.get(k):
            z.append(f"\n## {titel}")
            z += [box(t) for t in out[k][:25]]
    teile = [x for x in str(ort or "").split("/") if x]
    fokus_pr = teile[1] if len(teile) > 1 and teile[0] == "projekte" else ""
    z.append("\n## Projekte")
    for pr in out.get("projekte", []):
        genau = pr["name"] == fokus_pr
        ez = pr.get("endziel") or {}
        ez = ez.get("text", "") if isinstance(ez, dict) else str(ez)
        z.append(f"\n### {pr['name']} ({'aktiv' if pr.get('aktiv') else pr.get('status') or 'ruht'})" + (f" – Endziel: {ez}" if ez else ""))
        for ph in pr.get("phasen") or []:
            if ph.get("fertig") and not genau:
                continue
            offen = [g for g in ph.get("ziele", []) if not g.get("fertig")]
            z.append(f"Phase: {ph['name']}" + (" (aktiv)" if ph.get("aktiv") else "") + (" ✓" if ph.get("fertig") else "") +
                     (f", bis {ph['frist']}" if ph.get("frist") else "") + f", {len(offen)} offen")
            grenze = 40 if (genau or ph.get("aktiv")) else 4
            for g in offen[:grenze]:
                extra = ", ".join(x for x in [f"{g['aufwand']} Std" if g.get("aufwand") else "", f"!{g['prio']}" if g.get("prio") else "",
                                               f"bis {g['frist']}" if g.get("frist") else "", g.get("abschnitt") or ""] if x)
                z.append(f"  - {g['name']}" + (f" ({extra})" if extra else ""))
            if len(offen) > grenze:
                z.append(f"  … und {len(offen) - grenze} weitere")
    text = "\n".join(z)
    return text[:16000]


def chat_merken(frage, antwort):
    v = vault()
    if not v:
        return
    p = v / mem("Cockpit-Chat.md")
    try:
        alt = p.read_text("utf-8") if p.exists() else "---\ntyp: automatik\n---\n# Cockpit-Chat\n\nFragen an Claude in BlockEm (neueste unten). Claude im Projekt-Chat kann hier nachlesen.\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(alt.rstrip("\n") + f"\n\n## {datetime.now().strftime('%d.%m.%Y %H:%M')}\n**{nutzer_name() or 'Ich'}:** {frage}\n\n**{ki_status().get('name') or 'KI'}:** {antwort}\n", "utf-8")
    except OSError:
        pass


KI_AKTIONEN = """
Wenn ein konkreter Schritt in BlockEm hilft, hänge ganz am Ende GENAU EINEN Block an (höchstens 5 Einträge), sonst keinen:
```aktionen
[{"typ": "heute", "text": "Aufgabe", "projekt": "Projektname oder leer"},
 {"typ": "aufgabe", "text": "neue Aufgabe", "projekt": "Projektname", "phase": "Phasenname oder leer", "std": 2, "prio": "muss|soll|kann"},
 {"typ": "habit", "text": "Habit-Name"},
 {"typ": "fokus", "text": "Aufgabe", "projekt": "Projektname oder leer", "minuten": 50}]
```
typ "heute" plant eine Aufgabe für heute ein, "aufgabe" legt sie in einem Projekt an, "habit" legt einen Habit an, "fokus" startet eine Fokus-Session.
Nutze nur Projekte und Phasen, die es in den Daten gibt. Der Nutzer bestätigt jede Aktion selbst mit einem Tipp."""


def ki_aktionen_lesen(antwort):
    """Antwort -> (Text ohne Aktionen-Block, [geprüfte Aktionen])."""
    m = re.search(r"```aktionen\s*(\[.*?\])\s*```", antwort, re.S)
    if not m:
        return antwort, []
    text = (antwort[:m.start()] + antwort[m.end():]).strip()
    try:
        roh = json.loads(m.group(1))
    except ValueError:
        return text, []
    projekte = project_files()
    out = []
    for x in roh if isinstance(roh, list) else []:
        if not isinstance(x, dict) or x.get("typ") not in ("heute", "aufgabe", "habit", "fokus"):
            continue
        t = einzeilig(x.get("text"))[:200]
        if not t:
            continue
        pr = x.get("projekt") if x.get("projekt") in projekte else None
        if x["typ"] == "aufgabe" and not pr:
            continue
        a = {"typ": x["typ"], "text": t, "projekt": pr}
        if x["typ"] == "aufgabe":
            a.update(phase=einzeilig(x.get("phase"))[:120] or None, std=zahl(x.get("std")) if x.get("std") else None,
                     prio=x.get("prio") if x.get("prio") in ("muss", "soll", "kann") else None)
        if x["typ"] == "fokus":
            try:
                a["minuten"] = max(5, min(240, int(x.get("minuten") or 50)))
            except (TypeError, ValueError):
                a["minuten"] = 50
        out.append(a)
    return text, out[:5]


def ki_aktion(data):
    """POST /api/ki-aktion {typ, text, projekt, phase, std, prio, minuten}: einen Vorschlag der KI übernehmen (nach Tipp des Nutzers)."""
    typ, text, pr = data.get("typ"), einzeilig(data.get("text"))[:200], data.get("projekt") or None
    if not text:
        raise ValueError("Leerer Vorschlag")
    if pr and pr not in project_files():
        raise ValueError("Projekt nicht gefunden")
    if typ == "heute":
        add("heute", text, pr)
    elif typ == "aufgabe":
        if not pr:
            raise ValueError("Projekt fehlt")
        zeile = text + (dauer(std=zahl(data["std"])) if data.get("std") else "") + (f" !{data['prio']}" if data.get("prio") in ("muss", "soll", "kann") else "")
        phasen = [p["name"] for p in parse_project(project_files()[pr])]
        add("ziel", zeile, pr, data.get("phase") if data.get("phase") in phasen else None)
    elif typ == "habit":
        habit({"aktion": "add", "name": text})
    elif typ == "fokus":
        fokus_action({"aktion": "start", "projekt": pr, "aufgabe": text, "minuten": data.get("minuten") or 50})
    else:
        raise ValueError("Unbekannte Aktion")


def ki_frage(data):
    """POST /api/frage {frage, verlauf: [{frage, antwort}], ort} -> {antwort, aktionen}."""
    frage = " ".join(str(data.get("frage") or "").split())[:2000]
    if not frage:
        raise ValueError("Bitte eine Frage eingeben.")
    name = nutzer_name() or "der Nutzer"
    verlauf = [x for x in (data.get("verlauf") or [])[-4:] if isinstance(x, dict)]
    vorher = "\n".join(f"{name}: {str(x.get('frage'))[:600]}\nDu: {str(x.get('antwort'))[:1500]}" for x in verlauf)
    anleitung = (f"Du bist die KI in BlockEm von {name} (eine App für Ziele, Projekte, Habits, Fokus und Bildschirmzeit). "
                 "Der Text kommt oft per Spracheingabe, Versprecher sinngemäß lesen. Antworte auf Deutsch, kurz und konkret, "
                 "wie ein guter Freund und Coach: erst die Antwort, dann höchstens 3 bis 5 Stichpunkte. Nutze die echten Aufgaben, "
                 "Projekte, Phasen und Termine aus den Daten unten, nenne sie beim Namen, erfinde nichts dazu. Keine Gamification.\n" + KI_AKTIONEN)
    prompt = (f"Gerade offen in BlockEm: {data.get('ort') or 'Heute'}\n\n# Cockpit-Daten\n{ki_kontext(data.get('ort'))}\n"
              + (f"\n# Bisheriges Gespräch\n{vorher}\n" if vorher else "") + f"\n# Frage\n{frage}")
    antwort, aktionen = ki_aktionen_lesen(ki_text(prompt, anleitung, timeout=240))
    chat_merken(frage, antwort)
    return {"antwort": antwort, "aktionen": aktionen}


def ki_rueckblick(data):
    """POST /api/ki-rueckblick {art, datum} -> {antwort, aktionen}: ehrliche Auswertung der Woche/des Monats durch die gewählte KI."""
    art = "monat" if data.get("art") == "monat" else "woche"
    try:
        bezug = date.fromisoformat(str(data.get("datum") or ""))
    except ValueError:
        bezug = date.today()
    r = rueckblick(art, bezug)
    m = lambda sek: f"{round((sek or 0) / 60)} Min"
    s, f, k = r["summe"], r["fokus"], r["konsistenz"]
    z = [f"# {r['titel']} ({r['von']} bis {r['bis']}{'' if r.get('fertig') else ', läuft noch'})",
         f"Produktiv {m(s.get('produktiv'))}, Ablenkung {m(s.get('prokrastination'))}, neutral {m(s.get('neutral'))}",
         f"Fokus-Sessions: {f.get('sessions', 0)}, Fokuszeit {m(f.get('produktiv'))}, {f.get('prozent', 0)} % fokussiert, {f.get('anzahl', 0)}× abgelenkt",
         f"Consistency: {k.get('schnitt') if k.get('schnitt') is not None else '–'} % (Schwelle {k.get('schwelle')} %), {k.get('durchgezogen')} von {k.get('tage')} Tagen durchgezogen",
         "\n## Tage"] + [f"- {t['date']}: produktiv {m(t['produktiv'])}, Ablenkung {m(t['prokrastination'])}" for t in r["tage"]]
    z += ["\n## Zeit pro Projekt"] + [f"- {x['name']}: {m(x['sek'])}" for x in r["projekte"][:10]]
    z += ["\n## Habits (erledigt/fällig)"] + [f"- {x['name']}: {x['erledigt']}/{x['faellig']}" for x in r["habits"] if x.get("faellig")]
    z += ["\n## Größte Ablenkungen"] + [f"- {x['name']}: {m(x['sek'])}" for x in r["ablenkung"][:8]]
    z += [f"\n## Erledigt ({len(r['erledigt'])})"] + [f"- {t}" for t in r["erledigt"][:30]]
    name = nutzer_name() or "der Nutzer"
    anleitung = (f"Du bist die KI in BlockEm von {name}. Werte {'die Woche' if art == 'woche' else 'den Monat'} ehrlich aus, auf Deutsch, "
                 "wie ein guter Coach: nicht schönreden, nicht schimpfen, keine Gamification. Aufbau:\n"
                 "1. Ein Satz Fazit.\n2. Was gut lief (2 bis 3 Stichpunkte, mit echten Zahlen und Namen).\n"
                 "3. Wo Zeit verloren ging und warum vermutlich (2 bis 3 Stichpunkte).\n"
                 "4. Drei konkrete Regeln oder Änderungen für die nächste Woche (z. B. eine Sperrzeit für eine bestimmte App, ein fester Fokus-Block, "
                 "ein Habit), so formuliert, dass man sie sofort umsetzen kann.\nNur Fakten aus den Daten, nichts erfinden.\n" + KI_AKTIONEN)
    prompt = "\n".join(z) + f"\n\n# Plan und Projekte\n{ki_kontext()[:9000]}"
    antwort, aktionen = ki_aktionen_lesen(ki_text(prompt, anleitung, timeout=240))
    with AUTOMATIK_LOCK:
        a = automatik_load()
        kr = a.setdefault("kiRueckblick", {})
        kr[f"{art}:{r['von']}"] = {"antwort": antwort, "aktionen": aktionen, "zeit": datetime.now().strftime("%d.%m. %H:%M")}
        for alt in sorted(kr)[:-12]:
            kr.pop(alt, None)
        automatik_save(a)
    chat_merken(f"Rückblick {r['titel']}", antwort)
    return {"antwort": antwort, "aktionen": aktionen}


# ---------- Import: Projekte, Aufgaben und Habits aus anderen Apps (über einen Prompt für ChatGPT/Claude oder die eigene KI) ----------
IMPORT_FORMAT = """{
  "projekte": [
    {"name": "Projektname", "endziel": "Was am Ende stehen soll", "frist": "31.12.2026",
     "fertigWenn": ["woran man merkt, dass das Projekt fertig ist"],
     "phasen": [
       {"name": "Name der Phase", "ziel": "Ziel der Phase", "fertigWenn": "woran man merkt, dass die Phase fertig ist", "frist": "",
        "aufgaben": [{"text": "konkrete Aufgabe", "std": 2, "prio": "muss", "frist": "", "done": false}]}
     ]}
  ],
  "heute": [{"text": "Aufgabe für heute", "projekt": "Projektname oder leer"}],
  "woche": [{"text": "Ziel für diese Woche", "projekt": "Projektname oder leer"}],
  "monat": [{"text": "Ziel für diesen Monat", "projekt": "Projektname oder leer"}],
  "jahr": [{"text": "großes Ziel für dieses Jahr", "projekt": "Projektname oder leer"}],
  "habits": [{"name": "Gewohnheit", "rhythmus": "täglich oder z. B. Mo, Mi, Fr oder 3x pro Woche"}]
}"""

IMPORT_REGELN = """So ordnest du ein:
- Jedes Projekt bekommt ein Projektziel (endziel), dann Phasen in sinnvoller Reihenfolge (große Etappen, 2 bis 6 Stück), darin konkrete Aufgaben in Arbeitsreihenfolge.
- std = geschätzte Stunden pro Aufgabe (Zahl), prio = muss, soll oder kann. Erledigtes mit "done": true. Fristen als TT.MM.JJJJ.
- Nichts erfinden: Was du nicht weißt, bleibt leer ("" oder []). Die Namen in meinen Worten lassen.
- Nur Projekte, an denen ich wirklich arbeite oder arbeiten will.
- Habits = alles, was ich regelmäßig tun will (Sport, Lesen, Beten, früh aufstehen …), mit Rhythmus.
- heute/woche/monat/jahr = Ziele für diesen Zeitraum, die gerade anstehen (auch ohne Projekt)."""


def import_prompt():
    return ("Ich ziehe mit meiner Planung in eine neue App um (BlockEm). Sammle alles, was du über meine Projekte, Ziele, "
            "Aufgaben, Gewohnheiten (Habits) und Ziele für Woche, Monat und Jahr weißt: aus unseren Gesprächen, deinen Erinnerungen über mich und den Dateien oder Texten, "
            "die ich dir dazu gebe (z. B. ein Export aus Notion, Todoist, Trello oder meine Notizen).\n\n" + IMPORT_REGELN +
            "\n\nAntworte NUR mit einem einzigen JSON-Block in genau diesem Format:\n```json\n" + IMPORT_FORMAT + "\n```")


def import_lesen(text):
    """JSON aus der eingefügten Antwort holen (auch in ```json … ``` oder mit Text drumherum) und säubern."""
    t = str(text or "")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S) or re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1) if m.lastindex else m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    zahl = lambda x: (float(str(x).replace(",", ".")) if str(x or "").strip() else None)
    def aufgabe(a):
        if isinstance(a, str):
            a = {"text": a}
        if not isinstance(a, dict) or not einzeilig(a.get("text")):
            return None
        try:
            std = zahl(a.get("std"))
        except ValueError:
            std = None
        pr = einzeilig(a.get("prio")).lower()
        return {"text": einzeilig(a["text"])[:200], "std": std if std and 0 < std <= 200 else None,
                "prio": pr if pr in ("muss", "soll", "kann") else "", "frist": einzeilig(a.get("frist"))[:20], "done": bool(a.get("done"))}
    out = {"projekte": [], "heute": [], "woche": [], "monat": [], "jahr": [], "habits": []}
    for p in d.get("projekte") or []:
        if not isinstance(p, dict):
            continue
        name = " ".join(str(p.get("name") or "").split()).replace("[[", "").replace("]]", "")
        name = re.sub(r"[/\\:#|^]", "-", name).strip(" .")[:80]
        if not name:
            continue
        phasen = []
        for ph in p.get("phasen") or []:
            if isinstance(ph, dict) and einzeilig(ph.get("name")):
                phasen.append({"name": einzeilig(ph["name"])[:80], "ziel": einzeilig(ph.get("ziel"))[:300],
                               "fertigWenn": einzeilig(ph.get("fertigWenn") if not isinstance(ph.get("fertigWenn"), list) else "; ".join(map(str, ph["fertigWenn"])))[:300],
                               "frist": einzeilig(ph.get("frist"))[:20], "aufgaben": [x for x in map(aufgabe, ph.get("aufgaben") or []) if x][:200]})
        fw = p.get("fertigWenn") or []
        out["projekte"].append({"name": name, "endziel": einzeilig(p.get("endziel"))[:300], "frist": einzeilig(p.get("frist"))[:20],
                                "fertigWenn": [einzeilig(x)[:200] for x in (fw if isinstance(fw, list) else [fw]) if einzeilig(x)][:8],
                                "phasen": phasen[:12]})
    for k in ("heute", "woche", "monat", "jahr"):
        for x in d.get(k) or []:
            x = {"text": x} if isinstance(x, str) else x
            if isinstance(x, dict) and einzeilig(x.get("text")):
                out[k].append({"text": einzeilig(x["text"])[:200], "projekt": einzeilig(x.get("projekt"))[:80]})
    for x in d.get("habits") or []:
        x = {"name": x} if isinstance(x, str) else x
        if isinstance(x, dict) and einzeilig(x.get("name")):
            out["habits"].append({"name": einzeilig(x["name"])[:80], "rhythmus": einzeilig(x.get("rhythmus"))[:40]})
    for k in out:
        out[k] = out[k][:30]
    return out


def importieren(data):
    """POST /api/import: {prompt: true} -> {prompt}; {text, pruefen: true} -> {vorschau}; {text} -> legt alles an -> {ergebnis}.
    Ist der Text kein JSON und eine KI verbunden, wandelt die KI ihn erst ins Format um."""
    if data.get("prompt"):
        return {"prompt": import_prompt()}
    text = str(data.get("text") or "")[:400000]
    d = import_lesen(text)
    mit_ki = False
    if d is None:
        if not text.strip():
            raise ValueError("Bitte zuerst die Antwort von ChatGPT oder Claude einfügen.")
        if not ki_status().get("anbieter"):
            raise ValueError("Darin steht kein Import-Block. Kopiere die ganze Antwort der KI (mit dem JSON), oder verbinde unter Einstellungen eine KI, dann geht auch normaler Text.")
        roh = ki_text("Hier sind meine Notizen bzw. ein Export aus einer anderen App:\n\n" + text[:60000],
                      "Du wandelst Planungs-Daten für BlockEm um.\n" + IMPORT_REGELN +
                      "\nAntworte NUR mit JSON in genau diesem Format:\n" + IMPORT_FORMAT, timeout=300)
        d, mit_ki = import_lesen(roh), True
        if d is None:
            raise RuntimeError("Die KI hat kein gültiges Format geliefert. Bitte nochmal versuchen.")
    vorhanden = {k.lower() for k in project_files()}
    for p in d["projekte"]:
        p["gibtEs"] = p["name"].lower() in vorhanden
    if data.get("pruefen"):
        return {"vorschau": d, "mitKi": mit_ki}
    if not vault():
        raise ValueError("Erst einen Datenordner einrichten (Einstellungen › Deine Daten).")
    erg = {"projekte": [], "uebersprungen": [], "aufgaben": 0, "heute": 0, "woche": 0, "monat": 0, "jahr": 0, "habits": 0}
    for p in d["projekte"]:
        if p["gibtEs"]:
            erg["uebersprungen"].append(p["name"])
            continue
        with VAULT_LOCK:
            neues_projekt(p["name"])
        if p["phasen"] or p["endziel"]:
            offen = [i for i, ph in enumerate(p["phasen"]) if not all(a["done"] for a in ph["aufgaben"]) or not ph["aufgaben"]]
            projektplan_setzen({"projekt": p["name"], "endziel": p["endziel"], "frist": p["frist"], "fertigWenn": p["fertigWenn"],
                                "phasen": p["phasen"], "aktiv": offen[0] if offen else 0})
        erg["projekte"].append(p["name"])
        erg["aufgaben"] += sum(len(ph["aufgaben"]) for ph in p["phasen"])
    namen = {k.lower(): k for k in project_files()}
    for k in ("heute", "woche", "monat", "jahr"):
        for x in d[k]:
            add(k, x["text"], namen.get(x["projekt"].lower()) if x["projekt"] else None)
            erg[k] += 1
    for h in d["habits"]:
        habit({"aktion": "add", "name": h["name"] + (f" ({h['rhythmus']})" if h["rhythmus"] and h["rhythmus"].lower() not in ("täglich", "jeden tag") else "")})
        erg["habits"] += 1
    return {"ergebnis": erg}


def ki_zusammenfassen(projekt, text, phasen):
    """Idee (oft Spracheingabe) -> {"titel", "stichpunkte", "phase"} über die gewählte KI."""
    st = ki_status()
    if not st["modell"]:
        raise RuntimeError("KI nicht bereit")
    system = ("Du hilfst, Ideen für ein Projekt zu notieren. Der Text ist oft per Spracheingabe diktiert: "
              "Füllwörter (ähm, genau, quasi, also) und Versprecher weglassen, den Sinn behalten. Antworte nur mit JSON:\n"
              "- titel: die Idee in einem kurzen Satz (höchstens 12 Wörter), auf Deutsch, in den Worten des Nutzers.\n"
              "- stichpunkte: 0 bis 5 kurze Stichpunkte mit dem, was umgesetzt werden soll. Leer lassen, wenn der Titel schon alles sagt.\n"
              "- phase: der exakte Name der passenden Phase aus der Liste, oder leer, wenn keine eindeutig passt.\n"
              "Nur Inhalt aus dem Text verwenden, nichts dazuerfinden.\n"
              'Format: {"titel": "...", "stichpunkte": ["..."], "phase": "..."}')
    user = f"Projekt: {projekt}\nPhasen:\n" + "\n".join("- " + p for p in phasen) + f"\n\nIdee:\n{text}"
    if st.get("anbieter") == "ollama":
        schema = {"type": "object", "additionalProperties": False, "required": ["titel", "stichpunkte", "phase"],
                  "properties": {"titel": {"type": "string"}, "stichpunkte": {"type": "array", "items": {"type": "string"}},
                                 "phase": {"type": "string", "enum": phasen + [""]}}}
        r = ollama("/api/chat", {"model": st["ollamaModell"], "stream": False, "format": schema, "options": {"temperature": 0.2},
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout=180)
        out = json.loads(r["message"]["content"])
    else:
        out = ki_json(user, system)
    titel = einzeilig(out.get("titel"))
    if not titel:
        raise RuntimeError("KI hat keinen Titel geliefert")
    return {"titel": titel, "stichpunkte": [einzeilig(x) for x in out.get("stichpunkte") or [] if einzeilig(x)][:6],
            "phase": out.get("phase") if out.get("phase") in phasen else ""}


def ideen_lesen(p):
    out = {"allgemein": [], "phasen": {}}
    if not p.exists():
        return out
    cur = None
    for line in p.read_text("utf-8").split("\n"):
        if line.startswith("## "):
            m = PHASE.match(line)
            cur = out["phasen"].setdefault(phase_name(m.group(1)).lower(), []) if m else out["allgemein"] if ALLGEMEIN.match(line) else None
        elif cur is not None and (m := IDEE.match(line)):
            cur.append({"text": clean(m.group(1)), "raw": m.group(1).strip(), "unter": []})
        elif cur and (m := re.match(r"^\s+[-*] (.+)$", line)):
            cur[-1]["unter"].append(clean(m.group(1)))
    return out


def ideen_datei(projekt):
    files = project_files()
    if projekt not in files:
        raise ValueError("Projekt nicht gefunden")
    p = files[projekt].with_name(projekt + IDEEN_ENDE)
    if not p.exists():
        p.write_text(f'---\ntyp: ideen\nstatus: aktiv\naktualisiert: {date.today().isoformat()}\nprojekt: "[[{projekt}]]"\n---\n'
                     f"# {projekt} – Ideen und Notizen\n\n## Allgemein\n\nProjekt: [[{projekt}]]\n", "utf-8")
    return p


def ideen_bereich(lines, phase, anlegen=False):
    """(Zeile der Überschrift, Ende) von „## Allgemein“ (phase leer) bzw. „## Phase: phase“; fehlt er, wird er angelegt."""
    want = (phase or "").strip().lower()
    for i, l in enumerate(lines):
        m = PHASE.match(l) if l.startswith("## ") else None
        if (want and m and phase_name(m.group(1)).lower() == want) or (not want and ALLGEMEIN.match(l)):
            e = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("## ") or lines[j].startswith("Projekt: [[")), len(lines))
            return i, e
    if not anlegen:
        raise ValueError("Bereich nicht gefunden")
    letzte = next((j for j in range(len(lines) - 1, -1, -1) if lines[j].strip()), 0)
    ende = letzte if lines and lines[letzte].startswith("Projekt: [[") else len(lines)
    k = next((j for j, l in enumerate(lines) if l.startswith("## ")), ende) if not want else ende
    block = ["## Allgemein" if not want else "## Phase: " + phase.strip(), ""]
    if k > 0 and lines[k - 1].strip():
        block = [""] + block
    lines[k:k] = block
    return ideen_bereich(lines, phase)


def ideen_phase_raten(projekt, text):
    """Idee ohne Phase: steckt ein Wort aus einem Phasennamen im Text (z. B. „Marketing“), gehört sie in diese Phase."""
    t = text.lower()
    for ph in parse_project(project_files()[projekt]):
        name = re.sub(r"^\d+\.\s*", "", ph["name"]).lower()
        woerter = [w for w in re.findall(r"[a-zäöüß]+", name) if len(w) >= 5 and w not in STOPPWOERTER]
        if any(re.search(r"\b" + re.escape(w[:max(5, len(w) - 2)]), t) for w in woerter):
            return ph["name"]
    return None


def idee(data):
    """aktion „add“ (phase leer + sortieren: passende Phase raten), „weg“ (raw = Zeile), „aufgabe“ (wird Aufgabe der Phase)."""
    projekt, phase, aktion = data.get("projekt"), (data.get("phase") or "").strip(), data.get("aktion") or "add"
    p = ideen_datei(projekt)
    einsortiert, ki = None, None
    unter = []
    if aktion == "add" and data.get("zusammenfassen") and einzeilig(data.get("text")):
        # vor dem Sperren: das lokale Modell braucht ein paar Sekunden
        try:
            phasen = [ph["name"] for ph in parse_project(project_files()[projekt]) if ph["name"] != "Aufgaben"]
            z = ki_zusammenfassen(projekt, " ".join(str(data["text"]).split()), phasen)
            data = {**data, "text": z["titel"]}
            unter = z["stichpunkte"]
            if not phase and data.get("sortieren") and z["phase"]:
                phase, data["sortieren"] = z["phase"], False
                einsortiert = z["phase"]
            ki = True
        except Exception as e:
            ki = f"KI ging nicht ({e}), Idee ist trotzdem gespeichert."
    with VAULT_LOCK:
        lines = p.read_text("utf-8").split("\n")
        if aktion == "add":
            text = einzeilig(data.get("text"))
            if not text:
                raise ValueError("Leere Idee")
            if not phase and data.get("sortieren"):
                phase = einsortiert = ideen_phase_raten(projekt, text) or ""
            s, e = ideen_bereich(lines, phase, anlegen=True)
            k = e
            while k > s + 1 and not lines[k - 1].strip():
                k -= 1
            lines[k:k] = ["- " + text] + ["\t- " + u for u in unter]
        else:
            s, e = ideen_bereich(lines, phase)
            raw = str(data.get("raw") or "").strip()
            i = next((j for j in range(s + 1, e) if (m := IDEE.match(lines[j])) and m.group(1).strip() == raw), None)
            if i is None:
                raise ValueError("Idee nicht gefunden")
            j = i + 1
            while j < e and lines[j][:1].isspace() and lines[j].strip():
                j += 1
            weg = lines[i:j]
            del lines[i:j]
            if aktion == "aufgabe":
                add("projekt", clean(raw), projekt, phase or None)
                if len(weg) > 1:
                    papierkorb(f"[[{p.stem}]] (Stichpunkte der Idee, die Aufgabe wurde)", weg)
            else:
                papierkorb(f"[[{p.stem}]]" + (f", Phase {phase}" if phase else ""), weg)
        head = "\n".join(lines)
        head = re.sub(r"^aktualisiert:.*$", "aktualisiert: " + date.today().isoformat(), head, count=1, flags=re.M)
        p.write_text(head, "utf-8")
    return {"einsortiert": einsortiert, "ki": ki}


def safe_note(rel_path):
    v = vault()
    p = (v / rel_path).resolve()
    if v.resolve() not in p.parents or p.suffix != ".md":
        raise ValueError("Pfad liegt nicht im Vault")
    return p


def set_boxes(path, key, done, projekt=None):
    """Setzt alle Checkboxen in path, deren Text (ohne Projekt-Link) key entspricht (optional nur mit [[projekt]])."""
    if not path.exists():
        return
    lines = path.read_text("utf-8").split("\n")
    changed = False
    for i, line in enumerate(lines):
        m = CHECKBOX.match(line)
        if m and base_text(m.group(4)) == key and (m.group(2) != " ") != done and (
                not projekt or projekt in LINK.findall(m.group(4))):
            lines[i] = m.group(1) + ("x" if done else " ") + m.group(3) + m.group(4)
            changed = True
    if changed:
        path.write_text("\n".join(lines), "utf-8")


def goal_block(lines, name):
    """(Zeile der Überschrift „### name“, Ende des Ziels) oder None.
    Eine Aufgabe als Kästchen direkt in der Phase zählt auch, samt eingerückter Unteraufgaben darunter."""
    for i, l in enumerate(lines):
        if l.startswith("### ") and clean(l[4:].replace("✓", "")).lower() == name.strip().lower():
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            return i, j
    for i, l in enumerate(lines):
        if (m := CHECKBOX.match(l)) and not l[:1].isspace() and base_text(m.group(4)) == name.strip().lower() and goal_of_line(lines, i) is None:
            j = i + 1
            while j < len(lines) and CHECKBOX.match(lines[j]) and lines[j][:1].isspace():
                j += 1
            return i, j
    return None


def sync_parent(lines, i):
    """Zeile i ist eine eingerückte Unteraufgabe: Kästchen der Aufgabe darüber = alle Unteraufgaben erledigt.
    Gibt (Zeile der Aufgabe, erledigt) zurück, wenn sich ihr Kästchen geändert hat, sonst None."""
    if not lines[i][:1].isspace() or not CHECKBOX.match(lines[i]):
        return None
    k = i
    while k > 0 and CHECKBOX.match(lines[k]) and lines[k][:1].isspace():
        k -= 1
    m = CHECKBOX.match(lines[k])
    if not m or lines[k][:1].isspace():
        return None
    j, subs = k + 1, []
    while j < len(lines) and CHECKBOX.match(lines[j]) and lines[j][:1].isspace():
        subs.append(j)
        j += 1
    done = all(CHECKBOX.match(lines[x]).group(2) != " " for x in subs)
    if (m.group(2) != " ") == done:
        return None
    lines[k] = m.group(1) + ("x" if done else " ") + m.group(3) + m.group(4)
    return k, done


def goal_of_line(lines, i):
    """Name des Ziels („### …“), unter dem Zeile i steht, sonst None."""
    for j in range(i, -1, -1):
        if lines[j].startswith("### "):
            return clean(lines[j][4:].replace("✓", ""))
        if lines[j].startswith("## ") or lines[j].startswith("# ") or ABSCHNITT.match(lines[j]):
            return None
    return None


def papierkorb(woher, zeilen):
    """Gelöschtes landet in „Claude Memory/Gelöscht.md“, damit man es zurückholen kann."""
    p = vault() / mem("Gelöscht.md")
    alt = p.read_text("utf-8") if p.exists() else "# Gelöscht\n\nHier landet, was in BlockEm gelöscht wurde. Zum Zurückholen einfach wieder in die Notiz kopieren.\n"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    p.write_text(alt.rstrip("\n") + f"\n\n## {stamp} – aus {woher}\n" + "\n".join(zeilen).strip("\n") + "\n", "utf-8")


def cockpit_eintraege_weg(projekt, namen):
    """Einträge „name – [[projekt]]“ aus der Cockpit-Notiz entfernen."""
    p = cockpit_path()
    if not p.exists():
        return
    namen = {n.strip().lower() for n in namen}
    lines = p.read_text("utf-8").split("\n")
    rest = [l for l in lines if not ((m := CHECKBOX.match(l)) and base_text(m.group(4)) in namen and projekt in LINK.findall(m.group(4)))]
    if len(rest) != len(lines):
        p.write_text("\n".join(rest), "utf-8")


def loeschen(data):
    """art „zeile“: eine Checkbox (id = Notiz:Zeile); „ziel“: Meilenstein „### name“ samt Aufgaben;
    „meilenstein“: Phase „## Phase: name“ samt allem darunter. Alles geht vorher in den Papierkorb."""
    art, files = data.get("art"), project_files()
    with VAULT_LOCK:
        if art == "zeile":
            rel_path, line = data["id"].rsplit(":", 1)
            p, i = safe_note(rel_path), int(line)
            lines = p.read_text("utf-8").split("\n")
            m = CHECKBOX.match(lines[i]) if i < len(lines) else None
            if not m or (data.get("text") and data["text"].strip().lower() not in (base_text(m.group(4)), clean(m.group(4)).lower())):
                raise ValueError("Die Notiz hat sich geändert, bitte Seite neu laden")
            papierkorb(rel_path, [lines[i]])
            del lines[i]
            p.write_text("\n".join(lines), "utf-8")
            return
        projekt, name = data.get("projekt"), (data.get("name") or "").strip()
        if projekt not in files:
            raise ValueError("Projekt nicht gefunden")
        f = files[projekt]
        lines = f.read_text("utf-8").split("\n")
        if art == "ziel":
            g = goal_block(lines, name)
            if not g:
                raise ValueError(f"Meilenstein nicht gefunden: {name}")
            i, j = g
            weg = [name]
        elif art == "meilenstein":
            i = next((k for k, l in enumerate(lines) if (m := PHASE.match(l)) and phase_name(m.group(1)).lower() == name.lower()), None)
            if i is None:
                raise ValueError(f"Phase nicht gefunden: {name}")
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## ") and not lines[j].startswith("Projekt: [["):
                j += 1
            weg = [name] + [clean(l[4:].replace("✓", "")) for l in lines[i:j] if l.startswith("### ")]
        elif art == "abschnitt":
            i, j = abschnitt_range(lines, data.get("phase"), name)
            weg = [clean(l[4:].replace("✓", "")) for l in lines[i:j] if l.startswith("### ")] + \
                  [base_text(m.group(4)) for l in lines[i:j] if (m := CHECKBOX.match(l)) and not l[:1].isspace()]
        else:
            raise ValueError("Unbekannte Art")
        papierkorb(rel(f), lines[i:j])   # Leerzeilen am Ende fallen mit weg
        del lines[i:j]
        f.write_text("\n".join(lines), "utf-8")
        cockpit_eintraege_weg(projekt, weg)


def toggle(item_id, done, text=None):
    rel_path, line = item_id.rsplit(":", 1)
    p = safe_note(rel_path)
    files = project_files()
    with VAULT_LOCK:
        lines = p.read_text("utf-8").split("\n")
        m = CHECKBOX.match(lines[int(line)]) if int(line) < len(lines) else None
        if not m or (text and text.strip().lower() not in (base_text(m.group(4)), clean(m.group(4)).lower())):
            raise ValueError("Die Notiz hat sich geändert, bitte Seite neu laden")
        lines[int(line)] = m.group(1) + ("x" if done else " ") + m.group(3) + m.group(4)
        eltern = sync_parent(lines, int(line))   # Unteraufgabe -> Aufgabe darüber mitziehen
        p.write_text("\n".join(lines), "utf-8")
        if eltern and (name := next((n for n, f in files.items() if f.resolve() == p), None)):
            set_boxes(cockpit_path(), base_text(CHECKBOX.match(lines[eltern[0]]).group(4)), eltern[1], projekt=name)
        key = base_text(m.group(4))
        projekt = next((l for l in LINK.findall(m.group(4)) if l in files), None)
        if p == cockpit_path().resolve() and projekt:
            # Ziel im Cockpit erledigt -> alle Schritte des Ziels in der Projekt-Notiz abhaken
            pl = files[projekt].read_text("utf-8").split("\n")
            if (g := goal_block(pl, clean(TAIL.sub("", m.group(4))))):
                steps = [k for k in range(*g) if CHECKBOX.match(pl[k])]
                if done:
                    # Ziel erreicht -> alle Schritte abhaken
                    for k in steps:
                        b = CHECKBOX.match(pl[k])
                        pl[k] = b.group(1) + "x" + b.group(3) + b.group(4)
                elif steps and all(CHECKBOX.match(pl[k]).group(2) != " " for k in steps):
                    # wieder offen -> letzter Schritt offen, sonst gilt das Ziel gleich wieder als erreicht
                    b = CHECKBOX.match(pl[steps[-1]])
                    pl[steps[-1]] = b.group(1) + " " + b.group(3) + b.group(4)
                    sync_parent(pl, steps[-1])
                files[projekt].write_text("\n".join(pl), "utf-8")
                set_boxes(p, key, done, projekt=projekt)   # dasselbe Ziel in Woche/Wochenplan
                return
        if not done and p in [f.resolve() for f in files.values()] and (ziel := goal_of_line(lines, int(line))):
            # Schritt wieder offen -> das Ziel im Cockpit ist auch nicht mehr fertig
            name = next(n for n, f in files.items() if f.resolve() == p)
            set_boxes(cockpit_path(), base_text(ziel), False, projekt=name)
        # dieselbe Aufgabe im Cockpit bzw. in der Projekt-Notiz mit abhaken
        for other in [cockpit_path(), *files.values()]:
            if other.resolve() != p:
                set_boxes(other, key, done)


def task_line(text, projekt, files):
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("Leerer Text")
    if projekt and projekt not in files:
        raise ValueError(f"Projekt nicht gefunden: {projekt}")
    return f"- [ ] {text}" + (f" – [[{projekt}]]" if projekt else "")


def phase_range(lines, phase):
    """(Zeile von „## Phase: phase“, Ende der Phase)."""
    want = (phase or "").strip().lower()
    i = next((i for i, l in enumerate(lines) if (m := PHASE.match(l)) and phase_name(m.group(1)).lower() == want), None)
    if i is None:
        raise ValueError(f"Phase nicht gefunden: {phase}")
    j = i + 1
    while j < len(lines) and not lines[j].startswith(("## ", "Projekt: [[")):
        j += 1
    return i, j


def abschnitt_range(lines, phase, name):
    """(Zeile von „#### Abschnitt: name“ in der Phase, Ende des Abschnitts)."""
    i, j = phase_range(lines, phase)
    heads = [x for x in range(i + 1, j) if ABSCHNITT.match(lines[x])]
    s = next((x for x in heads if clean(ABSCHNITT.match(lines[x]).group(1)).lower() == (name or "").strip().lower()), None)
    if s is None:
        raise ValueError(f"Abschnitt nicht gefunden: {name}")
    return s, next((x for x in heads if x > s), j)


def add_abschnitt(p, phase, name):
    """Neuer Abschnitt „#### Abschnitt: name“ am Ende der Phase. Der erste Abschnitt einer Phase nimmt die Aufgaben auf, die schon drinstehen."""
    lines = p.read_text("utf-8").split("\n")
    i, j = phase_range(lines, phase)
    heads = [x for x in range(i + 1, j) if ABSCHNITT.match(lines[x])]
    if any(clean(ABSCHNITT.match(lines[x]).group(1)).lower() == name.strip().lower() for x in heads):
        return
    k = None if heads else next((x for x in range(i + 1, j) if CHECKBOX.match(lines[x]) or lines[x].startswith("### ")), None)
    if k is not None:
        lines[k:k] = [f"#### Abschnitt: {name}", ""]
    else:
        k = j
        while k > i + 1 and not lines[k - 1].strip():
            k -= 1
        lines[k:k] = ["", f"#### Abschnitt: {name}", ""]
    p.write_text("\n".join(lines), "utf-8")


def add_goal(p, name, phase=None, abschnitt=None):
    """Neues Ziel „### name“ ans Ende der (aktiven) Phase; Notizen ohne Phasen bekommen eine Checkbox.
    Hat die Phase Abschnitte, kommt es in den gewünschten Abschnitt (sonst den ersten offenen)."""
    lines = p.read_text("utf-8").split("\n")
    if in_project(p, name):
        return
    heads = [i for i, l in enumerate(lines) if PHASE.match(l)]
    if not heads:
        boxes = [i for i, l in enumerate(lines) if CHECKBOX.match(l)]
        lines.insert(boxes[-1] + 1 if boxes else len(lines), f"- [ ] {name}")
    else:
        want = (phase or next((ph["name"] for ph in parse_project(p) if ph["aktiv"]), "")).lower()
        i = next((h for h in heads if phase_name(PHASE.match(lines[h]).group(1)).lower() == want), heads[-1])
        j = i + 1
        while j < len(lines) and not lines[j].startswith(("## ", "Projekt: [[")):
            j += 1
        heads = [x for x in range(i + 1, j) if ABSCHNITT.match(lines[x])]
        if heads:
            ziel_abs = (abschnitt or "").strip().lower()
            if not ziel_abs:
                pn = phase_name(PHASE.match(lines[i]).group(1)).lower()
                offen = [a["name"] for ph in parse_project(p) if ph["name"].lower() == pn for a in ph["abschnitte"] if not a["fertig"]]
                ziel_abs = (offen[0] if offen else clean(ABSCHNITT.match(lines[heads[-1]]).group(1))).lower()
            s = next((x for x in heads if clean(ABSCHNITT.match(lines[x]).group(1)).lower() == ziel_abs), heads[-1])
            i, j = s, next((x for x in heads if x > s), j)
        # neue Aufgabe als Kästchen direkt in der Phase (bzw. im Abschnitt), vor Aufgaben mit Unterschritten („### …“)
        k = next((x for x in range(i + 1, j) if lines[x].startswith("### ")), j)
        while k > i + 1 and not lines[k - 1].strip():
            k -= 1
        lines[k:k] = [f"- [ ] {name}"] + ([""] if k < len(lines) and lines[k].strip() else [])
    p.write_text("\n".join(lines), "utf-8")


def in_project(p, text):
    """Steht text schon als Ziel oder Schritt in der Projekt-Notiz?"""
    lines = p.read_text("utf-8").split("\n")
    return bool(goal_block(lines, text)) or any((m := box(l)) and base_text(m.group(4)) == base_text(text) for l in lines)


def add_milestone(p, name):
    """Neue Phase „## Phase: name“ am Ende (vor der Zeile „Projekt: [[…]]“)."""
    lines = p.read_text("utf-8").split("\n")
    if any((m := PHASE.match(l)) and phase_name(m.group(1)).lower() == name.lower() for l in lines):
        return
    j = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].startswith("Projekt: [[")), len(lines))
    while j > 0 and not lines[j - 1].strip():
        j -= 1
    lines[j:j] = ["", f"## Phase: {name}", ""]
    p.write_text("\n".join(lines), "utf-8")


def set_endziel(p, text):
    """„Endziel: …“ setzen oder ersetzen (steht unter der Überschrift der Notiz)."""
    lines = p.read_text("utf-8").split("\n")
    i = next((i for i, l in enumerate(lines) if ENDZIEL.match(l)), None)
    if i is not None:
        lines[i] = f"Endziel: {text}"
    else:
        h = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
        j = h + 1 if h is not None else len(lines)
        lines[j:j] = ["", f"Endziel: {text}"]
    p.write_text("\n".join(lines), "utf-8")


def add_step(p, ziel, text):
    lines = p.read_text("utf-8").split("\n")
    g = goal_block(lines, ziel)
    if not g:
        raise ValueError(f"Ziel nicht gefunden: {ziel}")
    if not lines[g[0]].startswith("### "):
        # einfache Aufgabe -> eingerückte Unteraufgabe darunter; eine neue offene Unteraufgabe macht die Aufgabe wieder offen
        lines.insert(g[1], f"\t- [ ] {text}")
        m = CHECKBOX.match(lines[g[0]])
        lines[g[0]] = m.group(1) + " " + m.group(3) + m.group(4)
        p.write_text("\n".join(lines), "utf-8")
        return
    boxes = [k for k in range(*g) if CHECKBOX.match(lines[k])]
    lines.insert(boxes[-1] + 1 if boxes else g[0] + 1, f"- [ ] {text}")
    p.write_text("\n".join(lines), "utf-8")


def neues_projekt(name):
    """Projekt-Ordner mit Hauptnotiz und Aufgaben-Notiz nach der Vorlage Projektplan (5 Ebenen) anlegen."""
    name = " ".join(str(name).split()).replace("[[", "").replace("]]", "")
    if not name or re.search(r"[/\\:#|^]", name) or name.startswith("."):
        raise ValueError("Ungültiger Projektname")
    if name in project_files():
        raise ValueError(f"Projekt „{name}“ gibt es schon")
    d = vault() / CFG["projekteOrdner"] / name
    d.mkdir(parents=True, exist_ok=True)
    heute = date.today().isoformat()
    main = d / f"{name}.md"
    if not main.exists():
        main.write_text(f"---\ntyp: projekt\nstatus: aktiv\nerstellt: {heute}\n---\n# {name}\n\nAufgaben: [[{name} – Aufgaben]]\n", "utf-8")
    (d / f"{name} – Aufgaben.md").write_text(
        f"---\ntyp: aufgaben\nstatus: aktiv\naktualisiert: {heute}\nprojekt: \"[[{name}]]\"\n---\n# {name} – Aufgaben\n\n"
        "Aufbau nach der [[Vorlage Projektplan]]: Ziel → Phasen → Meilensteine → Aufgaben → Zeitplanung. "
        "Wird gemeinsam mit Claude Ebene für Ebene ausgefüllt.\n\n"
        f"Projekt: [[{name}]]\n", "utf-8")


def einzeilig(t):
    """Eingabe aus dem Assistenten: eine Zeile, keine Markdown-Steuerzeichen am Anfang."""
    t = " ".join(str(t or "").split())
    return re.sub(r"^(?:[#>*-]+\s*|\[.?\]\s*)+", "", t).strip()


def dauer(std=None, minuten=None):
    """(3 Std) / (45 Min) für die Notiz."""
    try:
        if minuten:
            m = round(float(minuten))
            return f" ({m} Min)" if m < 60 else f" ({str(round(m / 60, 1)).replace('.0', '').replace('.', ',')} Std)"
        if std:
            return f" ({str(round(float(str(std).replace(',', '.')), 1)).replace('.0', '').replace('.', ',')} Std)"
    except ValueError:
        pass
    return ""


def projektplan_setzen(d):
    """Ergebnis des Planungs-Assistenten in „<Projekt> – Aufgaben.md“ schreiben und Monat/Woche/Heute in BlockEm eintragen.
    Modell: Projektziel → Phasen (Ziel der Phase, Fertig, wenn) → Umsetzung = Aufgaben direkt in der Phase (Aufwand, Priorität, Termin)
    (Woche und Heute plant John übergeordnet in BlockEm, nicht pro Projekt.)
    d: {projekt, endziel, frist, fertigWenn: [..], drin, nichtDrin, rahmen, aktiv,
        phasen: [{name, ziel, fertigWenn, frist, aufgaben: [{text, std, prio, frist, done, sub: [{text, min, done}]}]}],
        monat: [Phasen], woche: [Aufgaben], heute: [Aufgaben]}"""
    files = project_files()
    projekt = d.get("projekt")
    if projekt not in files:
        raise ValueError("Projekt nicht gefunden")
    f = files[projekt]

    def aufgabe(a, kopf=False):
        t = einzeilig(a.get("text"))
        pr = einzeilig(a.get("prio")).lower()
        fr = einzeilig(a.get("frist"))
        rest = dauer(std=a.get("std")) + (f" !{pr}" if pr in ("muss", "soll", "kann") else "") + (f" (bis {fr})" if fr else "")
        return f"### {t}{rest}" if kopf else f"- [{'x' if a.get('done') else ' '}] {t}{rest}"

    with VAULT_LOCK:
        alt = f.read_text("utf-8").split("\n")
        h1 = next((i for i, l in enumerate(alt) if l.startswith("# ")), None)
        kopf = alt[: h1 + 1] if h1 is not None else [f"# {projekt} – Aufgaben"]
        link = next((l for l in alt if l.startswith("Alter Plan und Ideen:")), None)
        out = kopf + [""]
        ez = einzeilig(d.get("endziel"))
        if ez:
            fr = einzeilig(d.get("frist"))
            out += [f"Endziel: {ez}" + (f" (bis {fr})" if fr else ""), ""]
            fw = [einzeilig(x) for x in d.get("fertigWenn") or [] if einzeilig(x)]
            if fw:
                out += ["Fertig, wenn:"] + [f"- {x}" for x in fw] + [""]
            info = [f"{k}: {einzeilig(d.get(v))}" for k, v in (("Drin", "drin"), ("Nicht drin", "nichtDrin"), ("Rahmen", "rahmen")) if einzeilig(d.get(v))]
            if info:
                out += info + [""]
        out += ["Aufbau nach der [[Vorlage Projektplan]]: Projektziel → Phasen → Umsetzung pro Phase.", ""]
        if link:
            out += [link, ""]
        phs = [ph for ph in d.get("phasen") or [] if einzeilig(ph.get("name"))]
        aktiv = int(d.get("aktiv") or 0)
        cur = (d.get("phasen") or [{}])[aktiv] if aktiv < len(d.get("phasen") or []) else {}
        erledigt = [bool(ez), bool(phs), any(einzeilig(a.get("text")) for a in cur.get("aufgaben") or [])]
        if any("## Phase: Durchplanen" in l for l in alt) or not all(erledigt):
            schritte = ["1. Projektziel festlegen", "2. Phasen festlegen", "3. Aufgaben der aktuellen Phase planen"]
            out += ["## Phase: Durchplanen" + ("" if phs else " (aktiv)"), "", "Fertig, wenn: Projektziel, Phasen und Aufgaben stehen.", "",
                    "### Projekt durchgeplant (1 Std)"] + [f"- [{'x' if e else ' '}] {t}" for e, t in zip(erledigt, schritte)] + [""]
        namen = []
        for n, ph in enumerate(d.get("phasen") or []):
            name = re.sub(r"^\d+[.)]\s*", "", einzeilig(ph.get("name")))
            if not name:
                continue
            name = f"{len(namen) + 1}. {name}"
            namen.append(name)
            fr = einzeilig(ph.get("frist"))
            out += [f"## Phase: {name}" + (" (aktiv)" if n == aktiv else "") + (f" (bis {fr})" if fr else "")] + look_zeile(ph) + [""]
            if einzeilig(ph.get("ziel")):
                out.append(f"Ziel der Phase: {einzeilig(ph['ziel'])}")
            if einzeilig(ph.get("fertigWenn")):
                out.append(f"Fertig, wenn: {einzeilig(ph['fertigWenn'])}")
            if out[-1] != "":
                out.append("")
            auf = [a for a in ph.get("aufgaben") or [] if einzeilig(a.get("text"))]
            # Aufgaben als Kästchen direkt in der Phase, in Arbeitsreihenfolge; Unteraufgaben eingerückt darunter.
            # Abschnitte („#### Abschnitt: …“) in ihrer Reihenfolge, Aufgaben ohne Abschnitt davor.
            abs_namen, abs_look = [], {}
            for x in ph.get("abschnitte") or []:
                if isinstance(x, dict) and einzeilig(x.get("name")):
                    abs_look[einzeilig(x.get("name")).lower()] = x
            for an in [einzeilig(x.get("name") if isinstance(x, dict) else x) for x in ph.get("abschnitte") or []] + [einzeilig(a.get("abschnitt")) for a in auf]:
                if an and an.lower() not in [x.lower() for x in abs_namen]:
                    abs_namen.append(an)
            gruppen = [(None, [a for a in auf if not einzeilig(a.get("abschnitt"))])] + \
                      [(an, [a for a in auf if einzeilig(a.get("abschnitt")).lower() == an.lower()]) for an in abs_namen]
            for an, gruppe in gruppen:
                if an:
                    out += [f"#### Abschnitt: {an}"] + look_zeile(abs_look.get(an.lower())) + [""]
                for a in gruppe:
                    out.append(aufgabe(a))
                    out += [f"\t- [{'x' if u.get('done') else ' '}] {einzeilig(u.get('text'))}{dauer(minuten=u.get('min'))}" for u in a.get("sub") or [] if einzeilig(u.get("text"))]
                if gruppe:
                    out.append("")
        out += [f"Projekt: [[{projekt}]]", ""]
        papierkorb(rel(f) + " (vor dem Assistenten)", alt)
        f.write_text("\n".join(out), "utf-8")
    for liste in ("monat", "woche", "heute"):
        for t in d.get(liste) or []:
            if einzeilig(t):
                add(liste, einzeilig(t), projekt)


def alter_plan(projekt):
    f = project_files().get(projekt)
    a = f.with_name(f"{projekt} – Alter Plan.md") if f else None
    return a.read_text("utf-8") if a and a.exists() else ""


def add(liste, text, projekt=None, phase=None, ziel=None, abschnitt=None):
    files = project_files()
    if liste == "neuesprojekt":
        with VAULT_LOCK:
            return neues_projekt(text)
    line = task_line(text, projekt if liste not in ("projekt", "ziel", "schritt", "meilenstein", "abschnitt") else None, files)
    with VAULT_LOCK:
        if liste == "endziel":
            if projekt not in files:
                raise ValueError("Projekt nicht gefunden")
            return set_endziel(files[projekt], " ".join(str(text).split()))
        if liste in ("projekt", "ziel", "schritt", "meilenstein", "abschnitt"):
            if projekt not in files:
                raise ValueError("Projekt nicht gefunden")
            if liste == "abschnitt":
                add_abschnitt(files[projekt], phase, line[6:])
            elif liste == "schritt":
                add_step(files[projekt], ziel or "", line[6:])
            elif liste == "meilenstein":
                add_milestone(files[projekt], line[6:])
            else:
                add_goal(files[projekt], line[6:], phase, abschnitt)
            return
        if liste in ("heute", "woche") and projekt and not in_project(files[projekt], line[6:].split(" – [[")[0]):
            # neues Ziel für heute/die Woche auch im Projekt anlegen (aktueller Meilenstein)
            add_goal(files[projekt], line[6:].split(" – [[")[0])
        if liste == "monat" and projekt and not in_project(files[projekt], line[6:].split(" – [[")[0]):
            add_milestone(files[projekt], clean(line[6:].split(" – [[")[0]))
        p = ensure_cockpit()
        rollover()
        head, secs = note_read(p)
        s = sec(secs, liste, after={"heute": None, "woche": "heute", "monat": "woche", "jahr": "monat"}[liste])
        if not has_task(s, line):
            append_task(s, line)
        if liste == "heute" and projekt:
            woche = sec(secs, "woche", after="heute")
            if not has_task(woche, line):
                append_task(woche, line)
        note_write(p, head, secs)


def umbenennen(projekt, alt, neu, art="ziel", phase=None):
    """Ziel „### alt“ (oder Meilenstein „## Meilenstein: alt“) in der Projekt-Notiz und alle Einträge „alt – [[projekt]]“ in BlockEm umbenennen."""
    files = project_files()
    neu = " ".join(str(neu).split()).replace("[[", "").replace("]]", "")
    if projekt not in files:
        raise ValueError("Projekt nicht gefunden")
    if not neu or neu.startswith("#"):
        raise ValueError("Leerer Name")
    with VAULT_LOCK:
        f = files[projekt]
        lines = f.read_text("utf-8").split("\n")
        if art == "abschnitt":
            s, _ = abschnitt_range(lines, phase, alt)
            fr = FRIST.search(ABSCHNITT.match(lines[s]).group(1))
            lines[s] = "#### Abschnitt: " + neu + (f" ({fr.group(0).strip()[1:-1]})" if fr else "")
            f.write_text("\n".join(lines), "utf-8")
            return
        if art == "meilenstein":
            i = next((i for i, l in enumerate(lines) if (m := PHASE.match(l)) and phase_name(m.group(1)).lower() == (alt or "").strip().lower()), None)
            if i is None:
                raise ValueError(f"Meilenstein nicht gefunden: {alt}")
            raw = PHASE.match(lines[i]).group(1)
            fr = FRIST.search(raw)
            lines[i] = "## Meilenstein: " + neu + (f" ({fr.group(0).strip()[1:-1]})" if fr else "") + (" (aktiv)" if "(aktiv)" in raw.lower() else "") + (" ✓" if "✓" in raw else "")
        else:
            g = goal_block(lines, alt or "")
            if not g:
                raise ValueError(f"Ziel nicht gefunden: {alt}")
            if neu.lower() != alt.strip().lower() and goal_block(lines, neu):
                raise ValueError(f"Ein Ziel „{neu}“ gibt es in {projekt} schon")
            fr, au = FRIST.search(lines[g[0]]), AUFWAND.search(lines[g[0]])
            if (b := CHECKBOX.match(lines[g[0]])) and not lines[g[0]].startswith("### "):
                pr = PRIO.search(b.group(4))
                lines[g[0]] = b.group(1) + b.group(2) + b.group(3) + neu + (au.group(0) if au else "") + (pr.group(0) if pr else "") + (fr.group(0) if fr else "")
            else:
                lines[g[0]] = "### " + neu + (f" ({au.group(0).strip()[1:-1]})" if au else "") + (f" ({fr.group(0).strip()[1:-1]})" if fr else "") + (" ✓" if "✓" in lines[g[0]] else "")
        f.write_text("\n".join(lines), "utf-8")
        p = cockpit_path()
        if p.exists():
            cl = p.read_text("utf-8").split("\n")
            for i, l in enumerate(cl):
                if (m := CHECKBOX.match(l)) and base_text(m.group(4)) == alt.strip().lower() and projekt in LINK.findall(m.group(4)):
                    cl[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{neu} – [[{projekt}]]"
            p.write_text("\n".join(cl), "utf-8")


def plan_setzen(data):
    """Ergebnis von „Woche planen“ / „Heute planen“ in die Cockpit-Notiz schreiben.
    data: {woche: [{text, projekt}], kw: int, tage: {"JJJJ-MM-TT": [{text, projekt}]}, heute: [{text, projekt}]}"""
    files = project_files()
    today = date.today()
    line = lambda it: task_line(it.get("text", ""), it.get("projekt") or None, files)
    with VAULT_LOCK:
        # selbst ergänzte Einträge mit Projekt auch in die Projekt-Notiz schreiben
        for it in [*(data.get("woche") or []), *(data.get("heute") or [])]:
            pr = it.get("projekt")
            if it.get("neu") and pr in files and not in_project(files[pr], it.get("text", "")):
                add("ziel", it["text"], pr)
        for it in data.get("monat") or []:
            pr = it.get("projekt")
            if it.get("neu") and pr in files and not in_project(files[pr], it.get("text", "")):
                add("meilenstein", it["text"], pr)
        p = ensure_cockpit()
        rollover()
        head, secs = note_read(p)
        heute = sec(secs, "heute")
        if data.get("monat") is not None:
            mo = sec(secs, "monat", after="plan" if any(x["key"] == "plan" for x in secs) else "woche")
            offen = {base_text(box(l).group(4)): l for l in mo["body"] if box(l) and not is_done(l)}
            neu = []
            for it in data["monat"]:
                l = line(it)
                k = base_text(box(l).group(4))
                if k not in {base_text(box(x).group(4)) for x in neu}:
                    neu.append(offen.get(k, l))
            mo["body"] = [l for l in mo["body"] if is_done(l) or (l.strip() and not box(l))] + neu
            mo["h"] = f"## Monat ({MONATE[today.month - 1]} {today.year})"
        if data.get("woche") is not None:
            woche = sec(secs, "woche", after="heute")
            offen = {base_text(box(l).group(4)): l for l in woche["body"] if box(l) and not is_done(l)}
            neu = []
            for it in data["woche"]:
                l = line(it)
                k = base_text(box(l).group(4))
                if k not in {base_text(box(x).group(4)) for x in neu}:
                    neu.append(offen.get(k, l))
            rest = [l for l in woche["body"] if is_done(l) or (l.strip() and not box(l))]
            woche["body"] = rest + neu
            woche["h"] = f"## Woche (KW {int(data.get('kw') or today.isocalendar()[1])})"
            # rausgenommene Wochenziele verschwinden auch aus Heute und von den Tagen (Erledigtes bleibt)
            weg = set(offen) - {base_text(box(x).group(4)) for x in neu}
            if weg:
                drop = lambda body: [l for l in body if not (box(l) and not is_done(l) and base_text(box(l).group(4)) in weg)]
                heute["body"] = drop(heute["body"])
                if (wp := next((x for x in secs if x["key"] == "plan"), None)):
                    wp["body"] = drop(wp["body"])
        if data.get("tage") is not None:
            wp = sec(secs, "plan", after="woche")
            days = {}
            for iso, items in data["tage"].items():
                d = date.fromisoformat(iso)
                lines = [line(it) for it in items]
                if d == today:
                    for l in lines:
                        if not has_task(heute, l):
                            append_task(heute, l)
                elif d > today and lines:
                    days[d] = lines
            # erledigte Einträge künftiger Tage und Zeilen ohne Tag behalten
            ohne = []
            for d, old in plan_days(wp):
                if d is None:
                    ohne.append((None, old))
                elif d > today:
                    # Tage außerhalb des geplanten Zeitraums bleiben ganz, sonst nur das Erledigte
                    keep = old if d.isoformat() not in data["tage"] else [l for l in old if is_done(l)]
                    days.setdefault(d, [])[:0] = keep
            plan_days_set(wp, ohne + sorted(days.items()))
        for it in data.get("heute") or []:
            l = line(it)
            if not has_task(heute, l):
                append_task(heute, l)
            if it.get("projekt"):
                # was heute mit Projekt dran ist, ist auch ein Wochenziel
                woche = sec(secs, "woche", after="heute")
                if not has_task(woche, l):
                    append_task(woche, l)
        note_write(p, head, secs)


def fmt(sec):
    m = round(sec / 60)
    return f"{m // 60} Std {m % 60} Min" if m >= 60 else f"{m} Min"


def write_report(res, final):
    """Tagesbericht in den Vault, damit Claude (und du in Obsidian) die Bildschirmzeit sieht."""
    v = vault()
    if not v or not CFG.get("tagesberichtOrdner"):
        return
    s = res["summe"]
    if not any(s.values()):
        return
    if not final and time.time() - REPORTED.get(res["date"], 0) < 600:
        return
    REPORTED[res["date"]] = time.time()
    act = s["produktiv"] + s["prokrastination"]
    score = round(s["produktiv"] / act * 100) if act else 0
    apps = "\n".join(f"| {a['name']} | {a['kat']} | {fmt(a['sek'])} |" for a in res["apps"])
    md = f"""---
typ: bildschirmzeit
datum: {res['date']}
fokus_score: {score}
produktiv_min: {round(s['produktiv'] / 60)}
prokrastination_min: {round(s['prokrastination'] / 60)}
neutral_min: {round(s['neutral'] / 60)}
nicht_am_pc_min: {round(s['afk'] / 60)}
stand: {res['stand']}
---
# Bildschirmzeit {res['date']}

Automatisch von BlockEm geschrieben (Quelle: ActivityWatch).

- **Fokus-Score:** {score} %
- **Produktiv:** {fmt(s['produktiv'])} · **Prokrastination:** {fmt(s['prokrastination'])} · **Neutral:** {fmt(s['neutral'])} · **Nicht am PC:** {fmt(s['afk'])}

| App / Seite | Kategorie | Zeit |
|---|---|---|
{apps}

```json
{json.dumps(res, ensure_ascii=False)}
```
"""
    d = v / CFG["tagesberichtOrdner"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{res['date']}.md").write_text(md, "utf-8")


# ---------- HTTP ----------

# ---------- Fokus-Timer ----------

FOKUS_FILE = DATEN / "fokus.json"
FOKUS_LOCK = threading.RLock()


def window_stats(start, end):
    """Was zwischen start und end (UTC) passiert ist: Mac über ActivityWatch, Handys über die Handy-App."""
    r = {"dauer": max(0, round((end - start).total_seconds())), "produktiv": 0, "neutral": 0, "prokrastination": 0,
         "weg": 0, "handy": 0, "abgelenkt": 0, "ablenkungen": []}
    if end <= start:
        return r
    abl, prod, bad, eps = {}, [], [], []
    try:
        win_id, afk_id, _ = pick_buckets()
    except Exception:
        win_id = afk_id = None
    active = []
    if afk_id:
        for s, t, d in events(afk_id, start, end):
            if d.get("status") == "not-afk":
                active.append((s, t))
            else:
                r["weg"] += (t - s).total_seconds()
    active = merge(active)
    if win_id:
        for s, t, d in events(win_id, start, end):
            for a, b in active:
                a, b = max(s, a), min(t, b)
                if b > a:
                    k = classify(d.get("app"), d.get("title"), label(d.get("app"), d.get("title")))   # mit eigener Einordnung (Werte-Reiter)
                    r[k] += (b - a).total_seconds()
                    if k == "produktiv":
                        prod.append((a, b))
                    if k == "prokrastination":
                        n = f"{label(d.get('app'), d.get('title'))} (Mac)"
                        abl[n] = abl.get(n, 0) + (b - a).total_seconds()
                        eps.append((a, b, n))
    days = {start.astimezone().date().isoformat(), end.astimezone().date().isoformat()}
    for dev in handy_devices():
        for day in days:
            for pkg, name, s_ms, t_ms in dev["tage"].get(day, []):
                s = max(datetime.fromtimestamp(s_ms / 1000, timezone.utc), start)
                t = min(datetime.fromtimestamp(t_ms / 1000, timezone.utc), end)
                if t <= s or pkg == "de.john.cockpit":   # BlockEm am Handy ansehen ist keine Ablenkung
                    continue
                sec = (t - s).total_seconds()
                r["handy"] += sec
                if classify(name, pkg, name) != "produktiv":
                    n = f"{name} ({dev['name']})"
                    abl[n] = abl.get(n, 0) + sec
                    bad.append((s, t))
                    eps.append((s, t, n))
    # Handy-Ablenkung, während am Mac noch ein produktives Fenster offen war, zählt nicht als produktiv
    bad = merge(bad)
    for a, b in merge(prod):
        for c, d in bad:
            if min(b, d) > max(a, c):
                r["produktiv"] -= (min(b, d) - max(a, c)).total_seconds()
    r["abgelenkt"] = sum(abl.values())
    # Wie oft abgelenkt: Ablenkungen mit weniger als 1 Minute Pause dazwischen zählen als eine, unter 15 Sekunden zählt nicht
    mom = []   # (start, ende, {name: sek}); Name des Moments = was darin am längsten lief
    for s, t, n in sorted(eps, key=lambda x: x[0]):
        sek = (t - s).total_seconds()
        if mom and (s - mom[-1][1]).total_seconds() < 60:
            mom[-1][1] = max(mom[-1][1], t)
            mom[-1][2][n] = mom[-1][2].get(n, 0) + sek
        else:
            mom.append([s, t, {n: sek}])
    mom = [(s, t, max(ns, key=ns.get)) for s, t, ns in mom if (t - s).total_seconds() >= 15]
    r["anzahl"] = len(mom)
    r["momente"] = [{"start": s.astimezone().isoformat(timespec="seconds"), "sek": round((t - s).total_seconds()), "name": n}
                    for s, t, n in mom[:30]]
    r["ablenkungen"] = [{"name": n, "sek": round(s)} for n, s in sorted(abl.items(), key=lambda x: -x[1])[:5]]
    for k in ("produktiv", "neutral", "prokrastination", "weg", "handy", "abgelenkt"):
        r[k] = round(r[k])
    r["fokus"] = round(100 * r["produktiv"] / r["dauer"]) if r["dauer"] else 0
    return r


def fokus_load():
    try:
        return json.loads(FOKUS_FILE.read_text("utf-8"))
    except Exception:
        return {"aktiv": None, "sessions": []}


def fokus_save(st):
    FOKUS_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")


def fokus_note(st):
    """Fokus-Sessions als Tabelle in den Vault schreiben (wird jedes Mal komplett neu geschrieben)."""
    v = vault()
    if not v:
        return
    p = v / CFG.get("fokusNotiz", mem("Fokus-Sessions.md"))
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in reversed(st["sessions"]):
        a, e, r = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["ende"]), s["ergebnis"]
        abl = ", ".join(f"{x['name']} {round(x['sek'] / 60)}" for x in r["ablenkungen"][:3] if x["sek"] >= 60)
        cell = lambda t: str(t or "").replace("|", "/")
        proj = "[[" + s["projekt"] + "]]" if s.get("projekt") else ""
        rows.append(f"| {a.date().isoformat()} | {a:%H:%M}–{e:%H:%M} | {proj} | "
                    f"{cell(s.get('aufgabe'))} | {s['minuten']} | {round(r['produktiv'] / 60)} | {round(r['abgelenkt'] / 60)}"
                    f"{f' ({cell(abl)})' if abl else ''} | {round(r['weg'] / 60)} | {r['fokus']} % |")
    p.write_text("---\ntyp: fokus-sessions\n---\n# Fokus-Sessions\n\n"
                 "Schreibt BlockEm nach jeder Session neu (Minuten). Abgelenkt = Prokrastination am Mac plus Handy-Nutzung, "
                 "die nicht produktiv ist. Handy-Daten kommen bis zu 15 Minuten später dazu.\n\n"
                 "| Datum | Zeit | Projekt | Aufgabe | Geplant | Produktiv | Abgelenkt | Weg | Fokus |\n"
                 "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n", "utf-8")


def fokus_summe(sessions, ab):
    """Echte Fokuszeit, Sessions und Ablenkungen aller Sessions seit dem Datum ab (JJJJ-MM-TT)."""
    r = {"sessions": 0, "dauer": 0, "produktiv": 0, "abgelenkt": 0, "anzahl": 0}
    top = {}
    for s in sessions:
        e = s.get("ergebnis")
        if s["start"][:10] < ab or not e:
            continue
        r["sessions"] += 1
        for k in ("dauer", "produktiv", "abgelenkt", "anzahl"):
            r[k] += e.get(k, 0)
        for x in e.get("ablenkungen", []):
            top[x["name"]] = top.get(x["name"], 0) + x["sek"]
    r["fokus"] = round(100 * r["produktiv"] / r["dauer"]) if r["dauer"] else 0
    r["top"] = [{"name": n, "sek": round(v)} for n, v in sorted(top.items(), key=lambda x: -x[1])[:3]]
    return r


def fokus_state():
    """Laufende Session prüfen (abgelaufen -> beenden), Ergebnisse der letzten 30 Minuten nachrechnen."""
    with FOKUS_LOCK:
        st = fokus_load()
        now = datetime.now().astimezone()
        changed = False
        a = st.get("aktiv")
        if a and now >= datetime.fromisoformat(a["planEnde"]):
            st["sessions"].append({**a, "ende": a["planEnde"], "final": False})
            st["aktiv"] = None
            changed = True
        for s in st["sessions"]:
            if not s.get("final"):
                ende = datetime.fromisoformat(s["ende"])
                s["ergebnis"] = window_stats(datetime.fromisoformat(s["start"]), ende)
                s["final"] = now > ende + timedelta(minutes=30)
                changed = True
        st["sessions"] = st["sessions"][-500:]
        if changed:
            fokus_save(st)
            fokus_note(st)
        out = {"aktiv": None, "sessions": st["sessions"][-12:][::-1]}
        if st.get("aktiv"):
            a = st["aktiv"]
            out["aktiv"] = {**a, "live": window_stats(datetime.fromisoformat(a["start"]), now),
                            "gesperrt": SPERRE_LOG["liste"] if SPERRE_LOG["start"] == a["start"] else []}
        monday = (now - timedelta(days=now.weekday())).date().isoformat()
        woche = {}
        for s in st["sessions"]:
            if s["start"][:10] >= monday and s.get("projekt"):
                woche[s["projekt"]] = woche.get(s["projekt"], 0) + s["ergebnis"]["produktiv"]
        out["wocheProjekt"] = woche
        out["auswertung"] = {"heute": fokus_summe(st["sessions"], now.date().isoformat()), "woche": fokus_summe(st["sessions"], monday)}
        return out


def fokus_action(data):
    with FOKUS_LOCK:
        fokus_state()
        st = fokus_load()
        now = datetime.now().astimezone().replace(microsecond=0)
        if data.get("aktion") == "start":
            if st.get("aktiv"):
                raise ValueError("Es läuft schon eine Fokus-Session")
            minuten = int(data.get("minuten") or 60)
            if not 1 <= minuten <= 240:
                raise ValueError("Dauer zwischen 1 und 240 Minuten")
            projekt = data.get("projekt") or None
            if projekt and projekt not in project_files():
                raise ValueError("Projekt nicht gefunden")
            st["aktiv"] = {"start": now.isoformat(), "planEnde": (now + timedelta(minutes=minuten)).isoformat(),
                           "minuten": minuten, "projekt": projekt, "aufgabe": (str(data.get("aufgabe") or "").strip()[:200] or None),
                           "vonPlan": bool(data.get("vonPlan"))}
        elif data.get("aktion") == "stop":
            if not ENTWICKLER:   # 1.0.48 (john 08.11.): ein Fokus läuft bis zum Ende, kein Abbrechen
                raise ValueError("Der Fokus läuft bis zum Ende.")
            a = st.get("aktiv")
            if a:
                st["aktiv"] = None
                # unter einer Minute zählt nicht als Session
                if (now - datetime.fromisoformat(a["start"])).total_seconds() >= 60:
                    st["sessions"].append({**a, "ende": now.isoformat(), "final": False})
        else:
            raise ValueError("Unbekannte Aktion")
        fokus_save(st)
    return fokus_state()


# ---------- Marktreif: Profil, Einstellungen, Ersteinrichtung, Datenexport ----------
def obsidian_vaults():
    """Vaults, die die Obsidian-App kennt (für die Ersteinrichtung)."""
    orte = [Path.home() / "Library" / "Application Support" / "obsidian", Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "obsidian",
            Path.home() / ".config" / "obsidian"]
    for o in orte:
        try:
            d = json.loads((o / "obsidian.json").read_text("utf-8"))
            return sorted({str(x.get("path")) for x in (d.get("vaults") or {}).values() if x.get("path") and Path(x["path"]).is_dir()})
        except Exception:
            continue
    return []


def profil():
    v = vault()
    return {"name": nutzer_name(), "zielDatum": CFG.get("zielDatum") or "", "zielText": CFG.get("zielText") or "", "version": APP_VERSION,
            "planung": PLANUNG, "eingerichtet": bool(CFG.get("eingerichtet") or (v and (v / CFG["cockpitNotiz"]).exists())),
            "vault": str(v) if v else "", "datenOrdner": mem(), "obsidian": bool(v and (v / ".obsidian").is_dir()),
            "vorschlagOrdner": str(Path.home() / "Documents" / "Fokus-Cockpit Daten"),
            "obsidianVaults": [] if v else obsidian_vaults(), "lan": bool(CFG.get("lan")), "port": CFG["port"]}


def config_speichern():
    tmp = DATEN / "config.json.tmp"
    tmp.write_text(json.dumps(CFG, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(DATEN / "config.json")


def einstellungen_setzen(data):
    """POST /api/einstellungen {name?, zielDatum?, zielText?, produktivZielMin?, prokrastinationLimitMin?, ordner?: {"art": "neu"|"vault", "pfad"?}, eingerichtet?}"""
    if "name" in data:
        CFG["name"] = einzeilig(data["name"])[:40]
    if "zielDatum" in data:
        z = str(data["zielDatum"] or "").strip()
        if z and not re.match(r"^\d{4}-\d{2}-\d{2}$", z):
            raise ValueError("Datum bitte als JJJJ-MM-TT")
        CFG["zielDatum"] = z
    if "zielText" in data:
        CFG["zielText"] = einzeilig(data["zielText"])[:60]
    for k in ("produktivZielMin", "prokrastinationLimitMin"):
        if data.get(k) is not None:
            CFG[k] = max(5, min(1440, int(data[k])))
    o = data.get("ordner")
    if isinstance(o, dict):
        if o.get("art") == "vault":
            pf = Path(str(o.get("pfad") or "")).expanduser()
            if not pf.is_dir():
                raise ValueError("Diesen Ordner gibt es nicht.")
            CFG["vault"] = str(pf)
        else:
            pf = Path(str(o.get("pfad") or "") or (Path.home() / "Documents" / "Fokus-Cockpit Daten")).expanduser()
            pf.mkdir(parents=True, exist_ok=True)
            CFG["vault"] = str(pf)
        if not (pf / CFG["cockpitNotiz"]).exists() and not (pf / "Claude Memory" / "Cockpit.md").exists():
            CFG.update(cockpitNotiz="Cockpit/Cockpit.md", datenOrdner="BlockEm", tagesberichtOrdner="Cockpit/Bildschirmzeit")
        (pf / CFG.get("projekteOrdner", "Projekte")).mkdir(parents=True, exist_ok=True)
        ensure_cockpit()
    if data.get("eingerichtet"):
        CFG["eingerichtet"] = True
    if data.get("sprache") in ("de", "en"):   # 24.11.: Sprache der Oberfläche, für Mitteilungen am Computer
        CFG["sprache"] = data["sprache"]
    config_speichern()


def export_zip():
    """Alle eigenen Daten als ZIP (ohne KI-Schlüssel): BlockEm-Dateien, Handy-Daten, Notizen aus dem Datenordner und den Projekten."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in ("automatik.json", "fokus.json", "konsistenz.json", "kategorien.json"):
            if (DATEN / n).exists():
                z.write(DATEN / n, f"Fokus-Cockpit/{n}")
        z.writestr("Fokus-Cockpit/config.json", json.dumps({k: v for k, v in CFG.items() if k != "handyCode"}, ensure_ascii=False, indent=2))
        if HANDY_DIR.is_dir():
            for f in HANDY_DIR.glob("*.json"):
                z.write(f, f"Fokus-Cockpit/handy-daten/{f.name}")
        v = vault()
        if v:
            for ordner in (mem(), CFG.get("projekteOrdner", "Projekte")):
                for f in (v / ordner).rglob("*.md") if (v / ordner).is_dir() else []:
                    z.write(f, f"Notizen/{f.relative_to(v)}")
        z.writestr("LIESMICH.txt", "Export aus BlockEm vom " + datetime.now().strftime("%d.%m.%Y %H:%M") +
                   ".\nNotizen sind normale Markdown-Dateien, die übrigen Daten JSON. KI-Schlüssel sind bewusst nicht enthalten.\n")
    return buf.getvalue()


# ---------- Umzug: Daten von einem Ort an einen anderen (Handy ↔ Computer ↔ Online-Server) ----------
# Grundlage ist der Export (export_zip). Einspielen sichert vorher den bisherigen Stand und ersetzt nur gleichnamige Dateien.
UMZUG_JSON = ("automatik.json", "fokus.json", "konsistenz.json", "kategorien.json")
UMZUG_NICHT = {"handyCode", "port", "lan", "vault", "modus", "activitywatch", "activitywatchPruefen", "erlaubteHosts", "entwickler"}


def umzug_einspielen(roh):
    import io
    import zipfile
    if fokus_laeuft() and not ENTWICKLER:   # 1.0.48: sonst beendet eine alte Sicherung den laufenden Fokus
        raise ValueError("Während eines Fokus geht das nicht. Warte, bis er vorbei ist.")
    try:
        z = zipfile.ZipFile(io.BytesIO(roh))
    except zipfile.BadZipFile:
        raise ValueError("Das ist keine BlockEm-Sicherung (ZIP-Datei).")
    namen = [i for i in z.infolist() if not i.is_dir()]
    if not any(i.filename.startswith("Fokus-Cockpit/") for i in namen):
        raise ValueError("In der Datei fehlt der Ordner „Fokus-Cockpit“. Bitte einen Export aus BlockEm nehmen.")
    if sum(i.file_size for i in namen) > 300 * 1024 * 1024:
        raise ValueError("Die Sicherung ist zu groß (über 300 MB).")
    sich = DATEN / "sicherung" / f"vor-umzug-{datetime.now():%Y-%m-%d-%H%M%S}.zip"
    sich.parent.mkdir(parents=True, exist_ok=True)
    sich.write_bytes(export_zip())
    v = vault()
    if not v:
        v = Path(CFG.get("vault") or Path.home() / "Documents" / "Fokus-Cockpit Daten").expanduser()
        v.mkdir(parents=True, exist_ok=True)
        CFG["vault"] = str(v)
    n_notizen = n_dateien = 0
    for i in namen:
        teile = Path(i.filename).parts
        if not teile or ".." in teile or i.filename.startswith(("/", "\\")) or ":" in teile[0]:
            continue
        daten = z.read(i)
        if teile[0] == "Notizen" and len(teile) > 1 and i.filename.endswith(".md"):
            ziel = v.joinpath(*teile[1:])
            n_notizen += 1
        elif teile[0] == "Fokus-Cockpit" and len(teile) == 2 and teile[1] == "automatik.json" and not ENTWICKLER:
            try:   # Sicherheitstest 24.11.: Regeln und Schutz aus einer Sicherung nur strenger übernehmen
                eingang = json.loads(daten.decode("utf-8"))
            except ValueError:
                continue
            with AUTOMATIK_LOCK:
                try:
                    automatik_save(umzug_strenger(automatik_load(), eingang if isinstance(eingang, dict) else {}))
                except (TypeError, AttributeError, ValueError, RecursionError):   # kaputte Sicherung: nichts übernehmen, Regeln bleiben
                    continue
            n_dateien += 1
            continue
        elif teile[0] == "Fokus-Cockpit" and len(teile) == 2 and teile[1] == "kategorien.json" and not ENTWICKLER and umzug_eingerichtet(automatik_load()):
            continue   # Sicherheitstest 24.11. (Prüfer): eigene Einordnung steuert Konsum-Regeln, darf nicht ohne Wartezeit ersetzt werden
        elif teile[0] == "Fokus-Cockpit" and len(teile) == 2 and teile[1] in UMZUG_JSON:
            ziel = DATEN / teile[1]
        elif teile[0] == "Fokus-Cockpit" and len(teile) == 3 and teile[1] == "handy-daten" and teile[2].endswith(".json"):
            ziel = HANDY_DIR / teile[2]
        elif i.filename == "Fokus-Cockpit/config.json":
            try:
                alt = json.loads(daten.decode("utf-8"))
            except ValueError:
                continue
            CFG.update({k: w for k, w in alt.items() if k not in UMZUG_NICHT})
            continue
        else:
            continue
        ziel.parent.mkdir(parents=True, exist_ok=True)
        tmp = ziel.with_name(ziel.name + ".neu")
        tmp.write_bytes(daten)
        os.replace(tmp, ziel)
        n_dateien += 1
    CFG["eingerichtet"] = True
    (DATEN / "config.json").write_text(json.dumps(CFG, ensure_ascii=False, indent=2), "utf-8")
    with LOCK:
        CACHE.clear()
    sperre_protokoll(f"Umzug: {n_dateien} Dateien eingespielt ({n_notizen} Notizen), vorher gesichert in {sich.name}")
    return {"ok": True, "dateien": n_dateien, "notizen": n_notizen, "sicherung": str(sich)}


# Diese Teile steuern Sperren und Konsum-Regeln: aus einer Sicherung nie, wenn hier schon etwas eingerichtet ist (nur über die Mischregeln unten)
UMZUG_STRENG = ("locker", "schutz", "profil", "kategorien", "innehalten", "orte", "regulierung", "erziehung", "sperre", "konsum", "panik")


def umzug_eingerichtet(a):
    return bool(a.get("profil") or a.get("schutz") or a.get("locker"))


def umzug_strenger(jetzt, neu):
    """Sicherheitstest 24.11.2026: Eine Sicherung darf nichts lockern. Ist hier schon etwas eingerichtet (Profil, Schutz oder Regeln),
    bleibt alles Vorhandene; dazu kommen nur neue Regeln, eingeschaltete Schalter und fehlende Daten. Ist nichts eingerichtet,
    kommt die Sicherung ganz, aber ohne offenes Lockern-Fenster und ohne Lösch-Marken (die würden sich auf andere Geräte verteilen)."""
    l_neu = dict(neu.get("locker") or {})
    for k in ("frei", "geloescht"):
        l_neu.pop(k, None)
    eingerichtet = umzug_eingerichtet(jetzt)
    if not eingerichtet:
        out = dict(neu)
        if "locker" in neu:
            out["locker"] = l_neu
        return out
    out = {k: w for k, w in neu.items() if k not in jetzt and k not in UMZUG_STRENG}
    out.update(jetzt)
    l_alt = jetzt.get("locker") or {}
    l = dict(l_alt)
    regeln = [r for r in l_alt.get("regeln") or [] if isinstance(r, dict)]
    ids = {str(r.get("id")) for r in regeln}
    l["regeln"] = list(l_alt.get("regeln") or [])
    for r in l_neu.get("regeln") or []:
        if isinstance(r, dict) and str(r.get("id")) not in ids:
            try:   # Prüfer: kaputte Regel aus der Sicherung legte sonst /api/fokus-handy lahm
                n = locker_regel_sauber(r)
            except (ValueError, TypeError, AttributeError):
                continue
            l["regeln"].append(n)
            ids.add(n["id"])
    if l_alt.get("an") is False and l_neu.get("an", True) is not False:
        l["an"] = True
    out["locker"] = l
    s_alt, s_neu = jetzt.get("schutz") or {}, neu.get("schutz") or {}
    if not isinstance(s_neu, dict):
        s_neu = {}
    s = dict(s_alt)
    for k, (std, _) in SCHUTZ_SCHALTER.items():
        s[k] = bool(s_alt.get(k, std)) or bool(s_neu.get(k, std))
    geraete = {g: dict(v) for g, v in (s_alt.get("geraete") or {}).items() if isinstance(v, dict)}
    g_neu = s_neu.get("geraete") if isinstance(s_neu.get("geraete"), dict) else {}
    for g, v in g_neu.items():
        for k, w in (v.items() if isinstance(v, dict) else ()):
            if w is True:
                geraete.setdefault(str(g), {})[k] = True
    s["geraete"] = geraete
    out["schutz"] = s
    p_neu = neu.get("profil")
    if not jetzt.get("profil") and isinstance(p_neu, dict) and profil_daten({"profil": p_neu})["pornoSchutz"]:
        out["profil"] = p_neu   # fehlendes Profil nur aus der Sicherung, wenn es strenger ist (Porno-Schutz)
    return out


def umzug_ziel(adresse):
    a = str(adresse or "").strip().rstrip("/")
    if not a:
        raise ValueError("Bitte die Adresse eintragen.")
    if not re.match(r"^https?://", a):
        a = "http://" + a
    return a


def umzug_senden(data):
    """Diesen Stand an ein anderes BlockEm schicken (z. B. vom Computer auf den Online-Server). Hier bleibt alles, wie es ist."""
    ziel, code = umzug_ziel(data.get("ziel")), str(data.get("code") or "").strip()
    if not code:
        raise ValueError("Bitte den Zugangscode des anderen BlockEm eintragen.")
    import base64
    body = json.dumps({"zip": base64.b64encode(export_zip()).decode()}).encode()
    req = Request(ziel + "/api/umzug", data=body, headers={"Content-Type": "application/json", "X-Cockpit-Code": code}, method="POST")
    from urllib.error import HTTPError, URLError
    try:
        with urlopen(req, timeout=180) as r:
            antwort = json.loads(r.read() or b"{}")
        sperre_protokoll(f"Umzug: Daten an {ziel} geschickt")
        return {k: w for k, w in antwort.items() if k != "plan"}
    except HTTPError as e:
        if e.code == 403:
            raise ValueError("Der Zugangscode passt nicht zu diesem BlockEm.")
        try:
            meldung = json.loads(e.read()).get("fehler") or ""
        except Exception:
            meldung = ""
        raise ValueError(meldung or f"Das andere BlockEm meldet Fehler {e.code}.")
    except (URLError, OSError):
        raise ValueError(f"Das andere BlockEm ist nicht erreichbar ({ziel}). Läuft es, und stimmt die Adresse?")


# ---------- Fokus Sync (Pro, 3.12): mehrere Geräte ohne eigenen Server, Ende-zu-Ende verschlüsselt ----------
# Jedes Gerät behält seine Daten (Handy: in der App, Computer: im Ordner) und gleicht sie über unseren Dienst ab (sync-dienst/sync_dienst.py).
# Der Dienst sieht nur verschlüsselte Päckchen mit zufällig aussehenden IDs. Schlüssel:
#   Passwort --PBKDF2--> Stamm --> "auth" (zum Anmelden, geht zum Dienst) und "kek" (packt den Hauptschlüssel ein, bleibt hier)
#   Hauptschlüssel (zufällig, 32 Byte) verschlüsselt alle Dateien; der Dienst bekommt ihn nur eingepackt ("box").
#   Notfall-Code (einmal angezeigt) packt den Hauptschlüssel ein zweites Mal ein: Passwort vergessen geht nur damit.
# Verschlüsselung nur mit der Standardbibliothek (läuft auch im Handy in Pyodide): SHAKE256 als Schlüsselstrom + HMAC-SHA256 (Encrypt-then-MAC).
# Abgleich (sync_jetzt): erst Änderungen holen und einspielen, dann eigene Änderungen hochladen (If-Match = bekannte Version).
# Haben beide Seiten dieselbe JSON-Datei geändert, wird Schlüssel für Schlüssel gemischt (Grundlage = letzter gemeinsamer Stand in sync-basis/);
# bei Notizen gewinnt die neuere, die andere liegt danach in sicherung/sync-konflikte/. Gelöschtes landet in sicherung/sync-geloescht/.
# Zustand in sync.json (chmod 600, nicht in Export/Sicherung): adresse, email, token, geraet, schluessel, rev, dateien {pfad: {id, rev, hash}}.
SYNC_DATEI = DATEN / "sync.json"
SYNC_BASIS = DATEN / "sync-basis"
SYNC_LOCK = threading.Lock()
SYNC_STANDARD = os.environ.get("COCKPIT_SYNC") or "https://95-217-170-121.sslip.io"   # 1.0.15 (04.11.): BlockEm Sync bei Hetzner Helsinki (cloud-config, Caddy über sslip.io)
SYNC_N = 300000                                        # PBKDF2-Runden fürs Passwort (Handy reines Python: ca. 2–5 Sek.); gleich wie SYNC_KDF_N im Dienst
SYNC_LOKAL = {"app/automatik.json": ("appUpdate", "sicherung", "hinweis")}   # gehört nur zu diesem Gerät
SYNC_LAUF = {"zuletzt": 0, "fehler": "", "geholt": 0, "hoch": 0}
SYNC_FEHLT = object()
SYNC_NOTFALL_ZEICHEN = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class SyncKonflikt(Exception):
    pass


def sync_load():
    try:
        return json.loads(SYNC_DATEI.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def sync_save(s):
    tmp = SYNC_DATEI.with_name("sync.json.neu")
    tmp.write_text(json.dumps(s, ensure_ascii=False), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, SYNC_DATEI)


def _sync_ab(k, wofuer):
    return hmac.new(k, b"fokus-sync|" + wofuer.encode(), hashlib.sha256).digest()


def sync_stamm(geheim, kdf):
    pw, salz = str(geheim).encode("utf-8"), bytes.fromhex(kdf["salz"])
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha256", pw, salz, int(kdf["n"]))
    return pbkdf2_sha256(pw, salz, int(kdf["n"]))


def sync_schluessel(geheim, kdf):
    """(auth als Hex für den Dienst, kek zum Ein-/Auspacken des Hauptschlüssels)"""
    st = sync_stamm(geheim, kdf)
    return _sync_ab(st, "auth").hex(), _sync_ab(st, "kek")


def _sync_strom(k, nonce, n):
    return hashlib.shake_256(b"FCS1" + _sync_ab(k, "enc") + nonce).digest(n) if n else b""


def sync_zu(k, klar):
    nonce = secrets.token_bytes(24)
    ct = (int.from_bytes(klar, "big") ^ int.from_bytes(_sync_strom(k, nonce, len(klar)), "big")).to_bytes(len(klar), "big") if klar else b""
    return b"FCS1" + nonce + ct + hmac.new(_sync_ab(k, "mac"), b"FCS1" + nonce + ct, hashlib.sha256).digest()


def sync_auf(k, paket):
    if len(paket) < 60 or not paket.startswith(b"FCS1"):
        raise ValueError("kein Sync-Päckchen")
    nonce, ct, tag = paket[4:28], paket[28:-32], paket[-32:]
    if not hmac.compare_digest(tag, hmac.new(_sync_ab(k, "mac"), b"FCS1" + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("Päckchen beschädigt oder falscher Schlüssel")
    return (int.from_bytes(ct, "big") ^ int.from_bytes(_sync_strom(k, nonce, len(ct)), "big")).to_bytes(len(ct), "big") if ct else b""


def sync_box(kek, master):
    import base64
    return base64.b64encode(sync_zu(kek, master)).decode()


def sync_aus_box(kek, box):
    import base64
    try:
        m = sync_auf(kek, base64.b64decode(box))
    except ValueError:
        raise ApiFehler("Der Schlüssel passt nicht (falsches Passwort oder falscher Notfall-Code).", 403)
    if len(m) != 32:
        raise ApiFehler("Der Schlüssel aus dem Sync-Dienst ist ungültig.", 502)
    return m


def sync_notfall_code():
    z = "".join(secrets.choice(SYNC_NOTFALL_ZEICHEN) for _ in range(25))
    return "-".join(z[i:i + 5] for i in range(0, 25, 5))


def sync_notfall_norm(code):
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper()).replace("0", "O").replace("1", "I")


def sync_neuer_kdf(n=SYNC_N):
    return {"salz": secrets.token_hex(16), "n": n}


def sync_adresse(a):
    a = str(a or "").strip().rstrip("/") or sync_load().get("testAdresse") or SYNC_STANDARD
    if not a:
        raise ApiFehler("Bitte die Adresse des Sync-Dienstes eintragen.")
    if not re.match(r"^https?://", a):
        a = "https://" + a
    if a.startswith("http://") and not re.match(r"^http://(localhost|127\.0\.0\.1|\[::1\]|[\d.]+|[\w-]+\.local)(:\d+)?$", a):
        raise ApiFehler("Der Sync-Dienst braucht eine sichere Verbindung (https://).")
    return a


def sync_http(methode, pfad, body=None, token=None, adresse=None, kopf=None, roh=False, timeout=60):
    """Anfrage an den Sync-Dienst. body: dict (JSON) oder bytes. Ohne Python-Zertifikate (Mac) über curl."""
    try:
        import ssl
        ssl_fehler = ssl.SSLError
    except ImportError:   # Handy (Pyodide) hat kein ssl, dort macht die App die Verbindung
        ssl_fehler = ()
    from urllib.error import HTTPError, URLError
    url = sync_adresse(adresse or sync_load().get("adresse")) + pfad
    h = {"User-Agent": "Fokus-Cockpit/" + APP_VERSION, **(kopf or {})}
    if isinstance(body, dict):
        data, h["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    else:
        data = body
        if data is not None:
            h.setdefault("Content-Type", "application/octet-stream")
    if token:
        h["Authorization"] = "Bearer " + token
    inhalt = b""
    try:
        try:
            with urlopen(Request(url, data=data, headers=h, method=methode), timeout=timeout) as r:
                code, inhalt = 200, r.read()
        except URLError as e:
            if isinstance(e, HTTPError) or not (ssl_fehler and isinstance(getattr(e, "reason", None), ssl_fehler)) or not Path("/usr/bin/curl").exists():
                raise
            code, inhalt = sync_curl(methode, url, data, h, timeout)
            if code >= 400:
                raise HTTPError(url, code, "", {}, None)   # Text steht schon in inhalt
    except HTTPError as e:
        try:
            inhalt = e.read() if e.fp else inhalt
        except Exception:
            inhalt = b""
        code = e.code
    except (URLError, OSError):
        raise ApiFehler("Der Sync-Dienst ist gerade nicht erreichbar. Bist du online?", 503)
    if code >= 400:
        try:
            j = json.loads(inhalt or b"{}")
        except ValueError:
            j = {}
        if code == 409 and "rev" in j:
            raise SyncKonflikt(j["rev"])
        if code == 401 and token:
            s = sync_load()
            if s.get("token") == token:
                s["abgemeldet"] = True
                sync_save(s)
        raise ApiFehler(j.get("fehler") or f"Der Sync-Dienst meldet Fehler {code}.", 402 if code == 402 else 403 if code in (401, 403) else 502)
    if roh:
        return inhalt
    return json.loads(inhalt or b"{}")


def sync_curl(methode, url, data, headers, timeout):
    import subprocess
    import tempfile
    fd, cfg = tempfile.mkstemp(prefix="cockpit-sync-")
    try:
        with os.fdopen(fd, "w") as f:
            for k, v in headers.items():
                f.write('header = "%s: %s"\n' % (k, str(v).replace('"', '\\"')))
        r = subprocess.run(["/usr/bin/curl", "-sS", "-X", methode, "-m", str(int(timeout)), "-K", cfg, "-w", "\n%{http_code}", url]
                           + (["--data-binary", "@-"] if data is not None else []), input=data or b"", capture_output=True, timeout=timeout + 5)
    finally:
        os.unlink(cfg)
    if r.returncode != 0:
        raise OSError(r.stderr.decode("utf-8", "replace")[:200])
    inhalt, _, code = r.stdout.rpartition(b"\n")
    return int(code) if code.isdigit() else 599, inhalt


def sync_dateien():
    """Was abgeglichen wird (wie beim Export): BlockEm-Dateien, Handy-Daten, Notizen aus dem Datenordner und den Projekten.
    Notizen-Pfade beginnen mit "daten/" bzw. "projekte/", damit „Claude Memory“ am Mac und „Cockpit“ im Handy zusammenpassen."""
    out = {}
    for n in UMZUG_JSON:
        if (DATEN / n).is_file():
            out["app/" + n] = DATEN / n
    if HANDY_DIR.is_dir():
        for f in HANDY_DIR.glob("*.json"):
            out["handy/" + f.name] = f
    v = vault()
    if v:
        for art, ordner in (("daten", mem()), ("projekte", CFG.get("projekteOrdner", "Projekte"))):
            o = v / ordner
            if o.is_dir():
                for f in o.rglob("*.md"):
                    if not any(t.startswith(".") for t in f.relative_to(o).parts):
                        out.setdefault(f"{art}/{f.relative_to(o).as_posix()}", f)
    return out


def sync_ziel(pfad):
    """Wohin eine Datei von einem anderen Gerät gehört (None = nicht erlaubt)."""
    art, _, rest = str(pfad).partition("/")
    teile = Path(rest).parts
    if not rest or not teile or ".." in teile or rest.startswith(("/", "\\")) or "\\" in rest or ":" in rest or any(t.startswith(".") for t in teile):
        return None
    if art == "app" and rest in UMZUG_JSON:
        return DATEN / rest
    if art == "handy" and len(teile) == 1 and rest.endswith(".json"):
        return HANDY_DIR / rest
    if art in ("daten", "projekte") and rest.endswith(".md"):
        v = vault()
        return v / (mem() if art == "daten" else CFG.get("projekteOrdner", "Projekte")) / rest if v else None
    return None


def sync_lesen(pfad, f):
    """Inhalt, wie er abgeglichen wird: JSON einheitlich formatiert und ohne die Teile, die nur zu diesem Gerät gehören."""
    try:
        b = f.read_bytes()
    except OSError:
        return None
    if not pfad.endswith(".json"):
        return b
    try:
        d = json.loads(b or b"null")
    except ValueError:
        return b
    if isinstance(d, dict) and pfad in SYNC_LOKAL:
        d = {k: w for k, w in d.items() if k not in SYNC_LOKAL[pfad]}
    return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sync_hash(b):
    return None if b is None else hashlib.sha256(b).hexdigest()


def sync_schreiben(pfad, f, inhalt):
    """Neuen Stand hier ablegen (None = auf dem anderen Gerät gelöscht: hier in die Sicherung schieben)."""
    if inhalt is None:
        if f.exists():
            ziel = DATEN / "sicherung" / "sync-geloescht" / datetime.now().strftime("%Y-%m-%d-%H%M%S") / pfad
            ziel.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, ziel)
        return
    if pfad in SYNC_LOKAL and f.exists():
        try:
            alt, neu = json.loads(f.read_bytes()), json.loads(inhalt)
            if isinstance(alt, dict) and isinstance(neu, dict):
                neu.update({k: alt[k] for k in SYNC_LOKAL[pfad] if k in alt})
                inhalt = json.dumps(neu, ensure_ascii=False, indent=1).encode("utf-8")
        except ValueError:
            pass
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".sync-neu")
    tmp.write_bytes(inhalt)
    os.replace(tmp, f)


def sync_mischen(basis, lokal, fern, fern_neuer):
    """Drei-Wege-Mischen für JSON: was nur eine Seite geändert hat, kommt durch; ändern beide denselben Wert, gewinnt der neuere Stand."""
    if isinstance(lokal, dict) and isinstance(fern, dict):
        b = basis if isinstance(basis, dict) else {}
        out = {}
        for k in list(lokal) + [k for k in fern if k not in lokal]:
            l, r, bb = lokal.get(k, SYNC_FEHLT), fern.get(k, SYNC_FEHLT), b.get(k, SYNC_FEHLT)
            if l == r or r == bb:
                w = l
            elif l == bb:
                w = r
            elif isinstance(l, dict) and isinstance(r, dict):
                w = sync_mischen(bb, l, r, fern_neuer)
            else:
                w = r if fern_neuer else l
            if w is not SYNC_FEHLT:
                out[k] = w
        return out
    return fern if fern_neuer else lokal


def _sync_basis(fid, inhalt=SYNC_FEHLT):
    f = SYNC_BASIS / fid
    if inhalt is SYNC_FEHLT:
        try:
            return f.read_bytes()
        except OSError:
            return None
    if inhalt is None:
        if f.exists():
            os.replace(f, f.with_name(fid + ".alt"))
        return None
    SYNC_BASIS.mkdir(parents=True, exist_ok=True)
    f.write_bytes(inhalt)
    return None


def sync_konflikt(pfad, f, lokal, fern, fern_t, fid):
    """Beide Seiten haben seit dem letzten Abgleich geändert. Ergebnis = was hier liegen soll (wird danach hochgeladen, falls anders)."""
    if fern is None:
        return lokal                      # drüben gelöscht, hier geändert: behalten
    if lokal is None:
        return fern                       # hier gelöscht, drüben geändert: zurückholen
    try:
        fern_neuer = float(fern_t or 0) >= f.stat().st_mtime
    except OSError:
        fern_neuer = True
    if pfad.endswith(".json"):
        try:
            basis = _sync_basis(fid)
            neu = sync_mischen(json.loads(basis) if basis else None, json.loads(lokal), json.loads(fern), fern_neuer)
            return json.dumps(neu, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except ValueError:
            pass
    verlierer = lokal if fern_neuer else fern
    ziel = DATEN / "sicherung" / "sync-konflikte" / datetime.now().strftime("%Y-%m-%d-%H%M%S") / pfad
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(verlierer)
    sperre_protokoll(f"Sync: {pfad} auf zwei Geräten geändert, {'neuere von drüben' if fern_neuer else 'diese'} behalten, andere in {ziel.parent.name}")
    return fern if fern_neuer else lokal


def sync_id(master, pfad):
    return hmac.new(master, b"id|" + pfad.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def sync_packen(master, s, pfad, inhalt, t):
    import zlib
    kopf = {"p": pfad, "t": t, "g": s.get("geraetName") or ""}
    if inhalt is None:
        kopf["x"] = 1
    return sync_zu(master, zlib.compress(json.dumps(kopf, ensure_ascii=False).encode("utf-8") + b"\n" + (inhalt or b""), 6))


def sync_entpacken(master, paket):
    import zlib
    roh = zlib.decompress(sync_auf(master, paket))
    kopf, _, inhalt = roh.partition(b"\n")
    k = json.loads(kopf)
    return k, (None if k.get("x") else inhalt)


def sync_jetzt():
    """Ein Abgleich: holen, einspielen, hochladen. Läuft schon einer, passiert nichts. Gibt den Status zurück."""
    if not SYNC_LOCK.acquire(blocking=False):
        return sync_status()
    try:
        s = sync_load()
        if not s.get("token") or s.get("abgemeldet") or not s.get("schluessel"):
            return sync_status()
        master, token = bytes.fromhex(s["schluessel"]), s["token"]
        s.setdefault("dateien", {})
        geholt = hoch = 0
        try:
            for _runde in range(3):
                ae = sx_aenderungen(s, token)
                nach_id = {e.get("id"): p for p, e in s["dateien"].items()}
                lokal = sync_dateien()
                for e in ae.get("dateien") or []:
                    p_alt = nach_id.get(e["id"])
                    if p_alt and int(s["dateien"][p_alt].get("rev") or 0) >= e["rev"]:
                        continue
                    try:
                        kopf, fern = sync_entpacken(master, sx_holen(s, token, e["id"]))
                    except (ValueError, KeyError) as err:
                        sperre_protokoll(f"Sync: Päckchen {e['id'][:8]} nicht lesbar ({err})")
                        continue
                    p = str(kopf.get("p") or "")
                    f = lokal.get(p) or sync_ziel(p)
                    if f is None:
                        continue
                    bek = s["dateien"].get(p) or {}
                    hier = sync_lesen(p, f) if f.exists() else None
                    if hier == fern or _sync_hash(hier) == bek.get("hash"):
                        neu = fern
                    else:
                        neu = sync_konflikt(p, f, hier, fern, kopf.get("t"), e["id"])
                    if neu != hier:
                        if p == "app/automatik.json":
                            with AUTOMATIK_LOCK:
                                sync_schreiben(p, f, neu)
                        else:
                            sync_schreiben(p, f, neu)
                        geholt += 1
                    s["dateien"][p] = {"id": e["id"], "rev": e["rev"], "hash": _sync_hash(fern)}
                    _sync_basis(e["id"], fern if p.endswith(".json") else None)
                liste = ae.get("dateien") or []
                s["rev"] = max([int(s.get("rev") or 0)] + [x["rev"] for x in liste]) if len(liste) >= 2000 else max(int(s.get("rev") or 0), int(ae.get("rev") or 0))
                if geholt:
                    with LOCK:
                        CACHE.clear()
                # Eigene Änderungen hochladen
                konflikt = False
                lokal = sync_dateien()
                for p, f in lokal.items():
                    b = sync_lesen(p, f)
                    bek = s["dateien"].get(p) or {}
                    if b is None or _sync_hash(b) == bek.get("hash"):
                        continue
                    if len(b) > 6 * 1024 * 1024:
                        continue
                    fid = bek.get("id") or sync_id(master, p)
                    try:
                        t = f.stat().st_mtime
                    except OSError:
                        t = time.time()
                    try:
                        r = sx_ablegen(s, token, fid, sync_packen(master, s, p, b, t), bek.get("rev"))
                    except SyncKonflikt:
                        konflikt = True
                        continue
                    s["dateien"][p] = {"id": fid, "rev": r["rev"], "hash": _sync_hash(b)}
                    _sync_basis(fid, b if p.endswith(".json") else None)
                    hoch += 1
                weg = [p for p, bek in s["dateien"].items() if p not in lokal and bek.get("hash") is not None and sync_ziel(p)]
                if len(weg) > 5 and len(weg) > len(lokal) / 2:
                    # Sicherheitsbremse: fehlt plötzlich mehr als die Hälfte (Ordner verschoben, Vault nicht da), nichts löschen
                    sperre_protokoll(f"Sync: {len(weg)} Dateien fehlen hier, drüben wird nichts gelöscht")
                    weg = []
                for p in weg:   # hier gelöscht → drüben auch
                    bek = s["dateien"][p]
                    try:
                        r = sx_ablegen(s, token, bek["id"], sync_packen(master, s, p, None, time.time()), bek.get("rev"))
                    except SyncKonflikt:
                        konflikt = True
                        continue
                    s["dateien"][p] = {"id": bek["id"], "rev": r["rev"], "hash": None}
                    hoch += 1
                sync_save(s)
                if not konflikt:
                    break
            s["zuletzt"], s["fehler"] = datetime.now().astimezone().isoformat(timespec="seconds"), ""
        except ApiFehler as e:
            s = {**sync_load(), "fehler": str(e)}
        sync_save(s)
        SYNC_LAUF.update(zuletzt=time.time(), geholt=geholt, hoch=hoch)
        return {**sync_status(), "geholt": geholt, "hoch": hoch}
    finally:
        SYNC_LOCK.release()


def sync_status():
    s = sync_load()
    return {"an": bool(s.get("token")) and bool(s.get("schluessel")) and not s.get("abgemeldet"), "abgemeldet": bool(s.get("abgemeldet")), "adresse": s.get("adresse") or s.get("testAdresse") or SYNC_STANDARD,
            "sichtbar": True, "art": sync_art(s), "entwickler": ENTWICKLER,
            "dienstDa": bool(SYNC_STANDARD or ENTWICKLER or s.get("testAdresse")), "driveDa": drive_da(),   # Release: Weg sichtbar, aber erst wählbar, wenn er online ist
            "driveAngemeldet": sync_art(s) == "drive" and bool((s.get("drive") or {}).get("refresh")) and not s.get("abgemeldet"),
            "standard": SYNC_STANDARD, "email": s.get("email") or "", "geraetName": s.get("geraetName") or "", "zuletzt": s.get("zuletzt") or "",
            "fehler": s.get("fehler") or "", "pro": s.get("pro"), "proBis": s.get("proBis"), "dateien": len(s.get("dateien") or {})}


def gemeinsam_anfrage(data):
    """Belohnung „Gemeinsam“ (11.11.2026) am Mac/Windows: reicht an den BlockEm-Server weiter (am Handy macht das die App, CockpitApp.gmAnfrage).
    Anonym: nur die Zufalls-ID des Geräts, kein Konto-Token."""
    was, gid = str(data.get("was") or "stand"), str(data.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", gid):
        raise ApiFehler("Ungültige Anfrage.")
    if was == "tag":
        return sync_http("POST", "/v1/gemeinsam/tag", {"id": gid, "tag": str(data.get("tag") or ""), "sauber": data.get("sauber") is True}, adresse=SYNC_STANDARD, timeout=15)
    if was == "stimme":
        return sync_http("POST", "/v1/gemeinsam/stimme", {"id": gid, "org": str(data.get("org") or "")}, adresse=SYNC_STANDARD, timeout=15)
    return sync_http("GET", "/v1/gemeinsam?id=" + gid, adresse=SYNC_STANDARD, timeout=15)


def sync_begleitung_koppeln(data):
    """Plan B (john 09.11.2026): Vertrauensperson-Begleitung dieses Handys mit dem Sync-Konto verbinden, damit Laptop/PC sie sehen.
    Ohne BlockEm-Sync-Konto passiert nichts (ohneKonto)."""
    s = sync_load()
    if not s.get("token") or s.get("abgemeldet") or sync_art(s) != "dienst":
        return {"ok": False, "ohneKonto": True}
    if data.get("holen"):   # 09.11. 10:38: Handy ohne Begleitung (neu installiert / stille Begleitung für Plan B) holt den Schlüssel übers Konto
        return sync_http("POST", "/v1/konto/begleitung-holen", {"play": bool(data.get("play"))}, token=s["token"])
    return sync_http("POST", "/v1/konto/begleitung", {"begleitung": str(data.get("begleitung") or ""), "play": bool(data.get("play"))}, token=s["token"])


def _sync_angemeldet(adresse, email, geraet_name, antwort, master):
    alt = sync_load()
    neu = {"art": "dienst", "adresse": adresse, "email": email, "token": antwort["token"], "geraet": antwort["geraet"], "geraetName": geraet_name,
           "schluessel": master.hex(), "pro": antwort.get("pro"), "proBis": antwort.get("proBis"), "rev": 0, "dateien": {}}
    if alt.get("testAdresse"):
        neu["testAdresse"] = alt["testAdresse"]   # 1.0.14: Test-Kanal-Adresse bleibt nach dem Anmelden
    if alt.get("email") == email and alt.get("schluessel") == master.hex() and alt.get("adresse") == adresse:
        neu.update(rev=alt.get("rev") or 0, dateien=alt.get("dateien") or {})   # gleiches Konto wieder angemeldet: Stand behalten
    sync_save(neu)


def sync_geraet_name(n):
    n = re.sub(r"[\x00-\x1f]", "", str(n or "")).strip()[:40]
    return n or ("Handy" if MODUS == "handy" else "Windows-PC" if sys.platform == "win32" else "Mac" if sys.platform == "darwin" else "Computer")


def sync_aktion(d):
    """POST /api/sync: registrieren, anmelden, notfall, jetzt, konto, geraetEntfernen, passwort, abmelden, kontoLoeschen."""
    a = str(d.get("aktion") or "")
    s = sync_load()
    if a.startswith("koppeln"):
        return sync_koppeln(a, d, s)
    if a == "driveAnmelden":
        return drive_anmelden(d)
    if a == "driveFertig":   # Handy: Rückweg von Google kam über die App
        ok, text = drive_zurueck(d.get("query"))
        if not ok:
            raise ApiFehler(text)
        return {**sync_status(), "hinweis": text}
    if a == "driveStart":
        return drive_start()
    if a == "testAdresse":   # 1.0.14: Test-Kanal am Handy: Sync-Dienst von Hand, bevor SYNC_STANDARD fest eingebaut ist
        v = str(d.get("adresse") or "").strip().rstrip("/")
        if v:
            v = sync_adresse(v)
            s["testAdresse"] = v
        else:
            s.pop("testAdresse", None)
        sync_save(s)
        return sync_status()
    if a in ("registrieren", "anmelden", "notfall"):
        adresse, email = sync_adresse(d.get("adresse")), str(d.get("email") or "").strip().lower()
        pw, name = str(d.get("passwort") or ""), sync_geraet_name(d.get("geraet"))
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise ApiFehler("Bitte eine gültige E-Mail-Adresse eintragen.")
        if a != "anmelden" and len(pw) < 10:
            raise ApiFehler("Das Passwort braucht mindestens 10 Zeichen.")
        if a == "registrieren":
            master, kdf, nkdf, code = secrets.token_bytes(32), sync_neuer_kdf(), sync_neuer_kdf(60000), sync_notfall_code()
            auth, kek = sync_schluessel(pw, kdf)
            nauth, nkek = sync_schluessel(sync_notfall_norm(code), nkdf)
            r = sync_http("POST", "/v1/registrieren", {"email": email, "auth": auth, "kdf": kdf, "box": sync_box(kek, master), "geraet": name,
                                                       "notfall": {"auth": nauth, "kdf": nkdf, "box": sync_box(nkek, master)}}, adresse=adresse)
            _sync_angemeldet(adresse, email, name, r, master)
            sperre_protokoll(f"Sync: Konto {email} angelegt, dieses Gerät heißt „{name}“")
            return {**sync_jetzt(), "notfallCode": code}
        if a == "anmelden":
            kdf = sync_http("POST", "/v1/vorbereiten", {"email": email}, adresse=adresse)["kdf"]
            auth, kek = sync_schluessel(pw, kdf)
            r = sync_http("POST", "/v1/anmelden", {"email": email, "auth": auth, "geraet": name}, adresse=adresse)
            _sync_angemeldet(adresse, email, name, r, sync_aus_box(kek, r["box"]))
            sperre_protokoll(f"Sync: angemeldet als {email} („{name}“)")
            return sync_jetzt()
        # Passwort vergessen: Notfall-Code + neues Passwort
        nkdf = sync_http("POST", "/v1/vorbereiten", {"email": email, "notfall": True}, adresse=adresse)["kdf"]
        nauth, nkek = sync_schluessel(sync_notfall_norm(d.get("code")), nkdf)
        r = sync_http("POST", "/v1/notfall", {"email": email, "auth": nauth, "geraet": name}, adresse=adresse)
        master = sync_aus_box(nkek, r["notfallBox"])
        kdf = sync_neuer_kdf()
        auth, kek = sync_schluessel(pw, kdf)
        sync_http("POST", "/v1/passwort", {"auth": auth, "kdf": kdf, "box": sync_box(kek, master)}, token=r["token"], adresse=adresse)
        _sync_angemeldet(adresse, email, name, r, master)
        sperre_protokoll(f"Sync: Passwort mit Notfall-Code neu gesetzt ({email})")
        return {**sync_jetzt(), "hinweis": "Neues Passwort gesetzt. Alle anderen Geräte sind abgemeldet und brauchen das neue Passwort."}
    if sync_art(s) == "drive" and a in ("abmelden", "kontoLoeschen", "konto", "geraetEntfernen", "passwort"):
        return drive_aktion(a, s)
    if not s.get("token"):
        raise ApiFehler("Dieses Gerät ist nicht beim Sync angemeldet.")
    if a == "jetzt":
        return sync_jetzt()
    if a == "konto":
        k = sync_http("GET", "/v1/konto", token=s["token"])
        s.update(pro=k.get("pro"), proBis=k.get("proBis"))
        sync_save(s)
        return {**sync_status(), "konto": k}
    if a == "geraetEntfernen":
        gid = str(d.get("id") or "")
        if not re.match(r"^[0-9a-f]{16}$", gid):
            raise ApiFehler("Unbekanntes Gerät.")
        sync_http("DELETE", "/v1/geraet/" + gid, token=s["token"])
        return sync_aktion({"aktion": "konto"})
    if a == "passwort":
        alt, neu = str(d.get("alt") or ""), str(d.get("neu") or "")
        if len(neu) < 10:
            raise ApiFehler("Das neue Passwort braucht mindestens 10 Zeichen.")
        kdf_alt = sync_http("POST", "/v1/vorbereiten", {"email": s["email"]})["kdf"]
        auth_alt, _ = sync_schluessel(alt, kdf_alt)
        kdf = sync_neuer_kdf()
        auth, kek = sync_schluessel(neu, kdf)
        sync_http("POST", "/v1/passwort", {"alt": auth_alt, "auth": auth, "kdf": kdf, "box": sync_box(kek, bytes.fromhex(s["schluessel"])),
                                           "andereAbmelden": bool(d.get("andereAbmelden"))}, token=s["token"])
        return {**sync_status(), "hinweis": "Passwort geändert."}
    if a in ("abmelden", "kontoLoeschen"):
        if a == "kontoLoeschen":
            kdf = sync_http("POST", "/v1/vorbereiten", {"email": s["email"]})["kdf"]
            auth, _ = sync_schluessel(str(d.get("passwort") or ""), kdf)
            sync_http("POST", "/v1/konto-loeschen", {"auth": auth}, token=s["token"])
        else:
            try:
                sync_http("POST", "/v1/abmelden", {}, token=s["token"], timeout=15)
            except ApiFehler:
                pass   # offline: hier trotzdem abmelden
        sync_save({"adresse": s.get("adresse") or ""})
        if SYNC_BASIS.is_dir():
            ziel = DATEN / "sicherung" / f"sync-basis-{datetime.now():%Y%m%d%H%M%S}"
            ziel.parent.mkdir(parents=True, exist_ok=True)
            os.replace(SYNC_BASIS, ziel)
        sperre_protokoll("Sync: " + ("Konto und alle Daten im Sync-Dienst gelöscht" if a == "kontoLoeschen" else "abgemeldet"))
        return {**sync_status(), "hinweis": "Konto gelöscht. Deine Daten auf diesem Gerät bleiben." if a == "kontoLoeschen" else "Abgemeldet. Deine Daten auf diesem Gerät bleiben."}
    raise ApiFehler("Unbekannte Sync-Aktion")


def sync_waechter():
    """Computer: jede Minute abgleichen, solange angemeldet (im Handy stößt die Oberfläche den Abgleich an)."""
    time.sleep(15)
    while True:
        try:
            if sync_load().get("token"):
                with leistung("Fokus Sync"):
                    sync_jetzt()
        except Exception as e:
            SYNC_LAUF["fehler"] = str(e)
        time.sleep(60)


# ---------- Sync-Wege (04.11.2026): BlockEm Sync (unser Dienst) oder Google Drive des Nutzers; Geräte per QR-Code verbinden ----------
# Beide Wege nutzen dieselben verschlüsselten Päckchen (sync_packen). Bei Drive liegen sie im versteckten App-Ordner (appDataFolder) im
# Google Drive des Nutzers; Name = Päckchen-ID, „rev“ = Drive-Version. Wir (der Anbieter) haben dabei gar keine Daten.
# Google-Anmeldung: OAuth für Desktop-Apps mit PKCE. Am Computer kommt Google über http://127.0.0.1:<port>/api/drive/zurueck zurück,
# im Handy öffnet die App kurz einen eigenen Rückweg auf 127.0.0.1 (CockpitApp.oauthStart) und reicht den Code weiter.
# Die Client-ID gehört zur App (Google-Cloud-Projekt von BlockEm); ohne sie ist der Weg sichtbar, aber noch nicht wählbar.
DRIVE_CLIENT = {"id": os.environ.get("COCKPIT_DRIVE_ID") or CFG.get("driveClientId") or "",
                "secret": os.environ.get("COCKPIT_DRIVE_SECRET") or CFG.get("driveClientSecret") or ""}
DRIVE_API = os.environ.get("COCKPIT_DRIVE_API") or "https://www.googleapis.com"   # Tests: Ersatz-Drive
DRIVE_TOKEN_URL = os.environ.get("COCKPIT_DRIVE_TOKEN") or "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "openid email https://www.googleapis.com/auth/drive.appdata"
DRIVE_KONTO = "clearmind-konto"          # Merker im App-Ordner: hier gibt es schon einen Schlüssel → neues Gerät per QR verbinden
DRIVE = {"anmeldung": {}, "liste": None, "token": "", "ablauf": 0}
KOPPLUNG = {}                            # id -> {"k": Schlüssel aus dem QR-Code, "art", "adresse", "bis"}
KOPPLUNG_SEK = 600


def sync_art(s=None):
    return (s if s is not None else sync_load()).get("art") or "dienst"


def drive_da():
    return bool(DRIVE_CLIENT["id"])


def drive_token(neu=False):
    s = sync_load()
    d = s.get("drive") or {}
    if not neu and DRIVE["token"] and DRIVE["ablauf"] > time.time() + 60:
        return DRIVE["token"]
    if not d.get("refresh"):
        raise ApiFehler("Dieses Gerät ist nicht mit Google Drive verbunden.", 403)
    try:
        code, j = g_http("POST", DRIVE_TOKEN_URL, form={"client_id": DRIVE_CLIENT["id"], "client_secret": DRIVE_CLIENT["secret"],
                                                        "refresh_token": d["refresh"], "grant_type": "refresh_token"})
    except RuntimeError:
        raise ApiFehler("Google ist gerade nicht erreichbar. Bist du online?", 503)
    if code != 200:
        if j.get("error") == "invalid_grant":
            s["abgemeldet"] = True
            sync_save(s)
            raise ApiFehler("Die Google-Anmeldung gilt nicht mehr (zurückgezogen oder abgelaufen). Bitte neu mit Google anmelden.", 403)
        raise ApiFehler(g_fehler(code, j).replace("Google Calendar API", "Google Drive API"), 502)
    DRIVE.update(token=j["access_token"], ablauf=time.time() + int(j.get("expires_in", 3600)))
    return DRIVE["token"]


def drive_http(methode, pfad, body=None, kopf=None, roh=False):
    """Anfrage an Google Drive mit dem Zugang dieses Geräts (bei abgelaufenem Zugang einmal erneuern)."""
    for versuch in (0, 1):
        h = {**(kopf or {}), "Authorization": "Bearer " + drive_token(neu=bool(versuch))}
        try:
            return sync_http(methode, pfad, body, adresse=DRIVE_API, kopf=h, roh=roh)
        except ApiFehler as e:
            if versuch or e.code != 403:
                raise


def drive_liste():
    """Alle Dateien im App-Ordner: Name -> {"id", "v" (Version), "t" (geändert)}."""
    out, seite = {}, ""
    while True:
        j = drive_http("GET", "/drive/v3/files?spaces=appDataFolder&pageSize=1000&fields=" + quote("nextPageToken,files(id,name,version,modifiedTime)")
                       + ("&pageToken=" + quote(seite) if seite else ""))
        for f in j.get("files") or []:
            alt = out.get(f["name"])
            if not alt or int(f.get("version") or 0) > alt["v"]:
                out[f["name"]] = {"id": f["id"], "v": int(f.get("version") or 0), "t": f.get("modifiedTime") or ""}
        seite = j.get("nextPageToken") or ""
        if not seite:
            break
    DRIVE["liste"] = out
    return out


def drive_hochladen(name, daten, drive_id=None):
    if drive_id:   # PATCH als POST mit Override: die Android-Brücke (HttpURLConnection) kennt kein PATCH
        j = drive_http("POST", f"/upload/drive/v3/files/{drive_id}?uploadType=media&fields=id,version", daten,
                       kopf={"X-HTTP-Method-Override": "PATCH", "Content-Type": "application/octet-stream"})
    else:
        g = "cm" + secrets.token_hex(12)
        meta = json.dumps({"name": name, "parents": ["appDataFolder"]}).encode()
        body = (f"--{g}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta
                + f"\r\n--{g}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + daten + f"\r\n--{g}--".encode())
        j = drive_http("POST", "/upload/drive/v3/files?uploadType=multipart&fields=id,version", body, kopf={"Content-Type": f"multipart/related; boundary={g}"})
    if DRIVE["liste"] is not None:
        DRIVE["liste"][name] = {"id": j["id"], "v": int(j.get("version") or 0), "t": datetime.now(timezone.utc).isoformat()}
    return j


def drive_loeschen(drive_id):
    try:
        drive_http("DELETE", f"/drive/v3/files/{drive_id}")
    except ApiFehler as e:
        if e.code != 502:
            raise


def drive_pruef(master):
    return hmac.new(master, b"clearmind-konto", hashlib.sha256).hexdigest()[:24]


# Transport für sync_jetzt: dieselben drei Schritte für beide Wege
def sx_aenderungen(s, token):
    if sync_art(s) == "drive":
        liste = drive_liste()
        jetzt_t = time.time()
        for n, x in list(liste.items()):   # liegengebliebene QR-Behälter wegräumen
            if n.startswith("kopplung-"):
                try:
                    alt = jetzt_t - datetime.fromisoformat(x["t"].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    alt = 0
                if alt > KOPPLUNG_SEK * 2:
                    drive_loeschen(x["id"])
        return {"rev": 0, "dateien": [{"id": n, "rev": x["v"]} for n, x in liste.items() if re.match(r"^[0-9a-f]{32}$", n)]}
    return sync_http("GET", f"/v1/aenderungen?seit={int(s.get('rev') or 0)}", token=token)


def sx_holen(s, token, fid):
    if sync_art(s) == "drive":
        x = (DRIVE["liste"] or {}).get(fid)
        if not x:
            raise ValueError("in Drive nicht mehr da")
        return drive_http("GET", f"/drive/v3/files/{x['id']}?alt=media", roh=True)
    return sync_http("GET", "/v1/datei/" + fid, token=token, roh=True)


def sx_ablegen(s, token, fid, paket, rev):
    if sync_art(s) == "drive":
        x = (DRIVE["liste"] if DRIVE["liste"] is not None else drive_liste()).get(fid)
        if (x["v"] if x else 0) != int(rev or 0):
            raise SyncKonflikt(x["v"] if x else 0)
        j = drive_hochladen(fid, paket, x["id"] if x else None)
        return {"rev": int(j.get("version") or 0)}
    return sync_http("PUT", "/v1/datei/" + fid, paket, token=token, kopf={"If-Match": str(int(rev or 0))})


# Google-Anmeldung für Drive
def drive_umleitung(d):
    if MODUS == "handy":
        u = str(d.get("umleitung") or "")
        if not re.match(r"^http://127\.0\.0\.1:\d{2,5}/$", u):
            raise ApiFehler("Die App kann die Google-Anmeldung gerade nicht öffnen. Bitte die App aktualisieren.")
        return u
    return f"http://127.0.0.1:{CFG['port']}/api/drive/zurueck"


def drive_anmelden(d):
    import base64
    if not drive_da():
        raise ApiFehler("Google Drive ist in dieser Version noch nicht freigeschaltet. Nimm vorerst BlockEm Sync.")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state, umleitung = secrets.token_urlsafe(16), drive_umleitung(d)
    DRIVE["anmeldung"] = {"state": state, "verifier": verifier, "umleitung": umleitung, "bis": time.time() + 900, "geraet": sync_geraet_name(d.get("geraet"))}
    from urllib.parse import urlencode
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": DRIVE_CLIENT["id"], "redirect_uri": umleitung, "response_type": "code", "scope": DRIVE_SCOPE,
        "access_type": "offline", "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256", "state": state})}


def drive_zurueck(query):
    """Code von Google gegen dauerhaften Zugang tauschen. (ok, text)"""
    import base64
    q = {k: v[0] for k, v in parse_qs(str(query or "").lstrip("?")).items()}
    an = DRIVE.get("anmeldung") or {}
    if q.get("error"):
        return False, "Anmeldung abgebrochen." if q["error"] == "access_denied" else f"Google meldet: {q['error']}"
    if not an or q.get("state") != an.get("state") or time.time() > an.get("bis", 0):
        return False, "Die Anmeldung ist abgelaufen. Bitte in BlockEm nochmal auf „Mit Google anmelden“ tippen."
    try:
        code, j = g_http("POST", DRIVE_TOKEN_URL, form={"client_id": DRIVE_CLIENT["id"], "client_secret": DRIVE_CLIENT["secret"], "code": q.get("code", ""),
                                                        "code_verifier": an["verifier"], "grant_type": "authorization_code", "redirect_uri": an["umleitung"]})
    except RuntimeError:
        return False, "Google ist gerade nicht erreichbar."
    DRIVE["anmeldung"] = {}
    if code != 200 or not j.get("refresh_token"):
        return False, g_fehler(code, j) if code != 200 else "Google hat keinen dauerhaften Zugang geschickt. Bitte nochmal anmelden."
    email = ""
    try:
        teil = j.get("id_token", "").split(".")[1]
        email = json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4))).get("email", "")
    except Exception:
        pass
    alt = sync_load()
    s = {"art": "drive", "drive": {"refresh": j["refresh_token"], "email": email}, "email": email, "geraetName": an.get("geraet") or sync_geraet_name("")}
    if alt.get("art") == "drive" and alt.get("schluessel") and (alt.get("drive") or {}).get("email") == email:
        s.update({k: alt[k] for k in ("schluessel", "dateien", "rev", "geraetName") if k in alt}, token="drive")   # gleiches Konto: Stand behalten
    sync_save(s)
    DRIVE.update(token=j["access_token"], ablauf=time.time() + int(j.get("expires_in", 3600)), liste=None)
    sperre_protokoll(f"Sync: mit Google Drive angemeldet ({email or 'ohne E-Mail'})")
    return True, f"Mit Google verbunden{(' (' + email + ')') if email else ''}. Du kannst zurück zu BlockEm."


def drive_start():
    """Erstes Gerät mit Drive: Schlüssel anlegen. Liegt im Drive schon ein BlockEm-Konto, muss dieses Gerät per QR-Code dazu."""
    s = sync_load()
    if sync_art(s) != "drive" or not (s.get("drive") or {}).get("refresh"):
        raise ApiFehler("Bitte zuerst mit Google anmelden.")
    if s.get("token"):
        return sync_jetzt()
    if DRIVE_KONTO in drive_liste():
        raise ApiFehler("In diesem Google Drive gibt es schon BlockEm-Daten. Verbinde dieses Gerät per QR-Code mit einem deiner anderen Geräte.", 409)
    master = secrets.token_bytes(32)
    drive_hochladen(DRIVE_KONTO, json.dumps({"v": 1, "pruef": drive_pruef(master)}).encode())
    s.update(token="drive", schluessel=master.hex(), rev=0, dateien={}, abgemeldet=False)
    sync_save(s)
    sperre_protokoll("Sync: neues BlockEm-Konto in Google Drive angelegt")
    return sync_jetzt()


# Geräte per QR-Code verbinden. Der QR-Code trägt eine zufällige ID und einen Schlüssel; nur wer ihn sieht, kann den Behälter öffnen.
# Richtung A: das neue Gerät zeigt den Code, ein angemeldetes scannt ihn und legt den Behälter ab (koppelnSenden).
# Richtung B: ein angemeldetes Gerät zeigt den Code mit fertigem Behälter (koppelnAnbieten), das neue scannt ihn (koppelnEinlesen).
def _b64u(b):
    import base64
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_auf(t):
    import base64
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


def koppel_qr(kid, k, art, adresse=""):
    return "clearmind:" + _b64u(json.dumps({"v": 1, "a": art, "i": kid, "k": _b64u(k), "u": adresse}, separators=(",", ":")).encode())


# 1.0.17 (john): Code zum Eintippen, falls die Kamera nicht geht. 12 Zeichen (60 Bit), ID und Schlüssel werden daraus abgeleitet,
# der Dienst sieht nur die ID. Abholen geht nur einmal und nur 10 Minuten lang, Raten ist damit aussichtslos.
KOPPEL_ZEICHEN = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def koppel_code_neu():
    c = "".join(secrets.choice(KOPPEL_ZEICHEN) for _ in range(12))
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def koppel_aus_code(code):
    c = re.sub(r"[^0-9A-Z]", "", str(code or "").upper())   # Bindestriche und Leerzeichen egal
    if len(c) != 12 or any(z not in KOPPEL_ZEICHEN for z in c):
        return None
    kid = hashlib.sha256(b"blockem-kopplung-id|" + c.encode()).hexdigest()[:32]
    k = pbkdf2_sha256(c.encode(), b"blockem-kopplung", 60000)
    return kid, k


def koppel_lesen(qr, art=None):
    t = str(qr or "").strip()
    if not t.startswith("clearmind:"):
        x = koppel_aus_code(t)
        if x:
            return {"i": x[0], "k": x[1], "a": art or "dienst", "u": ""}
    try:
        if not t.startswith("clearmind:"):
            raise ValueError
        j = json.loads(_b64u_auf(t[10:]))
        k, kid, art = _b64u_auf(j["k"]), str(j["i"]), j.get("a")
        if len(k) != 32 or not re.match(r"^[0-9a-f]{32}$", kid) or art not in ("dienst", "drive"):
            raise ValueError
        return {"i": kid, "k": k, "a": art, "u": str(j.get("u") or "")}
    except (ValueError, KeyError, TypeError):
        raise ApiFehler("Das ist kein gültiger BlockEm-Code. Bitte den QR-Code scannen oder die 12 Zeichen genau so eintippen, wie sie auf dem anderen Gerät stehen.")


def koppel_behaelter(s, k):
    import base64
    inhalt = {"m": s["schluessel"], "e": s.get("email") or "", "u": s.get("adresse") or "", "a": sync_art(s)}
    return base64.b64encode(sync_zu(k, json.dumps(inhalt).encode())).decode()


def koppel_ablegen(s, kid, k):
    """Angemeldetes Gerät: Behälter für das neue Gerät hinterlegen."""
    box = koppel_behaelter(s, k)
    if sync_art(s) == "drive":
        drive_hochladen("kopplung-" + kid, box.encode())
    else:
        sync_http("POST", "/v1/kopplung/" + kid, {"box": box, "geraet": "Neues Gerät"}, token=s["token"])


def koppel_abholen(kid, k, art, adresse, name):
    """Neues Gerät: Behälter holen und öffnen. None = noch nichts da."""
    import base64
    s = sync_load()
    if art == "drive":
        if sync_art(s) != "drive" or not (s.get("drive") or {}).get("refresh"):
            raise ApiFehler("Dieses Gerät ist noch nicht mit Google angemeldet.")
        x = drive_liste().get("kopplung-" + kid)
        if not x:
            return None
        box = drive_http("GET", f"/drive/v3/files/{x['id']}?alt=media", roh=True).decode()
        drive_loeschen(x["id"])
        r = None
    else:
        adresse = sync_adresse(adresse)
        try:
            r = sync_http("POST", f"/v1/kopplung/{kid}/abholen", {"geraet": name}, adresse=adresse)
        except ApiFehler as e:
            if "Noch nichts da" in str(e) or e.code == 502 and "404" in str(e):
                return None
            raise
        box = r["box"]
    try:
        inhalt = json.loads(sync_auf(k, base64.b64decode(box)))
        master = bytes.fromhex(inhalt["m"])
    except (ValueError, KeyError):
        raise ApiFehler("Der Code passt nicht. Bitte noch einmal scannen.", 403)
    if art == "drive":
        konto = drive_liste().get(DRIVE_KONTO)
        if konto:
            try:
                ok = json.loads(drive_http("GET", f"/drive/v3/files/{konto['id']}?alt=media", roh=True)).get("pruef") == drive_pruef(master)
            except ValueError:
                ok = False
            if not ok:
                raise ApiFehler("Das andere Gerät nutzt ein anderes Google-Konto. Melde beide Geräte mit demselben Google-Konto an.", 409)
        if s.get("schluessel") != master.hex():
            s.update(rev=0, dateien={})
        s.update(token="drive", schluessel=master.hex(), geraetName=name, abgemeldet=False)
        sync_save(s)
    else:
        _sync_angemeldet(adresse or inhalt.get("u"), r.get("email") or inhalt.get("e"), name, r, master)
        s2 = sync_load()
        s2["art"] = "dienst"
        sync_save(s2)
    sperre_protokoll(f"Sync: dieses Gerät („{name}“) per QR-Code verbunden ({'Google Drive' if art == 'drive' else 'BlockEm Sync'})")
    return sync_jetzt()


def drive_aktion(a, s):
    """Konto-Aktionen bei Google Drive: es gibt kein Konto bei uns, nur den App-Ordner im Drive des Nutzers."""
    d = s.get("drive") or {}
    if a == "konto":
        return {**sync_status(), "konto": {"email": d.get("email") or "", "geraete": [], "art": "drive"}}
    if a in ("passwort", "geraetEntfernen"):
        raise ApiFehler("Bei Google Drive gibt es kein BlockEm-Passwort. Geräte trennst du hier mit „Dieses Gerät abmelden“.")
    if a == "kontoLoeschen":
        for x in drive_liste().values():
            drive_loeschen(x["id"])
    if d.get("refresh"):
        try:
            g_http("POST", "https://oauth2.googleapis.com/revoke", form={"token": d["refresh"]}, timeout=10)
        except Exception:
            pass
    DRIVE.update(token="", ablauf=0, liste=None)
    sync_save({"art": "drive"})
    if SYNC_BASIS.is_dir():
        ziel = DATEN / "sicherung" / f"sync-basis-{datetime.now():%Y%m%d%H%M%S}"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        os.replace(SYNC_BASIS, ziel)
    sperre_protokoll("Sync: " + ("alle BlockEm-Daten in Google Drive gelöscht" if a == "kontoLoeschen" else "von Google Drive abgemeldet"))
    return {**sync_status(), "hinweis": "Deine BlockEm-Daten in Google Drive sind gelöscht. Die Daten auf diesem Gerät bleiben." if a == "kontoLoeschen"
            else "Abgemeldet. Deine Daten und Regeln auf diesem Gerät bleiben."}


def sync_koppeln(a, d, s):
    name = sync_geraet_name(d.get("geraet") or s.get("geraetName"))
    for kid in [i for i, x in KOPPLUNG.items() if x["bis"] < time.time()]:
        KOPPLUNG.pop(kid, None)
    if a == "koppelnZeigen":          # neues Gerät zeigt seinen Code
        art = "drive" if d.get("art") == "drive" else "dienst"
        adresse = sync_adresse(d.get("adresse")) if art == "dienst" else ""
        if art == "drive" and not (sync_art(s) == "drive" and (s.get("drive") or {}).get("refresh")):
            raise ApiFehler("Bitte zuerst mit Google anmelden (dasselbe Google-Konto wie auf deinen anderen Geräten).")
        code = koppel_code_neu()
        kid, k = koppel_aus_code(code)
        KOPPLUNG[kid] = {"k": k, "art": art, "adresse": adresse, "bis": time.time() + KOPPLUNG_SEK}
        return {"qr": koppel_qr(kid, k, art), "code": code, "id": kid, "sekunden": KOPPLUNG_SEK}
    if a == "koppelnWarten":          # neues Gerät fragt nach, ob der Behälter da ist
        x = KOPPLUNG.get(str(d.get("id") or ""))
        if not x:
            raise ApiFehler("Der Code ist abgelaufen. Bitte einen neuen zeigen lassen.", 410)
        r = koppel_abholen(d["id"], x["k"], x["art"], x["adresse"], name)
        if r is None:
            return {"warten": True}
        KOPPLUNG.pop(d["id"], None)
        return {**r, "verbunden": True}
    if a == "koppelnEinlesen":        # neues Gerät hat den Code eines angemeldeten Geräts gescannt
        q = koppel_lesen(d.get("qr"), "drive" if d.get("art") == "drive" else "dienst")
        for _ in range(3):
            r = koppel_abholen(q["i"], q["k"], q["a"], q["u"], name)
            if r is not None:
                return {**r, "verbunden": True}
            time.sleep(2)
        raise ApiFehler("Der Code ist abgelaufen oder schon benutzt. Bitte auf dem anderen Gerät einen neuen zeigen lassen.", 410)
    # ab hier: dieses Gerät ist angemeldet und nimmt ein neues auf
    if not s.get("token") or s.get("abgemeldet") or not s.get("schluessel"):
        raise ApiFehler("Dieses Gerät ist selbst noch nicht verbunden.")
    if a == "koppelnSenden":          # Code des neuen Geräts gescannt
        q = koppel_lesen(d.get("qr"), sync_art(s))
        if q["a"] != sync_art(s):
            raise ApiFehler("Das andere Gerät hat " + ("Google Drive" if q["a"] == "drive" else "BlockEm Sync") + " gewählt, dieses nutzt "
                            + ("Google Drive" if sync_art(s) == "drive" else "BlockEm Sync") + ". Bitte auf dem neuen Gerät denselben Weg wählen.")
        koppel_ablegen(s, q["i"], q["k"])
        sperre_protokoll("Sync: neues Gerät per QR-Code aufgenommen")
        return {**sync_status(), "hinweis": "Erledigt. Das neue Gerät holt sich jetzt deine Daten."}
    if a == "koppelnAnbieten":        # dieses Gerät zeigt einen Code mit fertigem Behälter
        code = koppel_code_neu()
        kid, k = koppel_aus_code(code)
        koppel_ablegen(s, kid, k)
        return {"qr": koppel_qr(kid, k, sync_art(s), s.get("adresse") or ""), "code": code, "id": kid, "sekunden": KOPPLUNG_SEK}
    raise ApiFehler("Unbekannte Sync-Aktion")


# ---------- Marktreif: Zeitleiste „Mein Tag“ und Fokus, der den Plan kennt ----------
# Die Aufgaben für heute bekommen feste Zeitblöcke in den Lücken zwischen den Kalender-Terminen (Tagesrahmen aus den Einstellungen).
# Einmal gesetzte Blöcke bleiben stehen; neue Aufgaben kommen in die nächste freie Lücke. Ist der Tag bestätigt („Passt so“),
# startet zu Beginn jedes Blocks der Fokus von selbst (mit Sperre), 3 Minuten vorher kommt eine Mitteilung.
# automatik.json "zeitplan": {"datum", "bloecke": {aufgabe klein: {"text", "projekt", "start": "HH:MM", "min", "fest"}}, "bestaetigt",
#   "gestartet": [...], "erinnert": [...], "uebersprungen": [...]}
ZP_PUFFER = 5           # Minuten Luft zwischen zwei Blöcken
ZP_START_FENSTER = 10   # so lange nach Blockbeginn startet der Fokus noch von selbst


def zp_zeit(tag, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(tag, datetime.min.time()).astimezone().replace(hour=h, minute=m)


def zp_laden(a=None):
    a = automatik_load() if a is None else a
    zp = a.get("zeitplan") or {}
    if zp.get("datum") != date.today().isoformat():
        zp = {"datum": date.today().isoformat(), "bloecke": {}, "bestaetigt": False, "gestartet": [], "erinnert": [], "uebersprungen": []}
    for k in ("gestartet", "erinnert", "uebersprungen"):
        zp.setdefault(k, [])
    zp.setdefault("bloecke", {})
    return zp


def zp_speichern(zp):
    with AUTOMATIK_LOCK:
        a = automatik_load()
        a["zeitplan"] = zp
        automatik_save(a)


def zp_dauer(t):
    """Minuten für eine Aufgabe: Rest laut Stundenangabe, ohne Angabe 1 Std; Blöcke zwischen 15 Min und 4 Std."""
    std = t.get("rest") if t.get("rest") is not None else t.get("std")
    m = round((std if std else 1) * 60 / 5) * 5
    return max(15, min(240, m))


def zp_platz(belegt, ab, bis, minuten):
    """Erste Lücke ab „ab“ bis „bis“, in die „minuten“ passen (belegt = [(start, ende)])."""
    pos = ab
    for s, e in sorted(belegt):
        if e + timedelta(minutes=ZP_PUFFER) <= pos:
            continue
        if s - pos >= timedelta(minutes=minuten):
            return pos
        pos = max(pos, e + timedelta(minutes=ZP_PUFFER))
    return pos if bis - pos >= timedelta(minutes=minuten) else None


def zeitplan(out, speichern=True):
    """Zeitleiste für heute (für die Oberfläche): Termine, Blöcke für die Aufgaben von heute, Fokus-Sessions."""
    if not automatik_an("zeitleiste"):
        return None
    heute = date.today()
    jetzt = datetime.now().astimezone().replace(second=0, microsecond=0)
    ke = kal_einstellung()
    von, bis = zp_zeit(heute, f"{ke['von']:02d}:00"), zp_zeit(heute, "00:00") + timedelta(hours=ke["bis"])
    termine = []
    if automatik_an("kalender") and any(k.get("an", True) and k.get("url") for k in ke["liste"]):
        termine = kal_termine(heute, ke)[0]
    zp = zp_laden()
    aufgaben = {t["text"].lower(): t for t in out.get("heute", [])}
    vorher = json.dumps(zp["bloecke"], sort_keys=True)
    zp["bloecke"] = {k: v for k, v in zp["bloecke"].items() if k in aufgaben}
    belegt = [(x["start"], x["ende"]) for x in termine if not x["ganz"]]
    # Feste Blöcke und Termine haben Vorrang: noch nicht begonnene automatische Blöcke, die damit kollidieren, werden neu verteilt
    fest = belegt + [(zp_zeit(heute, b["start"]), zp_zeit(heute, b["start"]) + timedelta(minutes=b["min"])) for b in zp["bloecke"].values() if b.get("fest")]
    for k, b in list(zp["bloecke"].items()):
        s0 = zp_zeit(heute, b["start"])
        e0 = s0 + timedelta(minutes=b["min"])
        if not b.get("fest") and s0 > jetzt and k not in zp["gestartet"] and any(s0 < e and s < e0 for s, e in fest):
            del zp["bloecke"][k]
    for b in zp["bloecke"].values():
        s = zp_zeit(heute, b["start"])
        belegt.append((s, s + timedelta(minutes=b["min"])))
    ab = max(von, jetzt + timedelta(minutes=(5 - jetzt.minute % 5) % 5))
    passt_nicht = []
    for k, t in aufgaben.items():
        if t["done"] or k in zp["bloecke"] or k in zp["uebersprungen"]:
            continue
        m = zp_dauer(t)
        s = zp_platz(belegt, ab, bis, m)
        if s is None:
            passt_nicht.append({"text": t["text"], "projekt": t.get("projekt"), "min": m})
            continue
        zp["bloecke"][k] = {"text": t["text"], "projekt": t.get("projekt"), "start": s.strftime("%H:%M"), "min": m, "fest": False}
        belegt.append((s, s + timedelta(minutes=m)))
    if speichern and json.dumps(zp["bloecke"], sort_keys=True) != vorher:
        zp_speichern(zp)
    bloecke = []
    for k, b in zp["bloecke"].items():
        s = zp_zeit(heute, b["start"])
        e = s + timedelta(minutes=b["min"])
        t = aufgaben[k]
        status = "fertig" if t["done"] else "jetzt" if s <= jetzt < e else "verpasst" if e <= jetzt else "kommt"
        bloecke.append({"text": t["text"], "projekt": t.get("projekt"), "start": b["start"], "ende": e.strftime("%H:%M"), "min": b["min"],
                        "fest": b.get("fest", False), "status": status, "gestartet": k in zp["gestartet"]})
    bloecke.sort(key=lambda x: x["start"])
    with FOKUS_LOCK:
        st = fokus_load()
    fokus = []
    for x in st.get("sessions", []) + ([st["aktiv"]] if st.get("aktiv") else []):
        if x["start"][:10] != heute.isoformat():
            continue
        s = datetime.fromisoformat(x["start"]).astimezone()
        e = datetime.fromisoformat(x.get("ende") or x["planEnde"]).astimezone()
        fokus.append({"start": s.strftime("%H:%M"), "ende": e.strftime("%H:%M"), "aufgabe": x.get("aufgabe") or "", "projekt": x.get("projekt") or "",
                      "laeuft": x is st.get("aktiv"), "produktivMin": round(((x.get("ergebnis") or {}).get("produktiv") or 0) / 60)})
    return {"von": ke["von"], "bis": ke["bis"], "jetzt": jetzt.strftime("%H:%M"), "bloecke": bloecke, "passtNicht": passt_nicht,
            "termine": [{k: x[k] for k in ("titel", "von", "bis", "ganz", "farbe", "kalender", "ort")} for x in termine],
            "fokus": fokus, "bestaetigt": zp.get("bestaetigt", False), "planfokus": automatik_an("planfokus"),
            "google": automatik_an("googleBloecke") and bool(g_laden().get("refresh_token")) if GOOGLE_DATEI.exists() else False,
            "uebersprungen": [t["text"] for k, t in aufgaben.items() if k in zp["uebersprungen"] and not t["done"]]}


def zeitplan_setzen(data):
    """POST /api/zeitplan:
    {"bestaetigen": true|false}            -> Tag so planen (Fokus startet dann von selbst) / zurücknehmen
    {"text", "start": "HH:MM"}             -> Block auf eine Uhrzeit legen (bleibt fest)
    {"text", "min": 45}                    -> Dauer ändern
    {"text", "raus": true}                 -> heute ohne festen Block (bleibt in der Liste)
    {"text", "rein": true}                 -> wieder einplanen
    {"neu": true}                          -> alles, was noch nicht begonnen hat, neu verteilen"""
    zp = zp_laden()
    k = str(data.get("text") or "").lower()
    if "bestaetigen" in data:
        zp["bestaetigt"] = bool(data["bestaetigen"])
    if data.get("neu"):
        jetzt = datetime.now().astimezone()
        zp["bloecke"] = {x: b for x, b in zp["bloecke"].items() if b.get("fest") or zp_zeit(date.today(), b["start"]) <= jetzt and x in zp["gestartet"]}
        zp["uebersprungen"] = []
    if k:
        b = zp["bloecke"].get(k)
        if data.get("start"):
            if not UHRZEIT.match(str(data["start"])):
                raise ValueError("Uhrzeit bitte als HH:MM")
            b = b or {"text": data["text"], "projekt": data.get("projekt"), "min": 60}
            b.update(start=data["start"], fest=True)
            zp["bloecke"][k] = b
            zp["gestartet"] = [x for x in zp["gestartet"] if x != k]
            zp["erinnert"] = [x for x in zp["erinnert"] if x != k]
            zp["uebersprungen"] = [x for x in zp["uebersprungen"] if x != k]
        if data.get("min") and b:
            b["min"] = max(15, min(240, int(data["min"])))
        if data.get("raus"):
            zp["bloecke"].pop(k, None)
            if k not in zp["uebersprungen"]:
                zp["uebersprungen"].append(k)
        if data.get("rein"):
            zp["uebersprungen"] = [x for x in zp["uebersprungen"] if x != k]
    zp_speichern(zp)
    if not data.get("vonGoogle") and GOOGLE_DATEI.exists():
        g_sync_starten()


# ---------- Google-Kalender: bestätigte Blöcke als Termine eintragen, in Google verschobene übernehmen ----------
# Anmeldung per OAuth (Desktop-App, PKCE, Rückweg über http://127.0.0.1:<port>/api/google/zurueck). Zugang in google.json (nur für dich lesbar,
# nicht in Sicherungen und Exporten). Die App braucht eine eigene Client-ID aus der Google Cloud (Einstellungen › Kalender › Google).
GOOGLE_DATEI = DATEN / "google.json"
GOOGLE = {"lock": threading.Lock(), "fehler": "", "zuletzt": 0, "anmeldung": {}}
G_SCOPE = "openid email https://www.googleapis.com/auth/calendar.events"
G_API = "https://www.googleapis.com/calendar/v3"


def g_laden():
    try:
        return json.loads(GOOGLE_DATEI.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def g_speichern(d):
    import os
    tmp = GOOGLE_DATEI.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(GOOGLE_DATEI)


def g_redirect():
    return f"http://127.0.0.1:{CFG['port']}/api/google/zurueck"


def g_http(methode, url, body=None, form=None, token=None, timeout=30):
    """HTTP für Google (JSON oder Formular). Bei fehlenden Zertifikaten über /usr/bin/curl. Gibt (code, dict) zurück."""
    ssl = _ssl_modul()
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    h = {"User-Agent": "Fokus-Cockpit"}
    data = None
    if form is not None:
        data, h["Content-Type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    if token:
        h["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, data=data, headers=h, method=methode), timeout=timeout) as r:
            t = r.read()
            return r.status, (json.loads(t) if t.strip() else {})
    except HTTPError as e:
        t = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(t)
        except ValueError:
            return e.code, {"error": t[:200]}
    except URLError as e:
        if not (isinstance(e.reason, ssl.SSLError) and Path("/usr/bin/curl").exists()):
            raise RuntimeError(f"Google nicht erreichbar: {e.reason}")
    import os
    import subprocess
    import tempfile
    fd, cfg = tempfile.mkstemp(prefix="cockpit-curl-")
    try:
        with os.fdopen(fd, "w") as f:
            for k, v in h.items():
                f.write('header = "%s: %s"\n' % (k, str(v).replace('"', '\\"')))
        r = subprocess.run(["/usr/bin/curl", "-sS", "-m", str(timeout), "-X", methode, "-K", cfg, "-w", "\n%{http_code}", url]
                           + (["--data-binary", "@-"] if data is not None else []), input=data or b"", capture_output=True, timeout=timeout + 5)
    finally:
        os.unlink(cfg)
    if r.returncode != 0:
        raise RuntimeError("Google nicht erreichbar: " + r.stderr.decode("utf-8", "replace").strip()[:200])
    t, _, code = r.stdout.decode("utf-8", "replace").rpartition("\n")
    try:
        return int(code), (json.loads(t) if t.strip() else {})
    except ValueError:
        return int(code) if code.isdigit() else 0, {"error": t[:200]}


def g_fehler(code, d):
    e = d.get("error")
    text = (e.get("message") if isinstance(e, dict) else d.get("error_description") or e) or ""
    if code in (400, 401) and str(e) in ("invalid_grant", "invalid_client", "unauthorized_client"):
        return "Google-Anmeldung abgelaufen oder Client-ID falsch. Bitte neu anmelden."
    if code == 403:
        return f"Google erlaubt das nicht ({text[:120]}). Ist die Google Calendar API im Projekt aktiviert?"
    return f"Google meldet Fehler {code}: {str(text)[:160]}"


def g_token(st):
    """Gültiges Zugangs-Token (wird mit dem Refresh-Token erneuert)."""
    if st.get("access_token") and st.get("ablauf", 0) > time.time() + 60:
        return st["access_token"]
    if not st.get("refresh_token"):
        raise RuntimeError("Nicht mit Google verbunden.")
    code, d = g_http("POST", "https://oauth2.googleapis.com/token", form={
        "client_id": st["client_id"], "client_secret": st.get("client_secret", ""), "refresh_token": st["refresh_token"], "grant_type": "refresh_token"})
    if code != 200:
        if d.get("error") == "invalid_grant":
            st.pop("refresh_token", None)
            g_speichern(st)
        raise RuntimeError(g_fehler(code, d))
    st["access_token"], st["ablauf"] = d["access_token"], time.time() + int(d.get("expires_in", 3600))
    g_speichern(st)
    return st["access_token"]


def g_status():
    st = g_laden()
    return {"clientDa": bool(st.get("client_id")), "clientMaske": (st.get("client_id") or "")[:10] + "…" if st.get("client_id") else "",
            "verbunden": bool(st.get("refresh_token")), "email": st.get("email", ""), "fehler": GOOGLE["fehler"],
            "zuletzt": datetime.fromtimestamp(GOOGLE["zuletzt"]).strftime("%H:%M") if GOOGLE["zuletzt"] else "", "redirect": g_redirect()}


def g_einstellen(data):
    """POST /api/google: {client: {id, secret}} | {anmelden: true} -> {url} | {trennen: true} | {sync: true}"""
    import base64
    st = g_laden()
    if isinstance(data.get("client"), dict):
        cid, sec = einzeilig(data["client"].get("id")), einzeilig(data["client"].get("secret"))
        if not re.match(r"^[\w.-]+\.apps\.googleusercontent\.com$", cid):
            raise ValueError("Die Client-ID endet auf .apps.googleusercontent.com. Bitte die ganze Client-ID einfügen.")
        if cid != st.get("client_id"):
            st = {"client_id": cid, "ereignisse": st.get("ereignisse", {})}
        if sec:
            st["client_secret"] = sec
        g_speichern(st)
    if data.get("anmelden"):
        if not st.get("client_id"):
            raise ValueError("Zuerst Client-ID und Clientschlüssel speichern.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        GOOGLE["anmeldung"] = {"state": state, "verifier": verifier, "bis": time.time() + 900}
        from urllib.parse import urlencode
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": st["client_id"], "redirect_uri": g_redirect(), "response_type": "code", "scope": G_SCOPE,
            "access_type": "offline", "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256", "state": state})}
    if data.get("trennen"):
        if st.get("refresh_token"):
            try:
                g_http("POST", "https://oauth2.googleapis.com/revoke", form={"token": st["refresh_token"]}, timeout=10)
            except Exception:
                pass
        for k in ("refresh_token", "access_token", "ablauf", "email"):
            st.pop(k, None)
        g_speichern(st)
        GOOGLE["fehler"] = ""
    if data.get("sync"):
        g_sync_starten()
    return {"google": g_status()}


def g_zurueck(query):
    """Rückweg von Google nach der Anmeldung: Code gegen Zugang tauschen. Gibt eine kleine HTML-Seite zurück."""
    import base64
    q = {k: v[0] for k, v in parse_qs(query).items()}
    an = GOOGLE.get("anmeldung") or {}
    if q.get("error"):
        return False, "Anmeldung abgebrochen." if q["error"] == "access_denied" else f"Google meldet: {q['error']}"
    if not an or q.get("state") != an.get("state") or time.time() > an.get("bis", 0):
        return False, "Die Anmeldung ist abgelaufen. Bitte in BlockEm nochmal auf „Mit Google anmelden“ tippen."
    st = g_laden()
    code, d = g_http("POST", "https://oauth2.googleapis.com/token", form={
        "client_id": st.get("client_id", ""), "client_secret": st.get("client_secret", ""), "code": q.get("code", ""),
        "code_verifier": an["verifier"], "grant_type": "authorization_code", "redirect_uri": g_redirect()})
    GOOGLE["anmeldung"] = {}
    if code != 200 or not d.get("refresh_token"):
        return False, g_fehler(code, d) if code != 200 else "Google hat keinen dauerhaften Zugang geschickt. Bitte nochmal anmelden."
    st.update(refresh_token=d["refresh_token"], access_token=d["access_token"], ablauf=time.time() + int(d.get("expires_in", 3600)))
    try:
        teil = d.get("id_token", "").split(".")[1]
        st["email"] = json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4))).get("email", "")
    except Exception:
        pass
    g_speichern(st)
    GOOGLE["fehler"] = ""
    g_sync_starten()
    return True, f"Verbunden{(' mit ' + st['email']) if st.get('email') else ''}."


def g_eigene_ids():
    """IDs der Termine, die BlockEm selbst in Google angelegt hat (damit sie über den iCal-Link nicht als fremde Termine zählen)."""
    return {e.get("id") for e in (g_laden().get("ereignisse") or {}).values() if e.get("id")}


def g_zeit(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def g_sync():
    """Bestätigter Tag -> Blöcke als Termine in Google (anlegen, verschieben, löschen). In Google verschoben -> feste Zeit in BlockEm;
    in Google gelöscht -> Block heute ohne feste Zeit."""
    st = g_laden()
    if not st.get("refresh_token") or not automatik_an("googleBloecke"):
        return
    heute = date.today()
    zp = zp_laden()
    ev = st.setdefault("ereignisse", {})
    soll = {k: b for k, b in zp["bloecke"].items() if k not in zp["uebersprungen"]} if zp.get("bestaetigt") else {}
    token = g_token(st)
    cal = st.get("kalender") or "primary"
    pfad = f"{G_API}/calendars/{quote(cal, safe='@.')}/events"
    tag0 = zp_zeit(heute, "00:00")
    code, d = g_http("GET", f"{pfad}?singleEvents=true&showDeleted=false&maxResults=100&privateExtendedProperty=fokusCockpit%3D1"
                     f"&timeMin={quote((tag0 - timedelta(hours=2)).isoformat())}&timeMax={quote((tag0 + timedelta(days=1, hours=2)).isoformat())}", token=token)
    if code != 200:
        raise RuntimeError(g_fehler(code, d))
    da = {x["id"]: x for x in d.get("items", []) if x.get("status") != "cancelled"}
    praefix = heute.isoformat() + "|"
    geaendert = []
    for key in [x for x in ev if x.startswith(praefix)]:
        k, e = key[len(praefix):], ev[key]
        if k not in soll:                                   # Block gibt es nicht mehr / Tag zurückgenommen -> Termin weg
            if e["id"] in da:
                g_http("DELETE", f"{pfad}/{e['id']}", token=token)
            del ev[key]
        elif e["id"] not in da:                             # in Google gelöscht -> heute ohne feste Zeit
            del ev[key]
            geaendert.append({"text": soll[k]["text"], "raus": True, "vonGoogle": True})
            soll.pop(k)
        elif da[e["id"]].get("start", {}).get("dateTime"):
            g = da[e["id"]]
            s, en = g_zeit(g["start"]["dateTime"]), g_zeit(g["end"]["dateTime"])
            gmin = max(5, int((en - s).total_seconds() // 60))
            if (s.strftime("%H:%M"), gmin) != (e["start"], e["min"]) and s.date() == heute:   # in Google verschoben
                e.update(start=s.strftime("%H:%M"), min=gmin)
                geaendert.append({"text": soll[k]["text"], "start": e["start"], "min": gmin, "vonGoogle": True})
                soll[k] = {**soll[k], "start": e["start"], "min": gmin}
    for k, b in soll.items():
        key, e = praefix + k, ev.get(praefix + k)
        s = zp_zeit(heute, b["start"])
        koerper = {"summary": b["text"], "description": "Geplant in BlockEm" + (f" · {b['projekt']}" if b.get("projekt") else ""),
                   "start": {"dateTime": s.isoformat()}, "end": {"dateTime": (s + timedelta(minutes=b["min"])).isoformat()},
                   "extendedProperties": {"private": {"fokusCockpit": "1"}}, "reminders": {"useDefault": False}}
        if not e:
            code, d = g_http("POST", pfad, body=koerper, token=token)
            if code not in (200, 201):
                raise RuntimeError(g_fehler(code, d))
            ev[key] = {"id": d["id"], "start": b["start"], "min": b["min"]}
        elif (b["start"], b["min"]) != (e["start"], e["min"]):
            code, d = g_http("PATCH", f"{pfad}/{e['id']}", body={k2: koerper[k2] for k2 in ("start", "end")}, token=token)
            if code != 200:
                raise RuntimeError(g_fehler(code, d))
            e.update(start=b["start"], min=b["min"])
    grenze = (heute - timedelta(days=14)).isoformat()
    st["ereignisse"] = {k: v for k, v in ev.items() if k[:10] >= grenze}
    g_speichern(st)
    for x in geaendert:
        zeitplan_setzen(x)
    GOOGLE["fehler"], GOOGLE["zuletzt"] = "", time.time()


def g_sync_starten():
    def lauf():
        if not GOOGLE["lock"].acquire(blocking=False):
            return
        try:
            g_sync()
        except Exception as e:
            GOOGLE["fehler"] = str(e)[:240]
        finally:
            GOOGLE["lock"].release()
    threading.Thread(target=lauf, daemon=True).start()


def plan_fokus_pruefen():
    """Alle 30 s: beginnt ein Block des bestätigten Tagesplans, startet der Fokus von selbst (3 Min vorher eine Mitteilung)."""
    a = automatik_load()
    if not automatik_an("planfokus", a) or not automatik_an("zeitleiste", a):
        return
    zp = a.get("zeitplan") or {}
    if zp.get("datum") != date.today().isoformat() or not zp.get("bestaetigt"):
        return
    jetzt = datetime.now().astimezone()
    for k, b in sorted((zp.get("bloecke") or {}).items(), key=lambda x: x[1]["start"]):
        s = zp_zeit(date.today(), b["start"])
        e = s + timedelta(minutes=b["min"])
        if k in zp.get("uebersprungen", []) or k in zp.get("gestartet", []) or e <= jetzt:
            continue
        if s - timedelta(minutes=3) <= jetzt < s and k not in zp.get("erinnert", []):
            mac_mitteilung("In 3 Minuten: Fokus", f"{b['text']} ({b['min']} Min). In BlockEm kannst du verschieben oder überspringen.")
            zp2 = zp_laden()
            zp2["erinnert"].append(k)
            zp_speichern(zp2)
            return
        if s <= jetzt < s + timedelta(minutes=ZP_START_FENSTER):
            with FOKUS_LOCK:
                if fokus_load().get("aktiv"):
                    return
            erledigt = next((t["done"] for t in plan()["heute"] if t["text"].lower() == k), True)
            zp2 = zp_laden()
            zp2["gestartet"].append(k)
            zp_speichern(zp2)
            if erledigt:
                return
            rest = max(5, min(240, int((e - jetzt).total_seconds() // 60)))
            projekt = b.get("projekt") if b.get("projekt") in project_files() else None
            fokus_action({"aktion": "start", "projekt": projekt, "aufgabe": b["text"], "minuten": rest, "vonPlan": True})
            mac_mitteilung("Fokus läuft", f"{b['text']} bis {e.strftime('%H:%M')}." + (" Ablenkungen sind gesperrt." if automatik_an("sperre", a) else ""))
            return


def plan_waechter():
    time.sleep(25)
    n = 0
    while True:
        try:
            with leistung("Tagesplan und Kalender"):
                plan_fokus_pruefen()
        except Exception as e:
            print("Plan-Fokus:", e)
        n += 1
        if n % 2 == 0:                     # jede Minute mit Google abgleichen (nur wenn verbunden)
            with leistung("Tagesplan und Kalender"):
                g_sync_starten()
        time.sleep(30)


FEHLVERSUCHE = {}   # IP -> Zeitpunkte falscher Zugangscodes (Server-Modus: 10 in 10 Min = gesperrt)


class ApiFehler(Exception):
    def __init__(self, text, code=400):
        super().__init__(text)
        self.code = code


def api_get(pfad, q, lokal=True):
    """Alle GET-Anfragen unter /api/ (außer Export und Google-Rückweg). Auch für die Handy-App ohne Server (Python im Handy)."""
    if pfad == "/api/handy-info":
        devs = [{"name": d["name"], "letzterSync": d.get("letzterSync"),
                 "heuteSek": sum(t - s for _, _, s, t in d["tage"].get(date.today().isoformat(), [])) // 1000}
                for d in handy_devices()]
        return ({"adresse": CFG.get("adresse") or f"http://{lan_ip()}:{CFG['port']}", "modus": MODUS, "lan": bool(CFG.get("lan")),
                               "code": CFG["handyCode"] if lokal else None, "geraete": devs})
    if pfad == "/api/schutz-pc":
        return schutz_pc.status()
    if pfad == "/api/status":
        errors, aw = [], False
        try:
            if MODUS != "mac" and not CFG.get("activitywatchPruefen"):
                raise LookupError("aus")
            if zaehler_an():
                aw = True
                raise LookupError("eigener Zähler")
            aw = bool(pick_buckets()[0])
            if not aw:
                errors.append("ActivityWatch läuft, hat aber noch keine Fenster-Daten.")
        except LookupError:
            pass
        except Exception as e:
            errors.append("ActivityWatch läuft gerade nicht, darum fehlt die Bildschirmzeit vom Mac. Öffne die App ActivityWatch (kostenlos auf activitywatch.net)."
                          if "refused" in str(e) or "Errno 61" in str(e) or "Errno 111" in str(e) else f"ActivityWatch antwortet nicht ({CFG['activitywatch']}): {e}")
        rf = mac_rechte_fehlen()
        errors.extend(rf)
        if not vault():
            errors.append("Noch kein Datenordner eingerichtet: Einstellungen › Daten (oder die Einrichtung beim Start).")
        return ({"ok": True, "aw": aw, "handy": bool(handy_devices()), "vault": bool(vault()), "fehler": errors, "rechteFehlen": rf, "modus": MODUS, "version": APP_VERSION,
                               "produktivZielMin": CFG["produktivZielMin"],
                               "prokrastinationLimitMin": CFG["prokrastinationLimitMin"]})
    if pfad == "/api/today":
        d = q.get("date", [date.today().isoformat()])[0]
        return (day_data(date.fromisoformat(d)))
    if pfad == "/api/week":
        return (week())
    if pfad == "/api/muster":
        return (muster_stand())
    if pfad == "/api/plan":
        return (plan())
    if pfad == "/api/alterplan":
        return ({"text": alter_plan(q.get("projekt", [""])[0])})
    if pfad == "/api/fokus":
        return (fokus_state())
    if pfad == "/api/locker":
        return (locker_info(q.get("token", [""])[0]))
    if pfad == "/api/locker-kopie":
        return locker_kopie()
    if pfad == "/api/export-json":
        import base64
        return ({"zip": base64.b64encode(export_zip()).decode()})
    if pfad == "/api/fokus-handy":
        return (fokus_handy(q.get("gesperrt", [""])[0], q.get("geraet", [""])[0], q.get("art", [""])[0], q.get("dns", [None])[0]))
    if pfad == "/api/leistung":
        return leistung_daten()
    if pfad == "/api/tracking":
        return ({"sauber": erwachsen_serie(), "innehalten": innehalten_daten(), "innehaltenJetzt": innehalten_wirksam(),
                 "erziehung": erz_status() if erz_an() else None, "schutz": schutz_handy(), "panik": panik_stand(), "profil": profil_daten(),
                 "kategorien": kategorien_daten(), "warum": warum_stand(), "oeffnungen": oeffnungen_stand(),
                 # 21.11.2026 (john): regul_stand statt regul_pruefen, die App regelt nicht mehr von selbst (kein Fenster, keine stillen Limits)
                 "orte": orte_daten(), "offline": offline_daten(), "appVersion": APP_VERSION, "regulierung": regul_stand(),
                 "geraete": geraete_namen(), "entwickler": ENTWICKLER,
                 "macDns": mac_dns_ok(), "pc": pc_name(),
                 "bildKi": {"letzt": BILD_KI["letzt"], "fehler": BILD_KI["fehler"], "pruefungen": BILD_KI["pruefungen"],
                            "gespart": BILD_KI["gespart"]},
                 "port": CFG["port"]})
    if pfad == "/api/sync":
        return sync_status()
    if pfad == "/api/erziehung":
        if not erz_an():
            raise ApiFehler("Nur in der Entwicklerversion", 404)
        return erz_status(neu="neu" in q)
    if pfad == "/api/offline":
        return offline_daten()
    if pfad == "/api/orte":
        return {"orte": orte_daten(), "kategorien": kategorien_daten()}
    if pfad == "/api/warum":
        return warum_stand()
    if pfad == "/api/rueckblick":
        bezug = date.fromisoformat(q["datum"][0]) if q.get("datum") else None
        return (rueckblick(q.get("art", ["woche"])[0], bezug, plan() if vault() else None))
    raise ApiFehler("Nicht gefunden", 404)


def api_post(pfad, data):
    """Alle POST-Anfragen unter /api/. Gibt die Antwort zurück (meist den neuen Plan)."""
    if pfad == "/api/handy":
        n = handy_save(data)
        g = str(data.get("geraet", "Handy"))
        return ({"ok": True, "gespeichert": n, "hinweis": hinweis_fuer_handy(g) or handy_update_hinweis(g, data.get("version")), "fokus": fokus_handy(geraet=str(data.get("geraet", "")))})
    if pfad == "/api/schutz-pc":
        return schutz_pc.aktion(data)
    if pfad == "/api/neustart":
        threading.Thread(target=neustart, daemon=True).start()
        return ({"ok": True})
    if pfad == "/api/fokus":
        return (fokus_action(data))
    if pfad == "/api/locker":
        return (locker_aktion(data))
    if pfad == "/api/locker-abgleich":
        return locker_abgleich(data)
    if pfad == "/api/innehalten":
        return (innehalten_setzen(data))
    if pfad == "/api/erziehung":
        if not erz_an():
            raise ApiFehler("Nur in der Entwicklerversion", 404)
        return (erz_aktion(data))
    if pfad == "/api/offline":
        return offline_aktion(data)
    if pfad == "/api/orte":
        return orte_setzen(data)
    if pfad == "/api/regulierung":
        return regul_setzen(data)
    if pfad == "/api/kategorien":
        return kategorien_setzen(data)
    if pfad == "/api/schutz":
        return (schutz_setzen(data))
    if pfad == "/api/profil":
        return profil_setzen(data)
    if pfad == "/api/mac-dns":
        return (mac_dns_einrichten(aus=bool(data.get("aus"))))
    if pfad == "/api/panik":
        panik_zaehlen(str(data.get("was") or "gedrückt"))
        return ({"ok": True, "panik": panik_stand()})
    if pfad == "/api/google":
        r = g_einstellen(data)
        return ({**plan(), **r})
    if pfad == "/api/kategorie":
        h = kategorie_setzen(data)
        return {**day_data(date.today()), "hinweis": h, "katGeplant": kat_geplant()}
    if pfad == "/api/ki":
        erg = ki_einstellen(data)
        return ({**plan(), **erg})
    if pfad == "/api/import":
        return (importieren(data))
    if pfad == "/api/einstellungen":
        einstellungen_setzen(data)
        return (plan())
    if pfad == "/api/umzug":
        import base64
        try:
            roh = base64.b64decode(str(data.get("zip") or ""), validate=False)
        except ValueError:
            raise ValueError("Die Sicherung kam nicht vollständig an.")
        return ({**umzug_einspielen(roh), **({"plan": plan()} if vault() else {})})
    if pfad == "/api/umzug-senden":
        return (umzug_senden(data))
    if pfad == "/api/sync":
        return sync_aktion(data)
    if pfad == "/api/begleitung-konto":
        return sync_begleitung_koppeln(data)
    if pfad == "/api/gemeinsam":
        return gemeinsam_anfrage(data)
    if not vault():
        raise ApiFehler("Kein Vault eingestellt")
    if pfad == "/api/idee":
        erg = idee(data)   # erst schreiben, dann den Plan neu lesen
        return ({**plan(), **erg})
    if pfad == "/api/frage":
        return (ki_frage(data))
    if pfad == "/api/ki-rueckblick":
        return (ki_rueckblick(data))
    if pfad == "/api/ki-aktion":
        ki_aktion(data)
        return (plan())
    if pfad == "/api/ki-laden":
        ki_laden()
        return (plan())
    if pfad == "/api/automatik":
        automatik_einstellen(data)
        return (plan())
    if pfad == "/api/habit":
        habit(data)
    elif pfad == "/api/tagesplan":
        tagesplan_setzen(data)
    elif pfad == "/api/echt":
        echt_setzen(data)
    elif pfad == "/api/wochenplan":
        wochenplan_setzen(data, plan())
    elif pfad == "/api/sperre":
        if data.get("test"):
            sperre_test()
        else:
            sperre_setzen(data)
    elif pfad == "/api/kalender":
        kalender_setzen(data)
    elif pfad == "/api/zeitplan":
        zeitplan_setzen(data)
    elif pfad == "/api/sicherung":
        nacht_sicherung(erzwingen=True)
    elif pfad == "/api/erkennung":
        erkennung_setzen(data)
        plan_ = plan()
        return ({**plan_, "tag": day_data(date.today())})
    elif pfad == "/api/toggle":
        toggle(data["id"], bool(data["done"]), data.get("text"))
    elif pfad == "/api/plan-setzen":
        plan_setzen(data)
    elif pfad == "/api/projektplan":
        projektplan_setzen(data)
    elif pfad == "/api/zeit":
        zeit_setzen(data)
    elif pfad in ("/api/projekt-look", "/api/look"):
        look_setzen(data)
    elif pfad == "/api/loeschen":
        loeschen(data)
    elif pfad == "/api/umbenennen":
        umbenennen(data.get("projekt"), data.get("alt"), data.get("neu"), data.get("art") or "ziel", data.get("phase"))
    elif pfad == "/api/add" and data.get("liste") in ("heute", "woche", "monat", "jahr", "projekt", "ziel", "schritt", "meilenstein", "abschnitt", "endziel", "neuesprojekt") and data.get("text", "").strip():
        add(data["liste"], data["text"], data.get("projekt") or None, data.get("phase"), data.get("ziel"), data.get("abschnitt"))
    else:
        raise ApiFehler("Unbekannte Anfrage")
    return plan()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, url, post=False):
        """Anfragen vom Mac selbst sind immer erlaubt, aus dem WLAN nur mit Zugangscode.
        Schutz vor fremden Webseiten im selben Browser: Host muss BlockEm selbst sein (gegen DNS-Rebinding),
        und Änderungen (POST) nur von der eigenen Seite (Origin) oder ohne Origin (Handy-App)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if MODUS == "server":
            # Im Internet: jede Anfrage an /api/ braucht den langen Zugangscode (auch über einen Proxy auf demselben Rechner)
            if post and self.headers.get("Origin"):
                o = urlparse(self.headers["Origin"])
                if (o.netloc or "").lower() != (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").lower():
                    return False
            if not url.path.startswith("/api/"):
                return True
            ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
            fehl = [t for t in FEHLVERSUCHE.get(ip, []) if t > time.time() - 600]
            if len(fehl) >= 10:
                return False
            code = self.headers.get("X-Cockpit-Code") or parse_qs(url.query).get("code", [""])[0]
            if secrets.compare_digest(code.strip().upper().encode(), str(CFG["handyCode"]).upper().encode()):
                return True
            FEHLVERSUCHE[ip] = fehl + [time.time()]
            return False
        if host and not (host in ("localhost", "127.0.0.1", "::1") or host.endswith((".local", ".ts.net")) or re.match(r"^[\d.]+$|^[0-9a-f:]+$", host)
                         or host in [h.lower() for h in CFG.get("erlaubteHosts", [])]):
            return False
        if post and self.headers.get("Origin"):
            o = urlparse(self.headers["Origin"])
            if (o.netloc or "").lower() != (self.headers.get("Host") or "").lower():
                return False
        if self.client_address[0] in ("127.0.0.1", "::1") or not url.path.startswith("/api/"):
            return True
        # Sicherheitstest 24.11.: 6-stelliger Code im WLAN war ohne Bremse durchprobierbar -> 20 Fehlversuche je 10 Min. pro Gerät
        ip = self.client_address[0]
        fehl = [t for t in FEHLVERSUCHE.get(ip, []) if t > time.time() - 600]
        if len(fehl) >= 20:
            return False
        code = self.headers.get("X-Cockpit-Code") or parse_qs(url.query).get("code", [""])[0]
        if secrets.compare_digest(code.strip().upper().encode(), str(CFG["handyCode"]).upper().encode()):
            return True
        if code.strip():   # Prüfer: Anfrage ohne Code (z. B. Mac-Suche) ist kein Rateversuch
            FEHLVERSUCHE[ip] = fehl + [time.time()]
        return False

    def vom_geraet_selbst(self):
        return MODUS != "server" and self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        url = urlparse(self.path)
        if not self.allowed(url):
            return self.send_json({"fehler": "Zugangscode fehlt oder ist falsch"}, 403)
        try:
            if url.path in ("/api/google/zurueck", "/api/drive/zurueck"):
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"fehler": "Nur am Mac selbst"}, 403)
                ok, text = (drive_zurueck if url.path == "/api/drive/zurueck" else g_zurueck)(url.query)
                import html as _html
                seite = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>BlockEm</title>"
                         "<body style='font:17px system-ui;background:#0f1218;color:#e8ecf2;display:grid;place-items:center;height:90vh;text-align:center'>"
                         f"<div><p style='font-size:40px;margin:0'>{'✓' if ok else '⚠︎'}</p><p>{_html.escape(text)}</p>"
                         f"<p><a style='color:#7cb3ff' href='http://localhost:{CFG['port']}/#einstellungen'>Zurück zu BlockEm</a></p></div>"
                         + (f"<script>setTimeout(()=>location.href='http://localhost:{CFG['port']}/#einstellungen',1500)</script>" if ok else "")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(seite)))
                self.end_headers()
                self.wfile.write(seite)
                return
            if url.path == "/api/export":
                body = export_zip()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="BlockEm-Export-{date.today().isoformat()}.zip"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if url.path.startswith("/api/"):
                return self.send_json(api_get(url.path, parse_qs(url.query), self.vom_geraet_selbst()))
            if url.path in STATIC:
                f = HERE / STATIC[url.path]
                if not f.exists():
                    return self.send_json({"fehler": "Nicht gefunden"}, 404)
                body = f.read_bytes()
                if f.name == "index.html":
                    body = SKELETON[0] + build_index() + SKELETON[1]
                ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                         ".webmanifest": "application/manifest+json", ".apk": "application/vnd.android.package-archive", ".png": "image/png"}[f.suffix]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self.send_json({"fehler": "Nicht gefunden"}, 404)
        except ApiFehler as e:
            self.send_json({"fehler": str(e)}, e.code)
        except Exception as e:
            self.send_json({"fehler": str(e)}, 500)

    def do_POST(self):
        url = urlparse(self.path)
        if not self.allowed(url, post=True):
            return self.send_json({"fehler": "Zugangscode fehlt oder ist falsch"}, 403)
        try:
            if url.path == "/api/bild-rueck":   # 3.25: Rückmeldung zur Bild-KI als NDJSON, kein JSON-Objekt
                return self.send_json(bild_rueck_speichern(self.headers.get("X-Geraet"), self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.send_json(api_post(url.path, data))
        except ApiFehler as e:
            self.send_json({"fehler": str(e)}, e.code)
        except Exception as e:
            self.send_json({"fehler": str(e)}, 400)

# Anfragen ans Cockpit (Fenster, Browser, Handys) zählen auch mit
_HANDLER_GET, _HANDLER_POST = Handler.do_GET, Handler.do_POST


def _get_gemessen(self):
    with leistung("Oberfläche und Handys (Anfragen)"):
        _HANDLER_GET(self)


def _post_gemessen(self):
    with leistung("Oberfläche und Handys (Anfragen)"):
        _HANDLER_POST(self)


Handler.do_GET, Handler.do_POST = _get_gemessen, _post_gemessen


schutz_pc.init(globals())


def locker_erinnern_waechter():
    """24.11. (john): Bescheid geben, sobald eine beantragte Lockerung gilt (Handy: eigener Wecker in Momente.java)."""
    while True:
        time.sleep(30)
        try:
            with AUTOMATIK_LOCK:
                a = automatik_load()
                l = a.get("locker") or {}
                if l.get("geplant"):
                    locker_geplant_anwenden(l)
                fertig = []
                for e in (l.get("geplant") or {}).values():   # auch was locker_aktion/Abgleich/ein Handy schon angewendet hat
                    if isinstance(e, dict) and e.get("melden"):
                        e["melden"] = False
                        fertig.append(str(e.get("satz") or "Deine Lockerung"))
                if fertig:
                    automatik_save(a)
            for s in fertig:
                mac_mitteilung("BlockEm", f"Deine Lockerung gilt jetzt: {s}")
        except Exception as e:
            print("Lockern-Erinnerung:", e)


def main():
    host = "0.0.0.0" if CFG.get("lan") or MODUS == "server" else "127.0.0.1"
    srv = ThreadingHTTPServer((host, CFG["port"]), Handler)
    threading.Thread(target=waechter, daemon=True).start()
    threading.Thread(target=leistung_waechter, daemon=True).start()
    if sys.platform in ("darwin", "win32") and MODUS == "mac":   # Mac: AppleScript, Windows: fenster-waechter-windows.ps1 (3.17)
        threading.Thread(target=sperr_waechter, daemon=True).start()
        threading.Thread(target=sperr_aufpasser, daemon=True).start()
        threading.Thread(target=bild_waechter, daemon=True).start()
        threading.Thread(target=schutz_pc.waechter, daemon=True).start()
    if zaehler_an():   # 3.18: eigener Zähler, ActivityWatch nicht mehr nötig
        threading.Thread(target=zaehler_waechter, daemon=True).start()
    elif sys.platform == "darwin" and MODUS == "mac":
        threading.Thread(target=aw_waechter, daemon=True).start()
    if PLANUNG:
        threading.Thread(target=plan_waechter, daemon=True).start()
    if MODUS == "mac":
        threading.Thread(target=sync_waechter, daemon=True).start()
        threading.Thread(target=locker_erinnern_waechter, daemon=True).start()
    print(f"BlockEm läuft: http://localhost:{CFG['port']}")
    if MODUS == "server":
        print(f"Server-Modus. Zugangscode: {CFG['handyCode']}  (Daten in {DATEN})")
    elif CFG.get("lan"):
        print(f"Handy (gleiches WLAN): http://{lan_ip()}:{CFG['port']}  Zugangscode: {CFG['handyCode']}")
    print("Beenden mit Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
