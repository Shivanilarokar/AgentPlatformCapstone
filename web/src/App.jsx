import { Navigate, Route, Routes } from "react-router-dom";

import Shell from "./Shell";
import AgentDetail from "./pages/AgentDetail";
import Build from "./pages/Build";
import Connections from "./pages/Connections";
import MyAgents from "./pages/MyAgents";
import Registry from "./pages/Registry";
import SignIn from "./pages/SignIn";
import AdminReview from "./pages/AdminReview";
import Marketplace from "./pages/Marketplace";

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

        <Route path="/marketplace" element={<Marketplace />} />
        <Route path="/review" element={<AdminReview />} />

        <Route index element={<Navigate to="/registry" replace />} />
      </Route>

      <Route path="*" element={<Navigate to="/registry" replace />} />
    </Routes>
  );
}
