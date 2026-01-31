import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import git
from git.exc import GitCommandError, InvalidGitRepositoryError
import os
import shutil

# --- 基础配置与页面设置 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")
# 数据库文件名（和你原有一致，不要改）
DB_FILE = "stock_data_v12.db"
# 仓库主分支（你的是main，不用改）
GIT_BRANCH = "main"

# --- Streamlit秘钥配置（修复云端兼容问题，直接定义变量，后续在Secrets填值）---
# 需在Streamlit Cloud中配置：Settings → Secrets → 填写以下4个参数
GITHUB_PAT = st.secrets.get("GITHUB_PAT", "")
GITHUB_USERNAME = st.secrets.get("GITHUB_USERNAME", "")
GITHUB_REPO_HTTPS = st.secrets.get("GITHUB_REPO_HTTPS", "")
GIT_USER_EMAIL = st.secrets.get("GIT_USER_EMAIL", "")

# --- 数据库连接（保留原有逻辑，适配云端）---
def get_connection():
    # 云端确保数据库文件在当前目录
    db_path = pathlib.Path(__file__).with_name(DB_FILE)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

conn = get_connection()
c = conn.cursor()

# --- 数据库表结构自动升级（保留你原有所有表）---
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
        last_updated TEXT,
        buy_base REAL DEFAULT 0.0,
        sell_base REAL DEFAULT 0.0
    )
''')
# 动态增加缺失列（兼容旧数据库，保留原有逻辑）
try:
    c.execute("ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0")
except sqlite3.OperationalError:
    pass
try:
    c.execute("ALTER TABLE trades ADD COLUMN note TEXT")
except sqlite3.OperationalError:
    pass
conn.commit()

# --- 工具函数：动态格式化数字（保留原有）---
def format_number(num):
    if pd.isna(num) or num is None:
        return "0"
    num_str = f"{num}"
    formatted = num_str.rstrip('0').rstrip('.') if '.' in num_str else num_str
    return formatted

# --- 工具函数：获取动态股票列表（保留原有）---
def get_dynamic_stock_list():
    try:
        t_stocks = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股", "中芯国际", "比亚迪"] + [s for s in t_stocks if s])))
    except:
        return ["汇丰控股", "中芯国际", "比亚迪"]

# --- 注入CSS样式（保留你原有所有样式）---
st.markdown("""
    <style>
    .custom-table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 15px; border-radius: 8px; overflow: hidden; box-shadow: 0 0 10px rgba(0,0,0,0.05); }
    .custom-table thead tr { background-color: #009879; color: #ffffff; text-align: center; font-weight: bold; }
    .custom-table th, .custom-table td { padding: 12px 15px; text-align: center; border-bottom: 1px solid #dddddd; }
    .custom-table tbody tr:nth-of-type(even) { background-color: #f8f8f8; }
    .profit-red { color: #d32f2f; font-weight: bold; }
    .loss-green { color: #388e3c; font-weight: bold; }
    .stToast { font-size: 14px; }
    </style>
""", unsafe_allow_html=True)

# --- 核心：云端适配的自动同步GitHub函数（修复秘钥调用，适配云端）---
def auto_sync_github():
    """
    数据修改后自动同步数据库文件到GitHub（适配Streamlit Cloud云端，PAT+HTTPS免密）
    同步结果会在页面右下角弹出提示
    """
    # 1. 检查秘钥是否配置，未配置直接提示（直接调用全局变量，修复云端兼容）
    pat = GITHUB_PAT.strip()
    username = GITHUB_USERNAME.strip()
    repo_https = GITHUB_REPO_HTTPS.strip()
    git_email = GIT_USER_EMAIL.strip()
    if not all([pat, username, repo_https, git_email]):
        st.toast("⚠️ GitHub同步未配置：请在Streamlit秘钥中填写PAT/用户名/仓库地址/邮箱", icon="⚠️")
        return

    # 2. 检查数据库文件是否存在
    db_path = pathlib.Path(__file__).with_name(DB_FILE)
    if not db_path.exists():
        st.toast(f"⚠️ 同步失败：数据库文件{DB_FILE}不存在", icon="⚠️")
        return

    # 3. 构造带PAT的仓库地址（核心：免密推送）
    pat_repo_https = repo_https.replace("https://", f"https://{username}:{pat}@")
    local_repo_path = pathlib.Path(__file__).parent.absolute()

    try:
        # 4. 初始化/拉取Git仓库（适配云端首次运行）
        if (local_repo_path / ".git").exists():
            # 已有仓库，拉取最新内容（避免冲突）
            repo = git.Repo(local_repo_path)
            origin = repo.remote(name="origin")
            origin.fetch()
            repo.git.checkout(GIT_BRANCH)
            repo.git.pull(origin, GIT_BRANCH)
        else:
            # 首次运行，克隆仓库到本地
            repo = git.Repo.clone_from(pat_repo_https, local_repo_path, branch=GIT_BRANCH)

        # 5. 配置Git用户信息（云端必须配置）
        repo.config_writer().set_value("user", "name", username).release()
        repo.config_writer().set_value("user", "email", git_email).release()

        # 6. 暂存数据库文件
        repo.git.add(str(db_path))

        # 7. 检查是否有变更（避免空提交）
        if repo.is_dirty(untracked_files=True) or repo.index.diff("HEAD"):
            # 8. 提交代码（备注带时间，方便追溯）
            commit_msg = f"自动同步数据库：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            repo.index.commit(commit_msg)

            # 9. 推送到GitHub
            origin = repo.remote(name="origin")
            origin.push(GIT_BRANCH)

            st.toast("✅ 数据库已自动同步到GitHub", icon="✅")
        else:
            st.toast("ℹ️ 数据库无变更，无需同步", icon="ℹ️")

    except GitCommandError as e:
        st.toast(f"❌ Git同步失败：{str(e)[:50]}...", icon="❌")
    except Exception as e:
        st.toast(f"❌ 同步异常：{str(e)[:50]}...", icon="❌")

# --- 侧边栏导航（保留你原有所有菜单）---
menu = ["📊 实时持仓", "💰 盈利账单", "🎯 价格目标管理", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# --- 1. 实时持仓（保留原有功能，所有commit后加同步）---
if choice == "📊 实时持仓":
    st.header("📊 持仓盈亏分析")
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
                    auto_sync_github()  # 数据修改→自动同步GitHub

        # 读取最新配置
        final_raw = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
        latest_config = {row[0]: (row[1], row[2]) for row in final_raw}
        summary = []
        all_active_records = []

        # 按个股处理交易（保留原有核心逻辑）
        for stock in stocks:
            s_df = df_trades[df_trades['code'] == stock].copy()
            now_p, manual_cost = latest_config.get(stock, (0.0, 0.0))
            net_buy = s_df[s_df['action'] == '买入']['quantity'].sum()
            net_sell = s_df[s_df['action'] == '卖出']['quantity'].sum()
            net_q = net_buy - net_sell

            if net_q != 0:
                if manual_cost > 0:
                    p_rate = ((now_p - manual_cost) / manual_cost) * 100 if net_q > 0 else ((manual_cost - now_p) / manual_cost) * 100
                else:
                    p_rate = 0.0
                summary.append([stock, net_q, format_number(manual_cost), format_number(now_p), f"{p_rate:.2f}%", p_rate])

            # 逐笔时间流处理交易（保留原有核心逻辑）
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
                            if remaining <= 0:
                                break
                            if sp['qty'] <= 0:
                                continue
                            cover_qty = min(sp['qty'], remaining)
                            gain = ((sp['price'] - price) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                            paired_trades.append({
                                "date": f"{sp['date']} → {trade_date}",
                                "code": stock,
                                "type": "✅ 已配对交易对",
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
                            if remaining <= 0:
                                break
                            if bp['qty'] <= 0:
                                continue
                            close_qty = min(bp['qty'], remaining)
                            gain = ((price - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                            paired_trades.append({
                                "date": f"{bp['date']} → {trade_date}",
                                "code": stock,
                                "type": "✅ 已配对交易对",
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

            # 收集未平仓持仓（保留原有）
            for bp in buy_positions:
                float_gain = ((now_p - bp['price']) / bp['price'] * 100) if bp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": bp['date'], "code": stock, "type": "🔴 买入持有",
                    "price": format_number(bp['price']), "qty": bp['qty'],
                    "gain_str": f"{float_gain:.2f}%", "gain_val": float_gain
                })
            for sp in sell_positions:
                float_gain = ((sp['price'] - now_p) / sp['price'] * 100) if sp['price'] > 0 else 0.0
                all_active_records.append({
                    "date": sp['date'], "code": stock, "type": "🟢 卖空持有",
                    "price": format_number(sp['price']), "qty": sp['qty'],
                    "gain_str": f"{float_gain:.2f}%", "gain_val": float_gain
                })
            all_active_records = paired_trades + all_active_records

        # 显示持仓概览（保留原有样式）
        st.subheader("1️⃣ 账户持仓概览 (手动成本模式)")
        if summary:
            summary.sort(key=lambda x: x[5], reverse=True)
            html = '<table class="custom-table"><thead><tr><th>股票代码</th><th>净持仓</th><th>手动成本</th><th>现价</th><th>盈亏比例</th></tr></thead><tbody>'
            for r in summary:
                c_class = "profit-red" if r[5] > 0 else "loss-green" if r[5] < 0 else ""
                html += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td class="{c_class}">{r[4]}</td></tr>'
            html += '</tbody></table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.info("📌 目前账户无任何净持仓")

        # 显示交易配对与未平仓明细（保留原有筛选/排序）
        st.write("---")
        st.subheader("2️⃣ 交易配对与未平仓单 (严格时间流)")
        with st.expander("🔍 筛选条件", expanded=False):
            col1, col2, col3 = st.columns(3)
            stock_filter = col1.text_input("筛选股票", placeholder="输入股票代码/名称")
            min_gain = col2.number_input("最小盈亏(%)", value=-100.0, step=0.1)
            max_gain = col3.number_input("最大盈亏(%)", value=100.0, step=0.1)
            trade_type = st.selectbox("交易类型筛选", ["全部", "✅ 已配对交易对", "🔴 买入持有", "🟢 卖空持有"], index=0)

        # 应用筛选
        filtered_records = all_active_records.copy()
        if stock_filter:
            filtered_records = [r for r in filtered_records if stock_filter.lower() in r["code"].lower()]
        if not (min_gain == -100 and max_gain == 100):
            filtered_records = [r for r in filtered_records if min_gain <= r['gain_val'] <= max_gain]
        if trade_type != "全部":
            filtered_records = [r for r in filtered_records if r["type"] == trade_type]

        # 显示筛选结果
        if filtered_records:
            sort_option = st.selectbox("排序方式", ["盈亏降序", "盈亏升序", "日期降序", "日期升序"], index=0)
            if sort_option == "盈亏降序":
                filtered_records.sort(key=lambda x: x['gain_val'], reverse=True)
            elif sort_option == "盈亏升序":
                filtered_records.sort(key=lambda x: x['gain_val'])
            elif sort_option == "日期降序":
                filtered_records.sort(key=lambda x: x['date'], reverse=True)
            elif sort_option == "日期升序":
                filtered_records.sort(key=lambda x: x['date'])

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

# --- 2. 盈利账单（保留原有所有功能）---
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
    else:
        st.info("📌 暂无交易记录，无法计算盈亏")

# --- 3. 价格目标管理（保留原有功能，commit后加同步）---
elif choice == "🎯 价格目标管理":
    # 确保表列存在
    def ensure_columns():
        for col in ["buy_base", "sell_base"]:
            try:
                c.execute(f"ALTER TABLE price_targets ADD COLUMN {col} REAL DEFAULT 0.0")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    ensure_columns()

    # 读取数据
    targets_raw = c.execute("SELECT code, buy_base, sell_base FROM price_targets").fetchall()
    targets_dict = {r[0]: {"buy": r[1] or 0.0, "sell": r[2] or 0.0} for r in targets_raw}
    current_prices = {row[0]: row[1] or 0.0 for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    all_stocks = get_dynamic_stock_list()

    # 标题+新增按钮
    c1, c2 = st.columns([4, 1])
    c1.markdown("## 🎯 价格目标管理")
    c2.markdown("<br>", unsafe_allow_html=True)
    with c2.expander("➕ 新增", expanded=False):
        selected_stock = st.selectbox("股票", [""] + all_stocks, key="target_stock_select")
        if selected_stock:
            curr = current_prices.get(selected_stock, 0.0)
            st.caption(f"现价 **{curr:.3f}**" if curr > 0 else "暂无现价")
            exist = targets_dict.get(selected_stock, {"buy": 0.0, "sell": 0.0})
            buy_val = float(exist["buy"]) if exist["buy"] else 0.0
            sell_val = float(exist["sell"]) if exist["sell"] else 0.0
            buy_base = st.number_input("买入基准", value=buy_val, step=0.001, format="%.3f")
            sell_base = st.number_input("卖出基准", value=sell_val, step=0.001, format="%.3f")
            if st.button("保存", type="primary"):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                c.execute("""
                    INSERT OR REPLACE INTO price_targets
                    (code, buy_base, sell_base, last_updated)
                    VALUES (?,?,?,?)
                """, (selected_stock, buy_base, sell_base, now_str))
                conn.commit()
                auto_sync_github()  # 数据修改→自动同步GitHub
                st.success("已保存")
                st.rerun()

    # 显示监控卡片（保留原有样式）
    st.subheader("当前监控")
    rows = []
    for stock in all_stocks:
        curr = current_prices.get(stock, 0.0)
        if curr <= 0:
            continue
        t = targets_dict.get(stock, {"buy": 0.0, "sell": 0.0})
        buy_base = t["buy"]
        sell_base = t["sell"]
        if buy_base > 0:
            buy_pct = abs((buy_base - curr) / buy_base * 100)
            rows.append([stock, "买入", buy_base, curr, buy_pct])
        if sell_base > 0:
            sell_pct = abs((curr - sell_base) / sell_base * 100)
            rows.append([stock, "卖出", sell_base, curr, sell_pct])

    if rows:
        rows.sort(key=lambda x: x[4])
        cols = st.columns(2)
        for idx, r in enumerate(rows):
            stock, direction, base, curr, pct = r
            color = "#4CAF50" if direction == "买入" else "#F44336"
            with cols[idx % 2]:
                st.markdown(f"""
                <div style="background:#fff;border-left:4px solid {color};border-radius:6px;
                            padding:8px 10px;margin-bottom:4px;box-shadow:0 1px 2px rgba(0,0,0,.08);">
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span style="font-size:1.05em;font-weight:600;">{stock}</span>
                        <span style="background:{color};color:#fff;border-radius:4px;padding:1px 5px;font-size:0.8em;">{direction}</span>
                    </div>
                    <div style="font-size:0.8em;color:#666;margin-top:2px;">基准 {base:.3f}　现价 {curr:.3f}</div>
                    <div style="margin-top:4px;font-size:1.15em;font-weight:500;color:{color};">
                        还差 {pct:.2f}%
                    </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("暂无基准价记录")

# --- 4. 交易录入（保留原有功能，commit后加同步）---
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
                auto_sync_github()  # 数据修改→自动同步GitHub
                st.success("交易记录已保存！")
                st.rerun()

# --- 5. 买卖信号（保留原有功能，commit后加同步）---
elif choice == "🔔 买卖信号":
    st.header("🔔 策略监控信号")

    # 新增监控
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
        s_high = c1.number_input("高点参考价", value=float(signal_data[0]) if signal_data else None, step=0.0001)
        h_date = c1.date_input("高点日期", value=datetime.strptime(signal_data[4], '%Y-%m-%d').date() if signal_data and signal_data[4] else datetime.now())
        s_low = c2.number_input("低点参考价", value=float(signal_data[1]) if signal_data else None, step=0.0001)
        l_date = c2.date_input("低点日期", value=datetime.strptime(signal_data[5], '%Y-%m-%d').date() if signal_data and signal_data[5] else datetime.now())
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
                auto_sync_github()  # 数据修改→自动同步GitHub
                st.success("监控已更新")
                st.rerun()

    # 显示监控列表（保留原有样式）
    sig_df = pd.read_sql("SELECT * FROM signals", conn)
    prices_map = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}

    if not sig_df.empty:
        html = '<table class="custom-table"><thead><tr><th>代码</th><th>高点(日期)</th><th>低点(日期)</th><th>距高点</th><th>距低点</th><th>建议</th></tr></thead><tbody>'
        for _, r in sig_df.iterrows():
            np = prices_map.get(r['code'], 0.0)
            dr = ((np - r['high_point']) / r['high_point'] * 100) if r['high_point'] > 0 else 0
            rr = ((np - r['low_point']) / r['low_point'] * 100) if r['low_point'] > 0 else 0
            st_text = "🟢 建议卖出" if rr >= r['up_threshold'] else "🔴 建议买入" if dr <= -r['down_threshold'] else "⚖️ 观望"
            high_point_formatted = format_number(r['high_point'])
            low_point_formatted = format_number(r['low_point'])
            html += f"<tr><td>{r['code']}</td><td>{high_point_formatted}<br><small>{r['high_date']}</small></td><td>{low_point_formatted}<br><small>{r['low_date']}</small></td><td>{dr:.2f}%</td><td>{rr:.2f}%</td><td>{st_text}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)

        # 清空监控
        if st.button("🗑️ 清空所有监控"):
            c.execute("DELETE FROM signals")
            conn.commit()
            auto_sync_github()  # 数据修改→自动同步GitHub
            st.rerun()
    else:
        st.info("当前没有设置任何监控信号")

# --- 6. 历史明细（保留原有功能，commit后加同步）---
elif choice == "📜 历史明细":
    st.header("📜 历史交易流水")
    df_full = pd.read_sql("SELECT id, date, code, action, price, quantity, note FROM trades ORDER BY date DESC, id DESC", conn)

    if df_full.empty:
        st.info("暂无交易记录")
    else:
        # 日期转换
        df_full['date'] = pd.to_datetime(df_full['date']).dt.date
        # 搜索筛选
        search_code = st.text_input("🔍 搜索股票代码（仅影响显示，不影响编辑）")
        df_display = df_full.copy()
        if search_code:
            df_display = df_display[df_display['code'].str.contains(search_code, case=False, na=False)]

        # 显示流水（保留原有样式）
        html = '<table class="custom-table"><thead><tr><th>日期</th><th>代码</th><th>操作</th><th>价格</th><th>数量</th><th>总额</th><th>备注</th></tr></thead><tbody>'
        for _, r in df_display.iterrows():
            tag = f'<span class="profit-red">{r["action"]}</span>' if r["action"] == "买入" else f'<span class="loss-green">{r["action"]}</span>'
            note_display = r['note'] if pd.notna(r['note']) and str(r['note']).strip() else '<small style="color:#888;">无备注</small>'
            html += f"<tr><td>{r['date']}</td><td>{r['code']}</td><td>{tag}</td><td>{r['price']:.3f}</td><td>{int(r['quantity'])}</td><td>{r['price']*r['quantity']:,.2f}</td><td>{note_display}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)

        # 数据库维护（保留原有编辑功能）
        st.warning("⚠️ 注意：下方编辑器操作的是**全部交易记录**（不受上方搜索影响），支持增删改，请谨慎操作！")
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
                        save_df = edited_df.copy()
                        save_df['date'] = pd.to_datetime(save_df['date']).dt.strftime('%Y-%m-%d')
                        save_df.to_sql('trades', conn, if_exists='replace', index=False)
                        conn.commit()
                        auto_sync_github()  # 数据修改→自动同步GitHub
                        st.success("所有交易记录已成功更新！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"保存失败：{e}")

# --- 7. 复盘日记（保留原有功能，commit后加同步）---
elif choice == "📓 复盘日记":
    st.header("📓 复盘日记")
    # 建表（兼容）
    c.execute("""
        CREATE TABLE IF NOT EXISTS journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            stock_name TEXT,
            content TEXT
        )
    """)
    conn.commit()

    # 写新日记
    with st.expander("✍️ 写新日记", expanded=True):
        stock_options = ["大盘"] + get_dynamic_stock_list()
        ds = st.selectbox("复盘对象", options=stock_options, index=None, key="new_journal_stock")
        content = st.text_area("心得内容", height=150, key="new_journal_content", placeholder="支持换行、列表、空格等格式")
        if st.button("保存日记", type="primary"):
            if ds and content.strip():
                c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                          (datetime.now().strftime('%Y-%m-%d'), ds, content.strip()))
                conn.commit()
                auto_sync_github()  # 数据修改→自动同步GitHub
                st.success("已存档")
                st.rerun()
            else:
                st.warning("请选择复盘对象并填写内容")

    # 展示历史日记（保留原有删除功能）
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
                            auto_sync_github()  # 数据修改→自动同步GitHub
                            st.success("已删除")
                            st.rerun()
                        else:
                            st.session_state[f"confirm_{row['id']}"] = True
                            st.warning("再点一次确认删除")
            st.caption(f"共 {len(journal_df)} 条记录，当前显示 {len(display_df)} 条")

# --- 下载数据库按钮（保留原有功能）---
col1, col2, col3 = st.columns([5, 1, 1])
with col3:
    db_path = pathlib.Path(__file__).with_name(DB_FILE)
    if db_path.exists():
        with open(db_path, "rb") as f:
            st.download_button(
                label="📥 下载数据库",
                data=f,
                file_name=DB_FILE,
                mime="application/x-sqlite3"
            )

# 关闭数据库连接
conn.close()
