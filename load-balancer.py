from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
import random
import time
import threading

# Backend-Server und ihre Gewichtung
BACKENDS = [
    ("http://192.168.178.28", 70),
    ("http://192.168.178.137", 30),
]
available_backends = dict(BACKENDS)
lock = threading.Lock()

# 🔧 Modus: 'proxy' oder 'redirect'
MODE = 'proxy'  # oder 'redirect'

def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

def choose_backend(path):
    with lock:
        sec = time.localtime().tm_sec
        print(f"[DEBUG] Request Path: {path}, Current Second: {sec}")
        # Primzahl-Sekunden → zwinge Server 1
        if is_prime(sec):
            print("[DEBUG] Prime second! Forcing Server 1.")
            return BACKENDS[0][0]

        # Gewichte bauen
        weights = []
        for b, w in available_backends.items():
            weights += [b] * w

        # Gerade Pfadlänge → Bonus für Server 2
        if len(path) % 2 == 0 and BACKENDS[1][0] in available_backends:
            weights += [BACKENDS[1][0]] * 20
            print("[DEBUG] Even-length path! Boosting Server 2.")

        selected = random.choice(weights) if weights else None
        print(f"[DEBUG] Selected Backend: {selected}")
        return selected

def health_check():
    while True:
        with lock:
            for b, w in BACKENDS:
                try:
                    requests.get(b, timeout=2)
                    if b not in available_backends:
                        available_backends[b] = w
                        print(f"[HEALTH] Backend {b} recovered!")
                except:
                    if b in available_backends:
                        available_backends.pop(b)
                        print(f"[HEALTH] Backend {b} down!")
        time.sleep(30)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        backend = choose_backend(self.path)
        if not backend:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Service Unavailable: No backends online")
            return

        target = f"{backend}{self.path}"
        print(f"[DEBUG] Mode: {MODE}, Target: {target}")

        if MODE == 'redirect':
            # 🔥 Redirect-Modus
            self.send_response(302)
            self.send_header('Location', target)
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            # 🚀 Proxy-Modus
            self.proxy_request(target)

    def proxy_request(self, url):
        try:
            # Holen ohne Streaming, damit wir Content-Length korrekt setzen können
            resp = requests.get(url, headers=self.headers, stream=False)
            data = resp.content

            # Statuscode senden
            self.send_response(resp.status_code)

            # Alle Header außer hop-by-hop, Content-Length und Content-Encoding
            for k, v in resp.headers.items():
                lk = k.lower()
                if lk in (
                    'connection',
                    'keep-alive',
                    'proxy-authenticate',
                    'proxy-authorization',
                    'upgrade',
                    'content-length',
                    'content-encoding'
                ):
                    continue
                self.send_header(k, v)

            # Einmalige, korrekte Content-Length
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()

            # Body schreiben
            self.wfile.write(data)

        except requests.RequestException:
            with lock:
                available_backends.pop(backend, None)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"Bad Gateway: Backend not reachable")

if __name__ == '__main__':
    # Health-Check im Hintergrund starten
    threading.Thread(target=health_check, daemon=True).start()

    server_address = ('', 8080)
    print(f"🚀 Chaotic LoadBalancer läuft auf Port 8080 im {MODE.upper()}-Modus...")
    HTTPServer(server_address, Handler).serve_forever()

