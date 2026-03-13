import React, { useState } from "react";

export function WarningsPanel({ warnings }) {
  const [expanded, setExpanded] = useState(false);
  const permissionWarnings = warnings.filter((w) => w.startsWith("[permission]"));
  const errorWarnings = warnings.filter((w) => w.startsWith("[error]"));
  const otherWarnings = warnings.filter((w) => !w.startsWith("[permission]") && !w.startsWith("[error]"));

  return (
    <div className="graph-stage-warnings">
      <button className="graph-warnings-toggle" onClick={() => setExpanded((v) => !v)}>
        <span>
          {permissionWarnings.length > 0 && (
            <span className="graph-warnings-perm-badge">{permissionWarnings.length} permission error{permissionWarnings.length === 1 ? "" : "s"}</span>
          )}
          {errorWarnings.length > 0 && (
            <span className="graph-warnings-perm-badge">{errorWarnings.length} error{errorWarnings.length === 1 ? "" : "s"}</span>
          )}
          {otherWarnings.length > 0 && (
            <span className="graph-warnings-other-badge">{otherWarnings.length} warning{otherWarnings.length === 1 ? "" : "s"}</span>
          )}
        </span>
        <span className="graph-warnings-caret">{expanded ? "▼" : "▲"}</span>
      </button>
      {expanded && (
        <ul className="graph-warnings-list">
          {permissionWarnings.map((w, i) => (
            <li key={`p-${i}`} className="graph-warnings-item graph-warnings-item--perm">
              {w.replace("[permission] ", "")}
            </li>
          ))}
          {errorWarnings.map((w, i) => (
            <li key={`e-${i}`} className="graph-warnings-item graph-warnings-item--error">
              {w.replace("[error] ", "")}
            </li>
          ))}
          {otherWarnings.map((w, i) => (
            <li key={`o-${i}`} className="graph-warnings-item">
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
