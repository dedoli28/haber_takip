"use strict";

/* ===================== Piyasa Görünümü / Haber Nabzı / Haber detay grafiği =====================
   Bu dosyada sembol ya da veri sağlayıcısı sabiti YOKTUR: ülke/endeks/aralık
   tanımları sunucudaki /api/market/config'ten okunur (market_symbols.py).
   Tarayıcı piyasa sağlayıcısını asla doğrudan çağırmaz; yalnızca kendi
   sunucumuzun /api/market/* ve /api/news/statistics uçlarını kullanır.
   Grafik kütüphanesi (Apache ECharts, vendor/) yalnızca grafik gerektiğinde
   yüklenir. */
const PiyasaGorunumu = (() => {
  const $ = (id) => document.getElementById(id);
  const IST = "Europe/Istanbul";
  const ISTEK_ZAMAN_ASIMI_MS = 15000;
  const ISTEMCI_ONBELLEK_MS = 60 * 1000;
  const YENILEME_MS = 120 * 1000;
  const DEPO_ULKE = "piyasa_ulke_v1";
  const DEPO_ACIK = "piyasa_acik_v1";

  const durum = {
    config: null,
    configHatasi: null,
    ulke: null,
    endeks: null,
    aralik: "1D",
    tur: "cizgi", // cizgi | mum
    sekme: "fiyat", // fiyat | nabiz
    acik: false,
    tabloAcik: false,
    veri: null, // son gecmis yaniti
    overview: null,
    gorunur: true,
    chart: null,
    sonAnahtar: null,
    sonYuklenme: 0,
    zamanlayici: null,
    istek: 0, // yaris kosullarini ayiklamak icin artan sayac
    iptal: null,
    nabizGrafikleri: [],
    nabizRo: null,
    ro: null,
    io: null,
  };
  const istemciOnbellek = new Map();

  /* ---------- Genel yardımcılar ---------- */
  function depoOku(k) {
    try { return localStorage.getItem(k); } catch (e) { return null; }
  }
  function depoYaz(k, v) {
    try { localStorage.setItem(k, v); } catch (e) { /* özel pencere vb.: sorun değil */ }
  }
  const azaltilmisHareket = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function kacis(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const sayiFmt = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const tamSayiFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
  const kompaktFmt = new Intl.NumberFormat("tr-TR", { notation: "compact", maximumFractionDigits: 1 });
  const sayi = (n) => (Number.isFinite(n) ? sayiFmt.format(n) : "—");
  const isaretliSayi = (n) => (Number.isFinite(n) ? (n > 0 ? "+" : n < 0 ? "−" : "") + sayiFmt.format(Math.abs(n)) : "—");
  const hacimMetni = (v) => (Number.isFinite(v) && v > 0 ? kompaktFmt.format(v) : "—");

  const gunSaatFmt = new Intl.DateTimeFormat("tr-TR", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
  const gunAyFmt = new Intl.DateTimeFormat("tr-TR", { timeZone: IST, day: "2-digit", month: "short" });
  const gunAySaatFmt = new Intl.DateTimeFormat("tr-TR", { timeZone: IST, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  const tamZamanFmt = new Intl.DateTimeFormat("tr-TR", { timeZone: IST, day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

  function eksenEtiketi(sn, aralik, gunlukMu) {
    const d = new Date(sn * 1000);
    if (gunlukMu) return gunAyFmt.format(d);
    return aralik === "1D" ? gunSaatFmt.format(d) : gunAySaatFmt.format(d);
  }

  function yonBilgisi(degisim) {
    if (!Number.isFinite(degisim) || degisim === 0) return { sinif: "flat", ok: "▬", ad: "değişim yok" };
    return degisim > 0 ? { sinif: "up", ok: "▲", ad: "artış" } : { sinif: "down", ok: "▼", ad: "düşüş" };
  }

  function cssDegeri(ad, yedek) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(ad).trim();
    return v || yedek;
  }
  function renkAlfa(renk, a) {
    let m = /^#([0-9a-f]{6})$/i.exec(renk);
    if (m) {
      const n = parseInt(m[1], 16);
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    }
    m = /^rgb\((\d+)[ ,]+(\d+)[ ,]+(\d+)\)$/i.exec(renk);
    return m ? `rgba(${m[1]},${m[2]},${m[3]},${a})` : renk;
  }
  function renkler() {
    return {
      yukari: cssDegeri("--up", "#22c55e"),
      asagi: cssDegeri("--down", "#ef4444"),
      yatay: cssDegeri("--text-muted", "#8b93a7"),
      yazi: cssDegeri("--text", "#e8eaf0"),
      soluk: cssDegeri("--text-muted", "#8b93a7"),
      izgara: cssDegeri("--chart-grid", "#232838"),
      panel: cssDegeri("--bg-panel", "#12151c"),
      kenar: cssDegeri("--border", "#232838"),
      vurgu: cssDegeri("--accent", "#4f7cff"),
    };
  }

  /* ---------- Ağ ---------- */
  async function getJson(url, sinyal) {
    const ac = new AbortController();
    const zaman = setTimeout(() => ac.abort("timeout"), ISTEK_ZAMAN_ASIMI_MS);
    const dis = () => ac.abort("iptal");
    if (sinyal) {
      if (sinyal.aborted) { clearTimeout(zaman); throw { tur: "iptal" }; }
      sinyal.addEventListener("abort", dis, { once: true });
    }
    try {
      const r = await fetch(url, { signal: ac.signal, headers: { Accept: "application/json" } });
      let govde = null;
      try { govde = await r.json(); } catch (e) { govde = null; }
      if (r.ok && govde && govde.ok !== false) return govde;
      throw { tur: "http", durum: r.status, kod: govde && govde.kod, hata: govde && govde.hata };
    } catch (e) {
      if (e && e.tur) throw e;
      if (ac.signal.aborted) throw { tur: ac.signal.reason === "timeout" ? "timeout" : "iptal" };
      throw { tur: "ag" };
    } finally {
      clearTimeout(zaman);
      if (sinyal) sinyal.removeEventListener("abort", dis);
    }
  }

  function hataBilgisi(e) {
    if (e.tur === "timeout") return { kod: "timeout", mesaj: "Piyasa verisi zamanında alınamadı.", tekrar: true };
    if (e.tur === "ag") return { kod: "ag", mesaj: "Bağlantı kurulamadı. İnternet bağlantınızı kontrol edip tekrar deneyin.", tekrar: true };
    if (e.kod === "yapilandirilmadi") {
      return { kod: e.kod, mesaj: "Piyasa verisi henüz yapılandırılmadı.", ek: "Sunucuda bir piyasa veri sağlayıcısı tanımlandığında grafikler burada görünecek. Haberler etkilenmez.", tekrar: false };
    }
    if (e.kod === "rate_limit") return { kod: e.kod, mesaj: "Piyasa verisi sağlayıcısının istek sınırına ulaşıldı. Birkaç dakika sonra tekrar deneyin.", tekrar: true };
    if (e.kod === "veri_yok") return { kod: e.kod, mesaj: "Bu sembol veya aralık için veri bulunamadı.", tekrar: true };
    return { kod: e.kod || "hata", mesaj: "Piyasa verisi şu anda alınamıyor.", tekrar: true };
  }

  async function onbellekliGetir(anahtar, url, sinyal, zorla) {
    const kayit = istemciOnbellek.get(anahtar);
    if (!zorla && kayit && Date.now() - kayit.t < ISTEMCI_ONBELLEK_MS) return kayit.veri;
    const veri = await getJson(url, sinyal);
    istemciOnbellek.set(anahtar, { t: Date.now(), veri });
    return veri;
  }

  /* ---------- Grafik kütüphanesi (tembel yükleme) ---------- */
  let echartsSozu = null;
  function echartsYukle() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (echartsSozu) return echartsSozu;
    echartsSozu = new Promise((coz, red) => {
      const s = document.createElement("script");
      s.src = "vendor/echarts.min.js";
      s.async = true;
      s.onload = () => (window.echarts ? coz(window.echarts) : red(new Error("echarts yok")));
      s.onerror = () => { echartsSozu = null; red(new Error("echarts yuklenemedi")); };
      document.head.appendChild(s);
    });
    return echartsSozu;
  }

  /* ---------- Sekme (tablist) klavye gezintisi ---------- */
  function sekmeKlavye(kap) {
    kap.addEventListener("keydown", (e) => {
      const sekmeler = [...kap.querySelectorAll('[role="tab"]')];
      const i = sekmeler.indexOf(document.activeElement);
      if (i < 0) return;
      let yeni = -1;
      if (e.key === "ArrowRight") yeni = (i + 1) % sekmeler.length;
      else if (e.key === "ArrowLeft") yeni = (i - 1 + sekmeler.length) % sekmeler.length;
      else if (e.key === "Home") yeni = 0;
      else if (e.key === "End") yeni = sekmeler.length - 1;
      if (yeni >= 0) {
        e.preventDefault();
        sekmeler[yeni].focus();
        sekmeler[yeni].click();
      }
    });
  }
  function sekmeSec(kap, secilen) {
    kap.querySelectorAll('[role="tab"]').forEach((b) => {
      const aktif = b === secilen;
      b.setAttribute("aria-selected", aktif ? "true" : "false");
      b.tabIndex = aktif ? 0 : -1;
      b.classList.toggle("is-active", aktif);
    });
  }

  /* ---------- Durum (iskelet / hata / boş) yönetimi ---------- */
  function panelYapilandirilmadiIsaretle(isaret) {
    $("marketPanel").classList.toggle("is-yapilandirilmadi", !!isaret);
  }

  function durumGoster(kap, tur, bilgi, tekrarFn) {
    const iskelet = kap.querySelector(".market-iskelet");
    const durumKutusu = kap.querySelector(".market-durum");
    const grafik = kap.querySelector(".market-chart, .detail-market-chart");
    iskelet.hidden = tur !== "yukleniyor";
    durumKutusu.hidden = tur === "yukleniyor" || tur === "hazir";
    if (grafik) grafik.style.visibility = tur === "hazir" ? "visible" : "hidden";
    kap.setAttribute("aria-busy", tur === "yukleniyor" ? "true" : "false");
    if (tur === "yukleniyor" || tur === "hazir") return;

    durumKutusu.textContent = "";
    durumKutusu.dataset.kod = (bilgi && bilgi.kod) || "";
    const p = el("p", "market-durum-mesaj", bilgi.mesaj);
    p.setAttribute("role", "status");
    durumKutusu.appendChild(p);
    if (bilgi.ek) durumKutusu.appendChild(el("p", "market-durum-ek", bilgi.ek));
    if (bilgi.tekrar && tekrarFn) {
      const b = el("button", "btn btn-outline btn-sm market-tekrar", "Yeniden dene");
      b.type = "button";
      b.addEventListener("click", tekrarFn);
      durumKutusu.appendChild(b);
    }
  }

  /* ---------- Grafik seçenekleri ---------- */
  function ohlcUygunMu(noktalar) {
    return noktalar.some((n) => n.high > n.low || n.open !== n.close);
  }

  function noktaAciklamasi(n, aralik, gunlukMu, paraBirimi) {
    const d = new Date(n.time * 1000);
    const zaman = gunlukMu ? tamZamanFmt.format(d).replace(/ \d{2}:\d{2}$/, "") : tamZamanFmt.format(d);
    return { zaman, para: paraBirimi ? ` ${paraBirimi}` : "" };
  }

  function secenekOlustur(noktalar, ayar) {
    const r = renkler();
    const { tur, aralik, kompakt, haberSn, paraBirimi } = ayar;
    const farklar = noktalar.slice(1).map((n, i) => n.time - noktalar[i].time).filter((x) => x > 0);
    const adim = farklar.length ? Math.min(...farklar) : 0;
    const gunlukMu = adim >= 86400 - 1;
    const kategoriler = noktalar.map((n) => eksenEtiketi(n.time, aralik, gunlukMu));
    const ilk = noktalar[0].close;
    const son = noktalar[noktalar.length - 1].close;
    const anaRenk = son >= ilk ? r.yukari : r.asagi;
    const animasyon = !azaltilmisHareket() && ayar.animasyon !== false;
    const genis = window.innerWidth >= 700;

    const seri = [];
    if (tur === "mum") {
      seri.push({
        type: "candlestick",
        name: "Fiyat",
        data: noktalar.map((n) => [n.open, n.close, n.low, n.high]),
        itemStyle: { color: r.yukari, color0: r.asagi, borderColor: r.yukari, borderColor0: r.asagi },
      });
    } else {
      const s = {
        type: "line",
        name: "Kapanış",
        data: noktalar.map((n) => n.close),
        showSymbol: false,
        symbolSize: 6,
        sampling: "lttb",
        smooth: false,
        lineStyle: { width: 2, color: anaRenk },
        itemStyle: { color: anaRenk },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: renkAlfa(anaRenk, 0.22) }, { offset: 1, color: renkAlfa(anaRenk, 0) }],
          },
        },
      };
      seri.push(s);
    }
    if (haberSn != null) {
      let idx = noktalar.findIndex((n) => n.time >= haberSn);
      if (idx < 0) idx = noktalar.length - 1;
      seri[0].markLine = {
        silent: true,
        symbol: "none",
        animation: false,
        lineStyle: { type: "dashed", color: r.vurgu, width: 1.5 },
        label: { formatter: "Haber", color: r.vurgu, position: "insideEndTop", fontWeight: 700 },
        data: [{ xAxis: idx }],
      };
    }

    return {
      animation: animasyon,
      animationDuration: 500,
      animationEasing: "cubicOut",
      animationDurationUpdate: animasyon ? 300 : 0,
      backgroundColor: "transparent",
      textStyle: { fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif" },
      grid: { left: 4, right: kompakt ? 8 : 14, top: kompakt ? 12 : 18, bottom: kompakt ? 22 : genis ? 56 : 30, containLabel: true },
      tooltip: {
        trigger: "axis",
        confine: true,
        axisPointer: { type: "cross", label: { show: false }, lineStyle: { color: r.soluk, type: "dashed" }, crossStyle: { color: r.soluk } },
        backgroundColor: r.panel,
        borderColor: r.kenar,
        textStyle: { color: r.yazi, fontSize: 12 },
        extraCssText: "box-shadow:0 8px 24px rgba(0,0,0,.35);border-radius:10px;",
        formatter: (p) => {
          const i = Array.isArray(p) ? p[0].dataIndex : p.dataIndex;
          const n = noktalar[i];
          if (!n) return "";
          const a = noktaAciklamasi(n, aralik, gunlukMu, paraBirimi);
          const satir = (k, v) => `<div style="display:flex;justify-content:space-between;gap:18px"><span style="color:${r.soluk}">${k}</span><b style="font-variant-numeric:tabular-nums">${v}</b></div>`;
          return `<div style="min-width:170px"><div style="font-weight:700;margin-bottom:6px">${kacis(a.zaman)}</div>` +
            satir("Açılış", sayi(n.open)) + satir("En yüksek", sayi(n.high)) + satir("En düşük", sayi(n.low)) +
            satir("Kapanış", sayi(n.close)) + satir("Hacim", hacimMetni(n.volume)) + "</div>";
        },
      },
      xAxis: {
        type: "category",
        data: kategoriler,
        boundaryGap: tur === "mum",
        axisLine: { lineStyle: { color: r.izgara } },
        axisTick: { show: false },
        axisLabel: { color: r.soluk, fontSize: 11, hideOverlap: true, margin: 10, alignMinLabel: "left", alignMaxLabel: "right" },
      },
      yAxis: {
        type: "value",
        scale: true,
        position: "right",
        splitLine: { lineStyle: { color: r.izgara, opacity: 0.7 } },
        axisLabel: { color: r.soluk, fontSize: 11, formatter: (v) => sayiFmt.format(v).replace(/,00$/, "") },
      },
      dataZoom: [
        { type: "inside", filterMode: "filter", zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false, minValueSpan: 5 },
        ...(kompakt || !genis ? [] : [{
          type: "slider", height: 20, bottom: 8, filterMode: "filter", borderColor: r.izgara,
          fillerColor: renkAlfa(r.vurgu, 0.18), textStyle: { color: r.soluk }, handleSize: 14, brushSelect: false,
        }]),
      ],
      series: seri,
    };
  }

  function ozetMetni(veri) {
    const p = veri.points;
    const ilk = p[0], son = p[p.length - 1];
    const enYuksek = Math.max(...p.map((x) => x.high));
    const enDusuk = Math.min(...p.map((x) => x.low));
    const fark = son.close - ilk.open;
    const yuzde = ilk.open ? (fark / ilk.open) * 100 : NaN;
    return { ilk, son, enYuksek, enDusuk, fark, yuzde };
  }

  /* ===================== Ana Piyasa paneli ===================== */
  function ulkeVerisi() {
    return durum.config && durum.config.ulkeler.find((u) => u.kod === durum.ulke);
  }
  function endeksVerisi() {
    const u = ulkeVerisi();
    return u && (u.endeksler.find((e) => e.id === durum.endeks) || u.endeksler[0]);
  }

  function baslikMesajiYaz(metin) {
    const kutu = $("marketOzet");
    kutu.textContent = "";
    kutu.appendChild(el("span", "market-ozet-soluk", metin));
  }

  function baslikOzetiYaz(overview) {
    const kutu = $("marketOzet");
    kutu.textContent = "";
    const u = ulkeVerisi();
    if (!overview) {
      kutu.appendChild(el("span", "market-ozet-soluk", durum.configHatasi ? "Piyasa verisi alınamıyor" : "Yükleniyor…"));
      return;
    }
    const ana = overview.indices.find((i) => i.available && i.symbol === (overview.default_symbol)) || overview.indices.find((i) => i.available);
    if (!ana) { kutu.appendChild(el("span", "market-ozet-soluk", "Veri yok")); return; }
    const y = yonBilgisi(ana.change_pct ?? ana.change);
    kutu.appendChild(el("span", "market-ozet-ad", `${u ? u.ad + " · " : ""}${ana.name}`));
    kutu.appendChild(el("span", "market-ozet-deger", sayi(ana.last)));
    const d = el("span", `market-ozet-degisim ${y.sinif}`, `${y.ok} ${Number.isFinite(ana.change_pct) ? isaretliSayi(ana.change_pct) + "%" : isaretliSayi(ana.change)}`);
    d.setAttribute("aria-label", `${y.ad} ${Number.isFinite(ana.change_pct) ? isaretliSayi(ana.change_pct) + " yüzde" : isaretliSayi(ana.change)}`);
    kutu.appendChild(d);
  }

  function quoteYaz(overview) {
    const kutu = $("marketAnlik");
    kutu.textContent = "";
    if (!overview) return;
    const e = overview.indices.find((i) => i.symbol === durum.endeks && i.available);
    if (!e) { kutu.appendChild(el("span", "market-ozet-soluk", "Bu endeks için anlık değer alınamadı.")); return; }
    const y = yonBilgisi(e.change);
    kutu.appendChild(el("span", "market-anlik-ad", e.name));
    kutu.appendChild(el("span", "market-anlik-deger", `${sayi(e.last)}${e.currency ? " " + e.currency : ""}`));
    const d = el("span", `market-anlik-degisim ${y.sinif}`);
    d.textContent = `${y.ok} ${isaretliSayi(e.change)} (${isaretliSayi(e.change_pct)}%)`;
    d.setAttribute("aria-label", `${y.ad}: ${isaretliSayi(e.change)}, yüzde ${isaretliSayi(e.change_pct)}`);
    kutu.appendChild(d);
    if (e.updated_at) {
      const t = el("time", "market-anlik-zaman", `Son değer: ${tamZamanFmt.format(new Date(e.updated_at))} (TSİ)`);
      t.dateTime = e.updated_at;
      kutu.appendChild(t);
    }
    if (e.market_open === true) kutu.appendChild(el("span", "market-rozet", "Piyasa açık"));
    else if (e.market_open === false) kutu.appendChild(el("span", "market-rozet", "Piyasa kapalı"));
  }

  function metaYaz(veri) {
    const m = $("marketMeta");
    m.textContent = "";
    if (!veri) return;
    const parcalar = [];
    if (veri.last_bar_at) parcalar.push(`Son veri: ${tamZamanFmt.format(new Date(veri.last_bar_at))} (TSİ)`);
    parcalar.push(`Kaynak: ${veri.source || "—"}`);
    parcalar.push("Veriler gecikmeli olabilir; yatırım tavsiyesi değildir.");
    m.appendChild(document.createTextNode(parcalar.join(" · ")));
    if (veri.stale) m.appendChild(el("span", "market-rozet market-rozet-uyari", "Eski veri (sağlayıcıya ulaşılamadı)"));
    if (veri.mock) m.appendChild(el("span", "market-rozet market-rozet-mock", "TEST VERİSİ — gerçek piyasa verisi değildir"));
  }

  function erisilebilirAciklama(veri) {
    const ad = veri.name;
    const o = ozetMetni(veri);
    const aralikEtiket = (durum.config.araliklar.find((a) => a.id === veri.range) || {}).etiket || veri.range;
    const y = yonBilgisi(o.fark);
    return `${ad} fiyat grafiği, ${aralikEtiket} aralığı. Başlangıç ${sayi(o.ilk.open)}, son değer ${sayi(o.son.close)}, ` +
      `${y.ad} ${isaretliSayi(o.fark)} (yüzde ${isaretliSayi(o.yuzde)}). En yüksek ${sayi(o.enYuksek)}, en düşük ${sayi(o.enDusuk)}. ` +
      `${veri.points.length} veri noktası.`;
  }

  function tabloYaz(veri) {
    const kutu = $("marketTabloKutusu");
    kutu.textContent = "";
    const tablo = el("table", "market-tablo");
    const cap = el("caption", null, `${veri.name} — son ${Math.min(60, veri.points.length)} veri noktası (${veri.interval})`);
    tablo.appendChild(cap);
    const bas = el("thead");
    const satir = el("tr");
    ["Zaman (TSİ)", "Açılış", "En yüksek", "En düşük", "Kapanış", "Hacim"].forEach((k) => {
      const th = el("th", null, k);
      th.scope = "col";
      satir.appendChild(th);
    });
    bas.appendChild(satir);
    tablo.appendChild(bas);
    const govde = el("tbody");
    veri.points.slice(-60).reverse().forEach((n) => {
      const tr = el("tr");
      const th = el("th", null, tamZamanFmt.format(new Date(n.time * 1000)));
      th.scope = "row";
      tr.appendChild(th);
      [n.open, n.high, n.low, n.close].forEach((v) => tr.appendChild(el("td", null, sayi(v))));
      tr.appendChild(el("td", null, hacimMetni(n.volume)));
      govde.appendChild(tr);
    });
    tablo.appendChild(govde);
    kutu.appendChild(tablo);
  }

  function turDugmeleriniGuncelle(veri) {
    const uygun = ohlcUygunMu(veri.points);
    const mumBtn = $("marketTurMum");
    mumBtn.disabled = !uygun;
    mumBtn.title = uygun ? "" : "Bu veri için mum grafik uygun değil (açılış/yüksek/düşük bilgisi yok).";
    if (!uygun && durum.tur === "mum") durum.tur = "cizgi";
    $("marketTurCizgi").setAttribute("aria-pressed", durum.tur === "cizgi" ? "true" : "false");
    mumBtn.setAttribute("aria-pressed", durum.tur === "mum" ? "true" : "false");
  }

  async function anaGrafigiCiz(veri, ilkCizim) {
    const kap = $("marketChart");
    let ec;
    try {
      ec = await echartsYukle();
    } catch (e) {
      durumGoster($("marketGrafikKutusu"), "hata", { kod: "kutuphane", mesaj: "Grafik kütüphanesi yüklenemedi.", tekrar: true }, () => yukle(true));
      return;
    }
    if (!durum.acik) return;
    let c = durum.chart;
    if (!c || c.isDisposed()) {
      c = ec.init(kap, null, { renderer: "canvas" });
      durum.chart = c;
      if (!durum.ro) {
        durum.ro = new ResizeObserver(() => { if (durum.chart && !durum.chart.isDisposed()) durum.chart.resize(); });
        durum.ro.observe(kap);
      }
    }
    turDugmeleriniGuncelle(veri);
    const secenek = secenekOlustur(veri.points, {
      tur: durum.tur, aralik: veri.range, kompakt: false, paraBirimi: veri.currency, animasyon: ilkCizim,
    });
    c.setOption(secenek, { notMerge: ilkCizim, lazyUpdate: true });
    kap.setAttribute("aria-label", erisilebilirAciklama(veri));
    $("marketGrafikAciklama").textContent = erisilebilirAciklama(veri);
  }

  function anaGrafigiTemizle() {
    if (durum.ro) { durum.ro.disconnect(); durum.ro = null; }
    if (durum.chart && !durum.chart.isDisposed()) durum.chart.dispose();
    durum.chart = null;
    durum.sonAnahtar = null;
  }

  function yenilemeyiPlanla() {
    clearTimeout(durum.zamanlayici);
    if (!durum.acik || durum.sekme !== "fiyat") return;
    durum.zamanlayici = setTimeout(() => {
      if (document.hidden || !durum.gorunur) { yenilemeyiPlanla(); return; }
      yukle(false, true);
    }, YENILEME_MS);
  }

  async function yukle(zorla, sessiz) {
    if (!durum.config || !durum.ulke || !durum.endeks) return;
    if (durum.iptal) durum.iptal.abort();
    const ac = new AbortController();
    durum.iptal = ac;
    const benimIstegim = ++durum.istek;
    const kutu = $("marketGrafikKutusu");
    const anahtar = `${durum.endeks}|${durum.aralik}|${durum.tur}`;
    const ilkCizim = anahtar !== durum.sonAnahtar || !durum.chart;
    if (!sessiz || ilkCizim) durumGoster(kutu, "yukleniyor");

    const mum = (durum.config.araliklar.find((a) => a.id === durum.aralik) || {}).varsayilan_mum_araligi;
    try {
      const [ov, gecmis] = await Promise.all([
        onbellekliGetir(`ov|${durum.ulke}`, `/api/market/overview?country=${encodeURIComponent(durum.ulke)}`, ac.signal, zorla).catch((e) => {
          if (e.tur === "iptal") throw e;
          return null; // genel bakis olmadan grafik yine de gosterilebilir
        }),
        onbellekliGetir(`h|${durum.endeks}|${durum.aralik}`,
          `/api/market/history?symbol=${encodeURIComponent(durum.endeks)}&range=${encodeURIComponent(durum.aralik)}${mum ? "&interval=" + encodeURIComponent(mum) : ""}`,
          ac.signal, zorla),
      ]);
      if (benimIstegim !== durum.istek) return;
      durum.veri = gecmis;
      durum.overview = ov;
      durum.sonYuklenme = Date.now();
      baslikOzetiYaz(ov);
      quoteYaz(ov);
      metaYaz(gecmis);
      durumGoster(kutu, "hazir");
      await anaGrafigiCiz(gecmis, ilkCizim);
      durum.sonAnahtar = anahtar;
      if (durum.tabloAcik) tabloYaz(gecmis);
      yenilemeyiPlanla();
    } catch (e) {
      if (e.tur === "iptal" || benimIstegim !== durum.istek) return;
      const bilgi = hataBilgisi(e);
      durumGoster(kutu, bilgi.kod === "veri_yok" ? "bos" : "hata", bilgi, () => yukle(true));
      panelYapilandirilmadiIsaretle(bilgi.kod === "yapilandirilmadi");
      baslikMesajiYaz(bilgi.kod === "yapilandirilmadi" ? "Piyasa verisi yapılandırılmadı" : "Piyasa verisi alınamıyor");
      metaYaz(null);
      quoteYaz(null);
      anaGrafigiTemizle();
      yenilemeyiPlanla();
    }
  }

  /* ---------- Ülke / endeks / aralık kontrolleri ---------- */
  function ulkeSekmeleriniOlustur() {
    const kap = $("marketUlkeSekmeleri");
    kap.textContent = "";
    durum.config.ulkeler.forEach((u) => {
      const b = el("button", "market-sekme", u.ad);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.id = `marketUlke-${u.kod}`;
      b.setAttribute("aria-controls", "marketFiyatPaneli");
      b.dataset.ulke = u.kod;
      b.addEventListener("click", () => ulkeSec(u.kod, b));
      kap.appendChild(b);
    });
    const secili = kap.querySelector(`[data-ulke="${durum.ulke}"]`) || kap.firstElementChild;
    sekmeSec(kap, secili);
  }

  function endeksSeciciyiOlustur() {
    const kap = $("marketEndeksSecici");
    kap.textContent = "";
    const u = ulkeVerisi();
    kap.hidden = !u || u.endeksler.length < 2;
    if (kap.hidden) return;
    u.endeksler.forEach((e) => {
      const b = el("button", "market-mini", e.ad);
      b.type = "button";
      b.dataset.endeks = e.id;
      b.setAttribute("aria-pressed", e.id === durum.endeks ? "true" : "false");
      b.addEventListener("click", () => {
        durum.endeks = e.id;
        kap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
        quoteYaz(durum.overview);
        yukle(false);
      });
      kap.appendChild(b);
    });
  }

  function ulkeSec(kod, dugme) {
    if (durum.ulke === kod && durum.endeks) return;
    durum.ulke = kod;
    depoYaz(DEPO_ULKE, kod);
    const u = ulkeVerisi();
    durum.endeks = (u.endeksler.find((e) => e.varsayilan) || u.endeksler[0]).id;
    sekmeSec($("marketUlkeSekmeleri"), dugme || $("marketUlkeSekmeleri").querySelector(`[data-ulke="${kod}"]`));
    endeksSeciciyiOlustur();
    durum.overview = null;
    quoteYaz(null);
    baslikOzetiYaz(null);
    if (durum.acik) yukle(false);
    else ozetiYukle();
  }

  function aralikDugmeleriniOlustur() {
    const kap = $("marketAraliklar");
    kap.textContent = "";
    durum.config.araliklar.forEach((a) => {
      const b = el("button", "market-mini", a.etiket);
      b.type = "button";
      b.dataset.aralik = a.id;
      b.setAttribute("aria-pressed", a.id === durum.aralik ? "true" : "false");
      b.setAttribute("aria-label", { "1D": "1 gün", "1W": "1 hafta", "1M": "1 ay", "3M": "3 ay", "1Y": "1 yıl" }[a.id] || a.etiket);
      b.addEventListener("click", () => {
        durum.aralik = a.id;
        kap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
        yukle(false);
      });
      kap.appendChild(b);
    });
  }

  function yakinlastir(carpan) {
    const c = durum.chart;
    if (!c || c.isDisposed()) return;
    const dz = (c.getOption().dataZoom || [])[0] || { start: 0, end: 100 };
    const orta = (dz.start + dz.end) / 2;
    let aralik = (dz.end - dz.start) * carpan;
    aralik = Math.min(100, Math.max(4, aralik));
    let bas = orta - aralik / 2;
    let son = orta + aralik / 2;
    if (bas < 0) { son -= bas; bas = 0; }
    if (son > 100) { bas -= son - 100; son = 100; }
    c.dispatchAction({ type: "dataZoom", start: Math.max(0, bas), end: Math.min(100, son) });
  }
  function kaydir(oran) {
    const c = durum.chart;
    if (!c || c.isDisposed()) return;
    const dz = (c.getOption().dataZoom || [])[0] || { start: 0, end: 100 };
    const genislik = dz.end - dz.start;
    let bas = dz.start + genislik * oran;
    bas = Math.min(100 - genislik, Math.max(0, bas));
    c.dispatchAction({ type: "dataZoom", start: bas, end: bas + genislik });
  }
  function sifirla() {
    if (durum.chart && !durum.chart.isDisposed()) durum.chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  }

  function kontrolleriBagla() {
    $("marketTurCizgi").addEventListener("click", () => { durum.tur = "cizgi"; if (durum.veri) { turDugmeleriniGuncelle(durum.veri); anaGrafigiCiz(durum.veri, true); } });
    $("marketTurMum").addEventListener("click", () => { durum.tur = "mum"; if (durum.veri) { turDugmeleriniGuncelle(durum.veri); anaGrafigiCiz(durum.veri, true); } });
    $("marketYakin").addEventListener("click", () => yakinlastir(0.5));
    $("marketUzak").addEventListener("click", () => yakinlastir(2));
    $("marketSifirla").addEventListener("click", sifirla);
    $("marketTabloBtn").addEventListener("click", () => {
      durum.tabloAcik = !durum.tabloAcik;
      $("marketTabloBtn").setAttribute("aria-pressed", durum.tabloAcik ? "true" : "false");
      $("marketTabloKutusu").hidden = !durum.tabloAcik;
      if (durum.tabloAcik && durum.veri) tabloYaz(durum.veri);
    });
    const grafik = $("marketChart");
    grafik.addEventListener("keydown", (e) => {
      if (e.key === "+" || e.key === "=") { e.preventDefault(); yakinlastir(0.5); }
      else if (e.key === "-" || e.key === "_") { e.preventDefault(); yakinlastir(2); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); kaydir(-0.2); }
      else if (e.key === "ArrowRight") { e.preventDefault(); kaydir(0.2); }
      else if (e.key === "0" || e.key === "Home") { e.preventDefault(); sifirla(); }
    });
  }

  /* ---------- Panel aç/kapat ---------- */
  async function panelAc(ac, kaydet) {
    durum.acik = ac;
    const panel = $("marketPanel");
    panel.classList.toggle("is-collapsed", !ac);
    $("marketToggle").setAttribute("aria-expanded", ac ? "true" : "false");
    $("marketBody").hidden = !ac;
    if (kaydet !== false) depoYaz(DEPO_ACIK, ac ? "1" : "0");
    if (ac) {
      if (!durum.ulke) {
        durumGoster($("marketGrafikKutusu"), "yukleniyor");
        try {
          await configYukle();
          ilkKurulum();
          if (!durum.config.yapilandirildi) {
            baslikMesajiYaz("Piyasa verisi yapılandırılmadı");
            panelYapilandirilmadiIsaretle(true);
          }
        } catch (e) {
          baslikMesajiYaz("Piyasa verisi alınamıyor");
          if (durum.acik) yukleHatasi();
          return;
        }
        if (!durum.acik) return;
      }
      sekmeDegistir(durum.sekme);
    } else {
      clearTimeout(durum.zamanlayici);
      if (durum.iptal) durum.iptal.abort();
      anaGrafigiTemizle();
      nabizTemizle();
    }
  }

  function sekmeDegistir(hedef) {
    durum.sekme = hedef;
    const kap = document.querySelector(".market-ana-sekmeler");
    sekmeSec(kap, kap.querySelector(`[data-sekme="${hedef}"]`));
    $("marketFiyatPaneli").hidden = hedef !== "fiyat";
    $("marketNabizPaneli").hidden = hedef !== "nabiz";
    if (!durum.acik) return;
    if (hedef === "fiyat") {
      nabizTemizle();
      yukle(false);
    } else {
      clearTimeout(durum.zamanlayici);
      if (durum.iptal) durum.iptal.abort();
      anaGrafigiTemizle();
      nabziYukle(false);
    }
  }

  /* ---------- Yapılandırma + başlık özeti ---------- */
  async function configYukle() {
    if (durum.config) return durum.config;
    try {
      durum.config = await getJson("/api/market/config");
      durum.configHatasi = null;
    } catch (e) {
      durum.configHatasi = e;
      throw e;
    }
    return durum.config;
  }

  function ilkKurulum() {
    if (durum.ulke) return;
    const kayitli = depoOku(DEPO_ULKE);
    const ulkeler = durum.config.ulkeler;
    durum.ulke = (ulkeler.find((u) => u.kod === kayitli) || ulkeler[0]).kod;
    const u = ulkeVerisi();
    durum.endeks = (u.endeksler.find((e) => e.varsayilan) || u.endeksler[0]).id;
    ulkeSekmeleriniOlustur();
    endeksSeciciyiOlustur();
    aralikDugmeleriniOlustur();
  }

  async function ozetiYukle() {
    try {
      await configYukle();
    } catch (e) {
      baslikOzetiYaz(null);
      return;
    }
    ilkKurulum();
    if (!durum.config.yapilandirildi) {
      const k = $("marketOzet");
      k.textContent = "";
      k.appendChild(el("span", "market-ozet-soluk", "Piyasa verisi yapılandırılmadı"));
      return;
    }
    try {
      const ov = await onbellekliGetir(`ov|${durum.ulke}`, `/api/market/overview?country=${encodeURIComponent(durum.ulke)}`, null, false);
      durum.overview = ov;
      baslikOzetiYaz(ov);
      quoteYaz(ov);
    } catch (e) {
      const k = $("marketOzet");
      k.textContent = "";
      k.appendChild(el("span", "market-ozet-soluk", "Piyasa verisi alınamıyor"));
    }
  }

  /* ===================== Haber Nabzı ===================== */
  const NABIZ_RENK = {
    ulke: { TR: "#e11d48", US: "#0ea5e9", DE: "#14b8a6", CN: "#f97316" },
    sinif: { cok_onemli: "#ef4444", onemli: "#f59e0b", bakmaya_deger: "#3b82f6", onemsiz: "#6b7280" },
    kategori: ["#8b5cf6", "#a78bfa", "#7c3aed", "#c4b5fd", "#6d28d9", "#ddd6fe"],
  };

  function nabizTemizle() {
    durum.nabizGrafikleri.forEach((c) => { if (c && !c.isDisposed()) c.dispose(); });
    durum.nabizGrafikleri = [];
  }

  function halkaSecenegi(kayitlar, renkFn) {
    const r = renkler();
    const animasyon = !azaltilmisHareket();
    return {
      animation: animasyon,
      animationDuration: 500,
      backgroundColor: "transparent",
      textStyle: { fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif" },
      tooltip: { trigger: "item", confine: true, backgroundColor: r.panel, borderColor: r.kenar, textStyle: { color: r.yazi, fontSize: 12 },
        formatter: (p) => `${kacis(p.name)}: <b>${tamSayiFmt.format(p.value)}</b> (${p.percent}%)` },
      legend: { bottom: 0, type: "plain", itemWidth: 10, itemHeight: 10, itemGap: 10, textStyle: { color: r.soluk, fontSize: 11 } },
      series: [{
        type: "pie", radius: ["40%", "62%"], center: ["50%", "38%"], avoidLabelOverlap: true, label: { show: false }, labelLine: { show: false },
        itemStyle: { borderColor: r.panel, borderWidth: 2 },
        data: kayitlar.map((k, i) => ({ name: `${k.label} · ${tamSayiFmt.format(k.count)}`, value: k.count, itemStyle: { color: renkFn(k, i) } })),
      }],
    };
  }

  function saatlikSecenek(saatlik) {
    const r = renkler();
    const animasyon = !azaltilmisHareket();
    const saatFmt = new Intl.DateTimeFormat("tr-TR", { timeZone: IST, hour: "2-digit", hour12: false });
    return {
      animation: animasyon,
      animationDuration: 500,
      backgroundColor: "transparent",
      textStyle: { fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif" },
      grid: { left: 4, right: 10, top: 14, bottom: 4, containLabel: true },
      tooltip: { trigger: "axis", confine: true, axisPointer: { type: "shadow" }, backgroundColor: r.panel, borderColor: r.kenar, textStyle: { color: r.yazi, fontSize: 12 },
        formatter: (p) => { const n = saatlik[p[0].dataIndex]; const d = new Date(n.hour_start);
          return `${kacis(gunAySaatFmt.format(d))}<br><b>${tamSayiFmt.format(n.count)}</b> haber`; } },
      xAxis: { type: "category", data: saatlik.map((n) => saatFmt.format(new Date(n.hour_start))), axisTick: { show: false }, axisLine: { lineStyle: { color: r.izgara } },
        axisLabel: { color: r.soluk, fontSize: 11, interval: window.innerWidth < 700 ? 3 : 1 } },
      yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: r.izgara, opacity: 0.7 } }, axisLabel: { color: r.soluk, fontSize: 11 } },
      series: [{ type: "bar", data: saatlik.map((n) => n.count), itemStyle: { color: r.vurgu, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 22 }],
    };
  }

  function nabizMetni(ad, kayitlar) {
    return `${ad}: ` + (kayitlar.length ? kayitlar.map((k) => `${k.label} ${tamSayiFmt.format(k.count)}`).join(", ") : "veri yok");
  }

  async function nabziCiz(veri) {
    const ec = await echartsYukle();
    if (!durum.acik || durum.sekme !== "nabiz") return;
    nabizTemizle();
    const kartlar = [
      { id: "nabizSaatlik", ad: "Saatlere göre haber sayısı (son 24 saat)", opt: saatlikSecenek(veri.hourly),
        metin: `Saatlere göre haber sayısı, son 24 saat, toplam ${veri.total}: ` + veri.hourly.map((n) => `${saatliEtiket(n.hour_start)} ${n.count}`).join(", ") },
      { id: "nabizUlke", ad: "Ülkelere göre", opt: halkaSecenegi(veri.by_country, (k) => NABIZ_RENK.ulke[k.key] || "#6b7280"), metin: nabizMetni("Ülkelere göre dağılım", veri.by_country) },
      { id: "nabizKategori", ad: "Kategoriye göre", opt: halkaSecenegi(veri.by_category, (k, i) => NABIZ_RENK.kategori[i % NABIZ_RENK.kategori.length]), metin: nabizMetni("Kategoriye göre dağılım", veri.by_category) },
      { id: "nabizSinif", ad: "Önem seviyesine göre", opt: halkaSecenegi(veri.by_importance, (k) => NABIZ_RENK.sinif[k.key] || "#6b7280"), metin: nabizMetni("Önem seviyesine göre dağılım", veri.by_importance) },
    ];
    kartlar.forEach((k) => {
      const kap = $(k.id);
      kap.setAttribute("aria-label", k.metin);
      const c = ec.getInstanceByDom(kap) || ec.init(kap, null, { renderer: "canvas" });
      c.setOption(k.opt, true);
      durum.nabizGrafikleri.push(c);
      const metinKutusu = $(`${k.id}Metin`);
      if (metinKutusu) metinKutusu.textContent = k.metin;
    });
    if (!durum.nabizRo) {
      durum.nabizRo = new ResizeObserver(() => durum.nabizGrafikleri.forEach((c) => { if (!c.isDisposed()) c.resize(); }));
      durum.nabizRo.observe($("marketNabizPaneli"));
    }
  }

  function saatliEtiket(iso) {
    return new Intl.DateTimeFormat("tr-TR", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
  }

  async function nabziYukle(zorla) {
    const kutu = $("marketNabizKutusu");
    const benimIstegim = ++durum.istek;
    if (durum.iptal) durum.iptal.abort();
    const ac = new AbortController();
    durum.iptal = ac;
    durumGoster(kutu, "yukleniyor");
    $("marketNabizIcerik").hidden = true;
    try {
      const veri = await onbellekliGetir("nabiz", "/api/news/statistics?range=24h", ac.signal, zorla);
      if (benimIstegim !== durum.istek) return;
      if (!veri.total) {
        durumGoster(kutu, "bos", { kod: "bos", mesaj: "Son 24 saatte istatistik oluşturacak haber bulunamadı.", tekrar: true }, () => nabziYukle(true));
        return;
      }
      durumGoster(kutu, "hazir");
      $("marketNabizIcerik").hidden = false;
      $("marketNabizMeta").textContent = `Toplam ${tamSayiFmt.format(veri.total)} haber · Haberlerin ilk görüldüğü zamana göre (TSİ) · Güncellendi: ${tamZamanFmt.format(new Date(veri.generated_at))}`;
      await nabziCiz(veri);
    } catch (e) {
      if (e.tur === "iptal" || benimIstegim !== durum.istek) return;
      const b = hataBilgisi(e);
      const mesaj = b.kod === "yapilandirilmadi" ? "Haber istatistikleri şu anda alınamıyor." : b.mesaj.replace("Piyasa verisi", "Haber istatistikleri");
      durumGoster(kutu, "hata", { ...b, mesaj }, () => nabziYukle(true));
    }
  }

  /* ===================== Haber detay grafiği ===================== */
  const detay = { token: 0, iptal: null, chart: null, ro: null, oge: null, aralik: "1D", sembol: null, veri: null };

  function detayTemizle() {
    detay.token++;
    if (detay.iptal) detay.iptal.abort();
    if (detay.ro) { detay.ro.disconnect(); detay.ro = null; }
    if (detay.chart && !detay.chart.isDisposed()) detay.chart.dispose();
    detay.chart = null;
    detay.oge = null;
    const blok = $("detailMarket");
    if (blok) blok.hidden = true;
  }

  function haberEndeksi(oge) {
    if (!durum.config) return null;
    const kod = (oge && oge.ulke ? String(oge.ulke) : "US").toUpperCase();
    const u = durum.config.ulkeler.find((x) => x.kod === kod);
    if (!u) return null;
    const e = u.endeksler.find((x) => x.varsayilan) || u.endeksler[0];
    return e ? { id: e.id, ad: e.ad, ulkeAd: u.ad } : null;
  }

  async function detayYukle(zorla) {
    const oge = detay.oge;
    if (!oge) return;
    const benim = ++detay.token;
    if (detay.iptal) detay.iptal.abort();
    const ac = new AbortController();
    detay.iptal = ac;
    const kutu = $("detailMarketKutu");
    durumGoster(kutu, "yukleniyor");
    const mum = (durum.config.araliklar.find((a) => a.id === detay.aralik) || {}).varsayilan_mum_araligi;
    try {
      const veri = await onbellekliGetir(`h|${detay.sembol}|${detay.aralik}`,
        `/api/market/history?symbol=${encodeURIComponent(detay.sembol)}&range=${encodeURIComponent(detay.aralik)}${mum ? "&interval=" + mum : ""}`, ac.signal, zorla);
      if (benim !== detay.token) return;
      detay.veri = veri;
      durumGoster(kutu, "hazir");
      const ec = await echartsYukle();
      if (benim !== detay.token) return;
      const kap = $("detailMarketChart");
      let c = detay.chart;
      if (!c || c.isDisposed()) {
        c = ec.init(kap, null, { renderer: "canvas" });
        detay.chart = c;
        detay.ro = new ResizeObserver(() => { if (detay.chart && !detay.chart.isDisposed()) detay.chart.resize(); });
        detay.ro.observe(kap);
      }
      const haberSn = oge.ilkGorulme ? Math.floor(new Date(oge.ilkGorulme).getTime() / 1000) : null;
      const p = veri.points;
      const adim = p.length > 1 ? p[1].time - p[0].time : 0;
      const iceride = haberSn != null && Number.isFinite(haberSn) && haberSn >= p[0].time - adim && haberSn <= p[p.length - 1].time + adim;
      c.setOption(secenekOlustur(p, { tur: "cizgi", aralik: veri.range, kompakt: true, paraBirimi: veri.currency, haberSn: iceride ? haberSn : null, animasyon: true }), true);
      const o = ozetMetni(veri);
      const y = yonBilgisi(o.fark);
      const q = $("detailMarketAnlik");
      q.textContent = "";
      q.appendChild(el("span", "detail-market-deger", `${sayi(o.son.close)}${veri.currency ? " " + veri.currency : ""}`));
      const d = el("span", `market-anlik-degisim ${y.sinif}`, `${y.ok} ${isaretliSayi(o.fark)} (${isaretliSayi(o.yuzde)}%)`);
      d.setAttribute("aria-label", `${y.ad}: ${isaretliSayi(o.fark)}, yüzde ${isaretliSayi(o.yuzde)} bu aralıkta`);
      q.appendChild(d);
      const aciklama = `${veri.name}: son değer ${sayi(o.son.close)}, bu aralıkta ${y.ad} ${isaretliSayi(o.fark)} (yüzde ${isaretliSayi(o.yuzde)}).`;
      kap.setAttribute("aria-label", `${veri.name} fiyat grafiği. ${aciklama}` + (iceride ? " Haberin yayın zamanı grafikte dikey çizgiyle işaretli." : ""));
      $("detailMarketNot").textContent = [
        iceride ? "Kesikli dikey çizgi haberin görüldüğü zamanı gösterir." : "Haberin zamanı bu aralığın dışında kaldığı için işaretlenmedi.",
        veri.stale ? "Eski veri gösteriliyor." : "",
        veri.mock ? "TEST VERİSİ — gerçek piyasa verisi değildir." : "",
      ].filter(Boolean).join(" ");
    } catch (e) {
      if (e.tur === "iptal" || benim !== detay.token) return;
      durumGoster(kutu, e.kod === "veri_yok" ? "bos" : "hata", hataBilgisi(e), () => detayYukle(true));
      if (detay.chart && !detay.chart.isDisposed()) detay.chart.clear();
      $("detailMarketAnlik").textContent = "";
      $("detailMarketNot").textContent = "";
    }
  }

  async function detayGoster(oge) {
    detayTemizle();
    const blok = $("detailMarket");
    if (!blok) return;
    try {
      await configYukle();
    } catch (e) {
      return; // yapılandırma alınamadıysa bölümü hiç gösterme
    }
    if (!durum.config.yapilandirildi) return; // sağlayıcı yok: bölüm oluşturulmaz
    ilkKurulum();

    let sembol = null, ad = null, baslik, aciklama = "";
    if (oge.iliskiliSembol && /^[A-Z.\-]{1,8}$/.test(oge.iliskiliSembol)) {
      sembol = oge.iliskiliSembol;
      ad = sembol;
      baslik = `İlgili Piyasa Hareketi · ${sembol}`;
    } else {
      const e = haberEndeksi(oge);
      if (!e) return; // güvenilir bir sembol/endeks yok: bölüm oluşturulmaz
      sembol = e.id;
      ad = e.ad;
      baslik = `İlgili Piyasa Hareketi · ${e.ulkeAd}: ${e.ad}`;
      aciklama = "Bu haber belirli bir şirketle eşleştirilemedi; haberin ülkesinin ana endeksi gösteriliyor.";
    }
    detay.oge = oge;
    detay.sembol = sembol;
    detay.aralik = "1D";
    $("detailMarketBaslik").textContent = baslik;
    $("detailMarketAciklama").textContent = aciklama;
    $("detailMarketAciklama").hidden = !aciklama;
    blok.hidden = false;
    const kap = $("detailMarketAraliklar");
    kap.textContent = "";
    ["1D", "1W", "1M"].forEach((id) => {
      const a = durum.config.araliklar.find((x) => x.id === id);
      if (!a) return;
      const b = el("button", "market-mini", a.etiket);
      b.type = "button";
      b.setAttribute("aria-pressed", id === detay.aralik ? "true" : "false");
      b.setAttribute("aria-label", { "1D": "1 gün", "1W": "1 hafta", "1M": "1 ay" }[id]);
      b.addEventListener("click", () => {
        detay.aralik = id;
        kap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
        detayYukle(false);
      });
      kap.appendChild(b);
    });
    $("detailMarketChart").setAttribute("aria-label", `${ad} fiyat grafiği yükleniyor`);
    detayYukle(false);
  }

  /* ===================== Kurulum ===================== */
  function baslat() {
    const panel = $("marketPanel");
    if (!panel) return;

    $("marketToggle").addEventListener("click", () => panelAc(!durum.acik));
    const anaSekmeler = document.querySelector(".market-ana-sekmeler");
    sekmeKlavye(anaSekmeler);
    anaSekmeler.querySelectorAll('[role="tab"]').forEach((b) => b.addEventListener("click", () => { if (durum.sekme !== b.dataset.sekme) sekmeDegistir(b.dataset.sekme); else sekmeSec(anaSekmeler, b); }));
    sekmeKlavye($("marketUlkeSekmeleri"));
    kontrolleriBagla();

    // Görünür olunca (ve yalnızca bir kez) başlık özetini yükle: sayfa açılışında ağ isteği yok.
    if ("IntersectionObserver" in window) {
      durum.io = new IntersectionObserver((girdiler) => {
        girdiler.forEach((g) => {
          durum.gorunur = g.isIntersecting;
          if (g.isIntersecting && !durum.ozetYuklendi) {
            durum.ozetYuklendi = true;
            ozetiYukle();
          } else if (g.isIntersecting && durum.acik && durum.sekme === "fiyat" && Date.now() - durum.sonYuklenme > YENILEME_MS) {
            yukle(false, true);
          }
        });
      }, { rootMargin: "120px" });
      durum.io.observe(panel);
    } else {
      ozetiYukle();
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && durum.acik && durum.sekme === "fiyat" && Date.now() - durum.sonYuklenme > YENILEME_MS) yukle(false, true);
    });
    window.addEventListener("pagehide", () => { anaGrafigiTemizle(); nabizTemizle(); detayTemizle(); });

    if (depoOku(DEPO_ACIK) === "1") {
      // Kullanıcı önceki ziyarette paneli açık bırakmıştı.
      durum.ozetYuklendi = true;
      panelAc(true, false);
    }
  }

  function yukleHatasi() {
    const bilgi = hataBilgisi(durum.configHatasi || { tur: "ag" });
    durumGoster($("marketGrafikKutusu"), "hata", bilgi, () => { durum.configHatasi = null; configYukle().then(() => { ilkKurulum(); yukle(true); }).catch(yukleHatasi); });
  }

  /* Ana sayfa "Haberler" sekmesi görünürlüğü (Kaydedilenler'e geçince yenileme durur). */
  function gorunurluk(gorunur) {
    durum.gorunur = gorunur;
    if (!gorunur) {
      clearTimeout(durum.zamanlayici);
      if (durum.iptal) durum.iptal.abort();
    } else if (durum.acik && durum.sekme === "fiyat") {
      if (durum.chart && !durum.chart.isDisposed()) durum.chart.resize();
      if (Date.now() - durum.sonYuklenme > YENILEME_MS) yukle(false, true); else yenilemeyiPlanla();
    }
  }

  /* Tema değişince açık grafikleri güncel CSS renkleriyle yeniden çiz. */
  function temaDegisti() {
    if (durum.acik && durum.sekme === "fiyat" && durum.veri && durum.chart && !durum.chart.isDisposed()) anaGrafigiCiz(durum.veri, false);
    if (durum.acik && durum.sekme === "nabiz") {
      const kayit = istemciOnbellek.get("nabiz");
      if (kayit) nabziCiz(kayit.veri);
    }
    if (detay.oge && detay.veri && detay.chart && !detay.chart.isDisposed()) detayYukle(false);
  }

  return { baslat, gorunurluk, temaDegisti, detayGoster, detayTemizle };
})();
