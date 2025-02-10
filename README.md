# MoodeAudio Metadata on Waveshare 1.83-inch LCD

Script for displaying MoodeAudio cover art on a Waveshare 1.83-inch LCD screen. This script is loosely based on the Waveshare example and uses the library files and fonts that came with this example. These files are included in the `lib` folder of this repo.<br>

https://www.waveshare.com/wiki/1.83inch_LCD_Module

<img src="https://github.com/user-attachments/assets/8c38addf-272e-420d-8e9a-0f944b51a7f7" style="width:300px; height:auto;"><br>
<sub>The LCD in a temporary configuration consisting of an Argon One case and some Danish bricks.</sub><br>

## Prerequisites
Connect the screen according to the diagram below.
![image](https://github.com/user-attachments/assets/9180c546-3529-4612-8237-3af47816893e)<br> 
<sub>Waveshare connection diagram (source: https://www.waveshare.com/wiki/1.83inch_LCD_Module)</sub><br>

Enable SPI: `sudo raspi-config` ➡ Interface Options ➡ enable SPI.

Install the required library: `pip install RPi.GPIO`

## Installation
You can place `lcd_updater.py` and the `lib` folder under `/var/local/www/commandw/` and enable the LCD updater in MoodeAudio under ➡ Configure ➡ Peripherals. 

Set the permissions: `sudo chmod 755 lcd_updater.py` and `sudo chmod -R 755 bin`

⚠ Note: `lcd_updater.py` is replaced with a stub after every update, so keep a backup elsewhere. Additionally, use a systemd watcher on `spotmeta.txt` to include Spotify metadata.

## Alternative Installation Method
Instead of enabling the LCD updater in MoodeAudio, you can use a systemd watcher for both `currentsong.txt` and `spotmeta.txt`. As a result, you can place this repository wherever you want—for example, in your home folder (~), which remains untouched during updates. Personally, I prefer this method, as I got tired of copying everything back after every update.

Ensure the correct permissions: `sudo chmod -R 755 lcd`

## Example of systemd watcher configuration for `spotmeta.txt`
`sudo nano /etc/systemd/system/spotwatcher.service`
```
[Unit]
Description = Run LCD_updater.py on spotmeta change
ConditionPathExists=/var/local/www/spotmeta.txt

[Service]
ExecStart=/usr/bin/python3 ~/lcd/lcd_updater.py
```
`sudo nano /etc/systemd/system/spotwatcher.path`
```
[Unit]
Description=Monitor spotmeta.txt and trigger spotwatcher service

[Path]
PathModified=/var/local/www/spotmeta.txt

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl daemon-reload
sudo systemctl enable --now spotwatcher.path
```

## Example of optional systemd watcher configuration for `currentsong.txt`
`sudo nano /etc/systemd/system/currentsong.service`
```
[Unit]
Description = Run LCD_updater.py on currentsong.txt change
ConditionPathExists=/var/local/www/currentsong.txt

[Service]
ExecStart=/usr/bin/python3 ~/lcd/lcd_updater.py
```
`sudo nano /etc/systemd/system/currentsong.path`
```
[Unit]
Description=Monitor currentsong.txt and trigger currentsong service

[Path]
PathModified=/var/local/www/currentsong.txt

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl daemon-reload
sudo systemctl enable --now currentsong.path
```

Enjoy!
