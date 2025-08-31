from lora import *
from utime import sleep_ms, ticks_ms

# --- CONFIGURACIÓN ---
APP_EUI = "0000000000000000"
APP_KEY = "2C6B6BE6AFE9E9DBFA3406ABEA253736"
PORT    = 37  # Puerto en TTN para identificar este script

# Crear objeto LoRa en EU868, sin polling automático
lora = Lora(band=BAND_EU868, poll_ms=0, debug=True)

print("Firmware:", lora.get_fw_version())
print("Device EUI:", lora.get_device_eui())

# Forzar SF12 (DR0) para máximo alcance si la librería lo soporta
try:
    lora.set_datarate(0)
    print("Data rate:", lora.get_datarate())
except Exception as e:
    print("No se pudo cambiar DR:", e)

# Función para comprobar join sin usar poll
def wait_for_join(timeout_ms=180000):
    """Espera hasta timeout_ms a que NJS sea 1."""
    start = ticks_ms()
    while (ticks_ms() - start) < timeout_ms:
        try:
            if lora.get_join_status():
                return True
        except:
            pass
        sleep_ms(500)
    return False

# --- JOIN OTAA ---
print("Iniciando join OTAA...")
try:
    joined = lora.join_OTAA(APP_EUI, APP_KEY, timeout=90000)  # Espera inicial 90s
except LoraErrorTimeout:
    print("Timeout inicial de join, esperando join-accept tardío...")
    joined = False
except LoraErrorBusy:
    print("Módem ocupado, reintentar más tarde.")
    joined = False
except Exception as e:
    print("Error en join:", e)
    joined = False

# Espera pasiva si no está unido
if not joined:
    joined = wait_for_join(180000)  # 3 min más de espera pasiva

if not joined:
    raise SystemExit("No se pudo unir a TTN. Revisa Live Data y espera 2–3 min antes de reintentar.")

print("¡Conectado a TTN!")

# --- ENVÍO DE MENSAJE DE PRUEBA ---
lora.set_port(PORT)
run_id = str((ticks_ms() // 1000) % 100000)  # identificador único de esta ejecución
payload = "RUN%s#1" % run_id

try:
    if lora.send_data(payload, False):  # False = no confirmado (más rápido)
        print("Uplink enviado: '%s' en FPort %d" % (payload, PORT))
    else:
        print("Uplink no confirmado enviado.")
except LoraErrorTimeout:
    print("Timeout al enviar uplink.")
except Exception as e:
    print("Error al enviar uplink:", e)

# --- RECEPCIÓN DE DOWNLINKS ---
while True:
    try:
        if lora.available():
            data = lora.receive_data()
            if data:
                print("Downlink recibido en puerto", data["port"], ":", data["data"])
        lora.poll()  # Ahora sí se puede usar poll
    except Exception as e:
        print("Error en bucle:", e)
    sleep_ms(1000)
