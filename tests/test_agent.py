import dataclasses
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent as agent_mod  # noqa: E402
import analysis as an  # noqa: E402
from backtest import VARIANTS, Precomputed, run_backtest, variant_config  # noqa: E402
from broker import LiveBroker, floor_step, round_tick  # noqa: E402
from config import Config  # noqa: E402
from exchange import SIDE_BUY, sign  # noqa: E402
from report import summarize  # noqa: E402
from risk import Risk, edge_ok, entry_plan, in_window, new_state  # noqa: E402


def make_klines(n=60, start=100.0, drift=0.0, vol=10.0, last_vol=None, taker=0.5, rng=0.01, t0=0, step=60_000):
    out, price = [], start
    for i in range(n):
        o = price
        c = o * (1 + drift)
        v = last_vol if (last_vol and i == n - 2) else vol
        out.append([t0 + i * step, o, max(o, c) * (1 + rng), min(o, c) * (1 - rng), c, v,
                    t0 + (i + 1) * step - 1, v * c, 10, v * taker, 0, 0])
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
    """GOOD güçlü alımda, WASH sahte hacimli, BAD düşüşte."""

    def __init__(self):
        self.price = {"GOOD_TRY": 100.0, "WASH_TRY": 50.0, "BAD_TRY": 10.0, "BTC_TRY": 4_000_000.0}
        self.btc_drift = 0.002
        self.good_5m_jump = 0.0

    def symbols(self):
        return [{"symbol": s, "baseAsset": s.split("_")[0], "quoteAsset": "TRY", "type": 1,
                 "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
                             {"filterType": "PRICE_FILTER", "tickSize": "0.01"}]}
                for s in self.price] + [{"symbol": "BTC_USDT", "quoteAsset": "USDT", "type": 1}]

    def tickers_24h(self):
        return [{"symbol": s.replace("_", ""), "quoteVolume": "50000000", "priceChangePercent": "3",
                 "lastPrice": str(p)} for s, p in self.price.items()]

    def klines(self, sym, tf, limit, start_ms=None):
        p = self.price[sym]
        if sym == "BTC_TRY":
            return make_klines(start=p, drift=self.btc_drift)
        if sym == "GOOD_TRY":
            k = make_klines(start=p * 0.8, drift=0.004, last_vol=50, taker=0.75)
            if tf == "5m" and self.good_5m_jump:
                k[-1][4] = k[-3][4] * (1 + self.good_5m_jump)
            return k
        if sym == "WASH_TRY":
            return make_klines(start=p, drift=0.0, last_vol=80, taker=0.5, rng=0.0001)
        return make_klines(start=p * 1.3, drift=-0.004, last_vol=40, taker=0.25)

    def trades(self, sym, limit):
        return make_trades(price=self.price[sym], uniform=(sym == "WASH_TRY"),
                           buy_ratio={"GOOD_TRY": 0.8, "BAD_TRY": 0.2}.get(sym, 0.5))

    def depth(self, sym, limit):
        return make_depth(self.price[sym])


class FakeGlobal:
    def __init__(self, usdt_try=40.0):
        self.usdt_try = usdt_try
        self.prices = {"GOODUSDT": 2.5, "BADUSDT": 0.25, "WASHUSDT": 1.25}
        self.rvol_last = 50

    def tickers_24h(self):
        return ([{"symbol": "USDTTRY", "lastPrice": str(self.usdt_try)}]
                + [{"symbol": s, "lastPrice": str(p)} for s, p in self.prices.items()])

    def klines(self, sym, tf, limit):
        return make_klines(last_vol=self.rvol_last)


class FakeNotifier:
    def __init__(self):
        self.sent, self.queue = [], []

    def send(self, text):
        self.sent.append(text)

    def commands(self):
        out, self.queue = self.queue, []
        return out


def test_config(tmp, **kw):
    base = dict(data_dir=Path(tmp), no_buy_hours="", confirm_rounds=1)
    base.update(kw)
    return Config(**base)


# --------------------------------------------------------------------------- analiz
class AnalysisTests(unittest.TestCase):
    def test_signature_matches_binance_doc_example(self):
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
                 "&recvWindow=5000&timestamp=1499827319559")
        self.assertEqual(sign(secret, query),
                         "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_tf_metrics_detects_volume_spike_and_buying(self):
        m = an.tf_metrics(make_klines(drift=0.003, last_vol=40, taker=0.8))
        self.assertTrue(m.trend_up)
        self.assertAlmostEqual(m.rvol, 4.0)
        self.assertGreater(m.taker_buy_ratio, 0.7)

    def test_fake_volume(self):
        self.assertGreaterEqual(an.fake_volume_score(make_trades(uniform=True), None, None, 0)[0], 0.5)
        self.assertLess(an.fake_volume_score(make_trades(), None, None, 0)[0], 0.3)

    def test_evaluate_labels(self):
        c = FakeClient()
        sig = {s: an.evaluate(s, {tf: c.klines(s, tf, 60) for tf in ("15m", "1h", "4h")},
                              c.trades(s, 500), c.depth(s, 100), {"quoteVolume": "50000000"})
               for s in ("GOOD_TRY", "WASH_TRY", "BAD_TRY")}
        self.assertEqual(sig["GOOD_TRY"].label, "ALIM BAŞLADI")
        self.assertEqual(sig["WASH_TRY"].label, "ŞÜPHELİ HACİM")
        self.assertEqual(sig["BAD_TRY"].label, "SATIŞ BASKISI")
        self.assertGreater(sig["GOOD_TRY"].score, 0.45)
        self.assertGreater(sig["GOOD_TRY"].atr_pct, 0)

    def test_evaluate_without_trades_and_depth(self):
        s = an.evaluate("X_TRY", {"15m": make_klines(drift=0.003)}, [], None, {"lastPrice": "5"})
        self.assertEqual(s.price, 5.0)

    def test_atr_slippage_limit_price(self):
        self.assertAlmostEqual(an.atr_pct(make_klines(rng=0.01)), 2.0, delta=0.1)
        d = {"asks": [[100, 1], [101, 1], [110, 100]], "bids": [[99, 1]]}
        self.assertAlmostEqual(an.slippage_pct(d, 100), 0.0)
        self.assertGreater(an.slippage_pct(d, 250), 0.5)
        self.assertEqual(an.slippage_pct(d, 1e9), 99.0)
        self.assertAlmostEqual(an.limit_price_for(d, 150, 0.3), 100.3)  # sınırla kısıtlı

    def test_spoof_detection(self):
        prev = make_depth(100)
        prev["bids"][2][1] = 20_000  # dev alış duvarı
        now = make_depth(100)
        self.assertTrue(an.detect_spoof(prev, now))
        self.assertFalse(an.detect_spoof(prev, prev))
        traded = make_depth(99.5)  # fiyat duvarın altına indi: gerçek emirdi
        self.assertFalse(an.detect_spoof(prev, traded))

    def test_btc_regime(self):
        up = make_klines(drift=0.002)
        down = make_klines(drift=-0.002)
        self.assertTrue(an.btc_regime(up, up, up, 2).ok)
        self.assertFalse(an.btc_regime(down, down, up, 2).ok)
        crash = make_klines(drift=0.002)
        crash[-1][4] = crash[-2][4] * 0.97
        self.assertFalse(an.btc_regime(up, up, crash, 2).ok)

    def test_global_check(self):
        gt = {"lastPrice": "2.5"}
        self.assertTrue(an.global_check(100, gt, 40, 1.0, 1.0, 1.5, 2.5)[0])
        self.assertFalse(an.global_check(103, gt, 40, 1.0, 1.0, 1.5, 2.5)[0])   # %3 prim
        self.assertFalse(an.global_check(100, gt, 40, 6.0, 1.0, 1.5, 2.5)[0])   # sadece TR'de hacim
        self.assertTrue(an.global_check(100, None, 40, 6.0, None, 1.5, 2.5)[0])  # globalde yok

    def test_resample(self):
        k = make_klines(n=8, step=15 * 60_000)
        r = an.resample(k, 60)
        self.assertEqual(len(r), 2)
        self.assertAlmostEqual(r[0][5], 40)

    def test_rounding(self):
        self.assertEqual(floor_step(1.23456, 0.001), 1.234)
        self.assertEqual(round_tick(1.239, 0.01), 1.23)


# --------------------------------------------------------------------------- risk
class RiskTests(unittest.TestCase):
    def setUp(self):
        self.cfg = test_config(tempfile.mkdtemp())
        self.risk = Risk(self.cfg, new_state(self.cfg))

    def test_entry_plan_scales_with_volatility(self):
        calm, wild = entry_plan(self.cfg, 0.5), entry_plan(self.cfg, 3.0)
        self.assertEqual(calm["stop_pct"], 1.5)          # alt sınır
        self.assertEqual(wild["stop_pct"], 6.0)
        self.assertEqual(wild["tp_pct"], 12.0)
        self.assertLess(wild["size_try"], calm["size_try"])
        self.assertLessEqual(calm["size_try"], self.cfg.max_trade_try)
        self.assertAlmostEqual(wild["size_try"] * wild["stop_pct"] / 100, self.cfg.risk_per_trade_try)

    def test_consecutive_losses_pause_and_cooldown(self):
        now = 1_000_000
        for i in range(3):
            self.risk.record_close(f"C{i}_TRY", -10, now)
        self.assertIn("mola", self.risk.global_block(now + 60))
        self.assertEqual(self.risk.global_block(now + 3 * 3600), "")
        self.assertGreater(self.risk.state["cooldowns"]["C0_TRY"], now)

    def test_weekly_and_daily_limits(self):
        self.risk.state["realized_week"] = -2500
        self.assertIn("haftalık", self.risk.global_block(time.time()))
        self.risk.state["realized_week"] = 0
        self.risk.state["realized_today"] = -800
        self.assertIn("günlük", self.risk.global_block(time.time()))

    def test_hour_window(self):
        self.assertTrue(in_window(3, (2, 8)))
        self.assertFalse(in_window(8, (2, 8)))
        self.assertTrue(in_window(23, (22, 6)) and in_window(1, (22, 6)))
        self.assertFalse(in_window(12, None))

    def test_stablecoin_blacklist_and_confirmation(self):
        cfg = test_config(tempfile.mkdtemp(), blacklist="PEPE", confirm_rounds=2)
        r = Risk(cfg, new_state(cfg))
        sig = lambda sym: an.Signal(sym, 0.8, "ALIM BAŞLADI", 0.0, 1.0, 0.1, 3.0, 1e8)  # noqa: E731
        self.assertEqual(r.symbol_block(sig("USDT_TRY"), {}, 0), "sabit değerli coin")
        self.assertEqual(r.symbol_block(sig("PEPE_TRY"), {}, 0), "kara listede")
        r.update_streaks([sig("AVAX_TRY")])
        self.assertIn("teyid", r.symbol_block(sig("AVAX_TRY"), {}, 0))
        r.update_streaks([sig("AVAX_TRY")])
        self.assertEqual(r.symbol_block(sig("AVAX_TRY"), {}, 0), "")

    def test_reentry_cooldown_after_profit_and_daily_buy_limit(self):
        self.risk.record_close("A_TRY", +5, 1000)
        self.assertGreater(self.risk.state["cooldowns"]["A_TRY"], 1000 + 5 * 3600)
        self.assertEqual(self.risk.state["consecutive_losses"], 0)
        for _ in range(self.cfg.max_daily_buys):
            self.risk.record_buy()
        self.assertIn("günlük alım sınırı", self.risk.global_block(time.time()))
        self.risk.state["day"] = "eski"
        self.risk.roll_periods(time.time())
        self.assertEqual(self.risk.global_block(time.time()), "")

    def test_signal_exit_only_in_loss_and_after_rounds(self):
        bad = an.Signal("A_TRY", -0.5, "SATIŞ BASKISI", 0, 1.0, 0.1, 0, 1e8)
        self.risk.update_sell_streaks([bad])
        self.assertFalse(self.risk.signal_exit("A_TRY", in_loss=True))   # 1 tur yetmez
        self.risk.update_sell_streaks([bad])
        self.assertTrue(self.risk.signal_exit("A_TRY", in_loss=True))
        self.assertFalse(self.risk.signal_exit("A_TRY", in_loss=False))  # kârdayken satmaz
        self.risk.update_sell_streaks([])
        self.assertFalse(self.risk.signal_exit("A_TRY", in_loss=True))   # seri bozuldu

    def test_edge_check(self):
        self.assertTrue(edge_ok(self.cfg, {"tp_pct": 3.0}))
        self.assertFalse(edge_ok(self.cfg, {"tp_pct": 1.0}))  # maliyet %0.3 x 5 = %1.5

    def test_pump_waits_for_pullback_then_rebound(self):
        s = an.Signal("X_TRY", 0.8, "ALIM BAŞLADI", 0, 100.0, 0.1, 3, 1e8, change_5m_pct=7)
        self.assertIn("ani yükseliş", self.risk.pump_block(s, 0))
        s.change_5m_pct, s.price = 0.5, 101.0
        self.assertIn("geri çekilme", self.risk.pump_block(s, 60))
        s.price, s.change_1m_pct = 99.0, -0.2
        self.assertIn("yeniden yükseliş", self.risk.pump_block(s, 120))
        s.change_1m_pct = 0.3
        self.assertEqual(self.risk.pump_block(s, 180), "")


# --------------------------------------------------------------------------- ajan
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()
        agent_mod.STOP_FILE = Path(self.tmp.name) / "STOP"
        self.make()

    def make(self, **kw):
        self.cfg = test_config(self.tmp.name, **kw)
        self.client, self.glob, self.notes = FakeClient(), FakeGlobal(), FakeNotifier()
        self.agent = agent_mod.Agent(self.cfg, client=self.client, global_client=self.glob, notifier=self.notes)
        self.agent.load_symbols()

    def tearDown(self):
        self.sleep.stop()
        self.tmp.cleanup()

    def reprice(self, sym, price):
        self.client.price[sym] = price
        self.agent.depth_cache = {}

    def test_only_try_pairs_loaded(self):
        self.assertEqual(set(self.agent.symbols), {"GOOD_TRY", "WASH_TRY", "BAD_TRY", "BTC_TRY"})

    def test_buys_only_good_coin_with_volatility_sizing(self):
        self.agent.step()
        pos = self.agent.state["positions"]
        self.assertEqual(list(pos), ["GOOD_TRY"])
        p = pos["GOOD_TRY"]
        self.assertGreater(p["stop_pct"], 1.5)
        self.assertLess(p["stop_price"], p["entry"])
        self.assertAlmostEqual(self.agent.state["cash_try"], 10_000 - p["cost_try"])
        self.assertTrue(any("ALIM GOOD_TRY" in m for m in self.notes.sent))

    def test_confirmation_needs_two_rounds(self):
        self.make(confirm_rounds=2)
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})
        self.agent.step()
        self.assertIn("GOOD_TRY", self.agent.state["positions"])

    def test_btc_downtrend_blocks_buying(self):
        self.client.btc_drift = -0.003
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_global_premium_blocks_buying(self):
        self.glob.prices["GOODUSDT"] = 2.4  # TR fiyatı globalden ~%4 pahalı
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_pump_blocks_buying(self):
        self.client.good_5m_jump = 0.08
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})
        self.assertIn("GOOD_TRY", self.agent.state["pump_watch"])

    def test_no_buy_hours(self):
        self.make(no_buy_hours="0-24")
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_stop_loss_then_cooldown(self):
        self.agent.step()
        entry = self.agent.state["positions"]["GOOD_TRY"]["entry"]
        self.reprice("GOOD_TRY", entry * 0.90)
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertLess(self.agent.state["realized_today"], 0)
        self.assertGreater(self.agent.state["cooldowns"]["GOOD_TRY"], time.time())
        self.reprice("GOOD_TRY", 100.0)
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})  # bekleme süresinde

    def test_partial_take_then_trailing_stop(self):
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        entry, qty0 = p["entry"], p["qty"]
        self.reprice("GOOD_TRY", entry * (1 + p["stop_pct"] / 100 + 0.002))
        self.agent.manage_positions([])
        p = self.agent.state["positions"]["GOOD_TRY"]
        self.assertTrue(p["partial_done"])
        self.assertAlmostEqual(p["qty"], qty0 / 2, places=3)
        self.assertGreater(p["stop_price"], entry)  # stop girişin üstüne çekildi
        self.assertGreater(self.agent.state["realized_today"], 0)
        self.reprice("GOOD_TRY", entry * 1.001)
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertGreater(self.agent.state["realized_today"], 0)

    def test_sell_signal_exits_only_losing_position(self):
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        p["partial_done"] = True
        bad = an.Signal("GOOD_TRY", -0.5, "SATIŞ BASKISI", 0, 1.0, 0.1, 0, 1e8)
        for _ in range(2):
            self.agent.risk.update_sell_streaks([bad])
        self.reprice("GOOD_TRY", p["entry"] * 1.005)   # kârda: satılmaz
        self.agent.manage_positions([bad])
        self.assertIn("GOOD_TRY", self.agent.state["positions"])
        self.reprice("GOOD_TRY", p["entry"] * 0.995)   # zararda + 2 tur satış sinyali: satılır
        self.agent.manage_positions([bad])
        self.assertEqual(self.agent.state["positions"], {})

    def test_take_profit(self):
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        p["partial_done"] = True
        self.reprice("GOOD_TRY", p["entry"] * (1 + p["tp_pct"] / 100 + 0.01))
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})

    def test_telegram_commands(self):
        self.notes.queue = ["/durdur"]
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})
        self.notes.queue = ["/devam"]
        self.agent.step()
        self.assertIn("GOOD_TRY", self.agent.state["positions"])
        self.notes.queue = ["/hepsinisat"]
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})
        self.notes.queue = ["/durum"]
        self.agent.handle_commands()
        self.assertIn("Mod: SANAL", self.notes.sent[-1])

    def test_stop_file(self):
        agent_mod.STOP_FILE.write_text("")
        self.agent.step()
        self.assertEqual(self.agent.state["positions"], {})

    def test_journal_and_report(self):
        self.agent.step()
        self.reprice("GOOD_TRY", 50)
        self.agent.manage_positions([])
        rows = agent_mod.load(self.agent.journal_path)
        self.assertEqual([r["islem"] for r in rows], ["AL", "SAT"])
        s = summarize(rows)
        self.assertEqual(s["kapanan"], 1)
        self.assertEqual(s["kazanma_orani"], 0)
        self.assertIn("ALIM BAŞLADI", s["sinyale_gore"])

    def test_live_mode_requires_confirmation(self):
        with self.assertRaises(ValueError):
            Config(mode="live", api_key="k", api_secret="s").validate()
        Config(mode="live", api_key="k", api_secret="s", confirm_live="EVET").validate()

    def test_config_from_env(self):
        env = {"MODE": "paper", "BTC_FILTER": "false", "MAX_OPEN_POSITIONS": "5", "RISK_PER_TRADE_TRY": "50",
               "BLACKLIST": "PEPE, shib"}
        with mock.patch.dict("os.environ", env, clear=False), mock.patch("config.load_env"):
            cfg = Config.from_env()
        self.assertFalse(cfg.btc_filter)
        self.assertEqual(cfg.max_open_positions, 5)
        self.assertEqual(cfg.risk_per_trade_try, 50.0)
        self.assertEqual(cfg.blacklist_set, {"PEPE", "SHIB"})


# --------------------------------------------------------------------------- canlı aracı
class FakeSignedClient(FakeClient):
    """Bakiyeleri tutan sahte borsa: emirler anında gerçekleşir."""

    def __init__(self):
        super().__init__()
        self.bal = {"TRY": {"free": 5000.0, "locked": 0.0}}
        self.orders = {}
        self.next_id = 1

    def balances(self):
        return {k: dict(v) for k, v in self.bal.items()}

    def _b(self, asset):
        return self.bal.setdefault(asset, {"free": 0.0, "locked": 0.0})

    def limit_order(self, symbol, side, qty, price):
        assert side == SIDE_BUY
        base = symbol.split("_")[0]
        self._b(base)["free"] += qty
        self._b("TRY")["free"] -= qty * price * 1.001
        return {"orderId": 0}

    def market_sell(self, symbol, qty):
        base = symbol.split("_")[0]
        assert self._b(base)["free"] >= qty - 1e-12, "kilitli bakiye satılamaz"
        self._b(base)["free"] -= qty
        self._b("TRY")["free"] += qty * self.price[symbol] * 0.999
        return {}

    def stop_loss_limit(self, symbol, qty, stop, limit):
        base = symbol.split("_")[0]
        self._b(base)["free"] -= qty
        self._b(base)["locked"] += qty
        oid = self.next_id
        self.next_id += 1
        self.orders[oid] = (base, qty, stop)
        return {"orderId": oid}

    def cancel_order(self, oid):
        if oid in self.orders:
            base, qty, _ = self.orders.pop(oid)
            self._b(base)["locked"] -= qty
            self._b(base)["free"] += qty
        return {}

    def trigger_stop(self, oid):
        base, qty, stop = self.orders.pop(oid)
        self._b(base)["locked"] -= qty
        self._b("TRY")["free"] += qty * stop * 0.999


class LiveBrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()
        agent_mod.STOP_FILE = Path(self.tmp.name) / "STOP"
        self.cfg = test_config(self.tmp.name, mode="live", api_key="k", api_secret="s", confirm_live="EVET")
        self.client = FakeSignedClient()
        self.notes = FakeNotifier()
        self.agent = agent_mod.Agent(self.cfg, client=self.client, global_client=FakeGlobal(), notifier=self.notes)
        self.assertIsInstance(self.agent.broker, LiveBroker)
        self.agent.load_symbols()

    def tearDown(self):
        self.sleep.stop()
        self.tmp.cleanup()

    def test_buy_places_exchange_stop_and_sell_cancels_it(self):
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        self.assertGreater(p["qty"], 0)
        self.assertAlmostEqual(p["cost_try"], 5000 - self.client.bal["TRY"]["free"], places=6)
        self.assertIsNotNone(p["stop_order_id"])
        self.assertAlmostEqual(self.client.bal["GOOD"]["locked"], p["qty"])
        self.client.price["GOOD_TRY"] = 50
        self.agent.depth_cache = {}
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertAlmostEqual(self.client.bal["GOOD"]["locked"], 0)

    def test_exchange_stop_fill_is_detected(self):
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        self.client.trigger_stop(p["stop_order_id"])
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertLess(self.agent.state["realized_today"], 0)
        self.assertEqual(self.agent.state["halted"], "")

    def test_stop_fill_detected_when_user_already_holds_coin(self):
        self.client.bal["GOOD"] = {"free": 3.0, "locked": 0.0}
        self.agent.step()
        p = self.agent.state["positions"]["GOOD_TRY"]
        self.client.trigger_stop(p["stop_order_id"])
        self.agent.manage_positions([])
        self.assertEqual(self.agent.state["positions"], {})
        self.assertEqual(self.agent.state["halted"], "")
        self.assertAlmostEqual(self.client.bal["GOOD"]["free"], 3.0)  # kullanıcının coinine dokunulmadı

    def test_balance_mismatch_halts(self):
        self.agent.step()
        self.client.bal["GOOD"]["locked"] *= 0.5  # biri elle yarısını sattı
        self.agent.manage_positions([])
        self.assertIn("uyuşmuyor", self.agent.state["halted"])
        self.assertTrue(any("uyuşmuyor" in m for m in self.notes.sent))


# --------------------------------------------------------------------------- geçmiş veri testi
class BacktestTests(unittest.TestCase):
    def test_runs_and_reports(self):
        cfg = test_config(tempfile.mkdtemp(), min_quote_volume_try=0, min_listing_days=1, btc_filter=False)
        step = 15 * 60_000
        r = random.Random(3)
        candles, price = [], 100.0
        for i in range(3000):  # yükseliş ve düşüş dalgaları
            drift = 0.0012 if (i // 300) % 2 == 0 else -0.0012
            o = price
            c = o * (1 + drift + r.uniform(-0.002, 0.002))
            v = 10 * (4 if i % 300 < 20 else 1)
            candles.append([i * step, o, max(o, c) * 1.002, min(o, c) * 0.998, c, v,
                            (i + 1) * step - 1, v * c, 10, v * (0.75 if drift > 0 else 0.3), 0, 0])
            price = c
        summary, trades = run_backtest(cfg, {"X_TRY": candles})
        self.assertGreater(summary["islem_sayisi"], 0)
        for key in ("kazanma_orani_%", "toplam_kar_zarar_try", "en_buyuk_dusus_try", "kar_faktoru"):
            self.assertIn(key, summary)
        buys = [t for t in trades if t["islem"] == "AL"]
        sells = [t for t in trades if t["islem"] == "SAT"]
        self.assertEqual(len(buys), len(sells))
        self.assertGreater(summary["komisyon_ve_kayma_try"], 0)
        self.assertAlmostEqual(summary["komisyon_oncesi_kar_try"],
                               summary["toplam_kar_zarar_try"] + summary["komisyon_ve_kayma_try"], places=1)
        # Karşılaştırma: aynı ön hesapla bütün ayarlar çalışır, eski ayarlar daha çok işlem yapar
        pre = Precomputed({"X_TRY": candles})
        split = pre.times[len(pre.times) // 2]
        res = {name: run_backtest(variant_config(cfg, ov), end_ms=split, pre=pre)[0]
               for name, ov in VARIANTS}
        self.assertLess(res["komisyon yarıya inerse"]["komisyon_ve_kayma_try"],
                        res["yeni varsayılan"]["komisyon_ve_kayma_try"])
        self.assertGreaterEqual(res["eski ayarlar"]["islem_sayisi"], res["yeni varsayılan"]["islem_sayisi"])
        self.assertGreater(res["yeni varsayılan"]["islem_sayisi"], 0)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- swing stratejisi
def swing_candles(days=60, seed=5, up=True):
    """30 saatlik döngü: 24 saat trend, 4 saat geri çekilme, 2 saat güçlü dönüş (15dk mumlar)."""
    r = random.Random(seed)
    out, p, step = [], 100.0, 15 * 60_000
    for i in range(days * 96):
        h = (i // 4) % 30
        drift = (0.0005 if up else -0.0005) if h < 24 else (-0.0025 if h < 28 else 0.003)
        o = p
        c = o * (1 + drift + r.uniform(-0.0005, 0.0005))
        v = 10 * (1.5 if h >= 28 else 1)
        out.append([i * step, o, max(o, c) * 1.001, min(o, c) * 0.999, c, v, (i + 1) * step - 1,
                    v * c, 10, v * (0.7 if drift > 0 else 0.35), 0, 0])
        p = c
    return out


class SwingTests(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(strategy="swing", min_quote_volume_try=0, min_listing_days=1, btc_filter=False,
                    max_24h_change_pct=1000)
        base.update(kw)
        return test_config(tempfile.mkdtemp(), **base)

    def test_active_config_switches_rules(self):
        c = self.cfg().active()
        self.assertEqual(c.reward_risk, c.swing_rr)
        self.assertEqual(c.max_daily_buys, c.swing_max_daily_buys)
        self.assertEqual(c.reentry_cooldown_hours, 24.0)
        self.assertGreater(c.partial_take_r, 100)  # kademeli satış kapalı
        self.assertIs(test_config(tempfile.mkdtemp()).active().strategy, "momentum")
        with self.assertRaises(ValueError):
            Config(strategy="yok").validate()

    def test_setup_found_in_uptrend_not_in_downtrend(self):
        from strategy_swing import swing_setup
        cfg = self.cfg().active()
        for up, expect in ((True, True), (False, False)):
            pre = Precomputed({"X_TRY": swing_candles(up=up)})
            found = [pre.swing(cfg, "X_TRY", t) for t in pre.times if t % 3_600_000 == 0]
            oks = [s for s in found if s.ok]
            self.assertEqual(bool(oks), expect)
            for s in oks:
                self.assertGreaterEqual(s.stop_pct, cfg.swing_min_stop_pct)
                self.assertLessEqual(s.stop_pct, cfg.swing_max_stop_pct)
        self.assertFalse(swing_setup(cfg, [], [], []).ok)

    def test_backtest_swing_trades_less_than_momentum(self):
        cfg = self.cfg()
        pre = Precomputed({"X_TRY": swing_candles()})
        sw, trades = run_backtest(cfg, pre=pre)
        mo, _ = run_backtest(dataclasses.replace(cfg, strategy="momentum"), pre=pre)
        self.assertGreater(sw["islem_sayisi"], 0)
        self.assertLess(sw["islem_sayisi"], mo["islem_sayisi"])
        self.assertLessEqual(sw["gunluk_islem"], cfg.swing_max_daily_buys)
        self.assertNotIn("sinyal döndü", sw["satis_sebepleri"])
        down, _ = run_backtest(cfg, pre=Precomputed({"X_TRY": swing_candles(up=False)}))
        self.assertEqual(down["islem_sayisi"], 0)

    def test_agent_uses_swing_plan_and_exits_on_trend_break(self):
        from strategy_swing import SwingSetup
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch("time.sleep", lambda s: None):
            agent_mod.STOP_FILE = Path(tmp.name) / "STOP"
            cfg = test_config(tmp.name, strategy="swing")
            client = FakeClient()
            ag = agent_mod.Agent(cfg, client=client, global_client=FakeGlobal(), notifier=FakeNotifier())
            ag.load_symbols()
            ok = SwingSetup(True, "test", stop_pct=4.0, trail_pct=3.0, entry_ref=100.06)
            none = SwingSetup(False, "yok")
            with mock.patch("agent.swing_setup", side_effect=lambda c, h, f, d: none):
                ag.step()
            self.assertEqual(ag.state["positions"], {})  # kurulum yoksa alım yok
            with mock.patch("agent.swing_setup",
                            side_effect=lambda c, h, f, d: ok if float(h[-1][4]) > 90 else none):
                ag.step()
            p = ag.state["positions"]["GOOD_TRY"]
            self.assertEqual(p["stop_pct"], 4.0)
            self.assertEqual(p["tp_pct"], 10.0)  # 4 x 2.5
            self.assertEqual(list(ag.state["positions"]), ["GOOD_TRY"])
            broken = SwingSetup(False, "trend", trend_broken=True)
            with mock.patch("agent.swing_setup", side_effect=lambda c, h, f, d: broken):
                ag.step()
            self.assertEqual(ag.state["positions"], {})
            rows = agent_mod.load(ag.journal_path)
            self.assertIn("trend bozuldu", rows[-1]["sebep"])
