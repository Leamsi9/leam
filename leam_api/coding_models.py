"""Validated per-thread defaults; never changes a resumed/shared Codex session."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class CodingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="gpt-5.6-sol", min_length=1, max_length=200)
    reasoningEffort: str = Field(default="medium", min_length=1, max_length=20)


class CodingModels:
    key = "coding-model-defaults"

    def __init__(self, store, bridge):
        self.store, self.bridge = store, bridge

    def selection(self):
        return CodingSelection.model_validate(self.store.get(self.key) or {})

    async def validate(self, selection):
        cursor = None
        for _ in range(10):
            page = await self.bridge.request(
                "model/list", {"limit": 100, "cursor": cursor}
            )
            for model in page.get("data", []):
                if model.get("hidden") or model.get("model") != selection.model:
                    continue
                efforts = {
                    item.get("reasoningEffort")
                    for item in model.get("supportedReasoningEfforts", [])
                }
                if selection.reasoningEffort not in efforts:
                    raise HTTPException(
                        422,
                        "This reasoning effort is not supported by the selected coding model",
                    )
                return
            cursor = page.get("nextCursor")
            if not cursor:
                raise HTTPException(
                    422,
                    "The selected coding model is unavailable. Choose an available model in Settings.",
                )
        raise HTTPException(502, "Coding model catalog exceeded its page limit")

    async def start_parameters(self, cwd):
        selected = self.selection()
        await self.validate(selected)
        return {
            "cwd": str(cwd),
            "model": selected.model,
            "config": {"model_reasoning_effort": selected.reasoningEffort},
        }


def router(models):
    routes = APIRouter(prefix="/api/settings")

    @routes.get("/coding-model")
    async def selection():
        return models.selection()

    @routes.post("/coding-model")
    async def save(body: CodingSelection):
        await models.validate(body)
        models.store.set(models.key, body.model_dump())
        return {"saved": True, **body.model_dump()}

    return routes
