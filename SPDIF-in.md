# S/PDIF input to MoOdeAudio
Because Apple Music is, for obvious reasons, not supported on MoOde, I needed a way to get audio from my Apple TV into CamillaDSP running on MoOde. Since I already had an S/PDIF signal coming from my TV, this seemed like a logical starting point. After some quick online searching in the usual places, I found a USB sound card with an S/PDIF input for about €9.

![images](https://github.com/user-attachments/assets/c7bf6416-f67d-4f25-b8a8-db5e39aed71a)

Further searching led me to a very useful guide for the HiFiBerry Digi+ I/O:

https://silvester.org.uk/2024/03/12/compact-disc-player-s-pdif-input-for-moode-audio-on-raspberry-pi/

After some fiddling around, I came up with the following steps to get everything working.

1. Check your hardware

`arecord -l`

The output will look something like this:
```
**** List of CAPTURE Hardware Devices ****
card 1: ICUSBAUDIO7D [ICUSBAUDIO7D], device 0: USB Audio [USB Audio]
  Subdevices: 0/1
  Subdevice #0: subdevice #0
```
2.  Edit /etc/asound.conf 
Don’t forget to adjust hw:1,0,0 to match your card, device, and subdevice numbers as shown above.
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
3. Set amixer to use the S/PDIF input instead of the analog input on the same device and subdevices.
   
`amixer -c 1 set 'PCM Capture Source' 'IEC958 In'`
`amixer -c 1 set 'IEC958 In' cap`

4. Verify the setting

`amixer -c 1 get 'PCM Capture Source'`

The output should be similar to:
```
Simple mixer control 'PCM Capture Source',0
  Capabilities: enum
  Items: 'Mic' 'Line' 'IEC958 In' 'Mixer'
  Item0: 'IEC958 In'
```

5. Save the settings and reboot

`sudo alsactl store`

`sudo reboot`

6. Add a radio station to MoOde using the following URL:
   (Double-check the parameters for your setup.)
   
`alsa://hw:1,0?format=44100:16:2` (check parameters again)

<img width="375" height="182" alt="Screenshot 2026-01-31 at 13 00 10" src="https://github.com/user-attachments/assets/c8c0473a-bd92-4be8-a8c9-e51b938c6a99" />

Finally, because MoOde does not accept uploaded logos as metadata for this type of URL, I manually copied `spdif.jpg` to `/var/local/www/imagesw/radio-logos/` and adjusted the permissions:

`sudo mv spdif.jpg /var/local/www/imagesw/radio-logos/`
`sudo chown root:root spdif.jpg`
`sudo chmod 777 spdig.jpg`




