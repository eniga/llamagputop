llamagputop

This is a terminal monitor for Linux that tracks your GPUs and llama.cpp inference in a single Python file. It doesn't need any external dependencies beyond the standard library.
In order to work correctly and completely --metrics must be activated on your llama.cpp servers, chmod 755 and sudo will help with all the remaining metrics. Everything can work anyway (but with less data). 

```text
 llamagputop · 2 GPUs · llama: 5 servers                     14:23:07 

╭─ Intel · TigerLake-H GT1 [UHD Graphics] ──────────────────────────╮
│ util █▊░░░░░░░░░░░░  12% │ avg 9% │ core 350 MHz                   │
│ range 350–1450 eff 600 MHz                                         │
│ no data vram — memory is shared with system RAM                    │
│         temp, power — no hwmon node on this card                   │
╰───────────────────────────────────────────────────────────────────╯
╭─ NVIDIA · GA106M [GeForce RTX 3060 Mobile / Max-Q] ───────────────╮
│ util ████████▋░░░░░  62% │ avg 41% │ mem 18%                       │
│ vram ██████████▎░░░ 4512/6144 MiB │ gpu 68.0°C │ core 1425.0 MHz   │
│ vmem 7000.0 MHz │ power 85/115 W                                   │
╰───────────────────────────────────────────────────────────────────╯
╭─ llama processes ─────────────────────────────────────────────────╮
│ 1738957 GigaChat3.1-10B-A1.8B-q4_K RSS 6393M   VRAM — (CPU only)  │
│ 1730176 Ling-3.0-tiny-MXFP4_MOE    RSS 4209M   VRAM — (CPU only)  │
│ 1730591 gemma4-e4b-sft-claude-opus RSS 3542M   VRAM — (CPU only)  │
│ 1701115 bge-m3-clean-random-Q6_K   RSS 1272M   VRAM — (CPU only)  │
│ 1737341 bge-reranker-v2-m3-Q6_K    RSS  752M   VRAM — (CPU only)  │
╰───────────────────────────────────────────────────────────────────╯
╭─ power ───────────────────────────────────────────────────────────╮
│ NVIDIA 85/115 W │ CPU 45 W                                         │
│ total 130 W  avg 118 W  peak 180 W                                 │
│ session 1h04m  energy used 126.1 Wh                                │
╰───────────────────────────────────────────────────────────────────╯
╭─ llama.cpp (1738957: 8080) ───────────────────────────────────────╮
│ status  generating  ctx 5120/slot · 10240 total (2 slots)  active 1 │
│ prefill 24 t/s last │ gen 28.7 t/s (med 28.7±0.3)                  │
│ kv ▎░░░░░░░░░░░  1.1%  115/10240 tok │ budget 107/150              │
╰───────────────────────────────────────────────────────────────────╯
╭─ llama.cpp (1730176: 8081) ───────────────────────────────────────╮
│ status    idle         ctx 8192     active 0, queued 0             │
╰───────────────────────────────────────────────────────────────────╯
╭─ trends ──────────────────────────────────────────────────────────╮
│ util %  ▂▃▃▄▅▆▇██████████████████████████████████████████████████ │
│ gen t/s ░░░░░░░░░░░▂▂▃▄▅▆▇███████████████████████████████████████ │
╰───────────────────────────────────────────────────────────────────╯
```

It watches every GPU in your machine including AMD, Intel, and NVIDIA, along with your CPU, RAM, and power draw. What sets this tool apart from others is the llama.cpp panel. It automatically detects every running llama server and shows you the metrics that actually matter when serving a model. You can see your prefill and generation speed with live medians and standard deviations. A speed is shown live only while a request is actually running; once it finishes, the rate it ran at is kept and marked "last" rather than left standing as if it were the current one. Start your server with --metrics and the completed-request figures come from the server's own counters, which is the exact number it timed itself. The time to first token is read from those same counters rather than clocked here, because the server knows it exactly while polling would carry the refresh interval as its error bar; when several slots finished inside one interval the figure is a summed prefill time and is named as such, since a sum of waits is not a wait. It also shows you your KV cache fill, aggregated across every slot, and it reads every slot rather than the first — a server started with -np 2 hands a request to whichever slot is free. If you use speculative decoding, it tells you which draft head is in use, its acceptance rate, the tokens per step, and the per position acceptance. These inference statistics are tied directly to the model, meaning they reset automatically when the model changes.

I designed this tool to read everything directly from the source. Data is pulled from sysfs, proc, and the driver. It can use optional helpers like intel gpu top or nvidia smi only if they are present on your system. It never blindly trusts a sensor. Any out of range readings are dropped and it just keeps the last good value. If a card, a tool, or a counter is missing, it honestly shows a dash with a reason instead of giving you a fake zero — a server started without --metrics has no prefill counter, and that is not the same thing as a prefill of zero. That holds for the cards too, and the reason is written for the thing that is actually missing. Where the answer is a program, it names the BINARY and not a package, since package names differ between distributions while binaries do not: a machine without nvidia-smi reads "no data · util, vram, temp, power, clocks — needs nvidia-smi" instead of an empty panel. On AMD, though, almost everything comes from sysfs, so naming a binary would send you after a package that changes nothing; there the reason names the driver attribute or the missing node instead, as in "gpu_busy_percent not exposed by this driver" or "no power1_average on this hwmon node". An integrated GPU says its memory is the system's rather than drawing a bar against the whole of RAM, and it knows which it is by measurement rather than by its name — a discrete Arc is an Intel card with real VRAM, so what decides it is whether the total reported for the card is the machine's own RAM total. The same care applies in the other direction, to readings that are genuinely zero: a card in zero-fan mode reports 0 rpm and is shown as stopped rather than as having no tachometer, an idle GPU reads 0% and says so, and 0 watts is a measurement while an unreadable meter is a dash. Inference is read on its own thread, so a server busy enough to stop answering slows nothing down on screen: the rest of the panel keeps its once-a-second refresh and the stalled server says so.

It features a summary strip at the top for quick glances at your generation speed, free VRAM, and total watts. Below that, it generates trend sparklines so you can see exactly when something changed. Each one is a strip chart: one column per refresh, newest on the right, sliding left as time passes. The scale is fitted to the range the data actually occupies rather than anchored at zero, so a metric living in a narrow band still shows its shape, and it is snapped to a round step so the graph scrolls instead of flickering. Every row carries the peak for the whole session, and a bar that does not start at zero says where its floor is. The KV cache has a row of its own on generative servers, and unlike the speed rows it keeps its real value while the server is idle: a slot that is not working still holds its conversation, so recording a zero there would draw a collapse that never happened. Percentages keep their absolute scale. A dot means a true zero and nothing else. The script enumerates your DRM cards and identifies the driver and vendor, bringing you all the deep hardware metrics like temperatures, clocks, core voltage, and GTT versus VRAM eviction data. On AMD the VRM figures are temperatures and are shown as such: the binary metrics blob reports them in degrees, its own voltage fields read as unsupported on the cards checked, and the real core voltage comes from hwmon. 

Every server is found just by scanning the process list, so there are no ports to configure. If you run multiple servers, they each get their own dedicated panel automatically. The process list intelligently analyzes your active models and distinguishes between those running on the GPU and those running strictly on your CPU, explicitly labeling CPU-only RAM usages so you are never left guessing where your memory went. On AMD and Intel that split comes from the kernel, which reports device memory separately from the host-side spill; NVIDIA does not expose those counters at all, so there the figure comes from nvidia-smi and the spill is shown as unknown rather than as zero, because one number for memory on the card cannot be divided into two. It even has a server config section that reads the settings from the command line so you know exactly how the model was loaded — every flag it was launched with, not a curated subset, so DRY, XTC, mirostat, pooling, LoRA and anything a newer llama.cpp adds all show up. API keys are masked. Sampler values there are the server's defaults, which a request carrying its own overrides, and the panel says so. 

For your CPU and RAM, it tracks utilisation, temperatures, frequency, RAPL power, and swap destinations like zram versus disk. The power section aggregates every readable watt meter to give you the cumulative energy drawn during your session and the energy efficiency of your models. 

The tool is completely portable. Since it discovers hardware at runtime, it runs on any Linux machine. 

The tests live in `tests/` and use the standard library only, like the program: run them with `python3 -m unittest discover -s tests`. They need no particular hardware and touch no network, since every reader is driven against a temporary directory standing in for sysfs. Most of them come in pairs, because the difficult half of "a missing reading is a dash" is proving that a real zero still reads as zero: a parked fan, a power-gated GPU, an idle server and a freshly started card are all genuinely at zero and have to survive.

To use it, just run the python script in your terminal. You can pass a port number to focus on a single server, or use the once or line flags for scripting and logging. The TUI is fully interactive. You can quit with q, scroll with your arrow keys, change the refresh rate with plus and minus, and reset the history with z.

If you want to track your CPU power, make sure your kernel allows reading the RAPL counter. You might need to add a udev rule to grant your user access to the powercap sysfs directory.

There is also a web dashboard, `dashboard.py`, which serves everything the terminal view collects over HTTP. It is not a second monitor with its own opinions about your hardware: it imports `llamagputop.py` as a module and drives the same collection seam the TUI uses, so the browser and the terminal are reading one set of numbers rather than two implementations of them. It reuses the same helpers for context text, for the reason a speed is unavailable, and for the medians and extremes, and it keeps the same contract about missing data — `None` and `0` stay distinct all the way through the JSON into the page, so a card with no readable power meter renders an em dash while a genuinely idle GPU renders 0%. It has no dependencies beyond the standard library either, so there is nothing to install.

Run it with `python3 dashboard.py` and it listens on `0.0.0.0:7778`. A bare number changes the HTTP port, `--bind` changes the address, `--interval` changes how often it samples, and `--llama-port` focuses a single llama.cpp server the way the positional port does for the TUI. It serves `/` for the dashboard itself, `/api/metrics` for the whole snapshot as JSON, `/healthz` for whether the collector has produced a sample yet, which is what you want a proxy or a container healthcheck to poll, and `/favicon.svg` for the tab icon — a bar meter in the same three colours the page uses for ok, busy and hot, drawn as bars rather than lettering because a favicon is read at 16 px where text turns to mush. The tab is titled with the machine's hostname as soon as the first sample lands, so several of these open at once stay tellable apart. A request for `/favicon.ico`, which browsers make unprompted, is answered with a 204 rather than left to log a 404.

The llama.cpp panels come first, above the hardware, since whether anything is actually generating is the usual reason for opening the page and it should not need a scroll. Below them the page shows a card per GPU with utilisation, VRAM, power against the card's cap, temperatures from every sensor the driver exposes, both clocks, and a sparkline of recent utilisation with its session min, average and peak. Each card also carries a bandwidth block: the PCIe link the card is negotiating right now and the maximum it can negotiate, both converted to GB/s through the same encoding-efficiency table the terminal view uses, the narrowest hop in the chain when that is the real limit, and live rx/tx throughput streamed from `nvidia-smi dmon`. Reading the two link figures together is the point: a link sitting at 2.5 GT/s while idle is power management and comes back the moment work arrives, but a width below what the card is capable of is physical, usually a slot bifurcated because a second card is in the machine, and it will not recover. Peak VRAM bandwidth is deliberately not shown. It needs the memory bus width, which no driver here exposes, not in `nvidia-smi -q`, not as a query field, not in sysfs, and deriving it from a table of product names would be exactly the invented number this project refuses to print, so the memory side reports its clock and its controller utilisation and says why the rest is missing. The CPU and memory cards carry utilisation, frequency, load average, temperatures, RAPL power, and the full memory breakdown down to zram versus disk swap. Each llama.cpp server gets its own panel with prefill and generation speed, a last-known rate explicitly marked "last" rather than dressed up as current, time to first token, context, KV cache fill, slot occupancy, speculative decoding acceptance and draft type, and the power attributed to the cards that server is actually running on. The server's full launch configuration is there too, grouped as the TUI groups it, with API keys masked. Every panel ends with an "all fields" section listing whatever keys the collector returned that the panel above did not already show, so a metric added to `llamagputop.py` later appears in the web view on its own rather than being silently dropped, and the raw snapshot is at the bottom of the page for when you want the JSON. The browser polls once a second; nothing is pushed, so it survives a reverse proxy that buffers.

To keep it running across reboots, install it as a systemd service. The unit in this repository, `llamagputop-dashboard.service`, carries no username and no paths: its `[Service]` section is deliberately left incomplete, and the command below appends `User=`, `Group=`, `WorkingDirectory=` and `ExecStart=` to it, filled in from the account you are logged in as and from wherever you cloned the repository. Run it from inside the checkout, as your normal user — do not put `sudo` in front of the whole block, or the appended user becomes root, which is both wrong and unnecessary since nothing here needs privilege.

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
systemctl status llamagputop-dashboard --no-pager
journalctl -u llamagputop-dashboard -f
```

Change the trailing `7778` if you want a different port. If your checkout lives on a separate mount, add a `RequiresMountsFor=` line to the `[Unit]` section so systemd waits for it.

`After=llama-server.service` there is ordering and not a dependency, on purpose: the dashboard is useful with no llama.cpp server running at all and should not be torn down when one stops. If you put it behind a reverse proxy, give it its own subdomain rather than a subpath, because the page loads its assets from the site root. Put authentication in front of it if it will be reachable from outside your network: it exposes no way to start or stop anything and it masks API keys, but it does publish your model paths, ports, process IDs and hardware inventory to anyone who can open it.

Copyright 2026 XscannedX. MIT License.
