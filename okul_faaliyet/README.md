# Okul Faaliyet Ücreti Takip Programı

Okulda düzenlenen faaliyetler (gezi, tiyatro, etkinlik vb.) için öğrencilerden toplanan paranın
öğrenci ve sınıf bazında takibini yapar. Hangi öğrencinin ne kadar ödediğini, kimin hiç ödemediğini,
sınıfların toplamda ne kadar topladığını gösterir; raporları Excel ve PDF olarak verir.

Program okulun bir bilgisayarında çalışır. Aynı bilgisayardan veya okul ağındaki diğer
bilgisayar, tablet ve telefonlardan tarayıcıyla kullanılır. Veriler yalnızca o bilgisayarda,
`veri` klasöründe durur.

## Kurulum (Windows)

1. [python.org](https://www.python.org/downloads/) adresinden Python'u kurun. Kurulumun ilk
   ekranında **"Add python.exe to PATH"** kutusunu işaretleyin.
2. Bu klasördeki **`baslat_okul.bat`** dosyasına çift tıklayın. İlk açılışta gerekli paketler
   kurulur (internet gerekir), ardından tarayıcı kendiliğinden açılır.
3. İlk ekranda okulun adını, adınızı, kullanıcı adınızı ve şifrenizi belirleyin.
4. **Sınıflar → Excel'den öğrenci listesi yükle** ile tüm okulun listesini aktarın.

Program penceresi açık kaldığı sürece çalışır. Kapatmak için siyah pencereyi kapatın.

Okul ağındaki diğer bilgisayarlardan girmek için siyah pencerede yazan
`http://192.168.x.x:5000` adresini kullanın. Windows güvenlik duvarı izin isterse
"Özel ağlar" için izin verin.

## Öğrenci listesi nasıl hazırlanır?

İlk satırda başlıklar olan bir Excel dosyası (.xlsx):

| Sınıf | Okul No | Ad Soyad | Veli Telefonu |
|---|---|---|---|
| 9-A | 101 | Ayşe Yılmaz | 0555 111 22 33 |
| 9-A | 102 | Mehmet Kaya | |

- "Ad Soyad" yerine ayrı **Adı** ve **Soyadı** sütunları da olabilir.
- Sınıf sütunu yoksa her sayfanın adı sınıf adı sayılır (her sınıf ayrı sayfada).
- Veli telefonu isteğe bağlıdır.
- Aynı okul numarası tekrar yüklenirse öğrenci yeniden eklenmez; adı ve sınıfı güncellenir.
- Program içinden örnek dosya indirilebilir.

Sınıf sayfasından numara ve ad soyad yapıştırarak tek tek öğrenci de eklenebilir.

## Kullanım

1. **Faaliyet Oluştur**: faaliyetin adı, öğrenci başı ücret, isteğe bağlı son ödeme tarihi ve
   katılacak sınıflar (tümü, kademe veya tek tek).
2. Faaliyete tıklayın, **sınıfı seçin**. Öğrenci listesi açılır.
3. Ödeme yapan öğrencinin satırına **tutarı** ve **tarihi** yazıp **Kaydet**'e basın.
   "Tamamı" düğmesi kalan borcu tutara yazar.
4. Durum kendiliğinden hesaplanır: **Tamamlandı**, **Kısmi** (kalan tutar yazılı),
   **Ödenmedi**, **Muaf**, **Katılmıyor**.
5. **Raporlar**'dan istediğiniz raporu ekranda görün, **Excel** veya **PDF** indirin.

### Raporlar

| Rapor | Gösterdiği |
|---|---|
| Sınıf ödeme listesi | Her öğrencinin ücreti, ödediği, kalanı, durumu |
| Hiç ödeme yapmayanlar | Hiç para vermeyen öğrenciler ve veli telefonu |
| Eksik ödeyenler | Kısmi ödeyenler ve kalan tutarları |
| Sınıf bazında özet | Her sınıfın hedefi, topladığı, kalanı ve tahsilat yüzdesi |
| Okul geneli özet | Toplam hedef, toplanan, kalan, en yüksek ve en düşük sınıf |
| Tarih aralığı tahsilat dökümü | Belirli günlerde alınan ödemeler |

Raporlar tek sınıf, birkaç sınıf veya tüm okul için alınabilir. PDF'lerde okul adı, rapor tarihi,
sayfa numarası ve "Hazırlayan / Onaylayan" imza alanları bulunur.

### Diğer özellikler

- **Muafiyet ve indirim**: öğrenci detay sayfasından muaf, indirimli ücret veya "katılmıyor".
- **Taksitli ödeme**: bir öğrencinin birden fazla ödemesi tarihleriyle ayrı ayrı tutulur.
- **Hatalı kayıt**: ödemeler silinmez, nedeni yazılarak iptal edilir; iz kalır.
- **Fazla ödeme uyarısı**: kalan borçtan fazla tutar ancak onay kutusuyla kaydedilir.
- **Kullanıcılar**: yönetici her şeyi görür; sınıf öğretmeni yalnızca kendi sınıfının ödemesini
  girer ve raporunu alır.
- **İşlem geçmişi**: kim, ne zaman, hangi ödemeyi girdi veya iptal etti.
- **Faaliyeti kapatma**: kapatılan faaliyete ödeme girilmez, raporları alınmaya devam eder.
- **Yedek**: Ayarlar sayfasından tüm verinin yedeği indirilir.

## Yedek ve veri

Tüm veriler `veri/okul.db` dosyasındadır. Haftada bir **Ayarlar → Yedeği indir** ile yedek alıp
USB belleğe kaydedin. Yedekten dönmek için programı kapatın, yedek dosyanın adını `okul.db` yapıp
`veri` klasörüne koyun.

Veriler internete gönderilmez. T.C. kimlik numarası tutulmaz. Veli telefonu yalnızca girilirse
saklanır (KVKK).

## Geliştirici notları

```bash
pip install -r requirements.txt
python app.py              # http://localhost:5000
python -m pytest -q test_okul.py
```

Flask + SQLite; Excel için openpyxl, PDF için reportlab (Türkçe karakterler için `fonts/`
klasöründeki DejaVu Sans fontu). Tutarlar veritabanında kuruş cinsinden tam sayı olarak tutulur.
