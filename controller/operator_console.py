# Controller Slice 1: Operator Console Implementation

import tkinter as tk
from tkinter import ttk
from controller.runtime_supervisor import RuntimeSupervisor


class OperatorConsole(tk.Tk):
    def __init__(self, runtime_supervisor: RuntimeSupervisor):
        super().__init__()
        self.title('Operator Console')
        self.geometry('800x600')
        self.runtime_supervisor = runtime_supervisor
        self.create_widgets()

    def create_widgets(self):
        # Controller state display
        self.controller_state = ttk.Label(self, text="Controller State: Unknown")
        self.controller_state.pack()

        # OPS-001 worker status card
        self.ops_worker_status = ttk.Label(self, text="OPS-001 Status: Unknown")
        self.ops_worker_status.pack()

        # DEV-001 worker status card
        self.dev_worker_status = ttk.Label(self, text="DEV-001 Status: Unknown")
        self.dev_worker_status.pack()

        # QA-001 worker status card
        self.qa_worker_status = ttk.Label(self, text="QA-001 Status: Unknown")
        self.qa_worker_status.pack()

        # Queue and mission summary display
        self.queue_summary = ttk.Label(self, text="Queue Summary: None")
        self.queue_summary.pack()

    def refresh(self):
        try:
            # Update controller state
            controller_state = self.runtime_supervisor.get_controller_state()
            self.controller_state.config(text=f"Controller State: {controller_state}")

            # Update worker statuses
            ops_status = self.runtime_supervisor.get_worker_status('OPS-001')
            self.ops_worker_status.config(text=f"OPS-001 Status: {ops_status}")

            dev_status = self.runtime_supervisor.get_worker_status('DEV-001')
            self.dev_worker_status.config(text=f"DEV-001 Status: {dev_status}")

            qa_status = self.runtime_supervisor.get_worker_status('QA-001')
            self.qa_worker_status.config(text=f"QA-001 Status: {qa_status}")

            # Update queue summary
            queue_summary = self.runtime_supervisor.get_queue_summary()
            self.queue_summary.config(text=f"Queue Summary: {queue_summary}")

        except Exception as e:
            self.controller_state.config(text=f"Controller State: Error - {str(e)}")
            self.ops_worker_status.config(text="OPS-001 Status: Error")
            self.dev_worker_status.config(text="DEV-001 Status: Error")
            self.qa_worker_status.config(text="QA-001 Status: Error")
            self.queue_summary.config(text="Queue Summary: Error")

    def start_console(self):
        self.mainloop()


if __name__ == '__main__':
    runtime_supervisor = RuntimeSupervisor()
    OperatorConsole(runtime_supervisor).start_console()

