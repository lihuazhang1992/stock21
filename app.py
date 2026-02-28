from git import Repo
import os, shutil, streamlit as st_git
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
        # 定义路径
        base_dir = pathlib.Path(__file__).parent
        repo_dir = base_dir / ".git_repo"
        db_name = DB_FILE.name
        auth_url = REPO_URL.replace("https://", f"https://x-access-token:{TOKEN}@")

        # 1. 环境清理：如果文件夹已存在，强制删除以防止状态污染
        if repo_dir.exists():
            shutil.rmtree(repo_dir)

        # 2. 深度为1的克隆（快速且干净）
        repo = Repo.clone_from(auth_url, repo_dir, depth=1)

        # 3. 必须配置用户信息，否则无法 commit
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Streamlit_Bot")
            cw.set_value("user", "email", "bot@example.com")

        # 4. 覆盖数据库文件
        shutil.copy2(base_dir / db_name, repo_dir / db_name)

        # 5. 检查变化并推送
        if repo.is_dirty(untracked_files=True):
            repo.git.add(all=True)
            repo.index.commit(f"Auto-sync {datetime.now().strftime('%m%d-%H%M')}")
            
            # 强制推送防止冲突
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
# ==========================================


# --- 1. 基础配置与数据库连接 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(pathlib.Path(__file__).with_name("stock_data_v12.db"), check_same_thread=False)
# === 启动时：如果本地没有数据库，从 GitHub 下载 ===
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
        st.stop()  # 停止运行
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
    CREATE TABLE IF NOT EXISTS strategy_notes (
        code TEXT PRIMARY KEY,
        logic TEXT,
        max_holding_amount REAL DEFAULT 0.0,
        annual_return REAL DEFAULT 0.0
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS decision_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        date TEXT,
        decision TEXT,
        reason TEXT
    )
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS price_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        start_date TEXT,
        end_date TEXT,
        change_pct REAL
    )
''')
# 动态增加缺失列（兼容旧版本）
try:
    c.execute("ALTER TABLE strategy_notes ADD COLUMN annual_return REAL DEFAULT 0.0")
except:
    pass
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

try:
    c.execute("ALTER TABLE strategy_notes ADD COLUMN buy_base_price REAL DEFAULT 0.0")
    c.execute("ALTER TABLE strategy_notes ADD COLUMN buy_drop_pct REAL DEFAULT 0.0")
    c.execute("ALTER TABLE strategy_notes ADD COLUMN sell_base_price REAL DEFAULT 0.0")
    c.execute("ALTER TABLE strategy_notes ADD COLUMN sell_rise_pct REAL DEFAULT 0.0")
except:
    pass

thread = threading.Thread(target=sync_db_to_github, daemon=True)
thread.start()

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

# --- 2. 侧边栏导航 ---
menu = ["📈 策略复盘", "📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# --- 实时持仓 ---

# --- 📈 策略复盘 ---
if choice == "📈 策略复盘":
    st.header("📈 策略复盘与深度账本")
    
    all_stocks = get_dynamic_stock_list()
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    latest_prices_data = {row[0]: (row[1], row[2]) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}
    manual_costs = {k: v[1] for k, v in latest_prices_data.items()}
    
    # 统一选择股票
    selected_stock = st.selectbox("🔍 选择分析股票", all_stocks, index=0 if all_stocks else None)
    
    if selected_stock:
        s_df = df_trades[df_trades['code'] == selected_stock].copy()
        now_p = latest_prices.get(selected_stock, 0.0)
        
        # --- 核心计算：已实现利润与最高持仓占用 ---
        realized_profit = 0.0
        max_occupied_amount = 0.0
        current_occupied_amount = 0.0
        
        buy_pool = []  # 存储买入单：{'price': p, 'qty': q}
        sell_pool = [] # 存储卖空单：{'price': p, 'qty': q}
        
        net_q = 0
        total_cost_basis = 0.0
        
        for _, t in s_df.iterrows():
            price = t['price']
            qty = t['quantity']
            
            if t['action'] == '买入':
                # 1. 检查是否有卖空单需要回补（平仓卖空）
                remaining_to_buy = qty
                # 利润最大化原则：回补卖空时，优先回补价格最高的卖空单（利润更大）
                while remaining_to_buy > 0 and sell_pool:
                    sell_pool.sort(key=lambda x: x['price'], reverse=True) # 价格最高优先
                    sp = sell_pool[0]
                    match_q = min(remaining_to_buy, sp['qty'])
                    realized_profit += (sp['price'] - price) * match_q
                    sp['qty'] -= match_q
                    remaining_to_buy -= match_q
                    if sp['qty'] <= 0: sell_pool.pop(0)
                
                # 2. 剩余部分作为买入开仓
                if remaining_to_buy > 0:
                    buy_pool.append({'price': price, 'qty': remaining_to_buy})
                
                net_q += qty
            else: # 卖出
                # 1. 检查是否有买入单需要平仓
                remaining_to_sell = qty
                # 利润最大化原则：卖出时，优先平仓价格最低的买入单（利润更大）
                while remaining_to_sell > 0 and buy_pool:
                    buy_pool.sort(key=lambda x: x['price']) # 价格最低优先
                    bp = buy_pool[0]
                    match_q = min(remaining_to_sell, bp['qty'])
                    realized_profit += (price - bp['price']) * match_q
                    bp['qty'] -= match_q
                    remaining_to_sell -= match_q
                    if bp['qty'] <= 0: buy_pool.pop(0)
                
                # 2. 剩余部分作为卖空开仓
                if remaining_to_sell > 0:
                    sell_pool.append({'price': price, 'qty': remaining_to_sell})
                
                net_q -= qty
            
            # 计算当前占用金额 (所有未平仓单的成本总额)
            current_occupied_amount = sum(x['price'] * x['qty'] for x in buy_pool) + sum(x['price'] * x['qty'] for x in sell_pool)
            max_occupied_amount = max(max_occupied_amount, current_occupied_amount)

        # 当前持仓成本价（直接调用手动录入成本）与盈亏
        avg_cost = manual_costs.get(selected_stock, 0.0)
        if net_q > 0: # 净买入持仓
            holding_profit_amount = (now_p - avg_cost) * net_q
            holding_profit_pct = (now_p - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        elif net_q < 0: # 净卖空持仓
            abs_q = abs(net_q)
            holding_profit_amount = (avg_cost - now_p) * abs_q
            holding_profit_pct = (avg_cost - now_p) / avg_cost * 100 if avg_cost > 0 else 0
        else:
            holding_profit_amount = 0.0
            holding_profit_pct = 0.0

        # 读取手动录入数据
        # --- 安全读取策略数据 (防止 OperationalError 和 NameError) ---
        strategy_df = pd.read_sql("SELECT * FROM strategy_notes WHERE code = ?", conn, params=(selected_stock,))
        if not strategy_df.empty:
            s_row = strategy_df.iloc[0]
            saved_logic = s_row.get('logic', "")
            saved_annual = s_row.get('annual_return', 0.0)
            s_buy_base = s_row.get('buy_base_price', 0.0)
            s_buy_drop = s_row.get('buy_drop_pct', 0.0)
            s_sell_base = s_row.get('sell_base_price', 0.0)
            s_sell_rise = s_row.get('sell_rise_pct', 0.0)
            saved_buy_logic = s_row.get('buy_logic', "")
            saved_sell_logic = s_row.get('sell_logic', "")
        else:
            saved_logic = ""
            saved_annual = 0.0
            s_buy_base = 0.0
            s_buy_drop = 0.0
            s_sell_base = 0.0
            s_sell_rise = 0.0
            saved_buy_logic = ""
            saved_sell_logic = "" 

        # --- 第一区：核心指标卡片 ---
        st.subheader(f"📊 {selected_stock} 核心数据概览")

        # --- 1. 数据准备 ---
        # 计算监控价与状态
        buy_monitor_p = s_buy_base * (1 - s_buy_drop / 100) if s_buy_base > 0 else 0
        sell_monitor_p = s_sell_base * (1 + s_sell_rise / 100) if s_sell_base > 0 else 0
        is_buy_triggered = (s_buy_base > 0 and now_p <= buy_monitor_p)
        is_sell_triggered = (s_sell_base > 0 and now_p >= sell_monitor_p)
        
        # 获取涨跌周期平均值
        cycles_data = pd.read_sql("SELECT change_pct FROM price_cycles WHERE code = ?", conn, params=(selected_stock,))
        up_avg = cycles_data[cycles_data['change_pct'] > 0]['change_pct'].mean() if not cycles_data.empty else 0
        down_avg = cycles_data[cycles_data['change_pct'] < 0]['change_pct'].mean() if not cycles_data.empty else 0

        # --- 2. 3行x4列 固定网格展示 ---
        # 第一行：持仓基础数据
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        r1c1.metric("持仓数量", f"{net_q}")
        r1c2.metric("持仓市值", f"{abs(net_q) * now_p:,.2f}")
        r1c3.metric("成本价", f"{avg_cost:.3f}")
        r1c4.metric("当前现价", f"{now_p:.3f}")

        # 第二行：盈亏与收益
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        p_color = "normal" if holding_profit_amount >= 0 else "inverse"
        r2c1.metric("持仓盈亏额", f"{holding_profit_amount:,.2f}", delta=f"{holding_profit_pct:.2f}%", delta_color=p_color)
        r2c2.metric("已实现利润", f"{realized_profit:,.2f}")
        r2c3.metric("最高占用金额", f"{max_occupied_amount:,.2f}")
        r2c4.metric("历史年化收益", f"{saved_annual:.2f}%")

        # 第三行：监控与涨跌幅 (固定位置)
        r3c1, r3c2, r3c3, r3c4 = st.columns(4)
        
        # 买入监控价
        if s_buy_base > 0:
            b_label = "🔴 买入监控 (达标)" if is_buy_triggered else "📥 买入监控 (观察)"
            r3c1.metric(b_label, f"{buy_monitor_p:.3f}")
        else:
            r3c1.metric("📥 买入监控", "未设置")
            
        # 卖出监控价
        if s_sell_base > 0:
            s_label = "🔴 卖出监控 (达标)" if is_sell_triggered else "📤 卖出监控 (观察)"
            r3c2.metric(s_label, f"{sell_monitor_p:.3f}")
        else:
            r3c2.metric("📤 卖出监控", "未设置")

        # 平均涨跌幅 (强制显示)
        r3c3.metric("📈 平均涨幅", f"{up_avg:.2f}%" if not pd.isna(up_avg) else "0.00%")
        r3c4.metric("📉 平均跌幅", f"{down_avg:.2f}%" if not pd.isna(down_avg) else "0.00%")
        # --- 左右并排逻辑展示 ---
        if saved_buy_logic or saved_sell_logic:
            lc1, lc2 = st.columns(2)
            if saved_buy_logic:
                lc1.markdown(f"""
                <div style="background: rgba(0, 0, 0, 0.4); border-radius: 12px; padding: 20px; border-left: 8px solid #00C49F; margin-top: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
                    <h4 style="margin-top:0; color:#00C49F; font-size:1.1em; font-weight:bold; margin-bottom:10px;">🟢 买入原则</h4>
                    <div style="white-space: pre-wrap; font-size: 1.0em; color:#FFFFFF; font-weight: 500; line-height: 1.5;">{saved_buy_logic}</div>
                </div>
                """, unsafe_allow_html=True)
            if saved_sell_logic:
                lc2.markdown(f"""
                <div style="background: rgba(0, 0, 0, 0.4); border-radius: 12px; padding: 20px; border-left: 8px solid #FF4B4B; margin-top: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
                    <h4 style="margin-top:0; color:#FF4B4B; font-size:1.1em; font-weight:bold; margin-bottom:10px;">🔴 卖出原则</h4>
                    <div style="white-space: pre-wrap; font-size: 1.0em; color:#FFFFFF; font-weight: 500; line-height: 1.5;">{saved_sell_logic}</div>
                </div>
                """, unsafe_allow_html=True)

        st.divider()

        # --- 第二区：交易逻辑与决策历史 ---
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
                    c.execute("""
                        INSERT OR REPLACE INTO strategy_notes 
                        (code, logic, max_holding_amount, annual_return, buy_base_price, buy_drop_pct, sell_base_price, sell_rise_pct, buy_logic, sell_logic) 
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (selected_stock, saved_logic, max_occupied_amount, new_annual, new_buy_base, new_buy_drop, new_sell_base, new_sell_rise, new_buy_logic, new_sell_logic))
                    conn.commit()
                    st.success("已保存")
                    st.rerun()
            


        with col_right:
            st.subheader("📜 决策历史记录")
            with st.expander("➕ 新增决策记录"):
                with st.form("new_decision", clear_on_submit=True):
                    d_date = st.date_input("日期", datetime.now())
                    d_content = st.text_input("决策内容", placeholder="例如：减仓30%")
                    d_reason = st.text_area("决策原因", placeholder="为什么做这个决策？")
                    if st.form_submit_button("记录决策"):
                        c.execute("INSERT INTO decision_history (code, date, decision, reason) VALUES (?,?,?,?)", 
                                  (selected_stock, d_date.strftime('%Y-%m-%d'), d_content, d_reason))
                        conn.commit()
                        st.rerun()
            
            # 决策历史列表与删除
            decisions = pd.read_sql("SELECT id, date, decision, reason FROM decision_history WHERE code = ? ORDER BY date DESC", conn, params=(selected_stock,))
            for _, row in decisions.iterrows():
                with st.container(border=True):
                    head_col, del_col = st.columns([9, 1])
                    head_col.markdown(f"**{row['date']} | {row['decision']}**")
                    if del_col.button("🗑️", key=f"del_dec_{row['id']}"):
                        c.execute("DELETE FROM decision_history WHERE id = ?", (row['id'],))
                        conn.commit()
                        st.rerun()
                    st.caption(row['reason'])

        st.divider()

        # --- 第三区：涨跌周期管理 ---
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
                    st.rerun()
        
        with cycle_list:
            cycles = pd.read_sql("SELECT id, start_date, end_date, change_pct FROM price_cycles WHERE code = ? ORDER BY start_date DESC", conn, params=(selected_stock,))
            if not cycles.empty:
                up_avg = cycles[cycles['change_pct'] > 0]['change_pct'].mean()
                down_avg = cycles[cycles['change_pct'] < 0]['change_pct'].mean()
                st.markdown(f"📈 **平均涨幅:** `{up_avg:.2f}%` | 📉 **平均跌幅:** `{down_avg:.2f}%`")
                
                # 周期表格展示
                for _, row in cycles.iterrows():
                    c_col, d_col = st.columns([8, 2])
                    color = "#d32f2f" if row['change_pct'] > 0 else "#388e3c"
                    c_col.markdown(f"`{row['start_date']} → {row['end_date']}` <span style='color:{color}; font-weight:bold;'>({row['change_pct']:+.2f}%)</span>", unsafe_allow_html=True)
                    if d_col.button("删除", key=f"del_cyc_{row['id']}"):
                        c.execute("DELETE FROM price_cycles WHERE id = ?", (row['id'],))
                        conn.commit()
                        st.rerun()
            else:
                st.info("暂无涨跌周期记录")
    else:
        st.info("请先在交易录入中添加股票数据")

elif choice == "📊 实时持仓":
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
                    thread = threading.Thread(target=sync_db_to_github, daemon=True)
                    thread.start()
       
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
                                "type": "✅ 已配对交易对",
                                "price": f"{format_number(sp['price'])} → {format_number(price)}",
                                "qty": cover_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
                            })
                            # 更新持仓数量
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        # 清理已耗尽的卖空持仓（数量为0的移除）
                        sell_positions = [sp for sp in sell_positions if sp['qty'] > 0]

                    # 步骤2：剩余买入量加入正向持仓池（成为未平仓买入）
                    if remaining > 0:
                        buy_positions.append({
                            'date': trade_date,
                            'price': price,
                            'qty': remaining
                        })

                elif action == '卖出':
                    # 步骤1：先平仓正向持仓（低价买入单优先平仓，锁定低价盈利）
                    if buy_positions and remaining > 0:
                        # 买入单按价格从低到高排序，低价优先平仓
                        for bp in sorted(buy_positions, key=lambda x: x['price']):
                            if remaining <= 0:
                                break
                            if bp['qty'] <= 0:
                                continue
                            # 计算平仓数量（取剩余卖出量和买入单量的最小值）
                            close_qty = min(bp['qty'], remaining)
                            # 计算平仓的盈亏比例
                            gain = ((price - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                            # 记录配对交易对
                            paired_trades.append({
                                "date": f"{bp['date']} → {trade_date}",
                                "code": stock,
                                "type": "✅ 已配对交易对",
                                "price": f"{format_number(bp['price'])} → {format_number(price)}",
                                "qty": close_qty,
                                "gain_str": f"{gain:.2f}%",
                                "gain_val": gain
                            })
                            # 更新持仓数量
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        # 清理已耗尽的正向持仓（数量为0的移除）
                        buy_positions = [bp for bp in buy_positions if bp['qty'] > 0]

                    # 步骤2：剩余卖出量加入卖空持仓池（无正向持仓时，记为卖空开仓）
                    if remaining > 0:
                        sell_positions.append({
                            'date': trade_date,
                            'price': price,
                            'qty': remaining
                        })

            # 收集未平仓的正向持仓（买入持有）
            for bp in buy_positions:
                float_gain = ((now_p - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": bp['date'],
                    "code": stock,
                    "type": "🔴 买入持有",
                    "price": format_number(bp['price']),
                    "qty": bp['qty'],
                    "gain_str": f"{float_gain:.2f}%",
                    "gain_val": float_gain
                })

            # 收集未平仓的卖空持仓（卖空持有）
            for sp in sell_positions:
                float_gain = ((sp['price'] - now_p) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": sp['date'],
                    "code": stock,
                    "type": "🟢 卖空持有",
                    "price": format_number(sp['price']),
                    "qty": sp['qty'],
                    "gain_str": f"{float_gain:.2f}%",
                    "gain_val": float_gain
                })

            # 已配对交易对优先显示，拼接到列表头部
            all_active_records = paired_trades + all_active_records
            # ---------------------------------------------------------------------------------
       
        # 显示账户持仓概览
        st.subheader("1️⃣ 账户持仓概览 (手动成本模式)")
        if summary:
            # 按盈亏比例倒序排序
            summary.sort(key=lambda x: x[5], reverse=True)
            html = '<table class="custom-table"><thead><tr><th>股票代码</th><th>净持仓</th><th>手动成本</th><th>现价</th><th>盈亏比例</th></tr></thead><tbody>'
            for r in summary:
                # 盈利红色，亏损绿色
                c_class = "profit-red" if r[5] > 0 else "loss-green" if r[5] < 0 else ""
                html += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td class="{c_class}">{r[4]}</td></tr>'
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("📌 目前账户无任何净持仓")
       
        # 显示交易配对与未平仓明细
        st.write("---")
        st.subheader("2️⃣ 交易配对与未平仓单 (严格时间流)")
      
        # 筛选条件
        with st.expander("🔍 筛选条件", expanded=False):
            col1, col2, col3 = st.columns(3)
            stock_filter = col1.text_input("筛选股票", placeholder="输入股票代码/名称")
            min_gain = col2.number_input("最小盈亏(%)", value=-100.0, step=0.1)
            max_gain = col3.number_input("最大盈亏(%)", value=100.0, step=0.1)
            trade_type = st.selectbox("交易类型筛选", ["全部", "✅ 已配对交易对", "🔴 买入持有", "🟢 卖空持有"], index=0)
      
        # 应用筛选逻辑
        filtered_records = all_active_records.copy()
        if stock_filter:
            filtered_records = [r for r in filtered_records if stock_filter.lower() in r["code"].lower()]
        if not (min_gain == -100 and max_gain == 100):
            filtered_records = [r for r in filtered_records if min_gain <= r['gain_val'] <= max_gain]
        if trade_type != "全部":
            filtered_records = [r for r in filtered_records if r["type"] == trade_type]
      
        # 显示筛选后的明细
        if filtered_records:
            # 排序选项
            sort_option = st.selectbox("排序方式", ["盈亏降序", "盈亏升序", "日期降序", "日期升序"], index=0)
            if sort_option == "盈亏降序":
                filtered_records.sort(key=lambda x: x['gain_val'], reverse=True)
            elif sort_option == "盈亏升序":
                filtered_records.sort(key=lambda x: x['gain_val'])
            elif sort_option == "日期降序":
                filtered_records.sort(key=lambda x: x['date'], reverse=True)
            elif sort_option == "日期升序":
                filtered_records.sort(key=lambda x: x['date'])
          
            # 渲染明细表格
            html = '<table class="custom-table"><thead><tr><th>交易时间</th><th>股票</th><th>交易类型</th><th>成交价格</th><th>数量</th><th>盈亏百分比</th></tr></thead><tbody>'
            for r in filtered_records:
                c_class = "profit-red" if r['gain_val'] > 0 else "loss-green" if r['gain_val'] < 0 else ""
                html += f'<tr><td>{r["date"]}</td><td>{r["code"]}</td><td>{r["type"]}</td><td>{r["price"]}</td><td>{r["qty"]}</td><td class="{c_class}">{r["gain_str"]}</td></tr>'
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("📌 暂无符合条件的交易记录/持仓")
    else:
        st.info("📌 交易数据库为空，请先录入交易记录")

# --- 盈利账单 ---
elif choice == "💰 盈利账单":
    st.header("💰 盈利账单 (总额对冲法)")
    df_trades = pd.read_sql("SELECT * FROM trades", conn)
    latest_prices_data = {row[0]: (row[1], row[2]) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}
    manual_costs = {k: v[1] for k, v in latest_prices_data.items()}
  
    if not df_trades.empty:
        profit_list = []
        for stock in df_trades['code'].unique():
            s_df = df_trades[df_trades['code'] == stock]
            now_p = latest_prices.get(stock, 0.0)
            total_buy_cash = s_df[s_df['action'] == '买入'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            total_sell_cash = s_df[s_df['action'] == '卖出'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            net_q = s_df[s_df['action'] == '买入']['quantity'].sum() - s_df[s_df['action'] == '卖出']['quantity'].sum()
            current_value = net_q * now_p if net_q > 0 else 0
            total_profit = (total_sell_cash + current_value) - total_buy_cash
            profit_list.append({"股票名称": stock, "累计投入": total_buy_cash, "累计回收": total_sell_cash, "持仓市值": current_value, "总盈亏": total_profit})
        pdf = pd.DataFrame(profit_list).sort_values(by="总盈亏", ascending=False)
        st.metric("账户总体贡献", f"{pdf['总盈亏'].sum():,.2f}")
      
        html = '<table class="custom-table"><thead><tr><th>股票名称</th><th>累计投入</th><th>累计回收</th><th>持仓市值</th><th>总盈亏</th></tr></thead><tbody>'
        for _, r in pdf.iterrows():
            c_class = "profit-red" if r['总盈亏'] > 0 else "loss-green" if r['总盈亏'] < 0 else ""
            html += f"<tr><td>{r['股票名称']}</td><td>{r['累计投入']:,.2f}</td><td>{r['累计回收']:,.2f}</td><td>{r['持仓市值']:,.2f}</td><td class='{c_class}'>{r['总盈亏']:,.2f}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)

# --- 价格目标管理 ---
elif choice == "🎯 价格目标管理":
    st.markdown("## 🎯 价格目标管理")

    # ========== 数据库表结构升级 ==========
    def ensure_price_target_v2_table():
        c.execute("CREATE TABLE IF NOT EXISTS price_targets_v2 (code TEXT PRIMARY KEY, buy_high_point REAL, buy_drop_pct REAL, buy_break_status TEXT DEFAULT '未突破', buy_low_after_break REAL, sell_low_point REAL, sell_rise_pct REAL, sell_break_status TEXT DEFAULT '未突破', sell_high_after_break REAL, last_updated TEXT)")
        conn.commit()

    ensure_price_target_v2_table()

    # ========== 辅助函数 ==========
    def get_current_price(stock_code):
        result = c.execute("SELECT current_price FROM prices WHERE code = ?", (stock_code,)).fetchone()
        return float(result[0]) if result and result[0] else 0.0

    def save_price_target_v2(code, data):
        c.execute("INSERT OR REPLACE INTO price_targets_v2 (code, buy_high_point, buy_drop_pct, buy_break_status, buy_low_after_break, sell_low_point, sell_rise_pct, sell_break_status, sell_high_after_break, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, data.get('buy_high_point'), data.get('buy_drop_pct'), data.get('buy_break_status', '未突破'), data.get('buy_low_after_break'),
             data.get('sell_low_point'), data.get('sell_rise_pct'), data.get('sell_break_status', '未突破'), data.get('sell_high_after_break'),
             datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit()
        thread = threading.Thread(target=sync_db_to_github, daemon=True)
        thread.start()

    def load_price_target_v2(code):
        row = c.execute('SELECT * FROM price_targets_v2 WHERE code = ?', (code,)).fetchone()
        if row:
            return {'code': row[0], 'buy_high_point': row[1], 'buy_drop_pct': row[2], 'buy_break_status': row[3] or '未突破', 'buy_low_after_break': row[4],
                    'sell_low_point': row[5], 'sell_rise_pct': row[6], 'sell_break_status': row[7] or '未突破', 'sell_high_after_break': row[8]}
        return None

    def delete_price_target_v2(code):
        c.execute('DELETE FROM price_targets_v2 WHERE code = ?', (code,))
        conn.commit()
        thread = threading.Thread(target=sync_db_to_github, daemon=True)
        thread.start()

    # ========== 核心计算函数 ==========
    def calc_buy_target(config, current_price):
        result = {'base_price': None, 'cycle_drop': None, 'buy_target': None, 'rebound_pct': None, 'to_target_pct': None}
        high_point = config.get('buy_high_point')
        drop_pct = config.get('buy_drop_pct')
        if not high_point or not drop_pct:
            return result
        result['base_price'] = round(high_point * (1 - drop_pct / 100), 3)
        if config.get('buy_break_status') == '已突破':
            low_after_break = config.get('buy_low_after_break')
            if low_after_break:
                result['cycle_drop'] = round(high_point - low_after_break, 3)
                result['buy_target'] = round(low_after_break + result['cycle_drop'] * 0.382, 3)
                result['rebound_pct'] = round((result['buy_target'] - low_after_break) / low_after_break * 100, 2)
                if current_price > 0 and result['buy_target']:
                    result['to_target_pct'] = round((result['buy_target'] - current_price) / current_price * 100, 2)
        return result

    def calc_sell_target(config, current_price):
        result = {'base_price': None, 'cycle_rise': None, 'sell_target': None, 'fallback_pct': None, 'to_target_pct': None}
        low_point = config.get('sell_low_point')
        rise_pct = config.get('sell_rise_pct')
        if not low_point or not rise_pct:
            return result
        result['base_price'] = round(low_point * (1 + rise_pct / 100), 3)
        if config.get('sell_break_status') == '已突破':
            high_after_break = config.get('sell_high_after_break')
            if high_after_break:
                result['cycle_rise'] = round(high_after_break - low_point, 3)
                result['sell_target'] = round(high_after_break - result['cycle_rise'] * 0.618, 3)
                result['fallback_pct'] = round((high_after_break - result['sell_target']) / high_after_break * 100, 2)
                if current_price > 0 and result['sell_target']:
                    result['to_target_pct'] = round((current_price - result['sell_target']) / result['sell_target'] * 100, 2)
        return result

    # ========== 获取所有监控数据 ==========
    all_stocks = get_dynamic_stock_list()
    all_configs = c.execute("SELECT * FROM price_targets_v2 WHERE buy_high_point IS NOT NULL OR sell_low_point IS NOT NULL").fetchall()

    # 构建监控列表数据
    monitor_items = []
    for row in all_configs:
        code = row[0]
        buy_config = {'buy_high_point': row[1], 'buy_drop_pct': row[2], 'buy_break_status': row[3], 'buy_low_after_break': row[4]}
        sell_config = {'sell_low_point': row[5], 'sell_rise_pct': row[6], 'sell_break_status': row[7], 'sell_high_after_break': row[8]}
        curr_price = get_current_price(code)

        # 买入体系
        if buy_config['buy_high_point'] and buy_config['buy_drop_pct']:
            buy_calc = calc_buy_target(buy_config, curr_price)
            if buy_config['buy_break_status'] == '已突破' and buy_calc['buy_target']:
                monitor_items.append({
                    'code': code,
                    'type': '买入',
                    'trend': '反弹中',
                    'target_price': buy_calc['buy_target'],
                    'current_price': curr_price,
                    'to_target_pct': buy_calc['to_target_pct'],
                    'break_status': '已突破'
                })
            elif buy_config['buy_break_status'] == '未突破':
                monitor_items.append({
                    'code': code,
                    'type': '买入',
                    'trend': '等待突破',
                    'target_price': buy_calc['base_price'],
                    'current_price': curr_price,
                    'to_target_pct': round((buy_calc['base_price'] - curr_price) / curr_price * 100, 2) if curr_price > 0 else None,
                    'break_status': '未突破'
                })

        # 卖出体系
        if sell_config['sell_low_point'] and sell_config['sell_rise_pct']:
            sell_calc = calc_sell_target(sell_config, curr_price)
            if sell_config['sell_break_status'] == '已突破' and sell_calc['sell_target']:
                monitor_items.append({
                    'code': code,
                    'type': '卖出',
                    'trend': '回调中',
                    'target_price': sell_calc['sell_target'],
                    'current_price': curr_price,
                    'to_target_pct': sell_calc['to_target_pct'],
                    'break_status': '已突破'
                })
            elif sell_config['sell_break_status'] == '未突破':
                monitor_items.append({
                    'code': code,
                    'type': '卖出',
                    'trend': '等待突破',
                    'target_price': sell_calc['base_price'],
                    'current_price': curr_price,
                    'to_target_pct': round((sell_calc['base_price'] - curr_price) / curr_price * 100, 2) if curr_price > 0 else None,
                    'break_status': '未突破'
                })

    # ========== 1. 主要监控窗口（醒目卡片）==========
    st.subheader("📊 实时监控")

    if monitor_items:
        # 按距离目标百分比排序（绝对值小的在前）
        monitor_items.sort(key=lambda x: abs(x['to_target_pct']) if x['to_target_pct'] is not None else float('inf'))

        # 每行显示3个卡片
        cols_per_row = 3
        for i in range(0, len(monitor_items), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, item in enumerate(monitor_items[i:i+cols_per_row]):
                with cols[j]:
                    is_buy = item['type'] == '买入'
                    color = "#22c55e" if is_buy else "#ef4444"  # 绿色买入，红色卖出
                    trend_color = "#3b82f6" if item['trend'] == '等待突破' else color

                    # 距离百分比显示
                    if item['to_target_pct'] is not None:
                        if item['to_target_pct'] > 0:
                            pct_text = f"还差 {item['to_target_pct']:.2f}%"
                        else:
                            pct_text = f"已超出 {abs(item['to_target_pct']):.2f}%"
                    else:
                        pct_text = "-"

                    # 突破状态标签
                    break_badge = "🟢" if item['break_status'] == '已突破' else "⏳"

                    st.markdown(f"""
                    <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-radius: 12px; padding: 16px; margin-bottom: 12px; border-left: 4px solid {color}; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            <span style="font-size: 1.2em; font-weight: bold; color: #f8fafc;">{item['code']}</span>
                            <span style="background: {color}; color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600;">{item['type']}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                            <span style="color: #94a3b8; font-size: 0.9em;">趋势:</span>
                            <span style="color: {trend_color}; font-weight: 600;">{break_badge} {item['trend']}</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                            <span style="color: #94a3b8; font-size: 0.9em;">目标价:</span>
                            <span style="color: #f8fafc; font-size: 1.4em; font-weight: bold;">{item['target_price']:.3f}</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                            <span style="color: #94a3b8; font-size: 0.9em;">当前价:</span>
                            <span style="color: #cbd5e1; font-size: 1.1em;">{f"{item['current_price']:.3f}" if item['current_price'] > 0 else "未设置"}</span>
                        </div>
                        <div style="background: rgba(255,255,255,0.05); border-radius: 8px; padding: 8px 12px; text-align: center;">
                            <span style="color: #fbbf24; font-size: 1.1em; font-weight: bold;">📊 {pct_text}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
    else:
        st.info("📌 暂无价格目标监控，请在下方配置")

    st.divider()

    # ========== 2. 配置区域（展开/折叠）==========
    with st.expander("⚙️ 配置价格目标", expanded=False):
        all_stocks = get_dynamic_stock_list()
        selected_stock = st.selectbox("📌 选择股票", [""] + all_stocks, key="pt_stock_select")

        if selected_stock:
            current_price = get_current_price(selected_stock)
            existing_config = load_price_target_v2(selected_stock) or {'buy_high_point': None, 'buy_drop_pct': None, 'buy_break_status': '未突破', 'buy_low_after_break': None, 'sell_low_point': None, 'sell_rise_pct': None, 'sell_break_status': '未突破', 'sell_high_after_break': None}

            st.markdown(f"**当前股票:** `{selected_stock}`　　**当前价格:** `{current_price:.3f}" if current_price > 0 else "未设置" + "`")

            col_buy, col_sell = st.columns(2)

            # 买入体系配置
            with col_buy:
                st.markdown("#### 🔴 买入价体系（前期高点下跌）")
                with st.container(border=True):
                    buy_high = st.number_input("前期高点价位", value=float(existing_config['buy_high_point']) if existing_config.get('buy_high_point') else None, step=0.001, format="%.3f", key="buy_high_point")
                    buy_drop = st.number_input("下跌幅度 (%)", value=float(existing_config['buy_drop_pct']) if existing_config.get('buy_drop_pct') else None, step=0.1, format="%.2f", key="buy_drop_pct")
                    buy_break = st.selectbox("突破基准价状态", options=["未突破", "已突破"], index=0 if existing_config.get('buy_break_status') != '已突破' else 1, key="buy_break_status")
                    buy_low_after = None
                    if buy_break == "已突破":
                        buy_low_after = st.number_input("突破后最低价", value=float(existing_config['buy_low_after_break']) if existing_config.get('buy_low_after_break') else None, step=0.001, format="%.3f", key="buy_low_after_break")

            # 卖出体系配置
            with col_sell:
                st.markdown("#### 🟢 卖出价体系（前期低点上涨）")
                with st.container(border=True):
                    sell_low = st.number_input("前期低点价位", value=float(existing_config['sell_low_point']) if existing_config.get('sell_low_point') else None, step=0.001, format="%.3f", key="sell_low_point")
                    sell_rise = st.number_input("上涨幅度 (%)", value=float(existing_config['sell_rise_pct']) if existing_config.get('sell_rise_pct') else None, step=0.1, format="%.2f", key="sell_rise_pct")
                    sell_break = st.selectbox("突破基准价状态", options=["未突破", "已突破"], index=0 if existing_config.get('sell_break_status') != '已突破' else 1, key="sell_break_status")
                    sell_high_after = None
                    if sell_break == "已突破":
                        sell_high_after = st.number_input("突破后最高价", value=float(existing_config['sell_high_after_break']) if existing_config.get('sell_high_after_break') else None, step=0.001, format="%.3f", key="sell_high_after_break")

            # 保存按钮
            col_save, col_delete = st.columns([1, 1])
            with col_save:
                if st.button("💾 保存配置", type="primary"):
                    config_data = {'buy_high_point': buy_high, 'buy_drop_pct': buy_drop, 'buy_break_status': buy_break, 'buy_low_after_break': buy_low_after, 'sell_low_point': sell_low, 'sell_rise_pct': sell_rise, 'sell_break_status': sell_break, 'sell_high_after_break': sell_high_after}
                    save_price_target_v2(selected_stock, config_data)
                    st.success("✅ 配置已保存")
                    st.rerun()
            with col_delete:
                if st.button("🗑️ 删除配置", type="secondary"):
                    delete_price_target_v2(selected_stock)
                    st.warning("⚠️ 配置已删除")
                    st.rerun()
        else:
            st.info("👆 请选择要配置的股票")

    st.divider()

    # ========== 3. 详细数据窗口（普通表格）==========
    st.subheader("📋 监控参数详情")

    if all_configs:
        detail_data = []
        for row in all_configs:
            code, b_high, b_drop, b_break, b_low, s_low, s_rise, s_break, s_high, _ = row
            curr_p = get_current_price(code)

            # 买入体系详情
            if b_high and b_drop:
                buy_base = round(b_high * (1 - b_drop / 100), 3)
                rebound_pct = '-'
                if b_break == '已突破' and b_low:
                    cycle_drop = round(b_high - b_low, 3)
                    buy_target = round(b_low + cycle_drop * 0.382, 3)
                    rebound_pct = round((buy_target - b_low) / b_low * 100, 2) if b_low > 0 else '-'
                    to_target = round((buy_target - curr_p) / curr_p * 100, 2) if curr_p > 0 else None
                else:
                    buy_target = '-'
                    to_target = round((buy_base - curr_p) / curr_p * 100, 2) if curr_p > 0 else None

                detail_data.append({
                    '股票': code, '体系': '买入', '突破状态': b_break,
                    '前期高点': b_high, '下跌幅度': f"{b_drop:.2f}%", '基准价': buy_base,
                    '突破后极值': b_low if b_low else '-', '目标价': buy_target,
                    '当前价': curr_p if curr_p > 0 else '-',
                    '距离目标': f"{to_target:.2f}%" if to_target is not None else '-',
                    '反弹值': f"{rebound_pct:.2f}%" if rebound_pct != '-' else '-',
                    '回落值': '-'
                })

            # 卖出体系详情
            if s_low and s_rise:
                sell_base = round(s_low * (1 + s_rise / 100), 3)
                fallback_pct = '-'
                if s_break == '已突破' and s_high:
                    cycle_rise = round(s_high - s_low, 3)
                    sell_target = round(s_high - cycle_rise * 0.618, 3)
                    fallback_pct = round((s_high - sell_target) / s_high * 100, 2) if s_high > 0 else '-'
                    to_target = round((curr_p - sell_target) / sell_target * 100, 2) if curr_p > 0 else None
                else:
                    sell_target = '-'
                    to_target = round((sell_base - curr_p) / curr_p * 100, 2) if curr_p > 0 else None

                detail_data.append({
                    '股票': code, '体系': '卖出', '突破状态': s_break,
                    '前期低点': s_low, '上涨幅度': f"{s_rise:.2f}%", '基准价': sell_base,
                    '突破后极值': s_high if s_high else '-', '目标价': sell_target,
                    '当前价': curr_p if curr_p > 0 else '-',
                    '距离目标': f"{to_target:.2f}%" if to_target is not None else '-',
                    '反弹值': '-',
                    '回落值': f"{fallback_pct:.2f}%" if fallback_pct != '-' else '-'
                })

        if detail_data:
            # 美化成HTML表格
            html = '<table class="custom-table"><thead><tr><th>股票</th><th>体系</th><th>突破状态</th><th>前期极值</th><th>幅度(%)</th><th>基准价</th><th>突破后极值</th><th>目标价</th><th>当前价</th><th>距离目标(%)</th><th>反弹值(%)</th><th>回落值(%)</th></tr></thead><tbody>'
            for item in detail_data:
                # 根据体系调整列显示（前期极值和幅度根据体系不同）
                pre_extreme = item['前期高点'] if item['体系'] == '买入' else item['前期低点']
                amplitude = item['下跌幅度'] if item['体系'] == '买入' else item['上涨幅度']
                html += f"<tr><td>{item['股票']}</td><td>{item['体系']}</td><td>{item['突破状态']}</td><td>{pre_extreme}</td><td>{amplitude}</td><td>{item['基准价']}</td><td>{item['突破后极值']}</td><td>{item['目标价']}</td><td>{item['当前价']}</td><td>{item['距离目标']}</td><td>{item['反弹值']}</td><td>{item['回落值']}</td></tr>"
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("暂无有效配置数据")
    else:
        st.info("暂无价格目标配置")

# --- 交易录入 ---
elif choice == "📝 交易录入":
    st.header("📝 交易录入")
    full_list = get_dynamic_stock_list()
    t_code = st.selectbox("选择股票", options=["【添加新股票】"] + full_list, index=None)
    final_code = st.text_input("新股票名（必填）") if t_code == "【添加新股票】" else t_code
    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        d = c1.date_input("日期", datetime.now())
        a = c2.selectbox("操作", ["买入", "卖出"])
       
        p = c1.number_input("单价", value=None, min_value=0.0, step=0.001, format="%.3f")
        q = c2.number_input("数量", value=None, min_value=1, step=1)
       
        note = st.text_input("备注（可选）", placeholder="例如：突破20日均线买入、分红除权、止盈卖出等")
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
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("交易记录已保存！")
                st.rerun()

# --- 买卖信号 ---
elif choice == "🔔 买卖信号":
    st.header("🔔 策略监控信号")
    
    # 新增：动态格式化数字函数（去除末尾无意义的0）
    def format_number(num):
        """动态格式化数字，保留有效小数位，去除末尾无意义的0"""
        if pd.isna(num) or num is None or num == 0:
            return "0"
        formatted = f"{num}".rstrip('0').rstrip('.') if '.' in f"{num}" else f"{num}"
        return formatted
  
    with st.expander("➕ 设置新监控"):
        existing_signals = pd.read_sql("SELECT code FROM signals", conn)['code'].tolist()
        s_code = st.selectbox("监控股票", options=get_dynamic_stock_list(), index=None)
      
        signal_data = None
        if s_code and s_code in existing_signals:
            signal_data = c.execute("""
                SELECT high_point, low_point, up_threshold, down_threshold, high_date, low_date
                FROM signals WHERE code = ?
            """, (s_code,)).fetchone()
      
        c1, c2 = st.columns(2)
        # 修改1：调小输入步长到0.0001，支持更多小数位输入（无format限制）
        s_high = c1.number_input("高点参考价", value=float(signal_data[0]) if signal_data else None, step=0.0001)
        h_date = c1.date_input("高点日期", value=datetime.strptime(signal_data[4], '%Y-%m-%d').date() if signal_data and signal_data[4] else datetime.now())
      
        s_low = c2.number_input("低点参考价", value=float(signal_data[1]) if signal_data else None, step=0.0001)
        l_date = c2.date_input("低点日期", value=datetime.strptime(signal_data[5], '%Y-%m-%d').date() if signal_data and signal_data[5] else datetime.now())
      
        # 百分比输入框也支持更多小数位（可选，保持原有逻辑也可以）
        s_up = c1.number_input("上涨触发 (%)", value=float(signal_data[2]) if signal_data else 20.0, step=0.01)
        s_down = c2.number_input("回调触发 (%)", value=float(signal_data[3]) if signal_data else 20.0, step=0.01)
      
        if st.button("🚀 启动/更新监控"):
            if all([s_code, s_high, s_low, s_up, s_down]):
                c.execute("""
                    INSERT OR REPLACE INTO signals
                    (code, high_point, low_point, up_threshold, down_threshold, high_date, low_date)
                    VALUES (?,?,?,?,?,?,?)
                """, (s_code, s_high, s_low, s_up, s_down,
                      h_date.strftime('%Y-%m-%d'), l_date.strftime('%Y-%m-%d')))
                conn.commit()
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("监控已更新")
                st.rerun()
   
    sig_df = pd.read_sql("SELECT * FROM signals", conn)
    prices_map = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
  
    if not sig_df.empty:
        html = '<table class="custom-table"><thead><tr><th>代码</th><th>高点(日期)</th><th>低点(日期)</th><th>距高点</th><th>距低点</th><th>建议</th></tr></thead><tbody>'
        for _, r in sig_df.iterrows():
            np = prices_map.get(r['code'], 0.0)
            dr = ((np - r['high_point']) / r['high_point'] * 100) if r['high_point'] > 0 else 0
            rr = ((np - r['low_point']) / r['low_point'] * 100) if r['low_point'] > 0 else 0
            st_text = "🟢 建议卖出" if rr >= r['up_threshold'] else "🔴 建议买入" if dr <= -r['down_threshold'] else "⚖️ 观望"
            
            # 修改2：移除:.2f，改用动态格式化函数处理高点/低点参考价
            high_point_formatted = format_number(r['high_point'])
            low_point_formatted = format_number(r['low_point'])
            
            html += f"<tr><td>{r['code']}</td><td>{high_point_formatted}<br><small>{r['high_date']}</small></td><td>{low_point_formatted}<br><small>{r['low_date']}</small></td><td>{dr:.2f}%</td><td>{rr:.2f}%</td><td>{st_text}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
      
        if st.button("🗑️ 清空所有监控"):
            c.execute("DELETE FROM signals")
            conn.commit()
            thread = threading.Thread(target=sync_db_to_github, daemon=True)
            thread.start()
            st.rerun()
    else:
        st.info("当前没有设置任何监控信号")

# --- 历史明细 ---
elif choice == "📜 历史明细":
    st.header("📜 历史交易流水")
   
    # 读取完整数据，并将 date 列转换为 datetime.date 类型
    df_full = pd.read_sql("SELECT id, date, code, action, price, quantity, note FROM trades ORDER BY date DESC, id DESC", conn)
   
    if df_full.empty:
        st.info("暂无交易记录")
    else:
        # 关键修复：将字符串日期转换为 date 对象
        df_full['date'] = pd.to_datetime(df_full['date']).dt.date
       
        # 显示部分：支持搜索筛选（仅影响显示）
        search_code = st.text_input("🔍 搜索股票代码（仅影响显示，不影响编辑）")
        df_display = df_full.copy()
        if search_code:
            df_display = df_display[df_display['code'].str.contains(search_code, case=False, na=False)]
       
        # 美化显示筛选结果
        html = '<table class="custom-table"><thead><tr><th>日期</th><th>代码</th><th>操作</th><th>价格</th><th>数量</th><th>总额</th><th>备注</th></tr></thead><tbody>'
        for _, r in df_display.iterrows():
            tag = f'<span class="profit-red">{r["action"]}</span>' if r["action"] == "买入" else f'<span class="loss-green">{r["action"]}</span>'
            note_display = r['note'] if pd.notna(r['note']) and str(r['note']).strip() else '<small style="color:#888;">无备注</small>'
            html += f"<tr><td>{r['date']}</td><td>{r['code']}</td><td>{tag}</td><td>{r['price']:.3f}</td><td>{int(r['quantity'])}</td><td>{r['price']*r['quantity']:,.2f}</td><td>{note_display}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
       
        st.warning("⚠️ 注意：下方编辑器操作的是**全部交易记录**（不受上方搜索影响），支持增删改，请谨慎操作！")
       
        # 编辑部分：使用转换后的 df_full（date 为 date 类型）
        with st.expander("🛠️ 数据库维护（编辑全部交易记录，支持增、删、改）", expanded=False):
            edited_df = st.data_editor(
                df_full,
                use_container_width=True,
                num_rows="dynamic",
                hide_index=False,
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True),
                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
                    "code": st.column_config.TextColumn("代码", required=True),
                    "action": st.column_config.SelectboxColumn("操作", options=["买入", "卖出"], required=True),
                    "price": st.column_config.NumberColumn("价格", min_value=0.0, format="%.3f", required=True),
                    "quantity": st.column_config.NumberColumn("数量", min_value=1, step=1, required=True),
                    "note": st.column_config.TextColumn("备注", width="large"),
                },
                key="trades_editor"
            )
           
            col_save, col_cancel = st.columns([1, 4])
            with col_save:
                if st.button("💾 提交所有修改", type="primary"):
                    try:
                        # 保存前：将 date 列转回字符串格式，适配数据库 TEXT 类型
                        save_df = edited_df.copy()
                        save_df['date'] = pd.to_datetime(save_df['date']).dt.strftime('%Y-%m-%d')
                       
                        # 替换整个表（现在是完整数据，安全）
                        save_df.to_sql('trades', conn, if_exists='replace', index=False)
                        conn.commit()
                        thread = threading.Thread(target=sync_db_to_github, daemon=True)
                        thread.start()
                        st.success("所有交易记录已成功更新！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"保存失败：{e}")

# --- 复盘日记 ---
elif choice == "📓 复盘日记":
    st.header("📓 复盘日记")

    # 1) 建表
    c.execute("""
        CREATE TABLE IF NOT EXISTS journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            stock_name TEXT,
            content TEXT
        )
    """)
    conn.commit()
    thread = threading.Thread(target=sync_db_to_github, daemon=True)
    thread.start()

    # 2) 写新日记
    with st.expander("✍️ 写新日记", expanded=True):
        stock_options = ["大盘"] + get_dynamic_stock_list()
        ds = st.selectbox("复盘对象", options=stock_options, index=None, key="new_journal_stock")
        st.caption("🎨 提示：支持 HTML 颜色标签，如 <span style='color:red'>红色文字</span>")
        content = st.text_area("心得内容", height=150, key="new_journal_content", placeholder="支持换行、列表、空格等格式")
        if st.button("保存日记", type="primary"):
            if ds and content.strip():
                c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                          (datetime.now().strftime('%Y-%m-%d'), ds, content.strip()))
                conn.commit()
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("已存档")
                st.rerun()
            else:
                st.warning("请选择复盘对象并填写内容")

    # 3) 展示（带删除按钮）
    st.subheader("历史复盘记录")
    journal_df = pd.read_sql("SELECT id, date, stock_name, content FROM journal ORDER BY date DESC, id DESC", conn)

    if journal_df.empty:
        st.info("暂无复盘记录")
    else:
        unique_stocks = ["全部"] + sorted(journal_df['stock_name'].unique().tolist())
        filter_stock = st.selectbox("筛选股票/大盘", options=unique_stocks, index=0)
        display_df = journal_df if filter_stock == "全部" else journal_df[journal_df['stock_name'] == filter_stock]

        if display_df.empty:
            st.info(f"没有与「{filter_stock}」相关的复盘记录")
        else:
            for _, row in display_df.iterrows():
                # 删除按钮：二次确认
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f"""
                    <div style="background:#f7f7f7;border-left:4px solid #2196F3;border-radius:4px;padding:8px 10px;margin-bottom:4px;">
                        <div style="font-size:0.85em;color:#555;">{row['date']} · {row['stock_name']}</div>
                        <div style="white-space: pre-line;font-size:0.95em;margin-top:4px;">
                            {row['content']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                with col2:
                    if st.button("🗑️", key=f"del_{row['id']}"):
                        if st.session_state.get(f"confirm_{row['id']}", False):
                            c.execute("DELETE FROM journal WHERE id = ?", (row['id'],))
                            conn.commit()
                            thread = threading.Thread(target=sync_db_to_github, daemon=True)
                            thread.start()
                            st.success("已删除")
                            st.rerun()
                        else:
                            st.session_state[f"confirm_{row['id']}"] = True
                            st.warning("再点一次确认删除")

            st.caption(f"共 {len(journal_df)} 条记录，当前显示 {len(display_df)} 条")



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
