import os
import logging
from pathlib import Path

logger = logging.getLogger("architect_directives")

# Only these files are considered Architect directives, in this exact order.
# Explicit allow-list + ordering keeps the consolidated prompt deterministic,
# which matters for prompt-cache hit rates (a reordered prefix = a cache miss)
# and keeps unrelated files (e.g. security-monitoring checklists, __pycache__,
# this module itself) out of the persona's soul block.
ARCHITECT_DIRECTIVE_FILES = [
    "ai-persona-voice-directive.md",
    "ai-software-architecture-planning-directive.md",
    "ai-software-development-directive.md",
    "ai-code-editing-directive.md",
    "ai-code-testing-workflow-directive.md",
    "ai-defect-fix-workflow-directive.md",
]


def read_architect_subfolder_contents_os(base_directory: str) -> dict:
    """Read the known Architect directive files from any 'architect'
    subfolder under base_directory, in a fixed order.

    Only files listed in ARCHITECT_DIRECTIVE_FILES are read, and missing
    files are skipped (and logged) rather than silently producing an
    "Error reading file" string inside the system prompt.
    """
    file_contents = {}
    for root, dirs, files in os.walk(base_directory):
        # Deterministic traversal order
        dirs.sort()
        if os.path.basename(root) != 'architect':
            continue

        available = set(files)
        for file_name in ARCHITECT_DIRECTIVE_FILES:
            if file_name not in available:
                logger.warning("Architect directive file not found: %s/%s", root, file_name)
                continue

            file_path = os.path.join(root, file_name)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_contents[file_path] = f.read()
            except (UnicodeDecodeError, PermissionError, OSError) as e:
                logger.error("Failed to read architect directive %s: %s", file_path, e)
                # Skip rather than embed an error string into the prompt
                continue

    return file_contents


def get_architect_directive_files() -> str:
    # 1. Get the directory where this current script lives
    current_dir = Path(__file__).resolve().parent

    # 2. Read the known directive files from the 'architect' subfolder, in order
    directives_dict = read_architect_subfolder_contents_os(str(current_dir))

    # 3. Consolidate the contents into a single string
    consolidated_content = []

    for file_path, content in directives_dict.items():
        # Extract just the filename (e.g., "ai-code-editing-directive.md") for a clean header
        file_name = os.path.basename(file_path)

        # Format each file nicely with clear separation boundaries
        file_section = f"=== FILE: {file_name} ===\n{content}\n\n"
        consolidated_content.append(file_section)

    # Join all file contents together into a single master string
    return "".join(consolidated_content)

# --- To run and test it ---
if __name__ == "__main__":
    all_directives = get_architect_directive_files()
    print(all_directives)