# storage_local.py  — Mini-DB robusta en SD (OpenMV/MicroPython)
import os, time, ujson, uhashlib

# ====== CONFIG GLOBAL (ajústala si quieres) ======
SITE_ID_NAME = "SALA_MAQUINAS_A"
FW_VERSION   = 7
TZ_NAME      = "Europe/Madrid"

ROTACION_MENSUAL   = True     # events_YYYYMM.csv
SAVE_PROOF_IMAGE   = True     # guardar siempre foto
SAVE_ONLY_NO_CASCO = False    # ignorado si arriba es True

# En tu Portenta, la SD es la raíz "/"
BASE_SD   = "/"
MEDIA_DIR = BASE_SD + "media"
CONFIG_DIR= BASE_SD + "config"
DATA_DIR  = BASE_SD + "data"
CARDS_CSV = CONFIG_DIR + "/cards.csv"

# ====== UTILIDADES ======
def _ensure_dirs():
    # Crea carpetas si no existen, sin asumir /sd
    for p in (CONFIG_DIR, DATA_DIR, MEDIA_DIR):
        try: os.mkdir(p)
        except OSError: pass

def _now_iso():
    try:
        y,m,d,hh,mm,ss,_,_ = time.localtime()
        return "%02d-%02d-%04dT%02d:%02d:%02d"%(d,m,y,hh,mm,ss)
    except:
        return "1970-01-01T00:00:00"

def _current_month_tag():
    y,m,_,_,_,_,_,_ = time.localtime()
    return "%04d%02d"%(y,m)

def _events_csv_path(month_tag=None):
    if not month_tag:
        month_tag = _current_month_tag() if ROTACION_MENSUAL else ""
    return ("%s/events_%s.csv"%(DATA_DIR, month_tag)) if month_tag else (DATA_DIR + "/events.csv")

def _events_manifest_path(month_tag=None):
    if not month_tag:
        month_tag = _current_month_tag() if ROTACION_MENSUAL else ""
    return ("%s/events_%s.manifest.json"%(DATA_DIR, month_tag)) if month_tag else (DATA_DIR + "/events.manifest.json")

def _csv_write_header_if_needed(path, header_line):
    try:
        s = os.stat(path)
        if s[6] > 0:
            return
    except OSError:
        pass
    with open(path, "w") as f:
        f.write(header_line + "\n")
        f.flush()
    _sync_sd()

def _sha256_file(path):
    # Soporta puertos sin .hexdigest()
    try:
        import ubinascii
    except:
        ubinascii = None
    h = uhashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(1024)
                if not b: break
                h.update(b)
        try:
            return h.hexdigest()     # si existe
        except:
            d = h.digest()           # fallback universal
            if ubinascii:
                return ubinascii.hexlify(d).decode()
            _hex = "0123456789abcdef"
            return "".join(_hex[(x>>4)&0xF] + _hex[x&0xF] for x in d)
    except:
        return ""

def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        ujson.dump(obj, f)
        f.flush()
    _sync_sd()
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(tmp, path)

def _atomic_rewrite_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
    _sync_sd()
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(tmp, path)

def _sync_sd():
    try:
        import uos
        uos.sync()
    except:
        pass

# ====== ACL (tarjetas) por (site_code, user_code) ======
_AUTH_BY_TUPLE = {}  # {(site,user): nombre}

def _to_int_or_none(s):
    s = (s or "").strip()
    if not s: return None
    try: return int(s)
    except: return None

def load_cards():
    global _AUTH_BY_TUPLE
    try: os.stat(CARDS_CSV)
    except OSError:
        # Plantilla inicial
        header = "site_code,user_code,nombre,enabled\n148,19828,Operario_A,1\n"
        _atomic_rewrite_text(CARDS_CSV, header)
    # (Re)carga
    local = {}
    with open(CARDS_CSV, "r") as f:
        first = True
        for line in f:
            line = line.strip()
            if not line: continue
            if first: first=False; continue
            parts = [p.strip() for p in line.split(",")]
            sc = _to_int_or_none(parts[0] if len(parts)>0 else "")
            uc = _to_int_or_none(parts[1] if len(parts)>1 else "")
            nm = parts[2] if len(parts)>2 else ""
            en = (parts[3].strip() != "0") if len(parts)>3 else True
            if en and sc is not None and uc is not None:
                local[(sc,uc)] = nm or "Operario"
    _AUTH_BY_TUPLE = local
    return len(_AUTH_BY_TUPLE)

def add_card_tuple(site_code, user_code, nombre, enabled=True):
    global _AUTH_BY_TUPLE
    _AUTH_BY_TUPLE[(site_code,user_code)] = nombre
    with open(CARDS_CSV, "a") as f:
        f.write("{},{},{},{}\n".format(site_code, user_code, nombre, 1 if enabled else 0))
        f.flush()
    _sync_sd()

def is_card_authorized(site_code, user_code):
    nm = _AUTH_BY_TUPLE.get((site_code,user_code), "")
    return (nm != ""), nm

# ====== EVENTOS ======
_HEADER = "timestamp,tz,checkpoint,version,raw26,site_code,user_code,nombre,autorizado,casco,score,img_path"

def _ensure_events_file():
    path = _events_csv_path()
    _csv_write_header_if_needed(path, _HEADER)
    return path

def append_event(raw26, site_code, user_code, nombre, autorizado, casco, score, img_path=""):
    """
    Escribe 1 línea y fuerza a disco (append + flush + sync).
    """
    path = _ensure_events_file()
    ts = _now_iso()
    line = "{ts},{tz},{chk},{ver},{raw},{sc},{uc},{nm},{auth},{cas},{scr:.2f},{img}\n".format(
        ts=ts, tz=TZ_NAME, chk=SITE_ID_NAME, ver=FW_VERSION,
        raw=(raw26 if raw26 is not None else ""),
        sc=(site_code if site_code is not None else ""),
        uc=(user_code if user_code is not None else ""),
        nm=(nombre or ""),
        auth=(1 if autorizado else 0),
        cas=(1 if casco else 0),
        scr=(score if score is not None else 0.0),
        img=(img_path or "")
    )
    with open(path, "a") as f:
        f.write(line)
        f.flush()
    _sync_sd()

def save_proof_image_if_needed(img, raw26, casco, force=False):
    if not SAVE_PROOF_IMAGE:
        return ""
    if SAVE_ONLY_NO_CASCO and casco and not force:
        return ""
    ts = _now_iso().replace(":", "").replace("-", "")
    fname = "{}/{}_{}.jpg".format(MEDIA_DIR, ts, raw26 if raw26 is not None else "no_raw")
    try:
        img.save(fname, quality=85)
        _sync_sd()
        return fname
    except Exception as e:
        print("No se pudo guardar imagen:", e)
        return ""

# ====== MANIFEST & AUDITORÍA ======
def update_manifest():
    """
    Recalcula SHA-256 y número de eventos (sin cabecera) del CSV del mes actual.
    Guarda manifest JSON de forma atómica.
    """
    mt = _current_month_tag() if ROTACION_MENSUAL else ""
    csv_path = _events_csv_path(mt)
    manifest_path = _events_manifest_path(mt)

    # Asegura volcado a SD antes de leer
    _sync_sd()

    # Cuenta líneas (excluye cabecera)
    count = 0
    try:
        with open(csv_path, "r") as f:
            first = True
            for _ in f:
                if first: first=False
                else: count += 1
    except OSError:
        pass

    sha = _sha256_file(csv_path)
    manifest = {
        "month": mt,
        "csv": csv_path,
        "count": count,
        "sha256": sha,
        "checkpoint": SITE_ID_NAME,
        "version": FW_VERSION,
        "tz": TZ_NAME,
        "updated_at": _now_iso()
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest

# ====== CONSULTAS ======
def query_events(month_tag=None, user_code=None, casco=None, site_code=None):
    """
    month_tag: 'YYYYMM' (o None = mes actual si ROTACION_MENSUAL)
    Filtra por user_code (int), casco (bool) y/o site_code (int).
    Devuelve lista de dicts.
    """
    path = _events_csv_path(month_tag if month_tag else None)
    res = []
    try:
        with open(path, "r") as f:
            header = f.readline().strip().split(",")
            for line in f:
                row = line.strip().split(",")
                if len(row) != len(header):  # línea corrupta/partida
                    continue
                rec = dict(zip(header, row))
                if user_code is not None:
                    if (rec["user_code"] == "") or (int(rec["user_code"]) != user_code):
                        continue
                if site_code is not None:
                    if (rec["site_code"] == "") or (int(rec["site_code"]) != site_code):
                        continue
                if casco is not None:
                    if int(rec["casco"] or 0) != (1 if casco else 0):
                        continue
                res.append(rec)
    except OSError:
        pass
    return res

# ====== INIT ======
def init_storage():
    _ensure_dirs()
    n = load_cards()          # carga ACL en RAM
    _ensure_events_file()     # asegura cabecera presente
    return n
