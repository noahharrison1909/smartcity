// Forbindelse til en micro:bit over USB med WebSerial (Chrome / Edge).
//
// Linjer FRA micro:bit'en kan have tre formater:
//   "123456,GL.lys,120"   (gateway: serienummer, navn, værdi)
//   "GL.lys,120"          (micro:bit direkte i USB)
//   "GL.lys:120"          (MakeCode serial.writeValue)
// Linjer TIL micro:bit'en er altid "GL.lampe,100\n".

const LINE = /^(?:(-?\d+),)?([A-Za-z0-9]{2}\.[A-Za-z0-9]+)\s*[,:]\s*(-?\d+(?:\.\d+)?)$/;

export function parseLine(line) {
  const m = LINE.exec(line.trim());
  if (!m) return null;
  return { serial: m[1] ?? "usb", name: m[2], value: Number(m[3]) };
}

export class MicrobitLink {
  constructor({ onData, onRaw, onStatus }) {
    this.onData = onData;
    this.onRaw = onRaw || (() => {});
    this.onStatus = onStatus || (() => {});
    this.port = null;
    this.writeChain = Promise.resolve();
  }

  get supported() { return "serial" in navigator; }
  get connected() { return !!this.port; }

  async connect() {
    if (!this.supported) throw new Error("WebSerial virker kun i Chrome eller Edge på en computer.");
    const port = await navigator.serial.requestPort({ filters: [{ usbVendorId: 0x0d28 }] }); // 0x0d28 = micro:bit
    await port.open({ baudRate: 115200 });
    this.port = port;

    const encoder = new TextEncoderStream();
    this.writableClosed = encoder.readable.pipeTo(port.writable).catch(() => {});
    this.writer = encoder.writable.getWriter();

    const decoder = new TextDecoderStream();
    this.readableClosed = port.readable.pipeTo(decoder.writable).catch(() => {});
    this.reader = decoder.readable.getReader();

    navigator.serial.addEventListener("disconnect", this._onDisconnect = (e) => {
      if (e.target === this.port) this.disconnect();
    });
    this.onStatus("connected");
    this._readLoop();
  }

  async _readLoop() {
    let buf = "";
    try {
      while (true) {
        const { value, done } = await this.reader.read();
        if (done) break;
        buf += value;
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const raw of lines) {
          const line = raw.replace(/\r$/, "").trim();
          if (!line) continue;
          const d = parseLine(line);
          if (d) this.onData(d); else this.onRaw(line);
        }
        if (buf.length > 1000) buf = ""; // beskyt mod skrald uden linjeskift
      }
    } catch (e) {
      console.warn("Læsefejl", e);
    }
    this.disconnect();
  }

  send(name, value) {
    if (!this.writer) return;
    // Kø, så linjer ikke blandes sammen
    // og med en lille pause, så gateway'ens lille modtage-buffer ikke løber over
    this.writeChain = this.writeChain
      .then(() => this.writer?.write(`${name},${value}\n`))
      .then(() => new Promise((r) => setTimeout(r, 20)))
      .catch(() => {});
  }

  async disconnect() {
    if (!this.port) return;
    const port = this.port;
    this.port = null;
    navigator.serial.removeEventListener("disconnect", this._onDisconnect);
    try { await this.reader?.cancel(); } catch {}
    try { await this.readableClosed; } catch {}
    try { await this.writer?.close(); } catch {}
    try { await this.writableClosed; } catch {}
    this.writer = null;
    try { await port.close(); } catch {}
    this.onStatus("disconnected");
  }
}
