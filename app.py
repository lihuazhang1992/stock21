import os, shutil, streamlit as st_git
import pathlib
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import sqlite3
import threading
from datetime import datetime

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

# ── 股票名称 → 东方财富 secid 映射（内置兜底表）──
# 东方财富 secid 格式：市场前缀.代码
#   A股 沪市(上交所) → 1.xxxxxx
#   A股 深市(深交所) → 0.xxxxxx
#   港股            → 116.xxxxx（5位，不足补0）
#   美股            → 105.XXXX
# 数据库 stock_info 中用户录入的代码优先，此表仅作兜底
TICKER_MAP = {
    # 港股
    "中芯国际":  "116.00981",
    "汇丰控股":  "116.00005",
    "中银香港":  "116.02388",
    "紫金矿业":  "116.02899",
    "电能实业":  "116.00006",
    "福耀玻璃":  "116.03606",
    # A股 深市
    "比亚迪":    "0.002594",
    "阳光电源":  "0.300274",
    "纳指ETF":   "0.159941",
    # A股 沪市
    "长江电力":  "1.600900",
    # 美股
    "联合健康":  "105.UNH",
    "特斯拉":    "105.TSLA",
    "伯克希尔":  "105.BRK-B",
}

# yfinance ticker（用于降级回退，stock_info 里存的是东方财富 secid）
_YF_FALLBACK = {
    "中芯国际":  "0981.HK",
    "汇丰控股":  "0005.HK",
    "中银香港":  "2388.HK",
    "紫金矿业":  "2899.HK",
    "电能实业":  "0006.HK",
    "福耀玻璃":  "3606.HK",
    "比亚迪":    "002594.SZ",
    "阳光电源":  "300274.SZ",
    "长江电力":  "600900.SS",
    "纳指ETF":   "159941.SZ",
    "联合健康":  "UNH",
    "特斯拉":    "TSLA",
    "伯克希尔":  "BRK-B",
}

def _build_ticker_map() -> dict:
    """合并数据库用户录入代码（优先）和内置 TICKER_MAP（兜底）"""
    merged = dict(TICKER_MAP)
    try:
        rows = conn.execute(
            "SELECT stock_name, stock_code FROM stock_info WHERE stock_code IS NOT NULL AND stock_code != ''"
        ).fetchall()
        for name, code in rows:
            merged[name] = code  # 用户录入的覆盖内置
    except Exception:
        pass
    return merged

def _fetch_eastmoney(secids: list) -> dict:
    """
    东方财富批量行情接口。
    secids: ['1.600900', '0.002594', '116.00981', '105.TSLA', ...]
    返回 {股票纯代码: 最新价(float)}
    f2=现价（交易时段）, f18=昨收（非交易时段兜底）, f12=代码
    """
    import urllib.request, json
    if not secids:
        return {}
    fields = "f2,f18,f12"   # f2=现价, f18=昨收（非交易时段兜底）, f12=代码
    url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&fields={fields}&secids={','.join(secids)}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        items = (data.get("data") or {}).get("diff") or []
        result = {}
        for item in items:
            price = item.get("f2")
            code  = str(item.get("f12", ""))
            if not code:
                continue
            # f2 为 "-"、None 或 0 时，用昨收 f18 兜底
            if price is None or price == "-" or price == 0:
                price = item.get("f18")
            if price is None or price == "-" or price == 0:
                continue
            result[code] = round(float(price), 4)
        return result
    except Exception:
        return {}

def fetch_latest_prices(stock_names: list) -> dict:
    """
    批量拉取最新价，优先东方财富接口（低延迟），失败时回退 yfinance。
    返回 {股票名称: 最新价(float)}
    """
    ticker_map = _build_ticker_map()
    result = {}

    # ── 第一步：东方财富批量请求 ──
    secid_to_name = {}
    for name in stock_names:
        secid = ticker_map.get(name)
        if secid:
            code_part = secid.split(".", 1)[-1]   # "1.600900" → "600900"
            secid_to_name[code_part] = (name, secid)

    if secid_to_name:
        em_result = _fetch_eastmoney([v[1] for v in secid_to_name.values()])
        for code_part, (name, _) in secid_to_name.items():
            if code_part in em_result:
                result[name] = em_result[code_part]

    # ── 第二步：东方财富未能拿到的，用 yfinance 兜底 ──
    missing = [n for n in stock_names if n not in result]
    if missing and _YF_OK:
        for name in missing:
            yf_ticker = _YF_FALLBACK.get(name)
            if not yf_ticker:
                continue
            try:
                hist = yf.Ticker(yf_ticker).history(period="2d")
                if not hist.empty:
                    result[name] = round(float(hist["Close"].iloc[-1]), 4)
            except Exception:
                pass

    return result


# ============== 自动备份 GitHub ==============
import base64, json, urllib.request
# Streamlit Cloud 只有 /mnt/data 是可持久化目录，本地运行时回退到脚本目录
_DATA_DIR = pathlib.Path(os.environ.get("STREAMLIT_DATA_DIR", "/mnt/data"))
if not _DATA_DIR.exists():
    _DATA_DIR = pathlib.Path(__file__).parent
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = _DATA_DIR / "stock_data_v12.db"
print(f"[init] DB_FILE={DB_FILE}, data_dir={_DATA_DIR}")
try:
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN    = os.getenv("GITHUB_TOKEN")
    REPO_URL = os.getenv("REPO_URL")
except Exception:
    TOKEN    = st.secrets.get("GITHUB_TOKEN", "")
    REPO_URL = st.secrets.get("REPO_URL", "")

# 启动时诊断：检查 TOKEN 和 REPO_URL 是否正确加载
print(f"[init] TOKEN={'YES' if TOKEN else 'NO'}, REPO_URL={REPO_URL[:30] if REPO_URL else 'EMPTY'}")

def _parse_github_repo_info(repo_url):
    """从 repo URL 解析 owner 和 repo 名"""
    # 支持 https://github.com/owner/repo.git 或 https://github.com/owner/repo
    clean = repo_url.rstrip("/").replace(".git", "")
    parts = clean.rstrip("/").split("/")
    return parts[-2], parts[-1]

def sync_db_to_github():
    """通过 GitHub Contents API 直接上传 db 文件，无需 clone/push。必须先 conn.commit() 再调用。"""
    if not (TOKEN and REPO_URL):
        st.toast("⚠️ 同步跳过：TOKEN 或 REPO_URL 未配置", icon="⚠️")
        return
    try:
        # WAL 模式下先 commit + checkpoint，确保所有数据写入主库文件
        try:
            _c = get_connection()
            _c.commit()
            _c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        owner, repo = _parse_github_repo_info(REPO_URL)
        db_name = DB_FILE.name
        db_bytes = DB_FILE.read_bytes()
        print(f"[sync] db size: {len(db_bytes)} bytes, owner={owner}, repo={repo}")
        b64_content = base64.b64encode(db_bytes).decode()

        # 先尝试获取文件当前 SHA（用于更新；文件不存在则创建）
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{db_name}"
        req = urllib.request.Request(api_url, headers={
            "Authorization": f"token {TOKEN}",
            "User-Agent": "Streamlit-Bot"
        })
        sha = None
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                sha = data.get("sha")
                print(f"[sync] got SHA: {sha}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise  # 404 表示文件不存在（首次上传），其他错误抛出
            print(f"[sync] file not found on GitHub (404), will create")

        # 上传（创建或更新）
        payload = json.dumps({
            "message": f"Auto-sync {datetime.now().strftime('%m%d-%H%M')}",
            "content": b64_content,
        }).encode()
        if sha:
            payload_dict = json.loads(payload)
            payload_dict["sha"] = sha
            payload = json.dumps(payload_dict).encode()

        put_req = urllib.request.Request(api_url, data=payload, method="PUT", headers={
            "Authorization": f"token {TOKEN}",
            "User-Agent": "Streamlit-Bot",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(put_req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if result.get("commit"):
                print(f"[sync] SUCCESS: {result['commit'].get('sha', '')}")
                st.toast("✅ GitHub 同步成功", icon="📤")
    except Exception as e:
        print(f"[sync] ERROR: {e}")
        st.toast(f"⚠️ 备份失败: {e}", icon="⚠️")
# ==========================================

st.set_page_config(page_title="股票管理系统 Pro", layout="wide", page_icon="📈")

@st.cache_resource
def get_connection():
    db_path = str(DB_FILE)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")   # 允许读写并发，避免锁冲突
    return _conn

# ── 每次启动都从 GitHub 拉取最新数据库 ──
try:
    owner, repo = _parse_github_repo_info(REPO_URL)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{DB_FILE.name}"
    req = urllib.request.Request(api_url, headers={
        "Authorization": f"token {TOKEN}",
        "User-Agent": "Streamlit-Bot"
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
        db_b64 = data.get("content", "")
        if db_b64:
            # 先关闭旧连接再覆盖文件，避免锁冲突
            try:
                conn_old = get_connection.__wrapped__() if hasattr(get_connection, '__wrapped__') else None
            except Exception:
                conn_old = None
            DB_FILE.write_bytes(base64.b64decode(db_b64))
            st.toast("✅ 已从 GitHub 加载最新数据库", icon="📥")
except urllib.error.HTTPError as e:
    if e.code == 404:
        st.toast("🆕 GitHub 无数据库，将创建新库", icon="✨")
    else:
        st.toast(f"⚠️ GitHub 加载失败(code={e.code})，使用本地数据库", icon="⚠️")
except Exception as e:
    st.toast(f"⚠️ GitHub 加载失败，使用本地数据库: {e}", icon="⚠️")

conn = get_connection()
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS stock_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_name TEXT UNIQUE,
    stock_code TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT,
    action TEXT, price REAL, quantity INTEGER, note TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS prices (
    code TEXT PRIMARY KEY, current_price REAL, manual_cost REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS signals (
    code TEXT PRIMARY KEY, high_point REAL, low_point REAL,
    up_threshold REAL, down_threshold REAL, high_date TEXT, low_date TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, stock_name TEXT, content TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS strategy_notes (
    code TEXT PRIMARY KEY, logic TEXT, max_holding_amount REAL DEFAULT 0.0, annual_return REAL DEFAULT 0.0)''')
c.execute('''CREATE TABLE IF NOT EXISTS decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT, decision TEXT, reason TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS price_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, start_date TEXT, end_date TEXT, change_pct REAL)''')

for col_sql in [
    "ALTER TABLE strategy_notes ADD COLUMN annual_return REAL DEFAULT 0.0",
    "ALTER TABLE strategy_notes ADD COLUMN buy_base_price REAL DEFAULT 0.0",
    "ALTER TABLE strategy_notes ADD COLUMN buy_drop_pct REAL DEFAULT 0.0",
    "ALTER TABLE strategy_notes ADD COLUMN sell_base_price REAL DEFAULT 0.0",
    "ALTER TABLE strategy_notes ADD COLUMN sell_rise_pct REAL DEFAULT 0.0",
    "ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0",
    "ALTER TABLE trades ADD COLUMN note TEXT",
]:
    try:
        c.execute(col_sql)
    except:
        pass

c.execute('''CREATE TABLE IF NOT EXISTS price_targets (
    code TEXT PRIMARY KEY, base_price REAL DEFAULT 0.0, buy_target REAL DEFAULT 0.0,
    sell_target REAL DEFAULT 0.0, last_updated TEXT)''')
conn.commit()

# ── 将内置 TICKER_MAP 初始化写入 stock_info（INSERT OR IGNORE，不覆盖用户已录入的）──
for _name, _code in TICKER_MAP.items():
    try:
        c.execute("INSERT OR IGNORE INTO stock_info (stock_name, stock_code) VALUES (?, ?)", (_name, _code))
    except Exception:
        pass
conn.commit()

# 注意：启动时不再自动同步到 GitHub，避免用旧数据覆盖远程
# 同步只在用户修改数据后触发，确保推送的是最新数据

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + [s for s in t_stocks if s])))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# =====================================================================
# ██████╗ ███████╗███████╗██╗ ██████╗ ███╗   ██╗
# ██╔══██╗██╔════╝██╔════╝██║██╔════╝ ████╗  ██║
# ██║  ██║█████╗  ███████╗██║██║  ███╗██╔██╗ ██║
# ██║  ██║██╔══╝  ╚════██║██║██║   ██║██║╚██╗██║
# ██████╔╝███████╗███████║██║╚██████╔╝██║ ╚████║
# ╚═════╝ ╚══════╝╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝
# =====================================================================
st.markdown("""
<style>
/* ─── 设计令牌 ─── */
:root {
    --bg-base:       #0a0e1a;
    --bg-surface:    #111827;
    --bg-elevated:   #1a2235;
    --bg-card:       #162032;
    --bg-input:      #1e2d40;
    --border:        rgba(99,179,237,0.12);
    --border-hover:  rgba(99,179,237,0.30);
    --accent-blue:   #3b82f6;
    --accent-teal:   #06b6d4;
    --accent-green:  #10b981;
    --accent-red:    #f43f5e;
    --accent-amber:  #f59e0b;
    --accent-purple: #8b5cf6;
    --text-primary:  #f0f6ff;
    --text-secondary:#94a3b8;
    --text-muted:    #4b5e78;
    --profit:        #34d399;
    --loss:          #fb7185;
    --shadow-card:   0 4px 24px rgba(0,0,0,0.45);
    --shadow-glow:   0 0 24px rgba(59,130,246,0.15);
    --radius-sm:     6px;
    --radius-md:     10px;
    --radius-lg:     16px;
    --radius-xl:     22px;
    --transition:    0.18s cubic-bezier(.4,0,.2,1);
}

/* ─── 全局重置 ─── */
html, body, [class*="css"] {
    font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
}
.stApp {
    background: var(--bg-base) !important;
    color: var(--text-primary) !important;
}
.block-container {
    padding-top: 1.2rem !important;
    padding-bottom: 2rem !important;
    max-width: 1400px !important;
}

/* ─── 侧边栏 ─── */
[data-testid="stSidebar"] {
    background: var(--bg-surface) !important;
    border-right: 1px solid var(--border) !important;
    box-shadow: 4px 0 32px rgba(0,0,0,0.4) !important;
}
[data-testid="stSidebar"] .stRadio > label {
    color: var(--text-secondary) !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    margin-bottom: 8px !important;
    padding-left: 4px !important;
}
[data-testid="stSidebar"] .stRadio [data-testid="stMarkdownContainer"] p {
    color: var(--text-primary) !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] {
    margin-bottom: 2px !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] > label {
    background: transparent !important;
    border-radius: var(--radius-md) !important;
    padding: 10px 14px !important;
    transition: all var(--transition) !important;
    border: 1px solid transparent !important;
    cursor: pointer !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] > label:hover {
    background: var(--bg-elevated) !important;
    border-color: var(--border-hover) !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"][aria-checked="true"] > label,
[data-testid="stSidebar"] [data-baseweb="radio"] input:checked + label {
    background: linear-gradient(135deg, rgba(59,130,246,0.18), rgba(6,182,212,0.10)) !important;
    border-color: rgba(59,130,246,0.45) !important;
    box-shadow: 0 0 12px rgba(59,130,246,0.20) !important;
}

/* ─── 标题样式 ─── */
h1, h2, h3, h4 {
    color: var(--text-primary) !important;
    letter-spacing: -0.02em !important;
}
/* ─── 隐藏顶部 Header 工具栏（保留侧边栏收起/展开按钮）─── */
[data-testid="stHeader"] {
    background: transparent !important;
    pointer-events: none !important;
}
[data-testid="stHeader"] > * {
    pointer-events: none !important;
    opacity: 0 !important;
}
/* Streamlit ≥1.38 侧边栏收起/展开按钮：data-testid 已改为 stSidebarCollapseButton */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button {
    pointer-events: auto !important;
    opacity: 1 !important;
    visibility: visible !important;
    display: flex !important;
    z-index: 9999 !important;
    color: var(--text-primary) !important;
}
[data-testid="stSidebarCollapseButton"] button {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border-hover) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: 2px 0 16px rgba(0,0,0,0.5) !important;
}
[data-testid="stSidebarCollapseButton"] button:hover {
    background: var(--accent-blue) !important;
    border-color: var(--accent-blue) !important;
}

/* ─── 通用卡片 ─── */
.pro-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 20px 22px;
    box-shadow: var(--shadow-card);
    transition: all var(--transition);
    position: relative;
    overflow: hidden;
}
.pro-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent-blue), var(--accent-teal));
    opacity: 0;
    transition: opacity var(--transition);
}
.pro-card:hover::before { opacity: 1; }
.pro-card:hover {
    border-color: var(--border-hover);
    box-shadow: var(--shadow-card), var(--shadow-glow);
    transform: translateY(-1px);
}

/* ─── 指标卡片 ─── */
.metric-card {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 14px 16px 12px;
    min-width: 0;
    box-sizing: border-box;
    transition: all var(--transition);
}
.metric-card:hover {
    border-color: var(--border-hover);
    background: var(--bg-card);
}
.metric-label {
    font-size: 0.70em;
    color: var(--text-secondary);
    font-weight: 500;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.metric-value {
    font-size: 1.15em;
    font-weight: 700;
    white-space: nowrap;
    letter-spacing: -0.01em;
}
.metric-sub {
    font-size: 0.76em;
    margin-top: 3px;
    font-weight: 500;
}

/* ─── 表格样式 ─── */
.pro-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 13.5px;
    border-radius: var(--radius-lg);
    overflow: hidden;
    box-shadow: var(--shadow-card);
    margin: 8px 0 16px;
}
.pro-table thead tr {
    background: linear-gradient(135deg, #1e3a5f, #152c47);
}
.pro-table thead th {
    padding: 13px 16px;
    text-align: center;
    color: var(--accent-teal);
    font-weight: 600;
    font-size: 0.80em;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    border-bottom: 1px solid rgba(6,182,212,0.25);
    white-space: nowrap;
}
.pro-table tbody tr {
    background: var(--bg-surface);
    transition: background var(--transition);
}
.pro-table tbody tr:nth-of-type(even) { background: var(--bg-elevated); }
.pro-table tbody tr:hover { background: rgba(59,130,246,0.08) !important; }
.pro-table tbody td {
    padding: 11px 16px;
    text-align: center;
    color: var(--text-primary);
    border-bottom: 1px solid var(--border);
    font-size: 0.92em;
}
.pro-table tbody tr:last-child td { border-bottom: none; }

/* ─── 盈亏颜色 ─── */
.profit-red  { color: var(--profit) !important; font-weight: 700 !important; }
.loss-green  { color: var(--loss)   !important; font-weight: 700 !important; }

/* ─── 标签/徽章 ─── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.76em;
    font-weight: 600;
    letter-spacing: 0.03em;
}
.badge-buy  { background: rgba(16,185,129,0.15); color: var(--accent-green); border: 1px solid rgba(16,185,129,0.3); }
.badge-sell { background: rgba(244,63,94,0.15);  color: var(--accent-red);   border: 1px solid rgba(244,63,94,0.3); }
.badge-hold { background: rgba(245,158,11,0.15); color: var(--accent-amber); border: 1px solid rgba(245,158,11,0.3); }
.badge-watch{ background: rgba(59,130,246,0.15); color: var(--accent-blue);  border: 1px solid rgba(59,130,246,0.3); }

/* ─── 分割线 ─── */
hr, .stDivider { border-color: var(--border) !important; margin: 1rem 0 !important; }

/* ─── 输入框 ─── */
.stTextInput input, .stNumberInput input, .stTextArea textarea,
.stSelectbox [data-baseweb="select"] > div {
    background: var(--bg-input) !important;
    border-color: var(--border) !important;
    color: var(--text-primary) !important;
    border-radius: var(--radius-sm) !important;
    font-size: 0.90em !important;
    transition: all var(--transition) !important;
    caret-color: var(--accent-teal) !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: var(--accent-blue) !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.25) !important;
    outline: none !important;
    caret-color: var(--accent-teal) !important;
}
/* 输入框光标闪烁动画增强 */
@keyframes blink-caret {
    0%, 100% { border-color: var(--accent-teal); }
    50%       { border-color: transparent; }
}
input:focus, textarea:focus {
    caret-color: var(--accent-teal) !important;
}
.stTextInput label, .stNumberInput label, .stTextArea label,
.stSelectbox label, .stDateInput label {
    color: var(--text-secondary) !important;
    font-size: 0.82em !important;
    font-weight: 500 !important;
}
/* ─── textarea 自动增高支持 ─── */
.stTextArea [data-baseweb="textarea"] { height: auto !important; }
.stTextArea textarea { overflow: hidden !important; resize: none !important; height: auto !important; }
.stTextArea [class*="stTextArea"] > div > div { height: auto !important; }
.stTextArea div[data-testid="stGrowingTextarea"] { height: auto !important; }

/* ─── 按钮 ─── */
.stButton > button {
    background: linear-gradient(135deg, var(--accent-blue), #2563eb) !important;
    color: #fff !important;
    border: none !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    font-size: 0.88em !important;
    letter-spacing: 0.02em !important;
    padding: 8px 18px !important;
    transition: all var(--transition) !important;
    box-shadow: 0 2px 12px rgba(59,130,246,0.35) !important;
}
.stButton > button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 20px rgba(59,130,246,0.55) !important;
    filter: brightness(1.08) !important;
}
.stButton > button[kind="secondary"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-secondary) !important;
    box-shadow: none !important;
}
.stButton > button[kind="secondary"]:hover {
    border-color: var(--border-hover) !important;
    color: var(--text-primary) !important;
    box-shadow: none !important;
}

/* ─── Form ─── */
[data-testid="stForm"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-lg) !important;
    padding: 20px !important;
}
[data-testid="stFormSubmitButton"] > button {
    background: linear-gradient(135deg, var(--accent-teal), var(--accent-blue)) !important;
    color: #fff !important;
    border: none !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 700 !important;
    box-shadow: 0 2px 16px rgba(6,182,212,0.35) !important;
}

/* ─── Expander ─── */
[data-testid="stExpander"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary {
    color: var(--text-primary) !important;
    font-weight: 600 !important;
    font-size: 0.92em !important;
    padding: 12px 16px !important;
}
[data-testid="stExpander"] summary:hover { background: var(--bg-card) !important; }

/* ─── Metric ─── */
[data-testid="stMetric"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    padding: 14px 18px !important;
}
[data-testid="stMetricLabel"] { color: var(--text-secondary) !important; font-size: 0.82em !important; }
[data-testid="stMetricValue"] { color: var(--text-primary) !important; font-weight: 700 !important; }

/* ─── Info / Success / Warning / Error ─── */
[data-testid="stAlert"] {
    border-radius: var(--radius-md) !important;
    font-size: 0.88em !important;
}

/* ─── Selectbox ─── */
[data-baseweb="popover"] [data-baseweb="menu"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
}
[data-baseweb="option"] { color: var(--text-primary) !important; }
[data-baseweb="option"]:hover { background: rgba(59,130,246,0.12) !important; }

/* ─── Container with border ─── */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px !important;
}

/* ─── DataEditor ─── */
[data-testid="stDataEditor"] {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
}

/* ─── 隐藏 1px iframe ─── */
iframe[title="st_components_v1.html"] {
    display: block !important; height: 1px !important; min-height: 0 !important;
    overflow: hidden !important; visibility: hidden !important;
    margin: 0 !important; padding: 0 !important;
}
div[data-testid="stCustomComponentV1"] {
    height: 1px !important; min-height: 0 !important;
    overflow: hidden !important; margin: 0 !important; padding: 0 !important;
}

/* ─── 滚动条 ─── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-base); }
::-webkit-scrollbar-thumb { background: var(--text-muted); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-secondary); }

/* ─── 页面标题区 ─── */
.page-title {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: -0.5rem 0 1.2rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
}
.page-title h2 {
    margin: 0 !important;
    font-size: 1.45em !important;
    font-weight: 800 !important;
    background: linear-gradient(135deg, #f0f6ff, var(--accent-teal));
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
}

/* ─── 侧边栏品牌区 ─── */
.sidebar-brand {
    padding: 20px 16px 18px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border);
    text-align: center;
}
.sidebar-brand .brand-icon {
    font-size: 2.2em;
    display: block;
    margin-bottom: 6px;
}
.sidebar-brand .brand-name {
    font-size: 1.05em;
    font-weight: 800;
    color: var(--text-primary);
    letter-spacing: -0.01em;
}
.sidebar-brand .brand-ver {
    font-size: 0.72em;
    color: var(--text-muted);
    margin-top: 2px;
}

/* ─── 监控卡片 ─── */
.monitor-card {
    background: linear-gradient(145deg, #162032, #0f1927);
    border-radius: var(--radius-lg);
    padding: 18px;
    margin-bottom: 12px;
    box-shadow: var(--shadow-card);
    transition: all var(--transition);
    position: relative;
    overflow: hidden;
}
.monitor-card::after {
    content: '';
    position: absolute;
    top: -40%; right: -20%;
    width: 120px; height: 120px;
    border-radius: 50%;
    opacity: 0.04;
    background: currentColor;
    pointer-events: none;
}
.monitor-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}

/* ─── 复盘日记卡片 ─── */
.journal-card {
    background: var(--bg-elevated);
    border-left: 3px solid var(--accent-blue);
    border-radius: 0 var(--radius-md) var(--radius-md) 0;
    padding: 12px 16px;
    margin-bottom: 8px;
    transition: all var(--transition);
}
.journal-card:hover {
    border-left-color: var(--accent-teal);
    background: var(--bg-card);
}
.journal-meta {
    font-size: 0.78em;
    color: var(--text-muted);
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.journal-content {
    font-size: 0.92em;
    color: var(--text-primary);
    line-height: 1.7;
    white-space: pre-line;
}

/* ─── 决策历史卡片 ─── */
.decision-card {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 10px 14px;
    margin-bottom: 8px;
    transition: all var(--transition);
}
.decision-card:hover { border-color: var(--border-hover); }

/* ─── textarea 自动增高：field-sizing ─── */
.stTextArea textarea {
    field-sizing: content !important;
    min-height: 68px !important;
}

/* ─── 固定股票选择器（右上角浮层） ─── */
#fixed-stock-picker {
    position: fixed !important;
    top: 14px !important;
    right: 20px !important;
    z-index: 100000 !important;
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
    background: rgba(22, 32, 50, 0.95) !important;
    backdrop-filter: blur(20px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
    border: 1px solid rgba(99, 179, 237, 0.30) !important;
    border-radius: 12px !important;
    padding: 7px 14px !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(99, 179, 237, 0.08) !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
}
#fixed-stock-picker:hover {
    border-color: rgba(99, 179, 237, 0.55) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.15) !important;
}
#fixed-stock-picker label {
    font-size: 13px !important;
    color: #94a3b8 !important;
    font-weight: 500 !important;
    white-space: nowrap !important;
    margin: 0 !important;
    display: flex !important;
    align-items: center !important;
    gap: 5px !important;
}
#fixed-stock-picker select {
    background: rgba(30, 45, 64, 0.9) !important;
    color: #f0f6ff !important;
    border: 1px solid rgba(99, 179, 237, 0.20) !important;
    border-radius: 8px !important;
    padding: 6px 30px 6px 12px !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif !important;
    cursor: pointer !important;
    outline: none !important;
    appearance: auto !important;
    min-width: 120px !important;
    transition: border-color 0.2s, background 0.2s !important;
}
#fixed-stock-picker select:hover {
    border-color: rgba(99, 179, 237, 0.50) !important;
    background: rgba(30, 45, 64, 1) !important;
}
#fixed-stock-picker select:focus {
    border-color: #3b82f6 !important;
    box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25) !important;
}
/* 侧边栏展开时自动偏移 */
.sidebar-expanded #fixed-stock-picker {
    right: 20px !important;
}
</style>
""", unsafe_allow_html=True)

# ─── 浮动按钮（侧边栏切换 + 回到顶部） ───
components.html("""
<script>
(function() {
    var doc = window.parent.document;

    // ── 侧边栏展开/收起 悬浮按钮 ──
    function makeSidebarToggle() {
        if (doc.getElementById('__sb_toggle_btn')) return;
        var btn = doc.createElement('button');
        btn.id = '__sb_toggle_btn';
        btn.innerHTML = '&#9776;';
        btn.title = '展开/收起导航栏';
        btn.style.cssText = [
            'position:fixed','top:14px','left:14px','z-index:99999',
            'width:38px','height:38px','border-radius:10px',
            'background:linear-gradient(135deg,#1a2235,#162032)',
            'border:1px solid rgba(99,179,237,0.35)',
            'color:#f0f6ff','font-size:18px','cursor:pointer',
            'display:flex','align-items:center','justify-content:center',
            'box-shadow:0 4px 20px rgba(0,0,0,0.5)',
            'transition:all 0.18s'
        ].join(';');
        btn.onmouseenter = function(){ this.style.background='linear-gradient(135deg,#3b82f6,#06b6d4)'; this.style.borderColor='#3b82f6'; };
        btn.onmouseleave = function(){ this.style.background='linear-gradient(135deg,#1a2235,#162032)'; this.style.borderColor='rgba(99,179,237,0.35)'; };
        btn.onclick = function() {
            // 依次尝试各版本 Streamlit 的原生收起/展开按钮
            var selectors = [
                '[data-testid="stSidebarCollapseButton"] button',
                '[data-testid="collapsedControl"] button',
                '[data-testid="stSidebar"] [data-testid="baseButton-headerNoPadding"]',
                '[data-testid="stSidebarContent"] ~ button',
                'section[data-testid="stSidebar"] + div button',
                '[aria-label="Close sidebar"]',
                '[aria-label="Open sidebar"]',
                '[aria-label="收起侧边栏"]',
                '[aria-label="展开侧边栏"]'
            ];
            var clicked = false;
            for (var i = 0; i < selectors.length; i++) {
                var el = doc.querySelector(selectors[i]);
                if (el) { el.click(); clicked = true; break; }
            }
            // 如果都找不到，直接操作 sidebar 的 aria-expanded 属性
            if (!clicked) {
                var sidebar = doc.querySelector('[data-testid="stSidebar"]');
                if (sidebar) {
                    var isCollapsed = sidebar.getAttribute('aria-expanded') === 'false';
                    sidebar.setAttribute('aria-expanded', isCollapsed ? 'true' : 'false');
                }
            }
        };
        doc.body.appendChild(btn);
    }
    setTimeout(makeSidebarToggle, 800);
    var sbObs = new MutationObserver(function(){ makeSidebarToggle(); });
    sbObs.observe(doc.body, { childList: true, subtree: false });

    // ── 回到顶部按钮 ──
    if (!doc.getElementById('wb-back-to-top')) {
        var btn = doc.createElement('button');
        btn.id = 'wb-back-to-top';
        btn.innerHTML = '&#8679;';
        btn.title = '回到顶部';
        btn.style.cssText = [
            'position:fixed','bottom:36px','right:36px','z-index:999999',
            'width:48px','height:48px','border-radius:50%','border:none',
            'background:linear-gradient(135deg,#3b82f6,#06b6d4)','color:#fff',
            'font-size:24px','font-weight:bold','cursor:pointer',
            'box-shadow:0 4px 20px rgba(59,130,246,0.5)',
            'display:flex','align-items:center','justify-content:center',
            'opacity:0.88','transition:all 0.2s','line-height:1'
        ].join(';');
        btn.onmouseenter = function(){ this.style.opacity='1'; this.style.transform='translateY(-2px) scale(1.1)'; };
        btn.onmouseleave = function(){ this.style.opacity='0.88'; this.style.transform=''; };
        btn.onclick = function() {
            var candidates = [
                doc.querySelector('[data-testid="stAppViewBlockContainer"]'),
                doc.querySelector('[data-testid="stMain"]'),
                doc.querySelector('section.main'),
                doc.querySelector('.main'),
                doc.documentElement, doc.body
            ];
            var scrolled = false;
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                if (el && el.scrollTop > 0) { el.scrollTo({top:0,behavior:'smooth'}); scrolled=true; break; }
            }
            if (!scrolled) { for (var j=0;j<candidates.length;j++) { if(candidates[j]) candidates[j].scrollTo({top:0,behavior:'smooth'}); } }
        };
        doc.body.appendChild(btn);
    }
})();
</script>
""", height=1)

# ─── 侧边栏品牌区 ───
st.sidebar.markdown("""
<div class="sidebar-brand">
    <span class="brand-icon">📈</span>
    <div class="brand-name">股票管理系统</div>
    <div class="brand-ver">Professional · v22.1</div>
</div>
""", unsafe_allow_html=True)

menu = ["🏠 股票详情中心", "📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu, label_visibility="collapsed")

# ─── 辅助函数 ───
def format_number(num):
    if num is None or (isinstance(num, float) and pd.isna(num)):
        return "0"
    s = f"{num}"
    return s.rstrip('0').rstrip('.') if '.' in s else s

def _metric_card(label, value, sub="", val_color="var(--text-primary)"):
    sub_html = f'<div class="metric-sub" style="color:{val_color}">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card">'
        f'  <div class="metric-label">{label}</div>'
        f'  <div class="metric-value" style="color:{val_color}">{value}</div>'
        f'  {sub_html}'
        f'</div>'
    )

def _page_title(icon, title, subtitle=""):
    sub_html = f'<span style="font-size:0.78em;color:var(--text-muted);font-weight:400;margin-left:8px">{subtitle}</span>' if subtitle else ""
    st.markdown(
        f'<div class="page-title"><h2>{icon} {title}{sub_html}</h2></div>',
        unsafe_allow_html=True
    )

# =====================================================================
#  🏠 股票详情中心（一体化视图）
# =====================================================================
if choice == "🏠 股票详情中心":
    all_stocks = get_dynamic_stock_list()
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)

    # ── 顶部标题 ──
    _page_title("🏠", "股票详情中心", "单股全景 · 一页尽览")

    # ── 股票选择器（原生 selectbox，可靠响应） ──
    if all_stocks:
        # 保持上次选中的股票（跨次重跑不丢失）
        _prev = st.session_state.get("detail_selected_stock", all_stocks[0])
        _prev_idx = all_stocks.index(_prev) if _prev in all_stocks else 0
        selected_stock = st.selectbox(
            "🔍 选择股票",
            all_stocks,
            index=_prev_idx,
            key="detail_stock_selectbox",
        )
        st.session_state["detail_selected_stock"] = selected_stock
    else:
        selected_stock = None

    # ── 自动更新全部现价（带缓存，5分钟内不重复拉取） ──
    _now_ts = datetime.now().timestamp()
    _last_fetch_ts = st.session_state.get("_prices_fetch_ts", 0)
    _cache_ttl = 300   # 秒（5分钟）
    if all_stocks and (_now_ts - _last_fetch_ts > _cache_ttl):
        with st.spinner("正在获取最新行情…"):
            _auto_fetched = fetch_latest_prices(all_stocks)
        # 无论接口是否返回数据，都更新时间戳，避免每次刷新都重试
        st.session_state["_prices_fetch_ts"] = _now_ts
        if _auto_fetched:
            for _name, _price in _auto_fetched.items():
                if _price and _price > 0:   # 只写入有效价格，绝不用0覆盖历史价格
                    _old = c.execute("SELECT current_price, manual_cost FROM prices WHERE code = ?", (_name,)).fetchone()
                    _mc  = float(_old[1]) if _old and _old[1] is not None else 0.0
                    c.execute("INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?,?,?)",
                              (_name, _price, _mc))
            conn.commit()
            sync_db_to_github()

    latest_prices_data = {row[0]: (row[1] or 0.0, row[2] or 0.0) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}
    manual_costs  = {k: v[1] for k, v in latest_prices_data.items()}

    if selected_stock:
        s_df   = df_trades[df_trades['code'] == selected_stock].copy()
        now_p  = latest_prices.get(selected_stock) or 0.0

        # ── 盈亏计算 ──
        realized_profit = 0.0
        max_occupied_amount = 0.0
        buy_pool  = []
        sell_pool = []
        net_q = 0

        for _, t in s_df.iterrows():
            price = t['price']
            qty   = t['quantity']
            if t['action'] == '买入':
                remaining_to_buy = qty
                while remaining_to_buy > 0 and sell_pool:
                    sell_pool.sort(key=lambda x: x['price'], reverse=True)
                    sp = sell_pool[0]
                    match_q = min(remaining_to_buy, sp['qty'])
                    realized_profit += (sp['price'] - price) * match_q
                    sp['qty'] -= match_q
                    remaining_to_buy -= match_q
                    if sp['qty'] <= 0: sell_pool.pop(0)
                if remaining_to_buy > 0:
                    buy_pool.append({'price': price, 'qty': remaining_to_buy})
                net_q += qty
            else:
                remaining_to_sell = qty
                while remaining_to_sell > 0 and buy_pool:
                    buy_pool.sort(key=lambda x: x['price'])
                    bp = buy_pool[0]
                    match_q = min(remaining_to_sell, bp['qty'])
                    realized_profit += (price - bp['price']) * match_q
                    bp['qty'] -= match_q
                    remaining_to_sell -= match_q
                    if bp['qty'] <= 0: buy_pool.pop(0)
                if remaining_to_sell > 0:
                    sell_pool.append({'price': price, 'qty': remaining_to_sell})
                net_q -= qty
            current_occ = sum(x['price']*x['qty'] for x in buy_pool) + sum(x['price']*x['qty'] for x in sell_pool)
            max_occupied_amount = max(max_occupied_amount, current_occ)

        avg_cost = manual_costs.get(selected_stock, 0.0)
        if net_q > 0:
            holding_profit_amount = (now_p - avg_cost) * net_q
            holding_profit_pct    = (now_p - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        elif net_q < 0:
            holding_profit_amount = (avg_cost - now_p) * abs(net_q)
            holding_profit_pct    = (avg_cost - now_p) / avg_cost * 100 if avg_cost > 0 else 0
        else:
            holding_profit_amount = holding_profit_pct = 0.0

        strategy_data = c.execute(
            "SELECT logic, annual_return, buy_base_price, buy_drop_pct, sell_base_price, sell_rise_pct FROM strategy_notes WHERE code = ?",
            (selected_stock,)
        ).fetchone()
        saved_logic   = strategy_data[0] if strategy_data else ""
        saved_annual  = strategy_data[1] if strategy_data else 0.0
        s_buy_base    = strategy_data[2] if strategy_data else 0.0
        s_buy_drop    = strategy_data[3] if strategy_data else 0.0
        s_sell_base   = strategy_data[4] if strategy_data else 0.0
        s_sell_rise   = strategy_data[5] if strategy_data else 0.0

        buy_monitor_p  = s_buy_base  * (1 - s_buy_drop  / 100) if s_buy_base  > 0 else 0
        sell_monitor_p = s_sell_base * (1 + s_sell_rise / 100) if s_sell_base > 0 else 0
        is_buy_triggered  = (s_buy_base  > 0 and now_p <= buy_monitor_p)
        is_sell_triggered = (s_sell_base > 0 and now_p >= sell_monitor_p)

        pnl_color = "var(--profit)" if holding_profit_amount >= 0 else "var(--loss)"
        pnl_str   = f"+{holding_profit_amount:,.2f}" if holding_profit_amount >= 0 else f"{holding_profit_amount:,.2f}"
        pnl_pct   = f"+{holding_profit_pct:.2f}%" if holding_profit_pct >= 0 else f"{holding_profit_pct:.2f}%"
        rp_color  = "var(--profit)" if realized_profit >= 0 else "var(--loss)"
        rp_str    = f"+{realized_profit:,.2f}" if realized_profit >= 0 else f"{realized_profit:,.2f}"

        b_label = ("🟢 买入监控 · 达标" if is_buy_triggered else "📥 买入监控 · 观察")
        s_label = ("🔴 卖出监控 · 达标" if is_sell_triggered else "📤 卖出监控 · 观察")

        buy_val       = f"{buy_monitor_p:.3f}"  if s_buy_base  > 0 else "—"
        sell_val      = f"{sell_monitor_p:.3f}" if s_sell_base > 0 else "—"
        buy_drop_val  = f"{s_buy_drop:.2f}%"    if s_buy_drop  else "—"
        sell_rise_val = f"{s_sell_rise:.2f}%"   if s_sell_rise else "—"

        b_color = "var(--profit)" if is_buy_triggered  else "var(--text-secondary)"
        s_color = "var(--loss)"   if is_sell_triggered else "var(--text-secondary)"

        # ═══════════════════════════════════════════════
        # 第一行：核心数据卡片（12格网格）
        # ═══════════════════════════════════════════════
        st.markdown(f'<div style="margin-bottom:6px;font-size:0.80em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600">📊 {selected_stock} · 核心数据</div>', unsafe_allow_html=True)

        row1 = [
            _metric_card("持仓数量",   f"{net_q}"),
            _metric_card("持仓市值",   f"{abs(net_q)*now_p:,.2f}"),
            _metric_card("手动成本价", f"{avg_cost:.3f}"),
            _metric_card("当前现价",   f"{now_p:.3f}"),
            _metric_card("持仓盈亏额", pnl_str, sub=pnl_pct, val_color=pnl_color),
            _metric_card("已实现利润", rp_str,  val_color=rp_color),
        ]
        row2 = [
            _metric_card("最高占用金额",   f"{max_occupied_amount:,.2f}"),
            _metric_card("历史年化收益",   f"{saved_annual:.2f}%"),
            _metric_card(b_label,          buy_val,       val_color=b_color),
            _metric_card(s_label,          sell_val,      val_color=s_color),
            _metric_card("📤 卖出上涨比例", sell_rise_val),
            _metric_card("📥 买入下跌比例", buy_drop_val),
        ]
        grid = 'style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:0 0 8px"'
        st.markdown(
            f'<div {grid}>{"".join(row1)}</div>'
            f'<div {grid}>{"".join(row2)}</div>',
            unsafe_allow_html=True
        )

        st.divider()

        # ═══════════════════════════════════════════════
        # 第2行：交易逻辑(左) + 价格目标 & 买卖信号(右)
        # ═══════════════════════════════════════════════
        col_strat, col_target = st.columns([1, 1], gap="medium")

        # ──────────────────────────────────────────────
        # 左列：交易逻辑 & 参数设置
        # ──────────────────────────────────────────────
        with col_strat:
            st.markdown('<div style="font-size:0.85em;font-weight:700;color:var(--accent-teal);margin-bottom:8px;padding-bottom:4px;border-bottom:2px solid var(--accent-teal)">🧠 交易逻辑 & 参数设置</div>', unsafe_allow_html=True)
            with st.form(f"strategy_form_{selected_stock}"):
                new_logic = st.text_area("交易逻辑（买卖原则）", value=saved_logic, height=90,
                                         placeholder="描述该股票的操作策略、买卖原则…",
                                         label_visibility="collapsed",
                                         key=f"sf_logic_{selected_stock}")
                new_annual = st.number_input("📈 年化收益率 (%)", value=float(saved_annual), step=0.01,
                                             key=f"sf_annual_{selected_stock}")
                st.markdown('<div style="font-size:0.75em;color:var(--accent-green);font-weight:600;margin:6px 0 2px">📥 买入监控参数</div>', unsafe_allow_html=True)
                bc1, bc2 = st.columns(2)
                new_buy_base = bc1.number_input("基准价", value=float(s_buy_base), step=0.01, key=f"sf_buy_base_{selected_stock}")
                new_buy_drop = bc2.number_input("下跌 (%)", value=float(s_buy_drop), step=0.1, key=f"sf_buy_drop_{selected_stock}")
                st.markdown('<div style="font-size:0.75em;color:var(--accent-red);font-weight:600;margin:6px 0 2px">📤 卖出监控参数</div>', unsafe_allow_html=True)
                sc1, sc2 = st.columns(2)
                new_sell_base = sc1.number_input("基准价", value=float(s_sell_base), step=0.01, key=f"sf_sell_base_{selected_stock}")
                new_sell_rise = sc2.number_input("上涨 (%)", value=float(s_sell_rise), step=0.1, key=f"sf_sell_rise_{selected_stock}")
                if st.form_submit_button("💾 保存设置", use_container_width=True, type="primary"):
                    c.execute("""
                        INSERT OR REPLACE INTO strategy_notes
                        (code, logic, max_holding_amount, annual_return, buy_base_price, buy_drop_pct, sell_base_price, sell_rise_pct)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (selected_stock, new_logic, max_occupied_amount, new_annual,
                          new_buy_base, new_buy_drop, new_sell_base, new_sell_rise))
                    conn.commit()
                    sync_db_to_github()
                    st.success("✅ 已保存")
                    st.rerun()

        # ──────────────────────────────────────────────
        # 中列：价格目标监控
        # ──────────────────────────────────────────────
        with col_target:
            st.markdown('<div style="font-size:0.85em;font-weight:700;color:var(--accent-blue);margin-bottom:8px;padding-bottom:4px;border-bottom:2px solid var(--accent-blue)">🎯 价格目标监控</div>', unsafe_allow_html=True)

            def ensure_price_target_v2_table_inline():
                c.execute("""CREATE TABLE IF NOT EXISTS price_targets_v2 (
                    code TEXT PRIMARY KEY, buy_high_point REAL, buy_drop_pct REAL,
                    buy_break_status TEXT DEFAULT '未突破', buy_low_after_break REAL,
                    buy_rebound_pct REAL DEFAULT 0.0, sell_low_point REAL, sell_rise_pct REAL,
                    sell_break_status TEXT DEFAULT '未突破', sell_high_after_break REAL,
                    sell_fallback_pct REAL DEFAULT 0.0, last_updated TEXT)""")
                for col_sql in ["ALTER TABLE price_targets_v2 ADD COLUMN buy_rebound_pct REAL DEFAULT 0.0",
                            "ALTER TABLE price_targets_v2 ADD COLUMN sell_fallback_pct REAL DEFAULT 0.0"]:
                    try: c.execute(col_sql)
                    except: pass
                sync_db_to_github()
                conn.commit()

            ensure_price_target_v2_table_inline()

            pt_row = c.execute('SELECT * FROM price_targets_v2 WHERE code = ?', (selected_stock,)).fetchone()
            pt_cfg = {}
            if pt_row:
                cols_pt = [d[0] for d in c.description]
                pt_cfg  = dict(zip(cols_pt, pt_row))

            bhp  = pt_cfg.get('buy_high_point')
            bdp  = pt_cfg.get('buy_drop_pct')
            bbs  = pt_cfg.get('buy_break_status', '未突破')
            blb  = pt_cfg.get('buy_low_after_break')
            brb  = pt_cfg.get('buy_rebound_pct', 0.0) or 0.0
            slp  = pt_cfg.get('sell_low_point')
            srp  = pt_cfg.get('sell_rise_pct')
            sbs  = pt_cfg.get('sell_break_status', '未突破')
            shb  = pt_cfg.get('sell_high_after_break')
            sfb  = pt_cfg.get('sell_fallback_pct', 0.0) or 0.0

            def _pt_status_card(label, base_p, target_p, curr_p, break_status, color):
                if not base_p:
                    return f'<div style="background:var(--bg-elevated);border-radius:8px;padding:10px 14px;margin-bottom:8px;border:1px solid var(--border);font-size:0.83em;color:var(--text-muted)">暂未配置{label}目标</div>'
                dist_pct = round((target_p - curr_p) / curr_p * 100, 2) if curr_p > 0 and target_p else None
                dist_str = f"差 {dist_pct:.2f}%" if dist_pct is not None else "—"
                bs_icon  = "🟢" if break_status == '已突破' else "⏳"
                tgt_str  = f"{target_p:.3f}" if target_p else f"{base_p:.3f}"
                return f'''<div style="background:var(--bg-elevated);border-left:3px solid {color};border-radius:0 8px 8px 0;
                    padding:10px 12px;margin-bottom:8px">
                    <div style="display:flex;justify-content:space-between;align-items:center">
                        <span style="color:{color};font-weight:700;font-size:0.83em">{label}</span>
                        <span style="font-size:0.75em;color:var(--text-muted)">{bs_icon} {break_status}</span>
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:6px">
                        <div><div style="font-size:0.70em;color:var(--text-muted)">目标价</div>
                             <div style="font-size:0.95em;font-weight:700;color:#f0f6ff">{tgt_str}</div></div>
                        <div><div style="font-size:0.70em;color:var(--text-muted)">基准价</div>
                             <div style="font-size:0.82em;color:var(--text-secondary)">{base_p:.3f}</div></div>
                        <div><div style="font-size:0.70em;color:var(--text-muted)">距目标</div>
                             <div style="font-size:0.82em;font-weight:600;color:#fbbf24">{dist_str}</div></div>
                    </div>
                </div>'''

            buy_base_p  = round(bhp * (1 - bdp / 100), 3)  if bhp and bdp  else None
            buy_tgt_p   = round(blb * (1 + brb / 100), 3)  if blb and bbs == '已突破' else buy_base_p
            sell_base_p = round(slp * (1 + srp / 100), 3)  if slp and srp  else None
            sell_tgt_p  = round(shb * (1 - sfb / 100), 3)  if shb and sbs == '已突破' else sell_base_p

            # 状态卡（直接显示）
            st.markdown(
                _pt_status_card("📥 买入目标", buy_base_p, buy_tgt_p, now_p, bbs, "#10b981") +
                _pt_status_card("📤 卖出目标", sell_base_p, sell_tgt_p, now_p, sbs, "#f43f5e"),
                unsafe_allow_html=True
            )

            # 配置表单（折叠）
            with st.expander("⚙️ 修改价格目标配置", expanded=False):
                with st.form(f"pt_inline_form_{selected_stock}"):
                    st.caption("📥 买入体系（高点下跌突破）")
                    pb1, pb2 = st.columns(2)
                    ni_bhp = pb1.number_input("前期高点", value=float(bhp) if bhp else None, step=0.001, format="%.3f", key=f"il_buy_high_{selected_stock}")
                    ni_bdp = pb2.number_input("下跌幅度(%)", value=float(bdp) if bdp else None, step=0.1, format="%.2f", key=f"il_buy_drop_{selected_stock}")
                    ni_bbs = st.selectbox("买入突破状态", ["未突破","已突破"], index=0 if bbs != '已突破' else 1, key=f"il_buy_break_{selected_stock}")
                    ni_blb = ni_brb = None
                    if ni_bbs == "已突破":
                        pb3, pb4 = st.columns(2)
                        ni_blb = pb3.number_input("突破后最低价", value=float(blb) if blb else None, step=0.001, format="%.3f", key=f"il_buy_low_{selected_stock}")
                        ni_brb = pb4.number_input("反弹幅度(%)", value=float(brb), step=0.1, format="%.2f", key=f"il_buy_reb_{selected_stock}")
                    st.caption("📤 卖出体系（低点上涨突破）")
                    ps1, ps2 = st.columns(2)
                    ni_slp = ps1.number_input("前期低点", value=float(slp) if slp else None, step=0.001, format="%.3f", key=f"il_sell_low_{selected_stock}")
                    ni_srp = ps2.number_input("上涨幅度(%)", value=float(srp) if srp else None, step=0.1, format="%.2f", key=f"il_sell_rise_{selected_stock}")
                    ni_sbs = st.selectbox("卖出突破状态", ["未突破","已突破"], index=0 if sbs != '已突破' else 1, key=f"il_sell_break_{selected_stock}")
                    ni_shb = ni_sfb = None
                    if ni_sbs == "已突破":
                        ps3, ps4 = st.columns(2)
                        ni_shb = ps3.number_input("突破后最高价", value=float(shb) if shb else None, step=0.001, format="%.3f", key=f"il_sell_high_{selected_stock}")
                        ni_sfb = ps4.number_input("回落幅度(%)", value=float(sfb), step=0.1, format="%.2f", key=f"il_sell_fall_{selected_stock}")
                    if st.form_submit_button("💾 保存价格目标", use_container_width=True):
                        c.execute("""INSERT OR REPLACE INTO price_targets_v2
                            (code, buy_high_point, buy_drop_pct, buy_break_status, buy_low_after_break, buy_rebound_pct,
                             sell_low_point, sell_rise_pct, sell_break_status, sell_high_after_break, sell_fallback_pct, last_updated)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (selected_stock, ni_bhp, ni_bdp, ni_bbs, ni_blb, ni_brb or 0.0,
                             ni_slp, ni_srp, ni_sbs, ni_shb, ni_sfb or 0.0,
                             datetime.now().strftime('%Y-%m-%d %H:%M')))
                        conn.commit()
                        sync_db_to_github()
                        st.success("✅ 已保存")
                        st.rerun()

            # ── 买卖信号（紧跟价格目标下方） ──
            st.markdown('<div style="font-size:0.83em;font-weight:700;color:var(--accent-amber);margin:12px 0 8px;padding-bottom:4px;border-bottom:2px solid var(--accent-amber)">🔔 买卖信号</div>', unsafe_allow_html=True)
            sig_row = c.execute(
                "SELECT high_point, low_point, up_threshold, down_threshold, high_date, low_date FROM signals WHERE code = ?",
                (selected_stock,)
            ).fetchone()
            if sig_row:
                s_high_pt, s_low_pt, s_up_th, s_down_th, s_h_date, s_l_date = sig_row
                s_high_pt = s_high_pt or 0.0
                s_low_pt  = s_low_pt  or 0.0
                s_up_th   = s_up_th   or 0.0
                s_down_th = s_down_th or 0.0
                dr = ((now_p - s_high_pt) / s_high_pt * 100) if s_high_pt > 0 else 0
                rr = ((now_p - s_low_pt)  / s_low_pt  * 100) if s_low_pt  > 0 else 0
                if rr >= s_up_th:
                    sig_badge = f'<span class="badge badge-sell">🟢 建议卖出</span>'
                    sig_bg = "rgba(244,63,94,0.08)"
                elif dr <= -s_down_th:
                    sig_badge = f'<span class="badge badge-buy">🔴 建议买入</span>'
                    sig_bg = "rgba(16,185,129,0.08)"
                else:
                    sig_badge = f'<span class="badge badge-hold">⚖️ 观望</span>'
                    sig_bg = "rgba(245,158,11,0.08)"
                dr_cls = "profit-red" if dr >= 0 else "loss-green"
                rr_cls = "profit-red" if rr >= 0 else "loss-green"
                st.markdown(f'''
                <div style="background:{sig_bg};border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:8px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                        <span style="font-size:0.80em;color:var(--text-secondary)">信号状态</span>
                        {sig_badge}
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:0.80em">
                        <div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:7px">
                            <div style="color:var(--text-muted);font-size:0.78em">高点 {s_h_date}</div>
                            <div style="font-weight:600">{s_high_pt}</div>
                            <div class="{dr_cls}" style="font-size:0.83em">距高点 {dr:.2f}%</div>
                        </div>
                        <div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:7px">
                            <div style="color:var(--text-muted);font-size:0.78em">低点 {s_l_date}</div>
                            <div style="font-weight:600">{s_low_pt}</div>
                            <div class="{rr_cls}" style="font-size:0.83em">距低点 {rr:.2f}%</div>
                        </div>
                    </div>
                    <div style="margin-top:4px;display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:0.78em">
                        <div style="color:var(--text-muted)">卖出触发: <span style="color:var(--text-primary);font-weight:600">+{s_up_th}%</span></div>
                        <div style="color:var(--text-muted)">买入触发: <span style="color:var(--text-primary);font-weight:600">-{s_down_th}%</span></div>
                    </div>
                </div>
                ''', unsafe_allow_html=True)
            else:
                st.markdown('<div style="color:var(--text-muted);font-size:0.82em;padding:10px;background:var(--bg-elevated);border-radius:8px;text-align:center">暂无信号配置</div>', unsafe_allow_html=True)

        # ═══════════════════════════════════════════════
        # 第3行：决策历史（全宽）
        # ═══════════════════════════════════════════════
        st.divider()
        st.markdown('<div style="font-size:0.85em;font-weight:700;color:var(--accent-purple);margin-bottom:8px;padding-bottom:4px;border-bottom:2px solid var(--accent-purple)">📜 决策历史</div>', unsafe_allow_html=True)
        with st.form("new_decision", clear_on_submit=True):
            dc1, dc2 = st.columns(2)
            d_content = dc1.text_area("决策内容", placeholder="例如：减仓30%", height=68, label_visibility="visible")
            d_reason  = dc2.text_area("决策原因（可选）", placeholder="为什么做这个决策？", height=68, label_visibility="visible")
            d_date    = datetime.now()
            if st.form_submit_button("➕ 记录决策", use_container_width=True):
                c.execute("INSERT INTO decision_history (code, date, decision, reason) VALUES (?,?,?,?)",
                          (selected_stock, d_date.strftime('%Y-%m-%d'), d_content, d_reason))
                conn.commit()
                sync_db_to_github()
                st.rerun()

        # textarea 自动增高 JS（直接注入主文档，不经过 iframe）
        st.markdown("""<script>
        (function(){
            function grow(){
                document.querySelectorAll('textarea').forEach(function(ta){
                    if(ta.dataset.autogrow) return;
                    ta.dataset.autogrow = '1';
                    ta.style.minHeight = '68px';
                    ta.style.overflow = 'hidden';
                    function fit(){
                        ta.style.height = 'auto';
                        ta.style.height = Math.max(68, ta.scrollHeight) + 'px';
                    }
                    fit();
                    ta.addEventListener('input', fit);
                });
            }
            grow();
            setTimeout(grow, 500);
            setTimeout(grow, 1500);
            setTimeout(grow, 3000);
            var ob = new MutationObserver(function(){ setTimeout(grow, 200); });
            ob.observe(document.body, {childList:true, subtree:true});
            setTimeout(function(){ ob.disconnect(); }, 10000);
        })();
        </script>""", unsafe_allow_html=True)

        decisions = pd.read_sql(
            "SELECT id, date, decision, reason FROM decision_history WHERE code = ? ORDER BY date DESC LIMIT 15",
            conn, params=(selected_stock,)
        )
        if decisions.empty:
            st.markdown('<div style="color:var(--text-muted);font-size:0.82em;padding:8px;text-align:center">暂无决策记录</div>', unsafe_allow_html=True)
        else:
            for _, row in decisions.iterrows():
                head_col, del_col = st.columns([20, 1])
                head_col.markdown(
                    f'<div class="decision-card" style="display:flex;gap:16px;align-items:baseline">'
                    f'<div style="flex-shrink:0;font-weight:600;font-size:0.82em;color:var(--accent-blue);min-width:80px">{row["date"]}</div>'
                    f'<div style="font-size:0.85em;color:var(--text-primary);min-width:120px">{row["decision"]}</div>'
                    + (f'<div style="font-size:0.77em;color:var(--text-secondary)">{row["reason"]}</div>' if row["reason"] else "")
                    + '</div>',
                    unsafe_allow_html=True
                )
                if del_col.button("✕", key=f"del_dec_{row['id']}", help="删除"):
                    c.execute("DELETE FROM decision_history WHERE id = ?", (row['id'],))
                    conn.commit()
                    sync_db_to_github()
                    st.rerun()

        st.divider()

        # ═══════════════════════════════════════════════
        # 第4行：交易配对与未平仓单 + 历史交易明细（并排）
        # ═══════════════════════════════════════════════
        col_trade_pair, col_trade_hist = st.columns([5, 4], gap="medium")

        # ──────────────────────────────────────────────
        # 左：交易配对与未平仓单
        # ──────────────────────────────────────────────
        with col_trade_pair:
            st.markdown('<div style="font-size:0.88em;font-weight:700;color:var(--accent-green);margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--border)">🔗 交易配对与未平仓单</div>', unsafe_allow_html=True)

            # 重新计算本股票配对信息
            pair_buy_positions  = []
            pair_sell_positions = []
            pair_paired_trades  = []

            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                t_date  = trade['date']
                t_act   = trade['action']
                t_price = trade['price']
                t_qty   = trade['quantity']
                remaining = t_qty

                if t_act == '买入':
                    if pair_sell_positions and remaining > 0:
                        for sp in sorted(pair_sell_positions, key=lambda x: -x['price']):
                            if remaining <= 0: break
                            if sp['qty'] <= 0: continue
                            cover_qty = min(sp['qty'], remaining)
                            gain = ((sp['price'] - t_price) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                            pair_paired_trades.append({
                                "日期": f"{sp['date']} → {t_date}",
                                "类型": "✅ 配对闭合",
                                "价格": f"{format_number(sp['price'])} → {format_number(t_price)}",
                                "数量": cover_qty,
                                "盈亏%": gain
                            })
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        pair_sell_positions = [sp for sp in pair_sell_positions if sp['qty'] > 0]
                    if remaining > 0:
                        pair_buy_positions.append({'date': t_date, 'price': t_price, 'qty': remaining})

                elif t_act == '卖出':
                    if pair_buy_positions and remaining > 0:
                        for bp in sorted(pair_buy_positions, key=lambda x: x['price']):
                            if remaining <= 0: break
                            if bp['qty'] <= 0: continue
                            close_qty = min(bp['qty'], remaining)
                            gain = ((t_price - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                            pair_paired_trades.append({
                                "日期": f"{bp['date']} → {t_date}",
                                "类型": "✅ 配对闭合",
                                "价格": f"{format_number(bp['price'])} → {format_number(t_price)}",
                                "数量": close_qty,
                                "盈亏%": gain
                            })
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        pair_buy_positions = [bp for bp in pair_buy_positions if bp['qty'] > 0]
                    if remaining > 0:
                        pair_sell_positions.append({'date': t_date, 'price': t_price, 'qty': remaining})

            # 未平仓单
            open_positions = []
            for bp in pair_buy_positions:
                float_gain = ((now_p - bp['price']) / bp['price'] * 100) if bp['price'] > 0 and now_p > 0 else 0.0
                open_positions.append({
                    "日期": bp['date'], "类型": "🔴 买入持有",
                    "价格": format_number(bp['price']), "数量": bp['qty'], "盈亏%": float_gain
                })
            for sp in pair_sell_positions:
                float_gain = ((sp['price'] - now_p) / sp['price'] * 100) if sp['price'] > 0 and now_p > 0 else 0.0
                open_positions.append({
                    "日期": sp['date'], "类型": "🟢 卖空持有",
                    "价格": format_number(sp['price']), "数量": sp['qty'], "盈亏%": float_gain
                })

            # 未平仓单展示（优先显示）
            if open_positions:
                st.markdown(f'<div style="font-size:0.80em;font-weight:600;color:var(--accent-amber);margin-bottom:6px">⚡ 未平仓单（{len(open_positions)} 笔）</div>', unsafe_allow_html=True)
                html_open = '<table class="pro-table"><thead><tr><th>建仓日期</th><th>方向</th><th>成本价</th><th>数量</th><th>浮盈亏</th></tr></thead><tbody>'
                for r in open_positions:
                    cls = "profit-red" if r['盈亏%'] > 0 else ("loss-green" if r['盈亏%'] < 0 else "")
                    html_open += f'<tr><td>{r["日期"]}</td><td>{r["类型"]}</td><td>{r["价格"]}</td><td>{r["数量"]}</td><td class="{cls}">{r["盈亏%"]:.2f}%</td></tr>'
                html_open += '</tbody></table>'
                st.markdown(html_open, unsafe_allow_html=True)
            else:
                st.markdown('<div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);border-radius:8px;padding:10px 14px;font-size:0.85em;color:var(--accent-green);margin-bottom:8px">✅ 当前无未平仓单，持仓已全部平仓</div>', unsafe_allow_html=True)

            # 已配对交易
            if pair_paired_trades:
                with st.expander(f"📋 历史配对记录（共 {len(pair_paired_trades)} 笔）", expanded=False):
                    html_pair = '<table class="pro-table"><thead><tr><th>交易时间段</th><th>状态</th><th>进出价</th><th>数量</th><th>盈亏%</th></tr></thead><tbody>'
                    for r in pair_paired_trades:
                        cls = "profit-red" if r['盈亏%'] > 0 else ("loss-green" if r['盈亏%'] < 0 else "")
                        html_pair += f'<tr><td style="font-size:0.85em">{r["日期"]}</td><td>{r["类型"]}</td><td>{r["价格"]}</td><td>{r["数量"]}</td><td class="{cls}">{r["盈亏%"]:.2f}%</td></tr>'
                    html_pair += '</tbody></table>'
                    st.markdown(html_pair, unsafe_allow_html=True)
            else:
                st.markdown('<div style="color:var(--text-muted);font-size:0.83em;padding:8px 0">暂无已配对交易记录</div>', unsafe_allow_html=True)

        # ──────────────────────────────────────────────
        # 右：历史交易明细
        # ──────────────────────────────────────────────
        with col_trade_hist:
            st.markdown('<div style="font-size:0.88em;font-weight:700;color:var(--accent-blue);margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--border)">📋 历史交易明细</div>', unsafe_allow_html=True)

            if s_df.empty:
                st.markdown('<div style="color:var(--text-muted);font-size:0.85em;padding:12px;text-align:center">暂无交易记录</div>', unsafe_allow_html=True)
            else:
                hist_df = s_df.sort_values(['date', 'id'], ascending=[False, False]).head(30)
                html_hist = '<table class="pro-table"><thead><tr><th>日期</th><th>操作</th><th>价格</th><th>数量</th><th>金额</th><th>备注</th></tr></thead><tbody>'
                for _, hr in hist_df.iterrows():
                    act_html = '<span class="badge badge-buy">买入</span>' if hr['action'] == '买入' else '<span class="badge badge-sell">卖出</span>'
                    note_str = str(hr['note']).strip() if pd.notna(hr['note']) and str(hr['note']).strip() not in ['', 'nan'] else '—'
                    amt = hr['price'] * hr['quantity']
                    html_hist += f'<tr><td style="font-size:0.88em">{hr["date"]}</td><td>{act_html}</td><td>{hr["price"]:.3f}</td><td>{int(hr["quantity"])}</td><td style="font-size:0.88em">{amt:,.0f}</td><td style="font-size:0.83em;color:var(--text-secondary)">{note_str}</td></tr>'
                html_hist += '</tbody></table>'
                st.markdown(html_hist, unsafe_allow_html=True)
                total_trades = len(s_df)
                if total_trades > 30:
                    st.caption(f"📌 仅展示最近 30 笔，共 {total_trades} 笔 · 完整记录请查看「📜 历史明细」")

        st.divider()

        # ═══════════════════════════════════════════════
        # 第四行：复盘日记（底部，可折叠）
        # ═══════════════════════════════════════════════
        with st.expander(f"📓 {selected_stock} 复盘日记", expanded=False):
            st.caption("🎨 支持 HTML 颜色标签，如 <span style='color:#f59e0b'>重点文字</span>")
            jcol1, jcol2 = st.columns([4, 1])
            new_journal_content = jcol1.text_area("写新日记", height=80, placeholder="支持换行、列表、空格等格式……", label_visibility="collapsed")
            if jcol2.button("📌 存档", type="primary", use_container_width=True):
                if new_journal_content.strip():
                    c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                              (datetime.now().strftime('%Y-%m-%d'), selected_stock, new_journal_content.strip()))
                    conn.commit()
                    sync_db_to_github()
                    st.success("✅ 已存档")
                    st.rerun()
                else:
                    st.warning("⚠️ 请填写内容")

            journal_rows = pd.read_sql(
                "SELECT id, date, stock_name, content FROM journal WHERE stock_name = ? ORDER BY date DESC, id DESC",
                conn, params=(selected_stock,)
            )
            if journal_rows.empty:
                st.info(f"📌 暂无「{selected_stock}」复盘记录")
            else:
                for _, jrow in journal_rows.iterrows():
                    jc1, jc2 = st.columns([12, 1])
                    with jc1:
                        st.markdown(
                            f'<div class="journal-card">'
                            f'<div class="journal-meta">'
                            f'<span style="background:rgba(59,130,246,0.15);color:var(--accent-blue);border-radius:4px;padding:1px 8px;font-weight:600">{jrow["stock_name"]}</span>'
                            f'<span>{jrow["date"]}</span>'
                            f'</div>'
                            f'<div class="journal-content">{jrow["content"]}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                    with jc2:
                        if st.button("✕", key=f"jdel_{jrow['id']}", help="删除"):
                            c.execute("DELETE FROM journal WHERE id = ?", (jrow['id'],))
                            conn.commit()
                            sync_db_to_github()
                            st.rerun()

    else:
        st.info("💡 请先在交易录入中添加股票数据")

# =====================================================================
#  📊 实时持仓
# =====================================================================
elif choice == "📊 实时持仓":
    _page_title("📊", "实时持仓", "手动成本模式")

    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)

    if not df_trades.empty:
        stocks = df_trades['code'].unique()

        with st.expander("🛠️ 维护现价与手动成本", expanded=True):
            raw_prices  = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
            config_query = {row[0]: (row[1], row[2]) for row in raw_prices}

            # ── 自动更新现价 ──
            if _YF_OK:
                _btn_col, _tip_col = st.columns([1, 3])
                if _btn_col.button("🔄 自动更新全部现价", type="primary", use_container_width=True):
                    with _tip_col:
                        with st.spinner("正在拉取最新行情，请稍候…"):
                            _fetched = fetch_latest_prices(list(stocks))
                    if _fetched:
                        for _name, _price in _fetched.items():
                            _old = config_query.get(_name, (0.0, 0.0))
                            _mc  = float(_old[1]) if _old[1] is not None else 0.0
                            c.execute(
                                "INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?,?,?)",
                                (_name, _price, _mc)
                            )
                            # 直接更新 session_state 为新价格，让 rerun 后输入框显示最新值
                            st.session_state[f"p_{_name}"] = _price
                        conn.commit()
                        sync_db_to_github()
                        _detail = "  |  ".join([f"{k} → {v}" for k, v in _fetched.items()])
                        _tip_col.success(f"✅ 已更新 {len(_fetched)} 只：{_detail}")
                        _no_map = [s for s in stocks if s not in TICKER_MAP]
                        if _no_map:
                            _tip_col.warning(f"⚠️ 未配置 Ticker（需手动维护）：{'、'.join(_no_map)}")
                        # 强制重新加载页面以显示最新数据
                        st.rerun()
                    else:
                        _tip_col.error("❌ 获取失败，请检查网络后重试，或手动填写现价")
            else:
                st.info("💡 在 requirements.txt 中添加 `yfinance` 后即可启用自动更新现价功能")

            st.markdown("---")

            for stock in stocks:
                col1, col2 = st.columns(2)
                stored_vals = config_query.get(stock, (0.0, 0.0))
                old_p = float(stored_vals[0]) if stored_vals[0] is not None else 0.0
                old_c = float(stored_vals[1]) if stored_vals[1] is not None else 0.0
                new_p = col1.number_input(f"{stock} 现价",     value=old_p, key=f"p_{stock}", step=0.0001)
                new_c = col2.number_input(f"{stock} 手动成本", value=old_c, key=f"c_{stock}", step=0.0001)
                if new_p != old_p or new_c != old_c:
                    c.execute("INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?, ?, ?)",
                              (stock, new_p, new_c))
                    conn.commit()
                    sync_db_to_github()

        final_raw     = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
        latest_config = {row[0]: (row[1] or 0.0, row[2] or 0.0) for row in final_raw}

        summary = []
        all_active_records = []

        for stock in stocks:
            s_df   = df_trades[df_trades['code'] == stock].copy()
            now_p, manual_cost = latest_config.get(stock, (0.0, 0.0))
            net_buy  = s_df[s_df['action'] == '买入']['quantity'].sum()
            net_sell = s_df[s_df['action'] == '卖出']['quantity'].sum()
            net_q    = net_buy - net_sell

            if net_q != 0:
                if manual_cost > 0:
                    p_rate = ((now_p - manual_cost) / manual_cost * 100) if net_q > 0 else ((manual_cost - now_p) / manual_cost * 100)
                else:
                    p_rate = 0.0
                summary.append([stock, net_q, format_number(manual_cost), format_number(now_p), f"{p_rate:.2f}%", p_rate])

            buy_positions  = []
            sell_positions = []
            paired_trades  = []

            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                trade_date = trade['date']
                action     = trade['action']
                price      = trade['price']
                qty        = trade['quantity']
                remaining  = qty

                if action == '买入':
                    if sell_positions and remaining > 0:
                        for sp in sorted(sell_positions, key=lambda x: -x['price']):
                            if remaining <= 0: break
                            if sp['qty'] <= 0: continue
                            cover_qty = min(sp['qty'], remaining)
                            gain = ((sp['price'] - price) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                            paired_trades.append({
                                "date": f"{sp['date']} → {trade_date}", "code": stock,
                                "type": "✅ 已配对交易对",
                                "price": f"{format_number(sp['price'])} → {format_number(price)}",
                                "qty": cover_qty, "gain_str": f"{gain:.2f}%", "gain_val": gain
                            })
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        sell_positions = [sp for sp in sell_positions if sp['qty'] > 0]
                    if remaining > 0:
                        buy_positions.append({'date': trade_date, 'price': price, 'qty': remaining})

                elif action == '卖出':
                    if buy_positions and remaining > 0:
                        for bp in sorted(buy_positions, key=lambda x: x['price']):
                            if remaining <= 0: break
                            if bp['qty'] <= 0: continue
                            close_qty = min(bp['qty'], remaining)
                            gain = ((price - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                            paired_trades.append({
                                "date": f"{bp['date']} → {trade_date}", "code": stock,
                                "type": "✅ 已配对交易对",
                                "price": f"{format_number(bp['price'])} → {format_number(price)}",
                                "qty": close_qty, "gain_str": f"{gain:.2f}%", "gain_val": gain
                            })
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        buy_positions = [bp for bp in buy_positions if bp['qty'] > 0]
                    if remaining > 0:
                        sell_positions.append({'date': trade_date, 'price': price, 'qty': remaining})

            for bp in buy_positions:
                float_gain = ((now_p - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": bp['date'], "code": stock, "type": "🔴 买入持有",
                    "price": format_number(bp['price']), "qty": bp['qty'],
                    "gain_str": f"{float_gain:.2f}%", "gain_val": float_gain
                })
            for sp in sell_positions:
                float_gain = ((sp['price'] - now_p) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": sp['date'], "code": stock, "type": "🟢 卖空持有",
                    "price": format_number(sp['price']), "qty": sp['qty'],
                    "gain_str": f"{float_gain:.2f}%", "gain_val": float_gain
                })

            all_active_records = paired_trades + all_active_records

        # ── 两栏布局：持仓概览 ＋ 未平仓单 ──
        ov_col, open_col = st.columns([4, 5], gap="medium")

        with ov_col:
            st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px">1️⃣ 账户持仓概览</div>', unsafe_allow_html=True)
            if summary:
                summary.sort(key=lambda x: x[5], reverse=True)
                html = '<table class="pro-table"><thead><tr><th>股票</th><th>净持仓</th><th>手动成本</th><th>现价</th><th>盈亏%</th></tr></thead><tbody>'
                for r in summary:
                    cls = "profit-red" if r[5] > 0 else ("loss-green" if r[5] < 0 else "")
                    html += f'<tr><td><b>{r[0]}</b></td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td class="{cls}">{r[4]}</td></tr>'
                html += '</tbody></table>'
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.info("📌 目前账户无任何净持仓")

        with open_col:
            # 分开：未平仓单 vs 已配对
            open_records  = [r for r in all_active_records if r["type"] in ("🔴 买入持有", "🟢 卖空持有")]
            paired_records = [r for r in all_active_records if r["type"] == "✅ 已配对交易对"]

            # 未平仓单（高优先度，始终展示）
            st.markdown(
                f'<div style="font-size:0.82em;color:var(--accent-amber);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px">'
                f'⚡ 未平仓单（{len(open_records)} 笔）</div>',
                unsafe_allow_html=True
            )
            if open_records:
                html_open = '<table class="pro-table"><thead><tr><th>建仓日期</th><th>股票</th><th>方向</th><th>成本价</th><th>数量</th><th>浮盈亏</th></tr></thead><tbody>'
                for r in open_records:
                    cls = "profit-red" if r['gain_val'] > 0 else ("loss-green" if r['gain_val'] < 0 else "")
                    html_open += f'<tr><td>{r["date"]}</td><td><b>{r["code"]}</b></td><td>{r["type"]}</td><td>{r["price"]}</td><td>{r["qty"]}</td><td class="{cls}">{r["gain_str"]}</td></tr>'
                html_open += '</tbody></table>'
                st.markdown(html_open, unsafe_allow_html=True)
            else:
                st.markdown('<div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);border-radius:8px;padding:10px 14px;font-size:0.85em;color:var(--accent-green)">✅ 当前无未平仓单，所有仓位已平仓</div>', unsafe_allow_html=True)

        st.divider()

        # ── 已配对交易（独立区块，可筛选） ──
        st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin:8px 0 8px">2️⃣  已配对交易明细</div>', unsafe_allow_html=True)

        with st.expander("🔍 筛选条件", expanded=False):
            col1, col2, col3 = st.columns(3)
            stock_filter = col1.text_input("筛选股票", placeholder="代码/名称")
            min_gain     = col2.number_input("最小盈亏(%)", value=-100.0, step=0.1)
            max_gain     = col3.number_input("最大盈亏(%)", value=100.0,  step=0.1)

        filtered_records = paired_records.copy()
        if stock_filter:
            filtered_records = [r for r in filtered_records if stock_filter.lower() in r["code"].lower()]
        if not (min_gain == -100 and max_gain == 100):
            filtered_records = [r for r in filtered_records if min_gain <= r['gain_val'] <= max_gain]

        if filtered_records:
            sort_option = st.selectbox("排序方式", ["盈亏降序", "盈亏升序", "日期降序", "日期升序"])
            if sort_option == "盈亏降序":   filtered_records.sort(key=lambda x: x['gain_val'], reverse=True)
            elif sort_option == "盈亏升序": filtered_records.sort(key=lambda x: x['gain_val'])
            elif sort_option == "日期降序": filtered_records.sort(key=lambda x: x['date'], reverse=True)
            elif sort_option == "日期升序": filtered_records.sort(key=lambda x: x['date'])

            html = '<table class="pro-table"><thead><tr><th>交易时间段</th><th>股票</th><th>进出价格</th><th>数量</th><th>盈亏 %</th></tr></thead><tbody>'
            for r in filtered_records:
                cls  = "profit-red" if r['gain_val'] > 0 else ("loss-green" if r['gain_val'] < 0 else "")
                html += f'<tr><td>{r["date"]}</td><td><b>{r["code"]}</b></td><td>{r["price"]}</td><td>{r["qty"]}</td><td class="{cls}">{r["gain_str"]}</td></tr>'
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("📌 暂无已配对交易记录")
    else:
        st.info("📌 交易数据库为空，请先录入交易记录")

# =====================================================================
#  💰 盈利账单
# =====================================================================
elif choice == "💰 盈利账单":
    _page_title("💰", "盈利账单", "已平仓 + 未平仓")

    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    latest_prices_data = {row[0]: (row[1] or 0.0, row[2] or 0.0) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}

    if not df_trades.empty:
        profit_list = []
        for stock in df_trades['code'].unique():
            s_df  = df_trades[df_trades['code'] == stock].copy()
            now_p = latest_prices.get(stock, 0.0)

            realized_profit = unrealized_profit = 0.0
            buy_pool  = []
            sell_pool = []

            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                action    = trade['action']
                price     = trade['price']
                qty       = trade['quantity']
                remaining = qty

                if action == '买入':
                    if sell_pool and remaining > 0:
                        for sp in sorted(sell_pool, key=lambda x: -x['price']):
                            if remaining <= 0: break
                            if sp['qty'] <= 0: continue
                            cover_qty = min(sp['qty'], remaining)
                            realized_profit += (sp['price'] - price) * cover_qty
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        sell_pool = [sp for sp in sell_pool if sp['qty'] > 0]
                    if remaining > 0:
                        buy_pool.append({'price': price, 'qty': remaining})
                else:
                    if buy_pool and remaining > 0:
                        for bp in sorted(buy_pool, key=lambda x: x['price']):
                            if remaining <= 0: break
                            if bp['qty'] <= 0: continue
                            close_qty = min(bp['qty'], remaining)
                            realized_profit += (price - bp['price']) * close_qty
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        buy_pool = [bp for bp in buy_pool if bp['qty'] > 0]
                    if remaining > 0:
                        sell_pool.append({'price': price, 'qty': remaining})

            for bp in buy_pool:  unrealized_profit += (now_p - bp['price']) * bp['qty']
            for sp in sell_pool: unrealized_profit += (sp['price'] - now_p) * sp['qty']

            long_value  = sum(bp['qty'] for bp in buy_pool)  * now_p
            short_value = -sum(sp['qty'] for sp in sell_pool) * now_p
            current_value = long_value + short_value
            total_profit  = realized_profit + unrealized_profit

            total_buy_cash  = s_df[s_df['action'] == '买入'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            total_sell_cash = s_df[s_df['action'] == '卖出'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()

            profit_list.append({
                "股票名称": stock, "累计投入": total_buy_cash, "累计回收": total_sell_cash,
                "已实现盈亏": realized_profit, "未实现盈亏": unrealized_profit,
                "持仓市值": current_value, "总盈亏": total_profit
            })

        pdf = pd.DataFrame(profit_list).sort_values(by="总盈亏", ascending=False)

        total_realized   = pdf['已实现盈亏'].sum()
        total_unrealized = pdf['未实现盈亏'].sum()
        total_overall    = pdf['总盈亏'].sum()

        c1, c2, c3 = st.columns(3)
        c1.metric("📌 已实现盈亏", f"{total_realized:,.2f}")
        c2.metric("⏳ 未实现盈亏", f"{total_unrealized:,.2f}")
        c3.metric("🏦 账户总体贡献", f"{total_overall:,.2f}")

        st.divider()
        st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px">📊 各股票盈亏明细</div>', unsafe_allow_html=True)

        html = '<table class="pro-table"><thead><tr><th>股票</th><th>累计投入</th><th>累计回收</th><th>已实现盈亏</th><th>未实现盈亏</th><th>持仓市值</th><th>总盈亏</th></tr></thead><tbody>'
        for _, r in pdf.iterrows():
            t_cls  = "profit-red" if r['总盈亏']     > 0 else ("loss-green" if r['总盈亏']     < 0 else "")
            r_cls  = "profit-red" if r['已实现盈亏'] > 0 else ("loss-green" if r['已实现盈亏'] < 0 else "")
            u_cls  = "profit-red" if r['未实现盈亏'] > 0 else ("loss-green" if r['未实现盈亏'] < 0 else "")
            html += f"""<tr>
                <td><b>{r['股票名称']}</b></td>
                <td>{r['累计投入']:,.2f}</td>
                <td>{r['累计回收']:,.2f}</td>
                <td class='{r_cls}'>{r['已实现盈亏']:,.2f}</td>
                <td class='{u_cls}'>{r['未实现盈亏']:,.2f}</td>
                <td>{r['持仓市值']:,.2f}</td>
                <td class='{t_cls}'>{r['总盈亏']:,.2f}</td>
            </tr>"""
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("📌 交易数据库为空，请先录入交易记录")

# =====================================================================
#  🎯 价格目标管理
# =====================================================================
elif choice == "🎯 价格目标管理":
    _page_title("🎯", "价格目标管理", "突破监控体系")

    def ensure_price_target_v2_table():
        c.execute("""CREATE TABLE IF NOT EXISTS price_targets_v2 (
            code TEXT PRIMARY KEY, buy_high_point REAL, buy_drop_pct REAL,
            buy_break_status TEXT DEFAULT '未突破', buy_low_after_break REAL,
            buy_rebound_pct REAL DEFAULT 0.0, sell_low_point REAL, sell_rise_pct REAL,
            sell_break_status TEXT DEFAULT '未突破', sell_high_after_break REAL,
            sell_fallback_pct REAL DEFAULT 0.0, last_updated TEXT)""")
        for col in ["ALTER TABLE price_targets_v2 ADD COLUMN buy_rebound_pct REAL DEFAULT 0.0",
                    "ALTER TABLE price_targets_v2 ADD COLUMN sell_fallback_pct REAL DEFAULT 0.0"]:
            try: c.execute(col)
            except: pass
        conn.commit()

    ensure_price_target_v2_table()

    def get_current_price(code):
        r = c.execute("SELECT current_price FROM prices WHERE code = ?", (code,)).fetchone()
        return float(r[0]) if r and r[0] else 0.0

    def save_price_target_v2(code, data):
        c.execute("""INSERT OR REPLACE INTO price_targets_v2
            (code, buy_high_point, buy_drop_pct, buy_break_status, buy_low_after_break, buy_rebound_pct,
             sell_low_point, sell_rise_pct, sell_break_status, sell_high_after_break, sell_fallback_pct, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, data.get('buy_high_point'), data.get('buy_drop_pct'), data.get('buy_break_status','未突破'),
             data.get('buy_low_after_break'), data.get('buy_rebound_pct'),
             data.get('sell_low_point'), data.get('sell_rise_pct'), data.get('sell_break_status','未突破'),
             data.get('sell_high_after_break'), data.get('sell_fallback_pct'),
             datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit()
        sync_db_to_github()

    def load_price_target_v2(code):
        row = c.execute('SELECT * FROM price_targets_v2 WHERE code = ?', (code,)).fetchone()
        if row:
            d = dict(zip([col[0] for col in c.description], row))
            return {
                'buy_high_point': d.get('buy_high_point'), 'buy_drop_pct': d.get('buy_drop_pct'),
                'buy_break_status': d.get('buy_break_status','未突破'), 'buy_low_after_break': d.get('buy_low_after_break'),
                'buy_rebound_pct': d.get('buy_rebound_pct', 0.0),
                'sell_low_point': d.get('sell_low_point'), 'sell_rise_pct': d.get('sell_rise_pct'),
                'sell_break_status': d.get('sell_break_status','未突破'), 'sell_high_after_break': d.get('sell_high_after_break'),
                'sell_fallback_pct': d.get('sell_fallback_pct', 0.0)
            }
        return None

    def delete_price_target_v2(code):
        c.execute('DELETE FROM price_targets_v2 WHERE code = ?', (code,))
        conn.commit()
        sync_db_to_github()

    def calc_buy_target(config, current_price):
        r = {'base_price': None, 'buy_target': None, 'rebound_pct': None, 'to_target_pct': None}
        hp, dp = config.get('buy_high_point'), config.get('buy_drop_pct')
        if not hp or not dp: return r
        r['base_price'] = round(hp * (1 - dp / 100), 3)
        if config.get('buy_break_status') == '已突破':
            lb = config.get('buy_low_after_break')
            rb = config.get('buy_rebound_pct', 0.0)
            if lb:
                r['buy_target']  = round(lb * (1 + rb / 100), 3)
                r['rebound_pct'] = rb
                if current_price > 0:
                    r['to_target_pct'] = round((r['buy_target'] - current_price) / current_price * 100, 2)
        return r

    def calc_sell_target(config, current_price):
        r = {'base_price': None, 'sell_target': None, 'fallback_pct': None, 'to_target_pct': None}
        lp, rp = config.get('sell_low_point'), config.get('sell_rise_pct')
        if not lp or not rp: return r
        r['base_price'] = round(lp * (1 + rp / 100), 3)
        if config.get('sell_break_status') == '已突破':
            ha = config.get('sell_high_after_break')
            fb = config.get('sell_fallback_pct', 0.0)
            if ha:
                r['sell_target']  = round(ha * (1 - fb / 100), 3)
                r['fallback_pct'] = fb
                if current_price > 0:
                    r['to_target_pct'] = round((current_price - r['sell_target']) / r['sell_target'] * 100, 2)
        return r

    all_stocks    = get_dynamic_stock_list()
    query         = """SELECT code, buy_high_point, buy_drop_pct, buy_break_status, buy_low_after_break,
                        buy_rebound_pct, sell_low_point, sell_rise_pct, sell_break_status,
                        sell_high_after_break, sell_fallback_pct FROM price_targets_v2
                       WHERE buy_high_point IS NOT NULL OR sell_low_point IS NOT NULL"""
    all_configs_raw = c.execute(query).fetchall()

    monitor_items = []
    for row in all_configs_raw:
        d = {
            'code': row[0], 'buy_high_point': row[1], 'buy_drop_pct': row[2],
            'buy_break_status': row[3], 'buy_low_after_break': row[4], 'buy_rebound_pct': row[5] or 0.0,
            'sell_low_point': row[6], 'sell_rise_pct': row[7], 'sell_break_status': row[8],
            'sell_high_after_break': row[9], 'sell_fallback_pct': row[10] or 0.0
        }
        code       = d['code']
        curr_price = get_current_price(code)

        if d['buy_high_point'] and d['buy_drop_pct']:
            bc = calc_buy_target(d, curr_price)
            if d['buy_break_status'] == '已突破' and bc['buy_target']:
                monitor_items.append({'code': code, 'type': '买入', 'trend': '反弹中',
                    'target_price': bc['buy_target'], 'current_price': curr_price,
                    'to_target_pct': bc['to_target_pct'], 'break_status': '已突破'})
            elif d['buy_break_status'] == '未突破':
                monitor_items.append({'code': code, 'type': '买入', 'trend': '等待突破',
                    'target_price': bc['base_price'], 'current_price': curr_price,
                    'to_target_pct': round((bc['base_price'] - curr_price) / curr_price * 100, 2) if curr_price > 0 else None,
                    'break_status': '未突破'})

        if d['sell_low_point'] and d['sell_rise_pct']:
            sc = calc_sell_target(d, curr_price)
            if d['sell_break_status'] == '已突破' and sc['sell_target']:
                monitor_items.append({'code': code, 'type': '卖出', 'trend': '回调中',
                    'target_price': sc['sell_target'], 'current_price': curr_price,
                    'to_target_pct': sc['to_target_pct'], 'break_status': '已突破'})
            elif d['sell_break_status'] == '未突破':
                monitor_items.append({'code': code, 'type': '卖出', 'trend': '等待突破',
                    'target_price': sc['base_price'], 'current_price': curr_price,
                    'to_target_pct': round((sc['base_price'] - curr_price) / curr_price * 100, 2) if curr_price > 0 else None,
                    'break_status': '未突破'})

    # ── 实时监控卡片 ──
    st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:12px">📡 实时监控</div>', unsafe_allow_html=True)

    if monitor_items:
        monitor_items.sort(key=lambda x: abs(x['to_target_pct']) if x['to_target_pct'] is not None else float('inf'))
        cols_per_row = 3
        for i in range(0, len(monitor_items), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, item in enumerate(monitor_items[i:i+cols_per_row]):
                with cols[j]:
                    is_buy    = item['type'] == '买入'
                    acc_color = "#10b981" if is_buy else "#f43f5e"
                    tr_color  = "#3b82f6" if item['trend'] == '等待突破' else acc_color
                    break_icon = "🟢" if item['break_status'] == '已突破' else "⏳"

                    if item['to_target_pct'] is not None:
                        pct_text = (f"还差 {item['to_target_pct']:.2f}%" if item['to_target_pct'] > 0
                                    else f"已超出 {abs(item['to_target_pct']):.2f}%")
                    else:
                        pct_text = "—"

                    type_bg  = "rgba(16,185,129,0.15)"  if is_buy else "rgba(244,63,94,0.15)"
                    type_brd = "rgba(16,185,129,0.35)"  if is_buy else "rgba(244,63,94,0.35)"

                    st.markdown(f"""
                    <div class="monitor-card" style="border-left:3px solid {acc_color};">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                            <span style="font-size:1.1em;font-weight:800;color:#f0f6ff">{item['code']}</span>
                            <span style="background:{type_bg};color:{acc_color};border:1px solid {type_brd};
                                padding:2px 10px;border-radius:20px;font-size:0.76em;font-weight:700">
                                {item['type']}
                            </span>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px">
                            <span style="color:#64748b;font-size:0.80em">趋势</span>
                            <span style="color:{tr_color};font-weight:600;font-size:0.88em">{break_icon} {item['trend']}</span>
                        </div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
                            <div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:8px 10px">
                                <div style="color:#64748b;font-size:0.72em;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px">目标价</div>
                                <div style="color:#f0f6ff;font-size:1.15em;font-weight:700">{item['target_price']:.3f}</div>
                            </div>
                            <div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:8px 10px">
                                <div style="color:#64748b;font-size:0.72em;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px">当前价</div>
                                <div style="color:#94a3b8;font-size:1.0em;font-weight:600">{f"{item['current_price']:.3f}" if item['current_price'] > 0 else "—"}</div>
                            </div>
                        </div>
                        <div style="background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.25);
                            border-radius:6px;padding:7px 12px;text-align:center">
                            <span style="color:#fbbf24;font-size:0.92em;font-weight:700">📊 {pct_text}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
    else:
        st.info("📌 暂无价格目标监控，请在下方配置")

    st.divider()

    # ── 配置区 ──
    with st.expander("⚙️ 配置价格目标", expanded=False):
        sel_stock = st.selectbox("📌 选择股票", [""] + all_stocks, key="pt_stock_select")
        if sel_stock:
            curr_p  = get_current_price(sel_stock)
            ec      = load_price_target_v2(sel_stock) or {
                'buy_high_point': None, 'buy_drop_pct': None, 'buy_break_status': '未突破',
                'buy_low_after_break': None, 'buy_rebound_pct': 0.0,
                'sell_low_point': None, 'sell_rise_pct': None, 'sell_break_status': '未突破',
                'sell_high_after_break': None, 'sell_fallback_pct': 0.0
            }
            st.markdown(
                f'<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;'
                f'padding:10px 14px;margin-bottom:12px;font-size:0.88em">'
                f'<b style="color:var(--accent-teal)">{sel_stock}</b>'
                f'<span style="color:var(--text-muted);margin:0 8px">|</span>'
                f'当前价 <b style="color:var(--text-primary)">{curr_p:.3f}</b></div>',
                unsafe_allow_html=True
            )

            col_buy, col_sell = st.columns(2, gap="large")
            with col_buy:
                st.markdown('<div style="color:var(--accent-green);font-weight:700;font-size:0.90em;margin-bottom:8px">📥 买入体系（高点下跌）</div>', unsafe_allow_html=True)
                with st.container(border=True):
                    buy_high    = st.number_input("前期高点", value=float(ec['buy_high_point']) if ec.get('buy_high_point') else None, step=0.001, format="%.3f", key="buy_high_point")
                    buy_drop    = st.number_input("下跌幅度 (%)", value=float(ec['buy_drop_pct']) if ec.get('buy_drop_pct') else None, step=0.1, format="%.2f", key="buy_drop_pct")
                    buy_break   = st.selectbox("突破状态", ["未突破", "已突破"], index=0 if ec.get('buy_break_status') != '已突破' else 1, key="buy_break_status")
                    buy_low_after = buy_rebound = None
                    if buy_break == "已突破":
                        x1, x2 = st.columns(2)
                        buy_low_after = x1.number_input("突破后最低价", value=float(ec['buy_low_after_break']) if ec.get('buy_low_after_break') else None, step=0.001, format="%.3f", key="buy_low_after_break")
                        buy_rebound   = x2.number_input("反弹幅度 (%)", value=float(ec.get('buy_rebound_pct', 0.0)), step=0.1, format="%.2f", key="buy_rebound_pct_input")

            with col_sell:
                st.markdown('<div style="color:var(--accent-red);font-weight:700;font-size:0.90em;margin-bottom:8px">📤 卖出体系（低点上涨）</div>', unsafe_allow_html=True)
                with st.container(border=True):
                    sell_low    = st.number_input("前期低点", value=float(ec['sell_low_point']) if ec.get('sell_low_point') else None, step=0.001, format="%.3f", key="sell_low_point")
                    sell_rise   = st.number_input("上涨幅度 (%)", value=float(ec['sell_rise_pct']) if ec.get('sell_rise_pct') else None, step=0.1, format="%.2f", key="sell_rise_pct")
                    sell_break  = st.selectbox("突破状态", ["未突破", "已突破"], index=0 if ec.get('sell_break_status') != '已突破' else 1, key="sell_break_status")
                    sell_high_after = sell_fallback = None
                    if sell_break == "已突破":
                        x1, x2 = st.columns(2)
                        sell_high_after = x1.number_input("突破后最高价", value=float(ec['sell_high_after_break']) if ec.get('sell_high_after_break') else None, step=0.001, format="%.3f", key="sell_high_after_break")
                        sell_fallback   = x2.number_input("回落幅度 (%)", value=float(ec.get('sell_fallback_pct', 0.0)), step=0.1, format="%.2f", key="sell_fallback_pct_input")

            cs, cd = st.columns([1, 1])
            with cs:
                if st.button("💾 保存配置", type="primary", use_container_width=True):
                    save_price_target_v2(sel_stock, {
                        'buy_high_point': buy_high, 'buy_drop_pct': buy_drop, 'buy_break_status': buy_break,
                        'buy_low_after_break': buy_low_after, 'buy_rebound_pct': buy_rebound or 0.0,
                        'sell_low_point': sell_low, 'sell_rise_pct': sell_rise, 'sell_break_status': sell_break,
                        'sell_high_after_break': sell_high_after, 'sell_fallback_pct': sell_fallback or 0.0
                    })
                    st.success("✅ 配置已保存")
                    st.rerun()
            with cd:
                if st.button("🗑️ 删除配置", type="secondary", use_container_width=True):
                    delete_price_target_v2(sel_stock)
                    st.warning("⚠️ 已删除")
                    st.rerun()
        else:
            st.info("👆 请选择要配置的股票")

    st.divider()

    # ── 监控参数详情 ──
    st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px">📋 监控参数详情</div>', unsafe_allow_html=True)

    if all_configs_raw:
        detail_data = []
        for row in all_configs_raw:
            d = {
                'code': row[0], 'buy_high_point': row[1], 'buy_drop_pct': row[2],
                'buy_break_status': row[3], 'buy_low_after_break': row[4], 'buy_rebound_pct': row[5] or 0.0,
                'sell_low_point': row[6], 'sell_rise_pct': row[7], 'sell_break_status': row[8],
                'sell_high_after_break': row[9], 'sell_fallback_pct': row[10] or 0.0
            }
            code   = d['code']
            curr_p = get_current_price(code)

            if d['buy_high_point'] and d['buy_drop_pct']:
                buy_base = round(d['buy_high_point'] * (1 - d['buy_drop_pct'] / 100), 3)
                if d['buy_break_status'] == '已突破' and d['buy_low_after_break']:
                    buy_target = round(d['buy_low_after_break'] * (1 + d['buy_rebound_pct'] / 100), 3)
                    to_tgt = round((buy_target - curr_p) / curr_p * 100, 2) if curr_p > 0 else None
                    rebound_d = f"{d['buy_rebound_pct']:.2f}%"
                else:
                    buy_target = '—'
                    to_tgt = round((buy_base - curr_p) / curr_p * 100, 2) if curr_p > 0 else None
                    rebound_d = '—'
                detail_data.append({'股票': code, '体系': '买入', '突破': d['buy_break_status'],
                    '极值': d['buy_high_point'], '幅度': f"{d['buy_drop_pct']:.2f}%", '基准价': buy_base,
                    '突破后极值': d['buy_low_after_break'] or '—', '目标价': buy_target,
                    '当前价': curr_p if curr_p > 0 else '—',
                    '距目标%': f"{to_tgt:.2f}%" if to_tgt is not None else '—',
                    '反弹': rebound_d, '回落': '—'})

            if d['sell_low_point'] and d['sell_rise_pct']:
                sell_base = round(d['sell_low_point'] * (1 + d['sell_rise_pct'] / 100), 3)
                if d['sell_break_status'] == '已突破' and d['sell_high_after_break']:
                    sell_target = round(d['sell_high_after_break'] * (1 - d['sell_fallback_pct'] / 100), 3)
                    to_tgt = round((curr_p - sell_target) / sell_target * 100, 2) if curr_p > 0 else None
                    fallback_d = f"{d['sell_fallback_pct']:.2f}%"
                else:
                    sell_target = '—'
                    to_tgt = round((sell_base - curr_p) / curr_p * 100, 2) if curr_p > 0 else None
                    fallback_d = '—'
                detail_data.append({'股票': code, '体系': '卖出', '突破': d['sell_break_status'],
                    '极值': d['sell_low_point'], '幅度': f"{d['sell_rise_pct']:.2f}%", '基准价': sell_base,
                    '突破后极值': d['sell_high_after_break'] or '—', '目标价': sell_target,
                    '当前价': curr_p if curr_p > 0 else '—',
                    '距目标%': f"{to_tgt:.2f}%" if to_tgt is not None else '—',
                    '反弹': '—', '回落': fallback_d})

        if detail_data:
            html = '<table class="pro-table"><thead><tr><th>股票</th><th>体系</th><th>突破</th><th>极值</th><th>幅度</th><th>基准价</th><th>突破后极值</th><th>目标价</th><th>当前价</th><th>距目标%</th><th>反弹</th><th>回落</th></tr></thead><tbody>'
            for item in detail_data:
                sys_color = "#10b981" if item['体系'] == '买入' else "#f43f5e"
                html += f"""<tr>
                    <td><b>{item['股票']}</b></td>
                    <td><span style="color:{sys_color};font-weight:600">{item['体系']}</span></td>
                    <td>{item['突破']}</td><td>{item['极值']}</td><td>{item['幅度']}</td>
                    <td>{item['基准价']}</td><td>{item['突破后极值']}</td><td>{item['目标价']}</td>
                    <td>{item['当前价']}</td><td>{item['距目标%']}</td>
                    <td>{item['反弹']}</td><td>{item['回落']}</td>
                </tr>"""
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("暂无有效配置")
    else:
        st.info("暂无价格目标配置")

# =====================================================================
#  📝 交易录入
# =====================================================================
elif choice == "📝 交易录入":
    _page_title("📝", "交易录入", "新增 / 管理交易记录")

    full_list  = get_dynamic_stock_list()
    t_code     = st.selectbox("选择股票", options=["【添加新股票】"] + full_list, index=None)

    # 添加新股票时额外要求填写 ticker 代码
    if t_code == "【添加新股票】":
        _nc1, _nc2, _nc3 = st.columns([2, 1.2, 1.8])
        final_code  = _nc1.text_input("新股票名称（必填）", placeholder="例如：腾讯控股")
        _market     = _nc2.selectbox(
            "所属市场",
            options=["A股·沪市", "A股·深市", "港股", "美股"],
            index=0,
        )
        _market_prefix = {
            "A股·沪市": "1.",
            "A股·深市": "0.",
            "港股":     "116.",
            "美股":     "105.",
        }[_market]
        _market_hint = {
            "A股·沪市": "600900",
            "A股·深市": "002594",
            "港股":     "00981",
            "美股":     "TSLA",
        }[_market]
        _raw_code   = _nc3.text_input(
            "股票代码（纯代码，系统自动加前缀）",
            placeholder=f"例如：{_market_hint}",
        )
        # 自动拼接 secid 前缀
        new_ticker_inp = (_market_prefix + _raw_code.strip()) if _raw_code.strip() else ""
    else:
        final_code     = t_code
        new_ticker_inp = None

    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        d      = c1.date_input("交易日期", datetime.now())
        a      = c2.selectbox("操作方向", ["买入", "卖出"])
        p      = c1.number_input("成交单价", value=None, min_value=0.0, step=0.001, format="%.3f")
        q      = c2.number_input("成交数量", value=None, min_value=1, step=1)
        note   = st.text_input("备注（可选）", placeholder="例如：突破20日均线买入、分红除权、止盈卖出……")

        submitted = st.form_submit_button("✅ 保存交易记录", use_container_width=True)
        if submitted:
            if not final_code:
                st.error("❌ 请填写或选择股票")
            elif t_code == "【添加新股票】" and not new_ticker_inp:
                st.error("❌ 添加新股票时请填写股票代码")
            elif p is None or q is None:
                st.error("❌ 请填写单价和数量")
            else:
                c.execute("INSERT INTO trades (date, code, action, price, quantity, note) VALUES (?,?,?,?,?,?)",
                          (d.strftime('%Y-%m-%d'), final_code, a, p, q, note.strip() or None))
                # 若是新股票，同步写入 stock_info（含 ticker 代码）
                if t_code == "【添加新股票】" and new_ticker_inp:
                    try:
                        c.execute(
                            "INSERT OR IGNORE INTO stock_info (stock_name, stock_code) VALUES (?, ?)",
                            (final_code.strip(), new_ticker_inp.strip()),
                        )
                    except Exception:
                        pass
                conn.commit()
                sync_db_to_github()
                st.success(f"✅ 已保存：{final_code} {a} {q}股 @ {p}")
                st.rerun()

# =====================================================================
#  🔔 买卖信号
# =====================================================================
elif choice == "🔔 买卖信号":
    _page_title("🔔", "买卖信号", "策略监控")

    def fmt(num):
        if num is None or (isinstance(num, float) and pd.isna(num)) or num == 0: return "0"
        s = f"{num}"
        return s.rstrip('0').rstrip('.') if '.' in s else s

    with st.expander("➕ 设置 / 更新监控", expanded=False):
        existing_signals = pd.read_sql("SELECT code FROM signals", conn)['code'].tolist()
        s_code  = st.selectbox("监控股票", options=get_dynamic_stock_list(), index=None)
        sig_data = None
        if s_code and s_code in existing_signals:
            sig_data = c.execute(
                "SELECT high_point, low_point, up_threshold, down_threshold, high_date, low_date FROM signals WHERE code = ?",
                (s_code,)
            ).fetchone()

        c1, c2 = st.columns(2)
        s_high  = c1.number_input("高点参考价", value=float(sig_data[0]) if sig_data else None, step=0.0001)
        h_date  = c1.date_input("高点日期",   value=datetime.strptime(sig_data[4], '%Y-%m-%d').date() if sig_data and sig_data[4] else datetime.now())
        s_low   = c2.number_input("低点参考价", value=float(sig_data[1]) if sig_data else None, step=0.0001)
        l_date  = c2.date_input("低点日期",   value=datetime.strptime(sig_data[5], '%Y-%m-%d').date() if sig_data and sig_data[5] else datetime.now())
        s_up    = c1.number_input("上涨触发 (%)", value=float(sig_data[2]) if sig_data else 20.0, step=0.01)
        s_down  = c2.number_input("回调触发 (%)", value=float(sig_data[3]) if sig_data else 20.0, step=0.01)

        if st.button("🚀 启动 / 更新监控", type="primary"):
            if all([s_code, s_high, s_low, s_up, s_down]):
                c.execute("""INSERT OR REPLACE INTO signals
                    (code, high_point, low_point, up_threshold, down_threshold, high_date, low_date)
                    VALUES (?,?,?,?,?,?,?)""",
                    (s_code, s_high, s_low, s_up, s_down,
                     h_date.strftime('%Y-%m-%d'), l_date.strftime('%Y-%m-%d')))
                conn.commit()
                sync_db_to_github()
                st.success("✅ 监控已更新")
                st.rerun()

    sig_df     = pd.read_sql("SELECT * FROM signals", conn)
    prices_map = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}

    if not sig_df.empty:
        html = '<table class="pro-table"><thead><tr><th>代码</th><th>高点</th><th>低点</th><th>距高点</th><th>距低点</th><th>建议操作</th></tr></thead><tbody>'
        for _, r in sig_df.iterrows():
            np_      = prices_map.get(r['code']) or 0.0   # current_price 可能 None/NULL
            _hp      = float(r['high_point'])  if pd.notna(r['high_point'])  else 0.0
            _lp      = float(r['low_point'])   if pd.notna(r['low_point'])   else 0.0
            _up_th   = float(r['up_threshold'])   if pd.notna(r['up_threshold'])   else 0.0
            _down_th = float(r['down_threshold']) if pd.notna(r['down_threshold']) else 0.0
            dr   = ((np_ - _hp) / _hp * 100) if _hp > 0 else 0
            rr   = ((np_ - _lp) / _lp * 100) if _lp > 0 else 0
            if rr >= _up_th:
                badge = '<span class="badge badge-sell">🟢 建议卖出</span>'
            elif dr <= -_down_th:
                badge = '<span class="badge badge-buy">🔴 建议买入</span>'
            else:
                badge = '<span class="badge badge-hold">⚖️ 观望</span>'
            dr_cls = "profit-red" if dr >= 0 else "loss-green"
            rr_cls = "profit-red" if rr >= 0 else "loss-green"
            html += f"""<tr>
                <td><b>{r['code']}</b></td>
                <td>{fmt(r['high_point'])}<br><small style="color:#64748b">{r['high_date']}</small></td>
                <td>{fmt(r['low_point'])}<br><small style="color:#64748b">{r['low_date']}</small></td>
                <td class="{dr_cls}">{dr:.2f}%</td>
                <td class="{rr_cls}">{rr:.2f}%</td>
                <td>{badge}</td>
            </tr>"""
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)

        if st.button("🗑️ 清空所有监控", type="secondary"):
            c.execute("DELETE FROM signals")
            conn.commit()
            sync_db_to_github()
            st.rerun()
    else:
        st.info("📌 当前没有设置任何监控信号")

# =====================================================================
#  📜 历史明细
# =====================================================================
elif choice == "📜 历史明细":
    _page_title("📜", "历史明细", "完整交易流水")

    df_full = pd.read_sql(
        "SELECT id, date, code, action, price, quantity, note FROM trades ORDER BY date DESC, id DESC", conn
    )

    # ══════════════════════════════════════════════════════════
    # 顶部：快速录入新交易
    # ══════════════════════════════════════════════════════════
    with st.expander("➕ 快速录入新交易记录", expanded=False):
        all_stock_list = get_dynamic_stock_list()
        with st.form("quick_trade_form", clear_on_submit=True):
            qc1, qc2, qc3, qc4, qc5, qc6 = st.columns([2, 2, 1.5, 1.5, 1.5, 1.5])
            q_date  = qc1.date_input("📅 交易日期", datetime.now())
            q_code  = qc2.selectbox("🏷️ 股票名称", options=all_stock_list if all_stock_list else [""], index=0)
            q_act   = qc3.selectbox("📌 操作方向", ["买入", "卖出"])
            q_price = qc4.number_input("💰 成交价格", min_value=0.0, step=0.001, format="%.3f")
            q_qty   = qc5.number_input("📦 成交数量", min_value=1, step=1, value=100)
            q_note  = qc6.text_input("📝 备注", placeholder="可选")
            submitted = st.form_submit_button("✅ 提交录入", use_container_width=True, type="primary")
            if submitted:
                if q_price <= 0:
                    st.error("❌ 价格必须大于 0")
                elif not q_code:
                    st.error("❌ 请选择股票名称")
                else:
                    conn.execute(
                        "INSERT INTO trades (date, code, action, price, quantity, note) VALUES (?,?,?,?,?,?)",
                        (q_date.strftime('%Y-%m-%d'), q_code, q_act, q_price, int(q_qty), q_note.strip() or None)
                    )
                    conn.commit()
                    sync_db_to_github()
                    st.success(f"✅ 已录入：{q_code} {q_act} {q_price:.3f} × {int(q_qty)}")
                    st.rerun()

    st.divider()

    if df_full.empty:
        st.info("📌 暂无交易记录")
    else:
        df_full['date'] = pd.to_datetime(df_full['date']).dt.date

        # ── 统计摘要 ──
        total_count = len(df_full)
        buy_count   = len(df_full[df_full['action'] == '买入'])
        sell_count  = len(df_full[df_full['action'] == '卖出'])
        stock_count = df_full['code'].nunique()
        total_buy_amt  = (df_full[df_full['action']=='买入']['price'] * df_full[df_full['action']=='买入']['quantity']).sum()
        total_sell_amt = (df_full[df_full['action']=='卖出']['price'] * df_full[df_full['action']=='卖出']['quantity']).sum()

        _summary_items = [
            ("📋", "总记录数",   str(total_count),              "var(--text-primary)"),
            ("📈", "涉及股票",   str(stock_count),              "var(--text-primary)"),
            ("🔴", "买入笔数",   str(buy_count),                "var(--accent-red, #f43f5e)"),
            ("🟢", "卖出笔数",   str(sell_count),               "var(--accent-green, #10b981)"),
            ("💸", "累计买入额", f"{total_buy_amt:,.0f}",       "var(--accent-red, #f43f5e)"),
            ("💰", "累计卖出额", f"{total_sell_amt:,.0f}",      "var(--accent-green, #10b981)"),
        ]
        _cols = st.columns(6)
        for _col, (_icon, _label, _val, _color) in zip(_cols, _summary_items):
            _col.markdown(
                f"""<div style="background:var(--bg-elevated,#1e2533);border:1px solid var(--border,#2d3748);
                border-radius:10px;padding:10px 8px;text-align:center;min-width:0">
                  <div style="font-size:0.72em;color:var(--text-muted,#94a3b8);white-space:nowrap;
                  overflow:hidden;text-overflow:ellipsis">{_icon} {_label}</div>
                  <div style="font-size:1.05em;font-weight:700;color:{_color};
                  word-break:break-all;line-height:1.3;margin-top:4px">{_val}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        st.divider()

        # ── 搜索与筛选（横向全宽单行）──
        fcol1, fcol2, fcol3, fcol4 = st.columns([3, 2, 2, 2])
        search_code  = fcol1.text_input("🔍 搜索股票", placeholder="输入股票名称关键字", label_visibility="visible")
        act_filter   = fcol2.selectbox("操作类型", ["全部", "买入", "卖出"])
        sort_mode    = fcol3.selectbox("排序方式", ["日期降序（最新）", "日期升序（最早）"])
        date_range   = fcol4.selectbox("时间范围", ["全部", "最近30天", "最近90天", "最近1年"])

        df_display = df_full.copy()
        if search_code:
            df_display = df_display[df_display['code'].str.contains(search_code, case=False, na=False)]
        if act_filter != "全部":
            df_display = df_display[df_display['action'] == act_filter]
        if date_range != "全部":
            import datetime as _dt
            days_map = {"最近30天": 30, "最近90天": 90, "最近1年": 365}
            cutoff = _dt.date.today() - _dt.timedelta(days=days_map[date_range])
            df_display = df_display[df_display['date'] >= cutoff]
        if sort_mode == "日期升序（最早）":
            df_display = df_display.sort_values(['date', 'id'])

        st.markdown(
            f'<div style="font-size:0.82em;color:var(--text-muted);margin-bottom:10px">'
            f'当前显示 <b style="color:var(--text-primary)">{len(df_display)}</b> 条 / 共 {total_count} 条</div>',
            unsafe_allow_html=True
        )

        # ── 全宽交易记录表格 ──
        html = '''<table class="pro-table" style="width:100%;table-layout:fixed">
<colgroup>
  <col style="width:10%"><col style="width:12%"><col style="width:8%">
  <col style="width:10%"><col style="width:9%"><col style="width:12%"><col style="width:39%">
</colgroup>
<thead><tr><th>日期</th><th>股票</th><th>操作</th><th>价格</th><th>数量</th><th>总额</th><th>备注</th></tr></thead><tbody>'''
        for _, r in df_display.iterrows():
            if r['action'] == '买入':
                act_html = '<span class="badge badge-buy">买入</span>'
                row_bg   = "background:rgba(239,68,68,0.04)"
            else:
                act_html = '<span class="badge badge-sell">卖出</span>'
                row_bg   = "background:rgba(34,197,94,0.04)"
            note_raw  = str(r['note']).strip() if pd.notna(r['note']) and str(r['note']).strip() not in ['', 'nan'] else ''
            note_html = note_raw if note_raw else f'<span style="color:var(--text-muted);font-size:0.85em">—</span>'
            amt = r['price'] * r['quantity']
            html += (
                f'<tr style="{row_bg}">'
                f'<td>{r["date"]}</td>'
                f'<td><b style="color:var(--accent-blue)">{r["code"]}</b></td>'
                f'<td>{act_html}</td>'
                f'<td style="font-weight:600">{r["price"]:.3f}</td>'
                f'<td>{int(r["quantity"])}</td>'
                f'<td style="font-weight:600">{amt:,.2f}</td>'
                f'<td style="font-size:0.85em;color:var(--text-secondary);word-break:break-all">{note_html}</td>'
                f'</tr>'
            )
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)

        st.divider()

        st.warning("⚠️ 下方编辑器操作**全部交易记录**（不受搜索影响），请谨慎！")

        with st.expander("🛠️ 数据库维护（支持增、删、改）", expanded=False):
            edited_df = st.data_editor(
                df_full, use_container_width=True, num_rows="dynamic", hide_index=False,
                column_config={
                    "id":       st.column_config.NumberColumn("ID", disabled=True),
                    "date":     st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
                    "code":     st.column_config.TextColumn("代码", required=True),
                    "action":   st.column_config.SelectboxColumn("操作", options=["买入", "卖出"], required=True),
                    "price":    st.column_config.NumberColumn("价格", min_value=0.0, format="%.3f", required=True),
                    "quantity": st.column_config.NumberColumn("数量", min_value=1, step=1, required=True),
                    "note":     st.column_config.TextColumn("备注", width="large"),
                },
                key="trades_editor"
            )
            col_sv, _ = st.columns([1, 4])
            with col_sv:
                if st.button("💾 提交所有修改", type="primary"):
                    try:
                        save_df = edited_df.copy()
                        save_df['date'] = pd.to_datetime(save_df['date']).dt.strftime('%Y-%m-%d')
                        save_df.to_sql('trades', conn, if_exists='replace', index=False)
                        conn.commit()
                        sync_db_to_github()
                        st.success("✅ 交易记录已更新")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 保存失败：{e}")

# =====================================================================
#  📓 复盘日记
# =====================================================================
elif choice == "📓 复盘日记":
    _page_title("📓", "复盘日记", "交易心得归档")

    c.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, stock_name TEXT, content TEXT)""")
    conn.commit()

    with st.expander("✍️ 写新日记", expanded=True):
        stock_options = ["大盘"] + get_dynamic_stock_list()
        ds      = st.selectbox("复盘对象", options=stock_options, index=None, key="new_journal_stock")
        st.caption("🎨 支持 HTML 颜色标签，如 <span style='color:#f59e0b'>重点文字</span>")
        content = st.text_area("心得内容", height=140, key="new_journal_content",
                               placeholder="支持换行、列表、空格等格式……")
        if st.button("📌 存档", type="primary"):
            if ds and content.strip():
                c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                          (datetime.now().strftime('%Y-%m-%d'), ds, content.strip()))
                conn.commit()
                sync_db_to_github()
                st.success("✅ 已存档")
                st.rerun()
            else:
                st.warning("⚠️ 请选择复盘对象并填写内容")

    st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin:10px 0 12px">📚 历史复盘记录</div>', unsafe_allow_html=True)

    journal_df = pd.read_sql(
        "SELECT id, date, stock_name, content FROM journal ORDER BY date DESC, id DESC", conn
    )

    if journal_df.empty:
        st.info("📌 暂无复盘记录")
    else:
        unique_stocks = ["全部"] + sorted(journal_df['stock_name'].unique().tolist())
        filter_stock  = st.selectbox("筛选标的", options=unique_stocks, index=0)
        display_df    = journal_df if filter_stock == "全部" else journal_df[journal_df['stock_name'] == filter_stock]

        if display_df.empty:
            st.info(f"没有与「{filter_stock}」相关的记录")
        else:
            for _, row in display_df.iterrows():
                col1, col2 = st.columns([12, 1])
                with col1:
                    st.markdown(
                        f'<div class="journal-card">'
                        f'<div class="journal-meta">'
                        f'<span style="background:rgba(59,130,246,0.15);color:var(--accent-blue);border-radius:4px;padding:1px 8px;font-weight:600">{row["stock_name"]}</span>'
                        f'<span>{row["date"]}</span>'
                        f'</div>'
                        f'<div class="journal-content">{row["content"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                with col2:
                    if st.button("✕", key=f"del_{row['id']}", help="删除"):
                        if st.session_state.get(f"confirm_{row['id']}", False):
                            c.execute("DELETE FROM journal WHERE id = ?", (row['id'],))
                            conn.commit()
                            sync_db_to_github()
                            st.rerun()
                        else:
                            st.session_state[f"confirm_{row['id']}"] = True
                            st.warning("再点一次确认删除")

            st.caption(f"共 {len(journal_df)} 条 · 当前显示 {len(display_df)} 条")

# =====================================================================
#  📝 交易录入
# =====================================================================
elif choice == "📝 交易录入":
    _page_title("📝", "交易录入", "快速添加交易记录")

    # ── 股票管理区域 ──
    st.markdown('<div style="font-size:0.95em;font-weight:700;color:var(--text-primary);margin-bottom:10px">📋 股票列表管理</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])

    # 添加新股票
    with st.form("add_stock_form", clear_on_submit=True):
        _sm1, _sm2, _sm3 = st.columns([2, 1.2, 1.8])
        new_stock_name  = _sm1.text_input("股票名称", placeholder="例如：腾讯控股")
        _sm_market      = _sm2.selectbox(
            "所属市场",
            options=["A股·沪市", "A股·深市", "港股", "美股"],
            index=0,
        )
        _sm_prefix = {
            "A股·沪市": "1.",
            "A股·深市": "0.",
            "港股":     "116.",
            "美股":     "105.",
        }[_sm_market]
        _sm_hint = {
            "A股·沪市": "600900",
            "A股·深市": "002594",
            "港股":     "00981",
            "美股":     "TSLA",
        }[_sm_market]
        _sm_raw         = _sm3.text_input("股票代码（纯代码）", placeholder=f"例如：{_sm_hint}")
        new_stock_code  = (_sm_prefix + _sm_raw.strip()) if _sm_raw.strip() else ""

        submitted = st.form_submit_button("➕ 添加股票", type="primary", use_container_width=True)

        if submitted and new_stock_name and new_stock_code:
            try:
                c.execute("INSERT INTO stock_info (stock_name, stock_code) VALUES (?, ?)",
                          (new_stock_name.strip(), new_stock_code.strip()))
                conn.commit()
                sync_db_to_github()
                st.success(f"✅ 已添加：{new_stock_name} ({new_stock_code})")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("❌ 该股票名称已存在，请使用其他名称或删除后重新添加")
            except Exception as e:
                st.error(f"❌ 添加失败：{e}")
        elif submitted and new_stock_name and not _sm_raw.strip():
            st.error("❌ 请填写股票代码")

    # 显示股票列表
    st.markdown("---")
    st.markdown('<div style="font-size:0.82em;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:600;margin-bottom:8px">已管理的股票</div>', unsafe_allow_html=True)

    stock_list = pd.read_sql("SELECT id, stock_name, stock_code FROM stock_info ORDER BY stock_name", conn)

    if not stock_list.empty:
        html = '<table class="pro-table"><thead><tr><th>股票名称</th><th>股票代码</th><th>操作</th></tr></thead><tbody>'
        for _, row in stock_list.iterrows():
            html += f"""<tr>
                <td><b>{row['stock_name']}</b></td>
                <td>{row['stock_code']}</td>
                <td>
                    <button type="button" onclick="delete_stock({row['id']})" style="background:#f43f5e;color:white;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:0.85em">删除</button>
                </td>
            </tr>"""
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)

        # JavaScript 删除函数
        components.html("""
        <script>
        function delete_stock(id) {
            if (confirm('确定要删除这只股票吗？此操作不可撤销。')) {
                const deleteEvent = new CustomEvent('delete-stock', { detail: { id: id } });
                window.parent.document.dispatchEvent(deleteEvent);
            }
        }
        </script>
        <script>
        window.parent.document.addEventListener('delete-stock', function(e) {
            const url = new URL(window.location.href);
            url.searchParams.set('delete_stock_id', e.detail.id);
            window.location.href = url.toString();
        });
        </script>
        """, height=0)

        # 检查是否有删除请求
        if 'delete_stock_id' in st.query_params:
            delete_id = st.query_params['delete_stock_id']
            try:
                # 检查该股票是否有交易记录
                stock_name = c.execute("SELECT stock_name FROM stock_info WHERE id = ?", (delete_id,)).fetchone()
                if stock_name:
                    stock_name = stock_name[0]
                    has_trades = c.execute("SELECT COUNT(*) FROM trades WHERE code = ?", (stock_name,)).fetchone()[0]
                    if has_trades > 0:
                        st.warning(f"⚠️ 该股票有 {has_trades} 条交易记录，请先删除交易记录再删除股票")
                    else:
                        c.execute("DELETE FROM stock_info WHERE id = ?", (delete_id,))
                        c.execute("DELETE FROM prices WHERE code = ?", (stock_name,))
                        c.execute("DELETE FROM strategy_notes WHERE code = ?", (stock_name,))
                        c.execute("DELETE FROM price_targets WHERE code = ?", (stock_name,))
                        c.execute("DELETE FROM signals WHERE code = ?", (stock_name,))
                        conn.commit()
                        sync_db_to_github()
                        st.success(f"✅ 已删除：{stock_name}")
                        st.query_params.clear()
                        st.rerun()
            except Exception as e:
                st.error(f"❌ 删除失败：{e}")
    else:
        st.info("📌 暂无股票，请在上方添加")

    st.divider()

    # ── 交易录入区域 ──
    st.markdown('<div style="font-size:0.95em;font-weight:700;color:var(--text-primary);margin-bottom:10px">✏️ 录入交易</div>', unsafe_allow_html=True)

    with st.form("add_trade_form", clear_on_submit=True):
        col_date, col_stock, col_action = st.columns([2, 2, 1])
        trade_date = col_date.date_input("交易日期", value=datetime.now())
        trade_stock = col_stock.selectbox("选择股票", options=sorted(stock_list['stock_name'].tolist()) if not stock_list.empty else [], index=None)
        trade_action = col_action.selectbox("操作", options=["买入", "卖出"])

        col_price, col_qty, col_note = st.columns([2, 2, 3])
        trade_price = col_price.number_input("成交价格", min_value=0.0, step=0.001, format="%.3f")
        trade_qty = col_qty.number_input("数量", min_value=1, step=1)
        trade_note = col_note.text_input("备注（可选）", placeholder="交易说明")

        submitted_trade = st.form_submit_button("📥 录入交易", type="primary", use_container_width=True)

        if submitted_trade:
            if trade_stock and trade_price > 0 and trade_qty > 0:
                try:
                    c.execute(
                        "INSERT INTO trades (date, code, action, price, quantity, note) VALUES (?, ?, ?, ?, ?, ?)",
                        (trade_date.strftime('%Y-%m-%d'), trade_stock, trade_action, trade_price, int(trade_qty), trade_note.strip())
                    )
                    conn.commit()
                    sync_db_to_github()
                    st.success(f"✅ 交易已录入：{trade_stock} {trade_action} {trade_qty}股 @ {trade_price}")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 录入失败：{e}")
            else:
                st.warning("⚠️ 请填写完整的交易信息")

# =====================================================================
#  底部工具栏
# =====================================================================
st.divider()
col_spacer, col_dl = st.columns([8, 1])
with col_dl:
    db_path = pathlib.Path(__file__).with_name("stock_data_v12.db")
    if db_path.exists():
        with open(db_path, "rb") as f:
            st.download_button(
                label="📥 备份 DB",
                data=f,
                file_name="stock_data_v12.db",
                mime="application/x-sqlite3",
                help="下载本地数据库备份"
            )
