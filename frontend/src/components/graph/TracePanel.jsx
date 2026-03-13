import React, { useCallback, useEffect, useState } from "react";

function statusClass(trace) {
  if (trace.has_fault) return "trace-row--fault";
  if (trace.has_error) return "trace-row--error";
  if (trace.has_throttle) return "trace-row--throttle";
  return "trace-row--ok";
}

function statusLabel(trace) {
  if (trace.has_fault) return "FAULT";
  if (trace.has_error) return "ERROR";
  if (trace.has_throttle) return "THROTTLE";
  return "OK";
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const ms = seconds * 1000;
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function WaterfallBar({ segment, traceStart, traceEnd }) {
  const totalDuration = traceEnd - traceStart;
  if (!totalDuration || !segment.start_time || !segment.end_time) return null;

  const left = ((segment.start_time - traceStart) / totalDuration) * 100;
  const width = Math.max(0.5, ((segment.end_time - segment.start_time) / totalDuration) * 100);
  const indent = (segment.depth || 0) * 12;

  let barColor = "#00e7aa";
  if (segment.fault) barColor = "#ff4444";
  else if (segment.error) barColor = "#ffaa00";

  return (
    <div className="waterfall-row" style={{ paddingLeft: indent }}>
      <div className="waterfall-label" title={segment.name}>
        {segment.name || "unknown"}
        {segment.duration != null && <span className="waterfall-dur">{segment.duration}ms</span>}
      </div>
      <div className="waterfall-track">
        <div
          className="waterfall-bar"
          style={{
            left: `${left}%`,
            width: `${width}%`,
            backgroundColor: barColor,
          }}
        />
      </div>
    </div>
  );
}

function TraceWaterfall({ trace, onClose }) {
  if (!trace) return null;

  const segments = trace.segments || [];
  const allSubs = segments.flatMap((s) => [
    { name: s.name, start_time: s.start_time, end_time: s.end_time, duration: s.duration, fault: s.fault, error: s.error, depth: 0, origin: s.origin },
    ...(s.subsegments || []),
  ]);

  const times = allSubs.filter((s) => s.start_time && s.end_time);
  const traceStart = times.length > 0 ? Math.min(...times.map((s) => s.start_time)) : 0;
  const traceEnd = times.length > 0 ? Math.max(...times.map((s) => s.end_time)) : 1;

  return (
    <div className="trace-waterfall">
      <div className="trace-waterfall-header">
        <span className="trace-waterfall-title">TRACE {trace.trace_id}</span>
        <button className="trace-waterfall-close" onClick={onClose}>CLOSE</button>
      </div>
      <div className="trace-waterfall-body">
        {allSubs.length === 0 && (
          <div className="trace-waterfall-empty">No segments available</div>
        )}
        {allSubs.map((seg, i) => (
          <WaterfallBar key={`${seg.name}-${i}`} segment={seg} traceStart={traceStart} traceEnd={traceEnd} />
        ))}
      </div>
    </div>
  );
}

export function TracePanel({ traces, region, fetchTraceDetail, onClose }) {
  const [selectedTrace, setSelectedTrace] = useState(null);
  const [traceDetail, setTraceDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleTraceClick = useCallback(async (trace) => {
    if (selectedTrace?.trace_id === trace.trace_id) {
      setSelectedTrace(null);
      setTraceDetail(null);
      return;
    }
    setSelectedTrace(trace);
    setTraceDetail(null);
    setError("");
    setLoading(true);
    try {
      const detail = await fetchTraceDetail(trace.trace_id, region);
      setTraceDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [selectedTrace, fetchTraceDetail, region]);

  // Reset when traces change
  useEffect(() => {
    setSelectedTrace(null);
    setTraceDetail(null);
  }, [traces]);

  if (!traces?.length) return null;

  return (
    <div className="trace-panel">
      <div className="trace-panel-header">
        <span className="trace-panel-title">TRACES ({traces.length})</span>
        <button className="trace-panel-close" onClick={onClose}>CLOSE</button>
      </div>

      <div className="trace-panel-list">
        {traces.slice(0, 100).map((trace) => (
          <button
            key={trace.trace_id}
            className={`trace-row ${statusClass(trace)} ${selectedTrace?.trace_id === trace.trace_id ? "trace-row--selected" : ""}`}
            onClick={() => handleTraceClick(trace)}
          >
            <span className={`trace-status-pill ${statusClass(trace)}`}>{statusLabel(trace)}</span>
            <span className="trace-entry">{trace.entry_point || "—"}</span>
            <span className="trace-method">{trace.http_method || ""}</span>
            <span className="trace-duration">{formatDuration(trace.duration)}</span>
            <span className="trace-id-short">{trace.trace_id?.slice(-8) || ""}</span>
          </button>
        ))}
      </div>

      {loading && <div className="trace-panel-loading">Loading trace details...</div>}
      {error && <div className="trace-panel-error">{error}</div>}

      {traceDetail && (
        <TraceWaterfall
          trace={traceDetail}
          onClose={() => { setSelectedTrace(null); setTraceDetail(null); }}
        />
      )}
    </div>
  );
}
