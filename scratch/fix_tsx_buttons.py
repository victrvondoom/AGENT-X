import os
import re

target_dir = r"d:\My projects\AGENT X\SENTINEL-main\src"
count = 0

for root, _, files in os.walk(target_dir):
    for filename in files:
        if filename.endswith(".tsx") or filename.endswith(".jsx"):
            filepath = os.path.join(root, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Add type="button" to buttons that don't have a type attribute, allowing for multiline
            # We look for <button, then optionally some non-> characters, then >
            # But wait, in JSX, type={"button"} or type="button" can be used.
            # A safer way:
            def replacer(match):
                tag_content = match.group(0)
                if 'type=' in tag_content or 'type={' in tag_content:
                    return tag_content
                # insert type="button" right after <button
                return tag_content.replace('<button', '<button type="button"', 1)
            
            # Match <button followed by any characters until >
            # using DOTALL so it matches across newlines
            new_content = re.sub(r'<button\b[^>]*>', replacer, content, flags=re.DOTALL)

            if new_content != content:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                count += 1
                print(f"Fixed buttons in {filepath}")

print(f"Total files updated: {count}")
