import { Chip, Typography } from '@mui/material';
import DetailDialog, { DetailSection } from 'src/components/DetailDialog';
import { listTableMonoCellSx } from 'src/components/ListTable';
import type { ModelProfilePayload } from 'src/hooks/useModelProfilesApi';

export default function ModelProfileDetailDialog({
  profile,
  version,
  onClose,
}: {
  profile: ModelProfilePayload | null;
  version?: number;
  onClose: () => void;
}) {
  return (
    <DetailDialog
      open={profile !== null}
      onClose={onClose}
      title={profile?.name ?? 'Model profile'}
      secondary={version === undefined ? undefined : `v${version}`}
    >
      {profile ? (
        <>
          <DetailSection title="Description">
            <Typography>{profile.description || '—'}</Typography>
          </DetailSection>
          <DetailSection title="Status">
            <Chip
              label={profile.enabled ? 'Enabled' : 'Disabled'}
              color={profile.enabled ? 'success' : 'default'}
              size="small"
            />
            {profile.is_default ? (
              <Chip
                label="Default"
                size="small"
                color="primary"
                sx={{ ml: 1 }}
              />
            ) : null}
          </DetailSection>
          <DetailSection title="Primary model">
            <Typography sx={listTableMonoCellSx}>
              {profile.primary.model_id}
            </Typography>
          </DetailSection>
          <DetailSection title="Economy model">
            <Typography sx={listTableMonoCellSx}>
              {profile.economy.model_id}
            </Typography>
          </DetailSection>
          <DetailSection title="Economy reasoning">
            <Typography>{profile.economy.reasoning_effort}</Typography>
          </DetailSection>
          <DetailSection title="Cost cap">
            <Typography>{`$${profile.run_cost_budget_usd}`}</Typography>
          </DetailSection>
          <DetailSection title="User reasoning levels">
            <Typography>{profile.user_reasoning_efforts.join(', ')}</Typography>
          </DetailSection>
          <DetailSection title="Default reasoning">
            <Typography>{profile.default_reasoning_effort}</Typography>
          </DetailSection>
          <DetailSection title="Stage overrides">
            {Object.entries(profile.stage_overrides).map(
              ([stage, override]) => (
                <Typography key={stage} variant="body2">
                  {stage.replaceAll('_', ' ')}:{' '}
                  {override.model_id || 'Inherit model'};{' '}
                  {override.reasoning_effort ?? 'Inherit reasoning'}
                </Typography>
              ),
            )}
            {Object.keys(profile.stage_overrides).length === 0 ? (
              <Typography>None</Typography>
            ) : null}
          </DetailSection>
        </>
      ) : null}
    </DetailDialog>
  );
}
