# Haber Takip Platformu (Web / Vercel Sürümü)

Finviz haberlerini çeken, Gemini API ile borsa önemine göre sınıflandırıp
Türkçe özetleyen web uygulaması. `borsa` klasöründeki masaüstü uygulamasının
Vercel'de yayınlanabilen web sürümü.

## Mimari

- `app.py` — FastAPI backend. İki uç nokta:
  - `GET /api/haberler?gun=bugun|dun` — Finviz'den haberleri çeker (Gemini
    kullanmaz, sadece kazıma/ayrıştırma).
  - `POST /api/siniflandir` — Gönderilen bir grup haberi (`items`, `apiKey`,
    `model`) Gemini ile sınıflandırıp Türkçe özetler.
- `public/` — statik arayüz (index.html, style.css, app.js). Aynı `app.py`
  üzerinden servis edilir.
- `gemini_client.py`, `finviz_scraper.py` — backend yardımcı modülleri.

**Gemini API anahtarı hiçbir zaman sunucuda saklanmaz.** Kullanıcı anahtarı
kendi tarayıcısında (`localStorage`) saklar; her sınıflandırma isteğinde
tarayıcıdan sunucuya, sunucudan da doğrudan Google'a iletilir.

## Yerelde Çalıştırma

```bash
pip install -r requirements.txt fastapi uvicorn
python -m uvicorn app:app --reload
```

Tarayıcıda `http://127.0.0.1:8000` adresini aç.

## Vercel'e Yayınlama

### Yöntem 1 — Vercel CLI (en hızlı)

```bash
npm install -g vercel
cd borsa-web
vercel login
vercel --prod
```

CLI klasörü otomatik algılar (kök dizindeki `app.py` + `requirements.txt`),
sorulara varsayılan cevaplarla geçebilirsin. Birkaç dakika içinde bir
`https://....vercel.app` adresi verecek.

### Yöntem 2 — GitHub üzerinden (Vercel dashboard)

1. Bu klasörü bir GitHub reposuna push'la.
2. vercel.com üzerinde "Add New Project" → reponu seç.
3. Vercel Python'u otomatik algılar, "Deploy" de.
4. Her `git push`'ta otomatik yeniden yayınlanır.

### Sonrasında

Yayınlanan adrese giren herkes kendi Gemini API anahtarını Ayarlar'dan
girip kullanabilir — anahtarlar birbirine karışmaz, her tarayıcı kendi
anahtarını saklar.

## Notlar

- Finviz'in ücretsiz haber akışı yalnızca bugünü ve dünün bir kısmını sağlar.
- Gemini ücretsiz kotası hesap/model bazlı günlük sınırlıdır; yoğun kullanımda
  429 hatası alınabilir (arayüzde açıkça gösterilir).
