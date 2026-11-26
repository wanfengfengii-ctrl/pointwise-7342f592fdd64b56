import sqlite3

from delivery.release import get_issued_batch
from desk.batches import DeskConflict, get_batch
from desk.press_client import PressClient
from desk.progress import read_progress
from desk.takeover_store import (
    TakeoverRecord,
    add_qualifications,
    get_qualifications,
    get_takeover_by_request,
    get_takeover_for_batch,
    insert_takeover_request,
    set_takeover_run,
)
from layout.plan import plan_to_record


def _identity(record: TakeoverRecord, sheets: list[dict]) -> dict:
    return {
        'batch_id': record.batch_id,
        'request_id': record.request_id,
        'from_press': record.from_press,
        'to_press': record.to_press,
        'sheets': sheets,
    }


def _delivered_response(db: sqlite3.Connection, record: TakeoverRecord) -> dict:
    batch = get_batch(db, record.batch_id)
    issued = get_issued_batch(db, record.batch_id)
    if issued is None:
        raise DeskConflict('delivery_not_found')
    by_number = {sheet.number: sheet for sheet in issued.sheets}
    sheets = []
    for planned in batch.plan.sheets:
        actual = by_number[planned.number]
        sheets.append({
            'number': planned.number,
            'state': 'printed',
            'press_id': actual.press_id,
            'run_id': actual.run_id,
            'ejection_id': actual.ejection_id,
        })
    return _identity(record, sheets)


def build_takeover_view(db: sqlite3.Connection, client: PressClient,
                        record: TakeoverRecord) -> dict:
    issued = get_issued_batch(db, record.batch_id)
    if issued is not None:
        return _delivered_response(db, record)

    batch = get_batch(db, record.batch_id)
    progress = read_progress(db, client, record.batch_id)
    actual = {event['number']: event for event in progress.actual}
    qualified = set(get_qualifications(db, record.batch_id))
    sheets = []
    for planned in batch.plan.sheets:
        number = planned.number
        event = actual.get(number)
        if event is not None:
            sheets.append({
                'number': number,
                'state': 'printed',
                'press_id': event['press_id'],
                'run_id': event['run_id'],
                'ejection_id': event['ejection_id'],
            })
        elif record.to_run_id is not None and number in qualified:
            sheets.append({
                'number': number,
                'state': 'ready',
                'press_id': record.to_press,
                'run_id': record.to_run_id,
                'ejection_id': None,
            })
        else:
            sheets.append({
                'number': number,
                'state': 'pending',
                'press_id': None,
                'run_id': None,
                'ejection_id': None,
            })
    return _identity(record, sheets)


def _validate_prepared(prepared, batch, from_press: str) -> list[int]:
    if not isinstance(prepared, dict):
        raise DeskConflict('invalid_takeover_evidence')
    if (prepared.get('batch_id') != batch.batch_id
            or prepared.get('press_id') != from_press
            or prepared.get('run_id') != batch.run_id):
        raise DeskConflict('invalid_takeover_evidence')
    eligible = prepared.get('eligible')
    planned = {sheet.number for sheet in batch.plan.sheets}
    if (not isinstance(eligible, list)
            or any(type(number) is not int or number not in planned for number in eligible)
            or len(set(eligible)) != len(eligible)):
        raise DeskConflict('invalid_takeover_evidence')
    return eligible


def continue_takeover(db: sqlite3.Connection, client: PressClient,
                      record: TakeoverRecord) -> dict:
    if get_issued_batch(db, record.batch_id) is not None:
        return build_takeover_view(db, client, record)

    batch = get_batch(db, record.batch_id)
    if batch.run_id is None or batch.press_id != record.from_press:
        raise DeskConflict('batch_not_dispatched')

    prepared = client.prepare_takeover(record.from_press, batch.run_id, record.request_id)
    eligible = _validate_prepared(prepared, batch, record.from_press)
    with db:
        add_qualifications(db, batch.batch_id, record.request_id, eligible)

    to_run_id = record.to_run_id
    if to_run_id is None:
        qualifications = get_qualifications(db, batch.batch_id)
        if qualifications:
            remote = client.enqueue(record.to_press, batch.batch_id,
                                     plan_to_record(batch.plan))
            if (not isinstance(remote, dict)
                    or remote.get('batch_id') != batch.batch_id
                    or remote.get('press_id') != record.to_press
                    or not isinstance(remote.get('run_id'), str)
                    or not remote['run_id']):
                raise DeskConflict('run_reference_conflict')
            to_run_id = remote['run_id']
            with db:
                set_takeover_run(db, record.request_id, to_run_id)
            record = TakeoverRecord(record.request_id, record.batch_id,
                                    record.from_press, record.to_press, to_run_id)

    if to_run_id is not None:
        qualifications = get_qualifications(db, batch.batch_id)
        client.sync_eligibility(record.to_press, to_run_id, list(qualifications))

    return build_takeover_view(db, client, record)


def submit_takeover(db: sqlite3.Connection, client: PressClient, batch_id: str,
                    request_id: str, from_press: str, to_press: str) -> dict:
    existing = get_takeover_by_request(db, request_id)
    if existing is not None:
        if (existing.batch_id, existing.from_press, existing.to_press) != (
                batch_id, from_press, to_press):
            raise DeskConflict('takeover_request_conflict')
        return continue_takeover(db, client, existing)

    batch = get_batch(db, batch_id)
    if not client.knows_press(from_press) or not client.knows_press(to_press):
        raise KeyError('unknown_press')
    if from_press == to_press:
        raise DeskConflict('takeover_same_press')
    if batch.run_id is None or batch.press_id is None:
        raise DeskConflict('batch_not_dispatched')
    if batch.press_id != from_press:
        raise DeskConflict('takeover_from_press_conflict')
    if get_issued_batch(db, batch_id) is not None:
        raise DeskConflict('batch_delivered')
    if get_takeover_for_batch(db, batch_id) is not None:
        raise DeskConflict('takeover_already_requested')

    record = TakeoverRecord(request_id, batch_id, from_press, to_press, None)
    try:
        with db:
            insert_takeover_request(db, record)
    except sqlite3.IntegrityError as exc:
        same_request = get_takeover_by_request(db, request_id)
        if same_request is not None:
            if (same_request.batch_id, same_request.from_press, same_request.to_press) == (
                    batch_id, from_press, to_press):
                return continue_takeover(db, client, same_request)
            raise DeskConflict('takeover_request_conflict') from exc
        raise DeskConflict('takeover_already_requested') from exc
    return continue_takeover(db, client, record)


def get_takeover_view(db: sqlite3.Connection, client: PressClient,
                      batch_id: str, request_id: str) -> dict:
    get_batch(db, batch_id)
    record = get_takeover_by_request(db, request_id)
    if record is None or record.batch_id != batch_id:
        raise KeyError('unknown_takeover')
    return build_takeover_view(db, client, record)
