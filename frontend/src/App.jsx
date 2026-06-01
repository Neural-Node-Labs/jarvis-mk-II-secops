import { useState, useEffect, useRef, useCallback } from "react";

// ─── Dynamic URLs ──────────────────────────────────────────────────────────────
const _base = import.meta.env.VITE_API_URL || "";
const _wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = _base
  ? `${_base.replace(/^http/, "ws")}/ws/chat`
  : `${_wsProto}//${window.location.host}/ws/chat`;
const API_URL = _base ? `${_base}/api` : `/api`;

// ─── Provider catalogue ───────────────────────────────────────────────────────
const PROVIDERS = {
  deepseek:  { label: "DeepSeek",      color: "#4FFFFF", schema: "openai",
               models: ["deepseek-chat","deepseek-coder","deepseek-v4-pro","deepseek-v4-flash"],
               keyLabel: "DEEPSEEK_API_KEY", needsKey: true, needsUrl: false },
  ollama:    { label: "Ollama / Llama", color: "#FF9F4A", schema: "openai",
               models: ["llama3","llama3.1","llama3.2","codellama","mistral","phi3","gemma2"],
               keyLabel: null, needsKey: false, needsUrl: true },
  anthropic: { label: "Anthropic",      color: "#CC785C", schema: "anthropic",
               models: ["claude-sonnet-4-20250514","claude-opus-4-5","claude-haiku-4-5"],
               keyLabel: "ANTHROPIC_API_KEY", needsKey: true, needsUrl: false },
  openai:    { label: "OpenAI",         color: "#74AA9C", schema: "openai",
               models: ["gpt-4o","gpt-4o-mini","gpt-4-turbo","gpt-3.5-turbo"],
               keyLabel: "OPENAI_API_KEY", needsKey: true, needsUrl: false },
};

const SCHEMA_DOCS = {
  openai: {
    label: "OpenAI-compatible",
    badge: "#74AA9C",
    description: "Used by DeepSeek, Ollama, OpenAI. System prompt as first message. Responses via choices[0].delta.content.",
    fields: [
      { name: "messages",    type: "array",   note: "First item is {role:system} for system prompt" },
      { name: "model",       type: "string",  note: "Model name string" },
      { name: "temperature", type: "float",   note: "0.0 – 2.0" },
      { name: "max_tokens",  type: "integer", note: "Max output tokens" },
      { name: "stream",      type: "boolean", note: "SSE: data: {...} lines, data: [DONE] to end" },
    ],
    response: "choices[0].delta.content (stream) / choices[0].message.content (non-stream)",
  },
  anthropic: {
    label: "Anthropic Messages API",
    badge: "#CC785C",
    description: "Used exclusively by Anthropic. System prompt as top-level field. Responses via content_block_delta events.",
    fields: [
      { name: "messages",    type: "array",   note: "Must strictly alternate user/assistant. No system role." },
      { name: "system",      type: "string",  note: "Top-level field, not inside messages array" },
      { name: "model",       type: "string",  note: "Model name string" },
      { name: "max_tokens",  type: "integer", note: "Required. Max output tokens" },
      { name: "stream",      type: "boolean", note: "SSE: event:/data: pairs. type=content_block_delta carries text." },
    ],
    response: "delta.text from content_block_delta events (stream) / content[0].text (non-stream)",
  },
};

const SKILL_COLORS = {
  filesystem:    { bg: "#1a2a1a", border: "#3a7a3a", icon: "📁" },
  os_execution:  { bg: "#2a1a1a", border: "#7a3a3a", icon: "⚙️" },
  cbd_architect: { bg: "#1a1a2a", border: "#3a3a7a", icon: "🏗️" },
};

// ─── Shared style tokens ───────────────────────────────────────────────────────
const S = {
  label: { color: "#7a9a7a", fontFamily: "monospace", fontSize: 11, display: "block", marginBottom: 4 },
  input: {
    width: "100%", padding: "7px 10px", borderRadius: 4,
    background: "#0d150d", border: "1px solid #2a4a2a",
    color: "#cce8cc", fontFamily: "monospace", fontSize: 12,
    boxSizing: "border-box", outline: "none",
  },
  select: {
    width: "100%", padding: "7px 10px", borderRadius: 4,
    background: "#0d150d", border: "1px solid #2a4a2a",
    color: "#cce8cc", fontFamily: "monospace", fontSize: 12,
    boxSizing: "border-box",
  },
  row: { marginBottom: 14 },
  sectionTitle: { color: "#4FFFFF", fontFamily: "monospace", fontSize: 12, fontWeight: "bold", marginBottom: 10, marginTop: 18, letterSpacing: 1 },
  badge: (color) => ({
    display: "inline-block", padding: "1px 7px", borderRadius: 3,
    background: `${color}22`, border: `1px solid ${color}66`,
    color, fontFamily: "monospace", fontSize: 10, marginLeft: 6,
  }),
};

// ─── Toggle Checkbox ───────────────────────────────────────────────────────────
const Toggle = ({ checked, onChange, label, color = "#4FFFFF", title }) => (
  <label title={title} style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer",
    userSelect: "none", fontFamily: "monospace", fontSize: 10, color: checked ? color : "#4a6a4a",
    padding: "3px 8px", borderRadius: 4,
    background: checked ? `${color}11` : "transparent",
    border: `1px solid ${checked ? color + "44" : "#2a3a2a"}`,
    transition: "all 0.15s", whiteSpace: "nowrap" }}>
    <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
      style={{ accentColor: color, width: 11, height: 11 }} />
    {label}
  </label>
);

// ─── MessageBubble ─────────────────────────────────────────────────────────────
const MessageBubble = ({ msg }) => {
  if (msg.role === "user") return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
      <div style={{ background: "linear-gradient(135deg,#1a3a1a,#0a2a0a)", border: "1px solid #3a7a3a",
        borderRadius: "12px 12px 2px 12px", padding: "10px 14px", maxWidth: "70%",
        color: "#90ff90", fontFamily: "monospace", fontSize: 13, lineHeight: 1.6, wordBreak: "break-word" }}>
        <span style={{ color: "#4a8a4a", fontSize: 10, display: "block", marginBottom: 4 }}>YOU</span>
        {msg.content}
      </div>
    </div>
  );

  if (msg.role === "tool_call") {
    const s = SKILL_COLORS[msg.skill] || { bg: "#1a1a1a", border: "#555", icon: "🔧" };
    return (
      <div style={{ marginBottom: 8, padding: "8px 12px", background: s.bg, border: `1px solid ${s.border}`, borderRadius: 6 }}>
        <span style={{ color: s.border, fontSize: 11, fontFamily: "monospace" }}>
          {s.icon} SKILL:{msg.skill} → {msg.action}
          {msg.confirmed && <span style={{ color: "#ffaa44", marginLeft: 6 }}>⚡ AUTO-CONFIRMED</span>}
        </span>
        <pre style={{ margin: "4px 0 0", fontSize: 10, color: "#888", overflow: "auto", maxHeight: 80 }}>
          {JSON.stringify(msg.params, null, 2)}
        </pre>
      </div>
    );
  }

  if (msg.role === "tool_result") {
    const ok = msg.data?.success;
    return (
      <div style={{ marginBottom: 8, padding: "8px 12px", background: ok ? "#0d1f0d" : "#1f0d0d",
        border: `1px solid ${ok ? "#2a5a2a" : "#5a2a2a"}`, borderRadius: 6 }}>
        <span style={{ color: ok ? "#4a9a4a" : "#9a4a4a", fontSize: 11, fontFamily: "monospace" }}>
          {ok ? "✓ RESULT" : "✗ ERROR"}
        </span>
        <pre style={{ margin: "4px 0 0", fontSize: 10, color: "#aaa", overflow: "auto", maxHeight: 120 }}>
          {JSON.stringify(msg.data?.output || msg.data?.error, null, 2)}
        </pre>
      </div>
    );
  }

  // React mode status message
  if (msg.role === "react_status") return (
    <div style={{ marginBottom: 6, padding: "5px 12px", background: "#0a1a2a",
      border: "1px solid #2a4a7a", borderRadius: 4, display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ color: "#4a9aff", fontSize: 10, fontFamily: "monospace" }}>
        🔁 ReAct [{msg.iteration}] — {msg.phase}
      </span>
      {msg.healing && <span style={{ color: "#ffaa44", fontSize: 9 }}>🩹 self-healing</span>}
    </div>
  );

  if (msg.role === "assistant") return (
    <div style={{ display: "flex", gap: 10, marginBottom: 12, alignItems: "flex-start" }}>
      <div style={{ width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
        background: "linear-gradient(135deg,#4FFFFF22,#4FFFFF44)", border: "1px solid #4FFFFF66",
        display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, color: "#4FFFFF" }}>A</div>
      <div style={{ background: "#0a0f0a", border: "1px solid #1a2a1a", borderRadius: "2px 12px 12px 12px",
        padding: "10px 14px", maxWidth: "80%", color: "#d4e8d4", fontFamily: "monospace",
        fontSize: 13, lineHeight: 1.7, wordBreak: "break-word", whiteSpace: "pre-wrap" }}>
        {msg.content}
        {msg.streaming && <span style={{ animation: "blink 1s infinite", color: "#4FFFFF" }}>▋</span>}
      </div>
    </div>
  );

  return null;
};

// ─── ConfirmDialog ─────────────────────────────────────────────────────────────
const ConfirmDialog = ({ confirm, onConfirm, onCancel }) => (
  <div style={{ margin: "12px 0", padding: 16, background: "linear-gradient(135deg,#2a1a0a,#1a0a00)",
    border: "2px solid #ff6a00", borderRadius: 8 }}>
    <div style={{ color: "#ff9a44", fontFamily: "monospace", fontSize: 12, marginBottom: 8 }}>⚠️ CONFIRMATION REQUIRED</div>
    <div style={{ color: "#ffcc88", fontFamily: "monospace", fontSize: 13, marginBottom: 12, lineHeight: 1.5 }}>{confirm.prompt}</div>
    <div style={{ display: "flex", gap: 8 }}>
      <button onClick={() => onConfirm(confirm.confirm_id)} style={{
        padding: "6px 16px", background: "#7a0000", border: "1px solid #ff3333",
        color: "#ffaaaa", borderRadius: 4, cursor: "pointer", fontFamily: "monospace", fontSize: 12 }}>✓ CONFIRM EXECUTE</button>
      <button onClick={() => onCancel(confirm.confirm_id)} style={{
        padding: "6px 16px", background: "#1a1a1a", border: "1px solid #555",
        color: "#aaa", borderRadius: 4, cursor: "pointer", fontFamily: "monospace", fontSize: 12 }}>✗ CANCEL</button>
    </div>
  </div>
);

// ─── SchemaViewer ──────────────────────────────────────────────────────────────
const SchemaViewer = ({ schema }) => {
  const doc = SCHEMA_DOCS[schema];
  if (!doc) return null;
  return (
    <div style={{ background: "#080e08", border: `1px solid ${doc.badge}44`, borderRadius: 6, padding: 12, marginTop: 8 }}>
      <div style={{ color: doc.badge, fontFamily: "monospace", fontSize: 11, marginBottom: 6, fontWeight: "bold" }}>
        {doc.label} — Request / Response Schema
      </div>
      <div style={{ color: "#7a9a7a", fontSize: 11, fontFamily: "monospace", marginBottom: 8 }}>{doc.description}</div>
      <div style={{ color: "#5a8a5a", fontSize: 10, fontFamily: "monospace", marginBottom: 6 }}>REQUEST FIELDS</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
        <thead>
          <tr>
            {["field","type","note"].map(h => (
              <th key={h} style={{ textAlign: "left", color: "#3a6a3a", paddingBottom: 4, paddingRight: 12 }}>{h.toUpperCase()}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {doc.fields.map(f => (
            <tr key={f.name}>
              <td style={{ color: doc.badge, paddingRight: 12, paddingBottom: 3 }}>{f.name}</td>
              <td style={{ color: "#6a9a6a", paddingRight: 12, paddingBottom: 3 }}>{f.type}</td>
              <td style={{ color: "#8aaa8a", paddingBottom: 3 }}>{f.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ color: "#5a8a5a", fontSize: 10, fontFamily: "monospace", marginTop: 8, marginBottom: 4 }}>RESPONSE TOKEN PATH</div>
      <div style={{ color: "#9acc9a", fontSize: 10, fontFamily: "monospace" }}>{doc.response}</div>
    </div>
  );
};

// ─── Settings Tab (full page) ─────────────────────────────────────────────────
const SettingsTab = ({ onClose, onSaved }) => {
  const [cfg, setCfg] = useState(null);
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("deepseek-chat");
  const [customModel, setCustomModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [schemaOverride, setSchemaOverride] = useState(false);
  const [schemaFormat, setSchemaFormat] = useState("openai");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [activeSchemaTab, setActiveSchemaTab] = useState("openai");

  useEffect(() => {
    fetch(`${API_URL}/config`)
      .then(r => r.json())
      .then(data => {
        setCfg(data);
        setProvider(data.provider || "deepseek");
        setModel(data.model || "deepseek-chat");
        setBaseUrl(data.base_url || "");
        setTemperature(data.temperature ?? 0.7);
        setMaxTokens(data.max_tokens ?? 4096);
        setSchemaOverride(data.schema_override || false);
        setSchemaFormat(data.schema_format || "openai");
        setActiveSchemaTab(data.schema_format || "openai");
      })
      .catch(() => setError("Could not load current config from backend."));
  }, []);

  const prov = PROVIDERS[provider] || PROVIDERS.deepseek;
  const activeSchema = schemaOverride ? schemaFormat : (prov.schema);
  const isCustomModel = model === "__custom__";

  const handleProviderChange = (p) => {
    const pd = PROVIDERS[p];
    setProvider(p);
    setModel(pd.models[0]);
    setCustomModel("");
    if (!schemaOverride) setActiveSchemaTab(pd.schema);
    setApiKey("");
    setBaseUrl(pd.needsUrl ? (p === "ollama" ? "http://localhost:11434" : "") : "");
  };

  const handleSave = async () => {
    setSaving(true); setError(""); setSaved(false);
    const finalModel = isCustomModel ? customModel.trim() : model;
    if (!finalModel) { setError("Model name is required."); setSaving(false); return; }
    try {
      const payload = {
        provider,
        model: finalModel,
        api_key: apiKey || undefined,
        base_url: baseUrl || undefined,
        temperature: parseFloat(temperature),
        max_tokens: parseInt(maxTokens),
        schema_format: schemaOverride ? schemaFormat : null,
      };
      const res = await fetch(`${API_URL}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.status === "ok") {
        setSaved(true);
        setActiveSchemaTab(data.schema_format);
        onSaved(data);
        setTimeout(() => setSaved(false), 3000);
      } else {
        setError(JSON.stringify(data));
      }
    } catch (e) {
      setError(`Save failed: ${e.message}`);
    }
    setSaving(false);
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "#040a04", zIndex: 50, overflowY: "auto",
      fontFamily: "monospace" }}>
      <div style={{ padding: "14px 24px", borderBottom: "1px solid #1a2a1a", background: "#060c06",
        display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, zIndex: 10 }}>
        <div style={{ color: "#4FFFFF", fontSize: 14, fontWeight: "bold", letterSpacing: 2 }}>⚙ SETTINGS</div>
        <button onClick={onClose} style={{ background: "none", border: "1px solid #2a4a2a",
          color: "#7aaa7a", padding: "4px 14px", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>
          ✕ CLOSE
        </button>
      </div>

      <div style={{ maxWidth: 760, margin: "0 auto", padding: "24px 24px 48px" }}>
        <div style={S.sectionTitle}>LLM PROVIDER</div>

        <div style={S.row}>
          <span style={S.label}>PROVIDER</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {Object.entries(PROVIDERS).map(([key, p]) => (
              <button key={key} onClick={() => handleProviderChange(key)} style={{
                padding: "6px 14px", borderRadius: 4, cursor: "pointer",
                background: provider === key ? `${p.color}22` : "#0d150d",
                border: `1px solid ${provider === key ? p.color : "#2a4a2a"}`,
                color: provider === key ? p.color : "#4a6a4a",
                fontFamily: "monospace", fontSize: 11, transition: "all 0.15s",
              }}>
                {p.label}
                {provider === key && <span style={S.badge(p.color)}>{p.schema}</span>}
              </button>
            ))}
          </div>
        </div>

        <div style={S.row}>
          <span style={S.label}>MODEL</span>
          <select value={isCustomModel ? "__custom__" : model}
            onChange={e => { setModel(e.target.value); if (e.target.value !== "__custom__") setCustomModel(""); }}
            style={S.select}>
            {prov.models.map(m => <option key={m} value={m}>{m}</option>)}
            <option value="__custom__">— custom model name —</option>
          </select>
          {isCustomModel && (
            <input value={customModel} onChange={e => setCustomModel(e.target.value)}
              placeholder="Enter exact model name..."
              style={{ ...S.input, marginTop: 6 }} />
          )}
        </div>

        {prov.needsKey && (
          <div style={S.row}>
            <span style={S.label}>API KEY {cfg?.key_configured && <span style={{ color: "#4a9a4a" }}>● configured</span>}</span>
            <div style={{ display: "flex", gap: 6 }}>
              <input value={apiKey} onChange={e => setApiKey(e.target.value)}
                type={showKey ? "text" : "password"}
                placeholder={cfg?.key_configured ? "Key is set — enter new key to replace" : `Paste ${prov.keyLabel} here (or set env var)`}
                style={{ ...S.input, flex: 1 }} />
              <button onClick={() => setShowKey(v => !v)} style={{
                background: "#0d150d", border: "1px solid #2a4a2a", color: "#7a9a7a",
                padding: "0 10px", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>
                {showKey ? "HIDE" : "SHOW"}
              </button>
            </div>
            <div style={{ color: "#3a5a3a", fontSize: 10, marginTop: 4 }}>
              Not saved to disk. Set <code style={{ color: "#7a9a7a" }}>{prov.keyLabel}</code> env var to avoid re-entering on restart.
            </div>
          </div>
        )}

        {(prov.needsUrl || baseUrl) && (
          <div style={S.row}>
            <span style={S.label}>BASE URL</span>
            <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
              placeholder={provider === "ollama" ? "http://localhost:11434" : "https://api.example.com/v1"}
              style={S.input} />
            <div style={{ color: "#3a5a3a", fontSize: 10, marginTop: 4 }}>
              {provider === "ollama" ? "Ollama runs locally. In Docker use http://ollama:11434" : "Leave blank to use provider default endpoint."}
            </div>
          </div>
        )}

        <div style={S.sectionTitle}>GENERATION PARAMETERS</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={S.row}>
            <span style={S.label}>TEMPERATURE  <span style={{ color: "#4a7a4a" }}>{temperature}</span></span>
            <input type="range" min="0" max="2" step="0.05" value={temperature}
              onChange={e => setTemperature(parseFloat(e.target.value))}
              style={{ width: "100%", accentColor: prov.color }} />
            <div style={{ display: "flex", justifyContent: "space-between", color: "#3a5a3a", fontSize: 9, marginTop: 2 }}>
              <span>0 precise</span><span>1 balanced</span><span>2 creative</span>
            </div>
          </div>
          <div style={S.row}>
            <span style={S.label}>MAX TOKENS</span>
            <input type="number" value={maxTokens} onChange={e => setMaxTokens(parseInt(e.target.value) || 1024)}
              min="256" max="32768" step="256" style={S.input} />
          </div>
        </div>

        <div style={S.sectionTitle}>
          WIRE SCHEMA
          <span style={{ ...S.badge(SCHEMA_DOCS[activeSchema]?.badge || "#888"), marginLeft: 8 }}>
            {SCHEMA_DOCS[activeSchema]?.label || activeSchema}
          </span>
          {schemaOverride && <span style={S.badge("#ffaa44")}>OVERRIDE ACTIVE</span>}
        </div>

        <div style={{ background: "#080e08", border: "1px solid #1a3a1a", borderRadius: 6, padding: 12, marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: "#6a9a6a", lineHeight: 1.6 }}>
            The wire schema controls how requests are built and responses parsed.
            Auto-detection picks the correct schema from the provider.
          </div>
          <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", color: "#7a9a7a", fontSize: 11 }}>
              <input type="checkbox" checked={schemaOverride}
                onChange={e => { setSchemaOverride(e.target.checked); if (!e.target.checked) setActiveSchemaTab(prov.schema); }}
                style={{ accentColor: "#ffaa44" }} />
              Override schema (advanced)
            </label>
            {schemaOverride && (
              <div style={{ display: "flex", gap: 6 }}>
                {["openai","anthropic"].map(s => (
                  <button key={s} onClick={() => { setSchemaFormat(s); setActiveSchemaTab(s); }} style={{
                    padding: "3px 10px", borderRadius: 3, cursor: "pointer",
                    background: schemaFormat === s ? `${SCHEMA_DOCS[s].badge}22` : "#0d150d",
                    border: `1px solid ${schemaFormat === s ? SCHEMA_DOCS[s].badge : "#2a4a2a"}`,
                    color: schemaFormat === s ? SCHEMA_DOCS[s].badge : "#4a6a4a",
                    fontFamily: "monospace", fontSize: 10,
                  }}>{s}</button>
                ))}
              </div>
            )}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: "#4a7a4a" }}>
            Auto-detected for <strong style={{ color: "#7aaa7a" }}>{provider}</strong>:{" "}
            <span style={{ color: SCHEMA_DOCS[prov.schema]?.badge }}>{prov.schema}</span>
          </div>
        </div>

        <div style={{ display: "flex", gap: 4, marginBottom: -1 }}>
          {["openai","anthropic"].map(s => (
            <button key={s} onClick={() => setActiveSchemaTab(s)} style={{
              padding: "5px 14px", borderRadius: "4px 4px 0 0", cursor: "pointer",
              background: activeSchemaTab === s ? "#080e08" : "#060c06",
              border: `1px solid ${activeSchemaTab === s ? SCHEMA_DOCS[s].badge + "88" : "#1a3a1a"}`,
              borderBottom: activeSchemaTab === s ? "1px solid #080e08" : "1px solid #1a3a1a",
              color: activeSchemaTab === s ? SCHEMA_DOCS[s].badge : "#3a5a3a",
              fontFamily: "monospace", fontSize: 10,
            }}>{SCHEMA_DOCS[s].label}</button>
          ))}
        </div>
        <SchemaViewer schema={activeSchemaTab} />

        <div style={{ marginTop: 28, display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={handleSave} disabled={saving} style={{
            padding: "9px 28px", borderRadius: 6, cursor: saving ? "not-allowed" : "pointer",
            background: saving ? "#0d150d" : `linear-gradient(135deg, ${prov.color}33, ${prov.color}11)`,
            border: `1px solid ${saving ? "#2a4a2a" : prov.color}`,
            color: saving ? "#4a6a4a" : prov.color,
            fontFamily: "monospace", fontSize: 12, fontWeight: "bold",
          }}>
            {saving ? "SAVING…" : "APPLY & SAVE"}
          </button>
          {saved && <span style={{ color: "#4a9a4a", fontSize: 11 }}>✓ Saved. API key must be re-entered on restart.</span>}
          {error && <span style={{ color: "#9a4a4a", fontSize: 11 }}>✗ {error}</span>}
        </div>

        {cfg && (
          <div style={{ marginTop: 24, padding: 12, background: "#060c06", border: "1px solid #1a2a1a", borderRadius: 6 }}>
            <div style={{ color: "#3a5a3a", fontSize: 10, marginBottom: 8 }}>CURRENTLY ACTIVE (from backend)</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 10, fontFamily: "monospace" }}>
              {[
                ["provider", cfg.provider], ["model", cfg.model],
                ["schema_format", cfg.schema_format], ["schema_override", cfg.schema_override ? "yes" : "auto"],
                ["temperature", cfg.temperature], ["max_tokens", cfg.max_tokens],
                ["api key", cfg.key_configured ? "● configured" : "✗ not set"],
                ["base_url", cfg.base_url || "(default)"],
              ].map(([k,v]) => (
                <div key={k}>
                  <span style={{ color: "#3a5a3a" }}>{k}: </span>
                  <span style={{ color: k === "api key" ? (cfg.key_configured ? "#4a9a4a" : "#9a4a4a") : "#7aaa7a" }}>{String(v)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [pendingConfirms, setPendingConfirms] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [providerInfo, setProviderInfo] = useState({ provider: "deepseek", model: "deepseek-chat", schema_format: "openai" });
  const [skills, setSkills] = useState([]);

  // ── New feature flags ──
  const [autoConfirm, setAutoConfirm] = useState(false);   // auto-confirm destructive actions
  const [reactMode, setReactMode] = useState(false);        // ReAct self-healing loop
  const reactIterRef = useRef(0);                           // iteration counter for ReAct
  const reactActiveRef = useRef(false);                     // prevent overlapping loops

  const wsRef = useRef(null);
  const bottomRef = useRef(null);
  const handleEventRef = useRef(null);
  const reconnectDelay = useRef(1000);

  useEffect(() => {
    fetch(`${API_URL}/config`).then(r => r.json()).then(setProviderInfo).catch(() => {});
    fetch(`${API_URL}/skills`).then(r => r.json()).then(d => setSkills(d.skills || [])).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); reconnectDelay.current = 1000; };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          setTimeout(connect, reconnectDelay.current);
          reconnectDelay.current = Math.min(reconnectDelay.current * 2, 15000);
        }
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try { if (handleEventRef.current) handleEventRef.current(JSON.parse(e.data)); } catch {}
      };
    };
    connect();
    return () => { cancelled = true; wsRef.current?.close(); };
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // ── Send a raw WS message, returns a Promise that resolves on "done" ──────────
  const sendWS = useCallback((payload) => {
    return new Promise((resolve) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        resolve({ error: "not_connected" });
        return;
      }
      wsRef.current.send(JSON.stringify(payload));
      // Resolve when the agent signals completion
      const unsub = () => { handleEventRef._doneCallbacks = (handleEventRef._doneCallbacks || []).filter(cb => cb !== unsub_inner); };
      const unsub_inner = (evt) => { if (evt.type === "done") { unsub(); resolve(evt); } };
      handleEventRef._doneCallbacks = [...(handleEventRef._doneCallbacks || []), unsub_inner];
    });
  }, []);

  const handleEvent = useCallback((event) => {
    const { type, data } = event;

    // Fire any done-callbacks registered by sendWS
    if (type === "done" && handleEventRef._doneCallbacks?.length) {
      handleEventRef._doneCallbacks.forEach(cb => cb(event));
    }

    if (type === "token") {
      setStreaming(true);
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.streaming)
          return [...prev.slice(0,-1), { ...last, content: last.content + data }];
        return [...prev, { role: "assistant", content: data, streaming: true }];
      });
    } else if (type === "done") {
      setStreaming(false);
      setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
    } else if (type === "tool_call") {
      setMessages(prev => [...prev, { role: "tool_call", ...data }]);
    } else if (type === "tool_result") {
      setMessages(prev => [...prev, { role: "tool_result", data }]);
    } else if (type === "confirm_needed") {
      // Auto-confirm if flag is on — fire and forget, no dialog shown
      if (autoConfirmRef.current) {
        setMessages(prev => [...prev, {
          role: "tool_call", skill: data.skill, action: data.action, params: {},
          confirmed: true,
        }]);
        wsRef.current?.send(JSON.stringify({ type: "confirm", confirm_id: data.confirm_id }));
      } else {
        setPendingConfirms(prev => [...prev, data]);
      }
    } else if (type === "confirm_cancelled") {
      setPendingConfirms(prev => prev.filter(c => c.confirm_id !== data));
    } else if (type === "error") {
      setMessages(prev => [...prev, { role: "assistant", content: `❌ Error: ${data}` }]);
      setStreaming(false);
    }
  }, []);

  // Keep autoConfirm accessible in the ws callback without re-registering
  const autoConfirmRef = useRef(autoConfirm);
  useEffect(() => { autoConfirmRef.current = autoConfirm; }, [autoConfirm]);

  handleEventRef.current = handleEvent;

  // ── Core send ─────────────────────────────────────────────────────────────────
  const sendMessage = useCallback((overrideMsg) => {
    const msg = (overrideMsg ?? input).trim();
    if (!msg || streaming) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "⚠️ Not connected to backend. Check that the backend is running, then try again.",
        streaming: false,
      }]);
      return;
    }
    if (!overrideMsg) setInput("");
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    wsRef.current.send(JSON.stringify({ type: "chat", message: msg }));
  }, [input, streaming]);

  // ── Continue button — sends "continue" nudge to keep agent going ──────────────
  const sendContinue = useCallback(() => {
    if (streaming) return;
    sendMessage("continue");
  }, [sendMessage, streaming]);

  // ── ReAct loop — runs Reason→Act cycles until task complete or max iterations ──
  const MAX_REACT_ITER = 10;
  const startReactLoop = useCallback(async (initialMsg) => {
    if (reactActiveRef.current) return;
    reactActiveRef.current = true;
    reactIterRef.current = 0;

    const msg = (initialMsg ?? input).trim();
    if (!msg) { reactActiveRef.current = false; return; }
    if (!overrideMsg_guard(msg)) { reactActiveRef.current = false; return; }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      reactActiveRef.current = false; return;
    }

    setInput("");
    setMessages(prev => [...prev, { role: "user", content: msg }]);

    let lastError = null;
    let continueLoop = true;

    while (continueLoop && reactIterRef.current < MAX_REACT_ITER) {
      reactIterRef.current += 1;
      const iter = reactIterRef.current;
      const isHealing = lastError !== null;

      setMessages(prev => [...prev, {
        role: "react_status",
        iteration: iter,
        phase: isHealing ? "self-healing → retrying" : "reasoning",
        healing: isHealing,
      }]);

      // Build prompt — on error, inject self-healing instruction
      const prompt = isHealing
        ? `The previous attempt encountered an error: "${lastError}". Analyze what went wrong, correct your approach, and try again to complete the original task.`
        : (iter === 1 ? msg : "continue");

      // Wait for agent to finish this cycle
      await new Promise((resolve) => {
        let resolved = false;
        const finish = () => { if (!resolved) { resolved = true; resolve(); } };

        setStreaming(true);
        wsRef.current.send(JSON.stringify({ type: "chat", message: prompt }));

        // Listen for done or error
        const origHandler = handleEventRef.current;
        handleEventRef.current = (event) => {
          origHandler(event);
          if (event.type === "done") {
            setStreaming(false);
            finish();
          } else if (event.type === "error") {
            lastError = event.data;
            setStreaming(false);
            finish();
          } else if (event.type === "tool_result") {
            // Track errors from tool results for self-healing
            if (!event.data?.success) lastError = event.data?.error || "Tool execution failed";
            else lastError = null;
          }
        };

        // Safety timeout per iteration (120s)
        setTimeout(finish, 120000);
      });

      // Decide whether to continue: stop if no error and agent signals completion
      // (heuristic: if last assistant message contains completion keywords)
      const lastMsg = messages[messages.length - 1];
      const lastContent = lastMsg?.content?.toLowerCase() || "";
      const taskComplete = !lastError && (
        lastContent.includes("task complete") ||
        lastContent.includes("done") ||
        lastContent.includes("finished") ||
        lastContent.includes("completed") ||
        iter >= MAX_REACT_ITER
      );

      if (taskComplete) continueLoop = false;
    }

    setMessages(prev => [...prev, {
      role: "react_status",
      iteration: reactIterRef.current,
      phase: reactIterRef.current >= MAX_REACT_ITER ? `max iterations (${MAX_REACT_ITER}) reached` : "task complete ✓",
      healing: false,
    }]);

    reactActiveRef.current = false;
    setStreaming(false);
  }, [input, messages]);

  // Guard: don't start react loop if already streaming or input empty
  function overrideMsg_guard(msg) { return !!(msg && !streaming); }

  const handleConfirm = (id) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    wsRef.current?.send(JSON.stringify({ type: "confirm", confirm_id: id }));
  };
  const handleCancel = (id) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    wsRef.current?.send(JSON.stringify({ type: "cancel_confirm", confirm_id: id }));
  };

  const handleReset = async () => {
    reactActiveRef.current = false;
    await fetch(`${API_URL}/reset`, { method: "POST" }).catch(() => {});
    setMessages([]); setPendingConfirms([]);
  };

  const pColor = PROVIDERS[providerInfo.provider]?.color || "#4FFFFF";
  const schemaColor = SCHEMA_DOCS[providerInfo.schema_format]?.badge || "#888";
  const canSend = !streaming && !!input.trim();
  const canContinue = !streaming && connected;

  return (
    <div style={{ minHeight: "100vh", background: "#040a04", display: "flex",
      flexDirection: "column", fontFamily: "monospace" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0f0a; }
        ::-webkit-scrollbar-thumb { background: #2a4a2a; border-radius: 2px; }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes pulse { 0%,100%{opacity:0.6} 50%{opacity:1} }
        @keyframes react-pulse { 0%,100%{box-shadow:0 0 0 0 #4a9aff33} 50%{box-shadow:0 0 8px 2px #4a9aff44} }
        textarea:focus, input:focus, select:focus { outline: 1px solid #2a5a2a; }
        button:active { opacity: 0.8; }
      `}</style>

      {/* ── Header ── */}
      <div style={{ padding: "10px 20px", borderBottom: "1px solid #1a2a1a", background: "#060c06",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        position: "sticky", top: 0, zIndex: 10 }}>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ color: "#4FFFFF", fontSize: 15, fontWeight: "bold", letterSpacing: 2 }}>◈ JARVIS</span>
          <span style={{ padding: "2px 7px", borderRadius: 3, fontSize: 10,
            background: connected ? "#0a1f0a" : "#1f0a0a",
            border: `1px solid ${connected ? "#2a5a2a" : "#5a2a2a"}`,
            color: connected ? "#4a9a4a" : "#9a4a4a",
            animation: connected ? "pulse 2s infinite" : "none" }}>
            {connected ? "● LIVE" : "○ OFFLINE"}
          </span>
          <span style={{ padding: "2px 7px", borderRadius: 3, fontSize: 10,
            background: `${pColor}11`, border: `1px solid ${pColor}44`, color: pColor }}>
            {providerInfo.provider?.toUpperCase()} / {providerInfo.model}
          </span>
          <span title={`Wire schema: ${SCHEMA_DOCS[providerInfo.schema_format]?.label}`}
            style={{ padding: "2px 7px", borderRadius: 3, fontSize: 9,
              background: `${schemaColor}11`, border: `1px solid ${schemaColor}44`, color: schemaColor, cursor: "default" }}>
            {providerInfo.schema_format || "openai"}
            {providerInfo.schema_override && " ⚡"}
          </span>
        </div>

        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {skills.map(s => {
            const sc = SKILL_COLORS[s.name] || { border: "#555", icon: "🔧" };
            return (
              <span key={s.name} title={s.description} style={{ padding: "2px 7px", borderRadius: 3,
                border: `1px solid ${sc.border}`, color: sc.border, fontSize: 9, cursor: "default" }}>
                {sc.icon} {s.name}
              </span>
            );
          })}
          <button onClick={() => setSettingsOpen(true)} style={{ padding: "4px 12px",
            background: "#0f1f0f", border: "1px solid #2a4a2a",
            color: "#7aaa7a", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>⚙ SETTINGS</button>
          <button onClick={handleReset} style={{ padding: "4px 12px",
            background: "#1f0f0f", border: "1px solid #4a2a2a",
            color: "#aa7a7a", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>↺ RESET</button>
        </div>
      </div>

      {/* ── Messages ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px", maxWidth: 900, width: "100%", margin: "0 auto" }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", paddingTop: 60 }}>
            <div style={{ fontSize: 40, marginBottom: 16, color: "#2a4a2a" }}>◈</div>
            <div style={{ color: "#4FFFFF44", fontSize: 14, marginBottom: 8 }}>AGENT READY</div>
            <div style={{ color: "#2a5a2a", fontSize: 12, lineHeight: 2 }}>
              Provider: {providerInfo.provider} / {providerInfo.model} · Schema: {providerInfo.schema_format}<br/>
              <span style={{ color: "#3a7a3a" }}>Configure provider, API key, and schema in ⚙ SETTINGS</span>
            </div>
            <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
              {["List files in my home directory","Show running processes",
                "Build a REST API using CBD methodology","What's my system info?"].map(s => (
                <button key={s} onClick={() => setInput(s)} style={{
                  padding: "6px 14px", background: "#0a1a0a", border: "1px solid #1a3a1a",
                  color: "#5a9a5a", borderRadius: 4, cursor: "pointer", fontSize: 11 }}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => <MessageBubble key={i} msg={msg} />)}

        {pendingConfirms.map(c => (
          <ConfirmDialog key={c.confirm_id} confirm={c} onConfirm={handleConfirm} onCancel={handleCancel} />
        ))}

        {streaming && (
          <div style={{ color: "#2a5a2a", fontSize: 11, padding: "4px 38px", animation: "pulse 1s infinite" }}>
            {reactMode && reactActiveRef.current ? `🔁 ReAct iter ${reactIterRef.current} — thinking...` : "◈ thinking..."}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Input bar ── */}
      <div style={{ borderTop: "1px solid #1a2a1a", background: "#060c06",
        maxWidth: 900, width: "100%", margin: "0 auto", position: "sticky", bottom: 0 }}>

        {/* ── Mode toggles row ── */}
        <div style={{ padding: "8px 16px 0", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Toggle
            checked={autoConfirm}
            onChange={setAutoConfirm}
            color="#ffaa44"
            label="⚡ Auto-confirm"
            title="Automatically approve all destructive actions without prompting. Use with caution."
          />
          <Toggle
            checked={reactMode}
            onChange={setReactMode}
            color="#4a9aff"
            label="🔁 ReAct"
            title="Enable self-healing ReAct loop: agent will reason, act, observe results, and self-correct errors automatically until the task is complete."
          />
          {reactMode && (
            <span style={{ color: "#4a9aff66", fontSize: 9, fontFamily: "monospace" }}>
              max {MAX_REACT_ITER} iterations · auto self-corrects on error
            </span>
          )}
          {autoConfirm && (
            <span style={{ color: "#ffaa4466", fontSize: 9, fontFamily: "monospace" }}>
              ⚠ destructive actions will execute without confirmation
            </span>
          )}
        </div>

        {/* ── Textarea + buttons row ── */}
        <div style={{ padding: "8px 16px 12px" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
            <div style={{ flex: 1, border: `1px solid ${reactMode ? "#2a4a7a" : "#2a4a2a"}`, borderRadius: 8,
              background: "#0a0f0a", overflow: "hidden",
              boxShadow: streaming ? (reactMode ? "0 0 8px #4a9aff22" : "0 0 8px #4FFFFF22") : "none",
              transition: "box-shadow 0.3s, border-color 0.3s",
              animation: reactMode && reactActiveRef.current ? "react-pulse 2s infinite" : "none" }}>
              <textarea value={input} onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    reactMode ? startReactLoop() : sendMessage();
                  }
                }}
                placeholder={reactMode
                  ? "Describe task for ReAct agent... (will self-heal on errors)"
                  : "Ask the agent anything... (Shift+Enter for newline)"}
                rows={1} style={{ width: "100%", padding: "10px 14px", background: "transparent",
                  border: "none", color: "#90ff90", fontFamily: "inherit", fontSize: 13,
                  resize: "none", lineHeight: 1.5, minHeight: 42 }} />
            </div>

            {/* Continue button */}
            <button
              onClick={sendContinue}
              disabled={!canContinue}
              title="Send 'continue' to nudge the agent to keep going"
              style={{
                padding: "10px 12px", borderRadius: 8, fontSize: 12, transition: "all 0.2s",
                background: canContinue ? "#0a1a2a" : "#0a0a0a",
                border: `1px solid ${canContinue ? "#2a5a9a" : "#2a2a2a"}`,
                color: canContinue ? "#6a9aff" : "#3a3a3a",
                cursor: canContinue ? "pointer" : "not-allowed",
                whiteSpace: "nowrap", fontFamily: "monospace",
              }}>
              ▷▷
            </button>

            {/* Send button */}
            <button
              onClick={() => reactMode ? startReactLoop() : sendMessage()}
              disabled={!canSend}
              title={reactMode ? "Start ReAct loop" : "Send message"}
              style={{
                padding: "10px 18px", borderRadius: 8, fontSize: 16, transition: "all 0.2s",
                background: canSend
                  ? (reactMode ? "linear-gradient(135deg,#0a2a4a,#0a1a3a)" : "linear-gradient(135deg,#1a3a1a,#0a2a0a)")
                  : "#0a0a0a",
                border: `1px solid ${canSend ? (reactMode ? "#4a9aff" : (connected ? "#4a9a4a" : "#6a6a2a")) : "#2a2a2a"}`,
                color: canSend ? (reactMode ? "#90c0ff" : (connected ? "#90ff90" : "#aaaa44")) : "#3a3a3a",
                cursor: canSend ? "pointer" : "not-allowed",
              }}>
              {reactMode ? "⟳" : "▶"}
            </button>
          </div>

          <div style={{ color: "#1a3a1a", fontSize: 10, marginTop: 5, display: "flex", gap: 16 }}>
            <span>Enter to send · Shift+Enter newline</span>
            {autoConfirm && <span style={{ color: "#5a4a1a" }}>⚡ auto-confirm ON</span>}
            {reactMode && <span style={{ color: "#1a3a5a" }}>🔁 ReAct ON — self-healing enabled</span>}
          </div>
        </div>
      </div>

      {/* ── Settings overlay ── */}
      {settingsOpen && (
        <SettingsTab
          onClose={() => setSettingsOpen(false)}
          onSaved={(data) => {
            setProviderInfo(prev => ({ ...prev, ...data }));
            fetch(`${API_URL}/config`).then(r => r.json()).then(setProviderInfo).catch(() => {});
          }}
        />
      )}
    </div>
  );
}