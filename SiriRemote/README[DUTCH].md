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
| `00 20` | Menu/Back | wissel Playback en de laatste Library-view |
| touchpad links + fysieke click | Vorige nummer | `previous` |
| touchpad rechts + fysieke click | Volgende nummer | `next` |
| `00 00` | release | geen opdracht |

Pas `/etc/default/siri-remote-moode` aan en herstart na een wijziging:

```sh
sudoedit /etc/default/siri-remote-moode
sudo systemctl restart siri-remote-moode
```

Alleen een fysieke click op het touchpad geeft een opdracht; aanraken en vegen
doen niets. De Gen-1 X-positie ligt ongeveer tussen `2278` en `3914`. De daemon
gebruikt standaard `3096` als midden en negeert een smalle zone van 60 eenheden
aan beide kanten van het midden. Dit is instelbaar met `SIRI_TOUCH_X_SPLIT`,
`SIRI_TOUCH_DEAD_ZONE` en `SIRI_TOUCH_MAX_AGE_SECONDS`.

De Menu/Back-knop wisselt via de al aanwezige X11-bibliotheken tussen Playback
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
- Een blokkerende Bluetooth-socket met expliciete `select()`-polling voorkomt
  de CPU-spin die Python socket-timeouts op sommige recente kernels geven.
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
