#!/usr/bin/env python3
"""Renumber all 274 tasks into prefix-based IDs:
  T001-T164  (general)
  M001-M098  (multimodal)
  C01-C12    (conversation, unchanged)

Usage:
  python3 scripts/renumber_tasks.py --dry-run      # preview mapping
  python3 scripts/renumber_tasks.py --execute       # do the rename
  python3 scripts/renumber_tasks.py --verify        # post-rename checks
  python3 scripts/renumber_tasks.py --rollback      # undo using saved mapping
"""

import argparse
import ast
import json
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"


# ════════════════════════════════════════════════════════════════════
# Phase 1: Build mapping
# ════════════════════════════════════════════════════════════════════

def _extract_numeric_key(dirname: str) -> int:
    """Extract the numeric portion for sort ordering.
    T01zh_email_triage -> 1, T105_clock -> 105, C01zh_foo -> 1
    """
    m = re.match(r"[A-Z](\d+)", dirname)
    if not m:
        raise ValueError(f"Cannot extract numeric key from {dirname}")
    return int(m.group(1))


def _suffix(dirname: str) -> str:
    """Everything after the prefix+digits: 'T01zh_email_triage' -> 'zh_email_triage'."""
    m = re.match(r"[A-Z]\d+(.*)$", dirname)
    return m.group(1) if m else ""


def build_mapping() -> dict[str, str]:
    """Return {old_dir_name: new_dir_name} for all 274 tasks."""
    general = []
    multimodal = []
    conversation = []

    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        task_yaml = d / "task.yaml"
        if not task_yaml.exists():
            print(f"  [WARN] skipping {name}: no task.yaml")
            continue

        with open(task_yaml) as f:
            data = yaml.safe_load(f)
        tags = data.get("tags") or []

        if name.startswith("C"):
            conversation.append(name)
        elif "multimodal" in tags:
            multimodal.append(name)
        else:
            general.append(name)

    # Sort each group by current numeric ID
    general.sort(key=_extract_numeric_key)
    multimodal.sort(key=_extract_numeric_key)
    conversation.sort(key=_extract_numeric_key)

    mapping = {}

    # General → T001-T164 (3-digit zero-padded)
    for i, old in enumerate(general, start=1):
        suf = _suffix(old)
        new = f"T{i:03d}{suf}"
        mapping[old] = new

    # Multimodal → M001-M098 (3-digit zero-padded)
    for i, old in enumerate(multimodal, start=1):
        suf = _suffix(old)
        new = f"M{i:03d}{suf}"
        mapping[old] = new

    # Conversation → identity
    for old in conversation:
        mapping[old] = old

    return mapping


# ════════════════════════════════════════════════════════════════════
# Phase 2: Validate mapping
# ════════════════════════════════════════════════════════════════════

def validate_mapping(mapping: dict[str, str]) -> bool:
    ok = True

    # Count by prefix
    t_count = sum(1 for v in mapping.values() if v.startswith("T"))
    m_count = sum(1 for v in mapping.values() if v.startswith("M"))
    c_count = sum(1 for v in mapping.values() if v.startswith("C"))
    total = len(mapping)

    print(f"  Total: {total}  (T={t_count}, M={m_count}, C={c_count})")

    if total != 274:
        print(f"  [FAIL] Expected 274 tasks, got {total}")
        ok = False
    if t_count != 164:
        print(f"  [FAIL] Expected 164 T-tasks, got {t_count}")
        ok = False
    if m_count != 98:
        print(f"  [FAIL] Expected 98 M-tasks, got {m_count}")
        ok = False
    if c_count != 12:
        print(f"  [FAIL] Expected 12 C-tasks, got {c_count}")
        ok = False

    # Check uniqueness of new names
    new_names = list(mapping.values())
    if len(new_names) != len(set(new_names)):
        dupes = [n for n in new_names if new_names.count(n) > 1]
        print(f"  [FAIL] Duplicate new names: {set(dupes)}")
        ok = False

    # Check zh/en paired tasks stay consecutive
    # Build a set of known zh/en pairs by checking load_peer_grader references
    # (EN grader loads zh grader → they must be consecutive)
    peer_pattern = re.compile(r'load_peer_grader\("([^"]+)"\)')
    pair_issues = []
    for old_name, new_name in mapping.items():
        grader = TASKS_DIR / old_name / "grader.py"
        if not grader.exists():
            continue
        text = grader.read_text(encoding="utf-8")
        m = peer_pattern.search(text)
        if not m:
            continue
        zh_old = m.group(1)
        zh_new = mapping.get(zh_old)
        if zh_new is None:
            pair_issues.append(f"  {old_name}: peer {zh_old} not in mapping")
            continue
        # Check they're consecutive: zh_new number should be new_name number - 1
        zh_num = _extract_numeric_key(zh_new)
        en_num = _extract_numeric_key(new_name)
        if en_num != zh_num + 1:
            pair_issues.append(
                f"  Pair broken: {zh_old}->{zh_new} (#{zh_num}) / "
                f"{old_name}->{new_name} (#{en_num}), gap={en_num - zh_num}"
            )

    if pair_issues:
        for issue in pair_issues:
            print(issue)
        ok = False
    else:
        print("  [OK] All zh/en pairs remain consecutive")

    if ok:
        print("  [OK] Mapping validation passed")
    return ok


# ════════════════════════════════════════════════════════════════════
# Phase 3: Rename directories (using temp names to avoid collisions)
# ════════════════════════════════════════════════════════════════════

def rename_directories(mapping: dict[str, str], dry_run: bool = False):
    """Rename task directories using intermediate temp names."""
    # Only process tasks that actually change
    renames = {k: v for k, v in mapping.items() if k != v}
    print(f"  Renaming {len(renames)} directories ({len(mapping) - len(renames)} unchanged)")

    if dry_run:
        return

    # Step 1: Rename all to temp names
    for old in renames:
        src = TASKS_DIR / old
        tmp = TASKS_DIR / f"__RENAME__{renames[old]}"
        if not src.exists():
            print(f"  [ERROR] Source directory not found: {src}")
            sys.exit(1)
        src.rename(tmp)

    # Step 2: Rename temp names to final names
    for old, new in renames.items():
        tmp = TASKS_DIR / f"__RENAME__{new}"
        dst = TASKS_DIR / new
        if dst.exists():
            print(f"  [ERROR] Target already exists: {dst}")
            sys.exit(1)
        tmp.rename(dst)

    print(f"  [OK] {len(renames)} directories renamed")


# ════════════════════════════════════════════════════════════════════
# Phase 4: Update file contents
# ════════════════════════════════════════════════════════════════════

def _build_substitution_table(mapping: dict[str, str]) -> list[tuple[str, str]]:
    """Build sorted substitution pairs (old, new), longest-first to prevent
    partial matches (e.g. T10_ matching inside T100_)."""
    pairs = [(old, new) for old, new in mapping.items() if old != new]
    # Sort by decreasing old name length
    pairs.sort(key=lambda x: -len(x[0]))
    return pairs


def _apply_substitutions(text: str, subs: list[tuple[str, str]]) -> str:
    """Apply all substitutions to text. Uses longest-first order to prevent
    partial matches."""
    for old, new in subs:
        text = text.replace(old, new)
    return text


def update_file_contents(mapping: dict[str, str], dry_run: bool = False):
    """Update all files that reference task IDs."""
    subs = _build_substitution_table(mapping)
    if not subs:
        print("  No substitutions needed")
        return

    updated_files = []

    # 1. All task.yaml files
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        yaml_path = task_dir / "task.yaml"
        if yaml_path.exists():
            _update_file(yaml_path, subs, dry_run, updated_files)

    # 2. All grader.py files
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        grader_path = task_dir / "grader.py"
        if grader_path.exists():
            _update_file(grader_path, subs, dry_run, updated_files)

    # 3. Mock service server.py files
    mock_dir = ROOT / "mock_services"
    if mock_dir.exists():
        for svc_dir in sorted(mock_dir.iterdir()):
            if not svc_dir.is_dir():
                continue
            server_py = svc_dir / "server.py"
            if server_py.exists():
                _update_file(server_py, subs, dry_run, updated_files)

    # 4. Shell scripts
    for sh_name in [
        "run_webpage_tasks.sh",
        "run_video_tasks.sh",
        "run_multimodal_eval_lirang.sh",
    ]:
        sh_path = ROOT / sh_name
        if sh_path.exists():
            _update_file(sh_path, subs, dry_run, updated_files)

    # 5. docs/examples/task_template_reference.yaml
    doc_path = ROOT / "docs" / "examples" / "task_template_reference.yaml"
    if doc_path.exists():
        _update_file(doc_path, subs, dry_run, updated_files)

    # 6. mock_services/web_real_injection/server.py comment (T47 -> T047)
    # Already covered by mock_services traversal above

    print(f"  [OK] Updated {len(updated_files)} files")
    if dry_run and updated_files:
        for f in updated_files[:20]:
            print(f"    would update: {f}")
        if len(updated_files) > 20:
            print(f"    ... and {len(updated_files) - 20} more")


def _update_file(
    path: Path,
    subs: list[tuple[str, str]],
    dry_run: bool,
    updated_files: list[str],
):
    """Read a file, apply substitutions, write back if changed."""
    text = path.read_text(encoding="utf-8")
    new_text = _apply_substitutions(text, subs)
    if new_text != text:
        updated_files.append(str(path.relative_to(ROOT)))
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# Phase 5: Shell script glob patterns
# ════════════════════════════════════════════════════════════════════

def update_shell_patterns(dry_run: bool = False):
    """Update case-statement glob patterns in shell scripts that filter by
    task ID prefix ranges."""
    changes = {
        # run_webpage_tasks.sh: T1[0-3]* matches old T105-T139
        # New IDs are M001-M035, so pattern should match M*
        "run_webpage_tasks.sh": [
            ("T1[0-3]*", "M*"),
        ],
        # run_video_tasks.sh: T1[23]* matches old T126-T139
        # New IDs are M022-M035, so pattern should match M*
        "run_video_tasks.sh": [
            ("T1[23]*", "M*"),
        ],
    }

    for filename, replacements in changes.items():
        path = ROOT / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new_text = text
        for old_pat, new_pat in replacements:
            new_text = new_text.replace(old_pat, new_pat)
        if new_text != text:
            if not dry_run:
                path.write_text(new_text, encoding="utf-8")
            print(f"  Updated glob patterns in {filename}")


# ════════════════════════════════════════════════════════════════════
# Rollback support
# ════════════════════════════════════════════════════════════════════

ROLLBACK_FILE = ROOT / "scripts" / "renumber_rollback.json"


def save_rollback(mapping: dict[str, str]):
    """Save inverse mapping for rollback."""
    inverse = {v: k for k, v in mapping.items()}
    with open(ROLLBACK_FILE, "w") as f:
        json.dump(inverse, f, indent=2, ensure_ascii=False)
    print(f"  Saved rollback mapping to {ROLLBACK_FILE.relative_to(ROOT)}")


def do_rollback():
    """Rollback using saved inverse mapping."""
    if not ROLLBACK_FILE.exists():
        print("[ERROR] No rollback file found")
        sys.exit(1)

    with open(ROLLBACK_FILE) as f:
        inverse = json.load(f)

    print(f"Rolling back {len(inverse)} tasks...")

    # The inverse mapping IS a valid mapping (current -> original)
    renames = {k: v for k, v in inverse.items() if k != v}

    # Step 1: temp names
    for old in renames:
        src = TASKS_DIR / old
        tmp = TASKS_DIR / f"__RENAME__{renames[old]}"
        if src.exists():
            src.rename(tmp)

    # Step 2: final names
    for old, new in renames.items():
        tmp = TASKS_DIR / f"__RENAME__{new}"
        dst = TASKS_DIR / new
        tmp.rename(dst)

    # Update file contents with inverse subs
    subs = _build_substitution_table(inverse)
    updated = []
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        for fname in ["task.yaml", "grader.py"]:
            fpath = task_dir / fname
            if fpath.exists():
                _update_file(fpath, subs, False, updated)

    mock_dir = ROOT / "mock_services"
    if mock_dir.exists():
        for svc_dir in sorted(mock_dir.iterdir()):
            if not svc_dir.is_dir():
                continue
            server_py = svc_dir / "server.py"
            if server_py.exists():
                _update_file(server_py, subs, False, updated)

    for sh_name in ["run_webpage_tasks.sh", "run_video_tasks.sh",
                     "run_multimodal_eval_lirang.sh"]:
        sh_path = ROOT / sh_name
        if sh_path.exists():
            _update_file(sh_path, subs, False, updated)

    doc_path = ROOT / "docs" / "examples" / "task_template_reference.yaml"
    if doc_path.exists():
        _update_file(doc_path, subs, False, updated)

    print(f"  Rolled back {len(renames)} directories, updated {len(updated)} files")
    ROLLBACK_FILE.unlink()


# ════════════════════════════════════════════════════════════════════
# Verification
# ════════════════════════════════════════════════════════════════════

def verify():
    """Run 8 automated checks after renumbering."""
    all_ok = True

    # 1. Directory count
    dirs = [d.name for d in TASKS_DIR.iterdir() if d.is_dir()]
    n = len(dirs)
    if n == 274:
        print(f"  [OK] 1. Directory count: {n}")
    else:
        print(f"  [FAIL] 1. Directory count: {n} (expected 274)")
        all_ok = False

    # 2. Prefix distribution
    t_count = sum(1 for d in dirs if d.startswith("T"))
    m_count = sum(1 for d in dirs if d.startswith("M"))
    c_count = sum(1 for d in dirs if d.startswith("C"))
    if t_count == 164 and m_count == 98 and c_count == 12:
        print(f"  [OK] 2. Prefix distribution: T={t_count}, M={m_count}, C={c_count}")
    else:
        print(f"  [FAIL] 2. Prefix distribution: T={t_count}, M={m_count}, C={c_count}")
        all_ok = False

    # 3. task_id matches directory name
    mismatches = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        yaml_path = d / "task.yaml"
        if not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        tid = data.get("task_id", "")
        if tid != d.name:
            mismatches.append(f"    {d.name}: task_id={tid}")
    if not mismatches:
        print("  [OK] 3. All task_id fields match directory names")
    else:
        print(f"  [FAIL] 3. {len(mismatches)} task_id mismatches:")
        for m in mismatches[:10]:
            print(m)
        all_ok = False

    # 4. No stale old T-prefix IDs in path references
    # Look for patterns like "tasks/T01zh_" or "tasks/T99_" (real path refs)
    # Ignore docstrings/comments which may mention old IDs informally
    stale_path_pattern = re.compile(r'tasks/T(\d{1,2})(zh|_)')
    # Also check load_peer_grader("T01zh_...") calls
    stale_peer_pattern = re.compile(r'load_peer_grader\("T(\d{1,2})(zh|_)')
    # Also check task_id: T01zh_ or task_id: T99_ in YAML
    stale_tid_pattern = re.compile(r'task_id:\s*T(\d{1,2})(zh|_)')
    stale_hits = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        for fname in ["task.yaml", "grader.py"]:
            fpath = d / fname
            if not fpath.exists():
                continue
            text = fpath.read_text(encoding="utf-8")
            for pat in [stale_path_pattern, stale_peer_pattern, stale_tid_pattern]:
                for m in pat.finditer(text):
                    num = int(m.group(1))
                    if num < 100:
                        stale_hits.append(f"    {d.name}/{fname}: {m.group(0)}")
    if not stale_hits:
        print("  [OK] 4. No stale 1-2 digit T-prefix IDs in paths/references")
    else:
        print(f"  [FAIL] 4. {len(stale_hits)} stale old IDs found:")
        for h in stale_hits[:20]:
            print(h)
        all_ok = False

    # 5. load_peer_grader targets exist
    peer_pattern = re.compile(r'load_peer_grader\("([^"]+)"\)')
    bad_peers = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        grader_path = d / "grader.py"
        if not grader_path.exists():
            continue
        text = grader_path.read_text(encoding="utf-8")
        for m in peer_pattern.finditer(text):
            target = m.group(1)
            target_dir = TASKS_DIR / target
            if not target_dir.exists() or not (target_dir / "grader.py").exists():
                bad_peers.append(f"    {d.name}/grader.py -> {target}")
    if not bad_peers:
        print("  [OK] 5. All load_peer_grader targets exist")
    else:
        print(f"  [FAIL] 5. {len(bad_peers)} broken load_peer_grader references:")
        for b in bad_peers:
            print(b)
        all_ok = False

    # 6. Mock service fixture paths exist
    mock_dir = ROOT / "mock_services"
    task_path_pattern = re.compile(r'tasks/([A-Z][A-Za-z0-9_]+?)/')
    bad_mock_refs = []
    if mock_dir.exists():
        for svc_dir in sorted(mock_dir.iterdir()):
            if not svc_dir.is_dir():
                continue
            server_py = svc_dir / "server.py"
            if not server_py.exists():
                continue
            text = server_py.read_text(encoding="utf-8")
            for m in task_path_pattern.finditer(text):
                ref_task = m.group(1)
                if not (TASKS_DIR / ref_task).exists():
                    bad_mock_refs.append(f"    {svc_dir.name}/server.py -> {ref_task}")
    if not bad_mock_refs:
        print("  [OK] 6. All mock service fixture paths valid")
    else:
        print(f"  [FAIL] 6. {len(bad_mock_refs)} broken mock service refs:")
        for b in bad_mock_refs:
            print(b)
        all_ok = False

    # 7. YAML parse check
    yaml_errors = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        yaml_path = d / "task.yaml"
        if not yaml_path.exists():
            continue
        try:
            with open(yaml_path) as f:
                yaml.safe_load(f)
        except Exception as e:
            yaml_errors.append(f"    {d.name}: {e}")
    if not yaml_errors:
        print("  [OK] 7. All 274 task.yaml files parse successfully")
    else:
        print(f"  [FAIL] 7. {len(yaml_errors)} YAML parse errors:")
        for e in yaml_errors[:10]:
            print(e)
        all_ok = False

    # 8. Python syntax check for grader.py files
    py_errors = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        grader_path = d / "grader.py"
        if not grader_path.exists():
            continue
        try:
            text = grader_path.read_text(encoding="utf-8")
            ast.parse(text)
        except SyntaxError as e:
            py_errors.append(f"    {d.name}: {e}")
    if not py_errors:
        print("  [OK] 8. All grader.py files have valid Python syntax")
    else:
        print(f"  [FAIL] 8. {len(py_errors)} Python syntax errors:")
        for e in py_errors[:10]:
            print(e)
        all_ok = False

    if all_ok:
        print("\n  === ALL CHECKS PASSED ===")
    else:
        print("\n  === SOME CHECKS FAILED ===")
    return all_ok


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Renumber claw-eval tasks")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview mapping only")
    group.add_argument("--execute", action="store_true", help="Execute the renumbering")
    group.add_argument("--verify", action="store_true", help="Run post-rename checks")
    group.add_argument("--rollback", action="store_true", help="Undo renumbering")
    args = parser.parse_args()

    os.chdir(ROOT)

    if args.rollback:
        do_rollback()
        return

    if args.verify:
        print("Running verification checks...")
        ok = verify()
        sys.exit(0 if ok else 1)

    # Build and validate mapping
    print("Building mapping...")
    mapping = build_mapping()

    print("Validating mapping...")
    if not validate_mapping(mapping):
        print("[ABORT] Mapping validation failed")
        sys.exit(1)

    if args.dry_run:
        print("\n=== DRY RUN — Mapping Preview ===\n")
        # Show renames grouped by prefix
        renames = {k: v for k, v in mapping.items() if k != v}
        identity = {k: v for k, v in mapping.items() if k == v}

        for prefix_label, prefix in [("General (T)", "T"), ("Multimodal (M)", "M")]:
            group_items = sorted(
                [(k, v) for k, v in renames.items() if v.startswith(prefix)],
                key=lambda x: x[1],
            )
            print(f"\n{prefix_label}: {len(group_items)} renames")
            for old, new in group_items:
                print(f"  {old:45s} -> {new}")

        print(f"\nConversation (C): {len(identity)} unchanged")
        for old in sorted(identity):
            print(f"  {old}")

        # Preview file content updates
        print("\n=== File Content Updates (preview) ===")
        update_file_contents(mapping, dry_run=True)
        update_shell_patterns(dry_run=True)
        return

    if args.execute:
        print("\n=== EXECUTING RENUMBERING ===\n")

        # Save rollback
        print("Saving rollback mapping...")
        save_rollback(mapping)

        # Rename directories
        print("Renaming directories...")
        rename_directories(mapping, dry_run=False)

        # Update file contents
        print("Updating file contents...")
        update_file_contents(mapping, dry_run=False)

        # Update shell glob patterns
        print("Updating shell patterns...")
        update_shell_patterns(dry_run=False)

        print("\n=== DONE ===")
        print("Run --verify to check results.")


if __name__ == "__main__":
    main()
