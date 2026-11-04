# SmartCity – live bysimulering med micro:bit

En hjemmeside hvor en simuleret by kører hele tiden, og hvor rigtige micro:bits kan
overtage de enkelte modeller. Data går begge veje: micro:bit → by (sensorer) og by → micro:bit (aktuatorer).
Alt gemmes i en SQLite-database.

## Start

```bash
python3 server.py
```

Åbn **http://localhost:8000** i **Chrome eller Edge** (WebSerial virker kun der).
Ingen installation – kun Pythons standardbibliotek.

## Sådan hænger det sammen

```
 model-micro:bits ──radio──▶ GATEWAY-micro:bit ──USB──▶ browser (WebSerial) ──HTTP──▶ server.py ──▶ smartcity.db
        ▲                                                       │                        │
        └────────────radio────────── GATEWAY ◀──USB────────────┘◀── live-strøm (SSE) ────┘
```

1. Læg `microbit/1-gateway.ts` på én micro:bit og sæt den i USB.
2. Tryk **Forbind micro:bit** på siden og vælg den.
3. Hver gruppe lægger kode på deres model-micro:bit (se eksemplerne i `microbit/`).
4. Så snart en micro:bit sender, bliver modellen **LIVE** (grøn) og simuleringen holder op med at styre den værdi.
   Stopper den med at sende i 20 sekunder, tager simuleringen over igen – byen går aldrig i stå.

## Protokollen (det eleverne skal lære)

Navn = `KODE.nøgle` (max 8 tegn), værdi = tal.

```ts
radio.setGroup(1)
radio.setTransmitSerialNumber(true)
radio.sendValue("GL.lys", input.lightLevel())        // send til byen

radio.onReceivedValue(function (name, value) {        // modtag fra byen
    if (name == "GL.lampe") { ... }
})
```

| Kode | Model | Sensorer (micro:bit → by) | Aktuatorer (by → micro:bit) |
|---|---|---|---|
| BY | Vejrstation | sol, regn, vind, temp | tid, prod, forbrug |
| SB | Skraldebil | – | aktiv, pos |
| DH | Drivhus | fugt, temp | pumpe |
| VM | Vindmølle | vind | kW, stop |
| GL | Gadelys | lys, bev | lampe |
| LS | Ladestander | bil | lad, kW |
| TL | Trafiklys | biler | fase (0 rød, 1 gul, 2 grøn) |
| VP | Varmepumpe | inde | on |
| OV | Oversvømmelse | vand | alarm |
| RV | Regnvandstank | tank | fuld |
| S1 / S2 | Skraldespand | fyld | tom |

En micro:bit direkte i USB (uden gateway) kan også bruges: `serial.writeValue("GL.lys", 120)`.

## Modellerne påvirker hinanden

- Regn fylder **regnvandstanken** → den vander **drivhuset** → er tanken fuld, løber vandet over i kloakken → **oversvømmelse**.
- **Vindmølle** + solceller giver strøm → overskud får **ladestanderen** til at lade hurtigt (smart opladning).
- **Gadelys** dæmpes til 30 % uden bevægelse og går op på 100 % når nogen går forbi.
- **Skraldebilen** kører kun ud når en spand er over 80 %, og springer spande under 40 % over.
- **Trafiklyset** giver længere grøn tid når der holder mange biler i kø.

## Databasen (`smartcity.db`)

`models` 1—* `sensors` 1—* `readings` *—1 `devices` (plus `commands`, `events`, `state`, `settings`).
Kan åbnes med fx DB Browser for SQLite. Eksempel:

```sql
SELECT r.ts, s.model_code || '.' || s.key AS navn, r.value, r.source, r.device
FROM readings r JOIN sensors s ON s.id = r.sensor_id
WHERE r.source = 'microbit' ORDER BY r.ts DESC LIMIT 20;
```

## API

| Metode | Sti | Hvad |
|---|---|---|
| GET | `/api/stream` | Live-strøm (Server-Sent Events): `state`, `log`, `cmd` |
| GET | `/api/state` | Hele byens tilstand |
| GET | `/api/history?name=GL.lys&minutes=30` | Historik fra databasen |
| POST | `/api/data` | `{"items":[{"name":"GL.lys","value":12,"serial":"123","source":"microbit"}]}` |
| POST | `/api/sim` | `{"speed":5}`, `{"paused":true}`, `{"weather":"storm"}`, `{"hour":21}`, `{"reset":true}` |
| POST | `/api/release` | `{"name":"GL.lampe"}` – giv værdien tilbage til simuleringen |

## Optimeringer der allerede er bygget ind

- **Databasen skrives i batches** fra én tråd (én transaktion pr. ½ sekund i stedet for én pr. måling).
- **Simulerede værdier gemmes kun hvert 30. sekund**, mens micro:bit-data gemmes med det samme.
  Målinger ældre end 7 dage slettes automatisk.
- **Radioen spares**: en kommando sendes kun når værdien har ændret sig nok (`step`), og højst én gang i sekundet pr. navn.
  Værdier der kom fra en micro:bit, sendes ikke tilbage til den.
- **Dublet-filter**: hvis to gateways hører samme radiopakke, tælles den kun én gang.
- **Browseren samler micro:bit-data** og sender dem samlet hvert 150 ms.
- **Serverens tidsskridt deles op** ved høj hastighed, så simuleringen er stabil ved 20×.
- **Server-Sent Events** i stedet for at browseren spørger hele tiden.

## Idéer til videre optimering

1. **Login fra det gamle projekt** – lås `/api/sim`, `reset` og "Send kommando" bag admin-login (BCrypt), og lad klasser logge ind med klassekode.
2. **Klasse-/scenarie-tabel** – knyt `devices` og `readings` til en klasse, så læreren kan se hver gruppes data og progression (din mange-til-mange-idé fra Før eksamen 1).
3. **Opgave-tjek** – automatisk "bestået" når en gruppe fx har fået gadelyset til at reagere på bevægelse 3 gange.
4. **Radio-grupper pr. klasse** så to klasser kan køre samtidig uden at forstyrre hinanden.
5. **Afvigelsesbaseret logging (deadband)** for micro:bit-data også – gem kun hvis værdien har ændret sig, så databasen ikke vokser med 1 række/sek pr. micro:bit.
6. **Scenarier** som læreren kan starte: "Skybrud kl. 17", "Strømsvigt", "Rush hour" – gemt i databasen.
7. **ChatGPT-hjælper** fra SmartCity 2 der kan se gruppens seneste data og give hints.
8. **Hosting**: `server.py` kører på enhver Linux-server (fx Raspberry Pi i klassen). WebSerial kræver https uden for localhost.
