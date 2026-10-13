#!/usr/bin/env python3
"""Ask Jev the same states in both question modes and score the answers with one judge.

Why this exists
---------------
``noul`` asks N *independent* questions ("would r0c3 be the best?") and the policy takes the
argmax; ``choice`` asks one question that picks among all candidates and returns a normalised
distribution over them. Those are different problems — independent scoring with an argmax is
not a ranking — and the observed symptoms (answers spread 0.18-0.54, the winner landing at
position 10 of 18 by a heuristic's own ordering) are consistent with the first, without
proving it. This script is the experiment that decides: identical state, identical candidate
list, identical message, only the question type changes.

The judge
---------
Deliberately *not* Jev, and deliberately not a claim about ground truth: every candidate is
scored by replaying it through the simulator (``drop`` on the shape's own cells — the same
call ``legal_placements`` makes) and applying the search policy's weights to the resulting
board. It answers "which move does a strong heuristic prefer", so the numbers here are
*regret against that heuristic* — a move the heuristic dislikes may still be good. What it
does measure cleanly is whether one question formulation produces choices closer to the
heuristic's own ordering than the other.

Usage:
    .venv/bin/python scripts/tetris_jev_modes.py --states 6          # real calls (paid)
    .venv/bin/python scripts/tetris_jev_modes.py --self-check        # fake answers, no cost
    .venv/bin/python scripts/tetris_jev_modes.py --states 6 --out captures/jev-modes/run.json
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"
PLAY = SKILLS / SKILL / "samples" / "session-play"
#: The committed playfield samples are the playfield itself, so the ROI is the whole image.
WHOLE = {"board_roi_ratios": [0.0, 0.0, 1.0, 1.0]}
MODES = ("noul", "choice")


def wrong_interpreter() -> str | None:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


def modules():
    from artemis.reflex.registry import load_skill

    load_skill(SKILL, directory=SKILLS)
    return {
        "perception": importlib.import_module(f"artemis_skills_{SKILL}.perception"),
        "policy": importlib.import_module(f"artemis_skills_{SKILL}.policy"),
        "policy_jev": importlib.import_module(f"artemis_skills_{SKILL}.policy_jev"),
        "jev_state": importlib.import_module(f"artemis_skills_{SKILL}.jev_state"),
        "sim": importlib.import_module(f"artemis_skills_{SKILL}.simulator"),
    }


# --------------------------------------------------------------------------- #
# States, from the committed corpus (no device)
# --------------------------------------------------------------------------- #


def states_from_corpus(mods, calibration: dict, limit: int) -> list[dict]:
    """Replay the committed playfield samples and keep the frames that show a piece."""
    manifest = json.loads((PLAY / "manifest.json").read_text(encoding="utf-8"))
    perception = mods["perception"]
    settings = {**calibration, **WHOLE, "stall": {**calibration.get("stall", {}), "window_s": 5.0}}
    perception.reset_history()
    found: list[dict] = []
    seen: set[tuple] = set()
    for entry in manifest["frames"]:
        state = perception.parse((PLAY / entry["file"]).read_bytes(), settings)
        if not state or not state.get("current_piece_mask"):
            continue
        # Consecutive frames of one position are the *same* state, not six samples: the first
        # run asked one board six times and reported its statistics as if they were six. The
        # fingerprint is the board plus the piece's cells, ignoring where the piece sits.
        matrix = state.get("board_matrix") or []
        shape = sim_fingerprint(state.get("current_piece_mask") or [])
        key = (tuple(matrix), shape)
        if key in seen:
            continue
        seen.add(key)
        found.append({"file": entry["file"], "t_mono": entry["t_mono"], "state": state})
        if len(found) >= limit:
            break
    return found


def sim_fingerprint(mask) -> tuple:
    """The piece's shape, moved to the origin, so two frames of one piece compare equal."""
    cells = [(r, c) for r, row in enumerate(mask) for c, cell in enumerate(row) if cell]
    if not cells:
        return ()
    top = min(r for r, _ in cells)
    left = min(c for _, c in cells)
    return tuple(sorted((r - top, c - left) for r, c in cells))


# --------------------------------------------------------------------------- #
# The judge: simulator + the search policy's weights
# --------------------------------------------------------------------------- #


def judge(mods, calibration: dict, board: list[str], candidates):
    """Rank **the candidates Jev was offered** with the simulator and the search weights.

    Two things this must get right, both of which the first version got wrong: the board is
    already in the simulator's own representation (``policy_jev._board`` converts "0"/"1" —
    converting again filled every empty cell and no placement fit), and the ranking has to
    happen *inside the offered candidate list* — otherwise "the best move" could be one Jev
    was never asked about, and the regret would measure the cap rather than the answer.
    """
    sim, policy = mods["sim"], mods["policy"]
    weights = calibration.get("weights") or {}

    scores: dict[tuple[int, int], float] = {}
    for placement, cells in candidates:
        dropped = sim.drop(board, cells, placement.column, gravity="down")
        if dropped is None:
            continue
        settled, cleared = sim.clear_lines(dropped)
        scores[(placement.rotation, placement.column)] = policy.score_board(
            settled, cleared, weights
        )

    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    ranks = {key: index + 1 for index, (key, _) in enumerate(ordered)}

    def score_of(placement) -> float | None:
        return scores.get((int(placement.rotation), int(placement.column)))

    return ordered, ranks, score_of


# --------------------------------------------------------------------------- #
# One state, both modes
# --------------------------------------------------------------------------- #


def compare_state(mods, calibration: dict, sample: dict) -> dict:
    """Same state, same candidates, same message — only the question type differs."""
    state = sample["state"]
    policy_jev, jev_state = mods["policy_jev"], mods["jev_state"]
    board = policy_jev._board(state)
    mask = state.get("current_piece_mask") or []
    gravity = jev_state.gravity_direction(state.get("motion_vector"), "down")

    candidates = policy_jev._candidates(board, mask, gravity, calibration)
    ordered, ranks, score_of = judge(mods, calibration, board, candidates)
    best_score = ordered[0][1] if ordered else None

    message = jev_state.build_state(
        board_matrix=board,
        pieces=state,
        mechanics=policy_jev._mechanics(calibration),
        next_preview=state.get("next_preview"),
        candidates=candidates,
    )

    arms = {}
    for mode in MODES:
        arm_cal = {**calibration, "jev": {**calibration["jev"], "mode": mode}}
        started = time.monotonic()
        placement, confidence = policy_jev._ask(mode, message, candidates, arm_cal, {})
        seconds = time.monotonic() - started
        if placement is None:
            arms[mode] = {"chosen": None, "seconds": round(seconds, 2), "reason": "no answer"}
            continue
        key = (int(placement.rotation), int(placement.column))
        chosen_score = score_of(placement)
        arms[mode] = {
            "chosen": {"rotation": key[0], "column": key[1]},
            "confidence": None if confidence is None else round(float(confidence), 4),
            "seconds": round(seconds, 2),
            "judge_score": None if chosen_score is None else round(chosen_score, 3),
            "judge_rank": ranks.get(key),
            "regret": None
            if chosen_score is None or best_score is None
            else round(best_score - chosen_score, 3),
        }

    return {
        "file": sample["file"],
        "t_mono": sample["t_mono"],
        "candidates": len(candidates),
        "judged": len(ordered),
        "judge_best": {"rotation": ordered[0][0][0], "column": ordered[0][0][1]}
        if ordered
        else None,
        "judge_best_score": None if best_score is None else round(best_score, 3),
        "arms": arms,
    }


def summarise(rows: list[dict]) -> dict:
    out: dict = {"states": len(rows), "modes": {}}
    for mode in MODES:
        arms = [row["arms"][mode] for row in rows if row["arms"].get(mode, {}).get("chosen")]
        regrets = [arm["regret"] for arm in arms if arm.get("regret") is not None]
        ranks = [arm["judge_rank"] for arm in arms if arm.get("judge_rank")]
        out["modes"][mode] = {
            "answered": len(arms),
            "answered_rate": round(len(arms) / max(1, len(rows)), 3),
            "mean_regret": round(statistics.mean(regrets), 3) if regrets else None,
            "median_regret": round(statistics.median(regrets), 3) if regrets else None,
            "judge_top1_rate": round(sum(1 for r in ranks if r == 1) / len(ranks), 3)
            if ranks
            else None,
            "judge_top3_rate": round(sum(1 for r in ranks if r <= 3) / len(ranks), 3)
            if ranks
            else None,
            "mean_seconds": round(statistics.mean([a["seconds"] for a in arms]), 2)
            if arms
            else None,
            "mean_confidence": round(
                statistics.mean([a["confidence"] for a in arms if a["confidence"] is not None]), 3
            )
            if any(a["confidence"] is not None for a in arms)
            else None,
        }
    agreed = [
        row
        for row in rows
        if row["arms"].get("noul", {}).get("chosen")
        and row["arms"]["noul"]["chosen"] == row["arms"].get("choice", {}).get("chosen")
    ]
    out["agreement"] = {
        "same_choice": len(agreed),
        "rate": round(len(agreed) / max(1, len(rows)), 3),
    }
    return out


def self_check(mods, calibration: dict) -> int:
    """Every path except the network: fake both question types, run one state, print it."""
    from artemis.services import systemone

    real_ask = systemone.ask
    real_choice = systemone.choice

    def fake_ask(state, questions, **kwargs):
        return {name: {"type": "noul", "noul": 0.5} for name in questions}

    def fake_choice(state, instructions, criteria, **kwargs):
        names = list(criteria)
        return names[0], {name: 1.0 / len(names) for name in names}

    failures: list[str] = []
    try:
        systemone.ask = fake_ask
        systemone.choice = fake_choice
        samples = states_from_corpus(mods, calibration, 1)
        if not samples:
            failures.append("the committed corpus produced no state with a piece")
        else:
            row = compare_state(mods, calibration, samples[0])
            for mode in MODES:
                arm = row["arms"].get(mode) or {}
                if "chosen" not in arm:
                    failures.append(f"{mode}: no arm produced a choice")
                elif arm.get("judge_rank") is None:
                    failures.append(f"{mode}: the judge could not rank the choice")
            summary = summarise([row])
            if not summary["modes"]["noul"]["answered"]:
                failures.append("the summary lost the noul arm")
    finally:
        systemone.ask = real_ask
        systemone.choice = real_choice

    print(json.dumps({"self_check_failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", type=int, default=6)
    parser.add_argument("--out", help="write the full report here")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    interpreter = wrong_interpreter()
    if interpreter:
        print(f"wrong interpreter: run me with {interpreter}", file=sys.stderr)
        return 2

    mods = modules()
    calibration = json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))
    if args.self_check:
        return self_check(mods, calibration)

    samples = states_from_corpus(mods, calibration, args.states)
    print(f"从已提交语料取到 {len(samples)} 个含下落块的状态（离线，无设备）", flush=True)
    rows = []
    for index, sample in enumerate(samples, 1):
        row = compare_state(mods, calibration, sample)
        rows.append(row)
        arms = row["arms"]
        print(
            f"  {index}/{len(samples)} {sample['file']} 候选 {row['candidates']} | "
            f"启发式最优 r{row['judge_best']['rotation']}c{row['judge_best']['column']} | "
            + " | ".join(
                f"{mode}: "
                + (
                    "无答案"
                    if not arms[mode].get("chosen")
                    else f"r{arms[mode]['chosen']['rotation']}c{arms[mode]['chosen']['column']} "
                    f"rank{arms[mode]['judge_rank']} regret{arms[mode]['regret']} "
                    f"{arms[mode]['seconds']}s"
                )
                for mode in MODES
            ),
            flush=True,
        )

    report = {"summary": summarise(rows), "rows": rows}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {out}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n== 汇总（判据 = search 策略的评分，不是 ground truth）==")
        for mode, stats in report["summary"]["modes"].items():
            print(
                f"  {mode:7s} 答案率 {stats['answered_rate']} 平均 regret {stats['mean_regret']} "
                f"中位 {stats['median_regret']} 命中启发式最优 {stats['judge_top1_rate']} "
                f"进前三 {stats['judge_top3_rate']} 平均耗时 {stats['mean_seconds']}s"
            )
        print(f"  两模式选择相同的比例: {report['summary']['agreement']['rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
