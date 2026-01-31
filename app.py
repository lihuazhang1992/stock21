import pathlib
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from github import Github, GithubException

# --- 基础配置 ---
st.set_page_config(page_title="股票管理系统 v22.1", layout="wide")

def get_connection():
    return sqlite3.connect(pathlib.Path(__file__).with_name("stock_data_v12.db"), check_same_thread=False)

conn = get_connection()
c = conn.cursor()

# --- 创建/升级表结构 ---
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

try: c.execute("ALTER TABLE prices ADD COLUMN manual_cost REAL DEFAULT 0.0"); conn.commit()
except: pass
try: c.execute("ALTER TABLE trades ADD COLUMN note TEXT"); conn.commit()
except: pass

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

# --- 同步数据库到 GitHub ---
def sync_db_to_github():
    db_filename = "stock_data_v12.db"
    local_path = pathlib.Path(__file__).with_name(db_filename)
    if not local_path.exists():
        return

    try:
        token = st.secrets["GITHUB_TOKEN"]
        owner = st.secrets["REPO_OWNER"]
        repo_name = st.secrets["REPO_NAME"]

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")

        with open(local_path, "rb") as f:
            content = f.read()

        commit_msg = f"Auto sync {db_filename} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        try:
            file = repo.get_contents(db_filename)
            repo.update_file(db_filename, commit_msg, content, file.sha, branch="main")
        except GithubException as e:
            if e.status == 404:
                repo.create_file(db_filename, commit_msg, content, branch="main")
    except Exception as e:
        st.error(f"同步失败：{str(e)}")

# --- 侧边栏菜单（去掉 emoji 测试兼容性） ---
menu = [
    "实时持仓",
    "盈利账单",
    "价格目标管理",
    "交易录入",
    "买卖信号",
    "历史明细",
    "复盘日记"
]
choice = st.sidebar.radio("功能导航", menu)

# 调试输出：显示当前 choice 值
st.sidebar.write("当前选择：", choice)

# --- 主内容区 ---
if choice == "实时持仓":
    st.header("实时持仓")
    st.write("这里是持仓页面 - 如果看到这行说明分支正常执行")

    df_trades = pd.read_sql("SELECT * FROM trades ORDER BY date ASC, id ASC", conn)
    if df_trades.empty:
        st.info("暂无交易记录")
    else:
        st.write("有交易数据，共", len(df_trades), "条")

elif choice == "盈利账单":
    st.header("盈利账单")
    st.write("盈利账单页面")

elif choice == "价格目标管理":
    st.header("价格目标管理")
    st.write("价格目标页面")

elif choice == "交易录入":
    st.header("交易录入")
    st.write("交易录入页面 - 请测试是否能看到输入框")

    full_list = get_dynamic_stock_list()
    t_code = st.selectbox("选择股票", options=["【添加新股票】"] + full_list)
    final_code = st.text_input("新股票名（如果选添加新股票）") if t_code == "【添加新股票】" else t_code

    with st.form("trade_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        d = col1.date_input("日期", datetime.now())
        a = col2.selectbox("操作", ["买入", "卖出"])
        p = col1.number_input("单价", min_value=0.0, step=0.001, format="%.3f")
        q = col2.number_input("数量", min_value=1, step=1)
        note = st.text_input("备注（可选）")
        submitted = st.form_submit_button("保存")

        if submitted and final_code and p is not None and q is not None:
            c.execute("""
                INSERT INTO trades (date, code, action, price, quantity, note)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (d.strftime('%Y-%m-%d'), final_code, a, p, q, note or None))
            conn.commit()
            sync_db_to_github()
            st.success("保存成功")
            st.rerun()

elif choice == "买卖信号":
    st.header("买卖信号")
    st.write("买卖信号页面")

elif choice == "历史明细":
    st.header("历史明细")
    st.write("历史明细页面")

elif choice == "复盘日记":
    st.header("复盘日记")
    st.write("复盘日记页面")

# 下载按钮（放在最后，无条件显示）
col1, col2, col3 = st.columns([5, 1, 1])
with col3:
    db_path = pathlib.Path(__file__).with_name("stock_data_v12.db")
    if db_path.exists():
        with open(db_path, "rb") as f:
            st.download_button(
                label="下载数据库",
                data=f,
                file_name="stock_data_v12.db",
                mime="application/x-sqlite3"
            )
