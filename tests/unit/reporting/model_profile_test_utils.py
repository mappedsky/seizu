from reporting.schema.model_profiles import RESOLVED_MODEL_STAGES, ResolvedModelProfile


def resolved_model_profile(
    *,
    source: str = "environment",
    profile_id: str | None = None,
    reasoning_effort: str | None = None,
) -> ResolvedModelProfile:
    """Build a complete immutable model configuration for unit tests."""
    stages = {
        stage: {
            "primary": {
                "model_id": f"primary/{stage}",
                "max_output_tokens": 10_000,
                "reasoning_effort": reasoning_effort or "",
                "role": "default" if stage == "assistant" else stage,
            },
            "economy": {
                "model_id": f"economy/{stage}",
                "max_output_tokens": 5_000,
                "reasoning_effort": "low",
                "role": "default" if stage == "assistant" else stage,
            },
        }
        for stage in RESOLVED_MODEL_STAGES
    }
    return ResolvedModelProfile(
        source=source,
        profile_id=profile_id,
        profile_name=profile_id or "",
        profile_version=1 if profile_id else None,
        reasoning_effort=reasoning_effort,
        cost_budget_usd=1,
        stages=stages,
    )
