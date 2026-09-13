"""
Skill Router - Route user intents to appropriate skills

Core skill implementations and adapters used by the synchronous agent router.
"""
from copy import deepcopy
import asyncio
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import RLock
from tempfile import TemporaryDirectory
from typing import Dict, Any, List, Optional

from .base_skill import BaseSkill
from .smart_progress_tracker import SmartProgressTracker
from .smart_task_allocator import SmartTaskAllocator
from .quality_control_skill import QualityControlSkill
from .requirements_assessment_skill import RequirementsAssessmentSkill
from .cost_control_skill import CostControlSkill
from .quote_scheduling_skill import QuoteSchedulingSkill
from .progress_management_skill import ProgressManagementSkill
from .delivery_skill import DeliverySkill
from .retrospective_skill import RetrospectiveSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS
from artpm_agent.parsers.excel_parser import ExcelQuoteParser
from artpm_agent.utils.image_validation import MAX_IMAGE_FILE_SIZE, load_validated_image
from artpm_agent.utils.mineru_adapter import (
    MINERU_SUPPORTED_SUFFIXES,
    MinerUDocumentConverter,
)
from artpm_agent.utils.unlimited_ocr import UnlimitedOCRClient
from artpm_agent.plugins import PluginManager
from artpm_agent.tenancy import (
    TenantContext,
    TenantContextManager,
    TenantContextError,
    WorkspaceAccessDenied,
    tenant_context_from_host,
)


logger = logging.getLogger(__name__)
_REMINDER_DISPATCH_RESULTS: Dict[str, Dict[str, Any]] = {}
_REMINDER_DISPATCH_LOCK = RLock()
MAX_DOCUMENT_FILE_SIZE = 50 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 32768
_LEGACY_DOCUMENT_SUFFIXES = frozenset({
    ".xlsx",
    ".xls",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".pdf",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
})
_SUPPORTED_DOCUMENT_SUFFIXES = _LEGACY_DOCUMENT_SUFFIXES | MINERU_SUPPORTED_SUFFIXES


def _ocr_client_ready(client: Any) -> bool:
    """Use a cached service probe when supported; test doubles stay compatible."""
    if client is None:
        return False
    ready = getattr(client, "ready", None)
    try:
        if callable(ready):
            return bool(ready())
        return bool(getattr(client, "configured", True))
    except Exception:
        return False


# ===== Inline Skill Implementations =====

class DocumentClassifierParser(BaseSkill):
    """智能识别和分类各类业务文档(报价单/排期表/反馈单等),提取结构化数据"""

    skill_name = "document_classifier_parser"
    description = "智能识别和分类各类业务文档(报价单/排期表/反馈单等),提取结构化数据"
    version = "1.0"
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        file_path = inputs.get("file_path", "")
        source = inputs.get("source", "local")
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            return {"success": False, "error": f"文件不存在: {file_path}"}

        suffix = path.suffix.lower()
        if suffix not in _SUPPORTED_DOCUMENT_SUFFIXES:
            return {
                "success": False,
                "error": f"暂不支持的文档格式: {suffix or '无扩展名'}",
            }
        file_size = path.stat().st_size
        if file_size <= 0:
            return {"success": False, "error": "附件文件不能为空"}
        if file_size > MAX_DOCUMENT_FILE_SIZE:
            return {
                "success": False,
                "error": "单个附件不能超过 50 MB",
            }

        # MinerU is an optional document backend.  It is injected by the
        # Agent context so direct skill calls and LangGraph tasks share the
        # same conversion contract; unavailable/failed conversion falls back
        # to the existing lightweight parsers below.
        mineru_converter = self.context.get("mineru_converter")
        if mineru_converter is None:
            mineru_config = self.config.get("mineru") if isinstance(self.config, dict) else None
            if isinstance(mineru_config, dict):
                mineru_converter = MinerUDocumentConverter(mineru_config)
                self.context["mineru_converter"] = mineru_converter
        mineru_error = None
        if mineru_converter is not None and suffix in MINERU_SUPPORTED_SUFFIXES:
            try:
                converted = mineru_converter.convert(
                    path,
                    user_hint=str(inputs.get("user_hint") or ""),
                )
                if converted.success:
                    return converted.as_document_result(path, source=source)
                if converted.attempted:
                    mineru_error = converted.error
                    self._log(
                        f"MinerU conversion failed; using local fallback: {converted.error}",
                        "WARNING",
                    )
            except Exception as error:
                mineru_error = str(error)
                self._log(
                    f"MinerU adapter failed; using local fallback: {error}",
                    "WARNING",
                )

        if suffix not in _LEGACY_DOCUMENT_SUFFIXES:
            detail = mineru_error or "MinerU backend is not available"
            return {
                "success": False,
                "error": f"This format requires MinerU: {suffix}. {detail}",
            }

        if suffix in {".xlsx", ".xls"}:
            parsed = ExcelQuoteParser().parse(path, inputs.get("user_hint"))
            if not parsed.get("success"):
                return parsed
            return {
                **parsed,
                "source": source,
                "file_path": str(path.resolve()),
                "confidence": 0.95 if parsed.get("assets") else 0.7,
                "raw_text": "",
            }

        if suffix in {".txt", ".md", ".csv", ".json"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            document_type = "报价单" if any(word in content for word in ["报价", "单价", "总价"]) else "普通文档"
            return {
                "success": True,
                "document_type": document_type,
                "source": source,
                "file_path": str(path.resolve()),
                "extracted_data": {},
                "raw_text": content[:MAX_EXTRACTED_TEXT_CHARS],
                "confidence": 0.7 if document_type == "报价单" else 0.5,
            }

        if suffix == ".pdf":
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                page_texts = [page.extract_text() or "" for page in pdf.pages]
                page_count = len(pdf.pages)
            blank_page_indices = [
                index for index, text in enumerate(page_texts) if not text.strip()
            ]
            content = "\n".join(text for text in page_texts if text.strip())
            extracted_data = {
                "page_count": page_count,
                "native_text_pages": page_count - len(blank_page_indices),
                "pages_requiring_ocr": [index + 1 for index in blank_page_indices],
            }
            ocr_status = "not_needed" if not blank_page_indices else "unavailable"
            if blank_page_indices:
                ocr_client = self.context.get("unlimited_ocr_client")
                if ocr_client is None:
                    configured_ocr = self.context.get("unlimited_ocr")
                    if configured_ocr:
                        ocr_client = (
                            configured_ocr
                            if hasattr(configured_ocr, "parse")
                            else UnlimitedOCRClient(configured_ocr)
                        )
                if _ocr_client_ready(ocr_client):
                    try:
                        import fitz

                        with TemporaryDirectory(prefix="artpm_ocr_pdf_") as temp_dir:
                            rendered_paths = []
                            render_indices = blank_page_indices[:16]
                            render_count = len(render_indices)
                            matrix = fitz.Matrix(300 / 72, 300 / 72)
                            document = fitz.open(path)
                            try:
                                for page_index in render_indices:
                                    rendered_path = Path(temp_dir) / (
                                        f"page_{page_index + 1:04d}.png"
                                    )
                                    document[page_index].get_pixmap(
                                        matrix=matrix, alpha=False
                                    ).save(str(rendered_path))
                                    rendered_paths.append(rendered_path)
                            finally:
                                document.close()
                            multi_page = len(rendered_paths) > 1
                            ocr_result = ocr_client.parse(
                                rendered_paths,
                                prompt=(
                                    "Multi page parsing."
                                    if multi_page
                                    else "document parsing."
                                ),
                                image_mode="base" if multi_page else "gundam",
                            )
                            ocr_prompt = (
                                "Multi page parsing."
                                if multi_page
                                else "document parsing."
                            )
                            ocr_image_mode = "base" if multi_page else "gundam"
                        ocr_text = str(getattr(ocr_result, "text", "") or "").strip()
                        ocr_ok = bool(getattr(ocr_result, "ok", False)) and bool(ocr_text)
                        ocr_status = "completed" if ocr_ok else "unavailable"
                        extracted_data["ocr"] = {
                            "engine": str(
                                getattr(ocr_result, "source", "unlimited-ocr")
                            ),
                            "ok": ocr_ok,
                            "degraded": bool(getattr(ocr_result, "degraded", False)),
                            "truncated": bool(getattr(ocr_result, "truncated", False)),
                            "rendered_pages": render_count,
                            "rendered_page_numbers": [
                                index + 1 for index in render_indices
                            ],
                            "pages_truncated": len(blank_page_indices) > render_count,
                            "quality": "unreported",
                            "model": getattr(
                                getattr(ocr_client, "config", None), "model", None
                            ),
                            "image_mode": ocr_image_mode,
                            "prompt": ocr_prompt,
                            "processor_applied": bool(
                                getattr(
                                    getattr(ocr_client, "config", None),
                                    "custom_logit_processor",
                                    "",
                                )
                            ),
                        }
                        if getattr(ocr_result, "error", None):
                            extracted_data["ocr"]["error"] = str(ocr_result.error)
                        if ocr_ok:
                            page_label = ", ".join(
                                str(index + 1) for index in render_indices
                            )
                            ocr_block = f"[OCR pages {page_label}]\n{ocr_text}"
                            content = "\n\n".join(
                                block for block in (content, ocr_block) if block
                            )
                    except Exception as error:
                        ocr_status = "unavailable"
                        extracted_data["ocr"] = {
                            "engine": "unlimited-ocr",
                            "ok": False,
                            "degraded": True,
                            "error": "OCR service unavailable",
                        }
                        self._log(f"Unlimited-OCR PDF fallback unavailable: {error}", "WARNING")
            return {
                "success": True,
                "document_type": "PDF资料",
                "source": source,
                "file_path": str(path.resolve()),
                "extracted_data": extracted_data,
                "raw_text": content[:MAX_EXTRACTED_TEXT_CHARS],
                "ocr_status": ocr_status,
                "ocr_available": ocr_status == "completed",
                "requires_vision": bool(
                    blank_page_indices and ocr_status != "completed"
                ),
                "confidence": (
                    None
                    if blank_page_indices
                    else (0.8 if content.strip() else 0.4)
                ),
            }

        if suffix == ".docx":
            from docx import Document

            document = Document(path)
            blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    blocks.append("\t".join(cell.text.strip() for cell in row.cells))
            content = "\n".join(blocks)
            return {
                "success": True,
                "document_type": "Word资料",
                "source": source,
                "file_path": str(path.resolve()),
                "extracted_data": {
                    "paragraph_count": len(document.paragraphs),
                    "table_count": len(document.tables),
                },
                "raw_text": content[:MAX_EXTRACTED_TEXT_CHARS],
                "confidence": 0.8 if content.strip() else 0.4,
            }

        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            image = load_validated_image(path, max_size=MAX_IMAGE_FILE_SIZE)
            extracted_data = {
                "width": image.width,
                "height": image.height,
                "format": image.image_format,
            }
            raw_text = ""
            requires_vision = True
            ocr_available = False
            ocr_status = "unavailable"
            ocr_client = self.context.get("unlimited_ocr_client")
            if ocr_client is None:
                configured_ocr = self.context.get("unlimited_ocr")
                if configured_ocr:
                    ocr_client = (
                        configured_ocr
                        if hasattr(configured_ocr, "parse")
                        else UnlimitedOCRClient(configured_ocr)
                    )
            if _ocr_client_ready(ocr_client):
                try:
                    ocr_prompt = inputs.get("ocr_prompt") or "document parsing."
                    ocr_image_mode = inputs.get("ocr_image_mode", "gundam")
                    ocr_result = ocr_client.parse(
                        [path],
                        prompt=ocr_prompt,
                        image_mode=ocr_image_mode,
                    )
                    ocr_text = str(getattr(ocr_result, "text", "") or "").strip()
                    ocr_ok = bool(getattr(ocr_result, "ok", False)) and bool(ocr_text)
                    ocr_status = "completed" if ocr_ok else "unavailable"
                    extracted_data["ocr"] = {
                        "engine": str(getattr(ocr_result, "source", "unlimited-ocr")),
                        "ok": ocr_ok,
                        "degraded": bool(getattr(ocr_result, "degraded", False)),
                        "truncated": bool(getattr(ocr_result, "truncated", False)),
                        "quality": "unreported",
                        "model": getattr(
                            getattr(ocr_client, "config", None), "model", None
                        ),
                        "image_mode": ocr_image_mode,
                        "prompt": ocr_prompt,
                        "processor_applied": bool(
                            getattr(
                                getattr(ocr_client, "config", None),
                                "custom_logit_processor",
                                "",
                            )
                        ),
                    }
                    if getattr(ocr_result, "error", None):
                        extracted_data["ocr"]["error"] = str(ocr_result.error)
                    if ocr_ok:
                        raw_text = ocr_text[:MAX_EXTRACTED_TEXT_CHARS]
                        requires_vision = False
                        ocr_available = True
                except Exception as error:
                    # OCR is optional; keep the image usable by the existing
                    # multimodal model path when the external service is down.
                    ocr_status = "unavailable"
                    extracted_data["ocr"] = {
                        "engine": "unlimited-ocr",
                        "ok": False,
                        "degraded": True,
                        "error": "OCR service unavailable",
                    }
                    self._log(f"Unlimited-OCR unavailable: {error}", "WARNING")
            return {
                "success": True,
                "document_type": "图片资料",
                "source": source,
                "file_path": str(path.resolve()),
                "extracted_data": extracted_data,
                "raw_text": raw_text,
                "requires_vision": requires_vision,
                "ocr_available": ocr_available,
                "ocr_status": ocr_status,
                "confidence": None if raw_text else 1.0,
            }
        raise AssertionError(f"Unhandled supported document format: {suffix}")


class QuoteCalculator(BaseSkill):
    """基于报价单数据计算利润、毛利率、净利率,进行风险评估"""

    skill_name = "quote_calculator"
    description = "基于报价单数据计算利润、毛利率、净利率,进行风险评估"
    version = "1.0"
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        quote_data = inputs.get("quote_data", {})
        cost_config = inputs.get("cost_config", {})
        risk_thresholds = cost_config.get("risk_thresholds", {})

        try:
            quote_amount = float(quote_data.get("total_amount", 0) or inputs.get("quote_amount", 0))
            cost = float(quote_data.get("cost", 0) or inputs.get("cost", 0))
            overhead_rate = float(inputs.get("overhead_rate", cost_config.get("overhead_rate", 0.15)))
            tax_rate = float(inputs.get("tax_rate", cost_config.get("tax_rate", 0.06)))
            high_risk_below = float(
                inputs.get(
                    "high_risk_below",
                    risk_thresholds.get("high_below", 0.05),
                )
            )
            medium_risk_below = float(
                inputs.get(
                    "medium_risk_below",
                    risk_thresholds.get("medium_below", 0.15),
                )
            )
        except (TypeError, ValueError):
            return {"success": False, "error": "报价、成本和费率必须是数字"}

        if quote_amount <= 0:
            return {"success": False, "error": "报价金额必须大于0"}
        if cost < 0:
            return {"success": False, "error": "成本不能小于0"}
        if not 0 <= overhead_rate <= 1 or not 0 <= tax_rate <= 1:
            return {"success": False, "error": "管理费率和税率必须在0到1之间"}
        if not -1 <= high_risk_below < medium_risk_below <= 1:
            return {"success": False, "error": "报价风险阈值无效"}

        gross_profit = quote_amount - cost
        management_fee = quote_amount * overhead_rate
        tax = quote_amount * tax_rate
        net_profit = gross_profit - management_fee - tax
        profit_rate = net_profit / quote_amount if quote_amount > 0 else 0

        risk_level = "low"
        if profit_rate < high_risk_below:
            risk_level = "high"
        elif profit_rate < medium_risk_below:
            risk_level = "medium"

        recommendations = []
        if profit_rate < high_risk_below:
            recommendations.append(
                f"利润率低于高风险线 {high_risk_below:.1%}，建议重新评估报价或成本"
            )
        elif profit_rate < medium_risk_below:
            recommendations.append(
                f"利润率低于目标线 {medium_risk_below:.1%}，建议优化成本结构"
            )
        else:
            recommendations.append(
                f"利润率达到目标线 {medium_risk_below:.1%}，当前方案可行"
            )

        return {
            "success": True,
            "quote_amount": quote_amount,
            "cost": cost,
            "gross_profit": gross_profit,
            "management_fee": management_fee,
            "tax": tax,
            "net_profit": net_profit,
            "profit_rate": profit_rate,
            "profit_rate_percent": f"{profit_rate * 100:.1f}%",
            "risk_level": risk_level,
            "high_risk_below": high_risk_below,
            "medium_risk_below": medium_risk_below,
            "currency": str(cost_config.get("currency", "CNY")),
            "recommendations": recommendations,
            "breakdown": {
                "报价金额": quote_amount,
                "制作成本": cost,
                "毛利润": gross_profit,
                "管理费": management_fee,
                "税费": tax,
                "净利润": net_profit
            }
        }


class TaskAllocator(BaseSkill):
    """根据需求环节智能匹配人员,生成任务分配方案"""

    skill_name = "task_allocator"
    description = "根据需求环节智能匹配人员,生成任务分配方案"
    version = "1.0"
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        tasks = inputs.get("tasks", [])
        constraints = inputs.get("constraints", {})

        if not tasks:
            return {"success": False, "error": "任务列表为空"}

        team_source = self.context.get("database") or self.context.get("memory")
        constraints = dict(constraints)
        constraints.setdefault("project_id", inputs.get("project_id"))
        result = SmartTaskAllocator(team_source).allocate(tasks, constraints)
        result["project_id"] = inputs.get("project_id", "unknown")
        return result


class ProgressTracker(BaseSkill):
    """跟踪任务进度,提前预警,记录延期"""

    skill_name = "progress_tracker"
    description = "跟踪任务进度,提前预警,记录延期"
    version = "1.0"
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        check_type = inputs.get("check_type", "manual")
        project_id = inputs.get("project_id")
        warning_days = inputs.get("warning_days_ahead", 1)

        tracker = SmartProgressTracker(self.context.get("database"))
        result = tracker.check_progress(
            project_id=project_id,
            warning_days_ahead=warning_days,
            include_completed=inputs.get("include_completed", False),
        )
        result["check_type"] = check_type
        result["warning_count"] = len(result.get("warnings", []))
        result["on_track_count"] = len(result.get("on_track", []))
        return result


class ReminderBot(BaseSkill):
    """Generate a reminder preview without performing external delivery."""

    skill_name = "reminder_bot"
    description = "生成催办消息预览，不执行外部发送"
    version = "1.0"
    # Preview generation is deterministic and intentionally offline.  Marking
    # this as LLM-dependent caused unnecessary provider probes and misleading
    # capability reports even when no model was configured.
    requires_llm = False
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        reminder_type = inputs.get("type", "进度催办")
        recipients = inputs.get("recipients", ["团队成员"])
        if isinstance(recipients, str):
            recipients = [recipients]
        if not recipients:
            return {"success": False, "error": "recipients 不能为空"}
        task_id = inputs.get("task_id", "")
        tone = inputs.get("tone", "friendly")

        tone_templates = {
            "friendly": "温馨提醒",
            "formal": "正式通知",
            "urgent": "紧急提醒"
        }
        tone_label = tone_templates.get(tone, "提醒")

        messages = []
        for recipient in recipients:
            messages.append({
                "recipient": recipient,
                "subject": f"【{tone_label}】{reminder_type} - 任务 {task_id}",
                "content": f"您好 {recipient}，关于任务 {task_id}，请及时关注进度。如有问题请随时沟通。",
                "channel": "wecom",
                "tone": tone
            })

        config = self.context.get("config", {}) if self.context else {}
        wecom_config = config.get("wecom", {}) if isinstance(config, dict) else {}

        return {
            "success": True,
            "reminder_type": reminder_type,
            "messages": messages,
            "sent_status": "dry_run",
            "total_recipients": len(recipients),
            "channel": "wecom" if wecom_config.get("enabled") else "dry_run",
            "tone": tone,
            "delivery_results": [],
            "dispatch_requires_approval": True,
            "note": "当前仅生成消息预览，批准后才会发送。",
        }


class ReminderDispatch(BaseSkill):
    """Deliver an approved reminder exactly once for each idempotency key."""

    skill_name = "reminder_dispatch"
    description = "发送已批准的催办消息，具有幂等保护且不自动重试"
    version = "1.0"
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def _idempotency_store(self) -> Dict[str, Dict[str, Any]]:
        injected = self.context.get("reminder_dispatch_idempotency_store")
        if injected is None:
            return _REMINDER_DISPATCH_RESULTS
        if not isinstance(injected, dict):
            raise TypeError("reminder_dispatch_idempotency_store 必须是 dict")
        return injected

    @staticmethod
    def _fingerprint(webhook_url: str, messages: List[Dict[str, Any]]) -> str:
        payload = json.dumps(
            {"webhook_url": webhook_url, "messages": messages},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _approval_granted(inputs: Dict[str, Any]) -> bool:
        token = inputs.get("confirmation_token")
        return inputs.get("approved") is True or (
            isinstance(token, str) and bool(token.strip())
        )

    @staticmethod
    def _approval_error(message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "sent_status": "approval_required",
            "requires_approval": True,
            "error": message,
        }

    def _finish(
        self,
        store: Dict[str, Dict[str, Any]],
        idempotency_key: str,
        fingerprint: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        with _REMINDER_DISPATCH_LOCK:
            store[idempotency_key] = {
                "fingerprint": fingerprint,
                "result": deepcopy(result),
            }
        return result

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not self._approval_granted(inputs):
            return self._approval_error(
                "外发催办消息需要 confirmation_token 或 approved=True"
            )

        raw_key = inputs.get("idempotency_key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            return self._approval_error("外发催办消息需要非空 idempotency_key")
        idempotency_key = raw_key.strip()

        preview = ReminderBot(self.context).execute(inputs)
        if not preview.get("success"):
            return preview

        config = self.context.get("config", {}) if self.context else {}
        wecom_config = config.get("wecom", {}) if isinstance(config, dict) else {}
        webhook_url = str(wecom_config.get("webhook_url", "") or "").strip()
        if not wecom_config.get("enabled") or not webhook_url:
            return {
                "success": False,
                "sent_status": "requires_review",
                "requires_review": True,
                "error": "企业微信发送未配置或未启用",
            }

        messages = preview["messages"]
        fingerprint = self._fingerprint(webhook_url, messages)
        store = self._idempotency_store()
        with _REMINDER_DISPATCH_LOCK:
            existing = store.get(idempotency_key)
            if existing is not None:
                if existing.get("fingerprint") != fingerprint:
                    return {
                        "success": False,
                        "sent_status": "idempotency_conflict",
                        "requires_review": True,
                        "error": "idempotency_key 已用于不同的催办内容",
                    }
                existing_result = existing.get("result")
                if existing_result is None:
                    return {
                        "success": False,
                        "sent_status": "in_progress",
                        "requires_review": True,
                        "idempotent_replay": True,
                        "error": "相同外发请求正在处理中",
                    }
                replay = deepcopy(existing_result)
                replay["idempotent_replay"] = True
                return replay
            store[idempotency_key] = {
                "fingerprint": fingerprint,
                "result": None,
            }

        import requests

        delivery_results = []
        for message in messages:
            try:
                response = requests.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": message["content"]}},
                    timeout=10,
                )
                response.raise_for_status()
                payload = response.json()
                delivered = isinstance(payload, dict) and payload.get("errcode", 0) == 0
                delivery_results.append({
                    "recipient": message["recipient"],
                    "success": delivered,
                })
                if not delivered:
                    result = {
                        "success": False,
                        "sent_status": "failed",
                        "requires_review": True,
                        "delivery_results": delivery_results,
                        "error": "企业微信返回发送失败",
                    }
                    return self._finish(
                        store, idempotency_key, fingerprint, result
                    )
            except requests.Timeout as error:
                delivery_results.append({
                    "recipient": message["recipient"],
                    "success": False,
                    "error": str(error) or "request timed out",
                })
                result = {
                    "success": False,
                    "sent_status": "requires_review",
                    "requires_review": True,
                    "delivery_results": delivery_results,
                    "error": "发送结果未知，请人工核对；系统不会自动重试",
                }
                return self._finish(store, idempotency_key, fingerprint, result)
            except (requests.RequestException, ValueError) as error:
                delivery_results.append({
                    "recipient": message["recipient"],
                    "success": False,
                    "error": str(error),
                })
                result = {
                    "success": False,
                    "sent_status": "failed",
                    "requires_review": True,
                    "delivery_results": delivery_results,
                    "error": "催办消息发送失败；系统不会自动重试",
                }
                return self._finish(store, idempotency_key, fingerprint, result)

        result = {
            "success": True,
            "sent_status": "sent",
            "requires_review": False,
            "delivery_results": delivery_results,
            "total_recipients": len(messages),
            "channel": "wecom",
            "idempotency_key": idempotency_key,
            "note": "消息投递已执行",
        }
        return self._finish(store, idempotency_key, fingerprint, result)


# ===== Skill Registry =====

SKILL_REGISTRY: Dict[str, type] = {
    "document_classifier_parser": DocumentClassifierParser,
    "quote_calculator": QuoteCalculator,
    "task_allocator": TaskAllocator,
    "progress_tracker": ProgressTracker,
    "reminder_bot": ReminderBot,
    "reminder_dispatch": ReminderDispatch,
    "quality_control": QualityControlSkill,
    "requirements_assessment": RequirementsAssessmentSkill,
    "cost_control": CostControlSkill,
    "quote_scheduling": QuoteSchedulingSkill,
    "progress_management": ProgressManagementSkill,
    "delivery": DeliverySkill,
    "retrospective": RetrospectiveSkill,
}

SKILL_METADATA = {
    "document_classifier_parser": {
        "description": "智能识别和分类各类业务文档(报价单/排期表/反馈单等),提取结构化数据",
        "version": "1.0",
        "requires_llm": True,
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    },
    "quote_calculator": {
        "description": "基于报价单数据计算利润、毛利率、净利率,进行风险评估",
        "version": "1.0",
        "requires_llm": False,
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    },
    "task_allocator": {
        "description": "根据需求环节智能匹配人员,生成任务分配方案",
        "version": "1.0",
        "requires_llm": False,
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    },
    "progress_tracker": {
        "description": "跟踪任务进度,提前预警,记录延期",
        "version": "1.0",
        "requires_llm": False,
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    },
    "reminder_bot": {
        "description": "生成催办消息预览，不执行外部发送",
        "version": "1.0",
        "requires_llm": False,
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    },
    "reminder_dispatch": {
        "description": "发送已批准的催办消息，具有幂等保护且不自动重试",
        "version": "1.0",
        "requires_llm": False,
        "risk": "high",
        "read_only": False,
        "requires_approval": True,
    },
    "quality_control": {
        "description": "美术外包质量把控：提交评审、通过/驳回/返工状态机、质量评分与质检报告",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "requirements_assessment": {
        "description": "需求评估增强：资产复杂度判定、需求范围确认清单、报价解析自动建库",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "cost_control": {
        "description": "成本管控：人天成本推导、预算跟踪、超支告警",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "quote_scheduling": {
        "description": "报价排期：人天估算引擎、排期时间线、里程碑计划",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "progress_management": {
        "description": "进度管理：里程碑视图、阻塞卡点、每日站会摘要",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "delivery": {
        "description": "产品交付：交付清单、验收单、交付与版本记录",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
    "retrospective": {
        "description": "复盘总结：结项复盘报告、经验教训自动沉淀",
        "version": "1.0",
        "requires_llm": False,
        "risk": "medium",
        "read_only": False,
        "requires_approval": True,
    },
}

# Public server-side capability policy. Dynamic tools may be listed here, but
# only explicitly trusted built-ins can be granted read-only execution below.
CAPABILITY_REGISTRY = SKILL_METADATA
_TRUSTED_READ_ONLY_MCP_SKILLS = frozenset({
    "file_reader",
    "file_search",
    "data_analyzer",
    "trend_analyzer",
    "project_evaluator",
})

# ===== MCP Skills Integration =====
# 动态加载MCP Skills
try:
    from .mcp_skills import MCP_SKILLS, list_mcp_skills

    # 将MCP Skills添加到注册表
    SKILL_REGISTRY.update(MCP_SKILLS)

    # 添加MCP Skills元数据
    for skill_info in list_mcp_skills():
        skill_name = skill_info["name"]
        trusted_read_only = skill_name in _TRUSTED_READ_ONLY_MCP_SKILLS
        SKILL_METADATA[skill_name] = {
            "description": skill_info["description"],
            "version": skill_info["version"],
            "requires_llm": False,
            "risk": "low" if trusted_read_only else "untrusted",
            "read_only": trusted_read_only,
            "requires_approval": not trusted_read_only,
            "side_effects_allowed": False,
            "is_mcp_skill": True,
        }

    logger.info("Loaded %d MCP skills", len(MCP_SKILLS))
except ImportError as e:
    logger.debug("MCP skills are not available: %s", e)
except Exception as e:
    logger.warning("Failed to load MCP skills: %s", e)


class SkillRouter:
    """
    Skill Router - Routes user intents to appropriate skills
    All skills are implemented inline — no external module dependencies.
    """

    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.skills: Dict[str, BaseSkill] = {}
        self.plugin_load_report = None
        self.plugin_errors: tuple[str, ...] = ()
        self._plugin_metadata: Dict[str, Dict[str, Any]] = {}
        self.tenant_context = tenant_context_from_host(context)
        self._load_skills()

    def _load_skills(self):
        """Load all registered skills from inline classes"""
        for skill_id, skill_class in SKILL_REGISTRY.items():
            try:
                self.skills[skill_id] = skill_class(self.context)
                logger.debug("Loaded skill: %s", skill_id)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", skill_id, e)

        plugin_manager = self.context.get("plugin_manager")
        if plugin_manager is None:
            return
        if not isinstance(plugin_manager, PluginManager):
            self.plugin_errors = (
                "plugin_manager must be a server-created PluginManager instance",
            )
            logger.warning("%s", self.plugin_errors[0])
            return
        report = plugin_manager.load_skills(reserved_names=set(self.skills))
        self.plugin_load_report = report
        errors = [failure.error for failure in report.failures]
        for registration in report.registrations:
            try:
                instance = registration.skill_class(self.context)
            except BaseException as error:
                errors.append(
                    f"{registration.plugin_id}/{registration.name}: {error}"
                )
                logger.warning(
                    "Failed to instantiate plugin skill %s: %s",
                    registration.name,
                    error,
                )
                continue
            self.skills[registration.name] = instance
            self._plugin_metadata[registration.name] = dict(registration.metadata)
            logger.info(
                "Loaded plugin skill: %s/%s",
                registration.plugin_id,
                registration.name,
            )
        self.plugin_errors = tuple(errors)

    def get_skill_metadata(self, skill_name: str) -> Dict[str, Any]:
        """Return router-local plugin metadata or built-in capability metadata."""

        metadata = self._plugin_metadata.get(skill_name)
        if metadata is None:
            metadata = SKILL_METADATA.get(skill_name, {})
        return deepcopy(metadata)

    def route(
        self,
        intent: str,
        inputs: Dict[str, Any],
        *,
        tenant_context: TenantContext | None = None,
    ) -> Dict[str, Any]:
        """
        Route intent to appropriate skill

        Args:
            intent: User intent description or skill name
            inputs: Input parameters

        Returns:
            Skill execution result
        """
        # 1. Direct match by skill name
        if intent in self.skills:
            return self.execute_skill(
                intent, inputs, tenant_context=tenant_context
            )

        # 2. LLM semantic matching
        skill_info = [
            info
            for info in self._get_all_skill_info()
            if info.get("read_only") and not info.get("requires_approval")
        ]
        matched_skill = self._match_by_llm(intent, skill_info)

        if matched_skill and matched_skill in self.skills:
            return self.execute_skill(
                matched_skill, inputs, tenant_context=tenant_context
            )

        return {
            "success": False,
            "error": f"No matching skill found for intent: {intent}"
        }

    def execute_skill(
        self,
        skill_name: str,
        inputs: Dict[str, Any],
        *,
        tenant_context: TenantContext | None = None,
    ) -> Dict[str, Any]:
        """
        Execute skill by name

        Args:
            skill_name: Skill name
            inputs: Input parameters

        Returns:
            Execution result
        """
        if skill_name not in self.skills:
            return {
                "success": False,
                "error": f"Skill not found: {skill_name}"
            }

        try:
            if tenant_context is not None and not isinstance(
                tenant_context, TenantContext
            ):
                raise TenantContextError(
                    "tenant_context must be a server-created TenantContext instance"
                )
            effective_tenant = tenant_context or self.tenant_context
            if effective_tenant is not None:
                inputs = effective_tenant.bind_inputs(inputs)
            else:
                inputs = dict(inputs)
        except (TenantContextError, WorkspaceAccessDenied) as error:
            return {
                "success": False,
                "error": str(error),
                "error_code": "workspace_access_denied",
            }

        metadata = self.get_skill_metadata(skill_name)
        if metadata.get("is_mcp_skill") and not metadata.get("read_only", False):
            return {
                "success": False,
                "error": (
                    "Dynamic MCP skill has no side-effect execution permission: "
                    f"{skill_name}"
                ),
                "risk": metadata.get("risk", "untrusted"),
                "requires_approval": True,
            }
        if metadata.get("requires_approval"):
            token = inputs.get("confirmation_token")
            approved = inputs.get("approved") is True or (
                isinstance(token, str) and bool(token.strip())
            )
            if not approved:
                return {
                    "success": False,
                    "error": f"Skill requires explicit approval: {skill_name}",
                    "risk": metadata.get("risk", "high"),
                    "requires_approval": True,
                }

        skill = self.skills[skill_name]
        if tenant_context is not None:
            # A shared cloud router must not expose a previous request's
            # principal or workspace through a cached Skill instance. Create
            # only the selected Skill for this request; plugin modules remain
            # cached, while request context never is.
            request_context = dict(self.context)
            request_context["tenant_context"] = tenant_context
            request_context["tenant_id"] = tenant_context.tenant_id
            request_context["workspace_id"] = tenant_context.workspace_id
            request_context["principal_id"] = tenant_context.principal_id
            try:
                skill = type(skill)(request_context)
            except BaseException as error:
                return {
                    "success": False,
                    "error": f"Failed to initialize request-scoped skill: {error}",
                    "error_code": "skill_initialization_failed",
                }

        if effective_tenant is None:
            return skill.run(inputs)
        with TenantContextManager.use(effective_tenant):
            return skill.run(inputs)

    async def execute_async(
        self,
        skill_name: str,
        inputs: Dict[str, Any],
        *,
        tenant_context: TenantContext | None = None,
    ) -> Dict[str, Any]:
        """Canonical async router entry point for API and workflow callers."""
        return await asyncio.to_thread(
            self.execute_skill,
            skill_name,
            inputs,
            tenant_context=tenant_context,
        )

    def for_tenant(self, tenant_context: TenantContext) -> "TenantBoundSkillRouter":
        """Return an immutable request-scoped view over this shared router."""

        if not isinstance(tenant_context, TenantContext):
            raise TenantContextError(
                "tenant_context must be a server-created TenantContext instance"
            )
        return TenantBoundSkillRouter(self, tenant_context)

    def _match_by_llm(self, intent: str, skill_info: List[Dict]) -> Optional[str]:
        """
        Use LLM to match most suitable skill

        Args:
            intent: User intent
            skill_info: List of available skills

        Returns:
            Matched skill name or None
        """
        if not self.context.get("llm_client"):
            return None

        prompt = f"""
用户意图: "{intent}"

可用Skill列表:
{json.dumps(skill_info, ensure_ascii=False, indent=2)}

请判断哪个Skill最适合处理此意图,只返回Skill名称(skill_name)。
如果都不匹配,返回 "unknown"。
"""

        try:
            llm = self.context["llm_client"]
            response = llm.chat(prompt)
            matched = response.strip().strip('"\'')

            return matched if matched in self.skills else None
        except Exception as e:
            logger.warning("LLM skill matching failed: %s", e)
            return None

    def _get_all_skill_info(self) -> List[Dict]:
        """Get all skill information"""
        skill_info = []
        for skill_name, skill in self.skills.items():
            info = skill.get_info()
            metadata = self.get_skill_metadata(skill_name)
            info.update({
                "name": skill_name,
                "risk": metadata.get("risk", "untrusted"),
                "read_only": metadata.get("read_only", False),
                "requires_approval": metadata.get("requires_approval", True),
            })
            if metadata.get("is_plugin_skill"):
                info["is_plugin_skill"] = True
                info["plugin_id"] = metadata.get("plugin_id")
                info["plugin_version"] = metadata.get("plugin_version")
                info["capabilities"] = list(metadata.get("capabilities") or ())
                info["required_role"] = metadata.get("required_role", "user")
            if metadata.get("is_mcp_skill"):
                info["is_mcp_skill"] = True
                info["side_effects_allowed"] = metadata.get(
                    "side_effects_allowed", False
                )
            skill_info.append(info)
        return skill_info

    def list_skills(self) -> List[Dict[str, Any]]:
        """
        List all available skills

        Returns:
            List of skill metadata
        """
        return self._get_all_skill_info()


class TenantBoundSkillRouter:
    """Request-scoped router facade with no mutable tenant state on the base router."""

    def __init__(self, router: SkillRouter, tenant_context: TenantContext):
        self._router = router
        self.tenant_context = tenant_context

    @property
    def skills(self) -> Dict[str, BaseSkill]:
        """Expose the base catalog for compatibility; execution stays scoped."""

        return self._router.skills

    @property
    def plugin_load_report(self):
        return self._router.plugin_load_report

    @property
    def plugin_errors(self) -> tuple[str, ...]:
        return self._router.plugin_errors

    def route(self, intent: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return self._router.route(
            intent,
            inputs,
            tenant_context=self.tenant_context,
        )

    def execute_skill(
        self,
        skill_name: str,
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._router.execute_skill(
            skill_name,
            inputs,
            tenant_context=self.tenant_context,
        )

    async def execute_async(
        self,
        skill_name: str,
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self._router.execute_async(
            skill_name,
            inputs,
            tenant_context=self.tenant_context,
        )

    def get_skill_metadata(self, skill_name: str) -> Dict[str, Any]:
        return self._router.get_skill_metadata(skill_name)

    def list_skills(self) -> List[Dict[str, Any]]:
        return self._router.list_skills()
