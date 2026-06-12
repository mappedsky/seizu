import { Autocomplete, TextField } from '@mui/material';
import type { SxProps, Theme } from '@mui/material/styles';

interface SyncMetadataAutocompleteProps {
  label: 'grouptype' | 'syncedtype' | 'groupid';
  value: string;
  onChange: (value: string) => void;
  options: string[];
  sx?: SxProps<Theme>;
}

/**
 * Free-solo autocomplete for a watch-scan field, offering the distinct values
 * present on SyncMetadata nodes while still accepting arbitrary input
 * (watch-scan fields are regexes, e.g. `.*`).
 */
function SyncMetadataAutocomplete({
  label,
  value,
  onChange,
  options,
  sx,
}: SyncMetadataAutocompleteProps) {
  return (
    <Autocomplete
      freeSolo
      options={options}
      inputValue={value}
      onInputChange={(_event, newValue) => onChange(newValue)}
      size="small"
      sx={sx}
      renderInput={(params) => (
        <TextField {...params} label={label} size="small" />
      )}
    />
  );
}

export default SyncMetadataAutocomplete;
