import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputBase,
  LinearProgress,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import AutoAwesome from '@mui/icons-material/AutoAwesome';
import Close from '@mui/icons-material/Close';
import PlayArrow from '@mui/icons-material/PlayArrow';
import ModelProfileSelect from 'src/components/ModelProfileSelect';
import type {
  ReasoningEffort,
  ModelProfileChoice,
} from 'src/hooks/useModelProfilesApi';
import { fontFamilyMono, fontFamilySans } from 'src/theme/typography';

/** What the composer is taking: a Cypher query, or a question to turn into one. */
export type ComposerMode = 'cypher' | 'ask';

/**
 * The question the query in the editor came from, and how that went. A note
 * tied to a history entry is shown only while that entry is the one running,
 * so a later run hides it without anything having to clear it.
 */
export interface QueryProvenance {
  historyId: string | null;
  prompt: string;
  explanation?: string;
  problems?: string[];
  severity: 'info' | 'warning' | 'error';
  /** What generated it, e.g. "Accurate · low"; absent for a restored entry. */
  answeredBy?: string;
}

export interface ModelPick {
  profiles: ModelProfileChoice[];
  profileId: string;
  reasoningEffort: ReasoningEffort | '';
  onChange: (profileId: string, reasoningEffort: ReasoningEffort) => void;
}

// The editor starts at one line and grows with the query up to this many
// lines, then scrolls, so the graph keeps most of the page.
const QUERY_MAX_ROWS = 12;
const QUESTION_MAX_ROWS = 4;

// The modifier this platform's users reach for; either one runs the query.
export const RUN_SHORTCUT =
  typeof navigator !== 'undefined' && /Mac|iP(hone|ad)/.test(navigator.platform)
    ? '⌘+Enter'
    : 'Ctrl+Enter';

const primaryButtonSx = {
  bgcolor: 'primary.main',
  color: 'primary.contrastText',
  flexShrink: 0,
  height: 32,
  width: 32,
  '&:hover': { bgcolor: 'primary.dark' },
  '&.Mui-disabled': {
    bgcolor: 'action.disabledBackground',
    color: 'action.disabled',
  },
} as const;

const cypherInputSx = {
  fontFamily: fontFamilyMono,
  fontSize: 13,
  // Show the characters as typed: `->` stays two glyphs, not an arrow.
  fontVariantLigatures: 'none',
  lineHeight: 1.6,
  py: 0.5,
} as const;

interface QueryComposerProps {
  /** Whether Ask mode exists for this user at all. */
  canGenerate: boolean;
  mode: ComposerMode;
  onModeChange: (mode: ComposerMode) => void;
  queryText: string;
  onQueryTextChange: (value: string) => void;
  onRun: () => void;
  questionText: string;
  onQuestionTextChange: (value: string) => void;
  onGenerate: () => void;
  onCancelGenerate: () => void;
  /** `performance.now()` when the pending generation started, else null. */
  generatingSince: number | null;
  provenance: QueryProvenance | null;
  onDismissProvenance: () => void;
  onRefine: () => void;
  modelPick: ModelPick | null;
}

/**
 * The console's query box. Without Ask mode it is one line and a run button.
 * With it, the question a query came from sits above the Cypher it became, in
 * the same surface, with a mode switch and the model choice below.
 */
export default function QueryComposer(props: QueryComposerProps) {
  if (!props.canGenerate) return <CypherOnlyComposer {...props} />;
  return <StackedComposer {...props} />;
}

function CypherOnlyComposer({
  queryText,
  onQueryTextChange,
  onRun,
}: QueryComposerProps) {
  return (
    <ComposerSurface busy={false}>
      <Box
        sx={{
          alignItems: 'flex-end',
          display: 'flex',
          gap: 1,
          pl: 1.5,
          pr: 0.75,
          py: 0.75,
        }}
      >
        <CypherInput
          value={queryText}
          onChange={onQueryTextChange}
          onRun={onRun}
          readOnly={false}
        />
        <RunButton disabled={!queryText.trim()} onRun={onRun} />
      </Box>
    </ComposerSurface>
  );
}

function StackedComposer({
  mode,
  onModeChange,
  queryText,
  onQueryTextChange,
  onRun,
  questionText,
  onQuestionTextChange,
  onGenerate,
  onCancelGenerate,
  generatingSince,
  provenance,
  onDismissProvenance,
  onRefine,
  modelPick,
}: QueryComposerProps) {
  const asking = mode === 'ask';
  const generating = generatingSince !== null;
  const questionRef = useRef<HTMLTextAreaElement | null>(null);
  const cypherRef = useRef<HTMLTextAreaElement | null>(null);

  // Moving focus is a response to the mode changing, which the parent decides;
  // the field the new mode edits takes it.
  const previousMode = useRef(mode);
  useEffect(() => {
    if (previousMode.current === mode) return;
    previousMode.current = mode;
    (mode === 'ask' ? questionRef : cypherRef).current?.focus();
  }, [mode]);

  const showQuestionRow = asking || provenance !== null;
  // In Ask mode the editor is context for the question ("only the public
  // ones"), so it stays visible only when there is something to refine.
  const showCypherRow = !asking || queryText.trim() !== '';

  const handleQuestionKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      if (generating) onCancelGenerate();
      else if (!questionText.trim()) onModeChange('cypher');
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!generating) onGenerate();
    }
  };

  return (
    <ComposerSurface busy={generating}>
      {showQuestionRow && (
        <QuestionRow
          asking={asking}
          generating={generating}
          questionText={questionText}
          onQuestionTextChange={onQuestionTextChange}
          onKeyDown={handleQuestionKeyDown}
          inputRef={questionRef}
          provenance={provenance}
          onDismissProvenance={onDismissProvenance}
          onRefine={onRefine}
        />
      )}

      {showCypherRow && (
        <Box
          sx={{
            borderTop: showQuestionRow ? 1 : 0,
            borderColor: 'divider',
            px: 1.5,
            pt: 0.5,
            // In Ask mode the query is context, not the thing being written.
            opacity: asking ? 0.72 : 1,
            transition: 'opacity 150ms ease-out',
            '&:focus-within': { opacity: 1 },
          }}
        >
          <CypherInput
            value={queryText}
            onChange={onQueryTextChange}
            onRun={onRun}
            readOnly={generating}
            inputRef={cypherRef}
          />
        </Box>
      )}

      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          gap: 1,
          minWidth: 0,
          pl: 0.75,
          pr: 0.75,
          pb: 0.75,
          pt: 0.5,
        }}
      >
        <ToggleButtonGroup
          exclusive
          size="small"
          value={mode}
          aria-label="Composer mode"
          disabled={generating}
          onChange={(_event, next: ComposerMode | null) => {
            if (next) onModeChange(next);
          }}
          sx={{
            flexShrink: 0,
            '& .MuiToggleButton-root': {
              border: 0,
              borderRadius: 1.5,
              gap: 0.5,
              height: 28,
              px: 1.25,
              textTransform: 'none',
              fontSize: 13,
              fontWeight: 500,
              color: 'text.secondary',
            },
            '& .MuiToggleButton-root.Mui-selected': {
              bgcolor: 'action.selected',
              color: 'text.primary',
            },
            '& .MuiToggleButton-root.Mui-selected[value="ask"]': {
              color: 'primary.main',
            },
            bgcolor: 'action.hover',
            borderRadius: 2,
            p: 0.25,
          }}
        >
          <ToggleButton value="cypher">Cypher</ToggleButton>
          <ToggleButton value="ask">
            <AutoAwesome sx={{ fontSize: 14 }} />
            Ask
          </ToggleButton>
        </ToggleButtonGroup>

        {asking && modelPick && (
          <ModelProfileSelect
            profiles={modelPick.profiles}
            profileId={modelPick.profileId}
            reasoningEffort={modelPick.reasoningEffort}
            disabled={generating}
            onChange={modelPick.onChange}
          />
        )}

        <Box sx={{ flex: 1 }} />

        {generating ? (
          <>
            <Elapsed since={generatingSince} />
            <Button
              size="small"
              onClick={onCancelGenerate}
              sx={{
                color: 'text.secondary',
                flexShrink: 0,
                textTransform: 'none',
              }}
            >
              Cancel
            </Button>
            <Box sx={{ ...primaryButtonSx, ...centeredSx }}>
              <CircularProgress
                size={16}
                color="inherit"
                aria-label="Generating query"
              />
            </Box>
          </>
        ) : asking ? (
          <Tooltip title="Generate query (Enter)">
            <span style={{ flexShrink: 0 }}>
              <IconButton
                aria-label="Generate query"
                onClick={onGenerate}
                disabled={!questionText.trim()}
                size="small"
                sx={primaryButtonSx}
              >
                <AutoAwesome fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        ) : (
          <RunButton disabled={!queryText.trim()} onRun={onRun} />
        )}
      </Box>
    </ComposerSurface>
  );
}

const centeredSx = {
  alignItems: 'center',
  borderRadius: '50%',
  display: 'flex',
  justifyContent: 'center',
} as const;

function ComposerSurface({
  busy,
  children,
}: {
  busy: boolean;
  children: React.ReactNode;
}) {
  return (
    <Box
      data-testid="query-composer"
      aria-busy={busy}
      sx={{
        bgcolor: 'background.paper',
        border: 1,
        borderColor: busy ? 'primary.main' : 'divider',
        borderRadius: 2,
        flexShrink: 0,
        mt: 1.5,
        overflow: 'hidden',
        position: 'relative',
        transition: 'border-color 120ms ease',
        '&:focus-within': { borderColor: 'primary.main' },
      }}
    >
      {busy && (
        <LinearProgress
          aria-hidden
          sx={{ height: 2, left: 0, position: 'absolute', right: 0, top: 0 }}
        />
      )}
      {children}
    </Box>
  );
}

function QuestionRow({
  asking,
  generating,
  questionText,
  onQuestionTextChange,
  onKeyDown,
  inputRef,
  provenance,
  onDismissProvenance,
  onRefine,
}: {
  asking: boolean;
  generating: boolean;
  questionText: string;
  onQuestionTextChange: (value: string) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  inputRef: React.Ref<HTMLTextAreaElement>;
  provenance: QueryProvenance | null;
  onDismissProvenance: () => void;
  onRefine: () => void;
}) {
  const accent =
    provenance?.severity === 'error'
      ? 'error.main'
      : provenance?.severity === 'warning'
        ? 'warning.main'
        : 'primary.main';
  // Asking with a generated query in the editor refines it: the question it
  // came from stays in view as context rather than being sent again. A failed
  // ask keeps its own question in the field, and its error below.
  const refining =
    asking && provenance !== null && provenance.severity !== 'error';
  const detail = provenance;
  return (
    <Box
      data-testid="query-question"
      role={detail && detail.severity !== 'info' ? 'alert' : undefined}
      sx={{ px: 1.5, pt: 1, pb: 0.75 }}
    >
      <Box sx={{ alignItems: 'flex-start', display: 'flex', gap: 1 }}>
        <AutoAwesome
          aria-hidden
          sx={{
            color: asking ? 'primary.main' : accent,
            flexShrink: 0,
            fontSize: 16,
            mt: '5px',
          }}
        />
        {asking ? (
          <InputBase
            multiline
            fullWidth
            minRows={1}
            maxRows={QUESTION_MAX_ROWS}
            value={questionText}
            readOnly={generating}
            inputRef={inputRef}
            onChange={(e) => onQuestionTextChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={
              refining
                ? 'Change it — only the ones published this year?'
                : 'Ask about the graph — which S3 buckets are public?'
            }
            inputProps={{ 'aria-label': 'Question' }}
            sx={{
              fontFamily: fontFamilySans,
              fontSize: 14,
              lineHeight: 1.6,
              p: 0,
            }}
          />
        ) : (
          <Typography
            variant="body2"
            sx={{
              flex: 1,
              fontSize: 14,
              lineHeight: 1.6,
              minWidth: 0,
              overflowWrap: 'anywhere',
            }}
          >
            {provenance?.prompt}
          </Typography>
        )}
        {!asking && (
          <Box sx={{ display: 'flex', flexShrink: 0, gap: 0.25, mt: '-2px' }}>
            <Button
              size="small"
              onClick={onRefine}
              sx={{ minWidth: 0, px: 1, textTransform: 'none' }}
            >
              Refine
            </Button>
            <Tooltip title="Clear question">
              <IconButton
                aria-label="Clear question"
                onClick={onDismissProvenance}
                size="small"
              >
                <Close fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        )}
      </Box>

      {refining && detail && (
        <Typography
          variant="caption"
          component="div"
          color="text.secondary"
          sx={{ overflowWrap: 'anywhere', pl: 3, pt: 0.25 }}
        >
          Refining “{detail.prompt}”
        </Typography>
      )}

      {!refining &&
        detail &&
        (detail.explanation ||
          detail.answeredBy ||
          detail.problems?.length ||
          detail.severity === 'warning') && (
          <Box sx={{ pl: 3, pt: 0.25 }}>
            {detail.explanation && (
              <Typography variant="body2" color="text.secondary">
                {detail.explanation}
              </Typography>
            )}
            {detail.severity === 'warning' && (
              <Typography variant="body2" color="text.secondary">
                The generated query did not validate. Edit it and run it
                yourself.
              </Typography>
            )}
            {detail.problems?.map((problem) => (
              <Typography
                key={problem}
                variant="caption"
                component="div"
                color={detail.severity === 'info' ? 'text.secondary' : accent}
                sx={{ overflowWrap: 'anywhere' }}
              >
                {problem}
              </Typography>
            ))}
            {detail.answeredBy && (
              <Typography
                variant="caption"
                component="div"
                color="text.secondary"
                sx={{ fontFamily: fontFamilyMono, mt: 0.25 }}
              >
                {detail.answeredBy}
              </Typography>
            )}
          </Box>
        )}
    </Box>
  );
}

function CypherInput({
  value,
  onChange,
  onRun,
  readOnly,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  readOnly: boolean;
  inputRef?: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <InputBase
      multiline
      fullWidth
      minRows={1}
      maxRows={QUERY_MAX_ROWS}
      value={value}
      readOnly={readOnly}
      inputRef={inputRef}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          onRun();
        }
      }}
      placeholder={`MATCH (n) RETURN n LIMIT 25 · ${RUN_SHORTCUT} to run`}
      inputProps={{ 'aria-label': 'Cypher query', spellCheck: false }}
      sx={{ alignSelf: 'center', ...cypherInputSx }}
    />
  );
}

function RunButton({
  disabled,
  onRun,
}: {
  disabled: boolean;
  onRun: () => void;
}) {
  return (
    <Tooltip title={`Run query (${RUN_SHORTCUT})`}>
      <span style={{ flexShrink: 0 }}>
        <IconButton
          aria-label="Run query"
          onClick={onRun}
          disabled={disabled}
          size="small"
          sx={primaryButtonSx}
        >
          <PlayArrow fontSize="small" />
        </IconButton>
      </span>
    </Tooltip>
  );
}

/** Seconds since a generation started, so a long wait reads as progress. */
function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      aria-live="off"
      sx={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}
    >
      Generating · {seconds}s
    </Typography>
  );
}
