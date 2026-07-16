import { GlobalRegistrator } from '@happy-dom/global-registrator';

// Register the DOM before the test setup imports React Testing Library.
// Static ESM imports are evaluated before a module body, so this must remain
// a separate, earlier Bun preload.
GlobalRegistrator.register({ width: 1920, height: 1080 });
