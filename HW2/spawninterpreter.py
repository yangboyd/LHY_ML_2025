"""
DO NOT MODIFY THIS CELL

Python interpreter for executing code snippets and capturing their output.
"""


import logging
import os
import queue
import signal
import sys
import time
import traceback
import zipfile
from pathlib import Path
from shutil import rmtree
import shutil
from multiprocessing import Process, Queue
from typing import Hashable, cast

import humanize
import rich
import shutup
from rich.logging import RichHandler
from rich.syntax import Syntax
from dataclasses import dataclass
from dataclasses_json import DataClassJsonMixin


@dataclass
class ExecutionResult(DataClassJsonMixin):
    """
    Result of executing a code snippet in the interpreter.
    Contains the output, execution time, and exception information.
    """
    term_out: list[str]
    exec_time: float
    exc_type: str | None
    exc_info: dict | None = None
    exc_stack: list[tuple] | None = None

def exception_summary(e, exec_file_name):
    """Generates a string that summarizes an exception and its stack trace"""
    tb_lines = traceback.format_exception(e)
    # Combine the traceback lines into a single string, skipping lines that contain "importlib".
    tb_str = "".join(
        [
            line
            for line in tb_lines
            # if "importlib" not in line  # Filter out unwanted traceback lines.
        ]
    )

    exc_info = {}
    if hasattr(e, "args"):
        exc_info["args"] = [str(i) for i in e.args]  # Store the exception arguments as strings.
    for att in ["name", "msg", "obj"]:
        if hasattr(e, att):
            exc_info[att] = str(getattr(e, att))  # Store additional attributes if available.

    tb = traceback.extract_tb(e.__traceback__)  # Extract the traceback information.
    # Create a list of tuples for each frame in the traceback.
    exc_stack = [(t.filename, t.lineno, t.name, t.line) for t in tb]

    return tb_str, e.__class__.__name__, exc_info, exc_stack  # Return the formatted traceback and exception details.



# Define a class that redirects write operations to a multiprocessing queue.
class RedirectQueue:
    def __init__(self, queue, timeout=5):
        self.queue = queue  # Store the provided queue.
        self.timeout = timeout  # Set the timeout for queue operations.

    def write(self, msg):
        try:
            self.queue.put(msg, timeout=self.timeout)  # Attempt to put the message into the queue.
        except queue.Full:
            print.warning("Queue write timed out")  # Warn if the queue is full and the write times out.

    def flush(self):
        pass  # No operation is needed for flushing in this context.

# Define the Interpreter class that simulates a standalone Python REPL.
class Interpreter:
    def __init__(
        self,
        timeout: int = 3600,  # Default timeout of 3600 seconds.
        agent_file_name: str = "runfile.py",  # Default file name for writing the agent's code.
    ):
        """
        Simulates a standalone Python REPL with an execution time limit.

        Args:
            timeout (int, optional): Timeout for each code execution step. Defaults to 3600.
            agent_file_name (str, optional): The name for the agent's code file. Defaults to "runfile.py".
        """
        self.timeout = timeout  # Save the timeout value.
        self.agent_file_name = agent_file_name  # Save the agent file name.
        self.process: Process = None  # Initialize the process attribute (will hold the child process).

    def child_proc_setup(self, result_outq: Queue) -> None:
        # Import shutup to suppress warnings in the child process.
        import shutup

        shutup.mute_warnings()  # Mute all warnings before further execution.

        # Redirect both stdout and stderr to the provided result queue.
        # trunk-ignore(mypy/assignment)
        sys.stdout = sys.stderr = RedirectQueue(result_outq)

    def _run_session(
        self, code_inq: Queue, result_outq: Queue, event_outq: Queue
    ) -> None:
        self.child_proc_setup(result_outq)  # Set up the child process for capturing output.

        global_scope: dict = {}  # Create an empty dictionary to serve as the global scope.
        while True:  # Continuously wait for new code to execute.
            code = code_inq.get()  # Retrieve code from the code input queue.
            with open(self.agent_file_name, "w") as f:  # Open the agent file for writing.
                f.write(code)  # Write the received code into the file.

            event_outq.put(("state:ready",))  # Signal that the interpreter is ready to execute the code.
            try:
                # Compile and execute the code within the global scope.
                exec(compile(code, self.agent_file_name, "exec"), global_scope)
            except BaseException as e:
                # If an exception occurs, generate a summary of the exception.
                tb_str, e_cls_name, exc_info, exc_stack = exception_summary(
                    e,
                    self.agent_file_name,
                )
                result_outq.put(tb_str)  # Put the traceback string into the result queue.
                if e_cls_name == "KeyboardInterrupt":
                    e_cls_name = "TimeoutError"  # Convert a KeyboardInterrupt into a TimeoutError.

                event_outq.put(("state:finished", e_cls_name, exc_info, exc_stack))  # Signal that execution finished with an error.
            else:
                event_outq.put(("state:finished", None, None, None))  # Signal that execution finished successfully.

            os.remove(self.agent_file_name)  # Remove the agent file after execution.

            result_outq.put("<|EOF|>")  # Put an EOF marker to indicate the end of output.

  
def run_method_in_process(code_inq: Queue, result_outq: Queue, event_outq: Queue):
    """Helper function to run a method of an object in a process."""
    interpreter = Interpreter()
    interpreter._run_session(code_inq, result_outq, event_outq)

def foo(q,r,s):
  print("code: ",q.get(3))
  r.put(("state:ready",))
  s.put(("state:ready",))