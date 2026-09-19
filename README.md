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

## Piyasa Görünümü ve Haber Nabzı

Haberler sekmesinde, araç çubuğu ile kartlar arasında başlangıçta kompakt duran,
genişletilebilir bir **Piyasa Görünümü** vardır: Türkiye / ABD / Almanya / Çin
sekmeleri, ülkenin ana endeksi (BIST 100, S&P 500 + NASDAQ, DAX, Shanghai
Composite), son değer, değişim, 1G-1H-1A-3A-1Y aralıkları, çizgi/mum grafiği,
tooltip (açılış/en yüksek/en düşük/kapanış/hacim), yakınlaştırma-kaydırma-sıfırlama
ve tablo görünümü. İkinci sekme **Haber Nabzı** son 24 saatin haber sayılarını
(saatlik, ülke, tür, önem) gösterir. Haber detayında haberle *güvenilir* biçimde
eşleşen bir hisse kodu (yalnızca Pazar Nabzı haberlerinde tek bir "(TICKER)"
varsa) ya da haberin ülkesinin ana endeksi, haberin zamanını gösteren dikey
çizgiyle çizilir; hiçbiri yoksa bölüm hiç oluşturulmaz.

- **Sağlayıcı katmanı:** `market_data_provider.py` (sağlayıcıdan bağımsız arayüz +
  Twelve Data + yalnızca-geliştirme `mock`), `market_data_service.py` (doğrulama,
  önbellek, tek-uçuş, hız sınırı), `market_symbols.py` (**tek yapılandırma dosyası**:
  izinli endeksler, sağlayıcı sembol/kod eşlemeleri, aralıklar, TTL'ler). Arayüz
  sembol/sağlayıcı bilmez; `GET /api/market/config`'ten okur.
- **Güvenlik:** tarayıcı sağlayıcıyı asla doğrudan çağırmaz; API anahtarı yalnızca
  sunucu ortam değişkenindedir ve Authorization başlığıyla gönderilir (URL'e/loga
  yazılmaz). Yalnızca `market_symbols.py`'deki endeksler ve haberlerde geçen
  hisse kodları kabul edilir; başka semboller sağlayıcıya gönderilmez. Hata
  yanıtları genel mesaj taşır, sağlayıcı ayrıntısı sızmaz.
- **Önbellek (Redis + bellek):** gün içi 2-5 dk, uzun dönem 15-30 dk; sağlayıcı hata
  verirse son başarılı kayıt "eski veri" olarak gösterilir; aynı anahtar için
  eş zamanlı istekler tek sağlayıcı çağrısına indirgenir; dakikalık çağrı
  bütçesi (`MARKET_DATA_MAX_CALLS_PER_MIN`) uygulanır. Sahte fiyat **asla**
  üretimde gösterilmez; veri yoksa "Piyasa verisi şu anda alınamıyor" durumu çıkar.
- **Uç noktalar:** `GET /api/market/config`, `/api/market/overview?country=TR`,
  `/api/market/history?symbol=XU100&range=1D[&interval=5m]`,
  `/api/news/statistics?range=24h`. `/api/haberler` yanıtındaki bazı haberlere
  `iliskiliSembol` alanı eklenir (depoya yazılmaz, anlık türetilir).
- **Redis anahtarları (yeni):** `htp:market:v1:h:<sembol>:<aralık>:<mum>:<sağlayıcı>`
  (mum serisi), `htp:market:v1:q:<sembol>:<sağlayıcı>` (anlık değer), her birinin
  `:kilit` eşi (20 sn SET NX EX) ve `htp:market:butce:<dakika>` (INCR+EXPIRE).
  Mevcut anahtarlar ve haber veri modeli **değişmedi**.
- **Grafik kütüphanesi:** Apache ECharts 5.6 (`static_ui/vendor/`, Apache-2.0,
  LICENSE/NOTICE ile birlikte), yalnızca grafik gerektiğinde yüklenir.

### Sağlayıcı seçimi, lisans ve kotalar (ÖNEMLİ)

Kod bugün **Twelve Data** ile çalışacak şekilde yazılmıştır (Authorization
başlığı, `time_series` + `quote`). Twelve Data'nın herkese açık kataloğunda
`XU100` (BIST 100), `GDAXI` (DAX) ve `000001` (SSE Composite) listelenir;
**S&P 500 (`SPX`) ve NASDAQ (`IXIC`) herkese açık katalogda görünmez** — bu
kapsam planınıza bağlı olabilir, anahtarı aldıktan sonra doğrulayın (kapsam
dışıysa arayüz o endeks için "veri yok" durumunu gösterir).

**Lisans:** Twelve Data bireysel planları "kişisel, dahili ve ticari olmayan"
kullanım içindir; ücretsiz Basic plan *internal non-display* kullanımdır (800
kredi/gün, 8 kredi/dk). Bu uygulama **herkese açık** bir web sitesi olduğu için
verileri ziyaretçilere göstermek için sağlayıcının **gösterim (display) lisansı**
gereken bir plan (iş/business lisansı) gerekir; sağlayıcının güncel kullanım
şartlarını ve önbellekleme/yeniden dağıtım kurallarını satın almadan önce
sağlayıcıdan yazılı teyit edin. Bu proje bunu sizin yerinize kararlaştırmaz ve
API anahtarı içermez. Başka bir sağlayıcı için `PiyasaVeriSaglayici`
arayüzünü uygulayıp `saglayici_al()`'a eklemek ve `market_symbols.py`'ye
o sağlayıcının sembol eşlemesini yazmak yeterlidir.

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
| `MARKET_DATA_PROVIDER` | piyasa grafikleri için | `twelvedata`. Boşsa piyasa görünümü "yapılandırılmadı" gösterir (haberler etkilenmez). |
| `MARKET_DATA_API_KEY` | piyasa grafikleri için | Sağlayıcı API anahtarı; yalnızca sunucuda okunur, istemciye asla gönderilmez. |
| `MARKET_DATA_MAX_CALLS_PER_MIN` | hayır | Dakikada azami sağlayıcı çağrısı (varsayılan 6). |
| `MARKET_DATA_ALLOW_MOCK` | hayır | YALNIZCA geliştirme: `MARKET_DATA_PROVIDER=mock` ile sahte seri. Vercel production'da etkinleşmez. |

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

Testler (ağ/anahtar gerektirmez):

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```


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
