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

const DashboardLayoutContent = styled('main')({
  flex: '1 1 auto',
  height: '100%',
  overflow: 'auto',
  // Focused by the skip link, which is a jump rather than a control.
  '&:focus': { outline: 'none' },
});

/** The first stop for the keyboard: hidden until focused, it jumps past the
 *  app bar and sidebar to the page itself. */
const SkipLink = styled('a')(({ theme }) => ({
  ...theme.typography.body2,
  backgroundColor: theme.palette.primary.main,
  borderRadius: theme.shape.borderRadius,
  color: theme.palette.primary.contrastText,
  fontWeight: 600,
  left: theme.spacing(2),
  padding: theme.spacing(1, 2),
  position: 'fixed',
  textDecoration: 'none',
  top: theme.spacing(1.5),
  transform: 'translateY(-200%)',
  zIndex: theme.zIndex.tooltip + 1,
  '&:focus': { transform: 'none' },
}));

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
      <SkipLink
        href="#content"
        onClick={(event) => {
          // Move focus without the anchor's scroll, which would tuck the top
          // of the page under the pinned toolbar.
          event.preventDefault();
          document.getElementById('content')?.focus({ preventScroll: true });
        }}
      >
        Skip to content
      </SkipLink>
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
          <DashboardLayoutContent id="content" tabIndex={-1}>
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
