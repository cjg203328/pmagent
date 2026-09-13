"""Versioned built-in ArtPM workflow definitions."""

from __future__ import annotations

from .models import WorkflowDefinition, WorkflowStepDefinition, WorkflowTrigger


QUOTE_ASSESSMENT = WorkflowDefinition(
    id="quote_assessment",
    version=1,
    name="报价评估",
    description="根据报价与成本数据计算利润并给出项目风险结论。",
    source="builtin",
    read_only=True,
    priority=30,
    trigger=WorkflowTrigger(
        keywords=("报价", "成本", "利润", "毛利", "净利", "赚多少"),
        attachment_extensions=("xlsx", "xls", "csv"),
        min_keyword_matches=1,
    ),
    steps=(
        WorkflowStepDefinition(
            id="calculate_quote",
            skill_id="quote_calculator",
            capability="quote.calculate",
            input_map={
                "quote_amount": "$input.quote_amount",
                "cost": "$input.cost",
                "cost_config": "$input.cost_config",
                "overhead_rate": "$input.overhead_rate",
            },
            output_key="assessment",
            on_error="retry",
            retryable=True,
            idempotent=True,
            max_retries=2,
            retry_backoff="exponential",
            retry_backoff_seconds=1.0,
        ),
    ),
)

PROGRESS_CHECK = WorkflowDefinition(
    id="progress_check",
    version=1,
    name="进度检查",
    description="检查项目任务进度、临期节点与延期风险。",
    source="builtin",
    read_only=True,
    priority=20,
    trigger=WorkflowTrigger(
        keywords=("进度", "延期", "截止", "到期", "里程碑", "交付"),
        required_context_keys=("project_id",),
        min_keyword_matches=1,
    ),
    steps=(
        WorkflowStepDefinition(
            id="check_progress",
            skill_id="progress_tracker",
            capability="projects.read",
            input_map={"project_id": "$context.project_id"},
            output_key="progress",
            on_error="retry",
            retryable=True,
            idempotent=True,
            max_retries=2,
            retry_backoff="exponential",
            retry_backoff_seconds=1.0,
        ),
    ),
)

REMINDER_DISPATCH = WorkflowDefinition(
    id="reminder_dispatch",
    version=1,
    name="催办发送",
    description="根据任务上下文生成并发送催办提醒。",
    source="builtin",
    read_only=True,
    priority=25,
    trigger=WorkflowTrigger(
        keywords=("催办", "提醒", "通知", "催一下", "发消息"),
        required_context_keys=("task_id",),
        min_keyword_matches=1,
    ),
    steps=(
        WorkflowStepDefinition(
            id="preview_reminder",
            skill_id="reminder_bot",
            capability="reminders.preview",
            input_map={
                "task_id": "$context.task_id",
                "recipients": "$input.recipients",
                "tone": "$input.tone",
            },
            output_key="preview",
        ),
        WorkflowStepDefinition(
            id="dispatch_reminder",
            skill_id="reminder_dispatch",
            capability="reminders.dispatch",
            input_map={
                "task_id": "$context.task_id",
                "recipients": "$input.recipients",
                "tone": "$input.tone",
            },
            output_key="delivery",
            side_effect=True,
            approval="user",
        ),
    ),
)

BUILTIN_WORKFLOWS: tuple[WorkflowDefinition, ...] = (
    QUOTE_ASSESSMENT,
    PROGRESS_CHECK,
    REMINDER_DISPATCH,
)


def get_builtin_workflows() -> tuple[WorkflowDefinition, ...]:
    """Return the immutable built-in workflow catalog."""
    return BUILTIN_WORKFLOWS
