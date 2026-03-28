def generate_multi_step_payload():
    return {
        "task": {
            "steps": [
                {
                    "id": "step-1",
                    "instructions": "Do something",
                    "foo": "bar",
                    "other_key": "other_value",
                    "tool": {
                        "name": "some_tool"
                    }
                },
                {
                    "id": "step-2",
                    "instructions": "Do something else",
                    "baz": "qux",
                }
            ]
        }
    }
