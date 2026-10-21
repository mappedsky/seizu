import { useMemo, useState, useEffect } from 'react';
import { useRoutes } from 'react-router-dom';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import useMediaQuery from '@mui/material/useMediaQuery';
import CssBaseline from '@mui/material/CssBaseline';
import GlobalStyles from 'src/components/GlobalStyles';
import AuthProvider from 'src/components/AuthProvider';
import shadows from 'src/theme/shadows';
import { shape, surfaceComponents } from 'src/theme/surfaces';
import typography from 'src/theme/typography';
import { paletteFor } from 'src/theme/palette';
import { contentContainerRootStyles } from 'src/theme/layout';
import routes from 'src/routes';
import {
  AuthConfigContext,
  type AuthConfig,
  type OidcConfig,
} from 'src/authConfig.context';
import {
  FeaturesContext,
  DEFAULT_FEATURES,
  BACKEND_DEFAULT_FEATURES,
  type Features,
} from 'src/features.context';
import { CurrentUserProvider } from 'src/hooks/useCurrentUser';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

function App() {
  const prefersDarkMode = useMediaQuery('(prefers-color-scheme: dark)');
  const [authConfig, setAuthConfig] = useState<AuthConfig>({
    auth_required: true,
    oidc: null,
    loaded: false,
  });
  const [features, setFeatures] = useState<Features>(DEFAULT_FEATURES);

  useEffect(() => {
    fetch('/api/v1/config')
      .then((r) => r.json())
      .then(
        (data: {
          auth_required: boolean;
          oidc: OidcConfig | null;
          features?: Partial<Features>;
        }) => {
          setAuthConfig({
            auth_required: data.auth_required,
            oidc: data.oidc ?? null,
            loaded: true,
          });
          setFeatures({
            ...BACKEND_DEFAULT_FEATURES,
            ...(data.features ?? {}),
          });
        },
      )
      .catch(() => {
        // Keep auth_required:true on error — safe fallback — but mark loaded
        // so AuthProvider stops waiting and runs its (safe-fallback) flow.
        setAuthConfig((prev) => ({ ...prev, loaded: true }));
      });
  }, []);

  const theme = useMemo(
    () =>
      createTheme({
        palette: paletteFor(prefersDarkMode ? 'dark' : 'light'),
        shadows,
        shape,
        typography,
        components: {
          ...surfaceComponents,
          MuiContainer: {
            styleOverrides: {
              root: ({ ownerState, theme }) => ({
                ...contentContainerRootStyles(theme),
                ...(ownerState.disableGutters && {
                  paddingLeft: 0,
                  paddingRight: 0,
                }),
              }),
            },
          },
        },
      }),
    [prefersDarkMode],
  );

  const routing = useRoutes(routes);

  return (
    <AuthConfigContext.Provider value={authConfig}>
      <FeaturesContext.Provider value={features}>
        <ThemeProvider theme={theme}>
          <GlobalStyles />
          {/* enableColorScheme: native scrollbars and form controls follow the
              theme instead of staying light in dark mode. */}
          <CssBaseline enableColorScheme />
          <AuthProvider>
            <CurrentUserProvider>{routing}</CurrentUserProvider>
          </AuthProvider>
        </ThemeProvider>
      </FeaturesContext.Provider>
    </AuthConfigContext.Provider>
  );
}

export default App;
