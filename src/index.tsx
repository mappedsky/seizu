import createCache from '@emotion/cache';
import { CacheProvider } from '@emotion/react';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { getCspNonce, preinjectReactDraggableStyle } from './cspNonce';

const cspNonce = getCspNonce();
preinjectReactDraggableStyle(cspNonce);

const container = document.getElementById('root') as HTMLElement;
const root = createRoot(container);
const emotionCache = createCache({
  key: 'mui',
  nonce: cspNonce,
});

// StrictMode double-invokes renders and effects in development so impure
// renders and missing cleanup fail here rather than in production (UI-001).
root.render(
  <StrictMode>
    <CacheProvider value={emotionCache}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </CacheProvider>
  </StrictMode>,
);
