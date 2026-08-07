import { ScadWorkspace } from "./ScadWorkspace";

/** Full-screen modal wrapper around ScadWorkspace — used from a project's Sources tab. */
export function ScadEditor({
  projectId,
  relPath,
  initialCode,
  onClose,
  onSaved,
}: {
  projectId: string;
  relPath: string | null;
  initialCode: string;
  onClose: () => void;
  onSaved: (relPath: string) => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="w-full max-w-6xl h-[85vh]" onClick={(e) => e.stopPropagation()}>
        <ScadWorkspace
          projectId={projectId}
          relPath={relPath}
          initialCode={initialCode}
          onSaved={onSaved}
          onClose={onClose}
          className="h-full"
        />
      </div>
    </div>
  );
}
