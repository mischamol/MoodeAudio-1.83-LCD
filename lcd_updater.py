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

def getMetaData() -> tuple[str, str, str, int]:
    with open('/var/local/www/currentsong.txt', 'r') as file:
        data = dict(line.strip().split('=', 1) for line in file)
    source = data.get("file") 
    if source in ("Spotify Active", "AirPlay Active"):
        return getExternalMetadata(source)
    coverurl = urllib.parse.unquote(data.get("coverurl")).lstrip('/') #locall files start with /, radio streams without
    return f"http://localhost/{coverurl}", data.get("title"), data.get("artist"), int(data.get("volume"))

def getExternalMetadata(source: str) -> tuple[str, str, str]:
    path = {"Spotify Active": "/var/local/www/spotmeta.txt", "AirPlay Active": "/var/local/www/aplmeta.txt"}[source]
    with open(path, 'r') as f:
        items = [i.strip() for line in f for i in line.strip().split('~~~')]
    coverurl = {
        "Spotify Active": lambda i: next(x for x in i if x.startswith("https://i.scdn.co/")), 
        "AirPlay Active": lambda i: "http://localhost/" + next(x for x in i if x.endswith(".jpg")) 
    }[source](items) # if source 'Spotify Active' look for first item that starts with http://...; if airplay look for first iten that ends with .jpg
    return coverurl, items[0], items[1]

def getImage(coverurl: str) -> Image.Image:
    try:
        response = requests.get(coverurl)
        Image.open(BytesIO(response.content)).verify()
    except: #if not a valid url or valid image fallback to default cover
        response = requests.get("http://localhost/images/default-notfound-cover.jpg")     
    return Image.open(BytesIO(response.content)) #need to re-open after verify()

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
    image = ImageOps.pad(image, (240, 280), method=Image.Resampling.BILINEAR, color=(0, 0, 0), centering=(0.5, 0)) #padding with black 
    draw = ImageDraw.Draw(image)
    draw.text((25, 240), artist , fill = "WHITE",font=Font)
    draw.text((25, 260), song , fill = "WHITE",font=Font)
    return image

def get_previous_volume() -> int:
    if os.path.exists(VOLUME_CACHE_PATH):
        try:
            with open(VOLUME_CACHE_PATH, 'r') as f:
                return int(f.read().strip())
        except:
            return -1
    return -1

def set_previous_volume(volume: int):
    try:
        with open(VOLUME_CACHE_PATH, 'w') as f:
            f.write(str(volume))
    except Exception as e:
        logging.warning(f"Error saving volume: {e}")

def draw_volume_overlay(image: Image.Image, volume: int) -> Image.Image:
    overlay = image.copy().convert("RGBA")
    width, height, radius = 240, 240, 40 
    rect = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    ImageDraw.Draw(rect).rounded_rectangle([(0, 0), (width, height)], radius=radius, fill=(100, 100, 100, 200))
    overlay = Image.alpha_composite(overlay, rect)
    font = ImageFont.truetype("lib/Font02.ttf", 72)
    text = f"{volume}%"
    text_size = font.getsize(text)
    pos = ((width - text_size[0]) // 2, (height - text_size[1]) // 2)
    draw = ImageDraw.Draw(overlay)
    draw.text(pos, text, font=font, fill=(255, 255, 255, 255))
    return overlay.convert("RGB")


try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.clear()
    disp.bl_DutyCycle(50) #set backlight brightness 
    coverurl, song, artist, volume = getMetaData()
    coverart=getImage(coverurl)
    screenImage=drawImage(coverart, song, artist)
    disp.ShowImage(screenImage)
    previous_volume = get_previous_volume()
    if volume != -1 and volume != previous_volume:
        overlay = draw_volume_overlay(screenImage, volume)
        disp.ShowImage(overlay)
        time.sleep(1)
        disp.ShowImage(screenImage)
        set_previous_volume(volume)
    disp.module_exit()
except IOError as e:
    logging.info(e)  
