import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Box, Card, CardContent, Typography } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import CypherGraph from 'src/components/reports/CypherGraph';
import QueryComposer, {
  type ComposerMode,
  type QueryProvenance,
} from 'src/components/QueryComposer';
import QueryConsoleSchemaPanel from 'src/components/QueryConsoleSchemaPanel';
import { useFeature } from 'src/features.context';
import {
  type GeneratedQuery,
  useGenerateQuery,
  useQueryConsoleModels,
} from 'src/hooks/useGenerateQuery';
import type { ReasoningEffort } from 'src/hooks/useModelProfilesApi';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  useFetchHistoryItem,
  QueryHistoryItem,
} from 'src/hooks/useQueryHistory';
import { pageContentSx } from 'src/theme/layout';
import { useSidePanel } from 'src/hooks/useSidePanel';

/** Exactly one thing is running: a typed query, or a stored history entry. */
type ConsoleRun =
  | { kind: 'query'; query: string; key: number }
  | { kind: 'history'; historyId: string; key: number };

const QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY =
  'seizu:query-console:schema-panel-open';
const QUERY_CONSOLE_MODE_STORAGE_KEY = 'seizu:query-console:composer-mode';
const QUERY_CONSOLE_MODEL_STORAGE_KEY = 'seizu:query-console:model';

interface StoredModelPick {
  profileId: string;
  reasoningEffort: ReasoningEffort;
}

function readStored<T>(
  key: string,
  parse: (raw: string) => T | null,
): T | null {
  if (typeof window === 'undefined') return null;
  const raw = window.localStorage.getItem(key);
  if (raw === null) return null;
  try {
    return parse(raw);
  } catch {
    return null;
  }
}

/** "Accurate · low", or the model id when the deployment has no profiles. */
function answeredBy(result: GeneratedQuery): string | undefined {
  const source = result.profile_name || result.model_id;
  if (!source) return undefined;
  return result.reasoning_effort
    ? `${source} · ${result.reasoning_effort}`
    : source;
}

export default function QueryConsole() {
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const navigate = useNavigate();
  const location = useLocation();
  const fetchHistoryItem = useFetchHistoryItem();
  const generateQuery = useGenerateQuery();
  const generationEnabled = useFeature('query_generation');
  const [queryText, setQueryText] = useState('');
  const [questionText, setQuestionText] = useState('');
  const [modeChoice, setModeChoice] = useState<ComposerMode>(
    () =>
      readStored(QUERY_CONSOLE_MODE_STORAGE_KEY, (raw) =>
        raw === 'ask' ? 'ask' : null,
      ) ?? 'cypher',
  );
  const [modelChoice, setModelChoice] = useState<StoredModelPick | null>(() =>
    readStored(QUERY_CONSOLE_MODEL_STORAGE_KEY, (raw) => {
      const parsed = JSON.parse(raw) as Partial<StoredModelPick>;
      return parsed.profileId && parsed.reasoningEffort
        ? (parsed as StoredModelPick)
        : null;
    }),
  );
  const [generatingSince, setGeneratingSince] = useState<number | null>(null);
  const [note, setNote] = useState<QueryProvenance | null>(null);
  // Only the latest question's answer may land; an earlier one that returns
  // late, or one that returns after the user ran something else, is dropped.
  const generationSeqRef = useRef(0);
  const generationAbortRef = useRef<AbortController | null>(null);
  // What this page is showing, and nothing else decides it. `location` is
  // deliberately absent from this: deriving the run from the address bar means
  // re-deriving it while our own navigate() is still settling, and a completed
  // query that publishes its URL then reads back as a request to run something,
  // which publishes again. The console re-ran one query 222 times that way.
  const [run, setRun] = useState<ConsoleRun | null>(null);
  // Every `?h=` this page put in the address bar, and the last URL the restore
  // below has already acted on. Both are refs mutated only from callbacks and
  // never cleared on read, so a double-invoked effect or render repeats no work
  // -- unlike the `justPushedRef` this replaces, which cleared its flag as it
  // read it and so was defeated by StrictMode's second pass.
  const ownHistoryIds = useRef<Set<string>>(new Set());
  const handledSearch = useRef<string | null>(null);

  const [schemaPanelOpen, setSchemaPanelOpen] = useState(() => {
    if (typeof window === 'undefined') return true;
    const storedValue = window.localStorage.getItem(
      QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY,
    );
    return storedValue === null ? true : storedValue === 'true';
  });
  const schemaPanel = useSidePanel(schemaPanelOpen, setSchemaPanelOpen);
  const { dismiss: dismissSchemaPanel, setOpen: setSchemaPanelShown } =
    schemaPanel;
  const [historyRefreshTrigger, setHistoryRefreshTrigger] = useState(0);

  const handleQueryComplete = useCallback(
    (historyId: string | null) => {
      // Null for a re-executed history entry, which creates no new record and
      // so has nothing to publish.
      if (!historyId) return;
      setHistoryRefreshTrigger((n) => n + 1);
      // Claimed before navigating, so the restore below never treats this
      // page's own URL as somewhere the user asked to go.
      ownHistoryIds.current.add(historyId);
      handledSearch.current = `?h=${historyId}`;
      navigate(`?h=${historyId}`);
    },
    [navigate],
  );

  const submittedQuery = run?.kind === 'query' ? run.query : undefined;
  const submittedHistoryId =
    run?.kind === 'history' ? run.historyId : undefined;
  const runKey = run?.key ?? 0;

  // The address bar is an *input* only when it names a history entry this page
  // did not publish: a back/forward, or a link opened cold. That is a change in
  // an external system (the history stack) rather than a value this render
  // could derive, so it is read here and acted on once per URL -- the narrow
  // case UI-003 leaves to an effect.
  useEffect(() => {
    if (handledSearch.current === location.search) return undefined;
    handledSearch.current = location.search;

    const historyId = new URLSearchParams(location.search).get('h');
    if (!historyId || ownHistoryIds.current.has(historyId)) return undefined;
    ownHistoryIds.current.add(historyId);

    const search = location.search;
    // The one place this page writes run state from an effect, because the
    // history stack is an external system rather than something this render
    // can derive -- the carve-out UI-003 names, recorded as UI-005.
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- see UI-005
    setRun((previous) => ({
      kind: 'history',
      historyId,
      key: (previous?.key ?? 0) + 1,
    }));
    void fetchHistoryItem(historyId).then((item) => {
      // Stale only once the page has moved to another URL. Not a cleanup
      // flag: StrictMode's re-run returns early above, so a flag set by the
      // first run's cleanup would drop the only fetch there is.
      if (handledSearch.current !== search || !item) return;
      setQueryText(item.query);
      if (item.prompt) {
        setNote({ historyId, prompt: item.prompt, severity: 'info' });
      }
    });
    return undefined;
    // `fetchHistoryItem` resolves null until there is a token, so the editor
    // text needs no gate of its own here.
  }, [location.search, fetchHistoryItem]);

  const queryTextRef = useRef(queryText);
  queryTextRef.current = queryText;

  /** Drop any generation in flight; its answer will be ignored if it lands. */
  const stopGenerating = useCallback(() => {
    generationSeqRef.current += 1;
    generationAbortRef.current?.abort();
    generationAbortRef.current = null;
    setGeneratingSince(null);
  }, []);

  // Leaving the page drops a generation in flight, or its answer would land
  // and navigate() the user back here.
  useEffect(
    () => () => {
      generationSeqRef.current += 1;
      generationAbortRef.current?.abort();
      generationAbortRef.current = null;
    },
    [],
  );

  const handleRun = useCallback(() => {
    const trimmed = queryTextRef.current.trim();
    if (!trimmed) return;
    stopGenerating();
    // The query being run is the user's own now, so the question it may have
    // come from no longer describes it.
    setNote(null);
    setRun((previous) => ({
      kind: 'query',
      query: trimmed,
      key: (previous?.key ?? 0) + 1,
    }));
  }, []);

  const questionTextRef = useRef(questionText);
  questionTextRef.current = questionText;

  const modelPickRef = useRef<StoredModelPick | null>(null);

  /** Turn the question into a query, put it in the editor, and run it if it validated. */
  const handleGenerate = useCallback(async () => {
    const question = questionTextRef.current.trim();
    if (!question) return;
    stopGenerating();
    const seq = generationSeqRef.current;
    const controller = new AbortController();
    generationAbortRef.current = controller;
    setGeneratingSince(performance.now());
    setNote(null);
    const pick = modelPickRef.current;
    const outcome = await generateQuery(question, {
      currentQuery: queryTextRef.current,
      modelProfileId: pick?.profileId,
      reasoningEffort: pick?.reasoningEffort,
      signal: controller.signal,
    });
    if (seq !== generationSeqRef.current) return;
    generationAbortRef.current = null;
    setGeneratingSince(null);

    if (!outcome.ok) {
      setNote({
        historyId: null,
        prompt: question,
        problems: [outcome.error],
        severity: 'error',
      });
      return;
    }

    const { result } = outcome;
    const by = answeredBy(result);
    if (!result.query) {
      setNote({
        historyId: null,
        prompt: question,
        answeredBy: by,
        explanation: result.explanation,
        problems: result.errors.length
          ? result.errors
          : ['No query was generated.'],
        severity: 'error',
      });
      return;
    }

    setQueryText(result.query);
    // The question now lives above the query it produced; the field is left
    // empty for the next one.
    setQuestionText('');
    setModeChoice('cypher');
    if (result.errors.length) {
      // Shown for editing, not run: the validator would only refuse it again.
      setNote({
        historyId: null,
        prompt: question,
        answeredBy: by,
        explanation: result.explanation,
        problems: result.errors,
        severity: 'warning',
      });
      return;
    }

    setNote({
      historyId: result.history_id,
      prompt: question,
      answeredBy: by,
      explanation: result.explanation,
      problems: result.warnings.length ? result.warnings : undefined,
      severity: 'info',
    });
    if (result.history_id) {
      // Already recorded with its question, so it runs by id and adds no
      // second, prompt-less history entry.
      const historyId = result.history_id;
      ownHistoryIds.current.add(historyId);
      handledSearch.current = `?h=${historyId}`;
      setHistoryRefreshTrigger((n) => n + 1);
      setRun((previous) => ({
        kind: 'history',
        historyId,
        key: (previous?.key ?? 0) + 1,
      }));
      navigate(`?h=${historyId}`);
    } else {
      setRun((previous) => ({
        kind: 'query',
        query: result.query,
        key: (previous?.key ?? 0) + 1,
      }));
    }
  }, [generateQuery, navigate, stopGenerating]);

  /** Insert a query from the schema browser and run it immediately. */
  const handleQuerySelect = useCallback(
    (query: string) => {
      stopGenerating();
      setNote(null);
      setQueryText(query);
      setRun((previous) => ({
        kind: 'query',
        query,
        key: (previous?.key ?? 0) + 1,
      }));
      dismissSchemaPanel();
    },
    [dismissSchemaPanel, stopGenerating],
  );

  /** Load a query from history into the editor and re-execute by history ID. */
  const handleHistorySelect = useCallback(
    (item: QueryHistoryItem) => {
      stopGenerating();
      setQueryText(item.query);
      setNote(
        item.prompt
          ? {
              historyId: item.history_id,
              prompt: item.prompt,
              severity: 'info',
            }
          : null,
      );
      setModeChoice('cypher');
      ownHistoryIds.current.add(item.history_id);
      handledSearch.current = `?h=${item.history_id}`;
      setRun((previous) => ({
        kind: 'history',
        historyId: item.history_id,
        key: (previous?.key ?? 0) + 1,
      }));
      navigate(`?h=${item.history_id}`);
      dismissSchemaPanel();
    },
    [navigate, dismissSchemaPanel, stopGenerating],
  );

  /** Start from scratch: empty editor and question, no result, no `?h=`. */
  const handleNewQuery = useCallback(() => {
    stopGenerating();
    setQueryText('');
    setQuestionText('');
    setNote(null);
    setRun(null);
    // Nothing this page published is on screen any more, so Back to one of
    // those entries is a request to restore it, not this page's own echo.
    ownHistoryIds.current.clear();
    if (location.search) {
      // Claimed first, so the restore effect reads the bare URL as handled.
      handledSearch.current = '';
      navigate({ search: '' });
    }
  }, [location.search, navigate, stopGenerating]);

  const handleModeChange = useCallback((next: ComposerMode) => {
    setModeChoice(next);
    window.localStorage.setItem(QUERY_CONSOLE_MODE_STORAGE_KEY, next);
  }, []);

  const handleModelChange = useCallback(
    (profileId: string, reasoningEffort: ReasoningEffort) => {
      const next = { profileId, reasoningEffort };
      setModelChoice(next);
      window.localStorage.setItem(
        QUERY_CONSOLE_MODEL_STORAGE_KEY,
        JSON.stringify(next),
      );
    },
    [],
  );

  const handleSchemaPanelToggle = useCallback(
    (tab?: 'schema' | 'history') => {
      if (tab) {
        setSchemaPanelShown(true);
        return;
      }
      setSchemaPanelShown((value) => !value);
    },
    [setSchemaPanelShown],
  );

  useEffect(() => {
    window.localStorage.setItem(
      QUERY_CONSOLE_SCHEMA_PANEL_STORAGE_KEY,
      String(schemaPanelOpen),
    );
  }, [schemaPanelOpen]);

  const generationAllowed = generationEnabled && hasPermission('chat:use');
  // Which profiles and levels the profiles enable for the console; with no
  // profiles at all the deployment's own settings apply and there is nothing
  // to pick.
  const { options: consoleModels } = useQueryConsoleModels(generationAllowed);
  const canGenerate = generationAllowed && consoleModels.available;
  // A mode the user can no longer use falls back to Cypher rather than
  // leaving them in a composer whose button cannot work.
  const mode: ComposerMode = canGenerate ? modeChoice : 'cypher';
  const visibleNote =
    note &&
    (note.historyId === null ||
      (run?.kind === 'history' && run.historyId === note.historyId))
      ? note
      : null;

  const modelProfiles = consoleModels.profiles;
  const storedProfile = modelChoice
    ? modelProfiles.find((item) => item.profile_id === modelChoice.profileId)
    : undefined;
  const pickedProfile =
    storedProfile ??
    modelProfiles.find(
      (item) => item.profile_id === consoleModels.default_profile_id,
    );
  // A remembered level the profile no longer offers falls back to its default.
  const pickedEffort: ReasoningEffort | '' = pickedProfile
    ? storedProfile &&
      modelChoice &&
      storedProfile.reasoning_efforts.includes(modelChoice.reasoningEffort)
      ? modelChoice.reasoningEffort
      : pickedProfile.default_reasoning_effort
    : '';
  modelPickRef.current =
    pickedProfile && pickedEffort
      ? { profileId: pickedProfile.profile_id, reasoningEffort: pickedEffort }
      : null;
  // One profile at one level is not a choice: the picker stays out of the way.
  const choiceCount = modelProfiles.reduce(
    (count, item) => count + item.reasoning_efforts.length,
    0,
  );

  if (permissionsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (!hasPermission('query:execute')) {
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to the query console.</Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden' }}
    >
      {/* Side panel (schema / history) */}
      <QueryConsoleSchemaPanel
        open={schemaPanel.open}
        overlay={schemaPanel.overlay}
        onToggle={handleSchemaPanelToggle}
        onQuerySelect={handleQuerySelect}
        onHistorySelect={handleHistorySelect}
        historyRefreshTrigger={historyRefreshTrigger}
      />

      {/* Main content */}
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          ...pageContentSx,
          boxSizing: 'border-box',
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        {/* Graph panel */}
        <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          {submittedQuery || submittedHistoryId ? (
            <CypherGraph
              cypher={submittedHistoryId ? undefined : submittedQuery}
              queryHistoryId={submittedHistoryId}
              fillHeight
              refreshKey={runKey}
              onQueryComplete={handleQueryComplete}
            />
          ) : (
            <Card
              sx={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <CardContent>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  align="center"
                >
                  Run a query below to visualize the graph.
                </Typography>
              </CardContent>
            </Card>
          )}
        </Box>

        <QueryComposer
          canGenerate={canGenerate}
          mode={mode}
          onModeChange={handleModeChange}
          queryText={queryText}
          onQueryTextChange={setQueryText}
          onRun={handleRun}
          questionText={questionText}
          onQuestionTextChange={setQuestionText}
          onGenerate={() => void handleGenerate()}
          onCancelGenerate={stopGenerating}
          generatingSince={generatingSince}
          provenance={visibleNote}
          onDismissProvenance={() => setNote(null)}
          onRefine={() => handleModeChange('ask')}
          canStartNew={
            run !== null ||
            note !== null ||
            queryText.trim() !== '' ||
            questionText.trim() !== ''
          }
          onNewQuery={handleNewQuery}
          modelPick={
            choiceCount > 1
              ? {
                  profiles: modelProfiles.map((item) => ({
                    ...item,
                    is_default:
                      item.profile_id === consoleModels.default_profile_id,
                  })),
                  profileId: pickedProfile?.profile_id ?? '',
                  reasoningEffort: pickedEffort,
                  onChange: handleModelChange,
                }
              : null
          }
        />
      </Box>
    </Box>
  );
}
