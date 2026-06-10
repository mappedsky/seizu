import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  memo,
  useCallback,
  useRef,
  useState,
} from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Send from '@mui/icons-material/Send';
import Stop from '@mui/icons-material/Stop';

interface ChatInputProps {
  busy: boolean;
  disabled: boolean;
  onSubmit: (text: string) => void;
  onStop: () => void;
}

export default memo(function ChatInput({
  busy,
  disabled,
  onSubmit,
  onStop,
}: ChatInputProps) {
  const [input, setInput] = useState('');
  const [inputHeight, setInputHeight] = useState(140);
  const inputHeightRef = useRef(inputHeight);
  inputHeightRef.current = inputHeight;
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);

  const handleInputChange = (
    event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    setInput(event.target.value);
  };

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

  const handleInputKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    submitInput();
  };

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStartY.current = e.clientY;
    dragStartHeight.current = inputHeightRef.current;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';

    const handleMouseMove = (ev: MouseEvent) => {
      const delta = dragStartY.current - ev.clientY;
      setInputHeight(
        Math.max(100, Math.min(420, dragStartHeight.current + delta)),
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
      <Box sx={{ flexShrink: 0, height: inputHeight }}>
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
              placeholder="Ask about your security graph..."
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
          </CardContent>
        </Card>
      </Box>
    </>
  );
});
