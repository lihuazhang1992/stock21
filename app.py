# --- 持仓全景洞察 ---
elif choice == "📊 持仓全景洞察":
    st.header("📊 持仓全景洞察")
    
    # ========== 数据库表结构升级 ==========
    def ensure_analytics_tables():
        c.execute("""
            CREATE TABLE IF NOT EXISTS trading_logic (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                action TEXT,
                logic TEXT,
                decision_date TEXT,
                reason TEXT,
                FOREIGN KEY(code) REFERENCES trades(code)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS peak_holdings (
                code TEXT PRIMARY KEY,
                peak_amount REAL,
                peak_date TEXT,
                FOREIGN KEY(code) REFERENCES trades(code)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cycle_analysis (
                code TEXT PRIMARY KEY,
                avg_up_pct REAL,
                avg_down_pct REAL,
                total_cycles INTEGER,
                FOREIGN KEY(code) REFERENCES trades(code)
            )
        """)
        conn.commit()
    
    ensure_analytics_tables()

    # ========== 辅助函数 ==========
    def calculate_peak_amount(code):
        current_holdings = c.execute("""
            SELECT SUM(quantity * price) as current_value 
            FROM trades 
            WHERE code = ? AND action = '买入' 
            GROUP BY code
        """, (code,)).fetchone()
        
        if current_holdings and current_holdings[0]:
            return round(current_holdings[0], 2)
        return 0.0

    def record_peak(code, current_amount):
        current_peak = c.execute("""
            SELECT peak_amount FROM peak_holdings 
            WHERE code = ? 
            ORDER BY peak_date DESC LIMIT 1
        """, (code,)).fetchone()
        
        if not current_peak or current_amount > current_peak[0]:
            c.execute("""
                INSERT INTO peak_holdings (code, peak_amount, peak_date) 
                VALUES (?, ?, ?)
            """, (code, current_amount, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()

    def analyze_cycles(code):
        df = pd.read_sql(f"""
            SELECT date, price, action 
            FROM trades 
            WHERE code = ? 
            ORDER BY date ASC
        """, conn, params=(code,))
        
        cycles = []
        current_cycle = []
        prev_action = None
        
        for _, row in df.iterrows():
            if prev_action is None:
                prev_action = row['action']
                current_cycle.append(row)
            else:
                if row['action'] != prev_action:
                    cycles.append(current_cycle)
                    current_cycle = [row]
                    prev_action = row['action']
                else:
                    current_cycle.append(row)
        
        if current_cycle:
            cycles.append(current_cycle)
        
        stats = {'avg_up': [], 'avg_down': []}
        for cycle in cycles:
            if len(cycle) < 2:
                continue
            start_price = cycle[0]['price']
            end_price = cycle[-1]['price']
            change = (end_price - start_price) / start_price * 100
            
            if cycle[0]['action'] == '买入':
                stats['avg_up'].append(change)
            else:
                stats['avg_down'].append(change)
        
        avg_up = round(sum(stats['avg_up']) / len(stats['avg_up']), 2) if stats['avg_up'] else 0
        avg_down = round(sum(stats['avg_down']) / len(stats['avg_down']), 2) if stats['avg_down'] else 0
        
        c.execute("""
            UPDATE cycle_analysis 
            SET avg_up_pct = ?, avg_down_pct = ?, total_cycles = ?
            WHERE code = ?
        """, (avg_up, avg_down, len(cycles), code))
        conn.commit()

    # ========== 数据同步 ==========
    def sync_analytics():
        for code in get_dynamic_stock_list():
            current_holdings = calculate_peak_amount(code)
            record_peak(code, current_holdings)
            analyze_cycles(code)
    
    thread = threading.Thread(target=sync_analytics, daemon=True)
    thread.start()

    # ========== 核心展示逻辑 ==========
    st.subheader("📈 持仓概览")
    df_trades = pd.read_sql("""
        SELECT 
            t.code,
            SUM(CASE WHEN action='买入' THEN quantity ELSE 0 END) as buy_qty,
            SUM(CASE WHEN action='卖出' THEN quantity ELSE 0 END) as sell_qty,
            MAX(CASE WHEN action='卖出' THEN price ELSE 0 END) as last_sell_price,
            MAX(CASE WHEN action='买入' THEN price ELSE 0 END) as last_buy_price,
            p.current_price,
            p.manual_cost
        FROM trades t
        LEFT JOIN prices p ON t.code = p.code
        GROUP BY t.code
    """, conn)

    if not df_trades.empty:
        df_trades['net_qty'] = df_trades['buy_qty'] - df_trades['sell_qty']
        df_trades['cost_price'] = df_trades.apply(
            lambda row: (row['last_buy_price'] * row['buy_qty'] - 
                         row['last_sell_price'] * row['sell_qty']) / 
                        (row['buy_qty'] - row['sell_qty']) 
            if row['net_qty'] !=0 else 0, axis=1
        )
        df_trades['market_value'] = df_trades['current_price'] * df_trades['net_qty']
        df_trades['unrealized_pnl'] = (df_trades['current_price'] - df_trades['cost_price']) * df_trades['net_qty']
        df_trades['realized_pnl'] = (df_trades['last_sell_price'] - df_trades['last_buy_price']) * df_trades['sell_qty']
        df_trades['peak_amount'] = df_trades['code'].apply(calculate_peak_amount)
        df_trades['profit_ratio'] = (df_trades['current_price'] / df_trades['cost_price'] - 1) * 100
        
        df_trades = df_trades.sort_values(by='market_value', ascending=False)
        
        html = '<table class="custom-table"><thead><tr>'
        html += ''.join([f'<th>{col}</th>' for col in [
            '股票代码', '持仓数量', '成本价', '现价', '市值',
            '未实现盈亏', '盈亏比例', '最高持仓额', '交易逻辑', '决策历史'
        ]])
        html += '</tr></thead><tbody>'
        
        for _, row in df_trades.iterrows():
            logic_html = "<div style='padding:5px;background:#2c3e50;color:white;border-radius:4px;margin-bottom:4px;'>"
            logic_html += c.execute("""
                SELECT reason, decision_date 
                FROM trading_logic 
                WHERE code = ? 
                ORDER BY decision_date DESC
            """, (row['code'],)).fetchall()
            logic_html += "</div>"
            
            html += f"""
            <tr>
                <td>{row['code']}</td>
                <td>{int(row['net_qty'])}</td>
                <td>{row['cost_price']:.3f}</td>
                <td>{row['current_price']:.3f}</td>
                <td>{row['market_value']:.2f}</td>
                <td>{row['unrealized_pnl']:.2f}</td>
                <td>{row['profit_ratio']:.2f}%</td>
                <td>{row['peak_amount']:.2f}</td>
                <td>{logic_html}</td>
                <td>
                    <button onclick="toggleDecisionHistory('{row['code']}')">查看决策历史</button>
                    <div id="decision_history_{row['code']}" style="display:none;">
                        {c.execute("""
                            SELECT action, decision_date, reason 
                            FROM trading_logic 
                            WHERE code = ? 
                            ORDER BY decision_date DESC
                        """, (row['code'],)).fetchall()}
                    </div>
                </td>
            </tr>
            """
        html += '</tbody></table>'
        st.markdown(html, unsafe_allow_html=True)
        
        st.write("""
            <style>
            .custom-table td button {{
                background: #3498db;
                color: white;
                padding: 4px 8px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
            }}
            .custom-table td div {{
                margin-top: 4px;
                padding: 4px;
                border: 1px solid #bdc3c7;
                border-radius: 4px;
            }}
            </style>
            """, unsafe_allow_html=True)
        
        st.subheader("🔄 周期分析")
        cycle_analysis = c.execute("""
            SELECT 
                code,
                avg_up_pct,
                avg_down_pct,
                total_cycles
            FROM cycle_analysis
            ORDER BY total_cycles DESC
        """).fetchall()
        
        html_cycle = '<table class="custom-table"><thead><tr>'
        html_cycle += ''.join([f'<th>{col}</th>' for col in [
            '股票代码', '平均涨幅', '平均跌幅', '完整周期数'
        ]])
        html_cycle += '</tr></thead><tbody>'
        
        for row in cycle_analysis:
            html_cycle += f"<tr><td>{row[0]}</td><td>{row[1]:.2f}%</td><td>{row[2]:.2f}%</td><td>{row[3]}</td></tr>"
        html_cycle += '</tbody></table>'
        st.markdown(html_cycle, unsafe_allow_html=True)
        
        st.write("""
            <style>
            .custom-table td {{
                text-align: center;
                padding: 8px;
            }}
            </style>
            """, unsafe_allow_html=True)
    else:
        st.info("请先录入交易记录")

# 新增辅助函数
def record_trading_logic():
    with st.form("trading_logic_form"):
        selected_code = st.selectbox("选择股票", get_dynamic_stock_list())
        action = st.selectbox("操作类型", ["买入", "卖出"])
        logic_reason = st.text_area("决策逻辑")
        decision_date = st.date_input("决策日期", datetime.now())
        
        if st.form_submit_button("记录决策"):
            c.execute("""
                INSERT INTO trading_logic 
                (code, action, logic, decision_date) 
                VALUES (?, ?, ?, ?)
            """, (selected_code, action, logic_reason, decision_date.strftime('%Y-%m-%d')))
            conn.commit()
            thread = threading.Thread(target=sync_db_to_github, daemon=True)
            thread.start()
            st.success("决策记录已保存")

# 在侧边栏添加入口
st.sidebar.markdown("""
    <div style="padding: 10px; background-color: #f0f2f6; border-radius: 8px;">
        <h4 style="color: #2c3e50;">持仓分析工具</h4>
        <button onclick="toggleAnalysisTools()">展开工具</button>
    </div>
""", unsafe_allow_html=True)

# 隐藏的分析工具入口
analysis_expander = st.sidebar.expander("", expanded=False)
with analysis_expander:
    record_trading_logic()