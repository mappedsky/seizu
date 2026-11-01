import { lazy } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import DashboardLayout from 'src/components/DashboardLayout';
import MainLayout from 'src/components/MainLayout';
import NotFound from 'src/pages/NotFound';
import LoggedOut from 'src/pages/LoggedOut';

// Each page is its own chunk, fetched on first visit; the layouts' Suspense
// boundaries cover the wait.
const Dashboard = lazy(() => import('src/pages/Dashboard'));
const Reports = lazy(() => import('src/pages/Reports'));
const ReportHistory = lazy(() => import('src/pages/ReportHistory'));
const ReportVersionView = lazy(() => import('src/pages/ReportVersionView'));
const ReportsList = lazy(() => import('src/pages/ReportsList'));
const Spaces = lazy(() => import('src/pages/Spaces'));
const SpaceDetail = lazy(() => import('src/pages/SpaceDetail'));
const QueryConsole = lazy(() => import('src/pages/QueryConsole'));
const ChatInterface = lazy(() => import('src/pages/ChatInterface'));
const BatchConfirmationPage = lazy(
  () => import('src/pages/BatchConfirmationPage'),
);
const ConfirmationPage = lazy(() => import('src/pages/ConfirmationPage'));
const ScheduledChats = lazy(() => import('src/pages/ScheduledChats'));
const ScheduledChatView = lazy(() => import('src/pages/ScheduledChatView'));
const ScheduledChatHistory = lazy(
  () => import('src/pages/ScheduledChatHistory'),
);
const Toolsets = lazy(() => import('src/pages/Toolsets'));
const ToolsetTools = lazy(() => import('src/pages/ToolsetTools'));
const ToolsetHistory = lazy(() => import('src/pages/ToolsetHistory'));
const ToolEditor = lazy(() => import('src/pages/ToolEditor'));
const ToolHistory = lazy(() => import('src/pages/ToolHistory'));
const Skillsets = lazy(() => import('src/pages/Skillsets'));
const SkillsetSkills = lazy(() => import('src/pages/SkillsetSkills'));
const SkillsetHistory = lazy(() => import('src/pages/SkillsetHistory'));
const SkillHistory = lazy(() => import('src/pages/SkillHistory'));
const Roles = lazy(() => import('src/pages/Roles'));
const RoleHistory = lazy(() => import('src/pages/RoleHistory'));
const Workflows = lazy(() => import('src/pages/Workflows'));
const WorkflowView = lazy(() => import('src/pages/WorkflowView'));
const WorkflowHistory = lazy(() => import('src/pages/WorkflowHistory'));
const Plugins = lazy(() => import('src/pages/Plugins'));
const PluginEditor = lazy(() => import('src/pages/PluginEditor'));
const PluginHistory = lazy(() => import('src/pages/PluginHistory'));
const ModelProfiles = lazy(() => import('src/pages/ModelProfiles'));
const ModelProfileHistory = lazy(() => import('src/pages/ModelProfileHistory'));
const ModelProfileEditor = lazy(() => import('src/pages/ModelProfileEditor'));
const ChatConnections = lazy(() => import('src/pages/ChatConnections'));

function LegacyWorkflowRedirect({ history = false }: { history?: boolean }) {
  const { id } = useParams();
  return (
    <Navigate
      replace
      to={
        id
          ? `/app/workflows/${id}${history ? '/history' : ''}`
          : '/app/workflows'
      }
    />
  );
}

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
      { path: 'spaces', element: <Spaces /> },
      { path: 'spaces/:spaceId', element: <SpaceDetail /> },
      { path: 'spaces/:spaceId/reports/:reportId', element: <SpaceDetail /> },
      { path: 'query-console', element: <QueryConsole /> },
      // One route, not two. As separate entries the landing and a conversation
      // were separate element instances, so asking a question unmounted the
      // page mid-navigation: the session was created and the question it was
      // created for went with the discarded state.
      { path: 'chat/connections', element: <ChatConnections /> },
      { path: 'chat/:threadId?', element: <ChatInterface /> },
      {
        path: 'confirmations/batch/:batchId',
        element: <BatchConfirmationPage />,
      },
      { path: 'confirmations/:confirmationId', element: <ConfirmationPage /> },
      { path: 'workflows', element: <Workflows /> },
      { path: 'workflows/:id', element: <WorkflowView /> },
      { path: 'workflows/:id/history', element: <WorkflowHistory /> },
      { path: 'scheduled-queries', element: <LegacyWorkflowRedirect /> },
      { path: 'scheduled-queries/:id', element: <LegacyWorkflowRedirect /> },
      {
        path: 'scheduled-queries/:id/history',
        element: <LegacyWorkflowRedirect history />,
      },
      { path: 'scheduled-chats', element: <ScheduledChats /> },
      { path: 'scheduled-chats/:id', element: <ScheduledChatView /> },
      {
        path: 'scheduled-chats/:id/history',
        element: <ScheduledChatHistory />,
      },
      { path: 'toolsets', element: <Toolsets /> },
      { path: 'toolsets/:toolsetId/tools', element: <ToolsetTools /> },
      { path: 'toolsets/:toolsetId/tools/new', element: <ToolEditor /> },
      {
        path: 'toolsets/:toolsetId/tools/:toolId/edit',
        element: <ToolEditor />,
      },
      { path: 'toolsets/:toolsetId/history', element: <ToolsetHistory /> },
      {
        path: 'toolsets/:toolsetId/tools/:toolId/history',
        element: <ToolHistory />,
      },
      { path: 'skillsets', element: <Skillsets /> },
      { path: 'plugins', element: <Plugins /> },
      { path: 'plugins/:pluginId/edit', element: <PluginEditor /> },
      { path: 'plugins/:pluginId/history', element: <PluginHistory /> },
      { path: 'skillsets/:skillsetId/skills', element: <SkillsetSkills /> },
      { path: 'skillsets/:skillsetId/history', element: <SkillsetHistory /> },
      {
        path: 'skillsets/:skillsetId/skills/:skillId/history',
        element: <SkillHistory />,
      },
      { path: 'roles', element: <Roles /> },
      { path: 'roles/:roleId/history', element: <RoleHistory /> },
      { path: 'model-profiles', element: <ModelProfiles /> },
      { path: 'model-profiles/new', element: <ModelProfileEditor /> },
      {
        path: 'model-profiles/:profileId/edit',
        element: <ModelProfileEditor />,
      },
      {
        path: 'model-profiles/:profileId/history',
        element: <ModelProfileHistory />,
      },
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
