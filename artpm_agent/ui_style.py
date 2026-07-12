STYLE_CSS = """
<style>
    :root {
        --pm-paper: #ffffff;
        --pm-canvas: #f4f6f8;
        --pm-ink: #18202e;
        --pm-muted: #667085;
        --pm-line: #dde2e8;
        --pm-accent: #2458c6;
        --pm-accent-soft: #eaf1ff;
        --pm-success: #147d64;
        --pm-success-soft: #eaf7f3;
        --pm-warning: #b7680b;
        --pm-warning-soft: #fff4df;
        --pm-danger: #b93830;
        --pm-danger-soft: #fff0ef;
        --pm-radius: 6px;
        --pm-space-1: 4px;
        --pm-space-2: 8px;
        --pm-space-3: 12px;
        --pm-space-4: 16px;
        --pm-space-5: 20px;
        --pm-space-6: 24px;
        --pm-space-8: 32px;
        --pm-chat-shell-width: 800px;
        --pm-chat-thread-width: 720px;
    }

    *, *::before, *::after {
        box-sizing: border-box;
        letter-spacing: 0 !important;
    }

    html, body {
        font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
        color: var(--pm-ink);
    }

    #MainMenu, footer, header, .stDeployButton {
        display: none !important;
    }

    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: var(--pm-canvas);
    }

    [data-testid="stMainBlockContainer"] {
        width: 100%;
        max-width: 1240px;
        padding: 28px 40px 48px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
        max-width: calc(var(--pm-chat-shell-width) + 40px);
        padding-left: 20px;
        padding-right: 20px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
        padding-top: clamp(176px, 25vh, 260px);
        text-align: center;
    }

    h1, h2, h3, p {
        margin-top: 0;
    }

    h1 {
        color: var(--pm-ink);
        font-size: 28px !important;
        line-height: 1.25 !important;
        font-weight: 680 !important;
        margin: 0 0 4px !important;
    }

    h2 {
        color: var(--pm-ink);
        font-size: 18px !important;
        line-height: 1.4 !important;
        font-weight: 650 !important;
    }

    h3 {
        color: var(--pm-ink);
        font-size: 15px !important;
        line-height: 1.45 !important;
        font-weight: 650 !important;
    }

    p, li, label, [data-testid="stMarkdownContainer"] {
        font-size: 14px;
        line-height: 1.65;
    }

    .page-kicker,
    .section-label,
    .sidebar-section-label {
        color: var(--pm-accent);
        font-size: 12px;
        font-weight: 650;
        line-height: 1.4;
    }

    .page-kicker {
        margin-bottom: 6px;
    }

    .page-meta {
        color: var(--pm-muted);
        font-size: 13px;
        margin: 0 0 20px;
    }

    .section-heading {
        align-items: baseline;
        border-bottom: 1px solid var(--pm-line);
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
        width: 44px;
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

    /* Sidebar */
    [data-testid="stSidebar"] {
        background: #f0f3f2;
        border-right: 1px solid var(--pm-line);
        min-width: 280px !important;
        width: 280px !important;
    }

    [data-testid="stSidebarContent"] {
        padding: 24px 16px 20px;
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0;
    }

    .sidebar-brand {
        align-items: center;
        display: flex;
        gap: 10px;
        margin: 2px 4px 22px;
    }

    .brand-mark {
        background: var(--pm-accent);
        display: block;
        height: 14px;
        transform: rotate(45deg);
        width: 14px;
    }

    .brand-name {
        color: var(--pm-ink);
        font-size: 16px;
        font-weight: 700;
        line-height: 1;
    }

    .sidebar-section-label {
        margin: 0 8px 8px;
        padding-bottom: 8px;
    }

    [data-testid="stSidebar"] .stButton {
        margin-bottom: 4px;
    }

    [data-testid="stSidebar"] .stButton button {
        border: 1px solid transparent;
        border-radius: var(--pm-radius);
        box-shadow: none;
        justify-content: flex-start;
        min-height: 44px;
        padding: 0 12px;
        transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
        width: 100%;
    }

    [data-testid="stSidebar"] .stButton button p {
        text-align: left;
        width: 100%;
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"] {
        background: transparent;
        color: #4b5565;
    }

    [data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {
        background: rgba(255, 255, 255, 0.72);
        border-color: #d5dae1;
        color: var(--pm-ink);
    }

    [data-testid="stSidebar"] .stButton button[kind="primary"] {
        background: var(--pm-accent-soft);
        border-color: transparent;
        box-shadow: inset 3px 0 0 var(--pm-accent);
        color: var(--pm-ink);
        font-weight: 650;
    }

    .st-key-conversation_panel {
        margin-top: 4px;
    }

    .st-key-conversation_panel .sidebar-section-label {
        margin-left: 4px;
    }

    .st-key-conversation_list {
        margin: 4px 0 8px;
        overflow-x: hidden;
    }

    .st-key-conversation_list .stButton {
        margin-bottom: 2px !important;
    }

    .st-key-conversation_list .stButton button {
        min-height: 36px !important;
        padding: 0 10px !important;
    }

    .st-key-conversation_list .stButton button p {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stHorizontalBlock"] {
        gap: 4px;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child {
        min-width: 36px;
        flex: 0 0 36px;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child button {
        width: 36px !important;
        min-width: 36px !important;
        padding: 0 !important;
        justify-content: center !important;
    }

    .st-key-conversation_list [class*="st-key-conversation_row_"] [data-testid="stColumn"]:last-child button p {
        display: none;
    }

    .st-key-conversation_list [class*="st-key-conversation_delete_confirm_"] {
        border-left: 2px solid var(--pm-danger);
        margin: 2px 0 6px;
        padding-left: 8px;
    }

    .st-key-new_conversation button {
        justify-content: center !important;
        min-height: 38px !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background: rgba(255, 255, 255, 0.48);
        border-color: var(--pm-line);
        border-radius: var(--pm-radius);
        margin-top: 6px;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton button {
        justify-content: center;
        min-height: 38px;
    }

    .st-key-sidebar_modes {
        border-top: 1px solid var(--pm-line);
        margin-top: 14px;
        padding-top: 12px;
    }

    .st-key-sidebar_modes [data-testid="stHorizontalBlock"] {
        gap: 8px;
    }

    .st-key-sidebar_modes .stButton button {
        justify-content: center !important;
        min-height: 38px !important;
    }

    .sidebar-ledger {
        border-top: 1px solid var(--pm-line);
        margin-top: 24px;
        padding: 18px 4px 0;
    }

    .ledger-row {
        align-items: center;
        display: flex;
        justify-content: space-between;
        min-height: 30px;
    }

    .ledger-row span {
        color: var(--pm-muted);
        font-size: 12px;
    }

    .ledger-row strong {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        font-size: 13px;
        font-weight: 600;
    }

    .service-state {
        align-items: center;
        color: var(--pm-muted);
        display: flex;
        font-size: 12px;
        gap: 7px;
        margin: 20px 4px 0;
    }

    .state-dot {
        background: var(--pm-success);
        border-radius: 50%;
        height: 7px;
        width: 7px;
    }

    .state-dot.offline {
        background: var(--pm-danger);
    }

    /* Main controls */
    [data-testid="stMain"] .stButton button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        border-radius: var(--pm-radius);
        box-shadow: none;
        font-size: 14px;
        font-weight: 600;
        min-height: 44px;
        padding: 0 16px;
        transition: background-color 120ms ease, border-color 120ms ease;
    }

    [data-testid="stMain"] button[kind="primary"] {
        background: var(--pm-accent) !important;
        border-color: var(--pm-accent) !important;
    }

    [data-testid="stMain"] button[kind="primary"]:hover {
        background: #1d49b2 !important;
        border-color: #1d49b2 !important;
    }

    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    [role="tab"]:focus-visible {
        outline: 3px solid rgba(36, 88, 211, 0.24) !important;
        outline-offset: 2px !important;
    }

    .stTextInput input,
    .stTextArea textarea,
    [data-baseweb="select"] > div {
        background: var(--pm-paper) !important;
        border-color: #cfd5dd !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: none !important;
        min-height: 44px;
    }

    .stTextInput input:focus,
    .stTextArea textarea:focus {
        border-color: var(--pm-accent) !important;
        box-shadow: 0 0 0 3px rgba(36, 88, 211, 0.12) !important;
    }

    [data-testid="stFileUploaderDropzone"] {
        background: transparent;
        border: 1px dashed #b8c0cc;
        border-radius: var(--pm-radius);
        min-height: 132px;
        padding: 20px;
    }

    [data-testid="stFileUploaderDropzone"] button {
        min-height: 40px;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--pm-line);
        border-radius: var(--pm-radius);
        overflow: hidden;
    }

    [data-testid="stAlert"] {
        border-radius: var(--pm-radius);
        box-shadow: none;
    }

    [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--pm-line);
        gap: 20px;
    }

    [data-baseweb="tab"] {
        font-size: 14px;
        min-height: 44px;
        padding-left: 2px;
        padding-right: 2px;
    }

    [data-baseweb="tab-highlight"] {
        background-color: var(--pm-accent);
    }

    /* Project ledger metric band */
    .metric-rail {
        background: var(--pm-paper);
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 16px 0 0;
    }

    .metric-item {
        min-width: 0;
        padding: 16px 20px;
    }

    .metric-item.tone-blue {
        background: var(--pm-accent-soft);
        box-shadow: inset 0 3px 0 var(--pm-accent);
    }

    .metric-item.tone-amber {
        background: var(--pm-warning-soft);
        box-shadow: inset 0 3px 0 var(--pm-warning);
    }

    .metric-item.tone-teal {
        background: var(--pm-success-soft);
        box-shadow: inset 0 3px 0 var(--pm-success);
    }

    .metric-item.tone-blue .metric-label {
        color: var(--pm-accent);
    }

    .metric-item.tone-amber .metric-label {
        color: var(--pm-warning);
    }

    .metric-item.tone-teal .metric-label {
        color: var(--pm-success);
    }

    .metric-item:not(:last-child) {
        border-right: 1px solid var(--pm-line);
    }

    .metric-label {
        color: var(--pm-muted);
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 5px;
    }

    .metric-value {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        font-size: 24px;
        font-weight: 600;
        line-height: 1.2;
        overflow-wrap: anywhere;
    }

    .metric-note {
        color: var(--pm-muted);
        font-size: 12px;
        margin-top: 4px;
    }

    .metric-note.positive {
        color: var(--pm-success);
    }

    .definition-grid {
        background: var(--pm-paper);
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin-top: 12px;
    }

    .definition-item {
        border-bottom: 1px solid var(--pm-line);
        display: grid;
        gap: 8px;
        grid-template-columns: 96px 1fr;
        min-height: 48px;
        padding: 12px 16px;
    }

    .definition-item:nth-child(odd) {
        border-right: 1px solid var(--pm-line);
    }

    .definition-item dt {
        color: var(--pm-muted);
        font-size: 12px;
    }

    .definition-item dd {
        color: var(--pm-ink);
        font-size: 14px;
        font-weight: 600;
        margin: 0;
        overflow-wrap: anywhere;
    }

    .step-rail {
        border-bottom: 1px solid var(--pm-line);
        border-top: 1px solid var(--pm-line);
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        margin: 16px 0 20px;
    }

    .step {
        color: var(--pm-muted);
        font-size: 13px;
        padding: 12px 16px;
    }

    .step.current {
        background: var(--pm-accent-soft);
        color: var(--pm-accent);
    }

    .step.pending {
        background: var(--pm-warning-soft);
        color: var(--pm-warning);
    }

    .step + .step {
        border-left: 1px solid var(--pm-line);
    }

    .step strong {
        color: var(--pm-ink);
        font-family: Bahnschrift, "Segoe UI", sans-serif;
        margin-right: 8px;
    }

    /* Conversation */
    .chat-empty-marker {
        height: 0;
        overflow: hidden;
    }

    .chat-ready-state {
        align-items: center;
        color: var(--pm-muted);
        display: flex;
        font-size: 13px;
        gap: 8px;
        justify-content: center;
        margin-top: 10px;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) h1 {
        font-size: 30px !important;
        text-align: center;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-meta,
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) .page-kicker {
        display: none;
    }

    [data-testid="stChatMessage"] {
        background: transparent;
        border-bottom: 0;
        border-radius: 0;
        gap: 12px;
        padding: 8px 0;
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

    [data-testid="stChatMessage"]:has(.chat-role-user) {
        background: transparent;
        border-radius: 0;
        justify-content: flex-end;
        margin: 0;
        max-width: none;
        padding: 8px 0;
        width: 100%;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageAvatarCustom"] {
        display: none;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stChatMessageContent"] {
        background: var(--pm-accent-soft);
        border-radius: 8px;
        flex: 0 1 auto;
        margin: 0;
        max-width: 74%;
        min-height: 36px;
        min-width: 0;
        overflow-wrap: anywhere;
        padding: 7px 12px;
        width: fit-content;
    }

    [data-testid="stChatMessageContent"]
    [data-testid="stMarkdownContainer"] {
        margin-bottom: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] {
        min-width: 0;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] li {
        font-size: 14px !important;
        line-height: 1.45 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :first-child {
        margin-top: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] > :last-child {
        margin-bottom: 0 !important;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ul,
    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] ol {
        margin-block: 4px;
        padding-inline-start: 20px;
    }

    [data-testid="stChatMessage"]:has(.chat-role-user)
    [data-testid="stMarkdownContainer"] pre {
        max-width: 100%;
        overflow-x: auto;
    }

    [data-testid="stChatMessage"]:has(.chat-role-assistant) {
        margin-bottom: 8px;
    }

    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarAssistant"] {
        border: 1px solid var(--pm-line);
        height: 32px;
        width: 32px;
    }

    [data-testid="stChatMessageContent"] p:last-child {
        margin-bottom: 0 !important;
    }

    .chat-attachments {
        color: var(--pm-muted);
        display: flex;
        flex-wrap: wrap;
        font-size: 12px;
        gap: 4px 10px;
        margin-top: 6px;
    }

    .chat-attachment-name {
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    [data-testid="stChatInput"] {
        background: var(--pm-paper);
        border: 1px solid #cfd5dd;
        border-radius: 16px;
        box-shadow: 0 10px 30px rgba(24, 32, 46, 0.10);
    }

    [data-testid="stChatInput"] textarea {
        background: transparent !important;
        font-size: 14px;
        min-height: 46px;
    }

    [data-testid="stChatInput"] textarea:focus {
        box-shadow: none !important;
        outline: none !important;
    }

    [data-testid="stChatInput"] button {
        color: var(--pm-accent);
        min-height: 44px;
        min-width: 44px;
    }

    .st-key-clear_chat {
        align-items: flex-end;
        display: flex;
    }

    .st-key-clear_chat button {
        min-width: 44px !important;
        margin-left: auto;
        padding: 0 !important;
        width: 44px !important;
    }

    .st-key-clear_chat button p {
        border: 0;
        clip: rect(0 0 0 0);
        clip-path: inset(50%);
        height: 1px;
        margin: -1px;
        overflow: hidden;
        padding: 0;
        position: absolute;
        white-space: nowrap;
        width: 1px;
    }

    .st-key-chat_header [data-testid="stHorizontalBlock"] {
        align-items: flex-end;
        flex-wrap: nowrap;
    }

    .st-key-chat_header [data-testid="stColumn"]:first-child {
        flex: 1 1 auto;
        min-width: 0;
    }

    .st-key-chat_header [data-testid="stColumn"]:last-child {
        flex: 0 0 44px;
        min-width: 44px;
        width: 44px;
    }

    [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
        margin-left: auto;
        margin-right: auto;
        max-width: var(--pm-chat-shell-width);
    }

    [data-testid="stBottom"] {
        background: transparent !important;
    }

    [data-testid="stAppScrollToBottomContainer"]:has(.chat-empty-marker)
    [data-testid="stChatInput"] textarea {
        min-height: 72px;
        padding-top: 14px;
    }

    .stSpinner > div {
        border-top-color: var(--pm-accent) !important;
    }

    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-duration: 0.01ms !important;
        }
    }

    @media (max-width: 760px) {
        [data-testid="stSidebar"] {
            min-width: min(88vw, 320px) !important;
            width: min(88vw, 320px) !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding: 20px 16px 32px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-page-marker) {
            padding-left: 16px;
            padding-right: 16px;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
            padding-top: 176px;
        }

        [data-testid="stBottomBlockContainer"] [data-testid="stVerticalBlock"] {
            max-width: calc(100vw - 32px);
        }

        .st-key-chat_thread {
            max-width: 100%;
        }

        [data-testid="stChatMessage"]:has(.chat-role-user)
        [data-testid="stChatMessageContent"] {
            max-width: 88%;
        }

        h1 {
            font-size: 24px !important;
        }

        .metric-rail {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .metric-item:nth-child(2) {
            border-right: 0;
        }

        .metric-item:nth-child(-n+2) {
            border-bottom: 1px solid var(--pm-line);
        }

        .metric-item {
            padding: 14px 12px;
        }

        .metric-value {
            font-size: 21px;
        }

        .definition-grid {
            grid-template-columns: 1fr;
        }

        .definition-item:nth-child(odd) {
            border-right: 0;
        }

        .section-heading {
            align-items: flex-start;
            gap: 8px;
        }
    }
</style>
"""
