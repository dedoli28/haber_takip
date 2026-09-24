# Piyasa Pusulası (Web / Vercel Sürümü)

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
3. `GET /api/cron/gun-ozeti` (Vercel Cron, günde 3 kez) — günün özetini Gemini ile **bir kez** üretip
   Redis'e kaydeder (aşağıya bakın).

`POST /api/tara` ilk ikisini sırayla çağıran bir kısayoldur (manuel test için).

### Gün özeti (10:00 / 14:00 / 18:00) ve Gemini kotası

Özet **Türkiye saatiyle 10:00, 14:00 ve 18:00**'de otomatik üretilir ve Redis'te
**tarih anahtarlı** saklanır: `htp:gun_ozeti:v2:<YYYY-MM-DD>` (14 gün TTL). Kayıt
`durum` (`ready` / `generating` / `failed`), özet metni (`kategoriler`, `genelOzet`),
`olusturulmaZamani`, `tarih`, `haberSayisi`, `sonHataZamani`, `hataKodu` (yalnızca
kısa kod; hata metni saklanmaz) ve `kaynak` alanlarını taşır. Yeniden üretim
sırasında ya da başarısız denemeden sonra bir önceki başarılı içerik korunur.
`htp:gun_ozeti:v2:son` son başarılı özeti tutar (bugün henüz özet yokken dünkü özet
"dünkü özet" uyarısıyla gösterilir); `...:kilit:<tarih>` aynı anda tek Gemini isteğini
sağlar (SET NX EX 90 sn); `...:bekleme` kullanıcı kaynaklı denemeler arasında 15 dk bekler.

**Zamanlama (Vercel Cron, UTC):** `vercel.json` içinde üç giriş vardır
(`0 7`, `0 11`, `0 15` * * *) ve hepsi `GET /api/cron/gun-ozeti`'ni çağırır
(07/11/15 UTC = 10/14/18 İstanbul; Türkiye yıl boyu UTC+3). Vercel Cron yalnızca
`GET` ile ve UTC'ye göre çalışır; `CRON_SECRET` tanımlıysa `Authorization: Bearer
<CRON_SECRET>` başlığını kendisi ekler ve uç nokta bunu sabit zamanlı karşılaştırır.
**`CRON_SECRET` tanımlı değilse (ve `X-Poll-Secret` da gelmiyorsa) uç nokta 401
döner, cron çalışmaz.** Hobby planda her cron ifadesi günde bir kez çalışabilir
(bu yüzden üç ayrı giriş vardır) ancak zamanlama saat başı hassasiyetindedir
(10:00 işi 10:00–10:59 arasında çalışabilir); Pro planda dakika hassasiyeti vardır.
Vercel aynı çalışmayı nadiren iki kez teslim edebilir: son 60 dk içinde üretilmiş özet
varsa cron işi atlanır (`sonuc: atlandi`). Harici bir cron servisi
kullanmak isterseniz `POST /api/gun-ozeti-olustur` (X-Poll-Secret) aynı mantığı çalıştırır.

**Arayüz akışı:** "Günü Özetle" modalı `GET /api/gun-ozeti`'ni okur (Gemini'yi
çağırmaz; eski istemciler için `POST` da aynı yanıtı verir). Durumlar: hazır, hazırlanıyor
(kısa aralıkla yoklanır), hata (önceki özet + neden + "Tekrar dene"), henüz yok (planlı
saatleri ve sonraki güncellemeyi açıklar). Cron kaçarsa ya da geç kalırsa, planlı saat
geçtiği halde ondan sonra üretilmiş özet yoksa modal `POST /api/gun-ozeti/yenile` ile
**kontrollü** yenileme ister: yalnızca özet bayatsa, üretim sürmüyorsa ve son
kullanıcı kaynaklı denemeden 15 dk geçtiyse Gemini çağrılır (kilit sayesinde
aynı anda tek istek). Böylece herkese açık modal kotayı tüketemez (günde en fazla
birkaç istek). Üretimde Gemini'ye yalnızca **bugünün en yeni haberleri** gönderilir:
kategori başına en fazla 8 haber, ülkeler arasında dengeli; "önemsiz" haberler dahil
edilmez; tek deneme ve ~25 sn zaman aşımı (Vercel'in 60 sn sınırı için).

`POST /api/gun-sonu` (gün sonu e-postası) bugüne ait içerikli özet varsa onu kullanır
(Gemini çağrılmaz); yoksa bir kez üretip kaydeder.

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

## Tarama (Hisse Ekranı)

Haberler'in yanında, kullanıcı isteğiyle eklenen bir **Tarama** sekmesi
vardır: Finviz Elite'in resmi Tarayıcı API'sinden (CSV dışa aktarma)
çekilen S&P 500 / NASDAQ 100 kapsamındaki hisseler; sabit 6 çarpan/oran
filtresi (Forward P/E 10–20, PEG 0–1, P/FCF 10–20, EV/EBITDA 8–15,
EV/EBIT 10–18, FCF Yield %5–10 — hepsi kullanıcı tarafından değiştirilebilir),
sektör/ülke çipleri, arama ve sıralanabilir tablo.

**Finviz Elite hesabı GEREKİR** (`FINVIZ_AUTH_TOKEN`, aşağıda). İlk sürüm
Finviz'in anonim (girişsiz) HTML görünümünü kazıyordu ve geliştirme
ortamında sorunsuz çalıştı, ama **canlıda (Vercel) Finviz'in Cloudflare
koruması sunucu IP'sine 403 + JS meydan okuması ("Just a moment...")
döndürdü** — `cloudscraper` ile bile aşılamadı. Finviz Elite'in resmi
dışa aktarma uç noktası (`elite.finviz.com/export/screener`, kişisel bir
`auth` token'ıyla) bu korumadan etkilenmedi ve ayrıca tüm evreni tek
istekte (sayfalama olmadan) döndürüyor. Token, Finviz'de **Tarama →
Tarayıcı API'si** sayfasındaki "API Belirteci Oluştur" ile alınır.

- **Sağlayıcı katmanı:** `finviz_tarama.py` (Finviz Elite CSV dışa aktarma
  uç noktasından tek istekte tüm evreni çeker, `csv` modülüyle ayrıştırır;
  `cloudscraper` — Cloudflare JS meydan okumasına karşı ek güvenlik payı,
  bu uç noktada gerekmiyor ama zararı yok), `tarama_servisi.py` (Redis
  önbellek + tek-uçuş kilit + istemci tarafı filtreleme için tam veri
  döndürme — `haberler` panelindeki desenle aynı).
- **EV/EBIT:** Finviz'de doğrudan yoktur; `Enterprise Value / (Satışlar ×
  Faaliyet Marjı)` ile **yaklaşık** hesaplanır (kullanıcı onayıyla). Eksik/
  anlamsız girdide (sıfır/negatif EBIT) uydurma değer konmaz, alan `null`
  kalır ve o filtre için hisse eşleşmez.
- **Yenileme:** Vercel Cron, günde 2 kez × 2 evren = 4 ayrı günlük giriş
  (Hobby planının "ifade başına günde bir" sınırına uymak için; bkz.
  `vercel.json`). Elle tetiklemek için `POST /api/tarama-guncelle?evren=sp500`
  (X-Poll-Secret).
- **Uç noktalar:** `GET /api/tarama/config` (evrenler, filtre tanımları,
  evrene göre sektör/ülke listeleri), `GET /api/tarama/sonuclar?evren=sp500`
  (önbellekteki tüm hisseler — filtreleme tarayıcıda yapılır),
  `POST /api/tarama/analiz` (seçili/filtrelenmiş hisseleri Gemini ile
  yorumlar; "Analiz Et" ile aynı desende IP başına 10 dakikada 5 istekle
  sınırlıdır).
- **Redis anahtarları:** `htp:tarama:v1:<evren>` (hisse listesi + zaman
  damgası, 3 gün TTL), `htp:tarama:v1:<evren>:kilit` (55 sn SET NX EX).

## AI Sohbet

**AI** sekmesi, platformun güncel verisiyle konuşabilen bir Gemini sohbet
arayüzüdür ("Bugün önemli ne var?", "AAPL nasıl gidiyor?" gibi sorular
yanıtlar). Sunucu durumsuzdur: her istekte tarayıcı tüm konuşma geçmişini
gönderir (`localStorage`'a otomatik yazılmaz — yalnızca kullanıcı "Sohbeti
Kaydet"e basarsa Kaydedilenler'e eklenir).

- **Baglam (grounding):** `sohbet_baglami.py`, YENİ bir Finviz/Gemini isteği
  TETİKLEMEDEN, zaten onbellekte olan üç kaynaktan bir özet çıkarır: (1)
  günün hazır özeti (varsa), (2) en önemli/güncel ~12 haber, (3) kullanıcının
  son mesajında geçen ve S&P 500/NASDAQ 100 tarama önbelleğinde bulunan
  hisseler (basit ticker eşleştirme — gerçek bir fonksiyon çağırma/tool-use
  akışı değildir). Veri yoksa model "bu konuda güncel veri yok" demesi için
  açıkça yönlendirilir; uydurma sayı/haber üretmemesi istenir.
- **Uç nokta:** `POST /api/sohbet` — gövde `{"mesajlar": [{"rol": "kullanici"|
  "asistan", "metin": "..."}]}` (son öge `kullanici` olmalı), yanıt
  `{"ok": true, "yanit": "..."}`. Herkese açık olduğu için IP başına 10
  dakikada en fazla 20 mesajla sınırlıdır; geçmiş sunucuda son 20 mesaja,
  her mesaj 2000 karaktere kırpılır.

## Kaydedilenler: haber + hisse + sohbet

Kaydedilenler artık üç tür kayıt tutar (hepsi `localStorage`, hesap/sunucu
tarafı yok): **haberler** (mevcut davranış, `url` ile anahtarlanır),
**hisseler** (Tarama tablosundaki ★ ile eklenir/kaldırılır, `hisse:<TICKER>`
ile anahtarlanır) ve **sohbetler** (AI sekmesinde "Sohbeti Kaydet",
`sohbet:<id>` ile anahtarlanır). Bir **Tür** filtresi (Tümü/Haberler/
Hisseler/Sohbetler) üstte durur; mevcut Haber Türü/Ülke/Önem filtreleri
yalnızca haber kayıtlarını etkiler. Kayıtlı bir sohbete tıklamak salt-okunur
bir önizleme açar; "Sohbete Devam Et" AI sekmesine geçip o konuşmayı
kaldığı yerden sürdürür (üstüne yazar, birleştirmez).

## Ortam değişkenleri

| Değişken | Zorunlu | Açıklama |
|----------|---------|----------|
| `GEMINI_API_KEY` | evet | Google Gemini API anahtarı ([aistudio.google.com](https://aistudio.google.com/apikey)). Yalnızca sunucuda tutulur, istemciye gönderilmez. |
| `GEMINI_MODEL` | hayır | Kullanılacak model. Varsayılan: `gemini-flash-lite-latest`. |
| `UPSTASH_REDIS_REST_URL` | evet | Upstash Redis REST URL'i. |
| `UPSTASH_REDIS_REST_TOKEN` | evet | Upstash Redis REST token'ı. |
| `POLL_SECRET` | evet | Cron/yönetim uç noktalarının gizli anahtarı. **Tanımlı değilse bu uç noktalar hiç çalışmaz (401).** Uzun ve rastgele bir değer seçin. |
| `CRON_SECRET` | gün özeti cron'u için | Vercel Cron'un `Authorization: Bearer` ile gönderdiği gizli değer (en az 16 rastgele karakter). **Tanımlı değilse `/api/cron/gun-ozeti` 401 döner** (özet yine de arayüzden kontrollü yenilemeyle üretilebilir). |
| `ALLOWED_ORIGINS` | hayır | CORS için ek izinli originler, virgülle ayrılmış (ör. `https://ornek.com,https://baska.com`). |
| `GMAIL_ADDRESS` | e-posta için | Bildirimleri gönderecek Gmail adresi. |
| `GMAIL_APP_PASSWORD` | e-posta için | Google hesabında 2 adımlı doğrulama açıldıktan sonra "Uygulama Şifreleri"nden oluşturulan 16 haneli şifre (normal Gmail şifreniz değildir). |
| `MARKET_DATA_PROVIDER` | piyasa grafikleri için | `twelvedata`. Boşsa piyasa görünümü "yapılandırılmadı" gösterir (haberler etkilenmez). |
| `MARKET_DATA_API_KEY` | piyasa grafikleri için | Sağlayıcı API anahtarı; yalnızca sunucuda okunur, istemciye asla gönderilmez. |
| `MARKET_DATA_MAX_CALLS_PER_MIN` | hayır | Dakikada azami sağlayıcı çağrısı (varsayılan 6). |
| `MARKET_DATA_ALLOW_MOCK` | hayır | YALNIZCA geliştirme: `MARKET_DATA_PROVIDER=mock` ile sahte seri. Vercel production'da etkinleşmez. |
| `FINVIZ_AUTH_TOKEN` | Tarama sekmesi için | Finviz Elite hesabının kişisel dışa aktarma token'ı (Finviz'de *Tarama → Tarayıcı API'si → API Belirteci Oluştur*). **Tanımlı değilse tarama hiç güncellenmez** (cron/manuel tetikleme "hata" döner; sekme kendisi etkilenmez, yalnızca "veri yok" gösterir). |

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
| `/api/cron/gun-ozeti` | **vercel.json'da tanımlı** (10:00/14:00/18:00 İstanbul) | Gün özetini üretip tarih anahtarlı kayda yazar. Harici cron kullanacaksanız `/api/gun-ozeti-olustur` (POST, X-Poll-Secret) aynı işi yapar. |
| `/api/gun-sonu` | günde bir (ör. 23:45) | Günün özeti + bildirilmemiş haberler e-postası. |
| `/api/sabah-ozeti` | her sabah 06:00 (İstanbul) | Gece biriken "çok önemli" haberleri e-postayla gönderir. |
| `/api/cron/tarama` | **vercel.json'da tanımlı** (06:00/18:00 İstanbul × sp500/nasdaq100) | Finviz'den tarama verisini çeker, evren bazlı önbelleğe yazar. Harici cron kullanacaksanız `/api/tarama-guncelle?evren=sp500` (POST, X-Poll-Secret) aynı işi yapar. |

`X-Poll-Secret` ile korunan diğer uç noktalar: `/api/tara`, `/api/depo-sifirla`,
`/api/depo-tekillestir`, `/api/basarisiz-siniflandirmalari-temizle`,
`/api/sentetik-haber`, `/api/sentetik-haber-temizle`.

## Güvenlik

- **Cron/yönetim uç noktaları:** `POLL_SECRET` tanımlı değilse veya başlık
  yanlışsa `401` döner; karşılaştırma sabit zamanlıdır.
- **CORS:** yalnızca `https://haber-takip-nper.vercel.app`,
  `http://127.0.0.1:8000`, `http://localhost:8000` ve `ALLOWED_ORIGINS` ile
  eklenenlere izin verilir. İzinli başlıklar: `Content-Type`, `X-Poll-Secret`.
- **`/api/analiz` ve `/api/tarama/analiz`:** herkese açık oldukları için IP
  başına 10 dakikada en fazla 5 istekle sınırlıdır (Upstash Redis `INCR` +
  `EXPIRE`; IP, Redis anahtarında SHA-256 ile hashlenmiş olarak tutulur).
  Aşılırsa `429` döner.
- **`/api/sohbet`:** aynı IP/SHA-256 deseniyle, 10 dakikada en fazla 20
  mesajla sınırlıdır (bir sohbet birden çok tur gerektirdiği için diğer AI
  uçlarından daha yüksek).
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
