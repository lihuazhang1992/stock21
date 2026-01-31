# app.py  —— 股票管理系统 v22.1（含自动备份 GitHub）
import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
# -------------------- 自动备份 begin --------------------
import os, shutil
from git import Repo
DB_FILE  = pathlib.Path(__file__).with_name("stock_data_v12.db")
try:                       # 本地用 .env ；Cloud 用 st.secrets
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN    = os.getenv("GITHUB_TOKEN")
    REPO_URL = os.getenv("REPO_URL")
except Exception:
    TOKEN    = st.secrets.get("GITHUB_TOKEN", "")
    REPO_URL = st.secrets.get("REPO_URL", "")

def auto_commit():
    """克隆→复制db→commit→push"""
    if not (TOKEN and REPO_URL):
        return            # 没配 token 就静默跳过
    try:
        repo_dir = pathlib.Path(__file__).with_name(".git_repo")
        if not repo_dir.exists():
            repo = Repo.clone_from(REPO_URL.replace("https://",
                                   f"https://x-access-token:{TOKEN}@"),
                                   repo_dir, depth=1)
        else:
            repo = Repo(repo_dir)
            repo.remotes.origin.pull()
        shutil.copy2(DB_FILE, repo_dir/DB_FILE.name)
        repo.git.add(DB_FILE.name)
        repo.index.commit(f"auto backup {datetime.utcnow():%m%d-%H%M}")
        repo.remotes.origin.push()
    except Exception as e:
        st.toast(f"git auto-push 失败：{e}", icon="⚠️")
# -------------------- 自动备份 end ----------------------

st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

# =========  数据库连接 & 建表  =========
@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)
conn = get_conn()
c = conn.cursor()

def init_db():
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, code TEXT, action TEXT,
                    price REAL, quantity INTEGER, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS prices (
                    code TEXT PRIMARY KEY,
                    current_price REAL, manual_cost REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS signals (
                    code TEXT PRIMARY KEY, high_point REAL,
                    low_point REAL, up_threshold REAL,
                    down_threshold REAL, high_date TEXT, low_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, stock_name TEXT, content TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS price_targets (
                    code TEXT PRIMARY KEY, base_price REAL DEFAULT 0.0,
                    buy_target REAL DEFAULT 0.0, sell_target REAL DEFAULT 0.0,
                    last_updated TEXT)''')
    # 兼容旧库
    for col in ["manual_cost","note"]:
        try:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")
        except: pass
    conn.commit(); auto_commit()
init_db()

# =============  通用函数  =============
def get_dynamic_stock_list():
    try:
        t = pd.read_sql("SELECT DISTINCT code FROM trades", conn)['code'].tolist()
        return sorted(list(set(["汇丰控股","中芯国际","比亚迪"]+[s for s in t if s])))
    except: return ["汇丰控股","中芯国际","比亚迪"]

def format_number(num):
    if pd.isna(num) or num is None or num==0: return "0"
    s = f"{num}"
    return s.rstrip('0').rstrip('.') if '.' in s else s

# =============  侧边栏  =============
menu = ["📊 实时持仓","💰 盈利账单","🎯 价格目标管理",
        "📝 交易录入","🔔 买卖信号","📜 历史明细","📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)

# =============  1️⃣ 实时持仓  =============
if choice=="📊 实时持仓":
    st.header("📊 持仓盈亏分析")
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    if df_trades.empty:
        st.info("📌 交易数据库为空，请先录入交易记录")
        st.stop()

    # 维护现价/手动成本
    with st.expander("🛠️ 维护现价与手动成本", expanded=True):
        raw_prices = c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()
        config_query = {row[0]: (row[1], row[2]) for row in raw_prices}
        for stock in df_trades['code'].unique():
            col1, col2 = st.columns(2)
            old_p, old_c = config_query.get(stock, (0.0, 0.0))
            new_p = col1.number_input(f"{stock} 现价", value=float(old_p), key=f"p_{stock}", step=0.0001)
            new_c = col2.number_input(f"{stock} 手动成本", value=float(old_c), key=f"c_{stock}", step=0.0001)
            if new_p!=old_p or new_c!=old_c:
                c.execute("INSERT OR REPLACE INTO prices (code, current_price, manual_cost) VALUES (?,?,?)",
                          (stock, new_p, new_c))
                conn.commit(); auto_commit()

    # 汇总
    summary, all_active_records = [], []
    latest_config = {row[0]: (row[1], row[2]) for row in
                     c.execute("SELECT code, current_price, manual_cost FROM prices").fetchall()}
    for stock in df_trades['code'].unique():
        s_df = df_trades[df_trades['code']==stock]
        net_q = (s_df[s_df['action']=='买入']['quantity'].sum() -
                 s_df[s_df['action']=='卖出']['quantity'].sum())
        now_p, manual_cost = latest_config.get(stock, (0.0, 0.0))
        if net_q!=0:
            p_rate = ((now_p - manual_cost)/manual_cost*100) if manual_cost>0 else 0.0
            summary.append([stock, net_q, format_number(manual_cost),
                            format_number(now_p), f"{p_rate:.2f}%", p_rate])
        # 逐笔时间流配对
        buy_pos, sell_pos, paired = [], [], []
        for _, trd in s_df.sort_values(['date','id']).iterrows():
            dt, act, prc, qty = trd['date'], trd['action'], trd['price'], trd['quantity']
            rem = qty
            if act=='买入':
                if sell_pos and rem>0:
                    for sp in sorted(sell_pos, key=lambda x: -x['price']):
                        if rem<=0: break
                        cover = min(sp['qty'], rem)
                        gain = ((sp['price']-prc)/sp['price']*100) if sp['price']>0 else 0
                        paired.append({"date":f"{sp['date']}→{dt}","code":stock,
                                       "type":"✅ 已配对","price":f"{sp['price']}→{prc}",
                                       "qty":cover,"gain_str":f"{gain:.2f}%","gain_val":gain})
                        sp['qty']-=cover; rem-=cover
                    sell_pos = [s for s in sell_pos if s['qty']>0]
                if rem>0: buy_pos.append({'date':dt,'price':prc,'qty':rem})
            else: # 卖出
                if buy_pos and rem>0:
                    for bp in sorted(buy_pos, key=lambda x: x['price']):
                        if rem<=0: break
                        close = min(bp['qty'], rem)
                        gain = ((prc-bp['price'])/bp['price']*100) if bp['price']>0 else 0
                        paired.append({"date":f"{bp['date']}→{dt}","code":stock,
                                       "type":"✅ 已配对","price":f"{bp['price']}→{prc}",
                                       "qty":close,"gain_str":f"{gain:.2f}%","gain_val":gain})
                        bp['qty']-=close; rem-=close
                    buy_pos = [b for b in buy_pos if b['qty']>0]
                if rem>0: sell_pos.append({'date':dt,'price':prc,'qty':rem})
        # 未平仓
        for bp in buy_pos:
            fg = ((now_p-bp['price'])/bp['price']*100) if bp['price']>0 else 0
            all_active_records.append({"date":bp['date'],"code":stock,"type":"🔴 买入持有",
                                       "price":format_number(bp['price']),"qty":bp['qty'],
                                       "gain_str":f"{fg:.2f}%","gain_val":fg})
        for sp in sell_pos:
            fg = ((sp['price']-now_p)/sp['price']*100) if sp['price']>0 else 0
            all_active_records.append({"date":sp['date'],"code":stock,"type":"🟢 卖空持有",
                                       "price":format_number(sp['price']),"qty":sp['qty'],
                                       "gain_str":f"{fg:.2f}%","gain_val":fg})
        all_active_records = paired + all_active_records

    # 展示
    st.subheader("1️⃣ 账户持仓概览 (手动成本模式)")
    if summary:
        summary.sort(key=lambda x: x[5], reverse=True)
        html = '<table class="custom-table"><thead><tr><th>股票</th><th>净持仓</th><th>手动成本</th><th>现价</th><th>盈亏比例</th></tr></thead><tbody>'
        for r in summary:
            cls = "profit-red" if r[5]>0 else "loss-green" if r[5]<0 else ""
            html += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td class="{cls}">{r[4]}</td></tr>'
        st.markdown(html+'</tbody></table>', unsafe_allow_html=True)
    else: st.info("暂无净持仓")

    st.write("---")
    st.subheader("2️⃣ 交易配对与未平仓单 (严格时间流)")
    with st.expander("🔍 筛选条件", expanded=False):
        col1,col2,col3=st.columns(3)
        stock_filter = col1.text_input("筛选股票", placeholder="输入代码/名称")
        min_gain = col2.number_input("最小盈亏(%)", value=-100.0, step=0.1)
        max_gain = col3.number_input("最大盈亏(%)", value=100.0, step=0.1)
        trade_type = st.selectbox("交易类型", ["全部","✅ 已配对交易对","🔴 买入持有","🟢 卖空持有"], index=0)
    filtered = all_active_records.copy()
    if stock_filter:
        filtered = [r for r in filtered if stock_filter.lower() in r["code"].lower()]
    if not (min_gain==-100 and max_gain==100):
        filtered = [r for r in filtered if min_gain<=r["gain_val"]<=max_gain]
    if trade_type!="全部":
        filtered = [r for r in filtered if r["type"]==trade_type]
    if filtered:
        sort_opt = st.selectbox("排序方式", ["盈亏降序","盈亏升序","日期降序","日期升序"], index=0)
        if sort_opt=="盈亏降序": filtered.sort(key=lambda x: x["gain_val"], reverse=True)
        elif sort_opt=="盈亏升序": filtered.sort(key=lambda x: x["gain_val"])
        elif sort_opt=="日期降序": filtered.sort(key=lambda x: x["date"], reverse=True)
        else: filtered.sort(key=lambda x: x["date"])
        html = '<table class="custom-table"><thead><tr><th>交易时间</th><th>股票</th><th>类型</th><th>成交价格</th><th>数量</th><th>盈亏</th></tr></thead><tbody>'
        for r in filtered:
            cls = "profit-red" if r["gain_val"]>0 else "loss-green" if r["gain_val"]<0 else ""
            html += f'<tr><td>{r["date"]}</td><td>{r["code"]}</td><td>{r["type"]}</td><td>{r["price"]}</td><td>{r["qty"]}</td><td class="{cls}">{r["gain_str"]}</td></tr>'
        st.markdown(html+'</tbody></table>', unsafe_allow_html=True)
    else: st.info("暂无符合条件记录")

# =============  2️⃣ 盈利账单  =============
elif choice=="💰 盈利账单":
    st.header("💰 盈利账单 (总额对冲法)")
    df_trades = pd.read_sql("SELECT * FROM trades", conn)
    latest_prices = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    if df_trades.empty:
        st.info("暂无交易"); st.stop()
    profit_list = []
    for stock in df_trades['code'].unique():
        s_df = df_trades[df_trades['code']==stock]
        now_p = latest_prices.get(stock, 0.0)
        total_buy = (s_df[s_df['action']=='买入']['price']*s_df[s_df['action']=='买入']['quantity']).sum()
        total_sell = (s_df[s_df['action']=='卖出']['price']*s_df[s_df['action']=='卖出']['quantity']).sum()
        net_q = s_df[s_df['action']=='买入']['quantity'].sum() - s_df[s_df['action']=='卖出']['quantity'].sum()
        cur_val = net_q*now_p if net_q>0 else 0
        profit_list.append({"股票":stock, "投入":total_buy, "回收":total_sell, "市值":cur_val, "盈亏":(total_sell+cur_val-total_buy)})
    pdf = pd.DataFrame(profit_list).sort_values(by="盈亏", ascending=False)
    st.metric("账户总体贡献", f"{pdf['盈亏'].sum():,.2f}")
    html = '<table class="custom-table"><thead><tr><th>股票</th><th>累计投入</th><th>累计回收</th><th>持仓市值</th><th>总盈亏</th></tr></thead><tbody>'
    for _,r in pdf.iterrows():
        cls = "profit-red" if r["盈亏"]>0 else "loss-green" if r["盈亏"]<0 else ""
        html += f"<tr><td>{r['股票']}</td><td>{r['投入']:,.2f}</td><td>{r['回收']:,.2f}</td><td>{r['市值']:,.2f}</td><td class='{cls}'>{r['盈亏']:,.2f}</td></tr>"
    st.markdown(html+'</tbody></table>', unsafe_allow_html=True)

# =============  3️⃣ 价格目标管理  =============
elif choice=="🎯 价格目标管理":
    # 读数据
    targets_raw = c.execute("SELECT code, buy_target, sell_target FROM price_targets").fetchall()
    targets_dict = {row[0]: {"buy": row[1] or 0.0, "sell": row[2] or 0.0} for row in targets_raw}
    current_prices = {row[0]: row[1] or 0.0 for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    all_stocks = get_dynamic_stock_list()
    c1,c2=st.columns([4,1])
    c1.markdown("## 🎯 价格目标管理")
    with c2.expander("➕ 新增", expanded=True):
        sel = st.selectbox("股票", [""]+all_stocks, key="tgt_stock")
        if sel:
            curr = current_prices.get(sel, 0.0)
            st.caption(f"现价 **{curr:.3f}**" if curr>0 else "暂无现价")
            exist = targets_dict.get(sel, {"buy":0.0, "sell":0.0})
            b = st.number_input("买入基准", value=float(exist["buy"]), step=0.001, format="%.3f")
            s = st.number_input("卖出基准", value=float(exist["sell"]), step=0.001, format="%.3f")
            if st.button("保存", type="primary"):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                c.execute("INSERT OR REPLACE INTO price_targets (code, buy_target, sell_target, last_updated) VALUES (?,?,?,?)",
                          (sel, b, s, now_str))
                conn.commit(); auto_commit()
                st.success("已保存"); st.rerun()
    # 卡片展示
    st.subheader("当前监控")
    rows = []
    for stock in all_stocks:
        curr = current_prices.get(stock, 0.0)
        if curr<=0: continue
        t = targets_dict.get(stock, {"buy":0, "sell":0})
        if t["buy"]>0: rows.append([stock, "买入", t["buy"], curr, abs((t["buy"]-curr)/t["buy"]*100)])
        if t["sell"]>0: rows.append([stock, "卖出", t["sell"], curr, abs((curr-t["sell"])/t["sell"]*100)])
    if rows:
        rows.sort(key=lambda x: x[4])
        cols = st.columns(2)
        for idx, r in enumerate(rows):
            stock, direc, base, curr, pct = r
            color = "#4CAF50" if direc=="买入" else "#F44336"
            with cols[idx%2]:
                st.markdown(f"""
                <div style="background:#fff;border-left:4px solid {color};border-radius:6px;padding:8px 10px;margin-bottom:4px;box-shadow:0 1px 2px rgba(0,0,0,.08);">
                    <div style="display:flex;align-items:center;gap:6px;"><span style="font-size:1.05em;font-weight:600;">{stock}</span><span style="background:{color};color:#fff;border-radius:4px;padding:1px 5px;font-size:0.8em;">{direc}</span></div>
                    <div style="font-size:0.8em;color:#666;margin-top:2px;">基准 {base:.3f}　现价 {curr:.3f}</div>
                    <div style="margin-top:4px;font-size:1.15em;font-weight:500;color:{color};">还差 {pct:.2f}%</div>
                </div>""", unsafe_allow_html=True)
    else: st.info("暂无基准价记录")

# =============  4️⃣ 交易录入  =============
elif choice=="📝 交易录入":
    st.header("📝 交易录入")
    full_list = get_dynamic_stock_list()
    t_code = st.selectbox("选择股票", ["【添加新股票】"]+full_list, index=None)
    final_code = st.text_input("新股票名（必填）") if t_code=="【添加新股票】" else t_code
    with st.form("trade_form", clear_on_submit=True):
        col1,col2=st.columns(2)
        d = col1.date_input("日期", datetime.now())
        a = col2.selectbox("操作", ["买入","卖出"])
        p = col1.number_input("单价", value=None, min_value=0.0, step=0.001, format="%.3f")
        q = col2.number_input("数量", value=None, min_value=1, step=1)
        note = st.text_input("备注（可选）", placeholder="突破20日均线买入/分红除权/止盈卖出等")
        submitted = st.form_submit_button("保存交易")
        if submitted:
            if not final_code: st.error("请填写或选择股票代码")
            elif p is None or q is None: st.error("请填写单价和数量")
            else:
                c.execute("INSERT INTO trades (date,code,action,price,quantity,note) VALUES (?,?,?,?,?,?)",
                          (d.strftime('%Y-%m-%d'), final_code, a, p, q, note if note.strip() else None))
                conn.commit(); auto_commit()
                st.success("交易记录已保存！"); st.rerun()

# =============  5️⃣ 买卖信号  =============
elif choice=="🔔 买卖信号":
    st.header("🔔 策略监控信号")
    def fmt(n): return format_number(n)
    with st.expander("➕ 设置新监控"):
        existing = pd.read_sql("SELECT code FROM signals", conn)['code'].tolist()
        s_code = st.selectbox("监控股票", get_dynamic_stock_list(), index=None)
        data = None
        if s_code and s_code in existing:
            data = c.execute("SELECT high_point,low_point,up_threshold,down_threshold,high_date,low_date FROM signals WHERE code=?", (s_code,)).fetchone()
        col1,col2=st.columns(2)
        s_high = col1.number_input("高点参考价", value=float(data[0]) if data else None, step=0.0001)
        h_date = col1.date_input("高点日期", datetime.strptime(data[4],'%Y-%m-%d').date() if data and data[4] else datetime.now())
        s_low  = col2.number_input("低点参考价", value=float(data[1]) if data else None, step=0.0001)
        l_date = col2.date_input("低点日期", datetime.strptime(data[5],'%Y-%m-%d').date() if data and data[5] else datetime.now())
        s_up   = col1.number_input("上涨触发(%)", value=float(data[2]) if data else 20.0, step=0.01)
        s_down = col2.number_input("回调触发(%)", value=float(data[3]) if data else 20.0, step=0.01)
        if st.button("🚀 启动/更新监控"):
            if all([s_code, s_high, s_low, s_up, s_down]):
                c.execute("INSERT OR REPLACE INTO signals (code,high_point,low_point,up_threshold,down_threshold,high_date,low_date) VALUES (?,?,?,?,?,?,?)",
                          (s_code, s_high, s_low, s_up, s_down, h_date.strftime('%Y-%m-%d'), l_date.strftime('%Y-%m-%d')))
                conn.commit(); auto_commit()
                st.success("监控已更新"); st.rerun()
    sig_df = pd.read_sql("SELECT * FROM signals", conn)
    prices_map = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    if sig_df.empty:
        st.info("当前没有设置任何监控信号")
    else:
        html = '<table class="custom-table"><thead><tr><th>代码</th><th>高点(日期)</th><th>低点(日期)</th><th>距高点</th><th>距低点</th><th>建议</th></tr></thead><tbody>'
        for _,r in sig_df.iterrows():
            np = prices_map.get(r['code'], 0.0)
            dr = ((np - r['high_point'])/r['high_point']*100) if r['high_point']>0 else 0
            rr = ((np - r['low_point'])/r['low_point']*100) if r['low_point']>0 else 0
            st_text = "🟢 建议卖出" if rr>=r['up_threshold'] else "🔴 建议买入" if dr<=-r['down_threshold'] else "⚖️ 观望"
            html += f"<tr><td>{r['code']}</td><td>{fmt(r['high_point'])}<br><small>{r['high_date']}</small></td><td>{fmt(r['low_point'])}<br><small>{r['low_date']}</small></td><td>{dr:.2f}%</td><td>{rr:.2f}%</td><td>{st_text}</td></tr>"
        st.markdown(html+'</tbody></table>', unsafe_allow_html=True)
        if st.button("🗑️ 清空所有监控"):
            c.execute("DELETE FROM signals")
            conn.commit(); auto_commit()
            st.rerun()

# =============  6️⃣ 历史明细  =============
elif choice=="📜 历史明细":
    st.header("📜 历史交易流水")
    df_full = pd.read_sql("SELECT id, date, code, action, price, quantity, note FROM trades ORDER BY date DESC, id DESC", conn)
    if df_full.empty:
        st.info("暂无交易记录"); st.stop()
    df_full['date'] = pd.to_datetime(df_full['date']).dt.date
    search_code = st.text_input("🔍 搜索股票代码（仅影响显示）")
    df_disp = df_full.copy()
    if search_code:
        df_disp = df_disp[df_disp['code'].str.contains(search_code, case=False, na=False)]
    html = '<table class="custom-table"><thead><tr><th>日期</th><th>代码</th><th>操作</th><th>价格</th><th>数量</th><th>总额</th><th>备注</th></tr></thead><tbody>'
    for _,r in df_disp.iterrows():
        tag = f'<span class="profit-red">{r["action"]}</span>' if r["action"]=="买入" else f'<span class="loss-green">{r["action"]}</span>'
        note_disp = r['note'] if pd.notna(r['note']) and str(r['note']).strip() else '<small style="color:#888;">无备注</small>'
        html += f"<tr><td>{r['date']}</td><td>{r['code']}</td><td>{tag}</td><td>{r['price']:.3f}</td><td>{int(r['quantity'])}</td><td>{r['price']*r['quantity']:,.2f}</td><td>{note_disp}</td></tr>"
    st.markdown(html+'</tbody></table>', unsafe_allow_html=True)
    st.warning("⚠️ 下方编辑器操作的是全部交易记录（不受搜索影响），支持增删改，请谨慎操作！")
    with st.expander("🛠️ 数据库维护", expanded=False):
        edited = st.data_editor(df_full, use_container_width=True, num_rows="dynamic",
                                column_config={
                                    "id": st.column_config.NumberColumn("ID", disabled=True),
                                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
                                    "code": st.column_config.TextColumn("代码", required=True),
                                    "action": st.column_config.SelectboxColumn("操作", options=["买入","卖出"], required=True),
                                    "price": st.column_config.NumberColumn("价格", min_value=0.0, format="%.3f", required=True),
                                    "quantity": st.column_config.NumberColumn("数量", min_value=1, step=1, required=True),
                                    "note": st.column_config.TextColumn("备注", width="large"),
                                }, key="de")
        if st.button("💾 提交所有修改", type="primary"):
            try:
                save_df = edited.copy()
                save_df['date'] = pd.to_datetime(save_df['date']).dt.strftime('%Y-%m-%d')
                save_df.to_sql('trades', conn, if_exists='replace', index=False)
                conn.commit(); auto_commit()
                st.success("所有交易记录已更新！"); st.rerun()
            except Exception as e: st.error(f"保存失败：{e}")

# =============  7️⃣ 复盘日记  =============
elif choice=="📓 复盘日记":
    st.header("📓 复盘日记")
    with st.expander("✍️ 写新日记", expanded=True):
        stock_opts = ["大盘"] + get_dynamic_stock_list()
        ds = st.selectbox("复盘对象", stock_opts, index=None, key="j_stock")
        content = st.text_area("心得内容", height=150, placeholder="支持换行、列表、空格等格式")
        if st.button("保存日记", type="primary"):
            if ds and content.strip():
                c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)",
                          (datetime.now().strftime('%Y-%m-%d'), ds, content.strip()))
                conn.commit(); auto_commit()
                st.success("已存档"); st.rerun()
            else: st.warning("请选择复盘对象并填写内容")
    st.subheader("历史复盘记录")
    j_df = pd.read_sql("SELECT id, date, stock_name, content FROM journal ORDER BY date DESC, id DESC", conn)
    if j_df.empty:
        st.info("暂无复盘记录")
    else:
        unique = ["全部"] + sorted(j_df['stock_name'].unique().tolist())
        filt = st.selectbox("筛选股票/大盘", unique, index=0)
        disp_df = j_df if filt=="全部" else j_df[j_df['stock_name']==filt]
        if disp_df.empty:
            st.info(f"没有与「{filt}」相关的复盘记录")
        else:
            for _, row in disp_df.iterrows():
                col1,col2=st.columns([5,1])
                with col1:
                    st.markdown(f"""
                    <div style="background:#f7f7f7;border-left:4px solid #2196F3;border-radius:4px;padding:8px 10px;margin-bottom:4px;">
                        <div style="font-size:0.85em;color:#555;">{row['date']} · {row['stock_name']}</div>
                        <div style="white-space: pre-line;font-size:0.95em;margin-top:4px;">{row['content']}</div>
                    </div>""", unsafe_allow_html=True)
                with col2:
                    if st.button("🗑️", key=f"delj_{row['id']}"):
                        if st.session_state.get(f"confirmj_{row['id']}", False):
                            c.execute("DELETE FROM journal WHERE id=?", (row['id'],))
                            conn.commit(); auto_commit()
                            st.success("已删除"); st.rerun()
                        else:
                            st.session_state[f"confirmj_{row['id']}"] = True
                            st.warning("再点一次确认删除")
            st.caption(f"共 {len(j_df)} 条，当前显示 {len(disp_df)} 条")

# =============  下载数据库按钮  =============
col1,col2,col3=st.columns([5,1,1])
with col3:
    if DB_FILE.exists():
        with open(DB_FILE, "rb") as f:
            st.download_button(label="📥 下载数据库", data=f, file_name=DB_FILE.name, mime="application/x-sqlite3")
