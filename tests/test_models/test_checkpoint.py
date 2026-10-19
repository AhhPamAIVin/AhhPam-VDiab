"""Unit tests for SQLModelCheckpointSaver against a tiny real LangGraph graph
(not just calling the saver's methods in isolation) — see checkpoint.py's
docstring for why this hand-rolls LangGraph's checkpoint protocol on
SQLModel instead of the official langgraph-checkpoint-postgres package.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from sqlmodel import Session, select

from codebase.models.agents.checkpoint import (
    AgentCheckpoint,
    AgentCheckpointBlob,
    AgentCheckpointWrite,
    SQLModelCheckpointSaver,
)


class _State(TypedDict):
    messages: Annotated[list, add_messages]
    turn_count: int


def _bump(state: _State) -> dict:
    return {
        "turn_count": state.get("turn_count", 0) + 1,
        "messages": [AIMessage(content=f"turn {state.get('turn_count', 0) + 1}")],
    }


def _build_graph(checkpointer: SQLModelCheckpointSaver):
    graph = StateGraph(_State)
    graph.add_node("bump", _bump)
    graph.set_entry_point("bump")
    graph.add_edge("bump", END)
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_state_persists_and_accumulates_across_invokes_on_the_same_thread(session: Session):
    compiled = _build_graph(SQLModelCheckpointSaver())
    config = {"configurable": {"session": session, "thread_id": "patient-x:doctor-a"}}

    first = await compiled.ainvoke({"messages": [HumanMessage(content="hi")]}, config)
    assert first["turn_count"] == 1
    assert len(first["messages"]) == 2  # human + the node's reply

    second = await compiled.ainvoke({"messages": [HumanMessage(content="again")]}, config)
    assert second["turn_count"] == 2
    assert len(second["messages"]) == 4  # accumulated across both invokes, not reset


@pytest.mark.asyncio
async def test_different_thread_ids_are_fully_isolated(session: Session):
    compiled = _build_graph(SQLModelCheckpointSaver())
    thread_a = {"configurable": {"session": session, "thread_id": "patient-x:doctor-a"}}
    thread_b = {"configurable": {"session": session, "thread_id": "patient-x:doctor-b"}}

    await compiled.ainvoke({"messages": [HumanMessage(content="hi")]}, thread_a)
    await compiled.ainvoke({"messages": [HumanMessage(content="hi again")]}, thread_a)

    result_b = await compiled.ainvoke({"messages": [HumanMessage(content="fresh thread")]}, thread_b)
    assert result_b["turn_count"] == 1  # unaffected by thread_a's two prior invokes
    assert len(result_b["messages"]) == 2


@pytest.mark.asyncio
async def test_get_tuple_returns_none_for_a_thread_never_invoked(session: Session):
    saver = SQLModelCheckpointSaver()
    result = saver.get_tuple({"configurable": {"session": session, "thread_id": "never-invoked"}})
    assert result is None


@pytest.mark.asyncio
async def test_list_returns_checkpoints_newest_first(session: Session):
    compiled = _build_graph(SQLModelCheckpointSaver())
    config = {"configurable": {"session": session, "thread_id": "list-me"}}
    await compiled.ainvoke({"messages": [HumanMessage(content="one")]}, config)
    await compiled.ainvoke({"messages": [HumanMessage(content="two")]}, config)

    saver = SQLModelCheckpointSaver()
    tuples = list(saver.list({"configurable": {"session": session, "thread_id": "list-me"}}))
    assert len(tuples) >= 2
    checkpoint_ids = [t.checkpoint["id"] for t in tuples]
    assert checkpoint_ids == sorted(checkpoint_ids, reverse=True)


@pytest.mark.asyncio
async def test_delete_thread_removes_all_rows_across_all_three_tables(session: Session, monkeypatch):
    compiled = _build_graph(SQLModelCheckpointSaver())
    config = {"configurable": {"session": session, "thread_id": "to-delete"}}
    await compiled.ainvoke({"messages": [HumanMessage(content="hi")]}, config)

    assert session.exec(select(AgentCheckpoint).where(AgentCheckpoint.thread_id == "to-delete")).first() is not None

    # delete_thread has no `config` param (BaseCheckpointSaver's own contract), so
    # it always falls back to the module-level engine rather than the test's
    # injected session — point that module-level engine at this test's DB for
    # the duration of this one call.
    import codebase.models.agents.checkpoint as checkpoint_module

    monkeypatch.setattr(checkpoint_module, "engine", session.get_bind())
    SQLModelCheckpointSaver().delete_thread("to-delete")

    assert session.exec(select(AgentCheckpoint).where(AgentCheckpoint.thread_id == "to-delete")).first() is None
    assert session.exec(select(AgentCheckpointBlob).where(AgentCheckpointBlob.thread_id == "to-delete")).first() is None
    assert (
        session.exec(select(AgentCheckpointWrite).where(AgentCheckpointWrite.thread_id == "to-delete")).first() is None
    )
