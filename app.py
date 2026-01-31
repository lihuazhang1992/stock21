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
# 配置项（你只需要确认/修改这2处，其余不用动）
# --------------------------
DB_FILE = "stock_data_v12.db"  # 你的数据库文件名，保持和之前一致！
TABLE_NAME = "trade_records"  # 交易记录表名，自动创建

# --------------------------
# 1. 数据库初始化与操作函数（核心：真正写入数据）
# --------------------------
def init_db():
    """初始化数据库，表不存在则自动创建"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 创建交易记录表：包含股票名、买入价格、数量、交易日期、创建时间
    c.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_name TEXT NOT NULL,  # 股票名称
        buy_price REAL NOT NULL,   # 买入价格
        buy_quantity INTEGER NOT NULL,  # 买入数量
        trade_date DATE NOT NULL,  # 交易日期
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP  # 记录创建时间（自动生成）
    )
    """)
    conn.commit()
    conn.close()

def add_trade_record(stock_name, buy_price, buy_quantity, trade_date):
    """写入交易数据到数据库（核心保存函数）"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"""
    INSERT INTO {TABLE_NAME} (stock_name, buy_price, buy_quantity, trade_date)
    VALUES (?, ?, ?, ?)
    """, (stock_name, buy_price, buy_quantity, trade_date))
    conn.commit()
    conn.close()

def get_all_trades():
    """获取所有交易数据，用于页面展示"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 让结果可以按列名访问
    c = conn.cursor()
    c.execute(f"SELECT * FROM {TABLE_NAME} ORDER BY create_time DESC")
    data = c.fetchall()
    conn.close()
    # 转换为DataFrame可识别的格式
    return [dict(row) for row in data]

# --------------------------
# 2. GitHub自动同步函数（你需要修改这3处！！！）
# --------------------------
def auto_sync_github():
    """将最新数据库文件同步到GitHub（修复后：仅在数据写入成功后调用）"""
    try:
        # --------------------------
        # 【必须修改这3处为你自己的GitHub信息！】
        # --------------------------
        repo_path = Path(__file__).parent  # 仓库本地路径，默认不用改（云端自动识别）
        git_user = "lihuazhang1992"       # 替换成你的GitHub账号（比如lihuazhang1992）
        git_email = "522421290@qq.com"    # 替换成你的GitHub绑定邮箱

        # 初始化git仓库
        repo = git.Repo(repo_path)
        # 设置git用户信息（云端需要）
        repo.config_user_email(git_email)
        repo.config_user_name(git_user)

        # 添加数据库文件到暂存区
        repo.index.add([DB_FILE])
        # 提交信息：带时间戳，方便追溯
        commit_msg = f"自动同步数据库：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        repo.index.commit(commit_msg)
        # 推送到GitHub远程仓库
        origin = repo.remote(name="origin")
        origin.push()

        return True, "同步成功"
    except Exception as e:
        return False, f"同步失败：{str(e)}"

# --------------------------
# 3. 页面主布局（和你之前一致的操作界面）
# --------------------------
def main():
    # 初始化数据库（首次运行自动建表）
    init_db()
    # 设置页面标题
    st.title("股票交易数据管理")
    st.divider()

    # 输入区域：股票名、买入价格、数量、交易日期
    st.subheader("新增买入记录")
    col1, col2, col3 = st.columns(3)
    with col1:
        stock_name = st.text_input("股票名称", placeholder="比如：贵州茅台", label_visibility="visible")
    with col2:
        buy_price = st.number_input("买入价格", min_value=0.01, step=0.01, placeholder="输入价格")
    with col3:
        buy_quantity = st.number_input("买入数量", min_value=1, step=1, placeholder="输入股数")
    trade_date = st.date_input("交易日期", value=datetime.now())  # 默认当天，可手动选择

    st.divider()

    # --------------------------
    # 核心：修复后的保存按钮（先写入→再刷新→最后同步）
    # --------------------------
    if st.button("保存交易记录", type="primary", use_container_width=True):
        # 第一步：验证输入数据（不能为空/无效）
        if not stock_name or buy_price <= 0 or buy_quantity <= 0:
            st.error("❌ 请填写完整有效数据！股票名、价格、数量不能为空/0")
        else:
            try:
                # 第二步：真正写入数据到数据库（核心！）
                add_trade_record(stock_name, buy_price, buy_quantity, trade_date.strftime("%Y-%m-%d"))
                st.success("✅ 交易数据保存成功！")

                # 第三步：自动刷新页面，实时显示新数据（不用手动刷新）
                st.experimental_rerun()

            except Exception as e:
                st.error(f"❌ 数据保存失败！原因：{str(e)}")

    # --------------------------
    # 同步按钮：可选（也可在保存后自动同步，下面已加自动同步）
    # --------------------------
    st.caption("💡 保存成功后会自动同步到GitHub，也可手动点击下方按钮同步")
    if st.button("手动同步到GitHub", use_container_width=True):
        success, msg = auto_sync_github()
        if success:
            st.success(f"✅ {msg}")
        else:
            st.error(f"❌ {msg}")

    st.divider()

    # --------------------------
    # 数据展示区域：实时显示所有交易记录
    # --------------------------
    st.subheader("所有交易记录")
    trade_data = get_all_trades()
    if trade_data:
        st.dataframe(trade_data, use_container_width=True, hide_index=True)
        # 可选：显示统计信息
        total_quantity = sum([d["buy_quantity"] for d in trade_data])
        st.info(f"📊 累计买入总股数：{total_quantity}")
    else:
        st.info("📭 暂无交易记录，保存第一条记录后这里会显示")

# --------------------------
# 自动同步逻辑：页面加载/数据变化后，自动同步最新数据
# --------------------------
# 每次页面刷新（保存后/手动刷新），自动同步一次
try:
    auto_sync_github()
except:
    pass

# 运行主程序
if __name__ == "__main__":
    main()
