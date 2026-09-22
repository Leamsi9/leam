"""Owned streaming resources close even if ASGI never starts iterating them."""

import anyio
from fastapi.responses import StreamingResponse


class OwnedStream:
    def __init__(self, iterator, close):
        self.iterator = iterator
        self.close = close
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        try:
            return await anext(self.iterator)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self):
        if not self.closed:
            self.closed = True
            # Shield cleanup from an enclosing ASGI disconnect cancellation scope.
            with anyio.CancelScope(shield=True):
                try:
                    await self.close()
                finally:
                    await self.iterator.aclose()


class VoiceStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()
