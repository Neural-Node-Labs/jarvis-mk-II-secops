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
async function api(url: string, opts: RequestInit = {}): Promise<Response> {
  const auth = getAuth();
  const headers: Record<string, string> = { ...(opts.headers as any || {}) };
  if (auth?.token) headers["Authorization"] = `Bearer ${auth.token}`;
  const res = await fetch(url, { ...opts, headers });
  if (res.status === 401) {
    // Token invalid or expired — clear session and force re-login
    clearAuth();
    window.dispatchEvent(new CustomEvent("jarvis:signout", { detail: "token_expired" }));
  }
  return res;
}

// ─── Design Tokens — Iron Man HUD ─────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════
// DYNAMIC PERSONA / THEME SYSTEM
// Themes are loaded from the backend (GET /api/personas).
// J is mutated in-place by applyTheme() so every inline style picks up new values.
// ═══════════════════════════════════════════════════════════════════════════════
interface Theme {
  bg: string; bgDeep: string; bgPanel: string; bgCard: string; bgCardHover: string;
  accent: string; accentDim: string; accentGlow: string; accentGlow2: string;
  warm: string; warmDim: string; gold: string; goldDim: string;
  textPri: string; textSec: string; textDim: string;
  border: string; borderMid: string; borderHi: string;
  ok: string; okDim: string; err: string; errDim: string;
  warn: string; warnDim: string; react: string; reactDim: string;
  fontImport: string; fontMono: string; fontHeader: string;
  glyph: string; wordmark: string; subtitle: string; tagline: string; scanline: string;
}

// Default jarvis theme — used before API personas load
const THEME_JARVIS: Theme = {
  bg:"#030609",bgDeep:"#010305",bgPanel:"#060C14",bgCard:"#08101A",bgCardHover:"#0C1520",
  accent:"#00C8FF",accentDim:"#006A88",accentGlow:"#00C8FF18",accentGlow2:"#00C8FF40",
  warm:"#FF6B35",warmDim:"#3A1A0A",gold:"#FFB830",goldDim:"#3A2A00",
  textPri:"#B8D8F0",textSec:"#3A6A8A",textDim:"#1A3A50",
  border:"#0C1E2E",borderMid:"#1A3A55",borderHi:"#00C8FF44",
  ok:"#00FF88",okDim:"#003322",err:"#FF4455",errDim:"#2A0008",
  warn:"#FFB830",warnDim:"#2A1E00",react:"#7B68EE",reactDim:"#1A1640",
  fontImport:"@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap');",
  fontMono:"'Share Tech Mono', monospace",fontHeader:"'Rajdhani', monospace",
  glyph:"◈",wordmark:"J.A.R.V.I.S.",
  subtitle:"JUST A RATHER VERY INTELLIGENT SYSTEM · MK II",
  tagline:"STARK INDUSTRIES PROPRIETARY · SECURE ACCESS REQUIRED",
  scanline:"#00C8FF22",
};

// J — mutable palette, mutated by applyTheme()
const J: Theme = (() => {
  let saved = "jarvis";
  try { saved = localStorage.getItem("jarvis_persona") || "jarvis"; } catch {}
  return { ...THEME_JARVIS }; // always start with jarvis; theme applied after API load
})();

// _personaThemeCache: populated from API on login/persona-list fetch
const _personaThemeCache: Record<string, Partial<Theme>> = { jarvis: THEME_JARVIS };

function applyTheme(id: string, themeOverride?: Partial<Theme>) {
  const t = themeOverride || _personaThemeCache[id] || THEME_JARVIS;
  Object.assign(J, THEME_JARVIS, t); // always reset to jarvis defaults first, then overlay
}

function cacheAndApplyPersona(personaList: any[]) {
  for (const p of personaList) {
    if (p.theme && typeof p.theme === "object") {
      _personaThemeCache[p.id] = p.theme as Partial<Theme>;
    }
  }
}

// PersonaData — typed persona record from API
interface PersonaData {
  id: string; name: string; tagline: string;
  soul?: string; directives?: string; skills?: string[];
  theme: Partial<Theme>; builtin: boolean; icon?: string;
}

// _personaRegistry: populated from GET /api/personas at login/boot.
// Components read from this — never from the stale PERSONA_META const.
let _personaRegistry: PersonaData[] = [
  { id: "jarvis", name: "J.A.R.V.I.S.", tagline: "Iron Man's AI — Security, pentest, and programming.",
    theme: THEME_JARVIS, builtin: true, icon: "◈" },
];

function getPersonaMeta(id: string): PersonaData {
  return _personaRegistry.find(p => p.id === id) || _personaRegistry[0];
}

function refreshPersonaRegistry(list: any[]) {
  _personaRegistry = list.map(p => ({
    ...p,
    icon: p.theme?.glyph || p.icon || "◈",
  }));
  cacheAndApplyPersona(list);
}

const PERSONA_KEY = "jarvis_persona";
function loadPersona(): string {
  try { return localStorage.getItem(PERSONA_KEY) || "jarvis"; } catch { return "jarvis"; }
}
function savePersona(id: string) {
  try { localStorage.setItem(PERSONA_KEY, id); } catch {}
}

// Client-side SHA-256 — pre-hash password before sending to API.
// Server stores PBKDF2(sha256(password)) so raw password is never on the wire.
async function sha256hex(plain: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(plain));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}

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
// Built as a function so it re-reads J (mutated by applyTheme) on every render.
// Persona-specific extras: OMNIKON gets a glitch flicker, KRAKEN gets an ember flicker.
function buildGlobalCSS(personaId: string): string {
  const extra =
    personaId === "omnikon"
      ? `
  @keyframes omni-glitch { 0%,100%{ clip-path: inset(0 0 0 0); transform: translate(0,0); } 20%{ clip-path: inset(10% 0 60% 0); transform: translate(-2px,0); } 40%{ clip-path: inset(50% 0 20% 0); transform: translate(2px,0); } 60%{ clip-path: inset(80% 0 2% 0); transform: translate(-1px,0); } 80%{ clip-path: inset(30% 0 40% 0); transform: translate(1px,0); } }
  .persona-glitch { position: relative; }
  .persona-glitch::before, .persona-glitch::after { content: attr(data-text); position: absolute; inset: 0; }
  .persona-glitch::before { color: ${J.react}; animation: omni-glitch 2.6s infinite linear alternate-reverse; left: 2px; }
  .persona-glitch::after  { color: ${J.warm};  animation: omni-glitch 3.1s infinite linear alternate-reverse; left: -2px; }
`
      : personaId === "kraken"
      ? `
  @keyframes ember-flicker { 0%,100%{ opacity: 1; filter: drop-shadow(0 0 6px ${J.accentGlow2}); } 45%{ opacity: 0.85; } 50%{ opacity: 0.6; filter: drop-shadow(0 0 14px ${J.accent}); } 55%{ opacity: 0.9; } }
  .persona-glitch { animation: ember-flicker 3.2s infinite ease-in-out; }
`
      : "";

  return `
  ${J.fontImport}
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: ${J.bg}; color: ${J.textPri}; font-family: ${J.fontMono}; }
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
${extra}
`;
}

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
const Lbl = ({ c = J.textSec, children }: { c?: string; children: any }) => (
  <span style={{ color: c, fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase", fontFamily: J.fontHeader, fontWeight: 600 }}>
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
  }}>{J.glyph}</div>
);

// HUD Status chip
const Chip = ({ label, color = J.textSec, pulse = false, bg }: { label: string; color?: string; pulse?: boolean; bg?: string }) => (
  <div style={{
    padding: "2px 8px", borderRadius: 2, fontSize: 9,
    background: bg || `${color}0A`, border: `1px solid ${color}33`,
    color, letterSpacing: "0.12em", animation: pulse ? "hud-pulse 2.5s infinite" : "none",
    fontFamily: J.fontHeader, fontWeight: 600,
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
    transition: "all 0.15s", fontFamily: J.fontHeader, fontWeight: 600,
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
const SettingsPanel = ({ onClose, onSaved, authedUser }: {
  onClose: () => void;
  onSaved: (d: any) => void;
  authedUser: string | null;
}) => {
  const [tab,     setTab]     = useState<"llm"|"users">("llm");
  // ── LLM tab ──
  const [prov,    setProv]    = useState("deepseek");
  const [model,   setModel]   = useState("deepseek-coder");
  const [key,     setKey]     = useState("");
  const [url,     setUrl]     = useState("");
  const [temp,    setTemp]    = useState(0.7);
  const [maxTok,  setMaxTok]  = useState(8192);
  const [showKey, setShowKey] = useState(false);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);
  const [llmErr,  setLlmErr]  = useState("");
  const P = PROVIDERS[prov] || PROVIDERS.deepseek;

  const saveLLM = async () => {
    setSaving(true); setLlmErr(""); setSaved(false);
    try {
      onSaved({ provider: prov, model, temperature: temp, max_tokens: maxTok, api_key: key || undefined, base_url: url || undefined });
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (e: any) { setLlmErr(`Error: ${e.message}`); }
    setSaving(false);
  };

  // ── Users tab ──
  const [users,     setUsers]     = useState<any[]>([]);
  const [usersLoad, setUsersLoad] = useState(false);
  const [usersErr,  setUsersErr]  = useState("");
  const [selected,  setSelected]  = useState<any>(null);
  const [editPass,  setEditPass]  = useState("");
  const [editPass2, setEditPass2] = useState("");
  const [editActive,setEditActive]= useState(true);
  const [editSaving,setEditSaving]= useState(false);
  const [editSaved, setEditSaved] = useState(false);
  const [newUser,   setNewUser]   = useState("");
  const [newPass,   setNewPass]   = useState("");
  const [newPass2,  setNewPass2]  = useState("");
  const [creating,  setCreating]  = useState(false);
  const [deleting,  setDeleting]  = useState<string|null>(null);

  const isAdmin = authedUser === "admin";

  const loadUsers = async () => {
    setUsersLoad(true); setUsersErr("");
    try {
      const r = await api(`${API_URL}/auth/users`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setUsers(d.users || []);
    } catch (e: any) { setUsersErr(e.message); }
    setUsersLoad(false);
  };

  const selectUser = (u: any) => {
    setSelected(u); setEditPass(""); setEditPass2("");
    setEditActive(u.is_active === 1 || u.is_active === true);
    setEditSaved(false);
  };

  const saveUser = async () => {
    if (!selected) return;
    if (editPass && editPass !== editPass2) { setUsersErr("Passwords do not match"); return; }
    if (editPass && editPass.length < 6)    { setUsersErr("Password must be ≥ 6 chars"); return; }
    setEditSaving(true); setUsersErr(""); setEditSaved(false);
    try {
      const body: any = { is_active: editActive };
      if (editPass) body.password = await sha256hex(editPass);
      const r = await api(`${API_URL}/auth/users/${selected.username}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || `HTTP ${r.status}`); }
      setEditSaved(true); setEditPass(""); setEditPass2("");
      setTimeout(() => setEditSaved(false), 2000);
      loadUsers();
    } catch (e: any) { setUsersErr(e.message); }
    setEditSaving(false);
  };

  const createUser = async () => {
    const u = newUser.trim().toLowerCase();
    if (!u || !newPass) { setUsersErr("Username and password required"); return; }
    if (newPass !== newPass2) { setUsersErr("Passwords do not match"); return; }
    if (newPass.length < 6)  { setUsersErr("Password must be ≥ 6 chars"); return; }
    setCreating(true); setUsersErr("");
    try {
      const r = await api(`${API_URL}/auth/register`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: await sha256hex(newPass) }),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || `HTTP ${r.status}`); }
      setNewUser(""); setNewPass(""); setNewPass2("");
      loadUsers();
    } catch (e: any) { setUsersErr(e.message); }
    setCreating(false);
  };

  const deleteUser = async (username: string) => {
    if (!window.confirm(`Delete user "${username}"? This cannot be undone.`)) return;
    setDeleting(username);
    try {
      const r = await api(`${API_URL}/auth/users/${username}`, { method: "DELETE" });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
      if (selected?.username === username) setSelected(null);
      loadUsers();
    } catch (e: any) { setUsersErr(e.message); }
    setDeleting(null);
  };

  // Load users when switching to users tab
  const INP: React.CSSProperties = {
    width: "100%", padding: "7px 10px", borderRadius: 3,
    background: J.bgCard, border: `1px solid ${J.borderMid}`,
    color: J.textPri, fontSize: 12,
  };

  const TAB = (id: typeof tab, label: string) => (
    <button onClick={() => { setTab(id); if (id === "users") loadUsers(); }} style={{
      padding: "6px 20px", fontSize: 11, fontFamily: J.fontHeader, fontWeight: 600,
      background: tab === id ? J.bgCard : "transparent",
      border: `1px solid ${tab === id ? J.accent + "55" : J.border}`,
      borderBottom: tab === id ? `1px solid ${J.bgCard}` : `1px solid ${J.border}`,
      borderRadius: "3px 3px 0 0", color: tab === id ? J.accent : J.textSec,
    }}>{label}</button>
  );

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F2`, zIndex: 100, display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <ArcReactor size={22} glow={false} />
          <Lbl c={J.accent}>System Configuration — {J.wordmark}</Lbl>
        </div>
        <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕ CLOSE</button>
      </div>
      {/* Tabs */}
      <div style={{ padding: "0 24px", background: J.bgPanel, display: "flex", gap: 2, borderBottom: `1px solid ${J.border}`, flexShrink: 0 }}>
        {TAB("llm",   "⚙ LLM CONFIG")}
        {TAB("users", "⬡ USER MANAGEMENT")}
      </div>
      {/* Body */}
      <div style={{ flex: 1, overflowY: "auto" }}>

        {/* ── LLM Tab ── */}
        {tab === "llm" && (
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
              <select value={model} onChange={e => setModel(e.target.value)} style={{ width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 2, background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12 }}>
                {P.models.map(m => <option key={m} value={m}>{m} — max {(mTok(m)/1000).toFixed(0)}K tokens</option>)}
              </select>
            </div>
            {P.needsKey && (
              <div style={{ marginBottom: 14 }}>
                <Lbl>API Key — {P.keyLabel}</Lbl>
                <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                  <input value={key} onChange={e => setKey(e.target.value)} type={showKey ? "text" : "password"} placeholder={`Enter ${P.keyLabel}`} style={{ flex: 1, padding: "7px 10px", borderRadius: 2, background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12 }} />
                  <button onClick={() => setShowKey(v => !v)} style={{ background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "0 12px", borderRadius: 2, fontSize: 10 }}>{showKey ? "HIDE" : "SHOW"}</button>
                </div>
              </div>
            )}
            {P.needsUrl && (
              <div style={{ marginBottom: 14 }}>
                <Lbl>Base URL</Lbl>
                <input value={url} onChange={e => setUrl(e.target.value)} placeholder="http://localhost:11434" style={{ ...INP, marginTop: 6 }} />
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 22 }}>
              <div>
                <Lbl>Temperature — {temp.toFixed(2)}</Lbl>
                <input type="range" min="0" max="2" step="0.05" value={temp} onChange={e => setTemp(parseFloat(e.target.value))} style={{ width: "100%", marginTop: 8, accentColor: P.color }} />
              </div>
              <div>
                <Lbl>Max Output Tokens</Lbl>
                <input type="number" value={maxTok} onChange={e => setMaxTok(Math.min(parseInt(e.target.value)||256, mTok(model)))} min="256" max={mTok(model)} style={{ ...INP, marginTop: 6 }} />
              </div>
            </div>
            {llmErr && <div style={{ color: J.err, fontSize: 11, marginBottom: 12, padding: "6px 10px", background: J.errDim, border: `1px solid ${J.err}33`, borderRadius: 2 }}>{llmErr}</div>}
            <button onClick={saveLLM} disabled={saving} style={{ padding: "8px 28px", borderRadius: 2, background: saved ? J.okDim : `${J.accent}0C`, border: `1px solid ${saved ? J.ok : J.accent}`, color: saved ? J.ok : J.accent, fontSize: 12, letterSpacing: "0.1em" }}>
              {saving ? "SAVING…" : saved ? "✓ CONFIGURATION SAVED" : "APPLY CONFIGURATION"}
            </button>
          </div>
        )}

        {/* ── Users Tab ── */}
        {tab === "users" && (
          <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", height: "100%", overflow: "hidden" }}>
            {/* User list */}
            <div style={{ borderRight: `1px solid ${J.border}`, overflowY: "auto", display: "flex", flexDirection: "column" }}>
              {/* Create new user (admin only) */}
              {isAdmin && (
                <div style={{ padding: "12px 14px", borderBottom: `1px solid ${J.border}`, background: J.bgDeep }}>
                  <Lbl c={J.ok}>⊞ NEW USER</Lbl>
                  <input value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="Username" style={{ ...INP, marginTop: 6, marginBottom: 5 }} />
                  <input type="password" value={newPass} onChange={e => setNewPass(e.target.value)} placeholder="Password (min 6)" style={{ ...INP, marginBottom: 5 }} />
                  <input type="password" value={newPass2} onChange={e => setNewPass2(e.target.value)} placeholder="Confirm password" onKeyDown={e => e.key === "Enter" && createUser()} style={{ ...INP, marginBottom: 8 }} />
                  <button onClick={createUser} disabled={creating || !newUser.trim() || !newPass} style={{ width: "100%", padding: "6px", background: `${J.ok}0C`, border: `1px solid ${J.ok}55`, color: J.ok, borderRadius: 3, fontSize: 10, letterSpacing: "0.08em" }}>
                    {creating ? "CREATING…" : "⊞ CREATE USER"}
                  </button>
                </div>
              )}
              {/* Users list */}
              <div style={{ flex: 1, overflowY: "auto" }}>
                {usersLoad && <div style={{ padding: 16, color: J.textSec, fontSize: 11, animation: "hud-pulse 1.5s infinite" }}>Loading users…</div>}
                {users.map(u => (
                  <div key={u.username} onClick={() => selectUser(u)} style={{
                    padding: "10px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: 8,
                    background: selected?.username === u.username ? `${J.accent}0A` : "transparent",
                    borderLeft: `3px solid ${selected?.username === u.username ? J.accent : (u.is_active ? J.ok : J.err)}`,
                    borderBottom: `1px solid ${J.border}`,
                    transition: "all 0.1s",
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ color: selected?.username === u.username ? J.textPri : J.textSec, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {u.username}
                        {u.username === "admin" && <span style={{ color: J.gold, fontSize: 9, marginLeft: 6 }}>ADMIN</span>}
                        {u.username === authedUser && <span style={{ color: J.accentDim, fontSize: 9, marginLeft: 6 }}>YOU</span>}
                      </div>
                      <div style={{ color: u.is_active ? J.ok : J.err, fontSize: 9, marginTop: 2 }}>
                        {u.is_active ? "● ACTIVE" : "○ INACTIVE"}
                        <span style={{ color: J.textDim, marginLeft: 6 }}>{u.created_at?.slice(0,10)}</span>
                      </div>
                    </div>
                    {isAdmin && u.username !== "admin" && u.username !== authedUser && (
                      <button onClick={e => { e.stopPropagation(); deleteUser(u.username); }} disabled={deleting === u.username} title={`Delete ${u.username}`}
                        style={{ background: "none", border: "none", color: J.err, fontSize: 14, cursor: "pointer", opacity: 0.6, padding: 0, flexShrink: 0 }}>
                        {deleting === u.username ? "…" : "⊗"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Edit panel */}
            <div style={{ overflowY: "auto", padding: "24px 28px" }}>
              {usersErr && <div style={{ color: J.err, fontSize: 11, marginBottom: 16, padding: "8px 12px", background: J.errDim, border: `1px solid ${J.err}33`, borderRadius: 3 }}>⚠ {usersErr}</div>}
              {!selected ? (
                <div style={{ color: J.textDim, textAlign: "center", paddingTop: 60, fontSize: 11 }}>
                  <div style={{ fontSize: 24, marginBottom: 12, opacity: 0.3 }}>⬡</div>
                  Select a user to edit their account.
                </div>
              ) : (
                <div>
                  <div style={{ color: J.accent, fontSize: 13, fontFamily: J.fontHeader, fontWeight: 700, marginBottom: 4 }}>
                    {selected.username}
                    {selected.username === "admin" && <span style={{ color: J.gold, fontSize: 10, marginLeft: 8 }}>ADMINISTRATOR</span>}
                  </div>
                  <div style={{ color: J.textDim, fontSize: 10, marginBottom: 20 }}>Created: {selected.created_at?.slice(0,16)}</div>

                  {/* Change password */}
                  <div style={{ marginBottom: 14 }}>
                    <Lbl>New Password <span style={{ color: J.textDim }}>(leave blank to keep current)</span></Lbl>
                    <input type="password" value={editPass} onChange={e => setEditPass(e.target.value)} placeholder="Enter new password…" style={{ ...INP, marginTop: 6 }} />
                  </div>
                  <div style={{ marginBottom: 20 }}>
                    <Lbl>Confirm New Password</Lbl>
                    <input type="password" value={editPass2} onChange={e => setEditPass2(e.target.value)} placeholder="Confirm…" style={{ ...INP, marginTop: 6 }} />
                  </div>

                  {/* Active toggle (admin only, not self) */}
                  {isAdmin && selected.username !== "admin" && (
                    <div style={{ marginBottom: 20, display: "flex", alignItems: "center", gap: 12 }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                        <input type="checkbox" checked={editActive} onChange={e => setEditActive(e.target.checked)} style={{ accentColor: J.ok, width: 14, height: 14 }} />
                        <span style={{ color: editActive ? J.ok : J.err, fontSize: 11 }}>{editActive ? "● Account Active" : "○ Account Inactive (user cannot log in)"}</span>
                      </label>
                    </div>
                  )}

                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={saveUser} disabled={editSaving} style={{
                      padding: "8px 24px", background: editSaved ? J.okDim : `${J.accent}0C`,
                      border: `1px solid ${editSaved ? J.ok : J.accent}`,
                      color: editSaved ? J.ok : J.accent, borderRadius: 3, fontSize: 11, letterSpacing: "0.08em",
                    }}>{editSaving ? "SAVING…" : editSaved ? "✓ SAVED" : "✓ SAVE CHANGES"}</button>
                    <button onClick={() => setSelected(null)} style={{ padding: "8px 14px", background: J.bgCard, border: `1px solid ${J.border}`, color: J.textSec, borderRadius: 3, fontSize: 11 }}>CANCEL</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MEMORY SYSTEM PANEL — Episodic + Semantic + Procedural stores
// Backend: GET /api/memory/{user_id}?n=N  DELETE /api/memory/{user_id}
// Blueprint: memory-blueprint.md
// ═══════════════════════════════════════════════════════════════════════════════
const MemoryPanel = ({ onClose, userId }: { onClose: () => void; userId: string }) => {
  const [tab,      setTab]      = useState<"episodic"|"search"|"stats">("episodic");
  const [history,  setHistory]  = useState("");
  const [loading,  setLoading]  = useState(true);
  const [n,        setN]        = useState(20);
  const [clearing, setClearing] = useState(false);
  const [cleared,  setCleared]  = useState(false);
  const [query,    setQuery]    = useState("");
  const [searching,setSearching]= useState(false);
  const [results,  setResults]  = useState<any[]>([]);
  const [stats,    setStats]    = useState<any>(null);
  const [statsLoad,setStatsLoad]= useState(false);
  const [err,      setErr]      = useState("");

  const loadEpisodic = async (count: number) => {
    setLoading(true); setErr("");
    try {
      const r = await api(`${API_URL}/memory/${userId}?n=${count}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setHistory(d.history || "No episodic memory found.");
    } catch (e: any) { setErr(`Episodic retrieval failed: ${e.message}`); setHistory(""); }
    setLoading(false);
  };

  const loadStats = async () => {
    setStatsLoad(true); setErr("");
    try {
      // Use memory_manager skill via chat for stats — fallback to basic info
      const r = await api(`${API_URL}/memory/${userId}?n=1`);
      const d = await r.json();
      setStats({
        user_id: userId,
        has_history: !!(d.history),
        turns_preview: d.n || "—",
        store_types: ["EpisodicStore (conversation turns)", "ProceduralStore (skill sequences)", "SemanticStore (vector knowledge)"],
        blueprint: "memory-blueprint.md v1.0.0",
      });
    } catch (e: any) { setErr(`Stats unavailable: ${e.message}`); }
    setStatsLoad(false);
  };

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true); setErr("");
    try {
      // POST to /api/chat with memory_manager search skill call
      const r = await api(`${API_URL}/memory/${userId}?n=50`);
      const d = await r.json();
      const hist: string = d.history || "";
      // Client-side keyword filter over returned history blocks
      const blocks = hist.split(/\n(?=### \[)/).filter(b => b.toLowerCase().includes(query.toLowerCase()));
      setResults(blocks.length ? blocks : ["No matches found for: " + query]);
    } catch (e: any) { setErr(`Search failed: ${e.message}`); }
    setSearching(false);
  };

  const clear = async () => {
    setClearing(true); setErr("");
    try {
      const r = await api(`${API_URL}/memory/${userId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setCleared(true); setHistory("Memory cleared.");
      setResults([]); setStats(null);
      setTimeout(() => setCleared(false), 3000);
    } catch (e: any) { setErr(`Clear failed: ${e.message}`); }
    setClearing(false);
  };

  useEffect(() => { loadEpisodic(n); loadStats(); }, []);

  const TAB_BTN = (id: typeof tab, label: string, color: string) => (
    <button onClick={() => setTab(id)} style={{
      padding: "5px 16px", borderRadius: "3px 3px 0 0", fontSize: 10,
      background: tab === id ? J.bgCard : "transparent",
      border: `1px solid ${tab === id ? color + "66" : J.border}`,
      borderBottom: tab === id ? `1px solid ${J.bgCard}` : `1px solid ${J.border}`,
      color: tab === id ? color : J.textSec,
      fontFamily: J.fontHeader, fontWeight: 600, letterSpacing: "0.1em",
    }}>{label}</button>
  );

  const overlayStyle: React.CSSProperties = {
    position: "fixed", inset: 0, background: `${J.bgDeep}F4`, zIndex: 100,
    display: "flex", flexDirection: "column", fontFamily: J.fontMono,
  };

  return (
    <div style={overlayStyle}>
      {/* Header */}
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 22, height: 22, borderRadius: "50%", background: J.bgCard, border: `1px solid ${J.gold}55`, display: "flex", alignItems: "center", justifyContent: "center", color: J.gold, fontSize: 11 }}>◉</div>
          <div>
            <div style={{ color: J.gold, fontSize: 11, letterSpacing: "0.18em", fontFamily: J.fontHeader, fontWeight: 700 }}>MEMORY SUBSYSTEM</div>
            <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.12em" }}>EPISODIC · PROCEDURAL · SEMANTIC — {userId.toUpperCase()}</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={clear} disabled={clearing} style={{ padding: "4px 12px", background: J.errDim, border: `1px solid ${J.err}44`, color: cleared ? J.ok : "#FF9999", borderRadius: 2, fontSize: 10, letterSpacing: "0.06em" }}>
            {cleared ? "✓ CLEARED" : clearing ? "CLEARING…" : "⊗ PURGE ALL"}
          </button>
          <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕</button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ padding: "12px 28px 0", background: J.bgPanel, display: "flex", gap: 2, borderBottom: `1px solid ${J.border}` }}>
        {TAB_BTN("episodic", "◉ EPISODIC STORE", J.gold)}
        {TAB_BTN("search",   "⊕ SEMANTIC SEARCH", J.accent)}
        {TAB_BTN("stats",    "⊞ STORE STATUS",    J.ok)}
      </div>

      {err && <div style={{ background: J.errDim, border: `1px solid ${J.err}44`, color: J.err, fontSize: 11, padding: "8px 28px" }}>⚠ {err}</div>}

      {/* Tab bodies */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px", maxWidth: 900, margin: "0 auto", width: "100%" }}>

        {/* ── EPISODIC ── */}
        {tab === "episodic" && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <Lbl c={J.textSec}>Show last</Lbl>
              <select value={n} onChange={e => { setN(+e.target.value); loadEpisodic(+e.target.value); }} style={{ padding: "3px 8px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 11, borderRadius: 2 }}>
                {[5,10,20,50,100,200].map(v => <option key={v} value={v}>{v} turns</option>)}
              </select>
              <button onClick={() => loadEpisodic(n)} style={{ padding: "4px 10px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.accent, borderRadius: 2, fontSize: 10 }}>↺ REFRESH</button>
              <div style={{ marginLeft: "auto", padding: "4px 12px", background: `${J.gold}0C`, border: `1px solid ${J.gold}33`, borderRadius: 2 }}>
                <Lbl c={J.gold}>EpisodicStore — conversation timeline</Lbl>
              </div>
            </div>
            {loading
              ? <div style={{ color: J.textSec, textAlign: "center", padding: 40, animation: "hud-pulse 1.5s infinite" }}>Accessing episodic memory…</div>
              : history
                ? <pre style={{ color: J.textSec, fontSize: 12, lineHeight: 1.9, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{history}</pre>
                : <div style={{ color: J.textDim, textAlign: "center", padding: 40 }}>No episodic memory recorded yet.</div>
            }
          </div>
        )}

        {/* ── SEMANTIC SEARCH ── */}
        {tab === "search" && (
          <div>
            <div style={{ marginBottom: 20, padding: 16, background: J.bgCard, border: `1px solid ${J.borderMid}`, borderLeft: `2px solid ${J.accent}`, borderRadius: 4 }}>
              <Lbl c={J.accent}>SEMANTIC SEARCH — Find relevant memories by keyword or symptom</Lbl>
              <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                <input value={query} onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doSearch()}
                  placeholder="Enter keyword, error message, or symptom…"
                  style={{ flex: 1, padding: "8px 12px", background: J.bgPanel, border: `1px solid ${J.borderMid}`, color: J.textPri, borderRadius: 3, fontSize: 12 }} />
                <button onClick={doSearch} disabled={searching || !query.trim()} style={{ padding: "8px 20px", background: `${J.accent}0C`, border: `1px solid ${J.accent}55`, color: J.accent, borderRadius: 3, fontSize: 11, letterSpacing: "0.08em" }}>
                  {searching ? "SEARCHING…" : "⊕ SEARCH"}
                </button>
              </div>
              <div style={{ color: J.textDim, fontSize: 10, marginTop: 6 }}>Searches across all episodic memory blocks for matching content</div>
            </div>
            {results.length > 0 && (
              <div>
                <div style={{ color: J.textSec, fontSize: 10, marginBottom: 10, letterSpacing: "0.1em" }}>{results.length} RESULT{results.length !== 1 ? "S" : ""} FOR "{query}"</div>
                {results.map((r, i) => (
                  <div key={i} style={{ marginBottom: 8, padding: "10px 14px", background: J.bgCard, border: `1px solid ${J.borderMid}`, borderLeft: `2px solid ${J.accent}55`, borderRadius: 3 }}>
                    <pre style={{ color: J.textSec, fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.7, margin: 0 }}>{r}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── STORE STATUS ── */}
        {tab === "stats" && (
          <div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 20 }}>
              {[
                { label: "EpisodicStore", desc: "Conversation timeline", color: J.gold, icon: "◉", status: "ACTIVE" },
                { label: "SemanticStore", desc: "Vector knowledge retrieval", color: J.accent, icon: "⊕", status: "ACTIVE" },
                { label: "ProceduralStore", desc: "Skill action sequences", color: J.ok, icon: "⬡", status: "ACTIVE" },
              ].map(s => (
                <div key={s.label} style={{ padding: 14, background: J.bgCard, border: `1px solid ${s.color}22`, borderTop: `2px solid ${s.color}55`, borderRadius: 4, position: "relative" }}>
                  <Corners color={s.color} size={6} />
                  <div style={{ color: s.color, fontSize: 18, marginBottom: 6 }}>{s.icon}</div>
                  <div style={{ color: s.color, fontSize: 11, letterSpacing: "0.1em", fontFamily: J.fontHeader, fontWeight: 700 }}>{s.label}</div>
                  <div style={{ color: J.textSec, fontSize: 10, marginTop: 3 }}>{s.desc}</div>
                  <div style={{ marginTop: 8, display: "inline-block", padding: "2px 8px", background: `${J.ok}0C`, border: `1px solid ${J.ok}33`, color: J.ok, fontSize: 9, borderRadius: 2, letterSpacing: "0.1em" }}>{s.status}</div>
                </div>
              ))}
            </div>
            {statsLoad
              ? <div style={{ color: J.textSec, textAlign: "center", padding: 20, animation: "hud-pulse 1.5s infinite" }}>Loading store status…</div>
              : stats && (
                <div style={{ padding: 16, background: J.bgCard, border: `1px solid ${J.borderMid}`, borderRadius: 4 }}>
                  <Lbl c={J.ok}>STORE METADATA</Lbl>
                  <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    {Object.entries(stats).filter(([k]) => !Array.isArray(stats[k])).map(([k, v]: any) => (
                      <div key={k} style={{ fontSize: 11 }}>
                        <span style={{ color: J.textSec }}>{k}: </span>
                        <span style={{ color: J.textPri }}>{String(v)}</span>
                      </div>
                    ))}
                  </div>
                  {stats.store_types && (
                    <div style={{ marginTop: 10 }}>
                      <Lbl>Store Types</Lbl>
                      {stats.store_types.map((t: string) => (
                        <div key={t} style={{ color: J.textSec, fontSize: 11, marginTop: 4 }}>▸ {t}</div>
                      ))}
                    </div>
                  )}
                </div>
              )
            }
            <div style={{ marginTop: 16, padding: "10px 14px", background: `${J.gold}08`, border: `1px solid ${J.gold}22`, borderRadius: 4 }}>
              <Lbl c={J.gold}>Memory Architecture — memory-blueprint.md v1.0.0</Lbl>
              <div style={{ color: J.textSec, fontSize: 11, lineHeight: 1.8, marginTop: 6 }}>
                <div>▸ <span style={{ color: J.gold }}>EpisodicStore</span> — Markdown file per user, append-only turn log, retrieve by count</div>
                <div>▸ <span style={{ color: J.accent }}>SemanticStore</span> — Vector index for unstructured knowledge retrieval (query + k → docs)</div>
                <div>▸ <span style={{ color: J.ok }}>ProceduralStore</span> — SQLite-backed action sequence library (skill_name → steps)</div>
                <div>▸ <span style={{ color: J.react }}>MemoryOrchestrator</span> — Unified dispatch across all stores ({"{type, payload}"} → result)</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// EXPERIENCED PANEL — EXP knowledge base: search, view, capture, promote
// Backend: GET /api/experienced  GET /api/experienced/search  POST /api/experienced/rebuild
//          POST /api/cbd/experienced_capture  POST /api/cbd/experienced_promote
// Blueprint: experienced-blueprint.md
// ═══════════════════════════════════════════════════════════════════════════════
const ExperiencedPanel = ({ onClose, userId }: { onClose: () => void; userId: string }) => {
  const [tab,       setTab]       = useState<"search"|"capture"|"lifecycle">("search");
  const [entries,   setEntries]   = useState<any[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [selected,  setSelected]  = useState<any>(null);
  const [query,     setQuery]     = useState("");
  const [category,  setCategory]  = useState("");
  const [severity,  setSeverity]  = useState("");
  const [searching, setSearching] = useState(false);
  const [rebuilding,setRebuilding]= useState(false);
  const [rebuildOk, setRebuildOk] = useState(false);
  const [err,       setErr]       = useState("");
  const [capForm,   setCapForm]   = useState({
    title: "", category: "environment-setup", severity: "confusing",
    symptom: "", root_cause: "", solution: "", environment_os: "", environment_runtime: "",
  });
  const [capturing, setCapturing] = useState(false);
  const [captured,  setCaptured]  = useState<any>(null);
  const [promoteId, setPromoteId] = useState("");
  const [promoteStatus, setPromoteStatus] = useState("CONFIRMED");
  const [promoting, setPromoting] = useState(false);
  const [promoted,  setPromoted]  = useState<any>(null);

  const CATEGORIES = ["dependency-conflict","environment-setup","configuration","build-tooling","runtime-crash","integration","test-infrastructure","performance-degradation","security-constraint"];
  const SEVERITIES = ["blocking","degrading","confusing"];
  const STATUSES   = ["CONFIRMED","STABLE","SUPERSEDED"];
  const STATUS_COLORS: Record<string,string> = { DRAFT: J.textSec, CONFIRMED: J.ok, STABLE: J.accent, SUPERSEDED: J.err };

  const load = async (q = "", cat = "", sev = "") => {
    setLoading(true); setErr("");
    try {
      const params = new URLSearchParams();
      if (q)   params.set("q", q);
      if (cat) params.set("category", cat);
      if (sev) params.set("severity", sev);
      const url = q || cat || sev ? `${API_URL}/experienced/search?${params}` : `${API_URL}/experienced`;
      const r = await api(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setEntries(d.output?.results || d.results || []);
    } catch (e: any) { setErr(`Failed to load: ${e.message}`); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const search = () => { setSearching(true); load(query, category, severity).finally(() => setSearching(false)); };

  const rebuild = async () => {
    setRebuilding(true); setErr("");
    try {
      const r = await api(`${API_URL}/experienced/rebuild`, { method: "POST" });
      const d = await r.json();
      setRebuildOk(true); setTimeout(() => setRebuildOk(false), 3000);
      load();
    } catch (e: any) { setErr(`Rebuild failed: ${e.message}`); }
    setRebuilding(false);
  };

  const capture = async () => {
    if (!capForm.title || !capForm.symptom) { setErr("Title and symptom are required."); return; }
    setCapturing(true); setErr("");
    try {
      const payload = {
        params: {
          title: capForm.title, category: capForm.category, severity: capForm.severity,
          discovery: { symptom: capForm.symptom, discovery_path: "", misleading_signals: [] },
          root_cause: { statement: capForm.root_cause, explanation: "", trigger_conditions: "" },
          solution: { fix: capForm.solution, why_it_works: "", alternatives_considered: [], side_effects: "None known" },
          environment: { os: capForm.environment_os, runtime_version: capForm.environment_runtime, framework_version: "", tool_versions: [] },
        }
      };
      const r = await api(`${API_URL}/cbd/experienced_capture`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (d.output) { setCaptured(d.output); load(); }
      else throw new Error(d.error || "Unknown error");
    } catch (e: any) { setErr(`Capture failed: ${e.message}`); }
    setCapturing(false);
  };

  const promote = async () => {
    if (!promoteId.trim()) { setErr("EXP ID is required."); return; }
    setPromoting(true); setErr("");
    try {
      const payload = { params: { exp_id: promoteId.trim().toUpperCase(), new_status: promoteStatus } };
      const r = await api(`${API_URL}/cbd/experienced_promote`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (d.output) { setPromoted(d.output); load(); }
      else throw new Error(d.error || "Unknown error");
    } catch (e: any) { setErr(`Promote failed: ${e.message}`); }
    setPromoting(false);
  };

  const TAB_BTN = (id: typeof tab, label: string, color: string) => (
    <button onClick={() => setTab(id)} style={{
      padding: "5px 16px", borderRadius: "3px 3px 0 0", fontSize: 10,
      background: tab === id ? J.bgCard : "transparent",
      border: `1px solid ${tab === id ? color + "66" : J.border}`,
      borderBottom: tab === id ? `1px solid ${J.bgCard}` : `1px solid ${J.border}`,
      color: tab === id ? color : J.textSec,
      fontFamily: J.fontHeader, fontWeight: 600, letterSpacing: "0.1em",
    }}>{label}</button>
  );

  const FLD = (label: string, el: any) => (
    <div style={{ marginBottom: 12 }}>
      <Lbl>{label}</Lbl>
      <div style={{ marginTop: 5 }}>{el}</div>
    </div>
  );

  const INP = (val: string, set: (v: string) => void, ph = "", multi = false): any => multi
    ? <textarea value={val} onChange={e => set(e.target.value)} placeholder={ph} rows={3} style={{ width: "100%", padding: "7px 10px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3, resize: "vertical" }} />
    : <input value={val} onChange={e => set(e.target.value)} placeholder={ph} style={{ width: "100%", padding: "7px 10px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }} />;

  const SEL = (val: string, set: (v: string) => void, opts: string[]) => (
    <select value={val} onChange={e => set(e.target.value)} style={{ width: "100%", padding: "7px 10px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }}>
      {opts.map(o => <option key={o} value={o}>{o}</option>)}
    </select>
  );

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F4`, zIndex: 100, display: "flex", flexDirection: "column", fontFamily: J.fontMono }}>
      {/* Header */}
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 22, height: 22, borderRadius: "50%", background: J.bgCard, border: `1px solid ${J.react}55`, display: "flex", alignItems: "center", justifyContent: "center", color: J.react, fontSize: 11 }}>⬢</div>
          <div>
            <div style={{ color: J.react, fontSize: 11, letterSpacing: "0.18em", fontFamily: J.fontHeader, fontWeight: 700 }}>EXPERIENCED KNOWLEDGE BASE</div>
            <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.12em" }}>DRAFT → CONFIRMED → STABLE → SUPERSEDED · experienced-blueprint.md</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button onClick={rebuild} disabled={rebuilding} style={{ padding: "4px 12px", background: `${J.accent}0C`, border: `1px solid ${J.accent}44`, color: rebuildOk ? J.ok : J.accent, borderRadius: 2, fontSize: 10, letterSpacing: "0.06em" }}>
            {rebuildOk ? "✓ REBUILT" : rebuilding ? "REBUILDING…" : "↺ REBUILD INDEX"}
          </button>
          <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕</button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ padding: "12px 28px 0", background: J.bgPanel, display: "flex", gap: 2, borderBottom: `1px solid ${J.border}` }}>
        {TAB_BTN("search",    "⊕ KNOWLEDGE SEARCH",   J.react)}
        {TAB_BTN("capture",   "⊞ CAPTURE ENTRY",      J.ok)}
        {TAB_BTN("lifecycle", "↺ LIFECYCLE MANAGER",  J.warn)}
      </div>

      {err && <div style={{ background: J.errDim, border: `1px solid ${J.err}44`, color: J.err, fontSize: 11, padding: "8px 28px" }}>⚠ {err}</div>}

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px", maxWidth: 1100, margin: "0 auto", width: "100%", display: tab === "search" && selected ? "grid" : "block", gridTemplateColumns: "1fr 1.4fr", gap: 20 }}>

        {/* ── SEARCH ── */}
        {tab === "search" && (
          <>
            {/* Left: list */}
            <div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
                <input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && search()}
                  placeholder="Search by symptom, keyword, or error…"
                  style={{ flex: 1, minWidth: 160, padding: "7px 10px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 11, borderRadius: 3 }} />
                <select value={category} onChange={e => setCategory(e.target.value)} style={{ padding: "7px 8px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textSec, fontSize: 10, borderRadius: 3 }}>
                  <option value="">All categories</option>
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <select value={severity} onChange={e => setSeverity(e.target.value)} style={{ padding: "7px 8px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textSec, fontSize: 10, borderRadius: 3 }}>
                  <option value="">All severities</option>
                  {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <button onClick={search} disabled={searching} style={{ padding: "7px 14px", background: `${J.react}0C`, border: `1px solid ${J.react}55`, color: J.react, borderRadius: 3, fontSize: 10, letterSpacing: "0.06em" }}>
                  {searching ? "…" : "⊕ SEARCH"}
                </button>
              </div>

              {loading
                ? <div style={{ color: J.textSec, textAlign: "center", padding: 30, animation: "hud-pulse 1.5s infinite" }}>Loading knowledge base…</div>
                : entries.length === 0
                  ? <div style={{ color: J.textDim, textAlign: "center", padding: 30 }}>No entries found. Use Capture Entry to add knowledge.</div>
                  : entries.map((e: any) => (
                    <div key={e.exp_id} onClick={() => setSelected(e === selected ? null : e)}
                      style={{
                        marginBottom: 6, padding: "10px 14px", cursor: "pointer",
                        background: selected?.exp_id === e.exp_id ? `${J.react}0C` : J.bgCard,
                        border: `1px solid ${selected?.exp_id === e.exp_id ? J.react + "55" : J.border}`,
                        borderLeft: `3px solid ${STATUS_COLORS[e.status] || J.textSec}`,
                        borderRadius: 3, transition: "all 0.15s",
                      }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                        <div>
                          <span style={{ color: J.react, fontSize: 10, fontFamily: J.fontHeader, fontWeight: 700 }}>{e.exp_id}</span>
                          <span style={{ color: J.textPri, fontSize: 12, marginLeft: 10 }}>{e.title}</span>
                        </div>
                        <div style={{ display: "flex", gap: 6, flexShrink: 0, marginLeft: 10 }}>
                          <Chip label={e.status || "DRAFT"} color={STATUS_COLORS[e.status] || J.textSec} />
                          <Chip label={e.severity || "—"} color={e.severity === "blocking" ? J.err : e.severity === "degrading" ? J.warn : J.textSec} />
                        </div>
                      </div>
                      <div style={{ color: J.textSec, fontSize: 10, marginTop: 4 }}>
                        <span style={{ color: J.accentDim }}>{e.category}</span>
                        {e.relevance_signal && <span style={{ marginLeft: 10, color: J.textDim }}>match: {e.relevance_signal}</span>}
                      </div>
                    </div>
                  ))
              }
            </div>

            {/* Right: detail */}
            {selected && (
              <div style={{ padding: 16, background: J.bgCard, border: `1px solid ${J.borderMid}`, borderTop: `2px solid ${J.react}55`, borderRadius: 4, position: "sticky" as const, top: 20, maxHeight: "calc(100vh - 180px)", overflowY: "auto" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                  <div>
                    <span style={{ color: J.react, fontSize: 12, fontFamily: J.fontHeader, fontWeight: 700 }}>{selected.exp_id}</span>
                    <Chip label={selected.status} color={STATUS_COLORS[selected.status] || J.textSec} />
                  </div>
                  <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", color: J.textSec, fontSize: 16, cursor: "pointer" }}>×</button>
                </div>
                <div style={{ color: J.textPri, fontSize: 13, fontFamily: J.fontHeader, fontWeight: 600, marginBottom: 8 }}>{selected.title}</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                  <Chip label={selected.category} color={J.accentDim} />
                  <Chip label={selected.severity} color={selected.severity === "blocking" ? J.err : J.warn} />
                </div>
                <div style={{ color: J.textSec, fontSize: 11, lineHeight: 1.7 }}>
                  <div style={{ color: J.textDim, fontSize: 9, marginBottom: 4, letterSpacing: "0.1em" }}>FILE PATH</div>
                  <div style={{ color: J.accent, wordBreak: "break-all", fontSize: 10 }}>{selected.file_path || "—"}</div>
                </div>
                <div style={{ marginTop: 12, padding: "8px 12px", background: J.bgPanel, border: `1px solid ${J.border}`, borderRadius: 3 }}>
                  <Lbl c={J.textSec}>Lifecycle</Lbl>
                  <div style={{ display: "flex", gap: 4, marginTop: 6, alignItems: "center" }}>
                    {["DRAFT","CONFIRMED","STABLE","SUPERSEDED"].map((s, i, arr) => (
                      <div key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <div style={{ padding: "2px 8px", borderRadius: 2, fontSize: 9, background: selected.status === s ? `${STATUS_COLORS[s]}15` : "transparent", border: `1px solid ${selected.status === s ? STATUS_COLORS[s] : J.border}`, color: selected.status === s ? STATUS_COLORS[s] : J.textDim, fontFamily: J.fontHeader, fontWeight: 600 }}>{s}</div>
                        {i < arr.length - 1 && <span style={{ color: J.textDim, fontSize: 10 }}>→</span>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* ── CAPTURE ENTRY ── */}
        {tab === "capture" && (
          <div style={{ maxWidth: 680 }}>
            <div style={{ marginBottom: 16, padding: "10px 14px", background: `${J.ok}08`, border: `1px solid ${J.ok}22`, borderLeft: `3px solid ${J.ok}`, borderRadius: 4 }}>
              <Lbl c={J.ok}>Phase III — Experience Capture (CBD v2.2)</Lbl>
              <div style={{ color: J.textSec, fontSize: 11, marginTop: 4, lineHeight: 1.7 }}>
                New entries start as <span style={{ color: J.textSec }}>DRAFT</span>. Use Lifecycle Manager to promote to CONFIRMED after root cause is verified.
              </div>
            </div>
            {captured && (
              <div style={{ marginBottom: 16, padding: "12px 16px", background: J.okDim, border: `1px solid ${J.ok}44`, borderRadius: 4 }}>
                <div style={{ color: J.ok, fontSize: 11, marginBottom: 6 }}>✓ ENTRY CAPTURED — {captured.exp_id}</div>
                <div style={{ color: J.textSec, fontSize: 11 }}>Status: <span style={{ color: J.textPri }}>{captured.status}</span> · File: <span style={{ color: J.accent, fontSize: 10 }}>{captured.file_path}</span></div>
                <button onClick={() => setCaptured(null)} style={{ marginTop: 8, padding: "3px 10px", background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, borderRadius: 2, fontSize: 10 }}>Capture Another</button>
              </div>
            )}
            {!captured && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={{ gridColumn: "1/-1" }}>{FLD("Title *", INP(capForm.title, v => setCapForm(p => ({...p, title: v})), "Brief description of the issue"))}</div>
                {FLD("Category *", SEL(capForm.category, v => setCapForm(p => ({...p, category: v})), CATEGORIES))}
                {FLD("Severity *", SEL(capForm.severity, v => setCapForm(p => ({...p, severity: v})), SEVERITIES))}
                <div style={{ gridColumn: "1/-1" }}>{FLD("Symptom / Error Message *", INP(capForm.symptom, v => setCapForm(p => ({...p, symptom: v})), "Verbatim error text or exact symptom observed…", true))}</div>
                <div style={{ gridColumn: "1/-1" }}>{FLD("Root Cause Statement", INP(capForm.root_cause, v => setCapForm(p => ({...p, root_cause: v})), "One precise sentence describing why this happened…"))}</div>
                <div style={{ gridColumn: "1/-1" }}>{FLD("Solution / Fix", INP(capForm.solution, v => setCapForm(p => ({...p, solution: v})), "Exact steps or code that resolves the issue…", true))}</div>
                {FLD("OS", INP(capForm.environment_os, v => setCapForm(p => ({...p, environment_os: v})), "e.g. Kali Linux 2024.1"))}
                {FLD("Runtime Version", INP(capForm.environment_runtime, v => setCapForm(p => ({...p, environment_runtime: v})), "e.g. Python 3.12.0"))}
                <div style={{ gridColumn: "1/-1" }}>
                  <button onClick={capture} disabled={capturing || !capForm.title || !capForm.symptom} style={{
                    padding: "9px 28px", background: `${J.ok}0C`, border: `1px solid ${J.ok}`,
                    color: J.ok, borderRadius: 3, fontSize: 12, letterSpacing: "0.12em",
                  }}>{capturing ? "CAPTURING…" : "⊞ CAPTURE ENTRY AS DRAFT"}</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── LIFECYCLE ── */}
        {tab === "lifecycle" && (
          <div style={{ maxWidth: 680 }}>
            <div style={{ marginBottom: 20, padding: "10px 14px", background: `${J.warn}08`, border: `1px solid ${J.warn}22`, borderLeft: `3px solid ${J.warn}`, borderRadius: 4 }}>
              <Lbl c={J.warn}>LifecycleManager — Transition entry status</Lbl>
              <div style={{ color: J.textSec, fontSize: 11, marginTop: 4, lineHeight: 1.8 }}>
                Valid transitions: DRAFT → CONFIRMED (root cause verified) → STABLE (reused successfully) → SUPERSEDED (replaced by newer entry)
              </div>
            </div>
            <div style={{ padding: 20, background: J.bgCard, border: `1px solid ${J.borderMid}`, borderRadius: 4, marginBottom: 16 }}>
              {FLD("EXP ID", <input value={promoteId} onChange={e => setPromoteId(e.target.value)} placeholder="e.g. EXP-0001" style={{ width: "100%", padding: "7px 10px", background: J.bgPanel, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }} />)}
              {FLD("New Status", (
                <div style={{ display: "flex", gap: 6 }}>
                  {STATUSES.map(s => (
                    <button key={s} onClick={() => setPromoteStatus(s)} style={{
                      padding: "5px 14px", borderRadius: 3,
                      background: promoteStatus === s ? `${STATUS_COLORS[s]}15` : J.bgPanel,
                      border: `1px solid ${promoteStatus === s ? STATUS_COLORS[s] : J.border}`,
                      color: promoteStatus === s ? STATUS_COLORS[s] : J.textSec, fontSize: 11,
                    }}>{s}</button>
                  ))}
                </div>
              ))}
              <button onClick={promote} disabled={promoting || !promoteId.trim()} style={{ padding: "8px 24px", background: `${J.warn}0C`, border: `1px solid ${J.warn}55`, color: J.warn, borderRadius: 3, fontSize: 11, letterSpacing: "0.1em", marginTop: 8 }}>
                {promoting ? "TRANSITIONING…" : "↺ APPLY TRANSITION"}
              </button>
            </div>
            {promoted && (
              <div style={{ padding: 14, background: J.okDim, border: `1px solid ${J.ok}44`, borderRadius: 4 }}>
                <div style={{ color: J.ok, fontSize: 11, marginBottom: 6 }}>✓ STATUS UPDATED — {promoted.exp_id}</div>
                <div style={{ color: J.textSec, fontSize: 11 }}>
                  {promoted.previous_status} → <span style={{ color: STATUS_COLORS[promoted.new_status] || J.ok }}>{promoted.new_status}</span>
                  <span style={{ color: J.textDim, marginLeft: 10, fontSize: 10 }}>{promoted.transitioned_at?.slice(0,16)}</span>
                </div>
              </div>
            )}
            {/* Lifecycle diagram */}
            <div style={{ marginTop: 20, padding: 16, background: J.bgCard, border: `1px solid ${J.border}`, borderRadius: 4 }}>
              <Lbl c={J.warn}>Allowed Transitions</Lbl>
              <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                {[
                  { from: "DRAFT", to: "CONFIRMED", label: "root cause confirmed" },
                  { from: "CONFIRMED", to: "STABLE", label: "reused successfully" },
                  { from: "CONFIRMED", to: "SUPERSEDED", label: "replaced by new" },
                  { from: "STABLE", to: "SUPERSEDED", label: "replaced by new" },
                ].map((t, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", background: J.bgPanel, border: `1px solid ${J.border}`, borderRadius: 3 }}>
                    <span style={{ color: STATUS_COLORS[t.from], fontSize: 10, fontFamily: J.fontHeader, fontWeight: 600 }}>{t.from}</span>
                    <span style={{ color: J.textDim, fontSize: 10 }}>→</span>
                    <span style={{ color: STATUS_COLORS[t.to], fontSize: 10, fontFamily: J.fontHeader, fontWeight: 600 }}>{t.to}</span>
                    <span style={{ color: J.textDim, fontSize: 9 }}>({t.label})</span>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 10, color: J.textDim, fontSize: 10 }}>Skip transitions (e.g. DRAFT → STABLE) are rejected by LifecycleManager.</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// SELF-EVOLUTION PANEL — 9-phase autonomous evolution pipeline
// Backend: POST /api/evolve (SSE stream)
// Blueprint: self-evolution-skill-blueprint.md
// ═══════════════════════════════════════════════════════════════════════════════
const EvolutionPanel = ({ onClose, userId }: { onClose: () => void; userId: string }) => {
  const [phase,      setPhase]     = useState<"configure"|"running"|"approval"|"done">("configure");
  const [taskDesc,   setTaskDesc]  = useState("");
  const [originPath, setOriginPath]= useState("");
  const [slug,       setSlug]      = useState("");
  const [launching,  setLaunching] = useState(false);
  const [log,        setLog]       = useState<string[]>([]);
  const [approval,   setApproval]  = useState<string | null>(null);
  const [approved,   setApproved]  = useState(false);
  const [err,        setErr]       = useState("");
  const [currentPhaseNum, setCurrentPhaseNum] = useState(0);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const PHASES = [
    { n: 0, label: "Experience Lookup",     color: J.react },
    { n: 1, label: "Workspace Bootstrap",   color: J.accent },
    { n: 2, label: "Source Discovery",      color: J.accent },
    { n: 3, label: "Analysis",              color: J.accent },
    { n: 4, label: "Blueprint Generation",  color: J.gold },
    { n: 5, label: "HUMAN APPROVAL GATE",   color: J.warn },
    { n: 6, label: "Implementation",        color: J.ok },
    { n: 7, label: "Testing",               color: J.ok },
    { n: 8, label: "Deploy + Verify",       color: J.ok },
    { n: 9, label: "Experience Capture",    color: J.react },
  ];

  const launch = async () => {
    if (!taskDesc.trim() || !originPath.trim()) { setErr("Task description and origin path are required."); return; }
    const autoSlug = slug.trim() || taskDesc.trim().toLowerCase().replace(/\s+/g,"-").slice(0,30);
    setErr(""); setLaunching(true); setLog([]); setPhase("running"); setCurrentPhaseNum(0);

    try {
      const r = await api(`${API_URL}/evolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_description: taskDesc, origin_path: originPath, slug: autoSlug, user_id: userId }),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || `HTTP ${r.status}`); }

      const reader = r.body!.getReader();
      const dec    = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const ev = JSON.parse(line.slice(5).trim());
            if (ev.type === "token" && ev.data) {
              setLog(prev => {
                const last = prev[prev.length - 1] || "";
                return [...prev.slice(0,-1), last + ev.data];
              });
            } else if (ev.type === "react_status") {
              const phaseMatch = ev.data?.phase?.match(/phase\s*(\d)/i);
              if (phaseMatch) setCurrentPhaseNum(parseInt(phaseMatch[1]));
              setLog(prev => [...prev, `\n── REACT [${ev.data?.iteration || "?"}] ${ev.data?.phase?.toUpperCase()} ──\n`]);
            } else if (ev.type === "tool_result") {
              const output = ev.data?.output;
              if (output && typeof output === "object") setLog(prev => [...prev, `  → ${JSON.stringify(output).slice(0,200)}\n`]);
            } else if (ev.type === "done") {
              const allText = log.join("") ;
              // Check if agent stopped for blueprint approval
              if (allText.toLowerCase().includes("approval") || allText.toLowerCase().includes("awaiting")) {
                setPhase("approval");
                setApproval(allText);
              } else {
                setPhase("done");
              }
              break;
            } else if (ev.type === "error") {
              setErr(`Agent error: ${ev.data}`); setPhase("configure");
            }
          } catch {}
        }
      }
    } catch (e: any) { setErr(`Launch failed: ${e.message}`); setPhase("configure"); }
    setLaunching(false);
  };

  const sendApproval = async (approved: boolean) => {
    setApproved(approved);
    if (approved) {
      setLog(prev => [...prev, "\n── ✓ BLUEPRINT APPROVED — PROCEEDING TO PHASE 6 ──\n"]);
      setCurrentPhaseNum(6);
      // Re-trigger the agent with approval signal via chat
      try {
        const r = await api(`${API_URL}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: "APPROVED — proceed with implementation", user_id: userId, react: true }),
        });
        if (r.ok) {
          const reader = r.body!.getReader();
          const dec    = new TextDecoder();
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = dec.decode(value);
            const lines = chunk.split("\n");
            for (const line of lines) {
              if (!line.startsWith("data:")) continue;
              try {
                const ev = JSON.parse(line.slice(5).trim());
                if (ev.type === "token" && ev.data) setLog(prev => { const last = prev[prev.length-1]||""; return [...prev.slice(0,-1), last+ev.data]; });
                if (ev.type === "done") { setPhase("done"); return; }
              } catch {}
            }
          }
        }
      } catch (e: any) { setLog(prev => [...prev, `\nApproval relay error: ${e.message}`]); }
      setPhase("done");
    } else {
      setLog(prev => [...prev, "\n── ✕ BLUEPRINT REJECTED — EVOLUTION ABORTED ──\n"]);
      setPhase("done");
    }
  };

  const reset = () => { setPhase("configure"); setLog([]); setErr(""); setApproval(null); setApproved(false); setCurrentPhaseNum(0); };

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F4`, zIndex: 100, display: "flex", flexDirection: "column", fontFamily: J.fontMono }}>
      {/* Header */}
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 22, height: 22, borderRadius: "50%", background: J.bgCard, border: `1px solid ${J.warm}55`, animation: phase === "running" ? "hud-glow 2s ease-in-out infinite" : "none", display: "flex", alignItems: "center", justifyContent: "center", color: J.warm, fontSize: 11 }}>◈</div>
          <div>
            <div style={{ color: J.warm, fontSize: 11, letterSpacing: "0.18em", fontFamily: J.fontHeader, fontWeight: 700 }}>SELF-EVOLUTION PIPELINE</div>
            <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.12em" }}>9-PHASE AUTONOMOUS EVOLUTION · HUMAN APPROVAL GATE AT PHASE 5</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {phase !== "configure" && <button onClick={reset} style={{ padding: "4px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textSec, borderRadius: 2, fontSize: 10 }}>↺ RESET</button>}
          <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕</button>
        </div>
      </div>

      {err && <div style={{ background: J.errDim, border: `1px solid ${J.err}44`, color: J.err, fontSize: 11, padding: "8px 28px" }}>⚠ {err}</div>}

      {/* Phase progress bar */}
      {phase !== "configure" && (
        <div style={{ padding: "10px 28px", background: J.bgPanel, borderBottom: `1px solid ${J.border}`, display: "flex", gap: 2 }}>
          {PHASES.map(p => (
            <div key={p.n} title={`Phase ${p.n}: ${p.label}`} style={{
              flex: 1, height: 4, borderRadius: 2,
              background: p.n < currentPhaseNum ? p.color : p.n === currentPhaseNum ? `${p.color}` : J.border,
              opacity: p.n < currentPhaseNum ? 0.6 : p.n === currentPhaseNum ? 1 : 0.25,
              animation: p.n === currentPhaseNum && phase === "running" ? "hud-pulse 1.2s infinite" : "none",
              transition: "background 0.5s, opacity 0.5s",
            }} />
          ))}
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px", display: "grid", gridTemplateColumns: phase !== "configure" ? "280px 1fr" : "1fr", gap: 20, maxWidth: 1100, margin: "0 auto", width: "100%" }}>

        {/* ── Left: phase map or configure form ── */}
        {phase === "configure" ? (
          <div style={{ maxWidth: 680, margin: "0 auto", width: "100%" }}>
            <div style={{ marginBottom: 20, padding: "12px 16px", background: `${J.warm}08`, border: `1px solid ${J.warm}22`, borderLeft: `3px solid ${J.warm}`, borderRadius: 4 }}>
              <Lbl c={J.warm}>9-Phase Autonomous Evolution Protocol</Lbl>
              <div style={{ color: J.textSec, fontSize: 11, marginTop: 6, lineHeight: 1.8 }}>
                Agent will: analyse existing code → design improvements → generate blueprint → <span style={{ color: J.warn }}>⚠ PAUSE for your approval</span> → implement → test → deploy → capture experience. All work happens in an isolated workspace.
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <div style={{ gridColumn: "1/-1" }}>
                <Lbl>Task Description *</Lbl>
                <textarea value={taskDesc} onChange={e => setTaskDesc(e.target.value)} rows={3}
                  placeholder="Describe what to evolve or improve…"
                  style={{ width: "100%", marginTop: 5, padding: "8px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3, resize: "vertical" }} />
              </div>
              <div style={{ gridColumn: "1/-1" }}>
                <Lbl>Origin Path * — skill/file to evolve</Lbl>
                <input value={originPath} onChange={e => setOriginPath(e.target.value)}
                  placeholder="e.g. /app/skills/port_scanner.py or /app/skills/os_execution/"
                  style={{ width: "100%", marginTop: 5, padding: "8px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }} />
              </div>
              <div style={{ gridColumn: "1/-1" }}>
                <Lbl>Workspace Slug — optional label</Lbl>
                <input value={slug} onChange={e => setSlug(e.target.value)}
                  placeholder="e.g. add-rate-limiting (auto-generated if empty)"
                  style={{ width: "100%", marginTop: 5, padding: "8px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }} />
              </div>
              <div style={{ gridColumn: "1/-1", marginTop: 8 }}>
                <button onClick={launch} disabled={launching || !taskDesc.trim() || !originPath.trim()} style={{
                  padding: "10px 32px", background: `${J.warm}0C`, border: `1px solid ${J.warm}`,
                  color: J.warm, borderRadius: 3, fontSize: 12, letterSpacing: "0.15em",
                  boxShadow: `0 0 12px ${J.warm}22`,
                }}>{launching ? "LAUNCHING…" : "◈ INITIATE EVOLUTION"}</button>
                <div style={{ color: J.textDim, fontSize: 10, marginTop: 8 }}>⚠ Agent will pause at Phase 5 (Blueprint Approval Gate) before making any changes</div>
              </div>
            </div>
          </div>
        ) : (
          /* Phase map sidebar */
          <div>
            {PHASES.map(p => (
              <div key={p.n} style={{
                marginBottom: 4, padding: "8px 12px",
                background: p.n < currentPhaseNum ? `${p.color}08` : p.n === currentPhaseNum ? `${p.color}12` : "transparent",
                border: `1px solid ${p.n <= currentPhaseNum ? p.color + "33" : J.border}`,
                borderLeft: `3px solid ${p.n < currentPhaseNum ? p.color + "88" : p.n === currentPhaseNum ? p.color : J.border}`,
                borderRadius: 3, transition: "all 0.4s",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: p.n < currentPhaseNum ? p.color : p.n === currentPhaseNum ? p.color : J.textDim, fontSize: 9, fontFamily: J.fontHeader, fontWeight: 700, minWidth: 24 }}>
                    {p.n < currentPhaseNum ? "✓" : p.n === currentPhaseNum ? "▶" : String(p.n).padStart(2,"0")}
                  </span>
                  <span style={{ color: p.n <= currentPhaseNum ? p.color : J.textDim, fontSize: 10, fontFamily: J.fontHeader, fontWeight: p.n === currentPhaseNum ? 700 : 400 }}>
                    {p.label}
                  </span>
                  {p.n === 5 && <Chip label="GATE" color={J.warn} />}
                  {p.n === currentPhaseNum && phase === "running" && <span style={{ color: p.color, fontSize: 9, animation: "hud-pulse 1s infinite" }}>⬡</span>}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── Right: live log ── */}
        {phase !== "configure" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Approval gate */}
            {phase === "approval" && (
              <div style={{ padding: 18, background: `${J.warn}08`, border: `2px solid ${J.warn}66`, borderRadius: 6, position: "relative" }}>
                <Corners color={J.warn} size={10} />
                <div style={{ color: J.warn, fontSize: 12, letterSpacing: "0.12em", marginBottom: 8, fontFamily: J.fontHeader, fontWeight: 700 }}>
                  ⚠ PHASE 5 — BLUEPRINT APPROVAL GATE
                </div>
                <div style={{ color: "#FFD5A0", fontSize: 13, marginBottom: 14, lineHeight: 1.65 }}>
                  The agent has generated a blueprint for your review. Approve to proceed with implementation, or reject to abort the evolution.
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => sendApproval(true)} style={{ padding: "8px 24px", background: J.okDim, border: `1px solid ${J.ok}88`, color: J.ok, borderRadius: 3, fontSize: 12, letterSpacing: "0.1em" }}>
                    ✓ APPROVED — PROCEED TO IMPLEMENTATION
                  </button>
                  <button onClick={() => sendApproval(false)} style={{ padding: "8px 24px", background: J.errDim, border: `1px solid ${J.err}88`, color: J.err, borderRadius: 3, fontSize: 12, letterSpacing: "0.1em" }}>
                    ✕ REJECTED — ABORT
                  </button>
                </div>
              </div>
            )}

            {phase === "done" && (
              <div style={{ padding: "10px 16px", background: approved === false ? J.errDim : J.okDim, border: `1px solid ${approved === false ? J.err : J.ok}44`, borderRadius: 4 }}>
                <span style={{ color: approved === false ? J.err : J.ok, fontSize: 11, letterSpacing: "0.1em" }}>
                  {approved === false ? "✕ EVOLUTION ABORTED BY OPERATOR" : "✓ EVOLUTION PIPELINE COMPLETE"}
                </span>
              </div>
            )}

            {/* Agent log */}
            <div ref={logRef} style={{ flex: 1, background: J.bgDeep, border: `1px solid ${J.border}`, borderRadius: 4, padding: "14px 16px", overflowY: "auto", maxHeight: "calc(100vh - 320px)", minHeight: 300 }}>
              <div style={{ color: J.textDim, fontSize: 9, marginBottom: 8, letterSpacing: "0.12em" }}>EVOLUTION LOG — {taskDesc.slice(0,50)}{taskDesc.length > 50 ? "…" : ""}</div>
              {log.length === 0 && phase === "running" && (
                <div style={{ color: J.accentDim, fontSize: 11, animation: "hud-pulse 1.5s infinite" }}>Agent initialising…</div>
              )}
              {log.map((line, i) => (
                <pre key={i} style={{
                  margin: 0, fontSize: 11, lineHeight: 1.7,
                  color: line.includes("── REACT") ? J.react
                       : line.includes("✓") || line.includes("COMPLETE") ? J.ok
                       : line.includes("✕") || line.includes("ERROR") || line.includes("FAIL") ? J.err
                       : line.includes("APPROVAL") || line.includes("PHASE 5") ? J.warn
                       : line.includes("→") ? J.accentDim
                       : J.textSec,
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}>{line}</pre>
              ))}
              {phase === "running" && (
                <span style={{ color: J.warm, animation: "hud-blink 1s infinite", fontSize: 14 }}>▋</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};


// ═══════════════════════════════════════════════════════════════════════════════
// TELEMETRY TYPES
// ═══════════════════════════════════════════════════════════════════════════════
interface TokenSnapshot {
  ts:         string;
  role:       string;
  chars:      number;
  est_tokens: number;
  model:      string;
  provider:   string;
}
interface CommandEntry {
  id:          string;
  ts:          string;
  skill:       string;
  action:      string;
  params:      any;
  success:     boolean;
  duration_ms: number | null;
  returncode:  number | null;
  stdout_len:  number;
  stderr_len:  number;
  timed_out:   boolean;
  is_kali:     boolean;
  command:     string;
  stdout:      string;
  stderr:      string;
}
function estTokens(chars: number) { return Math.ceil(chars / 4); }
function isKaliSkill(skill: string) {
  return ["os_execution","kali"].includes(skill);
}
function tsNow() { return new Date().toISOString(); }
function fmtTs(ts: string) {
  try { return new Date(ts).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return ts.slice(11,19); }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TELEMETRY PANEL — Token Usage + Command Log + Kali Audit
// ═══════════════════════════════════════════════════════════════════════════════
const TelemetryPanel = ({
  onClose,
  tokenLog,
  commandLog,
  onClearTokens,
  onClearCommands,
}: {
  onClose: () => void;
  tokenLog: TokenSnapshot[];
  commandLog: CommandEntry[];
  onClearTokens: () => void;
  onClearCommands: () => void;
}) => {
  const [tab, setTab] = useState<"tokens"|"commands"|"kali">("tokens");
  const [cmdFilter, setCmdFilter] = useState<"all"|"kali"|"success"|"failed">("all");
  const [expandId, setExpandId] = useState<string|null>(null);

  // ── Token aggregation ────────────────────────────────────────────────────
  const totalChars     = tokenLog.reduce((s, t) => s + t.chars, 0);
  const totalEst       = tokenLog.reduce((s, t) => s + t.est_tokens, 0);
  const userChars      = tokenLog.filter(t => t.role === "user").reduce((s, t) => s + t.chars, 0);
  const asstChars      = tokenLog.filter(t => t.role === "assistant").reduce((s, t) => s + t.chars, 0);
  const kaliCmds       = commandLog.filter(c => c.is_kali);
  const failedCmds     = commandLog.filter(c => !c.success || c.returncode !== 0);
  const avgDuration    = commandLog.length
    ? Math.round(commandLog.filter(c => c.duration_ms != null).reduce((s, c) => s + (c.duration_ms||0), 0)
        / commandLog.filter(c => c.duration_ms != null).length)
    : 0;

  // ── Filtered command list ────────────────────────────────────────────────
  const filteredCmds = commandLog.filter(c => {
    if (cmdFilter === "kali")    return c.is_kali;
    if (cmdFilter === "success") return c.success && (c.returncode === 0 || c.returncode === null);
    if (cmdFilter === "failed")  return !c.success || (c.returncode !== null && c.returncode !== 0);
    return true;
  }).slice().reverse();  // newest first

  const kaliFiltered = kaliCmds.slice().reverse();

  // ── Stat card ────────────────────────────────────────────────────────────
  const StatCard = ({ label, value, color = J.accent, sub = "" }: any) => (
    <div style={{
      padding: "12px 14px", background: J.bgCard,
      border: `1px solid ${color}22`, borderTop: `2px solid ${color}55`,
      borderRadius: 4, position: "relative" as const,
    }}>
      <Corners color={color} size={5} />
      <div style={{ color, fontSize: 22, fontFamily: J.fontHeader, fontWeight: 700, lineHeight: 1 }}>{value}</div>
      <div style={{ color: J.textSec, fontSize: 10, marginTop: 4, letterSpacing: "0.08em" }}>{label}</div>
      {sub && <div style={{ color: J.textDim, fontSize: 9, marginTop: 2 }}>{sub}</div>}
    </div>
  );

  // ── Row renderer ─────────────────────────────────────────────────────────
  const CmdRow = ({ c }: { c: CommandEntry }) => {
    const expanded = expandId === c.id;
    const sk = SKILL_META[c.skill] || { border: J.borderMid, icon: "◈" };
    const rcColor = c.returncode === 0 ? J.ok : c.returncode === null ? J.textSec : J.err;
    const successColor = c.success ? J.ok : J.err;
    return (
      <div style={{
        marginBottom: 4,
        background: expanded ? J.bgCard : "transparent",
        border: `1px solid ${expanded ? J.borderMid : J.border}`,
        borderLeft: `3px solid ${c.is_kali ? J.err : sk.border}`,
        borderRadius: 3, overflow: "hidden",
      }}>
        {/* Row header */}
        <div onClick={() => setExpandId(expanded ? null : c.id)} style={{
          padding: "7px 12px", cursor: "pointer",
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" as const,
        }}>
          <span style={{ color: J.textDim, fontSize: 9, minWidth: 70 }}>{fmtTs(c.ts)}</span>
          <span style={{ color: c.is_kali ? J.err : sk.border, fontSize: 10, minWidth: 90, fontFamily: J.fontHeader, fontWeight: 600 }}>
            {c.is_kali ? "⚡ KALI" : sk.icon} {c.skill.toUpperCase()}
          </span>
          <span style={{ color: J.accentDim, fontSize: 10, minWidth: 80 }}>{c.action}</span>
          <span style={{ flex: 1, color: J.textPri, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const, fontFamily: J.fontMono }}>
            {c.command || JSON.stringify(c.params).slice(0,80)}
          </span>
          <div style={{ display: "flex", gap: 5, alignItems: "center", flexShrink: 0 }}>
            {c.duration_ms != null && <span style={{ color: J.textSec, fontSize: 9 }}>{c.duration_ms}ms</span>}
            {c.timed_out && <Chip label="TIMEOUT" color={J.warn} />}
            {c.returncode != null && <span style={{ color: rcColor, fontSize: 10, fontFamily: J.fontHeader, fontWeight: 700 }}>rc={c.returncode}</span>}
            <div style={{ width: 7, height: 7, borderRadius: "50%", background: successColor, flexShrink: 0 }} />
          </div>
        </div>
        {/* Expanded detail */}
        {expanded && (
          <div style={{ padding: "0 12px 12px", borderTop: `1px solid ${J.border}` }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
              <div>
                <div style={{ color: J.textDim, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>PARAMS</div>
                <pre style={{ color: J.textSec, fontSize: 10, whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const, margin: 0 }}>
                  {JSON.stringify(c.params, null, 2).slice(0, 400)}
                </pre>
              </div>
              <div>
                <div style={{ color: J.textDim, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>METRICS</div>
                <div style={{ fontSize: 10, lineHeight: 2, color: J.textSec }}>
                  <div>Duration: <span style={{ color: J.textPri }}>{c.duration_ms != null ? c.duration_ms + "ms" : "—"}</span></div>
                  <div>Return code: <span style={{ color: rcColor }}>{c.returncode != null ? c.returncode : "—"}</span></div>
                  <div>Stdout bytes: <span style={{ color: J.textPri }}>{c.stdout_len}</span></div>
                  <div>Stderr bytes: <span style={{ color: c.stderr_len > 0 ? J.warn : J.textPri }}>{c.stderr_len}</span></div>
                  <div>Timed out: <span style={{ color: c.timed_out ? J.err : J.ok }}>{c.timed_out ? "YES" : "no"}</span></div>
                </div>
              </div>
            </div>
            {c.stdout && (
              <div style={{ marginTop: 10 }}>
                <div style={{ color: J.ok, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>STDOUT (first 2KB)</div>
                <pre style={{ color: J.textSec, fontSize: 10, background: J.bgDeep, padding: "8px 10px", borderRadius: 3, maxHeight: 180, overflowY: "auto" as const, margin: 0, whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const }}>
                  {c.stdout.slice(0, 2048)}{c.stdout.length > 2048 ? "\n… [truncated]" : ""}
                </pre>
              </div>
            )}
            {c.stderr && (
              <div style={{ marginTop: 8 }}>
                <div style={{ color: J.err, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>STDERR</div>
                <pre style={{ color: "#FF8888", fontSize: 10, background: J.bgDeep, padding: "8px 10px", borderRadius: 3, maxHeight: 120, overflowY: "auto" as const, margin: 0, whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const }}>
                  {c.stderr.slice(0, 1024)}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const TAB_BTN = (id: typeof tab, label: string, color: string, badge?: number) => (
    <button onClick={() => setTab(id)} style={{
      padding: "5px 16px", borderRadius: "3px 3px 0 0", fontSize: 10,
      background: tab === id ? J.bgCard : "transparent",
      border: `1px solid ${tab === id ? color + "66" : J.border}`,
      borderBottom: tab === id ? `1px solid ${J.bgCard}` : `1px solid ${J.border}`,
      color: tab === id ? color : J.textSec,
      fontFamily: J.fontHeader, fontWeight: 600, letterSpacing: "0.1em",
      display: "flex", alignItems: "center", gap: 6,
    }}>
      {label}
      {badge != null && badge > 0 && (
        <span style={{ background: color, color: J.bg, borderRadius: 10, padding: "0 5px", fontSize: 8, fontWeight: 700 }}>{badge}</span>
      )}
    </button>
  );

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F4`, zIndex: 100, display: "flex", flexDirection: "column", fontFamily: J.fontMono }}>
      {/* Header */}
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 22, height: 22, borderRadius: "50%", background: J.bgCard, border: `1px solid ${J.accent}55`, display: "flex", alignItems: "center", justifyContent: "center", color: J.accent, fontSize: 11 }}>⊕</div>
          <div>
            <div style={{ color: J.accent, fontSize: 11, letterSpacing: "0.18em", fontFamily: J.fontHeader, fontWeight: 700 }}>TELEMETRY & AUDIT</div>
            <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.12em" }}>TOKEN USAGE · COMMAND LOG · KALI AUDIT</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={tab === "tokens" ? onClearTokens : onClearCommands} style={{ padding: "4px 12px", background: J.errDim, border: `1px solid ${J.err}44`, color: "#FF9999", borderRadius: 2, fontSize: 10, letterSpacing: "0.06em" }}>
            ⊗ CLEAR {tab === "tokens" ? "TOKEN LOG" : "COMMAND LOG"}
          </button>
          <button onClick={onClose} style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕</button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ padding: "12px 28px 0", background: J.bgPanel, display: "flex", gap: 2, borderBottom: `1px solid ${J.border}` }}>
        {TAB_BTN("tokens",   "⊕ TOKEN USAGE",   J.accent, tokenLog.length)}
        {TAB_BTN("commands", "⬡ COMMAND LOG",   J.react, commandLog.length)}
        {TAB_BTN("kali",     "⚡ KALI AUDIT",    J.err, kaliCmds.length)}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px", maxWidth: 1200, margin: "0 auto", width: "100%" }}>

        {/* ── TOKEN USAGE ── */}
        {tab === "tokens" && (
          <div>
            {/* Summary cards */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 20 }}>
              <StatCard label="Total Est. Tokens"  value={totalEst.toLocaleString()}           color={J.accent} sub="chars ÷ 4" />
              <StatCard label="Total Characters"   value={totalChars.toLocaleString()}          color={J.accentDim} />
              <StatCard label="User Input"         value={estTokens(userChars).toLocaleString()} color={J.gold}   sub={`${userChars} chars`} />
              <StatCard label="AI Output"          value={estTokens(asstChars).toLocaleString()} color={J.react}  sub={`${asstChars} chars`} />
              <StatCard label="Message Count"      value={tokenLog.length}                       color={J.ok} />
            </div>

            {/* Per-message log */}
            {tokenLog.length === 0
              ? <div style={{ color: J.textDim, textAlign: "center", padding: 40 }}>No token data recorded yet. Start chatting.</div>
              : (
                <div>
                  <div style={{ display: "grid", gridTemplateColumns: "80px 70px 90px 80px 1fr 80px", gap: 0, padding: "4px 12px", background: J.bgCard, borderRadius: "3px 3px 0 0", borderBottom: `1px solid ${J.border}` }}>
                    {["TIME","ROLE","~TOKENS","CHARS","MODEL","PROVIDER"].map(h => (
                      <Lbl key={h} c={J.textDim}>{h}</Lbl>
                    ))}
                  </div>
                  <div style={{ maxHeight: "calc(100vh - 320px)", overflowY: "auto", border: `1px solid ${J.border}`, borderRadius: "0 0 3px 3px" }}>
                    {tokenLog.slice().reverse().map((t, i) => (
                      <div key={i} style={{
                        display: "grid", gridTemplateColumns: "80px 70px 90px 80px 1fr 80px",
                        padding: "6px 12px", gap: 0,
                        borderBottom: `1px solid ${J.border}`,
                        background: i % 2 === 0 ? "transparent" : `${J.bgCard}66`,
                      }}>
                        <span style={{ color: J.textDim, fontSize: 10 }}>{fmtTs(t.ts)}</span>
                        <span style={{ color: t.role === "user" ? J.gold : J.accent, fontSize: 10, fontFamily: J.fontHeader, fontWeight: 600 }}>{t.role.toUpperCase()}</span>
                        <span style={{ color: t.role === "assistant" ? J.react : J.gold, fontSize: 11, fontFamily: J.fontHeader, fontWeight: 700 }}>~{t.est_tokens.toLocaleString()}</span>
                        <span style={{ color: J.textSec, fontSize: 10 }}>{t.chars.toLocaleString()}</span>
                        <span style={{ color: J.textSec, fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.model}</span>
                        <span style={{ color: PROVIDERS[t.provider]?.color || J.textSec, fontSize: 10, fontFamily: J.fontHeader }}>{t.provider}</span>
                      </div>
                    ))}
                  </div>
                  {/* Usage bar */}
                  <div style={{ marginTop: 16, padding: "10px 14px", background: J.bgCard, border: `1px solid ${J.borderMid}`, borderRadius: 4 }}>
                    <Lbl c={J.textSec}>CONTEXT USAGE RATIO — user vs assistant</Lbl>
                    <div style={{ marginTop: 8, height: 8, background: J.bgPanel, borderRadius: 4, overflow: "hidden", display: "flex" }}>
                      <div style={{ width: `${totalChars ? (userChars/totalChars)*100 : 50}%`, background: J.gold, borderRadius: "4px 0 0 4px", transition: "width 0.3s" }} />
                      <div style={{ flex: 1, background: J.react }} />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", marginTop: 5 }}>
                      <span style={{ color: J.gold, fontSize: 9 }}>USER {totalChars ? Math.round((userChars/totalChars)*100) : 0}%</span>
                      <span style={{ color: J.react, fontSize: 9 }}>ASSISTANT {totalChars ? Math.round((asstChars/totalChars)*100) : 0}%</span>
                    </div>
                  </div>
                </div>
              )
            }
          </div>
        )}

        {/* ── COMMAND LOG ── */}
        {tab === "commands" && (
          <div>
            {/* Summary */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 16 }}>
              <StatCard label="Total Commands"  value={commandLog.length}         color={J.react} />
              <StatCard label="Kali Commands"   value={kaliCmds.length}           color={J.err}   sub="os_execution" />
              <StatCard label="Failed"          value={failedCmds.length}         color={failedCmds.length > 0 ? J.err : J.ok} />
              <StatCard label="Avg Duration"    value={avgDuration ? avgDuration+"ms" : "—"} color={J.accent} />
              <StatCard label="Timed Out"       value={commandLog.filter(c=>c.timed_out).length} color={J.warn} />
            </div>
            {/* Filter bar */}
            <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
              {(["all","kali","success","failed"] as const).map(f => (
                <button key={f} onClick={() => setCmdFilter(f)} style={{
                  padding: "4px 12px", borderRadius: 2, fontSize: 10,
                  background: cmdFilter === f ? `${J.react}12` : "transparent",
                  border: `1px solid ${cmdFilter === f ? J.react : J.border}`,
                  color: cmdFilter === f ? J.react : J.textSec,
                  fontFamily: J.fontHeader, fontWeight: 600, letterSpacing: "0.08em",
                }}>{f.toUpperCase()} {f === "all" ? `(${commandLog.length})` : f === "kali" ? `(${kaliCmds.length})` : f === "failed" ? `(${failedCmds.length})` : ""}</button>
              ))}
              <div style={{ marginLeft: "auto", color: J.textDim, fontSize: 9, alignSelf: "center" }}>Click a row to expand stdout/stderr</div>
            </div>
            {filteredCmds.length === 0
              ? <div style={{ color: J.textDim, textAlign: "center", padding: 40 }}>{commandLog.length === 0 ? "No commands executed yet." : "No commands match the current filter."}</div>
              : filteredCmds.map(c => <CmdRow key={c.id} c={c} />)
            }
          </div>
        )}

        {/* ── KALI AUDIT ── */}
        {tab === "kali" && (
          <div>
            <div style={{ marginBottom: 16, padding: "10px 16px", background: `${J.err}08`, border: `1px solid ${J.err}22`, borderLeft: `3px solid ${J.err}`, borderRadius: 4 }}>
              <Lbl c={J.err}>KALI SECURITY TOOL AUDIT LOG</Lbl>
              <div style={{ color: J.textSec, fontSize: 11, marginTop: 4, lineHeight: 1.7 }}>
                All <span style={{ color: J.err }}>os_execution</span> and <span style={{ color: J.err }}>kali</span> skill invocations are recorded here. This log cannot be cleared separately — use Clear Command Log to reset all.
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
              <StatCard label="Kali Executions"  value={kaliCmds.length}                                             color={J.err} />
              <StatCard label="Succeeded"        value={kaliCmds.filter(c => c.success && c.returncode === 0).length} color={J.ok} />
              <StatCard label="Failed / Error"   value={kaliCmds.filter(c => !c.success || (c.returncode !== null && c.returncode !== 0)).length} color={J.warn} />
              <StatCard label="Total Exec Time"  value={kaliCmds.reduce((s,c) => s+(c.duration_ms||0), 0) + "ms"}    color={J.accent} />
            </div>
            {kaliFiltered.length === 0
              ? <div style={{ color: J.textDim, textAlign: "center", padding: 40 }}>No Kali tool executions recorded yet.</div>
              : kaliFiltered.map(c => <CmdRow key={c.id} c={c} />)
            }
          </div>
        )}
      </div>
    </div>
  );
};


// ─── Instruction Store (localStorage-backed) ──────────────────────────────────
const INSTR_KEY = "jarvis_instructions";
interface Instruction {
  id:      string;
  title:   string;
  body:    string;
  enabled: boolean;
  created: string;
}
const DEFAULT_INSTRUCTIONS: Instruction[] = [
  { id: "i1", title: "Task Validation Rules",      enabled: false, created: new Date().toISOString(),
    body: "Before starting any task:\n1. Restate the goal in one sentence.\n2. List what you will do (max 5 steps).\n3. Identify any destructive actions and flag them.\n4. Confirm with operator before executing step 1." },
  { id: "i2", title: "System Upgrade Procedure",   enabled: false, created: new Date().toISOString(),
    body: "When upgrading system packages:\n1. Run: apt-get update\n2. Check: apt-get --simulate upgrade\n3. Review changes with operator.\n4. Run: apt-get upgrade -y only after approval.\n5. Verify services after upgrade." },
  { id: "i3", title: "Localhost Security Checklist", enabled: false, created: new Date().toISOString(),
    body: "Localhost security audit checklist:\n- Open ports: nmap -sV localhost\n- Running services: ss -tulnp\n- World-writable files: find / -perm -002 -type f 2>/dev/null\n- SUID binaries: find / -perm -4000 2>/dev/null\n- Crontabs: cat /etc/crontab && crontab -l\n- Auth log: tail -50 /var/log/auth.log" },
  { id: "i4", title: "ReAct Self-Healing Rules",    enabled: false, created: new Date().toISOString(),
    body: "When a tool call fails:\n1. Read the exact error message.\n2. Identify the root cause (wrong path, wrong params, missing dependency).\n3. Fix the specific parameter that caused the failure.\n4. Do NOT retry identically — always change something.\n5. After 3 failures on the same action, report to operator and stop." },
  { id: "i5", title: "CBD Blueprint Protocol",      enabled: false, created: new Date().toISOString(),
    body: "For all architectural work:\n1. Run experienced_lookup FIRST.\n2. Use cbd_architect.analyze_request before writing any code.\n3. Generate blueprint.md — STOP and show it to operator.\n4. Do NOT implement until operator explicitly writes APPROVED.\n5. Implement one component at a time. Test before moving to next." },
];
function loadInstructions(): Instruction[] {
  try {
    const raw = localStorage.getItem(INSTR_KEY);
    return raw ? JSON.parse(raw) : DEFAULT_INSTRUCTIONS;
  } catch { return DEFAULT_INSTRUCTIONS; }
}
function saveInstructions(list: Instruction[]) {
  try { localStorage.setItem(INSTR_KEY, JSON.stringify(list)); } catch {}
}
function buildInstructionBlock(list: Instruction[]): string {
  const active = list.filter(i => i.enabled);
  if (!active.length) return "";
  return active.map(i => `## ${i.title}\n${i.body}`).join("\n\n");
}

// ─── Instructions Panel ───────────────────────────────────────────────────────
const InstructionsPanel = ({
  instructions, setInstructions, onClose,
}: {
  instructions: Instruction[];
  setInstructions: (v: Instruction[]) => void;
  onClose: () => void;
}) => {
  const [editing, setEditing] = useState<Instruction | null>(null);
  const [newMode, setNewMode] = useState(false);
  const [form,    setForm]    = useState({ title: "", body: "" });

  const save = () => {
    if (!form.title.trim() || !form.body.trim()) return;
    if (newMode) {
      const created: Instruction = { id: Date.now().toString(), title: form.title.trim(), body: form.body.trim(), enabled: false, created: new Date().toISOString() };
      const updated = [...instructions, created];
      setInstructions(updated); saveInstructions(updated);
    } else if (editing) {
      const updated = instructions.map(i => i.id === editing.id ? { ...i, title: form.title.trim(), body: form.body.trim() } : i);
      setInstructions(updated); saveInstructions(updated);
    }
    setEditing(null); setNewMode(false); setForm({ title: "", body: "" });
  };

  const del = (id: string) => {
    const updated = instructions.filter(i => i.id !== id);
    setInstructions(updated); saveInstructions(updated);
    if (editing?.id === id) { setEditing(null); setForm({ title: "", body: "" }); }
  };

  const toggle = (id: string) => {
    const updated = instructions.map(i => i.id === id ? { ...i, enabled: !i.enabled } : i);
    setInstructions(updated); saveInstructions(updated);
  };

  const startEdit = (i: Instruction) => { setEditing(i); setNewMode(false); setForm({ title: i.title, body: i.body }); };
  const startNew  = () => { setEditing(null); setNewMode(true); setForm({ title: "", body: "" }); };
  const cancel    = () => { setEditing(null); setNewMode(false); setForm({ title: "", body: "" }); };

  const activeCount = instructions.filter(i => i.enabled).length;

  return (
    <div style={{ position: "fixed", inset: 0, background: `${J.bgDeep}F4`, zIndex: 100, display: "flex", flexDirection: "column", fontFamily: J.fontMono }}>
      <div style={{ padding: "12px 24px", borderBottom: `1px solid ${J.borderMid}`, background: J.bgPanel, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 22, height: 22, borderRadius: "50%", background: J.bgCard, border: `1px solid ${J.gold}55`, display: "flex", alignItems: "center", justifyContent: "center", color: J.gold, fontSize: 11 }}>⬡</div>
          <div>
            <div style={{ color: J.gold, fontSize: 11, letterSpacing: "0.18em", fontFamily: J.fontHeader, fontWeight: 700 }}>OPERATOR INSTRUCTIONS</div>
            <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.12em" }}>{activeCount} ACTIVE · Checked instructions are sent with every message</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={startNew} style={{ padding: "4px 14px", background: `${J.ok}0C`, border: `1px solid ${J.ok}44`, color: J.ok, borderRadius: 2, fontSize: 10, letterSpacing: "0.06em" }}>⊞ NEW INSTRUCTION</button>
          <button onClick={onClose}  style={{ background: "none", border: `1px solid ${J.borderMid}`, color: J.textSec, padding: "4px 14px", borderRadius: 2, fontSize: 11 }}>✕</button>
        </div>
      </div>
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "320px 1fr", overflow: "hidden" }}>
        {/* Left: list */}
        <div style={{ borderRight: `1px solid ${J.border}`, overflowY: "auto", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
          {instructions.length === 0 && (
            <div style={{ color: J.textDim, textAlign: "center", padding: 30, fontSize: 11 }}>No instructions. Click NEW to create one.</div>
          )}
          {instructions.map(ins => (
            <div key={ins.id} onClick={() => startEdit(ins)} style={{
              padding: "10px 12px", borderRadius: 3, cursor: "pointer",
              background: editing?.id === ins.id ? `${J.gold}0C` : J.bgCard,
              border: `1px solid ${editing?.id === ins.id ? J.gold + "55" : J.border}`,
              borderLeft: `3px solid ${ins.enabled ? J.gold : J.border}`,
              transition: "all 0.15s",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input type="checkbox" checked={ins.enabled}
                  onChange={e => { e.stopPropagation(); toggle(ins.id); }}
                  style={{ accentColor: J.gold, width: 13, height: 13, cursor: "pointer", flexShrink: 0 }}
                  onClick={e => e.stopPropagation()}
                />
                <span style={{ flex: 1, color: ins.enabled ? J.textPri : J.textSec, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ins.title}</span>
                {ins.enabled && <Chip label="ON" color={J.gold} />}
              </div>
              <div style={{ color: J.textDim, fontSize: 10, marginTop: 4, marginLeft: 21, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {ins.body.split("\n")[0].slice(0, 60)}
              </div>
            </div>
          ))}
        </div>
        {/* Right: editor */}
        <div style={{ overflowY: "auto", padding: "20px 24px" }}>
          {(newMode || editing) ? (
            <div>
              <div style={{ color: newMode ? J.ok : J.gold, fontSize: 11, letterSpacing: "0.12em", marginBottom: 16, fontFamily: J.fontHeader, fontWeight: 700 }}>
                {newMode ? "⊞ NEW INSTRUCTION" : `EDITING — ${editing?.title}`}
              </div>
              <div style={{ marginBottom: 12 }}>
                <Lbl>Title</Lbl>
                <input value={form.title} onChange={e => setForm(p => ({ ...p, title: e.target.value }))}
                  placeholder="e.g. Task Validation Rules"
                  style={{ width: "100%", marginTop: 5, padding: "8px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3 }} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <Lbl>Instruction Body — sent verbatim to the LLM when enabled</Lbl>
                <textarea value={form.body} onChange={e => setForm(p => ({ ...p, body: e.target.value }))}
                  rows={14}
                  placeholder={"Write step-by-step instructions, rules, or checklists...\nEach line is sent as-is to the LLM system context."}
                  style={{ width: "100%", marginTop: 5, padding: "10px 12px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 12, borderRadius: 3, resize: "vertical", lineHeight: 1.7 }} />
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={save} disabled={!form.title.trim() || !form.body.trim()} style={{ padding: "7px 22px", background: `${J.ok}0C`, border: `1px solid ${J.ok}`, color: J.ok, borderRadius: 3, fontSize: 11, letterSpacing: "0.1em" }}>
                  {newMode ? "⊞ CREATE" : "✓ SAVE"}
                </button>
                {editing && (
                  <button onClick={() => del(editing.id)} style={{ padding: "7px 16px", background: J.errDim, border: `1px solid ${J.err}44`, color: "#FF9999", borderRadius: 3, fontSize: 11 }}>⊗ DELETE</button>
                )}
                <button onClick={cancel} style={{ padding: "7px 16px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textSec, borderRadius: 3, fontSize: 11 }}>CANCEL</button>
              </div>
            </div>
          ) : (
            <div style={{ color: J.textDim, textAlign: "center", padding: 60 }}>
              <div style={{ fontSize: 28, marginBottom: 16, color: J.gold, opacity: 0.4 }}>⬡</div>
              <div style={{ fontSize: 11, lineHeight: 2 }}>Select an instruction to edit it,<br/>or click NEW INSTRUCTION to create one.<br/><br/>
                <span style={{ color: `${J.gold}66` }}>Checked instructions are prepended to every message sent to the LLM.</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};


// ═══════════════════════════════════════════════════════════════════════════════
// WORKSPACE FILE BROWSER SIDEBAR
// Default path: /app/{user_id}/workspace/
// Backend: GET /api/workspace/{user_id}  POST /api/workspace/{user_id}/read
// ═══════════════════════════════════════════════════════════════════════════════
interface WsEntry {
  name:    string;
  path:    string;
  dir:     string;
  size:    number;
  is_text: boolean;
  type:    "file";
}
interface WsFile {
  path:    string;
  name:    string;
  content: string;
  size:    number;
  is_text: boolean;
  truncated: boolean;
  error:   string | null;
}

function fmtSize(b: number): string {
  if (b >= 1_048_576) return `${(b/1_048_576).toFixed(1)}M`;
  if (b >= 1_024)     return `${(b/1_024).toFixed(1)}K`;
  return `${b}B`;
}

function fileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  const map: Record<string,string> = {
    py:"⬡", js:"⬡", ts:"⬡", jsx:"⬡", tsx:"⬡",
    sh:"⚙", bash:"⚙", zsh:"⚙",
    md:"◈", txt:"◈", rst:"◈",
    json:"⊞", yaml:"⊞", yml:"⊞", toml:"⊞", xml:"⊞",
    html:"◉", css:"◉",
    sql:"⊕", csv:"⊕",
    log:"⬢", conf:"⬢", cfg:"⬢", ini:"⬢", env:"⬢",
    go:"⬡", rs:"⬡", java:"⬡", c:"⬡", cpp:"⬡", h:"⬡",
  };
  return map[ext] || "◫";
}

const WorkspaceSidebar = ({
  userId,
  onFilesSelected,
  onProjectChange,
  currentProject,
}: {
  userId:          string;
  onFilesSelected: (files: WsFile[]) => void;
  onProjectChange: (project: string) => void;
  currentProject:  string;
}) => {
  // ── Project state ───────────────────────────────────────────────────────────
  const [projects,     setProjects]     = useState<any[]>([]);
  const [projLoading,  setProjLoading]  = useState(true);
  const [showNewProj,  setShowNewProj]  = useState(false);
  const [newProjName,  setNewProjName]  = useState("");
  const [newProjDesc,  setNewProjDesc]  = useState("");
  const [creating,     setCreating]     = useState(false);
  const [deleting,     setDeleting]     = useState(false);
  const [projDropOpen, setProjDropOpen] = useState(false);
  const projDropRef = useRef<HTMLDivElement>(null);

  // ── File state ──────────────────────────────────────────────────────────────
  const [entries,      setEntries]      = useState<WsEntry[]>([]);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState("");
  const [checked,      setChecked]      = useState<Set<string>>(new Set());
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [wsRoot,       setWsRoot]       = useState("");
  const [filter,       setFilter]       = useState("");
  const [sortBy,       setSortBy]       = useState<"name"|"size"|"dir">("name");
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set([""]));

  // ── Load projects ────────────────────────────────────────────────────────────
  const loadProjects = async () => {
    setProjLoading(true);
    try {
      const r = await api(`${API_URL}/projects/${userId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setProjects(d.projects || []);
    } catch { setProjects([]); }
    setProjLoading(false);
  };

  // ── Load files for current project ──────────────────────────────────────────
  const loadFiles = async (proj: string) => {
    setLoading(true); setError(""); setEntries([]); setChecked(new Set()); onFilesSelected([]);
    try {
      const r = await api(`${API_URL}/workspace/${userId}?project=${encodeURIComponent(proj)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setEntries(d.entries || []);
      setWsRoot(d.workspace_root || `/tmp/${userId}/${proj}/workspace`);
    } catch (e: any) {
      setError(e.message || "Cannot load workspace");
    }
    setLoading(false);
  };

  useEffect(() => { loadProjects(); }, [userId]);
  useEffect(() => { loadFiles(currentProject); }, [currentProject, userId]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!projDropOpen) return;
    const h = (e: MouseEvent) => {
      if (projDropRef.current && !projDropRef.current.contains(e.target as Node))
        setProjDropOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [projDropOpen]);

  // ── Create project ──────────────────────────────────────────────────────────
  const createProject = async () => {
    const name = newProjName.trim();
    if (!name) return;
    setCreating(true);
    try {
      const r = await api(`${API_URL}/projects/${userId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description: newProjDesc.trim() }),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "Create failed"); }
      const d = await r.json();
      await loadProjects();
      onProjectChange(d.name);
      setShowNewProj(false); setNewProjName(""); setNewProjDesc("");
      setProjDropOpen(false);
    } catch (e: any) { setError(e.message); }
    setCreating(false);
  };

  // ── Delete project ───────────────────────────────────────────────────────────
  const deleteProject = async (name: string) => {
    if (!window.confirm(`Delete project "${name}" and ALL its files? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      const r = await api(`${API_URL}/projects/${userId}/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "Delete failed"); }
      await loadProjects();
      onProjectChange("default");
    } catch (e: any) { setError(e.message); }
    setDeleting(false);
  };

  // ── File ops ─────────────────────────────────────────────────────────────────
  const readChecked = async (newChecked: Set<string>) => {
    if (newChecked.size === 0) { onFilesSelected([]); return; }
    setLoadingFiles(true);
    try {
      const r = await api(`${API_URL}/workspace/${userId}/read`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: Array.from(newChecked), project: currentProject }),
      });
      const d = await r.json();
      onFilesSelected(d.files || []);
    } catch { onFilesSelected([]); }
    setLoadingFiles(false);
  };

  const toggle = (path: string) => {
    setChecked(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      readChecked(next);
      return next;
    });
  };

  const checkAll  = () => { const s = new Set(filtered.filter(e => e.is_text).map(e => e.path)); setChecked(s); readChecked(s); };
  const uncheckAll = () => { setChecked(new Set()); onFilesSelected([]); };

  // ── Filter + sort + group ────────────────────────────────────────────────────
  const filtered = entries
    .filter(e => !filter || e.name.toLowerCase().includes(filter.toLowerCase()) || e.dir.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => sortBy === "size" ? b.size - a.size : sortBy === "dir" ? a.dir.localeCompare(b.dir) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));

  const byDir = filtered.reduce<Record<string, WsEntry[]>>((acc, e) => {
    const d = e.dir || "";
    if (!acc[d]) acc[d] = [];
    acc[d].push(e);
    return acc;
  }, {});

  const totalChecked = checked.size;
  const totalSize    = entries.filter(e => checked.has(e.path)).reduce((s, e) => s + e.size, 0);
  const currentProjMeta = projects.find(p => p.name === currentProject);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: J.bgPanel, borderLeft: `1px solid ${J.border}`, fontFamily: J.fontMono }}>

      {/* ── Project Picker ── */}
      <div style={{ padding: "10px 12px", borderBottom: `1px solid ${J.border}`, background: J.bgDeep }}>
        <Lbl c={J.accent}>PROJECT</Lbl>

        {/* Dropdown trigger */}
        <div ref={projDropRef} style={{ position: "relative", marginTop: 6 }}>
          <button onClick={() => setProjDropOpen(o => !o)} style={{
            width: "100%", padding: "6px 10px", textAlign: "left",
            background: J.bgCard, border: `1px solid ${projDropOpen ? J.accent : J.borderMid}`,
            borderRadius: 3, color: J.textPri, fontSize: 11,
            display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
          }}>
            <span style={{ color: J.accent, fontSize: 10 }}>◈</span>
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {currentProject}
              {currentProjMeta?.is_default && <span style={{ color: J.textDim, fontSize: 9, marginLeft: 6 }}>(default)</span>}
            </span>
            <span style={{ color: J.textDim, fontSize: 9 }}>
              {currentProjMeta ? `${currentProjMeta.file_count}f · ${fmtSize(currentProjMeta.size_bytes)}` : ""}
            </span>
            <span style={{ color: J.textDim, fontSize: 11 }}>{projDropOpen ? "▴" : "▾"}</span>
          </button>

          {/* Dropdown panel */}
          {projDropOpen && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 50,
              background: J.bgPanel, border: `1px solid ${J.borderMid}`,
              borderRadius: 4, boxShadow: "0 8px 32px #000A",
              maxHeight: 320, overflowY: "auto",
            }}>
              {/* Existing projects */}
              <div style={{ padding: "4px 0" }}>
                {projLoading
                  ? <div style={{ padding: "8px 12px", color: J.textDim, fontSize: 10, animation: "hud-pulse 1.5s infinite" }}>Loading projects…</div>
                  : projects.map(p => (
                    <div key={p.name} onClick={() => { onProjectChange(p.name); setProjDropOpen(false); }}
                      style={{
                        padding: "8px 12px", cursor: "pointer",
                        background: p.name === currentProject ? `${J.accent}0C` : "transparent",
                        borderLeft: `3px solid ${p.name === currentProject ? J.accent : "transparent"}`,
                        display: "flex", alignItems: "center", gap: 8,
                        transition: "background 0.1s",
                      }}
                      onMouseEnter={e => { if (p.name !== currentProject) (e.currentTarget as HTMLDivElement).style.background = `${J.bgCard}`; }}
                      onMouseLeave={e => { if (p.name !== currentProject) (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
                    >
                      <span style={{ color: p.name === currentProject ? J.accent : J.textDim, fontSize: 10 }}>◈</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ color: p.name === currentProject ? J.textPri : J.textSec, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.name}
                          {p.is_default && <span style={{ color: J.textDim, fontSize: 9, marginLeft: 5 }}>default</span>}
                        </div>
                        <div style={{ color: J.textDim, fontSize: 9, marginTop: 1 }}>
                          {p.file_count} file{p.file_count !== 1 ? "s" : ""} · {fmtSize(p.size_bytes)}
                        </div>
                      </div>
                      {!p.is_default && (
                        <button onClick={ev => { ev.stopPropagation(); deleteProject(p.name); }} disabled={deleting}
                          title={`Delete project "${p.name}"`}
                          style={{ background: "none", border: "none", color: J.err, fontSize: 12, cursor: "pointer", opacity: 0.6, padding: "0 2px", flexShrink: 0 }}>⊗</button>
                      )}
                    </div>
                  ))
                }
              </div>

              {/* New project form */}
              <div style={{ borderTop: `1px solid ${J.border}`, padding: 10 }}>
                {showNewProj ? (
                  <div>
                    <div style={{ color: J.ok, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>⊞ NEW PROJECT</div>
                    <input value={newProjName} onChange={e => setNewProjName(e.target.value)}
                      placeholder="Project name (e.g. web-scanner)"
                      onKeyDown={e => { if (e.key === "Enter") createProject(); if (e.key === "Escape") setShowNewProj(false); }}
                      autoFocus
                      style={{ width: "100%", padding: "5px 8px", background: J.bgCard, border: `1px solid ${J.borderMid}`, color: J.textPri, fontSize: 11, borderRadius: 2, marginBottom: 5 }} />
                    <input value={newProjDesc} onChange={e => setNewProjDesc(e.target.value)}
                      placeholder="Description (optional)"
                      style={{ width: "100%", padding: "5px 8px", background: J.bgCard, border: `1px solid ${J.border}`, color: J.textSec, fontSize: 10, borderRadius: 2, marginBottom: 7 }} />
                    <div style={{ display: "flex", gap: 5 }}>
                      <button onClick={createProject} disabled={creating || !newProjName.trim()} style={{
                        flex: 1, padding: "5px", background: `${J.ok}0C`, border: `1px solid ${J.ok}55`,
                        color: J.ok, borderRadius: 2, fontSize: 10, letterSpacing: "0.06em",
                      }}>{creating ? "CREATING…" : "⊞ CREATE"}</button>
                      <button onClick={() => { setShowNewProj(false); setNewProjName(""); setNewProjDesc(""); }} style={{
                        padding: "5px 10px", background: J.bgCard, border: `1px solid ${J.border}`,
                        color: J.textSec, borderRadius: 2, fontSize: 10,
                      }}>CANCEL</button>
                    </div>
                  </div>
                ) : (
                  <button onClick={() => setShowNewProj(true)} style={{
                    width: "100%", padding: "6px", background: "transparent",
                    border: `1px dashed ${J.borderMid}`, color: J.textSec,
                    borderRadius: 2, fontSize: 10, cursor: "pointer", letterSpacing: "0.06em",
                  }}>⊞ NEW PROJECT</button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Path display */}
        <div style={{ color: J.textDim, fontSize: 8, marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", letterSpacing: "0.04em" }} title={wsRoot}>
          /tmp/{userId}/{currentProject}/workspace
        </div>
      </div>

      {/* ── File browser header ── */}
      <div style={{ padding: "8px 12px", borderBottom: `1px solid ${J.border}`, background: J.bgDeep }}>
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 6 }}>
          <Lbl c={J.textSec}>FILES</Lbl>
          <div style={{ flex: 1 }} />
          <button onClick={() => loadFiles(currentProject)} title="Refresh" style={{ background: "none", border: `1px solid ${J.border}`, color: J.textDim, padding: "2px 6px", borderRadius: 2, fontSize: 9 }}>↺</button>
          <button
            onClick={async () => {
              const r = await api(`${API_URL}/workspace/${userId}/zip?project=${encodeURIComponent(currentProject)}`);
              if (!r.ok) return;
              const blob = await r.blob();
              const url  = URL.createObjectURL(blob);
              const a    = document.createElement("a");
              a.href     = url;
              a.download = `${currentProject}-workspace.zip`;
              document.body.appendChild(a); a.click(); document.body.removeChild(a);
              URL.revokeObjectURL(url);
            }}
            title="Download workspace as .zip"
            style={{ background: "none", border: `1px solid ${J.border}`, color: J.accentDim, padding: "2px 6px", borderRadius: 2, fontSize: 9, cursor: "pointer" }}
          >⬇</button>
        </div>
        <input value={filter} onChange={e => setFilter(e.target.value)}
          placeholder="Filter…"
          style={{ width: "100%", padding: "4px 8px", background: J.bgCard, border: `1px solid ${J.border}`, color: J.textPri, fontSize: 10, borderRadius: 2, marginBottom: 5 }} />
        <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
          {(["name","size","dir"] as const).map(s => (
            <button key={s} onClick={() => setSortBy(s)} style={{
              padding: "2px 5px", borderRadius: 2, fontSize: 8,
              background: sortBy === s ? `${J.accent}12` : "transparent",
              border: `1px solid ${sortBy === s ? J.accent : J.border}`,
              color: sortBy === s ? J.accent : J.textDim,
              fontFamily: J.fontHeader, fontWeight: 600,
            }}>{s.toUpperCase()}</button>
          ))}
          <div style={{ flex: 1 }} />
          <button onClick={checkAll}    style={{ padding: "2px 5px", borderRadius: 2, fontSize: 8, background: "none", border: `1px solid ${J.border}`, color: J.textDim }}>ALL</button>
          <button onClick={uncheckAll}  style={{ padding: "2px 5px", borderRadius: 2, fontSize: 8, background: "none", border: `1px solid ${J.border}`, color: J.textDim }}>NONE</button>
        </div>
      </div>

      {/* ── Checked summary ── */}
      {totalChecked > 0 && (
        <div style={{ padding: "4px 12px", background: `${J.accent}08`, borderBottom: `1px solid ${J.accent}22`, display: "flex", gap: 6, alignItems: "center" }}>
          {loadingFiles
            ? <span style={{ color: J.accentDim, fontSize: 9, animation: "hud-pulse 1s infinite" }}>Reading…</span>
            : <>
                <span style={{ color: J.accent, fontSize: 9 }}>◈ {totalChecked} selected · {fmtSize(totalSize)}</span>
                <span style={{ color: J.ok, fontSize: 9, marginLeft: "auto" }}>→ next message</span>
              </>
          }
        </div>
      )}

      {/* ── File list ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
        {loading && <div style={{ color: J.textSec, fontSize: 10, textAlign: "center", padding: 20, animation: "hud-pulse 1.5s infinite" }}>Loading…</div>}
        {error && (
          <div style={{ padding: "10px 12px" }}>
            <div style={{ color: J.err, fontSize: 10, marginBottom: 4 }}>✗ {error}</div>
            <div style={{ color: J.textDim, fontSize: 9 }}>Workspace: /tmp/{userId}/{currentProject}/workspace</div>
          </div>
        )}
        {!loading && !error && entries.length === 0 && (
          <div style={{ padding: "12px", color: J.textDim, fontSize: 10, lineHeight: 1.8 }}>
            Workspace empty.<br/>
            <span style={{ color: J.accent, fontSize: 9 }}>Agent-created files appear here.</span>
          </div>
        )}
        {!loading && Object.entries(byDir).map(([dir, files]) => (
          <div key={dir}>
            {dir !== "" && (
              <div onClick={() => setExpandedDirs(prev => { const n = new Set(prev); n.has(dir) ? n.delete(dir) : n.add(dir); return n; })}
                style={{ padding: "3px 10px", cursor: "pointer", display: "flex", alignItems: "center", gap: 5, background: J.bgCard, borderBottom: `1px solid ${J.border}`, borderTop: `1px solid ${J.border}`, userSelect: "none" }}>
                <span style={{ color: J.accentDim, fontSize: 9 }}>{expandedDirs.has(dir) ? "▾" : "▸"}</span>
                <span style={{ color: J.textSec, fontSize: 9, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{dir}</span>
                <span style={{ color: J.textDim, fontSize: 8, marginLeft: "auto" }}>{files.length}</span>
              </div>
            )}
            {(dir === "" || expandedDirs.has(dir)) && files.map(e => {
              const isChk = checked.has(e.path);
              return (
                <div key={e.path} onClick={() => e.is_text && toggle(e.path)}
                  title={`${e.path} \u00b7 ${fmtSize(e.size)}${!e.is_text ? " (binary)" : ""}`}
                  style={{
                    padding: "5px 10px 5px 12px", display: "flex", alignItems: "center", gap: 6,
                    cursor: e.is_text ? "pointer" : "default",
                    background: isChk ? `${J.accent}0A` : "transparent",
                    borderLeft: `2px solid ${isChk ? J.accent : "transparent"}`,
                    borderBottom: `1px solid ${J.border}18`,
                    transition: "all 0.1s",
                  }}>
                  <input type="checkbox" checked={isChk} disabled={!e.is_text}
                    onChange={() => e.is_text && toggle(e.path)}
                    onClick={ev => ev.stopPropagation()}
                    style={{ accentColor: J.accent, width: 11, height: 11, flexShrink: 0, cursor: e.is_text ? "pointer" : "not-allowed", opacity: e.is_text ? 1 : 0.3 }} />
                  <span style={{ color: isChk ? J.accent : J.textDim, fontSize: 10, flexShrink: 0 }}>{fileIcon(e.name)}</span>
                  <span style={{ flex: 1, color: isChk ? J.textPri : (e.is_text ? J.textSec : J.textDim), fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {e.name}
                  </span>
                  <span style={{ color: J.textDim, fontSize: 9, flexShrink: 0 }}>{fmtSize(e.size)}</span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
};


// ─── Persona Picker ─────────────────────────────────────────────────────────
// Dropdown to switch active persona. Changes the page theme (J palette) AND
// is sent to the backend with every message so prompt_builder.build_system_prompt
// selects the matching soul block for the LLM system prompt.
const PersonaPicker = ({ persona, onChange, personas }: {
  persona: string;
  onChange: (id: string) => void;
  personas: PersonaData[];
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = personas.find(p => p.id === persona) || personas[0] || { id:"jarvis", name:"J.A.R.V.I.S.", tagline:"", icon:"◈", theme:{accent:J.accent}, builtin:true };

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button onClick={() => setOpen(o => !o)} title={current.tagline} style={{
        padding: "3px 10px", borderRadius: 2, fontSize: 10,
        background: open ? `${current.color}12` : "transparent",
        border: `1px solid ${open ? current.color : J.border}`,
        color: current.theme?.accent || J.accent, letterSpacing: "0.05em",
        display: "flex", alignItems: "center", gap: 5,
        fontFamily: J.fontHeader, fontWeight: 600,
        transition: "all 0.2s",
      }}>
        <span>{current.icon}</span>
        <span>{persona.toUpperCase()}</span>
        <span style={{ fontSize: 8 }}>▾</span>
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "110%", left: 0, zIndex: 200,
          background: J.bgPanel, border: `1px solid ${J.borderMid}`,
          borderRadius: 5, padding: 6, width: 220,
          boxShadow: "0 12px 40px #000C",
        }}>
          {personas.map((p) => (
            <button key={p.id} onClick={() => { onChange(p.id); setOpen(false); }} style={{
              width: "100%", padding: "8px 10px", borderRadius: 3, marginBottom: 2,
              background: persona === p.id ? `${p.theme?.accent || J.accent}12` : "transparent",
              border: `1px solid ${persona === p.id ? (p.theme?.accent || J.accent) : "transparent"}`,
              color: persona === p.id ? (p.theme?.accent || J.accent) : J.textSec,
              textAlign: "left", display: "flex", flexDirection: "column", gap: 2,
              fontFamily: J.fontMono,
            }}
              onMouseEnter={e => { if (persona !== id) (e.currentTarget as HTMLButtonElement).style.background = J.bgCard; }}
              onMouseLeave={e => { if (persona !== id) (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: J.fontHeader, fontWeight: 700 }}>
                <span style={{ fontSize: 13 }}>{p.theme?.glyph || p.icon || "◈"}</span>{p.name}
                {persona === p.id && <span style={{ marginLeft: "auto", fontSize: 9 }}>✓ ACTIVE</span>}
              </span>
              <span style={{ fontSize: 9, color: J.textDim, lineHeight: 1.5 }}>{p.tagline}</span>
            </button>
          ))}
          <div style={{ padding: "6px 10px 2px", color: J.textDim, fontSize: 8, letterSpacing: "0.08em", borderTop: `1px solid ${J.border}`, marginTop: 4 }}>
            Changes theme + LLM system prompt persona
          </div>
        </div>
      )}
    </div>
  );
};


// ═══════════════════════════════════════════════════════════════════════════════
// PERSONA MANAGER PANEL — full CRUD + LLM generation
// Backend: GET/POST/PUT/DELETE /api/personas  POST /api/personas/generate
// ═══════════════════════════════════════════════════════════════════════════════
const DEFAULT_THEME: Partial<Theme> = {
  accent:"#00C8FF",accentDim:"#006A88",accentGlow:"#00C8FF18",accentGlow2:"#00C8FF40",
  bg:"#030609",bgDeep:"#010305",bgPanel:"#060C14",bgCard:"#08101A",bgCardHover:"#0C1520",
  warm:"#FF6B35",warmDim:"#3A1A0A",gold:"#FFB830",goldDim:"#3A2A00",
  textPri:"#B8D8F0",textSec:"#3A6A8A",textDim:"#1A3A50",
  border:"#0C1E2E",borderMid:"#1A3A55",borderHi:"#00C8FF44",
  ok:"#00FF88",okDim:"#003322",err:"#FF4455",errDim:"#2A0008",
  warn:"#FFB830",warnDim:"#2A1E00",react:"#7B68EE",reactDim:"#1A1640",
  fontImport:"@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap');",
  fontMono:"'Share Tech Mono', monospace",fontHeader:"'Rajdhani', monospace",
  glyph:"◈",wordmark:"NEW PERSONA",subtitle:"CUSTOM PERSONA",
  tagline:"DEFINE YOUR SPECIALIST",scanline:"#00C8FF22",
};

function blankPersona(): Partial<PersonaData> {
  return {
    id:"", name:"", tagline:"", soul:"", directives:"",
    skills:["filesystem","os_execution","memory_manager"],
    theme:{ ...DEFAULT_THEME }, builtin:false,
  };
}

const ThemeColorRow = ({ label, field, theme, setTheme }: {
  label: string; field: string; theme: any; setTheme: (t: any) => void;
}) => (
  <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:4 }}>
    <span style={{ color:J.textDim, fontSize:9, width:80, flexShrink:0 }}>{label}</span>
    <input type="color" value={theme[field] || "#000000"}
      onChange={e => setTheme((t: any) => ({ ...t, [field]: e.target.value }))}
      style={{ width:28, height:20, borderRadius:2, border:`1px solid ${J.border}`, cursor:"pointer", background:"none" }} />
    <input value={theme[field] || ""} onChange={e => setTheme((t: any) => ({ ...t, [field]: e.target.value }))}
      style={{ flex:1, padding:"2px 6px", background:J.bgCard, border:`1px solid ${J.border}`, color:J.textPri, fontSize:10, borderRadius:2 }} />
  </div>
);

const PersonaManager = ({
  onClose, userId, onPersonasChanged,
}: {
  onClose: () => void;
  userId: string;
  onPersonasChanged: (list: PersonaData[]) => void;
}) => {
  const [personas,    setPersonas]    = useState<PersonaData[]>([]);
  const [loading,     setLoading]     = useState(true);
  const [selected,    setSelected]    = useState<PersonaData | null>(null);
  const [editData,    setEditData]    = useState<any>(null);
  const [tab,         setTab]         = useState<"soul"|"directives"|"skills"|"theme">("soul");
  const [saving,      setSaving]      = useState(false);
  const [saved,       setSaved]       = useState(false);
  const [deleting,    setDeleting]    = useState(false);
  const [err,         setErr]         = useState("");
  const [hint,        setHint]        = useState("");
  const [generating,  setGenerating]  = useState(false);
  const [isNew,       setIsNew]       = useState(false);

  const ALL_SKILLS = ["filesystem","os_execution","cbd_architect","file_streamer",
                      "memory_manager","jarvis_mkii"];

  const load = async () => {
    setLoading(true);
    try {
      const r = await api(`${API_URL}/personas`);
      const d = await r.json();
      const list = d.personas || [];
      setPersonas(list);
      onPersonasChanged(list);
    } catch (e: any) { setErr(e.message); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const selectPersona = (p: PersonaData) => {
    setSelected(p); setIsNew(false); setErr(""); setSaved(false);
    // Load full detail
    api(`${API_URL}/personas/${p.id}`).then(r => r.json()).then(d => {
      setEditData({ ...d, theme: { ...DEFAULT_THEME, ...d.theme } });
    }).catch(() => setEditData({ ...p, theme: { ...DEFAULT_THEME, ...p.theme } }));
  };

  const newPersona = () => {
    const blank = blankPersona();
    setSelected(null); setIsNew(true); setEditData(blank); setErr(""); setSaved(false); setTab("soul");
  };

  const generate = async () => {
    if (!hint.trim()) return;
    setGenerating(true); setErr("");
    try {
      const r = await api(`${API_URL}/personas/generate`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ hint: hint.trim(), user_id: userId }),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "Generation failed"); }
      const d = await r.json();
      if (d.persona) {
        setEditData({ ...d.persona, theme: { ...DEFAULT_THEME, ...d.persona.theme } });
        setIsNew(true); setSelected(null); setHint(""); setTab("soul");
      }
    } catch (e: any) { setErr(`Generation failed: ${e.message}`); }
    setGenerating(false);
  };

  const save = async () => {
    if (!editData?.name?.trim()) { setErr("Name is required"); return; }
    if (!editData?.id?.trim())   { setErr("ID is required (slug, no spaces)"); return; }
    setSaving(true); setErr(""); setSaved(false);
    try {
      const method = isNew ? "POST" : "PUT";
      const url    = isNew ? `${API_URL}/personas` : `${API_URL}/personas/${editData.id}`;
      const r = await api(url, {
        method, headers:{"Content-Type":"application/json"},
        body: JSON.stringify(editData),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || `HTTP ${r.status}`); }
      setSaved(true); setTimeout(() => setSaved(false), 2000);
      await load();
      if (isNew) setIsNew(false);
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };

  const del = async () => {
    if (!editData || editData.builtin) return;
    if (!window.confirm(`Delete persona "${editData.name}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      const r = await api(`${API_URL}/personas/${editData.id}`, { method:"DELETE" });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
      setSelected(null); setEditData(null); setIsNew(false);
      await load();
    } catch (e: any) { setErr(e.message); }
    setDeleting(false);
  };

  const setField = (field: string, val: any) =>
    setEditData((d: any) => ({ ...d, [field]: val }));
  const setThemeField = (field: string, val: any) =>
    setEditData((d: any) => ({ ...d, theme: { ...(d.theme||{}), [field]: val } }));

  const toggleSkill = (sk: string) =>
    setEditData((d: any) => ({
      ...d, skills: d.skills?.includes(sk)
        ? d.skills.filter((s: string) => s !== sk)
        : [...(d.skills||[]), sk],
    }));

  const TAB_BTN = (id: typeof tab, label: string) => (
    <button onClick={() => setTab(id)} style={{
      padding:"4px 12px", fontSize:9, fontFamily:J.fontHeader, fontWeight:600,
      background: tab===id ? J.bgCard : "transparent",
      border:`1px solid ${tab===id ? J.accent+"55" : J.border}`,
      borderBottom: tab===id ? `1px solid ${J.bgCard}` : `1px solid ${J.border}`,
      borderRadius:"3px 3px 0 0", color: tab===id ? J.accent : J.textSec,
    }}>{label}</button>
  );

  const INP: React.CSSProperties = { width:"100%", padding:"7px 10px", borderRadius:3,
    background:J.bgCard, border:`1px solid ${J.borderMid}`, color:J.textPri, fontSize:12 };
  const TA: React.CSSProperties = { ...INP, resize:"vertical" as const };

  return (
    <div style={{ position:"fixed", inset:0, background:`${J.bgDeep}F4`, zIndex:100,
      display:"flex", flexDirection:"column", fontFamily:J.fontMono }}>
      {/* Header */}
      <div style={{ padding:"12px 24px", borderBottom:`1px solid ${J.borderMid}`,
        background:J.bgPanel, display:"flex", alignItems:"center",
        justifyContent:"space-between", flexShrink:0 }}>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <div style={{ width:22, height:22, borderRadius:"50%", background:J.bgCard,
            border:`1px solid ${J.accent}55`, display:"flex", alignItems:"center",
            justifyContent:"center", color:J.accent, fontSize:12 }}>◈</div>
          <div>
            <div style={{ color:J.accent, fontSize:11, letterSpacing:"0.18em",
              fontFamily:J.fontHeader, fontWeight:700 }}>PERSONA MANAGER</div>
            <div style={{ color:J.textDim, fontSize:8, letterSpacing:"0.1em" }}>
              ADD · EDIT · DELETE PERSONAS · LLM-GENERATED SUGGESTIONS
            </div>
          </div>
        </div>
        <button onClick={onClose} style={{ background:"none", border:`1px solid ${J.borderMid}`,
          color:J.textSec, padding:"4px 14px", borderRadius:2, fontSize:11 }}>✕</button>
      </div>

      <div style={{ flex:1, display:"grid", gridTemplateColumns:"260px 1fr", overflow:"hidden" }}>

        {/* ── Left: persona list ── */}
        <div style={{ borderRight:`1px solid ${J.border}`, display:"flex",
          flexDirection:"column", overflow:"hidden" }}>

          {/* LLM Generate hint */}
          <div style={{ padding:"12px 14px", borderBottom:`1px solid ${J.border}`,
            background:J.bgDeep, flexShrink:0 }}>
            <Lbl c={J.react}>⚡ AI GENERATE PERSONA</Lbl>
            <div style={{ display:"flex", gap:6, marginTop:6 }}>
              <input value={hint} onChange={e => setHint(e.target.value)}
                onKeyDown={e => e.key==="Enter" && generate()}
                placeholder='e.g. "Cyber War King" or "DevOps Expert"'
                style={{ flex:1, padding:"5px 8px", background:J.bgCard,
                  border:`1px solid ${J.borderMid}`, color:J.textPri,
                  fontSize:10, borderRadius:3 }} />
              <button onClick={generate} disabled={generating || !hint.trim()} style={{
                padding:"5px 10px", background:`${J.react}0C`,
                border:`1px solid ${J.react}55`, color:J.react,
                borderRadius:3, fontSize:10, flexShrink:0,
              }}>{generating ? "…" : "⚡"}</button>
            </div>
            <div style={{ color:J.textDim, fontSize:8, marginTop:4 }}>
              LLM generates soul, directives, theme colors and skills
            </div>
          </div>

          {/* New persona button */}
          <div style={{ padding:"8px 14px", borderBottom:`1px solid ${J.border}`, flexShrink:0 }}>
            <button onClick={newPersona} style={{ width:"100%", padding:"6px",
              background:`${J.ok}0C`, border:`1px solid ${J.ok}44`, color:J.ok,
              borderRadius:3, fontSize:10, letterSpacing:"0.08em" }}>⊞ NEW PERSONA</button>
          </div>

          {/* Persona list */}
          <div style={{ flex:1, overflowY:"auto" }}>
            {loading && <div style={{ padding:16, color:J.textSec, fontSize:11,
              animation:"hud-pulse 1.5s infinite" }}>Loading personas…</div>}
            {personas.map(p => {
              const color = (p.theme as any)?.accent || J.accent;
              const isActive = (isNew ? false : selected?.id === p.id) ||
                               (!isNew && editData?.id === p.id);
              return (
                <div key={p.id} onClick={() => selectPersona(p)} style={{
                  padding:"10px 14px", cursor:"pointer", display:"flex",
                  alignItems:"center", gap:8, transition:"all 0.1s",
                  background: isActive ? `${color}0A` : "transparent",
                  borderLeft:`3px solid ${isActive ? color : "transparent"}`,
                  borderBottom:`1px solid ${J.border}`,
                }}>
                  <div style={{ width:22, height:22, borderRadius:"50%", flexShrink:0,
                    background:`${color}18`, border:`1px solid ${color}44`,
                    display:"flex", alignItems:"center", justifyContent:"center",
                    fontSize:11, color }}>
                    {(p.theme as any)?.glyph || p.icon || "◈"}
                  </div>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{ color: isActive ? J.textPri : J.textSec, fontSize:11,
                      overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                      {p.name}
                      {p.builtin && <span style={{ color:J.gold, fontSize:8, marginLeft:6 }}>BUILT-IN</span>}
                    </div>
                    <div style={{ color:J.textDim, fontSize:9, marginTop:1,
                      overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                      {p.tagline}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ── Right: editor ── */}
        <div style={{ display:"flex", flexDirection:"column", overflow:"hidden" }}>
          {!editData ? (
            <div style={{ flex:1, display:"flex", alignItems:"center", justifyContent:"center",
              flexDirection:"column", gap:12, color:J.textDim }}>
              <div style={{ fontSize:32, opacity:0.3 }}>◈</div>
              <div style={{ fontSize:11 }}>Select a persona to edit, or click ⊞ NEW PERSONA</div>
              <div style={{ fontSize:10, color:`${J.accent}55` }}>
                Use ⚡ AI GENERATE to create a persona from a hint
              </div>
            </div>
          ) : (
            <>
              {/* Editor header */}
              <div style={{ padding:"12px 20px", borderBottom:`1px solid ${J.border}`,
                flexShrink:0, display:"flex", alignItems:"center", justifyContent:"space-between" }}>
                <div style={{ flex:1, display:"grid", gridTemplateColumns:"1fr 1fr", gap:10 }}>
                  <div>
                    <Lbl>Display Name *</Lbl>
                    <input value={editData.name||""} onChange={e => setField("name",e.target.value)}
                      placeholder="e.g. The Cyber King" style={{ ...INP, marginTop:4 }} />
                  </div>
                  <div>
                    <Lbl>ID * <span style={{ color:J.textDim }}>(slug, no spaces)</span></Lbl>
                    <input value={editData.id||""} onChange={e => setField("id",e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g,"_"))}
                      placeholder="e.g. cyber_king"
                      disabled={!isNew && editData.id}
                      style={{ ...INP, marginTop:4, opacity: (!isNew && editData.id) ? 0.6 : 1 }} />
                  </div>
                  <div style={{ gridColumn:"1/-1" }}>
                    <Lbl>Tagline</Lbl>
                    <input value={editData.tagline||""} onChange={e => setField("tagline",e.target.value)}
                      placeholder="One-line description shown in picker" style={{ ...INP, marginTop:4 }} />
                  </div>
                </div>
                <div style={{ display:"flex", gap:6, marginLeft:16, flexShrink:0 }}>
                  {!editData.builtin && editData.id && !isNew && (
                    <button onClick={del} disabled={deleting} style={{ padding:"6px 12px",
                      background:J.errDim, border:`1px solid ${J.err}44`,
                      color:"#FF9999", borderRadius:3, fontSize:10 }}>
                      {deleting ? "…" : "⊗ DELETE"}
                    </button>
                  )}
                  <button onClick={save} disabled={saving} style={{ padding:"6px 18px",
                    background: saved ? J.okDim : `${J.accent}0C`,
                    border:`1px solid ${saved ? J.ok : J.accent}`,
                    color: saved ? J.ok : J.accent, borderRadius:3, fontSize:10,
                    letterSpacing:"0.08em" }}>
                    {saving ? "SAVING…" : saved ? "✓ SAVED" : (isNew ? "⊞ CREATE" : "✓ SAVE")}
                  </button>
                </div>
              </div>

              {err && <div style={{ margin:"0 20px", padding:"6px 10px", background:J.errDim,
                border:`1px solid ${J.err}33`, color:J.err, fontSize:10, borderRadius:3 }}>⚠ {err}</div>}

              {/* Tab bar */}
              <div style={{ padding:"8px 20px 0", display:"flex", gap:2, flexShrink:0,
                borderBottom:`1px solid ${J.border}` }}>
                {TAB_BTN("soul",       "◈ SOUL")}
                {TAB_BTN("directives", "⬡ DIRECTIVES")}
                {TAB_BTN("skills",     "⚙ SKILLS")}
                {TAB_BTN("theme",      "◉ THEME")}
              </div>

              {/* Tab body */}
              <div style={{ flex:1, overflowY:"auto", padding:"16px 20px" }}>

                {tab === "soul" && (
                  <div>
                    <div style={{ color:J.textSec, fontSize:10, marginBottom:8, lineHeight:1.7 }}>
                      The <span style={{ color:J.accent }}>soul</span> defines this persona's identity, voice, and character.
                      It is the first thing the LLM reads — make it vivid and opinionated.
                      {editData.builtin && <span style={{ color:J.gold }}> Built-in soul can be edited but not deleted.</span>}
                    </div>
                    <textarea value={editData.soul||""} onChange={e => setField("soul",e.target.value)}
                      rows={18} style={{ ...TA }} placeholder={"## Identity\n\nYou are...\n\nVoice: ...\nMission: ..."} />
                  </div>
                )}

                {tab === "directives" && (
                  <div>
                    <div style={{ color:J.textSec, fontSize:10, marginBottom:8, lineHeight:1.7 }}>
                      <span style={{ color:J.accent }}>Directives</span> are role-specific instructions —
                      expertise areas, tool preferences, workflow rules. These focus the persona on its specialty.
                    </div>
                    <textarea value={editData.directives||""} onChange={e => setField("directives",e.target.value)}
                      rows={18} style={{ ...TA }} placeholder={"## Directives\n\n- Expert in...\n- Always use X tool for Y\n- Output format: ..."} />
                  </div>
                )}

                {tab === "skills" && (
                  <div>
                    <div style={{ color:J.textSec, fontSize:10, marginBottom:12, lineHeight:1.7 }}>
                      Skills bound to this persona. The agent can only invoke skills in this list.
                    </div>
                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:8 }}>
                      {ALL_SKILLS.map(sk => {
                        const sm = SKILL_META[sk] || { border:J.borderMid, icon:"◈" };
                        const on = (editData.skills||[]).includes(sk);
                        return (
                          <div key={sk} onClick={() => toggleSkill(sk)} style={{
                            padding:"10px 14px", borderRadius:4, cursor:"pointer",
                            background: on ? `${sm.border}0C` : J.bgCard,
                            border:`1px solid ${on ? sm.border : J.border}`,
                            borderLeft:`3px solid ${on ? sm.border : J.border}`,
                            display:"flex", alignItems:"center", gap:8, transition:"all 0.15s",
                          }}>
                            <input type="checkbox" checked={on} readOnly
                              style={{ accentColor:sm.border, width:13, height:13, cursor:"pointer" }} />
                            <span style={{ color:sm.border, fontSize:13 }}>{sm.icon}</span>
                            <span style={{ color: on ? J.textPri : J.textSec, fontSize:11 }}>{sk}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {tab === "theme" && (
                  <div>
                    <div style={{ color:J.textSec, fontSize:10, marginBottom:12, lineHeight:1.7 }}>
                      <span style={{ color:J.accent }}>Theme</span> controls how the UI looks for this persona.
                      Colors, fonts, glyph, wordmark.
                    </div>
                    {/* Preview strip */}
                    <div style={{
                      marginBottom:16, padding:"12px 16px", borderRadius:4,
                      background: editData.theme?.bg || J.bg,
                      border:`2px solid ${editData.theme?.accent || J.accent}44`,
                      display:"flex", alignItems:"center", gap:12,
                    }}>
                      <div style={{
                        width:36, height:36, borderRadius:"50%",
                        background:`${editData.theme?.accent || J.accent}18`,
                        border:`1px solid ${editData.theme?.accent || J.accent}66`,
                        display:"flex", alignItems:"center", justifyContent:"center",
                        color:editData.theme?.accent || J.accent, fontSize:18,
                      }}>{editData.theme?.glyph || "◈"}</div>
                      <div>
                        <div style={{ color:editData.theme?.accent || J.accent, fontSize:13,
                          fontFamily:editData.theme?.fontHeader || J.fontHeader, fontWeight:700 }}>
                          {editData.theme?.wordmark || editData.name || "PERSONA"}
                        </div>
                        <div style={{ color:editData.theme?.textDim || J.textDim, fontSize:9 }}>
                          {editData.theme?.subtitle || "SUBTITLE"}
                        </div>
                      </div>
                      <div style={{ marginLeft:"auto", color:editData.theme?.textSec || J.textSec, fontSize:9 }}>
                        PREVIEW
                      </div>
                    </div>

                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"0 20px" }}>
                      {/* Identity */}
                      <div>
                        <Lbl c={J.accent}>IDENTITY</Lbl>
                        <div style={{ marginTop:8 }}>
                          {["glyph","wordmark","subtitle","tagline"].map(f => (
                            <div key={f} style={{ marginBottom:6 }}>
                              <Lbl>{f}</Lbl>
                              <input value={(editData.theme||{})[f]||""}
                                onChange={e => setThemeField(f,e.target.value)}
                                style={{ ...INP, marginTop:3, fontSize:11 }} />
                            </div>
                          ))}
                          <div style={{ marginBottom:6 }}>
                            <Lbl>fontHeader (CSS font stack)</Lbl>
                            <input value={(editData.theme||{}).fontHeader||""}
                              onChange={e => setThemeField("fontHeader",e.target.value)}
                              placeholder="'Rajdhani', monospace"
                              style={{ ...INP, marginTop:3, fontSize:11 }} />
                          </div>
                          <div style={{ marginBottom:6 }}>
                            <Lbl>fontImport (@import url(...))</Lbl>
                            <input value={(editData.theme||{}).fontImport||""}
                              onChange={e => setThemeField("fontImport",e.target.value)}
                              placeholder="@import url('https://fonts.googleapis.com/...')"
                              style={{ ...INP, marginTop:3, fontSize:10 }} />
                          </div>
                        </div>
                      </div>
                      {/* Colors */}
                      <div>
                        <Lbl c={J.accent}>COLORS</Lbl>
                        <div style={{ marginTop:8 }}>
                          {["accent","accentDim","bg","bgDeep","bgPanel","bgCard",
                            "textPri","textSec","textDim","border","borderMid",
                            "ok","okDim","err","errDim","warn","react","gold","warm","scanline"
                          ].map(f => (
                            <ThemeColorRow key={f} label={f} field={f}
                              theme={editData.theme||{}} setTheme={(fn: any) => {
                                const t = fn(editData.theme||{});
                                setField("theme",t);
                              }} />
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// ─── Login Screen ─────────────────────────────────────────────────────────────
const Login = ({ onAuth, personas }: { onAuth: (u: string, persona: string) => void; personas: PersonaData[] }) => {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr]   = useState("");
  const [loading, setLoading] = useState(false);
  const [shaking, setShaking] = useState(false);
  const [persona, setPersona] = useState<string>(() => loadPersona());

  const switchPersona = (id: string, themeData?: Partial<Theme>) => {
    applyTheme(id, themeData);
    savePersona(id);
    setPersona(id);
  };

  const [mode, setMode] = useState<"login"|"register">("login");
  const [regUser, setRegUser] = useState("");
  const [regPass, setRegPass] = useState("");
  const [regPass2, setRegPass2] = useState("");
  const [regLoading, setRegLoading] = useState(false);

  const go = async () => {
    const u = user.trim().toLowerCase();
    if (!u) { setErr("Operator ID is required."); return; }
    setLoading(true); setErr("");
    try {
      const hashedPass = await sha256hex(pass);
      const res = await fetch(`${API_URL}/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: hashedPass }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.access_token) {
        storeAuth(data.access_token, data.username || u);
        onAuth(data.username || u, persona);
      } else if (res.status === 401) {
        setErr(data.detail || "Invalid credentials. Default: admin / admin123");
        setShaking(true); setTimeout(() => setShaking(false), 500);
      } else if (res.status === 405 || res.status === 404) {
        storeAuth(`dev-${u}-${Date.now()}`, u);
        onAuth(u, persona);
      } else {
        setErr(data.detail || `Server error ${res.status}`);
        setShaking(true); setTimeout(() => setShaking(false), 500);
      }
    } catch {
      storeAuth(`offline-${u}-${Date.now()}`, u);
      onAuth(u, persona);
    }
    setLoading(false);
  };

  const register = async () => {
    const u = regUser.trim().toLowerCase();
    if (!u || !regPass) { setErr("Username and password required."); return; }
    if (regPass !== regPass2) { setErr("Passwords do not match."); return; }
    if (regPass.length < 6) { setErr("Password must be at least 6 characters."); return; }
    setRegLoading(true); setErr("");
    try {
      const hashedRegPass = await sha256hex(regPass);
      const res = await fetch(`${API_URL}/auth/register`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: hashedRegPass }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setUser(u); setPass(regPass);
        setMode("login");
        setErr("");
        // auto-login after register
        const res2 = await fetch(`${API_URL}/auth/login`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: u, password: hashedRegPass }),
        });
        const data2 = await res2.json().catch(() => ({}));
        if (res2.ok && data2.access_token) {
          storeAuth(data2.access_token, data2.username || u);
          onAuth(data2.username || u, persona);
        }
      } else {
        setErr(data.detail || `Registration failed: ${res.status}`);
        setShaking(true); setTimeout(() => setShaking(false), 500);
      }
    } catch (e: any) { setErr(`Error: ${e.message}`); }
    setRegLoading(false);
  };

  const inp: React.CSSProperties = {
    width: "100%", padding: "9px 13px", borderRadius: 3,
    background: J.bgCard, border: `1px solid ${J.borderMid}`,
    color: J.textPri, fontSize: 13, boxSizing: "border-box",
  };

  return (
    <div style={{ minHeight: "100vh", background: J.bg, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <style>{buildGlobalCSS(persona)}</style>

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
            }}>{J.glyph}</div>
          </div>
          <div data-text={J.wordmark} className={persona !== "jarvis" ? "persona-glitch" : ""}
            style={{ color: J.accent, fontSize: 22, letterSpacing: "0.22em", fontFamily: J.fontHeader, fontWeight: 700 }}>{J.wordmark}</div>
          <div style={{ color: J.textDim, fontSize: 9, marginTop: 5, letterSpacing: "0.28em", fontFamily: J.fontHeader }}>{J.subtitle}</div>
          <Divider color={J.borderMid} />
          <div style={{ color: J.textDim, fontSize: 8, marginTop: 6, letterSpacing: "0.2em" }}>{J.tagline}</div>
        </div>

        {/* ── Persona Selector ── */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, justifyContent: "center", marginBottom: 20 }}>
          {(personas.length > 0 ? personas : _personaRegistry).map(p => {
            const color = (p.theme as any)?.accent || J.accent;
            const glyph = (p.theme as any)?.glyph || p.icon || "◈";
            return (
              <button key={p.id} onClick={() => switchPersona(p.id, p.theme)}
                title={p.tagline} style={{
                  flex: "0 1 calc(33% - 4px)", minWidth: 70,
                  padding: "7px 4px", borderRadius: 3,
                  background: persona === p.id ? `${color}15` : J.bgCard,
                  border: `1px solid ${persona === p.id ? color : J.border}`,
                  color: persona === p.id ? color : J.textDim,
                  fontSize: 9, letterSpacing: "0.06em", fontFamily: J.fontHeader, fontWeight: 600,
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
                }}>
                <span style={{ fontSize: 13 }}>{glyph}</span>
                <span style={{ overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", maxWidth:"100%" }}>{p.name}</span>
              </button>
            );
          })}
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

        {err && (
          <div style={{ color: J.err, fontSize: 11, marginBottom: 14, padding: "7px 12px", background: J.errDim, border: `1px solid ${J.err}33`, borderRadius: 2 }}>
            ✕ {err}
            {err.includes("admin123") && (
              <div style={{ color: J.textSec, fontSize: 10, marginTop: 6 }}>
                💡 Tip: First boot default is <span style={{ color: J.accent }}>admin</span> / <span style={{ color: J.accent }}>admin123</span>
                {" — or "}
                <button onClick={() => { setErr(""); setMode("register"); }} style={{ background: "none", border: "none", color: J.accent, cursor: "pointer", textDecoration: "underline", fontSize: 10 }}>create a new account</button>
              </div>
            )}
          </div>
        )}

        {mode === "login" ? (
          <>
            <button onClick={go} disabled={loading} style={{
              width: "100%", padding: "11px", borderRadius: 3,
              background: loading ? J.bgCard : `${J.accent}0C`,
              border: `1px solid ${loading ? J.borderMid : J.accent}`,
              color: loading ? J.textSec : J.accent,
              fontSize: 12, fontWeight: "bold", letterSpacing: "0.18em",
              transition: "all 0.2s", fontFamily: J.fontHeader,
            }}>{loading ? `${J.glyph} AUTHENTICATING…` : "▶ INITIALIZE SEQUENCE"}</button>
            <div style={{ textAlign: "center", marginTop: 12 }}>
              <button onClick={() => { setMode("register"); setErr(""); }} style={{
                background: "none", border: "none", color: J.textSec, cursor: "pointer", fontSize: 10,
              }}>No account? Register →</button>
            </div>
          </>
        ) : (
          <div>
            <div style={{ color: J.accent, fontSize: 10, letterSpacing: "0.12em", marginBottom: 12, fontFamily: J.fontHeader }}>◈ CREATE OPERATOR ACCOUNT</div>
            <div style={{ marginBottom: 10 }}>
              <Lbl>Username</Lbl>
              <input value={regUser} onChange={e => setRegUser(e.target.value)} style={{ ...inp, marginTop: 5 }} placeholder="Choose a username" />
            </div>
            <div style={{ marginBottom: 10 }}>
              <Lbl>Password</Lbl>
              <input type="password" value={regPass} onChange={e => setRegPass(e.target.value)} style={{ ...inp, marginTop: 5 }} placeholder="Min 6 characters" />
            </div>
            <div style={{ marginBottom: 14 }}>
              <Lbl>Confirm Password</Lbl>
              <input type="password" value={regPass2} onChange={e => setRegPass2(e.target.value)} onKeyDown={e => e.key === "Enter" && register()} style={{ ...inp, marginTop: 5 }} />
            </div>
            <button onClick={register} disabled={regLoading} style={{
              width: "100%", padding: "10px", borderRadius: 3,
              background: `${J.ok}0C`, border: `1px solid ${J.ok}`,
              color: J.ok, fontSize: 12, letterSpacing: "0.15em", fontFamily: J.fontHeader,
            }}>{regLoading ? "CREATING…" : "⊞ CREATE ACCOUNT"}</button>
            <div style={{ textAlign: "center", marginTop: 10 }}>
              <button onClick={() => { setMode("login"); setErr(""); }} style={{ background: "none", border: "none", color: J.textSec, cursor: "pointer", fontSize: 10 }}>← Back to login</button>
            </div>
          </div>
        )}

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
  const [experiencedOpen, setExperiencedOpen] = useState(false);
  const [evolutionOpen,   setEvolutionOpen]   = useState(false);
  const [telemetryOpen,   setTelemetryOpen]   = useState(false);
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  const [tokenLog,        setTokenLog]        = useState<TokenSnapshot[]>([]);
  const [commandLog,      setCommandLog]      = useState<CommandEntry[]>([]);
  const cmdCounterRef = useRef(0);
  // Instructions
  const [instructions,    setInstructions]    = useState<Instruction[]>(() => loadInstructions());
  // Memory toggle — when false, memory is NOT sent to LLM
  const [memoryEnabled,   setMemoryEnabled]   = useState(true);
  // HALT state
  const [halted,          setHalted]          = useState(false);
  const haltedRef = useRef(false);
  // Persona — affects theme + system prompt sent to LLM
  const [persona,         setPersonaState]    = useState<string>(() => loadPersona());
  const [personaList,     setPersonaList]     = useState<PersonaData[]>(_personaRegistry);
  const [personaMgrOpen,  setPersonaMgrOpen]  = useState(false);
  const personaRef = useRef(persona);
  const setPersona = useCallback((id: string, themeData?: Partial<Theme>) => {
    applyTheme(id, themeData);
    savePersona(id);
    personaRef.current = id;
    setPersonaState(id);
  }, []);
  const onPersonasChanged = useCallback((list: PersonaData[]) => {
    refreshPersonaRegistry(list);
    setPersonaList([...list]);
    // Re-apply current persona theme in case it was edited
    const cur = list.find(p => p.id === personaRef.current);
    if (cur?.theme) applyTheme(cur.id, cur.theme);
  }, []);
  // Workspace
  const [workspaceOpen,   setWorkspaceOpen]   = useState(true);   // sidebar visible by default
  const [workspaceFiles,  setWorkspaceFiles]  = useState<WsFile[]>([]);  // files checked in sidebar
  const [currentProject,  setCurrentProject]  = useState<string>("default");  // active project (/tmp/{user}/{project}/workspace)
  const currentProjectRef = useRef<string>("default");
  // Keep ref in sync so react loop (inside useCallback closure) always reads latest
  useEffect(() => { currentProjectRef.current = currentProject; }, [currentProject]);
  const [provInfo,        setProvInfo]        = useState({ provider: "deepseek", model: "deepseek-coder", schema_format: "openai" });
  const provInfoRef = useRef({ provider: "deepseek", model: "deepseek-coder" });
  // Keep provInfoRef in sync
  // (updated in useEffect below)
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

  // Auto-signout on 401 (token expired / revoked)
  useEffect(() => {
    const handler = () => { setAuthedUser(null); };
    window.addEventListener("jarvis:signout", handler);
    return () => window.removeEventListener("jarvis:signout", handler);
  }, []);

  // Re-apply theme whenever persona changes (covers cold-start and picker changes)
  useEffect(() => {
    if (_personaThemeCache[persona]) {
      applyTheme(persona);
    }
  }, [persona]);

  // Sync provInfoRef whenever provInfo changes
  useEffect(() => { provInfoRef.current = { provider: provInfo.provider, model: provInfo.model }; }, [provInfo]);
  // Sync personaRef whenever persona changes
  useEffect(() => { personaRef.current = persona; }, [persona]);

  // Load personas from API after login — populates dynamic persona list + themes
  useEffect(() => {
    if (!authedUser) return;
    api(`${API_URL}/personas`).then(r => r.json()).then(d => {
      const list = d.personas || [];
      refreshPersonaRegistry(list);
      setPersonaList(list);
      // Apply saved persona theme now that themes are loaded
      const saved = loadPersona();
      const cur   = list.find((p: any) => p.id === saved);
      if (cur?.theme) {
        applyTheme(saved, cur.theme);
        setPersonaState(saved);
      }
    }).catch(() => {});
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
        // Record token snapshot for telemetry
        if (data) {
          setTokenLog(tl => {
            const last2 = tl[tl.length - 1];
            if (last2?.role === "assistant" && Date.now() - new Date(last2.ts).getTime() < 30000) {
              // Accumulate into current snapshot
              return [...tl.slice(0,-1), { ...last2, chars: last2.chars + data.length, est_tokens: estTokens(last2.chars + data.length) }];
            }
            return [...tl, { ts: tsNow(), role: "assistant", chars: data.length, est_tokens: estTokens(data.length), model: provInfoRef.current.model, provider: provInfoRef.current.provider }];
          });
        }
        return [...prev, { role: "assistant", content: data || "", streaming: true }];
      });
    } else if (type === "halted") {
      // Server confirmed HALT — already handled client-side but clear streaming flag
      setStreaming(false);
    } else if (type === "done") {
      setStreaming(false);
      setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
    } else if (type === "tool_call") {
      setMessages(prev => [...prev, { role: "tool_call", ...data }]);
    } else if (type === "tool_result") {
      setMessages(prev => [...prev, { role: "tool_result", data }]);
      // Telemetry: backend now embeds skill/action/params in every tool_result event
      // so we read them directly — no cross-event stash needed
      const skill  = data?.skill  || "";
      const action = data?.action || "";
      const params = data?.params || {};
      const output = data?.output || {};
      if (skill) {
        cmdCounterRef.current += 1;
        const entry: CommandEntry = {
          id:          String(cmdCounterRef.current),
          ts:          tsNow(),
          skill,
          action,
          params,
          success:     data?.success    ?? true,
          duration_ms: output?.duration_ms ?? null,
          returncode:  output?.returncode  ?? null,
          stdout_len:  output?.stdout?.length || 0,
          stderr_len:  output?.stderr?.length || 0,
          timed_out:   output?.timed_out      ?? false,
          is_kali:     isKaliSkill(skill),
          command:     output?.command || params?.command || "",
          stdout:      output?.stdout  || "",
          stderr:      output?.stderr  || "",
        };
        setCommandLog(cl => [...cl, entry]);
      }
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
    // Record user token snapshot
    setTokenLog(tl => [...tl, { ts: tsNow(), role: "user", chars: msg.length, est_tokens: estTokens(msg.length), model: provInfo.model, provider: provInfo.provider }]);

    // Merge workspace files with manually attached files
    const instrBlock = buildInstructionBlock(instructions);
    const wsAtts = workspaceFiles
      .filter(f => f.content && !f.error)
      .map(f => ({
        name: f.path,
        mime: f.is_text ? "text/plain" : "application/octet-stream",
        size: f.size,
        ...(f.is_text ? { text: f.content } : { b64: f.content }),
      }));
    const allAtts = [...toSend, ...wsAtts];

    wsRef.current.send(JSON.stringify({
      message:        msg,
      react:          reactMode,
      auto_confirm:   autoConfirm,
      memory_enabled: memoryEnabled,
      persona:        personaRef.current,
      project:        currentProject,
      instructions:   instrBlock || undefined,
      attachments:    allAtts.length ? allAtts : undefined,
    }));
  }, [input, streaming, attachments, reactMode, autoConfirm, memoryEnabled, instructions, currentProject]);

  // ── Manual continue (for non-react mode only) ─────────────────────────────
  const sendContinue = useCallback(() => { if (!streaming) send("continue"); }, [send, streaming]);

  // ── Client-side ReAct loop (when backend react=false, drives iterations) ──
  // When reactMode=true, we pass react:true to backend and it handles all iterations.
  // This client loop is a FALLBACK for backward compatibility.
  const startClientReact = useCallback(async (initMsg?: string) => {
    if (reactActiveRef.current) return;
    if (haltedRef.current) return;  // HALT: refuse to start new loop after halt
    reactActiveRef.current = true;
    reactIterRef.current = 0;
    const msg = (initMsg ?? input).trim();
    // Guard: do not start a client react loop with no message content
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
      // HALT check at top of every iteration
      if (haltedRef.current) {
        setMessages(prev => [...prev, { role: "react_status", iteration: reactIterRef.current, phase: "⛔ HALTED BY OPERATOR", healing: false }]);
        break;
      }
      reactIterRef.current++;
      const iter = reactIterRef.current;
      const healing = lastErr !== null;

      setMessages(prev => [...prev, { role: "react_status", iteration: iter, phase: healing ? "self-healing" : "reasoning", healing }]);

      const prompt = healing ? `Previous error: "${lastErr}". Diagnose, correct, retry.` : (iter === 1 ? msg : "continue");
      // Build active instruction block
      const instrBlock = buildInstructionBlock(instructions);

      await new Promise<void>(resolve => {
        let done = false;
        const finish = () => { if (!done) { done = true; resolve(); } };
        setStreaming(true);
        const wsAttsR = workspaceFiles
          .filter(f => f.content && !f.error)
          .map(f => ({
            name: f.path,
            mime: f.is_text ? "text/plain" : "application/octet-stream",
            size: f.size,
            ...(f.is_text ? { text: f.content } : { b64: f.content }),
          }));
        const reactAtts = iter === 1 ? [...toSend, ...wsAttsR] : [];
        wsRef.current!.send(JSON.stringify({
          message:        prompt,
          react:          true,
          auto_confirm:   autoConfRef.current,
          memory_enabled: memoryEnabled,
          persona:        personaRef.current,
          project:        currentProjectRef.current,
          instructions:   instrBlock || undefined,
          attachments:    reactAtts.length ? reactAtts : undefined,
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

  // HALT — hard stop everything, wipe session, prevent resume
  const handleHalt = useCallback(async () => {
    // 1. Signal the loop to stop immediately
    haltedRef.current = true;
    reactActiveRef.current = false;
    setStreaming(false);
    setHalted(true);

    // 2. Send HALT via WS (fast path, fires before REST)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ halt: true, message: "" }));
    }

    // 3. Also call REST halt endpoint to destroy server session
    if (authedUser) {
      await api(`${API_URL}/halt/${authedUser}`, { method: "POST" }).catch(() => {});
    }

    // 4. Clear all client state — conversation, pending confirms, attachments
    setMessages(prev => [...prev, {
      role: "assistant",
      content: "⛔ HARD STOP — All execution halted. Session destroyed. Memory cleared. Awaiting new directive.",
    }]);
    setPendingConfirms([]);
    setAttachments([]);

    // 5. Re-enable after brief lockout (prevents accidental immediate restart)
    setTimeout(() => { haltedRef.current = false; setHalted(false); }, 3000);
  }, [authedUser]);

  const canSend     = !streaming && (!!input.trim() || attachments.length > 0);
  const canContinue = !streaming && connected;
  const pColor      = PROVIDERS[provInfo.provider]?.color || J.accent;

  if (!authedUser) return <Login personas={personaList} onAuth={(u, p) => {
    setAuthedUser(u);
    // Apply the persona chosen on the Login screen immediately into App state
    if (p && _personaThemeCache[p]) {
      applyTheme(p);
      savePersona(p);
      setPersonaState(p);
      personaRef.current = p;
    }
  }} />;

  return (
    <div style={{ minHeight: "100vh", background: J.bg, display: "flex", flexDirection: "column" }}
      onDrop={onDrop} onDragOver={onDragOver}>
      <style>{buildGlobalCSS(persona)}</style>

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
              <div data-text={J.wordmark} className={persona !== "jarvis" ? "persona-glitch" : ""}
                style={{ color: J.accent, fontSize: 12, letterSpacing: "0.2em", fontFamily: J.fontHeader, fontWeight: 700, lineHeight: 1.2 }}>🧠 S.I.R</div>
              <div style={{ color: J.textDim, fontSize: 8, letterSpacing: "0.15em", fontFamily: J.fontHeader, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 180 }}>Super Intelligent Robot Platform</div>
            </div>
          </div>
          <Divider color={J.borderMid} />

          <PersonaPicker persona={persona} onChange={(id) => { const p = personaList.find(x=>x.id===id); setPersona(id, p?.theme); }} personas={personaList} />

          <Chip label={connected ? "● ONLINE" : "○ OFFLINE"} color={connected ? J.ok : J.err} pulse={connected} />
          <button onClick={() => setWorkspaceOpen(o => !o)} title="Toggle workspace file browser"
            style={{
              padding: "2px 8px", borderRadius: 2, fontSize: 10,
              background: workspaceOpen ? `${J.accent}12` : "transparent",
              border: `1px solid ${workspaceOpen ? J.accent : J.border}`,
              color: workspaceOpen ? J.accent : J.textDim,
              fontFamily: J.fontHeader, fontWeight: 600,
            }}>◫ {workspaceOpen ? "FILES ▸" : "FILES ◂"}
          </button>
          {tokenLog.length > 0 && (
            <button onClick={() => setTelemetryOpen(true)} title="Open telemetry panel" style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}>
              <Chip label={`⊕ ~${tokenLog.reduce((s,t)=>s+t.est_tokens,0).toLocaleString()} tok · ${commandLog.length} cmd`} color={J.accentDim} />
            </button>
          )}
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
            { label: "◉ MEMORY",    action: () => setMemoryOpen(true),      color: J.gold },
            { label: "⬢ EXPERIENCED", action: () => setExperiencedOpen(true), color: J.react },
            { label: "◈ EVOLVE",    action: () => setEvolutionOpen(true),   color: J.warm },
            { label: "⊕ TELEMETRY", action: () => setTelemetryOpen(true),   color: J.accent },
            { label: "◈ PERSONAS",  action: () => setPersonaMgrOpen(true),   color: J.react },
            { label: "⬡ INSTRUCT",  action: () => setInstructionsOpen(true), color: J.gold },
            { label: "⚙ CONFIG", action: () => setSettingsOpen(true), color: J.textSec },
            { label: "↺ RESET",  action: handleReset,                  color: J.err },
          ].map(b => (
            <button key={b.label} onClick={b.action} style={{
              padding: "4px 10px", background: "transparent",
              border: `1px solid ${J.border}`, color: J.textSec,
              borderRadius: 2, fontSize: 10, letterSpacing: "0.06em",
              transition: "border-color 0.2s, color 0.2s",
              fontFamily: J.fontHeader, fontWeight: 600,
            }}
              onMouseEnter={e => { (e.target as any).style.borderColor = b.color; (e.target as any).style.color = b.color; }}
              onMouseLeave={e => { (e.target as any).style.borderColor = J.border; (e.target as any).style.color = J.textSec; }}
            >{b.label}</button>
          ))}
          {/* ⛔ HALT — hard stop, always visible */}
          <button
            onClick={handleHalt}
            disabled={halted}
            title="HARD STOP — Halt all execution, destroy session, clear memory"
            style={{
              padding: "4px 14px", borderRadius: 2, fontSize: 10,
              background: halted ? J.errDim : `${J.err}15`,
              border: `2px solid ${halted ? J.err + "33" : J.err}`,
              color: halted ? J.err + "88" : J.err,
              letterSpacing: "0.1em", fontFamily: J.fontHeader, fontWeight: 700,
              animation: streaming && !halted ? "hud-pulse 1.5s infinite" : "none",
              boxShadow: streaming && !halted ? `0 0 8px ${J.err}33` : "none",
            }}>
            {halted ? "⛔ HALTED" : "⛔ HALT"}
          </button>

          <button onClick={() => { clearAuth(); setAuthedUser(null); }}
            title={`Session: ${authedUser}`}
            style={{ padding: "4px 10px", background: "transparent", border: `1px solid ${J.border}`, color: J.textSec, borderRadius: 2, fontSize: 10, letterSpacing: "0.06em", fontFamily: J.fontHeader, fontWeight: 600 }}>
            ⇤ {authedUser?.toUpperCase()}
          </button>
        </div>
      </div>

      {/* ── Main content area (message feed + optional workspace sidebar) ── */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden", minHeight: 0 }}>
      {/* ── Message feed ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px", maxWidth: workspaceOpen ? "none" : 940, width: "100%", margin: workspaceOpen ? "0" : "0 auto" }}>

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
            <div style={{ color: `${J.accent}77`, fontSize: 12, letterSpacing: "0.2em", marginBottom: 8, fontFamily: J.fontHeader, fontWeight: 600 }}>SYSTEM ONLINE — AWAITING DIRECTIVE</div>
            <div style={{ color: J.textDim, fontSize: 10, lineHeight: 2.2, letterSpacing: "0.08em", marginBottom: 28 }}>
              {provInfo.provider.toUpperCase()} / {provInfo.model} &nbsp;·&nbsp; {authedUser?.toUpperCase()} AUTHENTICATED
              <br />
              <span style={{ color: J.accent, opacity: 0.55 }}>
                ◫ PROJECT: {currentProject} &nbsp;·&nbsp; /tmp/{authedUser}/{currentProject}/workspace
              </span>
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
          <div style={{ color: J.accentDim, fontSize: 10, padding: "4px 42px", animation: "hud-pulse 1.2s infinite", letterSpacing: "0.12em", fontFamily: J.fontHeader }}>
            {reactMode && reactActiveRef.current
              ? `↺ REACT [${String(reactIterRef.current).padStart(2, "0")}] — PROCESSING…`
              : "◈  PROCESSING…"}
          </div>
        )}
        <div ref={bottomRef} />
      </div>{/* end message feed */}

      {/* ── Workspace sidebar ── */}
      {workspaceOpen && authedUser && (
        <div style={{ width: 260, flexShrink: 0, borderLeft: `1px solid ${J.border}`, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <WorkspaceSidebar
            userId={authedUser}
            onFilesSelected={setWorkspaceFiles}
            currentProject={currentProject}
            onProjectChange={setCurrentProject}
          />
        </div>
      )}
      </div>{/* end main content area */}

      {/* ── Input dock ── */}
      <div style={{
        borderTop: `1px solid ${J.border}`,
        background: `${J.bgPanel}E8`,
        backdropFilter: "blur(10px)",
        maxWidth: 940, width: "100%", margin: "0 auto",
        position: "sticky", bottom: 0,
      }}>
        {/* Staged attachments + workspace files */}
        {(attachments.length > 0 || workspaceFiles.length > 0) && (
          <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", borderBottom: `1px solid ${J.border}` }}>
            {attachments.length > 0 && <Lbl>◈ Attached:</Lbl>}
            {attachments.map((att: any, i: number) => (
              <AttBadge key={i} att={att} onRemove={() => setAttachments(prev => prev.filter((_: any, j: number) => j !== i))} />
            ))}
            {workspaceFiles.length > 0 && <Lbl c={J.accent}>◫ Workspace:</Lbl>}
            {workspaceFiles.filter(f => !f.error).map((f, i) => (
              <div key={i} style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 8px", borderRadius: 2,
                background: `${J.accent}0A`, border: `1px solid ${J.accent}22`,
                color: J.textSec, fontSize: 10,
              }}>
                <span style={{ color: J.accent }}>◫</span>
                <span style={{ color: J.textPri, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.path}>{f.name}</span>
                <span style={{ color: J.textDim }}>{fmtSize(f.size)}</span>
                {f.truncated && <span style={{ color: J.warn, fontSize: 8 }}>TRUNC</span>}
              </div>
            ))}
          </div>
        )}

        {/* Mode toggles */}
        <div style={{ padding: "8px 16px 0", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <Toggle on={autoConfirm}   set={setAutoConfirm}  color={J.warm}    label="⚡ Auto-confirm"
            title="Automatically approve destructive actions without prompting. Use with caution." />
          <Toggle on={reactMode}     set={setReactMode}    color={J.react}   label="↺ ReAct"
            title="Enable autonomous ReAct loop. AI will reason, act, observe and self-correct until task is done." />
          <Toggle on={memoryEnabled} set={setMemoryEnabled} color={J.gold}   label="◉ Memory"
            title="When OFF, no conversation history is sent to the LLM. Keeps context clean and prevents hallucination from old turns." />
          {/* Active instructions chips */}
          {instructions.filter(i => i.enabled).map(i => (
            <span key={i.id} onClick={() => setInstructionsOpen(true)} title={i.body} style={{
              padding: "3px 8px", borderRadius: 2, fontSize: 9, cursor: "pointer",
              background: `${J.gold}0A`, border: `1px solid ${J.gold}33`, color: J.gold,
              fontFamily: J.fontHeader, fontWeight: 600, letterSpacing: "0.06em",
              maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>⬡ {i.title}</span>
          ))}
          {!memoryEnabled && <span style={{ color: `${J.gold}55`, fontSize: 9, fontFamily: J.fontHeader }}>◉ Memory OFF — clean context</span>}
          {autoConfirm    && <span style={{ color: `${J.warm}55`, fontSize: 9, fontFamily: J.fontHeader }}>⚡ auto-confirm active</span>}
          {uploading      && <span style={{ color: `${J.accent}55`, fontSize: 9, animation: "hud-pulse 1s infinite" }}>⬆ reading files…</span>}
          {/* Active workspace badge — always visible so operator knows where agent writes */}
          <span
            onClick={() => setWorkspaceOpen(o => !o)}
            title={`/tmp/${authedUser}/${currentProject}/workspace — click to ${workspaceOpen ? "hide" : "show"} file browser`}
            style={{
              marginLeft: "auto", padding: "2px 8px", borderRadius: 2, cursor: "pointer",
              background: `${J.accent}08`, border: `1px solid ${J.accent}22`,
              color: J.accentDim, fontSize: 9, letterSpacing: "0.05em",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 280,
              fontFamily: J.fontMono,
            }}>
            ◫ /tmp/{authedUser}/{currentProject}/workspace
          </span>
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
                  : `Query ${J.wordmark} … (Shift+Enter for newline · drag & drop files to attach)`
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
                  fontFamily: J.fontHeader, fontWeight: 600,
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

          <div style={{ color: J.textDim, fontSize: 9, marginTop: 5, display: "flex", gap: 14, flexWrap: "wrap", letterSpacing: "0.06em", fontFamily: J.fontHeader }}>
            <span>Enter — send · Shift+Enter — newline · drag & drop to attach</span>
            {reactMode && <span style={{ color: `${J.react}55` }}>↺ REACT MODE — AI continues autonomously until TASK_COMPLETE</span>}
            {autoConfirm && <span style={{ color: `${J.warm}55` }}>⚡ AUTO-CONFIRM ACTIVE</span>}
          </div>
        </div>
      </div>

      {/* ── Overlays ── */}
      {settingsOpen    && <SettingsPanel    onClose={() => setSettingsOpen(false)}    onSaved={d => setProvInfo(prev => ({ ...prev, ...d }))} authedUser={authedUser} />}
      {memoryOpen      && <MemoryPanel      onClose={() => setMemoryOpen(false)}      userId={authedUser || "default"} />}
      {experiencedOpen && <ExperiencedPanel onClose={() => setExperiencedOpen(false)} userId={authedUser || "default"} />}
      {evolutionOpen   && <EvolutionPanel   onClose={() => setEvolutionOpen(false)}   userId={authedUser || "default"} />}
      {telemetryOpen   && (
        <TelemetryPanel
          onClose={() => setTelemetryOpen(false)}
          tokenLog={tokenLog}
          commandLog={commandLog}
          onClearTokens={() => setTokenLog([])}
          onClearCommands={() => setCommandLog([])}
        />
      )}
      {personaMgrOpen && authedUser && (
        <PersonaManager
          onClose={() => setPersonaMgrOpen(false)}
          userId={authedUser}
          onPersonasChanged={onPersonasChanged}
        />
      )}
      {instructionsOpen && (
        <InstructionsPanel
          instructions={instructions}
          setInstructions={setInstructions}
          onClose={() => setInstructionsOpen(false)}
        />
      )}
      {/* ⛔ HALTED overlay — brief 3s flash */}
      {halted && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 9000, pointerEvents: "none",
          border: `4px solid ${J.err}`,
          background: `${J.err}08`,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            padding: "20px 48px", background: J.bgPanel,
            border: `2px solid ${J.err}`, borderRadius: 6,
            color: J.err, fontSize: 24, letterSpacing: "0.25em",
            fontFamily: J.fontHeader, fontWeight: 700,
            animation: "hud-pulse 0.5s ease-in-out",
            boxShadow: `0 0 60px ${J.err}44`,
          }}>⛔ HARD STOP — EXECUTION HALTED</div>
        </div>
      )}
    </div>
  );
}
