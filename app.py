import streamlit as st
import pandas as pd
import time
from datetime import datetime
import logging
import json
import os

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,%(msecs)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 导入业务模块
from src.data_fetcher import get_fund_holdings, get_realtime_stock_prices, get_fund_history_nav, get_fund_real_time_estimate_from_1234567
from src.valuation import estimate_nav_change

# ====================== 页面配置 ======================
st.set_page_config(
    page_title="基金净值估算器",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 关键修改：优化表格样式，强制两列布局，无需滑动
st.markdown("""
    <style>
    /* 整体清爽 */
    .stApp {
        background-color: #fafbfc;
        padding: 0 8px;
    }
    /* 标题卡片 */
    .title-card {
        background: white;
        padding: 1rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        margin-bottom: 1rem;
    }
    /* 核心修改：表格强制两列，不允许横向滚动，一屏显示 */
    div[data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden !important;  /* 禁止横向滚动 */
        box-shadow: 0 1px 4px rgba(0,0,0,0.04);
        width: 100% !important;
    }
    /* 表格单元格样式：左列名称，右列涨跌，自动分配宽度 */
    div[data-testid="stDataFrame"] table {
        width: 100% !important;
        table-layout: fixed !important;  /* 固定表格布局 */
    }
    /* 基金名称列：占70%宽度，自动换行，不截断 */
    div[data-testid="stDataFrame"] table th:nth-child(1),
    div[data-testid="stDataFrame"] table td:nth-child(1) {
        width: 70% !important;
        word-wrap: break-word !important;  /* 自动换行 */
        white-space: normal !important;
        padding: 10px 8px !important;
    }
    /* 涨跌列：占30%宽度，居中显示 */
    div[data-testid="stDataFrame"] table th:nth-child(2),
    div[data-testid="stDataFrame"] table td:nth-child(2) {
        width: 30% !important;
        text-align: center !important;
        padding: 10px 8px !important;
    }
    /* 移动端字体适配 */
    @media (max-width: 768px) {
        div[data-testid="stDataFrame"] table td, 
        div[data-testid="stDataFrame"] table th {
            font-size: 14px !important;
        }
    }
    </style>
""", unsafe_allow_html=True)

# 标题区域
st.markdown("""
<div class="title-card">
    <h2 style="margin:0; color:#1f2937">🇨🇳 基金实时估值</h2>
    <p style="color:#6b7280; margin: 0.2rem 0 0 0;">只看核心：基金名称 + 实时涨跌</p>
</div>
""", unsafe_allow_html=True)

VALUATION_OPTIONS = ["原有手动加权", "天天基金API"]
SAVE_FILE = "fund_valuation_config.json"

# ====================== 持久化配置 ======================
def load_config():
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(SAVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

if "fund_valuation_mode" not in st.session_state:
    st.session_state["fund_valuation_mode"] = load_config()

if "btn_clicked_code" not in st.session_state:
    st.session_state["btn_clicked_code"] = None
if "btn_clicked_mode" not in st.session_state:
    st.session_state["btn_clicked_mode"] = None

def change_valuation_mode(code, new_mode):
    if code and new_mode:
        st.session_state["fund_valuation_mode"][code] = new_mode
        save_config(st.session_state["fund_valuation_mode"])
        st.session_state["btn_clicked_code"] = None
        st.session_state["btn_clicked_mode"] = None
        try:
            st.rerun()
        except AttributeError:
            st.experimental_rerun()

# ====================== 侧边栏 ======================
if st.button("📱 基金配置 & 刷新", width='stretch'):
    st.session_state["sidebar_expanded"] = not st.session_state.get("sidebar_expanded", False)

if st.session_state.get("sidebar_expanded", False):
    st.sidebar.header("配置")
    default_funds = "019454,165520,021986,025208,012544,012920,270023,001467,016532,018043,270042,166301,002611,457001,539002"
    fund_input = st.sidebar.text_area("基金代码", value=default_funds, height=160)
    codes = [c.strip() for c in fund_input.split(',') if c.strip()]

    for code in codes:
        if code not in st.session_state["fund_valuation_mode"]:
            st.session_state["fund_valuation_mode"][code] = "原有手动加权"
    to_del = [k for k in st.session_state["fund_valuation_mode"] if k not in codes]
    for k in to_del:
        del st.session_state["fund_valuation_mode"][k]

    st.sidebar.subheader("批量估值方式")
    batch_mode = st.sidebar.radio("默认方式", options=VALUATION_OPTIONS, index=0, horizontal=True)
    if st.sidebar.button("应用到所有基金", width='stretch'):
        for code in codes:
            st.session_state["fund_valuation_mode"][code] = batch_mode
        save_config(st.session_state["fund_valuation_mode"])
        st.rerun()

    auto_refresh = st.sidebar.checkbox("自动刷新 60秒", value=False)
    refresh_btn = st.sidebar.button("立即刷新", width='stretch')
else:
    default_funds = "019454,165520,021986,025208,012544,012920,270023,001467,016532,018043,270042,166301,002611,457001,539002"
    codes = [c.strip() for c in default_funds.split(',') if c.strip()]
    auto_refresh = False
    refresh_btn = False

# ====================== 业务逻辑 ======================
from concurrent.futures import ThreadPoolExecutor, as_completed

@st.cache_data(ttl=3600)
def fetch_history_cached(code, days):
    return get_fund_history_nav(code, days)

def process_single_fund(code, valuation_mode):
    try:
        if valuation_mode == "天天基金API":
            ttfund_data = get_fund_real_time_estimate_from_1234567(code)
            if ttfund_data:
                return {
                    '基金代码': code,
                    '基金名称': ttfund_data['fund_name'],
                    '估算涨跌': ttfund_data['estimate_change'],
                    '状态': '成功',
                    '估值方式': '天天基金API'
                }
            else:
                valuation_mode = "原有手动加权"

        if valuation_mode == "原有手动加权":
            result_data = get_fund_holdings(code)
            if not result_data:
                return {'基金代码': code,'基金名称': '--','估算涨跌': None,'状态': '失败','估值方式': '手动'}
            fund_name, holdings = result_data[:2]
            stock_fetch_codes = [h.get('fetch_code', h['code']) for h in holdings]
            prices = get_realtime_stock_prices(stock_fetch_codes)
            valuation = estimate_nav_change(holdings, prices)
            return {
                '基金代码': code,
                '基金名称': fund_name,
                '估算涨跌': valuation['estimated_change'],
                '状态': '成功',
                '估值方式': '手动加权'
            }

    except Exception as e:
        logging.error(f"出错 {code}: {e}")
        return {'基金代码': code,'基金名称': 'Error','估算涨跌': None,'状态': '错误','估值方式': '-'}

def process_funds(code_list):
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures_map = {
            executor.submit(process_single_fund, c, st.session_state["fund_valuation_mode"].get(c, "原有手动加权")): c
            for c in code_list if c.strip()
        }
        for future in as_completed(futures_map):
            try:
                results.append(future.result())
            except:
                pass
    return results

# ====================== 页面渲染 ======================
dashboard = st.empty()

def render_dashboard():
    if st.session_state["btn_clicked_code"] and st.session_state["btn_clicked_mode"]:
        change_valuation_mode(
            st.session_state["btn_clicked_code"],
            st.session_state["btn_clicked_mode"]
        )

    with dashboard.container():
        valid_codes = codes
        if not valid_codes:
            st.warning("请输入基金代码")
            return
        data = process_funds(valid_codes)

        code_order_map = {c: i for i, c in enumerate(valid_codes)}
        summary = []
        for item in data:
            code = item.get("基金代码", "").strip()
            if not code:
                continue
            summary.append({
                "排序": code_order_map.get(code, 999),
                "基金名称": item.get("基金名称", "--"),
                "估算涨跌": item.get("估算涨跌"),
                "基金代码_内部": code,  # 内部用，不显示
                "当前估值方式": st.session_state["fund_valuation_mode"].get(code, "原有手动加权")
            })

        df = pd.DataFrame(summary).sort_values("排序").drop(columns=["排序"])

        # 涨跌颜色
        def color_change(v):
            if isinstance(v, (float, int)):
                return 'background-color: #fef2f2; color: #e53e3e; font-weight: 600' if v > 0 else \
                       'background-color: #f0fdf4; color: #22c55e; font-weight: 600' if v < 0 else \
                       'color: #6b7280'
            return ''

        # ====================== 核心：两列布局，无需滑动 ======================
        st.subheader("📊 基金估值概览")
        view_df = df[["基金名称", "估算涨跌"]].copy()

        # 样式优化：涨跌列带背景色，更醒目
        styler = view_df.style\
            .format({"估算涨跌": "{:+.2f}%"}, na_rep="--")\
            .map(color_change, subset=["估算涨跌"])\
            .set_table_styles([
                {'selector': 'th', 'props': [('background-color', '#f9fafb'), ('border', 'none')]},
                {'selector': 'td', 'props': [('border', 'none')]},
                {'selector': 'tr', 'props': [('border-bottom', '1px solid #f3f4f6')]},
                {'selector': 'tr:last-child', 'props': [('border-bottom', 'none')]}
            ])

        # 固定列宽配置：名称列宽占比大，涨跌列小且居中
        column_config = {
            "基金名称": st.column_config.TextColumn("基金名称", width=180),  # 自适应宽度
            "估算涨跌": st.column_config.NumberColumn("涨跌(%)", format="%.2f%%", width="flex"),
        }

        # 渲染表格：强制100%宽度，无横向滚动
        # 渲染表格：强制100%宽度，无横向滚动
        st.dataframe(
            styler,
            column_config=column_config,
            hide_index=True,
            height=len(df) * 38,  # 高度刚好匹配行数，无空白
            width='stretch' # 占满宽度（只留这一个）
        )

        # ====================== 操作区 ======================
        st.subheader("⚙️ 估值方式切换")
        for _, row in df.iterrows():
            code = row["基金代码_内部"]
            name = row["基金名称"]
            mode = row["当前估值方式"]

            st.markdown(f"**{name}**")
            c1, c2 = st.columns(2)
            with c1:
                if st.button(
                    "手动加权",
                    key=f"m{code}",
                    type="primary" if mode == "原有手动加权" else "secondary",
                    width='stretch'
                ):
                    st.session_state["btn_clicked_code"] = code
                    st.session_state["btn_clicked_mode"] = "原有手动加权"
            with c2:
                if st.button(
                    "天天API",
                    key=f"a{code}",
                    type="primary" if mode == "天天基金API" else "secondary",
                    width='stretch'
                ):
                    st.session_state["btn_clicked_code"] = code
                    st.session_state["btn_clicked_mode"] = "天天基金API"
            st.divider()

# ====================== 运行 ======================
render_dashboard()

if auto_refresh:
    time.sleep(60)
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()

if st.button("🔄 立即刷新所有数据", width='stretch'):
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()