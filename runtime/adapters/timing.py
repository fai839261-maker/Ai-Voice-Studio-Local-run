"""One line of timing information per generated part; the app turns it into the speed report."""
import json


def emit(task, **fields):
    print("AVS_TIMING " + json.dumps({"model": task.get("model"), **fields}), flush=True)
