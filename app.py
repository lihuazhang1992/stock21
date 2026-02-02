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

elif choice == "🎯 价格目标管理":
    st.header("🎯 动态追踪目标管理")

    # --- 1. 强制检查并修复数据库表结构 ---
    def sync_target_table_schema():
        # 确保所有必要的列都存在
        cols_to_add = {
            "base_price": "REAL DEFAULT 0.0",
            "buy_target": "REAL DEFAULT 0.0",    # 这里我们借用来存 追踪百分比
            "peak_price": "REAL DEFAULT 0.0",
            "is_triggered": "INTEGER DEFAULT 0",
            "last_updated": "TEXT"
        }
        for col, definition in cols_to_add.items():
            try:
                c.execute(f"ALTER TABLE price_targets ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass # 列已存在
        conn.commit()

    sync_target_table_schema()

    # --- 2. 新增/编辑监控配置 ---
    with st.expander("➕ 设置监控基准 (突破后追踪)", expanded=False):
        all_stocks = get_dynamic_stock_list()
        selected_stock = st.selectbox("选择股票", [""] + all_stocks, key="add_target_stock")
        
        if selected_stock:
            curr_map = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
            curr = curr_map.get(selected_stock, 0.0)
            st.write(f"当前现价: `{curr:.3f}`")
            
            col1, col2, col3 = st.columns(3)
            # 这里的逻辑是：设定一个基准价格，以及突破后希望回撤/反弹多少百分比再提醒
            base_p = col1.number_input("1. 突破基准价", value=curr, step=0.001, format="%.3f")
            trace_p = col2.number_input("2. 追踪回调/反弹 (%)", value=2.0, step=0.1)
            
            if st.button("启动监控"):
                # 初始状态 is_triggered=0, peak_price=0
                c.execute("""
                    INSERT OR REPLACE INTO price_targets (code, base_price, buy_target, is_triggered, peak_price, last_updated)
                    VALUES (?, ?, ?, 0, 0, ?)
                """, (selected_stock, base_p, trace_p, datetime.now().strftime("%m-%d %H:%M")))
                conn.commit()
                threading.Thread(target=sync_db_to_github, daemon=True).start()
                st.success(f"{selected_stock} 监控已启动")
                st.rerun()

    # --- 3. 监控看板 ---
    # 重新读取数据，确保列名完整
    targets_df = pd.read_sql("SELECT * FROM price_targets", conn)
    current_prices = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}

    if not targets_df.empty:
        for _, row in targets_df.iterrows():
            stock = row['code']
            curr = current_prices.get(stock, 0.0)
            base = row['base_price']
            trace_limit = row['buy_target'] # 追踪百分比
            peak = row['peak_price']
            triggered = row['is_triggered']
            
            with st.container():
                # 样式美化
                st.markdown(f"### {stock} {'<span style=\"color:#ff4b4b;font-size:0.6em;border:1px solid #ff4b4b;padding:2px 5px;border-radius:5px;\">追踪中</span>' if triggered else '<span style=\"color:#666;font-size:0.6em;border:1px solid #666;padding:2px 5px;border-radius:5px;\">等待突破</span>'}", unsafe_allow_html=True)
                
                c1, c2, c3 = st.columns([2, 3, 1])
                
                # 第一列：基础信息
                c1.write(f"**基准价:** `{base:.3f}`")
                c1.write(f"**现价:** `{curr:.3f}`")
                
                # 第二列：动态逻辑核心
                if not triggered:
                    # 自动检测是否突破 (不管是向上还是向下，只要跨过基准线就激活)
                    # 如果你想更精准，可以加个方向判断。这里默认“跨过基准”即开始追踪
                    dist = abs(curr - base) / base * 100
                    c2.info(f"距离基准价还差: `{dist:.2f}%`")
                    if (base > 0 and ((curr >= base and base > 0) or (curr <= base and base > 0))): # 简单逻辑触发
                        if st.button(f"🚩 手动/自动激活追踪 ({stock})"):
                            c.execute("UPDATE price_targets SET is_triggered=1, peak_price=? WHERE code=?", (curr, stock))
                            conn.commit()
                            st.rerun()
                else:
                    # 追踪阶段：手动设置峰值
                    new_peak = c2.number_input(f"手动输入突破后峰值 (最高/最低)", value=float(peak) if peak > 0 else curr, step=0.001, format="%.3f", key=f"inp_{stock}")
                    if new_peak != peak:
                        c.execute("UPDATE price_targets SET peak_price=? WHERE code=?", (new_peak, stock))
                        conn.commit()
                        st.rerun()
                    
                    # 计算偏离百分比
                    diff_pct = abs((curr - new_peak) / new_peak * 100) if new_peak > 0 else 0
                    progress = min(diff_pct / trace_limit, 1.0) if trace_limit > 0 else 0
                    
                    c2.write(f"**当前偏离度:** `{diff_pct:.2f}%` (目标 `{trace_limit}%`)")
                    c2.progress(progress)
                    
                    if diff_pct >= trace_limit:
                        st.warning(f"🔔 {stock} 已达到追踪目标！当前偏离 {diff_pct:.2f}%")

                # 第三列：操作
                if c3.button("🗑️ 删除", key=f"del_{stock}"):
                    c.execute("DELETE FROM price_targets WHERE code=?", (stock,))
                    conn.commit()
                    threading.Thread(target=sync_db_to_github, daemon=True).start()
                    st.rerun()
                
                st.divider()
    else:
        st.info("暂无监控记录，点击上方“新增”开始追踪")








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












