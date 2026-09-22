"""Exact-session model controls; never implicitly resumes or changes defaults."""

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .coding_models import CodingSelection
from .shared_session import SharedSessionError
from .shared_session_commands import SubmissionUncertain


class ThreadSelection(CodingSelection):
    generation: str = Field(min_length=1, max_length=128)
    expectedModel: str | None = Field(max_length=200)
    expectedReasoningEffort: str | None = Field(max_length=20)


class ThreadModels:
    def __init__(self, models, shared, bindings, locks):
        self.models = models
        self.bridge = models.bridge
        self.shared, self.bindings, self.locks = shared, bindings, locks

    async def read(self, thread_id):
        if self.shared.owns(thread_id):
            data = await self.shared.read()
            generation = self.shared.generation
        else:
            data = await self.bridge.request(
                "thread/read", {"threadId": thread_id, "includeTurns": False}
            )
            generation = getattr(self.bridge, "generation", 0)
            data["connected"] = (
                thread_id in self.bindings
                and self.bindings[thread_id] == generation
                and data.get("thread", {}).get("status", {}).get("type")
                not in {"notLoaded", "systemError"}
            )
        thread = data.get("thread", {})
        if thread.get("id") != thread_id:
            raise HTTPException(502, "Codex returned a different session")
        return {
            "threadId": thread_id,
            "scope": "session",
            "generation": str(generation),
            "connected": bool(data.get("connected")),
            "model": thread.get("model"),
            "reasoningEffort": thread.get("reasoningEffort"),
        }

    @staticmethod
    def check(current, body):
        if not current["connected"] or current["generation"] != body.generation:
            raise HTTPException(
                409, "Session connection changed. Refresh model settings."
            )
        if not current["model"]:
            raise HTTPException(
                409, "Current session model is unavailable. Refresh settings."
            )
        if (current["model"], current["reasoningEffort"]) != (
            body.expectedModel,
            body.expectedReasoningEffort,
        ):
            raise HTTPException(409, "Session model changed. Refresh before applying.")

    async def save(self, thread_id, body):
        shared = self.shared.owns(thread_id)
        lock = self.shared.lock if shared else self.locks[thread_id]
        async with lock:
            current = await self.read(thread_id)
            self.check(current, body)
            await self.models.validate(body)
            # Catalog I/O can span an owner/generation/configuration transition.
            current = await self.read(thread_id)
            self.check(current, body)
            if shared != self.shared.owns(thread_id):
                raise HTTPException(
                    409, "Session owner changed. Refresh model settings."
                )
            if shared:
                snapshot = self.shared.snapshot
                self.shared.require_runtime_ready()

                def revalidate():
                    self.shared.require_runtime_ready()
                    value = self.shared.view()
                    thread = value["thread"]
                    if (
                        not self.shared.connected
                        or self.shared.generation != body.generation
                        or self.shared.snapshot.owner != snapshot.owner
                        or (thread.get("model"), thread.get("reasoningEffort"))
                        != (body.expectedModel, body.expectedReasoningEffort)
                    ):
                        raise SharedSessionError(
                            "Session changed; refresh model settings"
                        )

                try:
                    result = await self.shared.commands.update_settings(
                        snapshot.owner,
                        body.model,
                        body.reasoningEffort,
                        body.expectedModel,
                        body.expectedReasoningEffort,
                        revalidate,
                    )
                except SubmissionUncertain as error:
                    raise HTTPException(
                        502,
                        "Model change delivery is uncertain. Refresh to read the actual settings before trying again.",
                    ) from error
                except SharedSessionError as error:
                    raise HTTPException(409, str(error)) from error
                if result.get("applied") is not True:
                    raise HTTPException(
                        409, "Model changed in another client. Refresh settings."
                    )
            else:
                # Native experimental API changes only subsequent-turn settings;
                # generation fencing forbids silently resurrecting a lost binding.
                await self.bridge.request(
                    "thread/settings/update",
                    {
                        "threadId": thread_id,
                        "model": body.model,
                        "effort": body.reasoningEffort,
                    },
                    expected_generation=self.bindings[thread_id],
                )
            actual = await self.read(thread_id)
            return {
                **actual,
                "accepted": True,
                "confirmed": actual["connected"]
                and actual["generation"] == body.generation
                and actual["model"] == body.model
                and actual["reasoningEffort"] == body.reasoningEffort,
            }


def router(models, shared, bindings, locks):
    routes = APIRouter(prefix="/api/codex/threads")
    service = ThreadModels(models, shared, bindings, locks)

    @routes.get("/{thread_id}/model")
    async def read(thread_id: str):
        return await service.read(thread_id)

    @routes.post("/{thread_id}/model")
    async def save(thread_id: str, body: ThreadSelection):
        return await service.save(thread_id, body)

    return routes
