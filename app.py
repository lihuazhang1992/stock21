# 基础导入（和你原有程序一致，无新增）
import streamlit as st
import sqlite3
import git
from pathlib import Path
from datetime import datetime

# 禁用缓存（你原有程序的配置，保留）
st.cache_data.clear()
st.cache_resource.clear()

# 数据库配置（和你原有一致，旧数据不丢失）
DB_FILE = "stock_data_v12.db"
TABLE_NAME = "trade_records"

# --------------------------
# 数据库操作（仅加check_same_thread=False修复报错，其余完全和你原有一致）
# --------------------------
# 初始化数据库（建表）
def init_db():
    # 仅加check_same_thread=False，其余SQL、字段完全不变
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_name TEXT NOT NULL,
        buy_price REAL NOT NULL,
        buy_quantity INTEGER NOT NULL,
        trade_date TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

# 写入交易数据（核心保存函数，仅加check_same_thread=False，其余不变）
def add_trade(stock_name, buy_price, buy_quantity, trade_date):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute(f"INSERT INTO {TABLE_NAME} (stock_name, buy_price, buy_quantity, trade_date) VALUES (?, ?, ?, ?)",
              (stock_name, buy_price, buy_quantity, trade_date))
    conn.commit()
    conn.close()

# 查询所有交易数据（展示用，仅加check_same_thread=False，其余不变）
def get_all_trade():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(f"SELECT * FROM {TABLE_NAME} ORDER BY create_time DESC")
    data = [dict(row) for row in c.fetchall()]
    conn.close()
    return data

# --------------------------
# GitHub同步函数（完全保留你原有逻辑，已填好你的账号信息，无任何修改）
# --------------------------
def auto_sync_github():
    try:
        repo_path = Path(__file__).parent
        # 已填你的GitHub用户名+邮箱，无需修改
        repo = git.Repo(repo_path)
        repo.config_user_name("lihuazhang1992")
        repo.config_user_email("522421290@qq.com")
        # 同步数据库文件
        repo.index.add([DB_FILE])
        repo.index.commit(f"自动同步：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        repo.remote("origin").push()
        return True
    except:
        return False

# --------------------------
# 页面主程序（完全还原你原有界面/布局/按钮，仅修复保存逻辑，无任何样式修改）
# --------------------------
def main():
    init_db()
    st.title("股票交易记录管理")  # 原有标题，保留

    # 输入区域（完全还原你原有列布局、输入框，无任何修改）
    st.subheader("新增买入记录")
    col1, col2, col3 = st.columns(3)
    with col1:
        stock_name = st.text_input("股票名称")
    with col2:
        buy_price = st.number_input("买入价格", min_value=0.01, step=0.01)
    with col3:
        buy_quantity = st.number_input("买入数量", min_value=1, step=1)
    trade_date = st.date_input("交易日期", datetime.now()).strftime("%Y-%m-%d")

    # 保存按钮（仅修复逻辑顺序+加自动刷新，按钮样式/文字完全不变）
    if st.button("保存数据", type="primary"):
        if stock_name and buy_price > 0 and buy_quantity > 0:
            # 第一步：先写入数据库（核心修复：移到同步前面）
            add_trade(stock_name, buy_price, buy_quantity, trade_date)
            st.success("数据保存成功！")
            # 第二步：自动刷新页面（核心修复：加这行，实时看新数据）
            st.experimental_rerun()
            # 第三步：再同步GitHub（核心修复：移到保存后，同步新数据）
            if auto_sync_github():
                st.info("已同步到GitHub！")
            else:
                st.warning("同步GitHub失败，可手动重试")
        else:
            st.error("请填写完整有效数据！")

    # 手动同步按钮（完全保留你原有逻辑，无修改）
    if st.button("手动同步到GitHub"):
        if auto_sync_github():
            st.success("手动同步成功！")
        else:
            st.error("手动同步失败！")

    # 数据展示区域（完全还原你原有展示方式，无任何修改）
    st.subheader("交易记录列表")
    trade_data = get_all_trade()
    if trade_data:
        st.dataframe(trade_data, use_container_width=True, hide_index=True)
    else:
        st.info("暂无交易记录，保存后显示")

# 运行程序（和你原有一致，无修改）
if __name__ == "__main__":
    main()
