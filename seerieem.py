import os, time, requests, json, base64, re
from datetime import datetime, timezone, timedelta

# ── Config ─────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")
GITHUB_FILE  = "saham_data.json"

WIB = timezone(timedelta(hours=7))

# ── Filter Harga Saham ──────────────────────────────────
MIN_HARGA    = float(os.environ.get("MIN_HARGA",    "50"))    # min harga saham
MAX_HARGA    = float(os.environ.get("MAX_HARGA",    "1000"))   # max harga saham
MIN_SINYAL   = int(os.environ.get("MIN_SINYAL",    "3"))      # min sinyal untuk alert
SCAN_MENIT   = int(os.environ.get("SCAN_MENIT",    "20"))     # scan tiap X menit

# Default watchlist — saham gocap aktif
DEFAULT_WATCHLIST = [
    "BUMI", "BRPT", "DEWA", "FIRE", "KONI",
    "MITI", "MICE", "WIRG", "TINS", "ANTM",
    "WIKA", "ADHI", "PTPP", "WSKT", "MDKA",
    "INCO", "VALE", "SMCB", "BSDE", "LPKR",
]

# ── State ───────────────────────────────────────────────
last_update_id = 0
alerted_today  = set()
data = {
    "watchlist": [],
    "posisi": {}
}

# ── Helpers ─────────────────────────────────────────────
def now_str():
    return datetime.now(WIB).strftime("%d/%m/%Y %H:%M")

def fmt(val):
    return f"Rp {abs(val):,.0f}".replace(",", ".")

def log(msg):
    print(f"[{datetime.now(WIB).strftime('%H:%M:%S')}] {msg}", flush=True)

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
        log(f"TG Error: {e}")

# ── GitHub Storage ───────────────────────────────────────
def load_data():
    global data
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log("⚠️ GitHub tidak dikonfigurasi!")
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10
        )
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            data = json.loads(content)
            log(f"✅ Data loaded — {len(data['watchlist'])} saham dipantau")
            # Tambah default watchlist kalau masih kosong
            if not data["watchlist"]:
                data["watchlist"] = DEFAULT_WATCHLIST.copy()
                save_data()
                log(f"📋 Default watchlist ditambahkan: {len(data['watchlist'])} saham")
        else:
            log("📂 File belum ada — pakai default watchlist")
            data["watchlist"] = DEFAULT_WATCHLIST.copy()
            save_data()
    except Exception as e:
        log(f"⚠️ Gagal load: {e}")

def save_data():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10
        )
        sha = r.json().get("sha", "") if r.status_code == 200 else ""
        content = base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False).encode()
        ).decode()
        payload = {"message": f"Update saham {now_str()}", "content": content}
        if sha:
            payload["sha"] = sha
        requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            json=payload, timeout=15
        )
        log("✅ Data tersimpan ke GitHub!")
    except Exception as e:
        log(f"⚠️ Gagal save: {e}")

# ── Fetch Data Saham ────────────────────────────────────
def get_stock_data(ticker):
    try:
        symbol = f"{ticker}.JK"
        url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = requests.get(url, params={"interval": "1d", "range": "30d"},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return None
        quotes  = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes  = [c for c in quotes.get("close",  []) if c is not None]
        volumes = [v for v in quotes.get("volume", []) if v is not None]
        if len(closes) < 5 or len(volumes) < 5:
            return None
        return {
            "ticker":     ticker,
            "price":      closes[-1],
            "price_prev": closes[-2],
            "volume":     volumes[-1],
            "vol_avg":    sum(volumes[:-1]) / len(volumes[:-1]),
            "closes":     closes,
            "volumes":    volumes,
        }
    except Exception as e:
        log(f"⚠️ Gagal fetch {ticker}: {e}")
        return None

# ── Indikator ────────────────────────────────────────────
def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    gains  = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

def calc_ema(prices, period):
    if len(prices) < period: return prices[-1]
    k, ema = 2/(period+1), prices[0]
    for p in prices[1:]: ema = p*k + ema*(1-k)
    return ema

def calc_macd(prices):
    if len(prices) < 26: return 0, 0
    macd = calc_ema(prices, 12) - calc_ema(prices, 26)
    return macd, calc_ema([macd], 9)

# ── Fetch Berita ────────────────────────────────────────
def get_berita(ticker):
    berita = []
    sources = [
        f"https://news.google.com/rss/search?q={ticker}+saham&hl=id&gl=ID&ceid=ID:id",
        f"https://www.kontan.co.id/search?term={ticker}&rss=1",
    ]
    for url in sources:
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
            titles += re.findall(r'<title>(.*?)</title>', r.text)
            for t in titles:
                t = t.strip()
                if ticker in t.upper() or "saham" in t.lower():
                    if t not in berita and len(t) > 10:
                        berita.append(t[:70])
            if berita:
                break
        except:
            continue
    return berita[:3]

def get_sentimen(berita_list):
    positif = ["naik", "untung", "profit", "laba", "tumbuh", "meningkat",
               "rally", "bullish", "beli", "dividen", "akuisisi"]
    negatif = ["turun", "rugi", "merosot", "anjlok", "bearish",
               "koreksi", "delisting", "gagal", "masalah", "default"]
    pos = sum(1 for b in berita_list for k in positif if k in b.lower())
    neg = sum(1 for b in berita_list for k in negatif if k in b.lower())
    if pos > neg: return "😊 POSITIF"
    if neg > pos: return "😟 NEGATIF"
    return "😐 NETRAL"

# ── Analisa ─────────────────────────────────────────────
def analisa(ticker):
    d = get_stock_data(ticker)
    if not d: return None

    price, price_prev = d["price"], d["price_prev"]
    volume, vol_avg   = d["volume"], d["vol_avg"]
    closes, volumes   = d["closes"], d["volumes"]

    rsi        = calc_rsi(closes)
    ema9       = calc_ema(closes, 9)
    ema21      = calc_ema(closes, 21)
    macd, sig  = calc_macd(closes)
    vol_ratio  = volume / vol_avg if vol_avg > 0 else 1
    chg_pct    = (price - price_prev) / price_prev * 100

    sinyal  = 0
    reasons = []

    # Volume spike — prioritas utama
    if vol_ratio >= 5:
        sinyal += 3; reasons.append(f"🚨 Volume SPIKE {vol_ratio:.0f}x!!!")
    elif vol_ratio >= 3:
        sinyal += 2; reasons.append(f"🔥 Volume {vol_ratio:.1f}x")
    elif vol_ratio >= 2:
        sinyal += 1; reasons.append(f"📈 Volume {vol_ratio:.1f}x")

    if rsi < 35:   sinyal += 2; reasons.append(f"RSI={rsi:.0f} oversold✅")
    elif rsi < 45: sinyal += 1; reasons.append(f"RSI={rsi:.0f}✅")

    if ema9 > ema21: sinyal += 1; reasons.append("EMA9>EMA21✅")
    if macd > sig:   sinyal += 1; reasons.append("MACD✅")
    if chg_pct > 0:  sinyal += 1; reasons.append(f"+{chg_pct:.1f}%✅")

    return {
        "ticker": ticker, "price": price, "chg_pct": chg_pct,
        "vol_ratio": vol_ratio, "rsi": rsi,
        "ema9": ema9, "ema21": ema21,
        "sinyal": sinyal, "reasons": reasons,
    }

def send_signal(result, label="📊 SIGNAL"):
    ticker    = result["ticker"]
    price     = result["price"]
    chg_pct   = result["chg_pct"]
    vol_ratio = result["vol_ratio"]
    sinyal    = result["sinyal"]
    reasons   = result["reasons"]
    target    = price * 1.08
    sl        = price * 0.95

    berita   = get_berita(ticker)
    sentimen = get_sentimen(berita)
    berita_str = "\n".join([f"• {b}" for b in berita]) if berita else "• Tidak ada berita"

    vol_emoji = "🚨" if vol_ratio >= 5 else "🔥" if vol_ratio >= 3 else "📈"
    chg_emoji = "🟢" if chg_pct >= 0 else "🔴"

    send_telegram(
        f"{label}: *{ticker}*\n"
        f"⏰ {now_str()}\n\n"
        f"💰 Harga: *{fmt(price)}*\n"
        f"{chg_emoji} Perubahan: {chg_pct:+.1f}%\n"
        f"{vol_emoji} Volume: {vol_ratio:.1f}x rata-rata\n\n"
        f"*Indikator ({sinyal} sinyal):*\n"
        f"{chr(10).join(reasons)}\n\n"
        f"*📰 Berita:*\n{berita_str}\n"
        f"*Sentimen: {sentimen}*\n\n"
        f"🎯 Target: {fmt(target)} (+8%)\n"
        f"🛑 Stop Loss: {fmt(sl)} (-5%)\n\n"
        f"⚠️ _Bukan saran investasi!_"
    )

# ── Pantau Posisi ────────────────────────────────────────
def cek_posisi():
    """Cek P/L semua posisi yang dipegang"""
    if not data["posisi"]:
        return
    for ticker, pos in list(data["posisi"].items()):
        d = get_stock_data(ticker)
        if not d: continue
        curr      = d["price"]
        modal     = pos["modal"]
        harga_beli= pos["harga_beli"]
        qty       = modal / harga_beli
        pl_pct    = (curr - harga_beli) / harga_beli * 100
        pl_idr    = (curr - harga_beli) * qty
        emoji     = "🟢" if pl_pct >= 0 else "🔴"

        # Notif kalau profit >= 8% atau loss >= 5%
        if pl_pct >= 8:
            send_telegram(
                f"🎯 *TARGET PROFIT: {ticker}*\n"
                f"💰 Harga: {fmt(curr)}\n"
                f"🟢 P/L: +{pl_pct:.1f}% (+{fmt(pl_idr)})\n"
                f"Pertimbangkan untuk jual! 😄"
            )
        elif pl_pct <= -5:
            send_telegram(
                f"🛑 *STOP LOSS: {ticker}*\n"
                f"💰 Harga: {fmt(curr)}\n"
                f"🔴 P/L: {pl_pct:.1f}% ({fmt(pl_idr)})\n"
                f"Pertimbangkan cut loss! ⚠️"
            )
        time.sleep(0.5)

# ── Scan Watchlist ───────────────────────────────────────
def scan_watchlist():
    now = datetime.now(WIB)
    hari = now.weekday()
    jam  = now.hour * 60 + now.minute

    if hari in [5, 6]:
        log("Akhir pekan — libur!")
        return
    if hari == 4 and now.hour >= 16:
        log("Jumat sudah tutup!")
        return
    if not (8*60+30 <= jam < 16*60):
        log("Di luar jam trading")
        return

    if not data["watchlist"]:
        log("Watchlist kosong!")
        return

    log(f"Scanning {len(data['watchlist'])} saham...")
    candidates = []
    for ticker in data["watchlist"]:
        result = analisa(ticker)
        if result and result["sinyal"] >= MIN_SINYAL:
            candidates.append(result)
        time.sleep(0.5)

    candidates.sort(key=lambda x: x["sinyal"], reverse=True)
    for result in candidates[:3]:
        if result["ticker"] not in alerted_today:
            send_signal(result)
            alerted_today.add(result["ticker"])
            time.sleep(2)

    if not candidates:
        log("Tidak ada kandidat")

# ── Jam Notif ────────────────────────────────────────────
sent_notif = set()

def cek_notif_jadwal():
    now  = datetime.now(WIB)
    hari = now.weekday()
    key  = now.strftime("%Y%m%d%H%M")

    if hari < 5 and now.hour == 8 and now.minute == 30 and key not in sent_notif:
        sent_notif.add(key)
        send_telegram("🔔 *Pasar Buka!*\n📅 08:30 WIB — Bursa buka!\n📊 Bot mulai scan saham...\nSemangat trading! 💪")

    if hari == 4 and now.hour == 16 and now.minute == 0 and key not in sent_notif:
        sent_notif.add(key)
        send_telegram("🔔 *Pasar Tutup!*\n📅 Jumat 16:00 WIB — Bursa tutup!\n😴 Sampai Senin pagi!\nSelamat weekend! 🎉")

    # Reset alert harian
    if now.hour == 0 and now.minute == 0:
        alerted_today.clear()

# ── Command Handler ──────────────────────────────────────
def handle_command(text):
    parts = text.strip().split()
    cmd   = parts[0].lower()

    # /pantau TICKER
    if cmd == "/pantau":
        if len(parts) < 2:
            send_telegram("Format: /pantau TICKER\nContoh: /pantau WIRG")
            return
        ticker = parts[1].upper()
        if ticker in data["watchlist"]:
            send_telegram(f"⚠️ {ticker} sudah ada di watchlist!")
            return
        # Validasi ticker
        d = get_stock_data(ticker)
        if not d:
            send_telegram(f"❌ Saham {ticker} tidak ditemukan di Yahoo Finance!")
            return
        data["watchlist"].append(ticker)
        save_data()
        send_telegram(
            f"✅ *{ticker} ditambahkan ke watchlist!*\n"
            f"💰 Harga sekarang: {fmt(d['price'])}\n"
            f"📋 Total watchlist: {len(data['watchlist'])} saham"
        )

    # /hapus TICKER
    elif cmd == "/hapus":
        if len(parts) < 2:
            send_telegram("Format: /hapus TICKER\nContoh: /hapus WIRG")
            return
        ticker = parts[1].upper()
        if ticker not in data["watchlist"]:
            send_telegram(f"❌ {ticker} tidak ada di watchlist!")
            return
        data["watchlist"].remove(ticker)
        save_data()
        send_telegram(f"✅ *{ticker} dihapus dari watchlist!*")

    # /beli TICKER MODAL
    elif cmd == "/beli":
        if len(parts) < 3:
            send_telegram("Format: /beli TICKER MODAL\nContoh: /beli WIRG 500000")
            return
        ticker = parts[1].upper()
        try:
            modal = float(parts[2].replace(".", "").replace(",", ""))
        except:
            send_telegram("❌ Modal tidak valid!")
            return
        d = get_stock_data(ticker)
        if not d:
            send_telegram(f"❌ Saham {ticker} tidak ditemukan!")
            return
        harga_beli = d["price"]
        data["posisi"][ticker] = {
            "modal":      modal,
            "harga_beli": harga_beli,
            "waktu":      now_str()
        }
        save_data()
        qty = modal / harga_beli
        send_telegram(
            f"✅ *Posisi {ticker} dicatat!*\n"
            f"💰 Harga beli: {fmt(harga_beli)}\n"
            f"📦 Qty estimasi: {qty:.0f} lot\n"
            f"💵 Modal: {fmt(modal)}\n"
            f"🎯 Target: {fmt(harga_beli * 1.08)} (+8%)\n"
            f"🛑 Stop Loss: {fmt(harga_beli * 0.95)} (-5%)"
        )

    # /jual TICKER
    elif cmd == "/jual":
        if len(parts) < 2:
            send_telegram("Format: /jual TICKER\nContoh: /jual WIRG")
            return
        ticker = parts[1].upper()
        if ticker not in data["posisi"]:
            send_telegram(f"❌ Tidak ada posisi {ticker}!")
            return
        pos  = data["posisi"][ticker]
        d    = get_stock_data(ticker)
        curr = d["price"] if d else pos["harga_beli"]
        pl_pct = (curr - pos["harga_beli"]) / pos["harga_beli"] * 100
        pl_idr = (curr - pos["harga_beli"]) * (pos["modal"] / pos["harga_beli"])
        emoji  = "🟢" if pl_pct >= 0 else "🔴"
        del data["posisi"][ticker]
        save_data()
        send_telegram(
            f"✅ *Posisi {ticker} ditutup!*\n"
            f"💰 Harga jual: {fmt(curr)}\n"
            f"💰 Harga beli: {fmt(pos['harga_beli'])}\n"
            f"{emoji} P/L: {pl_pct:+.1f}% ({fmt(pl_idr)})\n"
            f"📅 Masuk: {pos['waktu']}\n"
            f"📅 Keluar: {now_str()}"
        )

    # /posisi
    elif cmd == "/posisi":
        if not data["posisi"]:
            send_telegram("📊 Tidak ada posisi aktif!")
            return
        msg = "📊 *Posisi Aktif:*\n\n"
        total_modal = 0
        total_pl    = 0
        for ticker, pos in data["posisi"].items():
            d    = get_stock_data(ticker)
            curr = d["price"] if d else pos["harga_beli"]
            pl_pct = (curr - pos["harga_beli"]) / pos["harga_beli"] * 100
            pl_idr = (curr - pos["harga_beli"]) * (pos["modal"] / pos["harga_beli"])
            emoji  = "🟢" if pl_pct >= 0 else "🔴"
            msg += f"{emoji} *{ticker}*: {fmt(curr)} | {pl_pct:+.1f}% ({fmt(pl_idr)})\n"
            total_modal += pos["modal"]
            total_pl    += pl_idr
            time.sleep(0.3)
        net_emoji = "🟢" if total_pl >= 0 else "🔴"
        msg += f"\n{net_emoji} *Total P/L: {fmt(total_pl)}*"
        send_telegram(msg)

    # /cek TICKER
    elif cmd == "/cek":
        if len(parts) < 2:
            send_telegram("Format: /cek TICKER\nContoh: /cek BBCA")
            return
        ticker = parts[1].upper()
        send_telegram(f"🔍 Menganalisa *{ticker}*...")
        result = analisa(ticker)
        if result:
            send_signal(result, f"📊 Analisa {ticker}")
        else:
            send_telegram(f"❌ Tidak ada data untuk {ticker}!")

    # /scan
    elif cmd == "/scan":
        send_telegram("🔍 Scanning watchlist sekarang...")
        scan_watchlist()

    # /watchlist
    elif cmd == "/watchlist":
        if not data["watchlist"]:
            send_telegram("📋 Watchlist kosong!\nGunakan /pantau TICKER untuk tambah saham.")
            return
        wl = "\n".join([f"• {t}" for t in data["watchlist"]])
        send_telegram(f"📋 *Watchlist ({len(data['watchlist'])} saham):*\n{wl}")

    # /status
    elif cmd == "/status":
        now  = datetime.now(WIB)
        hari = now.weekday()
        jam  = now.hour * 60 + now.minute
        hari_nama = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"][hari]
        if hari in [5, 6]:
            status = "❌ LIBUR AKHIR PEKAN"
        elif 8*60+30 <= jam < 16*60:
            status = "✅ JAM TRADING"
        else:
            status = "❌ LUAR JAM TRADING"

        send_telegram(
            f"📊 *Saham Bot Status*\n"
            f"⏰ {now_str()} ({hari_nama})\n"
            f"📈 {status}\n"
            f"👁️ Watchlist: {len(data['watchlist'])} saham\n"
            f"💼 Posisi aktif: {len(data['posisi'])} saham\n"
            f"🔔 Alert hari ini: {len(alerted_today)}\n\n"
            f"*Command:*\n"
            f"/pantau TICKER — tambah ke watchlist\n"
            f"/hapus TICKER — hapus dari watchlist\n"
            f"/beli TICKER MODAL — catat posisi beli\n"
            f"/jual TICKER — catat posisi jual\n"
            f"/posisi — lihat semua posisi\n"
            f"/cek TICKER — analisa 1 saham\n"
            f"/scan — scan watchlist sekarang\n"
            f"/watchlist — lihat watchlist"
        )

    # /config — lihat dan edit settings
    elif cmd == "/config":
        send_telegram(
            f"⚙️ *Settings Saham Bot*\n\n"
            f"💰 Harga min: {fmt(MIN_HARGA)}\n"
            f"💰 Harga max: {fmt(MAX_HARGA)}\n"
            f"📊 Min sinyal: {MIN_SINYAL}\n"
            f"⏱️ Scan tiap: {SCAN_MENIT} menit\n\n"
            f"*Edit via Railway Variables:*\n"
            f"`MIN_HARGA` — harga minimum saham\n"
            f"`MAX_HARGA` — harga maximum saham\n"
            f"`MIN_SINYAL` — minimum sinyal (1-5)\n"
            f"`SCAN_MENIT` — interval scan (menit)"
        )

    else:
        send_telegram(
            f"❓ Command tidak dikenal.\n\n"
            f"*Command:*\n"
            f"/pantau TICKER — tambah watchlist\n"
            f"/hapus TICKER — hapus watchlist\n"
            f"/beli TICKER MODAL — catat beli\n"
            f"/jual TICKER — catat jual\n"
            f"/posisi — lihat posisi\n"
            f"/cek TICKER — analisa saham\n"
            f"/scan — scan sekarang\n"
            f"/watchlist — lihat watchlist\n"
            f"/config — lihat settings\n"
            f"/status — status bot"
        )

# ── Telegram Updates ─────────────────────────────────────
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

def check_tg_commands():
    for update in get_tg_updates():
        global last_update_id
        last_update_id = update["update_id"]
        msg     = update.get("message", {})
        text    = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if text and text.startswith("/") and chat_id == str(TG_CHAT_ID):
            log(f"📱 Command: {text}")
            handle_command(text)

# ── Main ─────────────────────────────────────────────────
def main():
    log("🚀 Saham Bot v2 dimulai...")
    load_data()
    send_telegram(
        f"🚀 *Saham Signal Bot AKTIF!*\n"
        f"👁️ Watchlist: {len(data['watchlist'])} saham\n"
        f"💼 Posisi: {len(data['posisi'])} saham\n"
        f"⏰ Jam trading: 08:30-16:00 WIB\n\n"
        f"*Command:*\n"
        f"/pantau TICKER — tambah watchlist\n"
        f"/beli TICKER MODAL — catat posisi\n"
        f"/scan — scan sekarang\n"
        f"/status — status lengkap"
    )

    tick = 0
    while True:
        try:
            check_tg_commands()
            cek_notif_jadwal()
            # Scan & cek posisi setiap SCAN_MENIT menit
            if tick % (SCAN_MENIT * 60 // 5) == 0:
                scan_watchlist()
                cek_posisi()
            tick += 1
            time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"❌ Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
