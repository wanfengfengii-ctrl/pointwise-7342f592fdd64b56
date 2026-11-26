from dataclasses import dataclass
import sqlite3

from desk.batches import DeskBatch, DeskConflict, get_batch
from desk.press_client import PressClient
from desk.takeover_store import get_takeover_for_batch


@dataclass(frozen=True)
class BatchProgress:
    batch_id: str
    press_id: str | None
    run_id: str | None
    actual: tuple[dict, ...]
    missing: tuple[int, ...]


def partition_actual_sheets(batch: DeskBatch, events: list[dict],
                            allowed_refs: set[tuple[str, str]] | None = None) -> BatchProgress:
    planned = {sheet.number for sheet in batch.plan.sheets}
    if allowed_refs is None:
        allowed_refs = {(batch.press_id, batch.run_id)}
    actual = {}
    ejection_ids = set()
    if not isinstance(events, list):
        raise DeskConflict('invalid_run_evidence')
    for event in events:
        if not isinstance(event, dict):
            raise DeskConflict('invalid_run_evidence')
        number = event.get('number')
        ejection_id = event.get('ejection_id')
        event_ref = (event.get('press_id'), event.get('run_id'))
        if (event.get('batch_id') != batch.batch_id or event_ref not in allowed_refs
                or type(number) is not int
                or number not in planned or number in actual
                or not isinstance(ejection_id, str) or not ejection_id or ejection_id in ejection_ids):
            raise DeskConflict('invalid_run_evidence')
        actual[number] = event
        ejection_ids.add(ejection_id)
    return BatchProgress(batch.batch_id, batch.press_id, batch.run_id,
                         tuple(actual[number] for number in sorted(actual)),
                         tuple(sorted(planned.difference(actual))))


def read_progress(db: sqlite3.Connection, client: PressClient, batch_id: str) -> BatchProgress:
    batch = get_batch(db, batch_id)
    refs = []
    if batch.run_id is not None:
        refs.append((batch.press_id, batch.run_id))
    takeover = get_takeover_for_batch(db, batch_id)
    if takeover is not None and takeover.to_run_id is not None:
        refs.append((takeover.to_press, takeover.to_run_id))

    events = []
    for press_id, run_id in refs:
        remote = client.read_run(press_id, run_id)
        if (remote.get('batch_id'), remote.get('press_id'), remote.get('run_id')) != (
                batch.batch_id, press_id, run_id):
            raise DeskConflict('invalid_run_evidence')
        remote_events = remote.get('actual')
        if not isinstance(remote_events, list):
            raise DeskConflict('invalid_run_evidence')
        events.extend(remote_events)
    return partition_actual_sheets(batch, events, set(refs))
