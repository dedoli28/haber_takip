"""Piyasa veri servisi: parametre dogrulama, onbellek, tek-ucus ve hiz siniri.

Katmanlar:
  1. Dogrulama: yalnizca market_symbols.py'deki endeksler (ve haber
     dogrulayicisindan gecen hisse kodlari) disariya sorulur.
  2. Onbellek: surec ici bellek + Redis (Upstash). Redis yoksa/erisilemezse
     yalnizca bellek kullanilir; servis calismaya devam eder.
  3. Tek-ucus (single-flight): ayni anahtar icin es zamanli istekler tek bir
     saglayici cagrisina indirgenir (surec ici kilit + Redis SET NX kilidi).
  4. Hiz siniri: dakikada azami MARKET_DATA_MAX_CALLS_PER_MIN saglayici
     cagrisi (varsayilan 6); asilirsa varsa eski onbellek gosterilir.
  5. Saglayici hatasinda varsa SON BASARILI onbellek "eski veri" olarak doner.

Sahte veri yalnizca `mock` saglayicida ve yalnizca gelistirmede uretilir; bu
modul hicbir yerde kendiliginden sahte fiyat uretmez.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import market_symbols as ms
import redis_store
from market_data_provider import PiyasaVeriSaglayici, SaglayiciHatasi, saglayici_al

log = logging.getLogger("market")

ANAHTAR_ONEKI = "htp:market:v1"
KILIT_SN = 20
KILIT_BEKLEME_SN = 4.0
KILIT_YOKLAMA_SN = 0.4
ANLIK_TTL_SN = 90
_SEMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")


class PiyasaHatasi(Exception):
    """Istemciye gosterilebilir, GENEL mesajli hata (saglayici ayrintisi icermez)."""

    def __init__(self, kod: str, mesaj: str, http: int = 503, retry_after: int | None = None):
        super().__init__(mesaj)
        self.kod = kod
        self.mesaj = mesaj
        self.http = http
        self.retry_after = retry_after


_HATA_ESLEME = {
    "rate_limit": ("rate_limit", "Piyasa verisi sağlayıcısının istek sınırına ulaşıldı; biraz sonra tekrar deneyin.", 429),
    "timeout": ("timeout", "Piyasa verisi sağlayıcısı zamanında yanıt vermedi.", 504),
    "sembol": ("veri_yok", "Bu sembol için veri bulunamadı.", 404),
}
_GENEL = ("saglayici_hatasi", "Piyasa verisi şu anda alınamıyor.", 503)


def _saglayici_hatasini_cevir(e: SaglayiciHatasi) -> PiyasaHatasi:
    kod, mesaj, http = _HATA_ESLEME.get(e.kod, _GENEL)
    return PiyasaHatasi(kod, mesaj, http, retry_after=60 if e.kod == "rate_limit" else None)


# ------------------------------------------------------------------ Yardimcilar
def _simdi() -> float:
    return time.time()


def _iso(epoch: float | int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


_saglayici_onbellek: dict[tuple, PiyasaVeriSaglayici | None] = {}


def _saglayici() -> tuple[PiyasaVeriSaglayici | None, str]:
    """Saglayiciyi env'den kurar; ayni yapilandirma icin ornegi yeniden kullanir."""
    anahtar = tuple(
        os.environ.get(k, "")
        for k in ("MARKET_DATA_PROVIDER", "MARKET_DATA_API_KEY", "MARKET_DATA_ALLOW_MOCK", "VERCEL_ENV")
    )
    onceki = _saglayici_onbellek.get(anahtar)
    if onceki is not None:
        return onceki, "ok"
    saglayici, durum = saglayici_al()
    if saglayici is not None:
        _saglayici_onbellek.clear()
        _saglayici_onbellek[anahtar] = saglayici
    return saglayici, durum


# --------------------------------------------------------------------- Onbellek
_bellek: dict[str, dict] = {}
_bellek_kilidi = threading.Lock()
_anahtar_kilitleri: dict[str, threading.Lock] = {}
_butce_sayaci: dict[int, int] = {}


def sifirla() -> None:
    """Testler icin: tum surec ici durumu temizler."""
    with _bellek_kilidi:
        _bellek.clear()
        _anahtar_kilitleri.clear()
        _butce_sayaci.clear()
        _saglayici_onbellek.clear()


def _yerel_kilit(anahtar: str) -> threading.Lock:
    with _bellek_kilidi:
        return _anahtar_kilitleri.setdefault(anahtar, threading.Lock())


def _oku(anahtar: str) -> dict | None:
    """Once bellekten, yoksa Redis'ten kaydi okur (Redis hatalari yutulur)."""
    with _bellek_kilidi:
        kayit = _bellek.get(anahtar)
    if kayit is not None:
        return kayit
    try:
        ham = redis_store.onbellek_oku(anahtar)
    except Exception:  # noqa: BLE001 - Redis yok/erisilemez: bellekle devam
        return None
    if not ham:
        return None
    try:
        kayit = json.loads(ham)
    except ValueError:
        return None
    if not isinstance(kayit, dict) or "veri" not in kayit or "alindi" not in kayit:
        return None
    with _bellek_kilidi:
        _bellek[anahtar] = kayit
    return kayit


def _yaz(anahtar: str, kayit: dict) -> None:
    with _bellek_kilidi:
        _bellek[anahtar] = kayit
    try:
        redis_store.onbellek_yaz(anahtar, json.dumps(kayit, ensure_ascii=False), ms.ESKI_VERI_SAKLAMA_SN)
    except Exception as e:  # noqa: BLE001
        log.warning("piyasa onbellegi Redis'e yazilamadi: %s", type(e).__name__)


def _taze_mi(kayit: dict | None, ttl_sn: int) -> bool:
    return kayit is not None and (_simdi() - float(kayit["alindi"])) < ttl_sn


def _azami_cagri_dk() -> int:
    try:
        return max(1, int(os.environ.get("MARKET_DATA_MAX_CALLS_PER_MIN", "6")))
    except ValueError:
        return 6


def _butce_asildi_mi() -> bool:
    """Dakikalik saglayici cagri butcesi. Once dagitik (Redis) sayac; Redis
    yoksa surec ici sayac."""
    dakika = int(_simdi() // 60)
    azami = _azami_cagri_dk()
    try:
        return redis_store.istek_siniri_asildi_mi(f"htp:market:butce:{dakika}", azami, 120)
    except Exception:  # noqa: BLE001
        with _bellek_kilidi:
            for eski in [d for d in _butce_sayaci if d < dakika]:
                del _butce_sayaci[eski]
            _butce_sayaci[dakika] = _butce_sayaci.get(dakika, 0) + 1
            return _butce_sayaci[dakika] > azami


def _redis_kilidi_al(anahtar: str) -> bool | None:
    """True: kilit alindi; False: baska ornek yeniliyor; None: Redis yok."""
    try:
        return redis_store.kilit_al(f"{anahtar}:kilit", KILIT_SN)
    except Exception:  # noqa: BLE001
        return None


def _redis_kilidi_birak(anahtar: str) -> None:
    try:
        redis_store.kilit_birak(f"{anahtar}:kilit")
    except Exception:  # noqa: BLE001
        pass


def _getir_veya_yenile(anahtar: str, ttl_sn: int, uret: Callable[[], dict]) -> tuple[dict, bool, int]:
    """(veri, eski_mi, kalan_ttl_sn). Taze onbellek varsa saglayiciyi cagirmaz;
    ayni anahtar icin es zamanli istekleri tek cagriya indirger; saglayici
    basarisiz olursa (ya da butce asilirsa) varsa eski kaydi doner."""
    kayit = _oku(anahtar)
    if _taze_mi(kayit, ttl_sn):
        return kayit["veri"], False, max(0, int(ttl_sn - (_simdi() - float(kayit["alindi"]))))

    with _yerel_kilit(anahtar):
        kayit = _oku(anahtar)  # baska thread yenilemis olabilir
        if _taze_mi(kayit, ttl_sn):
            return kayit["veri"], False, max(0, int(ttl_sn - (_simdi() - float(kayit["alindi"]))))

        redis_kilidi = _redis_kilidi_al(anahtar)
        if redis_kilidi is False:
            # Baska bir sunucu ornegi bu anahtari yeniliyor: kisaca bekleyip onun sonucunu kullan.
            bitis = _simdi() + KILIT_BEKLEME_SN
            while _simdi() < bitis:
                time.sleep(KILIT_YOKLAMA_SN)
                with _bellek_kilidi:
                    _bellek.pop(anahtar, None)  # Redis'teki yeni kaydi gorebilmek icin
                yeni = _oku(anahtar)
                if _taze_mi(yeni, ttl_sn):
                    return yeni["veri"], False, max(0, int(ttl_sn - (_simdi() - float(yeni["alindi"]))))
            if kayit is not None:
                return kayit["veri"], True, 0

        try:
            if _butce_asildi_mi():
                raise SaglayiciHatasi("rate_limit", "yerel dakikalik cagri butcesi asildi")
            veri = uret()
        except SaglayiciHatasi as e:
            log.warning("piyasa saglayici hatasi: %s", e)
            if kayit is not None:
                return kayit["veri"], True, 0
            raise _saglayici_hatasini_cevir(e) from e
        finally:
            if redis_kilidi:
                _redis_kilidi_birak(anahtar)

        _yaz(anahtar, {"alindi": _simdi(), "veri": veri})
        return veri, False, ttl_sn


# ------------------------------------------------------------------- Dogrulama
def _saglayiciyi_hazirla() -> PiyasaVeriSaglayici:
    saglayici, durum = _saglayici()
    if saglayici is None:
        raise PiyasaHatasi("yapilandirilmadi", "Piyasa verisi henüz yapılandırılmadı.", 503)
    return saglayici


def _ulke_dogrula(ulke: str) -> str:
    ulke = (ulke or "").strip().upper()
    if ulke not in ms.ULKELER:
        raise PiyasaHatasi("gecersiz_parametre", "Geçersiz ülke.", 400)
    return ulke


def _sembol_coz(sembol: str, saglayici: PiyasaVeriSaglayici, hisse_dogrulayici: Callable[[str], bool] | None):
    """(sembol_kimligi, gorunen_ad, para_birimi, saglayici_kodu). Yalnizca izinli
    endeksler ya da dogrulayicidan gecen hisse kodlari kabul edilir."""
    sembol = (sembol or "").strip().upper()
    if not _SEMBOL_RE.match(sembol):
        raise PiyasaHatasi("gecersiz_parametre", "Geçersiz sembol.", 400)
    endeks = ms.ENDEKS_SOZLUGU.get(sembol)
    if endeks is not None:
        kod = endeks.saglayici.get(saglayici.anahtar)
        if kod is None:
            raise PiyasaHatasi("veri_yok", "Bu sembol için veri bulunamadı.", 404)
        return endeks.id, endeks.ad, endeks.para_birimi, kod
    if ms.HISSE_KODU_RE.match(sembol) and hisse_dogrulayici is not None and hisse_dogrulayici(sembol):
        return sembol, sembol, None, {"symbol": sembol}
    raise PiyasaHatasi("gecersiz_parametre", "Bu sembol için veri sunulmuyor.", 400)


# ----------------------------------------------------------------------- Kirpma
def _kirp(mumlar: list[dict], aralik: ms.Aralik) -> list[dict]:
    if not mumlar:
        return []
    son = mumlar[-1]["time"]
    if aralik.pencere_sn is None:  # 1G: son islem gununun (UTC takvim gunu) mumlari
        gun = datetime.fromtimestamp(son, tz=timezone.utc).date()
        return [m for m in mumlar if datetime.fromtimestamp(m["time"], tz=timezone.utc).date() == gun]
    return [m for m in mumlar if m["time"] >= son - aralik.pencere_sn]


# ------------------------------------------------------------------ Genel arayuz
def yapilandirma() -> dict:
    """Saglayiciya HIC baglanmadan sekmeleri/araliklari tarif eder."""
    saglayici, durum = _saglayici()
    return {
        "ok": True,
        "yapilandirildi": saglayici is not None,
        "durum": durum,
        "mock": bool(saglayici and saglayici.mock),
        "kaynak": saglayici.ad if saglayici else None,
        "ulkeler": [
            {
                "kod": kod,
                "ad": ad,
                "endeksler": [
                    {"id": e.id, "ad": e.ad, "para_birimi": e.para_birimi, "varsayilan": e.varsayilan}
                    for e in ms.ulkenin_endeksleri(kod)
                ],
            }
            for kod, ad in ms.ULKELER.items()
        ],
        "araliklar": [
            {"id": a.id, "etiket": a.etiket, "mum_araliklari": list(a.araliklar), "varsayilan_mum_araligi": a.araliklar[0]}
            for a in ms.ARALIKLAR.values()
        ],
    }


def genel_bakis(ulke: str) -> tuple[dict, int]:
    """(yanit, onbellek_sn). Ulkenin endekslerinin son degeri/degisimi."""
    ulke = _ulke_dogrula(ulke)
    saglayici = _saglayiciyi_hazirla()

    sonuclar = []
    herhangi_eski = False
    son_hata: PiyasaHatasi | None = None
    kalan_ttl = ANLIK_TTL_SN
    for endeks in ms.ulkenin_endeksleri(ulke):
        kod = endeks.saglayici.get(saglayici.anahtar)
        girdi = {"symbol": endeks.id, "name": endeks.ad, "currency": endeks.para_birimi, "default": endeks.varsayilan}
        if kod is None:
            sonuclar.append({**girdi, "available": False})
            continue
        anahtar = f"{ANAHTAR_ONEKI}:q:{endeks.id}:{saglayici.anahtar}"
        try:
            veri, eski, ttl = _getir_veya_yenile(anahtar, ANLIK_TTL_SN, lambda kod=kod: saglayici.anlik(kod))
        except PiyasaHatasi as e:
            son_hata = e
            sonuclar.append({**girdi, "available": False})
            continue
        herhangi_eski = herhangi_eski or eski
        kalan_ttl = min(kalan_ttl, ttl)
        sonuclar.append(
            {
                **girdi,
                "available": True,
                "last": veri["son"],
                "change": veri.get("degisim"),
                "change_pct": veri.get("degisim_yuzde"),
                "previous_close": veri.get("onceki_kapanis"),
                "updated_at": _iso(veri.get("zaman")),
                "market_open": veri.get("piyasa_acik"),
                "stale": eski,
            }
        )

    if not any(s["available"] for s in sonuclar):
        raise son_hata or PiyasaHatasi("veri_yok", "Bu ülke için veri bulunamadı.", 404)

    ana = ms.ulkenin_varsayilan_endeksi(ulke)
    yanit = {
        "ok": True,
        "country": ulke,
        "name": ms.ULKELER[ulke],
        "default_symbol": ana.id if ana else None,
        "indices": sonuclar,
        "stale": herhangi_eski,
        "mock": saglayici.mock,
        "source": saglayici.ad,
    }
    return yanit, 0 if herhangi_eski else kalan_ttl


def gecmis(sembol: str, aralik: str, interval: str | None = None, hisse_dogrulayici: Callable[[str], bool] | None = None) -> tuple[dict, int]:
    """(yanit, onbellek_sn). Mum serisi; yapisi sozlesmeye uygundur (points)."""
    aralik_kimligi = (aralik or "").strip().upper()
    a = ms.ARALIKLAR.get(aralik_kimligi)
    if a is None:
        raise PiyasaHatasi("gecersiz_parametre", "Geçersiz zaman aralığı.", 400)
    mum = (interval or a.araliklar[0]).strip().lower()
    if mum not in a.araliklar:
        raise PiyasaHatasi("gecersiz_parametre", "Bu zaman aralığı için geçersiz mum aralığı.", 400)

    saglayici = _saglayiciyi_hazirla()
    kimlik, ad, para, kod = _sembol_coz(sembol, saglayici, hisse_dogrulayici)

    def uret() -> dict:
        ham = saglayici.mumlar(kod, mum, a.cikti[mum])
        mumlar = _kirp(ham, a)
        if not mumlar:
            raise SaglayiciHatasi("sembol", "bos seri")
        return {"points": mumlar}

    anahtar = f"{ANAHTAR_ONEKI}:h:{kimlik}:{a.id}:{mum}:{saglayici.anahtar}"
    veri, eski, kalan = _getir_veya_yenile(anahtar, a.ttl_sn, uret)

    kayit = _oku(anahtar)
    noktalar = veri["points"]
    yanit = {
        "ok": True,
        "symbol": kimlik,
        "name": ad,
        "currency": para,
        "range": a.id,
        "interval": mum,
        "updated_at": _iso(float(kayit["alindi"])) if kayit else None,
        "last_bar_at": _iso(noktalar[-1]["time"]),
        "points": noktalar,
        "stale": eski,
        "mock": saglayici.mock,
        "source": saglayici.ad,
    }
    return yanit, 0 if eski else kalan
