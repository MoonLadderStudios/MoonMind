# UI Interaction Contract: Frontend Input and Focus Contract

## Scope

This contract defines the player-visible behavior for THOR-404 frontend menu screens, panels, and generated action buttons. It applies to native fallback widgets for Home, Play, Options, and future menu panels that consume the shared menu input/focus contract.

## Active Menu Surface Input

When a menu screen or panel becomes active:
- it installs default confirm handling for generated action activation;
- it installs default Back/Cancel handling for child-panel dismissal or previous-state return;
- it assigns a valid initial focus target when a focusable generated action exists.

## Generated Button Focus

Generated action buttons that are visible and actionable by the current menu state must:
- participate in focus navigation;
- accept keyboard focus;
- accept controller focus;
- preserve focus state when pointer input is used.

If no valid generated action exists, the surface must not leave focus pointing at a destroyed or invalid widget.

## Activation Parity

For the same generated action:
- mouse click;
- keyboard confirm;
- controller confirm;

must invoke the same menu coordinator behavior. Input-specific handlers may translate input events, but the selected action's behavior must converge before side effects are performed.

## Back and Cancel

Back or Cancel on a child menu surface must:
- dismiss the active panel or return to the previous frontend state;
- restore focus to the appropriate previous-state target when possible;
- choose a valid fallback focus target when the preferred target is unavailable.

Back or Cancel on a root surface must not leave focus invalid.

## Home Focus Restoration

Given Home launches Play:
- returning from Play restores focus to the Home Play navigation action when valid.

Given Home launches Options:
- returning from Options restores focus to the Home Options navigation action when valid.

If the target action no longer exists or is not focusable, the menu selects a valid fallback focus target.

## Acceptance Evidence

The contract is satisfied when automated coverage demonstrates:
- initial focus is assigned for native fallback Home, Play, and Options surfaces;
- pointer, keyboard, and controller activation reach the same coordinator action;
- Back/Cancel exits Play and Options child surfaces appropriately;
- Play-to-Home and Options-to-Home focus restoration target the originating Home actions;
- the complete behavior works without authored presentation assets.
