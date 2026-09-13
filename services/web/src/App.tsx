import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Gate from "./Gate";
import NotFound from "./pages/NotFound";
import Layout from "./Layout";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import Secrets from "./pages/Secrets";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import NewAgent from "./pages/NewAgent";
import AgentDetail from "./pages/AgentDetail";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Changes from "./pages/Changes";
import Relay from "./pages/Relay";
import Tickets from "./pages/Tickets";
import TicketDetail from "./pages/TicketDetail";
import Wiki from "./pages/Wiki";
import WikiPage from "./pages/WikiPage";
import Dlq from "./pages/Dlq";
import Memories from "./pages/Memories";
import Reporting from "./pages/Reporting";
import Reports from "./pages/Reports";
import Apps from "./pages/Apps";
import Help from "./pages/Help";
import Schedules from "./pages/Schedules";
import Skills from "./pages/Skills";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Gate />}>
          <Route path="/setup" element={<Setup />} />
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/agents/new" element={<NewAgent />} />
            <Route path="/agents/:name" element={<AgentDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/relay" element={<Relay />} />
            {/* Conversations became Relay DMs (docs/design/19) — the old
                path is still in bookmarks and in agents' own links. */}
            <Route path="/conversations" element={<Navigate to="/relay?kind=dm" replace />} />
            <Route path="/tickets" element={<Tickets />} />
            <Route path="/tickets/:key" element={<TicketDetail />} />
            <Route path="/wiki" element={<Wiki />} />
            <Route path="/wiki/:slug" element={<WikiPage />} />
            <Route path="/changes" element={<Changes />} />
            <Route path="/dlq" element={<Dlq />} />
            <Route path="/reporting" element={<Reporting />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/reports/:type" element={<Reports />} />
            <Route path="/reports/:type/:date" element={<Reports />} />
            <Route path="/apps" element={<Apps />} />
            <Route path="/help" element={<Help />} />
            <Route path="/help/:slug" element={<Help />} />
            <Route path="/memories" element={<Memories />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/schedules" element={<Schedules />} />
            <Route path="/secrets" element={<Secrets />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
