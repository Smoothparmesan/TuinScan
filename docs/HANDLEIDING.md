# Tuinscan — Handleiding

Deze handleiding is voor iedereen die Tuinscan wil gebruiken, ook zonder ervaring met computers of programmeren. Volg de stappen precies in deze volgorde. Elke stap zegt **wat je doet**, **wat je ziet** en **wat je daarna doet**.

> Technische naslag staat in `README.md`. Die heb je voor deze handleiding niet nodig.

---

## Wat doet Tuinscan?

Tuinscan bekijkt luchtfoto's en hoogtekaarten van een wijk en zoekt tuinen van huurwoningen waar mogelijk iets aan de hand is. Bijvoorbeeld: veel nieuw groen, struiken over de schutting, een nieuw schuurtje of een tuin die helemaal verhard is.

Je krijgt:

- een **rapport** (een webpagina) met de tuinen die het meest opvallen, met foto's en de reden;
- een **Excel-bestand** waarin je na het tuinbezoek invult of de melding klopte.

Tuinscan geeft alleen aan **waar je het beste kunt gaan kijken**. Het is geen oordeel over een huurder.

## Wat heb je nodig?

- Een Windows-laptop met internet.
- De **Tobias-export**: een Excel-bestand met de huurwoningen (vraag dit op bij de beheerder van Tobias).
- **QGIS**: een gratis kaartprogramma. Staat het niet op je laptop? Zoek het in het Bedrijfsportal/Software Center, of vraag ICT om het te installeren.
- De eerste keer ongeveer een uur. Daarna gaat een nieuwe ronde in een paar minuten.

## Woorden die je tegenkomt

| Woord | Betekenis |
| --- | --- |
| **PowerShell** | Een zwart of blauw venster waarin je opdrachten typt. Je typt (of plakt) een regel en drukt op **Enter**. |
| **Opdracht** | Een regel tekst die je in PowerShell plakt, bijv. `uv run tuinscan score --config mijn.yaml`. |
| **Map** | Een folder op je computer, zoals je die in de Verkenner ziet. |
| **Instellingenbestand** | Het bestand `mijn.yaml`. Hierin staat welke wijk je scant en waar je bestanden staan. Je opent het met Kladblok. |
| **VHE** | Verhuureenheid: één huurwoning. |
| **Ronde** | Eén keer scannen van een wijk. Elke ronde krijgt een eigen map met uitkomsten. |
| **PDOK** | De overheidswebsite waar Tuinscan de kaarten en luchtfoto's ophaalt. |

---

## Deel A — Eenmalig installeren

### A1. Bestandsextensies zichtbaar maken

Dit voorkomt dat een bestand per ongeluk `mijn.yaml.txt` heet.

1. Open de **Verkenner** (het geel mapje onderin je scherm).
2. Klik bovenin op **Beeld** → **Weergeven** → zet een vinkje bij **Bestandsnaamextensies**.

### A2. De Tuinscan-map op je laptop zetten

1. Je krijgt de Tuinscan-map van de beheerder, of als ZIP-bestand van GitHub (groene knop **Code** → **Download ZIP**).
2. Pak het ZIP-bestand uit (rechtsklik → **Alles uitpakken**) naar `C:\Tuinscan`.
3. Controleer: in `C:\Tuinscan` zie je onder andere de mappen `config`, `docs` en `src`.

### A3. PowerShell openen in de Tuinscan-map

Dit doe je **elke keer** dat je met Tuinscan werkt.

1. Open `C:\Tuinscan` in de Verkenner.
2. Klik in de **adresbalk** bovenin (waar `C:\Tuinscan` staat), typ `powershell` en druk op **Enter**.
3. Er opent een venster. Op de laatste regel staat iets als `PS C:\Tuinscan>`. Dan zit je goed.

**Plakken in PowerShell:** kopieer een opdracht uit deze handleiding, klik in het PowerShell-venster en druk op **rechtermuisknop** (of **Ctrl+V**). Druk daarna op **Enter**.

### A4. Het hulpprogramma uv installeren

uv zorgt dat Tuinscan werkt. Je hebt er geen beheerdersrechten voor nodig.

1. **Plak deze opdracht** in PowerShell en druk op Enter:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Wacht tot je weer `PS C:\Tuinscan>` ziet.
3. **Sluit PowerShell en open het opnieuw** (zoals in A3). Anders kent PowerShell uv nog niet.

### A5. Tuinscan klaarzetten

1. **Plak** en druk op Enter (dit kan een paar minuten duren):
   ```powershell
   uv sync
   ```
2. **Controleer** of het werkt:
   ```powershell
   uv run tuinscan --help
   ```
   Je ziet nu een lijst met stappen zoals `inspecteer`, `download` en `score`. Dan is de installatie klaar.

---

## Deel B — Mappen en instellingen

### B1. Mappen maken

Maak in de Verkenner deze mappen aan (rechtsklik → **Nieuw** → **Map**):

```
C:\TuinscanData
C:\TuinscanData\invoer
C:\TuinscanData\cache
C:\TuinscanData\uitvoer
```

> **Belangrijk:** zet deze mappen **niet in OneDrive** (ook niet op het Bureaublad of in Documenten als die met OneDrive synchroniseren). De bestanden bevatten adressen. Tuinscan weigert te starten als een map in OneDrive staat.

| Map | Wat erin komt |
| --- | --- |
| `invoer` | De Tobias-export en bestanden die je zelf aanpast |
| `cache` | Kaarten en foto's die Tuinscan downloadt (niet aankomen) |
| `uitvoer` | Per ronde een map met het rapport en het Excel-bestand |

### B2. De Tobias-export neerzetten

Zet het Excel-bestand van Tobias in `C:\TuinscanData\invoer`. Onthoud de precieze bestandsnaam, bijv. `tobias_export.xlsx`.

### B3. Het instellingenbestand maken

1. Ga in de Verkenner naar `C:\Tuinscan\config`.
2. **Kopieer** `tuinscan.example.yaml` en plak het in `C:\Tuinscan` (de hoofdmap, niet in `config`).
3. **Hernoem** de kopie naar `mijn.yaml`.
4. Open het: rechtsklik → **Openen met** → **Kladblok**.

**Regels bij het aanpassen** (anders werkt het bestand niet):

- Pas alleen de waarde **na de dubbele punt** aan.
- Laat de spaties aan het begin van een regel staan. Gebruik nooit de Tab-toets.
- Gebruik een **punt** voor kommagetallen: `0.26`, niet `0,26`.
- Gebruik in mappen een gewone schuine streep: `C:/TuinscanData/invoer`.
- Sla op met **Ctrl+S**.

### B4. Deze vijf dingen invullen

Je vindt ze bovenin het bestand.

| Wat | Waar in het bestand | Voorbeeld |
| --- | --- | --- |
| Naam van de ronde | `naam:` in de regel `gebied:` | `naam: houten-noord-2026` |
| Werkgebied (zie B5) | `bbox_rd:` in de regel `gebied:` | `bbox_rd: [140200, 447800, 140900, 448400]` |
| Mappen | regel `mappen:` | Laat staan als je de mappen uit B1 gebruikt |
| Naam Tobias-export | `bestand:` onder `tobias:` | `bestand: tobias_export.xlsx` |
| Wijknaam | `wijk: []` in de regel `filters:` | `wijk: [Houten-Noord]` |

- De **rondenaam** wordt een mapnaam. Gebruik letters, cijfers en streepjes, geen spaties.
- De **wijknaam** schrijf je precies zoals in de Tobias-export (hoofdletters tellen mee). Meer wijken: `wijk: [Centrum-Oost, Centrum-West]`.

Sla het bestand op.

### B5. Het werkgebied opzoeken in QGIS

Tuinscan wil het werkgebied als een rechthoek in meters ("RD-coördinaten"). Zo vind je die:

1. Open **QGIS** en start een **Nieuw leeg project**.
2. Zet de kaart in meters: klik rechtsonder op de knop met `EPSG:…`, zoek `28992`, kies **Amersfoort / RD New** en klik **OK**.
3. Haal een kaart op: in het paneel **Browser** links, klap **XYZ Tiles** open en dubbelklik op **OpenStreetMap**.
4. Zoom in op je wijk (scrollwiel).
5. Beweeg de muis naar de **linkeronderhoek** van het gebied. Onderin het scherm bij **Coördinaat** staan twee getallen, bijv. `140200,447800`. Schrijf ze op (afgerond, zonder cijfers achter de komma).
6. Doe hetzelfde voor de **rechterbovenhoek**, bijv. `140900,448400`.
7. Vul in je instellingenbestand in: `bbox_rd: [linksonder-x, linksonder-y, rechtsboven-x, rechtsboven-y]`, dus `bbox_rd: [140200, 447800, 140900, 448400]`.

Tip: houd het gebied beperkt (een paar honderd meter breed). Een groter gebied duurt langer om te downloaden.

---

## Deel C — De eerste keer een wijk scannen

Open PowerShell in `C:\Tuinscan` (zie A3). Plak elke opdracht, druk op Enter en wacht tot je weer `PS C:\Tuinscan>` ziet.

### C1. Kolommen van de Tobias-export koppelen

```powershell
uv run tuinscan inspecteer --config mijn.yaml
```

**Wat je ziet:** een lijst met de kolommen uit je Excel-bestand en welk gegeven Tuinscan erin herkent.

**Wat je doet:**

1. Open `C:\TuinscanData\invoer\kolommen.yaml` met Kladblok.
2. Elke regel ziet er zo uit: `gegeven: Kolomnaam in Excel`, bijv. `vhe_nummer: VHE-nummer`.
3. **Controleer dat deze regels er staan en kloppen:** `vhe_nummer`, `bag_vbo_id`, `wijk`, `complex`, `woningtype`, en `adres` (of `straat` en `huisnummer`).
4. Klopt een kolomnaam niet? Typ de kolomnaam **precies** zoals hij bovenaan in Excel staat. Sla op.

Bestond `kolommen.yaml` al? Dan staat het nieuwe voorstel in `kolommen.voorstel.yaml` en blijft je oude bestand ongewijzigd.

### C2. Kaarten en luchtfoto's downloaden

```powershell
uv run tuinscan download --config mijn.yaml
```

**Wat je ziet:** Tuinscan haalt kaarten, luchtfoto's en hoogtegegevens op. Reken op ongeveer anderhalve minuut per 80 woningen.

**Wat je doet:** staat er in de uitvoer het woord `mislukt`? Kijk dan bij **Problemen oplossen**. Anders: door naar C3.

### C3. Groen leren herkennen (kalibreren)

Luchtfoto's verschillen per jaar in kleur. Tuinscan moet daarom per foto leren wat "groen" is.

```powershell
uv run tuinscan kalibreer --config mijn.yaml
```

**Wat je ziet:** per foto een tabel en een regel zoals:

```
  Voorstel: luchtfoto.lagen.cir_nu.ndvi_drempel: 0.26
```

**Wat je doet:** zet elk voorstel in `mijn.yaml`.

1. Zoek in `mijn.yaml` de regel van die foto. Bij `cir_nu` is dat:
   ```
       cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14"}      # summer CIR
   ```
2. Zet de waarde vóór de sluitende accolade `}`, met een komma ervoor:
   ```
       cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14", ndvi_drempel: 0.26}      # summer CIR
   ```
3. Doe dit voor **elk** voorstel (meestal vier: `cir_nu`, `cir_vorig`, `zomer_nu`, `zomer_vorig`). Bij `zomer_…` heet het `exg_drempel` in plaats van `ndvi_drempel`. Neem precies over wat het voorstel zegt.
4. Sla op en draai `kalibreer` **nog een keer**.
5. **Controleer** de kolom `groen` in de tabellen: bij `rijbaan` en `dak` hooguit ongeveer **10 %**, bij `begroeid` minstens ongeveer **85 %**. Klopt dat, dan ben je klaar met deze stap.

### C4. Tuinen bepalen

```powershell
uv run tuinscan tuinen --config mijn.yaml
```

**Wat je ziet:** Tuinscan zoekt bij elke woning de tuin.

**Wat je doet — controleren in QGIS:**

1. Open QGIS met de kaart uit B5 (of maak die opnieuw).
2. Sleep `C:\TuinscanData\uitvoer\<rondenaam>\tuinen.gpkg` vanuit de Verkenner naar het QGIS-venster.
3. Bekijk ongeveer **10 tuinen**: liggen de vlakken op de juiste plek, achter of voor het goede huis?
4. Klik op een vlak met het **i-knopje** (Objecten identificeren) om het VHE-nummer en `voor`/`achter` te zien.

Wil je de echte luchtfoto als achtergrond? Kies **Kaartlagen** → **Laag toevoegen** → **WMS/WMTS-laag toevoegen** → **Nieuw**, geef een naam en plak als URL `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?request=GetCapabilities&service=WMTS`. Klik **Verbinden**, kies de nieuwste laag en klik **Toevoegen**.

**Een tuin klopt niet? Zo herstel je hem:**

1. Klik in het paneel **Lagen** op `tuinvlak`. Selecteer de foute tuinen met het **selectiegereedschap** (houd Ctrl ingedrukt voor meerdere).
2. Rechtsklik op `tuinvlak` → **Exporteren** → **Objecten opslaan als…**
3. Vul in: indeling **GeoPackage**, bestandsnaam `C:\TuinscanData\invoer\tuincorrecties.gpkg`, laagnaam `tuinvlak`, CRS **EPSG:28992**. Zet een vinkje bij **Alleen geselecteerde objecten opslaan**. Klik **OK**.
4. In de nieuwe laag: klik op het **potlood** (Bewerken aan), kies het **Knooppuntgereedschap** en versleep de hoekpunten tot de tuin klopt.
5. Ontbreekt een tuin helemaal? Kies **Polygoonobject toevoegen**, klik de hoeken van de tuin aan, rechtsklik om af te ronden en vul `vhe_nummer` en `deel` (`voor` of `achter`) in.
6. Klik op **Opslaan** en zet het potlood weer uit.
7. Draai `tuinen` opnieuw. Tuinscan gebruikt nu jouw tekening.

Lukt dit niet? Noteer de VHE-nummers en vraag hulp. De rest van de stappen werkt ook zonder correcties.

### C5. Score, rapport en Excel-bestand maken

Plak deze drie opdrachten na elkaar:

```powershell
uv run tuinscan score --config mijn.yaml
```
```powershell
uv run tuinscan rapport --config mijn.yaml
```
```powershell
uv run tuinscan terugkoppeling maak --config mijn.yaml
```

Klaar. De uitkomsten staan in `C:\TuinscanData\uitvoer\<rondenaam>\`.

---

## Deel D — De uitkomsten gebruiken

### D1. Wat staat er in de uitvoermap?

| Bestand | Wat je ermee doet |
| --- | --- |
| `rapport.html` | **Dubbelklik** om te openen in je browser. Werkt ook zonder internet. |
| `terugkoppeling.xlsx` | Invullen tijdens of na het tuinbezoek. |
| `foutenlijst.csv` | Woningen waarbij iets misging. Opent in Excel. |
| `tuinscan.log` | Logboek voor als je hulp vraagt. Bevat geen adressen. |

### D2. Het rapport lezen

Bovenaan staan de tuinen met de hoogste score. Per tuin zie je foto's en waarom hij opvalt:

| Signaal | In gewone woorden |
| --- | --- |
| S1 | Er is deze zomer veel meer groen dan vorige zomer |
| S2 | Veel hoge begroeiing (hoger dan 2 meter) |
| S3 | Groen dat over de erfgrens hangt |
| S4a | Een nieuw hoog object, bijv. een schuur of overkapping |
| S5 | De tuin is in beide jaren grotendeels verhard of niet groen |

Staat er **"ontbrekend"** bij een signaal? Dan kon Tuinscan dat niet meten; het telt dan niet mee.

### D3. Na de bezoeken

1. Vul in `terugkoppeling.xlsx` per bezochte tuin in: **oordeel** (terecht/onterecht), **oorzaak** en **actie**. Je kiest steeds uit een lijstje.
2. Sla op en **sluit Excel**.
3. Plak:
   ```powershell
   uv run tuinscan terugkoppeling lees --config mijn.yaml
   ```
4. Er komt een bestand `kwaliteit.csv` bij. Daarin zie je hoe vaak elk signaal terecht was. Geef dit door aan wie de instellingen beheert, zodat de volgende ronde beter wordt.

---

## Deel E — Een volgende ronde in dezelfde wijk

Eén opdracht doet alles (downloaden, tuinen, score, rapport en Excel-bestand):

```powershell
uv run tuinscan alles --config mijn.yaml
```

- Wil je de vorige ronde bewaren? Geef de nieuwe ronde een eigen naam:
  ```powershell
  uv run tuinscan alles --config mijn.yaml --ronde houten-noord-2027
  ```
- Zijn er **nieuwe luchtfoto's** of scan je een **andere wijk**? Doe dan eerst weer C2 en C3 (downloaden en kalibreren).
- Bestaat er al een ingevuld `terugkoppeling.xlsx`? Dan overschrijft Tuinscan dat niet, zodat je ingevulde oordelen veilig blijven.

---

## Deel F — Opruimen

Als je klaar bent met een ronde, ruim je de bestanden met adressen op:

```powershell
uv run tuinscan opruimen --config mijn.yaml
```

Tuinscan vraagt je de rondenaam te typen als bevestiging. Daarna zijn de downloads, de uitvoermap van deze ronde en de Tobias-export weg.

- Wil je je getekende tuincorrecties bewaren? Zet er ` --bewaar-correcties` achter.
- **Mappen van andere rondes blijven staan en bevatten ook adressen.** Ruim die op met `--ronde <naam>` erachter, of verwijder ze zelf.

---

## Problemen oplossen

| Wat je ziet | Wat je doet |
| --- | --- |
| `uv` wordt niet herkend | Sluit PowerShell en open het opnieuw (A3). Helpt dat niet, doe A4 opnieuw. |
| `mijn.yaml` niet gevonden | Controleer dat PowerShell in `C:\Tuinscan` staat (`PS C:\Tuinscan>`) en dat het bestand niet `mijn.yaml.txt` heet (A1). |
| Melding over een ontbrekende of foute instelling | De melding noemt de naam. Zoek die in `mijn.yaml` en controleer spaties, komma's en punten (B3). |
| Melding over OneDrive | Verplaats `C:\TuinscanData` naar een plek buiten OneDrive en pas `mappen:` aan. |
| "Sluit bestand … en probeer opnieuw" | Sluit het bestand in Excel of QGIS en plak de opdracht opnieuw. |
| Download `mislukt` op de werklaptop | Het bedrijfsnetwerk blokkeert PDOK. Vraag ICT om `api.pdok.nl` en `service.pdok.nl` toe te staan, of probeer het via een ander netwerk. |
| Veel woningen met "geen tuinvlak" in `foutenlijst.csv` | De wijknaam in `mijn.yaml` klopt niet met het werkgebied. Controleer B4 en B5. |
| Regel met VHE `*` in `foutenlijst.csv` | Het probleem geldt voor de hele export. Vaak heeft Excel lange nummers afgerond: vraag een export waarin de BAG-kolom als tekst staat. |
| Wegen of daken tellen als groen | Doe C3 opnieuw. |
| Rondenaam geweigerd | Gebruik geen spaties of tekens als `\ / : * ? " < > \|` en geen namen als `CON` of `NUL`. |

Kom je er niet uit? Stuur `tuinscan.log` uit de uitvoermap naar wie Tuinscan beheert. Dat bestand bevat geen adressen.

---

## Privacy: wat je moet weten

- Tuinscan stuurt **geen adressen** naar PDOK. Het vraagt alleen kaarten op voor het hele werkgebied.
- Uit Tobias leest Tuinscan alleen woninggegevens (VHE-nummer, BAG-nummer, wijk, complex, woningtype), **geen huurdersgegevens**.
- In het Excel-bestand kies je uit vaste lijstjes; je typt geen vrije tekst over huurders.
- Deel het rapport alleen met collega's die het nodig hebben.
- Ruim een ronde op als je klaar bent (Deel F).
