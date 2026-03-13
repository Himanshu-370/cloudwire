import { useCallback, useEffect, useRef, useState } from "react";
import { normalizeGraph } from "../lib/graphTransforms";

const API_PREFIX = "/api";
const MAX_SCAN_MS = 5 * 60 * 1000;

function nextPollDelayMs(startedAt) {
  const elapsed = Date.now() - startedAt;
  if (elapsed <= 30_000) return 1000;
  if (elapsed <= 60_000) return 2000;
  return 3000;
}

function isTerminal(status) {
  return ["completed", "failed", "cancelled"].includes(status);
}

async function parseError(response, fallback) {
  let raw = "";
  let payload = null;
  try {
    raw = await response.text();
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = null;
  }
  const apiError = payload?.error;
  if (apiError?.message) return apiError.message;
  if (typeof payload?.detail === "string") return payload.detail;
  return raw || `${fallback} (${response.status})`;
}

async function requestJson(path, options = {}, fallback = "API request failed") {
  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, options);
  } catch (error) {
    if (error instanceof TypeError && /failed to fetch|network/i.test(error.message)) {
      throw new Error("Unable to reach the backend.");
    }
    throw error;
  }
  if (!response.ok) throw new Error(await parseError(response, fallback));
  return response.json();
}

const EMPTY_GRAPH = { nodes: [], edges: [], metadata: {} };

function toMsg(err) {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return String(err);
}

export function useXRayPolling() {
  const [graphData, setGraphData] = useState(EMPTY_GRAPH);
  const [jobStatus, setJobStatus] = useState(null);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [error, setError] = useState("");
  const [traceSummaries, setTraceSummaries] = useState([]);
  const pollState = useRef({ token: 0, timer: null, startedAt: 0 });

  const clearPolling = useCallback(() => {
    pollState.current.token += 1;
    if (pollState.current.timer) {
      window.clearTimeout(pollState.current.timer);
      pollState.current.timer = null;
    }
  }, []);

  const fetchJobStatus = useCallback(async (jobId) => {
    return requestJson(`/xray/scan/${encodeURIComponent(jobId)}`, {}, "Unable to fetch X-Ray scan status");
  }, []);

  const fetchJobGraph = useCallback(async (jobId, token) => {
    const payload = await requestJson(
      `/xray/scan/${encodeURIComponent(jobId)}/graph`,
      {},
      "Unable to load X-Ray graph"
    );
    if (token === undefined || token === pollState.current.token) {
      setGraphData(normalizeGraph(payload));
    }
    return payload;
  }, []);

  const fetchTraces = useCallback(async (jobId) => {
    try {
      const payload = await requestJson(
        `/xray/traces?job_id=${encodeURIComponent(jobId)}`,
        {},
        "Unable to fetch trace summaries"
      );
      setTraceSummaries(payload.traces || []);
      return payload;
    } catch (err) {
      // Non-critical — trace summaries are supplementary
      console.debug("Failed to fetch trace summaries:", err);
      return { traces: [], count: 0 };
    }
  }, []);

  const pollJob = useCallback(
    async (jobId, token) => {
      if (token !== pollState.current.token) return;
      if (Date.now() - pollState.current.startedAt > MAX_SCAN_MS) {
        setScanLoading(false);
        setError("X-Ray scan timed out after 5 minutes.");
        return;
      }

      try {
        const statusPayload = await fetchJobStatus(jobId);
        if (token !== pollState.current.token) return;
        setJobStatus(statusPayload);

        try {
          await fetchJobGraph(jobId, token);
          if (token !== pollState.current.token) return;
          setError("");
        } catch (graphError) {
          if (token !== pollState.current.token) return;
          setError(toMsg(graphError));
        }

        if (isTerminal(statusPayload.status)) {
          setScanLoading(false);
          if (statusPayload.status === "completed") {
            await fetchTraces(jobId);
          }
          if (statusPayload.status === "failed" && statusPayload.error) {
            setError(statusPayload.error);
          } else if (statusPayload.status !== "failed") {
            setError("");
          }
          return;
        }

        const delay = nextPollDelayMs(pollState.current.startedAt);
        pollState.current.timer = window.setTimeout(() => pollJob(jobId, token), delay);
      } catch (scanError) {
        if (token !== pollState.current.token) return;
        setScanLoading(false);
        setError(toMsg(scanError));
      }
    },
    [fetchJobGraph, fetchJobStatus, fetchTraces]
  );

  const startPolling = useCallback(
    async (jobId) => {
      clearPolling();
      const token = pollState.current.token;
      pollState.current.startedAt = Date.now();
      await pollJob(jobId, token);
    },
    [clearPolling, pollJob]
  );

  const runXRayScan = useCallback(
    async ({ region = "us-east-1", timeRangeMinutes = 60, filterExpression = null, groupName = null, forceRefresh = false }) => {
      clearPolling();
      setGraphData(EMPTY_GRAPH);
      setJobStatus(null);
      setTraceSummaries([]);
      setError("");
      setScanLoading(true);

      try {
        const body = {
          region,
          time_range_minutes: timeRangeMinutes,
          force_refresh: forceRefresh,
        };
        if (filterExpression) body.filter_expression = filterExpression;
        if (groupName) body.group_name = groupName;

        const payload = await requestJson("/xray/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }, "Unable to start X-Ray scan");

        const jobId = payload.job_id;
        setCurrentJobId(jobId);

        const statusPayload = await fetchJobStatus(jobId);
        setJobStatus(statusPayload);

        await fetchJobGraph(jobId, pollState.current.token);

        if (isTerminal(statusPayload.status)) {
          setScanLoading(false);
          if (statusPayload.status === "completed") {
            await fetchTraces(jobId);
          }
          if (statusPayload.status === "failed" && statusPayload.error) {
            setError(statusPayload.error);
          }
          return payload;
        }

        await startPolling(jobId);
        return payload;
      } catch (scanError) {
        setScanLoading(false);
        setError(toMsg(scanError));
        throw scanError;
      }
    },
    [clearPolling, fetchJobGraph, fetchJobStatus, fetchTraces, startPolling]
  );

  const stopXRayScan = useCallback(async () => {
    if (!currentJobId) return null;
    clearPolling();
    setScanLoading(false);

    try {
      const payload = await requestJson(`/xray/scan/${encodeURIComponent(currentJobId)}/stop`, {
        method: "POST",
      }, "Unable to stop X-Ray scan");
      setJobStatus(payload);
      return payload;
    } catch (stopError) {
      setError(toMsg(stopError));
      return null;
    }
  }, [clearPolling, currentJobId]);

  const fetchTraceDetail = useCallback(async (traceId, region = "us-east-1") => {
    return requestJson(
      `/xray/traces/${encodeURIComponent(traceId)}?region=${encodeURIComponent(region)}`,
      {},
      "Unable to fetch trace details"
    );
  }, []);

  useEffect(() => {
    return () => clearPolling();
  }, [clearPolling]);

  return {
    xrayGraphData: graphData,
    xrayJobStatus: jobStatus,
    xrayCurrentJobId: currentJobId,
    xrayLoading: scanLoading,
    xrayError: error,
    setXRayError: setError,
    traceSummaries,
    runXRayScan,
    stopXRayScan,
    fetchTraceDetail,
    clearXRayPolling: clearPolling,
  };
}

export function formatXRayJobStatusLabel(jobStatus) {
  if (!jobStatus) return "idle";
  if (jobStatus.cancellation_requested && !isTerminal(jobStatus.status)) {
    return "stop requested";
  }
  return jobStatus.status;
}
