import {
  Activity, AlertTriangle, Bot, Bug, Check, ChevronDown, CircleDot, Clock3,
  Database, Gauge, LoaderCircle, Network, Pause, Play, Radio, RotateCcw,
  Search, SkipForward, Sparkles, Square, UserRound, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Dict = Record<string, any>;
type Recording = { id: string; name: string; source_id: string; robot_type: string; frames: number; duration_s: number; expected_event?: string };
type Source = { id: string; name: string; robot_type: string; status: string; frame_count: number; human_count: number; controller_status: string; recording_run_id?: string; latest_cycle?: Cycle };
type Stage = { stage: string; status: string; duration_ms: number; payload?: Dict | null; error?: string | null };
type Change = { entity: string; field: string; from: unknown; to: unknown };
type Cycle = {
  sequence: number; observation_id: string; elapsed_s: number; status: string;
  duration_ms: number; observation: Dict; social_state?: Dict | null;
  scheduler?: { decision_triggered: boolean; triggers: string[] } | null;
  behavior_intent?: Dict | null; stages: Stage[]; changes: Change[];
  error?: string | null; ground_truth?: Dict | null;
};
type Run = {
  id: string; name: string; mode: string; recording_id: string; policy_name: string;
  policy_mode: string; status: string; current_index: number; frame_count: number;
  processed_count: number; duration_s: number; current_cycle?: Cycle | null; cycles: Cycle[];
};
type Bootstrap = {
  sources: Source[]; recordings: Recording[]; runs: Array<Partial<Run>>;
  health: { status: string; mode: string; actuation_enabled: boolean };
};

const stageLabels: Record<string, string> = {
  observation: "Observation", validation: "Validation", state: "Social state",
  scheduler: "Scheduler", policy: "Policy", intent: "Intent",
};

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) }, ...options,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status})`);
  return body;
};

const formatValue = (value: unknown) => {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return String(value);
};
const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));

export function App() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [liveSourceId, setLiveSourceId] = useState<string | null>(null);
  const [selectedSequence, setSelectedSequence] = useState<number | null>(null);
  const [selectedStage, setSelectedStage] = useState("scheduler");
  const [tab, setTab] = useState<"transition" | "payload" | "timing">("transition");
  const [speed, setSpeed] = useState(1);
  const [policyMode, setPolicyMode] = useState<"stub" | "current">("stub");
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInput.current?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  const loadBootstrap = useCallback(async () => {
    try {
      const data = await api<Bootstrap>("/api/v1/monitor/bootstrap");
      setBootstrap(data); setError(null); return data;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Monitor unavailable");
      return null;
    }
  }, []);

  const createRun = useCallback(async (recording: Recording, warmup = 1) => {
    setBusy(true); setPlaying(false); setLiveSourceId(null);
    try {
      const created = await api<Run>("/api/v1/monitor/replays", {
        method: "POST", body: JSON.stringify({ recording_id: recording.id, policy_mode: policyMode }),
      });
      const stepped = await api<Run>(`/api/v1/monitor/replays/${created.id}/step`, {
        method: "POST", body: JSON.stringify({ count: Math.min(warmup, created.frame_count) }),
      });
      setRun(stepped); setSelectedSequence(stepped.current_cycle?.sequence ?? null);
      setSelectedStage("scheduler"); setError(null); await loadBootstrap();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not create replay");
    } finally { setBusy(false); }
  }, [loadBootstrap, policyMode]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void (async () => {
      const data = await loadBootstrap();
      if (data?.recordings.length) {
        const preferred = data.recordings.find((item) => item.id.includes("newcomer_requests_guidance")) ?? data.recordings[0];
        await createRun(preferred, 24);
      }
    })();
  }, [createRun, loadBootstrap]);

  useEffect(() => {
    if (!liveSourceId || !bootstrap) return;
    const source = bootstrap.sources.find((item) => item.id === liveSourceId);
    if (!source?.latest_cycle) return;
    setRun({
      id: `live:${source.id}`, name: source.name, mode: "live", recording_id: source.id,
      policy_name: "Live shadow stream", policy_mode: "current", status: source.status,
      current_index: source.frame_count - 1, frame_count: source.frame_count,
      processed_count: source.frame_count, duration_s: 0, current_cycle: source.latest_cycle,
      cycles: [source.latest_cycle],
    });
    setSelectedSequence(source.latest_cycle.sequence);
  }, [bootstrap, liveSourceId]);

  useEffect(() => {
    const events = new EventSource("/api/v1/monitor/events");
    let timer: number | null = null;
    const refresh = () => {
      if (timer !== null) return;
      timer = window.setTimeout(() => {
        timer = null;
        void loadBootstrap();
      }, 750);
    };
    events.addEventListener("source.observed", refresh);
    events.addEventListener("recording.started", refresh);
    events.addEventListener("recording.stopped", refresh);
    return () => {
      events.close();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [loadBootstrap]);

  const step = useCallback(async (count = 1) => {
    if (!run || busy || run.status === "complete") return;
    setBusy(true);
    try {
      const next = await api<Run>(`/api/v1/monitor/replays/${run.id}/step`, {
        method: "POST", body: JSON.stringify({ count }),
      });
      setRun(next); setSelectedSequence(next.current_cycle?.sequence ?? null);
      if (next.status === "complete") setPlaying(false);
      setError(null);
    } catch (requestError) {
      setPlaying(false); setError(requestError instanceof Error ? requestError.message : "Replay step failed");
    } finally { setBusy(false); }
  }, [busy, run]);

  useEffect(() => {
    if (!playing || !run || busy || run.status === "complete") return;
    const delay = speed >= 8 ? 60 : Math.max(70, 100 / speed);
    const timer = window.setTimeout(() => void step(speed >= 8 ? 8 : 1), delay);
    return () => window.clearTimeout(timer);
  }, [busy, playing, run, speed, step]);

  const reset = async () => {
    if (!run || busy) return;
    setBusy(true); setPlaying(false);
    try {
      const next = await api<Run>(`/api/v1/monitor/replays/${run.id}/reset`, { method: "POST" });
      setRun(next); setSelectedSequence(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Reset failed");
    } finally { setBusy(false); }
  };

  const selectedCycle = useMemo(() => {
    if (!run?.cycles.length) return null;
    return run.cycles.find((cycle) => cycle.sequence === selectedSequence) ?? run.current_cycle ?? null;
  }, [run, selectedSequence]);
  const stages = selectedCycle?.stages ?? [];
  const activeStage = stages.find((stage) => stage.stage === selectedStage) ?? stages[0] ?? null;
  const humans = selectedCycle?.social_state?.humans ?? [];
  const action = selectedCycle?.behavior_intent?.action ?? (selectedCycle?.scheduler?.decision_triggered ? "PENDING" : "NO DECISION");
  const source = bootstrap?.sources[0];

  const toggleRecording = async () => {
    if (!source) return;
    setBusy(true);
    try {
      if (source.recording_run_id) {
        await api(`/api/v1/monitor/recordings/${source.recording_run_id}/stop`, { method: "POST" });
      } else {
        await api("/api/v1/monitor/recordings/start", { method: "POST", body: JSON.stringify({ source_id: source.id }) });
      }
      await loadBootstrap();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Recording control failed");
    } finally { setBusy(false); }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Network size={18} /></div><div><strong>Pipeline Lens</strong><span>P4P observability</span></div></div>
        <label className="search"><Search size={15} /><input ref={searchInput} aria-label="Search recordings" placeholder="Search recordings" value={query} onChange={(event) => setQuery(event.target.value)} /><kbd>⌘K</kbd></label>
        <nav>
          <p className="nav-label">Workspace</p>
          <button className="nav-item active"><Activity size={16} /> Observatory <span>{run ? 1 : 0}</span></button>
          <a className="nav-item" href="/debug"><Bug size={16} /> LLM Debug</a>
          <button className="nav-item"><Database size={16} /> Recordings <span>{bootstrap?.recordings.length ?? 0}</span></button>
          <button className="nav-item"><Bot size={16} /> Sources {bootstrap?.sources.some((item) => item.status === "online") && <i className="online-dot" />}</button>
        </nav>
        <div className="source-section">
          <p className="nav-label">Connected sources</p>
          {bootstrap?.sources.length ? bootstrap.sources.map((item) => (
            <button className={`source-card ${liveSourceId === item.id ? "selected" : ""}`} key={item.id} onClick={() => setLiveSourceId(item.id)}><span className="source-icon"><Bot size={17} /></span><span><strong>{item.name}</strong><small>{item.status} · {item.frame_count} frames</small></span><i className={item.status === "online" ? "online-dot" : "stale-dot"} /></button>
          )) : <div className="empty-source"><Radio size={15} /><span>Waiting for robot traffic</span></div>}
          <p className="nav-label recording-label">Replay library</p>
          {bootstrap?.recordings.filter((recording) => recording.name.toLowerCase().includes(query.trim().toLowerCase())).map((recording) => (
            <button className={`source-card ${run?.recording_id === recording.id ? "selected" : ""}`} key={recording.id} onClick={() => void createRun(recording)} disabled={busy}>
              <span className="source-icon replay"><RotateCcw size={17} /></span><span><strong>{recording.name}</strong><small>{recording.frames} frames · {recording.duration_s.toFixed(1)} s</small></span>
            </button>
          ))}
        </div>
        <div className="sidebar-footer"><div><span className="pulse" /> {bootstrap?.health.status ?? "Connecting"}</div><small>Local shadow mode · actuation off</small></div>
      </aside>

      <main>
        <header className="topbar">
          <div><div className="eyebrow"><span>{run?.mode?.toUpperCase() ?? "MONITOR"}</span> / {run?.recording_id ?? "awaiting run"}</div><h1>{run?.name ?? "Pipeline observatory"} <em>— {run?.policy_name ?? "select a recording"}</em></h1></div>
          <div className="header-actions">
            <label className="speed-select policy-select"><Sparkles size={15} /><select aria-label="Replay policy" value={policyMode} onChange={(event) => setPolicyMode(event.target.value as "stub" | "current")}><option value="stub">Deterministic</option><option value="current">Current Ollama</option></select><ChevronDown size={14} /></label>
            <label className="speed-select"><Clock3 size={16} /><select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={8}>Max</option></select><ChevronDown size={14} /></label>
            <button className="record-button" onClick={() => void toggleRecording()} disabled={!source || busy} title={source ? "Record the first live source" : "Connect a live source to record"}>{source?.recording_run_id ? <Square size={10} fill="currentColor" /> : <span />}{source?.recording_run_id ? "Stop recording" : "Record live"}</button>
          </div>
        </header>
        {error && <div className="error-banner"><AlertTriangle size={15} /><span>{error}</span><button onClick={() => setError(null)}><X size={14} /></button></div>}

        <section className="metrics">
          <article><span>Frame</span><strong>{String(selectedCycle?.sequence ?? 0).padStart(3, "0")} <small>/ {String(run?.frame_count ?? 0).padStart(3, "0")}</small></strong><i><CircleDot size={14} /> {(selectedCycle?.elapsed_s ?? 0).toFixed(2)} s</i></article>
          <article><span>Pipeline latency</span><strong>{(selectedCycle?.duration_ms ?? 0).toFixed(1)} <small>ms</small></strong><i className="good"><Gauge size={14} /> {selectedCycle?.status ?? "idle"}</i></article>
          <article><span>Humans tracked</span><strong>{String(humans.length).padStart(2, "0")}</strong><i><UserRound size={14} /> {humans.map((human: Dict) => human.track_id).join(", ") || "empty scene"}</i></article>
          <article><span>Outcome</span><strong className="accent">{action}</strong><i><Sparkles size={14} /> {selectedCycle?.behavior_intent?.decision_confidence ?? "—"} confidence</i></article>
        </section>

        <section className="stage-panel">
          <div className="section-heading"><div><span>Cycle trace</span><small>Every stage for the selected observation</small></div><div className="legend"><i className="done" /> Complete <i className="skipped" /> Skipped <i className="failed" /> Failed</div></div>
          <div className="stage-flow">
            {(stages.length ? stages : Object.keys(stageLabels).map((stage) => ({ stage, status: "waiting", duration_ms: 0 }))).map((stage, index, values) => (
              <div className="stage-wrap" key={stage.stage}>
                <button className={`stage-card status-${stage.status} ${selectedStage === stage.stage ? "selected" : ""}`} onClick={() => setSelectedStage(stage.stage)}>
                  <span className="stage-index">0{index + 1}</span><span className="stage-check">{stage.status === "failed" ? <X size={12} /> : stage.status === "skipped" || stage.status === "waiting" ? <span /> : <Check size={12} />}</span>
                  <strong>{stageLabels[stage.stage] ?? stage.stage}</strong><small>{stageDetail(stage, selectedCycle)}</small><time>{stage.duration_ms.toFixed(1)} ms</time>
                </button>{index < values.length - 1 && <span className="stage-link" />}
              </div>
            ))}
          </div>
        </section>

        <section className="workbench">
          <article className="scene-card panel">
            <div className="panel-title"><div><span>Robot frame</span><small>X forward · Y left · current state</small></div><div className="scene-legend"><i /> observed <i /> predicted</div></div>
            <div className="scene-grid"><div className="axis axis-x" /><div className="axis axis-y" /><div className="range-ring ring-one" /><div className="range-ring ring-two" /><div className="robot"><Bot size={20} /><span>ROBOT</span></div>
              {humans.map((human: Dict) => {
                const x = human.position_robot_m?.x ?? 0; const y = human.position_robot_m?.y ?? 0;
                return <div className={`human ${human.predicted_only ? "predicted" : ""}`} style={{ left: `${clamp(50 - y * 10, 8, 92)}%`, top: `${clamp(72 - x * 10, 10, 90)}%` }} key={human.track_id}><UserRound size={18} /><span>{human.track_id}</span><small>{human.distance_m?.toFixed(1) ?? "?"} m · {human.motion_relation?.toLowerCase()}</small></div>;
              })}
              {!humans.length && <div className="scene-empty"><UserRound size={18} /><span>No human tracks in this frame</span></div>}
              <span className="axis-label x">+X</span><span className="axis-label y">+Y</span>
            </div>
          </article>

          <article className="inspector panel">
            <div className="tabs"><button className={tab === "transition" ? "active" : ""} onClick={() => setTab("transition")}>Transition</button><button className={tab === "payload" ? "active" : ""} onClick={() => setTab("payload")}>Payload</button><button className={tab === "timing" ? "active" : ""} onClick={() => setTab("timing")}>Timing</button></div>
            {activeStage ? <><div className="inspector-head"><span className="trigger-icon">{activeStage.status === "failed" ? <AlertTriangle size={17} /> : <Radio size={17} />}</span><div><small>{activeStage.stage.toUpperCase()} · {activeStage.status.toUpperCase()}</small><strong>{stageDetail(activeStage, selectedCycle)}</strong></div><time>{activeStage.duration_ms.toFixed(2)} ms</time></div>
              {tab === "transition" && <TransitionView cycle={selectedCycle} stage={activeStage} />}{tab === "payload" && <pre className="json-view">{JSON.stringify(activeStage.payload ?? {}, null, 2)}</pre>}{tab === "timing" && <TimingView stages={stages} />}</> : <div className="inspector-empty"><LoaderCircle size={20} /><span>Step the replay to inspect its first cycle.</span></div>}
          </article>
        </section>

        <section className="timeline-panel panel">
          <div className="timeline-head"><div className="transport"><button aria-label="Restart" onClick={() => void reset()} disabled={!run || run.mode !== "replay" || busy}><RotateCcw size={16} /></button><button className="play" aria-label={playing ? "Pause" : "Play"} onClick={() => setPlaying((value) => !value)} disabled={!run || run.mode !== "replay" || busy || run.status === "complete"}>{playing ? <Pause size={17} /> : <Play size={17} fill="currentColor" />}</button><button aria-label="Step forward" onClick={() => void step()} disabled={!run || run.mode !== "replay" || busy || run.status === "complete"}><SkipForward size={16} /></button></div><div><span>Observation timeline</span><small>Amber markers indicate scheduler activity</small></div><time>{formatClock(selectedCycle?.elapsed_s ?? 0)} / {formatClock(run?.duration_s ?? 0)}</time></div>
          <div className="timeline-scroll"><div className="timeline" style={{ gridTemplateColumns: `repeat(${run?.frame_count ?? 1}, minmax(12px, 1fr))` }}><div className="timeline-line" />
            {Array.from({ length: run?.frame_count ?? 1 }, (_, index) => { const sequence = index + 1; const cycle = run?.cycles.find((item) => item.sequence === sequence); const active = selectedCycle?.sequence === sequence; return <button key={sequence} className={`${active ? "active" : ""} ${cycle?.scheduler?.decision_triggered ? "event" : ""} ${cycle ? "processed" : "pending"}`} onClick={() => cycle && setSelectedSequence(sequence)} aria-label={`Frame ${sequence}`}><i />{active && <b>{sequence}</b>}</button>; })}
          </div></div>
        </section>
      </main>
    </div>
  );
}

function TransitionView({ cycle, stage }: { cycle: Cycle | null; stage: Stage }) {
  const triggers = cycle?.scheduler?.triggers ?? [];
  return <><div className={`callout ${stage.status === "failed" ? "error" : ""}`}><span>{stage.status === "failed" ? "Stage failure" : `${triggers.length} scheduler trigger${triggers.length === 1 ? "" : "s"}`}</span><p>{stage.error ?? (cycle?.scheduler?.decision_triggered ? "The social situation changed enough to request a fresh policy decision." : "The pipeline retained the existing behavior while gathering more evidence.")}</p></div>
    {!!triggers.length && <div className="chips">{triggers.map((trigger) => <span key={trigger}>{trigger}</span>)}</div>}<p className="mini-label">Changes since previous frame</p><div className="change-list">{cycle?.changes.length ? cycle.changes.slice(0, 7).map((change, index) => <div key={`${change.entity}-${change.field}-${index}`}><span>{change.entity} · {change.field}</span><code>{formatValue(change.from)}</code><b>→</b><code className="new">{formatValue(change.to)}</code></div>) : <div className="no-change">No typed state changes in this frame</div>}</div></>;
}

function TimingView({ stages }: { stages: Stage[] }) {
  const maximum = Math.max(1, ...stages.map((stage) => stage.duration_ms));
  return <div className="timing-view">{stages.map((stage) => <div key={stage.stage}><span>{stageLabels[stage.stage] ?? stage.stage}</span><i><b style={{ width: `${Math.max(2, stage.duration_ms / maximum * 100)}%` }} /></i><time>{stage.duration_ms.toFixed(2)} ms</time></div>)}</div>;
}

function stageDetail(stage: Stage, cycle: Cycle | null) {
  if (stage.error) return "Stage failed";
  if (stage.stage === "observation") return cycle ? `${cycle.observation.humans?.length ?? 0} observations` : "Awaiting frame";
  if (stage.stage === "validation") return stage.status === "waiting" ? "Awaiting frame" : `Schema ${stage.payload?.schema_version ?? "v1.0"}`;
  if (stage.stage === "state") return cycle ? `${cycle.social_state?.humans?.length ?? 0} tracks updated` : "Awaiting state";
  if (stage.stage === "scheduler") return cycle?.scheduler?.decision_triggered ? "Decision triggered" : "No decision due";
  if (stage.stage === "policy") return stage.status === "skipped" ? "Not invoked" : stage.payload?.action ?? stage.status;
  if (stage.stage === "intent") return cycle?.behavior_intent?.action ?? (stage.status === "skipped" ? "No intent" : stage.status);
  return stage.status;
}

function formatClock(seconds: number) {
  const minutes = Math.floor(seconds / 60); const remainder = seconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(2).padStart(5, "0")}`;
}
