import { describe, expect, it } from 'vitest';

import {
  COLLECTION_LIST_DISPLAY_MODES,
  decodeSkillDetail,
  encodeSkillDetailPath,
  resolveRecurringListDisplay,
  resolveSkillListDisplay,
  resolveWorkflowListDisplay,
  collectionListDisplayModeByValue,
  type CollectionListDisplayMode,
} from './collectionListDisplayMode';

const modeValues = (): CollectionListDisplayMode[] => COLLECTION_LIST_DISPLAY_MODES.map((mode) => mode.value);

describe('collection list display mode registry', () => {
  it('exposes exactly the canonical hidden, sidebar, and table modes', () => {
    expect(modeValues()).toEqual(['hidden', 'sidebar', 'table']);
    expect(COLLECTION_LIST_DISPLAY_MODES).toHaveLength(3);
  });

  it('uses canonical labels, icon identities, and list regions without label matching', () => {
    expect(COLLECTION_LIST_DISPLAY_MODES).toEqual([
      { value: 'hidden', label: 'No list', icon: 'Square', listRegion: 'none' },
      { value: 'sidebar', label: 'Sidebar list', icon: 'PanelLeft', listRegion: 'sidebar' },
      { value: 'table', label: 'Full screen table', icon: 'Rows3', listRegion: 'primary-surface' },
    ]);
    expect(collectionListDisplayModeByValue('hidden')?.label).toBe('No list');
    expect(collectionListDisplayModeByValue('No list')).toBeNull();
  });
});

describe('resolveWorkflowListDisplay', () => {
  it('returns the full declarative shape for the workflows table route', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows', requestedMode: 'table' })).toEqual({
      requestedMode: 'table',
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      selection: { workflowId: null, source: 'none' },
      targetPath: '/workflows',
      status: null,
    });
  });

  it('opens the selected workflow from /workflows for hidden and sidebar modes', () => {
    for (const requestedMode of ['hidden', 'sidebar'] as const) {
      expect(resolveWorkflowListDisplay({
        pathname: '/workflows',
        requestedMode,
        selectedWorkflowId: 'mm:selected',
        search: '?source=temporal',
      })).toMatchObject({
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'workflows-table',
        routeAction: 'navigate-selected-detail',
        primarySurface: 'workflow-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection: { workflowId: 'mm:selected', source: 'last-selected' },
        targetPath: '/workflows/mm%3Aselected?source=temporal',
      });
    }
  });

  it('uses the first visible workflow from /workflows when no selected workflow exists', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows',
      requestedMode: 'sidebar',
      firstVisibleWorkflowId: 'mm:first-visible',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'resolve-first-row',
      primarySurface: 'workflow-detail',
      listSurface: 'sidebar',
      selection: { workflowId: 'mm:first-visible', source: 'first-visible-row' },
      targetPath: '/workflows/mm%3Afirst-visible',
    });
  });

  it('keeps /workflows effective as table when no workflow can be opened', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows', requestedMode: 'hidden' })).toEqual({
      requestedMode: 'hidden',
      effectiveMode: 'table',
      surface: 'workflows-table',
      routeAction: 'none',
      primarySurface: 'empty-workflows',
      listSurface: 'table',
      selection: { workflowId: null, source: 'none' },
      targetPath: '/workflows',
      status: 'No workflow can be opened from the current list.',
    });
  });

  it.each(['chat', 'overview', 'execution', 'evidence', 'steps', 'artifacts', 'runs', 'debug'])(
    'preserves the %s detail subroute when switching only between hidden and sidebar',
    (subroute) => {
      const pathname = subroute === 'chat' ? '/workflows/mm%3A123/chat' : `/workflows/mm%3A123/${subroute}`;
      expect(resolveWorkflowListDisplay({
        pathname,
        search: '?source=temporal',
        requestedMode: 'hidden',
      })).toMatchObject({
        effectiveMode: 'hidden',
        surface: 'workflow-detail',
        routeAction: 'none',
        primarySurface: 'workflow-detail',
        listSurface: 'none',
        selection: { workflowId: 'mm:123', source: 'route' },
        targetPath: `${pathname}?source=temporal`,
      });

      expect(resolveWorkflowListDisplay({
        pathname,
        requestedMode: 'sidebar',
      })).toMatchObject({
        effectiveMode: 'sidebar',
        routeAction: 'none',
        targetPath: pathname,
      });
    },
  );

  it('preserves the default detail route when switching only between hidden and sidebar', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123',
      search: '?source=temporal',
      requestedMode: 'hidden',
    })).toMatchObject({
      effectiveMode: 'hidden',
      surface: 'workflow-detail',
      routeAction: 'none',
      primarySurface: 'workflow-detail',
      listSurface: 'none',
      selection: { workflowId: 'mm:123', source: 'route' },
      targetPath: '/workflows/mm%3A123?source=temporal',
    });

    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123',
      requestedMode: 'sidebar',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'none',
      targetPath: '/workflows/mm%3A123',
    });
  });

  it('navigates detail routes and create to the workflows table for table mode', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123/debug',
      search: '?source=temporal&limit=10&token=secret&unsafe=1',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      surface: 'workflow-detail',
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      targetPath: '/workflows?source=temporal&limit=10',
    });

    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/new',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      surface: 'workflow-start',
      routeAction: 'navigate-workflows',
      primarySurface: 'workflow-table',
      listSurface: 'table',
      targetPath: '/workflows',
    });
  });

  it('converts API-style pageSize context to table limit for table mode navigation', () => {
    expect(resolveWorkflowListDisplay({
      pathname: '/workflows/mm%3A123/debug',
      search: '?source=temporal&pageSize=100&stateIn=completed',
      requestedMode: 'table',
    })).toMatchObject({
      routeAction: 'navigate-workflows',
      targetPath: '/workflows?source=temporal&stateIn=completed&limit=100',
    });
  });

  it('keeps create as primary and renders the workflow list as a sidebar in sidebar mode', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/workflows/new', requestedMode: 'sidebar' })).toMatchObject({
      requestedMode: 'sidebar',
      effectiveMode: 'sidebar',
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: 'sidebar',
      targetPath: '/workflows/new',
      status: null,
    });
    expect(resolveWorkflowListDisplay({ pathname: '/workflows/new', requestedMode: 'hidden' })).toMatchObject({
      requestedMode: 'hidden',
      effectiveMode: 'hidden',
      surface: 'workflow-start',
      routeAction: 'none',
      primarySurface: 'workflow-start',
      listSurface: 'none',
      targetPath: '/workflows/new',
      status: null,
    });
  });

  it('returns null for unsupported dashboard routes', () => {
    expect(resolveWorkflowListDisplay({ pathname: '/settings', requestedMode: 'table' })).toBeNull();
  });
});

describe('resolveRecurringListDisplay', () => {
  it('keeps /schedules as the recurring table surface in table mode', () => {
    expect(resolveRecurringListDisplay({
      pathname: '/schedules',
      requestedMode: 'table',
      search: '?scope=personal',
    })).toEqual({
      requestedMode: 'table',
      effectiveMode: 'table',
      surface: 'recurring-table',
      routeAction: 'none',
      primarySurface: 'recurring-table',
      listSurface: 'table',
      selection: { definitionId: null, source: 'none' },
      targetPath: '/schedules?scope=personal',
      status: null,
    });
  });

  it('opens the selected recurring schedule from /schedules for sidebar and hidden modes', () => {
    for (const requestedMode of ['sidebar', 'hidden'] as const) {
      expect(resolveRecurringListDisplay({
        pathname: '/schedules',
        requestedMode,
        selectedDefinitionId: 'daily:scan',
      })).toMatchObject({
        requestedMode,
        effectiveMode: requestedMode,
        surface: 'recurring-table',
        routeAction: 'navigate-selected-detail',
        primarySurface: 'recurring-detail',
        listSurface: requestedMode === 'hidden' ? 'none' : 'sidebar',
        selection: { definitionId: 'daily:scan', source: 'last-selected' },
        targetPath: '/schedules/daily%3Ascan',
      });
    }
  });

  it('uses the first visible recurring schedule when no selected schedule exists', () => {
    expect(resolveRecurringListDisplay({
      pathname: '/schedules',
      requestedMode: 'sidebar',
      firstVisibleDefinitionId: 'first:recurring',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'resolve-first-row',
      primarySurface: 'recurring-detail',
      listSurface: 'sidebar',
      selection: { definitionId: 'first:recurring', source: 'first-visible-row' },
      targetPath: '/schedules/first%3Arecurring',
    });
  });

  it('keeps an empty /schedules route in table mode when no recurring schedule can be opened', () => {
    expect(resolveRecurringListDisplay({ pathname: '/schedules', requestedMode: 'hidden' })).toEqual({
      requestedMode: 'hidden',
      effectiveMode: 'table',
      surface: 'recurring-table',
      routeAction: 'none',
      primarySurface: 'empty-recurring',
      listSurface: 'table',
      selection: { definitionId: null, source: 'none' },
      targetPath: '/schedules',
      status: 'No recurring schedule can be opened from the current list.',
    });
  });

  it('keeps detail routes on hidden and sidebar modes and returns to /schedules for table mode', () => {
    expect(resolveRecurringListDisplay({
      pathname: '/schedules/daily%3Ascan',
      search: '?scope=personal',
      requestedMode: 'hidden',
    })).toMatchObject({
      effectiveMode: 'hidden',
      surface: 'recurring-detail',
      routeAction: 'none',
      primarySurface: 'recurring-detail',
      listSurface: 'none',
      selection: { definitionId: 'daily:scan', source: 'route' },
      targetPath: '/schedules/daily%3Ascan?scope=personal',
    });

    expect(resolveRecurringListDisplay({
      pathname: '/schedules/daily%3Ascan',
      requestedMode: 'sidebar',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'none',
      listSurface: 'sidebar',
      targetPath: '/schedules/daily%3Ascan',
    });

    expect(resolveRecurringListDisplay({
      pathname: '/schedules/daily%3Ascan',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      routeAction: 'navigate-recurring',
      primarySurface: 'recurring-table',
      listSurface: 'table',
      targetPath: '/schedules',
    });
  });
});

describe('decodeSkillDetail', () => {
  it('decodes safe percent-encoded skill IDs', () => {
    expect(decodeSkillDetail('/skills/speckit-orchestrate')).toEqual({ skillId: 'speckit-orchestrate' });
    expect(decodeSkillDetail('/skills/my%20skill')).toEqual({ skillId: 'my skill' });
    expect(decodeSkillDetail('/skills/skill.v2/')).toEqual({ skillId: 'skill.v2' });
  });

  it('rejects malformed percent encoding', () => {
    expect(decodeSkillDetail('/skills/%zz')).toBeNull();
    expect(decodeSkillDetail('/skills/%')).toBeNull();
  });

  it('rejects encoded slashes and empty IDs', () => {
    expect(decodeSkillDetail('/skills/foo%2Fbar')).toBeNull();
    expect(decodeSkillDetail('/skills/%2F')).toBeNull();
    expect(decodeSkillDetail('/skills/')).toBeNull();
  });

  it('rejects unsupported extra path segments and non-skills routes', () => {
    expect(decodeSkillDetail('/skills/foo/bar')).toBeNull();
    expect(decodeSkillDetail('/skills')).toBeNull();
    expect(decodeSkillDetail('/workflows/foo')).toBeNull();
  });
});

describe('encodeSkillDetailPath', () => {
  it('percent-encodes the skill ID and preserves the search context', () => {
    expect(encodeSkillDetailPath('my skill')).toBe('/skills/my%20skill');
    expect(encodeSkillDetailPath('speckit', '?q=spec')).toBe('/skills/speckit?q=spec');
  });
});

describe('resolveSkillListDisplay', () => {
  it('keeps /skills as the skills table surface in table mode', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills',
      requestedMode: 'table',
      search: '?q=spec',
    })).toEqual({
      requestedMode: 'table',
      effectiveMode: 'table',
      surface: 'skills-table',
      routeAction: 'none',
      primarySurface: 'skill-table',
      listSurface: 'table',
      selection: { skillId: null, source: 'none' },
      targetPath: '/skills?q=spec',
      status: null,
    });
  });

  it('opens the remembered skill from /skills for sidebar mode', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills',
      requestedMode: 'sidebar',
      selectedSkillId: 'pr-resolver',
    })).toMatchObject({
      requestedMode: 'sidebar',
      effectiveMode: 'sidebar',
      surface: 'skills-table',
      routeAction: 'navigate-selected-detail',
      primarySurface: 'skill-detail',
      listSurface: 'sidebar',
      selection: { skillId: 'pr-resolver', source: 'last-selected' },
      targetPath: '/skills/pr-resolver',
    });
  });

  it('resolves the first visible skill from /skills for hidden mode', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills',
      requestedMode: 'hidden',
      firstVisibleSkillId: 'speckit-orchestrate',
    })).toMatchObject({
      effectiveMode: 'hidden',
      routeAction: 'resolve-first-row',
      primarySurface: 'skill-detail',
      listSurface: 'none',
      selection: { skillId: 'speckit-orchestrate', source: 'first-visible-row' },
      targetPath: '/skills/speckit-orchestrate',
    });
  });

  it('keeps an empty /skills route in table mode when no skill can be opened', () => {
    expect(resolveSkillListDisplay({ pathname: '/skills', requestedMode: 'sidebar' })).toEqual({
      requestedMode: 'sidebar',
      effectiveMode: 'table',
      surface: 'skills-table',
      routeAction: 'none',
      primarySurface: 'empty-skills',
      listSurface: 'table',
      selection: { skillId: null, source: 'none' },
      targetPath: '/skills',
      status: 'No skill can be opened from the current list.',
    });
  });

  it('switches detail routes between hidden and sidebar without changing the route', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills/pr-resolver',
      search: '?q=pr',
      requestedMode: 'hidden',
    })).toMatchObject({
      effectiveMode: 'hidden',
      surface: 'skill-detail',
      routeAction: 'none',
      primarySurface: 'skill-detail',
      listSurface: 'none',
      selection: { skillId: 'pr-resolver', source: 'route' },
      targetPath: '/skills/pr-resolver?q=pr',
    });

    expect(resolveSkillListDisplay({
      pathname: '/skills/pr-resolver',
      requestedMode: 'sidebar',
    })).toMatchObject({
      effectiveMode: 'sidebar',
      routeAction: 'none',
      listSurface: 'sidebar',
      targetPath: '/skills/pr-resolver',
    });
  });

  it('navigates a detail route to /skills for table mode and preserves the filter context', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills/pr-resolver',
      search: '?q=pr',
      requestedMode: 'table',
    })).toMatchObject({
      effectiveMode: 'table',
      surface: 'skill-detail',
      routeAction: 'navigate-skills',
      primarySurface: 'skill-table',
      listSurface: 'table',
      selection: { skillId: 'pr-resolver', source: 'route' },
      targetPath: '/skills?q=pr',
    });
  });

  it('accepts safe percent-encoded IDs and rejects malformed detail routes', () => {
    expect(resolveSkillListDisplay({
      pathname: '/skills/my%20skill',
      requestedMode: 'sidebar',
    })).toMatchObject({
      selection: { skillId: 'my skill', source: 'route' },
    });

    expect(resolveSkillListDisplay({ pathname: '/skills/%zz', requestedMode: 'sidebar' })).toBeNull();
    expect(resolveSkillListDisplay({ pathname: '/skills/foo%2Fbar', requestedMode: 'sidebar' })).toBeNull();
    expect(resolveSkillListDisplay({ pathname: '/skills/foo/bar', requestedMode: 'sidebar' })).toBeNull();
  });

  it('returns null for unsupported non-Skills routes', () => {
    expect(resolveSkillListDisplay({ pathname: '/workflows', requestedMode: 'table' })).toBeNull();
    expect(resolveSkillListDisplay({ pathname: '/settings', requestedMode: 'sidebar' })).toBeNull();
  });
});
