"""Gunun ozeti: tarih anahtarli kalici kayit, durumlar, kilit ve yenileme kurallari.

Saklama (Upstash Redis, 14 gun TTL), tarih = Europe/Istanbul takvim gunu:
  htp:gun_ozeti:v2:<YYYY-MM-DD>        o gunun kaydi (asagida)
  htp:gun_ozeti:v2:son                 son BASARILI (hazir) ozetin tam kaydi; bugun
                                       henuz ozet yokken dunku ozeti gosterebilmek icin
  htp:gun_ozeti:v2:kilit:<YYYY-MM-DD>  uretim kilidi (SET NX EX): ayni anda tek Gemini istegi
  htp:gun_ozeti:v2:bekleme             kullanici kaynakli yenilemeler arasi bekleme

Kayit: {tarih, durum: ready|generating|failed, kategoriler, genelOzet,
        olusturulmaZamani, haberSayisi, uretimBaslangic, sonHataZamani,
        hataKodu, kaynak, uretimSayisi}. Bir onceki basarili icerik, yeniden
uretim sirasinda ya da basarisiz denemeden sonra da KORUNUR (arayuz eski
ozeti gostermeye devam eder); `durum` her zaman SON denemenin durumudur.

Zamanlama: ozet Istanbul saatiyle GUN_OZETI_SAATLERI'nde (10:00, 14:00, 18:00)
uretilir. Vercel Cron UTC calisir (vercel.json: 07:00/11:00/15:00 UTC) ve
Hobby planda dakika hassasiyeti yoktur (saat basi +59 dk); bu yuzden bir
"yenileme gerekli" kurali ve kontrollu kullanici kaynakli yenileme de vardir.
Hata metinleri istemciye gitmez, yalnizca kisa `hataKodu` saklanir.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import redis_store

log = logging.getLogger("gun_ozeti")

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
GUN_OZETI_SAATLERI = (10, 14, 18)  # Europe/Istanbul

ANAHTAR = "htp:gun_ozeti:v2"
SON_ANAHTAR = f"{ANAHTAR}:son"
BEKLEME_ANAHTAR = f"{ANAHTAR}:bekleme"
SAKLAMA_SN = 14 * 24 * 3600
KILIT_SN = 90  # Gemini ~25 sn + Redis; Vercel fonksiyon siniri 60 sn
URETIM_ZAMAN_ASIMI_SN = 120  # 'generating' bundan uzun surerse takili sayilir
CRON_MIN_ARALIK_SN = 60 * 60  # cron kopya teslimi / kullanici yenilemesiyle cakisma korumasi
YENILEME_BEKLEME_SN = 15 * 60  # kullanici kaynakli iki uretim denemesi arasi


class OzetHatasi(Exception):
    """Istemciye gitmeyen, kisa `kod` tasiyan uretim hatasi."""

    def __init__(self, kod: str, ayrinti: str = ""):
        super().__init__(f"{kod}: {ayrinti}")
        self.kod = kod


def hata_kodu(e: Exception) -> str:
    if isinstance(e, OzetHatasi):
        return e.kod
    m = str(e).lower()
    if "günlük kota" in m or "perday" in m:
        return "gemini_kota"
    if "429" in m or "istek limitine" in m:
        return "gemini_limit"
    if "timed out" in m or "timeout" in m:
        return "gemini_zaman_asimi"
    if any(k in m for k in ("503", "502", "500", "504", "yoğun", "erişilemez")):
        return "gemini_yogun"
    return "bilinmeyen"


# ------------------------------------------------------------------ zaman
def _simdi() -> datetime:
    """Tek saat kaynagi (testlerde degistirilebilir)."""
    return datetime.now(timezone.utc)


def simdi_istanbul(simdi: datetime | None = None) -> datetime:
    return (simdi or _simdi()).astimezone(ISTANBUL_TZ)


def bugun(simdi: datetime | None = None) -> str:
    return simdi_istanbul(simdi).date().isoformat()


def _slot(simdi_ist: datetime, saat: int) -> datetime:
    return simdi_ist.replace(hour=saat, minute=0, second=0, microsecond=0)


def son_gecen_slot(simdi: datetime | None = None) -> datetime | None:
    """Bugun icinde gecmis en son planli saat (yoksa None: ilk slottan once)."""
    s = simdi_istanbul(simdi)
    gecenler = [_slot(s, h) for h in GUN_OZETI_SAATLERI if _slot(s, h) <= s]
    return max(gecenler) if gecenler else None


def sonraki_slot_metni(simdi: datetime | None = None) -> str:
    s = simdi_istanbul(simdi)
    for h in GUN_OZETI_SAATLERI:
        if _slot(s, h) > s:
            return f"{h:02d}:00"
    return f"yarın {GUN_OZETI_SAATLERI[0]:02d}:00"


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ depo
def _anahtar(tarih: str) -> str:
    return f"{ANAHTAR}:{tarih}"


def _kilit_anahtari(tarih: str) -> str:
    return f"{ANAHTAR}:kilit:{tarih}"


def _json(ham) -> dict | None:
    if not ham:
        return None
    try:
        v = json.loads(ham)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def _kaydet(tarih: str, kayit: dict) -> None:
    redis_store.onbellek_yaz(_anahtar(tarih), json.dumps(kayit, ensure_ascii=False), SAKLAMA_SN)


def _toplu_oku(tarih: str) -> tuple[dict | None, dict | None, int]:
    """(bugunun kaydi, son basarili kayit, yenileme bekleme suresi sn) - tek istek."""
    yanit = redis_store.komut_calistir(
        [["GET", _anahtar(tarih)], ["GET", SON_ANAHTAR], ["TTL", BEKLEME_ANAHTAR]]
    )
    ttl = yanit[2].get("result")
    return _json(yanit[0].get("result")), _json(yanit[1].get("result")), int(ttl) if isinstance(ttl, int) and ttl > 0 else 0


def _icerik_var_mi(kayit: dict | None) -> bool:
    return bool(kayit and (kayit.get("genelOzet") or kayit.get("kategoriler")))


def _uretim_takili_mi(kayit: dict | None, simdi: datetime) -> bool:
    if not kayit or kayit.get("durum") != "generating":
        return False
    b = _parse(kayit.get("uretimBaslangic"))
    return b is None or (simdi - b).total_seconds() > URETIM_ZAMAN_ASIMI_SN


def guncel_mi(kayit: dict | None, simdi: datetime | None = None) -> bool:
    """Bugunun icerigi son gecen planli saatten SONRA uretilmis mi? (Ilk
    slottan once bugun icin ozet beklenmez.)"""
    simdi = simdi or _simdi()
    slot = son_gecen_slot(simdi)
    if slot is None:
        return True
    if not _icerik_var_mi(kayit) or kayit.get("tarih") != bugun(simdi):
        return False
    uretim = _parse(kayit.get("olusturulmaZamani"))
    return bool(uretim and uretim >= slot)


# ------------------------------------------------------------------ okuma
def _durum(kayit: dict | None, simdi: datetime) -> str:
    """Bugunun kaydinin durumu; takili kalmis 'generating' -> 'failed'."""
    if kayit is None:
        return "missing"
    if _uretim_takili_mi(kayit, simdi):
        return "failed"
    return kayit.get("durum", "missing")


def oku(simdi: datetime | None = None, uretilebilir: bool = True) -> dict:
    """Arayuzun kullandigi tek yanit. Redis erisilemezse RuntimeError firlatir."""
    simdi = simdi or _simdi()
    tarih = bugun(simdi)
    kayit, son, bekleme = _toplu_oku(tarih)
    return _yanit(kayit, son, bekleme, simdi, uretilebilir)


def _yanit(kayit: dict | None, son: dict | None, bekleme: int, simdi: datetime, uretilebilir: bool) -> dict:
    tarih = bugun(simdi)
    durum = _durum(kayit, simdi)
    bugun_icerik = kayit if _icerik_var_mi(kayit) else None

    if bugun_icerik is not None:
        icerik, gecmis = bugun_icerik, False
    elif _icerik_var_mi(son):
        icerik, gecmis = son, son.get("tarih") != tarih
    else:
        icerik, gecmis = None, False

    guncel = guncel_mi(bugun_icerik, simdi)
    yenileme_gerekli = (
        uretilebilir and son_gecen_slot(simdi) is not None and not guncel and durum != "generating" and bekleme == 0
    )
    icerik = icerik or {}
    return {
        "ok": True,
        "tarih": tarih,
        "durum": durum,
        "gosterilenTarih": icerik.get("tarih"),
        "gecmisGun": gecmis,
        "kategoriler": icerik.get("kategoriler", []),
        "genelOzet": icerik.get("genelOzet", ""),
        "olusturulmaZamani": icerik.get("olusturulmaZamani"),
        "haberSayisi": icerik.get("haberSayisi"),
        "guncelMi": guncel,
        "yenilemeGerekli": yenileme_gerekli,
        "sonrakiDenemeSn": bekleme or None,
        "planliSaatler": [f"{h:02d}:00" for h in GUN_OZETI_SAATLERI],
        "sonrakiGuncelleme": sonraki_slot_metni(simdi),
        "sonHataZamani": (kayit or {}).get("sonHataZamani"),
        "hataKodu": "zaman_asimi" if (kayit and _uretim_takili_mi(kayit, simdi)) else (kayit or {}).get("hataKodu"),
    }


def bugunun_hazir_ozeti(simdi: datetime | None = None) -> dict | None:
    """Bugune ait, icerigi olan kayit (gun sonu e-postasi icin); yoksa None."""
    simdi = simdi or _simdi()
    kayit, _, _ = _toplu_oku(bugun(simdi))
    return kayit if _icerik_var_mi(kayit) else None


# ------------------------------------------------------------------ uretim
def _kilit_birak(tarih: str) -> None:
    try:
        redis_store.kilit_birak(_kilit_anahtari(tarih))
    except Exception:  # noqa: BLE001
        pass


def uret(
    uretici: Callable[[], dict],
    *,
    kaynak: str,
    simdi: datetime | None = None,
    min_aralik_sn: int = 0,
) -> dict:
    """Ozeti uretip bugunun kaydina yazar. `uretici()` {kategoriler, genelOzet,
    haberSayisi} dondurur ya da OzetHatasi/Exception firlatir.

    Donen sozluk: {"sonuc": "ready"|"failed"|"generating"|"atlandi", "kayit": ...,
    "hataKodu": ...}. Es zamanli ikinci istek 'generating' ile geri doner;
    min_aralik_sn icinde uretilmis ozet varsa 'atlandi' (cron kopya teslimi)."""
    enjekte = simdi is not None  # testlerde sabit saat; uretimde olusturulma zamani uretim SONRASI alinir
    simdi = simdi or _simdi()
    tarih = bugun(simdi)
    mevcut, _, _ = _toplu_oku(tarih)

    if min_aralik_sn and _icerik_var_mi(mevcut):
        onceki = _parse(mevcut.get("olusturulmaZamani"))
        if onceki and (simdi - onceki).total_seconds() < min_aralik_sn:
            return {"sonuc": "atlandi", "kayit": mevcut, "hataKodu": None}

    if not redis_store.kilit_al(_kilit_anahtari(tarih), KILIT_SN):
        return {"sonuc": "generating", "kayit": mevcut, "hataKodu": None}

    onceki_icerik = {
        k: mevcut[k] for k in ("kategoriler", "genelOzet", "olusturulmaZamani", "haberSayisi") if mevcut and k in mevcut
    }
    uretim_sayisi = int((mevcut or {}).get("uretimSayisi", 0))
    try:
        _kaydet(tarih, {
            **(mevcut or {}), **onceki_icerik, "tarih": tarih, "durum": "generating",
            "uretimBaslangic": simdi.isoformat(timespec="seconds"), "kaynak": kaynak,
        })
        try:
            ozet = uretici()
        except Exception as e:  # noqa: BLE001
            kod = hata_kodu(e)
            log.warning("gun ozeti uretilemedi (%s): %s", kod, type(e).__name__)
            kayit = {
                **onceki_icerik, "tarih": tarih, "durum": "failed", "kaynak": kaynak,
                "sonHataZamani": simdi.isoformat(timespec="seconds"), "hataKodu": kod,
                "uretimSayisi": uretim_sayisi,
            }
            _kaydet(tarih, kayit)
            return {"sonuc": "failed", "kayit": kayit, "hataKodu": kod}

        kayit = {
            "tarih": tarih, "durum": "ready", "kategoriler": ozet.get("kategoriler", []),
            "genelOzet": ozet.get("genelOzet", ""), "haberSayisi": ozet.get("haberSayisi"),
            "olusturulmaZamani": (simdi if enjekte else _simdi()).isoformat(timespec="seconds"),
            "kaynak": kaynak, "sonHataZamani": None, "hataKodu": None, "uretimSayisi": uretim_sayisi + 1,
        }
        _kaydet(tarih, kayit)
        redis_store.onbellek_yaz(SON_ANAHTAR, json.dumps(kayit, ensure_ascii=False), SAKLAMA_SN)
        return {"sonuc": "ready", "kayit": kayit, "hataKodu": None}
    finally:
        _kilit_birak(tarih)


def kullanici_yenilemesi(uretici: Callable[[], dict], *, uretilebilir: bool, simdi: datetime | None = None) -> dict:
    """Arayuzden gelen KONTROLLU yenileme: yalnizca ozet bayatsa (planli saat gectigi
    halde ondan sonra uretilmis ozet yoksa), uretim devam etmiyorsa ve son
    kullanici kaynakli denemeden en az YENILEME_BEKLEME_SN gectiyse Gemini'ye
    gider. Yoksa neden bilgisiyle geri doner (Gemini cagrilmaz)."""
    enjekte = simdi is not None
    simdi = simdi or _simdi()
    kayit, son, bekleme = _toplu_oku(bugun(simdi))
    durum = _yanit(kayit, son, bekleme, simdi, uretilebilir)

    if not uretilebilir:
        return {"sonuc": "uretilemez", "durum": durum}
    if durum["durum"] == "generating":
        return {"sonuc": "generating", "durum": durum}
    if durum["guncelMi"]:
        return {"sonuc": "guncel", "durum": durum}
    if bekleme or not redis_store.kilit_al(BEKLEME_ANAHTAR, YENILEME_BEKLEME_SN):
        return {"sonuc": "beklemede", "durum": durum}

    sonuc = uret(uretici, kaynak="kullanici", simdi=simdi if enjekte else None)
    return {"sonuc": sonuc["sonuc"], "durum": oku(simdi, uretilebilir)}
