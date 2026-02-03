from git import Repo
import os, shutil
import pathlib
import streamlit as st
import pandas as pd
import sqlite3
import threading
from datetime import datetime

# ============== 自动备份 GitHub ==============
DB_FILE = pathlib.Path(__file__).with_name("stock_data_v12.db")

try:
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN    = os.getenv("GITHUB_TOKEN")
    REPO_URL = os.getenv("REPO_URL")
except Exception:
    TOKEN    = st.secrets.get("GITHUB_TOKEN", "")
    REPO_URL = st.secrets.get("REPO_URL", "")

def sync_db_to_github():
    if not (TOKEN and REPO_URL):
        return
    try:
        base_dir = pathlib.Path(__file__).parent
        repo_dir = base_dir / ".git_repo"
        db_name = DB_FILE.name
        auth_url = REPO_URL.replace("https://", f"https://x-access-token:{TOKEN}@")

        if repo_dir.exists():
            shutil.rmtree(repo_dir)

        repo = Repo.clone_from(auth_url, repo_dir, depth=1)

        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Streamlit_Bot")
            cw.set_value("user", "email", "bot@example.com")

        shutil.copy2(base_dir / db_name, repo_dir / db_name)

        if repo.is_dirty(untracked_files=True):
            repo.git.add(all=True)
            repo.index.commit(f"Auto-sync {datetime.now().strftime('%m%d-%H%M')}")
            origin = repo.remote(name='origin')
            origin.push(force=True)
            if not os.environ.get("STREAMLIT_CLOUD"):
                st.toast("✅ GitHub 同步成功", icon="📤")
    except Exception as e:
        print(f"GitHub备份错误: {e}")
        if not os.environ.get("STREAMLIT_CLOUD"):
            st.toast(f"⚠️ 备份失败: {e}", icon="⚠️")

# --- 基础配置 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

# 首次启动：尝试从 GitHub 拉取数据库
if not DB_FILE.exists():
    try:
        repo_dir = pathlib.Path(__file__).with_name(".git_repo")
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        auth_url = REPO_URL.replace("https://", f"https://x-access-token:{TOKEN}@")
        Repo.clone_from(auth_url, repo_dir, depth=1)
        remote_db = repo_dir / DB_FILE.name
        if remote_db.exists():
            shutil.copy2(remote_db, DB_FILE)
            st.toast("✅ 已从 GitHub 加载数据库", icon="📥")
        else:
            st.toast("🆕 GitHub 无数据库，将创建新库", icon="✨")
    except Exception as e:
        st.error(f"❌ 无法从 GitHub 加载数据库: {e}")
        # 不 stop，让它继续创建新库

conn = get_connection()
c = conn.cursor()

# 创建/升级表结构
c.execute('''CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    code TEXT,
    action TEXT,
    price REAL,
    quantity INTEGER,
    note TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS prices (
    code TEXT PRIMARY KEY,
    current_price REAL,
    manual_cost REAL
)''')
c.execute('''CREATE TABLE IF NOT EXISTS signals (
    code TEXT PRIMARY KEY,
    high_point REAL,
    low_point REAL,
    up_threshold REAL,
    down_threshold REAL,
    high_date TEXT,
    low_date TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    stock_name TEXT,
    content TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS price_targets (
    code TEXT PRIMARY KEY,
    base_price REAL DEFAULT 0.0,
    prior_high REAL DEFAULT 0.0,
    prior_low REAL DEFAULT 0.0,
    break_low REAL DEFAULT 0.0,
    break_high REAL DEFAULT 0.0,
    trend TEXT,
    last_updated TEXT
)''')

# 兼容旧表结构
for col in ["manual_cost"]:
    try: c.execute("ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0"); conn.commit()
    except: pass
for col in ["note"]:
    try: c.execute("ALTER TABLE trades ADD COLUMN note TEXT"); conn.commit()
    except: pass

thread = threading.Thread(target=sync_db_to_github, daemon=True)
thread.start()

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(set(["汇丰控股", "中芯国际", "比亚迪"] + t_stocks))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# CSS
st.markdown("""
<style>
.custom-table { width:100%; border-collapse:collapse; margin:10px 0; font-size:15px; border-radius:8px; overflow:hidden; box-shadow:0 0 10px rgba(0,0,0,0.05); }
.custom-table thead tr { background:#009879; color:#fff; text-align:center; font-weight:bold; }
.custom-table th, .custom-table td { padding:12px 15px; text-align:center; border-bottom:1px solid #ddd; }
.custom-table tbody tr:nth-of-type(even) { background:#f8f8f8; }
.profit-red  { color:#d32f2f; font-weight:bold; }
.loss-green  { color:#388e3c; font-weight:bold; }
</style>
""", unsafe_allow_html=True)

# 侧边栏
menu = ["📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# ────────────────────────────────────────────────
#                价格目标管理（已修改）
# ────────────────────────────────────────────────
if choice == "🎯 价格目标管理":

    def ensure_columns():
        for col in ["prior_high", "prior_low", "break_low", "break_high", "trend"]:
            try:
                c.execute(f"ALTER TABLE price_targets ADD COLUMN {col} {'REAL DEFAULT 0.0' if col != 'trend' else 'TEXT'}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

    ensure_columns()

    targets_raw = c.execute("""
        SELECT code, base_price, prior_high, prior_low, break_low, break_high, trend 
        FROM price_targets
    """).fetchall()

    targets_dict = {
        r[0]: {
            "base_price": r[1] or 0.0,
            "prior_high": r[2] or 0.0,
            "prior_low": r[3] or 0.0,
            "break_low": r[4] or 0.0,
            "break_high": r[5] or 0.0,
            "trend": r[6] or ""
        } for r in targets_raw
    }

    current_prices = {
        row[0]: row[1] or 0.0
        for row in c.execute("SELECT code, current_price FROM prices").fetchall()
    }

    all_stocks = get_dynamic_stock_list()

    c1, c2 = st.columns([5, 1])
    c1.markdown("## 🎯 价格目标管理")
    with c2.expander("➕ 新增/编辑", expanded=False):
        selected = st.selectbox("股票", [""] + all_stocks, key="target_select_new")
        if selected:
            exist = targets_dict.get(selected, {"base_price":0,"prior_high":0,"prior_low":0,"break_low":0,"break_high":0,"trend":""})
            curr_p = current_prices.get(selected, 0.0)

            st.caption(f"现价：**{curr_p:.3f}**" if curr_p > 0 else "暂无现价")

            base_price   = st.number_input("基准价",       value=float(exist["base_price"]),   step=0.001, format="%.3f")
            prior_high   = st.number_input("前期最高价",   value=float(exist["prior_high"]),   step=0.001, format="%.3f")
            prior_low    = st.number_input("前期最低价",   value=float(exist["prior_low"]),    step=0.001, format="%.3f")
            break_low    = st.number_input("突破后最低价", value=float(exist["break_low"]),    step=0.001, format="%.3f")
            break_high   = st.number_input("突破后最高价", value=float(exist["break_high"]),   step=0.001, format="%.3f")

            trend_options = ["待设置", "突破基数", "突破反弹", "突破回落"]
            trend_idx = trend_options.index(exist["trend"]) if exist["trend"] in trend_options else 0
            trend_sel = st.selectbox("当前趋势", trend_options, index=trend_idx)

            if st.button("保存", type="primary"):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                c.execute("""
                    INSERT OR REPLACE INTO price_targets
                    (code, base_price, prior_high, prior_low, break_low, break_high, trend, last_updated)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (selected, base_price, prior_high, prior_low, break_low, break_high, trend_sel, now_str))
                conn.commit()
                threading.Thread(target=sync_db_to_github, daemon=True).start()
                st.success("已保存")
                st.rerun()

    st.subheader("监控列表")

    rows = []

    for stock in all_stocks:
        curr = current_prices.get(stock, 0.0)
        if curr <= 0: continue

        t = targets_dict.get(stock, {})
        base   = t.get("base_price", 0.0)
        p_high = t.get("prior_high", 0.0)
        p_low  = t.get("prior_low",  0.0)
        b_low  = t.get("break_low",  0.0)
        b_high = t.get("break_high", 0.0)
        trend  = t.get("trend",      "待设置")

        if base <= 0: continue

        is_breakout = curr > base   # 简单判断是否已突破（可根据需要改为更复杂的逻辑）

        if not is_breakout:
            # 未突破
            dist_pct = abs((curr - base) / base * 100) if base > 0 else 0
            dir_str = "上涨" if curr < base else "下跌"
            rows.append([stock, "待突破", base, curr, dist_pct, trend, 0.0, f"距基准 {dir_str}"])
        else:
            # 已突破
            if p_high <= p_low or p_low <= 0 or p_high <= 0:
                rows.append([stock, "已突破（数据不足）", base, curr, 0.0, trend, 0.0, "无法计算"])
                continue

            fib_rebound  = (p_high - p_low) / p_low  * 0.382
            fib_fallback = (p_high - p_low) / p_high * 0.618

            if trend == "突破反弹" and b_low > 0:
                target = b_low * (1 + fib_rebound)
                pct = abs((curr - target) / target * 100) if target > 0 else 0
                rows.append([stock, "买入目标", target, curr, pct, trend, fib_rebound*100, "反弹38.2%"])

            elif trend == "突破回落" and b_high > 0:
                target = b_high * (1 - fib_fallback)
                pct = abs((curr - target) / target * 100) if target > 0 else 0
                rows.append([stock, "卖出目标", target, curr, pct, trend, fib_fallback*100, "回落61.8%"])

            else:
                rows.append([stock, "已突破", base, curr, 0.0, trend, 0.0, "待确认方向"])

    if not rows:
        st.info("暂无任何价格目标设置")
    else:
        # 排序：待突破的优先（距离近的排前），然后是已突破的
        pending = [r for r in rows if r[1] == "待突破"]
        others  = [r for r in rows if r[1] != "待突破"]

        pending.sort(key=lambda x: x[4])
        others.sort(key=lambda x: x[4])

        display_rows = pending + others

        cols = st.columns(2)
        for i, row in enumerate(display_rows):
            stock, status, target, curr, pct, trend, prop, prop_type = row

            if "待突破" in status:
                color = "#FF9800"   # 橙色
            elif "买入" in status:
                color = "#4CAF50"   # 绿色
            elif "卖出" in status:
                color = "#F44336"   # 红色
            else:
                color = "#9E9E9E"   # 灰色

            with cols[i % 2]:
                st.markdown(f"""
                <div style="background:#fff; border-left:5px solid {color}; border-radius:6px; padding:12px; margin-bottom:8px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                        <span style="font-size:1.15em; font-weight:600;">{stock}</span>
                        <span style="background:{color}; color:white; padding:3px 10px; border-radius:4px; font-size:0.85em;">{status}</span>
                    </div>
                    <div style="color:#555; font-size:0.9em; margin:4px 0;">
                        趋势：{trend}
                    </div>
                    <div style="font-size:0.95em; color:#222;">
                        关键价位 <strong>{target:.3f}</strong>　　现价 {curr:.3f}
                    </div>
                    <div style="font-size:0.9em; color:#666; margin-top:4px;">
                        {prop_type}：{prop:.2f}%　　还差 <strong>{pct:.2f}%</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

# 以下为其他功能（保持不变，省略以节省篇幅）
# 如果需要完整包含其他部分，请告诉我，我可以继续补全
# ────────────────────────────────────────────────

# 交易录入、历史明细、复盘日记 等其他功能代码保持原样
# 这里只展示了修改后的「价格目标管理」部分

st.markdown("---")
st.caption("股票管理系统 v22.1 | 数据自动备份至 GitHub")
