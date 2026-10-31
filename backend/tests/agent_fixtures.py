"""Synthetic provider messages shared by agent tests."""

import json


def completion(content="Fixture final answer", calls=None, finish_reason=None, **message_fields):
    message = {"role": "assistant", "content": content, **message_fields}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{
        "message": message,
        "finish_reason": finish_reason or ("tool_calls" if calls else "stop"),
    }]}


def tool_call(name, arguments, call_id="fixture-call-1"):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
