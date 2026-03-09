# Quickstart: Implement 5.14

To verify the task 5.14 implementation:

1. Start the temporal environment:
   ```bash
   docker compose up -d temporal
   ```
2. Run the validation tests:
   ```bash
   pytest tests/unit/workflows/temporal/test_task_5_14.py
   ```