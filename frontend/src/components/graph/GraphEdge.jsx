import React from "react";
import { getServiceVisual } from "../../lib/serviceVisuals.jsx";

function nodeRadius(node) {
  const size = Math.min(node.width || 88, node.height || 88);
  return size / 2 - 6;
}

function boundaryPoint(from, to, radius) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 1) return from;
  return { x: from.x + (dx / dist) * radius, y: from.y + (dy / dist) * radius };
}

function curvedPath(source, target) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  const absDx = Math.abs(dx);
  const absDy = Math.abs(dy);

  // For mostly-horizontal edges (left→right flow), use smooth horizontal bezier
  if (absDx > absDy * 0.6 && absDx > 60) {
    const cx1 = source.x + dx * 0.4;
    const cy1 = source.y;
    const cx2 = target.x - dx * 0.4;
    const cy2 = target.y;
    const labelX = source.x + dx * 0.5;
    const labelY = source.y + dy * 0.5 - 4;
    return { path: `M${source.x},${source.y} C${cx1},${cy1} ${cx2},${cy2} ${target.x},${target.y}`, labelX, labelY };
  }

  // For short or vertical edges, use a gentle curve offset
  const curvature = Math.min(0.2, 30 / (dist || 1));
  const mx = source.x + dx * 0.5;
  const my = source.y + dy * 0.5;
  const cx = mx - dy * curvature;
  const cy = my + dx * curvature;
  return { path: `M${source.x},${source.y} Q${cx},${cy} ${target.x},${target.y}`, labelX: cx, labelY: cy };
}

function xrayEdgeColor(errorRate) {
  if (errorRate > 5) return "#ff4444";
  if (errorRate > 1) return "#ffaa00";
  return "#00e7aa";
}

function xrayEdgeWidth(requests) {
  if (!requests || requests <= 0) return 1.5;
  // Log scale, clamped 1.5-6
  return Math.min(6, Math.max(1.5, 1.5 + Math.log10(requests) * 1.2));
}

function formatLatency(ms) {
  if (ms == null) return "";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function GraphEdge({ edge, sourceNode, targetNode, highlighted, hovered, showLabel, animated, pathHighlight, blastEdge }) {
  const sourceVisual = getServiceVisual(sourceNode?.service);
  const isXRay = edge.source_attr === "xray" || edge.has_xray || edge.via === "xray_trace";

  // Color override based on mode
  let color = sourceVisual.color;
  if (pathHighlight) color = "#ffffff";
  else if (blastEdge === "up") color = "#ff9900";
  else if (blastEdge === "down") color = "#00e7ff";
  else if (isXRay) color = xrayEdgeColor(edge.error_rate || 0);

  const sourceRadius = nodeRadius(sourceNode);
  const targetRadius = nodeRadius(targetNode);
  const sourcePos = boundaryPoint(sourceNode.position, targetNode.position, sourceRadius);
  const targetPos = boundaryPoint(targetNode.position, sourceNode.position, targetRadius);

  const { path, labelX, labelY } = curvedPath(sourcePos, targetPos);

  const pathId = `edge-path-${edge.id}`;
  const baseWidth = isXRay ? xrayEdgeWidth(edge.requests) : 1;
  const strokeWidth = pathHighlight ? 2.5 : hovered ? Math.max(baseWidth, 2) : baseWidth;
  const opacity = pathHighlight ? 1 : (highlighted ? (hovered ? 0.9 : isXRay ? 0.7 : 0.45) : 0.08);
  const dashArray = isXRay ? "0" : (pathHighlight || hovered ? "0" : "4,4");
  const animDuration = 1.4 + (Math.abs(edge.id?.charCodeAt(0) || 0) % 10) * 0.08;

  // X-Ray label: show latency + request count
  const xrayLabel = isXRay && (edge.avg_latency_ms || edge.requests)
    ? [formatLatency(edge.avg_latency_ms), edge.requests ? `${edge.requests} req` : ""].filter(Boolean).join(" · ")
    : null;

  return (
    <g>
      <path
        id={pathId}
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        opacity={opacity}
        strokeDasharray={dashArray}
        markerEnd={`url(#arrow-${sourceNode.service})`}
        className={`graph-edge-path${isXRay ? " graph-edge-path--xray" : ""}`}
      />
      {animated && highlighted && (
        <circle r={isXRay ? 4 : 3.5} fill={color} opacity="0.8">
          <animateMotion dur={`${animDuration}s`} repeatCount="indefinite">
            <mpath href={`#${pathId}`} />
          </animateMotion>
        </circle>
      )}
      {/* Standard relationship label */}
      {(showLabel || pathHighlight) && edge.relationship && !xrayLabel && (
        <text
          x={labelX}
          y={labelY - 8}
          textAnchor="middle"
          fontSize="10"
          fill={color}
          opacity="0.9"
          letterSpacing="0.04em"
        >
          {String(edge.relationship || edge.label || "")}
        </text>
      )}
      {/* X-Ray latency/request label */}
      {(showLabel || hovered || pathHighlight) && xrayLabel && (
        <g>
          <rect
            x={labelX - xrayLabel.length * 3 - 6}
            y={labelY - 18}
            width={xrayLabel.length * 6 + 12}
            height="14"
            rx="3"
            fill="#0a1520"
            fillOpacity="0.85"
            stroke={color}
            strokeWidth="0.5"
            strokeOpacity="0.5"
          />
          <text
            x={labelX}
            y={labelY - 8}
            textAnchor="middle"
            fontSize="9"
            fill={color}
            opacity="0.95"
            fontWeight="600"
            letterSpacing="0.03em"
          >
            {xrayLabel}
          </text>
        </g>
      )}
    </g>
  );
}
