import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

# --- 1. 基础配置与数据库连接 ---
st.set_page_config(page_title="股票管理系统 v21", layout="wide")

def get_connection():
    # 建议维持现有的数据库文件名
    return sqlite3.connect(pathlib.Path(__file__).with_name("stock_data_v12.db"), check_same_thread=False)

conn = get_connection()
c = conn.cursor()

# --- 核心：数据库表结构自动升级逻辑 ---
# 这样即使你的数据库是旧版的，也会自动增加缺失的“日期”列
c.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT, action TEXT, price REAL, quantity INTEGER)')
c.execute('CREATE TABLE IF NOT EXISTS prices (code TEXT PRIMARY KEY, current_price REAL)')
c.execute('CREATE TABLE IF NOT EXISTS signals (code TEXT PRIMARY KEY, high_point REAL, low_point REAL, up_threshold REAL, down_threshold REAL)')
c.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, stock_name TEXT, content TEXT)')

# 检查并添加缺失的日期列（防止 OperationalError）
try:
    c.execute("ALTER TABLE signals ADD COLUMN high_date TEXT")
    c.execute("ALTER TABLE signals ADD COLUMN low_date TEXT")
except sqlite3.OperationalError:
    pass # 如果列已经存在，直接跳过
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

# --- 2. 侧边栏导航 ---
menu = ["📊 实时持仓", "💰 盈利账单", "📝 交易录入", "🔔 买卖信号", "📜 历史明细", "📓 复盘日记"]
choice = st.sidebar.radio("功能导航", menu)
stored_prices = dict(c.execute("SELECT code, current_price FROM prices").fetchall())

# --- 3. 实时持仓 (完整替换版) ---
if choice == "📊 实时持仓":
    st.header("📊 持仓盈亏分析")
    
    # 获取原始交易数据
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    
    if not df_trades.empty:
        stocks = df_trades['code'].unique()
        
        # --- 顶部：现价更新区 ---
        with st.expander("🛠️ 快速更新现价", expanded=True):
            cols = st.columns(3)
            for i, stock in enumerate(stocks):
                old_p = stored_prices.get(stock, 0.0)
                new_p = cols[i%3].number_input(f"{stock} 现价", value=float(old_p), key=f"p_{stock}", step=0.01)
                if new_p != old_p:
                    c.execute("INSERT OR REPLACE INTO prices (code, current_price) VALUES (?, ?)", (stock, new_p))
                    conn.commit()
        
        # 获取最新存储的价格
        latest_prices = dict(c.execute("SELECT code, current_price FROM prices").fetchall())
        
        summary = []           # 用于存放“账户持仓概览”
        all_active_records = [] # 用于存放“多笔活跃单”

        # --- 核心逻辑：基于价格优化的对冲计算 ---
        for stock in stocks:
            s_df = df_trades[df_trades['code'] == stock].copy()
            now_p = latest_prices.get(stock, 0.0)
            
            # 1. 提取买卖池
            # 这里的排序是关键：买入按价格升序（低价在前），卖出按价格降序（高价在前）
            buys = s_df[s_df['action'] == '买入'].sort_values('price', ascending=True).to_dict('records')
            sells = s_df[s_df['action'] == '卖出'].sort_values('price', ascending=False).to_dict('records')
            
            # 2. 执行双向对冲过程
            # 先用所有的卖单去抵消低价的买单
            temp_sells = [dict(s) for s in sells]
            for s in temp_sells:
                s_qty = s['quantity']
                for b in buys:
                    if b['quantity'] > 0 and s_qty > 0:
                        take = min(b['quantity'], s_qty)
                        b['quantity'] -= take
                        s_qty -= take
                s['quantity'] = s_qty # 更新卖单剩余量

            # 3. 提取未被平仓的记录
            remaining_buys = [b for b in buys if b['quantity'] > 0]
            remaining_sells = [s for s in temp_sells if s['quantity'] > 0]

            # 4. 计算概览汇总 (Summary)
            net_q = sum(b['quantity'] for b in remaining_buys) - sum(s['quantity'] for s in remaining_sells)
            
            if net_q != 0:
                if net_q > 0: # 多头持仓
                    avg_p = sum(b['price'] * b['quantity'] for b in remaining_buys) / net_q
                    p_rate = ((now_p - avg_p) / avg_p * 100) if avg_p > 0 else 0
                else: # 空头持仓
                    avg_p = sum(s['price'] * s['quantity'] for s in remaining_sells) / abs(net_q)
                    p_rate = ((avg_p - now_p) / avg_p * 100) if avg_p > 0 else 0
                
                summary.append([stock, net_q, f"{avg_p:.2f}", f"{now_p:.2f}", f"{p_rate:.2f}%", p_rate])

            # 5. 构造详细的活跃单追踪列表
            for b in remaining_buys:
                gain = ((now_p - b['price']) / b['price'] * 100)
                all_active_records.append({
                    "date": b['date'], "code": stock, "type": "买入持有", 
                    "price": b['price'], "qty": b['quantity'], "gain_str": f"{gain:.2f}%", "gain_val": gain
                })
            for s in remaining_sells:
                gain = ((s['price'] - now_p) / s['price'] * 100)
                all_active_records.append({
                    "date": s['date'], "code": stock, "type": "卖空持有", 
                    "price": s['price'], "qty": s['quantity'], "gain_str": f"{gain:.2f}%", "gain_val": gain
                })

        # --- 渲染界面 1：账户持仓概览 ---
        st.subheader("1️⃣ 账户持仓概览 (盈利最高优先)")
        if summary:
            # 按盈亏比例排序
            summary.sort(key=lambda x: x[5], reverse=True)
            html = '<table class="custom-table"><thead><tr><th>代码</th><th>净持仓</th><th>成本</th><th>现价</th><th>盈亏</th></tr></thead><tbody>'
            for r in summary:
                c_class = "profit-red" if r[5] > 0 else "loss-green" if r[5] < 0 else ""
                html += f'<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td class="{c_class}">{r[4]}</td></tr>'
            st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
        else:
            st.info("目前没有持仓。")

        # --- 渲染界面 2：多笔活跃单追踪 ---
        st.write("---")
        st.subheader("2️⃣ 多笔未平仓活跃单 (最优价格匹配)")
        
        if all_active_records:
            # 筛选器
            active_codes = sorted(list(set([r['code'] for r in all_active_records])))
            col_f1, col_f2 = st.columns([1, 2])
            selected_stocks = col_f1.multiselect("🔍 筛选股票", options=active_codes, placeholder="全部显示")
            
            # 应用筛选
            display_data = all_active_records
            if selected_stocks:
                display_data = [r for r in all_active_records if r['code'] in selected_stocks]
            
            # 排序：盈利最高优先
            display_data.sort(key=lambda x: x['gain_val'], reverse=True)

            # HTML 表格渲染
            html = '<table class="custom-table"><thead><tr><th>买入/卖出日期</th><th>股票</th><th>类型</th><th>成交单价</th><th>剩余数量</th><th>单笔盈亏</th></tr></thead><tbody>'
            for r in display_data:
                c_class = "profit-red" if r['gain_val'] > 0 else "loss-green" if r['gain_val'] < 0 else ""
                html += f'''<tr>
                    <td>{r['date']}</td>
                    <td>{r['code']}</td>
                    <td>{r['type']}</td>
                    <td>{r['price']:.2f}</td>
                    <td>{r['qty']}</td>
                    <td class="{c_class}">{r['gain_str']}</td>
                </tr>'''
            st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
            st.caption("注：系统已自动为您平掉利润空间最大的订单。")
        else:
            st.info("暂无未平仓的详细单据。")

    else:
        st.info("欢迎使用！请先在‘交易录入’菜单中添加您的第一笔交易。")

# --- 4. 盈利账单 (回归总额对冲逻辑版) ---
elif choice == "💰 盈利账单":
    st.header("💰 盈利账单 (总额对冲法)")
    
    df_trades = pd.read_sql("SELECT * FROM trades", conn)
    latest_prices = dict(c.execute("SELECT code, current_price FROM prices").fetchall())
    
    if not df_trades.empty:
        profit_list = []
        for stock in df_trades['code'].unique():
            s_df = df_trades[df_trades['code'] == stock]
            now_p = latest_prices.get(stock, 0.0)
            
            # 核心逻辑：钱的进出总额
            total_buy_cash = s_df[s_df['action'] == '买入'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            total_sell_cash = s_df[s_df['action'] == '卖出'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            
            # 计算剩余持仓数量
            net_q = s_df[s_df['action'] == '买入']['quantity'].sum() - s_df[s_df['action'] == '卖出']['quantity'].sum()
            current_value = net_q * now_p if net_q > 0 else 0
            
            # 总贡献 = (卖出的钱 + 手里剩下的钱) - 买入花的钱
            total_profit = (total_sell_cash + current_value) - total_buy_cash
            
            profit_list.append({
                "股票名称": stock,
                "累计投入": round(total_buy_cash, 2),
                "累计回收": round(total_sell_cash, 2),
                "剩余持仓市值": round(current_value, 2),
                "总盈亏": round(total_profit, 2)
            })

        pdf = pd.DataFrame(profit_list).sort_values(by="总盈亏", ascending=False)
        
        # 数据看板
        st.divider()
        st.metric("账户总体贡献", f"{pdf['总盈亏'].sum():,.2f}")
        st.divider()

        # HTML 渲染表格
        html = '<table class="custom-table"><thead><tr><th>股票名称</th><th>累计投入</th><th>累计回收</th><th>持仓市值</th><th>总盈亏</th></tr></thead><tbody>'
        for _, r in pdf.iterrows():
            c_class = "profit-red" if r['总盈亏'] > 0 else "loss-green" if r['总盈亏'] < 0 else ""
            html += f'''<tr>
                <td>{r['股票名称']}</td>
                <td>{r['累计投入']:,.2f}</td>
                <td>{r['累计回收']:,.2f}</td>
                <td>{r['剩余持仓市值']:,.2f}</td>
                <td class="{c_class}">{r['总盈亏']:,.2f}</td>
            </tr>'''
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
    else:
        st.info("暂无数据。")

# --- 5. 交易录入 ---
elif choice == "📝 交易录入":
    st.header("📝 交易录入")
    full_list = get_dynamic_stock_list()
    t_code = st.selectbox("选择股票", options=["【添加新股票】"] + full_list, index=None, placeholder="请选择...")
    final_code = st.text_input("新股票名") if t_code == "【添加新股票】" else t_code
    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        d, a = c1.date_input("日期", datetime.now()), c2.selectbox("操作", ["买入", "卖出"])
        p = c1.number_input("单价", value=None, min_value=0.0, step=0.001, placeholder="输入单价")
        q = c2.number_input("数量", value=None, min_value=1, step=1, placeholder="输入数量")
        if st.form_submit_button("保存"):
            if final_code and p and q:
                c.execute("INSERT INTO trades (date, code, action, price, quantity) VALUES (?,?,?,?,?)", (d.strftime('%Y-%m-%d'), final_code, a, p, q))
                conn.commit(); st.rerun()

# --- 6. 买卖信号 (修改重点：日期追踪 + 去除预设) ---
elif choice == "🔔 买卖信号":
    st.header("🔔 策略监控信号")
    with st.expander("➕ 设置新监控 (无预设值版)", expanded=True):
        s_code = st.selectbox("1. 监控股票", options=get_dynamic_stock_list(), index=None, placeholder="请选择...")
        c1, c2 = st.columns(2)
        s_high = c1.number_input("2. 高点参考价", value=None, min_value=0.0, step=0.01, placeholder="输入最高价")
        h_date = c1.date_input("3. 高点日期", value=None)
        s_low = c2.number_input("4. 低点参考价", value=None, min_value=0.0, step=0.01, placeholder="输入最低价")
        l_date = c2.date_input("5. 低点日期", value=None)
        s_up = c1.number_input("6. 上涨触发 (%)", value=None, min_value=0.0, placeholder="如 5.0")
        s_down = c2.number_input("7. 回调触发 (%)", value=None, min_value=0.0, placeholder="如 5.0")
        
        if st.button("🚀 启动监控"):
            if all([s_code, s_high, s_low, s_up, s_down]):
                h_date_s = h_date.strftime('%Y-%m-%d') if h_date else "未记录"
                l_date_s = l_date.strftime('%Y-%m-%d') if l_date else "未记录"
                c.execute("""INSERT OR REPLACE INTO signals 
                          (code, high_point, high_date, low_point, low_date, up_threshold, down_threshold) 
                          VALUES (?,?,?,?,?,?,?)""", 
                          (s_code, s_high, h_date_s, s_low, l_date_s, s_up, s_down))
                conn.commit(); st.success(f"✅ {s_code} 已启动"); st.rerun()
            else: st.error("❌ 请完整填写参数")

    sig_df = pd.read_sql("SELECT * FROM signals", conn)
    if not sig_df.empty:
        html = '<table class="custom-table"><thead><tr><th>代码</th><th>高点(日期)</th><th>低点(日期)</th><th>距高点</th><th>距低点</th><th>建议</th></tr></thead><tbody>'
        for _, r in sig_df.iterrows():
            np = stored_prices.get(r['code'], 0.0)
            dr = ((np - r['high_point']) / r['high_point'] * 100) if r['high_point'] > 0 else 0
            rr = ((np - r['low_point']) / r['low_point'] * 100) if r['low_point'] > 0 else 0
            st_text = "🟢 建议卖出" if rr >= r['up_threshold'] else "🔴 建议买入" if dr <= -r['down_threshold'] else "⚖️ 观望"
            html += f"<tr><td>{r['code']}</td><td>{r['high_point']:.2f}<br><small>{r['high_date']}</small></td><td>{r['low_point']:.2f}<br><small>{r['low_date']}</small></td><td>{dr:.2f}%</td><td>{rr:.2f}%</td><td>{st_text}</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)
        if st.button("🗑️ 清空监控"): c.execute("DELETE FROM signals"); conn.commit(); st.rerun()

# --- 7. 历史明细 (HTML 视图版 - 修复显示问题) ---
elif choice == "📜 历史明细":
    st.header("📜 历史交易流水")

    # 1. 重新注入 CSS 样式（确保样式在当前页面生效）
    st.markdown("""
        <style>
        .history-table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 14px; border-radius: 8px; overflow: hidden; box-shadow: 0 0 10px rgba(0,0,0,0.05); }
        .history-table thead tr { background-color: #4A5568; color: #ffffff; text-align: center; }
        .history-table th, .history-table td { padding: 12px 15px; text-align: center; border-bottom: 1px solid #edf2f7; }
        .history-table tbody tr:nth-of-type(even) { background-color: #f7fafc; }
        .action-buy { color: #e53e3e; font-weight: bold; background-color: #fff5f5; border-radius: 4px; padding: 4px 8px; }
        .action-sell { color: #38a169; font-weight: bold; background-color: #f0fff4; border-radius: 4px; padding: 4px 8px; }
        </style>
    """, unsafe_allow_html=True)

    # 2. 获取数据并执行初步搜索
    df_h = pd.read_sql("SELECT * FROM trades ORDER BY date DESC, id DESC", conn)

    if not df_h.empty:
        # 顶部搜索功能
        search_code = st.text_input("🔍 搜索股票代码", placeholder="输入代码筛选历史记录...")
        if search_code:
            df_h = df_h[df_h['code'].str.contains(search_code, case=False)]

        # 3. 手动构建 HTML 字符串
        # 这种方式能彻底去除滚动条并实现你想要的“持仓概览”式美感
        html_content = '<table class="history-table"><thead><tr>'
        html_content += '<th>交易日期</th><th>股票代码</th><th>操作类型</th><th>成交价格</th><th>成交数量</th><th>交易总额</th>'
        html_content += '</tr></thead><tbody>'
        
        for _, r in df_h.iterrows():
            # 逻辑判定
            action_tag = f'<span class="action-buy">{r["action"]}</span>' if r['action'] == "买入" else f'<span class="action-sell">{r["action"]}</span>'
            total_cost = r['price'] * r['quantity']
            
            html_content += '<tr>'
            html_content += f'<td>{r["date"]}</td>'
            html_content += f'<td><b>{r["code"]}</b></td>'
            html_content += f'<td>{action_tag}</td>'
            html_content += f'<td>{r["price"]:.3f}</td>'
            html_content += f'<td>{int(r["quantity"])}</td>'
            html_content += f'<td>{total_cost:,.2f}</td>'
            html_content += '</tr>'
        
        html_content += '</tbody></table>'
        
        # 4. 使用 unsafe_allow_html=True 渲染，否则会显示为纯文本源码
        st.markdown(html_content, unsafe_allow_html=True)
        
        # 5. 隐藏的维护功能
        st.write("---")
        with st.expander("🛠️ 数据库维护 (如需修改或删除记录)"):
            st.info("提示：在此处修改数据后点击下方保存，HTML 视图将同步更新。")
            # 动态计算高度以防此处的编辑器也产生滚动条
            ed_height = (len(df_h) + 1) * 35 + 3
            ed_df = st.data_editor(df_h, use_container_width=True, num_rows="dynamic", hide_index=True, column_config={"id": None}, height=ed_height)
            if st.button("💾 提交并刷新视图"):
                ed_df.to_sql('trades', conn, if_exists='replace', index=False)
                st.success("数据库已成功同步！")
                st.rerun()
    else:
        st.info("暂无历史明细数据。")
# --- 8. 复盘日记 (美化排版) ---
elif choice == "📓 复盘日记":
    st.header("📓 每日复盘")
    ds = st.selectbox("复盘对象", ["大盘"] + get_dynamic_stock_list(), index=None, placeholder="请选择记录对象...")
    cont = st.text_area("心得内容", placeholder="记录今日操作逻辑或市场观察...", height=150)
    
    if st.button("🚀 提交存档"):
        if ds and cont:
            c.execute("INSERT INTO journal (date, stock_name, content) VALUES (?,?,?)", 
                      (datetime.now().strftime('%Y-%m-%d'), ds, cont))
            conn.commit()
            st.success("✅ 复盘内容已保存")
            st.rerun()
        else:
            st.warning("⚠️ 请选择对象并填写内容")

    st.divider()
    # 使用卡片式布局展示历史日记
    journal_df = pd.read_sql("SELECT * FROM journal ORDER BY date DESC", conn)
    for _, r in journal_df.iterrows():
        with st.chat_message("user"): # 借用对话框样式作为卡片
            st.write(f"**{r['date']} | {r['stock_name']}**")
            st.write(r['content'])
