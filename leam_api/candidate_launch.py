"""Fixed app/MCP launcher; adoption of its service commands is operator-owned."""

import argparse
import os

from .candidate_deployment import CandidateDeployment


def main():
    parser = argparse.ArgumentParser(
        description="Launch a fixed Leam candidate service from the private operator descriptor"
    )
    parser.add_argument("role", choices=("app", "mcp"))
    args = parser.parse_args()
    plan = CandidateDeployment().launch_plan(args.role)
    # Drop stale Leam overrides from the unit. Only the validated descriptor owns
    # launch settings; unrelated OS variables retain existing normal behavior.
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("LEAM_") and k != "PYTHONPATH"
    }
    environment.update(plan["environment"])
    os.chdir(plan["cwd"])
    os.execve(plan["argv"][0], plan["argv"], environment)


if __name__ == "__main__":
    main()
