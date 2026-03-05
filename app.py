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

# ============================================================
# 💰 盈利账单 - 修改后的版本（已平仓+未平仓）
# ============================================================
elif choice == "💰 盈利账单":
    st.header("💰 盈利账单 (已平仓+未平仓)")
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    latest_prices_data = {row[0]: (row[1], row[2]) for row in c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    latest_prices = {k: v[0] for k, v in latest_prices_data.items()}
    manual_costs = {k: v[1] for k, v in latest_prices_data.items()}
  
    if not df_trades.empty:
        profit_list = []
        
        for stock in df_trades['code'].unique():
            s_df = df_trades[df_trades['code'] == stock].copy()
            now_p = latest_prices.get(stock, 0.0)
            
            # ============ 核心逻辑：严格按时间流处理交易 ============
            realized_profit = 0.0  # 已实现盈亏
            unrealized_profit = 0.0  # 未实现盈亏
            
            buy_pool = []  # 存储未平仓的买入单：{'price': p, 'qty': q}
            sell_pool = []  # 存储未平仓的卖空单：{'price': p, 'qty': q}
            
            # 严格按【交易日期+ID】升序处理每一笔交易
            for _, trade in s_df.sort_values(['date', 'id']).iterrows():
                action = trade['action']
                price = trade['price']
                qty = trade['quantity']
                remaining = qty
                
                if action == '买入':
                    # 步骤1：先回补卖空持仓（高价卖空单优先回补，锁定卖空盈利）
                    if sell_pool and remaining > 0:
                        # 卖空单按价格从高到低排序，高价优先回补
                        for sp in sorted(sell_pool, key=lambda x: -x['price']):
                            if remaining <= 0:
                                break
                            if sp['qty'] <= 0:
                                continue
                            # 计算回补数量
                            cover_qty = min(sp['qty'], remaining)
                            # 计算卖空回补的实现盈亏（卖价 - 买价）
                            realized_profit += (sp['price'] - price) * cover_qty
                            # 更新持仓数量
                            sp['qty'] -= cover_qty
                            remaining -= cover_qty
                        # 清理已耗尽的卖空持仓
                        sell_pool = [sp for sp in sell_pool if sp['qty'] > 0]
                    
                    # 步骤2：剩余买入量加入正向持仓池
                    if remaining > 0:
                        buy_pool.append({'price': price, 'qty': remaining})
                
                else:  # 卖出
                    # 步骤1：先平仓正向持仓（低价买入单优先平仓，锁定低价盈利）
                    if buy_pool and remaining > 0:
                        # 买入单按价格从低到高排序，低价优先平仓
                        for bp in sorted(buy_pool, key=lambda x: x['price']):
                            if remaining <= 0:
                                break
                            if bp['qty'] <= 0:
                                continue
                            # 计算平仓数量
                            close_qty = min(bp['qty'], remaining)
                            # 计算平仓的实现盈亏（卖价 - 买价）
                            realized_profit += (price - bp['price']) * close_qty
                            # 更新持仓数量
                            bp['qty'] -= close_qty
                            remaining -= close_qty
                        # 清理已耗尽的正向持仓
                        buy_pool = [bp for bp in buy_pool if bp['qty'] > 0]
                    
                    # 步骤2：剩余卖出量加入卖空持仓池
                    if remaining > 0:
                        sell_pool.append({'price': price, 'qty': remaining})
            
            # ============ 计算未实现盈亏 ============
            # 未平仓的正向持仓（买入持有）
            for bp in buy_pool:
                unrealized_profit += (now_p - bp['price']) * bp['qty']
            
            # 未平仓的卖空持仓（卖空持有）
            for sp in sell_pool:
                unrealized_profit += (sp['price'] - now_p) * sp['qty']
            
            # ============ 计算总持仓市值 ============
            # 正向持仓市值
            long_value = sum(bp['qty'] for bp in buy_pool) * now_p
            # 卖空持仓市值（负数表示）
            short_value = -sum(sp['qty'] for sp in sell_pool) * now_p
            current_value = long_value + short_value
            
            # ============ 总体盈亏 ============
            total_profit = realized_profit + unrealized_profit
            
            # 累计投入和累计回收（用于参考）
            total_buy_cash = s_df[s_df['action'] == '买入'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            total_sell_cash = s_df[s_df['action'] == '卖出'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            
            profit_list.append({
                "股票名称": stock,
                "累计投入": total_buy_cash,
                "累计回收": total_sell_cash,
                "已实现盈亏": realized_profit,
                "未实现盈亏": unrealized_profit,
                "持仓市值": current_value,
                "总盈亏": total_profit
            })
        
        pdf = pd.DataFrame(profit_list).sort_values(by="总盈亏", ascending=False)
        
        # 显示账户总体贡献
        total_realized = pdf['已实现盈亏'].sum()
        total_unrealized = pdf['未实现盈亏'].sum()
        total_overall = pdf['总盈亏'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("已实现盈亏", f"{total_realized:,.2f}", delta=None)
        col2.metric("未实现盈亏", f"{total_unrealized:,.2f}", delta=None)
        col3.metric("账户总体贡献", f"{total_overall:,.2f}", delta=None)
        
        # 显示详细表格
        st.write("---")
        st.subheader("📊 各股票盈亏明细")
        
        html = '<table class="custom-table"><thead><tr><th>股票名称</th><th>累计投入</th><th>累计回收</th><th>已实现盈亏</th><th>未实现盈亏</th><th>持仓市值</th><th>总盈亏</th></tr></thead><tbody>'
        for _, r in pdf.iterrows():
            # 总盈亏的颜色标记
            c_class = "profit-red" if r['总盈亏'] > 0 else "loss-green" if r['总盈亏'] < 0 else ""
            # 已实现盈亏的颜色标记
            realized_class = "profit-red" if r['已实现盈亏'] > 0 else "loss-green" if r['已实现盈亏'] < 0 else ""
            # 未实现盈亏的颜色标记
            unrealized_class = "profit-red" if r['未实现盈亏'] > 0 else "loss-green" if r['未实现盈亏'] < 0 else ""
            
            html += f"""<tr>
                <td>{r['股票名称']}</td>
                <td>{r['累计投入']:,.2f}</td>
                <td>{r['累计回收']:,.2f}</td>
                <td class='{realized_class}'>{r['已实现盈亏']:,.2f}</td>
                <td class='{unrealized_class}'>{r['未实现盈亏']:,.2f}</td>
                <td>{r['持仓市值']:,.2f}</td>
                <td class='{c_class}'>{r['总盈亏']:,.2f}</td>
            </tr>"""
        
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("📌 交易数据库为空，请先录入交易记录")
