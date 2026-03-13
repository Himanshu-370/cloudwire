import React, { useState } from "react";
import { createServiceIcon, getServiceVisual } from "../../lib/serviceVisuals.jsx";

export function ServiceSidebar({
  serviceCounts,
  hiddenServices,
  onShowAllServices,
  onToggleService,
  collapsedServices,
  onToggleCluster,
  showIsolated,
  onToggleIsolated,
  isolatedCount,
  stats,
  query,
  onQueryChange,
  filteredNodes,
  selectedNodeId,
  onSelectNode,
  totalNodes,
  searchTruncated,
}) {
  const [servicesExpanded, setServicesExpanded] = useState(true);
  const totalResources = Object.values(serviceCounts).reduce((total, value) => total + value, 0);
  const serviceEntries = Object.entries(serviceCounts);
  const hasData = serviceEntries.length > 0;

  return (
    <aside className="sidebar-shell">

      {/* ── Stats strip ── */}
      {hasData && (
        <div className="sidebar-stats-strip">
          {Object.entries(stats).map(([label, value]) => (
            <div key={label} className="sidebar-stat-chip">
              <span className="sidebar-stat-chip-value">{value}</span>
              <span className="sidebar-stat-chip-label">{label}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Services section ── */}
      <section className="sidebar-block">
        <button
          className="sidebar-section-header"
          onClick={() => setServicesExpanded((v) => !v)}
          aria-expanded={servicesExpanded}
        >
          <span className="sidebar-section-title">Services</span>
          <span className="sidebar-section-header-right">
            {hasData && (
              <span className="sidebar-section-count">{serviceEntries.length}</span>
            )}
            <span className="sidebar-section-caret">{servicesExpanded ? "▾" : "▸"}</span>
          </span>
        </button>

        {servicesExpanded && (
          <div className="sidebar-services-list">
            {/* "All" pill — only shown when some services are hidden */}
            {hiddenServices.length > 0 && (
              <button
                className="sidebar-filter-pill sidebar-filter-pill--all active"
                onClick={onShowAllServices}
                title="Show all services"
              >
                <span className="sidebar-service-main">
                  <span>All</span>
                </span>
                <span className="sidebar-row-count">{totalResources}</span>
              </button>
            )}

            {serviceEntries.map(([service, count]) => {
              const visual = getServiceVisual(service);
              const hidden = hiddenServices.includes(service);
              const collapsed = collapsedServices?.has(service);
              return (
                <div key={service} className="sidebar-pill-row">
                  <button
                    className={`sidebar-filter-pill sidebar-filter-pill-inline ${hidden ? "" : "active"}`}
                    onClick={() => onToggleService(service)}
                    title={hidden ? `Show ${visual.label}` : `Hide ${visual.label}`}
                  >
                    <span className="sidebar-service-main">
                      <span className="sidebar-service-icon" style={{ color: visual.color }}>
                        {createServiceIcon(service, visual.color)}
                      </span>
                      <span>{visual.label}</span>
                    </span>
                    <span className="sidebar-row-count">{count}</span>
                  </button>
                  {onToggleCluster && (
                    <button
                      className={`sidebar-cluster-btn ${collapsed ? "active" : ""}`}
                      onClick={() => onToggleCluster(service)}
                      title={collapsed ? `Expand ${visual.label}` : `Collapse ${visual.label}`}
                    >
                      {collapsed ? (
                        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                          <rect x="1" y="1" width="8" height="8" rx="1" stroke="currentColor" strokeWidth="1.2"/>
                          <path d="M3 5h4M5 3v4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                        </svg>
                      ) : (
                        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                          <rect x="1" y="1" width="8" height="8" rx="1" stroke="currentColor" strokeWidth="1.2"/>
                          <path d="M3 5h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              );
            })}

            {!hasData && (
              <div className="sidebar-empty-state">Run a scan to see services.</div>
            )}
          </div>
        )}
      </section>

      {/* ── Disconnected toggle — inline, no extra section block ── */}
      {isolatedCount > 0 && (
        <section className="sidebar-block">
          <button
            className={`sidebar-isolated-toggle ${showIsolated ? "active" : ""}`}
            onClick={onToggleIsolated}
            title={showIsolated ? "Hide disconnected resources" : "Show disconnected resources"}
          >
            <span className="sidebar-isolated-label">
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ flexShrink: 0 }}>
                <circle cx="5" cy="5" r="3.5" stroke="currentColor" strokeWidth="1.2" strokeDasharray="2 1.5"/>
              </svg>
              Disconnected
            </span>
            <span className="sidebar-isolated-badge">{isolatedCount}</span>
          </button>
        </section>
      )}

      {/* ── Resource search ── */}
      <section className="sidebar-search-section sidebar-block sidebar-block-grow">
        <div className="sidebar-search-wrap">
          <svg className="sidebar-search-icon" width="11" height="11" viewBox="0 0 11 11" fill="none">
            <circle cx="4.5" cy="4.5" r="3.5" stroke="currentColor" strokeWidth="1.2"/>
            <path d="M7.5 7.5l2 2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
          </svg>
          <input
            id="resource-search-input"
            className="sidebar-search-input"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search resources…"
          />
          {query && (
            <button
              className="sidebar-search-clear"
              onClick={() => onQueryChange("")}
              aria-label="Clear search"
            >
              ✕
            </button>
          )}
        </div>

        {searchTruncated > 0 && (
          <div className="sidebar-search-note">
            {searchTruncated} matches — refine to narrow
          </div>
        )}

        <div className="sidebar-results-list">
          {filteredNodes.length === 0 ? (
            <div className="sidebar-empty-state">
              {query ? "No matches." : "No resources."}
            </div>
          ) : (
            filteredNodes.map((node) => {
              const visual = getServiceVisual(node.service);
              return (
                <button
                  key={node.id}
                  className={`sidebar-result-row ${selectedNodeId === node.id ? "active" : ""}`}
                  onClick={() => onSelectNode(node.id)}
                  title={node.id}
                >
                  <span className="sidebar-result-icon" style={{ color: visual.color }}>
                    {createServiceIcon(node.service, visual.color)}
                  </span>
                  <span className="sidebar-result-name">{node.label || node.id}</span>
                  <span className="sidebar-result-svc">{visual.label || node.service}</span>
                </button>
              );
            })
          )}
        </div>
      </section>
    </aside>
  );
}
