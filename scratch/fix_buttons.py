import os
import re

for filename in os.listdir("templates"):
    if not filename.endswith(".html"):
        continue
    filepath = os.path.join("templates", filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Add type="button" to buttons that don't have a type attribute
    new_content = re.sub(r'<button\b(?![^>]*\btype=)', r'<button type="button"', content)

    if new_content != content:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Fixed buttons in {filename}")
