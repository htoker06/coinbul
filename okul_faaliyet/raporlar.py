"""Hazır raporlar. Her rapor ekranda, Excel'de ve PDF'te aynı tabloyu üretir.

Sütun tipleri: metin, para (kuruş), yuzde, tarih (ISO), sayi.
"""
import db

RAPOR_TURLERI = [
    ("sinif_listesi", "Sınıf ödeme listesi"),
    ("odemeyenler", "Hiç ödeme yapmayanlar"),
    ("eksik", "Eksik ödeyenler"),
    ("sinif_ozet", "Sınıf bazında özet"),
    ("okul_ozet", "Okul geneli özet"),
    ("tahsilat", "Tarih aralığı tahsilat dökümü"),
]
RAPOR_ADLARI = dict(RAPOR_TURLERI)


def rapor_uret(con, tur, faaliyet_id, sinif_idler=None, bas="", bit=""):
    """sinif_idler: None = tüm sınıflar, aksi halde izin verilen/seçilen sınıf id kümesi."""
    f = con.execute("SELECT * FROM faaliyetler WHERE id = ?", (faaliyet_id,)).fetchone()
    if f is None:
        raise ValueError("Faaliyet bulunamadı")
    if tur not in RAPOR_ADLARI:
        raise ValueError("Geçersiz rapor türü")
    rapor = {
        "baslik": RAPOR_ADLARI[tur],
        "faaliyet": f["ad"],
        "bilgi": [f"Faaliyet: {f['ad']}", f"Öğrenci başı ücret: {db.para_yaz(f['ucret'])}"],
        "sutunlar": [],
        "satirlar": [],
        "toplam": None,
    }
    if sinif_idler is not None:
        adlar = [r["ad"] for r in con.execute(
            f"SELECT ad FROM siniflar WHERE id IN ({','.join('?' * len(sinif_idler))})",
            list(sinif_idler))] if sinif_idler else []
        rapor["bilgi"].append("Sınıf: " + (", ".join(sorted(adlar, key=db.sinif_sira_anahtari)) or "-"))

    def ogrenciler():
        return [o for o in db.ogrenci_durumlari(con, faaliyet_id)
                if sinif_idler is None or o["sinif_id"] in sinif_idler]

    if tur == "sinif_listesi":
        rapor["sutunlar"] = [("Sınıf", "metin"), ("No", "metin"), ("Ad Soyad", "metin"),
                             ("Ücret", "para"), ("Ödenen", "para"), ("Kalan", "para"),
                             ("Durum", "metin"), ("Son ödeme", "tarih")]
        liste = ogrenciler()
        rapor["satirlar"] = [[o["sinif"], o["okul_no"], o["ad_soyad"], o["ucret"], o["odenen"],
                              o["kalan"], o["durum"], o["son_odeme"]] for o in liste]
        rapor["toplam"] = ["Toplam", "", f"{len(liste)} öğrenci",
                           sum(o["ucret"] for o in liste), sum(o["odenen"] for o in liste),
                           sum(o["kalan"] for o in liste), "", ""]

    elif tur == "odemeyenler":
        rapor["sutunlar"] = [("Sınıf", "metin"), ("No", "metin"), ("Ad Soyad", "metin"),
                             ("Veli telefonu", "metin"), ("Borç", "para")]
        liste = [o for o in ogrenciler() if o["durum"] == db.DURUM_YOK]
        rapor["satirlar"] = [[o["sinif"], o["okul_no"], o["ad_soyad"], o["veli_tel"], o["kalan"]]
                             for o in liste]
        rapor["toplam"] = ["Toplam", "", f"{len(liste)} öğrenci", "", sum(o["kalan"] for o in liste)]

    elif tur == "eksik":
        rapor["sutunlar"] = [("Sınıf", "metin"), ("No", "metin"), ("Ad Soyad", "metin"),
                             ("Ücret", "para"), ("Ödenen", "para"), ("Kalan", "para"),
                             ("Veli telefonu", "metin")]
        liste = [o for o in ogrenciler() if o["durum"] == db.DURUM_KISMI]
        rapor["satirlar"] = [[o["sinif"], o["okul_no"], o["ad_soyad"], o["ucret"], o["odenen"],
                              o["kalan"], o["veli_tel"]] for o in liste]
        rapor["toplam"] = ["Toplam", "", f"{len(liste)} öğrenci", sum(o["ucret"] for o in liste),
                           sum(o["odenen"] for o in liste), sum(o["kalan"] for o in liste), ""]

    elif tur == "sinif_ozet":
        rapor["sutunlar"] = [("Sınıf", "metin"), ("Öğrenci", "sayi"), ("Hedef", "para"),
                             ("Toplanan", "para"), ("Kalan", "para"), ("Tahsilat", "yuzde"),
                             ("Tamamlayan", "sayi"), ("Kısmi", "sayi"), ("Ödemeyen", "sayi"),
                             ("Muaf", "sayi")]
        ozetler = db.sinif_ozetleri(con, faaliyet_id, sinif_idler)
        rapor["satirlar"] = [[s["sinif"], s["ogrenci"], s["hedef"], s["toplanan"], s["kalan"],
                              s["yuzde"], s["tamam"], s["kismi"], s["yok"], s["muaf"]]
                             for s in ozetler]
        t = db.toplam(ozetler)
        rapor["toplam"] = ["Toplam", t["ogrenci"], t["hedef"], t["toplanan"], t["kalan"],
                           t["yuzde"], t["tamam"], t["kismi"], t["yok"], t["muaf"]]

    elif tur == "okul_ozet":
        ozetler = db.sinif_ozetleri(con, faaliyet_id, sinif_idler)
        t = db.toplam(ozetler)
        dolu = [s for s in ozetler if s["hedef"]]
        en_iyi = max(dolu, key=lambda s: s["yuzde"], default=None)
        en_kotu = min(dolu, key=lambda s: s["yuzde"], default=None)
        rapor["sutunlar"] = [("Gösterge", "metin"), ("Değer", "metin")]
        rapor["satirlar"] = [
            ["Katılan sınıf sayısı", str(len(ozetler))],
            ["Ücret ödeyecek öğrenci", str(t["ogrenci"] - t["muaf"])],
            ["Muaf öğrenci", str(t["muaf"])],
            ["Katılmayan öğrenci", str(t["katilmiyor"])],
            ["Toplanması gereken", db.para_yaz(t["hedef"])],
            ["Toplanan", db.para_yaz(t["toplanan"])],
            ["Kalan", db.para_yaz(t["kalan"])],
            ["Tahsilat oranı", f"%{t['yuzde']}"],
            ["Tamamlayan / kısmi / ödemeyen", f"{t['tamam']} / {t['kismi']} / {t['yok']}"],
            ["En yüksek tahsilat", f"{en_iyi['sinif']} (%{en_iyi['yuzde']})" if en_iyi else "-"],
            ["En düşük tahsilat", f"{en_kotu['sinif']} (%{en_kotu['yuzde']})" if en_kotu else "-"],
        ]

    elif tur == "tahsilat":
        rapor["sutunlar"] = [("Tarih", "tarih"), ("Sınıf", "metin"), ("No", "metin"),
                             ("Ad Soyad", "metin"), ("Tutar", "para"), ("Açıklama", "metin"),
                             ("Kaydeden", "metin")]
        sql = """SELECT p.*, o.okul_no, o.ad_soyad, s.ad AS sinif, o.sinif_id
                 FROM odemeler p JOIN ogrenciler o ON o.id = p.ogrenci_id
                 JOIN siniflar s ON s.id = o.sinif_id
                 WHERE p.faaliyet_id = ? AND p.iptal = 0"""
        params = [faaliyet_id]
        if bas:
            sql += " AND p.tarih >= ?"
            params.append(bas)
        if bit:
            sql += " AND p.tarih <= ?"
            params.append(bit)
        sql += " ORDER BY p.tarih, p.id"
        liste = [r for r in con.execute(sql, params)
                 if sinif_idler is None or r["sinif_id"] in sinif_idler]
        rapor["satirlar"] = [[r["tarih"], r["sinif"], r["okul_no"], r["ad_soyad"], r["tutar"],
                              r["aciklama"], r["kaydeden"]] for r in liste]
        rapor["toplam"] = ["Toplam", "", "", f"{len(liste)} ödeme", sum(r["tutar"] for r in liste),
                           "", ""]
        aralik = f"{db.tarih_yaz(bas) or 'başlangıç'} - {db.tarih_yaz(bit) or 'bugün'}"
        rapor["bilgi"].append(f"Tarih aralığı: {aralik}")

    return rapor


def hucre_metni(deger, tip):
    if tip == "para":
        return db.para_yaz(deger) if deger != "" else ""
    if tip == "yuzde":
        return f"%{deger}" if deger != "" else ""
    if tip == "tarih":
        return db.tarih_yaz(deger)
    return "" if deger is None else str(deger)
