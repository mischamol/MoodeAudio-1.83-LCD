# Howto add a nameless usb soundcard with spdif input to MoodeAudio

![images](https://github.com/user-attachments/assets/c7bf6416-f67d-4f25-b8a8-db5e39aed71a)

This can be found for around €9,- on AliExpress Amazon and many other places


check your hardware
`arecord -l`
output will be something like this:
```
**** List of CAPTURE Hardware Devices ****
card 1: ICUSBAUDIO7D [ICUSBAUDIO7D], device 0: USB Audio [USB Audio]
  Subdevices: 0/1
  Subdevice #0: subdevice #0
```

edit /etc/asound.conf (don't forget to edit hw:1,0,0 to your card, subdevice, subdevice settings as shown above)

`sudo nano /etc/asound.conf`

```
pcm.usb_spdif_in {
    type plug
    slave {
        pcm "hw:1,0,0"
    }
}

ctl.usb_spdif_in {
    type hw
    card 1
}
```

Set amixer to use the SPDIF input instead of analog which is on the same channel
`amixer -c 1 set 'PCM Capture Source' 'IEC958 In'`
`amixer -c 1 set 'IEC958 In' cap`

Check
`amixer -c 1 get 'PCM Capture Source'`
Output should be something like:
```
Simple mixer control 'PCM Capture Source',0
  Capabilities: enum
  Items: 'Mic' 'Line' 'IEC958 In' 'Mixer'
  Item0: 'IEC958 In'
```

save
`sudo alsactl store`

`sudo reboot`

add a radio station with the folowing url:
`alsa://hw:1,0?format=44100:16:2` (check parameters again)

<img width="375" height="182" alt="Screenshot 2026-01-31 at 13 00 10" src="https://github.com/user-attachments/assets/c8c0473a-bd92-4be8-a8c9-e51b938c6a99" />

