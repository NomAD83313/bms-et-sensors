import time
from board import SCL, SDA
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306

import subprocess
import netifaces 

# Create the I2C interface.
i2c = busio.I2C(SCL, SDA)

# Create the SSD1306 OLED class.
# The first two parameters are the pixel width and pixel height.  Change these
# to the right size for your display!
disp = adafruit_ssd1306.SSD1306_I2C(128, 32, i2c)

# Clear display.
disp.fill(0)
disp.show()

# Create blank image for drawing.
# Make sure to create image with mode '1' for 1-bit color.
width = disp.width
height = disp.height
image = Image.new("1", (width, height))

# Get drawing object to draw on image.
draw = ImageDraw.Draw(image)

# Draw a black filled box to clear the image.
draw.rectangle((0, 0, width, height), outline=0, fill=0)

# Draw some shapes.
# First define some constants to allow easy resizing of shapes.
padding = -2
top = padding
bottom = height - padding
# Move left to right keeping track of the current x position for drawing shapes.
x = 0


# Load default font.
font = ImageFont.load_default()



def clear():
        # Draw a black filled box to clear the image.
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        disp.image(image)
        disp.show()

def display():
    t0 = time.time()
    clear()
    while True:
        if (time.time()-t0 > 30):
            print("=================================")
            t0 = time.time()
        elif (time.time()-t0 > 25):
            print("clear")
            clear()

        elif (time.time()-t0 > 0):
            print("ip " + str(time.time() - t0))
            line = 0
            for n in netifaces.interfaces():
                try:
                    addrs = netifaces.ifaddresses(n)
                    text = str(n)+": "+addrs[netifaces.AF_INET][0]['addr']

                    draw.text((x, top + 8*line), text, font=font, fill=255)
                    line = line + 1
                except:
                    draw.text((x, top + 8*line), "Failure",  font=font, fill=255)
            line = 0
        disp.image(image)
        disp.show()
        
display()