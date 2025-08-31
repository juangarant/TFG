# wifi_setup.py — Wi-Fi + NTP con ajuste automático Europe/Madrid (CET/CEST)
import uos, time, ubinascii, uhashlib
try:
    import ucryptolib
    HAVE_AES = True
except:
    HAVE_AES = False

WIFI_CFG = "/config/wifi.json"

def _device_key_16():
    import pyb
    uid = pyb.unique_id()
    return uhashlib.sha256(uid).digest()[:16]  # 16 bytes

def _pad_pkcs7(b):
    n = 16 - (len(b) % 16)
    return b + bytes([n]) * n

def _unpad_pkcs7(b):
    n = b[-1]
    if n < 1 or n > 16: raise ValueError("PKCS7 inválido")
    return b[:-n]

def _rand_bytes(n=16):
    try:
        return uos.urandom(n)
    except:
        r = bytearray(n); t = time.ticks_ms() & 0xFFFFFFFF
        for i in range(n):
            t = (1103515245 * (t + i) + 12345) & 0xFFFFFFFF
            r[i] = t & 0xFF
        return bytes(r)

def _b64e(b): return ubinascii.b2a_base64(b).strip().decode()
def _b64d(s): return ubinascii.a2b_base64(s.strip())

def save_wifi_config(ssid, password, encrypt=True):
    """Crea /config/wifi.json. Ejecuta UNA vez desde el REPL."""
    try: uos.mkdir("/config")
    except OSError: pass
    obj = {"ssid": ssid}
    if encrypt and HAVE_AES:
        iv = _rand_bytes(16)
        aes = ucryptolib.aes(_device_key_16(), 2, iv)  # CBC
        ct = aes.encrypt(_pad_pkcs7(password.encode()))
        obj.update({"enc": True, "iv": _b64e(iv), "pwd": _b64e(ct)})
    else:
        obj.update({"enc": False, "pwd": password})
    import ujson
    with open(WIFI_CFG, "w") as f:
        ujson.dump(obj, f); f.flush()
        try: uos.sync()
        except: pass
    print("Wi-Fi guardado en", WIFI_CFG, "(enc={})".format(obj.get("enc", False)))

def load_wifi_config():
    """Devuelve (ssid, password) descifrada si procede."""
    import ujson
    with open(WIFI_CFG, "r") as f:
        cfg = ujson.loads(f.read())
    ssid = cfg.get("ssid", "")
    if cfg.get("enc", False) and HAVE_AES:
        iv  = _b64d(cfg["iv"])
        ct  = _b64d(cfg["pwd"])
        aes = ucryptolib.aes(_device_key_16(), 2, iv)
        pwd = _unpad_pkcs7(aes.decrypt(ct)).decode()
    else:
        pwd = cfg.get("pwd", "")
    return ssid, pwd

# ---------- Cálculo DST Europa/Madrid ----------
def _is_leap(y):
    return (y%4==0 and y%100!=0) or (y%400==0)

def _weekday(y,m,d):
    # Sakamoto: 0=Dom,1=Lun,...,6=Sab
    t=[0,3,2,5,0,3,5,1,4,6,2,4]
    if m<3: y-=1
    w=(y + y//4 - y//100 + y//400 + t[m-1] + d)%7
    return w  # 0=Dom

def _days_in_month(y,m):
    if m==2: return 29 if _is_leap(y) else 28
    return 30 if m in (4,6,9,11) else 31

def _last_sunday(y, m):
    d = _days_in_month(y, m)
    while _weekday(y, m, d) != 0:  # 0 = Domingo
        d -= 1
    return d

def _europe_madrid_offset_minutes(y,m,d,h):
    # DST desde último domingo de marzo 01:00 UTC hasta último domingo de octubre 01:00 UTC
    mar_sun = _last_sunday(y, 3)
    oct_sun = _last_sunday(y,10)
    after_start = (m>3) or (m==3 and (d>mar_sun or (d==mar_sun and h>=1)))
    before_end  = (m<10) or (m==10 and (d<oct_sun or (d==oct_sun and h<1)))
    if after_start and before_end:
        return 120  # CEST = UTC+2
    return 60       # CET  = UTC+1

def wifi_connect_and_ntp_local():
    """Conecta a Wi-Fi, sincroniza NTP (UTC) y ajusta RTC a hora local Europe/Madrid (CET/CEST)."""
    import network, ntptime, pyb
    ssid, pwd = load_wifi_config()
    sta = network.WLAN(network.STA_IF); sta.active(True)
    if not sta.isconnected():
        sta.connect(ssid, pwd)
        t0 = time.ticks_ms()
        while not sta.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise Exception("Timeout Wi-Fi")
            time.sleep_ms(200)
    print("Wi-Fi OK:", sta.ifconfig())

    # 1) Pone RTC en UTC
    ntptime.host = "pool.ntp.org"; ntptime.settime()
    y,m,d,hh,mm,ss,_,_ = time.localtime()  # ahora refleja UTC
    # 2) Calcula offset automático (CET/CEST)
    off_min = _europe_madrid_offset_minutes(y,m,d,hh)
    # 3) Convierte a hora local y escribe RTC local
    try:
        t_utc = time.mktime((y,m,d,hh,mm,ss,0,0))
    except:
        # Fallback simple si faltara mktime
        t_utc = (hh*3600 + mm*60 + ss)
    t_local = t_utc + off_min*60
    y,m,d,hh,mm,ss,_,_ = time.localtime(t_local)
    # weekday 1=Lun..7=Dom
    wd = _weekday(y,m,d); wd = 7 if wd==0 else wd
    pyb.RTC().datetime((y,m,d,wd,hh,mm,ss,0))
    print("RTC ajustado a hora local Europe/Madrid (offset {} min)".format(off_min))

# Compat: la antigua función en UTC por si la usas en algún sitio
def wifi_connect_and_ntp():
    import network, ntptime
    ssid, pwd = load_wifi_config()
    sta = network.WLAN(network.STA_IF); sta.active(True)
    if not sta.isconnected():
        sta.connect(ssid, pwd)
        t0 = time.ticks_ms()
        while not sta.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise Exception("Timeout Wi-Fi")
            time.sleep_ms(200)
    print("Wi-Fi OK:", sta.ifconfig())
    ntptime.host = "pool.ntp.org"; ntptime.settime()
    print("NTP OK (UTC)")
