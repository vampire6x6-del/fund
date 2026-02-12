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

# 页面配置
st.set_page_config(page_title="基金净值估算器", layout="wide")
st.title("🇨🇳 中国公募基金实时净值估算系统")
st.markdown("基于前十大重仓股实时估算基金净值涨跌幅。")

VALUATION_OPTIONS = ["原有手动加权", "天天基金API"]
SAVE_FILE = "fund_valuation_config.json"

# ====================== 【核心：持久化保存】 ======================
def load_config():
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(SAVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# 加载本地保存的配置
if "fund_valuation_mode" not in st.session_state:
    st.session_state["fund_valuation_mode"] = load_config()

# 初始化按钮点击状态
if "btn_clicked_code" not in st.session_state:
    st.session_state["btn_clicked_code"] = None
if "btn_clicked_mode" not in st.session_state:
    st.session_state["btn_clicked_mode"] = None

# 处理按钮点击事件
def change_valuation_mode(code, new_mode):
    if code and new_mode:
        st.session_state["fund_valuation_mode"][code] = new_mode
        save_config(st.session_state["fund_valuation_mode"])
        # 重置点击状态
        st.session_state["btn_clicked_code"] = None
        st.session_state["btn_clicked_mode"] = None
        # 兼容不同版本的刷新
        try:
            st.rerun()
        except AttributeError:
            st.experimental_rerun()

# ====================== 侧边栏 ======================
st.sidebar.header("配置")
default_funds = "019454,165520,021986,025208,012544,012920,270023,001467,016532,018043,270042,166301,002611,457001,539002"
fund_input = st.sidebar.text_area("基金代码 (英文逗号分隔)", value=default_funds, height=100)
codes = [c.strip() for c in fund_input.split(',') if c.strip()]

# 自动同步新基金，保留老基金配置
for code in codes:
    if code not in st.session_state["fund_valuation_mode"]:
        st.session_state["fund_valuation_mode"][code] = "原有手动加权"

# 清理不存在的基金
to_del = [k for k in st.session_state["fund_valuation_mode"].keys() if k not in codes]
for k in to_del:
    del st.session_state["fund_valuation_mode"][k]

# 批量设置
st.sidebar.subheader("批量估值方式")
batch_mode = st.sidebar.radio("未单独设置的基金使用此默认值", options=VALUATION_OPTIONS, index=0)

if st.sidebar.button("应用批量设置到所有基金（覆盖已设置）"):
    for code in codes:
        st.session_state["fund_valuation_mode"][code] = batch_mode
    save_config(st.session_state["fund_valuation_mode"])
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()

auto_refresh = st.sidebar.checkbox("自动刷新 (每60秒)", value=False)
refresh_btn = st.sidebar.button("立即刷新数据")

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
                history_df = get_fund_history_nav(code, days=365)
                return {
                    '基金代码': code,
                    '基金名称': ttfund_data['fund_name'],
                    '持仓日期': f"估值更新：{ttfund_data['update_time']}",
                    '状态': '成功（天天基金API）',
                    '估算涨跌': ttfund_data['estimate_change'],
                    'Details': [],
                    'History': history_df,
                    '更新时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    '估值方式': '天天基金API'
                }
            else:
                valuation_mode = "原有手动加权"

        if valuation_mode == "原有手动加权":
            result_data = get_fund_holdings(code)
            if not result_data:
                return {'基金代码': code,'基金名称': '--','持仓日期': '--','状态': '获取持仓失败','估算涨跌': None,'Details': [],'估值方式': '原有手动加权'}
            if len(result_data) == 3:
                fund_name, holdings, report_date = result_data
            else:
                fund_name, holdings = result_data
                report_date = "--"
            stock_fetch_codes = [h.get('fetch_code', h['code']) for h in holdings]
            prices = get_realtime_stock_prices(stock_fetch_codes)
            valuation = estimate_nav_change(holdings, prices)
            history_df = get_fund_history_nav(code, days=365)
            return {
                '基金代码': code,
                '基金名称': fund_name,
                '持仓日期': report_date,
                '状态': '成功（原有手动加权）',
                '估算涨跌': valuation['estimated_change'],
                'Details': valuation['details'],
                'History': history_df,
                '更新时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                '估值方式': '原有手动加权'
            }

    except Exception as e:
        logging.error(f"处理基金 {code} 出错: {e}")
        ttfund_data = get_fund_real_time_estimate_from_1234567(code)
        if ttfund_data:
            return {'基金代码': code,'基金名称': ttfund_data['fund_name'],'持仓日期': f"估值更新：{ttfund_data['update_time']}",'状态': '成功（天天基金API-兜底）','估算涨跌': ttfund_data['estimate_change'],'Details': [],'History': None,'更新时间': ttfund_data['update_time'],'估值方式': '天天基金API-兜底'}
        return {'基金代码': code,'基金名称': 'Error','持仓日期': '--','状态': f'Error: {str(e)}','估算涨跌': None,'Details': [],'估值方式': '无'}

def process_funds(code_list):
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures_map = {executor.submit(process_single_fund, c, st.session_state["fund_valuation_mode"].get(c, "原有手动加权")): c for c in code_list if c.strip()}
        for future in as_completed(futures_map):
            try:
                results.append(future.result())
            except:
                pass
    return results

# ====================== 页面渲染 ======================
dashboard = st.empty()

def render_dashboard():
    # 处理pending的按钮点击
    if st.session_state["btn_clicked_code"] and st.session_state["btn_clicked_mode"]:
        change_valuation_mode(
            st.session_state["btn_clicked_code"],
            st.session_state["btn_clicked_mode"]
        )

    with dashboard.container():
        valid_codes = [c.strip() for c in fund_input.split(',') if c.strip()]
        if not valid_codes:
            st.warning("请输入至少一个基金代码。")
            return
        data = process_funds(valid_codes)
        if not data:
            st.error("未找到有效基金数据。")
            return

        # 固定排序（按输入顺序）
        code_order_map = {code: idx for idx, code in enumerate(valid_codes)}
        summary_data = []
        for item in data:
            code = item.get('基金代码', '').strip()
            if not code: continue
            current_mode = st.session_state["fund_valuation_mode"].get(code, "原有手动加权")
            summary_data.append({
                '排序索引': code_order_map.get(code, 999),
                '基金代码': code,
                '基金名称': item.get('基金名称', '--'),
                '估算涨跌': item.get('估算涨跌'),
                '当前估值方式': current_mode,
                '状态': item.get('状态', '--'),
                '更新时间': item.get('更新时间', ''),
                '持仓日期': item.get('持仓日期', '--'),
            })

        df_summary = pd.DataFrame(summary_data)
        df_summary = df_summary.sort_values('排序索引').drop('排序索引', axis=1)
        df_summary = df_summary.dropna(subset=['基金代码'])
        df_summary = df_summary[df_summary['基金代码'] != '']
        df_summary = df_summary.reset_index(drop=True)

        # 涨跌颜色样式函数
        def color_change(val):
            if isinstance(val, (float, int)):
                return 'color: #D32F2F' if val>0 else 'color: #388E3C' if val<0 else ''
            return ''

        st.subheader("📊 基金估值概览")
        
        # 先渲染表格主体（无按钮）
        column_config = {
            "基金代码": st.column_config.TextColumn("基金代码", width="small") if hasattr(st.column_config, 'TextColumn') else None,
            "基金名称": st.column_config.TextColumn("基金名称", width="large") if hasattr(st.column_config, 'TextColumn') else None,
            "估算涨跌": st.column_config.NumberColumn(
                "估算涨跌",
                format="%.2f%%",
                width="small",
                help="基于当前估值方式计算的实时涨跌幅"
            ) if hasattr(st.column_config, 'NumberColumn') else None,
            "当前估值方式": st.column_config.TextColumn("当前估值方式", width="small") if hasattr(st.column_config, 'TextColumn') else None,
            "状态": st.column_config.TextColumn("状态", width="medium") if hasattr(st.column_config, 'TextColumn') else None,
            "更新时间": st.column_config.TextColumn("更新时间", width="small") if hasattr(st.column_config, 'TextColumn') else None,
            "持仓日期": st.column_config.TextColumn("持仓日期", width="small") if hasattr(st.column_config, 'TextColumn') else None,
        }
        
        # 过滤掉None值（兼容极低版本Streamlit）
        column_config = {k: v for k, v in column_config.items() if v is not None}

        # 动态高度，适配按钮
        dynamic_height = 40 + len(df_summary) * 40
        dynamic_height = max(dynamic_height, 200)

        # 应用样式
        styler = df_summary.style\
            .format({'估算涨跌': "{:+.2f}%"}, na_rep="--")\
            .map(color_change, subset=['估算涨跌'])
        
        # 渲染基础表格（核心修改：移除use_container_width，改用width='stretch'）
        st.dataframe(
            styler,
            column_config=column_config if column_config else None,
            hide_index=True,
            width='stretch',  # 替换 use_container_width=True
            height=dynamic_height - 100,  # 留出按钮区域高度
        )

        # ====================== 核心：表格下方的操作按钮区 ======================
        st.subheader("⚙️ 估值方式操作（对应上方表格行）")
        st.markdown("💡 点击按钮切换对应基金的估值方式")
        
        # 按表格顺序显示按钮（每行对应表格一行）
        for idx, (_, row) in enumerate(df_summary.iterrows()):
            code = row['基金代码']
            name = row['基金名称']
            current_mode = row['当前估值方式']
            
            # 每行一个操作栏，和表格行一一对应
            col1, col2, col3, col4 = st.columns([1, 3, 1, 1])
            with col1:
                st.markdown(f"**{code}**")
            with col2:
                st.markdown(f"{name}")
            with col3:
                # 手动加权按钮（核心修改：移除use_container_width，改用width='stretch'）
                if st.button(
                    "手动加权",
                    key=f"btn_manual_{code}",
                    type="primary" if current_mode == "原有手动加权" else "secondary",
                    width='stretch'  # 替换 use_container_width=True
                ):
                    st.session_state["btn_clicked_code"] = code
                    st.session_state["btn_clicked_mode"] = "原有手动加权"
            with col4:
                # 天天API按钮（核心修改：移除use_container_width，改用width='stretch'）
                if st.button(
                    "天天API",
                    key=f"btn_api_{code}",
                    type="primary" if current_mode == "天天基金API" else "secondary",
                    width='stretch'  # 替换 use_container_width=True
                ):
                    st.session_state["btn_clicked_code"] = code
                    st.session_state["btn_clicked_mode"] = "天天基金API"
            
            # 行分隔线
            if idx < len(df_summary) - 1:
                st.markdown("---")

        # 保留详细信息区（可折叠）
        with st.expander("📋 查看基金详细信息", expanded=False):
            valid_data = [d for d in data if d.get('基金代码', '').strip()]
            # 按输入顺序排序详情标签页
            valid_data_sorted = sorted(valid_data, key=lambda x: code_order_map.get(x['基金代码'], 999))
            tabs = st.tabs([f"{d['基金代码']} - {d['基金名称'][:8]}..." for d in valid_data_sorted])
            
            for i, tab in enumerate(tabs):
                with tab:
                    item = valid_data_sorted[i]
                    if item['状态'].startswith('成功'):
                        # 核心指标卡片
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            st.metric("实时估算涨跌", f"{item['估算涨跌']:+.2f}%")
                        with c2:
                            st.metric("持仓报告期", item['持仓日期'])
                        with c3:
                            st.metric("更新时间", item['更新时间'])

                        st.divider()

                        # 图表区
                        chart_tab1, chart_tab2 = st.tabs(["📉 实时分时走势", "📅 历史净值趋势"])
                        with chart_tab1:
                            f_code = item['基金代码']
                            if 'fund_intraday' in st.session_state and f_code in st.session_state['fund_intraday']:
                                df_intra = st.session_state['fund_intraday'][f_code]
                                if not df_intra.empty:
                                    import altair as alt
                                    chart_intra = alt.Chart(df_intra).mark_line(color='#FFA500').encode(
                                        x=alt.X('Time', title='时间'),
                                        y=alt.Y('Estimate', title='估算涨跌(%)', scale=alt.Scale(zero=False))
                                    ).properties(height=250)
                                    st.altair_chart(chart_intra, width='stretch')  # 替换 use_container_width=True
                            else:
                                st.info("分时数据收集中...")

                        with chart_tab2:
                            if 'History' in item and item['History'] is not None and not item['History'].empty:
                                range_map = {'1周':7,'1月':30,'3月':90,'6月':180,'1年':365}
                                selected_range = st.radio(
                                    "时间范围", list(range_map.keys()), index=1,
                                    key=f"range_{item['基金代码']}", horizontal=True, label_visibility="collapsed")
                                days_limit = range_map[selected_range]
                                start_date = pd.Timestamp.now() - pd.Timedelta(days=days_limit)
                                chart_df = item['History'][item['History']['date'] >= start_date]
                                import altair as alt
                                chart_hist = alt.Chart(chart_df).mark_line().encode(
                                    x=alt.X('date', title='日期', axis=alt.Axis(format='%m-%d')),
                                    y=alt.Y('nav', title='单位净值', scale=alt.Scale(zero=False)),
                                    tooltip=['date','nav']
                                ).properties(height=250)
                                st.altair_chart(chart_hist, width='stretch')  # 替换 use_container_width=True

                        # 重仓股详情
                        with st.expander("🔍 查看重仓股详情", expanded=False):
                            details = item['Details']
                            df_det = pd.DataFrame(details)
                            if not df_det.empty:
                                df_det = df_det[['code','name','weight','price','change']]
                                df_det.columns = ['代码','名称','权重(%)','现价','涨跌(%)']
                                for col in ['权重(%)','现价','涨跌(%)']:
                                    df_det[col] = pd.to_numeric(df_det[col], errors='coerce').fillna(0.0)
                                def highlight_change(val):
                                    if val>0: return 'color:#d63031'
                                    elif val<0: return 'color:#00b894'
                                    return ''
                                st.dataframe(
                                    df_det.style.map(highlight_change, subset=['涨跌(%)'])
                                    .format({'权重(%)':"{:.2f}",'现价':"{:.2f}",'涨跌(%)':"{:+.2f}"}),
                                    width='stretch'  # 替换 use_container_width=True
                                )
                    else:
                        st.error(f"获取数据失败: {item.get('状态')}")

# ====================== 运行 ======================
if not codes:
    st.warning("请输入基金代码")
    st.stop()

# 自动刷新逻辑
if auto_refresh:
    render_dashboard()
    time.sleep(60)
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()
else:
    render_dashboard()

# 手动刷新按钮
if refresh_btn:
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()