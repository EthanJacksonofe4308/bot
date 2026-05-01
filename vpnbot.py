"""
🤖 Telegram Bot - Kiểm tra mạng VPN tại Trung Quốc (Full Version)
==================================================================
Yêu cầu:
    pip install python-telegram-bot requests speedtest-cli

Cách dùng:
    1. Lấy token từ @BotFather trên Telegram
    2. Đặt BOT_TOKEN bên dưới
    3. Chạy: python china_vpn_checker_bot.py
"""

import asyncio
import socket
import time
import json
import base64
import requests
from datetime import datetime
from urllib.parse import urlparse, unquote

import speedtest as speedtest_lib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ===================== CẤU HÌNH =====================
BOT_TOKEN  = "8734023025:AAEbSc8oiMY5t-P0cHTVjy-zYHp1hmql_40"   # 👈 Thay bằng token của bạn
ADMIN_IDS  = [6727174487]                       # 👈 Thêm Telegram user_id admin (vd: [123456789])
TIMEOUT    = 6                        # giây cho mỗi request kiểm tra
MAX_SUBS   = 10                       # tối đa số link sub mỗi user lưu
# ====================================================

# ConversationHandler states
WAITING_PING_HOST = 1

# Lưu sub link theo user { user_id: [{"name":..., "url":..., "proxies":[...]}, ...] }
user_subs: dict[int, list] = {}

# ==================== SITE LISTS ====================

BLOCKED_SITES = {
    "Google":    ("google.com",       443),
    "YouTube":   ("youtube.com",      443),
    "Facebook":  ("facebook.com",     443),
    "Instagram": ("instagram.com",    443),
    "Twitter/X": ("twitter.com",      443),
    "WhatsApp":  ("web.whatsapp.com", 443),
    "Telegram":  ("telegram.org",     443),
    "Wikipedia": ("wikipedia.org",    443),
    "GitHub":    ("github.com",       443),
    "Netflix":   ("netflix.com",      443),
    "Discord":   ("discord.com",      443),
    "Reddit":    ("reddit.com",       443),
    "Spotify":   ("spotify.com",      443),
    "TikTok":    ("tiktok.com",       443),
    "OpenAI":    ("openai.com",       443),
    "Claude":    ("claude.ai",        443),
}

ALLOWED_SITES = {
    "Baidu":   ("baidu.com",     443),
    "WeChat":  ("weixin.qq.com", 443),
    "Alibaba": ("alibaba.com",   443),
    "QQ":      ("qq.com",        443),
}

IP_APIS = [
    "https://api.ipify.org?format=json",
    "https://ip-api.com/json",
    "https://ipinfo.io/json",
]


# ==================== CORE FUNCTIONS ====================

def check_host(host: str, port: int, timeout: int = TIMEOUT) -> tuple[bool, float]:
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.time() - start) * 1000, 1)
    except Exception:
        return False, round((time.time() - start) * 1000, 1)


def get_public_ip() -> dict:
    for url in IP_APIS:
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                d = r.json()
                return {
                    "ip":      d.get("ip") or d.get("query", "N/A"),
                    "country": d.get("country") or d.get("countryCode", "N/A"),
                    "city":    d.get("city", "N/A"),
                    "org":     d.get("org") or d.get("isp", "N/A"),
                    "region":  d.get("region") or d.get("regionName", "N/A"),
                }
        except Exception:
            continue
    return {"ip": "Lỗi", "country": "?", "city": "?", "org": "?", "region": "?"}


def run_full_check() -> dict:
    blocked = {n: dict(zip(["ok", "ms"], check_host(h, p))) for n, (h, p) in BLOCKED_SITES.items()}
    allowed = {n: dict(zip(["ok", "ms"], check_host(h, p))) for n, (h, p) in ALLOWED_SITES.items()}
    ip_info = get_public_ip()

    ok_count = sum(1 for v in blocked.values() if v["ok"])
    total = len(blocked)
    ratio = ok_count / total

    if ratio >= 0.7:
        status, emoji = "VPN ĐANG HOẠT ĐỘNG TỐT", "🟢"
    elif ratio >= 0.3:
        status, emoji = "VPN HOẠT ĐỘNG YẾU / KHÔNG ỔN ĐỊNH", "🟡"
    else:
        status, emoji = "KHÔNG CÓ VPN / VPN BỊ CHẶN", "🔴"

    return {
        "blocked": blocked, "allowed": allowed, "ip_info": ip_info,
        "vpn_status": status, "vpn_emoji": emoji,
        "ok_count": ok_count, "total": total,
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


def do_ping(host: str, count: int = 4) -> dict:
    clean = host.replace("https://", "").replace("http://", "").split("/")[0]
    port = 443
    if ":" in clean:
        parts = clean.rsplit(":", 1)
        clean = parts[0]
        try:
            port = int(parts[1])
        except Exception:
            pass

    results = []
    for _ in range(count):
        ok, ms = check_host(clean, port, timeout=5)
        results.append(ms if ok else None)
        time.sleep(0.3)

    valid = [r for r in results if r is not None]
    lost = count - len(valid)
    return {
        "host": clean, "port": port, "count": count, "results": results,
        "min": min(valid) if valid else 0,
        "max": max(valid) if valid else 0,
        "avg": round(sum(valid) / len(valid), 1) if valid else 0,
        "loss": round(lost / count * 100),
        "success": len(valid),
    }


def do_speedtest() -> dict:
    try:
        st = speedtest_lib.Speedtest()
        st.get_best_server()
        server = st.results.server
        return {
            "ok": True,
            "download": round(st.download() / 1_000_000, 2),
            "upload":   round(st.upload()   / 1_000_000, 2),
            "ping":     round(st.results.ping, 1),
            "server":   f"{server.get('name','?')}, {server.get('country','?')}",
            "sponsor":  server.get("sponsor", "?"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def decode_sub_link(url: str) -> list[dict]:
    """Tải và giải mã link sub VPN (VMess/VLESS/SS/Trojan, Base64 hoặc plain)."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "ClashForWindows"})
        r.raise_for_status()
        content = r.text.strip()
        try:
            content = base64.b64decode(content + "==").decode("utf-8", errors="ignore")
        except Exception:
            pass

        proxies = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("vmess://"):
                try:
                    d = json.loads(base64.b64decode(line[8:] + "==").decode("utf-8", errors="ignore"))
                    proxies.append({"type": "VMess", "name": d.get("ps", "vmess"),
                                    "server": d.get("add", "?"), "port": d.get("port", "?"), "net": d.get("net", "tcp")})
                except Exception:
                    proxies.append({"type": "VMess", "name": line[:40], "server": "?", "port": "?", "net": "?"})
            elif line.startswith("vless://"):
                p = urlparse(line)
                proxies.append({"type": "VLESS", "name": unquote(p.fragment or p.hostname or "vless"),
                                "server": p.hostname or "?", "port": p.port or "?", "net": "?"})
            elif line.startswith("ss://"):
                p = urlparse(line)
                proxies.append({"type": "Shadowsocks", "name": unquote(p.fragment or p.hostname or "ss"),
                                "server": p.hostname or "?", "port": p.port or "?", "net": "?"})
            elif line.startswith("trojan://"):
                p = urlparse(line)
                proxies.append({"type": "Trojan", "name": unquote(p.fragment or p.hostname or "trojan"),
                                "server": p.hostname or "?", "port": p.port or "?", "net": "?"})
        return proxies or [{"error": "Không tìm thấy proxy nào trong sub link này."}]
    except Exception as e:
        return [{"error": str(e)}]


# ==================== FORMATTERS ====================

def fmt_check(r: dict) -> str:
    ip = r["ip_info"]
    lines = [
        "🌐 *BÁO CÁO KIỂM TRA MẠNG VPN*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 `{r['timestamp']}`",
        "",
        "📍 *THÔNG TIN IP*",
        f"  IP: `{ip['ip']}` | `{ip['country']}` - `{ip['city']}`",
        f"  ISP: `{ip['org']}`",
        "",
        f"{r['vpn_emoji']} *{r['vpn_status']}*",
        f"  Truy cập được: `{r['ok_count']}/{r['total']}` site quốc tế",
        "",
        "🚫 *SITE BỊ CHẶN (GFW)*",
    ]
    for name, info in r["blocked"].items():
        icon = "✅" if info["ok"] else "❌"
        spd = f" `{info['ms']}ms`" if info["ok"] else ""
        lines.append(f"  {icon} {name}{spd}")
    lines += ["", "🇨🇳 *SITE TRUNG QUỐC*"]
    for name, info in r["allowed"].items():
        icon = "✅" if info["ok"] else "❌"
        spd = f" `{info['ms']}ms`" if info["ok"] else ""
        lines.append(f"  {icon} {name}{spd}")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━",
              "💡 _✅ = kết nối được | ❌ = bị chặn_"]
    return "\n".join(lines)


def fmt_ping(result: dict) -> str:
    rows = []
    for i, ms in enumerate(result["results"], 1):
        rows.append(f"  #{i}: ✅ `{ms}ms`" if ms is not None else f"  #{i}: ❌ Timeout")
    loss = result["loss"]
    quality = ("🟢 Kết nối tốt" if loss == 0 else
               "🟡 Trung bình" if loss <= 25 else
               "🟠 Yếu" if loss <= 75 else "🔴 Mất kết nối")
    return (
        f"🏓 *PING KẾT QUẢ*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Host: `{result['host']}:{result['port']}`\n\n"
        + "\n".join(rows) +
        f"\n\n📊 *Thống kê:*\n"
        f"  Min: `{result['min']}ms` | Max: `{result['max']}ms` | Avg: `{result['avg']}ms`\n"
        f"  Mất gói: `{loss}%` ({result['count']-result['success']}/{result['count']})\n"
        f"  {quality}"
    )


def fmt_speed(r: dict) -> str:
    if not r["ok"]:
        return f"❌ Đo tốc độ thất bại!\nLỗi: `{r['error']}`"
    dl = r["download"]
    rating = ("🟢 Rất nhanh" if dl >= 100 else "🟢 Nhanh" if dl >= 50
              else "🟡 Trung bình" if dl >= 20 else "🟠 Chậm" if dl >= 5 else "🔴 Rất chậm")
    return (
        f"⚡ *KẾT QUẢ ĐO TỐC ĐỘ MẠNG*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📥 Download: `{r['download']} Mbps`\n"
        f"📤 Upload:   `{r['upload']} Mbps`\n"
        f"🏓 Ping:     `{r['ping']} ms`\n"
        f"🖥️ Server:  `{r['sponsor']}` — `{r['server']}`\n\n"
        f"Đánh giá: {rating}"
    )


# ==================== KEYBOARDS ====================

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Kiểm tra VPN",     callback_data="check_now"),
         InlineKeyboardButton("🌐 Xem IP",           callback_data="check_ip")],
        [InlineKeyboardButton("🏓 Ping",             callback_data="menu_ping"),
         InlineKeyboardButton("⚡ Speedtest",        callback_data="menu_speed")],
        [InlineKeyboardButton("🔗 Sub Link",         callback_data="menu_sub")],
        [InlineKeyboardButton("ℹ️ Hướng dẫn",       callback_data="help")],
    ])

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu chính", callback_data="back_start")]])

def kb_recheck(action: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Làm lại", callback_data=action),
         InlineKeyboardButton("🔙 Menu",    callback_data="back_start")],
    ])


# ==================== COMMAND HANDLERS ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Bot Kiểm Tra VPN Trung Quốc*\n\n"
        "🔍 Kiểm tra VPN & GFW\n"
        "🌐 IP công khai & vị trí\n"
        "🏓 Ping host/IP bất kỳ\n"
        "⚡ Đo tốc độ Download/Upload\n"
        "🔗 Quản lý Sub Link VPN\n"
    )
    await update.message.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Đang kiểm tra, vui lòng chờ...")
    results = await asyncio.get_event_loop().run_in_executor(None, run_full_check)
    await msg.edit_text(fmt_check(results), parse_mode="Markdown", reply_markup=kb_recheck("check_now"))


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Đang lấy thông tin IP...")
    ip = await asyncio.get_event_loop().run_in_executor(None, get_public_ip)
    text = (
        f"📍 *THÔNG TIN IP CỦA BẠN*\n━━━━━━━━━━━━━━━━\n"
        f"🔹 IP: `{ip['ip']}`\n🔹 Quốc gia: `{ip['country']}`\n"
        f"🔹 Vùng: `{ip['region']}`\n🔹 Thành phố: `{ip['city']}`\n"
        f"🔹 ISP: `{ip['org']}`\n\n"
        f"💡 _IP tại TQ → VPN chưa bật | IP nước khác → VPN OK_"
    )
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())


async def cmd_ping_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        return await _exec_ping(update.message, context.args[0])
    await update.message.reply_text(
        "🏓 *Nhập host/IP để ping:*\n_Ví dụ: `google.com` hoặc `8.8.8.8:443`_\n\nGõ /cancel để hủy.",
        parse_mode="Markdown",
    )
    return WAITING_PING_HOST


async def cmd_ping_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _exec_ping(update.message, update.message.text.strip())
    return ConversationHandler.END


async def _exec_ping(message, host: str):
    msg = await message.reply_text(f"🏓 Đang ping `{host}`...", parse_mode="Markdown")
    result = await asyncio.get_event_loop().run_in_executor(None, do_ping, host)
    await msg.edit_text(fmt_ping(result), parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="back_start")]]))


async def cmd_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "⚡ *Đang đo tốc độ...*\n_Mất khoảng 30–60 giây, vui lòng chờ._", parse_mode="Markdown"
    )
    result = await asyncio.get_event_loop().run_in_executor(None, do_speedtest)
    await msg.edit_text(fmt_speed(result), parse_mode="Markdown", reply_markup=kb_recheck("menu_speed"))


async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /sub              → Xem danh sách
    /sub add Name | URL → Thêm sub link
    /sub del <số>     → Xóa sub link
    /sub view <số>    → Xem proxy trong sub link
    """
    uid = update.effective_user.id
    args = context.args

    if not args:
        await _show_sub_menu(update.message, uid)
        return

    cmd = args[0].lower()

    # --- ADD ---
    if cmd == "add":
        full = " ".join(args[1:])
        if len(user_subs.get(uid, [])) >= MAX_SUBS:
            await update.message.reply_text(f"❌ Đã đạt giới hạn {MAX_SUBS} sub link!")
            return
        if "|" in full:
            parts = full.split("|", 1)
            name, url = parts[0].strip(), parts[1].strip()
        else:
            url = full.strip()
            name = f"Sub {len(user_subs.get(uid, [])) + 1}"
        if not url.startswith("http"):
            await update.message.reply_text("❌ URL không hợp lệ (phải bắt đầu http/https).")
            return
        msg = await update.message.reply_text(f"⏳ Đang tải sub link `{name}`...", parse_mode="Markdown")
        proxies = await asyncio.get_event_loop().run_in_executor(None, decode_sub_link, url)
        if proxies and "error" in proxies[0]:
            await msg.edit_text(f"❌ Lỗi: `{proxies[0]['error']}`", parse_mode="Markdown")
            return
        if uid not in user_subs:
            user_subs[uid] = []
        user_subs[uid].append({"name": name, "url": url, "proxies": proxies})
        lines = [f"  • [{p.get('type','?')}] `{p.get('name','?')}`" for p in proxies[:12]]
        if len(proxies) > 12:
            lines.append(f"  _... và {len(proxies)-12} proxy khác_")
        await msg.edit_text(
            f"✅ *Đã thêm: {name}*\nTổng `{len(proxies)}` proxy\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Danh sách sub", callback_data="menu_sub")]]),
        )

    # --- DEL ---
    elif cmd == "del":
        subs = user_subs.get(uid, [])
        try:
            idx = int(args[1]) - 1
            removed = subs.pop(idx)
            await update.message.reply_text(f"🗑️ Đã xóa sub link: *{removed['name']}*", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Cú pháp: `/sub del <số thứ tự>`", parse_mode="Markdown")

    # --- VIEW ---
    elif cmd == "view":
        subs = user_subs.get(uid, [])
        try:
            idx = int(args[1]) - 1
            s = subs[idx]
            proxies = s.get("proxies", [])
            lines = [f"  • [{p.get('type','?')}] `{p.get('name','?')}` — `{p.get('server','?')}:{p.get('port','?')}`"
                     for p in proxies[:20]]
            if len(proxies) > 20:
                lines.append(f"  _... và {len(proxies)-20} proxy khác_")
            await update.message.reply_text(
                f"📋 *{s['name']}*\nURL: `{s['url'][:60]}...`\nTổng: `{len(proxies)}` proxy\n\n" + "\n".join(lines),
                parse_mode="Markdown",
            )
        except Exception:
            await update.message.reply_text("❌ Cú pháp: `/sub view <số thứ tự>`", parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "🔗 *Sub Link - Hướng dẫn:*\n"
            "`/sub` — Xem danh sách\n"
            "`/sub add Tên | URL` — Thêm sub\n"
            "`/sub view 1` — Xem proxy trong sub #1\n"
            "`/sub del 1` — Xóa sub #1",
            parse_mode="Markdown",
        )


async def _show_sub_menu(message, uid: int):
    subs = user_subs.get(uid, [])
    lines = [f"🔗 *QUẢN LÝ SUB LINK VPN*\n\nBạn có `{len(subs)}/{MAX_SUBS}` sub link.\n"]
    if subs:
        for i, s in enumerate(subs, 1):
            lines.append(f"  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy")
        lines += [
            "",
            "Lệnh:\n"
            "• `/sub add Tên | URL` — Thêm mới\n"
            "• `/sub view 1` — Xem proxy sub #1\n"
            "• `/sub del 1` — Xóa sub #1",
        ]
    else:
        lines.append("Chưa có sub link nào.\n\nThêm bằng:\n`/sub add Tên | URL`")
    await message.reply_text("\n".join(lines), parse_mode="Markdown",
                             reply_markup=kb_back())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *HƯỚNG DẪN SỬ DỤNG*\n\n"
        "*Lệnh chính:*\n"
        "• `/start` — Menu chính\n"
        "• `/check` — Kiểm tra VPN & GFW\n"
        "• `/ip` — Xem IP công khai\n"
        "• `/ping <host>` — Ping host/IP\n"
        "• `/speed` — Đo tốc độ mạng\n"
        "• `/sub` — Quản lý sub link VPN\n"
        "• `/help` — Hướng dẫn này\n\n"
        "*Sub Link:*\n"
        "• `/sub add HK Free | https://...` — Thêm\n"
        "• `/sub view 1` — Xem proxy trong sub #1\n"
        "• `/sub del 1` — Xóa sub #1\n"
        "Hỗ trợ: VMess · VLESS · Shadowsocks · Trojan\n\n"
        "*Đọc kết quả VPN:*\n"
        "🟢 Tốt — 🟡 Yếu — 🔴 Không có VPN\n"
        "✅ = kết nối được | ❌ = bị chặn\n\n"
        "⚠️ _Bot kiểm tra từ máy chủ chạy bot, không phải thiết bị của bạn_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Đã hủy.")
    return ConversationHandler.END


# ==================== CALLBACK HANDLER ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    if data == "check_now":
        await query.edit_message_text("⏳ Đang kiểm tra mạng, vui lòng chờ...")
        results = await asyncio.get_event_loop().run_in_executor(None, run_full_check)
        await query.edit_message_text(fmt_check(results), parse_mode="Markdown", reply_markup=kb_recheck("check_now"))

    elif data == "check_ip":
        await query.edit_message_text("🔍 Đang lấy thông tin IP...")
        ip = await asyncio.get_event_loop().run_in_executor(None, get_public_ip)
        text = (
            f"📍 *THÔNG TIN IP*\n━━━━━━━━━━━━━━━━\n"
            f"🔹 IP: `{ip['ip']}`\n🔹 Quốc gia: `{ip['country']}`\n"
            f"🔹 Vùng: `{ip['region']}`\n🔹 Thành phố: `{ip['city']}`\n"
            f"🔹 ISP: `{ip['org']}`"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())

    elif data == "menu_ping":
        await query.edit_message_text(
            "🏓 *Ping Host*\n\nDùng lệnh:\n`/ping google.com`\n`/ping 8.8.8.8`\n`/ping 1.1.1.1:53`",
            parse_mode="Markdown", reply_markup=kb_back(),
        )

    elif data == "menu_speed":
        await query.edit_message_text("⚡ *Đang đo tốc độ...*\n_Mất 30–60 giây, vui lòng chờ._", parse_mode="Markdown")
        result = await asyncio.get_event_loop().run_in_executor(None, do_speedtest)
        await query.edit_message_text(fmt_speed(result), parse_mode="Markdown", reply_markup=kb_recheck("menu_speed"))

    elif data == "menu_sub":
        subs = user_subs.get(uid, [])
        lines = [f"🔗 *QUẢN LÝ SUB LINK VPN*\n\nBạn có `{len(subs)}/{MAX_SUBS}` sub link.\n"]
        if subs:
            for i, s in enumerate(subs, 1):
                lines.append(f"  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy")
            lines.append("\n`/sub view 1` | `/sub del 1` | `/sub add Tên | URL`")
        else:
            lines.append("Chưa có sub link.\nThêm: `/sub add Tên | URL`")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu chính", callback_data="back_start")]]),
        )

    elif data == "help":
        text = (
            "📖 *HƯỚNG DẪN*\n\n"
            "• `/check` — Kiểm tra VPN\n• `/ip` — Xem IP\n"
            "• `/ping <host>` — Ping\n• `/speed` — Speedtest\n"
            "• `/sub` — Sub link VPN\n\n"
            "🟢 VPN tốt | 🟡 Yếu | 🔴 Không VPN\n"
            "✅ = OK | ❌ = Bị chặn"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())

    elif data == "back_start":
        await query.edit_message_text(
            "👋 *Bot Kiểm Tra VPN Trung Quốc*\nChọn tính năng:",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


# ==================== MAIN ====================

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Lỗi: Bạn chưa đặt BOT_TOKEN!")
        print("   → Lấy token từ @BotFather trên Telegram rồi đặt vào biến BOT_TOKEN.")
        return

    print("🤖 Bot đang khởi động...")
    app = Application.builder().token(BOT_TOKEN).build()

    ping_conv = ConversationHandler(
        entry_points=[CommandHandler("ping", cmd_ping_entry)],
        states={WAITING_PING_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_ping_text)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("ip",     cmd_ip))
    app.add_handler(CommandHandler("speed",  cmd_speed))
    app.add_handler(CommandHandler("sub",    cmd_sub))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(ping_conv)
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ Bot đang chạy! Gửi /start trên Telegram để bắt đầu.")
    print("   Nhấn Ctrl+C để dừng.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
