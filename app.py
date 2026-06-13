"""报销助手智能体 - Streamlit主入口"""
import sys
import os
import io
import json
import tempfile

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from config import (
    DEFAULT_MODEL,
    EXPENSE_TYPES,
    load_prompt,
    PROMPT_NAMES,
    get_api_key,
)
from services.zip_extractor import ZipExtractor
from services.invoice_parser import InvoiceParser
from services.excel_generator import ExcelGenerator
from services.data_correlator import DataCorrelator


# ========== 页面配置 ==========
st.set_page_config(
    page_title="报销助手智能体",
    page_icon="📋",
    layout="wide",
)

# 手机端样式优化
st.markdown(
    """
    <style>
    /* 手机端全局优化 */
    @media (max-width: 768px) {
        /* 主区域缩小内边距 */
        .main .block-container {
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        /* 标题字体缩小 */
        h1 { font-size: 1.4rem !important; }
        h2 { font-size: 1.1rem !important; }
        h3 { font-size: 1rem !important; }
        /* 按钮全宽 */
        .stButton > button {
            width: 100% !important;
            font-size: 0.9rem !important;
        }
        /* 输入框字体加大便于操作 */
        .stTextInput input, .stTextArea textarea {
            font-size: 16px !important;
        }
        /* 列布局堆叠 */
        .stColumns > div {
            flex-direction: column !important;
        }
    }
    /* 全局优化 */
    .stProgress > div > div > div {
        background-color: #4CAF50;
    }
    .stExpander > div:first-child {
        font-weight: 600;
    }
    /* 文件上传框突出显示 */
    [data-testid="stFileUploader"] {
        border: 2px dashed #4CAF50 !important;
        border-radius: 8px !important;
        padding: 1rem !important;
    }
    /* 隐藏 Streamlit Cloud 右上角工具栏 */
    button[kind="icon"],
    button[data-testid="baseButton-headerNoSecondary"],
    button[aria-label="Main menu"],
    div[data-testid="stToolbar"],
    div[data-testid="stHeaderActionElements"] {
        display: none !important;
        visibility: hidden !important;
    }
    header[data-testid="stHeader"] {
        background-color: transparent !important;
    }
    /* 窄屏 metric 自适应 */
    @media (max-width: 900px) {
        div[data-testid="stMetric"] {
            min-width: 0 !important;
        }
        div[data-testid="stMetric"] label {
            font-size: 0.8rem !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            font-size: 1.2rem !important;
        }
    }
    /* 分类标签样式 */
    .category-header {
        background: linear-gradient(90deg, #f0f2f6 0%, transparent 100%);
        padding: 8px 12px;
        border-radius: 6px;
        margin-bottom: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ========== 会话状态初始化 ==========
if "api_key" not in st.session_state:
    st.session_state.api_key = get_api_key("")
if "model" not in st.session_state:
    st.session_state.model = DEFAULT_MODEL
if "system_prompt" not in st.session_state:
    st.session_state.system_prompt = load_prompt("default_prompt")
if "invoice_parse_prompt" not in st.session_state:
    st.session_state.invoice_parse_prompt = load_prompt("invoice_parse")
if "hotel_infer_prompt" not in st.session_state:
    st.session_state.hotel_infer_prompt = load_prompt("hotel_infer")
if "work_match_prompt" not in st.session_state:
    st.session_state.work_match_prompt = load_prompt("work_match")
if "extractor" not in st.session_state:
    st.session_state.extractor = None
if "invoices" not in st.session_state:
    st.session_state.invoices = []
if "generated_excel" not in st.session_state:
    st.session_state.generated_excel = None
if "current_step" not in st.session_state:
    st.session_state.current_step = 0


# ========== 侧边栏：配置区 ==========
with st.sidebar:
    st.header("⚙️ 系统配置")

    # API Key
    st.subheader("🔑 API 配置")
    default_key = get_api_key("")
    st.session_state.api_key = st.text_input(
        "通义千问 API Key",
        value=st.session_state.api_key or default_key,
        type="password",
        help="请输入您的通义千问 DashScope API Key",
    )

    # 模型选择
    st.session_state.model = st.selectbox(
        "模型选择",
        options=["qwen-vl-max", "qwen-vl-plus", "qwen-max"],
        index=0,
        help="推荐使用 qwen-vl-max，视觉识别能力最强",
    )

    st.divider()
    
    # 提示词配置
    st.subheader("📝 提示词配置")

    PROMPT_KEYS = ["default_prompt", "invoice_parse", "hotel_infer", "work_match"]
    PROMPT_STATE_KEYS = {
        "default_prompt": "system_prompt",
        "invoice_parse": "invoice_parse_prompt",
        "hotel_infer": "hotel_infer_prompt",
        "work_match": "work_match_prompt",
    }

    for pkey in PROMPT_KEYS:
        with st.expander(f"📄 {PROMPT_NAMES[pkey]}", expanded=False):
            state_key = PROMPT_STATE_KEYS[pkey]
            st.session_state[state_key] = st.text_area(
                f"编辑 {PROMPT_NAMES[pkey]}",
                value=st.session_state[state_key],
                height=200,
                key=f"ta_{pkey}",
                help=f"可自定义「{PROMPT_NAMES[pkey]}」",
            )
            if st.button(f"🔄 重置", key=f"reset_{pkey}"):
                st.session_state[state_key] = load_prompt(pkey)
                st.rerun()


# ========== 主区域 ==========
st.title("📋 报销助手智能体")
st.caption("上传压缩包、Excel模板和工作描述，自动生成报销单")

# 步骤导航
col_a, col_b, col_c = st.columns(3)
with col_a:
    if st.button("1️⃣ 上传文件", use_container_width=True, type="primary" if st.session_state.current_step == 0 else "secondary"):
        st.session_state.current_step = 0
        st.rerun()
with col_b:
    if st.button("2️⃣ 预览与编辑", use_container_width=True, type="primary" if st.session_state.current_step == 1 else "secondary"):
        st.session_state.current_step = 1
        st.rerun()
with col_c:
    if st.button("3️⃣ 生成报销单", use_container_width=True, type="primary" if st.session_state.current_step == 2 else "secondary"):
        st.session_state.current_step = 2
        st.rerun()

st.divider()

# 根据 current_step 确定当前步骤
step_index = st.session_state.current_step

# ========== 步骤1：上传文件 ==========
if step_index == 0:
    st.header("步骤一：上传必要文件")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📄 Excel 报销模板")
        template_file = st.file_uploader(
            "上传空白 Excel 报销模板",
            type=["xlsx", "xls"],
            key="template_upload",
            help="上传当月的空白报销Excel模板文件",
        )
        if template_file:
            st.success(f"✅ 模板已上传: {template_file.name}")

    with col2:
        st.subheader("📦 发票压缩包")
        zip_file = st.file_uploader(
            "上传发票压缩包 (ZIP格式)",
            type=["zip"],
            key="zip_upload",
            help="上传包含所有发票和行程单的ZIP压缩包（支持PDF和图片）",
        )
        if zip_file:
            st.success(f"✅ 压缩包已上传: {zip_file.name}")

    st.divider()
    st.subheader("💼 当月工作内容描述")
    work_description = st.text_area(
        "请输入当月工作内容描述",
        placeholder="例如：衢州市中医院放射回访、桐庐妇保放射培训、衢州二院放射调试",
        height=100,
        key="work_desc",
    )

    st.divider()

    # 开始解析按钮
    if st.button("🚀 开始解析发票", type="primary", use_container_width=True):
        # 验证输入
        if not st.session_state.api_key:
            st.warning("⚠️ 未填写 API Key，酒店日期推算和照片解析将不可用，其余功能正常")

        if not zip_file:
            st.error("❌ 请上传发票压缩包")
            st.stop()

        if not template_file:
            st.error("❌ 请上传 Excel 报销模板")
            st.stop()

        # 保存模板
        st.session_state.template_bytes = template_file.getvalue()
        st.session_state.work_description = work_description

        # 解压压缩包
        with st.spinner("📦 正在解压压缩包..."):
            extractor = ZipExtractor()
            file_list = extractor.extract_zip(zip_file.getvalue())
            st.session_state.extractor = extractor

            img_count, pdf_count = extractor.get_file_count()
            st.info(
                f"📊 解压完成: 共识别 {len(file_list)} 个文件 "
                f"(图片: {img_count}, PDF: {pdf_count})"
            )

        # 展示解压的文件列表（默认收起）
        with st.expander(f"📁 解压完成，共 {len(file_list)} 个文件（点击展开详情）", expanded=False):
            for f in file_list:
                icon = "🖼️" if f["type"] == "image" else "📄"
                img_count = len(f.get("images", []))
                st.write(
                    f"{icon} **{f['filename']}** "
                    f"(类型: {f['type']}, 图片数: {img_count})"
                )

        # 解析发票
        with st.spinner("🤖 正在调用 AI 识别发票..."):
            progress_bar = st.progress(0)
            status_text = st.empty()

            def progress_callback(current, total, filename):
                progress_bar.progress(current / total)
                status_text.text(
                    f"正在识别 ({current}/{total}): {filename}"
                )

            parser = InvoiceParser(
                api_key=st.session_state.api_key,
                model=st.session_state.model,
            )

            invoices = parser.parse_all_invoices(
                file_list,
                system_prompt=st.session_state.system_prompt,
                progress_callback=progress_callback,
                invoice_parse_prompt=st.session_state.invoice_parse_prompt,
            )

            st.session_state.invoices = invoices
            progress_bar.progress(1.0)
            status_text.text(f"✅ 发票识别完成，共识别 {len(invoices)} 张票据")

        # 数据关联智能体：打车发票+行程单配对、酒店日期推算、工作内容匹配
        with st.spinner("🔗 正在运行关联智能体（打车配对 + 酒店日期推算 + 工作内容匹配）..."):
            correlator = DataCorrelator(
                api_key=st.session_state.api_key,
                model="qwen-max",
            )
            invoices = correlator.correlate_all(
                raw_invoices=invoices,
                work_description=work_description,
                hotel_infer_prompt=st.session_state.hotel_infer_prompt,
                work_match_prompt=st.session_state.work_match_prompt,
            )
            st.session_state.invoices = invoices
            st.success("✅ 数据关联完成（打车行程单配对 + 酒店日期推算 + 工作内容匹配）")

        # 计算出差天数
        generator = ExcelGenerator(st.session_state.template_bytes)
        travel_days = generator.calculate_travel_days(invoices)
        st.session_state.travel_days = travel_days

        if travel_days:
            st.info(
                "📊 出差天数统计: "
                + "、".join(
                    f"{city}({days}天)" for city, days in travel_days.items()
                )
                + f"，共{sum(travel_days.values())}天"
            )

        st.success("🎉 所有文件解析完成！正在跳转到预览页面...")
        st.session_state.current_step = 1
        st.rerun()


# ========== 步骤2：预览与编辑 ==========
elif step_index == 1:
    st.header("步骤二：预览与编辑解析结果")

    if not st.session_state.invoices:
        st.warning("⚠️ 请先在步骤一完成文件上传和解析")
        st.stop()

    invoices = st.session_state.invoices

    # ---- 汇总摘要 ----
    from collections import Counter
    type_counts = Counter(inv.get("type", "其他") for inv in invoices)
    summary_parts = [f"{t}: {c}张" for t, c in type_counts.items()]
    st.info(f"📊 共 {len(invoices)} 张票据 — " + "、".join(summary_parts))

    # ---- 分类 tabs ----
    # 定义分类顺序和图标
    CATEGORY_ORDER = [
        ("🚄 火车票", "火车票"),
        ("🚖 打车", "打车"),
        ("✈️ 飞机票", "飞机票"),
        ("🏨 酒店", "酒店"),
        ("🍽️ 餐饮", "餐饮"),
        ("📦 快递/其他", None),  # None = 其余类型
    ]

    # 构建分类数据
    categorized = {}
    for label, type_key in CATEGORY_ORDER:
        if type_key:
            items = [(i, inv) for i, inv in enumerate(invoices) if inv.get("type") == type_key]
        else:
            known_types = {t for _, t in CATEGORY_ORDER if t}
            items = [(i, inv) for i, inv in enumerate(invoices) if inv.get("type", "其他") not in known_types]
        if items:
            categorized[label] = items

    tab_labels = list(categorized.keys())
    if len(tab_labels) > 1:
        tabs = st.tabs(tab_labels)
    else:
        tabs = [st.container()]

    edited_invoices = list(invoices)  # shallow copy

    for tab_idx, (label, items) in enumerate(categorized.items()):
        container = tabs[tab_idx] if len(tab_labels) > 1 else tabs[0]
        with container:
            # 分类小计
            cat_total = sum(float(inv.get("amount", 0)) for _, inv in items)
            st.caption(f"{len(items)} 张，合计 ¥{cat_total:.2f}")

            for i, inv in items:
                with st.expander(
                    f"#{i+1} | {inv.get('date', '')} | "
                    f"{inv.get('start_location', '')}{(' → ' + inv.get('end_location', '')) if inv.get('end_location') else ''} | "
                    f"¥{inv.get('amount', '')}",
                    expanded=False,
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        inv["date"] = st.text_input(
                            "日期", value=inv.get("date", ""), key=f"date_{i}"
                        )
                        inv["type"] = st.selectbox(
                            "费用类型",
                            options=EXPENSE_TYPES,
                            index=EXPENSE_TYPES.index(inv.get("type", "其他"))
                            if inv.get("type", "其他") in EXPENSE_TYPES
                            else EXPENSE_TYPES.index("其他"),
                            key=f"type_{i}",
                        )
                        inv["amount"] = st.text_input(
                            "金额", value=str(inv.get("amount", "")), key=f"amount_{i}"
                        )

                    with col2:
                        inv["start_location"] = st.text_input(
                            "起点", value=inv.get("start_location", ""), key=f"start_{i}",
                        )
                        inv["end_location"] = st.text_input(
                            "终点", value=inv.get("end_location", ""), key=f"end_{i}",
                        )

                    with col3:
                        inv["work_content"] = st.text_input(
                            "工作内容", value=inv.get("work_content", ""), key=f"work_{i}",
                        )
                        if inv.get("type") == "酒店":
                            inv["hotel_name"] = st.text_input(
                                "酒店名称", value=inv.get("hotel_name", ""), key=f"hotel_{i}",
                            )
                            inv["check_in_date"] = st.text_input(
                                "入住日期", value=inv.get("check_in_date", ""), key=f"checkin_{i}",
                            )
                            inv["check_out_date"] = st.text_input(
                                "离店日期", value=inv.get("check_out_date", ""), key=f"checkout_{i}",
                            )
                            inv["nights"] = st.text_input(
                                "住宿天数", value=str(inv.get("nights", "")), key=f"nights_{i}",
                            )
                            inv["daily_rate"] = st.text_input(
                                "单日单价", value=str(inv.get("daily_rate", "")), key=f"rate_{i}",
                            )

                    # 餐饮发票：标记是否为请客
                    if inv.get("type") == "餐饮":
                        inv["is_entertainment"] = st.checkbox(
                            "🍽️ 请客发票（按实际金额计入报销）",
                            value=inv.get("is_entertainment", False),
                            key=f"entertain_{i}",
                            help="餐饮发票一般作为补贴替票，不计入报销总额。勾选此项表示该发票为请客用餐，将按实际金额计入报销。",
                        )

                    # 打车行程：仅顺风车需餐费代替
                    if inv.get("type") == "打车" and "顺风车" in inv.get("car_type", ""):
                        inv["need_substitute"] = st.checkbox(
                            f"🚗 餐费代替（{inv.get('car_type', '')}）",
                            value=inv.get("need_substitute", False),
                            key=f"substitute_{i}",
                            help="顺风车无滴滴发票，需用餐饮发票代替。",
                        )

                    # 原始识别文本
                    with st.expander("📄 查看原始识别文本", expanded=False):
                        st.text(inv.get("raw_text", "无"))

    st.session_state.invoices = edited_invoices

    # 保存修改按钮
    if st.button("💾 保存修改", type="primary", use_container_width=True):
        for inv in edited_invoices:
            try:
                inv["amount"] = float(inv.get("amount", 0))
            except ValueError:
                inv["amount"] = 0.0
        st.session_state.invoices = edited_invoices
        st.session_state.generated_excel = None  # 清除已生成的Excel，步骤3重新生成
        st.success("✅ 修改已保存，正在跳转...")
        st.session_state.current_step = 2
        st.rerun()

    # 出差天数统计
    st.divider()
    st.subheader("📅 出差天数统计")
    travel_days = st.session_state.get("travel_days", {})

    if travel_days:
        cols = st.columns(len(travel_days) + 1)
        for idx, (city, days) in enumerate(travel_days.items()):
            cols[idx].metric(label=city, value=f"{days}天")
        total_days = sum(travel_days.values())
        cols[-1].metric(label="合计", value=f"{total_days}天")
    else:
        st.info("暂无出差天数统计")


# ========== 步骤3：生成报销单 ==========
elif step_index == 2:
    st.header("步骤三：生成报销单")

    if not st.session_state.invoices:
        st.warning("⚠️ 请先在步骤一完成文件上传和解析")
        st.stop()

    if not hasattr(st.session_state, "template_bytes"):
        st.warning("⚠️ 请先在步骤一上传 Excel 模板")
        st.stop()

    # 数据汇总
    invoices = st.session_state.invoices
    travel_days = st.session_state.get("travel_days", {})
    work_description = st.session_state.get("work_description", "")

    st.subheader("📊 数据汇总")
    total_all = sum(float(inv.get("amount", 0)) for inv in invoices)
    meal_exclude = sum(
        float(inv.get("amount", 0)) for inv in invoices
        if inv.get("type") == "餐饮" and not inv.get("is_entertainment", False)
    )
    total_reimburse = total_all - meal_exclude
    total_days = sum(travel_days.values())
    subsidy = total_days * 50

    # 2x2 布局（窄屏友好）
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.metric("票据总数", f"{len(invoices)} 张")
    with row1_col2:
        st.metric("报销总金额", f"¥{total_reimburse:.2f}")

    row2_col1, row2_col2 = st.columns(2)
    with row2_col1:
        st.metric("出差天数", f"{total_days} 天")
    with row2_col2:
        st.metric("出差补贴", f"¥{subsidy:.2f}")

    if meal_exclude > 0:
        st.caption(f"含票据¥{total_all:.2f}，已扣除补贴替票餐费¥{meal_exclude:.2f}")

    st.divider()

    # 自动生成 Excel（进入步骤3时立即执行）
    if not st.session_state.generated_excel:
        with st.spinner("🔄 正在生成报销单..."):
            try:
                generator = ExcelGenerator(st.session_state.template_bytes)
                excel_bytes = generator.generate(
                    invoices=invoices,
                    travel_days=travel_days,
                    work_description=work_description,
                )
                st.session_state.generated_excel = excel_bytes
            except Exception as e:
                st.error(f"❌ 生成失败: {e}")

    # 直接显示下载按钮
    if st.session_state.generated_excel:
        st.success("✅ 报销单已生成，点击下方按钮下载")

        st.download_button(
            label="📥 下载报销 Excel 文件",
            data=st.session_state.generated_excel,
            file_name="报销单_已填写.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )

        # 重新生成按钮（数据修改后可重新生成）
        if st.button("🔄 重新生成", use_container_width=True):
            st.session_state.generated_excel = None
            st.rerun()
