"""Gunun ozeti: zamanlama (UTC/Istanbul), tarih anahtarli kayit, durumlar, kilit,
kontrollu yenileme, cron yetkisi ve "Coded by" metninin kaldirildigi testleri."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

import gun_ozeti_servisi as g
import redis_store

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class SahteRedis:
    """Bellekte TTL destekli minik Redis: GET/SET(EX,NX)/DEL/TTL."""

    def __init__(self):
        self.veri: dict[str, tuple[str, float | None]] = {}
        self.saat = [1_000_000.0]

    def _canli(self, k):
        v = self.veri.get(k)
        if v is None:
            return None
        if v[1] is not None and v[1] <= self.saat[0]:
            del self.veri[k]
            return None
        return v

    def komut(self, komutlar):
        cikti = []
        for c in komutlar:
            ad, k = c[0], c[1]
            if ad == "GET":
                v = self._canli(k)
                cikti.append({"result": v[0] if v else None})
            elif ad == "TTL":
                v = self._canli(k)
                cikti.append({"result": -2 if v is None else (-1 if v[1] is None else int(v[1] - self.saat[0]))})
            elif ad == "DEL":
                cikti.append({"result": 1 if self.veri.pop(k, None) else 0})
            else:
                raise AssertionError(ad)
        return cikti

    def yaz(self, k, deger, ttl):
        self.veri[k] = (deger, self.saat[0] + ttl)

    def kilit_al(self, k, ttl):
        if self._canli(k):
            return False
        self.veri[k] = ("1", self.saat[0] + ttl)
        return True

    def kilit_birak(self, k):
        self.veri.pop(k, None)

    def kur(self, monkeypatch):
        monkeypatch.setattr(redis_store, "komut_calistir", self.komut)
        monkeypatch.setattr(redis_store, "onbellek_yaz", self.yaz)
        monkeypatch.setattr(redis_store, "onbellek_oku", lambda k: (self._canli(k) or (None,))[0])
        monkeypatch.setattr(redis_store, "kilit_al", self.kilit_al)
        monkeypatch.setattr(redis_store, "kilit_birak", self.kilit_birak)
        return self


@pytest.fixture
def redis(monkeypatch):
    return SahteRedis().kur(monkeypatch)


def ozet(n=12):
    return {"kategoriler": [{"kategori": "ana", "ozet": "Piyasalar karisik."}], "genelOzet": "Gun dengeli gecti.", "haberSayisi": n}


# ------------------------------------------------------------------ zamanlama
def test_tarih_istanbul_takvimine_gore():
    assert g.bugun(utc(2026, 9, 19, 20, 59)) == "2026-09-19"  # 23:59 TRT
    assert g.bugun(utc(2026, 9, 19, 21, 0)) == "2026-09-20"  # 00:00 TRT


def test_planli_saatler_istanbul_10_14_18():
    assert g.GUN_OZETI_SAATLERI == (10, 14, 18)
    assert g.son_gecen_slot(utc(2026, 9, 19, 6, 59)) is None  # 09:59 TRT
    assert g.son_gecen_slot(utc(2026, 9, 19, 7, 0)).hour == 10
    assert g.son_gecen_slot(utc(2026, 9, 19, 10, 59)).hour == 10
    assert g.son_gecen_slot(utc(2026, 9, 19, 11, 0)).hour == 14
    assert g.son_gecen_slot(utc(2026, 9, 19, 20, 0)).hour == 18
    assert g.sonraki_slot_metni(utc(2026, 9, 19, 8, 0)) == "14:00"
    assert g.sonraki_slot_metni(utc(2026, 9, 19, 16, 0)) == "yarın 10:00"


def test_vercel_cron_ifadeleri_utc_ve_istanbul_saatlerine_denk():
    from zoneinfo import ZoneInfo

    cfg = json.load(open(os.path.join(KOK, "vercel.json"), encoding="utf-8"))
    crons = [c for c in cfg["crons"] if c["path"] == "/api/cron/gun-ozeti"]
    assert len(crons) == 3
    saatler_utc = sorted(int(c["schedule"].split()[1]) for c in crons)
    assert all(c["schedule"].split()[0] == "0" and c["schedule"].split()[2:] == ["*", "*", "*"] for c in crons)  # her gun, tam saat
    assert saatler_utc == [7, 11, 15]
    for h_utc, h_tr in zip(saatler_utc, g.GUN_OZETI_SAATLERI):
        assert datetime(2026, 1, 15, h_utc, tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Istanbul")).hour == h_tr
        assert datetime(2026, 7, 15, h_utc, tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Istanbul")).hour == h_tr  # DST yok
    assert cfg["functions"]["app.py"]["maxDuration"] == 60


# ------------------------------------------------------------------ kayit / durumlar
def test_bos_durum_ve_slot_oncesi_bayat_sayilmaz(redis):
    y = g.oku(utc(2026, 9, 19, 6, 0))  # 09:00 TRT
    assert y["durum"] == "missing" and y["guncelMi"] is True and y["yenilemeGerekli"] is False
    assert y["planliSaatler"] == ["10:00", "14:00", "18:00"] and y["sonrakiGuncelleme"] == "10:00"
    y = g.oku(utc(2026, 9, 19, 7, 5))  # 10:05 TRT, ozet yok
    assert y["durum"] == "missing" and y["guncelMi"] is False and y["yenilemeGerekli"] is True


def test_basarili_uretim_tarih_anahtarli_kayit_ve_durum(redis):
    t = utc(2026, 9, 19, 7, 5)
    s = g.uret(lambda: ozet(12), kaynak="cron", simdi=t)
    assert s["sonuc"] == "ready"
    kayit = json.loads(redis.veri["htp:gun_ozeti:v2:2026-09-19"][0])
    assert kayit["durum"] == "ready" and kayit["tarih"] == "2026-09-19" and kayit["haberSayisi"] == 12
    assert kayit["olusturulmaZamani"] == "2026-09-19T07:05:00+00:00" and kayit["kaynak"] == "cron" and kayit["hataKodu"] is None
    assert kayit["genelOzet"] == "Gun dengeli gecti."
    assert "htp:gun_ozeti:v2:son" in redis.veri
    assert redis.veri["htp:gun_ozeti:v2:2026-09-19"][1] - redis.saat[0] == 14 * 24 * 3600  # 14 gun TTL
    y = g.oku(t + timedelta(minutes=1))
    assert y["durum"] == "ready" and y["guncelMi"] is True and y["gecmisGun"] is False and y["yenilemeGerekli"] is False


def test_planli_saat_gecince_ozet_bayatlar(redis):
    g.uret(lambda: ozet(), kaynak="cron", simdi=utc(2026, 9, 19, 7, 5))
    y = g.oku(utc(2026, 9, 19, 11, 30))  # 14:30 TRT
    assert y["durum"] == "ready" and y["guncelMi"] is False and y["yenilemeGerekli"] is True
    assert y["genelOzet"]  # bayat olsa da icerik gosterilir


def test_uretim_sirasinda_durum_generating_ve_eski_icerik_korunur(redis):
    g.uret(lambda: ozet(5), kaynak="cron", simdi=utc(2026, 9, 19, 7, 5))
    gorulen = {}

    def uretici():
        gorulen["y"] = g.oku(utc(2026, 9, 19, 11, 6))
        return ozet(9)

    g.uret(uretici, kaynak="cron", simdi=utc(2026, 9, 19, 11, 5))
    assert gorulen["y"]["durum"] == "generating" and gorulen["y"]["genelOzet"] == "Gun dengeli gecti."
    assert g.oku(utc(2026, 9, 19, 11, 7))["haberSayisi"] == 9


def test_basarisiz_uretim_kod_saklar_metin_saklamaz_eski_icerik_korunur(redis):
    g.uret(lambda: ozet(5), kaynak="cron", simdi=utc(2026, 9, 19, 7, 5))

    def bozuk():
        raise RuntimeError("Gemini geçici olarak yoğun/erişilemez (503): apikey=SECRET123 ic ayrinti")

    s = g.uret(bozuk, kaynak="cron", simdi=utc(2026, 9, 19, 11, 5))
    assert s["sonuc"] == "failed" and s["hataKodu"] == "gemini_yogun"
    ham = redis.veri["htp:gun_ozeti:v2:2026-09-19"][0]
    assert "SECRET123" not in ham and "ic ayrinti" not in ham
    y = g.oku(utc(2026, 9, 19, 11, 6))
    assert y["durum"] == "failed" and y["hataKodu"] == "gemini_yogun" and y["sonHataZamani"] and y["genelOzet"]
    assert y["yenilemeGerekli"] is True  # icerik bayat + hata sonrasi tekrar denenebilir


def test_hata_kodu_esleme():
    assert g.hata_kodu(g.OzetHatasi("haber_yok")) == "haber_yok"
    assert g.hata_kodu(RuntimeError("Gemini ücretsiz günlük kota sınırı aşıldı")) == "gemini_kota"
    assert g.hata_kodu(RuntimeError("Gemini istek limitine ulaşıldı (429)")) == "gemini_limit"
    assert g.hata_kodu(RuntimeError("Read timed out. (read timeout=25)")) == "gemini_zaman_asimi"
    assert g.hata_kodu(RuntimeError("HTTP 400: kotu istek")) == "bilinmeyen"


def test_haber_yoksa_failed_haber_yok(redis):
    def yok():
        raise g.OzetHatasi("haber_yok")

    assert g.uret(yok, kaynak="cron", simdi=utc(2026, 9, 19, 7, 5))["hataKodu"] == "haber_yok"
    assert g.oku(utc(2026, 9, 19, 7, 6))["durum"] == "failed"


def test_bugun_yokken_dunku_son_basarili_ozet_gosterilir(redis):
    g.uret(lambda: ozet(7), kaynak="cron", simdi=utc(2026, 9, 18, 15, 5))
    y = g.oku(utc(2026, 9, 19, 6, 0))  # ertesi gun 09:00 TRT
    assert y["durum"] == "missing" and y["gecmisGun"] is True and y["gosterilenTarih"] == "2026-09-18" and y["genelOzet"]
    assert y["guncelMi"] is True  # ilk slottan once bugun icin ozet beklenmez


# ------------------------------------------------------------------ kilit / kopya teslim / takili uretim
def test_es_zamanli_ikinci_uretim_calismaz(redis):
    t = utc(2026, 9, 19, 7, 5)
    cagri = []
    ikinci = {}

    def uretici():
        cagri.append(1)
        ikinci["s"] = g.uret(lambda: cagri.append(2) or ozet(), kaynak="kullanici", simdi=t)
        return ozet()

    g.uret(uretici, kaynak="cron", simdi=t)
    assert cagri == [1] and ikinci["s"]["sonuc"] == "generating"
    assert not any(k.startswith("htp:gun_ozeti:v2:kilit") for k in redis.veri)  # kilit birakildi


def test_kilit_hata_olsa_da_birakilir(redis):
    t = utc(2026, 9, 19, 7, 5)
    g.uret(lambda: (_ for _ in ()).throw(RuntimeError("x")), kaynak="cron", simdi=t)
    assert not any(k.startswith("htp:gun_ozeti:v2:kilit") for k in redis.veri)


def test_gercek_es_zamanli_thread_tek_uretim(redis):
    t = utc(2026, 9, 19, 7, 5)
    sayac = []
    baslasin = threading.Event()

    def yavas():
        sayac.append(1)
        baslasin.wait(0.3)
        return ozet()

    sonuclar = []
    kanallar = [threading.Thread(target=lambda: sonuclar.append(g.uret(yavas, kaynak="cron", simdi=t)["sonuc"])) for _ in range(5)]
    [k.start() for k in kanallar]
    [k.join() for k in kanallar]
    assert sayac == [1] and sorted(sonuclar).count("ready") == 1


def test_cron_kopya_teslimi_60_dk_icinde_atlanir(redis):
    cagri = []
    g.uret(lambda: cagri.append(1) or ozet(), kaynak="cron", simdi=utc(2026, 9, 19, 7, 5), min_aralik_sn=g.CRON_MIN_ARALIK_SN)
    s = g.uret(lambda: cagri.append(2) or ozet(), kaynak="cron", simdi=utc(2026, 9, 19, 7, 40), min_aralik_sn=g.CRON_MIN_ARALIK_SN)
    assert s["sonuc"] == "atlandi" and cagri == [1]
    s = g.uret(lambda: cagri.append(3) or ozet(), kaynak="cron", simdi=utc(2026, 9, 19, 11, 2), min_aralik_sn=g.CRON_MIN_ARALIK_SN)
    assert s["sonuc"] == "ready" and cagri == [1, 3]


def test_takili_generating_hata_sayilir_ve_yeniden_uretilebilir(redis):
    t = utc(2026, 9, 19, 7, 5)
    redis.yaz("htp:gun_ozeti:v2:2026-09-19", json.dumps({"tarih": "2026-09-19", "durum": "generating", "uretimBaslangic": t.isoformat()}), 999)
    y = g.oku(t + timedelta(minutes=5))
    assert y["durum"] == "failed" and y["hataKodu"] == "zaman_asimi"
    assert g.uret(lambda: ozet(), kaynak="cron", simdi=t + timedelta(minutes=5))["sonuc"] == "ready"


# ------------------------------------------------------------------ kontrollu kullanici yenilemesi
def test_kullanici_yenilemesi_kurallari(redis):
    cagri = []
    uretici = lambda: cagri.append(1) or ozet()  # noqa: E731
    # slot oncesi: gerek yok
    assert g.kullanici_yenilemesi(uretici, uretilebilir=True, simdi=utc(2026, 9, 19, 6, 0))["sonuc"] == "guncel"
    # anahtar yok
    assert g.kullanici_yenilemesi(uretici, uretilebilir=False, simdi=utc(2026, 9, 19, 7, 5))["sonuc"] == "uretilemez"
    assert cagri == []
    # bayat -> uretir
    s = g.kullanici_yenilemesi(uretici, uretilebilir=True, simdi=utc(2026, 9, 19, 7, 5))
    assert s["sonuc"] == "ready" and cagri == [1] and s["durum"]["guncelMi"] is True
    # hemen tekrar: guncel
    assert g.kullanici_yenilemesi(uretici, uretilebilir=True, simdi=utc(2026, 9, 19, 7, 6))["sonuc"] == "guncel" and cagri == [1]
    # 14:00 slotu gecti -> bayat ama 15 dk bekleme suruyor mu? (bekleme anahtari 15 dk once yazildi, sahte saat ilerletilmedi)
    s = g.kullanici_yenilemesi(uretici, uretilebilir=True, simdi=utc(2026, 9, 19, 11, 5))
    assert s["sonuc"] == "beklemede" and cagri == [1] and s["durum"]["sonrakiDenemeSn"]
    redis.saat[0] += g.YENILEME_BEKLEME_SN + 1
    s = g.kullanici_yenilemesi(uretici, uretilebilir=True, simdi=utc(2026, 9, 19, 11, 20))
    assert s["sonuc"] == "ready" and cagri == [1, 1]


def test_kullanici_yenilemesi_basarisizsa_bekleme_uygular(redis):
    cagri = []

    def bozuk():
        cagri.append(1)
        raise RuntimeError("503 yoğun")

    t = utc(2026, 9, 19, 7, 5)
    assert g.kullanici_yenilemesi(bozuk, uretilebilir=True, simdi=t)["sonuc"] == "failed"
    assert g.kullanici_yenilemesi(bozuk, uretilebilir=True, simdi=t + timedelta(minutes=1))["sonuc"] == "beklemede"
    assert cagri == [1]  # Gemini tekrar tekrar dovulmez


def test_uretim_devam_ederken_kullanici_yenilemesi_generating_doner(redis):
    t = utc(2026, 9, 19, 7, 5)
    ic = {}

    def uretici():
        ic["s"] = g.kullanici_yenilemesi(lambda: 1 / 0, uretilebilir=True, simdi=t)
        return ozet()

    g.uret(uretici, kaynak="cron", simdi=t)
    assert ic["s"]["sonuc"] == "generating"


# ------------------------------------------------------------------ HTTP uclari
@pytest.fixture
def istemci(redis, monkeypatch):
    from fastapi.testclient import TestClient

    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(g, "_simdi", lambda: utc(2026, 9, 19, 7, 5))
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("POLL_SECRET", raising=False)
    return TestClient(uygulama.app), uygulama


def test_get_ve_post_gun_ozeti_ayni_sozlesme(istemci):
    c, _ = istemci
    for cagri in (c.get, c.post):
        r = cagri("/api/gun-ozeti")
        j = r.json()
        assert r.status_code == 200 and j["ok"] and j["durum"] == "missing" and j["yenilemeGerekli"] is True
        assert j["planliSaatler"] == ["10:00", "14:00", "18:00"] and r.headers["cache-control"] == "no-store"


def test_gun_ozeti_uc_noktasi_redis_hatasinda_genel_mesaj(istemci, monkeypatch):
    c, _ = istemci

    def patlar(*a, **k):
        raise RuntimeError("upstash tokeni SECRET-XYZ")

    monkeypatch.setattr(redis_store, "komut_calistir", patlar)
    r = c.get("/api/gun-ozeti")
    assert r.status_code == 503 and "SECRET-XYZ" not in r.text and r.json()["kod"] == "depo_hatasi"


def test_yenile_uc_noktasi_bir_kez_uretir(istemci, monkeypatch):
    c, uygulama = istemci
    cagri = []
    monkeypatch.setattr(uygulama, "_gun_ozeti_uretici", lambda depo=None: cagri.append(1) or ozet(3))
    r = c.post("/api/gun-ozeti/yenile").json()
    assert r["sonuc"] == "ready" and r["durum"] == "ready" and r["haberSayisi"] == 3 and cagri == [1]
    assert c.post("/api/gun-ozeti/yenile").json()["sonuc"] == "guncel" and cagri == [1]


def test_yenile_gemini_anahtari_yoksa_uretmez(istemci, monkeypatch):
    c, _ = istemci
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert c.post("/api/gun-ozeti/yenile").json()["sonuc"] == "uretilemez"


def test_cron_yetkisi(istemci, monkeypatch):
    c, uygulama = istemci
    cagri = []
    monkeypatch.setattr(uygulama, "_gun_ozeti_uretici", lambda depo=None: cagri.append(1) or ozet())

    # Hicbir secret tanimli degil: hicbir istek yetkili degil.
    assert c.get("/api/cron/gun-ozeti", headers={"Authorization": "Bearer x"}).status_code == 401
    assert c.get("/api/cron/gun-ozeti", headers={"X-Poll-Secret": ""}).status_code == 401

    monkeypatch.setenv("CRON_SECRET", "cron-gizli-degeri-123456")
    assert c.get("/api/cron/gun-ozeti").status_code == 401
    assert c.get("/api/cron/gun-ozeti", headers={"Authorization": "Bearer yanlis"}).status_code == 401
    assert c.get("/api/cron/gun-ozeti", headers={"Authorization": "cron-gizli-degeri-123456"}).status_code == 401  # Bearer yok
    assert cagri == []

    r = c.get("/api/cron/gun-ozeti", headers={"Authorization": "Bearer cron-gizli-degeri-123456"})
    assert r.status_code == 200 and r.json()["sonuc"] == "ready" and cagri == [1]
    assert r.headers["cache-control"] == "no-store"
    # Vercel ayni calismayi iki kez teslim ederse ikinci istek atlanir.
    r2 = c.get("/api/cron/gun-ozeti", headers={"Authorization": "Bearer cron-gizli-degeri-123456"})
    assert r2.json()["sonuc"] == "atlandi" and cagri == [1]

    # POST yontemi cron ucunda yok (Vercel GET kullanir)
    assert c.post("/api/cron/gun-ozeti", headers={"Authorization": "Bearer cron-gizli-degeri-123456"}).status_code in (404, 405)


def test_harici_cron_x_poll_secret_ile_ve_eski_uc(istemci, monkeypatch):
    c, uygulama = istemci
    monkeypatch.setattr(uygulama, "_gun_ozeti_uretici", lambda depo=None: ozet())
    monkeypatch.setenv("POLL_SECRET", "poll-gizli-degeri-123456")
    assert c.post("/api/gun-ozeti-olustur").status_code == 401
    assert c.post("/api/gun-ozeti-olustur", headers={"X-Poll-Secret": "yanlis"}).status_code == 401
    r = c.post("/api/gun-ozeti-olustur", headers={"X-Poll-Secret": "poll-gizli-degeri-123456"})
    assert r.status_code == 200 and r.json()["sonuc"] == "ready"
    assert c.get("/api/cron/gun-ozeti", headers={"X-Poll-Secret": "poll-gizli-degeri-123456"}).status_code == 200


def test_cron_basarisiz_uretim_502_ve_ayrinti_sizmaz(istemci, monkeypatch):
    c, uygulama = istemci
    monkeypatch.setenv("CRON_SECRET", "cron-gizli-degeri-123456")

    def bozuk(depo=None):
        raise RuntimeError("HTTP 500: apikey=SUPER-GIZLI ic ayrinti")

    monkeypatch.setattr(uygulama, "_gun_ozeti_uretici", bozuk)
    r = c.get("/api/cron/gun-ozeti", headers={"Authorization": "Bearer cron-gizli-degeri-123456"})
    assert r.status_code == 502 and r.json()["hataKodu"] == "gemini_yogun" and "SUPER-GIZLI" not in r.text


def test_gun_sonu_ozeti_bugunku_hazir_kaydi_kullanir_yoksa_bir_kez_uretir(istemci, monkeypatch):
    c, uygulama = istemci
    cagri = []
    monkeypatch.setattr(uygulama, "_gun_ozeti_uretici", lambda depo=None: cagri.append(1) or ozet(4))
    ilk = uygulama._gun_sonu_ozetini_hazirla({})
    assert ilk["genelOzet"] and cagri == [1]
    ikinci = uygulama._gun_sonu_ozetini_hazirla({})  # bugunun hazir ozeti var: Gemini yok
    assert ikinci["genelOzet"] and cagri == [1]


def test_gun_ozeti_uretici_hata_kodlari(monkeypatch):
    import app as uygulama

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(g.OzetHatasi) as e:
        uygulama._gun_ozeti_uretici({})
    assert e.value.kod == "anahtar_yok"
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    with pytest.raises(g.OzetHatasi) as e:
        uygulama._gun_ozeti_uretici({})  # bugun icin haber yok
    assert e.value.kod == "haber_yok"


# ------------------------------------------------------------------ "Coded by" kaldirildi
def test_coded_by_metni_hicbir_arayuz_dosyasinda_yok():
    yasak = re.compile(r"coded\s*by|credit-watermark|Alper Safa", re.I)
    for koklu, dizinler, dosyalar in os.walk(os.path.join(KOK, "static_ui")):
        dizinler[:] = [d for d in dizinler if d != "vendor"]
        for d in dosyalar:
            if d.endswith((".html", ".css", ".js")):
                icerik = open(os.path.join(koklu, d), encoding="utf-8").read()
                assert not yasak.search(icerik), d
