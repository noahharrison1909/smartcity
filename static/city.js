// Tegner byen som SVG og opdaterer den ud fra serverens tilstand.
const NS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else e.setAttribute(k, v);
  }
  if (parent) parent.appendChild(e);
  return e;
}

const fmt = (v, d = 0) => Number(v).toLocaleString("da-DK", { maximumFractionDigits: d, minimumFractionDigits: d });

// Kort tekst der vises under hver model (bruges også i kortene under kortet)
export function summary(code, V) {
  const g = (k) => V[`${code}.${k}`];
  switch (code) {
    case "BY": return `${fmt(g("temp"), 1)} °C · ${fmt(g("vind"))} m/s`;
    case "SB": return g("aktiv") >= 1 ? `Kører · ${fmt(g("pos"))} %` : "På depot";
    case "DH": return `Fugt ${fmt(g("fugt"))} %${g("pumpe") >= 1 ? " · vander" : ""}`;
    case "VM": return g("stop") >= 1 ? "Stormstop" : `${fmt(g("kW"))} kW`;
    case "GL": return g("lampe") > 0 ? `Lampe ${fmt(g("lampe"))} %` : "Slukket";
    case "LS": return g("bil") >= 1 ? `${fmt(g("lad"))} % · ${fmt(g("kW"), 1)} kW` : "Ledig";
    case "TL": return `${["Rød", "Gul", "Grøn"][Math.round(g("fase"))]} · ${fmt(g("biler"))} biler`;
    case "VP": return `${fmt(g("inde"), 1)} °C${g("on") >= 1 ? " · varmer" : ""}`;
    case "OV": return g("alarm") >= 1 ? `ALARM ${fmt(g("vand"))} cm` : `${fmt(g("vand"))} cm`;
    case "RV": return `${fmt(g("tank"))} %`;
    case "S1": case "S2": return `${fmt(g("fyld"))} % fuld`;
    default: return "";
  }
}

// Skraldebilens rute (rundt om midterblokken), pos 0-100 %
const ROUTE = [[330, 420], [330, 200], [670, 200], [670, 420], [330, 420]];
const ROUTE_LEN = ROUTE.slice(1).reduce((s, p, i) => s + Math.hypot(p[0] - ROUTE[i][0], p[1] - ROUTE[i][1]), 0);
function routePoint(pct) {
  let d = (pct / 100) * ROUTE_LEN;
  for (let i = 1; i < ROUTE.length; i++) {
    const [x0, y0] = ROUTE[i - 1], [x1, y1] = ROUTE[i];
    const len = Math.hypot(x1 - x0, y1 - y0);
    if (d <= len) return [x0 + ((x1 - x0) * d) / len, y0 + ((y1 - y0) * d) / len];
    d -= len;
  }
  return ROUTE[0];
}

export class CityMap {
  constructor(svg, models, onSelect) {
    this.svg = svg;
    this.models = models;
    this.onSelect = onSelect;
    this.nodes = {};
    this.fx = {};
    this.build();
  }

  build() {
    const s = this.svg;
    const defs = el("defs", {}, s);
    const grad = el("radialGradient", { id: "lampGlow" }, defs);
    el("stop", { offset: "0%", "stop-color": "#fff6b0", "stop-opacity": "1" }, grad);
    el("stop", { offset: "100%", "stop-color": "#ffd84d", "stop-opacity": "0" }, grad);
    const heat = el("radialGradient", { id: "heatGlow" }, defs);
    el("stop", { offset: "0%", "stop-color": "#ff7a45", "stop-opacity": ".75" }, heat);
    el("stop", { offset: "100%", "stop-color": "#ff7a45", "stop-opacity": "0" }, heat);

    // Jord, park og mark
    el("rect", { x: 0, y: 0, width: 1000, height: 600, fill: "#d9ecd0" }, s);
    el("rect", { x: 688, y: 438, width: 312, height: 162, fill: "#c9e3b5" }, s);
    for (let i = 0; i < 6; i++) el("line", { x1: 700, y1: 460 + i * 24, x2: 990, y2: 460 + i * 24, stroke: "#b7d79f", "stroke-width": 6 }, s);

    // Lavning hvor vandet samler sig
    el("rect", { x: 30, y: 452, width: 230, height: 132, rx: 14, fill: "#c7ccd3", stroke: "#a8afb8" }, s);
    this.fx.flood = el("rect", { x: 30, y: 584, width: 230, height: 0, rx: 14, fill: "#4a90d9", "fill-opacity": .75, class: "fillbar" }, s);

    // Veje
    const road = { fill: "#59626e" };
    el("rect", Object.assign({ x: 0, y: 182, width: 1000, height: 36 }, road), s);
    el("rect", Object.assign({ x: 0, y: 402, width: 1000, height: 36 }, road), s);
    el("rect", Object.assign({ x: 312, y: 0, width: 36, height: 600 }, road), s);
    el("rect", Object.assign({ x: 652, y: 0, width: 36, height: 600 }, road), s);
    const dash = { stroke: "#e8e2c4", "stroke-width": 2, "stroke-dasharray": "14 12" };
    el("line", Object.assign({ x1: 0, y1: 200, x2: 1000, y2: 200 }, dash), s);
    el("line", Object.assign({ x1: 0, y1: 420, x2: 1000, y2: 420 }, dash), s);
    el("line", Object.assign({ x1: 330, y1: 0, x2: 330, y2: 600 }, dash), s);
    el("line", Object.assign({ x1: 670, y1: 0, x2: 670, y2: 600 }, dash), s);
    // Fodgængerfelt ved trafiklyset
    for (let i = 0; i < 5; i++) el("rect", { x: 292, y: 184 + i * 7, width: 16, height: 4, fill: "#f3f4f6" }, s);

    // Huse
    const houses = [
      [370, 22, 70, 86], [585, 20, 52, 96], [710, 22, 62, 58], [905, 24, 72, 70],
      [24, 236, 76, 92], [128, 262, 60, 56], [214, 300, 74, 80],
      [360, 330, 56, 62], [560, 236, 72, 70], [566, 324, 62, 62],
      [835, 352, 54, 38], [600, 466, 46, 62],
    ];
    const windows = [];
    for (const [x, y, w, h] of houses) {
      el("rect", { x, y, width: w, height: h, rx: 4, fill: "#e6dccb", stroke: "#c5b9a3" }, s);
      for (let wx = x + 8; wx < x + w - 10; wx += 16)
        for (let wy = y + 10; wy < y + h - 12; wy += 18) windows.push([wx, wy]);
    }

    // Rør fra regnvandstank til drivhus
    this.fx.pipe = el("path", { d: "M175 65 V44 H70 V65", fill: "none", stroke: "#94a3b8", "stroke-width": 5, "stroke-linecap": "round" }, s);

    // Nat
    this.fx.night = el("rect", { id: "nightOverlay", x: 0, y: 0, width: 1000, height: 600, fill: "#0b1838", "fill-opacity": 0 }, s);
    this.fx.windows = windows.map(([x, y]) => el("rect", { x, y, width: 8, height: 9, fill: "#ffd76a", "fill-opacity": 0, class: "window" }, s));

    // Gadelygte
    el("line", { x1: 405, y1: 252, x2: 405, y2: 224, stroke: "#334155", "stroke-width": 3 }, s);
    this.fx.lampGlow = el("circle", { cx: 405, cy: 224, r: 0, fill: "url(#lampGlow)", class: "glow" }, s);
    el("circle", { cx: 405, cy: 224, r: 4, fill: "#fde68a" }, s);

    // Varmepumpe-varme
    const vp = this.model("VP");
    this.fx.heat = el("circle", { cx: vp.x, cy: vp.y, r: 70, fill: "url(#heatGlow)", "fill-opacity": 0, class: "glow" }, s);

    // Vindmølle
    el("line", { x1: 960, y1: 448, x2: 960, y2: 575, stroke: "#e5e7eb", "stroke-width": 6, "stroke-linecap": "round" }, s);
    this.fx.blades = el("g", { class: "blades" }, s);
    for (let i = 0; i < 3; i++) {
      el("path", { d: "M960 448 L956 410 Q960 402 964 410 Z", fill: "#f8fafc", stroke: "#cbd5e1", transform: `rotate(${i * 120} 960 448)` }, this.fx.blades);
    }
    el("circle", { cx: 960, cy: 448, r: 5, fill: "#94a3b8" }, s);

    // Trafiklys
    el("rect", { x: 304, y: 108, width: 18, height: 50, rx: 5, fill: "#1f2937" }, s);
    this.fx.tl = ["#ef4444", "#f59e0b", "#22c55e"].map((c, i) =>
      el("circle", { cx: 313, cy: 119 + i * 14, r: 5, fill: c, "fill-opacity": .15 }, s));
    this.fx.cars = [];
    const carColors = ["#3b82f6", "#ef4444", "#f59e0b", "#10b981", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];
    for (let i = 0; i < 12; i++) {
      const g = el("g", { class: "car", opacity: 0 }, s);
      el("rect", { x: -9, y: -5, width: 18, height: 10, rx: 3, fill: carColors[i % carColors.length] }, g);
      el("rect", { x: 1, y: -4, width: 5, height: 8, rx: 1, fill: "#dbeafe" }, g);
      this.fx.cars.push(g);
    }

    // Skraldebil på ruten
    el("text", { x: 345, y: 500, "font-size": 11, "text-anchor": "middle", fill: "#475569", text: "Depot" }, s);
    this.fx.truck = el("g", { class: "truck" }, s);
    el("circle", { r: 16, fill: "#fff", stroke: "#475569", "stroke-width": 2 }, this.fx.truck);
    el("text", { "font-size": 20, "text-anchor": "middle", "dominant-baseline": "central", text: "🚛" }, this.fx.truck);

    // Regn
    this.fx.rain = [];
    const rain = el("g", { "pointer-events": "none" }, s);
    for (let i = 0; i < 90; i++) {
      const x = Math.random() * 1000, y = Math.random() * 600 - 600;
      const d = el("line", { x1: x, y1: y, x2: x - 4, y2: y + 18, class: "drop", opacity: 0 }, rain);
      d.style.animationDuration = `${0.55 + Math.random() * 0.4}s`;
      d.style.animationDelay = `${-Math.random()}s`;
      this.fx.rain.push(d);
    }

    // Modeller (klikbare)
    for (const m of this.models) this.buildNode(m);
  }

  model(code) { return this.models.find((m) => m.code === code); }

  buildNode(m) {
    const g = el("g", { class: "node", transform: `translate(${m.x} ${m.y})`, tabindex: 0, role: "button", "aria-label": m.name }, this.svg);
    el("circle", { r: 30, class: "halo" }, g);
    el("text", { class: "icon", y: 1, text: m.icon }, g);
    const badge = el("circle", { cx: 22, cy: -22, r: 7, class: "badge sim", stroke: "#fff", "stroke-width": 2 }, g);
    el("text", { class: "label", y: 46, text: m.name }, g);
    const val = el("text", { class: "val", y: 61, text: "" }, g);
    let bar = null;
    if (["S1", "S2", "RV", "DH", "LS"].includes(m.code)) {
      el("rect", { x: 36, y: -26, width: 9, height: 52, rx: 3, fill: "#e5e7eb", stroke: "#cbd5e1" }, g);
      bar = el("rect", { x: 37, y: 25, width: 7, height: 0, rx: 2, fill: "#22c55e", class: "fillbar" }, g);
    }
    let pulse = null;
    if (m.code === "OV") pulse = el("circle", { r: 34, fill: "none", stroke: "#dc2626", "stroke-width": 4, class: "pulse", opacity: 0 }, g);
    g.addEventListener("click", () => this.onSelect(m.code));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") this.onSelect(m.code); });
    this.nodes[m.code] = { g, badge, val, bar, pulse };
  }

  select(code) {
    for (const [c, n] of Object.entries(this.nodes)) n.g.classList.toggle("selected", c === code);
  }

  update(state) {
    const V = state.values, S = state.source;
    const sol = V["BY.sol"], regn = V["BY.regn"];

    // Dag/nat
    const dark = Math.max(0, 1 - sol / 45);
    this.fx.night.setAttribute("fill-opacity", (dark * 0.5).toFixed(2));
    const lit = Math.round(this.fx.windows.length * dark * 0.7);
    this.fx.windows.forEach((w, i) => w.setAttribute("fill-opacity", (i * 7919) % this.fx.windows.length < lit ? 0.9 : 0));

    // Regn
    const drops = Math.round(Math.min(1, regn / 13) * this.fx.rain.length);
    this.fx.rain.forEach((d, i) => d.setAttribute("opacity", i < drops ? 0.7 : 0));

    // Gadelys og varme
    const lampe = V["GL.lampe"];
    this.fx.lampGlow.setAttribute("r", lampe > 0 ? 18 + lampe * 0.35 : 0);
    const on = V["VP.on"] >= 1;
    this.fx.heat.setAttribute("fill-opacity", on ? 1 : 0);

    // Vindmølle
    const kW = V["VM.kW"];
    this.fx.blades.style.animationDuration = `${Math.max(0.35, 5 - kW / 14)}s`;
    this.fx.blades.style.animationPlayState = kW > 0 ? "running" : "paused";

    // Trafiklys og kø
    const fase = Math.round(V["TL.fase"]);
    this.fx.tl.forEach((c, i) => c.setAttribute("fill-opacity", i === 2 - fase ? 1 : 0.15));
    const cars = Math.min(12, Math.round(V["TL.biler"]));
    this.fx.cars.forEach((c, i) => {
      c.setAttribute("opacity", i < cars ? 1 : 0);
      c.setAttribute("transform", `translate(${290 - i * 24} 209)`);
    });

    // Skraldebil
    const [tx, ty] = V["SB.aktiv"] >= 1 ? routePoint(V["SB.pos"]) : [345, 470];
    this.fx.truck.setAttribute("transform", `translate(${tx} ${ty})`);

    // Oversvømmelse og regnvandsrør
    const h = Math.min(132, V["OV.vand"] * 1.32);
    this.fx.flood.setAttribute("y", 584 - h);
    this.fx.flood.setAttribute("height", h);
    const pumping = V["DH.pumpe"] >= 1;
    this.fx.pipe.setAttribute("stroke", pumping ? "#3b82f6" : "#94a3b8");
    this.fx.pipe.setAttribute("stroke-dasharray", pumping ? "8 6" : "none");

    // Noder
    for (const m of this.models) {
      const n = this.nodes[m.code];
      const srcs = m.keys.map((k) => S[`${m.code}.${k.key}`]);
      const src = srcs.includes("microbit") ? "live" : srcs.includes("web") ? "web" : "sim";
      n.badge.setAttribute("class", `badge ${src}`);
      n.g.classList.toggle("live", src === "live");
      n.val.textContent = summary(m.code, V);
      if (n.bar) {
        const pct = { S1: V["S1.fyld"], S2: V["S2.fyld"], RV: V["RV.tank"], DH: V["DH.fugt"], LS: V["LS.lad"] }[m.code];
        const bh = Math.max(0, Math.min(50, pct / 2));
        n.bar.setAttribute("height", bh);
        n.bar.setAttribute("y", 25 - bh);
        const bad = m.code.startsWith("S") ? pct >= 80 : m.code === "LS" ? false : pct < 25;
        n.bar.setAttribute("fill", bad ? "#ef4444" : m.code === "S1" || m.code === "S2" ? (pct >= 50 ? "#f59e0b" : "#22c55e") : "#3b82f6");
      }
    }
    const alarm = V["OV.alarm"] >= 1;
    this.nodes.OV.g.classList.toggle("alarm", alarm);
    this.nodes.OV.pulse.setAttribute("opacity", alarm ? 1 : 0);
  }
}
