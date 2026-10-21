import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  memo,
  useCallback,
  useState,
} from 'react';
import Box from '@mui/material/Box';
import IconButton from '@mui/material/IconButton';
import InputBase from '@mui/material/InputBase';
import Tooltip from '@mui/material/Tooltip';
import ArrowUpward from '@mui/icons-material/ArrowUpward';
import Stop from '@mui/icons-material/Stop';

interface ChatInputProps {
  busy: boolean;
  disabled: boolean;
  onSubmit: (text: string) => void;
  onStop: () => void;
  /** Optional controls shown on the left of the composer's control row. Keep
   *  the node referentially stable while streaming so this stays memoized. */
  footerControls?: ReactNode;
}

// One line to start; the field grows with its text up to this many lines and
// scrolls beyond it.
const MAX_ROWS = 8;

/**
 * The message composer: one surface holding the text and, beneath it, a single
 * row of controls — model and send. Enter sends;
 * Shift+Enter, Ctrl+Enter and Cmd+Enter each start a new line.
 */
export default memo(function ChatInput({
  busy,
  disabled,
  onSubmit,
  onStop,
  footerControls,
}: ChatInputProps) {
  const [input, setInput] = useState('');

  const submitInput = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || busy || disabled) return false;
    setInput('');
    onSubmit(trimmed);
    return true;
  }, [busy, disabled, input, onSubmit]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitInput();
  };

  const handleInputKeyDown = (
    event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (event.metaKey || event.ctrlKey) {
      // Either modifier means "new line", whichever a platform reaches for.
      // The browser inserts nothing for these itself, so place the line
      // break at the caret (replacing any selection) and keep the caret
      // after it.
      const field = event.currentTarget;
      const start = field.selectionStart ?? input.length;
      const end = field.selectionEnd ?? start;
      setInput(`${input.slice(0, start)}\n${input.slice(end)}`);
      requestAnimationFrame(() => {
        field.selectionStart = field.selectionEnd = start + 1;
      });
      return;
    }
    submitInput();
  };

  const canSend = !busy && !!input.trim() && !disabled;

  return (
    <Box
      component="form"
      data-testid="chat-composer"
      onSubmit={handleSubmit}
      sx={{
        bgcolor: 'background.paper',
        border: 1,
        borderColor: 'divider',
        borderRadius: 3,
        flexShrink: 0,
        mt: 1,
        pb: 0.75,
        pt: 1,
        px: 1.5,
        transition: 'border-color 120ms ease',
        '&:focus-within': { borderColor: 'primary.main' },
      }}
    >
      <InputBase
        multiline
        fullWidth
        minRows={1}
        maxRows={MAX_ROWS}
        value={input}
        onChange={(
          event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
        ) => setInput(event.target.value)}
        onKeyDown={handleInputKeyDown}
        placeholder="Ask Seizu..."
        disabled={busy}
        inputProps={{ 'aria-label': 'Message' }}
        sx={(theme) => ({
          fontSize: theme.typography.body1.fontSize,
          lineHeight: 1.5,
          px: 0.5,
          py: 0.5,
        })}
      />
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          gap: 0.5,
          minWidth: 0,
          mt: 0.5,
        }}
      >
        {footerControls}
        <Tooltip title={busy ? 'Stop response' : 'Send message'}>
          <span style={{ marginLeft: 'auto', flexShrink: 0 }}>
            <IconButton
              aria-label={busy ? 'Stop' : 'Send'}
              disabled={!busy && !canSend}
              onClick={busy ? onStop : undefined}
              size="small"
              type={busy ? 'button' : 'submit'}
              sx={{
                bgcolor: 'primary.main',
                color: 'primary.contrastText',
                height: 32,
                width: 32,
                '&:hover': { bgcolor: 'primary.dark' },
                '&.Mui-disabled': {
                  bgcolor: 'action.disabledBackground',
                  color: 'action.disabled',
                },
              }}
            >
              {busy ? (
                <Stop fontSize="small" />
              ) : (
                <ArrowUpward fontSize="small" />
              )}
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
});
