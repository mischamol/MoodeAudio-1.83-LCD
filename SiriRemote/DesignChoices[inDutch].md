# Siri Remote voor moOde Audio

Deze daemon gebruikt rechtstreeks het Linux Bluetooth L2CAP/ATT-socket op de
vaste LE ATT-channel (CID 4). Er is geen `gatttool`, `bluepy`, `bleak`,
`dbus_next`, `dbus` of `gi` nodig.

## Wat de referentie doet

In `Yanndroid/SiriRemote-Linux` roept `remote/remote.py` uiteindelijk aan:

```python
self.__peripheral.writeCharacteristic(0x001d, b'\xAF', True)
```

De `True` betekent een write **met response**: ATT Write Request (`0x12`),
gevolgd door een verwachte ATT Write Response (`0x13`). De kernelreferentie
`siri-remote-lkm` start eerst de HID-I/O en stuurt daarna feature-report
`F0 AF`. BlueZ verwijdert report-ID `F0`, waardoor op ATT-niveau alleen `AF`
naar `0x001d` gaat. Deze daemon registreert daarom eerst het notification-pad,
zodat een onmiddellijk eerste knoprapport niet tussen initialisatie en luisteren
verloren gaat:

1. ATT Write Request `12 24 00 01 00` en wacht op `13`;
2. ATT Write Request `12 1d 00 af` en wacht op `13`;
3. verwerkt ATT notifications `1b 23 00 ...`.

## Installeren op moOde

De remote moet al gepaird en trusted zijn. Stop eerst een nog actieve
`gatttool`-sessie.

```sh
bluetoothctl info 70:48:0F:F2:65:99
sudo sh ./install.sh 70:48:0F:F2:65:99
journalctl -u siri-remote-moode -f
```

Controleer in `bluetoothctl info` minimaal `Paired: yes` en `Trusted: yes`. Zo
nodig:

```sh
bluetoothctl trust 70:48:0F:F2:65:99
```

## Mapping

| Code | Knop | moOde-opdracht |
|---:|---|---|
| `00 01` kort | TV/Home | geen actie |
| `00 01` 3 seconden | TV/Home | Raspberry Pi uitschakelen |
| `00 02` | Volume + | `set_volume -up 5` |
| `00 04` | Volume - | `set_volume -dn 5` |
| `00 08` | Play/Pause | `toggle_play_pause` |
| `00 10` | Microfoon/Siri | genegeerd |
| `00 20` | Menu/Back | wissel Playback en de laatste Library-view |
| touchpad links + fysieke click | Vorige nummer | `previous` |
| touchpad rechts + fysieke click | Volgende nummer | `next` |
| `00 00` | release | geen opdracht |

Pas `/etc/default/siri-remote-moode` aan en herstart na een wijziging:

```sh
sudoedit /etc/default/siri-remote-moode
sudo systemctl restart siri-remote-moode
```

### Externe renderers

Standaard worden gewone remote-commando's genegeerd zolang moOde een externe
renderer als actief meldt, waaronder AirPlay, Spotify Connect en Bluetooth.
Dit geldt voor Play/Pause, Volume, Previous/Next, Menu/Back en optionele
gewone knopkoppelingen. Home blijft na drie seconden de Pi uitschakelen. Ook de automatische batterijcontrole
blijft actief. Een geblokkeerde knop toont één seconde `Disabled:` met daaronder
de actieve renderernaam, zonder de bediening of een volgende snelle knopdruk op
te houden. Ondersteund zijn Bluetooth, AirPlay, Spotify, Deezer, Squeezelite,
Plexamp, RoonBridge, Audio Input en Multiroom Receiver. Die laatste naam wordt
over twee regels verdeeld.

Voor een gewone knopactie leest de daemon de rendererflags rechtstreeks en
alleen-lezen uit moOde's SQLite-database. Daarmee vervalt ongeveer 0,2–0,3
seconde PHP/HTTP-vertraging per knopdruk. Als de database niet beschikbaar is,
valt de code automatisch terug op het bestaande alleen-lezen HTTP-endpoint. De
uitkomst wordt maximaal 0,25 seconde bewaard; er draait geen extra polling. Als
beide controles mislukken, wordt de gewone actie voor de zekerheid genegeerd.
Instellingen:

```text
SIRI_IGNORE_DURING_RENDERER=yes
SIRI_RENDERER_CACHE_SECONDS=0.25
SIRI_RENDERER_DIRECT_DB=yes
MOODE_DB_PATH=/var/local/www/db/moode-sqlite3.db
```

Alleen een fysieke click op het touchpad geeft een opdracht; aanraken en vegen
doen niets. De Gen-1 X-positie ligt ongeveer tussen `2278` en `3914`. De daemon
gebruikt standaard `3096` als midden en negeert een smalle zone van 60 eenheden
aan beide kanten van het midden. Dit is instelbaar met `SIRI_TOUCH_X_SPLIT`,
`SIRI_TOUCH_DEAD_ZONE` en `SIRI_TOUCH_MAX_AGE_SECONDS`.

Een Menu/Back-druk wisselt direct via de al aanwezige X11-bibliotheken tussen Playback
en de Library-view waar Playback werkelijk vandaan kwam. Voor iedere druk leest
het script moOde's actuele `current_view`; het gebruikt dus geen interne gok die
verouderd raakt als het scherm handmatig wordt bediend. Vanuit `playback,album`
keert Menu terug naar Album, vanuit `playback,radio` naar Radio Stations. Dit
werkt hetzelfde voor Folder, Tag en Playlist.
Het Python-script klikt daarvoor op de cover-art-link in plaats van op
artiest/metadata.
Bij het starten wacht het script op X11 en synchroniseert het eerst naar
Playback. Er worden geen extra packages, configuratiebestanden of
moOde-bestanden toegevoegd.

De Home/TV-knop voert bij kort indrukken niets uit. Alleen onafgebroken drie
seconden vasthouden voert `/usr/bin/systemctl poweroff` uit. De knopcode en duur zijn instelbaar met
`SIRI_HOME_BUTTON_MASK` en `SIRI_HOME_HOLD_SECONDS`.
De Microfoon/Siri-knop wordt volledig genegeerd, omdat die op deze generatie ook
Apples voice/audio-pad activeert en daarmee de raw ATT-verbinding kan resetten.
Er is bewust geen handmatige batterijopvraag aan een knop gekoppeld. De
automatische controle en waarschuwingen bij een laag percentage blijven actief.

## Schermoverlay

De daemon toont zonder compositor of extra package een ronde schermoverlay:

- Play/Pauze toont de toestand die moOde na de toggle retourneert;
- Volume toont het werkelijke percentage dat moOde na de wijziging retourneert;
- touchpad links/rechts toont Previous/Next;
- Home toont tijdens vasthouden een annuleerbare `3`, `2`, `1`-aftelling;
- bij 5–9% Siri Remote-batterij verschijnt iedere vijf minuten één seconde een wit
  batterijsymbool met het zojuist uitgelezen percentage;
- bij 0–4% wordt de batterij iedere minuut opnieuw uitgelezen en knippert
  die witte overlay drie keer;
- Menu/Back toont bewust geen overlay.

Bluetooth, REST-opdrachten en de overlay draaien in afzonderlijke workers. Een
overlay pauzeert de knopactie dus niet. De overlaywachtrij gebruikt uitsluitend
het nieuwste event: bij snel achter elkaar Volume indrukken vervangt ieder nieuw
percentage direct het vorige en worden oude percentages niet later afgespeeld.
De daemon controleert eenmaal na verbinden, daarna standaard iedere 15 minuten,
iedere vijf minuten bij 5–9% en iedere minuut bij 0–4%. Eerst wordt het percentage
daadwerkelijk via ATT uitgelezen; pas daarna wordt de melding getoond en het
volgende interval gekozen. Er zijn geen parallelle ATT-reads.
De standaardduur is één seconde en is instelbaar met
`SIRI_OVERLAY_SECONDS`; zet `SIRI_OVERLAY=no` om de overlay uit te schakelen.
De nep-transparante achtergrond wordt met de reeds aanwezige X11-, Cairo- en
Lato-componenten opgebouwd. Binnen de cirkel wordt de vastgelegde cover eerst
verkleind en weer vergroot voor een snelle frosted-glassvervaging. Een antraciete
tint van 36% en een subtiele diagonale licht- en schaduwlaag vormen het glas;
een dunne, gedeeltelijk transparante witte rand scheidt de cirkel van de
coverart. Labels in normale letterdikte en vette
hoofdwaarden volgen de visuele hiërarchie van
moOde. Op het geteste 720 x 1280-portretscherm valt het middelpunt van de
overlay exact samen met het middelpunt van de coverart. Na het verbergen krijgt
Chromium kort tijd om de achtergrond opnieuw te
tekenen, zodat een oude overlay niet onder de volgende melding blijft staan.
Bij het starten warmt de overlayworker X11, Cairo, het Lato-lettertype en het
glasschalingspad onzichtbaar op. Dit is gereed voordat de afstandsbediening klaar
is en voorkomt dat juist de eerste echte overlay merkbaar later verschijnt.
Het laatste power-symbool bij shutdown gebruikt
exact dezelfde positie en grootte als tijdens het aftellen. Er worden geen
moOde-bestanden gewijzigd.
De overlay is klikdoorlatend, zodat Menu/Back ook tijdens een batterijmelding
blijft werken.

De batterijbewaking is instelbaar met:

```text
SIRI_BATTERY_CHECK_SECONDS=900
SIRI_BATTERY_LOW_CHECK_SECONDS=300
SIRI_BATTERY_CRITICAL_CHECK_SECONDS=60
SIRI_BATTERY_LOW_PERCENT=10
SIRI_BATTERY_CRITICAL_PERCENT=5
```

De query wordt correct percent-encoded, bijvoorbeeld:

```text
http://localhost/command/?cmd=set_volume%20-up%205
```

## Testen en diagnose

Test eerst de REST-laag los van Bluetooth:

```sh
curl -G -S -s --data-urlencode 'cmd=toggle_play_pause' http://localhost/command/
curl -G -S -s --data-urlencode 'cmd=set_volume -up 5' http://localhost/command/
curl -G -S -s --data-urlencode 'cmd=set_volume -dn 5' http://localhost/command/
curl -G -S -s --data-urlencode 'cmd=previous' http://localhost/command/
curl -G -S -s --data-urlencode 'cmd=next' http://localhost/command/
```

Voor extra touchlogging zet je tijdelijk `SIRI_DEBUG=yes` in
`/etc/default/siri-remote-moode`, en dan:

```sh
sudo systemctl daemon-reload
sudo systemctl restart siri-remote-moode
journalctl -u siri-remote-moode -f
```

Een fysieke click hoort in debugmodus bijvoorbeeld te tonen:

```text
Touch report: x=2500 split=3096 buttons=0x80 ...
Touchpad left / Previous -> previous
```

Veelvoorkomende meldingen:

- `Device or resource busy`: een andere ATT-client heeft CID 4 in gebruik.
  Sluit `gatttool` en andere BLE-testclients; de daemon probeert automatisch
  opnieuw met exponentiële backoff van 1 tot 30 seconden.
- `insufficient authentication/authorization/encryption`: controleer pairing en
  trust. De standaard `SIRI_SECURITY=medium` vraagt een versleutelde verbinding
  op basis van de bestaande bond.
- Geen verbinding na knopstilte: druk een knop in; de Siri Remote adverteert dan
  weer. Reconnect blijft automatisch actief.
- Een random-address fout: probeer `SIRI_ADDR_TYPE=random`; bij de opgegeven
  Apple-vendor-MAC is `public` de logische standaard.

Status en beheer:

```sh
systemctl status siri-remote-moode
journalctl -u siri-remote-moode --since today
sudo systemctl restart siri-remote-moode
sudo systemctl disable --now siri-remote-moode
```

## Verwijderen

De daemon, systemd-service en configuratie volledig verwijderen:

```sh
sudo sh ./uninstall.sh
```

Om `/etc/default/siri-remote-moode` te bewaren voor een latere herinstallatie:

```sh
sudo sh ./uninstall.sh --keep-config
```

De uninstaller verandert de Bluetooth-pairing en eventuele afzonderlijke
BlueZ-configuratie niet.

## Ontwerpkeuzes

- Alleen Python 3-standaardbibliotheek plus Linux libc.
- ATT writes met response; een mislukte initialisatie wordt nooit als succes
  behandeld.
- HTTP in een aparte worker, zodat een trage moOde-response geen Bluetooth
  notifications blokkeert.
- Alleen het opzetten van de Bluetooth-verbinding is niet-blokkerend en wordt
  na standaard twee seconden afgebroken. Zo kan een slapende remote niet één
  kernel-connectiepoging tientallen seconden of langer blokkeren. Eenmaal
  verbonden gebruikt de daemon een blokkerende socket met expliciete
  `select()`-polling; dit voorkomt de CPU-spin die Python socket-time-outs op
  sommige recente kernels geven.
- Deze remote gebruikt aantoonbaar MTU 23. Batterij-keepalive is uitgeschakeld,
  omdat dit de verbinding niet betrouwbaarder maakte. Bij een bezette CID 4 vraagt de daemon BlueZ
  automatisch de verbinding los te laten voordat hij opnieuw probeert.
- Acties alleen op de overgang van los naar ingedrukt; `00 00` reset de status.
- Touchreports worden ook verwerkt als de knopbyte niet verandert. Een fysieke
  touchpad-click wordt maximaal eenmaal afgehandeld en gebruikt de meest recente
  X-positie om links/rechts te bepalen.
- Geen automatische HTTP-retry voor Play/Pause, omdat een verloren response na
  succesvolle uitvoering anders de actie kan terugdraaien.
- systemd herstart het proces bij crashes; de daemon zelf herverbindt bij gewone
  BLE-disconnects.

Referenties:

- <https://github.com/Yanndroid/SiriRemote-Linux>
- <https://github.com/Yanndroid/siri-remote-lkm>
- <https://github.com/retsyx/SiriRemote>
- <https://github.com/moode-player/moode/blob/develop/www/command/index.php>
