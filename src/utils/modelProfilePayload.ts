import type { ModelProfilePayload } from 'src/hooks/useModelProfilesApi';

export function modelProfilePayload(
  profile: ModelProfilePayload,
): ModelProfilePayload {
  return {
    name: profile.name,
    description: profile.description,
    enabled: profile.enabled,
    is_default: profile.is_default,
    primary: profile.primary,
    economy: profile.economy,
    stage_overrides: profile.stage_overrides,
    user_reasoning_efforts: profile.user_reasoning_efforts,
    default_reasoning_effort: profile.default_reasoning_effort,
    run_cost_budget_usd: profile.run_cost_budget_usd,
  };
}
