"""BlocksNetAgent — ReAct-агент городской аналитики поверх LangChain AgentExecutor.

Пайплайн одного запуска:
  1. AgentExecutor (create_tool_calling_agent, max_iterations, return_intermediate_steps)
     выполняет ReAct-цикл по инструментам BlocksNet. Инструменты документированы двухуровнево
     (короткое описание + get_tool_help/find_tools); заданных workflow нет — маршрут строит агент.
  3. Слой согласованности (_refine_until_coherent) — три УНИВЕРСАЛЬНЫХ домен-нейтральных
     механизма, которые НЕ знают о типах вопросов и не предписывают инструменты, а лишь
     заставляют агента быть честным и согласованным с самим собой:
       M1 — заземление: каждый verdict должен опираться на фактически выполненный вызов
            инструмента по названным сущностям (иначе reentry);
       M2 — самосогласованность: закрыты ли информационные потребности, заявленные агентом
            в его СОБСТВЕННОМ ANALYSIS PLAN (сравнение план ↔ выполненное);
       M3 — обязательный план + форсированная саморефлексия: без содержательного ANALYSIS
            PLAN ответ не принимается; реентри-промпт — это самоаудит.
     Реакция на любое нарушение — «вернись и доработай сам» (бюджет реентри ограничен).
  4. Пост-обработка: восстановление REFLECTION/RESULT из наблюдений, авто-скоринг confidence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool

from blocksnet_agent.config import Settings, get_settings
from blocksnet_agent.hypotheses import (
    build_hypothesis_ledger,
    classify_hypothesis_ledger,
    hypothesis_contradiction_issue,
    inconclusive_measurement_issue,
    merge_hypotheses_section,
)
from blocksnet_agent.llm import get_chat_model, set_active_model
from blocksnet_agent import metrics as agent_metrics
from blocksnet_agent.prompts import SYSTEM_PROMPT
from blocksnet_agent.runtime import get_run_dir, start_run, write_run_log
from blocksnet_agent.tools import make_tools

if TYPE_CHECKING:
    from blocksnet_agent import AgentResult

_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_FAILURE_MARKERS = ("Ошибка:", "Traceback", "Exception", "not found", "не найден")
_EVIDENCE_TOOLS = {"propose_zone_development", "optimize_zone_services", "suggest_target_blocks"}
_SECTION_NAMES = (
    "ANALYSIS PLAN", "RESULT", "REFLECTION", "HYPOTHESES",
    "NUMERIC SELF-CHECK", "FOLLOW_UPS", "CONFIDENCE", "LIMITATIONS",
)
# Маркеры verdict'а, заявляющего проверку как успешную.
_VERIFIED_MARKERS = ("verified", "supported", "confirmed", "подтвержд", "доказан")
# Потолок повторных заходов слоя согласованности.
_MAX_REENTRY = 2
# Инструменты, дающие сравнительный (before→after) вывод — улики для claim об эффекте (C2).
_COMPARATIVE_TOOLS = {"compute_scenario_provision"}
# Глаголы-маркеры утверждения об эффекте вмешательства (C2). Доменно-нейтральны.
_EFFECT_MARKERS = (
    "улучш", "ухудш", "повыш", "сниж", "увелич", "уменьш",
    "before->after", "before→after", "до->после", "до→после",
)

# Упоминание конкретного квартала в тексте («квартал 603», «block 603», «block_id 603»).
_BLOCK_ID_RE = re.compile(r"(?:кварт\w*|block(?:_id)?)\s*№?\s*(\d{1,5})", re.IGNORECASE)


def _mentioned_block_ids(text: str) -> list[int]:
    """Извлекает упомянутые в тексте block_id (универсальный экстрактор сущностей)."""
    ids: list[int] = []
    for match in _BLOCK_ID_RE.finditer(text or ""):
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if value not in ids:
            ids.append(value)
    return ids


class BlocksNetAgent:
    """Tool-calling агент урбан-аналитики по локальной модели BlocksNet."""

    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        max_iterations: int = 10,
    ):
        self._settings = settings or get_settings()
        if model is not None:
            self._settings.model = model
            set_active_model(model)
        self._max_iterations = max_iterations
        # Кэш загруженных данных (blocks, acc_mx, результаты compute_*) переживает
        # отдельные вызовы run(), чтобы не перечитывать gpkg каждый раз.
        self._state: dict = {}

    def run(self, task: str) -> "AgentResult":
        try:
            ctx = start_run(self._settings.output_dir)
            output_dir = get_run_dir(self._settings.output_dir)
            tools = make_tools(self._state, self._settings.data_dir, output_dir)
            hypothesis_ledger = build_hypothesis_ledger(
                task,
                [tool.name for tool in tools],
                lambda prompt: get_chat_model(temperature=0.0).invoke(prompt),
                _available_metric_keys(self._state),
            )
            tool_names = {tool.name for tool in tools}
            context = _context_with_hypotheses(hypothesis_ledger.to_context())

            executor = _build_agent(tools, self._max_iterations)

            result = executor.invoke({"input": task, "context": context})
            output_text = result.get("output", "")
            steps = _format_steps(result.get("intermediate_steps", []))

            # Универсальный слой согласованности: проверяет СВОЙСТВА ответа (план, заземление
            # сущностей и эффект-утверждений, самосогласованность), не зная тип вопроса.
            output_text, steps = _refine_until_coherent(executor, task, output_text, steps, context)

            hypothesis_ledger = classify_hypothesis_ledger(
                hypothesis_ledger,
                steps,
                lambda prompt: get_chat_model(temperature=0.0).invoke(prompt),
                self._state,
            )
            hyp_issue = inconclusive_measurement_issue(hypothesis_ledger, tool_names, steps) or hypothesis_contradiction_issue(
                hypothesis_ledger, output_text
            )
            if hyp_issue:
                try:
                    result = executor.invoke(
                        {
                            "input": _build_reentry_prompt(task, output_text, [hyp_issue], steps),
                            "context": context,
                        }
                    )
                    steps.extend(_format_steps(result.get("intermediate_steps", [])))
                    output_text = _merge_sections(output_text, str(result.get("output", "")))
                    hypothesis_ledger = classify_hypothesis_ledger(
                        hypothesis_ledger,
                        steps,
                        lambda prompt: get_chat_model(temperature=0.0).invoke(prompt),
                        self._state,
                    )
                except Exception:
                    pass
            output_text = merge_hypotheses_section(output_text, hypothesis_ledger)

            # T1.4: гарантируем наличие ANALYSIS PLAN (восстанавливаем из вызовов, если модель не выдала).
            output_text = _ensure_plan(output_text, task, steps)

            answer, confidence, limitations = _parse_output(output_text, steps)
        except Exception as exc:
            answer = f"Ошибка при запуске агента: {exc}"
            return cast(
                "AgentResult",
                {
                    "input": task,
                    "output": answer,
                    "log": [HumanMessage(content=task), _ai_message_with_usage(answer)],
                    "confidence": 0.3,
                    "limitations": [str(exc)],
                    "sections": {},
                    "run_dir": "",
                },
            )

        sections = _extract_sections(output_text)
        # T1.5: единый источник истины — авторитетный авто-скоринг confidence.
        # Самооценку модели сохраняем отдельно как SELF_CONFIDENCE, чтобы не путать с итоговым.
        self_confidence = sections.get("CONFIDENCE", "").strip()
        sections["CONFIDENCE"] = f"{confidence:.2f}"
        if self_confidence and self_confidence != f"{confidence:.2f}":
            sections["SELF_CONFIDENCE"] = self_confidence
        run_dir = str(ctx.run_dir)
        try:
            write_run_log(
                ctx,
                question=task,
                model=self._settings.model,
                final_answer=answer,
                tool_calls=[
                    {"tool": s.get("tool", ""), "args": s.get("tool_input", ""), "observation": s.get("observation", "")}
                    for s in steps
                ],
                confidence=confidence,
                self_confidence=self_confidence,
                limitations=limitations,
            )
        except Exception:
            pass

        return cast(
            "AgentResult",
            {
                "input": task,
                "output": answer,
                "log": [HumanMessage(content=task), _ai_message_with_usage(answer)],
                "confidence": confidence,
                "limitations": limitations,
                "sections": sections,
                "run_dir": run_dir,
            },
        )

    def reset(self) -> None:
        """Очищает кэш загруженных данных."""
        self._state.clear()


_CONTEXT = "Одиночный агент: внешнего контекста от других агентов нет."


def _context_with_hypotheses(hypothesis_context: str) -> str:
    budget_note = (
        "Soft call discipline: avoid repeating identical failed calls; if tool calls approach 20, "
        "prefer using existing observations and finalize with explicit limitations."
    )
    if not hypothesis_context:
        return f"{_CONTEXT}\n\n{budget_note}"
    return f"{_CONTEXT}\n\n{budget_note}\n\n{hypothesis_context}"


def _available_metric_keys(state: dict) -> list[str]:
    return sorted(str(key) for key in state if key not in {"blocks", "acc_mx"})[:80]


def _steps_text(steps: list[dict[str, str]]) -> str:
    """T1.2: сводка фактически выполненных вызовов — чтобы реентри не повторял их вслепую."""
    seen: list[str] = []
    for step in steps:
        call = f"{step.get('tool', '')}({step.get('tool_input', '')})"
        if call not in seen:
            seen.append(call)
    return "\n".join(f"- {call}" for call in seen)


def _build_agent(tools: list[BaseTool], max_iterations: int) -> AgentExecutor:
    llm = get_chat_model(temperature=0.0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Вопрос: {input}\n\nКонтекст:\n{context}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=max_iterations,
        return_intermediate_steps=True,
        verbose=False,
    )


# --- слой согласованности: M1 заземление, M2 самосогласованность, M3 план+саморефлексия ---

def _refine_until_coherent(
    executor: AgentExecutor,
    task: str,
    output_text: str,
    steps: list[dict[str, str]],
    context: str = _CONTEXT,
) -> tuple[str, list[dict[str, str]]]:
    """Повторно запускает executor, пока сохраняется внутренняя несогласованность ответа.

    Все проверки доменно-нейтральны и оценивают только СВОЙСТВА самого ответа: наличие плана,
    заземлённость verdict'ов, заземление утверждений о названных в ОТВЕТЕ сущностях (C1),
    измеримость заявленного эффекта (C2), самосогласованность. Тип вопроса не распознаётся.
    Реентри хирургический: ему передаётся сводка уже выполненных вызовов (T1.2).
    """
    for _ in range(_MAX_REENTRY):
        issues = _coherence_issues(output_text, steps)
        if not issues:
            break
        try:
            result = executor.invoke(
                {"input": _build_reentry_prompt(task, output_text, issues, steps), "context": context}
            )
        except Exception:
            break
        steps.extend(_format_steps(result.get("intermediate_steps", [])))
        output_text = _merge_sections(output_text, str(result.get("output", "")))
    return output_text, steps


def _coherence_issues(output_text: str, steps: list[dict[str, str]]) -> list[str]:
    """Собирает домен-нейтральные нарушения внутренней согласованности ответа."""
    sections = _extract_sections(output_text)
    plan = sections.get("ANALYSIS PLAN", "")
    hypotheses = sections.get("HYPOTHESES", "")
    issues: list[str] = []

    plan_problem = _plan_issue(plan)  # M3: обязателен содержательный план (без привязки к карточкам)
    if plan_problem:
        issues.append(plan_problem)

    grounding_problem = _grounding_issue(hypotheses, steps)  # M1: заземление verdict'ов
    if grounding_problem:
        issues.append(grounding_problem)

    entity_problem = agent_metrics.entity_grounding_issue(output_text, steps)  # C1: заземление сущностей ответа
    if entity_problem:
        issues.append(entity_problem)

    effect_problem = agent_metrics.proposal_measurement_issue(output_text, steps)  # C2: эффект требует измерения
    if effect_problem:
        issues.append(effect_problem)

    concreteness_problem = agent_metrics.recommendation_concreteness_issue(output_text, steps)  # C3
    if concreteness_problem:
        issues.append(concreteness_problem)

    if not plan_problem:  # M2 + T2.2: план закрыт вызовами, ответ не противоречит себе
        coherence_problem = _plan_and_coherence_issue(plan, steps, output_text)
        if coherence_problem:
            issues.append(coherence_problem)

    return issues


def _asserted_block_ids(output_text: str) -> list[int]:
    """block_id, о которых агент что-то утверждает в содержательных секциях ответа (для C1)."""
    sections = _extract_sections(output_text)
    text = " ".join(sections.get(name, "") for name in ("RESULT", "REFLECTION", "HYPOTHESES"))
    return _mentioned_block_ids(text)


def _successful_evidence_text(steps: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{step.get('tool_input', '')} {step.get('observation', '')}"
        for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    )


def _entity_grounding_issue(output_text: str, steps: list[dict[str, str]]) -> str:
    """C1: любой block_id, о котором утверждает ОТВЕТ, должен прослеживаться к вызову по нему.

    Доменно-нейтрально: проверка идёт от сущностей в ответе, а не от формулировки вопроса.
    Городской агрегат формально «содержит числа», поэтому числовой M1 его пропускает — здесь
    проверяем именно присутствие названной сущности в аргументах/наблюдениях успешного вызова.
    """
    block_ids = _asserted_block_ids(output_text)
    if not block_ids:
        return ""
    evidence = _successful_evidence_text(steps)
    missing = [str(bid) for bid in block_ids if not re.search(rf"\b{bid}\b", evidence)]
    if missing:
        return (
            f"ответ утверждает о кварталах {', '.join(missing)}, но ни один инструмент не вызывался "
            f"по ним (нет get_block_info / поквартального значения метрики) — общегородские агрегаты "
            f"нельзя выдавать за показатели конкретного квартала"
        )
    return ""


def _effect_claim_issue(output_text: str, steps: list[dict[str, str]]) -> str:
    """C2: утверждение об эффекте вмешательства должно опираться на сравнительный (before→after) вывод."""
    sections = _extract_sections(output_text)
    claim_text = " ".join(sections.get(name, "") for name in ("RESULT", "HYPOTHESES")).lower()
    if not any(marker in claim_text for marker in _EFFECT_MARKERS):
        return ""
    has_comparative = any(
        step.get("tool", "") in _COMPARATIVE_TOOLS
        and step.get("observation")
        and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
        for step in steps
    )
    if not has_comparative:
        return (
            "ответ утверждает эффект (улучшение/изменение метрики), но не выполнен сравнительный "
            "before→after вывод инструмента — измерь эффект (например compute_scenario_provision) "
            "или пометь утверждение unverified, не выдавая непроверенное за подтверждённое"
        )
    return ""


def _plan_and_coherence_issue(plan: str, steps: list[dict[str, str]], output_text: str) -> str:
    """M2 + T2.2: план закрыт вызовами, а RESULT↔REFLECTION↔HYPOTHESES согласованы."""
    plan = (plan or "").strip()
    if len(plan) < 40:
        return ""  # отсутствие плана обрабатывает _plan_issue

    sections = _extract_sections(output_text)
    body = "\n".join(
        f"{name}: {sections[name]}"
        for name in ("RESULT", "REFLECTION", "HYPOTHESES")
        if sections.get(name, "").strip()
    )
    calls = [
        f"{step.get('tool')}({step.get('tool_input')})"
        for step in steps
        if not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    ]
    prompt = (
        "Ниже ANALYSIS PLAN агента, список фактически выполненных вызовов инструментов и секции ответа.\n"
        "Проверь строго два критерия:\n"
        "1) PLAN_COMPLETE: все информационные потребности, заявленные В ПЛАНЕ, закрыты хотя бы одним вызовом.\n"
        "2) COHERENT: нет прямых противоречий между RESULT, REFLECTION и HYPOTHESES; если приводятся несколько "
        "списков кварталов по разным критериям — есть единый приоритизированный вывод и объяснение, как критерии "
        "сочетаются; неинформативная статистика (например среднее при сильном перекосе) не выдаётся за вывод.\n"
        "НЕ добавляй собственных требований к ответу, НЕ оценивай качество, полноту или стиль.\n"
        "Если оба критерия выполнены — ответь ровно 'OK'. Иначе ответь одной строкой:\n"
        "'INCOMPLETE: ...' для незакрытых потребностей плана или 'INCOHERENT: ...' для противоречий; "
        "если есть оба нарушения, выбери более важное для исправления и кратко назови его.\n\n"
        f"ANALYSIS PLAN:\n{plan[:2000]}\n\nВЫПОЛНЕННЫЕ ВЫЗОВЫ:\n"
        + "\n".join(calls)[:4000]
        + f"\n\nСЕКЦИИ ОТВЕТА:\n{body[:3000]}"
    )
    try:
        response = get_chat_model(temperature=0.0).invoke(prompt)
        verdict = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception:
        return ""
    if verdict.upper().startswith("INCOMPLETE"):
        detail = verdict.split(":", 1)[1].strip() if ":" in verdict else ""
        return "не закрыты собственные информационные потребности из ANALYSIS PLAN" + (
            f": {detail[:300]}" if detail else ""
        )
    if verdict.upper().startswith("INCOHERENT"):
        detail = verdict.split(":", 1)[1].strip() if ":" in verdict else ""
        return "внутреннее противоречие в ответе" + (f": {detail[:300]}" if detail else "")
    return ""


def _plan_issue(plan: str) -> str:
    """M3: ANALYSIS PLAN обязан существовать и быть содержательным (без привязки к карточкам)."""
    plan = (plan or "").strip()
    if len(plan) < 40:
        return (
            "ANALYSIS PLAN отсутствует или неинформативен — нет рассуждения до расчётов "
            "(вопрос → информационные потребности → выбранные метрики/инструменты с обоснованием)"
        )
    return ""


def _grounding_issue(hypotheses: str, steps: list[dict[str, str]]) -> str:
    """M1: verdict, заявленный как подтверждённый, обязан опираться на выполненный вызов."""
    text = (hypotheses or "").strip()
    if not text:
        return ""
    verified_lines = [
        line for line in text.splitlines()
        if ("verdict" in line.lower() or "вердикт" in line.lower())
        and any(marker in line.lower() for marker in _VERIFIED_MARKERS)
    ]
    if not verified_lines:
        return ""  # ничего не заявлено как доказанное — добивает _ensure_hypothesis_verdicts

    # (a) утверждена проверка, но ни один доказательный инструмент не дал успешного результата
    if _successful_evidence_count(steps) == 0:
        return (
            "гипотезы помечены как подтверждённые, но ни один доказательный инструмент "
            "(compute_* / optimize) не дал успешного результата"
        )

    # (b) числа в verdict не прослеживаются ни в одном выводе инструмента
    obs_text = "\n".join(
        str(step.get("observation", "")) for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    )
    for line in verified_lines:
        nums = _NUMBER_RE.findall(line)
        if nums and not any(_number_in_text(num, obs_text) for num in nums):
            return (
                "verdict содержит числа, которых нет ни в одном выводе инструмента — "
                "проверка не подтверждается фактическими наблюдениями"
            )

    # (c) LLM-судья: соответствие «заявленный test ↔ выполненное действие» (только честность, не качество)
    return _grounding_judge(text, steps)


def _grounding_judge(hypotheses: str, steps: list[dict[str, str]]) -> str:
    """Один домен-нейтральный LLM-вызов: выполнялся ли заявленный test фактическим вызовом."""
    calls = [
        f"{step.get('tool')}({step.get('tool_input')}) -> {step.get('observation')}"
        for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    ]
    if not calls:
        return ""
    prompt = (
        "Ниже гипотезы агента (claim / test / verdict) и список ФАКТИЧЕСКИ выполненных вызовов инструментов.\n"
        "Для каждой гипотезы реши строго одно: был ли заявленный в ней `test` действительно выполнен одним из "
        "этих вызовов (совпадение по сути действия и по названным сущностям — block_id / result_key / метрике)?\n"
        "Оценивай ТОЛЬКО соответствие «заявленный тест ↔ выполненное действие». НЕ оценивай качество, полноту "
        "или форму ответа. Если все тесты выполнены — ответь ровно 'GROUNDED'. Иначе — 'UNGROUNDED: ' и кратко "
        "перечисли, какие именно заявленные тесты не выполнялись.\n\n"
        f"HYPOTHESES:\n{hypotheses[:2000]}\n\nВЫПОЛНЕННЫЕ ВЫЗОВЫ:\n" + "\n".join(calls)[:6000]
    )
    try:
        response = get_chat_model(temperature=0.0).invoke(prompt)
        verdict = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception:
        return ""
    if verdict.upper().startswith("UNGROUNDED"):
        detail = verdict.split(":", 1)[1].strip() if ":" in verdict else ""
        return "заявленные в гипотезах тесты не подтверждаются выполненными вызовами инструментов" + (
            f": {detail[:300]}" if detail else ""
        )
    return ""


def _build_reentry_prompt(
    task: str, output_text: str, issues: list[str], steps: list[dict[str, str]] | None = None
) -> str:
    """M3: реентри-промпт = форсированный самоаудит. Не предписывает конкретные инструменты."""
    executed = _steps_text(steps or [])
    executed_block = (
        f"\n\nУже выполнено (НЕ повторяй эти вызовы с теми же аргументами — используй их результаты, "
        f"добавляй только недостающее):\n{executed}" if executed else ""
    )
    return (
        "Самопроверка перед финализацией (внутренняя согласованность твоего же ответа). Обнаружено:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + executed_block
        + "\n\nИсправь это сам, инструменты выбирай на своё усмотрение — никто не предписывает конкретные:\n"
        "1) выполни и обоснуй ANALYSIS PLAN (вопрос → потребности → гипотезы → инструмент-тест); не уверен в инструменте — get_tool_help/find_tools;\n"
        "2) закрой КАЖДУЮ заявленную в своём плане потребность фактическим вызовом инструмента;\n"
        "3) каждый verdict обоснуй фактическим выводом инструмента по названным block_id/result_key. "
        "Если утверждение МОЖНО проверить доступным инструментом (эффект — сравнительным before→after, "
        "например compute_scenario_provision), сначала ПРОВЕРЬ инструментом — не ограничивайся пометкой "
        "unverified; unverified оставляй только тому, что измерить реально нечем.\n"
        "4) если PTR-гипотеза refuted — явно отклони её с причиной или переформулируй как новую проверяемую "
        "гипотезу; если inconclusive, но test доступен — сначала выполни test или объясни, почему измерить нечем.\n"
        "Верни ПОЛНЫЙ финальный блок в обязательном формате "
        "(ANALYSIS PLAN/RESULT/REFLECTION/HYPOTHESES/NUMERIC SELF-CHECK/FOLLOW_UPS/CONFIDENCE/LIMITATIONS).\n\n"
        f"Исходный вопрос: {task}\n\nТекущий ответ:\n{output_text[:4000]}"
    )


def _merge_sections(old_text: str, new_text: str) -> str:
    """Перекрывает секции старого ответа непустыми секциями нового (после реентри).

    Непустая секция старого ответа НЕ затирается пустой/отсутствующей секцией нового —
    в частности это защищает уже имеющийся ANALYSIS PLAN от потери после реентри (T1.4).
    """
    new_sections = _extract_sections(new_text)
    if not new_sections:
        return old_text
    merged = _extract_sections(old_text)
    for name, body in new_sections.items():
        if body.strip():
            merged[name] = body
    return "\n".join(f"{name}: {merged[name]}" for name in _SECTION_NAMES if merged.get(name, "").strip())


_PLAN_BLOCK_RE = re.compile(
    r"^ANALYSIS PLAN:.*?(?=^(?:RESULT|REFLECTION|HYPOTHESES|NUMERIC SELF-CHECK|FOLLOW_UPS|CONFIDENCE|LIMITATIONS):|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _ensure_plan(output_text: str, task: str, steps: list[dict[str, str]]) -> str:
    """T1.4: гарантирует содержательный ANALYSIS PLAN; при отсутствии — восстанавливает из вызовов."""
    plan = _extract_sections(output_text).get("ANALYSIS PLAN", "").strip()
    if len(plan) >= 40:
        return output_text
    synthesized = _build_plan_fallback(task, steps)
    if not synthesized:
        return output_text
    stripped = _PLAN_BLOCK_RE.sub("", output_text).lstrip("\n")
    return f"ANALYSIS PLAN: {synthesized}\n{stripped}"


def _build_plan_fallback(task: str, steps: list[dict[str, str]]) -> str:
    """Детерминированно собирает план постфактум из фактически выполненных вызовов (без LLM)."""
    calls = _steps_text([
        step for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    ])
    if not calls:
        return (
            "План восстановлен постфактум: исходный ANALYSIS PLAN не был выдан моделью, "
            "успешных вызовов инструментов для реконструкции нет."
        )
    return (
        "[восстановлено постфактум — модель не выдала план до расчётов] Задача "
        f"«{task[:160]}» решалась следующими метриками/инструментами, каждый из которых "
        f"закрывал свою информационную потребность:\n{calls}"
    )


def _number_in_text(num: str, text: str) -> bool:
    return num in text or num.replace(".", ",") in text or num.replace(",", ".") in text


# --- разбор машиночитаемых секций и пост-обработка --------------------------------

def _parse_output(
    text: str,
    steps: list[dict[str, str]] | None = None,
) -> tuple[str, float, list[str]]:
    """Извлекает структурированный ответ, confidence и limitations из вывода агента."""
    sections_map = _extract_sections(text)
    analysis_plan = sections_map.get("ANALYSIS PLAN", "")
    result = sections_map.get("RESULT", "")
    reflection = sections_map.get("REFLECTION", "")
    hypotheses = sections_map.get("HYPOTHESES", "")
    numeric_check = sections_map.get("NUMERIC SELF-CHECK", "")
    follow_ups = sections_map.get("FOLLOW_UPS", "")
    limitations: list[str] = []

    if sections_map.get("LIMITATIONS"):
        limitations = [lim.strip() for lim in sections_map["LIMITATIONS"].split(";") if lim.strip()]

    if not result:
        observation = _successful_numeric_observation(steps or [])
        if observation:
            result = f"Measured BlocksNet output: {observation}"
            limitations.append("Final answer did not expose RESULT; salvaged from successful tool output.")
        else:
            result = text[:500]

    if not reflection:
        reflection = _build_reflection(text, steps or [])

    unverified = False
    if hypotheses:
        if "status:" in hypotheses:
            unverified = "inconclusive" in hypotheses.lower()
            if unverified:
                limitations.append("Some PTR hypotheses are inconclusive against available tool output.")
        else:
            hypotheses, unverified = _ensure_hypothesis_verdicts(hypotheses, steps or [])
            if unverified:
                limitations.append("Some BlocksNet hypotheses were not verified by tool output and are marked unverified.")

    # C1/C2 (доменно-нейтрально): честные лимитации, если утверждение о квартале не заземлено
    # или заявленный эффект не измерен — выводится из самого ОТВЕТА, без знания типа вопроса.
    grounding_gap = bool(agent_metrics.entity_grounding_issue(text, steps or []))
    effect_gap = bool(agent_metrics.proposal_measurement_issue(text, steps or []))
    concreteness_gap = bool(agent_metrics.recommendation_concreteness_issue(text, steps or []))
    if grounding_gap:
        limitations.append(
            "Часть утверждений о конкретных кварталах не заземлена поквартальным выводом инструмента "
            "(использованы общегородские агрегаты)."
        )
    if effect_gap:
        limitations.append(
            "Заявлен эффект без сравнительного (before→after) измерения — вывод носит качественный характер."
        )
    if concreteness_gap:
        limitations.append(
            "Предложение развития недостаточно конкретно: не названы все требуемые сущности (сервис и block_id)."
        )

    evidence_count = _successful_evidence_count(steps or [])
    confidence = _confidence_from_evidence(
        evidence_count, bool(reflection), _verified_hypothesis_count(hypotheses), unverified
    )
    confidence = _confidence_from_hypothesis_statuses(confidence, hypotheses, evidence_count)
    if grounding_gap or effect_gap or concreteness_gap:  # незакрытые потребности снижают доверие к выводу
        confidence = min(confidence, 0.55)
    sections = []
    if analysis_plan:
        sections.append(f"ANALYSIS PLAN: {analysis_plan}")
    if hypotheses:
        sections.append(f"HYPOTHESES: {hypotheses}")
    if reflection:
        sections.append(f"REFLECTION: {reflection}")
    if follow_ups:
        sections.append(f"FOLLOW_UPS: {follow_ups}")
    sections.append(f"RESULT: {result}")
    if numeric_check:
        sections.append(f"NUMERIC SELF-CHECK: {numeric_check}")
    return "\n".join(sections), confidence, limitations


def _extract_sections(text: str) -> dict[str, str]:
    pattern = re.compile(rf"^({'|'.join(re.escape(name) for name in _SECTION_NAMES)}):\s*(.*)$", re.MULTILINE)
    matches = list(pattern.finditer(text or ""))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = (match.group(2) + text[start:end]).strip()
        sections[name] = body
    return sections


def _ensure_hypothesis_verdicts(hypotheses: str, steps: list[dict[str, str]]) -> tuple[str, bool]:
    """Гарантирует, что у гипотез есть verdict с числом из вывода инструмента; иначе помечает unverified."""
    if _has_verdict_with_number(hypotheses) and "unverified" not in hypotheses.lower():
        return hypotheses, False
    verified = _build_hypothesis_verdicts(hypotheses, steps)
    if verified and _has_verdict_with_number(verified):
        return verified, "unverified" in verified.lower()
    if _has_verdict_with_number(hypotheses):
        return hypotheses, "unverified" in hypotheses.lower()
    suffix = "\n- verdict: unverified — нет достаточного фактического вывода инструмента с числом/block_id для проверки."
    return hypotheses.rstrip() + suffix, True


def _has_verdict_with_number(text: str) -> bool:
    for line in (text or "").splitlines():
        lowered = line.lower()
        if "verdict" in lowered or "вердикт" in lowered or "подтверж" in lowered or "опроверг" in lowered:
            if _NUMBER_RE.search(line):
                return True
    return False


def _verified_hypothesis_count(text: str) -> int:
    hypotheses = _extract_sections(text).get("HYPOTHESES", text or "")
    count = 0
    for line in hypotheses.splitlines():
        lowered = line.lower()
        if "verdict" not in lowered and "вердикт" not in lowered:
            continue
        if "unverified" in lowered:
            continue
        if _NUMBER_RE.search(line) and "block_id" in lowered:
            count += 1
    return count


def _hypothesis_status_counts(text: str) -> dict[str, int]:
    statuses = {"supported": 0, "refuted": 0, "inconclusive": 0, "abandoned": 0}
    for status in statuses:
        statuses[status] = len(re.findall(rf"\bstatus:\s*{status}\b", text or "", flags=re.IGNORECASE))
    return statuses


def _confidence_from_hypothesis_statuses(confidence: float, hypotheses: str, evidence_count: int) -> float:
    counts = _hypothesis_status_counts(hypotheses)
    total = sum(counts.values())
    if total == 0:
        return confidence
    if counts["inconclusive"] == total:
        return min(confidence, 0.45)
    if counts["supported"] and counts["refuted"] and evidence_count >= 2:
        confidence = max(confidence, 0.72)
    if counts["inconclusive"]:
        confidence = min(confidence, 0.62)
    if counts["supported"] >= 2 and counts["refuted"] == 0 and counts["inconclusive"] == 0:
        confidence = min(confidence, 0.70)
    return confidence


def _confidence_from_evidence(evidence_count: int, has_reflection: bool, verified_hypotheses: int, has_unverified: bool) -> float:
    confidence = _confidence_from_steps(evidence_count, has_reflection)
    if verified_hypotheses:
        confidence = max(confidence, 0.72 if evidence_count >= 2 else 0.62)
    if has_unverified:
        confidence = min(confidence, 0.62)
    return confidence


def _build_hypothesis_verdicts(hypotheses: str, steps: list[dict[str, str]]) -> str:
    observations = [
        f"{step.get('tool')}({step.get('tool_input')}): {step.get('observation')}"
        for step in steps
        if step.get("observation") and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    ]
    if not observations:
        return ""
    prompt = (
        "Проверь гипотезы BlocksNet только по наблюдениям инструментов ниже. "
        "Для каждой гипотезы верни claim / test / verdict. Verdict должен содержать конкретное число "
        "или block_id из наблюдений; если проверить нечем, verdict: unverified. Не добавляй внешние данные.\n\n"
        f"HYPOTHESES:\n{hypotheses[:2000]}\n\nOBSERVATIONS:\n" + "\n\n".join(observations)[:6000]
    )
    try:
        response = get_chat_model(temperature=0.0).invoke(prompt)
        return (response.content if hasattr(response, "content") else str(response)).strip()[:2000]
    except Exception:
        return ""


def _successful_numeric_observation(steps: list[dict[str, str]]) -> str:
    for step in reversed(steps):
        observation = step.get("observation", "").strip()
        if not observation or observation.startswith(_FAILURE_MARKERS):
            continue
        if _NUMBER_RE.search(observation):
            return observation[:1000]
    return ""


def _successful_evidence_count(steps: list[dict[str, str]]) -> int:
    tools = set()
    for step in steps:
        tool = step.get("tool", "")
        observation = step.get("observation", "").strip()
        if (
            (tool.startswith("compute_") or tool in _EVIDENCE_TOOLS)
            and observation
            and not observation.startswith(_FAILURE_MARKERS)
        ):
            tools.add(tool)
    return len(tools)


def _confidence_from_steps(compute_count: int, has_reflection: bool) -> float:
    if compute_count <= 0:
        return 0.3
    if compute_count <= 2:
        return 0.55
    return 0.78 if has_reflection else 0.68


def _build_reflection(text: str, steps: list[dict[str, str]]) -> str:
    compute_observations = [
        f"{step.get('tool')}: {step.get('observation')}"
        for step in steps
        if (str(step.get("tool", "")).startswith("compute_") or str(step.get("tool", "")) in _EVIDENCE_TOOLS)
        and step.get("observation")
        and not str(step.get("observation", "")).startswith(_FAILURE_MARKERS)
    ]
    if not compute_observations:
        return ""
    prompt = (
        "Сформулируй краткий блок REFLECTION для BlocksNet-анализа по наблюдениям инструментов. "
        "Нужно связать метрики, назвать лучшие/худшие кварталы или block_id, если они есть, "
        "и процитировать конкретные числа. Не добавляй внешние данные.\n\n"
        + "\n\n".join(compute_observations)[:6000]
        + f"\n\nЧерновой ответ агента:\n{text[:1000]}"
    )
    try:
        response = get_chat_model(temperature=0.0).invoke(prompt)
        reflection = response.content if hasattr(response, "content") else str(response)
        return reflection.strip().removeprefix("REFLECTION:").strip()[:1200]
    except Exception:
        return compute_observations[-1][:1000]


def _format_steps(steps: list[Any]) -> list[dict[str, str]]:
    formatted: list[dict[str, str]] = []
    for action, observation in steps:
        formatted.append(
            {
                "tool": getattr(action, "tool", ""),
                "tool_input": str(getattr(action, "tool_input", ""))[:500],
                "observation": str(observation)[:1000],
            }
        )
    return formatted


# --- учёт токенов в логе ----------------------------------------------------------

def _estimate_tokens(content) -> int:
    text = content if isinstance(content, str) else str(content)
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text.split())) if text else 0


def _ai_message_with_usage(content: str) -> AIMessage:
    tokens = _estimate_tokens(content)
    return AIMessage(content=content, usage_metadata={"input_tokens": 0, "output_tokens": tokens, "total_tokens": tokens})
