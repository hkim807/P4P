import {
  Activity,
  AlertTriangle,
  Bot,
  Bug,
  Check,
  ChevronDown,
  Clock3,
  Code2,
  Copy,
  FileJson,
  Gauge,
  Network,
  Radio,
  RefreshCw,
  Sparkles,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  DebugDecisionSnapshot,
  DebugRobotInput,
  DebugSnapshotEnvelope,
  DebugSource,
  MonitorBootstrap,
} from "./debugTypes";

type ConnectionState = "connecting" | "live" | "retrying";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const body = (await response.json()) as T & {
    error?: string | { message?: string };
  };
  if (!response.ok) {
    const detail = typeof body.error === "string" ? body.error : body.error?.message;
    throw new Error(detail ?? `Request failed (${response.status})`);
  }
  return body;
}

export function DebugPage() {
  const [sources, setSources] = useState<DebugSource[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [envelope, setEnvelope] = useState<DebugSnapshotEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [copied, setCopied] = useState<string | null>(null);
  const [sourcesSampledAtMs, setSourcesSampledAtMs] = useState(Date.now());
  const [nowMs, setNowMs] = useState(Date.now());
  const selectedSourceRef = useRef<string | null>(null);
  const fetchSequence = useRef(0);

  useEffect(() => {
    selectedSourceRef.current = selectedSourceId;
  }, [selectedSourceId]);

  const loadSources = useCallback(async () => {
    try {
      const data = await getJson<MonitorBootstrap>("/api/v1/monitor/bootstrap");
      setSources(data.sources);
      setSourcesSampledAtMs(Date.now());
      setSelectedSourceId((current) => {
        if (current && data.sources.some((source) => source.id === current)) {
          return current;
        }
        const withSnapshot = [...data.sources]
          .filter((source) => source.latest_debug_state_timestamp_us !== null)
          .sort(
            (left, right) =>
              (right.latest_debug_state_timestamp_us ?? 0) -
              (left.latest_debug_state_timestamp_us ?? 0),
          )[0];
        return withSnapshot?.id ?? data.sources[0]?.id ?? null;
      });
      setError(null);
    } catch (requestError) {
      setError(
        requestError instanceof Error ? requestError.message : "Monitor unavailable",
      );
    }
  }, []);

  const loadSnapshot = useCallback(
    async (sourceId: string, minimumRevision = 0, quiet = false) => {
      const sequence = ++fetchSequence.current;
      if (!quiet) setLoading(true);
      try {
        const data = await getJson<DebugSnapshotEnvelope>(
          `/api/v1/monitor/sources/${encodeURIComponent(sourceId)}/debug-snapshot`,
        );
        if (sequence !== fetchSequence.current || data.revision < minimumRevision) {
          return;
        }
        setEnvelope((current) => {
          if (
            current?.source_id === data.source_id &&
            current.revision > data.revision
          ) {
            return current;
          }
          return data;
        });
        setError(null);
      } catch (requestError) {
        if (sequence !== fetchSequence.current) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Debug snapshot unavailable",
        );
      } finally {
        if (sequence === fetchSequence.current) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!selectedSourceId) {
      setEnvelope(null);
      setLoading(false);
      return;
    }
    setEnvelope((current) =>
      current?.source_id === selectedSourceId ? current : null,
    );
    void loadSnapshot(selectedSourceId);
  }, [loadSnapshot, selectedSourceId]);

  useEffect(() => {
    const events = new EventSource("/api/v1/monitor/events");
    let refreshTimer: number | null = null;
    const scheduleSourceRefresh = () => {
      if (refreshTimer !== null) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = null;
        void loadSources();
      }, 500);
    };
    events.onopen = () => setConnection("live");
    events.onerror = () => setConnection("retrying");
    events.addEventListener("source.observed", scheduleSourceRefresh);
    events.addEventListener("debug.snapshot.updated", (event) => {
      scheduleSourceRefresh();
      try {
        const update = JSON.parse((event as MessageEvent<string>).data) as {
          source_id: string;
          revision: number;
        };
        if (selectedSourceRef.current === update.source_id) {
          void loadSnapshot(update.source_id, update.revision, true);
        }
      } catch {
        scheduleSourceRefresh();
      }
    });
    return () => {
      events.close();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    };
  }, [loadSnapshot, loadSources]);

  const selectedSource = sources.find((source) => source.id === selectedSourceId);
  const snapshot = envelope?.source_id === selectedSourceId ? envelope.snapshot : null;
  const output = snapshot?.validated_response ?? null;
  const rankedActions = useMemo(
    () => Object.entries(output?.action_scores ?? {})
      .map(([action, details]) => ({ action, ...details }))
      .sort((a, b) => b.score - a.score),
    [output],
  );

  const copyText = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      window.setTimeout(() => setCopied((current) => (current === label ? null : current)), 1_500);
    } catch {
      setError("Clipboard access is unavailable in this browser.");
    }
  };

  return (
    <div className="debug-shell">
      <aside className="debug-sidebar">
        <div className="brand">
          <div className="brand-mark"><Network size={18} /></div>
          <div><strong>Pipeline Lens</strong><span>P4P observability</span></div>
        </div>
        <nav>
          <p className="nav-label">Workspace</p>
          <a className="nav-item" href="/"><Activity size={16} /> Observatory</a>
          <a className="nav-item active" href="/debug"><Bug size={16} /> LLM Debug</a>
        </nav>
        <div className="debug-source-list">
          <div className="debug-source-heading">
            <p className="nav-label">Connected sources</p>
            <button onClick={() => void loadSources()} aria-label="Refresh sources">
              <RefreshCw size={13} />
            </button>
          </div>
          {sources.length ? sources.map((source) => {
            const sourceStatus = currentSourceStatus(source, sourcesSampledAtMs, nowMs);
            return (
              <button
                className={`source-card ${selectedSourceId === source.id ? "selected" : ""}`}
                key={source.id}
                onClick={() => setSelectedSourceId(source.id)}
              >
                <span className="source-icon"><Bot size={17} /></span>
                <span>
                  <strong>{source.name}</strong>
                  <small>
                    {source.latest_debug_snapshot_status
                      ? `${source.latest_debug_snapshot_status.toLowerCase()} · r${source.debug_snapshot_revision}`
                      : "awaiting debug inference"}
                  </small>
                </span>
                <i className={sourceStatus === "online" ? "online-dot" : "stale-dot"} />
              </button>
            );
          }) : (
            <div className="empty-source"><Radio size={15} /><span>Waiting for robot traffic</span></div>
          )}
        </div>
        <div className="sidebar-footer">
          <div>
            {connection === "live" ? <Wifi size={12} /> : <WifiOff size={12} />}
            {connection === "live" ? "Live updates" : connection}
          </div>
          <small>Debug inspection · actuation unchanged</small>
        </div>
      </aside>

      <main className="debug-main">
        <header className="debug-topbar">
          <div>
            <div className="eyebrow"><span>DEBUG MODE</span> / EVIDENCE TRACE</div>
            <h1>LLM decision debug</h1>
            <p>One complete inference at a time, from SocialState to selected action.</p>
          </div>
          <div className={`debug-live-state ${connection}`}>
            <i /> {connection === "live" ? "Live" : "Reconnecting"}
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <AlertTriangle size={15} /><span>{error}</span>
            <button onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}

        {!selectedSourceId ? (
          <EmptyDebugState
            icon={<Radio size={24} />}
            title="Waiting for a connected source"
            detail="Start the gateway and send a Navel observation to populate this page."
          />
        ) : loading && !snapshot ? (
          <EmptyDebugState
            icon={<RefreshCw className="spin" size={24} />}
            title="Loading the latest inference"
            detail={`Reading the retained snapshot for ${selectedSource?.name ?? selectedSourceId}.`}
          />
        ) : !snapshot ? (
          <EmptyDebugState
            icon={<Bug size={24} />}
            title="No Debug inference yet"
            detail="This source is connected, but no Debug decision has completed. Run the gateway with DECISION_MODE=DEBUG and wait for a decision trigger."
          />
        ) : (
          <div className="debug-content" aria-live="polite">
            <SnapshotHeader
              snapshot={snapshot}
              revision={envelope?.revision ?? 0}
              source={selectedSource}
              sourcesSampledAtMs={sourcesSampledAtMs}
              nowMs={nowMs}
            />

            {snapshot.error && (
              <section className="debug-failure">
                <AlertTriangle size={18} />
                <div>
                  <span>{snapshot.error.code}</span>
                  <strong>Inference could not produce a valid decision</strong>
                  <p>{snapshot.error.message}</p>
                </div>
              </section>
            )}

            <section className="situation-card">
              <div className="debug-section-label"><Sparkles size={14} /> Social situation</div>
              <p>{output?.social_summary ?? "No validated social interpretation is available for this failed request."}</p>
              <div className="situation-context">
                <span>{Array.isArray(snapshot.raw_social_state.humans) ? snapshot.raw_social_state.humans.length : 0} humans in state</span>
                <span>{snapshot.observation_id}</span>
              </div>
            </section>

            <div className="debug-primary-grid">
              <section className="debug-panel">
                <PanelHeading
                  icon={<Radio size={15} />}
                  title="Robot inputs"
                  detail="Values cited from this exact SocialState"
                />
                <div className="robot-input-list">
                  {output?.robot_inputs.length ? output.robot_inputs.map((input, index) => (
                    <RobotInputRow
                      input={input}
                      state={snapshot.raw_social_state}
                      key={`${input.source}-${index}`}
                    />
                  )) : <EmptyInline text="No validated robot inputs were returned." />}
                </div>
              </section>

              <section className="recommendation-card">
                <div className="debug-section-label"><Sparkles size={14} /> Recommended action</div>
                {output ? (
                  <>
                    <div className="recommended-action">
                      <strong>{output.recommended_action}</strong>
                      <span>{formatPercent(actionScore(output.recommended_action, rankedActions))}</span>
                    </div>
                    <p>{output.decision_rationale}</p>
                    <div className="reason-chips">
                      {output.reason_codes.map((reason) => <span key={reason}>{reason}</span>)}
                    </div>
                    <small>Model-reported preference score · not a calibrated probability</small>
                  </>
                ) : <EmptyInline text="No action was accepted for this request." />}
              </section>
            </div>

            <section className="debug-panel evidence-panel">
              <PanelHeading
                icon={<Bug size={15} />}
                title="Evidence detected"
                detail="Direct state facts and model interpretations remain distinct"
              />
              <div className="evidence-grid">
                {output?.evidence.length ? output.evidence.map((item, index) => (
                  <article className={`evidence-item ${item.type.toLowerCase()}`} key={`${item.type}-${index}`}>
                    <span>{item.type === "OBSERVATION" ? "Observed" : "Interpreted"}</span>
                    <p>{item.description}</p>
                    <div>{item.source_fields.map((sourceField) => <code key={sourceField}>{sourceField}</code>)}</div>
                  </article>
                )) : <EmptyInline text="No validated evidence was returned." />}
              </div>
            </section>

            <section className="debug-panel ranking-panel">
              <PanelHeading
                icon={<Gauge size={15} />}
                title="Action ranking"
                detail="Every available action, ordered by normalized model score"
              />
              <div className="action-ranking">
                {rankedActions.length ? rankedActions.map((item, index) => (
                  <article className={item.action === output?.recommended_action ? "selected" : ""} key={item.action}>
                    <span className="rank-number">{String(index + 1).padStart(2, "0")}</span>
                    <strong>{item.action}</strong>
                    <div className="score-track"><i style={{ width: `${item.score * 100}%` }} /></div>
                    <b>{formatPercent(item.score)}</b>
                    <p>{item.reason}</p>
                  </article>
                )) : <EmptyInline text="Action scores are unavailable because validation failed." />}
              </div>
            </section>

            {!!output?.uncertainties.length && (
              <section className="uncertainty-panel">
                <div className="debug-section-label"><AlertTriangle size={14} /> Uncertainty and missing information</div>
                <ul>{output.uncertainties.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
              </section>
            )}

            <section className="debug-viewers">
              <DebugViewer
                icon={<Code2 size={15} />}
                title="View exact LLM prompt"
                subtitle={`${snapshot.rendered_messages.length} ordered messages captured at request time`}
                copyLabel="prompt"
                copyValue={renderMessagesForCopy(snapshot)}
                copied={copied}
                onCopy={copyText}
              >
                <div className="message-viewer">
                  {snapshot.rendered_messages.map((message, index) => (
                    <section key={`${message.role}-${index}`}>
                      <span>{message.role.toUpperCase()} PROMPT</span>
                      <pre>{message.content}</pre>
                    </section>
                  ))}
                </div>
              </DebugViewer>
              <DebugViewer
                icon={<FileJson size={15} />}
                title="View Raw SocialState"
                subtitle="Deep copy supplied to the prompt builder for this request"
                copyLabel="state"
                copyValue={JSON.stringify(snapshot.raw_social_state, null, 2)}
                copied={copied}
                onCopy={copyText}
              >
                <pre className="debug-code-view">{JSON.stringify(snapshot.raw_social_state, null, 2)}</pre>
              </DebugViewer>
              <DebugViewer
                icon={<FileJson size={15} />}
                title="View raw Ollama response"
                subtitle="Original completion retained before parsing and validation"
                copyLabel="response"
                copyValue={snapshot.raw_response ?? ""}
                copied={copied}
                onCopy={copyText}
              >
                <pre className="debug-code-view">{snapshot.raw_response ?? "No response content was received."}</pre>
              </DebugViewer>
              <DebugViewer
                icon={<Check size={15} />}
                title="View validated model output"
                subtitle="Structured output after schema and grounding checks"
                copyLabel="validated"
                copyValue={JSON.stringify(snapshot.validated_response, null, 2)}
                copied={copied}
                onCopy={copyText}
              >
                <pre className="debug-code-view">{JSON.stringify(snapshot.validated_response, null, 2)}</pre>
              </DebugViewer>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function SnapshotHeader({
  snapshot,
  revision,
  source,
  sourcesSampledAtMs,
  nowMs,
}: {
  snapshot: DebugDecisionSnapshot;
  revision: number;
  source: DebugSource | undefined;
  sourcesSampledAtMs: number;
  nowMs: number;
}) {
  const sourceAge = source ? currentSourceAge(source, sourcesSampledAtMs, nowMs) : null;
  return (
    <section className="snapshot-meta">
      <div><span>Request</span><code title={snapshot.request_id}>{shortId(snapshot.request_id)}</code><small>revision {revision}</small></div>
      <div><span>SocialState time</span><strong>{snapshot.state_timestamp_us.toLocaleString()} µs</strong><small>{snapshot.clock_domain}</small></div>
      <div><span>LLM request</span><strong>{formatTimestamp(snapshot.metadata.requested_at)}</strong><small>{formatTimestamp(snapshot.metadata.responded_at)} response</small></div>
      <div><span>LLM latency</span><strong>{snapshot.metadata.latency_ms.toFixed(1)} ms</strong><small>{snapshot.metadata.finish_reason ?? snapshot.status.toLowerCase()}</small></div>
      <div><span>Model</span><strong>{snapshot.metadata.returned_model ?? snapshot.metadata.requested_model}</strong><small>{snapshot.metadata.provider} · {snapshot.metadata.decision_mode}</small></div>
      <div><span>Source freshness</span><strong>{sourceAge === null ? "Unknown" : `${sourceAge.toFixed(1)} s ago`}</strong><small>{source ? currentSourceStatus(source, sourcesSampledAtMs, nowMs) : snapshot.source_id}</small></div>
    </section>
  );
}

function PanelHeading({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="debug-panel-heading"><span>{icon}</span><div><strong>{title}</strong><small>{detail}</small></div></div>;
}

function RobotInputRow({ input, state }: { input: DebugRobotInput; state: Record<string, unknown> }) {
  const missing = input.latest_value === null;
  return (
    <article className={missing ? "missing" : ""}>
      <div><strong>{pointerLabel(input.source)}</strong><code>{input.source}</code></div>
      <span>{formatInputValue(input)}</span>
      <p>{input.interpretation}</p>
      <small>{freshnessForInput(input.source, state)}</small>
    </article>
  );
}

function DebugViewer({
  icon,
  title,
  subtitle,
  copyLabel,
  copyValue,
  copied,
  onCopy,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  copyLabel: string;
  copyValue: string;
  copied: string | null;
  onCopy: (label: string, value: string) => Promise<void>;
  children: React.ReactNode;
}) {
  return (
    <details className="debug-viewer">
      <summary><span>{icon}</span><div><strong>{title}</strong><small>{subtitle}</small></div><ChevronDown size={15} /></summary>
      <div className="viewer-body">
        <button className="copy-button" onClick={() => void onCopy(copyLabel, copyValue)}>
          {copied === copyLabel ? <Check size={13} /> : <Copy size={13} />}
          {copied === copyLabel ? "Copied" : `Copy ${copyLabel}`}
        </button>
        {children}
      </div>
    </details>
  );
}

function EmptyDebugState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <section className="debug-empty">{icon}<strong>{title}</strong><p>{detail}</p></section>;
}

function EmptyInline({ text }: { text: string }) {
  return <div className="debug-inline-empty">{text}</div>;
}

function renderMessagesForCopy(snapshot: DebugDecisionSnapshot) {
  return snapshot.rendered_messages
    .map((message) => `${message.role.toUpperCase()} PROMPT\n\n${message.content}`)
    .join("\n\n---\n\n");
}

function actionScore(action: string, scores: Array<{ action: string; score: number }>) {
  return scores.find((item) => item.action === action)?.score ?? 0;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatTimestamp(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function shortId(value: string) {
  return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-8)}` : value;
}

function pointerLabel(pointer: string) {
  const parts = pointer.split("/").filter(Boolean);
  const leaf = parts.at(-1)?.replaceAll("_", " ") ?? pointer;
  if (parts[0] === "humans" && parts[1] !== undefined) {
    return `Human ${parts[1]} · ${leaf}`;
  }
  return leaf;
}

function formatInputValue(input: DebugRobotInput) {
  const value = input.latest_value;
  if (value === null) return "UNKNOWN";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (typeof value !== "number") return String(value);
  if (input.source.endsWith("_m")) return `${value.toFixed(2)} m`;
  if (input.source.endsWith("_mps")) return `${value.toFixed(2)} m/s`;
  if (input.source.endsWith("_ms")) return `${value.toFixed(0)} ms`;
  if (/(probability|score)$/.test(input.source) && value >= 0 && value <= 1) {
    return `${Math.round(value * 100)}% (${value})`;
  }
  return String(value);
}

function freshnessForInput(pointer: string, state: Record<string, unknown>) {
  const match = pointer.match(/^\/humans\/(\d+)\//);
  const humans = Array.isArray(state.humans) ? state.humans : [];
  const human = match ? humans[Number(match[1])] : null;
  if (isRecord(human) && typeof human.state_age_ms === "number") {
    return `Track state age ${human.state_age_ms.toLocaleString()} ms`;
  }
  return "No field-level timestamp available";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function currentSourceAge(source: DebugSource, sampledAtMs: number, nowMs: number) {
  return source.age_s + Math.max(0, nowMs - sampledAtMs) / 1_000;
}

function currentSourceStatus(source: DebugSource, sampledAtMs: number, nowMs: number) {
  const ageSeconds = currentSourceAge(source, sampledAtMs, nowMs);
  if (ageSeconds <= 2.5) return "online";
  if (ageSeconds <= 10) return "stale";
  return "offline";
}
