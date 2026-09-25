"""AI sohbet: baglam olusturma (sohbet_baglami) ve /api/sohbet uc noktasi.
Finviz/Gemini'ye GERCEK istek atmaz; alt seviye onbellek okuma fonksiyonlari
dogrudan monkeypatch edilir (test_tarama.py'deki desenle ayni)."""

from __future__ import annotations

import pytest

import gun_ozeti_servisi
import redis_store
import sohbet_baglami
import tarama_servisi as ts


# ------------------------------------------------------------------ _guncel_haberler
def _haber(url, sinif="onemli", ilk_gorulme="2026-09-24T10:00:00", **kw):
    taban = {
        "url": url, "sinif": sinif, "baslikTr": f"Başlık {url}", "baslik": f"Title {url}",
        "ai_ozet": "Kısa özet metni.", "saat": "10:00", "kaynak": "Finviz", "ilkGorulme": ilk_gorulme,
        "tarih": "2026-09-24", "kategori": "hisse", "ulke": "US",
    }
    taban.update(kw)
    return taban


def test_guncel_haberler_onem_ve_zamana_gore_siralanir(monkeypatch):
    depo = {
        "a": _haber("a", sinif="onemsiz", ilk_gorulme="2026-09-24T12:00:00"),
        "b": _haber("b", sinif="cok_onemli", ilk_gorulme="2026-09-24T09:00:00"),
        "c": _haber("c", sinif="cok_onemli", ilk_gorulme="2026-09-24T11:00:00"),
    }
    monkeypatch.setattr(redis_store, "depo_yukle", lambda: depo)
    sonuc = sohbet_baglami._guncel_haberler()
    # once onem (cok_onemli > onemsiz), esitlikte en yeni once (c, sonra b), en son onemsiz (a)
    assert [h["url"] for h in sonuc] == ["c", "b", "a"]


def test_guncel_haberler_depo_hatasinda_bos_liste(monkeypatch):
    def patlar():
        raise RuntimeError("redis yok")
    monkeypatch.setattr(redis_store, "depo_yukle", patlar)
    assert sohbet_baglami._guncel_haberler() == []


def test_guncel_haberler_azami_siniri_asmaz(monkeypatch):
    depo = {str(i): _haber(str(i)) for i in range(30)}
    monkeypatch.setattr(redis_store, "depo_yukle", lambda: depo)
    assert len(sohbet_baglami._guncel_haberler()) == sohbet_baglami.AZAMI_HABER


# ------------------------------------------------------------------ ticker adayi tespiti
def _hisse(ticker, **kw):
    taban = {"ticker": ticker, "sirket": f"{ticker} Inc", "sektor": "Technology", "fiyat": 100.0,
              "degisim_yuzde": 1.0, "forward_pe": 15.0, "peg": 0.8, "p_fcf": 12.0,
              "ev_ebitda": 10.0, "ev_ebit": 11.0, "fcf_yield": 8.0}
    taban.update(kw)
    return taban


def test_ticker_deseni_kucuk_harfli_normal_kelimelerle_eslesmez():
    # Regresyon: eski desen ([A-Za-z]{1,5}) neredeyse her Turkce cumledeki
    # sozcukle eslesip her mesajda gereksiz 2 ekstra Redis okumasina (ve
    # gecikme/zaman asimi riskine) yol aciyordu. Yalnizca BUYUK harfle
    # yazilmis 2-5 harfli kelimeler aday sayilmali.
    assert sohbet_baglami._TICKER_ADAYI_DESENI.findall("bugün önemli ne var, kısaca özetler misin?") == []
    assert sohbet_baglami._TICKER_ADAYI_DESENI.findall("aapl nasıl gidiyor bugün") == []


def test_ticker_deseni_buyuk_harfli_kelimeyi_yakalar():
    assert sohbet_baglami._TICKER_ADAYI_DESENI.findall("AAPL nasıl gidiyor bugün") == ["AAPL"]


def test_evren_hisseleri_guvenli_hatada_bos_liste(monkeypatch):
    def patlar(evren):
        raise RuntimeError("onbellek yok")
    monkeypatch.setattr(ts, "oku", patlar)
    assert sohbet_baglami._evren_hisseleri_guvenli("sp500") == []


def test_hisseleri_esle_eslesen_tickeri_bulur():
    sonuc = sohbet_baglami._hisseleri_esle({"AAPL"}, [[_hisse("AAPL"), _hisse("MSFT")], []])
    assert [h["ticker"] for h in sonuc] == ["AAPL"]


def test_hisseleri_esle_iki_evrende_tekrar_etmez():
    sonuc = sohbet_baglami._hisseleri_esle({"AAPL"}, [[_hisse("AAPL")], [_hisse("AAPL")]])
    assert len(sonuc) == 1


def test_hisseleri_esle_eslesme_yoksa_bos():
    assert sohbet_baglami._hisseleri_esle({"ZZZZZ"}, [[_hisse("AAPL")]]) == []


# ------------------------------------------------------------------ baglam_olustur
def test_baglam_olustur_hicbir_veri_yoksa_acikca_belirtir(monkeypatch):
    monkeypatch.setattr(gun_ozeti_servisi, "bugunun_hazir_ozeti", lambda: None)
    monkeypatch.setattr(redis_store, "depo_yukle", lambda: {})
    monkeypatch.setattr(ts, "oku", lambda evren: {"hisseler": []})
    baglam = sohbet_baglami.baglam_olustur("merhaba")
    assert "veri" in baglam.lower() and "yok" in baglam.lower()


def test_baglam_olustur_gun_ozetini_icerir(monkeypatch):
    monkeypatch.setattr(gun_ozeti_servisi, "bugunun_hazir_ozeti", lambda: {"genelOzet": "Piyasalar yükseldi."})
    monkeypatch.setattr(redis_store, "depo_yukle", lambda: {})
    monkeypatch.setattr(ts, "oku", lambda evren: {"hisseler": []})
    assert "Piyasalar yükseldi." in sohbet_baglami.baglam_olustur("merhaba")


def test_baglam_olustur_hisse_ve_haber_birlikte(monkeypatch):
    monkeypatch.setattr(gun_ozeti_servisi, "bugunun_hazir_ozeti", lambda: None)
    monkeypatch.setattr(redis_store, "depo_yukle", lambda: {"a": _haber("a")})
    monkeypatch.setattr(ts, "oku", lambda evren: {"hisseler": [_hisse("AAPL")]} if evren == "sp500" else {"hisseler": []})
    baglam = sohbet_baglami.baglam_olustur("AAPL nasıl?")
    assert "AAPL" in baglam and "Başlık a" in baglam


# ==================================================================== /api/sohbet
class SahteRedis:
    def __init__(self):
        self.sayaclar: dict[str, int] = {}

    def _pipeline(self, komutlar):
        cikti = []
        for c in komutlar:
            if c[0] == "INCR":
                self.sayaclar[c[1]] = self.sayaclar.get(c[1], 0) + 1
                cikti.append({"result": self.sayaclar[c[1]]})
            elif c[0] == "EXPIRE":
                cikti.append({"result": 1})
        return cikti

    def kur(self, monkeypatch):
        monkeypatch.setattr(redis_store, "_pipeline", self._pipeline)
        return self


@pytest.fixture
def redis(monkeypatch):
    return SahteRedis().kur(monkeypatch)


def test_sohbet_gemini_anahtari_yoksa_500(istemci, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = istemci.post("/api/sohbet", json={"mesajlar": [{"rol": "kullanici", "metin": "merhaba"}]})
    assert r.status_code == 500


def test_sohbet_bos_mesaj_listesi_400(istemci, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    r = istemci.post("/api/sohbet", json={"mesajlar": []})
    assert r.status_code == 400


def test_sohbet_son_mesaj_asistandansa_400(istemci, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    r = istemci.post("/api/sohbet", json={"mesajlar": [{"rol": "asistan", "metin": "merhaba"}]})
    assert r.status_code == 400


def test_sohbet_basarili_ve_gemini_baglami_gorur(istemci, monkeypatch, redis):
    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(uygulama.sohbet_baglami, "baglam_olustur", lambda mesaj: "TEST_BAGLAM_ISARETI")
    yakalanan = {}

    def sahte_gemini(prompt, schema, api_key, model, timeout=10, deneme_sayisi=2):
        yakalanan["prompt"] = prompt
        return {"yanit": "Merhaba, nasıl yardımcı olabilirim?"}

    monkeypatch.setattr(uygulama, "gemini_json_iste", sahte_gemini)
    r = istemci.post("/api/sohbet", json={"mesajlar": [
        {"rol": "kullanici", "metin": "merhaba"},
        {"rol": "asistan", "metin": "selam!"},
        {"rol": "kullanici", "metin": "bugün ne var?"},
    ]})
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["yanit"] == "Merhaba, nasıl yardımcı olabilirim?"
    assert "TEST_BAGLAM_ISARETI" in yakalanan["prompt"]
    assert "bugün ne var?" in yakalanan["prompt"]
    assert "merhaba" in yakalanan["prompt"]  # gecmis de promptta


def test_sohbet_uzun_gecmis_kirpilir(istemci, monkeypatch, redis):
    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(uygulama.sohbet_baglami, "baglam_olustur", lambda mesaj: "")
    yakalanan = {}

    def sahte_gemini(prompt, schema, api_key, model, timeout=10, deneme_sayisi=2):
        yakalanan["prompt"] = prompt
        return {"yanit": "ok"}

    monkeypatch.setattr(uygulama, "gemini_json_iste", sahte_gemini)
    # gercekci gecmis: kullanici/asistan almasik, EN SONDA kullanici (son mesaj yanitlanacak)
    cok_uzun_gecmis = [{"rol": "kullanici" if i % 2 == 0 else "asistan", "metin": f"mesaj-{i}"} for i in range(39)]
    r = istemci.post("/api/sohbet", json={"mesajlar": cok_uzun_gecmis})
    assert r.status_code == 200
    assert "mesaj-0" not in yakalanan["prompt"]  # en eski mesajlar kirpildi
    assert "mesaj-38" in yakalanan["prompt"]  # en son mesaj korunur


def test_sohbet_rate_limit(istemci, monkeypatch, redis):
    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(uygulama.sohbet_baglami, "baglam_olustur", lambda mesaj: "")
    monkeypatch.setattr(uygulama, "gemini_json_iste", lambda *a, **k: {"yanit": "ok"})
    govde = {"mesajlar": [{"rol": "kullanici", "metin": "merhaba"}]}
    for _ in range(uygulama.SOHBET_AZAMI_ISTEK):
        assert istemci.post("/api/sohbet", json=govde).status_code == 200
    r = istemci.post("/api/sohbet", json=govde)
    assert r.status_code == 429 and "Retry-After" in r.headers
