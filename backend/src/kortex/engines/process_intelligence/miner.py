"""
KORTEX Process Miner & Graph Constructor.

Implements mathematically guaranteed, deterministic Directly-Follows Graph (DFG)
construction and trace variant extraction from observed execution records.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from kortex.engines.process_intelligence.exceptions import GraphBoundingError
from kortex.engines.process_intelligence.interfaces import IProcessMiner
from kortex.engines.process_intelligence.models import (
    NODE_END_CANCELLED,
    NODE_END_FAILED,
    NODE_END_SUCCESS,
    NODE_OTHER_STEPS,
    NODE_START,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    TERMINAL_NODES,
    ProcessEdge,
    ProcessGraph,
    ProcessGraphMetadata,
    ProcessNode,
    RawInstanceTrace,
    TraceVariant,
    VariantListResult,
)

MAX_NODES_GUARANTEE = 100
MAX_EDGES_GUARANTEE = 500
MAX_NON_TERMINAL_STEPS = 95  # 4 virtual terminals + 1 [__OTHER_STEPS__] + 95 steps = 100 max


class ProcessMiner(IProcessMiner):
    """Deterministic, bounded process mining engine."""

    def build_directly_follows_graph(
        self,
        definition_id: str,
        traces: list[RawInstanceTrace],
        total_matching: int,
        window_clamped: bool,
        version_analyzed: str | None,
        available_versions: list[str],
    ) -> ProcessGraph:
        """Construct a bounded directly-follows graph from observed instance traces.

        Guarantees mathematically that returned graph has <= 100 nodes and <= 500 edges.
        """
        # 1. Tally raw transitions and node visitations from observed traces
        raw_transitions: dict[tuple[str, str], int] = defaultdict(int)
        raw_latencies: dict[tuple[str, str], list[float]] = defaultdict(list)
        node_visitations: dict[str, int] = defaultdict(int)
        terminal_nodes_present: set[str] = set()

        for trace in traces:
            steps = trace.steps
            if not steps:
                continue

            # Start edge
            first_step = steps[0].step_id
            raw_transitions[(NODE_START, first_step)] += 1
            node_visitations[first_step] += 1

            # Step-to-step transitions
            for i in range(len(steps) - 1):
                curr_s = steps[i]
                next_s = steps[i + 1]
                src = curr_s.step_id
                dst = next_s.step_id

                raw_transitions[(src, dst)] += 1
                node_visitations[dst] += 1

                # Calculate latency (target started_at - source completed_at)
                if curr_s.completed_at and next_s.started_at:
                    delta = (next_s.started_at - curr_s.completed_at).total_seconds() * 1000.0
                    raw_latencies[(src, dst)].append(max(0.0, delta))
                elif curr_s.started_at and next_s.started_at:
                    delta = (next_s.started_at - curr_s.started_at).total_seconds() * 1000.0
                    raw_latencies[(src, dst)].append(max(0.0, delta))

            # Terminal edge
            last_step = steps[-1]
            terminal_target: str | None = None
            if trace.state == STATE_COMPLETED:
                terminal_target = NODE_END_SUCCESS
            elif trace.state == STATE_FAILED:
                terminal_target = NODE_END_FAILED
            elif trace.state == STATE_CANCELLED:
                terminal_target = NODE_END_CANCELLED

            if terminal_target:
                raw_transitions[(last_step.step_id, terminal_target)] += 1
                terminal_nodes_present.add(terminal_target)

        # 2. Rank non-terminal step nodes and bound to MAX_NON_TERMINAL_STEPS (95)
        all_step_ids = sorted(node_visitations.keys())
        # Sort key: (-visitations, step_id ascending)
        ranked_step_ids = sorted(all_step_ids, key=lambda s: (-node_visitations[s], s))

        nodes_collapsed = False
        collapsed_node_count = 0
        kept_step_set: set[str] = set()

        if len(ranked_step_ids) <= MAX_NON_TERMINAL_STEPS:
            kept_step_set = set(ranked_step_ids)
        else:
            nodes_collapsed = True
            kept_steps = ranked_step_ids[:MAX_NON_TERMINAL_STEPS]
            kept_step_set = set(kept_steps)
            collapsed_node_count = len(ranked_step_ids) - MAX_NON_TERMINAL_STEPS

        # Node remapping helper
        def remap_node(n: str) -> str:
            if n in TERMINAL_NODES or n == NODE_START:
                return n
            if n in kept_step_set:
                return n
            return NODE_OTHER_STEPS

        # 3. Remap edges and aggregate counts/latencies
        remapped_transitions: dict[tuple[str, str], int] = defaultdict(int)
        remapped_latencies: dict[tuple[str, str], list[float]] = defaultdict(list)

        for (src, dst), count in raw_transitions.items():
            r_src = remap_node(src)
            r_dst = remap_node(dst)
            remapped_transitions[(r_src, r_dst)] += count
            if (src, dst) in raw_latencies:
                remapped_latencies[(r_src, r_dst)].extend(raw_latencies[(src, dst)])

        # 4. Bound edges to MAX_EDGES_GUARANTEE (500)
        all_remapped_edges = list(remapped_transitions.keys())
        # Sort key: (-count, src ascending, dst ascending)
        ranked_edges = sorted(
            all_remapped_edges,
            key=lambda e: (-remapped_transitions[e], e[0], e[1]),
        )

        edges_pruned = False
        pruned_edge_count = 0
        pruned_transitions_total = 0
        retained_edges_list = ranked_edges

        if len(ranked_edges) > MAX_EDGES_GUARANTEE:
            edges_pruned = True
            retained_edges_list = ranked_edges[:MAX_EDGES_GUARANTEE]
            pruned_edges = ranked_edges[MAX_EDGES_GUARANTEE:]
            pruned_edge_count = len(pruned_edges)
            pruned_transitions_total = sum(remapped_transitions[e] for e in pruned_edges)

        # 5. Compute outgoing totals per source for probability normalization
        outbound_totals: dict[str, int] = defaultdict(int)
        for src, dst in retained_edges_list:
            outbound_totals[src] += remapped_transitions[(src, dst)]

        # 6. Build final ProcessEdge models
        final_edges: list[ProcessEdge] = []
        for src, dst in retained_edges_list:
            count = remapped_transitions[(src, dst)]
            denom = outbound_totals.get(src, 0)
            prob = round(count / denom, 4) if denom > 0 else 0.0

            lats = remapped_latencies.get((src, dst), [])
            avg_lat = round(sum(lats) / len(lats), 2) if lats else 0.0
            med_lat = round(sorted(lats)[len(lats) // 2], 2) if lats else 0.0

            final_edges.append(
                ProcessEdge(
                    source=src,
                    target=dst,
                    transition_count=count,
                    transition_probability=prob,
                    avg_latency_ms=avg_lat,
                    median_latency_ms=med_lat,
                )
            )

        # 7. Build final ProcessNode models
        final_nodes: list[ProcessNode] = []

        # Start node
        if any(e.source == NODE_START for e in final_edges):
            start_visitations = sum(e.transition_count for e in final_edges if e.source == NODE_START)
            final_nodes.append(
                ProcessNode(
                    id=NODE_START,
                    name="Start",
                    is_approval=False,
                    is_terminal=False,
                    is_grouped=False,
                    total_visitations=start_visitations,
                )
            )

        # Kept real step nodes
        for s_id in sorted(kept_step_set):
            final_nodes.append(
                ProcessNode(
                    id=s_id,
                    name=s_id,
                    is_approval=False,
                    is_terminal=False,
                    is_grouped=False,
                    total_visitations=node_visitations.get(s_id, 0),
                )
            )

        # Collapsed node if present
        if nodes_collapsed:
            collapsed_visitations = sum(node_visitations[s] for s in ranked_step_ids[MAX_NON_TERMINAL_STEPS:])
            final_nodes.append(
                ProcessNode(
                    id=NODE_OTHER_STEPS,
                    name=f"Other Steps ({collapsed_node_count} steps collapsed)",
                    is_approval=False,
                    is_terminal=False,
                    is_grouped=True,
                    total_visitations=collapsed_visitations,
                )
            )

        # Terminal nodes
        for term in (NODE_END_SUCCESS, NODE_END_FAILED, NODE_END_CANCELLED):
            if any(e.target == term for e in final_edges):
                term_visitations = sum(e.transition_count for e in final_edges if e.target == term)
                final_nodes.append(
                    ProcessNode(
                        id=term,
                        name=term.replace("[", "").replace("]", "").replace("_", " ").title(),
                        is_approval=False,
                        is_terminal=True,
                        is_grouped=False,
                        total_visitations=term_visitations,
                    )
                )

        # Hard invariant validation
        if len(final_nodes) > MAX_NODES_GUARANTEE:
            raise GraphBoundingError(
                f"Graph node count {len(final_nodes)} exceeds maximum allowed {MAX_NODES_GUARANTEE}"
            )
        if len(final_edges) > MAX_EDGES_GUARANTEE:
            raise GraphBoundingError(
                f"Graph edge count {len(final_edges)} exceeds maximum allowed {MAX_EDGES_GUARANTEE}"
            )

        metadata = ProcessGraphMetadata(
            sample_size=len(traces),
            total_instances_matching=total_matching,
            is_sampled=total_matching > len(traces),
            nodes_collapsed=nodes_collapsed,
            collapsed_node_count=collapsed_node_count,
            edges_pruned=edges_pruned,
            pruned_edge_count=pruned_edge_count,
            pruned_transitions_total=pruned_transitions_total,
            window_clamped=window_clamped,
            version_analyzed=version_analyzed,
            available_versions=available_versions,
        )

        return ProcessGraph(
            definition_id=definition_id,
            nodes=final_nodes,
            edges=final_edges,
            metadata=metadata,
        )

    def extract_trace_variants(
        self,
        definition_id: str,
        traces: list[RawInstanceTrace],
        limit: int,
        total_matching: int,
        window_clamped: bool,
        version_analyzed: str | None,
        available_versions: list[str],
    ) -> VariantListResult:
        """Extract unique sequential execution paths and their frequency distributions.

        Bounded deterministically to max 50 variants.
        """
        variant_counts: dict[tuple[str, ...], int] = defaultdict(int)
        variant_durations: dict[tuple[str, ...], list[float]] = defaultdict(list)

        for trace in traces:
            path = tuple(s.step_id for s in trace.steps)
            variant_counts[path] += 1
            if trace.state == STATE_COMPLETED and trace.updated_at and trace.created_at:
                dur = (trace.updated_at - trace.created_at).total_seconds() * 1000.0
                if dur >= 0.0:
                    variant_durations[path].append(dur)

        total_traces = len(traces)
        if total_traces == 0:
            metadata = ProcessGraphMetadata(
                sample_size=0,
                total_instances_matching=total_matching,
                is_sampled=False,
                window_clamped=window_clamped,
                version_analyzed=version_analyzed,
                available_versions=available_versions,
            )
            return VariantListResult(
                definition_id=definition_id,
                total_variants_discovered=0,
                returned_variants=[],
                metadata=metadata,
            )

        # Rank variants: (-frequency, path_signature)
        all_paths = list(variant_counts.keys())
        ranked_paths = sorted(
            all_paths,
            key=lambda p: (-variant_counts[p], "->".join(p)),
        )

        clamped_limit = max(1, min(limit, 50))
        top_paths = ranked_paths[:clamped_limit]

        returned_variants: list[TraceVariant] = []
        for path in top_paths:
            freq = variant_counts[path]
            pct = round((freq / total_traces) * 100.0, 2)
            durs = variant_durations.get(path, [])
            avg_dur = round(sum(durs) / len(durs), 2) if durs else 0.0

            # Deterministic hash ID
            sig = "->".join(path)
            var_id = f"var_{hashlib.sha256(sig.encode()).hexdigest()[:10]}"

            returned_variants.append(
                TraceVariant(
                    variant_id=var_id,
                    steps=list(path),
                    frequency=freq,
                    percentage=pct,
                    avg_duration_ms=avg_dur,
                )
            )

        # If there are remaining paths, collapse into synthetic OTHER variant
        if len(ranked_paths) > clamped_limit:
            overflow_paths = ranked_paths[clamped_limit:]
            overflow_freq = sum(variant_counts[p] for p in overflow_paths)
            overflow_pct = round((overflow_freq / total_traces) * 100.0, 2)

            all_overflow_durs: list[float] = []
            for p in overflow_paths:
                all_overflow_durs.extend(variant_durations.get(p, []))
            overflow_avg = round(sum(all_overflow_durs) / len(all_overflow_durs), 2) if all_overflow_durs else 0.0

            returned_variants.append(
                TraceVariant(
                    variant_id="var_other_variants",
                    steps=["[__OTHER_VARIANTS__]"],
                    frequency=overflow_freq,
                    percentage=overflow_pct,
                    avg_duration_ms=overflow_avg,
                )
            )

        metadata = ProcessGraphMetadata(
            sample_size=total_traces,
            total_instances_matching=total_matching,
            is_sampled=total_matching > total_traces,
            window_clamped=window_clamped,
            version_analyzed=version_analyzed,
            available_versions=available_versions,
        )

        return VariantListResult(
            definition_id=definition_id,
            total_variants_discovered=len(ranked_paths),
            returned_variants=returned_variants,
            metadata=metadata,
        )
