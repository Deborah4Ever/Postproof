"""
Free-trial usage tracking, keyed by a client-generated trial_id (cookie),
not IP - Railway sits behind a proxy so every visitor would otherwise
share one IP and one trial. Same append-anywhere-safe spirit as the
ledger file, but this needs a keyed counter, not a flat log, so it's
stored as a single JSON object of {trial_id: count} instead of jsonl.
"""

import json
import os

TRIALS_PATH = os.environ.get("TRIALS_PATH", "receipts/trials.json")
TRIAL_CAP = 2


def _load() -> dict:
    if not os.path.exists(TRIALS_PATH):
        return {}
    with open(TRIALS_PATH) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(TRIALS_PATH) or ".", exist_ok=True)
    with open(TRIALS_PATH, "w") as f:
        json.dump(data, f)


def get_trial_count(trial_id: str) -> int:
    return _load().get(trial_id, 0)


def increment_trial(trial_id: str) -> int:
    data = _load()
    data[trial_id] = data.get(trial_id, 0) + 1
    _save(data)
    return data[trial_id]
