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
    data = {}
    with open('/var/local/www/currentsong.txt', 'r') as file:
        for line in file:
            key, value = line.strip().split('=', 1)  # split on first '='
            data[key] = value
        if data.get("file")=="Spotify Active": #spotify uses spotmeta.txt instead of currentsong.txt
            imageurl, song, artist= getSpotMetaData()
        elif data.get("file")=="AirPlay Active": #airplay
            imageurl, song, artist="http://localhost/images/default-notfound-cover.jpg", data.get("outrate"), "Airplay"
        else: #local file or radio stream
            coverurl=data.get("coverurl").replace('%2F', '/')
            if coverurl.startswith('/'):
                coverurl = coverurl[1:] #strip first "/" which is added when playing local files, but not with streams)
            imageurl='http://localhost/'+ coverurl
            song=data.get("title")
            artist=data.get("artist")
        #print(imageurl)
    return imageurl, song, artist

def getSpotMetaData() -> tuple[str, str, str]:
    with open('/var/local/www/spotmeta.txt', 'r') as spotfile:
        spotdata=spotfile.readline().split('~~~')
        song=spotdata[0]
        artist=spotdata[1]
        imageurl = next((item.strip() for item in spotdata if item.startswith("http")), None)
        #return first elemment that starts with http instead of using index (url index changes sometimes)
        if not imageurl: #url not found on first line (multiple artists)
            imageurl = next((item.strip() for line in spotfile for item in line.strip().split('~~~') if item.startswith("http")),"")
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
    imageurl, song, artist =getMetaData()
    coverart=getImage(imageurl)
    image=drawImage(coverart, song, artist)
    disp.ShowImage(image)
    disp.module_exit()
except IOError as e:
    logging.info(e)  
