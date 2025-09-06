# main.py — Portenta H7 + Vision Shield
# - Wiegand-26 (RFID)
# - FOMO (casco / nocasco)
# - CSV + fotos en SD
# - Subida a Supabase (upload-month)
# - Auto-actualiza ACL (cards.csv) desde Supabase (cards-manifest)
#
# Requisitos en / (raíz SD):
#   /config/server.json   -> { function_url, cards_url, edge_api_key }
#   /config/cards.csv
#   /data/, /media/, /model/
#   storage_local.py, cloud_sync.py, cards_sync.py, wifi_setup.py (opcional)

from machine import Pin
import time, math, uos, gc
import sensor, image
import ml
import pyb

# ===== Módulos propios =====
import storage_local as db
import cloud_sync as cloud
import cards_sync

# ===== Wi-Fi + NTP =====
_have_network = False
try:
    import network
    _have_network = True
except:
    _have_network = False

try:
    from wifi_setup import wifi_connect_and_ntp_local
    if _have_network:
        wifi_connect_and_ntp_local()   # Ajusta RTC (CET/CEST)
    else:
        raise Exception("Módulo 'network' no disponible")
except Exception as e:
    pyb.RTC().datetime((2025, 8, 15, 5, 17, 30, 0, 0))
    print("Wi-Fi/NTP no disponible:", e)
    print("Fecha por defecto aplicada: 15/08/2025 17:30:00")

# ===== Rutas de trabajo (SD como raíz) =====
db.BASE_SD    = "/"
db.MEDIA_DIR  = "/media"
db.CONFIG_DIR = "/config"
db.DATA_DIR   = "/data"
db.CARDS_CSV  = "/config/cards.csv"

# =========================
# LEDs
# =========================
redLED   = pyb.LED(1)
greenLED = pyb.LED(2)
blueLED  = pyb.LED(3)

def _led_all_off():
    redLED.off(); greenLED.off(); blueLED.off()

def led_show(color="blue", duration_ms=800):
    _led_all_off()
    (redLED if color=="red" else greenLED if color=="green" else blueLED).on()
    pyb.delay(duration_ms)
    _led_all_off()

# =========================
# FOMO / inferencia
# =========================
MIN_CONFIDENCE     = 0.40
MAX_FRAMES_CHECK   = 8
EARLY_STOP_ON_HIT  = True

# =========================
# Pines Wiegand
# =========================
D0_PIN = "D14"
D1_PIN = "D13"
TIMEOUT_MS = 50
ANTIREBOTE_MS = 800

CARD_COOLDOWN_MS      = 6000
EVENT_DEDUP_WINDOW_MS = 6000

pin_d0 = Pin(D0_PIN, Pin.IN, Pin.PULL_UP)
pin_d1 = Pin(D1_PIN, Pin.IN, Pin.PULL_UP)

last_wiegand_ms = 0
card_value = 0
bit_count = 0

_ticks_ms   = time.ticks_ms
_ticks_diff = time.ticks_diff

recent_raw26      = {}  # raw26 -> ticks
recent_card_logic = {}  # (site,user) -> ticks

_last_event_key = None
_last_event_ts  = 0

def _count_ones(val: int) -> int:
    c = 0
    while val:
        c += (val & 1)
        val >>= 1
    return c

def _paridad_ok(raw26: int) -> bool:
    parity_msb = bool((raw26 >> 25) & 1)
    parity_lsb = bool(raw26 & 1)
    hi12 = (raw26 >> 13) & 0xFFF
    lo12 = (raw26 >> 1) & 0xFFF
    p1_ok = ((_count_ones(hi12) % 2) == 0) == parity_msb
    p2_ok = ((_count_ones(lo12) % 2) == 1) == parity_lsb
    return p1_ok and p2_ok

def _irq_d0(pin):
    global card_value, bit_count, last_wiegand_ms
    card_value = (card_value << 1)
    bit_count += 1
    last_wiegand_ms = _ticks_ms()

def _irq_d1(pin):
    global card_value, bit_count, last_wiegand_ms
    card_value = (card_value << 1) | 1
    bit_count += 1
    last_wiegand_ms = _ticks_ms()

pin_d0.irq(trigger=Pin.IRQ_FALLING, handler=_irq_d0)
pin_d1.irq(trigger=Pin.IRQ_FALLING, handler=_irq_d1)

def _reset_wiegand():
    global card_value, bit_count
    card_value = 0
    bit_count = 0

def _extract_fields(raw26: int):
    # [P1][8b site][16b user][P2]
    site_code = (raw26 >> 17) & 0xFF
    user_code = (raw26 >> 1)  & 0xFFFF
    return site_code, user_code

# =========================
# Inicialización storage + ACL
# =========================
n_cards = db.init_storage()
print("ACL cargada con", n_cards, "tarjetas")

# =========================
# Cámara
# =========================
sensor.reset()
sensor.set_pixformat(sensor.GRAYSCALE)
sensor.set_framesize(sensor.QVGA)      # 320x240
sensor.set_windowing((240, 240))       # recorte cuadrado para FOMO
sensor.skip_frames(time=2000)

# =========================
# Modelo TFLite + labels
# =========================
try:
    load_to_fb = uos.stat("/model/trained.tflite")[6] > (gc.mem_free() - (64*1024))
    net = ml.Model("/model/trained.tflite", load_to_fb=load_to_fb)
    print("Modelo TFLite cargado.")
except Exception as e:
    raise Exception('No se pudo cargar "/model/trained.tflite": ' + str(e))

try:
    labels = [line.rstrip('\n') for line in open("/model/labels.txt")]
    print("Etiquetas:", labels)
except Exception as e:
    raise Exception('No se pudo cargar "/model/labels.txt": ' + str(e))

try:
    IDX_CASCO   = labels.index('casco')
    IDX_NOCASCO = labels.index('nocasco')
except ValueError:
    raise RuntimeError("labels.txt debe contener 'casco' y 'nocasco' (además de 'background').")

threshold_list = [(int(MIN_CONFIDENCE * 255 + 0.5), 255)]

def fomo_post_process(model, inputs, outputs):
    ob, oh, ow, oc = model.output_shape[0]
    x_scale = inputs[0].roi[2] / ow
    y_scale = inputs[0].roi[3] / oh
    scale = min(x_scale, y_scale)
    x_offset = ((inputs[0].roi[2] - (ow * scale)) / 2) + inputs[0].roi[0]
    y_offset = ((inputs[0].roi[3] - (oh * scale)) / 2) + inputs[0].roi[1]
    l = [[] for _ in range(oc)]
    for i in range(oc):
        img = image.Image(outputs[0][0, :, :, i] * 255)
        blobs = img.find_blobs(threshold_list, x_stride=1, y_stride=1, area_threshold=1, pixels_threshold=1)
        for b in blobs:
            x,y,w,h = b.rect()
            score = img.get_statistics(thresholds=threshold_list, roi=(x,y,w,h)).l_mean()/255.0
            x = int((x * scale) + x_offset); y = int((y * scale) + y_offset)
            w = int(w * scale); h = int(h * scale)
            l[i].append((x,y,w,h,score))
    return l

def detect_once_counts():
    img = sensor.snapshot()
    results = net.predict([img], callback=fomo_post_process)
    casco_count = nocasco_count = 0
    casco_best = nocasco_best = 0.0
    for i, det_list in enumerate(results):
        if i == 0: continue
        if i == IDX_CASCO:
            for (_x,_y,_w,_h,score) in det_list:
                if score < MIN_CONFIDENCE: continue
                casco_count += 1; casco_best = max(casco_best, score)
        elif i == IDX_NOCASCO:
            for (_x,_y,_w,_h,score) in det_list:
                if score < MIN_CONFIDENCE: continue
                nocasco_count += 1; nocasco_best = max(nocasco_best, score)
    return (casco_count, casco_best, nocasco_count, nocasco_best)

def decide_helmet(MAX_FRAMES=8, EARLY_STOP=True):
    tot_casco = tot_nocasco = 0
    best_casco = best_nocasco = 0.0
    for _ in range(MAX_FRAMES):
        c_cnt, c_best, n_cnt, n_best = detect_once_counts()
        tot_casco += c_cnt; tot_nocasco += n_cnt
        best_casco = max(best_casco, c_best); best_nocasco = max(best_nocasco, n_best)
        if EARLY_STOP and (tot_casco >= 2 and tot_casco > tot_nocasco) and best_casco >= MIN_CONFIDENCE: break
        if EARLY_STOP and (tot_nocasco >= 2 and tot_nocasco > tot_casco) and best_nocasco >= MIN_CONFIDENCE: break
    if (tot_casco == 0 and tot_nocasco == 0):
        return (False, 0.0)  # fallback seguro: NO CASCO
    if tot_casco != tot_nocasco:
        return (tot_casco > tot_nocasco), (best_casco if tot_casco > tot_nocasco else best_nocasco)
    return (best_casco > best_nocasco), max(best_casco, best_nocasco)  # empates -> NO CASCO


# =========================
# Sync a la nube (CSV mensual)
# =========================
SYNC_COOLDOWN_MS = 8000
_last_sync_ms    = -600000

def _yyyymm_now():
    y, m = time.localtime()[0], time.localtime()[1]
    return "%04d%02d" % (y, m)

def _sync_current_month(tag=""):
    global _last_sync_ms
    now = _ticks_ms()
    if _ticks_diff(now, _last_sync_ms) < SYNC_COOLDOWN_MS:
        return
    yyyymm = _yyyymm_now()
    try:
        db.update_manifest()
    except Exception as e:
        print("update_manifest error:", e)
    print("[cloud] Subiendo", yyyymm, ("(%s)" % tag) if tag else "")
    resp = cloud.upload_month(yyyymm)
    print("[cloud] Respuesta:", resp)
    if resp and resp.get("ok") and resp.get("verified", False):
        led_show("blue", 200)
    _last_sync_ms = now

# =========================
# Auto-update de ACL (cards.csv) desde Supabase
# =========================
def _reload_acl():
    try:
        db.init_storage()
        print("ACL recargada desde cards.csv")
    except Exception as e:
        print("Error recargando ACL:", e)

# Chequeo inicial (si hay Wi-Fi)
try:
    cards_sync.ensure_cards_updated(db_reload_fn=_reload_acl)
except Exception as e:
    print("[ACL] Chequeo inicial fallido:", e)

_POLL_ACL_MS  = 10 * 60 * 1000  # 10 min
_last_poll_ms = -600000

def _poll_cards_if_due():
    global _last_poll_ms
    now = _ticks_ms()
    if _ticks_diff(now, _last_poll_ms) < _POLL_ACL_MS:
        return
    _last_poll_ms = now
    try:
        res = cards_sync.ensure_cards_updated(db_reload_fn=_reload_acl)
        if res.get("updated"):
            print("[ACL] Actualizada a versión", res.get("version"))
    except Exception as e:
        print("[ACL] Poll error:", e)

# =========================
# Reintentos Wi-Fi/NTP (background)
# =========================
_WIFI_RETRY_COOLDOWN_MS = 60000
_last_wifi_try_ms       = -60000
_wifi_was_connected     = False

def _wifi_retry_tick():
    global _last_wifi_try_ms, _wifi_was_connected
    if not _have_network:
        return
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
    except:
        return

    if sta.isconnected():
        if not _wifi_was_connected:
            print("Wi-Fi conectado:", sta.ifconfig())
            _wifi_was_connected = True
            # Tras reconectar: ajustar NTP y chequear ACL
            try:
                wifi_connect_and_ntp_local()
            except Exception as e:
                print("NTP tras reconexión fallido:", e)
            try:
                cards_sync.ensure_cards_updated(db_reload_fn=_reload_acl)
            except Exception as e:
                print("[ACL] Chequeo tras reconexión fallido:", e)
        return

    _wifi_was_connected = False
    now = _ticks_ms()
    if _ticks_diff(now, _last_wifi_try_ms) < _WIFI_RETRY_COOLDOWN_MS:
        return
    try:
        wifi_connect_and_ntp_local()
        print("Reconexión OK + NTP ajustado.")
        _wifi_was_connected = True
        try:
            cards_sync.ensure_cards_updated(db_reload_fn=_reload_acl)
        except Exception as e:
            print("[ACL] Chequeo tras reconexión fallido:", e)
    except Exception as e:
        print("Reintento Wi-Fi fallido:", e)
    finally:
        _last_wifi_try_ms = now

# =========================
# Arranque visual
# =========================
print("Wiegand-26 listo. Esperando tarjetas...")
_led_all_off()
for _ in range(2):
    greenLED.on(); pyb.delay(200); greenLED.off(); pyb.delay(200)

# =========================
# Bucle principal
# =========================
while True:
    _wifi_retry_tick()
    _poll_cards_if_due()

    if bit_count > 0:
        if _ticks_diff(_ticks_ms(), last_wiegand_ms) > TIMEOUT_MS:
            bc = bit_count; raw26 = card_value; _reset_wiegand()

            if bc == 26 and _paridad_ok(raw26):
                now = _ticks_ms()

                last_raw = recent_raw26.get(raw26)
                if (last_raw is not None) and (_ticks_diff(now, last_raw) < CARD_COOLDOWN_MS):
                    continue
                recent_raw26[raw26] = now

                site_code, user_code = _extract_fields(raw26)

                last_logic = recent_card_logic.get((site_code, user_code))
                if (last_logic is not None) and (_ticks_diff(now, last_logic) < CARD_COOLDOWN_MS):
                    continue
                recent_card_logic[(site_code, user_code)] = now

                print("Tarjeta -> Bits=26, RAW26={}, Site={}, User={}".format(raw26, site_code, user_code))

                autorizado, nombre = db.is_card_authorized(site_code, user_code)
                if not autorizado:
                    print("ACCESO DENEGADO: tarjeta no autorizada.\n")
                    ev_key = (site_code, user_code, False, False)
                    if (_last_event_key == ev_key) and (_ticks_diff(now, _last_event_ts) < EVENT_DEDUP_WINDOW_MS):
                        led_show("blue", 600)
                        continue
                    led_show("blue", 600)
                    db.append_event(raw26, site_code, user_code, "", False, False, 0.0, "")
                    db.update_manifest()
                    _last_event_key, _last_event_ts = ev_key, now
                    _sync_current_month(tag="no_autorizado")
                    time.sleep_ms(250)
                    continue

                print("Tarjeta autorizada ({}). Detección...".format(nombre))

                is_helmet, score = decide_helmet(MAX_FRAMES=MAX_FRAMES_CHECK, EARLY_STOP=EARLY_STOP_ON_HIT)

                ev_key = (site_code, user_code, True, bool(is_helmet))
                if (_last_event_key == ev_key) and (_ticks_diff(now, _last_event_ts) < EVENT_DEDUP_WINDOW_MS):
                    led_show("green" if is_helmet else "red", 500)
                    _sync_current_month(tag="dedup")
                    continue

                proof_img = sensor.snapshot()
                img_path = db.save_proof_image_if_needed(proof_img, raw26, is_helmet)
                db.append_event(raw26, site_code, user_code, nombre, True, is_helmet, score, img_path)

                if is_helmet:
                    led_show("green", 800)
                    print("ACCESO PERMITIDO (casco). Score: {:.2f}\n".format(score))
                else:
                    led_show("red", 800)
                    print("ACCESO DENEGADO (nocasco). Score: {:.2f}\n".format(score))

                db.update_manifest()
                _last_event_key, _last_event_ts = ev_key, now
                _sync_current_month(tag="autorizado")
                time.sleep_ms(250)
            else:
                print("Trama W26 inválida: bits={} raw={:b}".format(bc, raw26))

    time.sleep_ms(2)
