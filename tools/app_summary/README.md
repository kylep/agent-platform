# App summary

`counts` groups records in the App tool role `source` by one readable field and
returns at most 20 value/count pairs. It uses only the executor-injected
`_app_data.url` scan endpoint. The tool has no secret, database connection or
external network destination; the API applies the viewer's App facts before
returning each scan page.

Example App tool fact: `tool: app_summary` with a `source` role bound to a
collection for `read`. A typed/v2 tool view can bind `action: counts` and pass
`field: status` and `top: 10`. The field must be readable, and values must be
short scalars (at most 128 characters).
