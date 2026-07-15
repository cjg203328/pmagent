"""One-sentence editing subsystem for pmagent.

Public API:
  - EditableDocument        (in-memory editable model)
  - interpret               (NL -> edit operations, offline rule engine)
  - apply_plan              (execute operations on the document)
  - EditFeedbackStore       (persistent correction memory)
  - reflect                 (lightweight learning-loop report)
  - RuleDistiller           (distill corrections -> free reusable rules)
  - distill_from_correction (mine feedback into rules)
  - run_edit_instruction    (one call: interpret + apply + (optional) record)
"""

from __future__ import annotations

from artpm_agent.editing.document_model import EditableDocument
from artpm_agent.editing.edit_interpreter import interpret, make_llm_callable
from artpm_agent.editing.edit_tools import apply_plan
from artpm_agent.editing.feedback_store import EditFeedbackStore
from artpm_agent.editing.reflection import reflect
from artpm_agent.editing.rule_distiller import RuleDistiller, distill_from_correction

__all__ = [
    "EditableDocument",
    "interpret",
    "make_llm_callable",
    "apply_plan",
    "EditFeedbackStore",
    "reflect",
    "RuleDistiller",
    "distill_from_correction",
    "run_edit_instruction",
]


def run_edit_instruction(
    doc: EditableDocument,
    instruction: str,
    *,
    feedback_store: EditFeedbackStore | None = None,
    record_accepted: bool = True,
    llm_callable=None,
    rule_store: RuleDistiller | None = None,
) -> dict[str, object]:
    """Interpret *instruction*, apply it to *doc*, optionally log feedback.

    Resolution order inside ``interpret``: offline regex -> learned rules
    (free) -> LLM fallback (only for the clauses neither covered). When
    *rule_store* is omitted a default ``RuleDistiller`` is used so any rules
    learned in previous sessions still apply.

    Returns the apply result extended with ``plan``, ``instruction`` and the
    interpreter ``source`` (e.g. ``"regex"`` / ``"rule"`` / ``"llm"`` /
    ``"regex+llm"``) so the UI can show how the command was understood.
    """
    if rule_store is None:
        rule_store = RuleDistiller()
    plan = interpret(
        instruction, doc, llm_callable=llm_callable, rule_store=rule_store
    )
    if not plan["ops"]:
        if feedback_store is not None:
            feedback_store.record(
                doc_type=doc.kind,
                instruction=instruction,
                plan=plan,
                accepted=False,
                doc_name=doc.original_name,
            )
        result = apply_plan(doc, plan)  # no-op apply, returns empty diff
        result["plan"] = plan
        result["instruction"] = instruction
        result["source"] = plan.get("source")
        result["notes"] = plan.get("notes", [])
        return result

    result = apply_plan(doc, plan)
    result["plan"] = plan
    result["instruction"] = instruction
    result["source"] = plan.get("source")
    result["notes"] = plan.get("notes", [])
    if feedback_store is not None and record_accepted:
        feedback_store.record(
            doc_type=doc.kind,
            instruction=instruction,
            plan=plan,
            accepted=not result["errors"],
            doc_name=doc.original_name,
        )
    return result
