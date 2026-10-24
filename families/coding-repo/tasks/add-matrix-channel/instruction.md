Add a Matrix channel to the `notify` package in `/app`.

- It is configured with `homeserver` (required, an `https://` URL, used without any trailing slash), `room_id` (required, starting with `!`), and `access_token` (required).
- `format(message)` returns the JSON body `{"msgtype": "m.text", "body": "<title>\n\n<body>"}`, serialized with `json.dumps(..., sort_keys=True)`.
- `send(message)` posts that body to `<homeserver>/_matrix/client/v3/rooms/<room_id>/send/m.room.message`, with the room ID inserted as is, and the headers `Authorization: Bearer <access_token>` and `Content-Type: application/json`. It returns the transport's status code.

Follow the conventions in the repository's AGENTS.md.
