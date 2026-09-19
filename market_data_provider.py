"""Saglayicidan bagimsiz piyasa veri saglayici katmani.

`PiyasaVeriSaglayici` arayuzunu uygulayan siniflar disaridan (ör. Twelve Data)
mum/anlik fiyat verisi alir ve dahili, saglayicidan bagimsiz bicime cevirir:

    mum   = {"time": unix_sn(UTC), "open": f, "high": f, "low": f, "close": f, "volume": int | None}
    anlik = {"son": f, "onceki_kapanis": f | None, "degisim": f | None,
             "degisim_yuzde": f | None, "zaman": unix_sn | None,
             "piyasa_acik": bool | None, "para_birimi": str | None}

Yapilandirma ortam degiskenleriyle yapilir (asla istemciye/JS'e gonderilmez):
  MARKET_DATA_PROVIDER  "twelvedata" (bos = piyasa verisi kapali)
  MARKET_DATA_API_KEY   saglayici API anahtari (yalnizca sunucuda)

`mock` saglayici YALNIZCA gelistirme/test icindir (MARKET_DATA_ALLOW_MOCK=1
gerekir ve Vercel production ortaminda hicbir kosulda etkinlesmez).

Saglayici hatalari `SaglayiciHatasi` olarak yukselir; `ayrinti` yalnizca
sunucu logu icindir, istemciye gonderilmez.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Mapping

import requests


class SaglayiciHatasi(Exception):
    """kod: rate_limit | timeout | auth | sembol | plan | upstream | yapilandirma"""

    def __init__(self, kod: str, ayrinti: str = ""):
        super().__init__(f"{kod}: {ayrinti}")
        self.kod = kod
        self.ayrinti = ayrinti


class PiyasaVeriSaglayici:
    anahtar = ""  # ENDEKSLER[...].saglayici sozlugundeki anahtar
    ad = ""  # arayuzde gorunen kaynak adi
    mock = False

    def mumlar(self, kod: Mapping[str, str], aralik: str, adet: int) -> list[dict]:
        raise NotImplementedError

    def anlik(self, kod: Mapping[str, str]) -> dict:
        raise NotImplementedError


def _sayi(deger) -> float | None:
    try:
        f = float(deger)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------- Twelve Data
class TwelveDataSaglayici(PiyasaVeriSaglayici):
    """Twelve Data REST API (https://twelvedata.com/docs).

    LISANS UYARISI: bireysel planlar kisisel/dahili/ticari olmayan kullanim
    icindir; ucretsiz plan "internal non-display"dir. Herkese acik bir sitede
    gostermek icin saglayicinin gosterim (display) lisansi gerekir - README'ye
    bakin. API anahtari Authorization basligiyla gonderilir (URL'e/loga
    yazilmaz).
    """

    anahtar = "twelvedata"
    ad = "Twelve Data"
    BASE_URL = "https://api.twelvedata.com"
    _ARALIK = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "1d": "1day", "1w": "1week"}

    def __init__(self, api_key: str, session: requests.Session | None = None, timeout=(3.05, 8), yeniden_deneme: int = 1):
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._yeniden_deneme = yeniden_deneme

    # -- HTTP
    def _istek(self, yol: str, params: dict) -> dict:
        basliklar = {"Authorization": f"apikey {self._api_key}", "Accept": "application/json"}
        son_hata: SaglayiciHatasi | None = None
        yanit = None
        for deneme in range(self._yeniden_deneme + 1):
            try:
                yanit = self._session.get(self.BASE_URL + yol, params=params, headers=basliklar, timeout=self._timeout)
            except requests.Timeout:
                son_hata = SaglayiciHatasi("timeout", f"{yol} zaman asimi")
            except requests.RequestException as e:
                son_hata = SaglayiciHatasi("upstream", f"{yol} baglanti hatasi: {type(e).__name__}")
            else:
                if yanit.status_code >= 500:
                    son_hata = SaglayiciHatasi("upstream", f"{yol} HTTP {yanit.status_code}")
                else:
                    son_hata = None
                    break
            if deneme < self._yeniden_deneme:
                time.sleep(0.3 * (deneme + 1))
        if son_hata is not None or yanit is None:
            raise son_hata or SaglayiciHatasi("upstream", "yanit yok")

        try:
            govde = yanit.json()
        except ValueError:
            govde = None

        # Twelve Data hatalari HTTP 200 + {"status":"error","code":...} olarak da donebilir.
        hata_kodu = None
        mesaj = ""
        if isinstance(govde, dict) and govde.get("status") == "error":
            hata_kodu = int(govde.get("code") or yanit.status_code or 0)
            mesaj = str(govde.get("message") or "")[:200]
        elif yanit.status_code >= 400:
            hata_kodu = yanit.status_code

        if hata_kodu is not None:
            kucuk = mesaj.lower()
            if hata_kodu in (401, 403):
                raise SaglayiciHatasi("auth", f"{yol} {hata_kodu}: {mesaj}")
            if hata_kodu == 429:
                raise SaglayiciHatasi("rate_limit", f"{yol} 429: {mesaj}")
            if hata_kodu == 404:
                raise SaglayiciHatasi("sembol", f"{yol} 404: {mesaj}")
            if hata_kodu == 400:
                if "plan" in kucuk or "upgrade" in kucuk or "subscription" in kucuk:
                    raise SaglayiciHatasi("plan", f"{yol} 400: {mesaj}")
                raise SaglayiciHatasi("sembol", f"{yol} 400: {mesaj}")
            raise SaglayiciHatasi("upstream", f"{yol} {hata_kodu}: {mesaj}")
        if not isinstance(govde, dict):
            raise SaglayiciHatasi("upstream", f"{yol} beklenmeyen govde")
        return govde

    # -- Parcalar
    @staticmethod
    def _zaman(metin: str) -> int | None:
        for bicim in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(metin, bicim).replace(tzinfo=timezone.utc).timestamp())
            except (TypeError, ValueError):
                continue
        return None

    def mumlar(self, kod: Mapping[str, str], aralik: str, adet: int) -> list[dict]:
        if aralik not in self._ARALIK:
            raise SaglayiciHatasi("sembol", f"desteklenmeyen aralik {aralik}")
        params = {
            "symbol": kod["symbol"],
            "interval": self._ARALIK[aralik],
            "outputsize": int(adet),
            "timezone": "UTC",
            "order": "ASC",
        }
        if kod.get("mic_code"):
            params["mic_code"] = kod["mic_code"]
        govde = self._istek("/time_series", params)

        satirlar = govde.get("values")
        if not isinstance(satirlar, list):
            raise SaglayiciHatasi("upstream", "time_series: values yok")

        gorulen: dict[int, dict] = {}
        for s in satirlar:
            if not isinstance(s, dict):
                continue
            t = self._zaman(s.get("datetime"))
            kapanis = _sayi(s.get("close"))
            if t is None or kapanis is None:
                continue
            hacim = _sayi(s.get("volume"))
            gorulen[t] = {
                "time": t,
                "open": _sayi(s.get("open")) if _sayi(s.get("open")) is not None else kapanis,
                "high": _sayi(s.get("high")) if _sayi(s.get("high")) is not None else kapanis,
                "low": _sayi(s.get("low")) if _sayi(s.get("low")) is not None else kapanis,
                "close": kapanis,
                "volume": int(hacim) if hacim is not None else None,
            }
        return [gorulen[t] for t in sorted(gorulen)]

    def anlik(self, kod: Mapping[str, str]) -> dict:
        params = {"symbol": kod["symbol"]}
        if kod.get("mic_code"):
            params["mic_code"] = kod["mic_code"]
        govde = self._istek("/quote", params)

        son = _sayi(govde.get("close"))
        if son is None:
            raise SaglayiciHatasi("upstream", "quote: close yok")
        onceki = _sayi(govde.get("previous_close"))
        degisim = _sayi(govde.get("change"))
        yuzde = _sayi(govde.get("percent_change"))
        if degisim is None and onceki not in (None, 0):
            degisim = son - onceki
        if yuzde is None and onceki not in (None, 0):
            yuzde = (son - onceki) / onceki * 100
        zaman = govde.get("timestamp")
        if not isinstance(zaman, (int, float)):
            zaman = self._zaman(govde.get("datetime"))
        acik = govde.get("is_market_open")
        return {
            "son": son,
            "onceki_kapanis": onceki,
            "degisim": degisim,
            "degisim_yuzde": yuzde,
            "zaman": int(zaman) if zaman else None,
            "piyasa_acik": acik if isinstance(acik, bool) else None,
            "para_birimi": govde.get("currency") if isinstance(govde.get("currency"), str) else None,
        }


# ----------------------------------------------------------------------- Mock
class MockSaglayici(PiyasaVeriSaglayici):
    """YALNIZCA gelistirme/test: deterministik sahte fiyat serisi. Uretimde
    (Vercel production) asla etkinlestirilmez; yanitlar `mock: true` isaretlenir."""

    anahtar = "mock"
    ad = "Test verisi (mock)"
    mock = True
    _ADIM_SN = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400, "1w": 7 * 86400}

    def __init__(self, simdi: datetime | None = None):
        self._simdi = simdi

    def _taban(self, kod: Mapping[str, str]):
        h = int(hashlib.sha256(kod["symbol"].encode()).hexdigest(), 16)
        return 1000 + (h % 19000), random.Random(h)

    def mumlar(self, kod: Mapping[str, str], aralik: str, adet: int) -> list[dict]:
        adim = self._ADIM_SN[aralik]
        simdi = self._simdi or datetime.now(timezone.utc)
        t = int(simdi.timestamp()) // adim * adim
        zamanlar: list[int] = []
        gun_ici = aralik in ("5m", "15m", "30m", "1h")
        while len(zamanlar) < adet:
            d = datetime.fromtimestamp(t, tz=timezone.utc)
            uygun = d.weekday() < 5 and (not gun_ici or 7 <= d.hour < 15) if aralik != "1w" else True
            if uygun:
                zamanlar.append(t)
            t -= adim
        zamanlar.reverse()

        taban, rng = self._taban(kod)
        fiyat = float(taban)
        mumlar = []
        for zaman in zamanlar:
            acilis = fiyat
            kapanis = max(1.0, acilis * (1 + rng.uniform(-0.004, 0.0042)))
            yuksek = max(acilis, kapanis) * (1 + rng.uniform(0, 0.002))
            dusuk = min(acilis, kapanis) * (1 - rng.uniform(0, 0.002))
            mumlar.append(
                {
                    "time": zaman,
                    "open": round(acilis, 2),
                    "high": round(yuksek, 2),
                    "low": round(dusuk, 2),
                    "close": round(kapanis, 2),
                    "volume": rng.randint(50_000, 5_000_000),
                }
            )
            fiyat = kapanis
        return mumlar

    def anlik(self, kod: Mapping[str, str]) -> dict:
        gunluk = self.mumlar(kod, "1d", 3)
        son, onceki = gunluk[-1]["close"], gunluk[-2]["close"]
        return {
            "son": son,
            "onceki_kapanis": onceki,
            "degisim": round(son - onceki, 2),
            "degisim_yuzde": round((son - onceki) / onceki * 100, 2),
            "zaman": gunluk[-1]["time"],
            "piyasa_acik": None,
            "para_birimi": None,
        }


# --------------------------------------------------------------------- Fabrika
def saglayici_al(env: Mapping[str, str] | None = None) -> tuple[PiyasaVeriSaglayici | None, str]:
    """(saglayici | None, durum). durum: ok | yapilandirilmadi | anahtar_yok |
    bilinmeyen_saglayici | mock_yasak. Eksik yapilandirma hicbir zaman
    exception firlatmaz; cagiran anlasilir bos durum gosterir."""
    env = os.environ if env is None else env
    ad = (env.get("MARKET_DATA_PROVIDER") or "").strip().lower()
    if not ad:
        return None, "yapilandirilmadi"
    if ad == "mock":
        izinli = (env.get("MARKET_DATA_ALLOW_MOCK") or "").strip().lower() in ("1", "true", "yes")
        if not izinli or (env.get("VERCEL_ENV") or "").strip().lower() == "production":
            return None, "mock_yasak"
        return MockSaglayici(), "ok"
    if ad == "twelvedata":
        anahtar = (env.get("MARKET_DATA_API_KEY") or "").strip()
        if not anahtar:
            return None, "anahtar_yok"
        return TwelveDataSaglayici(anahtar), "ok"
    return None, "bilinmeyen_saglayici"
