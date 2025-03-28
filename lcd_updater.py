#!/usr/bin/python3
#
# Script for displaying MoodeAudio coverart on Waveshare 1.83 inch lcd screen
# This script is loosely based on the Waveshare example and uses the library files and fonts 
# that came with this example. These files are included in the lib folder of this repo.

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
    if data.get("file")=="Spotify Active": #spotify uses spotmeta.txt instead of currentsong.txt
        return getSpotMetaData()
    elif data.get("file")=="AirPlay Active": #airplay
        return "http://localhost/images/default-notfound-cover.jpg", data.get("outrate"), "Airplay"
    coverurl = 'http://localhost/'+ urllib.parse.unquote(data.get("coverurl")).lstrip('/')# strip leading / (added by local files, but not by streams)
    return coverurl, data.get("title"), data.get("artist")

def getSpotMetaData() -> tuple[str, str, str]:
    with open('/var/local/www/spotmeta.txt', 'r') as spotfile:
        lines = spotfile.readlines()
    all_items = [item.strip() for line in lines for item in line.strip().split('~~~')]
    song, artist = all_items[0], all_items[1] 
    imageurl = next((item for item in all_items if item.startswith("http")), "")
    return imageurl, song, artist

def getImage(imageurl) -> Image.Image:
    response = requests.get(imageurl)
    if response.status_code!=200: #if not ok
        response = requests.get("http://localhost/images/default-notfound-cover.jpg") #fallback to default cover         
    image=Image.open(BytesIO(response.content))
    return image

def roundImage(image, radius) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    width, height = image.size
    draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    rounded_image = Image.new("RGBA", image.size)
    rounded_image.paste(image, (0, 0), mask=mask)
    return rounded_image

def drawImage(image, song, artist)-> Image.Image:
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
    imageurl, song, artist = getMetaData()
    image=getImage(imageurl)
    screenImage=drawImage(image, song, artist)
    disp.ShowImage(screenImage)
    disp.module_exit()
except IOError as e:
    logging.info(e)  
