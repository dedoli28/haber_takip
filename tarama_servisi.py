"""Temel carpan/oran taramasi: onbellekleme, filtreleme ve Gemini analizi.

Finviz'den periyodik (cron ile) cekilen veri Redis'te evren basina saklanir:
  htp:tarama:v1:<evren>        {"hisseler": [...], "guncellendi": iso, "hatalar": [...]}
  htp:tarama:v1:<evren>:kilit  cron es zamanli calismasini onler (SET NX EX)

Filtreleme TAMAMEN sunucu tarafinda, onbellekteki TAM veri kumesi uzerinde
yapilir (Finviz'in kendi araligi filtrelerine gitmeden): bu, Finviz'de dogrudan
olmayan turetilmis alanlarda (EV/EBIT, FCF Yield) da filtrelemeyi mumkun kilar
ve kullaniciya istedigi herhangi bir araligi secme serbestligi verir.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import finviz_tarama
import redis_store

log = logging.getLogger("tarama")

ANAHTAR_ONEKI = "htp:tarama:v1"
SAKLAMA_SN = 3 * 24 * 3600  # cron bir donemi kacirsa bile eski veri "guncelleme zamani" ile gosterilmeye devam eder
KILIT_SN = 55  # bir tarama cagrisinin en kotu ihtimalle surebilecegi sure (Vercel 60sn siniri)

EVRENLER = {
    "sp500": "S&P 500",
    "nasdaq100": "NASDAQ 100",
}
VARSAYILAN_EVREN = "sp500"

# Kullaniciya sunulan 6 carpan/oran filtresi: (alan, etiket, birim, varsayilan_min, varsayilan_max).
FILTRE_TANIMLARI = [
    {"alan": "forward_pe", "etiket": "Forward P/E", "birim": "", "varsayilan_min": 10, "varsayilan_max": 20},
    {"alan": "peg", "etiket": "PEG", "birim": "", "varsayilan_min": 0, "varsayilan_max": 1},
    {"alan": "p_fcf", "etiket": "P/FCF", "birim": "", "varsayilan_min": 10, "varsayilan_max": 20},
    {"alan": "ev_ebitda", "etiket": "EV/EBITDA", "birim": "", "varsayilan_min": 8, "varsayilan_max": 15},
    {"alan": "ev_ebit", "etiket": "EV/EBIT", "birim": "", "varsayilan_min": 10, "varsayilan_max": 18, "yaklasik": True},
    {"alan": "fcf_yield", "etiket": "FCF Yield", "birim": "%", "varsayilan_min": 5, "varsayilan_max": 10},
]
FILTRE_ALANLARI = {f["alan"] for f in FILTRE_TANIMLARI}


class TaramaHatasi(Exception):
    def __init__(self, kod: str, ayrinti: str = ""):
        super().__init__(f"{kod}: {ayrinti}")
        self.kod = kod


def _anahtar(evren: str) -> str:
    return f"{ANAHTAR_ONEKI}:{evren}"


def _kilit_anahtari(evren: str) -> str:
    return f"{ANAHTAR_ONEKI}:{evren}:kilit"


def evreni_dogrula(evren: str | None) -> str:
    evren = (evren or VARSAYILAN_EVREN).strip().lower()
    if evren not in EVRENLER:
        raise TaramaHatasi("gecersiz_evren", evren)
    return evren


def _kayit_oku(evren: str) -> dict | None:
    ham = redis_store.onbellek_oku(_anahtar(evren))
    if not ham:
        return None
    try:
        kayit = json.loads(ham)
    except (ValueError, TypeError):
        return None
    return kayit if isinstance(kayit, dict) else None


def yenile(evren: str = VARSAYILAN_EVREN, *, kaynak: str = "cron") -> dict:
    """Finviz'den evrenin tum verisini ceker ve onbellege yazar. Ayni evren
    icin es zamanli ikinci cagri Redis kilidiyle engellenir (Gemini'yi degil
    Finviz'i korur ama ayni desen: tek seferde tek tarama). 'sonuc':
    hazir|atlandi|hata doner."""
    evren = evreni_dogrula(evren)
    if not redis_store.kilit_al(_kilit_anahtari(evren), KILIT_SN):
        return {"sonuc": "atlandi", "evren": evren}

    try:
        try:
            hisseler, hatalar = finviz_tarama.tarama_verisi_cek(evren)
        except Exception as e:  # noqa: BLE001
            log.warning("tarama basarisiz (%s): %s", evren, type(e).__name__)
            return {"sonuc": "hata", "evren": evren, "hata": type(e).__name__}
        if not hisseler:
            return {"sonuc": "hata", "evren": evren, "hata": "veri_yok"}

        kayit = {
            "hisseler": hisseler,
            "guncellendi": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hatalar": hatalar,
            "kaynak": kaynak,
        }
        redis_store.onbellek_yaz(_anahtar(evren), json.dumps(kayit, ensure_ascii=False), SAKLAMA_SN)
        return {"sonuc": "hazir", "evren": evren, "hisseSayisi": len(hisseler), "hatalar": hatalar}
    finally:
        redis_store.kilit_birak(_kilit_anahtari(evren))


def _sayisal(deger, varsayilan=None):
    try:
        return float(deger)
    except (TypeError, ValueError):
        return varsayilan


def filtrele(hisseler: list[dict], filtreler: dict, sektorler: set[str] | None = None,
             ulkeler: set[str] | None = None, arama: str = "") -> list[dict]:
    """Onbellekteki tam listeyi verilen kriterlere gore daraltir. `filtreler`:
    {alan: (min, max)} - min/max None ise o uc sinirsizdir. Alani eksik/None
    olan hisseler (Finviz'de veri yoksa, ör. kar etmeyen sirketlerde P/E)
    o filtre icin ELENIR - "bilinmiyor" degeri araliga dahil sayilmaz."""
    sonuc = hisseler
    for alan, (min_v, max_v) in filtreler.items():
        if alan not in FILTRE_ALANLARI or (min_v is None and max_v is None):
            continue
        def uyuyor(h, alan=alan, min_v=min_v, max_v=max_v):
            v = h.get(alan)
            if v is None:
                return False
            if min_v is not None and v < min_v:
                return False
            if max_v is not None and v > max_v:
                return False
            return True
        sonuc = [h for h in sonuc if uyuyor(h)]

    if sektorler:
        sonuc = [h for h in sonuc if h.get("sektor") in sektorler]
    if ulkeler:
        sonuc = [h for h in sonuc if h.get("ulke") in ulkeler]
    if arama:
        q = arama.strip().lower()
        sonuc = [h for h in sonuc if q in (h.get("ticker") or "").lower() or q in (h.get("sirket") or "").lower()]
    return sonuc


def oku(evren: str = VARSAYILAN_EVREN, filtreler: dict | None = None, sektorler: set[str] | None = None,
        ulkeler: set[str] | None = None, arama: str = "", sirala: str | None = None, ters: bool = False) -> dict:
    """Onbellekteki veriyi (varsa) filtreleyip dondurur. Onbellek hic
    doldurulmamissa 'durum': 'missing' ile bos liste doner (hata degil -
    ilk cron calisana kadar beklenen bir durum)."""
    evren = evreni_dogrula(evren)
    kayit = _kayit_oku(evren)
    if kayit is None:
        return {"ok": True, "evren": evren, "durum": "missing", "guncellendi": None, "hisseler": [],
                "toplamHisseSayisi": 0, "filtrelenmisSayisi": 0}

    hisseler = filtrele(kayit.get("hisseler", []), filtreler or {}, sektorler, ulkeler, arama)
    if sirala and (hisseler and sirala in hisseler[0]):
        hisseler = sorted(hisseler, key=lambda h: (h.get(sirala) is None, h.get(sirala)), reverse=ters)

    return {
        "ok": True,
        "evren": evren,
        "durum": "ready",
        "guncellendi": kayit.get("guncellendi"),
        "hisseler": hisseler,
        "toplamHisseSayisi": len(kayit.get("hisseler", [])),
        "filtrelenmisSayisi": len(hisseler),
    }


def mevcut_sektorler_ulkeler(evren: str = VARSAYILAN_EVREN) -> tuple[list[str], list[str]]:
    """Onbellekteki veride GERCEKTEN gorulen sektor/ulke degerlerini (alfabetik)
    dondurur - kullanilmayan onlarca ulkeyi gostermemek icin sabit global
    liste yerine bu tercih edilir."""
    kayit = _kayit_oku(evreni_dogrula(evren))
    if kayit is None:
        return [], []
    hisseler = kayit.get("hisseler", [])
    sektorler = sorted({h["sektor"] for h in hisseler if h.get("sektor")})
    ulkeler = sorted({h["ulke"] for h in hisseler if h.get("ulke")})
    return sektorler, ulkeler
