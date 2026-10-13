"""报销助手智能体 - Streamlit主入口"""
import sys
import os
import io
import json
import tempfile
import zipfile
import math
import re
from datetime import datetime

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from config import (
    DEFAULT_MODEL,
    EXPENSE_TYPES,
    load_prompt,
    PROMPT_NAMES,
    get_api_key,
    get_builtin_template_bytes,
)
from services.zip_extractor import ZipExtractor
from services.invoice_parser import InvoiceParser
from services.excel_generator import ExcelGenerator
from services.data_correlator import DataCorrelator
from services.record_validation import normalize_record


def _amount_or_zero(value) -> float:
    """用于页面汇总展示：非法输入按 0 展示，但保存时会阻止继续。"""
    try:
        amount = float(value)
        return amount if math.isfinite(amount) else 0.0
    except (TypeError, ValueError):
        return 0.0


EDITOR_FIELDS = {
    "date": "date", "type": "type", "amount": "amount", "start": "start_location",
    "end": "end_location", "work": "work_content", "hotel": "hotel_name",
    "checkin": "check_in_date", "checkout": "check_out_date", "nights": "nights",
    "rate": "daily_rate", "entertain": "is_entertainment", "substitute": "need_substitute",
}


def _sync_editor_widgets():
    """导航点击与字段编辑同一次提交时，也保留最新输入。"""
    if st.session_state.current_step != 1:
        return
    for i, invoice in enumerate(st.session_state.invoices):
        for prefix, field in EDITOR_FIELDS.items():
            key = f"{prefix}_{i}"
            if key in st.session_state:
                invoice[field] = st.session_state[key]


def _navigate(step):
    _sync_editor_widgets()
    st.session_state.current_step = step


def _clear_editor_widgets():
    for key in list(st.session_state):
        if any(re.fullmatch(re.escape(prefix) + r"_\d+", key) for prefix in EDITOR_FIELDS):
            del st.session_state[key]


def _validated_invoices(invoices):
    normalized, errors = [], []
    for i, invoice in enumerate(invoices):
        try:
            if invoice.get("type") in {"出差补贴", "通讯补贴", "餐补", "打车行程单"}:
                raise ValueError("补贴由系统自动生成，行程单应为打车记录，请核对费用类型")
            normalized.append(normalize_record(invoice, allow_zero=True))
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"第 {i + 1} 条：{exc}")
    return normalized, errors


def _generation_fingerprint(invoices, travel_days, work_description: str) -> str:
    """生成当前报销数据指纹，避免用户绕过保存按钮时下载旧 Excel。"""
    return json.dumps(
        {
            "invoices": invoices,
            "travel_days": travel_days,
            "work_description": work_description,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


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
    :root {
        --ra-primary: #059669;
        --ra-primary-dark: #047857;
        --ra-surface: #ffffff;
        --ra-border: #d7e5e0;
        --ra-text: #0f172a;
        --ra-muted: #64748b;
    }
    /* 桌面端保持宽松的数据工作区，移动端自动收紧。 */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
    }
    h1, h2, h3 { color: var(--ra-text); letter-spacing: -0.02em; }
    .stButton > button, .stDownloadButton > button {
        min-height: 44px;
        border-radius: 10px;
        border: 1px solid var(--ra-border);
        font-weight: 600;
        transition: background-color 160ms ease, border-color 160ms ease, transform 160ms ease;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        border-color: var(--ra-primary);
        transform: translateY(-1px);
    }
    .stButton > button:focus-visible, .stDownloadButton > button:focus-visible,
    input:focus-visible, textarea:focus-visible {
        outline: 3px solid rgba(5, 150, 105, 0.32) !important;
        outline-offset: 2px;
    }
    div[data-testid="stMetric"] {
        background: var(--ra-surface);
        border: 1px solid var(--ra-border);
        border-radius: 12px;
        padding: 0.85rem 1rem;
    }
    div[data-testid="stMetricLabel"] { color: var(--ra-muted); }
    div[data-testid="stMetricValue"] { color: var(--ra-text); font-variant-numeric: tabular-nums; }
    .stAlert { border-radius: 10px; }
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
        .main .block-container { padding-top: 1rem; padding-bottom: 2rem; }
    }
    /* 全局优化 */
    .stProgress > div > div > div {
        background-color: var(--ra-primary);
    }
    .stExpander > div:first-child {
        font-weight: 600;
    }
    /* 文件上传框突出显示 */
    [data-testid="stFileUploader"] {
        border: 2px dashed var(--ra-primary) !important;
        border-radius: 12px !important;
        padding: 1rem !important;
        background: #f0f8f6 !important;
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
if "generated_excel_fingerprint" not in st.session_state:
    st.session_state.generated_excel_fingerprint = None
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
        options=["qwen-vl-max", "qwen-vl-plus"],
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
                del st.session_state[f"ta_{pkey}"]
                st.rerun()


# ========== 主区域 ==========
st.title("📋 报销助手智能体")
st.caption("上传发票压缩包和工作描述，自动生成报销单")
st.caption("版本：2026.10.13 · 识别完整性与导出校验更新")

# 步骤导航
col_a, col_b, col_c = st.columns(3)
with col_a:
    st.button("1️⃣ 上传文件", use_container_width=True, type="primary" if st.session_state.current_step == 0 else "secondary", on_click=_navigate, args=(0,))
with col_b:
    st.button("2️⃣ 预览与编辑", use_container_width=True, type="primary" if st.session_state.current_step == 1 else "secondary", on_click=_navigate, args=(1,))
with col_c:
    st.button("3️⃣ 生成报销单", use_container_width=True, type="primary" if st.session_state.current_step == 2 else "secondary", on_click=_navigate, args=(2,))

st.divider()

# 根据 current_step 确定当前步骤
step_index = st.session_state.current_step
if step_index > 0 and st.session_state.get("failed_files"):
    st.warning("以下文件未完整识别，当前报销数据不包含这些文件。请返回上传页重试；若继续导出，请根据原单在 Excel 中补齐：")
    for failed_filename in st.session_state.failed_files:
        st.text(failed_filename)
if step_index > 0 and st.session_state.get("duplicate_files"):
    st.info("以下文件内容完全相同，已仅保留一份，避免重复报销：")
    for duplicate in st.session_state.duplicate_files:
        st.text(f"{duplicate['filename']} → 保留 {duplicate['original']}")

# ========== 步骤1：上传文件 ==========
if step_index == 0:
    st.header("步骤一：上传发票并解析")

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
            st.warning("⚠️ 未填写 API Key，照片解析和工作内容智能匹配将不可用，其余功能正常")

        if not zip_file:
            st.error("❌ 请上传发票压缩包")
            st.stop()

        # 加载内置模板
        st.session_state.template_bytes = get_builtin_template_bytes()
        st.session_state.work_description = work_description

        # 解压压缩包
        with st.spinner("📦 正在解压压缩包..."):
            extractor = ZipExtractor()
            try:
                file_list = extractor.extract_zip(zip_file.getvalue())
            except zipfile.BadZipFile:
                extractor.cleanup()
                st.error("❌ ZIP 文件无法读取，请重新压缩后再上传。")
                st.stop()
            except ValueError as exc:
                extractor.cleanup()
                st.error(f"❌ ZIP 文件包含不安全路径，已拒绝处理：{exc}")
                st.stop()
            except (RuntimeError, OSError):
                extractor.cleanup()
                st.error("❌ 压缩包无法解压，请确认未加密且文件完整后重新上传。")
                st.stop()
            if not file_list:
                extractor.cleanup()
                st.error("压缩包中没有支持的 PDF 或图片，请检查文件后重新上传。")
                st.stop()
            if st.session_state.extractor:
                st.session_state.extractor.cleanup()
            st.session_state.extractor = extractor
            st.session_state.duplicate_files = extractor.duplicate_files
            _clear_editor_widgets()
            st.session_state.generated_excel = None
            st.session_state.generated_excel_fingerprint = None
            st.session_state.travel_days = {}
            st.session_state.pop("confirm_partial_export", None)

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
        with st.spinner("🔎 正在识别发票..."):
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
            st.session_state.failed_files = parser.failed_files
            progress_bar.progress(1.0)
            status_text.text(f"识别结束，共取得 {len(invoices)} 条记录，{len(parser.failed_files)} 个文件未完整识别")
            if not invoices:
                st.error("没有取得可用记录。请检查文件是否清晰、完整，并确认识别服务配置后重试。")
                for failed_filename in parser.failed_files:
                    st.text(failed_filename)
                st.stop()

        # 数据关联智能体：打车发票+行程单配对、快递明细去重、工作内容匹配
        with st.spinner("🔗 正在运行关联智能体（打车配对 + 快递明细整理 + 工作内容匹配）..."):
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
            st.success("✅ 数据关联完成（打车行程单配对 + 快递明细整理 + 工作内容匹配）")

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
                + f"，自然日共{generator.total_travel_days(travel_days)}天"
            )

        # 火车票往返配对校验
        roundtrip_check = DataCorrelator.check_train_roundtrip(invoices)
        if roundtrip_check['summary']:
            st.warning("🚄 火车票往返配对提醒：\n" + roundtrip_check['summary'])
        else:
            st.success("✅ 火车票往返配对检查正常")

        st.info("识别流程结束，正在跳转到预览页面核对结果。")
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
    summary_parts = [f"{t}: {c}条" for t, c in type_counts.items()]
    st.info(f"📊 共 {len(invoices)} 条记录 — " + "、".join(summary_parts))
    st.caption("编辑内容会自动保留；保存或进入生成页时，会校验数据并更新出差天数。请核对日期、路线和金额是否与原单一致。")
    st.caption("打车发票与行程单按金额、日期关联；相同金额的不同订单仍需对照原单核对。")

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
            cat_total = sum(_amount_or_zero(inv.get("amount", 0)) for _, inv in items)
            st.caption(f"{len(items)} 条，合计 ¥{cat_total:.2f}")

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
                            "日期", value=inv.get("date", ""), key=f"date_{i}",
                            help="格式：YYYY-MM-DD，例如 2026-09-07",
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
                            "金额", value=str(inv.get("amount", "")), key=f"amount_{i}",
                            help="请输入非负数字，最多保留两位小数",
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

    normalized, validation_errors = _validated_invoices(edited_invoices)
    if validation_errors:
        st.session_state.generated_excel = None
        st.session_state.generated_excel_fingerprint = None
        st.error("请先修正以下内容：\n" + "\n".join(f"- {item}" for item in validation_errors))

    # 保存修改按钮
    if st.button("💾 保存修改", type="primary", use_container_width=True):
        if validation_errors:
            st.stop()
        st.session_state.invoices = normalized
        # 编辑日期/地点/酒店入住日期后立即同步出差天数，避免生成报销单仍使用旧统计。
        template_bytes = st.session_state.get("template_bytes") or get_builtin_template_bytes()
        st.session_state.template_bytes = template_bytes
        generator = ExcelGenerator(template_bytes)
        st.session_state.travel_days = generator.calculate_travel_days(normalized)
        st.session_state.generated_excel = None  # 清除已生成的Excel，步骤3重新生成
        st.session_state.generated_excel_fingerprint = None
        st.success("✅ 修改已保存，正在跳转...")
        st.session_state.current_step = 2
        st.rerun()

    # 出差天数统计
    st.divider()
    st.subheader("📅 出差天数统计")
    travel_days = st.session_state.get("travel_days", {})

    recalc_col, hint_col = st.columns([1, 3])
    with recalc_col:
        if st.button("🔄 重新计算", use_container_width=True):
            if validation_errors:
                st.stop()
            template_bytes = st.session_state.get("template_bytes") or get_builtin_template_bytes()
            st.session_state.template_bytes = template_bytes
            generator = ExcelGenerator(template_bytes)
            st.session_state.travel_days = generator.calculate_travel_days(normalized)
            st.session_state.generated_excel = None
            st.session_state.generated_excel_fingerprint = None
            st.rerun()
    with hint_col:
        st.caption("修改日期、地点或酒店入住/离店日期后，点击“重新计算”；点击“保存修改”也会自动更新。")

    if travel_days:
        cols = st.columns(len(travel_days) + 1)
        for idx, (city, days) in enumerate(travel_days.items()):
            cols[idx].metric(label=city, value=f"{days}天")
        stats_generator = ExcelGenerator(st.session_state.get("template_bytes") or get_builtin_template_bytes())
        stats_generator.calculate_travel_days(normalized if not validation_errors else [])
        total_days = stats_generator.total_travel_days(travel_days)
        cols[-1].metric(label="自然日合计", value=f"{total_days}天")
        st.caption("出发日和返回日均计入；同日跨城市会出现在各城市统计中，但总天数和补贴只计一次。缺少行程依据时请核对原单。")
    else:
        st.info("暂无出差天数统计")


# ========== 步骤3：生成报销单 ==========
elif step_index == 2:
    st.header("步骤三：生成报销单")

    if not st.session_state.invoices:
        st.warning("⚠️ 请先在步骤一完成文件上传和解析")
        st.stop()

    if not hasattr(st.session_state, "template_bytes"):
        # 自动加载内置模板
        st.session_state.template_bytes = get_builtin_template_bytes()

    # 数据汇总
    invoices, validation_errors = _validated_invoices(st.session_state.invoices)
    if validation_errors:
        st.session_state.generated_excel = None
        st.session_state.generated_excel_fingerprint = None
        st.error("无法生成报销单，请返回预览页修正：\n" + "\n".join(f"- {item}" for item in validation_errors))
        st.stop()
    st.session_state.invoices = invoices
    generator = ExcelGenerator(st.session_state.template_bytes)
    travel_days = generator.calculate_travel_days(invoices)
    st.session_state.travel_days = travel_days
    work_description = st.session_state.get("work_description", "")

    st.subheader("📊 数据汇总")
    total_all = sum(_amount_or_zero(inv.get("amount", 0)) for inv in invoices)
    meal_exclude = sum(
        _amount_or_zero(inv.get("amount", 0)) for inv in invoices
        if inv.get("type") == "餐饮" and not inv.get("is_entertainment", False)
    )
    total_reimburse = total_all - meal_exclude
    total_days = generator.total_travel_days(travel_days)
    subsidy = total_days * 50
    generation_fingerprint = _generation_fingerprint(invoices, travel_days, work_description)

    # 2x2 布局（窄屏友好）
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.metric("记录总数", f"{len(invoices)} 条")
    with row1_col2:
        st.metric("导出总金额", f"¥{total_reimburse + subsidy + 700:.2f}")

    row2_col1, row2_col2 = st.columns(2)
    with row2_col1:
        st.metric("出差天数", f"{total_days} 天")
    with row2_col2:
        st.metric("出差补贴", f"¥{subsidy:.2f}")

    if meal_exclude > 0:
        st.caption(f"含票据¥{total_all:.2f}，已扣除补贴替票餐费¥{meal_exclude:.2f}")
    st.caption(f"费用金额 ¥{total_reimburse:.2f}，另加出差补贴（50元/天）、通讯补贴200元和餐补500元。")
    if st.session_state.get("failed_files"):
        if not st.checkbox("我已核对未识别文件，知道导出不包含这些费用", key="confirm_partial_export"):
            st.session_state.generated_excel = None
            st.session_state.generated_excel_fingerprint = None
            st.info("请先核对上方未识别文件；需要补齐时，请返回上传页重新识别。")
            st.stop()

    st.divider()

    # 自动生成 Excel（进入步骤3时立即执行）
    if (
        not st.session_state.generated_excel
        or st.session_state.get("generated_excel_fingerprint") != generation_fingerprint
    ):
        st.session_state.generated_excel = None
        st.session_state.generated_excel_fingerprint = None
        with st.spinner("🔄 正在生成报销单..."):
            try:
                template_bytes = st.session_state.get("template_bytes") or get_builtin_template_bytes()
                st.session_state.template_bytes = template_bytes
                generator = ExcelGenerator(template_bytes)
                excel_bytes = generator.generate(
                    invoices=invoices,
                    travel_days=travel_days,
                    work_description=work_description,
                )
                st.session_state.generated_excel = excel_bytes
                st.session_state.generated_excel_fingerprint = generation_fingerprint
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
            st.session_state.generated_excel_fingerprint = None
            st.rerun()
