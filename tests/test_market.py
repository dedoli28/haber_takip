"""Piyasa veri katmani testleri (ag/anahtar gerektirmez).

Calistirma:  pip install pytest httpx  &&  python -m pytest tests -q
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

import market_data_provider as mdp
import market_data_service as svc
import market_symbols as ms
import redis_store

ANAHTAR = "TEST-GIZLI-ANAHTAR-12345"


# ------------------------------------------------------------------ Yardimcilar
class SahteYanit:
    def __init__(self, status=200, govde=None, gecersiz_json=False):
        self.status_code = status
        self._govde = govde
        self._gecersiz = gecersiz_json

    def json(self):
        if self._gecersiz:
            raise ValueError("json degil")
        return self._govde


class SahteOturum:
    def __init__(self, isleyici):
        self.isleyici = isleyici
        self.cagrilar: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.cagrilar.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        sonuc = self.isleyici(url, params or {})
        if isinstance(sonuc, Exception):
            raise sonuc
        return sonuc


def _seri(baslangic: datetime, adet: int, adim: timedelta, taban=100.0, gunluk=False):
    """time_series 'values' (yeniden eskiye, Twelve Data gibi) uretir."""
    satirlar = []
    for i in range(adet):
        t = baslangic + i * adim
        satirlar.append(
            {
                "datetime": t.strftime("%Y-%m-%d") if gunluk else t.strftime("%Y-%m-%d %H:%M:%S"),
                "open": f"{taban + i:.2f}",
                "high": f"{taban + i + 1:.2f}",
                "low": f"{taban + i - 1:.2f}",
                "close": f"{taban + i + 0.5:.2f}",
                "volume": str(1000 + i),
            }
        )
    return list(reversed(satirlar))


def twelve(isleyici, **kw):
    return mdp.TwelveDataSaglayici(ANAHTAR, session=SahteOturum(isleyici), yeniden_deneme=kw.get("yeniden_deneme", 1))


def sahte_saglayici_kur(monkeypatch, saglayici):
    monkeypatch.setattr(svc, "_saglayici", lambda: (saglayici, "ok"))


class SayacSaglayici(mdp.PiyasaVeriSaglayici):
    """Cagri sayan, istege gore yavas/hatali taklit saglayici."""

    anahtar = "twelvedata"
    ad = "Test"

    def __init__(self, seri=None, hata=None, gecikme=0.0):
        self.mum_cagri = 0
        self.anlik_cagri = 0
        self.seri = seri
        self.hata = hata
        self.gecikme = gecikme

    def mumlar(self, kod, aralik, adet):
        self.mum_cagri += 1
        time.sleep(self.gecikme)
        if self.hata:
            raise self.hata
        return self.seri or [
            {"time": 1_800_000_000 + i * 300, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}
            for i in range(5)
        ]

    def anlik(self, kod):
        self.anlik_cagri += 1
        if self.hata:
            raise self.hata
        return {"son": 100.0, "onceki_kapanis": 99.0, "degisim": 1.0, "degisim_yuzde": 1.01, "zaman": 1_800_000_000,
                "piyasa_acik": True, "para_birimi": "TRY"}


# --------------------------------------------------------- Twelve Data ayristirma
def test_twelvedata_mumlari_normalize_eder_ve_anahtari_basliktan_gonderir():
    bas = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)
    sag = twelve(lambda u, p: SahteYanit(200, {"status": "ok", "values": _seri(bas, 4, timedelta(minutes=5))}))
    mumlar = sag.mumlar({"symbol": "XU100", "mic_code": "XIST"}, "5m", 130)

    assert [m["time"] for m in mumlar] == sorted(m["time"] for m in mumlar)  # artan
    assert mumlar[0] == {"time": int(bas.timestamp()), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}
    cagri = sag._session.cagrilar[0]
    assert cagri["params"]["interval"] == "5min" and cagri["params"]["mic_code"] == "XIST"
    assert cagri["params"]["timezone"] == "UTC"
    assert "apikey" not in {k.lower() for k in cagri["params"]}  # anahtar URL'de degil
    assert cagri["headers"]["Authorization"] == f"apikey {ANAHTAR}"


def test_twelvedata_gunluk_tarih_ve_eksik_alanlar():
    yanit = {"status": "ok", "values": [
        {"datetime": "2026-09-17", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "0"},
        {"datetime": "2026-09-18", "close": "13"},  # sadece kapanis
        {"datetime": "bozuk", "close": "1"},        # atlanir
        {"datetime": "2026-09-19", "close": "nan"},  # atlanir
    ]}
    mumlar = twelve(lambda u, p: SahteYanit(200, yanit)).mumlar({"symbol": "X"}, "1d", 10)
    assert [m["time"] for m in mumlar] == [
        int(datetime(2026, 9, 17, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 9, 18, tzinfo=timezone.utc).timestamp()),
    ]
    assert mumlar[1]["open"] == mumlar[1]["high"] == mumlar[1]["low"] == 13.0 and mumlar[1]["volume"] is None


def test_twelvedata_anlik():
    yanit = {"close": "10500.5", "previous_close": "10400", "change": "100.5", "percent_change": "0.97",
             "timestamp": 1_800_000_000, "is_market_open": False, "currency": "TRY"}
    a = twelve(lambda u, p: SahteYanit(200, yanit)).anlik({"symbol": "XU100"})
    assert a["son"] == 10500.5 and a["degisim"] == 100.5 and a["piyasa_acik"] is False and a["zaman"] == 1_800_000_000
    # change/percent yoksa hesaplanir
    b = twelve(lambda u, p: SahteYanit(200, {"close": "110", "previous_close": "100", "datetime": "2026-09-18"})).anlik({"symbol": "X"})
    assert b["degisim"] == 10 and b["degisim_yuzde"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    "yanit,beklenen",
    [
        (SahteYanit(429, {"status": "error", "code": 429, "message": "limit"}), "rate_limit"),
        (SahteYanit(200, {"status": "error", "code": 429, "message": "You have run out of API credits"}), "rate_limit"),
        (SahteYanit(401, {"status": "error", "code": 401, "message": "apikey wrong"}), "auth"),
        (SahteYanit(200, {"status": "error", "code": 403, "message": "forbidden"}), "auth"),
        (SahteYanit(404, {"status": "error", "code": 404, "message": "not found"}), "sembol"),
        (SahteYanit(200, {"status": "error", "code": 400, "message": "symbol not found"}), "sembol"),
        (SahteYanit(200, {"status": "error", "code": 400, "message": "This endpoint requires a plan upgrade"}), "plan"),
        (SahteYanit(200, gecersiz_json=True), "upstream"),
        (SahteYanit(200, ["liste"]), "upstream"),
    ],
)
def test_twelvedata_hata_esleme(yanit, beklenen):
    with pytest.raises(mdp.SaglayiciHatasi) as e:
        twelve(lambda u, p: yanit).mumlar({"symbol": "X"}, "5m", 10)
    assert e.value.kod == beklenen


def test_twelvedata_timeout_ve_5xx_bir_kez_yeniden_denenir_429_denenmez(monkeypatch):
    monkeypatch.setattr(mdp.time, "sleep", lambda s: None)
    bas = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)
    ok = SahteYanit(200, {"status": "ok", "values": _seri(bas, 2, timedelta(minutes=5))})

    sirali = iter([requests.Timeout(), ok])
    sag = twelve(lambda u, p: next(sirali))
    assert len(sag.mumlar({"symbol": "X"}, "5m", 5)) == 2 and len(sag._session.cagrilar) == 2

    sirali = iter([SahteYanit(503), SahteYanit(503)])
    sag = twelve(lambda u, p: next(sirali))
    with pytest.raises(mdp.SaglayiciHatasi) as e:
        sag.mumlar({"symbol": "X"}, "5m", 5)
    assert e.value.kod == "upstream" and len(sag._session.cagrilar) == 2

    sag = twelve(lambda u, p: SahteYanit(429, {"status": "error", "code": 429, "message": "x"}))
    with pytest.raises(mdp.SaglayiciHatasi):
        sag.mumlar({"symbol": "X"}, "5m", 5)
    assert len(sag._session.cagrilar) == 1  # 429 tekrar denenmez

    sag = twelve(lambda u, p: requests.Timeout())
    with pytest.raises(mdp.SaglayiciHatasi) as e:
        sag.mumlar({"symbol": "X"}, "5m", 5)
    assert e.value.kod == "timeout"


# ---------------------------------------------------------------- Saglayici fabrikasi
def test_fabrika_durumlari():
    assert mdp.saglayici_al({}) == (None, "yapilandirilmadi")
    assert mdp.saglayici_al({"MARKET_DATA_PROVIDER": "twelvedata"}) == (None, "anahtar_yok")
    assert mdp.saglayici_al({"MARKET_DATA_PROVIDER": "bilinmeyen"}) == (None, "bilinmeyen_saglayici")
    s, d = mdp.saglayici_al({"MARKET_DATA_PROVIDER": "twelvedata", "MARKET_DATA_API_KEY": ANAHTAR})
    assert d == "ok" and s.anahtar == "twelvedata" and not s.mock


def test_mock_yalnizca_gelistirmede():
    assert mdp.saglayici_al({"MARKET_DATA_PROVIDER": "mock"}) == (None, "mock_yasak")  # izin bayragi yok
    assert mdp.saglayici_al({"MARKET_DATA_PROVIDER": "mock", "MARKET_DATA_ALLOW_MOCK": "1", "VERCEL_ENV": "production"}) == (None, "mock_yasak")
    s, d = mdp.saglayici_al({"MARKET_DATA_PROVIDER": "mock", "MARKET_DATA_ALLOW_MOCK": "1", "VERCEL_ENV": "preview"})
    assert d == "ok" and s.mock
    s, d = mdp.saglayici_al({"MARKET_DATA_PROVIDER": "mock", "MARKET_DATA_ALLOW_MOCK": "1"})
    assert d == "ok" and s.mock


def test_mock_seri_deterministik_ve_makul():
    m = mdp.MockSaglayici(simdi=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    a = m.mumlar({"symbol": "XU100"}, "5m", 50)
    assert a == m.mumlar({"symbol": "XU100"}, "5m", 50)
    assert all(x["high"] >= max(x["open"], x["close"]) and x["low"] <= min(x["open"], x["close"]) for x in a)
    assert all(a[i]["time"] < a[i + 1]["time"] for i in range(len(a) - 1))


# ------------------------------------------------------------------ Servis: dogrulama
def test_yapilandirma_saglayicisiz_calisir(istemci):
    r = istemci.get("/api/market/config").json()
    assert r["ok"] and r["yapilandirildi"] is False and r["durum"] == "yapilandirilmadi"
    assert [u["kod"] for u in r["ulkeler"]] == ["TR", "US", "DE", "CN"]
    assert {e["id"] for u in r["ulkeler"] for e in u["endeksler"]} == {"XU100", "SPX", "IXIC", "GDAXI", "SSEC"}
    assert [a["id"] for a in r["araliklar"]] == ["1D", "1W", "1M", "3M", "1Y"]


def test_yapilandirilmamis_anlasilir_bos_durum_kodu_kirmaz(istemci):
    for url in ("/api/market/overview?country=TR", "/api/market/history?symbol=XU100&range=1D"):
        r = istemci.get(url)
        assert r.status_code == 503 and r.json()["kod"] == "yapilandirilmadi"
        assert "yapılandırılmadı" in r.json()["hata"]


@pytest.mark.parametrize(
    "url",
    [
        "/api/market/overview?country=FR",
        "/api/market/overview?country=%3Cscript%3E",
        "/api/market/history?symbol=AAPL&range=1D",            # izinli degil, haberde de yok
        "/api/market/history?symbol=XU100%3BDROP&range=1D",     # bicim
        "/api/market/history?symbol=../etc&range=1D",
        "/api/market/history?symbol=XU100&range=5Y",
        "/api/market/history?symbol=XU100&range=1D&interval=1m",  # bu aralik icin izinli degil
        "/api/market/history?symbol=XU100&range=1M&interval=5m",
        "/api/market/history",                                     # symbol zorunlu
    ],
)
def test_gecersiz_parametreler_saglayiciya_gitmeden_reddedilir(istemci, monkeypatch, url):
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    r = istemci.get(url)
    assert r.status_code in (400, 422)
    assert sag.mum_cagri == 0 and sag.anlik_cagri == 0


def test_hisse_yalnizca_haberlerde_gecen_koddan(istemci, monkeypatch):
    import app as uygulama

    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    monkeypatch.setattr(uygulama.redis_store, "depo_yukle", lambda: {
        "u1": {"kategori": "pazar_nabzi", "kaynakOzeti": "İlgili şirket(ler): Ionic Digital Inc (IOND), fiyat değişimi: 1%"},
    })
    assert istemci.get("/api/market/history?symbol=IOND&range=1D").status_code == 200
    assert istemci.get("/api/market/history?symbol=TSLA&range=1D").status_code == 400
    assert sag.mum_cagri == 1


def test_haber_sembolu_yalniz_guvenilir_tek_kod():
    ok = {"kategori": "pazar_nabzi", "kaynakOzeti": "İlgili şirket(ler): Lennar Corp (LEN); Foo Inc (FOO)"}
    assert ms.haber_sembolu(ok) is None  # iki kod: belirsiz
    assert ms.haber_sembolu({"kategori": "pazar_nabzi", "kaynakOzeti": "İlgili şirket(ler): Lennar Corp (LEN)"}) == "LEN"
    assert ms.haber_sembolu({"kategori": "pazar_nabzi", "kaynakOzeti": "İlgili şirket(ler): Alibaba (ADR) (BABA)"}) is None
    assert ms.haber_sembolu({"kategori": "ana", "kaynakOzeti": "İlgili şirket(ler): Lennar Corp (LEN)"}) is None
    assert ms.haber_sembolu({"kategori": "pazar_nabzi", "kaynakOzeti": "Fed faiz kararı (FOMC)"}) is None  # sirket ifadesi yok
    assert ms.haber_sembolu({"kategori": "pazar_nabzi"}) is None


def test_haberler_uc_noktasi_iliskili_sembolu_ekler(istemci, monkeypatch):
    import app as uygulama

    monkeypatch.setattr(uygulama.redis_store, "depo_yukle", lambda: {
        "a": {"url": "a", "kategori": "pazar_nabzi", "kaynakOzeti": "İlgili şirket(ler): Lennar Corp (LEN)", "ilkGorulme": "2026-09-18T10:00:00+00:00"},
        "b": {"url": "b", "kategori": "ana", "ilkGorulme": "2026-09-18T09:00:00+00:00"},
    })
    monkeypatch.setattr(uygulama.redis_store, "son_tarama_yukle", lambda: None)
    h = istemci.get("/api/haberler").json()["haberler"]
    assert h[0].get("iliskiliSembol") == "LEN" and "iliskiliSembol" not in h[1]


# --------------------------------------------------------- Servis: yanit sozlesmesi
def test_gecmis_yanit_sozlesmesi_ve_1g_kirpma(istemci, monkeypatch):
    dun = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
    bugun = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)
    seri = [
        {"time": int((dun + timedelta(minutes=5 * i)).timestamp()), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
        for i in range(5)
    ] + [
        {"time": int((bugun + timedelta(minutes=5 * i)).timestamp()), "open": 10, "high": 12, "low": 9, "close": 11, "volume": None}
        for i in range(6)
    ]
    sahte_saglayici_kur(monkeypatch, SayacSaglayici(seri=seri))
    r = istemci.get("/api/market/history?symbol=xu100&range=1D&interval=5m")
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["symbol"] == "XU100" and j["name"] == "BIST 100"
    assert j["currency"] == "TRY" and j["interval"] == "5m" and j["range"] == "1D"
    assert len(j["points"]) == 6 and all(p["close"] == 11 for p in j["points"])  # yalniz son gun
    assert set(j["points"][0]) == {"time", "open", "high", "low", "close", "volume"}
    assert j["stale"] is False and j["mock"] is False and j["updated_at"] and j["last_bar_at"]
    assert "s-maxage" in r.headers["cache-control"]


def test_1h_penceresi(monkeypatch):
    son = 1_800_000_000
    seri = [{"time": son - i * 86400, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(20)][::-1]
    a = ms.ARALIKLAR["1W"]
    kirpilmis = svc._kirp(seri, a)
    assert kirpilmis[-1]["time"] == son and kirpilmis[0]["time"] >= son - 7 * 86400 and len(kirpilmis) == 8


def test_genel_bakis(istemci, monkeypatch):
    sahte_saglayici_kur(monkeypatch, SayacSaglayici())
    j = istemci.get("/api/market/overview?country=us").json()
    assert j["ok"] and j["country"] == "US" and j["default_symbol"] == "SPX"
    assert [i["symbol"] for i in j["indices"]] == ["SPX", "IXIC"]
    assert j["indices"][0]["last"] == 100.0 and j["indices"][0]["change_pct"] == 1.01 and j["indices"][0]["updated_at"]


def test_genel_bakis_bir_endeks_hata_verirse_digeri_gosterilir(monkeypatch):
    class Kismi(SayacSaglayici):
        def anlik(self, kod):
            if kod["symbol"] == "IXIC":
                raise mdp.SaglayiciHatasi("plan", "ozel-ayrinti")
            return super().anlik(kod)

    sahte_saglayici_kur(monkeypatch, Kismi())
    yanit, _ = svc.genel_bakis("US")
    durum = {i["symbol"]: i["available"] for i in yanit["indices"]}
    assert durum == {"SPX": True, "IXIC": False}


def test_genel_bakis_hepsi_basarisizsa_hata(monkeypatch):
    sahte_saglayici_kur(monkeypatch, SayacSaglayici(hata=mdp.SaglayiciHatasi("upstream", "ic-ayrinti")))
    with pytest.raises(svc.PiyasaHatasi) as e:
        svc.genel_bakis("TR")
    assert e.value.http == 503


# ------------------------------------------------------------- Servis: onbellek vb.
def test_onbellek_ikinci_cagri_saglayiciya_gitmez_ttl_dolunca_gider(monkeypatch):
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    saat = [1_000_000.0]
    monkeypatch.setattr(svc, "_simdi", lambda: saat[0])

    svc.gecmis("XU100", "1D")
    svc.gecmis("XU100", "1D")
    assert sag.mum_cagri == 1
    saat[0] += ms.ARALIKLAR["1D"].ttl_sn + 1
    svc.gecmis("XU100", "1D")
    assert sag.mum_cagri == 2
    svc.gecmis("XU100", "1D", "15m")  # farkli anahtar
    assert sag.mum_cagri == 3


def test_ttl_gun_ici_kisa_uzun_donem_uzun():
    assert 60 <= ms.ARALIKLAR["1D"].ttl_sn <= 300
    assert all(600 <= ms.ARALIKLAR[k].ttl_sn <= 1800 for k in ("1M", "3M", "1Y"))


def test_saglayici_hatasinda_son_basarili_onbellek_eski_veri_olarak_doner(monkeypatch):
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    saat = [1_000_000.0]
    monkeypatch.setattr(svc, "_simdi", lambda: saat[0])

    taze, _ = svc.gecmis("XU100", "1D")
    assert taze["stale"] is False
    saat[0] += 10_000
    sag.hata = mdp.SaglayiciHatasi("upstream", "koptu")
    eski, sn = svc.gecmis("XU100", "1D")
    assert eski["stale"] is True and eski["points"] == taze["points"] and sn == 0


def test_onbellek_yokken_hata_genel_mesajla_doner_ayrinti_sizmaz(istemci, monkeypatch):
    sahte_saglayici_kur(monkeypatch, SayacSaglayici(hata=mdp.SaglayiciHatasi("auth", f"apikey {ANAHTAR} reddedildi")))
    r = istemci.get("/api/market/history?symbol=XU100&range=1D")
    assert r.status_code == 503 and r.json()["hata"] == "Piyasa verisi şu anda alınamıyor."
    assert ANAHTAR not in r.text and "reddedildi" not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_rate_limit_429_ve_retry_after(istemci, monkeypatch):
    sahte_saglayici_kur(monkeypatch, SayacSaglayici(hata=mdp.SaglayiciHatasi("rate_limit", "x")))
    r = istemci.get("/api/market/history?symbol=XU100&range=1D")
    assert r.status_code == 429 and r.json()["kod"] == "rate_limit" and r.headers["retry-after"] == "60"


def test_timeout_504(istemci, monkeypatch):
    sahte_saglayici_kur(monkeypatch, SayacSaglayici(hata=mdp.SaglayiciHatasi("timeout", "x")))
    assert istemci.get("/api/market/history?symbol=XU100&range=1D").status_code == 504


def test_yerel_dakikalik_butce_saglayici_cagrilarini_sinirlar(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MAX_CALLS_PER_MIN", "2")
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    monkeypatch.setattr(svc, "_simdi", lambda: 1_000_000.0)
    svc.gecmis("XU100", "1D")
    svc.gecmis("XU100", "1W")
    with pytest.raises(svc.PiyasaHatasi) as e:
        svc.gecmis("XU100", "1M")
    assert e.value.kod == "rate_limit" and sag.mum_cagri == 2
    svc.gecmis("XU100", "1D")  # onbellekten: butce harcamaz
    assert sag.mum_cagri == 2


def test_butce_asilinca_varsa_eski_veri_gosterilir(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MAX_CALLS_PER_MIN", "1")
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    monkeypatch.setattr(svc, "_simdi", lambda: 1_000_000.0)  # tum cagrilar ayni dakikada
    svc.gecmis("XU100", "1D")  # butceyi (1 cagri/dk) harcar
    monkeypatch.setattr(svc, "_taze_mi", lambda k, ttl: False)  # TTL dolmus say
    v, _ = svc.gecmis("XU100", "1D")  # yenileme denenir ama butce doldu -> eski veri
    assert v["stale"] is True and sag.mum_cagri == 1


def test_es_zamanli_istekler_tek_saglayici_cagrisina_indirgenir(monkeypatch):
    sag = SayacSaglayici(gecikme=0.3)
    sahte_saglayici_kur(monkeypatch, sag)
    sonuclar = []

    def is_():
        sonuclar.append(svc.gecmis("XU100", "1D")[0]["points"])

    kanallar = [threading.Thread(target=is_) for _ in range(8)]
    [t.start() for t in kanallar]
    [t.join() for t in kanallar]
    assert sag.mum_cagri == 1 and len(sonuclar) == 8 and all(s == sonuclar[0] for s in sonuclar)


class SahteRedis:
    def __init__(self):
        self.veri: dict[str, str] = {}
        self.kilitler: set[str] = set()

    def kur(self, monkeypatch):
        monkeypatch.setattr(redis_store, "onbellek_oku", lambda k: self.veri.get(k))
        monkeypatch.setattr(redis_store, "onbellek_yaz", lambda k, v, ttl: self.veri.__setitem__(k, v))
        monkeypatch.setattr(redis_store, "kilit_al", lambda k, ttl: (k not in self.kilitler) and not self.kilitler.add(k))
        monkeypatch.setattr(redis_store, "kilit_birak", lambda k: self.kilitler.discard(k))
        monkeypatch.setattr(redis_store, "istek_siniri_asildi_mi", lambda k, m, p: False)


def test_redis_onbellegi_ornekler_arasinda_paylasilir(monkeypatch):
    redis = SahteRedis()
    redis.kur(monkeypatch)
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)

    ilk, _ = svc.gecmis("XU100", "1D")
    assert any(k.startswith("htp:market:v1:h:XU100:1D:5m") for k in redis.veri)
    svc._bellek.clear()  # baska sunucu ornegi: bos bellek, ayni Redis
    ikinci, _ = svc.gecmis("XU100", "1D")
    assert sag.mum_cagri == 1 and ikinci["points"] == ilk["points"]
    assert not redis.kilitler  # kilit birakildi


def test_baska_ornek_yenilerken_bekler_ve_onun_sonucunu_kullanir(monkeypatch):
    redis = SahteRedis()
    redis.kur(monkeypatch)
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    monkeypatch.setattr(svc, "KILIT_YOKLAMA_SN", 0.05)
    anahtar = f"{svc.ANAHTAR_ONEKI}:h:XU100:1D:5m:twelvedata"
    redis.kilitler.add(f"{anahtar}:kilit")  # baska ornek kilidi tutuyor

    def diger_ornek_yazar():
        time.sleep(0.2)
        redis.veri[anahtar] = json.dumps({"alindi": time.time(), "veri": {"points": [
            {"time": 1_800_000_000, "open": 1, "high": 1, "low": 1, "close": 7, "volume": 1}]}})

    threading.Thread(target=diger_ornek_yazar).start()
    veri, _ = svc.gecmis("XU100", "1D")
    assert veri["points"][0]["close"] == 7 and sag.mum_cagri == 0


def test_redis_hatasi_servisi_bozmaz(monkeypatch):
    def patlar(*a, **k):
        raise RuntimeError("redis yok")

    for ad in ("onbellek_oku", "onbellek_yaz", "kilit_al", "kilit_birak", "istek_siniri_asildi_mi"):
        monkeypatch.setattr(redis_store, ad, patlar)
    sag = SayacSaglayici()
    sahte_saglayici_kur(monkeypatch, sag)
    assert svc.gecmis("XU100", "1D")[0]["ok"]
    assert svc.gecmis("XU100", "1D")[0]["ok"] and sag.mum_cagri == 1


# --------------------------------------------------------------- Mock uctan uca
def test_mock_ile_uctan_uca_yanit_mock_isaretli(istemci, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("MARKET_DATA_ALLOW_MOCK", "1")
    for r_ in ms.ARALIKLAR:
        j = istemci.get(f"/api/market/history?symbol=GDAXI&range={r_}").json()
        assert j["ok"] and j["mock"] is True and j["points"], r_
    assert istemci.get("/api/market/config").json()["mock"] is True


def test_mock_production_ortaminda_kapali(istemci, monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("MARKET_DATA_ALLOW_MOCK", "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    r = istemci.get("/api/market/history?symbol=XU100&range=1D")
    assert r.status_code == 503 and r.json()["kod"] == "yapilandirilmadi"


# ---------------------------------------------------------- Gizli anahtar sizintisi
def test_api_anahtari_hicbir_yanita_ve_statik_dosyaya_sizmaz(istemci, monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("MARKET_DATA_PROVIDER", "twelvedata")
    monkeypatch.setenv("MARKET_DATA_API_KEY", ANAHTAR)
    sah = SahteOturum(lambda u, p: SahteYanit(401, {"status": "error", "code": 401, "message": f"bad key {ANAHTAR}"}))
    monkeypatch.setattr(mdp.requests, "Session", lambda: sah)

    metinler = []
    for url in (
        "/api/market/config",
        "/api/market/overview?country=TR",
        "/api/market/history?symbol=XU100&range=1D",
        "/api/market/history?symbol=XU100&range=9Z",
        "/api/news/statistics?range=24h",
    ):
        r = istemci.get(url)
        metinler.append(r.text + json.dumps(dict(r.headers)))
    assert all(ANAHTAR not in m for m in metinler)

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for koklu, _, dosyalar in os.walk(os.path.join(kok, "static_ui")):
        for d in dosyalar:
            if d.endswith((".js", ".html", ".css")) and "vendor" not in koklu:
                icerik = open(os.path.join(koklu, d), encoding="utf-8").read()
                assert "MARKET_DATA_API_KEY" not in icerik and ANAHTAR not in icerik, d


def test_arayuz_kodunda_sembol_veya_saglayici_sabiti_yok():
    """Sembol/saglayici kodlari yalnizca market_symbols.py'de tutulur."""
    import os
    import re

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Sembol kimlikleri buyuk harfli ve tam kelime olarak aranir ("endeksSecici" gibi
    # sozcuklerle yanlis eslesmesin); saglayici adi buyuk/kucuk harf duyarsiz.
    yasak = re.compile(r"\b(XU100|GDAXI|SPX|IXIC|SSEC)\b")
    yasak_saglayici = re.compile(r"twelvedata", re.I)
    for d in ("market.js", "app.js", "index.html", "style.css"):
        icerik = open(os.path.join(kok, "static_ui", d), encoding="utf-8").read()
        assert not yasak.search(icerik) and not yasak_saglayici.search(icerik), d


# --------------------------------------------------------------- Haber istatistikleri
def test_haber_istatistikleri_hesabi():
    import app as uygulama

    simdi = datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc)  # Istanbul 15:30
    ist = lambda **k: (simdi - timedelta(**k)).isoformat()
    depo = {
        "1": {"ilkGorulme": ist(minutes=10), "ulke": "TR", "kategori": "ana", "sinif": "cok_onemli"},
        "2": {"ilkGorulme": ist(minutes=20), "kategori": "hisse", "sinif": "onemli"},          # ulke yok -> US
        "3": {"ilkGorulme": ist(hours=3), "ulke": "DE", "kategori": "ana", "sinif": "onemsiz"},
        "4": {"ilkGorulme": ist(hours=30), "ulke": "CN", "kategori": "ana", "sinif": "onemli"},  # 24s disi
        "5": {"ilkGorulme": "bozuk", "ulke": "TR"},
        "6": {"ilkGorulme": (simdi + timedelta(hours=2)).isoformat(), "ulke": "TR"},              # gelecek
        "7": {"ulke": "TR"},
    }
    s = uygulama._haber_istatistikleri_hesapla(depo, simdi)
    assert s["total"] == 3 and len(s["hourly"]) == 24
    assert s["hourly"][-1]["count"] == 2  # icinde bulunulan saat (15:00-15:59)
    assert s["hourly"][-1]["hour_start"].startswith("2026-09-18T15:00")
    assert s["hourly"][0]["hour_start"].startswith("2026-09-17T16:00")
    assert {c["key"]: c["count"] for c in s["by_country"]} == {"TR": 1, "US": 1, "DE": 1}
    assert [c["key"] for c in s["by_country"]] == ["TR", "US", "DE"]
    assert {c["key"]: c["count"] for c in s["by_importance"]} == {"cok_onemli": 1, "onemli": 1, "onemsiz": 1}
    assert sum(c["count"] for c in s["by_category"]) == 3


def test_haber_istatistikleri_ucu(istemci, monkeypatch):
    import app as uygulama

    monkeypatch.setattr(uygulama.redis_store, "depo_yukle", lambda: {})
    j = istemci.get("/api/news/statistics?range=24h").json()
    assert j["ok"] and j["total"] == 0 and j["range"] == "24h"
    assert istemci.get("/api/news/statistics?range=99h").status_code == 400

    def patlar():
        raise RuntimeError("redis ic ayrinti")

    monkeypatch.setattr(uygulama.redis_store, "depo_yukle", patlar)
    r = istemci.get("/api/news/statistics")
    assert r.status_code == 503 and "ic ayrinti" not in r.text


def test_haberler_gzip_sikistirir(istemci, monkeypatch):
    import app as uygulama

    depo = {str(i): {"url": str(i), "baslikTr": "x" * 200, "ilkGorulme": "2026-09-18T10:00:00+00:00"} for i in range(50)}
    monkeypatch.setattr(uygulama.redis_store, "depo_yukle", lambda: depo)
    monkeypatch.setattr(uygulama.redis_store, "son_tarama_yukle", lambda: None)
    r = istemci.get("/api/haberler", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
