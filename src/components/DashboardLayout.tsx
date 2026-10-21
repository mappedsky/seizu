import { Suspense, useState, type CSSProperties } from 'react';
import { Outlet } from 'react-router-dom';
import PageLoading from 'src/components/PageLoading';
import { styled } from '@mui/material';
import DashboardNavbar from 'src/components/DashboardNavbar';
import DashboardSidebar from 'src/components/DashboardSidebar';
import {
  DASHBOARD_NAVBAR_HEIGHT,
  DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY,
  DASHBOARD_SIDEBAR_COLLAPSED_WIDTH,
  DASHBOARD_SIDEBAR_EXPANDED_WIDTH,
  DASHBOARD_SIDEBAR_WIDTH_VAR,
} from 'src/components/dashboardLayoutConstants';

const DashboardLayoutRoot = styled('div')(({ theme }) => ({
  backgroundColor: theme.palette.background.default,
  display: 'flex',
  height: '100%',
  overflow: 'hidden',
  width: '100%',
}));

const DashboardLayoutWrapper = styled('div')(({ theme }) => ({
  display: 'flex',
  flex: '1 1 auto',
  overflow: 'hidden',
  paddingTop: DASHBOARD_NAVBAR_HEIGHT,
  transition: theme.transitions.create('padding-left', {
    duration: theme.transitions.duration.shorter,
  }),
  [theme.breakpoints.up('lg')]: {
    paddingLeft: `var(${DASHBOARD_SIDEBAR_WIDTH_VAR})`,
  },
}));

const DashboardLayoutContainer = styled('div')({
  display: 'flex',
  flex: '1 1 auto',
  overflow: 'hidden',
});

const DashboardLayoutContent = styled('div')({
  flex: '1 1 auto',
  height: '100%',
  overflow: 'auto',
});

function getInitialSidebarCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return (
      window.localStorage.getItem(DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY) ===
      'true'
    );
  } catch {
    return false;
  }
}

function persistSidebarCollapsed(collapsed: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      DASHBOARD_SIDEBAR_COLLAPSED_STORAGE_KEY,
      String(collapsed),
    );
  } catch {
    // Ignore storage failures so the layout still behaves normally in private mode.
  }
}

function DashboardLayout() {
  const [isMobileNavOpen, setMobileNavOpen] = useState(false);
  const [isSidebarCollapsed, setSidebarCollapsed] = useState(
    getInitialSidebarCollapsed,
  );
  const sidebarWidth = isSidebarCollapsed
    ? DASHBOARD_SIDEBAR_COLLAPSED_WIDTH
    : DASHBOARD_SIDEBAR_EXPANDED_WIDTH;

  const handleSidebarToggle = () => {
    setSidebarCollapsed((collapsed) => {
      const nextCollapsed = !collapsed;
      persistSidebarCollapsed(nextCollapsed);
      return nextCollapsed;
    });
  };

  return (
    <DashboardLayoutRoot
      style={
        { [DASHBOARD_SIDEBAR_WIDTH_VAR]: `${sidebarWidth}px` } as CSSProperties
      }
    >
      <DashboardNavbar
        onMobileNavOpen={() => setMobileNavOpen(true)}
        onSidebarToggle={handleSidebarToggle}
        sidebarCollapsed={isSidebarCollapsed}
      />
      <DashboardSidebar
        collapsed={isSidebarCollapsed}
        onMobileClose={() => setMobileNavOpen(false)}
        openMobile={isMobileNavOpen}
      />
      <DashboardLayoutWrapper>
        <DashboardLayoutContainer>
          <DashboardLayoutContent>
            <Suspense fallback={<PageLoading />}>
              <Outlet />
            </Suspense>
          </DashboardLayoutContent>
        </DashboardLayoutContainer>
      </DashboardLayoutWrapper>
    </DashboardLayoutRoot>
  );
}

export default DashboardLayout;
