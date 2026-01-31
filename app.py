import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import subprocess
import os
import time

# ======================================================
# GitHub 自动同步（新增，不影响 UI）
# ======================================================

_last_git_sync = 0

def git_sync_safe(commit_msg="auto update db"):
    global _last_git_sync
    if time.time() - _last_git_sync < 8:
        return
    try:
        repo_dir = pathlib.Path(__file__).parent
        os.chdir(repo_dir)

        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]

        subprocess.run(
            ["git", "remote", "set-url", "origin",
             f"https://{token}@github.com/{repo}.git"],
            check=False
        )

        subprocess.run(["git", "add", "stock_data_v12.db"], check=True)
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=False
        )
        subprocess.run(["git", "push"], check=True)

        _last_git_sync = time.time()
    except Exception as e:
        st.warning(f"⚠️ GitHub 同步失败：{e}")

# ======================================================
# 基础配置 & 数据库
# ======================================================

st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(
        pathlib.Path(__file__).with_name("stock_data_v12.db"),
        check_same_thread=False
    )

conn = get_connection()
c = conn.cursor()

# ======================================================
# 数据表
# ======================================================

c.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    code TEXT,
    action TEXT,
    price REAL,
    quantity INTEGER,
    note TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS prices (
    code TEXT PRIMARY KEY,
    current_price REAL,
    manual_cost REAL
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS signals (
    code TEXT PRIMARY KEY,
    high_point REAL,
    low_point REAL,
    up_threshold REAL,
    down_threshold REAL,
    high_date TEXT,
    low_date TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    stock_name TEXT,
    content TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS price_targets (
    code TEXT PRIMARY KEY,
    base_price REAL DEFAULT 0.0,
    buy_target REAL DEFAULT 0.0,
    sell_target REAL DEFAULT 0.0,
    last_updated TEXT
)
""")

conn.commit()

# ======================================================
# 工具函数
# ======================================================

def get_dynamic_stock_list():
    try:
        t = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + [x for x in t if x])))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

def format_number(num):
    if pd.isna(num) or num is None:
        return "0"
    s = f"{num}"
    return s.rstrip('0').rstrip('.') if '.' in s else s

# ======================================================
# CSS
# ======================================================

st.markdown("""
<style>
.custom-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 15px;
}
.custom-table thead tr {
    background-color: #009879;
    color: white;
    text-align: center;
}
.custom-table th, .custom-table td {
    padding: 10px;
    text-align: center;
}
.custom-table tbody tr:nth-of-type(even) {
    background-color: #f8f8f8;
}
.profit-red { color: #d32f2f; font-weight: bold; }
.loss-green { color: #388e3c; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ======================================================
# 侧边栏
# ======================================================

menu = [
    "📊 实时持仓",
    "💰 盈利账单",
    "🎯 价格目标管理",
    "📝 交易录入",
    "🔔 买卖信号",
    "📜 历史明细",
    "📓 复盘日记"
]

choice = st.sidebar.radio("功能导航", menu)

# ======================================================
# 📊 实时持仓（完整原逻辑）
# ======================================================

if choice == "📊 实时持仓":
    st.header("📊 持仓盈亏分析")

    df_trades = pd.read_sql(
        "SELECT * FROM trades ORDER BY date ASC, id ASC", conn
    )

    if df_trades.empty:
        st.info("📌 暂无交易记录")
    else:
        stocks = df_trades['code'].unique()

        with st.expander("🛠️ 维护现价与手动成本", expanded=True):
            raw = c.execute(
                "SELECT code, current_price, manual_cost FROM prices"
            ).fetchall()
            config = {r[0]: (r[1] or 0.0, r[2] or 0.0) for r in raw}

            for s in stocks:
                col1, col2 = st.columns(2)
                old_p, old_c = config.get(s, (0.0, 0.0))
                p = col1.number_input(f"{s} 现价", value=float(old_p), step=0.0001)
                cst = col2.number_input(f"{s} 手动成本", value=float(old_c), step=0.0001)

                if p != old_p or cst != old_c:
                    c.execute(
                        "INSERT OR REPLACE INTO prices (code,current_price,manual_cost) VALUES (?,?,?)",
                        (s, p, cst)
                    )
                    conn.commit()
                    git_sync_safe(f"update price {s}")

        price_map = {
            r[0]: (r[1] or 0.0, r[2] or 0.0)
            for r in c.execute("SELECT code,current_price,manual_cost FROM prices")
        }

        summary = []

        for s in stocks:
            sdf = df_trades[df_trades['code'] == s]
            now_p, manual_cost = price_map.get(s, (0.0, 0.0))

            buy_q = sdf[sdf['action'] == '买入']['quantity'].sum()
            sell_q = sdf[sdf['action'] == '卖出']['quantity'].sum()
            net_q = buy_q - sell_q

            if net_q != 0 and manual_cost > 0:
                if net_q > 0:
                    rate = (now_p - manual_cost) / manual_cost * 100
                else:
                    rate = (manual_cost - now_p) / manual_cost * 100
                summary.append([s, net_q, manual_cost, now_p, rate])

        if summary:
            html = '<table class="custom-table"><thead><tr><th>股票</th><th>净持仓</th><th>成本</th><th>现价</th><th>盈亏%</th></tr></thead><tbody>'
            for r in sorted(summary, key=lambda x: x[4], reverse=True):
                cls = "profit-red" if r[4] > 0 else "loss-green"
                html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{format_number(r[2])}</td><td>{format_number(r[3])}</td><td class='{cls}'>{r[4]:.2f}%</td></tr>"
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)
# ======================================================
# 💰 盈利账单
# ======================================================

if choice == "💰 盈利账单":
    st.header("💰 交易盈亏分析")
    
    df_trades = pd.read_sql(
        "SELECT * FROM trades ORDER BY date ASC, id ASC", conn
    )

    if df_trades.empty:
        st.info("📌 暂无交易记录")
    else:
        summary = []
        for s in df_trades['code'].unique():
            sdf = df_trades[df_trades['code'] == s]
            price_map = {r[0]: (r[1] or 0.0, r[2] or 0.0)
                         for r in c.execute("SELECT code, current_price, manual_cost FROM prices")}
            now_p, manual_cost = price_map.get(s, (0.0, 0.0))

            buy_q = sdf[sdf['action'] == '买入']['quantity'].sum()
            sell_q = sdf[sdf['action'] == '卖出']['quantity'].sum()
            net_q = buy_q - sell_q

            if net_q != 0 and manual_cost > 0:
                if net_q > 0:
                    rate = (now_p - manual_cost) / manual_cost * 100
                else:
                    rate = (manual_cost - now_p) / manual_cost * 100

                summary.append([s, net_q, manual_cost, now_p, rate])

        if summary:
            html = '<table class="custom-table"><thead><tr><th>股票</th><th>净持仓</th><th>成本</th><th>现价</th><th>盈亏%</th></tr></thead><tbody>'
            for r in sorted(summary, key=lambda x: x[4], reverse=True):
                cls = "profit-red" if r[4] > 0 else "loss-green"
                html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{format_number(r[2])}</td><td>{format_number(r[3])}</td><td class='{cls}'>{r[4]:.2f}%</td></tr>"
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)

# ======================================================
# 🎯 价格目标管理
# ======================================================

if choice == "🎯 价格目标管理":
    st.header("🎯 股票价格目标设置")

    stock_list = get_dynamic_stock_list()
    with st.form("set_price_target"):
        stock = st.selectbox("选择股票", stock_list)
        buy_target = st.number_input("设定买入目标", min_value=0.0, step=0.01)
        sell_target = st.number_input("设定卖出目标", min_value=0.0, step=0.01)
        submit_button = st.form_submit_button(label="保存目标")

        if submit_button:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                INSERT OR REPLACE INTO price_targets
                (code, buy_target, sell_target, last_updated)
                VALUES (?, ?, ?, ?)
            """, (stock, buy_target, sell_target, current_time))
            conn.commit()
            git_sync_safe(f"set price targets for {stock}")
            st.success(f"已保存 {stock} 的价格目标")

# ======================================================
# 📝 交易录入
# ======================================================

if choice == "📝 交易录入":
    st.header("📝 新增交易记录")

    with st.form("trade_form"):
        stock_code = st.text_input("股票代码")
        action = st.selectbox("操作类型", ["买入", "卖出"])
        price = st.number_input("交易价格", min_value=0.0, step=0.01)
        quantity = st.number_input("数量", min_value=1, step=1)
        note = st.text_area("备注")
        submit_button = st.form_submit_button(label="提交")

        if submit_button:
            if not stock_code or price <= 0 or quantity <= 0:
                st.warning("⚠️ 请填写完整有效的信息")
            else:
                trade_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("""
                    INSERT INTO trades (date, code, action, price, quantity, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (trade_date, stock_code, action, price, quantity, note))
                conn.commit()
                git_sync_safe(f"new trade: {action} {quantity} shares of {stock_code} at {price}")
                st.success(f"交易记录已提交: {action} {quantity} shares of {stock_code} at {price}")

# ======================================================
# 🔔 买卖信号
# ======================================================

if choice == "🔔 买卖信号":
    st.header("🔔 股票买卖信号")
    st.markdown("""
    这个模块用于设置与追踪买入/卖出信号，如价格突破某个阈值。
    """)

    stock_list = get_dynamic_stock_list()

    with st.form("set_signals"):
        stock = st.selectbox("选择股票", stock_list)
        high_threshold = st.number_input("设定卖出阈值", min_value=0.0, step=0.01)
        low_threshold = st.number_input("设定买入阈值", min_value=0.0, step=0.01)
        submit_button = st.form_submit_button(label="保存信号")

        if submit_button:
            c.execute("""
                INSERT OR REPLACE INTO signals
                (code, up_threshold, down_threshold, high_point, low_point, high_date, low_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (stock, high_threshold, low_threshold, 0.0, 0.0, "", ""))
            conn.commit()
            git_sync_safe(f"set signal for {stock}")
            st.success(f"已保存 {stock} 的买卖信号")

# ======================================================
# 📜 历史明细
# ======================================================

if choice == "📜 历史明细":
    st.header("📜 交易历史明细")

    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC", conn)

    if df_trades.empty:
        st.info("📌 暂无历史记录")
    else:
        st.dataframe(df_trades)

# ======================================================
# 📓 复盘日记
# ======================================================

if choice == "📓 复盘日记":
    st.header("📓 我的复盘日记")

    with st.form("journal_form"):
        journal_date = st.date_input("日期", datetime.now())
        journal_content = st.text_area("复盘内容")
        submit_button = st.form_submit_button(label="提交日记")

        if submit_button:
            if journal_content:
                c.execute("""
                    INSERT INTO journal (date, stock_name, content)
                    VALUES (?, ?, ?)
                """, (journal_date.strftime("%Y-%m-%d"), "复盘", journal_content))
                conn.commit()
                git_sync_safe("new journal entry")
                st.success("日记已提交")

# ======================================================
# 📥 下载数据库
# ======================================================

if st.sidebar.button("📥 下载数据库"):
    db_path = pathlib.Path(__file__).with_name("stock_data_v12.db")
    st.download_button(
        label="下载数据库",
        data=db_path.read_bytes(),
        file_name="stock_data_v12.db",
        mime="application/octet-stream"
    )
