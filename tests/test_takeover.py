from http.client import RemoteDisconnected
import threading
import unittest

from samples.takeover_fixture import TakeoverWorkshop, seed_incident


class TakeoverHttpTest(unittest.TestCase):
    def setUp(self):
        self.rig = self.enterContext(TakeoverWorkshop())

    def takeover(self, batch_id, *, request_id='change-1', **overrides):
        body = {'request_id': request_id, 'from_press': 'A', 'to_press': 'B'}
        body.update(overrides)
        return self.rig.desk(f'/batches/{batch_id}/takeover', body)

    def get_takeover(self, batch_id, request_id='change-1'):
        return self.rig.desk(f'/batches/{batch_id}/takeover/{request_id}')

    def print_number_on_b(self, run_id, number):
        for stage in ('front', 'back', 'eject'):
            status, result = self.rig.press(
                'B', f'/runs/{run_id}/advance',
                {'request_id': f'{stage}-{number}-b'})
            self.assertEqual(status, 200, result)

    def inspect_and_deliver(self, batch_id, progress):
        measurements = [{'ejection_id': event['ejection_id'], 'deviation_mm': 0}
                        for event in progress['actual']]
        status, issued = self.rig.desk(f'/batches/{batch_id}/inspection',
                                       {'measurements': measurements})
        self.assertEqual(status, 200, issued)
        status, delivery = self.rig.desk(f'/batches/{batch_id}/delivery')
        self.assertEqual(status, 200, delivery)
        self.assertEqual(issued, delivery)
        return delivery

    def test_late_a_ejection_ending(self):
        seed = seed_incident(self.rig)
        batch_id = seed['batch']['batch_id']
        original_run = seed['original']['run_id']

        status, view = self.takeover(batch_id)
        self.assertEqual(status, 200, view)
        self.assertEqual(
            [(sheet['number'], sheet['state']) for sheet in view['sheets']],
            [(41, 'printed'), (42, 'ready'), (43, 'pending')])
        self.assertEqual(view['sheets'][0]['ejection_id'], 'A-P1')
        self.assertEqual((view['sheets'][1]['press_id'], view['sheets'][1]['run_id']),
                         ('B', 'B-R1'))
        self.assertEqual(view['sheets'][2:], [{
            'number': 43, 'state': 'pending', 'press_id': None,
            'run_id': None, 'ejection_id': None}])
        self.assertEqual(self.get_takeover(batch_id), (200, view))

        self.print_number_on_b('B-R1', 42)
        status, after_b = self.takeover(batch_id)
        self.assertEqual(status, 200, after_b)
        self.assertEqual(
            [(sheet['number'], sheet['state'], sheet['press_id'], sheet['ejection_id'])
             for sheet in after_b['sheets']],
            [(41, 'printed', 'A', 'A-P1'),
             (42, 'printed', 'B', 'B-P1'),
             (43, 'pending', None, None)])

        self.rig.reopen_desk()
        self.rig.reopen_press('A')
        status, late_42 = self.rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'front-42'})
        self.assertEqual(status, 200, late_42)
        self.assertEqual(late_42['stage'], 'front')
        status, back_42 = self.rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'back-42'})
        self.assertEqual(status, 200, back_42)
        status, eject_42 = self.rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'eject-42'})
        self.assertEqual(status, 409, eject_42)
        self.assertEqual([e['number'] for e in self.rig.devices['A'].ejection_events()], [41])
        self.assertEqual([e['number'] for e in self.rig.devices['B'].ejection_events()], [42])

        status, eject_43 = self.rig.press(
            'A', f'/runs/{original_run}/advance', {'request_id': 'eject-43'})
        self.assertEqual(status, 200, eject_43)
        self.assertEqual(eject_43['ejection_id'], 'A-P2')

        status, progress = self.rig.desk(f'/batches/{batch_id}/progress')
        self.assertEqual(status, 200, progress)
        self.assertEqual(progress['missing'], [])
        self.assertEqual(
            [(e['number'], e['press_id'], e['run_id'], e['ejection_id'])
             for e in progress['actual']],
            [(41, 'A', 'A-R1', 'A-P1'),
             (42, 'B', 'B-R1', 'B-P1'),
             (43, 'A', 'A-R1', 'A-P2')])

        delivery = self.inspect_and_deliver(batch_id, progress)
        self.assertEqual(
            [(row['print_number'], row['press_id'], row['ejection_id'])
             for row in delivery['sheets']],
            [('041', 'A', 'A-P1'), ('042', 'B', 'B-P1'), ('043', 'A', 'A-P2')])

        self.rig.stop_press('A')
        self.rig.stop_press('B')
        self.rig.reopen_desk()
        status, offline_delivery = self.rig.desk(f'/batches/{batch_id}/delivery')
        self.assertEqual(status, 200, offline_delivery)
        self.assertEqual(offline_delivery, delivery)
        status, replayed = self.takeover(batch_id)
        self.assertEqual(status, 200, replayed)
        self.assertEqual(self.get_takeover(batch_id), (200, replayed))

    def test_discard_and_b_reprint_ending_after_restart(self):
        seed = seed_incident(self.rig)
        batch_id = seed['batch']['batch_id']

        status, view = self.takeover(batch_id)
        self.assertEqual(status, 200, view)
        self.print_number_on_b('B-R1', 42)

        self.rig.reopen_desk()
        self.rig.reopen_press('A')
        discard_path = f"/runs/{seed['original']['run_id']}/discard"
        receipt_body = {'request_id': 'eject-43'}
        expected_receipt = {'request_id': 'eject-43', 'run_id': 'A-R1',
                            'number': 43, 'state': 'discarded'}
        status, receipt = self.rig.press('A', discard_path, receipt_body)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt, expected_receipt)
        self.assertEqual(self.rig.press('A', discard_path, receipt_body),
                         (200, expected_receipt))

        status, late = self.rig.press(
            'A', f"/runs/{seed['original']['run_id']}/advance",
            {'request_id': 'eject-43'})
        self.assertEqual(status, 409, late)
        self.assertEqual([e['number'] for e in self.rig.devices['A'].ejection_events()], [41])

        status, renewed = self.takeover(batch_id)
        self.assertEqual(status, 200, renewed)
        self.assertEqual(renewed['sheets'][2], {
            'number': 43, 'state': 'ready', 'press_id': 'B',
            'run_id': 'B-R1', 'ejection_id': None})
        self.print_number_on_b('B-R1', 43)

        status, progress = self.rig.desk(f'/batches/{batch_id}/progress')
        self.assertEqual(status, 200, progress)
        self.assertEqual(
            [(e['number'], e['press_id'], e['ejection_id']) for e in progress['actual']],
            [(41, 'A', 'A-P1'), (42, 'B', 'B-P1'), (43, 'B', 'B-P2')])
        delivery = self.inspect_and_deliver(batch_id, progress)
        self.assertEqual(
            [(row['print_number'], row['press_id']) for row in delivery['sheets']],
            [('041', 'A'), ('042', 'B'), ('043', 'B')])

    def test_lost_post_reply_replay_and_conflicts(self):
        seed = seed_incident(self.rig)
        batch_id = seed['batch']['batch_id']
        path = f'/batches/{batch_id}/takeover'
        body = {'request_id': 'change-lost', 'from_press': 'A', 'to_press': 'B'}
        self.rig.drop_next_post.add(('batches', batch_id, 'takeover'))
        with self.assertRaises(RemoteDisconnected):
            self.rig.desk(path, body)

        self.rig.reopen_desk()
        status, view = self.get_takeover(batch_id, 'change-lost')
        self.assertEqual(status, 200, view)
        self.assertEqual([sheet['state'] for sheet in view['sheets']],
                         ['printed', 'ready', 'pending'])
        status, replayed = self.rig.desk(path, body)
        self.assertEqual(status, 200, replayed)
        self.assertEqual(view, replayed)
        self.assertEqual(self.rig.desk(path, {**body, 'to_press': 'A'})[0], 409)
        self.assertEqual(self.takeover(batch_id, request_id='change-second')[0], 409)
        self.assertEqual(self.get_takeover(batch_id, 'missing')[0], 404)

    def test_takeover_request_matrix(self):
        seed = seed_incident(self.rig)
        batch_id = seed['batch']['batch_id']
        self.assertEqual(self.takeover('B404')[0], 404)
        self.assertEqual(self.takeover(batch_id, to_press='X')[0], 404)
        self.assertEqual(self.takeover(batch_id, to_press='A')[0], 409)
        self.assertEqual(self.takeover(batch_id, from_press='B', to_press='A')[0], 409)

        revision_status, revision = self.rig.desk('/editions', self.rig.sample['draft'])
        self.assertEqual(revision_status, 201, revision)
        status, unpatched = self.rig.desk('/batches', {
            'revision_id': revision['revision_id'], 'start': 51, 'count': 1})
        self.assertEqual(status, 201, unpatched)
        self.assertEqual(self.takeover(unpatched['batch_id'])[0], 409)

        status, accepted = self.takeover(batch_id, request_id='change-matrix')
        self.assertEqual(status, 200, accepted)
        self.assertEqual(self.takeover(batch_id, request_id='change-other')[0], 409)
        self.assertEqual(self.rig.desk(f'/batches/{batch_id}/takeover/nope')[0], 404)

    def test_discard_requires_arrived_action_and_takeover(self):
        revision_status, revision = self.rig.desk('/editions', self.rig.sample['draft'])
        self.assertEqual(revision_status, 201, revision)
        status, batch = self.rig.desk('/batches', {
            'revision_id': revision['revision_id'], 'start': 61, 'count': 1})
        self.assertEqual(status, 201, batch)
        status, ref = self.rig.desk(f"/batches/{batch['batch_id']}/dispatch",
                                   {'press_id': 'A', 'request_id': 'dispatch'})
        self.assertEqual(status, 200, ref)
        run_id = ref['run_id']
        for request_id in ('front-61', 'back-61'):
            status, result = self.rig.press('A', f'/runs/{run_id}/advance',
                                            {'request_id': request_id})
            self.assertEqual(status, 200, result)
        self.rig.controls['A'].hold_before_effect.add('eject-61')
        self.assertEqual(self.rig.press('A', f'/runs/{run_id}/advance',
                                        {'request_id': 'eject-61'})[0], 503)

        discard_path = f'/runs/{run_id}/discard'
        self.assertEqual(self.rig.press('A', discard_path,
                                        {'request_id': 'eject-61'})[0], 409)
        status, _ = self.takeover(batch['batch_id'])
        self.assertEqual(status, 200)
        self.assertEqual(self.rig.press('A', discard_path,
                                        {'request_id': 'front-61'})[0], 409)
        self.assertEqual(self.rig.press('A', discard_path,
                                        {'request_id': 'missing'})[0], 404)
        status, receipt = self.rig.press('A', discard_path,
                                        {'request_id': 'eject-61'})
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt['state'], 'discarded')

    def test_blocked_old_face_instruction_replays_but_cannot_eject(self):
        revision_status, revision = self.rig.desk('/editions', self.rig.sample['draft'])
        self.assertEqual(revision_status, 201, revision)
        status, batch = self.rig.desk('/batches', {
            'revision_id': revision['revision_id'], 'start': 71, 'count': 1})
        self.assertEqual(status, 201, batch)
        status, ref = self.rig.desk(f"/batches/{batch['batch_id']}/dispatch",
                                    {'press_id': 'A', 'request_id': 'dispatch'})
        self.assertEqual(status, 200, ref)
        run_id = ref['run_id']

        entered = threading.Event()
        release = threading.Event()

        def block_arrival(action):
            if action.request_id == 'front-71':
                entered.set()
                self.assertTrue(release.wait(5))

        self.rig.devices['A'].before_arrival = block_arrival
        outcome = {}

        def old_call():
            outcome['response'] = self.rig.press(
                'A', f'/runs/{run_id}/advance', {'request_id': 'front-71'})

        worker = threading.Thread(target=old_call)
        worker.start()
        self.assertTrue(entered.wait(5))
        status, view = self.takeover(batch['batch_id'])
        self.assertEqual(status, 200, view)
        self.assertEqual(view['sheets'][0]['state'], 'ready')
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome['response'][0], 200, outcome['response'])
        status, back = self.rig.press(
            'A', f'/runs/{run_id}/advance', {'request_id': 'back-71'})
        self.assertEqual(status, 200, back)
        status, eject = self.rig.press(
            'A', f'/runs/{run_id}/advance', {'request_id': 'eject-71'})
        self.assertEqual(status, 409, eject)
        self.assertEqual(self.rig.devices['A'].count_ejections(run_id), 0)

    def test_completed_lost_face_action_can_replay_without_ejection(self):
        revision_status, revision = self.rig.desk('/editions', self.rig.sample['draft'])
        self.assertEqual(revision_status, 201, revision)
        status, batch = self.rig.desk('/batches', {
            'revision_id': revision['revision_id'], 'start': 91, 'count': 1})
        self.assertEqual(status, 201, batch)
        status, ref = self.rig.desk(f"/batches/{batch['batch_id']}/dispatch",
                                    {'press_id': 'A', 'request_id': 'dispatch'})
        self.assertEqual(status, 200, ref)
        run_id = ref['run_id']
        self.rig.controls['A'].drop_after_effect.add('front-91')
        with self.assertRaises(RemoteDisconnected):
            self.rig.press('A', f'/runs/{run_id}/advance', {'request_id': 'front-91'})
        self.assertIsNone(self.rig.saved_advance('A', 'front-91'))

        status, view = self.takeover(batch['batch_id'])
        self.assertEqual(status, 200, view)
        self.assertEqual(view['sheets'][0]['state'], 'ready')
        status, replay = self.rig.press('A', f'/runs/{run_id}/advance',
                                        {'request_id': 'front-91'})
        self.assertEqual(status, 200, replay)
        self.assertEqual((replay['number'], replay['stage']), (91, 'front'))
        status, back = self.rig.press('A', f'/runs/{run_id}/advance',
                                      {'request_id': 'back-91'})
        self.assertEqual(status, 200, back)
        status, eject = self.rig.press('A', f'/runs/{run_id}/advance',
                                       {'request_id': 'eject-91'})
        self.assertEqual(status, 409, eject)
        self.assertEqual(self.rig.devices['A'].count_ejections(run_id), 0)

    def test_discard_race_against_paused_effect(self):
        revision_status, revision = self.rig.desk('/editions', self.rig.sample['draft'])
        self.assertEqual(revision_status, 201, revision)
        status, batch = self.rig.desk('/batches', {
            'revision_id': revision['revision_id'], 'start': 81, 'count': 1})
        self.assertEqual(status, 201, batch)
        status, ref = self.rig.desk(f"/batches/{batch['batch_id']}/dispatch",
                                    {'press_id': 'A', 'request_id': 'dispatch'})
        self.assertEqual(status, 200, ref)
        run_id = ref['run_id']
        for request_id in ('front-81', 'back-81'):
            self.assertEqual(self.rig.press('A', f'/runs/{run_id}/advance',
                                            {'request_id': request_id})[0], 200)

        entered = threading.Event()
        release = threading.Event()

        def block_effect(action):
            if action.request_id == 'eject-81':
                entered.set()
                self.assertTrue(release.wait(5))

        self.rig.devices['A'].before_effect = block_effect
        outcome = {}

        def eject_call():
            outcome['response'] = self.rig.press(
                'A', f'/runs/{run_id}/advance', {'request_id': 'eject-81'})

        worker = threading.Thread(target=eject_call)
        worker.start()
        self.assertTrue(entered.wait(5))

        status, view = self.takeover(batch['batch_id'])
        self.assertEqual(status, 200, view)
        self.assertEqual(view['sheets'][0]['state'], 'pending')

        discard_path = f'/runs/{run_id}/discard'
        status, receipt = self.rig.press('A', discard_path, {'request_id': 'eject-81'})
        self.assertEqual(status, 200, receipt)
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome['response'][0], 409)
        self.assertEqual(self.rig.devices['A'].count_ejections(run_id), 0)
        self.assertEqual(self.rig.press('A', discard_path, {'request_id': 'eject-81'})[0], 200)


if __name__ == '__main__':
    unittest.main()
