#!/usr/bin/env python3
"""
SmartCity – server
==================
Kører kun med Pythons standardbibliotek (ingen pip install).

    python3 server.py            -> http://localhost:8000

Den gør tre ting:
  1. Kører en bysimulering (vejr, energi, trafik, skrald, vand ...) i en baggrundstråd.
  2. Gemmer alle målinger, kommandoer og hændelser i en SQLite-database (smartcity.db).
  3. Sender live-opdateringer til alle browsere med Server-Sent Events (/api/stream).

Micro:bits kobles til browseren med WebSerial. Browseren sender deres data hertil
(POST /api/data), og serveren sender kommandoer tilbage via strømmen (event "cmd"),
som browseren skriver ud til micro:bit'en.
"""
import hmac
import json
import math
import os
import queue
import random
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DB_PATH = os.environ.get("SMARTCITY_DB", os.path.join(ROOT, "smartcity.db"))
PORT = int(os.environ.get("PORT", "8000"))
# Hvis ADMIN_KEY er sat (fx på Render), kræver alt der ÆNDRER byen en adgangskode.
# Alle kan stadig se byen. Lokalt uden ADMIN_KEY er alt åbent.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

TICK_SECONDS = 1.0        # hvor tit simuleringen regner (rigtige sekunder)
LIVE_HOLD = 20.0          # så længe en micro:bit-værdi "vinder" over simuleringen
GATEWAY_TIMEOUT = 90.0    # gateway regnes for offline efter dette (Chrome kan bremse baggrundsfaner)
WEB_HOLD = 60.0           # så længe en værdi sat fra hjemmesiden holdes
SNAPSHOT_EVERY = 30.0     # gem simulerede værdier i databasen hvert 30. sekund
RETENTION_DAYS = 7        # slet målinger ældre end dette

# --------------------------------------------------------------------------------------
# Modeller. Navnet en micro:bit bruger er "KODE.nøgle" (max 8 tegn, fx "GL.lys").
#   sensor   = data der kommer FRA en micro:bit (eller simuleres hvis ingen er koblet på)
#   aktuator = data byen sender TIL micro:bit'en (lys, pumpe, alarm ...)
# step = hvor meget en aktuator skal ændre sig før der sendes en ny radiobesked.
# --------------------------------------------------------------------------------------
MODELS = [
    {"code": "BY", "name": "Vejrstation", "icon": "🌦️", "x": 930, "y": 300,
     "desc": "Byens fælles vejr og energiregnskab. En micro:bit kan være vejrstation og sende temperatur.",
     "keys": [
         {"key": "tid", "label": "Klokken", "unit": "", "kind": "aktuator", "min": 0, "max": 24, "step": 1},
         {"key": "sol", "label": "Sollys", "unit": "%", "kind": "sensor", "min": 0, "max": 100},
         {"key": "regn", "label": "Regn", "unit": "mm/t", "kind": "sensor", "min": 0, "max": 20},
         {"key": "vind", "label": "Vind", "unit": "m/s", "kind": "sensor", "min": 0, "max": 30},
         {"key": "temp", "label": "Temperatur", "unit": "°C", "kind": "sensor", "min": -10, "max": 35},
         {"key": "prod", "label": "Produktion", "unit": "kW", "kind": "aktuator", "min": 0, "max": 150, "step": 10},
         {"key": "forbrug", "label": "Forbrug", "unit": "kW", "kind": "aktuator", "min": 0, "max": 150, "step": 10},
     ]},
    {"code": "SB", "name": "Skraldebil", "icon": "🚛", "x": 420, "y": 500,
     "desc": "Kører ud når en skraldespand er over 80 %, og tømmer kun spande der er over 40 %.",
     "keys": [
         {"key": "aktiv", "label": "Kører", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
         {"key": "pos", "label": "Rute", "unit": "%", "kind": "aktuator", "min": 0, "max": 100, "step": 10},
     ]},
    {"code": "DH", "name": "Drivhus", "icon": "🪴", "x": 70, "y": 95,
     "desc": "Måler jordfugtighed. Pumpen starter under 35 % og stopper over 70 %. Bruger vand fra regnvandstanken.",
     "keys": [
         {"key": "fugt", "label": "Jordfugt", "unit": "%", "kind": "sensor", "min": 0, "max": 100},
         {"key": "temp", "label": "Temperatur", "unit": "°C", "kind": "sensor", "min": -10, "max": 45},
         {"key": "pumpe", "label": "Pumpe", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
     ]},
    {"code": "VM", "name": "Vindmølle", "icon": "🌬️", "x": 880, "y": 515,
     "desc": "Producerer strøm efter vindhastigheden. Stopper ved storm over 25 m/s og starter igen under 20 m/s.",
     "keys": [
         {"key": "vind", "label": "Vind", "unit": "m/s", "kind": "sensor", "min": 0, "max": 30},
         {"key": "kW", "label": "Effekt", "unit": "kW", "kind": "aktuator", "min": 0, "max": 60, "step": 5},
         {"key": "stop", "label": "Stormstop", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
     ]},
    {"code": "GL", "name": "Gadelys", "icon": "💡", "x": 450, "y": 265,
     "desc": "Tænder når det er mørkt. Dæmpes til 30 % når ingen går forbi, 100 % ved bevægelse.",
     "keys": [
         {"key": "lys", "label": "Lysniveau", "unit": "", "kind": "sensor", "min": 0, "max": 255},
         {"key": "bev", "label": "Bevægelse", "unit": "", "kind": "sensor", "min": 0, "max": 1},
         {"key": "lampe", "label": "Lampe", "unit": "%", "kind": "aktuator", "min": 0, "max": 100, "step": 10},
     ]},
    {"code": "LS", "name": "Ladestander", "icon": "🔌", "x": 820, "y": 105,
     "desc": "Smart opladning: lader hurtigt når der er overskud af strøm eller om natten.",
     "keys": [
         {"key": "bil", "label": "Bil tilsluttet", "unit": "", "kind": "sensor", "min": 0, "max": 1},
         {"key": "lad", "label": "Batteri", "unit": "%", "kind": "aktuator", "min": 0, "max": 100, "step": 5},
         {"key": "kW", "label": "Ladeeffekt", "unit": "kW", "kind": "aktuator", "min": 0, "max": 11, "step": 1},
     ]},
    {"code": "TL", "name": "Trafiklys", "icon": "🚦", "x": 262, "y": 120,
     "desc": "Adaptivt trafiklys: grøn-tiden bliver længere jo flere biler der holder i kø.",
     "keys": [
         {"key": "biler", "label": "Biler i kø", "unit": "", "kind": "sensor", "min": 0, "max": 20},
         {"key": "fase", "label": "Fase (0 rød, 1 gul, 2 grøn)", "unit": "", "kind": "aktuator", "min": 0, "max": 2, "step": 1},
     ]},
    {"code": "VP", "name": "Varmepumpe", "icon": "🏠", "x": 520, "y": 520,
     "desc": "Holder huset mellem 20,5 og 22,5 °C. Huset taber varme hurtigere når det er koldt ude.",
     "keys": [
         {"key": "inde", "label": "Indetemperatur", "unit": "°C", "kind": "sensor", "min": 10, "max": 30},
         {"key": "on", "label": "Tændt", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
     ]},
    {"code": "OV", "name": "Oversvømmelse", "icon": "🌊", "x": 140, "y": 515,
     "desc": "Måler vandstand i kloakken. Alarm ved 40 cm. Stiger hvis regnvandstanken løber over.",
     "keys": [
         {"key": "vand", "label": "Vandstand", "unit": "cm", "kind": "sensor", "min": 0, "max": 100},
         {"key": "alarm", "label": "Alarm", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
     ]},
    {"code": "RV", "name": "Regnvandstank", "icon": "🛢️", "x": 175, "y": 95,
     "desc": "Samler regnvand til drivhuset. Når den er fuld, løber vandet over i kloakken.",
     "keys": [
         {"key": "tank", "label": "Fyldning", "unit": "%", "kind": "sensor", "min": 0, "max": 100},
         {"key": "fuld", "label": "Fuld", "unit": "", "kind": "aktuator", "min": 0, "max": 1, "step": 1},
     ]},
    {"code": "S1", "name": "Skraldespand 1", "icon": "🗑️", "x": 500, "y": 110,
     "desc": "Måler hvor fuld spanden er. Får besked (S1.tom) når skraldebilen har tømt den.",
     "keys": [
         {"key": "fyld", "label": "Fyldning", "unit": "%", "kind": "sensor", "min": 0, "max": 100},
         {"key": "tom", "label": "Tømt antal gange", "unit": "", "kind": "aktuator", "min": 0, "max": 999, "step": 1},
     ]},
    {"code": "S2", "name": "Skraldespand 2", "icon": "🗑️", "x": 745, "y": 310,
     "desc": "Måler hvor fuld spanden er. Får besked (S2.tom) når skraldebilen har tømt den.",
     "keys": [
         {"key": "fyld", "label": "Fyldning", "unit": "%", "kind": "sensor", "min": 0, "max": 100},
         {"key": "tom", "label": "Tømt antal gange", "unit": "", "kind": "aktuator", "min": 0, "max": 999, "step": 1},
     ]},
]

KEYDEF = {f"{m['code']}.{k['key']}": dict(k, model=m["code"]) for m in MODELS for k in m["keys"]}

DEFAULTS = {
    "BY.tid": 7, "BY.sol": 20, "BY.regn": 0, "BY.vind": 6, "BY.temp": 9, "BY.prod": 0, "BY.forbrug": 30,
    "SB.aktiv": 0, "SB.pos": 0,
    "DH.fugt": 55, "DH.temp": 16, "DH.pumpe": 0,
    "VM.vind": 6, "VM.kW": 0, "VM.stop": 0,
    "GL.lys": 50, "GL.bev": 0, "GL.lampe": 30,
    "LS.bil": 0, "LS.lad": 0, "LS.kW": 0,
    "TL.biler": 2, "TL.fase": 0,
    "VP.inde": 21, "VP.on": 0,
    "OV.vand": 5, "OV.alarm": 0,
    "RV.tank": 40, "RV.fuld": 0,
    "S1.fyld": 30, "S1.tom": 0,
    "S2.fyld": 55, "S2.tom": 0,
}

WEATHER = {  # regn mm/t, vind m/s, skydække (1 = klar himmel)
    "sol":   {"regn": 0.0,  "vind": 4.0,  "sky": 1.0,  "label": "Sol"},
    "skyet": {"regn": 0.0,  "vind": 7.0,  "sky": 0.55, "label": "Skyet"},
    "regn":  {"regn": 4.0,  "vind": 9.0,  "sky": 0.3,  "label": "Regn"},
    "storm": {"regn": 13.0, "vind": 21.0, "sky": 0.15, "label": "Storm"},
}
WEATHER_NEXT = {  # sandsynlighed for skift i "auto"
    "sol": ["sol", "sol", "skyet"],
    "skyet": ["sol", "skyet", "regn"],
    "regn": ["skyet", "regn", "regn", "storm"],
    "storm": ["regn", "regn", "storm"],
}


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# --------------------------------------------------------------------------------------
# Database: én skrive-tråd der samler skrivninger i batches (hurtigt og trådsikkert)
# --------------------------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS models(
    code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS sensors(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_code TEXT NOT NULL REFERENCES models(code),
    key TEXT NOT NULL, label TEXT, unit TEXT, kind TEXT,
    UNIQUE(model_code, key));
CREATE TABLE IF NOT EXISTS devices(
    serial TEXT PRIMARY KEY, model_code TEXT REFERENCES models(code),
    first_seen REAL, last_seen REAL, packets INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS readings(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    sensor_id INTEGER NOT NULL REFERENCES sensors(id),
    value REAL NOT NULL, source TEXT NOT NULL, device TEXT REFERENCES devices(serial));
CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts ON readings(sensor_id, ts);
CREATE TABLE IF NOT EXISTS commands(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    sensor_id INTEGER NOT NULL REFERENCES sensors(id), value REAL NOT NULL, source TEXT);
CREATE TABLE IF NOT EXISTS state(
    sensor_id INTEGER PRIMARY KEY REFERENCES sensors(id), value REAL, updated REAL);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


class Database:
    def __init__(self, path):
        self.path = path
        self.q = queue.Queue()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = self.connect()
        conn.executescript(SCHEMA)
        for m in MODELS:
            conn.execute("INSERT INTO models(code,name,description) VALUES(?,?,?) "
                         "ON CONFLICT(code) DO UPDATE SET name=excluded.name, description=excluded.description",
                         (m["code"], m["name"], m["desc"]))
            for k in m["keys"]:
                conn.execute("INSERT INTO sensors(model_code,key,label,unit,kind) VALUES(?,?,?,?,?) "
                             "ON CONFLICT(model_code,key) DO UPDATE SET label=excluded.label, unit=excluded.unit, kind=excluded.kind",
                             (m["code"], k["key"], k["label"], k["unit"], k["kind"]))
        conn.commit()
        self.sensor_id = {f"{mc}.{k}": i for i, mc, k in conn.execute("SELECT id, model_code, key FROM sensors")}
        conn.close()
        threading.Thread(target=self._writer, daemon=True, name="db-writer").start()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def write(self, sql, params=()):
        self.q.put((sql, params))

    def _writer(self):
        conn = self.connect()
        last_prune = 0
        while True:
            batch = [self.q.get()]
            deadline = time.time() + 0.5
            while time.time() < deadline:
                try:
                    batch.append(self.q.get(timeout=max(0, deadline - time.time())))
                except queue.Empty:
                    break
            try:
                with conn:
                    for sql, params in batch:
                        conn.execute(sql, params)
            except sqlite3.Error as e:
                print("DB-fejl:", e)
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                cutoff = time.time() - RETENTION_DAYS * 86400
                with conn:
                    conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
                    conn.execute("DELETE FROM commands WHERE ts < ?", (cutoff,))
                    conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))

    # Læsninger: egen forbindelse pr. kald (WAL gør at læsning ikke blokerer skrivning)
    def query(self, sql, params=()):
        conn = self.connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


# --------------------------------------------------------------------------------------
# Hub: sender events til alle tilsluttede browsere
# --------------------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, event, data):
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait((event, payload))
                except queue.Full:
                    dead.append(q)  # klienten hænger -> smid den ud
            for q in dead:
                self.clients.discard(q)


# --------------------------------------------------------------------------------------
# Byen
# --------------------------------------------------------------------------------------
class City:
    def __init__(self, db, hub):
        self.db, self.hub = db, hub
        self.lock = threading.RLock()
        self.values = dict(DEFAULTS)
        self.source = {}       # navn -> "microbit" | "web"   (kun mens hold er aktivt)
        self.hold_until = {}   # navn -> tidspunkt hvor simuleringen må tage over igen
        self.last_sent = {}    # navn -> (værdi, tid) for sidste kommando
        self.devices = {}      # serienummer -> info
        self.recent = {}       # (serial, navn) -> (værdi, tid)  dublet-filter
        self.events = deque(maxlen=60)
        self.minute = 7 * 60.0
        self.day = 1
        self.speed = 1.0       # byminutter pr. rigtigt sekund
        self.paused = False
        self.weather_mode = "auto"
        self.regime = "sol"
        self.tl_timer = 0.0
        self.tl_green_len = 6.0
        self.flags = set()
        self.gateway_seen = 0.0   # sidste livstegn fra en gateway-fane i klassen
        self.last_snapshot_db = 0.0
        self.last_state_db = 0.0
        self._restore()

    # ---------- persistens ----------
    def _restore(self):
        rows = self.db.query("SELECT s.model_code || '.' || s.key, st.value FROM state st JOIN sensors s ON s.id = st.sensor_id")
        for name, value in rows:
            if name in self.values and value is not None:
                self.values[name] = value
        settings = dict(self.db.query("SELECT key, value FROM settings"))
        self.minute = float(settings.get("minute", self.minute))
        self.day = int(settings.get("day", self.day))
        self.speed = float(settings.get("speed", self.speed))
        self.weather_mode = settings.get("weather_mode", self.weather_mode)
        self.regime = settings.get("regime", self.regime)
        for row in self.db.query("SELECT ts, level, text FROM events ORDER BY id DESC LIMIT 60"):
            self.events.appendleft({"ts": row[0], "level": row[1], "text": row[2]})

    def _persist_state(self, now):
        for name, value in self.values.items():
            self.db.write("INSERT INTO state(sensor_id,value,updated) VALUES(?,?,?) "
                          "ON CONFLICT(sensor_id) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                          (self.db.sensor_id[name], value, now))
        for k, v in (("minute", self.minute), ("day", self.day), ("speed", self.speed),
                     ("weather_mode", self.weather_mode), ("regime", self.regime)):
            self.db.write("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (k, str(v)))

    # ---------- hjælpere ----------
    def v(self, name):
        return self.values[name]

    def held(self, name, now=None):
        return self.hold_until.get(name, 0) > (now or time.time())

    def sim(self, name, value):
        """Simuleringen sætter en værdi – men kun hvis ingen micro:bit/bruger har overtaget den."""
        if not self.held(name):
            d = KEYDEF[name]
            self.values[name] = clamp(value, d["min"], d["max"])

    def log(self, text, level="info"):
        ev = {"ts": time.time(), "level": level, "text": text}
        self.events.append(ev)
        self.db.write("INSERT INTO events(ts,level,text) VALUES(?,?,?)", (ev["ts"], level, text))
        self.hub.publish("log", ev)

    def flag(self, key, on, text_on, text_off=None, level="warn"):
        """Logger kun når en tilstand skifter (undgår at spamme loggen)."""
        if on and key not in self.flags:
            self.flags.add(key)
            self.log(text_on, level)
        elif not on and key in self.flags:
            self.flags.discard(key)
            if text_off:
                self.log(text_off, "info")

    # ---------- data udefra ----------
    def receive(self, name, value, source="microbit", serial=None):
        """Data fra en micro:bit (via browseren) eller fra hjemmesiden."""
        if name not in KEYDEF:
            return False, f"Ukendt navn '{name}'. Brug fx GL.lys"
        try:
            value = float(value)
        except (TypeError, ValueError):
            return False, "Værdien skal være et tal"
        if math.isnan(value) or math.isinf(value):
            return False, "Værdien skal være et tal"
        now = time.time()
        d = KEYDEF[name]
        value = clamp(value, d["min"], d["max"])
        with self.lock:
            if serial is not None:
                serial = str(serial)
                key = (serial, name)
                prev = self.recent.get(key)
                if prev and prev[0] == value and now - prev[1] < 0.4:
                    return True, "dublet"  # samme pakke fra to gateways
                self.recent[key] = (value, now)
                dev = self.devices.get(serial)
                if dev is None:
                    dev = self.devices[serial] = {"serial": serial, "model": d["model"], "first": now, "packets": 0}
                    self.log(f"Ny micro:bit ({serial}) sender som {d['model']} – {MODEL_BY_CODE[d['model']]['name']}")
                dev.update(last=now, model=d["model"], last_name=name, last_value=value)
                dev["packets"] += 1
                self.db.write("INSERT INTO devices(serial,model_code,first_seen,last_seen,packets) VALUES(?,?,?,?,1) "
                              "ON CONFLICT(serial) DO UPDATE SET model_code=excluded.model_code, "
                              "last_seen=excluded.last_seen, packets=packets+1",
                              (serial, d["model"], now, now))
            was_live = self.source.get(name) == "microbit" and self.held(name, now)
            self.values[name] = value
            self.source[name] = source
            self.hold_until[name] = now + (LIVE_HOLD if source == "microbit" else WEB_HOLD)
            if source == "microbit":
                # micro:bit'en kender allerede værdien -> send den ikke tilbage
                self.last_sent[name] = (value, now)
            if source == "microbit" and not was_live:
                self.log(f"{name} kører nu LIVE fra micro:bit")
            self.db.write("INSERT INTO readings(ts,sensor_id,value,source,device) VALUES(?,?,?,?,?)",
                          (now, self.db.sensor_id[name], value, source, serial))
            if source == "web" and d["kind"] == "aktuator":
                self._send(name, value, now, force=True, source="web")
        return True, "ok"

    def gateway_ping(self, connected=True):
        with self.lock:
            was = self.gateway_online()
            self.gateway_seen = time.time() if connected else 0.0
            if connected and not was:
                self.log("Gateway i klassen er online")
            elif not connected and was:
                self.log("Gateway i klassen er afbrudt", "warn")

    def gateway_online(self, now=None):
        return (now or time.time()) - self.gateway_seen < GATEWAY_TIMEOUT

    def release(self, name):
        with self.lock:
            self.hold_until.pop(name, None)
            self.source.pop(name, None)

    def control(self, data):
        with self.lock:
            if "speed" in data:
                self.speed = clamp(float(data["speed"]), 0.1, 60)
            if "paused" in data:
                self.paused = bool(data["paused"])
            if data.get("weather") in ("auto", *WEATHER):
                self.weather_mode = data["weather"]
                if self.weather_mode != "auto":
                    self.regime = self.weather_mode
                self.log(f"Vejret sat til: {WEATHER.get(self.weather_mode, {'label': 'Automatisk'})['label']}")
            if "hour" in data:
                self.minute = clamp(float(data["hour"]), 0, 23.99) * 60
                self.log(f"Klokken sat til {int(self.minute // 60):02d}:00")
            if data.get("reset"):
                self.values = dict(DEFAULTS)
                self.hold_until.clear()
                self.source.clear()
                self.flags.clear()
                self.minute, self.day = 7 * 60.0, 1
                self.log("Byen er nulstillet")
            if data.get("resync"):
                self.last_sent.clear()  # ny gateway -> send alle aktuatorer igen

    # ---------- kommandoer til micro:bits ----------
    def _send(self, name, value, now, force=False, source="sim"):
        d = KEYDEF[name]
        out = round(value)
        prev = self.last_sent.get(name)
        if not force and prev is not None:
            if abs(out - prev[0]) < d.get("step", 1) or now - prev[1] < 1.0:
                return
        self.last_sent[name] = (out, now)
        self.hub.publish("cmd", {"name": name, "value": out})
        self.db.write("INSERT INTO commands(ts,sensor_id,value,source) VALUES(?,?,?,?)",
                      (now, self.db.sensor_id[name], out, source))

    # ---------- selve simuleringen ----------
    def tick(self, dt):
        now = time.time()
        with self.lock:
            m = 0.0 if self.paused else self.speed * dt   # byminutter der er gået
            if m > 0:
                # Store tidsskridt (høj hastighed) deles op i små, så simuleringen bliver stabil
                n = max(1, math.ceil(m / 2.0))
                for _ in range(n):
                    self._step(m / n, dt / n)
            for name, d in KEYDEF.items():
                if d["kind"] == "aktuator" and self.source.get(name) != "microbit":
                    self._send(name, self.values[name], now)
            if self.gateway_seen and not self.gateway_online(now):
                self.gateway_seen = 0.0
                self.log("Gateway i klassen svarer ikke længere", "warn")
            for name in [n for n, t in self.hold_until.items() if t <= now]:
                if self.source.get(name) == "microbit":
                    self.log(f"{name}: ingen data fra micro:bit – simuleringen tager over", "info")
                self.hold_until.pop(name)
                self.source.pop(name, None)
            if now - self.last_snapshot_db > SNAPSHOT_EVERY:
                self.last_snapshot_db = now
                for name, value in self.values.items():
                    if self.source.get(name) is None:  # micro:bit/web gemmes allerede ved modtagelse
                        self.db.write("INSERT INTO readings(ts,sensor_id,value,source,device) VALUES(?,?,?,?,NULL)",
                                      (now, self.db.sensor_id[name], value, "sim"))
            if now - self.last_state_db > 10:
                self.last_state_db = now
                self._persist_state(now)
            snap = self.snapshot(now)
        self.hub.publish("state", snap)

    def _step(self, m, dt):
        V, S = self.v, self.sim
        self.minute += m
        if self.minute >= 1440:
            self.minute -= 1440
            self.day += 1
            self.log(f"Dag {self.day} begynder")
        h = self.minute / 60.0

        # --- Vejr ---
        if self.weather_mode == "auto":
            if random.random() < m / 90.0:  # ca. ét skift hver 1,5 bytime
                new = random.choice(WEATHER_NEXT[self.regime])
                if new != self.regime:
                    self.regime = new
                    self.log(f"Vejret skifter: {WEATHER[new]['label']}")
        else:
            self.regime = self.weather_mode
        w = WEATHER[self.regime]
        k = min(1.0, m / 15.0)  # hvor hurtigt vejret glider mod målet
        daylight = max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0.0
        S("BY.tid", math.floor(h))
        S("BY.sol", V("BY.sol") + (daylight * 100 * w["sky"] - V("BY.sol")) * k)
        S("BY.regn", max(0, V("BY.regn") + (w["regn"] - V("BY.regn")) * k + random.gauss(0, 0.3) * (w["regn"] > 0)))
        S("BY.vind", max(0, V("BY.vind") + (w["vind"] - V("BY.vind")) * k + random.gauss(0, 0.6)))
        target_temp = 9 + 6 * math.sin(math.pi * (h - 9) / 12) - (3 if w["regn"] > 0 else 0)
        S("BY.temp", V("BY.temp") + (target_temp - V("BY.temp")) * k * 0.5)
        sol, regn, temp = V("BY.sol"), V("BY.regn"), V("BY.temp")
        activity = 0.15 if h < 6 or h >= 23 else 1.0 if 7 <= h < 9 or 15 <= h < 18 else 0.6

        # --- Vindmølle ---
        S("VM.vind", V("BY.vind") + random.gauss(0, 0.3))
        vind = V("VM.vind")
        S("VM.stop", 1 if vind > 25 else 0 if vind < 20 else V("VM.stop"))  # hysterese
        S("VM.kW", 0 if (vind < 3 or V("VM.stop")) else 60 * min(1.0, ((vind - 3) / 9) ** 3))
        self.flag("storm", V("VM.stop") == 1, "Storm! Vindmøllen er stoppet for at beskytte sig selv", "Vindmøllen kører igen")

        # --- Gadelys ---
        S("GL.lys", sol * 2.55 + random.gauss(0, 3))
        if V("GL.bev") >= 1 and random.random() < m * 0.5:
            S("GL.bev", 0)
        elif V("GL.bev") < 1 and random.random() < m * 0.12 * activity:
            S("GL.bev", 1)
        dark = V("GL.lys") < 50
        S("GL.lampe", (100 if V("GL.bev") >= 1 else 30) if dark else 0)

        # --- Trafiklys (kører i rigtige sekunder, så det er til at følge med i) ---
        rush = 1.0 if 7 <= h < 9 or 15 <= h < 17 else 0.35 if 6 <= h < 22 else 0.08
        arrivals = 0.9 * rush * dt
        cars = V("TL.biler") + (1 if random.random() < arrivals else 0)
        self.tl_timer += dt
        fase = V("TL.fase")
        if fase == 2:
            cars -= 1.2 * dt if random.random() < 0.9 else 0
            if self.tl_timer >= self.tl_green_len:
                fase, self.tl_timer = 1, 0.0
        elif fase == 1 and self.tl_timer >= 1.5:
            fase, self.tl_timer = 0, 0.0
        elif fase == 0 and self.tl_timer >= 5:
            fase, self.tl_timer = 2, 0.0
            self.tl_green_len = clamp(4 + 0.7 * V("TL.biler"), 4, 15)  # adaptiv grøn-tid
        S("TL.biler", max(0, round(cars, 1)))
        S("TL.fase", fase)

        # --- Regnvand, drivhus og oversvømmelse hænger sammen ---
        tank = V("RV.tank") + regn * m * 0.05
        overflow = max(0.0, tank - 100)
        pumping = V("DH.pumpe") >= 1
        if pumping:
            tank -= m * 0.25
        S("RV.tank", tank)
        S("RV.fuld", 1 if V("RV.tank") >= 98 else 0)
        self.flag("tank_tom", pumping and V("RV.tank") <= 1,
                  "Regnvandstanken er tom – drivhuset bruger drikkevand", "Regnvandstanken har vand igen")

        S("DH.fugt", V("DH.fugt") - m * 0.05 * (1 + sol / 60) + (m * 0.9 if pumping else 0))
        fugt = V("DH.fugt")
        S("DH.pumpe", 1 if fugt < 35 else 0 if fugt > 70 else V("DH.pumpe"))
        S("DH.temp", temp + 6 * sol / 100 + 4)

        S("OV.vand", V("OV.vand") + m * (regn * 0.25 - 2.0) + overflow * 0.5)  # overflow er allerede ganget med tiden
        S("OV.alarm", 1 if V("OV.vand") >= 40 else 0 if V("OV.vand") < 30 else V("OV.alarm"))
        self.flag("flood", V("OV.alarm") == 1, "OVERSVØMMELSESALARM – vandstanden er over 40 cm", "Vandstanden er faldet igen", "alarm")

        # --- Varmepumpe ---
        inde = V("VP.inde") - (V("VP.inde") - temp) * 0.006 * m + (0.25 * m if V("VP.on") >= 1 else 0)
        S("VP.inde", inde)
        S("VP.on", 1 if V("VP.inde") < 20.5 else 0 if V("VP.inde") > 22.5 else V("VP.on"))

        # --- Skraldespande og skraldebil ---
        S("S1.fyld", V("S1.fyld") + m * 0.12 * activity * random.uniform(0.5, 1.5))
        S("S2.fyld", V("S2.fyld") + m * 0.09 * activity * random.uniform(0.5, 1.5))
        if V("SB.aktiv") < 1 and (V("S1.fyld") >= 80 or V("S2.fyld") >= 80):
            S("SB.aktiv", 1)
            S("SB.pos", 0)
            self.log("Skraldebilen kører ud")
        if V("SB.aktiv") >= 1:
            before = V("SB.pos")
            S("SB.pos", before + m * 2.5)
            after = V("SB.pos")
            for stop, code in ((35, "S1"), (60, "S2")):
                if before < stop <= after:
                    if V(f"{code}.fyld") >= 40:
                        self.values[f"{code}.fyld"] = 0  # tømning vinder også over micro:bit
                        self.hold_until.pop(f"{code}.fyld", None)
                        self.values[f"{code}.tom"] += 1
                        self.log(f"Skraldebilen tømte {MODEL_BY_CODE[code]['name']}")
                    else:
                        self.log(f"Skraldebilen sprang {MODEL_BY_CODE[code]['name']} over (under 40 %)")
            if after >= 100:
                S("SB.aktiv", 0)
                S("SB.pos", 0)
                self.log("Skraldebilen er tilbage på depotet")

        # --- Energi og smart opladning ---
        solar = 30 * sol / 100
        base = 22 + 12 * activity
        forbrug_uden_bil = base + V("GL.lampe") * 0.08 + V("VP.on") * 6 + V("DH.pumpe") * 1.5
        overskud = V("VM.kW") + solar - forbrug_uden_bil
        if V("LS.bil") < 1 and 15 <= h < 21 and random.random() < m / 120:
            S("LS.bil", 1)
            S("LS.lad", random.uniform(15, 50))
            self.log("En elbil er sat til ladestanderen")
        elif V("LS.bil") >= 1 and (V("LS.lad") >= 100 or (7 <= h < 9 and random.random() < m / 60)):
            S("LS.bil", 0)
            S("LS.lad", 0)
            self.log("Elbilen kører fra ladestanderen")
        if V("LS.bil") >= 1 and V("LS.lad") < 100:
            S("LS.kW", 11 if (overskud > 11 or h < 6) else 3.7)
        else:
            S("LS.kW", 0)
        S("LS.lad", V("LS.lad") + V("LS.kW") * m / 60 / 60 * 100)  # 60 kWh batteri
        S("BY.prod", V("VM.kW") + solar)
        S("BY.forbrug", forbrug_uden_bil + V("LS.kW"))

    def snapshot(self, now=None):
        now = now or time.time()
        src = {n: (self.source[n] if self.held(n, now) and n in self.source else "sim") for n in self.values}
        return {
            "t": now,
            "clock": {"day": self.day, "minute": self.minute, "speed": self.speed, "paused": self.paused,
                      "weather": self.weather_mode, "regime": self.regime, "regimeLabel": WEATHER[self.regime]["label"]},
            "values": {n: round(v, 2) for n, v in self.values.items()},
            "source": src,
            "devices": [dict(d, age=round(now - d["last"], 1)) for d in self.devices.values()],
            "gateway": {"online": self.gateway_online(now),
                        "age": round(now - self.gateway_seen, 1) if self.gateway_seen else None},
        }


MODEL_BY_CODE = {m["code"]: m for m in MODELS}


def sim_loop(city):
    last = time.time()
    while True:
        time.sleep(TICK_SECONDS)
        now = time.time()
        try:
            city.tick(min(5.0, now - last))
        except Exception as e:  # simuleringen må aldrig dø
            print("Simuleringsfejl:", repr(e))
        last = now


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon", ".json": "application/json", ".ts": "text/plain; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartCity/1.0"

    def log_message(self, fmt, *args):
        pass  # hold konsollen ren

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("for stor forespørgsel")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        p = url.path
        if p == "/api/stream":
            return self.stream()
        if p == "/api/config":
            return self.send_json({"locked": bool(ADMIN_KEY)})
        if p == "/api/models":
            return self.send_json(MODELS)
        if p == "/api/state":
            with CITY.lock:
                return self.send_json(CITY.snapshot())
        if p == "/api/events":
            with CITY.lock:
                return self.send_json(list(CITY.events))
        if p == "/api/history":
            name = qs.get("name", [""])[0]
            minutes = float(qs.get("minutes", ["30"])[0])
            if name not in KEYDEF:
                return self.send_json({"error": "ukendt navn"}, 400)
            rows = DB.query("SELECT ts, value, source FROM readings WHERE sensor_id=? AND ts>=? ORDER BY ts LIMIT 5000",
                            (DB.sensor_id[name], time.time() - minutes * 60))
            return self.send_json(rows)
        if p == "/api/devices":
            rows = DB.query("SELECT serial, model_code, first_seen, last_seen, packets FROM devices ORDER BY last_seen DESC")
            return self.send_json([dict(zip(("serial", "model", "first", "last", "packets"), r)) for r in rows])
        return self.static(p)

    def authorized(self):
        if not ADMIN_KEY:
            return True
        return hmac.compare_digest(self.headers.get("X-Admin-Key", "").encode(), ADMIN_KEY.encode())

    def do_POST(self):
        p = urlparse(self.path).path
        if not self.authorized():
            return self.send_json({"error": "Forkert eller manglende adgangskode"}, 401)
        if p == "/api/auth":
            return self.send_json({"ok": True})
        try:
            data = self.read_json()
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json({"error": str(e)}, 400)
        if p == "/api/data":
            if isinstance(data, dict) and data.get("gateway"):
                CITY.gateway_ping(True)
            items = data.get("items") if isinstance(data, dict) and "items" in data else [data]
            results = []
            for it in items[:200]:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or f"{it.get('model', '')}.{it.get('key', '')}"
                source = it.get("source") if it.get("source") in ("microbit", "web") else "microbit"
                ok, msg = CITY.receive(name, it.get("value"), source, it.get("serial"))
                results.append({"name": name, "ok": ok, "msg": msg})
            return self.send_json({"results": results})
        if p == "/api/gateway":
            CITY.gateway_ping(bool(data.get("connected", True)))
            return self.send_json({"ok": True})
        if p == "/api/release":
            CITY.release(str(data.get("name", "")))
            return self.send_json({"ok": True})
        if p == "/api/sim":
            try:
                CITY.control(data)
            except (TypeError, ValueError) as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"ok": True})
        return self.send_json({"error": "ikke fundet"}, 404)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = HUB.subscribe()
        try:
            with CITY.lock:
                first = json.dumps(CITY.snapshot(), ensure_ascii=False)
            self.wfile.write(f"retry: 2000\nevent: state\ndata: {first}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    event, payload = q.get(timeout=15)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self.send_json({"error": "ikke fundet"}, 404)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


DB = Database(DB_PATH)
HUB = Hub()
CITY = City(DB, HUB)

if __name__ == "__main__":
    threading.Thread(target=sim_loop, args=(CITY,), daemon=True, name="simulation").start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"SmartCity kører på http://localhost:{PORT}  (database: {DB_PATH})"
          + ("  [adgangskode slået til]" if ADMIN_KEY else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopper ...")
