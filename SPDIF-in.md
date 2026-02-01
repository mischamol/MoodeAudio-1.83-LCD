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
Apparently, the input channel is located on card 1, subdevice 0, subdevice 0.

To find out some more spec,type (change hw params accordig to your output above):

`arecord -D hw:1,0,0 --dump-hw-params`

Output should be something like this:
```
Warning: Some sources (like microphones) may produce inaudible results
         with 8-bit sampling. Use '-f' argument to increase resolution
         e.g. '-f S16_LE'.
HW Params of device "hw:1,0,0":
--------------------
ACCESS:  MMAP_INTERLEAVED RW_INTERLEAVED
FORMAT:  S16_LE
SUBFORMAT:  STD MSBITS_MAX
SAMPLE_BITS: 16
FRAME_BITS: 32
CHANNELS: 2
RATE: [44100 48000]
PERIOD_TIME: [1000 1000000]
PERIOD_SIZE: [45 48000]
PERIOD_BYTES: [180 192000]
PERIODS: [2 1024]
BUFFER_TIME: [1875 2000000]
BUFFER_SIZE: [90 96000]
BUFFER_BYTES: [360 384000]
TICK_TIME: ALL
--------------------
arecord: set_params:1387: Sample format non available
Available formats:
- S16_LE
```
The input supports 16-bit, 2-channel audio at a sample rate of 44.1 kHz or 48 kHz. For Apple TV, we need to use the latter.

2. Set amixer to use the S/PDIF input instead of the analog input on the same device and subdevices.
   
`amixer -c 1 set 'PCM Capture Source' 'IEC958 In'`  -> selects the s/pdif (IEC958) input device for capture

`amixer -c 1 set 'IEC958 In' cap`  -> enable capturing on s/pdif input

3. Verify the setting

`amixer -c 1 get 'PCM Capture Source'`

The output should be similar to:
```
Simple mixer control 'PCM Capture Source',0
  Capabilities: enum
  Items: 'Mic' 'Line' 'IEC958 In' 'Mixer'
  Item0: 'IEC958 In'
```

Check the last line to see if 'IEC958 In'  is selected

4. Save the settings and reboot

`sudo alsactl store`

`sudo reboot`

5. Add a radio station to MoOde using the following URL (change the parameters to your situation):
   
`alsa://hw:1,0?format=48000:16:2` 

<img width="477" height="182" alt="Screenshot 2026-02-01 at 13 53 56" src="https://github.com/user-attachments/assets/35d4d60f-58c0-44a6-876a-5bdc042a6cd8" />


Finally, because MoOde does not accept uploaded logos as metadata for this type of URL, I manually copied `spdif.jpg` to `/var/local/www/imagesw/radio-logos/`, adjusted the permissions, and added an elif clause to the `getMetaData` function in `lcd_updater.py`.
```
sudo mv spdif.jpg /var/local/www/imagesw/radio-logos/
sudo chown root:root spdif.jpg
sudo chmod 777 spdig.jpg
```



