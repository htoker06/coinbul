# Minik Adımlar — Anasınıfı Planlayıcı

Türkiye Yüzyılı Maarif Modeli Okul Öncesi Eğitim Programı yapısına göre
yıllık, aylık ve günlük plan hazırlamak ve bol bol etkinlik üretmek için
tek dosyalık bir web uygulaması.

## Nasıl açılır?

`index.html` dosyasını çift tıklayarak tarayıcıda açmanız yeterli; kurulum,
internet bağlantısı (yazı tipleri hariç) veya sunucu gerekmez.

## Neler var?

- **Yıllık Plan** — Eylül–Haziran arası 10 ay; her ay için tema, haftalık
  konular, belirli gün ve haftalar, değerler ve odak alanlar. Hepsi düzenlenebilir.
- **Aylık Plan** — Tema bannerı, haftalık kartlar, takvim, alan becerileri ve
  öğrenme çıktıları seçimi, kavramsal beceriler, eğilimler, SDÖ becerileri,
  değerler, okuryazarlık becerileri, içerik çerçevesi, aile katılımı ve değerlendirme.
  "Tüm iş günlerini planla" ile ayın bütün günleri tek dokunuşla doldurulur.
- **Günlük Plan** — Maarif Modeli başlıklarıyla (alan becerileri → değerlendirme)
  ve resimli günlük akışla. Etkinlik eklendikçe çıktılar, materyaller, kavramlar,
  farklılaştırma ve aile katılımı otomatik dolar.
- **Etkinlik Üretici** — 7 alan × 18 tema × 3 yaş grubu; her etkinlikte görsel,
  materyaller, dikkat çekme, adımlar, değerlendirme soruları, destekleme /
  zenginleştirme ve aile katılımı bulunur. Tek dokunuşla plana ya da bankaya eklenir.
- **Etkinlik Bankam** — Kaydedilen etkinlikler; arama ve alan filtresi.
- **Ayarlar** — Öğretmen/okul bilgileri, yedekleme (JSON), öğrenme çıktıları listesini düzenleme.

Veriler tarayıcının yerel deposunda (localStorage) tutulur. Başka cihaza
taşımak için Ayarlar → Yedekleme bölümünü kullanın.

> Öğrenme çıktısı kodları ve ifadeleri programın yapısına göre hazırlanmış bir
> çalışma listesidir; resmî program metniyle karşılaştırıp Ayarlar'dan düzenleyin.
