"""AI sohbet sekmesi icin baglam (grounding) olusturur: gunun ozeti (varsa),
en onemli/guncel haberlerin kisa listesi ve kullanicinin son mesajinda gecen
olasi hisse kodlarinin en son tarama (screener) verisi. Hepsi ZATEN
onbellekte olan kaynaklardan okunur; bu modul yeni bir Finviz/Gemini cagrisi
TETIKLEMEZ (yalnizca redis_store/gun_ozeti_servisi/tarama_servisi'nin
onbellek okuma fonksiyonlarini kullanir)."""

from __future__ import annotations

import concurrent.futures
import re

import gun_ozeti_servisi
import redis_store
import tarama_servisi

AZAMI_HABER = 12
AZAMI_HISSE = 5
_ONEM_SIRA = {"cok_onemli": 0, "onemli": 1, "bakmaya_deger": 2, "onemsiz": 3}
# BUYUK harfle yazilmis 2-5 harfli kelimeler (ör. "AAPL nasil?"). Kucuk/karisik
# harfli her kelimeyi eslesen eski desen ([A-Za-z]{1,5}) neredeyse HER mesajda
# tetikleniyordu (Turkce cumlelerdeki sıradan kelimeler bile eslesiyordu),
# bu da her sohbet mesajinda gereksiz 2 ekstra Redis okumasina (sp500 +
# nasdaq100) yol aciyor, toplam gecikmeyi artirip zaman asimi riskini
# yukseltiyordu. Kullanicinin GERCEKTEN BUYUK harfle yazdigi kisa kelimeler
# (borsa ticker'lari yazilirken dogal olarak buyuk harf kullanilir) cok daha
# az yanlis pozitif uretir.
_TICKER_ADAYI_DESENI = re.compile(r"\b[A-Z]{2,5}\b")


def _guncel_haberler() -> list[dict]:
    try:
        depo = redis_store.depo_yukle()
    except Exception:  # noqa: BLE001
        return []
    ogeler = sorted(depo.values(), key=lambda o: o.get("ilkGorulme", ""), reverse=True)
    ogeler = sorted(ogeler, key=lambda o: _ONEM_SIRA.get(o.get("sinif"), 9))
    return ogeler[:AZAMI_HABER]


def _evren_hisseleri_guvenli(evren: str) -> list[dict]:
    try:
        return tarama_servisi.oku(evren).get("hisseler", [])
    except Exception:  # noqa: BLE001
        return []


def _hisseleri_esle(adaylar: set[str], evren_sonuclari: list[list[dict]]) -> list[dict]:
    bulunanlar: list[dict] = []
    gorulen: set[str] = set()
    for hisseler in evren_sonuclari:
        for h in hisseler:
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


def _gun_ozeti_guvenli() -> dict | None:
    try:
        return gun_ozeti_servisi.bugunun_hazir_ozeti()
    except Exception:  # noqa: BLE001
        return None


def baglam_olustur(mesaj: str) -> str:
    """Sohbet promptuna eklenecek, sunucuda ZATEN onbellekte olan platform
    verisinin kisa Turkce ozeti. Hicbir veri yoksa bunu acikca belirtir
    (boylece model 'veri yok' demek yerine uydurmaz).

    Kaynaklar (gun ozeti + haberler + evren basina tarama) birbirinden
    BAGIMSIZ Redis okumalaridir; sirayla cagirmak gecikmeleri toplardi (ve
    Vercel'in fonksiyon zaman asimina yaklastirirdi). Paralel calistirilarak
    toplam gecikme, TEK EN YAVAS cagriya indirilir."""
    adaylar = set(_TICKER_ADAYI_DESENI.findall(mesaj))
    evrenler = ("sp500", "nasdaq100") if adaylar else ()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2 + len(evrenler)) as executor:
        gelecek_ozet = executor.submit(_gun_ozeti_guvenli)
        gelecek_haberler = executor.submit(_guncel_haberler)
        gelecek_hisseler = [executor.submit(_evren_hisseleri_guvenli, e) for e in evrenler]
        ozet = gelecek_ozet.result()
        haberler = gelecek_haberler.result()
        hisseler = _hisseleri_esle(adaylar, [g.result() for g in gelecek_hisseler]) if adaylar else []

    parcalar: list[str] = []
    if ozet and ozet.get("genelOzet"):
        parcalar.append(f"Bugünün genel piyasa özeti: {ozet['genelOzet']}")
    if haberler:
        parcalar.append("Güncel/önemli haberler:\n" + "\n".join(_haber_satiri(h) for h in haberler))
    if hisseler:
        parcalar.append(
            "Mesajda geçen ve platformun tarama (screener) verisinde bulunan hisseler:\n"
            + "\n".join(_hisse_satiri(h) for h in hisseler)
        )

    if not parcalar:
        return "(Şu anda platformda gösterilecek güncel haber/tarama verisi yok.)"
    return "\n\n".join(parcalar)
