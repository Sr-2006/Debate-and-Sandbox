import sys
import os
import argparse
import json
from rl_engine.config import RL_OPERATING_MODE, RL_LAPTOP1_TRANSPORT
from rl_engine.trainer import train_linucb_policy
from rl_engine.evaluator import evaluate_policy
from rl_engine.episode_store import EpisodeStore
from rl_engine.model_store import ModelStore


def validate_ci_safety() -> bool:
    """
    Validates CI safety constraints:
    1. Operating mode must be SHADOW.
    2. Laptop 1 transport must be disabled.
    3. No episode stored in CI database may be eligible for training.
    """
    errors = []
    if RL_OPERATING_MODE != "SHADOW":
        errors.append(f"RL_OPERATING_MODE is '{RL_OPERATING_MODE}', expected 'SHADOW' in CI")

    if RL_LAPTOP1_TRANSPORT != "disabled":
        errors.append(f"RL_LAPTOP1_TRANSPORT is '{RL_LAPTOP1_TRANSPORT}', expected 'disabled' in CI")

    epstore = EpisodeStore()
    episodes = epstore.get_eligible_episodes()
    if len(episodes) > 0:
        errors.append(f"Found {len(episodes)} eligible real episodes in CI run, expected 0 (all CI runs must be simulated/ineligible)")

    if errors:
        print("=== CI SAFETY VALIDATION FAILED ===")
        for err in errors:
            print(f"  - {err}")
        return False

    print("=== CI SAFETY VALIDATION PASSED ===")
    print("  - Mode: SHADOW")
    print("  - Transport: disabled")
    print("  - Eligible real training episodes: 0")
    return True


def main():
    parser = argparse.ArgumentParser(description="RL Advisory Engine CLI")
    subparsers = parser.add_subparsers(dest="command")

    # validate-ci-safety
    subparsers.add_parser("validate-ci-safety", help="Validates CI safety constraints")

    # train
    train_parser = subparsers.add_parser("train", help="Train LinUCB policy on eligible episodes")
    train_parser.add_argument("--db", default=None, help="SQLite database path")

    # evaluate
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate policy")
    eval_parser.add_argument("--candidate-model", default="promoted", help="Model version or path")

    # status
    subparsers.add_parser("status", help="Show RL engine status")

    # build-episodes
    subparsers.add_parser("build-episodes", help="Build episodes from reports")

    args = parser.parse_args()

    if args.command == "validate-ci-safety":
        ok = validate_ci_safety()
        sys.exit(0 if ok else 1)
    elif args.command == "train":
        mver, summary = train_linucb_policy(db_path=args.db)
        print(f"Training complete. Model Version: {mver}")
        print(json.dumps(summary, indent=2))
    elif args.command == "evaluate":
        res = evaluate_policy(args.candidate_model)
        print(json.dumps(res, indent=2))
    elif args.command == "status":
        print(f"RL_OPERATING_MODE: {RL_OPERATING_MODE}")
        print(f"RL_LAPTOP1_TRANSPORT: {RL_LAPTOP1_TRANSPORT}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
