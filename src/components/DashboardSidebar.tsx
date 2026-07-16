import { Link as RouterLink } from 'react-router-dom';
import { Box, Drawer, List } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import Dashboard from '@mui/icons-material/Dashboard';
import Insights from '@mui/icons-material/Insights';
import Article from '@mui/icons-material/Article';
import Terminal from '@mui/icons-material/Terminal';
import Chat from '@mui/icons-material/Chat';
import Schedule from '@mui/icons-material/Schedule';
import EventRepeat from '@mui/icons-material/EventRepeat';
import Extension from '@mui/icons-material/Extension';
import Psychology from '@mui/icons-material/Psychology';
import AdminPanelSettings from '@mui/icons-material/AdminPanelSettings';
import NavItem, { NavItemData } from 'src/components/NavItem';
import Hidden from 'src/components/Hidden';
import { useReportsList } from 'src/hooks/useReportsApi';
import { usePermissions } from 'src/hooks/usePermissions';
import { useFeature } from 'src/features.context';
import {
  DASHBOARD_SIDEBAR_EXPANDED_WIDTH,
  DASHBOARD_SIDEBAR_WIDTH_VAR,
} from 'src/components/dashboardLayoutConstants';

interface DashboardSidebarProps {
  collapsed?: boolean;
  onMobileClose?: () => void;
  openMobile?: boolean;
}

function DashboardSidebar({
  collapsed = false,
  onMobileClose = () => {},
  openMobile = false,
}: DashboardSidebarProps) {
  const theme = useTheme();
  const { reports } = useReportsList();
  const hasPermission = usePermissions();
  const chatEnabled = useFeature('chat');
  const canUseChat = chatEnabled && hasPermission('chat:use');
  const chatSchedulesEnabled = useFeature('chat_schedules');
  const canScheduleChats =
    chatSchedulesEnabled && hasPermission('chat:schedule');
  const logoSrc = collapsed
    ? theme.palette.mode === 'dark'
      ? '/static/images/logo-mark.svg'
      : '/static/images/logo-mark-light.svg'
    : theme.palette.mode === 'dark'
      ? '/static/images/logo-horizontal-white.svg'
      : '/static/images/logo-horizontal-black.svg';
  const reportSubitems: NavItemData[] = reports
    .filter((report) => report.pinned)
    .map((report) => ({
      href: `/app/reports/${report.report_id}`,
      title: report.name,
      icon: Article,
    }));
  const items: NavItemData[] = [
    {
      href: '/app/dashboard',
      icon: Dashboard,
      title: 'Dashboard',
    },
    {
      href: '/app/reports',
      icon: Insights,
      title: 'Reports',
      subItems: reportSubitems.length > 0 ? reportSubitems : undefined,
    },
    ...(hasPermission('query:execute')
      ? [
          {
            href: '/app/query-console',
            icon: Terminal,
            title: 'Query Console',
          },
        ]
      : []),
    ...(canUseChat
      ? [
          {
            href: '/app/chat',
            icon: Chat,
            title: 'Chat',
          },
        ]
      : []),
    {
      href: '/app/workflows',
      icon: Schedule,
      title: 'Workflows',
    },
    ...(canScheduleChats
      ? [
          {
            href: '/app/scheduled-chats',
            icon: EventRepeat,
            title: 'Scheduled Chats',
          },
        ]
      : []),
    {
      href: '/app/toolsets',
      icon: Extension,
      title: 'MCP Toolsets',
    },
    {
      href: '/app/skillsets',
      icon: Psychology,
      title: 'MCP Skillsets',
    },
    ...(hasPermission('roles:read')
      ? [
          {
            href: '/app/roles',
            icon: AdminPanelSettings,
            title: 'Roles',
          },
        ]
      : []),
  ];

  const content = (isCollapsed: boolean) => (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflowX: 'hidden',
      }}
    >
      <Box
        component={RouterLink}
        to="/"
        sx={{
          alignItems: 'center',
          display: 'flex',
          height: 68,
          justifyContent: isCollapsed ? 'center' : 'flex-start',
          px: isCollapsed ? 1 : 2,
          textDecoration: 'none',
        }}
      >
        <Box
          component="img"
          alt="Seizu"
          src={logoSrc}
          sx={{
            display: 'block',
            height: isCollapsed ? 34 : 42,
            maxWidth: '100%',
            objectFit: 'contain',
          }}
        />
      </Box>
      <Box sx={{ p: isCollapsed ? 1 : 2 }}>
        <List>
          {items.map((item) => (
            <NavItem
              collapsed={isCollapsed}
              href={item.href}
              key={item.title}
              title={item.title}
              icon={item.icon}
              subItems={item.subItems}
            />
          ))}
        </List>
      </Box>
    </Box>
  );

  return (
    <>
      <Hidden lgUp>
        <Drawer
          anchor="left"
          onClose={onMobileClose}
          open={openMobile}
          variant="temporary"
          slotProps={{
            paper: {
              sx: {
                width: DASHBOARD_SIDEBAR_EXPANDED_WIDTH,
              },
            },
          }}
        >
          {content(false)}
        </Drawer>
      </Hidden>
      <Hidden>
        <Drawer
          anchor="left"
          open
          variant="persistent"
          slotProps={{
            paper: {
              sx: (theme) => ({
                width: `var(${DASHBOARD_SIDEBAR_WIDTH_VAR})`,
                top: 0,
                height: '100%',
                overflowX: 'hidden',
                transition: theme.transitions.create('width', {
                  duration: theme.transitions.duration.shorter,
                }),
              }),
            },
          }}
        >
          {content(collapsed)}
        </Drawer>
      </Hidden>
    </>
  );
}

export default DashboardSidebar;
