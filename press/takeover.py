import json
import sqlite3

from press.duplex import DuplexStage, RunConflict
from press.ejection import read_actual_ejections
from press.queue import read_queued_run, reconcile_sheet_eligibility
from press.simulator import SimulatedPress


def prepare_run_takeover(db: sqlite3.Connection, device: SimulatedPress,
                         run_id: str, request_id: str) -> dict:
    run = read_queued_run(db, device.press_id, run_id)
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO run_handoffs(run_id, request_id) VALUES (?, ?)',
                   (run_id, request_id))
        row = db.execute('SELECT request_id FROM run_handoffs WHERE run_id = ?', (run_id,)).fetchone()
        if row[0] != request_id:
            raise RunConflict('takeover_request_conflict')

    numbers = tuple(sheet.number for sheet in run.plan.sheets)
    eligible = device.fence_numbers_for_takeover(run_id, numbers)
    actual = [
        {'ejection_id': record.ejection_id, 'action_id': record.action_id,
         'batch_id': record.batch_id, 'run_id': record.run_id,
         'press_id': record.press_id, 'number': record.number}
        for record in read_actual_ejections(device, run_id)
    ]
    actual_numbers = {event['number'] for event in actual}
    pending = tuple(number for number in numbers
                    if number not in eligible and number not in actual_numbers)
    return {**run.reference(), 'eligible': list(eligible),
            'pending': list(pending), 'actual': actual}


def sync_run_eligibility(db: sqlite3.Connection, device: SimulatedPress,
                         run_id: str, numbers) -> dict:
    run = read_queued_run(db, device.press_id, run_id)
    if not isinstance(numbers, list) or any(type(number) is not int for number in numbers):
        raise ValueError('invalid_numbers')
    enabled = reconcile_sheet_eligibility(db, run_id, numbers)
    return {**run.reference(), 'enabled': list(enabled)}


def discard_ejection(db: sqlite3.Connection, device: SimulatedPress,
                     run_id: str, request_id: str) -> dict:
    run = read_queued_run(db, device.press_id, run_id)
    row = db.execute('''SELECT run_id, number, stage, action_id, result
                        FROM advance_requests WHERE request_id = ?''',
                     (request_id,)).fetchone()
    if row is None:
        raise KeyError('unknown_advance')
    original_run, number, target_stage, action_id, saved_result = row
    if original_run != run_id:
        raise RunConflict('advance_request_conflict')

    saved_discard = db.execute('SELECT result FROM discarded_actions WHERE request_id = ?',
                               (request_id,)).fetchone()
    if saved_discard is not None:
        return json.loads(saved_discard[0])

    handoff = db.execute('SELECT 1 FROM run_handoffs WHERE run_id = ?', (run_id,)).fetchone()
    if handoff is None:
        raise RunConflict('discard_not_available')
    if target_stage != DuplexStage.EJECTED.value:
        raise RunConflict('discard_requires_ejection_action')
    if saved_result is not None:
        raise RunConflict('ejection_already_occurred')

    receipt = device.discard_ejection(action_id=action_id, request_id=request_id,
                                     run_id=run_id, number=number)
    body = json.dumps(receipt, sort_keys=True, separators=(',', ':'))
    with db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT result FROM discarded_actions WHERE request_id = ?',
                           (request_id,)).fetchone()
        if prior is not None:
            return json.loads(prior[0])
        db.execute('UPDATE sheet_work SET enabled = 0 WHERE run_id = ? AND number = ?',
                   (run_id, number))
        db.execute('''INSERT INTO discarded_actions(request_id, run_id, number, action_id, result)
                      VALUES (?, ?, ?, ?, ?)''',
                   (request_id, run_id, number, action_id, body))
    return receipt
