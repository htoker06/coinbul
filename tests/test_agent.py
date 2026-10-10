import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent as agent_mod  # noqa: E402
from analysis import evaluate, fake_volume_score, tf_metrics  # noqa: E402
from broker import floor_step  # noqa: E402
from config import Config  # noqa: E402
from exchange import sign  # noqa: E402


def make_klines(n=60, start=100.0, drift=0.0, vol=10.0, last_vol=None, taker=0.5, rng=0.01):
    out, price = [], start
    for i in range(n):
        o = price
        c = o * (1 + drift)
        v = last_vol if (last_vol and i == n - 2) else vol
        out.append([i, o, max(o, c) * (1 + rng), min(o, c) * (1 - rng), c, v, i, v * c, 10, v * taker, 0, 0])
        price = c
    return out


def make_trades(n=200, price=100.0, uniform=False, buy_ratio=0.5, seed=1):
    r = random.Random(seed)
    trades, t = [], 1_000_000
    for i in range(n):
        q = 1.0 if uniform else round(r.uniform(0.01, 2), 4)
        t += 1000 if uniform else r.randint(50, 3000)
        maker = (i % 2 == 0) if uniform else (r.random() > buy_ratio)
        trades.append({"price": price, "qty": q, "quoteQty": q * price, "time": t, "isBuyerMaker": maker})
    return trades


def make_depth(price=100.0, spread=0.001, size=1000.0):
    bids = [[price * (1 - spread / 2 - i * 0.001), size] for i in range(20)]
    asks = [[price * (1 + spread / 2 + i * 0.001), size] for i in range(20)]
    return {"bids": bids, "asks": asks}


class FakeClient:
    """Bir coin güçlü alımda, biri sahte hacimli, biri düşüşte."""

    def __init__(self):
        self.price = {"GOOD_TRY": 100.0, "WASH_TRY": 50.0, "BAD_TRY": 10.0}

    def symbols(self):
        return [{"symbol": s, "baseAsset": s.split("_")[0], "quoteAsset": "TRY", "type": 1,
                 "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"}]}
                for s in self.price] + [{"symbol": "BTC_USDT", "quoteAsset": "USDT", "type": 1}]

    def tickers_24h(self):
        return [{"symbol": s.replace("_", ""), "quoteVolume": "50000000", "priceChangePercent": "3",
                 "lastPrice": str(p)} for s, p in self.price.items()]

    def klines(self, sym, tf, limit):
        p = self.price[sym]
        if sym == "GOOD_TRY":
            return make_klines(start=p * 0.8, drift=0.004, last_vol=50, taker=0.75)
        if sym == "WASH_TRY":
            return make_klines(start=p, drift=0.0, last_vol=80, taker=0.5, rng=0.0001)
        return make_klines(start=p * 1.3, drift=-0.004, last_vol=40, taker=0.25)

    def trades(self, sym, limit):
        return make_trades(price=self.price[sym], uniform=(sym == "WASH_TRY"),
                           buy_ratio={"GOOD_TRY": 0.8, "BAD_TRY": 0.2}.get(sym, 0.5))

    def depth(self, sym, limit):
        return make_depth(self.price[sym])


class AnalysisTests(unittest.TestCase):
    def test_signature_matches_binance_doc_example(self):
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
                 "&recvWindow=5000&timestamp=1499827319559")
        self.assertEqual(sign(secret, query),
                         "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_tf_metrics_detects_volume_spike_and_buying(self):
        m = tf_metrics(make_klines(drift=0.003, last_vol=40, taker=0.8))
        self.assertTrue(m.trend_up)
        self.assertAlmostEqual(m.rvol, 4.0)
        self.assertGreater(m.taker_buy_ratio, 0.7)

    def test_uniform_alternating_trades_flagged_as_fake(self):
        score, reasons = fake_volume_score(make_trades(uniform=True), None, None, 0)
        self.assertGreaterEqual(score, 0.5)
        self.assertTrue(reasons)

    def test_random_trades_not_flagged(self):
        score, _ = fake_volume_score(make_trades(), None, None, 0)
        self.assertLess(score, 0.3)

    def test_evaluate_labels(self):
        c = FakeClient()
        sig = {s: evaluate(s, {tf: c.klines(s, tf, 60) for tf in ("15m", "1h", "4h")},
                           c.trades(s, 500), c.depth(s, 100), {"quoteVolume": "50000000"})
               for s in c.price}
        self.assertEqual(sig["GOOD_TRY"].label, "ALIM BAŞLADI")
        self.assertEqual(sig["WASH_TRY"].label, "ŞÜPHELİ HACİM")
        self.assertEqual(sig["BAD_TRY"].label, "SATIŞ BASKISI")
        self.assertGreater(sig["GOOD_TRY"].score, 0.45)
        self.assertLess(sig["BAD_TRY"].score, 0)

    def test_floor_step(self):
        self.assertEqual(floor_step(1.23456, 0.001), 1.234)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(data_dir=Path(self.tmp.name))
        agent_mod.STOP_FILE = Path(self.tmp.name) / "STOP"
        agent_mod.time.sleep = lambda s: None
        self.client = FakeClient()
        self.agent = agent_mod.Agent(self.cfg, client=self.client)
        self.agent.load_symbols()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_try_pairs_loaded(self):
        self.assertEqual(set(self.agent.symbols), {"GOOD_TRY", "WASH_TRY", "BAD_TRY"})

    def test_buys_only_good_coin_then_stop_loss(self):
        self.agent.step()
        self.assertEqual(list(self.agent.state["positions"]), ["GOOD_TRY"])
        self.assertAlmostEqual(self.agent.state["cash_try"], 9500.0)

        self.client.price["GOOD_TRY"] = 100.0 * 0.95  # %5 düşüş -> zarar durdur
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertLess(self.agent.state["realized_today"], 0)
        self.assertTrue(self.agent.journal_path.exists())

    def test_daily_loss_limit_blocks_buying(self):
        self.agent.state["realized_today"] = -1000
        self.agent._roll_day = lambda: None
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_stop_file_blocks_buying(self):
        agent_mod.STOP_FILE.write_text("")
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_live_mode_requires_confirmation(self):
        with self.assertRaises(ValueError):
            Config(mode="live", api_key="k", api_secret="s").validate()
        Config(mode="live", api_key="k", api_secret="s", confirm_live="EVET").validate()


if __name__ == "__main__":
    unittest.main()
