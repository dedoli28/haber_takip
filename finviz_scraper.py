"""Finviz haber/blog sayfalarini ceker. Sunucu tarafinda calisir.

Finviz'in birden fazla haber kategorisi var (Piyasa, Hisse, ETF, Kripto,
Pazar Nabzi) ve her biri ayri bir URL'de (?v=N). Kategoriye gore saat
hucresinin formati da degisiyor:
  - Piyasa Haberleri ve Bloglar: mutlak saat (ör. "02:05PM") ya da "Aug-19"
  - Hisse/ETF/Kripto/Pazar Nabzi Haberleri: goreli sure (ör. "19 min",
    "3 hours") ya da "Aug-19"

Not: "Pazar Nabzi" (?v=6) diger kategorilerden farkli bir yapida - gercek
disaridan bir makale linki yok, bunun yerine Finviz'in kendi AI'inin
urettigi kisa ticker guncellemeleri var (ör. "BofA Securities initiates
Alvotech with Buy rating"), her birine bir hisse rozeti (ticker + varsa
fiyat degisimi) iliskilendirilmis. Bu yuzden ayri bir ayristirici kullanir;
"Kaynagi Ac" o hissenin Finviz sayfasina gider.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FINVIZ_URL = "https://finviz.com/news.ashx"
FINVIZ_BASE = "https://finviz.com"

KATEGORI_V_PARAM = {
    "ana": None,
    "hisse": "3",
    "etf": "4",
    "kripto": "5",
    "pazar_nabzi": "6",
}

TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE)
RELATIVE_RE = re.compile(r"^\d+\s*(min|mins|minute|minutes|hour|hours|hr|hrs)$", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _satir_tarihini_belirle(saat_metni: str, bugun: date) -> date:
    if TIME_RE.match(saat_metni) or RELATIVE_RE.match(saat_metni):
        return bugun
    try:
        d = datetime.strptime(f"{saat_metni}-{bugun.year}", "%b-%d-%Y").date()
        if d > bugun:
            d = d.replace(year=d.year - 1)
        return d
    except ValueError:
        return bugun


def _sayfayi_getir(v_param: str | None) -> BeautifulSoup:
    url = FINVIZ_URL if not v_param else f"{FINVIZ_URL}?v={v_param}"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _tabloyu_ayristir(tablo) -> list[dict]:
    bugun = datetime.now().date()
    haberler: list[dict] = []
    gorulen_url: set[str] = set()
    sayac = 0

    for satir in tablo.select("tr.news_table-row"):
        saat_hucre = satir.select_one(".news_date-cell")
        link = satir.select_one(".news_link-cell a")
        if saat_hucre is None or link is None:
            continue

        saat_metni = saat_hucre.get_text(strip=True)
        tarih = _satir_tarihini_belirle(saat_metni, bugun)

        url = link.get("href", "").strip()
        baslik = link.get_text(strip=True)
        ozet_hucre = satir.select_one(".news_link-cell")
        kaynak_ozeti = ozet_hucre.get("data-boxover-text", "").strip() if ozet_hucre else ""

        if not url or url in gorulen_url:
            continue
        gorulen_url.add(url)

        try:
            kaynak = urlparse(url).netloc.replace("www.", "")
        except Exception:
            kaynak = ""

        haberler.append(
            {
                "id": str(sayac),
                "saat": saat_metni,
                "tarih": tarih.isoformat(),
                "baslik": baslik,
                "url": url,
                "kaynak": kaynak,
                "kaynakOzeti": kaynak_ozeti,
            }
        )
        sayac += 1

    return haberler


def _pazar_nabzi_ayristir(tablo) -> list[dict]:
    bugun = datetime.now().date()
    haberler: list[dict] = []
    gorulen: set[str] = set()
    sayac = 0

    for satir in tablo.select("tr.news_table-row"):
        saat_hucre = satir.select_one(".news_date-cell")
        baslik_el = satir.select_one(".market-pulse-headline")
        if saat_hucre is None or baslik_el is None:
            continue

        saat_metni = saat_hucre.get_text(strip=True)
        tarih = _satir_tarihini_belirle(saat_metni, bugun)
        baslik = baslik_el.get_text(strip=True)

        anahtar = f"{saat_metni}|{baslik}"
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)

        bilgi_parcalari = []
        url = None
        for rozet in satir.select(".market-pulse-badges a[data-boxover-ticker]"):
            sirket = rozet.get("data-boxover-company", "").strip()
            tam_metin = rozet.get_text(strip=True)
            ilk_span = rozet.select_one("span")
            gorunen_ticker = (
                ilk_span.get_text(strip=True) if ilk_span else (tam_metin.split()[0] if tam_metin else "")
            )
            degisim = tam_metin[len(gorunen_ticker):].strip() if tam_metin.startswith(gorunen_ticker) else ""

            parca = f"{sirket} ({gorunen_ticker})" if sirket else gorunen_ticker
            if degisim:
                parca += f", fiyat değişimi: {degisim}"
            if parca:
                bilgi_parcalari.append(parca)

            if url is None:
                href = rozet.get("href", "").strip()
                if href:
                    url = href if href.startswith("http") else f"{FINVIZ_BASE}{href}"

        haberler.append(
            {
                "id": str(sayac),
                "saat": saat_metni,
                "tarih": tarih.isoformat(),
                "baslik": baslik,
                "url": url or "https://finviz.com/news?v=6",
                "kaynak": "finviz.com",
                "kaynakOzeti": ("İlgili şirket(ler): " + "; ".join(bilgi_parcalari)) if bilgi_parcalari else "",
            }
        )
        sayac += 1

    return haberler


def finviz_haberlerini_cek(kategori: str = "ana") -> list[dict]:
    """Secilen kategorideki (ana/hisse/etf/kripto/pazar_nabzi) haber
    tablosunu ceker."""
    if kategori not in KATEGORI_V_PARAM:
        kategori = "ana"
    soup = _sayfayi_getir(KATEGORI_V_PARAM[kategori])
    tablo = soup.select_one("table.styled-table-new")
    if tablo is None:
        raise RuntimeError("Finviz sayfasında haber tablosu bulunamadı (site yapısı değişmiş olabilir).")

    if kategori == "pazar_nabzi":
        return _pazar_nabzi_ayristir(tablo)
    return _tabloyu_ayristir(tablo)


def finviz_bloglarini_cek() -> list[dict]:
    """Bloglar tablosu yalnizca ana (kategori parametresiz) sayfada, ikinci
    tablo olarak bulunuyor; kategori seciminden bagimsizdir."""
    soup = _sayfayi_getir(None)
    tablolar = soup.select("table.styled-table-new")
    if len(tablolar) < 2:
        raise RuntimeError("Finviz sayfasında bloglar tablosu bulunamadı (site yapısı değişmiş olabilir).")
    return _tabloyu_ayristir(tablolar[1])


TUM_TURLER = ["ana", "hisse", "etf", "kripto", "pazar_nabzi", "blog"]


def tum_turleri_cek() -> tuple[list[dict], list[str]]:
    """Tum haber kategorilerini + bloglari tek seferde ceker, her ogeye
    'kategori' alanini ekler, URL'e gore tekillestirir. (haberler, hatalar)
    tuple'i dondurur - bir kategori basarisiz olursa digerlerine devam eder."""
    birlesik: list[dict] = []
    gorulen: set[str] = set()
    hatalar: list[str] = []

    for kategori in TUM_TURLER:
        try:
            if kategori == "blog":
                ogeler = finviz_bloglarini_cek()
            else:
                ogeler = finviz_haberlerini_cek(kategori=kategori)
        except Exception as e:  # noqa: BLE001
            hatalar.append(f"{kategori}: {e}")
            continue

        for o in ogeler:
            if o["url"] in gorulen:
                continue
            gorulen.add(o["url"])
            o["kategori"] = kategori
            birlesik.append(o)

    return birlesik, hatalar
