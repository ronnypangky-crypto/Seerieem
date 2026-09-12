import os, time, requests, json
from datetime import datetime, timezone, timedelta

# ── Config ─────────────────────────────────────────────
TG_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
NEWS_API    = os.environ.get("NEWS_API_KEY", "")  # dari newsapi.org (gratis)

WIB = timezone(timedelta(hours=7))

# ── Daftar Saham yang Dipantau ──────────────────────────
# Tambah/hapus sesuai kebutuhan
WATCHLIST = [
    "BUMI", "BRPT", "DEWA", "FIRE", "KONI",
    "MITI", "MICE", "PGAS", "TINS", "ANTM",
    "BSDE", "WIKA", "ADHI", "PTPP", "WSKT"
]

# ── State ───────────────────────────────────────────────
last_update_id  = 0
volume_history  = {}  # {ticker: [vol1, vol2, ...]}
price_history   = {}  # {ticker: [price1, price2, ...]}
alerted_today   = set()  # ticker yang sudah dapat alert hari ini

# ── Helpers ─────────────────────────────────────────────
def now_str():
    return datetime.now(WIB).strftime("%d/%m/%Y %H:%M")

def fmt_harga(val):
    return f"Rp {val:,.0f}".replace(",", ".")

def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"TG Error: {e}")

# ── Fetch Data Saham (Yahoo Finance) ────────────────────
def get_stock_data(ticker):
    """Ambil data saham dari Yahoo Finance"""
    try:
        symbol = f"{ticker}.JK"
        url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "interval": "1d",
            "range":    "30d",
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        meta    = result[0].get("meta", {})
        quotes  = result[0].get("indicators", {}).get("quote", [{}])[0]
        timestamps = result[0].get("timestamp", [])

        closes  = quotes.get("close", [])
        volumes = quotes.get("volume", [])
        opens   = quotes.get("open", [])

        if not closes or not volumes:
            return None

        # Filter None values
        closes  = [c for c in closes  if c is not None]
        volumes = [v for v in volumes if v is not None]

        if len(closes) < 5 or len(volumes) < 5:
            return None

        return {
            "ticker":       ticker,
            "price":        closes[-1],
            "price_prev":   closes[-2],
            "volume":       volumes[-1],
            "volume_avg":   sum(volumes[:-1]) / len(volumes[:-1]),
            "closes":       closes,
            "volumes":      volumes,
            "currency":     meta.get("currency", "IDR"),
        }
    except Exception as e:
        print(f"⚠️ Gagal fetch {ticker}: {e}")
        return None

# ── Indikator Teknikal ──────────────────────────────────
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains  = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    k, ema = 2 / (period + 1), prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_macd(prices):
    if len(prices) < 26: return 0, 0
    macd   = calc_ema(prices, 12) - calc_ema(prices, 26)
    signal = calc_ema([macd], 9)
    return macd, signal

# ── Fetch Berita ────────────────────────────────────────
def get_berita(ticker):
    """Ambil berita terkait saham dari Google News RSS"""
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+saham+Indonesia&hl=id&gl=ID&ceid=ID:id"
        r   = requests.get(url, timeout=10)
        
        # Parse RSS sederhana
        import re
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
        titles = [t for t in titles if ticker in t or "saham" in t.lower()][:3]
        
        if not titles:
            return []
        return titles
    except:
        return []

def get_sentimen(berita_list):
    """Analisa sentimen sederhana dari judul berita"""
    positif = ["naik", "untung", "profit", "laba", "tumbuh", "meningkat", 
                "rally", "bullish", "rekomendasi beli", "target", "dividen"]
    negatif = ["turun", "rugi", "merosot", "anjlok", "bearish", "jual",
                "koreksi", "delisting", "gagal", "masalah"]
    
    pos_count = 0
    neg_count = 0
    
    for berita in berita_list:
        berita_lower = berita.lower()
        pos_count += sum(1 for k in positif if k in berita_lower)
        neg_count += sum(1 for k in negatif if k in berita_lower)
    
    if pos_count > neg_count:
        return "😊 POSITIF"
    elif neg_count > pos_count:
        return "😟 NEGATIF"
    return "😐 NETRAL"

# ── Analisa & Signal ─────────────────────────────────────
def analisa_saham(ticker):
    data = get_stock_data(ticker)
    if not data:
        return None

    price       = data["price"]
    price_prev  = data["price_prev"]
    volume      = data["volume"]
    volume_avg  = data["volume_avg"]
    closes      = data["closes"]
    volumes     = data["volumes"]

    # Filter harga 50-200 perak (saham gocap)
    if price < 50 or price > 300:
        return None

    # Indikator
    rsi      = calc_rsi(closes)
    ema9     = calc_ema(closes, 9)
    ema21    = calc_ema(closes, 21)
    macd, signal = calc_macd(closes)
    vol_ratio= volume / volume_avg if volume_avg > 0 else 1
    chg_pct  = (price - price_prev) / price_prev * 100 if price_prev > 0 else 0

    sinyal   = 0
    reasons  = []

    # 1. Volume Spike — yang paling penting untuk gocap!
    if vol_ratio >= 5:
        sinyal += 3
        reasons.append(f"🚨 Volume SPIKE {vol_ratio:.0f}x!!!")
    elif vol_ratio >= 3:
        sinyal += 2
        reasons.append(f"🔥 Volume naik {vol_ratio:.1f}x")
    elif vol_ratio >= 2:
        sinyal += 1
        reasons.append(f"📈 Volume naik {vol_ratio:.1f}x")

    # 2. RSI oversold
    if rsi < 35:
        sinyal += 2
        reasons.append(f"RSI={rsi:.0f} oversold✅")
    elif rsi < 45:
        sinyal += 1
        reasons.append(f"RSI={rsi:.0f}✅")

    # 3. EMA trend
    if ema9 > ema21:
        sinyal += 1
        reasons.append("EMA9>EMA21✅")

    # 4. MACD
    if macd > signal:
        sinyal += 1
        reasons.append("MACD✅")

    # 5. Harga naik hari ini
    if chg_pct > 0:
        sinyal += 1
        reasons.append(f"Naik +{chg_pct:.1f}%✅")

    return {
        "ticker":    ticker,
        "price":     price,
        "chg_pct":   chg_pct,
        "volume":    volume,
        "vol_ratio": vol_ratio,
        "rsi":       rsi,
        "ema9":      ema9,
        "ema21":     ema21,
        "sinyal":    sinyal,
        "reasons":   reasons,
    }

def send_signal(result):
    ticker    = result["ticker"]
    price     = result["price"]
    chg_pct   = result["chg_pct"]
    vol_ratio = result["vol_ratio"]
    rsi       = result["rsi"]
    sinyal    = result["sinyal"]
    reasons   = result["reasons"]

    # Target dan stop loss
    target    = price * 1.08  # target +8%
    stoploss  = price * 0.95  # stop loss -5%

    # Ambil berita
    berita    = get_berita(ticker)
    sentimen  = get_sentimen(berita)

    # Berita list
    berita_str = ""
    if berita:
        for b in berita[:2]:
            berita_str += f"• {b[:60]}...\n"
    else:
        berita_str = "• Tidak ada berita terkini\n"

    # Emoji volume
    if vol_ratio >= 5:
        vol_emoji = "🚨🚨🚨"
    elif vol_ratio >= 3:
        vol_emoji = "🔥🔥"
    else:
        vol_emoji = "📈"

    chg_emoji = "🟢" if chg_pct >= 0 else "🔴"

    msg = (
        f"📊 *SIGNAL SAHAM: {ticker}*\n"
        f"⏰ {now_str()}\n\n"
        f"💰 Harga: *{fmt_harga(price)}*\n"
        f"{chg_emoji} Perubahan: {chg_pct:+.1f}%\n"
        f"{vol_emoji} Volume: {vol_ratio:.1f}x rata-rata\n\n"
        f"*Indikator:*\n"
        f"{chr(10).join(reasons)}\n\n"
        f"*📰 Berita:*\n"
        f"{berita_str}"
        f"*Sentimen: {sentimen}*\n\n"
        f"🎯 Target: {fmt_harga(target)} (+8%)\n"
        f"🛑 Stop Loss: {fmt_harga(stoploss)} (-5%)\n\n"
        f"⚠️ _Bukan saran investasi — lakukan riset sendiri!_"
    )
    send_telegram(msg)
    print(f"📊 Signal terkirim: {ticker} | {sinyal} sinyal | Vol {vol_ratio:.1f}x")

# ── Scan Semua Saham ─────────────────────────────────────
def scan_saham():
    global alerted_today
    now = datetime.now(WIB)

    # Reset alert harian setiap tengah malam
    if now.hour == 0 and now.minute < 5:
        alerted_today = set()

    hari = now.weekday()  # 0=Senin, 4=Jumat, 5=Sabtu, 6=Minggu

    # Sabtu & Minggu libur
    if hari in [5, 6]:
        print(f"[{now_str()}] Akhir pekan — libur trading!")
        return

    # Jumat tutup jam 16:00
    if hari == 4 and now.hour >= 16:
        print(f"[{now_str()}] Jumat sudah tutup!")
        return

    # Scan hanya jam trading: 09:00-16:00 WIB
    if not (9 <= now.hour < 16):
        print(f"[{now_str()}] Di luar jam trading — skip scan")
        return

    print(f"[{now_str()}] Scanning {len(WATCHLIST)} saham...")

    candidates = []
    for ticker in WATCHLIST:
        result = analisa_saham(ticker)
        if result and result["sinyal"] >= 3:
            candidates.append(result)
        time.sleep(0.5)  # jangan spam Yahoo Finance

    # Sort by sinyal terkuat
    candidates.sort(key=lambda x: x["sinyal"], reverse=True)

    # Kirim signal untuk yang belum dapat alert hari ini
    for result in candidates[:3]:  # max 3 signal per scan
        ticker = result["ticker"]
        if ticker not in alerted_today:
            send_signal(result)
            alerted_today.add(ticker)
            time.sleep(2)

    if not candidates:
        print(f"[{now_str()}] Tidak ada kandidat yang memenuhi syarat")

# ── Telegram Commands ────────────────────────────────────
def get_tg_updates():
    global last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 5},
            timeout=10
        )
        if r.json().get("ok"):
            return r.json().get("result", [])
    except: pass
    return []

def handle_command(text):
    text = text.strip().lower()

    if text == "/scan":
        send_telegram("🔍 Scanning saham sekarang...")
        scan_saham()

    elif text.startswith("/cek "):
        ticker = text[5:].strip().upper()
        send_telegram(f"🔍 Menganalisa {ticker}...")
        result = analisa_saham(ticker)
        if result:
            send_signal(result)
        else:
            send_telegram(f"❌ Tidak ada data untuk {ticker} atau harga di luar range!")

    elif text == "/watchlist":
        wl = "\n".join([f"• {t}" for t in WATCHLIST])
        send_telegram(f"📋 *Watchlist Saham:*\n{wl}")

    elif text == "/status":
        now = datetime.now(WIB)
        jam_trading = "✅ JAM TRADING" if 9 <= now.hour < 16 else "❌ LUAR JAM TRADING"
        send_telegram(
            f"📊 *Saham Bot Status*\n"
            f"⏰ {now_str()}\n"
            f"📈 {jam_trading}\n"
            f"👁️ Watchlist: {len(WATCHLIST)} saham\n"
            f"🔔 Alert hari ini: {len(alerted_today)} saham\n\n"
            f"*Command:*\n"
            f"/scan — scan semua saham sekarang\n"
            f"/cek TICKER — analisa 1 saham\n"
            f"/watchlist — lihat daftar saham\n"
            f"/status — status bot"
        )
    else:
        send_telegram(
            f"❓ Command tidak dikenal.\n\n"
            f"*Command:*\n"
            f"/scan — scan semua saham\n"
            f"/cek TICKER — analisa 1 saham (contoh: /cek BBCA)\n"
            f"/watchlist — lihat watchlist\n"
            f"/status — status bot"
        )

def check_tg_commands():
    updates = get_tg_updates()
    for update in updates:
        global last_update_id
        last_update_id = update["update_id"]
        msg     = update.get("message", {})
        text    = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if text and text.startswith("/") and chat_id == str(TG_CHAT_ID):
            print(f"📱 Command: {text}")
            handle_command(text)

# ── Main ─────────────────────────────────────────────────
def main():
    print("🚀 Saham Bot dimulai...")
    send_telegram(
        f"🚀 *Saham Signal Bot AKTIF!*\n"
        f"👁️ Memantau {len(WATCHLIST)} saham gocap\n"
        f"🔔 Auto scan setiap 30 menit jam trading\n\n"
        f"*Command:*\n"
        f"/scan — scan semua saham\n"
        f"/cek TICKER — analisa 1 saham\n"
        f"/watchlist — lihat watchlist\n"
        f"/status — status bot"
    )

    tick = 0
    while True:
        try:
            check_tg_commands()
            # Scan otomatis setiap 30 menit
            if tick % (30 * 60 // 5) == 0:
                scan_saham()

            # Notif Jumat jam 16:00 — pasar tutup
            now_check = datetime.now(WIB)
            if now_check.weekday() == 4 and now_check.hour == 16 and now_check.minute == 0 and tick % 12 == 0:
                send_telegram(
                    "🔔 *Pasar Tutup!*\n"
                    "📅 Jumat 16:00 WIB — Bursa tutup!\n"
                    "😴 Bot istirahat sampai Senin pagi 09:00 WIB.\n"
                    "Selamat weekend! 🎉"
                )

            # Notif Senin jam 09:00 — pasar buka
            if now_check.weekday() == 0 and now_check.hour == 9 and now_check.minute == 0 and tick % 12 == 0:
                send_telegram(
                    "🔔 *Pasar Buka!*\n"
                    "📅 Senin 09:00 WIB — Bursa buka!\n"
                    "📊 Bot mulai scan saham...\n"
                    "Semangat trading! 💪"
                )
            tick += 1
            time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
