import { BrowserRouter, Route, Routes } from "react-router-dom";
import Gate from "./Gate";
import Layout from "./Layout";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import Secrets from "./pages/Secrets";
import Placeholder from "./pages/Placeholder";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Gate />}>
          <Route path="/setup" element={<Setup />} />
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Placeholder title="Dashboard" />} />
            <Route path="/agents" element={<Placeholder title="Agents" />} />
            <Route path="/agents/:name" element={<Placeholder title="Agent" />} />
            <Route path="/runs" element={<Placeholder title="Runs" />} />
            <Route path="/runs/:id" element={<Placeholder title="Run" />} />
            <Route path="/secrets" element={<Secrets />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
