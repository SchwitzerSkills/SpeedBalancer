#!/usr/bin/env python3
import threading
import time
import random
import sqlite3
import socket
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from flask import Flask, jsonify, render_template_string, request

# --- SQLite Setup ---
DB = 'loadbalancer.db'
conn = sqlite3.connect(DB, check_same_thread=False)
c = conn.cursor()
# Create tables: backends with name, url, weight; metrics with url, count
c.execute('''CREATE TABLE IF NOT EXISTS backends(
    name TEXT,
    url TEXT PRIMARY KEY,
    weight INTEGER
)''')
c.execute('''CREATE TABLE IF NOT EXISTS metrics(
    url TEXT PRIMARY KEY,
    count INTEGER
)''')
# Default entries (only inserted if DB empty)
defaults = [
    ('webserver1', 'http://192.168.178.28', 70),
    ('webserver2', 'http://192.168.178.137', 30)
]
for name, url, w in defaults:
    c.execute('INSERT OR IGNORE INTO backends(name, url, weight) VALUES(?, ?, ?)', (name, url, w))
    c.execute('INSERT OR IGNORE INTO metrics(url, count) VALUES(?, 0)', (url,))
conn.commit()
lock = threading.Lock()
MODE = 'proxy'  # or 'redirect'

# --- Health Check ---
def health_check():
    while True:
        with lock:
            c.execute('SELECT url FROM backends')
            for (u,) in c.fetchall():
                try:
                    requests.get(u, timeout=2)
                except:
                    c.execute('DELETE FROM backends WHERE url=?', (u,))
            conn.commit()
        time.sleep(30)

# --- Flask Dashboard ---
app = Flask(__name__)

dashboard_tpl = '''
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SpeedBalancer Dashboard - {{ hostname }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>body{background:#f8f9fa;} .card{margin-bottom:1rem;} label{font-weight:bold;}</style>
</head>
<body>
<div class="container py-4">
  <h1>Dashboard: {{ hostname }}</h1>
  <div class="row">
    <div class="col-md-6">
      <div class="card">
        <div class="card-header">Anfragen pro Server</div>
        <div class="card-body"><canvas id="chartReq"></canvas></div>
      </div>
    </div>
    <div class="col-md-6">
      <div class="card">
        <div class="card-header">Backend-Gewichte</div>
        <div class="card-body"><canvas id="chartWgt"></canvas></div>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-header">Backends konfigurieren</div>
    <div class="card-body">
      <form id="formSettings">
        <div class="row" id="cfg"></div>
        <button class="btn btn-primary mt-2">Speichern Änderungen</button>
      </form>
      <hr>
      <h5>Neues Backend hinzufügen</h5>
      <form id="formAdd" class="row g-3">
        <div class="col-md-4"><input id="addName" class="form-control" placeholder="Name"></div>
        <div class="col-md-4"><input id="addUrl" class="form-control" placeholder="URL"></div>
        <div class="col-md-2"><input id="addWeight" type="number" min="1" class="form-control" placeholder="Gewicht"></div>
        <div class="col-md-2"><button class="btn btn-success">Hinzufügen</button></div>
      </form>
    </div>
  </div>
</div>
<script>
const COLORS = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40'];
let reqChart, wgtChart, backends;

async function getJSON(path) {
  const res = await fetch(path);
  return await res.json();
}

function makeChart(id, type) {
  const ctx = document.getElementById(id).getContext('2d');
  return new Chart(ctx, {
    type: type,
    data: { labels: [], datasets: [{ label: id, data: [], backgroundColor: [] }] },
    options: { responsive: true }
  });
}

async function loadMetrics() {
  const metrics = await getJSON('/metrics');
  backends = await getJSON('/backends');
  const labels = backends.map(b => b.name);
  const data = backends.map(b => metrics[b.url] || 0);
  reqChart.data.labels = labels;
  reqChart.data.datasets[0].data = data;
  reqChart.data.datasets[0].backgroundColor = labels.map((_, i) => COLORS[i % COLORS.length]);
  reqChart.update();
}

async function loadSettings() {
  backends = await getJSON('/backends');
  const labels = backends.map(b => b.name);
  const weights = backends.map(b => b.weight);
  wgtChart.data.labels = labels;
  wgtChart.data.datasets[0].data = weights;
  wgtChart.data.datasets[0].backgroundColor = labels.map((_, i) => COLORS[i % COLORS.length]);
  wgtChart.update();

  const cfg = document.getElementById('cfg');
  cfg.innerHTML = '';
  backends.forEach((b, i) => {
    const div = document.createElement('div');
    div.className = 'col-md-4 mb-3';
    div.innerHTML = `
      <input type="hidden" name="oldUrl-${i}" value="${b.url}">
      <label>Name</label>
      <input class="form-control mb-1" name="name-${i}" value="${b.name}">
      <label>URL</label>
      <input class="form-control mb-1" name="url-${i}" value="${b.url}">
      <label>Weight</label>
      <input class="form-control" type="number" min="1" name="weight-${i}" value="${b.weight}">
      <button class="btn btn-danger btn-sm mt-1" onclick="removeBackend('${b.url}'); return false;">Entfernen</button>
    `;
    cfg.appendChild(div);
  });
}

async function removeBackend(url) {
  await fetch('/backends', {
    method: 'DELETE', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url})
  });
  await loadMetrics();
  await loadSettings();
}

window.onload = () => {
  reqChart = makeChart('chartReq', 'bar');
  wgtChart = makeChart('chartWgt', 'pie');
  loadMetrics(); loadSettings();
  setInterval(loadMetrics, 2000);

  document.getElementById('formSettings').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    for (let i = 0; i < backends.length; i++) {
      const oldUrl = fd.get(`oldUrl-${i}`);
      const name = fd.get(`name-${i}`);
      const url = fd.get(`url-${i}`);
      const weight = +fd.get(`weight-${i}`);
      await fetch('/backends', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({oldUrl, name, url, weight})
      });
    }
    await loadMetrics(); loadSettings();
  };

  document.getElementById('formAdd').onsubmit = async e => {
    e.preventDefault();
    const name = document.getElementById('addName').value;
    const url = document.getElementById('addUrl').value;
    const weight = +document.getElementById('addWeight').value;
    if (name && url && weight > 0) {
      await fetch('/backends', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, url, weight})
      });
      document.getElementById('addName').value = '';
      document.getElementById('addUrl').value = '';
      document.getElementById('addWeight').value = '';
      await loadMetrics(); loadSettings();
    }
  };
};
</script>
</body>
</html>
'''

@app.route('/')
def dash():
    return render_template_string(dashboard_tpl, hostname=socket.gethostname())

@app.route('/backends', methods=['GET','POST','DELETE'])
def backends_api():
    if request.method == 'GET':
        with lock:
            c.execute('SELECT name,url,weight FROM backends')
            rows = c.fetchall()
        return jsonify([{'name':n, 'url':u, 'weight':w} for n,u,w in rows])
    if request.method == 'POST':
        data = request.json
        old = data.get('oldUrl')
        with lock:
            # handle rename if URL changed
            if old and old != data['url']:
                c.execute('DELETE FROM backends WHERE url=?', (old,))
                c.execute('DELETE FROM metrics WHERE url=?', (old,))
            c.execute('INSERT OR REPLACE INTO backends(name,url,weight) VALUES(?,?,?)', (data['name'], data['url'], data['weight']))
            c.execute('INSERT OR IGNORE INTO metrics(url,count) VALUES(?,0)', (data['url'],))
            conn.commit()
        return '', 204
    if request.method == 'DELETE':
        data = request.json
        with lock:
            c.execute('DELETE FROM backends WHERE url=?', (data['url'],))
            c.execute('DELETE FROM metrics WHERE url=?', (data['url'],))
            conn.commit()
        return '', 204

@app.route('/metrics')
def metrics_api():
    with lock:
        c.execute('SELECT url,count FROM metrics')
        rows = c.fetchall()
    return jsonify(dict(rows))

# --- LoadBalancer ---
def is_prime(n):
    if n < 2: return False
    for i in range(2, int(n**0.5)+1):
        if n % i == 0: return False
    return True

class LBHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with lock:
            c.execute('SELECT url,weight FROM backends')
            items = c.fetchall()
            sec = time.localtime().tm_sec
            if items and is_prime(sec):
                backend = items[0][0]
            else:
                arr = []
                for u,w in items:
                    arr += [u]*w
                if len(self.path) % 2 == 0 and len(items) > 1:
                    arr += [items[1][0]]*20
                backend = random.choice(arr) if arr else None
            if backend:
                c.execute('UPDATE metrics SET count=count+1 WHERE url=?', (backend,))
                conn.commit()
        if not backend:
            self.send_response(503)
            self.end_headers()
            return
        target = f"{backend}{self.path}"
        try:
            r = requests.get(target, headers=self.headers)
            body = r.content
            self.send_response(r.status_code)
            for k,v in r.headers.items():
                if k.lower() in ('connection','content-length','content-encoding'): continue
                self.send_header(k,v)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except:
            with lock:
                c.execute('DELETE FROM backends WHERE url=?', (backend,))
                conn.commit()
            self.send_response(502)
            self.end_headers()

if __name__ == '__main__':
    threading.Thread(target=health_check, daemon=True).start()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    print('Balancer->8080, Dash->0.0.0.0:5000')
    HTTPServer(('', 8080), LBHandler).serve_forever()
