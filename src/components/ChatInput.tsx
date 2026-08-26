import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  memo,
  useCallback,
  useRef,
  useState,
} from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Send from '@mui/icons-material/Send';
import Stop from '@mui/icons-material/Stop';

interface ChatInputProps {
  busy: boolean;
  disabled: boolean;
  onSubmit: (text: string) => void;
  onStop: () => void;
  /** Optional controls shown on the left side of the composer footer. Keep the
   *  node referentially stable while streaming so this component stays memoized. */
  footerControls?: ReactNode;
  /** Whether the caller may bypass confirmations at all; hides the control when
   *  false. Passed as flags rather than a rendered node so this stays memoized
   *  against a parent that re-renders on every streamed frame. */
  showBypassConfirmations?: boolean;
  bypassConfirmations?: boolean;
  onBypassConfirmationsChange?: (value: boolean) => void;
}

const DEFAULT_INPUT_HEIGHT = 140;
const MAX_AUTO_INPUT_HEIGHT = DEFAULT_INPUT_HEIGHT * 2;

export default memo(function ChatInput({
  busy,
  disabled,
  onSubmit,
  onStop,
  footerControls,
  showBypassConfirmations = false,
  bypassConfirmations = false,
  onBypassConfirmationsChange,
}: ChatInputProps) {
  const [input, setInput] = useState('');
  const [inputHeight, setInputHeight] = useState(DEFAULT_INPUT_HEIGHT);
  const inputHeightRef = useRef(inputHeight);
  inputHeightRef.current = inputHeight;
  const autoSizingEnabledRef = useRef(true);
  const dragStartYRef = useRef(0);
  const dragStartHeightRef = useRef(0);

  const handleInputChange = (
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    setInput(event.target.value);
    if (
      !autoSizingEnabledRef.current ||
      !(event.target instanceof HTMLTextAreaElement)
    ) {
      return;
    }
    const textarea = event.target;
    const nonTextHeight = Math.max(
      0,
      inputHeightRef.current - textarea.clientHeight,
    );
    const previousHeight = textarea.style.getPropertyValue('height');
    const previousPriority = textarea.style.getPropertyPriority('height');
    textarea.style.setProperty('height', '0px', 'important');
    const contentHeight = textarea.scrollHeight;
    if (previousHeight) {
      textarea.style.setProperty('height', previousHeight, previousPriority);
    } else {
      textarea.style.removeProperty('height');
    }
    setInputHeight(
      Math.max(
        DEFAULT_INPUT_HEIGHT,
        Math.min(MAX_AUTO_INPUT_HEIGHT, contentHeight + nonTextHeight),
      ),
    );
  };

  const submitInput = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || busy || disabled) return false;
    setInput('');
    if (autoSizingEnabledRef.current) setInputHeight(DEFAULT_INPUT_HEIGHT);
    onSubmit(trimmed);
    return true;
  }, [busy, disabled, input, onSubmit]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitInput();
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    submitInput();
  };

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    autoSizingEnabledRef.current = false;
    dragStartYRef.current = e.clientY;
    dragStartHeightRef.current = inputHeightRef.current;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (ev: MouseEvent) => {
      const delta = dragStartYRef.current - ev.clientY;
      setInputHeight(
        Math.max(100, Math.min(420, dragStartHeightRef.current + delta)),
      );
    };

    const handleMouseUp = () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, []);

  return (
    <>
      <Box
        onMouseDown={handleDragStart}
        sx={{
          alignItems: 'center',
          cursor: 'ns-resize',
          display: 'flex',
          flexShrink: 0,
          height: 8,
          justifyContent: 'center',
          my: 0.5,
          '&::after': {
            bgcolor: 'divider',
            borderRadius: 2,
            content: '""',
            display: 'block',
            height: 4,
            transition: 'background-color 0.15s',
            width: 48,
          },
          '&:hover::after': { bgcolor: 'primary.main' },
        }}
      />
      <Box
        data-testid="chat-composer"
        style={{ height: inputHeight }}
        sx={{ flexShrink: 0 }}
      >
        <Card sx={{ height: '100%' }}>
          <CardContent
            component="form"
            onSubmit={handleSubmit}
            sx={{
              display: 'flex',
              flexDirection: 'column',
              height: '100%',
              '&:last-child': { pb: 2 },
            }}
          >
            <TextField
              multiline
              fullWidth
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              placeholder="Ask Seizu..."
              disabled={busy}
              variant="outlined"
              slotProps={{
                input: {
                  endAdornment: (
                    <InputAdornment
                      position="end"
                      sx={{ alignSelf: 'flex-end', mb: 0.5 }}
                    >
                      <Tooltip title={busy ? 'Stop response' : 'Send message'}>
                        <span>
                          <IconButton
                            aria-label={busy ? 'Stop' : 'Send'}
                            color="primary"
                            disabled={!busy && (!input.trim() || disabled)}
                            onClick={busy ? onStop : undefined}
                            type={busy ? 'button' : 'submit'}
                          >
                            {busy ? <Stop /> : <Send />}
                          </IconButton>
                        </span>
                      </Tooltip>
                    </InputAdornment>
                  ),
                },
              }}
              sx={{
                flex: 1,
                minHeight: 0,
                '& .MuiInputBase-root': {
                  alignItems: 'flex-start',
                  height: '100%',
                },
                '& .MuiInputBase-input': {
                  boxSizing: 'border-box',
                  height: '100% !important',
                  overflow: 'auto !important',
                },
              }}
            />
            {footerControls || showBypassConfirmations ? (
              <Box
                sx={{
                  alignItems: 'center',
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 1,
                  justifyContent: 'space-between',
                  mt: 1.5,
                }}
              >
                {footerControls}
                {showBypassConfirmations ? (
                  <Tooltip title="Run actions without per-action confirmation prompts. Every bypassed action is audit-logged.">
                    <FormControlLabel
                      control={
                        <Switch
                          checked={bypassConfirmations}
                          onChange={(event) =>
                            onBypassConfirmationsChange?.(event.target.checked)
                          }
                          size="small"
                        />
                      }
                      label={
                        <Typography color="text.secondary" variant="caption">
                          Bypass confirmations
                        </Typography>
                      }
                      sx={{ ml: 'auto', mr: 0 }}
                    />
                  </Tooltip>
                ) : null}
              </Box>
            ) : null}
          </CardContent>
        </Card>
      </Box>
    </>
  );
});
