import network, time
from umqtt_simple import MQTTClient
import ssl

SSID = "XXX"
PASSWORD = "XXX"

MQTT_BROKER = "uf867860.ala.eu-central-1.emqxsl.com"
MQTT_PORT   = 8883
MQTT_USER   = "portenta01"
MQTT_PASS   = "portenta01"
MQTT_TOPIC  = b"tfg/datos"

CA_PATH = "/flash/emqxsl-ca.crt"   # pon aquí tu .crt; si falla, convierte a .der y cambia la ruta

# Wi‑Fi
wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect(SSID, PASSWORD)
t0 = time.ticks_ms()
while not wifi.isconnected():
    if time.ticks_diff(time.ticks_ms(), t0) > 20000:
        raise RuntimeError("Timeout Wi‑Fi")
    time.sleep_ms(200)
print("Wi‑Fi:", wifi.ifconfig())

# TLS
ssl_params = {
    "cert_reqs": ssl.CERT_REQUIRED,
    "ca_certs": CA_PATH,   # si tu build no soporta PEM, usa "/flash/emqxsl-ca.der"
}

try:
    client = MQTTClient(
        client_id="portenta-client",
        server=MQTT_BROKER,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASS,
        keepalive=60,
        ssl=True,
        ssl_params=ssl_params,
    )
    client.connect()
    print("MQTTS conectado (CA verificado)")
except Exception as e:
    print("Fallo TLS con CA:", e)
    # Fallback temporal: cifrado sin validar CA (solo para pruebas)
    client = MQTTClient(
        client_id="portenta-client",
        server=MQTT_BROKER,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASS,
        keepalive=60,
        ssl=True,
        ssl_params={"cert_reqs": ssl.CERT_NONE},
    )
    client.connect()
    print("MQTTS conectado (sin verificación CA)")

payload = b'{"operator_id":"17614840","helmet_ok":true,"device":"portenta01"}'
client.publish(MQTT_TOPIC, payload)
print("Publicado:", payload)

client.disconnect()
