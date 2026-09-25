"""Bildirim ayarlari: eposta listesi (mevcut davranis) + esik sayilari
(YENI - kullanici artik Ayarlar'dan degistirebilir). Gercek Redis/e-posta
gondermez; ayarlar_yukle/kaydet ve e-posta gonderimi dogrudan monkeypatch
edilir."""

from __future__ import annotations

from datetime import datetime

import pytest

import app as uygulama
import email_client
import redis_store


# ------------------------------------------------------------------ _esikleri_dogrula
def test_esikleri_dogrula_gecerli_degerleri_korur():
    assert uygulama._esikleri_dogrula({"cok_onemli": 5, "onemli": 20, "bakmaya_deger": 40}) == {
        "cok_onemli": 5, "onemli": 20, "bakmaya_deger": 40,
    }


def test_esikleri_dogrula_eksik_alan_varsayilana_duser():
    sonuc = uygulama._esikleri_dogrula({"cok_onemli": 3})
    assert sonuc["cok_onemli"] == 3
    assert sonuc["onemli"] == uygulama.ESIKLER["onemli"]
    assert sonuc["bakmaya_deger"] == uygulama.ESIKLER["bakmaya_deger"]


def test_esikleri_dogrula_gecersiz_deger_varsayilana_duser():
    sonuc = uygulama._esikleri_dogrula({"cok_onemli": "abc", "onemli": None, "bakmaya_deger": [1, 2]})
    assert sonuc == uygulama.ESIKLER


def test_esikleri_dogrula_sifir_ve_negatif_1e_sabitlenir():
    sonuc = uygulama._esikleri_dogrula({"cok_onemli": 0, "onemli": -5, "bakmaya_deger": 50})
    assert sonuc["cok_onemli"] == 1 and sonuc["onemli"] == 1


def test_esikleri_dogrula_asiri_buyuk_deger_ust_sinira_sabitlenir():
    sonuc = uygulama._esikleri_dogrula({"cok_onemli": 999999, "onemli": 25, "bakmaya_deger": 50})
    assert sonuc["cok_onemli"] == uygulama.ESIK_AZAMI


def test_esikleri_dogrula_sozluk_degilse_tum_varsayilanlar():
    assert uygulama._esikleri_dogrula(None) == uygulama.ESIKLER
    assert uygulama._esikleri_dogrula("bozuk") == uygulama.ESIKLER


# ==================================================================== /api/ayarlar
class SahteAyarDeposu:
    def __init__(self):
        self.ayarlar: dict = {}

    def _yukle(self):
        return {**redis_store.VARSAYILAN_AYARLAR, **self.ayarlar}

    def _kaydet(self, yeni):
        self.ayarlar = dict(yeni)

    def kur(self, monkeypatch):
        monkeypatch.setattr(redis_store, "ayarlar_yukle", self._yukle)
        monkeypatch.setattr(redis_store, "ayarlar_kaydet", self._kaydet)
        return self


@pytest.fixture
def ayar_deposu(monkeypatch):
    return SahteAyarDeposu().kur(monkeypatch)


def test_ayarlar_get_varsayilan_esikleri_doner(istemci, ayar_deposu):
    r = istemci.get("/api/ayarlar")
    j = r.json()
    assert r.status_code == 200 and j["ok"]
    assert j["ayarlar"]["esikler"] == uygulama.ESIKLER


def test_ayarlar_post_ozel_esikleri_kaydeder(istemci, ayar_deposu):
    r = istemci.post("/api/ayarlar", json={
        "alicilar": [{"eposta": "a@b.com"}],
        "esikler": {"cok_onemli": 3, "onemli": 15, "bakmaya_deger": 30},
    })
    j = r.json()
    assert r.status_code == 200 and j["ok"]
    assert j["ayarlar"]["esikler"] == {"cok_onemli": 3, "onemli": 15, "bakmaya_deger": 30}

    # kalici mi? tekrar okuyunca ayni degerler gelmeli
    r2 = istemci.get("/api/ayarlar")
    assert r2.json()["ayarlar"]["esikler"] == {"cok_onemli": 3, "onemli": 15, "bakmaya_deger": 30}


def test_ayarlar_post_gecersiz_esik_varsayilana_duser(istemci, ayar_deposu):
    r = istemci.post("/api/ayarlar", json={"alicilar": [], "esikler": {"cok_onemli": "yanlis"}})
    assert r.json()["ayarlar"]["esikler"]["cok_onemli"] == uygulama.ESIKLER["cok_onemli"]


def test_ayarlar_post_esikler_gonderilmezse_varsayilan_kullanilir(istemci, ayar_deposu):
    r = istemci.post("/api/ayarlar", json={"alicilar": [{"eposta": "a@b.com"}]})
    assert r.json()["ayarlar"]["esikler"] == uygulama.ESIKLER


# ==================================================================== esik bildirimi ozel esigi kullanir mi
def _haber(url, sinif):
    return {"url": url, "sinif": sinif, "baslikTr": f"Başlık {url}", "baslik": url, "kategori": "hisse", "ulke": "US"}


@pytest.fixture
def gunduz(monkeypatch):
    monkeypatch.setattr(uygulama, "_istanbul_saati", lambda: datetime(2026, 9, 25, 12, 0, tzinfo=uygulama.ISTANBUL_TZ))


def test_esik_takibi_ozel_dusuk_esikte_daha_erken_tetiklenir(monkeypatch, ayar_deposu, gunduz):
    ayar_deposu.ayarlar["alicilar"] = [{"eposta": "a@b.com"}]
    ayar_deposu.ayarlar["esikler"] = {"cok_onemli": 2, "onemli": 25, "bakmaya_deger": 50}

    monkeypatch.setattr(redis_store, "sayaclari_yukle", lambda: {})
    monkeypatch.setattr(redis_store, "sayaclari_kaydet", lambda s: None)
    monkeypatch.setattr(redis_store, "son_gonderim_yukle", lambda: {})
    monkeypatch.setattr(redis_store, "son_gonderim_kaydet", lambda s: None)
    monkeypatch.setattr(email_client, "yapilandirilmis_mi", lambda: True)

    gonderilen = {}
    def sahte_gonder(aliciler, konu, html):
        gonderilen["aliciler"] = aliciler
    monkeypatch.setattr(email_client, "eposta_gonder", sahte_gonder)

    # varsayilan esik (10) asilmazdi ama ozel esik (2) 2 haberle asilir
    yeni_ogeler = [_haber("u1", "cok_onemli"), _haber("u2", "cok_onemli")]
    hatalar = uygulama._esik_takibi_ve_bildirim(yeni_ogeler, {})
    assert hatalar == []
    assert gonderilen.get("aliciler") == ["a@b.com"]


def test_esik_takibi_esik_asilmazsa_mail_gitmez(monkeypatch, ayar_deposu, gunduz):
    ayar_deposu.ayarlar["alicilar"] = [{"eposta": "a@b.com"}]
    ayar_deposu.ayarlar["esikler"] = {"cok_onemli": 10, "onemli": 25, "bakmaya_deger": 50}

    monkeypatch.setattr(redis_store, "sayaclari_yukle", lambda: {})
    monkeypatch.setattr(redis_store, "sayaclari_kaydet", lambda s: None)
    monkeypatch.setattr(redis_store, "son_gonderim_yukle", lambda: {})
    monkeypatch.setattr(redis_store, "son_gonderim_kaydet", lambda s: None)
    monkeypatch.setattr(email_client, "yapilandirilmis_mi", lambda: True)

    gonderilen = {}
    monkeypatch.setattr(email_client, "eposta_gonder", lambda *a: gonderilen.setdefault("gitti", True))

    uygulama._esik_takibi_ve_bildirim([_haber("u1", "cok_onemli")], {})
    assert "gitti" not in gonderilen
