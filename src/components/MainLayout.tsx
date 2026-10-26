import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import PageLoading from 'src/components/PageLoading';
import { styled } from '@mui/material';
import MainNavbar from './MainNavbar';

const MainLayoutRoot = styled('div')(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  display: 'flex',
  height: '100%',
  overflow: 'hidden',
  width: '100%',
}));

const MainLayoutWrapper = styled('div')({
  display: 'flex',
  flex: '1 1 auto',
  overflow: 'hidden',
  paddingTop: 64,
});

const MainLayoutContainer = styled('div')({
  display: 'flex',
  flex: '1 1 auto',
  overflow: 'hidden',
});

const MainLayoutContent = styled('div')({
  flex: '1 1 auto',
  height: '100%',
  overflow: 'auto',
});

function MainLayout() {
  return (
    <MainLayoutRoot>
      <MainNavbar />
      <MainLayoutWrapper>
        <MainLayoutContainer>
          <MainLayoutContent>
            <Suspense fallback={<PageLoading />}>
              <Outlet />
            </Suspense>
          </MainLayoutContent>
        </MainLayoutContainer>
      </MainLayoutWrapper>
    </MainLayoutRoot>
  );
}

export default MainLayout;
