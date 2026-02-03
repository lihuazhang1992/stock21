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
        else:
            print("数据无变动，无需同步")

    except Exception as e:
        print(f"GitHub备份错误: {e}")
        if not os.environ.get("STREAMLIT_CLOUD"):
            st.toast(f"⚠️ 备份失败: {e}", icon="⚠️")

# --- 基础配置 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

# 启动时从 GitHub 拉取数据库
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
        st.stop()

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
    buy_base REAL DEFAULT 0.0,
    buy_rebound_pct REAL DEFAULT 0.0,
    buy_low_point REAL DEFAULT 0.0,
    buy_status TEXT DEFAULT '未设置',
    sell_base REAL DEFAULT 0.0,
    sell_pullback_pct REAL DEFAULT 0.0,
    sell_high_point REAL DEFAULT 0.0,
    sell_status TEXT DEFAULT '未设置',
    last_updated TEXT
)''')

# 兼容旧表结构，添加缺失列
for table, col, col_type in [
    ("prices", "manual_cost", "REAL DEFAULT 0.0"),
    ("trades", "note", "TEXT"),
    ("price_targets", "buy_rebound_pct", "REAL DEFAULT 0.0"),
    ("price_targets", "buy_low_point", "REAL DEFAULT 0.0"),
    ("price_targets", "buy_status", "TEXT DEFAULT '未设置'"),
    ("price_targets", "sell_pullback_pct", "REAL DEFAULT 0.0"),
    ("price_targets", "sell_high_point", "REAL DEFAULT 0.0"),
    ("price_targets", "sell_status", "TEXT DEFAULT '未设置'")
]:
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError:
        pass

conn.commit()
threading.Thread(target=sync_db_to_github, daemon=True).start()

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(set(["汇丰控股", "中芯国际", "比亚迪"] + t_stocks))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# CSS 样式
st.markdown("""
    <style>
    .custom-table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 15px; border-radius: 8px; overflow: hidden; box-shadow: 0 0 10px rgba(0,0,0,0.05); }
    .custom-table thead tr { background-color: #009879; color: #ffffff; text-align: center; font-weight: bold; }
    .custom-table th, .custom-table td { padding: 12px 15px; text-align: center; border-bottom: 1px solid #dddddd; }
    .custom-table tbody tr:nth-of-type(even) { background-color: #f8f8f8; }
    .profit-red { color: #d32f2f; font-weight: bold; }
    .loss-green { color: #388e3c; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# 侧边栏导航
menu = ["📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# ──────────────────────────────────────────────
#               价格目标管理（已修复）
# ──────────────────────────────────────────────
if choice == "🎯 价格目标管理":
    targets_raw = c.execute("""
        SELECT code, buy_base, buy_rebound_pct, buy_low_point, buy_status,
               sell_base, sell_pullback_pct, sell_high_point, sell_status
        FROM price_targets
    """).fetchall()
    
    targets_dict = {r[0]: {
        "buy_base": r[1] or 0.0,
        "buy_rebound_pct": r[2] or 0.0,
        "buy_low_point": r[3] or 0.0,
        "buy_status": r[4] or "未设置",
        "sell_base": r[5] or 0.0,
        "sell_pullback_pct": r[6] or 0.0,
        "sell_high_point": r[7] or 0.0,
        "sell_status": r[8] or "未设置"
    } for r in targets_raw}

    current_prices = {row[0]: row[1] or 0.0 for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    all_stocks = get_dynamic_stock_list()

    c1, c2 = st.columns([4, 1])
    c1.markdown("## 🎯 价格目标管理")
    
    with c2.expander("➕ 新增/编辑", expanded=False):
        selected_stock = st.selectbox("股票", [""] + all_stocks, key="target_stock_select_new")
        if selected_stock:
            curr = current_prices.get(selected_stock, 0.0)
            st.caption(f"现价 **{curr:.3f}**" if curr > 0 else "暂无现价")
            exist = targets_dict.get(selected_stock, {
                "buy_base": 0.0, "buy_rebound_pct": 0.0, "buy_low_point": 0.0, "buy_status": "未设置",
                "sell_base": 0.0, "sell_pullback_pct": 0.0, "sell_high_point": 0.0, "sell_status": "未设置"
            })
            
            st.subheader("买入设置（跌破后反弹买入）")
            buy_base = st.number_input("买入基准价", value=exist["buy_base"], step=0.001, format="%.3f")
            buy_rebound_pct = st.number_input("反弹百分比 (%)", value=exist["buy_rebound_pct"], step=0.01, min_value=0.0)
            buy_low_point = st.number_input("当前最低价（手动更新）", value=exist["buy_low_point"], step=0.001, format="%.3f")
            buy_status = st.selectbox("买入阶段", ["未设置", "正在跌破", "跌破后反弹中"], index=["未设置", "正在跌破", "跌破后反弹中"].index(exist["buy_status"]))
            
            st.subheader("卖出设置（突破后回调卖出）")
            sell_base = st.number_input("卖出基准价", value=exist["sell_base"], step=0.001, format="%.3f")
            sell_pullback_pct = st.number_input("回调百分比 (%)", value=exist["sell_pullback_pct"], step=0.01, min_value=0.0)
            sell_high_point = st.number_input("当前最高价（手动更新）", value=exist["sell_high_point"], step=0.001, format="%.3f")
            sell_status = st.selectbox("卖出阶段", ["未设置", "正在突破", "突破后回调中"], index=["未设置", "正在突破", "突破后回调中"].index(exist["sell_status"]))
            
            if st.button("保存设置", type="primary"):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                c.execute("""
                    INSERT OR REPLACE INTO price_targets
                    (code, buy_base, buy_rebound_pct, buy_low_point, buy_status,
                     sell_base, sell_pullback_pct, sell_high_point, sell_status, last_updated)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (selected_stock, buy_base, buy_rebound_pct, buy_low_point, buy_status,
                      sell_base, sell_pullback_pct, sell_high_point, sell_status, now_str))
                conn.commit()
                threading.Thread(target=sync_db_to_github, daemon=True).start()
                st.success("设置已保存")
                st.rerun()

    st.subheader("当前监控卡片")

    rows = []
    for stock in all_stocks:
        curr = current_prices.get(stock, 0.0)
        if curr <= 0:
            continue
        t = targets_dict.get(stock, {
            "buy_base": 0.0, "buy_rebound_pct": 0.0, "buy_low_point": 0.0, "buy_status": "未设置",
            "sell_base": 0.0, "sell_pullback_pct": 0.0, "sell_high_point": 0.0, "sell_status": "未设置"
        })
        
        # 买入目标
        if t["buy_base"] > 0 and t["buy_status"] != "未设置":
            if t["buy_status"] == "跌破后反弹中" and t["buy_low_point"] > 0:
                target = t["buy_low_point"] * (1 + t["buy_rebound_pct"] / 100)
                diff_pct = (curr - target) / target * 100
                label = f"还差 {abs(diff_pct):.2f}%" if curr < target else f"已超 {abs(diff_pct):.2f}%"
                rows.append([stock, "买入", t["buy_base"], curr, target, abs(diff_pct), label, t["buy_status"], t["buy_low_point"] or 0])
            else:
                rows.append([stock, "买入", t["buy_base"], curr, 0, 9999, "等待最低价更新" if t["buy_status"] == "正在跌破" else "未激活", t["buy_status"], 0])
        
        # 卖出目标
        if t["sell_base"] > 0 and t["sell_status"] != "未设置":
            if t["sell_status"] == "突破后回调中" and t["sell_high_point"] > 0:
                target = t["sell_high_point"] * (1 - t["sell_pullback_pct"] / 100)
                diff_pct = (target - curr) / target * 100
                label = f"还差 {abs(diff_pct):.2f}%" if curr > target else f"已超 {abs(diff_pct):.2f}%"
                rows.append([stock, "卖出", t["sell_base"], curr, target, abs(diff_pct), label, t["sell_status"], t["sell_high_point"] or 0])
            else:
                rows.append([stock, "卖出", t["sell_base"], curr, 0, 9999, "等待最高价更新" if t["sell_status"] == "正在突破" else "未激活", t["sell_status"], 0])

    if rows:
        rows.sort(key=lambda x: x[5])  # 按距离排序
        cols = st.columns(2)
        for idx, r in enumerate(rows):
            stock, direction, base, curr, target, pct, label, status, point = r
            color = "#4CAF50" if direction == "买入" else "#F44336"
            point_label = "最低价" if direction == "买入" else "最高价"
            
            # 安全显示
            point_display = f"{point:.3f}" if point > 0 else "未设置"
            target_display = f"{target:.3f}" if target > 0 else "未计算"
            
            with cols[idx % 2]:
                st.markdown(f"""
                <div style="background:#ffffff; border-left:5px solid {color}; border-radius:8px; 
                            padding:12px 14px; margin-bottom:8px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
                    <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
                        <span style="font-size:1.15em; font-weight:700;">{stock}</span>
                        <span style="background:{color}; color:white; border-radius:6px; padding:3px 8px; font-size:0.9em;">
                            {direction}
                        </span>
                    </div>
                    <div style="font-size:0.85em; color:#555; line-height:1.5;">
                        基准价　{base:.3f}　｜　现价　{curr:.3f}
                    </div>
                    <div style="font-size:0.85em; color:#555; line-height:1.5;">
                        {point_label}　{point_display}　｜　状态　{status}
                    </div>
                    <div style="margin-top:10px; font-size:1.22em; font-weight:700; color:{color};">
                        目标价　{target_display}　　{label}
                    </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("暂无任何价格目标设置")

# 以下是其他功能部分（保持原样，未做改动）
# ...（省略其他菜单的代码，如实时持仓、盈利账单、交易录入等）

# 如果你需要完整包含所有功能的代码，请告诉我，我可以继续把后面部分也贴上来。
# 但通常价格目标这块修复后，整个程序应该就能正常运行了。

# ──────────────────────────────────────────────
#               页面底部下载按钮
# ──────────────────────────────────────────────
col1, col2, col3 = st.columns([5, 1, 1])
with col3:
    if DB_FILE.exists():
        with open(DB_FILE, "rb") as f:
            st.download_button(
                label="📥 下载数据库",
                data=f,
                file_name="stock_data_v12.db",
                mime="application/x-sqlite3"
            )
