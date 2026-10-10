# Binance TR TRY Ajanı

Binance TR'deki TRY paritelerini tarar ve kurallara uyan coinlerde sizin yerinize alım satım yapar.
Varsayılan mod **sanal paradır** (gerçek emir gönderilmez).

> Uyarı: Bu bir yatırım tavsiyesi değildir ve kâr garantisi yoktur. Kripto paralar çok oynaktır.
> Kaybetmeyi göze alamayacağınız parayla canlı moda geçmeyin.

## Kurulum

Python 3.10 veya daha yeni bir sürüm gerekir.

```bash
git clone https://github.com/htoker06/coinbul.git
cd coinbul
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

## Windows'ta çift tıklayarak çalıştırma

| Dosya | Ne yapar |
|---|---|
| `baslat.bat` | Ajanı sürekli çalıştırır. İlk açılışta gerekli paketi kurar ve `.env` dosyasını oluşturur. Ajan bir hatayla kapanırsa 30 saniye sonra yeniden başlatır. |
| `tara.bat` | Sadece tarama yapar, işlem yapmaz. |
| `gecmis_test.bat` | Son 90 günle geçmiş veri testi yapar. |
| `karsilastir.bat` | Farklı ayarları aynı geçmiş veride yan yana dener: ilk 60 günde seçer, son 30 günde sınar. |
| `karsilastir_swing.bat` | Yavaş (swing) stratejinin ayarlarını 180 günle karşılaştırır; ilk satır karşılaştırma için mevcut stratejidir. |
| `karsilastir_180.bat` | Aynısını 180 günle yapar: ilk 120 günde seçer, son 60 günde sınar. Daha uzun sürer ama daha güvenilirdir. |
| `rapor.bat` | Performans raporunu gösterir. |

**Bilgisayar açılınca kendiliğinden başlasın:** `Win + R` tuşlarına basın, `shell:startup` yazıp Enter'a basın.
Açılan klasöre `baslat.bat` dosyasının **kısayolunu** koyun (dosyaya sağ tıklayın, "Kısayol oluştur" deyin,
kısayolu o klasöre taşıyın). Bilgisayarın uyku moduna geçmemesi için Ayarlar > Sistem > Güç bölümünden
uyku süresini "Hiçbir zaman" yapın.

## Kullanım (komut satırı)

```bash
python agent.py --scan      # sadece tara ve raporla, işlem yapma
python agent.py --once      # tek tur
python agent.py             # sürekli çalış
python backtest.py --days 90 --top 20   # kuralları geçmiş veriyle dene
python backtest.py --days 90 --top 20 --karsilastir   # farklı ayarları yan yana dene
python report.py            # sanal işlemlerin performans raporu (--live: canlı)
```

## Önerilen yol

1. `python backtest.py --days 180` ile kuralları geçmiş veride deneyin. Kazanma oranı, kâr faktörü
   ve en büyük düşüş size kabul edilebilir geliyor mu?
2. **1–2 hafta sanal modda çalıştırın** ve `python report.py` ile sonuçlara bakın.
3. Binance TR'de API anahtarı oluşturun: sadece **Okuma + Spot İşlem**, **para çekme izni kapalı**,
   IP kısıtlamalı. Anahtarı sadece kendi `.env` dosyanıza yazın, kimseyle paylaşmayın.
4. Küçük tutarla canlıya geçin: `MODE=live`, `CONFIRM_LIVE=EVET`, `RISK_PER_TRADE_TRY=15`,
   `MAX_TRADE_TRY=300`, `MAX_OPEN_POSITIONS=2`.
5. İlk canlı işlemleri Binance TR uygulamasından kontrol edin: miktar, fiyat ve **borsadaki stop emri**
   ajanın kaydıyla uyuşmalı.

## İki strateji

`.env` içindeki `STRATEGY` ayarıyla seçilir:

- **`momentum`** (varsayılan): 15 dakikalık hacim, alıcı baskısı ve trend sinyalleriyle hızlı işlem.
  Aşağıdaki "Ajan her turda ne yapar?" bölümü bu stratejiyi anlatır.
- **`swing`**: Günlük ve 4 saatlik trend yukarıyken, fiyat saatlik ortalamaya geri çekilip yeşil bir
  mumla döndüğünde alır. Stop geri çekilmenin dibinin altına, hedef stop mesafesinin `SWING_RR`
  katına konur (%7–25). 4 saatlik trend bozulursa satar. Kademeli satış yoktur; satıştan sonra aynı coin
  24 saat alınmaz, günde en fazla 2 alım yapılır. Sahte hacim, Bitcoin, pump, global karşılaştırma,
  kayma, saat ve zarar sınırı kontrolleri iki stratejide de geçerlidir.

## Ajan her turda ne yapar?

1. 24 saatlik hacmi `MIN_QUOTE_VOLUME_TRY` üstündeki TRY paritelerini seçer.
2. Her coin için 1dk–günlük mumlara, son 500 işleme ve emir defterine bakıp -1..+1 arası puan verir:
   trend %35, alıcı baskısı %35, hacim artışı %20, emir defteri %10; sahte hacim puanı kadar düşürülür.
3. Açık pozisyonları yönetir (aşağıdaki satış kuralları).
4. Bütün kontrollerden geçen coinleri alır.

### Alım kontrolleri

| # | Kontrol | Ayar |
|---|---|---|
| | Puan yeterli, sahte hacim düşük, makas dar, 24 saatte aşırı yükselmemiş | `BUY_SCORE`, `MAX_FAKE_SCORE`, `MAX_SPREAD_PCT`, `MAX_24H_CHANGE_PCT` |
| 1 | **Bitcoin filtresi:** BTC 4s ve günlükte ortalamanın altındaysa ya da 1 saatte sert düştüyse alım yok | `BTC_FILTER`, `BTC_MAX_DROP_1H_PCT` |
| 3 | **Mola:** üst üste zarardan sonra bekleme; zarar ettiren coini bir süre almama | `MAX_CONSECUTIVE_LOSSES`, `LOSS_PAUSE_MINUTES`, `SYMBOL_COOLDOWN_HOURS` |
| 4 | **Günlük ve haftalık zarar sınırı** | `MAX_DAILY_LOSS_TRY`, `MAX_WEEKLY_LOSS_TRY` |
| 5 | **Kayma koruması:** alım fiyatı defterde çok yukarı kayacaksa alma; limit emir kullan | `MAX_SLIPPAGE_PCT`, `ORDER_TYPE` |
| 7 | **Riskli coinler:** sabit değerli coinler, yeni listelenenler ve kara liste alınmaz | `MIN_LISTING_DAYS`, `BLACKLIST` |
| 8 | **Pump koruması:** 5 dakikada sert yükselen coin hemen alınmaz; geri çekilme ve yeniden yükseliş beklenir | `PUMP_5M_PCT`, `PUMP_PULLBACK_PCT` |
| 9 | **Global karşılaştırma:** TRY fiyatı global Binance'ten çok farklıysa ya da hacim sadece TR'de artıyorsa alma | `GLOBAL_CHECK`, `MAX_PREMIUM_PCT`, `LOCAL_RVOL_RATIO` |
| 10 | **Sahte emir duvarı:** kaybolan büyük emirler tespit edilir, emir defteri puanına sayılmaz | — |
| 11 | **Sinyal teyidi:** sinyal birkaç tur üst üste sürmeli | `CONFIRM_ROUNDS` |
| 17 | **Saat filtresi:** gece saatlerinde alım yok | `NO_BUY_HOURS` |

### Tutar ve satış kuralları

- **(2) Oynaklığa göre stop ve tutar:** Stop mesafesi coinin 1 saatlik ATR'sinin `ATR_MULT` katıdır
  (`MIN_STOP_PCT`–`MAX_STOP_PCT` arası). Alım tutarı, stop olursa yaklaşık `RISK_PER_TRADE_TRY`
  kaybedilecek şekilde hesaplanır (`MIN_TRADE_TRY`–`MAX_TRADE_TRY` arası).
- **Kâr al:** stop mesafesinin `REWARD_RISK` katı.
- **(16) Kademeli satış:** kâr stop mesafesine ulaşınca pozisyonun `PARTIAL_FRACTION` kadarı satılır,
  stop giriş fiyatının üstüne çekilir (kalan kısım zararla kapanmaz).
- **İz süren stop:** kâr stop mesafesini geçince stop, en yüksek fiyatın ATR x `TRAILING_ATR_MULT` altına taşınır.
- Süre dolması (`MAX_HOLD_HOURS`).
- **Sinyal döndü:** coinin durumu `SATIŞ BASKISI` / `ŞÜPHELİ HACİM` olursa, ama sadece pozisyon
  zarardayken ve sinyal `SIGNAL_EXIT_ROUNDS` tur sürdüyse. Kârdaki pozisyonları stoplar yönetir.
- **Aşırı işlem önleme:** satılan coin (kârla da olsa) `REENTRY_COOLDOWN_HOURS` saat yeniden alınmaz,
  günde en fazla `MAX_DAILY_BUYS` alım yapılır, kâr hedefi komisyonun `MIN_EDGE_MULT` katından
  küçükse işleme girilmez.
- **(6) Borsada stop:** Canlı modda stop emri Binance TR'ye de yazılır ve stop yükseldikçe güncellenir.
  Bilgisayar kapansa bile borsa stopu uygular (`EXCHANGE_STOP`).

## Takip ve kontrol

- **(14) Telegram:** Her alım/satım, hata ve günlük özet telefonunuza gelir. Komutlar:
  `/durum`, `/durdur` (yeni alım yok), `/devam`, `/hepsinisat`. Sadece `TELEGRAM_CHAT_ID`'den gelen
  komutlar kabul edilir. Kurulum `.env.example` içinde anlatılıyor.
- **(15) Sağlık kontrolü:** 3 tur üst üste hata olursa haber verir, 10 turda kapanır. Canlı modda
  borsadaki bakiye ajanın kaydıyla uyuşmazsa yeni alımları durdurup haber verir.
  Python tamamen kapanırsa (elektrik kesintisi gibi) mesaj gönderemez; bu durumda borsadaki stop emirleri korur.
- **STOP dosyası:** Klasöre `STOP` adlı boş bir dosya koyarsanız ajan yeni alım yapmadan durur;
  içine `SAT` yazarsanız önce bütün pozisyonları satar.
- **(13) Rapor:** `python report.py` günlük, haftalık ve toplam kâr/zarar, komisyon, kazanma oranı,
  sinyal türüne ve satış sebebine göre dağılım gösterir.

Ajan sadece kendi aldığı miktarı satar; hesabınızda önceden bulunan coinlere dokunmaz.

## Kayıtlar (`data/` klasörü)

- `tarama.csv`: son tarama
- `islemler_paper.csv` / `islemler_live.csv`: işlem defteri
- `state_*.json`: açık pozisyonlar, kâr/zarar, molalar
- `backtest_islemler.csv`: geçmiş veri testinin işlemleri
- `ajan.log`: ayrıntılı kayıt

## Bilinen sınırlar

- Binance TR API adresleri ve emir tipleri (limit, stop-limit, iptal) belgelere göre yazıldı ama gerçek
  borsada denenmedi. İlk iş `--scan` ile verinin geldiğini, sonra küçük bir tutarla emir, stop ve iptal
  işlemlerini Binance TR uygulamasından doğrulayın.
- Global karşılaştırma api.binance.com'a erişemezse uyarı yazıp bu kontrolü atlar.
- Geçmiş veri testinde saniyelik işlem akışı, emir defteri, global karşılaştırma ve 5dk pump kontrolü yoktur;
  sonuçları yaklaşık kabul edin.
- Sadece ana piyasa (`type=1`) TRY pariteleri taranır.

## Test

```bash
python -m unittest discover -s tests
```
