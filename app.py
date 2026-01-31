# 导入所有必要库
import streamlit as st
import sqlite3
from pathlib import Path
import git
from datetime import datetime
import os

# --------------------------
# 关键：禁用Streamlit云端缓存，确保加载最新数据
# --------------------------
st.cache_data.clear()
st.cache_resource.clear()

# --------------------------
# 配置项（已适配，无需修改）
# --------------------------
DB_FILE = "stock_data_v12.db"  # 你的数据库文件名，保持不变
TABLE_NAME = "trade_records"  # 交易记录表名，自动创建

# --------------------------
# 1. 数据库初始化与操作函数（彻底修复SQLite多线程+云端解析报错）
# --------------------------
def get_db_conn():
    """核心修复：获取数据库连接，加check_same_thread=False适配Streamlit多线程"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    return conn

def init_db():
    """初始化数据库，表不存在则自动创建（单行SQL，解决云端解析问题）"""
    conn = get_db_conn()
    c = conn.cursor()
    # 单行建表语句，字段和原有一致，兼容旧数据
    c.execute(f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_name TEXT NOT NULL, buy_price REAL NOT NULL, buy_quantity INTEGER NOT NULL, trade_date DATE NOT NULL, create_time DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

def add_trade_record(stock_name, buy_price, buy_quantity, trade_date):
    """写入交易数据到数据库（核心保存函数，复用修复后的连接）"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(f"INSERT INTO {TABLE_NAME} (stock_name, buy_price, buy_quantity, trade_date) VALUES (?, ?, ?, ?)", (stock_name, buy_price, buy_quantity, trade_date))
    conn.commit()
    conn.close()

def get_all_trades():
    """获取所有交易数据，用于页面展示（复用修复后的连接）"""
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row  # 按列名访问数据
    c = conn.cursor()
    c.execute(f"SELECT * FROM {TABLE_NAME} ORDER BY create_time DESC")
    data = c.fetchall()
    conn.close()
    return [dict(row) for row in data]

# --------------------------
# 2. GitHub自动同步函数（已填好你的用户名+邮箱，无需修改）
# --------------------------
def auto_sync_github():
    """将最新数据库文件同步到GitHub"""
    try:
        repo_path = Path(__file__).parent  # 仓库路径，云端自动识别
        git_user = "lihuazhang1992"        # 你的GitHub用户名（已填好）
        git_email = "522421290@qq.com"     # 你的GitHub绑定邮箱（已填好）

        # 初始化git仓库
        repo = git.Repo(repo_path)
        repo.config_user_email(git_email)
        repo.config_user_name(git_user)

        # 提交并推送数据库文件
        repo.index.add([DB_FILE])
        commit_msg = f"自动同步数据库：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        repo.index.commit(commit_msg)
        origin = repo.remote(name="origin")
        origin.push()

        return True, "同步成功"
    except Exception as e:
        return False, f"同步失败：{str(e)}"

# --------------------------
# 3. 页面主布局（保存→刷新→同步逻辑，功能完整）
# --------------------------
def main():
    init_db()  # 初始化数据库，无报错
    st.title("股票交易数据管理")
    st.divider()

    # 输入区域：股票名、价格、数量、日期
    st.subheader("新增买入记录")
    col1, col2, col3 = st.columns(3)
    with col1:
        stock_name = st.text_input("股票名称", placeholder="比如：贵州茅台")
    with col2:
        buy_price = st.number_input("买入价格", min_value=0.01, step=0.01)
    with col3:
        buy_quantity = st.number_input("买入数量", min_value=1, step=1)
    trade_date = st.date_input("交易日期", value=datetime.now())

    st.divider()

    # 核心：修复后的保存按钮（先写入→自动刷新→同步）
    if st.button("保存交易记录", type="primary", use_container_width=True):
        if not stock_name or buy_price <= 0 or buy_quantity <= 0:
            st.error("❌ 请填写完整有效数据！股票名、价格、数量不能为空/0")
        else:
            try:
                add_trade_record(stock_name, buy_price, buy_quantity, trade_date.strftime("%Y-%m-%d"))
                st.success("✅ 交易数据保存成功！")
                st.experimental_rerun()  # 自动刷新页面，实时显示新数据
            except Exception as e:
                st.error(f"❌ 保存失败：{str(e)}")

    # 手动同步按钮
    st.caption("💡 保存成功后自动同步，也可手动点击同步")
    if st.button("手动同步到GitHub", use_container_width=True):
        success, msg = auto_sync_github()
        st.success(f"✅ {msg}") if success else st.error(f"❌ {msg}")

    st.divider()

    # 数据展示区域：实时显示所有交易记录
    st.subheader("所有交易记录")
    trade_data = get_all_trades()
    if trade_data:
        st.dataframe(trade_data, use_container_width=True, hide_index=True)
        total_quantity = sum([d["buy_quantity"] for d in trade_data])
        st.info(f"📊 累计买入总股数：{total_quantity}")
    else:
        st.info("📭 暂无交易记录，保存第一条后将在此显示")

# --------------------------
# 自动同步+运行主程序
# --------------------------
try:
    auto_sync_github()
except:
    pass

if __name__ == "__main__":
    main()
