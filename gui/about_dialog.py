from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser,
                               QVBoxLayout)

from paths import ASSETS_DIR
from version import APP_NAME, VERSION

WEBSITE_URL = "https://www.undabtanzbar.de"
DONATE_URL = "https://paypal.me/yakobo"
GITHUB_URL = "https://github.com/Yakobo1970/FlacCheck"

ABOUT_HTML = f"""
<h3>Was macht dieses Programm?</h3>
<p>{APP_NAME} durchsucht ganze Musikordner samt Unterordnern (FLAC, MP3, WAV, AIFF, OGG,
M4A, WMA, APE …) nach Fehlern <i>im Audiomaterial selbst</i> – also nach Fehlern, die eine
Prüfsummen-Kontrolle nicht findet: Der Track springt intern oder enthält Lesefehler-Rauschen,
hat aber trotzdem die richtige Länge, und der fehlerhafte Inhalt ist korrekt in die Prüfsumme
eingegangen.</p>

<h3>Kurzanleitung</h3>
<ol>
<li>Ordner wählen und „Scan starten“. Scans addieren sich; bereits geprüfte, unveränderte
Dateien werden übersprungen.</li>
<li>Auffällige Tracks erscheinen als Wellenform. Marker anklicken = Fundstelle in Schleife
anhören.</li>
<li>Fehler nicht hörbar? ✕ entfernt den Marker. Fehler bestätigt? Häkchen „Defekt“ setzen.</li>
<li>Bestätigte Defekte ins Log schreiben und von dort löschen, kopieren oder verschieben.</li>
</ol>

<h3>Erkannte Fehlerarten</h3>
<p>Fehlrauschen/Stottern · Taktsprung · Schnitt · lauter Knackser · Aussetzer ·
plötzliche Stille · Rauschstoß · Dekodierfehler. Geprüft wird immer in voller Tiefe; die
Anzeige-Stufe (niedrig/mittel/hoch) filtert nur, was angezeigt wird.</p>

<h3>Wichtiger Hinweis zur Zuverlässigkeit</h3>
<p>Die Erkennung ist ein <b>Hilfsmittel</b>, kein Ersatz fürs eigene Hinhören, und
<b>nicht zu 100&nbsp;% zuverlässig</b>. Besonders Taktsprünge in Live-Aufnahmen oder Musik
ohne festes Tempo sind schwer zu erkennen. Deshalb entscheidet am Ende immer der Mensch:
Erst was von Hand als defekt bestätigt wurde, landet im Log.</p>

<h3>Links</h3>
<p>Website: <a href="{WEBSITE_URL}">www.undabtanzbar.de</a><br>
Quellcode und neue Versionen: <a href="{GITHUB_URL}">github.com/Yakobo1970/FlacCheck</a></p>

<h3>Entwicklung</h3>
<p>Idee, Konzept und Regie: <b>Kay 'Yakobo' Fiedler</b>.<br>
Umgesetzt mit Claude (Anthropic) als KI-Programmierassistent für Code, Signalanalyse und
Fehlersuche – kalibriert an echten, von Hand durchgehörten Fehlerfunden aus dem eigenen
Musikarchiv.</p>

<h3>Unterstützen</h3>
<p>{APP_NAME} ist kostenlos. Wer die Entwicklung unterstützen möchte, kann über den
Knopf unten eine kleine Spende per PayPal schicken – vielen Dank!</p>
"""


TIPS_HTML = """
<h3>Schnellster Weg zu den meisten echten Fehlern</h3>
<ul>
<li><b>Anzeige: niedrig</b></li>
<li>Sortierung <b>„Verdacht (stärkste zuerst)“</b> (Standard)</li>
<li>Alle Fehlerarten eingeschaltet lassen</li>
</ul>
<p>Gemessen an rund 35.000 FLAC-Tracks mit 127 bestätigten Defekten:</p>
<table cellspacing="0" cellpadding="3" border="1" style="border-collapse:collapse">
<tr><th align="left">Anzeige</th><th align="right">Tracks mit Fund</th><th align="right">echte Defekte</th></tr>
<tr><td>niedrig (Welle rot)</td><td align="right">1.286</td><td align="right"><b>127</b></td></tr>
<tr><td>mittel (+ gelb)</td><td align="right">3.054</td><td align="right">127</td></tr>
<tr><td>hoch (+ grün)</td><td align="right">8.876</td><td align="right">127</td></tr>
</table>
<p><b>Alle</b> echten Defekte waren schon auf „niedrig“ zu sehen – „mittel“ und „hoch“ brachten
nur zusätzliche Fehlalarme. Durch die Sortierung stehen die meisten Defekte zudem oben:
in den ersten 200 Tracks lagen 103, in den ersten 400 schon 115 der 127. Weiter unten werden
echte Fehler auch bei rot schnell selten; wer wenig Zeit hat, kann dort aufhören.</p>
<p>„Mittel“ und „hoch“ lohnen sich nur für Sonderfälle, etwa wenn man einen bekannten Fehler
in einem bestimmten Track sucht, den „niedrig“ nicht zeigt.</p>

<h3>Welche Fehlerarten am häufigsten stimmen</h3>
<p>Anteil echter Defekte unter den Funden auf „niedrig“:</p>
<ul>
<li><b>Plötzliche Stille</b> ≈ 74&nbsp;%, <b>Aussetzer</b> ≈ 64&nbsp;%,
<b>Fehlrauschen/Stottern</b> ≈ 38&nbsp;% – fast immer Lesefehler.</li>
<li><b>Schnitt</b> ≈ 8&nbsp;%, <b>Taktsprung</b> ≈ 2&nbsp;% – oft Snare-Rolls, Breaks oder
echte Taktwechsel. Viele Fehlalarme, aber manche echten Fehler zeigen sich nur so.</li>
</ul>
<p>Die Standardsortierung stellt die zuverlässigen Arten ohnehin nach vorn. Wer nur die
sicheren Treffer abarbeiten will, kann den Fehlerarten-Filter auf <b>Aussetzer</b>,
<b>Plötzliche Stille</b> und <b>Fehlrauschen/Stottern</b> beschränken.</p>

<h3>Lauter Knackser</h3>
<p>Gilt nur noch, wenn die Spitze über den Pegel des <b>gesamten restlichen Tracks</b>
hinausragt und <b>neben dem BPM-Raster</b> liegt – also wirklich einmalig ist. Ein kräftiger
Schlagzeug-Treffer zählt damit nicht mehr. Die Art ist dadurch selten, aber aussagekräftig.</p>

<h3>Schnitt</h3>
<p>Eine Schnittkante in der Wellenform zählt nur, wenn danach das Audio <b>mitten im Takt
wegbricht</b> oder <b>aus der Taktzählung hüpft</b> – also z.&nbsp;B. auf 2/4 oder 3/4
weiterläuft statt auf der „1“. Oft beginnt so ein Hüpfer mit einem lauten Knacks.
Schlagzeug-Treffer auf dem Raster zählen nicht mehr.</p>

<h3>Grenzen der Automatik</h3>
<p>Manches erkennt das Tool technisch völlig richtig, obwohl es künstlerisch gewollt ist –
etwa ein Outro, das einen springenden Plattenspieler simuliert. Ob das ein Fehler ist,
entscheidet der Hörer: einfach mit ✕ als Fehlalarm entfernen.</p>

<h3>Weitere Tipps</h3>
<ul>
<li>Zu schnell auf ✕ gedrückt? Unter „Anzeigen: Fehlerfrei“ stehen die zuletzt
bearbeiteten Tracks oben – mit ↺ holt man den Marker zurück.</li>
<li>Sortierung „Fehlerart“ gruppiert gleiche Fehler – das Ohr gewöhnt sich so schneller
an das jeweilige Fehlerbild (praktisch für einen zweiten Durchgang).</li>
<li>Die Anzeige-Stufe und die Filter wirken sofort, ohne neuen Scan.</li>
<li>Besondere Fälle (z. B. MP3s zum Nachbearbeiten) per Rechtsklick in die <b>Sonderliste</b>
parken. Dort per Rechtsklick → <b>Bearbeiten</b> teilen oder Teile herausschneiden – MP3 ohne
Neukodierung, FLAC sample-genau. Danach → <b>Zur Neubewertung zurückschicken</b>.</li>
<li>Dateien verschoben oder umbenannte Ordner? Einfach den neuen Ordner scannen – verschobene
Titel werden an Name und Spieldauer erkannt und behalten ihren Prüfstand.</li>
<li>Einzelne Dateien oder Ordner lassen sich direkt ins Fenster ziehen – sie werden sofort
analysiert.</li>
<li>Als defekt bestätigte Titel bleiben in der Defekt-Liste, auch wenn eine verbesserte
Erkennung dort später nichts mehr findet. Häkchen entfernen, wenn der Titel doch in Ordnung ist.</li>
</ul>
"""


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Über {APP_NAME} {VERSION}")
        self.resize(620, 600)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        logo = ASSETS_DIR / "logo.png"
        if logo.exists():
            icon_label = QLabel(self)
            icon_label.setPixmap(QPixmap(str(logo)).scaled(72, 72, Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))
            header.addWidget(icon_label)
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel(f"<b style='font-size:17pt'>{APP_NAME}</b>", self))
        title_box.addWidget(QLabel(f"Version {VERSION} · Audio-Fehlerfinder", self))
        header.addSpacing(8)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        tabs = QTabWidget(self)
        for label, html in ((f"Über {APP_NAME}", ABOUT_HTML), ("Tooltipps", TIPS_HTML)):
            browser = QTextBrowser(self)
            browser.setOpenExternalLinks(True)
            browser.setHtml(html)
            tabs.addTab(browser, label)
        self.tabs = tabs
        layout.addWidget(tabs, stretch=1)

        buttons = QHBoxLayout()
        donate = QPushButton("♥  Spenden via PayPal", self)
        donate.setToolTip(DONATE_URL)
        donate.setStyleSheet("QPushButton { padding: 6px 14px; font-weight: bold; }")
        donate.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))
        web = QPushButton("www.undabtanzbar.de", self)
        web.setToolTip(WEBSITE_URL)
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(WEBSITE_URL)))
        close_btn = QPushButton("Schließen", self)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(donate)
        buttons.addWidget(web)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
