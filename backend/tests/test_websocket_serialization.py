from datetime import date, datetime, timezone

import pytest
from pydantic import BaseModel

from backend.services.json_safety import to_json_safe
from backend.services.pbg_streaming_protocol import ConnectionManager


class SampleModel(BaseModel):
    created_at: datetime
    labels: set[str]


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


def test_to_json_safe_handles_nested_datetime_payloads():
    payload = {
        "type": "STATE_UPDATE",
        "timestamp": datetime(2026, 4, 28, 17, 0, tzinfo=timezone.utc),
        "data": {
            "dates": [date(2026, 4, 28)],
            "tuple": (datetime(2026, 4, 28, 18, 30),),
            "set": {"alpha", "beta"},
            "model": SampleModel(
                created_at=datetime(2026, 4, 28, 19, 45, tzinfo=timezone.utc),
                labels={"live", "pulse"},
            ),
        },
    }

    safe = to_json_safe(payload)

    assert safe["timestamp"] == "2026-04-28T17:00:00+00:00"
    assert safe["data"]["dates"] == ["2026-04-28"]
    assert safe["data"]["tuple"] == ["2026-04-28T18:30:00"]
    assert isinstance(safe["data"]["set"], list)
    assert safe["data"]["model"]["created_at"] == "2026-04-28T19:45:00+00:00"
    assert isinstance(safe["data"]["model"]["labels"], list)


@pytest.mark.asyncio
async def test_conversion_success_broadcast_shape():
    """CONVERSION_SUCCESS emitted by /convert must carry required fields, all JSON-safe."""
    manager = ConnectionManager()
    ws = FakeWebSocket()
    manager.active_connections.append(ws)

    import datetime as dt
    await manager.broadcast_json({
        "type": "CONVERSION_SUCCESS",
        "source": "sportybet",
        "target": "bet9ja",
        "selections": 3,
        "timestamp": dt.datetime(2026, 5, 9, 10, 0, tzinfo=dt.timezone.utc).isoformat(),
    })

    msg = ws.messages[0]
    assert msg["type"] == "CONVERSION_SUCCESS"
    assert msg["source"] == "sportybet"
    assert msg["target"] == "bet9ja"
    assert msg["selections"] == 3
    assert "timestamp" in msg


@pytest.mark.asyncio
async def test_connection_manager_broadcasts_json_safe_payload():
    manager = ConnectionManager()
    websocket = FakeWebSocket()
    manager.active_connections.append(websocket)

    await manager.broadcast_json({
        "type": "VALUE_SIGNAL",
        "timestamp": datetime(2026, 4, 28, 20, 15, tzinfo=timezone.utc),
        "payload": SampleModel(
            created_at=datetime(2026, 4, 28, 20, 16, tzinfo=timezone.utc),
            labels={"edge"},
        ),
    })

    assert websocket.messages == [{
        "type": "VALUE_SIGNAL",
        "timestamp": "2026-04-28T20:15:00+00:00",
        "payload": {
            "created_at": "2026-04-28T20:16:00+00:00",
            "labels": ["edge"],
        },
    }]
