# Untitled - By: juang - Fri Jul 25 2025

import sensor
import time
import machine
import os
import gc

# Configuración
LABEL = "casco" #Nombre de la carpeta donde se guardan las imagenes
PIXFORMAT = sensor.GRAYSCALE
FRAMESIZE = sensor.QVGA #Tamaño de la imagen (QVGA=320x240)
BUTTON_PIN = "D14" #Pin de la portenta donde está conectado el botón

# Inicialización cámara
sensor.reset()
sensor.set_pixformat(PIXFORMAT)
sensor.set_framesize(FRAMESIZE)
sensor.skip_frames(time=2000)

# LED
led = machine.LED("LED_BLUE")
led.off()

# Botón
button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)  # Botón con resistencia pull-down
index = 0

# Crear carpeta si no existe
if LABEL not in os.listdir():
    os.mkdir(LABEL)

# Bucle principal
print("Listo. Capturando imágenes. Pulsa el botón para guardar.")

while True:
    img = sensor.snapshot()
    #print("%d" % (button.value()))  # Muestra el estado del botón

    if button.value() == 0:  # pulsado
        timestamp = time.ticks_ms()
        filename = "%s/img-%d_%d.jpg" % (LABEL, index, timestamp)
        img.save(filename)
        led.on()
        print("Imagen guardada:", filename)
        time.sleep_ms(300)  # evita guardar varias seguidas por un solo toque
        led.off()
        index += 1
        gc.collect()
