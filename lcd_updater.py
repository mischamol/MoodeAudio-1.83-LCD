#!/usr/bin/python3
#
# Script for displaying MoodeAudio coverart on waveshare 1.83 inch lcd screen
# part of this code including some of the included libraries come from the waveshare example files

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
#logging.basicConfig(level = logging.DEBUG)
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # set working dir
Font = ImageFont.truetype("lib/Font02.ttf", 18)

def roundImage(image, radius) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    width, height = image.size
    draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    rounded_image = Image.new("RGBA", image.size)
    rounded_image.paste(image, (0, 0), mask=mask)
    return rounded_image


def getMetaData() -> tuple[str, str, str]:
    data = {}
    with open('/var/local/www/currentsong.txt', 'r') as file:
        for line in file:
            key, value = line.strip().split('=', 1)  # split on first '='
            data[key] = value
        if data.get("file")=="Spotify Active": #spotify uses spotmeta.txt instead of currentsong.txt
            with open('/var/local/www/spotmeta.txt', 'r') as spotfile:
                spotdata=spotfile.readline().split('~~~') #first line consists of artist,song, album and coverart
            imageurl = next((item for item in spotdata if item.startswith("https://")), None).rstrip() 
            #return list elemment that starts with https:// => element location changes sometimes
            artist=spotdata[1]
            song=spotdata[0]
        else: #local file or radio stream
            coverurl=data.get("coverurl").replace('%2F', '/')
            if coverurl.startswith('/'):
                coverurl = coverurl[1:] #strip first "/" which is added when playing local files, but not with streams)
            imageurl='http://localhost/'+ coverurl
            song=data.get("title")
            artist=data.get("artist")
        #print(imageurl)
    return imageurl, song, artist

try:
    disp = LCD_1inch83.LCD_1inch83(spi=SPI.SpiDev(bus, device),spi_freq=10000000,rst=RST,dc=DC,bl=BL)
    disp.Init()
    disp.clear()
    disp.bl_DutyCycle(50) #set backlight brightness (examplescript: 50)
    imageurl, song, artist =getMetaData()
    response = requests.get(imageurl)
    if response.status_code!=200: #if not ok
        response = requests.get("http://localhost/images/default-album-cover.png") #fallback to default cover         
    image=Image.open(BytesIO(response.content))
    image = image.resize((240, 240))
    image=roundImage(image, 40)
    image = ImageOps.pad(image, (240, 280), method=Image.Resampling.BILINEAR, color=(0, 0, 0), centering=(0.5, 0)) #padding with black 
    draw = ImageDraw.Draw(image)
    draw.text((25, 240), artist , fill = "WHITE",font=Font)
    draw.text((25, 260), song , fill = "WHITE",font=Font)
    disp.ShowImage(image)
    disp.module_exit()
except IOError as e:
    logging.info(e)  
