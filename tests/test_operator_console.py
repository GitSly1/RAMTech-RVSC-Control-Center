# Test Operator Console Implementation

import unittest
from controller.operator_console import OperatorConsole
from controller.runtime_supervisor import RuntimeSupervisor


class TestOperatorConsole(unittest.TestCase):
    def test_console_creation(self):
        runtime_supervisor = RuntimeSupervisor()
        console = OperatorConsole(runtime_supervisor)
        self.assertIsInstance(console, OperatorConsole)

    def test_console_refresh(self):
        runtime_supervisor = RuntimeSupervisor()
        console = OperatorConsole(runtime_supervisor)
        console.refresh()
        self.assertIn('Controller State', console.controller_state.cget('text'))
        self.assertIn('OPS-001', console.ops_worker_status.cget('text'))
        self.assertIn('DEV-001', console.dev_worker_status.cget('text'))
        self.assertIn('QA-001', console.qa_worker_status.cget('text'))
        self.assertIn('Queue Summary', console.queue_summary.cget('text'))


if __name__ == '__main__':
    unittest.main()

