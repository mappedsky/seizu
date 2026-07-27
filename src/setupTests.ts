import '@testing-library/jest-dom';
import { cleanup, configure } from '@testing-library/react';

// React Testing Library's automatic cleanup does not reliably register under
// Bun. Explicit global teardown prevents mounted components, dialogs, and
// hooks from leaking into later tests or test files.
afterEach(cleanup);

// The whole suite shares one Bun process and per-test wall time grows several
// times over as it progresses, so findBy*/waitFor calls that resolve in well
// under the 1s default when a file runs alone can exceed it late in a full
// run. That surfaced as a timeout on a different heavy test each run. This is
// headroom for that drift; it does not slow passing tests, since these helpers
// resolve as soon as the condition holds.
configure({ asyncUtilTimeout: 5000 });

import { TextEncoder, TextDecoder } from 'util';

(global as Record<string, unknown>).TextEncoder = TextEncoder;
(global as Record<string, unknown>).TextDecoder = TextDecoder;
