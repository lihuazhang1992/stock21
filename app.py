import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import subprocess
import os
import time

# ================== GitHub 自动同步 ==================

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

# ================== 基础配置 ==================

st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(
        pathlib.Path(__file__).with_name("stock_data_v12.db"),
        check_same_thread=False
    )

conn = get_connection()
c = conn.cursor()

# ================== 数据库结构 ==================

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
    buy_base REAL DEFAULT 0.0,
    sell_base REAL DEFAULT 0.0,
    last_updated TEXT
)
""")

conn.commit()

# ================== 工具函数 ==================

def get_dynamic_stock_list():
    try:
        df = pd.read_sql("SELECT DISTINCT code FROM trades", conn)
        base = ["汇丰控股", "中芯国际", "比亚迪"]
        return sorted(list(set(base + df['code'].dropna().tolist())))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

def format_number(num):
    if pd.isna(num) or num is None:
        return "0"
    s = f"{num}"
    return s.rstrip('0').rstrip('.') if '.' in s else s

# ================== 侧边栏 ==================

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

# =====================================================
# ================== 交易录入 =========================
# =====================================================

if choice == "📝 交易录入":
    st.header("📝 交易录入")

    stocks = get_dynamic_stock_list()
    sel = st.selectbox("股票", ["【新增】"] + stocks)
    code = st.text_input("股票名称") if sel == "【新增】" else sel

    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        d = c1.date_input("日期", datetime.now())
        a = c2.selectbox("操作", ["买入", "卖出"])
        p = c1.number_input("价格", min_value=0.0, step=0.001)
        q = c2.number_input("数量", min_value=1, step=1)
        note = st.text_input("备注")
        ok = st.form_submit_button("保存")

        if ok:
            if not code:
                st.error("股票不能为空")
            else:
                c.execute(
                    "INSERT INTO trades (date, code, action, price, quantity, note) VALUES (?,?,?,?,?,?)",
                    (d.strftime("%Y-%m-%d"), code, a, p, q, note)
                )
                conn.commit()
                git_sync_safe("add trade")
                st.success("✅ 已保存并同步到 GitHub")
                st.rerun()

# =====================================================
# ================== 历史明细 =========================
# =====================================================

elif choice == "📜 历史明细":
    st.header("📜 历史交易流水")

    df = pd.read_sql(
        "SELECT id, date, code, action, price, quantity, note FROM trades ORDER BY date DESC, id DESC",
        conn
    )

    if df.empty:
        st.info("暂无交易")
    else:
        df['date'] = pd.to_datetime(df['date']).dt.date
        st.dataframe(df, use_container_width=True)

        st.warning("⚠️ 下方编辑会直接修改数据库")
        edited = st.data_editor(df, num_rows="dynamic", key="editor")

        if st.button("💾 保存所有修改", type="primary"):
            save = edited.copy()
            save['date'] = pd.to_datetime(save['date']).dt.strftime("%Y-%m-%d")
            save.to_sql("trades", conn, if_exists="replace", index=False)
            conn.commit()
            git_sync_safe("edit trades")
            st.success("已保存并同步")
            st.rerun()

# =====================================================
# ================== 复盘日记 =========================
# =====================================================

elif choice == "📓 复盘日记":
    st.header("📓 复盘日记")

    with st.expander("✍️ 写新日记", expanded=True):
        stock = st.selectbox("对象", ["大盘"] + get_dynamic_stock_list())
        content = st.text_area("内容", height=150)
        if st.button("保存日记"):
            if content.strip():
                c.execute(
                    "INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d"), stock, content)
                )
                conn.commit()
                git_sync_safe("add journal")
                st.success("已保存并同步")
                st.rerun()

    df = pd.read_sql("SELECT * FROM journal ORDER BY id DESC", conn)
    for _, r in df.iterrows():
        col1, col2 = st.columns([5,1])
        col1.markdown(f"**{r['date']} · {r['stock_name']}**\n\n{r['content']}")
        if col2.button("🗑️", key=f"d{r['id']}"):
            c.execute("DELETE FROM journal WHERE id=?", (r['id'],))
            conn.commit()
            git_sync_safe("delete journal")
            st.rerun()

# =====================================================
# 其他模块（实时持仓 / 盈利账单 / 信号 / 目标）
# 👉 你原来的代码可原样保留
# 👉 规则只有一句：conn.commit() 后加 git_sync_safe()
# =====================================================
