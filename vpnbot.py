"""
🤖 Telegram Bot - Kiểm tra VPN Trung Quốc (Full Version v2)
============================================================
Tính năng mới:
  ✅ Tự động cập nhật sub link định kỳ
  ✅ Ping tất cả node trong sub link
  ✅ Menu inline đầy đủ, không cần gõ lệnh
  ✅ Nút bấm cho mọi thao tác

Cài đặt:
    pip install python-telegram-bot requests speedtest-cli
"""

import asyncio
import socket
import time
import json
import base64
import requests
import yaml
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
BOT_TOKEN            = ""
ADMIN_IDS            = []
TIMEOUT              = 6       # giây timeout kiểm tra host
MAX_SUBS             = 10      # tối đa sub link mỗi user
PING_COUNT           = 4       # số lần ping mỗi node
MAX_NODES_PING       = 30      # tối đa node ping 1 lần (tránh timeout)
# Sub link luôn được fetch lại mỗi khi dùng lệnh (view/ping/update)
# ====================================================

# States cho ConversationHandler
WAITING_PING_HOST = 1

# { user_id: [{"name":str, "url":str, "proxies":list, "updated_at":str}, ...] }
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
        with socket.create_connection((host, int(port)), timeout=timeout):
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


def do_ping_single(host: str, port: int, count: int = PING_COUNT) -> dict:
    """Ping 1 host, trả về kết quả thống kê."""
    results = []
    for _ in range(count):
        ok, ms = check_host(host, port, timeout=4)
        results.append(ms if ok else None)
        time.sleep(0.2)
    valid = [r for r in results if r is not None]
    lost = count - len(valid)
    return {
        "host": host, "port": port,
        "results": results,
        "min": min(valid) if valid else 0,
        "max": max(valid) if valid else 0,
        "avg": round(sum(valid) / len(valid), 1) if valid else 0,
        "loss": round(lost / count * 100),
        "success": len(valid),
        "count": count,
    }


def do_ping_host(host_raw: str) -> dict:
    """Ping từ input text của user (tự parse host:port)."""
    clean = host_raw.replace("https://", "").replace("http://", "").split("/")[0]
    port = 443
    if ":" in clean:
        parts = clean.rsplit(":", 1)
        clean = parts[0]
        try:
            port = int(parts[1])
        except Exception:
            pass
    return do_ping_single(clean, port)


def ping_all_nodes(proxies: list) -> list[dict]:
    """Ping tất cả node trong sub link, trả về list kết quả."""
    results = []
    nodes = proxies[:MAX_NODES_PING]
    for p in nodes:
        server = p.get("server", "")
        port   = p.get("port", 443)
        name   = p.get("name", "?")
        ptype  = p.get("type", "?")
        if not server or server == "?":
            results.append({"name": name, "type": ptype, "server": server,
                            "port": port, "ok": False, "avg": 0, "loss": 100})
            continue
        try:
            ok, ms = check_host(str(server), int(port), timeout=4)
            results.append({
                "name": name, "type": ptype, "server": server, "port": port,
                "ok": ok, "avg": ms if ok else 0, "loss": 0 if ok else 100,
            })
        except Exception:
            results.append({"name": name, "type": ptype, "server": server,
                            "port": port, "ok": False, "avg": 0, "loss": 100})
    return results


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


def _parse_clash_yaml(content: str) -> list[dict]:
    """Parse Clash YAML format (proxies: [...])"""
    proxies = []
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return []
        clash_proxies = data.get("proxies") or data.get("Proxies") or []
        for p in clash_proxies:
            if not isinstance(p, dict):
                continue
            ptype = str(p.get("type", "?")).upper()
            # Chuẩn hóa type
            type_map = {
                "SS": "SS", "SHADOWSOCKS": "SS",
                "VMESS": "VMess", "VLESS": "VLESS",
                "TROJAN": "Trojan", "HYSTERIA": "Hysteria",
                "HYSTERIA2": "Hysteria2", "TUIC": "TUIC",
                "SOCKS5": "SOCKS5", "HTTP": "HTTP",
            }
            ptype = type_map.get(ptype, ptype)
            proxies.append({
                "type":   ptype,
                "name":   str(p.get("name", "?")),
                "server": str(p.get("server", "?")),
                "port":   p.get("port", 443),
                "net":    str(p.get("network", p.get("net", "?"))),
            })
    except Exception:
        pass
    return proxies


def _parse_v2ray_lines(content: str) -> list[dict]:
    """Parse V2Ray plain/base64 lines (vmess:// vless:// ss:// trojan://)"""
    proxies = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("vmess://"):
            try:
                d = json.loads(base64.b64decode(line[8:] + "==").decode("utf-8", errors="ignore"))
                proxies.append({
                    "type": "VMess", "name": d.get("ps", "vmess"),
                    "server": d.get("add", "?"), "port": d.get("port", 443),
                    "net": d.get("net", "tcp"),
                })
            except Exception:
                pass
        elif line.startswith("vless://"):
            p = urlparse(line)
            proxies.append({
                "type": "VLESS", "name": unquote(p.fragment or p.hostname or "vless"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "?",
            })
        elif line.startswith("ss://"):
            p = urlparse(line)
            proxies.append({
                "type": "SS", "name": unquote(p.fragment or p.hostname or "ss"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "?",
            })
        elif line.startswith("trojan://"):
            p = urlparse(line)
            proxies.append({
                "type": "Trojan", "name": unquote(p.fragment or p.hostname or "trojan"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "?",
            })
        elif line.startswith("hysteria2://") or line.startswith("hy2://"):
            p = urlparse(line)
            proxies.append({
                "type": "Hysteria2", "name": unquote(p.fragment or p.hostname or "hy2"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "udp",
            })
        elif line.startswith("hysteria://"):
            p = urlparse(line)
            proxies.append({
                "type": "Hysteria", "name": unquote(p.fragment or p.hostname or "hysteria"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "udp",
            })
        elif line.startswith("tuic://"):
            p = urlparse(line)
            proxies.append({
                "type": "TUIC", "name": unquote(p.fragment or p.hostname or "tuic"),
                "server": p.hostname or "?", "port": p.port or 443, "net": "udp",
            })
    return proxies


def decode_sub_link(url: str) -> list[dict]:
    """
    Tải & giải mã sub link VPN.
    Hỗ trợ: Clash YAML, VMess, VLESS, SS, Trojan, Hysteria2, TUIC (Base64 hoặc plain)
    """
    try:
        headers = {
            "User-Agent": "ClashForWindows/0.20.39",
            "Accept": "*/*",
        }
        r = requests.get(url, timeout=12, headers=headers)
        r.raise_for_status()
        content = r.text.strip()

        # 1. Thử parse Clash YAML trước (có chữ "proxies:")
        if "proxies:" in content or "Proxies:" in content:
            proxies = _parse_clash_yaml(content)
            if proxies:
                return proxies

        # 2. Thử decode Base64 rồi parse lại
        try:
            decoded = base64.b64decode(content + "==").decode("utf-8", errors="ignore")
            # Kiểm tra decoded có phải Clash YAML không
            if "proxies:" in decoded or "Proxies:" in decoded:
                proxies = _parse_clash_yaml(decoded)
                if proxies:
                    return proxies
            # Thử parse V2Ray lines từ decoded
            proxies = _parse_v2ray_lines(decoded)
            if proxies:
                return proxies
        except Exception:
            pass

        # 3. Thử parse V2Ray lines từ content gốc
        proxies = _parse_v2ray_lines(content)
        if proxies:
            return proxies

        return [{"error": "Không tìm thấy proxy nào. Sub link có thể dùng định dạng khác hoặc đã hết hạn."}]
    except Exception as e:
        return [{"error": str(e)}]


def fetch_sub_fresh(sub: dict) -> dict:
    """Tải lại proxy từ URL, cập nhật sub dict."""
    proxies = decode_sub_link(sub["url"])
    if proxies and "error" not in proxies[0]:
        sub["proxies"] = proxies
        sub["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return sub


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


def fmt_ping_single(result: dict) -> str:
    rows = []
    for i, ms in enumerate(result["results"], 1):
        rows.append(f"  #{i}: ✅ `{ms}ms`" if ms is not None else f"  #{i}: ❌ Timeout")
    loss = result["loss"]
    quality = ("🟢 Kết nối tốt" if loss == 0 else
               "🟡 Trung bình"  if loss <= 25 else
               "🟠 Yếu"         if loss <= 75 else "🔴 Mất kết nối")
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


def fmt_ping_nodes(sub_name: str, node_results: list, total_nodes: int) -> str:
    ok_nodes    = [n for n in node_results if n["ok"]]
    fail_nodes  = [n for n in node_results if not n["ok"]]

    # Sắp xếp node OK theo ping tăng dần
    ok_nodes.sort(key=lambda x: x["avg"])

    lines = [
        f"🏓 *PING NODE — {sub_name}*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Đã test: `{len(node_results)}/{total_nodes}` node",
        f"✅ Sống: `{len(ok_nodes)}` | ❌ Chết: `{len(fail_nodes)}`",
        "",
        "📶 *Node sống (sắp xếp theo ping):*",
    ]
    if ok_nodes:
        for n in ok_nodes:
            bar = "🟢" if n["avg"] < 150 else "🟡" if n["avg"] < 300 else "🔴"
            lines.append(f"  {bar} `{n['avg']}ms` [{n['type']}] {n['name'][:35]}")
    else:
        lines.append("  _Không có node nào kết nối được_")

    if fail_nodes:
        lines += ["", "💀 *Node chết:*"]
        for n in fail_nodes[:10]:
            lines.append(f"  ❌ [{n['type']}] {n['name'][:35]}")
        if len(fail_nodes) > 10:
            lines.append(f"  _... và {len(fail_nodes)-10} node chết khác_")

    if total_nodes > MAX_NODES_PING:
        lines += ["", f"⚠️ _Chỉ test {MAX_NODES_PING}/{total_nodes} node đầu tiên_"]

    lines += ["", f"🕐 `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`"]
    return "\n".join(lines)


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
        [InlineKeyboardButton("🔍 Kiểm tra VPN",  callback_data="check_now"),
         InlineKeyboardButton("🌐 Xem IP",        callback_data="check_ip")],
        [InlineKeyboardButton("🏓 Ping Host",     callback_data="menu_ping"),
         InlineKeyboardButton("⚡ Speedtest",     callback_data="menu_speed")],
        [InlineKeyboardButton("🔗 Sub Link VPN",  callback_data="menu_sub")],
        [InlineKeyboardButton("ℹ️ Hướng dẫn",    callback_data="help")],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu chính", callback_data="back_start")]])


def kb_recheck(action: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Làm lại",  callback_data=action),
         InlineKeyboardButton("🔙 Menu",     callback_data="back_start")],
    ])


def kb_sub_list(uid: int):
    """Keyboard danh sách sub với nút Ping & Cập nhật cho từng sub."""
    subs = user_subs.get(uid, [])
    rows = []
    for i, s in enumerate(subs):
        rows.append([
            InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"sub_view_{i}"),
            InlineKeyboardButton("🏓 Ping",         callback_data=f"sub_ping_{i}"),
            InlineKeyboardButton("🔄 Update",       callback_data=f"sub_update_{i}"),
            InlineKeyboardButton("🗑️",             callback_data=f"sub_del_{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Thêm Sub Link", callback_data="sub_add_guide")])
    rows.append([InlineKeyboardButton("🔄 Cập nhật tất cả", callback_data="sub_update_all")])
    rows.append([InlineKeyboardButton("🔙 Menu chính",       callback_data="back_start")])
    return InlineKeyboardMarkup(rows)



# ==================== COMMAND HANDLERS ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    subs = user_subs.get(uid, [])
    text = "👋 *Bot Kiểm Tra VPN Trung Quốc*\n\n"
    text += "*Lệnh sub link:*\n"
    text += "`/new <tên> <url>` — Thêm sub link\n"
    text += "`/remove <số>` — Xóa sub link\n"
    text += "`/view <số>` — Xem server trong sub\n"
    text += "`/ping <số>` — Ping tất cả node sub\n\n"
    text += "*Lệnh khác:*\n"
    text += "`/check` — Kiểm tra VPN & GFW\n"
    text += "`/ip` — Xem IP công khai\n"
    text += "`/speed` — Đo tốc độ mạng\n\n"
    if subs:
        text += f"*Sub link của bạn ({len(subs)}/{MAX_SUBS}):*\n"
        for i, s in enumerate(subs, 1):
            text += f"  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy\n"
    else:
        text += "_Chưa có sub link nào. Dùng /new để thêm._"
    await update.message.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/new <tên> <url>  hoặc  /new <url>"""
    uid  = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Cú pháp: `/new <tên> <url>`\nVí dụ: `/new vpnstore https://example.com/sub/abc`",
            parse_mode="Markdown"
        )
        return
    if len(user_subs.get(uid, [])) >= MAX_SUBS:
        await update.message.reply_text(f"❌ Đã đạt giới hạn {MAX_SUBS} sub link!")
        return

    # Tìm URL trong args
    url_idx = next((i for i, a in enumerate(args) if a.startswith("http")), -1)
    if url_idx < 0:
        await update.message.reply_text("❌ Không tìm thấy URL hợp lệ (phải bắt đầu http/https).", parse_mode="Markdown")
        return

    url  = args[url_idx]
    name = " ".join(args[:url_idx]).strip().rstrip("|").strip()
    if not name:
        name = f"Sub {len(user_subs.get(uid, [])) + 1}"

    msg = await update.message.reply_text(f"⏳ Đang tải sub link *{name}*...", parse_mode="Markdown")
    proxies = await asyncio.get_event_loop().run_in_executor(None, decode_sub_link, url)
    if proxies and "error" in proxies[0]:
        await msg.edit_text(f"❌ Lỗi: `{proxies[0]['error']}`", parse_mode="Markdown")
        return
    if uid not in user_subs:
        user_subs[uid] = []
    user_subs[uid].append({
        "name": name, "url": url, "proxies": proxies,
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })
    idx = len(user_subs[uid]) - 1
    lines = [f"  • [{p.get('type','?')}] `{p.get('name','?')}`" for p in proxies[:12]]
    if len(proxies) > 12:
        lines.append(f"  _... và {len(proxies)-12} proxy khác_")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🏓 Ping nodes", callback_data=f"sub_ping_{idx}"),
        InlineKeyboardButton(f"📋 Xem server", callback_data=f"sub_view_{idx}"),
    ]])
    await msg.edit_text(
        f"✅ *Đã thêm: {name}*\nTổng `{len(proxies)}` proxy\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=kb,
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/remove <số>"""
    uid  = update.effective_user.id
    subs = user_subs.get(uid, [])
    if not context.args:
        await update.message.reply_text("❌ Cú pháp: `/remove <số>`\nVí dụ: `/remove 1`", parse_mode="Markdown")
        return
    try:
        idx     = int(context.args[0]) - 1
        removed = subs.pop(idx)
        text = f"🗑️ Đã xóa sub: *{removed['name']}*\n\n"
        if subs:
            text += f"*Còn lại ({len(subs)}/{MAX_SUBS}):*\n"
            for i, s in enumerate(subs, 1):
                text += f"  `{i}.` *{s['name']}*\n"
        else:
            text += "_Danh sách sub trống._"
        await update.message.reply_text(text, parse_mode="Markdown")
    except (IndexError, ValueError):
        subs_list = "\n".join([f"  `{i}.` *{s['name']}*" for i, s in enumerate(subs, 1)]) or "_Trống_"
        await update.message.reply_text(
            f"❌ Số thứ tự không hợp lệ!\n\nDanh sách hiện tại:\n{subs_list}",
            parse_mode="Markdown"
        )


async def cmd_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/view <số> — Fetch lại sub rồi hiện danh sách server"""
    uid  = update.effective_user.id
    subs = user_subs.get(uid, [])
    if not context.args:
        await update.message.reply_text("❌ Cú pháp: `/view <số>`\nVí dụ: `/view 1`", parse_mode="Markdown")
        return
    try:
        idx = int(context.args[0]) - 1
        s   = subs[idx]
    except (IndexError, ValueError):
        subs_list = "\n".join([f"  `{i}.` *{s['name']}*" for i, s in enumerate(subs, 1)]) or "_Trống_"
        await update.message.reply_text(
            f"❌ Không tìm thấy sub #{context.args[0]}\n\nDanh sách:\n{subs_list}",
            parse_mode="Markdown"
        )
        return

    # Luôn fetch lại sub để lấy server mới nhất
    msg = await update.message.reply_text(
        f"🔄 Đang fetch sub *{s['name']}* để lấy server mới nhất...", parse_mode="Markdown"
    )
    subs[idx] = await asyncio.get_event_loop().run_in_executor(None, fetch_sub_fresh, s)
    s       = subs[idx]
    proxies = s.get("proxies", [])

    if not proxies or "error" in proxies[0]:
        await msg.edit_text(f"❌ Lỗi fetch sub: `{proxies[0].get('error','?')}`", parse_mode="Markdown")
        return

    lines = [
        f"  `{i}.` [{p.get('type','?')}] `{p.get('name','?')}` — `{p.get('server','?')}:{p.get('port','?')}`"
        for i, p in enumerate(proxies[:25], 1)
    ]
    if len(proxies) > 25:
        lines.append(f"  _... và {len(proxies)-25} server khác_")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏓 Ping tất cả node", callback_data=f"sub_ping_{idx}"),
    ]])
    await msg.edit_text(
        f"📋 *{s['name']}*\n"
        f"Tổng: `{len(proxies)}` server | Fetch: `{s.get('updated_at','?')}`\n"
        f"_✅ Danh sách vừa được tải mới nhất từ link sub_\n\n"
        + "\n".join(lines),
        parse_mode="Markdown", reply_markup=kb,
    )


async def cmd_ping_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping <số> — Fetch lại sub rồi ping tất cả node"""
    uid  = update.effective_user.id
    subs = user_subs.get(uid, [])
    if not context.args:
        await update.message.reply_text("❌ Cú pháp: `/ping <số>`\nVí dụ: `/ping 1`", parse_mode="Markdown")
        return
    try:
        idx = int(context.args[0]) - 1
        s   = subs[idx]
    except (IndexError, ValueError):
        subs_list = "\n".join([f"  `{i}.` *{s['name']}*" for i, s in enumerate(subs, 1)]) or "_Trống_"
        await update.message.reply_text(
            f"❌ Không tìm thấy sub #{context.args[0]}\n\nDanh sách:\n{subs_list}",
            parse_mode="Markdown"
        )
        return

    await _exec_ping_nodes(update.message, s, idx)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Đang kiểm tra, vui lòng chờ...")
    results = await asyncio.get_event_loop().run_in_executor(None, run_full_check)
    await msg.edit_text(fmt_check(results), parse_mode="Markdown", reply_markup=kb_recheck("check_now"))


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Đang lấy thông tin IP...")
    ip  = await asyncio.get_event_loop().run_in_executor(None, get_public_ip)
    text = (
        f"📍 *THÔNG TIN IP CỦA BẠN*\n━━━━━━━━━━━━━━━━\n"
        f"🔹 IP: `{ip['ip']}`\n🔹 Quốc gia: `{ip['country']}`\n"
        f"🔹 Vùng: `{ip['region']}`\n🔹 Thành phố: `{ip['city']}`\n"
        f"🔹 ISP: `{ip['org']}`\n\n"
        f"💡 _IP tại TQ → VPN chưa bật | IP nước khác → VPN OK_"
    )
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())


async def cmd_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "⚡ *Đang đo tốc độ...*\n_Mất khoảng 30–60 giây, vui lòng chờ._", parse_mode="Markdown"
    )
    result = await asyncio.get_event_loop().run_in_executor(None, do_speedtest)
    await msg.edit_text(fmt_speed(result), parse_mode="Markdown", reply_markup=kb_recheck("menu_speed"))


async def _show_sub_menu(message, uid: int):
    subs = user_subs.get(uid, [])
    text = f"🔗 *DANH SÁCH SUB LINK*\n\nBạn có `{len(subs)}/{MAX_SUBS}` sub link.\n"
    if subs:
        text += "_Fetch mới nhất mỗi khi /view hoặc /ping_ ✅\n\n"
        for i, s in enumerate(subs, 1):
            text += f"  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy | `{s.get('updated_at','?')}`\n"
    else:
        text += "\nChưa có sub link.\nDùng: `/new <tên> <url>`"
    await message.reply_text(text, parse_mode="Markdown", reply_markup=kb_sub_list(uid))


async def _exec_ping_nodes(message, sub: dict, sub_idx: int):
    # Luôn fetch lại sub trước khi ping
    msg = await message.reply_text(
        f"🔄 Đang fetch sub *{sub['name']}* để lấy server mới nhất...", parse_mode="Markdown"
    )
    loop    = asyncio.get_event_loop()
    sub     = await loop.run_in_executor(None, fetch_sub_fresh, sub)
    # Cập nhật lại vào user_subs
    for uid_key, subs in user_subs.items():
        if sub_idx < len(subs) and subs[sub_idx].get("url") == sub.get("url"):
            subs[sub_idx] = sub
            break
    proxies = sub.get("proxies", [])
    total   = len(proxies)
    if not proxies or "error" in proxies[0]:
        await msg.edit_text(f"❌ Lỗi fetch sub: `{proxies[0].get('error','?')}`", parse_mode="Markdown")
        return
    await msg.edit_text(
        f"🏓 Đang ping `{min(total, MAX_NODES_PING)}/{total}` node của *{sub['name']}*...\n_Vui lòng chờ..._",
        parse_mode="Markdown",
    )
    results = await loop.run_in_executor(None, ping_all_nodes, proxies)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Ping lại", callback_data=f"sub_ping_{sub_idx}"),
        InlineKeyboardButton("📋 Xem server", callback_data=f"sub_view_{sub_idx}"),
    ]])
    await msg.edit_text(
        fmt_ping_nodes(sub["name"], results, total),
        parse_mode="Markdown", reply_markup=kb,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *HƯỚNG DẪN SỬ DỤNG*\n\n"
        "*Sub Link:*\n"
        "• `/new <tên> <url>` — Thêm sub link\n"
        "• `/remove <số>` — Xóa sub link\n"
        "• `/view <số>` — Xem server trong sub (fetch mới)\n"
        "• `/ping <số>` — Ping tất cả node sub (fetch mới)\n\n"
        "*Công cụ:*\n"
        "• `/check` — Kiểm tra VPN & GFW\n"
        "• `/ip` — Xem IP công khai\n"
        "• `/speed` — Đo tốc độ mạng\n\n"
        "💡 _/view và /ping luôn tải lại link sub để có server mới nhất_\n\n"
        "*Hỗ trợ:* Clash YAML · VMess · VLESS · SS · Trojan · Hysteria2 · TUIC\n\n"
        "🟢 <150ms  🟡 150–300ms  🔴 >300ms"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Đã hủy.")
    return ConversationHandler.END


# ==================== CALLBACK HANDLER ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    # ── Kiểm tra VPN ──────────────────────────────────
    if data == "check_now":
        await query.edit_message_text("⏳ Đang kiểm tra mạng, vui lòng chờ...")
        results = await asyncio.get_event_loop().run_in_executor(None, run_full_check)
        await query.edit_message_text(fmt_check(results), parse_mode="Markdown", reply_markup=kb_recheck("check_now"))

    # ── Xem IP ────────────────────────────────────────
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

    # ── Ping host ─────────────────────────────────────
    elif data == "menu_ping":
        await query.edit_message_text(
            "🏓 *Ping Host / IP*\n\nGõ lệnh:\n"
            "`/ping google.com`\n`/ping 8.8.8.8`\n`/ping 1.2.3.4:443`\n\n"
            "Hoặc ping node VPN:\n`/sub ping 1`",
            parse_mode="Markdown", reply_markup=kb_back(),
        )

    # ── Speedtest ─────────────────────────────────────
    elif data == "menu_speed":
        await query.edit_message_text("⚡ *Đang đo tốc độ...*\n_Mất 30–60 giây, vui lòng chờ._", parse_mode="Markdown")
        result = await asyncio.get_event_loop().run_in_executor(None, do_speedtest)
        await query.edit_message_text(fmt_speed(result), parse_mode="Markdown", reply_markup=kb_recheck("menu_speed"))

    # ── Sub link menu ─────────────────────────────────
    elif data == "menu_sub":
        subs = user_subs.get(uid, [])
        text = f"🔗 *QUẢN LÝ SUB LINK VPN*\n\nBạn có `{len(subs)}/{MAX_SUBS}` sub link."
        if subs:
            text += "\n_Fetch mới nhất mỗi khi bấm Ping / View_ ✅\n"
            for i, s in enumerate(subs, 1):
                text += f"\n  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy | `{s.get('updated_at','?')}`"
        else:
            text += "\n\nChưa có sub link.\nDùng: `/sub add Tên | URL`"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_sub_list(uid))

    # ── Sub: thêm hướng dẫn ───────────────────────────
    elif data == "sub_add_guide":
        await query.edit_message_text(
            "➕ *Thêm Sub Link VPN*\n\n"
            "Gõ lệnh:\n`/sub add Tên | URL`\n\n"
            "Ví dụ:\n`/sub add HK Free | https://example.com/sub/abc123`\n\n"
            "Hỗ trợ: VMess · VLESS · Shadowsocks · Trojan\n"
            "_(Base64 hoặc plain text)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Danh sách", callback_data="menu_sub")]]),
        )

    # ── Sub: xem node ─────────────────────────────────
    elif data.startswith("sub_view_"):
        idx  = int(data.split("_")[-1])
        subs = user_subs.get(uid, [])
        if idx < len(subs):
            # Luôn fetch lại sub để có server mới nhất
            await query.edit_message_text("🔄 Đang lấy danh sách server mới nhất...", parse_mode="Markdown")
            subs[idx] = await asyncio.get_event_loop().run_in_executor(None, fetch_sub_fresh, subs[idx])
            s       = subs[idx]
            proxies = s.get("proxies", [])
            lines   = [
                f"  • [{p.get('type','?')}] `{p.get('name','?')}` — `{p.get('server','?')}:{p.get('port','?')}`"
                for p in proxies[:20]
            ]
            if len(proxies) > 20:
                lines.append(f"  _... và {len(proxies)-20} proxy khác_")
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🏓 Ping nodes", callback_data=f"sub_ping_{idx}"),
            ],[InlineKeyboardButton("🔙 Danh sách",  callback_data="menu_sub")]])
            await query.edit_message_text(
                f"📋 *{s['name']}*\nProxy: `{len(proxies)}` | Fetch: `{s.get('updated_at','?')}`\n\n"
                + "\n".join(lines),
                parse_mode="Markdown", reply_markup=kb,
            )

    # ── Sub: ping tất cả node (luôn fetch lại sub trước) ──────
    elif data.startswith("sub_ping_"):
        idx  = int(data.split("_")[-1])
        subs = user_subs.get(uid, [])
        if idx < len(subs):
            # Bước 1: Fetch sub mới nhất
            await query.edit_message_text(f"🔄 Đang fetch danh sách server mới nhất...", parse_mode="Markdown")
            subs[idx] = await asyncio.get_event_loop().run_in_executor(None, fetch_sub_fresh, subs[idx])
            s       = subs[idx]
            proxies = s.get("proxies", [])
            total   = len(proxies)
            # Bước 2: Ping các node
            await query.edit_message_text(
                f"🏓 Đang ping `{min(total, MAX_NODES_PING)}/{total}` node của *{s['name']}*...\n_Vui lòng chờ..._",
                parse_mode="Markdown",
            )
            results = await asyncio.get_event_loop().run_in_executor(None, ping_all_nodes, proxies)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ping lại", callback_data=f"sub_ping_{idx}"),
            ],[InlineKeyboardButton("🔙 Danh sách", callback_data="menu_sub")]])
            await query.edit_message_text(
                fmt_ping_nodes(s["name"], results, total),
                parse_mode="Markdown", reply_markup=kb,
            )

    # ── Sub: manual re-fetch (still available) ───────
    elif data.startswith("sub_update_"):
        idx  = int(data.split("_")[-1])
        subs = user_subs.get(uid, [])
        if idx < len(subs):
            s = subs[idx]
            await query.edit_message_text(f"🔄 Đang cập nhật *{s['name']}*...", parse_mode="Markdown")
            updated = await asyncio.get_event_loop().run_in_executor(None, fetch_sub_fresh, s)
            subs[idx] = updated
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🏓 Ping nodes", callback_data=f"sub_ping_{idx}"),
                InlineKeyboardButton("🔙 Danh sách",  callback_data="menu_sub"),
            ]])
            await query.edit_message_text(
                f"✅ *Đã cập nhật: {updated['name']}*\n"
                f"Tổng: `{len(updated.get('proxies',[]))}` proxy\n"
                f"Lúc: `{updated.get('updated_at','?')}`",
                parse_mode="Markdown", reply_markup=kb,
            )

    # ── Sub: cập nhật tất cả ─────────────────────────
    elif data == "sub_update_all":
        subs = user_subs.get(uid, [])
        if not subs:
            await query.answer("Chưa có sub link nào!", show_alert=True)
            return
        await query.edit_message_text(f"🔄 Đang cập nhật {len(subs)} sub link...", parse_mode="Markdown")
        for i, sub in enumerate(subs):
            subs[i] = await asyncio.get_event_loop().run_in_executor(None, fetch_sub_fresh, sub)
        user_subs[uid] = subs
        text = f"✅ *Đã cập nhật {len(subs)} sub link*\n\n"
        for i, s in enumerate(subs, 1):
            text += f"  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy\n"
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Danh sách", callback_data="menu_sub")]]),
        )

    # ── Sub: xóa ─────────────────────────────────────
    elif data.startswith("sub_del_"):
        idx  = int(data.split("_")[-1])
        subs = user_subs.get(uid, [])
        if idx < len(subs):
            removed = subs.pop(idx)
            await query.answer(f"🗑️ Đã xóa: {removed['name']}", show_alert=True)
        # Re-render menu sub
        text = f"🔗 *QUẢN LÝ SUB LINK VPN*\n\nBạn có `{len(subs)}/{MAX_SUBS}` sub link."
        if subs:
            for i, s in enumerate(subs, 1):
                text += f"\n  `{i}.` *{s['name']}* — `{len(s.get('proxies',[]))}` proxy"
        else:
            text += "\n\nChưa có sub link."
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_sub_list(uid))

    # ── Help ──────────────────────────────────────────
    elif data == "help":
        text = (
            "📖 *HƯỚNG DẪN*\n\n"
            "• `/check` — Kiểm tra VPN\n"
            "• `/ip` — Xem IP\n"
            "• `/ping <host>` — Ping\n"
            "• `/speed` — Speedtest\n"
            "• `/sub` — Sub link VPN\n"
            "• `/sub ping 1` — Ping node sub #1\n"
            "• `/sub update 1` — Cập nhật sub #1\n\n"
            "🟢 <150ms  🟡 150–300ms  🔴 >300ms\n"
            "✅ = sống | ❌ = chết"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())

    # ── Menu chính ────────────────────────────────────
    elif data == "back_start":
        await query.edit_message_text(
            "👋 *Bot Kiểm Tra VPN Trung Quốc*\nChọn tính năng:",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


# ==================== MAIN ====================

def main():
    print("🤖 Bot đang khởi động...")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("new",     cmd_new))
    app.add_handler(CommandHandler("remove",  cmd_remove))
    app.add_handler(CommandHandler("view",    cmd_view))
    app.add_handler(CommandHandler("ping",    cmd_ping_sub))
    app.add_handler(CommandHandler("check",   cmd_check))
    app.add_handler(CommandHandler("ip",      cmd_ip))
    app.add_handler(CommandHandler("speed",   cmd_speed))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ Bot đang chạy! Gửi /start trên Telegram để bắt đầu.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
