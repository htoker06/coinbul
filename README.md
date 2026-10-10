# Binance TR TRY Ajanı

Binance TR'deki bütün TRY paritelerini tarar ve kurallara uyan coinlerde sizin yerinize alım satım yapar.
Varsayılan mod **sanal paradır** (gerçek emir gönderilmez).

> Uyarı: Bu bir yatırım tavsiyesi değildir ve kâr garantisi yoktur. Kripto paralar çok oynaktır.
> Kaybetmeyi göze alamayacağınız parayla canlı moda geçmeyin.

## Ne yapar?

Her turda (varsayılan 60 saniye):

1. 24 saatlik hacmi `MIN_QUOTE_VOLUME_TRY` üstündeki TRY paritelerini seçer (en fazla `MAX_SYMBOLS`).
2. Her coin için 1dk, 5dk, 15dk, 30dk, 1s, 4s ve günlük mumlara, son 500 işleme (saniyelik akış) ve emir defterine bakar.
3. Her coine -1 ile +1 arasında bir puan ve bir durum verir:
   `ALIM BAŞLADI`, `YÜKSELİŞ EĞİLİMİ`, `NÖTR`, `DÜŞÜŞ EĞİLİMİ`, `SATIŞ BASKISI`, `ŞÜPHELİ HACİM`.
   - **Trend:** fiyat her zaman diliminde EMA20'nin üstünde mi?
   - **Hacim:** son mum hacmi önceki 20 mumun ortalamasının kaç katı?
   - **Alım/satım baskısı:** hacmin ne kadarı piyasa alıcısından geldi (taker buy oranı)?
   - **Sahte hacim:** hacim patladığı halde fiyatın kıpırdamaması, hep aynı miktarda işlemler,
     aynı miktarda ardışık al-sat, saat gibi düzenli işlem aralıkları, hacme göre sığ emir defteri.
     Sahte hacim puanı, coinin toplam puanını düşürür.
4. Sonucu ekrana ve `data/tarama.csv` dosyasına yazar.
5. Açık pozisyonları kontrol eder ve şu durumlarda satar: zarar durdur, kâr al, iz süren stop,
   süre dolması ya da sinyalin `SATIŞ BASKISI` veya `ŞÜPHELİ HACİM`e dönmesi.
6. Puanı `BUY_SCORE` üstündeki, sahte hacim şüphesi düşük, makası dar ve 24 saatte aşırı yükselmemiş
   coinlerden `TRADE_SIZE_TRY` tutarında alır.

## Güvenlik kilitleri

| Kilit | Ayar |
|---|---|
| Varsayılan sanal mod | `MODE=paper` |
| Canlı mod için çift onay | `MODE=live` ve `CONFIRM_LIVE=EVET` |
| İşlem başına tutar | `TRADE_SIZE_TRY` |
| En fazla açık pozisyon | `MAX_OPEN_POSITIONS` |
| Günlük zarar sınırı (aşılınca yeni alım yapılmaz) | `MAX_DAILY_LOSS_TRY` |
| Acil durdurma | Klasöre `STOP` adında boş bir dosya koyun: yeni alım yapılmaz ve ajan durur. Dosyanın içine `SAT` yazarsanız önce bütün pozisyonları satar, sonra durur. |

Ajan sadece kendi aldığı miktarı satar. Hesabınızda daha önceden bulunan coinlere dokunmaz.

## Kurulum

Python 3.10 veya daha yeni bir sürüm gerekir.

```bash
cd binance-tr-ajan
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

## Kullanım

```bash
python agent.py --scan   # sadece tara ve raporla
python agent.py --once   # tek tur (sanal) işlem
python agent.py          # sürekli çalış
```

Kayıtlar `data/` klasörüne yazılır:
- `tarama.csv`: son tarama
- `islemler_paper.csv` / `islemler_live.csv`: alım satım defteri
- `state_*.json`: açık pozisyonlar ve kâr/zarar
- `ajan.log`: ayrıntılı kayıt

## Önerilen yol

1. **1–2 hafta sanal modda çalıştırın.** `islemler_paper.csv` dosyasında kâr/zarar sonuçlarına bakın,
   gerekirse `.env` ayarlarını değiştirin.
2. Binance TR'de API anahtarı oluşturun. Sadece **Okuma + Spot İşlem** izni verin.
   **Para çekme izni vermeyin** ve anahtara kendi IP adresinizi kısıtlama olarak ekleyin.
3. Küçük bir tutarla canlıya geçin: `.env` içinde `MODE=live`, `CONFIRM_LIVE=EVET`,
   `TRADE_SIZE_TRY=200`, `MAX_OPEN_POSITIONS=2` gibi değerler kullanın.
4. İlk canlı işlemleri Binance TR uygulamasından kontrol edin. Alınan miktar ve fiyat ajanın kaydıyla uyuşmalı.

## Bilinen sınırlar

- Binance TR API adresleri belgelere göre yazıldı ama gerçek borsada denenmedi. Bu yüzden ilk iş
  `--scan` ile veri geldiğini, sonra küçük bir tutarla emir gönderimini doğrulayın.
- Sadece ana piyasa (`type=1`) TRY pariteleri taranır.
- Canlı satışta kaydedilen fiyat, o andaki en iyi alış fiyatıdır. Gerçekleşen fiyat biraz farklı olabilir;
  kesin rakam Binance TR işlem geçmişinde görünür.
- Ajan bilgisayarınız açık ve internete bağlıyken çalışır. Kapanırsa yeniden başlattığınızda açık
  pozisyonları `state_*.json` dosyasından okuyup takibe devam eder.

## Test

```bash
python -m unittest discover -s tests
```
