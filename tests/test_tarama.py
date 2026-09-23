"""Hisse taramasi (screener): sayi ayristirma, turetilmis oranlar (EV/EBIT,
FCF Yield), Finviz Elite CSV disa aktarma ayristirma (agdan bagimsiz - sabit
ornek CSV ile), onbellek/filtreleme servisi ve API uclari. Finviz'e GERCEK
istek atmaz (network yok); sutun kimlikleri ve CSV basliklari, Finviz'e
atilan canli dogrulama istekleriyle onaylanmis, burada sabit test verisiyle
calisilir."""

from __future__ import annotations

import json

import pytest

import finviz_tarama as ft
import redis_store
import tarama_servisi as ts


# ------------------------------------------------------------------ sayi ayristirma
@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        ("46.40B", 46_400_000_000.0),
        ("7.37M", 7_370_000.0),
        ("500K", 500_000.0),
        ("18.42%", 18.42),
        ("-1.60%", -1.6),
        ("24.40", 24.4),
        ("-25.10", -25.1),
        ("-", None),
        ("", None),
        ("N/A", None),
        ("bozuk123x", None),
    ],
)
def test_sayi_ayristir(girdi, beklenen):
    assert ft._sayi_ayristir(girdi) == beklenen


# ------------------------------------------------------------------ turetilmis alanlar
def test_fcf_yield_pfcf_tersidir():
    oge = {"p_fcf": 20.0, "enterprise_value": None, "satislar": None, "oper_marj": None}
    ft._turetilmis_alanlar(oge)
    assert oge["fcf_yield"] == 5.0  # 100/20


def test_fcf_yield_pfcf_yoksa_none():
    oge = {"p_fcf": None, "enterprise_value": None, "satislar": None, "oper_marj": None}
    ft._turetilmis_alanlar(oge)
    assert oge["fcf_yield"] is None


def test_fcf_yield_negatif_pfcf_de_hesaplanir_isaretiyle():
    # Negatif P/FCF (nakit yakan sirket) icin de matematiksel ters alinir;
    # yorumlamayi (anlamli olup olmadigini) kullanici/arayuz filtre araligina birakir.
    oge = {"p_fcf": -20.0, "enterprise_value": None, "satislar": None, "oper_marj": None}
    ft._turetilmis_alanlar(oge)
    assert oge["fcf_yield"] == -5.0


def test_ev_ebit_dogru_hesaplanir():
    # EBIT = Sales * OperM = 1000 * 0.20 = 200; EV/EBIT = 2000/200 = 10
    oge = {"p_fcf": None, "enterprise_value": 2000.0, "satislar": 1000.0, "oper_marj": 20.0}
    ft._turetilmis_alanlar(oge)
    assert oge["ev_ebit"] == 10.0


def test_ev_ebit_eksik_girdide_none():
    for eksik in ("enterprise_value", "satislar", "oper_marj"):
        oge = {"enterprise_value": 2000.0, "satislar": 1000.0, "oper_marj": 20.0, "p_fcf": None}
        oge[eksik] = None
        ft._turetilmis_alanlar(oge)
        assert oge["ev_ebit"] is None, eksik


def test_ev_ebit_sifir_veya_negatif_operasyon_karinda_none():
    # Zarar eden (negatif operasyon marji) sirketlerde EV/EBIT anlamsizdir; uydurma deger konmaz.
    oge = {"enterprise_value": 2000.0, "satislar": 1000.0, "oper_marj": -5.0, "p_fcf": None}
    ft._turetilmis_alanlar(oge)
    assert oge["ev_ebit"] is None


# ------------------------------------------------------------------ CSV ayristirma (sabit ornek)
_CSV_BASLIK_SATIRI = (
    '"No.","Ticker","Company","Sector","Industry","Country","Market Cap",'
    '"Forward P/E","PEG","P/Free Cash Flow","Operating Margin","Price","Change",'
    '"Sales","Exchange","Enterprise Value","EV/EBITDA"'
)


def _ornek_csv_satiri(ticker: str, sirket: str) -> str:
    # Gercek Finviz Elite disa aktarma satirini taklit eder (canli dogrulanmis
    # basliklar/sira; para birimleri milyon cinsinden duz sayidir, B/M/K eki YOK).
    return (
        f'1,"{ticker}","{sirket}","Technology","Software - Application","USA",'
        f'100000.00,15.00,0.80,12.00,25.00%,150.00,1.20%,500.00,"NASD",1200000.00,10.50'
    )


def _ornek_csv(satirlar: list[str]) -> str:
    return "\n".join([_CSV_BASLIK_SATIRI, *satirlar])


def test_satirdan_oge_ticker_ve_alanlar_dogru_eslesir():
    import csv
    import io

    okuyucu = csv.DictReader(io.StringIO(_ornek_csv([_ornek_csv_satiri("MSFT", "Microsoft Corp")])))
    oge = ft._satirdan_oge(next(okuyucu))
    assert oge["ticker"] == "MSFT"
    assert oge["sirket"] == "Microsoft Corp"
    assert oge["sektor"] == "Technology"
    assert oge["ulke"] == "USA"
    assert oge["piyasa_degeri"] == 100_000.0
    assert oge["forward_pe"] == 15.0
    assert oge["peg"] == 0.8
    assert oge["p_fcf"] == 12.0
    assert oge["oper_marj"] == 25.0
    assert oge["borsa"] == "NASD"
    assert oge["enterprise_value"] == 1_200_000.0
    assert oge["ev_ebitda"] == 10.5
    # turetilmis alanlar da satir ayristirmasinda otomatik hesaplanir
    assert oge["fcf_yield"] == round(100 / 12.0, 4)
    assert oge["ev_ebit"] is not None


def test_satirdan_oge_bos_hucre_none_doner():
    # CSV'de eksik deger bos hucre olarak gelir (ör: "...,,,12.00,..." - Forward
    # P/E ve PEG bos), "-" degil.
    import csv
    import io

    satir = (
        '1,"ZZZ","Zeta Inc","Technology","Software - Application","USA",'
        '100000.00,,,12.00,25.00%,150.00,1.20%,500.00,"NASD",1200000.00,10.50'
    )
    okuyucu = csv.DictReader(io.StringIO(_ornek_csv([satir])))
    oge = ft._satirdan_oge(next(okuyucu))
    assert oge["forward_pe"] is None and oge["peg"] is None


def test_satirdan_oge_ticker_yoksa_none():
    import csv
    import io

    satir = _ornek_csv_satiri("X", "X Inc").replace('"X"', '""', 1)
    okuyucu = csv.DictReader(io.StringIO(_ornek_csv([satir])))
    assert ft._satirdan_oge(next(okuyucu)) is None


def test_auth_token_tanimli_degilse_hata(monkeypatch):
    monkeypatch.delenv("FINVIZ_AUTH_TOKEN", raising=False)
    with pytest.raises(ft.FinvizYapilandirmaHatasi):
        ft._auth_token()


def test_tarama_verisi_cek_uctan_uca_csv_ayristirir_ve_tekillestirir(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "test-token")

    class SahteYanit:
        status_code = 200
        text = _ornek_csv([_ornek_csv_satiri("AAPL", "Apple Inc"), _ornek_csv_satiri("AAPL", "Apple Inc")])

        def raise_for_status(self):
            pass

    class SahteIstemci:
        def get(self, url, params=None, timeout=None):
            assert params["auth"] == "test-token"
            assert url == ft.TARAMA_URL
            return SahteYanit()

    monkeypatch.setattr(ft, "_istemci", lambda: SahteIstemci())
    hisseler, hatalar = ft.tarama_verisi_cek("sp500")
    assert hatalar == []
    assert [h["ticker"] for h in hisseler] == ["AAPL"]  # tekrarlanan satir tekillestirilir


def test_vercel_cron_tarama_girisleri_gunde_bir_hobby_uyumlu():
    import json
    import os

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(kok, "vercel.json"), encoding="utf-8"))
    crons = [c for c in cfg["crons"] if c["path"].startswith("/api/cron/tarama")]
    # Hobby plan her cron ifadesinin GUNDE BIR calismasini zorunlu kilar; bu
    # yuzden 2 evren x 2 saat = 4 AYRI gunluk giris var, tek bir "iki kez/gun" ifadesi yok.
    assert len(crons) == 4
    assert {c["path"] for c in crons} == {"/api/cron/tarama?evren=sp500", "/api/cron/tarama?evren=nasdaq100"}
    for c in crons:
        parcalar = c["schedule"].split()
        assert parcalar[2:] == ["*", "*", "*"], c  # gunde bir (Hobby siniri)


def test_filtre_kodlari_kapsam_disi_deger_icermez():
    # Kullanici kapsami "yalnizca ABD" secti: en azindan sp500/nasdaq100 tanimli olmali.
    assert set(ft.EVREN_FILTRE_KODU) >= {"sp500", "nasdaq100"}
    assert ft.EVREN_FILTRE_KODU["sp500"] == "idx_sp500"


# ==================================================================== tarama_servisi
class SahteRedis:
    def __init__(self):
        self.veri: dict[str, str] = {}
        self.kilitler: set[str] = set()
        self.sayaclar: dict[str, int] = {}

    def _pipeline(self, komutlar):
        cikti = []
        for c in komutlar:
            if c[0] == "INCR":
                self.sayaclar[c[1]] = self.sayaclar.get(c[1], 0) + 1
                cikti.append({"result": self.sayaclar[c[1]]})
            elif c[0] == "EXPIRE":
                cikti.append({"result": 1})
            else:
                raise AssertionError(c)
        return cikti

    def kur(self, monkeypatch):
        monkeypatch.setattr(redis_store, "onbellek_oku", lambda k: self.veri.get(k))
        monkeypatch.setattr(redis_store, "onbellek_yaz", lambda k, v, ttl: self.veri.__setitem__(k, v))
        monkeypatch.setattr(redis_store, "kilit_al", lambda k, ttl: (k not in self.kilitler) and not self.kilitler.add(k))
        monkeypatch.setattr(redis_store, "kilit_birak", lambda k: self.kilitler.discard(k))
        monkeypatch.setattr(redis_store, "_pipeline", self._pipeline)
        return self


@pytest.fixture
def redis(monkeypatch):
    return SahteRedis().kur(monkeypatch)


def _hisse(ticker, sektor="Technology", ulke="USA", **kw):
    taban = {
        "ticker": ticker, "sirket": f"{ticker} Inc", "sektor": sektor, "endustri": "Software",
        "ulke": ulke, "piyasa_degeri": 1e9, "forward_pe": 15.0, "peg": 0.8, "p_fcf": 15.0,
        "oper_marj": 20.0, "fiyat": 100.0, "degisim_yuzde": 1.0, "satislar": 1e8, "borsa": "NASD",
        "enterprise_value": 1e9, "ev_ebitda": 10.0,
    }
    taban.update(kw)
    ft._turetilmis_alanlar(taban)
    return taban


def test_evreni_dogrula():
    assert ts.evreni_dogrula(None) == ts.VARSAYILAN_EVREN
    assert ts.evreni_dogrula("NASDAQ100") == "nasdaq100"
    with pytest.raises(ts.TaramaHatasi):
        ts.evreni_dogrula("bilinmeyen")


def test_oku_onbellek_bossa_missing_doner(redis):
    y = ts.oku("sp500")
    assert y["durum"] == "missing" and y["hisseler"] == [] and y["guncellendi"] is None


def test_yenile_ve_oku_uctan_uca(redis, monkeypatch):
    hisseler = [_hisse("AAA"), _hisse("BBB")]
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: (hisseler, []))
    s = ts.yenile("sp500")
    assert s["sonuc"] == "hazir" and s["hisseSayisi"] == 2
    y = ts.oku("sp500")
    assert y["durum"] == "ready" and y["toplamHisseSayisi"] == 2 and len(y["hisseler"]) == 2
    assert y["guncellendi"]
    assert not any(k.endswith(":kilit") for k in redis.kilitler)  # kilit birakildi


def test_yenile_bos_sonuc_hata_sayilir(redis, monkeypatch):
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: ([], []))
    s = ts.yenile("sp500")
    assert s["sonuc"] == "hata" and s["hata"] == "veri_yok"


def test_yenile_istisna_hata_doner_kilit_birakilir(redis, monkeypatch):
    def patlar(evren, **kw):
        raise RuntimeError("ag hatasi")
    monkeypatch.setattr(ft, "tarama_verisi_cek", patlar)
    s = ts.yenile("sp500")
    assert s["sonuc"] == "hata"
    assert not redis.kilitler


def test_yenile_es_zamanli_ikinci_cagri_atlanir(redis, monkeypatch):
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: ([_hisse("AAA")], []))
    redis.kilitler.add(f"{ts.ANAHTAR_ONEKI}:sp500:kilit")  # baska bir cagri zaten calisiyor
    s = ts.yenile("sp500")
    assert s["sonuc"] == "atlandi"


def test_filtrele_araliklar():
    hisseler = [_hisse("A", forward_pe=12), _hisse("B", forward_pe=25), _hisse("C", forward_pe=None)]
    sonuc = ts.filtrele(hisseler, {"forward_pe": (10, 20)})
    assert [h["ticker"] for h in sonuc] == ["A"]  # B araligin disinda, C veri yok -> elenir


def test_filtrele_kullanicinin_istedigi_6_kriter_birlikte():
    # ev_ebit = enterprise_value / (satislar * oper_marj/100) = 2.8e8 / (1e8*0.20) = 14, filtre araliginda (10-18)
    tam = _hisse("UYUYOR", forward_pe=15, peg=0.5, p_fcf=15, ev_ebitda=10, oper_marj=20, satislar=1e8, enterprise_value=2.8e8)
    uymuyor = _hisse("UYMUYOR", forward_pe=30, peg=3, p_fcf=40, ev_ebitda=25, oper_marj=5, satislar=1e8, enterprise_value=1e9)
    filtreler = {f["alan"]: (f["varsayilan_min"], f["varsayilan_max"]) for f in ts.FILTRE_TANIMLARI}
    sonuc = ts.filtrele([tam, uymuyor], filtreler)
    assert [h["ticker"] for h in sonuc] == ["UYUYOR"]


def test_filtrele_sektor_ulke_arama():
    a = _hisse("AAPL", sektor="Technology", ulke="USA")
    b = _hisse("SAP", sektor="Technology", ulke="Germany")
    c = _hisse("XOM", sektor="Energy", ulke="USA")
    assert [h["ticker"] for h in ts.filtrele([a, b, c], {}, sektorler={"Technology"})] == ["AAPL", "SAP"]
    assert [h["ticker"] for h in ts.filtrele([a, b, c], {}, ulkeler={"USA"})] == ["AAPL", "XOM"]
    assert [h["ticker"] for h in ts.filtrele([a, b, c], {}, arama="xom")] == ["XOM"]
    assert [h["ticker"] for h in ts.filtrele([a, b, c], {}, arama="APPLE".lower())] == []
    a["sirket"] = "Apple Inc"
    assert [h["ticker"] for h in ts.filtrele([a, b, c], {}, arama="apple")] == ["AAPL"]


def test_mevcut_sektorler_ulkeler_gercek_veriden_turetilir(redis, monkeypatch):
    hisseler = [_hisse("A", sektor="Technology", ulke="USA"), _hisse("B", sektor="Energy", ulke="Canada")]
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: (hisseler, []))
    ts.yenile("sp500")
    sektorler, ulkeler = ts.mevcut_sektorler_ulkeler("sp500")
    assert sektorler == ["Energy", "Technology"] and ulkeler == ["Canada", "USA"]


def test_mevcut_sektorler_ulkeler_bossa_bos_liste(redis):
    assert ts.mevcut_sektorler_ulkeler("sp500") == ([], [])


# ==================================================================== HTTP uclari
def test_config_ucu_finviz_gitmeden_calisir(istemci):
    r = istemci.get("/api/tarama/config")
    j = r.json()
    assert r.status_code == 200 and j["ok"]
    assert {e["kod"] for e in j["evrenler"]} == {"sp500", "nasdaq100"}
    alanlar = {f["alan"] for f in j["filtreler"]}
    assert alanlar == {"forward_pe", "peg", "p_fcf", "ev_ebitda", "ev_ebit", "fcf_yield"}
    # kullanicinin istedigi varsayilan araliklar birebir
    varsayilanlar = {f["alan"]: (f["varsayilan_min"], f["varsayilan_max"]) for f in j["filtreler"]}
    assert varsayilanlar == {
        "forward_pe": (10, 20), "peg": (0, 1), "p_fcf": (10, 20),
        "ev_ebitda": (8, 15), "ev_ebit": (10, 18), "fcf_yield": (5, 10),
    }


def test_sonuclar_ucu_bos_onbellek_missing(istemci, redis):
    r = istemci.get("/api/tarama/sonuclar?evren=sp500")
    j = r.json()
    assert r.status_code == 200 and j["durum"] == "missing" and j["hisseler"] == []
    assert r.headers["cache-control"] == "no-store"


def test_sonuclar_ucu_gecersiz_evren(istemci):
    r = istemci.get("/api/tarama/sonuclar?evren=hicbirsey")
    assert r.status_code == 400


def test_sonuclar_ucu_dolu_onbellek(istemci, redis, monkeypatch):
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: ([_hisse("AAA"), _hisse("BBB")], []))
    ts.yenile("sp500")
    r = istemci.get("/api/tarama/sonuclar?evren=sp500")
    j = r.json()
    assert j["durum"] == "ready" and j["toplamHisseSayisi"] == 2 and len(j["hisseler"]) == 2


def test_cron_ucu_yetkisiz(istemci, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("POLL_SECRET", raising=False)
    assert istemci.get("/api/cron/tarama").status_code == 401


def test_cron_ucu_calisir_ve_tekrar_cagrida_kilitle_atlanir(istemci, redis, monkeypatch):
    import app as uygulama

    monkeypatch.setenv("CRON_SECRET", "cron-gizli-degeri-123456")
    cagri = []
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: (cagri.append(1) or [_hisse("AAA")], []))
    r = istemci.get("/api/cron/tarama?evren=sp500", headers={"Authorization": "Bearer cron-gizli-degeri-123456"})
    assert r.status_code == 200 and r.json()["sonuc"] == "hazir" and cagri == [1]


def test_harici_cron_x_poll_secret_ile(istemci, redis, monkeypatch):
    monkeypatch.setenv("POLL_SECRET", "poll-gizli-degeri-123456")
    monkeypatch.setattr(ft, "tarama_verisi_cek", lambda evren, **kw: ([_hisse("AAA")], []))
    assert istemci.post("/api/tarama-guncelle").status_code == 401
    r = istemci.post("/api/tarama-guncelle", headers={"X-Poll-Secret": "poll-gizli-degeri-123456"})
    assert r.status_code == 200 and r.json()["sonuc"] == "hazir"


def test_tarama_analiz_gemini_anahtari_yoksa_500(istemci, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = istemci.post("/api/tarama/analiz", json={"hisseler": [{"ticker": "AAA"}]})
    assert r.status_code == 500


def test_tarama_analiz_bos_liste_400(istemci, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    r = istemci.post("/api/tarama/analiz", json={"hisseler": []})
    assert r.status_code == 400


def test_tarama_analiz_basarili_ve_gemini_gorur(istemci, monkeypatch, redis):
    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    yakalanan = {}

    def sahte_gemini(prompt, schema, api_key, model):
        yakalanan["prompt"] = prompt
        return {"temalar": "Cogu yazilim sirketi.", "dikkat_cekenler": [{"ticker": "AAA", "not": "dusuk PEG"}], "uyari": "Yatirim tavsiyesi degildir."}

    monkeypatch.setattr(uygulama, "gemini_json_iste", sahte_gemini)
    r = istemci.post("/api/tarama/analiz", json={
        "filtreOzeti": "Forward P/E 10-20",
        "hisseler": [{"ticker": "AAA", "sirket": "AAA Inc", "sektor": "Technology", "ulke": "USA",
                       "forward_pe": 15, "peg": 0.5, "p_fcf": 15, "ev_ebitda": 10, "ev_ebit": 12, "fcf_yield": 6.5}],
    })
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["temalar"] and j["dikkatCekenler"][0]["ticker"] == "AAA"
    assert "AAA" in yakalanan["prompt"] and "Forward P/E 10-20" in yakalanan["prompt"]


def test_tarama_analiz_rate_limit(istemci, monkeypatch, redis):
    import app as uygulama

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(uygulama, "gemini_json_iste", lambda *a, **k: {"temalar": "x", "dikkat_cekenler": [], "uyari": "x"})
    gövde = {"hisseler": [{"ticker": "AAA"}]}
    for _ in range(uygulama.TARAMA_ANALIZ_AZAMI_ISTEK):
        assert istemci.post("/api/tarama/analiz", json=gövde).status_code == 200
    r = istemci.post("/api/tarama/analiz", json=gövde)
    assert r.status_code == 429 and "Retry-After" in r.headers
