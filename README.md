# MoodeAudio metadata on waveshare 1.83 inch LCD

Script for displaying MoodeAudio coverart on waveshare 1.83 inch lcd screen. Don't forget to enable SPI: sudo rasp-config => enable spi  and pip install RPi.GPiO. 

You can place this files under /var/local/www/commandw/ and enable the lcd updater in moodeaudio under 'configure' -> 'Periphals. However, lcd_updater.py is replaced by a stub after every update, so keep a backup somewhere else. Also use een systemd watcher on spotmeta.txt to include spotify metadata


Alternatively don't enable the lcd_updater in MoodeAudio and use een systemd watcher on both currentsong.txt and spotmeta.txt and place this repo in your home folder, which is left untouched during update.

Remember to sudo chmod -R 777 lcd.

## Example of systemd watcher configuration for spotmeta.txt
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

## Example of optional systemd watcher for currentsong.txt
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
