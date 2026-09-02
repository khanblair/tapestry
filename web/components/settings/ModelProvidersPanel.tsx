/**
 * Ported verbatim from `settingsScreen()`'s `models` tab body in the
 * prototype: Anthropic/DeepSeek/Gemini/OpenRouter connected, Qwen not
 * connected, plus the LiteLLM routing hint.
 */
const PROVIDERS = [
  { name: "Anthropic", status: "Connected" },
  { name: "DeepSeek", status: "Connected" },
  { name: "Gemini", status: "Connected" },
  { name: "Qwen", status: "Not connected" },
  { name: "OpenRouter", status: "Connected" },
] as const;

export function ModelProvidersPanel() {
  return (
    <div>
      {PROVIDERS.map((provider) => (
        <div className="toggle-row" key={provider.name}>
          <div className="tt">{provider.name}</div>
          <span className={`chip ${provider.status === "Connected" ? "on" : ""}`}>
            {provider.status}
          </span>
        </div>
      ))}
      <div className="empty-hint" style={{ marginTop: 10 }}>
        Routed through LiteLLM — personas pick a provider individually in Persona Management.
      </div>
    </div>
  );
}
