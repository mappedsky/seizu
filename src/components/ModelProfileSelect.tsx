import { FormControl, ListSubheader, MenuItem, Select } from '@mui/material';
import type {
  ModelProfileChoice,
  ReasoningEffort,
} from 'src/hooks/useModelProfilesApi';

/**
 * The model profile and reasoning level a request runs with, as one quiet
 * control. Shared by the chat composer and the query console's Ask mode.
 */
export default function ModelProfileSelect({
  profiles,
  profileId,
  reasoningEffort,
  lockedProfileId,
  disabled,
  onChange,
}: {
  profiles: ModelProfileChoice[];
  profileId: string;
  reasoningEffort: ReasoningEffort | '';
  lockedProfileId?: string | null;
  disabled: boolean;
  onChange: (profileId: string, reasoningEffort: ReasoningEffort) => void;
}) {
  const visibleProfiles = lockedProfileId
    ? profiles.filter((profile) => profile.profile_id === lockedProfileId)
    : profiles;
  if (visibleProfiles.length === 0) return null;
  const options = visibleProfiles.flatMap((profile) =>
    profile.reasoning_efforts.map((effort) => ({
      effort,
      key: `${profile.profile_id}:${effort}`,
      profile,
    })),
  );
  const value =
    profileId && reasoningEffort ? `${profileId}:${reasoningEffort}` : '';
  return (
    // A quiet text control in the composer's control row rather than a full
    // field: it truncates before it crowds the send button.
    <FormControl
      disabled={disabled}
      size="small"
      sx={{ flexShrink: 1, minWidth: 0 }}
    >
      <Select
        variant="standard"
        disableUnderline
        inputProps={{ 'aria-label': 'Model and reasoning' }}
        sx={(theme) => ({
          borderRadius: 1,
          color: 'text.secondary',
          fontSize: theme.typography.body2.fontSize,
          '&:hover': { bgcolor: 'action.hover', color: 'text.primary' },
          '& .MuiSelect-select': {
            overflow: 'hidden',
            pl: 1,
            py: 0.5,
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          },
          '& .MuiSelect-icon': { color: 'inherit' },
        })}
        onChange={(event) => {
          const selected = options.find(
            (option) => option.key === event.target.value,
          );
          if (selected) onChange(selected.profile.profile_id, selected.effort);
        }}
        renderValue={(selectedValue) => {
          const selected = options.find(
            (option) => option.key === selectedValue,
          );
          if (!selected) return '';
          return `${selected.profile.name} · ${selected.effort}`;
        }}
        value={value}
      >
        {visibleProfiles.flatMap((profile) => {
          const defaultMark = profile.is_default ? ' (default)' : '';
          // A profile with one level is one choice, not a group of one.
          if (profile.reasoning_efforts.length === 1) {
            const [effort] = profile.reasoning_efforts;
            return [
              <MenuItem
                key={`${profile.profile_id}:${effort}`}
                value={`${profile.profile_id}:${effort}`}
              >
                {profile.name} · {effort}
                {defaultMark}
              </MenuItem>,
            ];
          }
          return [
            <ListSubheader key={`${profile.profile_id}:group`}>
              {profile.name}
              {defaultMark}
            </ListSubheader>,
            ...profile.reasoning_efforts.map((effort) => (
              <MenuItem
                key={`${profile.profile_id}:${effort}`}
                value={`${profile.profile_id}:${effort}`}
                sx={{ pl: 3 }}
              >
                {effort}
              </MenuItem>
            )),
          ];
        })}
      </Select>
    </FormControl>
  );
}
