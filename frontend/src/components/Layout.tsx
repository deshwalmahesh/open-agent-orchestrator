import { NavLink, Outlet } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

export default function Layout() {
  const { user, logout } = useAuth();

  return (
    <div className="flex h-screen bg-background">
      <aside className="w-56 border-r flex flex-col p-4 shrink-0">
        <div className="font-semibold text-sm mb-0.5 leading-tight">Multi Agent</div>
        <div className="font-semibold text-sm mb-1 leading-tight">Orchestrator</div>
        <div className="text-xs text-muted-foreground mb-4">Orchestration UI</div>
        <Separator className="mb-4" />
        <nav className="flex-1 space-y-1">
          {[
            { to: "/agents", label: "Pipelines" },
            { to: "/chats", label: "Chats" },
            { to: "/personas", label: "Personas" },
            { to: "/skills", label: "Skills" },
            { to: "/integrations", label: "Integrations" },
          ].map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "block px-3 py-2 rounded-md text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <Separator className="my-3" />
        <p className="text-xs text-muted-foreground truncate mb-2">{user?.email}</p>
        <Button variant="outline" size="sm" onClick={logout}>
          Logout
        </Button>
      </aside>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
