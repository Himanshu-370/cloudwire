import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, { Background, Controls, MarkerType, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const SERVICE_COLORS = {
  apigateway: "#00f7ff",
  lambda: "#8eff5a",
  sqs: "#ff9e44",
  eventbridge: "#ff49d7",
  dynamodb: "#a183ff",
  unknown: "#7f9aac",
};

const EDGE_COLORS = {
  invokes: "#00f7ff",
  calls: "#8eff5a",
  triggers: "#ff49d7",
  default: "#7f9aac",
};

const NODE_WIDTH = 220;
const ARC_SPACING = 285;

function toTitleCase(text) {
  return String(text || "")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function compactText(text) {
  const value = String(text || "");
  if (value.length <= 52) return value;
  return `${value.slice(0, 28)}...${value.slice(-18)}`;
}

function parseServicesInput(rawText) {
  return rawText
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function nextPollDelayMs(startedAt) {
  const elapsed = Date.now() - startedAt;
  if (elapsed <= 30_000) return 1000;
  if (elapsed <= 60_000) return 2000;
  return 3000;
}

function collectDownstreamIds(startNodeId, edges) {
  const adjacency = new Map();
  edges.forEach((edge) => {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    adjacency.get(edge.source).push(edge.target);
  });

  const visited = new Set([startNodeId]);
  const queue = [startNodeId];
  while (queue.length > 0) {
    const current = queue.shift();
    const children = adjacency.get(current) || [];
    for (const next of children) {
      if (visited.has(next)) continue;
      visited.add(next);
      queue.push(next);
    }
  }
  return visited;
}

function buildLevels(nodes, edges) {
  const indegree = new Map();
  const adjacency = new Map();

  nodes.forEach((node) => {
    indegree.set(node.id, 0);
    adjacency.set(node.id, []);
  });

  edges.forEach((edge) => {
    if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) return;
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
    adjacency.get(edge.source).push(edge.target);
  });

  const queue = Array.from(indegree.entries())
    .filter(([, degree]) => degree === 0)
    .map(([id]) => id)
    .sort();
  const levels = new Map(queue.map((id) => [id, 0]));

  while (queue.length > 0) {
    const current = queue.shift();
    const currentLevel = levels.get(current) || 0;
    for (const next of adjacency.get(current) || []) {
      levels.set(next, Math.max(levels.get(next) || 0, currentLevel + 1));
      indegree.set(next, (indegree.get(next) || 0) - 1);
      if ((indegree.get(next) || 0) === 0) queue.push(next);
    }
  }

  let fallback = Math.max(1, ...Array.from(levels.values()));
  nodes.forEach((node) => {
    if (!levels.has(node.id)) {
      fallback += 1;
      levels.set(node.id, fallback);
    }
  });
  return levels;
}

function splitByConnectivity(nodes, edges) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const degree = new Map(nodes.map((node) => [node.id, 0]));
  const undirected = new Map(nodes.map((node) => [node.id, []]));
  const validEdges = [];

  edges.forEach((edge) => {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) return;
    validEdges.push(edge);
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    if (edge.target !== edge.source) {
      degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
      undirected.get(edge.source).push(edge.target);
      undirected.get(edge.target).push(edge.source);
    }
  });

  const connectedIds = new Set(
    Array.from(degree.entries())
      .filter(([, value]) => value > 0)
      .map(([id]) => id)
  );
  const unconnectedNodes = nodes.filter((node) => !connectedIds.has(node.id));

  const components = [];
  const visited = new Set();
  Array.from(connectedIds)
    .sort()
    .forEach((startId) => {
      if (visited.has(startId)) return;
      const queue = [startId];
      const componentIds = [];
      visited.add(startId);
      while (queue.length > 0) {
        const current = queue.shift();
        componentIds.push(current);
        for (const next of undirected.get(current) || []) {
          if (!connectedIds.has(next) || visited.has(next)) continue;
          visited.add(next);
          queue.push(next);
        }
      }
      components.push(componentIds);
    });

  const componentNodes = components
    .map((ids) => ids.map((id) => nodeById.get(id)).filter(Boolean))
    .filter((group) => group.length > 0)
    .sort((a, b) => b.length - a.length);

  return {
    componentNodes,
    unconnectedNodes,
    validEdges,
  };
}

function edgesForNodeSet(edges, nodeSet) {
  return edges.filter((edge) => nodeSet.has(edge.source) && nodeSet.has(edge.target));
}

function layoutFlowComponent(nodes, edges, originX, originY) {
  const levels = buildLevels(nodes, edges);
  const buckets = new Map();
  nodes.forEach((node) => {
    const lvl = levels.get(node.id) || 0;
    if (!buckets.has(lvl)) buckets.set(lvl, []);
    buckets.get(lvl).push(node);
  });

  const positioned = [];
  Array.from(buckets.keys())
    .sort((a, b) => a - b)
    .forEach((lvl) => {
      const bucket = buckets.get(lvl) || [];
      bucket.sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)));
      bucket.forEach((node, index) => {
        const row = index % 8;
        const lane = Math.floor(index / 8);
        positioned.push({
          ...node,
          position: {
            x: originX + lvl * 310 + lane * 78,
            y: originY + row * 118,
          },
        });
      });
    });
  return positioned;
}

function layoutCircularComponent(nodes, edges, centerX, centerY) {
  const levels = buildLevels(nodes, edges);
  const buckets = new Map();
  nodes.forEach((node) => {
    const lvl = levels.get(node.id) || 0;
    if (!buckets.has(lvl)) buckets.set(lvl, []);
    buckets.get(lvl).push(node);
  });

  const positioned = [];
  Array.from(buckets.keys())
    .sort((a, b) => a - b)
    .forEach((lvl) => {
      const bucket = [...(buckets.get(lvl) || [])];
      bucket.sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)));

      if (lvl === 0 && bucket.length === 1) {
        positioned.push({ ...bucket[0], position: { x: centerX, y: centerY } });
        return;
      }

      let cursor = 0;
      let ringIndex = 0;
      while (cursor < bucket.length) {
        const baseRadius = 120 + lvl * 190 + ringIndex * 150;
        const ringCapacity = Math.max(6, Math.floor((2 * Math.PI * baseRadius) / ARC_SPACING));
        const take = Math.min(ringCapacity, bucket.length - cursor);
        const angleOffset = lvl * 0.2 + ringIndex * 0.12;

        for (let i = 0; i < take; i += 1) {
          const node = bucket[cursor + i];
          const angle = take === 1 ? angleOffset : (2 * Math.PI * i) / take + angleOffset;
          positioned.push({
            ...node,
            position: {
              x: centerX + Math.cos(angle) * baseRadius,
              y: centerY + Math.sin(angle) * baseRadius,
            },
          });
        }
        cursor += take;
        ringIndex += 1;
      }
    });

  return positioned;
}

function layoutFlow(nodes, edges) {
  const positioned = [];
  const { componentNodes, unconnectedNodes, validEdges } = splitByConnectivity(nodes, edges);

  const componentCols = Math.max(1, Math.ceil(Math.sqrt(componentNodes.length || 1)));
  let maxConnectedX = 0;

  componentNodes.forEach((component, index) => {
    const col = index % componentCols;
    const row = Math.floor(index / componentCols);
    const originX = 120 + col * 1260;
    const originY = 90 + row * 940;
    const idSet = new Set(component.map((node) => node.id));
    const componentEdges = edgesForNodeSet(validEdges, idSet);
    const laidOut = layoutFlowComponent(component, componentEdges, originX, originY);
    laidOut.forEach((node) => {
      maxConnectedX = Math.max(maxConnectedX, node.position.x);
      positioned.push(node);
    });
  });

  const isolatedBaseX = (maxConnectedX || 320) + 700;
  const isolatedCols = Math.max(3, Math.ceil(Math.sqrt(unconnectedNodes.length || 1)));
  [...unconnectedNodes]
    .sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)))
    .forEach((node, index) => {
      const col = index % isolatedCols;
      const row = Math.floor(index / isolatedCols);
      positioned.push({
        ...node,
        position: {
          x: isolatedBaseX + col * 255,
          y: 100 + row * 112,
        },
      });
    });

  return positioned;
}

function layoutCircularTree(nodes, edges) {
  const positioned = [];
  const { componentNodes, unconnectedNodes, validEdges } = splitByConnectivity(nodes, edges);
  const componentCols = Math.max(1, Math.ceil(Math.sqrt(componentNodes.length || 1)));
  let maxConnectedX = 0;

  componentNodes.forEach((component, index) => {
    const col = index % componentCols;
    const row = Math.floor(index / componentCols);
    const centerX = 820 + col * 1650;
    const centerY = 780 + row * 1320;
    const idSet = new Set(component.map((node) => node.id));
    const componentEdges = edgesForNodeSet(validEdges, idSet);
    const laidOut = layoutCircularComponent(component, componentEdges, centerX, centerY);
    laidOut.forEach((node) => {
      maxConnectedX = Math.max(maxConnectedX, node.position.x);
      positioned.push(node);
    });
  });

  const isolatedBaseX = (maxConnectedX || 1250) + 760;
  const isolatedCols = Math.max(4, Math.ceil(Math.sqrt(unconnectedNodes.length || 1)));
  [...unconnectedNodes]
    .sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id)))
    .forEach((node, index) => {
      const col = index % isolatedCols;
      const row = Math.floor(index / isolatedCols);
      positioned.push({
        ...node,
        position: {
          x: isolatedBaseX + col * 255,
          y: 220 + row * 112,
        },
      });
    });

  return positioned;
}

function mapsEqualByPosition(prevMap, nextMap) {
  const prevKeys = Object.keys(prevMap);
  const nextKeys = Object.keys(nextMap);
  if (prevKeys.length !== nextKeys.length) return false;
  for (const key of nextKeys) {
    const a = prevMap[key];
    const b = nextMap[key];
    if (!a || !b) return false;
    if (a.x !== b.x || a.y !== b.y) return false;
  }
  return true;
}

export default function App() {
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], metadata: {} });
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [resourceDetails, setResourceDetails] = useState(null);
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState("us-east-1");
  const [servicesText, setServicesText] = useState("apigateway, lambda, sqs, eventbridge, dynamodb");
  const [scanMode, setScanMode] = useState("quick");
  const [layoutMode, setLayoutMode] = useState("circular");
  const [scanLoading, setScanLoading] = useState(false);
  const [error, setError] = useState("");
  const [highlightEnabled, setHighlightEnabled] = useState(true);
  const [jobStatus, setJobStatus] = useState(null);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [showSearchPanel, setShowSearchPanel] = useState(true);
  const [showDetailsPanel, setShowDetailsPanel] = useState(true);
  const [nodePositions, setNodePositions] = useState({});
  const pollState = useRef({ token: 0, timer: null, startedAt: 0 });
  const reactFlowRef = useRef(null);
  const prevLayoutModeRef = useRef(layoutMode);
  const prevJobIdRef = useRef(currentJobId);

  const downstreamIds = useMemo(() => {
    if (!selectedNodeId || !highlightEnabled) return new Set();
    return collectDownstreamIds(selectedNodeId, graphData.edges);
  }, [selectedNodeId, highlightEnabled, graphData.edges]);

  const autoLayoutNodes = useMemo(() => {
    if (layoutMode === "flow") return layoutFlow(graphData.nodes, graphData.edges);
    return layoutCircularTree(graphData.nodes, graphData.edges);
  }, [graphData.nodes, graphData.edges, layoutMode]);

  useEffect(() => {
    setNodePositions((prev) => {
      const hardReset =
        prevLayoutModeRef.current !== layoutMode || prevJobIdRef.current !== currentJobId;
      const next = {};
      autoLayoutNodes.forEach((node) => {
        next[node.id] = hardReset ? node.position : prev[node.id] || node.position;
      });
      prevLayoutModeRef.current = layoutMode;
      prevJobIdRef.current = currentJobId;
      if (mapsEqualByPosition(prev, next)) return prev;
      return next;
    });
  }, [autoLayoutNodes, layoutMode, currentJobId]);

  const flowNodes = useMemo(() => {
    return autoLayoutNodes.map((node) => {
      const serviceKey = node.service || "unknown";
      const color = SERVICE_COLORS[serviceKey] || SERVICE_COLORS.unknown;
      const selected = selectedNodeId === node.id;
      const downstream = downstreamIds.has(node.id);
      const muted = selectedNodeId && !selected && !downstream;
      return {
        id: node.id,
        service: serviceKey,
        position: nodePositions[node.id] || node.position,
        data: {
          label: (
            <div className="space-y-1 text-left">
              <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-300">
                {toTitleCase(serviceKey)}
              </div>
              <div className="text-[12px] font-semibold text-slate-100">{compactText(node.label || node.id)}</div>
              <div className="text-[10px] text-slate-500">{compactText(node.id)}</div>
            </div>
          ),
        },
        style: {
          width: NODE_WIDTH,
          minHeight: 72,
          padding: 10,
          border: `1px solid ${selected ? "#fbffff" : color}`,
          background: "linear-gradient(165deg, rgba(5, 9, 16, 0.98), rgba(10, 16, 26, 0.95))",
          boxShadow: selected ? "0 0 26px rgba(255,255,255,0.25)" : `0 0 12px ${color}66`,
          opacity: muted ? 0.14 : 1,
        },
      };
    });
  }, [autoLayoutNodes, nodePositions, selectedNodeId, downstreamIds]);

  const flowEdges = useMemo(() => {
    const denseGraph = graphData.nodes.length > 80 || graphData.edges.length > 140;
    const showLabel = !denseGraph || Boolean(selectedNodeId);
    return graphData.edges.map((edge) => {
      const edgeColor = EDGE_COLORS[edge.relationship] || EDGE_COLORS.default;
      const isTrigger = (edge.relationship || "").toLowerCase() === "triggers";
      const isLinkedToSelected = selectedNodeId
        ? edge.source === selectedNodeId ||
          edge.target === selectedNodeId ||
          (downstreamIds.has(edge.source) && downstreamIds.has(edge.target))
        : true;
      const emphasized = !selectedNodeId || (highlightEnabled && isLinkedToSelected);
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: showLabel ? (edge.relationship || "depends_on").toUpperCase() : undefined,
        type: layoutMode === "circular" ? "bezier" : "smoothstep",
        animated: emphasized && !selectedNodeId && !denseGraph,
        markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
        style: {
          stroke: edgeColor,
          strokeWidth: emphasized ? 2 : 1,
          strokeDasharray: isTrigger ? "6 4" : "0",
          opacity: emphasized ? 0.88 : denseGraph ? 0.04 : 0.1,
        },
        labelStyle: { fill: "#c8e8ff", fontSize: 10, fontWeight: 600 },
        labelBgStyle: { fill: "rgba(0, 0, 0, 0.9)", fillOpacity: 1 },
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 4,
      };
    });
  }, [graphData.nodes.length, graphData.edges, selectedNodeId, downstreamIds, highlightEnabled, layoutMode]);

  const filteredNodes = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return graphData.nodes;
    return graphData.nodes.filter((node) => {
      const bag = `${node.id} ${node.label || ""} ${node.service || ""}`.toLowerCase();
      return bag.includes(normalizedQuery);
    });
  }, [graphData.nodes, query]);

  const layoutClass = useMemo(() => {
    if (showSearchPanel && showDetailsPanel) {
      return "grid min-h-0 gap-3 lg:grid-cols-[210px_minmax(0,1fr)_280px]";
    }
    if (showSearchPanel && !showDetailsPanel) {
      return "grid min-h-0 gap-3 lg:grid-cols-[210px_minmax(0,1fr)]";
    }
    if (!showSearchPanel && showDetailsPanel) {
      return "grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1fr)_280px]";
    }
    return "grid min-h-0 gap-3 grid-cols-1";
  }, [showSearchPanel, showDetailsPanel]);

  const onNodesChange = useCallback((changes) => {
    setNodePositions((prev) => {
      const next = { ...prev };
      changes.forEach((change) => {
        if (change.type === "position" && change.position) {
          next[change.id] = change.position;
        }
        if (change.type === "remove") {
          delete next[change.id];
        }
      });
      return next;
    });
  }, []);

  function clearSelection() {
    setSelectedNodeId(null);
    setResourceDetails(null);
  }

  const fitGraph = useCallback(() => {
    window.requestAnimationFrame(() => {
      reactFlowRef.current?.fitView({
        padding: 0.28,
        duration: 380,
        minZoom: 0.01,
        maxZoom: 1.5,
      });
    });
  }, []);

  function clearPolling() {
    pollState.current.token += 1;
    if (pollState.current.timer) {
      window.clearTimeout(pollState.current.timer);
      pollState.current.timer = null;
    }
  }

  async function fetchGraph() {
    const response = await fetch(`${API_BASE_URL}/graph`);
    if (!response.ok) throw new Error(`GET /graph failed (${response.status})`);
    const payload = await response.json();
    setGraphData(payload);
  }

  async function fetchJobStatus(jobId) {
    const response = await fetch(`${API_BASE_URL}/scan/${encodeURIComponent(jobId)}`);
    if (!response.ok) throw new Error(`GET /scan/{job_id} failed (${response.status})`);
    return response.json();
  }

  async function fetchJobGraph(jobId) {
    const response = await fetch(`${API_BASE_URL}/scan/${encodeURIComponent(jobId)}/graph`);
    if (!response.ok) throw new Error(`GET /scan/{job_id}/graph failed (${response.status})`);
    const payload = await response.json();
    setGraphData(payload);
    return payload;
  }

  async function fetchResource(resourceId, jobId) {
    const params = new URLSearchParams();
    if (jobId) params.set("job_id", jobId);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`${API_BASE_URL}/resource/${encodeURIComponent(resourceId)}${suffix}`);
    if (!response.ok) throw new Error(`GET /resource/{id} failed (${response.status})`);
    const payload = await response.json();
    setResourceDetails(payload);
  }

  async function pollJob(jobId, token) {
    if (token !== pollState.current.token) return;
    try {
      const statusPayload = await fetchJobStatus(jobId);
      if (token !== pollState.current.token) return;
      setJobStatus(statusPayload);
      await fetchJobGraph(jobId);

      if (["completed", "failed", "cancelled"].includes(statusPayload.status)) {
        setScanLoading(false);
        return;
      }

      const delay = nextPollDelayMs(pollState.current.startedAt);
      pollState.current.timer = window.setTimeout(() => pollJob(jobId, token), delay);
    } catch (err) {
      if (token !== pollState.current.token) return;
      setScanLoading(false);
      setError(String(err));
    }
  }

  async function startPolling(jobId) {
    clearPolling();
    const token = pollState.current.token;
    pollState.current.startedAt = Date.now();
    await pollJob(jobId, token);
  }

  async function runScan() {
    clearPolling();
    clearSelection();
    setError("");
    setScanLoading(true);
    setNodePositions({});

    try {
      const services = parseServicesInput(servicesText);
      const response = await fetch(`${API_BASE_URL}/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ region, services, mode: scanMode }),
      });
      if (!response.ok) throw new Error(`POST /scan failed (${response.status})`);
      const payload = await response.json();
      const jobId = payload.job_id;
      setCurrentJobId(jobId);

      const statusPayload = await fetchJobStatus(jobId);
      setJobStatus(statusPayload);
      await fetchJobGraph(jobId);

      if (["completed", "failed", "cancelled"].includes(statusPayload.status)) {
        setScanLoading(false);
        return;
      }
      await startPolling(jobId);
    } catch (err) {
      setScanLoading(false);
      setError(String(err));
    }
  }

  async function stopScan() {
    if (!currentJobId) return;
    try {
      const response = await fetch(`${API_BASE_URL}/scan/${encodeURIComponent(currentJobId)}/stop`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(`POST /scan/{job_id}/stop failed (${response.status})`);
      const payload = await response.json();
      setJobStatus(payload);
      setScanLoading(false);
      clearPolling();
      await fetchJobGraph(currentJobId);
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    async function bootstrap() {
      try {
        await fetchGraph();
      } catch (err) {
        setError(String(err));
      }
    }
    bootstrap();
    return () => clearPolling();
  }, []);

  useEffect(() => {
    if (!selectedNodeId) return;
    fetchResource(selectedNodeId, currentJobId).catch((err) => {
      setResourceDetails(null);
      setError(String(err));
    });
  }, [selectedNodeId, currentJobId]);

  return (
    <div className="h-screen w-screen overflow-hidden bg-black">
      <div className="grid h-full grid-rows-[auto_1fr] gap-2 p-2">
        <header className="rounded-lg border border-cyanline/35 bg-black px-4 py-3 shadow-neon">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-cyanline">AWS Flow Visualizer</h1>
              <p className="text-sm text-slate-400">Drag nodes, click empty canvas to unselect, use stop to cancel scan.</p>
            </div>

            <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-8">
              <label className="text-xs">
                <span className="mb-1 block text-slate-300">AWS Region</span>
                <input
                  className="w-full rounded-md border border-cyanline/35 bg-black px-2 py-2 text-sm outline-none ring-cyanline focus:ring-1"
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                />
              </label>
              <label className="text-xs sm:col-span-2">
                <span className="mb-1 block text-slate-300">Services</span>
                <input
                  className="w-full rounded-md border border-cyanline/35 bg-black px-2 py-2 text-sm outline-none ring-cyanline focus:ring-1"
                  value={servicesText}
                  onChange={(e) => setServicesText(e.target.value)}
                />
              </label>
              <label className="text-xs">
                <span className="mb-1 block text-slate-300">Scan Mode</span>
                <select
                  className="w-full rounded-md border border-cyanline/35 bg-black px-2 py-2 text-sm outline-none ring-cyanline focus:ring-1"
                  value={scanMode}
                  onChange={(e) => setScanMode(e.target.value)}
                >
                  <option value="quick">Quick</option>
                  <option value="deep">Deep</option>
                </select>
              </label>
              <label className="text-xs">
                <span className="mb-1 block text-slate-300">Layout</span>
                <select
                  className="w-full rounded-md border border-cyanline/35 bg-black px-2 py-2 text-sm outline-none ring-cyanline focus:ring-1"
                  value={layoutMode}
                  onChange={(e) => setLayoutMode(e.target.value)}
                >
                  <option value="circular">Circular Tree</option>
                  <option value="flow">Flow</option>
                </select>
              </label>
              <button
                onClick={() => setShowSearchPanel((v) => !v)}
                className="rounded-md border border-cyanline/40 bg-black px-3 py-2 text-xs font-semibold text-cyanline"
              >
                {showSearchPanel ? "Hide Search" : "Show Search"}
              </button>
              <button
                onClick={() => setShowDetailsPanel((v) => !v)}
                className="rounded-md border border-cyanline/40 bg-black px-3 py-2 text-xs font-semibold text-cyanline"
              >
                {showDetailsPanel ? "Hide Details" : "Show Details"}
              </button>
              <button
                onClick={runScan}
                disabled={scanLoading}
                className="rounded-md border border-matrix/80 bg-matrix/15 px-4 py-2 text-sm font-semibold text-matrix transition hover:bg-matrix/25 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {scanLoading ? "Scanning..." : "Run Scan"}
              </button>
              <button
                onClick={fitGraph}
                className="rounded-md border border-cyanline/60 bg-cyanline/10 px-4 py-2 text-sm font-semibold text-cyanline"
              >
                Fit Graph
              </button>
              <button
                onClick={stopScan}
                disabled={!scanLoading || !currentJobId}
                className="rounded-md border border-red-400/70 bg-red-500/10 px-4 py-2 text-sm font-semibold text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Stop Scan
              </button>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-300">
            <span>Nodes: {graphData.metadata?.node_count || graphData.nodes.length}</span>
            <span>Edges: {graphData.metadata?.edge_count || graphData.edges.length}</span>
            {jobStatus && <span>Status: {jobStatus.status}</span>}
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={highlightEnabled}
                onChange={(e) => setHighlightEnabled(e.target.checked)}
              />
              Highlight downstream dependencies
            </label>
          </div>

          {jobStatus && (
            <div className="mt-2 rounded-md border border-cyanline/30 bg-[#050505] p-2 text-xs">
              <div className="flex items-center justify-between text-slate-200">
                <span>
                  Progress: {jobStatus.progress_percent}% ({jobStatus.services_done}/{jobStatus.services_total})
                </span>
                <span className="text-cyanline">{jobStatus.current_service || "idle"}</span>
              </div>
              <div className="mt-1 h-2 w-full rounded bg-slate-900">
                <div
                  className="h-2 rounded bg-cyanline transition-all"
                  style={{ width: `${jobStatus.progress_percent || 0}%` }}
                />
              </div>
            </div>
          )}

          {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
          {(graphData.metadata?.warnings || []).length > 0 && (
            <div className="mt-2 rounded-md border border-amber-400/30 bg-amber-500/10 p-2 text-xs text-amber-200">
              {(graphData.metadata?.warnings || []).slice(0, 4).map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          )}
        </header>

        <main className={layoutClass}>
          {showSearchPanel && (
            <aside className="min-h-0 rounded-lg border border-cyanline/25 bg-black p-3">
              <h2 className="mb-2 text-sm font-semibold text-cyanline">Search Resources</h2>
              <input
                className="mb-3 w-full rounded-md border border-cyanline/30 bg-black px-2 py-2 text-sm outline-none ring-cyanline focus:ring-1"
                placeholder="Search by ID, label, service..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="max-h-[calc(100vh-250px)] overflow-auto rounded-md border border-cyanline/20">
                {filteredNodes.length === 0 ? (
                  <p className="p-3 text-xs text-slate-400">No resources found.</p>
                ) : (
                  filteredNodes.map((node) => {
                    const active = selectedNodeId === node.id;
                    return (
                      <button
                        key={node.id}
                        className={`block w-full border-b border-cyanline/10 px-3 py-2 text-left text-xs ${
                          active ? "bg-cyanline/15 text-cyanline" : "hover:bg-cyanline/10"
                        }`}
                        onClick={() => setSelectedNodeId(node.id)}
                      >
                        <p className="truncate font-semibold">{node.label || node.id}</p>
                        <p className="truncate text-[11px] text-slate-400">{node.service || "unknown"}</p>
                      </button>
                    );
                  })
                )}
              </div>
            </aside>
          )}

          <section className="relative min-h-0 rounded-lg border border-cyanline/25 bg-black">
            <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-wrap gap-2 text-[10px]">
              {Object.entries(SERVICE_COLORS).map(([service, color]) => (
                <span
                  key={service}
                  className="rounded border px-2 py-0.5 uppercase tracking-[0.1em]"
                  style={{ borderColor: `${color}99`, color }}
                >
                  {service}
                </span>
              ))}
            </div>

            {layoutMode === "circular" && (
              <div className="pointer-events-none absolute inset-0 z-0 flex items-center justify-center">
                <div className="h-[320px] w-[320px] rounded-full border border-cyanline/15" />
                <div className="absolute h-[620px] w-[620px] rounded-full border border-cyanline/10" />
                <div className="absolute h-[960px] w-[960px] rounded-full border border-cyanline/10" />
                <div className="absolute h-[1300px] w-[1300px] rounded-full border border-cyanline/10" />
              </div>
            )}

            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              fitView
              fitViewOptions={{ padding: 0.28, duration: 450, minZoom: 0.01 }}
              minZoom={0.01}
              maxZoom={2}
              onInit={(instance) => {
                reactFlowRef.current = instance;
              }}
              onNodesChange={onNodesChange}
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              onPaneClick={clearSelection}
              nodesDraggable
              className="h-full w-full"
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(0, 255, 188, 0.1)" gap={24} size={1} />
              <MiniMap
                pannable
                zoomable
                nodeStrokeColor={(n) => SERVICE_COLORS[n?.service] || SERVICE_COLORS.unknown}
                nodeColor={() => "#030303"}
              />
              <Controls showInteractive />
            </ReactFlow>
          </section>

          {showDetailsPanel && (
            <aside className="min-h-0 rounded-lg border border-cyanline/25 bg-black p-3">
              <h2 className="mb-2 text-sm font-semibold text-cyanline">Node Details</h2>
              {!resourceDetails ? (
                <p className="text-xs text-slate-400">
                  Click a node for details. Click empty canvas to clear selection.
                </p>
              ) : (
                <div className="max-h-[calc(100vh-250px)] space-y-3 overflow-auto text-xs">
                  <div className="rounded-md border border-cyanline/20 bg-[#060606] p-2">
                    <p className="break-all font-semibold text-matrix">{resourceDetails.node.label}</p>
                    <p className="break-all text-slate-400">{resourceDetails.node.id}</p>
                    <p className="mt-1 text-slate-300">
                      Service: <span className="text-cyanline">{resourceDetails.node.service || "unknown"}</span>
                    </p>
                    <p className="text-slate-300">
                      Type: <span className="text-cyanline">{resourceDetails.node.type || "resource"}</span>
                    </p>
                  </div>

                  <div className="rounded-md border border-cyanline/20 bg-[#060606] p-2">
                    <p className="mb-1 font-semibold text-cyanline">Incoming ({resourceDetails.incoming.length})</p>
                    {resourceDetails.incoming.length === 0 ? (
                      <p className="text-slate-400">None</p>
                    ) : (
                      resourceDetails.incoming.map((edge) => (
                        <p key={edge.id} className="break-all text-slate-200">
                          {edge.source} {"->"} {edge.target} ({edge.relationship || "depends_on"})
                        </p>
                      ))
                    )}
                  </div>

                  <div className="rounded-md border border-cyanline/20 bg-[#060606] p-2">
                    <p className="mb-1 font-semibold text-cyanline">Outgoing ({resourceDetails.outgoing.length})</p>
                    {resourceDetails.outgoing.length === 0 ? (
                      <p className="text-slate-400">None</p>
                    ) : (
                      resourceDetails.outgoing.map((edge) => (
                        <p key={edge.id} className="break-all text-slate-200">
                          {edge.source} {"->"} {edge.target} ({edge.relationship || "depends_on"})
                        </p>
                      ))
                    )}
                  </div>

                  <div className="rounded-md border border-cyanline/20 bg-[#060606] p-2">
                    <p className="mb-1 font-semibold text-cyanline">Raw Metadata</p>
                    <pre className="overflow-auto whitespace-pre-wrap text-[11px] text-slate-300">
                      {JSON.stringify(resourceDetails.node, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </aside>
          )}
        </main>
      </div>
    </div>
  );
}
