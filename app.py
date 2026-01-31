import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from github import Github, GithubException

# --- 基础配置 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(pathlib.Path(__file__).with_name("stock_data_v12.db"), check_same_thread=False)

conn = get_connection()
c = conn.cursor()

# --- 表结构（原样） ---
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

try: c.execute("ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0"); conn.commit()
except: pass
try: c.execute("ALTER TABLE trades ADD COLUMN note TEXT"); conn.commit()
except: pass

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + [s for s in t_stocks if s])))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# CSS
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

# 同步函数（原样）
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
        commit_msg = f"Auto sync {db_filename} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        try:
            file = repo.get_contents(db_filename)
            repo.update_file(db_filename, commit_msg, content, file.sha, branch="main")
        except GithubException as e:
            if e.status == 404:
                repo.create_file(db_filename, commit_msg, content, branch="main")
    except Exception as e:
        st.error(f"同步失败：{str(e)}")

# 菜单（无 emoji）
menu = [
    "实时持仓",
    "盈利账单",
    "价格目标管理",
    "交易录入",
    "买卖信号",
    "历史明细",
    "复盘日记"
]
choice = st.sidebar.radio("功能导航", menu)

# 调试行
st.sidebar.write("当前选择：", choice)

# 主内容
if choice == "实时持仓":
    st.header("实时持仓盈亏分析")

    def format_number(num):
        if pd.isna(num) or num is None:
            return "0"
        num_str = f"{num}"
        formatted = num_str.rstrip('0').rstrip('.') if '.' in num_str else num_str
        return formatted

    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)

    if not df_trades.empty:
        stocks = df_trades['code'].unique()

        with st.expander("维护现价与手动成本", expanded=True):
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

        # 读取最新配置
        final_raw = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
        latest_config = {row[0]: (row[1], row[2]) for row in final_raw}

        summary = []
        all_active_records = []

        for stock in stocks:
            s_df = df_trades[df_trades['code'] == stock].copy()
            now_p, manual_cost = latest_config.get(stock, (0.0, 0.0))

            net_buy = s_df[s_df['action'] == '买入']['quantity'].sum()
            net_sell = s_df[s_df['action'] == '卖出']['quantity'].sum()
            net_q = net_buy - net_sell

            if net_q != 0:
                if manual_cost > 0:
                    if net_q > 0:
                        p_rate = ((now_p - manual_cost) / manual_cost) * 100
                    else:
                        p_rate = ((manual_cost - now_p) / manual_cost) * 100
                else:
                    p_rate = 0.0
                summary.append([
                    stock, net_q, format_number(manual_cost),
                    format_number(now_p), f"{p_rate:.2f}%", p_rate
                ])

            # 逐笔处理交易逻辑（原代码核心部分）
            buy_positions = []
            sell_positions = []
            paired_trades = []

            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                trade_date = trade['date']
                action = trade['action']
                price = trade['price']
                qty = trade['quantity']
                remaining = qty

                if action == '买入':
                    if sell_positions and remaining > 0:
                        for sp in sorted(sell_positions, key=lambda x: -x['price']):
                            if remaining <= 0: break
                            if sp['qty'] <= 0: continue
                            cover_qty = min(sp['qty'], remaining)
                            gain = ((sp['price'] - price) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                            paired_trades.append({
                                "date": f"{sp['date']} → {trade_date}",
                                "code": stock,
                                "type": "已配对交易对",
                                "price": f"{format_number(sp['price'])} → {format_number(price)}",
                                "qty": cover_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
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
                                "date": f"{bp['date']} → {trade_date}",
                                "code": stock,
                                "type": "已配对交易对",
                                "price": f"{format_number(bp['price'])} → {format_number(price)}",
                                "qty": close_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
                            })
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        buy_positions = [bp for bp in buy_positions if bp['qty'] > 0]

                    if remaining > 0:
                        sell_positions.append({'date': trade_date, 'price': price, 'qty': remaining})

            # 未平仓记录
            for bp in buy_positions:
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

        # 显示总结表格（简化版，先显示净持仓）
        if summary:
            df_summary = pd.DataFrame(summary, columns=["股票", "净持仓", "手动成本", "现价", "盈亏率", "盈亏率数值"])
            st.subheader("持仓总结")
            st.dataframe(df_summary.style.format({"盈亏率": "{:.2f}%"}), use_container_width=True)

        if all_active_records:
            df_active = pd.DataFrame(all_active_records)
            st.subheader("未平仓记录")
            st.table(df_active)

    else:
        st.info("暂无交易记录")

elif choice == "交易录入":
    st.header("交易录入")

    full_list = get_dynamic_stock_list()
    t_code = st.selectbox("选择股票", options=["【添加新股票】"] + full_list, index=None)
    final_code = st.text_input("新股票名（必填）") if t_code == "【添加新股票】" else t_code

    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        d = c1.date_input("日期", datetime.now())
        a = c2.selectbox("操作", ["买入", "卖出"])

        p = c1.number_input("单价", value=None, min_value=0.0, step=0.001, format="%.3f")
        q = c2.number_input("数量", value=None, min_value=1, step=1)

        note = st.text_input("备注（可选）")
        submitted = st.form_submit_button("保存交易")

        if submitted:
            if not final_code:
                st.error("请填写或选择股票代码")
            elif p is None or q is None:
                st.error("请填写单价和数量")
            else:
                c.execute("""
                    INSERT INTO trades (date, code, action, price, quantity, note)
                    VALUES (?,?,?,?,?,?)
                """, (d.strftime('%Y-%m-%d'), final_code, a, p, q, note if note.strip() else None))
                conn.commit()
                sync_db_to_github()
                st.success("交易记录已保存！")
                st.rerun()

# 其他页面占位（你可以后期加回完整逻辑）
elif choice == "盈利账单":
    st.header("盈利账单")
    st.write("待实现")

elif choice == "价格目标管理":
    st.header("价格目标管理")
    st.write("待实现")

elif choice == "买卖信号":
    st.header("买卖信号")
    st.write("待实现")

elif choice == "历史明细":
    st.header("历史明细")
    st.write("待实现")

elif choice == "复盘日记":
    st.header("复盘日记")
    st.write("待实现")

# 下载按钮
col1, col2, col3 = st.columns([5, 1, 1])
with col3:
    db_path = pathlib.Path(__file__).with_name("stock_data_v12.db")
    if db_path.exists():
        with open(db_path, "rb") as f:
            st.download_button(
                label="下载数据库",
                data=f,
                file_name="stock_data_v12.db",
                mime="application/x-sqlite3"
            )
