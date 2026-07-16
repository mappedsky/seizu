import '@testing-library/jest-dom';
import { cleanup } from '@testing-library/react';

// React Testing Library's automatic cleanup does not reliably register under
// Bun. Explicit global teardown prevents mounted components, dialogs, and
// hooks from leaking into later tests or test files.
afterEach(cleanup);

import { TextEncoder, TextDecoder } from 'util';

(global as Record<string, unknown>).TextEncoder = TextEncoder;
(global as Record<string, unknown>).TextDecoder = TextDecoder;
