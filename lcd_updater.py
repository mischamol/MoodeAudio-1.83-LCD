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

# Raspberry Pi pin configuration:
RST = 27
DC = 25
BL = 18
bus = 0 
device = 0 
logging.basicConfig(level = logging.WARNING)
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # set working dir
Font = ImageFont.truetype("lib/Font02.ttf", 18)

def getMetaData() -> tuple[str, str, str]:
    with open('/var/local/www/currentsong.txt', 'r') as file:
        data = dict(line.strip().split('=', 1) for line in file)
    source = data.get("file") 
    if source in ("Spotify Active", "AirPlay Active"):
        return getExternalMetadata(source)
    coverurl = urllib.parse.unquote(data.get("coverurl")).lstrip('/') #locall files start with /, radio streams without
    return f"http://localhost/{coverurl}", data.get("title"), data.get("artist")

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

try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.clear()
    disp.bl_DutyCycle(50) #set backlight brightness 
    coverurl, song, artist = getMetaData()
    coverart=getImage(coverurl)
    screenImage=drawImage(coverart, song, artist)
    disp.ShowImage(screenImage)
    disp.module_exit()
except IOError as e:
    logging.info(e)  
