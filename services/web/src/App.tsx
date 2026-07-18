import { BrowserRouter, Route, Routes } from "react-router-dom";
import Gate from "./Gate";
import Layout from "./Layout";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import Secrets from "./pages/Secrets";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";

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
            <Route path="/agents/:name" element={<AgentDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/secrets" element={<Secrets />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
