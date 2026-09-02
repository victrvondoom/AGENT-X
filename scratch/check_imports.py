import os
import importlib.util
import sys

root_dir = r"d:\My projects\AGENT X\agentx\subsystems\sentinel_x"
errors = []

for dirpath, _, filenames in os.walk(root_dir):
    for filename in filenames:
        if filename.endswith(".py"):
            filepath = os.path.join(dirpath, filename)
            module_name = "test_import_" + filename[:-3]
            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                module = importlib.util.module_from_spec(spec)
                # We need to temporarily add the root dir to sys.path so absolute imports work
                sys.path.insert(0, r"d:\My projects\AGENT X")
                spec.loader.exec_module(module)
                sys.path.pop(0)
            except Exception as e:
                errors.append(f"{filepath}: {type(e).__name__}: {e}")

for err in errors:
    print(err)
if not errors:
    print("No import errors found.")
