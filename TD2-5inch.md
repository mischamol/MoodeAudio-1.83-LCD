I also added a 5 inch raspberry pi touch display 2. To increase the scaling I edited  `~/.xinitrc` replaced the line `--window-size="$SCREEN_RES" \` (in the part where chromium is besing started) with:

Portrait mode
```
--window-size="360,640" \
--force-device-scale-factor=2 \
```

landscape mode
```
--window-size="640,360" \
--force-device-scale-factor=2 \
```
Also for landscape mode edit `/boot/firmware/cmdline.txt` and add the folowing to the end of the line (not a newline): `video=DSI-1:720x1280@60,rotate=<rotation-value>`
Strangely the rotation is exactly opposite to MoOdeAudio, so if you rotate 90 degrees in moode, here it should state 270 degrees.

<img width="3623" height="2434" alt="image" src="https://github.com/user-attachments/assets/1dcdb9c9-299d-433e-81a4-137052edf8eb" />
