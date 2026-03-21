1. Modify `api_service/static/task_dashboard/dashboard.js` to change the priority input from `number` to `range`. Update the `min`, `max`, and `value` attributes of the range input. We'll use `min="-10" max="10"`. So middle is `0`.
2. Do the same for `api_service/static/task_dashboard/dashboard.js` around line `4284`.
3. Wait for the user to verify the changes using `frontend_verification_instructions` and ensure Playwright tests are passing.
4. Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.
5. Submit a pull request via `gh pr create`.
