#!/usr/bin/env python3
"""Web dashboard for llamagputop.

Serves every metric llamagputop.py collects over HTTP on 0.0.0.0:7778.

This file deliberately adds no rendering logic of its own to llamagputop.py: it
imports it as a module and drives the SAME collection seam the curses TUI uses
(discover_gpus -> feeds -> _LlamaFeed -> _collect), so the web view and the
terminal view are reading one set of numbers, not two implementations of them.

It also honours llamagputop's central contract: a missing reading renders as an
em dash, never as 0. `None` and `0` are different facts here (an idle GPU really
is at 0 %; a card with no power source available is not at 0 W), and the JSON
keeps them distinct so the browser can too.

Usage:  python3 dashboard.py [PORT] [--bind ADDR] [--llama-port N] [--interval S]
No dependencies beyond the Python standard library.
"""

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import llamagputop as L  # noqa: E402

HTTP_PORT = 7778
BIND = "0.0.0.0"


# ----------------------------------------------------------------- json safety
def san(o):
    """JSON-safe deep copy. Preserves None (it means 'no reading')."""
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        # NaN/inf are not valid JSON and would break the whole poll
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {str(k): san(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [san(v) for v in o]
    return str(o)


def series(key, limit=120):
    """A HIST deque as a plain list, most recent `limit` points."""
    s = L.HIST.get(key)
    if not s:
        return []
    v = [x for x in list(s) if isinstance(x, (int, float))]
    return san(v[-limit:])


def stats(key):
    """median/stdev over the recent window plus min/avg/max over all history."""
    med, sd = L.median_dev(key)
    lo, avg, hi = L._extremes(key)
    return san({"median": med, "stdev": sd, "min": lo, "avg": avg, "max": hi})


def _temp_state(t):
    if t is None:
        return "none"
    return "crit" if t >= 90 else "warn" if t >= 80 else "ok"


def _util_state(u):
    if u is None:
        return "none"
    return "crit" if u >= 95 else "warn" if u >= 80 else "ok"


def _pct(used, total):
    if used is None or not total:
        return None
    return 100.0 * used / total


# ------------------------------------------------------------------- snapshot
def build_snapshot(gpus_data, cpu, mem, llamas, cfgs, procs, interval, started):
    total_w, notes = L.system_power(gpus_data, cpu)
    sess = L._session
    sess_s = (sess["t"] - sess["start"]) if (sess.get("t") and sess.get("start")) else 0.0

    gpus = []
    for i, s in enumerate(gpus_data):
        g = san(dict(s))
        g["index"] = i
        g["vram_pct"] = _pct(s.get("vram_used"), s.get("vram_total"))
        g["vram_free"] = (s["vram_total"] - s["vram_used"]) if (
            s.get("vram_total") is not None and s.get("vram_used") is not None) else None
        g["power_pct"] = _pct(s.get("power"), s.get("power_cap"))
        g["temp_state"] = _temp_state(s.get("temp_main"))
        g["util_state"] = _util_state(s.get("util"))
        g["util_series"] = series(f"gpu{i}_util")
        g["vram_series"] = series(f"gpu{i}_vram")
        g["util_stats"] = stats(f"gpu{i}_util")
        gpus.append(g)

    c = san(dict(cpu))
    c["util_state"] = _util_state(cpu.get("util"))
    c["temp_state"] = _temp_state(cpu.get("temp"))
    c["util_series"] = series("cpu_util")

    m = san(dict(mem))
    if mem.get("total") is not None and mem.get("free") is not None:
        m["used"] = mem["total"] - mem["free"]
        m["used_pct"] = _pct(m["used"], mem["total"])
    else:
        m["used"] = m["used_pct"] = None
    m["swap_pct"] = _pct(mem.get("swap_used"), mem.get("swap_total"))
    m["free_series"] = series("ram_series")

    srv = []
    for d in llamas:
        port = d["port"]
        e = san(dict(d))
        e["ctx_text"] = L._ctx_text(d)
        e["why_pp"] = L._why(d, "pp")
        e["why_tg"] = L._why(d, "tg")
        e["kv_pct"] = (d["kv"] * 100) if d.get("kv") is not None else None
        e["spec_pct"] = (d["spec"] * 100) if d.get("spec") is not None else None
        e["tg_series"] = series(f"tg_series@{port}")
        e["pp_series"] = series(f"pp_series@{port}")
        e["kv_series"] = series(f"kv_series@{port}")
        e["tg_stats"] = stats(f"tg_gen@{port}")
        e["pp_stats"] = stats(f"pp_gen@{port}")
        e["config"] = san(cfgs.get(port))
        srv.append(e)

    ps = []
    for p in procs:
        q = san(dict(p))
        gtt = p.get("gtt")
        if gtt:
            vf = None
            for s in gpus_data:
                if s.get("vram_total") is not None and s.get("vram_used") is not None:
                    vf = s["vram_total"] - s["vram_used"]
                    break
            st = L._gtt_status(gtt, vf)
            q["gtt_note"] = st[0] if st else None
        else:
            q["gtt_note"] = None
        ps.append(q)

    return {
        "ready": True,
        "ts": time.time(),
        "time": time.strftime("%H:%M:%S"),
        "host": socket.gethostname(),
        "interval": interval,
        "uptime_s": time.time() - started,
        "power": {
            "total_w": san(total_w),
            "notes": san(notes),
            "session_energy_j": san(sess.get("energy_j")),
            "session_wh": san((sess.get("energy_j") or 0) / 3600.0),
            "session_s": san(sess_s),
            "series": series("power_series"),
        },
        "gpus": gpus,
        "cpu": c,
        "mem": m,
        "llamas": srv,
        "procs": ps,
        "error": None,
    }


# ------------------------------------------------------------------ collector
class Collector(threading.Thread):
    def __init__(self, llama_port=None, interval=1.0):
        super().__init__(daemon=True)
        self.llama_port = llama_port
        self.interval = interval
        self.lock = threading.Lock()
        self.snapshot = {"ready": False, "error": None,
                         "message": "starting hardware discovery"}
        self.started = time.time()

    def latest(self):
        with self.lock:
            return self.snapshot

    def run(self):
        try:
            gpus, feeds = L.discover_gpus()
            for f in feeds.values():
                f.start()
            lfeed = L._LlamaFeed(self.llama_port)
            lfeed.start()
            # prime the derivatives exactly as the TUI and --once paths do: the first
            # CPU sample has no previous jiffies to difference against, and _collect's
            # trend records need one tick of history before they mean anything.
            L.cpu_sample()
            time.sleep(1.0)
            L._collect(gpus, self.llama_port, lfeed)
        except Exception as e:
            with self.lock:
                self.snapshot = {"ready": False, "error": f"{type(e).__name__}: {e}"}
            return
        while True:
            t0 = time.monotonic()
            try:
                data = L._collect(gpus, self.llama_port, lfeed)
                snap = build_snapshot(*data, interval=self.interval,
                                      started=self.started)
                with self.lock:
                    self.snapshot = snap
            except Exception as e:
                with self.lock:
                    prev = dict(self.snapshot)
                    prev["error"] = f"{type(e).__name__}: {e}"
                    self.snapshot = prev
            time.sleep(max(0.05, self.interval - (time.monotonic() - t0)))


COLLECTOR = None


# ----------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llamagputop-dashboard"

    def log_message(self, fmt, *args):
        pass  # journal gets the collector's errors; access logs are noise here

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/metrics":
            snap = COLLECTOR.latest()
            self._send(200, json.dumps(snap, allow_nan=False),
                       "application/json; charset=utf-8")
        elif path == "/healthz":
            ok = bool(COLLECTOR.latest().get("ready"))
            self._send(200 if ok else 503,
                       json.dumps({"ready": ok}), "application/json")
        else:
            self._send(404, json.dumps({"error": "not found"}), "application/json")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>llamagputop</title>
<style>
:root{
  --bg:#0e1116; --panel:#161b22; --panel2:#1c232d; --line:#2a3340;
  --fg:#e6edf3; --dim:#8b949e; --ok:#3fb950; --warn:#d29922; --crit:#f85149;
  --accent:#58a6ff; --ser:#39c5cf;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:13px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.num,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}
header{position:sticky;top:0;z-index:5;background:rgba(14,17,22,.92);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
  padding:10px 16px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
header h1{margin:0;font-size:15px;font-weight:650;letter-spacing:.2px}
header .host{color:var(--accent)}
.stat{color:var(--dim);font-size:12px}
.stat b{color:var(--fg);font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
.live{background:var(--ok);box-shadow:0 0 6px var(--ok)}
.dead{background:var(--crit);box-shadow:0 0 6px var(--crit)}
main{padding:16px;max-width:1600px;margin:0 auto}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.9px;color:var(--dim);
  margin:22px 0 10px;font-weight:600}
h2:first-child{margin-top:0}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 14px}
.card h3{margin:0 0 3px;font-size:13.5px;font-weight:650}
.card .sub{color:var(--dim);font-size:11.5px;margin-bottom:10px}
.row{display:flex;justify-content:space-between;gap:10px;padding:2.5px 0}
.row span:first-child{color:var(--dim)}
.row span:last-child{text-align:right}
.bar{height:7px;background:var(--panel2);border-radius:4px;overflow:hidden;margin:4px 0 2px}
.bar>i{display:block;height:100%;background:var(--ok);transition:width .35s ease}
.bar>i.warn{background:var(--warn)} .bar>i.crit{background:var(--crit)}
.bar>i.none{background:var(--line)}
.lbl{display:flex;justify-content:space-between;font-size:11.5px;color:var(--dim);margin-top:7px}
.lbl b{color:var(--fg);font-weight:600}
.ok{color:var(--ok)} .warn{color:var(--warn)} .crit{color:var(--crit)}
.dim{color:var(--dim)} .acc{color:var(--accent)}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;
  border:1px solid var(--line);background:var(--panel2);color:var(--dim)}
.pill.on{color:var(--ok);border-color:rgba(63,185,80,.4)}
.pill.off{color:var(--crit);border-color:rgba(248,81,73,.4)}
.pill.busy{color:var(--ser);border-color:rgba(57,197,207,.4)}
svg.spark{display:block;width:100%;height:34px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.wrap{overflow-x:auto}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--dim);font-size:11.5px;user-select:none}
summary:hover{color:var(--fg)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:1px 12px;margin-top:8px;font-size:11.5px}
.kv div:nth-child(odd){color:var(--dim)}
.kv div:nth-child(even){text-align:right;word-break:break-all}
.cfg{margin-top:6px}
.cfg .g{color:var(--accent);font-size:11px;margin:7px 0 2px;text-transform:uppercase;letter-spacing:.5px}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;
  padding:11px;overflow:auto;max-height:460px;font-size:11.5px}
.banner{background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.4);
  color:var(--crit);padding:9px 12px;border-radius:8px;margin-bottom:14px;font-size:12.5px}
.empty{color:var(--dim);font-style:italic;padding:8px 0}
</style></head><body>
<header>
  <h1>llamagputop <span class="host" id="host">…</span></h1>
  <span class="stat"><span class="dot dead" id="dot"></span><span id="conn">connecting</span></span>
  <span class="stat">power <b class="num" id="pwr">—</b></span>
  <span class="stat">session <b class="num" id="wh">—</b></span>
  <span class="stat">uptime <b class="num" id="up">—</b></span>
  <span class="stat">updated <b class="num" id="clock">—</b></span>
</header>
<main>
  <div id="err"></div>
  <div id="body"><div class="empty">Waiting for the first sample…</div></div>
</main>
<script>
const $ = id => document.getElementById(id);
const E = s => String(s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// llamagputop's contract: a missing reading is an em dash, never a zero.
const n = (v, d = 0) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d);
const pc = v => v === null || v === undefined ? '—' : Number(v).toFixed(0) + '%';

function dur(s){
  if (s === null || s === undefined) return '—';
  s = Math.floor(s);
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600),
        m = Math.floor(s%3600/60), x = s%60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${x}s`;
  return `${x}s`;
}
function bar(pct, state){
  const w = (pct === null || pct === undefined) ? 0 : Math.max(0, Math.min(100, pct));
  const cls = (pct === null || pct === undefined) ? 'none' : (state || '');
  return `<div class="bar"><i class="${cls}" style="width:${w}%"></i></div>`;
}
function spark(arr, color, maxOverride){
  if (!arr || arr.length < 2) return '';
  const max = maxOverride !== undefined && maxOverride !== null
    ? maxOverride : Math.max(...arr, 1e-9);
  const min = Math.min(...arr, 0);
  const rng = (max - min) || 1;
  const W = 100, H = 30;
  const pts = arr.map((v, i) => {
    const x = arr.length === 1 ? 0 : (i / (arr.length - 1)) * W;
    const y = H - ((v - min) / rng) * H;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.4"
      vector-effect="non-scaling-stroke"/></svg>`;
}
function row(k, v){ return `<div class="row"><span>${E(k)}</span><span class="num">${v}</span></div>`; }

// Every field not already shown above, so nothing llamagputop collects is hidden —
// including keys added to the tool after this dashboard was written.
function rest(obj, shown){
  const keys = Object.keys(obj).filter(k =>
    !shown.includes(k) && !k.endsWith('_series') && !k.endsWith('_stats') &&
    k !== 'config' && typeof obj[k] !== 'object');
  if (!keys.length) return '';
  return `<details><summary>all fields (${keys.length})</summary><div class="kv">` +
    keys.map(k => `<div>${E(k)}</div><div class="num">${
      obj[k] === null || obj[k] === undefined ? '—' : E(obj[k])}</div>`).join('') +
    `</div></details>`;
}

function gpuCard(g){
  const shown = ['index','vendor','name','pci_addr','util','vram_used','vram_total',
    'vram_pct','vram_free','temp_main','power','power_cap','power_pct','sclk','mclk',
    'mem_util','util_state','temp_state'];
  const temps = g.temp && Object.keys(g.temp).length
    ? Object.entries(g.temp).map(([k,v]) => `${E(k)} ${n(v)}°`).join(' · ') : null;
  return `<div class="card">
    <h3>GPU${g.index} · ${E(g.name || g.vendor || 'GPU')}</h3>
    <div class="sub">${E(g.vendor || '')} ${g.pci_addr ? '· ' + E(g.pci_addr) : ''}</div>
    <div class="lbl"><span>utilisation</span><b class="${g.util_state}">${pc(g.util)}</b></div>
    ${bar(g.util, g.util_state)}
    <div class="lbl"><span>VRAM</span><b>${n(g.vram_used)} / ${n(g.vram_total)} MiB · ${pc(g.vram_pct)}</b></div>
    ${bar(g.vram_pct, g.vram_pct >= 95 ? 'crit' : g.vram_pct >= 85 ? 'warn' : '')}
    <div class="lbl"><span>power</span><b>${n(g.power,1)} / ${n(g.power_cap)} W</b></div>
    ${bar(g.power_pct, g.power_pct >= 95 ? 'warn' : '')}
    <div style="margin-top:9px">
      ${row('temperature', `<span class="${g.temp_state}">${n(g.temp_main)} °C</span>`)}
      ${temps ? row('sensors', E(temps)) : ''}
      ${row('memory controller', pc(g.mem_util))}
      ${row('core clock', n(g.sclk) + ' MHz')}
      ${row('memory clock', n(g.mclk) + ' MHz')}
      ${row('VRAM free', n(g.vram_free) + ' MiB')}
    </div>
    ${spark(g.util_series, 'var(--ok)', 100)}
    <div class="lbl"><span>utilisation, recent</span><b>${
      g.util_stats ? `min ${n(g.util_stats.min)} · avg ${n(g.util_stats.avg)} · max ${n(g.util_stats.max)}` : '—'}</b></div>
    ${rest(g, shown)}
  </div>`;
}

function cpuCard(c){
  const shown = ['name','ncpu','util','freq','temp','power','rapl_present',
    'loadavg','util_state','temp_state'];
  const load = Array.isArray(c.load) ? c.load.join('  ') : (c.loadavg ?? '—');
  return `<div class="card">
    <h3>CPU</h3><div class="sub">${E(c.name || '')} · ${n(c.ncpu)} threads</div>
    <div class="lbl"><span>utilisation</span><b class="${c.util_state}">${pc(c.util)}</b></div>
    ${bar(c.util, c.util_state)}
    ${row('frequency', n(c.freq) + ' MHz')}
    ${row('load average', `<span class="mono">${E(load)}</span>`)}
    ${row('temperature', `<span class="${c.temp_state}">${n(c.temp)} °C</span>`)}
    ${row('package power', c.power === null
        ? '<span class="dim">— RAPL not readable</span>' : n(c.power,1) + ' W')}
    ${c.temps && Object.keys(c.temps).length
      ? row('sensors', Object.entries(c.temps).map(([k,v]) => `${E(k)} ${n(v)}°`).join(' · ')) : ''}
    ${spark(c.util_series, 'var(--accent)', 100)}
    ${rest(c, shown)}
  </div>`;
}

function memCard(m){
  const shown = ['total','free','used','used_pct','cached','buffers','committed',
    'dirty','shmem','swap_used','swap_total','swap_pct'];
  return `<div class="card">
    <h3>Memory</h3><div class="sub">${n(m.total)} MiB total</div>
    <div class="lbl"><span>used</span><b>${n(m.used)} / ${n(m.total)} MiB · ${pc(m.used_pct)}</b></div>
    ${bar(m.used_pct, m.used_pct >= 92 ? 'crit' : m.used_pct >= 80 ? 'warn' : '')}
    <div class="lbl"><span>swap</span><b>${n(m.swap_used)} / ${n(m.swap_total)} MiB</b></div>
    ${bar(m.swap_pct, m.swap_pct >= 50 ? 'warn' : '')}
    ${row('free', n(m.free) + ' MiB')}
    ${row('cached', n(m.cached) + ' MiB')}
    ${row('buffers', n(m.buffers) + ' MiB')}
    ${row('committed', n(m.committed) + ' MiB')}
    ${row('dirty', n(m.dirty) + ' MiB')}
    ${row('shmem', n(m.shmem) + ' MiB')}
    ${spark(m.free_series, 'var(--ser)')}
    <div class="lbl"><span>free MiB, recent</span><b></b></div>
    ${rest(m, shown)}
  </div>`;
}

function cfgBlock(cfg){
  if (!cfg) return '';
  const groups = Object.keys(cfg);
  if (!groups.length) return '';
  return `<details><summary>server configuration</summary><div class="cfg">` +
    groups.map(g => `<div class="g">${E(g)}</div>` +
      (cfg[g] || []).map(p => `<div class="row"><span>${E(p[0])}</span><span class="num">${E(p[1])}</span></div>`).join('')
    ).join('') + `</div></details>`;
}

function srvCard(d){
  const shown = ['port','pid','flavor','alive','stale','phase','model','pp','tg',
    'pp_last','tg_last','kv','kv_pct','spec','spec_pct','ctx','ctx_total','slots',
    'ctx_text','why_pp','why_tg','power_w','active','queued','ttft_last',
    'spec_acc','spec_draft','spec_type','spec_nmax','cache_hit','decoded',
    'kv_used','kv_cap','metrics_off','slots_off','multi','pp_life','tg_life'];
  const state = !d.alive ? 'off' : d.stale ? 'busy' : (d.phase === 'generating' || d.phase === 'prefill') ? 'busy' : 'on';
  const pill = !d.alive ? `<span class="pill off">offline</span>`
    : d.stale ? `<span class="pill busy">no answer</span>`
    : `<span class="pill ${state === 'busy' ? 'busy' : 'on'}">${E(d.phase || 'idle')}</span>`;
  // a past number must not wear the present tense — mark last-known rates as such
  const sp = (live, last, dec, why) => live !== null && live !== undefined
    ? `<b>${n(live, dec)}</b>`
    : (last !== null && last !== undefined
        ? `<b class="dim">${n(last, dec)}</b> <span class="dim">(last)</span>`
        : `<span class="dim">— ${E(why)}</span>`);
  return `<div class="card">
    <h3>llama.cpp :${E(d.port)} ${pill}</h3>
    <div class="sub">${E(d.model || 'no model loaded')}${d.pid ? ' · pid ' + E(d.pid) : ''} · ${E(d.flavor || '')}</div>
    ${row('generation t/s', sp(d.tg, d.tg_last, 1, d.why_tg))}
    ${row('prefill t/s', sp(d.pp, d.pp_last, 0, d.why_pp))}
    ${row('TTFT', d.ttft_last === null || d.ttft_last === undefined ? '<span class="dim">—</span>' : n(d.ttft_last,2) + ' s')}
    ${row('context', `<span class="mono">${E(d.ctx_text)}</span>`)}
    <div class="lbl"><span>KV cache</span><b>${pc(d.kv_pct)}${
      d.kv_used ? ` · ${n(d.kv_used)} cells` : ''}</b></div>
    ${bar(d.kv_pct, d.kv_pct >= 90 ? 'crit' : d.kv_pct >= 75 ? 'warn' : '')}
    ${row('slots', `${n(d.active)} active / ${n(d.slots)} · ${n(d.queued)} queued`)}
    ${row('speculative', d.spec_pct === null || d.spec_pct === undefined
        ? '<span class="dim">—</span>'
        : `${n(d.spec_pct)}% accepted${d.spec_type ? ' · ' + E(d.spec_type) : ''}${
            d.spec_nmax ? ' · n_max ' + n(d.spec_nmax) : ''}`)}
    ${row('cache hits', n(d.cache_hit))}
    ${row('decoded', n(d.decoded) + ' tok')}
    ${row('GPU power attributed', n(d.power_w, 1) + ' W')}
    ${d.metrics_off ? row('metrics endpoint', '<span class="warn">off</span>') : ''}
    ${d.slots_off ? row('slots endpoint', '<span class="warn">off</span>') : ''}
    ${d.tg_series && d.tg_series.length > 1
      ? spark(d.tg_series, 'var(--ok)') + `<div class="lbl"><span>generation t/s</span><b>${
          d.tg_stats && d.tg_stats.median !== null
            ? `median ${n(d.tg_stats.median,1)} · max ${n(d.tg_stats.max,1)}` : ''}</b></div>` : ''}
    ${d.pp_series && d.pp_series.length > 1
      ? spark(d.pp_series, 'var(--accent)') + `<div class="lbl"><span>prefill t/s</span><b>${
          d.pp_stats && d.pp_stats.median !== null
            ? `median ${n(d.pp_stats.median)} · max ${n(d.pp_stats.max)}` : ''}</b></div>` : ''}
    ${d.kv_series && d.kv_series.length > 1
      ? spark(d.kv_series, 'var(--warn)', 100) + `<div class="lbl"><span>KV %</span><b></b></div>` : ''}
    ${cfgBlock(d.config)}
    ${rest(d, shown)}
  </div>`;
}

function procTable(ps){
  if (!ps || !ps.length) return '<div class="empty">No llama.cpp processes detected.</div>';
  return `<div class="card"><div class="wrap"><table>
    <tr><th>pid</th><th>name</th><th>model</th><th>RSS MiB</th><th>VRAM MiB</th>
        <th>GTT</th><th>GPU</th><th>source</th><th>note</th></tr>` +
    ps.map(p => `<tr>
      <td class="num">${E(p.pid ?? '—')}</td><td>${E(p.name ?? '—')}</td>
      <td>${E(p.model || '—')}</td><td class="num">${n(p.rss)}</td>
      <td class="num">${n(p.vram)}</td><td class="num">${n(p.gtt)}</td>
      <td>${p.gpu_accel ? '<span class="ok">yes</span>' : '<span class="dim">no</span>'}</td>
      <td class="dim">${E(p.vram_src || '—')}</td>
      <td class="${p.gtt_note && p.gtt_note.includes('⚠') ? 'crit' : 'dim'}">${
        E(p.gtt_note || p.error || '')}</td></tr>`).join('') +
    `</table></div></div>`;
}

function render(s){
  $('host').textContent = s.host || '';
  $('pwr').textContent = n(s.power && s.power.total_w, 0) + ' W';
  $('wh').textContent = n(s.power && s.power.session_wh, 2) + ' Wh';
  $('up').textContent = dur(s.uptime_s);
  $('clock').textContent = s.time || '—';
  $('err').innerHTML = s.error
    ? `<div class="banner">collector error: ${E(s.error)}</div>` : '';

  const notes = (s.power && s.power.notes || []).length
    ? `<div class="card"><h3>Power notes</h3>${
        s.power.notes.map(x => `<div class="row"><span>${E(x)}</span><span></span></div>`).join('')}</div>` : '';

  $('body').innerHTML =
    `<h2>GPUs</h2><div class="grid">${(s.gpus || []).map(gpuCard).join('') ||
      '<div class="empty">No GPUs detected.</div>'}</div>` +
    `<h2>System</h2><div class="grid">${cpuCard(s.cpu || {})}${memCard(s.mem || {})}` +
    `<div class="card"><h3>Power</h3><div class="sub">whole box</div>
      ${row('total draw', n(s.power && s.power.total_w, 1) + ' W')}
      ${row('session energy', n(s.power && s.power.session_wh, 3) + ' Wh')}
      ${row('session length', dur(s.power && s.power.session_s))}
      ${spark(s.power && s.power.series, 'var(--warn)')}
      <div class="lbl"><span>watts, recent</span><b></b></div></div>${notes}</div>` +
    `<h2>llama.cpp servers</h2><div class="grid">${(s.llamas || []).map(srvCard).join('') ||
      '<div class="empty">No llama.cpp servers found.</div>'}</div>` +
    `<h2>Processes</h2>${procTable(s.procs)}` +
    `<h2>Raw snapshot</h2><details><summary>show full JSON (every collected field)</summary>
      <pre>${E(JSON.stringify(s, null, 2))}</pre></details>`;
}

let fails = 0;
async function tick(){
  try{
    const r = await fetch('api/metrics', {cache:'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    fails = 0;
    $('dot').className = 'dot live';
    if (!s.ready){
      $('conn').textContent = s.error ? 'collector failed' : (s.message || 'starting');
      $('dot').className = 'dot dead';
      if (s.error) $('err').innerHTML = `<div class="banner">${E(s.error)}</div>`;
      return;
    }
    $('conn').textContent = 'live';
    render(s);
  }catch(e){
    if (++fails > 2){
      $('dot').className = 'dot dead';
      $('conn').textContent = 'disconnected';
    }
  }
}
tick();
setInterval(tick, 1000);
</script></body></html>
"""


def main():
    global COLLECTOR, HTTP_PORT, BIND
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return
    llama_port = None
    interval = 1.0
    pos = [a for a in argv if a.isdigit()]
    if pos:
        HTTP_PORT = int(pos[0])
    for flag, cast in (("--bind", str), ("--llama-port", str), ("--interval", float)):
        if flag in argv:
            val = argv[argv.index(flag) + 1]
            if flag == "--bind":
                BIND = val
            elif flag == "--llama-port":
                llama_port = val
            else:
                interval = cast(val)

    COLLECTOR = Collector(llama_port=llama_port, interval=interval)
    COLLECTOR.start()

    srv = ThreadingHTTPServer((BIND, HTTP_PORT), Handler)
    srv.daemon_threads = True
    print(f"llamagputop dashboard on http://{BIND}:{HTTP_PORT}  "
          f"(interval {interval}s)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
