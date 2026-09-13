import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import type {
  ChatElicitation,
  InputAction,
} from 'src/hooks/useChatElicitations';

export default function ChatElicitationCard({
  item,
  busy,
  onRespond,
  onResume,
}: {
  item: ChatElicitation;
  busy: boolean;
  onRespond: (
    action: InputAction,
    content?: Record<string, unknown>,
  ) => Promise<void>;
  onResume: () => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expired = new Date(item.expires_at).getTime() <= Date.now();
  const pending = item.status === 'pending' && !expired;
  const fields = item.requested_schema?.properties ?? {};
  const act = async (action: InputAction) => {
    setSending(true);
    setError(null);
    try {
      const content =
        item.kind === 'form' && action === 'accept'
          ? {
              ...Object.fromEntries(
                Object.entries(fields)
                  .filter(
                    ([, field]) => field.type === 'boolean' && !field.enum,
                  )
                  .map(([key]) => [key, false]),
              ),
              ...values,
            }
          : undefined;
      await onRespond(action, content);
      setValues({});
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not submit input.');
    } finally {
      setSending(false);
    }
  };
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2">
        {item.proxy_name} · {item.tool_name}
      </Typography>
      <Alert severity="warning" sx={{ my: 1 }}>
        This request comes from {item.proxy_name}. Your answers will be sent to
        that server and may appear in chat or be processed by the AI model. Do
        not enter passwords, API keys, access tokens, or verification codes.
      </Alert>
      <Typography sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {item.message}
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {pending ? (
        <Box
          component="form"
          onSubmit={(event) => {
            event.preventDefault();
            void act('accept');
          }}
        >
          <Stack spacing={2} sx={{ mt: 2 }}>
            {item.kind === 'url' && item.url && (
              <Button
                component="a"
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open {new URL(item.url).host}
              </Button>
            )}
            {item.kind === 'form' &&
              Object.entries(fields).map(([key, field]) => {
                const required =
                  item.requested_schema?.required?.includes(key) ?? false;
                if (field.type === 'boolean' && !field.enum)
                  return (
                    <FormControlLabel
                      key={key}
                      label={field.title ?? key}
                      control={
                        <Checkbox
                          disabled={sending || busy}
                          checked={values[key] === true}
                          onChange={(_, checked) =>
                            setValues((old) => ({ ...old, [key]: checked }))
                          }
                        />
                      }
                    />
                  );
                return (
                  <TextField
                    key={key}
                    label={field.title ?? key}
                    helperText={field.description}
                    required={required}
                    disabled={sending || busy}
                    select={!!field.enum}
                    value={values[key] ?? ''}
                    type={
                      !field.enum &&
                      (field.type === 'number' || field.type === 'integer')
                        ? 'number'
                        : 'text'
                    }
                    slotProps={{
                      htmlInput: {
                        maxLength: field.maxLength ?? 4096,
                        minLength: field.minLength,
                        min: field.minimum,
                        max: field.maximum,
                        step: field.type === 'integer' ? 1 : 'any',
                      },
                    }}
                    onChange={(event) => {
                      const raw = event.target.value;
                      const value = field.enum
                        ? field.enum[Number(raw)]
                        : field.type === 'number' || field.type === 'integer'
                          ? raw === ''
                            ? undefined
                            : Number(raw)
                          : raw;
                      setValues((old) => ({ ...old, [key]: value }));
                    }}
                    {...(field.enum
                      ? {
                          value:
                            values[key] === undefined
                              ? ''
                              : field.enum.indexOf(
                                  values[key] as string | number | boolean,
                                ),
                        }
                      : {})}
                  >
                    {field.enum?.map((value, index) => (
                      <MenuItem key={index} value={index}>
                        {field.enumNames?.[index] ?? String(value)}
                      </MenuItem>
                    ))}
                  </TextField>
                );
              })}
            <Stack direction="row" spacing={1}>
              <Button type="submit" disabled={sending || busy}>
                {item.kind === 'url' ? 'Completed' : 'Submit'}
              </Button>
              <Button
                disabled={sending || busy}
                onClick={() => void act('decline')}
              >
                Decline
              </Button>
              <Button
                disabled={sending || busy}
                onClick={() => void act('cancel')}
              >
                Cancel
              </Button>
            </Stack>
          </Stack>
        </Box>
      ) : (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <Typography>
            {expired && item.status !== 'consumed' ? 'expired' : item.status}
          </Typography>
          {item.status !== 'consumed' && (
            <Button
              disabled={sending || busy}
              onClick={() => {
                setSending(true);
                void onResume()
                  .catch(() => setError('Could not resume. Try again.'))
                  .finally(() => setSending(false));
              }}
            >
              Continue chat
            </Button>
          )}
        </Stack>
      )}
    </Paper>
  );
}
