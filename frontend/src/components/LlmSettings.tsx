import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmProvider, type LlmStatus } from "../api";
import { Spinner } from "./ui";
import { useUi } from "../store";

const PROVIDER_LABELS: Record<LlmProvider, string> = {
  ollama: "Ollama",
  lmstudio: "LM Studio",
};

const PROVIDER_HELP: Record<LlmProvider, string> = {
  ollama:
    "Default port 11434. It only listens on 127.0.0.1 unless you start it with OLLAMA_HOST=0.0.0.0.",
  lmstudio:
    "Default port 1234. Turn on “Serve on Local Network” in its Developer tab, or it will refuse this machine.",
};

export function LlmSettings() {
  const queryClient = useQueryClient();
  const notify = useUi((s) => s.notify);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: api.llmSettings,
  });

  const [provider, setProvider] = useState<LlmProvider>("ollama");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [temperature, setTemperature] = useState(0.3);
  const [probe, setProbe] = useState<LlmStatus | null>(null);

  useEffect(() => {
    if (!settings) return;
    setProvider(settings.provider);
    setBaseUrl(settings.base_url);
    setModel(settings.model);
    setSystemPrompt(settings.system_prompt);
    setTemperature(settings.temperature);
  }, [settings]);

  const test = useMutation({
    mutationFn: () => api.testLlmSettings({ provider, base_url: baseUrl, model }),
    onSuccess: (result) => {
      setProbe(result);
      // A single model on the other end is almost certainly the one you want.
      if (result.available && !model && result.models.length === 1) {
        setModel(result.models[0]);
      }
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveLlmSettings({
        provider,
        base_url: baseUrl,
        model,
        system_prompt: systemPrompt,
        temperature,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["llm-settings"] });
      queryClient.invalidateQueries({ queryKey: ["llm"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
      notify("LLM settings saved", "success");
    },
    onError: (error: Error) => notify(error.message, "error"),
  });

  if (isLoading || !settings) return <Spinner />;

  const dirty =
    provider !== settings.provider ||
    baseUrl !== settings.base_url ||
    model !== settings.model ||
    systemPrompt !== settings.system_prompt ||
    temperature !== settings.temperature;

  const promptIsDefault = systemPrompt === settings.default_system_prompt;

  return (
    <section className="card p-4 space-y-4">
      <div>
        <h2 className="font-medium">Local LLM</h2>
        <p className="text-xs text-slate-500 mt-1">
          Powers the “Polish with LLM” button when publishing. Entirely optional — everything
          else works without it.
        </p>
      </div>

      <div>
        <label className="label">Provider</label>
        <div className="flex gap-1">
          {settings.providers.map((option) => (
            <button
              key={option}
              onClick={() => {
                setProvider(option);
                setProbe(null);
              }}
              className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                provider === option
                  ? "bg-accent text-ink-900 border-accent font-semibold"
                  : "border-ink-500 text-slate-300 hover:bg-ink-700"
              }`}
            >
              {PROVIDER_LABELS[option]}
            </button>
          ))}
        </div>
        <p className="text-xs text-slate-500 mt-1.5">{PROVIDER_HELP[provider]}</p>
      </div>

      <div>
        <label className="label">Address</label>
        <div className="flex gap-2">
          <input
            className="input font-mono"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setProbe(null);
            }}
            placeholder={
              provider === "ollama" ? "192.168.1.50:11434" : "192.168.1.50:1234"
            }
          />
          <button
            className="btn whitespace-nowrap"
            disabled={!baseUrl.trim() || test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? "Testing…" : "Test"}
          </button>
        </div>
        <p className="text-xs text-slate-500 mt-1.5">
          The LAN address of the machine running it. A bare IP is fine — the port is filled in
          for you. Note this app connects from the server, so <code className="font-mono">
          localhost</code> would mean the server itself, not your desktop.
        </p>
      </div>

      {probe && (
        <div
          className={`rounded-lg px-3 py-2 text-sm border ${
            probe.available
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/10 text-rose-200"
          }`}
        >
          {probe.available ? (
            <>
              Connected to {PROVIDER_LABELS[probe.provider ?? provider]} — {probe.models.length}{" "}
              model{probe.models.length === 1 ? "" : "s"} available.
            </>
          ) : (
            <>
              <p>{probe.reason}</p>
              {probe.hint && <p className="mt-1 text-rose-300/80 text-xs">{probe.hint}</p>}
            </>
          )}
        </div>
      )}

      <div>
        <label className="label">Model</label>
        {probe?.available && probe.models.length > 0 ? (
          <select
            className="input"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="">Choose a model…</option>
            {probe.models.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="input font-mono"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={provider === "ollama" ? "llama3.1" : "qwen2.5-7b-instruct"}
          />
        )}
        <p className="text-xs text-slate-500 mt-1.5">
          Press Test to list what is actually installed on that machine.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <label className="label mb-0">System prompt</label>
          <button
            className="btn btn-ghost text-xs py-0.5"
            disabled={promptIsDefault}
            onClick={() => setSystemPrompt(settings.default_system_prompt)}
          >
            Reset to default
          </button>
        </div>
        <textarea
          className="input font-mono text-sm mt-1"
          rows={12}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
        />
        <p className="text-xs text-slate-500 mt-1.5">
          Sent with every polish request. The default keeps facts and Markdown structure intact
          and forbids inventing settings. Leave it blank to restore the default.
        </p>
      </div>

      <div>
        <label className="label">Temperature — {temperature.toFixed(2)}</label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={temperature}
          onChange={(e) => setTemperature(Number(e.target.value))}
          className="w-full accent-amber-500"
        />
        <p className="text-xs text-slate-500">
          Low keeps it faithful to your text. Above ~0.6 it starts rephrasing more freely.
        </p>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <button
          className="btn btn-primary"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        {dirty && <span className="text-xs text-amber-400">Unsaved changes</span>}
        <span className="ml-auto text-xs text-slate-600 font-mono truncate">
          {settings.settings_path}
        </span>
      </div>
    </section>
  );
}
