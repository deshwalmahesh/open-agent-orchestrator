"""Run-scoped IDs go in `RunnableConfig["configurable"]`, NOT here."""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
