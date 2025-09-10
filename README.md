# MoodeAudio Metadata on Waveshare 1.83-inch LCD
Script for displaying coverart and some basic information (volume, state and mute) from MoodeAudio on Waveshare 1.83 inch lcd screen. Currently the script supports metadata from local files, radio streams, Spotify and Airplay. This script uses the library files and fonts that came with the Waveshare example (included in the `lib` folder). The original example can be found here: https://www.waveshare.com/wiki/1.83inch_LCD_Module.<br>

<img src="https://github.com/user-attachments/assets/d258697a-e3ea-450f-969d-3c5b2cd47aeb" style="width:300px; height:auto;"><br>

<sub>The LCD in a 3d printed enclosure on an Argon One case showing volume overlay .</sub><br>

## Prerequisites
Connect the screen according to the diagram below.
![image](https://github.com/user-attachments/assets/9180c546-3529-4612-8237-3af47816893e)<br> 
<sub>Waveshare connection diagram (source: https://www.waveshare.com/wiki/1.83inch_LCD_Module)</sub><br>

Enable SPI: `sudo raspi-config` ➡ Interface Options ➡ enable SPI.

Install the required library: `pip install RPi.GPIO`

## Installation
You can place `lcd_updater.py` and the `lib` folder under `/var/local/www/commandw/` and enable the LCD updater in MoodeAudio under ➡ Configure ➡ Peripherals. 

Set the permissions: `sudo chmod 755 lcd_updater.py` and `sudo chmod -R 755 lib`

⚠ Note: `lcd_updater.py` is replaced with a stub after every update, so keep a backup elsewhere. Additionally, use a systemd watcher on `spotmeta.txt` and `aplmeta.txt` to include Spotify and Airplay metadata .

## Alternative Installation Method
Instead of enabling the LCD updater in MoodeAudio, you can also only enable the metadata file under ➡ Configure ➡ Audio ➡ MPD Options and use a systemd watcher for `currentsong.txt`. As a result, you can place this repository wherever you want—for example, in your home folder (~), which remains untouched during updates. Personally, I prefer this method, as I got tired of copying everything back after every update.

Ensure the correct permissions: `sudo chmod -R 755 lcd`

## Example of systemd watcher configuration for `spotmeta.txt` 
Tip: Don't forget to change `username` in the path with your own.
`sudo nano /etc/systemd/system/spotwatcher.service`
```
[Unit]
Description = Run LCD_updater.py on spotmeta change
ConditionPathExists=/var/local/www/spotmeta.txt

[Service]
ExecStart=/usr/bin/python3 /home/username/lcd/lcd_updater.py
```
`sudo nano /etc/systemd/system/spotwatcher.path`
```
[Unit]
Description=Monitor spotmeta.txt and trigger spotwatcher service
After=network.target

[Path]
PathModified=/var/local/www/spotmeta.txt

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl daemon-reload
sudo systemctl enable --now spotwatcher.path
```
## Example of systemd watcher configuration for `aplmeta.txt`
`sudo nano /etc/systemd/system/aplwatcher.service`
```
[Unit]
Description = Run LCD_updater.py on aplmeta change
ConditionPathExists=/var/local/www/aplmeta.txt

[Service]
ExecStart=/usr/bin/python3 /home/username/lcd/lcd_updater.py
```
`sudo nano /etc/systemd/system/aplwatcher.path`
```
[Unit]
Description=Monitor aplmeta.txt and trigger aplwatcher service
After=network.target

[Path]
PathModified=/var/local/www/aplmeta.txt

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl daemon-reload
sudo systemctl enable --now aplwatcher.path
```

## Example of optional systemd watcher configuration for `currentsong.txt` 
`sudo nano /etc/systemd/system/currentsong.service`
```
[Unit]
Description = Run LCD_updater.py on currentsong.txt change
ConditionPathExists=/var/local/www/currentsong.txt

[Service]
ExecStart=/usr/bin/python3 /home/username/lcd/lcd_updater.py
```
`sudo nano /etc/systemd/system/currentsong.path`
```
[Unit]
Description=Monitor currentsong.txt and trigger currentsong service
After=network.target

[Path]
PathModified=/var/local/www/currentsong.txt

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl daemon-reload
sudo systemctl enable --now currentsong.path
```

To also include the 'off' overlay when shutdown is pressed we also need to patch `/etc/argon/argonpowerbutton.py` just above the shutdown command to  call `lcd_updater.py`. This can by done by hand or by running the folowing command (don't forget to change your homedir in both places)

```
sudo bash -c "grep -q '/home/username/lcd/lcd_updater.py shutdown' /etc/argon/argonpowerbutton.py || \
sed -i -E '0,/^([[:space:]]*)os\.system\([[:space:]]*(\"shutdown[^\"]*\"|\"systemctl[[:space:]]+poweroff[^\"]*\")\)/ s//\1os.system('\''\/usr\/bin\/python3 \/home\/username\/lcd\/lcd_updater.py shutdown'\'')\
&/' /etc/argon/argonpowerbutton.py"
```
`sudo systemctl restart argononed.service`

Finally, by default, MoodeAudio checks whether `currentsong.txt` should be updated every 3 seconds via `/var/www/daemon/worker.php`. Personally, I found this a bit unresponsive for my overlay, so I patched the original `WORKER_SLEEP` constant value from 3000000 microseconds to 500000 microseconds.

`sudo sed -i 's/const WORKER_SLEEP = [0-9]\+;/const WORKER_SLEEP = 500000;/' /var/www/inc/sleep-interval.php`

Enjoy!


Note: Primarily for archival purposes, this repository also includes a lircrc file (`irexec.lircrc`) that maps the Argon One Remote to basic Moode commands (such as toggle, volume, next, previous, etc.). After downloading the Argon script and running`argonone-ir`, you can replace the existing script in `/etc/lirc/` with this one. If you don't own a Argon case and remote you can just ignore or delete the lircrc file. 
<br>

Note2: Also for archiving purposes, I included the bricklink studio file (`1.83inchLCDCasing.io`), which contains the design of the screen casing as shown in the image above.
