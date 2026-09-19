# Haber Takip Platformu (Web / Vercel Sürümü)

Finviz ve RSS kaynaklarından ABD, Türkiye, Almanya ve Çin finans haberlerini
çeken; Gemini API ile önemine göre sınıflandırıp Türkçe özetleyen web uygulaması.

## Mimari

- `app.py` — FastAPI backend. Haber çekme, sınıflandırma, bildirim ve önbellekli
  gün özeti uç noktalarını sunar.
- `static_ui/` — statik arayüz (index.html, style.css, app.js). Aynı `app.py`
  üzerinden servis edilir.
- `gemini_client.py`, `finviz_scraper.py` — backend yardımcı modülleri.

Gemini API anahtarı yalnızca Vercel ortam değişkeninde tutulur; HTML veya
JavaScript içine yazılmaz ve tarayıcıya gönderilmez.

## Haber kaynakları

- ABD: Finviz haber, hisse, ETF, kripto, pazar nabzı ve blog akışları
- Türkiye: Investing.com BIST RSS ve Sözcü Borsa RSS
- Almanya: wallstreet:online ve Tagesschau Wirtschaft RSS
- Çin: Google News ekonomi/borsa RSS araması

RSS kaynakları kaynak başına en fazla 30 güncel kayıt döndürür. Cron aralığını
10 dakikanın altına indirmemek önerilir.

## Ortam değişkenleri

- `GEMINI_API_KEY` — sunucu tarafındaki Gemini anahtarı
- `GEMINI_MODEL` — isteğe bağlı, varsayılan `gemini-flash-lite-latest`
- `UPSTASH_REDIS_REST_URL` ve `UPSTASH_REDIS_REST_TOKEN`
- `POLL_SECRET` — zorunlu cron/yönetim sırrı
- `SETTINGS_SECRET` — isteğe bağlı ayrı ayarlar sırrı; yoksa `POLL_SECRET` kullanılır
- `ALLOWED_ORIGINS` — isteğe bağlı, virgülle ayrılmış CORS origin listesi
- E-posta için `GMAIL_ADDRESS` ve `GMAIL_APP_PASSWORD`

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

## Zamanlanmış çağrılar

Cron servisi korumalı uç noktalara `X-Poll-Secret: <POLL_SECRET>` başlığıyla
istek göndermelidir. Sırrı URL query parametresine koymayın; URL'ler loglara
yazılabilir.

- `POST /api/haber-cek` — kaynakları tarar
- `POST /api/haber-siniflandir` — kuyruktan bir grubu Gemini ile işler
- `POST /api/gun-ozeti-olustur` — özet üretip Redis'e kaydeder; günde 1–4 kez yeterlidir
- `POST /api/sabah-ozeti` ve `POST /api/gun-sonu` — e-posta görevleri

Arayüzdeki “Günü Özetle” butonu Gemini'yi doğrudan çağırmaz; Redis'teki son
hazır özeti okur. Böylece her tıklamada kota tüketilmez ve Vercel timeout riski
önemli ölçüde azalır.

## Notlar

- Gemini analiz uç noktası IP başına 10 dakikada 5 istekle sınırlandırılmıştır.
- `POLL_SECRET` tanımlı değilse cron/yönetim uç noktaları kapalı kalır.
- Ayarlar ve bildirim e-posta listesi yönetim sırrı olmadan okunamaz/değiştirilemez.
