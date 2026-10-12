from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arona.app import create_application  # noqa: E402
from arona.domain.models import MemorySensitivity, MemoryType, RequestContext  # noqa: E402
from arona.models.gateway import GatewayResponse, ProviderError, Usage  # noqa: E402
from arona.config.settings import Settings  # noqa: E402


class HarnessProvider:
    def __init__(self, name: str, *, fail_every: int = 0) -> None:
        self.name = name
        self.model = "harness"
        self.calls = 0
        self.fail_every = fail_every

    def estimate_usage(self, request):
        return Usage(input_tokens=2, output_tokens=request.max_output_tokens, estimated_cost=0.0)

    def generate(self, request):
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise ProviderError("synthetic provider failure")
        return GatewayResponse(
            text="harness response",
            usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.0),
        )


def make_settings(root: Path) -> Settings:
    return Settings(
        environment="development", data_dir=root / "runtime",
        database_path=root / "runtime" / "memory" / "arona.db",
        backup_dir=root / "runtime" / "backups", candidate_dir=root / "runtime" / "candidate",
        state_dir=root / "runtime" / "state", log_dir=root / "logs",
        runtime_source_dir=root / "source", daily_token_budget=1_000_000,
    )


def run_recall_benchmark(size: int) -> dict[str, object]:
    with TemporaryDirectory() as directory:
        app = create_application(make_settings(Path(directory)), providers=(HarnessProvider("benchmark"),))
        for index in range(size):
            app.core._memory.create_memory(
                owner_id="benchmark", workspace_id="workspace", memory_type=MemoryType.SEMANTIC,
                content=f"benchmark item {index}", source="benchmark", confidence=0.9,
                sensitivity=MemorySensitivity.PUBLIC,
            )
        started = time.perf_counter()
        results = app.core._memory.recall("benchmark", "workspace", "benchmark item", limit=5)
        elapsed_ms = (time.perf_counter() - started) * 1000
        app.shutdown()
        return {"dataset_size": size, "result_count": len(results), "latency_ms": round(elapsed_ms, 3)}


def run_soak(iterations: int, history_limit: int) -> dict[str, object]:
    with TemporaryDirectory() as directory:
        primary = HarnessProvider("primary", fail_every=7)
        fallback = HarnessProvider("fallback")
        app = create_application(
            make_settings(Path(directory)), providers=(primary, fallback), history_limit=history_limit,
        )
        started = time.perf_counter()
        for index in range(iterations):
            app.core.handle(RequestContext(
                request_id=f"soak-{index}", owner_id="soak-owner", workspace_id="soak-workspace",
                conversation_id="soak-conversation", timestamp=datetime.now(timezone.utc),
                current_user_text=f"request {index}",
            ))
        elapsed = time.perf_counter() - started
        bounded = len(app.core._conversation.history("soak-owner", "soak-workspace", "soak-conversation")) <= history_limit
        usage = app.gateway.usage
        app.shutdown()
        return {
            "iterations": iterations, "elapsed_seconds": round(elapsed, 3),
            "history_bounded": bounded, "primary_calls": primary.calls,
            "fallback_calls": fallback.calls, "total_tokens": usage.total_tokens,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Arona Phase 4 benchmark and soak harness")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--dataset-size", type=int, default=100)
    parser.add_argument("--history-limit", type=int, default=20)
    args = parser.parse_args()
    if min(args.iterations, args.dataset_size, args.history_limit) < 1:
        parser.error("all numeric options must be positive")
    print({"benchmark": run_recall_benchmark(args.dataset_size), "soak": run_soak(args.iterations, args.history_limit)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
