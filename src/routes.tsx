import { Navigate } from 'react-router-dom';
import DashboardLayout from 'src/components/DashboardLayout';
import MainLayout from 'src/components/MainLayout';
import Dashboard from 'src/pages/Dashboard';
import Reports from 'src/pages/Reports';
import ReportHistory from 'src/pages/ReportHistory';
import ReportVersionView from 'src/pages/ReportVersionView';
import ReportsList from 'src/pages/ReportsList';
import NotFound from 'src/pages/NotFound';
import QueryConsole from 'src/pages/QueryConsole';
import ChatInterface from 'src/pages/ChatInterface';
import BatchConfirmationPage from 'src/pages/BatchConfirmationPage';
import ConfirmationPage from 'src/pages/ConfirmationPage';
import ScheduledQueries from 'src/pages/ScheduledQueries';
import ScheduledQueryHistory from 'src/pages/ScheduledQueryHistory';
import ScheduledChats from 'src/pages/ScheduledChats';
import ScheduledChatView from 'src/pages/ScheduledChatView';
import ScheduledChatHistory from 'src/pages/ScheduledChatHistory';
import Toolsets from 'src/pages/Toolsets';
import ToolsetTools from 'src/pages/ToolsetTools';
import ToolsetHistory from 'src/pages/ToolsetHistory';
import ToolHistory from 'src/pages/ToolHistory';
import Skillsets from 'src/pages/Skillsets';
import SkillsetSkills from 'src/pages/SkillsetSkills';
import SkillsetHistory from 'src/pages/SkillsetHistory';
import SkillHistory from 'src/pages/SkillHistory';
import Roles from 'src/pages/Roles';
import RoleHistory from 'src/pages/RoleHistory';
import LoggedOut from 'src/pages/LoggedOut';

const routes = [
  {
    path: 'app',
    element: <DashboardLayout />,
    children: [
      { path: 'dashboard', element: <Dashboard /> },
      { path: 'reports', element: <ReportsList /> },
      { path: 'reports/:id', element: <Reports /> },
      { path: 'reports/:id/history', element: <ReportHistory /> },
      { path: 'reports/:id/versions/:version', element: <ReportVersionView /> },
      { path: 'query-console', element: <QueryConsole /> },
      { path: 'chat', element: <ChatInterface /> },
      { path: 'chat/:threadId', element: <ChatInterface /> },
      {
        path: 'confirmations/batch/:batchId',
        element: <BatchConfirmationPage />,
      },
      { path: 'confirmations/:confirmationId', element: <ConfirmationPage /> },
      { path: 'scheduled-queries', element: <ScheduledQueries /> },
      {
        path: 'scheduled-queries/:id/history',
        element: <ScheduledQueryHistory />,
      },
      { path: 'scheduled-chats', element: <ScheduledChats /> },
      { path: 'scheduled-chats/:id', element: <ScheduledChatView /> },
      {
        path: 'scheduled-chats/:id/history',
        element: <ScheduledChatHistory />,
      },
      { path: 'toolsets', element: <Toolsets /> },
      { path: 'toolsets/:toolsetId/tools', element: <ToolsetTools /> },
      { path: 'toolsets/:toolsetId/history', element: <ToolsetHistory /> },
      {
        path: 'toolsets/:toolsetId/tools/:toolId/history',
        element: <ToolHistory />,
      },
      { path: 'skillsets', element: <Skillsets /> },
      { path: 'skillsets/:skillsetId/skills', element: <SkillsetSkills /> },
      { path: 'skillsets/:skillsetId/history', element: <SkillsetHistory /> },
      {
        path: 'skillsets/:skillsetId/skills/:skillId/history',
        element: <SkillHistory />,
      },
      { path: 'roles', element: <Roles /> },
      { path: 'roles/:roleId/history', element: <RoleHistory /> },
      // Unknown /app/* paths render the 404 page inside the standard
      // dashboard chrome (navbar + sidebar) so it matches the rest of the app.
      { path: '*', element: <NotFound /> },
    ],
  },
  {
    path: '/',
    element: <MainLayout />,
    children: [
      { path: 'logged-out', element: <LoggedOut /> },
      { path: '/', element: <Navigate to="/app/dashboard" /> },
    ],
  },
  // Any other unmatched top-level path also lands on the dashboard-framed 404,
  // preserving the requested URL rather than redirecting to a bespoke route.
  {
    path: '*',
    element: <DashboardLayout />,
    children: [{ path: '*', element: <NotFound /> }],
  },
];

export default routes;
