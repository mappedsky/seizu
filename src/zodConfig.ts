// Run Zod without its JIT. Zod probes for `eval` with `Function('')` before
// compiling object parsers; the app's CSP (`script-src 'self'`) refuses that
// and the browser reports a violation on every page that loads a Zod schema
// (the AI SDK, on the chat pages). `jitless` skips the probe entirely.
//
// Imported first in `index.tsx`: the probe runs when an object schema is
// constructed, so this must evaluate before any module that builds one.
import { config } from 'zod';

config({ jitless: true });
