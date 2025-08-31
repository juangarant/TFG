# cards_sync.py — sincroniza /config/cards.csv desde tu Edge Function
# server.json esperado en /config:
# {
#   "cards_url": "https://<project>.functions.supabase.co/cards-manifest",
#   "edge_api_key": "<EDGE_API_KEY>"
# }

import urequests as requests
import ujson as json
import uhashlib
import ubinascii
import uos

CFG_PATH   = "/config/server.json"
CARDS_PATH = "/config/cards.csv"
CARDS_TMP  = "/config/cards.csv.tmp"
STATE_PATH = "/config/cards_state.json"

# ---------- utilidades ----------

def _cfg():
    with open(CFG_PATH) as f:
        return json.loads(f.read())

def _sha256_hex_file(path):
    """Devuelve el SHA-256 en hex de un archivo (compatible MicroPython)."""
    h = uhashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4096)
            if not b:
                break
            h.update(b)
    # En MicroPython no hay hexdigest(); usamos hexlify sobre digest()
    return ubinascii.hexlify(h.digest()).decode()

def _load_state():
    try:
        return json.loads(open(STATE_PATH).read())
    except:
        return {}

def _save_state(s):
    try:
        open(STATE_PATH, "w").write(json.dumps(s))
    except Exception as e:
        print("[ACL] No se pudo guardar STATE:", e)

def _dechunk(s):
    # Intenta decodificar Transfer-Encoding: chunked si aparece
    try:
        i = 0
        out = []
        ln = len(s)
        while True:
            j = s.find("\r\n", i)
            if j == -1:
                return s   # no parece chunked
            size_hex = s[i:j]
            try:
                size = int(size_hex, 16)
            except:
                return s   # no es chunked válido
            i = j + 2
            if size == 0:
                return "".join(out)
            end = i + size
            if end > ln:
                return s   # incompleto
            out.append(s[i:end])
            i = end + 2   # saltar \r\n tras el chunk
    except:
        return s

# ---------- red ----------

def fetch_manifest():
    """Obtiene manifest {version, sha256, url, size?, updated_at?}."""
    cfg = _cfg()
    url = cfg.get("cards_url")
    key = cfg.get("edge_api_key")
    if not url or not key:
        raise Exception("Faltan 'cards_url' o 'edge_api_key' en /config/server.json")

    r = requests.get(url, headers={"x-edge-key": key})
    try:
        if r.status_code != 200:
            raise Exception("manifest status=%d" % r.status_code)
        try:
            return json.loads(r.text)
        except:
            txt = r.text
            dj = _dechunk(txt) if txt else txt
            return json.loads(dj)
    finally:
        r.close()

def _download_csv_to_tmp(csv_url):
    """Descarga CSV en CARDS_TMP (modo binario)."""
    r = requests.get(csv_url)
    try:
        if r.status_code != 200:
            raise Exception("csv status=%d" % r.status_code)
        try:
            data = r.content
        except:
            data = (r.text or "").encode()
        with open(CARDS_TMP, "wb") as f:
            f.write(data)
    finally:
        r.close()

# ---------- lógica principal ----------

def ensure_cards_updated(db_reload_fn=None, verbose=True):
    """
    Verifica manifest y actualiza /config/cards.csv si cambian 'version' o 'sha256'.
    Llama a db_reload_fn() tras actualizar para recargar ACL en memoria.
    """
    st = _load_state()
    cur_version = st.get("version")
    cur_sha     = st.get("sha256")

    mf = fetch_manifest()
    # Nombres según tu Edge Function
    new_version = str(mf.get("version", ""))  # epoch (segundos) -> string
    new_sha     = str(mf.get("sha256", ""))
    csv_url     = mf.get("url")
    # opcionales:
    mf_size     = mf.get("size")
    mf_updated  = mf.get("updated_at")

    if not csv_url:
        raise Exception("manifest sin 'url'")

    # ¿Existe local?
    local_exists = False
    try:
        uos.stat(CARDS_PATH)
        local_exists = True
    except:
        local_exists = False

    # Si existe y tenemos sha del manifest, comprobamos integridad
    need_update = True
    if local_exists and new_sha:
        try:
            local_sha = _sha256_hex_file(CARDS_PATH)
            if local_sha == new_sha:
                # sha coincide: si versión también, nada que hacer
                if new_version and cur_version == new_version:
                    if verbose:
                        print("[ACL] CSV al día (versión y sha coinciden)")
                    return {"updated": False, "version": new_version}
                # sha igual pero versión distinta (p.ej. renuevo metadata): solo guardo estado
                if verbose:
                    print("[ACL] sha coincide. Actualizo estado a version=%s" % new_version)
                _save_state({"version": new_version, "sha256": new_sha})
                return {"updated": False, "version": new_version}
            else:
                if verbose:
                    print("[ACL] sha local != manifest → actualizar")
        except Exception as e:
            if verbose:
                print("[ACL] No se pudo calcular sha local:", e)
        need_update = True
    else:
        need_update = True

    if verbose:
        if mf_size is not None:
            print("[ACL] Descargando (%s bytes aprox)..." % str(mf_size))
        else:
            print("[ACL] Descargando CSV de tarjetas...")

    # Descarga a TMP
    _download_csv_to_tmp(csv_url)

    # Validación sha
    if new_sha:
        tmp_sha = _sha256_hex_file(CARDS_TMP)
        if tmp_sha != new_sha:
            try:
                uos.remove(CARDS_TMP)
            except:
                pass
            raise Exception("sha256 mismatch: tmp=%s manifest=%s" % (tmp_sha, new_sha))

    # Mover tmp -> definitivo
    try:
        try:
            uos.remove(CARDS_PATH)
        except:
            pass
        uos.rename(CARDS_TMP, CARDS_PATH)
    except Exception as e:
        try:
            uos.remove(CARDS_TMP)
        except:
            pass
        raise e

    # Guardar nuevo estado
    _save_state({"version": new_version, "sha256": new_sha})

    # Recargar ACL en memoria si procede
    if db_reload_fn:
        try:
            db_reload_fn()
        except Exception as e:
            print("ACL reload error:", e)

    if verbose:
        if mf_updated:
            print("[ACL] Actualizado a version=%s (updated_at=%s)" % (new_version, mf_updated))
        else:
            print("[ACL] Actualizado a version=%s" % new_version)

    return {"updated": True, "version": new_version}
