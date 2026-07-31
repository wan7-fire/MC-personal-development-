# ZXCode Security Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add project-local vertical security checks for Bash, WriteFile, and EditFile with policy loading, dispatch prechecks, tool-layer fallbacks, and HITL persistence.

**Architecture:** Keep the first version small: one `zxcode/security.py` module owns rule evaluation, config loading/saving, and session/permanent approvals; dispatch asks it before tool execution, and file/shell tools keep local guards as a second line. The app owns the approval dialog and writes permanent approvals back into `zxcode-security.toml`.

**Tech Stack:** Python 3.11 stdlib (`tomllib`, `pathlib`, `json`), existing tool runtime, existing Textual UI, `unittest`.

---

### Task 1: Add failing security-policy tests

**Files:**
- Create: `tests/test_security.py`
- Create: `zxcode-security.toml`

- [ ] **Step 1: Write the failing test**

```python
def test_security_policy_blocks_blacklisted_shell():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_security -v`
Expected: `ModuleNotFoundError` or missing symbol failures.

- [ ] **Step 3: Write minimal implementation**

```python
def placeholder():
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_security -v`
Expected: PASS.

### Task 2: Implement `zxcode/security.py`

**Files:**
- Create: `zxcode/security.py`
- Modify: `zxcode/config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_decision_priority_prefers_session_then_project_then_mode():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_security -v`
Expected: missing behavior failures.

- [ ] **Step 3: Write minimal implementation**

```python
def load_policy(root): ...
def decide(...): ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_security -v`
Expected: PASS.

### Task 3: Wire dispatcher and tool fallbacks

**Files:**
- Modify: `zxcode/dispatch.py`
- Modify: `zxcode/tools/files.py`
- Modify: `zxcode/tools/shell.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_short_circuits_denied_calls():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dispatch -v`
Expected: security-aware assertions fail.

- [ ] **Step 3: Write minimal implementation**

```python
if security.deny(...): return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dispatch -v`
Expected: PASS.

### Task 4: Add HITL dialog and permanent approvals

**Files:**
- Modify: `zxcode/app.py`
- Modify: `zxcode/security.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
def test_permanent_allow_writes_security_config():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_app -v`
Expected: dialog/button/config failures.

- [ ] **Step 3: Write minimal implementation**

```python
def confirm_tool(...): ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_app -v`
Expected: PASS.

### Task 5: End-to-end verification

**Files:**
- Modify: `tests/test_security.py`
- Modify: `tests/test_dispatch.py`
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
def test_all_checks_work_together_end_to_end():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: at least one targeted failure before implementation is complete.

- [ ] **Step 3: Write minimal implementation**

```python
# tighten the shared security path until green
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS.
