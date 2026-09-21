"""Private stdio transport; Codex credentials and raw transport never reach the browser."""

import asyncio
import json
import uuid


class CodexError(Exception):
    pass


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
                    if packet["method"] == "serverRequest/resolved":
                        upstream = str(packet.get("params", {}).get("requestId"))
                        key = self.upstream_requests.pop(upstream, None)
                        if key:
                            self.requests.pop(key, None)
                            self.responding.discard(key)
                            packet["params"]["requestId"] = key
                    self.on_event("codex", packet)
                else:
                    future = self.pending.pop(packet.get("id"), None)
                    if future and not future.done():
                        if "error" in packet:
                            future.set_exception(
                                CodexError(
                                    packet["error"].get(
                                        "message", "Codex request failed"
                                    )
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

    async def close(self):
        self.ready = False
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
