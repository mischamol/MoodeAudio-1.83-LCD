I also added a 5 inch raspberry pi touch display 2. To increase the scaling a added the following to `~/.xinitrc` in the part where chromium is being started

Portrait mode
```
--window-size="360,640" 
--force-device-scale-factor=2
```

landscape mode
```
--window-size="640,360" 
--force-device-scale-factor=2
```
Als for landscape mode edit `/boot/firmware/cmdline.txt` and add the folowing to the end of the line (not a newline): `video=DSI-1:720x1280@60,rotate=<rotation-value>`
Strangely the rotation is exactly opposite to MoOdeAudio, so if you rotate 90 degrees is moode, here it should state 270 degrees.
