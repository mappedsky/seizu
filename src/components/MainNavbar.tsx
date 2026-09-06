import { Link as RouterLink } from 'react-router-dom';
import { AppBar, Toolbar } from '@mui/material';
import type { AppBarProps } from '@mui/material';
import Logo from './Logo';

function MainNavbar(props: AppBarProps) {
  return (
    <AppBar elevation={0} {...props}>
      <Toolbar sx={{ height: 64 }}>
        <RouterLink to="/">
          <Logo />
        </RouterLink>
      </Toolbar>
    </AppBar>
  );
}

export default MainNavbar;
