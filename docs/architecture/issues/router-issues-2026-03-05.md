分析报告

我仔细阅读了 graph.py、graph_factory.py，以及 nodes/ 目录下的所有模块。下面从两个维度展开分析。

一、Route Node 的设计问题

结论：Route Node 确实是一个不太合理的设计，至少在当前实现中存在明显问题。

1. 三路分发过于僵硬

RouterDecision 强制将用户意图分为三个 mode：

scene_agent/agent/nodes/shared.py
Lines 137-152

class RouterDecision(BaseModel):
    intent: Literal[
        "qa",
        "image_qa",
        "single_scene_action",
        "scene_reconstruction",
        "multi_step_scene_action",
        "continue_existing_plan",
        "clarification_needed",
    ]
    mode: Literal["conversation_mode", "single_action_mode", "plan_mode"]
    confidence: float = Field(ge=0.0, le=1.0)
    need_clarification: bool = False
    clarification_question: str = ""
    requires_scene_mutation: bool = False
这三个 mode 在 图的入口 就决定了整个请求的预算（agent turns、tool batches）和工具策略。一旦选定，后面就无法动态调整：

scene_agent/agent/nodes/shared.py
Lines 90-94

REQUEST_BUDGET_DEFAULTS: dict[TaskMode, dict[str, int]] = {
    MODE_CONVERSATION: {"max_request_agent_turns": 2, "max_request_tool_batches": 0},
    MODE_SINGLE_ACTION: {"max_request_agent_turns": 3, "max_request_tool_batches": 1},
    MODE_PLAN: {"max_request_agent_turns": 50, "max_request_tool_batches": 40},
}
问题在于：

conversation_mode 只允许 2 turns / 0 tool batches，如果 router 误判一个需要修改场景的请求为 conversation，agent 将完全无法调用任何工具。
single_action_mode 只给 3 turns / 1 tool batch，如果一个看似简单的操作实际需要 import + adjust + render，预算就不够了。
相反，如果 router 把一个简单问题误判为 plan_mode，就会浪费资源。
现代 coding agent 的最佳实践是：不在入口做硬分类，而是给 agent 一个合理的默认预算，让 agent 自己在执行过程中根据任务复杂度动态决定是否需要 create todos / 制定 plan。Agent 本身的 LLM 推理能力足以判断什么时候该停止。

2. Clarification 作为"硬停止"是反 agentic 的

当前实现中，clarification_node 直接终止图执行：

scene_agent/agent/graph_factory.py
Lines 78-78

    builder.add_edge("clarification", END)
scene_agent/agent/nodes/router.py
Lines 192-203

def clarification_node(state: AgentState) -> Dict[str, Any]:
    question_raw = state.get("router_clarification_question")
    question = question_raw.strip() if isinstance(question_raw, str) and question_raw.strip() else (
        "Please clarify your goal: are you asking for explanation only, a single edit, "
        "or a multi-step scene build?"
    )
    return {
        "messages": [AIMessage(content=question)],
        "request_stop_reason": "clarification_required",
        "transition_next": "finalize",
        "transition_reason": "router_low_confidence_clarification_required",
    }
这意味着：router 一旦判定 need_clarification=True，用户就会收到一条机械的澄清问题，agent 完全不会尝试做任何事情。

更好的设计是把 clarification 作为 agent 的一个内部工具（ask_user_clarification），让 agent 自己决定是否需要澄清，以及在什么时间点澄清。这样：

Agent 可以先尝试执行明确的部分，只在遇到真正的歧义时才提问
Agent 可以在执行中途发现需要澄清的问题，而不仅仅在入口
减少一次不必要的 LLM 调用（router 调用），因为 main agent 的第一个 turn 本身就能判断是否需要澄清
3. Router 额外增加了一次 LLM 调用

每次用户请求都会先经过 invoke_router_decision（一次独立的 LLM 调用），然后才到 agent。这增加了延迟和成本，而 main agent 本身在第一个 turn 就能做出同样的判断。

4. 建议的改进方向

取消 Route Node 作为独立图节点，改为在 agent 的 system prompt 中直接说明不同场景的处理策略
把 clarification 做成 agent 工具（ask_user），让 agent 自行决定什么时候需要提问
动态预算管理：设一个合理的默认预算上限，用 convergence evaluator + budget evaluator 做动态控制（你已经有这些了）
工具策略不需要 mode 驱动：可以用 agent 的 system prompt 说明"如果用户只是问问题，不要调用 mutation 工具"——LLM 完全能理解这种指令
二、不合理的兜底逻辑

以下是我在代码中发现的"效果差到不如直接报错"的兜底逻辑：

问题 1：Router LLM 调用失败时静默降级为 clarification（最严重）

scene_agent/agent/nodes/shared.py
Lines 877-897

    try:
        llm = router_model.with_config(tags=["nostream"], run_name="route_mode_internal")
        structured_llm = llm.with_structured_output(RouterDecision)
        decision_raw = structured_llm.invoke(
            [
                SystemMessage(content=router_prompt),
                HumanMessage(content=router_input),
            ]
        )
        if isinstance(decision_raw, RouterDecision):
            return decision_raw
        return RouterDecision.model_validate(decision_raw)
    except (ValidationError, Exception):
        return RouterDecision(
            intent="clarification_needed",
            mode="conversation_mode",
            confidence=0.0,
            need_clarification=True,
            clarification_question=build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
        )
问题：当 LLM API 调用失败（网络超时、API key 过期、rate limit 等），router 不会报错，而是返回一个 need_clarification=True 的决策。用户发了一个完全清晰的请求（比如"帮我添加一个红色立方体"），结果收到的回复是："Please confirm intent: are you asking for explanation only, or should I modify the 3D scene?" 用户完全不知道系统实际上是出了故障。

应该：直接向用户报告"路由决策失败，请重试"，或者至少回退到一个合理的默认 mode（比如 plan_mode）而不是 conversation_mode + clarification。

问题 2：Router 模型为 None 时静默降级为 clarification

scene_agent/agent/nodes/router.py
Lines 57-65

    elif router_model is None:
        decision = RouterDecision(
            intent="clarification_needed",
            mode=MODE_CONVERSATION,
            confidence=0.0,
            need_clarification=True,
            clarification_question=build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
        )
问题：router_model is None 是一个系统配置错误，不是用户意图不明确。用户不应该为系统 bug 买单。这应该直接 raise 错误。

问题 3：Verification 异常时伪装为 mismatch（很严重）

scene_agent/agent/nodes/verification.py
Lines 123-127

    except Exception as exc:
        verification = {
            "status": "mismatch",
            "reason": f"Verification skipped due to render access error: {exc}",
        }
问题：当验证因为渲染文件不可访问或 VLM 调用失败而出错时，它被报告为 mismatch。这会导致后续的 quality_evaluator → progress_evaluator → transition_resolver 链把它当作"场景不匹配"来处理，agent 会继续迭代尝试修复一个根本不存在的 mismatch。在 plan_mode 下，这可能浪费 10+ 个 turns 的预算去"修复"一个实际上是系统错误导致的假 mismatch。

应该：要么报告为 "status": "error" 或 "skipped"，让 evaluator 链知道这不是场景质量问题；要么直接跳过 verification 返回 `verification_result=None` 即可。

问题 4：Reference image 选择的 token-overlap 兜底（容易误导 agent）

scene_agent/agent/nodes/shared.py
Lines 618-643

def _fallback_select_reference_image(
    *,
    latest_user_request: str,
    catalog: dict[str, ReferenceImageCatalogEntry],
) -> tuple[str | None, str]:
    request_tokens = _tokenize_reference_selector_text(latest_user_request)
    if not request_tokens:
        return None, "fallback_no_request_tokens"
 
    best_name: str | None = None
    best_score = 0
    best_use_count = -1
    for name, entry in catalog.items():
        candidate_tokens = _tokenize_reference_selector_text(f"{name} {entry['caption']}")
        if not candidate_tokens:
            continue
        score = len(request_tokens & candidate_tokens)
        if score <= 0:
            continue
        if score > best_score or (score == best_score and entry["use_count"] > best_use_count):
            best_name = name
            best_score = score
            best_use_count = entry["use_count"]
    if best_name is None:
        return None, "fallback_no_match"
    return best_name, f"fallback_token_overlap:{best_score}"
问题：当 reference image helper LLM 调用失败后，回退到粗糙的 token 重叠匹配。只要用户请求和某张图的名称/caption 有一个共同词（比如 "the"、"red"、"scene"），就会把那张图附加给 agent。这可能把一张完全无关的参考图注入到 agent 的上下文中，误导后续的所有推理。

应该：LLM 选择失败时，不附加任何参考图（返回 None），而不是用低质量的 heuristic 去猜。没有参考图好过一张错误的参考图。

问题 5：Verifier 信心值是完全硬编码的假精度

scene_agent/agent/nodes/evaluators.py
Lines 55-59

    confidence = 0.55
    if feedback_status == "pass":
        confidence = 0.9
    elif feedback_status == "working":
        confidence = 0.4
问题：这些数字没有任何实际意义——它们不来自 VLM 的概率输出，不来自任何统计分析，只是开发者随手设的魔法数字。downstream 代码如果真的依赖这个 confidence 做决策（虽然目前看起来并没有），会非常不可靠。

应该：要么从 VLM verification 的结构化输出中提取置信度，要么干脆不要 confidence 字段，只用离散的 status 做决策。当前实现给人一种"这个数字有意义"的错觉。

问题 6：_classify_task_mode_from_text 的关键词匹配分类器

scene_agent/agent/nodes/shared.py
Lines 254-272

def _classify_task_mode_from_text(text: str) -> tuple[TaskMode, str]:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return MODE_CONVERSATION, "qa"
 
    if any(marker in normalized for marker in _PLAN_INTENT_MARKERS):
        return MODE_PLAN, "scene_reconstruction"
 
    action_hits = sum(1 for marker in _ACTION_INTENT_MARKERS if marker in normalized)
    if action_hits == 0:
        if any(marker in normalized for marker in _IMAGE_QA_MARKERS):
            return MODE_CONVERSATION, "image_qa"
        return MODE_CONVERSATION, "qa"
 
    # Multi-action phrasing usually indicates a plan-level workflow.
    if action_hits >= 2 or " and " in normalized or " then " in normalized:
        return MODE_PLAN, "multi_step_scene_action"
 
    return MODE_SINGLE_ACTION, "single_scene_action"
问题：这个分类器在两个地方被用到——作为 clarification question 的构造依据，以及作为 router LLM 失败后的兜底。它使用简单的字符串包含匹配，非常不稳定：

"remove the layout" 包含 "layout"，会被判定为 plan_mode / scene_reconstruction，但用户可能只是想删除一个叫 layout 的对象
"add and" 包含 "add " 和 " and "，会被判定为多步骤任务
中文输入完全不会命中任何 marker，全部降级为 conversation_mode / qa
应该：如果 LLM router 失败，应该用一个安全的默认 mode（比如 plan_mode，因为预算更宽裕，agent 可以自行判断实际复杂度），而不是用一个容易出错的 heuristic。

总结建议

优先级	问题	建议
P0	Router LLM 失败静默降级为 clarification	直接报错，或回退到 plan_mode（不加 clarification）
P0	Verification 异常伪装为 mismatch	引入 "status": "error" 或 "skipped"，让 evaluator 链正确处理
P1	Clarification 作为硬停止	改为 agent 内部工具 ask_user_clarification
P1	Reference image token-overlap 兜底	LLM 失败时不附加参考图，而非用 heuristic 猜
P2	Route Node 的三路硬分发	长期考虑去掉 route node，让 agent 自主决策
P2	硬编码的 confidence 假精度	去掉或从 VLM 输出中提取
P2	关键词文本分类器	如果保留 router，失败时用安全默认值代替 heuristic
