"""
fetch() compartido por los scrapers que pegan contra pccomponentes.com.

Cloudflare empezó a devolver 403 (challenge "Just a moment...") a las
peticiones hechas con urllib "a pelo" — confirmado que no es cosa de headers
(mismo resultado probando varios User-Agent) ni de un endpoint concreto
(pasa igual en /portatiles, /tablets y /categorias/mini-pcs, y tanto desde
un entorno cloud como desde una IP residencial normal): lo que está
detectando es el fingerprint TLS/HTTP2 del cliente, que en urllib no se
parece en nada al de un navegador real.

curl_cffi imita ese fingerprint (impersonate="chrome124") y con eso basta
para pasar el filtro sin necesitar un navegador headless completo. Es una
dependencia opcional a propósito: si no está instalada, cae de vuelta a
urllib normal (por si Cloudflare deja de bloquear, o para quien no quiera
instalar nada). Instalación si hace falta: `pip install curl_cffi`.
"""
import time
import urllib.request

try:
    from curl_cffi import requests as _curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}


def fetch(url, retries=4, headers=None):
    headers = headers or HEADERS
    last_err = None
    for attempt in range(retries):
        try:
            if HAS_CURL_CFFI:
                resp = _curl_requests.get(url, headers=headers, impersonate="chrome124", timeout=20)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code} (curl_cffi)")
                return resp.text
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 3)
    raise last_err
