# llamagputop

A monitor for Linux machines that serve local LLMs. It finds every inference server on the box
(**llama.cpp**, **vLLM** and **radiance**) and every GPU, drive and power meter, and shows them
side by side. You see whether each server is answering, how fast it generates and prefills, how
full its KV cache is, how much of each prompt its cache saves, and what the hardware under it
is doing.

There are two front ends, and both read the same numbers:

- `dashboard.py`, a web dashboard with a JSON API, meant to run as a systemd service.
- `llamagputop.py`, a curses terminal UI, with one-shot and logging modes for scripts.

Each is a single file that uses only the Python standard library, so there is nothing to
install.

```text
 llamagputop · 3 GPUs · llama: Qwen3.8-Flash-Next                                 14:43:00
 ╭─ AMD · Navi 48 [Radeon AI PRO R9700] ──────────────────── PCIe 32 GT/s x8 · 31.5 GB/s ─╮
 │ util ▌░░░░░░░░░░░░░   3% │ avg 4% │ mem 0% │ media 0%                                  │
 │ vram ██████████████ 32519/32624 MiB │ edge 39°C │ junction 41°C │ mem 40°C             │
 │ vrm gfx 41°C │ vrm soc 39°C │ vrm mem 40°C │ core 41 MHz │ vmem 96 MHz │ fclk 582 MHz  │
 │ soc 417 MHz │ power 18/240 W │ vdd 619 mV │ fan 1200 rpm (24%)                         │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ disks ───────────────────────────────────────────────────────────────────── 2 drives ─╮
 │ WD_BLACK SN850X HS 2000G 1863 GiB │ r 0.0 w 0.0 MB/s │ 0 IOPS │ busy 0% │ 56°C         │
 │ Lexar SSD EQ790 2TB      1863 GiB │ r 0.0 w 0.0 MB/s │ 0 IOPS │ busy 0% │ 52°C         │
 │ /                      █████▊░░░░░░░░ 746/1830 GiB 43% │ on WD_BLACK SN850X HS 2000GB  │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯
 ╭─ radiance ─────────────────────────────────────────────────────────────────────────────╮
 │ status   idle   ctx 200000/request · pool 486464 tok · 8 seqs max   active 0, queued 0 │
 │ prefill — (idle) │ gen — (idle) │ session —/176.2 t/s avg                              │
 │ kv ░░░░░░░░░░░░░░ 0.0%  0/486464 tok │ prompt reuse 9.8% │ preempted 0                 │
 │ head mtp in container n-max 3 │ draft 57.7% accepted  2.73 tok/step                    │
 │ engine   1.0.8 │ step 15.5 ms decode │ experts 98.6% in VRAM │ pcie 0.00 GB/s h2d      │
 ╰────────────────────────────────────────────────────────────────────────────────────────╯

 q quit · ↑↓ PgUp/PgDn scroll · +/- rate 1s · z reset
```

*An excerpt from the terminal UI on a dual Radeon AI PRO R9700 box serving Qwen3.8-Flash-Next
on radiance. The full screen also has a summary strip, the second GPU, CPU, memory, power, the
server's launch flags and trend sparklines.*

## Quick start

```bash
git clone https://github.com/eniga/llamagputop.git
cd llamagputop
python3 dashboard.py              # web dashboard on http://0.0.0.0:7778
python3 llamagputop.py            # terminal UI
```

You need Linux and Python 3. There are no ports to configure: with no arguments, both front
ends find every GPU, drive and inference server themselves. To keep the dashboard running, see
[Run it as a service](#run-it-as-a-service).

The tool runs without any of the following, but it shows less. Each missing piece is reported
on screen, with a reason, in the place where its data would have been.

| Add | What you get |
|---|---|
| `--metrics` on llama-server | Prefill/decode counters, TTFT, prompt-cache share, latency and speculative stats. Without it those fields read "metrics off", not zero. |
| `sudo` or the server's user | Per-process VRAM for servers owned by another user. Containerised vLLM and radiance run as root. |
| RAPL read access | CPU package power. The TUI offers a one-off `sudo chmod` at startup; for a permanent fix see [CPU power](#cpu-power-rapl). |
| `nvidia-smi` | All NVIDIA card stats, per-process VRAM and dashboard PCIe throughput |
| `amd-smi` | Dashboard PCIe rx/tx on AMD, where the driver publishes it. On RDNA it reports N/A, and the dashboard says so. |
| `intel_gpu_top`, `nvtop` | Intel engine utilisation and VRAM |
| `lspci` / an up-to-date `pci.ids` | Product names. A new card such as the R9700 shows as "Device 7551" until `update-pciids` runs. |

## The dashboard

The page is laid out top to bottom in the order you usually need it:

1. **Header.** Host name, connection state, CPU and RAM use, whole-box power, session energy,
   and two toggles: **Basic / Advanced** and **light / dark**.
2. **Headline tiles.** The busiest server's decode and prefill rate, KV cache, requests, TTFT
   and draft acceptance, then average GPU utilisation, the hottest card and GPU power against
   the cap.
3. **Endpoints.** One row per server, described [below](#the-endpoints-table).
4. **Inference servers.** A panel per server (see [Inference engines](#inference-engines)).
5. **GPUs.** A card per GPU: utilisation with a sparkline, VRAM, power against the cap, a
   temperature bar and a chip for every sensor, clocks, and the PCIe link with live traffic.
6. **System.** CPU and memory, plus the disk and power panels in Advanced.
7. **Processes and raw JSON** (Advanced only).

**Basic** keeps the numbers that tell you whether serving works and how fast. **Advanced** adds
the diagnostic rows, the disk, power, process and raw-JSON sections, and each panel's launch
configuration and *all fields* list. Warnings, such as preemptions or an endpoint that is
switched off, show in both views. Switching only toggles CSS, so nothing repaints and open
sections stay open. The view and the theme are remembered per browser, and `?view=advanced`
opens a link in Advanced.

Every panel ends with an *all fields* section listing any collected key the panel didn't show,
so a new metric appears on the page without a dashboard change.

### The endpoints table

| Column | Big figure | Line under it |
|---|---|---|
| **Endpoint** | Health pill and `engine :port` | Model, and the phase while a request runs |
| **Type** | Engine | Engine version (radiance), running and waiting requests |
| **Resp. rate** | Answered requests ÷ (answered + failed), with a bar | Hover for answered, errors, rejected and client-cancelled counts |
| **Latency** | Average end-to-end seconds `/` peak | The bar fills to average ÷ peak; the marker is the last request |
| **Generation tok/s** | Live rate, or the last rate (dimmed) when idle | avg · max over this model's generating ticks |
| **Prefill tok/s** | Same, for prefill | avg · max over completed prefills |
| **Prompt-cache hit** | Cached share of the most recent prompts | Average since the server started |

The health pill reads `healthy`, `no metrics` (the server answers but exports no counters),
`no answer` (the probe is backing off after a timeout) or `offline`.

What each figure is built from depends on what the engine exports:

| | llama.cpp | vLLM | radiance |
|---|---|---|---|
| Response rate | None. llama.cpp exports no request outcomes. | `vllm:request_success_total` by `finished_reason`: `error` counts as a failure. A build without an `error` reason has no rate. | The same, plus `radiance:http_admission_rejected_total` as failures |
| Latency average | Prompt plus predicted seconds per finished request, over the requests this process watched (counted only when one slot was working). Queue time is not included. | Lifetime mean of `vllm:e2e_request_latency_seconds` | Same as vLLM |
| Latency peak | The slowest request seen | The slowest per-refresh mean. With one request finishing per refresh, that is the request itself. | Same as vLLM |
| Cache hit | `prompt_tokens_cached_total` against new prompt tokens | `vllm:prompt_tokens_cached_total` ÷ `vllm:prompt_tokens_total` | `radiance:attn_cached_tokens_total` ÷ `vllm:prompt_tokens_total` |

Client cancels (`finished_reason="abort"`, such as a stop button or a closed tab) are counted
and shown on hover, but they are not held against the server. When nothing measured a figure,
its cell shows a dash and hovering it says why.

## Inference engines

Servers are found by scanning `/proc` for their processes. The port comes from each server's
command line, each server gets its own panel, and a panel's speed history resets when its
model changes. Each server's API key is read from its own command line, so authenticated
servers need no setup.

| | llama.cpp | vLLM | radiance |
|---|---|---|---|
| Found by | `llama-server` process. Routers (`--models-dir`/`--models-preset`) are skipped and their per-model children probed. | `vllm` or a python process running `vllm serve` | `radiance` process (its `docker-init` wrapper is skipped) |
| Reads | `/metrics`, `/slots`, `/props` | `/metrics` (no auth), `/v1/models` | `/metrics`, `/stats`, `/v1/models` (all need the key) |
| Live decode | Slope of the busy slots' progress in `/slots`, because `/metrics` moves only when a request ends | Slope of `vllm:generation_tokens_total` | Slope of `radiance:decode_tokens_total`, because the vLLM-compatible counter moves only when a request ends |
| Prefill | Live, then `last` | The last completed request, from histograms. The prompt counter jumps once per request, so no live rate exists. | Live from the engine's gauge during prefill, then `last`. The gauge isn't read while idle, because it decays. |
| KV cache | Summed over every slot | Fraction of the shared pool, in tokens | Pool and use from `/stats` |
| Context line | `ctx N/slot · total (k slots)` | `ctx N/request · pool P tok · S seqs max` | Same as vLLM, with ctx from `--max-model-len` |
| Speculative | Draft head, acceptance, tokens/step, per position | Method and head from `--speculative-config`, per-position acceptance | Adds expected tokens per drafting step, computed from per-position acceptance |
| Extras | TTFT from server counters, slot occupancy | Runs, prefix-cache reuse, ingest, preemptions, reasoning parser, TTFT/TPOT/e2e | Decode step time, experts held in VRAM with promotions/demotions, engine PCIe traffic, KV held for the next turn, linear-state reuse, version, uptime |

vLLM and radiance panels are organised around **runs**: a run is a stretch of refreshes with work
in it. A run panel shows decode peak, average, duration and tokens for the live or last run, and
prefill for the requests completed during it.

Rules shared by all three engines:

- **A speed is live only while a request runs.** When it finishes, the panel keeps the rate it
  ran at and labels it `last`.
- **Counters come from the server, not from this tool's timing.** Timing by polling would add
  the refresh interval as error. When several requests finish inside one refresh, TTFT becomes
  a mean or a summed prefill time, and the label says which.
- **The configuration panel shows every launch flag**, grouped by role (loading, parallel,
  memory, scheduling, prefix cache, speculative, sampling, server). Nothing is picked from a
  fixed list, so flags added by newer engine versions appear without code changes. API keys are
  masked. Sampler values are labelled as server defaults, since a request's own settings
  override them.
- **On vLLM and radiance, judge fullness from the KV bar.** These engines reserve their KV pool
  at startup, so VRAM sits near 100% even when nothing runs. That memory really is unavailable
  to anything else, so the VRAM bar counts it as used.
- **Each probe runs on its own thread.** A server that stops answering doesn't slow the others.
  After a timeout the probe backs off and the panel says so.

To reach a server that discovery can't see:

```bash
LLAMAGPUTOP_VLLM=host:port[,host:port]   # vLLM elsewhere, or a container whose host port differs from its internal one
LLAMAGPUTOP_API_KEY=...                   # key for a server whose command line can't be read
```

## Hardware

**GPUs.** DRM cards are enumerated from sysfs and read through each vendor's own interfaces:

- **AMD:** `amdgpu` sysfs, hwmon and the binary `gpu_metrics` blob (v1.3, decoded field by
  field). This gives utilisation and memory-controller and media activity, VRAM and GTT, the edge,
  junction and memory temperatures, all three VRM temperatures (gfx, SoC and memory), the gfx,
  memory, fabric and SoC clocks, core voltage, fan rpm as a share of `fan1_max`, throttle
  state, and power against the cap. Power is sampled at 10 Hz on a background thread and
  averaged over each refresh, because a single `power1_average` read only shows one ~50 ms
  window.
- **NVIDIA:** a persistent `nvidia-smi` feed, including per-process VRAM. The NVIDIA driver
  doesn't put VRAM in `fdinfo`.
- **Intel:** i915/xe sysfs, `intel_gpu_top` for engine utilisation and `nvtop` for VRAM. An
  integrated card is detected by measurement, not by name. A card that reports a total equal to
  `MemTotal` shares system RAM and gets no VRAM bar; a discrete Arc keeps its bar.

**Temperatures** fade from green through amber to red on one scale per sensor: green up to
60 °C, amber at 80 °C, red from 90 °C. A sensor that declares a lower critical limit (drives,
mostly) has its scale pulled down so that red starts 5 °C below that limit. The headline GPU
temperature names its sensor, because junction runs about 25 °C above the edge sensor that
other tools show.

**PCIe.** The link shown is the one at the root port the card hangs from, because a card's own
node can describe a switch on the card itself. On an R9700 that node reads 32 GT/s x16 while
the slot runs x8. If the root port is QEMU's emulated one, the panel marks it virtual, drops
the "narrowed link" warning, and shows the width the card's firmware reports in `gpu_metrics`.
Speeds are shown as negotiated and maximum, and converted to GB/s net of encoding overhead.

**CPU, memory and power.** CPU utilisation, per-core temperatures, frequency, load average and
RAPL package power. Memory broken down into cached, buffers, dirty, committed and swap, with
zram and disk swap shown separately. The power panel adds up every readable meter and tracks
session energy in Wh.

**Disks.** Each physical drive shows read/write MB/s, IOPS, its busy share of the last refresh,
and its temperature against its declared limit (the NVMe controller sensor, or `drivetemp` for
SATA). Each real filesystem shows its used space as `df` computes it, plus the drive it really
lives on. That drive is found by walking partitions and device-mapper slaves, so an LVM volume
on a thin pool still names the NVMe underneath. Partitions and dm/md layers aren't listed as
drives, so no I/O is counted twice. tmpfs, overlay and bind mounts are skipped, and network
filesystems are marked as such.

**Processes.** Every inference process with its RSS, VRAM and GTT. On AMD and Intel the
kernel's `fdinfo` separates device memory from host spill. On NVIDIA they can't be separated,
so spill shows as unknown, not as zero.

## Terminal UI

```text
llamagputop.py [PORT] [--once | --line | --probe]

  PORT      focus on one server's port. Omit it to show every server.
  --once    print one status line and exit       (scripts)
  --line    print a status line every refresh    (logging)
  --probe   dump the detected hardware and servers, then exit
```

Keys: `q` quits, `↑↓ PgUp PgDn Home End` scroll, `+`/`-` change the refresh rate, `z` resets
history. `NO_COLOR` is honoured. Temperatures use a seven-step 256-colour ramp, and fall back to
green/yellow/red on the same scale on terminals with fewer colours.

The **trend** panel draws strip charts, one column per refresh with the newest on the right.
The scale fits the range the data occupies and snaps to a round step. Each row shows its
session peak, and a row whose scale doesn't start at zero shows its floor. A dot always means a
true zero. On a generative server the KV row keeps its value while the server is idle, because
an idle slot still holds its conversation.

The endpoints table is in the dashboard only.

### CPU power (RAPL)

Most kernels let only root read the RAPL energy counter, as a side-channel mitigation. To make
it readable permanently, add a udev rule:

```bash
echo 'SUBSYSTEM=="powercap", KERNEL=="intel-rapl:*", RUN+="/bin/chmod a+r /sys%p/energy_uj"' \
  | sudo tee /etc/udev/rules.d/99-rapl.rules
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=powercap
```

## Web dashboard reference

```text
python3 dashboard.py [PORT] [--bind ADDR] [--llama-port N] [--interval S]
```

The defaults are `0.0.0.0:7778` with a 1 s refresh.

| Path | Serves |
|---|---|
| `/` | The page. It polls once a second, with no push, so it works behind buffering proxies. |
| `/api/metrics` | The whole snapshot as JSON: `gpus`, `cpu`, `mem`, `disks`, `power`, `llamas` (one entry per server, every field), `endpoints` (the table rows), `procs`, plus `host`, `ts` and `uptime_s`. A missing reading is `null`, never `0`. |
| `/healthz` | 200 once the first sample is in. Use it for proxy or container health checks. |
| `/favicon.svg` | Tab icon. `/favicon.ico` gets a 204. |

### Run it as a service

The unit file in this repo names no user and no paths. The command below adds `User=`,
`Group=`, `WorkingDirectory=` and `ExecStart=` for whoever runs it. Run it from inside the
checkout as your normal user, without `sudo` in front of the whole block, or the unit's user
becomes root, which the dashboard doesn't need.

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

After a `git pull`, run `sudo systemctl restart llamagputop-dashboard`. The unit's
`After=llama-server.service` only sets ordering, not a dependency: the dashboard is useful with
no server running and shouldn't stop when a server does. If the checkout is on a separate mount,
add `RequiresMountsFor=`.

**Exposure.** The dashboard can't start or stop anything, and it masks API keys. It does show
model paths, ports, PIDs and your hardware inventory, so put authentication in front of it
before exposing it beyond your LAN. Behind a reverse proxy, give it its own subdomain, not a
subpath, because the page loads its assets from `/`.

## Design

**What the tool promises**

- **Read at the source.** Data comes from `/sys`, `/proc`, the driver and each server's own
  counters. Vendor tools are used only when installed.
- **`None` and `0` are different facts.** An idle GPU shows 0%. A card with no readable power
  meter shows `—`. A parked fan reads "stopped", not "no tachometer". An engine that exports no
  failure counter has no response rate, not 100%. The distinction is kept all the way into the
  JSON.
- **Every dash explains itself.** When the fix is a program, the reason names the binary
  ("needs nvidia-smi"), because package names differ between distributions. When the data
  comes from sysfs, it names the missing attribute ("gpu_busy_percent not exposed by this
  driver").
- **No sensor is trusted blindly.** Readings outside a sane range are dropped and the last good
  value is kept.
- **Numbers are measured, not looked up.** For example, there is no peak VRAM bandwidth: it
  needs the memory bus width, which no driver exposes, and a table of product names would be
  exactly the kind of invented number this project avoids.

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
 │ _collect()  ← the one collection seam, records the history     │
 │ endpoint_row()  one server sample → one endpoints-table row    │
 └───────────────┬────────────────────────────────┬───────────────┘
                 │                                │
       curses TUI / --once / --line         dashboard.py (imports it)
                                            Collector thread → build_snapshot()
                                            PcieFeed (nvidia-smi / amd-smi / intel_gpu_top)
                                            ThreadingHTTPServer → / , /api/metrics
```

The dashboard has no collection code of its own. It imports `llamagputop.py`, calls the same
`_collect()` as the TUI, and reuses its helpers for context text, "unavailable because"
reasons, medians, extremes and endpoint rows, so the browser and the terminal always agree. The
only thing the dashboard reads itself is PCIe throughput, because that needs a long-running
vendor process.

The probe classes follow the engines. `VllmProbe` extends `LlamaProbe`, and `RadianceProbe`
extends `VllmProbe`, because radiance exports vLLM's metric names and histograms. Each subclass
overrides only what its engine measures differently, down to a class attribute naming a
counter (`_GEN_COUNTER`, `_CACHED_COUNTER`, `_REJECT_COUNTERS`).

## Tests

```bash
python3 -m unittest discover -s tests
```

There are 169 tests. They use only the standard library and need no particular hardware or
network: every reader runs against a temporary directory standing in for sysfs, or against a
stub feed or scripted HTTP responses with a fake clock. Most come in pairs: one proves that a
missing reading shows as a dash, and the other proves that a real zero (a parked fan, an idle
server, a power-gated GPU, a server with no errors) still shows as zero. The Intel reader tests
read `/proc/meminfo`, so run the suite on Linux.

## Lineage

llamagputop began as [XscannedX/llamagputop](https://github.com/XscannedX/llamagputop), a
terminal monitor for GPUs and llama.cpp, released as 1.0.7.0 in August 2026. This fork keeps
its principles (one file, no dependencies, everything read at its source, a dash with a reason
in place of a made-up number) and has since grown well past it:

| Area | Upstream 1.0.7.0 | Now |
|---|---|---|
| Inference engines | llama.cpp | llama.cpp, vLLM and radiance, each with its own probe and panel; llama.cpp routers are skipped |
| Front ends | Terminal UI | Terminal UI, plus a web dashboard with an endpoints table, headline tiles, Basic/Advanced and light/dark views, a JSON API and a systemd unit |
| Authentication | None | Each server's API key read from its command line, or from `LLAMAGPUTOP_API_KEY` |
| AMD | One power read per refresh; edge/junction/mem, VRM gfx/soc | 10 Hz power averaging, memory regulator temperature, labelled headline sensor, fan % of `fan1_max` |
| PCIe | Root-port link in the TUI | The same link in both views, virtual root ports flagged, firmware link width, live rx/tx |
| Storage | None | Per-drive I/O, IOPS, busy % and temperature, with filesystems mapped to their drives |
| Temperatures | Three fixed colours | A continuous green → amber → red scale per sensor |
| Tests | 76 | 169 |

Every change is listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT. The original work is © 2026 XscannedX (see [LICENSE](LICENSE)). This fork's changes are
released under the same license.
