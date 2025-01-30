# MoodeAudio Metadata on Waveshare 1.83-inch LCD

Script for displaying MoodeAudio cover art on a Waveshare 1.83-inch LCD screen. This script is based on the Waveshare example and uses the library files and fonts that were included with this example. For convenience these files are included in the  `lib` folder of this repo.

https://www.waveshare.com/wiki/1.83inch_LCD_Module

## Prerequisites
Connect the screen as covered in the abovementioned Waveshare wiki.

Enable SPI: `sudo raspi-config` ➡ Go to Interface Options and enable SPI.

Install the required library: `pip install RPi.GPIO`

## Installation
You can place `lcd_updater.py` and the `lib` folder under `/var/local/www/commandw/` and enable the LCD updater in MoodeAudio under ➡ Configure → Peripherals.

⚠ Note: `lcd_updater.py` is replaced with a stub after every update, so keep a backup elsewhere. Additionally, use a systemd watcher on `spotmeta.txt` to include Spotify metadata.

## Alternative Installation Method
Instead of enabling the LCD updater in MoodeAudio, you can use a systemd watcher for both `currentsong.txt` and `spotmeta.txt`. As a result, you can place this repository wherever you want—for example, in your home folder, which remains untouched during updates.

Ensure the correct permissions: `sudo chmod -R 777 lcd`

## Example of systemd watcher configuration for `spotmeta.txt`
```
sudo nano /etc/systemd/system/spotwatcher.service

[Unit]
Description = Run LCD_updater.py on spotmeta change
ConditionPathExists=/var/local/www/spotmeta.txt

[Service]
ExecStart=/usr/bin/python3 ~/lcd/lcd_updater.py

sudo nano /etc/systemd/system/spotwatcher.path

[Unit]
Description=Monitor spotmeta.txt and trigger spotwatcher service

[Path]
PathModified=/var/local/www/spotmeta.txt

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload
sudo systemctl enable --now spotwatcher.path
```

## Example of optional systemd watcher configuration for `currentsong.txt`
```
sudo nano /etc/systemd/system/currentsong.service

[Unit]
Description = Run LCD_updater.py on currentsong.txt change
ConditionPathExists=/var/local/www/currentsong.txt

[Service]
ExecStart=/usr/bin/python3 ~/lcd/lcd_updater.py

sudo nano /etc/systemd/system/currentsong.path
[Unit]
Description=Monitor currentsong.txt and trigger currentsong service

[Path]
PathModified=/var/local/www/currentsong.txt

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload
sudo systemctl enable --now currentsong.path

```
