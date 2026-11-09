"""Stratified train/val/test split of Reimu-master perspectives (deterministic).

Reads every ``.rep`` entry straight from the Dauphine zip (no extraction),
tiers replays by Reimu/Marisa presence, and splits the 128 Reimu-master
perspectives (124 replays, 4 of which are Reimu mirrors) into::

    train : 88 perspectives in 4 incremental batches of 22 (batch_1..batch_4)
    val   : 20 perspectives (early stopping / hparams)
    test  : 20 perspectives (strictly held out)

Mirror matches (both sides ``master == "reimu"``) are assigned atomically so
both perspectives land in the SAME split — zero file leakage across splits.
Within (date, matchup) strata the order is shuffled with ``seed=42`` and
dealt round-robin (14:3:3 per 20), so splits are stratified by matchup/date.

Also emits a ``marisa_master_perspectives`` index and tier2/tier3 file lists.

Usage: ``python scripts/build_replay_splits.py`` (repo root as cwd or not).
"""

from __future__ import annotations

import json
import random
import sys
import zipfile

from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from th155.replay_parser import parse_bytes  # noqa: E402

SEED = 42
ZIP_PATH = ROOT / "replay" / "1519replays - Dauphine 250101-251124 119.zip"
OUT_PATH = ROOT / "configs" / "replay_splits.json"

N_TRAIN, N_VAL, N_TEST = 88, 20, 20
BATCH_SIZE = 22
# Round-robin pattern over 20 consecutive stratified units: 14 train, 3 val, 3 test.
PATTERN = ["train"] * 14 + ["val"] * 3 + ["test"] * 3
MIRROR_PATTERN = ["train", "train", "val", "test"]


def _persp(info: dict, team: int) -> dict:
    masters, slaves = info["masters"], info["slaves"]
    opp = 1 - team
    return {
        "file": info["file"],
        "team": team,
        "master": masters[team],
        "slave": slaves[team],
        "opp_master": masters[opp],
        "opp_slave": slaves[opp],
    }


def main() -> dict:
    infos: list[dict] = []
    with zipfile.ZipFile(ZIP_PATH) as zf:
        names = sorted(n for n in zf.namelist() if n.endswith(".rep"))
        for name in names:
            with zf.open(name, "r") as f:
                rep = parse_bytes(f.read(), source=name)
            ud = rep.userdata
            infos.append({
                "file": name,
                "date": name.split("/")[0],
                "masters": list(ud["master_name"]),
                "slaves": list(ud["slave_name"]),
                "matchup": (ud["master_name"][0], ud["slave_name"][0],
                            ud["master_name"][1], ud["slave_name"][1]),
            })

    tier1 = [i for i in infos
             if i["masters"][0] == "reimu" or i["masters"][1] == "reimu"]
    tier1_files = {i["file"] for i in tier1}
    tier2 = [i for i in infos if i["file"] not in tier1_files and (
        i["masters"][0] == "marisa" or i["masters"][1] == "marisa"
        or i["slaves"][0] == "reimu" or i["slaves"][1] == "reimu")]
    tier2_files = {i["file"] for i in tier2}
    tier3 = [i for i in infos
             if i["file"] not in tier1_files and i["file"] not in tier2_files]

    # Tier-1 slave breakdown (from the Reimu-master side's perspective).
    slave_of_reimu: Counter = Counter()
    for info in tier1:
        for team in (0, 1):
            if info["masters"][team] == "reimu":
                slave_of_reimu[info["slaves"][team]] += 1
    n_persp = sum(slave_of_reimu.values())
    mirrors = sorted(i["file"] for i in tier1
                     if i["masters"][0] == "reimu" and i["masters"][1] == "reimu")

    # --- deterministic stratified deal ---
    rng = random.Random(SEED)
    mirror_infos = [i for i in tier1 if i["file"] in set(mirrors)]
    single_infos = [i for i in tier1 if i["file"] not in set(mirrors)]

    def stratify(items: list[dict]) -> list[dict]:
        groups: dict[tuple, list[dict]] = {}
        for info in items:
            groups.setdefault((info["date"], info["matchup"]), []).append(info)
        ordered: list[dict] = []
        for key in sorted(groups):
            group = sorted(groups[key], key=lambda i: i["file"])
            rng.shuffle(group)
            ordered.extend(group)
        return ordered

    ordered_singles = stratify(single_infos)
    ordered_mirrors = stratify(mirror_infos)

    buckets: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for idx, info in enumerate(ordered_singles):
        team = 0 if info["masters"][0] == "reimu" else 1
        buckets[PATTERN[idx % len(PATTERN)]].append(_persp(info, team))
    for idx, info in enumerate(ordered_mirrors):
        split = MIRROR_PATTERN[idx % len(MIRROR_PATTERN)]
        buckets[split].append(_persp(info, 0))
        buckets[split].append(_persp(info, 1))

    assert len(buckets["train"]) == N_TRAIN, len(buckets["train"])
    assert len(buckets["val"]) == N_VAL, len(buckets["val"])
    assert len(buckets["test"]) == N_TEST, len(buckets["test"])
    # Mirror atomicity + cross-split file disjointness.
    for m in mirrors:
        holders = [s for s, ps in buckets.items() if any(p["file"] == m for p in ps)]
        assert holders and len(holders) == 1, (m, holders)
    file_sets = [{p["file"] for p in buckets[s]} for s in ("train", "val", "test")]
    assert not (file_sets[0] & file_sets[1] or file_sets[0] & file_sets[2]
                or file_sets[1] & file_sets[2])

    key = lambda p: (p["file"], p["team"])  # noqa: E731
    train_sorted = sorted(buckets["train"], key=lambda p: (p["file"], p["team"]))
    batches = {
        f"batch_{n + 1}": train_sorted[n * BATCH_SIZE:(n + 1) * BATCH_SIZE]
        for n in range(N_TRAIN // BATCH_SIZE)
    }
    assert all(len(b) == BATCH_SIZE for b in batches.values())

    marisa_index = sorted(
        (_persp(info, team) for info in infos for team in (0, 1)
         if info["masters"][team] == "marisa"),
        key=lambda p: (p["file"], p["team"]),
    )
    for p in marisa_index:
        owner = next(i for i in infos if i["file"] == p["file"])
        p["tier"] = ("tier1_reimu_master" if owner in tier1
                     else "tier2_marisa_master_or_reimu_slave")

    manifest = {
        "seed": SEED,
        "zip": ZIP_PATH.name,
        "targets": {"train": N_TRAIN, "val": N_VAL, "test": N_TEST,
                    "train_batches": N_TRAIN // BATCH_SIZE,
                    "batch_size": BATCH_SIZE},
        "tier_counts": {
            "tier1_reimu_master_replays": len(tier1),
            "tier1_reimu_master_perspectives": n_persp,
            "tier1_mirror_replays": len(mirrors),
            "tier2_marisa_master_or_reimu_slave_replays": len(tier2),
            "tier3_other_replays": len(tier3),
            "total_replays": len(infos),
        },
        "tier1_slave_of_reimu": dict(slave_of_reimu.most_common()),
        "mirror_files": mirrors,
        "splits": {
            "train": {"batches": batches},
            "val": sorted(buckets["val"], key=key),
            "test": sorted(buckets["test"], key=key),
        },
        "marisa_master_perspectives": marisa_index,
        "tier2_files": sorted(i["file"] for i in tier2),
        "tier3_files": sorted(i["file"] for i in tier3),
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"replays: {len(infos)} | tier1 {len(tier1)} ({n_persp} persp, "
          f"{len(mirrors)} mirrors) | tier2 {len(tier2)} | tier3 {len(tier3)}")
    print(f"tier1 slave-of-reimu: {dict(slave_of_reimu.most_common())}")
    print(f"train {len(buckets['train'])} = 4x{BATCH_SIZE} batches | "
          f"val {len(buckets['val'])} | test {len(buckets['test'])}")
    print(f"marisa-master perspectives: {len(marisa_index)}")
    print(f"wrote {OUT_PATH}")
    return manifest


if __name__ == "__main__":
    main()
