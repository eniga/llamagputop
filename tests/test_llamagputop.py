import json
#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for llamagputop.

Standard library only, like the program itself. Run them with

    python3 -m unittest discover -s tests -v

or run this file directly. No network is touched and no particular hardware is
needed: every reader is driven against a temporary directory that stands in for
sysfs, or against a stub feed.

Almost everything here comes in pairs, and the second half of each pair is the
half that matters. The program's promise is that a missing reading shows as a
dash with a reason and never as a zero, and the hard part of testing that is not
the missing case: it is the zero one. A fan parked at 0 rpm, a power-gated GPU
at 0 MHz, an idle server at 0 tokens per second and a freshly started card with
0 MiB allocated are all real readings that have to survive. A suite that checked
only the first half would pass a change that turns every zero into a dash, which
is the same defect facing the other way.

Each fixture also carries a control that proves it can express presence before
any absence is concluded from it. A fixture that silently produces nothing makes
every "this field is empty" assertion pass for the wrong reason.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "llamagputop.py")


def _load():
    spec = importlib.util.spec_from_file_location("llamagputop_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lgt = _load()


class Base(unittest.TestCase):
    """Shared temporary tree, and a reset of the module-level state.

    The program keeps its history, its last-good sensor cache and its per-port
    probes at module level, which is right for a monitor that runs for hours and
    wrong for tests that must not inherit each other's leftovers.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="llamagputop-test-")
        self._ttl = lgt._SANE_TTL
        lgt.HIST.clear()
        lgt._last_good.clear()
        lgt._probes.clear()
        lgt._port_seen.clear()
        lgt._last_pp.clear()
        lgt._model_ports.clear()
        lgt._CPU_E.update({"t": 0.0, "j": 0, "w": None})

    def tearDown(self):
        lgt._SANE_TTL = self._ttl
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------------------------------------------------------------- fixtures
    def card(self, name, dev=None, hwmon=None, gt=None):
        """A directory that looks enough like a DRM card node to read from.

        `hwmon=None` means the node is absent entirely, which is a different
        state from a node whose optional files are missing, and both occur.
        """
        path = os.path.join(self.root, name)
        devdir = os.path.join(path, "device")
        os.makedirs(devdir, exist_ok=True)
        for fname, value in (dev or {}).items():
            self.write(os.path.join(devdir, fname), value)
        if hwmon is not None:
            hwdir = os.path.join(devdir, "hwmon", "hwmon0")
            os.makedirs(hwdir, exist_ok=True)
            for fname, value in hwmon.items():
                self.write(os.path.join(hwdir, fname), value)
        if gt is not None:
            gtdir = os.path.join(path, "gt", "gt0")
            os.makedirs(gtdir, exist_ok=True)
            for fname, value in gt.items():
                self.write(os.path.join(gtdir, fname), value)
        return path

    @staticmethod
    def write(path, value):
        with open(path, "w") as handle:
            handle.write(str(value))

    @staticmethod
    def rows(sample, width=96):
        return "\n".join("".join(text for text, _attr in row)
                         for row in lgt._gpu_rows(sample, width))


class StubNvtop:
    """Stands in for the memory feed thread.

    by_key maps a device name to (used_mib, total_mib, timestamp, utilisation),
    which is the shape the real reader builds from the tool's JSON output.
    """

    def __init__(self, rows):
        self.tool_ok = True
        self.answered = True
        self.by_key = {name: (used, total, time.monotonic(), util)
                       for name, (used, total, util) in rows.items()}


class StubEngine:
    """Stands in for the engine feed thread."""

    def __init__(self, freq, alive=True):
        self.tool_ok = True
        self.data = {"alive": alive, "ts": time.monotonic(),
                     "engines": {"RCS": 5.0}, "freq": freq}


class StubLlamaFeed:
    """Hands a fixed fleet to the collector so no HTTP request is made."""

    def __init__(self, fleet=()):
        self.data = [dict(entry) for entry in fleet]
        self.at = time.monotonic()


# =============================================================================
class SaneCache(Base):
    """A held reading has to expire, without losing the reason it is held."""

    def test_in_range_value_is_returned(self):
        self.assertEqual(lgt.sane("k", 60, 1, 130), 60)

    def test_rejected_value_with_no_history_is_absent(self):
        self.assertIsNone(lgt.sane("k", 0, 1, 130))

    def test_shipped_window_is_ten_seconds(self):
        self.assertEqual(lgt._SANE_TTL, 10.0)

    def test_value_held_past_the_window_expires(self):
        lgt._SANE_TTL = 0.3
        lgt.sane("k", 60, 1, 130)
        time.sleep(0.45)
        self.assertIsNone(lgt.sane("k", 0, 1, 130))

    def test_value_held_briefly_survives(self):
        """Holding across a bad tick is the point of the cache, not a bug."""
        lgt.sane("k", 60, 1, 130)
        time.sleep(0.05)
        self.assertEqual(lgt.sane("k", 0, 1, 130), 60)

    def test_intermittent_sensor_never_expires(self):
        """A sensor answering every other tick keeps its value indefinitely."""
        lgt._SANE_TTL = 0.3
        seen = []
        for i in range(6):
            seen.append(lgt.sane("k", 60 if i % 2 == 0 else 0, 1, 130))
            time.sleep(0.1)
        self.assertEqual(seen, [60] * 6)

    def test_a_fresh_reading_restarts_the_window(self):
        lgt._SANE_TTL = 0.3
        lgt.sane("k", 60, 1, 130)
        time.sleep(0.2)
        lgt.sane("k", 61, 1, 130)
        time.sleep(0.2)
        self.assertEqual(lgt.sane("k", 0, 1, 130), 61)

    def test_a_good_reading_always_beats_the_cache(self):
        lgt.sane("k", 60, 1, 130)
        self.assertEqual(lgt.sane("k", 42, 1, 130), 42)


# =============================================================================
class EnergyCaches(Base):
    """The two wattage caches carry a timestamp and never used to read it."""

    def gpu_watts(self, gap):
        """Two counter reads produce a rate, then the file vanishes for `gap`.

        The counter starts non-zero on purpose: the rate is only computed once a
        previous reading exists, so a fixture starting at zero would never
        produce a wattage at all and every assertion below would pass without
        testing anything.
        """
        lgt._SANE_TTL = 0.3
        path = self.card("energy", hwmon={"energy1_input": 1000})
        node = lgt._hwmon_of(path)
        gpu = lgt.IntelGpu(path, "Fixture", 0, None, None)
        gpu._power()
        time.sleep(0.45)
        self.write(os.path.join(node, "energy1_input"), 1000000)
        live = gpu._power()
        os.remove(os.path.join(node, "energy1_input"))
        time.sleep(gap)
        return live, gpu._power()

    def cpu_watts(self, gap):
        lgt._SANE_TTL = 0.3
        rapl = os.path.join(self.root, "rapl")
        os.makedirs(rapl, exist_ok=True)
        real = lgt.RAPL_CPU
        lgt.RAPL_CPU = rapl
        try:
            self.write(os.path.join(rapl, "energy_uj"), 1000)
            lgt.cpu_sample()
            time.sleep(0.45)
            self.write(os.path.join(rapl, "energy_uj"), 1000000)
            live = lgt.cpu_sample().get("power")
            os.remove(os.path.join(rapl, "energy_uj"))
            time.sleep(gap)
            return live, lgt.cpu_sample().get("power")
        finally:
            lgt.RAPL_CPU = real

    def test_gpu_fixture_produces_a_real_wattage(self):
        live, _ = self.gpu_watts(0.05)
        self.assertIsNotNone(live)
        self.assertGreater(live, 0.5)

    def test_gpu_wattage_expires(self):
        self.assertIsNone(self.gpu_watts(0.45)[1])

    def test_gpu_wattage_held_briefly_survives(self):
        self.assertIsNotNone(self.gpu_watts(0.05)[1])

    def test_cpu_fixture_produces_a_real_wattage(self):
        live, _ = self.cpu_watts(0.05)
        self.assertIsNotNone(live)
        self.assertGreater(live, 0.5)

    def test_cpu_wattage_expires(self):
        self.assertIsNone(self.cpu_watts(0.45)[1])

    def test_cpu_wattage_held_briefly_survives(self):
        self.assertIsNotNone(self.cpu_watts(0.05)[1])


# =============================================================================
class AmdReader(Base):
    """An absent sysfs file must not read as zero, and a real zero must stay."""

    BARE = {}
    ZEROS = {
        "gpu_busy_percent": 0,            # idle card
        "mem_busy_percent": 0,
        "mem_info_vram_used": 0,          # nothing allocated yet
        "mem_info_vram_total": 12884901888,
        "mem_info_gtt_used": 0,
        "mem_info_gtt_total": 8589934592,
        "pp_dpm_sclk": "0: 500Mhz *\n1: 2450Mhz\n",
        "pp_dpm_mclk": "0: 96Mhz *\n1: 1124Mhz\n",
    }
    ZEROS_HWMON = {
        "temp1_label": "edge", "temp1_input": 41000,
        "power1_average": 8000000,
        "in0_input": 806,
        "fan1_input": 0,                  # zero-rpm mode, a shipped feature
        "pwm1": 0,
    }
    FULL = {"gpu_busy_percent": 37, "mem_busy_percent": 12,
            "mem_info_vram_used": 3221225472, "mem_info_vram_total": 12884901888,
            "mem_info_gtt_used": 1073741824, "mem_info_gtt_total": 8589934592}
    FULL_HWMON = {"temp1_label": "edge", "temp1_input": 63000,
                  "power1_average": 142000000, "fan1_input": 1450, "pwm1": 128}

    def sample(self, tag, dev, hwmon=None):
        return lgt.AmdGpu(self.card(tag, dev=dev, hwmon=hwmon), "Fixture").sample()

    def bare(self):
        return self.sample("bare", self.BARE)

    def zeros(self):
        return self.sample("zeros", self.ZEROS, self.ZEROS_HWMON)

    def full(self):
        return self.sample("full", self.FULL, self.FULL_HWMON)

    # -- the fixture can express presence, so an absence below means something
    def test_fixture_reports_a_real_utilisation(self):
        self.assertEqual(self.full().get("util"), 37)

    def test_fixture_reports_a_real_memory_figure(self):
        self.assertEqual(self.full().get("vram_used"), 3072)

    # -- a card that exposes nothing
    def test_absent_utilisation_is_not_zero(self):
        self.assertIsNone(self.bare().get("util"))

    def test_absent_memory_is_not_zero(self):
        sample = self.bare()
        self.assertIsNone(sample.get("vram_total"))
        self.assertIsNone(sample.get("gtt_total"))
        self.assertIsNone(sample.get("mem_util"))

    def test_every_empty_field_carries_a_reason(self):
        needs = self.bare().get("needs") or {}
        for field in ("util", "vram", "temp", "power", "clocks"):
            self.assertIn(field, needs)

    def test_no_reason_names_a_binary_to_install(self):
        """On this vendor the answer is about the driver, not a missing tool."""
        joined = " ".join((self.bare().get("needs") or {}).values())
        for binary in ("nvidia-smi", "intel_gpu_top", "nvtop"):
            self.assertNotIn(binary, joined)

    def test_reasons_reach_the_panel(self):
        self.assertIn("no data", self.rows(self.bare()))

    def test_no_fan_row_is_invented(self):
        self.assertNotIn("fan ", self.rows(self.bare()))

    # -- a card whose readings are legitimately zero
    def test_idle_utilisation_stays_zero(self):
        self.assertEqual(self.zeros().get("util"), 0)
        self.assertEqual(self.zeros().get("mem_util"), 0)

    def test_unallocated_memory_stays_zero(self):
        sample = self.zeros()
        self.assertEqual(sample.get("vram_used"), 0)
        self.assertEqual(sample.get("vram_total"), 12288)

    def test_a_parked_fan_reads_zero_rather_than_unknown(self):
        self.assertEqual(self.zeros().get("fan_rpm"), 0)
        self.assertEqual(self.zeros().get("fan_pct"), 0)

    def test_a_parked_fan_is_visible_on_the_panel(self):
        self.assertIn("fan ", self.rows(self.zeros()))

    def test_a_spinning_fan_is_still_shown(self):
        self.assertIn("1450 rpm", self.rows(self.full()))

    def test_a_field_that_answered_gets_no_reason(self):
        needs = self.zeros().get("needs") or {}
        for field in ("util", "vram", "temp", "power"):
            self.assertNotIn(field, needs)


# =============================================================================
class IntelReader(Base):
    """Shared memory, a gated clock, and a fallback that never ran."""

    IGPU = "TigerLake-H GT1 (UHD Graphics)"
    DISCRETE = "Intel Arc A770 Graphics"

    def sample(self, tag, feed=None, engine=None, gt=None, hwmon=None):
        path = self.card(tag, gt=gt, hwmon=hwmon)
        return lgt.IntelGpu(path, self.IGPU, 0, engine, feed).sample()

    # -- an integrated GPU has no memory of its own
    def test_a_discrete_card_keeps_its_memory_pair(self):
        feed = StubNvtop({self.DISCRETE: (3000, 16384, 44.0)})
        card = self.card("arc")
        sample = lgt.IntelGpu(card, self.DISCRETE, 0, None, feed).sample()
        self.assertEqual(sample.get("vram_used"), 3000)
        self.assertEqual(sample.get("vram_total"), 16384)
        self.assertIsNone(sample.get("vram_shared"))
        self.assertIn("3000/16384", self.rows(sample))

    def test_module_memory_total_matches_an_independent_read(self):
        """Read here rather than taken from the module, which would prove nothing."""
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    self.assertEqual(lgt.RAM_TOTAL_MIB, int(line.split()[1]) // 1024)
                    return
        self.fail("no MemTotal in /proc/meminfo")

    def test_a_total_equal_to_system_memory_is_marked_shared(self):
        feed = StubNvtop({self.IGPU: (15018, lgt.RAM_TOTAL_MIB, 12.0)})
        sample = self.sample("igpu", feed=feed)
        self.assertTrue(sample.get("vram_shared"))
        self.assertIn(sample.get("vram_total"), (None, 0))
        self.assertEqual((sample.get("needs") or {}).get("vram"),
                         "memory is shared with system RAM")

    def test_a_shared_card_draws_no_memory_bar(self):
        feed = StubNvtop({self.IGPU: (15018, lgt.RAM_TOTAL_MIB, 12.0)})
        rendered = self.rows(self.sample("igpu-bar", feed=feed))
        self.assertNotIn("MiB", rendered)
        self.assertIn("shared with system RAM", rendered)

    def test_the_older_signature_still_routes_the_same_way(self):
        """A total with no used figure was the case the first guard covered."""
        feed = StubNvtop({self.IGPU: (None, lgt.RAM_TOTAL_MIB, 12.0)})
        self.assertTrue(self.sample("igpu-noused", feed=feed).get("vram_shared"))

    # -- a power-gated card really does report zero
    def test_a_gated_clock_reads_zero(self):
        sample = self.sample("gated", gt={"rps_act_freq_mhz": 0})
        self.assertEqual(sample.get("sclk"), 0)
        self.assertIn("core 0 MHz", self.rows(sample))

    def test_an_absent_clock_file_is_not_a_zero(self):
        sample = self.sample("noclock", gt={})
        self.assertIsNone(sample.get("sclk"))
        self.assertNotIn("core ", self.rows(sample))

    def test_a_normal_clock_is_unaffected(self):
        self.assertEqual(self.sample("clk", gt={"rps_act_freq_mhz": 1350}).get("sclk"),
                         1350)

    def test_an_out_of_range_clock_is_still_refused(self):
        self.assertIsNone(self.sample("wild", gt={"rps_act_freq_mhz": 99999}).get("sclk"))

    # -- the fallback that a present-but-empty key used to disable
    def test_sysfs_fills_in_when_the_feed_reports_no_frequency(self):
        sample = self.sample("fb0", engine=StubEngine(0),
                             gt={"rps_act_freq_mhz": 1350})
        self.assertEqual(sample.get("sclk"), 1350)

    def test_sysfs_fills_in_when_the_feed_frequency_is_absent(self):
        sample = self.sample("fbn", engine=StubEngine(None),
                             gt={"rps_act_freq_mhz": 1350})
        self.assertEqual(sample.get("sclk"), 1350)

    def test_a_live_feed_frequency_still_wins(self):
        sample = self.sample("fbw", engine=StubEngine(800),
                             gt={"rps_act_freq_mhz": 1350})
        self.assertEqual(sample.get("sclk"), 800)

    def test_a_parked_fan_reads_zero_here_too(self):
        sample = self.sample("fan", hwmon={"fan1_input": 0, "pwm1": 0,
                                           "temp1_input": 44000})
        self.assertEqual(sample.get("fan_rpm"), 0)


# =============================================================================
class StatusLine(Base):
    """The one-line output has to keep the same promise as the panel."""

    CPU = {"util": 12.0, "temp": 64, "power": 31.0, "rapl_present": True}
    MEM = {"free": 6717, "total": 31864}

    def line(self, gpu, cpu=None):
        return lgt._text_line([gpu], dict(cpu or self.CPU), self.MEM, [])

    def segment(self, out, prefix):
        """Assertions are scoped to one segment, never to the whole line.

        A substring over a line that concatenates six independent readings
        cannot say which reading it matched, so it is not an assertion about
        the field.
        """
        for part in out.split(" | ")[1:]:
            if part.startswith(prefix):
                return part
        self.fail("no segment starting with %r in %r" % (prefix, out))

    def gpu_segment(self, **fields):
        base = {"vendor": "Intel", "util": 1.0, "temp_main": 40, "power": 5.0}
        base.update(fields)
        return self.segment(self.line(base), base["vendor"])

    def test_absent_power_is_a_dash(self):
        seg = self.gpu_segment(power=None, needs={"power": "no energy counter"})
        self.assertIn("—W", seg)
        self.assertNotIn("0W", seg)

    def test_a_real_zero_power_stays_zero(self):
        self.assertIn("0W", self.gpu_segment(power=0.0))

    def test_absent_utilisation_is_a_dash(self):
        self.assertIn("util —%", self.gpu_segment(util=None))

    def test_a_real_zero_utilisation_stays_zero(self):
        self.assertIn("util 0%", self.gpu_segment(util=0.0))

    def test_absent_cpu_utilisation_is_a_dash(self):
        cpu = dict(self.CPU, util=None)
        seg = self.segment(self.line({"vendor": "Intel", "util": 1.0,
                                      "temp_main": 40, "power": 5.0}, cpu), "CPU")
        self.assertIn("CPU —%", seg)

    def test_a_real_zero_cpu_utilisation_stays_zero(self):
        cpu = dict(self.CPU, util=0.0)
        seg = self.segment(self.line({"vendor": "Intel", "util": 1.0,
                                      "temp_main": 40, "power": 5.0}, cpu), "CPU")
        self.assertIn("CPU 0%", seg)

    def test_absent_memory_used_is_a_dash(self):
        seg = self.gpu_segment(vram_used=None, vram_total=6144)
        self.assertIn("vram —/6144", seg)

    def test_a_real_zero_memory_used_stays_zero(self):
        self.assertIn("vram 0/6144", self.gpu_segment(vram_used=0, vram_total=6144))

    def test_no_memory_total_renders_a_bare_dash(self):
        self.assertIn("vram -", self.gpu_segment())

    def test_absent_temperature_stays_a_dash(self):
        self.assertIn("-C", self.gpu_segment(temp_main=None))

    def test_absent_cpu_power_stays_a_dash(self):
        cpu = dict(self.CPU, power=None)
        seg = self.segment(self.line({"vendor": "Intel", "util": 1.0,
                                      "temp_main": 40, "power": 5.0}, cpu), "CPU")
        self.assertIn("64C -", seg)


# =============================================================================
class PortFocus(Base):
    """The port argument selects one server, and says so when it is silent."""

    DISCOVERED = [
        {"pid": "101", "port": "7795", "host": "127.0.0.1", "model_hint": "a"},
        {"pid": "102", "port": "7797", "host": "127.0.0.1", "model_hint": "b"},
        {"pid": "103", "port": "7799", "host": "127.0.0.1", "model_hint": "c"},
        {"pid": "104", "port": "8181", "host": "127.0.0.1", "model_hint": "d"},
    ]

    def setUp(self):
        super().setUp()
        self._discover = lgt.discover_llama_servers
        self._sample = lgt.LlamaProbe.sample
        lgt.discover_llama_servers = lambda: [dict(s) for s in self.DISCOVERED]
        lgt.LlamaProbe.sample = lambda self: {"alive": True, "phase": "idle",
                                              "model": ""}

    def tearDown(self):
        lgt.discover_llama_servers = self._discover
        lgt.LlamaProbe.sample = self._sample
        super().tearDown()

    def ports(self, arg):
        lgt._probes.clear()
        return [entry["port"] for entry in lgt.sample_llama_fleet(arg)]

    def test_the_stub_discovers_the_whole_fleet(self):
        self.assertEqual(self.ports(None), ["7795", "7797", "7799", "8181"])

    def test_a_discovered_port_focuses_to_one(self):
        self.assertEqual(self.ports("7795"), ["7795"])

    def test_an_undiscovered_port_is_still_probed_alone(self):
        self.assertEqual(self.ports("65535"), ["65535"])

    def test_focusing_clears_the_multi_server_flag(self):
        lgt._probes.clear()
        self.assertEqual([e.get("multi") for e in lgt.sample_llama_fleet("7795")],
                         [False])

    def test_no_argument_keeps_the_multi_server_flag(self):
        lgt._probes.clear()
        self.assertEqual([e.get("multi") for e in lgt.sample_llama_fleet(None)],
                         [True] * 4)

    def test_an_integer_port_matches_the_string_ports(self):
        """Discovery yields strings; comparing the wrong types would match none."""
        self.assertEqual(self.ports(7795), ["7795"])


class LoneSilentServer(Base):
    """Skipping a silent server is right only while another one is answering."""

    DEAD = {"alive": False, "phase": "off", "stale": True, "port": "65535",
            "model": ""}
    ALIVE = {"alive": True, "phase": "idle", "port": "7795", "model": "m",
             "pp": None, "tg": None, "pp_last": None, "tg_last": None,
             "multi": True}
    CPU = {"util": 1.0, "temp": 50, "power": 10.0, "rapl_present": True}
    MEM = {"free": 1, "total": 2}

    def line(self, fleet):
        return lgt._text_line([], dict(self.CPU), self.MEM, fleet)

    def test_a_lone_silent_server_is_reported(self):
        out = self.line([self.DEAD])
        self.assertIn("65535", out)
        self.assertIn("did not answer", out)

    def test_a_silent_server_beside_a_live_one_is_skipped(self):
        out = self.line([self.ALIVE, self.DEAD])
        self.assertNotIn("65535", out)
        self.assertIn("7795", out)

    def test_an_empty_fleet_adds_nothing(self):
        self.assertNotIn("llama", self.line([]))


# =============================================================================
class TrendSeries(Base):
    """A dot on a trend row means a true zero and nothing else."""

    class FakeGpu:
        vendor = "NVIDIA"

        def __init__(self, payload):
            self.payload = payload

        def sample(self):
            return dict(self.payload)

    def series(self, key, values):
        for value in values:
            lgt.record(key, value)
        return list(lgt.HIST.get(key, []))

    def collect_util(self, payload, ticks=4):
        gpu = self.FakeGpu(dict(payload, name="Fixture", temp={}, temp_crit={}))
        for _ in range(ticks):
            lgt._collect([gpu], None, StubLlamaFeed())
        return list(lgt.HIST.get("gpu0_util", []))

    def test_real_values_are_kept(self):
        self.assertEqual(self.series("a", [10.0, 20.0]), [10.0, 20.0])

    def test_a_non_finite_value_is_refused(self):
        self.assertEqual(self.series("b", [5.0, float("nan")]), [5.0, 5.0])

    def test_an_absent_value_holds_the_previous_one(self):
        self.assertEqual(self.series("c", [62.0, None]), [62.0, 62.0])

    def test_an_absent_value_with_no_history_records_nothing(self):
        self.assertEqual(self.series("d", [None, None]), [])

    def test_a_card_with_no_source_charts_nothing(self):
        self.assertEqual(
            self.collect_util({"util": None, "needs": {"util": "needs nvidia-smi"}}),
            [])

    def test_a_real_zero_is_charted(self):
        self.assertEqual(self.series("e", [0.0, 0.0]), [0.0, 0.0])

    def test_a_card_reporting_zero_charts_zeros(self):
        self.assertEqual(self.collect_util({"util": 0.0}), [0] * 4)

    def test_a_card_reporting_a_rate_charts_it(self):
        self.assertEqual(self.collect_util({"util": 62.0}), [62.0] * 4)

    def test_a_real_zero_between_real_values_survives(self):
        self.assertEqual(self.series("f", [50.0, 0.0, 50.0]), [50.0, 0.0, 50.0])


class ServerSeries(Base):
    """Idle and silent are different facts about a server."""

    IDLE = {"alive": True, "phase": "idle", "port": "7795", "model": "m",
            "tg": None, "pp": None, "pp_last": None, "tg_last": None, "kv": None}

    def series(self, entry, ticks=3, port="7795"):
        for _ in range(ticks):
            lgt._collect([], None, StubLlamaFeed([entry]))
        return list(lgt.HIST.get("tg_series@%s" % port, []))

    def test_an_idle_server_charts_a_true_zero(self):
        self.assertEqual(self.series(self.IDLE), [0] * 3)

    def test_a_generating_server_charts_its_rate(self):
        busy = dict(self.IDLE, phase="generating", tg=28.7)
        self.assertEqual(self.series(busy), [28.7] * 3)

    def test_a_silent_server_charts_nothing(self):
        silent = dict(self.IDLE, phase="not answering", stale=True)
        self.assertEqual(self.series(silent), [])


class SeriesRetirement(Base):
    """A port that is gone takes all of its series with it."""

    DEAD, LIVE = 59991, 59992

    def retire(self):
        now = time.monotonic()
        for port in (self.DEAD, self.LIVE):
            lgt.record("kv_series@%s" % port, 42.0)
            lgt.record("tg_series@%s" % port, 1.0)
        lgt._port_seen[self.DEAD] = now - 300
        lgt._port_seen[self.LIVE] = now
        lgt._collect([], None, StubLlamaFeed())

    def test_the_existing_series_of_a_retired_port_are_dropped(self):
        self.retire()
        self.assertNotIn("tg_series@%s" % self.DEAD, lgt.HIST)

    def test_the_cache_series_of_a_retired_port_is_dropped_too(self):
        self.retire()
        self.assertNotIn("kv_series@%s" % self.DEAD, lgt.HIST)

    def test_a_live_port_keeps_its_series(self):
        self.retire()
        self.assertIn("kv_series@%s" % self.LIVE, lgt.HIST)


# =============================================================================
class FakeClock:
    """time.monotonic under test control, so a rate over one second is exactly one
    second and the suite does not sleep."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


def vllm_metrics(running=0, waiting=0, gen=0, kv=0.0, hits=0, queries=0,
                 preempt=0, spec=True, drafts=0, dtok=0, acc=0, pos=(),
                 pf_tok=0, pf_s=0.0, pf_n=0, g_sum=0, g_n=0, dec_s=0.0,
                 ttft_s=0.0, ttft_n=0, pool=591218, prompt_total=None, cached_total=None,
                 success=None, e2e_s=None, tpot_s=None):
    """A /metrics body in vLLM's shape, labels and all. The newer counters are emitted
    only when a test asks for them, so the older tests see exactly the body they did."""
    L = 'engine="0",model_name="m"'
    out = [
        "# HELP vllm:num_requests_running running",
        f"vllm:num_requests_running{{{L}}} {running}",
        f"vllm:num_requests_waiting{{{L}}} {waiting}",
        f"vllm:kv_cache_usage_perc{{{L}}} {kv}",
        f"vllm:prefix_cache_queries_total{{{L}}} {queries}",
        f"vllm:prefix_cache_hits_total{{{L}}} {hits}",
        f"vllm:num_preemptions_total{{{L}}} {preempt}",
        f"vllm:generation_tokens_total{{{L}}} {gen}",
        f"vllm:request_prefill_kv_computed_tokens_sum{{{L}}} {pf_tok}",
        f"vllm:request_prompt_tokens_sum{{{L}}} {pf_tok}",
        f"vllm:request_prefill_time_seconds_sum{{{L}}} {pf_s}",
        f"vllm:request_prefill_time_seconds_count{{{L}}} {pf_n}",
        f"vllm:request_generation_tokens_sum{{{L}}} {g_sum}",
        f"vllm:request_generation_tokens_count{{{L}}} {g_n}",
        f"vllm:request_decode_time_seconds_sum{{{L}}} {dec_s}",
        f"vllm:time_to_first_token_seconds_sum{{{L}}} {ttft_s}",
        f"vllm:time_to_first_token_seconds_count{{{L}}} {ttft_n}",
        f'vllm:cache_config_info{{block_size="1664",{L},kv_cache_size_tokens="{pool}",'
        f'num_gpu_blocks="424"}} 1.0',
    ]
    if prompt_total is not None:
        out += [f"vllm:prompt_tokens_total{{{L}}} {prompt_total}",
                f"vllm:prompt_tokens_cached_total{{{L}}} {cached_total or 0}",
                f"vllm:request_prompt_tokens_count{{{L}}} {pf_n}"]
    if success is not None:
        out.append(f"vllm:request_success_total{{{L}}} {success}")
    if e2e_s is not None:
        out += [f"vllm:e2e_request_latency_seconds_sum{{{L}}} {e2e_s}",
                f"vllm:e2e_request_latency_seconds_count{{{L}}} {g_n}"]
    if tpot_s is not None:
        out += [f"vllm:request_time_per_output_token_seconds_sum{{{L}}} {tpot_s}",
                f"vllm:request_time_per_output_token_seconds_count{{{L}}} {g_n}"]
    if spec:
        out += [f"vllm:spec_decode_num_drafts_total{{{L}}} {drafts}",
                f"vllm:spec_decode_num_draft_tokens_total{{{L}}} {dtok}",
                f"vllm:spec_decode_num_accepted_tokens_total{{{L}}} {acc}"]
        out += [f'vllm:spec_decode_num_accepted_tokens_per_pos_total{{{L},position="{i}"}} {v}'
                for i, v in enumerate(pos)]
    return "\n".join(out) + "\n"


class VllmCommandLine(Base):
    """What a vLLM command line says, read without a process running."""

    CMD = ["/opt/vllm/bin/python3", "/opt/vllm/bin/vllm", "serve",
           "/models/unsloth/Qwen3.8-27B-FP8", "--served-model-name=Qwen3.8-27B-FP8",
           "--tensor-parallel-size=2", "--max-num-seqs=8", "--api-key=secret123",
           "--host=0.0.0.0", "--port=8000",
           '--speculative-config={"method":"mtp","num_speculative_tokens":8}']

    def test_equals_form_is_a_flag_and_a_value(self):
        pairs = lgt._parse_cmdline_flags(["x", "--port=8000", "--tp", "2"])
        self.assertEqual(pairs, [("--port", "8000"), ("--tp", "2")])

    def test_a_llama_value_with_an_equals_sign_is_left_alone(self):
        pairs = lgt._parse_cmdline_flags(["llama-server", "-ot", "blk=CPU"])
        self.assertEqual(pairs, [("-ot", "blk=CPU")])

    def test_port_host_and_served_name(self):
        srv = lgt.vllm_from_cmd(self.CMD, 42)
        self.assertEqual((srv["port"], srv["host"], srv["model_hint"], srv["kind"]),
                         ("8000", "127.0.0.1", "Qwen3.8-27B-FP8", "vllm"))

    def test_default_port_is_vllms_not_llamas(self):
        self.assertEqual(lgt.vllm_from_cmd(["vllm", "serve", "/m/x"])["port"], "8000")

    def test_positional_model_when_no_served_name(self):
        self.assertEqual(lgt.vllm_from_cmd(["vllm", "serve", "/m/org/Model/"])["model_hint"],
                         "Model")

    def test_only_the_api_server_is_a_vllm_server(self):
        self.assertTrue(lgt._is_vllm("vllm", ["vllm", "serve", "m"]))
        self.assertFalse(lgt._is_vllm("VLLM::Worker_TP", ["VLLM::Worker_TP0"]))
        self.assertFalse(lgt._is_vllm("vllm", ["vllm", "bench"]))

    def test_mtp_head_is_native(self):
        self.assertEqual(lgt.vllm_spec_from_cmd(self.CMD), ("mtp", "native (in model)", "8"))

    def test_a_draft_model_is_named(self):
        cmd = ['--speculative-config={"method":"dflash","model":"/m/DFlash2/","num_speculative_tokens":7}']
        self.assertEqual(lgt.vllm_spec_from_cmd(cmd), ("dflash", "DFlash2", "7"))

    def test_no_speculation_is_absent_not_a_default(self):
        self.assertEqual(lgt.vllm_spec_from_cmd(["vllm", "serve", "m"]), (None, None, None))

    def test_config_panel_groups_and_masks(self):
        cfg = dict((g, dict(rows)) for g, rows in lgt.vllm_settings_from_cmd(self.CMD).items())
        self.assertEqual(cfg["loading"]["model"], "Qwen3.8-27B-FP8")
        self.assertEqual(cfg["scheduling"]["max-seqs"], "8")
        self.assertNotIn("secret123", cfg["server"]["api-key"])

    def test_prometheus_labels_are_parsed_not_kept_in_the_name(self):
        m = lgt.prom_parse('a:b{x="1",position="3"} 2.5\n# c\nplain 1\n')
        self.assertEqual(m["a:b"], [({"x": "1", "position": "3"}, 2.5)])
        self.assertEqual(m["plain"], [({}, 1.0)])


class VllmProbeRates(Base):
    """The probe against a scripted /metrics sequence and a fake clock."""

    def setUp(self):
        super().setUp()
        self._time = lgt.time
        self.clock = FakeClock()
        lgt.time = self.clock
        self.pr = lgt.VllmProbe("8000")
        self.pr._cmdline = lambda: VllmCommandLine.CMD
        self.body = ""
        self.models = '{"data":[{"id":"Qwen3.8-27B-FP8","max_model_len":262144}]}'
        self.pr._get = lambda path, timeout=2.5: (
            self.models if path == "/v1/models" else self.body)

    def tearDown(self):
        lgt.time = self._time
        super().tearDown()

    def tick(self, dt=1.0, **kw):
        self.clock.t += dt
        self.body = vllm_metrics(**kw)
        return self.pr.sample()

    BASE = dict(gen=100, pf_tok=1000, pf_s=1.0, pf_n=1, g_sum=100, g_n=1, dec_s=2.0,
                ttft_s=1.0, ttft_n=1, drafts=10, dtok=80, acc=30, pos=(8, 6))

    def run_request(self):
        b = dict(self.BASE)
        self.tick(**b)                                          # idle baseline
        pre = self.tick(**dict(b, running=1, kv=0.07))          # prefill: no token yet
        g1 = self.tick(**dict(b, running=1, kv=0.07, gen=150))  # first tokens
        g2 = self.tick(**dict(b, running=1, kv=0.07, gen=200))  # one second later
        done = self.tick(**dict(b, gen=210, kv=0.07, pf_tok=4000, pf_s=2.5, pf_n=2,
                                g_sum=210, g_n=2, dec_s=4.18, ttft_s=2.6, ttft_n=2))
        return pre, g1, g2, done

    def test_phases_follow_the_generation_counter(self):
        pre, g1, g2, done = self.run_request()
        self.assertEqual([s["phase"] for s in (pre, g1, g2, done)],
                         ["prefill", "generating", "generating", "idle"])

    def test_live_rate_is_the_counter_slope(self):
        _pre, g1, g2, _done = self.run_request()
        self.assertIsNone(g1["tg"])            # one point is not a rate
        self.assertAlmostEqual(g2["tg"], 50.0)

    def test_prefill_is_never_a_live_rate(self):
        for s in self.run_request():
            self.assertIsNone(s["pp"])

    def test_completed_request_rates_come_from_the_histograms(self):
        *_rest, done = self.run_request()
        self.assertAlmostEqual(done["pp_last"], 3000 / 1.5)
        self.assertAlmostEqual(done["ttft_last"], 1.6)
        # 110 tokens, the first of them prefill's, over 2.18 s of decode
        self.assertAlmostEqual(done["tg_last"], 109 / 2.18)
        self.assertIsNone(done["tg"])

    def test_kv_is_the_pool_fraction_in_tokens(self):
        _pre, _g1, g2, _done = self.run_request()
        self.assertAlmostEqual(g2["kv"], 0.07)
        self.assertEqual(g2["kv_cap"], 591218)
        self.assertEqual(g2["kv_used"], round(0.07 * 591218))

    def test_speculative_counters(self):
        s = self.tick(**self.BASE)
        self.assertAlmostEqual(s["spec"], 30 / 80)
        self.assertAlmostEqual(s["tok_step"], 1 + 30 / 10)
        self.assertEqual(s["spec_pos"], [0.8, 0.6])
        self.assertEqual(s["spec_type"], "mtp")

    def test_model_and_context_from_the_api(self):
        s = self.tick(**self.BASE)
        self.assertEqual((s["model"], s["ctx"], s["slots"]), ("Qwen3.8-27B-FP8", 262144, 8))

    # ---- the pairs: absent stays absent, zero stays zero
    def test_no_speculation_counters_is_not_zero_acceptance(self):
        s = self.tick(**dict(self.BASE, spec=False))
        self.assertIsNone(s["spec"])
        self.assertIsNone(s["tok_step"])

    def test_zero_prefix_hits_is_a_real_zero(self):
        s = self.tick(**dict(self.BASE, queries=500, hits=0))
        self.assertEqual(s["reuse"], 0.0)

    def test_no_prefix_lookups_is_no_reuse_figure(self):
        s = self.tick(**dict(self.BASE, queries=0, hits=0))
        self.assertIsNone(s["reuse"])

    def test_an_empty_cache_reads_zero_not_absent(self):
        s = self.tick(**self.BASE)
        self.assertEqual(s["kv"], 0.0)
        self.assertEqual(s["preemptions"], 0)

    def test_idle_server_has_no_live_rate_and_no_invented_last(self):
        s = self.tick(**self.BASE)
        s = self.tick(**self.BASE)
        self.assertIsNone(s["tg"])
        self.assertIsNone(s["tg_last"])
        self.assertEqual(s["phase"], "idle")

    def test_a_server_without_vllm_counters_says_so(self):
        self.clock.t += 1
        self.body = "process_cpu_seconds_total 1.0\n"
        s = self.pr.sample()
        self.assertTrue(s["alive"])
        self.assertTrue(s["metrics_off"])

    # ---- runs: live while work is happening, summarised once it stops
    def test_a_run_is_live_while_it_lasts(self):
        _pre, _g1, g2, _done = self.run_request()
        r = g2["run"]
        self.assertTrue(r["live"])
        self.assertEqual(r["tokens"], 100)
        self.assertAlmostEqual(r["peak"], 50.0)

    def test_a_finished_run_is_summarised(self):
        self.run_request()
        s = self.tick(**dict(self.BASE, gen=210, g_sum=210, g_n=2, dec_s=4.18,
                             pf_tok=4000, pf_s=2.5, pf_n=2, ttft_s=2.6, ttft_n=2))
        r = s["run"]
        self.assertFalse(r["live"])
        self.assertEqual(r["tokens"], 110)     # 50 + 50 + the 10 that landed as it finished
        self.assertAlmostEqual(r["avg"], 110 / 3.0)   # over the three ticks that decoded
        self.assertAlmostEqual(r["peak"], 50.0)
        self.assertAlmostEqual(r["dur"], 4.0)  # from the prefill tick to the idle one
        self.assertIsNotNone(r["ended"])

    def test_prefill_figures_come_from_completed_requests(self):
        *_rest, done = self.run_request()
        p = done["prun"]
        self.assertEqual(p["p_tok"], 3000)
        self.assertAlmostEqual(p["p_time"], 1.5)
        self.assertAlmostEqual(p["p_avg"], 2000.0)
        self.assertAlmostEqual(p["p_peak"], 2000.0)

    def test_a_request_between_two_scrapes_is_still_a_run(self):
        self.tick(**self.BASE)
        self.tick(**dict(self.BASE, gen=130))  # never seen running: it began and ended
        s = self.tick(**dict(self.BASE, gen=130))
        self.assertEqual(s["run"]["tokens"], 30)
        self.assertAlmostEqual(s["run"]["peak"], s["run"]["avg"])  # no slope: avg is all

    def test_recent_reuse_and_ingest(self):
        self.tick(**dict(self.BASE, prompt_total=1000, cached_total=0))
        s = self.tick(**dict(self.BASE, prompt_total=5000, cached_total=3000))
        self.assertAlmostEqual(s["reuse_recent"]["frac"], 0.75)
        self.assertEqual(s["reuse_recent"]["tokens"], 4000)
        self.assertAlmostEqual(s["ingest"], 4000.0)
        s = self.tick(**dict(self.BASE, prompt_total=5000, cached_total=3000))
        self.assertAlmostEqual(s["ingest"], 0.0)                # nothing new arrived
        self.assertAlmostEqual(s["reuse_recent"]["frac"], 0.75)  # the last reuse is kept

    def test_lifetime_means_and_totals(self):
        s = self.tick(**dict(self.BASE, g_sum=600, g_n=3, ttft_s=1.5, ttft_n=3,
                             e2e_s=30.0, tpot_s=0.06, success=3, prompt_total=9000, pf_n=3))
        self.assertAlmostEqual(s["ttft_avg"], 0.5)
        self.assertAlmostEqual(s["e2e_avg"], 10.0)
        self.assertAlmostEqual(s["tpot_avg"], 0.02)
        self.assertAlmostEqual(s["answer_avg"], 200.0)
        self.assertEqual((s["gen_total"], s["prompt_total"], s["req_total"]), (100, 9000, 3))

    def test_a_counter_reset_is_not_a_negative_rate(self):
        self.tick(**dict(self.BASE, pf_tok=9000, pf_s=5.0, pf_n=3))
        s = self.tick(**self.BASE)             # server restarted: totals fell
        self.assertIsNone(s["pp_last"])

    def test_context_line_names_the_pool(self):
        s = self.tick(**self.BASE)
        self.assertEqual(lgt._ctx_text(s),
                         "ctx 262144/request · pool 591218 tok · 8 seqs max")


class UnreadableProcessReason(Base):
    """A process whose fdinfo cannot be read says why, and the right why."""

    @staticmethod
    def why(**kw):
        p = {"pid": "1", "name": "VLLM::Worker_TP", "model": "", "rss": 1, "read": False,
             "error": "oserror", "caps": ["cap 0", "cap_sys_ptrace"], "gpu_accel": False}
        p.update(kw)
        return "".join(t for t, _a in lgt._llama_proc_rows([p], 120)[0])

    def test_a_root_process_is_blamed_on_ownership_not_capabilities(self):
        if os.geteuid() == 0:
            self.skipTest("running as root: every process is ours")
        self.assertIn("owned by root", self.why(uid=0))

    def test_our_own_capable_binary_still_names_the_capability(self):
        self.assertIn("non-dumpable", self.why(uid=os.geteuid()))


class PcieChainInAVm(Base):
    """Inside a VM the chain's root is QEMU's port and its link is invented by QEMU.

    Measured 2026-09-29 on two passed-through R9700s: the card nodes read 32 GT/s x16,
    the emulated root ports 16 GT/s x16 (max x32), the physical slots 32 GT/s x8, and
    only the card's gpu_metrics blob carried the 8.
    """

    def tree(self, root_vendor):
        root = os.path.join(self.root, "0000:00:1c.0")
        card = os.path.join(root, "0000:01:00.0")
        os.makedirs(card)
        for path, cur, mx in ((root, ("16.0 GT/s PCIe", 16), ("16.0 GT/s PCIe", 32)),
                              (card, ("32.0 GT/s PCIe", 16), ("32.0 GT/s PCIe", 16))):
            self.write(os.path.join(path, "current_link_speed"), cur[0])
            self.write(os.path.join(path, "current_link_width"), cur[1])
            self.write(os.path.join(path, "max_link_speed"), mx[0])
            self.write(os.path.join(path, "max_link_width"), mx[1])
        self.write(os.path.join(root, "vendor"), root_vendor)
        return card

    def test_the_root_port_link_is_the_one_reported(self):
        link = lgt.pcie_chain(self.tree("0x1b36"))
        self.assertEqual((link["gts"], link["width"]), (16.0, 16))
        self.assertEqual((link["max_gts"], link["max_width"]), (16.0, 32))

    def test_a_qemu_root_port_is_flagged_virtual(self):
        self.assertTrue(lgt.pcie_chain(self.tree("0x1b36"))["virtual"])

    def test_a_real_root_port_is_not_flagged(self):
        self.assertFalse(lgt.pcie_chain(self.tree("0x8086"))["virtual"])

    def test_the_text_says_virtual_and_gives_the_firmware_width(self):
        link = lgt.pcie_chain(self.tree("0x1b36"))
        link["fw_width"] = 8
        text = lgt.pcie_text(link)
        self.assertIn("virtual root port", text)
        self.assertIn("card firmware reports x8", text)

    def test_a_plain_link_carries_no_notes(self):
        self.assertNotIn("(", lgt.pcie_text(lgt.pcie_chain(self.tree("0x8086"))))


class AmdFirmwareLinkWidth(Base):
    """gpu_metrics v1_3 pcie_link_width, offset 74."""

    def blob(self, content_rev, width):
        import struct
        d = bytearray(120)
        struct.pack_into("<HBB", d, 0, 120, 1, content_rev)
        struct.pack_into("<H", d, 74, width)
        struct.pack_into("<Q", d, 112, 0)
        card = os.path.join(self.root, f"c{content_rev}w{width}")
        os.makedirs(os.path.join(card, "device"))
        with open(os.path.join(card, "device", "gpu_metrics"), "wb") as f:
            f.write(bytes(d))
        return lgt._amd_gpu_metrics(card)

    def test_the_width_is_read(self):
        self.assertEqual(self.blob(3, 8).get("pcie_fw_width"), 8)

    def test_a_zero_width_is_not_a_width(self):
        self.assertIsNone(self.blob(3, 0).get("pcie_fw_width"))

    def test_an_unsupported_marker_is_not_a_width(self):
        self.assertIsNone(self.blob(3, 0xFFFF).get("pcie_fw_width"))

    def test_older_layouts_are_not_read_at_that_offset(self):
        self.assertNotIn("pcie_fw_width", self.blob(1, 8))


class AmdVrmTemperatures(Base):
    """gpu_metrics v1_3 temperature_vrgfx/vrsoc/vrmem, offsets 10, 12 and 14."""

    def card_with(self, vrgfx, vrsoc, vrmem):
        import struct
        d = bytearray(120)
        struct.pack_into("<HBB", d, 0, 120, 1, 3)
        struct.pack_into("<HHH", d, 10, vrgfx, vrsoc, vrmem)
        struct.pack_into("<Q", d, 112, 0)
        card = self.card(f"v{vrgfx}-{vrsoc}-{vrmem}", dev={},
                         hwmon={"temp1_label": "edge", "temp1_input": 50000})
        with open(os.path.join(card, "device", "gpu_metrics"), "wb") as f:
            f.write(bytes(d))
        return lgt.AmdGpu(card, "Fixture").sample()

    def test_all_three_regulators_join_the_temperatures(self):
        t = self.card_with(56, 57, 58)["temp"]
        self.assertEqual((t["vrm gfx"], t["vrm soc"], t["vrm mem"]), (56, 57, 58))

    def test_the_memory_regulator_is_its_own_field(self):
        self.assertEqual(self.card_with(56, 57, 58)["vrm_temp_mem"], 58)

    def test_an_unsupported_marker_is_not_a_temperature(self):
        s = self.card_with(56, 57, 0xFFFF)
        self.assertIsNone(s["vrm_temp_mem"])
        self.assertNotIn("vrm mem", s["temp"])


class AmdPowerAverage(Base):
    """Power is the mean over the refresh interval, not one read."""

    def test_the_feed_averages_what_it_sampled_and_resets(self):
        f = lgt._AmdPowerFeed(["h"])
        f._acc["h"] = [201.0 + 290.0 + 238.0, 3]
        self.assertAlmostEqual(f.take("h"), 243.0)
        self.assertIsNone(f.take("h"))

    def test_the_sample_uses_the_feed_mean(self):
        card = self.card("pf", dev={}, hwmon={"power1_average": 290000000})
        f = lgt._AmdPowerFeed([])
        g = lgt.AmdGpu(card, "Fixture", f)
        f._acc[g.hw] = [476.0, 2]
        self.assertEqual(g.sample()["power"], 238.0)

    def test_without_samples_it_reads_once(self):
        card = self.card("p1", dev={}, hwmon={"power1_average": 240000000})
        g = lgt.AmdGpu(card, "Fixture", lgt._AmdPowerFeed([]))
        self.assertEqual(g.sample()["power"], 240.0)

    def test_without_a_feed_it_reads_once(self):
        card = self.card("p0", dev={}, hwmon={"power1_average": 240000000})
        self.assertEqual(lgt.AmdGpu(card, "Fixture").sample()["power"], 240.0)


class AmdFanPercent(Base):
    """The fan percentage is rpm over the declared top speed, pwm duty only as a fallback."""

    def test_percent_of_top_speed(self):
        card = self.card("f1", dev={}, hwmon={"fan1_input": 1811, "fan1_max": 5100, "pwm1": 99})
        s = lgt.AmdGpu(card, "Fixture").sample()
        self.assertEqual((s["fan_rpm"], s["fan_pct"]), (1811, 36))

    def test_a_stopped_fan_is_zero_percent(self):
        card = self.card("f0", dev={}, hwmon={"fan1_input": 0, "fan1_max": 5100, "pwm1": 0})
        self.assertEqual(lgt.AmdGpu(card, "Fixture").sample()["fan_pct"], 0)

    def test_duty_when_no_top_speed_is_declared(self):
        card = self.card("fd", dev={}, hwmon={"fan1_input": 1811, "pwm1": 99})
        self.assertEqual(lgt.AmdGpu(card, "Fixture").sample()["fan_pct"], 39)


class AmdHeadlineSensor(Base):
    """The headline temperature says which sensor it is."""

    def test_junction_is_named_when_it_is_the_headline(self):
        card = self.card("j", dev={}, hwmon={"temp1_label": "edge", "temp1_input": 66000,
                                             "temp2_label": "junction", "temp2_input": 91000})
        s = lgt.AmdGpu(card, "Fixture").sample()
        self.assertEqual((s["temp_main"], s["temp_main_label"]), (91, "junction"))

    def test_edge_is_named_when_it_is_the_only_sensor(self):
        card = self.card("e", dev={}, hwmon={"temp1_label": "edge", "temp1_input": 66000})
        self.assertEqual(lgt.AmdGpu(card, "Fixture").sample()["temp_main_label"], "edge")


def radiance_metrics(running=0, dec=0, steps=0, step_s=0.0, ptps=0.0, dtok=0, dacc=0,
                     pos=(), gen=0, kv=0.0):
    L = 'model_name="Qwen3.8-Flash-Next",engine="0"'
    out = [f"vllm:num_requests_running{{{L}}} {running}",
           f"vllm:num_requests_waiting{{{L}}} 0",
           f"vllm:kv_cache_usage_perc{{{L}}} {kv}",
           f"vllm:generation_tokens_total{{{L}}} {gen}",
           f"vllm:gpu_prefix_cache_hit_rate{{{L}}} 0.25",
           f"radiance:decode_tokens_total{{{L}}} {dec}",
           f"radiance:engine_steps_decode_total{{{L}}} {steps}",
           f"radiance:engine_step_seconds_decode_total{{{L}}} {step_s}",
           f"radiance:prefill_tokens_per_second{{{L}}} {ptps}",
           f"radiance:draft_tokens_total{{{L}}} {dtok}",
           f"radiance:draft_accepted_total{{{L}}} {dacc}",
           f"radiance:linear_prefix_cache_hit_rate{{{L}}} 0.5"]
    out += [f'radiance:draft_position_acceptance_ratio{{{L},position="{i}"}} {v}'
            for i, v in enumerate(pos)]
    return "\n".join(out) + "\n"


RADIANCE_STATS = json.dumps({
    "version": "1.0.6", "uptime_s": 290, "kv_tokens_total": 486464, "kv_tokens_used": 48646,
    "kv_tokens_cached": 1200,
    "experts": {"routed": 1000, "routed_res": 960, "promotions": 12, "demotions": 3,
                "tiers": [{"name": "vram", "bytes": 27299543040}, {"name": "pinned", "bytes": 4479298560}]},
    "link": {"world": 2, "ar_calls": 100, "h2d_bytes": 0, "d2h_bytes": 0}})


class RadianceCommandLine(Base):
    """What a radiance command line says, read without a process running."""

    CMD = ["radiance", "--model", "/models/qwen3.8-next-flash-fp8-iq4r-moe.rad", "--host", "::",
           "--port", "8080", "--api-key", "secret123", "--tp", "2", "--tp-wire", "wht6",
           "--max-num-seqs", "8", "--max-model-len", "200000", "--placement", "expert_tiered",
           "--num-speculative-tokens", "3", "--served-model-name", "Qwen3.8-Flash-Next",
           "--reasoning-effort", "xhigh"]

    def test_port_host_and_served_name(self):
        srv = lgt.radiance_from_cmd(self.CMD, 7)
        self.assertEqual((srv["port"], srv["host"], srv["model_hint"], srv["kind"], srv["flavor"]),
                         ("8080", "127.0.0.1", "Qwen3.8-Flash-Next", "radiance", "radiance"))

    def test_container_name_without_served_name(self):
        srv = lgt.radiance_from_cmd(["radiance", "--model", "/m/flash.rad"])
        self.assertEqual((srv["port"], srv["model_hint"]), ("8000", "flash"))

    def test_the_container_init_wrapper_is_not_the_server(self):
        self.assertTrue(lgt._is_radiance("radiance", self.CMD))
        self.assertFalse(lgt._is_radiance("docker-init", ["/sbin/docker-init", "--"] + self.CMD))

    def test_speculation_depth_off_and_auto(self):
        self.assertEqual(lgt.radiance_spec_from_cmd(self.CMD), ("mtp", "in container", "3"))
        self.assertEqual(lgt.radiance_spec_from_cmd(["radiance", "--model", "m"]),
                         ("mtp", "in container", "auto"))
        self.assertEqual(lgt.radiance_spec_from_cmd(["radiance", "--num-speculative-tokens", "0"]),
                         (None, None, None))

    def test_config_panel_groups_and_masks(self):
        cfg = dict((g, dict(rows)) for g, rows in
                   lgt.settings_from_cmd(self.CMD, lgt._RADIANCE_FLAG_GROUPS).items())
        self.assertEqual(cfg["parallel"]["wire"], "wht6")
        self.assertEqual(cfg["memory"]["placement"], "expert_tiered")
        self.assertEqual(cfg["sampling"]["reasoning"], "xhigh")
        self.assertNotIn("secret123", cfg["server"]["api-key"])
        self.assertNotIn("other", cfg)

    def test_expected_tokens_per_step(self):
        # 1 + 0.7 + 0.7*0.6 + 0.7*0.6*0.5
        self.assertAlmostEqual(lgt.RadianceProbe._tokens_per_step([0.7, 0.6, 0.5]), 2.33)
        self.assertEqual(lgt.RadianceProbe._tokens_per_step([]), 1.0)


class RadianceProbeRates(Base):
    """The probe against a scripted /metrics + /stats sequence and a fake clock."""

    def setUp(self):
        super().setUp()
        self._time = lgt.time
        self.clock = FakeClock()
        lgt.time = self.clock
        self.pr = lgt.RadianceProbe("8080")
        self.pr._cmdline = lambda: RadianceCommandLine.CMD
        self.pr._STATS_TTL = 0.0
        self.body = ""
        self.paths = []

        def get(path, timeout=2.5):
            self.paths.append(path)
            if path == "/v1/models":
                return '{"data":[{"id":"Qwen3.8-Flash-Next"}]}'
            if path == "/stats":
                return RADIANCE_STATS
            return self.body
        self.pr._get = get

    def tearDown(self):
        lgt.time = self._time
        super().tearDown()

    def tick(self, dt=1.0, **kw):
        self.clock.t += dt
        self.body = radiance_metrics(**kw)
        return self.pr.sample()

    def test_live_rate_is_the_engine_decode_counter_not_the_vllm_one(self):
        # vllm:generation_tokens_total stays flat until the request ends, as measured
        self.tick(running=0, dec=100, steps=10, step_s=0.2)
        self.tick(running=1, dec=100, steps=10, step_s=0.2, ptps=6900)      # prefill
        g1 = self.tick(running=1, dec=240, steps=60, step_s=1.0)
        g2 = self.tick(running=1, dec=380, steps=110, step_s=1.8)
        self.assertEqual(g2["phase"], "generating")
        self.assertAlmostEqual(g2["tg"], 140.0)

    def test_prefill_is_live_only_while_prefilling(self):
        self.tick(running=0, dec=100)
        pre = self.tick(running=1, dec=100, ptps=6955.0)
        self.assertEqual(pre["phase"], "prefill")
        self.assertAlmostEqual(pre["pp"], 6955.0)
        # the gauge decays for seconds after the work stops: idle must not read it as live
        idle = self.tick(running=0, dec=150, ptps=719.0)
        self.assertIsNone(idle["pp"])
        self.assertAlmostEqual(idle["pp_last"], 6955.0)

    def test_step_time_is_decode_seconds_over_decode_steps(self):
        self.tick(running=1, dec=100, steps=100, step_s=1.5)
        s = self.tick(running=1, dec=200, steps=150, step_s=2.4)
        self.assertAlmostEqual(s["step_ms"], 18.0)

    def test_speculation_from_the_engine_counters(self):
        s = self.tick(dtok=300, dacc=150, pos=(0.7, 0.6, 0.5))
        self.assertAlmostEqual(s["spec"], 0.5)
        self.assertEqual(s["spec_pos"], [0.7, 0.6, 0.5])
        self.assertAlmostEqual(s["tok_step"], 2.33)
        self.assertEqual((s["spec_type"], s["spec_nmax"]), ("mtp", "3"))

    def test_stats_fill_pool_held_kv_and_the_expert_plane(self):
        s = self.tick()
        self.assertEqual((s["kv_pool"], s["kv_used"], s["kv_held"]), (486464, 48646, 1200))
        self.assertAlmostEqual(s["kv"], 48646 / 486464)
        self.assertAlmostEqual(s["experts"]["resident_hit"], 0.96)
        self.assertEqual(s["experts"]["tiers"]["vram"], 27299543040)
        self.assertEqual((s["engine_version"], s["uptime_s"]), ("1.0.6", 290))

    def test_context_and_reasoning_come_from_the_command_line(self):
        s = self.tick()
        self.assertEqual((s["model"], s["ctx"], s["slots"]), ("Qwen3.8-Flash-Next", 200000, 8))
        self.assertEqual(s["reasoning_format"], "effort xhigh")

    def test_reuse_rates_are_the_published_gauges(self):
        s = self.tick()
        self.assertAlmostEqual(s["reuse"], 0.25)
        self.assertAlmostEqual(s["linear_reuse"], 0.5)

    # ---- the pairs: absent stays absent, zero stays zero
    def test_no_drafting_yet_is_not_zero_acceptance(self):
        s = self.tick(dtok=0, dacc=0)
        self.assertIsNone(s["spec"])
        self.assertIsNone(s["tok_step"])

    def test_an_auth_failure_reads_as_metrics_off_not_as_zeros(self):
        self.clock.t += 1
        self.body = '{"error":{"message":"invalid api key"}}'
        s = self.pr.sample()
        self.assertTrue(s["metrics_off"])
        self.assertIsNone(s["tg"])

    def test_fleet_builds_a_radiance_probe_for_a_radiance_server(self):
        srv = lgt.radiance_from_cmd(RadianceCommandLine.CMD, 7)
        saved = (lgt.discover_llama_servers, lgt.RadianceProbe.sample, dict(lgt._probes))
        try:
            lgt._probes.clear()
            lgt.discover_llama_servers = lambda: [srv]
            lgt.RadianceProbe.sample = lambda self: {"alive": True}
            out = lgt.sample_llama_fleet()
            self.assertIs(type(lgt._probes["8080"]), lgt.RadianceProbe)
            self.assertEqual((out[0]["flavor"], out[0]["port"]), ("radiance", "8080"))
        finally:
            lgt.discover_llama_servers, lgt.RadianceProbe.sample = saved[0], saved[1]
            lgt._probes.clear(); lgt._probes.update(saved[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
