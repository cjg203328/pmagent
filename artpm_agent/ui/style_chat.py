"""Chat-specific visual layer kept outside the legacy stylesheet facade."""


CHAT_POLISH_CSS = """
<style>
    /* Compact, work-focused chat landing surface. */
    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
        padding-top: clamp(64px, 10vh, 104px) !important;
    }

    [data-testid="stMainBlockContainer"]:has(.chat-empty-marker)::before {
        background: var(--pm-sidebar-bg) !important;
        border-bottom: 1px solid var(--pm-line-light);
        border-radius: 0 !important;
        height: 144px !important;
    }

    .pm-welcome {
        margin: 0 auto;
        max-width: 720px;
        position: relative;
        text-align: center;
        width: 100%;
        z-index: 1;
    }

    .pm-welcome-kicker {
        align-items: center;
        color: var(--pm-muted);
        display: inline-flex;
        font-size: 12px;
        font-weight: 650;
        gap: 8px;
        line-height: 1;
        margin-bottom: 18px;
    }

    .pm-welcome-mark {
        align-items: center;
        background: var(--pm-accent);
        border-radius: var(--pm-radius-sm);
        color: var(--pm-paper);
        display: inline-flex;
        font-size: 11px;
        height: 28px;
        justify-content: center;
        width: 28px;
    }

    .pm-welcome h1 {
        color: var(--pm-ink) !important;
        font-size: 30px !important;
        line-height: 1.24 !important;
        margin: 0 0 10px !important;
    }

    .pm-welcome p {
        color: var(--pm-muted) !important;
        font-size: 15px !important;
        line-height: 1.6 !important;
        margin: 0 auto !important;
        max-width: 520px;
    }

    .st-key-welcome_suggestions {
        margin: 24px auto 0 !important;
        max-width: 720px !important;
        position: relative;
        width: 100%;
        z-index: 1;
    }

    .st-key-welcome_suggestions > [data-testid="stVerticalBlock"] {
        gap: 10px !important;
    }

    .st-key-welcome_suggestions [data-testid="stHorizontalBlock"] {
        gap: 10px !important;
    }

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button {
        background: var(--pm-paper) !important;
        border: 1px solid var(--pm-line) !important;
        border-radius: var(--pm-radius) !important;
        box-shadow: var(--pm-shadow-sm) !important;
        color: var(--pm-ink-secondary) !important;
        justify-content: flex-start !important;
        min-height: 50px !important;
        padding: 0 16px !important;
        text-align: left !important;
        width: 100% !important;
    }

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button:hover {
        background: var(--pm-accent-soft) !important;
        border-color: var(--pm-accent) !important;
        box-shadow: var(--pm-shadow-md) !important;
        color: var(--pm-accent) !important;
        transform: translateY(-1px);
    }

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--pm-focus) !important;
        outline-offset: 2px !important;
    }

    .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
        [data-testid="stButton"] button p {
        color: inherit !important;
        font-size: 13.5px !important;
        font-weight: 600 !important;
        text-align: left !important;
    }

    [data-testid="stBottomBlockContainer"] {
        border-top: 1px solid var(--pm-line-light);
        padding-top: 12px !important;
    }

    [data-testid="stChatInput"] {
        border-radius: var(--pm-radius-lg) !important;
        box-shadow: var(--pm-shadow-md) !important;
    }

    [data-testid="stChatInput"]:focus-within {
        box-shadow: var(--pm-shadow-input), 0 0 0 2px var(--pm-accent-glow) !important;
    }

    [data-testid="stChatInput"] textarea {
        min-height: 44px !important;
        padding-bottom: 4px !important;
        padding-top: 11px !important;
    }

    @media (max-width: 760px) {
        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker) {
            padding-top: 48px !important;
        }

        [data-testid="stMainBlockContainer"]:has(.chat-empty-marker)::before {
            height: 116px !important;
        }

        .pm-welcome-kicker { margin-bottom: 14px; }

        .pm-welcome h1 { font-size: 27px !important; }

        .pm-welcome p {
            font-size: 14px !important;
            max-width: 34ch;
        }

        .st-key-welcome_suggestions {
            margin-top: 18px !important;
        }

        .st-key-welcome_suggestions [data-testid="stHorizontalBlock"] {
            display: grid !important;
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .st-key-welcome_suggestions [data-testid="stColumn"] {
            min-width: 0 !important;
            width: auto !important;
        }

        .st-key-welcome_suggestions [data-testid="stHorizontalBlock"]
            > [data-testid="stColumn"]:only-child {
            grid-column: 1 / -1;
        }

        .st-key-welcome_suggestions [data-testid="stVerticalBlock"]
            [data-testid="stButton"] button {
            min-height: 48px !important;
            padding: 0 12px !important;
        }
    }

</style>
"""


__all__ = ["CHAT_POLISH_CSS"]
