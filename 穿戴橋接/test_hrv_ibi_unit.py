# -*- coding: utf-8 -*-
"""離線：合成 hr_ibi 紀錄寫進暫存 jsonl，走 ibi_windows → hrv 正式那條路。"""
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ibi_windows as iw
from hrv import analyse

K = 1.31
T0 = 1_794_000_000_000


def true_beats(n, mean_ms=800, rmssd=25.0, seed=1):
    """相鄰差 ~N(0, rmssd) 的真實毫秒序列（RMSSD ≈ rmssd）。"""
    rnd = random.Random(seed)
    x = [mean_ms]
    for _ in range(n - 1):
        x.append(x[-1] + rnd.gauss(0, rmssd / math.sqrt(2)) * math.sqrt(2) * 0.7071)
        x[-1] += (mean_ms - x[-1]) * 0.05     # 拉回平均，免得飄走
    return x


def rmssd(x):
    d = [b - a for a, b in zip(x, x[1:])]
    return math.sqrt(sum(v * v for v in d) / len(d))


def ultra2_records(ms, still=1.0, st0_every=7, on_body="on"):
    """ """
    recs, motion, t, i = [], [], T0, 0
    while i < len(ms):
        start, beats, st, segs, acc = t, [], [], [], 0.0
        while i < len(ms) and acc < 15000:
            beats.append(round(ms[i] * K)); st.append(0 if i % st0_every == 0 else -1)
            acc += ms[i]; i += 1
        for s in range(int(acc // 1000) + 1):
            segs.append([start + s * 1000, 0])
        hr = round(60000 / (sum(ms[i - len(beats):i]) / len(beats)))
        recs.append({"t": "x", "type": "hr_ibi", "on_body": on_body, "status": 1, "hr": hr,
                     "hrs": [hr] * len(segs), "sensor_t": start, "seg": segs,
                     "ibi_raw": beats, "ibi_st": st,
                     "ibi": [b for b, s in zip(beats, st) if s == 0]})
        t = segs[-1][0] + 1000
    for m in range(0, t - T0, 60_000):
        motion.append({"type": "motion", "sensor_t": T0 + m, "still_ratio": still})
    return recs + motion


def write(rows):
    d = Path(tempfile.mkdtemp())
    p = d / "2026-11-04.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


class UnitAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.ms = true_beats(1000)          # ~13 分鐘
        self.truth = rmssd(self.ms)

    def test_unit_ratio_recovered_and_rmssd_in_ms(self):
        ws = iw.windows_from_file(write(ultra2_records(self.ms)), trusted_only=False)
        self.assertTrue(ws, "應該切得出窗口")
        for w in ws:
            self.assertAlmostEqual(w.unit_k, K, delta=0.03)
            self.assertTrue(w.raw)
            mean_ms = sum(w.clean) / len(w.clean)
            self.assertAlmostEqual(mean_ms, 800, delta=40, msg="換算後要回到真實毫秒")
        got = sorted(analyse(w).rmssd_ms for w in ws)
        self.assertAlmostEqual(got[len(got) // 2], self.truth, delta=self.truth * 0.15,
                               msg="RMSSD 要是真實毫秒，不是放大 1.31 倍")

    def test_filter_marks_doubled_beat_and_does_not_inflate(self):
        ms = list(self.ms)
        for j in range(200, 900, 97):       # 塞幾個兩拍併一拍
            ms[j] = ms[j] * 2
        ws = iw.windows_from_file(write(ultra2_records(ms)), trusted_only=False)
        self.assertTrue(any(w.artifact_pct > 0 for w in ws), "併拍要被標成雜訊")
        got = sorted(analyse(w).rmssd_ms for w in ws)
        self.assertLess(got[len(got) // 2], self.truth * 1.3, "雜訊拍兩側的配對要作廢，不能灌水")
        self.assertTrue(all(w.kept < 1.0 or w.artifact_pct == 0 for w in ws))

    def test_st0_only_is_not_used(self):
        """回歸：只留 st=0（舊欄位 ibi）會把不相鄰的拍子湊成假相鄰。新格式一律讀 ibi_raw。"""
        recs = ultra2_records(self.ms)
        loaded = iw._load(write(recs))
        raw_n = sum(len(r["ibi_raw"]) for r in recs if r.get("type") == "hr_ibi")
        self.assertEqual(sum(len(r["ibi"]) for r in loaded), raw_n, "要讀全部拍子，不是只讀 st=0")

        st0 = [b / K for r in recs if r.get("type") == "hr_ibi" for b in r["ibi"]]
        self.assertGreater(rmssd(st0), self.truth * 1.5)

    def test_records_with_empty_ibi_but_raw_are_kept(self):
        """ """
        recs = ultra2_records(self.ms, st0_every=10**9)   # 幾乎沒有 st=0
        for r in recs:
            if r.get("type") == "hr_ibi":
                r["ibi"] = []
        self.assertTrue(iw.windows_from_file(write(recs), trusted_only=False))

    def test_moving_windows_are_low_trust_and_hidden_by_default(self):
        moving = write(ultra2_records(self.ms, still=0.3))
        allw = iw.windows_from_file(moving, trusted_only=False)
        self.assertTrue(allw)
        self.assertTrue(all(not w.trusted for w in allw), "在動的窗口要標低可信")
        self.assertEqual(iw.windows_from_file(moving), [], "預設不回低可信的窗口（錶腦不准拿來判斷）")
        still = iw.windows_from_file(write(ultra2_records(self.ms, still=1.0)))
        self.assertTrue(still and all(w.trusted for w in still))
        self.assertIn("可信度", analyse(still[0]).說法)

    def test_raw_without_motion_is_not_trusted(self):
        rows = [r for r in ultra2_records(self.ms) if r.get("type") != "motion"]
        allw = iw.windows_from_file(write(rows), trusted_only=False)
        self.assertTrue(allw and all(w.confidence == 0.0 for w in allw))

    def test_old_format_unchanged(self):
        """ """
        rows, t = [], T0
        for i in range(0, len(self.ms), 20):
            chunk = [round(x) for x in self.ms[i:i + 20]]
            rows.append({"type": "hr_ibi", "on_body": "on", "status": 1, "hr": 75, "sensor_t": t, "ibi": chunk})
            t += sum(chunk)
        rows += [{"type": "motion", "sensor_t": T0 + m, "still_ratio": 0.7} for m in range(0, t - T0, 60_000)]
        ws = iw.windows_from_file(write(rows), trusted_only=False)
        self.assertTrue(ws)
        for w in ws:
            self.assertFalse(w.raw)
            self.assertEqual(w.unit_k, 1.0)
            self.assertTrue(w.trusted, "舊錶 still_ratio 到不了 0.9，不能用 新錶的門檻判它")


if __name__ == "__main__":
    unittest.main(verbosity=2)
