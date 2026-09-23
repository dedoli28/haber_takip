"""Finviz Elite'in resmi Tarayici API'si (CSV disa aktarma) ile temel
carpan/oran verisi ceker: Forward P/E, PEG, P/FCF, EV/EBITDA + turetilen
EV/EBIT ve FCF Yield, sektor/ulke/borsa bilgisiyle birlikte. Sunucu
tarafinda calisir.

Onceki surum Finviz'in ANONIM (girissiz) HTML sayfasini kaziyordu; bu
gelistirme ortaminda calisti ama Vercel'in sunucu IP'sinden Cloudflare
403 + JS meydan okumasina ("Just a moment...") takildi (canli olarak
gozlemlendi, cloudscraper ile bile asilamadi). Finviz Elite'in resmi disa
aktarma uc noktasi (elite.finviz.com/export/screener, kisisel bir 'auth'
token'iyla) bu korumadan ETKILENMEDI ve AYRICA tum evreni TEK istekte
(sayfalama OLMADAN, CSV olarak) donduruyor - canli test edildi (S&P 500:
tek istekte 503 satir). Bu yuzden Finviz Elite hesabi + FINVIZ_AUTH_TOKEN
ortam degiskeni artik GEREKLI (bkz. _auth_token).

Sutun kimlikleri (daha once anonim gorunumde c=0,1,<id> ile tek tek
dogrulanmisti; export uc noktasinda da AYNI kimlikler gecerli - canli
test edildi): Ticker=1, Company=2, Sector=3, Industry=4, Country=5,
Market Cap=6, Forward P/E=8, PEG=9, P/FCF=13, Oper M=40, Price=65,
Change %=66, Sales=82, Exchange=129, Enterprise Value=144, EV/EBITDA=145.
Finviz'in kendisinde EV/EBIT ya da FCF Yield alani YOKTUR; ikisi de
asagida turetilir (bkz. _turetilmis_alanlar).
"""

from __future__ import annotations

import csv
import io
import os
import threading

import cloudscraper

TARAMA_URL = "https://elite.finviz.com/export/screener"

# Sutun kimligi -> sonuc sozlugundeki alan adi. Sira, istekte gonderilen c=
# parametresiyle AYNI olmali (asagidaki SUTUN_ID_SIRASI).
SUTUN_ALAN_ADI = {
    1: "ticker",
    2: "sirket",
    3: "sektor",
    4: "endustri",
    5: "ulke",
    6: "piyasa_degeri",
    8: "forward_pe",
    9: "peg",
    13: "p_fcf",
    40: "oper_marj",
    65: "fiyat",
    66: "degisim_yuzde",
    82: "satislar",
    129: "borsa",
    144: "enterprise_value",
    145: "ev_ebitda",
}
SUTUN_ID_SIRASI = sorted(SUTUN_ALAN_ADI)
_SAYISAL_ALANLAR = {
    "piyasa_degeri", "forward_pe", "peg", "p_fcf", "oper_marj", "fiyat",
    "degisim_yuzde", "satislar", "enterprise_value", "ev_ebitda",
}

# Finviz'in CSV basligi -> ic alan adi. SUTUN_ALAN_ADI ile ayni sirayi
# (SUTUN_ID_SIRASI) izler; export uc noktasi bu basliklari canli test
# edilerek dogrulanmis sekilde doner.
_CSV_BASLIK_ALAN = {
    "Ticker": "ticker", "Company": "sirket", "Sector": "sektor",
    "Industry": "endustri", "Country": "ulke", "Market Cap": "piyasa_degeri",
    "Forward P/E": "forward_pe", "PEG": "peg", "P/Free Cash Flow": "p_fcf",
    "Operating Margin": "oper_marj", "Price": "fiyat", "Change": "degisim_yuzde",
    "Sales": "satislar", "Exchange": "borsa", "Enterprise Value": "enterprise_value",
    "EV/EBITDA": "ev_ebitda",
}

# Dogrulanmis filtre kodlari (finviz.com/screener sol panelindeki secim
# listeleriyle birebir): Index -> idx_<kod>, Sector -> sec_<kod>,
# Country -> geo_<kod>, Exchange -> exch_<kod>.
EVREN_FILTRE_KODU = {"sp500": "idx_sp500", "nasdaq100": "idx_ndx", "nasdaq": "exch_nasd"}
SEKTOR_KODU = {
    "Basic Materials": "basicmaterials", "Communication Services": "communicationservices",
    "Consumer Cyclical": "consumercyclical", "Consumer Defensive": "consumerdefensive",
    "Energy": "energy", "Financial": "financial", "Healthcare": "healthcare",
    "Industrials": "industrials", "Real Estate": "realestate", "Technology": "technology",
    "Utilities": "utilities",
}


class FinvizYapilandirmaHatasi(Exception):
    """FINVIZ_AUTH_TOKEN ortam degiskeni tanimli degil."""


def _auth_token() -> str:
    token = os.environ.get("FINVIZ_AUTH_TOKEN", "").strip()
    if not token:
        raise FinvizYapilandirmaHatasi(
            "FINVIZ_AUTH_TOKEN tanimli degil (Finviz Elite > Tarama > Tarayici "
            "API'si sayfasindan alinir)."
        )
    return token


_scraper: cloudscraper.CloudScraper | None = None
_scraper_kilit = threading.Lock()


def _istemci() -> cloudscraper.CloudScraper:
    """Paylasilan, Cloudflare JS meydan okumasini cozebilen istemci. Elite'in
    /export/screener uc noktasi canli testte meydan okumaya takilmadi ama
    guvenlik payi icin yine de bu istemci kullanilir (requests'e gore
    dezavantaji yok)."""
    global _scraper
    if _scraper is None:
        with _scraper_kilit:
            if _scraper is None:
                _scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
    return _scraper


def _sayi_ayristir(metin: str) -> float | None:
    """Finviz sayi hucrelerini (ör. '46156.35', '-2.12%', '', '-') float'a
    cevirir. Yuzde isaretini kaldirir; bos/'-'/'N/A' icin None doner."""
    metin = (metin or "").strip().replace(",", "")
    if not metin or metin in ("-", "N/A"):
        return None
    carpan = 1.0
    if metin[-1] in "BMK":
        carpan = {"B": 1e9, "M": 1e6, "K": 1e3}[metin[-1]]
        metin = metin[:-1]
    elif metin.endswith("%"):
        metin = metin[:-1]
    try:
        return float(metin) * carpan
    except ValueError:
        return None


def _turetilmis_alanlar(oge: dict) -> None:
    """Finviz'de dogrudan olmayan iki orani, yerinde (in place) ekler:
    FCF Yield (%) = 100 / P/FCF; EV/EBIT (yaklasik) = Enterprise Value /
    (Sales x Operating Margin). Gerekli girdilerden biri eksik/gecersizse
    (ör. negatif/sifir P/FCF, sifir Oper M) sonuc None birakilir - uydurma
    deger konmaz."""
    p_fcf = oge.get("p_fcf")
    oge["fcf_yield"] = round(100.0 / p_fcf, 4) if p_fcf else None

    ev = oge.get("enterprise_value")
    satis = oge.get("satislar")
    oper_marj = oge.get("oper_marj")
    if ev and satis and oper_marj:
        ebit = satis * (oper_marj / 100.0)
        oge["ev_ebit"] = round(ev / ebit, 4) if ebit > 0 else None
    else:
        oge["ev_ebit"] = None


def _satirdan_oge(satir: dict) -> dict | None:
    """CSV DictReader satirini (Finviz basligi -> ic alan adi) modele cevirir.
    Ticker eksikse (ör. bos/bozuk satir) None doner."""
    oge: dict = {}
    for csv_baslik, alan in _CSV_BASLIK_ALAN.items():
        deger = satir.get(csv_baslik)
        oge[alan] = _sayi_ayristir(deger) if alan in _SAYISAL_ALANLAR else (deger or None)
    if not oge.get("ticker"):
        return None
    _turetilmis_alanlar(oge)
    return oge


def tarama_verisi_cek(evren: str = "sp500") -> tuple[list[dict], list[str]]:
    """Finviz Elite'in disa aktarma uc noktasindan verilen evrendeki
    (varsayilan S&P 500) TUM hisseleri TEK istekte ceker. (hisseler, hatalar)
    dondurur; 'hatalar' bu surumde (sayfalama olmadigi icin) pratikte hep
    bos listedir, imza onceki (sayfalama tabanli) surumle uyumlu kalsin diye
    korunur."""
    filtre = EVREN_FILTRE_KODU.get(evren, EVREN_FILTRE_KODU["sp500"])
    params = {
        "v": "151",
        "f": filtre,
        "c": ",".join(str(i) for i in [0, *SUTUN_ID_SIRASI]),
        "auth": _auth_token(),
    }
    resp = _istemci().get(TARAMA_URL, params=params, timeout=30)
    resp.raise_for_status()

    okuyucu = csv.DictReader(io.StringIO(resp.text))
    hisseler: list[dict] = []
    gorulen: set[str] = set()
    for satir in okuyucu:
        oge = _satirdan_oge(satir)
        if oge is None or oge["ticker"] in gorulen:
            continue
        gorulen.add(oge["ticker"])
        hisseler.append(oge)
    return hisseler, []
