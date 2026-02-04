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
    latest_prices = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
  
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
            code = row[0]
            curr_price = get_current_price(code)
            
            # 买入体系参数
            buy_high_point = row[1] or 0.0
            buy_drop_pct = row[2] or 0.0
            buy_break_status = row[3] or "未突破"
            buy_low_after_break = row[4] or 0.0
            buy_calc = calc_buy_target({
                'buy_high_point': buy_high_point,
                'buy_drop_pct': buy_drop_pct,
                'buy_break_status': buy_break_status,
                'buy_low_after_break': buy_low_after_break
            }, curr_price)
            
            # 卖出体系参数
            sell_low_point = row[5] or 0.0
            sell_rise_pct = row[6] or 0.0
            sell_break_status = row[7] or "未突破"
            sell_high_after_break = row[8] or 0.0
            sell_calc = calc_sell_target({
                'sell_low_point': sell_low_point,
                'sell_rise_pct': sell_rise_pct,
                'sell_break_status': sell_break_status,
                'sell_high_after_break': sell_high_after_break
            }, curr_price)
            
            # 组装详情数据（包含反弹值/回落值）
            detail_data.append({
                "股票代码": code,
                "当前价格": f"{curr_price:.3f}" if curr_price > 0 else "未设置",
                # 买入体系
                "买入-前期高点": f"{buy_high_point:.3f}",
                "买入-下跌幅度(%)": f"{buy_drop_pct:.2f}",
                "买入-突破状态": buy_break_status,
                "买入-突破后低点": f"{buy_low_after_break:.3f}" if buy_low_after_break > 0 else "未设置",
                "买入-周期跌幅": f"{buy_calc['cycle_drop']:.3f}" if buy_calc['cycle_drop'] else "0.000",
                "买入-反弹目标价": f"{buy_calc['buy_target']:.3f}" if buy_calc['buy_target'] else "0.000",
                "买入-反弹比例(%)": f"{buy_calc['rebound_pct']:.2f}" if buy_calc['rebound_pct'] else "0.00",
                # 卖出体系
                "卖出-前期低点": f"{sell_low_point:.3f}",
                "卖出-上涨幅度(%)": f"{sell_rise_pct:.2f}",
                "卖出-突破状态": sell_break_status,
                "卖出-突破后高点": f"{sell_high_after_break:.3f}" if sell_high_after_break > 0 else "未设置",
                "卖出-周期涨幅": f"{sell_calc['cycle_rise']:.3f}" if sell_calc['cycle_rise'] else "0.000",
                "卖出-回落目标价": f"{sell_calc['sell_target']:.3f}" if sell_calc['sell_target'] else "0.000",
                "卖出-回落比例(%)": f"{sell_calc['fallback_pct']:.2f}" if sell_calc['fallback_pct'] else "0.00",
                "最后更新时间": row[9] if len(row) > 9 else "未记录"
            })
        
        # 转换为DataFrame并显示
        df_detail = pd.DataFrame(detail_data)
        # 调整列的显示顺序
        show_columns = [
            "股票代码", "当前价格",
            "买入-前期高点", "买入-下跌幅度(%)", "买入-突破状态", "买入-突破后低点",
            "买入-周期跌幅", "买入-反弹目标价", "买入-反弹比例(%)",
            "卖出-前期低点", "卖出-上涨幅度(%)", "卖出-突破状态", "卖出-突破后高点",
            "卖出-周期涨幅", "卖出-回落目标价", "卖出-回落比例(%)",
            "最后更新时间"
        ]
        st.dataframe(df_detail[show_columns], use_container_width=True)
    else:
        st.info("📌 暂无价格目标配置数据，请先配置监控参数")

# --- 交易录入 ---
elif choice == "📝 交易录入":
    # 此处保留原代码逻辑（因用户未提供完整交易录入模块，保持原有结构）
    st.header("📝 交易录入")
    col1, col2, col3 = st.columns(3)
    with col1:
        trade_date = st.date_input("交易日期", datetime.now())
        stock_code = st.selectbox("股票代码", get_dynamic_stock_list())
    with col2:
        action = st.selectbox("操作类型", ["买入", "卖出"])
        price = st.number_input("成交价格", min_value=0.0001, step=0.0001, format="%.4f")
    with col3:
        quantity = st.number_input("成交数量", min_value=1, step=1)
        note = st.text_input("交易备注（选填）")
    
    if st.button("✅ 提交交易记录", type="primary"):
        try:
            c.execute("INSERT INTO trades (date, code, action, price, quantity, note) VALUES (?, ?, ?, ?, ?, ?)",
                      (trade_date.strftime('%Y-%m-%d'), stock_code, action, price, quantity, note))
            conn.commit()
            thread = threading.Thread(target=sync_db_to_github, daemon=True)
            thread.start()
            st.success("✅ 交易记录录入成功！")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 录入失败: {e}")

# --- 买卖信号 ---
elif choice == "🔔 买卖信号":
    st.header("🔔 买卖信号管理")
    # 读取现有信号配置
    signal_data = c.execute("SELECT * FROM signals").fetchall()
    signal_dict = {row[0]: {
        'high_point': row[1], 'low_point': row[2],
        'up_threshold': row[3], 'down_threshold': row[4],
        'high_date': row[5], 'low_date': row[6]
    } for row in signal_data}
    
    selected_stock = st.selectbox("选择股票", get_dynamic_stock_list())
    current_signal = signal_dict.get(selected_stock, {})
    
    col1, col2 = st.columns(2)
    with col1:
        high_point = st.number_input("高点价格", value=current_signal.get('high_point', 0.0), step=0.001, format="%.3f")
        high_date = st.date_input("高点日期", value=datetime.strptime(current_signal.get('high_date', '2024-01-01'), '%Y-%m-%d') if current_signal.get('high_date') else datetime.now())
        up_threshold = st.number_input("上涨阈值(%)", value=current_signal.get('up_threshold', 0.0), step=0.1, format="%.1f")
    with col2:
        low_point = st.number_input("低点价格", value=current_signal.get('low_point', 0.0), step=0.001, format="%.3f")
        low_date = st.date_input("低点日期", value=datetime.strptime(current_signal.get('low_date', '2024-01-01'), '%Y-%m-%d') if current_signal.get('low_date') else datetime.now())
        down_threshold = st.number_input("下跌阈值(%)", value=current_signal.get('down_threshold', 0.0), step=0.1, format="%.1f")
    
    if st.button("💾 保存信号配置", type="primary"):
        c.execute("INSERT OR REPLACE INTO signals (code, high_point, low_point, up_threshold, down_threshold, high_date, low_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (selected_stock, high_point, low_point, up_threshold, down_threshold,
                   high_date.strftime('%Y-%m-%d'), low_date.strftime('%Y-%m-%d')))
        conn.commit()
        thread = threading.Thread(target=sync_db_to_github, daemon=True)
        thread.start()
        st.success("✅ 信号配置保存成功！")
    
    # 信号提醒
    st.subheader("📢 实时信号提醒")
    current_prices = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    for stock in get_dynamic_stock_list():
        if stock not in signal_dict or stock not in current_prices:
            continue
        sig = signal_dict[stock]
        curr_p = current_prices[stock]
        if curr_p <= 0:
            continue
        
        # 计算涨幅/跌幅
        if sig['high_point'] > 0:
            down_pct = ((sig['high_point'] - curr_p) / sig['high_point']) * 100
            if down_pct >= sig['down_threshold']:
                st.warning(f"⚠️ {stock} 下跌超过阈值: 当前{curr_p:.3f}, 高点{sig['high_point']:.3f}, 跌幅{down_pct:.1f}% (阈值{sig['down_threshold']:.1f}%)")
        
        if sig['low_point'] > 0:
            up_pct = ((curr_p - sig['low_point']) / sig['low_point']) * 100
            if up_pct >= sig['up_threshold']:
                st.success(f"📈 {stock} 上涨超过阈值: 当前{curr_p:.3f}, 低点{sig['low_point']:.3f}, 涨幅{up_pct:.1f}% (阈值{sig['up_threshold']:.1f}%)")

# --- 历史明细 ---
elif choice == "📜 历史明细":
    st.header("📜 交易历史明细")
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date DESC, id DESC", conn)
    if not df_trades.empty:
        # 筛选功能
        col1, col2, col3 = st.columns(3)
        with col1:
            stock_filter = st.text_input("筛选股票代码")
        with col2:
            action_filter = st.selectbox("筛选操作类型", ["全部", "买入", "卖出"])
        with col3:
            date_filter = st.date_input("筛选日期", value=None)
        
        # 应用筛选
        filtered_df = df_trades.copy()
        if stock_filter:
            filtered_df = filtered_df[filtered_df['code'].str.contains(stock_filter, case=False)]
        if action_filter != "全部":
            filtered_df = filtered_df[filtered_df['action'] == action_filter]
        if date_filter:
            filtered_df = filtered_df[filtered_df['date'] == date_filter.strftime('%Y-%m-%d')]
        
        # 显示表格
        st.dataframe(filtered_df, use_container_width=True)
        
        # 删除功能
        if not filtered_df.empty:
            selected_id = st.selectbox("选择要删除的记录ID", filtered_df['id'].tolist())
            if st.button("🗑️ 删除选中记录", type="secondary"):
                c.execute("DELETE FROM trades WHERE id = ?", (selected_id,))
                conn.commit()
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("✅ 记录已删除！")
                st.rerun()
    else:
        st.info("📌 暂无交易历史记录")

# --- 复盘日记 ---
elif choice == "📓 复盘日记":
    st.header("📓 交易复盘日记")
    
    # 读取日记数据
    df_journal = pd.read_sql("SELECT * FROM journal ORDER BY date DESC, id DESC", conn)
    
    # 新增日记
    col1, col2 = st.columns([2, 1])
    with col1:
        journal_date = st.date_input("日记日期", datetime.now())
        stock_name = st.selectbox("关联股票", get_dynamic_stock_list())
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)  # 对齐
        if st.button("➕ 新增日记", type="primary"):
            c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?, ?, ?)",
                      (journal_date.strftime('%Y-%m-%d'), stock_name, ""))
            conn.commit()
            thread = threading.Thread(target=sync_db_to_github, daemon=True)
            thread.start()
            st.rerun()
    
    # 编辑/查看日记
    if not df_journal.empty:
        selected_id = st.selectbox("选择日记ID", df_journal['id'].tolist())
        selected_journal = df_journal[df_journal['id'] == selected_id].iloc[0]
        
        content = st.text_area("日记内容", value=selected_journal['content'], height=300)
        col_save, col_delete = st.columns(2)
        with col_save:
            if st.button("💾 保存日记", type="primary"):
                c.execute("UPDATE journal SET date=?, stock_name=?, content=? WHERE id=?",
                          (selected_journal['date'], selected_journal['stock_name'], content, selected_id))
                conn.commit()
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("✅ 日记已保存！")
        with col_delete:
            if st.button("🗑️ 删除日记", type="secondary"):
                c.execute("DELETE FROM journal WHERE id=?", (selected_id,))
                conn.commit()
                thread = threading.Thread(target=sync_db_to_github, daemon=True)
                thread.start()
                st.success("✅ 日记已删除！")
                st.rerun()
        
        # 显示日记列表
        st.subheader("日记列表")
        st.dataframe(df_journal[['id', 'date', 'stock_name']], use_container_width=True)
    else:
        st.info("📌 暂无复盘日记，请点击新增按钮创建")

# 关闭数据库连接（程序结束时）
conn.close()
