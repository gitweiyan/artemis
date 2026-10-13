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
#: The six arms of the redesign plan (§4), each one variable away from a neighbour:
#:   A/B  placements, bare ids        — the formulation pair (noul vs choice)
#:   C'/D' placements + consequences  — observability, without the 14 KB board of the first C/D
#:   E/F  actions + / without consequences — option granularity (one button vs one landing)
ARMS = (
    ("A", "placement", "noul", False),
    ("B", "placement", "choice", False),
    ("C'", "placement", "choice", True),
    ("D'", "placement", "noul", True),
    ("E", "action", "choice", True),
    ("F", "action", "choice", False),
)


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
        "policy_actions": importlib.import_module(f"artemis_skills_{SKILL}.policy_actions"),
    }


# --------------------------------------------------------------------------- #
# States, from the committed corpus (no device)
# --------------------------------------------------------------------------- #


def synthetic_states(calibration: dict, count: int, *, seed: int = 7) -> list[dict]:
    """Boards built here, so the sample is not one 30-second recording.

    The corpus is a single session; the first four-arm run reported statistics over four
    states that were nearly the same position. These are deterministic (seeded), varied in
    stack shape, and use the game's own shapes from ``mechanics.shapes`` — including the 1, 2
    and 3 cell pieces, which are half of what this game spawns.
    """
    import random

    rng = random.Random(seed)
    shapes = list((calibration["mechanics"].get("shapes") or {}).values()) or [[[1, 1], [1, 1]]]
    states: list[dict] = []
    for index in range(count):
        heights = [rng.randint(0, 7) for _ in range(10)]
        rows = [["0"] * 10 for _ in range(20)]
        for column, height in enumerate(heights):
            for row in range(20 - height, 20):
                rows[row][column] = "1"
        if index % 4 == 1:  # a well
            for row in range(20 - max(heights) - 1, 20):
                rows[row][3] = "0"
        if index % 4 == 2:  # a hole under an overhang
            rows[20 - max(heights)][5] = "0"
        board = ["".join(row) for row in rows]
        shape = shapes[index % len(shapes)]
        top = rng.randint(0, 2)
        column = rng.randint(0, 10 - len(shape[0]))
        states.append(
            {
                "board_matrix": board,
                "current_piece_mask": shape,
                "current_piece_positions": [
                    [top + r, column + c]
                    for r, line in enumerate(shape)
                    for c, cell in enumerate(line)
                    if cell
                ],
                "motion_vector": [1, 0],
                "_source": "synthetic",
            }
        )
    return states


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
    """Same state, and per arm only one thing changes: space, question type, or consequences."""
    state = sample["state"]
    policy_jev, jev_state = mods["policy_jev"], mods["jev_state"]
    board = policy_jev._board(state)
    mask = state.get("current_piece_mask") or []
    gravity = jev_state.gravity_direction(state.get("motion_vector"), "down")

    candidates = policy_jev._candidates(board, mask, gravity, calibration)
    ordered, ranks, score_of = judge(mods, calibration, board, candidates)
    best_score = ordered[0][1] if ordered else None
    action_message = jev_state.build_action_state(
        board_matrix=board,
        pieces=state,
        mechanics=policy_jev._mechanics(calibration),
        next_preview=state.get("next_preview"),
        rotation=0,
        column=min((int(c) for _, c in state.get("current_piece_positions") or []), default=0),
        consequences=True,
        plan=False,
    )
    action_ids = [entry["id"] for entry in action_message["candidate_actions"]]
    start_column = min((int(c) for _, c in state.get("current_piece_positions") or []), default=0)

    arms = {}
    for name, space, mode, consequences in ARMS:
        if space == "placement":
            message = jev_state.build_state(
                board_matrix=board,
                pieces=state,
                mechanics=policy_jev._mechanics(calibration),
                next_preview=state.get("next_preview"),
                candidates=candidates,
                consequences=consequences,
            )
            started = time.monotonic()
            placement, confidence = policy_jev._ask(
                mode,
                message,
                candidates,
                {**calibration, "jev": {**calibration["jev"], "mode": mode}},
                {},
            )
            seconds = time.monotonic() - started
            key = None if placement is None else (int(placement.rotation), int(placement.column))
            score = (
                None
                if key is None
                else score_of(type("P", (), {"rotation": key[0], "column": key[1]})())
            )
            arms[name] = {
                "space": space,
                "mode": mode,
                "consequences": consequences,
                "bytes": len(json.dumps(message, ensure_ascii=False)),
                "seconds": round(seconds, 2),
                "chosen": None if key is None else {"rotation": key[0], "column": key[1]},
                "confidence": confidence,
                "judge_score": None if score is None else round(score, 3),
                "judge_rank": None if key is None else ranks.get(key),
                "regret": None
                if score is None or best_score is None
                else round(best_score - score, 3),
            }
            continue
        bare = dict(action_message)
        if not consequences:
            bare = {
                **action_message,
                "candidate_actions": [
                    {k: v for k, v in entry.items() if k != "then_lands"}
                    for entry in action_message["candidate_actions"]
                ],
            }
        started = time.monotonic()
        chosen, _note, _source = mods["policy_actions"]._ask(
            mode,
            bare,
            action_ids,
            {**calibration, "jev": {**calibration["jev"], "mode": mode}},
            {},
        )
        seconds = time.monotonic() - started
        # The action leads to one specific landing; score it with the same judge, so regret is
        # in the same units as the placement arms.
        key = None
        if chosen:
            shapes = list(mods["sim"].orientations_from_mask(mask))
            rotation = 1 % max(1, len(shapes)) if chosen == "rotate" else 0
            reach = jev_state._reach(
                board,
                shapes,
                0,
                start_column,
                chosen,
                mask,
                mods["sim"].drop,
                mods["sim"].mask_cells,
                "down",
            )
            if reach is not None:
                key = (rotation, reach[1])
        score = (
            None
            if key is None
            else score_of(type("P", (), {"rotation": key[0], "column": key[1]})())
        )
        arms[name] = {
            "space": space,
            "mode": mode,
            "consequences": consequences,
            "bytes": len(json.dumps(bare, ensure_ascii=False)),
            "seconds": round(seconds, 2),
            "chosen": {"action": chosen} if chosen else None,
            "confidence": None,
            "judge_score": None if score is None else round(score, 3),
            "judge_rank": None if key is None else ranks.get(key),
            "regret": None if score is None or best_score is None else round(best_score - score, 3),
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
    for name, _space, _mode, _results in ARMS:
        arms = [row["arms"][name] for row in rows if row["arms"].get(name, {}).get("chosen")]
        regrets = [arm["regret"] for arm in arms if arm.get("regret") is not None]
        ranks = [arm["judge_rank"] for arm in arms if arm.get("judge_rank")]
        out["modes"][name] = {
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
    pairs = {
        "A_vs_B": ("A", "B"),
        "B_vs_C'": ("B", "C'"),
        "C'_vs_E": ("C'", "E"),
        "E_vs_F": ("E", "F"),
    }
    out["agreement"] = {}
    for label, (left, right) in pairs.items():
        same = sum(
            1
            for row in rows
            if row["arms"].get(left, {}).get("chosen")
            and row["arms"][left]["chosen"] == row["arms"].get(right, {}).get("chosen")
        )
        out["agreement"][label] = {"same_choice": same, "rate": round(same / max(1, len(rows)), 3)}
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
            for name, _space, _mode, _results in ARMS:
                arm = row["arms"].get(name) or {}
                if "chosen" not in arm:
                    failures.append(f"{name}: no arm produced a choice")
                elif arm.get("judge_rank") is None:
                    failures.append(f"{name}: the judge could not rank the choice")
            summary = summarise([row])
            if not summary["modes"]["A"]["answered"]:
                failures.append("the summary lost the noul arm")
    finally:
        systemone.ask = real_ask
        systemone.choice = real_choice

    print(json.dumps({"self_check_failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", type=int, default=6)
    parser.add_argument(
        "--source",
        choices=("corpus", "synthetic", "both"),
        default="synthetic",
        help="where the states come from: the committed recording, generated boards, or both",
    )
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

    samples = []
    if args.source in ("corpus", "both"):
        found = states_from_corpus(mods, calibration, args.states)
        samples += [{"file": f["file"], "t_mono": f["t_mono"], "state": f["state"]} for f in found]
    if args.source in ("synthetic", "both"):
        wanted = args.states if args.source == "synthetic" else max(1, args.states - len(samples))
        samples += [
            {"file": f"synthetic-{index + 1:02d}", "t_mono": None, "state": state}
            for index, state in enumerate(synthetic_states(calibration, wanted))
        ]
    print(
        f"状态 {len(samples)} 个（来源 {args.source}，离线、无设备）"
        f"；六臂 = {', '.join(name for name, *_ in ARMS)}",
        flush=True,
    )
    rows = []
    for index, sample in enumerate(samples, 1):
        row = compare_state(mods, calibration, sample)
        rows.append(row)
        arms = row["arms"]
        print(
            f"  {index}/{len(samples)} {sample['file']} 候选 {row['candidates']} | "
            f"启发式最优 r{row['judge_best']['rotation']}c{row['judge_best']['column']} | "
            + " | ".join(
                f"{name}: "
                + (
                    "无答案"
                    if not arms[name].get("chosen")
                    else (
                        f"r{arms[name]['chosen']['rotation']}c{arms[name]['chosen']['column']}"
                        if "rotation" in arms[name]["chosen"]
                        else str(arms[name]["chosen"]["action"])
                    )
                    + f" regret{arms[name]['regret']} {arms[name]['seconds']}s "
                    f"{arms[name]['bytes']}B"
                )
                for name, _space, _mode, _results in ARMS
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
