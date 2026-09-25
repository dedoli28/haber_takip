"""gemini_arama_ile_metin_iste: Google Arama destekli (grounding) duz metin
istegi - metin/kaynak (groundingChunks) ayristirmasi. Gercek Gemini'ye istek
atmaz; requests.post dogrudan monkeypatch edilir."""

from __future__ import annotations

import pytest

import gemini_client as gc


class SahteYanit:
    def __init__(self, status_code=200, json_veri=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_veri or {}
        self.text = text or str(json_veri)

    def json(self):
        return self._json


def _aday(metin, grounding_chunks=None):
    aday = {"content": {"parts": [{"text": metin}]}}
    if grounding_chunks is not None:
        aday["groundingMetadata"] = {"groundingChunks": grounding_chunks}
    return aday


def test_arama_metin_kaynaksiz_yanit(monkeypatch):
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("Merhaba!")]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert sonuc == {"metin": "Merhaba!", "kaynaklar": []}


def test_arama_metin_grounding_chunklari_ayristirir(monkeypatch):
    chunks = [
        {"web": {"uri": "https://a.com/x", "title": "A Sitesi"}},
        {"web": {"uri": "https://b.com/y", "title": "B Sitesi"}},
    ]
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("Yanıt metni", chunks)]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert sonuc["metin"] == "Yanıt metni"
    assert sonuc["kaynaklar"] == [
        {"baslik": "A Sitesi", "url": "https://a.com/x"},
        {"baslik": "B Sitesi", "url": "https://b.com/y"},
    ]


def test_arama_metin_tekrarlanan_url_tekillestirilir(monkeypatch):
    chunks = [
        {"web": {"uri": "https://a.com/x", "title": "A"}},
        {"web": {"uri": "https://a.com/x", "title": "A (tekrar)"}},
    ]
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("m", chunks)]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert len(sonuc["kaynaklar"]) == 1


def test_arama_metin_baslik_yoksa_url_kullanilir(monkeypatch):
    chunks = [{"web": {"uri": "https://a.com/x"}}]
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("m", chunks)]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert sonuc["kaynaklar"] == [{"baslik": "https://a.com/x", "url": "https://a.com/x"}]


def test_arama_metin_grounding_yoksa_bos_liste(monkeypatch):
    # groundingMetadata alani hic olmayabilir (arama hic yapilmadi) - hata degil.
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("m")]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert sonuc["kaynaklar"] == []


def test_arama_metin_bos_grounding_chunks_bos_liste(monkeypatch):
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(json_veri={"candidates": [_aday("m", [])]}))
    sonuc = gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert sonuc["kaynaklar"] == []


def test_arama_metin_429_sonra_tukenirse_hata(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda *a: None)
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(status_code=429, text="limit"))
    with pytest.raises(RuntimeError, match="429"):
        gc.gemini_arama_ile_metin_iste("prompt", "key", "model", deneme_sayisi=2)


def test_arama_metin_kalici_http_hatasi_hemen_firlar(monkeypatch):
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: SahteYanit(status_code=404, text="bulunamadi"))
    with pytest.raises(RuntimeError, match="404"):
        gc.gemini_arama_ile_metin_iste("prompt", "key", "model")


def test_arama_metin_api_anahtari_hata_metninden_gizlenir(monkeypatch):
    def patlar(*a, **k):
        raise RuntimeError("baglanti hatasi ...key=GIZLI_ANAHTAR...")
    monkeypatch.setattr(gc.requests, "post", patlar)
    with pytest.raises(RuntimeError) as bilgi:
        gc.gemini_arama_ile_metin_iste("prompt", "GIZLI_ANAHTAR", "model")
    assert "GIZLI_ANAHTAR" not in str(bilgi.value)


def test_arama_metin_tools_google_search_gonderilir(monkeypatch):
    yakalanan = {}
    def sahte_post(url, params=None, json=None, timeout=None):
        yakalanan["json"] = json
        return SahteYanit(json_veri={"candidates": [_aday("m")]})
    monkeypatch.setattr(gc.requests, "post", sahte_post)
    gc.gemini_arama_ile_metin_iste("prompt", "key", "model")
    assert yakalanan["json"]["tools"] == [{"google_search": {}}]
    # JSON sema/responseMimeType KULLANILMAMALI (bilinen google_search+schema sorunu)
    assert "responseSchema" not in yakalanan["json"].get("generationConfig", {})
    assert "responseMimeType" not in yakalanan["json"].get("generationConfig", {})
