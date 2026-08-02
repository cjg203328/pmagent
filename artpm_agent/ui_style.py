STYLE_CSS = """
<style>
    /* ArtPM Agent UI design system */
    :root {
        /* ── 色板 ── */
        --pm-paper:        #ffffff;
        --pm-canvas:       #fbfbfa;
        --pm-surface:      #f3f3f1;
        --pm-sidebar-bg:   #f5f5f3;
        --pm-sidebar-hover:#ebebe8;
        --pm-sidebar-active:#e7e7e4;
        --pm-ink:          #202124;
        --pm-ink-secondary:#41454b;
        --pm-muted:        #646a73;
        --pm-line:         #dededb;
        --pm-line-light:   #ecece9;

        /* Codex-like focus color: neutral reading surfaces, blue actions. */
        --pm-accent:       #2563eb;
        --pm-accent-hover: #1d4ed8;
        --pm-accent-soft:  #eff6ff;
        --pm-accent-glow:  rgba(37, 99, 235, 0.12);
        --pm-accent-warm:  #f7f7f5;
        --pm-accent-pale:  #dbeafe;
        --pm-focus:        #1d4ed8;

        /* Chat surfaces stay quiet; the accent is reserved for actions. */
        --pm-chat-user-bg:     #f0f0ee;
        --pm-chat-user-fg:     #202124;
        --pm-chat-assistant-bg:transparent;
        --pm-chat-assistant-fg:#41454b;
        --pm-chat-border:      #e3e3df;
        --pm-chat-code-bg:     #f3f3f1;

        /* 语义色 */
        --pm-success:      #22c55e;
        --pm-success-soft: #ecfdf5;
        --pm-warning:      #f59e0b;
        --pm-warning-soft: #fffbeb;
        --pm-danger:       #ef4444;
        --pm-danger-soft:  #fef2f2;

        /* 圆角 */
        --pm-radius-sm:    6px;
        --pm-radius:       8px;
        --pm-radius-lg:    10px;
        --pm-radius-xl:    12px;

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
        --pm-chat-thread-width:  820px;
        --pm-sidebar-width:      280px;
        --pm-composer-reserve:   136px;
        --pm-page-gutter:        clamp(20px, 3.5vw, 44px);

        /* 阴影系统 */
        --pm-shadow-sm:  0 1px 2px rgba(32,33,36,0.04);
        --pm-shadow-md:  0 4px 14px rgba(32,33,36,0.06);
        --pm-shadow-lg:  0 10px 28px rgba(32,33,36,0.08);
        --pm-shadow-input:0 1px 3px rgba(32,33,36,0.06);
        --pm-shadow-card: 0 1px 4px rgba(32,33,36,0.04);

        /* Explicit, interruptible properties; avoid catch-all transitions. */
        --pm-ease-out: cubic-bezier(.23, 1, .32, 1);
        --pm-ease-in-out: cubic-bezier(.77, 0, .175, 1);
        --pm-duration-press: 140ms;
        --pm-duration-ui: 200ms;
        --pm-transition: color var(--pm-duration-ui) var(--pm-ease-out),
                          background-color var(--pm-duration-ui) var(--pm-ease-out),
                          border-color var(--pm-duration-ui) var(--pm-ease-out),
                          box-shadow var(--pm-duration-ui) var(--pm-ease-out),
                          transform var(--pm-duration-ui) var(--pm-ease-out),
                          opacity var(--pm-duration-ui) var(--pm-ease-out);
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
        padding: 28px var(--pm-page-gutter) 44px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
        max-width: calc(var(--pm-chat-shell-width) + 44px);
        padding-bottom: var(--pm-composer-reserve);
        padding-left: 28px;
        padding-right: 28px;
    }

    [data-testid="stAppScrollToBottomContainer"]:has(.chat-page-marker) {
        scroll-padding-bottom: var(--pm-composer-reserve);
    }

    /* ── 欢迎/空状态居中 ── */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
        padding-top: clamp(112px, 15vh, 176px);
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
        line-height: 1.6;
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
        margin: 0 0 24px;
    }

    /* ── 分区标题（带底部装饰线）── */
    .section-heading {
        align-items: baseline;
        border-bottom: 1px solid var(--pm-line-light);
        display: flex;
        justify-content: space-between;
        margin: 24px 0 12px;
        padding-bottom: 8px;
        position: relative;
    }

    .section-heading::after {
        background: var(--pm-accent);
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
        background: var(--pm-ink);
        display: block;
        height: 15px;
        transform: rotate(45deg);
        width: 15px;
        border-radius: 2px;
        box-shadow: none;
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
        background: var(--pm-sidebar-active);
        border-color: transparent;
        box-shadow: inset 2px 0 0 var(--pm-accent) !important;
        color: var(--pm-ink);
        font-weight: 600;
    }

    [data-testid="stSidebar"] .stButton button[kind="primary"]:disabled {
        cursor: default !important;
        opacity: 1 !important;
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
    .st-key-conversation_list [data-testid="stHorizontalBlock"] {
        align-items: center;
        gap: 4px !important;
    }
    .st-key-conversation_list [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child .stButton button {
        min-width: 32px !important;
        padding: 0 !important;
        color: var(--pm-muted) !important;
        background: transparent !important;
        border-color: transparent !important;
        box-shadow: none !important;
    }
    .st-key-conversation_list [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child .stButton button:hover:not(:disabled) {
        color: var(--pm-danger) !important;
        background: var(--pm-danger-soft) !important;
        border-color: var(--pm-danger-soft) !important;
    }
    .st-key-new_conversation button {
        justify-content: center !important;
        min-height: 40px !important;
        border-radius: var(--pm-radius) !important;
    }

    /* ── 侧边栏导航 pills ── */
    .st-key-sidebar_nav_pills {
        border-top: 1px solid var(--pm-line-light);
        margin-top: 16px;
        padding-top: 12px;
    }
    .st-key-sidebar_nav_pills [data-testid="stPillsContainer"] {
        gap: 4px !important;
    }
    .st-key-sidebar_nav_pills [data-testid="stPill"] {
        flex: 1 1 0 !important;
        min-width: 0 !important;
        justify-content: center !important;
        font-size: 13px !important;
    }

    .st-key-sidebar_nav_pills button[role="radio"] {
        background: transparent !important;
        border: 1px solid var(--pm-line) !important;
        color: var(--pm-ink-secondary) !important;
        box-shadow: none !important;
    }

    .st-key-sidebar_nav_pills button[role="radio"][aria-checked="true"] {
        background: var(--pm-accent-soft) !important;
        border-color: var(--pm-accent) !important;
        color: var(--pm-accent) !important;
    }

    .st-key-sidebar_nav_pills button[role="radio"]:hover {
        background: var(--pm-sidebar-hover) !important;
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
    }

    .state-dot.offline { background: var(--pm-danger); }

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
        outline: 2px solid var(--pm-focus) !important;
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
        margin: 16px 0 0;
        overflow: hidden;
        box-shadow: var(--pm-shadow-card);
    }

    .metric-item {
        min-width: 0;
        padding: 16px 18px;
    }

    .metric-item.tone-blue,
    .metric-item.tone-amber,
    .metric-item.tone-teal { background: var(--pm-paper); }

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
    .st-key-welcome_suggestions {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 8px;
        max-width: 680px;
        margin: 26px auto 0;
    }

    /* ── 欢迎区内的按钮自动获得芯片外观（Streamlit button → chip）── */
    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
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

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button:hover {
        border-color: var(--pm-accent) !important;
        background: var(--pm-accent-soft) !important;
        color: var(--pm-accent) !important;
        transform: translateY(-1px);
        box-shadow: var(--pm-shadow-md) !important;
    }

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
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
        gap: 12px;
        padding: 8px 0;
        width: 100%;
    }

    .st-key-chat_thread {
        gap: 12px !important;
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-thread-width);
        width: 100%;
    }

    .chat-role-marker,
    [data-testid="stElementContainer"]:has(.chat-role-marker) {
        display: none;
    }

    /* User message: Codex-like neutral bubble, aligned to the right. */
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
        background: var(--pm-chat-user-bg);
        border: 1px solid var(--pm-chat-border);
        border-radius: 16px 16px 6px 16px;
        color: var(--pm-chat-user-fg);
        flex: 0 1 auto;
        margin: 0;
        max-width: 78%;
        min-height: 36px;
        min-width: 0;
        overflow-wrap: anywhere;
        padding: 8px 14px;
        width: fit-content;
        box-shadow: none;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] p,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] li {
        color: var(--pm-chat-user-fg) !important;
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
        background: var(--pm-paper) !important;
        border: 1px solid var(--pm-chat-border);
        border-radius: var(--pm-radius-sm);
    }

    /* Assistant message: an unframed reading surface, like Codex. */
    [data-testid="stChatMessage"]:has(.chat-role-assistant) {
        margin-bottom: 8px;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] {
        background: var(--pm-chat-assistant-bg);
        border: 0;
        border-radius: 0;
        box-shadow: none;
        flex: 1 1 auto;
        margin: 0;
        max-width: calc(100% - 48px);
        min-height: 36px;
        overflow-wrap: anywhere;
        padding: 3px 0 6px;
        width: auto;
        transition: color var(--pm-duration-ui) var(--pm-ease-out);
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"]:hover {
        box-shadow: none;
    }

    /* 助手消息内文字：更舒适的行高与间距 */
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] p,
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] li {
        font-size: 14.5px !important;
        line-height: 1.65 !important;
        color: var(--pm-chat-assistant-fg) !important;
        margin-bottom: 4px;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] p:last-child,
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] li:last-child {
        margin-bottom: 0;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] h3,
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] strong {
        color: var(--pm-ink) !important;
        font-weight: 650;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] ul,
    [data-testid="stChatMessage"]:has(.chat-role-assistant)
    [data-testid="stChatMessageContent"] ol {
        margin-block: 8px 4px;
        padding-inline-start: 20px;
    }

    /* 头像 */
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarAssistant"] {
        border: 1.5px solid var(--pm-line);
        border-radius: 50%;
        height: 32px;
        width: 32px;
        box-shadow: none;
    }

    [data-testid="stChatMessageContent"] p:last-child { margin-bottom: 0 !important; }

    /* Feedback is one compact action group. Streamlit normally stacks every
       column on narrow screens, which separated these two icon buttons. */
    [data-testid="stHorizontalBlock"]:has([class*="st-key-fb_up_"]):has([class*="st-key-fb_down_"]) {
        align-items: center;
        flex-wrap: nowrap !important;
        gap: 4px !important;
        margin-top: 2px;
    }
    [data-testid="stHorizontalBlock"]:has([class*="st-key-fb_up_"])
        > [data-testid="stColumn"]:has([class*="st-key-fb_up_"]),
    [data-testid="stHorizontalBlock"]:has([class*="st-key-fb_down_"])
        > [data-testid="stColumn"]:has([class*="st-key-fb_down_"]) {
        flex: 0 0 40px !important;
        min-width: 40px !important;
        width: 40px !important;
    }
    [data-testid="stHorizontalBlock"]:has([class*="st-key-fb_up_"]):has([class*="st-key-fb_down_"])
        > [data-testid="stColumn"]:last-child {
        flex: 1 1 auto !important;
        min-width: 0 !important;
        width: auto !important;
    }

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
    /* Persistent permission gate: compact, explicit and decision focused. */
    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-permission-anchor) {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line) !important;
        border-left: 3px solid var(--pm-warning) !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: var(--pm-shadow-card);
        margin: 12px 0;
        max-width: 100%;
        scroll-margin-bottom: var(--pm-composer-reserve);
    }

    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-permission-anchor)
    > [data-testid="stVerticalBlock"] {
        gap: 10px;
        padding: 16px 18px;
    }

    .pm-permission-anchor,
    [data-testid="stElementContainer"]:has(.pm-permission-anchor) {
        display: none;
    }

    .pm-permission-heading {
        align-items: flex-start;
        display: flex;
        gap: 10px;
        justify-content: space-between;
    }

    .pm-permission-title {
        min-width: 0;
    }

    .pm-permission-title h3 {
        color: var(--pm-ink);
        font-size: 15px;
        font-weight: 700;
        line-height: 1.35;
        margin: 0 !important;
    }

    .pm-permission-title p {
        color: var(--pm-muted);
        font-size: 12px;
        line-height: 1.45;
        margin: 2px 0 0;
        overflow-wrap: anywhere;
    }

    .pm-risk {
        border: 1px solid var(--pm-line);
        border-radius: 999px;
        color: var(--pm-muted);
        flex: 0 0 auto;
        font-size: 11px;
        font-weight: 650;
        line-height: 22px;
        padding: 0 8px;
    }

    .pm-risk-medium,
    .pm-risk-high {
        background: var(--pm-warning-soft);
        border-color: #f3d28f;
        color: #8a5600;
    }

    .pm-risk-critical,
    .pm-risk-untrusted {
        background: var(--pm-danger-soft);
        border-color: #efb5b5;
        color: #a32929;
    }

    .pm-permission-action {
        color: var(--pm-ink);
        font-size: 14.5px;
        font-weight: 700;
        line-height: 1.45;
        margin-top: 2px;
        overflow-wrap: anywhere;
    }

    .pm-permission-facts {
        border-bottom: 1px solid var(--pm-line-light);
        display: grid;
        gap: 8px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        padding-bottom: 8px;
    }

    .pm-permission-fact {
        background: transparent;
        border: 0;
        border-radius: 0;
        min-width: 0;
        padding: 4px 0;
    }

    .pm-permission-fact + .pm-permission-fact {
        border-left: 1px solid var(--pm-line-light);
        padding-left: 12px;
    }

    .pm-permission-fact span,
    .pm-permission-params > span {
        color: var(--pm-muted);
        display: block;
        font-size: 11px;
        font-weight: 650;
        line-height: 1.35;
        margin-bottom: 3px;
    }

    .pm-permission-fact strong {
        color: var(--pm-ink-secondary);
        display: block;
        font-size: 12px;
        font-weight: 550;
        line-height: 1.45;
        overflow-wrap: anywhere;
    }

    .pm-permission-params {
        border-top: 1px solid var(--pm-line-light);
        padding-top: 9px;
    }

    .pm-permission-params code {
        background: transparent;
        color: var(--pm-ink-secondary);
        display: block;
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        font-size: 11.5px;
        line-height: 1.5;
        max-width: 100%;
        overflow-wrap: anywhere;
        padding: 0;
        white-space: normal;
    }

    [class*="st-key-permission_actions_"] [data-testid="stHorizontalBlock"] {
        gap: 10px;
    }

    [class*="st-key-permission_actions_"] button {
        min-height: 44px !important;
        width: 100%;
    }

    [data-testid="stChatInput"]:has(textarea:disabled) {
        background: var(--pm-paper);
        border-color: var(--pm-line);
        box-shadow: none;
    }

    [data-testid="stChatInput"] textarea:disabled {
        color: var(--pm-muted) !important;
        opacity: 1 !important;
        -webkit-text-fill-color: var(--pm-muted);
    }

    /* Visual workflow editor: a readable graph preview backed by the
       structured data editor, with stable node dimensions for narrow screens. */
    [data-testid="stVerticalBlockBorderWrapper"]:has(.workflow-designer-marker) {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line) !important;
        border-radius: var(--pm-radius) !important;
        margin: 8px 0 16px;
        overflow: hidden;
    }

    .workflow-designer-marker,
    [data-testid="stElementContainer"]:has(.workflow-designer-marker) {
        display: none;
    }

    .pm-workflow-canvas {
        align-items: stretch;
        display: flex;
        gap: 8px;
        margin: 8px 0 16px;
        max-width: 100%;
        overflow-x: auto;
        padding: 4px 2px 10px;
    }

    .pm-workflow-node {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line);
        border-radius: var(--pm-radius);
        display: flex;
        flex: 0 0 156px;
        flex-direction: column;
        gap: 4px;
        min-height: 92px;
        padding: 10px;
    }

    .pm-workflow-node strong,
    .pm-workflow-node span,
    .pm-workflow-node small {
        overflow-wrap: anywhere;
    }

    .pm-workflow-node strong {
        color: var(--pm-ink);
        font-size: 13px;
    }

    .pm-workflow-node span:not(.pm-workflow-node-index) {
        color: var(--pm-ink);
        font-size: 12px;
    }

    .pm-workflow-node small {
        color: var(--pm-muted);
        font-size: 11px;
        line-height: 1.35;
    }

    .pm-workflow-node-index {
        align-items: center;
        background: var(--pm-ink);
        border-radius: 999px;
        color: var(--pm-paper);
        display: inline-flex;
        font-size: 10px;
        height: 20px;
        justify-content: center;
        width: 20px;
    }

    .pm-workflow-edge {
        align-self: center;
        color: var(--pm-muted);
        flex: 0 0 auto;
        font-size: 18px;
    }

    .pm-workflow-empty {
        border: 1px dashed var(--pm-line);
        color: var(--pm-muted);
        padding: 16px;
        width: 100%;
    }

    [data-testid="stChatInput"] {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line);
        border-radius: 16px;
        box-shadow: var(--pm-shadow-input);
        transition: var(--pm-transition);
        overflow: hidden;
    }

    [data-testid="stChatInput"]:focus-within {
        border-color: var(--pm-accent);
        box-shadow: var(--pm-shadow-input), 0 0 0 2px var(--pm-accent-glow);
    }

    [data-testid="stChatInput"] textarea {
        background: transparent !important;
        font-size: 14.5px;
        line-height: 1.6;
        min-height: 48px;
        padding: 12px 16px 4px !important;
    }

    [data-testid="stChatInput"] textarea:focus {
        box-shadow: none !important;
        outline: none !important;
    }

    [data-testid="stChatInput"] textarea::placeholder {
        color: var(--pm-muted) !important;
    }

    /* The access mode is a real Streamlit popover layered into the composer,
       immediately after the native attachment button. */
    .st-key-chat_composer_shell {
        position: relative;
        width: 100%;
    }

    .st-key-chat_composer_shell > [data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }

    .st-key-chat_voice_callback {
        bottom: calc(100% + 10px);
        left: 0;
        pointer-events: none;
        position: absolute !important;
        width: 100% !important;
        z-index: 8;
    }

    .st-key-chat_voice_callback > [data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }

    .pm-voice-callback {
        align-items: center;
        background: var(--pm-paper);
        border: 1px solid var(--pm-line);
        border-radius: 8px;
        box-shadow: var(--pm-shadow-md);
        display: flex;
        gap: 10px;
        margin-left: 8px;
        max-width: min(460px, calc(100vw - 48px));
        min-height: 44px;
        padding: 8px 12px;
        width: max-content;
    }

    .pm-voice-callback-icon {
        align-items: center;
        background: var(--pm-surface);
        border-radius: 50%;
        color: var(--pm-focus);
        display: inline-flex;
        flex: 0 0 28px;
        font-size: 17px;
        height: 28px;
        justify-content: center;
        width: 28px;
    }

    .pm-voice-callback-copy {
        display: grid;
        gap: 1px;
        min-width: 0;
    }

    .pm-voice-callback-label {
        color: var(--pm-ink-secondary);
        font-size: 13.5px;
        font-weight: 600;
        line-height: 1.35;
    }

    .pm-voice-callback-detail {
        color: var(--pm-muted);
        font-size: 12px;
        line-height: 1.35;
        overflow-wrap: anywhere;
    }

    .pm-voice-callback--success .pm-voice-callback-icon {
        background: var(--pm-success-soft);
        color: var(--pm-success);
    }

    .pm-voice-callback--warning .pm-voice-callback-icon {
        background: var(--pm-warning-soft);
        color: var(--pm-warning);
    }

    .pm-voice-callback--danger .pm-voice-callback-icon {
        background: var(--pm-danger-soft);
        color: var(--pm-danger);
    }

    .st-key-chat_composer_shell:has(
        [data-testid="stChatInputMicButton"] button[aria-label="Stop recording"]
    )::before,
    .st-key-chat_composer_shell:has(
        [data-testid="stChatInputMicButton"] button[aria-label="Pause recording"]
    )::before {
        background: var(--pm-danger-soft);
        border: 1px solid var(--pm-danger);
        border-radius: 8px;
        color: var(--pm-danger);
        content: "正在录音";
        font-size: 12.5px;
        font-weight: 600;
        padding: 6px 10px;
        position: absolute;
        right: 8px;
        top: -40px;
        z-index: 9;
    }

    [data-testid="stChatInput"]:has(
        [data-testid="stChatInputMicButton"] button[aria-label="Stop recording"]
    ),
    [data-testid="stChatInput"]:has(
        [data-testid="stChatInputMicButton"] button[aria-label="Pause recording"]
    ) {
        border-color: var(--pm-danger) !important;
        box-shadow: var(--pm-shadow-input), 0 0 0 2px var(--pm-danger-soft) !important;
    }

    .st-key-chat_access_popover {
        bottom: 10px;
        left: 96px;
        position: absolute !important;
        width: max-content !important;
        z-index: 6;
    }

    .st-key-chat_access_popover button {
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
        color: var(--pm-warning) !important;
        font-size: 13.5px !important;
        font-weight: 500 !important;
        height: 36px !important;
        min-height: 36px !important;
        padding: 0 8px !important;
        white-space: nowrap;
    }

    .st-key-chat_access_popover button:hover {
        background: var(--pm-warning-soft) !important;
        color: #b45309 !important;
    }

    .st-key-chat_access_popover button:focus-visible {
        outline: 2px solid var(--pm-focus) !important;
        outline-offset: 1px !important;
    }

    .pm-access-panel-anchor,
    [data-testid="stElementContainer"]:has(.pm-access-panel-anchor) {
        display: none !important;
    }

    [data-testid="stPopoverBody"]:has(.pm-access-panel-anchor) {
        background: var(--pm-paper) !important;
        border: 1px solid var(--pm-line) !important;
        border-radius: 14px !important;
        box-shadow: var(--pm-shadow-lg) !important;
        max-width: calc(100vw - 32px) !important;
        padding: 20px !important;
        width: min(460px, calc(100vw - 32px)) !important;
    }

    [data-testid="stPopoverBody"]:has(.pm-access-panel-anchor)
    > [data-testid="stVerticalBlock"] {
        gap: 14px !important;
    }

    .pm-access-panel-copy {
        display: grid;
        gap: 8px;
    }

    .pm-access-panel-title {
        color: var(--pm-ink);
        font-size: 16px;
        font-weight: 700;
        line-height: 1.35;
    }

    .pm-access-panel-description,
    .pm-access-panel-footnote {
        color: var(--pm-muted);
        font-size: 13px;
        line-height: 1.55;
        overflow-wrap: anywhere;
    }

    [data-testid="stPopoverBody"]:has(.pm-access-panel-anchor)
    [data-testid="stButton"] button {
        background: var(--pm-paper) !important;
        border: 1px solid var(--pm-line) !important;
        border-radius: var(--pm-radius-sm) !important;
        box-shadow: none !important;
        color: var(--pm-ink-secondary) !important;
        font-size: 14px !important;
        font-weight: 500 !important;
        min-height: 48px !important;
        width: 100% !important;
    }

    [data-testid="stPopoverBody"]:has(.pm-access-panel-anchor)
    [data-testid="stButton"] button:hover:not(:disabled) {
        background: var(--pm-surface) !important;
        border-color: var(--pm-focus) !important;
    }

    [class*="st-key-confirm_full_access_"] button {
        background: var(--pm-ink) !important;
        border-color: var(--pm-ink) !important;
        color: var(--pm-paper) !important;
        box-shadow: none !important;
    }

    [class*="st-key-confirm_full_access_"] button:hover:not(:disabled) {
        background: var(--pm-ink-secondary) !important;
        border-color: var(--pm-ink-secondary) !important;
    }

    [class*="st-key-confirm_full_access_"] button:disabled {
        background: var(--pm-surface) !important;
        border-color: var(--pm-line) !important;
        color: var(--pm-muted) !important;
    }

    .st-key-chat_composer_shell [data-testid="stChatInput"] textarea {
        padding-left: 16px !important;
        padding-right: 16px !important;
    }

    [data-testid="stChatInput"] button {
        color: var(--pm-ink-secondary);
        min-height: 36px;
        min-width: 36px;
        transition: var(--pm-transition);
        border-radius: var(--pm-radius) !important;
    }

    [data-testid="stChatInput"] button:hover {
        background: var(--pm-surface) !important;
        color: var(--pm-ink) !important;
    }

    [data-testid="stChatInputFileUploadButton"] {
        align-items: center;
        display: flex;
        flex: 0 0 36px;
        justify-content: center;
    }

    [data-testid="stChatInputFileUploadButton"] button,
    [data-testid="stChatInputFileUploadButton"] button[aria-label="Upload files"] {
        background: transparent !important;
        border: 0 !important;
        height: 36px !important;
        padding: 0 !important;
        width: 36px !important;
    }

    [data-testid="stChatInputFileUploadButton"] button:focus-visible,
    [data-testid="stChatInputFileUploadButton"] button[aria-label="Upload files"]:focus-visible {
        outline: 2px solid var(--pm-focus) !important;
        outline-offset: 1px !important;
    }

    [data-testid="stChatInputMicButton"] {
        align-items: center;
        display: flex;
        flex: 0 0 36px;
        justify-content: center;
    }

    [data-testid="stChatInputMicButton"] button {
        background: transparent !important;
        border: 0 !important;
        height: 36px !important;
        padding: 0 !important;
        width: 36px !important;
    }

    [data-testid="stChatInputMicButton"] button:focus-visible {
        outline: 2px solid var(--pm-focus) !important;
        outline-offset: 1px !important;
    }

    [data-testid="stChatInputSubmitButton"] {
        background: var(--pm-ink) !important;
        color: var(--pm-paper) !important;
        height: 36px !important;
        margin: 0 !important;
        padding: 0 !important;
        width: 36px !important;
    }

    [data-testid="stChatInputSubmitButton"]:hover:not(:disabled) {
        background: var(--pm-ink-secondary) !important;
        color: var(--pm-paper) !important;
    }

    [data-testid="stChatInputSubmitButton"]:disabled {
        background: var(--pm-surface) !important;
        color: var(--pm-muted) !important;
        opacity: 0.72;
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
    [data-testid="stBottomBlockContainer"] {
        background: var(--pm-canvas) !important;
        padding-bottom: max(14px, env(safe-area-inset-bottom));
        padding-top: 12px;
    }

    [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-shell-width);
    }

    [data-testid="stBottom"] { background: transparent !important; }

    /* 空状态下输入框更大 */
    [data-testid="stAppScrollToBottomContainer"]:has(.chat-empty-marker)
    [data-testid="stChatInput"] textarea {
        min-height: 48px;
        padding-top: 12px;
    }

    /* ── 加载动画 ── */
    .stSpinner > div {
        border-top-color: var(--pm-accent) !important;
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

    /* Reading surfaces stay still; only explicit controls transition. */
    [data-testid="stChatMessageContent"] {
        transition: color var(--pm-duration-ui) var(--pm-ease-out);
    }

    /* ══════════════════════════════════════
       Quiet ghost controls for copy and feedback actions.
    ══════════════════════════════════════ */
    [data-testid="stChatMessage"] button,
    [data-testid="stChatMessageContent"] button {
        background: transparent !important;
        border: 1px solid transparent !important;
        border-radius: var(--pm-radius-sm) !important;
        box-shadow: none !important;
        color: var(--pm-muted) !important;
        font-size: 12.5px !important;
        font-weight: 600 !important;
        min-height: 32px !important;
        min-width: 32px !important;
        padding: 4px 8px !important;
        transition: background-color var(--pm-duration-ui) var(--pm-ease-out),
                    border-color var(--pm-duration-ui) var(--pm-ease-out),
                    color var(--pm-duration-ui) var(--pm-ease-out),
                    transform var(--pm-duration-press) var(--pm-ease-out) !important;
    }

    [data-testid="stChatMessage"] button:hover,
    [data-testid="stChatMessageContent"] button:hover {
        background: var(--pm-surface) !important;
        border-color: var(--pm-line) !important;
        transform: none;
        box-shadow: none !important;
    }

    /* User-bubble controls keep the same quiet treatment. */
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] button {
        background: transparent !important;
        border-color: transparent !important;
        color: var(--pm-muted) !important;
    }
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] button:hover {
        background: var(--pm-paper) !important;
        border-color: var(--pm-line) !important;
    }

    /* ── 图片在消息中自适应 ── */
    [data-testid="stChatMessageContent"] img {
        max-width: 100%;
        border-radius: var(--pm-radius);
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

    /* Structured callback windows keep failures actionable without exposing
       raw provider traces in the reading surface. */
    [class*="st-key-error_callback_"] {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line);
        border-left: 3px solid var(--pm-danger);
        border-radius: var(--pm-radius);
        margin: 10px 0;
        padding: 10px 12px;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-error-callback-anchor),
    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-action-callback-anchor) {
        background: var(--pm-paper);
        border: 1px solid var(--pm-line) !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: none !important;
        margin: 10px 0;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-error-callback-anchor) {
        border-left: 3px solid var(--pm-danger) !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-action-callback-anchor) {
        border-left: 3px solid var(--pm-success) !important;
    }
    .pm-error-callback-anchor,
    .pm-action-callback-anchor,
    [data-testid="stElementContainer"]:has(.pm-error-callback-anchor),
    [data-testid="stElementContainer"]:has(.pm-action-callback-anchor) {
        display: none !important;
    }
    [class*="st-key-error_callback_"] [data-testid="stAlert"] {
        border: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [class*="st-key-backend_link_status"] {
        margin-top: 10px;
        padding: 0 6px;
    }

    /* ── Design-engineering interaction rules ──
       Feedback starts on press, while decorative movement stays out of the
       high-frequency reading surfaces. */
    [data-testid="stMain"] button:active,
    [data-testid="stSidebar"] button:active,
    [data-testid="stChatInput"] button:active,
    [data-testid="stChatMessage"] button:active,
    [data-testid="stDownloadButton"] button:active,
    [data-testid="stFormSubmitButton"] button:active {
        transform: scale(0.97) !important;
        transition-duration: var(--pm-duration-press) !important;
    }

    /* Message bodies and data containers are for reading, not hover theatre. */
    [data-testid="stChatMessageContent"]:hover {
        transform: none !important;
    }

    /* Hover is a fine-pointer affordance; touch devices should not get sticky
       hover transforms after a tap. */
    @media (hover: none), (pointer: coarse) {
        [data-testid="stMain"] button:hover,
        [data-testid="stSidebar"] button:hover,
        [data-testid="stChatInput"] button:hover,
        [data-testid="stChatMessage"] button:hover,
        [data-testid="stDownloadButton"] button:hover,
        [data-testid="stFormSubmitButton"] button:hover {
            transform: none !important;
        }
    }

    /* ── 禁用态更克制 ── */
    button:disabled,
    [data-testid="stChatInput"] button:disabled {
        opacity: 0.45 !important;
        cursor: not-allowed !important;
        box-shadow: none !important;
    }

    /* Chat history is a high-frequency surface; reruns stay visually stable. */
    .st-key-chat_thread { animation: none !important; }

    /* Give the empty state a quiet surface without a decorative gradient. */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker)::before {
        background: var(--pm-accent-soft);
        border-radius: 0 0 var(--pm-radius-xl) var(--pm-radius-xl);
        content: "";
        height: 192px;
        left: 0;
        pointer-events: none;
        position: absolute;
        top: 0;
        width: 100%;
        z-index: 0;
    }

    /* ══════════════════════════════════════
       文件产物卡片 — 对标 Kimi 智能体交互
    ══════════════════════════════════════ */
    .pm-artifact-anchor { display: none !important; }

    [data-testid="stContainer"]:has(.pm-artifact-anchor) {
        background: var(--pm-paper) !important;
        border: 1px solid var(--pm-line) !important;
        border-radius: var(--pm-radius-lg) !important;
        box-shadow: var(--pm-shadow-card) !important;
        padding: 16px 18px !important;
        margin: 12px 0 4px !important;
        transition: box-shadow 200ms ease, transform 200ms ease;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor):hover {
        box-shadow: var(--pm-shadow-md) !important;
    }

    /* 卡片头部：类型图标 + 文件名 + 元信息 */
    .pm-artifact-head {
        align-items: center;
        display: flex;
        gap: 12px;
        margin-bottom: 14px;
    }
    .pm-artifact-icon {
        align-items: center;
        background: var(--pm-accent-soft);
        border-radius: var(--pm-radius);
        color: var(--pm-accent);
        display: flex;
        flex: 0 0 40px;
        font-size: 20px;
        height: 40px;
        justify-content: center;
        width: 40px;
    }
    .pm-artifact-meta { flex: 1 1 auto; min-width: 0; }
    .pm-artifact-name {
        color: var(--pm-ink);
        font-size: 15px;
        font-weight: 650;
        line-height: 1.3;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .pm-artifact-sub {
        color: var(--pm-muted);
        font-size: 12.5px;
        margin-top: 2px;
    }

    /* 主下载按钮（蓝底白字，独占一行） */
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-dl_main_"] button {
        background: var(--pm-accent) !important;
        border-color: var(--pm-accent) !important;
        color: #fff !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-dl_main_"] button:hover {
        background: var(--pm-accent-hover) !important;
        border-color: var(--pm-accent-hover) !important;
        transform: translateY(-1px);
        box-shadow: var(--pm-shadow-md) !important;
    }

    /* 导出区标签 */
    .pm-artifact-export-label {
        color: var(--pm-muted);
        font-size: 12px;
        font-weight: 600;
        margin: 4px 0 8px;
    }

    /* 导出按钮（另存为 X）：浅色次级，hover 高亮 */
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_export_"] button {
        background: var(--pm-surface) !important;
        border-color: var(--pm-line) !important;
        color: var(--pm-ink-secondary) !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        min-height: 38px !important;
        padding: 0 12px !important;
        box-shadow: none !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_export_"] button:hover {
        background: var(--pm-accent-soft) !important;
        border-color: var(--pm-accent) !important;
        color: var(--pm-accent) !important;
        transform: translateY(-1px);
    }

    /* 预览触发按钮（与导出同级，次级浅色，与下载按钮等高并排） */
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_preview_"] button {
        background: var(--pm-surface) !important;
        border-color: var(--pm-line) !important;
        color: var(--pm-ink-secondary) !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        padding: 0 12px !important;
        box-shadow: none !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_preview_"] button:hover {
        background: var(--pm-accent-soft) !important;
        border-color: var(--pm-accent) !important;
        color: var(--pm-accent) !important;
        transform: translateY(-1px);
    }

    /* 预览区分隔线 + 表格容器 */
    .pm-artifact-hr {
        border: 0;
        border-top: 1px solid var(--pm-line-light);
        margin: 12px 0 !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor) [data-testid="stDataFrame"] {
        margin: 0 0 4px !important;
        border-radius: var(--pm-radius) !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor) [data-testid="stDataFrame"] tbody {
        font-size: 13px;
    }

    /* 一句话编辑展开器：去边框，顶部细线分隔，内敛紧凑 */
    [data-testid="stContainer"]:has(.pm-artifact-anchor) [data-testid="stExpander"] {
        border: 0 !important;
        border-top: 1px solid var(--pm-line-light) !important;
        border-radius: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [data-testid="stExpander"] > summary,
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [data-testid="stExpander"] [data-testid="stExpanderSummary"] {
        color: var(--pm-ink-secondary);
        font-size: 13px;
        font-weight: 600;
        padding: 12px 0 !important;
    }

    /* 编辑下载 + 生成新版本按钮：蓝底白字（行动点） */
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_edit_download_"] button,
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_edit_submit_"] button {
        background: var(--pm-accent) !important;
        border-color: var(--pm-accent) !important;
        color: #fff !important;
    }
    [data-testid="stContainer"]:has(.pm-artifact-anchor)
        [class*="st-key-artifact_edit_submit_"] button:hover {
        background: var(--pm-accent-hover) !important;
        border-color: var(--pm-accent-hover) !important;
        transform: translateY(-1px);
        box-shadow: var(--pm-shadow-md) !important;
    }

    /* ══════════════════════════════════════
       响应式适配
    ══════════════════════════════════════ */
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-property: color, background-color, border-color, box-shadow, opacity !important;
            transition-duration: 160ms !important;
            animation: none !important;
        }

        [data-testid="stMain"] button:hover,
        [data-testid="stSidebar"] button:hover,
        [data-testid="stChatMessage"] button:hover,
        [data-testid="stChatMessageContent"]:hover {
            transform: none !important;
        }
    }

    @media (prefers-reduced-transparency: reduce) {
        [data-testid="stSidebar"],
        [data-testid="stChatInput"] {
            background: var(--pm-paper) !important;
            backdrop-filter: none !important;
        }
    }

    @media (prefers-contrast: more) {
        [data-testid="stSidebar"],
        [data-testid="stChatInput"],
        [data-testid="stChatMessageContent"] {
            border-color: var(--pm-ink-secondary) !important;
        }
    }

    @media (max-width: 760px) {
        :root { --pm-composer-reserve: 164px; }

        [data-testid="stSidebar"] {
            min-width: min(88vw, 300px) !important;
            width: min(88vw, 300px) !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding: 18px 18px 32px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
            padding-left: 16px;
            padding-right: 16px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
            padding-top: 96px;
        }

        [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
            max-width: calc(100vw - 32px);
        }

        .st-key-chat_access_popover {
            left: 56px;
        }

        .st-key-chat_voice_callback {
            bottom: calc(100% + 8px);
        }

        .pm-voice-callback {
            margin-left: 0;
            max-width: 100%;
            min-height: 42px;
        }

        .st-key-chat_access_popover button {
            font-size: 13px !important;
            padding-left: 6px !important;
            padding-right: 6px !important;
        }

        .st-key-chat_composer_shell [data-testid="stChatInput"] textarea {
            padding-left: 14px !important;
            padding-right: 14px !important;
        }

        [data-testid="stPopoverBody"]:has(.pm-access-panel-anchor) {
            border-radius: 12px !important;
            padding: 16px !important;
            width: calc(100vw - 24px) !important;
        }

        .st-key-chat_thread {
            gap: 8px !important;
            max-width: 100%;
        }

        [class*="st-key-permission_actions_"] [data-testid="stHorizontalBlock"] {
            flex-direction: column;
        }

        [class*="st-key-permission_actions_"] [data-testid="stColumn"] {
            flex: 1 1 auto !important;
            min-width: 0 !important;
            width: 100% !important;
        }

        [data-testid="stVerticalBlockBorderWrapper"]:has(.pm-permission-anchor)
        > [data-testid="stVerticalBlock"] {
            padding: 14px;
        }

        .pm-permission-facts { grid-template-columns: 1fr; }

        .pm-permission-fact + .pm-permission-fact {
            border-left: 0;
            border-top: 1px solid var(--pm-line-light);
            padding-left: 0;
            padding-top: 8px;
        }

        .pm-workflow-canvas {
            align-items: stretch;
            flex-direction: column;
            overflow-x: hidden;
        }

        .pm-workflow-node {
            flex-basis: auto;
            min-height: 80px;
            width: 100%;
        }

        .pm-workflow-edge {
            align-self: center;
            transform: rotate(90deg);
        }

        [data-testid="stChatMessage"]:has(.chat-role-user)
        [data-testid="stChatMessageContent"] { max-width: 88%; }

        h1 { font-size: 26px !important; }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) h1 {
            font-size: 28px !important;
        }

        .st-key-welcome_suggestions {
            gap: 8px;
            margin-top: 20px;
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
