import { Navigate, Route, Routes } from "react-router-dom";

import Shell from "./Shell";
import AgentDetail from "./pages/AgentDetail";
import Build from "./pages/Build";
import Connections from "./pages/Connections";
import MyAgents from "./pages/MyAgents";
import Registry from "./pages/Registry";
import SignIn from "./pages/SignIn";
import Soon from "./pages/Soon";

export default function App() {
  return (
    <Routes>
      <Route path="/signin" element={<SignIn />} />

      <Route element={<Shell />}>
        <Route path="/registry" element={<Registry />} />

        <Route path="/build" element={<Build />} />
        <Route path="/agents" element={<MyAgents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/connections" element={<Connections />} />

        <Route path="/marketplace" element={
          <Soon title="Marketplace" step={10}
                sub="Agents other workspaces published and an admin approved."
                what="Install someone else's agent design into your workspace, using your own credentials." />} />
        <Route path="/review" element={
          <Soon title="Admin Review" step={10}
                sub="Nothing reaches the marketplace without a person approving it."
                what="A queue of submissions, each one a paused run waiting for a decision." />} />

        <Route index element={<Navigate to="/registry" replace />} />
      </Route>

      <Route path="*" element={<Navigate to="/registry" replace />} />
    </Routes>
  );
}
