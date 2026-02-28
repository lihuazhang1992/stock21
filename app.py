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

try:                       # 本地优先 .env；Cloud 用 st.secrets
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN    = os.getenv("GITHUB_TOKEN")
    REPO_URL = os.getenv("REPO_URL")
except Exception:
    TOKEN    = st.secrets.get("GITHUB_TOKEN", "")
    REPO_URL = st.secrets.get("REPO_URL", "")

def sync_db_to_github():
    """彻底修复 exit code(128) 的备份逻辑"""
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
        print(f"GitHub备份严重错误: {e}")
        if not os.environ.get("STREAMLIT_CLOUD"):
            st.toast(f"⚠️ 备份失败: {e}", icon="⚠️")

# --- 1. 基础配置与数据库连接 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

# 启动时：如果本地没有数据库，从 GitHub 下载
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

# --- 数据库表结构 ---
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

c.execute('''CREATE TABLE IF NOT EXISTS strategy_notes (
    code TEXT PRIMARY KEY,
    logic TEXT,
    max_holding_amount REAL DEFAULT 0.0,
    annual_return REAL DEFAULT 0.0,
    buy_base_price REAL DEFAULT 0.0,
    buy_drop_pct REAL DEFAULT 0.0,
    sell_base_price REAL DEFAULT 0.0,
    sell_rise_pct REAL DEFAULT 0.0,
    buy_logic TEXT,
    sell_logic TEXT
)''')

c.execute('''CREATE TABLE IF NOT EXISTS decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    date TEXT,
    decision TEXT,
    reason TEXT
)''')

c.execute('''CREATE TABLE IF NOT EXISTS price_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    start_date TEXT,
    end_date TEXT,
    change_pct REAL
)''')

c.execute('''CREATE TABLE IF NOT EXISTS price_targets (
    code TEXT PRIMARY KEY,
    base_price REAL DEFAULT 0.0,
    buy_target REAL DEFAULT 0.0,
    sell_target REAL DEFAULT 0.0,
    last_updated TEXT
)''')

# 动态增加缺失列（兼容旧版本）
for col in [
    ("strategy_notes", "annual_return", "REAL DEFAULT 0.0"),
    ("strategy_notes", "buy_base_price", "REAL DEFAULT 0.0"),
    ("strategy_notes", "buy_drop_pct", "REAL DEFAULT 0.0"),
    ("strategy_notes", "sell_base_price", "REAL DEFAULT 0.0"),
    ("strategy_notes", "sell_rise_pct", "REAL DEFAULT 0.0"),
    ("strategy_notes", "buy_logic", "TEXT"),
    ("strategy_notes", "sell_logic", "TEXT"),
    ("prices", "manual_cost", "REAL DEFAULT 0.0"),
    ("trades", "note", "TEXT")
]:
    try:
        c.execute(f"ALTER TABLE {col[0]} ADD COLUMN {col[1]} {col[2]}")
    except sqlite3.OperationalError:
        pass

conn.commit()

# 启动后台备份线程
threading.Thread(target=sync_db_to_github, daemon=True).start()

def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + t_stocks)))
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
menu = ["📈 策略复盘", "📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# ── 策略复盘 ────────────────────────────────────────────────────────────────
if choice == "📈 策略复盘":
    st.header("📈 策略复盘与深度账本")
    
    all_stocks = get_dynamic_stock_list()
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    latest_prices_data = {row[0]: (row[1], row[2]) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}
    manual_costs = {k: v[1] for k, v in latest_prices_data.items()}
    
    selected_stock = st.selectbox("🔍 选择分析股票", all_stocks, index=0 if all_stocks else None)
    
    if selected_stock:
        s_df = df_trades[df_trades['code'] == selected_stock].copy()
        now_p = latest_prices.get(selected_stock, 0.0)
        
        # 核心计算：已实现利润、最高占用、当前占用
        realized_profit = 0.0
        max_occupied_amount = 0.0
        current_occupied_amount = 0.0
        buy_pool = []
        sell_pool = []
        net_q = 0
        
        for _, t in s_df.iterrows():
            price = t['price']
            qty = t['quantity']
            
            if t['action'] == '买入':
                remaining = qty
                while remaining > 0 and sell_pool:
                    sell_pool.sort(key=lambda x: x['price'], reverse=True)
                    sp = sell_pool[0]
                    match = min(remaining, sp['qty'])
                    realized_profit += (sp['price'] - price) * match
                    sp['qty'] -= match
                    remaining -= match
                    if sp['qty'] <= 0: sell_pool.pop(0)
                if remaining > 0:
                    buy_pool.append({'price': price, 'qty': remaining})
                net_q += qty
            else:
                remaining = qty
                while remaining > 0 and buy_pool:
                    buy_pool.sort(key=lambda x: x['price'])
                    bp = buy_pool[0]
                    match = min(remaining, bp['qty'])
                    realized_profit += (price - bp['price']) * match
                    bp['qty'] -= match
                    remaining -= match
                    if bp['qty'] <= 0: buy_pool.pop(0)
                if remaining > 0:
                    sell_pool.append({'price': price, 'qty': remaining})
                net_q -= qty
            
            current_occupied_amount = sum(x['price']*x['qty'] for x in buy_pool) + sum(x['price']*x['qty'] for x in sell_pool)
            max_occupied_amount = max(max_occupied_amount, current_occupied_amount)

        avg_cost = manual_costs.get(selected_stock, 0.0)
        holding_profit_amount = 0.0
        holding_profit_pct = 0.0
        if net_q != 0 and avg_cost > 0:
            if net_q > 0:
                holding_profit_amount = (now_p - avg_cost) * net_q
                holding_profit_pct = (now_p - avg_cost) / avg_cost * 100
            else:
                abs_q = abs(net_q)
                holding_profit_amount = (avg_cost - now_p) * abs_q
                holding_profit_pct = (avg_cost - now_p) / avg_cost * 100

        # 读取策略笔记
        strategy_df = pd.read_sql("SELECT * FROM strategy_notes WHERE code = ?", conn, params=(selected_stock,))
        if not strategy_df.empty:
            row = strategy_df.iloc[0]
            saved_annual = row.get('annual_return', 0.0)
            s_buy_base = row.get('buy_base_price', 0.0)
            s_buy_drop = row.get('buy_drop_pct', 0.0)
            s_sell_base = row.get('sell_base_price', 0.0)
            s_sell_rise = row.get('sell_rise_pct', 0.0)
            saved_buy_logic = row.get('buy_logic', "")
            saved_sell_logic = row.get('sell_logic', "")
        else:
            saved_annual = 0.0
            s_buy_base = s_buy_drop = s_sell_base = s_sell_rise = 0.0
            saved_buy_logic = saved_sell_logic = ""

        # 核心指标卡片
        st.subheader(f"📊 {selected_stock} 核心数据概览")
        buy_monitor_p = s_buy_base * (1 - s_buy_drop / 100) if s_buy_base > 0 else 0
        sell_monitor_p = s_sell_base * (1 + s_sell_rise / 100) if s_sell_base > 0 else 0
        is_buy_triggered = s_buy_base > 0 and now_p <= buy_monitor_p
        is_sell_triggered = s_sell_base > 0 and now_p >= sell_monitor_p

        cycles_data = pd.read_sql("SELECT change_pct FROM price_cycles WHERE code = ?", conn, params=(selected_stock,))
        up_avg = cycles_data[cycles_data['change_pct'] > 0]['change_pct'].mean() if not cycles_data.empty else 0
        down_avg = cycles_data[cycles_data['change_pct'] < 0]['change_pct'].mean() if not cycles_data.empty else 0

        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        r1c1.metric("持仓数量", f"{net_q}")
        r1c2.metric("持仓市值", f"{abs(net_q) * now_p:,.2f}")
        r1c3.metric("成本价", f"{avg_cost:.3f}")
        r1c4.metric("当前现价", f"{now_p:.3f}")

        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        p_color = "normal" if holding_profit_amount >= 0 else "inverse"
        r2c1.metric("持仓盈亏额", f"{holding_profit_amount:,.2f}", delta=f"{holding_profit_pct:.2f}%", delta_color=p_color)
        r2c2.metric("已实现利润", f"{realized_profit:,.2f}")
        r2c3.metric("最高占用金额", f"{max_occupied_amount:,.2f}")
        r2c4.metric("历史年化收益", f"{saved_annual:.2f}%")

        r3c1, r3c2, r3c3, r3c4 = st.columns(4)
        if s_buy_base > 0:
            b_label = "🔴 买入监控 (达标)" if is_buy_triggered else "📥 买入监控 (观察)"
            r3c1.metric(b_label, f"{buy_monitor_p:.3f}")
        else:
            r3c1.metric("📥 买入监控", "未设置")
        if s_sell_base > 0:
            s_label = "🔴 卖出监控 (达标)" if is_sell_triggered else "📤 卖出监控 (观察)"
            r3c2.metric(s_label, f"{sell_monitor_p:.3f}")
        else:
            r3c2.metric("📤 卖出监控", "未设置")
        r3c3.metric("📈 平均涨幅", f"{up_avg:.2f}%" if not pd.isna(up_avg) else "0.00%")
        r3c4.metric("📉 平均跌幅", f"{down_avg:.2f}%" if not pd.isna(down_avg) else "0.00%")

        if saved_buy_logic or saved_sell_logic:
            lc1, lc2 = st.columns(2)
            if saved_buy_logic:
                lc1.markdown(f"""
                <div style="background: rgba(0,0,0,0.4);border-radius:12px;padding:20px;border-left:8px solid #00C49F;margin-top:15px;box-shadow:0 4px 15px rgba(0,0,0,0.3);">
                    <h4 style="margin:0;color:#00C49F;font-size:1.1em;font-weight:bold;margin-bottom:10px;">🟢 买入原则</h4>
                    <div style="white-space:pre-wrap;font-size:1.0em;color:#FFFFFF;font-weight:500;line-height:1.5;">{saved_buy_logic}</div>
                </div>
                """, unsafe_allow_html=True)
            if saved_sell_logic:
                lc2.markdown(f"""
                <div style="background: rgba(0,0,0,0.4);border-radius:12px;padding:20px;border-left:8px solid #FF4B4B;margin-top:15px;box-shadow:0 4px 15px rgba(0,0,0,0.3);">
                    <h4 style="margin:0;color:#FF4B4B;font-size:1.1em;font-weight:bold;margin-bottom:10px;">🔴 卖出原则</h4>
                    <div style="white-space:pre-wrap;font-size:1.0em;color:#FFFFFF;font-weight:500;line-height:1.5;">{saved_sell_logic}</div>
                </div>
                """, unsafe_allow_html=True)

        st.divider()

        # 交易逻辑与决策历史
        col_left, col_right = st.columns([1, 1])
        
        with col_left:
            st.subheader("🧠 交易逻辑与参数设置")
            with st.form("strategy_form"):
                st.write("**📝 交易逻辑 (买卖原则)**")
                fc1, fc2 = st.columns(2)
                new_buy_logic = fc1.text_area("🟢 买入原则", value=saved_buy_logic, height=150)
                new_sell_logic = fc2.text_area("🔴 卖出原则", value=saved_sell_logic, height=150)
                new_annual = st.number_input("历史平均年化收益率 (%)", value=float(saved_annual), step=0.01)
                
                st.write("---")
                st.write("**📥 买入监控设置**")
                col_b1, col_b2 = st.columns(2)
                new_buy_base = col_b1.number_input("买入基准价", value=float(s_buy_base), step=0.01)
                new_buy_drop = col_b2.number_input("下跌比例 (%)", value=float(s_buy_drop), step=0.1)
                
                st.write("**📤 卖出监控设置**")
                col_s1, col_s2 = st.columns(2)
                new_sell_base = col_s1.number_input("卖出基准价", value=float(s_sell_base), step=0.01)
                new_sell_rise = col_s2.number_input("上涨比例 (%)", value=float(s_sell_rise), step=0.1)
                
                if st.form_submit_button("💾 保存所有设置"):
                    try:
                        c.execute("""
                            INSERT OR REPLACE INTO strategy_notes 
                            (code, logic, max_holding_amount, annual_return, buy_base_price, buy_drop_pct, sell_base_price, sell_rise_pct, buy_logic, sell_logic) 
                            VALUES (?,?,?,?,?,?,?,?,?,?)
                        """, (selected_stock, "", max_occupied_amount, new_annual, new_buy_base, new_buy_drop, new_sell_base, new_sell_rise, new_buy_logic, new_sell_logic))
                        conn.commit()
                        threading.Thread(target=sync_db_to_github, daemon=True).start()
                        st.success("✅ 配置已成功保存")
                        st.rerun()
                    except Exception as e:
                        st.error(f"保存失败：{e}")
                        try:
                            c.execute("INSERT OR REPLACE INTO strategy_notes (code, max_holding_amount, annual_return) VALUES (?,?,?)",
                                      (selected_stock, max_occupied_amount, new_annual))
                            conn.commit()
                            st.warning("部分字段保存失败，已保存核心数据")
                            st.rerun()
                        except Exception as e2:
                            st.error(f"核心数据也保存失败：{e2}")

        with col_right:
            st.subheader("📜 决策历史记录")
            with st.expander("➕ 新增决策记录"):
                with st.form("new_decision", clear_on_submit=True):
                    d_date = st.date_input("日期", datetime.now())
                    d_content = st.text_input("决策内容")
                    d_reason = st.text_area("决策原因")
                    if st.form_submit_button("记录决策"):
                        c.execute("INSERT INTO decision_history (code, date, decision, reason) VALUES (?,?,?,?)", 
                                  (selected_stock, d_date.strftime('%Y-%m-%d'), d_content, d_reason))
                        conn.commit()
                        threading.Thread(target=sync_db_to_github, daemon=True).start()
                        st.rerun()

            decisions = pd.read_sql("SELECT id, date, decision, reason FROM decision_history WHERE code = ? ORDER BY date DESC", conn, params=(selected_stock,))
            for _, row in decisions.iterrows():
                with st.container(border=True):
                    head_col, del_col = st.columns([9, 1])
                    head_col.markdown(f"**{row['date']} | {row['decision']}**")
                    if del_col.button("🗑️", key=f"del_dec_{row['id']}"):
                        c.execute("DELETE FROM decision_history WHERE id = ?", (row['id'],))
                        conn.commit()
                        threading.Thread(target=sync_db_to_github, daemon=True).start()
                        st.rerun()
                    st.caption(row['reason'])

        st.divider()

        # 涨跌周期管理
        st.subheader("📉 历史涨跌周期统计")
        cycle_input, cycle_list = st.columns([1, 2])
        
        with cycle_input:
            with st.form("new_cycle", clear_on_submit=True):
                st.write("**新增涨跌周期**")
                cy_start = st.date_input("开始日期")
                cy_end = st.date_input("结束日期")
                cy_pct = st.number_input("涨跌幅 (%)", step=0.01)
                if st.form_submit_button("添加周期"):
                    c.execute("INSERT INTO price_cycles (code, start_date, end_date, change_pct) VALUES (?,?,?,?)", 
                              (selected_stock, cy_start.strftime('%Y-%m-%d'), cy_end.strftime('%Y-%m-%d'), cy_pct))
                    conn.commit()
                    threading.Thread(target=sync_db_to_github, daemon=True).start()
                    st.rerun()
        
        with cycle_list:
            cycles = pd.read_sql("SELECT id, start_date, end_date, change_pct FROM price_cycles WHERE code = ? ORDER BY start_date DESC", conn, params=(selected_stock,))
            if not cycles.empty:
                up_avg = cycles[cycles['change_pct'] > 0]['change_pct'].mean()
                down_avg = cycles[cycles['change_pct'] < 0]['change_pct'].mean()
                st.markdown(f"📈 **平均涨幅:** `{up_avg:.2f}%` | 📉 **平均跌幅:** `{down_avg:.2f}%`")
                for _, row in cycles.iterrows():
                    c_col, d_col = st.columns([8, 2])
                    color = "#d32f2f" if row['change_pct'] > 0 else "#388e3c"
                    c_col.markdown(f"`{row['start_date']} → {row['end_date']}` <span style='color:{color};font-weight:bold;'>({row['change_pct']:+.2f}%)</span>", unsafe_allow_html=True)
                    if d_col.button("删除", key=f"del_cyc_{row['id']}"):
                        c.execute("DELETE FROM price_cycles WHERE id = ?", (row['id'],))
                        conn.commit()
                        threading.Thread(target=sync_db_to_github, daemon=True).start()
                        st.rerun()
            else:
                st.info("暂无涨跌周期记录")

# 其他功能页面保持原样（这里省略大量代码以节省篇幅）
# 你可以把下面这些部分从你原来的代码里直接复制过来替换对应位置：
#   - 实时持仓
#   - 盈利账单
#   - 价格目标管理
#   - 交易录入
#   - 买卖信号
#   - 历史明细
#   - 复盘日记
#   - 最后的下载按钮

# 示例：下载数据库按钮（放在文件末尾）
st.sidebar.markdown("---")
if DB_FILE.exists():
    with open(DB_FILE, "rb") as f:
        st.sidebar.download_button(
            label="📥 下载数据库",
            data=f,
            file_name="stock_data_v12.db",
            mime="application/x-sqlite3"
        )
