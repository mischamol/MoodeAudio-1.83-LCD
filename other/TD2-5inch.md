I added a 5 inch raspberry pi touch display 2. To increase the scaling I edited  `~/.xinitrc`, added three lines of code
```
SCALE_FACTOR=2
WIDTH=$(( ${SCREEN_RES%,*} / SCALE_FACTOR ))
HEIGHT=$(( ${SCREEN_RES#*,} / SCALE_FACTOR ))
```
And replaced the line `--window-size="$SCREEN_RES" \` (in the part where chromium is being started) with:

```
--window-size="${WIDTH},${HEIGHT}" \
--force-device-scale-factor="$SCALE_FACTOR" \
```
The total if statement for starting the WebUi would like something like this:
```
# Launch WebUI or Peppy
if [ $WEBUI_SHOW = "1" ]; then
	# Clear browser cache
	$(/var/www/util/sysutil.sh clearbrcache)
	SCALE_FACTOR=2
	WIDTH=$(( ${SCREEN_RES%,*} / SCALE_FACTOR ))
	HEIGHT=$(( ${SCREEN_RES#*,} / SCALE_FACTOR ))
	# Launch chromium browser
	chromium \
	--app="http://localhost/" \
	--window-size="${WIDTH},${HEIGHT}" \
	--force-device-scale-factor="$SCALE_FACTOR" \
	--window-position="0,0" \
	--enable-features="OverlayScrollbar" \
	--no-first-run \
	--disable-infobars \
	--disable-session-crashed-bubble \
	--kiosk
```
For landscape mode also edit `/boot/firmware/cmdline.txt` and add the folowing to the end of the line (not a newline): `video=DSI-1:720x1280@60,rotate=<rotation-value>`
Strangely the rotation is exactly opposite to MoOdeAudio, so if you rotate 90 degrees in moode, here it should state 270 degrees.

<img width="640" height="481" alt="image" src="https://github.com/user-attachments/assets/325c2474-fea0-4634-8129-d9dd1f1c187c" />

