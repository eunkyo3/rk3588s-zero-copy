#!/usr/bin/env python3
"""Tests for tools/m7_probe.py, the driver of M7's probe of the output bindings (prereg/M7.md §3): the
conversion of each layout and type to configuration 1's NCHW INT8, the comparison rule, the rotation and the
statistics of the cost, and the driver's handling of its processes, with a stand-in for build/m7_output_probe.
Standard library only.

Usage: python3 tests/test_m7_probe.py
"""
import csv
import math
import subprocess
import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import m7_probe as mp  # noqa: E402

PAD = 0x55  # what padding channels and padded rows hold: never read


def attr(fmt, type_, dims, zp=0, scale=1.0, w_stride=0):
    return {"fmt": fmt, "type": type_, "dims": list(dims), "zp": zp, "scale": mp.f32(scale), "w_stride": w_stride,
            "ext": "i8" if type_ == "INT8" else "f32"}


def index(shape, b, ch, y, x):
    n, c, h, w = shape
    return ((b * c + ch) * h + y) * w + x


def positions(shape):
    n, c, h, w = shape
    return [(b, ch, y, x) for b in range(n) for ch in range(c) for y in range(h) for x in range(w)]


def nhwc(vals, shape, cs=None, ws=None, pad=PAD):
    """NCHW values laid out as NHWC, with cs channels a pixel (padding beyond C) and ws pixels a row."""
    n, c, h, w = shape
    cs, ws = cs or c, ws or w
    out = [pad] * (n * h * ws * cs)
    for b, ch, y, x in positions(shape):
        out[((b * h + y) * ws + x) * cs + ch] = vals[index(shape, b, ch, y, x)]
    return out


def nc1hwc2(vals, shape, c2, c1=None, ws=None, pad=PAD):
    """NCHW values laid out as NC1HWC2: channel c in block c // c2 at c % c2, padding channels after C."""
    n, c, h, w = shape
    c1, ws = c1 or -(-c // c2), ws or w
    out = [pad] * (n * c1 * h * ws * c2)
    for b, ch, y, x in positions(shape):
        out[((((b * c1 + ch // c2) * h + y) * ws + x) * c2 + ch % c2)] = vals[index(shape, b, ch, y, x)]
    return out


def floats(values):
    return array("f", values).tobytes()


def signed(b):
    return b - 256 if b > 127 else b


def dequantized(q, zp, scale):
    return [(signed(b) - zp) * mp.f32(scale) for b in q]


class Conversion(unittest.TestCase):
    SHAPE = (1, 3, 2, 4)
    VALS = bytes(range(24))

    def test_nchw_with_the_querys_dims_is_read_as_it_is(self):
        data = self.VALS + bytes([PAD] * 8)  # memory beyond the tensor: size_with_stride above size
        self.assertEqual(mp.to_nchw(data, attr("NCHW", "INT8", self.SHAPE), self.SHAPE), self.VALS)

    def test_nhwc_with_the_querys_dims_is_a_permutation_of_the_axes(self):
        data = bytes(nhwc(self.VALS, self.SHAPE))
        self.assertNotEqual(data, self.VALS)
        self.assertEqual(mp.to_nchw(data, attr("NHWC", "INT8", self.SHAPE), self.SHAPE), self.VALS)

    def test_native_nhwc_drops_padding_channels_and_reads_the_row_stride(self):
        data = bytes(nhwc(self.VALS, self.SHAPE, cs=5, ws=6))
        a = attr("NHWC", "INT8", (1, 2, 4, 5), w_stride=6)
        self.assertEqual(mp.to_nchw(data, a, self.SHAPE), self.VALS)

    def test_nc1hwc2_channels_put_back_with_the_padding_channels_dropped(self):
        # C = 3 is not a multiple of the block, C2 = 2: block 1 holds channel 2 and one padding channel.
        data = bytes(nc1hwc2(self.VALS, self.SHAPE, c2=2))
        self.assertEqual(len(data), 1 * 2 * 2 * 4 * 2)
        self.assertEqual(mp.to_nchw(data, attr("NC1HWC2", "INT8", (1, 2, 2, 4, 2)), self.SHAPE), self.VALS)

    def test_nc1hwc2_as_the_board_gives_a_one_channel_output(self):
        # Output 2 of the model (docs/notes/spike/spike_run.log): NCHW 1x1x80x80, native 1x2x80x80x16.
        shape = (1, 1, 5, 7)
        vals = bytes((i * 37) % 256 for i in range(35))
        data = bytes(nc1hwc2(vals, shape, c2=16, c1=2))
        self.assertEqual(mp.to_nchw(data, attr("NC1HWC2", "INT8", (1, 2, 5, 7, 16)), shape), vals)

    def test_nc1hwc2_with_a_row_stride(self):
        data = bytes(nc1hwc2(self.VALS, self.SHAPE, c2=2, ws=8))
        a = attr("NC1HWC2", "INT8", (1, 2, 2, 4, 2), w_stride=8)
        self.assertEqual(mp.to_nchw(data, a, self.SHAPE), self.VALS)

    def test_float32_nhwc(self):
        vals = [float(v) / 4 for v in range(24)]
        got = mp.to_nchw(floats(nhwc(vals, self.SHAPE, pad=1e9)), attr("NHWC", "FLOAT32", self.SHAPE), self.SHAPE)
        self.assertEqual(list(got), vals)

    def test_what_cannot_be_read_is_a_mismatch(self):
        cases = [
            (bytes(nc1hwc2(self.VALS, self.SHAPE, c2=2)), attr("NC1HWC2", "INT8", (1, 1, 2, 4, 2))),  # 2 < C
            (bytes(nhwc(self.VALS, self.SHAPE)), attr("NHWC", "INT8", (1, 4, 2, 3))),  # not N, H, W, C'
            (self.VALS[:20], attr("NCHW", "INT8", self.SHAPE)),  # too few bytes
            (bytes(48), attr("NCHW", "FLOAT16", self.SHAPE)),  # no conversion in the rule
            (self.VALS, attr("NCHW", "INT8", (1, 3, 8))),  # NCHW with other dims
        ]
        for data, a in cases:
            with self.subTest(a=a), self.assertRaises(mp.Mismatch):
                mp.to_nchw(data, a, self.SHAPE)

    def test_configuration_1_must_be_nchw_int8(self):
        self.assertEqual(mp.logical_shape(attr("NCHW", "INT8", self.SHAPE)), self.SHAPE)
        for a in (attr("NHWC", "INT8", self.SHAPE), attr("NCHW", "FLOAT32", self.SHAPE),
                  attr("NC1HWC2", "INT8", (1, 2, 2, 4, 2))):
            with self.subTest(a=a), self.assertRaises(mp.Defect):
                mp.logical_shape(a)


class Comparison(unittest.TestCase):
    Q = bytes([0x80, 0xFD, 0x00, 0x7F, 0x10])  # -128, -3, 0, 127, 16

    def test_int8_values_must_be_equal(self):
        self.assertIsNone(mp.compare_values(self.Q, 0, 1.0, bytearray(self.Q)))
        off = bytearray(self.Q)
        off[2] = 1
        self.assertEqual(mp.compare_values(self.Q, 0, 1.0, off)[0], 1)
        self.assertIn("1 of 5 values", mp.compare_values(self.Q, 0, 1.0, off)[1])

    def test_int8_difference_is_between_signed_values(self):
        off = bytearray(self.Q)
        off[0] = 0x7F  # -128 read as 127
        self.assertEqual(mp.compare_values(self.Q, 0, 1.0, off)[0], 255)

    def test_float32_at_exactly_half_a_scale_step_is_accepted(self):
        zp, scale = -3, 0.5
        base = dequantized(self.Q, zp, scale)
        for sign in (1, -1):
            got = array("f", [v + sign * scale / 2 for v in base])
            self.assertEqual(list(got), [v + sign * 0.25 for v in base])  # exact in float32
            self.assertIsNone(mp.compare_values(self.Q, zp, mp.f32(scale), got))

    def test_float32_just_above_half_a_scale_step_is_refused(self):
        zp, scale = -3, 0.5
        got = array("f", dequantized(self.Q, zp, scale))
        got[1] = 0.25 + 2 ** -20  # q = zp: 0, and a little more than half the scale away
        found = mp.compare_values(self.Q, zp, mp.f32(scale), got)
        self.assertAlmostEqual(found[0], 0.25 + 2 ** -20, places=12)
        self.assertIn("1 of 5 values", found[1])

    def test_float32_with_the_models_scale(self):
        zp, scale = -58, mp.f32(0.117659)  # output 0's (spike_run.log)
        got = array("f", dequantized(self.Q, zp, scale))
        self.assertIsNone(mp.compare_values(self.Q, zp, scale, got))
        got[4] += scale  # one step off
        self.assertIsNotNone(mp.compare_values(self.Q, zp, scale, got))

    def test_float32_nan_is_refused(self):
        got = array("f", dequantized(self.Q, 0, 1.0))
        got[3] = math.nan
        found = mp.compare_values(self.Q, 0, 1.0, got)
        self.assertTrue(math.isnan(found[0]))

    def test_a_different_count_is_refused(self):
        self.assertEqual(mp.compare_values(self.Q, 0, 1.0, self.Q[:4])[0], math.inf)


def frame_values(shape, frame, k):
    n = math.prod(shape)
    return bytes((i * 7 + frame * 13 + k * 3) % 256 for i in range(n))


class CompareDumps(unittest.TestCase):
    SHAPES = [(1, 3, 2, 4), (1, 1, 2, 3)]
    REF = [attr("NCHW", "INT8", (1, 3, 2, 4), zp=-5, scale=0.5), attr("NCHW", "INT8", (1, 1, 2, 3), zp=-128,
                                                                         scale=0.25)]

    def ref_bytes(self, r, k):
        return frame_values(self.SHAPES[k], r % 2, k)

    def test_native_and_float_layouts_agree(self):
        native = [attr("NC1HWC2", "INT8", (1, 2, 2, 4, 2)), attr("NC1HWC2", "INT8", (1, 2, 2, 3, 16))]
        c2 = [2, 16]
        self.assertIsNone(mp.compare_dumps(self.REF, native, 4, self.ref_bytes, lambda r, k: bytes(
            nc1hwc2(self.ref_bytes(r, k), self.SHAPES[k], c2=c2[k], c1=2))))
        f = [attr("NHWC", "FLOAT32", s) for s in self.SHAPES]
        self.assertIsNone(mp.compare_dumps(self.REF, f, 4, self.ref_bytes, lambda r, k: floats(nhwc(dequantized(
            self.ref_bytes(r, k), self.REF[k]["zp"], self.REF[k]["scale"]), self.SHAPES[k]))))

    def test_the_reason_names_the_output_the_run_and_the_largest_difference(self):
        def got(r, k):
            b = bytearray(self.ref_bytes(r, k))
            if (r, k) == (1, 0):
                b[3] = (b[3] + 2) % 256
            if (r, k) == (2, 1):
                b[0] = (b[0] + 5) % 256
            return bytes(b)
        reason = mp.compare_dumps(self.REF, self.REF, 3, self.ref_bytes, got)
        self.assertIn("2 of 6 outputs differ", reason)
        self.assertIn("the first, output 0 in run 1: largest difference 2 in 1 of 24 values", reason)
        self.assertIn("the largest difference, 5, in output 1 in run 2", reason)

    def test_a_stale_output_is_a_difference(self):
        reason = mp.compare_dumps(self.REF, self.REF, 2, self.ref_bytes, lambda r, k: self.ref_bytes(0, k))
        self.assertIn("output 0 in run 1", reason)

    def test_an_unreadable_layout_or_another_output_count(self):
        bad = [attr("NC1HWC2", "INT8", (1, 1, 2, 4, 2)), self.REF[1]]
        self.assertIn("output 0: NC1HWC2", mp.compare_dumps(self.REF, bad, 1, self.ref_bytes, self.ref_bytes))
        self.assertIn("1 outputs, configuration 1 has 2",
                      mp.compare_dumps(self.REF, self.REF[:1], 1, self.ref_bytes, self.ref_bytes))

    def test_the_reference_must_hold_per_frame_and_differ_between_frames(self):
        good = lambda r, k: frame_values(self.SHAPES[k], r % 2, k)  # noqa: E731
        self.assertIsNone(mp.reference_problem(40, good, 2))
        self.assertIsNone(mp.reference_problem(1, lambda r, k: b"", 2))
        stale = lambda r, k: frame_values(self.SHAPES[k], 0 if r == 5 else r % 2, k)  # noqa: E731
        self.assertIn("run 5 differs from its run 1", mp.reference_problem(40, stale, 2))
        same = lambda r, k: frame_values(self.SHAPES[k], 0, k)  # noqa: E731
        self.assertEqual(mp.reference_problem(40, same, 2), "the two frames give the same outputs")


class Cost(unittest.TestCase):
    def test_rotation_starts_round_r_with_the_r_mod_n_th(self):
        accepted = [1, 2, 4, 6]
        self.assertEqual(mp.rotation(accepted, 0), [1, 2, 4, 6])
        self.assertEqual(mp.rotation(accepted, 1), [2, 4, 6, 1])
        self.assertEqual(mp.rotation(accepted, 3), [6, 1, 2, 4])
        self.assertEqual(mp.rotation(accepted, 4), [1, 2, 4, 6])
        self.assertEqual(mp.rotation(accepted, 14), [4, 6, 1, 2])
        firsts = [mp.rotation(accepted, r)[0] for r in range(15)]
        self.assertEqual({c: firsts.count(c) for c in accepted}, {1: 4, 2: 4, 4: 4, 6: 3})
        self.assertEqual(mp.rotation([1], 7), [1])

    def test_a_rounds_value_is_the_mean_per_run(self):
        rows = [{"thread_cpu_ns": "1000000", "process_cpu_ns": "2500000"},
                {"thread_cpu_ns": "3000000", "process_cpu_ns": "3500000"}]
        self.assertEqual(mp.round_means(rows), (2.0, 3.0))
        with self.assertRaises(ValueError):
            mp.round_means([])

    def test_median_and_range_over_the_rounds(self):
        self.assertEqual(mp.summary([3.0, 1.0, 2.0]), (2.0, 1.0, 3.0))
        self.assertEqual(mp.summary([4.0, 1.0, 2.0, 3.0]), (2.5, 1.0, 4.0))

    def test_cost_columns_and_the_difference_from_configuration_1(self):
        ref = [(1.0, 2.0), (1.2, 2.2), (1.1, 2.1)]
        mine = [(1.5, 3.0), (1.7, 3.4), (1.6, 3.2)]
        self.assertEqual(mp.cost_columns(mine, ref),
                         ["3", "1.6000", "1.5000", "1.7000", "3.2000", "3.0000", "3.4000", "0.5000", "1.1000"])
        self.assertEqual(mp.cost_columns(ref, ref)[-2:], ["0.0000", "0.0000"])
        self.assertEqual(mp.cost_columns([], ref), [""] * 9)
        self.assertEqual(mp.cost_columns(mine, None)[-2:], ["", ""])


def args(**kw):
    a = {"run_kind": "check", "freeze_m7_commit": "none", "keep_dumps": False}
    a.update(kw)
    return mock.Mock(**a)


class Processes(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())

    def start(self, side_effect):
        with mock.patch.object(mp.subprocess, "run", side_effect=side_effect):
            return mp.start("dump", 5, self.t / "d", args())

    def test_a_signal_refuses(self):
        why = self.start(lambda cmd, **kw: subprocess.CompletedProcess(cmd, -11))
        self.assertEqual(why, "killed by SIGSEGV")

    def test_an_exit_code_refuses_with_the_tools_reason(self):
        def tool(cmd, **kw):
            Path(cmd[3], "refused.csv").write_text(
                "# start_time_utc: 2026-11-26T00:00:00Z\nkey,value\nexit_code,5\nstage,run\n"
                "reason,rknn_run returned -1\nrun,0\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 5)
        self.assertEqual(self.start(tool), "exit code 5 (rknn_run failed): rknn_run returned -1 in run 0")

    def test_a_failure_of_the_tool_gives_its_last_message(self):
        def tool(cmd, stdout=None, **kw):
            stdout.write("bench: cannot read models/yolov8n_i8.rknn\n")
            return subprocess.CompletedProcess(cmd, 1)
        self.assertEqual(self.start(tool),
                         "exit code 1 (a failure of the tool): bench: cannot read models/yolov8n_i8.rknn")

    def test_a_timeout_refuses(self):
        def tool(cmd, timeout=None, **kw):
            raise subprocess.TimeoutExpired(cmd, timeout)
        self.assertEqual(self.start(tool), f"timed out after {mp.DUMP_TIMEOUT_S} s")

    def test_success(self):
        self.assertIsNone(self.start(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0)))

    def test_the_command_line(self):
        seen = []

        def tool(cmd, **kw):
            seen.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(mp.subprocess, "run", side_effect=tool):
            mp.start("time", 3, self.t / "t", args(run_kind="m7", freeze_m7_commit="f" * 40))
            mp.start("dump", 1, self.t / "s", args(), runs=1)
        self.assertEqual(seen[0][0], [str(mp.BINARY), "time", "3", str(self.t / "t"), "--run-kind", "m7",
                                      "--freeze-m7-commit", "f" * 40])
        self.assertEqual(seen[0][1]["timeout"], mp.TIME_TIMEOUT_S)
        self.assertEqual(seen[0][1]["cwd"], mp.ROOT)
        self.assertEqual(seen[1][0][-2:], ["--runs", "1"])


SHAPES = [(1, 3, 2, 4), (1, 1, 2, 3)]
QUANT = [(-5, 0.5), (-128, 0.25)]
ATTR_COLUMNS = ("output,name,n_dims,dims,n_elems,size,size_with_stride,w_stride,h_stride,fmt,type,qnt_type,zp,"
                "scale,queried_fmt,queried_type,bytes,ext")


class FakeTool:
    """A stand-in for build/m7_output_probe. behaviour: {config: "ok", "off" (one value a scale step off in the
    last run), ("exit", code), ("signal", n), "time-fails-in-round-<r>"}; a configuration's layout is its
    binding's, on two small outputs."""

    def __init__(self, behaviour, frames_equal=False):
        self.behaviour, self.frames_equal = behaviour, frames_equal
        self.calls = []

    def layout(self, config, k, q):
        """(fmt, type, dims, w_stride, bytes) of output k holding the INT8 values q, as configuration config
        binds it."""
        shape, (zp, scale) = SHAPES[k], QUANT[k]
        n, c, h, w = shape
        if config == 1:
            return "NCHW", "INT8", shape, 0, q + bytes([PAD] * 6)  # size_with_stride above size
        if config == 2:
            return "NHWC", "INT8", shape, 0, bytes(nhwc(q, shape))
        if config in (3, 4):
            f = dequantized(q, zp, scale)
            f = [v + 0.2 * scale for v in f]  # within half a step
            return (("NCHW", "FLOAT32", shape, 0, floats(f)) if config == 3 else
                    ("NHWC", "FLOAT32", shape, 0, floats(nhwc(f, shape, pad=1e9))))
        if config == 5:
            return "NC1HWC2", "INT8", (n, 2, h, w, 16), 0, bytes(nc1hwc2(q, shape, c2=16, c1=2))
        return "NHWC", "INT8", (n, h, w, 4), 0, bytes(nhwc(q, shape, cs=4))  # 6: native NHWC, C padded to 4

    def __call__(self, cmd, cwd=None, stdout=None, stderr=None, timeout=None, **kw):
        if cmd[0] != str(mp.BINARY):  # timedatectl
            return subprocess.CompletedProcess(cmd, 0, stdout="yes\n")
        mode, config, out = cmd[1], int(cmd[2]), Path(cmd[3])
        runs = int(cmd[cmd.index("--runs") + 1]) if "--runs" in cmd else (40 if mode == "dump" else 20)
        self.calls.append((mode, config, out.name, runs))
        b = self.behaviour.get(config, "ok")
        if isinstance(b, tuple) and b[0] == "signal":
            return subprocess.CompletedProcess(cmd, -b[1])
        if isinstance(b, tuple) and b[0] == "exit":
            (out / "refused.csv").write_text(f"key,value\nexit_code,{b[1]}\nstage,run\nreason,rknn_run returned -1\n"
                                             "run,0\n", encoding="utf-8")
            stdout.write("E RKNN: Meet unsupported tensor dtype\n")
            return subprocess.CompletedProcess(cmd, b[1])
        head = "# start_time_utc: 2026-11-26T00:00:00Z\n# tool: fake\n"
        if mode == "time":
            r = int(out.name[1:3])
            if b == f"time-fails-in-round-{r}":
                return subprocess.CompletedProcess(cmd, -11)
            thread, process = config * 1000000 + r * 1000, config * 2000000 + r * 2000  # ns per run
            rows = "".join(f"{i},{i % 2},{thread},{process},{config * 3000000}\n" for i in range(runs))
            (out / "time.csv").write_text(head + "run,frame,thread_cpu_ns,process_cpu_ns,wall_ns\n" + rows,
                                          encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)
        lines = []
        for r in range(runs):
            for k in range(len(SHAPES)):
                q = bytearray(frame_values(SHAPES[k], 0 if self.frames_equal else r % 2, k))
                if b == "off" and r == runs - 1 and k == 1:
                    q[2] = (q[2] + 1) % 256
                fmt, type_, dims, ws, data = self.layout(config, k, bytes(q))
                ext = "i8" if type_ == "INT8" else "f32"
                (out / f"run{r:02d}_out{k}.{ext}").write_bytes(data)
                if r == 0:
                    zp, scale = QUANT[k]
                    lines.append(f"{k},o{k},{len(dims)},{'x'.join(map(str, dims))},{math.prod(SHAPES[k])},0,0,{ws},0,"
                                 f"{fmt},{type_},AFFINE_ASYMMETRIC,{zp},{scale:.9g},NCHW,INT8,{len(data)},{ext}")
        (out / "attrs.csv").write_text(head + ATTR_COLUMNS + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)


FREEZE_M7 = "0123456789abcdef0123456789abcdef01234567"  # a full commit SHA, as freeze-m7's would be


def read_probe(out):
    lines = [line for line in (out / "probe.csv").read_text(encoding="utf-8").splitlines() if not line.startswith("# ")]
    return {int(r["config"]): r for r in csv.DictReader(lines)}


class Driver(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.out = self.t / "probe"

    def run_probe(self, tool, *extra):
        with mock.patch.object(mp.subprocess, "run", side_effect=tool):
            return mp.main(["run", "--out", str(self.out), *extra])

    def run_full(self, tool, *extra):
        """The whole probe: run kind m7, after freeze-m7 (M7 §8 step 3)."""
        return self.run_probe(tool, "--run-kind", "m7", "--freeze-m7-commit", FREEZE_M7, *extra)

    def test_the_whole_probe(self):
        tool = FakeTool({4: "off", 5: ("exit", 5)})
        self.assertEqual(self.run_full(tool), 0)
        rows = read_probe(self.out)
        self.assertEqual({c: r["outcome"] for c, r in rows.items()},
                         {1: "accepted", 2: "accepted", 3: "accepted", 4: "refused", 5: "refused", 6: "accepted"})
        self.assertIn("1 of 80 outputs differ", rows[4]["reason"])
        self.assertIn("output 1 in run 39", rows[4]["reason"])
        self.assertIn("half the scale", rows[4]["reason"])
        self.assertEqual(rows[5]["reason"], "exit code 5 (rknn_run failed): rknn_run returned -1 in run 0")
        self.assertEqual([c[:2] for c in tool.calls[:6]], [("dump", k) for k in range(1, 7)])
        self.assertTrue(all(c[3] == 40 for c in tool.calls[:6]))
        timed = [c for c in tool.calls if c[0] == "time"]
        self.assertEqual(len(timed), 15 * 4)
        self.assertEqual([c[1] for c in timed[:8]], [1, 2, 3, 6, 2, 3, 6, 1])
        self.assertEqual(timed[-1][2], "r14_config2")  # round 14 starts with the (14 mod 4)-th: 3
        # thread CPU in round r: config ms + r µs; the median is round 7's.
        self.assertEqual(rows[3]["thread_cpu_ms_median"], "3.0070")
        self.assertEqual((rows[3]["thread_cpu_ms_min"], rows[3]["thread_cpu_ms_max"]), ("3.0000", "3.0140"))
        self.assertEqual(rows[3]["process_cpu_ms_median"], "6.0140")
        self.assertEqual((rows[6]["thread_cpu_ms_median_minus_config1"],
                          rows[6]["process_cpu_ms_median_minus_config1"]), ("5.0000", "10.0000"))
        self.assertEqual(rows[1]["thread_cpu_ms_median_minus_config1"], "0.0000")
        self.assertEqual(rows[4]["thread_cpu_ms_median"], "")
        self.assertFalse(list(self.out.glob("dump/*/run*")))  # the dumps are removed once compared
        self.assertTrue((self.out / "dump" / "config2" / "attrs.csv").exists())
        self.assertTrue((self.out / "time" / "r00_config1" / "time.csv").exists())
        header = (self.out / "probe.csv").read_text(encoding="utf-8")
        for key in ("start_time_utc", "ntp_synchronized: yes", f"prereg_m7_commit: {mp.PREREG_M7_COMMIT}",
                    f"freeze_m7_commit: {FREEZE_M7}", "run_kind: m7", "model_sha256", "code_commit", "amendment",
                    "tool: m7_probe", "configuration: 1-6"):
            self.assertIn(f"# {key}", header)
        rounds = (self.out / "rounds.csv").read_text(encoding="utf-8")
        self.assertIn("1,0,2,2.0010,4.0020,20,ok", rounds)

    def test_a_configuration_whose_time_process_fails_is_refused_from_then_on(self):
        tool = FakeTool({3: "time-fails-in-round-2"})
        self.assertEqual(self.run_full(tool), 0)
        rows = read_probe(self.out)
        self.assertEqual(rows[3]["outcome"], "refused")
        self.assertEqual(rows[3]["reason"], "round 2: killed by SIGSEGV")
        self.assertEqual(rows[3]["thread_cpu_ms_median"], "")
        timed = [c for c in tool.calls if c[0] == "time"]
        self.assertEqual(sum(c[1] == 3 for c in timed), 3)  # rounds 0, 1 and 2
        self.assertEqual(sum(c[1] == 2 for c in timed), 15)
        self.assertEqual(rows[2]["rounds"], "15")
        # Round 3 keeps the rotation of the six: it starts with the 3rd, configuration 4.
        r3 = [c[1] for c in timed if c[2].startswith("r03_")]
        self.assertEqual(r3, [4, 5, 6, 1, 2])

    def test_keep_dumps(self):
        self.assertEqual(self.run_full(FakeTool({}), "--keep-dumps"), 0)
        self.assertEqual(len(list(self.out.glob("dump/config3/run*_out*.f32"))), 40 * 2)

    def test_configuration_1_failing_is_a_defect(self):
        tool = FakeTool({1: ("signal", 11)})
        self.assertEqual(self.run_full(tool), 1)
        rows = read_probe(self.out)
        self.assertEqual(rows[1]["outcome"], "refused")
        self.assertIn("killed by SIGSEGV", rows[1]["reason"])
        self.assertIn("defect", rows[1]["reason"])
        self.assertEqual({rows[c]["outcome"] for c in range(2, 7)}, {"not run"})
        self.assertEqual(len(tool.calls), 1)

    def test_configuration_1_whose_frames_agree_is_a_defect(self):
        self.assertEqual(self.run_full(FakeTool({}, frames_equal=True)), 1)
        self.assertIn("the two frames give the same outputs", read_probe(self.out)[1]["reason"])

    def test_configuration_1_failing_in_a_round_is_a_defect(self):
        tool = FakeTool({1: "time-fails-in-round-4"})
        self.assertEqual(self.run_full(tool), 1)
        rows = read_probe(self.out)
        self.assertIn("configuration 1 in round 4", rows[1]["reason"])
        self.assertEqual(rows[2]["thread_cpu_ms_median"], "")  # the probe stopped: no cost

    def test_the_smoke_before_freeze(self):
        tool = FakeTool({})
        self.assertEqual(self.run_probe(tool, "--only-config-1-one-frame"), 0)
        self.assertEqual(tool.calls, [("dump", 1, "config1", 1)])
        rows = read_probe(self.out)
        self.assertEqual(rows[1]["outcome"], "ran")
        self.assertEqual({rows[c]["outcome"] for c in range(2, 7)}, {"not run"})
        self.assertIn("# configuration: 1, one run on one frame", (self.out / "probe.csv").read_text(encoding="utf-8"))

    def test_arguments(self):
        tool = FakeTool({})
        self.assertEqual(self.run_probe(tool, "--run-kind", "m7"), 2)
        self.assertEqual(self.run_probe(tool, "--freeze-m7-commit", "abc"), 2)
        self.assertEqual(self.run_probe(tool, "--run-kind", "m7", "--freeze-m7-commit", "a" * 40,
                                        "--only-config-1-one-frame"), 2)
        self.assertEqual(tool.calls, [])
        self.out.mkdir()
        (self.out / "x").write_text("", encoding="utf-8")
        self.assertEqual(self.run_full(tool), 2)  # not into a directory that holds something
        self.assertEqual(tool.calls, [])

    def test_the_whole_probe_with_run_kind_check_is_refused(self):
        # M7 §8 step 3: "The probe is not run before freeze-m7, other than its configuration 1 on one frame".
        tool = FakeTool({})
        self.assertEqual(self.run_probe(tool), 2)
        self.assertEqual(self.run_probe(tool, "--run-kind", "check", "--freeze-m7-commit", FREEZE_M7), 2)
        self.assertEqual(tool.calls, [])
        self.assertFalse(self.out.exists())


class Parsing(unittest.TestCase):
    def test_attrs_csv(self):
        scale = array("f", [0.117659])[0]  # a float32, printed as the tool prints it (%.9g)
        text = ("# tool: m7_output_probe\n" + ATTR_COLUMNS + "\n"
                f"0,318,5,1x4x80x80x16,409600,409600,409600,0,0,NC1HWC2,INT8,AFFINE_ASYMMETRIC,-58,{scale:.9g},"
                "NC1HWC2,INT8,409600,i8\n")
        (a,) = mp.parse_attrs(text)
        self.assertEqual(a["dims"], [1, 4, 80, 80, 16])
        self.assertEqual((a["fmt"], a["type"], a["zp"], a["ext"]), ("NC1HWC2", "INT8", -58, "i8"))
        self.assertEqual(a["scale"], scale)
        with self.assertRaises(ValueError):
            mp.parse_attrs("# tool: x\n" + ATTR_COLUMNS + "\n")

    def test_failure_reason_without_a_record(self):
        self.assertEqual(mp.failure_reason(7, None, ""), "exit code 7 (an unknown failure): no message")


if __name__ == "__main__":
    unittest.main()
