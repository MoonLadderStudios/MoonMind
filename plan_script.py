import json

def generate_multi_step_payload():
    return {
        "task": {
            "steps": [
                {
                    "id": "step1",
                    "instructions": "Do something",
                    "foo": "bar",
                    "other_key": "other_value",
                    "tool": {
                        "name": "some_tool"
                    }
                },
                {
                    "id": "step2",
                    "instructions": "Do something else",
                    "baz": "qux",
                }
            ]
        }
    }
