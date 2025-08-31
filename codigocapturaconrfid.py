# --- Integración Wiegand26 + Edge Impulse FOMO (casco/nocasco) ---
# Portenta H7 + Vision Shield
# Pines Wiegand: D14 = D0, D13 = D1
# Modelo FOMO de Edge Impulse cargado como "trained"

from machine import Pin
import time, math
import sensor, image
import ml
from ml.utils import NMS

# =========================
# CONFIGURACIÓN
# =========================

# Tarjetas autorizadas (puedes usar RAW completo o (site_code, user_code))
AUTHORIZED_RAW26 = {
    #23098353: "Operario_A"  # Ejemplo si quieres usar RAW decimal
}

AUTHORIZED_TUPLES = {
    (148, 19828): "Operario_A"  # Ejemplo (site_code, user_code)
}

# Umbral de confianza
MIN_CONFIDENCE = 0.40
# Frames a capturar por tarjeta
MAX_FRAMES_CHECK = 8
EARLY_STOP_ON_HIT = True

# Pines Wiegand (Portenta H7)
D0_PIN = "D14"
D1_PIN = "D13"
TIMEOUT_MS = 50

# =========================
# WIEGAND 26 (IRQ + paridad)
# =========================

pin_d0 = Pin(D0_PIN, Pin.IN, Pin.PULL_UP)
pin_d1 = Pin(D1_PIN, Pin.IN, Pin.PULL_UP)

last_wiegand_ms = 0
card_value = 0
bit_count = 0
lecturas_ok = 0

_ticks_ms = time.ticks_ms
_ticks_diff = time.ticks_diff

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
    site_code = (raw26 >> 17) & 0xFF
    user_code = (raw26 >> 1) & 0xFFFF
    return site_code, user_code

# =========================
# MODELO EDGE IMPULSE (FOMO)
# =========================

sensor.reset()
sensor.set_pixformat(sensor.GRAYSCALE)
sensor.set_framesize(sensor.QVGA)
sensor.skip_frames(time=2000)

model = ml.Model("trained")
labels = model.labels  # ['background', 'casco', 'nocasco']

try:
    IDX_CASCO   = labels.index('casco')
    IDX_NOCASCO = labels.index('nocasco')
except ValueError:
    raise RuntimeError("El modelo no contiene etiquetas 'casco' y 'nocasco'.")

threshold_list = [(math.ceil(MIN_CONFIDENCE * 255), 255)]

def fomo_post_process(model_obj, inputs, outputs):
    n, oh, ow, oc = model_obj.output_shape[0]
    nms = NMS(ow, oh, inputs[0].roi)
    for i in range(oc):
        img = image.Image(outputs[0][0, :, :, i] * 255)
        blobs = img.find_blobs(threshold_list, x_stride=1, area_threshold=1, pixels_threshold=1)
        for b in blobs:
            x, y, w, h = b.rect()
            score = img.get_statistics(thresholds=threshold_list, roi=(x,y,w,h)).l_mean()/255.0
            nms.add_bounding_box(x, y, x+w, y+h, score, i)
    return nms.get_bounding_boxes()

def detect_once_counts():
    img = sensor.snapshot()
    lists_per_class = model.predict([img], callback=fomo_post_process)

    casco_count, nocasco_count = 0, 0
    casco_best, nocasco_best = 0.0, 0.0

    for i, det_list in enumerate(lists_per_class):
        if i == 0:
            continue
        for (_x,_y,_w,_h), score in det_list:
            if score < MIN_CONFIDENCE:
                continue
            if i == IDX_CASCO:
                casco_count += 1
                if score > casco_best: casco_best = score
            elif i == IDX_NOCASCO:
                nocasco_count += 1
                if score > nocasco_best: nocasco_best = score

    return (casco_count, casco_best, nocasco_count, nocasco_best)

def decide_helmet(MAX_FRAMES=8, EARLY_STOP=True):
    tot_casco = tot_nocasco = 0
    best_casco = best_nocasco = 0.0

    for _ in range(MAX_FRAMES):
        c_cnt, c_best, n_cnt, n_best = detect_once_counts()
        tot_casco += c_cnt
        tot_nocasco += n_cnt
        if c_best > best_casco: best_casco = c_best
        if n_best > best_nocasco: best_nocasco = n_best

        if EARLY_STOP and (tot_casco >= 2 and tot_casco > tot_nocasco) and best_casco >= MIN_CONFIDENCE:
            break
        if EARLY_STOP and (tot_nocasco >= 2 and tot_nocasco > tot_casco) and best_nocasco >= MIN_CONFIDENCE:
            break

    if (tot_casco == 0 and tot_nocasco == 0):
        return (best_casco >= best_nocasco), max(best_casco, best_nocasco)

    if tot_casco != tot_nocasco:
        return (tot_casco > tot_nocasco), (best_casco if tot_casco > tot_nocasco else best_nocasco)

    return (best_casco >= best_nocasco), max(best_casco, best_nocasco)

# =========================
# BUCLE PRINCIPAL
# =========================

print("Wiegand-26 listo. Esperando tarjetas...")
print("Modelo cargado:", model)

last_card_ts = 0
ANTIREBOTE_MS = 800

while True:
    if bit_count > 0:
        if _ticks_diff(_ticks_ms(), last_wiegand_ms) > TIMEOUT_MS:
            bc = bit_count
            raw = card_value
            _reset_wiegand()

            if bc == 26 and _paridad_ok(raw):
                now = _ticks_ms()
                if _ticks_diff(now, last_card_ts) < ANTIREBOTE_MS:
                    continue
                last_card_ts = now

                site_code, user_code = _extract_fields(raw)

                tarjeta_valida = False
                nombre = None

                if raw in AUTHORIZED_RAW26:
                    tarjeta_valida = True
                    nombre = AUTHORIZED_RAW26[raw]
                elif (site_code, user_code) in AUTHORIZED_TUPLES:
                    tarjeta_valida = True
                    nombre = AUTHORIZED_TUPLES[(site_code, user_code)]

                print("Tarjeta detectada -> Bits=26, RAW26={}, Site={}, User={}".format(raw, site_code, user_code))

                if not tarjeta_valida:
                    print("ACCESO DENEGADO: tarjeta no autorizada.\n")
                    continue

                print("Tarjeta autorizada ({}). Iniciando detección...".format(nombre))
                is_helmet, score = decide_helmet(MAX_FRAMES=MAX_FRAMES_CHECK, EARLY_STOP=EARLY_STOP_ON_HIT)

                if is_helmet:
                    print("ACCESO PERMITIDO (casco). Score máx: {:.2f}\n".format(score))
                else:
                    print("ACCESO DENEGADO (nocasco). Score máx: {:.2f}\n".format(score))

            else:
                print("Trama W26 inválida: bits={} raw={:b}".format(bc, raw))

    time.sleep_ms(2)

