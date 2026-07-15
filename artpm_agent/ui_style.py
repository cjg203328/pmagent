STYLE_CSS = """
<style>
    /* ArtPM Agent UI design system */
    :root {
        /* ── 色板 ── */
        --pm-paper:        #ffffff;
        --pm-canvas:       #fafafa;
        --pm-surface:      #f7f8fa;
        --pm-sidebar-bg:   #f5f6f7;
        --pm-sidebar-hover:#eceef1;
        --pm-ink:          #1a1a2e;
        --pm-ink-secondary:#3d3d54;
        --pm-muted:        #8b8fa3;
        --pm-line:         #e8eaf0;
        --pm-line-light:   #f0f1f5;

        /* 主色 */
        --pm-accent:       #4a6cf7;
        --pm-accent-hover: #3558e0;
        --pm-accent-soft:  #eef2ff;
        --pm-accent-glow:  rgba(74, 108, 247, 0.08);

        /* 语义色 */
        --pm-success:      #22c55e;
        --pm-success-soft: #ecfdf5;
        --pm-warning:      #f59e0b;
        --pm-warning-soft: #fffbeb;
        --pm-danger:       #ef4444;
        --pm-danger-soft:  #fef2f2;

        /* 圆角 */
        --pm-radius-sm:    8px;
        --pm-radius:       12px;
        --pm-radius-lg:    16px;
        --pm-radius-xl:    20px;

        /* 间距令牌 */
        --pm-space-1: 4px;
        --pm-space-2: 8px;
        --pm-space-3: 12px;
        --pm-space-4: 16px;
        --pm-space-5: 20px;
        --pm-space-6: 24px;
        --pm-space-8: 32px;
        --pm-space-10:40px;
        --pm-space-12:48px;

        /* 布局 */
        --pm-chat-shell-width:   860px;
        --pm-chat-thread-width:  780px;
        --pm-sidebar-width:      280px;

        /* 阴影系统 */
        --pm-shadow-sm:  0 1px 2px rgba(26,26,46,0.04);
        --pm-shadow-md:  0 4px 16px rgba(26,26,46,0.06);
        --pm-shadow-lg:  0 8px 32px rgba(26,26,46,0.10);
        --pm-shadow-input:0 4px 24px rgba(26,26,46,0.08);
        --pm-shadow-card: 0 2px 12px rgba(26,26,46,0.05);

        /* 过渡 */
        --pm-transition: all 200ms cubic-bezier(.4,0,.2,1);
    }

    *, *::before, *::after {
        box-sizing: border-box;
        letter-spacing: 0 !important;
    }

    html, body {
        font-family: -apple-system, "PingFang SC", "SF Pro Text",
                     "Segoe UI", "Microsoft YaHei UI", "Noto Sans SC",
                     sans-serif;
        color: var(--pm-ink);
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    /* ── 隐藏原生杂项与默认多页导航 ── */
    #MainMenu, footer, .stDeployButton,
    [data-testid="stToolbar"],
    [data-testid="stSidebarNav"],
    [data-testid="stSidebarNavItems"],
    [data-testid="stSidebarNavSeparator"] {
        display: none !important;
    }

    header,
    [data-testid="stHeader"] {
        background: transparent !important;
        box-shadow: none !important;
    }

    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"] {
        background: var(--pm-canvas) !important;
    }

    /* ── 主内容容器 ── */
    [data-testid="stMainBlockContainer"] {
        width: 100%;
        max-width: 1280px;
        padding: 32px 44px 56px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
        max-width: calc(var(--pm-chat-shell-width) + 44px);
        padding-left: 28px;
        padding-right: 28px;
    }

    /* ── 欢迎/空状态居中 ── */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
        padding-top: clamp(160px, 22vh, 240px);
        text-align: center;
        display: flex;
        flex-direction: column;
        align-items: center;
    }

    /* ── 排版层级 ── */
    h1, h2, h3, p { margin-top: 0; }

    h1 {
        color: var(--pm-ink);
        font-size: 32px !important;
        line-height: 1.25 !important;
        font-weight: 700 !important;
        margin: 0 0 8px !important;
        letter-spacing: 0 !important;
    }

    h2 {
        color: var(--pm-ink);
        font-size: 18px !important;
        line-height: 1.45 !important;
        font-weight: 600 !important;
    }

    h3 {
        color: var(--pm-ink);
        font-size: 15px !important;
        line-height: 1.5 !important;
        font-weight: 600 !important;
    }

    p, li, label, [data-testid="stMarkdownContainer"] {
        font-size: 14px;
        line-height: 1.7;
        color: var(--pm-ink-secondary);
    }

    .page-kicker,
    .section-label,
    .sidebar-section-label {
        color: var(--pm-accent);
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: uppercase;
        line-height: 1.4;
    }

    .page-kicker { margin-bottom: 8px; }

    .page-meta {
        color: var(--pm-muted);
        font-size: 14px;
        margin: 0 0 28px;
    }

    /* ── 分区标题（带底部装饰线）── */
    .section-heading {
        align-items: baseline;
        border-bottom: 1px solid var(--pm-line-light);
        display: flex;
        justify-content: space-between;
        margin: 28px 0 16px;
        padding-bottom: 10px;
        position: relative;
    }

    .section-heading::after {
        background: linear-gradient(90deg, var(--pm-accent), transparent);
        bottom: -1px;
        content: "";
        height: 2px;
        left: 0;
        position: absolute;
        width: 56px;
        border-radius: 1px;
    }

    .section-heading strong {
        color: var(--pm-ink);
        font-size: 15px;
        font-weight: 650;
    }
    .section-heading span {
        color: var(--pm-muted);
        font-size: 12px;
    }

    /* ══════════════════════════════════════
       侧边栏
    ══════════════════════════════════════ */
    [data-testid="stSidebar"] {
        background: var(--pm-sidebar-bg) !important;
        border-right: 1px solid var(--pm-line-light);
        min-width: var(--pm-sidebar-width) !important;
        width: var(--pm-sidebar-width) !important;
        transition: var(--pm-transition);
    }

    [data-testid="stSidebarContent"] {
        padding: 24px 18px 20px;
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0;
    }

    /* ── 品牌 Logo 区 ── */
    .sidebar-brand {
        align-items: center;
        display: flex;
        gap: 11px;
        margin: 2px 2px 26px;
        padding: 4px 0;
    }

    .brand-mark {
        background: linear-gradient(135deg, var(--pm-accent), #7c5cfc);
        display: block;
        height: 15px;
        transform: rotate(45deg);
        width: 15px;
        border-radius: 2px;
        box-shadow: 0 2px 8px rgba(74,108,247,0.25);
    }

    .brand-name {
        color: var(--pm-ink);
        font-size: 17px;
        font-weight: 700;
        letter-spacing: 0;
        line-height: 1;
    }

    .sidebar-section-label {
        margin: 0 10px 10px;
        padding-bottom: 6px;
    }

    /* ── 侧边栏按钮 ── */
    [data-testid="stSidebar"] .stButton { margin-bottom: 3px; }

    [data-testid="stSidebar"] .stButton button {
        border: 1px solid transparent;
        border-radius: var(--pm-radius-sm) !important;
        box-shadow: none !important;
        justify-content: flex-start;
        min-height: 42px;
        padding: 0 13px;
        transition: var(--pm-transition);
        width: 100%;
        font-size: 14px !important;
    }

    [data-testid="stSidebar"] .stButton button p {
        text-align: left;
        width: 100%;
        font-size: 14px !important;
        font-weight: 500 !important;
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"] {
        background: transparent;
        color: var(--pm-ink-secondary);
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
        background: var(--pm-sidebar-hover);
        border-color: var(--pm-line);
        color: var(--pm-ink);
    }

    [data-testid="stSidebar"] .stButton button[kind="primary"] {
        background: var(--pm-accent-soft);
        border-color: transparent;
        box-shadow: inset 3px 0 0 var(--pm-accent) !important;
        color: var(--pm-accent);
        font-weight: 600;
    }

    /* ── 会话面板 ── */
    .st-key-conversation_panel { margin-top: 6px; }
    .st-key-conversation_panel .sidebar-section-label { margin-left: 6px; }

    .st-key-conversation_list {
        margin: 4px 0 8px;
        overflow-x: hidden;
    }
    .st-key-conversation_list .stButton { margin-bottom: 2px !important; }
    .st-key-conversation_list .stButton button {
        min-height: 38px !important;
        padding: 0 11px !important;
    }
    .st-key-conversation_list .stButton button p {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .st-key-new_conversation button {
        justify-content: center !important;
        min-height: 40px !important;
        border-radius: var(--pm-radius) !important;
    }

    /* ── 底部导航模式切换 ── */
    .st-key-sidebar_modes {
        border-top: 1px solid var(--pm-line-light);
        margin-top: 18px;
        padding-top: 14px;
    }
    .st-key-sidebar_modes [data-testid="stHorizontalBlock"] { gap: 10px; }
    .st-key-sidebar_modes .stButton button {
        justify-content: center !important;
        min-height: 40px !important;
        border-radius: var(--pm-radius) !important;
    }

    /* ── 主界面固定导航：侧栏隐藏时仍可切换 ── */
    .st-key-global_modes {
        position: fixed;
        right: 18px;
        top: 52px;
        z-index: 2147483000;
        width: 172px;
        padding: 5px;
        background: rgba(255,255,255,0.98);
        border: 1px solid var(--pm-line-light);
        border-radius: var(--pm-radius);
        box-shadow: var(--pm-shadow-md);
        backdrop-filter: blur(14px);
        isolation: isolate;
        pointer-events: auto;
    }

    .st-key-global_modes [data-testid="stHorizontalBlock"] { gap: 4px; }

    .st-key-global_modes .stButton button {
        border-radius: var(--pm-radius-sm) !important;
        min-height: 34px !important;
        padding: 0 8px !important;
        justify-content: center !important;
        box-shadow: none !important;
        white-space: nowrap !important;
    }

    .st-key-global_modes .stButton button p {
        font-size: 13px !important;
        font-weight: 600 !important;
    }

    @media (max-width: 760px) {
        .st-key-global_modes {
            right: 12px;
            top: 48px;
            width: 152px;
        }
    }

    /* ── 侧边栏底部统计 ── */
    .sidebar-ledger {
        border-top: 1px solid var(--pm-line-light);
        margin-top: 28px;
        padding: 20px 6px 0;
    }

    .ledger-row {
        align-items: center;
        display: flex;
        justify-content: space-between;
        min-height: 30px;
    }

    .ledger-row span {
        color: var(--pm-muted);
        font-size: 12.5px;
    }

    .ledger-row strong {
        color: var(--pm-ink);
        font-family: "SF Mono", Bahnschrift, "Consolas", monospace;
        font-size: 13.5px;
        font-weight: 650;
        letter-spacing: 0;
    }

    .service-state {
        align-items: center;
        color: var(--pm-muted);
        display: flex;
        font-size: 12px;
        gap: 7px;
        margin: 22px 6px 0;
    }

    .state-dot {
        background: var(--pm-success);
        border-radius: 50%;
        height: 7px;
        width: 7px;
        animation: pulse-dot 2s ease-in-out infinite;
    }

    .state-dot.offline { background: var(--pm-danger); animation: none; }

    @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50%      { opacity: 0.55; transform: scale(0.85); }
    }

    /* ══════════════════════════════════════
       主区域控件 — 统一按钮 / 输入 / 表单
    ══════════════════════════════════════ */
    [data-testid="stMain"] .stButton button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        border-radius: var(--pm-radius-sm) !important;
        box-shadow: var(--pm-shadow-sm) !important;
        font-size: 14px;
        font-weight: 600;
        min-height: 44px;
        padding: 0 20px;
        transition: var(--pm-transition);
    }

    [data-testid="stMain"] button[kind="primary"] {
        background: var(--pm-accent) !important;
        border-color: var(--pm-accent) !important;
        color: #fff !important;
    }

    [data-testid="stMain"] button[kind="primary"]:hover {
        background: var(--pm-accent-hover) !important;
        border-color: var(--pm-accent-hover) !important;
        transform: translateY(-1px);
        box-shadow: var(--pm-shadow-md) !important;
    }

    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    [role="tab"]:focus-visible {
        outline: 3px solid rgba(74, 108, 247, 0.20) !important;
        outline-offset: 2px !important;
    }

    .stTextInput input,
    .stTextArea textarea,
    [data-baseweb="select"] > div {
        background: var(--pm-paper) !important;
        border: 1.5px solid var(--pm-line) !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: none !important;
        min-height: 46px;
        transition: var(--pm-transition) !important;
    }

    .stTextInput input:focus,
    .stTextArea textarea:focus {
        border-color: var(--pm-accent) !important;
        box-shadow: 0 0 0 4px var(--pm-accent-glow) !important;
    }

    [data-testid="stFileUploaderDropzone"] {
        background: var(--pm-surface);
        border: 1.5px dashed var(--pm-line);
        border-radius: var(--pm-radius-lg);
        min-height: 140px;
        padding: 24px;
        transition: var(--pm-transition);
    }

    [data-testid="stFileUploaderDropzone"]:hover {
        border-color: var(--pm-accent);
        background: var(--pm-accent-soft);
    }

    [data-testid="stFileUploaderDropzone"] button { min-height: 40px; }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--pm-line-light);
        border-radius: var(--pm-radius);
        overflow: hidden;
    }

    [data-testid="stAlert"] {
        border-radius: var(--pm-radius) !important;
        box-shadow: none !important;
        border: 1px solid var(--pm-line-light) !important;
    }

    [data-baseweb="tab-list"] {
        border-bottom: 1.5px solid var(--pm-line-light);
        gap: 4px;
    }

    [data-baseweb="tab"] {
        font-size: 14px;
        font-weight: 500;
        min-height: 44px;
        padding-left: 4px;
        padding-right: 4px;
        border-radius: var(--pm-radius-sm) var(--pm-radius-sm) 0 0;
    }

    [data-baseweb="tab-highlight"] {
        background-color: var(--pm-accent) !important;
        border-radius: var(--pm-radius-sm) var(--pm-radius-sm) 0 0 !important;
    }

    /* ══════════════════════════════════════
       数据指标条
    ══════════════════════════════════════ */
    .metric-rail {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line-light);
        border-radius: var(--pm-radius);
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 20px 0 0;
        overflow: hidden;
        box-shadow: var(--pm-shadow-card);
    }

    .metric-item {
        min-width: 0;
        padding: 20px 22px;
    }

    .metric-item.tone-blue   { background: var(--pm-accent-soft); border-left: 3px solid var(--pm-accent); }
    .metric-item.tone-amber { background: var(--pm-warning-soft); border-left: 3px solid var(--pm-warning); }
    .metric-item.tone-teal  { background: var(--pm-success-soft); border-left: 3px solid var(--pm-success); }

    .metric-item.tone-blue   .metric-label { color: var(--pm-accent); }
    .metric-item.tone-amber .metric-label { color: var(--pm-warning); }
    .metric-item.tone-teal  .metric-label { color: var(--pm-success); }

    .metric-item:not(:last-child) { border-right: 1px solid var(--pm-line-light); }

    .metric-label {
        color: var(--pm-muted);
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 0;
    }

    .metric-value {
        color: var(--pm-ink);
        font-family: "SF Mono", Bahnschrift, "Segoe UI", sans-serif;
        font-size: 26px;
        font-weight: 700;
        line-height: 1.2;
        letter-spacing: 0;
        overflow-wrap: anywhere;
    }

    .metric-note {
        color: var(--pm-muted);
        font-size: 12px;
        margin-top: 4px;
    }
    .metric-note.positive { color: var(--pm-success); }

    /* ══════════════════════════════════════
       定义表格
    ══════════════════════════════════════ */
    .definition-grid {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line-light);
        border-radius: var(--pm-radius);
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin-top: 14px;
        box-shadow: var(--pm-shadow-card);
    }

    .definition-item {
        border-bottom: 1px solid var(--pm-line-light);
        display: grid;
        gap: 8px;
        grid-template-columns: 96px 1fr;
        min-height: 50px;
        padding: 14px 18px;
    }
    .definition-item:nth-child(odd) { border-right: 1px solid var(--pm-line-light); }

    .definition-item dt { color: var(--pm-muted); font-size: 12px; font-weight: 500; }
    .definition-item dd { color: var(--pm-ink); font-size: 14px; font-weight: 600; margin: 0; overflow-wrap: anywhere; }

    /* ══════════════════════════════════════
       步骤条
    ══════════════════════════════════════ */
    .step-rail {
        border: 1px solid var(--pm-line-light);
        border-radius: var(--pm-radius);
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        margin: 18px 0 22px;
        overflow: hidden;
    }

    .step {
        color: var(--pm-muted);
        font-size: 13px;
        padding: 14px 18px;
    }
    .step.current { background: var(--pm-accent-soft); color: var(--pm-accent); font-weight: 600; }
    .step.pending { background: var(--pm-warning-soft); color: var(--pm-warning); }
    .step + .step { border-left: 1px solid var(--pm-line-light); }
    .step strong { color: var(--pm-ink); font-family: "SF Mono", Bahnschrift, sans-serif; margin-right: 8px; }

    /* ══════════════════════════════════════
       对话系统 — 核心聊天体验
    ══════════════════════════════════════ */

    /* 空状态标记 */
    .chat-empty-marker { height: 0; overflow: hidden; }

    /* 空状态大标题增强 */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) h1 {
        font-size: 36px !important;
        text-align: center;
        letter-spacing: 0 !important;
        color: var(--pm-ink) !important;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-meta,
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-kicker {
        display: none;
    }

    /* ── 快捷建议芯片（Suggestion Chips）── */
    .welcome-suggestions {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 10px;
        max-width: 680px;
        margin: 36px auto 0;
    }

    /* ── 欢迎区内的按钮自动获得芯片外观（Streamlit button → chip）── */
    .welcome-suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button {
        background: var(--pm-paper) !important;
        border: 1.5px solid var(--pm-line) !important;
        border-radius: 999px !important;
        color: var(--pm-ink-secondary) !important;
        font-size: 13.5px !important;
        font-weight: 500 !important;
        min-height: auto !important;
        padding: 10px 16px !important;
        box-shadow: var(--pm-shadow-sm) !important;
        text-align: center !important;
        transition: var(--pm-transition) !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }

    .welcome-suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button:hover {
        border-color: var(--pm-accent) !important;
        background: var(--pm-accent-soft) !important;
        color: var(--pm-accent) !important;
        transform: translateY(-1px);
        box-shadow: var(--pm-shadow-md) !important;
    }

    .welcome-suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button p {
        font-size: 13.5px !important;
        font-weight: 500 !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
    }

    /* ── 消息气泡 ── */
    [data-testid="stChatMessage"] {
        background: transparent;
        border-bottom: 0;
        border-radius: 0;
        gap: 14px;
        padding: 10px 0;
        width: 100%;
    }

    .st-key-chat_thread {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-thread-width);
        width: 100%;
    }

    .chat-role-marker,
    [data-testid="stElementContainer"]:has(.chat-role-marker) {
        display: none;
    }

    /* 用户消息 — 右对齐气泡 */
    [data-testid="stChatMessage"]:has(.chat-role-user) {
        background: transparent;
        border-radius: 0;
        justify-content: flex-end;
        margin: 0;
        max-width: none;
        padding: 10px 0;
        width: 100%;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageAvatarCustom"] { display: none; }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] {
        background: var(--pm-accent);
        border-radius: var(--pm-radius-lg) var(--pm-radius-lg) 4px var(--pm-radius-lg);
        color: #fff;
        flex: 0 1 auto;
        margin: 0;
        max-width: 72%;
        min-height: 36px;
        min-width: 0;
        overflow-wrap: anywhere;
        padding: 10px 16px;
        width: fit-content;
        box-shadow: var(--pm-shadow-sm);
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] p,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] li {
        color: #fff !important;
        font-size: 14px !important;
        line-height: 1.55 !important;
    }

    [data-testid="stChatMessageContent"]
    [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] { min-width: 0; }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :first-child { margin-top: 0 !important; }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :last-child { margin-bottom: 0 !important; }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ul,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ol {
        margin-block: 5px;
        padding-inline-start: 20px;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] pre {
        max-width: 100%; overflow-x: auto;
        background: rgba(255,255,255,0.12) !important;
        border-radius: var(--pm-radius-sm);
    }

    /* 助手消息 */
    [data-testid="stChatMessage"]:has(.chat-role-assistant) {
        margin-bottom: 10px;
    }

    /* 头像 */
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarAssistant"] {
        border: 1.5px solid var(--pm-line);
        border-radius: 50%;
        height: 34px;
        width: 34px;
        box-shadow: var(--pm-shadow-sm);
    }

    [data-testid="stChatMessageContent"] p:last-child { margin-bottom: 0 !important; }

    /* ── 附件显示 ── */
    .chat-attachments {
        color: var(--pm-muted);
        display: flex;
        flex-wrap: wrap;
        font-size: 12px;
        gap: 5px 10px;
        margin-top: 8px;
    }

    .chat-attachment-name {
        background: var(--pm-surface);
        border: 1px solid var(--pm-line);
        border-radius: var(--pm-radius-sm);
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 3px 9px;
    }

    /* ══════════════════════════════════════
       输入框
    ══════════════════════════════════════ */
    [data-testid="stChatInput"] {
        background: var(--pm-paper);
        border: 1.5px solid var(--pm-line);
        border-radius: var(--pm-radius-xl);
        box-shadow: var(--pm-shadow-input);
        transition: var(--pm-transition);
        overflow: hidden;
    }

    [data-testid="stChatInput"]:focus-within {
        border-color: var(--pm-accent);
        box-shadow: var(--pm-shadow-input), 0 0 0 4px var(--pm-accent-glow);
    }

    [data-testid="stChatInput"] textarea {
        background: transparent !important;
        font-size: 14.5px;
        line-height: 1.6;
        min-height: 48px;
        padding: 4px 0 !important;
    }

    [data-testid="stChatInput"] textarea:focus {
        box-shadow: none !important;
        outline: none !important;
    }

    [data-testid="stChatInput"] textarea::placeholder {
        color: var(--pm-muted) !important;
    }

    [data-testid="stChatInput"] button {
        color: var(--pm-accent);
        min-height: 46px;
        min-width: 46px;
        transition: var(--pm-transition);
        border-radius: var(--pm-radius) !important;
    }

    [data-testid="stChatInput"] button:hover {
        background: var(--pm-accent-soft) !important;
    }

    /* ── 清空按钮 ── */
    .st-key-clear_chat {
        align-items: flex-end;
        display: flex;
    }
    .st-key-clear_chat button {
        min-width: 44px !important;
        margin-left: auto;
        padding: 0 !important;
        width: 44px !important;
        border-radius: var(--pm-radius-sm) !important;
    }
    .st-key-clear_chat button p {
        border: 0; clip: rect(0 0 0 0); clip-path: inset(50%);
        height: 1px; margin: -1px; overflow: hidden;
        padding: 0; position: absolute; white-space: nowrap; width: 1px;
    }

    /* ── 对话头部 ── */
    .st-key-chat_header [data-testid="stHorizontalBlock"] {
        align-items: flex-end;
        flex-wrap: nowrap;
    }
    .st-key-chat_header [data-testid="stColumn"]:first-child {
        flex: 1 1 auto; min-width: 0;
    }
    .st-key-chat_header [data-testid="stColumn"]:last-child {
        flex: 0 0 44px; min-width: 44px; width: 44px;
    }

    /* ── 底部输入容器 ── */
    [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-shell-width);
    }

    [data-testid="stBottom"] { background: transparent !important; }

    /* 空状态下输入框更大 */
    [data-testid="stAppScrollToBottomContainer"]:has(.chat-empty-marker)
    [data-testid="stChatInput"] textarea {
        min-height: 64px;
        padding-top: 16px;
    }

    /* ── 加载动画 ── */
    .stSpinner > div {
        border-top-color: var(--pm-accent) !important;
    }

    /* ══════════════════════════════════════
       容器卡片通用样式（用于审批等）
    ══════════════════════════════════════ */
    [data-testid="stVerticalBlock"] > [data-testid="stContainer"],
    [data-testid="column"] > [data-testid="stContainer"] {
        border: 1px solid var(--pm-line-light) !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: var(--pm-shadow-card) !important;
        background: var(--pm-paper) !important;
        padding: 18px 20px !important;
        margin-bottom: 4px !important;
    }

    /* ══════════════════════════════════════
       体验增强层 — 滚动条 / 代码块 / 微交互
    ══════════════════════════════════════ */

    /* ── 自定义滚动条 ── */
    * {
        scrollbar-width: thin;
        scrollbar-color: var(--pm-line) transparent;
    }
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-thumb {
        background: var(--pm-line);
        border-radius: 999px;
        border: 2px solid transparent;
        background-clip: content-box;
    }
    ::-webkit-scrollbar-thumb:hover { background: var(--pm-muted); }
    ::-webkit-scrollbar-track { background: transparent; }

    /* ── 文本选区 ── */
    ::selection { background: var(--pm-accent-glow); color: var(--pm-ink); }

    /* ── 助手消息代码块（Kimi/豆包风格）── */
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
        [data-testid="stMarkdownContainer"] pre {
        background: var(--pm-surface) !important;
        border: 1px solid var(--pm-line);
        border-radius: var(--pm-radius) !important;
        padding: 14px 16px !important;
        margin: 10px 0 !important;
        overflow-x: auto;
        font-size: 13px;
        line-height: 1.6;
    }
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
        [data-testid="stMarkdownContainer"] code {
        font-family: "SF Mono", "JetBrains Mono", Bahnschrift, Consolas, monospace;
        font-size: 0.9em;
    }
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
        [data-testid="stMarkdownContainer"] :not(pre) > code {
        background: var(--pm-surface) !important;
        border: 1px solid var(--pm-line-light);
        border-radius: 6px;
        padding: 1px 6px;
        color: var(--pm-accent);
    }

    /* ── 消息内容微交互 ── */
    [data-testid="stChatMessageContent"] { transition: box-shadow 200ms ease; }

    /* ── 图片在消息中自适应 ── */
    [data-testid="stChatMessageContent"] img {
        max-width: 100%;
        border-radius: var(--pm-radius);
    }

    /* ── 容器卡片悬浮微抬升 ── */
    [data-testid="stVerticalBlock"] > [data-testid="stContainer"]:hover,
    [data-testid="column"] > [data-testid="stContainer"]:hover {
        box-shadow: var(--pm-shadow-md) !important;
        transform: translateY(-1px);
    }

    /* ── 加载/思考指示器 ── */
    .stSpinner {
        display: inline-flex;
        align-items: center;
        gap: 9px;
        color: var(--pm-muted);
        font-size: 13px;
    }

    /* ── 提示框左侧强调线 ── */
    [data-testid="stAlert"] {
        border-left: 3px solid var(--pm-line) !important;
    }
    [data-testid="stAlert"][kind="error"] { border-left-color: var(--pm-danger) !important; }
    [data-testid="stAlert"][kind="success"] { border-left-color: var(--pm-success) !important; }
    [data-testid="stAlert"][kind="warning"] { border-left-color: var(--pm-warning) !important; }
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] { font-size: 14px; }

    /* ── 禁用态更克制 ── */
    button:disabled,
    [data-testid="stChatInput"] button:disabled {
        opacity: 0.45 !important;
        cursor: not-allowed !important;
        box-shadow: none !important;
    }

    /* ── 对话线程进场淡入（每次导航一次，避免逐条重播）── */
    @keyframes pm-fade-in {
        from { opacity: 0; }
        to   { opacity: 1; }
    }
    .st-key-chat_thread { animation: pm-fade-in 260ms ease both; }

    /* ── 欢迎页底部柔光 ── */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker)::before {
        background: radial-gradient(
            60% 50% at 50% 0%,
            var(--pm-accent-glow),
            transparent 70%
        );
        content: "";
        height: 240px;
        left: 0;
        pointer-events: none;
        position: absolute;
        top: 0;
        width: 100%;
        z-index: 0;
    }

    /* ══════════════════════════════════════
       响应式适配
    ══════════════════════════════════════ */
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-duration: 0.01ms !important;
            animation: none !important;
        }
    }

    @media (max-width: 760px) {
        [data-testid="stSidebar"] {
            min-width: min(88vw, 300px) !important;
            width: min(88vw, 300px) !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding: 20px 18px 36px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
            padding-left: 16px;
            padding-right: 16px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
            padding-top: 120px;
        }

        [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
            max-width: calc(100vw - 32px);
        }

        .st-key-chat_thread { max-width: 100%; }

        [data-testid="stChatMessage"]:has(.chat-role-user)
        [data-testid="stChatMessageContent"] { max-width: 88%; }

        h1 { font-size: 26px !important; }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) h1 {
            font-size: 28px !important;
        }

        .welcome-suggestions {
            gap: 8px;
            margin-top: 24px;
        }

        .metric-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .metric-item:nth-child(2) { border-right: 0; }
        .metric-item:nth-child(-n+2) { border-bottom: 1px solid var(--pm-line-light); }
        .metric-item { padding: 14px 14px; }
        .metric-value { font-size: 22px; }

        .definition-grid { grid-template-columns: 1fr; }
        .definition-item:nth-child(odd) { border-right: 0; }

        .section-heading { align-items: flex-start; gap: 8px; }
    }
</style>
"""
