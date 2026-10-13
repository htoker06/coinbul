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
- **Anekdot Kayıtları (EK-2)** — Programdaki anekdot kayıt formunun alanlarıyla
  (çocuğun adı soyadı, tarih, gözlenen mekân, gözlenen durum, gözlenen beceriler,
  gözlemcinin genel değerlendirmesi). Gözlenen beceriler o günün planından önerilir
  ya da programdan seçilir; kayıtlar çocuğa göre süzülür.
- **Word’e aktar** — Günlük plan (EK-6), aylık plan (EK-5) ve anekdot kayıtları
  (tek kayıt ya da bir çocuğun tüm kayıtları) gerçek `.docx` dosyası olarak indirilir.
  Dosya tarayıcıda, internet ya da ek program gerekmeden oluşturulur.
- **Ayarlar** — Okul, öğretmen, yaş grubu; programdaki tüm çıktıları arayabileceğiniz program kütüphanesi; yedekleme (JSON).

Veriler tarayıcının yerel deposunda (localStorage) tutulur. Başka cihaza
taşımak için Ayarlar → Yedekleme bölümünü kullanın.

## Kaynak

Tüm kodlar ve ifadeler MEB Türkiye Yüzyılı Maarif Modeli Okul Öncesi Eğitim
Programı'ndan alınmıştır (tymm.meb.gov.tr):

- **EK-1** alan tabloları: 36-48, 48-60 ve 60-72 ay için tüm öğrenme çıktıları ve
  süreç bileşenleri (ör. `MAB6. Sayma → MAB.1. → MAB.1.e.`)
- **EK-5 / EK-6**: aylık ve günlük plan şablonları; bölüm adları ve sırası bu
  şablonlarla aynıdır
- **EK-7** kavram listesi, **EK-8** belirli gün ve haftalar, **EK-15** aylık
  eğitim planı kontrol çizelgesi
- Eğilimler (E1.1…), sosyal-duygusal öğrenme becerileri (SDB…), değerler (D1.1…)
  ve okuryazarlık becerileri (OB…) programdaki numaralarıyla
- Genel metinler, [60-72 ay Mayıs aylık plan örneği](https://tymm.meb.gov.tr/okul-oncesi/unite/481)
  ve [60-72 ay günlük plan örneği](https://tymm.meb.gov.tr/okul-oncesi/unite/484) esas alınarak yazılmıştır

Etkinlik adımlarının sonunda, MEB örneğinde olduğu gibi ilgili kodlar parantez
içinde yer alır, ör. `(MAB.1.e, SDB2.2)`. Süreç bileşeni harfleri yaş grubuna göre
farklı anlam taşıdığı için (ör. MAB.1.b) etkinlikler seçilen yaş grubunun kendi
bileşenlerine çevrilir; karşılığı olmayan kod o yaş için gösterilmez.
