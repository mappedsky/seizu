import { useEffect, useRef } from 'react';
import { Typography, TextField, Autocomplete } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { useLazyCypherQuery } from 'src/hooks/useCypherQuery';
import { setQueryStringValue } from 'src/components/QueryString';
import type { InputValue } from 'src/config.context';

type AutocompleteOption = InputValue;

interface CypherAutocompleteProps {
  cypher?: string;
  params?: Record<string, unknown>;
  inputId?: string;
  inputDefault?: AutocompleteOption;
  labelName?: string;
  value?: Record<string, AutocompleteOption | undefined>;
  setValue?: (val: Record<string, AutocompleteOption | undefined>) => void;
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
  size?: 'small' | 'medium';
}

export default function CypherAutocomplete({
  cypher,
  params,
  inputId,
  inputDefault,
  labelName,
  value,
  setValue,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
  size = 'medium',
}: CypherAutocompleteProps) {
  const [run, { loading, error, records, tokenExpired }] = useLazyCypherQuery(
    cypher,
    reportQueryToken,
  );

  const runRef = useRef(run);
  runRef.current = run;

  useEffect(() => {
    runRef.current(params, { force: (refreshKey ?? 0) > 0 });
  }, [cypher, params, refreshKey]);

  useEffect(() => {
    if (tokenExpired) {
      onTokenExpired?.();
    }
  }, [tokenExpired, onTokenExpired]);

  if (error) {
    return (
      <Typography>Failed to load data from backend, please reload.</Typography>
    );
  }

  if (loading || records === undefined) {
    return (
      <TextField
        disabled
        fullWidth
        label={labelName}
        size={size}
        value=""
        variant="outlined"
        slotProps={{
          input: {
            endAdornment: <ConstellationSpinner size={16} />,
          },
        }}
      />
    );
  }

  const mungedRecords: AutocompleteOption[] = records.map((record) => {
    const value = String(record['value'] ?? '');
    return {
      label: String('label' in record ? (record['label'] ?? value) : value),
      value,
    };
  });

  // Add a clear/default option only if it isn't already present in the results.
  const clearOption: AutocompleteOption = inputDefault ?? {
    label: '',
    value: '',
  };
  const clearValue = clearOption.value;
  const alreadyPresent = mungedRecords.some((r) => r.value === clearValue);
  if (!alreadyPresent) {
    mungedRecords.push(clearOption);
  }

  return (
    <Autocomplete
      // Use null instead of undefined so MUI always treats this as a controlled input.
      value={value?.[inputId || ''] ?? null}
      onChange={(event, newValue) => {
        if (newValue === null || newValue === undefined) {
          setValue?.({ ...value, [inputId || '']: inputDefault });
          if (inputId) setQueryStringValue(inputId, inputDefault?.value);
        } else {
          setValue?.({ ...value, [inputId || '']: newValue });
          if (inputId) setQueryStringValue(inputId, newValue?.value);
        }
      }}
      filterSelectedOptions
      selectOnFocus
      clearOnBlur
      handleHomeEndKeys
      id={inputId}
      size={size}
      getOptionLabel={(option) => option.label}
      options={mungedRecords}
      isOptionEqualToValue={(option, val) => {
        if (option?.value === val.value) {
          return true;
        }
        return false;
      }}
      renderInput={(selectParams) => (
        <TextField {...selectParams} label={labelName} variant="outlined" />
      )}
    />
  );
}
