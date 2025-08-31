# cloud_sync.py — Portenta/OpenMV: subida mensual de CSV + manifest a Supabase
# Robusto frente a respuestas chunked o texto plano (intenta dechunk + parse JSON).

import os, ujson, utime
try:
    import urequests as requests
except:
    raise OSError("HTTPS no disponible: falta urequests/TLS en el firmware")

CONFIG_PATH = "/config/server.json"

def _exists(p):
    try:
        os.stat(p); return True
    except:
        return False

def _load_cfg():
    with open(CONFIG_PATH, "r") as f:
        return ujson.loads(f.read())

def _multipart(fields, files):
    boundary = "----PPE%u" % utime.ticks_ms()
    CRLF = "\r\n"
    body = b""

    for k, v in fields.items():
        body += ("--%s%s" % (boundary, CRLF)).encode()
        body += ('Content-Disposition: form-data; name="%s"%s%s' % (k, CRLF, CRLF)).encode()
        body += (str(v)).encode() + CRLF.encode()

    for name, (filename, content, ctype) in files.items():
        body += ("--%s%s" % (boundary, CRLF)).encode()
        body += ('Content-Disposition: form-data; name="%s"; filename="%s"%s' % (name, filename, CRLF)).encode()
        body += ('Content-Type: %s%s%s' % (ctype, CRLF, CRLF)).encode()
        body += content + CRLF.encode()

    body += ("--%s--%s" % (boundary, CRLF)).encode()
    return body, "multipart/form-data; boundary=%s" % boundary

def _dechunk(s):
    # Convierte Transfer-Encoding: chunked en cuerpo plano (si detecta formato chunked).
    # s: str
    try:
        i = 0
        out = []
        ln = len(s)
        while True:
            j = s.find("\r\n", i)
            if j == -1:
                return s  # no parece chunked
            size_hex = s[i:j]
            # Si el tamaño no es hex, no es chunked
            try:
                size = int(size_hex, 16)
            except:
                return s
            i = j + 2
            if size == 0:
                # fin de chunks; puede haber cabeceras/trailers tras el 0 CRLF
                return "".join(out)
            end = i + size
            if end > ln:
                return s  # cuerpo inconsistente; devolvemos original
            out.append(s[i:end])
            i = end + 2  # saltar CRLF del chunk
    except:
        return s

def _parse_json_response(r):
    status = getattr(r, "status_code", None)
    # 1) Intenta JSON directo
    try:
        return r.json()
    except:
        pass
    # 2) Lee texto y dechunk
    try:
        txt = r.text
    except:
        txt = None
    try:
        if txt:
            dj = _dechunk(txt)
            return ujson.loads(dj)
    except:
        pass
    # 3) Fallback: devuelve info mínima para depurar en el caller
    return {"ok": False, "status": status, "text": (txt[:256] if txt else None)}

def upload_month(yyyymm):
    """
    Sube /data/events_<yyyymm>.csv y /data/events_<yyyymm>.manifest.json
    al endpoint Edge Function. Devuelve el JSON de respuesta del servidor
    (dict) o {ok: False, ...} si hay fallo local/red.
    """
    cfg = _load_cfg()
    url = cfg["function_url"].rstrip("/")
    edge_key = cfg["edge_api_key"]

    csv_path = "/data/events_%s.csv" % yyyymm
    man_path = "/data/events_%s.manifest.json" % yyyymm

    if not (_exists(csv_path) and _exists(man_path)):
        return {"ok": False, "error": "faltan_ficheros", "csv": _exists(csv_path), "manifest": _exists(man_path)}

    # Cargar bytes
    with open(csv_path, "rb") as f:
        csv_bytes = f.read()
    with open(man_path, "rb") as f:
        man_bytes = f.read()

    fields = {"yyyymm": yyyymm}
    files = {
        "csv": ("events_%s.csv" % yyyymm, csv_bytes, "text/csv"),
        "manifest": ("events_%s.manifest.json" % yyyymm, man_bytes, "application/json"),
    }
    body, content_type = _multipart(fields, files)
    headers = {
        "Content-Type": content_type,
        "x-edge-key": edge_key,
        # Si activas Verify JWT en la función: añade Authorization con anon key.
        # "Authorization": "Bearer <TU_ANON_KEY>"
    }

    try:
        r = requests.post(url, data=body, headers=headers)
        try:
            resp = _parse_json_response(r)
        finally:
            try:
                r.close()
            except:
                pass
        return resp
    except Exception as e:
        return {"ok": False, "error": "http_err:%s" % e}
