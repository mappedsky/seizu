import React from 'react';
import { useTheme } from '@mui/material/styles';

function Logo(props: React.ImgHTMLAttributes<HTMLImageElement>) {
  const theme = useTheme();
  // Logo sits on the app bar, which uses background.paper: a white wordmark
  // on the dark surface, a black one on the light surface.
  const src =
    theme.palette.mode === 'dark'
      ? '/static/images/logo-horizontal-white.svg'
      : '/static/images/logo-horizontal-black.svg';
  return <img alt="Seizu" src={src} height="50" {...props} />;
}

export default Logo;
