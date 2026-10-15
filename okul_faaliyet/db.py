"""Veritabanı katmanı: tablolar, para/tarih yardımcıları ve durum hesaplama.

Tutarlar kuruş cinsinden tam sayı olarak saklanır (1.250,50 TL -> 125050).
Tarihler YYYY-AA-GG olarak saklanır, ekranda GG.AA.YYYY gösterilir.
"""
import re
import sqlite3
from datetime import date, datetime

SEMA = """
CREATE TABLE IF NOT EXISTS siniflar (
    id INTEGER PRIMARY KEY,
    ad TEXT NOT NULL UNIQUE,
    kademe TEXT NOT NULL DEFAULT '',
    ogretmen TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ogrenciler (
    id INTEGER PRIMARY KEY,
    sinif_id INTEGER NOT NULL REFERENCES siniflar(id),
    okul_no TEXT NOT NULL DEFAULT '',
    ad_soyad TEXT NOT NULL,
    veli_tel TEXT NOT NULL DEFAULT '',
    aktif INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS faaliyetler (
    id INTEGER PRIMARY KEY,
    ad TEXT NOT NULL,
    aciklama TEXT NOT NULL DEFAULT '',
    ucret INTEGER NOT NULL,
    son_tarih TEXT NOT NULL DEFAULT '',
    acik INTEGER NOT NULL DEFAULT 1,
    olusturma TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS faaliyet_siniflar (
    faaliyet_id INTEGER NOT NULL REFERENCES faaliyetler(id),
    sinif_id INTEGER NOT NULL REFERENCES siniflar(id),
    PRIMARY KEY (faaliyet_id, sinif_id)
);
-- Öğrenciye özel ayar: muaf, katılmıyor veya indirimli ücret
CREATE TABLE IF NOT EXISTS katilim_ayar (
    faaliyet_id INTEGER NOT NULL REFERENCES faaliyetler(id),
    ogrenci_id INTEGER NOT NULL REFERENCES ogrenciler(id),
    tip TEXT NOT NULL CHECK (tip IN ('muaf', 'katilmiyor', 'indirim')),
    ucret INTEGER,
    aciklama TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (faaliyet_id, ogrenci_id)
);
-- Ödemeler silinmez; hatalı kayıt iptal edilir ve iz kalır
CREATE TABLE IF NOT EXISTS odemeler (
    id INTEGER PRIMARY KEY,
    faaliyet_id INTEGER NOT NULL REFERENCES faaliyetler(id),
    ogrenci_id INTEGER NOT NULL REFERENCES ogrenciler(id),
    tutar INTEGER NOT NULL,
    tarih TEXT NOT NULL,
    aciklama TEXT NOT NULL DEFAULT '',
    kaydeden TEXT NOT NULL,
    kayit_zamani TEXT NOT NULL,
    iptal INTEGER NOT NULL DEFAULT 0,
    iptal_eden TEXT NOT NULL DEFAULT '',
    iptal_zamani TEXT NOT NULL DEFAULT '',
    iptal_neden TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kullanicilar (
    id INTEGER PRIMARY KEY,
    kullanici_adi TEXT NOT NULL UNIQUE,
    ad TEXT NOT NULL,
    sifre_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('yonetici', 'ogretmen')),
    sinif_id INTEGER REFERENCES siniflar(id)
);
CREATE TABLE IF NOT EXISTS islem_gecmisi (
    id INTEGER PRIMARY KEY,
    zaman TEXT NOT NULL,
    kullanici TEXT NOT NULL,
    islem TEXT NOT NULL,
    detay TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ayarlar (
    anahtar TEXT PRIMARY KEY,
    deger TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ogrenci_sinif ON ogrenciler(sinif_id);
CREATE INDEX IF NOT EXISTS ix_odeme_faaliyet ON odemeler(faaliyet_id, ogrenci_id);
"""

DURUM_TAMAM = "Tamamlandı"
DURUM_KISMI = "Kısmi"
DURUM_YOK = "Ödenmedi"
DURUM_MUAF = "Muaf"
DURUM_KATILMIYOR = "Katılmıyor"


def baglan(yol):
    con = sqlite3.connect(yol)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def kur(con):
    con.executescript(SEMA)
    con.commit()


def simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def bugun():
    return date.today().isoformat()


def para_oku(metin):
    """'1.250,50', '1250,5', '1250.50', '1250' -> kuruş. Geçersizse ValueError."""
    s = str(metin).strip().replace("TL", "").replace("₺", "").replace(" ", "")
    if not s:
        raise ValueError("Tutar boş")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise ValueError(f"Geçersiz tutar: {metin}")
    lira, _, kurus = s.partition(".")
    return int(lira) * 100 + int((kurus + "00")[:2])


def para_yaz(kurus, birim=True):
    """125050 -> '1.250,50 TL'"""
    kurus = int(kurus or 0)
    isaret = "-" if kurus < 0 else ""
    lira, k = divmod(abs(kurus), 100)
    metin = f"{isaret}{lira:,}".replace(",", ".") + f",{k:02d}"
    return metin + " TL" if birim else metin


def tarih_oku(metin):
    """'15.10.2026' veya '2026-10-15' -> '2026-10-15'. Geçersizse ValueError."""
    s = str(metin).strip()
    for bicim in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, bicim).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Geçersiz tarih: {metin}")


def tarih_yaz(iso):
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def kayit_ekle(con, kullanici, islem, detay=""):
    con.execute(
        "INSERT INTO islem_gecmisi (zaman, kullanici, islem, detay) VALUES (?, ?, ?, ?)",
        (simdi(), kullanici, islem, detay),
    )


def ayar_al(con, anahtar, varsayilan=""):
    r = con.execute("SELECT deger FROM ayarlar WHERE anahtar = ?", (anahtar,)).fetchone()
    return r["deger"] if r else varsayilan


def ayar_yaz(con, anahtar, deger):
    con.execute(
        "INSERT INTO ayarlar (anahtar, deger) VALUES (?, ?) "
        "ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger",
        (anahtar, deger),
    )


def ogrenci_durumlari(con, faaliyet_id, sinif_id=None, ogrenci_id=None):
    """Faaliyete dahil sınıflardaki aktif öğrencilerin ücret/ödenen/kalan/durum listesi.

    Pasif (nakil) öğrenci, faaliyette ödemesi varsa yine listelenir.
    """
    f = con.execute("SELECT * FROM faaliyetler WHERE id = ?", (faaliyet_id,)).fetchone()
    if f is None:
        return []
    sql = """
        SELECT o.id, o.okul_no, o.ad_soyad, o.veli_tel, o.aktif,
               s.id AS sinif_id, s.ad AS sinif,
               k.tip, k.ucret AS ozel_ucret, k.aciklama AS ayar_aciklama,
               COALESCE(p.odenen, 0) AS odenen, p.son_odeme, COALESCE(p.adet, 0) AS odeme_adet
        FROM ogrenciler o
        JOIN siniflar s ON s.id = o.sinif_id
        JOIN faaliyet_siniflar fs ON fs.sinif_id = s.id AND fs.faaliyet_id = :f
        LEFT JOIN katilim_ayar k ON k.faaliyet_id = :f AND k.ogrenci_id = o.id
        LEFT JOIN (
            SELECT ogrenci_id, SUM(tutar) AS odenen, MAX(tarih) AS son_odeme, COUNT(*) AS adet
            FROM odemeler WHERE faaliyet_id = :f AND iptal = 0 GROUP BY ogrenci_id
        ) p ON p.ogrenci_id = o.id
        WHERE (o.aktif = 1 OR COALESCE(p.odenen, 0) > 0)
    """
    params = {"f": faaliyet_id}
    if sinif_id is not None:
        sql += " AND s.id = :s"
        params["s"] = sinif_id
    if ogrenci_id is not None:
        sql += " AND o.id = :o"
        params["o"] = ogrenci_id

    sonuc = []
    for r in con.execute(sql, params):
        tip = r["tip"]
        if tip == "muaf":
            ucret = 0
        elif tip == "katilmiyor":
            ucret = 0
        elif tip == "indirim" and r["ozel_ucret"] is not None:
            ucret = r["ozel_ucret"]
        else:
            ucret = f["ucret"]
        odenen = r["odenen"]
        kalan = max(ucret - odenen, 0)
        if tip == "katilmiyor":
            durum = DURUM_KATILMIYOR
        elif tip == "muaf":
            durum = DURUM_MUAF
        elif odenen >= ucret:
            durum = DURUM_TAMAM
        elif odenen > 0:
            durum = DURUM_KISMI
        else:
            durum = DURUM_YOK
        sonuc.append({
            "id": r["id"], "okul_no": r["okul_no"], "ad_soyad": r["ad_soyad"],
            "veli_tel": r["veli_tel"], "aktif": r["aktif"],
            "sinif_id": r["sinif_id"], "sinif": r["sinif"],
            "tip": tip, "ayar_aciklama": r["ayar_aciklama"] or "",
            "ucret": ucret, "odenen": odenen, "kalan": kalan,
            "fazla": max(odenen - ucret, 0),
            "durum": durum, "son_odeme": r["son_odeme"] or "", "odeme_adet": r["odeme_adet"],
        })
    sonuc.sort(key=lambda o: (sinif_sira_anahtari(o["sinif"]), sinif_sira_anahtari(o["okul_no"]),
                              o["ad_soyad"]))
    return sonuc


def sinif_ozetleri(con, faaliyet_id, sinif_idler=None):
    """Sınıf başına: öğrenci, hedef, toplanan, kalan, yüzde ve durum sayıları."""
    ozet = {}
    for o in ogrenci_durumlari(con, faaliyet_id):
        if sinif_idler is not None and o["sinif_id"] not in sinif_idler:
            continue
        s = ozet.setdefault(o["sinif_id"], {
            "sinif_id": o["sinif_id"], "sinif": o["sinif"], "ogrenci": 0, "hedef": 0,
            "toplanan": 0, "kalan": 0, "tamam": 0, "kismi": 0, "yok": 0, "muaf": 0,
            "katilmiyor": 0,
        })
        if o["durum"] == DURUM_KATILMIYOR:
            s["katilmiyor"] += 1
            s["toplanan"] += o["odenen"]
            continue
        s["ogrenci"] += 1
        s["hedef"] += o["ucret"]
        s["toplanan"] += o["odenen"]
        s["kalan"] += o["kalan"]
        anahtar = {DURUM_TAMAM: "tamam", DURUM_KISMI: "kismi", DURUM_YOK: "yok", DURUM_MUAF: "muaf"}
        s[anahtar[o["durum"]]] += 1
    # Faaliyete dahil ama öğrencisi olmayan sınıflar da görünsün
    for r in con.execute(
        "SELECT s.id, s.ad FROM faaliyet_siniflar fs JOIN siniflar s ON s.id = fs.sinif_id "
        "WHERE fs.faaliyet_id = ?", (faaliyet_id,)
    ):
        if (sinif_idler is None or r["id"] in sinif_idler) and r["id"] not in ozet:
            ozet[r["id"]] = {
                "sinif_id": r["id"], "sinif": r["ad"], "ogrenci": 0, "hedef": 0, "toplanan": 0,
                "kalan": 0, "tamam": 0, "kismi": 0, "yok": 0, "muaf": 0, "katilmiyor": 0,
            }
    liste = sorted(ozet.values(), key=lambda s: sinif_sira_anahtari(s["sinif"]))
    for s in liste:
        s["yuzde"] = round(100 * s["toplanan"] / s["hedef"]) if s["hedef"] else 0
    return liste


def toplam(ozetler):
    t = {"ogrenci": 0, "hedef": 0, "toplanan": 0, "kalan": 0, "tamam": 0, "kismi": 0,
         "yok": 0, "muaf": 0, "katilmiyor": 0}
    for s in ozetler:
        for k in t:
            t[k] += s[k]
    t["yuzde"] = round(100 * t["toplanan"] / t["hedef"]) if t["hedef"] else 0
    return t


def sinif_sira_anahtari(ad):
    """'9-A' < '10-A' olacak şekilde doğal sıralama."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", ad)]
