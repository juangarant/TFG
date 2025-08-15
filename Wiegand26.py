# adaptado para microPhyton con Thonny
# Leer codigo Wiegand 26 de un lector RFID usando PortentaH7 (D14=D0, D13=D1)
# usando dos vectores de interrupcion

from machine import Pin
import time


# --- Config de pines --- para Portenta H7
D0_PIN = "D14"
D1_PIN = "D13"

pin_d0 = Pin(D0_PIN, Pin.IN, Pin.PULL_UP)
pin_d1 = Pin(D1_PIN, Pin.IN, Pin.PULL_UP)

# variables globales usado por IRQ
last_wiegand_ms = 0
card_value      = 0
bit_count       = 0
lecturas_ok     = 0

# Referencia funciones globales para agilizar su uso en la IRQ, pre-capturamos referencias
_ticks_ms = time.ticks_ms
_ticks_diff = time.ticks_diff

# --- Funciones auxiliares ---

def cuenta_unos(val: int) -> int:
    c = 0
    while val:
        c += (val & 1)
        val >>= 1
    return c

def paridad_ok(dato: int) -> bool:
    # Wiegand 26:
    # bit 25 (MSB de 26 bits) = paridad par sobre bits 24..13 (12 bits) —aqui se usa convención común: MSB primero—
    # bit 0  (LSB)            = paridad impar sobre bits 12..1 (12 bits)
    parity1 = bool ((dato >> 25) & 1)      # bit 25 (MSB)
    parity2 = bool (dato & 1)              # bit 0 (LSB)

    # 12 bits intermedios altos: bits 24..13  -> desplazamos 13 y tomamos 12 bits
    cadena1 = (dato >> 13) & 0xFFF
    # 12 bits intermedios bajos:  bits 12..1  -> desplazamos 1 y tomamos 12 bits
    cadena2 = (dato >> 1) & 0xFFF

    # En W26 estándar:
    #  - primer bit = paridad **par** de la mitad alta -> debe ser 1 si los 12 bits tienen #impares
    #  - último bit = paridad **impar** de la mitad baja -> debe ser 1 si los 12 bits tienen #pares

    # Paridad par: el bit de paridad es igual al número de unos módulo 2
    parity1_ok = parity1 == (cuenta_unos(cadena1) % 2 == 1)
    # Paridad impar: el bit de paridad es el opuesto al número de unos módulo 2
    parity2_ok = parity2 == (cuenta_unos(cadena2) % 2 == 0)

    return parity1_ok and parity2_ok


# --- rutinas para Manejadores de interrupción ---
def _handle_d0(pin):				# Añade un 0 por la der
    global card_value, bit_count, last_wiegand_ms
    card_value = (card_value << 1)
    bit_count += 1
    last_wiegand_ms = _ticks_ms()

def _handle_d1(pin):				# Añade un 1 (desplaza y OR 1)
    global card_value, bit_count, last_wiegand_ms
    card_value = (card_value << 1) | 1
    bit_count += 1
    last_wiegand_ms = _ticks_ms()

# Registrar IRQ por flanco de bajada: FALLING, Otros modos: RISING, CHANGE (Wiegand es activo-bajo)
pin_d0.irq(trigger=Pin.IRQ_FALLING, handler=_handle_d0)
pin_d1.irq(trigger=Pin.IRQ_FALLING, handler=_handle_d1)

def reset_buffer():
    global card_value, bit_count
    card_value = 0
    bit_count = 0

print("Wiegand-26 listo. Esperando datos...")

# --- Bucle principal ---
TIMEOUT_MS = 50		# timeOut maximo 50 ms sin cambios desde ultimo bits recibido

while True:
    if bit_count > 0:
        # ¿Se acabó el frame
        if _ticks_diff(_ticks_ms(), last_wiegand_ms) > TIMEOUT_MS:
            bc = bit_count
            raw = card_value

            if bc == 26:  	# frame de 26 bits
                if paridad_ok(raw): lecturas_ok=lecturas_ok+1
                else:  print("Error de paridad")
                site_code = (raw >> 17) & 0xFF		# Site code: bits 24..17 (8 bits) -> (>>17) & 0xFF
                user_code = (raw >> 1) & 0xFFFF		# User code: bits 16..1 (16 bits) -> (>>1) & 0xFFFF

                print("Bits = {}, Site_Code = {}, User_Code = {}".format(bc, site_code, user_code))
                print("Raw ok ({}): {:026b}\n".format(lecturas_ok,raw))
            else:
                print("Error: {} bits recibidos -> {:b}\n".format(bc, raw))

            reset_buffer()
            #print("Buffer borrado --------------\n")


    time.sleep_ms(2)	# sleep para no saturar la CPU
