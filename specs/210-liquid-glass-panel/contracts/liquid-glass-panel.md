# UI Contract: Liquid Glass Publish Panel

## Surface

The Create Page bottom task submission controls group is the contracted surface.

It contains:

- GitHub Repo control
- Branch control
- Publish Mode control
- Create task action
- Branch status message when needed

## Behavior Contract

- The panel presents as one liquid glass surface behind the controls.
- The surface treatment does not add, remove, rename, or reorder the contracted controls.
- Repository, branch, and publish mode controls keep their existing accessible names.
- The create action keeps its existing accessible name and submit behavior.
- Branch loading, branch stale, validation, disabled, and submitting states remain visible and readable.
- The visual treatment does not change submitted task payload meaning.

## Responsive Contract

- At desktop width, the controls remain in one compact bottom control group.
- At mobile width, the controls may wrap, but all controls remain inside the same bottom control group.
- Text, icons, and inputs must not overlap or clip in the checked desktop and mobile widths.

## Theme Contract

- The treatment remains readable in light and dark appearance settings.
- Fallback behavior for environments without backdrop blur support must preserve readable panel contrast.
