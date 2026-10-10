"""(13) Performans raporu: işlem defterinden günlük/haftalık özet.

Kullanım:
    python report.py              # sanal mod defteri
    python report.py --live       # canlı mod defteri
"""
import argparse
import csv
from collections import defaultdict
from datetime import datetime

from config import HERE


def load(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def summarize(rows, since=None):
    """since: 'YYYY-MM-DD' veya None. Satışlardan kâr/zarar istatistiği çıkarır."""
    sells = [r for r in rows if r["islem"] != "AL" and (not since or r["zaman"][:10] >= since)]
    buys = [r for r in rows if r["islem"] == "AL" and (not since or r["zaman"][:10] >= since)]
    closed = [r for r in sells if r["islem"] == "SAT"]
    pnls = [_f(r["kar_zarar_try"]) for r in sells]
    # Bir pozisyonun kısmi + son satışları toplanarak kazanan/kaybeden sayılır
    by_trade = defaultdict(float)
    for r in sells:
        by_trade[r.get("pozisyon_id") or r["zaman"]] += _f(r["kar_zarar_try"])
    results = [by_trade[r.get("pozisyon_id") or r["zaman"]] for r in closed]
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x <= 0]
    by_label = defaultdict(lambda: [0, 0.0])
    for r in closed:
        lab = r.get("sinyal") or "?"
        by_label[lab][0] += 1
        by_label[lab][1] += by_trade[r.get("pozisyon_id") or r["zaman"]]
    by_reason = defaultdict(int)
    for r in closed:
        by_reason[(r.get("sebep") or "?").split(" (")[0]] += 1
    return {
        "alim": len(buys), "kapanan": len(closed),
        "kar_zarar": sum(pnls),
        "komisyon": sum(_f(r.get("komisyon_try")) for r in rows
                        if not since or r["zaman"][:10] >= since),
        "kazanma_orani": len(wins) / len(results) * 100 if results else 0.0,
        "ort_kazanc": sum(wins) / len(wins) if wins else 0.0,
        "ort_kayip": sum(losses) / len(losses) if losses else 0.0,
        "sinyale_gore": dict(by_label),
        "satis_sebebi": dict(by_reason),
    }


def format_summary(title, s):
    lines = [f"📊 {title}",
             f"Alım: {s['alim']} | Kapanan: {s['kapanan']}",
             f"Kâr/zarar: {s['kar_zarar']:+.2f} TRY (komisyon ~{s['komisyon']:.2f} TRY)",
             f"Kazanma oranı: %{s['kazanma_orani']:.0f} | Ort. kazanç {s['ort_kazanc']:+.2f} | "
             f"Ort. kayıp {s['ort_kayip']:+.2f}"]
    if s["sinyale_gore"]:
        lines.append("Sinyale göre: " + ", ".join(f"{k} {n} işlem {v:+.0f} TRY"
                                                  for k, (n, v) in s["sinyale_gore"].items()))
    if s["satis_sebebi"]:
        lines.append("Satış sebebi: " + ", ".join(f"{k} {n}" for k, n in s["satis_sebebi"].items()))
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    rows = load(HERE / "data" / f"islemler_{'live' if args.live else 'paper'}.csv")
    if not rows:
        print("Henüz işlem yok.")
        return
    today = datetime.now().date()
    week_start = today.fromordinal(today.toordinal() - today.weekday())
    print(format_summary("Bugün", summarize(rows, today.isoformat())), "\n")
    print(format_summary("Bu hafta", summarize(rows, week_start.isoformat())), "\n")
    print(format_summary("Tümü", summarize(rows)))


if __name__ == "__main__":
    main()
