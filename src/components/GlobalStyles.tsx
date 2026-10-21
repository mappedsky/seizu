import { GlobalStyles as MuiGlobalStyles } from '@mui/material';
import { fontFamilyMono } from 'src/theme/typography';

function GlobalStyles() {
  return (
    <MuiGlobalStyles
      styles={{
        '*': {
          boxSizing: 'border-box',
          margin: 0,
          padding: 0,
        },
        html: {
          WebkitFontSmoothing: 'antialiased',
          MozOsxFontSmoothing: 'grayscale',
        },
        body: {
          height: '100%',
          width: '100%',
        },
        a: {
          textDecoration: 'none',
        },
        'code, kbd, samp, pre': {
          fontFamily: fontFamilyMono,
        },
        // Each grid cell is CSS-transformed, so it is its own stacking
        // context: a chart tooltip inside one cannot paint above the next
        // panel at any z-index. Lift the panel being used above its
        // neighbours instead (react-grid-layout drags at the same level).
        '.report-row .react-grid-item:hover, .report-row .react-grid-item:focus-within':
          {
            zIndex: 3,
          },
        // Figures in a column read down as well as across.
        'td, th': {
          fontVariantNumeric: 'tabular-nums',
        },
        '#root': {
          height: '100%',
          width: '100%',
        },
        '.mui-markdown-ol': {
          paddingLeft: 40,
          paddingTop: 10,
          paddingBottom: 10,
        },
        '.mui-markdown-ul': {
          paddingLeft: 40,
          paddingTop: 10,
          paddingBottom: 10,
        },
      }}
    />
  );
}

export default GlobalStyles;
