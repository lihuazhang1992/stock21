import sys
import os

def update_app():
    with open('/home/ubuntu/upload/app.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 1. 在数据库初始化部分添加新表
    new_tables = """
c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_notes (
        code TEXT PRIMARY KEY,
        logic TEXT,
        max_holding_amount REAL DEFAULT 0.0
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
"""
    
    insert_idx = -1
    for i, line in enumerate(lines):
        if "CREATE TABLE IF NOT EXISTS journal" in line:
            for j in range(i, len(lines)):
                if "''')" in lines[j] or '""")' in lines[j]:
                    insert_idx = j + 1
                    break
            break
    
    if insert_idx != -1:
        lines.insert(insert_idx, new_tables)

    # 2. 在菜单中添加新选项
    for i, line in enumerate(lines):
        if 'menu = ["📊 实时持仓"' in line:
            lines[i] = line.replace('menu = [', 'menu = ["📈 策略复盘", ')
            break

    # 3. 编写新模块内容
    # 注意：我们将第一个模块改为 if，后续的改为 elif
    new_module_code = """
# --- 📈 策略复盘 ---
if choice == "📈 策略复盘":
    st.header("📈 策略复盘与深度账本")
    
    all_stocks = get_dynamic_stock_list()
    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC", conn)
    latest_prices = {row[0]: row[1] for row in c.execute("SELECT code, current_price FROM prices").fetchall()}
    
    summary_data = []
    for stock in all_stocks:
        s_df = df_trades[df_trades['code'] == stock]
        now_p = latest_prices.get(stock, 0.0)
        
        net_buy_q = s_df[s_df['action'] == '买入']['quantity'].sum()
        net_sell_q = s_df[s_df['action'] == '卖出']['quantity'].sum()
        net_q = net_buy_q - net_sell_q
        
        total_cost_spent = 0
        remaining_q = 0
        for _, t in s_df.iterrows():
            if t['action'] == '买入':
                total_cost_spent += t['price'] * t['quantity']
                remaining_q += t['quantity']
            else:
                if remaining_q > 0:
                    avg_cost = total_cost_spent / remaining_q
                    total_cost_spent -= avg_cost * t['quantity']
                    remaining_q -= t['quantity']
        
        avg_cost = total_cost_spent / net_q if net_q > 0 else 0
        market_val = net_q * now_p
        
        realized_profit = 0
        temp_buy_pool = []
        for _, t in s_df.iterrows():
            if t['action'] == '买入':
                temp_buy_pool.append({'price': t['price'], 'q': t['quantity']})
            else:
                sell_q = t['quantity']
                while sell_q > 0 and temp_buy_pool:
                    if temp_buy_pool[0]['q'] <= sell_q:
                        realized_profit += (t['price'] - temp_buy_pool[0]['price']) * temp_buy_pool[0]['q']
                        sell_q -= temp_buy_pool[0]['q']
                        temp_buy_pool.pop(0)
                    else:
                        realized_profit += (t['price'] - temp_buy_pool[0]['price']) * sell_q
                        temp_buy_pool[0]['q'] -= sell_q
                        sell_q = 0
        
        holding_profit_amount = (now_p - avg_cost) * net_q if net_q > 0 else 0
        holding_profit_pct = ((now_p - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0
        
        history_amounts = []
        curr_q = 0
        for _, t in s_df.iterrows():
            if t['action'] == '买入': curr_q += t['quantity']
            else: curr_q -= t['quantity']
            history_amounts.append(curr_q * t['price'])
        max_holding_val = max(history_amounts) if history_amounts else 0
        
        if not s_df.empty:
            start_date = pd.to_datetime(s_df['date'].min())
            days = (datetime.now() - start_date).days
            total_profit = realized_profit + holding_profit_amount
            total_invested = s_df[s_df['action'] == '买入'].apply(lambda r: r['price'] * r['quantity'], axis=1).sum()
            annual_return = (total_profit / total_invested) / (days / 365) * 100 if days > 0 and total_invested > 0 else 0
        else:
            annual_return = 0
            
        summary_data.append({
            "股票": stock, "持仓数量": net_q, "持仓市值": market_val, "成本价": avg_cost, 
            "现价": now_p, "已实现利润": realized_profit, "持仓盈亏比": holding_profit_pct,
            "持仓盈亏额": holding_profit_amount, "历史最高持仓": max_holding_val, "年化收益": annual_return
        })

    st.subheader("📊 核心持仓与收益统计")
    sdf = pd.DataFrame(summary_data)
    if not sdf.empty:
        html = '<table class="custom-table"><thead><tr><th>股票</th><th>数量</th><th>市值</th><th>成本/现价</th><th>盈亏比</th><th>盈亏额</th><th>已实现利润</th><th>最高持仓</th><th>年化</th></tr></thead><tbody>'
        for _, r in sdf.iterrows():
            p_class = "profit-red" if r['持仓盈亏额'] >= 0 else "loss-green"
            html += f"<tr><td>{r['股票']}</td><td>{int(r['持仓数量'])}</td><td>{r['持仓市值']:,.2f}</td><td>{r['成本价']:.3f}<br>{r['现价']:.3f}</td><td class='{p_class}'>{r['持仓盈亏比']:.2f}%</td><td class='{p_class}'>{r['持仓盈亏额']:,.2f}</td><td>{r['已实现利润']:,.2f}</td><td>{r['历史最高持仓']:,.2f}</td><td>{r['年化收益']:.2f}%</td></tr>"
        st.markdown(html + '</tbody></table>', unsafe_allow_html=True)

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🧠 交易逻辑与最高峰值")
        sel_s = st.selectbox("选择股票", all_stocks, key="logic_s")
        if sel_s:
            logic_data = c.execute("SELECT logic, max_holding_amount FROM strategy_notes WHERE code = ?", (sel_s,)).fetchone()
            curr_logic = logic_data[0] if logic_data else ""
            curr_max = logic_data[1] if logic_data else 0.0
            with st.form("logic_form"):
                new_logic = st.text_area("交易逻辑 (买卖原则)", value=curr_logic, height=100)
                new_max = st.number_input("手动记录最高持仓金额", value=float(curr_max))
                if st.form_submit_button("保存逻辑"):
                    c.execute("INSERT OR REPLACE INTO strategy_notes (code, logic, max_holding_amount) VALUES (?,?,?)", (sel_s, new_logic, new_max))
                    conn.commit()
                    st.success("逻辑已保存")
                    st.rerun()
    with col2:
        st.subheader("📜 决策历史记录")
        if sel_s:
            with st.expander("➕ 新增决策记录"):
                with st.form("decision_form", clear_on_submit=True):
                    d_date = st.date_input("日期", datetime.now())
                    d_action = st.text_input("决策内容", placeholder="如：减仓50%")
                    d_reason = st.text_area("决策原因")
                    if st.form_submit_button("提交决策"):
                        c.execute("INSERT INTO decision_history (code, date, decision, reason) VALUES (?,?,?,?)", (sel_s, d_date.strftime('%Y-%m-%d'), d_action, d_reason))
                        conn.commit()
                        st.success("决策已记录")
                        st.rerun()
            h_df = pd.read_sql("SELECT date, decision, reason FROM decision_history WHERE code = ? ORDER BY date DESC", conn, params=(sel_s,))
            for _, row in h_df.iterrows():
                st.markdown(f"**{row['date']} | {row['decision']}**")
                st.caption(row['reason'])
                st.markdown("---")
    st.divider()
    st.subheader("📉 历史涨跌周期统计")
    if sel_s:
        c1, c2 = st.columns([1, 2])
        with c1:
            with st.form("cycle_form", clear_on_submit=True):
                st.write("新增周期")
                c_start = st.date_input("开始日期")
                c_end = st.date_input("结束日期")
                c_pct = st.number_input("涨跌幅 (%)", step=0.01)
                if st.form_submit_button("记录周期"):
                    c.execute("INSERT INTO price_cycles (code, start_date, end_date, change_pct) VALUES (?,?,?,?)", (sel_s, c_start.strftime('%Y-%m-%d'), c_end.strftime('%Y-%m-%d'), c_pct))
                    conn.commit()
                    st.rerun()
        with c2:
            cycles = pd.read_sql("SELECT * FROM price_cycles WHERE code = ? ORDER BY start_date DESC", conn, params=(sel_s,))
            if not cycles.empty:
                up_avg = cycles[cycles['change_pct'] > 0]['change_pct'].mean()
                down_avg = cycles[cycles['change_pct'] < 0]['change_pct'].mean()
                st.write(f"📈 平均涨幅: {up_avg:.2f}% | 📉 平均跌幅: {down_avg:.2f}%")
                for _, row in cycles.iterrows():
                    color = "#d32f2f" if row['change_pct'] > 0 else "#388e3c"
                    st.markdown(f"`{row['start_date']} → {row['end_date']}` <span style='color:{color}; font-weight:bold;'>({row['change_pct']:+.2f}%)</span>", unsafe_allow_html=True)
            else:
                st.info("暂无周期数据")
"""
    
    insert_module_idx = -1
    for i, line in enumerate(lines):
        if 'if choice == "📊 实时持仓":' in line:
            insert_module_idx = i
            # 将原来的 if 改为 elif
            lines[i] = line.replace('if choice == "📊 实时持仓":', 'elif choice == "📊 实时持仓":')
            break
            
    if insert_module_idx != -1:
        lines.insert(insert_module_idx, new_module_code)

    with open('/home/ubuntu/app_updated.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("Update successful: /home/ubuntu/app_updated.py created.")

if __name__ == "__main__":
    update_app()
