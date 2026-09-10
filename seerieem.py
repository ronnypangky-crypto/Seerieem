import os, json, requests, time, base64
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

# ── Config ─────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")  # format: username/repo
GITHUB_FILE   = "keuangan.json"

WIB = timezone(timedelta(hours=7))

# ── State ───────────────────────────────────────────────
last_update_id = 0
data_keuangan  = {
    "saldo": 0,
    "transaksi": []
}

# ── Helpers ─────────────────────────────────────────────
def now_str():
    return datetime.now(WIB).strftime("%d/%m/%Y %H:%M")

def fmt(val):
    return f"Rp {abs(val):,.0f}".replace(",", ".")

def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    text = f"💰 *Keuangan Bot*\n{msg}\n⏰ {now_str()}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"TG Error: {e}")

# ── GitHub Storage ───────────────────────────────────────
def load_from_github():
    global data_keuangan
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("⚠️ GITHUB_TOKEN atau GITHUB_REPO belum diset!")
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            },
            timeout=10
        )
        if r.status_code == 200:
            content = r.json().get("content", "")
            decoded = base64.b64decode(content).decode("utf-8")
            data_keuangan = json.loads(decoded)
            print(f"✅ Data loaded dari GitHub — saldo: {fmt(data_keuangan['saldo'])}")
        elif r.status_code == 404:
            print("📂 File belum ada di GitHub — mulai dari 0")
            save_to_github()
    except Exception as e:
        print(f"⚠️ Gagal load dari GitHub: {e}")

def save_to_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    try:
        # Cek apakah file sudah ada (untuk dapat SHA)
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            },
            timeout=10
        )
        sha = r.json().get("sha", "") if r.status_code == 200 else ""

        content = base64.b64encode(
            json.dumps(data_keuangan, indent=2, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")

        payload = {
            "message": f"Update keuangan {now_str()}",
            "content": content,
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            },
            json=payload,
            timeout=15
        )
        if r.status_code in [200, 201]:
            print("✅ Data tersimpan ke GitHub!")
        else:
            print(f"⚠️ Gagal simpan: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"⚠️ Gagal save ke GitHub: {e}")

# ── Telegram Commands ────────────────────────────────────
def get_tg_updates():
    global last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 5},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            return data.get("result", [])
    except:
        pass
    return []

def handle_command(text):
    global data_keuangan
    text = text.strip()
    parts = text.split(" ", 2)
    cmd = parts[0].lower()

    # /masuk 50000 gaji
    if cmd == "/masuk":
        if len(parts) < 2:
            send_telegram("Format: /masuk JUMLAH KETERANGAN\nContoh: /masuk 500000 gaji")
            return
        try:
            jumlah = float(parts[1].replace(".", "").replace(",", ""))
            ket    = parts[2] if len(parts) > 2 else "tidak ada keterangan"
        except:
            send_telegram("❌ Jumlah tidak valid!")
            return

        data_keuangan["saldo"] += jumlah
        data_keuangan["transaksi"].append({
            "type":   "masuk",
            "jumlah": jumlah,
            "ket":    ket,
            "waktu":  now_str()
        })
        save_to_github()
        send_telegram(
            f"✅ *Pemasukan dicatat!*\n"
            f"💚 +{fmt(jumlah)} — {ket}\n"
            f"💰 Saldo sekarang: *{fmt(data_keuangan['saldo'])}*"
        )

    # /keluar 20000 makan
    elif cmd == "/keluar":
        if len(parts) < 2:
            send_telegram("Format: /keluar JUMLAH KETERANGAN\nContoh: /keluar 20000 makan siang")
            return
        try:
            jumlah = float(parts[1].replace(".", "").replace(",", ""))
            ket    = parts[2] if len(parts) > 2 else "tidak ada keterangan"
        except:
            send_telegram("❌ Jumlah tidak valid!")
            return

        data_keuangan["saldo"] -= jumlah
        data_keuangan["transaksi"].append({
            "type":   "keluar",
            "jumlah": jumlah,
            "ket":    ket,
            "waktu":  now_str()
        })
        save_to_github()
        send_telegram(
            f"✅ *Pengeluaran dicatat!*\n"
            f"❤️ -{fmt(jumlah)} — {ket}\n"
            f"💰 Saldo sekarang: *{fmt(data_keuangan['saldo'])}*"
        )

    # /saldo
    elif cmd == "/saldo":
        send_telegram(f"💰 *Saldo sekarang: {fmt(data_keuangan['saldo'])}*")

    # /laporan
    elif cmd == "/laporan":
        transaksi = data_keuangan["transaksi"]
        if not transaksi:
            send_telegram("📊 Belum ada transaksi!")
            return

        # Filter bulan ini
        bulan_ini = datetime.now(WIB).strftime("%m/%Y")
        trans_bulan = [t for t in transaksi if t["waktu"].endswith(bulan_ini) or bulan_ini in t["waktu"]]

        total_masuk  = sum(t["jumlah"] for t in trans_bulan if t["type"] == "masuk")
        total_keluar = sum(t["jumlah"] for t in trans_bulan if t["type"] == "keluar")
        net          = total_masuk - total_keluar

        # 10 transaksi terakhir
        last10 = transaksi[-10:]
        lines  = ""
        for t in reversed(last10):
            emoji = "💚" if t["type"] == "masuk" else "❤️"
            tanda = "+" if t["type"] == "masuk" else "-"
            lines += f"{emoji} {tanda}{fmt(t['jumlah'])} — {t['ket']} ({t['waktu']})\n"

        emoji_net = "🟢" if net >= 0 else "🔴"
        send_telegram(
            f"📊 *Laporan Bulan Ini*\n\n"
            f"💚 Total Masuk: {fmt(total_masuk)}\n"
            f"❤️ Total Keluar: {fmt(total_keluar)}\n"
            f"{emoji_net} Net: {fmt(net)}\n"
            f"💰 Saldo: {fmt(data_keuangan['saldo'])}\n\n"
            f"*10 Transaksi Terakhir:*\n{lines}"
        )

    # /hapus — hapus transaksi terakhir
    elif cmd == "/hapus":
        if not data_keuangan["transaksi"]:
            send_telegram("❌ Tidak ada transaksi yang bisa dihapus!")
            return
        last = data_keuangan["transaksi"].pop()
        if last["type"] == "masuk":
            data_keuangan["saldo"] -= last["jumlah"]
        else:
            data_keuangan["saldo"] += last["jumlah"]
        save_to_github()
        send_telegram(
            f"🗑️ *Transaksi terakhir dihapus!*\n"
            f"{'💚' if last['type'] == 'masuk' else '❤️'} {last['ket']} — {fmt(last['jumlah'])}\n"
            f"💰 Saldo sekarang: {fmt(data_keuangan['saldo'])}"
        )

    # /reset — reset semua data
    elif cmd == "/reset":
        data_keuangan = {"saldo": 0, "transaksi": []}
        save_to_github()
        send_telegram("🔄 *Semua data direset!* Saldo kembali ke 0.")

    else:
        send_telegram(
            f"❓ Command tidak dikenal.\n\n"
            f"*Command:*\n"
            f"/masuk JUMLAH KETERANGAN\n"
            f"/keluar JUMLAH KETERANGAN\n"
            f"/saldo — cek saldo\n"
            f"/laporan — laporan bulan ini\n"
            f"/hapus — hapus transaksi terakhir\n"
            f"/reset — reset semua data"
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
    print("🚀 Keuangan Bot dimulai...")
    load_from_github()
    send_telegram(
        f"🚀 *Keuangan Bot AKTIF!*\n"
        f"💰 Saldo: {fmt(data_keuangan['saldo'])}\n\n"
        f"*Command:*\n"
        f"/masuk JUMLAH KETERANGAN\n"
        f"/keluar JUMLAH KETERANGAN\n"
        f"/saldo — cek saldo\n"
        f"/laporan — laporan bulan ini\n"
        f"/hapus — hapus transaksi terakhir"
    )
    while True:
        try:
            check_tg_commands()
            time.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
