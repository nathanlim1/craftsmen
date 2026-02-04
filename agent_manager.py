import json
from typing import Annotated, Dict, List, Optional, Sequence, Tuple, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent_subbuilder import SubBuilder
from retrieval import get_local_context
from schemas import ChunkResultSchema, ChunkTaskSchema


class AgentManager:
    def __init__(self, model: str) -> None:
        self.model = model

    class ManagerState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], add_messages]
        iterations: int

    def run(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: Optional[List[str]],
        max_blocks: int,
        subbuilder: SubBuilder,
    ) -> Tuple[List[Dict[str, int]], List[Dict[str, int]], Optional[List[str]]]:
        ledger_ops: List[Dict[str, int]] = []
        summaries: List[str] = []
        chosen_palette: Optional[List[str]] = palette
        completed: List[str] = []

        def build_tools():
            @tool("dispatch_chunk")
            def dispatch_chunk_tool(
                chunk_id: str,
                bounds_min: List[int],
                bounds_max: List[int],
                role: str,
                target_connections: List[str],
                palette: Optional[List[str]] = None,
                max_ops: Optional[int] = None,
                padding: Optional[int] = None,
            ):
                """Dispatch a sub-agent to build a chunk and return its results."""
                nonlocal chosen_palette
                if palette:
                    chosen_palette = palette
                if not chosen_palette:
                    return {"ok": False, "error": "Palette not set."}

                task = ChunkTaskSchema(
                    chunk_id=chunk_id,
                    bounds_min=bounds_min,
                    bounds_max=bounds_max,
                    role=role,
                    dependencies=[],
                    target_connections=target_connections,
                )
                local_context = get_local_context(
                    client=subbuilder.client,
                    bounds_min=tuple(bounds_min),
                    bounds_max=tuple(bounds_max),
                    padding=padding or 1,
                    ledger_ops=ledger_ops,
                )
                build_spec = {
                    "style_rules": [],
                    "structure_notes": [],
                    "palette": chosen_palette,
                    "max_ops_per_chunk": max_ops or 200,
                    "padding": padding or 1,
                }
                result: ChunkResultSchema = subbuilder.build_chunk(
                    task=task,
                    build_spec=build_spec,
                    local_context=local_context,
                    palette=chosen_palette,
                    max_ops=max_ops or 200,
                )
                if not result.success:
                    return {"ok": False, "error": result.error or "Chunk failed."}

                placements = [
                    {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                    for op in result.placements
                ]
                removals = [
                    {"x": op.x, "y": op.y, "z": op.z, "block": op.block}
                    for op in result.removals
                ]
                ledger_ops.extend(placements)
                ledger_ops.extend(removals)
                completed.append(chunk_id)
                summaries.append(result.summary)
                return {
                    "ok": True,
                    "chunk_id": chunk_id,
                    "summary": result.summary,
                    "placements": placements,
                    "removals": removals,
                    "palette": chosen_palette,
                }

            @tool("get_progress")
            def get_progress_tool():
                """Return current manager progress and palette selection."""
                return {
                    "completed_chunks": completed,
                    "summaries": summaries,
                    "ledger_ops_count": len(ledger_ops),
                    "palette": chosen_palette,
                }

            return [dispatch_chunk_tool, get_progress_tool]

        tools = build_tools()
        tool_node = ToolNode(tools)
        model = ChatOpenAI(model=self.model).bind_tools(tools)

        def call_model(state: AgentManager.ManagerState):
            system_text, user_text = self._compose_prompt(
                prompt, bounds_min, bounds_max, chosen_palette, max_blocks, summaries
            )
            response = model.invoke(
                [SystemMessage(content=system_text), HumanMessage(content=user_text)]
                + list(state["messages"])
            )
            print("\n[Manager LLM Response]")
            print(f"content: {response.content}")
            if getattr(response, "tool_calls", None):
                print(f"tool_calls: {json.dumps(response.tool_calls, indent=2)}")
            print("[/Manager LLM Response]\n")
            return {"messages": [response], "iterations": state["iterations"] + 1}

        def should_continue(state: AgentManager.ManagerState) -> str:
            last_message = state["messages"][-1]
            if state["iterations"] >= 10:
                return "end"
            if getattr(last_message, "tool_calls", None):
                return "continue"
            return "end"

        workflow = StateGraph(AgentManager.ManagerState)
        workflow.add_node("agent", call_model)
        workflow.add_node("tools", tool_node)
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent", should_continue, {"continue": "tools", "end": END}
        )
        workflow.add_edge("tools", "agent")
        graph = workflow.compile()

        graph.invoke({"messages": [], "iterations": 0})
        return ledger_ops, summaries, chosen_palette

    def _compose_prompt(
        self,
        prompt: str,
        bounds_min: Tuple[int, int, int],
        bounds_max: Tuple[int, int, int],
        palette: Optional[List[str]],
        max_blocks: int,
        summaries: List[str],
    ) -> Tuple[str, str]:
        palette_text = ", ".join(palette) if palette else "none (manager must choose)"
        system_text = (
            "You are a build manager orchestrating sequential chunk builds. "
            "Use tools to dispatch one chunk at a time. "
            "Break the build into smaller substeps (foundation, walls, roof, openings, details) "
            "and dispatch chunks that correspond to these substeps in order. "
            "Use the global bounds to anchor vertical placement: "
            "floor/foundation should be at bounds_min.y, walls should start at bounds_min.y+1, "
            "and roof should be at or above bounds_min.y+4 (depending on wall height). "
            "Do not float the entire structure above bounds_min.y unless explicitly requested. "
            "Always keep chunk bounds within the global bounds and make sure your intended "
            "placements fit entirely inside the chunk bounds you choose. "
            "Never ask the sub-agent to place blocks outside its chunk bounds. "
            "If no palette is provided, choose a coherent minecraft:* palette "
            "and pass it to dispatch_chunk. "
            "Be proactive: if the build is missing major components (roof, walls, floor, openings, details), "
            "you must dispatch more chunks until they are complete. "
            "Only stop when the build is structurally complete and visually coherent for the prompt. "
            "When done, respond with a JSON object: {\"summary\": \"...\"}."
        )
        user_text = (
            f"Build request: {prompt}\n"
            f"Global bounds: min={bounds_min} max={bounds_max}\n"
            f"Palette (optional): {palette_text}\n"
            f"Max blocks total: {max_blocks}\n"
            f"Completed chunk summaries: {json.dumps(summaries)}\n"
            "Decide the next chunk and call dispatch_chunk, or finish."
        )
        return system_text, user_text
