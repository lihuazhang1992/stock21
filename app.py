import streamlit as st
import pandas as pd
import sqlite3
import datetime
import threading
import os
import json
import requests
from datetime import datetime

# ==============================================================================
# 配置与初始化
# ==============================================================================
st.set_page_config(page_title="智能投资管理系统", layout="wide", page_icon="📈")

# 数据库文件路径
DB_FILE = "investment_db.sqlite"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # 可选：用于同步到Github
GITHUB_REPO = os.getenv("GITHUB_REPO", "")    # 可选：用户名/仓库名

# 初始化数据库连接
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 交易记录表
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            action TEXT,
            quantity REAL,
            price REAL,
            note TEXT
        )
    ''')
    
    # 股票现价与手动成本表
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            code TEXT PRIMARY KEY,
            current_price REAL,
            manual_cost REAL
        )
    ''')
    
    # 复盘日记表
    c.execute('''
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            content TEXT,
            mood TEXT
        )
    ''')
    
    # === 新增：策略逻辑表 ===
    c.execute('''
        CREATE TABLE IF NOT EXISTS strategy_logic (
            code TEXT PRIMARY KEY,
            buy_logic TEXT,
            sell_logic TEXT,
            last_updated TEXT
        )
    ''')
    
    # === 新增：决策历史增强表 (虽然主要用trades.note，但预留扩展) ===
    # 注意：我们直接复用 trades 表的 note 字段来记录决策原因，以简化结构
    
    conn.commit()
    return conn, c

conn, c = init_db()

# ==============================================================================
# 辅助函数
# ==============================================================================

def get_dynamic_stock_list():
    """获取所有出现过的股票代码"""
    df = pd.read_sql("SELECT DISTINCT code FROM trades", conn)
    return df['code'].tolist() if not df.empty else []

def sync_db_to_github():
    """后台线程：将数据库备份同步到Github (模拟功能)"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    try:
        # 这里仅为示例逻辑，实际需调用Github API上传文件
        pass
    except Exception as e:
        print(f"Sync failed: {e}")

# ==============================================================================
# 侧边栏导航
# ==============================================================================
st.sidebar.title("🚀 智能投资系统")
menu = [
    "📝 交易录入",
    "💰 持仓监控",
    "🧠 全景智策",  # <--- 新增模块
    "📓 复盘日记",
    "📊 数据报表",
    "⚙️ 设置"
]
choice = st.sidebar.radio("导航", menu)

# ==============================================================================
# 模块实现
# ==============================================================================

if choice == "📝 交易录入":
    st.header("📝 交易录入")
    
    col1, col2 = st.columns(2)
    with col1:
        date = st.date_input("日期", datetime.datetime.now())
        code = st.text_input("股票代码 (例如: 600519)").upper()
        action = st.selectbox("操作", ["买入", "卖出"])
        quantity = st.number_input("数量 (股)", min_value=100.0, step=100.0)
        price = st.number_input("成交价", min_value=0.01, step=0.01)
        
    with col2:
        note = st.text_area("决策原因/备注 (重要! 将用于全景智策分析)", 
                            placeholder="例：突破20日均线，基本面利好，或止损纪律...")
        
        if st.button("💾 保存交易"):
            if code and quantity and price:
                c.execute("INSERT INTO trades (date, code, action, quantity, price, note) VALUES (?, ?, ?, ?, ?, ?)",
                          (date.strftime('%Y-%m-%d'), code, action, quantity, price, note))
                conn.commit()
                
                # 如果该股票不在prices表中，初始化
                c.execute("SELECT code FROM prices WHERE code=?", (code,))
                if not c.fetchone():
                    c.execute("INSERT INTO prices (code, current_price, manual_cost) VALUES (?, 0.0, 0.0)", (code,))
                    conn.commit()
                
                st.success(f"成功录入 {action} {code} {quantity}股 @ {price}")
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
            else:
                st.error("请填写完整信息")

    st.divider()
    st.subheader("最近交易记录")
    df_recent = pd.read_sql("SELECT * FROM trades ORDER BY date DESC LIMIT 10", conn)
    st.dataframe(df_recent, use_container_width=True)

elif choice == "💰 持仓监控":
    st.header("💰 实时持仓监控")
    
    # 更新现价的简单界面
    st.subheader("更新现价与成本")
    codes = get_dynamic_stock_list()
    if codes:
        selected_code = st.selectbox("选择股票", codes)
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            new_price = st.number_input("最新市场价", min_value=0.0, step=0.01)
        with col_p2:
            new_cost = st.number_input("手动修正成本价 (可选)", min_value=0.0, step=0.01)
        
        if st.button("更新价格"):
            c.execute("INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?, ?, ?)",
                      (selected_code, new_price, new_cost if new_cost > 0 else None)) # 保留原成本如果未输入
            # 如果new_cost为0且原值存在，sqlite逻辑需调整，这里简化处理：只更新非零值或强制更新
            if new_cost == 0:
                 c.execute("UPDATE prices SET current_price=? WHERE code=?", (new_price, selected_code))
            else:
                 c.execute("UPDATE prices SET current_price=?, manual_cost=? WHERE code=?", (new_price, new_cost, selected_code))
            conn.commit()
            st.success("价格已更新")
            st.rerun()

    st.divider()
    st.subheader("持仓概览")
    df_trades = pd.read_sql("SELECT * FROM trades", conn)
    df_prices = pd.read_sql("SELECT * FROM prices", conn)
    
    if df_trades.empty:
        st.info("暂无交易数据")
    else:
        summary = []
        for code in df_trades['code'].unique():
            s_df = df_trades[df_trades['code'] == code]
            p_row = df_prices[df_prices['code'] == code]
            
            curr_p = p_row['current_price'].values[0] if not p_row.empty else 0.0
            cost_p = p_row['manual_cost'].values[0] if not p_row.empty and p_row['manual_cost'].values[0] > 0 else 0.0
            
            buy_qty = s_df[s_df['action']=='买入']['quantity'].sum()
            sell_qty = s_df[s_df['action']=='卖出']['quantity'].sum()
            hold_qty = buy_qty - sell_qty
            
            if hold_qty <= 0:
                continue
                
            # 计算简易盈亏
            if cost_p == 0:
                # 加权平均成本
                total_buy_val = (s_df[s_df['action']=='买入']['price'] * s_df[s_df['action']=='买入']['quantity']).sum()
                avg_cost = total_buy_val / buy_qty if buy_qty > 0 else 0
                cost_p = avg_cost
            
            market_val = hold_qty * curr_p
            cost_val = hold_qty * cost_p
            profit = market_val - cost_val
            profit_pct = (profit / cost_val * 100) if cost_val > 0 else 0
            
            summary.append({
                "代码": code,
                "持仓数量": hold_qty,
                "现价": curr_p,
                "成本价": cost_p,
                "持仓市值": market_val,
                "盈亏金额": profit,
                "盈亏比例(%)": profit_pct
            })
        
        if summary:
            df_sum = pd.DataFrame(summary)
            st.dataframe(df_sum.style.format({"盈亏金额": "{:.2f}", "盈亏比例(%)": "{:.2f}%"}), use_container_width=True)
            st.metric("总持仓市值", f"{df_sum['持仓市值'].sum():,.2f}")
        else:
            st.warning("当前无有效持仓（所有股票已卖出）")

# ==============================================================================
# 🧠 全景智策 (新增核心模块)
# ==============================================================================
elif choice == "🧠 全景智策":
    st.header("🧠 全景智策 | 持仓·逻辑·周期·决策")
    
    # --- 辅助函数定义 ---
    def get_strategy(code):
        row = c.execute("SELECT buy_logic, sell_logic FROM strategy_logic WHERE code=?", (code,)).fetchone()
        return row if row else ("", "")
    
    def save_strategy(code, buy_l, sell_l):
        c.execute("INSERT OR REPLACE INTO strategy_logic (code, buy_logic, sell_logic, last_updated) VALUES (?, ?, ?, ?)",
                  (code, buy_l, sell_l, datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit()
        thread = threading.Thread(target=sync_db_to_github, daemon=True)
        thread.start()

    def calculate_cycle_stats(df_stock):
        """计算涨跌周期统计"""
        if df_stock.empty: return [], 0.0, 0.0
        
        df_stock = df_stock.sort_values('date')
        cycles = []
        current_trend = None 
        start_date = None
        start_price = None
        peak_price = 0
        trough_price = float('inf')
        
        up_cycles = []
        down_cycles = []
        
        prices = df_stock['price'].tolist()
        dates = df_stock['date'].tolist()
        
        if len(prices) < 2: return [], 0.0, 0.0

        for i in range(1, len(prices)):
            p_prev, p_curr = prices[i-1], prices[i]
            d_prev, d_curr = dates[i-1], dates[i]
            change = (p_curr - p_prev) / p_prev
            
            if change > 0:
                if current_trend != 'up':
                    if current_trend == 'down' and start_date:
                        drop_pct = (trough_price - start_price) / start_price * 100
                        down_cycles.append(f"{start_date} → {d_prev} ({drop_pct:.2f}%)")
                    current_trend = 'up'
                    start_date = d_prev
                    start_price = p_prev
                    peak_price = p_curr
                else:
                    peak_price = max(peak_price, p_curr)
            elif change < 0:
                if current_trend != 'down':
                    if current_trend == 'up' and start_date:
                        rise_pct = (peak_price - start_price) / start_price * 100
                        up_cycles.append(f"{start_date} → {d_prev} (+{rise_pct:.2f}%)")
                    current_trend = 'down'
                    start_date = d_prev
                    start_price = p_prev
                    trough_price = p_curr
                else:
                    trough_price = min(trough_price, p_curr)
        
        avg_up = sum([float(c.split('(')[1].replace('%','').replace('+','')) for c in up_cycles]) / len(up_cycles) if up_cycles else 0.0
        avg_down = sum([float(c.split('(')[1].replace('%)','')) for c in down_cycles]) / len(down_cycles) if down_cycles else 0.0
        
        return up_cycles + down_cycles, avg_up, avg_down

    # --- 主界面 ---
    all_stocks = get_dynamic_stock_list()
    if not all_stocks:
        st.warning("暂无交易数据，请先在【交易录入】中记录。")
        st.stop()

    selected_stock = st.selectbox("🔍 选择股票进行深度分析", options=["全部"] + all_stocks)
    
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC", conn)
    df_prices = pd.read_sql("SELECT * FROM prices", conn)
    price_map = dict(zip(df_prices['code'], df_prices['current_price']))
    cost_map = dict(zip(df_prices['code'], df_prices['manual_cost']))
    
    if selected_stock != "全部":
        df_trades = df_trades[df_trades['code'] == selected_stock]
        stocks_to_analyze = [selected_stock]
    else:
        stocks_to_analyze = df_trades['code'].unique()

    for stock in stocks_to_analyze:
        with st.expander(f"📈 {stock} 全景分析", expanded=(selected_stock != "全部")):
            col_info, col_logic = st.columns([2, 1])
            
            with col_info:
                s_df = df_trades[df_trades['code'] == stock]
                curr_p = price_map.get(stock, 0.0)
                manual_c = cost_map.get(stock, 0.0)
                
                net_buy = s_df[s_df['action']=='买入']['quantity'].sum()
                net_sell = s_df[s_df['action']=='卖出']['quantity'].sum()
                hold_qty = net_buy - net_sell
                hold_value = hold_qty * curr_p
                
                total_buy_cash = (s_df[s_df['action']=='买入']['price'] * s_df[s_df['action']=='买入']['quantity']).sum()
                total_sell_cash = (s_df[s_df['action']=='卖出']['price'] * s_df[s_df['action']=='卖出']['quantity']).sum()
                
                if manual_c == 0 and hold_qty > 0:
                    avg_cost = total_buy_cash / net_buy if net_buy > 0 else 0
                    current_cost_val = avg_cost * hold_qty
                else:
                    current_cost_val = manual_c * hold_qty
                
                realized_profit = total_sell_cash - (total_buy_cash - current_cost_val) if hold_qty >= 0 else (total_sell_cash - total_buy_cash)
                total_profit = realized_profit + (hold_value - current_cost_val)
                
                max_hold_qty = 0
                running_qty = 0
                hist_high_price = s_df['price'].max()
                for _, row in s_df.iterrows():
                    if row['action'] == '买入': running_qty += row['quantity']
                    else: running_qty -= row['quantity']
                    max_hold_qty = max(max_hold_qty, running_qty)
                peak_hold_value = max_hold_qty * hist_high_price

                first_date = pd.to_datetime(s_df['date'].min())
                days_passed = (datetime.now() - first_date).days
                annual_return = ((1 + total_profit / total_buy_cash) ** (365 / max(days_passed, 1)) - 1) * 100 if total_buy_cash > 0 else 0

                cycles, avg_up, avg_down = calculate_cycle_stats(s_df)
                
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("持仓市值", f"{hold_value:,.2f}", delta=f"{hold_qty}股")
                m2.metric("总盈亏金额", f"{total_profit:,.2f}", delta=f"{(total_profit/current_cost_val*100) if current_cost_val else 0:.2f}%")
                m3.metric("已实现利润", f"{realized_profit:,.2f}")
                m4.metric("历史峰值持仓", f"{peak_hold_value:,.2f}")
                
                st.caption(f"💡 年化收益率估算: {annual_return:.2f}% | 平均涨幅: {avg_up:.2f}% | 平均跌幅: {avg_down:.2f}%")
                
                if cycles:
                    with st.popover("查看涨跌周期明细"):
                        for c in cycles:
                            st.text(c)
                else:
                    st.caption("交易数据不足以生成周期分析")

            with col_logic:
                st.subheader("🧠 策略与决策")
                buy_log, sell_log = get_strategy(stock)
                
                with st.form(f"logic_form_{stock}"):
                    new_buy = st.text_area("买入逻辑 (何时买?)", value=buy_log, height=70, placeholder="例：突破20日均线，且RSI<30")
                    new_sell = st.text_area("卖出逻辑 (何时卖?)", value=sell_log, height=70, placeholder="例：跌破10日均线，或盈利达20%")
                    if st.form_submit_button("保存策略"):
                        save_strategy(stock, new_buy, new_sell)
                        st.success("策略已更新")
                        st.rerun()
                
                st.divider()
                st.markdown("**📜 决策历史 (Why)**")
                recent_notes = s_df.tail(5)[['date', 'action', 'note']]
                if recent_notes.empty or recent_notes['note'].isna().all():
                    st.warning("暂无决策记录，请在录入交易时填写'备注'。")
                else:
                    for _, row in recent_notes.iterrows():
                        icon = "🟢" if row['action']=='买入' else "🔴"
                        note_text = row['note'] if pd.notna(row['note']) else "无记录"
                        st.markdown(f"{icon} **{row['date']} {row['action']}**: {note_text}")

    st.divider()
    st.info("💡 提示：决策原因需在 [📝 交易录入] 模块的'备注'栏中填写；策略逻辑在此模块维护。")

elif choice == "📓 复盘日记":
    st.header("📓 复盘日记")
    date = st.date_input("日期", datetime.datetime.now())
    content = st.text_area("今日复盘内容", height=200)
    mood = st.select_slider("心情指数", options=["😫", "😐", "🙂", "😃", "🤩"])
    
    if st.button("保存日记"):
        c.execute("INSERT INTO diary (date, content, mood) VALUES (?, ?, ?)",
                  (date.strftime('%Y-%m-%d'), content, mood))
        conn.commit()
        st.success("日记已保存")
        thread = threading.Thread(target=sync_db_to_github, daemon=True)
        thread.start()
    
    st.divider()
    df_diary = pd.read_sql("SELECT * FROM diary ORDER BY date DESC", conn)
    for _, row in df_diary.iterrows():
        with st.container():
            st.markdown(f"**{row['date']} {row['mood']}**")
            st.write(row['content'])
            st.divider()

elif choice == "📊 数据报表":
    st.header("📊 数据报表")
    tab1, tab2 = st.tabs(["交易流水", "资金曲线"])
    with tab1:
        df = pd.read_sql("SELECT * FROM trades ORDER BY date", conn)
        st.dataframe(df, use_container_width=True)
    with tab2:
        st.info("资金曲线功能开发中... (需结合每日净值计算)")

elif choice == "⚙️ 设置":
    st.header("⚙️ 系统设置")
    st.write("当前数据库文件:", DB_FILE)
    if st.button("🗑️ 清空所有数据 (危险操作)"):
        c.execute("DELETE FROM trades")
        c.execute("DELETE FROM prices")
        c.execute("DELETE FROM diary")
        c.execute("DELETE FROM strategy_logic")
        conn.commit()
        st.success("数据已清空")
        st.rerun()

# 关闭连接 (Streamlit 会在脚本重新运行时处理，此处仅为规范)
# conn.close() 
