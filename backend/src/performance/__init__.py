"""Action and expression specialist post-processing."""

from ..llm_json_client import LLMJsonClient
from .action import ActionSelectionStage
from .expression import ExpressionSelectionStage


def run_performance_pipeline(script, resource_loader, progress_callback=None, native_scenes=None):
    # Native four-file releases are already approved. Selecting resources here
    # would either invalidate their hashes or be silently lost at publication.
    if native_scenes is not None:
        return {"ok": True, "enriched_script": script, "skipped": "native_release_immutable"}
    try:
        client = LLMJsonClient(model_env="PERFORMANCE_MODEL")
        action_result = ActionSelectionStage(
            script, resource_loader, client, progress_callback
        ).run()
        expression_result = ExpressionSelectionStage(
            action_result["script"], resource_loader, client, progress_callback
        ).run()
        return {
            "ok": True,
            "enriched_script": expression_result["script"],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["ActionSelectionStage", "ExpressionSelectionStage", "run_performance_pipeline"]
