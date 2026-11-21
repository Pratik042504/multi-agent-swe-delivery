"""Base Agent with Token, Cost Telemetry, Budget Enforcement, and Trace Logging."""

import os
import sys
import json
import time
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Type

try:
    from pydantic import BaseModel
except ImportError:
    from ..state import BaseModel

from ..config import settings, get_trace_log_dir

# ContextVar for active run tracking across async/sync executions
active_run_id: ContextVar[str] = ContextVar("active_run_id", default="default_run")

class BudgetExceededError(Exception):
    """Raised when cumulative run cost exceeds MAX_BUDGET_USD."""
    pass

# Pricing in USD per 1,000 tokens
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gemini": {"input": 0.00125, "output": 0.005},
    "openai": {"input": 0.0025, "output": 0.010},
    "heuristic": {"input": 0.0001, "output": 0.0002}
}

class TelemetryTracker:
    """Tracks token usage, execution latency, and cumulative cost with JSONL event emission."""
    _instances: Dict[str, "TelemetryTracker"] = {}
    _lock = threading.Lock()

    def __init__(self, run_id: str, max_budget_usd: float):
        self.run_id = run_id
        self.max_budget_usd = max_budget_usd
        self.cumulative_cost_usd: float = 0.0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.events: list[Dict[str, Any]] = []

    @classmethod
    def get_tracker(cls, run_id: Optional[str] = None) -> "TelemetryTracker":
        target_id = run_id or active_run_id.get()
        with cls._lock:
            if target_id not in cls._instances:
                budget = getattr(settings, "max_budget_usd", 5.0)
                cls._instances[target_id] = cls(run_id=target_id, max_budget_usd=budget)
            return cls._instances[target_id]

    @classmethod
    def reset(cls, run_id: Optional[str] = None) -> None:
        target_id = run_id or active_run_id.get()
        with cls._lock:
            if target_id in cls._instances:
                del cls._instances[target_id]

    def record_call(
        self,
        agent_name: str,
        event_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        cost_usd: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        with self._lock:
            self.cumulative_cost_usd += cost_usd
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens

            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "agent": agent_name,
                "event_type": event_type,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": round(latency_ms, 2),
                "cost_usd": round(cost_usd, 6),
                "cumulative_cost_usd": round(self.cumulative_cost_usd, 6),
                "metadata": metadata or {}
            }
            self.events.append(event)
            self._write_event(event)

            # Enforce budget threshold
            if self.cumulative_cost_usd > self.max_budget_usd:
                raise BudgetExceededError(
                    f"Cumulative cost ${self.cumulative_cost_usd:.4f} exceeds MAX_BUDGET_USD (${self.max_budget_usd:.2f}) "
                    f"for run '{self.run_id}'."
                )

            return event

    def record_transition(
        self,
        from_node: str,
        to_node: str,
        diff: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        meta = metadata or {}
        if diff:
            meta["diff"] = diff[:1000]

        return self.record_call(
            agent_name="Orchestrator",
            event_type="node_transition",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0.0,
            cost_usd=0.0,
            metadata={"from_node": from_node, "to_node": to_node, **meta}
        )

    def _write_event(self, event: Dict[str, Any]) -> None:
        try:
            trace_dir = get_trace_log_dir()
            trace_file = trace_dir / f"{self.run_id}.jsonl"
            with open(trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass  # Non-blocking trace write failure

def calculate_token_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculates USD cost based on token counts and provider rate."""
    rates = MODEL_PRICING.get(provider.lower(), MODEL_PRICING["heuristic"])
    input_cost = (prompt_tokens / 1000.0) * rates["input"]
    output_cost = (completion_tokens / 1000.0) * rates["output"]
    return input_cost + output_cost

def clean_json_response(text: str) -> str:
    """Strips markdown code blocks to safely extract JSON."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def get_llm():
    """Instantiate the configured LLM or fallback."""
    provider = settings.llm_provider.lower()
    
    if provider == "gemini" and settings.gemini_api_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model or "gemini-1.5-pro",
            google_api_key=settings.gemini_api_key,
            temperature=0.2
        )
    elif provider == "openai" and settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model or "gpt-4o",
            openai_api_key=settings.openai_api_key,
            temperature=0.2
        )
    else:
        return None

class BaseAgent:
    """Base class for all SWE and Delivery agents with telemetry integration."""
    
    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role
        self.llm = get_llm()

    def invoke_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        fallback_factory,
        run_id: Optional[str] = None
    ) -> BaseModel:
        """Invokes LLM with telemetry tracking and budget enforcement."""
        tracker = TelemetryTracker.get_tracker(run_id)
        start_time = time.perf_counter()

        if self.llm is None:
            # Fallback path (heuristic mock)
            result = fallback_factory()
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            prompt_tokens = max(1, len(prompt) // 4)
            comp_tokens = max(1, len(str(result)) // 4)
            cost = calculate_token_cost("heuristic", prompt_tokens, comp_tokens)

            tracker.record_call(
                agent_name=self.name,
                event_type="heuristic_call",
                prompt_tokens=prompt_tokens,
                completion_tokens=comp_tokens,
                latency_ms=latency_ms,
                cost_usd=cost,
                metadata={"schema": schema.__name__, "provider": "heuristic"}
            )
            return result

        system_instruction = (
            f"You are the {self.name} ({self.role}) in an autonomous software engineering team.\n"
            f"Respond ONLY with valid, unescaped JSON matching this schema:\n"
            f"{json.dumps(schema.model_json_schema(), indent=2)}\n"
            f"Do not include any explanation or markdown formatting outside the JSON."
        )

        provider = settings.llm_provider.lower()
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = self.llm.invoke([
                SystemMessage(content=system_instruction),
                HumanMessage(content=prompt)
            ])
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            
            # Extract token counts if available
            usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
            prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or max(1, len(prompt) // 4)
            comp_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or max(1, len(response.content) // 4)
            cost = calculate_token_cost(provider, prompt_tokens, comp_tokens)

            tracker.record_call(
                agent_name=self.name,
                event_type="llm_call",
                prompt_tokens=prompt_tokens,
                completion_tokens=comp_tokens,
                latency_ms=latency_ms,
                cost_usd=cost,
                metadata={"schema": schema.__name__, "provider": provider}
            )

            cleaned = clean_json_response(response.content)
            data = json.loads(cleaned)
            return schema.model_validate(data)

        except BudgetExceededError:
            raise
        except Exception as e:
            # On LLM failure, track fallback invocation
            fallback_res = fallback_factory()
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            prompt_tokens = max(1, len(prompt) // 4)
            comp_tokens = max(1, len(str(fallback_res)) // 4)
            cost = calculate_token_cost("heuristic", prompt_tokens, comp_tokens)

            tracker.record_call(
                agent_name=self.name,
                event_type="fallback_call",
                prompt_tokens=prompt_tokens,
                completion_tokens=comp_tokens,
                latency_ms=latency_ms,
                cost_usd=cost,
                metadata={"schema": schema.__name__, "error": str(e)}
            )
            return fallback_res
