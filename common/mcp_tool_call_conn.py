#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import asyncio
import logging
import threading
import time  # WNC: Added for latency measurement
import uuid  # WNC: Added for task correlation IDs
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from string import Template
from typing import Any, Literal, Protocol

from typing_extensions import override

from common.constants import MCPServerType
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

MCPTaskType = Literal["list_tools", "tool_call"]
MCPTask = tuple[MCPTaskType, dict[str, Any], asyncio.Queue[Any]]


class ToolCallSession(Protocol):
    def tool_call(self, name: str, arguments: dict[str, Any]) -> str: ...


class MCPToolCallSession(ToolCallSession):
    _ALL_INSTANCES: weakref.WeakSet["MCPToolCallSession"] = weakref.WeakSet()

    def __init__(self, mcp_server: Any, server_variables: dict[str, Any] | None = None) -> None:
        self.__class__._ALL_INSTANCES.add(self)

        self._mcp_server = mcp_server
        self._server_variables = server_variables or {}
        self._queue = asyncio.Queue()
        self._close = False

        self._event_loop = asyncio.new_event_loop()
        self._thread_pool = ThreadPoolExecutor(max_workers=1)
        self._thread_pool.submit(self._event_loop.run_forever)

        # WNC: Log session initialization with sanitized URL
        url_sanitized = self._mcp_server.url.split('@')[-1] if '@' in self._mcp_server.url else self._mcp_server.url
        logging.info(f"WNC-MCP [CLIENT] session_init server_id={self._mcp_server.id} server_type={self._mcp_server.server_type} url={url_sanitized} operation=session_init")

        asyncio.run_coroutine_threadsafe(self._mcp_server_loop(), self._event_loop)

    async def _mcp_server_loop(self) -> None:
        url = self._mcp_server.url.strip()
        raw_headers: dict[str, str] = self._mcp_server.headers or {}
        headers: dict[str, str] = {}

        for h, v in raw_headers.items():
            nh = Template(h).safe_substitute(self._server_variables)
            nv = Template(v).safe_substitute(self._server_variables)
            if nh.strip() and nv.strip().strip("Bearer"):
                headers[nh] = nv

        if self._mcp_server.server_type == MCPServerType.SSE:
            # SSE transport
            try:
                async with sse_client(url, headers) as stream:
                    async with ClientSession(*stream) as client_session:
                        try:
                            await asyncio.wait_for(client_session.initialize(), timeout=5)
                            logging.info("client_session initialized successfully")
                            # WNC: Log successful SSE connection
                            logging.info(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=sse status=success operation=session_connected")
                            await self._process_mcp_tasks(client_session)
                        except asyncio.TimeoutError:
                            msg = f"Timeout initializing client_session for server {self._mcp_server.id}"
                            logging.error(msg)
                            # WNC: Log SSE connection timeout
                            logging.error(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=sse status=timeout timeout_sec=5 operation=session_connected")
                            await self._process_mcp_tasks(None, msg)
                        except asyncio.CancelledError:
                            logging.warning(f"SSE transport MCP session cancelled for server {self._mcp_server.id}")
                            # WNC: Log SSE session cancellation
                            logging.warning(f"WNC-MCP [CLIENT] session_cancelled server_id={self._mcp_server.id} transport=sse operation=session_cancelled")
                            return
            except Exception as e:
                msg = "Connection failed (possibly due to auth error). Please check authentication settings first"
                # WNC: Log SSE connection error
                logging.error(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=sse status=error error_type={type(e).__name__} operation=session_connected", exc_info=True)
                await self._process_mcp_tasks(None, msg)

        elif self._mcp_server.server_type == MCPServerType.STREAMABLE_HTTP:
            # Streamable HTTP transport
            try:
                async with streamablehttp_client(url, headers) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as client_session:
                        try:
                            await asyncio.wait_for(client_session.initialize(), timeout=5)
                            logging.info("client_session initialized successfully")
                            # WNC: Log successful streamable-http connection
                            logging.info(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=streamable-http status=success operation=session_connected")
                            await self._process_mcp_tasks(client_session)
                        except asyncio.TimeoutError:
                            msg = f"Timeout initializing client_session for server {self._mcp_server.id}"
                            logging.error(msg)
                            # WNC: Log streamable-http connection timeout
                            logging.error(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=streamable-http status=timeout timeout_sec=5 operation=session_connected")
                            await self._process_mcp_tasks(None, msg)
                        except asyncio.CancelledError:
                            logging.warning(f"STREAMABLE_HTTP MCP session cancelled for server {self._mcp_server.id}")
                            # WNC: Log streamable-http session cancellation
                            logging.warning(f"WNC-MCP [CLIENT] session_cancelled server_id={self._mcp_server.id} transport=streamable-http operation=session_cancelled")
                            return
            except Exception as e:
                logging.exception(e)
                # WNC: Log streamable-http connection error
                logging.error(f"WNC-MCP [CLIENT] session_connected server_id={self._mcp_server.id} transport=streamable-http status=error error_type={type(e).__name__} operation=session_connected", exc_info=True)
                msg = "Connection failed (possibly due to auth error). Please check authentication settings first"
                await self._process_mcp_tasks(None, msg)

        else:
            # WNC: Log unsupported server type
            logging.error(f"WNC-MCP [CLIENT] session_error server_id={self._mcp_server.id} server_type={self._mcp_server.server_type} status=unsupported_type operation=session_error")
            await self._process_mcp_tasks(None,
                                          f"Unsupported MCP server type: {self._mcp_server.server_type}, id: {self._mcp_server.id}")

    async def _process_mcp_tasks(self, client_session: ClientSession | None, error_message: str | None = None) -> None:
        while not self._close:
            try:
                mcp_task, arguments, result_queue = await asyncio.wait_for(self._queue.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # WNC: Generate task ID and start timing
            task_id = str(uuid.uuid4())
            start_time = time.time()

            logging.debug(f"Got MCP task {mcp_task} arguments {arguments}")

            # WNC: Create payload summary and log task received
            payload_summary = ""
            if mcp_task == "tool_call":
                tool_name = arguments.get("name", "unknown")
                arg_count = len(arguments.get("arguments", {}))
                payload_summary = f"tool_name={tool_name} arg_count={arg_count}"
            logging.debug(f"WNC-MCP [CLIENT] task_received server_id={self._mcp_server.id} task_id={task_id} task_type={mcp_task} {payload_summary} operation=task_received")

            r: Any = None

            if not client_session or error_message:
                r = ValueError(error_message)
                try:
                    await result_queue.put(r)
                except asyncio.CancelledError:
                    break
                continue

            try:
                if mcp_task == "list_tools":
                    # WNC: Log list_tools call
                    logging.debug(f"WNC-MCP [CLIENT] list_tools_call server_id={self._mcp_server.id} task_id={task_id} operation=list_tools_call")
                    r = await client_session.list_tools()
                elif mcp_task == "tool_call":
                    # WNC: Log call_tool call
                    tool_name = arguments.get("name", "unknown")
                    arg_count = len(arguments.get("arguments", {}))
                    logging.info(f"WNC-MCP [CLIENT] call_tool_call server_id={self._mcp_server.id} task_id={task_id} tool_name={tool_name} arg_count={arg_count} operation=call_tool_call")
                    r = await client_session.call_tool(**arguments)
                else:
                    r = ValueError(f"Unknown MCP task {mcp_task}")
            except Exception as e:
                # WNC: Log task error
                latency_ms = (time.time() - start_time) * 1000
                logging.error(f"WNC-MCP [CLIENT] task_completed server_id={self._mcp_server.id} task_id={task_id} task_type={mcp_task} status=error latency_ms={latency_ms:.2f} error_type={type(e).__name__} operation=task_completed", exc_info=True)
                r = e
            except asyncio.CancelledError:
                break

            # WNC: Log successful task completion
            if not isinstance(r, Exception):
                latency_ms = (time.time() - start_time) * 1000
                result_summary = ""
                if mcp_task == "list_tools" and hasattr(r, 'tools'):
                    tool_names = [t.name for t in r.tools]
                    tool_details = []
                    for t in r.tools:
                        # WNC: Sanitize description - replace newlines/tabs with spaces, then truncate to 100 chars
                        desc = (t.description or "").replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')[:100]
                        tool_details.append(f"{t.name}:{desc}")
                    tool_details_str = " | ".join(tool_details)
                    result_summary = f"tool_count={len(r.tools)} tool_names={tool_names} tool_details='{tool_details_str}'"
                elif mcp_task == "tool_call" and hasattr(r, 'content'):
                    content_len = sum(len(getattr(c, 'text', '')) for c in r.content if hasattr(c, 'text'))
                    result_summary = f"result_len={content_len}"
                logging.info(f"WNC-MCP [CLIENT] task_completed server_id={self._mcp_server.id} task_id={task_id} task_type={mcp_task} status=success latency_ms={latency_ms:.2f} {result_summary} operation=task_completed")

            try:
                await result_queue.put(r)
            except asyncio.CancelledError:
                break

    async def _call_mcp_server(self, task_type: MCPTaskType, request_timeout: float | int = 8, **kwargs) -> Any:
        if self._close:
            raise ValueError("Session is closed")

        results = asyncio.Queue()
        await self._queue.put((task_type, kwargs, results))

        try:
            result: CallToolResult | Exception = await asyncio.wait_for(results.get(), timeout=request_timeout)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"MCP task '{task_type}' timeout after {request_timeout}s")
        except Exception:
            raise

    async def _call_mcp_tool(self, name: str, arguments: dict[str, Any], request_timeout: float | int = 10) -> str:
        result: CallToolResult = await self._call_mcp_server("tool_call", name=name, arguments=arguments,
                                                             request_timeout=request_timeout)

        if result.isError:
            return f"MCP server error: {result.content}"

        # For now, we only support text content
        if isinstance(result.content[0], TextContent):
            return result.content[0].text
        else:
            return f"Unsupported content type {type(result.content)}"

    async def _get_tools_from_mcp_server(self, request_timeout: float | int = 8) -> list[Tool]:
        try:
            result: ListToolsResult = await self._call_mcp_server("list_tools", request_timeout=request_timeout)
            return result.tools
        except Exception:
            raise

    def get_tools(self, timeout: float | int = 10) -> list[Tool]:
        if self._close:
            raise ValueError("Session is closed")

        future = asyncio.run_coroutine_threadsafe(self._get_tools_from_mcp_server(request_timeout=timeout), self._event_loop)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            msg = f"Timeout when fetching tools from MCP server: {self._mcp_server.id} (timeout={timeout})"
            logging.error(msg)
            # WNC: Log get_tools timeout
            logging.error(f"WNC-MCP [CLIENT] get_tools server_id={self._mcp_server.id} status=timeout timeout_sec={timeout} operation=get_tools")
            raise RuntimeError(msg)
        except Exception as e:
            logging.exception(f"Error fetching tools from MCP server: {self._mcp_server.id}")
            # WNC: Log get_tools error
            logging.error(f"WNC-MCP [CLIENT] get_tools server_id={self._mcp_server.id} status=error error_type={type(e).__name__} operation=get_tools", exc_info=True)
            raise

    @override
    def tool_call(self, name: str, arguments: dict[str, Any], timeout: float | int = 10) -> str:
        if self._close:
            return "Error: Session is closed"

        future = asyncio.run_coroutine_threadsafe(self._call_mcp_tool(name, arguments), self._event_loop)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            logging.error(f"Timeout calling tool '{name}' on MCP server: {self._mcp_server.id} (timeout={timeout})")
            # WNC: Log tool_call timeout
            logging.error(f"WNC-MCP [CLIENT] tool_call server_id={self._mcp_server.id} tool_name={name} status=timeout timeout_sec={timeout} operation=tool_call")
            return f"Timeout calling tool '{name}' (timeout={timeout})."
        except Exception as e:
            logging.exception(f"Error calling tool '{name}' on MCP server: {self._mcp_server.id}")
            # WNC: Log tool_call error
            logging.error(f"WNC-MCP [CLIENT] tool_call server_id={self._mcp_server.id} tool_name={name} status=error error_type={type(e).__name__} operation=tool_call", exc_info=True)
            return f"Error calling tool '{name}': {e}."

    async def close(self) -> None:
        if self._close:
            return

        self._close = True

        # WNC: Log session close with pending tasks count
        pending_tasks = self._queue.qsize()
        logging.info(f"WNC-MCP [CLIENT] session_close server_id={self._mcp_server.id} pending_tasks={pending_tasks} operation=session_close")

        while not self._queue.empty():
            try:
                _, _, result_queue = self._queue.get_nowait()
                try:
                    await result_queue.put(asyncio.CancelledError("Session is closing"))
                except Exception:
                    pass
            except asyncio.QueueEmpty:
                break
            except Exception:
                break

        try:
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
        except Exception:
            pass

        try:
            self._thread_pool.shutdown(wait=True)
        except Exception:
            pass

        self.__class__._ALL_INSTANCES.discard(self)

    def close_sync(self, timeout: float | int = 5) -> None:
        if not self._event_loop.is_running():
            logging.warning(f"Event loop already stopped for {self._mcp_server.id}")
            # WNC: Log already stopped event loop
            logging.warning(f"WNC-MCP [CLIENT] session_close server_id={self._mcp_server.id} status=already_stopped operation=session_close")
            return

        try:
            future = asyncio.run_coroutine_threadsafe(self.close(), self._event_loop)
            try:
                future.result(timeout=timeout)
            except FuturesTimeoutError:
                logging.error(f"Timeout while closing session for server {self._mcp_server.id} (timeout={timeout})")
                # WNC: Log close timeout
                logging.error(f"WNC-MCP [CLIENT] session_close server_id={self._mcp_server.id} status=timeout timeout_sec={timeout} operation=session_close")
            except Exception as e:
                logging.exception(f"Unexpected error during close_sync for {self._mcp_server.id}")
                # WNC: Log close error
                logging.error(f"WNC-MCP [CLIENT] session_close server_id={self._mcp_server.id} status=error error_type={type(e).__name__} operation=session_close", exc_info=True)
        except Exception as e:
            logging.exception(f"Exception while scheduling close for server {self._mcp_server.id}")
            # WNC: Log scheduling error
            logging.error(f"WNC-MCP [CLIENT] session_close server_id={self._mcp_server.id} status=scheduling_error error_type={type(e).__name__} operation=session_close", exc_info=True)


def close_multiple_mcp_toolcall_sessions(sessions: list[MCPToolCallSession]) -> None:
    logging.info(f"Want to clean up {len(sessions)} MCP sessions")
    # WNC: Log batch cleanup start
    logging.info(f"WNC-MCP [CLIENT] batch_cleanup_start session_count={len(sessions)} operation=batch_cleanup_start")

    async def _gather_and_stop() -> None:
        try:
            await asyncio.gather(*[s.close() for s in sessions if s is not None], return_exceptions=True)
        except Exception as e:
            logging.exception("Exception during MCP session cleanup")
            # WNC: Log batch cleanup error
            logging.error(f"WNC-MCP [CLIENT] batch_cleanup error_type={type(e).__name__} operation=batch_cleanup", exc_info=True)
        finally:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

    try:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        asyncio.run_coroutine_threadsafe(_gather_and_stop(), loop).result()
        thread.join()
    except Exception as e:
        logging.exception("Exception during MCP session cleanup thread management")
        # WNC: Log thread management error
        logging.error(f"WNC-MCP [CLIENT] batch_cleanup error_type={type(e).__name__} operation=batch_cleanup", exc_info=True)

    # WNC: Log batch cleanup completion
    remaining = len(list(MCPToolCallSession._ALL_INSTANCES))
    logging.info(f"WNC-MCP [CLIENT] batch_cleanup_done closed_count={len(sessions)} remaining_global={remaining} operation=batch_cleanup_done")

    logging.info(
        f"{len(sessions)} MCP sessions has been cleaned up. {len(list(MCPToolCallSession._ALL_INSTANCES))} in global context.")


def shutdown_all_mcp_sessions():
    """Gracefully shutdown all active MCPToolCallSession instances."""
    sessions = list(MCPToolCallSession._ALL_INSTANCES)
    if not sessions:
        logging.info("No MCPToolCallSession instances to close.")
        return

    logging.info(f"Shutting down {len(sessions)} MCPToolCallSession instances...")
    close_multiple_mcp_toolcall_sessions(sessions)
    logging.info("All MCPToolCallSession instances have been closed.")


def mcp_tool_metadata_to_openai_tool(mcp_tool: Tool | dict) -> dict[str, Any]:
    if isinstance(mcp_tool, dict):
        return {
            "type": "function",
            "function": {
                "name": mcp_tool["name"],
                "description": mcp_tool["description"],
                "parameters": mcp_tool["inputSchema"],
            },
        }

    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "parameters": mcp_tool.inputSchema,
        },
    }
