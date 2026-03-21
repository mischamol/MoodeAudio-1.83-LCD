I also added a 5 inch raspberry pi touch display 2. To increase the scaling a added the following to `~/.xinitrc` in the part where chromium is being started
```
--window-size="360,640" 
--force-device-scale-factor=2
```
Sadly this only works in portrait mode. Changing the windows-size to "640-360" results in the window size switching back to default 1280x720
