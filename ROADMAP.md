# NUTPulse Roadmap

This roadmap lists planned improvements. It is not a commitment to a specific release date.

## Next release

### Make Settings actions clearer

The current global **Save settings** button appears inside the **Send report** card, which can make it look as if it only saves e-mail settings.

Planned changes:

- give each Settings section its own clearly labelled save action:
  - **Save energy settings** for energy price and cost preferences;
  - **Save UPS settings** for the NUT server connection and polling options;
  - **Save report settings** for weekly e-mail reports;
- keep **Test connection** separate from saving the UPS configuration and explain the result;
- show which section has unsaved changes;
- disable save actions when nothing has changed;
- show a confirmation next to the section that was saved;
- preserve entered values and display validation errors in the relevant section.

Acceptance criteria:

- a new user can tell which settings each button saves without prior knowledge;
- testing a NUT connection does not imply that the connection details were saved;
- saving one section cannot unintentionally change another section;
- the layout and feedback remain clear on desktop and mobile.
