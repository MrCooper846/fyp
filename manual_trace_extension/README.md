# Manual Link Trace Recorder

Small Chrome/Edge extension for logging the links you follow while manually researching university contacts.

## What it records

Each click record includes:

- `session_id`
- `attempt_index`
- `current_university`
- `row_type`
- `clicked_at`
- `source_url`
- `source_title`
- `clicked_href`
- `anchor_text`
- `link_target`
- `open_in_new_context`
- `same_tab_navigation_confirmed`
- `final_url`
- `navigation_confirmed_at`
- `tab_id`
- `frame_id`

## Load it in Chrome

1. Open `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select this folder:

`C:\Users\monke\Documents\Final Year Project\manual_trace_extension`

## Use it

1. Open the extension popup
2. Click `Start tracking`
3. When you switch to a new university homepage, click `Next university`
4. Browse normally and follow links while doing manual research
5. Click `Export CSV`

You can also save contacts manually in the popup while you research. Every field is optional, so you can save a name first and fill in the rest later in the session if needed.

The extension starts a fresh session each time you click `Start tracking`. The `Next university` button inserts a boundary row and increments the attempt counter so one combined CSV stays easy to split later.

## Notes

- It is designed to capture the links you click, not every page you ever open.
- Same-tab navigations are marked as confirmed when the browser reports a navigation after the click.
- New-tab or modifier-key clicks are still recorded, but they may not get a same-tab confirmation flag.
- If you change the extension files, reload it from `chrome://extensions` before testing again.
