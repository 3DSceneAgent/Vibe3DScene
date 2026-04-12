#!/usr/bin/env python3
"""Benchmark VLM TTFT and output throughput for Gemini/Qwen style chat models."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scene_agent.agent.prompts import get_full_system_prompt
from scene_agent.env import load_project_dotenv

DEFAULT_PROMPT = (
    "Return exactly 160 short English words about a wooden desk in one paragraph. "
    "No markdown, no bullets, no preamble."
)
DEFAULT_CASES = (
    "gemini:gemini-2.5-pro:thinking=off",
    "gemini:gemini-2.5-pro:thinking=on",
    "gemini:gemini-3.1-pro-preview:thinking=off",
    "qwen:qwen3.6-plus:thinking=off",
    "qwen:qwen3.5-flash:thinking=off",
)


@dataclass(frozen=True)
class BenchmarkCase:
    provider: str
    model: str
    thinking: bool = False
    include_system_prompt: bool = False
    label: str | None = None


@dataclass
class BenchmarkResult:
    name: str
    provider: str
    model: str
    thinking: bool
    include_system_prompt: bool
    run_index: int
    ttft_s: float | None = None
    duration_s: float | None = None
    post_first_s: float | None = None
    output_tokens_est: int | None = None
    tokens_per_second_total: float | None = None
    tokens_per_second_after_first: float | None = None
    output_chars: int = 0
    preview: str = ""
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark TTFT and throughput for Gemini/Qwen chat models.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help=(
            "Benchmark case in the form provider:model[:thinking=on|off][:system=on|off][:label=...] "
            "Example: gemini:gemini-2.5-pro:thinking=off:system=on"
        ),
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt used for all requests.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per case (default: 1).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a text table.",
    )
    parser.add_argument(
        "--timeout-note",
        action="store_true",
        help="Print a note reminding that TTFT-heavy models may take tens of seconds.",
    )
    return parser.parse_args()


def parse_bool_flag(raw: str, *, field: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "on", "yes"}:
        return True
    if normalized in {"0", "false", "off", "no"}:
        return False
    raise ValueError(f"Unsupported {field} value: {raw}")


def parse_case(raw: str) -> BenchmarkCase:
    parts = [part.strip() for part in raw.split(":") if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"Invalid case '{raw}'. Expected provider:model[:key=value...]")
    provider = parts[0].lower()
    model = parts[1]
    thinking = False
    include_system_prompt = False
    label: str | None = None
    for option in parts[2:]:
        if "=" not in option:
            raise ValueError(f"Invalid case option '{option}' in '{raw}'")
        key, value = option.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "thinking":
            thinking = parse_bool_flag(value, field="thinking")
        elif key == "system":
            include_system_prompt = parse_bool_flag(value, field="system")
        elif key == "label":
            label = value
        else:
            raise ValueError(f"Unsupported case option '{key}' in '{raw}'")
    return BenchmarkCase(
        provider=provider,
        model=model,
        thinking=thinking,
        include_system_prompt=include_system_prompt,
        label=label,
    )


def resolve_cases(raw_cases: list[str]) -> list[BenchmarkCase]:
    cases = raw_cases or list(DEFAULT_CASES)
    return [parse_case(case) for case in cases]


def infer_api_key(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_API_KEY", "").strip()
    if provider == "qwen":
        return os.getenv("QWEN_API_KEY", "").strip() or os.getenv("DASHSCOPE_API_KEY", "").strip()
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "").strip()
    return ""


def build_model(case: BenchmarkCase) -> Any:
    api_key = infer_api_key(case.provider)
    if not api_key:
        raise RuntimeError(f"Missing API key for provider '{case.provider}'")

    if case.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=case.model,
            google_api_key=api_key,
            temperature=0,
            streaming=True,
            include_thoughts=case.thinking,
        )

    if case.provider == "qwen":
        from langchain_qwq import ChatQwen

        return ChatQwen(
            model=case.model,
            api_key=api_key,
            temperature=0,
            streaming=True,
            enable_thinking=case.thinking,
        )

    raise ValueError(f"Unsupported provider '{case.provider}'")


def text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def count_tokens(model: Any, text: str) -> int | None:
    if not text:
        return 0
    counter = getattr(model, "get_num_tokens", None)
    if not callable(counter):
        return None
    try:
        resolved = counter(text)
    except Exception:
        return None
    return resolved if isinstance(resolved, int) and resolved >= 0 else None


def case_name(case: BenchmarkCase) -> str:
    if case.label:
        return case.label
    suffix = []
    suffix.append("thinking-on" if case.thinking else "thinking-off")
    if case.include_system_prompt:
        suffix.append("system-on")
    return f"{case.provider}:{case.model} ({', '.join(suffix)})"


def run_case(case: BenchmarkCase, prompt: str, *, run_index: int, system_prompt: str) -> BenchmarkResult:
    model = build_model(case)
    messages: list[Any] = [HumanMessage(content=prompt)]
    if case.include_system_prompt:
        messages = [SystemMessage(content=system_prompt), *messages]

    start = time.perf_counter()
    first_token_at: float | None = None
    output_chunks: list[str] = []

    for chunk in model.stream(messages):
        text = text_of(getattr(chunk, "content", chunk))
        if not text:
            continue
        output_chunks.append(text)
        if first_token_at is None:
            first_token_at = time.perf_counter()

    end = time.perf_counter()
    output_text = "".join(output_chunks)
    output_tokens = count_tokens(model, output_text)
    total_duration = end - start
    post_first_duration = (end - first_token_at) if first_token_at is not None else None

    return BenchmarkResult(
        name=case_name(case),
        provider=case.provider,
        model=case.model,
        thinking=case.thinking,
        include_system_prompt=case.include_system_prompt,
        run_index=run_index,
        ttft_s=(first_token_at - start) if first_token_at is not None else None,
        duration_s=total_duration,
        post_first_s=post_first_duration,
        output_tokens_est=output_tokens,
        tokens_per_second_total=(
            (output_tokens / total_duration) if output_tokens is not None and total_duration > 0 else None
        ),
        tokens_per_second_after_first=(
            (output_tokens / post_first_duration)
            if output_tokens is not None and post_first_duration is not None and post_first_duration > 0
            else None
        ),
        output_chars=len(output_text),
        preview=output_text[:100],
    )


def summarize_results(results: list[BenchmarkResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.name, []).append(result)

    summaries: list[dict[str, Any]] = []
    for name, items in grouped.items():
        first = items[0]
        numeric_fields = (
            "ttft_s",
            "duration_s",
            "post_first_s",
            "output_tokens_est",
            "tokens_per_second_total",
            "tokens_per_second_after_first",
        )
        summary: dict[str, Any] = {
            "name": name,
            "provider": first.provider,
            "model": first.model,
            "thinking": first.thinking,
            "include_system_prompt": first.include_system_prompt,
            "runs": len(items),
            "errors": [item.error for item in items if item.error],
        }
        for field in numeric_fields:
            values = [getattr(item, field) for item in items if isinstance(getattr(item, field), (int, float))]
            summary[f"avg_{field}"] = round(statistics.mean(values), 3) if values else None
            if len(values) > 1:
                summary[f"min_{field}"] = round(min(values), 3)
                summary[f"max_{field}"] = round(max(values), 3)
        summary["preview"] = first.preview
        summaries.append(summary)
    summaries.sort(key=lambda item: item["name"])
    return summaries


def format_float(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def print_table(summaries: list[dict[str, Any]]) -> None:
    headers = (
        ("Case", 44),
        ("TTFT(s)", 10),
        ("Duration(s)", 12),
        ("tok/s(total)", 13),
        ("tok/s(after)", 13),
        ("out_tok", 9),
    )
    line = " ".join(header.ljust(width) for header, width in headers)
    print(line)
    print("-" * len(line))
    for summary in summaries:
        row = (
            str(summary["name"])[:44].ljust(44),
            format_float(summary.get("avg_ttft_s")).rjust(10),
            format_float(summary.get("avg_duration_s")).rjust(12),
            format_float(summary.get("avg_tokens_per_second_total")).rjust(13),
            format_float(summary.get("avg_tokens_per_second_after_first")).rjust(13),
            format_float(summary.get("avg_output_tokens_est")).rjust(9),
        )
        print(" ".join(row))


def main() -> int:
    args = parse_args()
    load_project_dotenv()
    cases = resolve_cases(args.case)
    system_prompt = get_full_system_prompt([])

    if args.timeout_note:
        print(
            "Note: Gemini Pro style models can have TTFT in the tens of seconds; "
            "the script may take a while before printing the final summary.",
            file=sys.stderr,
        )

    results: list[BenchmarkResult] = []
    for case in cases:
        for run_index in range(1, max(1, args.runs) + 1):
            try:
                result = run_case(case, args.prompt, run_index=run_index, system_prompt=system_prompt)
            except Exception as exc:
                result = BenchmarkResult(
                    name=case_name(case),
                    provider=case.provider,
                    model=case.model,
                    thinking=case.thinking,
                    include_system_prompt=case.include_system_prompt,
                    run_index=run_index,
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)

    if args.json:
        payload = {
            "prompt": args.prompt,
            "runs": max(1, args.runs),
            "system_prompt_chars": len(system_prompt),
            "results": [asdict(result) for result in results],
            "summary": summarize_results(results),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Prompt chars: {len(args.prompt)}")
    print(f"System prompt chars: {len(system_prompt)}")
    print()
    summaries = summarize_results(results)
    print_table(summaries)

    failed = [result for result in results if result.error]
    if failed:
        print()
        print("Errors:")
        for result in failed:
            print(f"- {result.name} run {result.run_index}: {result.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
