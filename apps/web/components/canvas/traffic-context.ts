"use client";

import { createContext, useContext } from "react";

import type { NodeTraffic } from "@/lib/api";

/** Traffic drawn on the canvas (Phase 10.2), shared with the stations and
 *  lines without going through the graph, so polling it never rebuilds
 *  the drawing: per station its counts, and the lines that carried any
 *  ("source>target"). Null while Traffic is off. */
export type TrafficView = {
  nodes: Record<string, NodeTraffic>;
  edges: ReadonlySet<string>;
} | null;

export const TrafficContext = createContext<TrafficView>(null);

export const useTrafficView = () => useContext(TrafficContext);
