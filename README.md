# llamagputop

A monitor for Linux machines that serve local LLMs. It shows the GPUs, CPU, memory, disks and
power draw beside every inference server running on the box: **llama.cpp**, **vLLM** and
**radiance**. Each server gets its own panel with prefill and decode speed, KV cache fill,
speculative-decoding acceptance and the flags it was launched with.

It has two front ends that read the same numbers:

- `llamagputop.py`, a curses terminal UI that also has one-shot and logging modes for scripts.
- `dashboard.py`, a web dashboard with a JSON API, meant to run as a systemd service.

Both are single files that use only the Python standard library, so there is nothing to install.

```text
 llamagputop · 3 GPUs · llama: Qwen3.8-Flash-Next                                 14:43:00
 ╭─ AMD · Navi 48 [Radeon AI PRO R9700] ──────────────────── PCIe 32 GT/s x8 · 31.5 GB/s ─╮
 │ util ▌░░░░░░░░░░░░░   3% │ avg 4% │ mem 0% │ media 0%                                  │
 │ vram ██████████████ 32519/32624 MiB │ edge 39°C │ junction 41°C │ mem 40°C             │
 │ vrm gfx 41°C │ vrm soc 39°C │ vrm mem 40°C │ core 41 MHz │ vmem 96 MHz │ fclk 582 MHz  │
 │ soc 417 MHz │ power 18/240 W │ vdd 619 mV │ fan 1200 rpm (24%)                         │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ Intel · Arrow Lake-S [Intel Graphics] ────────────────────────────────────────────────╮
 │ util ░░░░░░░░░░░░░░ — │ core 0 MHz │ range 550–2000 eff 550 MHz                        │
 │ no data util — needs intel_gpu_top │ vram — not reported by nvtop for this card        │
 │         temp, power — no hwmon node on this card                                       │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ disks ───────────────────────────────────────────────────────────────────── 2 drives ─╮
 │ WD_BLACK SN850X HS 2000G 1863 GiB │ r 0.0 w 0.0 MB/s │ 0 IOPS │ busy 0% │ 56°C         │
 │ Lexar SSD EQ790 2TB      1863 GiB │ r 0.0 w 0.0 MB/s │ 0 IOPS │ busy 0% │ 52°C         │
 │ /                      █████▊░░░░░░░░ 746/1830 GiB 43% │ on WD_BLACK SN850X HS 2000GB  │
 │ /mnt/radiance-state    ███▉░░░░░░░░░░ 53/196 GiB 29% │ on Lexar SSD EQ790 2TB          │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ radiance ─────────────────────────────────────────────────────────────────────────────╮
 │ status   idle   ctx 200000/request · pool 486464 tok · 8 seqs max   active 0, queued 0 │
 │ prefill — (idle) │ gen — (idle) │ session —/176.2 t/s avg                              │
 │ kv ░░░░░░░░░░░░░░ 0.0%  0/486464 tok │ prompt reuse 9.8% │ preempted 0                 │
 │ head mtp in container n-max 3 │ draft 57.7% accepted  2.73 tok/step                    │
 │ per pos  74% 76% 78%                                                                   │
 │ engine   1.0.8 │ step 15.5 ms decode │ experts 98.6% in VRAM │ pcie 0.00 GB/s h2d      │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ llama processes ──────────────────────────────────────────────────────────────────────╮
 │  453408 Qwen3.8-Flash-Next         RSS 30673M   VRAM 32376M   GTT 8M                   │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯

 q quit · ↑↓ PgUp/PgDn scroll · +/- rate 1s · z reset
```

*An excerpt from a live frame on a dual Radeon AI PRO R9700 box serving Qwen3.8-Flash-Next on
radiance. The full screen also has a summary strip, the second GPU, CPU, memory, power, the
server's launch config and trend sparklines.*

## Where this came from

This is a fork of [XscannedX/llamagputop](https://github.com/XscannedX/llamagputop), which
released version 1.0.7.0 in August 2026 as a terminal monitor for GPUs plus llama.cpp. The fork
has kept that project's principles: one file, no dependencies, everything read at its source,
and a dash with a reason in place of a made-up number. Beyond that it has grown in several
directions:

| Area | Upstream (1.0.7.0) | This fork |
|---|---|---|
| Inference engines | llama.cpp | llama.cpp, vLLM and radiance, each with its own probe and panel. llama.cpp routers are skipped. |
| Front ends | Terminal UI | Terminal UI, plus a web dashboard with a JSON API and a systemd unit |
| Authentication | None | Each server's API key is read from its own command line, or from `LLAMAGPUTOP_API_KEY` |
| AMD power | One `power1_average` read per refresh | Sampled at 10 Hz on a background thread and averaged over each refresh |
| AMD sensors | Edge/junction/mem, VRM gfx/soc | Adds the memory regulator (`temperature_vrmem`), labels the headline sensor and gives fan % of `fan1_max` |
| PCIe | Root-port link in the TUI | The same link in both views, flagged when that port is a virtual VM one, plus the width the card's firmware reports and live rx/tx in the dashboard |
| Storage | — | Disk panel: per-drive I/O, IOPS, busy % and temperature, with each filesystem mapped to its physical drive |
| Tests | 76 | 156, still standard library only, with no hardware or network needed |

Everything is listed change by change in [CHANGELOG.md](CHANGELOG.md).

## Quick start

```bash
git clone https://github.com/eniga/llamagputop.git
cd llamagputop
python3 llamagputop.py            # terminal UI, finds every GPU and server on its own
python3 dashboard.py              # web dashboard on http://0.0.0.0:7778
```

You need Linux and Python 3. There are no pip packages. With no arguments the tool finds every
GPU, every disk and every inference server, so there are no ports to configure.

### What improves the picture

The tool runs without any of the following, but it shows less. Each missing piece is reported
on screen, with a reason, in the place where its data would have been.

| Add | What you get |
|---|---|
| `--metrics` on llama-server | Prefill/decode counters, TTFT and speculative stats. Without it, those fields read "metrics off", not zero. |
| `sudo` or matching user | Per-process VRAM for servers owned by another user. Containerised vLLM and radiance run as root. |
| RAPL read access | CPU package power. The TUI offers a one-off `sudo chmod` at startup. For a permanent fix, see [CPU power](#cpu-power-rapl). |
| `nvidia-smi` | All NVIDIA card stats, per-process VRAM and dashboard PCIe throughput |
| `amd-smi` | Dashboard PCIe rx/tx on AMD, where the driver publishes it. On RDNA it reports N/A, and the dashboard says so. |
| `intel_gpu_top`, `nvtop` | Intel engine utilisation and VRAM |
| `lspci` / up-to-date `pci.ids` | Product names. A new card such as the R9700 shows as "Device 7551" until `update-pciids` runs. |

## Inference engines

Servers are found by scanning `/proc` for their processes. The port comes from each server's
command line, every server gets its own panel, and the panels reset when the model changes.

| | llama.cpp | vLLM | radiance |
|---|---|---|---|
| Found by | `llama-server` process. Routers (`--models-dir`/`--models-preset`) are skipped, and their per-model children are probed. | `vllm` / python process running `vllm serve` | `radiance` process (its `docker-init` wrapper is skipped) |
| Endpoints | `/metrics`, `/slots`, `/props` | `/metrics` (no auth), `/v1/models` | `/metrics`, `/stats`, `/v1/models` (all need the key) |
| Live decode | Slope of the busy slots' progress in `/slots`, because `/metrics` only moves when a request ends | Slope of `vllm:generation_tokens_total` | Slope of `radiance:decode_tokens_total`, because the vLLM-compatible counter only moves when a request ends |
| Prefill | Live, then `last` | The last completed request, from histograms. The prompt counter jumps once per request, so no live rate exists. | Live from the engine's gauge during prefill, then `last`. The gauge isn't read while idle because it decays. |
| KV cache | Summed over every slot | Fraction of the shared page pool, in tokens | Same as vLLM |
| Context line | `ctx N/slot · total (k slots)` | `ctx N/request · pool P tok · S seqs max` | Same as vLLM, with ctx taken from `--max-model-len` |
| Speculative | Draft head, acceptance, tokens/step, per position | Method/head from `--speculative-config`, per-position acceptance | Adds expected tokens per drafting step, computed from per-position acceptance |
| Extras | TTFT from server counters, slot occupancy | Prefix-cache reuse, preemptions, reasoning parser, TTFT/TPOT/e2e | Decode step time, experts held in VRAM with promotions/demotions, engine PCIe traffic, KV held for the next turn, linear-state reuse, version, uptime |

Rules shared by all three engines:

- **A speed is live only while a request is running.** When the request finishes, the panel
  keeps the rate it ran at and labels it `last`.
- **Counters come from the server, not from this tool's own timing.** Timing by polling would
  add the refresh interval as an error. When several requests finish inside one refresh, TTFT
  becomes a mean or a summed prefill time, and the label says which.
- **The server config panel shows every launch flag**, grouped by role (loading, parallel,
  memory, scheduling, prefix cache, speculative, sampling, server). Nothing is picked out of a
  fixed list, so flags added by newer engine versions appear without code changes. API keys are
  masked. Sampler values are labelled as server defaults, since a request's own settings
  override them.
- **On vLLM and radiance, read the KV bar to judge how full the server is.** These engines
  reserve their KV pool at startup, so the VRAM bar sits near 100% even when nothing is
  running. That memory really is unavailable to anything else, and the VRAM bar counts it as
  used.
- **Each probe runs on its own thread.** A server that stops answering doesn't slow the screen.
  After a timeout the probe backs off, and the panel shows that it is doing so.

To reach a server that discovery can't see, use these variables:

```bash
LLAMAGPUTOP_VLLM=host:port[,host:port]   # vLLM on another machine, or a container whose host port differs from its internal one
LLAMAGPUTOP_API_KEY=...                   # key for a server whose command line can't be read
```

## Hardware

**GPUs.** DRM cards are enumerated from sysfs and read through the vendor's own interfaces:

- **AMD:** `amdgpu` sysfs, hwmon and the binary `gpu_metrics` blob (v1.3 decoded field by
  field). You get utilisation and memory-controller/media activity, VRAM and GTT, the edge,
  junction and memory temperatures, all three VRM temperatures, the gfx, memory, fabric and SoC
  clocks, core voltage, fan rpm with % of `fan1_max`, the throttle state, and power against the
  cap.
- **NVIDIA:** a persistent `nvidia-smi` feed, including per-process VRAM. The NVIDIA driver
  doesn't put VRAM in `fdinfo`.
- **Intel:** i915/xe sysfs, `intel_gpu_top` for engine utilisation and `nvtop` for VRAM.
  Whether a card is integrated is measured, not guessed from its name: when the card reports a
  total equal to `MemTotal`, it shares system RAM and gets no VRAM bar, while a discrete Arc
  keeps its bar.

**PCIe.** The link shown is the one at the root port the card hangs from. A card's own node can
describe a switch on the card itself; on an R9700 that node reads 32 GT/s x16 while the slot
runs x8. If the root port is QEMU's emulated one, the panel marks it as virtual, drops the
"narrowed link" warning and shows the width that the card's firmware reports from
`gpu_metrics`. Link speed is shown as negotiated and maximum, converted to GB/s with the
encoding overhead taken out.

**CPU, memory and power.** The tool reads CPU utilisation, per-core temperatures, frequency,
load average and RAPL package power. Memory is broken down into cached, buffers, dirty,
committed and swap, with zram and disk swap shown separately. The power panel adds up every
readable meter and tracks session energy in Wh.

**Disks.** Each physical drive shows its read/write MB/s, IOPS, busy share of the last refresh
and its temperature against the limit it declares (the NVMe controller sensor, or `drivetemp`
for SATA). Each real filesystem shows its used space as `df` computes it, plus the drive it
actually lives on. That drive is found by walking partitions and device-mapper slaves, so an
LVM volume on a thin pool still names the NVMe underneath it. Partitions and dm/md layers are
not listed as drives, so no I/O is counted twice. tmpfs, overlay and bind mounts are skipped.
Network filesystems are marked as network.

**Processes.** Every inference process is listed with its RSS, VRAM and GTT. On AMD and Intel,
the kernel's `fdinfo` separates device memory from host spill. On NVIDIA it can't be split, so
spill shows as unknown, not as zero.

## Design

**What the tool promises**

- **Read at the source.** Data comes from `/sys`, `/proc`, the driver and the server's own
  counters. Optional vendor tools are used only when they are installed.
- **`None` and `0` are different facts.** An idle GPU shows 0%. A card with no readable power
  meter shows `—`. A parked fan reads "stopped", not "no tachometer". This distinction is kept
  through to the dashboard's JSON.
- **Every dash explains itself.** When the fix is a program, the reason names the binary, as in
  "needs nvidia-smi", because package names differ between distributions. On AMD, where the
  data comes from sysfs, the reason names the missing attribute instead, as in
  "gpu_busy_percent not exposed by this driver".
- **No sensor is trusted blindly.** Readings outside a sane range are dropped, and the last good
  value is kept.
- **Numbers are measured, not looked up.** For example, the tool shows no peak VRAM bandwidth.
  That figure needs the memory bus width, which no driver exposes, and deriving it from a table
  of product names would be exactly the kind of invented number this project avoids.

**How it fits together**

```text
                       llamagputop.py
 ┌────────────────────────────────────────────────────────────────┐
 │ discover_gpus()  AmdGpu / IntelGpu / NvidiaGpu .sample()       │
 │   background feeds: _AmdPowerFeed (10 Hz), _NvidiaFeed,        │
 │   _IntelEngineFeed, _NvtopVramFeed                             │
 │ cpu_sample() mem_sample() disk_sample() llama_processes()      │
 │ _LlamaFeed thread → sample_llama_fleet()                       │
 │   discover_llama_servers() → LlamaProbe                        │
 │                              └─ VllmProbe ─ RadianceProbe      │
 │ _collect()  ← the one collection seam                          │
 └───────────────┬────────────────────────────────┬───────────────┘
                 │                                │
       curses TUI / --once / --line         dashboard.py (imports it)
                                            Collector thread → build_snapshot()
                                            PcieFeed (nvidia-smi / amd-smi / intel_gpu_top)
                                            ThreadingHTTPServer → / , /api/metrics
```

The web dashboard has no collection code of its own. It imports `llamagputop.py` and calls the
same `_collect()` as the TUI, and it reuses the TUI's helpers for context text, for
"unavailable because" reasons, and for medians and extremes. The browser and the terminal
therefore always agree. The only thing the dashboard reads for itself is PCIe throughput,
because that needs a long-running vendor process.

The class hierarchy follows the engines. `VllmProbe` extends `LlamaProbe`, and `RadianceProbe`
extends `VllmProbe`, because radiance exports vLLM's metric names and histograms. Each subclass
overrides only the parts where its engine measures differently.

## Terminal UI

```text
llamagputop.py [PORT] [--once | --line | --probe]

  PORT      focus one server's port. Omit it to show every server.
  --once    print one status line and exit       (scripts)
  --line    print a status line every refresh    (logging)
  --probe   dump the detected hardware and servers, then exit
```

Keys: `q` quits, `↑↓ PgUp PgDn Home End` scroll, `+`/`-` change the refresh rate, and `z`
resets history. The tool honours `NO_COLOR`.

The **trend** panel draws strip charts with one column per refresh and the newest on the right.
The scale fits the range the data actually occupies and snaps to a round step. Each row shows
its session peak, and a row whose scale doesn't start at zero shows its floor. A dot always
means a true zero. On a generative server the KV row keeps its value while the server is idle,
because an idle slot still holds its conversation.

### CPU power (RAPL)

Most kernels let only root read the RAPL energy counter, as a side-channel mitigation. To make
it readable for good, add a udev rule:

```bash
echo 'SUBSYSTEM=="powercap", KERNEL=="intel-rapl:*", RUN+="/bin/chmod a+r /sys%p/energy_uj"' \
  | sudo tee /etc/udev/rules.d/99-rapl.rules
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=powercap
```

## Web dashboard

```text
python3 dashboard.py [PORT] [--bind ADDR] [--llama-port N] [--interval S]
```

The defaults are `0.0.0.0:7778` with a 1 s refresh.

| Path | Serves |
|---|---|
| `/` | The dashboard page. It polls once a second, with no push, so it works behind buffering proxies. |
| `/api/metrics` | The whole snapshot as JSON: `gpus`, `cpu`, `mem`, `disks`, `power`, `llamas`, `procs`, plus `host`, `ts` and `uptime_s` |
| `/healthz` | 200 once the first sample is in. Use it for proxy or container health checks. |
| `/favicon.svg` | Tab icon. `/favicon.ico` gets a 204. |

A row of headline tiles opens the page: the busiest server's decode and prefill rate, KV
cache, requests, TTFT and draft acceptance, then GPU utilisation, the hottest card and GPU power
against the cap. The header carries CPU and RAM use, box power and session energy, and a
**Basic / Advanced** toggle. Basic keeps the numbers that say whether serving works and how fast;
Advanced adds every diagnostic row below, the disk, power, process and raw-JSON sections, and
each panel's configuration and *all fields*. The choice is remembered per browser, and
`?view=advanced` opens a link in Advanced. What follows describes the Advanced view.

The page puts the inference panels first, then a card for each GPU. A GPU card shows its
utilisation sparkline with min/avg/peak, VRAM, power against the cap, every temperature
sensor, the clocks, the PCIe link with live rx/tx and the narrowest hop in the chain, and the
power used by the cards that a given server runs on. CPU, memory and disks follow. vLLM and
radiance panels are organised around **runs**, meaning the stretch from a run's first request
to its last. A run panel shows decode peak, average, duration and tokens for the live or last
run, prefill for the requests completed in it, prompt-cache reuse and ingest rate, and lifetime
TTFT, TPOT and end-to-end latency. Every panel ends with an *all fields* section that lists any
collector key the panel didn't already show, so a new metric appears in the web view without
any dashboard change. The raw JSON is at the bottom of the page.

### Run it as a service

The unit file in this repo has no user or paths in it. The command below adds `User=`,
`Group=`, `WorkingDirectory=` and `ExecStart=` for whoever runs it. Run it from inside the
checkout as your normal user, without `sudo` in front of the whole block. Otherwise the unit's
user becomes root, which the dashboard doesn't need.

```bash
cd /path/to/llamagputop
{
  cat llamagputop-dashboard.service
  echo "User=$(id -un)"
  echo "Group=$(id -gn)"
  echo "WorkingDirectory=$PWD"
  echo "ExecStart=$(command -v python3) $PWD/dashboard.py 7778"
} | sudo tee /etc/systemd/system/llamagputop-dashboard.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now llamagputop-dashboard
journalctl -u llamagputop-dashboard -f
```

`After=llama-server.service` sets ordering only, not a dependency. The dashboard is still useful
with no server running, and it shouldn't stop when a server stops. If the checkout is on a
separate mount, add `RequiresMountsFor=`.

**Exposure.** The dashboard can't start or stop anything, and it masks API keys. It does show
model paths, ports, PIDs and your hardware inventory, so put authentication in front of it
before exposing it beyond your LAN. Behind a reverse proxy, give it its own subdomain rather
than a subpath, because the page loads its assets from `/`.

## Tests

```bash
python3 -m unittest discover -s tests
```

There are 156 tests. They use only the standard library and need no particular hardware or
network: every reader runs against a temporary directory that stands in for sysfs, or against a
stub feed or HTTP response. Most tests come in pairs, one proving that a missing reading shows
as a dash and one proving that a real zero (a parked fan, an idle server, a power-gated GPU)
still shows as zero.

## License

MIT. The original work is © 2026 XscannedX (see [LICENSE](LICENSE)). The changes made in this
fork are released under the same license.
