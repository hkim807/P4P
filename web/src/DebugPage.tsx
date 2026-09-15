import {
  Activity,
  AlertTriangle,
  Bot,
  Bug,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  FileJson,
  Gauge,
  History,
  Network,
  Radio,
  RefreshCw,
  Sparkles,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  DebugDecisionDetail,
  DebugDecisionHistoryPage,
  DebugDecisionSnapshot,
  DebugDecisionSummary,
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
  const [history, setHistory] = useState<DebugDecisionSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingOlder, setHistoryLoadingOlder] = useState(false);
  const [historyNextCursor, setHistoryNextCursor] = useState<number | null>(null);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [decisionDetail, setDecisionDetail] = useState<DebugDecisionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [copied, setCopied] = useState<string | null>(null);
  const [sourcesSampledAtMs, setSourcesSampledAtMs] = useState(Date.now());
  const [nowMs, setNowMs] = useState(Date.now());
  const selectedSourceRef = useRef<string | null>(null);
  const selectedRequestRef = useRef<string | null>(null);
  const latestHistoryRequestRef = useRef<string | null>(null);
  const fetchSequence = useRef(0);
  const historyFetchSequence = useRef(0);
  const detailFetchSequence = useRef(0);
  const historyNextCursorRef = useRef<number | null>(null);
  const historyHasLoadedRef = useRef(false);

  useEffect(() => {
    selectedSourceRef.current = selectedSourceId;
  }, [selectedSourceId]);

  useEffect(() => {
    selectedRequestRef.current = selectedRequestId;
  }, [selectedRequestId]);

  useEffect(() => {
    latestHistoryRequestRef.current = history[0]?.request_id ?? null;
  }, [history]);

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

  const loadHistory = useCallback(
    async (
      sourceId: string,
      options: {
        append?: boolean;
        quiet?: boolean;
        selectRequestId?: string;
      } = {},
    ) => {
      const { append = false, quiet = false, selectRequestId } = options;
      const cursor = append ? historyNextCursorRef.current : null;
      if (append && cursor === null) return;
      const sequence = ++historyFetchSequence.current;
      if (!quiet) {
        if (append) setHistoryLoadingOlder(true);
        else setHistoryLoading(true);
      }
      try {
        const query = new URLSearchParams({ limit: "50" });
        if (cursor !== null) query.set("before", String(cursor));
        const data = await getJson<DebugDecisionHistoryPage>(
          `/api/v1/monitor/sources/${encodeURIComponent(sourceId)}/debug-decisions?${query}`,
        );
        if (
          sequence !== historyFetchSequence.current ||
          selectedSourceRef.current !== sourceId
        ) {
          return;
        }
        const preserveLoaded = quiet && historyHasLoadedRef.current && !append;
        setHistory((current) => {
          if (!append && !preserveLoaded) return data.items;
          const known = new Set(current.map((item) => item.request_id));
          if (preserveLoaded) {
            return [
              ...data.items,
              ...current.filter(
                (item) => !data.items.some((fresh) => fresh.request_id === item.request_id),
              ),
            ];
          }
          return [
            ...current,
            ...data.items.filter((item) => !known.has(item.request_id)),
          ];
        });
        historyHasLoadedRef.current = true;
        if (!preserveLoaded) {
          historyNextCursorRef.current = data.next_cursor;
          setHistoryNextCursor(data.next_cursor);
        }
        setSelectedRequestId((current) =>
          selectRequestId ?? (append ? current : current ?? data.items[0]?.request_id ?? null),
        );
        setError(null);
      } catch (requestError) {
        if (sequence !== historyFetchSequence.current) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Debug decision history unavailable",
        );
      } finally {
        if (sequence === historyFetchSequence.current) {
          setHistoryLoading(false);
          setHistoryLoadingOlder(false);
        }
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
      setHistory([]);
      historyNextCursorRef.current = null;
      historyHasLoadedRef.current = false;
      setHistoryNextCursor(null);
      setSelectedRequestId(null);
      setDecisionDetail(null);
      setLoading(false);
      setHistoryLoading(false);
      return;
    }
    setEnvelope((current) =>
      current?.source_id === selectedSourceId ? current : null,
    );
    setHistory([]);
    historyNextCursorRef.current = null;
    historyHasLoadedRef.current = false;
    setHistoryNextCursor(null);
    setSelectedRequestId(null);
    setDecisionDetail(null);
    detailFetchSequence.current += 1;
    void loadSnapshot(selectedSourceId);
    void loadHistory(selectedSourceId);
  }, [loadHistory, loadSnapshot, selectedSourceId]);

  useEffect(() => {
    if (!selectedRequestId || !selectedSourceId) {
      setDecisionDetail(null);
      setDetailLoading(false);
      return;
    }
    const sequence = ++detailFetchSequence.current;
    setDetailLoading(true);
    void getJson<DebugDecisionDetail>(
      `/api/v1/monitor/debug-decisions/${encodeURIComponent(selectedRequestId)}`,
    )
      .then((data) => {
        if (
          sequence !== detailFetchSequence.current ||
          data.snapshot.source_id !== selectedSourceId ||
          data.snapshot.request_id !== selectedRequestId
        ) {
          return;
        }
        setDecisionDetail(data);
        setError(null);
      })
      .catch((requestError: unknown) => {
        if (sequence !== detailFetchSequence.current) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Debug decision detail unavailable",
        );
      })
      .finally(() => {
        if (sequence === detailFetchSequence.current) setDetailLoading(false);
      });
  }, [selectedRequestId, selectedSourceId]);

  useEffect(() => {
    const events = new EventSource("/api/v1/monitor/events");
    let refreshTimer: number | null = null;
    let snapshotTimer: number | null = null;
    let historyTimer: number | null = null;
    let pendingSnapshot: { source_id: string; revision: number } | null = null;
    let pendingHistory: { source_id: string; request_id: string } | null = null;
    const scheduleSourceRefresh = () => {
      if (refreshTimer !== null) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = null;
        void loadSources();
      }, 500);
    };
    const scheduleSnapshotRefresh = (update: { source_id: string; revision: number }) => {
      if (
        pendingSnapshot?.source_id !== update.source_id ||
        update.revision > pendingSnapshot.revision
      ) {
        pendingSnapshot = update;
      }
      if (snapshotTimer !== null) return;
      snapshotTimer = window.setTimeout(() => {
        snapshotTimer = null;
        const pending = pendingSnapshot;
        pendingSnapshot = null;
        if (pending && selectedSourceRef.current === pending.source_id) {
          void loadSnapshot(pending.source_id, pending.revision, true);
        }
      }, 100);
    };
    const scheduleHistoryRefresh = (update: { source_id: string; request_id: string }) => {
      pendingHistory = update;
      if (historyTimer !== null) return;
      historyTimer = window.setTimeout(() => {
        historyTimer = null;
        const pending = pendingHistory;
        pendingHistory = null;
        if (!pending || selectedSourceRef.current !== pending.source_id) return;
        const followsLatest =
          selectedRequestRef.current === null ||
          selectedRequestRef.current === latestHistoryRequestRef.current;
        void loadHistory(pending.source_id, {
          quiet: true,
          selectRequestId: followsLatest ? pending.request_id : undefined,
        });
      }, 100);
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
          scheduleSnapshotRefresh(update);
        }
      } catch {
        scheduleSourceRefresh();
      }
    });
    events.addEventListener("debug.decision.recorded", (event) => {
      scheduleSourceRefresh();
      try {
        const update = JSON.parse((event as MessageEvent<string>).data) as {
          source_id: string;
          request_id: string;
        };
        if (selectedSourceRef.current === update.source_id) {
          scheduleHistoryRefresh(update);
        }
      } catch {
        scheduleSourceRefresh();
      }
    });
    return () => {
      events.close();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      if (snapshotTimer !== null) window.clearTimeout(snapshotTimer);
      if (historyTimer !== null) window.clearTimeout(historyTimer);
    };
  }, [loadHistory, loadSnapshot, loadSources]);

  const selectedSource = sources.find((source) => source.id === selectedSourceId);
  const latestSnapshot =
    envelope?.source_id === selectedSourceId ? envelope.snapshot : null;
  const detailedSnapshot =
    decisionDetail?.snapshot.request_id === selectedRequestId
      ? decisionDetail.snapshot
      : null;
  const snapshot = selectedRequestId
    ? detailedSnapshot ??
      (latestSnapshot?.request_id === selectedRequestId ? latestSnapshot : null)
    : latestSnapshot;
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
                      ? `${source.latest_debug_snapshot_status.toLowerCase()} · ${source.debug_decision_count} decisions`
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
            <p>Review every saved inference from SocialState to selected action.</p>
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
        ) : (
          <div className="debug-workspace">
            <DecisionHistoryPanel
              items={history}
              selectedRequestId={selectedRequestId}
              latestRequestId={history[0]?.request_id ?? latestSnapshot?.request_id ?? null}
              total={selectedSource?.debug_decision_count ?? history.length}
              loading={historyLoading}
              loadingOlder={historyLoadingOlder}
              hasOlder={historyNextCursor !== null}
              onSelect={setSelectedRequestId}
              onLoadOlder={() => void loadHistory(selectedSourceId, { append: true })}
            />
            <div className="debug-detail">
          {(loading || historyLoading || detailLoading) && !snapshot ? (
            <EmptyDebugState
              icon={<RefreshCw className="spin" size={24} />}
              title="Loading decision details"
              detail={`Reading saved inferences for ${selectedSource?.name ?? selectedSourceId}.`}
            />
          ) : selectedRequestId && !snapshot ? (
            <EmptyDebugState
              icon={<AlertTriangle size={24} />}
              title="Decision detail unavailable"
              detail="Select another saved decision or refresh the connected source."
            />
          ) : !snapshot ? (
            <EmptyDebugState
              icon={<Bug size={24} />}
              title="No saved Debug decisions yet"
              detail="This source is connected, but no Debug decision has completed. Run the gateway with DECISION_MODE=DEBUG and wait for a decision trigger."
            />
          ) : (
          <div className="debug-content" aria-live="polite">
            <SnapshotHeader
              snapshot={snapshot}
              revision={
                latestSnapshot?.request_id === snapshot.request_id
                  ? envelope?.revision ?? null
                  : null
              }
              historyId={
                decisionDetail?.snapshot.request_id === snapshot.request_id
                  ? decisionDetail.history_id
                  : null
              }
              recordedAt={
                decisionDetail?.snapshot.request_id === snapshot.request_id
                  ? decisionDetail.recorded_at
                  : null
              }
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
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function DecisionHistoryPanel({
  items,
  selectedRequestId,
  latestRequestId,
  total,
  loading,
  loadingOlder,
  hasOlder,
  onSelect,
  onLoadOlder,
}: {
  items: DebugDecisionSummary[];
  selectedRequestId: string | null;
  latestRequestId: string | null;
  total: number;
  loading: boolean;
  loadingOlder: boolean;
  hasOlder: boolean;
  onSelect: (requestId: string) => void;
  onLoadOlder: () => void;
}) {
  return (
    <aside className="decision-history" aria-label="LLM decision history">
      <header>
        <span><History size={15} /></span>
        <div>
          <strong>Decision history</strong>
          <small>{total.toLocaleString()} saved for this source</small>
        </div>
      </header>
      <div className="decision-history-list">
        {loading && !items.length ? (
          <div className="decision-history-empty">
            <RefreshCw className="spin" size={16} />
            Loading saved decisions
          </div>
        ) : items.length ? items.map((item) => {
          const isLatest = item.request_id === latestRequestId;
          const isSelected = item.request_id === selectedRequestId;
          const description = item.status === "FAILED"
            ? item.error_message ?? item.error_code ?? "Inference failed"
            : item.decision_rationale ?? "No decision rationale was returned.";
          return (
            <button
              type="button"
              className={`decision-history-item ${isSelected ? "selected" : ""} ${item.status.toLowerCase()}`}
              key={item.request_id}
              aria-pressed={isSelected}
              onClick={() => onSelect(item.request_id)}
            >
              <div className="decision-history-status">
                {item.status === "COMPLETED" ? <Check size={12} /> : <AlertTriangle size={12} />}
              </div>
              <div className="decision-history-copy">
                <div>
                  <strong>{item.recommended_action ?? "FAILED"}</strong>
                  {isLatest && <span>Latest</span>}
                  <time dateTime={item.requested_at ?? item.recorded_at}>
                    <Clock3 size={10} /> {formatTimestamp(item.requested_at ?? item.recorded_at)}
                  </time>
                </div>
                <p>{description}</p>
                <small>
                  #{item.history_id} · {item.returned_model ?? item.requested_model ?? "unknown model"}
                  {item.latency_ms !== null ? ` · ${item.latency_ms.toFixed(0)} ms` : ""}
                </small>
              </div>
              <ChevronRight size={14} />
            </button>
          );
        }) : (
          <div className="decision-history-empty">
            <History size={17} />
            Decisions will appear here automatically
          </div>
        )}
      </div>
      {hasOlder && (
        <button
          type="button"
          className="load-older-decisions"
          disabled={loadingOlder}
          onClick={onLoadOlder}
        >
          {loadingOlder && <RefreshCw className="spin" size={12} />}
          {loadingOlder ? "Loading…" : "Load older decisions"}
        </button>
      )}
    </aside>
  );
}

function SnapshotHeader({
  snapshot,
  revision,
  historyId,
  recordedAt,
  source,
  sourcesSampledAtMs,
  nowMs,
}: {
  snapshot: DebugDecisionSnapshot;
  revision: number | null;
  historyId: number | null;
  recordedAt: string | null;
  source: DebugSource | undefined;
  sourcesSampledAtMs: number;
  nowMs: number;
}) {
  const sourceAge = source ? currentSourceAge(source, sourcesSampledAtMs, nowMs) : null;
  return (
    <section className="snapshot-meta">
      <div><span>Request</span><code title={snapshot.request_id}>{shortId(snapshot.request_id)}</code><small>{revision !== null ? `latest · revision ${revision}` : historyId !== null ? `saved decision #${historyId}` : "saved decision"}</small></div>
      <div><span>SocialState time</span><strong>{snapshot.state_timestamp_us.toLocaleString()} µs</strong><small>{snapshot.clock_domain}</small></div>
      <div><span>LLM request</span><strong>{formatTimestamp(snapshot.metadata.requested_at)}</strong><small>{formatTimestamp(snapshot.metadata.responded_at)} response</small></div>
      <div><span>LLM latency</span><strong>{snapshot.metadata.latency_ms.toFixed(1)} ms</strong><small>{snapshot.metadata.finish_reason ?? snapshot.status.toLowerCase()}</small></div>
      <div><span>Model</span><strong>{snapshot.metadata.returned_model ?? snapshot.metadata.requested_model}</strong><small>{snapshot.metadata.provider} · {snapshot.metadata.decision_mode}</small></div>
      <div><span>{recordedAt ? "Saved" : "Source freshness"}</span><strong>{recordedAt ? formatTimestamp(recordedAt) : sourceAge === null ? "Unknown" : `${sourceAge.toFixed(1)} s ago`}</strong><small>{recordedAt ? `source ${snapshot.source_id}` : source ? currentSourceStatus(source, sourcesSampledAtMs, nowMs) : snapshot.source_id}</small></div>
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
