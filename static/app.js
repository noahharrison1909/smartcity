import { CityMap, summary } from "./city.js";
import { MicrobitLink } from "./serial.js";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 0) => Number(v).toLocaleString("da-DK", { maximumFractionDigits: d });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SRC = { microbit: ["live", "LIVE"], web: ["web", "WEB"], sim: ["sim", "SIM"] };

let models = [];
let keydefs = {};
let state = null;
let selected = localStorage.getItem("sc.selected") || "GL";
const history = {};          // navn -> [{t, v}]
const HISTORY_MAX = 900;

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ------------------------------------------------------------------ opstart
async function init() {
  models = await api("/api/models");
  for (const m of models) for (const k of m.keys) keydefs[`${m.code}.${k.key}`] = { ...k, model: m.code };
  if (!models.some((m) => m.code === selected)) selected = "GL";

  window.city = new CityMap($("city"), models, select);
  buildStrip();
  buildControls();
  (await api("/api/events")).forEach(addLog);
  connectStream();
  select(selected);
}

// ------------------------------------------------------------------ live-strøm
function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (e) => onState(JSON.parse(e.data)));
  es.addEventListener("log", (e) => addLog(JSON.parse(e.data)));
  es.addEventListener("cmd", (e) => {
    const { name, value } = JSON.parse(e.data);
    if (link.connected) link.send(name, value);   // videre til micro:bit'en
  });
  es.onerror = () => banner("Mistet forbindelsen til serveren – prøver igen …", true);
  es.onopen = () => banner("");
}

function onState(s) {
  state = s;
  for (const [name, v] of Object.entries(s.values)) {
    const h = (history[name] ||= []);
    h.push({ t: s.t, v });
    if (h.length > HISTORY_MAX) h.shift();
  }
  window.city.update(s);
  updateClock(s.clock);
  updateStrip(s);
  updateDetail(s);
  updateEnergy(s.values);
  updateDevices(s.devices);
}

// ------------------------------------------------------------------ topbar
function buildControls() {
  const hourSel = $("hourSel");
  hourSel.innerHTML = `<option value="">—</option>` + Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, "0")}:00</option>`).join("");
  hourSel.onchange = () => { if (hourSel.value !== "") api("/api/sim", { hour: Number(hourSel.value) }); hourSel.value = ""; };
  $("weatherSel").onchange = (e) => api("/api/sim", { weather: e.target.value });
  $("speedSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-pause")) api("/api/sim", { paused: !state?.clock.paused });
    else api("/api/sim", { speed: Number(b.dataset.speed), paused: false });
  });
  $("resetBtn").onclick = () => { if (confirm("Nulstil hele byen til morgen på dag 1?")) api("/api/sim", { reset: true }); };
  $("connectBtn").onclick = toggleMicrobit;
  if (!link.supported) {
    $("connectBtn").title = "Kræver Chrome eller Edge på en computer";
  }
}

function updateClock(c) {
  const h = Math.floor(c.minute / 60), m = Math.floor(c.minute % 60);
  $("clockTime").textContent = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  $("clockDay").textContent = `Dag ${c.day}`;
  const icons = { sol: "☀️", skyet: "☁️", regn: "🌧️", storm: "⛈️" };
  $("clockWeather").textContent = `${icons[c.regime] || ""} ${c.regimeLabel}${c.weather === "auto" ? "" : " (låst)"}`;
  if (document.activeElement !== $("weatherSel")) $("weatherSel").value = c.weather;
  for (const b of $("speedSeg").querySelectorAll("button")) {
    b.classList.toggle("on", b.hasAttribute("data-pause") ? c.paused : !c.paused && Number(b.dataset.speed) === c.speed);
  }
}

// ------------------------------------------------------------------ kort under kortet
function buildStrip() {
  $("strip").innerHTML = models.map((m) => `
    <div class="mini" data-code="${m.code}" tabindex="0">
      <div class="mini-top"><span>${m.icon}</span><span>${esc(m.name)}</span><i class="dot sim"></i></div>
      <div class="mini-row"><span class="sum">–</span><code>${m.code}</code></div>
    </div>`).join("");
  $("strip").addEventListener("click", (e) => {
    const c = e.target.closest(".mini");
    if (c) select(c.dataset.code);
  });
}

function modelSource(code, s) {
  const srcs = models.find((m) => m.code === code).keys.map((k) => s.source[`${code}.${k.key}`]);
  return srcs.includes("microbit") ? "live" : srcs.includes("web") ? "web" : "sim";
}

function updateStrip(s) {
  for (const card of $("strip").children) {
    const code = card.dataset.code;
    card.querySelector(".sum").textContent = summary(code, s.values);
    card.querySelector(".dot").className = `dot ${modelSource(code, s)}`;
    card.classList.toggle("selected", code === selected);
  }
}

// ------------------------------------------------------------------ detaljepanel
function select(code) {
  selected = code;
  try { localStorage.setItem("sc.selected", code); } catch {}
  window.city.select(code);
  buildDetail();
  if (state) { updateDetail(state); updateStrip(state); }
  // Hent de sidste 30 minutter fra databasen, så grafen ikke starter tom
  const m = models.find((x) => x.code === code);
  for (const k of m.keys) {
    const name = `${code}.${k.key}`;
    api(`/api/history?name=${encodeURIComponent(name)}&minutes=30`).then((rows) => {
      const live = history[name] || [];
      const first = live.length ? live[0].t : Infinity;
      const old = rows.filter((r) => r[0] < first).map((r) => ({ t: r[0], v: r[1] }));
      history[name] = old.concat(live).slice(-HISTORY_MAX);
      if (selected === code && state) updateDetail(state);
    }).catch(() => {});
  }
}

function buildDetail() {
  const m = models.find((x) => x.code === selected);
  const rows = m.keys.map((k) => {
    const name = `${m.code}.${k.key}`;
    const isSensor = k.kind === "sensor";
    const step = k.max - k.min <= 1 ? 1 : k.max - k.min <= 30 ? 0.5 : 1;
    return `
      <div class="kv" data-name="${name}">
        <div class="kv-top">
          <span class="name">${esc(k.label)}</span>
          <code>${name}</code>
          <span class="chip kind" title="${isSensor ? "Data fra micro:bit til byen" : "Byen sender dette til micro:bit'en"}">${isSensor ? "sensor ↑" : "aktuator ↓"}</span>
          <span class="value"><span class="v">–</span> <span class="unit">${esc(k.unit)}</span></span>
          <span class="chip sim src">SIM</span>
        </div>
        <canvas class="spark"></canvas>
        <div class="sendrow">
          <input type="range" min="${k.min}" max="${k.max}" step="${step}" value="${k.min}">
          <input type="number" min="${k.min}" max="${k.max}" step="${step}" value="${k.min}">
          <button class="send" title="${isSensor ? "Lader som om en micro:bit sender værdien" : "Sender en kommando ud til micro:bit'en"}">${isSensor ? "Send som micro:bit" : "Send kommando"}</button>
          <button class="release hidden" title="Lad simuleringen styre værdien igen">↺</button>
        </div>
      </div>`;
  }).join("");
  const firstSensor = m.keys.find((k) => k.kind === "sensor");
  const firstAkt = m.keys.find((k) => k.kind === "aktuator");
  const snippet = [
    `<span class="c">// Send data til byen</span>`,
    firstSensor ? `radio.sendValue("${m.code}.${firstSensor.key}", værdi)` : `<span class="c">// (denne model har ingen sensorer)</span>`,
    `<span class="c">// Modtag kommandoer fra byen</span>`,
    `radio.onReceivedValue(function (name, value) {`,
    `    if (name == "${m.code}.${(firstAkt || firstSensor).key}") { ... }`,
    `})`,
  ].join("\n");

  $("detail").innerHTML = `
    <div class="detail-head"><span class="big">${m.icon}</span><div><h3>${esc(m.name)}</h3></div></div>
    <p class="detail-desc">${esc(m.desc)}</p>
    ${rows}
    <div class="snippet">${snippet}</div>`;

  for (const row of $("detail").querySelectorAll(".kv")) {
    const name = row.dataset.name;
    const [range, num] = row.querySelectorAll("input");
    range.oninput = () => (num.value = range.value);
    num.oninput = () => (range.value = num.value);
    row.dataset.touched = "";
    range.addEventListener("pointerdown", () => (row.dataset.touched = "1"));
    num.addEventListener("focus", () => (row.dataset.touched = "1"));
    row.querySelector(".send").onclick = async () => {
      const r = await api("/api/data", { items: [{ name, value: Number(num.value), source: "web" }] });
      row.dataset.touched = "";
      if (!r.results[0].ok) alert(r.results[0].msg);
    };
    row.querySelector(".release").onclick = () => api("/api/release", { name });
  }
}

function updateDetail(s) {
  for (const row of $("detail").querySelectorAll(".kv")) {
    const name = row.dataset.name;
    const d = keydefs[name];
    const v = s.values[name];
    const src = s.source[name] || "sim";
    const decimals = d.max - d.min <= 40 && !Number.isInteger(v) ? 1 : 0;
    row.querySelector(".v").textContent = fmt(v, decimals);
    const chip = row.querySelector(".src");
    chip.className = `chip src ${SRC[src][0]}`;
    chip.textContent = SRC[src][1];
    row.querySelector(".release").classList.toggle("hidden", src === "sim");
    if (!row.dataset.touched) {
      const [range, num] = row.querySelectorAll("input");
      range.value = v;
      num.value = Math.round(v * 10) / 10;
    }
    drawSpark(row.querySelector(".spark"), history[name] || [], d);
  }
}

function drawSpark(canvas, pts, d) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#f4f6f9";
  ctx.fillRect(0, 0, w, h);
  if (pts.length < 2) return;
  const t1 = pts[pts.length - 1].t, t0 = Math.max(pts[0].t, t1 - 30 * 60);
  const span = Math.max(1, t1 - t0);
  const y = (v) => h - 3 - ((v - d.min) / (d.max - d.min || 1)) * (h - 6);
  ctx.beginPath();
  let started = false;
  for (const p of pts) {
    if (p.t < t0) continue;
    const x = ((p.t - t0) / span) * w;
    if (!started) { ctx.moveTo(x, y(p.v)); started = true; } else ctx.lineTo(x, y(p.v));
  }
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 1.6;
  ctx.stroke();
}

// ------------------------------------------------------------------ energi, enheder, log
function updateEnergy(V) {
  const prod = V["BY.prod"], use = V["BY.forbrug"], max = Math.max(100, prod, use);
  $("eProd").style.width = `${(prod / max) * 100}%`;
  $("eUse").style.width = `${(use / max) * 100}%`;
  $("eProdV").textContent = `${fmt(prod)} kW`;
  $("eUseV").textContent = `${fmt(use)} kW`;
  const net = prod - use;
  $("eNet").innerHTML = net >= 0
    ? `⚡ Overskud på <b>${fmt(net)} kW</b> – elbilen lader hurtigt`
    : `🔻 Underskud på <b>${fmt(-net)} kW</b> – byen køber strøm`;
}

function updateDevices(devs) {
  $("devCount").textContent = devs.filter((d) => d.age < 20).length;
  if (!devs.length) {
    $("devices").innerHTML = `<li><span class="empty">Ingen micro:bits endnu. Sæt gateway-micro:bit'en i USB og tryk <b>Forbind micro:bit</b>.</span></li>`;
    return;
  }
  $("devices").innerHTML = devs.sort((a, b) => a.age - b.age).map((d) => {
    const m = models.find((x) => x.code === d.model);
    const on = d.age < 20;
    return `<li><i class="dot ${on ? "live" : "sim"}"></i>${m?.icon || ""} <b>${esc(d.model)}</b> <code>${esc(d.serial)}</code>
      <span class="age">${on ? `${d.packets} pakker` : `${fmt(d.age / 60)} min siden`}</span></li>`;
  }).join("");
}

function addLog(ev) {
  const li = document.createElement("li");
  li.className = ev.level || "info";
  const t = new Date(ev.ts * 1000);
  li.innerHTML = `<time>${t.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time><span>${esc(ev.text)}</span>`;
  const log = $("log");
  log.prepend(li);
  while (log.children.length > 80) log.lastChild.remove();
}

function banner(text, warn) {
  const b = $("connBanner");
  b.classList.toggle("hidden", !text);
  b.textContent = text;
  b.style.background = warn ? "" : "#ecfdf5";
}

// ------------------------------------------------------------------ micro:bit via USB
let pending = [];
let packets = 0;
let lastRawLog = 0;

const link = new MicrobitLink({
  onData(d) {
    packets++;
    pending.push({ name: d.name, value: d.value, serial: d.serial, source: "microbit" });
    $("connectBtn").textContent = `✓ micro:bit · ${packets} pakker`;
  },
  onRaw(line) {
    // Vis linjer vi ikke forstår (max én pr. sekund) – nyttigt når man fejlsøger
    if (Date.now() - lastRawLog > 1000) {
      lastRawLog = Date.now();
      addLog({ ts: Date.now() / 1000, level: "serial", text: `Ukendt linje fra micro:bit: "${line.slice(0, 60)}"` });
    }
  },
  onStatus(s) {
    const btn = $("connectBtn");
    btn.classList.toggle("connected", s === "connected");
    btn.textContent = s === "connected" ? "✓ micro:bit forbundet" : "🔌 Forbind micro:bit";
    if (s === "connected") {
      packets = 0;
      api("/api/sim", { resync: true });  // send alle aktuator-værdier ud igen
      addLog({ ts: Date.now() / 1000, level: "info", text: "micro:bit forbundet via USB" });
    } else {
      addLog({ ts: Date.now() / 1000, level: "warn", text: "micro:bit afbrudt" });
    }
  },
});

// Saml data i små pakker, så serveren ikke får én forespørgsel pr. radiobesked
setInterval(() => {
  if (!pending.length) return;
  const items = pending.splice(0, 200);
  api("/api/data", { items }).then((r) => {
    for (const x of r.results) if (!x.ok) addLog({ ts: Date.now() / 1000, level: "warn", text: `${x.name}: ${x.msg}` });
  }).catch(() => {});
}, 150);

async function toggleMicrobit() {
  try {
    if (link.connected) await link.disconnect();
    else await link.connect();
  } catch (e) {
    if (e.name !== "NotFoundError") alert(e.message); // NotFoundError = brugeren lukkede vinduet
  }
}

init().catch((e) => banner(`Kunne ikke starte: ${e.message}`, true));
