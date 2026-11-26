import json

from samples.takeover_fixture import TakeoverWorkshop, seed_incident


def _ok(response, expected=200):
    status, payload = response
    if status != expected:
        raise AssertionError(f'expected HTTP {expected}, got {status}: {payload}')
    return payload


def main():
    with TakeoverWorkshop() as rig:
        seeded = seed_incident(rig)
        batch_id = seeded['batch']['batch_id']
        original_run = seeded['original']['run_id']
        prefix = f'/batches/{batch_id}'
        takeover_body = {'request_id': 'change-demo', 'from_press': 'A', 'to_press': 'B'}

        takeover = _ok(rig.desk(prefix + '/takeover', takeover_body))
        b_run = takeover['sheets'][1]['run_id']
        for request_id in ('front-042-b', 'back-042-b', 'eject-042-b'):
            _ok(rig.press('B', f'/runs/{b_run}/advance', {'request_id': request_id}))

        checklist_after_042 = _ok(rig.desk(f'{prefix}/takeover/change-demo'))
        if [sheet['state'] for sheet in checklist_after_042['sheets']] != [
                'printed', 'printed', 'pending']:
            raise AssertionError('042 must be printed on B while 043 remains pending')

        rig.controls['A'].hold_before_arrival.remove('front-42')
        late_42 = _ok(rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'front-42'}))
        back_42 = _ok(rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'back-42'}))
        blocked_eject_status, blocked_eject_42 = rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'eject-42'})
        if blocked_eject_status != 409:
            raise AssertionError(f'A ejection must remain blocked: {blocked_eject_42}')

        rig.controls['A'].hold_before_effect.remove('eject-43')
        _ok(rig.press('A', f'/runs/{original_run}/advance', {'request_id': 'eject-43'}))
        progress = _ok(rig.desk(prefix + '/progress'))
        if progress['missing']:
            raise AssertionError('batch must be complete before inspection')

        deviations = rig.sample['deviations_mm']
        measurements = [{
            'ejection_id': event['ejection_id'],
            'deviation_mm': deviations[str(event['number'])],
        } for event in progress['actual']]
        delivery = _ok(rig.desk(prefix + '/inspection', {'measurements': measurements}))
        delivery = _ok(rig.desk(prefix + '/delivery'))

        devices = {
            press_id: rig.devices[press_id].ejection_events()
            for press_id in ('A', 'B')
        }
        print(json.dumps({
            'checklist_after_042': checklist_after_042,
            'late_a_front_042': late_42,
            'late_a_back_042': back_42,
            'blocked_a_eject_042': {'status': blocked_eject_status, 'response': blocked_eject_42},
            'progress': progress,
            'device_ejection_records': devices,
            'delivery': delivery,
        }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
