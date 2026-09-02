import os
import re

for filename in os.listdir("templates"):
    if not filename.endswith(".html"):
        continue
    filepath = os.path.join("templates", filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all button tags
    buttons = re.findall(r'<button[^>]*>', content)
    for b in buttons:
        if 'id=' not in b and 'onclick=' not in b:
            print(f"Suspicious button in {filename}: {b}")
