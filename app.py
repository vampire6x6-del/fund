import streamlit as st
import pandas as pd
import time
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,%(msecs)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 导入业务模块（根据你的实际路径调整）
from src.data_fetcher import get_fund_holdings, get_realtime_stock_prices, get_fund_history_nav, get_fund_real_time_estimate_from_1234567
from src.valuation import estimate_nav_change

# 页面基础配置
st.set_page_config(page_title="基金净值估算器", layout="wide")
st.title("🇨🇳 中国公募基金实时净值估算系统")
st.markdown("基于前十大重仓股实时估算基金净值涨跌幅。")

# 全局常量
VALUATION_OPTIONS = ["原有手动加权", "天天基金API"]

# ====================== 侧边栏配置 ======================
st.sidebar.header("配置")
default_funds = "019454,165520,021986,025208,012544,012920,270023,001467,016532,018043,270042,166301,002611,457001,539002"
fund_input = st.sidebar.text_area("基金代码 (英文逗号分隔)", value=default_funds, height=100)
codes = [c.strip() for c in fund_input.split(',') if c.strip()]

# 初始化估值方式配置（确保每个基金都有默认值）
if "fund_valuation_mode" not in st.session_state:
    st.session_state["fund_valuation_mode"] = {code: "原有手动加权" for code in codes}

# 批量估值方式设置
st.sidebar.subheader("批量估值方式")
batch_mode = st.sidebar.radio(
    "未单独设置的基金使用此默认值",
    options=VALUATION_OPTIONS,
    index=0
)

if st.sidebar.button("应用批量设置到所有基金（覆盖已设置）"):
    for code in codes:
        st.session_state["fund_valuation_mode"][code] = batch_mode
    st.rerun()

# 刷新相关配置
auto_refresh = st.sidebar.checkbox("自动刷新 (每60秒)", value=False)
refresh_btn = st.sidebar.button("立即刷新数据")

# ====================== 核心业务逻辑 ======================
from concurrent.futures import ThreadPoolExecutor, as_completed

@st.cache_data(ttl=3600)
def fetch_history_cached(code, days):
    """缓存基金历史净值数据"""
    return get_fund_history_nav(code, days)

def process_single_fund(code, valuation_mode):
    """处理单个基金的估值计算"""
    try:
        # 天天基金API估值方式
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
                logging.warning(f"天天基金API调用失败，降级为原有方式（{code}）")
                valuation_mode = "原有手动加权"

        # 原有手动加权估值方式
        if valuation_mode == "原有手动加权":
            result_data = get_fund_holdings(code)
            if not result_data:
                return {
                    '基金代码': code,
                    '基金名称': '--',
                    '持仓日期': '--',
                    '状态': '获取持仓失败',
                    '估算涨跌': None,
                    'Details': [],
                    '估值方式': '原有手动加权'
                }

            # 解析持仓数据
            if len(result_data) == 3:
                fund_name, holdings, report_date = result_data
            else:
                fund_name, holdings = result_data
                report_date = "--"

            # 获取股票实时价格并计算估值
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
        # 兜底：尝试使用天天基金API
        ttfund_data = get_fund_real_time_estimate_from_1234567(code)
        if ttfund_data:
            return {
                '基金代码': code,
                '基金名称': ttfund_data['fund_name'],
                '持仓日期': f"估值更新：{ttfund_data['update_time']}",
                '状态': '成功（天天基金API-兜底）',
                '估算涨跌': ttfund_data['estimate_change'],
                'Details': [],
                'History': None,
                '更新时间': ttfund_data['update_time'],
                '估值方式': '天天基金API-兜底'
            }
        # 完全失败的情况
        return {
            '基金代码': code,
            '基金名称': 'Error',
            '持仓日期': '--',
            '状态': f'Error: {str(e)}',
            '估算涨跌': None,
            'Details': [],
            '估值方式': '无'
        }

def process_funds(code_list):
    """批量处理基金估值"""
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures_map = {}
        for code in code_list:
            if code.strip():
                # 从session_state读取最新估值方式
                valuation_mode = st.session_state["fund_valuation_mode"].get(code, "原有手动加权")
                future = executor.submit(process_single_fund, code.strip(), valuation_mode)
                futures_map[future] = code.strip()

        # 收集结果
        for future in as_completed(futures_map):
            try:
                data = future.result()
                results.append(data)
            except Exception as e:
                logging.error(f"Future处理基金 {futures_map[future]} 出错: {e}")
    return results

# ====================== 页面渲染 ======================
dashboard = st.empty()

def render_dashboard():
    """渲染主仪表盘"""
    with dashboard.container():
        # 空值检查
        codes = [c.strip() for c in fund_input.split(',') if c.strip()]
        if not codes:
            st.warning("请输入至少一个基金代码。")
            return

        # 获取基金数据
        data = process_funds(codes)
        if not data:
            st.error("未找到有效基金数据。")
            return

        # 构建概览数据
        summary_data = []
        for item in data:
            code = item['基金代码']
            current_mode = st.session_state["fund_valuation_mode"].get(code, "原有手动加权")
            
            summary_data.append({
                '基金代码': code,
                '基金名称': item['基金名称'],
                '估算涨跌': item['估算涨跌'],
                '当前估值方式': current_mode,
                '状态': item['状态'],
                '更新时间': item.get('更新时间', ''),
                '持仓日期': item['持仓日期'],
            })

        df_summary = pd.DataFrame(summary_data)

        # 涨跌颜色样式函数
        def color_change(val):
            if isinstance(val, (float, int)):
                if val > 0:
                    return 'color: #D32F2F'  # 红色
                elif val < 0:
                    return 'color: #388E3C'  # 绿色
            return ''

        # ====================== 概览表格（最大化显示） ======================
        st.subheader("📊 基金估值概览")
        
        # 列顺序配置
        column_order = [
            '基金代码', '基金名称', '估算涨跌', 
            '当前估值方式', '状态', '更新时间', '持仓日期'
        ]
        df_display = df_summary[column_order]

        # 列属性配置
        column_config = {
            "基金代码": st.column_config.TextColumn("基金代码", width="small"),
            "基金名称": st.column_config.TextColumn("基金名称", width="medium"),
            "估算涨跌": st.column_config.NumberColumn(
                "估算涨跌",
                format="%.2f%%",
                width="small",
                help="基于当前估值方式计算的实时涨跌幅"
            ),
            "当前估值方式": st.column_config.TextColumn("当前估值方式", width="small"),
            "状态": st.column_config.TextColumn("状态", width="medium"),
            "更新时间": st.column_config.TextColumn("更新时间", width="small"),
            "持仓日期": st.column_config.TextColumn("持仓日期", width="small"),
        }

        # 最大化表格高度（占据大部分屏幕空间）
        styler = df_display.style\
            .format({'估算涨跌': "{:+.2f}%"}, na_rep="--")\
            .map(color_change, subset=['估算涨跌'])
            
        st.dataframe(
            styler,
            column_config=column_config,
            hide_index=True,
            width='stretch',
            height=600,  # 大幅增加表格高度
            use_container_width=False
        )

        # ====================== 估值方式操作区（紧凑布局） ======================
        st.subheader("⚙️ 估值方式操作")
        st.markdown("💡 点击按钮快速切换估值方式")
        
        # 使用紧凑的网格布局，减少垂直空间占用
        # 每行显示3个基金的操作按钮
        fund_chunks = [df_summary[i:i+3] for i in range(0, len(df_summary), 3)]
        
        for chunk in fund_chunks:
            # 为每个基金创建一列
            cols = st.columns([1]*len(chunk))
            for idx, (col, (_, row)) in enumerate(zip(cols, chunk.iterrows())):
                with col:
                    code = row['基金代码']
                    name = row['基金名称'][:6] + "..." if len(row['基金名称']) > 6 else row['基金名称']
                    current_mode = row['当前估值方式']
                    
                    # 紧凑的信息展示
                    st.markdown(f"**{code}**<br>{name}", unsafe_allow_html=True)
                    st.caption(f"当前：{current_mode}")
                    
                    # 小型按钮
                    btn_cols = st.columns(2)
                    with btn_cols[0]:
                        if st.button(
                            "手动加权", 
                            key=f"btn_manual_{code}", 
                            type="primary" if current_mode == "原有手动加权" else "secondary",
                            use_container_width=True
                        ):
                            st.session_state["fund_valuation_mode"][code] = "原有手动加权"
                            st.rerun()
                    with btn_cols[1]:
                        if st.button(
                            "天天API", 
                            key=f"btn_api_{code}", 
                            type="primary" if current_mode == "天天基金API" else "secondary",
                            use_container_width=True
                        ):
                            st.session_state["fund_valuation_mode"][code] = "天天基金API"
                            st.rerun()
            # 小分隔线
            st.markdown("---")

        # ====================== 详细信息区（可折叠） ======================
        with st.expander("📋 查看基金详细信息", expanded=False):
            tabs = st.tabs([f"{d['基金代码']}" for d in data])
            for i, tab in enumerate(tabs):
                with tab:
                    item = data[i]
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
                                    st.altair_chart(chart_intra, width='stretch')
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
                                st.altair_chart(chart_hist, width='stretch')

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
                                    width='stretch'
                                )
                    else:
                        st.error(f"获取数据失败: {item.get('状态')}")

# ====================== 主程序入口 ======================
if not codes:
    st.warning("请在侧边栏输入至少一个基金代码。")
    st.stop()

# 自动刷新逻辑
if auto_refresh:
    while True:
        render_dashboard()
        time.sleep(60)
        st.rerun()
else:
    render_dashboard()

# 手动刷新按钮
if refresh_btn:
    st.rerun()