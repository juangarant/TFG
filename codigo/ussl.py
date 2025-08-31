# /lib/ussl.py
import ssl as _ssl

# Reexporta todo lo de ssl
for _name in dir(_ssl):
    globals()[_name] = getattr(_ssl, _name)

def wrap_socket(sock, server_hostname=None, **kwargs):
    try:
        return _ssl.wrap_socket(sock, server_hostname=server_hostname, **kwargs)
    except TypeError:
        return _ssl.wrap_socket(sock, **kwargs)
