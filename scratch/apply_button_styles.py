import os
import re

target_dir = r"d:\My projects\AGENT X\SENTINEL-main\src\components"
PRIMARY_CLS = "rounded-full bg-zinc-950 px-5 py-2 text-sm font-medium text-white transition hover:bg-zinc-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
SECONDARY_CLS = "rounded-lg border border-zinc-300 px-3 py-1.5 text-sm font-medium transition hover:bg-zinc-100 dark:border-zinc-800 dark:hover:bg-zinc-900"

count_primary = 0
count_secondary = 0

def replacer(match):
    global count_primary, count_secondary
    entire_tag = match.group(0)
    
    # We don't want to replace tiny icon buttons, so we try to guess based on existing classes
    if 'bg-amber' in entire_tag or 'bg-blue' in entire_tag or 'primary' in entire_tag or 'text-amber' in entire_tag:
        cls = PRIMARY_CLS
        count_primary += 1
    else:
        cls = SECONDARY_CLS
        count_secondary += 1
        
    # Find className="..." and replace its contents.
    # Note: clsx("...", ...) makes this harder if it's dynamic.
    # Let's simplify: replace the entire className="..." or className={...} if possible,
    # or just replace the inner string.
    
    if 'className="' in entire_tag:
        return re.sub(r'className="[^"]*"', f'className="{cls}"', entire_tag)
    elif "className='" in entire_tag:
        return re.sub(r"className='[^']*'", f'className="{cls}"', entire_tag)
    elif "className={" in entire_tag:
        # A bit riskier if there is logic inside, but let's replace the whole clsx block
        # with our fixed class since we're sweeping.
        # This regex matches className={ ... } non-greedily, but only if it's within the tag
        return re.sub(r'className=\{[^}]*\}', f'className="{cls}"', entire_tag)
    else:
        # No className, just insert it
        return entire_tag.replace('<button', f'<button className="{cls}"', 1)

for root, _, files in os.walk(target_dir):
    for filename in files:
        if filename.endswith(".tsx") or filename.endswith(".jsx"):
            filepath = os.path.join(root, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Find all <button ... > tags
            new_content = re.sub(r'<button\b[^>]*>', replacer, content, flags=re.DOTALL)

            if new_content != content:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Updated buttons in {filename}")

print(f"Total primary: {count_primary}")
print(f"Total secondary: {count_secondary}")
