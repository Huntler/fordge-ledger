import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useEvents } from "./hooks/useEvents";
import { JobProgress, Toasts } from "./components/ui";
import { useUi } from "./store";
import Library from "./pages/Library";
import ProjectPage from "./pages/Project";
import PrintBoard from "./pages/PrintBoard";
import PublishPage from "./pages/Publish";
import SettingsPage from "./pages/Settings";

const NAV = [
  { to: "/library", label: "Library" },
  { to: "/prints", label: "Prints" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  useEvents();
  const notify = useUi((s) => s.notify);
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-ink-600 bg-ink-800/80 backdrop-blur sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-6">
          <NavLink to="/library" className="flex items-center gap-2 font-semibold">
            <span className="text-accent text-lg">⬢</span>
            Forge Ledger
          </NavLink>

          <nav className="flex gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-lg text-sm transition-colors ${
                    isActive ? "bg-ink-600 text-white" : "text-slate-400 hover:text-slate-200"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {health && (
              <span className="text-xs text-slate-500 font-mono hidden md:inline">
                {health.counts.projects} projects · {health.counts.prints} prints
              </span>
            )}
            <button
              className="btn text-xs"
              onClick={async () => {
                await api.rescan();
                notify("Rescanning the library…");
              }}
              title="Pick up anything the watcher missed"
            >
              Rescan
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-6 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route path="/library" element={<Library />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
          <Route path="/projects/:id/publish" element={<PublishPage />} />
          <Route path="/prints" element={<PrintBoard />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/library" replace />} />
        </Routes>
      </main>

      <Toasts />
      <JobProgress />
    </div>
  );
}
