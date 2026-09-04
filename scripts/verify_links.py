"""
scripts/verify_links.py — Link and Anchor Resolution Verifier.

Audits README.md and ARCHITECTURE.md to verify:
1. Every markdown link to local files exists on disk.
2. Every internal markdown anchor resolves to a valid GitHub slugified heading.
3. Every cross-file anchor and line-number anchor resolves.
"""

from __future__ import annotations

import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def github_slug(text: str) -> str:
    """Generate GitHub-compatible anchor slug from heading text."""
    # Remove markdown link formatting inside heading if any
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Lowercase
    slug = text.lower()
    # Remove all punctuation except hyphens, underscores, and spaces
    slug = re.sub(r"[^\w\s-]", "", slug)
    # Replace whitespace with single hyphens
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug


def check_file(filename: str) -> bool:
    print(f"\n=== Checking {filename} ===")
    if not os.path.exists(filename):
        print(f"Error: {filename} does not exist")
        return False

    with open(filename, "r", encoding="utf-8") as f:
        content = f.read()

    # Collect headings
    headings = re.findall(r"^(#+)\s+(.+)$", content, re.MULTILINE)
    anchors = set()
    for level, text in headings:
        slug = github_slug(text)
        anchors.add(slug)

    # Collect links: [text](target)
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
    broken: list[tuple[str, str, str]] = []

    for text, target in links:
        if (
            target.startswith("http://")
            or target.startswith("https://")
            or target.startswith("mailto:")
        ):
            continue

        # Strip trailing gh query/fragments like #gh-light-mode-only
        if "#gh-" in target:
            clean_path = target.split("#")[0]
            if not os.path.exists(clean_path):
                broken.append((text, target, f"Image path {clean_path} not found"))
            continue

        parts = target.split("#")
        target_file = parts[0].strip()
        target_anchor = parts[1].strip() if len(parts) > 1 else None

        if target_file == "":
            # Same file anchor
            if target_anchor not in anchors:
                broken.append((text, target, f"Anchor #{target_anchor} not found in {filename}"))
        else:
            if not os.path.exists(target_file):
                broken.append((text, target, f"File {target_file} not found on disk"))
            elif target_anchor:
                if target_file.endswith((".py", ".json", ".csv")):
                    # Check line number anchor e.g. L123 or L123-L145
                    m = re.match(r"^L(\d+)(?:-L(\d+))?$", target_anchor)
                    if m:
                        with open(target_file, "r", encoding="utf-8") as tf:
                            num_lines = len(tf.readlines())
                        start_l = int(m.group(1))
                        end_l = int(m.group(2)) if m.group(2) else start_l
                        if start_l > num_lines or end_l > num_lines:
                            broken.append(
                                (
                                    text,
                                    target,
                                    f"Line anchor #{target_anchor} exceeds file length ({num_lines} lines) in {target_file}",
                                )
                            )
                    else:
                        broken.append(
                            (
                                text,
                                target,
                                f"Unsupported anchor format #{target_anchor} on non-markdown file {target_file}",
                            )
                        )
                else:
                    with open(target_file, "r", encoding="utf-8") as tf:
                        t_content = tf.read()
                    t_headings = re.findall(r"^(#+)\s+(.+)$", t_content, re.MULTILINE)
                    t_anchors = {github_slug(t) for _, t in t_headings}
                    if target_anchor not in t_anchors:
                        broken.append(
                            (text, target, f"Anchor #{target_anchor} not found in {target_file}")
                        )

    if broken:
        print(f"❌ FOUND {len(broken)} BROKEN LINKS IN {filename}:")
        for b in broken:
            print(f"  • '{b[0]}' -> '{b[1]}': {b[2]}")
        return False
    else:
        print(f"✅ ALL {len(links)} links in {filename} resolved successfully!")
        return True


def main() -> None:
    ok1 = check_file("README.md")
    ok2 = check_file("ARCHITECTURE.md")
    if not (ok1 and ok2):
        sys.exit(1)


if __name__ == "__main__":
    main()
