# Fokus-Cockpit Updates

Hier liegt die neueste Testversion der Fokus-Cockpit Handy-App.

- `version.json`: die aktuelle Version. Die App prüft diese Datei und zeigt ein Update an, wenn es eine neuere gibt.
- `handy.apk`: die App zum Installieren auf Android.

Neue Version ablegen: auf dem Mac `bash store/update-vorbereiten.sh https://raw.githubusercontent.com/Heiwax/fokus-cockpit-updates/main "Was neu ist"` und die beiden Dateien aus `store/update/` hier hochladen.

Ab 1.0.93 (23.11.2026): Die Play-Dateien (.aab, blockem-play.apk) sind mit dem Bild-KI-Modell über 100 MB und passen nicht mehr auf GitHub. Sie liegen in Teilen unter `play/` (aab) und `mac/` (direkte APK mit Modell), siehe LIESMICH dort. `handy.apk` hier ist die direkte Fassung ohne eingebautes Modell, sie lädt es wie bisher nach.
