import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useUi } from "../store";

interface ServerEvent {
  event: string;
  data: Record<string, unknown>;
}

/**
 * Subscribes to the backend SSE stream and invalidates the affected queries,
 * so a file dropped into the library folder shows up without a manual refresh.
 */
export function useEvents() {
  const queryClient = useQueryClient();
  const setJob = useUi((s) => s.setJob);
  const clearJob = useUi((s) => s.clearJob);
  const notify = useUi((s) => s.notify);

  useEffect(() => {
    const source = new EventSource("/api/events");

    source.onmessage = (message) => {
      let payload: ServerEvent;
      try {
        payload = JSON.parse(message.data);
      } catch {
        return;
      }

      const { event, data } = payload;
      const projectId = data.project_id as string | undefined;

      switch (event) {
        case "job.progress":
          setJob(
            data.id as number,
            data.kind as string,
            data.progress as number,
            (data.message as string) ?? "",
          );
          break;

        case "job.done":
        case "job.cancelled":
          clearJob(data.id as number);
          queryClient.invalidateQueries({ queryKey: ["projects"] });
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          break;

        case "job.failed":
          clearJob(data.id as number);
          notify(`${data.kind} failed: ${data.error}`, "error");
          break;

        case "library.scanned":
        case "project.created":
        case "project.deleted":
          queryClient.invalidateQueries({ queryKey: ["projects"] });
          queryClient.invalidateQueries({ queryKey: ["tags"] });
          // The header counts come from /api/health, which would otherwise
          // keep showing the old total until something else refreshed it.
          queryClient.invalidateQueries({ queryKey: ["health"] });
          break;

        case "project.updated":
        case "image.added":
        case "image.source_added":
        case "model.source_added":
        case "render.finished":
        case "version.created":
        case "version.deleted":
        case "version.restored":
          queryClient.invalidateQueries({ queryKey: ["projects"] });
          if (projectId) {
            queryClient.invalidateQueries({ queryKey: ["project", projectId] });
            // The sources tab reads its own query, so refresh it too.
            queryClient.invalidateQueries({ queryKey: ["sources", projectId] });
          }
          break;

        case "print.ingested":
        case "print.updated":
        case "print.deleted":
          queryClient.invalidateQueries({ queryKey: ["prints"] });
          queryClient.invalidateQueries({ queryKey: ["print-stats"] });
          queryClient.invalidateQueries({ queryKey: ["failures"] });
          if (projectId) {
            queryClient.invalidateQueries({ queryKey: ["project", projectId] });
          }
          break;
      }
    };

    // EventSource reconnects on its own; refetch so nothing missed while
    // disconnected stays stale on screen.
    source.onerror = () => {
      queryClient.invalidateQueries();
    };

    return () => source.close();
  }, [queryClient, setJob, clearJob, notify]);
}
