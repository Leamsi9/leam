"""Private stdio transport; Codex credentials and raw transport never reach the browser."""

import asyncio
import json
import uuid


class CodexError(Exception):
    def __init__(self, message, *, rpc_code=None):
        super().__init__(message)
        self.rpc_code = rpc_code


class CodexGenerationError(CodexError):
    """The request was not written because its session binding is stale."""


class CodexClient:
    def __init__(self, on_event, command=None):
        self.on_event = on_event
        self.command = command or ["codex", "app-server", "--stdio"]
        self.process = None
        self.pending = {}
        self.requests = {}
        self.counter = 0
        self.start_lock = asyncio.Lock()
        self.reader = None
        self.drainer = None
        self.ready = False
        self.generation = 0
        self.responding = set()
        self.upstream_requests = {}
        self.dynamic_handlers = {}
        self.dynamic_tasks = set()
        self.dynamic_requests = {}

    async def start(self):
        async with self.start_lock:
            if (
                self.ready
                and self.process
                and self.process.returncode is None
                and self.reader
                and not self.reader.done()
            ):
                return
            await self.close()
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024,
            )
            self.reader = asyncio.create_task(self._read())
            self.drainer = asyncio.create_task(self._drain())
            try:
                await self._request(
                    "initialize",
                    {
                        "clientInfo": {"name": "leam", "version": "0.1.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await self.send({"method": "initialized"})
                self.ready = True
                self.generation += 1
            except BaseException:
                await self.close()
                raise

    async def _dynamic(self, packet, handler, generation):
        try:
            if len(self.dynamic_tasks) > 16:
                raise ValueError("Too many pending ticket tool calls")
            value = await handler(packet["params"])
            result = {"success": value.get("state") == "accepted", "contentItems": [{"type": "inputText", "text": json.dumps(value)}]}
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - contain client-tool failures without leaking private exceptions
            result = {"success": False, "contentItems": [{"type": "inputText", "text": "Ticket delegation failed or is uncertain. Inspect its source-bound receipt; do not invent a new request or claim completion."}]}
        key = str(packet["id"])
        current = self.requests.get(key)
        if generation != self.generation or not current or key in self.responding:
            return
        self.responding.add(key)
        try:
            await self.send({"id": current["_upstreamId"], "result": result})
        except BaseException:
            self.responding.discard(key)
            raise

    def retire_terminal_tools(self, packet):
        """A native terminal turn confirms consumed internal tool responses.

        Dynamic calls do not promise serverRequest/resolved. Never infer an
        approval answer, retire unanswered work, or match only an upstream ID.
        """
        if packet.get("method") != "turn/completed":
            return
        params = packet.get("params")
        if not isinstance(params, dict):
            return
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return
        thread_id, turn_id = params.get("threadId"), turn.get("id")
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or not isinstance(turn_id, str)
            or not turn_id
            or turn.get("status") not in {"completed", "failed", "interrupted"}
        ):
            return
        for key, request in tuple(self.requests.items()):
            source = request.get("params", {})
            if (
                key not in self.responding
                or request.get("_internal") is not True
                or request.get("method") != "item/tool/call"
                or source.get("threadId") != thread_id
                or source.get("turnId") != turn_id
            ):
                continue
            self.requests.pop(key, None)
            self.responding.discard(key)
            upstream = str(request.get("_upstreamId"))
            if self.upstream_requests.get(upstream) == key:
                self.upstream_requests.pop(upstream)
            # Do not cancel the local response task: its stdin drain may still
            # be finishing. The normal done callback retires its task binding.

    async def _drain(self):
        # stderr may contain sensitive diagnostics. Drain without storing it in public events.
        while await self.process.stderr.read(8192):
            pass

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                packet = json.loads(line)
                if "method" in packet:
                    if "id" in packet:
                        packet = self.register_request(packet)
                        handler = self.dynamic_handlers.get(packet.get("params", {}).get("tool"))
                        if packet["method"] == "item/tool/call" and handler:
                            self.requests[packet["id"]]["_internal"] = True
                            task = asyncio.create_task(self._dynamic(packet, handler, self.generation))
                            self.dynamic_tasks.add(task)
                            self.dynamic_requests[packet["id"]] = task
                            task.add_done_callback(self.dynamic_tasks.discard)
                            task.add_done_callback(lambda _, key=packet["id"]: self.dynamic_requests.pop(key, None))
                    if packet["method"] == "serverRequest/resolved":
                        upstream = str(packet.get("params", {}).get("requestId"))
                        key = self.upstream_requests.pop(upstream, None)
                        if key:
                            self.requests.pop(key, None)
                            task = self.dynamic_requests.get(key)
                            if task and key not in self.responding:
                                task.cancel()  # Cancel unactivated work when its native request is withdrawn.
                            self.responding.discard(key)
                            packet["params"]["requestId"] = key
                    self.retire_terminal_tools(packet)
                    self.on_event("codex", packet)
                else:
                    future = self.pending.pop(packet.get("id"), None)
                    if future and not future.done():
                        if "error" in packet:
                            future.set_exception(
                                CodexError(
                                    packet["error"].get(
                                        "message", "Codex request failed"
                                    ),
                                    rpc_code=packet["error"].get("code"),
                                )
                            )
                        else:
                            future.set_result(packet.get("result"))
        except (ValueError, OSError) as error:
            self.on_event(
                "codex.connection", {"state": "error", "error": type(error).__name__}
            )
        finally:
            self.ready = False
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        CodexError("Codex connection closed; reconcile before retrying")
                    )
            self.pending.clear()
            for task in self.dynamic_tasks:
                task.cancel()
            self.requests.clear()
            self.responding.clear()
            self.upstream_requests.clear()
            self.on_event("codex.connection", {"state": "disconnected"})

    async def send(self, packet):
        self.process.stdin.write((json.dumps(packet) + "\n").encode())
        await self.process.stdin.drain()

    async def _request(self, method, params):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 60)
        except TimeoutError as error:
            raise CodexError(
                "Codex request timed out; outcome may be uncertain"
            ) from error
        finally:
            self.pending.pop(request_id, None)

    async def request(self, method, params, *, expected_generation=None):
        await self.start()
        if expected_generation is not None and self.generation != expected_generation:
            raise CodexGenerationError(
                "Codex restarted. Reconnect this thread before sending."
            )
        # No suspension occurs between this check and stdin.write in send().
        return await self._request(method, params)

    async def respond(self, request_id, result):
        packet = self.requests.get(str(request_id))
        if not packet:
            raise CodexError("This request is no longer pending")
        validate_response(packet, result)
        key = str(request_id)
        if key in self.responding:
            raise ValueError("Response already submitted; awaiting Codex confirmation")
        self.responding.add(key)
        try:
            await self.send({"id": packet["_upstreamId"], "result": result})
        except BaseException:
            self.responding.discard(key)
            raise

    def register_request(self, packet):
        key = str(uuid.uuid4())
        self.upstream_requests[str(packet["id"])] = key
        stored = dict(packet, id=key, _upstreamId=packet["id"])
        self.requests[key] = stored
        return {k: v for k, v in stored.items() if not k.startswith("_")}

    async def drain_status(self):
        """Observe only this app-owned transport; never start/restart or interrupt it."""
        if self.process is None:
            return {"idle": not self.pending and not self.requests, "loadedThreads": 0}
        process, generation = self.process, self.generation
        if (
            process.returncode is not None
            or not self.ready
            or not self.reader
            or self.reader.done()
        ):
            return {
                "idle": False,
                "reason": "Native Coding transport state is uncertain",
            }
        if self.requests:
            return {
                "idle": False,
                "reason": "Native Coding awaits an approval or answer. End maintenance, answer the existing request, then prepare maintenance again.",
            }
        if (
            self.pending
            or self.responding
            or any(not task.done() for task in self.dynamic_tasks)
        ):
            return {"idle": False, "reason": "Native Coding has pending requests"}
        seen, cursors, cursor = set(), set(), None
        try:
            async with asyncio.timeout(15):
                while True:
                    result = await self._request(
                        "thread/loaded/list", {"cursor": cursor, "limit": 100}
                    )
                    rows = result.get("data")
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("Invalid native loaded-thread response")
                    for key in rows:
                        if (
                            not isinstance(key, str)
                            or not key
                            or len(key) > 128
                            or key in seen
                            or len(seen) >= 1000
                        ):
                            raise ValueError("Invalid native loaded-thread identity")
                        seen.add(key)
                        item = await self._request(
                            "thread/read", {"threadId": key, "includeTurns": False}
                        )
                        thread = item.get("thread", {})
                        if (
                            thread.get("id") != key
                            or thread.get("status", {}).get("type") != "idle"
                        ):
                            return {
                                "idle": False,
                                "reason": "Native Coding has active or uncertain work",
                            }
                    cursor = result.get("nextCursor")
                    if cursor is None:
                        break
                    if (
                        not isinstance(cursor, str)
                        or not cursor
                        or len(cursor) > 4096
                        or cursor in cursors
                    ):
                        raise ValueError("Invalid native loaded-thread cursor")
                    cursors.add(cursor)
                if (
                    self.process is not process
                    or self.generation != generation
                    or not self.ready
                    or self.pending
                    or self.requests
                    or self.responding
                    or any(not task.done() for task in self.dynamic_tasks)
                ):
                    raise ValueError("Native Coding changed during observation")
                return {"idle": True, "loadedThreads": len(seen)}
        except (CodexError, ValueError, TypeError, AttributeError, TimeoutError):
            return {
                "idle": False,
                "reason": "Native Coding idle state could not be verified",
            }

    async def close(self):
        self.ready = False
        retiring = self.dynamic_tasks - {asyncio.current_task()}
        for task in retiring:
            task.cancel()
        await asyncio.gather(*retiring, return_exceptions=True)
        self.dynamic_tasks.difference_update(retiring)
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in [self.reader, self.drainer]:
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in [self.reader, self.drainer] if task),
            return_exceptions=True,
        )


def validate_response(packet, result):
    """Only expose responses with deliberate UI support; never grant broader scope."""
    if not isinstance(result, dict):
        raise ValueError("Response must be an object")
    method = packet["method"]
    if method in [
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    ]:
        if set(result) != {"decision"} or result["decision"] not in [
            "accept",
            "decline",
            "cancel",
        ]:
            raise ValueError("Choose allow once, decline, or cancel")
        return
    if method == "item/tool/requestUserInput":
        questions = packet.get("params", {}).get("questions", [])
        answers = result.get("answers")
        if (
            set(result) != {"answers"}
            or not isinstance(answers, dict)
            or set(answers) != {q["id"] for q in questions}
        ):
            raise ValueError("Answer every requested question")
        for answer in answers.values():
            if (
                not isinstance(answer, dict)
                or set(answer) != {"answers"}
                or not isinstance(answer["answers"], list)
                or not answer["answers"]
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in answer["answers"]
                )
            ):
                raise ValueError("Each question needs a nonempty text answer")
        return
    raise ValueError("This Codex request type is not supported by this client yet")
