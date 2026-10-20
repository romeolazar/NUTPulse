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

### Correct and clarify per-UPS energy costs

The current **Monthly** and **Annual** values are immediate projections based on the latest real-power reading, assuming that load remains unchanged 24/7. They are not costs accumulated from stored history, so presenting them without an estimate label is misleading for a newly added UPS.

Planned changes:

- keep all cost figures individual for each UPS;
- show actual accumulated **This month**, **This year**, and **Total** costs from recorded samples;
- start accumulated costs at zero for a newly added UPS and show the period for which data is available;
- mark incomplete periods when samples are missing;
- if the current-load projection is retained, label it explicitly as **Estimated monthly at current load** and keep it visually separate from actual costs;
- never backfill or imply consumption from before NUTPulse started collecting data.

### Simplify the Settings page content

Planned changes:

- remove the time-zone value currently shown in the page header;
- remove **Last 7 Days Event Summary** from Settings; it is an e-mail report template and belongs in report preview/history, not in the settings layout;
- replace the disabled **Calculation basis** selector with clear explanatory text until more than one calculation method is available;
- when multiple methods are implemented, offer meaningful choices such as accumulated historical cost and current-load projection, with a short explanation of each;
- avoid controls that look interactive when they cannot be used.

Acceptance criteria:

- Settings contains configuration controls only, without dashboard or report-summary content;
- every visible selector is usable;
- projected and accumulated costs cannot be confused;
- the page header does not display the application time zone.

### Add secure SMTP configuration

Allow an authenticated administrator to configure report delivery from Settings:

- SMTP host and port;
- encryption mode, including STARTTLS/TLS as supported;
- username and a write-only password field;
- sender name and sender address;
- connection test and test-report actions;
- clear configured, incomplete, and error states.

Security requirements:

- never return or display the saved SMTP password in the browser;
- leaving the password field empty when editing must preserve the existing password;
- store credentials encrypted using a deployment-provided secret, or continue to support Docker secrets/environment variables for operators who do not want credentials stored by the application;
- never write SMTP credentials to logs, reports, screenshots, or the public repository.
