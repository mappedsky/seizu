import { useEffect, useRef, useState } from 'react';
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
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import type {
  ChatElicitation,
  InputAction,
} from 'src/hooks/useChatElicitations';

// How long the answer is confirmed in place before the card closes. Long
// enough to read, short enough that the turn it releases is the next thing to
// happen rather than something the reader is left waiting through.
const CONFIRMATION_MS = 1000;

const CONFIRMATIONS: Record<InputAction, string> = {
  accept: 'Answer sent',
  decline: 'Request declined',
  cancel: 'Request cancelled',
};

export default function ChatElicitationCard({
  item,
  busy,
  onRespond,
  onAnswered,
  onResume,
}: {
  item: ChatElicitation;
  busy: boolean;
  onRespond: (
    action: InputAction,
    content?: Record<string, unknown>,
  ) => Promise<void>;
  // The answer is recorded and confirmed; the card is done. Delivering it is
  // the caller's to start, and is deliberately not awaited here -- that
  // promise settles when the whole turn does, which would hold the card open
  // for the answer it already has.
  onAnswered: (action: InputAction) => void;
  onResume: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<InputAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Held in a ref so a re-render of the page does not restart the timer and
  // leave a confirmed card sitting open.
  const answeredRef = useRef(onAnswered);
  answeredRef.current = onAnswered;
  useEffect(() => {
    if (!sent) return;
    const timer = window.setTimeout(
      () => answeredRef.current(sent),
      CONFIRMATION_MS,
    );
    return () => window.clearTimeout(timer);
  }, [sent]);
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
      setSent(action);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not submit input.');
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
      {sent ? (
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: 'center', color: 'success.main', mt: 2 }}
        >
          <CheckCircleIcon fontSize="small" />
          <Typography variant="body2">{CONFIRMATIONS[sent]}</Typography>
        </Stack>
      ) : pending ? (
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
                onResume();
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
