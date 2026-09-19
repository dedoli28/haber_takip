"""Piyasa veri katmaninin TEK yapilandirma dosyasi.

Izinli endeksler, saglayici sembol/kod eslemeleri, zaman araligi tanimlari ve
onbellek sureleri yalnizca burada tutulur; arayuz (JS) bu bilgileri
`/api/market/config` uzerinden okur, kodunda sembol/saglayici sabiti tasimaz.

Yeni bir endeks eklemek icin ENDEKSLER listesine bir satir eklemek yeterlidir.
Yeni bir saglayici icin `saglayici` sozlugune o saglayicinin anahtariyla
(ör. "twelvedata") kendi sembol/kod eslemesi eklenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ulke sekmeleri (arayuzde bu sirayla gosterilir).
ULKELER: dict[str, str] = {"TR": "Türkiye", "US": "ABD", "DE": "Almanya", "CN": "Çin"}


@dataclass(frozen=True)
class Endeks:
    id: str  # API'de kullanilan dahili kimlik (izinli semboller yalnizca bunlardir)
    ulke: str
    ad: str
    para_birimi: str
    # Saglayici anahtari -> o saglayicinin bekledigi sembol/kod parametreleri.
    saglayici: dict[str, dict[str, str]] = field(default_factory=dict)
    varsayilan: bool = False  # ulkenin varsayilan (ana) endeksi
    # Sagliyicinin herkese acik katalogunda dogrulanamayan endeksler icin not
    # (arayuzde gosterilmez; README'de belirtilir).
    not_: str = ""


ENDEKSLER: list[Endeks] = [
    Endeks(
        id="XU100",
        ulke="TR",
        ad="BIST 100",
        para_birimi="TRY",
        saglayici={"twelvedata": {"symbol": "XU100", "mic_code": "XIST"}},
        varsayilan=True,
    ),
    Endeks(
        id="SPX",
        ulke="US",
        ad="S&P 500",
        para_birimi="USD",
        saglayici={"twelvedata": {"symbol": "SPX"}},
        varsayilan=True,
        not_="Twelve Data'nin herkese acik endeks katalogunda listelenmiyor; plan kapsamini dogrulayin.",
    ),
    Endeks(
        id="IXIC",
        ulke="US",
        ad="NASDAQ Composite",
        para_birimi="USD",
        saglayici={"twelvedata": {"symbol": "IXIC"}},
        not_="Twelve Data'nin herkese acik endeks katalogunda listelenmiyor; plan kapsamini dogrulayin.",
    ),
    Endeks(
        id="GDAXI",
        ulke="DE",
        ad="DAX",
        para_birimi="EUR",
        saglayici={"twelvedata": {"symbol": "GDAXI", "mic_code": "XETR"}},
        varsayilan=True,
    ),
    Endeks(
        id="SSEC",
        ulke="CN",
        ad="Shanghai Composite",
        para_birimi="CNY",
        saglayici={"twelvedata": {"symbol": "000001", "mic_code": "XSHG"}},
        varsayilan=True,
    ),
]

# Yalnizca-gelistirme "mock" saglayicisi icin her endekse dahili bir esleme eklenir.
for _e in ENDEKSLER:
    _e.saglayici.setdefault("mock", {"symbol": _e.id})

ENDEKS_SOZLUGU: dict[str, Endeks] = {e.id: e for e in ENDEKSLER}


def ulkenin_endeksleri(ulke: str) -> list[Endeks]:
    """Ulkenin endeksleri, varsayilan (ana) endeks basta."""
    liste = [e for e in ENDEKSLER if e.ulke == ulke]
    return sorted(liste, key=lambda e: not e.varsayilan)


def ulkenin_varsayilan_endeksi(ulke: str) -> Endeks | None:
    liste = ulkenin_endeksleri(ulke)
    return liste[0] if liste else None


# ------------------------------------------------------------------ Araliklar
# Arayuzdeki "1G, 1H, 1A, 3A, 1Y" secenekleri. `araliklar`: o zaman araligi icin
# IZINLI mum araliklari (ilki varsayilan); `cikti`: saglayicidan istenecek
# azami mum sayisi (kirpma bunun uzerinde yapilir); `pencere_sn`: kirpma
# penceresi (None = son islem gunu); `ttl_sn`: onbellek suresi.
@dataclass(frozen=True)
class Aralik:
    id: str
    etiket: str
    araliklar: tuple[str, ...]
    cikti: dict[str, int]
    pencere_sn: int | None
    ttl_sn: int


_GUN = 24 * 3600

ARALIKLAR: dict[str, Aralik] = {
    # Gun ici: 1-5 dakikalik onbellek.
    "1D": Aralik("1D", "1G", ("5m", "15m"), {"5m": 130, "15m": 50}, None, 120),
    "1W": Aralik("1W", "1H", ("30m", "1h"), {"30m": 130, "1h": 70}, 7 * _GUN, 300),
    # Uzun donem: 10-30 dakikalik onbellek.
    "1M": Aralik("1M", "1A", ("1d",), {"1d": 32}, 31 * _GUN, 900),
    "3M": Aralik("3M", "3A", ("1d",), {"1d": 95}, 92 * _GUN, 1200),
    "1Y": Aralik("1Y", "1Y", ("1d", "1w"), {"1d": 370, "1w": 60}, 366 * _GUN, 1800),
}

# Bozuk/eski kopya: saglayici hata verdiginde son basarili onbellek bu sure
# boyunca "eski veri" olarak gosterilebilir.
ESKI_VERI_SAKLAMA_SN = 7 * _GUN

# Hisse (haber detayi) sembolu: yalniz siki bicimli ABD kodlari; ayrica
# app.py yalnizca depodaki haberlerde gecen kodlara izin verir.
HISSE_KODU_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")
_PARANTEZ_KODU_RE = re.compile(r"\(([A-Z]{1,5}(?:[.\-][A-Z]{1,2})?)\)")


def haber_sembolu(oge: dict) -> str | None:
    """Haberle GUVENILIR bicimde iliskilendirilebilen tek bir hisse kodu
    (yoksa None). Yalnizca Finviz "Pazar Nabzi" haberlerinin kaynak
    ozetindeki ("Sirket Adi (TICKER)") kodlar kullanilir ve ancak tam BIR
    farkli kod varsa dondurulur; belirsizse sembol uydurulmaz."""
    if oge.get("kategori") != "pazar_nabzi":
        return None
    ozet = oge.get("kaynakOzeti") or ""
    if "şirket" not in ozet.lower():
        return None
    kodlar = set(_PARANTEZ_KODU_RE.findall(ozet))
    if len(kodlar) != 1:
        return None
    kod = next(iter(kodlar))
    return kod if HISSE_KODU_RE.match(kod) else None
