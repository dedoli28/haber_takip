"""AI sohbet sekmesi icin baglam (grounding) olusturur: gunun ozeti (varsa),
en onemli/guncel haberlerin kisa listesi ve kullanicinin son mesajinda gecen
olasi hisse kodlarinin en son tarama (screener) verisi. Hepsi ZATEN
onbellekte olan kaynaklardan okunur; bu modul yeni bir Finviz/Gemini cagrisi
TETIKLEMEZ (yalnizca redis_store/gun_ozeti_servisi/tarama_servisi'nin
onbellek okuma fonksiyonlarini kullanir)."""

from __future__ import annotations

import re

import gun_ozeti_servisi
import redis_store
import tarama_servisi

AZAMI_HABER = 12
AZAMI_HISSE = 5
_ONEM_SIRA = {"cok_onemli": 0, "onemli": 1, "bakmaya_deger": 2, "onemsiz": 3}
_TICKER_ADAYI_DESENI = re.compile(r"[A-Za-z]{1,5}")


def _guncel_haberler() -> list[dict]:
    try:
        depo = redis_store.depo_yukle()
    except Exception:  # noqa: BLE001
        return []
    ogeler = sorted(depo.values(), key=lambda o: o.get("ilkGorulme", ""), reverse=True)
    ogeler = sorted(ogeler, key=lambda o: _ONEM_SIRA.get(o.get("sinif"), 9))
    return ogeler[:AZAMI_HABER]


def _bahsedilen_hisseler(mesaj: str) -> list[dict]:
    """Mesajda gecen 1-5 harfli kelimeleri (buyuk/kucuk harf farketmez) olasi
    ticker sayar ve S&P 500 / NASDAQ 100 tarama onbelleginde arar. Gercek bir
    hisse eslesmedigi surece hicbir sey eklenmez (yanlis pozitif zararsizdir:
    Turkce bir kelime tesadufen bir ticker'a esit olsa bile yalnizca o hisse
    GERCEKTEN mevcut evrende varsa baglama girer)."""
    adaylar = {t.upper() for t in _TICKER_ADAYI_DESENI.findall(mesaj)}
    if not adaylar:
        return []
    bulunanlar: list[dict] = []
    gorulen: set[str] = set()
    for evren in ("sp500", "nasdaq100"):
        try:
            veri = tarama_servisi.oku(evren)
        except Exception:  # noqa: BLE001
            continue
        for h in veri.get("hisseler", []):
            ticker = h.get("ticker")
            if ticker in adaylar and ticker not in gorulen:
                gorulen.add(ticker)
                bulunanlar.append(h)
                if len(bulunanlar) >= AZAMI_HISSE:
                    return bulunanlar
    return bulunanlar


def _haber_satiri(h: dict) -> str:
    baslik = h.get("baslikTr") or h.get("baslik") or ""
    ozet = (h.get("ai_ozet") or "").strip()
    if len(ozet) > 140:
        ozet = ozet[:140].rstrip() + "…"
    saat = h.get("saat") or ""
    parcalar = [p for p in (saat, baslik) if p]
    satir = " ".join(parcalar) if not saat else f"[{saat}] {baslik}"
    return f"- {satir}: {ozet}" if ozet else f"- {satir}"


def _hisse_satiri(h: dict) -> str:
    return (
        f"- {h.get('ticker')} ({h.get('sirket', '')}, {h.get('sektor', '')}): "
        f"Fiyat {h.get('fiyat')} $ (günlük değişim {h.get('degisim_yuzde')}%), "
        f"Forward P/E {h.get('forward_pe')}, PEG {h.get('peg')}, P/FCF {h.get('p_fcf')}, "
        f"EV/EBITDA {h.get('ev_ebitda')}, EV/EBIT~ {h.get('ev_ebit')}, FCF Yield %{h.get('fcf_yield')}"
    )


def baglam_olustur(mesaj: str) -> str:
    """Sohbet promptuna eklenecek, sunucuda ZATEN onbellekte olan platform
    verisinin kisa Turkce ozeti. Hicbir veri yoksa bunu acikca belirtir
    (boylece model 'veri yok' demek yerine uydurmaz)."""
    parcalar: list[str] = []

    try:
        ozet = gun_ozeti_servisi.bugunun_hazir_ozeti()
    except Exception:  # noqa: BLE001
        ozet = None
    if ozet and ozet.get("genelOzet"):
        parcalar.append(f"Bugünün genel piyasa özeti: {ozet['genelOzet']}")

    haberler = _guncel_haberler()
    if haberler:
        parcalar.append("Güncel/önemli haberler:\n" + "\n".join(_haber_satiri(h) for h in haberler))

    hisseler = _bahsedilen_hisseler(mesaj)
    if hisseler:
        parcalar.append(
            "Mesajda geçen ve platformun tarama (screener) verisinde bulunan hisseler:\n"
            + "\n".join(_hisse_satiri(h) for h in hisseler)
        )

    if not parcalar:
        return "(Şu anda platformda gösterilecek güncel haber/tarama verisi yok.)"
    return "\n\n".join(parcalar)
