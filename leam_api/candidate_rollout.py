"""Operator-only release switching through the shared candidate restore lock."""

import argparse
import asyncio
import json
from pathlib import Path

from .candidate_deployment import CandidateDeployment, digest
from .recovery import SERVICES
from .restore_services import FixedRestoreServices


class ReleaseServices(FixedRestoreServices):
    """Ordinary app releases preserve the current runtime and recovery processes."""

    async def stop(self):
        await self.control._systemctl(
            "stop", *(SERVICES[name][1] for name in ("app", "mcp"))
        )
        for name in ("app", "mcp"):
            raw = await self.control._systemctl(
                "show", SERVICES[name][1], "--property=MainPID,ActiveState"
            )
            values = dict(
                line.split("=", 1) for line in raw.splitlines() if "=" in line
            )
            if values.get("MainPID") != "0" or values.get("ActiveState") not in {
                "inactive",
                "failed",
            }:
                raise ValueError("Candidate writer has not stopped")
        if any(
            row.get("reachable")
            for row in await self.control.status()
            if row["id"] in {"app", "mcp"}
        ):
            raise ValueError("A candidate listener remains active")

    async def start(self):
        await self.control._systemctl(
            "start", *(SERVICES[name][1] for name in ("app", "mcp"))
        )


async def _switch(deployment, services, release, expected_digest):
    with deployment.lock():
        before = deployment.read()
        if digest(before) != expected_digest:
            raise ValueError("Candidate changed; inspect a fresh descriptor")
        if await services.identity() != before["runtime"]:
            raise ValueError("Running runtime identity changed")
        after = deployment.prepare_release_locked(release, before["runtime"], before)
        # Validation precedes every side effect. A failed stop leaves the original
        # pointer intact; restoration must first prove writers are stopped.
        changed = False
        publication_attempted = False
        intended = {**after, "revision": before["revision"] + 1}
        try:
            await services.stop()
            publication_attempted = True
            after = deployment.replace_locked(after, before)
            changed = True
            await services.start()
            if not await services.healthy():
                raise ValueError("New release did not pass identity/health checks")
            return {"state": "deployed", "descriptor": after, "previous": before}
        except Exception as error:
            if publication_attempted and not changed:
                # os.replace can succeed before directory fsync raises. Determine
                # the actual pointer under the retained lock, never infer it from
                # a function failing to return. Unknown state needs explicit review.
                try:
                    observed = deployment.read()
                except Exception as read_error:
                    raise ValueError(
                        "Deployment pointer outcome is unknown; operator review required"
                    ) from read_error
                if digest(observed) == digest(intended):
                    after, changed = observed, True
                elif digest(observed) != digest(before):
                    raise ValueError(
                        "Deployment pointer changed unexpectedly; operator review required"
                    ) from error
            if not changed:
                raise ValueError(
                    "Deployment stopped before pointer change; inspect candidate services"
                ) from error
            try:
                await services.stop()
                restored = deployment.replace_locked(before, after)
                await services.start()
                if not await services.healthy():
                    raise ValueError("Previous release failed verification")
            except Exception as rollback_error:
                raise ValueError(
                    "Deployment and rollback require operator review; no automatic retry"
                ) from rollback_error
            return {
                "state": "rolled_back",
                "descriptor": restored,
                "previous": before,
                "message": "New release failed; previous release restored and verified",
            }


async def rollout(deployment, services, release, expected_digest):
    # Repeated cancellation must not release the operation lock while systemctl
    # or health verification is still running. Hard process death requires review.
    task = asyncio.create_task(_switch(deployment, services, release, expected_digest))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Switch a staged Leam release under the restore operation lock"
    )
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--expected-digest", required=True)
    args = parser.parse_args()
    deployment = CandidateDeployment()
    result = asyncio.run(
        rollout(
            deployment, ReleaseServices(deployment), args.release, args.expected_digest
        )
    )
    print(json.dumps(result))
    if result["state"] != "deployed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
