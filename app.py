import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from github import Github, GithubException

# --- 1. 基础配置与数据库连接 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(pathlib.Path(__file__).with_name("stock_data_v12.db"), check_same_thread=False)

conn = get_connection()
c = conn.cursor()

# --- 数据库表结构自动升级（修复：全部使用三引号）---
c.execute('''
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        code TEXT,
        action TEXT,
        price REAL,
        quantity INTEGER,
        note TEXT
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS prices (
        code TEXT PRIMARY KEY,
        current_price REAL,
        manual_cost REAL
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS signals (
        code TEXT PRIMARY KEY,
        high_point REAL,
        low_point REAL,
        up_threshold REAL,
        down_threshold REAL,
        high_date TEXT,
        low_date TEXT
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        stock_name TEXT,
        content TEXT
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS price_targets (
        code TEXT PRIMARY KEY,
        base_price REAL DEFAULT 0.0,
        buy_target REAL DEFAULT 0.0,
        sell_target REAL DEFAULT 0.0,
        last_updated TEXT
    )
''')
# 动态增加缺失列（兼容旧数据库）
try:
    c.execute("ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0")
except sqlite3.OperationalError:
    pass
try:
    c.execute("ALTER TABLE trades ADD COLUMN note TEXT")
except sqlite3.OperationalError:
    pass
conn.commit()

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + [s for s in t_stocks if s])))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# 注入 CSS 样式
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

# 同步函数
def sync_db_to_github():
    db_filename = "stock_data_v12.db"
    local_path = pathlib.Path(__file__).with_name(db_filename)
    if not local_path.exists():
        return

    try:
        token = st.secrets["GITHUB_TOKEN"]
        owner = st.secrets["REPO_OWNER"]
        repo_name = st.secrets["REPO_NAME"]

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")

        with open(local_path, "rb") as f:
            content = f.read()

        commit_msg = f"Auto-sync stock_data_v12.db - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        try:
            file = repo.get_contents(db_filename)
            repo.update_file(db_filename, commit_msg, content, file.sha, branch="main")
        except GithubException as e:
            if e.status == 404:
                repo.create_file(db_filename, commit_msg, content, branch="main")
            else:
                raise
    except Exception:
        pass  # 静默处理

# --- 2. 侧边栏导航 ---
menu = ["📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# --- 实时持仓 ---
if choice == "📊 实时持仓":
    st.header("📊 持仓盈亏分析")
  
    # 动态格式化数字的工具函数：去除末尾无意义的0
    def format_number(num):
        if pd.isna(num) or num is None:
            return "0"
        num_str = f"{num}"
        formatted = num_str.rstrip('0').rstrip('.') if '.' in num_str else num_str
        return formatted
  
    # 读取交易数据并按时间初始排序
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
  
    if not df_trades.empty:
        stocks = df_trades['code'].unique()
      
        # 维护个股现价/手动成本
        with st.expander("🛠️ 维护现价与手动成本", expanded=True):
            raw_prices = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
            config_query = {row[0]: (row[1], row[2]) for row in raw_prices}
          
            for stock in stocks:
                col1, col2 = st.columns(2)
                stored_vals = config_query.get(stock, (0.0, 0.0))
                old_p = float(stored_vals[0]) if stored_vals[0] is not None else 0.0
                old_c = float(stored_vals[1]) if stored_vals[1] is not None else 0.0
              
                new_p = col1.number_input(f"{stock} 现价", value=old_p, key=f"p_{stock}", step=0.0001)
                new_c = col2.number_input(f"{stock} 手动成本", value=old_c, key=f"c_{stock}", step=0.0001)
              
                if new_p != old_p or new_c != old_c:
                    c.execute("INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?, ?, ?)", 
                              (stock, new_p, new_c))
                    conn.commit()
                    sync_db_to_github()
       
        # 读取最新的现价/成本配置
        final_raw = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
        latest_config = {row[0]: (row[1], row[2]) for row in final_raw}
      
        summary = []
        all_active_records = []  # 存储所有配对交易对+未平仓持仓
        
        # 按个股处理交易和持仓
        for stock in stocks:
            s_df = df_trades[df_trades['code'] == stock].copy()
            now_p, manual_cost = latest_config.get(stock, (0.0, 0.0))
          
            # 计算净持仓（买入总量-卖出总量）
            net_buy = s_df[s_df['action'] == '买入']['quantity'].sum()
            net_sell = s_df[s_df['action'] == '卖出']['quantity'].sum()
            net_q = net_buy - net_sell
          
            # 计算账户层面的盈亏比例
            if net_q != 0:
                if manual_cost > 0:
                    if net_q > 0:
                        p_rate = ((now_p - manual_cost) / manual_cost) * 100  # 正向持仓盈亏
                    else:
                        p_rate = ((manual_cost - now_p) / manual_cost) * 100  # 卖空持仓盈亏
                else:
                    p_rate = 0.0
                summary.append([
                    stock, net_q, format_number(manual_cost),
                    format_number(now_p), f"{p_rate:.2f}%", p_rate
                ])
           
            # ------------------- 核心逻辑：逐笔时间流处理交易（无时间穿越） -------------------
            buy_positions = []  # 动态维护的正向持仓池（仅存未平仓买入单）
            sell_positions = []  # 动态维护的卖空持仓池（仅存未平仓卖出单）
            paired_trades = []   # 存储已配对的交易对

            # 严格按【交易日期+ID】升序处理每一笔交易，保证时间流正确
            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                trade_date = trade['date']
                action = trade['action']
                price = trade['price']
                qty = trade['quantity']
                remaining = qty  # 初始化剩余未处理数量

                if action == '买入':
                    # 步骤1：先回补卖空持仓（高价卖空单优先回补，锁定卖空盈利）
                    if sell_positions and remaining > 0:
                        # 卖空单按价格从高到低排序，高价优先回补
                        for sp in sorted(sell_positions, key=lambda x: -x['price']):
                            if remaining <= 0:
                                break
                            if sp['qty'] <= 0:
                                continue
                            # 计算回补数量（取剩余买入量和卖空单量的最小值）
                            cover_qty = min(sp['qty'], remaining)
                            # 计算卖空回补的盈亏比例
                            gain = ((sp['price'] - price) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                            # 记录配对交易对
                            paired_trades.append({
                                "date": f"{sp['date']} → {trade_date}",
                                "code": stock,
                                "type": "✅ 已配对交易对（买入回补卖空）",
                                "price": f"{format_number(sp['price'])} → {format_number(price)}",
                                "qty": cover_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
                            })
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        sell_positions = [sp for sp in sell_positions if sp['qty'] > 0]

                    # 步骤2：剩余买入量加入正向持仓池（低价优先，方便后续平仓时低买高卖最大化盈利）
                    if remaining > 0:
                        buy_positions.append({'date': trade_date, 'price': price, 'qty': remaining})

                elif action == '卖出':
                    # 步骤1：先平掉正向持仓（低价买入单优先平仓，锁定低买高卖盈利）
                    if buy_positions and remaining > 0:
                        # 正向持仓按价格从低到高排序，低价优先平仓
                        for bp in sorted(buy_positions, key=lambda x: x['price']):
                            if remaining <= 0:
                                break
                            if bp['qty'] <= 0:
                                continue
                            # 计算平仓数量
                            close_qty = min(bp['qty'], remaining)
                            # 计算平仓盈亏比例
                            gain = ((price - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                            # 记录配对交易对
                            paired_trades.append({
                                "date": f"{bp['date']} → {trade_date}",
                                "code": stock,
                                "type": "✅ 已配对交易对（卖出平掉买入）",
                                "price": f"{format_number(bp['price'])} → {format_number(price)}",
                                "qty": close_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
                            })
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        buy_positions = [bp for bp in buy_positions if bp['qty'] > 0]

                    # 步骤2：剩余卖出量加入卖空持仓池
                    if remaining > 0:
                        sell_positions.append({'date': trade_date, 'price': price, 'qty': remaining})

            # 未平仓记录
            for bp in buy_positions:
                if bp['qty'] > 0:
                    float_gain = ((now_p - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                    all_active_records.append({
                        "date": bp['date'],
                        "code": stock,
                        "type": "买入持有",
                        "price": format_number(bp['price']),
                        "qty": bp['qty'],
                        "gain_str": f"{float_gain:.2f}%",
                        "gain_val": float_gain
                    })

            for sp in sell_positions:
                if sp['qty'] > 0:
                    float_gain = ((sp['price'] - now_p) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                    all_active_records.append({
                        "date": sp['date'],
                        "code": stock,
                        "type": "卖空持有",
                        "price": format_number(sp['price']),
                        "qty": sp['qty'],
                        "gain_str": f"{float_gain:.2f}%",
                        "gain_val": float_gain
                    })

        # 显示总结（原样保留你的表格逻辑，这里假设你有后续的显示代码）

    else:
        st.info("当前没有交易记录")

# --- 其他页面（交易录入、历史明细、复盘日记等）保持原样，只在 commit 处加同步
# （由于你的原始文档中这些部分完整，我这里只展示关键修改点，实际替换时请保持原代码结构）

# 示例：在交易录入的保存处
# if submitted:
#     ...
#     conn.commit()
#     sync_db_to_github()

# 示例：在历史明细编辑保存处
# if st.button("💾 提交所有修改", type="primary"):
#     ...
#     conn.commit()
#     sync_db_to_github()

# 示例：在复盘日记保存和删除处
# if st.button("保存日记", type="primary"):
#     ...
#     conn.commit()
#     sync_db_to_github()

# if st.button("🗑️", key=f"del_{row['id']}"):
#     if st.session_state.get(f"confirm_{row['id']}", False):
#         ...
#         conn.commit()
#         sync_db_to_github()

# --- 下载数据库按钮 ---
col1, col2, col3 = st.columns([5, 1, 1])
with col3:
    db_path = pathlib.Path(__file__).with_name("stock_data_v12.db")
    if db_path.exists():
        with open(db_path, "rb") as f:
            st.download_button(
                label="📥 下载数据库",
                data=f,
                file_name="stock_data_v12.db",
                mime="application/x-sqlite3"
            )
