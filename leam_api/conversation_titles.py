"""Revisioned Leam display names; execution history stays owned by the runtime."""

import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RenameConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=0, ge=0)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a conversation title")
        return value


class DeleteConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class DeleteCodingConversation(DeleteConversation):
    deleteChildren: Literal[True]
    stopRunning: Literal[True]


class ConversationTitles:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def key(thread_id):
        return "companion.title:" + thread_id

    def overlay(self, items):
        keys = [self.key(item["thread_id"]) for item in items]
        if not keys:
            return
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT key,value FROM settings WHERE key IN ("
                + ",".join("?" for _ in keys)
                + ")",
                keys,
            ).fetchall()
        saved = {row["key"]: json.loads(row["value"]) for row in rows}
        for item in items:
            value = saved.get(self.key(item["thread_id"]))
            item["title_revision"] = value["revision"] if value else 0
            if value:
                item["title"] = value["title"]
                item["title_source"] = "leam"

    def rename(self, thread_id, body):
        key = self.key(thread_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            current = json.loads(row["value"]) if row else {"revision": 0}
            if current["revision"] != body.revision:
                raise HTTPException(
                    409, "Title changed on another device. Refresh before renaming."
                )
            value = {"title": body.title, "revision": body.revision + 1}
            db.execute(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        return {
            "thread_id": thread_id,
            "title": body.title,
            "title_revision": value["revision"],
        }

    def forget(self, thread_id):
        with self.store.connect() as db:
            db.execute("DELETE FROM settings WHERE key=?", (self.key(thread_id),))
