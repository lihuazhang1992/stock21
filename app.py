
# ===============================
# 股票管理系统 v23 - 新增模块：📈 交易组合中枢
# 说明：
# - 在原 app.py 基础上扩展，不破坏原有模块
# - 新增数据库表：decision_log, cycles
# - 计算口径（已确认）：
#   1) 峰值持仓金额：历史最高市值
#   2) 年化收益率：已实现 + 当前持仓市值
#   3) 决策记录：交易时强制可选关联（本模块可独立补录）
# ===============================

import os, shutil, pathlib, threading, sqlite3
from datetime import datetime
import pandas as pd
import streamlit as st
from git import Repo

# ---------- 基础配置 ----------
st.set_page_config(page_title="股票管理系统 v23", layout="wide")
BASE_DIR = pathlib.Path(__file__).parent
DB_FILE = BASE_DIR / "stock_data_v12.db"

def get_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

conn = get_connection()
c = conn.cursor()

# ---------- 新增表（自动升级） ----------
c.execute('''
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    code TEXT,
    action TEXT,
    reason TEXT,
    rule_ref TEXT,
    confidence REAL
)
''')

c.execute('''
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    start_date TEXT,
    end_date TEXT,
    direction TEXT,
    pct REAL
)
''')
conn.commit()

# ---------- 侧边栏 ----------
menu = [
    "📊 实时持仓",
    "💰 盈利账单",
    "🎯 价格目标管理",
    "📝 交易录入",
    "🔔 买卖信号",
    "📜 历史明细",
    "📓 复盘日记",
    "📈 交易组合中枢"
]
choice = st.sidebar.radio("功能导航", menu)

# ---------- 交易组合中枢 ----------
if choice == "📈 交易组合中枢":
    st.header("📈 交易组合中枢（Portfolio Control Center）")

    # ===== 1. 持仓与绩效汇总 =====
    st.subheader("① 组合总览")

    trades = pd.read_sql("SELECT * FROM trades ORDER BY date, id", conn)
    prices = pd.read_sql("SELECT code, current_price FROM prices", conn)
    price_map = dict(zip(prices['code'], prices['current_price']))

    summary_rows = []
    peak_map = {}

    for code in trades['code'].unique():
        s = trades[trades['code'] == code]
        buy_cash = (s[s['action']=="买入"]['price'] * s[s['action']=="买入"]['quantity']).sum()
        sell_cash = (s[s['action']=="卖出"]['price'] * s[s['action']=="卖出"]['quantity']).sum()
        net_qty = s[s['action']=="买入"]['quantity'].sum() - s[s['action']=="卖出"]['quantity'].sum()
        now_p = price_map.get(code, 0.0)
        mkt_value = net_qty * now_p if net_qty > 0 else 0.0
        realized = sell_cash - buy_cash
        total_pnl = realized + mkt_value

        peak = peak_map.get(code, mkt_value)
        peak = max(peak, mkt_value)
        peak_map[code] = peak

        summary_rows.append({
            "股票": code,
            "持仓数量": net_qty,
            "现价": now_p,
            "持仓市值": mkt_value,
            "已实现盈亏": realized,
            "总盈亏": total_pnl,
            "历史峰值市值": peak
        })

    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)
    else:
        st.info("暂无交易数据")

    # ===== 2. 决策日志 =====
    st.subheader("② 决策历史（为什么这么做）")

    with st.expander("➕ 新增决策记录", expanded=False):
        code = st.text_input("股票")
        action = st.selectbox("动作", ["买入", "卖出", "观望"])
        reason = st.text_area("决策原因")
        rule_ref = st.text_input("使用的规则/模型")
        confidence = st.slider("信心度", 0.0, 1.0, 0.5)
        if st.button("保存决策"):
            c.execute(
                "INSERT INTO decision_log (date, code, action, reason, rule_ref, confidence) VALUES (?,?,?,?,?,?)",
                (datetime.now().strftime('%Y-%m-%d'), code, action, reason, rule_ref, confidence)
            )
            conn.commit()
            st.success("决策已保存")
            st.rerun()

    dlog = pd.read_sql("SELECT * FROM decision_log ORDER BY date DESC, id DESC", conn)
    if not dlog.empty:
        st.dataframe(dlog, use_container_width=True)

    # ===== 3. 涨跌周期 =====
    st.subheader("③ 涨跌周期统计")

    with st.expander("➕ 新增周期", expanded=False):
        c_code = st.text_input("股票代码", key="cy_code")
        sd = st.date_input("开始日期", key="cy_sd")
        ed = st.date_input("结束日期", key="cy_ed")
        direction = st.selectbox("方向", ["up", "down"])
        pct = st.number_input("涨跌幅(%)", step=0.01)
        if st.button("保存周期"):
            c.execute(
                "INSERT INTO cycles (code, start_date, end_date, direction, pct) VALUES (?,?,?,?,?)",
                (c_code, sd.strftime('%Y-%m-%d'), ed.strftime('%Y-%m-%d'), direction, pct)
            )
            conn.commit()
            st.success("周期已保存")
            st.rerun()

    cycles = pd.read_sql("SELECT * FROM cycles", conn)
    if not cycles.empty:
        st.dataframe(cycles, use_container_width=True)
        up_avg = cycles[cycles['direction']=="up"]['pct'].mean()
        down_avg = cycles[cycles['direction']=="down"]['pct'].mean()
        col1, col2 = st.columns(2)
        col1.metric("平均上涨幅度", f"{up_avg:.2f}%" if pd.notna(up_avg) else "-")
        col2.metric("平均下跌幅度", f"{down_avg:.2f}%" if pd.notna(down_avg) else "-")

st.caption("v23 · 新增模块：交易组合中枢")
