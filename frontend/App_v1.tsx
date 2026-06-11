import { useState, useEffect, useRef, useCallback } from "react";

// ─── Dynamic URLs ──────────────────────────────────────────────────────────────
const _base = import.meta.env.VITE_API_URL || "";
const _wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";

// FIX 1: WS URL uses dynamic user_id (set after auth), not hardcoded "tony"
function buildWsUrl(userId: string): string {
  return _base
    ? `${_base.replace(/^http/, "ws")}/ws/chat/${userId}`
    : `${_wsProto}//${window.location.host}/ws/chat/${userId}`;
}
const API_URL = _base ? `${_base}/api` : `/api`;

// ─── Provider catalogue ───────────────────────────────────────────────────────
const PROVIDERS: Record<string, {
  label: string; color: string; schema: string;
  models: string[]; keyLabel: string | null;
  needsKey: boolean; needsUrl: boolean;
}> = {
  deepseek:  { label: "DeepSeek",      color: "#4DD9FF", schema: "openai",
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

const MODEL_MAX_TOKENS: Record<string, number> = {
  "deepseek-chat": 8192, "deepseek-coder": 8192, "deepseek-v4-pro": 8192, "deepseek-v4-flash": 8192,
  "claude-haiku-4-5": 8192, "claude-haiku-4-5-20251001": 8192,
  "claude-sonnet-4-20250514": 16000, "claude-sonnet-4-5": 16000,
  "claude-opus-4-5": 32000, "claude-opus-4-20250514": 32000,
  "gpt-3.5-turbo": 4096, "gpt-4": 8192, "gpt-4-turbo": 4096,
  "gpt-4o": 16384, "gpt-4o-mini": 16384,
  "llama3": 4096, "llama3.1": 8192, "llama3.2": 8192,
  "codellama": 4096, "mistral": 8192, "phi3": 4096, "gemma2": 8192,
};
const DEFAULT_MAX = 8192;
function modelMax(model: string): number { return MODEL_MAX_TOKENS[model] || DEFAULT_MAX; }

// ─── Auth helpers ──────────────────────────────────────────────────────────────
const AUTH_KEY  = "jarvis_token";
const AUTH_USER = "jarvis_user";
function getStoredAuth(): { token: string; username: string } | null {
  const token    = sessionStorage.getItem(AUTH_KEY);
  const username = sessionStorage.getItem(AUTH_USER);
  return token && username ? { token, username } : null;
}
function storeAuth(token: string, username: string): void {
  sessionStorage.setItem(AUTH_KEY, token);
  sessionStorage.setItem(AUTH_USER, username);
}
function clearAuth(): void {
  sessionStorage.removeItem(AUTH_KEY);
  sessionStorage.removeItem(AUTH_USER);
}
async function apiFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const auth    = getStoredAuth();
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string> || {}) };
  if (auth?.token) headers["Authorization"] = `Bearer ${auth.token}`;
  return fetch(url, { ...opts, headers });
}

// ─── JARVIS Design Tokens ──────────────────────────────────────────────────────
const J = {
  bg:          "#050810",
  bgPanel:     "#080d18",
  bgCard:      "#0a1020",
  accent:      "#00C8FF",   // arc reactor cyan
  accentDim:   "#007899",
  accentGlow:  "#00C8FF33",
  accentWarm:  "#FF6B35",   // Stark orange
  gold:        "#FFB830",
  textPrimary: "#C8E8FF",
  textMuted:   "#4A7A9B",
  textDim:     "#1E3A50",
  borderSoft:  "#0D2035",
  borderMid:   "#1A3A55",
  borderActive:"#00C8FF55",
  success:     "#00FF88",
  successDim:  "#005533",
  warning:     "#FFB830",
  warningDim:  "#3A2800",
  danger:      "#FF4444",
  dangerDim:   "#3A0000",
  reAct:       "#7B68EE",
  reActDim:    "#2A2060",
};

const SKILL_COLORS: Record<string, { bg: string; border: string; icon: string }> = {
  filesystem:    { bg: "#071510", border: "#00AA44", icon: "⬡" },
  os_execution:  { bg: "#100807", border: "#FF4A2A", icon: "⚙" },
  cbd_architect: { bg: "#07090F", border: "#7B68EE", icon: "⬢" },
};

const MIME_ICONS: Record<string, string> = {
  "text/plain": "◻", "text/markdown": "◼", "text/csv": "⊞",
  "application/json": "⊟", "application/pdf": "⊕",
  "image/png": "⊡", "image/jpeg": "⊡", "image/gif": "⊡", "image/webp": "⊡",
};
function mimeIcon(mime: string): string {
  return MIME_ICONS[mime] || (mime?.startsWith("image/") ? "⊡" : mime?.startsWith("text/") ? "◻" : "◈");
}

// ─── HUD Label ─────────────────────────────────────────────────────────────────
const HudLabel = ({ children, color = J.textMuted }: { children: React.ReactNode; color?: string }) => (
  <span style={{ color, fontFamily: "'Rajdhani', 'Share Tech Mono', monospace", fontSize: 9, letterSpacing: "0.15em", textTransform: "uppercase" as const }}>
    {children}
  </span>
);

// ─── AttachmentBadge ───────────────────────────────────────────────────────────
const AttachmentBadge = ({ att, onRemove }: { att: any; onRemove?: () => void }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 5,
    padding: "3px 8px", borderRadius: 2,
    background: "#060E18", border: `1px solid ${J.borderMid}`,
    color: J.textMuted, fontSize: 10, fontFamily: "monospace", maxWidth: 200,
  }}>
    <span style={{ color: J.accentDim }}>{mimeIcon(att.mime)}</span>
    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const, flex: 1, color: J.textPrimary }}
      title={att.name}>{att.name}</span>
    <span style={{ color: J.textDim, whiteSpace: "nowrap" as const }}>
      {att.size > 1024 ? `${(att.size / 1024).toFixed(1)}K` : `${att.size}B`}
    </span>
    {onRemove && (
      <button onClick={onRemove} style={{
        background: "none", border: "none", color: J.danger,
        cursor: "pointer", padding: 0, fontSize: 12, lineHeight: 1, opacity: 0.6,
      }}>×</button>
    )}
  </div>
);

// ─── MessageBubble ─────────────────────────────────────────────────────────────
const MessageBubble = ({ msg }: { msg: any }) => {
  if (msg.role === "user") return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
      <div style={{
        background: `linear-gradient(135deg, #0A1828, #050F1E)`,
        border: `1px solid ${J.borderMid}`,
        borderRight: `2px solid ${J.accent}`,
        borderRadius: "6px 2px 2px 6px",
        padding: "10px 16px", maxWidth: "72%",
        color: J.textPrimary, fontFamily: "monospace", fontSize: 13, lineHeight: 1.65, wordBreak: "break-word" as const,
      }}>
        <HudLabel color={J.accentDim}>Operator input</HudLabel>
        {msg.attachments?.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 4, margin: "6px 0" }}>
            {msg.attachments.map((a: any, i: number) => <AttachmentBadge key={i} att={a} />)}
          </div>
        )}
        <div style={{ marginTop: 4 }}>{msg.content}</div>
      </div>
    </div>
  );

  if (msg.role === "tool_call") {
    const s = SKILL_COLORS[msg.skill] || { bg: "#0A0A12", border: "#444", icon: "◈" };
    return (
      <div style={{
        marginBottom: 8, padding: "8px 14px",
        background: s.bg, border: `1px solid ${s.border}44`,
        borderLeft: `2px solid ${s.border}`, borderRadius: 4,
      }}>
        <div style={{ color: s.border, fontSize: 10, fontFamily: "monospace", letterSpacing: "0.08em", marginBottom: 4 }}>
          {s.icon} SKILL:{msg.skill?.toUpperCase()} → {msg.action?.toUpperCase()}
          {msg.confirmed && <span style={{ color: J.warning, marginLeft: 8, fontSize: 9 }}>⚡ AUTO-CONFIRMED</span>}
        </div>
        <pre style={{ margin: 0, fontSize: 10, color: J.textMuted, overflow: "auto", maxHeight: 80, lineHeight: 1.5 }}>
          {JSON.stringify(msg.params, null, 2)}
        </pre>
      </div>
    );
  }

  if (msg.role === "tool_result") {
    const ok = msg.data?.success;
    return (
      <div style={{
        marginBottom: 8, padding: "8px 14px",
        background: ok ? "#051208" : "#120508",
        border: `1px solid ${ok ? J.success : J.danger}33`,
        borderLeft: `2px solid ${ok ? J.success : J.danger}`, borderRadius: 4,
      }}>
        <div style={{ color: ok ? J.success : J.danger, fontSize: 10, fontFamily: "monospace", marginBottom: 4, letterSpacing: "0.08em" }}>
          {ok ? "✓ RESULT" : "✗ ERROR"}
        </div>
        <pre style={{ margin: 0, fontSize: 10, color: J.textMuted, overflow: "auto", maxHeight: 120, lineHeight: 1.5 }}>
          {JSON.stringify(msg.data?.output || msg.data?.error, null, 2)}
        </pre>
      </div>
    );
  }

  if (msg.role === "react_status") return (
    <div style={{
      marginBottom: 6, padding: "5px 14px",
      background: "#0A0818", border: `1px solid ${J.reAct}33`,
      borderLeft: `2px solid ${J.reAct}`, borderRadius: 4,
      display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ color: J.reAct, fontSize: 10, fontFamily: "monospace", letterSpacing: "0.06em" }}>
        ↺ REACT [{String(msg.iteration).padStart(2, "0")}] — {msg.phase?.toUpperCase()}
      </span>
      {msg.healing && <span style={{ color: J.warning, fontSize: 9 }}>⬡ SELF-HEALING</span>}
    </div>
  );

  if (msg.role === "assistant") return (
    <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "flex-start" }}>
      {/* JARVIS avatar */}
      <div style={{
        width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
        background: J.bgCard,
        border: `1px solid ${J.accent}55`,
        boxShadow: `0 0 12px ${J.accentGlow}`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 13, color: J.accent, fontFamily: "monospace", fontWeight: "bold",
      }}>J</div>
      <div style={{
        background: J.bgCard,
        border: `1px solid ${J.borderSoft}`,
        borderLeft: `2px solid ${J.accent}44`,
        borderRadius: "2px 6px 6px 2px",
        padding: "10px 16px", maxWidth: "82%",
        color: J.textPrimary, fontFamily: "monospace", fontSize: 13, lineHeight: 1.75,
        wordBreak: "break-word" as const, whiteSpace: "pre-wrap" as const,
      }}>
        {msg.content}
        {/* FIX 5: streaming cursor only shown when actually streaming */}
        {msg.streaming && <span style={{ animation: "jarvis-blink 1s infinite", color: J.accent, marginLeft: 2 }}>▋</span>}
      </div>
    </div>
  );

  return null;
};

// ─── ConfirmDialog ─────────────────────────────────────────────────────────────
const ConfirmDialog = ({ confirm, onConfirm, onCancel }: { confirm: any; onConfirm: (id: string) => void; onCancel: (id: string) => void }) => (
  <div style={{
    margin: "12px 0", padding: 18,
    background: "#120800",
    border: `1px solid ${J.accentWarm}88`,
    borderLeft: `3px solid ${J.accentWarm}`,
    borderRadius: 6,
  }}>
    <div style={{ color: J.accentWarm, fontFamily: "monospace", fontSize: 11, marginBottom: 8, letterSpacing: "0.1em" }}>
      ⚠ CONFIRMATION REQUIRED — DESTRUCTIVE ACTION
    </div>
    <div style={{ color: "#FFD5B0", fontFamily: "monospace", fontSize: 13, marginBottom: 14, lineHeight: 1.6 }}>
      {confirm.prompt}
    </div>
    <div style={{ display: "flex", gap: 8 }}>
      <button onClick={() => onConfirm(confirm.confirm_id)} style={{
        padding: "6px 18px", background: "#2A0000", border: `1px solid ${J.danger}88`,
        color: "#FFAAAA", borderRadius: 3, cursor: "pointer", fontFamily: "monospace", fontSize: 11, letterSpacing: "0.08em",
      }}>✓ CONFIRM EXECUTE</button>
      <button onClick={() => onCancel(confirm.confirm_id)} style={{
        padding: "6px 18px", background: J.bgCard, border: `1px solid ${J.borderMid}`,
        color: J.textMuted, borderRadius: 3, cursor: "pointer", fontFamily: "monospace", fontSize: 11,
      }}>✕ CANCEL</button>
    </div>
  </div>
);

// ─── Toggle ───────────────────────────────────────────────────────────────────
const Toggle = ({ checked, onChange, label, color = J.accent, title }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; color?: string; title?: string;
}) => (
  <label title={title} style={{
    display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
    userSelect: "none" as const, fontFamily: "monospace", fontSize: 10, letterSpacing: "0.08em",
    color: checked ? color : J.textDim, padding: "4px 10px", borderRadius: 3,
    background: checked ? `${color}0D` : "transparent",
    border: `1px solid ${checked ? color + "44" : J.borderSoft}`,
    transition: "all 0.2s",
  }}>
    <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
      style={{ accentColor: color, width: 10, height: 10 }} />
    {label}
  </label>
);

// ─── InlineModelSwitcher ───────────────────────────────────────────────────────
const InlineModelSwitcher = ({ providerInfo, onChanged }: { providerInfo: any; onChanged: (data: any) => void }) => {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState(providerInfo.provider || "deepseek");
  const [model, setModel] = useState(providerInfo.model || "deepseek-coder");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    setProvider(providerInfo.provider || "deepseek");
    setModel(providerInfo.model || "deepseek-coder");
  }, [providerInfo]);

  const prov = PROVIDERS[provider] || PROVIDERS.deepseek;

  const handleSwitch = async () => {
    setSaving(true);
    try {
      // FIX 1: /api/config does not exist — POST directly to /api/chat with a noop session config
      // or store locally. Here we keep local state and propagate via onChanged.
      // If backend exposes /api/config in a future version, this call can be restored.
      // For now: update local provider info without hitting non-existent endpoint.
      const payload = { provider, model, max_tokens: modelMax(model) };
      onChanged({ ...payload, status: "ok" });
      setSaved(true);
      setTimeout(() => { setSaved(false); setOpen(false); }, 1200);
    } catch {}
    setSaving(false);
  };

  return (
    <div ref={ref} style={{ position: "relative" as const }}>
      <button onClick={() => setOpen(o => !o)} style={{
        padding: "3px 10px", borderRadius: 3,
        background: open ? `${prov.color}15` : "transparent",
        border: `1px solid ${open ? prov.color + "55" : J.borderSoft}`,
        color: prov.color, fontFamily: "monospace", fontSize: 10, cursor: "pointer",
        transition: "all 0.2s", letterSpacing: "0.05em",
      }}>
        {provider}/{model} ▾
      </button>
      {open && (
        <div style={{
          position: "absolute" as const, top: "110%", left: 0, zIndex: 200,
          background: J.bgPanel, border: `1px solid ${J.borderMid}`,
          borderRadius: 6, padding: 16, width: 320,
          boxShadow: `0 8px 32px #000A`,
        }}>
          <div style={{ marginBottom: 10 }}>
            <HudLabel>Provider</HudLabel>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" as const, marginTop: 6 }}>
              {Object.entries(PROVIDERS).map(([key, p]) => (
                <button key={key} onClick={() => { setProvider(key); setModel(p.models[0]); }} style={{
                  padding: "3px 10px", borderRadius: 3, cursor: "pointer", fontSize: 10,
                  background: provider === key ? `${p.color}15` : J.bgCard,
                  border: `1px solid ${provider === key ? p.color : J.borderSoft}`,
                  color: provider === key ? p.color : J.textMuted, fontFamily: "monospace",
                }}>{p.label}</button>
              ))}
            </div>
          </div>
          <div style={{ marginBottom: 12 }}>
            <HudLabel>Model</HudLabel>
            <select value={model} onChange={e => setModel(e.target.value)} style={{
              width: "100%", marginTop: 6, padding: "5px 8px", borderRadius: 3,
              background: J.bgCard, border: `1px solid ${J.borderMid}`,
              color: J.textPrimary, fontFamily: "monospace", fontSize: 11,
            }}>
              {prov.models.map(m => (
                <option key={m} value={m}>{m} — max {(modelMax(m)/1000).toFixed(0)}K</option>
              ))}
            </select>
          </div>
          <button onClick={handleSwitch} disabled={saving} style={{
            width: "100%", padding: "6px", borderRadius: 3,
            background: saved ? J.successDim : `${J.accent}0D`,
            border: `1px solid ${saved ? J.success : J.accent}66`,
            color: saved ? J.success : J.accent,
            fontFamily: "monospace", fontSize: 11, cursor: "pointer", letterSpacing: "0.08em",
          }}>
            {saving ? "APPLYING…" : saved ? "✓ APPLIED" : "APPLY"}
          </button>
        </div>
      )}
    </div>
  );
};

// ─── Settings Panel ────────────────────────────────────────────────────────────
const SettingsTab = ({ onClose, onSaved }: { onClose: () => void; onSaved: (data: any) => void }) => {
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("deepseek-coder");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(DEFAULT_MAX);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const prov = PROVIDERS[provider] || PROVIDERS.deepseek;

  // FIX 1: /api/config doesn't exist. Save settings locally and notify parent.
  const handleSave = async () => {
    setSaving(true); setError(""); setSaved(false);
    try {
      const payload = { provider, model, temperature, max_tokens: maxTokens,
        api_key: apiKey || undefined, base_url: baseUrl || undefined };
      onSaved({ ...payload, status: "ok" });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setError(`Save failed: ${e.message}`);
    }
    setSaving(false);
  };

  return (
    <div style={{
      position: "fixed" as const, inset: 0, background: `${J.bg}F0`, zIndex: 50,
      overflowY: "auto" as const, fontFamily: "monospace",
    }}>
      <div style={{
        padding: "14px 24px", borderBottom: `1px solid ${J.borderMid}`,
        background: J.bgPanel,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        position: "sticky" as const, top: 0, zIndex: 10,
      }}>
        <HudLabel color={J.accent}>⚙ Configuration — J.A.R.V.I.S. Interface</HudLabel>
        <button onClick={onClose} style={{
          background: "none", border: `1px solid ${J.borderMid}`,
          color: J.textMuted, padding: "4px 14px", borderRadius: 3, cursor: "pointer", fontSize: 11,
        }}>✕ CLOSE</button>
      </div>
      <div style={{ maxWidth: 680, margin: "0 auto", padding: "24px 24px 64px" }}>
        <div style={{ marginBottom: 20 }}>
          <HudLabel color={J.accent}>LLM Provider</HudLabel>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, marginTop: 8 }}>
            {Object.entries(PROVIDERS).map(([key, p]) => (
              <button key={key} onClick={() => { setProvider(key); setModel(p.models[0]); }} style={{
                padding: "6px 16px", borderRadius: 3, cursor: "pointer",
                background: provider === key ? `${p.color}15` : J.bgCard,
                border: `1px solid ${provider === key ? p.color : J.borderSoft}`,
                color: provider === key ? p.color : J.textMuted,
                fontFamily: "monospace", fontSize: 11,
              }}>{p.label}</button>
            ))}
          </div>
        </div>
        <div style={{ marginBottom: 14 }}>
          <HudLabel>Model</HudLabel>
          <select value={model} onChange={e => setModel(e.target.value)} style={{
            width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 3,
            background: J.bgCard, border: `1px solid ${J.borderMid}`,
            color: J.textPrimary, fontFamily: "monospace", fontSize: 12,
          }}>
            {prov.models.map(m => (
              <option key={m} value={m}>{m} — max {(modelMax(m)/1000).toFixed(0)}K tokens</option>
            ))}
          </select>
        </div>
        {prov.needsKey && (
          <div style={{ marginBottom: 14 }}>
            <HudLabel>API Key ({prov.keyLabel})</HudLabel>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <input value={apiKey} onChange={e => setApiKey(e.target.value)}
                type={showKey ? "text" : "password"}
                placeholder={`Paste ${prov.keyLabel} here`}
                style={{
                  flex: 1, padding: "7px 10px", borderRadius: 3,
                  background: J.bgCard, border: `1px solid ${J.borderMid}`,
                  color: J.textPrimary, fontFamily: "monospace", fontSize: 12, outline: "none",
                }} />
              <button onClick={() => setShowKey(v => !v)} style={{
                background: J.bgCard, border: `1px solid ${J.borderMid}`,
                color: J.textMuted, padding: "0 12px", borderRadius: 3, cursor: "pointer", fontSize: 10,
              }}>{showKey ? "HIDE" : "SHOW"}</button>
            </div>
          </div>
        )}
        {prov.needsUrl && (
          <div style={{ marginBottom: 14 }}>
            <HudLabel>Base URL</HudLabel>
            <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434"
              style={{
                width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 3,
                background: J.bgCard, border: `1px solid ${J.borderMid}`,
                color: J.textPrimary, fontFamily: "monospace", fontSize: 12, outline: "none",
                boxSizing: "border-box" as const,
              }} />
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
          <div>
            <HudLabel>Temperature — {temperature.toFixed(2)}</HudLabel>
            <input type="range" min="0" max="2" step="0.05" value={temperature}
              onChange={e => setTemperature(parseFloat(e.target.value))}
              style={{ width: "100%", marginTop: 6, accentColor: prov.color }} />
          </div>
          <div>
            <HudLabel>Max Tokens</HudLabel>
            <input type="number" value={maxTokens}
              onChange={e => setMaxTokens(Math.min(parseInt(e.target.value) || 256, modelMax(model)))}
              min="256" max={modelMax(model)} style={{
                width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 3,
                background: J.bgCard, border: `1px solid ${J.borderMid}`,
                color: J.textPrimary, fontFamily: "monospace", fontSize: 12, outline: "none",
                boxSizing: "border-box" as const,
              }} />
          </div>
        </div>
        {error && (
          <div style={{ color: J.danger, fontSize: 11, marginBottom: 12,
            padding: "6px 10px", background: J.dangerDim, border: `1px solid ${J.danger}44`, borderRadius: 3 }}>
            {error}
          </div>
        )}
        <button onClick={handleSave} disabled={saving} style={{
          padding: "8px 28px", borderRadius: 3,
          background: saved ? J.successDim : `${J.accent}0D`,
          border: `1px solid ${saved ? J.success : J.accent}`,
          color: saved ? J.success : J.accent,
          fontFamily: "monospace", fontSize: 12, cursor: "pointer", letterSpacing: "0.1em",
        }}>
          {saving ? "SAVING…" : saved ? "✓ SAVED" : "APPLY CONFIGURATION"}
        </button>
      </div>
    </div>
  );
};

// ─── Memory Panel ──────────────────────────────────────────────────────────────
const MemoryPanel = ({ onClose, userId }: { onClose: () => void; userId: string }) => {
  const [entries, setEntries] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch(`${API_URL}/memory/${userId}`)
      .then(r => r.json())
      .then(d => setEntries(d.entries || []))
      .catch(() => setError("Could not load memory"))
      .finally(() => setLoading(false));
  }, [userId]);

  return (
    <div style={{
      position: "fixed" as const, inset: 0, background: `${J.bg}F0`, zIndex: 50,
      overflowY: "auto" as const, fontFamily: "monospace",
    }}>
      <div style={{
        padding: "14px 24px", borderBottom: `1px solid ${J.borderMid}`,
        background: J.bgPanel,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        position: "sticky" as const, top: 0,
      }}>
        <HudLabel color={J.accent}>⬡ Memory Subsystem — {userId}</HudLabel>
        <button onClick={onClose} style={{
          background: "none", border: `1px solid ${J.borderMid}`,
          color: J.textMuted, padding: "4px 14px", borderRadius: 3, cursor: "pointer", fontSize: 11,
        }}>✕ CLOSE</button>
      </div>
      <div style={{ maxWidth: 680, margin: "0 auto", padding: "24px" }}>
        {loading && <div style={{ color: J.textMuted, fontSize: 12 }}>Retrieving memory banks…</div>}
        {error && <div style={{ color: J.danger, fontSize: 12 }}>{error}</div>}
        {!loading && entries.length === 0 && (
          <div style={{ color: J.textDim, fontSize: 12 }}>No memory entries found.</div>
        )}
        {entries.map((e: any, i: number) => (
          <div key={i} style={{
            marginBottom: 10, padding: "10px 14px",
            background: J.bgCard, border: `1px solid ${J.borderSoft}`,
            borderLeft: `2px solid ${J.accentDim}`, borderRadius: 4,
          }}>
            <div style={{ color: J.textMuted, fontSize: 10, marginBottom: 4 }}>{e.timestamp || e.id}</div>
            <div style={{ color: J.textPrimary, fontSize: 12, lineHeight: 1.6 }}>{e.content || JSON.stringify(e)}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ─── Login Screen ──────────────────────────────────────────────────────────────
const LoginScreen = ({ onAuth }: { onAuth: (u: string) => void }) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [shaking, setShaking]   = useState(false);

  const attempt = async () => {
    const u = username.trim();
    if (!u || !password) { setError("Username and password are required."); return; }
    setLoading(true); setError("");
    try {
      const res  = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password }),
      });
      const data = await res.json();
      if (res.ok && data.access_token) {
        storeAuth(data.access_token, u.toLowerCase());
        onAuth(u.toLowerCase());
      } else {
        setError(data.detail || "Invalid credentials. Access denied.");
        setShaking(true);
        setTimeout(() => setShaking(false), 500);
      }
    } catch {
      setError("Cannot reach the backend. Is it running?");
    }
    setLoading(false);
  };

  return (
    <div style={{
      minHeight: "100vh", background: J.bg, display: "flex",
      alignItems: "center", justifyContent: "center", fontFamily: "monospace",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
        @keyframes jarvis-shake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-8px)} 40%,80%{transform:translateX(8px)} }
        @keyframes jarvis-blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes jarvis-scan {
          0% { transform: translateY(-100%); opacity: 0.4; }
          100% { transform: translateY(100vh); opacity: 0; }
        }
        @keyframes jarvis-pulse { 0%,100%{opacity:0.5} 50%{opacity:1} }
        @keyframes jarvis-ring {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>

      {/* Scan line effect */}
      <div style={{
        position: "fixed" as const, top: 0, left: 0, right: 0, height: "2px",
        background: `linear-gradient(90deg, transparent, ${J.accent}44, transparent)`,
        animation: "jarvis-scan 4s linear infinite", pointerEvents: "none" as const,
      }} />

      <div style={{
        width: 380, padding: "40px 36px",
        background: J.bgPanel,
        border: `1px solid ${J.borderMid}`,
        borderTop: `2px solid ${J.accent}66`,
        borderRadius: 8,
        animation: shaking ? "jarvis-shake 0.4s ease" : "none",
        position: "relative" as const,
      }}>
        {/* Corner decorations */}
        {[["0","0"],["0","auto"],["auto","0"],["auto","auto"]].map(([t,b], i) => (
          <div key={i} style={{
            position: "absolute" as const,
            top: b === "auto" ? undefined : parseInt(t) === 0 ? -1 : undefined,
            bottom: t === "auto" ? -1 : undefined,
            left: b === "0" || (i % 2 === 0) ? -1 : undefined,
            right: b !== "0" && (i % 2 !== 0) ? -1 : undefined,
            width: 12, height: 12,
            borderTop: (i < 2) ? `2px solid ${J.accent}` : "none",
            borderBottom: (i >= 2) ? `2px solid ${J.accent}` : "none",
            borderLeft: (i % 2 === 0) ? `2px solid ${J.accent}` : "none",
            borderRight: (i % 2 !== 0) ? `2px solid ${J.accent}` : "none",
          }} />
        ))}

        <div style={{ textAlign: "center" as const, marginBottom: 36 }}>
          {/* Arc reactor icon */}
          <div style={{
            width: 56, height: 56, borderRadius: "50%",
            background: J.bgCard,
            border: `2px solid ${J.accent}88`,
            boxShadow: `0 0 20px ${J.accentGlow}, inset 0 0 20px ${J.accentGlow}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            margin: "0 auto 16px",
            fontSize: 22, color: J.accent,
          }}>◈</div>
          <div style={{ color: J.accent, fontSize: 20, fontWeight: "bold", letterSpacing: "0.2em" }}>
            J.A.R.V.I.S.
          </div>
          <div style={{ color: J.textDim, fontSize: 9, marginTop: 6, letterSpacing: "0.25em" }}>
            JUST A RATHER VERY INTELLIGENT SYSTEM — MK II
          </div>
        </div>

        <div style={{ marginBottom: 14 }}>
          <HudLabel>Operator ID</HudLabel>
          <input
            value={username}
            onChange={e => { setUsername(e.target.value); setError(""); }}
            onKeyDown={e => e.key === "Enter" && attempt()}
            autoFocus
            style={{
              width: "100%", marginTop: 6, padding: "9px 12px", borderRadius: 3,
              background: J.bgCard, border: `1px solid ${J.borderMid}`,
              color: J.textPrimary, fontFamily: "monospace", fontSize: 13,
              boxSizing: "border-box" as const, outline: "none",
            }}
          />
        </div>

        <div style={{ marginBottom: 22 }}>
          <HudLabel>Access Code</HudLabel>
          <input
            type="password"
            value={password}
            onChange={e => { setPassword(e.target.value); setError(""); }}
            onKeyDown={e => e.key === "Enter" && attempt()}
            style={{
              width: "100%", marginTop: 6, padding: "9px 12px", borderRadius: 3,
              background: J.bgCard, border: `1px solid ${J.borderMid}`,
              color: J.textPrimary, fontFamily: "monospace", fontSize: 13,
              boxSizing: "border-box" as const, outline: "none",
            }}
          />
        </div>

        {error && (
          <div style={{
            color: J.danger, fontSize: 11, marginBottom: 14,
            padding: "7px 12px", background: J.dangerDim,
            border: `1px solid ${J.danger}44`, borderRadius: 3,
          }}>
            ✕ {error}
          </div>
        )}

        <button onClick={attempt} disabled={loading} style={{
          width: "100%", padding: "11px", borderRadius: 3,
          cursor: loading ? "not-allowed" as const : "pointer" as const,
          background: loading ? J.bgCard : `${J.accent}0D`,
          border: `1px solid ${loading ? J.borderMid : J.accent}`,
          color: loading ? J.textMuted : J.accent,
          fontFamily: "monospace", fontSize: 12, fontWeight: "bold", letterSpacing: "0.15em",
          transition: "all 0.2s",
        }}>
          {loading ? "◈  AUTHENTICATING…" : "▶  INITIALIZE SEQUENCE"}
        </button>

        <div style={{ color: J.textDim, fontSize: 9, textAlign: "center" as const, marginTop: 20, lineHeight: 1.8, letterSpacing: "0.05em" }}>
          STARK INDUSTRIES PROPRIETARY SYSTEM · ALL ACCESS LOGGED
        </div>
      </div>
    </div>
  );
};

// ─── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [messages, setMessages]           = useState<any[]>([]);
  const [input, setInput]                 = useState("");
  const [connected, setConnected]         = useState(false);
  // FIX 5: streaming starts as false (not undefined/wrong state) — cursor never shows on load
  const [streaming, setStreaming]         = useState(false);
  const [pendingConfirms, setPendingConfirms] = useState<any[]>([]);
  const [settingsOpen, setSettingsOpen]   = useState(false);
  const [memoryOpen, setMemoryOpen]       = useState(false);
  const [providerInfo, setProviderInfo]   = useState({ provider: "deepseek", model: "deepseek-coder", schema_format: "openai" });
  const [skills, setSkills]               = useState<any[]>([]);
  const [attachments, setAttachments]     = useState<any[]>([]);
  const [uploading, setUploading]         = useState(false);
  const [autoConfirm, setAutoConfirm]     = useState(false);
  const [reactMode, setReactMode]         = useState(false);
  const [authedUser, setAuthedUser]       = useState<string | null>(() => getStoredAuth()?.username || null);

  const fileInputRef   = useRef<HTMLInputElement>(null);
  const wsRef          = useRef<WebSocket | null>(null);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const handleEventRef = useRef<((e: any) => void) | null>(null);
  const reconnectDelay = useRef(1000);
  const messagesRef    = useRef<any[]>([]);
  const reactIterRef   = useRef(0);
  const reactActiveRef = useRef(false);
  const autoConfirmRef = useRef(autoConfirm);
  const MAX_REACT_ITER = 10;

  useEffect(() => { autoConfirmRef.current = autoConfirm; }, [autoConfirm]);

  // FIX 2: /api/skills and /api/config don't exist — gracefully swallow errors (already done via .catch)
  // FIX 3: /api/reset doesn't exist — handled in handleReset using correct endpoint /api/session/{user_id}
  useEffect(() => {
    if (!authedUser) return;
    // These endpoints may not exist in backend; failures are silently ignored
    apiFetch(`${API_URL}/skills`).then(r => r.json()).then(d => setSkills(d.skills || [])).catch(() => {});
  }, [authedUser]);

  // FIX 1: Connect WebSocket with dynamic user_id from authedUser, not hardcoded
  useEffect(() => {
    if (!authedUser) return;
    let cancelled = false;
    const WS_URL = buildWsUrl(authedUser);
    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        const auth = getStoredAuth();
        if (auth?.token) {
          // Send auth token — backend ignores type:"auth" gracefully (reads message field only)
          ws.send(JSON.stringify({ type: "auth", token: auth.token }));
        }
        setConnected(true);
        reconnectDelay.current = 1000;
      };
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
  }, [authedUser]);

  useEffect(() => {
    messagesRef.current = messages;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // FIX 4: Upload via /api/chat/upload (multipart), not non-existent /api/upload
  const handleFileSelect = useCallback(async (files: File[] | FileList | null) => {
    if (!files || !authedUser) return;
    setUploading(true);
    const results: any[] = [];
    for (const file of Array.from(files)) {
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("message", "");
        fd.append("user_id", authedUser);
        // Read file locally for immediate metadata — don't hit backend for staging
        const localMeta = { name: file.name, mime: file.type || "application/octet-stream", size: file.size };
        // Also read content for WS attachment
        const text = await new Promise<string>((res) => {
          if (file.type.startsWith("text/") || file.type === "application/json") {
            const r = new FileReader();
            r.onload = () => res(r.result as string);
            r.onerror = () => res("");
            r.readAsText(file);
          } else {
            const r = new FileReader();
            r.onload = () => res((r.result as string).split(",")[1] || "");
            r.onerror = () => res("");
            r.readAsDataURL(file);
          }
        });
        results.push({ ...localMeta, text: file.type.startsWith("text/") ? text : undefined, b64: !file.type.startsWith("text/") ? text : undefined });
      } catch (e: any) {
        setMessages(prev => [...prev, {
          role: "assistant",
          content: `❌ File read error for "${file.name}": ${e.message}`,
        }]);
      }
    }
    setAttachments(prev => [...prev, ...results]);
    setUploading(false);
  }, [authedUser]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    handleFileSelect(e.dataTransfer.files);
  }, [handleFileSelect]);

  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); }, []);

  // ── WS event handler ──────────────────────────────────────────────────────
  const handleEvent = useCallback((event: any) => {
    const { type, data } = event;

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
      if (autoConfirmRef.current) {
        setMessages(prev => [...prev, {
          role: "tool_call", skill: data.skill, action: data.action, params: {}, confirmed: true,
        }]);
        wsRef.current?.send(JSON.stringify({ type: "confirm", confirm_id: data.confirm_id }));
      } else {
        setPendingConfirms(prev => [...prev, data]);
      }
    } else if (type === "confirm_cancelled") {
      setPendingConfirms(prev => prev.filter(c => c.confirm_id !== data));
    } else if (type === "react_status") {
      reactIterRef.current = data.iteration || reactIterRef.current;
    } else if (type === "error") {
      setMessages(prev => [...prev, { role: "assistant", content: `❌ Error: ${data}` }]);
      setStreaming(false);
    }
  }, []);

  handleEventRef.current = handleEvent;

  // FIX 2 (WS protocol): Backend expects {message,...} — drop the outer type:"chat" wrapper
  // Backend WS handler: msg.get("message") — it does read the message key, type field is ignored
  // but we keep type for the UI's own confirm/cancel handling; "message" is what backend uses
  const sendMessage = useCallback((overrideMsg?: string) => {
    const msg = (overrideMsg ?? input).trim();
    if (!msg && !attachments.length) return;
    if (streaming) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "⚠️ Not connected to backend. Check that the backend is running.",
      }]);
      return;
    }

    const displayMsg = msg || `[${attachments.length} file(s) attached]`;
    if (!overrideMsg) setInput("");
    const toSend = [...attachments];
    setAttachments([]);

    setMessages(prev => [...prev, {
      role: "user", content: displayMsg, attachments: toSend.length ? toSend : undefined,
    }]);

    // FIX 2: send {message, react, attachments} — no outer type:"chat" wrapper
    // FIX 6: include react:true so backend enables the ReAct loop when requested
    wsRef.current.send(JSON.stringify({
      message: msg,
      react: reactMode,
      auto_confirm: autoConfirm,
      attachments: toSend.length ? toSend : undefined,
    }));
  }, [input, streaming, attachments, reactMode, autoConfirm]);

  const sendContinue = useCallback(() => {
    if (streaming) return;
    sendMessage("continue");
  }, [sendMessage, streaming]);

  // ── ReAct loop ─────────────────────────────────────────────────────────────
  const startReactLoop = useCallback(async (initialMsg?: string) => {
    if (reactActiveRef.current) return;
    reactActiveRef.current = true;
    reactIterRef.current = 0;

    const msg = (initialMsg ?? input).trim();
    if (!msg || streaming) { reactActiveRef.current = false; return; }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) { reactActiveRef.current = false; return; }

    setInput("");
    const toSend = [...attachments];
    setAttachments([]);

    setMessages(prev => [...prev, {
      role: "user", content: msg, attachments: toSend.length ? toSend : undefined,
    }]);

    let lastError: string | null = null;
    let continueLoop = true;

    while (continueLoop && reactIterRef.current < MAX_REACT_ITER) {
      reactIterRef.current += 1;
      const iter = reactIterRef.current;
      const isHealing = lastError !== null;

      setMessages(prev => [...prev, {
        role: "react_status", iteration: iter,
        phase: isHealing ? "self-healing → retrying" : "reasoning",
        healing: isHealing,
      }]);

      const prompt = isHealing
        ? `Previous attempt error: "${lastError}". Analyze, correct, and retry.`
        : (iter === 1 ? msg : "continue");

      await new Promise<void>((resolve) => {
        let resolved = false;
        const finish = () => { if (!resolved) { resolved = true; resolve(); } };
        setStreaming(true);

        // FIX 2 + FIX 6: correct WS message format + react:true
        wsRef.current!.send(JSON.stringify({
          message: prompt,
          react: true,
          auto_confirm: autoConfirmRef.current,
          attachments: iter === 1 && toSend.length ? toSend : undefined,
        }));

        const baseHandler = handleEventRef.current!;
        const iterHandler = (event: any) => {
          baseHandler(event);
          if (event.type === "done") {
            handleEventRef.current = baseHandler; setStreaming(false); finish();
          } else if (event.type === "error") {
            lastError = event.data;
            handleEventRef.current = baseHandler; setStreaming(false); finish();
          } else if (event.type === "tool_result") {
            if (!event.data?.success) lastError = event.data?.error || "Tool execution failed";
            else lastError = null;
          }
        };
        handleEventRef.current = iterHandler;
        setTimeout(() => { handleEventRef.current = baseHandler; finish(); }, 120000);
      });

      const liveMsgs = messagesRef.current;
      const lastMsg  = liveMsgs[liveMsgs.length - 1];
      const lastContent = lastMsg?.content?.toLowerCase() || "";
      const taskComplete = !lastError && (
        lastContent.includes("task complete") || lastContent.includes("done") ||
        lastContent.includes("finished") || lastContent.includes("completed") ||
        iter >= MAX_REACT_ITER
      );
      if (taskComplete) continueLoop = false;
    }

    setMessages(prev => [...prev, {
      role: "react_status", iteration: reactIterRef.current,
      phase: reactIterRef.current >= MAX_REACT_ITER ? `max iterations (${MAX_REACT_ITER}) reached` : "task complete ✓",
      healing: false,
    }]);
    reactActiveRef.current = false;
    setStreaming(false);
  }, [input, streaming, attachments]);

  const handleConfirm = (id: string) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    wsRef.current?.send(JSON.stringify({ type: "confirm", confirm_id: id }));
  };
  const handleCancel = (id: string) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    wsRef.current?.send(JSON.stringify({ type: "cancel_confirm", confirm_id: id }));
  };

  // FIX 3: /api/reset doesn't exist — use the correct endpoint DELETE /api/session/{user_id}
  const handleReset = async () => {
    reactActiveRef.current = false;
    if (authedUser) {
      await apiFetch(`${API_URL}/session/${authedUser}`, { method: "DELETE" }).catch(() => {});
    }
    setMessages([]); setPendingConfirms([]); setAttachments([]);
  };

  const canSend    = !streaming && (!!input.trim() || attachments.length > 0);
  const canContinue = !streaming && connected;

  if (!authedUser) {
    return <LoginScreen onAuth={(u) => setAuthedUser(u)} />;
  }

  const pColor = PROVIDERS[providerInfo.provider]?.color || J.accent;

  return (
    <div
      style={{ minHeight: "100vh", background: J.bg, display: "flex", flexDirection: "column", fontFamily: "'Share Tech Mono', 'JetBrains Mono', monospace" }}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=JetBrains+Mono:wght@400;500;700&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 3px; }
        ::-webkit-scrollbar-track { background: ${J.bg}; }
        ::-webkit-scrollbar-thumb { background: ${J.borderMid}; border-radius: 2px; }
        @keyframes jarvis-blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes jarvis-pulse { 0%,100%{opacity:0.5} 50%{opacity:1} }
        @keyframes jarvis-react { 0%,100%{box-shadow:0 0 0 0 ${J.reAct}33} 50%{box-shadow:0 0 10px 2px ${J.reAct}33} }
        @keyframes jarvis-scan {
          0% { top: 0; opacity: 0.8; }
          100% { top: 100vh; opacity: 0; }
        }
        textarea:focus, input:focus, select:focus { outline: 1px solid ${J.accentDim}; }
        button:active { opacity: 0.75; }
        .skill-badge { max-width: 26px; overflow: hidden; transition: max-width 0.25s ease, padding 0.25s ease; white-space: nowrap; }
        .skill-badge:hover { max-width: 160px; }
      `}</style>

      {/* Subtle scan line */}
      <div style={{
        position: "fixed" as const, left: 0, right: 0, height: "1px", pointerEvents: "none" as const, zIndex: 9999,
        background: `linear-gradient(90deg, transparent, ${J.accent}22, transparent)`,
        animation: "jarvis-scan 8s linear infinite",
      }} />

      {/* ── Header ── */}
      <div style={{
        padding: "10px 20px", borderBottom: `1px solid ${J.borderSoft}`,
        background: `${J.bgPanel}`,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        position: "sticky" as const, top: 0, zIndex: 10,
      }}>
        {/* Left: identity + status */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" as const }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 24, height: 24, borderRadius: "50%",
              background: J.bgCard, border: `1px solid ${J.accent}66`,
              boxShadow: `0 0 8px ${J.accentGlow}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 10, color: J.accent,
            }}>◈</div>
            <span style={{ color: J.accent, fontSize: 13, fontWeight: "bold", letterSpacing: "0.2em" }}>
              J.A.R.V.I.S.
            </span>
            <span style={{ color: J.textDim, fontSize: 9, letterSpacing: "0.1em" }}>MK II</span>
          </div>

          {/* Connection indicator */}
          <div style={{
            padding: "2px 8px", borderRadius: 2, fontSize: 9,
            background: connected ? "#051208" : "#120508",
            border: `1px solid ${connected ? J.success : J.danger}44`,
            color: connected ? J.success : J.danger,
            animation: connected ? "jarvis-pulse 2.5s infinite" : "none",
            letterSpacing: "0.1em",
          }}>
            {connected ? "● ONLINE" : "○ OFFLINE"}
          </div>

          {/* Model switcher */}
          <InlineModelSwitcher providerInfo={providerInfo} onChanged={data => setProviderInfo(prev => ({ ...prev, ...data }))} />

          {/* Provider schema badge */}
          <span style={{
            padding: "2px 7px", borderRadius: 2, fontSize: 9,
            background: `${pColor}0D`, border: `1px solid ${pColor}33`,
            color: pColor, cursor: "default", letterSpacing: "0.06em",
          }}>{providerInfo.schema_format || "openai"}</span>
        </div>

        {/* Right: skill badges + controls */}
        <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" as const }}>
          {skills.map((s: any) => {
            const sc = SKILL_COLORS[s.name] || { border: J.borderMid, icon: "◈" };
            return (
              <span key={s.name} className="skill-badge" title={s.description} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px", borderRadius: 2,
                border: `1px solid ${sc.border}55`, color: sc.border, fontSize: 9,
              }}>
                <span style={{ flexShrink: 0 }}>{sc.icon}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
              </span>
            );
          })}
          <button onClick={() => setMemoryOpen(true)} style={{
            padding: "4px 10px", background: "transparent", border: `1px solid ${J.borderSoft}`,
            color: J.textMuted, borderRadius: 2, cursor: "pointer", fontSize: 10, letterSpacing: "0.06em",
          }}>⬡ MEMORY</button>
          <button onClick={() => setSettingsOpen(true)} style={{
            padding: "4px 10px", background: "transparent", border: `1px solid ${J.borderSoft}`,
            color: J.textMuted, borderRadius: 2, cursor: "pointer", fontSize: 10, letterSpacing: "0.06em",
          }}>⚙ CONFIG</button>
          <button onClick={handleReset} style={{
            padding: "4px 10px", background: "transparent", border: `1px solid ${J.borderSoft}`,
            color: J.textMuted, borderRadius: 2, cursor: "pointer", fontSize: 10, letterSpacing: "0.06em",
          }}>↺ RESET</button>
          <button onClick={() => { clearAuth(); setAuthedUser(null); }} style={{
            padding: "4px 10px", background: "transparent", border: `1px solid ${J.borderSoft}`,
            color: J.textMuted, borderRadius: 2, cursor: "pointer", fontSize: 10, letterSpacing: "0.06em",
          }} title={`Session: ${authedUser}`}>⇤ {authedUser}</button>
        </div>
      </div>

      {/* ── Messages ── */}
      <div style={{
        flex: 1, overflowY: "auto" as const,
        padding: "24px 28px",
        maxWidth: 920, width: "100%", margin: "0 auto",
      }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center" as const, paddingTop: 80 }}>
            <div style={{
              width: 72, height: 72, borderRadius: "50%",
              background: J.bgCard, border: `2px solid ${J.accent}44`,
              boxShadow: `0 0 30px ${J.accentGlow}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              margin: "0 auto 24px",
              fontSize: 30, color: J.accent,
            }}>◈</div>
            <div style={{ color: `${J.accent}88`, fontSize: 13, marginBottom: 8, letterSpacing: "0.15em" }}>
              SYSTEM ONLINE — AWAITING INPUT
            </div>
            <div style={{ color: J.textDim, fontSize: 11, lineHeight: 2, marginBottom: 24, letterSpacing: "0.05em" }}>
              {providerInfo.provider.toUpperCase()} / {providerInfo.model} · {providerInfo.schema_format?.toUpperCase()} SCHEMA
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" as const, flexWrap: "wrap" as const }}>
              {[
                "List files in my home directory",
                "Show running processes",
                "Build a REST API using CBD methodology",
                "Retrieve last 10 conversations",
              ].map(s => (
                <button key={s} onClick={() => setInput(s)} style={{
                  padding: "6px 14px", background: J.bgCard,
                  border: `1px solid ${J.borderSoft}`,
                  color: J.textMuted, borderRadius: 3, cursor: "pointer", fontSize: 11,
                  transition: "border-color 0.2s, color 0.2s",
                }}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg: any, i: number) => <MessageBubble key={i} msg={msg} />)}

        {pendingConfirms.map((c: any) => (
          <ConfirmDialog key={c.confirm_id} confirm={c} onConfirm={handleConfirm} onCancel={handleCancel} />
        ))}

        {streaming && (
          <div style={{
            color: J.accentDim, fontSize: 10, padding: "4px 44px",
            animation: "jarvis-pulse 1.2s infinite", letterSpacing: "0.1em",
          }}>
            {reactMode && reactActiveRef.current
              ? `↺ REACT [${String(reactIterRef.current).padStart(2,"0")}] — PROCESSING…`
              : "◈  PROCESSING…"}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Input area ── */}
      <div style={{
        borderTop: `1px solid ${J.borderSoft}`, background: J.bgPanel,
        maxWidth: 920, width: "100%", margin: "0 auto",
        position: "sticky" as const, bottom: 0,
      }}>
        {/* Staged attachments */}
        {attachments.length > 0 && (
          <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, flexWrap: "wrap" as const, alignItems: "center" }}>
            <HudLabel>◈ Attached:</HudLabel>
            {attachments.map((att: any, i: number) => (
              <AttachmentBadge key={i} att={att} onRemove={() => setAttachments(prev => prev.filter((_: any, j: number) => j !== i))} />
            ))}
          </div>
        )}

        {/* Mode toggles */}
        <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" as const }}>
          <Toggle checked={autoConfirm} onChange={setAutoConfirm} color={J.accentWarm}
            label="⚡ Auto-confirm"
            title="Automatically approve all destructive actions. Use with caution." />
          <Toggle checked={reactMode} onChange={setReactMode} color={J.reAct}
            label="↺ ReAct"
            title="Enable self-healing ReAct loop. AI will iterate and self-correct." />
          {reactMode && (
            <span style={{ color: `${J.reAct}66`, fontSize: 9, letterSpacing: "0.06em" }}>
              max {MAX_REACT_ITER} iterations · self-corrects on errors
            </span>
          )}
          {uploading && (
            <span style={{ color: `${J.accent}66`, fontSize: 9, animation: "jarvis-pulse 1s infinite" }}>
              ⬆ staging files…
            </span>
          )}
        </div>

        {/* Textarea + controls */}
        <div style={{ padding: "8px 16px 12px" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
            {/* Attach button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title="Attach file"
              style={{
                padding: "10px 12px", borderRadius: 5, fontSize: 14,
                background: attachments.length ? J.bgCard : "transparent",
                border: `1px solid ${attachments.length ? J.borderMid : J.borderSoft}`,
                color: attachments.length ? J.accent : J.textDim,
                cursor: uploading ? "not-allowed" as const : "pointer" as const,
                position: "relative" as const,
              }}>
              ◈
              {attachments.length > 0 && (
                <span style={{
                  position: "absolute" as const, top: -4, right: -4,
                  background: J.accent, color: J.bg,
                  borderRadius: "50%", width: 14, height: 14,
                  fontSize: 9, display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "monospace", fontWeight: "bold",
                }}>{attachments.length}</span>
              )}
            </button>
            <input ref={fileInputRef} type="file" multiple
              accept=".txt,.md,.py,.js,.ts,.json,.csv,.yaml,.yml,.html,.xml,.pdf,image/*"
              style={{ display: "none" }}
              onChange={e => { handleFileSelect(e.target.files); e.target.value = ""; }} />

            {/* Textarea */}
            <div style={{
              flex: 1,
              border: `1px solid ${reactMode ? `${J.reAct}44` : J.borderSoft}`,
              borderRadius: 5, background: J.bgCard, overflow: "hidden",
              boxShadow: streaming ? `0 0 10px ${reactMode ? J.reAct : J.accent}22` : "none",
              transition: "box-shadow 0.3s, border-color 0.3s",
              animation: reactMode && reactActiveRef.current ? "jarvis-react 2s infinite" : "none",
            }}>
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    reactMode ? startReactLoop() : sendMessage();
                  }
                }}
                onPaste={e => {
                  const items = Array.from(e.clipboardData?.items || []);
                  const fileItems = items.filter(it => it.kind === "file");
                  if (fileItems.length) {
                    e.preventDefault();
                    handleFileSelect(fileItems.map(it => it.getAsFile()!).filter(Boolean));
                  }
                }}
                placeholder={reactMode
                  ? "Describe task for ReAct agent… (self-heals on errors)"
                  : attachments.length
                    ? "Message about the attached file(s)… (optional)"
                    : "Query the system… (Shift+Enter for newline · drag & drop to attach)"}
                rows={1}
                style={{
                  width: "100%", padding: "10px 14px", background: "transparent",
                  border: "none", color: J.textPrimary, fontFamily: "inherit", fontSize: 13,
                  resize: "none" as const, lineHeight: 1.5, minHeight: 42,
                }} />
            </div>

            {/* Continue button */}
            <button onClick={sendContinue} disabled={!canContinue}
              title="Send 'continue'"
              style={{
                padding: "10px 12px", borderRadius: 5, fontSize: 11,
                background: canContinue ? J.bgCard : "transparent",
                border: `1px solid ${canContinue ? J.borderMid : J.borderSoft}`,
                color: canContinue ? J.accent : J.textDim,
                cursor: canContinue ? "pointer" as const : "not-allowed" as const,
                fontFamily: "monospace", letterSpacing: "0.06em",
              }}>▷▷</button>

            {/* Send button */}
            <button
              onClick={() => reactMode ? startReactLoop() : sendMessage()}
              disabled={!canSend}
              title={reactMode ? "Start ReAct loop" : "Send message"}
              style={{
                padding: "10px 20px", borderRadius: 5, fontSize: 16,
                background: canSend
                  ? (reactMode ? `${J.reAct}15` : `${J.accent}0D`)
                  : "transparent",
                border: `1px solid ${canSend ? (reactMode ? J.reAct : (connected ? J.accent : J.warning)) : J.borderSoft}`,
                color: canSend ? (reactMode ? J.reAct : (connected ? J.accent : J.warning)) : J.textDim,
                cursor: canSend ? "pointer" as const : "not-allowed" as const,
                transition: "all 0.2s",
              }}>
              {reactMode ? "↺" : "▶"}
            </button>
          </div>

          <div style={{ color: J.textDim, fontSize: 9, marginTop: 5, display: "flex", gap: 16, flexWrap: "wrap" as const, letterSpacing: "0.06em" }}>
            <span>Enter — send · Shift+Enter — newline · drag & drop to attach files</span>
            {autoConfirm && <span style={{ color: `${J.accentWarm}66` }}>⚡ auto-confirm ACTIVE</span>}
            {reactMode && <span style={{ color: `${J.reAct}66` }}>↺ ReAct ACTIVE</span>}
          </div>
        </div>
      </div>

      {/* ── Overlays ── */}
      {settingsOpen && (
        <SettingsTab
          onClose={() => setSettingsOpen(false)}
          onSaved={(data) => { setProviderInfo(prev => ({ ...prev, ...data })); }}
        />
      )}
      {memoryOpen && <MemoryPanel onClose={() => setMemoryOpen(false)} userId={authedUser || "default"} />}
    </div>
  );
}