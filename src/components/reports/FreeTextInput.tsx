import { useState, useEffect } from 'react';
import { TextField } from '@mui/material';
import { InputValue } from 'src/config.context';
import { setQueryStringValue } from 'src/components/QueryString';

const DEBOUNCE_MS = 300;

interface FreeTextInputProps {
  inputId?: string;
  inputDefault?: InputValue;
  labelName?: string;
  value?: Record<string, InputValue | undefined>;
  setValue?: (val: Record<string, InputValue | undefined>) => void;
  size?: 'small' | 'medium';
}

export default function FreeTextInput({
  inputId,
  inputDefault,
  labelName,
  value,
  setValue,
  size = 'medium',
}: FreeTextInputProps) {
  const [localValue, setLocalValue] = useState(
    value?.[inputId || '']?.value ?? inputDefault?.value ?? '',
  );

  useEffect(() => {
    const timer = setTimeout(() => {
      if (
        localValue === null ||
        localValue === undefined ||
        localValue === ''
      ) {
        setValue?.({ ...value, [inputId || '']: inputDefault });
        if (inputId) setQueryStringValue(inputId, inputDefault?.value);
      } else {
        setValue?.({
          ...value,
          [inputId || '']: { label: '', value: localValue },
        });
        if (inputId) setQueryStringValue(inputId, localValue);
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [localValue]);

  return (
    <TextField
      onChange={(event) => setLocalValue(event.target.value)}
      value={localValue}
      id={inputId}
      label={labelName}
      variant="outlined"
      fullWidth
      size={size}
    />
  );
}
