import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/hooks/useAuth";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Agents from "@/pages/Agents";
import Canvas from "@/pages/Canvas";
import Chat from "@/pages/Chat";
import Skills from "@/pages/Skills";
import Personas from "@/pages/Personas";
import Integrations from "@/pages/Integrations";
import "@/index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

// eslint-disable-next-line react-refresh/only-export-components
function PrivateRoute() {
  const { token } = useAuth();
  return token ? <Outlet /> : <Navigate to="/login" replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<PrivateRoute />}>
              <Route element={<Layout />}>
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:id/canvas" element={<Canvas />} />
                <Route path="/chats" element={<Chat />} />
                <Route path="/chats/:id" element={<Chat />} />
                <Route path="/personas" element={<Personas />} />
                <Route path="/skills" element={<Skills />} />
                <Route path="/integrations" element={<Integrations />} />
                <Route path="/" element={<Navigate to="/agents" replace />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
