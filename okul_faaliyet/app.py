"""Okul Faaliyet Ücreti Takip Programı.

Çalıştırma: python app.py  ->  tarayıcıda http://localhost:5000
Okul ağındaki diğer bilgisayarlar: http://<bu bilgisayarın IP adresi>:5000
"""
import functools
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import cikti
import db
import ice_aktar
from raporlar import RAPOR_ADLARI, RAPOR_TURLERI, hucre_metni, rapor_uret

BURASI = os.path.dirname(os.path.abspath(__file__))


def uygulama_olustur(veri_yolu=None):
    app = Flask(__name__)
    veri_klasoru = os.path.join(BURASI, "veri")
    app.config["VERITABANI"] = veri_yolu or os.path.join(veri_klasoru, "okul.db")
    os.makedirs(os.path.dirname(app.config["VERITABANI"]), exist_ok=True)
    app.secret_key = _gizli_anahtar(os.path.dirname(app.config["VERITABANI"]))
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    with sqlite3.connect(app.config["VERITABANI"]) as c:
        c.executescript(db.SEMA)

    app.jinja_env.filters["para"] = db.para_yaz
    app.jinja_env.filters["tarih"] = db.tarih_yaz
    app.jinja_env.globals["hucre_metni"] = hucre_metni

    @app.before_request
    def hazirla():
        g.con = db.baglan(app.config["VERITABANI"])
        g.kullanici = None
        if "kullanici_id" in session:
            g.kullanici = g.con.execute("SELECT * FROM kullanicilar WHERE id = ?",
                                        (session["kullanici_id"],)).fetchone()
        if request.method == "POST":
            if request.form.get("csrf") != session.get("csrf"):
                abort(400, "Oturum süresi doldu, sayfayı yenileyip tekrar deneyin.")

    @app.teardown_request
    def kapat(_hata):
        con = g.pop("con", None)
        if con is not None:
            con.close()

    @app.context_processor
    def sablon_degiskenleri():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {
            "csrf": session["csrf"],
            "kullanici": g.get("kullanici"),
            "yonetici": _yonetici(),
            "okul_adi": db.ayar_al(g.con, "okul_adi", "Okul") if "con" in g else "Okul",
            "bugun": db.bugun(),
        }

    _rotalar(app)
    return app


def _gizli_anahtar(klasor):
    yol = os.path.join(klasor, ".gizli_anahtar")
    if not os.path.exists(yol):
        with open(yol, "w") as f:
            f.write(secrets.token_hex(32))
    with open(yol) as f:
        return f.read().strip()


def _yonetici():
    k = g.get("kullanici")
    return k is not None and k["rol"] == "yonetici"


def izinli_siniflar():
    """Yönetici için None (tümü), öğretmen için yalnızca kendi sınıfı."""
    if _yonetici():
        return None
    k = g.kullanici
    return {k["sinif_id"]} if k and k["sinif_id"] else set()


def sinif_izni(sinif_id):
    izin = izinli_siniflar()
    if izin is not None and sinif_id not in izin:
        abort(403)


def giris_gerekli(f):
    @functools.wraps(f)
    def sarmal(*a, **k):
        if g.con.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0] == 0:
            return redirect(url_for("kurulum"))
        if g.kullanici is None:
            return redirect(url_for("giris", sonra=request.path))
        return f(*a, **k)
    return sarmal


def yonetici_gerekli(f):
    @functools.wraps(f)
    @giris_gerekli
    def sarmal(*a, **k):
        if not _yonetici():
            abort(403)
        return f(*a, **k)
    return sarmal


def kaydet_gecmis(islem, detay=""):
    db.kayit_ekle(g.con, g.kullanici["ad"] if g.kullanici else "-", islem, detay)


def faaliyet_getir(fid):
    f = g.con.execute("SELECT * FROM faaliyetler WHERE id = ?", (fid,)).fetchone()
    if f is None:
        abort(404)
    return f


def _rotalar(app):

    # ---------------- Giriş / kurulum ----------------

    @app.route("/kurulum", methods=["GET", "POST"])
    def kurulum():
        if g.con.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0] > 0:
            return redirect(url_for("ana_sayfa"))
        if request.method == "POST":
            f = request.form
            if not f.get("kullanici_adi") or len(f.get("sifre", "")) < 6:
                flash("Kullanıcı adı girin; şifre en az 6 karakter olmalı.", "hata")
            elif f.get("sifre") != f.get("sifre2"):
                flash("Şifreler aynı değil.", "hata")
            else:
                db.ayar_yaz(g.con, "okul_adi", f.get("okul_adi", "").strip() or "Okul")
                db.ayar_yaz(g.con, "mudur_adi", f.get("ad", "").strip())
                cur = g.con.execute(
                    "INSERT INTO kullanicilar (kullanici_adi, ad, sifre_hash, rol) "
                    "VALUES (?, ?, ?, 'yonetici')",
                    (f["kullanici_adi"].strip(), f.get("ad", "").strip() or "Müdür",
                     generate_password_hash(f["sifre"])))
                session["kullanici_id"] = cur.lastrowid
                db.kayit_ekle(g.con, f.get("ad", "") or "Müdür", "Kurulum yapıldı")
                g.con.commit()
                flash("Kurulum tamamlandı. Şimdi sınıf ve öğrenci listesini yükleyin.", "tamam")
                return redirect(url_for("ice_aktar_sayfa"))
        return render_template("kurulum.html")

    @app.route("/giris", methods=["GET", "POST"])
    def giris():
        if g.con.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0] == 0:
            return redirect(url_for("kurulum"))
        if request.method == "POST":
            k = g.con.execute("SELECT * FROM kullanicilar WHERE kullanici_adi = ?",
                              (request.form.get("kullanici_adi", "").strip(),)).fetchone()
            if k and check_password_hash(k["sifre_hash"], request.form.get("sifre", "")):
                session.clear()
                session["kullanici_id"] = k["id"]
                sonra = request.args.get("sonra", "")
                return redirect(sonra if sonra.startswith("/") and not sonra.startswith("//")
                                else url_for("ana_sayfa"))
            flash("Kullanıcı adı veya şifre hatalı.", "hata")
        return render_template("giris.html")

    @app.route("/cikis", methods=["POST"])
    def cikis():
        session.clear()
        return redirect(url_for("giris"))

    @app.route("/sifre", methods=["GET", "POST"])
    @giris_gerekli
    def sifre_degistir():
        if request.method == "POST":
            f = request.form
            if not check_password_hash(g.kullanici["sifre_hash"], f.get("eski", "")):
                flash("Mevcut şifre hatalı.", "hata")
            elif len(f.get("yeni", "")) < 6 or f.get("yeni") != f.get("yeni2"):
                flash("Yeni şifre en az 6 karakter olmalı ve iki kez aynı yazılmalı.", "hata")
            else:
                g.con.execute("UPDATE kullanicilar SET sifre_hash = ? WHERE id = ?",
                              (generate_password_hash(f["yeni"]), g.kullanici["id"]))
                g.con.commit()
                flash("Şifreniz değiştirildi.", "tamam")
                return redirect(url_for("ana_sayfa"))
        return render_template("sifre.html")

    # ---------------- Ana sayfa ----------------

    @app.route("/")
    @giris_gerekli
    def ana_sayfa():
        izin = izinli_siniflar()
        faaliyetler = []
        for f in g.con.execute("SELECT * FROM faaliyetler ORDER BY acik DESC, id DESC"):
            ozet = db.sinif_ozetleri(g.con, f["id"], izin)
            if izin is not None and not ozet:
                continue
            faaliyetler.append({"f": f, "t": db.toplam(ozet), "sinif_sayisi": len(ozet)})
        sinif_sayisi = g.con.execute("SELECT COUNT(*) FROM siniflar").fetchone()[0]
        return render_template("ana_sayfa.html", faaliyetler=faaliyetler,
                               sinif_sayisi=sinif_sayisi)

    # ---------------- Faaliyetler ----------------

    def _faaliyet_formu(f_mevcut=None):
        form = request.form
        hatalar = []
        ad = form.get("ad", "").strip()
        if not ad:
            hatalar.append("Faaliyet adını yazın.")
        try:
            ucret = db.para_oku(form.get("ucret", ""))
            if ucret <= 0:
                hatalar.append("Ücret sıfırdan büyük olmalı.")
        except ValueError:
            ucret = 0
            hatalar.append("Ücreti rakamla yazın (ör. 250 veya 250,50).")
        son_tarih = ""
        if form.get("son_tarih"):
            try:
                son_tarih = db.tarih_oku(form["son_tarih"])
            except ValueError:
                hatalar.append("Son ödeme tarihi geçersiz.")
        siniflar = {int(s) for s in form.getlist("sinif") if s.isdigit()}
        if not siniflar:
            hatalar.append("En az bir sınıf seçin.")
        return hatalar, {"ad": ad, "aciklama": form.get("aciklama", "").strip(), "ucret": ucret,
                         "son_tarih": son_tarih, "siniflar": siniflar}

    def _sinif_listesi():
        return sorted(g.con.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM ogrenciler o WHERE o.sinif_id = s.id AND o.aktif = 1)"
            " AS mevcut FROM siniflar s").fetchall(),
            key=lambda s: db.sinif_sira_anahtari(s["ad"]))

    @app.route("/faaliyet/yeni", methods=["GET", "POST"])
    @yonetici_gerekli
    def faaliyet_yeni():
        siniflar = _sinif_listesi()
        if request.method == "POST":
            hatalar, v = _faaliyet_formu()
            if not hatalar:
                cur = g.con.execute(
                    "INSERT INTO faaliyetler (ad, aciklama, ucret, son_tarih, olusturma) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (v["ad"], v["aciklama"], v["ucret"], v["son_tarih"], db.simdi()))
                fid = cur.lastrowid
                g.con.executemany("INSERT INTO faaliyet_siniflar VALUES (?, ?)",
                                  [(fid, s) for s in v["siniflar"]])
                kaydet_gecmis("Faaliyet oluşturuldu",
                              f"{v['ad']} - {db.para_yaz(v['ucret'])}, {len(v['siniflar'])} sınıf")
                g.con.commit()
                flash(f"“{v['ad']}” faaliyeti oluşturuldu.", "tamam")
                return redirect(url_for("faaliyet", fid=fid))
            for h in hatalar:
                flash(h, "hata")
        return render_template("faaliyet_form.html", siniflar=siniflar, f=None,
                               secili={int(s) for s in request.form.getlist("sinif") if s.isdigit()})

    @app.route("/faaliyet/<int:fid>/duzenle", methods=["GET", "POST"])
    @yonetici_gerekli
    def faaliyet_duzenle(fid):
        f = faaliyet_getir(fid)
        siniflar = _sinif_listesi()
        secili = {r[0] for r in g.con.execute(
            "SELECT sinif_id FROM faaliyet_siniflar WHERE faaliyet_id = ?", (fid,))}
        if request.method == "POST":
            hatalar, v = _faaliyet_formu(f)
            cikan = secili - v["siniflar"]
            if cikan and g.con.execute(
                    f"SELECT COUNT(*) FROM odemeler p JOIN ogrenciler o ON o.id = p.ogrenci_id "
                    f"WHERE p.faaliyet_id = ? AND p.iptal = 0 AND o.sinif_id IN "
                    f"({','.join('?' * len(cikan))})", [fid, *cikan]).fetchone()[0]:
                hatalar.append("Ödeme alınmış bir sınıf faaliyetten çıkarılamaz.")
            if not hatalar:
                g.con.execute(
                    "UPDATE faaliyetler SET ad = ?, aciklama = ?, ucret = ?, son_tarih = ?, acik = ? "
                    "WHERE id = ?",
                    (v["ad"], v["aciklama"], v["ucret"], v["son_tarih"],
                     0 if request.form.get("kapali") else 1, fid))
                g.con.execute("DELETE FROM faaliyet_siniflar WHERE faaliyet_id = ?", (fid,))
                g.con.executemany("INSERT INTO faaliyet_siniflar VALUES (?, ?)",
                                  [(fid, s) for s in v["siniflar"]])
                kaydet_gecmis("Faaliyet güncellendi",
                              f"{v['ad']} - {db.para_yaz(v['ucret'])}"
                              + (" (kapatıldı)" if request.form.get("kapali") else ""))
                g.con.commit()
                flash("Faaliyet güncellendi.", "tamam")
                return redirect(url_for("faaliyet", fid=fid))
            for h in hatalar:
                flash(h, "hata")
        return render_template("faaliyet_form.html", siniflar=siniflar, f=f, secili=secili)

    @app.route("/faaliyet/<int:fid>")
    @giris_gerekli
    def faaliyet(fid):
        f = faaliyet_getir(fid)
        ozetler = db.sinif_ozetleri(g.con, fid, izinli_siniflar())
        if izinli_siniflar() is not None and len(ozetler) == 1:
            return redirect(url_for("tahsilat", fid=fid, sid=ozetler[0]["sinif_id"]))
        return render_template("faaliyet.html", f=f, ozetler=ozetler, t=db.toplam(ozetler))

    @app.route("/faaliyet/<int:fid>/sinif/<int:sid>")
    @giris_gerekli
    def tahsilat(fid, sid):
        f = faaliyet_getir(fid)
        sinif_izni(sid)
        sinif = g.con.execute("SELECT * FROM siniflar WHERE id = ?", (sid,)).fetchone()
        if sinif is None or not g.con.execute(
                "SELECT 1 FROM faaliyet_siniflar WHERE faaliyet_id = ? AND sinif_id = ?",
                (fid, sid)).fetchone():
            abort(404)
        ogrenciler = db.ogrenci_durumlari(g.con, fid, sinif_id=sid)
        ozet = db.sinif_ozetleri(g.con, fid, {sid})
        diger = [s for s in db.sinif_ozetleri(g.con, fid, izinli_siniflar())]
        return render_template("tahsilat.html", f=f, sinif=sinif, ogrenciler=ogrenciler,
                               ozet=ozet[0] if ozet else None, diger=diger)

    @app.route("/faaliyet/<int:fid>/odeme", methods=["POST"])
    @giris_gerekli
    def odeme_ekle(fid):
        f = faaliyet_getir(fid)
        oid = request.form.get("ogrenci_id", type=int)
        durum = db.ogrenci_durumlari(g.con, fid, ogrenci_id=oid)
        if not durum:
            abort(404)
        o = durum[0]
        sinif_izni(o["sinif_id"])
        geri = request.form.get("geri") or url_for("tahsilat", fid=fid, sid=o["sinif_id"])
        if not geri.startswith("/") or geri.startswith("//"):
            geri = url_for("tahsilat", fid=fid, sid=o["sinif_id"])
        if not f["acik"]:
            flash("Bu faaliyet kapatılmış; ödeme girilemez.", "hata")
            return redirect(geri)
        try:
            tutar = db.para_oku(request.form.get("tutar", ""))
            if tutar <= 0:
                raise ValueError
        except ValueError:
            flash(f"{o['ad_soyad']}: tutarı rakamla yazın (ör. 100 veya 100,50).", "hata")
            return redirect(geri)
        try:
            tarih = db.tarih_oku(request.form.get("tarih") or db.bugun())
        except ValueError:
            flash("Tarih geçersiz.", "hata")
            return redirect(geri)
        if tarih > db.bugun():
            flash("İleri tarihli ödeme girilemez.", "hata")
            return redirect(geri)
        if tutar > o["kalan"] and not request.form.get("fazla_onay"):
            flash(f"{o['ad_soyad']}: girilen {db.para_yaz(tutar)}, kalan borçtan "
                  f"({db.para_yaz(o['kalan'])}) fazla. Yine de kaydetmek için "
                  f"öğrenci sayfasında “fazla ödemeyi onayla” kutusunu işaretleyin.", "hata")
            return redirect(url_for("ogrenci_detay", fid=fid, oid=oid))
        g.con.execute(
            "INSERT INTO odemeler (faaliyet_id, ogrenci_id, tutar, tarih, aciklama, kaydeden, "
            "kayit_zamani) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fid, oid, tutar, tarih, request.form.get("aciklama", "").strip(),
             g.kullanici["ad"], db.simdi()))
        kaydet_gecmis("Ödeme alındı", f"{f['ad']} / {o['sinif']} {o['okul_no']} {o['ad_soyad']}: "
                                      f"{db.para_yaz(tutar)} ({db.tarih_yaz(tarih)})")
        g.con.commit()
        yeni = db.ogrenci_durumlari(g.con, fid, ogrenci_id=oid)[0]
        if yeni["kalan"] == 0:
            flash(f"{o['ad_soyad']}: {db.para_yaz(tutar)} kaydedildi. Ödeme TAMAMLANDI.", "tamam")
        else:
            flash(f"{o['ad_soyad']}: {db.para_yaz(tutar)} kaydedildi. Kalan: "
                  f"{db.para_yaz(yeni['kalan'])}.", "tamam")
        return redirect(geri)

    @app.route("/faaliyet/<int:fid>/ogrenci/<int:oid>")
    @giris_gerekli
    def ogrenci_detay(fid, oid):
        f = faaliyet_getir(fid)
        durum = db.ogrenci_durumlari(g.con, fid, ogrenci_id=oid)
        if not durum:
            abort(404)
        o = durum[0]
        sinif_izni(o["sinif_id"])
        odemeler = g.con.execute(
            "SELECT * FROM odemeler WHERE faaliyet_id = ? AND ogrenci_id = ? ORDER BY tarih, id",
            (fid, oid)).fetchall()
        return render_template("ogrenci.html", f=f, o=o, odemeler=odemeler)

    @app.route("/faaliyet/<int:fid>/ogrenci/<int:oid>/ayar", methods=["POST"])
    @yonetici_gerekli
    def katilim_ayar(fid, oid):
        f = faaliyet_getir(fid)
        durum = db.ogrenci_durumlari(g.con, fid, ogrenci_id=oid)
        if not durum:
            abort(404)
        o = durum[0]
        tip = request.form.get("tip", "")
        aciklama = request.form.get("aciklama", "").strip()
        if tip == "normal":
            g.con.execute("DELETE FROM katilim_ayar WHERE faaliyet_id = ? AND ogrenci_id = ?",
                          (fid, oid))
            detay = "normal ücret"
        elif tip in ("muaf", "katilmiyor", "indirim"):
            ucret = None
            if tip == "indirim":
                try:
                    ucret = db.para_oku(request.form.get("ucret", ""))
                except ValueError:
                    flash("İndirimli ücreti rakamla yazın.", "hata")
                    return redirect(url_for("ogrenci_detay", fid=fid, oid=oid))
                if ucret >= f["ucret"]:
                    flash("İndirimli ücret normal ücretten düşük olmalı.", "hata")
                    return redirect(url_for("ogrenci_detay", fid=fid, oid=oid))
            g.con.execute(
                "INSERT INTO katilim_ayar (faaliyet_id, ogrenci_id, tip, ucret, aciklama) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(faaliyet_id, ogrenci_id) DO UPDATE SET "
                "tip = excluded.tip, ucret = excluded.ucret, aciklama = excluded.aciklama",
                (fid, oid, tip, ucret, aciklama))
            detay = {"muaf": "muaf", "katilmiyor": "katılmıyor",
                     "indirim": f"indirimli ücret {db.para_yaz(ucret or 0)}"}[tip]
        else:
            abort(400)
        kaydet_gecmis("Katılım ayarı", f"{f['ad']} / {o['sinif']} {o['ad_soyad']}: {detay}"
                                       + (f" ({aciklama})" if aciklama else ""))
        g.con.commit()
        flash("Öğrencinin katılım ayarı kaydedildi.", "tamam")
        return redirect(url_for("ogrenci_detay", fid=fid, oid=oid))

    @app.route("/odeme/<int:pid>/iptal", methods=["POST"])
    @giris_gerekli
    def odeme_iptal(pid):
        p = g.con.execute(
            "SELECT p.*, o.sinif_id, o.ad_soyad, f.ad AS faaliyet FROM odemeler p "
            "JOIN ogrenciler o ON o.id = p.ogrenci_id JOIN faaliyetler f ON f.id = p.faaliyet_id "
            "WHERE p.id = ?", (pid,)).fetchone()
        if p is None:
            abort(404)
        sinif_izni(p["sinif_id"])
        neden = request.form.get("neden", "").strip()
        if not neden:
            flash("İptal nedenini yazın.", "hata")
        elif not p["iptal"]:
            g.con.execute(
                "UPDATE odemeler SET iptal = 1, iptal_eden = ?, iptal_zamani = ?, iptal_neden = ? "
                "WHERE id = ?", (g.kullanici["ad"], db.simdi(), neden, pid))
            kaydet_gecmis("Ödeme iptal edildi",
                          f"{p['faaliyet']} / {p['ad_soyad']}: {db.para_yaz(p['tutar'])} "
                          f"({db.tarih_yaz(p['tarih'])}) - neden: {neden}")
            g.con.commit()
            flash("Ödeme iptal edildi.", "tamam")
        return redirect(url_for("ogrenci_detay", fid=p["faaliyet_id"], oid=p["ogrenci_id"]))

    # ---------------- Raporlar ----------------

    @app.route("/raporlar")
    @giris_gerekli
    def raporlar():
        izin = izinli_siniflar()
        faaliyetler = g.con.execute("SELECT * FROM faaliyetler ORDER BY acik DESC, id DESC").fetchall()
        if izin is not None:
            faaliyetler = [f for f in faaliyetler if g.con.execute(
                f"SELECT 1 FROM faaliyet_siniflar WHERE faaliyet_id = ? AND sinif_id IN "
                f"({','.join('?' * len(izin)) or 'NULL'})", [f["id"], *izin]).fetchone()]
        fid = request.args.get("faaliyet", type=int)
        tur = request.args.get("tur", "sinif_listesi")
        secili = {int(s) for s in request.args.getlist("sinif") if s.isdigit()}
        bas = request.args.get("bas", "")
        bit = request.args.get("bit", "")
        bicim = request.args.get("bicim", "html")

        siniflar = []
        rapor = None
        if fid and any(f["id"] == fid for f in faaliyetler):
            siniflar = sorted(g.con.execute(
                "SELECT s.* FROM siniflar s JOIN faaliyet_siniflar fs ON fs.sinif_id = s.id "
                "WHERE fs.faaliyet_id = ?", (fid,)).fetchall(),
                key=lambda s: db.sinif_sira_anahtari(s["ad"]))
            if izin is not None:
                siniflar = [s for s in siniflar if s["id"] in izin]
            filtre = secili or None
            if izin is not None:
                filtre = (secili & izin) if secili else izin
            try:
                bas_iso = db.tarih_oku(bas) if bas else ""
                bit_iso = db.tarih_oku(bit) if bit else ""
            except ValueError:
                flash("Tarih aralığı geçersiz.", "hata")
                bas_iso = bit_iso = ""
            rapor = rapor_uret(g.con, tur if tur in RAPOR_ADLARI else "sinif_listesi", fid,
                               filtre, bas_iso, bit_iso)
            okul = db.ayar_al(g.con, "okul_adi")
            dosya = _dosya_adi(f"{rapor['faaliyet']} - {rapor['baslik']}")
            if bicim == "xlsx":
                return Response(
                    cikti.excel(rapor, okul),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers=_indirme_basligi(dosya + ".xlsx"))
            if bicim == "pdf":
                return Response(
                    cikti.pdf(rapor, okul, hazirlayan=g.kullanici["ad"],
                              onaylayan=_onaylayan()),
                    mimetype="application/pdf", headers=_indirme_basligi(dosya + ".pdf"))
        elif faaliyetler and not fid:
            return redirect(url_for("raporlar", faaliyet=faaliyetler[0]["id"], tur=tur))
        return render_template("raporlar.html", faaliyetler=faaliyetler, fid=fid, tur=tur,
                               turler=RAPOR_TURLERI, siniflar=siniflar, secili=secili,
                               bas=bas, bit=bit, rapor=rapor)

    def _onaylayan():
        ad = db.ayar_al(g.con, "mudur_adi")
        return f"{ad}\nOkul Müdürü" if ad else "Okul Müdürü"

    # ---------------- Sınıflar ve öğrenciler ----------------

    @app.route("/siniflar", methods=["GET", "POST"])
    @yonetici_gerekli
    def siniflar():
        if request.method == "POST":
            eklenen = 0
            for satir in request.form.get("siniflar", "").splitlines():
                if satir.strip():
                    _, yeni = ice_aktar.sinif_getir_olustur(g.con, satir)
                    eklenen += int(yeni)
            if eklenen:
                kaydet_gecmis("Sınıf eklendi", f"{eklenen} sınıf")
            g.con.commit()
            flash(f"{eklenen} yeni sınıf eklendi.", "tamam")
            return redirect(url_for("siniflar"))
        return render_template("siniflar.html", siniflar=_sinif_listesi())

    @app.route("/sinif/<int:sid>", methods=["GET", "POST"])
    @yonetici_gerekli
    def sinif(sid):
        s = g.con.execute("SELECT * FROM siniflar WHERE id = ?", (sid,)).fetchone()
        if s is None:
            abort(404)
        if request.method == "POST":
            islem = request.form.get("islem")
            if islem == "bilgi":
                ad = ice_aktar.sinif_adi_duzelt(request.form.get("ad", "")) or s["ad"]
                try:
                    g.con.execute("UPDATE siniflar SET ad = ?, kademe = ?, ogretmen = ? WHERE id = ?",
                                  (ad, request.form.get("kademe", "").strip(),
                                   request.form.get("ogretmen", "").strip(), sid))
                except sqlite3.IntegrityError:
                    flash(f"“{ad}” adında başka bir sınıf var.", "hata")
                    return redirect(url_for("sinif", sid=sid))
                flash("Sınıf bilgileri kaydedildi.", "tamam")
            elif islem == "ogrenci_ekle":
                kayitlar = ice_aktar.metin_oku(request.form.get("liste", ""))
                ozet = {"yeni": 0, "guncel": 0}
                for no, ad, tel in kayitlar:
                    ozet[ice_aktar.ogrenci_kaydet(g.con, sid, no, ad, tel)] += 1
                kaydet_gecmis("Öğrenci eklendi", f"{s['ad']}: {ozet['yeni']} yeni, "
                                                 f"{ozet['guncel']} güncellendi")
                flash(f"{ozet['yeni']} öğrenci eklendi, {ozet['guncel']} öğrenci güncellendi.",
                      "tamam")
            elif islem == "ogrenci_duzenle":
                oid = request.form.get("ogrenci_id", type=int)
                g.con.execute(
                    "UPDATE ogrenciler SET okul_no = ?, ad_soyad = ?, veli_tel = ? "
                    "WHERE id = ? AND sinif_id = ?",
                    (request.form.get("okul_no", "").strip(),
                     request.form.get("ad_soyad", "").strip(),
                     request.form.get("veli_tel", "").strip(), oid, sid))
                flash("Öğrenci bilgileri kaydedildi.", "tamam")
            elif islem in ("ogrenci_pasif", "ogrenci_aktif"):
                oid = request.form.get("ogrenci_id", type=int)
                aktif = 1 if islem == "ogrenci_aktif" else 0
                g.con.execute("UPDATE ogrenciler SET aktif = ? WHERE id = ? AND sinif_id = ?",
                              (aktif, oid, sid))
                o = g.con.execute("SELECT ad_soyad FROM ogrenciler WHERE id = ?", (oid,)).fetchone()
                kaydet_gecmis("Öğrenci " + ("aktif yapıldı" if aktif else "listeden çıkarıldı"),
                              f"{s['ad']} {o['ad_soyad'] if o else oid}")
                flash("Öğrenci " + ("tekrar listeye alındı." if aktif else
                                    "listeden çıkarıldı (ödeme kayıtları korunur)."), "tamam")
            elif islem == "ogrenci_tasi":
                oid = request.form.get("ogrenci_id", type=int)
                hedef = request.form.get("hedef_sinif", type=int)
                if hedef and g.con.execute("SELECT 1 FROM siniflar WHERE id = ?", (hedef,)).fetchone():
                    g.con.execute("UPDATE ogrenciler SET sinif_id = ? WHERE id = ? AND sinif_id = ?",
                                  (hedef, oid, sid))
                    flash("Öğrenci başka sınıfa taşındı.", "tamam")
            g.con.commit()
            return redirect(url_for("sinif", sid=sid))
        ogrenciler = sorted(g.con.execute(
            "SELECT * FROM ogrenciler WHERE sinif_id = ?", (sid,)).fetchall(),
            key=lambda o: (-o["aktif"], db.sinif_sira_anahtari(o["okul_no"]), o["ad_soyad"]))
        return render_template("sinif.html", s=s, ogrenciler=ogrenciler,
                               siniflar=_sinif_listesi())

    @app.route("/ice-aktar", methods=["GET", "POST"])
    @yonetici_gerekli
    def ice_aktar_sayfa():
        if request.method == "POST":
            dosya = request.files.get("dosya")
            if not dosya or not dosya.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
                flash("Lütfen bir Excel dosyası (.xls veya .xlsx) seçin.", "hata")
                return redirect(url_for("ice_aktar_sayfa"))
            try:
                kayitlar = ice_aktar.excel_oku(dosya.read())
            except Exception:
                flash("Dosya okunamadı. Excel (.xls veya .xlsx) dosyası olduğundan emin olun.", "hata")
                return redirect(url_for("ice_aktar_sayfa"))
            if not kayitlar:
                flash("Dosyada öğrenci bulunamadı. Başlık satırında “Ad Soyad” (veya “Adı” ve "
                      "“Soyadı”) sütunu olmalı.", "hata")
                return redirect(url_for("ice_aktar_sayfa"))
            ozet = ice_aktar.toplu_kaydet(g.con, kayitlar)
            kaydet_gecmis("Excel'den içe aktarıldı",
                          f"{dosya.filename}: {ozet['sinif']} yeni sınıf, {ozet['yeni']} yeni, "
                          f"{ozet['guncel']} güncellenen öğrenci")
            g.con.commit()
            flash(f"Aktarım tamamlandı: {ozet['sinif']} yeni sınıf, {ozet['yeni']} yeni öğrenci, "
                  f"{ozet['guncel']} güncellenen öğrenci.", "tamam")
            return redirect(url_for("siniflar"))
        return render_template("ice_aktar.html")

    @app.route("/ornek-ogrenci-listesi.xlsx")
    @giris_gerekli
    def ornek_liste():
        return Response(ice_aktar.ornek_excel(),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers=_indirme_basligi("ornek-ogrenci-listesi.xlsx"))

    # ---------------- Kullanıcılar, ayarlar, geçmiş, yedek ----------------

    @app.route("/kullanicilar", methods=["GET", "POST"])
    @yonetici_gerekli
    def kullanicilar():
        if request.method == "POST":
            f = request.form
            rol = "yonetici" if f.get("rol") == "yonetici" else "ogretmen"
            sid = f.get("sinif_id", type=int) if rol == "ogretmen" else None
            if not f.get("kullanici_adi", "").strip() or not f.get("ad", "").strip():
                flash("Ad ve kullanıcı adı zorunlu.", "hata")
            elif len(f.get("sifre", "")) < 6:
                flash("Şifre en az 6 karakter olmalı.", "hata")
            elif rol == "ogretmen" and not sid:
                flash("Öğretmen için sınıf seçin.", "hata")
            else:
                try:
                    g.con.execute(
                        "INSERT INTO kullanicilar (kullanici_adi, ad, sifre_hash, rol, sinif_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (f["kullanici_adi"].strip(), f["ad"].strip(),
                         generate_password_hash(f["sifre"]), rol, sid))
                    kaydet_gecmis("Kullanıcı eklendi", f"{f['ad']} ({f['kullanici_adi']}, {rol})")
                    g.con.commit()
                    flash("Kullanıcı eklendi.", "tamam")
                except sqlite3.IntegrityError:
                    flash("Bu kullanıcı adı zaten var.", "hata")
            return redirect(url_for("kullanicilar"))
        liste = g.con.execute(
            "SELECT k.*, s.ad AS sinif FROM kullanicilar k LEFT JOIN siniflar s ON s.id = k.sinif_id "
            "ORDER BY k.rol DESC, k.ad").fetchall()
        return render_template("kullanicilar.html", liste=liste, siniflar=_sinif_listesi())

    @app.route("/kullanici/<int:kid>/sil", methods=["POST"])
    @yonetici_gerekli
    def kullanici_sil(kid):
        if kid == g.kullanici["id"]:
            flash("Kendi hesabınızı silemezsiniz.", "hata")
        else:
            k = g.con.execute("SELECT * FROM kullanicilar WHERE id = ?", (kid,)).fetchone()
            if k:
                g.con.execute("DELETE FROM kullanicilar WHERE id = ?", (kid,))
                kaydet_gecmis("Kullanıcı silindi", f"{k['ad']} ({k['kullanici_adi']})")
                g.con.commit()
                flash("Kullanıcı silindi.", "tamam")
        return redirect(url_for("kullanicilar"))

    @app.route("/kullanici/<int:kid>/sifre", methods=["POST"])
    @yonetici_gerekli
    def kullanici_sifre(kid):
        yeni = request.form.get("sifre", "")
        if len(yeni) < 6:
            flash("Şifre en az 6 karakter olmalı.", "hata")
        else:
            g.con.execute("UPDATE kullanicilar SET sifre_hash = ? WHERE id = ?",
                          (generate_password_hash(yeni), kid))
            g.con.commit()
            flash("Şifre değiştirildi.", "tamam")
        return redirect(url_for("kullanicilar"))

    @app.route("/ayarlar", methods=["GET", "POST"])
    @yonetici_gerekli
    def ayarlar():
        if request.method == "POST":
            db.ayar_yaz(g.con, "okul_adi", request.form.get("okul_adi", "").strip() or "Okul")
            db.ayar_yaz(g.con, "mudur_adi", request.form.get("mudur_adi", "").strip())
            g.con.commit()
            flash("Ayarlar kaydedildi.", "tamam")
            return redirect(url_for("ayarlar"))
        return render_template("ayarlar.html", mudur_adi=db.ayar_al(g.con, "mudur_adi"))

    @app.route("/gecmis")
    @yonetici_gerekli
    def gecmis():
        kayitlar = g.con.execute(
            "SELECT * FROM islem_gecmisi ORDER BY id DESC LIMIT 500").fetchall()
        return render_template("gecmis.html", kayitlar=kayitlar)

    @app.route("/yedek")
    @yonetici_gerekli
    def yedek():
        with tempfile.TemporaryDirectory() as d:
            hedef_yol = os.path.join(d, "yedek.db")
            hedef = sqlite3.connect(hedef_yol)
            g.con.backup(hedef)
            hedef.close()
            with open(hedef_yol, "rb") as f:
                veri = f.read()
        kaydet_gecmis("Yedek indirildi")
        g.con.commit()
        ad = f"faaliyet-yedek-{datetime.now():%Y-%m-%d-%H%M}.db"
        return Response(veri, mimetype="application/octet-stream", headers=_indirme_basligi(ad))

    @app.errorhandler(403)
    def yasak(_e):
        return render_template("hata.html", mesaj="Bu sayfayı görme yetkiniz yok."), 403

    @app.errorhandler(404)
    def bulunamadi(_e):
        return render_template("hata.html", mesaj="Aradığınız kayıt bulunamadı."), 404

    @app.errorhandler(400)
    def kotu_istek(e):
        return render_template("hata.html", mesaj=getattr(e, "description", "Geçersiz istek.")), 400


def _dosya_adi(metin):
    tablo = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = metin.translate(tablo)
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in s).strip().replace(" ", "_")


def _indirme_basligi(ad):
    return {"Content-Disposition": f'attachment; filename="{ad}"'}


if __name__ == "__main__":
    import socket
    uygulama = uygulama_olustur()
    port = int(os.environ.get("PORT", "5000"))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = "bu-bilgisayarin-ip-adresi"
    print("\n  Okul Faaliyet Takip Programı çalışıyor.")
    print(f"  Bu bilgisayardan:        http://localhost:{port}")
    print(f"  Okul ağındaki diğerleri: http://{ip}:{port}")
    print("  Kapatmak için bu pencereyi kapatın.\n")
    import threading
    import webbrowser
    from waitress import serve
    if os.environ.get("TARAYICI_ACMA") != "1":
        threading.Timer(1.5, webbrowser.open, [f"http://localhost:{port}"]).start()
    serve(uygulama, host="0.0.0.0", port=port, threads=8)
