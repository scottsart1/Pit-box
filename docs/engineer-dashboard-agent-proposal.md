# Dashboard actions: proposed second phase

This is a proposal, not a feature in 5.1.0. The current release changes setup
recommendations, radio routing and scheduling; it does not let the engineer
control the dashboard.

The first useful action would be changing the view in response to a spoken
request. For example, “show my familiar race dashboard while I catch the car
ahead” should restore the driver's saved race layout and focus its gap/pace
cards. “Go back” should restore the previous view.

Expose a small set of typed actions to the existing reasoning loop:

- `show_view(view)` opens Drive, Strategy, Setup, Analysis or Connection.
- `use_saved_layout(layout_id)` selects an existing driver-owned layout.
- `focus_driver(driver_id)` highlights an identified rival in a compatible view.
- `set_card_visibility(card_id, visible)` adjusts the displayed cards.
- `undo_dashboard_action()` restores the previous dashboard state.

The browser should advertise available views, layouts and cards. The engineer
chooses from those identifiers and receives a success or failure receipt before
saying the change happened. Actions go through the app's existing navigation
and settings functions, not screen-coordinate clicks or generated JavaScript.
Ambiguous requests get a short clarification. A visible action log and one-tap
undo keep the driver's control available during radio use.

Start with reversible view and layout changes. A later, separately scoped phase
can cover persistent preferences. Network settings, provider keys, saved-session
deletion and external publication are outside the initial action set. Driving
advice remains grounded in the same telemetry tools; changing a view does not
change the game's car setup.

Acceptance should include voice requests while 60 Hz telemetry is streaming,
tablet portrait/landscape layouts, missing cards, unknown rival names, failed
actions, reconnects, undo and a driver manually changing the view during an
in-flight request. Preserve the driver's newer action when requests overlap.
