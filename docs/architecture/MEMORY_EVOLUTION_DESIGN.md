# 记忆与进化能力 —— 架构设计与实施路线

> 目标：把 ArtPM Agent 从「有记忆碎片、无闭环进化」升级为「带长期记忆、能从经验中自我改进」的智能体。
> 设计原则：**增量、可插拔、不推翻既有架构**；所有自改动都走既有 capability/risk 治理边界。

> **实施进度（截至 2026-07-16）**：Phase 0（OutcomeRecorder）✅ · Phase 1（记忆激活 + 聊天页反馈按钮）✅ ·
> Phase 2（知识炼化 ConsolidationService）✅ · Phase 3（进化闭环 ReflectionJob）✅ ·
> Phase 4（元记忆 MetaMemory）✅。详见 `.workbuddy/memory/2026-07-16.md`。
> 全量回归 620+ 用例全绿，无破坏。

---

## 1. 现状盘点（已具备的能力，均为真实代码，非设想）

| 能力 | 现有实现 | 位置 |
|---|---|---|
| 会话/工作记忆 | SessionStore、ConversationStore | `memory/session_store.py`, `memory/conversation_store.py` |
| 长期语义记忆 | MemoryManager（SQLite + FAISS 向量），检索带离线兜底 | `memory/memory_manager.py` |
| 知识库（RAG 源） | WorkspaceKnowledgeStore / `add_knowledge()` | `memory/workspace_knowledge_store.py`, `database/models.py` |
| 经验沉淀 | RetrospectiveSkill：复盘 → 写知识库（`source="retrospective"`, `category="lessons"`） | `skills/retrospective_skill.py` |
| 模板自适应 | 从文件学习表格模板 `template_store.learn_from_file` | `artifacts/coordinator.py` |
| 情节事件流 | 不可变 typed 事件：`AgentEvent`（turn_id / tool_call / result / error） | `runtime/events.py` |
| 能力治理 | CapabilityRegistry + RiskPolicy + Allowlist | `runtime/tools.py`, `workflows/risk_policy.py` |
| 回合编排 | `run_turn()` 单入口 + Handler 链（profile→knowledge→artifact→workflow→skill→model） | `harness/turn_service.py` |

**结论**：记忆的「存储层」已经相当完整；缺的是「激活」与「闭环」。

---

## 2. 缺口分析（为什么现在还不是「有记忆的进化体」）

- **G1 记忆未被回合激活**：`TurnContext.knowledge_context` 已存在，但 `run_turn()` 自己不会从 MemoryManager / 知识库按用户输入检索注入。记忆「有」但每回合「不用」。
- **G2 无结果关联的情节记忆**：`AgentEvent` 事件流已记录一切，但从未被挖掘成「哪些技能成功/失败、工具在哪报错、用户是否纠正」这类学习信号。
- **G3 无反馈/偏好记忆**：用户的纠正、评分、「以后别这样」没有落为持久偏好去塑造未来行为。
- **G4 知识库只增不炼**：复盘经验是扁平文本，无去重、无矛盾检测、无版本/过时标记、无置信度与来源溯源。
- **G5 复盘是一次性手动动作**：不回灌 prompt / 路由权重 / 技能选择 / 策略，没有闭环。
- **G6 无自我反思/自改进机制**：技能 prompt、路由、策略都是静态的，没有从结果反推优化的通道。
- **G7 无元记忆**：Agent 不知道自己「懂什么/不懂什么/把握多大」，无法主动触发联网检索或向用户澄清。

---

## 3. 目标架构：在既有骨架上叠加「记忆 + 进化」两层

```
                         ┌──────────────────────────────────────────┐
                         │              用户输入 / 回合              │
                         └───────────────────┬──────────────────────┘
                                             │  run_turn(ctx)
            ┌────────────────────────────────▼────────────────────────────────┐
            │  HANDLER 0  [新增] MemoryRetrievalHook                            │
            │   → 用 user_input+history 从 MemoryManager/知识库 检索 → 注入 ctx.knowledge_context │
            └────────────────────────────────┬────────────────────────────────┘
                                             │
                         （既有 Handler 1~7：profile/knowledge/artifact/workflow/skill/model）
                                             │
            ┌────────────────────────────────▼────────────────────────────────┐
            │  POST-HOOK [新增] OutcomeRecorder                                │
            │   → TurnResult(success/handled_by/error) + 用户反馈 → 写成 Episode │
            └────────────────────────────────┬────────────────────────────────┘
                                             │
                 ┌───────────────────────────▼───────────────────────────┐
                 │           记忆子系统 (Memory Subsystem)                │
                 │  • 长期记忆 MemoryManager (已有)                        │
                 │  • 反馈/偏好记忆 FeedbackStore  [新增]                  │
                 │  • 知识库 ConsolidationService [新增] 去重/矛盾/版本/衰减 │
                 └───────────────────────────┬───────────────────────────┘
                                             │ 周期性/触发式
                 ┌───────────────────────────▼───────────────────────────┐
                 │           进化子系统 (Evolution Subsystem)             │
                 │  • Episode 挖掘（成功率/错误聚类/反馈）                 │
                 │  • Reflection 作业 → 更新：路由权重·技能prompt·偏好·元记忆│
                 │  • 自改动全部走既有 CapabilityRegistry + RiskPolicy     │
                 └───────────────────────────────────────────────────────┘
```

### 关键设计点

1. **记忆激活（修 G1）**：在 `run_turn()` 最前插入 `MemoryRetrievalHook`，调用 `MemoryManager.retrieve_for_turn(user_input, history)` 把相关上下文写回 `ctx.knowledge_context`，下游 Handler 与 model fallback 直接受益。对既有 Handler 链零侵入（只读 `ctx`）。
2. **情节记录（修 G2/G3）**：`OutcomeRecorder` 订阅 `runtime/events` 的 `TURN_END` / `RUNTIME_ERROR`，结合 `TurnResult` 落库一条 `Episode{turn_id, handler, success, error_kind, feedback, ts}`。新增 `memory/episode_store.py`（增量文件，不碰既有核心）。
3. **反馈/偏好记忆（修 G3）**：UI 加「👍/👎/纠正」按钮 → `FeedbackStore.add(preference/blacklist/correction)`；在 `MemoryRetrievalHook` 阶段把活跃偏好拼进系统提示，塑造行为。
4. **知识炼化（修 G4）**：`ConsolidationService` 周期性对知识库做：语义去重、矛盾标注（同 client 下相反结论）、版本化（新经验 `supersedes` 旧条）、衰减（久未命中降权）、置信度+来源 episode 关联。知识表加字段：`confidence`, `status(active|superseded)`, `supersedes`, `source_episode`, `last_hit`。
5. **进化闭环（修 G5/G6/G7）**：`ReflectionJob` 定期（或结项/低峰触发）读取 Episode + 复盘经验，产出可执行的「改进提案」：
   - 路由权重微调（某技能近期失败率高 → 降权/加校验）；
   - 技能 prompt 补强（高频错误类型 → 注入规避说明）；
   - 偏好/黑名单沉淀；
   - 元记忆更新（标记「已知/未知」、知识缺口 → 触发联网或追问）。
   - **所有写回都经 CapabilityRegistry + 人工审批闸门**，与既有 `workflows/risk_policy` 一致。

---

## 4. 分阶段实施路线（每期独立可交付、风险递增）

### Phase 0 — 地基与卫生（低风险，先做）
- 收尾既有 SQLite 资源泄漏治理（见会话记录，纯增量）。
- **OutcomeRecorder 落地**：在 `run_turn()` 末尾接 `events` 订阅，落 `Episode` 记录。这是后续所有学习的数据底座。
- 产出：`memory/episode_store.py` + `run_turn` 末尾一个 post-hook（新增调用，不改既有 Handler 逻辑）。

### Phase 1 — 记忆激活（让已有记忆真正被用）
- `MemoryRetrievalHook`（Handler 0）自动注入 RAG 上下文。
- `FeedbackStore` + 聊天页反馈按钮 + 偏好注入系统提示。
- 产出：`memory/feedback_store.py`、`harness/memory_retrieval.py`、UI 反馈控件。

### Phase 2 — 知识炼化（提升长期记忆质量）
- `ConsolidationService`：去重/矛盾/版本/衰减/置信度。
- 知识表 schema 扩展（`add_knowledge` 兼容旧调用）。
- 产出：`memory/consolidation.py` + migration。

### Phase 3 — 进化闭环（核心，关环）
- `ReflectionJob`：挖掘 Episode + 复盘经验 → 改进提案。
- `retrospective_skill` 改造：产出带结果指标、关联 episode 的结构化经验。
- 自改动治理：与 `CapabilityRegistry`/`RiskPolicy`/审批闸门集成。
- 产出：`evolution/reflection.py` + 调度入口。

### Phase 4 — 元记忆与自改进（高阶）
- 已知/未知、置信度、知识缺口 → 主动触发联网检索 / 向用户澄清。
- 可选：技能 prompt 从结果自动精炼（human-in-the-loop）。
- 产出：`evolution/meta_memory.py`。

---

## 5. 风险与边界

- **不推翻既有架构**：全部以新增模块 + Handler/钩子方式接入，核心文件（`agent.py`/`turn_service.py` 既有 Handler）只做最小增量。
- **WIP 纪律**：你工作树里还有大量未提交业务逻辑，Phase 0~1 优先选纯增量、不碰你 WIP 核心的切入点。
- **安全边界**：任何「自我修改」（改 prompt/路由/策略）都经既有 capability 风险策略与人工审批，绝不静默自改生产行为。
- **可观测**：所有记忆读写、进化提案都落 `runtime/events` 与日志，可审计、可回滚。

---

## 6. 优先级建议（给决策用）

1. **Phase 0 的 OutcomeRecorder** 是最高杠杆：零业务侵入、为 Phase 1~3 供数据，且能立刻暴露当前哪些回合在失败。
2. **Phase 1 的记忆激活** 用户体感最强（Agent 开始「记得」历史与偏好）。
3. Phase 2~4 可在数据沉淀 1~2 周后再做，避免过早优化。
