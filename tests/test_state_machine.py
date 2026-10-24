import unittest

from app import state_machine as sm
from tests.helpers import FakeClock


class StateMachineTest(unittest.TestCase):
    def test_the_normal_cycle_is_allowed(self):
        machine = sm.StateMachine()
        for target in (sm.IDLE, sm.LISTENING, sm.PROCESSING_SPEECH, sm.GENERATING_RESPONSE,
                       sm.SPEAKING, sm.LISTENING, sm.PROCESSING_SPEECH, sm.GENERATING_RESPONSE,
                       sm.SPEAKING, sm.PRACTICE_WAITING, sm.LISTENING):
            self.assertTrue(machine.go(target, "test"), f"{machine.state} -> {target}")

    def test_refused_transition_changes_nothing(self):
        machine = sm.StateMachine()
        machine.go(sm.IDLE)
        self.assertFalse(machine.go(sm.PROCESSING_SPEECH))
        self.assertEqual(machine.state, sm.IDLE)
        with self.assertRaises(sm.InvalidTransition):
            machine.must(sm.PROCESSING_SPEECH)

    def test_shutting_down_is_final(self):
        machine = sm.StateMachine()
        machine.go(sm.SHUTTING_DOWN)
        for target in sm.STATES:
            self.assertFalse(machine.can(target))

    def test_every_state_can_reach_error_idle_and_shutdown(self):
        for state, targets in sm.TRANSITIONS.items():
            if state == sm.SHUTTING_DOWN:
                continue
            self.assertIn(sm.SHUTTING_DOWN, targets, state)
            if state != sm.ERROR:
                self.assertIn(sm.ERROR, targets, state)
            if state != sm.IDLE:
                self.assertIn(sm.IDLE, targets, state)

    def test_no_state_is_a_dead_end(self):
        for state, targets in sm.TRANSITIONS.items():
            if state != sm.SHUTTING_DOWN:
                self.assertTrue(targets - {state}, state)

    def test_deadlines_and_overdue(self):
        clock = FakeClock()
        machine = sm.StateMachine(clock=clock)
        machine.go(sm.IDLE)
        self.assertFalse(machine.overdue())
        clock.advance(10_000)
        self.assertFalse(machine.overdue(), "IDLE has no deadline")
        machine.go(sm.GENERATING_RESPONSE)
        clock.advance(sm.DEADLINES[sm.GENERATING_RESPONSE] + 1)
        self.assertTrue(machine.overdue())
        machine.go(sm.SPEAKING, deadline=5)
        clock.advance(4)
        self.assertFalse(machine.overdue())
        clock.advance(2)
        self.assertTrue(machine.overdue())

    def test_listener_and_history(self):
        seen = []
        machine = sm.StateMachine(on_change=lambda a, b, r: seen.append((a, b, r)))
        machine.go(sm.IDLE, "ready")
        self.assertEqual(seen, [(sm.INITIALISING, sm.IDLE, "ready")])
        self.assertEqual(machine.history[-1][1:], (sm.INITIALISING, sm.IDLE, "ready"))


if __name__ == "__main__":
    unittest.main()
