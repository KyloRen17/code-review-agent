from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from .nodes import ReviewPipeline
from .state import GraphState

_NODE_ORDER = [
    "load_input",
    "security_scan",
    "normalize_diff",
    "build_context",
    "select_tools",
    "execute_tools",
    "llm_analyze",
    "validate_findings",
    "generate_report",
    "publish",
]


def build_review_graph(pipeline: ReviewPipeline, checkpointer: BaseCheckpointSaver | None = None):
    builder = StateGraph(GraphState)
    for name in _NODE_ORDER:
        builder.add_node(name, getattr(pipeline, name))
    builder.add_edge(START, _NODE_ORDER[0])
    for a, b in zip(_NODE_ORDER, _NODE_ORDER[1:]):
        builder.add_edge(a, b)
    builder.add_edge(_NODE_ORDER[-1], END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
