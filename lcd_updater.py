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
VOLUME_CACHE_PATH = "/tmp/previousVolume.txt"

def getMetaData() -> tuple[str, str, str, str, str, str, str]:
    with open('/var/local/www/currentsong.txt', 'r') as file:
        data = dict(line.strip().split('=', 1) for line in file)
    source = data.get("file") 
    if source in ("Spotify Active", "AirPlay Active"): # * below is to unpack the tuple
        return (*getExternalMetadata(source), data.get("volume"), data.get("state"), source, data.get("mute"))
    coverurl = "http://localhost/" + urllib.parse.unquote(data.get("coverurl")).lstrip('/') #stream starts with /, files not
    return coverurl, data.get("title"), data.get("artist"), data.get("volume"), data.get("state"), source, data.get("mute")

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
    roundedImage = Image.new("RGBA", image.size)
    roundedImage.paste(image, (0, 0), mask=mask)
    return roundedImage

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
    imageWidth, imageHeight = overlay.size[0], overlay.size[0] #center on the coverart,not the entire screen
    centerX, centerY, radius = imageWidth // 2, imageHeight // 2, imageWidth // 3
    circleLayer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    ImageDraw.Draw(circleLayer).ellipse([centerX - radius, centerY - radius, centerX + radius, centerY + radius], fill=(100, 100, 100, 200))
    overlay = Image.alpha_composite(overlay, circleLayer)
    font = ImageFont.truetype("lib/Font02.ttf", 64)
    text = "Mute" if mute == "1" else "||" if state == "pause" else "■" if state == "stop" else f"{volume}%"
    textSize = font.getlength(text), font.size #drop in replacement for getsize
    textPosition = ((imageWidth - textSize[0]) // 2, (imageHeight - textSize[1]) // 2)
    drawText = ImageDraw.Draw(overlay)
    drawText.text(textPosition, text, font=font, fill=(255, 255, 255, 255))
    return overlay.convert("RGB")

try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.bl_DutyCycle(50)
    coverurl, song, artist, volume, state, source, mute = getMetaData()
    coverart = getImage(coverurl)
    screenImage = drawImage(coverart, song, artist)
    previousVolume = getPreviousVolume()
    imageToShow = screenImage
    if source not in ("Spotify Active", "AirPlay Active"):
        if volume != -1 and volume != previousVolume:
            overlay = drawOverlay(screenImage, volume, None, None) #no state and mute, because they supersede volume
            disp.ShowImage(overlay)
            time.sleep(1)
            setPreviousVolume(volume)
        if state in ("pause", "stop") or mute == "1":
            imageToShow = drawOverlay(screenImage, None, state, mute)
    disp.ShowImage(imageToShow)
    disp.module_exit()
except IOError as e:
    logging.info(e)
