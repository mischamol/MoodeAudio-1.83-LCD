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

# Pin configuration and basic initialisation:
RST = 27
DC = 25
BL = 18
bus = 0 
device = 0 
logging.basicConfig(level = logging.WARNING)
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # set working dir
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
    roundedImage.paste(image, mask=mask)
    return roundedImage.convert("RGB")

def drawImage(image: Image.Image, song: str, artist: str)-> Image.Image:
    image = image.resize((240, 240))
    image=roundImage(image, 40)
    image = ImageOps.pad(image, (240, 280), method=Image.Resampling.BILINEAR, color=(0, 0, 0), centering=(0.5, 0))
    draw = ImageDraw.Draw(image)
    Font = ImageFont.truetype("lib/Font02.ttf", 18)
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

def addCircle(imageWidth: int, imageHeight: int, image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    centerX, centerY, radius = imageWidth // 2, imageHeight // 2, imageWidth // 3
    circleLayer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(circleLayer).ellipse([centerX - radius, centerY - radius, centerX + radius, centerY + radius], fill=(100, 100, 100, 200))
    image.paste(circleLayer, mask=circleLayer)
    return image.convert("RGB")

def drawOverlay(image: Image.Image, volume: str ="", state: str = "", mute: str="") -> Image.Image:
    imageWidth, imageHeight = image.size[0], image.size[0] #center on the coverart,not the entire screen
    image=addCircle(imageWidth, imageHeight, image)
    Font = ImageFont.truetype("lib/Font02.ttf", 64)
    text = "Mute" if mute == "1" else "||" if state == "pause" else "■" if state == "stop" else f"{volume}%"
    textPosition = ((imageWidth - Font.getlength(text)) // 2, (imageHeight - Font.size) // 2)
    draw = ImageDraw.Draw(image)
    draw.text(textPosition, text, font=Font, fill=(255, 255, 255, 255))
    return image

def determineOverlay(disp: LCD_1inch83, screenImage: Image.Image, volume: str, state: str, mute: str, source: str) -> Image.Image:
    if source not in ("Spotify Active", "AirPlay Active"):
        previousVolume = getPreviousVolume()
        if volume != -1 and volume != previousVolume:
            volumeOverlay = drawOverlay(screenImage, volume, None, None)  #no state and mute, because they supersede volume
            disp.ShowImage(volumeOverlay)
            time.sleep(1)
            setPreviousVolume(volume)
        if state in ("pause", "stop") or mute == "1":
            screenImage = drawOverlay(screenImage, None, state, mute)
    return screenImage

try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.bl_DutyCycle(50)
    coverurl, song, artist, volume, state, source, mute = getMetaData()
    coverart = getImage(coverurl)
    screenImage = drawImage(coverart, song, artist)
    screenImage = determineOverlay(disp, screenImage, volume, state, mute, source)
    disp.ShowImage(screenImage)
    disp.module_exit()
except IOError as e:
    logging.info(e)
