# Quickstart: Claude Child Work

## Focused Unit Tests

Run the red-first unit coverage for child-work schema behavior:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py
```

Expected red-first failures before implementation:
- `ClaudeChildContext` is not exported.
- `ClaudeSessionGroup`, `ClaudeTeamMemberSession`, and `ClaudeTeamMessage` are not exported.
- Child-work event names and fixture-flow helper are not exported.

Expected result after implementation:
- Subagent child contexts reject peer-session collapse and promotion metadata.
- Team messages reject self-messages and cross-group members.
- Usage summaries validate rollups without merging subagent and team accounting.
- Child-work events require the correct child or group identifiers.

## Focused Integration-Style Boundary Tests

Run the boundary coverage for a representative child-work fixture flow:

```bash
pytest tests/integration/schemas/test_claude_child_work_boundary.py -q
```

Expected result:
- The fixture flow creates a parent session, completed subagent child context, completed team group, lead member, teammate member, peer message, and normalized lifecycle events.
- Subagent identity remains parent-owned and distinct from team member session identity.
- Team peer messaging carries sender, peer, and group identifiers.
- Usage remains separately inspectable for child context, team members, and group rollup.

## Full Verification Commands

Before final verification, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration CI when Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed-agent container, record the exact `/var/run/docker.sock` or compose blocker and keep focused non-Docker integration-style tests as local evidence.
