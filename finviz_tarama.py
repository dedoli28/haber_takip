"""Finviz'in "Custom" tarama (screener) goruntusunden temel carpan/oran
verisi ceker: Forward P/E, PEG, P/FCF, EV/EBITDA + turetilen EV/EBIT ve FCF
Yield, sektor/ulke/borsa bilgisiyle birlikte. Sunucu tarafinda calisir.

Finviz Elite gerekmez: kullanilan sutun kimlikleri ve filtre kodlari, Finviz'in
ANONIM (girissiz) "Custom" gorunumune (https://finviz.com/screener?v=151)
canli istekle dogrulanmis, herkese acik degerlerdir. Elite hesabi varsa
sayfa basina daha fazla satir donebilir (bu modul buna gore tasarlanmamistir,
sayfalama sabit SAYFA_BOYUTU varsayimiyla calisir) ama gerekli degildir.

Not: Finviz, Cloudflare arkasinda; bulut/sunucu IP'lerine (ör. Vercel'in
serverless fonksiyonlari) duz `requests` ile istek atildiginda 403 + JS
meydan okuma sayfasi ("Just a moment...") donebilir (gelistirme ortaminda
sorun cikmadi ama Vercel'de canli olarak gozlemlendi). Bu yuzden `requests`
yerine `cloudscraper` kullanilir (bkz. _istemci()); bu, standart Cloudflare
JS meydan okumasini kod icinde cozer, ekstra ikili/tarayici gerektirmez.

Sutun kimlikleri (her biri TEK TEK, c=0,1,<id> istegiyle dogrulanmis - Finviz
geciz/geciksiz id'leri sessizce atlayip kalanlari kaydirdigi icin genis bir
c= listesiyle toplu istek atip sirayla okumak yanlis eslesmeye yol aciyordu):
Ticker=1, Company=2, Sector=3, Industry=4, Country=5, Market Cap=6,
Forward P/E=8, PEG=9, P/FCF=13, Oper M=40, Price=65, Change %=66, Sales=82,
Exchange=129, Enterprise Value=144, EV/EBITDA=145. Finviz'in kendisinde
EV/EBIT ya da FCF Yield alani YOKTUR; ikisi de asagida turetilir (bkz.
_turetilmis_alanlar).
"""

from __future__ import annotations

import concurrent.futures
import re
import threading
import warnings
from datetime import datetime, timezone

import cloudscraper
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", module="bs4")

TARAMA_URL = "https://finviz.com/screener"
SAYFA_BOYUTU = 20  # Finviz'in anonim (Elite'siz) goruntudeki sabit sayfa boyutu

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
_YUZDE_ALANLAR = {"oper_marj", "degisim_yuzde"}
_SAYISAL_ALANLAR = {
    "piyasa_degeri", "forward_pe", "peg", "p_fcf", "oper_marj", "fiyat",
    "degisim_yuzde", "satislar", "enterprise_value", "ev_ebitda",
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

_scraper: cloudscraper.CloudScraper | None = None
_scraper_kilit = threading.Lock()


def _istemci() -> cloudscraper.CloudScraper:
    """Paylasilan, Cloudflare JS meydan okumasini cozebilen istemci (Vercel'in
    sunucu IP'si Finviz'in Cloudflare korumasindan 403 "Just a moment..."
    aliyor; duz requests.get bunu asamiyor). Tek ornek: meydan okuma bir kez
    cozulur, sayfalar arasinda ayni oturum/cookie'lerle yeniden kullanilir."""
    global _scraper
    if _scraper is None:
        with _scraper_kilit:
            if _scraper is None:
                _scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
    return _scraper


def _sayi_ayristir(metin: str) -> float | None:
    """Finviz sayi hucrelerini (ör. '46.40B', '18.42%', '-25.10', '-') float'a
    cevirir. B/M/K carpanlarini uygular, yuzde isaretini kaldirir."""
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


def _sayfayi_ayristir(soup: BeautifulSoup) -> list[dict]:
    tablo = soup.find("table", class_="screener_table")
    if tablo is None:
        raise RuntimeError("Finviz tarama tablosu bulunamadı (site yapısı değişmiş olabilir).")
    satirlar = tablo.find_all("tr")[1:]  # ilk satir basliktir

    ogeler = []
    for satir in satirlar:
        hucreler = satir.find_all("td")
        if len(hucreler) < len(SUTUN_ID_SIRASI) + 1:  # +1: "No." sutunu
            continue
        oge: dict = {}
        for i, sutun_id in enumerate(SUTUN_ID_SIRASI):
            alan = SUTUN_ALAN_ADI[sutun_id]
            hucre = hucreler[i + 1]  # +1: "No." sutununu atla
            if alan == "ticker" and hucre.has_attr("data-boxover-ticker"):
                # Ticker hucresi logo+kisaltma avatari icin ikinci, gorsel bir
                # <span>A</span> icerir; duz get_text() bunu gercek sembolle
                # birlestirip "AAAPL" gibi bozuk bir deger uretir. Temiz deger
                # bu oznitelikte.
                oge[alan] = hucre["data-boxover-ticker"]
                continue
            metin = hucre.get_text(strip=True)
            if alan in _SAYISAL_ALANLAR:
                oge[alan] = _sayi_ayristir(metin)
            else:
                oge[alan] = metin
        if not oge.get("ticker"):
            continue
        _turetilmis_alanlar(oge)
        ogeler.append(oge)
    return ogeler


def _sayfa_cek(filtre: str, baslangic_satir: int) -> list[dict]:
    params = {
        "v": "151",
        "c": ",".join(str(i) for i in [0, *SUTUN_ID_SIRASI]),
    }
    if filtre:
        params["f"] = filtre
    if baslangic_satir > 1:
        params["r"] = str(baslangic_satir)
    resp = _istemci().get(TARAMA_URL, params=params, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return _sayfayi_ayristir(soup)


def _toplam_sonuc_sayisi(soup: BeautifulSoup) -> int:
    """Sayfa basligindaki '#1 / 503 Total' turu metinden toplam sonuc sayisini
    okur; bulunamazsa 0 (cagiran tek sayfa donduysun diye yorumlar)."""
    m = re.search(r"/\s*([\d,]+)\s*Total", soup.get_text())
    return int(m.group(1).replace(",", "")) if m else 0


def tarama_verisi_cek(evren: str = "sp500", azami_paralel: int = 6) -> tuple[list[dict], list[str]]:
    """Verilen evrendeki (varsayilan S&P 500) tum hisseleri, gerekli
    sutunlarla PARALEL sayfalar halinde ceker. (hisseler, hatalar) dondurur;
    bir sayfa basarisiz olursa digerleri yine de sonuca girer, hata mesaji
    listeye eklenir. Bu fonksiyon HIC cagrilmadan onbellekte veri kalmasin
    diye genelde bir cron/is tarafindan periyodik cagrilmasi beklenir."""
    filtre = EVREN_FILTRE_KODU.get(evren, EVREN_FILTRE_KODU["sp500"])
    hatalar: list[str] = []

    resp = _istemci().get(
        TARAMA_URL,
        params={"v": "151", "f": filtre, "c": ",".join(str(i) for i in [0, *SUTUN_ID_SIRASI])},
        timeout=25,
    )
    resp.raise_for_status()
    ilk_soup = BeautifulSoup(resp.text, "html.parser")
    toplam = _toplam_sonuc_sayisi(ilk_soup)
    ilk_sayfa = _sayfayi_ayristir(ilk_soup)

    if toplam <= SAYFA_BOYUTU:
        return ilk_sayfa, hatalar

    baslangiclar = list(range(1 + SAYFA_BOYUTU, toplam + 1, SAYFA_BOYUTU))
    tum_ogeler: dict[int, list[dict]] = {0: ilk_sayfa}
    with concurrent.futures.ThreadPoolExecutor(max_workers=azami_paralel) as executor:
        gelecek_sira = {executor.submit(_sayfa_cek, filtre, b): b for b in baslangiclar}
        for gelecek in concurrent.futures.as_completed(gelecek_sira):
            baslangic = gelecek_sira[gelecek]
            try:
                tum_ogeler[baslangic] = gelecek.result()
            except Exception as e:  # noqa: BLE001
                hatalar.append(f"sayfa {baslangic}: {e}")

    birlesik: list[dict] = []
    gorulen: set[str] = set()
    for baslangic in sorted(tum_ogeler):
        for oge in tum_ogeler[baslangic]:
            if oge["ticker"] in gorulen:
                continue
            gorulen.add(oge["ticker"])
            birlesik.append(oge)
    return birlesik, hatalar
