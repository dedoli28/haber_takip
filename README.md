# Haber Takip Platformu (Web / Vercel Sürümü)

ABD, Türkiye, Almanya ve Çin finans haberlerini çeken, Gemini API ile borsa
önemine göre sınıflandırıp Türkçe özetleyen, önemli haberleri e-postayla
bildiren web uygulaması. FastAPI backend + `static_ui/` altında saf
HTML/CSS/JavaScript arayüz; kalıcı veri Upstash Redis'te tutulur.

## Haber kaynakları

Her haberin bir `ulke` alanı vardır (`US`, `TR`, `DE`, `CN`). Arayüzde
Haberler ve Kaydedilenler bölümlerinde ülkeye göre (çoklu) filtrelenebilir.
`ulke` alanı olmayan eski kayıtlar ABD sayılır.

| Ülke | Kaynak | Yöntem |
|------|--------|--------|
| ABD (`US`) | [Finviz](https://finviz.com/news.ashx) — Piyasa, Hisse, ETF, Kripto, Pazar Nabzı haberleri + Bloglar | HTML kazıma |
| Türkiye (`TR`) | Investing.com BIST — `https://tr.investing.com/rss/news_1067.rss` | RSS |
| Türkiye (`TR`) | Sözcü Borsa — `https://www.sozcu.com.tr/feeds-rss-category-borsa` | RSS |
| Almanya (`DE`) | wallstreet:online — `https://www.wallstreet-online.de/rss/nachrichten` | RSS |
| Almanya (`DE`) | Tagesschau Wirtschaft — `https://www.tagesschau.de/wirtschaft/index~rss2.xml` | RSS |
| Çin (`CN`) | Google News araması: `China economy OR China stocks OR "Shanghai Composite"` (son 2 gün) | RSS |

Tüm kaynaklar paralel (`ThreadPoolExecutor`) çekilir; bir kaynak hata verirse
diğerleri çalışmaya devam eder. Kaynak başına en fazla 30 güncel haber alınır
(RSS 2.0, RSS 1.0 ve Atom desteklenir; açıklamalardaki HTML etiketleri
temizlenir, tarihler UTC'ye çevrilir). Haberler URL'e göre tekilleştirilir.
Yeni bir RSS kaynağı eklemek için `finviz_scraper.py` içindeki
`RSS_KAYNAKLARI` listesine bir satır eklemek yeterlidir.

Bir kaynak geçici olarak hata verdiğinde o kaynağın daha önce kaydedilmiş
haberleri silinmez (yeniden sınıflandırılıp Gemini kotasını harcamasın diye).

## Mimari

- `app.py` — FastAPI backend.
- `finviz_scraper.py` — Finviz kazıyıcısı + RSS/Atom ayrıştırıcı.
- `gemini_client.py` — Gemini istemcisi ve promptlar.
- `redis_store.py` — Upstash Redis (REST) katmanı: haber deposu, ayarlar,
  sayaçlar, hazır gün özeti, istek sınırlama.
- `email_client.py` — Gmail SMTP ile e-posta gönderimi.
- `static_ui/` — arayüz (`index.html`, `style.css`, `app.js`); aynı `app.py`
  üzerinden servis edilir.

Tarama üç bağımsız adımdır ve her biri kendi cron'undan tetiklenir:

1. `POST /api/haber-cek` — tüm kaynakları çeker (Gemini kullanmaz), yeni
   haberleri sınıflandırma kuyruğuna ekler.
2. `POST /api/haber-siniflandir` — kuyruktan bir grup haberi (ülkeler/kategoriler
   arasında dengeli seçerek) Gemini ile sınıflandırır, Türkçeye çevirir,
   özetler ve depoya yazar; eşik e-postalarını tetikler.
3. `POST /api/gun-ozeti-olustur` — günün özetini Gemini ile **bir kez** üretip
   Redis'e kaydeder (aşağıya bakın).

`POST /api/tara` ilk ikisini sırayla çağıran bir kısayoldur (manuel test için).

### Gün özeti ve Gemini kotası

"Günü Özetle" butonu **Gemini'yi çağırmaz**; `POST /api/gun-ozeti` yalnızca
Redis'teki son hazır özeti döndürür (hazır özet yoksa `503`). Yani buton her
tıklamada Gemini kotası tüketmez.

Özeti üreten `POST /api/gun-ozeti-olustur` cron ile **günde 1–4 kez**
çağrılmalıdır (ör. 09:00, 13:00, 18:00, 23:00). Üretimde Gemini'ye yalnızca
**bugünün en yeni haberleri** gönderilir: kategori başına en fazla 8 haber,
ülkeler arasında dengeli (round-robin) seçilir; "önemsiz" haberler dahil
edilmez. İstek tek deneme ve ~25 sn zaman aşımıyla yapılır (Vercel'in 60 sn'lik
fonksiyon sınırını aşmamak için). Sonuç `kategoriler`, `genelOzet`,
`olusturulmaZamani` ve `haberSayisi` alanlarını içerir; arayüz hazırlanma
zamanını modalın altında gösterir.

`POST /api/gun-sonu` (gün sonu e-postası) önce Redis'te **bugün** üretilmiş
hazır özeti kullanır; yoksa bir kez üretip Redis'e kaydeder.

## Ortam değişkenleri

| Değişken | Zorunlu | Açıklama |
|----------|---------|----------|
| `GEMINI_API_KEY` | evet | Google Gemini API anahtarı ([aistudio.google.com](https://aistudio.google.com/apikey)). Yalnızca sunucuda tutulur, istemciye gönderilmez. |
| `GEMINI_MODEL` | hayır | Kullanılacak model. Varsayılan: `gemini-flash-lite-latest`. |
| `UPSTASH_REDIS_REST_URL` | evet | Upstash Redis REST URL'i. |
| `UPSTASH_REDIS_REST_TOKEN` | evet | Upstash Redis REST token'ı. |
| `POLL_SECRET` | evet | Cron/yönetim uç noktalarının gizli anahtarı. **Tanımlı değilse bu uç noktalar hiç çalışmaz (401).** Uzun ve rastgele bir değer seçin. |
| `ALLOWED_ORIGINS` | hayır | CORS için ek izinli originler, virgülle ayrılmış (ör. `https://ornek.com,https://baska.com`). |
| `GMAIL_ADDRESS` | e-posta için | Bildirimleri gönderecek Gmail adresi. |
| `GMAIL_APP_PASSWORD` | e-posta için | Google hesabında 2 adımlı doğrulama açıldıktan sonra "Uygulama Şifreleri"nden oluşturulan 16 haneli şifre (normal Gmail şifreniz değildir). |

Gizli değerleri (API anahtarı, token, secret) asla koda veya repoya yazmayın;
Vercel'de *Project → Settings → Environment Variables* bölümünden girin.

## Cron kurulumu (cron-job.org gibi)

Korumalı uç noktalar `POST` ile ve **`X-Poll-Secret`** başlığıyla çağrılır.
Secret URL'ye (sorgu parametresi olarak) **yazılmaz**: URL'ler sunucu/proxy
loglarına düşebilir, bu yüzden sorgu parametresi desteklenmez.

```bash
curl -X POST "https://haber-takip-nper.vercel.app/api/haber-cek" \
     -H "X-Poll-Secret: $POLL_SECRET"
```

cron-job.org'da: *Advanced → Headers* bölümüne `X-Poll-Secret` başlığını ekleyin,
istek yöntemini `POST` seçin.

| Uç nokta | Önerilen sıklık | Ne yapar |
|----------|-----------------|----------|
| `/api/haber-cek` | 15 dk'da bir | Kaynakları tarar, kuyruğa ekler. |
| `/api/haber-siniflandir` | 15 dk'da bir | Kuyruktan bir grubu sınıflandırır. |
| `/api/gun-ozeti-olustur` | günde 1–4 kez | Gün özetini üretip Redis'e kaydeder. |
| `/api/gun-sonu` | günde bir (ör. 23:45) | Günün özeti + bildirilmemiş haberler e-postası. |
| `/api/sabah-ozeti` | her sabah 06:00 (İstanbul) | Gece biriken "çok önemli" haberleri e-postayla gönderir. |

`X-Poll-Secret` ile korunan diğer uç noktalar: `/api/tara`, `/api/depo-sifirla`,
`/api/depo-tekillestir`, `/api/basarisiz-siniflandirmalari-temizle`,
`/api/sentetik-haber`, `/api/sentetik-haber-temizle`.

## Güvenlik

- **Cron/yönetim uç noktaları:** `POLL_SECRET` tanımlı değilse veya başlık
  yanlışsa `401` döner; karşılaştırma sabit zamanlıdır.
- **CORS:** yalnızca `https://haber-takip-nper.vercel.app`,
  `http://127.0.0.1:8000`, `http://localhost:8000` ve `ALLOWED_ORIGINS` ile
  eklenenlere izin verilir. İzinli başlıklar: `Content-Type`, `X-Poll-Secret`.
- **`/api/analiz`:** herkese açık olduğu için IP başına 10 dakikada en fazla 5
  istekle sınırlıdır (Upstash Redis `INCR` + `EXPIRE`; IP, Redis anahtarında
  SHA-256 ile hashlenmiş olarak tutulur). Aşılırsa `429` döner.
- `/api/ayarlar` (bildirim e-postaları) ve `/api/durum` şu an herhangi bir
  gizli anahtar istemez.
- E-posta gövdesindeki başlık/özet/URL değerleri HTML-kaçışlanır; arayüz
  sunucu metinlerini `textContent` ile gösterir.

## Yerelde çalıştırma

```bash
pip install -r requirements.txt uvicorn
export GEMINI_API_KEY=... UPSTASH_REDIS_REST_URL=... UPSTASH_REDIS_REST_TOKEN=... POLL_SECRET=...
python -m uvicorn app:app --reload
```

Tarayıcıda `http://127.0.0.1:8000` adresini aç.

## Vercel'e yayınlama

1. Repoyu GitHub'a push'la.
2. vercel.com üzerinde "Add New Project" → reponu seç; Vercel kökteki `app.py` +
   `requirements.txt`'i Python olarak otomatik algılar (`vercel.json`: fonksiyon
   süresi 60 sn).
3. Yukarıdaki ortam değişkenlerini ekle, "Deploy" de.
4. Her `git push`'ta otomatik yeniden yayınlanır.
5. cron-job.org'da yukarıdaki cron'ları kur.

## Notlar

- Finviz'in ücretsiz haber akışı yalnızca bugünü ve dünün bir kısmını sağlar;
  RSS akışları kaynağa göre daha eski haberler de içerebilir.
- Gemini ücretsiz kotası hesap/model bazlı günlük sınırlıdır; yoğun kullanımda
  429 hatası alınabilir (arayüzde açıkça gösterilir). Sınıflandırma kuyruğu bu
  yüzden her çağrıda tek bir grubu (10 haber) işler.
