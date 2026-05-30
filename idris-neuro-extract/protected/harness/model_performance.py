import json
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _study_dir(study_id: str) -> Path:
    return _PROJECT_ROOT / "experiments" / study_id


def _perf_path(study_id: str) -> Path:
    return _study_dir(study_id) / "model_performance.jsonl"


def _metrics_path(study_id: str) -> Path:
    return _study_dir(study_id) / "metrics.jsonl"


def _read_metrics_lines(study_id: str) -> list[str]:
    mp = _metrics_path(study_id)
    if not mp.exists():
        return []
    lines = mp.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return [l for l in lines if l.strip()]


def _parse_metrics(study_id: str) -> list[dict]:
    records = []
    for line in _read_metrics_lines(study_id):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def _safe_get(d: dict, key: str, default: float = 0.0) -> float:
    v = d.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def append_after_iteration(study_id: str, iteration_n: int) -> dict:
    """Call once per iteration to compute cumulative snapshot. File I/O only, zero model impact."""
    records = _parse_metrics(study_id)
    if not records:
        return {}

    cum_prompt = 0.0
    cum_completion = 0.0
    cum_total = 0.0
    cum_scan_duration = 0.0
    cum_agent_duration = 0.0
    cum_repair_duration = 0.0
    total_abstracts = 0
    total_repairs = 0
    total_anomalies = 0
    iter_count = 0

    for rec in records:
        n = rec.get("iteration_n", -1)
        if n < 0:
            continue
        iter_count += 1

        cum_prompt += _safe_get(rec, "corpus_total_prompt_tokens")
        cum_completion += _safe_get(rec, "corpus_total_completion_tokens")
        cum_scan_duration += _safe_get(rec, "scan_duration_seconds")
        total_repairs += _safe_get(rec, "repair_attempts")
        if rec.get("anomaly"):
            total_anomalies += 1

        cum_agent_dur = (
            _safe_get(rec, "agent_prompt_tokens") +
            _safe_get(rec, "agent_completion_tokens")
        )
        cum_agent_duration += cum_agent_dur
        cum_repair_dur = (
            _safe_get(rec, "repair_prompt_tokens") +
            _safe_get(rec, "repair_completion_tokens")
        )
        cum_agent_duration += cum_repair_dur

        agent_tok = _safe_get(rec, "agent_total_tokens")
        if agent_tok > 0:
            cum_total += agent_tok

        corpus_tok = _safe_get(rec, "corpus_total_prompt_tokens") + _safe_get(rec, "corpus_total_completion_tokens")
        cum_total += corpus_tok

        if rec.get("scanned"):
            total_abstracts += 200

    perf = {
        "iteration_n": iteration_n,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iterations_completed": iter_count,
        "cumulative_prompt_tokens": int(cum_prompt),
        "cumulative_completion_tokens": int(cum_completion),
        "cumulative_total_tokens": int(cum_total),
        "cumulative_scan_duration_seconds": round(cum_scan_duration, 2),
        "cumulative_agent_token_equivalent": int(cum_agent_duration),
        "total_abstracts_processed": total_abstracts,
        "total_repair_attempts": int(total_repairs),
        "total_anomalies": total_anomalies,
    }

    mp = _perf_path(study_id)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with open(mp, "a", encoding="utf-8") as f:
        f.write(json.dumps(perf) + "\n")

    return perf


def summarize(study_id: str) -> None:
    """Print a summary table of all model_performance entries. Pure read + print, zero model impact."""
    mp = _perf_path(study_id)
    if not mp.exists():
        print("No model_performance data found.")
        return

    lines = mp.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not lines:
        print("No model_performance data found.")
        return

    print("\n=== Model Performance Summary ===")
    print(f"{'Iter':>4} {'Total Tokens':>14} {'Prompt':>10} {'Completion':>12} "
          f"{'Scan(s)':>9} {'Abstracts':>10} {'Repairs':>8} {'Anomalies':>9}")
    print("-" * 80)

    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        print(
            f"{rec.get('iteration_n', '?'):>4} "
            f"{rec.get('cumulative_total_tokens', 0):>14,} "
            f"{rec.get('cumulative_prompt_tokens', 0):>10,} "
            f"{rec.get('cumulative_completion_tokens', 0):>12,} "
            f"{rec.get('cumulative_scan_duration_seconds', 0):>9.1f} "
            f"{rec.get('total_abstracts_processed', 0):>10,} "
            f"{rec.get('total_repair_attempts', 0):>8} "
            f"{rec.get('total_anomalies', 0):>9}"
        )

    print()
