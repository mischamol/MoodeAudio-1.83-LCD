#!/usr/bin/python3
#
# Script for displaying coverart from MoodeAudio on Waveshare 1.83 inch lcd screen.
# This script uses the library files and fonts that came with the Waveshare example (included in the lib folder).

import os
import sys 
import logging
import spidev as SPI
from lib import LCD_1inch83 #Waveshare lib
from PIL import Image, ImageOps, ImageDraw, ImageFont
import requests
import urllib.parse
from io import BytesIO
import time

# Raspberry Pi pin configuration:
RST = 27
DC = 25
BL = 18
bus = 0 
device = 0 
logging.basicConfig(level = logging.WARNING)
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # set working dir
Font = ImageFont.truetype("lib/Font02.ttf", 18)
VOLUME_CACHE_PATH = "/tmp/previous_volume.txt"

def getMetaData() -> tuple[str, str, str, str, str, str, str]:
    with open('/var/local/www/currentsong.txt', 'r') as file:
        data = dict(line.strip().split('=', 1) for line in file)
    source = data.get("file") 
    if source in ("Spotify Active", "AirPlay Active"):
        coverurl, title, artist = getExternalMetadata(source)
    else:
        coverurl = "http://localhost/" + urllib.parse.unquote(data.get("coverurl")).lstrip('/') #stream starts with /, files not
        title = data.get("title")
        artist = data.get("artist")
    return coverurl, title, artist, data.get("volume"), data.get("state"), source, data.get("mute")

def getExternalMetadata(source: str) -> tuple[str, str, str]:
    path = {"Spotify Active": "/var/local/www/spotmeta.txt", "AirPlay Active": "/var/local/www/aplmeta.txt"}[source]
    with open(path, 'r') as f:
        items = [i.strip() for line in f for i in line.strip().split('~~~')]
    coverurl = {
        "Spotify Active": lambda i: next(x for x in i if x.startswith("https://i.scdn.co/")), 
        "AirPlay Active": lambda i: "http://localhost/" + next(x for x in i if x.endswith(".jpg")) 
    }[source](items)
    return coverurl, items[0], items[1]

def getImage(coverurl: str) -> Image.Image:
    try:
        response = requests.get(coverurl)
        Image.open(BytesIO(response.content)).verify()
    except:
        response = requests.get("http://localhost/images/default-notfound-cover.jpg")     
    return Image.open(BytesIO(response.content))

def roundImage(image: Image.Image, radius: float) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    width, height = image.size
    draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    rounded_image = Image.new("RGBA", image.size)
    rounded_image.paste(image, (0, 0), mask=mask)
    return rounded_image

def drawImage(image: Image.Image, song: str, artist: str)-> Image.Image:
    image = image.resize((240, 240))
    image=roundImage(image, 40)
    image = ImageOps.pad(image, (240, 280), method=Image.Resampling.BILINEAR, color=(0, 0, 0), centering=(0.5, 0))
    draw = ImageDraw.Draw(image)
    draw.text((25, 240), artist , fill = "WHITE",font=Font)
    draw.text((25, 260), song , fill = "WHITE",font=Font)
    return image

def getPreviousVolume() -> str:
    try:
        with open(VOLUME_CACHE_PATH, 'r') as f:
            return f.read().strip()
    except:
        return -1

def setPreviousVolume(volume: str):
    try:
        with open(VOLUME_CACHE_PATH, 'w') as f:
            f.write(volume)
    except Exception as e:
        logging.warning(f"Error saving volume: {e}")

def drawOverlay(image: Image.Image, volume: str ="", state: str = "", mute: str="") -> Image.Image:
    overlay = image.convert("RGBA")
    image_width, image_height = overlay.size[0], overlay.size[0]
    center_x, center_y, radius = image_width // 2, image_height // 2, image_width // 3
    circle_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    ImageDraw.Draw(circle_layer).ellipse([center_x - radius, center_y - radius, center_x + radius, center_y + radius], fill=(100, 100, 100, 200))
    overlay = Image.alpha_composite(overlay, circle_layer)
    font = ImageFont.truetype("lib/Font02.ttf", 64)
    if mute=="1":
        text="Mute"
    elif state == "pause":
        text = "||"
    elif state == "stop":
        text = "■"
    else:
        text = f"{volume}%"
    text_size = font.getsize(text)
    text_position = ((image_width - text_size[0]) // 2, (image_height - text_size[1]) // 2)
    draw_text = ImageDraw.Draw(overlay)
    draw_text.text(text_position, text, font=font, fill=(255, 255, 255, 255))
    return overlay.convert("RGB")

try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.bl_DutyCycle(50)
    coverurl, song, artist, volume, state, source, mute = getMetaData()
    coverart = getImage(coverurl)
    screenImage = drawImage(coverart, song, artist)
    previous_volume = getPreviousVolume()
    image_to_show = screenImage
    if source not in ("Spotify Active", "AirPlay Active"):
        if volume != -1 and volume != previous_volume:
            overlay = drawOverlay(screenImage, volume, "")
            disp.ShowImage(overlay)
            time.sleep(1)
            setPreviousVolume(volume)
        if state in ("pause", "stop") or mute == "1":
            image_to_show = drawOverlay(screenImage, volume, state, mute)
    disp.ShowImage(image_to_show)
    disp.module_exit()
except IOError as e:
    logging.info(e)
