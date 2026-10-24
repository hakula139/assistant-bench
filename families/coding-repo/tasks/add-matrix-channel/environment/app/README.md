# notify

Deliver short notifications through pluggable channels. Each channel validates its configuration when it is created and posts through an injected transport.

```python
from notify import Message, get_channel

channel = get_channel("slack")({"webhook_url": "https://hooks.slack.com/services/T/B/X"}, transport)
channel.send(Message(title="Deploy finished", body="All checks passed."))
```
