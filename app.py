import streamlit as st
import sqlite3
import pandas as pd
import datetime

# 数据库连接
conn = sqlite3.connect("stock_data_v12.db")
c = conn.cursor()

# 样式定义
st.markdown("""
    <style>
    .custom-table th {
        background-color: #f1f1f1;
        font-weight: bold;
    }
    .custom-table td {
        text-align: center;
    }
    .profit-red {
        color: red;
    }
    .loss-green {
        color: green;
    }
    </style>
""", unsafe_allow_html=True)

# 数据格式化函数
def format_number(num):
    return f"{num:,.2f}"

# Git同步函数
def git_sync_safe(message):
    try:
        # 此处仅为示例，你可以根据实际情况进行Git同步
        pass
    except Exception as e:
        st.warning(f"Git同步失败: {e}")

# 侧边栏菜单
menu = ["📊 股票数据", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.selectbox("选择操作", menu)

# ======================================================
# 📊 股票数据
# ======================================================
if choice == "📊 股票数据":
    st.header("📊 股票数据展示")
    df = pd.read_sql("SELECT * FROM stock_data ORDER BY date DESC", conn)
    st.dataframe(df)

# ======================================================
# 💰 盈利账单
# ======================================================
if choice == "💰 盈利账单":
    st.header("💰 交易盈亏分析")
    
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)

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

    stock_list = pd.read_sql("SELECT DISTINCT code FROM stock_data", conn)["code"].tolist()
    with st.form("set_price_target"):
        stock = st.selectbox("选择股票", stock_list)
        buy_target = st.number_input("设定买入目标", min_value=0.0, step=0.01)
        sell_target = st.number_input("设定卖出目标", min_value=0.0, step=0.01)
        submit_button = st.form_submit_button(label="保存目标")

        if submit_button:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                trade_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    stock_list = pd.read_sql("SELECT DISTINCT code FROM stock_data", conn)["code"].tolist()

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
        journal_date = st.date_input("日期", datetime.datetime.now())
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
