# urequests.py — MicroPython-friendly requests con HTTPS (SNI) sin dependencia rígida de ussl
# Fuente: adaptado del urequests oficial con compatibilidad SNI y fallback ssl.

import usocket as socket
import sys

# ssl / ussl compat
try:
    import ussl as ssl
except ImportError:
    import ssl

def _wrap_tls(s, host, **kwargs):
    # Algunos ports aceptan server_hostname (SNI); otros no.
    try:
        return ssl.wrap_socket(s, server_hostname=host, **kwargs)
    except TypeError:
        return ssl.wrap_socket(s, **kwargs)

def request(method, url, data=None, json=None, headers={}, stream=None, timeout=None):
    try:
        proto, _, host, path = url.split("/", 3)
    except ValueError:
        proto, _, host = url.split("/", 2)
        path = ""
    if proto == "http:":
        port = 80
    elif proto == "https:":
        port = 443
    else:
        raise ValueError("Unsupported protocol: " + proto)

    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    ai = ai[0]

    s = socket.socket(ai[0], ai[1], ai[2])
    try:
        if timeout is not None:
            try:
                s.settimeout(timeout)
            except:
                pass

        s.connect(ai[-1])
        if proto == "https:":
            s = _wrap_tls(s, host)

        # Build request
        req = "%s /%s HTTP/1.1\r\nHost: %s\r\n" % (method, path, host)
        # Default headers
        if "User-Agent" not in headers:
            req += "User-Agent: urequests/0.9\r\n"
        if "Connection" not in headers:
            req += "Connection: close\r\n"

        # JSON body convenience
        if json is not None:
            import ujson
            data = ujson.dumps(json)
            headers["Content-Type"] = "application/json"

        if data is not None and "Content-Length" not in headers:
            try:
                content_length = len(data)
            except TypeError:
                # data podría ser un generador/stream: no calculamos
                content_length = None
            if content_length is not None:
                headers["Content-Length"] = str(content_length)

        # Headers
        for k, v in headers.items():
            req += "{}: {}\r\n".format(k, v)
        req += "\r\n"

        # Send headers
        s.write(req.encode() if isinstance(req, str) else req)

        # Send body (bytes/str)
        if data:
            if isinstance(data, str):
                data = data.encode()
            # Si es bytes o bytearray
            s.write(data)

        # Parse response
        # Lee status line
        l = _readline(s)
        try:
            protover, status, reason = l.split(None, 2)
        except ValueError:
            try:
                protover, status = l.split(None, 1)
                reason = b""
            except ValueError:
                protover = b"HTTP/1.1"
                status = b"0"
                reason = b""
        status = int(status)

        # Headers
        resp_headers = {}
        while True:
            l = _readline(s)
            if not l or l == b"\r\n":
                break
            k, v = l.split(b":", 1)
            resp_headers[k.strip().lower()] = v.strip()

        # Content
        if stream:
            raw = s
        else:
            content = _readall(s)
            s.close()
            raw = None

        return Response(status, reason, resp_headers, raw, content)

    except Exception as e:
        try:
            s.close()
        except:
            pass
        raise e

def _readline(s):
    l = b""
    while True:
        c = s.read(1)
        if not c:
            break
        l += c
        if c == b"\n":
            break
    return l

def _readall(s):
    b = b""
    while True:
        data = s.read(1024)
        if not data:
            break
        b += data
    return b

def head(url, **kw):    return request("HEAD", url, **kw)
def get(url, **kw):     return request("GET", url, **kw)
def post(url, **kw):    return request("POST", url, **kw)
def put(url, **kw):     return request("PUT", url, **kw)
def patch(url, **kw):   return request("PATCH", url, **kw)
def delete(url, **kw):  return request("DELETE", url, **kw)

class Response:
    def __init__(self, status_code, reason, headers, raw, content):
        self.status_code = status_code
        self.reason = reason
        self.headers = headers
        self.raw = raw
        self._content = content

    def close(self):
        if self.raw:
            try:
                self.raw.close()
            except:
                pass
        self.raw = None

    @property
    def content(self):
        if self.raw:
            self._content = _readall(self.raw)
            self.close()
        return self._content

    @property
    def text(self):
        try:
            return self.content.decode()
        except:
            return str(self.content)

    def json(self):
        import ujson
        return ujson.loads(self.content)
