import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { Spinner } from "../components/ui";

/**
 * Full-bleed iframe onto a forge-scad-editor instance (§4a). Replaces the
 * old in-tree Editor page — nothing editor-shaped ships in this app's own
 * bundle anymore; its absence at runtime is what makes leaving the
 * `forge-scad-editor` compose service out hide this route entirely.
 *
 * A bookmarked /editor must not render a blank frame if the editor becomes
 * unavailable after the link was saved — redirect to /library instead, same
 * as the nav item disappearing.
 */
export default function EditorFrame() {
  const { data: health, isLoading } = useQuery({ queryKey: ["health"], queryFn: api.health });

  if (isLoading) return <Spinner label="Checking editor availability…" />;
  if (!health?.editor.available) return <Navigate to="/library" replace />;

  return (
    <iframe
      src={health.editor.path ?? "/editor/"}
      title="SCAD Editor"
      className="w-full h-[calc(100vh-8.5rem)] border-0 rounded-xl"
    />
  );
}
