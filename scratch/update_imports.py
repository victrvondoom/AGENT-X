import os
import re

root_dir = r"d:\My projects\AGENT X\agentx\subsystems\sentinel_x"

for dirpath, _, filenames in os.walk(root_dir):
    for filename in filenames:
        if filename.endswith(".py"):
            filepath = os.path.join(dirpath, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Replace 'from app.' with 'from agentx.subsystems.sentinel_x.'
            new_content = re.sub(r'from app\.', r'from agentx.subsystems.sentinel_x.', content)
            # Replace 'import app.' with 'import agentx.subsystems.sentinel_x.'
            new_content = re.sub(r'import app\.', r'import agentx.subsystems.sentinel_x.', new_content)
            # Replace 'from app import' with 'from agentx.subsystems.sentinel_x import'
            new_content = re.sub(r'from app import', r'from agentx.subsystems.sentinel_x import', new_content)

            
            if new_content != content:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Updated {filepath}")
