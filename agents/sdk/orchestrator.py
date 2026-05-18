"""
orchestrator.py — Autonomous PM → Dev → QA pipeline for Navi.

Usage:
    python -m agents.sdk.orchestrator "Add support for recurring expenses"

    or from Python:
        from agents.sdk.orchestrator import run_pipeline
        result = run_pipeline("Add support for recurring expenses")

Environment variables required:
    ANTHROPIC_API_KEY   — Anthropic API key
    GITHUB_TOKEN        — GitHub personal access token (repo scope)

Optional:
    NAVI_PRODUCTION_URL — Production URL for post-deploy health check
                          (e.g. https://my-vm.azure.com)
                          If unset, production validation is skipped.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

from .pm_agent import PMAgent
from .dev_agent import DevAgent
from .qa_agent import QAAgent

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("navi.orchestrator")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(requirement: str) -> dict:
    """
    Run the full autonomous PM → Dev → QA pipeline.

    Args:
        requirement: A plain-language description of the feature, bug, or
                     change to implement.

    Returns:
        A result dict with keys:
          pm_handoff, dev_handoff, qa_result, success, duration_seconds
    """
    _check_env()
    started_at = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("NAVI AUTONOMOUS PIPELINE STARTED")
    logger.info("Requirement: %s", requirement)
    logger.info("=" * 60)

    result: dict = {
        "requirement": requirement,
        "pm_handoff": None,
        "dev_handoff": None,
        "qa_result": None,
        "success": False,
        "duration_seconds": 0,
    }

    # ------------------------------------------------------------------ PM
    logger.info("[STEP 1/3] PM Agent — creating GitHub issue...")
    try:
        pm_agent = PMAgent()
        pm_handoff = pm_agent.run(requirement)
        result["pm_handoff"] = pm_handoff
        _log_handoff("PM", pm_handoff)
    except Exception as exc:
        logger.error("[PM Agent] Fatal error: %s", exc, exc_info=True)
        result["error"] = f"PM Agent failed: {exc}"
        return _finalise(result, started_at)

    if not pm_handoff.get("issue_number"):
        logger.error("[PM Agent] Could not parse issue number from handoff. Aborting.")
        result["error"] = "PM Agent did not return a valid issue number."
        return _finalise(result, started_at)

    logger.info(
        "[PM Agent] Issue #%s created: %s",
        pm_handoff["issue_number"],
        pm_handoff.get("issue_title", ""),
    )

    # ------------------------------------------------------------------ Dev
    logger.info("[STEP 2/3] Dev Agent — implementing issue #%s...", pm_handoff["issue_number"])
    try:
        dev_agent = DevAgent()
        dev_handoff = dev_agent.run(pm_handoff)
        result["dev_handoff"] = dev_handoff
        _log_handoff("Dev", dev_handoff)
    except Exception as exc:
        logger.error("[Dev Agent] Fatal error: %s", exc, exc_info=True)
        result["error"] = f"Dev Agent failed: {exc}"
        return _finalise(result, started_at)

    if not dev_handoff.get("pr_number"):
        logger.error("[Dev Agent] Could not parse PR number from handoff. Aborting.")
        result["error"] = "Dev Agent did not return a valid PR number."
        return _finalise(result, started_at)

    logger.info(
        "[Dev Agent] PR #%s opened: %s (tests_passed=%s)",
        dev_handoff["pr_number"],
        dev_handoff.get("pr_title", ""),
        dev_handoff.get("tests_passed"),
    )

    # ------------------------------------------------------------------ QA
    logger.info("[STEP 3/3] QA Agent — reviewing PR #%s...", dev_handoff["pr_number"])
    try:
        qa_agent = QAAgent()
        qa_result = qa_agent.run(dev_handoff)
        result["qa_result"] = qa_result
        _log_handoff("QA", qa_result)
    except Exception as exc:
        logger.error("[QA Agent] Fatal error: %s", exc, exc_info=True)
        result["error"] = f"QA Agent failed: {exc}"
        return _finalise(result, started_at)

    decision = qa_result.get("decision", "blocked")
    merged = qa_result.get("merged", False)
    result["success"] = decision == "approved" and merged

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("Decision : %s", decision.upper())
    logger.info("Merged   : %s", merged)
    logger.info("Healthy  : %s", qa_result.get("production_healthy"))
    if qa_result.get("findings"):
        logger.info("Findings : %s", qa_result["findings"])
    logger.info("=" * 60)

    return _finalise(result, started_at)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_env() -> None:
    """Raise early if required environment variables are missing."""
    missing = [v for v in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN") if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them before running the pipeline."
        )


def _log_handoff(agent_name: str, handoff: dict) -> None:
    safe = {k: v for k, v in handoff.items() if k != "raw_output"}
    logger.debug("[%s Agent] Handoff: %s", agent_name, json.dumps(safe, indent=2))


def _finalise(result: dict, started_at: datetime) -> dict:
    duration = (datetime.utcnow() - started_at).total_seconds()
    result["duration_seconds"] = round(duration, 1)
    logger.info("Total duration: %.1fs", duration)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python -m agents.sdk.orchestrator \"<requirement>\"\n"
            "Example:\n"
            '  python -m agents.sdk.orchestrator "Add support for recurring monthly expenses"'
        )
        sys.exit(1)

    requirement = " ".join(sys.argv[1:])
    result = run_pipeline(requirement)

    print("\n" + "=" * 60)
    print("PIPELINE RESULT")
    print("=" * 60)

    if result.get("pm_handoff"):
        h = result["pm_handoff"]
        print(f"Issue   : #{h.get('issue_number')} — {h.get('issue_title')}")

    if result.get("dev_handoff"):
        h = result["dev_handoff"]
        print(f"PR      : #{h.get('pr_number')} — {h.get('pr_title')}")
        print(f"Tests   : {'✅ Passed' if h.get('tests_passed') else '❌ Failed'}")

    if result.get("qa_result"):
        q = result["qa_result"]
        print(f"QA      : {'✅ Approved' if q.get('decision') == 'approved' else '❌ Blocked'}")
        print(f"E2E     : {'✅ Passed' if q.get('e2e_passed') else '❌ Failed'}")
        print(f"Merged  : {'✅ Yes' if q.get('merged') else '❌ No'}")
        if q.get("e2e_failures"):
            print("E2E failures:")
            for f in q["e2e_failures"]:
                print(f"  • {f}")
        if q.get("findings"):
            print("Findings:")
            for f in q["findings"]:
                print(f"  • {f}")

    print(f"\nDuration: {result['duration_seconds']}s")
    print(f"Success : {'✅' if result['success'] else '❌'}")

    if result.get("error"):
        print(f"Error   : {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
