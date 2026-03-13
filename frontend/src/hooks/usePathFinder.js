import { useCallback, useEffect, useState } from "react";
import { findShortestPath } from "../lib/graphTransforms";

export function usePathFinder(graphNodes, graphEdges) {
  const [pathFinderMode, setPathFinderMode] = useState(false);
  const [pathSource, setPathSource] = useState(null);
  const [foundPath, setFoundPath] = useState([]);
  const [pathNotFound, setPathNotFound] = useState(false);

  // Clear path when exiting path finder mode
  useEffect(() => {
    if (!pathFinderMode) {
      setPathSource(null);
      setFoundPath([]);
    }
  }, [pathFinderMode]);

  // FIX #15: path-not-found message state with cleanup (replaces setTimeout)
  useEffect(() => {
    if (!pathNotFound) return undefined;
    const t = window.setTimeout(() => setPathNotFound(false), 3000);
    return () => window.clearTimeout(t);
  }, [pathNotFound]);

  const handleNodeSelect = useCallback((nodeId, setSelectedNodeId) => {
    if (pathFinderMode) {
      if (!pathSource) {
        setPathSource(nodeId);
        setSelectedNodeId(nodeId);
      } else if (pathSource === nodeId) {
        setPathSource(null);
        setFoundPath([]);
        setSelectedNodeId(null);
      } else {
        const path = findShortestPath(graphNodes, graphEdges, pathSource, nodeId);
        setFoundPath(path);
        setSelectedNodeId(nodeId);
        if (path.length === 0) setPathNotFound(true);
      }
      return true; // handled
    }
    return false; // not in path finder mode, caller should handle
  }, [pathFinderMode, pathSource, graphNodes, graphEdges]);

  const resetPathFinder = useCallback(() => {
    setPathSource(null);
    setFoundPath([]);
  }, []);

  const togglePathFinderMode = useCallback((selectedNodeId) => {
    const next = !pathFinderMode;
    setPathFinderMode(next);
    if (next && selectedNodeId) setPathSource(selectedNodeId);
  }, [pathFinderMode]);

  return {
    pathFinderMode,
    pathSource,
    foundPath,
    pathNotFound,
    handleNodeSelect,
    resetPathFinder,
    togglePathFinderMode,
  };
}
