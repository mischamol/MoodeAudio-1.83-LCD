# Pass Airplay volume to MoOdeAudio
Because my CamillaDSP setup uses volume-based loudness, I do not want external renderers such as AirPlay to control the local output volume. To prevent this, run the following patch. It comments out the part of `spspre.sh` that sets the local volume to 0 dB when an AirPlay session starts.

```bash
sudo sed -i '/^# Local$/,/^fi$/ {
  /^# Local$/! s/^/# /
}' /var/local/www/commandw/spspre.sh && \
sudo systemctl restart shairport-sync
```

To make Shairport Sync ignore AirPlay volume control, run:

```bash
sudo sed -i -E 's|^[[:space:]]*(//[[:space:]]*)?ignore_volume_control[[:space:]]*=.*;|ignore_volume_control = "yes";|' /etc/shairport-sync.conf && \
sudo sed -i -E 's|^[[:space:]]*(//[[:space:]]*)?default_airplay_volume[[:space:]]*=.*;|default_airplay_volume = 0.0;|' /etc/shairport-sync.conf && \
sudo systemctl restart shairport-sync
```

Finally, even though Shairport Sync ignores AirPlay volume control, we still want to pass the AirPlay volume value through to moOde Audio. This allows us to control the moOde volume from the AirPlay device.

For this, we use Shairport Sync’s `run_this_when_volume_is_set` option. Shairport Sync uses a volume range of `-30..0`, while moOde Audio uses `0..100`, so we need a small conversion script.

```bash
sudo tee /var/local/www/commandw/aplvol2moode.sh >/dev/null <<'EOF'
#!/bin/bash

APVOL="$1"

# AirPlay mute is -144. Treat it as moOde volume 0.
if awk -v v="$APVOL" 'BEGIN { exit !(v <= -143.0) }'; then
  LEVEL=0
else
  # AirPlay volume: -30.0 = 0%, 0.0 = 100%
  LEVEL=$(awk -v v="$APVOL" 'BEGIN {
    p = ((v + 30.0) / 30.0) * 100.0
    if (p < 0) p = 0
    if (p > 100) p = 100
    printf "%d", p + 0.5
  }')
fi

/var/www/util/vol.sh "$LEVEL" >/dev/null 2>&1
EOF

sudo chmod +x /var/local/www/commandw/aplvol2moode.sh
```

After that, edit `/etc/shairport-sync.conf` so Shairport Sync calls the script whenever the AirPlay volume changes.

```bash
sudo cp /etc/shairport-sync.conf /etc/shairport-sync.conf.bak.airplay-volume-hook && \
sudo sed -i -E 's|^[[:space:]]*(//[[:space:]]*)?run_this_when_volume_is_set[[:space:]]*=.*;|run_this_when_volume_is_set = "/var/local/www/commandw/aplvol2moode.sh";|' /etc/shairport-sync.conf && \
sudo systemctl restart shairport-sync
```

To test it manually, run the following. This should set the moOde volume to around 50.

```bash
sudo /var/local/www/commandw/aplvol2moode.sh -15.0
/var/www/util/vol.sh
```

For live testing, start playback from your AirPlay device. Then open:

```text
http://moode.local/command/?cmd=get_volume
```

Change the volume on your AirPlay device, then open the same URL again:

```text
http://moode.local/command/?cmd=get_volume
```
