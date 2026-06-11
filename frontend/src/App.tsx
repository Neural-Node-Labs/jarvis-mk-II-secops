import { useState, useEffect, useRef, useCallback } from "react";

// ─── Dynamic URLs ──────────────────────────────────────────────────────────────
const _base = (import.meta as any).env?.VITE_API_URL || "";
const _wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
function buildWsUrl(userId: string): string {
  return _base
    ? `${_base.replace(/^http/, "ws")}/ws/chat/${userId}`
    : `${_wsProto}//${window.location.host}/ws/chat/${userId}`;
}
const API_URL = _base ? `${_base}/api` : `/api`;

// ─── Providers ────────────────────────────────────────────────────────────────
const PROVIDERS: Record<string, { label: string; color: string; models: string[]; needsKey: boolean; needsUrl: boolean; keyLabel: string | null }> = {
  deepseek:  { label: "DeepSeek",  color: "#00C8FF", models: ["deepseek-chat","deepseek-coder","deepseek-v4-pro","deepseek-v4-flash"], needsKey: true,  needsUrl: false, keyLabel: "DEEPSEEK_API_KEY" },
  anthropic: { label: "Anthropic", color: "#CC785C", models: ["claude-sonnet-4-20250514","claude-opus-4-5","claude-haiku-4-5"], needsKey: true, needsUrl: false, keyLabel: "ANTHROPIC_API_KEY" },
  openai:    { label: "OpenAI",    color: "#74AA9C", models: ["gpt-4o","gpt-4o-mini","gpt-4-turbo","gpt-3.5-turbo"], needsKey: true, needsUrl: false, keyLabel: "OPENAI_API_KEY" },
  ollama:    { label: "Ollama",    color: "#FF9F4A", models: ["llama3.2","llama3.1","llama3","mistral","codellama","phi3","gemma2"], needsKey: false, needsUrl: true, keyLabel: null },
};
const MODEL_MAX: Record<string, number> = {
  "deepseek-chat":8192,"deepseek-coder":8192,"deepseek-v4-pro":8192,"deepseek-v4-flash":8192,
  "claude-sonnet-4-20250514":16000,"claude-opus-4-5":32000,"claude-haiku-4-5":8192,
  "gpt-4o":16384,"gpt-4o-mini":16384,"gpt-4-turbo":4096,"gpt-3.5-turbo":4096,
  "llama3":4096,"llama3.1":8192,"llama3.2":8192,"mistral":8192,"codellama":4096,"phi3":4096,"gemma2":8192,
};
function mTok(m: string) { return MODEL_MAX[m] || 8192; }

// ─── Auth ─────────────────────────────────────────────────────────────────────
const AUTH_KEY = "jarvis_token", AUTH_USER = "jarvis_user";
function getAuth() {
  const t = sessionStorage.getItem(AUTH_KEY), u = sessionStorage.getItem(AUTH_USER);
  return t && u ? { token: t, username: u } : null;
}
function storeAuth(t: string, u: string) {
  sessionStorage.setItem(AUTH_KEY, t); sessionStorage.setItem(AUTH_USER, u);
}
function clearAuth() {
  sessionStorage.removeItem(AUTH_KEY); sessionStorage.removeItem(AUTH_USER);
}
async function api(url: string, opts: RequestInit = {}) {
  const auth = getAuth();
  const headers: Record<string, string> = { ...(opts.headers as any || {}) };
  if (auth?.token) headers["Authorization"] = `Bearer ${auth.token}`;
  return fetch(url, { ...opts, headers });
}

// ─── Design Tokens — Iron Man HUD ─────────────────────────────────────────────
const J = {
  bg:           "#030609",
  bgDeep:       "#010305",
  bgPanel:      "#060C14",
  bgCard:       "#08101A",
  bgCardHover:  "#0C1520",
  accent:       "#00C8FF",   // arc reactor cyan
  accentDim:    "#006A88",
  accentGlow:   "#00C8FF18",
  accentGlow2:  "#00C8FF40",
  warm:         "#FF6B35",   // Stark orange
  warmDim:      "#3A1A0A",
  gold:         "#FFB830",
  goldDim:      "#3A2A00",
  textPri:      "#B8D8F0",
  textSec:      "#3A6A8A",
  textDim:      "#1A3A50",
  border:       "#0C1E2E",
  borderMid:    "#1A3A55",
  borderHi:     "#00C8FF44",
  ok:           "#00FF88",
  okDim:        "#003322",
  err:          "#FF4455",
  errDim:       "#2A0008",
  warn:         "#FFB830",
  warnDim:      "#2A1E00",
  react:        "#7B68EE",
  reactDim:     "#1A1640",
};

// ─── Skill registry ────────────────────────────────────────────────────────────
const SKILL_META: Record<string, { border: string; icon: string }> = {
  filesystem:         { border: "#00CC55", icon: "⬡" },
  os_execution:       { border: "#FF5533", icon: "⚙" },
  cbd_architect:      { border: "#7B68EE", icon: "⬢" },
  file_streamer:      { border: "#00B8CC", icon: "⇌" },
  memory_manager:     { border: "#FFB830", icon: "◉" },
  folder_reader:      { border: "#44BBFF", icon: "⊞" },
  jarvis_mkii:        { border: "#FF6B35", icon: "◈" },
};

// ─── CSS ──────────────────────────────────────────────────────────────────────
const GLOBAL_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: ${J.bg}; color: ${J.textPri}; font-family: 'Share Tech Mono', monospace; }
  ::-webkit-scrollbar { width: 3px; height: 3px; }
  ::-webkit-scrollbar-track { background: ${J.bg}; }
  ::-webkit-scrollbar-thumb { background: ${J.borderMid}; border-radius: 2px; }
  ::selection { background: ${J.accent}22; color: ${J.accent}; }
  input, textarea, select { outline: none; }
  input:focus, textarea:focus, select:focus { outline: 1px solid ${J.accentDim}; }
  button { cursor: pointer; font-family: inherit; }
  button:active { opacity: 0.75; }

  @keyframes hud-blink    { 0%,100%{opacity:1} 50%{opacity:0} }
  @keyframes hud-pulse    { 0%,100%{opacity:0.45} 50%{opacity:1} }
  @keyframes hud-glow     { 0%,100%{box-shadow:0 0 6px ${J.accentGlow}} 50%{box-shadow:0 0 18px ${J.accentGlow2}, 0 0 30px ${J.accentGlow}} }
  @keyframes hud-scan     { 0%{top:-2px; opacity:0.9} 100%{top:100vh; opacity:0} }
  @keyframes hud-shake    { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-7px)} 40%,80%{transform:translateX(7px)} }
  @keyframes hud-spin     { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }
  @keyframes hud-react    { 0%,100%{box-shadow:0 0 0 0 ${J.react}22} 50%{box-shadow:0 0 12px 3px ${J.react}33} }
  @keyframes hud-fadein   { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
  @keyframes hud-corner   { 0%,100%{opacity:0.4} 50%{opacity:1} }
  @keyframes hud-progress { 0%{width:0%} 100%{width:100%} }

  .hud-msg { animation: hud-fadein 0.2s ease-out forwards; }
  .hud-badge { max-width: 24px; overflow: hidden; transition: max-width 0.2s, background 0.2s; white-space: nowrap; }
  .hud-badge:hover { max-width: 150px; background: ${J.bgCard} !important; }

  .arc-ring {
    animation: hud-spin 12s linear infinite;
    transform-origin: center;
  }
  .arc-ring-slow { animation: hud-spin 20s linear infinite reverse; transform-origin: center; }
`;

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
const Lbl = ({ c = J.textSec, children }: { c?: string; children: any }) => (
  <span style={{ color: c, fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase", fontFamily: "'Rajdhani', monospace", fontWeight: 600 }}>
    {children}
  </span>
);

const Divider = ({ color = J.border }: { color?: string }) => (
  <div style={{ height: 1, background: `linear-gradient(90deg, transparent, ${color}, transparent)`, margin: "2px 0" }} />
);

// Corner bracket decoration
const Corners = ({ color = J.accent, size = 10 }: { color?: string; size?: number }) => {
  const s: React.CSSProperties = { position: "absolute", width: size, height: size, opacity: 0.7 };
  const b = `1.5px solid ${color}`;
  return (
    <>
      <div style={{ ...s, top: -1, left: -1, borderTop: b, borderLeft: b }} />
      <div style={{ ...s, top: -1, right: -1, borderTop: b, borderRight: b }} />
      <div style={{ ...s, bottom: -1, left: -1, borderBottom: b, borderLeft: b }} />
      <div style={{ ...s, bottom: -1, right: -1, borderBottom: b, borderRight: b }} />
    </>
  );
};

// Arc reactor SVG icon
const ArcReactor = ({ size = 32, glow = true }: { size?: number; glow?: boolean }) => (
  <div style={{
    width: size, height: size, borderRadius: "50%",
    background: `radial-gradient(circle, ${J.accent}22 0%, ${J.bgCard} 60%)`,
    border: `1px solid ${J.accent}66`,
    boxShadow: glow ? `0 0 ${size/2}px ${J.accentGlow2}, inset 0 0 ${size/3}px ${J.accentGlow}` : "none",
    display: "flex", alignItems: "center", justifyContent: "center",
    color: J.accent, fontSize: size * 0.38, flexShrink: 0,
  }}>◈</div>
);

// HUD Status chip
const Chip = ({ label, color = J.textSec, pulse = false, bg }: { label: string; color?: string; pulse?: boolean; bg?: string }) => (
  <div style={{
    padding: "2px 8px", borderRadius: 2, fontSize: 9,
    background: bg || `${color}0A`, border: `1px solid ${color}33`,
    color, letterSpacing: "0.12em", animation: pulse ? "hud-pulse 2.5s infinite" : "none",
    fontFamily: "'Rajdhani', monospace", fontWeight: 600,
  }}>{label}</div>
);

// ─── AttachmentBadge ──────────────────────────────────────────────────────────
const AttBadge = ({ att, onRemove }: { att: any; onRemove?: () => void }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 5,
    padding: "3px 8px", borderRadius: 2,
    background: J.bgCard, border: `1px solid ${J.borderMid}`,
    color: J.textSec, fontSize: 10, maxWidth: 200,
  }}>
    <span style={{ color: J.accentDim }}>◈</span>
    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, color: J.textPri }} title={att.name}>{att.name}</span>
    <span style={{ color: J.textDim }}>{att.size > 1024 ? `${(att.size/1024).toFixed(1)}K` : `${att.size}B`}</span>
    {onRemove && <button onClick={onRemove} style={{ background: "none", border: "none", color: J.err, fontSize: 13, lineHeight: 1, opacity: 0.7, padding: 0 }}>×</button>}
  </div>
);

// ─── MessageBubble ────────────────────────────────────────────────────────────
const Bubble = ({ msg }: { msg: any }) => {
  if (msg.role === "user") return (
    <div className="hud-msg" style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
      <div style={{
        background: `linear-gradient(135deg, #0A1828 0%, #050D18 100%)`,
        border: `1px solid ${J.borderMid}`,
        borderRight: `2px solid ${J.accent}55`,
        borderRadius: "5px 2px 2px 5px",
        padding: "10px 15px", maxWidth: "70%",
        color: J.textPri, fontSize: 13, lineHeight: 1.65, wordBreak: "break-word",
      }}>
        <Lbl c={J.accentDim}>Operator</Lbl>
        {msg.attachments?.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, margin: "6px 0" }}>
            {msg.attachments.map((a: any, i: number) => <AttBadge key={i} att={a} />)}
          </div>
        )}
        <div style={{ marginTop: 4 }}>{msg.content}</div>
      </div>
    </div>
  );

  if (msg.role === "tool_call") {
    const sk = SKILL_META[msg.skill] || { border: J.borderMid, icon: "◈" };
    return (
      <div className="hud-msg" style={{
        marginBottom: 6, padding: "7px 12px",
        background: `${sk.border}08`, border: `1px solid ${sk.border}30`,
        borderLeft: `2px solid ${sk.border}`, borderRadius: 3,
      }}>
        <div style={{ color: sk.border, fontSize: 10, letterSpacing: "0.08em", marginBottom: 3, display: "flex", alignItems: "center", gap: 6 }}>
          <span>{sk.icon}</span>
          <span>{msg.skill?.toUpperCase()} → {msg.action?.toUpperCase()}</span>
          {msg.confirmed && <Chip label="⚡ AUTO" color={J.warn} />}
        </div>
        <pre style={{ margin: 0, fontSize: 10, color: J.textSec, overflow: "auto", maxHeight: 80, lineHeight: 1.5 }}>
          {JSON.stringify(msg.params, null, 2)}
        </pre>
      </div>
    );
  }

  if (msg.role === "tool_result") {
    const ok = msg.data?.success;
    return (
      <div className="hud-msg" style={{
        marginBottom: 6, padding: "7px 12px",
        background: ok ? `${J.ok}08` : `${J.err}08`,
        border: `1px solid ${ok ? J.ok : J.err}25`,
        borderLeft: `2px solid ${ok ? J.ok : J.err}`, borderRadius: 3,
      }}>
        <div style={{ color: ok ? J.ok : J.err, fontSize: 10, letterSpacing: "0.08em", marginBottom: 3 }}>
          {ok ? "✓ RESULT" : "✗ ERROR"}
        </div>
        <pre style={{ margin: 0, fontSize: 10, color: J.textSec, overflow: "auto", maxHeight: 120, lineHeight: 1.5 }}>
          {JSON.stringify(msg.data?.output ?? msg.data?.error, null, 2)}
        </pre>
      </div>
    );
  }

  if (msg.role === "react_status") return (
    <div className="hud-msg" style={{
      marginBottom: 5, padding: "4px 12px",
      background: `${J.react}08`, border: `1px solid ${J.react}25`,
      borderLeft: `2px solid ${J.react}55`, borderRadius: 3,
      display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ color: J.react, fontSize: 10, letterSpacing: "0.08em" }}>
        ↺ REACT [{String(msg.iteration).padStart(2,"0")}] — {msg.phase?.toUpperCase()}
      </span>
      {msg.healing && <Chip label="⬡ SELF-HEALING" color={J.warn} />}
    </div>
  );

  if (msg.role === "assistant") return (
    <div className="hud-msg" style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "flex-start" }}>
      <ArcReactor size={28} />
      <div style={{
        background: J.bgCard, border: `1px solid ${J.border}`,
        borderLeft: `2px solid ${J.accent}30`,
        borderRadius: "2px 5px 5px 2px",
        padding: "10px 15px", maxWidth: "84%",
        color: J.textPri, fontSize: 13, lineHeight: 1.75,
        wordBreak: "break-word", whiteSpace: "pre-wrap",
      }}>
        {msg.content}
        {msg.streaming && <span style={{ animation: "hud-blink 1s infinite", color: J.accent, marginLeft: 2 }}>▋</span>}
      </div>
    </div>
  );

  return null;
};

// ─── Confirm Dialog ───────────────────────────────────────────────────────────
const ConfirmDlg = ({ c, onConfirm, onCancel }: { c: any; onConfirm: (id: string) => void; onCancel: (id: string) => void }) => (
  <div style={{
    margin: "10px 0", padding: 16, position: "relative",
    background: `${J.warn}08`, border: `1px solid ${J.warm}66`,
    borderLeft: `3px solid ${J.warm}`, borderRadius: 4,
  }}>
    <Corners color={J.warm} size={8} />
    <div style={{ color: J.warm, fontSize: 10, letterSpacing: "0.12em", marginBottom: 8 }}>⚠ CONFIRMATION REQUIRED — DESTRUCTIVE ACTION</div>
    <div style={{ color: "#FFD5B0", fontSize: 13, marginBottom: 14, lineHeight: 1.65 }}>{c.prompt}</div>
    <div style={{ display: "flex", gap: 8 }}>
      <button onClick={() => onConfirm(c.confirm_id)} style={{
        padding: "5px 16px", background: J.errDim, border: `1px solid ${J.err}66`,
        color: "#FFAAAA", borderRadius: 3, fontSize: 11, letterSpacing: "0.08em",
      }}>✓ CONFIRM EXECUTE</button>
      <button onClick={() => onCancel(c.confirm_id)} style={{
        padding: "5px 16px", background: J.bgCard, border: `1px solid ${J.borderMid}`,
        color: J.textSec, borderRadius: 3, fontSize: 11,
      }}>✕ CANCEL</button>
    </div>
  </div>
);

// ─── Toggle ───────────────────────────────────────────────────────────────────
const Toggle = ({ on, set, label, color = J.accent, title }: { on: boolean; set: (v: boolean) => void; label: string; color?: string; title?: string }) => (
  <label title={title} style={{
    display: "flex", alignItems: "center", gap: 5, cursor: "pointer",
    userSelect: "none", fontSize: 10, letterSpacing: "0.08em",
    color: on ? color : J.textDim, padding: "4px 10px", borderRadius: 2,
    background: on ? `${color}0C` : "transparent",
    border: `1px solid ${on ? color + "33" : J.border}`,
    transition: "all 0.15s", fontFamily: "'Rajdhani', monospace", fontWeight: 600,
  }}>
    <input type="checkbox" checked={on} onChange={e => set(e.target.checked)}
      style={{ accentColor: color, width: 10, height: 10 }} />
    {label}
  </label>
);

// ─── Model Switcher ───────────────────────────────────────────────────────────
const ModelPicker = ({ info, onChange }: { info: any; onChange: (d: any) => void }) => {
  const [open, setOpen] = useState(false);
  const [prov, setProv] = useState(info.provider || "deepseek");
  const [model, setModel] = useState(info.model || "deepseek-coder");
  const [saved, setSaved] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  useEffect(() => { setProv(info.provider || "deepseek"); setModel(info.model || "deepseek-coder"); }, [info]);

  const P = PROVIDERS[prov] || PROVIDERS.deepseek;

  const apply = () => {
    onChange({ provider: prov, model, max_tokens: mTok(model) });
    setSaved(true); setTimeout(() => { setSaved(false); setOpen(false); }, 1000);
  };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button onClick={() => setOpen(o => !o)} style={{
        padding: "3px 10px", borderRadius: 2, fontSize: 10,
        background: open ? `${P.color}12` : "transparent",
        border: `1px solid ${open ? P.color + "55" : J.border}`,
        color: P.color, letterSpacing: "0.05em", transition: "all 0.2s",
      }}>{prov}/{model} ▾</button>

      {open && (
        <div style={{
          position: "absolute", top: "110%", left: 0, zIndex: 200,
          background: J.bgPanel, border: `1px solid ${J.borderMid}`,
          borderRadius: 5, padding: 14, width: 310,
          boxShadow: `0 12px 40px #000C`,
        }}>
          <Lbl>Provider</Lbl>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6, marginBottom: 12 }}>
            {Object.entries(PROVIDERS).map(([k, p]) => (
              <button key={k} onClick={() => { setProv(k); setModel(p.models[0]); }} style={{
                padding: "3px 10px", borderRadius: 2, fontSize: 10,
                background: prov === k ? `${p.color}15` : J.bgCard,
                border: `1px solid ${prov === k ? p.color : J.border}`,
                color: prov === k ? p.color : J.textSec,
              }}>{p.label}</button>
            ))}
          </div>
          <Lbl>Model</Lbl>
          <select value={model} onChange={e => setModel(e.target.value)} style={{
            width: "100%", marginTop: 6, marginBottom: 12, padding: "5px 8px", borderRadius: 2,
            background: J.bgCard, border: `1px solid ${J.borderMid}`,
            color: J.textPri, fontSize: 11,
          }}>
            {P.models.map(m => <option key={m} value={m}>{m} — {(mTok(m)/1000).toFixed(0)}K</option>)}
          </select>
          <button onClick={apply} style={{
            width: "100%", padding: "6px", borderRadius: 2,
            background: saved ? J.okDim : `${J.accent}0C`,
            border: `1px solid ${saved ? J.ok : J.accent}55`,
            color: saved ? J.ok : J.accent, fontSize: 11, letterSpacing: "0.1em",
          }}>{saved ? "✓ APPLIED" : "APPLY"}</button>
        </div>
      )}
    </div>
  );
};

// ─── Settings Panel ───────────────────────────────────────────────────────────
const SettingsPanel = ({ onClose, onSaved }: { onClose: () => void; onSaved: (d: any) => void }) => {
  const [prov, setProv] = useState("deepseek");
  const [model, setModel] = useState("deepseek-coder");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const [temp, setTemp] = useState(0.7);
  const [maxTok, setMaxTok] = useState(8192);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  const P = PROVIDERS[prov] || PROVIDERS.deepseek;

  const save = async () => {
    setSaving(true); setErr(""); setSaved(false);
    try {
      onSaved({ provider: prov, model, temperature: temp, max_tokens: maxTok, api_key: key || undefined, base_url: url || undefined });
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (e: any) { setErr(`Error: ${e.message}`); }
    setSaving(false);
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F2`, zIndex: 100, overflowY: "auto" }}>
      <div style={{
        padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`,
        background: J.bgPanel, display: "flex", alignItems: "center",
        justifyContent: "space-between", position: "sticky", top: 0, zIndex: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <ArcReactor size={22} glow={false} />
          <Lbl c={J.accent}>System Configuration — J.A.R.V.I.S. MK II</Lbl>
        </div>
        <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕ CLOSE</button>
      </div>
      <div style={{ maxWidth: 660, margin: "0 auto", padding: "28px 24px 80px" }}>
        <div style={{ marginBottom: 20 }}>
          <Lbl c={J.accent}>LLM Provider</Lbl>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            {Object.entries(PROVIDERS).map(([k, p]) => (
              <button key={k} onClick={() => { setProv(k); setModel(p.models[0]); }} style={{
                padding: "6px 16px", borderRadius: 2,
                background: prov === k ? `${p.color}15` : J.bgCard,
                border: `1px solid ${prov === k ? p.color : J.border}`,
                color: prov === k ? p.color : J.textSec, fontSize: 11,
              }}>{p.label}</button>
            ))}
          </div>
        </div>
        <div style={{ marginBottom: 14 }}>
          <Lbl>Model</Lbl>
          <select value={model} onChange={e => setModel(e.target.value)} style={{
            width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 2,
            background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12,
          }}>
            {P.models.map(m => <option key={m} value={m}>{m} — max {(mTok(m)/1000).toFixed(0)}K tokens</option>)}
          </select>
        </div>
        {P.needsKey && (
          <div style={{ marginBottom: 14 }}>
            <Lbl>API Key — {P.keyLabel}</Lbl>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <input value={key} onChange={e => setKey(e.target.value)} type={showKey ? "text" : "password"}
                placeholder={`Enter ${P.keyLabel}`} style={{
                  flex: 1, padding: "7px 10px", borderRadius: 2,
                  background: J.bgCard, border: `1px solid ${J.borderMid}`,
                  color: J.textPri, fontSize: 12,
                }} />
              <button onClick={() => setShowKey(v => !v)} style={{
                background: J.bgCard, border: `1px solid ${J.borderMid}`,
                color: J.textSec, padding: "0 12px", borderRadius: 2, fontSize: 10,
              }}>{showKey ? "HIDE" : "SHOW"}</button>
            </div>
          </div>
        )}
        {P.needsUrl && (
          <div style={{ marginBottom: 14 }}>
            <Lbl>Base URL</Lbl>
            <input value={url} onChange={e => setUrl(e.target.value)} placeholder="http://localhost:11434"
              style={{ width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 2, background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12 }} />
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 22 }}>
          <div>
            <Lbl>Temperature — {temp.toFixed(2)}</Lbl>
            <input type="range" min="0" max="2" step="0.05" value={temp}
              onChange={e => setTemp(parseFloat(e.target.value))}
              style={{ width: "100%", marginTop: 8, accentColor: P.color }} />
          </div>
          <div>
            <Lbl>Max Output Tokens</Lbl>
            <input type="number" value={maxTok}
              onChange={e => setMaxTok(Math.min(parseInt(e.target.value)||256, mTok(model)))}
              min="256" max={mTok(model)} style={{
                width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 2,
                background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12,
              }} />
          </div>
        </div>
        {err && <div style={{ color: J.err, fontSize: 11, marginBottom: 12, padding: "6px 10px", background: J.errDim, border: `1px solid ${J.err}33`, borderRadius: 2 }}>{err}</div>}
        <button onClick={save} disabled={saving} style={{
          padding: "8px 28px", borderRadius: 2,
          background: saved ? J.okDim : `${J.accent}0C`,
          border: `1px solid ${saved ? J.ok : J.accent}`,
          color: saved ? J.ok : J.accent, fontSize: 12, letterSpacing: "0.1em",
        }}>{saving ? "SAVING…" : saved ? "✓ CONFIGURATION SAVED" : "APPLY CONFIGURATION"}</button>
      </div>
    </div>
  );
};

// ─── Memory Panel ─────────────────────────────────────────────────────────────
const MemoryPanel = ({ onClose, userId }: { onClose: () => void; userId: string }) => {
  const [history, setHistory] = useState("");
  const [loading, setLoading] = useState(true);
  const [n, setN] = useState(20);
  const [clearing, setClearing] = useState(false);
  const [cleared, setCleared] = useState(false);

  const load = async (count: number) => {
    setLoading(true);
    try {
      const r = await api(`${API_URL}/memory/${userId}?n=${count}`);
      const d = await r.json();
      setHistory(d.history || "_No memory entries found._");
    } catch { setHistory("_Unable to retrieve memory._"); }
    setLoading(false);
  };

  useEffect(() => { load(n); }, []);

  const clear = async () => {
    setClearing(true);
    try {
      await api(`${API_URL}/memory/${userId}`, { method: "DELETE" });
      setCleared(true); setHistory("_Memory cleared._");
      setTimeout(() => setCleared(false), 2000);
    } catch {}
    setClearing(false);
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F2`, zIndex: 100, display: "flex", flexDirection: "column" }}>
      <div style={{
        padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`,
        background: J.bgPanel, display: "flex", alignItems: "center",
        justifyContent: "space-between", position: "sticky", top: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <ArcReactor size={22} glow={false} />
          <Lbl c={J.gold}>Memory Subsystem — {userId}</Lbl>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Lbl>Show last</Lbl>
          <select value={n} onChange={e => { setN(+e.target.value); load(+e.target.value); }} style={{
            padding: "3px 6px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 11, borderRadius: 2,
          }}>
            {[5,10,20,50,100].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          <button onClick={clear} disabled={clearing} style={{
            padding: "4px 12px", background: J.errDim, border: `1px solid ${J.err}44`,
            color: cleared ? J.ok : "#FF9999", borderRadius: 2, fontSize: 10,
          }}>{cleared ? "✓ CLEARED" : clearing ? "CLEARING…" : "⊗ CLEAR"}</button>
          <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕ CLOSE</button>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px", maxWidth: 860, margin: "0 auto", width: "100%" }}>
        {loading
          ? <div style={{ color: J.textSec, fontSize: 12, padding: 20, textAlign: "center", animation: "hud-pulse 1.5s infinite" }}>Accessing memory banks…</div>
          : <pre style={{ color: J.textSec, fontSize: 12, lineHeight: 1.8, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{history}</pre>
        }
      </div>
    </div>
  );
};

// ─── Login Screen ─────────────────────────────────────────────────────────────
const Login = ({ onAuth }: { onAuth: (u: string) => void }) => {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr]   = useState("");
  const [loading, setLoading] = useState(false);
  const [shaking, setShaking] = useState(false);

  const go = async () => {
    const u = user.trim();
    if (!u || !pass) { setErr("Operator ID and access code required."); return; }
    setLoading(true); setErr("");
    try {
      const res = await fetch(`${API_URL}/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: pass }),
      });
      const data = await res.json();
      if (res.ok && data.access_token) {
        storeAuth(data.access_token, u.toLowerCase());
        onAuth(u.toLowerCase());
      } else {
        setErr(data.detail || "Access denied. Credentials not recognised.");
        setShaking(true); setTimeout(() => setShaking(false), 500);
      }
    } catch { setErr("Backend unreachable. Verify server status."); }
    setLoading(false);
  };

  const inp: React.CSSProperties = {
    width: "100%", padding: "9px 13px", borderRadius: 3,
    background: J.bgCard, border: `1px solid ${J.borderMid}`,
    color: J.textPri, fontSize: 13, boxSizing: "border-box",
  };

  return (
    <div style={{ minHeight: "100vh", background: J.bg, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <style>{GLOBAL_CSS}</style>

      {/* Ambient scan line */}
      <div style={{ position: "fixed", left: 0, right: 0, height: 1, background: `linear-gradient(90deg,transparent,${J.accent}33,transparent)`, animation: "hud-scan 6s linear infinite", pointerEvents: "none", zIndex: 9999 }} />

      {/* Background grid */}
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none",
        backgroundImage: `linear-gradient(${J.border} 1px, transparent 1px), linear-gradient(90deg, ${J.border} 1px, transparent 1px)`,
        backgroundSize: "40px 40px", opacity: 0.4,
      }} />

      <div style={{
        width: 400, padding: "44px 40px", position: "relative",
        background: J.bgPanel,
        border: `1px solid ${J.borderMid}`,
        borderTop: `2px solid ${J.accent}55`,
        borderRadius: 6,
        animation: shaking ? "hud-shake 0.4s ease" : "none",
        boxShadow: `0 0 60px ${J.accentGlow}, 0 0 120px ${J.bgDeep}`,
      }}>
        <Corners color={J.accent} size={12} />

        {/* Arc reactor header */}
        <div style={{ textAlign: "center", marginBottom: 36 }}>
          <div style={{ position: "relative", width: 64, height: 64, margin: "0 auto 18px" }}>
            {/* Spinning rings */}
            <div className="arc-ring" style={{
              position: "absolute", inset: 0, borderRadius: "50%",
              border: `1px solid ${J.accent}33`, borderTop: `1px solid ${J.accent}88`,
            }} />
            <div className="arc-ring-slow" style={{
              position: "absolute", inset: 4, borderRadius: "50%",
              border: `1px solid ${J.accent}22`, borderRight: `1px solid ${J.accent}66`,
            }} />
            <div style={{
              position: "absolute", inset: 0, borderRadius: "50%",
              background: `radial-gradient(circle, ${J.accent}25 0%, ${J.bgCard} 65%)`,
              border: `1px solid ${J.accent}66`,
              boxShadow: `0 0 24px ${J.accentGlow2}, inset 0 0 16px ${J.accentGlow}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 24, color: J.accent,
              animation: "hud-glow 3s ease-in-out infinite",
            }}>◈</div>
          </div>
          <div style={{ color: J.accent, fontSize: 22, letterSpacing: "0.22em", fontFamily: "'Rajdhani', monospace", fontWeight: 700 }}>J.A.R.V.I.S.</div>
          <div style={{ color: J.textDim, fontSize: 9, marginTop: 5, letterSpacing: "0.28em", fontFamily: "'Rajdhani', monospace" }}>JUST A RATHER VERY INTELLIGENT SYSTEM · MK II</div>
          <Divider color={J.borderMid} />
          <div style={{ color: J.textDim, fontSize: 8, marginTop: 6, letterSpacing: "0.2em" }}>STARK INDUSTRIES PROPRIETARY · SECURE ACCESS REQUIRED</div>
        </div>

        <div style={{ marginBottom: 14 }}>
          <Lbl>Operator ID</Lbl>
          <input value={user} onChange={e => { setUser(e.target.value); setErr(""); }}
            onKeyDown={e => e.key === "Enter" && go()} autoFocus style={{ ...inp, marginTop: 6 }} />
        </div>
        <div style={{ marginBottom: 22 }}>
          <Lbl>Access Code</Lbl>
          <input type="password" value={pass} onChange={e => { setPass(e.target.value); setErr(""); }}
            onKeyDown={e => e.key === "Enter" && go()} style={{ ...inp, marginTop: 6 }} />
        </div>

        {err && <div style={{ color: J.err, fontSize: 11, marginBottom: 14, padding: "7px 12px", background: J.errDim, border: `1px solid ${J.err}33`, borderRadius: 2 }}>✕ {err}</div>}

        <button onClick={go} disabled={loading} style={{
          width: "100%", padding: "11px", borderRadius: 3,
          background: loading ? J.bgCard : `${J.accent}0C`,
          border: `1px solid ${loading ? J.borderMid : J.accent}`,
          color: loading ? J.textSec : J.accent,
          fontSize: 12, fontWeight: "bold", letterSpacing: "0.18em",
          transition: "all 0.2s", fontFamily: "'Rajdhani', monospace",
        }}>{loading ? "◈ AUTHENTICATING…" : "▶ INITIALIZE SEQUENCE"}</button>

        <div style={{ color: J.textDim, fontSize: 8, textAlign: "center", marginTop: 20, letterSpacing: "0.08em", lineHeight: 2 }}>
          ALL ACCESS IS MONITORED · UNAUTHORISED USE IS PROHIBITED
        </div>
      </div>
    </div>
  );
};

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [messages,        setMessages]        = useState<any[]>([]);
  const [input,           setInput]           = useState("");
  const [connected,       setConnected]       = useState(false);
  const [streaming,       setStreaming]       = useState(false);  // FIX: starts false, no cursor on load
  const [pendingConfirms, setPendingConfirms] = useState<any[]>([]);
  const [settingsOpen,    setSettingsOpen]    = useState(false);
  const [memoryOpen,      setMemoryOpen]      = useState(false);
  const [provInfo,        setProvInfo]        = useState({ provider: "deepseek", model: "deepseek-coder", schema_format: "openai" });
  const [skills,          setSkills]          = useState<any[]>([]);
  const [attachments,     setAttachments]     = useState<any[]>([]);
  const [uploading,       setUploading]       = useState(false);
  const [autoConfirm,     setAutoConfirm]     = useState(false);
  const [reactMode,       setReactMode]       = useState(false);
  // FIX: user_id from actual logged-in user, never hardcoded
  const [authedUser,      setAuthedUser]      = useState<string | null>(() => getAuth()?.username || null);

  const fileRef        = useRef<HTMLInputElement>(null);
  const wsRef          = useRef<WebSocket | null>(null);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const evtHandlerRef  = useRef<((e: any) => void) | null>(null);
  const reconnDelay    = useRef(1000);
  const msgsRef        = useRef<any[]>([]);
  const reactIterRef   = useRef(0);
  const reactActiveRef = useRef(false);
  const autoConfRef    = useRef(autoConfirm);
  const MAX_ITER       = 30;  // matches backend REACT_MAX_ITERATIONS

  useEffect(() => { autoConfRef.current = autoConfirm; }, [autoConfirm]);

  // Load skills list (graceful fail if endpoint absent)
  useEffect(() => {
    if (!authedUser) return;
    api(`${API_URL}/models`).then(r => r.json()).then(d => {
      // /api/models returns provider catalogue — synthesize skills list
    }).catch(() => {});
    // Try skill list
    api(`${API_URL}/session/${authedUser}`).catch(() => {});
  }, [authedUser]);

  // FIX: WS URL uses actual user_id, reconnects when user changes
  useEffect(() => {
    if (!authedUser) return;
    let cancelled = false;
    const WS = buildWsUrl(authedUser);
    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(WS);
      wsRef.current = ws;
      ws.onopen = () => {
        const auth = getAuth();
        if (auth?.token) ws.send(JSON.stringify({ type: "auth", token: auth.token }));
        setConnected(true); reconnDelay.current = 1000;
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) { setTimeout(connect, reconnDelay.current); reconnDelay.current = Math.min(reconnDelay.current * 2, 15000); }
      };
      ws.onerror = () => ws.close();
      ws.onmessage = e => { try { if (evtHandlerRef.current) evtHandlerRef.current(JSON.parse(e.data)); } catch {} };
    };
    connect();
    return () => { cancelled = true; wsRef.current?.close(); };
  }, [authedUser]);

  useEffect(() => { msgsRef.current = messages; bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // FIX: File handling reads files locally, no upload endpoint needed
  const handleFiles = useCallback(async (files: FileList | File[] | null) => {
    if (!files || !authedUser) return;
    setUploading(true);
    const results: any[] = [];
    for (const file of Array.from(files)) {
      try {
        const content = await new Promise<string>(res => {
          const r = new FileReader();
          if (file.type.startsWith("text/") || ["application/json","application/javascript"].some(t => file.type === t)) {
            r.onload = () => res(r.result as string);
            r.readAsText(file);
          } else {
            r.onload = () => res(((r.result as string).split(",")[1]) || "");
            r.readAsDataURL(file);
          }
          r.onerror = () => res("");
        });
        const isText = file.type.startsWith("text/") || file.type === "application/json";
        results.push({ name: file.name, mime: file.type || "application/octet-stream", size: file.size, ...(isText ? { text: content } : { b64: content }) });
      } catch (e: any) {
        setMessages(prev => [...prev, { role: "assistant", content: `❌ File read error: ${e.message}` }]);
      }
    }
    setAttachments(prev => [...prev, ...results]);
    setUploading(false);
  }, [authedUser]);

  const onDrop = useCallback((e: React.DragEvent) => { e.preventDefault(); handleFiles(e.dataTransfer.files); }, [handleFiles]);
  const onDragOver = useCallback((e: React.DragEvent) => e.preventDefault(), []);

  // ── Event handler ─────────────────────────────────────────────────────────
  const handleEvt = useCallback((ev: any) => {
    const { type, data } = ev;
    if (type === "token") {
      setStreaming(true);
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.streaming)
          return [...prev.slice(0,-1), { ...last, content: last.content + data }];
        return [...prev, { role: "assistant", content: data || "", streaming: true }];
      });
    } else if (type === "done") {
      setStreaming(false);
      setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
      // FIX: If react mode is active server-side, "done" just means the current iteration ended.
      // The backend's own ReAct loop (react=true) handles continuation automatically.
    } else if (type === "tool_call") {
      setMessages(prev => [...prev, { role: "tool_call", ...data }]);
    } else if (type === "tool_result") {
      setMessages(prev => [...prev, { role: "tool_result", data }]);
    } else if (type === "confirm_needed") {
      if (autoConfRef.current) {
        setMessages(prev => [...prev, { role: "tool_call", skill: data.skill, action: data.action, params: {}, confirmed: true }]);
        // FIX: Send confirm via correct WS message format
        wsRef.current?.send(JSON.stringify({ confirm_id: data.confirm_id, message: "__confirm__" }));
      } else {
        setPendingConfirms(prev => [...prev, data]);
      }
    } else if (type === "confirm_cancelled") {
      setPendingConfirms(prev => prev.filter(c => c.confirm_id !== data));
    } else if (type === "react_status") {
      reactIterRef.current = data?.iteration || reactIterRef.current;
      setMessages(prev => [...prev, { role: "react_status", ...data }]);
    } else if (type === "error") {
      setMessages(prev => [...prev, { role: "assistant", content: `❌ ${data}` }]);
      setStreaming(false);
    } else if (type === "stream_start") {
      // backend SSE start acknowledgement — ignore
    }
  }, []);

  evtHandlerRef.current = handleEvt;

  // ── Send — FIX: correct WS payload, react flag, auto-continue behavior ────
  const send = useCallback((override?: string) => {
    const msg = (override ?? input).trim();
    if (!msg && !attachments.length) return;
    if (streaming) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setMessages(prev => [...prev, { role: "assistant", content: "⚠️ Not connected. Verify backend is running." }]);
      return;
    }
    const display = msg || `[${attachments.length} file(s) attached]`;
    if (!override) setInput("");
    const toSend = [...attachments];
    setAttachments([]);
    setMessages(prev => [...prev, { role: "user", content: display, attachments: toSend.length ? toSend : undefined }]);

    // FIX: Backend WS expects {message, react?, auto_confirm?, attachments?}
    // When react=true, backend handles the FULL ReAct loop internally — no need to click Continue
    wsRef.current.send(JSON.stringify({
      message: msg,
      react: reactMode,           // FIX: pass react flag so server loop runs to completion
      auto_confirm: autoConfirm,
      attachments: toSend.length ? toSend : undefined,
    }));
  }, [input, streaming, attachments, reactMode, autoConfirm]);

  // ── Manual continue (for non-react mode only) ─────────────────────────────
  const sendContinue = useCallback(() => { if (!streaming) send("continue"); }, [send, streaming]);

  // ── Client-side ReAct loop (when backend react=false, drives iterations) ──
  // When reactMode=true, we pass react:true to backend and it handles all iterations.
  // This client loop is a FALLBACK for backward compatibility.
  const startClientReact = useCallback(async (initMsg?: string) => {
    if (reactActiveRef.current) return;
    reactActiveRef.current = true;
    reactIterRef.current = 0;
    const msg = (initMsg ?? input).trim();
    if (!msg || streaming || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      reactActiveRef.current = false; return;
    }
    setInput("");
    const toSend = [...attachments];
    setAttachments([]);
    setMessages(prev => [...prev, { role: "user", content: msg, attachments: toSend.length ? toSend : undefined }]);

    let lastErr: string | null = null;
    let go = true;

    while (go && reactIterRef.current < MAX_ITER) {
      reactIterRef.current++;
      const iter = reactIterRef.current;
      const healing = lastErr !== null;

      setMessages(prev => [...prev, { role: "react_status", iteration: iter, phase: healing ? "self-healing" : "reasoning", healing }]);

      const prompt = healing ? `Previous error: "${lastErr}". Diagnose, correct, retry.` : (iter === 1 ? msg : "continue");

      await new Promise<void>(resolve => {
        let done = false;
        const finish = () => { if (!done) { done = true; resolve(); } };
        setStreaming(true);
        wsRef.current!.send(JSON.stringify({
          message: prompt,
          react: true,
          auto_confirm: autoConfRef.current,
          attachments: iter === 1 && toSend.length ? toSend : undefined,
        }));
        const base = evtHandlerRef.current!;
        const iter_h = (ev: any) => {
          base(ev);
          if (ev.type === "done") { evtHandlerRef.current = base; setStreaming(false); finish(); }
          else if (ev.type === "error") { lastErr = ev.data; evtHandlerRef.current = base; setStreaming(false); finish(); }
          else if (ev.type === "tool_result") { if (!ev.data?.success) lastErr = ev.data?.error || "tool failed"; else lastErr = null; }
        };
        evtHandlerRef.current = iter_h;
        setTimeout(() => { evtHandlerRef.current = base; finish(); }, 120000);
      });

      const last = msgsRef.current[msgsRef.current.length - 1];
      const content = last?.content?.toLowerCase() || "";
      const complete = !lastErr && (content.includes("task_complete") || content.includes("task complete") ||
        content.includes("done") || content.includes("completed") || iter >= MAX_ITER);
      if (complete) go = false;
    }

    setMessages(prev => [...prev, { role: "react_status", iteration: reactIterRef.current,
      phase: reactIterRef.current >= MAX_ITER ? `max iterations (${MAX_ITER}) reached` : "task complete ✓", healing: false }]);
    reactActiveRef.current = false;
    setStreaming(false);
  }, [input, streaming, attachments]);

  const onConfirm = (id: string) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    // FIX: backend confirm via correct message format
    wsRef.current?.send(JSON.stringify({ confirm_id: id, message: "__confirm__" }));
  };
  const onCancel = (id: string) => {
    setPendingConfirms(prev => prev.filter(c => c.confirm_id !== id));
    wsRef.current?.send(JSON.stringify({ type: "cancel_confirm", confirm_id: id, message: "" }));
  };

  // FIX: reset uses correct endpoint DELETE /api/session/{user_id}
  const handleReset = async () => {
    reactActiveRef.current = false;
    if (authedUser) await api(`${API_URL}/session/${authedUser}`, { method: "DELETE" }).catch(() => {});
    setMessages([]); setPendingConfirms([]); setAttachments([]);
  };

  const canSend     = !streaming && (!!input.trim() || attachments.length > 0);
  const canContinue = !streaming && connected;
  const pColor      = PROVIDERS[provInfo.provider]?.color || J.accent;

  if (!authedUser) return <Login onAuth={u => setAuthedUser(u)} />;

  return (
    <div style={{ minHeight: "100vh", background: J.bg, display: "flex", flexDirection: "column" }}
      onDrop={onDrop} onDragOver={onDragOver}>
      <style>{GLOBAL_CSS}</style>

      {/* Ambient scan line */}
      <div style={{ position: "fixed", left: 0, right: 0, height: 1, background: `linear-gradient(90deg,transparent,${J.accent}22,transparent)`, animation: "hud-scan 10s linear infinite", pointerEvents: "none", zIndex: 9999 }} />
      {/* Grid background */}
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none",
        backgroundImage: `linear-gradient(${J.bgDeep}88 1px, transparent 1px), linear-gradient(90deg, ${J.bgDeep}88 1px, transparent 1px)`,
        backgroundSize: "60px 60px", opacity: 0.5 }} />

      {/* ── HUD Header ── */}
      <div style={{
        padding: "8px 20px", borderBottom: `1px solid ${J.border}`,
        background: `${J.bgPanel}E0`,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        position: "sticky", top: 0, zIndex: 10,
        backdropFilter: "blur(10px)",
      }}>
        {/* Left cluster */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ArcReactor size={26} />
            <div>
              <div style={{ color: J.accent, fontSize: 12, letterSpacing: "0.2em", fontFamily: "'Rajdhani', monospace", fontWeight: 700, lineHeight: 1.2 }}>J.A.R.V.I.S.</div>
              <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.15em", fontFamily: "'Rajdhani', monospace" }}>MK II · NEURAL NODE LABS</div>
            </div>
          </div>
          <Divider color={J.borderMid} />

          <Chip label={connected ? "● ONLINE" : "○ OFFLINE"} color={connected ? J.ok : J.err} pulse={connected} />
          <ModelPicker info={provInfo} onChange={d => setProvInfo(prev => ({ ...prev, ...d }))} />
          <Chip label={provInfo.schema_format || "openai"} color={pColor} />
        </div>

        {/* Right cluster */}
        <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
          {/* Skill badges */}
          {skills.map((s: any) => {
            const sm = SKILL_META[s.name] || { border: J.borderMid, icon: "◈" };
            return (
              <span key={s.name} className="hud-badge" title={s.description} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px", borderRadius: 2,
                border: `1px solid ${sm.border}44`, color: sm.border, fontSize: 9,
              }}>
                <span style={{ flexShrink: 0 }}>{sm.icon}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
              </span>
            );
          })}
          {[
            { label: "⬡ MEMORY", action: () => setMemoryOpen(true), color: J.gold },
            { label: "⚙ CONFIG", action: () => setSettingsOpen(true), color: J.textSec },
            { label: "↺ RESET",  action: handleReset,                  color: J.err },
          ].map(b => (
            <button key={b.label} onClick={b.action} style={{
              padding: "4px 10px", background: "transparent",
              border: `1px solid ${J.border}`, color: J.textSec,
              borderRadius: 2, fontSize: 10, letterSpacing: "0.06em",
              transition: "border-color 0.2s, color 0.2s",
              fontFamily: "'Rajdhani', monospace", fontWeight: 600,
            }}
              onMouseEnter={e => { (e.target as any).style.borderColor = b.color; (e.target as any).style.color = b.color; }}
              onMouseLeave={e => { (e.target as any).style.borderColor = J.border; (e.target as any).style.color = J.textSec; }}
            >{b.label}</button>
          ))}
          <button onClick={() => { clearAuth(); setAuthedUser(null); }}
            title={`Session: ${authedUser}`}
            style={{ padding: "4px 10px", background: "transparent", border: `1px solid ${J.border}`, color: J.textSec, borderRadius: 2, fontSize: 10, letterSpacing: "0.06em", fontFamily: "'Rajdhani', monospace", fontWeight: 600 }}>
            ⇤ {authedUser?.toUpperCase()}
          </button>
        </div>
      </div>

      {/* ── Message feed ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px", maxWidth: 940, width: "100%", margin: "0 auto" }}>

        {/* Empty state */}
        {messages.length === 0 && (
          <div style={{ textAlign: "center", paddingTop: 80 }}>
            <div style={{ position: "relative", width: 80, height: 80, margin: "0 auto 28px" }}>
              <div className="arc-ring" style={{ position: "absolute", inset: 0, borderRadius: "50%", border: `1px solid ${J.accent}25`, borderTop: `1px solid ${J.accent}66` }} />
              <div className="arc-ring-slow" style={{ position: "absolute", inset: 6, borderRadius: "50%", border: `1px solid ${J.accent}15`, borderRight: `1px solid ${J.accent}44` }} />
              <div style={{
                position: "absolute", inset: 0, borderRadius: "50%",
                background: `radial-gradient(circle, ${J.accent}18 0%, ${J.bgCard} 65%)`,
                border: `1px solid ${J.accent}55`,
                boxShadow: `0 0 32px ${J.accentGlow2}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 30, color: J.accent,
              }}>◈</div>
            </div>
            <div style={{ color: `${J.accent}77`, fontSize: 12, letterSpacing: "0.2em", marginBottom: 8, fontFamily: "'Rajdhani', monospace", fontWeight: 600 }}>SYSTEM ONLINE — AWAITING DIRECTIVE</div>
            <div style={{ color: J.textDim, fontSize: 10, lineHeight: 2.2, letterSpacing: "0.08em", marginBottom: 28 }}>
              {provInfo.provider.toUpperCase()} / {provInfo.model} &nbsp;·&nbsp; {provInfo.schema_format?.toUpperCase()} SCHEMA &nbsp;·&nbsp; {authedUser?.toUpperCase()} AUTHENTICATED
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
              {["List files in home directory", "Show running processes", "Build a REST API using CBD methodology", "Run nmap on local network", "Retrieve last 10 conversations"].map(s => (
                <button key={s} onClick={() => setInput(s)} style={{
                  padding: "6px 14px", background: J.bgCard,
                  border: `1px solid ${J.border}`, color: J.textSec,
                  borderRadius: 3, fontSize: 11, transition: "all 0.2s",
                }}
                  onMouseEnter={e => { const t = e.target as any; t.style.borderColor = J.borderMid; t.style.color = J.textPri; }}
                  onMouseLeave={e => { const t = e.target as any; t.style.borderColor = J.border; t.style.color = J.textSec; }}
                >{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m: any, i: number) => <Bubble key={i} msg={m} />)}

        {pendingConfirms.map((c: any) => <ConfirmDlg key={c.confirm_id} c={c} onConfirm={onConfirm} onCancel={onCancel} />)}

        {streaming && (
          <div style={{ color: J.accentDim, fontSize: 10, padding: "4px 42px", animation: "hud-pulse 1.2s infinite", letterSpacing: "0.12em", fontFamily: "'Rajdhani', monospace" }}>
            {reactMode && reactActiveRef.current
              ? `↺ REACT [${String(reactIterRef.current).padStart(2, "0")}] — PROCESSING…`
              : "◈  PROCESSING…"}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Input dock ── */}
      <div style={{
        borderTop: `1px solid ${J.border}`,
        background: `${J.bgPanel}E8`,
        backdropFilter: "blur(10px)",
        maxWidth: 940, width: "100%", margin: "0 auto",
        position: "sticky", bottom: 0,
      }}>
        {/* Staged attachments */}
        {attachments.length > 0 && (
          <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", borderBottom: `1px solid ${J.border}` }}>
            <Lbl>◈ Staged:</Lbl>
            {attachments.map((att: any, i: number) => (
              <AttBadge key={i} att={att} onRemove={() => setAttachments(prev => prev.filter((_: any, j: number) => j !== i))} />
            ))}
          </div>
        )}

        {/* Mode toggles */}
        <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <Toggle on={autoConfirm} set={setAutoConfirm} color={J.warm} label="⚡ Auto-confirm"
            title="Automatically approve destructive actions without prompting. Use with caution." />
          <Toggle on={reactMode} set={setReactMode} color={J.react} label="↺ ReAct"
            title="Enable autonomous ReAct loop. AI will reason, act, observe and self-correct until task is done." />
          {reactMode && <span style={{ color: `${J.react}55`, fontSize: 9, letterSpacing: "0.06em", fontFamily: "'Rajdhani', monospace" }}>AI runs autonomously to completion · max {MAX_ITER} iterations</span>}
          {autoConfirm && <span style={{ color: `${J.warm}55`, fontSize: 9, letterSpacing: "0.06em", fontFamily: "'Rajdhani', monospace" }}>⚡ destructive actions execute without confirmation</span>}
          {uploading && <span style={{ color: `${J.accent}55`, fontSize: 9, animation: "hud-pulse 1s infinite" }}>⬆ reading files…</span>}
        </div>

        {/* Input row */}
        <div style={{ padding: "8px 16px 12px" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>

            {/* Attach button */}
            <button onClick={() => fileRef.current?.click()} disabled={uploading} title="Attach file (text, PDF, image)"
              style={{
                padding: "9px 11px", borderRadius: 4, fontSize: 14,
                background: attachments.length ? J.bgCard : "transparent",
                border: `1px solid ${attachments.length ? J.borderMid : J.border}`,
                color: attachments.length ? J.accent : J.textDim,
                position: "relative",
              }}>
              ◈
              {attachments.length > 0 && (
                <span style={{
                  position: "absolute", top: -4, right: -4,
                  background: J.accent, color: J.bg,
                  borderRadius: "50%", width: 14, height: 14,
                  fontSize: 9, display: "flex", alignItems: "center", justifyContent: "center",
                  fontWeight: "bold",
                }}>{attachments.length}</span>
              )}
            </button>
            <input ref={fileRef} type="file" multiple
              accept=".txt,.md,.py,.js,.ts,.json,.csv,.yaml,.yml,.html,.xml,.pdf,image/*"
              style={{ display: "none" }}
              onChange={e => { handleFiles(e.target.files); e.target.value = ""; }} />

            {/* Textarea */}
            <div style={{
              flex: 1, border: `1px solid ${reactMode ? `${J.react}44` : J.border}`,
              borderRadius: 4, background: J.bgCard, overflow: "hidden",
              boxShadow: streaming ? `0 0 12px ${reactMode ? J.react : J.accent}18` : "none",
              transition: "box-shadow 0.3s, border-color 0.3s",
              animation: reactMode && reactActiveRef.current ? "hud-react 2s infinite" : "none",
            }}>
              <textarea value={input} onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    reactMode ? startClientReact() : send();
                  }
                }}
                onPaste={e => {
                  const items = Array.from(e.clipboardData?.items || []);
                  const files = items.filter(i => i.kind === "file").map(i => i.getAsFile()!).filter(Boolean);
                  if (files.length) { e.preventDefault(); handleFiles(files); }
                }}
                placeholder={
                  reactMode ? "Describe task for autonomous ReAct agent… runs to completion without manual Continue clicks"
                  : attachments.length ? "Add context about the attached file(s)… (optional)"
                  : "Query J.A.R.V.I.S. … (Shift+Enter for newline · drag & drop files to attach)"
                }
                rows={1}
                style={{
                  width: "100%", padding: "10px 14px", background: "transparent",
                  border: "none", color: J.textPri, fontFamily: "inherit", fontSize: 13,
                  resize: "none", lineHeight: 1.5, minHeight: 42,
                }} />
            </div>

            {/* Continue button — shown in non-react mode only */}
            {!reactMode && (
              <button onClick={sendContinue} disabled={!canContinue} title="Continue — nudge the agent"
                style={{
                  padding: "9px 11px", borderRadius: 4, fontSize: 11,
                  background: canContinue ? J.bgCard : "transparent",
                  border: `1px solid ${canContinue ? J.borderMid : J.border}`,
                  color: canContinue ? J.accent : J.textDim,
                  fontFamily: "'Rajdhani', monospace", fontWeight: 600,
                  letterSpacing: "0.06em",
                }}>▷▷</button>
            )}

            {/* Send / React button */}
            <button onClick={() => reactMode ? startClientReact() : send()} disabled={!canSend}
              title={reactMode ? "Launch ReAct loop — AI runs to task completion" : "Send"}
              style={{
                padding: "9px 20px", borderRadius: 4, fontSize: 15,
                background: canSend ? (reactMode ? `${J.react}12` : `${J.accent}0C`) : "transparent",
                border: `1px solid ${canSend ? (reactMode ? J.react : (connected ? J.accent : J.warn)) : J.border}`,
                color: canSend ? (reactMode ? J.react : (connected ? J.accent : J.warn)) : J.textDim,
                transition: "all 0.2s",
                boxShadow: canSend && !streaming ? `0 0 8px ${reactMode ? J.react : J.accent}22` : "none",
              }}>
              {reactMode ? "↺" : "▶"}
            </button>
          </div>

          <div style={{ color: J.textDim, fontSize: 9, marginTop: 5, display: "flex", gap: 14, flexWrap: "wrap", letterSpacing: "0.06em", fontFamily: "'Rajdhani', monospace" }}>
            <span>Enter — send · Shift+Enter — newline · drag & drop to attach</span>
            {reactMode && <span style={{ color: `${J.react}55` }}>↺ REACT MODE — AI continues autonomously until TASK_COMPLETE</span>}
            {autoConfirm && <span style={{ color: `${J.warm}55` }}>⚡ AUTO-CONFIRM ACTIVE</span>}
          </div>
        </div>
      </div>

      {/* ── Overlays ── */}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} onSaved={d => setProvInfo(prev => ({ ...prev, ...d }))} />}
      {memoryOpen && <MemoryPanel onClose={() => setMemoryOpen(false)} userId={authedUser || "default"} />}
    </div>
  );
}