import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * Listens for postMessage from an embedded SCAD editor iframe — either the
 * full-page one (pages/EditorFrame.tsx) or the Sources-tab modal
 * (pages/Project.tsx) — see forge-scad-editor's pages/Editor.tsx
 * `notifyHostChanged`. The editor saves/exports/deletes through its own
 * backend, invisibly to this app's react-query cache, so
 * ["sources", projectId] and ["project", projectId] go stale unless
 * something invalidates them from here (§4c in the extraction plan).
 *
 * Mounted once, globally (App.tsx) — the message carries its own
 * `projectId`, so one listener covers both embedding points without needing
 * to know in advance which project is open.
 */
export function useEditorSync() {
  const queryClient = useQueryClient();

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      // Same-origin holds because the editor is only ever reached through
      // this app's own /editor/ proxy (or, in dev, the Vite proxy) — never
      // trust a message claiming to be this from anywhere else.
      if (event.origin !== window.location.origin) return;
      if (
        typeof event.data !== "object" ||
        event.data === null ||
        event.data.type !== "scad-editor:changed"
      ) {
        return;
      }
      const projectId = event.data.projectId;
      if (typeof projectId !== "string" || !projectId) return;
      queryClient.invalidateQueries({ queryKey: ["sources", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [queryClient]);
}
